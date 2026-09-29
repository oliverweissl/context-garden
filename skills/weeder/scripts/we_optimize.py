"""Move `background`/`examples` SKILL.md sections into references/<slug>.md,
leaving a one-line pointer. Automated only because it is lossless; description
and duplicate rewrites need judgment and are left to the agent.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from we_parse import FRONTMATTER_RE, parse_skill
from we_tokens import estimate_tokens

DEFAULT_MOVE_CATEGORIES = ("background", "examples")
MIN_TOKENS_TO_MOVE = 20  # not worth a whole reference file + pointer for a one-liner


def slugify(heading: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
    return s or "section"


def _unique_slug(base: str, taken: set[str]) -> str:
    slug = base
    n = 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    taken.add(slug)
    return slug


def render_section(s: dict) -> str:
    if s["heading"] is None:
        return s["content"]
    hashes = "#" * s["level"]
    body = s["content"]
    return f"{hashes} {s['heading']}\n\n{body}" if body else f"{hashes} {s['heading']}"


def check_out_dir(skill_dir: Path, out_dir: Path) -> None:
    """Refuse an --out that is, contains, or lives inside the source skill
    dir: the output is written by deleting out_dir and re-copying
    skill_dir into it, which would otherwise destroy the source."""
    src = Path(skill_dir).resolve()
    dst = Path(out_dir).resolve()
    if dst == src or dst.is_relative_to(src) or src.is_relative_to(dst):
        raise ValueError(
            f"--out {out_dir} overlaps the source skill dir {skill_dir}; "
            "pick an output dir outside it (and not containing it)"
        )


def rebuild_skill_md(frontmatter_block: str, sections: list[dict]) -> str:
    parts = [frontmatter_block.rstrip("\n")]
    for s in sections:
        rendered = render_section(s)
        if rendered.strip():
            parts.append(rendered)
    return "\n\n".join(parts) + "\n"


def optimize_skill(
    skill_dir,
    out_dir,
    move_categories=DEFAULT_MOVE_CATEGORIES,
    min_tokens_to_move: int = MIN_TOKENS_TO_MOVE,
) -> dict:
    skill_dir = Path(skill_dir)
    out_dir = Path(out_dir)
    check_out_dir(skill_dir, out_dir)
    parsed = parse_skill(skill_dir)

    fm_match = FRONTMATTER_RE.match(parsed["raw_skill_md"])
    frontmatter_block = fm_match.group(0) if fm_match else ""

    existing_ref_names = {Path(n).stem for n in parsed["references"]}
    taken_slugs = set(existing_ref_names)
    new_reference_files: dict[str, str] = {}
    moves = []

    new_sections = []
    for s in parsed["sections"]:
        tokens = estimate_tokens(s["content"])
        if (
            s["category"] in move_categories
            and s["heading"] is not None
            and tokens >= min_tokens_to_move
        ):
            slug = _unique_slug(slugify(s["heading"]), taken_slugs)
            filename = f"{slug}.md"
            new_reference_files[filename] = render_section(s) + "\n"
            pointer = f"See `references/{filename}`."
            new_sections.append({**s, "content": pointer})
            moves.append(
                {"heading": s["heading"], "tokens_moved": tokens, "to": f"references/{filename}"}
            )
        else:
            new_sections.append(s)

    new_skill_md = rebuild_skill_md(frontmatter_block, new_sections)

    # write output: copy the whole original skill dir, then overwrite SKILL.md and add new reference files
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(skill_dir, out_dir)
    (out_dir / "SKILL.md").write_text(new_skill_md)
    (out_dir / "references").mkdir(exist_ok=True)
    for filename, content in new_reference_files.items():
        (out_dir / "references" / filename).write_text(content)

    # Re-audit both dirs rather than re-deriving per-trigger cost here, so
    # we_audit stays the single source of truth for that figure.
    from we_audit import audit_skill

    before_skill_md = audit_skill(skill_dir)["skill_md_tokens"]
    after_skill_md = audit_skill(out_dir)["skill_md_tokens"]

    return {
        "skill_dir": str(skill_dir),
        "out_dir": str(out_dir),
        "moves": moves,
        "before_skill_md_tokens": before_skill_md,
        "after_skill_md_tokens": after_skill_md,
        "reduction_pct": (
            round(100 * (1 - after_skill_md / before_skill_md), 1) if before_skill_md else 0.0
        ),
    }
