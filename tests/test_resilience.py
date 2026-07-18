"""Corrupt input files must not crash the scan, and must leave a log record."""

import logging

from claude_config_dashboard import collectors, usage

GARBAGE = "{not json !!!"


class TestCorruptFiles:
    def test_corrupt_settings_json_returns_empty_and_warns(self, claude_env, caplog):
        (claude_env.claude / "settings.json").write_text(GARBAGE)
        with caplog.at_level(logging.WARNING, logger="claude_config_dashboard"):
            assert collectors.load_settings(claude_env.claude) == {}
        assert any("settings.json" in r.message for r in caplog.records)

    def test_corrupt_plugin_package_json_keeps_plugin(self, claude_env, caplog):
        pkg = claude_env.claude / "plugins" / "cache" / "my-marketplace" / "my-plugin" / "1.2.3" / "package.json"
        pkg.write_text(GARBAGE)
        with caplog.at_level(logging.DEBUG, logger="claude_config_dashboard"):
            settings = collectors.load_settings(claude_env.claude)
            plugins = collectors.collect_plugins_raw(claude_env.claude, settings)
        assert plugins[0]["version"] == "1.2.3"
        assert plugins[0]["description"] == ""
        assert any("package.json" in r.message for r in caplog.records)

    def test_corrupt_home_claude_json_keeps_settings_servers(self, claude_env, caplog):
        (claude_env.home / ".claude.json").write_text(GARBAGE)
        with caplog.at_level(logging.DEBUG, logger="claude_config_dashboard"):
            servers = collectors.collect_mcp_servers_raw(collectors.load_settings(claude_env.claude))
        assert [s["name"] for s in servers] == ["local-mcp"]

    def test_corrupt_session_start_json(self, claude_env, caplog):
        (claude_env.claude / "logs" / "session_start.json").write_text(GARBAGE)
        with caplog.at_level(logging.DEBUG, logger="claude_config_dashboard"):
            assert usage._load_session_map(claude_env.claude) == {}
            assert usage.list_known_projects(claude_env.claude) == []
        assert any("session_start.json" in r.message for r in caplog.records)

    def test_corrupt_pre_tool_use_json_keeps_transcript_stats(self, claude_env, caplog):
        (claude_env.claude / "logs" / "pre_tool_use.json").write_text(GARBAGE)
        with caplog.at_level(logging.DEBUG, logger="claude_config_dashboard"):
            stats = usage.collect_usage_stats(claude_env.claude, "*")
        # transcript-derived stats survive; only the supplement is skipped
        assert stats["skills"]["my-skill"]["count"] == 2
        assert "extra-mcp" not in stats["mcp"]

    def test_wrong_shape_session_start_json(self, claude_env):
        # valid JSON but not a list of dicts
        (claude_env.claude / "logs" / "session_start.json").write_text('{"cwd": "/x"}')
        assert usage._load_session_map(claude_env.claude) == {}

    def test_malformed_transcript_line_logged(self, claude_env, caplog):
        with caplog.at_level(logging.DEBUG, logger="claude_config_dashboard"):
            usage.collect_usage_stats(claude_env.claude, "*")
        assert any("malformed transcript line" in r.message for r in caplog.records)
