"""Context Tax: estimate the per-session token cost of installed config."""

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
