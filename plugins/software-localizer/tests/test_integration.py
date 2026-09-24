"""Integration test ("the seam"): drives every real software-localizer
script as a subprocess against the fixture toy adapter and a writable copy
of the fixture toy project, end to end --

    scaffold -> config -> adapter acceptance -> collect -> ledger sync ->
    canon (import/approve/freeze) -> translate -> review -> export ->
    audit -> report

-- plus three failure cases. `messages.json` is never hand-written anywhere
in this file: it only ever comes from a real
`collect.py` run. `localize.json` is filled in directly (exactly what an
operator does by hand in `SKILL.md` step 1) and every model turn's answer is
a small canned JSON file this test writes (exactly what the driving session
saves after a real turn, per `SKILL.md`) -- neither is a `messages.json`.
"""

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

import lz_common  # noqa: E402  (read-only: value hashing/atomic writes for the recovery fixture)

# Derived empirically from tests/fixtures/toy_project's actual form counts
# (cart.itemCount: en/de have 2 forms, ru has 3; mail.unreadCount: en/ru have
# 3 forms, de has 2) -- see the plugin's plural contract.
TOY_OPTIONS = {
    "plural_labels": {
        "en": {"2": [["one", True], ["other", False]], "3": [["zero", True], ["one", True], ["other", False]]},
        "de": {"2": [["one", True], ["other", False]], "3": [["one", True], ["other", False]]},
        "ru": {"2": [["one", False], ["few", False], ["many", False]],
               "3": [["one", False], ["few", False], ["many", False]]},
    },
    "general": {"2": 1, "3": 2},
}


# --- subprocess helpers ------------------------------------------------


def run_cli(script: str, args: list):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        capture_output=True, text=True,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"{script} {args}: expected one JSON line; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return proc.returncode, json.loads(lines[0])


def expect_ok(script: str, args: list) -> dict:
    code, payload = run_cli(script, args)
    assert code == 0, f"{script} {args} failed: {payload}"
    return payload


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- seam steps ----------------------------------------------------------


def drive_to_ledger_sync(work_root: Path, target_locales=("de", "ru"), after_copy=None):
    """Every step common to the happy path and every failure case: a fresh
    workspace, a writable copy of the fixture project, an accepted adapter,
    one collect, one ledger sync. Returns `(root, project_dir)`. `after_copy`,
    when given, is called with the writable `project_dir` right after it is
    copied and before scaffold/collect run -- a test mutates its OWN copy
    there (e.g. to make an otherwise-`existing` message `pending`), never the
    shared fixture under `tests/fixtures/toy_project`."""
    project_dir = work_root / "project"
    shutil.copytree(FIXTURES_DIR / "toy_project", project_dir)
    if after_copy is not None:
        after_copy(project_dir)
    root = work_root / "R"

    expect_ok("scaffold.py", ["--root", str(root)])

    cfg = read_json(root / "localize.json")
    cfg.update({
        "project_root": str(project_dir), "source_locale": "en", "target_locales": list(target_locales),
        "adapter": {
            "argv": [sys.executable, str(FIXTURES_DIR / "toy_adapter.py")],
            "code_dir": str(FIXTURES_DIR),  # the shared fixture script lives here
            "options": dict(TOY_OPTIONS),
        },
        "style": {loc: {"formality": "Sie" if loc == "de" else "вы", "notes": "concise"} for loc in target_locales},
    })
    write_json(root / "localize.json", cfg)
    expect_ok("config_validate.py", ["--root", str(root)])

    run_result = expect_ok("adapter_check.py", ["run", "--root", str(root)])
    assert run_result["ok"] is True, run_result

    coverage_out = root / "runs" / "_coverage" / "output.json"
    coverage_packet = read_json(root / "runs" / "_coverage" / "packet.json")
    write_json(coverage_out, {"run_id": coverage_packet["run_id"], "missing": []})
    expect_ok("adapter_check.py", ["accept", "--root", str(root), "--coverage", str(coverage_out), "--by", "tester"])

    expect_ok("collect.py", ["--root", str(root)])
    expect_ok("ledger.py", ["sync", "--root", str(root)])

    return root, project_dir


def do_canon(root: Path) -> str:
    """Propose "Save" (referenced by `dialog.confirm` via `@:nav.save`) as a
    ui_label, approve it for both locales, freeze. Returns the entry id."""
    build = expect_ok("packets.py", ["build", "--root", str(root), "--kind", "canon"])
    assert build["batches"], "expected at least one canon batch"

    added_id = None
    for batch in build["batches"]:
        batch_dir = Path(batch["dir"])
        packet = read_json(batch_dir / "packet.json")
        ids = {item["id"] for item in packet["items"]}
        candidates = []
        if "nav.save" in ids:
            candidates.append({
                "kind": "ui_label", "source": "Save", "note": "referenced by dialog.confirm",
                "occurrences": ["nav.save"],
                "translations": {
                    "de": {"proposed": "Speichern", "current": ["Speichern"]},
                    "ru": {"proposed": "Сохранить", "current": ["Сохранить"]},
                },
            })
        output_path = batch_dir / "canon.out.json"
        write_json(output_path, {"candidates": candidates})
        accept = expect_ok("packets.py", ["accept", "--root", str(root), "--run", str(batch_dir),
                                           "--output", str(output_path)])
        if accept["candidates"]:
            import_result = expect_ok("canon.py", ["import", "--root", str(root),
                                                     "--file", str(batch_dir / "candidates.json")])
            if import_result["added"]:
                added_id = import_result["added"][0]

    assert added_id, "expected the Save canon candidate to be imported"
    expect_ok("canon.py", ["approve", "--root", str(root), "--entry", added_id, "--locale", "de", "--by", "tester"])
    expect_ok("canon.py", ["approve", "--root", str(root), "--entry", added_id, "--locale", "ru", "--by", "tester"])
    lock = expect_ok("canon.py", ["freeze", "--root", str(root)])
    assert lock["entries"] == 1
    return added_id


def do_translate(root: Path) -> None:
    """de's only pending message is footer.copyright ("All rights
    reserved") -- ru already has every message translated."""
    build = expect_ok("packets.py", ["build", "--root", str(root), "--kind", "translate", "--locale", "de"])
    assert len(build["batches"]) == 1, build
    batch_dir = Path(build["batches"][0]["dir"])
    packet = read_json(batch_dir / "packet.json")
    assert [i["id"] for i in packet["items"]] == ["footer.copyright"]

    output_path = batch_dir / "translate.out.json"
    write_json(output_path, {"translations": {"footer.copyright": "Alle Rechte vorbehalten"}})
    accept = expect_ok("packets.py", ["accept", "--root", str(root), "--run", str(batch_dir),
                                       "--output", str(output_path)])
    assert accept["accepted"] == ["footer.copyright"], accept


def do_review(root: Path) -> None:
    build = expect_ok("packets.py", ["build", "--root", str(root), "--kind", "review", "--locale", "de"])
    assert len(build["batches"]) == 1, build
    batch_dir = Path(build["batches"][0]["dir"])
    packet = read_json(batch_dir / "packet.json")
    value_sha = packet["items"][0]["value_sha256"]

    output_path = batch_dir / "review.out.json"
    write_json(output_path, {"verdicts": {"footer.copyright": {
        "value_sha256": value_sha, "verdict": "pass", "issues": [], "proposed": None, "new_canon_candidates": [],
    }}})
    accept = expect_ok("packets.py", ["accept", "--root", str(root), "--run", str(batch_dir),
                                       "--output", str(output_path)])
    assert accept["passed"] == ["footer.copyright"], accept


def do_export(root: Path, project_dir: Path) -> None:
    dry = expect_ok("export_values.py", ["--root", str(root), "--locale", "de", "--dry-run"])
    assert dry["exported"] == 1, dry
    before = (project_dir / "locales" / "de.json").read_bytes()

    result = expect_ok("export_values.py", ["--root", str(root), "--locale", "de"])
    assert result["exported"] == 1, result

    after = (project_dir / "locales" / "de.json").read_bytes()
    assert before != after

    expect_ok("collect.py", ["--root", str(root)])
    messages = read_json(root / "messages.json")
    by_id = {m["id"]: m for m in messages["messages"]}
    assert by_id["footer.copyright"]["targets"]["de"] == "Alle Rechte vorbehalten"


def do_audit_and_report(root: Path) -> None:
    build = expect_ok("packets.py", ["build", "--root", str(root), "--kind", "audit", "--locale", "de"])
    assert build["batches"], "expected at least one audit batch"

    all_proposals = []
    fail_done = False
    for batch in build["batches"]:
        batch_dir = Path(batch["dir"])
        packet = read_json(batch_dir / "packet.json")
        verdicts = {}
        for item in packet["items"]:
            if not fail_done and item["id"] == "user.greeting":
                verdicts[item["id"]] = {
                    "value_sha256": item["value_sha256"], "verdict": "fail",
                    "issues": [{"kind": "style", "text": "could be warmer"}],
                    "proposed": "Hallo {name}!", "new_canon_candidates": [],
                }
                fail_done = True
            else:
                verdicts[item["id"]] = {
                    "value_sha256": item["value_sha256"], "verdict": "pass",
                    "issues": [], "proposed": None, "new_canon_candidates": [],
                }
        output_path = batch_dir / "audit.out.json"
        write_json(output_path, {"verdicts": verdicts})
        accept = expect_ok("packets.py", ["accept", "--root", str(root), "--run", str(batch_dir),
                                           "--output", str(output_path)])
        all_proposals.extend(accept["audit_proposals"])

    assert all_proposals == ["user.greeting"], all_proposals

    report_result = expect_ok("report.py", ["--root", str(root), "--locale", "de"])
    report_text = Path(report_result["report"]).read_text(encoding="utf-8")
    assert "## Audit findings" in report_text
    assert "user.greeting" in report_text
    assert "could be warmer" in report_text
    assert "## Exported" in report_text
    assert "footer.copyright" in report_text


# --- happy path ------------------------------------------------------------


def test_seam_happy_path(work_root):
    root, project_dir = drive_to_ledger_sync(work_root)
    do_canon(root)
    do_translate(root)
    do_review(root)
    do_export(root, project_dir)
    do_audit_and_report(root)


# --- plural end-to-end: bot review round 1, finding 1 -----------------------


def test_seam_plural_translate_review_export_regression(work_root):
    """Bot review round 1, finding 1 (P1): `packets.item_to_message()` used
    to reconstruct a plural packet item's `plural` spec without
    `source_labels`, while `ledger.context_sha256()` hashes it. So
    `accept_translate()` stored a `context_sha256` on the candidate computed
    with `source_labels=None`, and `export_values.py`'s staleness check --
    which recomputes `context_sha256` from the real, freshly-collected
    message (real `source_labels`) -- always found a mismatch and refused
    the candidate as stale ("the context changed since this candidate was
    reviewed"), even though nothing about the message had actually changed.
    No newly translated plural message could ever complete translate ->
    review -> export. Drive `cart.itemCount` (a real plural message in the
    fixture toy project, 2 forms for `de`) through the whole real seam --
    subprocesses, not hand-built ledger state -- and confirm it reaches
    export."""
    def make_cart_item_count_pending_for_de(project_dir: Path):
        de_path = project_dir / "locales" / "de.json"
        de_data = read_json(de_path)
        del de_data["cart.itemCount"]
        write_json(de_path, de_data)

    root, project_dir = drive_to_ledger_sync(work_root, after_copy=make_cart_item_count_pending_for_de)

    entry = read_json(root / "ledger" / "de.json")["entries"]["cart.itemCount"]
    assert entry["state"] == "pending"

    def find_batch_with(build_result: dict, msg_id: str) -> Path:
        for batch in build_result["batches"]:
            batch_dir = Path(batch["dir"])
            packet = read_json(batch_dir / "packet.json")
            if any(i["id"] == msg_id for i in packet["items"]):
                return batch_dir
        raise AssertionError(f"no batch carries {msg_id!r}: {build_result}")

    build = expect_ok("packets.py", ["build", "--root", str(root), "--kind", "translate", "--locale", "de"])
    batch_dir = find_batch_with(build, "cart.itemCount")
    packet = read_json(batch_dir / "packet.json")
    item = next(i for i in packet["items"] if i["id"] == "cart.itemCount")
    # The fix under test: the packet item must carry `source_labels`, or
    # `item_to_message()` at accept time reconstructs a plural spec that
    # hashes differently from the live message's.
    assert item["source_labels"], item

    output_path = batch_dir / "translate.out.json"
    write_json(output_path, {"translations": {"cart.itemCount": {"forms": ["{count} Artikel", "{count} Artikel"]}}})
    accept = expect_ok("packets.py", ["accept", "--root", str(root), "--run", str(batch_dir),
                                       "--output", str(output_path)])
    assert accept["accepted"] == ["cart.itemCount"], accept

    build = expect_ok("packets.py", ["build", "--root", str(root), "--kind", "review", "--locale", "de"])
    batch_dir = find_batch_with(build, "cart.itemCount")
    packet = read_json(batch_dir / "packet.json")
    value_sha = next(i["value_sha256"] for i in packet["items"] if i["id"] == "cart.itemCount")

    output_path = batch_dir / "review.out.json"
    write_json(output_path, {"verdicts": {"cart.itemCount": {
        "value_sha256": value_sha, "verdict": "pass", "issues": [], "proposed": None, "new_canon_candidates": [],
    }}})
    accept = expect_ok("packets.py", ["accept", "--root", str(root), "--run", str(batch_dir),
                                       "--output", str(output_path)])
    assert accept["passed"] == ["cart.itemCount"], accept

    # Pre-fix, this dry run refused "cart.itemCount" with "the context
    # changed since this candidate was reviewed" -- the bug this test guards.
    dry = expect_ok("export_values.py", ["--root", str(root), "--locale", "de", "--dry-run"])
    problem_ids = {p.get("id") for p in dry.get("problems", [])}
    assert "cart.itemCount" not in problem_ids, dry

    result = expect_ok("export_values.py", ["--root", str(root), "--locale", "de"])
    assert result["exported"] >= 1, result

    expect_ok("collect.py", ["--root", str(root)])
    messages = read_json(root / "messages.json")
    by_id = {m["id"]: m for m in messages["messages"]}
    assert by_id["cart.itemCount"]["targets"]["de"] == {"forms": ["{count} Artikel", "{count} Artikel"]}


# --- failure case 1: a person adds a target after sync, never overwritten --


def test_seam_failure_person_translates_pending_message_never_overwritten(work_root):
    root, project_dir = drive_to_ledger_sync(work_root)
    entry = read_json(root / "ledger" / "de.json")["entries"]["footer.copyright"]
    assert entry["state"] == "pending"
    assert entry["candidate"] is None

    de_path = project_dir / "locales" / "de.json"
    de_data = read_json(de_path)
    de_data["footer.copyright"] = "Ein Mensch hat das übersetzt"
    write_json(de_path, de_data)

    expect_ok("collect.py", ["--root", str(root)])
    expect_ok("ledger.py", ["sync", "--root", str(root)])

    entry = read_json(root / "ledger" / "de.json")["entries"]["footer.copyright"]
    assert entry["state"] == "existing"
    assert entry["candidate"] is None

    # The plugin never wrote to the project in this scenario -- collect and
    # sync are both read-only -- so the person's own text is still there.
    assert read_json(de_path)["footer.copyright"] == "Ein Mensch hat das übersetzt"

    # The seam continues into export: there is no candidate for this id (it
    # was never translated), so export must not overwrite the person's edit
    # either -- refused or skipped, and the bytes are exactly unchanged.
    before_export_bytes = de_path.read_bytes()
    export_result = expect_ok("export_values.py", ["--root", str(root), "--locale", "de"])
    assert export_result["exported"] == 0
    assert de_path.read_bytes() == before_export_bytes


# --- failure case 2: the source changes after review -> export refused ----


def test_seam_failure_source_changed_after_review_export_refused(work_root):
    root, project_dir = drive_to_ledger_sync(work_root)
    do_translate(root)
    do_review(root)

    en_path = project_dir / "locales" / "en.json"
    en_data = read_json(en_path)
    en_data["footer.copyright"] = "All rights reserved worldwide"
    write_json(en_path, en_data)

    code, payload = run_cli("export_values.py", ["--root", str(root), "--locale", "de", "--dry-run"])
    assert code == lz_common.EXIT_FAIL, payload
    assert payload["ok"] is False
    problem_ids = {p.get("id") for p in payload.get("problems", [])}
    assert "footer.copyright" in problem_ids

    de_path = project_dir / "locales" / "de.json"
    assert read_json(de_path).get("footer.copyright") is None


# --- failure case 3: an interrupted export is recovered on the next run ---


def test_seam_failure_interrupted_export_recovered_on_next_run(work_root):
    root, project_dir = drive_to_ledger_sync(work_root)
    do_translate(root)
    do_review(root)

    de_path = project_dir / "locales" / "de.json"
    ledger_path = root / "ledger" / "de.json"
    original_de_bytes = de_path.read_bytes()
    original_ledger_bytes = ledger_path.read_bytes()

    # Hand-construct the state a real crash mid-export would leave behind:
    # a journal still "in_progress" whose backup holds the pre-export bytes,
    # while the live project file has already been swapped for something
    # else and the ledger update never landed. This is the only practical
    # way to test recovery deterministically -- it is not messages.json.
    garbage_text = '{"footer.copyright": "mid-export garbage"}\n'
    stamp = "20260101T000000Z"
    export_dir = root / "exports" / stamp
    backup_dir = export_dir / "backup"
    lz_common.atomic_write_text(backup_dir / "project" / "locales" / "de.json", original_de_bytes.decode("utf-8"))
    lz_common.atomic_write_text(backup_dir / "ledger.json", original_ledger_bytes.decode("utf-8"))
    journal = {
        "schema": 1, "stamp": stamp, "locale": "de", "status": "in_progress",
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

    # The project file's bytes on disk (matching `new_sha256` above) are
    # what tell recovery it was already replaced -- a real crash mid-
    # replacement would leave exactly this, with no separate flag anywhere
    # recording it; recovery reconciles by content, not by a flag.
    de_path.write_text(garbage_text, encoding="utf-8")

    result = expect_ok("export_values.py", ["--root", str(root), "--locale", "de", "--dry-run"])
    assert result["recovered"] == [str(export_dir)]
    assert de_path.read_bytes() == original_de_bytes
    assert ledger_path.read_bytes() == original_ledger_bytes
    assert result["exported"] == 1  # the workspace is usable again after recovery

    journal_after = read_json(export_dir / "journal.json")
    assert journal_after["status"] == "rolled_back"
