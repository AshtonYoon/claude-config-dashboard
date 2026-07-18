"""Characterization tests pinning collector behavior (post-refactor API)."""

from claude_config_dashboard import collectors


class TestLoadSettings:
    def test_returns_parsed_settings(self, claude_env):
        settings = collectors.load_settings(claude_env.claude)
        assert "enabledPlugins" in settings
        assert settings["mcpServers"]["local-mcp"]["command"] == "npx"

    def test_missing_settings_returns_empty(self, empty_claude):
        assert collectors.load_settings(empty_claude) == {}


class TestFrontmatter:
    def test_parse_frontmatter(self, claude_env):
        front = collectors._parse_frontmatter(claude_env.claude / "agents" / "test-runner.md")
        assert front == {
            "name": "test-runner",
            "description": "Runs the tests",
            "tools": "Read, Bash",
        }

    def test_no_frontmatter_returns_empty(self, claude_env):
        front = collectors._parse_frontmatter(claude_env.claude / "commands" / "deploy.md")
        assert front == {}

    def test_first_desc_skips_frontmatter_and_headings(self, claude_env):
        desc = collectors._first_desc(claude_env.claude / "commands" / "deploy.md")
        assert desc == "Ship the current branch."


class TestCollectPlugins:
    def test_plugin_from_cache(self, claude_env):
        settings = collectors.load_settings(claude_env.claude)
        plugins = collectors.collect_plugins_raw(claude_env.claude, settings)
        assert len(plugins) == 1
        p = plugins[0]
        assert p["name"] == "my-plugin"
        assert p["label"] == "My Plugin"
        assert p["marketplace"] == "my-marketplace"
        assert p["version"] == "1.2.3"
        assert p["description"] == "A test plugin"
        assert p["repo_url"] == "https://github.com/user/repo"
        assert p["enabled"] is True
        assert p["readme_path"].endswith("README.md")
        assert p["installed_at"]  # mtime-derived date

    def test_no_settings_no_plugins(self, empty_claude):
        assert collectors.collect_plugins_raw(empty_claude, {}) == []


class TestCollectAgents:
    def test_agent_fields(self, claude_env):
        agents = collectors.collect_agents_raw(claude_env.claude)
        assert [a["slug"] for a in agents] == ["test-runner"]
        a = agents[0]
        assert a["name"] == "test-runner"
        assert a["description"] == "Runs the tests"
        assert a["tools"] == ["Read", "Bash"]
        assert a["category"] == "Testing"
        assert a["path"].endswith("test-runner.md")

    def test_missing_dir(self, empty_claude):
        assert collectors.collect_agents_raw(empty_claude) == []


class TestCollectSkills:
    def test_custom_and_plugin_skills(self, claude_env):
        skills = collectors.collect_skills_raw(claude_env.claude)
        by_slug = {s["slug"]: s for s in skills}
        assert set(by_slug) == {"my-skill", "single-file", "my-plugin"}

        assert by_slug["my-skill"]["source"] == "custom"
        assert by_slug["my-skill"]["description"] == "Does a thing"
        # case-insensitive: SKILL.md is checked first and macOS matches either case
        assert by_slug["my-skill"]["path"].lower().endswith("skill.md")

        assert by_slug["single-file"]["source"] == "custom"

        bundle = by_slug["my-plugin"]
        assert bundle["source"] == "plugin:my-plugin"
        assert bundle["name"] == "my-plugin (1 skills)"
        assert bundle["plugin_namespace"] == "my-plugin"
        assert bundle["path"].endswith("skills")

    def test_uppercase_skill_md_found(self, claude_env):
        # Real skills ship SKILL.md; must be found on case-sensitive filesystems
        d = claude_env.claude / "skills" / "upper-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: upper-skill\ndescription: Uppercase file\n---\nBody\n")
        skills = collectors.collect_skills_raw(claude_env.claude)
        upper = next(s for s in skills if s["slug"] == "upper-skill")
        assert upper["description"] == "Uppercase file"
        assert upper["path"].endswith("SKILL.md")

    def test_missing_dirs(self, empty_claude):
        assert collectors.collect_skills_raw(empty_claude) == []


class TestCollectCommands:
    def test_top_level_and_agent_prompts(self, claude_env):
        cmds = collectors.collect_commands(claude_env.claude)
        assert [(c["name"], c["slash"]) for c in cmds] == [
            ("deploy", "/deploy"),
            ("scan", "/agent_prompts/scan"),
        ]
        assert cmds[0]["description"] == "Ship the current branch."

    def test_missing_dir(self, empty_claude):
        assert collectors.collect_commands(empty_claude) == []


class TestCollectHooks:
    def test_hook_with_resolvable_script(self, claude_env):
        hooks = collectors.collect_hooks(collectors.load_settings(claude_env.claude))
        assert len(hooks) == 1
        h = hooks[0]
        assert h["trigger"] == "PreToolUse"
        assert h["matcher"] == "Bash"
        assert h["path"] == str(claude_env.hook_script)

    def test_empty_settings(self, empty_claude):
        assert collectors.collect_hooks({}) == []


class TestCollectMcpServers:
    def test_merges_settings_and_claude_json(self, claude_env):
        settings = collectors.load_settings(claude_env.claude)
        servers = collectors.collect_mcp_servers_raw(settings)
        by_name = {s["name"]: s for s in servers}
        assert set(by_name) == {"local-mcp", "json-mcp"}
        # settings.json wins over ~/.claude.json for duplicate names
        assert by_name["local-mcp"]["command"] == "npx"
        assert by_name["local-mcp"]["source"] == "settings.json"
        assert by_name["json-mcp"]["source"] == "~/.claude.json"

    def test_empty(self, empty_claude):
        assert collectors.collect_mcp_servers_raw({}) == []


class TestCollectRules:
    def test_rules_grouped_by_category(self, claude_env):
        rules = collectors.collect_rules(claude_env.claude)
        assert len(rules) == 1
        assert rules[0]["category"] == "common"
        assert [f["name"] for f in rules[0]["files"]] == ["style.md"]

    def test_missing_dir(self, empty_claude):
        assert collectors.collect_rules(empty_claude) == []


class TestScanDir:
    def test_scan_dir_collects_everything(self, claude_env):
        data = collectors.scan_dir(claude_env.claude)
        assert set(data) == {
            "plugins",
            "agents",
            "skills",
            "commands",
            "hooks",
            "mcp_servers",
            "rules",
        }
        assert len(data["plugins"]) == 1
        assert len(data["agents"]) == 1
