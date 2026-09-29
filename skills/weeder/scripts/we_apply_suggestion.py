"""Mechanically applies an agent-authored answer to `suggest` requests (all
judgment lives in the answer file). Writes a copy like `optimize`, so the
output goes through the same test-routing/test-function/diff gate.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from we_audit import audit_skill
from we_optimize import check_out_dir
from we_parse import FRONTMATTER_KEY_RE, FRONTMATTER_RE


def _whitespace_pattern(text: str) -> re.Pattern:
    """Whitespace-insensitive match: sentence-splitting flattens newlines in a
    member's text, so a literal substring search can miss it."""
    parts = [re.escape(w) for w in text.split()]
    return re.compile(r"\s+".join(parts))


def _target_path(out_dir: Path, source: str) -> Path:
    if source.startswith("references/"):
        return out_dir / source
    return out_dir / "SKILL.md"  # source looks like "SKILL.md#<heading>"


def _yaml_double_quoted(text: str) -> str:
    """Always-valid YAML scalar for arbitrary agent text (": ", "#",
    leading quotes, backslashes...): flattened to one line, double-quoted,
    with backslashes and double quotes escaped."""
    flat = " ".join(text.split())
    return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _apply_description(out_dir: Path, new_description: str) -> None:
    """Replaces the whole `description:` value in the frontmatter --
    including folded/literal block scalars (`>`/`|`) and plain/quoted
    values continued over several lines -- not just its first line."""
    if not isinstance(new_description, str) or not new_description.strip():
        raise ValueError("description_shorten.new_description must be a non-empty string")
    skill_md = out_dir / "SKILL.md"
    text = skill_md.read_text()
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("SKILL.md has no frontmatter to replace a `description:` in")
    lines = m.group(1).split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith("description:")), None)
    if start is None:
        raise ValueError("could not find a `description:` line in SKILL.md frontmatter to replace")
    end = start + 1
    while end < len(lines) and not FRONTMATTER_KEY_RE.match(lines[end]):
        end += 1  # continuation lines of the same value, up to the next top-level key
    lines[start:end] = [f"description: {_yaml_double_quoted(new_description)}"]
    skill_md.write_text(text[: m.start(1)] + "\n".join(lines) + text[m.end(1) :])


def _apply_duplicate_consolidation(
    out_dir: Path, report: dict, consolidations: list[dict]
) -> list[str]:
    """Rewrite the kept member to `canonical_text`, delete the others (replacing
    all would just make exact duplicates). Indexed by position, not `source`:
    two members can share a source label."""
    warnings = []
    groups = report["duplicate_groups"]
    for c in consolidations:
        idx = c["group_index"]
        if idx < 0 or idx >= len(groups):
            warnings.append(f"duplicate_consolidation: group_index {idx} out of range, skipped")
            continue
        members = groups[idx]["members"]
        keep_idx = c["keep_member_index"]
        if keep_idx < 0 or keep_idx >= len(members):
            warnings.append(
                f"duplicate_consolidation: keep_member_index {keep_idx} out of range for group {idx}, skipped"
            )
            continue
        canonical = c["canonical_text"]
        for member_idx, member in enumerate(members):
            path = _target_path(out_dir, member["source"])
            text = path.read_text()
            replacement = canonical if member_idx == keep_idx else ""
            new_text, n = _whitespace_pattern(member["text"]).subn(
                lambda _m, r=replacement: r, text, count=1
            )
            if n == 0:
                warnings.append(
                    f"duplicate_consolidation: could not find group {idx} member {member_idx}'s text "
                    f"verbatim in {member['source']} (it may contain inline code stripped during "
                    "detection), skipped"
                )
                continue
            path.write_text(new_text)
    return warnings


def _apply_unnecessary_removal(out_dir: Path, removals: list[dict]) -> list[str]:
    warnings = []
    path = out_dir / "SKILL.md"
    for r in removals:
        text = path.read_text()
        new_text, n = _whitespace_pattern(r["text_to_remove"]).subn(lambda _m: "", text, count=1)
        if n == 0:
            warnings.append(
                f"unnecessary_removal: could not find {r['text_to_remove']!r} verbatim, skipped"
            )
            continue
        path.write_text(new_text)
    return warnings


def apply_suggestion(skill_dir, out_dir, answer: dict, dup_threshold: float = 0.6) -> dict:
    skill_dir = Path(skill_dir)
    out_dir = Path(out_dir)
    check_out_dir(skill_dir, out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(skill_dir, out_dir)

    applied = []
    warnings: list[str] = []

    if "description_shorten" in answer:
        _apply_description(out_dir, answer["description_shorten"]["new_description"])
        applied.append("description_shorten")

    if "duplicate_consolidation" in answer:
        report = audit_skill(skill_dir, dup_threshold=dup_threshold)
        warnings += _apply_duplicate_consolidation(
            out_dir, report, answer["duplicate_consolidation"]
        )
        applied.append("duplicate_consolidation")

    if "unnecessary_removal" in answer:
        warnings += _apply_unnecessary_removal(out_dir, answer["unnecessary_removal"])
        applied.append("unnecessary_removal")

    return {
        "skill_dir": str(skill_dir),
        "out_dir": str(out_dir),
        "applied": applied,
        "warnings": warnings,
    }
