"""HTML rendering: per-tab renderers plus the page assembly from templates."""

import json
import urllib.parse
from datetime import datetime
from functools import cache
from importlib import resources
from pathlib import Path
from string import Template

from . import paths, security
from .context_tax import compute_context_tax
from .usage import _stale_info


@cache
def _template_text(name: str) -> str:
    tpl = resources.files("claude_config_dashboard") / "templates" / name
    return tpl.read_text(encoding="utf-8")


# ─── HTML Helpers ─────────────────────────────────────────────────────────────


def _e(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _open_link(label: str, path: str, cls: str = "") -> str:
    if not path:
        return f'<span class="{cls}">{label}</span>'
    security.register_openable(path)
    enc = urllib.parse.quote(path, safe="")
    return (
        f'<a onclick="openFile(\'{enc}\')" class="{cls} hover:underline cursor-pointer" title="{_e(path)}">{label}</a>'
    )


def _usage_html(stat: dict) -> str:
    count = stat.get("count", 0)
    last_used = stat.get("last_used", "")
    days, label, cls = _stale_info(last_used)
    if not cls:
        return ""
    title = _e(last_used) if last_used else ""
    count_badge = f'<span class="badge usage-count">{count}×</span> ' if count > 0 else ""
    return f'{count_badge}<span class="badge {cls}" title="{title}">{_e(label)}</span>'


def _sort_bar(grid_id: str, default: str = "name") -> str:
    buttons = [("name", "Name"), ("count", "Usage Count"), ("last", "Last Used")]
    btns = "".join(
        f'<button class="sort-btn {"active" if k == default else ""}" '
        f"onclick=\"sortGrid('{grid_id}','{k}',this)\">{label}</button>"
        for k, label in buttons
    )
    return f'<div class="sort-bar">{btns}</div>'


def _tab_btns(selected_dir: str) -> str:
    if selected_dir == "project-only":
        tabs = [
            ("mcp", "Project MCP"),
            ("skills", "Project Skills"),
            ("commands", "Project Commands"),
            ("hooks", "Project Hooks"),
            ("rules", "Project Rules"),
        ]
    else:
        tabs = [
            ("plugins", "Plugins"),
            ("agents", "Agents"),
            ("skills", "Skills"),
            ("commands", "Commands"),
            ("hooks", "Hooks"),
            ("mcp", "MCP Servers"),
            ("rules", "Rules"),
            ("tax", "Context Tax"),
            ("cleanup", "Cleanup"),
        ]
    return "".join(
        f'<button class="tab-btn" onclick="showTab(\'{t}\')" id="btn-{t}">{label}</button>' for t, label in tabs
    )


def _stats_header(items: list) -> str:
    parts = []
    for n, label, never in items:
        unused_line = (
            f'<div class="nav-stat-w">{never} unused</div>'
            if never
            else '<div class="nav-stat-w" style="visibility:hidden">·</div>'
        )
        parts.append(
            f'<div class="nav-stat">'
            f'<div class="nav-stat-n">{n}</div>'
            f'<div class="nav-stat-l">{label}</div>'
            f"{unused_line}</div>"
        )
    return "".join(parts)


def _dir_selector(selected_dir: str) -> str:
    """Toggle between ~/.claude and the project-only comparison view."""
    if paths.CWD_CLAUDE is None:
        return ""
    options = [
        f'<option value="home"{"  selected" if selected_dir == "home" else ""}>{_e("~/.claude")}</option>',
        f'<option value="project-only"{"  selected" if selected_dir == "project-only" else ""}>{_e("Project-only config")}</option>',
    ]
    return (
        '<select class="dir-select" onchange="window.location=\'/?dir=\'+this.value">' + "".join(options) + "</select>"
    )


def _home_hero(measured: dict, gaps: list, window_start: str) -> str:
    """Persistent banner above the tabs: measured session cost + install-vs-used gap.

    gaps is a list of (installed, used, label) tuples.
    """
    median = measured.get("median", 0)
    count = measured.get("count", 0)
    if not median and not any(installed for installed, _, _ in gaps):
        return ""

    token_block = ""
    if median:
        sess = "session" if count == 1 else "sessions"
        token_block = (
            f'<div style="font-family:Georgia,serif;font-size:38px;font-weight:500;color:var(--brand);line-height:1.1">{median:,}</div>'
            f'<div style="font-size:13px;color:var(--text-s);margin-top:4px">tokens <strong>every session starts with</strong>, before you do any work. '
            f"Measured from your last {count} {sess}, not estimated. "
            f'<a onclick="showTab(\'tax\')" class="hover:underline cursor-pointer" style="color:var(--brand)">See the breakdown &rarr;</a></div>'
        )

    gap_items = []
    for installed, used, label in gaps:
        if not installed:
            continue
        unused = installed - used
        unused_html = f' &middot; <span style="color:#b53333">{unused} never used</span>' if unused else ""
        gap_items.append(
            f'<div style="font-size:13px;color:var(--text-s)">'
            f'<strong style="color:var(--text-p)">{used}</strong> / {installed} {_e(label)} used{unused_html}</div>'
        )
    gap_block = (
        '<div style="display:flex;flex-wrap:wrap;gap:18px;margin-top:14px">' + "".join(gap_items) + "</div>"
        if gap_items
        else ""
    )

    window_note = (
        f'<div style="font-size:11px;color:var(--text-t);margin-top:10px">Usage measured from sessions since {_e(window_start[:10])}.</div>'
        if window_start
        else ""
    )
    return f'<div class="card" style="padding:22px 24px;margin-bottom:20px">{token_block}{gap_block}{window_note}</div>'


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
        ena_b = (
            '<span class="badge badge-green">enabled</span>'
            if enabled
            else '<span class="badge badge-red">disabled</span>'
        )
        repo_a = (
            (
                f'<a href="{_e(repo)}" target="_blank" class="al" style="font-size:12px">'
                f"{_e(repo.replace('https://github.com/', ''))}</a>"
            )
            if repo
            else ""
        )
        title = (
            _open_link(f'<span style="font-weight:600;color:var(--text-p)">{name}</span>', rp)
            if rp
            else f'<span style="font-weight:600;color:var(--text-p)">{name}</span>'
        )
        cards.append(f"""<div class="card">
  <div class="flex items-start justify-between mb-2">{title}<div class="flex gap-1 ml-2 flex-shrink-0">{ena_b}{ver_b}</div></div>
  {f'<p style="font-size:12px;color:var(--text-s);margin-bottom:8px">{desc}</p>' if desc else ""}
  <div class="flex items-center gap-2 flex-wrap">{repo_a}<span style="font-size:11px;color:var(--text-t)">@{mkt}</span></div>
  {f'<p style="font-size:11px;color:var(--text-t);margin-top:4px">installed: {inst}</p>' if inst else ""}
  {f'<div class="mt-2">{usage_badge}</div>' if usage_badge else ""}
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
            f"{_open_link(_e(a['name']), a['path'], 'al')}</td>"
            f'<td style="color:var(--text-s)">{_e(a["description"][:80])}</td>'
            f'<td class="whitespace-nowrap">'
            f"{_usage_html({'count': a.get('usage_count', 0), 'last_used': a.get('last_used', '')})}</td>"
            f"</tr>"
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
        title = (
            _open_link(f'<span style="font-weight:500;font-size:14px" class="al">{name}</span>', path)
            if path
            else f'<span style="font-weight:500;font-size:14px;color:var(--text-p)">{name}</span>'
        )
        desc_html = f'<p style="font-size:12px;color:var(--text-s)">{desc}</p>' if desc else ""
        usage_html = f'<div style="margin-top:8px">{usage_badge}</div>' if usage_badge else ""
        click_badge = (
            '<div style="margin-top:8px;font-size:11px;color:var(--brand)">Click to view child skill usage</div>'
            if clickable
            else ""
        )
        card_class = "card skill-item skill-item-clickable" if clickable else "card skill-item"
        cards.append(
            f'<div class="{card_class}" data-name="{_e(s["name"].lower())}" '
            f'data-count="{count}" data-last="{last_iso}" '
            f'data-skill-name="{name}" data-child-usage="{child_usage_json}">'
            f'<div class="flex items-start justify-between mb-1">{title}'
            f'<div class="flex gap-1 ml-2">{badge}</div></div>'
            f"{desc_html}{usage_html}{click_badge}</div>"
        )
    header = (
        f'<div class="flex items-center justify-between mb-3">{sort_bar}{summary}</div>'
        if (sort_bar or summary)
        else ""
    )
    return f'{header}<div id="skills-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">{"".join(cards)}</div>'


def render_commands(commands: list) -> str:
    if not commands:
        return '<tr><td colspan="2" style="color:var(--text-t);text-align:center;padding:32px">No project-only commands found.</td></tr>'
    rows = []
    for c in commands:
        slash = _e(c["slash"])
        desc = _e(c.get("description", ""))
        link = _open_link(f'<span style="font-family:monospace;color:var(--brand)">{slash}</span>', c["path"])
        rows.append(f'<tr><td class="whitespace-nowrap">{link}</td><td style="color:var(--text-s)">{desc}</td></tr>')
    return "".join(rows)


def render_hooks(hooks: list) -> str:
    colors = {
        "PreToolUse": "badge-amber",
        "PostToolUse": "badge-blue",
        "Stop": "badge-red",
        "SubagentStop": "badge-red",
        "UserPromptSubmit": "badge-green",
        "PreCompact": "badge-gray",
        "SessionStart": "badge-green",
    }
    parts = []
    for h in hooks:
        color = colors.get(h["trigger"], "badge-gray")
        cmd_display = _e(h["command"])
        cmd_html = (
            _open_link(
                f'<code style="font-size:12px;color:var(--text-s);word-break:break-all">{cmd_display}</code>', h["path"]
            )
            if h["path"]
            else f'<code style="font-size:12px;color:var(--text-s);word-break:break-all">{cmd_display}</code>'
        )
        parts.append(f"""<div class="card flex items-start gap-4">
  <span class="badge {color}" style="white-space:nowrap;margin-top:2px">{_e(h["trigger"])}</span>
  <div style="flex:1;min-width:0">{cmd_html}
    {f'<p style="font-size:11px;color:var(--text-t);margin-top:4px">matcher: {_e(h["matcher"])}</p>' if h.get("matcher") else ""}
  </div>
</div>""")
    return "".join(parts)


def render_mcp(servers: list, show_usage: bool = True, empty_message: str = "No MCP servers configured") -> str:
    if not servers:
        return (
            f'<div style="color:var(--text-t);font-size:14px;padding:32px;text-align:center">{_e(empty_message)}</div>'
        )
    never_count = sum(1 for s in servers if not s.get("last_used", ""))
    summary = f'<span class="badge badge-red">{never_count} never used</span>' if show_usage and never_count else ""
    sort_bar = _sort_bar("mcp-grid") if show_usage else ""
    header = (
        f'<div class="flex items-center justify-between mb-3">{sort_bar}{summary}</div>'
        if (sort_bar or summary)
        else ""
    )
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
            f"{f'<div class=mt-2>{usage_badge}</div>' if usage_badge else ''}"
            f"</div>"
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
            link = (
                _open_link(f'<span style="font-weight:500" class="al">{_e(name)}</span>', path)
                if path
                else f'<span style="font-weight:500;color:var(--text-p)">{_e(name)}</span>'
            )
            rows.append(
                f"<tr>"
                f"<td>{link}</td>"
                f'<td><span class="badge badge-gray">{type_label}</span></td>'
                f"<td>{usage_badge}</td>"
                f"</tr>"
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

    return (
        summary
        + section("Agents", stale_agents, "agent")
        + section("Skills", stale_skills, "skill")
        + section("MCP Servers", stale_mcp, "mcp")
    )


def render_context_tax(tax: dict) -> str:
    total = tax["total_tokens"]
    categories = tax["categories"]
    reclaimable = tax["reclaimable_items"]
    reclaimable_tokens = tax["reclaimable_tokens"]
    measured = tax.get("measured") or {}
    window_start = tax.get("window_start", "")
    max_cat = max((c["tokens"] for c in categories), default=0) or 1

    if measured.get("count"):
        median, mn, mx, cnt = measured["median"], measured["min"], measured["max"], measured["count"]
        sess = "session" if cnt == 1 else "sessions"
        hero = f"""<div class="card" style="text-align:center;padding:28px 16px;margin-bottom:8px">
  <div style="font-family:Georgia,serif;font-size:44px;font-weight:500;color:var(--brand)">{median:,}</div>
  <div style="font-size:13px;color:var(--text-s);margin-top:4px">tokens <strong>every session starts with</strong>, before you do any work</div>
  <div style="font-size:12px;color:var(--text-t);margin-top:6px">measured from your last {cnt} {sess} (range {mn:,}–{mx:,}), not estimated</div>
</div>"""
    else:
        hero = f"""<div class="card" style="text-align:center;padding:28px 16px;margin-bottom:8px">
  <div style="font-family:Georgia,serif;font-size:44px;font-weight:500;color:var(--brand)">~{total:,}</div>
  <div style="font-size:13px;color:var(--text-s);margin-top:4px">estimated tokens added to <strong>every session</strong> by this config</div>
</div>"""

    reclaim_html = ""
    if reclaimable:
        reclaim_rows = []
        for item in reclaimable[:20]:
            name_html = _e(item["name"])
            if item["path"]:
                name_html = _open_link(f'<span style="font-weight:500" class="al">{name_html}</span>', item["path"])
            else:
                name_html = f'<span style="font-weight:500;color:var(--text-p)">{name_html}</span>'
            badge = _usage_html({"count": 0, "last_used": item["last_used"] or ""})
            reclaim_rows.append(
                f"<tr><td>{name_html}</td>"
                f'<td><span class="badge badge-gray">{_e(item["kind"])}</span></td>'
                f'<td style="font-family:monospace">{item["tokens"]:,}</td>'
                f"<td>{badge}</td></tr>"
            )
        rows = "".join(reclaim_rows)
        plan_items = [
            {
                "name": i["name"],
                "kind": i["kind"],
                "tokens": i["tokens"],
                "archiveSource": i["archive_source"],
                "skipReason": i["skip_reason"],
            }
            for i in reclaimable
        ]
        plan_json = _e(
            json.dumps(
                {"archiveDir": tax["archive_dir"], "items": plan_items},
                separators=(",", ":"),
            )
        )
        reclaim_html = f"""<div style="background:rgba(201,100,66,.07);border:1px solid rgba(201,100,66,.18);border-radius:8px;padding:14px 18px;margin-bottom:20px">
  <p style="font-weight:500;color:#c96442;font-size:14px">~{reclaimable_tokens:,} tokens reclaimable</p>
  <p style="font-size:12px;color:#87867f;margin-top:2px">{len(reclaimable)} skills/agents unused for {30}+ days still cost context every session</p>
  <div id="cleanup-plan-data" data-plan="{plan_json}" style="margin-top:10px;display:flex;gap:8px">
    <button class="sort-btn" onclick="downloadCleanupScript()">Download cleanup script (.sh)</button>
    <button class="sort-btn" onclick="copyCleanupScript()">Copy to clipboard</button>
  </div>
  <p style="font-size:11px;color:var(--text-t);margin-top:8px">The script only moves files into a dated archive folder — nothing is deleted. Review it before running.</p>
</div>
<div style="border-radius:8px;overflow:hidden;margin-bottom:24px">
  <table class="at">
    <thead><tr><th>Item</th><th>Type</th><th>Est. tokens</th><th>Status</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

    bars = "".join(
        f"""<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
  <div style="width:110px;font-size:12px;color:var(--text-s);text-align:right">{_e(c["label"])}</div>
  <div style="flex:1;background:rgba(0,0,0,.04);border-radius:6px;height:18px;overflow:hidden">
    <div style="width:{max(c["tokens"] / max_cat * 100, 1):.1f}%;background:var(--brand);height:100%;border-radius:6px;opacity:.85"></div>
  </div>
  <div style="width:80px;font-family:monospace;font-size:12px;color:var(--text-p)">{c["tokens"]:,}</div>
</div>"""
        for c in categories
    )
    bars_caption = (
        '<p style="font-size:12px;color:var(--text-s);margin-bottom:10px">'
        "Where your own config contributes (static estimate, chars &divide; 4):</p>"
    )
    reconcile = ""
    if measured.get("count"):
        baseline = max(measured["median"] - total, 0)
        reconcile = (
            '<p style="font-size:12px;color:var(--text-s);margin-top:12px;padding-top:10px;border-top:1px solid rgba(0,0,0,.06)">'
            f"Your config is &asymp; <strong>{total:,}</strong> of the measured total. The remaining "
            f"&asymp; <strong>{baseline:,}</strong> is Claude Code's own baseline (system prompt + MCP tool "
            "schemas) &mdash; measured, and not yours to cut.</p>"
        )
    bars_html = f'<div class="card" style="margin-bottom:24px">{bars_caption}{bars}{reconcile}</div>'

    details = []
    for c in categories:
        if not c["items"]:
            continue
        rows = "".join(
            f"<tr>"
            f"<td>{_open_link(_e(i['name']), i['path'], 'al') if i['path'] else _e(i['name'])}</td>"
            f'<td style="font-family:monospace">{i["tokens"]:,}</td>'
            f"</tr>"
            for i in c["items"]
        )
        details.append(f"""<details class="mb-4">
  <summary class="flex items-center gap-2 py-2">
    <span style="font-weight:600;color:var(--text-p)">{_e(c["label"])}</span>
    <span class="badge badge-blue">{c["tokens"]:,} tokens</span>
  </summary>
  <div style="border-radius:8px;overflow:hidden;margin-top:8px">
    <table class="at">
      <thead><tr><th>Item</th><th>Est. tokens</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</details>""")

    if measured.get("count"):
        window_note = f" Usage measured from sessions since {_e(window_start[:10])}." if window_start else ""
        footnote = (
            '<p style="font-size:11px;color:var(--text-t);margin-top:16px">'
            "The headline number is <strong>measured</strong> from your session transcripts "
            "(cache-creation + cache-read + input tokens at each session's first turn), so it already includes "
            "Claude Code's system prompt and MCP tool schemas. The breakdown above is a separate static estimate "
            "(chars &divide; 4) of what your own config files contribute: CLAUDE.md and rules count full file content; "
            "skills, agents, and commands count their listing line (name + description) — bodies load only on "
            "invocation; hooks add no context cost." + window_note + "</p>"
        )
    else:
        footnote = (
            '<p style="font-size:11px;color:var(--text-t);margin-top:16px">'
            "Estimates use chars ÷ 4. CLAUDE.md and rules count full file content; skills, agents, and commands "
            "count their listing line (name + description) — bodies load only on invocation. "
            "MCP tool schemas are loaded at runtime and can't be measured statically; hooks add no context cost.</p>"
        )

    return hero + reclaim_html + bars_html + "".join(details) + footnote


# ─── Build HTML ───────────────────────────────────────────────────────────────


def build_html(data: dict, claude_dir: Path, selected_dir: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    p, ag, sk, co, ho, mc, ru = (
        data["plugins"],
        data["agents"],
        data["skills"],
        data["commands"],
        data["hooks"],
        data["mcp_servers"],
        data["rules"],
    )
    n_cats = len({a["category"] for a in ag}) if ag else 0
    agents_never = sum(1 for a in ag if not a.get("last_used", ""))
    skills_never = sum(1 for s in sk if not s.get("last_used", ""))
    mcp_never = sum(1 for m in mc if not m.get("last_used", ""))
    is_project_only = selected_dir == "project-only"

    if is_project_only:
        dir_label = "Project-only config"
        commands_note = "Only commands found in this project-local .claude directory"
        mcp_html = render_mcp(mc, show_usage=False, empty_message="No project-only MCP servers found.")
        skills_html = render_skills(sk, show_usage=False)
        commands_html = render_commands(co)
        hooks_html = (
            render_hooks(ho)
            if ho
            else '<div style="color:var(--text-t);font-size:14px;padding:32px;text-align:center">No project-only hooks found.</div>'
        )
        rules_html = (
            '<p style="font-size:12px;color:var(--text-t);margin-bottom:12px">Click filename to open in default app</p>'
            + f'<div class="grid grid-cols-1 md:grid-cols-2 gap-4">{render_rules(ru)}</div>'
            if ru
            else '<div style="color:var(--text-t);font-size:14px;padding:32px;text-align:center">No project-only rules found.</div>'
        )
        project_empty = not mc and not sk and not co and not ho and not ru
        project_intro = (
            '<div style="background:rgba(201,100,66,.07);border:1px solid rgba(201,100,66,.18);border-radius:8px;padding:14px 18px;margin-bottom:20px">'
            '<p style="font-weight:500;color:var(--text-p);font-size:14px">Only in this project</p>'
            '<p style="font-size:12px;color:var(--text-s);margin-top:4px">This view compares the current project\'s <code>.claude</code> with <code>~/.claude</code> and shows only project-specific MCP servers, skills, commands, hooks, and rules.</p>'
            "</div>"
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
        rules_html = (
            '<p style="font-size:12px;color:var(--text-t);margin-bottom:12px">Click filename to open in default app</p>'
            + f'<div class="grid grid-cols-1 md:grid-cols-2 gap-4">{render_rules(ru)}</div>'
        )
        project_intro = _home_hero(
            data.get("measured") or {},
            [
                (len(ag), len(ag) - agents_never, "agents"),
                (len(sk), len(sk) - skills_never, "skills"),
                (len(mc), len(mc) - mcp_never, "MCP servers"),
            ],
            data.get("window_start", ""),
        )

    dir_sel = _dir_selector(selected_dir)
    nav_stats = (
        _stats_header(
            [
                (len(sk), "Skills", 0),
                (len(co), "Commands", 0),
                (len(mc), "MCP", 0),
                (len(ho), "Hooks", 0),
                (sum(len(rule.get("files", [])) for rule in ru), "Rules", 0),
            ]
        )
        if is_project_only
        else _stats_header(
            [
                (len(p), "Plugins", 0),
                (len(ag), "Agents", agents_never),
                (len(sk), "Skills", skills_never),
                (len(co), "Commands", 0),
                (len(ho), "Hooks", 0),
                (len(mc), "MCP", mcp_never),
            ]
        )
    )
    pre_tabs_html = ""
    post_tabs_html = ""
    if not is_project_only:
        pre_tabs_html = f"""<div id="tab-plugins" class="tab-content">
  <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{render_plugins(p)}</div>
</div>

<div id="tab-agents" class="tab-content">
  <p style="font-size:12px;color:var(--text-t);margin-bottom:12px">{len(ag)} agents · {n_cats} categories · Click name to open
  {' · <span style="color:#b53333;font-weight:500">' + str(agents_never) + " never used</span>" if agents_never else ""}
  </p>
  {render_agents(ag)}
</div>"""
        post_tabs_html = f"""<div id="tab-hooks" class="tab-content">
  <p style="font-size:12px;color:var(--text-t);margin-bottom:12px">Click command to open script file</p>
  <div class="space-y-3">{hooks_html}</div>
</div>

<div id="tab-rules" class="tab-content">
  {rules_html}
</div>

<div id="tab-tax" class="tab-content">
  {render_context_tax(compute_context_tax(claude_dir, data))}
</div>

<div id="tab-cleanup" class="tab-content">
  {render_cleanup(ag, sk, mc)}
</div>"""
    else:
        post_tabs_html = f"""<div id="tab-hooks" class="tab-content">
  <p style="font-size:12px;color:var(--text-t);margin-bottom:12px">Only hooks found in this project-local .claude directory</p>
  <div class="space-y-3">{hooks_html}</div>
</div>

<div id="tab-rules" class="tab-content">
  {rules_html}
</div>"""

    config_dir_block = (
        f'<div style="display:flex;flex-direction:column;gap:2px;align-items:flex-end">'
        f'<span style="font-size:10px;color:rgba(255,255,255,.38);text-transform:uppercase;letter-spacing:.06em">Config dir</span>'
        f"{dir_sel}</div>"
        if dir_sel
        else ""
    )

    app_js = Template(_template_text("app.js")).substitute(
        has_character=str(paths.CHARACTER_IMG.exists()).lower(),
        token=security.SESSION_TOKEN,
    )

    return Template(_template_text("dashboard.html")).substitute(
        title_label=_e(dir_label),
        dir_label=_e(dir_label),
        now=now,
        nav_stats=nav_stats,
        config_dir_block=config_dir_block,
        tab_btns=_tab_btns(selected_dir),
        project_intro=project_intro,
        pre_tabs_html=pre_tabs_html,
        skills_html=skills_html,
        commands_note=commands_note,
        commands_html=commands_html,
        mcp_html=mcp_html,
        post_tabs_html=post_tabs_html,
        styles=_template_text("styles.css"),
        app_js=app_js,
    )
