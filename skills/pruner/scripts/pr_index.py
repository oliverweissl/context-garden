"""Repository indexer: walks the repo, parses Python/C/C++ files, and
stores files, symbols and name references in a SQLite store
(`.pruner/index.sqlite`, see pr_store.py). Incremental: files whose
(mtime, size) is unchanged are not even read; files whose content hash is
unchanged are not re-parsed; only changed files' rows are rewritten.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path, PurePosixPath

import pr_store
from pr_common import (
    CPP_SUFFIXES,
    PYTHON_SUFFIXES,
    classify_file,
    discover_files,
    estimate_tokens,
    read_text,
    sha256_text,
)
from pr_cpp import parse_cpp_file
from pr_python import parse_python_file

DB_FILENAME = "index.sqlite"
LEGACY_INDEX_FILENAME = "index.json"  # pre-v4 store; removed + rebuilt on sight
INDEX_VERSION = 4  # bump when id/format semantics change, forces a rebuild
PACKAGE_DECL_NAMES = {"pyproject.toml", "setup.cfg", "setup.py"}
PARSERS = ("auto", "builtin", "tree-sitter")


# ---------------------------------------------------------------- import resolution


def python_source_roots(all_files) -> list[str]:
    """Directories absolute imports may be rooted at: the repo root, any
    `src/` directory, and any directory holding a package declaration
    (pyproject.toml / setup.cfg / setup.py) plus its `src/`."""
    roots = {""}
    for f in all_files:
        p = PurePosixPath(f)
        parent = "" if str(p.parent) == "." else str(p.parent)
        if p.name in PACKAGE_DECL_NAMES:
            roots.add(parent)
            roots.add(f"{parent}/src" if parent else "src")
        parts = p.parts[:-1]
        if "src" in parts:
            roots.add("/".join(parts[: parts.index("src") + 1]))
    return sorted(roots, key=lambda r: (r.count("/"), r))


class _PyResolver:
    def __init__(self, all_files: set[str]):
        self.all_files = all_files
        # first path component under each root -> roots, so resolving
        # `a.b` only probes roots that actually contain `a`
        self.roots_by_top: dict[str, list[str]] = {}
        roots = python_source_roots(all_files)
        root_set = set(roots)
        for f in all_files:
            if not f.endswith(".py"):
                continue
            parts = PurePosixPath(f).parts
            for k in range(len(parts)):
                root = "/".join(parts[:k])
                if root in root_set:
                    top = parts[k][:-3] if k == len(parts) - 1 else parts[k]
                    lst = self.roots_by_top.setdefault(top, [])
                    if root not in lst:
                        lst.append(root)

    def resolve(self, module: str, importing_file: str) -> str | None:
        """Best-effort: only resolves imports that map onto a file in this
        repo (relative imports, or absolute ones under a source root).
        External imports (numpy, os, ...) resolve to None."""
        if module.startswith("."):
            base_dir = PurePosixPath(importing_file).parent
            level = len(module) - len(module.lstrip("."))
            for _ in range(level - 1):
                base_dir = base_dir.parent
            rest = module.lstrip(".")
            target = base_dir / rest.replace(".", "/") if rest else base_dir
            return self._probe([str(target)])
        top = module.split(".", 1)[0]
        rel = module.replace(".", "/")
        roots = self.roots_by_top.get(top, [])
        found = [c for r in roots if (c := self._probe([f"{r}/{rel}" if r else rel]))]
        if len(found) > 1:
            # several roots provide the package (monorepo): prefer the one
            # sharing the longest directory prefix with the importer
            found.sort(key=lambda c: -_common_prefix(c, importing_file))
        return found[0] if found else None

    def _probe(self, targets: list[str]) -> str | None:
        for t in targets:
            t = os.path.normpath(t).replace("\\", "/")
            t = "" if t == "." else t
            for cand in (f"{t}.py", f"{t}/__init__.py" if t else "__init__.py"):
                if cand in self.all_files:
                    return cand
        return None


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(PurePosixPath(a).parts, PurePosixPath(b).parts):
        if x != y:
            break
        n += 1
    return n


class _CppResolver:
    def __init__(self, all_files: set[str]):
        self.all_files = all_files
        self.by_basename: dict[str, list[str]] = {}
        for f in all_files:
            self.by_basename.setdefault(PurePosixPath(f).name, []).append(f)

    def resolve(self, header: str, importing_file: str) -> str | None:
        joined = os.path.normpath(str(PurePosixPath(importing_file).parent / header))
        joined = joined.replace("\\", "/")
        if joined in self.all_files:
            return joined
        tail = header.replace("\\", "/")
        while tail.startswith("../") or tail.startswith("./"):
            tail = tail.split("/", 1)[1]
        cands = [
            f
            for f in self.by_basename.get(PurePosixPath(tail).name, [])
            if f == tail or f.endswith("/" + tail)
        ]
        if not cands:
            return None
        cands.sort(key=lambda c: (-_common_prefix(c, importing_file), c))
        return cands[0]


# ---------------------------------------------------------------- parsing


def _parse_source(rel: str, text: str, kind: str, parser: str) -> tuple[str | None, dict]:
    suffix = Path(rel).suffix.lower()
    language = None
    if kind in ("python", "test") and suffix in PYTHON_SUFFIXES:
        language = "python"
    elif kind in ("cpp", "test") and suffix in CPP_SUFFIXES:
        language = "cpp"
    if language is None:
        return None, {"imports": [], "symbols": [], "parse_error": False}
    if parser != "builtin":
        import pr_treesitter

        parsed = pr_treesitter.parse(language, text)
        if parsed is not None:
            return language, parsed
    if language == "python":
        return language, parse_python_file(text)
    return language, parse_cpp_file(text)


def resolve_parser(requested: str) -> str:
    """'auto' -> 'tree-sitter' when the optional packages import, else 'builtin'."""
    if requested == "builtin":
        return "builtin"
    import pr_treesitter

    if pr_treesitter.available():
        return "tree-sitter"
    if requested == "tree-sitter":
        raise ValueError(
            "--parser tree-sitter requested but `tree_sitter` + `tree_sitter_python`/"
            "`tree_sitter_cpp` are not importable (pip install tree-sitter "
            "tree-sitter-python tree-sitter-cpp), or use --parser builtin"
        )
    return "builtin"


def _symbol_rows(text: str, symbols: list[dict]) -> tuple[list[list], list[tuple]]:
    """Symbol rows with file-local indices (row[0]; row[8] = parent's local
    index) -- turned into global row ids at insert time -- plus
    (local index, referenced name) edge rows."""
    lines = text.splitlines()
    seen: set[str] = set()
    by_qual: dict[str, int] = {}
    sym_rows, edge_rows = [], []
    for i, sym in enumerate(symbols):
        qual = sym["qualname"]
        dup = qual in seen  # @overload stubs, if/else defs, C++ overloads
        seen.add(qual)
        by_qual.setdefault(qual, i)
        sep = "::" if "::" in qual else "."
        parent = by_qual.get(qual.rsplit(sep, 1)[0]) if sep in qual else None
        tokens = estimate_tokens("\n".join(lines[sym["start_line"] - 1 : sym["end_line"]]))
        sym_rows.append(
            [
                i,
                sym["name"],
                qual,
                sym["type"],
                sym["start_line"],
                sym["end_line"],
                sym.get("doc", ""),
                tokens,
                parent,
                int(dup),
            ]
        )
        for name in sorted(set(sym.get("calls", []))):
            edge_rows.append((i, name))
    return sym_rows, edge_rows


def _discover(repo_root: Path) -> list[str]:
    """discover_files, minus symlinked files whose target is another indexed
    in-repo file (so the same content isn't indexed/selected twice)."""
    rel_paths = discover_files(repo_root)
    links = [r for r in rel_paths if (repo_root / r).is_symlink()]
    if not links:
        return rel_paths
    root_real = repo_root.resolve()
    direct = set(rel_paths) - set(links)
    seen_targets: set[Path] = set()
    skip = set()
    for rel in links:
        real = (repo_root / rel).resolve()
        if real.is_relative_to(root_real):
            if real.relative_to(root_real).as_posix() in direct or real in seen_targets:
                skip.add(rel)
                continue
        seen_targets.add(real)
    return [r for r in rel_paths if r not in skip]


# ---------------------------------------------------------------- build / update


def load_or_build_index(
    repo_root: Path, store_dir: Path, force: bool = False, parser: str = "auto"
) -> tuple[pr_store.Index, dict]:
    """Returns (index, report). Incremental: unchanged files (by mtime+size,
    then content hash) are neither re-read nor re-parsed."""
    store_dir.mkdir(parents=True, exist_ok=True)
    migrated = False
    legacy = store_dir / LEGACY_INDEX_FILENAME
    if legacy.exists():
        legacy.unlink()  # old JSON store: rebuilt below as sqlite
        migrated = True
    parser_used = resolve_parser(parser)
    conn = pr_store.connect(store_dir / DB_FILENAME)
    if (
        force
        or pr_store.get_meta(conn, "version") != str(INDEX_VERSION)
        or pr_store.get_meta(conn, "parser") != parser_used
    ):
        pr_store.reset(conn)

    existing = {
        row[0]: (row[1], row[2], row[3])
        for row in conn.execute("SELECT path, hash, mtime, size FROM files")
    }
    rel_paths = _discover(repo_root)
    reused = reparsed = 0
    touched: list[tuple] = []  # (rel, mtime, size) for hash-equal files with new stat
    new_files: dict[str, dict] = {}
    seen: set[str] = set()
    for rel in rel_paths:
        full = repo_root / rel
        try:
            st = full.stat()
        except OSError:
            continue
        old = existing.get(rel)
        if old and old[1] == st.st_mtime and old[2] == st.st_size:
            seen.add(rel)
            reused += 1
            continue
        text = read_text(full)
        if text is None:
            continue
        seen.add(rel)
        content_hash = sha256_text(text)
        if old and old[0] == content_hash:
            touched.append((st.st_mtime, st.st_size, rel))
            reused += 1
            continue
        reparsed += 1
        kind = classify_file(rel)
        language, parsed = _parse_source(rel, text, kind, parser_used)
        sym_rows, edge_rows = _symbol_rows(text, parsed["symbols"])
        new_files[rel] = {
            "row": [
                None,
                rel,
                content_hash,
                st.st_mtime,
                st.st_size,
                kind,
                language,
                estimate_tokens(text),
                len(text.splitlines()),
                int(parsed.get("parse_error", False)),
                json.dumps(parsed["imports"]),
                "[]",
            ],
            "symbols": sym_rows,
            "edges": edge_rows,
        }

    removed = set(existing) - seen  # deleted, or no longer readable
    file_set_changed = bool(removed) or any(r not in existing for r in new_files)

    if new_files or removed or touched:
        bulk = not existing and len(new_files) > 200
        if bulk:  # fresh store: build indexes once, after the inserts
            conn.executescript(pr_store.DROP_INDEXES)
        with conn:
            for rel in list(removed) + [r for r in new_files if r in existing]:
                (fid,) = conn.execute("SELECT id FROM files WHERE path=?", (rel,)).fetchone()
                conn.execute(
                    "DELETE FROM edges WHERE src IN (SELECT id FROM symbols WHERE file_id=?)",
                    (fid,),
                )
                conn.execute("DELETE FROM symbols WHERE file_id=?", (fid,))
                conn.execute("DELETE FROM files WHERE id=?", (fid,))
            conn.executemany("UPDATE files SET mtime=?, size=? WHERE path=?", touched)
            next_id = (conn.execute("SELECT max(id) FROM symbols").fetchone()[0] or 0) + 1
            for rec in new_files.values():
                fid = conn.execute(
                    "INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rec["row"]
                ).lastrowid
                for row in rec["symbols"]:
                    row[0] += next_id
                    row[8] = None if row[8] is None else row[8] + next_id
                    row.insert(1, fid)
                conn.executemany("INSERT INTO symbols VALUES (?,?,?,?,?,?,?,?,?,?,?)", rec["symbols"])
                conn.executemany(
                    "INSERT INTO edges VALUES (?,?)", ((i + next_id, n) for i, n in rec["edges"])
                )
                next_id += len(rec["symbols"])
            _resolve_imports(conn, None if file_set_changed else set(new_files))
            for key, value in (
                ("version", str(INDEX_VERSION)),
                ("parser", parser_used),
                ("repo_root", str(repo_root)),
                ("indexed_at", str(time.time())),
            ):
                conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
        if bulk:
            conn.executescript(pr_store.INDEXES)

    index = pr_store.Index(conn, repo_root)
    counts = index.counts()
    report = {
        "files_indexed": counts["files"],
        "files_unchanged": reused,
        "files_reparsed_or_new": reparsed,
        "files_removed": len(removed),
        "symbols": counts["symbols"],
        "call_edges": counts["edges"],
        "parser": parser_used,
        "migrated_legacy_json": migrated,
    }
    return index, report


def _resolve_imports(conn, only: set[str] | None) -> None:
    """Resolve imports/includes to in-repo files. Re-run for every file
    when the file set changed (a new file can change what an unchanged
    file's imports resolve to), otherwise only for re-parsed files."""
    rows = conn.execute("SELECT path, language, imports FROM files").fetchall()
    all_files = {r[0] for r in rows}
    py = _PyResolver(all_files)
    cpp = _CppResolver(all_files)
    updates = []
    for rel, language, imports in rows:
        if only is not None and rel not in only:
            continue
        resolver = py if language == "python" else cpp if language == "cpp" else None
        resolved = set()
        if resolver is not None:
            for imp in json.loads(imports or "[]"):
                target = resolver.resolve(imp, rel)
                if target and target != rel:
                    resolved.add(target)
        updates.append((json.dumps(sorted(resolved)), rel))
    conn.executemany("UPDATE files SET resolved_imports=? WHERE path=?", updates)
