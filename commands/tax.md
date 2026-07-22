---
description: Print a plain-text verdict on your ~/.claude config — measured per-session token cost (not estimated, from your real session history), the install-vs-actually-used gap, and the heaviest idle items. No browser, no server — faster than the full dashboard when you just want the answer.
allowed-tools: Bash
---

Run the Claude Config Dashboard's report mode and show the result directly.

## Steps

1. Pick a launcher, first available wins:
   ```bash
   command -v claude-config-dashboard || command -v uvx >/dev/null && echo "uvx claude-config-dashboard" || find "$HOME/.claude/plugins/cache/claude-config-dashboard" -name "dashboard.py" 2>/dev/null | sort -r | head -1
   ```
   - Installed console script: `claude-config-dashboard`
   - uvx (no install needed): `uvx claude-config-dashboard`
   - Plugin cache fallback: `python3 "<found dashboard.py path>"`

2. Run it in report mode:
   ```bash
   <launcher> --report
   ```

3. Show the report output to the user as-is — it's already plain text, no need to reformat or summarize it further.

4. If the report's verdict mentions reclaimable tokens, tell the user they can run `<launcher> --report --clean` to also print a review-first archive script (`mv`-only, never deletes) that they can save and run themselves.
