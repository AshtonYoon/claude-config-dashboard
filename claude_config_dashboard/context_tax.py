"""Context Tax: estimate how many tokens the installed config adds to every
Claude Code session's context window.

What counts, per category:
- CLAUDE.md   → full file content (injected verbatim each session)
- rules       → full file content (injected verbatim each session)
- skills      → name + frontmatter description (the listing line; bodies load
                only on invocation). Plugin bundles sum their child skills.
- agents      → name + frontmatter description (the agent-type listing)
- commands    → name + description line

Not estimable statically: MCP tool schemas (produced by running servers).
Hooks run harness-side and add no context cost. All numbers are estimates
using the common chars ÷ 4 approximation.
"""

import logging
import math
from datetime import datetime
from pathlib import Path

from .collectors import _parse_frontmatter, _skill_content_file
from .usage import _stale_info

log = logging.getLogger(__name__)

STALE_DAYS = 30


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError as exc:
        log.debug("cannot read %s: %s", path, exc)
        return ""


def _full_description(md_path: str) -> str:
    """Re-read the untruncated frontmatter description (collectors truncate)."""
    if not md_path:
        return ""
    return _parse_frontmatter(Path(md_path)).get("description", "")


def _listing_tokens(name: str, description: str) -> int:
    return estimate_tokens(f"{name}: {description}")


def _bundle_tokens(skills_dir: str) -> int:
    """Sum listing tokens over every skill shipped by a plugin bundle."""
    base = Path(skills_dir)
    if not base.is_dir():
        return 0
    total = 0
    for item in sorted(base.iterdir()):
        if item.name.startswith(".") or item.name == "learned":
            continue
        if item.is_dir():
            content = _skill_content_file(item)
            desc = _parse_frontmatter(content).get("description", "") if content else ""
            total += _listing_tokens(item.name, desc)
        elif item.suffix == ".md":
            desc = _parse_frontmatter(item).get("description", "")
            total += _listing_tokens(item.stem, desc)
    return total


def _is_stale(last_used: str) -> bool:
    if not last_used:
        return True
    days, _, _ = _stale_info(last_used)
    return days is not None and days > STALE_DAYS


def _skill_archive_source(s: dict) -> tuple:
    """What `mv` target archives this skill: its own directory for
    directory-based skills, the .md file itself for single-file skills.
    Returns (archive_source_or_None, skip_reason_or_None)."""
    if s.get("plugin_namespace"):
        return None, "plugin bundle — remove via /plugin uninstall instead"
    if s.get("is_symlink"):
        return None, "symlinked skill — remove the symlink target manually"
    path = s.get("path", "")
    if not path:
        return None, "no source file recorded"
    p = Path(path)
    if p.parent.name == s["slug"]:
        return str(p.parent), None  # directory-based skill: archive the whole folder
    return str(p), None  # single-file skill: archive the .md file


def compute_context_tax(claude_dir: Path, data: dict) -> dict:
    """Build the per-session token cost breakdown from enriched dashboard data."""
    claude_md_items = []
    claude_md = claude_dir / "CLAUDE.md"
    if claude_md.exists():
        claude_md_items.append(
            {
                "name": "CLAUDE.md",
                "path": str(claude_md),
                "tokens": estimate_tokens(_read_text(claude_md)),
                "kind": "claude_md",
                "last_used": None,
            }
        )

    rule_items = []
    for rule in data["rules"]:
        for f in rule.get("files", []):
            rule_items.append(
                {
                    "name": f"{rule['category']}/{f['name']}",
                    "path": f["path"],
                    "tokens": estimate_tokens(_read_text(Path(f["path"]))),
                    "kind": "rule",
                    "last_used": None,
                }
            )

    skill_items = []
    for s in data["skills"]:
        if s.get("plugin_namespace"):
            tokens = _bundle_tokens(s.get("path", ""))
        else:
            tokens = _listing_tokens(s["name"], _full_description(s.get("path", "")))
        archive_source, skip_reason = _skill_archive_source(s)
        skill_items.append(
            {
                "name": s["slug"],
                "path": s.get("path", ""),
                "tokens": tokens,
                "kind": "skill",
                "last_used": s.get("last_used", ""),
                "archive_source": archive_source,
                "skip_reason": skip_reason,
            }
        )

    agent_items = []
    for a in data["agents"]:
        path = a.get("path", "")
        agent_items.append(
            {
                "name": a["slug"],
                "path": path,
                "tokens": _listing_tokens(a["name"], _full_description(path)),
                "kind": "agent",
                "last_used": a.get("last_used", ""),
                "archive_source": path or None,
                "skip_reason": None if path else "no source file recorded",
            }
        )

    command_items = []
    for c in data["commands"]:
        desc = _full_description(c.get("path", "")) or c.get("description", "")
        command_items.append(
            {
                "name": c["slash"],
                "path": c.get("path", ""),
                "tokens": _listing_tokens(c["name"], desc),
                "kind": "command",
                "last_used": None,
            }
        )

    categories = [
        {"key": "claude_md", "label": "CLAUDE.md", "items": claude_md_items},
        {"key": "rules", "label": "Rules", "items": rule_items},
        {"key": "skills", "label": "Skills", "items": skill_items},
        {"key": "agents", "label": "Agents", "items": agent_items},
        {"key": "commands", "label": "Commands", "items": command_items},
    ]
    result_categories = []
    for cat in categories:
        items = sorted(cat["items"], key=lambda i: i["tokens"], reverse=True)
        result_categories.append({**cat, "items": items, "tokens": sum(i["tokens"] for i in items)})

    reclaimable = [
        i
        for i in (skill_items + agent_items)
        if i["last_used"] is not None and _is_stale(i["last_used"]) and i["tokens"] > 0
    ]
    reclaimable.sort(key=lambda i: i["tokens"], reverse=True)

    return {
        "total_tokens": sum(c["tokens"] for c in result_categories),
        "categories": result_categories,
        "reclaimable_items": reclaimable,
        "reclaimable_tokens": sum(i["tokens"] for i in reclaimable),
        "claude_dir": str(claude_dir),
        "archive_dir": str(claude_dir / "_archive" / datetime.now().strftime("%Y-%m-%d")),
    }
