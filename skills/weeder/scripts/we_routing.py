"""Lexical PROXY for "would a model pick this skill for this prompt": good for
catching before/after regressions, not for borderline cases (confirm those
with a real agent). See references/routing-heuristic.md.
"""

from __future__ import annotations

import re

_STOPWORDS = {
    "the",
    "a",
    "an",
    "in",
    "on",
    "of",
    "to",
    "for",
    "and",
    "or",
    "is",
    "are",
    "this",
    "that",
    "with",
    "when",
    "please",
    "need",
    "want",
    "use",
    "using",
    "help",
    "me",
    "my",
    "it",
    "be",
    "at",
    "by",
    "from",
    "you",
    "your",
    "i",
    "can",
    "would",
    "like",
    "some",
    "any",
}
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z]|$)")


def _split_identifier(token: str) -> list[str]:
    return [m.lower() for m in _CAMEL_RE.findall(token) if m]


# Below this, a best match is too weak to count as a trigger -- matters
# most with no --competing descriptions, where anything nonzero would
# otherwise "win" by default.
MIN_SCORE = 0.2


def _stem(word: str) -> str:
    """Light suffix stripping so "files"/"file", "deleting"/"delete"
    match; not a real stemmer (see routing-heuristic.md)."""
    for suffix, min_len in (("ing", 6), ("ed", 5), ("es", 5), ("s", 4)):
        if word.endswith(suffix) and len(word) >= min_len and not word.endswith("ss"):
            word = word[: -len(suffix)]
            break
    return word.rstrip("e") if len(word) > 4 else word


def extract_keywords(text: str) -> set[str]:
    words = _WORD_RE.findall(text or "")
    out = set()
    for w in words:
        out.add(w.lower())
        out.update(_split_identifier(w))
    return {_stem(w) for w in out if len(w) > 1 and w not in _STOPWORDS}


def relevance_score(description: str, prompt: str) -> float:
    """Fraction of the prompt's keywords the description contains (not Jaccard,
    so a longer description isn't penalised)."""
    desc_kw = extract_keywords(description)
    prompt_kw = extract_keywords(prompt)
    if not desc_kw or not prompt_kw:
        return 0.0
    return len(desc_kw & prompt_kw) / len(prompt_kw)


def predict_trigger(
    descriptions: dict[str, str], prompt: str
) -> tuple[str | None, dict[str, float]]:
    """`descriptions`: {skill_name: description_text}, at minimum the
    skill under test plus any --competing descriptions supplied. Returns
    (winning_skill_name_or_None, all_scores). None if the best score is
    below MIN_SCORE (nothing matched meaningfully)."""
    scores = {name: relevance_score(desc, prompt) for name, desc in descriptions.items()}
    if not scores:
        return None, scores
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < MIN_SCORE:
        return None, scores
    return best, scores


def evaluate_routing(
    skill_name: str, skill_description: str, examples: dict, competing: dict[str, str] | None = None
) -> dict:
    """`examples`: {"positive": [prompt, ...], "negative": [prompt, ...],
    "ambiguous": [{"prompt":, "expected": "trigger"|"no_trigger"|"either"}]}."""
    competing = competing or {}
    all_descriptions = {skill_name: skill_description, **competing}
    results = []

    for prompt in examples.get("positive", []):
        best, scores = predict_trigger(all_descriptions, prompt)
        results.append(_row(prompt, "positive", "trigger", best, scores, best == skill_name))

    for prompt in examples.get("negative", []):
        best, scores = predict_trigger(all_descriptions, prompt)
        results.append(_row(prompt, "negative", "no_trigger", best, scores, best != skill_name))

    for item in examples.get("ambiguous", []):
        prompt, expected = item["prompt"], item.get("expected", "either")
        best, scores = predict_trigger(all_descriptions, prompt)
        if expected == "either":
            correct = True
        elif expected == "trigger":
            correct = best == skill_name
        else:
            correct = best != skill_name
        results.append(_row(prompt, "ambiguous", expected, best, scores, correct))

    n = len(results)
    accuracy = (sum(r["correct"] for r in results) / n) if n else None
    return {"accuracy": accuracy, "n": n, "results": results}


def _row(prompt, kind, expected, predicted, scores, correct) -> dict:
    return {
        "prompt": prompt,
        "kind": kind,
        "expected": expected,
        "predicted": predicted,
        "correct": bool(correct),
        "top_scores": dict(sorted(scores.items(), key=lambda kv: -kv[1])[:3]),
    }
