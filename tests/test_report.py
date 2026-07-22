"""Plain-text --report and --clean output: the judgment, not the browse."""

from claude_config_dashboard import collectors, context_tax, enrich, report, usage


def _enriched(env):
    raw = collectors.scan_dir(env.claude)
    stats = usage.collect_usage_stats(env.claude, "*")
    return enrich.enrich_data(raw, stats)


class TestBuildReportUnmeasured:
    """claude_env's synthetic transcripts carry no usage block, so measured stays empty —
    this exercises the static-estimate fallback path."""

    def test_falls_back_to_static_estimate(self, claude_env):
        data = _enriched(claude_env)
        tax = context_tax.compute_context_tax(claude_env.claude, data)
        text = report.build_report(data, tax)

        assert "Claude Config Report" in text
        assert "not enough session history" in text
        assert f"~{tax['total_tokens']:,} tokens" in text

    def test_install_vs_used_gap_matches_fixture(self, claude_env):
        data = _enriched(claude_env)
        tax = context_tax.compute_context_tax(claude_env.claude, data)
        text = report.build_report(data, tax)

        # fixture: 1 agent (used), 2 skills (1 used, 1 never), 2 mcp (1 used, 1 never)
        assert "Agents" in text and "1/1 used" in text
        assert "Skills" in text and "1/2 used" in text and "1 idle" in text
        assert "MCP" in text and "1/2 used" in text

    def test_heaviest_idle_items_and_verdict(self, claude_env):
        data = _enriched(claude_env)
        tax = context_tax.compute_context_tax(claude_env.claude, data)
        text = report.build_report(data, tax)

        assert "Heaviest idle items" in text
        assert "single-file" in text  # never-used skill from the fixture
        assert "Verdict:" in text
        assert "dead weight" in text
        assert "--report --clean" in text

    def test_lean_config_has_no_clean_hint(self, claude_env):
        data = _enriched(claude_env)
        tax = context_tax.compute_context_tax(claude_env.claude, data)
        tax["reclaimable_items"] = []
        tax["reclaimable_tokens"] = 0
        text = report.build_report(data, tax)

        assert "Verdict: nothing looks idle" in text
        assert "--clean" not in text


class TestBuildReportMeasured:
    def test_measured_hero_and_baseline_reconcile(self, claude_env):
        data = _enriched(claude_env)
        tax = context_tax.compute_context_tax(claude_env.claude, data)
        tax["measured"] = {"median": 46708, "min": 34345, "max": 109104, "count": 9}
        tax["window_start"] = "2026-05-26T07:54:54.930Z"
        text = report.build_report(data, tax)

        assert "measured from your last 9 sessions (since 2026-05-26)" in text
        assert "46,708" in text
        baseline = 46708 - tax["total_tokens"]
        assert f"{baseline:,}" in text
        assert "not cuttable" in text
        assert "not enough session history" not in text


class TestBuildCleanupScript:
    def test_script_has_shebang_and_archive_setup(self, claude_env):
        data = _enriched(claude_env)
        tax = context_tax.compute_context_tax(claude_env.claude, data)
        script = report.build_cleanup_script(tax)

        assert script.startswith("#!/bin/sh")
        assert "set -e" in script
        assert f'ARCHIVE="{tax["archive_dir"]}"' in script
        assert 'mkdir -p "$ARCHIVE"' in script

    def test_script_moves_never_deletes(self, claude_env):
        data = _enriched(claude_env)
        tax = context_tax.compute_context_tax(claude_env.claude, data)
        script = report.build_cleanup_script(tax)

        assert "rm " not in script
        assert "rm\n" not in script
        # single-file skill is reclaimable and has a real archive source
        skill_item = next(i for i in tax["reclaimable_items"] if i["name"] == "single-file")
        assert skill_item["archive_source"]
        assert f'mv -n "{skill_item["archive_source"]}"' in script

    def test_skipped_items_listed_with_reason(self, claude_env):
        data = _enriched(claude_env)
        tax = context_tax.compute_context_tax(claude_env.claude, data)
        # force one reclaimable item to need manual review
        tax["reclaimable_items"] = [
            {
                "name": "bundled-thing",
                "kind": "skill",
                "tokens": 10,
                "archive_source": None,
                "skip_reason": "plugin-bundled; use /plugin uninstall",
            }
        ]
        script = report.build_cleanup_script(tax)

        assert "SKIPPED" in script
        assert "bundled-thing -- plugin-bundled; use /plugin uninstall" in script
        assert "mv -n" not in script
