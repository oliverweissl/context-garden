"""The Codex side of the plugin: .codex-plugin/plugin.json and hooks/codex-hooks.json.

Codex (checked against 0.150.1) does not expand ${CLAUDE_PLUGIN_ROOT} or
${PLUGIN_ROOT} in MCP server args, passes stdio servers only the variables
env_vars lists, and accepts only "deny" from PreToolUse hooks.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CODEX = json.loads((REPO_ROOT / ".codex-plugin" / "plugin.json").read_text())
CLAUDE = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())


def test_codex_manifest_matches_the_claude_one():
    assert CODEX["name"] == CLAUDE["name"]
    assert CODEX["version"] == CLAUDE["version"]
    assert (REPO_ROOT / CODEX["skills"]).is_dir()


def test_garden_control_starts_from_the_plugin_root_with_the_app_env():
    server = CODEX["mcpServers"]["garden-control"]
    assert server["cwd"] == "."  # Codex resolves it to the plugin root
    assert server["command"] == "python3"
    assert not any("${" in a for a in server["args"])  # never expanded by Codex
    assert (REPO_ROOT / server["args"][0]).is_file()
    assert {"CG_CONTROL", "CG_TOKEN"} <= set(server["env_vars"])


def _codex_hooks():
    return json.loads((REPO_ROOT / CODEX["hooks"]).read_text())["hooks"]


def test_codex_hooks_point_at_existing_scripts():
    for event, entries in _codex_hooks().items():
        for entry in entries:
            for hook in entry["hooks"]:
                paths = re.findall(r'"\$\{CLAUDE_PLUGIN_ROOT\}/([^"]+)"', hook["command"])
                assert paths, (event, hook["command"])
                for p in paths:
                    assert (REPO_ROOT / p).is_file(), (event, p)


def test_codex_hooks_avoid_what_codex_lacks_or_rejects():
    hooks = _codex_hooks()
    commands = [h["command"] for entries in hooks.values() for e in entries for h in e["hooks"]]
    assert not any("mycelium" in c for c in commands)  # no Agent tool, no allow/updatedInput
    assert "SubagentStop" not in hooks
    matchers = {e.get("matcher") for entries in hooks.values() for e in entries} - {None}
    assert matchers == {"Bash"}
    compost = [c for c in commands if "compost" in c]
    assert compost and all(c.endswith("hook --codex") for c in compost)
    assert all(h["timeout"] <= 3 for e in hooks["SessionEnd"] for h in e["hooks"])  # Codex caps it


def _co_hook(args, payload, env_mode):
    script = REPO_ROOT / "skills" / "compost" / "scripts" / "co_hook.py"
    out = subprocess.run(
        [sys.executable, str(script), *args],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "COMPOST_HOOK": env_mode},
    ).stdout
    return json.loads(out)["hookSpecificOutput"] if out.strip() else None


def test_compost_only_denies_under_codex(tmp_path):
    payload = {"tool_name": "Bash", "tool_input": {"command": "pytest -q"}, "cwd": str(tmp_path)}
    claude = _co_hook([], payload, "rewrite")
    assert claude["permissionDecision"] == "allow" and "updatedInput" in claude
    for mode in ("rewrite", "ask", "redirect"):
        codex = _co_hook(["--codex"], payload, mode)
        assert codex["permissionDecision"] == "deny"
        assert codex["permissionDecisionReason"]
        assert "updatedInput" not in codex
    assert _co_hook(["--codex"], payload, "off") is None


def test_control_server_explains_missing_env():
    script = REPO_ROOT / "mcp" / "cg_control.py"
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "report_to_manager", "arguments": {"text": "x"}},
        },
    ]
    run = subprocess.run(
        [sys.executable, str(script)],
        input="\n".join(json.dumps(m) for m in msgs) + "\n",
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=True,
    )
    listed, called = (json.loads(line) for line in run.stdout.splitlines())
    assert listed["result"]["tools"] == []
    assert called["result"]["isError"]
    assert "env_vars" in called["result"]["content"][0]["text"]
    assert "CG_CONTROL" in run.stderr
