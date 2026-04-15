#!/usr/bin/env python3
"""
Claude Config Dashboard
Starts a local HTTP server and opens the dashboard in the browser.

  /                → serves the dashboard HTML (global scope)
  /?project=/path  → serves dashboard scoped to a specific project
  /open?path=      → opens the file with the OS default app

Usage: python3 dashboard.py [--port 9876] [--project /path/to/project]
"""

import argparse
import glob as glob_mod
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HOME_CLAUDE = Path.home() / ".claude"
_cwd_path = Path.cwd() / ".claude"
CWD_CLAUDE = _cwd_path if (_cwd_path.is_dir() and _cwd_path.resolve() != HOME_CLAUDE.resolve()) else None
CLAUDE_DIR = HOME_CLAUDE  # mutable global used by collectors; set before each collection
PORT_DEFAULT = 9876

# Claude character mascot image — served at /character
_CHARACTER_IMG = Path(__file__).parent / "character.png"


# ─── Project / Session Mapping ────────────────────────────────────────────────

def _load_session_map() -> dict:
    """Returns {cwd: set_of_transcript_dirs} from session_start.json."""
    log = CLAUDE_DIR / "logs" / "session_start.json"
    cwd_to_dirs: dict = {}
    if not log.exists():
        return cwd_to_dirs
    try:
        for entry in json.loads(log.read_text()):
            cwd = entry.get("cwd", "")
            tp = entry.get("transcript_path", "")
            if cwd and tp:
                parent = str(Path(tp).parent)
                cwd_to_dirs.setdefault(cwd, set()).add(parent)
    except Exception:
        pass
    return cwd_to_dirs


def list_known_projects() -> list:
    """Return known projects sorted by name, each with cwd and session count."""
    log = CLAUDE_DIR / "logs" / "session_start.json"
    if not log.exists():
        return []
    counts: dict = {}
    try:
        for entry in json.loads(log.read_text()):
            cwd = entry.get("cwd", "")
            if cwd:
                counts[cwd] = counts.get(cwd, 0) + 1
    except Exception:
        pass
    return sorted(
        [{"cwd": cwd, "name": Path(cwd).name, "sessions": n} for cwd, n in counts.items()],
        key=lambda p: p["cwd"],
    )


# ─── Usage Stats ──────────────────────────────────────────────────────────────

_usage_cache: dict = {}  # project_cwd → stats dict


def _update_stat(bucket: dict, key: str, ts: str) -> None:
    if key not in bucket:
        bucket[key] = {"count": 0, "last_used": ""}
    bucket[key]["count"] += 1
    if ts and (not bucket[key]["last_used"] or ts > bucket[key]["last_used"]):
        bucket[key]["last_used"] = ts


def collect_usage_stats(project_cwd: str = "*") -> dict:
    """Parse transcripts and logs to build usage index.

    project_cwd: "*" for all projects, or an absolute path to scope to one project.
    """
    stats: dict = {"skills": {}, "agents": {}, "mcp": {}}

    if project_cwd == "*":
        patterns = [str(CLAUDE_DIR / "projects" / "**" / "*.jsonl")]
        recursive = True
    else:
        session_map = _load_session_map()
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
                        except Exception:
                            pass
            except Exception:
                pass

    # Supplement MCP from pre_tool_use.json (filtered by cwd when project-scoped)
    log_path = CLAUDE_DIR / "logs" / "pre_tool_use.json"
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
        except Exception:
            pass

    return stats


def get_cached_usage(project_cwd: str) -> dict:
    if project_cwd not in _usage_cache:
        _usage_cache[project_cwd] = collect_usage_stats(project_cwd)
    return _usage_cache[project_cwd]


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
    except Exception:
        return None, "", ""


def _usage_html(stat: dict) -> str:
    count = stat.get("count", 0)
    last_used = stat.get("last_used", "")
    days, label, cls = _stale_info(last_used)
    if not cls:
        return ""
    title = _e(last_used) if last_used else ""
    count_badge = f'<span class="badge usage-count">{count}×</span> ' if count > 0 else ""
    return f'{count_badge}<span class="badge {cls}" title="{title}">{_e(label)}</span>'


# ─── Raw Data Collectors ──────────────────────────────────────────────────────

def load_settings() -> dict:
    p = CLAUDE_DIR / "settings.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _parse_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    result = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
    return result


def _first_desc(path: Path) -> str:
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return ""
    in_front = past_front = False
    for line in text.splitlines():
        s = line.strip()
        if s == "---":
            if not in_front and not past_front:
                in_front = True; continue
            elif in_front:
                in_front = False; past_front = True; continue
        if in_front:
            continue
        if s and not s.startswith("#") and not s.startswith("<!--"):
            return s[:120]
    return ""


def collect_plugins_raw(settings: dict) -> list:
    enabled = settings.get("enabledPlugins", {})
    marketplaces = settings.get("extraKnownMarketplaces", {})
    cache_dir = CLAUDE_DIR / "plugins" / "cache"
    official_repos = {
        "anthropic-agent-skills": "https://github.com/anthropics/anthropic-agent-skills",
        "claude-plugins-official": "https://github.com/anthropics/claude-plugins-official",
        "playwright-skill": "https://github.com/anthropics/playwright-skill",
    }
    plugins = []
    for plugin_key, is_enabled in enabled.items():
        parts = plugin_key.split("@", 1)
        plugin_name = parts[0] if len(parts) == 2 else plugin_key
        marketplace = parts[1] if len(parts) == 2 else ""

        repo_url = ""
        if marketplace in marketplaces:
            src = marketplaces[marketplace].get("source", {})
            if src.get("source") == "github":
                repo_url = f"https://github.com/{src.get('repo', '')}"
        elif marketplace in official_repos:
            repo_url = official_repos[marketplace]

        version = description = installed_at = ""
        readme_path = ""
        mc = cache_dir / marketplace / plugin_name
        if mc.exists():
            versions = [d for d in mc.iterdir() if d.is_dir()]
            if versions:
                latest = sorted(versions, key=lambda p: p.name)[-1]
                version = latest.name
                pkg = latest / "package.json"
                if pkg.exists():
                    d = json.loads(pkg.read_text())
                    description = d.get("description", "")
                    repo_url = repo_url or d.get("homepage", "") or d.get("repository", {}).get("url", "").replace("git+", "").replace(".git", "")
                for name in ("README.md", "readme.md"):
                    rp = latest / name
                    if rp.exists():
                        readme_path = str(rp)
                        break
                installed_at = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d")

        plugins.append({
            "label": plugin_name.replace("-", " ").title(),
            "name": plugin_name,
            "marketplace": marketplace,
            "version": version,
            "description": description,
            "repo_url": repo_url,
            "enabled": is_enabled,
            "installed_at": installed_at,
            "readme_path": readme_path,
        })
    return plugins


def _categorize_agent(name: str) -> str:
    n = name.lower()
    if n.endswith("-pro"): return "Language Pro"
    if any(x in n for x in ["seo-", "content-", "marketer"]): return "Content & SEO"
    if any(x in n for x in ["cloud-", "kubernetes", "terraform", "devops", "deployment", "docker", "network"]): return "DevOps & Infra"
    if any(x in n for x in ["database", "sql", "postgres", "mlops", "data-"]): return "Data & DB"
    if any(x in n for x in ["security", "audit"]): return "Security"
    if any(x in n for x in ["test", "e2e", "tdd"]): return "Testing"
    if any(x in n for x in ["frontend", "ui-", "flutter", "mobile", "ios", "unity"]): return "Frontend & Mobile"
    if any(x in n for x in ["customer", "sales", "hr-", "legal", "business", "quant", "risk"]): return "Business"
    if any(x in n for x in ["ai-", "ml-", "prompt", "context", "llm"]): return "AI & ML"
    return "General"


def collect_agents_raw() -> list:
    d = CLAUDE_DIR / "agents"
    if not d.exists():
        return []
    agents = []
    for md in sorted(d.glob("*.md")):
        if md.name == "LICENSE":
            continue
        front = _parse_frontmatter(md)
        name = front.get("name", md.stem)
        tools_raw = front.get("tools", "")
        tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
        agents.append({
            "file": md.name,
            "path": str(md),
            "name": name or md.stem,
            "slug": md.stem,
            "description": front.get("description", "")[:120],
            "tools": tools[:6],
            "category": _categorize_agent(name or md.stem),
        })
    return agents


def collect_skills_raw() -> list:
    skills_dir = CLAUDE_DIR / "skills"
    cache_dir = CLAUDE_DIR / "plugins" / "cache"
    skills = []

    def scan(base: Path, source: str):
        if not base.exists():
            return
        for item in sorted(base.iterdir()):
            if item.name.startswith(".") or item.name == "learned":
                continue
            if item.is_dir():
                skill_md = item / "skill.md"
                readme = item / "README.md"
                content_path = skill_md if skill_md.exists() else (readme if readme.exists() else None)
                front = _parse_frontmatter(content_path) if content_path else {}
                name = front.get("name", item.name)
                desc = front.get("description", _first_desc(content_path) if content_path else "")
                skills.append({
                    "name": name or item.name,
                    "slug": item.name,
                    "description": desc[:100],
                    "source": source,
                    "is_symlink": item.is_symlink(),
                    "path": str(content_path) if content_path else "",
                })
            elif item.suffix == ".md":
                front = _parse_frontmatter(item)
                skills.append({
                    "name": front.get("name", item.stem) or item.stem,
                    "slug": item.stem,
                    "description": front.get("description", _first_desc(item))[:100],
                    "source": source,
                    "is_symlink": False,
                    "path": str(item),
                })

    def count_skill_entries(base: Path) -> int:
        if not base.exists():
            return 0
        total = 0
        for item in base.iterdir():
            if item.name.startswith(".") or item.name == "learned":
                continue
            if item.is_dir() or item.suffix == ".md":
                total += 1
        return total

    scan(skills_dir, "custom")

    seen_plugins: set[str] = set()
    if cache_dir.exists():
        for marketplace_dir in sorted(d for d in cache_dir.iterdir() if d.is_dir() and not d.name.startswith(".")):
            for plugin_dir in sorted(d for d in marketplace_dir.iterdir() if d.is_dir() and not d.name.startswith(".")):
                versions = [d for d in plugin_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
                if not versions:
                    continue
                latest = sorted(versions, key=lambda p: p.name)[-1]
                skills_path = latest / "skills"
                if not skills_path.exists() or plugin_dir.name in seen_plugins:
                    continue
                seen_plugins.add(plugin_dir.name)
                pkg = latest / "package.json"
                description = ""
                if pkg.exists():
                    try:
                        description = json.loads(pkg.read_text()).get("description", "")
                    except Exception:
                        description = ""
                skill_count = count_skill_entries(skills_path)
                if skill_count == 0:
                    continue
                skills.append({
                    "name": f"{plugin_dir.name} ({skill_count} skills)",
                    "slug": plugin_dir.name,
                    "plugin_namespace": plugin_dir.name,
                    "description": (description or f"Skill bundle from {plugin_dir.name} plugin")[:100],
                    "source": f"plugin:{plugin_dir.name}",
                    "is_symlink": False,
                    "path": str(skills_path),
                })
    return skills


def collect_commands() -> list:
    d = CLAUDE_DIR / "commands"
    if not d.exists():
        return []
    cmds = []
    for md in sorted(d.glob("*.md")):
        cmds.append({"name": md.stem, "slash": f"/{md.stem}", "description": _first_desc(md), "path": str(md)})
    ap = d / "agent_prompts"
    if ap.exists():
        for md in sorted(ap.glob("*.md")):
            cmds.append({"name": md.stem, "slash": f"/agent_prompts/{md.stem}", "description": _first_desc(md), "path": str(md)})
    return cmds


def collect_hooks(settings: dict) -> list:
    hooks = []
    for trigger, entries in settings.get("hooks", {}).items():
        for entry in entries:
            matcher = entry.get("matcher", "")
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                short_cmd = cmd if len(cmd) < 80 else cmd[:77] + "..."
                script_path = ""
                for token in cmd.split():
                    p = Path(token.replace("~", str(Path.home())))
                    if p.exists() and p.is_file():
                        script_path = str(p)
                        break
                hooks.append({
                    "trigger": trigger,
                    "matcher": matcher or "(all tools)",
                    "command": short_cmd,
                    "path": script_path,
                })
    return hooks


def collect_mcp_servers_raw(settings: dict) -> list:
    servers = []
    seen: set = set()

    def add(mcp: dict, source: str):
        for name, cfg in mcp.items():
            if name in seen:
                continue
            seen.add(name)
            servers.append({
                "name": name,
                "command": cfg.get("command", ""),
                "args": cfg.get("args", []),
                "source": source,
            })

    add(settings.get("mcpServers", {}), "settings.json")
    cj = Path.home() / ".claude.json"
    if cj.exists():
        try:
            add(json.loads(cj.read_text()).get("mcpServers", {}), "~/.claude.json")
        except Exception:
            pass
    return servers


def collect_rules() -> list:
    rules_dir = CLAUDE_DIR / "rules"
    if not rules_dir.exists():
        return []
    rules = []
    for cat in sorted(rules_dir.iterdir()):
        if not cat.is_dir() or cat.name.startswith("."): continue
        files = [{"name": f.name, "path": str(f)} for f in sorted(cat.glob("*.md"))]
        rules.append({"category": cat.name, "files": files})
    return rules


# ─── Usage Enrichment ─────────────────────────────────────────────────────────

def enrich_plugins(plugins: list, usage: dict) -> list:
    skill_stats = usage.get("skills", {})
    result = []
    for p in plugins:
        prefix = p["name"] + ":"
        count = sum(v["count"] for k, v in skill_stats.items() if k.startswith(prefix) or k == p["name"])
        last = max((v["last_used"] for k, v in skill_stats.items()
                    if (k.startswith(prefix) or k == p["name"]) and v["last_used"]), default="")
        result.append({**p, "usage_count": count, "last_used": last})
    return result


def enrich_agents(agents: list, usage: dict) -> list:
    agent_stats = usage.get("agents", {})
    result = []
    for a in agents:
        stat = agent_stats.get(a["slug"], {"count": 0, "last_used": ""})
        result.append({**a, "usage_count": stat["count"], "last_used": stat["last_used"]})
    return result


def enrich_skills(skills: list, usage: dict) -> list:
    skill_stats = usage.get("skills", {})
    result = []
    for s in skills:
        slug = s["slug"]
        plugin_namespace = s.get("plugin_namespace", "")
        child_usage = []
        if plugin_namespace:
            prefix = plugin_namespace + ":"
            count = sum(v["count"] for k, v in skill_stats.items() if k.startswith(prefix))
            last = max((v["last_used"] for k, v in skill_stats.items()
                        if k.startswith(prefix) and v["last_used"]), default="")
            child_usage = [
                {
                    "name": k.removeprefix(prefix),
                    "count": v["count"],
                    "last_used": v["last_used"],
                }
                for k, v in skill_stats.items()
                if k.startswith(prefix)
            ]
            child_usage.sort(key=lambda item: item["name"])
            child_usage.sort(key=lambda item: item["last_used"], reverse=True)
            child_usage.sort(key=lambda item: item["count"], reverse=True)
        else:
            stat = skill_stats.get(slug, {"count": 0, "last_used": ""})
            count, last = stat["count"], stat["last_used"]
        result.append({**s, "usage_count": count, "last_used": last, "child_usage": child_usage})
    return result


def enrich_mcp(servers: list, usage: dict) -> list:
    mcp_stats = usage.get("mcp", {})
    result = []
    for s in servers:
        stat = mcp_stats.get(s["name"], {"count": 0, "last_used": ""})
        result.append({**s, "usage_count": stat["count"], "last_used": stat["last_used"]})
    return result


def enrich_data(raw: dict, usage: dict) -> dict:
    return {
        "plugins":     enrich_plugins(raw["plugins"], usage),
        "agents":      enrich_agents(raw["agents"], usage),
        "skills":      enrich_skills(raw["skills"], usage),
        "commands":    raw["commands"],
        "hooks":       raw["hooks"],
        "mcp_servers": enrich_mcp(raw["mcp_servers"], usage),
        "rules":       raw["rules"],
    }


def build_project_only_data(home_raw: dict, project_raw: dict) -> dict:
    home_skill_slugs = {item["slug"] for item in home_raw["skills"]}
    home_command_slashes = {item["slash"] for item in home_raw["commands"]}
    home_mcp_names = {item["name"] for item in home_raw["mcp_servers"]}
    home_hooks = {(item["trigger"], item["matcher"], item["command"]) for item in home_raw["hooks"]}
    home_rule_names = {file["name"] for rule in home_raw["rules"] for file in rule.get("files", [])}

    project_only_rules = []
    for rule in project_raw["rules"]:
        files = [file for file in rule.get("files", []) if file["name"] not in home_rule_names]
        if files:
            project_only_rules.append({**rule, "files": files})

    return {
        "plugins": [],
        "agents": [],
        "skills": [item for item in project_raw["skills"] if item["slug"] not in home_skill_slugs],
        "commands": [item for item in project_raw["commands"] if item["slash"] not in home_command_slashes],
        "hooks": [
            item for item in project_raw["hooks"]
            if (item["trigger"], item["matcher"], item["command"]) not in home_hooks
        ],
        "mcp_servers": [item for item in project_raw["mcp_servers"] if item["name"] not in home_mcp_names],
        "rules": project_only_rules,
    }


# ─── HTML Helpers ─────────────────────────────────────────────────────────────

def _e(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def _open_link(label: str, path: str, cls: str = "") -> str:
    if not path:
        return f'<span class="{cls}">{label}</span>'
    enc = urllib.parse.quote(path, safe="")
    return (
        f'<a onclick="openFile(\'{enc}\')" class="{cls} hover:underline cursor-pointer"'
        f' title="{_e(path)}">{label}</a>'
    )

def _sort_bar(grid_id: str, default: str = "name") -> str:
    buttons = [("name", "Name"), ("count", "Usage Count"), ("last", "Last Used")]
    btns = "".join(
        f'<button class="sort-btn {"active" if k == default else ""}" '
        f'onclick="sortGrid(\'{grid_id}\',\'{k}\',this)">{label}</button>'
        for k, label in buttons
    )
    return f'<div class="sort-bar">{btns}</div>'

def _tab_btns(selected_dir: str) -> str:
    if selected_dir == "project-only":
        tabs = [
            ("mcp", "Project MCP"), ("skills", "Project Skills"),
            ("commands", "Project Commands"), ("hooks", "Project Hooks"),
            ("rules", "Project Rules")
        ]
    else:
        tabs = [("plugins", "Plugins"), ("agents", "Agents"), ("skills", "Skills"),
                ("commands", "Commands"), ("hooks", "Hooks"), ("mcp", "MCP Servers"),
                ("rules", "Rules"), ("cleanup", "Cleanup")]
    return "".join(
        f'<button class="tab-btn" onclick="showTab(\'{t}\')" id="btn-{t}">{label}</button>'
        for t, label in tabs
    )

def _stats_header(items: list) -> str:
    parts = []
    for n, label, never in items:
        unused_line = (f'<div class="nav-stat-w">{never} unused</div>'
                       if never else '<div class="nav-stat-w" style="visibility:hidden">·</div>')
        parts.append(
            f'<div class="nav-stat">'
            f'<div class="nav-stat-n">{n}</div>'
            f'<div class="nav-stat-l">{label}</div>'
            f'{unused_line}</div>'
        )
    return "".join(parts)

def _dir_selector(selected_dir: str) -> str:
    """Toggle between ~/.claude and the project-only comparison view."""
    if CWD_CLAUDE is None:
        return ""
    options = [
        f'<option value="home"{"  selected" if selected_dir == "home" else ""}>{_e("~/.claude")}</option>',
        f'<option value="project-only"{"  selected" if selected_dir == "project-only" else ""}>{_e("Project-only config")}</option>',
    ]
    return (
        f'<select class="dir-select" onchange="window.location=\'/?dir=\'+this.value">'
        + "".join(options) + "</select>"
    )


# ─── Renderers ────────────────────────────────────────────────────────────────

def render_plugins(plugins: list) -> str:
    cards = []
    for p in plugins:
        name = _e(p["label"])
        ver = _e(p.get("version", ""))
        desc = _e(p.get("description", ""))
        repo = p.get("repo_url", "")
        mkt = _e(p.get("marketplace", ""))
        inst = _e(p.get("installed_at", ""))
        enabled = p.get("enabled", True)
        rp = p.get("readme_path", "")
        usage_badge = _usage_html({"count": p.get("usage_count", 0), "last_used": p.get("last_used", "")})

        ver_b = f'<span class="badge badge-blue">{ver}</span>' if ver else ""
        ena_b = ('<span class="badge badge-green">enabled</span>' if enabled
                 else '<span class="badge badge-red">disabled</span>')
        repo_a = (f'<a href="{_e(repo)}" target="_blank" class="al" style="font-size:12px">'
                  f'{_e(repo.replace("https://github.com/", ""))}</a>') if repo else ""
        title = (_open_link(f'<span style="font-weight:600;color:var(--text-p)">{name}</span>', rp)
                 if rp else f'<span style="font-weight:600;color:var(--text-p)">{name}</span>')
        cards.append(f"""<div class="card">
  <div class="flex items-start justify-between mb-2">{title}<div class="flex gap-1 ml-2 flex-shrink-0">{ena_b}{ver_b}</div></div>
  {f'<p style="font-size:12px;color:var(--text-s);margin-bottom:8px">{desc}</p>' if desc else ''}
  <div class="flex items-center gap-2 flex-wrap">{repo_a}<span style="font-size:11px;color:var(--text-t)">@{mkt}</span></div>
  {f'<p style="font-size:11px;color:var(--text-t);margin-top:4px">installed: {inst}</p>' if inst else ''}
  {f'<div class="mt-2">{usage_badge}</div>' if usage_badge else ''}
</div>""")
    return "".join(cards)


def render_agents(agents: list) -> str:
    cats: dict = {}
    for a in agents:
        cats.setdefault(a["category"], []).append(a)
    parts = []
    for cat, items in sorted(cats.items()):
        rows = "".join(
            f'<tr data-name="{_e(a["name"].lower())}" '
            f'data-count="{a.get("usage_count", 0)}" data-last="{_e(a.get("last_used", ""))}">'
            f'<td class="whitespace-nowrap">'
            f'{_open_link(_e(a["name"]), a["path"], "al")}</td>'
            f'<td style="color:var(--text-s)">{_e(a["description"][:80])}</td>'
            f'<td class="whitespace-nowrap">'
            f'{_usage_html({"count": a.get("usage_count", 0), "last_used": a.get("last_used", "")})}</td>'
            f'</tr>'
            for a in items
        )
        table_id = "agent-table-" + cat.replace(" ", "-").replace("&", "")
        parts.append(f"""<details class="mb-4" open>
  <summary class="flex items-center gap-2 py-2">
    <span style="font-weight:600;color:var(--text-p)">{_e(cat)}</span>
    <span class="badge badge-blue">{len(items)}</span>
  </summary>
  <div style="border-radius:8px;overflow:hidden;margin-top:8px">
    <table class="at" id="{table_id}">
      <thead><tr>
        <th>Agent</th><th>Description</th>
        <th style="cursor:pointer" onclick="sortTable('{table_id}')">Usage ↕</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</details>""")
    return "".join(parts)


def render_skills(skills: list, show_usage: bool = True) -> str:
    never_count = sum(1 for s in skills if not s.get("last_used", ""))
    summary = f'<span class="badge badge-red">{never_count} never used</span>' if show_usage and never_count else ""
    sort_bar = _sort_bar("skills-grid", "name") if show_usage else ""
    cards = []
    for s in skills:
        name = _e(s["name"])
        desc = _e(s.get("description", ""))
        src = s.get("source", "custom")
        path = s.get("path", "")
        is_sym = s.get("is_symlink", False)
        last_iso = _e(s.get("last_used", ""))
        count = s.get("usage_count", 0)
        child_usage = s.get("child_usage", []) if show_usage else []
        child_usage_json = _e(json.dumps(child_usage, separators=(",", ":")))
        clickable = bool(child_usage)

        if src == "custom":
            badge = '<span class="badge source-custom">custom</span>'
        elif "plugin" in src:
            badge = f'<span class="badge source-plugin">{_e(src.replace("plugin:", ""))}</span>'
        else:
            badge = f'<span class="badge source-plugin">{_e(src)}</span>'
        if is_sym:
            badge += ' <span class="badge source-symlink">symlink</span>'

        usage_badge = _usage_html({"count": count, "last_used": s.get("last_used", "")}) if show_usage else ""
        title = (_open_link(f'<span style="font-weight:500;font-size:14px" class="al">{name}</span>', path)
                 if path else f'<span style="font-weight:500;font-size:14px;color:var(--text-p)">{name}</span>')
        desc_html = f'<p style="font-size:12px;color:var(--text-s)">{desc}</p>' if desc else ""
        usage_html = f'<div style="margin-top:8px">{usage_badge}</div>' if usage_badge else ""
        click_badge = '<div style="margin-top:8px;font-size:11px;color:var(--brand)">Click to view child skill usage</div>' if clickable else ""
        card_class = 'card skill-item skill-item-clickable' if clickable else 'card skill-item'
        cards.append(
            f'<div class="{card_class}" data-name="{_e(s["name"].lower())}" '
            f'data-count="{count}" data-last="{last_iso}" '
            f'data-skill-name="{name}" data-child-usage="{child_usage_json}">'
            f'<div class="flex items-start justify-between mb-1">{title}'
            f'<div class="flex gap-1 ml-2">{badge}</div></div>'
            f'{desc_html}{usage_html}{click_badge}</div>'
        )
    header = f'<div class="flex items-center justify-between mb-3">{sort_bar}{summary}</div>' if (sort_bar or summary) else ""
    return f'{header}<div id="skills-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">{"".join(cards)}</div>'


def render_commands(commands: list) -> str:
    if not commands:
        return '<tr><td colspan="2" style="color:var(--text-t);text-align:center;padding:32px">No project-only commands found.</td></tr>'
    rows = []
    for c in commands:
        slash = _e(c["slash"])
        desc = _e(c.get("description", ""))
        link = _open_link(f'<span style="font-family:monospace;color:var(--brand)">{slash}</span>', c["path"])
        rows.append(f'<tr><td class="whitespace-nowrap">{link}</td>'
                    f'<td style="color:var(--text-s)">{desc}</td></tr>')
    return "".join(rows)


def render_hooks(hooks: list) -> str:
    colors = {
        "PreToolUse":       "badge-amber",
        "PostToolUse":      "badge-blue",
        "Stop":             "badge-red",
        "SubagentStop":     "badge-red",
        "UserPromptSubmit": "badge-green",
        "PreCompact":       "badge-gray",
        "SessionStart":     "badge-green",
    }
    parts = []
    for h in hooks:
        color = colors.get(h["trigger"], "badge-gray")
        cmd_display = _e(h["command"])
        cmd_html = (_open_link(f'<code style="font-size:12px;color:var(--text-s);word-break:break-all">{cmd_display}</code>', h["path"])
                    if h["path"] else f'<code style="font-size:12px;color:var(--text-s);word-break:break-all">{cmd_display}</code>')
        parts.append(f"""<div class="card flex items-start gap-4">
  <span class="badge {color}" style="white-space:nowrap;margin-top:2px">{_e(h['trigger'])}</span>
  <div style="flex:1;min-width:0">{cmd_html}
    {f'<p style="font-size:11px;color:var(--text-t);margin-top:4px">matcher: {_e(h["matcher"])}</p>' if h.get("matcher") else ''}
  </div>
</div>""")
    return "".join(parts)


def render_mcp(servers: list, show_usage: bool = True, empty_message: str = "No MCP servers configured") -> str:
    if not servers:
        return f'<div style="color:var(--text-t);font-size:14px;padding:32px;text-align:center">{_e(empty_message)}</div>'
    never_count = sum(1 for s in servers if not s.get("last_used", ""))
    summary = f'<span class="badge badge-red">{never_count} never used</span>' if show_usage and never_count else ""
    sort_bar = _sort_bar("mcp-grid") if show_usage else ""
    header = f'<div class="flex items-center justify-between mb-3">{sort_bar}{summary}</div>' if (sort_bar or summary) else ""
    cards = []
    for s in servers:
        args = " ".join(_e(str(a)) for a in s.get("args", [])[:4])
        if len(s.get("args", [])) > 4:
            args += " ..."
        src = _e(s.get("source", ""))
        last_iso = _e(s.get("last_used", ""))
        count = s.get("usage_count", 0)
        usage_badge = _usage_html({"count": count, "last_used": s.get("last_used", "")}) if show_usage else ""
        src_badge = f'<span class="badge badge-gray">{src}</span>' if src else ""
        cards.append(
            f'<div class="card" data-name="{_e(s["name"].lower())}" data-count="{count}" data-last="{last_iso}">'
            f'<div class="flex items-center justify-between mb-1">'
            f'<h3 style="font-weight:600;color:var(--text-p)">{_e(s["name"])}</h3>{src_badge}</div>'
            f'<code style="font-size:12px;color:var(--text-s);word-break:break-all">{_e(s.get("command", ""))} {args}</code>'
            f'{f"<div class=mt-2>{usage_badge}</div>" if usage_badge else ""}'
            f'</div>'
        )
    return f'{header}<div id="mcp-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">{"".join(cards)}</div>'


def render_rules(rules: list) -> str:
    cards = []
    for r in rules:
        files_html = "".join(
            f'<li style="font-size:13px;padding:2px 0">{_open_link(_e(f["name"]), f["path"], "al")}</li>'
            for f in r["files"]
        )
        cards.append(f"""<div class="card">
  <h3 style="font-weight:600;color:var(--text-p);margin-bottom:8px">{_e(r["category"])}/</h3>
  <ul class="list-disc list-inside" style="line-height:1.8">{files_html}</ul>
</div>""")
    return "".join(cards)


def render_cleanup(agents: list, skills: list, mcp_servers: list) -> str:
    STALE_DAYS = 30

    def is_stale(item: dict) -> bool:
        if not item.get("last_used"):
            return True
        days, _, _ = _stale_info(item["last_used"])
        return days is not None and days > STALE_DAYS

    stale_agents = [a for a in agents if is_stale(a)]
    stale_skills = [s for s in skills if is_stale(s)]
    stale_mcp = [m for m in mcp_servers if is_stale(m)]
    total = len(stale_agents) + len(stale_skills) + len(stale_mcp)

    if total == 0:
        return '<div style="text-align:center;padding:48px;color:var(--text-t);font-size:14px">Everything looks active — no stale items found.</div>'

    def section(title: str, items: list, type_label: str) -> str:
        if not items:
            return ""
        rows = []
        for item in sorted(items, key=lambda x: x.get("last_used", "")):
            name = item.get("name", item.get("label", ""))
            path = item.get("path", item.get("readme_path", ""))
            usage_badge = _usage_html({"count": item.get("usage_count", 0), "last_used": item.get("last_used", "")})
            link = (_open_link(f'<span style="font-weight:500" class="al">{_e(name)}</span>', path)
                    if path else f'<span style="font-weight:500;color:var(--text-p)">{_e(name)}</span>')
            rows.append(
                f'<tr>'
                f'<td>{link}</td>'
                f'<td><span class="badge badge-gray">{type_label}</span></td>'
                f'<td>{usage_badge}</td>'
                f'</tr>'
            )
        return f"""<div style="margin-bottom:24px">
  <h3 style="font-weight:600;color:var(--text-p);margin-bottom:8px">{_e(title)} <span class="badge badge-red">{len(items)}</span></h3>
  <div style="border-radius:8px;overflow:hidden">
    <table class="at">
      <thead><tr><th>Name</th><th>Type</th><th>Status</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
</div>"""

    summary = f"""<div style="background:rgba(201,100,66,.07);border:1px solid rgba(201,100,66,.18);border-radius:8px;padding:14px 18px;margin-bottom:20px;display:flex;align-items:center;gap:14px">
  <div>
    <p style="font-weight:500;color:#c96442;font-size:14px">{total} items haven&#39;t been used in the last {STALE_DAYS} days</p>
    <p style="font-size:12px;color:#87867f;margin-top:2px">Review these to keep your .claude lean</p>
  </div>
</div>"""

    return summary + section("Agents", stale_agents, "agent") + section("Skills", stale_skills, "skill") + section("MCP Servers", stale_mcp, "mcp")


# ─── Build HTML ───────────────────────────────────────────────────────────────

def build_html(data: dict, claude_dir: Path, selected_dir: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    p, ag, sk, co, ho, mc, ru = (data["plugins"], data["agents"], data["skills"],
        data["commands"], data["hooks"], data["mcp_servers"], data["rules"])
    n_cats = len({a["category"] for a in ag}) if ag else 0
    agents_never = sum(1 for a in ag if not a.get("last_used", ""))
    skills_never = sum(1 for s in sk if not s.get("last_used", ""))
    mcp_never    = sum(1 for m in mc if not m.get("last_used", ""))
    is_project_only = selected_dir == "project-only"

    if is_project_only:
        dir_label = "Project-only config"
        commands_note = "Only commands found in this project-local .claude directory"
        mcp_html = render_mcp(mc, show_usage=False, empty_message="No project-only MCP servers found.")
        skills_html = render_skills(sk, show_usage=False)
        commands_html = render_commands(co)
        hooks_html = render_hooks(ho) if ho else '<div style="color:var(--text-t);font-size:14px;padding:32px;text-align:center">No project-only hooks found.</div>'
        rules_html = (
            '<p style="font-size:12px;color:var(--text-t);margin-bottom:12px">Click filename to open in default app</p>'
            + f'<div class="grid grid-cols-1 md:grid-cols-2 gap-4">{render_rules(ru)}</div>'
            if ru else '<div style="color:var(--text-t);font-size:14px;padding:32px;text-align:center">No project-only rules found.</div>'
        )
        project_empty = not mc and not sk and not co and not ho and not ru
        project_intro = (
            '<div style="background:rgba(201,100,66,.07);border:1px solid rgba(201,100,66,.18);border-radius:8px;padding:14px 18px;margin-bottom:20px">'
            '<p style="font-weight:500;color:var(--text-p);font-size:14px">Only in this project</p>'
            '<p style="font-size:12px;color:var(--text-s);margin-top:4px">This view compares the current project\'s <code>.claude</code> with <code>~/.claude</code> and shows only project-specific MCP servers, skills, commands, hooks, and rules.</p>'
            '</div>'
        )
        if project_empty:
            project_intro += '<div style="color:var(--text-t);font-size:14px;padding:32px 0;text-align:center">No project-only MCP servers, skills, commands, hooks, or rules found.</div>'
    else:
        dir_label = str(claude_dir).replace(str(Path.home()), "~")
        commands_note = "Click command to open in default app"
        mcp_html = render_mcp(mc)
        skills_html = render_skills(sk)
        commands_html = render_commands(co)
        hooks_html = render_hooks(ho)
        rules_html = '<p style="font-size:12px;color:var(--text-t);margin-bottom:12px">Click filename to open in default app</p>' + f'<div class="grid grid-cols-1 md:grid-cols-2 gap-4">{render_rules(ru)}</div>'
        project_intro = ""

    dir_sel = _dir_selector(selected_dir)
    nav_stats = (
        _stats_header([
            (len(sk), "Skills", 0), (len(co), "Commands", 0), (len(mc), "MCP", 0),
            (len(ho), "Hooks", 0), (sum(len(rule.get("files", [])) for rule in ru), "Rules", 0),
        ])
        if is_project_only else
        _stats_header([
            (len(p), "Plugins", 0), (len(ag), "Agents", agents_never),
            (len(sk), "Skills", skills_never), (len(co), "Commands", 0),
            (len(ho), "Hooks", 0), (len(mc), "MCP", mcp_never),
        ])
    )
    pre_tabs_html = ""
    post_tabs_html = ""
    if not is_project_only:
        pre_tabs_html = f'''<div id="tab-plugins" class="tab-content">
  <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_plugins(p)}</div>
</div>

<div id="tab-agents" class="tab-content">
  <p style="font-size:12px;color:var(--text-t);margin-bottom:12px">{len(ag)} agents · {n_cats} categories · Click name to open
  {' · <span style="color:#b53333;font-weight:500">' + str(agents_never) + ' never used</span>' if agents_never else ''}
  </p>
  {render_agents(ag)}
</div>'''
        post_tabs_html = f'''<div id="tab-hooks" class="tab-content">
  <p style="font-size:12px;color:var(--text-t);margin-bottom:12px">Click command to open script file</p>
  <div class="space-y-3">{hooks_html}</div>
</div>

<div id="tab-rules" class="tab-content">
  {rules_html}
</div>

<div id="tab-cleanup" class="tab-content">
  {render_cleanup(ag, sk, mc)}
</div>'''
    else:
        post_tabs_html = f'''<div id="tab-hooks" class="tab-content">
  <p style="font-size:12px;color:var(--text-t);margin-bottom:12px">Only hooks found in this project-local .claude directory</p>
  <div class="space-y-3">{hooks_html}</div>
</div>

<div id="tab-rules" class="tab-content">
  {rules_html}
</div>'''

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Config Dashboard · {_e(dir_label)}</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  :root {{
    --brand: #c96442; --brand-coral: #d97757;
    --bg: #f5f4ed; --surface: #faf9f5; --surface-white: #ffffff;
    --text-p: #141413; --text-s: #5e5d59; --text-t: #87867f;
    --border: #f0eee6; --border-warm: #e8e6dc;
    --dark-bg: #141413; --dark-surface: #30302e;
    --warm-sand: #e8e6dc;
    --shadow: rgba(0,0,0,.05) 0px 4px 24px;
    --shadow-ring: #e8e6dc 0px 0px 0px 0px, #d1cfc5 0px 0px 0px 1px;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: system-ui, -apple-system, Arial, sans-serif;
    font-size: 15px; line-height: 1.60;
    background: var(--bg); color: var(--text-p); margin: 0;
  }}
  .nav {{
    position: sticky; top: 0; z-index: 50; height: 72px;
    background: rgba(250, 249, 245, 0.80);
    backdrop-filter: saturate(160%) blur(18px);
    -webkit-backdrop-filter: saturate(160%) blur(18px);
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; padding: 0 24px; gap: 20px;
  }}
  .nav-brand {{ flex: 0 0 auto; }}
  .nav-title {{
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 16px; font-weight: 500; color: var(--text-p); letter-spacing: normal; line-height: 1.20;
  }}
  .nav-sub {{ font-size: 11px; color: var(--text-t); margin-top: 2px; }}
  .nav-stat {{ text-align: center; }}
  .nav-stat-n {{ font-size: 18px; font-weight: 500; color: var(--text-p); line-height: 1.1; font-family: Georgia, serif; }}
  .nav-stat-l {{ font-size: 10px; color: var(--text-t); font-weight: 400; text-transform: uppercase; letter-spacing: .05em; }}
  .nav-stat-w {{ font-size: 9px; color: #b53333; }}
  .dir-select {{
    font-size: 12px; background: var(--surface); color: var(--text-s);
    border: 1px solid var(--border-warm); border-radius: 8px; padding: 3px 8px; outline: none;
  }}
  .dir-select option {{ background: var(--surface); color: var(--text-p); }}
  .stop-btn {{
    font-size: 12px; padding: 5px 14px; border-radius: 8px;
    background: linear-gradient(180deg, #dc2626 0%, #b91c1c 100%); cursor: pointer; transition: all .15s; white-space: nowrap;
    font-weight: 600; color: #ffffff;
    box-shadow: 0 8px 18px rgba(185, 28, 28, 0.22);
  }}
  .stop-btn:hover {{ background: linear-gradient(180deg, #ef4444 0%, #dc2626 100%); color: #ffffff; box-shadow: 0 10px 22px rgba(220, 38, 38, 0.28); }}
  .stop-btn:active {{ transform: translateY(1px); box-shadow: 0 6px 14px rgba(185, 28, 28, 0.2); }}
  .tab-bar {{
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 8px 24px; display: flex; gap: 4px; flex-wrap: wrap;
  }}
  .tab-btn {{
    font-size: 13px; font-weight: 400; color: var(--text-s);
    border-radius: 8px; padding: 5px 14px; border: none; background: transparent; cursor: pointer; transition: all .15s;
  }}
  .tab-btn:hover:not(.active) {{ background: var(--border-warm); color: var(--text-p); }}
  .tab-btn.active {{ background: var(--brand); color: #faf9f5; }}
  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}
  .content {{ padding: 24px; max-width: 1040px; margin: 0 auto; }}
  .card {{
    background: var(--surface); border-radius: 8px; padding: 16px;
    box-shadow: var(--shadow); border: 1px solid var(--border); transition: box-shadow .2s;
  }}
  .card:hover {{ box-shadow: var(--shadow-ring); }}
  .skill-item-clickable {{ cursor: pointer; }}
  .skill-item-clickable:hover {{ border-color: rgba(201,100,66,.35); }}
  .modal-backdrop {{
    position: fixed; inset: 0; background: rgba(20,20,19,.45); z-index: 80;
    display: none; align-items: center; justify-content: center; padding: 24px;
  }}
  .modal-backdrop.open {{ display: flex; }}
  .modal {{
    width: min(720px, 100%); max-height: 80vh; overflow: auto;
    background: var(--surface-white); border: 1px solid var(--border-warm); border-radius: 12px;
    box-shadow: 0 24px 80px rgba(0,0,0,.18);
  }}
  .modal-head {{
    display:flex; align-items:center; justify-content:space-between; gap:12px;
    padding: 18px 20px; border-bottom: 1px solid var(--border);
  }}
  .modal-body {{ padding: 18px 20px 20px; }}
  .modal-close {{
    border: 1px solid var(--border-warm); background: var(--surface); color: var(--text-s);
    border-radius: 8px; padding: 6px 10px; font-size: 12px; cursor: pointer;
  }}
  .modal-empty {{ color: var(--text-t); font-size: 13px; text-align: center; padding: 24px 8px; }}
  .badge {{
    display: inline-block; padding: 1px 8px; border-radius: 980px;
    font-size: 11px; font-weight: 500; letter-spacing: .01em;
  }}
  .al {{ color: var(--brand); text-decoration: none; cursor: pointer; }}
  .al:hover {{ text-decoration: underline; color: var(--brand-coral); }}
  .badge-blue {{ background: rgba(201,100,66,.10); color: var(--brand); }}
  .badge-green {{ background: rgba(0,0,0,.05); color: #3d6b4e; border: 1px solid rgba(0,0,0,.06); }}
  .badge-gray {{ background: rgba(0,0,0,.04); color: var(--text-s); border: 1px solid var(--border-warm); }}
  .badge-red {{ background: rgba(181,51,51,.08); color: #b53333; }}
  .badge-amber {{ background: rgba(146,64,14,.08); color: #92400e; }}
  .source-custom {{ background: rgba(0,0,0,.05); color: #3d6b4e; border: 1px solid rgba(0,0,0,.06); }}
  .source-plugin {{ background: rgba(201,100,66,.10); color: var(--brand); }}
  .source-symlink {{ background: rgba(146,64,14,.08); color: #92400e; }}
  .stale-recent {{ background: rgba(0,0,0,.04); color: #3d6b4e; border: 1px solid rgba(0,0,0,.06); }}
  .stale-mid    {{ background: rgba(146,64,14,.08); color: #92400e; }}
  .stale-old    {{ background: rgba(181,51,51,.08); color: #b53333; }}
  .stale-never  {{ background: rgba(181,51,51,.08); color: #b53333; font-weight: 600; }}
  .usage-count  {{ background: rgba(201,100,66,.10); color: var(--brand); }}
  .sort-bar {{ display: flex; align-items: center; gap: 4px; }}
  .sort-btn {{
    padding: 2px 12px; border-radius: 8px; font-size: 12px; font-weight: 400;
    border: 1px solid var(--border-warm); background: var(--surface); color: var(--text-s);
    cursor: pointer; transition: all .15s;
    box-shadow: var(--warm-sand) 0px 0px 0px 0px, #d1cfc5 0px 0px 0px 1px;
  }}
  .sort-btn:hover {{ color: var(--brand); border-color: var(--brand); }}
  .sort-btn.active {{ background: var(--dark-bg); color: #faf9f5; border-color: var(--dark-bg); }}
  .at {{ border-collapse: collapse; width: 100%; background: var(--surface); border-radius: 8px; overflow: hidden; box-shadow: var(--shadow); border: 1px solid var(--border); }}
  .at th {{
    font-size: 10px; font-weight: 500; color: var(--text-t); text-transform: uppercase;
    letter-spacing: .07em; padding: 10px 16px; text-align: left;
    background: rgba(0,0,0,.01); border-bottom: 1px solid var(--border);
  }}
  .at td {{ padding: 10px 16px; border-bottom: 1px solid var(--border); font-size: 13px; vertical-align: middle; color: var(--text-p); }}
  .at tbody tr:last-child td {{ border-bottom: none; }}
  .at tbody tr:hover {{ background: var(--bg); }}
  details summary {{ cursor: pointer; list-style: none; }}
  details summary::before {{ content: "▶"; margin-right: 6px; font-size: 9px; color: var(--text-t); transition: transform .2s; display: inline-block; }}
  details[open] summary::before {{ transform: rotate(90deg); }}
  a {{ cursor: pointer; }}

  /* ── Floating character ── */
  .float-layer {{
    position: fixed; inset: 0;
    pointer-events: none; z-index: 0; overflow: hidden;
  }}
  .fc {{
    position: absolute;
    image-rendering: pixelated;
    opacity: 0;
    mix-blend-mode: multiply;
    animation: fcFloat linear infinite;
    will-change: transform, opacity;
  }}
  @keyframes fcFloat {{
    0%   {{ opacity: 0; transform: translateY(0)   rotate(-3deg); }}
    8%   {{ opacity: 1; }}
    45%  {{ transform: translateY(-22px) rotate(2deg); }}
    55%  {{ transform: translateY(-18px) rotate(-1deg); }}
    92%  {{ opacity: 1; }}
    100% {{ opacity: 0; transform: translateY(4px)  rotate(-3deg); }}
  }}
</style>
</head>
<body>

<div class="float-layer" id="float-layer"></div>

<nav class="nav">
  <div class="nav-brand">
    <div class="nav-title">Claude Config Dashboard</div>
    <div class="nav-sub">{_e(dir_label)} &middot; {now}</div>
  </div>
  <div style="flex:1"></div>
  <div style="display:flex;gap:20px;align-items:center">
    {nav_stats}
    {f'<div style="display:flex;flex-direction:column;gap:2px;align-items:flex-end"><span style="font-size:10px;color:rgba(255,255,255,.38);text-transform:uppercase;letter-spacing:.06em">Config dir</span>{dir_sel}</div>' if dir_sel else ""}
    <button class="stop-btn" onclick="fetch('/stop').then(()=>window.close())">Stop server</button>
  </div>
</nav>

<div class="tab-bar">{_tab_btns(selected_dir)}</div>

<div class="content">
{project_intro}
{pre_tabs_html}

<div id="tab-skills" class="tab-content">
  {skills_html}
</div>

<div id="tab-commands" class="tab-content">
  <p style="font-size:12px;color:var(--text-t);margin-bottom:12px">{commands_note}</p>
  <div style="border-radius:8px;overflow:hidden">
    <table class="at">
      <thead><tr><th>Command</th><th>Description</th></tr></thead>
      <tbody>{commands_html}</tbody>
    </table>
  </div>
</div>

<div id="tab-mcp" class="tab-content">
  {mcp_html}
</div>

{post_tabs_html}

</div>

<div id="skill-usage-modal" class="modal-backdrop" aria-hidden="true">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="skill-usage-modal-title">
    <div class="modal-head">
      <div>
        <div style="font-size:11px;color:var(--text-t);text-transform:uppercase;letter-spacing:.06em">Plugin skill usage</div>
        <h2 id="skill-usage-modal-title" style="margin:0;font-size:18px;font-weight:600;color:var(--text-p)"></h2>
      </div>
      <button type="button" class="modal-close" id="skill-usage-modal-close">Close</button>
    </div>
    <div class="modal-body" id="skill-usage-modal-body"></div>
  </div>
</div>

<script>
// ── Floating characters ──────────────────────────────────────────
(function() {{
  const layer = document.getElementById('float-layer');
  if (!layer) return;
  const HAS_CHAR = {str(_CHARACTER_IMG.exists()).lower()};
  if (!HAS_CHAR) return;

  const spots = [
    {{l:4,  t:18, s:52, dur:13, delay:0}},
    {{l:80, t:30, s:44, dur:10, delay:3}},
    {{l:18, t:62, s:48, dur:15, delay:7}},
    {{l:65, t:72, s:40, dur:11, delay:2}},
    {{l:88, t:55, s:56, dur:14, delay:9}},
    {{l:42, t:88, s:36, dur:12, delay:5}},
    {{l:55, t:10, s:42, dur:16, delay:1}},
  ];

  spots.forEach(function(sp) {{
    const img = document.createElement('img');
    img.src = '/character?v=' + Date.now();
    img.className = 'fc';
    img.style.left = sp.l + '%';
    img.style.top  = sp.t + '%';
    img.style.width = sp.s + 'px';
    img.style.animationDuration = sp.dur + 's';
    img.style.animationDelay = sp.delay + 's';
    layer.appendChild(img);
  }});
}})();

function showTab(name) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.getElementById('btn-' + name).classList.add('active');
  localStorage.setItem('claude-dash-tab', name);
}}
showTab(localStorage.getItem('claude-dash-tab') || (document.getElementById('tab-plugins') ? 'plugins' : 'mcp'));

function openFile(encodedPath) {{
  fetch('/open?path=' + encodedPath).catch(() => {{}});
}}

function sortGrid(gridId, key, btn) {{
  const grid = document.getElementById(gridId);
  if (!grid) return;
  const items = Array.from(grid.children);
  items.sort((a, b) => {{
    if (key === 'name') return a.dataset.name.localeCompare(b.dataset.name);
    if (key === 'count') return parseInt(b.dataset.count || 0) - parseInt(a.dataset.count || 0);
    if (key === 'last') {{
      const la = a.dataset.last || '', lb = b.dataset.last || '';
      if (!la && !lb) return 0;
      if (!la) return -1;
      if (!lb) return 1;
      return la.localeCompare(lb);
    }}
    return 0;
  }});
  items.forEach(item => grid.appendChild(item));
  const bar = btn.closest('.sort-bar');
  if (bar) bar.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}}

function sortTable(tableId) {{
  const table = document.getElementById(tableId);
  if (!table) return;
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const asc = table.dataset.sortAsc === '1';
  rows.sort((a, b) => {{
    const ca = parseInt(a.dataset.count || 0), cb = parseInt(b.dataset.count || 0);
    return asc ? ca - cb : cb - ca;
  }});
  rows.forEach(r => tbody.appendChild(r));
  table.dataset.sortAsc = asc ? '0' : '1';
}}

(function() {{
  const modal = document.getElementById('skill-usage-modal');
  const modalTitle = document.getElementById('skill-usage-modal-title');
  const modalBody = document.getElementById('skill-usage-modal-body');
  const closeBtn = document.getElementById('skill-usage-modal-close');
  if (!modal || !modalTitle || !modalBody || !closeBtn) return;

  function esc(text) {{
    return String(text)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;');
  }}

  function usageBadge(item) {{
    if (!item.last_used) return '<span class="badge stale-never">never used</span>';
    return '<span class="badge usage-count">' + esc(item.count) + ' uses</span>' +
      ' <span class="badge badge-gray">' + esc(item.last_used) + '</span>';
  }}

  function renderRows(items) {{
    if (!items.length) {{
      return '<div class="modal-empty">No recorded usage yet</div>';
    }}
    const rows = items.map((item) => {{
      return '<tr data-count="' + esc(item.count) + '">' +
        '<td style="font-weight:500">' + esc(item.name) + '</td>' +
        '<td class="whitespace-nowrap">' + esc(item.count) + '</td>' +
        '<td class="whitespace-nowrap">' + usageBadge(item) + '</td>' +
        '</tr>';
    }}).join('');
    return '<div style="border-radius:8px;overflow:hidden">' +
      '<table class="at"><thead><tr><th>Skill</th><th>Uses</th><th>Last used</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div>';
  }}

  function openSkillUsageModal(card) {{
    const title = card.dataset.skillName || 'Plugin skills';
    let items = [];
    try {{
      items = JSON.parse(card.dataset.childUsage || '[]');
    }} catch (_err) {{
      items = [];
    }}
    modalTitle.textContent = title;
    modalBody.innerHTML = renderRows(items);
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
  }}

  function closeSkillUsageModal() {{
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }}

  document.querySelectorAll('.skill-item[data-child-usage]').forEach((card) => {{
    if (!card.dataset.childUsage || card.dataset.childUsage === '[]') return;
    card.addEventListener('click', (event) => {{
      if (event.target.closest('a')) return;
      openSkillUsageModal(card);
    }});
  }});

  closeBtn.addEventListener('click', closeSkillUsageModal);
  modal.addEventListener('click', (event) => {{
    if (event.target === modal) closeSkillUsageModal();
  }});
  document.addEventListener('keydown', (event) => {{
    if (event.key === 'Escape' && modal.classList.contains('open')) {{
      closeSkillUsageModal();
    }}
  }});
}})();
</script>
</body>
</html>"""


# ─── HTTP Server (dynamic) ────────────────────────────────────────────────────

def make_handler(all_data: dict, server_ref: list):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)

            if parsed.path == "/character":
                if _CHARACTER_IMG.exists():
                    img_data = _CHARACTER_IMG.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "no-cache, no-store")
                    self.send_header("Content-Length", str(len(img_data)))
                    self.end_headers()
                    self.wfile.write(img_data)
                else:
                    self._respond(404, b"not found", "text/plain")

            elif parsed.path == "/open":
                qs = urllib.parse.parse_qs(parsed.query)
                path = qs.get("path", [""])[0]
                if path and Path(path).exists():
                    if sys.platform == "darwin":
                        subprocess.run(["open", path], check=False)
                    elif sys.platform.startswith("linux"):
                        subprocess.run(["xdg-open", path], check=False)
                    else:
                        os.startfile(path)
                self._respond(200, b"ok", "text/plain")

            elif parsed.path == "/stop":
                self._respond(200, b"stopping", "text/plain")
                threading.Thread(target=server_ref[0].shutdown, daemon=True).start()

            elif parsed.path in ("/", "/index.html"):
                qs = urllib.parse.parse_qs(parsed.query)
                selected_dir = qs.get("dir", ["home"])[0]
                if selected_dir not in all_data:
                    selected_dir = "home"
                usage = get_cached_usage("*")
                if selected_dir == "project-only":
                    raw_data = build_project_only_data(all_data["home"][0], all_data["project-only"][0])
                    claude_dir = all_data["project-only"][1]
                    data = enrich_data(raw_data, {"skills": {}, "agents": {}, "mcp": {}})
                else:
                    raw_data, claude_dir = all_data[selected_dir]
                    data = enrich_data(raw_data, usage)
                html = build_html(data, claude_dir, selected_dir)
                self._respond(200, html.encode("utf-8"), "text/html; charset=utf-8")

            else:
                self._respond(404, b"not found", "text/plain")

        def _respond(self, code: int, body: bytes, ct: str):
            self.send_response(code)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            if "/open" in (args[0] if args else ""):
                path = ""
                try:
                    path = urllib.parse.parse_qs(
                        urllib.parse.urlparse(args[0].split()[1]).query
                    ).get("path", [""])[0]
                except Exception:
                    pass
                print(f"  open: {path}")

    return Handler


# ─── Main ──────────────────────────────────────────────────────────────────────

def _scan_dir(claude_dir: Path) -> dict:
    global CLAUDE_DIR
    CLAUDE_DIR = claude_dir
    settings = load_settings()
    data = {
        "plugins":     collect_plugins_raw(settings),
        "agents":      collect_agents_raw(),
        "skills":      collect_skills_raw(),
        "commands":    collect_commands(),
        "hooks":       collect_hooks(settings),
        "mcp_servers": collect_mcp_servers_raw(settings),
        "rules":       collect_rules(),
    }
    return data


def main():
    parser = argparse.ArgumentParser(description="Claude Config Dashboard")
    parser.add_argument("--port", type=int, default=PORT_DEFAULT)
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open browser")
    args = parser.parse_args()

    print(f"Scanning {HOME_CLAUDE} ...")
    home_data = _scan_dir(HOME_CLAUDE)
    for k, v in home_data.items():
        if isinstance(v, list):
            print(f"  {k:<12}: {len(v)}")

    all_data: dict = {"home": (home_data, HOME_CLAUDE)}

    if CWD_CLAUDE is not None:
        cwd_label = "~/" + str(CWD_CLAUDE.relative_to(Path.home()))
        print(f"\nScanning {cwd_label} ...")
        cwd_data = _scan_dir(CWD_CLAUDE)
        for k, v in cwd_data.items():
            if isinstance(v, list):
                print(f"  {k:<12}: {len(v)}")
        all_data["project-only"] = (cwd_data, CWD_CLAUDE)

    # Usage stats always read from HOME_CLAUDE (that's where the logs live)
    global CLAUDE_DIR
    CLAUDE_DIR = HOME_CLAUDE

    print("\n  Pre-computing usage stats ...")
    get_cached_usage("*")

    initial_url = f"http://localhost:{args.port}"
    server_ref: list = [None]
    server = HTTPServer(("localhost", args.port), make_handler(all_data, server_ref))
    server_ref[0] = server
    print(f"Dashboard → {initial_url}")
    print("Click any filename to open in default app. Stop: Ctrl+C or the Stop button\n")

    if not args.no_open:
        threading.Thread(target=lambda: __import__("webbrowser").open(initial_url), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    print("Server stopped")


if __name__ == "__main__":
    main()
