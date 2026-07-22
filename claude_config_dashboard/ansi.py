"""Terminal color helpers for --report: 24-bit ANSI, stdlib only.

Every function takes an `enabled` flag and returns the input text unchanged
when it's False, so --report's plain-text output (piped, redirected, or
color explicitly off) is byte-identical to a build with no color support at
all. Colors mirror the web dashboard's palette (styles.css): terracotta
brand, red for stale/idle, green/amber for verdict severity.
"""

import os
import sys
from typing import Optional

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"

BRAND = (201, 100, 66)  # --brand in styles.css
BRAND_DEEP = (181, 51, 51)  # #b53333 -- stale-old / badge-red
NEUTRAL = (135, 134, 127)  # #87867f -- text-t
GOOD = (61, 143, 90)
WARN = (196, 143, 44)
BAD = (181, 51, 51)


def supports_color(force: Optional[bool] = None, stream=None) -> bool:
    """True if ANSI escapes should be emitted.

    Precedence: an explicit `force` argument (from --color/--no-color) wins
    outright; otherwise NO_COLOR (https://no-color.org) disables, FORCE_COLOR
    enables, and the default is whether stream is a real terminal.
    """
    if force is not None:
        return force
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR") is not None:
        return True
    stream = stream or sys.stdout
    return hasattr(stream, "isatty") and stream.isatty()


def _rgb_fg(rgb: tuple) -> str:
    r, g, b = rgb
    return f"\x1b[38;2;{r};{g};{b}m"


def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def _lerp_rgb(start: tuple, end: tuple, t: float) -> tuple:
    return (_lerp(start[0], end[0], t), _lerp(start[1], end[1], t), _lerp(start[2], end[2], t))


def style(text: str, *codes: str, enabled: bool = True) -> str:
    """Wrap text in the given ANSI codes as a single span (safe for substring
    matching -- codes sit outside the text, not interleaved character-by-character)."""
    if not enabled or not text:
        return text
    return "".join(codes) + text + RESET


def color(text: str, rgb: tuple, *, bold: bool = False, enabled: bool = True) -> str:
    codes = ([BOLD] if bold else []) + [_rgb_fg(rgb)]
    return style(text, *codes, enabled=enabled)


def gradient_text(text: str, start: tuple, end: tuple, *, bold: bool = False, enabled: bool = True) -> str:
    """Colors each character along a linear gradient from start to end RGB.

    Only used for decorative spans (titles, bar fill characters) that no test
    or downstream consumer needs to substring-match as a contiguous run --
    interleaving an escape code per character breaks that.
    """
    if not enabled or not text:
        return text
    n = max(len(text) - 1, 1)
    prefix = BOLD if bold else ""
    out = [f"{prefix}{_rgb_fg(_lerp_rgb(start, end, i / n))}{ch}" for i, ch in enumerate(text)]
    out.append(RESET)
    return "".join(out)


def gradient_bar(fraction: float, width: int, start: tuple, end: tuple, *, enabled: bool = True) -> str:
    """A width-wide bar: the first `fraction` filled with a start->end
    gradient, the remainder a dim track."""
    fraction = max(0.0, min(1.0, fraction))
    filled = round(width * fraction)
    if not enabled:
        return "#" * filled + "-" * (width - filled)
    bar = gradient_text("█" * filled, start, end, enabled=True) if filled else ""
    track = f"{DIM}{'░' * (width - filled)}{RESET}" if filled < width else ""
    return bar + track


def verdict_color(pct: float) -> tuple:
    """Severity color for a dead-weight percentage: green -> amber -> red."""
    if pct < 20:
        return GOOD
    if pct < 50:
        return WARN
    return BAD
