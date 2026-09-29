"""Per-skill on/manual/off modes (skills/*/scripts/cg_mode.py)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS = sorted(d.name for d in (REPO_ROOT / "skills").iterdir() if (d / "SKILL.md").is_file())


@pytest.fixture
def repo(tmp_path):
    """A scratch git repo with an isolated HOME and no mode env vars."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("CONTEXT_GARDEN_MODE_")}
    env.update(HOME=str(home), CLAUDE_PROJECT_DIR=str(root))
    return root, home, env


def _bin(skill, *args, repo, stdin=None):
    root, _, env = repo
    return subprocess.run(
        [str(REPO_ROOT / "skills" / skill / "bin" / skill), *args],
        cwd=root, env=env, input=stdin, capture_output=True, text=True,
    )


def test_mode_module_is_identical_in_every_skill():
    copies = {s: (REPO_ROOT / "skills" / s / "scripts" / "cg_mode.py").read_text() for s in SKILLS}
    assert len(set(copies.values())) == 1, "cg_mode.py copies drifted; keep them identical"


@pytest.mark.parametrize("skill", SKILLS)
def test_skill_md_injects_its_own_banner(skill):
    text = (REPO_ROOT / "skills" / skill / "SKILL.md").read_text()
    assert f"\n!`${{CLAUDE_SKILL_DIR}}/bin/{skill} mode --banner`\n" in text
    assert f"allowed-tools: Bash(${{CLAUDE_SKILL_DIR}}/bin/{skill} *)" in text


@pytest.mark.parametrize("skill", SKILLS)
def test_default_is_on_and_banner_is_silent(skill, repo):
    assert _bin(skill, "mode", repo=repo).stdout.strip() == f"{skill}: on (from default)"
    assert _bin(skill, "mode", "--banner", repo=repo).stdout == ""
    assert _bin(skill, "mode", "--is-on", repo=repo).returncode == 0


def test_manual_writes_skill_override_and_keeps_other_settings(repo):
    root, _, _ = repo
    local = root / ".claude" / "settings.local.json"
    local.parent.mkdir()
    local.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
    out = _bin("compost", "mode", "manual", repo=repo)
    assert out.returncode == 0, out.stderr
    data = json.loads(local.read_text())
    assert data["skillOverrides"] == {"compost": "user-invocable-only"}
    assert data["permissions"] == {"allow": ["Bash(ls)"]}
    assert "MANUAL mode" in _bin("compost", "mode", "--banner", repo=repo).stdout
    assert _bin("compost", "mode", "--is-on", repo=repo).returncode == 1


def test_off_banner_and_precedence(repo):
    root, home, env = repo
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"skillOverrides": {"pruner": "off"}}))
    assert "turned OFF" in _bin("pruner", "mode", "--banner", repo=repo).stdout
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"skillOverrides": {"pruner": "user-invocable-only"}})
    )
    assert _bin("pruner", "mode", repo=repo).stdout.startswith("pruner: manual")  # project > user
    _bin("pruner", "mode", "on", repo=repo)  # local > project
    assert _bin("pruner", "mode", repo=repo).stdout.startswith("pruner: on")
    env["CONTEXT_GARDEN_MODE_PRUNER"] = "off"  # env > everything
    assert _bin("pruner", "mode", repo=repo).stdout.startswith("pruner: off")


def test_name_only_counts_as_on(repo):
    root, _, _ = repo
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.local.json").write_text(
        json.dumps({"skillOverrides": {"weeder": "name-only"}})
    )
    assert _bin("weeder", "mode", "--is-on", repo=repo).returncode == 0


def test_refuses_to_rewrite_invalid_settings(repo):
    root, _, _ = repo
    (root / ".claude").mkdir()
    bad = root / ".claude" / "settings.local.json"
    bad.write_text("{not json")
    out = _bin("seedbank", "mode", "off", repo=repo)
    assert out.returncode != 0 and "not valid JSON" in (out.stderr + out.stdout)
    assert bad.read_text() == "{not json"


def test_mycelium_gate_is_silent_unless_on(repo):
    root, _, _ = repo
    payload = json.dumps({"session_id": "s", "cwd": str(root), "tool_name": "Agent",
                          "tool_input": {"description": "d", "prompt": "look around"}})
    assert "deny" in _bin("mycelium", "hook", "pre-agent", repo=repo, stdin=payload).stdout
    _bin("mycelium", "mode", "manual", repo=repo)
    assert _bin("mycelium", "hook", "pre-agent", repo=repo, stdin=payload).stdout == ""


def test_seedbank_hook_observes_only_when_on(repo):
    root, _, _ = repo
    (root / "a.py").write_text("x = 1\n")
    payload = json.dumps({"session_id": "s", "cwd": str(root), "tool_name": "Read",
                          "tool_input": {"file_path": str(root / "a.py")}})
    _bin("seedbank", "mode", "off", repo=repo)
    _bin("seedbank", "hook", repo=repo, stdin=payload)
    assert not (root / ".seedbank" / "observations.jsonl").exists()
    _bin("seedbank", "mode", "on", repo=repo)
    _bin("seedbank", "hook", repo=repo, stdin=payload)
    assert (root / ".seedbank" / "observations.jsonl").exists()
