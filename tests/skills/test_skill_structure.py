"""Structural validation for distributable Skills.

Discovers every skills/*/SKILL.md and checks basic structural requirements.
No Skill implementations exist yet, so this test currently has nothing to
validate and passes trivially -- it starts enforcing structure the moment
the first SKILL.md is added.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"


def _discovered_skill_files() -> list[Path]:
    if not SKILLS_DIR.exists():
        return []
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def test_skills_directory_exists():
    assert SKILLS_DIR.is_dir()


@pytest.mark.parametrize("skill_md", _discovered_skill_files(), ids=lambda p: p.parent.name)
def test_skill_md_has_required_frontmatter(skill_md: Path):
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{skill_md} must start with YAML frontmatter"

    parts = text.split("---", 2)
    assert len(parts) >= 3, f"{skill_md} frontmatter block is not closed"
    frontmatter = parts[1]

    for required_field in ("name", "description"):
        assert (
            f"{required_field}:" in frontmatter
        ), f"{skill_md} frontmatter is missing required field '{required_field}'"


@pytest.mark.parametrize("skill_md", _discovered_skill_files(), ids=lambda p: p.parent.name)
def test_skill_does_not_reference_repo_relative_paths(skill_md: Path):
    text = skill_md.read_text(encoding="utf-8")
    assert (
        "../../src" not in text
    ), f"{skill_md} must not depend on repository-relative paths outside its own directory"
