"""HTTP server: routes, request authorization, and the CLI entry point."""

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import security
from .collectors import scan_dir
from .context_tax import compute_context_tax
from .enrich import build_project_only_data, enrich_data
from .paths import CHARACTER_IMG, CWD_CLAUDE, HOME_CLAUDE, PORT_DEFAULT
from .render import build_html
from .report import build_cleanup_script, build_report
from .usage import get_cached_usage, get_disk_cached_usage, is_stale_item, measure_transcript_start


def make_handler(all_data: dict, server_ref: list):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)

            if parsed.path == "/character":
                if CHARACTER_IMG.exists():
                    img_data = CHARACTER_IMG.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "no-cache, no-store")
                    self.send_header("Content-Length", str(len(img_data)))
                    self.end_headers()
                    self.wfile.write(img_data)
                else:
                    self._respond(404, b"not found", "text/plain")

            elif parsed.path in ("/open", "/stop"):
                self._respond(405, b"method not allowed", "text/plain")

            elif parsed.path in ("/", "/index.html"):
                qs = urllib.parse.parse_qs(parsed.query)
                selected_dir = qs.get("dir", ["home"])[0]
                if selected_dir not in all_data:
                    selected_dir = "home"
                # Usage stats always come from ~/.claude (that's where logs live)
                usage = get_cached_usage(HOME_CLAUDE, "*")
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

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)

            if parsed.path not in ("/open", "/stop"):
                self._respond(404, b"not found", "text/plain")
                return
            token = self.headers.get("X-Dashboard-Token", "")
            if not security.is_authorized(self.headers.get("Host", ""), token):
                self._respond(403, b"forbidden", "text/plain")
                return

            if parsed.path == "/open":
                qs = urllib.parse.parse_qs(parsed.query)
                path = qs.get("path", [""])[0]
                if not security.is_openable(path):
                    self._respond(403, b"forbidden", "text/plain")
                    return
                resolved = str(Path(path).expanduser().resolve())
                if sys.platform == "darwin":
                    subprocess.run(["open", resolved], check=False)
                elif sys.platform.startswith("linux"):
                    subprocess.run(["xdg-open", resolved], check=False)
                else:
                    os.startfile(resolved)
                self._respond(200, b"ok", "text/plain")

            elif parsed.path == "/stop":
                self._respond(200, b"stopping", "text/plain")
                threading.Thread(target=server_ref[0].shutdown, daemon=True).start()

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
                    path = urllib.parse.parse_qs(urllib.parse.urlparse(args[0].split()[1]).query).get("path", [""])[0]
                except (IndexError, ValueError, AttributeError):
                    pass
                print(f"  open: {path}")

    return Handler


def _build_report_data(claude_dir: Path) -> tuple:
    raw = scan_dir(claude_dir)
    stats = get_cached_usage(claude_dir, "*")
    data = enrich_data(raw, stats)
    tax = compute_context_tax(claude_dir, data)
    return data, tax


def _run_statusline(claude_dir: Path) -> None:
    """Print one line for a Claude Code statusLine command: this session's
    measured start-of-context cost, plus how many installed items sit idle.
    Reads the statusline JSON payload (with transcript_path) from stdin."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        payload = {}

    transcript_path = payload.get("transcript_path", "")
    tokens = measure_transcript_start(transcript_path) if transcript_path else 0

    raw = scan_dir(claude_dir)
    stats = get_disk_cached_usage(claude_dir)
    data = enrich_data(raw, stats)
    idle = sum(
        1
        for bucket in (data["agents"], data["skills"], data["mcp_servers"])
        for item in bucket
        if is_stale_item(item)
    )

    parts = []
    if tokens:
        parts.append(f"⚙ {tokens // 1000}k start" if tokens >= 1000 else f"⚙ {tokens} start")
    if idle:
        parts.append(f"{idle} idle")
    print(" · ".join(parts) if parts else "⚙ config-tax: no data yet")


def main():
    parser = argparse.ArgumentParser(description="Claude Config Dashboard")
    parser.add_argument("--port", type=int, default=PORT_DEFAULT)
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open browser")
    parser.add_argument("--verbose", action="store_true", help="Log skipped/unreadable config files to stderr")
    parser.add_argument(
        "--report", action="store_true", help="Print a plain-text verdict to stdout and exit (no server)"
    )
    parser.add_argument(
        "--clean", action="store_true", help="With --report: also print an archive script for idle items"
    )
    parser.add_argument(
        "--statusline", action="store_true", help="Read Claude Code statusline JSON from stdin, print one line"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.statusline:
        _run_statusline(HOME_CLAUDE)
        return

    if args.report:
        data, tax = _build_report_data(HOME_CLAUDE)
        print(build_report(data, tax))
        if args.clean:
            if tax["reclaimable_items"]:
                print()
                print(build_cleanup_script(tax))
            else:
                print("\n# Nothing to clean -- no items idle 30+ days.", file=sys.stderr)
        return

    print(f"Scanning {HOME_CLAUDE} ...")
    home_data = scan_dir(HOME_CLAUDE)
    for k, v in home_data.items():
        if isinstance(v, list):
            print(f"  {k:<12}: {len(v)}")

    all_data: dict = {"home": (home_data, HOME_CLAUDE)}

    if CWD_CLAUDE is not None:
        cwd_label = "~/" + str(CWD_CLAUDE.relative_to(Path.home()))
        print(f"\nScanning {cwd_label} ...")
        cwd_data = scan_dir(CWD_CLAUDE)
        for k, v in cwd_data.items():
            if isinstance(v, list):
                print(f"  {k:<12}: {len(v)}")
        all_data["project-only"] = (cwd_data, CWD_CLAUDE)

    print("\n  Pre-computing usage stats ...")
    get_cached_usage(HOME_CLAUDE, "*")

    initial_url = f"http://localhost:{args.port}"
    server_ref: list = [None]
    server = HTTPServer(("localhost", args.port), make_handler(all_data, server_ref))
    server_ref[0] = server
    print(f"Dashboard → {initial_url}")
    print("Click any filename to open in default app. Stop: Ctrl+C or the Stop button\n")

    if not args.no_open:
        threading.Thread(target=lambda: webbrowser.open(initial_url), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    print("Server stopped")
