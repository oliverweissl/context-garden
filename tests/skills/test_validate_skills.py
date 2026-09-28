"""Unit tests for scripts/validate-skills: frontmatter parsing and the
outside-skill path check (prose only, code exempt)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load():
    path = REPO_ROOT / "scripts" / "validate-skills"
    loader = importlib.machinery.SourceFileLoader("validate_skills", str(path))
    spec = importlib.util.spec_from_loader("validate_skills", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


vs = _load()

FM = "---\nname: demo\ndescription: A demo skill.\n---\n"


def _skill(tmp_path: Path, body: str, frontmatter: str = FM) -> list[str]:
    (tmp_path / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")
    return vs.validate_skill(tmp_path)


def test_valid_skill_passes(tmp_path):
    assert _skill(tmp_path, "\n# Demo\n") == []


def test_frontmatter_delimiter_must_be_line_anchored(tmp_path):
    # `---` inside a value must not end the frontmatter
    fm = "---\nname: demo\ndescription: before---after\n---\n"
    assert _skill(tmp_path, "body\n", fm) == []
    errs = _skill(tmp_path, "", "--- name: demo\ndescription: d\n---\n")
    assert any("missing YAML frontmatter" in e for e in errs)
    errs = _skill(tmp_path, "", "---\nname: demo\ndescription: d\n")
    assert any("missing YAML frontmatter" in e for e in errs)


def test_frontmatter_closed_at_eof(tmp_path):
    assert _skill(tmp_path, "", "---\nname: demo\ndescription: d\n---") == []


def test_parent_path_in_prose_and_links_is_flagged(tmp_path):
    errs = _skill(tmp_path, "See ../../src/main.py and [x](../other/SKILL.md).\n")
    assert sum("outside the skill directory" in e for e in errs) == 2


def test_parent_path_in_code_is_exempt(tmp_path):
    body = (
        "Run `cd ../build && make` first.\n"
        "```bash\ncd ../..\n```\n"
        "~~~\ncp ../x .\n~~~\n"
        "Double ``a ../b`` span.\n"
    )
    assert _skill(tmp_path, body) == []


def test_fence_closes_only_on_matching_fence(tmp_path):
    assert _skill(tmp_path, "````\n```\n../inside\n````\nok\n") == []
    errs = _skill(tmp_path, "```\ncode\n```\nthen ../escape\n")
    assert any("../escape" in e for e in errs)
