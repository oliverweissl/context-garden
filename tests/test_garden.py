"""Plugin onboarding helper (hooks/garden.py) and the /context-garden:setup command."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GARDEN = REPO_ROOT / "hooks" / "garden.py"
SKILLS = sorted(d.name for d in (REPO_ROOT / "skills").iterdir() if (d / "SKILL.md").is_file())


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("CONTEXT_GARDEN_MODE_")}
    env.update(HOME=str(home), CLAUDE_PROJECT_DIR=str(root))
    return root, home, env


def _garden(*args, repo):
    root, _, env = repo
    return subprocess.run(
        ["python3", str(GARDEN), *args], cwd=root, env=env, capture_output=True, text=True
    )


def test_knows_every_skill():
    import importlib.util

    spec = importlib.util.spec_from_file_location("garden", GARDEN)
    garden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(garden)
    assert sorted(garden.SKILLS) == SKILLS
    for _, on in garden.PRESETS.values():
        assert set(on) <= set(SKILLS)


def test_fresh_install_gets_setup_notice(repo):
    out = json.loads(_garden("hook", "session-start", repo=repo).stdout)
    assert "/context-garden:setup" in out["systemMessage"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_preset_turns_on_its_skills_and_silences_notice(repo):
    root, _, _ = repo
    assert _garden("preset", "platform", repo=repo).returncode == 0
    overrides = json.loads((root / ".claude" / "settings.local.json").read_text())["skillOverrides"]
    assert overrides["greenhouse"] == "on" and overrides["seedbank"] == "on"
    assert overrides["mycelium"] == "user-invocable-only"
    assert _garden("hook", "session-start", repo=repo).stdout == ""


def test_preset_off_and_user_scope(repo):
    _, home, _ = repo
    assert _garden("preset", "off", "--scope", "user", repo=repo).returncode == 0
    overrides = json.loads((home / ".claude" / "settings.json").read_text())["skillOverrides"]
    assert set(overrides.values()) == {"off"}
    assert _garden("hook", "session-start", repo=repo).stdout == ""


def test_bad_input_is_rejected(repo):
    assert _garden("preset", "nope", repo=repo).returncode == 2
    assert _garden("set", "compost", "maybe", repo=repo).returncode == 2


def test_setup_command_and_hook_are_wired():
    command = (REPO_ROOT / "commands" / "setup.md").read_text()
    assert "${CLAUDE_PLUGIN_ROOT}/hooks/garden.py" in command
    hooks = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    commands = [h["command"] for entry in hooks["SessionStart"] for h in entry["hooks"]]
    assert any("hooks/garden.py\" hook session-start" in c for c in commands)
