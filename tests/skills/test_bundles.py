"""Project fingerprint shared by the seedbank and greenhouse export bundles
(skills/*/scripts/cg_fingerprint.py); the CLIs are covered by the smoke tests."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COPIES = sorted(REPO_ROOT.glob("skills/*/scripts/cg_fingerprint.py"))


def _load():
    spec = importlib.util.spec_from_file_location("cg_fingerprint", COPIES[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fp = _load()


def test_copies_are_identical():
    assert {p.parent.parent.name for p in COPIES} >= {"seedbank", "greenhouse"}
    assert len({p.read_text() for p in COPIES}) == 1, "cg_fingerprint.py copies drifted"


@pytest.mark.parametrize(
    "url, want",
    [
        ("https://user:pw@GitHub.com:443/acme/api.git", "github.com/acme/api"),
        ("git@github.com:acme/api.git", "github.com/acme/api"),
        ("ssh://git@gitlab.example.com:2222/group/sub/api.git/", "gitlab.example.com/group/sub/api"),
        ("https://github.com/acme/api/", "github.com/acme/api"),
        ("/home/me/src/api", ""),
        ("../api", ""),
        ("file:///home/me/src/api", ""),
        ("C:\\src\\api", ""),
        ("", ""),
    ],
)
def test_normalize_remote(url, want):
    assert fp.normalize_remote(url) == want


def test_match():
    a = {"remote": "github.com/acme/api", "root_commits": ["r1"]}
    assert fp.match(a, {"remote": "github.com/acme/api", "root_commits": []}) == "remote"
    assert fp.match(a, {"remote": "", "root_commits": ["r0", "r1"]}) == "root commit"
    assert fp.match(a, {"remote": "github.com/acme/web", "root_commits": ["r2"]}) == ""
    assert fp.match({"remote": "", "root_commits": []}, {"remote": "", "root_commits": []}) == ""
    assert fp.match(None, a) == ""


def test_fingerprint_is_the_same_in_every_clone(tmp_path):
    def git(*args, cwd):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       cwd=cwd, check=True, capture_output=True)

    src = tmp_path / "src"
    src.mkdir()
    git("init", "-q", cwd=src)
    git("remote", "add", "origin", "git@github.com:acme/api.git", cwd=src)
    (src / "f").write_text("x")
    git("add", "f", cwd=src)
    git("commit", "-qm", "init", cwd=src)
    git("clone", "-q", str(src), str(tmp_path / "clone"), cwd=tmp_path)

    a, b = fp.fingerprint(src), fp.fingerprint(tmp_path / "clone")
    assert a["remote"] == "github.com/acme/api"
    assert b["remote"] == ""  # cloned from a local path: not portable
    assert a["root_commits"] == b["root_commits"] and len(a["root_commits"]) == 1
    assert fp.match(a, b) == "root commit"
    assert str(tmp_path) not in repr(a) + repr(b)
    assert fp.is_empty(fp.fingerprint(tmp_path / "nowhere"))
