#!/usr/bin/env python3
"""Claude Config Dashboard — backward-compatible launcher.

The implementation lives in the claude_config_dashboard package next to this
file. This shim keeps the historical entry point working: the plugin command
(commands/show.md) locates and runs `dashboard.py` from the plugin cache, with
no installation step.

Usage: python3 dashboard.py [--port 9876] [--no-open]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_config_dashboard.server import main

if __name__ == "__main__":
    main()
