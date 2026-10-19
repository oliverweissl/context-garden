"""Deterministic repo harvest: what another repo's agent needs to know about
this one without reading it. Reads only tracked files and context-garden
state (AGENTS.md seedbank block, .seedbank/warm/, .mycelium/notes.json)."""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from pathlib import Path

from gh_base import git, slugify

try:
    import tomllib
except ImportError:  # Python < 3.11: manifests in TOML fall back to a regex scan
    tomllib = None

MAX_FACTS = 12
MAX_NOTES = 12
MAX_ITEM_CHARS = 240
MANIFEST_DEPTH = 2  # root and one level of subprojects (monorepos)
LANGUAGES = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".c": "C", ".h": "C/C++", ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".cs": "C#",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".scala": "Scala", ".sh": "Shell",
    ".tf": "Terraform", ".proto": "Protobuf", ".sql": "SQL", ".f90": "Fortran",
}
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)")
_CRED = re.compile(r"//[^/@]+@")
SEEDBANK_BLOCK = re.compile(r"<!-- seedbank:begin -->(.*?)<!-- seedbank:end -->", re.DOTALL)


def _clip(text: str, n: int = MAX_ITEM_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def repo_slug(repo_root: Path) -> str:
    """Remote basename (same for every clone), else the directory name."""
    remote = git(["remote", "get-url", "origin"], repo_root)
    if remote:
        return slugify(re.sub(r"\.git$", "", remote.rstrip("/").split("/")[-1].split(":")[-1]))
    return slugify(repo_root.name)


def readme_summary(repo_root: Path) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        text = _read(repo_root / name)
        if not text:
            continue
        para = []
        for line in text.splitlines():
            s = line.strip()
            skip = (not s or s.startswith(("#", "<", "!", "[!", "=", "-->", "|", "```"))
                    or set(s) <= set("=-~*"))
            if skip:
                if para:
                    break
                continue
            para.append(s)
        if para:
            return _clip(" ".join(para), 300)
    return ""


def _toml(text: str) -> dict:
    if tomllib is None:
        return {}
    try:
        return tomllib.loads(text)
    except Exception:  # noqa: BLE001 -- a broken manifest must not stop the harvest
        return {}


def _req_names(reqs) -> list:
    out = []
    for r in reqs or []:
        m = _REQ_NAME.match(str(r))
        if m:
            out.append(m.group(1))
    return out


def parse_manifest(path: Path) -> tuple:
    """(packages provided, dependencies, entrypoints) for one manifest file."""
    text, name = _read(path), path.name
    if name == "pyproject.toml":
        data = _toml(text)
        proj = data.get("project", {})
        poetry = data.get("tool", {}).get("poetry", {})
        pkgs = [proj.get("name") or poetry.get("name")]
        deps = _req_names(proj.get("dependencies"))
        deps += [d for d in (poetry.get("dependencies") or {}) if d.lower() != "python"]
        for extra in (proj.get("optional-dependencies") or {}).values():
            deps += _req_names(extra)
        entry = list((proj.get("scripts") or {}).keys())
        return [p for p in pkgs if p], deps, entry
    if name == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            return [], [], []
        deps = []
        for key in ("dependencies", "peerDependencies", "devDependencies"):
            deps += list((data.get(key) or {}).keys())
        entry = [f"npm run {s}" for s in (data.get("scripts") or {})]
        return [data["name"]] if data.get("name") else [], deps, entry
    if name == "go.mod":
        mod = re.search(r"^module\s+(\S+)", text, re.M)
        deps = re.findall(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-/]+)\s+v[\w.\-+]+", text, re.M)
        return [mod.group(1)] if mod else [], deps, []
    if name == "Cargo.toml":
        data = _toml(text)
        deps = list((data.get("dependencies") or {}).keys())
        pkg = (data.get("package") or {}).get("name")
        return [pkg] if pkg else [], deps, []
    if name == "requirements.txt":
        return [], _req_names(line for line in text.splitlines() if not line.startswith(("-", "#"))), []
    return [], [], []


MANIFESTS = ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", "requirements.txt")


def harvest(repo_root: Path, base_root: Path) -> dict:
    tracked = [f for f in git(["ls-files"], repo_root).splitlines() if f]
    langs = Counter(LANGUAGES[Path(f).suffix.lower()] for f in tracked if Path(f).suffix.lower() in LANGUAGES)
    tops = Counter(f.split("/", 1)[0] + ("/" if "/" in f else "") for f in tracked)

    packages, deps, entry = [], [], []
    for f in tracked:
        if Path(f).name in MANIFESTS and f.count("/") < MANIFEST_DEPTH and "/node_modules/" not in f:
            p, d, e = parse_manifest(repo_root / f)
            packages += p
            deps += d
            entry += e
    own = {p.lower() for p in packages}
    deps = sorted({d for d in deps if d.lower() not in own}, key=str.lower)

    remote = _CRED.sub("//", git(["remote", "get-url", "origin"], repo_root))
    return {
        "version": 1,
        "slug": repo_slug(repo_root),
        "name": repo_root.name,
        "remote": remote,
        "local_path": os.path.relpath(repo_root, base_root),
        "head": git(["rev-parse", "HEAD"], repo_root),
        "branch": git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root),
        "synced_at": time.time(),
        "readme_summary": readme_summary(repo_root),
        "languages": [lang for lang, _ in langs.most_common(4)],
        "layout": [f"{d} ({n})" for d, n in tops.most_common(10)],
        "packages": sorted(set(packages)),
        "dependencies": deps,
        "entrypoints": sorted(set(entry))[:15],
        "facts": garden_facts(repo_root),
        "warm_topics": sorted(p.stem for p in (repo_root / ".seedbank" / "warm").glob("*.md")),
        "findings": mycelium_notes(repo_root),
    }


def garden_facts(repo_root: Path) -> list:
    """Hot facts: the seedbank block of AGENTS.md if present, else the bullets
    of a hand-written AGENTS.md/CLAUDE.md."""
    for name in ("AGENTS.md", "CLAUDE.md"):
        text = _read(repo_root / name)
        block = SEEDBANK_BLOCK.search(text)
        body = block.group(1) if block else text
        bullets = [_clip(line.strip()[2:]) for line in body.splitlines()
                   if line.strip().startswith(("- ", "* ")) and "`.seedbank/warm/" not in line]
        if bullets:
            return bullets[:MAX_FACTS]
    return []


def mycelium_notes(repo_root: Path) -> list:
    try:
        notes = json.loads(_read(repo_root / ".mycelium" / "notes.json") or "{}").get("notes", [])
    except ValueError:
        return []
    return [_clip(f"{n['topic']}: {n['text']}") for n in notes
            if n.get("kind") != "status" and n.get("topic") and n.get("text")][:MAX_NOTES]
