"""Project fingerprint for portable export bundles (seedbank, greenhouse).

Identical copy in every skill that exports (skills never import each other);
tests/skills/test_bundles.py checks the copies stay identical.

A fingerprint identifies a project, not a checkout: it is the same in every
clone and never contains a local path or credentials.
  remote        origin URL without scheme, user, password, port or `.git`,
                host lowercased: "github.com/acme/orders-api" ("" when there
                is no origin or it is a local path)
  root_commits  SHA(s) of the parentless commit(s) of HEAD (a shallow clone
                reports its shallow boundary instead)
Two fingerprints match when the remotes are equal or a root commit is shared,
so a fork or a moved remote still matches by history.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_SCP = re.compile(r"^(?:[^@/\s]+@)?([^:/\s]+):(?!//)(.+)$")  # user@host:path


def _git(args, cwd) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
    except (FileNotFoundError, NotADirectoryError):  # no git binary / no such dir
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def normalize_remote(url: str) -> str:
    """'https://u:p@GitHub.com:443/acme/api.git' and 'git@github.com:acme/api'
    -> 'github.com/acme/api'. A local path (or file://) remote gives ''."""
    url = (url or "").strip()
    if _SCHEME.match(url):
        rest = _SCHEME.sub("", url)
        hostport, _, path = rest.partition("/")
        host = hostport.rsplit("@", 1)[-1].split(":", 1)[0]
    else:
        m = _SCP.match(url)
        if not m or len(m.group(1)) == 1:  # "C:\\..." is a Windows path, not a host
            return ""
        host, path = m.group(1), m.group(2)
    path = re.sub(r"\.git$", "", path.strip().strip("/")).strip("/")
    return f"{host.lower()}/{path}" if host and path else ""


def fingerprint(repo_root) -> dict:
    root = Path(repo_root)
    roots = _git(["rev-list", "--max-parents=0", "HEAD"], root).split()
    return {
        "remote": normalize_remote(_git(["remote", "get-url", "origin"], root)),
        "root_commits": sorted(set(roots)),
    }


def describe(fp: dict | None) -> str:
    fp = fp or {}
    roots = ",".join(r[:10] for r in fp.get("root_commits") or []) or "-"
    return f"remote={fp.get('remote') or '-'} root={roots}"


def match(a: dict | None, b: dict | None) -> str:
    """Why two fingerprints denote the same project ('remote' / 'root commit'),
    or '' when they do not (or either is empty)."""
    a, b = a or {}, b or {}
    if a.get("remote") and a.get("remote") == b.get("remote"):
        return "remote"
    if set(a.get("root_commits") or []) & set(b.get("root_commits") or []):
        return "root commit"
    return ""


def is_empty(fp: dict | None) -> bool:
    return not fp or not (fp.get("remote") or fp.get("root_commits"))
