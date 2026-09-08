"""Near-duplicate paragraph detection: normalized-word Jaccard similarity,
deterministic, no LLM. Same clustering spirit as compost's error
grouping -- group things that say the same thing, regardless of exact
wording, so a rule repeated in five places (spec's UC2) collapses to one
finding instead of five separate paragraphs to eyeball."""

from __future__ import annotations

import re
from itertools import combinations

from we_text import split_into_sentences, strip_code
from we_tokens import estimate_tokens

MIN_WORDS = 8
MIN_SENTENCE_WORDS = 6
DEFAULT_THRESHOLD = 0.6
DEFAULT_SENTENCE_THRESHOLD = 0.45
MIN_SHARED_PHRASE_WORDS = 6
_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize_words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _word_seq(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def longest_common_run(a: list[str], b: list[str]) -> int:
    """Longest common *contiguous* word run (classic longest-common-
    substring DP, at word granularity). This is what actually catches "the
    same rule restated with different surrounding elaboration": whole-
    sentence Jaccard penalizes the differing elaboration so heavily (it
    inflates the union) that two sentences sharing a clear 8-10 word
    imperative core can still fall below any reasonable Jaccard threshold
    once each has a different justifying clause tacked on. A shared
    contiguous run is a much more direct signal of "this is the same
    rule", independent of how much unrelated text surrounds it."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        curr = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
                best = max(best, curr[j])
        prev = curr
    return best


def split_paragraphs(text: str, source: str) -> list[dict]:
    """Fenced code blocks are stripped before splitting: a CLI usage
    example's command syntax legitimately repeats across sections (e.g.
    every subcommand's usage block starts with the same `python3 .../x.py`
    prefix) and is not the kind of duplicated *rule* this is looking for
    -- left in, it's a reliable source of false positives (caught by
    running this tool on its own SKILL.md)."""
    cleaned = strip_code(text)
    paras = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    out = []
    for p in paras:
        if len(p.split()) < MIN_WORDS:
            continue
        out.append({"text": p, "source": source, "words": _normalize_words(p)})
    return out


def split_sentences(text: str, source: str) -> list[dict]:
    """Complements split_paragraphs: a rule restated inside otherwise-
    different surrounding prose (spec's UC2 -- "the same command
    restriction appears in five sections", each phrased a bit differently
    and surrounded by different context) dilutes below the paragraph-level
    threshold even when the core sentence is a near-exact repeat. Sentence
    granularity catches that; paragraph granularity catches large repeated
    blocks. Both run, results in we_dupes.find_duplicates_in_skill are
    reported separately. See we_text.split_into_sentences for why code
    blocks/headings/list markers are stripped first."""
    out = []
    for s in split_into_sentences(text):
        if len(s.split()) < MIN_SENTENCE_WORDS:
            continue
        out.append({"text": s, "source": source, "words": _normalize_words(s), "seq": _word_seq(s)})
    return out


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def find_duplicate_groups(
    paragraphs: list[dict],
    threshold: float = DEFAULT_THRESHOLD,
    phrase_min_words: int | None = None,
) -> list[dict]:
    """`phrase_min_words`: if given, a pair is ALSO considered a duplicate
    when they share a contiguous word run of at least this length, even if
    their overall Jaccard similarity is below `threshold` (see
    longest_common_run's docstring for why -- use this for sentence-level
    matching, where "same rule, different surrounding elaboration" is
    common; leave None for paragraph-level, where a whole-block match is
    the actual thing being looked for)."""
    n = len(paragraphs)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    max_sim = {}
    for i, j in combinations(range(n), 2):
        sim = jaccard(paragraphs[i]["words"], paragraphs[j]["words"])
        is_dup = sim >= threshold
        if not is_dup and phrase_min_words is not None:
            run = longest_common_run(paragraphs[i].get("seq", []), paragraphs[j].get("seq", []))
            if run >= phrase_min_words:
                is_dup = True
                sim = max(
                    sim, run / max(1, min(len(paragraphs[i]["seq"]), len(paragraphs[j]["seq"])))
                )
        if is_dup:
            union(i, j)
            max_sim[i] = max(max_sim.get(i, 0.0), sim)
            max_sim[j] = max(max_sim.get(j, 0.0), sim)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    return [
        {
            "members": [
                {"text": paragraphs[i]["text"], "source": paragraphs[i]["source"]} for i in idxs
            ],
            "max_similarity": round(max(max_sim.get(i, 0.0) for i in idxs), 3),
            "duplicate_tokens_estimate": _estimate_dup_tokens(paragraphs, idxs),
        }
        for idxs in groups.values()
        if len(idxs) > 1
    ]


def _estimate_dup_tokens(paragraphs: list[dict], idxs: list[int]) -> int:
    """Tokens that would be saved by keeping only the first occurrence."""
    sizes = sorted((estimate_tokens(paragraphs[i]["text"]) for i in idxs), reverse=True)
    return sum(sizes[1:])  # keep the largest as canonical, the rest are "duplicate"


def find_duplicates_in_skill(
    parsed: dict,
    threshold: float = DEFAULT_THRESHOLD,
    sentence_threshold: float = DEFAULT_SENTENCE_THRESHOLD,
) -> dict:
    """Pulls content from every SKILL.md section plus every reference file,
    so duplication is caught both within SKILL.md and between SKILL.md and
    references (or between two references). Returns both granularities:
    `paragraphs` (large repeated blocks) and `sentences` (a rule restated
    inside otherwise-different surrounding prose -- spec's UC2 shape)."""
    paragraphs, sentences = [], []
    for s in parsed["sections"]:
        label = f"SKILL.md#{s['heading'] or 'intro'}"
        paragraphs.extend(split_paragraphs(s["content"], label))
        sentences.extend(split_sentences(s["content"], label))
    for name, text in parsed["references"].items():
        paragraphs.extend(split_paragraphs(text, f"references/{name}"))
        sentences.extend(split_sentences(text, f"references/{name}"))

    paragraph_groups = find_duplicate_groups(paragraphs, threshold)
    sentence_groups = find_duplicate_groups(
        sentences, sentence_threshold, phrase_min_words=MIN_SHARED_PHRASE_WORDS
    )
    # a sentence group whose members are already fully covered by a
    # reported paragraph group is redundant noise -- drop it
    paragraph_texts = {m["text"] for g in paragraph_groups for m in g["members"]}
    sentence_groups = [
        g
        for g in sentence_groups
        if not all(any(m["text"] in p for p in paragraph_texts) for m in g["members"])
    ]
    return {"paragraphs": paragraph_groups, "sentences": sentence_groups}
