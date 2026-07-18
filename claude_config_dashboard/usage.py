"""Usage analytics: parse session transcripts and hook logs into per-item
counts and last-used timestamps, plus staleness labelling."""

import glob as glob_mod
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Log files may be missing, unreadable, malformed, or of the wrong shape;
# usage parsing skips the bad entry (logging it) rather than aborting.
_READ_ERRORS = (OSError, ValueError, AttributeError, TypeError)

_usage_cache: dict = {}  # (claude_dir, project_cwd) → stats dict


def _load_session_map(claude_dir: Path) -> dict:
    """Returns {cwd: set_of_transcript_dirs} from session_start.json."""
    log_path = claude_dir / "logs" / "session_start.json"
    cwd_to_dirs: dict = {}
    if not log_path.exists():
        return cwd_to_dirs
    try:
        for entry in json.loads(log_path.read_text()):
            cwd = entry.get("cwd", "")
            tp = entry.get("transcript_path", "")
            if cwd and tp:
                parent = str(Path(tp).parent)
                cwd_to_dirs.setdefault(cwd, set()).add(parent)
    except _READ_ERRORS as exc:
        log.debug("unreadable session_start.json at %s: %s", log_path, exc)
    return cwd_to_dirs


def list_known_projects(claude_dir: Path) -> list:
    """Return known projects sorted by name, each with cwd and session count."""
    log_path = claude_dir / "logs" / "session_start.json"
    if not log_path.exists():
        return []
    counts: dict = {}
    try:
        for entry in json.loads(log_path.read_text()):
            cwd = entry.get("cwd", "")
            if cwd:
                counts[cwd] = counts.get(cwd, 0) + 1
    except _READ_ERRORS as exc:
        log.debug("unreadable session_start.json at %s: %s", log_path, exc)
    return sorted(
        [{"cwd": cwd, "name": Path(cwd).name, "sessions": n} for cwd, n in counts.items()],
        key=lambda p: p["cwd"],
    )


def _update_stat(bucket: dict, key: str, ts: str) -> None:
    if key not in bucket:
        bucket[key] = {"count": 0, "last_used": ""}
    bucket[key]["count"] += 1
    if ts and (not bucket[key]["last_used"] or ts > bucket[key]["last_used"]):
        bucket[key]["last_used"] = ts


def collect_usage_stats(claude_dir: Path, project_cwd: str = "*") -> dict:
    """Parse transcripts and logs to build usage index.

    project_cwd: "*" for all projects, or an absolute path to scope to one project.
    """
    stats: dict = {"skills": {}, "agents": {}, "mcp": {}}

    if project_cwd == "*":
        patterns = [str(claude_dir / "projects" / "**" / "*.jsonl")]
        recursive = True
    else:
        session_map = _load_session_map(claude_dir)
        dirs = session_map.get(project_cwd, set())
        if not dirs:
            return stats
        patterns = [str(Path(d) / "*.jsonl") for d in dirs]
        recursive = False

    for pattern in patterns:
        for path in glob_mod.glob(pattern, recursive=recursive):
            try:
                with open(path, errors="replace") as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            if entry.get("type") != "assistant":
                                continue
                            ts = entry.get("timestamp", "")
                            for block in entry.get("message", {}).get("content", []):
                                if not isinstance(block, dict) or block.get("type") != "tool_use":
                                    continue
                                name = block.get("name", "")
                                inp = block.get("input", {})
                                if name == "Skill":
                                    key = inp.get("skill", "")
                                    if key:
                                        _update_stat(stats["skills"], key, ts)
                                elif name == "Agent":
                                    key = inp.get("subagent_type", "")
                                    if key:
                                        _update_stat(stats["agents"], key, ts)
                                elif name.startswith("mcp__"):
                                    parts = name.split("__", 2)
                                    if len(parts) >= 2:
                                        _update_stat(stats["mcp"], parts[1], ts)
                        except _READ_ERRORS as exc:
                            log.debug("skipping malformed transcript line in %s: %s", path, exc)
            except OSError as exc:
                log.debug("cannot read transcript %s: %s", path, exc)

    # Supplement MCP from pre_tool_use.json (filtered by cwd when project-scoped)
    log_path = claude_dir / "logs" / "pre_tool_use.json"
    if log_path.exists():
        try:
            for entry in json.loads(log_path.read_text()):
                if project_cwd != "*" and entry.get("cwd", "") != project_cwd:
                    continue
                tn = entry.get("tool_name", "")
                if tn.startswith("mcp__"):
                    parts = tn.split("__", 2)
                    if len(parts) >= 2:
                        key = parts[1]
                        if key not in stats["mcp"]:
                            stats["mcp"][key] = {"count": 0, "last_used": ""}
                        stats["mcp"][key]["count"] += 1
        except _READ_ERRORS as exc:
            log.debug("unreadable pre_tool_use.json at %s: %s", log_path, exc)

    return stats


def get_cached_usage(claude_dir: Path, project_cwd: str) -> dict:
    key = (str(claude_dir), project_cwd)
    if key not in _usage_cache:
        _usage_cache[key] = collect_usage_stats(claude_dir, project_cwd)
    return _usage_cache[key]


def _stale_info(last_used: str) -> tuple:
    """Returns (days_or_None, label, css_class)."""
    if not last_used:
        return None, "Never used", "stale-never"
    try:
        dt = datetime.fromisoformat(last_used.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        total_seconds = max(int(delta.total_seconds()), 0)
        days = total_seconds // 86400
        date_str = dt.strftime("%Y-%m-%d")
        if total_seconds < 3600:
            minutes = max(total_seconds // 60, 1)
            return days, f"Used {minutes}m ago", "stale-recent"
        elif total_seconds < 86400:
            hours = total_seconds // 3600
            return days, f"Used {hours}h ago", "stale-recent"
        elif days <= 7:
            return days, f"Used {days}d ago", "stale-recent"
        elif days <= 30:
            return days, f"Used {days}d ago", "stale-mid"
        else:
            return days, f"Stale · {date_str}", "stale-old"
    except ValueError as exc:
        log.debug("unparseable timestamp %r: %s", last_used, exc)
        return None, "", ""
