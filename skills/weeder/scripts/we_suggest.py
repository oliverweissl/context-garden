"""Shapes we_audit's audit_skill() output into structured, assist-level-
gated judgment requests for SKILL.md workflow step 3. This never contacts
an LLM or the network -- a "request" is just structured framing of exactly
the information an agent doing step 3 freehand already reads from `weeder
audit`. The `--assist` level controls how many of these requests exist,
not their content: `none` produces zero (do step 3 freehand, as before),
`slight` structures only the description rewrite, `lot` also structures
duplicate-rule consolidation and obvious-content removal. See
references/llm-assist.md for the full request/answer schema."""

from __future__ import annotations

from we_audit import audit_skill


def _description_shorten_request(report: dict) -> dict:
    return {
        "kind": "description_shorten",
        "current_description": report["description"],
        "current_tokens": report["description_tokens"],
        "instructions": (
            "Draft 2-3 candidate shorter descriptions for this skill's frontmatter. Cut "
            'generic phrasing ("this skill helps you with...", "use this whenever you '
            'need..."); keep concrete trigger nouns/verbs a real prompt would contain. Pick '
            'one and answer with {"description_shorten": {"new_description": "..."}}.'
        ),
    }


def _duplicate_consolidation_request(report: dict) -> dict:
    groups = [
        {
            "group_index": i,
            "max_similarity": g["max_similarity"],
            "members": [{"member_index": j, **m} for j, m in enumerate(g["members"])],
        }
        for i, g in enumerate(report["duplicate_groups"])
    ]
    return {
        "kind": "duplicate_consolidation",
        "groups": groups,
        "instructions": (
            "For each group worth consolidating: pick the clearest wording, pick which "
            "member (by member_index -- two members can share the same `source`, e.g. two "
            "sentences in the same section, so index rather than source identifies one) "
            "should keep it, and state it once there -- delete the rest, don't repeat it "
            "verbatim in every location (that would just turn a near-duplicate into an exact "
            'one). Answer with {"duplicate_consolidation": [{"group_index": <int>, '
            '"keep_member_index": <int>, "canonical_text": "..."}, ...]}. Omit groups '
            "you'd rather leave as-is (e.g. legitimate parallel structure)."
        ),
    }


def _unnecessary_removal_request(report: dict) -> dict:
    items = [u for u in report["likely_unnecessary"] if "text" in u]
    return {
        "kind": "unnecessary_removal",
        "items": items,
        "instructions": (
            "Only include items that are genuinely obvious to a capable agent -- don't cut "
            'something just because it\'s short. Answer with {"unnecessary_removal": '
            '[{"heading": "...", "text_to_remove": "..."}, ...]} for the ones actually '
            "worth removing."
        ),
    }


def build_suggestions(skill_dir, level: str, dup_threshold: float = 0.6) -> dict:
    report = audit_skill(skill_dir, dup_threshold=dup_threshold)
    requests = []
    if level in ("slight", "lot"):
        requests.append(_description_shorten_request(report))
    if level == "lot":
        requests.append(_duplicate_consolidation_request(report))
        requests.append(_unnecessary_removal_request(report))
    return {"skill_dir": str(skill_dir), "assist_level": level, "requests": requests}


def render_suggestions_human(result: dict) -> str:
    if not result["requests"]:
        return (
            f"assist level: {result['assist_level']} -- no structured judgment request generated.\n"
            "Do SKILL.md workflow step 3 freehand: read `weeder audit`'s duplicate_groups and "
            "likely_unnecessary sections and edit the -optimized copy directly."
        )
    lines = [f"assist level: {result['assist_level']} -- {len(result['requests'])} request(s)", ""]
    for r in result["requests"]:
        lines.append(f"[{r['kind']}]")
        lines.append(r["instructions"])
        lines.append("")
    lines.append(
        "Write your answer(s) to a JSON file merging each kind's shape above, then run "
        "`weeder apply-suggestion <skill_dir> --answer <file>`."
    )
    return "\n".join(lines)
