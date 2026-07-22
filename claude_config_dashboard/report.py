"""Plain-text CLI report: a verdict, not a browse.

Renders the same measured/estimated data as the web dashboard's Context Tax
and Cleanup tabs, but as a single stdout block for --report and the
/config-tax:tax slash command — no server, no browser.
"""

from datetime import datetime

from . import ansi
from .usage import STALE_DAYS, is_stale_item


def _gap_line(label: str, items: list, width: int, color: bool) -> str:
    total = len(items)
    idle = sum(1 for i in items if is_stale_item(i, STALE_DAYS))
    used = total - idle
    if idle:
        idle_note = ansi.color(f"{idle} idle", ansi.BAD, enabled=color)
    else:
        idle_note = ansi.color("none idle", ansi.GOOD, enabled=color)
    return f"    {label:<{width}} {used}/{total} used      {idle_note}"


def build_report(data: dict, tax: dict, color: bool = False) -> str:
    measured = tax.get("measured") or {}
    window_start = tax.get("window_start", "")[:10]
    lines = [ansi.gradient_text("Claude Config Report", ansi.BRAND, ansi.BRAND_DEEP, bold=True, enabled=color)]

    if measured.get("count"):
        median, mn, mx, cnt = measured["median"], measured["min"], measured["max"], measured["count"]
        sess = "session" if cnt == 1 else "sessions"
        lines.append(f"measured from your last {cnt} {sess}" + (f" (since {window_start})" if window_start else ""))
        lines.append("")
        total = tax["total_tokens"]
        baseline = max(median - total, 0)
        median_s = ansi.style(f"{median:>7,}", ansi.BOLD, enabled=color)
        lines.append(f"  Every session starts with     {median_s} tokens   (range {mn:,}-{mx:,})")
        if color and median:
            bar = ansi.gradient_bar(total / median, 30, ansi.BRAND, ansi.BRAND_DEEP, enabled=True)
            lines.append(f"  {bar}  {ansi.style('your config', ansi.DIM, enabled=True)} vs baseline")
        baseline_s = ansi.color(f"{baseline:>7,}", ansi.NEUTRAL, enabled=color)
        lines.append(f"    Claude Code baseline        {baseline_s}   system prompt + MCP schemas, not cuttable")
        config_s = ansi.color(f"{total:>7,}", ansi.BRAND, bold=True, enabled=color)
        lines.append(f"    Your config                 {config_s}   <- this is what you control")
    else:
        lines.append("not enough session history yet to measure real cost")
        lines.append("")
        lines.append(f"  Estimated config cost (chars/4)   ~{tax['total_tokens']:,} tokens")

    lines.append("")
    lines.append(ansi.style(f"  Installed vs. actually used (last {STALE_DAYS} days)", ansi.BOLD, enabled=color))
    lines.append(_gap_line("Agents", data.get("agents", []), 7, color))
    lines.append(_gap_line("Skills", data.get("skills", []), 7, color))
    lines.append(_gap_line("MCP", data.get("mcp_servers", []), 7, color))

    reclaimable = tax.get("reclaimable_items") or []
    if reclaimable:
        lines.append("")
        lines.append(ansi.style("  Heaviest idle items", ansi.BOLD, enabled=color))
        for i, item in enumerate(reclaimable[:5], 1):
            status = "never used" if not item.get("last_used") else f"stale, unused {STALE_DAYS}+ days"
            status_s = ansi.color(status, ansi.BAD, enabled=color)
            tok_s = ansi.color(f"~{item['tokens']:,} tok", ansi.NEUTRAL, enabled=color)
            lines.append(f"    {i}. {item['name']} ({item['kind']})   {tok_s}   {status_s}")

    lines.append("")
    reclaimable_tokens = tax.get("reclaimable_tokens", 0)
    if reclaimable_tokens:
        pct = round(reclaimable_tokens / max(tax["total_tokens"], 1) * 100)
        verdict = ansi.color(
            f"~{pct}% of your config (~{reclaimable_tokens:,} tok) is dead weight.",
            ansi.verdict_color(pct),
            bold=True,
            enabled=color,
        )
        lines.append(f"  Verdict: {verdict}")
        hint = ansi.style(
            "-> claude-config-dashboard --report --clean   to generate an archive script", ansi.DIM, enabled=color
        )
        lines.append(f"  {hint}")
        lines.append(ansi.style("     (mv-only, never deletes -- review before running)", ansi.DIM, enabled=color))
    else:
        lines.append(ansi.color("  Verdict: nothing looks idle -- your config is lean.", ansi.GOOD, enabled=color))

    return "\n".join(lines)


def _sh_quote(s: str) -> str:
    return str(s).replace('"', '\\"')


def build_cleanup_script(tax: dict) -> str:
    """POSIX shell script that archives reclaimable items. Mirrors app.js's
    buildCleanupScript() so the CLI and the browser download produce the
    same plan from the same tax["reclaimable_items"] data."""
    lines = [
        "#!/bin/sh",
        "# Claude Config Dashboard -- cleanup plan",
        f"# Generated {datetime.now().strftime('%Y-%m-%d')}",
        "#",
        "# This does NOT delete anything -- it archives items unused for 30+ days",
        "# into a dated folder so you can review before removing them for good.",
        "# Read every line before running. Undo: move files back out of the archive folder.",
        "",
        "set -e",
        f'ARCHIVE="{_sh_quote(tax["archive_dir"])}"',
        'mkdir -p "$ARCHIVE"',
        "",
    ]
    skipped = []
    for item in tax.get("reclaimable_items") or []:
        source = item.get("archive_source")
        if source:
            lines.append(f'# {item["kind"]}: {item["name"]} (~{item["tokens"]} tokens/session)')
            src = _sh_quote(source)
            lines.append(f'[ -e "{src}" ] && mv -n "{src}" "$ARCHIVE/"')
            lines.append("")
        else:
            skipped.append(item)
    if skipped:
        lines.append("# SKIPPED -- needs manual review:")
        for item in skipped:
            lines.append(f'#   {item["name"]} -- {item.get("skip_reason", "")}')
    return "\n".join(lines) + "\n"
