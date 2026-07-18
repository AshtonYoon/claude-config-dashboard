"""CSRF defenses for the state-changing endpoints (/open, /stop)."""

import hmac
import secrets
from pathlib import Path

# Per-run token: embedded in the served HTML, required as X-Dashboard-Token.
# Other origins cannot read it, so cross-site requests fail the check.
SESSION_TOKEN = secrets.token_urlsafe(32)

# Only paths actually rendered as clickable links may be opened via /open.
OPENABLE_PATHS: set = set()


def register_openable(path: str) -> None:
    OPENABLE_PATHS.add(str(Path(path).expanduser().resolve()))


def is_openable(path: str) -> bool:
    if not path:
        return False
    resolved = Path(path).expanduser().resolve()
    return str(resolved) in OPENABLE_PATHS and resolved.exists()


def is_authorized(host_header: str, token: str) -> bool:
    # Host check defeats DNS rebinding; token check defeats CSRF.
    host = host_header.rsplit(":", 1)[0]
    if host not in ("localhost", "127.0.0.1"):
        return False
    return hmac.compare_digest(token, SESSION_TOKEN)
