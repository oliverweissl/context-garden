"""Routing test harness: a deterministic, offline PROXY for "would a real
model pick this skill for this prompt" -- not a replacement for it. See
references/routing-heuristic.md for exactly what this does and doesn't
predict, and why (same "documented lexical approximation" pattern as
pruner's relevance scoring, for the same reason: no embeddings/LLM
calls anywhere in this suite's deterministic tooling).

Use this to catch REGRESSIONS between a before/after description (did the
shortened description clearly lose a positive-trigger keyword?) and for a
fast first pass across many labeled examples. Confirm genuinely
borderline/ambiguous cases with a real agent before trusting a rewrite.
"""
from __future__ import annotations

import re

_STOPWORDS = {
    "the", "a", "an", "in", "on", "of", "to", "for", "and", "or", "is",
    "are", "this", "that", "with", "when", "please", "need", "want",
    "use", "using", "help", "me", "my", "it", "be", "at", "by", "from",
    "you", "your", "i", "can", "would", "like", "some", "any",
}
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z]|$)")


def _split_identifier(token: str) -> list[str]:
    return [m.lower() for m in _CAMEL_RE.findall(token) if m]


def extract_keywords(text: str) -> set[str]:
    words = _WORD_RE.findall(text or "")
    out = set()
    for w in words:
        out.add(w.lower())
        out.update(_split_identifier(w))
    return {w for w in out if len(w) > 1 and w not in _STOPWORDS}


def relevance_score(description: str, prompt: str) -> float:
    """Symmetric keyword-overlap (Jaccard) between a skill description and
    a candidate prompt. Deliberately simple and explainable over a more
    "accurate" asymmetric/weighted scheme -- see routing-heuristic.md."""
    desc_kw = extract_keywords(description)
    prompt_kw = extract_keywords(prompt)
    if not desc_kw or not prompt_kw:
        return 0.0
    union = len(desc_kw | prompt_kw)
    return len(desc_kw & prompt_kw) / union if union else 0.0


def predict_trigger(descriptions: dict[str, str], prompt: str) -> tuple[str | None, dict[str, float]]:
    """`descriptions`: {skill_name: description_text}, at minimum the
    skill under test plus any --competing descriptions supplied. Returns
    (winning_skill_name_or_None, all_scores). None if every score is 0
    (nothing matched at all)."""
    scores = {name: relevance_score(desc, prompt) for name, desc in descriptions.items()}
    if not scores:
        return None, scores
    best = max(scores, key=lambda k: scores[k])
    if scores[best] <= 0.0:
        return None, scores
    return best, scores


def evaluate_routing(skill_name: str, skill_description: str, examples: dict,
                      competing: dict[str, str] | None = None) -> dict:
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
        "prompt": prompt, "kind": kind, "expected": expected, "predicted": predicted,
        "correct": bool(correct), "top_scores": dict(sorted(scores.items(), key=lambda kv: -kv[1])[:3]),
    }
