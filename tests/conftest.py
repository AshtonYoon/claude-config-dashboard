"""Shared fixtures: a synthetic ~/.claude tree exercising every collector."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from claude_config_dashboard import security, usage


def _iso(days_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _assistant_line(tool_name: str, tool_input: dict, days_ago: float) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": _iso(days_ago),
            "message": {
                "content": [
                    {"type": "tool_use", "name": tool_name, "input": tool_input},
                ]
            },
        }
    )


PROJECT_CWD = "/work/alpha"


@pytest.fixture
def claude_env(tmp_path, monkeypatch):
    """Synthetic home dir with a fully populated .claude, isolated from the real one."""
    home = tmp_path / "home"
    claude = home / ".claude"

    # settings.json: plugins, hooks, MCP servers
    hook_script = claude / "hooks" / "guard.py"
    hook_script.parent.mkdir(parents=True)
    hook_script.write_text("print('hi')\n")
    settings = {
        "enabledPlugins": {"my-plugin@my-marketplace": True},
        "extraKnownMarketplaces": {
            "my-marketplace": {"source": {"source": "github", "repo": "user/repo"}},
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": f"python3 {hook_script}"}],
                },
            ],
        },
        "mcpServers": {
            "local-mcp": {"command": "npx", "args": ["-y", "local-server"]},
        },
    }
    (claude / "settings.json").write_text(json.dumps(settings))

    # ~/.claude.json with an extra MCP server plus a duplicate of local-mcp
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "json-mcp": {"command": "uvx", "args": ["json-server"]},
                    "local-mcp": {"command": "SHOULD-BE-IGNORED", "args": []},
                },
            }
        )
    )

    # plugin cache: my-marketplace/my-plugin/1.2.3 with a bundled skill
    plugin_root = claude / "plugins" / "cache" / "my-marketplace" / "my-plugin" / "1.2.3"
    plugin_root.mkdir(parents=True)
    (plugin_root / "package.json").write_text(
        json.dumps(
            {
                "description": "A test plugin",
                "homepage": "https://example.com/my-plugin",
            }
        )
    )
    (plugin_root / "README.md").write_text("# My Plugin\n")
    bundled = plugin_root / "skills" / "bundled-skill"
    bundled.mkdir(parents=True)
    (bundled / "skill.md").write_text("---\nname: bundled-skill\ndescription: Bundled skill\n---\nBody\n")

    # agents
    agents = claude / "agents"
    agents.mkdir()
    (agents / "test-runner.md").write_text(
        "---\nname: test-runner\ndescription: Runs the tests\ntools: Read, Bash\n---\nBody\n"
    )

    # skills: one directory skill, one single-file skill
    skills = claude / "skills"
    (skills / "my-skill").mkdir(parents=True)
    (skills / "my-skill" / "skill.md").write_text("---\nname: my-skill\ndescription: Does a thing\n---\nBody\n")
    (skills / "single-file.md").write_text("---\nname: single-file\ndescription: One file skill\n---\nBody\n")

    # commands (top-level + agent_prompts)
    commands = claude / "commands"
    (commands / "agent_prompts").mkdir(parents=True)
    (commands / "deploy.md").write_text("# Deploy\n\nShip the current branch.\n")
    (commands / "agent_prompts" / "scan.md").write_text("Scan things.\n")

    # rules
    rules = claude / "rules" / "common"
    rules.mkdir(parents=True)
    (rules / "style.md").write_text("# Style\n")

    # session transcripts: skill, agent, and MCP usage
    proj_dir = claude / "projects" / "alpha"
    proj_dir.mkdir(parents=True)
    transcript = proj_dir / "sess1.jsonl"
    transcript.write_text(
        "\n".join(
            [
                _assistant_line("Skill", {"skill": "my-skill"}, days_ago=1),
                _assistant_line("Skill", {"skill": "my-skill"}, days_ago=2),
                _assistant_line("Agent", {"subagent_type": "test-runner"}, days_ago=3),
                _assistant_line("mcp__local-mcp__do_thing", {}, days_ago=4),
                "not json at all {{{",
                json.dumps({"type": "user", "timestamp": _iso(0)}),
            ]
        )
        + "\n"
    )

    # other-project transcript (for project scoping tests)
    other_dir = claude / "projects" / "beta"
    other_dir.mkdir(parents=True)
    (other_dir / "sess2.jsonl").write_text(_assistant_line("Skill", {"skill": "other-skill"}, days_ago=5) + "\n")

    # logs read by the dashboard
    logs = claude / "logs"
    logs.mkdir()
    (logs / "session_start.json").write_text(
        json.dumps(
            [
                {"cwd": PROJECT_CWD, "transcript_path": str(transcript)},
                {"cwd": "/work/beta", "transcript_path": str(other_dir / "sess2.jsonl")},
            ]
        )
    )
    (logs / "pre_tool_use.json").write_text(
        json.dumps(
            [
                {"cwd": PROJECT_CWD, "tool_name": "mcp__extra-mcp__query"},
                {"cwd": "/work/beta", "tool_name": "mcp__beta-only-mcp__query"},
            ]
        )
    )

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(usage, "_usage_cache", {})
    monkeypatch.setattr(security, "OPENABLE_PATHS", set())

    return SimpleNamespace(
        home=home,
        claude=claude,
        hook_script=hook_script,
        transcript=transcript,
        project_cwd=PROJECT_CWD,
    )


@pytest.fixture
def empty_claude(tmp_path, monkeypatch):
    """An existing but completely empty .claude dir."""
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(usage, "_usage_cache", {})
    return claude
