---
description: Start the Claude Config Dashboard — a local web UI showing installed Claude config from ~/.claude plus a project-only comparison view for MCP servers, skills, commands, hooks, and rules found only in the current project's .claude. File names are clickable to open in the default app.
allowed-tools: Bash
---

Launch the Claude Config Dashboard web server and open it in the browser.

## Steps

1. Check if the server is already running on port 9876:
   ```bash
   lsof -ti :9876
   ```
   If output is non-empty, the server is already running — skip to step 4.

2. Pick a launcher, first available wins:
   ```bash
   command -v claude-config-dashboard || command -v uvx >/dev/null && echo "uvx claude-config-dashboard" || find "$HOME/.claude/plugins/cache/claude-config-dashboard" -name "dashboard.py" 2>/dev/null | sort -r | head -1
   ```
   - Installed console script: `claude-config-dashboard`
   - uvx (no install needed): `uvx claude-config-dashboard`
   - Plugin cache fallback: `python3 "<found dashboard.py path>"`

3. Start the server in the background using the launcher from step 2:
   ```bash
   <launcher> --no-open &
   ```
   Wait 1 second for it to start.

4. Open the dashboard in the browser:
   ```bash
   open http://localhost:9876
   ```

5. Tell the user:
   - Dashboard URL: http://localhost:9876
   - Clicking any file name opens it in the default app (editor, Finder, etc.)
   - To stop the server: `kill $(lsof -ti :9876)`
