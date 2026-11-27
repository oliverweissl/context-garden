#!/usr/bin/env python3
"""MCP server (stdio) for OpenProject (v3 REST API) and Jenkins: typed tool calls instead
of browser automation or hand-quoted curl. OPTIONAL: not part of the default `.mcp.json`;
see mcp/OPS_TOOLS.md for how to enable it.

Configuration comes from the process environment, then from an env file
(`OPS_ENV_FILE`, default `~/.config/context-garden/ops.env`). A backend with missing
settings offers no tools. `OPS_READ_ONLY=1` hides every tool that writes.

    OPENPROJECT_URL / OPENPROJECT_API_KEY / OPENPROJECT_PROJECT
    JENKINS_URL / JENKINS_USER / JENKINS_API_TOKEN

The same file is also a command-line tool: `ops_tools.py op show 123`,
`ops_tools.py jk log some/job lastFailedBuild`. Standard library only.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROTOCOL = "2025-06-18"
SERVER = {"name": "ops-tools", "version": "0.1.0"}
MAX_OUT = 60_000
TIMEOUT = 60


class OpsError(Exception):
    pass


# ---------------------------------------------------------------- configuration


def load_env_file() -> None:
    path = Path(os.environ.get("OPS_ENV_FILE") or "~/.config/context-garden/ops.env").expanduser()
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _ssl_context() -> ssl.SSLContext:
    # python.org builds on macOS ship without system CAs; verification stays on.
    for f in (os.environ.get("SSL_CERT_FILE"), "/etc/ssl/cert.pem"):
        if f and os.path.exists(f):
            return ssl.create_default_context(cafile=f)
    return ssl.create_default_context()


def _basic(user: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()


def _http(method, url, headers, data=None, parse=True, with_headers=False):
    if isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context()) as resp:
            body = resp.read()
            if with_headers:
                return {k.lower(): v for k, v in resp.headers.items()}
            if not parse:
                return body.decode("utf-8", "replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise OpsError(f"HTTP {e.code} {method} {url}: {e.read().decode('utf-8', 'replace')[:800]}")
    except urllib.error.URLError as e:
        raise OpsError(f"connection error {method} {url}: {e.reason}")


def _cut(text: str) -> str:
    if len(text) <= MAX_OUT:
        return text
    return text[:MAX_OUT] + f"\n... cut at {MAX_OUT} characters"


# ---------------------------------------------------------------- OpenProject


class OpenProject:
    def __init__(self) -> None:
        self.base = (os.environ.get("OPENPROJECT_URL") or "").rstrip("/")
        self.key = os.environ.get("OPENPROJECT_API_KEY") or ""
        self.project = os.environ.get("OPENPROJECT_PROJECT") or ""

    def configured(self) -> bool:
        return bool(self.base and self.key)

    def call(self, method, path, body=None):
        headers = {
            "Authorization": _basic("apikey", self.key),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        data = json.dumps(body) if body is not None else None
        return _http(method, self.base + path, headers, data)

    def _wp(self, wid):
        return self.call("GET", f"/api/v3/work_packages/{int(wid)}")

    def _named(self, path, name):
        els = self.call("GET", path)["_embedded"]["elements"]
        for e in els:
            if e.get("name", "").lower() == name.lower():
                return e["_links"]["self"]["href"]
        raise OpsError(
            f"not found: {name!r}. Options: {', '.join(e.get('name', '?') for e in els)}"
        )

    def _patch(self, wid, fields):
        fields["lockVersion"] = self._wp(wid)["lockVersion"]
        self.call("PATCH", f"/api/v3/work_packages/{int(wid)}", fields)
        return "ok"

    def _project(self):
        if not self.project:
            raise OpsError("OPENPROJECT_PROJECT is not set")
        return self.project

    def show(self, id):
        w = self._wp(id)
        links = w["_links"]
        head = (
            f"#{w['id']} {w['subject']}\nstatus: {links['status']['title']} | assignee: "
            f"{links.get('assignee', {}).get('title')} | version: "
            f"{links.get('version', {}).get('title')} | type: {links['type']['title']}\n"
            f"updated: {w['updatedAt']}\n\n"
        )
        return head + (w.get("description") or {}).get("raw", "")

    def comments(self, id, last=10):
        path = f"/api/v3/work_packages/{int(id)}/activities?pageSize=200"
        acts = self.call("GET", path)["_embedded"]["elements"]
        out = []
        for a in acts[-int(last) :]:
            who = a["_links"]["user"].get("title", "?")
            text = (a.get("comment") or {}).get("raw", "")
            details = "\n".join(d.get("raw", "") for d in a.get("details", []))
            out.append(f"--- {a['createdAt']} {who}\n{text}{details}")
        return "\n".join(out)

    def _rows(self, path):
        els = self.call("GET", path)["_embedded"]["elements"]
        return "\n".join(
            f"{k['id']} | {k['_links']['status']['title']} | {k['subject']}" for k in els
        )

    def children(self, id):
        f = json.dumps([{"parent": {"operator": "=", "values": [str(int(id))]}}])
        return self._rows("/api/v3/work_packages?pageSize=100&filters=" + urllib.parse.quote(f))

    def search(self, text):
        f = json.dumps([{"subject": {"operator": "~", "values": [text]}}])
        path = f"/api/v3/projects/{self._project()}/work_packages?pageSize=50&filters="
        return self._rows(path + urllib.parse.quote(f))

    def comment(self, id, text):
        path = f"/api/v3/work_packages/{int(id)}/activities?notify=false"
        self.call("POST", path, {"comment": {"raw": text}})
        return "ok"

    def status(self, id, name):
        href = self._named("/api/v3/statuses", name)
        return self._patch(id, {"_links": {"status": {"href": href}}})

    def assign(self, id, name):
        if name.lower() == "none":
            return self._patch(id, {"_links": {"assignee": {"href": None}}})
        f = json.dumps([{"name": {"operator": "~", "values": [name]}}])
        users = self.call("GET", "/api/v3/principals?filters=" + urllib.parse.quote(f))
        users = users["_embedded"]["elements"]
        if len(users) != 1:
            raise OpsError(f"user name ambiguous or unknown: {[u['name'] for u in users]}")
        href = users[0]["_links"]["self"]["href"]
        return self._patch(id, {"_links": {"assignee": {"href": href}}})

    def describe(self, id, text):
        return self._patch(id, {"description": {"raw": text}})

    def create(self, subject, parent=None, version=None, type="Task", description=None):
        project = self._project()
        links = {"type": {"href": self._named(f"/api/v3/projects/{project}/types", type)}}
        if parent:
            links["parent"] = {"href": f"/api/v3/work_packages/{int(parent)}"}
        if version:
            links["version"] = {
                "href": self._named(f"/api/v3/projects/{project}/versions", version)
            }
        body = {"subject": subject, "_links": links}
        if description:
            body["description"] = {"raw": description}
        w = self.call("POST", f"/api/v3/projects/{project}/work_packages", body)
        return f"created #{w['id']} {w['subject']} (unassigned)"


# ---------------------------------------------------------------- Jenkins


def _job(path: str) -> str:
    parts = [s for s in path.strip("/").split("/") if s]
    return "".join("/job/" + urllib.parse.quote(s, safe="") for s in parts)


def _utc(ms) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ms / 1000))


class Jenkins:
    def __init__(self) -> None:
        self.base = (os.environ.get("JENKINS_URL") or "").rstrip("/")
        self.user = os.environ.get("JENKINS_USER") or ""
        self.token = os.environ.get("JENKINS_API_TOKEN") or ""

    def configured(self) -> bool:
        return bool(self.base and self.user and self.token)

    def call(self, method, path, data=None, ctype=None, parse=True, with_headers=False):
        headers = {"Authorization": _basic(self.user, self.token)}
        if ctype:
            headers["Content-Type"] = ctype
        return _http(method, self.base + path, headers, data, parse, with_headers)

    def jobs(self, folder=""):
        tree = "jobs[name,_class,lastBuild[number,result]]"
        d = self.call("GET", f"{_job(folder)}/api/json?tree={tree}")
        out = []
        for j in d.get("jobs", []):
            lb = j.get("lastBuild") or {}
            kind = "dir " if "Folder" in j.get("_class", "") else "job "
            result = lb.get("result") or ("RUNNING" if lb else "")
            out.append(f"{kind}{j['name']:<45} {lb.get('number', '-')} {result}")
        return "\n".join(out)

    def info(self, job):
        d = self.call("GET", f"{_job(job)}/api/json")
        out = [f"{d['fullName']} | {d.get('color')}\n{d.get('url')}"]
        for prop in d.get("property", []):
            for pd in prop.get("parameterDefinitions", []) or []:
                out.append(
                    f"param {pd['name']} = {pd.get('defaultParameterValue', {}).get('value')}"
                )
        for k in ("lastBuild", "lastSuccessfulBuild", "lastFailedBuild"):
            if d.get(k):
                out.append(f"{k}: #{d[k]['number']}")
        return "\n".join(out)

    @staticmethod
    def _cause(b):
        for a in b.get("actions", []):
            for c in a.get("causes") or []:
                return c.get("shortDescription", "")
        return ""

    def builds(self, job, last=10):
        tree = "builds[number,result,timestamp,duration,building,actions[causes[shortDescription]]]"
        d = self.call("GET", f"{_job(job)}/api/json?tree={tree}{{0,{int(last)}}}")
        out = []
        for b in d["builds"]:
            result = "RUNNING" if b["building"] else b["result"]
            out.append(
                f"#{b['number']:<5} {result:<9} {_utc(b['timestamp'])} "
                f"{b['duration'] // 1000:>5}s  {self._cause(b)}"
            )
        return "\n".join(out)

    def status(self, job, build="lastBuild"):
        b = self.call("GET", f"{_job(job)}/{build}/api/json")
        result = "RUNNING" if b["building"] else b["result"]
        out = [
            f"#{b['number']} {result} {_utc(b['timestamp'])} duration {b['duration'] // 1000}s",
            self._cause(b),
        ]
        for a in b.get("actions", []):
            for p in a.get("parameters") or []:
                out.append(f"param {p['name']}={p.get('value')}")
            if "lastBuiltRevision" in a:
                rev = a["lastBuiltRevision"]
                out.append(f"git {rev['SHA1']} {[x['name'] for x in rev.get('branch', [])]}")
        return "\n".join(out)

    def log(self, job, build="lastBuild", lines=200):
        text = self.call("GET", f"{_job(job)}/{build}/consoleText", parse=False)
        ls = text.splitlines()
        return "\n".join(ls[-int(lines) :] if int(lines) else ls)

    def build(self, job, params=None):
        if params:
            data = urllib.parse.urlencode(list(params.items()))
            h = self.call(
                "POST",
                f"{_job(job)}/buildWithParameters",
                data,
                "application/x-www-form-urlencoded",
                with_headers=True,
            )
        else:
            h = self.call("POST", f"{_job(job)}/build", with_headers=True)
        return "queued " + h.get("location", "")

    def wait(self, job, number, timeout=3600):
        deadline = time.time() + int(timeout)
        while time.time() < deadline:
            try:
                b = self.call("GET", f"{_job(job)}/{int(number)}/api/json?tree=building,result")
                if not b["building"]:
                    return b["result"]
            except OpsError as e:
                if "HTTP 404" not in str(e):  # 404: still in the queue
                    raise
            time.sleep(15)
        raise OpsError("timeout")

    def stop(self, job, build):
        self.call("POST", f"{_job(job)}/{build}/stop", parse=False)
        return "stop sent"

    def queue(self):
        items = self.call("GET", "/queue/api/json")["items"]
        return "\n".join(f"{i['id']} | {i['task']['name']} | {i.get('why')}" for i in items)

    def nodes(self):
        out = []
        for n in self.call("GET", "/computer/api/json")["computer"]:
            state = "OFFLINE" if n["offline"] else "online"
            out.append(f"{n['displayName']:<25} {state} executors={n['numExecutors']}")
        return "\n".join(out)

    def config(self, job):
        return self.call("GET", f"{_job(job)}/config.xml", parse=False)

    def setconfig(self, job, xml):
        self.call("POST", f"{_job(job)}/config.xml", xml, "application/xml", parse=False)
        return "ok"

    def artifacts(self, job, build="lastBuild"):
        b = self.call("GET", f"{_job(job)}/{build}/api/json?tree=artifacts[relativePath],url")
        return "\n".join(b["url"] + "artifact/" + a["relativePath"] for a in b["artifacts"])


# ---------------------------------------------------------------- tool table


def _s(desc):
    return {"type": "string", "description": desc}


def _i(desc):
    return {"type": "integer", "description": desc}


def _t(backend, name, desc, props, required, write, fn):
    schema = {"type": "object", "properties": props, "required": required}
    return {
        "backend": backend,
        "write": write,
        "fn": fn,
        "def": {"name": name, "description": desc, "inputSchema": schema},
    }


WP = _i("Work package number, e.g. 4705")
JOB = _s("Job path, folders joined by /, e.g. Folder/Sub/job")
BUILD = _s("Build number, or lastBuild / lastFailedBuild / lastSuccessfulBuild")


def build_tools(op: OpenProject, jk: Jenkins) -> list[dict]:
    o, j = "op", "jk"
    return [
        _t(
            o,
            "op_show",
            "OpenProject work package: subject, status, assignee, version, description.",
            {"id": WP},
            ["id"],
            False,
            lambda a: op.show(a["id"]),
        ),
        _t(
            o,
            "op_comments",
            "Last activities (comments, changes) of a work package.",
            {"id": WP, "last": _i("How many, default 10")},
            ["id"],
            False,
            lambda a: op.comments(a["id"], a.get("last", 10)),
        ),
        _t(
            o,
            "op_children",
            "Child work packages with status.",
            {"id": WP},
            ["id"],
            False,
            lambda a: op.children(a["id"]),
        ),
        _t(
            o,
            "op_search",
            "Search work packages of the configured project by subject.",
            {"text": _s("Text in the subject")},
            ["text"],
            False,
            lambda a: op.search(a["text"]),
        ),
        _t(
            o,
            "op_comment",
            "WRITES: add a comment to a work package (no notification mail).",
            {"id": WP, "text": _s("Comment, markdown")},
            ["id", "text"],
            True,
            lambda a: op.comment(a["id"], a["text"]),
        ),
        _t(
            o,
            "op_status",
            "WRITES: set the status by name, e.g. 'In progress', 'Closed'.",
            {"id": WP, "name": _s("Status name")},
            ["id", "name"],
            True,
            lambda a: op.status(a["id"], a["name"]),
        ),
        _t(
            o,
            "op_assign",
            "WRITES: set the assignee by name, or 'none'.",
            {"id": WP, "name": _s("User name or 'none'")},
            ["id", "name"],
            True,
            lambda a: op.assign(a["id"], a["name"]),
        ),
        _t(
            o,
            "op_describe",
            "WRITES: replace the description of a work package.",
            {"id": WP, "text": _s("New description, markdown")},
            ["id", "text"],
            True,
            lambda a: op.describe(a["id"], a["text"]),
        ),
        _t(
            o,
            "op_create",
            "WRITES: create a work package in the configured project, unassigned.",
            {
                "subject": _s("Subject"),
                "parent": _i("Parent work package"),
                "version": _s("Version name"),
                "type": _s("Type name, default Task"),
                "description": _s("Description, markdown"),
            },
            ["subject"],
            True,
            lambda a: op.create(
                a["subject"],
                a.get("parent"),
                a.get("version"),
                a.get("type", "Task"),
                a.get("description"),
            ),
        ),
        _t(
            j,
            "jenkins_jobs",
            "List the jobs and folders of a Jenkins folder with last result.",
            {"folder": _s("Folder path, empty for the root")},
            [],
            False,
            lambda a: jk.jobs(a.get("folder", "")),
        ),
        _t(
            j,
            "jenkins_info",
            "Job summary: parameters and last builds.",
            {"job": JOB},
            ["job"],
            False,
            lambda a: jk.info(a["job"]),
        ),
        _t(
            j,
            "jenkins_builds",
            "Recent builds of a job: number, result, time, cause.",
            {"job": JOB, "last": _i("How many, default 10")},
            ["job"],
            False,
            lambda a: jk.builds(a["job"], a.get("last", 10)),
        ),
        _t(
            j,
            "jenkins_status",
            "One build: result, duration, parameters, git revision.",
            {"job": JOB, "build": BUILD},
            ["job"],
            False,
            lambda a: jk.status(a["job"], a.get("build", "lastBuild")),
        ),
        _t(
            j,
            "jenkins_log",
            "Console log of a build, last lines.",
            {"job": JOB, "build": BUILD, "lines": _i("Last N lines, default 200, 0 = all")},
            ["job"],
            False,
            lambda a: jk.log(a["job"], a.get("build", "lastBuild"), a.get("lines", 200)),
        ),
        _t(
            j,
            "jenkins_wait",
            "Wait until a build finished (polls every 15 s), return the result.",
            {"job": JOB, "number": _i("Build number"), "timeout": _i("Seconds, default 3600")},
            ["job", "number"],
            False,
            lambda a: jk.wait(a["job"], a["number"], a.get("timeout", 3600)),
        ),
        _t(
            j,
            "jenkins_queue",
            "Pending queue items and why they wait.",
            {},
            [],
            False,
            lambda a: jk.queue(),
        ),
        _t(
            j,
            "jenkins_nodes",
            "Agents and whether they are online.",
            {},
            [],
            False,
            lambda a: jk.nodes(),
        ),
        _t(
            j,
            "jenkins_config",
            "config.xml of a job.",
            {"job": JOB},
            ["job"],
            False,
            lambda a: jk.config(a["job"]),
        ),
        _t(
            j,
            "jenkins_artifacts",
            "Artifact URLs of a build.",
            {"job": JOB, "build": BUILD},
            ["job"],
            False,
            lambda a: jk.artifacts(a["job"], a.get("build", "lastBuild")),
        ),
        _t(
            j,
            "jenkins_build",
            "WRITES: trigger a build, optionally with parameters.",
            {
                "job": JOB,
                "params": {"type": "object", "description": "Build parameters, name to value"},
            },
            ["job"],
            True,
            lambda a: jk.build(a["job"], a.get("params")),
        ),
        _t(
            j,
            "jenkins_stop",
            "WRITES: abort a running build.",
            {"job": JOB, "build": BUILD},
            ["job", "build"],
            True,
            lambda a: jk.stop(a["job"], a["build"]),
        ),
        _t(
            j,
            "jenkins_setconfig",
            "WRITES: replace config.xml of a job. Read it with "
            "jenkins_config first and keep a copy.",
            {"job": JOB, "xml": _s("Complete config.xml")},
            ["job", "xml"],
            True,
            lambda a: jk.setconfig(a["job"], a["xml"]),
        ),
    ]


def active_tools(op, jk, read_only=None) -> list[dict]:
    if read_only is None:
        read_only = os.environ.get("OPS_READ_ONLY") == "1"
    ready = {"op": op.configured(), "jk": jk.configured()}
    return [
        t for t in build_tools(op, jk) if ready[t["backend"]] and not (read_only and t["write"])
    ]


# ---------------------------------------------------------------- MCP


INSTRUCTIONS = (
    "OpenProject (op_*) and Jenkins (jenkins_*) over their REST APIs. Use these instead of a "
    "browser. Tools marked WRITES change the system: only call them for what the user asked."
)


def tool_result(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle(msg: dict, tools: list[dict]) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER,
            "instructions": INSTRUCTIONS if tools else "",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [t["def"] for t in tools]}
    elif method == "tools/call":
        params = msg.get("params") or {}
        by_name = {t["def"]["name"]: t for t in tools}
        tool = by_name.get(params.get("name"))
        if tool is None:
            result = tool_result("not available: backend not configured or read-only mode", True)
        else:
            try:
                out = tool["fn"](params.get("arguments") or {})
                result = tool_result(_cut(str(out)))
            except (OpsError, KeyError, ValueError, TypeError) as e:
                result = tool_result(f"{type(e).__name__}: {e}", True)
    else:
        error = {"code": -32601, "message": f"unknown method {method}"}
        return {"jsonrpc": "2.0", "id": mid, "error": error}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve() -> int:
    load_env_file()
    tools = active_tools(OpenProject(), Jenkins())
    if not tools:
        print("ops-tools: nothing configured, see mcp/OPS_TOOLS.md", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle(msg, tools)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


# ---------------------------------------------------------------- CLI


def cli(argv: list[str]) -> int:
    """`op <tool> k=v ...` or `jk <tool> k=v ...`; the tool name without its prefix."""
    load_env_file()
    tools = active_tools(OpenProject(), Jenkins(), read_only=False)
    prefix = {"op": "op_", "jk": "jenkins_"}.get(argv[0] if argv else "")
    if prefix is None or len(argv) < 2:
        names = ", ".join(t["def"]["name"] for t in tools) or "(nothing configured)"
        print("usage: ops_tools.py op|jk TOOL [key=value ...]\ntools: " + names, file=sys.stderr)
        return 2
    by_name = {t["def"]["name"]: t for t in tools}
    tool = by_name.get(prefix + argv[1])
    if tool is None:
        print(f"unknown or unconfigured tool {prefix + argv[1]}", file=sys.stderr)
        return 2
    args = {}
    props = tool["def"]["inputSchema"]["properties"]
    for kv in argv[2:]:
        key, _, value = kv.partition("=")
        kind = props.get(key, {}).get("type")
        args[key] = (
            int(value) if kind == "integer" else json.loads(value) if kind == "object" else value
        )
    try:
        print(tool["fn"](args))
    except (OpsError, KeyError, ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]) if len(sys.argv) > 1 else serve())
