"""Structural parsing/classification of a skill directory: frontmatter,
SKILL.md sections (by H1/H2 heading), and reference files.

Classification is heading-keyword based, not semantic -- a section titled
"Examples" is classified `examples` regardless of what's actually in it.
This is a deliberate, documented approximation (see
references/classification.md): it's cheap, deterministic, and good enough
to drive progressive-disclosure refactoring, but a well-named heading with
off-topic content, or vice versa, will be misclassified.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,2})\s+(.*)$")

CATEGORIES = (
    "routing_metadata",
    "mandatory_instructions",
    "workflow",
    "constraints",
    "examples",
    "background",
    "external_reference",
)

_CATEGORY_PATTERNS = [
    ("examples", re.compile(r"\bexamples?\b", re.I)),
    ("background", re.compile(r"\b(background|motivation|why|rationale|history)\b", re.I)),
    ("external_reference", re.compile(r"\b(references?|see also|further reading|links?)\b", re.I)),
    (
        "constraints",
        re.compile(r"\b(constraints?|rules?|invariants?|guarantees?|safety|limitations?)\b", re.I),
    ),
    ("workflow", re.compile(r"\b(workflow|steps?|process|usage|how to|getting started)\b", re.I)),
]


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal frontmatter parser: single-line `key: value` pairs, with
    continuation lines (no leading `key:`) folded into the previous value.
    Not a general YAML parser -- sufficient for this suite's SKILL.md
    frontmatter (name/description only)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_text = m.group(1)
    body = text[m.end() :]
    fm: dict[str, str] = {}
    current_key = None
    for line in fm_text.splitlines():
        kv = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if kv:
            current_key = kv.group(1)
            fm[current_key] = kv.group(2).strip()
        elif current_key and line.strip():
            fm[current_key] += " " + line.strip()
    return fm, body


def split_sections(body: str) -> list[dict]:
    """Splits on H1/H2 headings only (H3+ stays nested inside its parent
    section's content, since finer granularity isn't needed for token
    accounting or progressive-disclosure moves at this scale)."""
    lines = body.splitlines()
    sections = []
    current = {"level": 0, "heading": None, "lines": []}
    for line in lines:
        m = HEADING_RE.match(line)
        if m:
            if current["lines"] or current["heading"] is not None:
                sections.append(current)
            current = {"level": len(m.group(1)), "heading": m.group(2).strip(), "lines": []}
        else:
            current["lines"].append(line)
    if current["lines"] or current["heading"] is not None:
        sections.append(current)
    for s in sections:
        s["content"] = "\n".join(s["lines"]).strip()
        del s["lines"]
    return sections


def classify_section(heading: str | None) -> str:
    if heading is None:
        return "mandatory_instructions"
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(heading):
            return category
    return "mandatory_instructions"


def parse_skill(skill_dir: Path) -> dict:
    skill_dir = Path(skill_dir)
    skill_md_path = skill_dir / "SKILL.md"
    raw = skill_md_path.read_text()
    frontmatter, body = parse_frontmatter(raw)
    sections = split_sections(body)
    for s in sections:
        s["category"] = classify_section(s["heading"])

    references = {}
    ref_dir = skill_dir / "references"
    if ref_dir.exists():
        for f in sorted(ref_dir.glob("*.md")):
            references[f.name] = f.read_text()

    return {
        "skill_dir": str(skill_dir),
        "name": frontmatter.get("name", skill_dir.name),
        "frontmatter": frontmatter,
        "sections": sections,
        "references": references,
        "raw_skill_md": raw,
        "raw_body": body,
    }
