"""HTTP handler tests: routes and the security regressions for /open and /stop."""

import http.client
import io
import json
import sys
import threading
from http.server import HTTPServer

import pytest

from claude_config_dashboard import collectors, security, server


@pytest.fixture
def dash_server(claude_env, monkeypatch):
    """Threaded dashboard server on an ephemeral port, scanning the fixture tree."""
    monkeypatch.setattr(server, "HOME_CLAUDE", claude_env.claude)
    home_data = collectors.scan_dir(claude_env.claude)
    all_data = {"home": (home_data, claude_env.claude)}
    server_ref = [None]
    srv = HTTPServer(("localhost", 0), server.make_handler(all_data, server_ref))
    server_ref[0] = srv
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    thread.join(timeout=5)


def _request(srv, method, path, headers=None):
    conn = http.client.HTTPConnection("localhost", srv.server_port, timeout=5)
    try:
        conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


class TestRoutes:
    def test_dashboard_page(self, dash_server):
        status, body = _request(dash_server, "GET", "/")
        assert status == 200
        text = body.decode()
        assert "Claude Config Dashboard" in text
        assert "my-skill" in text

    def test_unknown_dir_falls_back_to_home(self, dash_server):
        status, body = _request(dash_server, "GET", "/?dir=project-only")
        assert status == 200
        assert "btn-plugins" in body.decode()  # home view, not project-only

    def test_character_image(self, dash_server):
        status, body = _request(dash_server, "GET", "/character")
        assert status == 200
        assert body[:8] == b"\x89PNG\r\n\x1a\n"

    def test_unknown_path_404(self, dash_server):
        status, _ = _request(dash_server, "GET", "/nope")
        assert status == 404
        status, _ = _request(dash_server, "POST", "/nope")
        assert status == 404


class TestOpenEndpointSecurity:
    def test_get_is_405(self, dash_server):
        status, _ = _request(dash_server, "GET", "/open?path=/etc/hosts")
        assert status == 405
        status, _ = _request(dash_server, "GET", "/stop")
        assert status == 405

    def test_post_without_token_403(self, dash_server):
        status, _ = _request(dash_server, "POST", "/open?path=/etc/hosts")
        assert status == 403

    def test_post_wrong_token_403(self, dash_server):
        status, _ = _request(dash_server, "POST", "/open?path=/etc/hosts", {"X-Dashboard-Token": "wrong"})
        assert status == 403

    def test_valid_token_non_allowlisted_path_403(self, dash_server):
        status, _ = _request(
            dash_server, "POST", "/open?path=/etc/hosts", {"X-Dashboard-Token": security.SESSION_TOKEN}
        )
        assert status == 403

    def test_foreign_host_header_403(self, claude_env, dash_server):
        _request(dash_server, "GET", "/")  # render → allowlist populated
        agent_md = str(claude_env.claude / "agents" / "test-runner.md")
        status, _ = _request(
            dash_server,
            "POST",
            f"/open?path={agent_md}",
            {"X-Dashboard-Token": security.SESSION_TOKEN, "Host": "evil.example.com"},
        )
        assert status == 403

    def test_valid_token_allowlisted_path_opens(self, claude_env, dash_server, monkeypatch):
        _request(dash_server, "GET", "/")  # render → allowlist populated
        opened = []
        monkeypatch.setattr(server.subprocess, "run", lambda cmd, check: opened.append(cmd))
        if not (sys.platform == "darwin" or sys.platform.startswith("linux")):
            monkeypatch.setattr(server.os, "startfile", lambda p: opened.append(["startfile", p]), raising=False)
        agent_md = str(claude_env.claude / "agents" / "test-runner.md")
        status, body = _request(
            dash_server, "POST", f"/open?path={agent_md}", {"X-Dashboard-Token": security.SESSION_TOKEN}
        )
        assert status == 200
        assert body == b"ok"
        assert len(opened) == 1
        assert opened[0][-1] == agent_md


class TestStopEndpoint:
    def test_stop_requires_token(self, dash_server):
        status, _ = _request(dash_server, "POST", "/stop")
        assert status == 403

    def test_stop_with_token_shuts_down(self, dash_server):
        status, body = _request(dash_server, "POST", "/stop", {"X-Dashboard-Token": security.SESSION_TOKEN})
        assert status == 200
        assert body == b"stopping"


class TestMain:
    def test_main_scans_and_serves(self, claude_env, monkeypatch, capsys):
        monkeypatch.setattr(server, "HOME_CLAUDE", claude_env.claude)
        monkeypatch.setattr(server, "CWD_CLAUDE", None)
        monkeypatch.setattr(HTTPServer, "serve_forever", lambda self: None)
        monkeypatch.setattr(sys, "argv", ["claude-config-dashboard", "--no-open", "--port", "0"])
        server.main()
        out = capsys.readouterr().out
        assert "Scanning" in out
        assert "Dashboard →" in out
        assert "Server stopped" in out

    def test_main_scans_project_dir(self, claude_env, monkeypatch, capsys):
        project_claude = claude_env.home / "proj" / ".claude"
        (project_claude / "commands").mkdir(parents=True)
        (project_claude / "commands" / "local.md").write_text("Local command.\n")
        monkeypatch.setattr(server, "HOME_CLAUDE", claude_env.claude)
        monkeypatch.setattr(server, "CWD_CLAUDE", project_claude)
        monkeypatch.setattr(HTTPServer, "serve_forever", lambda self: None)
        monkeypatch.setattr(sys, "argv", ["claude-config-dashboard", "--no-open", "--port", "0"])
        server.main()
        out = capsys.readouterr().out
        assert out.count("Scanning") == 2

    def test_main_report_prints_verdict_and_no_server(self, claude_env, monkeypatch, capsys):
        monkeypatch.setattr(server, "HOME_CLAUDE", claude_env.claude)
        monkeypatch.setattr(sys, "argv", ["claude-config-dashboard", "--report"])
        server.main()
        out = capsys.readouterr().out
        assert "Claude Config Report" in out
        assert "Scanning" not in out
        assert "Dashboard →" not in out

    def test_main_report_clean_appends_script(self, claude_env, monkeypatch, capsys):
        monkeypatch.setattr(server, "HOME_CLAUDE", claude_env.claude)
        monkeypatch.setattr(sys, "argv", ["claude-config-dashboard", "--report", "--clean"])
        server.main()
        out = capsys.readouterr().out
        assert "Claude Config Report" in out
        assert "#!/bin/sh" in out
        assert "mv -n" in out

    def test_main_statusline_reads_stdin_and_prints_one_line(self, claude_env, monkeypatch, capsys):
        monkeypatch.setattr(server, "HOME_CLAUDE", claude_env.claude)
        payload = json.dumps({"transcript_path": str(claude_env.transcript)})
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        monkeypatch.setattr(sys, "argv", ["claude-config-dashboard", "--statusline"])
        server.main()
        out = capsys.readouterr().out.strip()
        assert "\n" not in out  # one line, safe for a statusline
        # fixture transcripts carry no usage block, so tokens are 0, but idle
        # items (single-file skill, json-mcp) should still be reported
        assert "idle" in out

    def test_main_statusline_handles_missing_stdin_gracefully(self, claude_env, monkeypatch, capsys):
        monkeypatch.setattr(server, "HOME_CLAUDE", claude_env.claude)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        monkeypatch.setattr(sys, "argv", ["claude-config-dashboard", "--statusline"])
        server.main()
        out = capsys.readouterr().out.strip()
        assert out  # never blank/crashes even with no payload
