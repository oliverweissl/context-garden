"""Portable export/import of a greenhouse base (cards + platform notes).

A bundle carries no local paths: `local_path` is dropped on export and the
importing base keeps its own. Cards are matched by project fingerprint
(cg_fingerprint), else by slug; notes by topic. Nothing newer is clobbered:
a card replaces the local one only when it was synced later, a note only when
it was updated later, unless --force. Curated fields are user content: list
fields are unioned, a local scalar (summary) is kept unless --force.
"""

from __future__ import annotations

import json
import time

from cg_fingerprint import is_empty, match, normalize_remote
from gh_base import Base, atomic_write_text, slugify

FORMAT = "greenhouse-base"
VERSION = 1
LOCAL_FIELDS = ("local_path",)


def card_fingerprint(card: dict) -> dict:
    """Stored fingerprint; cards synced before it existed fall back to the remote."""
    return card.get("fingerprint") or {"remote": normalize_remote(card.get("remote", "")),
                                       "root_commits": []}


def export_bundle(base: Base, slugs=None) -> dict:
    cards = base.cards()
    if slugs:
        wanted = {slugify(s) for s in slugs}
        missing = wanted - {c["slug"] for c in cards}
        if missing:
            raise KeyError(", ".join(sorted(missing)))
        cards = [c for c in cards if c["slug"] in wanted]
        notes = [n for n in base.notes() if wanted & set(n.get("repos") or [])]
    else:
        notes = base.notes()
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": time.time(),
        "base": base.meta().get("name"),
        "cards": [{k: v for k, v in c.items() if k not in LOCAL_FIELDS} for c in cards],
        "notes": notes,
    }


def _find(card: dict, cards: list):
    """(local card or None, conflict reason). A same-slug card of a different
    project (both fingerprinted, no match) is a conflict, not a match."""
    fp = card_fingerprint(card)
    if not is_empty(fp):
        for c in cards:
            if match(fp, card_fingerprint(c)):
                return c, ""
    same = next((c for c in cards if c["slug"] == card["slug"]), None)
    if same and not is_empty(fp) and not is_empty(card_fingerprint(same)):
        return None, f"slug {card['slug']} belongs to a different project here"
    return same, ""


def _merge_curated(local: dict, incoming: dict, force: bool) -> dict:
    out = dict(local)
    for key, value in incoming.items():
        if isinstance(value, list):
            merged = list(out.get(key) or [])
            out[key] = merged + [v for v in value if v not in merged]
        elif value and (force or not out.get(key)):
            out[key] = value
    return out


def import_bundle(base: Base, bundle: dict, force: bool = False) -> dict:
    """Merge a bundle into the base under one lock. Returns {added, updated,
    skipped} lists of human-readable lines."""
    report = {"added": [], "updated": [], "skipped": []}
    with base.lock():
        cards = base.cards()
        for card in bundle.get("cards", []):
            if not card.get("slug"):
                continue
            card = {k: v for k, v in card.items() if k not in LOCAL_FIELDS}
            card["slug"] = slugify(card["slug"])
            local, conflict = _find(card, cards)
            if conflict:  # never overwrite another project's card, not even with --force
                report["skipped"].append(f"card {card['slug']}: {conflict} (forget it first)")
                continue
            if local is None:
                card["imported_at"] = time.time()
                atomic_write_text(base.card_path(card["slug"]), _dump(card))
                cards.append(card)
                report["added"].append(f"card {card['slug']}")
                continue
            if card.get("synced_at", 0) <= local.get("synced_at", 0) and not force:
                report["skipped"].append(f"card {local['slug']}: local card is as new or newer")
                continue
            new = dict(card, slug=local["slug"], imported_at=time.time(),
                       created_at=local.get("created_at", card.get("created_at")),
                       curated=_merge_curated(local.get("curated") or {},
                                              card.get("curated") or {}, force))
            for key in LOCAL_FIELDS:
                if key in local:
                    new[key] = local[key]
            atomic_write_text(base.card_path(new["slug"]), _dump(new))
            cards = [c for c in cards if c is not local] + [new]
            report["updated"].append(f"card {new['slug']}")

        notes = base.notes()
        n = max((int(x["id"][1:]) for x in notes if x["id"][1:].isdigit()), default=0)
        for note in bundle.get("notes", []):
            if not note.get("topic") or not note.get("text"):
                continue
            old = next((x for x in notes if x["topic"].lower() == note["topic"].lower()), None)
            if old is None:
                n += 1
                notes.append(dict(note, id=f"p{n:03d}"))
                report["added"].append(f"note {note['topic']}")
            elif old["text"] == note["text"]:
                continue
            elif note.get("updated_at", 0) > old.get("updated_at", 0) or force:
                notes[notes.index(old)] = dict(note, id=old["id"], created_at=old.get("created_at"))
                report["updated"].append(f"note {note['topic']}")
            else:
                report["skipped"].append(f"note {note['topic']}: local note is newer")
        base._save_notes(notes)
        base._render_index_locked()
    return report


def _dump(card: dict) -> str:
    return json.dumps(card, indent=2) + "\n"
