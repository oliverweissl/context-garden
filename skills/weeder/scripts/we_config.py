"""Resolves the LLM-assist level (none/slight/lot) that gates how much of
SKILL.md workflow step 3 (shortening the description, consolidating
duplicate rules, removing obvious filler -- see we_suggest.py) is exposed
as a structured suggest/apply-suggestion request instead of done freehand.

Precedence: --assist flag > CONTEXT_GARDEN_LLM_ASSIST env var >
`llm_assist.level` in the nearest .context-garden/config.yaml found
walking up from the given start directory > "none". No level here ever
triggers a network call or needs an API key -- see
references/llm-assist.md for what actually changes at each level.
"""

from __future__ import annotations

import os
from pathlib import Path

LEVELS = ("none", "slight", "lot")
DEFAULT_LEVEL = "none"
ENV_VAR = "CONTEXT_GARDEN_LLM_ASSIST"
CONFIG_RELPATH = Path(".context-garden") / "config.yaml"


def _parse_minimal_yaml(text: str) -> dict:
    """Parses only the small subset of YAML this project's config actually
    uses: flat `key: value` lines, and one level of indented nesting
    (`key:` on its own line, followed by more-indented `subkey: value`
    lines). Not a general YAML parser -- deliberately, to avoid adding a
    PyYAML dependency to a skill that's otherwise pure stdlib (the same
    tradeoff we_parse.parse_frontmatter makes for SKILL.md's own
    frontmatter)."""
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, sep, value = line.strip().partition(":")
        if not sep:
            continue
        value = value.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value:
            parent[key] = value
        else:
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
    return root


def find_config_file(start_dir) -> Path | None:
    d = Path(start_dir).resolve()
    for candidate in (d, *d.parents):
        p = candidate / CONFIG_RELPATH
        if p.is_file():
            return p
    return None


def resolve_assist_level(explicit: str | None = None, start_dir=None) -> str:
    if explicit:
        if explicit not in LEVELS:
            raise ValueError(f"invalid assist level {explicit!r}, expected one of {LEVELS}")
        return explicit

    env_value = os.environ.get(ENV_VAR)
    if env_value:
        if env_value not in LEVELS:
            raise ValueError(f"invalid {ENV_VAR}={env_value!r}, expected one of {LEVELS}")
        return env_value

    config_path = find_config_file(start_dir if start_dir is not None else Path.cwd())
    if config_path is not None:
        config = _parse_minimal_yaml(config_path.read_text())
        assist = config.get("llm_assist", {})
        level = assist.get("level") if isinstance(assist, dict) else None
        if level:
            if level not in LEVELS:
                raise ValueError(
                    f"invalid llm_assist.level={level!r} in {config_path}, expected one of {LEVELS}"
                )
            return level

    return DEFAULT_LEVEL
