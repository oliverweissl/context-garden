"""Unit tests for the structured single-answer verify type (answer_key)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

from harness.verify import _dispatch, parse_answer_lines  # noqa: E402

SPEC = {"type": "answer_key", "path": "ANSWER.txt", "key": "ROOT_CAUSE", "expected": "get_timeout"}
PATTERN_SPEC = {**{k: v for k, v in SPEC.items() if k != "expected"}, "pattern": r"get_\w+"}


def _check(tmp_path, text, spec=SPEC):
    if text is not None:
        (tmp_path / "ANSWER.txt").write_text(text)
    return _dispatch(spec, None, tmp_path)


@pytest.mark.parametrize(
    "text",
    ["ROOT_CAUSE: get_timeout\n", "  ROOT_CAUSE:get_timeout  ", "`ROOT_CAUSE: get_timeout`\n"],
)
def test_single_correct_answer_passes(tmp_path, text):
    ok, note = _check(tmp_path, text)
    assert ok, note


def test_pattern_answer_passes(tmp_path):
    ok, note = _check(tmp_path, "ROOT_CAUSE: get_priority\n", PATTERN_SPEC)
    assert ok, note


def test_missing_file_fails(tmp_path):
    ok, note = _check(tmp_path, None)
    assert not ok and "missing file" in note


def test_missing_key_fails(tmp_path):
    ok, note = _check(tmp_path, "get_timeout\n")
    assert not ok and "missing answer line" in note


def test_multiple_answers_fail(tmp_path):
    ok, note = _check(tmp_path, "ROOT_CAUSE: get_timeout\nROOT_CAUSE: get_priority\n")
    assert not ok and "exactly one" in note


def test_multiple_identical_answers_still_fail(tmp_path):
    ok, _ = _check(tmp_path, "ROOT_CAUSE: get_timeout\nROOT_CAUSE: get_timeout\n")
    assert not ok


def test_wrong_answer_fails(tmp_path):
    ok, note = _check(tmp_path, "ROOT_CAUSE: get_priority\n")
    assert not ok and "wrong answer" in note


def test_pattern_must_match_whole_value(tmp_path):
    ok, _ = _check(tmp_path, "ROOT_CAUSE: get_timeout or maybe something else\n", PATTERN_SPEC)
    assert not ok


def test_key_is_case_sensitive_and_exact():
    assert parse_answer_lines("root_cause: x\nMY_ROOT_CAUSE: y\nROOT_CAUSE: z", "ROOT_CAUSE") == [
        "z"
    ]


def test_spec_needs_exactly_one_of_expected_or_pattern(tmp_path):
    (tmp_path / "ANSWER.txt").write_text("ROOT_CAUSE: x\n")
    with pytest.raises(ValueError):
        _dispatch({**SPEC, "pattern": "x"}, None, tmp_path)


def test_answer_key_nests_in_all(tmp_path):
    (tmp_path / "ANSWER.txt").write_text("ROOT_CAUSE: get_timeout\n")
    spec = {"type": "all", "checks": [SPEC, {**SPEC, "expected": "other"}]}
    ok, note = _dispatch(spec, None, tmp_path)
    assert not ok
    assert "[answer_key] PASS" in note and "[answer_key] FAIL" in note
