# Usage Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add usage metadata for commands, hooks, and rules to the dashboard so each tab shows count and latest timestamp using existing transcript/log sources.

**Architecture:** Extend `dashboard.py`'s existing transcript/log-driven usage pipeline instead of adding new services. Normalize command, hook, and rule identifiers into stable keys, enrich collected tab data with `usage_count`/`last_used`, then render the existing usage badges in the Commands, Hooks, and Rules tabs.

**Tech Stack:** Python 3 standard library, single-file dashboard renderer, Claude transcript JSONL files, Claude hook log JSON files

---

## File Structure

- Modify: `dashboard.py`
  - Add identifier normalization helpers for commands/hooks/rules
  - Extend usage collection to track `commands`, `hooks`, and `rules`
  - Add enrichment helpers for commands/hooks/rules
  - Update Commands/Hooks/Rules renderers to show usage badges
- Test manually against local Claude transcript and log files under `~/.claude/projects` and `~/.claude/logs`
- Optional verification fixture source: existing local files in `~/.claude/projects/**/*.jsonl` and `~/.claude/logs/*.json`

## Task 1: Add stable identity helpers for commands, hooks, and rules

**Files:**
- Modify: `dashboard.py` near `_update_stat` and the collectors that build command/hook/rule records
- Test: `dashboard.py` manual smoke checks via `python3 dashboard.py --no-open`

- [ ] **Step 1: Write the failing normalization checks in a Python REPL snippet**

```python
assert _normalize_command_key('/claude-config-dashboard:show') == 'claude-config-dashboard:show'
assert _normalize_command_key('claude-config-dashboard:show') == 'claude-config-dashboard:show'
assert _normalize_command_key('/agent_prompts/foo') == 'agent_prompts/foo'
assert _normalize_hook_key('PreToolUse', '(all tools)', 'python3 /tmp/hook.py', '/tmp/hook.py') == 'path:/tmp/hook.py'
assert _normalize_rule_key('/Users/ashton/.claude/rules/common/testing.md').endswith('/rules/common/testing.md')
```

- [ ] **Step 2: Run the snippet to verify it fails**

Run:
```bash
python3 - <<'PY'
from dashboard import _normalize_command_key
PY
```

Expected: `ImportError` or `AttributeError` because the normalization helpers do not exist yet.

- [ ] **Step 3: Add the minimal normalization helpers**

```python
def _normalize_command_key(name: str) -> str:
    return str(name or "").strip().lstrip("/")


def _normalize_hook_key(trigger: str, matcher: str, command: str, path: str = "") -> str:
    if path:
        return f"path:{path}"
    return f"meta:{trigger}|{matcher or '(all tools)'}|{command.strip()}"


def _normalize_rule_key(path: str) -> str:
    return str(Path(path).expanduser())
```

- [ ] **Step 4: Reuse helpers in raw collectors so records carry stable keys**

```python
cmds.append({
    "name": md.stem,
    "slash": f"/{md.stem}",
    "description": _first_desc(md),
    "path": str(md),
    "usage_key": _normalize_command_key(md.stem),
})
```

```python
hooks.append({
    "trigger": trigger,
    "matcher": matcher or "(all tools)",
    "command": short_cmd,
    "path": script_path,
    "usage_key": _normalize_hook_key(trigger, matcher or "(all tools)", cmd, script_path),
})
```

```python
files = [{
    "name": f.name,
    "path": str(f),
    "usage_key": _normalize_rule_key(str(f)),
} for f in sorted(cat.glob("*.md"))]
```

- [ ] **Step 5: Run a quick import check**

Run:
```bash
python3 - <<'PY'
from dashboard import _normalize_command_key, _normalize_hook_key, _normalize_rule_key
print(_normalize_command_key('/claude-config-dashboard:show'))
print(_normalize_hook_key('PreToolUse', '(all tools)', 'python3 /tmp/hook.py', '/tmp/hook.py'))
print(_normalize_rule_key('/tmp/rule.md'))
PY
```

Expected:
- prints `claude-config-dashboard:show`
- prints `path:/tmp/hook.py`
- prints `/tmp/rule.md`

## Task 2: Extend usage collection for commands, hooks, and rules

**Files:**
- Modify: `dashboard.py:collect_usage_stats`
- Test: `dashboard.py` manual snippet against local transcript/log samples

- [ ] **Step 1: Write the failing stats check**

```python
stats = collect_usage_stats('*')
assert 'commands' in stats
assert 'hooks' in stats
assert 'rules' in stats
```

- [ ] **Step 2: Run the check to verify it fails**

Run:
```bash
python3 - <<'PY'
from dashboard import collect_usage_stats
stats = collect_usage_stats('*')
print(stats.keys())
assert 'commands' in stats
assert 'hooks' in stats
assert 'rules' in stats
PY
```

Expected: assertion failure because only `skills`, `agents`, and `mcp` exist.

- [ ] **Step 3: Extend the stats buckets and parse command/rule events from transcript JSONL**

```python
stats: dict = {
    "skills": {},
    "agents": {},
    "mcp": {},
    "commands": {},
    "hooks": {},
    "rules": {},
}
```

```python
entry_type = entry.get("type")
ts = entry.get("timestamp", "")

if entry_type == "assistant":
    for block in entry.get("message", {}).get("content", []):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name", "")
        inp = block.get("input", {})
        if name == "Skill":
            _update_stat(stats["skills"], inp.get("skill", ""), ts)
        elif name == "Agent":
            _update_stat(stats["agents"], inp.get("subagent_type", ""), ts)
        elif name.startswith("mcp__"):
            parts = name.split("__", 2)
            if len(parts) >= 2:
                _update_stat(stats["mcp"], parts[1], ts)

if entry_type == "user":
    content = entry.get("message", {}).get("content", "")
    match = re.search(r"<command-name>/?([^<]+)</command-name>", content)
    if match:
        _update_stat(stats["commands"], _normalize_command_key(match.group(1)), ts)
    for rule_path in re.findall(r'"path":"([^"]+/rules/[^"]+\.md)"', line):
        _update_stat(stats["rules"], _normalize_rule_key(rule_path), ts)
```

- [ ] **Step 4: Extend log parsing for hook executions and keep existing MCP supplement**

```python
for log_name in [
    "pre_tool_use.json",
    "post_tool_use.json",
    "user_prompt_submit.json",
    "session_start.json",
    "stop.json",
    "subagent_stop.json",
    "pre_compact.json",
]:
    log_path = CLAUDE_DIR / "logs" / log_name
    if not log_path.exists():
        continue
    for entry in json.loads(log_path.read_text()):
        if project_cwd != "*" and entry.get("cwd", "") != project_cwd:
            continue
        ts = entry.get("timestamp", "")
        hook_name = entry.get("hook_event_name", "")
        tool_input = entry.get("tool_input", {})
        command = str(tool_input.get("command", ""))
        _update_stat(
            stats["hooks"],
            _normalize_hook_key(hook_name, "(all tools)", command, ""),
            ts,
        )
```

Keep the MCP supplement inside this loop by checking `tool_name.startswith('mcp__')` and calling `_update_stat(stats['mcp'], ...)` with the same timestamp.

- [ ] **Step 5: Run a targeted stats smoke test**

Run:
```bash
python3 - <<'PY'
from dashboard import collect_usage_stats
stats = collect_usage_stats('*')
print(sorted(stats.keys()))
print('commands', len(stats['commands']))
print('hooks', len(stats['hooks']))
print('rules', len(stats['rules']))
PY
```

Expected:
- includes `agents`, `commands`, `hooks`, `mcp`, `rules`, `skills`
- hook count is non-zero if local Claude hook logs exist
- command and rule counts may be zero or higher depending on local transcripts, but the call succeeds

## Task 3: Enrich commands, hooks, and rules with usage data

**Files:**
- Modify: `dashboard.py` near `enrich_plugins` / `enrich_data`
- Test: `python3` snippet importing the enrich helpers

- [ ] **Step 1: Write the failing enrichment check**

```python
assert enrich_commands([{"usage_key": "claude-config-dashboard:show"}], {"commands": {}})
```

- [ ] **Step 2: Run the check to verify it fails**

Run:
```bash
python3 - <<'PY'
from dashboard import enrich_commands
PY
```

Expected: `ImportError` because `enrich_commands` does not exist yet.

- [ ] **Step 3: Add enrichment helpers for the new categories**

```python
def enrich_commands(commands: list, usage: dict) -> list:
    command_stats = usage.get("commands", {})
    return [{
        **c,
        "usage_count": command_stats.get(c["usage_key"], {}).get("count", 0),
        "last_used": command_stats.get(c["usage_key"], {}).get("last_used", ""),
    } for c in commands]


def enrich_hooks(hooks: list, usage: dict) -> list:
    hook_stats = usage.get("hooks", {})
    return [{
        **h,
        "usage_count": hook_stats.get(h["usage_key"], {}).get("count", 0),
        "last_used": hook_stats.get(h["usage_key"], {}).get("last_used", ""),
    } for h in hooks]


def enrich_rules(rules: list, usage: dict) -> list:
    rule_stats = usage.get("rules", {})
    return [{
        **r,
        "files": [{
            **f,
            "usage_count": rule_stats.get(f["usage_key"], {}).get("count", 0),
            "last_used": rule_stats.get(f["usage_key"], {}).get("last_used", ""),
        } for f in r["files"]],
    } for r in rules]
```

- [ ] **Step 4: Wire the helpers into `enrich_data()`**

```python
def enrich_data(raw: dict, usage: dict) -> dict:
    return {
        "plugins": enrich_plugins(raw["plugins"], usage),
        "agents": enrich_agents(raw["agents"], usage),
        "skills": enrich_skills(raw["skills"], usage),
        "commands": enrich_commands(raw["commands"], usage),
        "hooks": enrich_hooks(raw["hooks"], usage),
        "mcp_servers": enrich_mcp(raw["mcp_servers"], usage),
        "rules": enrich_rules(raw["rules"], usage),
    }
```

- [ ] **Step 5: Run a direct helper test**

Run:
```bash
python3 - <<'PY'
from dashboard import enrich_commands, enrich_hooks, enrich_rules
usage = {
    'commands': {'claude-config-dashboard:show': {'count': 2, 'last_used': '2026-04-11T00:00:00Z'}},
    'hooks': {'path:/tmp/hook.py': {'count': 3, 'last_used': '2026-04-11T00:00:00Z'}},
    'rules': {'/tmp/rule.md': {'count': 4, 'last_used': '2026-04-11T00:00:00Z'}},
}
print(enrich_commands([{'usage_key': 'claude-config-dashboard:show'}], usage))
print(enrich_hooks([{'usage_key': 'path:/tmp/hook.py'}], usage))
print(enrich_rules([{'files': [{'usage_key': '/tmp/rule.md'}]}], usage))
PY
```

Expected: each printed record contains `usage_count` and `last_used`.

## Task 4: Render usage badges in Commands, Hooks, and Rules tabs

**Files:**
- Modify: `dashboard.py:render_commands`, `dashboard.py:render_hooks`, `dashboard.py:render_rules`, and the related tab copy if needed
- Test: `python3 dashboard.py --no-open` and browser verification at `http://localhost:9876`

- [ ] **Step 1: Write the failing renderer expectation**

```python
html = render_commands([{
    'slash': '/claude-config-dashboard:show',
    'description': 'Open dashboard',
    'path': '/tmp/show.md',
    'usage_count': 2,
    'last_used': '2026-04-11T00:00:00Z',
}])
assert 'usage-count' in html
```

- [ ] **Step 2: Run the check to verify it fails**

Run:
```bash
python3 - <<'PY'
from dashboard import render_commands
html = render_commands([{
    'slash': '/claude-config-dashboard:show',
    'description': 'Open dashboard',
    'path': '/tmp/show.md',
    'usage_count': 2,
    'last_used': '2026-04-11T00:00:00Z',
}])
print(html)
assert 'usage-count' in html
PY
```

Expected: assertion failure because the current Commands table renders no usage badge.

- [ ] **Step 3: Add usage display to Commands and Hooks**

```python
usage_html = _usage_html({
    "count": c.get("usage_count", 0),
    "last_used": c.get("last_used", ""),
})
rows.append(
    f'<tr data-count="{c.get("usage_count", 0)}" data-last="{_e(c.get("last_used", ""))}">'
    f'<td class="whitespace-nowrap">{link}</td>'
    f'<td style="color:var(--text-s)">{desc}</td>'
    f'<td>{usage_html}</td></tr>'
)
```

```python
usage_html = _usage_html({"count": h.get("usage_count", 0), "last_used": h.get("last_used", "")})
parts.append(f"""<div class="card flex items-start gap-4">
  <span class="badge {color}" style="white-space:nowrap;margin-top:2px">{_e(h['trigger'])}</span>
  <div style="flex:1;min-width:0">{cmd_html}
    {f'<p style="font-size:11px;color:var(--text-t);margin-top:4px">matcher: {_e(h["matcher"] )}</p>' if h.get("matcher") else ''}
    {f'<div style="margin-top:8px">{usage_html}</div>' if usage_html else ''}
  </div>
</div>""")
```

- [ ] **Step 4: Add per-file usage display to Rules and clarify the label as Loaded**

```python
files_html = "".join(
    f'<li style="font-size:13px;padding:6px 0">'
    f'<div>{_open_link(_e(f["name"]), f["path"], "al")}</div>'
    f'{f"<div style=\"margin-top:4px\">{_usage_html({\"count\": f.get(\"usage_count\", 0), \"last_used\": f.get(\"last_used\", \"\")}).replace("Usage Count", "Loaded")}</div>" if _usage_html({"count": f.get("usage_count", 0), "last_used": f.get("last_used", "")}) else ""}'
    f'</li>'
    for f in r["files"]
)
```

If replacing text inside `_usage_html()` feels brittle, add a small optional `count_label` parameter to `_usage_html()` and pass `count_label="Loaded"` from `render_rules()`.

- [ ] **Step 5: Run the dashboard and verify the UI**

Run:
```bash
python3 dashboard.py --no-open
```

Expected: server starts without syntax errors.

Then open `http://localhost:9876` and verify:
- Commands tab shows a usage column or badge
- Hooks tab shows execution badges on cards
- Rules tab shows loaded badges per file
- Empty usage still renders cleanly

## Task 5: Final verification and documentation touch-up

**Files:**
- Modify: `README.md` only if the UI wording now needs an explicit usage note
- Verify: `dashboard.py`, local browser session

- [ ] **Step 1: Check whether README needs an update**

Inspect whether the Features table should mention that Commands, Hooks, and Rules now include usage metadata.

- [ ] **Step 2: If README wording is stale, write the minimal update**

```markdown
| Commands | Slash commands with descriptions and usage metadata — click to open file |
| Hooks | Hook scripts by trigger type with execution metadata — click to open script |
| Rules | Rule files by category with load metadata — click to open file |
```

Skip this step if the existing README is still acceptable.

- [ ] **Step 3: Run a syntax smoke test**

Run:
```bash
python3 -m py_compile dashboard.py
```

Expected: no output.

- [ ] **Step 4: Run a focused behavior smoke test**

Run:
```bash
python3 - <<'PY'
from dashboard import collect_usage_stats, load_settings, collect_commands, collect_hooks, collect_rules, collect_mcp_servers_raw, collect_plugins_raw, collect_agents_raw, collect_skills_raw, enrich_data
settings = load_settings()
raw = {
    'plugins': collect_plugins_raw(),
    'agents': collect_agents_raw(),
    'skills': collect_skills_raw(),
    'commands': collect_commands(),
    'hooks': collect_hooks(settings),
    'mcp_servers': collect_mcp_servers_raw(settings),
    'rules': collect_rules(),
}
usage = collect_usage_stats('*')
data = enrich_data(raw, usage)
print(type(data['commands']).__name__, len(data['commands']))
print(type(data['hooks']).__name__, len(data['hooks']))
print(type(data['rules']).__name__, len(data['rules']))
PY
```

Expected: prints list counts for all three categories without exceptions.

- [ ] **Step 5: Commit**

```bash
git add dashboard.py README.md docs/superpowers/specs/2026-04-11-usage-tracking-design.md docs/superpowers/plans/2026-04-11-usage-tracking.md
git commit -m "feat: track command hook and rule usage"
```

Only run this step if the user explicitly asks for a commit.

## Self-Review

- **Spec coverage:**
  - Commands invoked counts and timestamps: covered in Tasks 1-4
  - Hooks executed counts and timestamps: covered in Tasks 1-4
  - Rules loaded counts and timestamps: covered in Tasks 1-4
  - No Cleanup tab changes: preserved by limiting UI work to Commands/Hooks/Rules only
- **Placeholder scan:** no `TODO`, `TBD`, or unresolved implementation markers remain
- **Type consistency:** all new categories use `usage_key`, `usage_count`, and `last_used` consistently across collection, enrichment, and rendering
