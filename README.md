# claude-config-dashboard

A Claude Code plugin that provides a local web dashboard for visualizing all third-party elements installed in `~/.claude`.

## Install

Add to your `~/.claude/settings.json`:

```json
{
  "enabledPlugins": {
    "claude-config-dashboard@claude-config-dashboard": true
  },
  "extraKnownMarketplaces": {
    "claude-config-dashboard": {
      "source": {
        "source": "github",
        "repo": "AshtonYoon/claude-config-dashboard"
      }
    }
  }
}
```

Then run `/install-plugin` or restart Claude Code.

## Usage

```
/claude-config-dashboard
```

The dashboard opens at **http://localhost:9876**.

## Features

| Tab | Contents |
|-----|----------|
| Plugins | Installed plugins with version, GitHub link, install date |
| Agents | Agents grouped by category — click name to open file |
| Skills | Custom and plugin-provided skills |
| Commands | Slash commands with descriptions — click to open file |
| Hooks | Hook scripts by trigger type — click to open script |
| MCP Servers | Configured MCP servers from settings.json and ~/.claude.json |
| Rules | Rule files by category — click to open file |

Clicking any file name opens it in the OS default app (editor, Finder, etc.).

## Requirements

- Python 3.x (standard library only, no pip install needed)
- macOS (uses `open` to launch default apps; Linux uses `xdg-open`)
