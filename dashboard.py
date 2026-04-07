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

CLAUDE_DIR = Path.home() / ".claude"
PORT_DEFAULT = 9876


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
        days = (datetime.now(timezone.utc) - dt).days
        date_str = dt.strftime("%Y-%m-%d")
        if days <= 7:
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
    ecc_dir = CLAUDE_DIR / "everything-claude-code" / "skills"
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

    scan(skills_dir, "custom")

    if ecc_dir.exists():
        ecc_count = sum(1 for d in ecc_dir.iterdir() if d.is_dir() and not d.name.startswith("."))
        skills.append({
            "name": f"everything-claude-code ({ecc_count} skills)",
            "slug": "everything-claude-code",
            "description": "Complete skill set from everything-claude-code plugin",
            "source": "plugin:everything-claude-code",
            "is_symlink": False,
            "path": str(ecc_dir),
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
        if slug == "everything-claude-code":
            count = sum(v["count"] for k, v in skill_stats.items() if k.startswith("everything-claude-code:"))
            last = max((v["last_used"] for k, v in skill_stats.items()
                        if k.startswith("everything-claude-code:") and v["last_used"]), default="")
        else:
            stat = skill_stats.get(slug, {"count": 0, "last_used": ""})
            count, last = stat["count"], stat["last_used"]
        result.append({**s, "usage_count": count, "last_used": last})
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

def _tab_btns() -> str:
    tabs = [("plugins", "Plugins"), ("agents", "Agents"), ("skills", "Skills"),
            ("commands", "Commands"), ("hooks", "Hooks"), ("mcp", "MCP Servers"),
            ("rules", "Rules"), ("cleanup", "🧹 Cleanup")]
    return "".join(
        f'<button class="tab-btn px-4 py-1.5 rounded text-sm font-medium text-gray-600 hover:bg-gray-100"'
        f' onclick="showTab(\'{t}\')" id="btn-{t}">{label}</button>'
        for t, label in tabs
    )

def _stats_header(items: list) -> str:
    parts = []
    for n, label, never in items:
        unused_line = f'<div class="text-xs text-red-400">{never} unused</div>' if never else ""
        parts.append(
            f'<div><div class="text-2xl font-bold text-indigo-600">{n}</div>'
            f'<div class="text-xs text-gray-500">{label}</div>'
            f'{unused_line}</div>'
        )
    return "".join(parts)

def _project_selector(known_projects: list, selected: str) -> str:
    options = ['<option value="*"' + (' selected' if selected == "*" else "") + '>🌐 All Projects</option>']
    for p in known_projects:
        sel = ' selected' if p["cwd"] == selected else ""
        label = _e(f'{p["name"]} ({p["sessions"]} sessions)')
        options.append(f'<option value="{_e(p["cwd"])}"{sel}>{label}</option>')
    opts_html = "".join(options)
    return (
        f'<select class="project-select text-sm border border-gray-200 rounded px-2 py-1 bg-white text-gray-700"'
        f' onchange="window.location=\'/?project=\'+encodeURIComponent(this.value)">'
        f'{opts_html}</select>'
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

        ver_b = f'<span class="badge bg-indigo-100 text-indigo-700">{ver}</span>' if ver else ""
        ena_b = ('<span class="badge bg-green-100 text-green-700">enabled</span>' if enabled
                 else '<span class="badge bg-red-100 text-red-600">disabled</span>')
        repo_a = (f'<a href="{_e(repo)}" target="_blank" class="text-xs text-indigo-500 hover:underline">'
                  f'{_e(repo.replace("https://github.com/", ""))}</a>') if repo else ""
        title = (_open_link(f'<span class="font-semibold text-gray-900">{name}</span>', rp)
                 if rp else f'<span class="font-semibold text-gray-900">{name}</span>')
        cards.append(f"""<div class="card">
  <div class="flex items-start justify-between mb-2">{title}<div class="flex gap-1 ml-2 flex-shrink-0">{ena_b}{ver_b}</div></div>
  {f'<p class="text-xs text-gray-500 mb-2">{desc}</p>' if desc else ''}
  <div class="flex items-center gap-2 flex-wrap">{repo_a}<span class="text-xs text-gray-400">@{mkt}</span></div>
  {f'<p class="text-xs text-gray-400 mt-1">installed: {inst}</p>' if inst else ''}
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
            f'<tr class="border-b hover:bg-gray-50" data-name="{_e(a["name"].lower())}" '
            f'data-count="{a.get("usage_count", 0)}" data-last="{_e(a.get("last_used", ""))}">'
            f'<td class="px-4 py-2 whitespace-nowrap">'
            f'{_open_link(_e(a["name"]), a["path"], "font-medium text-indigo-700")}</td>'
            f'<td class="px-4 py-2 text-xs text-gray-500">{_e(a["description"][:80])}</td>'
            f'<td class="px-4 py-2 whitespace-nowrap">'
            f'{_usage_html({"count": a.get("usage_count", 0), "last_used": a.get("last_used", "")})}</td>'
            f'</tr>'
            for a in items
        )
        table_id = "agent-table-" + cat.replace(" ", "-").replace("&", "")
        parts.append(f"""<details class="mb-4" open>
  <summary class="flex items-center gap-2 py-2">
    <span class="font-semibold text-gray-800">{_e(cat)}</span>
    <span class="badge bg-indigo-100 text-indigo-600">{len(items)}</span>
  </summary>
  <div class="bg-white border border-gray-200 rounded-lg overflow-hidden mt-2">
    <table class="w-full text-sm" id="{table_id}">
      <thead class="bg-gray-50 border-b">
        <tr>
          <th class="text-left px-4 py-2 font-semibold text-gray-600">Agent</th>
          <th class="text-left px-4 py-2 font-semibold text-gray-600">Description</th>
          <th class="text-left px-4 py-2 font-semibold text-gray-600 cursor-pointer hover:text-indigo-600"
              onclick="sortTable('{table_id}')">Usage ↕</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</details>""")
    return "".join(parts)


def render_skills(skills: list) -> str:
    never_count = sum(1 for s in skills if not s.get("last_used", ""))
    summary = f'<span class="text-xs text-red-500 font-medium">{never_count} never used</span>' if never_count else ""
    sort_bar = _sort_bar("skills-grid")
    cards = []
    for s in skills:
        name = _e(s["name"])
        desc = _e(s.get("description", ""))
        src = s.get("source", "custom")
        path = s.get("path", "")
        is_sym = s.get("is_symlink", False)
        last_iso = _e(s.get("last_used", ""))
        count = s.get("usage_count", 0)

        if src == "custom":
            badge = '<span class="badge source-custom">custom</span>'
        elif "plugin" in src:
            badge = f'<span class="badge source-plugin">{_e(src.replace("plugin:", ""))}</span>'
        else:
            badge = f'<span class="badge source-plugin">{_e(src)}</span>'
        if is_sym:
            badge += ' <span class="badge source-symlink">symlink</span>'

        usage_badge = _usage_html({"count": count, "last_used": s.get("last_used", "")})
        title = (_open_link(f'<span class="font-medium text-sm text-indigo-700">{name}</span>', path)
                 if path else f'<span class="font-medium text-sm text-gray-900">{name}</span>')
        cards.append(
            f'<div class="card skill-item" data-name="{_e(s["name"].lower())}" '
            f'data-count="{count}" data-last="{last_iso}">'
            f'<div class="flex items-start justify-between mb-1">{title}'
            f'<div class="flex gap-1 ml-2">{badge}</div></div>'
            f'{f"<p class=text-xs text-gray-500>{desc}</p>" if desc else ""}'
            f'{f"<div class=mt-2>{usage_badge}</div>" if usage_badge else ""}'
            f'</div>'
        )
    header = f'<div class="flex items-center justify-between mb-2">{sort_bar}{summary}</div>'
    return f'{header}<div id="skills-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">{"".join(cards)}</div>'


def render_commands(commands: list) -> str:
    rows = []
    for c in commands:
        slash = _e(c["slash"])
        desc = _e(c.get("description", ""))
        link = _open_link(f'<span class="font-mono text-indigo-600">{slash}</span>', c["path"])
        rows.append(f'<tr class="border-b hover:bg-gray-50"><td class="px-4 py-2 whitespace-nowrap">{link}</td>'
                    f'<td class="px-4 py-2 text-gray-600 text-sm">{desc}</td></tr>')
    return "".join(rows)


def render_hooks(hooks: list) -> str:
    colors = {
        "PreToolUse": "bg-yellow-100 text-yellow-800",
        "PostToolUse": "bg-blue-100 text-blue-800",
        "Stop": "bg-red-100 text-red-700",
        "SubagentStop": "bg-orange-100 text-orange-700",
        "UserPromptSubmit": "bg-green-100 text-green-800",
        "PreCompact": "bg-purple-100 text-purple-700",
        "SessionStart": "bg-teal-100 text-teal-700",
    }
    parts = []
    for h in hooks:
        color = colors.get(h["trigger"], "bg-gray-100 text-gray-700")
        cmd_display = _e(h["command"])
        cmd_html = (_open_link(f'<code class="text-xs text-gray-700 break-all">{cmd_display}</code>', h["path"])
                    if h["path"] else f'<code class="text-xs text-gray-700 break-all">{cmd_display}</code>')
        parts.append(f"""<div class="card flex items-start gap-4">
  <span class="badge {color} whitespace-nowrap mt-0.5">{_e(h['trigger'])}</span>
  <div class="flex-1 min-w-0">{cmd_html}
    {f'<p class="text-xs text-gray-400 mt-1">matcher: {_e(h["matcher"])}</p>' if h.get("matcher") else ''}
  </div>
</div>""")
    return "".join(parts)


def render_mcp(servers: list) -> str:
    if not servers:
        return '<div class="text-gray-400 text-sm py-8 text-center">No MCP servers configured</div>'
    never_count = sum(1 for s in servers if not s.get("last_used", ""))
    summary = f'<span class="text-xs text-red-500 font-medium">{never_count} never used</span>' if never_count else ""
    sort_bar = _sort_bar("mcp-grid")
    header = f'<div class="flex items-center justify-between mb-3">{sort_bar}{summary}</div>'
    cards = []
    for s in servers:
        args = " ".join(_e(str(a)) for a in s.get("args", [])[:4])
        if len(s.get("args", [])) > 4:
            args += " ..."
        src = _e(s.get("source", ""))
        last_iso = _e(s.get("last_used", ""))
        count = s.get("usage_count", 0)
        usage_badge = _usage_html({"count": count, "last_used": s.get("last_used", "")})
        src_badge = f'<span class="badge bg-gray-100 text-gray-500">{src}</span>' if src else ""
        cards.append(
            f'<div class="card" data-name="{_e(s["name"].lower())}" data-count="{count}" data-last="{last_iso}">'
            f'<div class="flex items-center justify-between mb-1">'
            f'<h3 class="font-semibold text-gray-900">{_e(s["name"])}</h3>{src_badge}</div>'
            f'<code class="text-xs text-gray-600 break-all">{_e(s.get("command", ""))} {args}</code>'
            f'{f"<div class=mt-2>{usage_badge}</div>" if usage_badge else ""}'
            f'</div>'
        )
    return f'{header}<div id="mcp-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">{"".join(cards)}</div>'


def render_rules(rules: list) -> str:
    cards = []
    for r in rules:
        files_html = "".join(
            f'<li class="text-xs py-0.5">{_open_link(_e(f["name"]), f["path"], "text-indigo-600")}</li>'
            for f in r["files"]
        )
        cards.append(f"""<div class="card">
  <h3 class="font-semibold text-gray-900 mb-2">{_e(r["category"])}/</h3>
  <ul class="list-disc list-inside space-y-0.5">{files_html}</ul>
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
        return '<div class="text-center py-12 text-gray-400 text-sm">Everything looks active — no stale items found.</div>'

    def section(title: str, items: list, type_label: str) -> str:
        if not items:
            return ""
        rows = []
        for item in sorted(items, key=lambda x: x.get("last_used", "")):
            name = item.get("name", item.get("label", ""))
            path = item.get("path", item.get("readme_path", ""))
            usage_badge = _usage_html({"count": item.get("usage_count", 0), "last_used": item.get("last_used", "")})
            link = (_open_link(f'<span class="font-medium text-indigo-700">{_e(name)}</span>', path)
                    if path else f'<span class="font-medium text-gray-700">{_e(name)}</span>')
            rows.append(
                f'<tr class="border-b hover:bg-gray-50">'
                f'<td class="px-4 py-2">{link}</td>'
                f'<td class="px-4 py-2"><span class="badge bg-gray-100 text-gray-500 text-xs">{type_label}</span></td>'
                f'<td class="px-4 py-2">{usage_badge}</td>'
                f'</tr>'
            )
        return f"""<div class="mb-6">
  <h3 class="font-semibold text-gray-700 mb-2">{_e(title)} <span class="badge bg-red-100 text-red-600">{len(items)}</span></h3>
  <div class="bg-white border border-gray-200 rounded-lg overflow-hidden">
    <table class="w-full text-sm">
      <thead class="bg-gray-50 border-b">
        <tr>
          <th class="text-left px-4 py-2 font-semibold text-gray-600">Name</th>
          <th class="text-left px-4 py-2 font-semibold text-gray-600">Type</th>
          <th class="text-left px-4 py-2 font-semibold text-gray-600">Status</th>
        </tr>
      </thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
</div>"""

    summary = f"""<div class="bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 mb-6 flex items-center gap-3">
  <span class="text-2xl">🧹</span>
  <div>
    <p class="font-semibold text-amber-800">{total} items haven't been used in the last {STALE_DAYS} days (in this scope)</p>
    <p class="text-xs text-amber-600 mt-0.5">Review these to keep your ~/.claude lean</p>
  </div>
</div>"""

    return summary + section("Agents", stale_agents, "agent") + section("Skills", stale_skills, "skill") + section("MCP Servers", stale_mcp, "mcp")


# ─── Build HTML ───────────────────────────────────────────────────────────────

def build_html(data: dict, project_cwd: str, known_projects: list) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    p, ag, sk, co, ho, mc, ru = (data["plugins"], data["agents"], data["skills"],
        data["commands"], data["hooks"], data["mcp_servers"], data["rules"])
    n_cats = len({a["category"] for a in ag})
    agents_never = sum(1 for a in ag if not a.get("last_used", ""))
    skills_never = sum(1 for s in sk if not s.get("last_used", ""))
    mcp_never    = sum(1 for m in mc if not m.get("last_used", ""))

    if project_cwd == "*":
        scope_label = "🌐 All Projects"
        scope_badge = '<span class="badge bg-gray-100 text-gray-600">global</span>'
    else:
        scope_name = Path(project_cwd).name
        scope_label = scope_name
        scope_badge = f'<span class="badge bg-indigo-100 text-indigo-700" title="{_e(project_cwd)}">{_e(scope_name)}</span>'

    project_sel = _project_selector(known_projects, project_cwd)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Config Dashboard · {_e(scope_label)}</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
  .tab-btn {{ transition: all .15s; }}
  .tab-btn.active {{ background:#6366f1; color:white; }}
  .tab-content {{ display:none; }}
  .tab-content.active {{ display:block; }}
  .badge {{ display:inline-block; padding:1px 8px; border-radius:9999px; font-size:11px; font-weight:600; }}
  .card {{ border:1px solid #e5e7eb; border-radius:8px; padding:16px; background:white; transition:border-color .15s,box-shadow .15s; }}
  .card:hover {{ border-color:#a5b4fc; box-shadow:0 2px 8px rgba(99,102,241,.1); }}
  .source-custom {{ background:#d1fae5; color:#065f46; }}
  .source-plugin {{ background:#dbeafe; color:#1e40af; }}
  .source-symlink {{ background:#fef3c7; color:#92400e; }}
  details summary {{ cursor:pointer; list-style:none; }}
  details summary::before {{ content:"▶"; margin-right:6px; font-size:10px; color:#6366f1; transition:transform .2s; }}
  details[open] summary::before {{ transform:rotate(90deg); }}
  a {{ cursor:pointer; }}
  .stale-recent {{ background:#d1fae5; color:#065f46; }}
  .stale-mid    {{ background:#fef3c7; color:#92400e; }}
  .stale-old    {{ background:#fee2e2; color:#991b1b; }}
  .stale-never  {{ background:#fee2e2; color:#991b1b; font-weight:700; }}
  .usage-count  {{ background:#e0e7ff; color:#3730a3; }}
  .sort-bar {{ display:flex; align-items:center; gap:4px; }}
  .sort-btn {{
    padding:2px 10px; border-radius:9999px; font-size:11px; font-weight:600;
    border:1px solid #e5e7eb; background:white; color:#6b7280; cursor:pointer; transition:all .15s;
  }}
  .sort-btn:hover {{ border-color:#6366f1; color:#6366f1; }}
  .sort-btn.active {{ background:#6366f1; color:white; border-color:#6366f1; }}
  .project-select {{ font-size:13px; }}
</style>
</head>
<body class="bg-gray-50 min-h-screen">

<div class="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between sticky top-0 z-10 shadow-sm">
  <div class="flex items-center gap-3">
    <span class="text-2xl">🤖</span>
    <div>
      <h1 class="text-lg font-bold text-gray-900">Claude Config Dashboard</h1>
      <p class="text-xs text-gray-400">{str(CLAUDE_DIR).replace(str(Path.home()), "~")} · {now}</p>
    </div>
  </div>
  <div class="flex items-center gap-6">
    <div class="flex gap-4 text-center">
      {_stats_header([
          (len(p),"Plugins",0),(len(ag),"Agents",agents_never),
          (len(sk),"Skills",skills_never),(len(co),"Commands",0),
          (len(ho),"Hooks",0),(len(mc),"MCP",mcp_never),
      ])}
    </div>
    <div class="flex flex-col items-end gap-1">
      <div class="text-xs text-gray-400">Scope</div>
      {project_sel}
    </div>
  </div>
</div>

<div class="bg-white border-b border-gray-200 px-6">
  <div class="flex gap-1 py-2 flex-wrap">{_tab_btns()}</div>
</div>

<div class="px-6 py-6 max-w-7xl mx-auto">

<div id="tab-plugins" class="tab-content">
  <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_plugins(p)}</div>
</div>

<div id="tab-agents" class="tab-content">
  <p class="text-xs text-gray-400 mb-4">{len(ag)} agents · {n_cats} categories · Click name to open in default app
  {f' · <span class="text-red-400 font-medium">{agents_never} never used in this scope</span>' if agents_never else ''}
  </p>
  {render_agents(ag)}
</div>

<div id="tab-skills" class="tab-content">
  {render_skills(sk)}
</div>

<div id="tab-commands" class="tab-content">
  <p class="text-xs text-gray-400 mb-3">Click command to open in default app</p>
  <div class="bg-white rounded-lg border border-gray-200 overflow-hidden">
    <table class="w-full text-sm">
      <thead class="bg-gray-50 border-b"><tr>
        <th class="text-left px-4 py-2 font-semibold text-gray-600">Command</th>
        <th class="text-left px-4 py-2 font-semibold text-gray-600">Description</th>
      </tr></thead>
      <tbody>{render_commands(co)}</tbody>
    </table>
  </div>
</div>

<div id="tab-hooks" class="tab-content">
  <p class="text-xs text-gray-400 mb-3">Click command to open script file</p>
  <div class="space-y-3">{render_hooks(ho)}</div>
</div>

<div id="tab-mcp" class="tab-content">
  {render_mcp(mc)}
</div>

<div id="tab-rules" class="tab-content">
  <p class="text-xs text-gray-400 mb-3">Click filename to open in default app</p>
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4">{render_rules(ru)}</div>
</div>

<div id="tab-cleanup" class="tab-content">
  {render_cleanup(ag, sk, mc)}
</div>

</div>

<script>
function showTab(name) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.getElementById('btn-' + name).classList.add('active');
  localStorage.setItem('claude-dash-tab', name);
}}
showTab(localStorage.getItem('claude-dash-tab') || 'plugins');

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
</script>
</body>
</html>"""


# ─── HTTP Server (dynamic) ────────────────────────────────────────────────────

def make_handler(raw_data: dict, known_projects: list):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)

            if parsed.path == "/open":
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

            elif parsed.path in ("/", "/index.html"):
                qs = urllib.parse.parse_qs(parsed.query)
                project_cwd = qs.get("project", ["*"])[0]
                usage = get_cached_usage(project_cwd)
                data = enrich_data(raw_data, usage)
                html = build_html(data, project_cwd, known_projects)
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

def main():
    parser = argparse.ArgumentParser(description="Claude Config Dashboard")
    parser.add_argument("--port", type=int, default=PORT_DEFAULT)
    parser.add_argument("--project", default=None,
                        help="Scope usage stats to a specific project path (default: all)")
    args = parser.parse_args()

    print("Scanning ~/.claude ...")
    settings = load_settings()
    raw_data = {
        "plugins":     collect_plugins_raw(settings),
        "agents":      collect_agents_raw(),
        "skills":      collect_skills_raw(),
        "commands":    collect_commands(),
        "hooks":       collect_hooks(settings),
        "mcp_servers": collect_mcp_servers_raw(settings),
        "rules":       collect_rules(),
    }
    for k, v in raw_data.items():
        if isinstance(v, list):
            print(f"  {k:<12}: {len(v)}")

    print("  Loading project list...")
    known_projects = list_known_projects()
    print(f"  {len(known_projects)} known projects")

    # Pre-warm the default scope
    default_project = args.project or "*"
    print(f"  Pre-computing usage stats (scope: {default_project}) ...")
    get_cached_usage(default_project)

    initial_url = f"http://localhost:{args.port}"
    if default_project != "*":
        initial_url += f"?project={urllib.parse.quote(default_project)}"

    server = HTTPServer(("localhost", args.port), make_handler(raw_data, known_projects))
    print(f"\nDashboard → {initial_url}")
    print("Click any filename to open in default app. Stop: Ctrl+C\n")

    threading.Thread(target=lambda: __import__("webbrowser").open(initial_url), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")


if __name__ == "__main__":
    main()
