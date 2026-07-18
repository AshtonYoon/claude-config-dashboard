"""Filesystem locations resolved once at import time."""

from pathlib import Path

HOME_CLAUDE = Path.home() / ".claude"
_cwd_path = Path.cwd() / ".claude"
CWD_CLAUDE = (
    _cwd_path
    if (_cwd_path.is_dir() and _cwd_path.resolve() != HOME_CLAUDE.resolve())
    else None
)
PORT_DEFAULT = 9876

# Claude character mascot image — served at /character
CHARACTER_IMG = Path(__file__).resolve().parent / "character.png"
