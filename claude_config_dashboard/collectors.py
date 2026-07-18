"""Raw config collectors: read plugins, agents, skills, commands, hooks,
MCP servers, and rules out of a .claude directory."""

import json
from datetime import datetime
from pathlib import Path


def load_settings(claude_dir: Path) -> dict:
    p = claude_dir / "settings.json"
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


def collect_plugins_raw(claude_dir: Path, settings: dict) -> list:
    enabled = settings.get("enabledPlugins", {})
    marketplaces = settings.get("extraKnownMarketplaces", {})
    cache_dir = claude_dir / "plugins" / "cache"
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


def collect_agents_raw(claude_dir: Path) -> list:
    d = claude_dir / "agents"
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


def _skill_content_file(item: Path):
    # SKILL.md is the documented name; also accept skill.md (historic) so the
    # lookup works on case-sensitive filesystems (Linux), then README.md.
    for name in ("SKILL.md", "skill.md", "README.md"):
        p = item / name
        if p.exists():
            return p
    return None


def collect_skills_raw(claude_dir: Path) -> list:
    skills_dir = claude_dir / "skills"
    cache_dir = claude_dir / "plugins" / "cache"
    skills = []

    def scan(base: Path, source: str):
        if not base.exists():
            return
        for item in sorted(base.iterdir()):
            if item.name.startswith(".") or item.name == "learned":
                continue
            if item.is_dir():
                content_path = _skill_content_file(item)
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

    seen_plugins: set = set()
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


def collect_commands(claude_dir: Path) -> list:
    d = claude_dir / "commands"
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


def collect_rules(claude_dir: Path) -> list:
    rules_dir = claude_dir / "rules"
    if not rules_dir.exists():
        return []
    rules = []
    for cat in sorted(rules_dir.iterdir()):
        if not cat.is_dir() or cat.name.startswith("."): continue
        files = [{"name": f.name, "path": str(f)} for f in sorted(cat.glob("*.md"))]
        rules.append({"category": cat.name, "files": files})
    return rules


def scan_dir(claude_dir: Path) -> dict:
    """Collect every config category from one .claude directory."""
    settings = load_settings(claude_dir)
    return {
        "plugins":     collect_plugins_raw(claude_dir, settings),
        "agents":      collect_agents_raw(claude_dir),
        "skills":      collect_skills_raw(claude_dir),
        "commands":    collect_commands(claude_dir),
        "hooks":       collect_hooks(settings),
        "mcp_servers": collect_mcp_servers_raw(settings),
        "rules":       collect_rules(claude_dir),
    }
