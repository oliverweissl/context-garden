"""garden-tools MCP server (mcp/garden_tools.py): tool list, mode gating, wrapped CLI results."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER = REPO_ROOT / "mcp" / "garden_tools.py"
SKILLS = REPO_ROOT / "skills"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "a.py").write_text("def f():\n    return 1\n")
    return root


def rpc(repo, calls, **modes):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CONTEXT_GARDEN_MODE_")}
    env.update({f"CONTEXT_GARDEN_MODE_{k.upper()}": v for k, v in modes.items()})
    env["HOME"] = str(repo.parent)  # no user-level mode overrides
    msgs = [{"jsonrpc": "2.0", "id": i + 1, **c} for i, c in enumerate(calls)]
    out = subprocess.run(
        [sys.executable, str(SERVER)],
        input="\n".join(map(json.dumps, msgs)) + "\n",
        capture_output=True,
        text=True,
        cwd=repo,
        env=env,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return [json.loads(line) for line in out.stdout.splitlines()]


def call(name, **args):
    return {"method": "tools/call", "params": {"name": name, "arguments": args}}


def test_lists_six_read_only_tools_without_any_environment(repo):
    r = rpc(repo, [{"method": "initialize", "params": {}}, {"method": "tools/list"}])
    assert r[0]["result"]["serverInfo"]["name"] == "garden-tools"
    names = {t["name"] for t in r[1]["result"]["tools"]}
    assert names == {
        "seedbank_facts",
        "greenhouse_brief",
        "pruner_select",
        "compost_get",
        "compost_grep",
        "mycelium_notes",
    }
    for t in r[1]["result"]["tools"]:
        assert t["inputSchema"]["required"] is not None


def test_a_tool_is_refused_while_its_skill_is_off_or_manual(repo):
    for mode in ({}, {"mycelium": "manual"}):
        res = rpc(repo, [call("mycelium_notes", query="anything")], **mode)[0]["result"]
        assert res["isError"] and "not on" in res["content"][0]["text"]


def test_mycelium_notes_returns_a_recorded_finding(repo):
    env_mode = {"mycelium": "on"}
    subprocess.run(
        [
            sys.executable,
            str(SKILLS / "mycelium/scripts/mycelium.py"),
            "note",
            "parser",
            "the parser lives in a.py",
            "a.py",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, "CONTEXT_GARDEN_MODE_MYCELIUM": "on", "HOME": str(repo.parent)},
    )
    res = rpc(repo, [call("mycelium_notes", query="parser")], **env_mode)[0]["result"]
    assert not res["isError"], res
    assert "a.py" in res["content"][0]["text"]


def test_compost_get_and_grep_read_a_stored_run(repo):
    env = {**os.environ, "CONTEXT_GARDEN_MODE_COMPOST": "on", "HOME": str(repo.parent)}
    compost = [sys.executable, str(SKILLS / "compost/scripts/compost.py")]
    subprocess.run(
        [
            *compost,
            "run",
            "--",
            sys.executable,
            "-c",
            "print('alpha'); print('needle here'); print('omega')",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    run_id = subprocess.run(
        [*compost, "list"], cwd=repo, capture_output=True, text=True, env=env
    ).stdout.split()[0]
    got, grep = rpc(
        repo,
        [
            call("compost_get", run_id=run_id, lines="all"),
            call("compost_grep", run_id=run_id, pattern="needle", fixed=True),
        ],
        compost="on",
    )
    assert "alpha" in got["result"]["content"][0]["text"]
    assert "needle here" in grep["result"]["content"][0]["text"]


def test_bad_arguments_and_unknown_tools_are_errors_not_crashes(repo):
    r = rpc(repo, [call("pruner_select"), call("nope"), {"method": "bogus"}])
    assert r[0]["result"]["isError"] and r[1]["result"]["isError"]
    assert r[2]["error"]["code"] == -32601
