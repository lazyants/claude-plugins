"""Canon (glossary) management for software-localizer.

`R/canon.json`: `{"schema": 1, "entries": [...]}`, an entry:

    {"id": "t-cart", "kind": "term|ui_label|dnt", "source": "Cart", "note": "",
     "occurrences": ["frontend:shop.cart.title"],
     "translations": {"de": {"value": "Warenkorb", "status": "proposed|approved",
                              "approved_by": None, "approved_at": None,
                              "current": ["Im Warenkorb"]}}}

`current` (only ever set by `import`, on a still-`proposed` translation) is
the locale's live renderings every canon turn that proposed this entry saw,
unioned across batches — kept for an operator to see side by side with the
proposal; `approve` drops it and `freeze` never carries it into
`canon.lock.json`.

`import` merges in candidates from a canon-turn's output:

    {"candidates": [{"kind", "source", "note", "occurrences",
                      "translations": {locale: {"proposed": value, "current": [...]}}}]}

A caller importing a review turn's `new_canon_candidates` (same table) wraps
that list in `{"candidates": [...]}` in this same shape before calling
`import --file F` — this module only ever reads the canonical shape above.

`R/canon.lock.json` is the frozen subset packets embed: every *approved*
`dnt` entry (`approve`'s `approved_by`/`approved_at`, set on the entry as a
whole) plus every entry with at least one *approved* translation (only those
translations, never the still-proposed ones), each entry with its own
sha256. A `dnt` entry a person never approved must not lock -- a
model-proposed `dnt` from a canon or review turn is only ever `import`ed, and
`import` never approves anything, so an unreviewed proposal must not become
an active do-not-translate rule the moment someone runs `freeze` (security-
review observation). `approved_occurrences` snapshots a DNT's active scope;
later imports extend `occurrences` with pending proposals, never that snapshot.
Legacy approved entries without it retain their existing scope until the next
import captures it. `scope` displays active/pending ids and the entire entry's
hash; `change-scope` applies only a person's hash-bound explicit delta.
`R/canon.history.json` is an append-only record of
every approval and change (who, when, before, after).

`R/canon.research/<locale>.json` holds the usage-research evidence for that
locale's entries (one file per locale, so parallel per-locale loops never
write the same file):

    {"schema": 1, "locale": "de", "entries": {"t-cart": {
        "kind": "term", "source": "Cart",
        "research": {"recommendation", "confidence", "note", "progress"?,
                     "findings": [{"site", "url", "rendering", "quote"}]},
        "research_sha256": "...", "researched": {"run", "at"},
        "verification": None | {"research_sha256", "findings":
            [{"index", "confirmed", "detail"}], "verdict", "problems",
            "better", "run", "at", "packet", "progress"?},
        "attempts": [{"kind", "packet", "at", "answer"}]}}}

`progress` distinguishes a complete bounded search (even with no findings)
from resource-limited work with exact remaining checks. `attempts` retains
the accepted answers; new packets carry checkpoints for safe continuation.
Only `packets.py accept` of a research or research_verify run writes it.
Nothing hashes it and `freeze` never reads it: evidence for a person's
decision, never an input to a candidate, a check or the lock. `approve`
copies `research_summary` of the entry's record into its history event.

`load_lock` (`packets.py` builds packets from it; `ledger.py`'s CLI passes
it into `sync`) and the research helpers (`load_research`, `save_research`,
`research_sha256`, `research_summary`, `research_flags`; used by
`packets.py` and `report.py`) are the functions other owners import
directly; `import`, `approve`, `freeze` and `change` are reached through the
CLI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lz_common  # noqa: E402

_KIND_PREFIX = {"term": "t", "ui_label": "u", "dnt": "d"}


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


def _read_or_default(path: Path, label: str, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    return lz_common.read_json(path, label)


def load(root) -> dict:
    return _read_or_default(Path(root) / "canon.json", "canon.json", {"schema": 1, "entries": []})


def save(root, canon: dict) -> None:
    lz_common.atomic_write_json(Path(root) / "canon.json", canon)


def load_lock(root) -> dict:
    return _read_or_default(Path(root) / "canon.lock.json", "canon.lock.json", {"schema": 1, "entries": []})


def load_history(root) -> dict:
    return _read_or_default(Path(root) / "canon.history.json", "canon.history.json", {"schema": 1, "events": []})


def _append_history(root, event: dict) -> None:
    history = load_history(root)
    history["events"].append(event)
    lz_common.atomic_write_json(Path(root) / "canon.history.json", history)


# ---------------------------------------------------------------------------
# usage-research evidence (see the module docstring)
# ---------------------------------------------------------------------------

RESEARCH_DIR_NAME = "canon.research"
RESEARCH_CONFIDENCE = ("high", "medium", "low")
RESEARCH_VERDICTS = ("holds", "weak", "refuted")


def resource_limited(result: dict | None) -> bool:
    """Legacy answers have no typed progress; never infer it from prose."""
    return bool(result and (result.get("progress") or {}).get("status") == "resource_limited")


def research_path(root, locale: str) -> Path:
    return Path(root) / RESEARCH_DIR_NAME / f"{locale}.json"


def load_research(root, locale: str) -> dict:
    return _read_or_default(
        research_path(root, locale), f"canon research ({locale})",
        {"schema": 1, "locale": locale, "entries": {}},
    )


def save_research(root, locale: str, data: dict) -> None:
    lz_common.atomic_write_json(research_path(root, locale), data)


def research_sha256(research: dict) -> str:
    """The hash a verification verdict binds to: a re-researched entry gets a
    new one, so a verdict cast on the old research never attaches to it."""
    return lz_common.sha256_json(research)


def research_summary(record: dict) -> dict:
    """The short view of one entry's evidence a person sees beside the
    proposal. `confirmed`, `verdict` and `better` stay `None` until the
    research has been verified."""
    research = record.get("research") or {}
    verification = record.get("verification")
    summary = {
        "recommendation": research.get("recommendation"),
        "confidence": research.get("confidence"),
        "findings": len(research.get("findings") or []),
        "confirmed": None, "verdict": None, "better": None,
    }
    if verification and not resource_limited(verification):
        summary["confirmed"] = sum(1 for f in verification.get("findings") or [] if f.get("confirmed") is True)
        summary["verdict"] = verification.get("verdict")
        summary["better"] = verification.get("better") or None
    for phase, result in (("research", research), ("verification", verification)):
        if result and "progress" in result:
            summary[f"{phase}_progress"] = result["progress"]
    return summary


def research_flags(record: dict) -> list:
    """Why a native speaker should check this entry: empty when the research
    was verified, holds, is not low-confidence and has a confirmed source."""
    summary = research_summary(record)
    flags = []
    for phase in ("research", "verification"):
        if resource_limited(record.get(phase)):
            flags.append(f"{phase} resource limited")
    if summary["verdict"] is None:
        flags.append("not verified")
    elif summary["verdict"] != "holds":
        flags.append(f"verdict {summary['verdict']}")
    if summary["confidence"] == "low":
        flags.append("low confidence")
    if summary["verdict"] is not None and summary["confirmed"] == 0:
        flags.append("no confirmed source")
    return flags


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
    # `kind` is already validated to be in `_KIND_PREFIX` by the only
    # caller (`import_candidates`, below its own `kind not in _KIND_PREFIX`
    # check), so a lookup fallback here would never fire.
    prefix = _KIND_PREFIX[kind]
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
    """Merge `candidates` into `canon` in place.

    An entry with the same `kind` and `source` merges: occurrences are
    unioned, `note` fills in only if the existing entry's is empty, and an
    incoming locale's `proposed` value either creates a fresh `proposed`
    translation or replaces an existing *proposed* one -- an already
    `approved` translation is never overwritten by an import. A well-shaped
    `current` (a list of strings) is added to the `current` the replaced
    `proposed` translation already had, display-only (see the module
    docstring); a malformed one is silently dropped rather than refusing the
    whole candidate. Returns `{"added": [...ids...], "merged": [...ids...]}`.
    For an approved DNT, new occurrences remain pending until `change-scope`;
    importing a subset never removes either active or pending occurrences.

    Raises `ValueError` for a candidate missing `kind`/`source` or naming an
    unknown `kind`; by the time a canon-turn or review-turn output reaches
    here it has already been validated by `packets.py`, so this is a
    cannot-run condition (the CLI lets it bubble to `run_main`, exit 2), not
    a business refusal.

    A candidate whose `occurrences` is present but not a list of strings is
    instead skipped and reported in `"skipped"` -- defence for a
    hand-written import file (packets.py's own canon-turn acceptance
    already rejects this shape before it ever reaches disk, but this
    module's `import` is also reachable directly from the CLI).
    """
    added, merged, skipped = [], [], []
    for candidate in candidates:
        kind = candidate.get("kind")
        source = candidate.get("source")
        if kind not in _KIND_PREFIX or not source:
            raise ValueError(f"invalid canon candidate (kind/source): {candidate!r}")

        raw_occurrences = candidate.get("occurrences") or []
        if not isinstance(raw_occurrences, list) or not all(isinstance(o, str) for o in raw_occurrences):
            skipped.append({"kind": kind, "source": source, "reason": "occurrences must be a list of strings"})
            continue
        occurrences = list(raw_occurrences)
        note = candidate.get("note") or ""
        translations_in = candidate.get("translations") or {}

        entry = _find_entry(canon, kind, source)
        if entry is None:
            entry = {
                "id": _new_id(canon, kind, source),
                "kind": kind,
                "source": source,
                "note": note,
                "occurrences": occurrences,
                "translations": {},
            }
            canon["entries"].append(entry)
            added.append(entry["id"])
        else:
            if kind == "dnt" and entry.get("approved_by"):
                # Legacy approvals cover the pre-import scope. New ids are
                # proposals until a person explicitly accepts their delta.
                entry.setdefault("approved_occurrences", list(entry.get("occurrences", [])))
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
            existing_translation = entry["translations"].get(locale)
            if existing_translation is not None and existing_translation.get("status") == "approved":
                continue
            new_translation = {
                "value": proposed, "status": "proposed",
                "approved_by": None, "approved_at": None,
            }
            # `current` (the locale's renderings the turns saw at proposal
            # time) is display-only: kept on the entry so an operator can
            # see it beside the proposal, but never fed into `freeze` -- it
            # is not part of what a candidate or a review verdict is checked
            # against. A term occurs all over a product, so several canon
            # batches propose it, each seeing only its own messages: the
            # merge keeps every batch's renderings (first-seen order, no
            # duplicates) while `value` is the latest proposal. Dropping the
            # earlier ones would hide exactly the inconsistency audit mode
            # exists to show. An empty list adds nothing, the same as an
            # absent `current` (a canon turn's "current" is the *existing*
            # renderings it found; a review turn's candidate carries none).
            merged_current = list((existing_translation or {}).get("current") or [])
            incoming_current = t.get("current") if isinstance(t, dict) else None
            if isinstance(incoming_current, list) and all(isinstance(c, str) for c in incoming_current):
                merged_current += incoming_current
            if merged_current:
                new_translation["current"] = list(dict.fromkeys(merged_current))
            entry["translations"][locale] = new_translation

    return {"added": added, "merged": merged, "skipped": skipped}


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
        if entry.get("approved_by"):
            lz_common.fail(
                "dnt entry is already approved; use `change-scope` for scope decisions",
                lz_common.EXIT_FAIL, entry=entry_id,
            )
        before = {"approved_by": entry.get("approved_by"), "approved_at": entry.get("approved_at")}
        entry["approved_by"] = by
        entry["approved_at"] = at
        entry["approved_occurrences"] = list(entry.get("occurrences", []))
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
    event = {
        "at": at, "by": by, "action": "approve", "entry": entry_id, "locale": locale,
        "before": before_value, "after": final_value,
    }
    result = {"entry": entry_id, "locale": locale, "value": final_value}
    # Evidence the person decided with, recorded beside the decision; it
    # never refuses an approval, whatever the research recommended.
    record = load_research(root, locale)["entries"].get(entry_id)
    if record is not None:
        event["research"] = result["research"] = research_summary(record)
    _append_history(root, event)
    return result


# ---------------------------------------------------------------------------
# freeze
# ---------------------------------------------------------------------------


def freeze(root, canon: dict | None = None) -> dict:
    if canon is None:
        canon = load(root)
    lock = _build_lock(canon)
    lz_common.atomic_write_json(Path(root) / "canon.lock.json", lock)
    return lock


def _build_lock(canon: dict) -> dict:
    """Prepare and validate a lock before any history or lock writes."""
    locked_entries = []
    for entry in canon["entries"]:
        if entry["kind"] == "dnt":
            if not entry.get("approved_by"):
                continue
            locked = {
                "id": entry["id"], "kind": entry["kind"], "source": entry["source"],
                "note": entry.get("note", ""), "occurrences": _active_dnt_scope(entry),
            }
        else:
            # `current` is display-only (see `import_candidates`) and never
            # reaches the frozen lock packets embed.
            approved = {
                loc: {k: v for k, v in t.items() if k != "current"}
                for loc, t in entry.get("translations", {}).items()
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
    groups = _freeze_groups(canon, locked_entries)
    if groups:
        lock["groups"] = groups
    return lock


def _freeze_groups(canon: dict, locked_entries: list) -> list:
    """Owner-reviewed, locale-scoped context; never imported from model output.

    Group membership and scope notes are maintained in canon.json through
    the project's normal review. Every member must already be approved for
    the group's locale. Groups stay outside entry hashes and enforcement.
    """
    entries = {entry["id"]: entry for entry in locked_entries}
    groups = []
    seen = set()
    for group in canon.get("groups", []):
        if not group.get("approved_by") or not group.get("approved_at"):
            continue
        for field in ("id", "locale", "note", "scope", "approved_by", "approved_at"):
            if not isinstance(group.get(field), str) or not group[field].strip():
                raise ValueError(f"approved canon group needs a nonempty {field}")
        ids = group.get("entry_ids")
        if (not isinstance(ids, list) or not ids
                or not all(isinstance(entry_id, str) for entry_id in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError(f"canon group {group['id']} needs distinct entry_ids")
        if group["id"] in seen:
            raise ValueError(f"duplicate canon group: {group['id']}")
        seen.add(group["id"])
        for entry_id in ids:
            entry = entries.get(entry_id)
            if entry is None or (entry["kind"] != "dnt" and
                    group["locale"] not in entry.get("translations", {})):
                raise ValueError(f"canon group {group['id']}: {entry_id} is not approved for {group['locale']}")
        groups.append({field: group[field] for field in (
            "id", "locale", "entry_ids", "note", "scope", "approved_by", "approved_at",
        )})
    return groups


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
    lock = _build_lock(canon)
    _append_history(root, event)
    lz_common.atomic_write_json(Path(root) / "canon.lock.json", lock)

    audit_request = {
        "entry": entry_id, "kind": entry["kind"], "source": entry["source"], "locale": locale,
        "before": before, "after": value, "reason": reason, "by": by, "at": at,
        "occurrences": list(entry.get("occurrences", [])),
    }
    lz_common.atomic_write_json(Path(root) / "runs" / locale / f"canon-audit-{entry_id}.json", audit_request)

    return event


# ---------------------------------------------------------------------------
# reviewed DNT scope changes
# ---------------------------------------------------------------------------


def _active_dnt_scope(entry: dict) -> list:
    """An absent snapshot preserves a legacy approval; an empty one is empty."""
    return list(entry.get("approved_occurrences", entry.get("occurrences", [])))


def scope(canon: dict, entry_id: str) -> dict:
    """Read the exact entry hash to bind a person's scope decision to."""
    entry = _find_by_id(canon, entry_id)
    if entry is None or entry.get("kind") != "dnt" or not entry.get("approved_by"):
        lz_common.fail("scope requires an approved dnt entry", lz_common.EXIT_FAIL, entry=entry_id)
    active = _active_dnt_scope(entry)
    return {
        "entry": entry_id, "source": entry["source"],
        "entry_sha256": lz_common.sha256_json(entry), "active": active,
        "pending": [i for i in entry.get("occurrences", []) if i not in active],
    }


def change_scope(root, canon: dict, entry_id: str, expect_sha256: str,
                 additions: list, removals: list, reason: str, by: str) -> dict:
    """Apply only an explicit person's DNT delta; imported proposals stay pending.

    Canon mutations are serialized by the coordinator, as with `change`.
    All business refusals and input reads precede the first mutation.
    """
    snapshot = scope(canon, entry_id)
    if snapshot["entry_sha256"] != expect_sha256:
        lz_common.fail("current entry does not match --expect-sha256", lz_common.EXIT_FAIL, entry=entry_id)
    if not by.strip() or not reason.strip():
        lz_common.fail("--by and --reason must be nonblank", lz_common.EXIT_FAIL)
    if (not additions and not removals
            or any(not isinstance(i, str) or not i.strip() for i in additions + removals)
            or len(set(additions)) != len(additions) or len(set(removals)) != len(removals)
            or set(additions) & set(removals)):
        lz_common.fail("supply nonempty, distinct, disjoint --add/--remove ids", lz_common.EXIT_FAIL)

    entry = _find_by_id(canon, entry_id)
    before = snapshot["active"]
    stored_before = list(entry.get("occurrences", []))
    if any(i in before for i in additions) or any(i not in stored_before for i in removals):
        lz_common.fail("--add is already active or --remove is not in the stored scope", lz_common.EXIT_FAIL)

    cfg = lz_common.load_config(root)
    messages = lz_common.load_messages(Path(root) / "messages.json")["messages"]
    known_ids = {m["id"] for m in messages}
    if any(i not in known_ids for i in additions):
        lz_common.fail("--add id not present in messages.json; collect first", lz_common.EXIT_FAIL)
    # Validate history before changing the in-memory canon, too.
    load_history(root)
    after = [i for i in before if i not in removals] + additions
    stored_after = [i for i in stored_before if i not in removals]
    stored_after += [i for i in additions if i not in stored_after]

    affected = []
    if before != after:
        # Keep removed/deleted ids for the packet builder's explicit skip report.
        affected = list(dict.fromkeys(before + after))
        # Use the same relevance rule as candidate hashes: source matches share
        # this entry's hash even outside its explicit DNT occurrence scope.
        import ledger
        impact_lock = {"entries": [{
            "id": entry_id, "kind": "dnt", "source": entry["source"], "occurrences": affected,
        }]}
        affected += [m["id"] for m in messages
                     if m["id"] not in affected and ledger.relevant_canon(m, impact_lock)]

    at = lz_common.now_iso()
    entry["occurrences"] = stored_after
    entry["approved_occurrences"] = after
    entry["scope_approved_by"] = by
    entry["scope_approved_at"] = at
    event = {
        "action": "change-scope", "entry": entry_id, "at": at, "by": by, "reason": reason,
        "before": before, "after": after,
        "stored_before": stored_before, "stored_after": stored_after,
        "additions": additions, "removals": removals,
        "entry_sha256_before": expect_sha256, "entry_sha256_after": lz_common.sha256_json(entry),
        "affected_occurrences": affected, "target_locales": list(cfg["target_locales"]),
    }
    lock = _build_lock(canon)
    _append_history(root, event)
    lz_common.atomic_write_json(Path(root) / "canon.lock.json", lock)
    for locale in cfg["target_locales"]:
        if not affected:
            continue
        audit_request = {
            **event, "kind": "dnt", "source": entry["source"], "locale": locale,
            "occurrences": affected, "scope_change": {"before": before, "after": after},
        }
        lz_common.atomic_write_json(
            Path(root) / "runs" / locale / f"canon-audit-{entry_id}.json", audit_request,
        )
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


def _cmd_scope(args) -> int:
    root = lz_common.resolve_root(args.root)
    lz_common.emit({"ok": True, **scope(load(root), args.entry)})
    return lz_common.EXIT_OK


def _cmd_change_scope(args) -> int:
    root = lz_common.resolve_root(args.root)
    canon = load(root)
    event = change_scope(root, canon, args.entry, args.expect_sha256,
                         args.add, args.remove, args.reason, args.by)
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

    p_scope = sub.add_parser("scope", help="inspect an approved dnt scope and its expected entry hash")
    p_scope.add_argument("--root", required=True)
    p_scope.add_argument("--entry", required=True)

    p_scope_change = sub.add_parser("change-scope", help="record a person's explicit dnt scope decision")
    p_scope_change.add_argument("--root", required=True)
    p_scope_change.add_argument("--entry", required=True)
    p_scope_change.add_argument("--expect-sha256", required=True)
    p_scope_change.add_argument("--add", action="append", default=[])
    p_scope_change.add_argument("--remove", action="append", default=[])
    p_scope_change.add_argument("--reason", required=True)
    p_scope_change.add_argument("--by", required=True)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    dispatch = {
        "import": _cmd_import, "approve": _cmd_approve, "freeze": _cmd_freeze, "change": _cmd_change,
        "scope": _cmd_scope, "change-scope": _cmd_change_scope,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    lz_common.run_main(main)
