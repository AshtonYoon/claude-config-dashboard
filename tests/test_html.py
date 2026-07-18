"""Marker-based characterization of enrichment and HTML rendering.

Asserts stable markers (names, tab ids, security attributes) rather than
full-page golden strings, so the planned template refactor doesn't break these.
"""

import dashboard


def _home_data(env):
    settings = dashboard.load_settings()
    raw = {
        "plugins": dashboard.collect_plugins_raw(settings),
        "agents": dashboard.collect_agents_raw(),
        "skills": dashboard.collect_skills_raw(),
        "commands": dashboard.collect_commands(),
        "hooks": dashboard.collect_hooks(settings),
        "mcp_servers": dashboard.collect_mcp_servers_raw(settings),
        "rules": dashboard.collect_rules(),
    }
    usage = dashboard.collect_usage_stats("*")
    return dashboard.enrich_data(raw, usage)


class TestEnrichment:
    def test_enrich_data_merges_usage(self, claude_env):
        data = _home_data(claude_env)

        skills = {s["slug"]: s for s in data["skills"]}
        assert skills["my-skill"]["usage_count"] == 2
        assert skills["my-skill"]["last_used"]
        assert skills["single-file"]["usage_count"] == 0

        agents = {a["slug"]: a for a in data["agents"]}
        assert agents["test-runner"]["usage_count"] == 1

        mcp = {s["name"]: s for s in data["mcp_servers"]}
        assert mcp["local-mcp"]["usage_count"] == 1
        assert mcp["json-mcp"]["usage_count"] == 0

    def test_plugin_bundle_child_usage(self, claude_env):
        usage = {"skills": {"my-plugin:sub-a": {"count": 3, "last_used": "2026-01-01T00:00:00Z"}},
                 "agents": {}, "mcp": {}}
        skills = dashboard.enrich_skills(dashboard.collect_skills_raw(), usage)
        bundle = next(s for s in skills if s["slug"] == "my-plugin")
        assert bundle["usage_count"] == 3
        assert bundle["child_usage"] == [
            {"name": "sub-a", "count": 3, "last_used": "2026-01-01T00:00:00Z"},
        ]


class TestBuildHtml:
    def test_home_view_markers(self, claude_env):
        html = dashboard.build_html(_home_data(claude_env), claude_env.claude, "home")

        assert "Claude Config Dashboard" in html
        # all home tabs render
        for tab in ("plugins", "agents", "skills", "commands", "hooks", "mcp", "rules", "cleanup"):
            assert f"btn-{tab}" in html
        # collected items appear
        for marker in ("My Plugin", "test-runner", "my-skill", "/deploy",
                       "local-mcp", "json-mcp", "style.md", "PreToolUse"):
            assert marker in html

    def test_security_markers(self, claude_env):
        html = dashboard.build_html(_home_data(claude_env), claude_env.claude, "home")

        # per-run CSRF token is embedded and endpoints are called via POST
        assert f"DASH_TOKEN = '{dashboard._SESSION_TOKEN}'" in html
        assert "X-Dashboard-Token" in html
        assert "method: 'POST'" in html
        assert "stopServer()" in html

    def test_rendering_registers_openable_paths(self, claude_env):
        assert dashboard._OPENABLE_PATHS == set()
        dashboard.build_html(_home_data(claude_env), claude_env.claude, "home")
        agent_md = str((claude_env.claude / "agents" / "test-runner.md").resolve())
        assert agent_md in dashboard._OPENABLE_PATHS

    def test_html_escapes_descriptions(self, claude_env):
        (claude_env.claude / "agents" / "evil.md").write_text(
            '---\nname: evil\ndescription: <script>alert(1)</script>\n---\nBody\n'
        )
        html = dashboard.build_html(_home_data(claude_env), claude_env.claude, "home")
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
