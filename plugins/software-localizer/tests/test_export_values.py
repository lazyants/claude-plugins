"""Tests for `export_values.py` (plan section 11): recovery-first, the
freshness guards, the temp-copy complete-record comparison, the journal +
backups (including the ledger) with a per-file byte re-check, and rollback.

Every test drives the real fixture toy adapter over a writable copy of the
fixture toy project (`work_root/project`), collected and synced for real --
`messages.json` is never hand-written here. Candidates are still built by
hand directly on the ledger (this file's job is export's own contract, not
`packets.py`'s, which has its own test file)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "skills" / "software-localizer" / "scripts"
FIXTURES_DIR = TESTS_DIR / "fixtures"
sys.path.insert(0, str(SCRIPTS_DIR))

import adapter_client  # noqa: E402
import canon as canon_mod  # noqa: E402
import export_values  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import lz_common  # noqa: E402

# Derived empirically from tests/fixtures/toy_project's actual form counts
# (cart.itemCount: en/de have 2 forms, ru has 3; mail.unreadCount: en/ru have
# 3 forms, de has 2) -- see the plugin's plural contract (plan section 4).
TOY_OPTIONS = {
    "plural_labels": {
        "en": {"2": [["one", True], ["other", False]], "3": [["zero", True], ["one", True], ["other", False]]},
        "de": {"2": [["one", True], ["other", False]], "3": [["one", True], ["other", False]]},
        "ru": {"2": [["one", False], ["few", False], ["many", False]],
               "3": [["one", False], ["few", False], ["many", False]]},
    },
    "general": {"2": 1, "3": 2},
}


# --- helpers -----------------------------------------------------------


def setup_export_workspace(work_root, target_locales=("de", "ru")):
    project_dir = work_root / "project"
    shutil.copytree(FIXTURES_DIR / "toy_project", project_dir)
    root = work_root / "R"

    cfg = {
        "schema": 1, "project_root": str(project_dir), "source_locale": "en",
        "target_locales": list(target_locales),
        "adapter": {
            "argv": [sys.executable, str(FIXTURES_DIR / "toy_adapter.py")],
            "code_dir": str(FIXTURES_DIR),  # the shared fixture script lives here
            "options": dict(TOY_OPTIONS),
        },
        "style": {loc: {"formality": "Sie", "notes": ""} for loc in target_locales},
        "allow_identical": [], "batch_size": 40, "max_rounds": 3, "adapter_timeout_s": 30,
    }
    lz_common.atomic_write_json(root / "localize.json", cfg)
    loaded_cfg = lz_common.load_config(root)
    lock = lz_common.adapter_digest(root, loaded_cfg)
    lz_common.atomic_write_json(root / "adapter.lock.json", lock)

    messages = adapter_client.collect(str(root), loaded_cfg, str(project_dir), str(root / "messages.json"))

    def parse_fn(items):
        return adapter_client.parse(str(root), loaded_cfg, str(project_dir), items)

    canon_lock = canon_mod.load_lock(root)
    ledger_mod.sync(root, loaded_cfg, messages, parse_fn, canon_lock)
    by_id = {m["id"]: m for m in messages["messages"]}
    return root, loaded_cfg, project_dir, by_id


# Every test in this file leaves canon untouched (no canon.json/canon.lock.json
# is ever written by `setup_export_workspace`), so `canon_mod.load_lock(root)`
# always resolves to this same empty lock -- computing the candidate's
# `canon_sha256` snapshot against it directly (instead of threading `root`
# through every call site) still matches what `do_export` itself loads.
EMPTY_CANON_LOCK = {"schema": 1, "entries": []}


def make_ready_candidate(cfg, message, locale, value, origin="translate", accepted_by=None, audited_target_sha=None):
    value_sha = lz_common.value_sha256(value)
    return {
        "value": value, "value_sha256": value_sha, "origin": origin,
        "source_sha256": ledger_mod.source_sha256(message),
        "context_sha256": ledger_mod.context_sha256(message, locale),
        "style_sha256": ledger_mod.style_sha256(cfg, locale),
        "canon_sha256": ledger_mod.canon_sha256(message, EMPTY_CANON_LOCK),
        "audited_target_sha256": audited_target_sha,
        "checks": "pass", "problems": [],
        "verdict": {"value_sha256": value_sha, "verdict": "pass", "issues": [], "run": "test"},
        "accepted_by": accepted_by,
    }


def set_candidate(root, locale, msg_id, candidate, state=None):
    ledger_data = ledger_mod.load(root)
    entry = ledger_data["locales"][locale][msg_id]
    entry["candidate"] = candidate
    if state is not None:
        entry["state"] = state
    ledger_mod.save(root, ledger_data)
    return ledger_data


def run_cli(args):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "export_values.py"), *args],
        capture_output=True, text=True,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one JSON line; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return proc.returncode, json.loads(lines[0])


# --- dry run -----------------------------------------------------------


def test_export_dry_run_reports_changes_without_writing(work_root):
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    set_candidate(root, "de", "footer.copyright",
                   make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten"))

    before = (project_dir / "locales" / "de.json").read_bytes()
    result = export_values.do_export(root, "de", dry_run=True)
    after = (project_dir / "locales" / "de.json").read_bytes()

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["exported"] == 1
    # The exported candidate is "de"; the file that will actually change is
    # `locales/de.json` (found from the staged-vs-live byte diff), never
    # `context.file` (the message's SOURCE file, `locales/en.json`).
    assert result["changed_by_file"] == {"locales/de.json": 1}
    assert before == after

    ledger_data = ledger_mod.load(root)
    assert ledger_data["locales"]["de"]["footer.copyright"]["state"] == "pending"


# --- real export ---------------------------------------------------------


def test_export_writes_value_and_marks_translated(work_root):
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    set_candidate(root, "de", "footer.copyright",
                   make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten"))

    result = export_values.do_export(root, "de", dry_run=False)
    assert result["ok"] is True
    assert result["exported"] == 1

    de_json = json.loads((project_dir / "locales" / "de.json").read_text(encoding="utf-8"))
    assert de_json["footer.copyright"] == "Alle Rechte vorbehalten"

    ledger_data = ledger_mod.load(root)
    entry = ledger_data["locales"]["de"]["footer.copyright"]
    assert entry["state"] == "translated"
    assert entry["last_exported_sha256"] == lz_common.value_sha256("Alle Rechte vorbehalten")

    journal_path = Path(result["journal"])
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["status"] == "done"


def test_export_only_touches_files_it_actually_changed(work_root):
    """en.json (source) and ru.json (a different locale) are both listed by
    the adapter but untouched by a de-only export -- neither should be
    backed up, rewritten, or even read for a byte comparison against a
    changed sha256; the journal's project-file list must name only de.json.
    """
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    set_candidate(root, "de", "footer.copyright",
                   make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten"))

    en_path = project_dir / "locales" / "en.json"
    ru_path = project_dir / "locales" / "ru.json"
    en_before = en_path.read_bytes()
    ru_before = ru_path.read_bytes()

    result = export_values.do_export(root, "de", dry_run=False)

    assert en_path.read_bytes() == en_before
    assert ru_path.read_bytes() == ru_before

    journal = json.loads(Path(result["journal"]).read_text(encoding="utf-8"))
    project_dests = {f["dest"] for f in journal["files"] if f["kind"] == "project"}
    assert project_dests == {str(project_dir / "locales" / "de.json")}


# --- freshness guards ------------------------------------------------------


def test_export_refused_when_live_target_changed_since_sync(work_root):
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    set_candidate(root, "de", "footer.copyright",
                   make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten"))

    # Someone else translates it directly in the project, bypassing the plugin.
    de_path = project_dir / "locales" / "de.json"
    de_data = json.loads(de_path.read_text(encoding="utf-8"))
    de_data["footer.copyright"] = "Ein Mensch hat das übersetzt"
    de_path.write_text(json.dumps(de_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    try:
        export_values.do_export(root, "de", dry_run=True)
        raise AssertionError("expected export to refuse")
    except SystemExit as exc:
        assert exc.code == lz_common.EXIT_FAIL

    # Nothing was touched.
    assert de_path.read_text(encoding="utf-8") == json.dumps(de_data, indent=2, ensure_ascii=False) + "\n"


def test_export_refused_when_source_changed_since_review(work_root):
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    candidate = make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten")
    candidate["source_sha256"] = "stale-hash-from-an-older-review"
    set_candidate(root, "de", "footer.copyright", candidate)

    try:
        export_values.do_export(root, "de", dry_run=True)
        raise AssertionError("expected export to refuse")
    except SystemExit as exc:
        assert exc.code == lz_common.EXIT_FAIL


def test_export_refused_when_canon_changed_after_review(work_root, capsys):
    """A new dnt entry approved for this message after its verdict must
    invalidate the candidate even though source/context/style never moved
    -- the export is refused, naming the id."""
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    set_candidate(root, "de", "footer.copyright",
                   make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten"))

    # Exportable before any canon change.
    result = export_values.do_export(root, "de", dry_run=True)
    assert result["exported"] == 1

    canon = canon_mod.load(root)
    added = canon_mod.import_candidates(canon, [{
        "kind": "dnt", "source": "footer.copyright's own do-not-translate rule",
        "occurrences": ["footer.copyright"],
    }])
    canon_mod.save(root, canon)
    canon_mod.approve(root, canon, added["added"][0], None, None, "tester")
    canon_mod.save(root, canon)
    canon_mod.freeze(root, canon)

    try:
        export_values.do_export(root, "de", dry_run=True)
        raise AssertionError("expected export to refuse after a canon change")
    except SystemExit as exc:
        assert exc.code == lz_common.EXIT_FAIL

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    problem_ids = {p.get("id") for p in payload.get("problems", [])}
    assert "footer.copyright" in problem_ids

    # Nothing was touched.
    de_data = json.loads((project_dir / "locales" / "de.json").read_text(encoding="utf-8"))
    assert de_data.get("footer.copyright") is None


# --- human_locked / accepted audit proposal --------------------------------


def test_export_human_locked_not_exported_unless_accepted(work_root):
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    candidate = make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten")
    # human_locked needs a *live* target for the staleness guard to compare
    # against, so give it one and point project_value_sha256 at it directly.
    de_path = project_dir / "locales" / "de.json"
    de_data = json.loads(de_path.read_text(encoding="utf-8"))
    de_data["footer.copyright"] = "Ein Mensch hat das übersetzt"
    de_path.write_text(json.dumps(de_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    ledger_data = ledger_mod.load(root)
    entry = ledger_data["locales"]["de"]["footer.copyright"]
    entry["state"] = "human_locked"
    entry["project_value_sha256"] = lz_common.value_sha256("Ein Mensch hat das übersetzt")
    entry["candidate"] = candidate
    ledger_mod.save(root, ledger_data)

    result = export_values.do_export(root, "de", dry_run=True)
    assert result["exported"] == 0  # human_locked, no accepted_by -> not exportable

    candidate["accepted_by"] = "alice"
    candidate["audited_target_sha256"] = lz_common.value_sha256("Ein Mensch hat das übersetzt")
    set_candidate(root, "de", "footer.copyright", candidate)

    result = export_values.do_export(root, "de", dry_run=True)
    assert result["exported"] == 1


# --- recovery ---------------------------------------------------------------


def test_export_recovers_leftover_in_progress_journal(work_root):
    """Content-based recovery, not a `replaced` flag (removed): this
    journal carries no such flag anywhere, only `backup_sha256`/
    `new_sha256`, and recovery still tells the already-replaced project
    file apart from the never-written ledger purely from what is on disk
    right now -- exactly the crash window (bytes swapped, flag never
    recorded) the flag-based version could not survive."""
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)

    de_path = project_dir / "locales" / "de.json"
    ledger_path = root / "ledger" / "de.json"  # ledger.py stores one file per locale
    original_de_bytes = de_path.read_bytes()
    original_ledger_bytes = ledger_path.read_bytes()
    garbage_text = '{"footer.copyright": "mid-export garbage"}\n'

    stamp = "20260101T000000Z"
    export_dir = root / "exports" / stamp
    backup_dir = export_dir / "backup"
    lz_common.atomic_write_text(backup_dir / "project" / "locales" / "de.json",
                                 original_de_bytes.decode("utf-8"))
    lz_common.atomic_write_text(backup_dir / "ledger.json", original_ledger_bytes.decode("utf-8"))
    journal = {
        "schema": export_values.JOURNAL_SCHEMA, "stamp": stamp, "locale": "de", "status": "in_progress",
        "started_at": "2026-01-01T00:00:00Z", "finished_at": None,
        "files": [
            {"kind": "project", "dest": str(de_path), "backup": "project/locales/de.json",
             "backup_sha256": lz_common.sha256_bytes(original_de_bytes),
             "new_sha256": lz_common.sha256_text(garbage_text)},
            {"kind": "ledger", "dest": str(ledger_path), "backup": "ledger.json",
             "backup_sha256": lz_common.sha256_bytes(original_ledger_bytes),
             "new_sha256": lz_common.sha256_bytes(original_ledger_bytes)},
        ],
    }
    lz_common.atomic_write_json(export_dir / "journal.json", journal)

    # Simulate a crash mid-replacement: the project file was already swapped
    # for its intended new value (bytes matching `new_sha256` above), but
    # the ledger update never landed (still matches its own `backup_sha256`).
    de_path.write_text(garbage_text, encoding="utf-8")

    result = export_values.do_export(root, "de", dry_run=True)
    assert result["recovered"] == [str(export_dir)]
    assert de_path.read_bytes() == original_de_bytes
    assert ledger_path.read_bytes() == original_ledger_bytes

    rolled_back_journal = json.loads((export_dir / "journal.json").read_text(encoding="utf-8"))
    assert rolled_back_journal["status"] == "rolled_back"


def test_export_recovery_leaves_a_persons_edit_and_reports_a_conflict(work_root, capsys):
    """A person edits the file themselves while an earlier export sat
    interrupted: bytes matching neither `backup_sha256` nor `new_sha256`.
    Recovery must leave it exactly as it is and this run must refuse (exit
    1) naming it, rather than guess and overwrite either the pre-export
    original or the interrupted export's intended value. The journal is
    still resolved (`rolled_back`) either way -- there is no command a
    person could use to clear a stuck journal, so a later export must not
    stay blocked by this one forever; the next `collect` + `ledger sync` is
    what is meant to pick up the person's edit."""
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)

    de_path = project_dir / "locales" / "de.json"
    ledger_path = root / "ledger" / "de.json"
    original_de_bytes = de_path.read_bytes()
    original_ledger_bytes = ledger_path.read_bytes()
    garbage_text = '{"footer.copyright": "mid-export garbage"}\n'

    stamp = "20260101T000000Z"
    export_dir = root / "exports" / stamp
    backup_dir = export_dir / "backup"
    lz_common.atomic_write_text(backup_dir / "project" / "locales" / "de.json",
                                 original_de_bytes.decode("utf-8"))
    lz_common.atomic_write_text(backup_dir / "ledger.json", original_ledger_bytes.decode("utf-8"))
    journal = {
        "schema": export_values.JOURNAL_SCHEMA, "stamp": stamp, "locale": "de", "status": "in_progress",
        "started_at": "2026-01-01T00:00:00Z", "finished_at": None,
        "files": [
            {"kind": "project", "dest": str(de_path), "backup": "project/locales/de.json",
             "backup_sha256": lz_common.sha256_bytes(original_de_bytes),
             "new_sha256": lz_common.sha256_text(garbage_text)},
            {"kind": "ledger", "dest": str(ledger_path), "backup": "ledger.json",
             "backup_sha256": lz_common.sha256_bytes(original_ledger_bytes),
             "new_sha256": lz_common.sha256_bytes(original_ledger_bytes)},
        ],
    }
    lz_common.atomic_write_json(export_dir / "journal.json", journal)

    # A person's own edit lands on the interrupted file -- neither the
    # pre-export original nor the interrupted export's intended value.
    persons_edit = '{"footer.copyright": "a person is editing this right now"}\n'
    de_path.write_text(persons_edit, encoding="utf-8")

    try:
        export_values.do_export(root, "de", dry_run=True)
        raise AssertionError("expected export to refuse on an unresolved conflict")
    except SystemExit as exc:
        assert exc.code == lz_common.EXIT_FAIL

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert str(de_path) in payload.get("conflicts", [])

    # Left untouched -- not the backup, not the interrupted export's value.
    assert de_path.read_text(encoding="utf-8") == persons_edit
    # The ledger (no conflict of its own) is still reconciled: still
    # matching its own backup_sha256, so nothing to do.
    assert ledger_path.read_bytes() == original_ledger_bytes

    journal_after = json.loads((export_dir / "journal.json").read_text(encoding="utf-8"))
    assert journal_after["status"] == "rolled_back"  # resolved -- never processed again
    assert journal_after["conflicts"] == [str(de_path)]

    # A later export (even the very next call) is not blocked by this
    # journal anymore: recovery finds nothing left `in_progress`, so it
    # proceeds normally. There is no ready candidate for footer.copyright
    # here, so it exports nothing -- and, either way, the person's edit is
    # never touched by export itself.
    result = export_values.do_export(root, "de", dry_run=True)
    assert result["exported"] == 0
    assert result["recovered"] == []
    assert de_path.read_text(encoding="utf-8") == persons_edit


# --- exclusive lock + unique journal dirs -----------------------------------


def test_export_dir_stamps_never_collide_within_the_same_second(monkeypatch):
    """`now_iso()` alone only has second precision; two exports started in
    the same second must still get distinct journal directories."""
    monkeypatch.setattr(export_values.lz_common, "now_iso", lambda: "2026-01-01T00-00-00Z")
    stamps = {export_values._new_export_stamp() for _ in range(20)}
    assert len(stamps) == 20


def test_export_holds_the_lock_before_recovery_runs(work_root, monkeypatch):
    """Recovery must only ever run while the exclusive lock is held -- so
    the lock has to be acquired first, structurally, not just believed to
    be uncontended in practice."""
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)

    order = []
    real_flock = export_values.fcntl.flock

    def traced_flock(fd, op):
        if op == export_values.fcntl.LOCK_EX:
            order.append("lock")
        return real_flock(fd, op)

    monkeypatch.setattr(export_values.fcntl, "flock", traced_flock)

    real_recover = export_values.recover_unfinished_exports

    def traced_recover(root_arg):
        order.append("recover")
        return real_recover(root_arg)

    monkeypatch.setattr(export_values, "recover_unfinished_exports", traced_recover)

    export_values.do_export(root, "de", dry_run=True)

    assert order == ["lock", "recover"]


# --- edit landing between staging and replace --------------------------------


def test_export_refuses_when_live_file_edited_after_staging(work_root, monkeypatch):
    """A person's edit that lands after staging (e.g. while the adapter
    subprocess or the re-collect/compare steps run) must be caught against
    the hash taken AT staging time -- not a hash taken later, right before
    backup, which would just absorb the edit into the backup itself and
    silently overwrite it."""
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    set_candidate(root, "de", "footer.copyright",
                   make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten"))

    de_path = project_dir / "locales" / "de.json"
    edited_text = '{"footer.copyright": "a person is editing this right now"}\n'

    real_stage_files = export_values.lz_common.stage_files

    def stage_then_edit(project_dir_arg, files, dest):
        real_stage_files(project_dir_arg, files, dest)
        # The edit lands right after staging captured its hash -- exactly
        # the window between staging and the eventual backup/replace.
        de_path.write_text(edited_text, encoding="utf-8")

    monkeypatch.setattr(export_values.lz_common, "stage_files", stage_then_edit)

    try:
        export_values.do_export(root, "de", dry_run=False)
        raise AssertionError("expected export to refuse")
    except SystemExit as exc:
        assert exc.code == lz_common.EXIT_FAIL

    # The person's edit survives -- not the pre-export original, and not
    # the candidate's translated value.
    assert de_path.read_text(encoding="utf-8") == edited_text


def test_export_refuses_when_an_untouched_declared_file_is_removed_after_staging(work_root, monkeypatch):
    """`_changed_files` (used to pick which files get backed up, replaced,
    and run through the per-file check right before each replace) skips a
    declared file outright once it stops being a file at all (`if not
    dest.is_file(): continue`) -- so a declared file this export leaves
    alone -- en.json (the source locale), untouched by a de-only export of
    footer.copyright, per `test_export_only_touches_files_it_actually_changed`
    -- that gets REMOVED between staging and commit never reaches that check
    under the old code: `_changed_files` silently treats "no longer a file"
    as "nothing to replace here", and the export would otherwise go on to
    replace de.json anyway. The new check, run immediately before the
    commit phase against every declared file (not only the ones about to be
    replaced), catches this and refuses the whole export with nothing
    replaced."""
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    set_candidate(root, "de", "footer.copyright",
                   make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten"))

    de_path = project_dir / "locales" / "de.json"
    en_path = project_dir / "locales" / "en.json"
    de_before = de_path.read_bytes()

    real_stage_files = export_values.lz_common.stage_files

    def stage_then_remove(project_dir_arg, files, dest):
        real_stage_files(project_dir_arg, files, dest)
        # en.json is declared by the adapter but never replaced by this
        # de-only export; removing it right after staging captured its hash
        # is the window this check has to catch.
        en_path.unlink()

    monkeypatch.setattr(export_values.lz_common, "stage_files", stage_then_remove)

    try:
        export_values.do_export(root, "de", dry_run=False)
        raise AssertionError("expected export to refuse")
    except SystemExit as exc:
        assert exc.code == lz_common.EXIT_FAIL

    # Nothing replaced: de.json (the file this export would have written)
    # is untouched, and en.json stays removed -- not silently recreated by
    # a backup/rollback path that was never entered for it.
    assert de_path.read_bytes() == de_before
    assert not en_path.exists()


# --- rollback restores only what it replaced ----------------------------------


def test_restore_backups_leaves_a_never_replaced_file_untouched(work_root):
    """`_restore_backups` decides per file from its CURRENT bytes, never a
    `replaced` flag (removed): a file whose bytes still match
    `backup_sha256` was never actually replaced and is left exactly as it
    is (restoring it too would just be a no-op, but the point of this test
    is that the decision needs no flag at all); a file whose bytes match
    `new_sha256` gets its backup restored. Neither is a conflict."""
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    export_dir = root / "exports" / "20260101T000000Z-manual"
    backup_dir = export_dir / "backup"

    replaced_dest = work_root / "replaced.txt"
    replaced_dest.write_bytes(b"post-replace-content")
    lz_common.atomic_write_text(backup_dir / "project" / "replaced.txt", "pre-export-content")

    unreplaced_dest = work_root / "unreplaced.txt"
    unreplaced_dest.write_bytes(b"pre-export-content-2")
    lz_common.atomic_write_text(backup_dir / "project" / "unreplaced.txt", "pre-export-content-2")

    journal = {
        "schema": export_values.JOURNAL_SCHEMA, "stamp": "20260101T000000Z-manual", "locale": "de",
        "status": "in_progress", "started_at": "2026-01-01T00:00:00Z", "finished_at": None,
        "files": [
            {"kind": "project", "dest": str(replaced_dest), "backup": "project/replaced.txt",
             "backup_sha256": lz_common.sha256_text("pre-export-content"),
             "new_sha256": lz_common.sha256_bytes(b"post-replace-content")},
            {"kind": "project", "dest": str(unreplaced_dest), "backup": "project/unreplaced.txt",
             "backup_sha256": lz_common.sha256_text("pre-export-content-2"),
             "new_sha256": lz_common.sha256_text("a-new-value-never-actually-written")},
        ],
    }

    conflicts = export_values._restore_backups(export_dir, journal)

    assert conflicts == []
    assert replaced_dest.read_bytes() == b"pre-export-content"
    assert unreplaced_dest.read_bytes() == b"pre-export-content-2"


def test_export_rolls_back_the_replaced_file_and_ledger_after_a_real_replace(work_root, monkeypatch):
    """Rollback through the REAL export path (`_do_real_export`'s own
    exception handler), not the separate crash-recovery entry point: inject
    a failure right after the export's one real file replacement (the toy
    adapter always produces exactly one destination file per locale, so
    that replacement is also the export's only one) and confirm the project
    file and the ledger both come back byte-identical to before, with the
    journal marked rolled_back."""
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    set_candidate(root, "de", "footer.copyright",
                   make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten"))

    de_path = project_dir / "locales" / "de.json"
    ledger_path = root / "ledger" / "de.json"
    original_de_bytes = de_path.read_bytes()
    original_ledger_bytes = ledger_path.read_bytes()

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure right after the real replace")

    # record_export runs immediately after the (single, real) project-file
    # replacement has already landed on disk, and before the ledger save.
    monkeypatch.setattr(export_values.ledger_mod, "record_export", boom)

    try:
        export_values.do_export(root, "de", dry_run=False)
        raise AssertionError("expected export to roll back")
    except SystemExit as exc:
        assert exc.code == lz_common.EXIT_FAIL

    assert de_path.read_bytes() == original_de_bytes
    assert ledger_path.read_bytes() == original_ledger_bytes

    export_dirs = [p for p in (root / "exports").iterdir() if p.is_dir()]
    assert len(export_dirs) == 1
    journal = json.loads((export_dirs[0] / "journal.json").read_text(encoding="utf-8"))
    assert journal["status"] == "rolled_back"


# --- CLI subprocess smoke test ----------------------------------------------


def test_export_via_cli(work_root):
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    set_candidate(root, "de", "footer.copyright",
                   make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten"))

    code, payload = run_cli(["--root", str(root), "--locale", "de", "--dry-run"])
    assert code == 0, payload
    assert payload["exported"] == 1


def test_export_refuses_when_an_untouched_declared_file_changes_after_the_backups(work_root, monkeypatch):
    """The declared files are checked once before the backups and the journal
    are written, and again right before the first replace; an edit to a file
    this export does not replace (the source catalog) in between is caught by
    the second check, and nothing is replaced."""
    root, cfg, project_dir, by_id = setup_export_workspace(work_root)
    set_candidate(root, "de", "footer.copyright",
                  make_ready_candidate(cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten"))
    de_path = project_dir / "locales" / "de.json"
    en_path = project_dir / "locales" / "en.json"
    de_before = de_path.read_bytes()

    real_write_json = export_values.lz_common.atomic_write_json

    def write_then_edit_source(path, obj):
        real_write_json(path, obj)
        if Path(path).name == "journal.json" and not getattr(write_then_edit_source, "done", False):
            write_then_edit_source.done = True
            en_path.write_text(en_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    monkeypatch.setattr(export_values.lz_common, "atomic_write_json", write_then_edit_source)

    try:
        export_values.do_export(root, "de", dry_run=False)
        raise AssertionError("expected export to refuse")
    except SystemExit as exc:
        assert exc.code == lz_common.EXIT_FAIL
    assert write_then_edit_source.done
    assert de_path.read_bytes() == de_before
