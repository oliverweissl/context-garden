#!/usr/bin/env python3
"""greenhouse: platform-wide memory shared by the repos of one platform.

A *base* (a folder with greenhouse.json, usually next to the repos) holds one
card per repo plus platform notes. Run `sync` in a repo to publish its card,
`brief "<task>"` to pull what other repos know. Offline, stdlib only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from gh_base import (
    POINTER, Base, default_location, discover, estimate_tokens, find_repo_root, head_of,
    slugify, summary_of, words,
)
from gh_harvest import harvest, repo_slug

# Shell-quoted: printed for the agent to run, and plugin paths may contain spaces.
BIN = shlex.quote(str(Path(__file__).resolve().parent.parent / "bin" / "greenhouse"))
DEFAULT_BUDGET = 600
HOOK_BUDGET = 400
CONFIG_DEFAULTS = {"auto_sync": False}  # stored in greenhouse.json, shared with every repo


def _open(args, repo_root: Path):
    root, how = discover(repo_root, args.base)
    return (Base(root) if root else None), how


def ask_for_base(repo_root: Path) -> str:
    """What Claude must ask the user when no base is found (a hook or a
    non-interactive `mode on` cannot ask itself)."""
    return (f"greenhouse: no base found for this repo. Ask the user (AskUserQuestion) which they want, "
            f"then run it:\n"
            f"  1. create a new base at {default_location(repo_root)}: {BIN} init [--name <platform>]\n"
            f"  2. use an existing base: {BIN} link <path> (or create one there: {BIN} init --at <path>)\n"
            f"  3. this repo is not part of a platform: {BIN} mode off")


def _no_base(how: str) -> int:
    print(f"no greenhouse base found ({how}).", file=sys.stderr)
    print(ask_for_base(find_repo_root()), file=sys.stderr)
    return 1


def cmd_check(args, repo_root) -> int:
    """Run after `mode on`: report the base, or settle where it should be.
    On a terminal the user answers here; otherwise Claude is told to ask."""
    base, how = _open(args, repo_root)
    if base:
        print(f"greenhouse base: {base.root} (found via {how})")
        return 0
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(ask_for_base(repo_root))
        return 0
    default = default_location(repo_root)
    print(f"No greenhouse base found for {repo_root.name}.\n"
          f"  [1] create a new base at {default}\n  [2] use or create a base at another path\n"
          "  [3] skip (this repo is not part of a platform)")
    choice = input("choice [1]: ").strip() or "1"
    if choice == "1":
        Base.create(default)
        print(f"created {default}")
        _write_pointer_if_needed(repo_root, default)
    elif choice == "2":
        target = Path(input("path: ").strip()).expanduser().resolve()
        existed = (target / "greenhouse.json").is_file()
        Base.create(target)
        print(f"{'using' if existed else 'created'} {target}")
        _write_pointer_if_needed(repo_root, target)
    else:
        print(f"skipped; turn greenhouse off here with {BIN} mode off if this repo is standalone")
    return 0


def card_state(base: Base, card: dict) -> str:
    """'' when current, 'stale' when the repo's local HEAD moved, '?' when
    the repo is not checked out next to the base."""
    path = (base.root / card.get("local_path", "")).resolve() if card.get("local_path") else None
    if not path or not (path / ".git").exists():
        return "?"
    return "" if head_of(path) == card.get("head") else "stale"


def neighbours(base: Base, slug: str, cards=None) -> tuple:
    uses, used_by = [], []
    for a, b, why in base.links(cards):
        if a == slug:
            uses.append(f"{b} ({why})")
        elif b == slug:
            used_by.append(f"{a} ({why})")
    return uses, used_by


# ------------------------------------------------------------------ commands

def cmd_where(args, repo_root) -> int:
    base, how = _open(args, repo_root)
    if not base:
        return _no_base(how)
    print(f"{base.root} (found via {how})")
    return 0


def cmd_init(args, repo_root) -> int:
    found, _ = discover(repo_root, None)
    target = Path(args.at).expanduser().resolve() if args.at else (found or default_location(repo_root))
    existed = (target / "greenhouse.json").is_file()
    base = Base.create(target, args.name)
    print(f"greenhouse base: {base.root}" + (" (already existed)" if existed else " (created)"))
    _write_pointer_if_needed(repo_root, base.root)
    return 0


def _write_pointer_if_needed(repo_root: Path, base_root: Path) -> None:
    found, _ = discover(repo_root, None)
    if found != Path(base_root).resolve():
        _write_pointer(repo_root, base_root)


def _write_pointer(repo_root: Path, base_root: Path) -> None:
    pointer = repo_root / POINTER
    rel = Path(os.path.relpath(base_root, repo_root)).as_posix()
    if pointer.exists():
        print(f"{pointer} exists; not overwriting it. Point it at {rel} yourself if needed.",
              file=sys.stderr)
        return
    pointer.write_text(rel + "\n")
    print(f"wrote {pointer} -> {rel} (commit it so teammates find the base)")


def cmd_link(args, repo_root) -> int:
    root = Path(args.path).expanduser().resolve()
    if not (root / "greenhouse.json").is_file():
        print(f"{root} is not a greenhouse base (no greenhouse.json); create it with {BIN} init --at {root}",
              file=sys.stderr)
        return 1
    _write_pointer(repo_root, root)
    return 0


def cmd_sync(args, repo_root) -> int:
    if not (repo_root / ".git").exists():
        print(f"greenhouse: {repo_root} is not a git repo; run sync from the repo whose card "
              f"to publish: cd <repo> && {BIN} sync", file=sys.stderr)
        return 2
    base, how = _open(args, repo_root)
    curated = {"summary": args.summary, "provides": args.provides or None,
               "consumes": args.consumes or None, "owners": args.owner or None}
    if args.dry_run:
        card = harvest(repo_root, base.root if base else default_location(repo_root))
        print(json.dumps(dict(card, curated={k: v for k, v in curated.items() if v}), indent=2))
        return 0
    if not base:
        return _no_base(how)
    card = base.save_card(harvest(repo_root, base.root), curated)
    if args.quiet:
        return 0
    uses, used_by = neighbours(base, card["slug"])
    print(f"synced {card['slug']} @ {card['head'][:10] or 'no-commit'} -> {base.card_path(card['slug'])}")
    print(f"  {len(card['packages'])} package(s), {len(card['dependencies'])} dependencies, "
          f"{len(card['facts'])} fact(s), {len(card['findings'])} finding(s)")
    if uses or used_by:
        print(f"  uses: {', '.join(uses) or '-'}; used by: {', '.join(used_by) or '-'}")
    if not summary_of(card):
        print(f'  no summary: add one with {BIN} sync --summary "<what this repo is for>"')
    return 0


def cmd_show(args, repo_root) -> int:
    base, how = _open(args, repo_root)
    if not base:
        return _no_base(how)
    card = base.load_card(args.slug)
    if not card:
        print(f"no card for {args.slug}; known: {', '.join(c['slug'] for c in base.cards()) or 'none'}",
              file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(card, indent=2))
        return 0
    print(render_card(base, card))
    return 0


def render_card(base: Base, card: dict) -> str:
    cur = card.get("curated") or {}
    state = card_state(base, card)
    lines = [f"# {card['slug']}" + (" (STALE: repo moved since sync, verify)" if state == "stale" else ""),
             summary_of(card) or "(no summary)",
             f"remote: {card.get('remote') or '-'} | checkout: {card.get('local_path') or '-'} | "
             f"synced at {card.get('head', '')[:10] or '-'}"]
    for label, key in (("languages", "languages"), ("packages", "packages"),
                       ("dependencies", "dependencies"), ("entrypoints", "entrypoints"),
                       ("layout", "layout"), ("warm topics", "warm_topics")):
        if card.get(key):
            lines.append(f"{label}: {', '.join(card[key])}")
    for label, key in (("provides", "provides"), ("consumes", "consumes"), ("owners", "owners")):
        if cur.get(key):
            lines.append(f"{label}: {'; '.join(cur[key])}")
    uses, used_by = neighbours(base, card["slug"])
    if uses or used_by:
        lines.append(f"uses: {', '.join(uses) or '-'} | used by: {', '.join(used_by) or '-'}")
    if card.get("facts"):
        lines += ["facts:"] + [f"- {f}" for f in card["facts"]]
    if card.get("findings"):
        lines += ["findings:"] + [f"- {f}" for f in card["findings"]]
    return "\n".join(lines)


def cmd_list(args, repo_root) -> int:
    base, how = _open(args, repo_root)
    if not base:
        return _no_base(how)
    cards = base.cards()
    print(f"{base.root}: {len(cards)} repo(s), {len(base.notes())} platform note(s)")
    for c in cards:
        state = card_state(base, c)
        flag = {"stale": " [stale]", "?": " [not checked out]"}.get(state, "")
        print(f"- {c['slug']}{flag}: {summary_of(c) or '(no summary)'}")
    return 0


def cmd_note(args, repo_root) -> int:
    base, how = _open(args, repo_root)
    if not base:
        return _no_base(how)
    note = base.add_note(args.topic, args.text, args.repo or [], repo_slug(repo_root))
    print(f"{note['id']} {note['topic']}" + (f" [{', '.join(note['repos'])}]" if note["repos"] else ""))
    return 0


def cmd_drop(args, repo_root) -> int:
    base, how = _open(args, repo_root)
    if not base:
        return _no_base(how)
    if not base.drop_note(args.note_id):
        print(f"no note {args.note_id}", file=sys.stderr)
        return 1
    print(f"dropped {args.note_id}")
    return 0


def cmd_forget(args, repo_root) -> int:
    base, how = _open(args, repo_root)
    if not base:
        return _no_base(how)
    if not base.forget(args.slug):
        print(f"no card for {args.slug}", file=sys.stderr)
        return 1
    print(f"removed card {slugify(args.slug)}")
    return 0


# ------------------------------------------------------------------ retrieval

NOTES_SHARE = 3  # notes get at most 1/NOTES_SHARE of the budget: repos come first
OBSOLETE_WEIGHT = 0.25  # an obsolete repo must match ~4x better to outrank a live one
MIN_ITEM_SCORE = 1.5  # a fact/finding needs about one rare word in common with the task
MIN_RELATIVE_SCORE = 0.3  # a repo/note scoring under 30% of the best match is noise
_OBSOLETE = re.compile(r"\b(obsolete|deprecated|archived|legacy|unmaintained)\b", re.I)
_SLUG_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]*")


def _is_obsolete(card: dict) -> bool:
    return bool(_OBSOLETE.search(f"{card['slug']} {summary_of(card)}"))


def _card_text(card: dict) -> str:
    cur = card.get("curated") or {}
    return " ".join([card["slug"], summary_of(card), *card.get("packages", []),
                     *cur.get("provides", []), *cur.get("consumes", [])])


def _weights(docs: list) -> dict:
    """Inverse document frequency per word: a word in every card or note
    ('app', the platform name) weighs ~0, a word in one ('keycloak') the most."""
    df = {}
    for doc in docs:
        for w in words(doc):
            df[w] = df.get(w, 0) + 1
    n = len(docs)
    return {w: math.log((n + 1) / (k + 0.5)) for w, k in df.items()}


def _score(query: set, text: str, weights: dict) -> float:
    return sum(weights.get(w, 0.0) for w in query & words(text))


def brief(base: Base, repo_root: Path, task: str, budget: int) -> str:
    cards = base.cards()
    notes_all = base.notes()
    in_repo = (repo_root / ".git").exists()
    me = repo_slug(repo_root) if in_repo else None
    q = words(task) - words(base.meta().get("name", ""))
    named = set(_SLUG_TOKEN.findall(task.lower()))
    uses, used_by = neighbours(base, me, cards)
    linked = {x.split(" ", 1)[0] for x in uses + used_by}
    weights = _weights([_card_text(c) for c in cards] + [f"{n['topic']} {n['text']}" for n in notes_all]
                       + [t for c in cards for t in c.get("facts", []) + c.get("findings", [])])

    out = [f"Greenhouse {base.meta().get('name', '')} ({base.root}): {len(cards)} repo(s)."]
    mine = next((c for c in cards if c["slug"] == me), None)
    if in_repo and mine is None:
        out.append(f"This repo ({me}) has no card yet: run {BIN} sync at the end of the task.")
    elif in_repo and mine.get("head") != head_of(repo_root):
        out.append(f"This repo's card is older than HEAD: run {BIN} sync at the end of the task.")
    if uses or used_by:
        out.append(f"This repo uses: {', '.join(uses) or '-'}; used by: {', '.join(used_by) or '-'}")

    notes = []
    for n in notes_all:
        s = _score(q, f"{n['topic']} {n['text']} {' '.join(n.get('repos', []))}", weights)
        if me in n.get("repos", []):
            s += 1
        if s:
            notes.append((s, n))
    notes.sort(key=lambda x: -x[0])
    if notes:
        notes = [x for x in notes if x[0] >= notes[0][0] * MIN_RELATIVE_SCORE]

    repos = []
    for c in cards:
        if c["slug"] == me:
            continue
        head = _score(q, _card_text(c), weights) + (10 if c["slug"] in named else 0)
        items = sorted(((_score(q, t, weights), t) for t in c.get("facts", []) + c.get("findings", [])),
                       key=lambda x: -x[0])
        items = [t for s, t in items if s >= MIN_ITEM_SCORE][:3]
        bonus = 2 if c["slug"] in linked else 0
        if head + bonus or items:
            score = head * 2 + bonus + len(items)
            repos.append((score * OBSOLETE_WEIGHT if _is_obsolete(c) else score, c, items))
    repos.sort(key=lambda x: -x[0])
    if repos:  # drop the long tail that only shares a common word with the task
        repos = [r for r in repos if r[0] >= repos[0][0] * MIN_RELATIVE_SCORE]

    used = estimate_tokens("\n".join(out))
    reserve = budget // NOTES_SHARE if notes else 0
    shown, block_out = [], []
    for _, c, items in repos:
        stale = " (STALE card)" if card_state(base, c) == "stale" else ""
        block = [f"- {c['slug']}{stale}: {summary_of(c) or '(no summary)'}"
                 + (f" [checkout {c['local_path']}]" if c.get("local_path") else "")]
        block += [f"  - {t}" for t in items]
        cost = estimate_tokens("\n".join(block))
        if used + cost > budget - reserve:
            break
        block_out += block
        used += cost
        shown.append(c["slug"])
    if block_out:
        out += ["Related repos:", *block_out]
    note_out = []
    for _, n in notes:
        line = f"- [{n['id']}] {n['topic']}: {n['text']}"
        if used + estimate_tokens(line) > budget:
            break
        note_out.append(line)
        used += estimate_tokens(line)
    if note_out:
        out += ["Platform notes:", *note_out]
    if not block_out and not note_out:
        out.append("Nothing in the base matches this task." if not notes and not repos else
                   f"Matches did not fit the budget: raise --budget (now {budget}).")
    if shown:
        out.append(f"Full card: {BIN} show <slug>. Read another repo's files only if a card points there.")
    return "\n".join(out)


def cmd_brief(args, repo_root) -> int:
    base, how = _open(args, repo_root)
    if not base:
        return _no_base(how)
    print(brief(base, repo_root, args.task, args.budget))
    return 0


def session_overview(base: Base, repo_root: Path) -> str:
    cards = base.cards()
    in_repo = (repo_root / ".git").exists()
    me = repo_slug(repo_root) if in_repo else None
    where = "This repo is part of" if in_repo else "This folder holds"
    lines = [f"[greenhouse] {where} platform '{base.meta().get('name', '')}' "
             f"({len(cards)} repos, {len(base.notes())} platform notes). Before changing an "
             f"interface another repo might use, run: {BIN} brief \"<task>\""]
    uses, used_by = neighbours(base, me, cards)
    if uses or used_by:
        lines.append(f"This repo uses: {', '.join(uses) or '-'}; used by: {', '.join(used_by) or '-'}")
    # Obsolete repos last: an alphabetical preview otherwise fills up with them.
    others = sorted((c for c in cards if c["slug"] != me),
                    key=_is_obsolete)
    used = estimate_tokens("\n".join(lines))
    for c in others:
        line = f"- {c['slug']}: {(summary_of(c) or '(no summary)')[:110]}"
        if used + estimate_tokens(line) > HOOK_BUDGET:
            lines.append(f"- ... {BIN} list shows all")
            break
        lines.append(line)
        used += estimate_tokens(line)
    mine = next((c for c in cards if c["slug"] == me), None)
    if in_repo and (mine is None or mine.get("head") != head_of(repo_root)):
        lines.append(f"This repo's card is missing or old: run {BIN} sync after the task.")
    return "\n".join(lines)


_PATHISH = re.compile(r"[\w.~/-]+")


def touched_repos(transcript: str, base: Path) -> list:
    """Child repos of `base` (a folder of repos, not a repo) that the
    session's tool calls named: by path (`repo/...`, absolute paths under
    `base`) or as a `cd` target. Read from the session transcript."""
    base = base.resolve()
    names = {c.name: c for c in sorted(base.iterdir()) if (c / ".git").exists()}
    found = []
    if not transcript:
        return []
    try:
        lines = Path(transcript).read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return []
    for line in lines:
        try:  # transcript lines may be partial or of other shapes
            content = (json.loads(line).get("message") or {}).get("content")
        except (json.JSONDecodeError, AttributeError):
            continue
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            for tok in _PATHISH.findall(json.dumps(item.get("input") or {})):
                if tok.startswith(str(base) + "/"):
                    tok = tok[len(str(base)) + 1:]
                elif tok.startswith("/"):
                    continue
                head = tok.lstrip("./").split("/", 1)[0]
                if head in names and ("/" in tok or f"cd {head}" in line) and names[head] not in found:
                    found.append(names[head])
    return found


def cmd_hook(args, repo_root) -> int:
    """session-start: platform overview (or the ask-for-a-base instruction).
    session-end: re-harvest this repo's card -- or, for a session started in
    a folder of repos, the card of every repo the session touched -- only if
    the base opted in with `config auto_sync true` (it writes to a shared folder)."""
    payload = json.loads(sys.stdin.read() or "{}")
    root = find_repo_root(payload.get("cwd") or repo_root)
    found, _ = discover(root, None)
    if args.event == "session-start":
        print(session_overview(Base(found), root) if found else ask_for_base(root))
        return 0
    if (root / ".git").exists():
        repos = [root]
    elif isinstance(payload.get("repos"), list):  # the detached child, below
        repos = [Path(r) for r in payload["repos"]]
    else:
        repos = touched_repos(payload.get("transcript_path"), root)
    targets = []
    for repo in repos:
        base, _ = discover(repo, None)
        if base and Base(base).meta().get("auto_sync"):
            targets.append((repo, base))
    if not targets:
        return 0
    if args.wait:
        for repo, base in targets:
            Base(base).save_card(harvest(repo, base), {})
    else:  # SessionEnd hooks get ~1.5 s: harvest in a detached child instead
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "hook", "session-end", "--wait"],
            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        child.stdin.write(json.dumps({"cwd": str(root), "repos": [str(r) for r, _ in targets]}).encode())
        child.stdin.close()  # don't wait: the child outlives this hook
    return 0


def cmd_config(args, repo_root) -> int:
    base, how = _open(args, repo_root)
    if not base:
        return _no_base(how)
    if args.value is None:
        print(json.dumps({k: base.meta().get(k, v) for k, v in CONFIG_DEFAULTS.items()}, indent=2))
        return 0
    value = args.value.lower() in ("1", "true", "on", "yes")
    base.set_meta(args.key, value)
    print(f"{args.key} = {value} ({base.root / 'greenhouse.json'})")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="greenhouse", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", help="base directory (default: discovered, see references/guide.md)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("where", help="print the base this repo uses and how it was found")
    i = sub.add_parser("init", help="create a base (default: <repo parent>/.greenhouse)")
    i.add_argument("--at", help="directory for the base")
    i.add_argument("--name", help="platform name")
    ln = sub.add_parser("link", help="write <git root>/.greenhouse pointing at an existing base")
    ln.add_argument("path")

    s = sub.add_parser("sync", help="harvest this repo and publish its card (creates a base if none)")
    s.add_argument("--summary", help="one sentence: what this repo is for (kept across syncs)")
    s.add_argument("--provides", action="append", help="interface others use, e.g. 'REST /v1/orders'")
    s.add_argument("--consumes", action="append", help="'<repo>: <what>' this repo relies on")
    s.add_argument("--owner", action="append", help="owning team or person")
    s.add_argument("--dry-run", action="store_true", help="print the card, write nothing")
    s.add_argument("--quiet", action="store_true")
    sub.add_parser("check", help="report the base, or ask where it should be (run after `mode on`)")
    c = sub.add_parser("config", help="show or set base settings shared by every repo")
    c.add_argument("key", nargs="?", choices=sorted(CONFIG_DEFAULTS))
    c.add_argument("value", nargs="?", help="true/false")

    b = sub.add_parser("brief", help="platform knowledge relevant to a task")
    b.add_argument("task")
    b.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="max tokens (approx.)")
    sh = sub.add_parser("show", help="one repo's full card")
    sh.add_argument("slug")
    sh.add_argument("--json", action="store_true")
    sub.add_parser("list", help="all repos in the base, with stale flags")

    n = sub.add_parser("note", help="platform-wide note (same topic replaces the old text)")
    n.add_argument("topic")
    n.add_argument("text")
    n.add_argument("--repo", action="append", help="repo slug the note concerns (repeatable)")
    d = sub.add_parser("drop", help="delete a platform note")
    d.add_argument("note_id")
    f = sub.add_parser("forget", help="remove a repo's card")
    f.add_argument("slug")

    h = sub.add_parser("hook", help="Claude Code hook entry (reads the hook JSON on stdin)")
    h.add_argument("event", choices=["session-start", "session-end"])
    h.add_argument("--wait", action="store_true", help=argparse.SUPPRESS)

    args = p.parse_args(argv)
    handler = {
        "where": cmd_where, "init": cmd_init, "link": cmd_link, "sync": cmd_sync,
        "brief": cmd_brief, "show": cmd_show, "list": cmd_list, "note": cmd_note,
        "drop": cmd_drop, "forget": cmd_forget, "hook": cmd_hook, "check": cmd_check,
        "config": cmd_config,
    }[args.cmd]
    return handler(args, find_repo_root())


if __name__ == "__main__":
    sys.exit(main())
