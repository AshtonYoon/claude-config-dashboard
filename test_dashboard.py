import unittest
from pathlib import Path

import dashboard


class ProjectOnlyDataTests(unittest.TestCase):
    def test_build_project_only_data_keeps_only_project_unique_items(self):
        home = {
            "plugins": [],
            "agents": [],
            "skills": [
                {"name": "Shared Skill", "slug": "shared-skill", "source": "custom", "path": "/home/shared.md"},
            ],
            "commands": [
                {"name": "shared", "slash": "/shared", "description": "", "path": "/home/shared.md"},
            ],
            "hooks": [
                {"trigger": "PreToolUse", "matcher": "Skill", "command": "shared-hook", "path": "/home/shared-hook.sh"},
            ],
            "mcp_servers": [
                {"name": "shared-mcp", "command": "shared", "args": [], "source": "settings.json"},
            ],
            "rules": [
                {"category": "common", "files": [{"name": "shared.md", "path": "/home/shared.md"}]},
            ],
        }
        project = {
            "plugins": [],
            "agents": [],
            "skills": [
                {"name": "Shared Skill", "slug": "shared-skill", "source": "custom", "path": "/project/shared.md"},
                {"name": "Project Skill", "slug": "project-skill", "source": "custom", "path": "/project/project.md"},
            ],
            "commands": [
                {"name": "shared", "slash": "/shared", "description": "", "path": "/project/shared.md"},
                {"name": "project-only", "slash": "/project-only", "description": "", "path": "/project/project.md"},
            ],
            "hooks": [
                {"trigger": "PreToolUse", "matcher": "Skill", "command": "shared-hook", "path": "/project/shared-hook.sh"},
                {"trigger": "PostToolUse", "matcher": "Agent", "command": "project-hook", "path": "/project/project-hook.sh"},
            ],
            "mcp_servers": [
                {"name": "shared-mcp", "command": "shared", "args": [], "source": "settings.local.json"},
                {"name": "project-mcp", "command": "project", "args": ["--flag"], "source": "settings.local.json"},
            ],
            "rules": [
                {"category": "common", "files": [{"name": "shared.md", "path": "/project/shared.md"}]},
                {"category": "project", "files": [{"name": "project-only.md", "path": "/project/project-only.md"}]},
            ],
        }

        data = dashboard.build_project_only_data(home, project)

        self.assertEqual([item["slug"] for item in data["skills"]], ["project-skill"])
        self.assertEqual([item["slash"] for item in data["commands"]], ["/project-only"])
        self.assertEqual([item["name"] for item in data["mcp_servers"]], ["project-mcp"])
        self.assertEqual([item["command"] for item in data["hooks"]], ["project-hook"])
        self.assertEqual(data["rules"], [{"category": "project", "files": [{"name": "project-only.md", "path": "/project/project-only.md"}]}])

    def test_build_html_for_project_only_view_hides_unrelated_tabs(self):
        data = {
            "plugins": [],
            "agents": [],
            "skills": [],
            "commands": [],
            "hooks": [],
            "mcp_servers": [],
            "rules": [],
        }

        html = dashboard.build_html(data, Path('/tmp/project/.claude'), 'project-only')

        self.assertIn('Project-only config', html)
        self.assertIn('No project-only MCP servers, skills, commands, hooks, or rules found.', html)
        self.assertNotIn('btn-plugins', html)
        self.assertNotIn('btn-agents', html)
        self.assertIn('btn-hooks', html)
        self.assertIn('btn-rules', html)
        self.assertNotIn('btn-cleanup', html)
        self.assertNotIn('Usage Count', html)
        self.assertNotIn('Last Used', html)


if __name__ == '__main__':
    unittest.main()
