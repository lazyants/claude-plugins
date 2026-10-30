"""Deleted IDs retain their evidence without blocking current locale exports."""

import copy
import json
import subprocess
import sys

import pytest

import ledger
import lz_common
import report
import status
from test_export_values import SCRIPTS_DIR, make_ready_candidate, run_cli, set_candidate, setup_export_workspace
from test_ledger import make_cfg, make_message, make_messages, sync_once


@pytest.mark.parametrize("state", ["pending", "stale", "translated", "escalated", "existing", "human_locked"])
def test_sync_retires_without_losing_evidence_and_resumes_unchanged_ids(work_root, state):
    cfg = make_cfg(target_locales=("de", "ru"))
    message = make_message("old", "Hello")
    sync_once(work_root, cfg, make_messages([message]))
    data = ledger.load(work_root)
    entry = data["locales"]["de"]["old"]
    entry["state"] = state
    entry["candidate"] = make_ready_candidate(
        cfg, message, "de", "Hallo", origin="audit", accepted_by="test-reviewer",
    )
    entry["audit"] = {"verdict": "fail", "run": "prior-audit", "issues": ["historical finding"]}
    entry["notes"] = [{"at": "2026-10-01T00:00:00Z", "note": "prior decision"}]
    entry["rounds"] = 2
    ledger.save(work_root, data)
    before = copy.deepcopy(entry)
    assert ledger.exportable(data, "de") == ["old"]

    for _ in range(2):
        result = sync_once(work_root, cfg, make_messages([]))
        retired = ledger.load(work_root)
        assert result["gone"] == [{"locale": locale, "id": "old"} for locale in ("de", "ru")]
        assert retired["locales"]["de"]["old"] == {**before, "retired": True}
        assert ledger.exportable(retired, "de") == []

    sync_once(work_root, cfg, make_messages([message]))
    restored = ledger.load(work_root)
    assert restored["locales"]["de"]["old"] == before
    assert ledger.exportable(restored, "de") == ["old"]


@pytest.mark.parametrize("drift", ["source", "context", "style", "canon"])
def test_reappearing_id_must_pass_ordinary_candidate_freshness(work_root, drift):
    cfg = make_cfg()
    message = make_message("old", "Hello")
    sync_once(work_root, cfg, make_messages([message]))
    data = ledger.load(work_root)
    data["locales"]["de"]["old"]["candidate"] = make_ready_candidate(cfg, message, "de", "Hallo")
    ledger.save(work_root, data)
    sync_once(work_root, cfg, make_messages([]))

    lock = {"schema": 1, "entries": []}
    if drift == "source":
        message["source"] = "Hello again"
    elif drift == "context":
        message["context"]["comment"] = "New meaning"
    elif drift == "style":
        cfg["style"]["de"]["notes"] = "Use formal language"
    else:
        lock["entries"] = [{
            "id": "hello", "kind": "term", "source": "Hello",
            "translations": {"de": {"value": "Guten Tag", "status": "approved"}},
        }]

    result = sync_once(work_root, cfg, make_messages([message]), canon_lock=lock)
    restored = ledger.load(work_root)
    assert not restored["locales"]["de"]["old"].get("retired")
    assert restored["locales"]["de"]["old"]["candidate"] is None
    assert result["counts"]["de"]["candidates_cleared"] == 1
    assert ledger.exportable(restored, "de") == []


def _collect_sync(root):
    for script, extra in (("collect.py", []), ("ledger.py", ["sync"])):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / script), *extra, "--root", str(root)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
    return json.loads(proc.stdout)


def _remove_message(project, msg_id):
    for path in (project / "locales").glob("*.json"):
        values = json.loads(path.read_text(encoding="utf-8"))
        values.pop(msg_id, None)
        lz_common.atomic_write_json(path, values)


def _reviewed_workspace(work_root):
    root, cfg, project, by_id = setup_export_workspace(work_root)
    set_candidate(root, "de", "nav.about", make_ready_candidate(
        cfg, by_id["nav.about"], "de", "Informationen", origin="audit",
        accepted_by="test-reviewer", audited_target_sha=lz_common.value_sha256("Über uns"),
    ))
    set_candidate(root, "de", "footer.copyright", make_ready_candidate(
        cfg, by_id["footer.copyright"], "de", "Alle Rechte vorbehalten",
    ))
    return root, cfg, project


def test_normal_collect_sync_unblocks_dry_and_real_exports_and_retains_history(work_root):
    root, _, project = _reviewed_workspace(work_root)
    retained = copy.deepcopy(ledger.load(root)["locales"]["de"]["nav.about"])
    _remove_message(project, "nav.about")

    # A deletion without the supported sync step still fails closed.
    code, payload = run_cli(["--root", str(root), "--locale", "de", "--dry-run"])
    assert code != 0
    assert {"id": "nav.about", "reason": "id is no longer present in the live project"} in payload["problems"]
    sync_result = _collect_sync(root)
    assert {"locale": "de", "id": "nav.about"} in sync_result["gone"]
    expected_retired = {**retained, "retired": True}
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    ledger_before = (root / "ledger" / "de.json").read_bytes()

    code, payload = run_cli(["--root", str(root), "--locale", "de", "--dry-run"])
    assert code == 0, payload
    assert payload["exported"] == 1
    assert {p: p.read_bytes() for p in before} == before
    assert (root / "ledger" / "de.json").read_bytes() == ledger_before

    code, payload = run_cli(["--root", str(root), "--locale", "de"])
    assert code == 0, payload
    assert payload["exported"] == 1
    values = json.loads((project / "locales" / "de.json").read_text(encoding="utf-8"))
    assert values["footer.copyright"] == "Alle Rechte vorbehalten"
    assert "nav.about" not in values
    assert ledger.load(root)["locales"]["de"]["nav.about"] == expected_retired

    text = report.build_report(root, "de")
    assert "`nav.about`" not in text.split("## Ready to export")[1].split("## Still needs translation")[0]
    assert "`nav.about`" in text.split("## Retired messages (history retained)")[1]


@pytest.mark.parametrize("drift", ["source", "target"])
def test_retirement_does_not_waive_stale_present_candidate_guards(work_root, drift):
    root, _, project = _reviewed_workspace(work_root)
    _remove_message(project, "nav.about")
    _collect_sync(root)
    path = project / "locales" / ("en.json" if drift == "source" else "de.json")
    values = json.loads(path.read_text(encoding="utf-8"))
    values["footer.copyright"] = "A later human edit"
    lz_common.atomic_write_json(path, values)
    before = path.read_bytes()

    code, payload = run_cli(["--root", str(root), "--locale", "de", "--dry-run"])
    assert code != 0
    assert [p["id"] for p in payload["problems"]] == ["footer.copyright"]
    assert path.read_bytes() == before


def test_returned_exported_id_with_a_human_edit_remains_locked(work_root):
    root, _, project = _reviewed_workspace(work_root)
    code, payload = run_cli(["--root", str(root), "--locale", "de"])
    assert code == 0, payload
    original = {p: p.read_bytes() for p in (project / "locales").glob("*.json")}
    _remove_message(project, "footer.copyright")
    _collect_sync(root)
    for path, content in original.items():
        path.write_bytes(content)
    path = project / "locales" / "de.json"
    values = json.loads(path.read_text(encoding="utf-8"))
    values["footer.copyright"] = "Edited by a person"
    lz_common.atomic_write_json(path, values)
    _collect_sync(root)

    data = ledger.load(root)
    entry = data["locales"]["de"]["footer.copyright"]
    assert not entry.get("retired")
    assert entry["state"] == "human_locked"
    assert entry["candidate"] is None
    assert "footer.copyright" not in ledger.exportable(data, "de")


@pytest.mark.parametrize("candidate", [None, {"checks": "pass", "value_sha256": "v", "verdict": None}])
def test_status_does_not_recommend_work_on_retired_ids(work_root, candidate):
    cfg = make_cfg(target_locales=("de", "ru"))
    data = {"locales": {
        "de": {"old": {"state": "pending", "retired": True, "candidate": candidate}},
        "ru": {"live": {"state": "pending", "candidate": None}},
    }}
    command = status._next_command(
        work_root, cfg, [], True, {"present": True, "current": True}, make_messages([]), data, {},
    )
    assert "--kind translate --locale ru" in command
