---
description: Start the Claude Config Dashboard — a local web UI showing all installed plugins, agents, skills, commands, hooks, and MCP servers in ~/.claude. File names are clickable to open in the default app.
allowed-tools: Bash
---

Launch the Claude Config Dashboard web server and open it in the browser.

## Steps

1. Find the bundled dashboard script from the plugin cache:
   ```bash
   find "$HOME/.claude/plugins/cache/claude-config-dashboard" -name "dashboard.py" 2>/dev/null | sort -r | head -1
   ```

2. Check if the server is already running on port 9876:
   ```bash
   lsof -ti :9876
   ```
   If output is non-empty, the server is already running — skip to step 4.

3. Start the server in the background using the path found in step 1:
   ```bash
   python3 "<path from step 1>" &
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
