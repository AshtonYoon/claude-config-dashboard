# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.6.1] - 2026-07-18

### Security
- `/open` and `/stop` are now POST-only and require a per-run CSRF token
  (`X-Dashboard-Token`) embedded in the served HTML. Previously any web page
  open in the browser could trigger `GET /open?path=...` and open arbitrary
  local files with the OS default app.
- `/open` now only accepts paths that the dashboard actually renders as
  clickable links (allowlist), instead of any existing path.
- `Host` header validation (`localhost` / `127.0.0.1` only) to defeat
  DNS-rebinding attacks.

### Added
- `LICENSE` file (MIT — previously only declared in manifests).
- This changelog.

### Fixed
- Module docstring documented a `--project` flag and endpoints that no longer
  exist.

## [1.6.0] - 2026-04-15

### Added
- Project-only config view: diffs the current project's `.claude` against
  `~/.claude` and shows MCP servers, skills, commands, hooks, and rules that
  exist only in the project.

## [1.5.0] - 2026-04-15

### Changed
- Generalized the plugin skills dashboard to work with any installed plugin.

## [1.4.0] - 2026-04-07

### Added
- Anthropic-inspired design system (parchment canvas, terracotta accent,
  serif/sans/mono typography) and a floating character mascot.

### Fixed
- Double browser open on dashboard launch.

## [1.3.0] - 2026-04-07

### Fixed
- Usage stats were computed against the wrong config dir when a project
  `.claude` was present (`CLAUDE_DIR` reset before computing usage stats).

### Added
- Update instructions and marketplace cache workaround in README.

## [1.2.0] - 2026-04-07

### Added
- Apple-inspired design system.
- Simplified config-dir toggle and a Stop server button.

## [1.1.0] - 2026-04-07

### Added
- Usage analytics from session transcripts (skills, agents, MCP servers),
  project scope filter, and a Cleanup tab flagging items unused for 30+ days.
- Scans the current project's `.claude` when present, falling back to
  `~/.claude`.

## [1.0.0] - 2026-04-07

### Added
- Initial release: local web dashboard for installed Claude Code config —
  plugins, agents, skills, commands, hooks, MCP servers, and rules — served
  from the Python standard library only. Packaged as a Claude Code plugin
  with a `/claude-config-dashboard:show` command.
