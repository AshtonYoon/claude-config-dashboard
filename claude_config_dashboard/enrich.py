"""Merge usage stats into collected config, and diff project vs home config."""


def enrich_plugins(plugins: list, usage: dict) -> list:
    skill_stats = usage.get("skills", {})
    result = []
    for p in plugins:
        prefix = p["name"] + ":"
        count = sum(v["count"] for k, v in skill_stats.items() if k.startswith(prefix) or k == p["name"])
        last = max(
            (
                v["last_used"]
                for k, v in skill_stats.items()
                if (k.startswith(prefix) or k == p["name"]) and v["last_used"]
            ),
            default="",
        )
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
            last = max(
                (v["last_used"] for k, v in skill_stats.items() if k.startswith(prefix) and v["last_used"]), default=""
            )
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
        "plugins": enrich_plugins(raw["plugins"], usage),
        "agents": enrich_agents(raw["agents"], usage),
        "skills": enrich_skills(raw["skills"], usage),
        "commands": raw["commands"],
        "hooks": raw["hooks"],
        "mcp_servers": enrich_mcp(raw["mcp_servers"], usage),
        "rules": raw["rules"],
        "measured": usage.get("session_context") or {},
        "window_start": usage.get("window_start", ""),
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
            item
            for item in project_raw["hooks"]
            if (item["trigger"], item["matcher"], item["command"]) not in home_hooks
        ],
        "mcp_servers": [item for item in project_raw["mcp_servers"] if item["name"] not in home_mcp_names],
        "rules": project_only_rules,
    }
