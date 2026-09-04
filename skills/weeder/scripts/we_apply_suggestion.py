"""Applies an agent-authored answer to weeder's suggest requests (see
we_suggest.py) mechanically -- no judgment happens in this module, only in
the answer file's content, which the calling agent supplies. Reuses the
copy-then-overwrite pattern from we_optimize.optimize_skill so
`apply-suggestion`'s output can be fed straight into the same
test-routing/test-function/diff gate as `optimize`'s output; SKILL.md
workflow steps 4-7 (validate before accepting) are unchanged either way.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from we_audit import audit_skill

_DESCRIPTION_LINE_RE = re.compile(r"^description:.*$", re.MULTILINE)


def _whitespace_pattern(text: str) -> re.Pattern:
    """Matches `text` against file content ignoring exact whitespace: a
    duplicate-group member's text may have had internal newlines flattened
    to single spaces during sentence-splitting (we_text.split_into_sentences),
    so a literal substring match against the raw file can miss it even
    though the words are identical and contiguous in the source."""
    parts = [re.escape(w) for w in text.split()]
    return re.compile(r"\s+".join(parts))


def _target_path(out_dir: Path, source: str) -> Path:
    if source.startswith("references/"):
        return out_dir / source
    return out_dir / "SKILL.md"  # source looks like "SKILL.md#<heading>"


def _apply_description(out_dir: Path, new_description: str) -> None:
    skill_md = out_dir / "SKILL.md"
    text = skill_md.read_text()
    new_text, n = _DESCRIPTION_LINE_RE.subn(f"description: {new_description}", text, count=1)
    if n == 0:
        raise ValueError("could not find a `description:` line in SKILL.md frontmatter to replace")
    skill_md.write_text(new_text)


def _apply_duplicate_consolidation(out_dir: Path, report: dict, consolidations: list[dict]) -> list[str]:
    """"State it once, delete the rest" (SKILL.md step 3) -- the member at
    `keep_member_index` gets rewritten to `canonical_text` in place; every
    other member in the group is deleted entirely from its own location,
    rather than every occurrence being replaced with identical repeated
    text (which would turn a near-duplicate into an exact one, the
    opposite of consolidating it). Indexed by position, not `source`,
    since two members can share the same source label (e.g. two sentences
    in the same section)."""
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
            warnings.append(f"duplicate_consolidation: keep_member_index {keep_idx} out of range for group {idx}, skipped")
            continue
        canonical = c["canonical_text"]
        for member_idx, member in enumerate(members):
            path = _target_path(out_dir, member["source"])
            text = path.read_text()
            replacement = canonical if member_idx == keep_idx else ""
            new_text, n = _whitespace_pattern(member["text"]).subn(replacement, text, count=1)
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
        new_text, n = _whitespace_pattern(r["text_to_remove"]).subn("", text, count=1)
        if n == 0:
            warnings.append(f"unnecessary_removal: could not find {r['text_to_remove']!r} verbatim, skipped")
            continue
        path.write_text(new_text)
    return warnings


def apply_suggestion(skill_dir, out_dir, answer: dict, dup_threshold: float = 0.6) -> dict:
    skill_dir = Path(skill_dir)
    out_dir = Path(out_dir)
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
        warnings += _apply_duplicate_consolidation(out_dir, report, answer["duplicate_consolidation"])
        applied.append("duplicate_consolidation")

    if "unnecessary_removal" in answer:
        warnings += _apply_unnecessary_removal(out_dir, answer["unnecessary_removal"])
        applied.append("unnecessary_removal")

    return {"skill_dir": str(skill_dir), "out_dir": str(out_dir), "applied": applied, "warnings": warnings}
