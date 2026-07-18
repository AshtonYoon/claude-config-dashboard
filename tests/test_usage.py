"""Characterization tests for transcript/log usage parsing and staleness labels."""

from datetime import datetime, timedelta, timezone

import dashboard


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


class TestCollectUsageStats:
    def test_global_scope_counts_all_projects(self, claude_env):
        stats = dashboard.collect_usage_stats("*")
        assert stats["skills"]["my-skill"]["count"] == 2
        assert stats["skills"]["other-skill"]["count"] == 1
        assert stats["agents"]["test-runner"]["count"] == 1
        # MCP name is the middle segment of mcp__<server>__<tool>
        assert stats["mcp"]["local-mcp"]["count"] == 1

    def test_last_used_is_max_timestamp(self, claude_env):
        stats = dashboard.collect_usage_stats("*")
        stat = stats["skills"]["my-skill"]
        # two invocations at ~1d and ~2d ago; last_used keeps the newer one
        newer = datetime.fromisoformat(stat["last_used"].replace("Z", "+00:00"))
        assert datetime.now(timezone.utc) - newer < timedelta(days=1, hours=1)

    def test_malformed_lines_and_non_assistant_entries_skipped(self, claude_env):
        stats = dashboard.collect_usage_stats("*")
        total = sum(v["count"] for bucket in stats.values() for v in bucket.values())
        # 2 skill + 1 agent + 1 mcp from alpha, 1 skill from beta,
        # + 2 mcp entries from pre_tool_use.json supplement
        assert total == 7

    def test_project_scope_uses_session_map(self, claude_env):
        stats = dashboard.collect_usage_stats(claude_env.project_cwd)
        assert stats["skills"]["my-skill"]["count"] == 2
        assert "other-skill" not in stats["skills"]

    def test_project_scope_unknown_cwd_returns_empty(self, claude_env):
        stats = dashboard.collect_usage_stats("/nowhere")
        assert stats == {"skills": {}, "agents": {}, "mcp": {}}

    def test_pre_tool_use_supplements_mcp(self, claude_env):
        global_stats = dashboard.collect_usage_stats("*")
        assert global_stats["mcp"]["extra-mcp"]["count"] == 1
        assert global_stats["mcp"]["beta-only-mcp"]["count"] == 1
        # project scope filters supplement entries by cwd
        scoped = dashboard.collect_usage_stats(claude_env.project_cwd)
        assert "extra-mcp" in scoped["mcp"]
        assert "beta-only-mcp" not in scoped["mcp"]

    def test_empty_claude_dir(self, empty_claude):
        assert dashboard.collect_usage_stats("*") == {"skills": {}, "agents": {}, "mcp": {}}


class TestGetCachedUsage:
    def test_caches_per_scope(self, claude_env, monkeypatch):
        first = dashboard.get_cached_usage("*")
        calls = []
        monkeypatch.setattr(
            dashboard, "collect_usage_stats", lambda scope="*": calls.append(scope)
        )
        second = dashboard.get_cached_usage("*")
        assert second is first
        assert calls == []


class TestStaleInfo:
    def test_never_used(self):
        assert dashboard._stale_info("") == (None, "Never used", "stale-never")

    def test_recent_minutes(self):
        ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        days, label, cls = dashboard._stale_info(ts)
        assert cls == "stale-recent"
        assert label.endswith("m ago")

    def test_recent_days(self):
        _, label, cls = dashboard._stale_info(_iso(3))
        assert cls == "stale-recent"
        assert label == "Used 3d ago"

    def test_mid(self):
        _, label, cls = dashboard._stale_info(_iso(15))
        assert cls == "stale-mid"

    def test_old(self):
        days, label, cls = dashboard._stale_info(_iso(45))
        assert cls == "stale-old"
        assert label.startswith("Stale")
        assert days >= 44

    def test_garbage_timestamp(self):
        assert dashboard._stale_info("not-a-date") == (None, "", "")


class TestSessionMap:
    def test_load_session_map(self, claude_env):
        m = dashboard._load_session_map()
        assert claude_env.project_cwd in m
        assert str(claude_env.transcript.parent) in m[claude_env.project_cwd]

    def test_list_known_projects(self, claude_env):
        projects = dashboard.list_known_projects()
        assert [p["name"] for p in projects] == ["alpha", "beta"]
        assert projects[0]["sessions"] == 1

    def test_missing_log(self, empty_claude):
        assert dashboard._load_session_map() == {}
        assert dashboard.list_known_projects() == []
