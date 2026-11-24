"""ops-tools MCP server (mcp/ops_tools.py): gating by configuration, read-only mode, and the
OpenProject / Jenkins calls against a local fake server."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "mcp" / "ops_tools.py"
SEEN: list[tuple[str, str, bytes, str]] = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json", headers=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _route(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        SEEN.append((method, self.path, self.rfile.read(n), self.headers.get("Authorization", "")))
        p = self.path
        if p == "/api/v3/work_packages/7":
            return self._send(
                {
                    "id": 7,
                    "subject": "Fake",
                    "lockVersion": 3,
                    "updatedAt": "t",
                    "description": {"raw": "desc"},
                    "_links": {"status": {"title": "New"}, "type": {"title": "Task"}},
                }
            )
        if p.startswith("/api/v3/statuses"):
            els = [{"name": "Closed", "_links": {"self": {"href": "/api/v3/statuses/12"}}}]
            return self._send({"_embedded": {"elements": els}})
        if p.startswith("/job/a/job/b/lastBuild/consoleText"):
            return self._send(b"l1\nl2\nl3\n", "text/plain")
        if p.startswith("/job/a/job/b/buildWithParameters"):
            return self._send(b"", headers={"Location": "https://j/queue/item/9/"})
        if p.startswith("/api/v3/work_packages/7"):
            return self._send({})
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PATCH(self):
        self._route("PATCH")


@pytest.fixture
def fake():
    SEEN.clear()
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def rpc(calls, env_extra, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENPROJECT_", "JENKINS_"))}
    env.update({"HOME": str(tmp_path), "OPS_ENV_FILE": str(tmp_path / "none.env"), **env_extra})
    msgs = [{"jsonrpc": "2.0", "id": i + 1, **c} for i, c in enumerate(calls)]
    out = subprocess.run(
        [sys.executable, str(SERVER)],
        input="\n".join(map(json.dumps, msgs)) + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return [json.loads(line) for line in out.stdout.splitlines()]


def call(tool, **args):
    return {"method": "tools/call", "params": {"name": tool, "arguments": args}}


def names(reply):
    return {t["name"] for t in reply["result"]["tools"]}


def test_nothing_configured_offers_no_tools(tmp_path):
    reply = rpc([{"method": "tools/list"}], {}, tmp_path)
    assert names(reply[0]) == set()


def test_backend_gating_and_read_only(tmp_path):
    op = {"OPENPROJECT_URL": "http://x", "OPENPROJECT_API_KEY": "k"}
    only_op = names(rpc([{"method": "tools/list"}], op, tmp_path)[0])
    assert "op_show" in only_op and "op_comment" in only_op
    assert not any(n.startswith("jenkins_") for n in only_op)
    both = {**op, "JENKINS_URL": "http://j", "JENKINS_USER": "u", "JENKINS_API_TOKEN": "t"}
    ro = names(rpc([{"method": "tools/list"}], {**both, "OPS_READ_ONLY": "1"}, tmp_path)[0])
    assert "jenkins_log" in ro and "op_show" in ro
    assert not ro & {"op_comment", "op_status", "jenkins_build", "jenkins_setconfig"}


def test_openproject_read_and_write(fake, tmp_path):
    env = {"OPENPROJECT_URL": fake, "OPENPROJECT_API_KEY": "secret", "OPENPROJECT_PROJECT": "p"}
    replies = rpc([call("op_show", id=7), call("op_status", id=7, name="Closed")], env, tmp_path)
    assert "#7 Fake" in replies[0]["result"]["content"][0]["text"]
    assert replies[1]["result"]["isError"] is False
    auth = base64.b64encode(b"apikey:secret").decode()
    assert all(a == f"Basic {auth}" for *_, a in SEEN)
    patch = next(s for s in SEEN if s[0] == "PATCH")
    body = json.loads(patch[2])
    assert body["lockVersion"] == 3
    assert body["_links"]["status"]["href"] == "/api/v3/statuses/12"


def test_unknown_status_lists_options(fake, tmp_path):
    env = {"OPENPROJECT_URL": fake, "OPENPROJECT_API_KEY": "k"}
    reply = rpc([call("op_status", id=7, name="Nope")], env, tmp_path)[0]
    assert reply["result"]["isError"] is True
    assert "Options: Closed" in reply["result"]["content"][0]["text"]


def test_jenkins_log_build_and_errors(fake, tmp_path):
    env = {"JENKINS_URL": fake, "JENKINS_USER": "u", "JENKINS_API_TOKEN": "t"}
    replies = rpc(
        [
            call("jenkins_log", job="a/b", lines=2),
            call("jenkins_build", job="a/b", params={"X": "1"}),
            call("jenkins_info", job="missing"),
        ],
        env,
        tmp_path,
    )
    assert replies[0]["result"]["content"][0]["text"] == "l2\nl3"
    assert "queue/item/9" in replies[1]["result"]["content"][0]["text"]
    assert replies[2]["result"]["isError"] is True
    assert "HTTP 404" in replies[2]["result"]["content"][0]["text"]
    post = next(s for s in SEEN if "buildWithParameters" in s[1])
    assert post[2] == b"X=1"


def test_unconfigured_tool_call_is_an_error(tmp_path):
    reply = rpc([call("jenkins_log", job="a")], {}, tmp_path)[0]
    assert reply["result"]["isError"] is True


def test_env_file_is_read(fake, tmp_path):
    f = tmp_path / "ops.env"
    f.write_text(f"OPENPROJECT_URL={fake}\nOPENPROJECT_API_KEY=k\n")
    reply = rpc([{"method": "tools/list"}], {"OPS_ENV_FILE": str(f)}, tmp_path)[0]
    assert "op_show" in names(reply)
