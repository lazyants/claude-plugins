#!/usr/bin/env python3
"""Read-only status report (plan sections 11-12). Never writes.

Reports counts per locale and state from the ledger, whether
`adapter.lock.json` is present and current, and the next command to run.
Every input is read defensively (a missing or corrupt file is reported as
`"absent"`/`"unknown"`, never a crash): the operator runs this at any point
in an unfinished pipeline, including before `localize.json` even exists.

The ledger is stored per locale at `R/ledger/<locale>.json`
(`{"schema": 1, "locale": L, "entries": {id: entry}}`); there is no
`R/ledger.json`. `_load_ledger` still reads every `R/ledger/*.json` file
directly, field by field, into the in-memory shape `{"schema": 1,
"locales": {locale: {id: entry}}}` -- a missing `R/ledger/` directory means
no locale has been synced yet, same as an empty one, and any file that is
missing, unreadable, or the wrong shape is skipped rather than raising.
`_next_command` does import `ledger.py` for the one rule it must not
re-derive (`ledger.exportable`, the same readiness rule `export_values.py`
itself enforces) -- that call is wrapped so a locale whose entries are too
malformed for it to read falls back to "unknown" for that locale rather
than crashing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ledger as ledger_mod  # noqa: E402
import lz_common  # noqa: E402


def _read_optional(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_ledger(root: Path) -> dict:
    """The ledger, aggregated across every target locale, read directly from
    `R/ledger/<locale>.json` (each `{"schema": 1, "locale": L, "entries":
    {id: entry}}`) into `{"schema": 1, "locales": {locale: {id: entry}}}`.
    This stays a direct, field-by-field read (not `ledger.load`) so a
    missing `R/ledger/` directory, or one with no readable files, still
    means "no locale synced yet" rather than a crash; any file that is
    missing, unreadable, or the wrong shape is skipped rather than raising.
    `ledger.py` is still imported elsewhere in this module (`_next_command`
    calls `ledger.exportable` on the dict this function returns, whose
    shape matches what `exportable` expects) -- only the read path here
    stays local."""
    locales: dict = {}
    ledger_dir = root / "ledger"
    if ledger_dir.is_dir():
        for path in sorted(ledger_dir.glob("*.json")):
            data = _read_optional(path)
            if not isinstance(data, dict):
                continue
            entries = data.get("entries")
            if not isinstance(entries, dict):
                continue
            locale = data.get("locale", path.stem)
            locales[locale] = entries
    return {"schema": 1, "locales": locales}


def _ledger_counts(ledger: dict) -> dict:
    """`{locale: {state: count}}` from a loaded ledger."""
    result: dict = {}
    locales = ledger.get("locales", {}) if isinstance(ledger, dict) else {}
    if not isinstance(locales, dict):
        return result
    for locale, entries in locales.items():
        by_state: dict = {}
        if isinstance(entries, dict):
            for entry in entries.values():
                state = entry.get("state", "unknown") if isinstance(entry, dict) else "unknown"
                by_state[state] = by_state.get(state, 0) + 1
        result[locale] = by_state
    return result


def _candidate_needs_review(entry) -> bool:
    """checks passed but no verdict is bound to the candidate's current
    value hash yet -- the same rule `packets.select_review` uses. `ledger.py`
    has no equivalent exposed helper for this one, so it stays local (unlike
    export-readiness below, which now calls `ledger.exportable` instead of
    re-deriving the rule)."""
    if not isinstance(entry, dict):
        return False
    cand = entry.get("candidate")
    if not isinstance(cand, dict) or cand.get("checks") != "pass":
        return False
    verdict = cand.get("verdict")
    return not isinstance(verdict, dict) or verdict.get("value_sha256") != cand.get("value_sha256")


def _exportable_ids_by_locale(ledger: dict, target_locales: list) -> dict:
    """`{locale: set(ids) | None}` via `ledger.exportable` -- the one place
    export-readiness is defined, so this calls it instead of keeping a
    second copy of the rule that can drift from the real one. `None` means
    that locale's entries were too malformed for `exportable` to read (it
    indexes with `entry["candidate"]`/`.get(...)` without guarding against a
    non-dict entry); that locale is then left out of every decision below
    -- "unknown", never a crash and never a guess."""
    result: dict = {}
    for locale in target_locales:
        try:
            result[locale] = set(ledger_mod.exportable(ledger, locale))
        except Exception:
            result[locale] = None
    return result


def _adapter_lock_status(root: Path, cfg) -> dict:
    """`{"present": bool, "current": bool | "unknown"}`. `"unknown"` covers
    every case where currency cannot be determined without a valid config
    or a readable lock file, so this never raises."""
    lock_path = root / "adapter.lock.json"
    if not lock_path.is_file():
        return {"present": False, "current": False}

    lock = _read_optional(lock_path)
    if lock is None or not isinstance(cfg, dict):
        return {"present": True, "current": "unknown"}

    if lz_common.validate_config(cfg, root):
        return {"present": True, "current": "unknown"}

    try:
        current = lz_common.adapter_digest(root, cfg)
    except SystemExit:
        return {"present": True, "current": "unknown"}

    # Same three fields `lz_common.require_accepted_adapter` compares (files,
    # argv, options_sha256) -- reproduced rather than called, since that
    # function's job is to `fail()` and exit, not report a status.
    is_current = (
        lock.get("files") == current.get("files")
        and lock.get("argv") == current.get("argv")
        and lock.get("options_sha256") == current.get("options_sha256")
    )
    return {"present": True, "current": is_current}


def _next_command(root: Path, cfg, cfg_problems, adapter_check_ran, adapter_lock, messages, ledger, ledger_counts) -> str:
    """A best-effort staging hint following plan section 12's order. This is
    presence-based, not a full re-derivation of pipeline correctness (that
    is `export_values.py`'s job at export time)."""
    root_display = str(root)
    if cfg is None:
        return f"scaffold.py --root {root_display}"
    if cfg_problems:
        return f"fill in localize.json, then config_validate.py --root {root_display}"
    if not adapter_check_ran:
        return f"build the adapter under R/adapter/, then adapter_check.py run --root {root_display}"
    if not adapter_lock["present"] or adapter_lock["current"] is not True:
        return f"run the coverage turn, then adapter_check.py accept --root {root_display} --coverage <file> --by <name>"
    if messages is None:
        return f"collect.py --root {root_display}"
    if not ledger.get("locales"):
        return f"ledger.py sync --root {root_display}"

    target_locales = cfg.get("target_locales")
    target_locales = target_locales if isinstance(target_locales, list) else []
    locales_entries = ledger.get("locales", {}) if isinstance(ledger, dict) else {}
    exportable_by_locale = _exportable_ids_by_locale(ledger, target_locales)

    # plan section 12's order: a candidate already passing checks is closer
    # to done than one that still needs a translate round, so review and
    # export outrank translate; audit (revisiting a value the plugin never
    # produced) is last.
    for locale in target_locales:
        entries = locales_entries.get(locale, {})
        if isinstance(entries, dict) and any(_candidate_needs_review(e) for e in entries.values()):
            return f"packets.py build --root {root_display} --kind review --locale {locale}"

    for locale in target_locales:
        if exportable_by_locale.get(locale):  # a non-empty set; None (unknown) also skips
            return f"export_values.py --root {root_display} --locale {locale}"

    for locale in target_locales:
        entries = locales_entries.get(locale, {})
        exportable_ids = exportable_by_locale.get(locale)
        if not isinstance(entries, dict) or exportable_ids is None:
            continue  # unknown export-readiness here -- never guess "needs translate"
        for msg_id, e in entries.items():
            if isinstance(e, dict) and e.get("state") in ("pending", "stale") and msg_id not in exportable_ids:
                return f"packets.py build --root {root_display} --kind translate --locale {locale}"

    for locale in target_locales:
        counts = ledger_counts.get(locale, {})
        if counts.get("existing", 0) or counts.get("translated", 0):
            return f"packets.py build --root {root_display} --kind audit --locale {locale} (optional: audit review)"

    return f"export_values.py --root {root_display} --locale <locale>, then report.py --root {root_display} --locale <locale>"


def main() -> int:
    parser = lz_common.make_parser(prog="status.py", description="Read-only status report.")
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = lz_common.resolve_root(args.root)

    cfg = _read_optional(root / "localize.json")
    cfg_problems = lz_common.validate_config(cfg, root) if isinstance(cfg, dict) else [
        {"field": "<root>", "message": "localize.json is missing or not an object"}
    ]

    adapter_check_ran = (root / "runs" / "_adapter_check.json").is_file()
    adapter_lock = _adapter_lock_status(root, cfg)

    messages = _read_optional(root / "messages.json")
    ledger = _load_ledger(root)
    ledger_counts = _ledger_counts(ledger)

    next_command = _next_command(
        root, cfg, cfg_problems, adapter_check_ran, adapter_lock, messages, ledger, ledger_counts
    )

    result = {
        "ok": True,
        "root": str(root),
        "config_ready": isinstance(cfg, dict) and not cfg_problems,
        "adapter_lock": adapter_lock,
        "messages_collected": messages is not None,
        "ledger_present": bool(ledger.get("locales")),
        "counts": ledger_counts,
        "next_command": next_command,
    }

    print(f"software-localizer status: {root}", file=sys.stderr)
    print(f"  config ready: {result['config_ready']}", file=sys.stderr)
    print(f"  adapter lock: present={adapter_lock['present']} current={adapter_lock['current']}", file=sys.stderr)
    print(f"  messages collected: {result['messages_collected']}", file=sys.stderr)
    for locale in sorted(ledger_counts):
        by_state = ledger_counts[locale]
        parts = ", ".join(f"{state}={count}" for state, count in sorted(by_state.items()))
        print(f"  {locale}: {parts or '(no entries)'}", file=sys.stderr)
    print(f"  next: {next_command}", file=sys.stderr)

    lz_common.emit(result)
    return lz_common.EXIT_OK


if __name__ == "__main__":
    lz_common.run_main(main)
