# claude-config-dashboard

[![CI](https://github.com/AshtonYoon/claude-config-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/AshtonYoon/claude-config-dashboard/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/claude-config-dashboard)](https://pypi.org/project/claude-config-dashboard/)
[![Python](https://img.shields.io/pypi/pyversions/claude-config-dashboard)](https://pypi.org/project/claude-config-dashboard/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A local web dashboard for everything installed in your `~/.claude` — plugins, agents, skills, commands, hooks, MCP servers, and rules — with usage analytics pulled from your session history, and a project-only view that diffs your current project's `.claude` against your home config. Ships as a standalone CLI (PyPI) and as a Claude Code plugin.

Unlike config *managers*, this is a deliberately **read-only, zero-dependency** analyzer (Python stdlib, no Node/React/build step): it tells you what your config **costs**. The **Context Tax** tab estimates how many tokens your setup adds to every single session, and — for anything unused for 30+ days — can generate a plain shell script that archives it (`mv`, never `rm`) for you to review and run yourself. The dashboard never touches your filesystem on its own.

<img width="1755" height="899" alt="Claude Config Dashboard screenshot" src="https://github.com/user-attachments/assets/f0332b7b-ad5f-4c2c-b73b-4054d6da2a24" />

<!-- demo GIF goes here: scan → tab switching → project-only diff → click-to-open -->

## Install & run

**No install, always latest** ([uv](https://docs.astral.sh/uv/)):
```bash
uvx claude-config-dashboard
```

**pip:**
```bash
pip install claude-config-dashboard
claude-config-dashboard
```

**Claude Code plugin** (adds the `/claude-config-dashboard:show` slash command):
```
/plugin marketplace add AshtonYoon/claude-config-dashboard
/plugin install claude-config-dashboard
/reload-plugins
```
then run `/claude-config-dashboard:show` inside Claude Code.

All three open the dashboard at **http://localhost:9876**. Pass `--port` to use a different one, or `--no-open` to skip launching a browser.

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
| Context Tax | Estimated tokens your config adds to every session, per item, plus a downloadable cleanup script (archive-only, review before running) for unused items |
| Cleanup | Agents, skills, and MCP servers unused for 30+ days |
| Project-only config | MCP servers, skills, commands, hooks, and rules that exist only in the current project's `.claude` |

Clicking any file name opens it in the OS default app (editor, Finder, etc.). Usage counts and "last used" come from parsing your local session transcripts — nothing leaves your machine.

## Security

The dashboard binds to `localhost` only. Opening a file (`/open`) or stopping the server (`/stop`) requires a per-run token embedded in the page and only accepts paths the dashboard itself is already showing you — a malicious web page open in another tab cannot trigger either endpoint. See [CHANGELOG.md](CHANGELOG.md) (v1.6.1) for the fix history.

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

`pip`/`uvx` installs pick up new releases automatically the next time you run them (`uvx` always fetches latest; `pip install -U claude-config-dashboard` to upgrade an existing install).

## Development

```bash
git clone https://github.com/AshtonYoon/claude-config-dashboard
cd claude-config-dashboard
uv venv .venv && uv pip install -p .venv pytest pytest-cov ruff

.venv/bin/pytest --cov            # 67 tests, coverage gate at 80%
.venv/bin/ruff check .
.venv/bin/ruff format .
```

The app lives in [`claude_config_dashboard/`](claude_config_dashboard/) (collectors → usage → enrich → render → server); `dashboard.py` at the repo root is a thin backward-compatible launcher for the plugin command. Design notes: [docs/DESIGN.md](docs/DESIGN.md).

## Requirements

- Python ≥ 3.9, standard library only at runtime (no dependencies to install)
- macOS or Linux (uses `open`/`xdg-open` to launch default apps)
