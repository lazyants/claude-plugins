#!/usr/bin/env python3
"""Export ledger candidates back into the live project (plan section 11).

Recovery of an interrupted previous export always runs first, unconditional
on the locale requested here — a leftover `in_progress` journal is a project
in an unknown state and blocks trust for every locale, not just the one that
produced it.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lz_common  # noqa: E402
import adapter_client  # noqa: E402
import ledger as ledger_mod  # noqa: E402

JOURNAL_SCHEMA = 1


# ---------------------------------------------------------------------------
# Atomic byte replace (export deals in raw file bytes, not JSON)
# ---------------------------------------------------------------------------


def _atomic_replace_bytes(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".lz-export-tmp-", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Recovery (plan section 11, step 1)
# ---------------------------------------------------------------------------


def _restore_backups(export_dir: Path, journal: dict) -> None:
    backup_dir = export_dir / "backup"
    for f in journal["files"]:
        data = (backup_dir / f["backup"]).read_bytes()
        _atomic_replace_bytes(Path(f["dest"]), data)


def recover_unfinished_exports(root: Path) -> list:
    exports_dir = root / "exports"
    recovered = []
    if not exports_dir.is_dir():
        return recovered
    for stamp_dir in sorted(p for p in exports_dir.iterdir() if p.is_dir()):
        journal_path = stamp_dir / "journal.json"
        if not journal_path.is_file():
            continue
        journal = lz_common.read_json(journal_path, f"export journal {journal_path}")
        if journal.get("status") != "in_progress":
            continue
        _restore_backups(stamp_dir, journal)
        journal["status"] = "rolled_back"
        journal["finished_at"] = lz_common.now_iso()
        lz_common.atomic_write_json(journal_path, journal)
        recovered.append(str(stamp_dir))
    return recovered


# ---------------------------------------------------------------------------
# Freshness guards (plan section 11, step 2)
# ---------------------------------------------------------------------------


def _staleness_problems(cfg: dict, locale: str, candidate_ids: list, by_id: dict, entries: dict) -> list:
    problems = []
    for msg_id in candidate_ids:
        message = by_id.get(msg_id)
        entry = entries.get(msg_id, {})
        candidate = entry.get("candidate") or {}
        if message is None:
            problems.append({"id": msg_id, "reason": "id is no longer present in the live project"})
            continue

        live_value = message.get("targets", {}).get(locale)
        live_sha = lz_common.value_sha256(live_value) if live_value is not None else None
        if candidate.get("accepted_by"):
            expected_sha = candidate.get("audited_target_sha256")
        else:
            expected_sha = entry.get("project_value_sha256")
        if live_sha != expected_sha:
            problems.append({"id": msg_id, "reason": "the live target changed since the last sync"})
            continue

        if ledger_mod.source_sha256(message) != candidate.get("source_sha256"):
            problems.append({"id": msg_id, "reason": "the source changed since this candidate was reviewed"})
            continue
        if ledger_mod.context_sha256(message, locale) != candidate.get("context_sha256"):
            problems.append({"id": msg_id, "reason": "the context changed since this candidate was reviewed"})
            continue
        if ledger_mod.style_sha256(cfg, locale) != candidate.get("style_sha256"):
            problems.append({"id": msg_id, "reason": "the locale style changed since this candidate was reviewed"})
    return problems


# ---------------------------------------------------------------------------
# Complete-record comparison (plan section 11, step 4)
# ---------------------------------------------------------------------------


def _compare_messages(live: dict, temp: dict, locale: str, values: dict) -> list:
    problems = []
    if set(live.get("files", [])) != set(temp.get("files", [])):
        problems.append({"reason": "the adapter's file list changed"})

    live_by_id = {m["id"]: m for m in live["messages"]}
    temp_by_id = {m["id"]: m for m in temp["messages"]}
    if set(live_by_id) != set(temp_by_id):
        problems.append({"reason": "the set of message ids changed"})

    for msg_id, live_msg in live_by_id.items():
        temp_msg = temp_by_id.get(msg_id)
        if temp_msg is None:
            continue
        if live_msg.get("source") != temp_msg.get("source"):
            problems.append({"id": msg_id, "reason": "source changed"})
        if live_msg.get("context") != temp_msg.get("context"):
            problems.append({"id": msg_id, "reason": "context changed"})
        if live_msg.get("plural") != temp_msg.get("plural"):
            problems.append({"id": msg_id, "reason": "plural spec changed"})

        live_targets = live_msg.get("targets", {})
        temp_targets = temp_msg.get("targets", {})
        for loc in set(live_targets) | set(temp_targets):
            live_value = live_targets.get(loc)
            temp_value = temp_targets.get(loc)
            if loc == locale and msg_id in values:
                if temp_value != values[msg_id]:
                    problems.append({"id": msg_id, "locale": loc,
                                      "reason": "the exported value does not match what was asked"})
            elif live_value != temp_value:
                problems.append({"id": msg_id, "locale": loc, "reason": "an unrelated target changed"})

    return problems


# ---------------------------------------------------------------------------
# Journal + backups + replacement (plan section 11, step 5)
# ---------------------------------------------------------------------------


def _do_real_export(root: Path, cfg: dict, locale: str, live_messages: dict, temp_project: Path,
                     candidate_ids: list, values: dict, changed_by_file: dict, ledger_data: dict,
                     recovered: list) -> dict:
    stamp = lz_common.now_iso().replace(":", "-")
    export_dir = root / "exports" / stamp
    project_root = Path(cfg["project_root"])
    # ledger.py stores one file per locale (`R/ledger/<locale>.json`), never
    # a single `R/ledger.json` -- so a parallel export of a different locale
    # never races this one's backup/replace. Only this locale's file is
    # backed up, updated and (on failure) restored.
    ledger_path = root / "ledger" / f"{locale}.json"

    # Only a file this export actually changed is backed up and replaced --
    # comparing the staged (already-exported) copy against the live file
    # tells us which, without re-running the adapter. A missing staged copy
    # is treated as "changed" defensively (stage_files should have copied
    # every listed file; this only guards a future contract change there).
    files_meta = []
    for relpath in live_messages.get("files", []):
        dest = project_root / relpath
        if not dest.is_file():
            continue
        staged = temp_project / relpath
        if staged.is_file() and staged.read_bytes() == dest.read_bytes():
            continue
        files_meta.append({"kind": "project", "relpath": relpath, "dest": str(dest),
                            "backup": f"project/{relpath}"})
    files_meta.append({"kind": "ledger", "relpath": None, "dest": str(ledger_path), "backup": "ledger.json"})

    backup_dir = export_dir / "backup"
    for meta in files_meta:
        data = Path(meta["dest"]).read_bytes()
        meta["sha256_before"] = lz_common.sha256_bytes(data)
        _atomic_replace_bytes(backup_dir / meta["backup"], data)

    journal = {
        "schema": JOURNAL_SCHEMA, "stamp": stamp, "locale": locale, "status": "in_progress",
        "started_at": lz_common.now_iso(), "finished_at": None,
        "files": [{"kind": m["kind"], "dest": m["dest"], "backup": m["backup"],
                    "sha256_before": m["sha256_before"]} for m in files_meta],
    }
    journal_path = export_dir / "journal.json"
    lz_common.atomic_write_json(journal_path, journal)

    try:
        for meta in files_meta:
            if meta["kind"] != "project":
                continue
            dest = Path(meta["dest"])
            current = dest.read_bytes()
            if lz_common.sha256_bytes(current) != meta["sha256_before"]:
                raise RuntimeError(f"{meta['relpath']} changed on disk during the export")
            new_bytes = (temp_project / meta["relpath"]).read_bytes()
            _atomic_replace_bytes(dest, new_bytes)

        ledger_mod.record_export(ledger_data, locale, values)
        ledger_mod.save(root, ledger_data, locales=[locale])

        journal["status"] = "done"
        journal["finished_at"] = lz_common.now_iso()
        lz_common.atomic_write_json(journal_path, journal)
    except BaseException as exc:
        _restore_backups(export_dir, journal)
        journal["status"] = "rolled_back"
        journal["finished_at"] = lz_common.now_iso()
        lz_common.atomic_write_json(journal_path, journal)
        lz_common.fail(f"export failed and was rolled back: {exc}", lz_common.EXIT_FAIL,
                        locale=locale, recovered=recovered)

    return {"ok": True, "locale": locale, "dry_run": False, "exported": len(candidate_ids),
            "changed_by_file": changed_by_file, "recovered": recovered, "journal": str(journal_path)}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def do_export(root: Path, locale: str, dry_run: bool) -> dict:
    recovered = recover_unfinished_exports(root)

    cfg = lz_common.load_config(root)
    if locale not in cfg["target_locales"]:
        lz_common.fail(f"locale is not a configured target: {locale}", lz_common.EXIT_CANNOT, locale=locale)
    lz_common.require_accepted_adapter(root, cfg)

    project_dir = str(Path(cfg["project_root"]))
    try:
        live_messages = adapter_client.collect(str(root), cfg, project_dir, str(root / "messages.json"))
    except adapter_client.AdapterError as exc:
        lz_common.fail(f"collect failed: {exc}", lz_common.EXIT_FAIL, locale=locale, recovered=recovered)
    by_id = {m["id"]: m for m in live_messages["messages"]}

    ledger_data = ledger_mod.load(root)
    candidate_ids = ledger_mod.exportable(ledger_data, locale)
    entries = ledger_data.get("locales", {}).get(locale, {})

    problems = _staleness_problems(cfg, locale, candidate_ids, by_id, entries)
    if problems:
        lz_common.fail("export refused: some candidates are stale", lz_common.EXIT_FAIL,
                        locale=locale, problems=problems, recovered=recovered)

    values = {msg_id: entries[msg_id]["candidate"]["value"] for msg_id in candidate_ids}
    if not values:
        return {"ok": True, "locale": locale, "dry_run": dry_run, "exported": 0,
                "changed_by_file": {}, "recovered": recovered}

    changed_by_file: dict = {}
    for msg_id in candidate_ids:
        file_path = by_id[msg_id].get("context", {}).get("file", "?")
        changed_by_file[file_path] = changed_by_file.get(file_path, 0) + 1

    with tempfile.TemporaryDirectory(dir=str(root)) as tmp:
        temp_project = Path(tmp) / "project"
        # Stage exactly the declared files, never the whole project (plan
        # section 6's staging refusal applies here too): a real project's
        # dependency/data directories dwarf its catalogs.
        lz_common.stage_files(project_dir, live_messages["files"], temp_project)
        try:
            adapter_client.export(str(root), cfg, str(temp_project), locale, values)
            temp_messages_path = Path(tmp) / "messages.json"
            temp_messages = adapter_client.collect(str(root), cfg, str(temp_project), str(temp_messages_path))
        except adapter_client.AdapterError as exc:
            lz_common.fail(f"export re-collect failed: {exc}", lz_common.EXIT_FAIL,
                            locale=locale, recovered=recovered)

        diff_problems = _compare_messages(live_messages, temp_messages, locale, values)
        if diff_problems:
            lz_common.fail("export refused: exporting changed more than the requested values",
                            lz_common.EXIT_FAIL, locale=locale, problems=diff_problems, recovered=recovered)

        if dry_run:
            return {"ok": True, "locale": locale, "dry_run": True, "exported": len(candidate_ids),
                    "changed_by_file": changed_by_file, "recovered": recovered}

        return _do_real_export(root, cfg, locale, live_messages, temp_project, candidate_ids, values,
                                changed_by_file, ledger_data, recovered)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = lz_common.make_parser("export_values.py", "Export ledger candidates back into the project.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--locale", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    root = lz_common.resolve_root(args.root)
    result = do_export(root, args.locale, args.dry_run)
    lz_common.emit(result)
    return lz_common.EXIT_OK


if __name__ == "__main__":
    lz_common.run_main(main)
