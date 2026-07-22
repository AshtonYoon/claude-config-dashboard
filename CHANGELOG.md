# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.10.0] - 2026-07-22

### Added
- **Measured Context Tax.** The Context Tax hero now shows the *measured*
  tokens every session starts with — parsed from your own session transcripts
  (cache-creation + cache-read + input tokens at each session's first turn),
  not a `chars ÷ 4` estimate. Because it comes from real usage records, this
  number already includes Claude Code's system prompt and MCP tool schemas —
  the parts the static estimator explicitly could not measure. The per-category
  static estimate is kept below as a "where your own config contributes"
  breakdown, with an explicit "Claude Code baseline (system prompt + MCP
  schemas), not yours to cut" line so the measured total reconciles with the
  config estimate. On this maintainer's machine the measured median (~47k) is
  ~2.7× the old static headline (~17k), which is the point: the old number
  understated real per-session cost. Subagent (sidechain) transcripts are
  excluded so they don't contaminate the median.
- **Home hero band.** A persistent banner above the tabs surfaces the measured
  session cost plus the install-vs-actually-used gap ("N / M agents used ·
  K never used") — the answer to "why not just `/context`?": `/context` is a
  point-in-time snapshot, this is measured across your real session history.
- **Usage-window disclaimer.** Both the hero band and the Context Tax footnote
  now state the date the usage window starts from, so "never used" is read as
  "not used since <date>" rather than an absolute claim.

## [1.9.0] - 2026-07-19

### Added
- **Cleanup script generator** on the Context Tax tab: for items unused
  30+ days, generates a reviewable POSIX shell script that archives them
  (`mv` into a dated `.claude/_archive/<date>/` folder — never `rm`) via
  "Download cleanup script (.sh)" or "Copy to clipboard". The script is
  built entirely client-side from data already on the page; no new server
  endpoint or filesystem-mutation capability was added, so the dashboard
  remains strictly read-only from the browser's perspective. Plugin-bundled
  skills and symlinked skills are listed as skipped, with a one-line reason,
  since they need manual handling (`/plugin uninstall`, symlink target).

## [1.8.0] - 2026-07-19

### Added
- **Context Tax tab**: estimates how many tokens your installed config adds
  to every Claude Code session — full content for CLAUDE.md and rules,
  listing lines (name + description) for skills, agents, and commands, with
  plugin bundles summing their child skills. Highlights "reclaimable"
  tokens: items unused for 30+ days that still cost context each session.
  Estimates use chars ÷ 4 and say so; MCP tool schemas are explicitly out
  of scope (not statically measurable).

## [1.7.0] - 2026-07-18

### Added
- PyPI packaging: install with `uvx claude-config-dashboard` or
  `pip install claude-config-dashboard`; releases publish via GitHub
  Actions Trusted Publishing on version tags.
- Test suite (67 tests, 93% coverage) with an isolated synthetic `~/.claude`
  fixture; coverage gate at 80%.
- GitHub Actions CI: ruff lint/format and pytest on ubuntu/macos ×
  Python 3.9/3.11/3.13, plus a version-sync check across the four manifests.

### Changed
- `dashboard.py` (1,523 lines) split into the `claude_config_dashboard`
  package (collectors / usage / enrich / render / security / server), with
  HTML, CSS, and JS extracted to `string.Template` files. The root
  `dashboard.py` remains as a thin launcher for the plugin command.
- Silent `except: pass` blocks replaced with narrowed exceptions and
  `logging`; new `--verbose` flag surfaces skipped/unreadable config files.

### Fixed
- Skills named `SKILL.md` (the documented casing) were invisible on
  case-sensitive filesystems (Linux); lookup now checks `SKILL.md` first.

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
