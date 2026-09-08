"""Repository indexer: walks the repo, parses Python/C/C++ files, and
builds a flat symbol table + name-resolved call graph + test-to-source
links. Incremental: unchanged files (by content hash) are not re-parsed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pr_common import (
    classify_file,
    discover_files,
    estimate_tokens,
    read_text,
    sha256_text,
)
from pr_cpp import parse_cpp_file
from pr_python import parse_python_file

INDEX_FILENAME = "index.json"


def _resolve_python_import(module: str, importing_file: str, all_files: set[str]) -> str | None:
    """Best-effort: only resolves imports that map onto a file actually in
    this repo (relative imports, or a top-level package/module name that
    matches a file/package at the repo root). Does not consult sys.path or
    installed packages -- external imports (numpy, os, ...) intentionally
    resolve to None."""
    if module.startswith("."):
        base_dir = Path(importing_file).parent
        level = len(module) - len(module.lstrip("."))
        for _ in range(level - 1):
            base_dir = base_dir.parent
        rest = module.lstrip(".")
        target_dir = base_dir / rest.replace(".", "/") if rest else base_dir
    else:
        target_dir = Path(module.replace(".", "/"))

    for candidate in (
        str(target_dir) + ".py",
        str(target_dir / "__init__.py"),
    ):
        candidate = candidate.replace("\\", "/").lstrip("./")
        if candidate in all_files:
            return candidate
    return None


def _resolve_cpp_include(header: str, importing_file: str, all_files: set[str]) -> str | None:
    importing_dir = Path(importing_file).parent
    same_dir = str(importing_dir / header).replace("\\", "/")
    if same_dir in all_files:
        return same_dir
    for f in all_files:
        if f.endswith("/" + header) or f == header:
            return f
    return None


_PY_SUFFIXES = {".py"}
_CPP_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}


def _test_targets(rel_path: str, all_files) -> list[str]:
    """Which source files this test file most likely exercises, by a
    filename-convention guess (test_foo.py <-> foo.py). Constrained to
    files of the *same language family* as the test file itself -- without
    this, e.g. `csrc/test_solver.cpp` and `tests/test_solver.py` would both
    stem-match both `interp/solver.py` and `csrc/solver.cpp`, wrongly
    cross-linking a Python test to a C++ source file and vice versa."""
    stem = Path(rel_path).stem
    suffix = Path(rel_path).suffix.lower()
    if suffix in _PY_SUFFIXES:
        allowed_suffixes = _PY_SUFFIXES
    elif suffix in _CPP_SUFFIXES:
        allowed_suffixes = _CPP_SUFFIXES
    else:
        allowed_suffixes = {suffix}

    guesses = set()
    if stem.startswith("test_"):
        guesses.add(stem[len("test_") :])
    if stem.endswith("_test"):
        guesses.add(stem[: -len("_test")])

    targets = set()
    for f in all_files:
        if Path(f).suffix.lower() in allowed_suffixes and Path(f).stem in guesses:
            targets.add(f)
    return sorted(targets)


def _parse_files(repo_root: Path, old_files: dict) -> tuple[dict, int, int]:
    """Returns (files_meta, reused_count, reparsed_count). Files whose
    content hash matches `old_files` are reused verbatim (no re-parse)."""
    files_meta: dict[str, dict] = {}
    rel_paths = discover_files(repo_root)
    reused = reparsed = 0

    for rel in rel_paths:
        full = repo_root / rel
        text = read_text(full)
        if text is None:
            continue
        content_hash = sha256_text(text)
        old_entry = old_files.get(rel)
        if old_entry and old_entry.get("hash") == content_hash:
            files_meta[rel] = old_entry
            reused += 1
            continue

        reparsed += 1
        kind = classify_file(rel)
        entry = {
            "hash": content_hash,
            "kind": kind,
            "language": None,
            "size_tokens": estimate_tokens(text),
            "line_count": text.count("\n") + 1,
            "imports": [],
            "resolved_imports": [],
            "symbols": [],
        }
        if kind in ("python",) or (kind == "test" and rel.endswith(".py")):
            parsed = parse_python_file(text)
            entry["language"] = "python"
        elif kind in ("cpp",) or (
            kind == "test"
            and Path(rel).suffix.lower() in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}
        ):
            parsed = parse_cpp_file(text)
            entry["language"] = "cpp"
        else:
            parsed = {"imports": [], "symbols": [], "parse_error": False}
        entry["imports"] = parsed["imports"]
        entry["symbols"] = parsed["symbols"]
        entry["parse_error"] = parsed.get("parse_error", False)
        files_meta[rel] = entry

    return files_meta, reused, reparsed


def _finish_index(repo_root: Path, files_meta: dict) -> dict:
    """Import resolution, symbol table, call-graph, test links. Always runs
    over the *full* current file set (reused + reparsed alike), since a
    newly added/changed file can change what an unrelated, unchanged file's
    imports resolve to."""
    all_files_set = set(files_meta.keys())

    # resolve imports/includes to in-repo files
    for rel, entry in files_meta.items():
        resolved = []
        for imp in entry["imports"]:
            if entry["language"] == "python":
                target = _resolve_python_import(imp, rel, all_files_set)
            elif entry["language"] == "cpp":
                target = _resolve_cpp_include(imp, rel, all_files_set)
            else:
                target = None
            if target:
                resolved.append(target)
        entry["resolved_imports"] = sorted(set(resolved))

    # flat symbol table + name index for call resolution
    symbol_index: dict[str, list[str]] = {}
    for rel, entry in files_meta.items():
        for sym in entry["symbols"]:
            sid = f"{rel}:{sym['qualname']}"
            sym["id"] = sid
            symbol_index.setdefault(sym["name"], []).append(sid)

    # name-based call edges: within the same file first (unambiguous),
    # else any same-named symbol among files this file imports, else any
    # same-named symbol repo-wide (last resort, most likely to be wrong --
    # flagged as such in the edge).
    call_edges = []
    for rel, entry in files_meta.items():
        file_symbol_ids = {s["qualname"]: s["id"] for s in entry["symbols"]}
        imported_files = set(entry["resolved_imports"])
        for sym in entry["symbols"]:
            for callee_name in sym["calls"]:
                target_id = None
                precision = None
                if callee_name in file_symbol_ids:
                    target_id = file_symbol_ids[callee_name]
                    precision = "same_file"
                else:
                    candidates = symbol_index.get(callee_name, [])
                    in_imports = [c for c in candidates if c.split(":")[0] in imported_files]
                    if len(in_imports) == 1:
                        target_id = in_imports[0]
                        precision = "resolved_import"
                    elif len(candidates) == 1:
                        target_id = candidates[0]
                        precision = "unique_name_repo_wide"
                    elif len(candidates) > 1:
                        precision = "ambiguous"
                if target_id:
                    call_edges.append({"from": sym["id"], "to": target_id, "precision": precision})

    # test -> source links
    test_links: dict[str, list[str]] = {}
    for rel, entry in files_meta.items():
        if entry["kind"] != "test":
            continue
        targets = set(entry["resolved_imports"])
        targets |= set(_test_targets(rel, files_meta))
        for target in targets:
            test_links.setdefault(target, []).append(rel)

    return {
        "repo_root": str(repo_root),
        "indexed_at": time.time(),
        "files": files_meta,
        "symbol_index": symbol_index,
        "call_edges": call_edges,
        "test_links": test_links,
    }


def build_index(repo_root: Path, old_files: dict | None = None) -> dict:
    """One-shot convenience: parse everything (or reuse from `old_files`)
    and return the finished index. Discards reused/reparsed counts --
    use load_or_build_index for that."""
    files_meta, _reused, _reparsed = _parse_files(repo_root, old_files or {})
    return _finish_index(repo_root, files_meta)


def load_or_build_index(repo_root: Path, store_dir: Path, force: bool = False) -> tuple[dict, dict]:
    """Returns (index, report). Incremental: files whose content hash is
    unchanged since the last index are reused verbatim, not re-parsed."""
    index_path = store_dir / INDEX_FILENAME
    old_files = {}
    if index_path.exists() and not force:
        try:
            old_files = json.loads(index_path.read_text()).get("files", {})
        except json.JSONDecodeError:
            old_files = {}

    files_meta, reused, reparsed = _parse_files(repo_root, old_files)
    new_index = _finish_index(repo_root, files_meta)

    store_dir.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(new_index, indent=2))
    report = {
        "files_indexed": len(new_index["files"]),
        "files_unchanged": reused,
        "files_reparsed_or_new": reparsed,
        "symbols": sum(len(e["symbols"]) for e in new_index["files"].values()),
        "call_edges": len(new_index["call_edges"]),
    }
    return new_index, report
