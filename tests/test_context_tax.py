"""Context Tax: estimate the per-session token cost of installed config."""

import json

from claude_config_dashboard import collectors, context_tax, enrich, usage

LONG_DESC = "This description is deliberately longer than one hundred characters " * 3


def _enriched(env):
    raw = collectors.scan_dir(env.claude)
    stats = usage.collect_usage_stats(env.claude, "*")
    return enrich.enrich_data(raw, stats)


class TestEstimateTokens:
    def test_rough_chars_over_four(self):
        assert context_tax.estimate_tokens("a" * 400) == 100

    def test_rounds_up_and_empty_is_zero(self):
        assert context_tax.estimate_tokens("abc") == 1
        assert context_tax.estimate_tokens("") == 0


class TestComputeContextTax:
    def test_categories_present(self, claude_env):
        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        assert [c["key"] for c in tax["categories"]] == [
            "claude_md",
            "rules",
            "skills",
            "agents",
            "commands",
        ]
        assert tax["total_tokens"] == sum(c["tokens"] for c in tax["categories"])
        assert tax["total_tokens"] > 0

    def test_claude_md_full_content_counted(self, claude_env):
        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        cat = next(c for c in tax["categories"] if c["key"] == "claude_md")
        expected = context_tax.estimate_tokens((claude_env.claude / "CLAUDE.md").read_text())
        assert cat["tokens"] == expected
        assert cat["items"][0]["name"] == "CLAUDE.md"

    def test_missing_claude_md_is_zero(self, empty_claude):
        raw = collectors.scan_dir(empty_claude)
        data = enrich.enrich_data(raw, {"skills": {}, "agents": {}, "mcp": {}})
        tax = context_tax.compute_context_tax(empty_claude, data)
        cat = next(c for c in tax["categories"] if c["key"] == "claude_md")
        assert cat["tokens"] == 0
        assert tax["total_tokens"] == 0

    def test_rules_use_full_file_content(self, claude_env):
        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        cat = next(c for c in tax["categories"] if c["key"] == "rules")
        style = next(i for i in cat["items"] if i["name"] == "common/style.md")
        expected = context_tax.estimate_tokens((claude_env.claude / "rules" / "common" / "style.md").read_text())
        assert style["tokens"] == expected

    def test_skill_uses_full_description_not_truncated(self, claude_env):
        d = claude_env.claude / "skills" / "wordy-skill"
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: wordy-skill\ndescription: {LONG_DESC}\n---\nBody\n")
        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        cat = next(c for c in tax["categories"] if c["key"] == "skills")
        wordy = next(i for i in cat["items"] if i["name"] == "wordy-skill")
        # collectors truncate to 100 chars; the tax must use the full description
        assert wordy["tokens"] >= context_tax.estimate_tokens(LONG_DESC.strip())

    def test_plugin_bundle_sums_child_descriptions(self, claude_env):
        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        cat = next(c for c in tax["categories"] if c["key"] == "skills")
        bundle = next(i for i in cat["items"] if i["name"] == "my-plugin")
        assert bundle["tokens"] > 0  # bundled-skill description counted

    def test_reclaimable_counts_unused_skills_and_agents(self, claude_env):
        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        names = {i["name"] for i in tax["reclaimable_items"]}
        # never used in fixture transcripts → reclaimable
        assert "single-file" in names
        # used 1-3 days ago → not reclaimable
        assert "my-skill" not in names
        assert "test-runner" not in names
        assert tax["reclaimable_tokens"] == sum(i["tokens"] for i in tax["reclaimable_items"])


class TestArchiveSource:
    def test_directory_skill_archives_whole_folder(self, claude_env):
        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        item = next(i for i in tax["reclaimable_items"] if i["name"] == "single-file")
        # single-file skill: archive source is the .md file itself
        assert item["archive_source"] == str(claude_env.claude / "skills" / "single-file.md")
        assert item["skip_reason"] is None

    def test_directory_based_skill_archive_source_is_its_folder(self, claude_env):
        d = claude_env.claude / "skills" / "dir-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: dir-skill\ndescription: A directory skill\n---\nBody\n")
        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        cat = next(c for c in tax["categories"] if c["key"] == "skills")
        item = next(i for i in cat["items"] if i["name"] == "dir-skill")
        assert item["archive_source"] == str(d)
        assert item["skip_reason"] is None

    def test_plugin_bundle_is_skipped(self, claude_env):
        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        cat = next(c for c in tax["categories"] if c["key"] == "skills")
        bundle = next(i for i in cat["items"] if i["name"] == "my-plugin")
        assert bundle["archive_source"] is None
        assert "plugin uninstall" in bundle["skip_reason"]

    def test_symlinked_skill_is_skipped(self, claude_env):
        target = claude_env.home / "elsewhere" / "linked-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("---\nname: linked-skill\ndescription: Symlinked\n---\nBody\n")
        link = claude_env.claude / "skills" / "linked-skill"
        link.symlink_to(target)

        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        cat = next(c for c in tax["categories"] if c["key"] == "skills")
        item = next(i for i in cat["items"] if i["name"] == "linked-skill")
        assert item["archive_source"] is None
        assert "symlink" in item["skip_reason"]

    def test_agent_archive_source_is_its_file(self, claude_env):
        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        cat = next(c for c in tax["categories"] if c["key"] == "agents")
        item = next(i for i in cat["items"] if i["name"] == "test-runner")
        assert item["archive_source"] == str(claude_env.claude / "agents" / "test-runner.md")
        assert item["skip_reason"] is None

    def test_archive_dir_is_dated_and_under_claude_dir(self, claude_env):
        tax = context_tax.compute_context_tax(claude_env.claude, _enriched(claude_env))
        assert tax["archive_dir"].startswith(str(claude_env.claude / "_archive"))


class TestContextTaxTab:
    def test_home_view_renders_tax_tab(self, claude_env):
        from claude_config_dashboard import render

        html = render.build_html(_enriched(claude_env), claude_env.claude, "home")
        assert "btn-tax" in html
        assert "tab-tax" in html
        assert "Context Tax" in html
        assert "tokens" in html
        # honesty footnote
        assert "chars ÷ 4" in html
        assert "MCP tool schemas" in html

    def test_project_only_view_has_no_tax_tab(self, claude_env):
        from claude_config_dashboard import render

        data = {k: [] for k in ("plugins", "agents", "skills", "commands", "hooks", "mcp_servers", "rules")}
        html = render.build_html(data, claude_env.claude, "project-only")
        assert "btn-tax" not in html


class TestCleanupPlan:
    def test_plan_json_embedded_with_archive_sources(self, claude_env):
        from claude_config_dashboard import render

        html = render.build_html(_enriched(claude_env), claude_env.claude, "home")
        assert "cleanup-plan-data" in html
        assert "downloadCleanupScript()" in html
        assert "copyCleanupScript()" in html

        marker = 'data-plan="'
        start = html.index(marker) + len(marker)
        end = html.index('"', start)
        raw = html[start:end]
        unescaped = raw.replace("&quot;", '"').replace("&amp;", "&")
        plan = json.loads(unescaped)

        assert plan["archiveDir"].startswith(str(claude_env.claude / "_archive"))
        by_name = {item["name"]: item for item in plan["items"]}
        assert by_name["single-file"]["archiveSource"] == str(claude_env.claude / "skills" / "single-file.md")
        assert by_name["single-file"]["skipReason"] is None

    def test_no_plan_data_when_nothing_reclaimable(self, empty_claude):
        from claude_config_dashboard import render

        raw = collectors.scan_dir(empty_claude)
        data = enrich.enrich_data(raw, {"skills": {}, "agents": {}, "mcp": {}})
        html = render.build_html(data, empty_claude, "home")
        # the JS helper functions always reference the id; only the actual
        # element (with its data-plan payload) should be absent
        assert 'id="cleanup-plan-data"' not in html

    def test_build_cleanup_script_js_present_in_app_bundle(self, claude_env):
        from claude_config_dashboard import render

        html = render.build_html(_enriched(claude_env), claude_env.claude, "home")
        assert "function buildCleanupScript" in html
        assert "mkdir -p" in html
        assert "mv -n" in html
