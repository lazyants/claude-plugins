"""Ledger state management for software-localizer (plan section 7).

Stored as one file per locale, `R/ledger/<locale>.json`:

    {"schema": 1, "locale": "<locale>", "entries": {id: entry}}

-- never a single `R/ledger.json` -- so two processes each working one
locale (a `packets.py`/`export_values.py` run per locale, in parallel) never
write the same file. In memory `load()`/`save()` still use the pinned shape
`{"schema": 1, "locales": {locale: {id: entry}}}`; `save(root, ledger,
locales=None)` writes only the listed locales (`None` = every locale in the
in-memory dict), each atomically, so a locale-scoped caller never touches a
sibling locale's file. An entry:

    {"state": "pending|translated|stale|existing|human_locked|escalated",
     "source_sha256": "...", "context_sha256": "...", "style_sha256": "...",
     "project_value_sha256": "...", "last_exported_sha256": None,
     "candidate": None, "rounds": 0, "notes": []}

`sync()` is the only place these states move for reasons other than a human
decision (`adopt`) or a model turn's outcome (`set_candidate`/`record_verdict`
/`mark_escalated`, applied by `packets.py`) or an export
(`record_export`, applied by `export_values.py`). Those four plus `load`,
`save` and `exportable` are the cross-module surface: `packets.py` and
`export_values.py` import and call them directly, never through the CLI.

`sync()` takes a `parse_fn` (a unary callable: `items -> {key: {"ok": ...}}`,
the same result shape `adapter_client.parse` returns) instead of importing
`adapter_client` itself, so a test can pass a fake and the pure logic never
needs a real adapter. The CLI's `sync` subcommand is the only place that
binds `parse_fn` to the real `adapter_client.parse` (imported lazily, inside
the command function, so importing `ledger` never requires `adapter_client`
to exist).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import checks
import lz_common

STATES = ("pending", "translated", "stale", "existing", "human_locked", "escalated")

# States sync() may move straight to "existing" when a project value shows up
# that the plugin itself did not export (plan section 7, second bullet).
_MAY_BECOME_EXISTING = ("pending", "stale", "escalated")

# States sync() only annotates with a note on drift, never moves (plan
# section 7, third bullet).
_NOTE_ONLY_ON_DRIFT = ("existing", "human_locked")


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


def _ledger_dir(root) -> Path:
    return Path(root) / "ledger"


def _locale_path(root, locale: str) -> Path:
    return _ledger_dir(root) / f"{locale}.json"


def load(root) -> dict:
    ledger_dir = _ledger_dir(root)
    locales: dict = {}
    if ledger_dir.is_dir():
        for path in sorted(ledger_dir.glob("*.json")):
            data = lz_common.read_json(path, f"ledger/{path.name}")
            locale = data.get("locale", path.stem)
            locales[locale] = data.get("entries", {})
    return {"schema": 1, "locales": locales}


def save(root, ledger: dict, locales: list | None = None) -> None:
    """Write only `locales` (default: every locale currently in `ledger`),
    each to its own file. A caller scoped to one locale passes `locales=[L]`
    so it never rewrites a sibling locale's file out from under a parallel
    process working that locale."""
    target = locales if locales is not None else list(ledger.get("locales", {}).keys())
    for locale in target:
        entries = ledger.get("locales", {}).get(locale, {})
        lz_common.atomic_write_json(_locale_path(root, locale), {
            "schema": 1, "locale": locale, "entries": entries,
        })


# ---------------------------------------------------------------------------
# per-locale content hashes (plan section 7)
# ---------------------------------------------------------------------------


def source_sha256(message: dict) -> str:
    return lz_common.value_sha256(message["source"])


def context_sha256(message: dict, locale: str) -> str:
    """The message's plural spec *for this locale*, `max_length`, and the
    sorted `count_arguments` — exactly the three things plan section 7 names,
    nothing else (`general_index`/`source_labels` drift is not tracked here;
    it is folded into `source_sha256` only insofar as the source itself
    changes)."""
    plural = message.get("plural")
    if plural is None:
        target_labels = None
        count_arguments: list = []
    else:
        target_labels = plural.get("target_labels", {}).get(locale)
        count_arguments = sorted(plural.get("count_arguments", []))
    max_length = (message.get("context") or {}).get("max_length")
    payload = {
        "plural_target_labels": target_labels,
        "max_length": max_length,
        "count_arguments": count_arguments,
    }
    return lz_common.sha256_json(payload)


def style_sha256(cfg: dict, locale: str) -> str:
    return lz_common.sha256_json(cfg.get("style", {}).get(locale))


def _project_value_sha256(message: dict, locale: str) -> str | None:
    value = (message.get("targets") or {}).get(locale)
    if value is None:
        return None
    return lz_common.value_sha256(value)


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


def _new_entry(state: str, source_sha: str, context_sha: str, style_sha: str, project_sha) -> dict:
    return {
        "state": state,
        "source_sha256": source_sha,
        "context_sha256": context_sha,
        "style_sha256": style_sha,
        "project_value_sha256": project_sha,
        "last_exported_sha256": None,
        "candidate": None,
        "rounds": 0,
        "notes": [],
    }


def _clear_candidate_if_stale(entry: dict, new_source_sha: str, new_context_sha: str, new_style_sha: str) -> bool:
    """Clear `entry["candidate"]` when its snapshot no longer matches the
    message's current hashes, or (for an audit candidate) when the project
    value it judged has since moved. Returns True when it cleared one."""
    candidate = entry.get("candidate")
    if candidate is None:
        return False
    snapshot_mismatch = (
        candidate.get("source_sha256") != new_source_sha
        or candidate.get("context_sha256") != new_context_sha
        or candidate.get("style_sha256") != new_style_sha
    )
    audited_mismatch = (
        candidate.get("origin") == "audit"
        and candidate.get("audited_target_sha256") is not None
        and candidate.get("audited_target_sha256") != entry["project_value_sha256"]
    )
    if snapshot_mismatch or audited_mismatch:
        entry["candidate"] = None
        return True
    return False


def sync(root, cfg: dict, messages: dict, parse_fn: Callable[[list], dict], canon_lock: dict) -> dict:
    """Apply plan section 7's transitions for every (locale, id) and persist
    the result. Returns a report: `{"gone": [...], "notes": [...], "counts":
    {locale: {...}}}`."""
    ledger = load(root)
    locales_dict = ledger.setdefault("locales", {})
    target_locales = cfg["target_locales"]

    report: dict = {"gone": [], "notes": [], "counts": {}}
    recheck_items: list = []

    for locale in target_locales:
        locale_entries = locales_dict.setdefault(locale, {})
        counts = {
            "new": 0, "existing_from_pending": 0, "human_locked": 0, "stale": 0,
            "notes": 0, "candidates_cleared": 0, "gone": 0, "context_rechecked": 0,
        }
        seen_ids: set = set()

        for message in messages["messages"]:
            msg_id = message["id"]
            seen_ids.add(msg_id)

            new_project_sha = _project_value_sha256(message, locale)
            new_source_sha = source_sha256(message)
            new_context_sha = context_sha256(message, locale)
            new_style_sha = style_sha256(cfg, locale)

            entry = locale_entries.get(msg_id)
            if entry is None:
                state = "existing" if new_project_sha is not None else "pending"
                locale_entries[msg_id] = _new_entry(
                    state, new_source_sha, new_context_sha, new_style_sha, new_project_sha,
                )
                counts["new"] += 1
                continue

            state = entry["state"]
            changed = []
            if entry["source_sha256"] != new_source_sha:
                changed.append("source")
            if entry["context_sha256"] != new_context_sha:
                changed.append("context")
            if entry["style_sha256"] != new_style_sha:
                changed.append("style")

            if (
                state in _MAY_BECOME_EXISTING
                and new_project_sha is not None
                and (entry["last_exported_sha256"] is None or new_project_sha != entry["last_exported_sha256"])
            ):
                entry["state"] = "existing"
                entry["candidate"] = None
                counts["existing_from_pending"] += 1
            elif state in _NOTE_ONLY_ON_DRIFT:
                if changed:
                    note_text = f"{', '.join(changed)} changed while {state}"
                    entry["notes"].append({"at": lz_common.now_iso(), "note": note_text})
                    report["notes"].append({"locale": locale, "id": msg_id, "note": note_text})
                    counts["notes"] += 1
            elif state == "translated":
                if new_project_sha != entry["last_exported_sha256"]:
                    entry["state"] = "human_locked"
                    counts["human_locked"] += 1
                elif "source" in changed or "style" in changed:
                    entry["state"] = "stale"
                    counts["stale"] += 1
                elif "context" in changed:
                    target_value = (message.get("targets") or {}).get(locale)
                    recheck_items.append({
                        "locale": locale, "id": msg_id, "message": message, "value": target_value,
                    })
                    counts["context_rechecked"] += 1
            # else: state in (pending, stale, escalated) with no drift into
            # "existing" this round -- nothing else to do here.

            entry["source_sha256"] = new_source_sha
            entry["context_sha256"] = new_context_sha
            entry["style_sha256"] = new_style_sha
            entry["project_value_sha256"] = new_project_sha
            if _clear_candidate_if_stale(entry, new_source_sha, new_context_sha, new_style_sha):
                counts["candidates_cleared"] += 1

        for msg_id in locale_entries:
            if msg_id not in seen_ids:
                report["gone"].append({"locale": locale, "id": msg_id})
                counts["gone"] += 1

        report["counts"][locale] = counts

    if recheck_items:
        _apply_context_rechecks(recheck_items, parse_fn, canon_lock, cfg, locales_dict, report)

    save(root, ledger, locales=list(target_locales))
    return report


def _apply_context_rechecks(
    recheck_items: list, parse_fn: Callable[[list], dict], canon_lock: dict, cfg: dict,
    locales_dict: dict, report: dict,
) -> None:
    """Re-run §9 checks on the project's current value for every `translated`
    entry whose context changed (plan section 7, fourth bullet), in one
    batched `parse_fn` call. A check failure moves the entry to `stale`."""
    parse_items: list = []
    plan: list = []
    for info in recheck_items:
        locale, msg_id, message, value = info["locale"], info["id"], info["message"], info["value"]
        source_forms = checks.forms_of(message["source"])
        value_forms = checks.forms_of(value)

        source_keys = [f"{locale}:{msg_id}:source:{i}" for i in range(len(source_forms))]
        value_keys = [f"{locale}:{msg_id}:value:{i}" for i in range(len(value_forms))]
        for key, form in zip(source_keys, source_forms):
            parse_items.append({"key": key, "text": form})
        for key, form in zip(value_keys, value_forms):
            parse_items.append({"key": key, "text": form})

        is_plural = message.get("plural") is not None
        plan.append((locale, msg_id, message, value, source_keys, value_keys, is_plural))

    results = parse_fn(parse_items) if parse_items else {}

    for locale, msg_id, message, value, source_keys, value_keys, is_plural in plan:
        if is_plural:
            source_parse: Any = [results[k] for k in source_keys]
            value_parse: Any = [results[k] for k in value_keys]
        else:
            source_parse = results[source_keys[0]]
            value_parse = results[value_keys[0]]

        problems = checks.check_candidate(message, locale, value, source_parse, value_parse, canon_lock, cfg)
        entry = locales_dict[locale][msg_id]
        if problems:
            entry["state"] = "stale"
            note_text = "context changed and the re-check failed"
            entry["notes"].append({"at": lz_common.now_iso(), "note": note_text, "problems": problems})
            report["notes"].append({"locale": locale, "id": msg_id, "note": note_text})
            report["counts"][locale]["stale"] += 1


# ---------------------------------------------------------------------------
# adopt
# ---------------------------------------------------------------------------


def adopt(ledger: dict, locale: str, ids: list | None, by: str) -> dict:
    """Move `existing`/`human_locked` entries to `translated`, treating the
    project's current value as if the plugin had exported it. `ids=None`
    adopts every eligible id for `locale` (`--all-existing`)."""
    locale_entries = ledger.get("locales", {}).get(locale, {})
    if ids is None:
        ids = [i for i, e in locale_entries.items() if e["state"] in ("existing", "human_locked")]

    adopted, skipped = [], []
    for msg_id in ids:
        entry = locale_entries.get(msg_id)
        if entry is None or entry["state"] not in ("existing", "human_locked"):
            skipped.append(msg_id)
            continue
        entry["state"] = "translated"
        entry["last_exported_sha256"] = entry["project_value_sha256"]
        adopted.append(msg_id)

    return {"adopted": adopted, "skipped": skipped, "by": by, "at": lz_common.now_iso()}


# ---------------------------------------------------------------------------
# functions packets.py / export_values.py call directly (never shell out)
# ---------------------------------------------------------------------------


def set_candidate(ledger: dict, locale: str, msg_id: str, candidate: dict) -> None:
    ledger["locales"][locale][msg_id]["candidate"] = dict(candidate)


def record_verdict(ledger: dict, locale: str, msg_id: str, verdict: dict) -> None:
    """Attach `verdict` to the id's candidate, unless it was cast on a value
    that is no longer the candidate (a stale verdict is silently dropped)."""
    entry = ledger["locales"][locale][msg_id]
    candidate = entry.get("candidate")
    if candidate is None or verdict.get("value_sha256") != candidate.get("value_sha256"):
        return
    candidate["verdict"] = dict(verdict)


def mark_escalated(ledger: dict, locale: str, msg_id: str, problems) -> None:
    entry = ledger["locales"][locale][msg_id]
    entry["state"] = "escalated"
    entry.setdefault("notes", []).append({
        "at": lz_common.now_iso(), "note": "escalated", "problems": problems,
    })


def exportable(ledger: dict, locale: str) -> list:
    """Ids ready for `export_values.py` (plan section 11, step 3)."""
    ids = []
    for msg_id, entry in ledger.get("locales", {}).get(locale, {}).items():
        candidate = entry.get("candidate")
        if candidate is None or candidate.get("checks") != "pass":
            continue
        verdict = candidate.get("verdict")
        if not verdict or verdict.get("verdict") != "pass":
            continue
        if verdict.get("value_sha256") != candidate.get("value_sha256"):
            continue
        eligible_state = entry.get("state") in ("pending", "stale", "translated", "escalated")
        if eligible_state or candidate.get("accepted_by"):
            ids.append(msg_id)
    return ids


def record_export(ledger: dict, locale: str, exported: dict) -> None:
    """After a successful export: each exported id becomes `translated`,
    with `last_exported_sha256` and `project_value_sha256` set to the value
    that was written (plan section 11, step 5)."""
    for msg_id, value in exported.items():
        entry = ledger["locales"][locale][msg_id]
        value_sha = lz_common.value_sha256(value)
        entry["state"] = "translated"
        entry["last_exported_sha256"] = value_sha
        entry["project_value_sha256"] = value_sha


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_project_dir(root, cfg: dict) -> str:
    p = Path(cfg["project_root"])
    if not p.is_absolute():
        p = Path(root) / p
    return str(p)


def _cmd_sync(args) -> int:
    root = lz_common.resolve_root(args.root)
    cfg = lz_common.load_config(root)
    messages = lz_common.load_messages(root / "messages.json")

    import canon  # local import: canon.py is this owner's sibling module
    canon_lock = canon.load_lock(root)

    import adapter_client  # lazy: only the CLI needs a real adapter
    project_dir = _resolve_project_dir(root, cfg)

    def parse_fn(items):
        return adapter_client.parse(str(root), cfg, project_dir, items)

    report = sync(root, cfg, messages, parse_fn, canon_lock)
    lz_common.emit({"ok": True, **report})
    return lz_common.EXIT_OK


def _cmd_adopt(args) -> int:
    root = lz_common.resolve_root(args.root)
    ledger = load(root)
    ids = None if args.all_existing else list(args.ids or [])
    if not args.all_existing and not ids:
        lz_common.fail("provide --id at least once, or --all-existing", lz_common.EXIT_CANNOT)
    result = adopt(ledger, args.locale, ids, args.by)
    save(root, ledger, locales=[args.locale])
    lz_common.emit({"ok": True, **result})
    return lz_common.EXIT_OK


def build_parser():
    parser = lz_common.make_parser("ledger.py", "Ledger state management")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="sync the ledger against the last collect")
    p_sync.add_argument("--root", required=True)

    p_adopt = sub.add_parser("adopt", help="treat existing project values as plugin translations")
    p_adopt.add_argument("--root", required=True)
    p_adopt.add_argument("--locale", required=True)
    group = p_adopt.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", action="append", dest="ids", metavar="ID")
    group.add_argument("--all-existing", action="store_true")
    p_adopt.add_argument("--by", required=True)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "sync":
        return _cmd_sync(args)
    if args.command == "adopt":
        return _cmd_adopt(args)
    lz_common.fail(f"unknown command: {args.command}", lz_common.EXIT_CANNOT)


if __name__ == "__main__":
    lz_common.run_main(main)
