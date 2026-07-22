"""Characterization tests for transcript/log usage parsing and staleness labels."""

import json
from datetime import datetime, timedelta, timezone

from claude_config_dashboard import usage


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class TestCollectUsageStats:
    def test_global_scope_counts_all_projects(self, claude_env):
        stats = usage.collect_usage_stats(claude_env.claude, "*")
        assert stats["skills"]["my-skill"]["count"] == 2
        assert stats["skills"]["other-skill"]["count"] == 1
        assert stats["agents"]["test-runner"]["count"] == 1
        # MCP name is the middle segment of mcp__<server>__<tool>
        assert stats["mcp"]["local-mcp"]["count"] == 1

    def test_last_used_is_max_timestamp(self, claude_env):
        stats = usage.collect_usage_stats(claude_env.claude, "*")
        stat = stats["skills"]["my-skill"]
        # two invocations at ~1d and ~2d ago; last_used keeps the newer one
        newer = datetime.fromisoformat(stat["last_used"].replace("Z", "+00:00"))
        assert datetime.now(timezone.utc) - newer < timedelta(days=1, hours=1)

    def test_malformed_lines_and_non_assistant_entries_skipped(self, claude_env):
        stats = usage.collect_usage_stats(claude_env.claude, "*")
        total = sum(v["count"] for name in ("skills", "agents", "mcp") for v in stats[name].values())
        # 2 skill + 1 agent + 1 mcp from alpha, 1 skill from beta,
        # + 2 mcp entries from pre_tool_use.json supplement
        assert total == 7

    def test_project_scope_uses_session_map(self, claude_env):
        stats = usage.collect_usage_stats(claude_env.claude, claude_env.project_cwd)
        assert stats["skills"]["my-skill"]["count"] == 2
        assert "other-skill" not in stats["skills"]

    def test_project_scope_unknown_cwd_returns_empty(self, claude_env):
        stats = usage.collect_usage_stats(claude_env.claude, "/nowhere")
        assert stats == {"skills": {}, "agents": {}, "mcp": {}}

    def test_pre_tool_use_supplements_mcp(self, claude_env):
        global_stats = usage.collect_usage_stats(claude_env.claude, "*")
        assert global_stats["mcp"]["extra-mcp"]["count"] == 1
        assert global_stats["mcp"]["beta-only-mcp"]["count"] == 1
        # project scope filters supplement entries by cwd
        scoped = usage.collect_usage_stats(claude_env.claude, claude_env.project_cwd)
        assert "extra-mcp" in scoped["mcp"]
        assert "beta-only-mcp" not in scoped["mcp"]

    def test_empty_claude_dir(self, empty_claude):
        assert usage.collect_usage_stats(empty_claude, "*") == {
            "skills": {},
            "agents": {},
            "mcp": {},
            "session_context": {},
            "window_start": "",
        }


class TestMeasuredSessionContext:
    def test_session_start_tokens_sums_cache_and_input(self):
        entry = {
            "message": {
                "usage": {
                    "cache_creation_input_tokens": 40000,
                    "cache_read_input_tokens": 10000,
                    "input_tokens": 71,
                }
            }
        }
        assert usage._session_start_tokens(entry) == 50071

    def test_session_start_tokens_missing_usage_is_zero(self):
        assert usage._session_start_tokens({"message": {}}) == 0
        assert usage._session_start_tokens({"message": {"usage": None}}) == 0

    def test_summarize_sessions_odd_and_even(self):
        assert usage._summarize_sessions([30, 10, 50]) == {
            "median": 30,
            "min": 10,
            "max": 50,
            "count": 3,
        }
        # even count: median averages the two middle values
        assert usage._summarize_sessions([10, 20, 30, 40])["median"] == 25

    def test_summarize_sessions_empty(self):
        assert usage._summarize_sessions([]) == {}

    def test_sidechain_transcripts_excluded_from_measured(self, tmp_path):
        proj = tmp_path / "projects" / "p"
        proj.mkdir(parents=True)

        def _asst(tokens, sidechain):
            return json.dumps(
                {
                    "type": "assistant",
                    "isSidechain": sidechain,
                    "timestamp": "2026-06-01T00:00:00.000Z",
                    "message": {"usage": {"cache_read_input_tokens": tokens}, "content": []},
                }
            )

        (proj / "main.jsonl").write_text(_asst(50000, False) + "\n")
        (proj / "agent-x.jsonl").write_text(_asst(9000, True) + "\n")

        stats = usage.collect_usage_stats(tmp_path, "*")
        # only the real user session counts toward measured context
        assert stats["session_context"] == {"median": 50000, "min": 50000, "max": 50000, "count": 1}

    def test_collect_reports_measured_context_and_window(self, claude_env):
        stats = usage.collect_usage_stats(claude_env.claude, "*")
        assert "session_context" in stats
        assert "window_start" in stats
        # window_start is the earliest transcript timestamp, when any exist
        if stats["window_start"]:
            assert stats["window_start"][:4].isdigit()


class TestGetCachedUsage:
    def test_caches_per_scope(self, claude_env, monkeypatch):
        first = usage.get_cached_usage(claude_env.claude, "*")
        calls = []
        monkeypatch.setattr(usage, "collect_usage_stats", lambda claude_dir, scope="*": calls.append(scope))
        second = usage.get_cached_usage(claude_env.claude, "*")
        assert second is first
        assert calls == []


class TestStaleInfo:
    def test_never_used(self):
        assert usage._stale_info("") == (None, "Never used", "stale-never")

    def test_recent_minutes(self):
        ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        days, label, cls = usage._stale_info(ts)
        assert cls == "stale-recent"
        assert label.endswith("m ago")

    def test_recent_days(self):
        _, label, cls = usage._stale_info(_iso(3))
        assert cls == "stale-recent"
        assert label == "Used 3d ago"

    def test_mid(self):
        _, label, cls = usage._stale_info(_iso(15))
        assert cls == "stale-mid"

    def test_old(self):
        days, label, cls = usage._stale_info(_iso(45))
        assert cls == "stale-old"
        assert label.startswith("Stale")
        assert days >= 44

    def test_garbage_timestamp(self):
        assert usage._stale_info("not-a-date") == (None, "", "")


class TestSessionMap:
    def test_load_session_map(self, claude_env):
        m = usage._load_session_map(claude_env.claude)
        assert claude_env.project_cwd in m
        assert str(claude_env.transcript.parent) in m[claude_env.project_cwd]

    def test_list_known_projects(self, claude_env):
        projects = usage.list_known_projects(claude_env.claude)
        assert [p["name"] for p in projects] == ["alpha", "beta"]
        assert projects[0]["sessions"] == 1

    def test_missing_log(self, empty_claude):
        assert usage._load_session_map(empty_claude) == {}
        assert usage.list_known_projects(empty_claude) == []
