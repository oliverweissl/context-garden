"""Load solver outputs from files, so checks can run against results
produced outside Python (C++/Fortran/MPI/SLURM jobs) instead of a
callable.

    S.io.load("out/n64.npy")                         # .npy
    S.io.load("out/fields.npz", key="u")              # .npz (key needed if >1 array)
    S.io.load("out/profile.csv", key="u")             # CSV/TSV/whitespace text, column by name or index
    S.io.load("out/result.json", key="stats.error")   # JSON, dotted key path (list indices allowed)
    S.io.from_files("out/n{N}.npy")                   # -> callable(N) for convergence checks
    S.io.load_errors("out/errors.json", key="error")  # -> {N: error} precomputed per resolution

Relative paths are resolved against the current directory first and then
against the directory of the spec being run (`trellis run` sets
`io.BASE_DIR`), so a spec works both from the repo root and from its own
directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

BASE_DIR: Path | None = None  # set by the CLI to the spec's directory


def resolve(path) -> Path:
    """Resolve a result path: absolute / existing-from-cwd first, else relative to BASE_DIR."""
    p = Path(os.fspath(path)).expanduser()
    if p.is_absolute() or p.exists() or BASE_DIR is None:
        return p
    alt = Path(BASE_DIR) / p
    return alt if alt.exists() else p


def _descend(obj, key_path):
    if key_path is None or key_path == "":
        return obj
    parts = key_path if isinstance(key_path, (list, tuple)) else str(key_path).split(".")
    for part in parts:
        if isinstance(obj, list):
            obj = obj[int(part)]
        elif isinstance(obj, dict):
            if part not in obj:
                raise KeyError(f"key {part!r} not found (available: {sorted(obj)[:20]})")
            obj = obj[part]
        else:
            raise KeyError(f"cannot descend into {type(obj).__name__} with key {part!r}")
    return obj


def _sniff_delimiter(path: Path):
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            for d in (",", ";", "\t"):
                if d in s:
                    return d
            return None  # whitespace
    return None


def _load_text(path: Path, key=None):
    delim = _sniff_delimiter(path)
    header = None
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if s and not s.startswith("#"):
                first = [t.strip().strip('"') for t in (s.split(delim) if delim else s.split())]
                try:
                    [float(t) for t in first]
                except ValueError:
                    header = first
                break
    data = np.loadtxt(path, delimiter=delim, skiprows=1 if header else 0, comments="#", ndmin=1)
    if key is None:
        return data
    if data.ndim == 1:
        data = data[:, None] if header and len(header) == 1 else data[None, :]
    if isinstance(key, str) and not key.lstrip("-").isdigit():
        if not header or key not in header:
            raise KeyError(f"{path}: column {key!r} not found (header: {header})")
        return data[:, header.index(key)]
    return data[:, int(key)]


def load(path, key=None) -> np.ndarray:
    """Load an array/scalar from .npy, .npz (`key` = array name), CSV/TSV/
    whitespace text (`key` = column name or index; delimiter and header
    auto-detected), or JSON (`key` = dotted key path, e.g. "stats.error"
    or "levels.2.err"). Always returns an ndarray (0-d for scalars)."""
    p = resolve(path)
    if not p.exists():
        raise FileNotFoundError(f"trellis.io: no such result file: {p}")
    suffix = p.suffix.lower()
    if suffix == ".npy":
        arr = np.load(p, allow_pickle=False)
        return arr if key is None else np.asarray(_descend(arr.tolist(), key))
    if suffix == ".npz":
        with np.load(p, allow_pickle=False) as z:
            names = list(z.files)
            if key is None:
                if len(names) != 1:
                    raise KeyError(f"{p}: .npz holds {names}; pass key=<name>")
                key = names[0]
            if key not in names:
                raise KeyError(f"{p}: .npz has no array {key!r} (available: {names})")
            return np.asarray(z[key])
    if suffix == ".json":
        return np.asarray(_descend(json.loads(p.read_text()), key), dtype=float)
    return _load_text(p, key)  # CSV/TSV/whitespace text (any other suffix too)


def from_files(template, key=None):
    """-> callable(p) loading the file `template.format(N=p, n=p, h=p, dt=p, p=p)`,
    e.g. from_files("out/n{N}.npy") or from_files("run_dt{dt:g}/u.csv", key="u").
    Convergence checks also accept the template string directly in place
    of `solve_fn`/`reference`."""
    template = os.fspath(template)

    def _load(p):
        return load(template.format(N=p, n=p, h=p, dt=p, p=p), key=key)

    _load.template = template  # recorded in the check's config
    return _load


_PARAM_KEYS = ("N", "n", "resolution", "resolutions", "h", "dt", "dts", "param", "params")


def load_errors(path, key=None, root=None, param_key=None) -> dict:
    """Precomputed errors per resolution -> {param (float): error (float)}.
    Accepted layouts (JSON, after descending the dotted `root` path):

      {"8": 0.01, "16": 0.0025}                        # mapping param -> error
      {"8": {"error": 0.01, ...}, ...}                 # mapping param -> record (key="error")
      [{"N": 8, "error": 0.01}, ...]                   # list of records (param_key auto: N/n/h/dt/...)
      {"N": [8, 16], "errors": [0.01, 0.0025]}         # parallel lists (key defaults to "errors")

    or a 2-column CSV/text file (param, error; header optional)."""
    p = resolve(path)
    if p.suffix.lower() != ".json":
        data = np.atleast_2d(_load_text(p))
        if data.shape[1] < 2:
            raise ValueError(f"{p}: need two columns (param, error)")
        return {float(r[0]): float(r[1]) for r in data}
    obj = _descend(json.loads(p.read_text()), root)
    if isinstance(obj, list):
        out = {}
        for rec in obj:
            pk = param_key or next((k for k in _PARAM_KEYS if k in rec), None)
            if pk is None:
                raise KeyError(f"{p}: record {rec} has no param key; pass param_key=")
            out[float(rec[pk])] = float(_descend(rec, key or "error"))
        return out
    if isinstance(obj, dict):
        pk = param_key or next((k for k in _PARAM_KEYS if k in obj and isinstance(obj[k], list)), None)
        if pk is not None:
            vals = _descend(obj, key or "errors")
            return {float(a): float(e) for a, e in zip(obj[pk], vals)}
        out = {}
        for k, v in obj.items():
            try:
                param = float(k)
            except ValueError:
                continue
            out[param] = float(_descend(v, key) if isinstance(v, (dict, list)) else v)
        if not out:
            raise ValueError(f"{p}: no numeric param keys found at root {root!r}")
        return out
    raise ValueError(f"{p}: unsupported errors layout ({type(obj).__name__})")
