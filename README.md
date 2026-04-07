# claude-config-dashboard

A Claude Code plugin that provides a local web dashboard for visualizing all third-party elements installed in `~/.claude`.

<img width="1752" height="763" alt="image" src="https://github.com/user-attachments/assets/93807ced-d609-4ea9-9948-a4a438a3ca14" />



## Install

```
/plugin marketplace add AshtonYoon/claude-config-dashboard
/plugin install claude-config-dashboard
/reload-plugins
```

## Usage

```
/claude-config-dashboard:show
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

## Update

```
/plugin update claude-config-dashboard
/reload-plugins
```

> **If `/plugin update` shows a "browse plugins" prompt instead of updating directly**, the local marketplace cache is stale. Run this once to refresh it:
> ```bash
> cd ~/.claude/plugins/marketplaces/claude-config-dashboard && git pull
> ```
> Then retry `/plugin update claude-config-dashboard`.

## Requirements

- Python 3.x (standard library only, no pip install needed)
- macOS (uses `open` to launch default apps; Linux uses `xdg-open`)
