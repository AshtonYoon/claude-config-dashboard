"""Plain-text CLI report: a verdict, not a browse.

Renders the same measured/estimated data as the web dashboard's Context Tax
and Cleanup tabs, but as a single stdout block for --report and the
/config-tax:tax slash command — no server, no browser.
"""

from datetime import datetime

from .usage import STALE_DAYS, is_stale_item


def _gap_line(label: str, items: list, width: int) -> str:
    total = len(items)
    idle = sum(1 for i in items if is_stale_item(i, STALE_DAYS))
    used = total - idle
    idle_note = f"{idle} idle" if idle else "none idle"
    return f"    {label:<{width}} {used}/{total} used      {idle_note}"


def build_report(data: dict, tax: dict) -> str:
    measured = tax.get("measured") or {}
    window_start = tax.get("window_start", "")[:10]
    lines = ["Claude Config Report"]

    if measured.get("count"):
        median, mn, mx, cnt = measured["median"], measured["min"], measured["max"], measured["count"]
        sess = "session" if cnt == 1 else "sessions"
        lines.append(f"measured from your last {cnt} {sess}" + (f" (since {window_start})" if window_start else ""))
        lines.append("")
        baseline = max(median - tax["total_tokens"], 0)
        lines.append(f"  Every session starts with     {median:>7,} tokens   (range {mn:,}-{mx:,})")
        lines.append(f"    Claude Code baseline        {baseline:>7,}   system prompt + MCP schemas, not cuttable")
        lines.append(f"    Your config                 {tax['total_tokens']:>7,}   <- this is what you control")
    else:
        lines.append("not enough session history yet to measure real cost")
        lines.append("")
        lines.append(f"  Estimated config cost (chars/4)   ~{tax['total_tokens']:,} tokens")

    lines.append("")
    lines.append(f"  Installed vs. actually used (last {STALE_DAYS} days)")
    lines.append(_gap_line("Agents", data.get("agents", []), 7))
    lines.append(_gap_line("Skills", data.get("skills", []), 7))
    lines.append(_gap_line("MCP", data.get("mcp_servers", []), 7))

    reclaimable = tax.get("reclaimable_items") or []
    if reclaimable:
        lines.append("")
        lines.append("  Heaviest idle items")
        for i, item in enumerate(reclaimable[:5], 1):
            status = "never used" if not item.get("last_used") else f"stale, unused {STALE_DAYS}+ days"
            lines.append(f"    {i}. {item['name']} ({item['kind']})   ~{item['tokens']:,} tok   {status}")

    lines.append("")
    reclaimable_tokens = tax.get("reclaimable_tokens", 0)
    if reclaimable_tokens:
        pct = round(reclaimable_tokens / max(tax["total_tokens"], 1) * 100)
        lines.append(f"  Verdict: ~{pct}% of your config (~{reclaimable_tokens:,} tok) is dead weight.")
        lines.append("  -> claude-config-dashboard --report --clean   to generate an archive script")
        lines.append("     (mv-only, never deletes -- review before running)")
    else:
        lines.append("  Verdict: nothing looks idle -- your config is lean.")

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
