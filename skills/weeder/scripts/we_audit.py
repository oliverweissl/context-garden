"""Measure step: token accounting per category/section, plus "likely
unnecessary content" flags (hedge/pleasantry filler, see we_filler.py;
background/example sections beyond a small cap). Combines we_parse's structural
classification and we_dupes' duplication detection into one report."""

from __future__ import annotations

from we_dupes import find_duplicates_in_skill
from we_filler import find_filler_sentences
from we_parse import parse_skill
from we_tokens import estimate_info, estimate_note, estimate_tokens

# Candidates to MOVE to references/, NOT already on-demand: the whole body
# loads on trigger until relocated. Don't exclude them from
# `on_trigger_body_tokens`, or it reports a reduction that hasn't happened.
MOVABLE_CATEGORIES = {"examples", "background", "external_reference"}


def audit_skill(skill_dir, dup_threshold: float = 0.6) -> dict:
    parsed = parse_skill(skill_dir)
    description = parsed["frontmatter"].get("description", "")
    description_tokens = estimate_tokens(description, "yaml")

    section_reports = []
    body_tokens = 0  # the whole body loads on trigger -- see MOVABLE_CATEGORIES note
    movable_tokens = 0
    for s in parsed["sections"]:
        tokens = estimate_tokens(s["content"])
        section_reports.append(
            {
                "heading": s["heading"],
                "category": s["category"],
                "tokens": tokens,
                "filler_sentences": find_filler_sentences(s["content"]),
            }
        )
        body_tokens += tokens
        if s["category"] in MOVABLE_CATEGORIES:
            movable_tokens += tokens

    reference_tokens = {name: estimate_tokens(text) for name, text in parsed["references"].items()}
    total_reference_tokens = sum(reference_tokens.values())

    duplicates = find_duplicates_in_skill(parsed, dup_threshold)
    duplicate_groups = duplicates["paragraphs"] + duplicates["sentences"]
    total_duplicate_tokens = sum(g["duplicate_tokens_estimate"] for g in duplicate_groups)

    likely_unnecessary = []
    for s in section_reports:
        if s["category"] in ("background", "examples") and s["tokens"] > 0:
            likely_unnecessary.append(
                {
                    "reason": (
                        f"category '{s['category']}' in SKILL.md -- candidate for moving to references/"
                    ),
                    "heading": s["heading"],
                    "tokens": s["tokens"],
                }
            )
        for f in s["filler_sentences"]:
            likely_unnecessary.append(
                {"reason": "generic filler phrasing", "heading": s["heading"], "text": f}
            )

    return {
        "skill_dir": str(skill_dir),
        "name": parsed["name"],
        "description_tokens": description_tokens,
        "description": description,
        "sections": section_reports,
        # always loaded = description only; body loads when the skill triggers
        "always_loaded_tokens": description_tokens,
        "on_trigger_body_tokens": body_tokens,
        "skill_md_tokens": description_tokens + body_tokens,
        "movable_tokens": movable_tokens,
        "reference_tokens": reference_tokens,
        "total_reference_tokens": total_reference_tokens,
        "duplicate_groups": duplicate_groups,
        "total_duplicate_tokens": total_duplicate_tokens,
        "likely_unnecessary": likely_unnecessary,
        "token_estimate": estimate_info(),
    }


def render_audit_human(report: dict) -> str:
    lines = [f"skill: {report['name']}  ({report['skill_dir']})", estimate_note(), ""]
    lines.append(
        f"Always-loaded (description): {report['always_loaded_tokens']:>5} tokens  "
        "(name+description, every session)"
    )
    lines.append(
        f"On-trigger (body):           {report['on_trigger_body_tokens']:>5} tokens  "
        "(all of the SKILL.md body -- loads in full when the skill triggers)"
    )
    lines.append(
        f"  of which movable:          {report['movable_tokens']:>5} tokens  (examples/background/external_reference sections -- not yet on-demand, but could be)"
    )
    lines.append(
        f"References:                  {report['total_reference_tokens']:>5} tokens  across {len(report['reference_tokens'])} file(s)  (genuinely on-demand already)"
    )
    lines.append("")
    lines.append("Sections:")
    for s in report["sections"]:
        heading = s["heading"] or "(intro, before first heading)"
        lines.append(f"  [{s['category']:<20}] {s['tokens']:>5} tok  {heading}")
    if report["duplicate_groups"]:
        lines.append("")
        lines.append(
            f"Duplicate content: {len(report['duplicate_groups'])} group(s), ~{report['total_duplicate_tokens']} redundant tokens"
        )
        for g in report["duplicate_groups"]:
            sources = ", ".join(sorted({m["source"] for m in g["members"]}))
            lines.append(
                f"  similarity={g['max_similarity']:.2f}  {len(g['members'])} occurrence(s) in: {sources}"
            )
            lines.append(
                f"    \"{g['members'][0]['text'][:100]}...\""
                if len(g["members"][0]["text"]) > 100
                else f"    \"{g['members'][0]['text']}\""
            )
    if report["likely_unnecessary"]:
        lines.append("")
        lines.append(f"Likely unnecessary content: {len(report['likely_unnecessary'])} item(s)")
        for u in report["likely_unnecessary"]:
            if "text" in u:
                lines.append(f"  - {u['reason']}: \"{u['text'][:80]}\"")
            else:
                lines.append(f"  - {u['reason']} ({u['tokens']} tok, heading: {u['heading']})")
    return "\n".join(lines)
