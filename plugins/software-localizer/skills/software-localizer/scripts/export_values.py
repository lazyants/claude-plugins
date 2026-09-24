#!/usr/bin/env python3
"""Export ledger candidates back into the live project (plan section 11).

Recovery of an interrupted previous export always runs first, unconditional
on the locale requested here — a leftover `in_progress` journal is a project
in an unknown state and blocks trust for every locale, not just the one that
produced it.

The whole flow for one call -- recovery, the freshness guards, staging and
the commit -- runs under one exclusive lock (`R/exports/.lock`). Without it,
two exports started in the same second could pick the same journal
directory, or one export's recovery could roll back another one that is
still running.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lz_common  # noqa: E402
import adapter_client  # noqa: E402
import canon as canon_mod  # noqa: E402
import ledger as ledger_mod  # noqa: E402

JOURNAL_SCHEMA = 1


# ---------------------------------------------------------------------------
# Exclusive lock (plan section 11's whole flow: recovery through commit)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _exclusive_export_lock(root: Path):
    """Hold `R/exports/.lock` (created if missing) for the whole call.
    Blocking is fine: a second export simply waits its turn instead of
    racing the first one's journal, backup or commit phase."""
    lock_dir = root / "exports"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".lock"
    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def _new_export_stamp() -> str:
    """A per-export directory name that can never collide, even for two
    exports started in the same second: `now_iso()` alone only has second
    precision."""
    return f"{lz_common.now_iso().replace(':', '-')}-{uuid.uuid4().hex[:8]}"


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


def _restore_backups(export_dir: Path, journal: dict) -> list:
    """Reconcile every journaled file against its CURRENT bytes, never a
    `replaced` flag set by a separate journal write after each replace (a
    flag that a crash landing between the replace and that write would
    leave stuck at `false` even though the replace had already happened).
    Per file: current bytes == `new_sha256` -> the replace landed -> restore
    the backup. current bytes == `backup_sha256` -> it never landed (or this
    already ran) -> nothing to do. Anything else -> someone edited the file
    after an interrupted export -> leave it, and report it as a conflict
    for the caller to surface. This one rule serves both next-run recovery
    and the real export's own exception-path rollback."""
    backup_dir = export_dir / "backup"
    conflicts = []
    for f in journal["files"]:
        dest = Path(f["dest"])
        current = dest.read_bytes() if dest.is_file() else None
        current_sha256 = lz_common.sha256_bytes(current) if current is not None else None
        if current_sha256 == f.get("new_sha256"):
            data = (backup_dir / f["backup"]).read_bytes()
            _atomic_replace_bytes(dest, data)
        elif current_sha256 == f.get("backup_sha256"):
            continue
        else:
            conflicts.append(f["dest"])
    return conflicts


def recover_unfinished_exports(root: Path) -> tuple[list, list]:
    """Returns `(recovered, conflicts)`: `recovered` is every leftover
    `in_progress` journal this call reconciled -- restoring what it can and
    leaving a conflicted file exactly as the person left it -- and then
    marked `rolled_back`, so it is never processed again (there is no
    command a person could run to clear an `in_progress` journal, so
    leaving one stuck there would wedge every future export). `conflicts`
    is every destination path left untouched because a person's edit
    landed on it since; the caller surfaces these in THIS run's failure
    output. A later call finds no `in_progress` journal left and proceeds
    normally -- the person's edit stays exactly as they left it, and the
    next `collect` + `ledger sync` will see it and lock the message
    (`human_locked`), which is the right outcome."""
    exports_dir = root / "exports"
    recovered = []
    conflicts = []
    if not exports_dir.is_dir():
        return recovered, conflicts
    for stamp_dir in sorted(p for p in exports_dir.iterdir() if p.is_dir()):
        journal_path = stamp_dir / "journal.json"
        if not journal_path.is_file():
            continue
        journal = lz_common.read_json(journal_path, f"export journal {journal_path}")
        if journal.get("status") != "in_progress":
            continue
        file_conflicts = _restore_backups(stamp_dir, journal)
        journal["status"] = "rolled_back"
        journal["finished_at"] = lz_common.now_iso()
        journal["conflicts"] = file_conflicts
        lz_common.atomic_write_json(journal_path, journal)
        recovered.append(str(stamp_dir))
        conflicts.extend(file_conflicts)
    return recovered, conflicts


# ---------------------------------------------------------------------------
# Freshness guards (plan section 11, step 2)
# ---------------------------------------------------------------------------


def _staleness_problems(cfg: dict, locale: str, candidate_ids: list, by_id: dict, entries: dict,
                         canon_lock: dict) -> list:
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
            continue
        # Canon can change after the candidate's verdict (a new dnt entry, a
        # newly approved term) without touching source/context/style at
        # all; a candidate snapshot taken against the old canon lock must
        # not export against the new one.
        if ledger_mod.canon_sha256(message, canon_lock) != candidate.get("canon_sha256"):
            problems.append({"id": msg_id, "reason": "the canon changed since this candidate was reviewed"})
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
# Which files an export actually touches (plan section 11, steps 4 and 5)
# ---------------------------------------------------------------------------


def _changed_files(live_messages: dict, temp_project: Path, project_root: Path) -> list:
    """Relative paths, from `live_messages["files"]`, whose bytes actually
    differ between the exported staged copy and the live project right now
    -- the export's real blast radius, found without re-running the
    adapter. `context.file` (a message's SOURCE file) is not this: a
    message's source file need not be the target file its locale value
    lives in at all."""
    changed = []
    for relpath in live_messages.get("files", []):
        dest = project_root / relpath
        if not dest.is_file():
            continue
        staged = temp_project / relpath
        if staged.is_file() and staged.read_bytes() == dest.read_bytes():
            continue
        changed.append(relpath)
    return changed


def _changed_by_file(changed_files: list, candidate_ids: list) -> dict:
    """Per-file candidate counts for the dry-run/export report. When the
    export touched exactly one file, every requested id's count attributes
    to it unambiguously. There is no per-id target-file mapping in the
    message contract, so the core has no adapter-independent way to split
    ids across more than one changed file; each such file is reported with
    an unknown count (`None`) rather than a fabricated split."""
    if not changed_files:
        return {}
    if len(changed_files) == 1:
        return {changed_files[0]: len(candidate_ids)}
    return {f: None for f in changed_files}


# ---------------------------------------------------------------------------
# Journal + backups + replacement (plan section 11, step 5)
# ---------------------------------------------------------------------------


def _ledger_snapshot_text(locale: str, entries: dict) -> str:
    """The exact bytes `ledger.save()` writes for one locale -- `ledger.py`
    pins this shape in its own module docstring (`{"schema": 1, "locale":
    ..., "entries": ...}`, `indent=2, sort_keys=True, ensure_ascii=False`
    plus a trailing newline). Computed here, once, so the same text is both
    hashed for the journal's `new_sha256` and written to disk -- never two
    separate serializations that could drift apart."""
    return json.dumps({"schema": 1, "locale": locale, "entries": entries},
                       indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _do_real_export(root: Path, cfg: dict, locale: str, live_messages: dict, temp_project: Path,
                     candidate_ids: list, values: dict, changed_by_file: dict, ledger_data: dict,
                     recovered: list, staging_sha256: dict) -> dict:
    stamp = _new_export_stamp()
    export_dir = root / "exports" / stamp
    project_root = Path(cfg["project_root"])
    # ledger.py stores one file per locale (`R/ledger/<locale>.json`), never
    # a single `R/ledger.json` -- so a parallel export of a different locale
    # never races this one's backup/replace. Only this locale's file is
    # backed up, updated and (on failure) restored.
    ledger_path = root / "ledger" / f"{locale}.json"

    # Only a file this export actually changed is backed up and replaced.
    # Every project file's new bytes are already sitting in `temp_project`,
    # so its `new_sha256` is known up front, before anything is replaced.
    # The ledger's new bytes are not knowable yet -- `record_export` hasn't
    # run -- so its entry starts with `new_bytes: None` and is completed
    # later, still strictly before the ledger is actually written.
    files_meta = []
    for relpath in _changed_files(live_messages, temp_project, project_root):
        files_meta.append({"kind": "project", "relpath": relpath, "dest": str(project_root / relpath),
                            "backup": f"project/{relpath}", "sha256_staged": staging_sha256.get(relpath),
                            "new_bytes": (temp_project / relpath).read_bytes()})
    files_meta.append({"kind": "ledger", "relpath": None, "dest": str(ledger_path), "backup": "ledger.json",
                        "sha256_staged": None, "new_bytes": None})

    backup_dir = export_dir / "backup"
    for meta in files_meta:
        data = Path(meta["dest"]).read_bytes()
        meta["backup_sha256"] = lz_common.sha256_bytes(data)
        _atomic_replace_bytes(backup_dir / meta["backup"], data)

    journal = {
        "schema": JOURNAL_SCHEMA, "stamp": stamp, "locale": locale, "status": "in_progress",
        "started_at": lz_common.now_iso(), "finished_at": None,
        "files": [{"kind": m["kind"], "dest": m["dest"], "backup": m["backup"],
                    "backup_sha256": m["backup_sha256"],
                    "new_sha256": lz_common.sha256_bytes(m["new_bytes"]) if m["new_bytes"] is not None else None}
                   for m in files_meta],
    }
    journal_path = export_dir / "journal.json"
    lz_common.atomic_write_json(journal_path, journal)

    def _set_new_sha256(dest: str, new_sha256: str) -> None:
        for f in journal["files"]:
            if f["dest"] == dest:
                f["new_sha256"] = new_sha256
                break
        lz_common.atomic_write_json(journal_path, journal)

    try:
        for meta in files_meta:
            if meta["kind"] != "project":
                continue
            dest = Path(meta["dest"])
            current = dest.read_bytes()
            # The guard is the hash taken when this file was STAGED --
            # before the adapter subprocess ran and before the re-collect
            # and compare steps -- never a hash taken here, right before
            # backup. A freshly-read hash would just become part of what
            # gets backed up, so an edit made during the (potentially slow)
            # adapter run would be silently overwritten instead of caught.
            expected = meta.get("sha256_staged")
            if expected is None or lz_common.sha256_bytes(current) != expected:
                raise RuntimeError(f"{meta['relpath']} changed on disk during the export")
            _atomic_replace_bytes(dest, meta["new_bytes"])

        ledger_mod.record_export(ledger_data, locale, values)
        ledger_text = _ledger_snapshot_text(locale, ledger_data["locales"][locale])
        # Record the ledger's new bytes before writing them -- recovery
        # must never have to guess what an interrupted ledger write was
        # going to produce.
        _set_new_sha256(str(ledger_path), lz_common.sha256_text(ledger_text))
        lz_common.atomic_write_text(ledger_path, ledger_text)

        journal["status"] = "done"
        journal["finished_at"] = lz_common.now_iso()
        lz_common.atomic_write_json(journal_path, journal)
    except BaseException as exc:
        conflicts = _restore_backups(export_dir, journal)
        # Always resolved to `rolled_back`, conflicts and all -- same rule
        # as `recover_unfinished_exports` -- so this journal is never
        # processed again; a conflicted file is simply left as the person
        # left it, and this run's own failure names it.
        journal["status"] = "rolled_back"
        journal["finished_at"] = lz_common.now_iso()
        journal["conflicts"] = conflicts
        lz_common.atomic_write_json(journal_path, journal)
        lz_common.fail(f"export failed and was rolled back: {exc}", lz_common.EXIT_FAIL,
                        locale=locale, recovered=recovered, conflicts=conflicts)

    return {"ok": True, "locale": locale, "dry_run": False, "exported": len(candidate_ids),
            "changed_by_file": changed_by_file, "recovered": recovered, "journal": str(journal_path)}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def do_export(root: Path, locale: str, dry_run: bool) -> dict:
    with _exclusive_export_lock(root):
        recovered, conflicts = recover_unfinished_exports(root)
        if conflicts:
            # Every other file was already reconciled and the leftover
            # journal is resolved (`rolled_back`) -- this call still stops
            # and names the conflicted files, once, so a person sees them;
            # a later call (this locale or another) is not blocked by this
            # journal again -- the next `collect` + `ledger sync` is what
            # picks up the person's edit and locks the message.
            lz_common.fail(
                "export blocked: recovering an earlier interrupted export found files edited since then",
                lz_common.EXIT_FAIL, locale=locale, recovered=recovered, conflicts=conflicts,
            )

        cfg = lz_common.load_config(root)
        if locale not in cfg["target_locales"]:
            lz_common.fail(f"locale is not a configured target: {locale}", lz_common.EXIT_CANNOT, locale=locale)
        lz_common.require_accepted_adapter(root, cfg)
        canon_lock = canon_mod.load_lock(root)

        project_root = Path(cfg["project_root"])
        project_dir = str(project_root)
        try:
            live_messages = adapter_client.collect(str(root), cfg, project_dir, str(root / "messages.json"))
        except adapter_client.AdapterError as exc:
            lz_common.fail(f"collect failed: {exc}", lz_common.EXIT_FAIL, locale=locale, recovered=recovered)
        by_id = {m["id"]: m for m in live_messages["messages"]}

        ledger_data = ledger_mod.load(root)
        candidate_ids = ledger_mod.exportable(ledger_data, locale)
        entries = ledger_data.get("locales", {}).get(locale, {})

        problems = _staleness_problems(cfg, locale, candidate_ids, by_id, entries, canon_lock)
        if problems:
            lz_common.fail("export refused: some candidates are stale", lz_common.EXIT_FAIL,
                            locale=locale, problems=problems, recovered=recovered)

        values = {msg_id: entries[msg_id]["candidate"]["value"] for msg_id in candidate_ids}
        if not values:
            return {"ok": True, "locale": locale, "dry_run": dry_run, "exported": 0,
                    "changed_by_file": {}, "recovered": recovered}

        with tempfile.TemporaryDirectory(dir=str(root)) as tmp:
            temp_project = Path(tmp) / "project"
            # Stage exactly the declared files, never the whole project (plan
            # section 6's staging refusal applies here too): a real project's
            # dependency/data directories dwarf its catalogs.
            lz_common.stage_files(project_dir, live_messages["files"], temp_project)
            # The freshness guard's baseline: each live file's bytes at the
            # moment it was staged, before the adapter subprocess (export)
            # or the re-collect/compare steps below can run.
            staging_sha256 = {
                relpath: lz_common.sha256_file(temp_project / relpath)
                for relpath in live_messages.get("files", [])
                if (temp_project / relpath).is_file()
            }
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

            changed_files = _changed_files(live_messages, temp_project, project_root)
            changed_by_file = _changed_by_file(changed_files, candidate_ids)

            if dry_run:
                return {"ok": True, "locale": locale, "dry_run": True, "exported": len(candidate_ids),
                        "changed_by_file": changed_by_file, "recovered": recovered}

            return _do_real_export(root, cfg, locale, live_messages, temp_project, candidate_ids, values,
                                    changed_by_file, ledger_data, recovered, staging_sha256)


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
