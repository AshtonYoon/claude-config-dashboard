# Usage Tracking for Commands, Hooks, and Rules

## Summary
Add usage tracking to the dashboard for commands, hooks, and rules using the same transcript/log-driven approach already used for skills, agents, and MCP servers. Commands will be tracked as invoked, hooks as executed, and rules as loaded.

## Goals
- Show usage count and latest timestamp in the Commands tab
- Show execution count and latest timestamp in the Hooks tab
- Show load count and latest timestamp in the Rules tab
- Keep the implementation within the existing dashboard plugin codebase
- Avoid introducing any new external logging system

## Non-Goals
- Extending the Cleanup tab in this change
- Proving that a rule influenced a response, beyond confirming it was loaded
- Reconstructing usage from data sources the dashboard cannot currently access
- Adding new background services or daemons

## Definitions
- **Commands = Invoked**: a slash command was explicitly called in a session transcript.
- **Hooks = Executed**: a configured hook command ran, based on available hook execution logs.
- **Rules = Loaded**: a rule file path was included in session context, not necessarily acted upon.

## Data Sources
### Commands
Use session transcript JSONL files already scanned by `collect_usage_stats()`. Extend transcript parsing to detect slash command invocation metadata and normalize it to the same identifiers produced by `collect_commands()`.

Normalization rules:
- Strip a leading `/`
- Preserve nested command namespaces such as `agent_prompts/foo`
- Store keys in one canonical slashless form

### Hooks
Continue using `collect_hooks()` to build the configured hook list from settings. Separately, scan available hook execution logs to derive usage counts. Match execution events to configured hooks using this priority order:
1. Resolved script path, when present
2. Normalized `trigger + matcher + command`

This keeps definition discovery and execution counting separate while allowing the UI to merge them.

### Rules
Continue using `collect_rules()` to enumerate rule files by category. Separately, derive rule load events from the same session context material already available to Claude sessions. Count per rule file path so the UI can attach usage directly to individual rule files.

## Data Model
Extend the usage stats structure from:

```python
{
  "skills": {},
  "agents": {},
  "mcp": {},
}
```

To:

```python
{
  "skills": {},
  "agents": {},
  "mcp": {},
  "commands": {},
  "hooks": {},
  "rules": {},
}
```

Each bucket continues to use the existing shape:

```python
{
  "count": 0,
  "last_used": ""
}
```

The same `_update_stat()` helper should be reused so all categories follow identical timestamp semantics.

## Enrichment Layer
Add dedicated enrichment helpers:
- `enrich_commands()`
- `enrich_hooks()`
- `enrich_rules()`

These should mirror the current pattern used for agents, skills, and MCP servers by merging raw collected items with the corresponding usage bucket and attaching:
- `usage_count`
- `last_used`

### Matching Strategy
- Commands: match by normalized command id
- Hooks: match by stable hook identity key
- Rules: match by full rule file path

## UI Changes
### Commands Tab
Add a usage column or badge showing:
- invocation count
- last invocation timestamp via the existing stale/usage badge renderer

Keep sorting controls aligned with the current usage-enabled tabs:
- Name
- Usage Count
- Last Used

### Hooks Tab
Keep the current card layout. Add a usage badge to each hook card showing:
- execution count
- last execution timestamp

### Rules Tab
Show a usage badge per rule file. Label the concept as **Loaded** in surrounding copy where needed to avoid implying behavioral proof.

## Error Handling
- Missing or malformed logs should not break page rendering
- Parsing failures should continue the current best-effort behavior and skip bad entries
- If no usage can be derived for a category, the UI should render zero/empty usage rather than failing

## Testing Strategy
- Verify commands with and without a leading slash normalize to the same key
- Verify nested command namespaces still match collected command entries
- Verify hook execution events map correctly to configured hooks when path exists and when only command text is available
- Verify rule file path matching attaches counts to the correct file
- Verify tabs render gracefully when usage buckets are empty

## Implementation Notes
- Keep the change localized to the existing `dashboard.py` structure
- Reuse `_usage_html()` for display consistency
- Do not extend the Cleanup tab in this change
- Prefer best-effort parsing over strict assumptions about every log entry

## Expected Outcome
After this change, the dashboard will expose usage metadata for commands, hooks, and rules in the same general style already used for skills, agents, and MCP servers, while keeping the semantics explicit:
- commands are invoked
- hooks are executed
- rules are loaded
