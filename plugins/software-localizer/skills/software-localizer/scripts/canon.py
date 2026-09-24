"""Canon (glossary) management for software-localizer (plan section 8).

`R/canon.json`: `{"schema": 1, "entries": [...]}`, an entry:

    {"id": "t-cart", "kind": "term|ui_label|dnt", "source": "Cart", "note": "",
     "occurrences": ["frontend:shop.cart.title"],
     "translations": {"de": {"value": "Warenkorb", "status": "proposed|approved",
                              "approved_by": None, "approved_at": None}}}

`import` merges in candidates from a canon-turn's output (plan section 10):

    {"candidates": [{"kind", "source", "note", "occurrences",
                      "translations": {locale: {"proposed": value, "current": [...]}}}]}

A caller importing a review turn's `new_canon_candidates` (same table) wraps
that list in `{"candidates": [...]}` in this same shape before calling
`import --file F` — this module only ever reads the canonical shape above.

`R/canon.lock.json` is the frozen subset packets embed: every `dnt` entry
(unconditionally) plus every entry with at least one *approved* translation
(only those translations, never the still-proposed ones), each entry with
its own sha256. `R/canon.history.json` is an append-only record of every
approval and change (who, when, before, after).

`load_lock` is the only function other owners import directly (`packets.py`
builds packets from it; `ledger.py`'s CLI passes it into `sync`); `import`,
`approve`, `freeze` and `change` are reached through the CLI.
"""

from __future__ import annotations

from pathlib import Path

import lz_common

_KIND_PREFIX = {"term": "t", "ui_label": "u", "dnt": "d"}


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


def load(root) -> dict:
    path = Path(root) / "canon.json"
    if not path.exists():
        return {"schema": 1, "entries": []}
    return lz_common.read_json(path, "canon.json")


def save(root, canon: dict) -> None:
    lz_common.atomic_write_json(Path(root) / "canon.json", canon)


def load_lock(root) -> dict:
    path = Path(root) / "canon.lock.json"
    if not path.exists():
        return {"schema": 1, "entries": []}
    return lz_common.read_json(path, "canon.lock.json")


def load_history(root) -> dict:
    path = Path(root) / "canon.history.json"
    if not path.exists():
        return {"schema": 1, "events": []}
    return lz_common.read_json(path, "canon.history.json")


def _append_history(root, event: dict) -> None:
    history = load_history(root)
    history["events"].append(event)
    lz_common.atomic_write_json(Path(root) / "canon.history.json", history)


# ---------------------------------------------------------------------------
# lookups / id generation
# ---------------------------------------------------------------------------


def _find_entry(canon: dict, kind: str, source: str):
    for entry in canon["entries"]:
        if entry["kind"] == kind and entry["source"] == source:
            return entry
    return None


def _find_by_id(canon: dict, entry_id: str):
    for entry in canon["entries"]:
        if entry["id"] == entry_id:
            return entry
    return None


def _slugify(text: str) -> str:
    out = []
    prev_dash = False
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    slug = "".join(out).strip("-")
    return slug or "entry"


def _new_id(canon: dict, kind: str, source: str) -> str:
    prefix = _KIND_PREFIX.get(kind, "e")
    base = f"{prefix}-{_slugify(source)}"
    existing_ids = {e["id"] for e in canon["entries"]}
    if base not in existing_ids:
        return base
    n = 2
    while f"{base}-{n}" in existing_ids:
        n += 1
    return f"{base}-{n}"


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def import_candidates(canon: dict, candidates: list) -> dict:
    """Merge `candidates` (plan section 10 shape) into `canon` in place.

    An entry with the same `kind` and `source` merges: occurrences are
    unioned, `note` fills in only if the existing entry's is empty, and an
    incoming locale's `proposed` value either creates a fresh `proposed`
    translation or replaces an existing *proposed* one -- an already
    `approved` translation is never overwritten by an import. Returns
    `{"added": [...ids...], "merged": [...ids...]}`.

    Raises `ValueError` for a candidate missing `kind`/`source` or naming an
    unknown `kind`; by the time a canon-turn or review-turn output reaches
    here it has already been validated by `packets.py`, so this is a
    cannot-run condition (the CLI lets it bubble to `run_main`, exit 2), not
    a business refusal.
    """
    added, merged = [], []
    for candidate in candidates:
        kind = candidate.get("kind")
        source = candidate.get("source")
        if kind not in _KIND_PREFIX or not source:
            raise ValueError(f"invalid canon candidate (kind/source): {candidate!r}")

        occurrences = list(candidate.get("occurrences") or [])
        note = candidate.get("note") or ""
        translations_in = candidate.get("translations") or {}

        entry = _find_entry(canon, kind, source)
        if entry is None:
            entry = {
                "id": _new_id(canon, kind, source),
                "kind": kind,
                "source": source,
                "note": note,
                "occurrences": list(occurrences),
                "translations": {},
            }
            canon["entries"].append(entry)
            added.append(entry["id"])
        else:
            for occ in occurrences:
                if occ not in entry["occurrences"]:
                    entry["occurrences"].append(occ)
            if not entry.get("note") and note:
                entry["note"] = note
            merged.append(entry["id"])

        for locale, t in translations_in.items():
            proposed = t.get("proposed") if isinstance(t, dict) else None
            if proposed is None:
                continue
            current = entry["translations"].get(locale)
            if current is not None and current.get("status") == "approved":
                continue
            entry["translations"][locale] = {
                "value": proposed, "status": "proposed",
                "approved_by": None, "approved_at": None,
            }

    return {"added": added, "merged": merged}


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


def approve(root, canon: dict, entry_id: str, locale: str | None, value: str | None, by: str) -> dict:
    """Approve one locale's translation (or, for a `dnt` entry, the entry as
    a whole -- no `locale`/`value`). `value`, when given, replaces whatever
    was proposed. Refuses (exit 1) a locale already approved -- that
    correction path is `change`, which keeps an audit trail of what it
    overwrote."""
    entry = _find_by_id(canon, entry_id)
    if entry is None:
        lz_common.fail(f"no canon entry: {entry_id}", lz_common.EXIT_FAIL, entry=entry_id)

    at = lz_common.now_iso()

    if entry["kind"] == "dnt":
        if locale is not None or value is not None:
            lz_common.fail(
                "a dnt entry is approved as a whole, without --locale/--value",
                lz_common.EXIT_FAIL, entry=entry_id,
            )
        before = {"approved_by": entry.get("approved_by"), "approved_at": entry.get("approved_at")}
        entry["approved_by"] = by
        entry["approved_at"] = at
        _append_history(root, {
            "at": at, "by": by, "action": "approve", "entry": entry_id, "locale": None,
            "before": before, "after": {"approved_by": by, "approved_at": at},
        })
        return {"entry": entry_id, "kind": "dnt"}

    if locale is None:
        lz_common.fail(
            "--locale is required to approve a term/ui_label entry",
            lz_common.EXIT_FAIL, entry=entry_id,
        )

    current = entry["translations"].get(locale)
    if current is not None and current.get("status") == "approved":
        lz_common.fail(
            f"{entry_id}/{locale} is already approved; use `change` to correct it",
            lz_common.EXIT_FAIL, entry=entry_id, locale=locale,
        )

    final_value = value if value is not None else (current or {}).get("value")
    if final_value is None:
        lz_common.fail(
            f"no proposed translation for {entry_id}/{locale}; provide --value",
            lz_common.EXIT_FAIL, entry=entry_id, locale=locale,
        )

    before_value = (current or {}).get("value")
    entry["translations"][locale] = {
        "value": final_value, "status": "approved", "approved_by": by, "approved_at": at,
    }
    _append_history(root, {
        "at": at, "by": by, "action": "approve", "entry": entry_id, "locale": locale,
        "before": before_value, "after": final_value,
    })
    return {"entry": entry_id, "locale": locale, "value": final_value}


# ---------------------------------------------------------------------------
# freeze
# ---------------------------------------------------------------------------


def freeze(root, canon: dict | None = None) -> dict:
    if canon is None:
        canon = load(root)

    locked_entries = []
    for entry in canon["entries"]:
        if entry["kind"] == "dnt":
            locked = {
                "id": entry["id"], "kind": entry["kind"], "source": entry["source"],
                "note": entry.get("note", ""), "occurrences": list(entry.get("occurrences", [])),
            }
        else:
            approved = {
                loc: dict(t) for loc, t in entry.get("translations", {}).items()
                if t.get("status") == "approved"
            }
            if not approved:
                continue
            locked = {
                "id": entry["id"], "kind": entry["kind"], "source": entry["source"],
                "note": entry.get("note", ""), "occurrences": list(entry.get("occurrences", [])),
                "translations": approved,
            }
        locked["sha256"] = lz_common.sha256_json(locked)
        locked_entries.append(locked)

    lock = {"schema": 1, "entries": locked_entries}
    lz_common.atomic_write_json(Path(root) / "canon.lock.json", lock)
    return lock


# ---------------------------------------------------------------------------
# change
# ---------------------------------------------------------------------------


def change(
    root, canon: dict, entry_id: str, locale: str, expect: str, value: str, reason: str, by: str,
) -> dict:
    """Correct an already-approved translation. Refuses (exit 1) unless
    `expect` equals the currently approved value -- the caller must be
    looking at what is actually approved, not an assumption. On success:
    refreezes `canon.lock.json`, appends to `canon.history.json`, and writes
    `R/runs/<locale>/canon-audit-<entry_id>.json`, the restricted-audit
    request `packets.py` builds an audit packet from (its occurrences,
    scoped to this one entry)."""
    entry = _find_by_id(canon, entry_id)
    if entry is None:
        lz_common.fail(f"no canon entry: {entry_id}", lz_common.EXIT_FAIL, entry=entry_id)
    if entry["kind"] == "dnt":
        lz_common.fail(
            "change is for a locale translation; a dnt entry has none",
            lz_common.EXIT_FAIL, entry=entry_id,
        )

    current = entry.get("translations", {}).get(locale)
    if current is None or current.get("status") != "approved" or current.get("value") != expect:
        lz_common.fail(
            f"{entry_id}/{locale}'s approved value does not match --expect",
            lz_common.EXIT_FAIL, entry=entry_id, locale=locale,
        )

    before = current.get("value")
    at = lz_common.now_iso()
    entry["translations"][locale] = {
        "value": value, "status": "approved", "approved_by": by, "approved_at": at,
    }

    event = {
        "at": at, "by": by, "action": "change", "entry": entry_id, "locale": locale,
        "before": before, "after": value, "reason": reason,
    }
    _append_history(root, event)
    freeze(root, canon)

    audit_request = {
        "entry": entry_id, "kind": entry["kind"], "source": entry["source"], "locale": locale,
        "before": before, "after": value, "reason": reason, "by": by, "at": at,
        "occurrences": list(entry.get("occurrences", [])),
    }
    lz_common.atomic_write_json(Path(root) / "runs" / locale / f"canon-audit-{entry_id}.json", audit_request)

    return event


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_import(args) -> int:
    root = lz_common.resolve_root(args.root)
    data = lz_common.read_json(Path(args.file), "canon import file")
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list):
        lz_common.fail("canon import file must have a 'candidates' list", lz_common.EXIT_CANNOT, file=args.file)
    canon = load(root)
    result = import_candidates(canon, candidates)
    save(root, canon)
    lz_common.emit({"ok": True, **result})
    return lz_common.EXIT_OK


def _cmd_approve(args) -> int:
    root = lz_common.resolve_root(args.root)
    canon = load(root)
    result = approve(root, canon, args.entry, args.locale, args.value, args.by)
    save(root, canon)
    lz_common.emit({"ok": True, **result})
    return lz_common.EXIT_OK


def _cmd_freeze(args) -> int:
    root = lz_common.resolve_root(args.root)
    lock = freeze(root)
    lz_common.emit({"ok": True, "entries": len(lock["entries"])})
    return lz_common.EXIT_OK


def _cmd_change(args) -> int:
    root = lz_common.resolve_root(args.root)
    canon = load(root)
    event = change(root, canon, args.entry, args.locale, args.expect, args.value, args.reason, args.by)
    save(root, canon)
    lz_common.emit({"ok": True, **event})
    return lz_common.EXIT_OK


def build_parser():
    parser = lz_common.make_parser("canon.py", "Canon (glossary) management")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="merge a canon-turn or review-turn output")
    p_import.add_argument("--root", required=True)
    p_import.add_argument("--file", required=True)

    p_approve = sub.add_parser("approve", help="approve one locale's translation, or a dnt entry as a whole")
    p_approve.add_argument("--root", required=True)
    p_approve.add_argument("--entry", required=True)
    p_approve.add_argument("--locale")
    p_approve.add_argument("--value")
    p_approve.add_argument("--by", required=True)

    p_freeze = sub.add_parser("freeze", help="rewrite canon.lock.json")
    p_freeze.add_argument("--root", required=True)

    p_change = sub.add_parser("change", help="correct an already-approved translation")
    p_change.add_argument("--root", required=True)
    p_change.add_argument("--entry", required=True)
    p_change.add_argument("--locale", required=True)
    p_change.add_argument("--expect", required=True)
    p_change.add_argument("--value", required=True)
    p_change.add_argument("--reason", required=True)
    p_change.add_argument("--by", required=True)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    dispatch = {
        "import": _cmd_import, "approve": _cmd_approve, "freeze": _cmd_freeze, "change": _cmd_change,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    lz_common.run_main(main)
