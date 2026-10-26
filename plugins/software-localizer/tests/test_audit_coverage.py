"""Audit completion is recoverable from accepted, snapshot-bound ledger records.

These tests feed real packet builds and accepts into reloaded state, the status
subprocess and the Markdown report. They exercise bookkeeping, not translation
quality: verdicts are explicit fixture inputs and the toy parser checks syntax.
The shipped synthetic native-session helper captures every canned guarded turn;
replay tests retain A's original receipt when a later build replaces the packet.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

import adapter_client
import canon
import ledger
import lz_common
import packet_handoff
import packets
import report
from handoff_fixture import record_agent_turn
from test_packets import (
    SCRIPTS_DIR,
    TEMPLATES_DIR,
    approved_context,
    fake_parse,
    make_cfg,
    make_entry,
    make_message,
    make_messages,
    make_plural_message,
    run_cli,
    setup_workspace,
)


@pytest.fixture(autouse=True)
def _parse_fixture_messages(monkeypatch):
    monkeypatch.setattr(adapter_client, "parse", fake_parse)


def _workspace(root, messages=None, canon_lock=None):
    if messages is None:
        messages = [make_message("a", "Hello", targets={"de": "Hallo"})]
    cfg = make_cfg()
    collected = make_messages(messages)
    entries = {
        message["id"]: make_entry(
            "existing" if message.get("targets", {}).get("de") is not None else "pending",
        )
        for message in messages
    }
    cfg = setup_workspace(root, cfg, collected, {"de": entries}, canon_lock=canon_lock)
    return cfg, collected


def _build(root, **kwargs):
    return packets.do_build(root, "audit", "de", TEMPLATES_DIR, **kwargs)


def _packet(batch):
    run_dir = Path(batch["dir"])
    return run_dir, lz_common.read_json(run_dir / "packet.json", "packet.json")


def _verdict(item, verdict="pass", proposed=None):
    return {
        "value_sha256": item["value_sha256"],
        "verdict": verdict,
        "issues": [] if verdict == "pass" else [{"kind": "meaning", "text": "Check the greeting's intended meaning."}],
        "proposed": proposed,
        "new_canon_candidates": [],
    }


def _record_answer(batch, verdicts=None):
    run_dir, packet = _packet(batch)
    if verdicts is None:
        verdicts = {item["id"]: _verdict(item) for item in packet["items"]}
    output_path = run_dir / "answer.json"
    output = {"verdicts": verdicts}
    if "note_revisions" in packet:
        output["note_revisions"] = copy.deepcopy(packet["note_revisions"])
    lz_common.atomic_write_json(output_path, output)
    record_agent_turn(run_dir, output_path)
    return run_dir, output_path


def _accept(root, batch, verdicts=None):
    run_dir, output_path = _record_answer(batch, verdicts)
    return packets.do_accept(root, run_dir, output_path)


def _assert_handoff_refusal(root, run_dir, output_path, capsys, expected="submitted packet differs"):
    with pytest.raises(SystemExit) as refusal:
        packets.do_accept(root, run_dir, output_path)
    assert refusal.value.code == lz_common.EXIT_CANNOT
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False and expected in payload["error"]


def _coverage(root, cfg=None, messages=None, canon_lock=None):
    if cfg is None:
        cfg = lz_common.load_config(root)
    if messages is None:
        messages = lz_common.read_json(root / "messages.json", "messages.json")
    if canon_lock is None:
        canon_lock = lz_common.read_json(root / "canon.lock.json", "canon.lock.json")
    return ledger.audit_coverage(ledger.load(root), messages, cfg, canon_lock, "de")


def _status(root):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "status.py"), "--root", str(root)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert len(lines) == 1, proc.stdout
    return json.loads(lines[0]), proc.stderr


def _selected(build_result):
    return {item["id"] for batch in build_result["batches"] for item in _packet(batch)[1]["items"]}


def _canon_entry(entry_id="t-hello", source="Hello", value="Hallo"):
    return {
        "id": entry_id, "kind": "term", "source": source,
        "translations": {"de": {"value": value, "status": "approved"}},
        "occurrences": ["a"],
    }


def _restricted_request(root):
    lz_common.atomic_write_json(root / "runs" / "de" / "canon-audit-t-hello.json", {
        "entry": "t-hello", "kind": "term", "source": "Hello", "locale": "de",
        "before": "Hallo", "after": "Guten Tag", "reason": "approved terminology correction",
        "by": "operator", "at": "2026-10-26T00:00:00Z", "occurrences": ["a"],
    })


def test_accepted_outcomes_survive_reload_and_feed_status_and_report(work_root):
    cfg, messages = _workspace(work_root, [
        make_message("a", "Hello", targets={"de": "Hallo"}),
        make_message("b", "World", targets={"de": "Welt"}),
        make_message("c", "Missing translation"),
    ])
    before = copy.deepcopy(ledger.load(work_root)["locales"]["de"])
    batch = _build(work_root)["batches"][0]
    run_dir, packet = _packet(batch)
    items = {item["id"]: item for item in packet["items"]}
    result = _accept(work_root, batch, {
        "a": _verdict(items["a"]), "b": _verdict(items["b"], "fail"),
    })
    assert result["passed"] == ["a"]
    assert result["failed"] == ["b"]
    entries = ledger.load(work_root)["locales"]["de"]
    for msg_id in ("a", "b"):
        record = entries[msg_id]["audit"]
        assert record["scope"] == "full"
        assert record["verdict"] == ("pass" if msg_id == "a" else "fail")
        assert record["snapshot"]["value_sha256"] == items[msg_id]["value_sha256"]
        assert record["accepted_at"]
        assert record["run"] == packet["batch"]
        assert Path(record["run_dir"]) == run_dir
        assert len(record["packet_sha256"]) == 64
        # The new evidence grants no translation or export permission.
        assert {key: entries[msg_id][key] for key in before[msg_id]} == before[msg_id]
    assert entries["b"]["audit"]["proposed"] is None
    assert entries["b"]["audit"]["issues"] == _verdict(items["b"], "fail")["issues"]

    coverage = _coverage(work_root, cfg, messages)
    assert {key: coverage[key] for key in (
        "total", "reviewed", "pass", "fail", "unresolved", "stale", "unreviewed",
    )} == {"total": 3, "reviewed": 2, "pass": 1, "fail": 1, "unresolved": 2, "stale": 0, "unreviewed": 1}
    assert coverage["ids"] == {"pass": ["a"], "fail": ["b"], "stale": [], "unreviewed": ["c"]}
    status, stderr = _status(work_root)
    assert status["audit"]["de"] == coverage
    assert "reviewed=2/3" in stderr
    markdown = report.build_report(work_root, "de")
    assert "## Audit coverage" in markdown
    assert "- reviewed: 2 / 3" in markdown
    assert "Check the greeting's intended meaning." in markdown
    assert "current full audit" in markdown
    assert "`c`" in markdown


def test_partial_and_refused_verdicts_remain_unreviewed_and_resume(work_root):
    _workspace(work_root, [make_message(key, "Hello", targets={"de": "Hallo"}) for key in "abcd"])
    batch = _build(work_root)["batches"][0]
    items = {item["id"]: item for item in _packet(batch)[1]["items"]}
    malformed = _verdict(items["b"])
    malformed["verdict"] = "uncertain"
    mismatched = _verdict(items["c"])
    mismatched["value_sha256"] = "0" * 64
    result = _accept(work_root, batch, {
        "a": _verdict(items["a"]), "b": malformed, "c": mismatched,
        "outside": _verdict(items["a"]),
    })
    assert result["passed"] == ["a"]
    assert set(result["missing"]) == {"b", "c", "d"}
    assert result["extra"] == ["outside"]
    coverage = _coverage(work_root)
    assert coverage["reviewed"] == 1 and coverage["unreviewed"] == 3
    assert coverage["ids"]["unreviewed"] == ["b", "c", "d"]
    assert _selected(_build(work_root)) == {"b", "c", "d"}


def test_untouched_old_batch_replay_preserves_newer_subset_audit_and_proposal(work_root):
    cfg, messages = _workspace(work_root, [
        make_message("a", "Hello", targets={"de": "Hallo"}),
        make_message("b", "World", targets={"de": "Welt"}),
    ])
    cfg["batch_size"] = 2
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    first = _build(work_root)["batches"][0]
    assert first["batch"] == "a"
    original_dir, original_output = _record_answer(first)
    original_packet = _packet(first)[1]
    original_receipt = packet_handoff.receipt_path(original_output).read_bytes()
    assert packets.do_accept(work_root, original_dir, original_output)["passed"] == ["a", "b"]

    messages["messages"][1]["targets"]["de"] = "Neue Welt"
    lz_common.atomic_write_json(work_root / "messages.json", messages)
    ledger.sync(work_root, cfg, messages,
                lambda items: fake_parse(work_root, cfg, Path(cfg["project_root"]), items), {"entries": []})
    resumed = _build(work_root)["batches"][0]
    assert resumed["batch"] == "b" and Path(resumed["dir"]) != original_dir
    item = _packet(resumed)[1]["items"][0]
    assert item["id"] == "b"
    assert _accept(work_root, resumed, {"b": _verdict(item, "fail", "Neue Welt!")})["audit_proposals"] == ["b"]
    newer_b = copy.deepcopy(ledger.load(work_root)["locales"]["de"]["b"])
    coverage_before = _coverage(work_root)

    # The old directory was never rebuilt: its original native receipt still verifies.
    assert packet_handoff.verify(original_dir, original_output)[0] == original_packet
    assert packet_handoff.receipt_path(original_output).read_bytes() == original_receipt
    replayed = packets.do_accept(work_root, original_dir, original_output)
    assert replayed["passed"] == ["a"] and replayed["missing"] == ["b"]
    assert replayed["failed"] == [] and replayed["audit_proposals"] == []
    assert ledger.load(work_root)["locales"]["de"]["b"] == newer_b
    assert _coverage(work_root) == coverage_before
    assert packets.do_accept_audit(work_root, "de", ["b"], "operator")["accepted"] == ["b"]
    candidate = ledger.load(work_root)["locales"]["de"]["b"]["candidate"]
    assert candidate["value"] == "Neue Welt!"


@pytest.mark.parametrize("changed", ["source", "target", "context", "style", "canon", "informational_context", "removed"])
@pytest.mark.parametrize("verdict", ["pass", "fail"])
def test_receipted_stale_audit_item_cannot_mutate_current_evidence(work_root, approved_context, changed, verdict):
    if changed == "informational_context":
        cfg, messages, lock = _approved_context_workspace(work_root, approved_context)
        prior_proposal, stale_proposal = "evidenzbasierte Auswertung", "evidenzbasierte Prüfung"
    else:
        lock = {"schema": 1, "entries": [_canon_entry()]}
        cfg, messages = _workspace(work_root, canon_lock=lock)
        prior_proposal, stale_proposal = "Hallo!", "Guten Tag!"
    msg_id = messages["messages"][0]["id"]
    first = _build(work_root)["batches"][0]
    first_item = _packet(first)[1]["items"][0]
    assert _accept(work_root, first, {msg_id: _verdict(first_item, "fail", prior_proposal)})["audit_proposals"] == [msg_id]
    stale_batch = _build(work_root, fresh=True)["batches"][0]
    run_dir, frozen_packet = _packet(stale_batch)
    stale_reply = _verdict(frozen_packet["items"][0], verdict, stale_proposal if verdict == "fail" else None)
    stale_reply["new_canon_candidates"] = [{
        "kind": "term", "source": "Greeting", "proposed": "Begrüßung", "note": "A stale output must not publish this candidate.",
    }]
    _, output_path = _record_answer(stale_batch, {msg_id: stale_reply})
    if changed == "source":
        messages["messages"][0]["source"] = "Hello again"
    elif changed == "target":
        messages["messages"][0]["targets"]["de"] = "Guten Tag"
    elif changed == "context":
        messages["messages"][0]["context"]["comment"] = "A changed meaning"
    elif changed == "style":
        cfg["style"]["de"]["notes"] = "A changed style requirement"
    elif changed == "canon":
        lock["entries"][0]["translations"]["de"]["value"] = "Guten Tag"
    elif changed == "informational_context":
        approved_context["canon"]["groups"][0]["scope"] += " A later approved scope clarification."
        lock = canon.freeze(work_root, approved_context["canon"])
    else:
        messages["messages"] = []
    lz_common.atomic_write_json(work_root / "messages.json", messages)
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    lz_common.atomic_write_json(work_root / "canon.lock.json", lock)
    before = ledger.load(work_root)
    assert packet_handoff.verify(run_dir, output_path)[0] == frozen_packet
    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["missing"] == [msg_id]
    assert result["passed"] == result["failed"] == result["audit_proposals"] == []
    assert ledger.load(work_root) == before
    assert not (run_dir / "new_canon_candidates.json").exists()


@pytest.mark.parametrize("changed", ["source", "context", "style", "canon"])
def test_old_answer_cannot_cover_rebuilt_packet_with_same_target(work_root, changed, capsys):
    canon_lock = {"schema": 1, "entries": [_canon_entry()]}
    cfg, messages = _workspace(work_root, canon_lock=canon_lock)
    first = _build(work_root)["batches"][0]
    first_dir, first_packet = _packet(first)
    first_item = first_packet["items"][0]
    retained_answer = _verdict(first_item)
    _, original_output = _record_answer(first, {"a": retained_answer})
    assert packet_handoff.verify(first_dir, original_output)[0] == first_packet
    if changed == "source":
        messages["messages"][0]["source"] = "Hello again"
    elif changed == "context":
        messages["messages"][0]["context"]["comment"] = "A changed greeting context"
    elif changed == "style":
        cfg["style"]["de"]["notes"] = "A changed style instruction"
    else:
        canon_lock["entries"][0]["translations"]["de"]["value"] = "Guten Tag"
    lz_common.atomic_write_json(work_root / "messages.json", messages)
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    lz_common.atomic_write_json(work_root / "canon.lock.json", canon_lock)
    rebuilt = _build(work_root)["batches"][0]
    rebuilt_dir, rebuilt_packet = _packet(rebuilt)
    rebuilt_item = rebuilt_packet["items"][0]
    assert rebuilt_dir == first_dir  # The ordinary rebuild reuses its existing run path.
    assert rebuilt_item["value_sha256"] == first_item["value_sha256"]
    assert rebuilt_packet != first_packet
    before = ledger.load(work_root)
    # Reuse A's output AND original native receipt; never capture B's request for A.
    _assert_handoff_refusal(work_root, rebuilt_dir, original_output, capsys)
    assert ledger.load(work_root) == before
    assert _coverage(work_root)["reviewed"] == 0
    accepted = _accept(work_root, rebuilt, {"a": _verdict(rebuilt_item)})
    assert accepted["passed"] == ["a"] and accepted["missing"] == []
    assert _coverage(work_root)["reviewed"] == 1


@pytest.mark.parametrize("old_scope,new_scope", [("full", "restricted"), ("restricted", "full")])
def test_audit_response_binding_cannot_cross_full_and_restricted_scopes(work_root, old_scope, new_scope, capsys):
    _workspace(work_root)
    _restricted_request(work_root)
    first = _build(work_root, **({"entry_ids": ["t-hello"]} if old_scope == "restricted" else {}))["batches"][0]
    first_dir, first_packet = _packet(first)
    first_item = first_packet["items"][0]
    _, original_output = _record_answer(first, {"a": _verdict(first_item)})
    assert packet_handoff.verify(first_dir, original_output)[0] == first_packet
    current = _build(work_root, **({"entry_ids": ["t-hello"]} if new_scope == "restricted" else {}))["batches"][0]
    current_dir, current_packet = _packet(current)
    current_item = current_packet["items"][0]
    assert first_item["value_sha256"] == current_item["value_sha256"]
    assert first_packet["canon_entry"] != current_packet["canon_entry"]
    before = ledger.load(work_root)
    _assert_handoff_refusal(work_root, current_dir, original_output, capsys, expected="handoff receipt required")
    assert ledger.load(work_root) == before
    accepted = _accept(work_root, current, {"a": _verdict(current_item)})
    assert accepted["passed"] == ["a"]
    assert _coverage(work_root)["reviewed"] == (1 if new_scope == "full" else 0)


def test_completed_audit_resumes_empty_and_fresh_rebuilds_all(work_root):
    _workspace(work_root)
    _accept(work_root, _build(work_root)["batches"][0])
    assert _build(work_root)["selected"] == 0
    assert _build(work_root)["batches"] == []
    assert _selected(_build(work_root, fresh=True)) == {"a"}
    code, result = run_cli([
        "build", "--root", str(work_root), "--kind", "audit", "--locale", "de", "--fresh",
    ])
    assert code == 0 and _selected(result) == {"a"}
    assert _coverage(work_root)["reviewed"] == 1  # Building does not revoke accepted evidence.


@pytest.mark.parametrize("kind", ["translate", "review", "canon", "research", "research_verify"])
def test_fresh_flag_is_refused_for_other_kinds(work_root, kind):
    _workspace(work_root)
    code, result = run_cli([
        "build", "--root", str(work_root), "--kind", kind, "--locale", "de", "--fresh",
    ])
    assert code == lz_common.EXIT_CANNOT and result["ok"] is False


@pytest.mark.parametrize("changed", [
    "source", "target", "context", "style", "canon", "target_labels", "source_labels", "general_index", "count_arguments",
])
def test_snapshot_changes_invalidate_coverage_without_ledger_sync(work_root, changed):
    plural_fields = {"target_labels", "source_labels", "general_index", "count_arguments"}
    if changed in plural_fields:
        message = make_plural_message(
            "a", ["Hello {count}", "Hello {count} people"],
            {"de": [{"label": "one", "exact": False}, {"label": "other", "exact": False}]},
            general_index=1, targets={"de": {"forms": ["Hallo {count}", "Hallo {count} Personen"]}},
        )
    else:
        message = make_message("a", "Hello", targets={"de": "Hallo"})
    canon_lock = {"schema": 1, "entries": [_canon_entry()]}
    cfg, messages = _workspace(work_root, [message], canon_lock)
    _accept(work_root, _build(work_root)["batches"][0])
    assert _coverage(work_root)["reviewed"] == 1
    frozen_record = copy.deepcopy(ledger.load(work_root)["locales"]["de"]["a"]["audit"])
    message = messages["messages"][0]
    if changed == "source":
        message["source"] = "Hello again"
    elif changed == "target":
        message["targets"]["de"] = "Guten Tag"
    elif changed == "context":
        message["context"]["comment"] = "Now a formal salutation"
    elif changed == "style":
        cfg["style"]["de"]["notes"] = "A new style instruction"
    elif changed == "canon":
        canon_lock["entries"][0]["translations"]["de"]["value"] = "Guten Tag"
    elif changed == "target_labels":
        message["plural"]["target_labels"]["de"][0]["label"] = "=1"
        message["plural"]["target_labels"]["de"][0]["exact"] = True
    elif changed == "source_labels":
        message["plural"]["source_labels"][0]["label"] = "=1"
        message["plural"]["source_labels"][0]["exact"] = True
    elif changed == "general_index":
        message["plural"]["general_index"] = 0
    else:
        message["plural"]["count_arguments"].append("total")
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    lz_common.atomic_write_json(work_root / "messages.json", messages)
    lz_common.atomic_write_json(work_root / "canon.lock.json", canon_lock)

    coverage = _coverage(work_root)
    assert coverage["reviewed"] == 0 and coverage["stale"] == 1
    assert coverage["ids"]["stale"] == ["a"]
    assert _selected(_build(work_root)) == {"a"}
    assert ledger.load(work_root)["locales"]["de"]["a"]["audit"] == frozen_record
    status, _ = _status(work_root)
    assert status["audit"]["de"] == coverage


def test_unrelated_canon_change_does_not_invalidate_audit(work_root):
    _workspace(work_root)
    _accept(work_root, _build(work_root)["batches"][0])
    unrelated_entry = _canon_entry("t-save", "Save", "Speichern")
    unrelated_entry["occurrences"] = []
    lz_common.atomic_write_json(work_root / "canon.lock.json", {
        "schema": 1, "entries": [unrelated_entry],
    })
    assert _coverage(work_root)["reviewed"] == 1
    assert _build(work_root)["selected"] == 0


def _approved_context_workspace(root, fixture):
    lock = canon.freeze(root, fixture["canon"])
    sample = fixture["messages"][0]
    cfg, messages = _workspace(root, [make_message(sample["id"], sample["source"], targets=sample["targets"])], lock)
    return cfg, messages, lock


@pytest.mark.parametrize("changed", ["group_note", "group_scope", "linked_note", "linked_translation"])
def test_approved_audit_context_change_invalidates_coverage_and_old_rebuilt_answer(work_root, approved_context, changed, capsys):
    cfg, messages, initial_lock = _approved_context_workspace(work_root, approved_context)
    message = messages["messages"][0]
    msg_id = message["id"]
    first = _build(work_root)["batches"][0]
    first_dir, first_packet = _packet(first)
    first_item = first_packet["items"][0]
    old_answer = _verdict(first_item)
    assert first_packet["canon_context"][msg_id]["groups"]
    _accept(work_root, first, {msg_id: old_answer})
    original_output = first_dir / "answer.json"
    assert packet_handoff.verify(first_dir, original_output)[0] == first_packet
    old_record = copy.deepcopy(ledger.load(work_root)["locales"]["de"][msg_id]["audit"])
    if changed.startswith("group_"):
        field = "note" if changed == "group_note" else "scope"
        approved_context["canon"]["groups"][0][field] += " Refined approved context."
    elif changed == "linked_note":
        approved_context["canon"]["entries"][1]["note"] += " Refined approved note."
    else:
        approved_context["canon"]["entries"][1]["translations"]["de"]["value"] = "Evidenzbasierte Familienforschung"
    changed_lock = canon.freeze(work_root, approved_context["canon"])
    assert ledger.canon_sha256(message, initial_lock) == ledger.canon_sha256(message, changed_lock)
    coverage = _coverage(work_root, cfg, messages, changed_lock)
    assert coverage["reviewed"] == 0 and coverage["ids"]["stale"] == [msg_id]
    assert _status(work_root)[0]["audit"]["de"] == coverage

    rebuilt = _build(work_root)["batches"][0]
    rebuilt_dir, rebuilt_packet = _packet(rebuilt)
    rebuilt_item = rebuilt_packet["items"][0]
    assert rebuilt_dir == first_dir
    assert rebuilt_item["value_sha256"] == first_item["value_sha256"]
    assert rebuilt_packet["canon_context"] != first_packet["canon_context"]
    _assert_handoff_refusal(work_root, rebuilt_dir, original_output, capsys)
    assert ledger.load(work_root)["locales"]["de"][msg_id]["audit"] == old_record
    assert _accept(work_root, rebuilt)["passed"] == [msg_id]
    new_snapshot = ledger.load(work_root)["locales"]["de"][msg_id]["audit"]["snapshot"]
    assert new_snapshot["canon_sha256"] == old_record["snapshot"]["canon_sha256"]
    assert new_snapshot["canon_context_sha256"] != old_record["snapshot"]["canon_context_sha256"]
    assert _coverage(work_root)["reviewed"] == 1


@pytest.mark.parametrize("changed", ["unrelated_group", "unrelated_entry", "unapproved_group", "unapproved_entry"])
def test_unrelated_or_unapproved_audit_context_does_not_invalidate(work_root, approved_context, changed):
    _, messages, initial_lock = _approved_context_workspace(work_root, approved_context)
    message = messages["messages"][0]
    first = _build(work_root)["batches"][0]
    first_context = _packet(first)[1]["canon_context"]
    _accept(work_root, first)
    source_canon = approved_context["canon"]
    if changed == "unrelated_group":
        source_canon["groups"][1]["note"] += " A revised save-button note."
    elif changed == "unrelated_entry":
        source_canon["entries"][3]["translations"]["de"]["value"] = "Sichern"
    elif changed == "unapproved_group":
        source_canon["groups"].append({
            **source_canon["groups"][0], "id": "unapproved-context", "approved_by": None,
            "note": "An unapproved interpretation must not enter the audit snapshot.",
        })
    else:
        source_canon["entries"].append({
            "id": "t-unapproved", "kind": "term", "source": "Unapproved guidance", "occurrences": [],
            "note": "A proposed addition", "translations": {"de": {"status": "proposed", "value": "Vorschlag"}},
        })
    changed_lock = canon.freeze(work_root, source_canon)
    assert ledger.canon_sha256(message, initial_lock) == ledger.canon_sha256(message, changed_lock)
    assert ledger.informational_canon_context(message, changed_lock, "de") == first_context[message["id"]]
    assert _coverage(work_root)["reviewed"] == 1
    assert _build(work_root)["selected"] == 0


def test_accept_rejects_changed_approved_context_without_rebuilding_packet(work_root, approved_context):
    _, messages, _ = _approved_context_workspace(work_root, approved_context)
    msg_id = messages["messages"][0]["id"]
    batch = _build(work_root)["batches"][0]
    frozen_packet = _packet(batch)[1]
    frozen_context = frozen_packet["canon_context"][msg_id]
    run_dir, output_path = _record_answer(batch)
    approved_context["canon"]["groups"][0]["scope"] += " A later approved scope clarification."
    current_lock = canon.freeze(work_root, approved_context["canon"])
    assert ledger.informational_canon_context(messages["messages"][0], current_lock, "de") != frozen_context
    before = ledger.load(work_root)
    assert packet_handoff.verify(run_dir, output_path)[0] == frozen_packet
    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["missing"] == [msg_id] and result["passed"] == []
    assert ledger.load(work_root) == before
    assert _packet(batch)[1] == frozen_packet  # No build has replaced the reviewed packet.
    assert _coverage(work_root)["ids"]["unreviewed"] == [msg_id]


def test_audit_context_hash_stays_separate_from_proposal_and_candidate_canon_hash(work_root, approved_context):
    lock = canon.freeze(work_root, approved_context["canon"])
    message = make_message("app:analysis", "evidence-based analysis {count}",
                           targets={"de": "evidenzbasierte Analysen {count}"})
    _workspace(work_root, [message], lock)
    batch = _build(work_root)["batches"][0]
    packet = _packet(batch)[1]
    assert packet["canon_context"][message["id"]]["groups"]
    proposed = "evidenzbasierte Auswertung {count}"
    result = _accept(work_root, batch, {message["id"]: _verdict(packet["items"][0], "fail", proposed)})
    assert result["audit_proposals"] == [message["id"]]
    entry = ledger.load(work_root)["locales"]["de"][message["id"]]
    mechanical_hash = ledger.canon_sha256(message, lock)
    assert entry["audit_proposal"]["checks"] == "pass"
    assert entry["audit_proposal"]["canon_sha256"] == mechanical_hash
    assert entry["audit"]["snapshot"]["canon_context_sha256"] == lz_common.sha256_json(packet["canon_context"][message["id"]])
    assert packets.do_accept_audit(work_root, "de", [message["id"]], "operator")["accepted"] == [message["id"]]
    candidate = ledger.load(work_root)["locales"]["de"][message["id"]]["candidate"]
    assert candidate["value"] == proposed and candidate["canon_sha256"] == mechanical_hash


def test_group_only_corrected_note_keeps_accepted_audit_current_and_candidate_hash_consistent(work_root, approved_context):
    _, messages, initial_lock = _approved_context_workspace(work_root, approved_context)
    message = messages["messages"][0]
    msg_id = message["id"]
    corrected = approved_context["canon"]["entries"][1]
    corrected_id = corrected["id"]
    assert corrected_id not in {entry["id"] for entry in ledger.relevant_canon(message, initial_lock)}
    assert msg_id not in corrected["occurrences"]
    assert corrected_id in {
        entry["id"] for entry in ledger.informational_canon_context(message, initial_lock, "de")["entries"]
    }
    event = canon.change_note(
        work_root, approved_context["canon"], corrected_id, corrected["note"],
        "A corrected meaning for the approved positioning adjective.",
        "The operator clarified the shared meaning.", "operator",
    )
    full_lock = canon.load_lock(work_root)
    expected_hash = ledger.canon_sha256(message, full_lock, "de")
    assert expected_hash != ledger.canon_sha256(message, full_lock)
    batch = _build(work_root)["batches"][0]
    run_dir, packet = _packet(batch)
    assert packet["note_revisions"][corrected_id] == event["note_revision"]
    proposed = "evidenzbasierte Auswertung"
    result = _accept(work_root, batch, {msg_id: _verdict(packet["items"][0], "fail", proposed)})
    assert result["audit_proposals"] == [msg_id]
    answer = lz_common.read_json(run_dir / "answer.json", "audit answer")
    assert answer["note_revisions"] == packet["note_revisions"]
    saved = ledger.load(work_root)["locales"]["de"][msg_id]
    assert saved["audit"]["snapshot"]["canon_sha256"] == expected_hash
    assert saved["audit_proposal"]["canon_sha256"] == expected_hash
    coverage = _coverage(work_root)
    assert coverage["reviewed"] == 1 and coverage["stale"] == 0 and coverage["fail"] == 1
    assert packets.do_accept_audit(work_root, "de", [msg_id], "operator")["accepted"] == [msg_id]
    candidate = ledger.load(work_root)["locales"]["de"][msg_id]["candidate"]
    assert candidate["value"] == proposed and candidate["canon_sha256"] == expected_hash


@pytest.mark.parametrize("verdict,proposed", [("pass", None), ("fail", "Hallo!")])
def test_accept_rejects_changed_style_after_native_capture(work_root, verdict, proposed):
    cfg, _ = _workspace(work_root)
    old_style_hash = ledger.style_sha256(cfg, "de")
    batch = _build(work_root)["batches"][0]
    item = _packet(batch)[1]["items"][0]
    run_dir, output_path = _record_answer(batch, {"a": _verdict(item, verdict, proposed)})
    cfg["style"]["de"]["notes"] = "Changed while the audit turn was running"
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    before = ledger.load(work_root)
    assert ledger.style_sha256(cfg, "de") != old_style_hash
    result = packets.do_accept(work_root, run_dir, output_path)
    assert result["missing"] == ["a"] and result["passed"] == result["failed"] == []
    assert ledger.load(work_root) == before
    assert _coverage(work_root)["unreviewed"] == 1
    assert _coverage(work_root)["reviewed"] == 0


def test_restricted_audit_does_not_grant_or_erase_full_coverage(work_root):
    _workspace(work_root)
    _restricted_request(work_root)
    restricted = _build(work_root, entry_ids=["t-hello"])["batches"][0]
    _accept(work_root, restricted)
    entry = ledger.load(work_root)["locales"]["de"]["a"]
    assert entry["canon_audits"]["t-hello"]["verdict"] == "pass"
    assert not entry.get("audit")
    assert _coverage(work_root)["unreviewed"] == 1
    assert _selected(_build(work_root)) == {"a"}
    _accept(work_root, _build(work_root)["batches"][0])
    full_record = copy.deepcopy(ledger.load(work_root)["locales"]["de"]["a"]["audit"])
    _accept(work_root, _build(work_root, entry_ids=["t-hello"])["batches"][0])
    assert ledger.load(work_root)["locales"]["de"]["a"]["audit"] == full_record
    assert _coverage(work_root)["reviewed"] == 1
    code, payload = run_cli([
        "build", "--root", str(work_root), "--kind", "audit", "--locale", "de",
        "--entry", "t-hello", "--fresh",
    ])
    assert code == lz_common.EXIT_CANNOT and payload["ok"] is False


@pytest.mark.parametrize("next_verdict", ["pass", "fail"])
@pytest.mark.parametrize("legacy_scope", [False, True])
def test_new_same_scope_verdict_retires_unaccepted_old_proposal(work_root, next_verdict, legacy_scope):
    _workspace(work_root)
    first = _build(work_root)["batches"][0]
    _accept(work_root, first, {"a": _verdict(_packet(first)[1]["items"][0], "fail", "Hallo!")})
    entries = ledger.load(work_root)
    proposal = entries["locales"]["de"]["a"]["audit_proposal"]
    assert proposal["value"] == "Hallo!"
    if legacy_scope:
        proposal.pop("audit_scope", None)
        ledger.save(work_root, entries, locales=["de"])
    fresh = _build(work_root, fresh=True)["batches"][0]
    _accept(work_root, fresh, {"a": _verdict(_packet(fresh)[1]["items"][0], next_verdict)})
    entry = ledger.load(work_root)["locales"]["de"]["a"]
    if legacy_scope:
        assert entry["audit_proposal"] == proposal
    elif next_verdict == "fail":
        assert entry["audit_proposal"]["value"] is None
        assert entry["audit_proposal"]["audit_scope"] == "full"
    else:
        assert not entry.get("audit_proposal")
    assert entry["candidate"] is None
    markdown = report.build_report(work_root, "de")
    assert ("Hallo!" in markdown) is legacy_scope
    if legacy_scope:
        assert "proposal scope: unknown (legacy record)" in markdown
    result = packets.do_accept_audit(work_root, "de", ["a"], "operator")
    if legacy_scope:
        assert result["accepted"] == ["a"]
        candidate = ledger.load(work_root)["locales"]["de"]["a"]["candidate"]
        assert candidate["value"] == "Hallo!" and candidate.get("audit_scope") is None
    else:
        assert result["accepted"] == []
        expected_reason = "audit finding has no proposed replacement" if next_verdict == "fail" else "no stored audit proposal"
        assert result["failed"][0]["reason"] == expected_reason
    coverage = _coverage(work_root)
    assert coverage["reviewed"] == 1
    assert coverage["unresolved"] == (1 if next_verdict == "fail" else 0)


def _legacy_finding(root, run, value, explicit_unknown=False):
    cfg, messages = _workspace(root)
    message = messages["messages"][0]
    proposal = {
        "value": value,
        "value_sha256": lz_common.value_sha256(value) if value is not None else None,
        "checks": "pass" if value is not None else None,
        "problems": [],
        "issues": [{"kind": "meaning", "text": "Legacy finding requiring operator follow-up."}],
        "audited_target_sha256": lz_common.value_sha256(message["targets"]["de"]),
        "source_sha256": ledger.source_sha256(message),
        "context_sha256": ledger.context_sha256(message, "de"),
        "style_sha256": ledger.style_sha256(cfg, "de"),
        "canon_sha256": ledger.canon_sha256(message, {"entries": []}),
        "run": run,
    }
    if explicit_unknown:
        proposal["audit_scope"] = None
    data = ledger.load(root)
    data["locales"]["de"]["a"]["audit_proposal"] = copy.deepcopy(proposal)
    ledger.save(root, data, locales=["de"])
    return proposal


def _assert_retained_history(markdown, original):
    section = markdown.split("## Retained audit findings", 1)[1].split("\n## ", 1)[0]
    assert "historical evidence; not an active proposal" in section
    scope = original.get("audit_scope")
    if scope is None:
        assert "saved scope: unknown (legacy record)" in section
    elif scope == "full":
        assert "saved scope: full audit" in section
    else:
        assert f"saved scope: restricted canon audit {scope}" in section
    assert f"run: {original['run']}" in section
    assert original["issues"][0]["text"] in section
    if original["value"] is not None:
        assert original["value"] in section
    for problem in original.get("problems", []):
        assert problem["detail"] in section
    for field, digest in original.items():
        if field.endswith("_sha256"):
            assert f"saved {field}: {digest}" in section
    for activation_hint in ("next:", "accept-audit", "--fresh", "rerun"):
        assert activation_hint not in section


@pytest.mark.parametrize("run,explicit_unknown", [
    ("legacy-full-batch", False), ("canon-audit-t-hello", False), ("legacy-null-scope", True),
])
@pytest.mark.parametrize("old_value", [None, "Guten Tag!"])
@pytest.mark.parametrize("verdict,proposed", [("pass", None), ("fail", None), ("fail", "Hallo!")])
def test_full_audit_preserves_real_legacy_findings_without_scope_or_verdict_record(
    work_root, run, explicit_unknown, old_value, verdict, proposed,
):
    original = _legacy_finding(work_root, run, old_value, explicit_unknown)
    initial = ledger.load(work_root)["locales"]["de"]["a"]
    assert not initial.get("audit") and not initial.get("canon_audits")
    batch = _build(work_root)["batches"][0]
    result = _accept(work_root, batch, {"a": _verdict(_packet(batch)[1]["items"][0], verdict, proposed)})
    saved = ledger.load(work_root)
    entry = saved["locales"]["de"]["a"]
    if old_value is None:
        assert entry["legacy_audit_findings"] == [original]
        if verdict == "fail":
            assert entry["audit_proposal"]["value"] == proposed
            assert entry["audit_proposal"]["audit_scope"] == "full"
        else:
            assert not entry.get("audit_proposal")
    else:
        assert entry["audit_proposal"] == original
        assert not entry.get("legacy_audit_findings")
    assert entry["audit"]["verdict"] == verdict
    assert result["audit_proposals"] == (["a"] if old_value is None and proposed is not None else [])
    assert result["audit_proposal_conflicts"] == (
        [{"id": "a", "scope": "full", "pending_scope": None}] if old_value is not None and proposed is not None else []
    )
    markdown = report.build_report(work_root, "de")
    if old_value is not None:
        assert f"proposal scope: unknown (legacy record); run: {run}" in markdown
    else:
        _assert_retained_history(markdown, original)
    assert "Legacy finding requiring operator follow-up." in markdown
    accepted = packets.do_accept_audit(work_root, "de", ["a"], "operator")
    if old_value is None and proposed is None:
        assert accepted["accepted"] == []
        expected_reason = "audit finding has no proposed replacement" if verdict == "fail" else "no stored audit proposal"
        assert accepted["failed"][0]["reason"] == expected_reason
        assert ledger.load(work_root) == saved
    else:
        assert accepted["accepted"] == ["a"]
        candidate = ledger.load(work_root)["locales"]["de"]["a"]["candidate"]
        assert candidate["value"] == (old_value if old_value is not None else proposed)
        assert candidate.get("audit_scope") == (None if old_value is not None else "full")


def test_restricted_pass_preserves_unknown_legacy_null_finding(work_root):
    original = _legacy_finding(work_root, "legacy-full-batch", None)
    _restricted_request(work_root)
    _accept(work_root, _build(work_root, entry_ids=["t-hello"])["batches"][0])
    saved = ledger.load(work_root)["locales"]["de"]["a"]
    assert saved["legacy_audit_findings"] == [original]
    assert not saved.get("audit_proposal")
    assert saved["canon_audits"]["t-hello"]["verdict"] == "pass"
    _assert_retained_history(report.build_report(work_root, "de"), original)


@pytest.mark.parametrize("legacy_kind", ["null", "failed_checks"])
@pytest.mark.parametrize("verdict,proposed", [("pass", None), ("fail", "Hallo!")])
def test_unapprovable_unknown_finding_is_retained_without_blocking_fresh_work(work_root, legacy_kind, verdict, proposed):
    original = _legacy_finding(work_root, "legacy-unapprovable", None if legacy_kind == "null" else "Broken correction")
    if legacy_kind == "failed_checks":
        original["checks"] = "fail"
        original["problems"] = [{"check": "arguments", "detail": "The previous correction failed its checks."}]
        data = ledger.load(work_root)
        data["locales"]["de"]["a"]["audit_proposal"] = copy.deepcopy(original)
        ledger.save(work_root, data, locales=["de"])
    batch = _build(work_root, fresh=True)["batches"][0]
    result = _accept(work_root, batch, {"a": _verdict(_packet(batch)[1]["items"][0], verdict, proposed)})
    saved = ledger.load(work_root)["locales"]["de"]["a"]
    assert saved["legacy_audit_findings"] == [original]
    assert result["audit_proposal_conflicts"] == []
    assert saved["candidate"] is None
    markdown = report.build_report(work_root, "de")
    _assert_retained_history(markdown, original)
    if verdict == "pass":
        assert not saved.get("audit_proposal")
        assert result["audit_proposals"] == []
        assert _coverage(work_root)["pass"] == 1
        return

    assert result["audit_proposals"] == ["a"]
    assert saved["audit_proposal"]["value"] == proposed and saved["audit_proposal"]["checks"] == "pass"
    assert packets.do_accept_audit(work_root, "de", ["a"], "operator")["accepted"] == ["a"]
    review = packets.do_build(work_root, "review", "de", TEMPLATES_DIR)["batches"][0]
    assert _accept(work_root, review)["passed"] == ["a"]
    final = ledger.load(work_root)
    candidate = final["locales"]["de"]["a"]["candidate"]
    assert candidate["value"] == proposed and candidate["accepted_by"] == "operator"
    assert ledger.exportable(final, "de") == ["a"]
    assert final["locales"]["de"]["a"]["legacy_audit_findings"] == [original]


def test_failed_check_other_scope_proposal_is_retained_without_blocking_checked_correction(work_root):
    _workspace(work_root)
    _restricted_request(work_root)
    restricted = _build(work_root, entry_ids=["t-hello"])["batches"][0]
    _accept(work_root, restricted, {"a": _verdict(_packet(restricted)[1]["items"][0], "fail", "Hallo {unexpected}")})
    original = copy.deepcopy(ledger.load(work_root)["locales"]["de"]["a"]["audit_proposal"])
    assert original["audit_scope"] == "t-hello" and original["checks"] == "fail"
    assert original["problems"]
    fresh = _build(work_root, fresh=True)["batches"][0]
    result = _accept(work_root, fresh, {"a": _verdict(_packet(fresh)[1]["items"][0], "fail", "Hallo!")})
    saved = ledger.load(work_root)["locales"]["de"]["a"]
    assert result["audit_proposals"] == ["a"] and result["audit_proposal_conflicts"] == []
    assert saved["legacy_audit_findings"] == [original]
    assert saved["audit_proposal"]["value"] == "Hallo!" and saved["audit_proposal"]["checks"] == "pass"
    assert saved["audit_proposal"]["audit_scope"] == "full"
    _assert_retained_history(report.build_report(work_root, "de"), original)
    assert packets.do_accept_audit(work_root, "de", ["a"], "operator")["accepted"] == ["a"]
    candidate = ledger.load(work_root)["locales"]["de"]["a"]["candidate"]
    assert candidate["value"] == "Hallo!" and candidate["audit_scope"] == "full"


def test_legacy_unknown_scope_survives_candidate_and_review_counterproposal(work_root):
    _legacy_finding(work_root, "canon-audit-t-hello", "Guten Tag!")
    assert packets.do_accept_audit(work_root, "de", ["a"], "operator")["accepted"] == ["a"]
    candidate = ledger.load(work_root)["locales"]["de"]["a"]["candidate"]
    assert candidate.get("audit_scope") is None
    build = packets.do_build(work_root, "review", "de", TEMPLATES_DIR)
    run_dir, packet = _packet(build["batches"][0])
    output_path = run_dir / "review-answer.json"
    lz_common.atomic_write_json(output_path, {"verdicts": {"a": {
        "value_sha256": packet["items"][0]["value_sha256"], "verdict": "fail",
        "issues": [{"kind": "style", "text": "Review counterproposal with an unknown original scope."}],
        "proposed": "Hallo!", "new_canon_candidates": [],
    }}})
    record_agent_turn(run_dir, output_path)
    assert packets.do_accept(work_root, run_dir, output_path)["audit_proposals"] == ["a"]
    proposal = ledger.load(work_root)["locales"]["de"]["a"]["audit_proposal"]
    assert proposal["value"] == "Hallo!" and proposal.get("audit_scope") is None


def test_restricted_pass_does_not_retire_full_audit_proposal(work_root):
    _workspace(work_root)
    first = _build(work_root)["batches"][0]
    _accept(work_root, first, {"a": _verdict(_packet(first)[1]["items"][0], "fail", "Hallo!")})
    full_proposal = copy.deepcopy(ledger.load(work_root)["locales"]["de"]["a"]["audit_proposal"])
    _restricted_request(work_root)
    _accept(work_root, _build(work_root, entry_ids=["t-hello"])["batches"][0])
    assert ledger.load(work_root)["locales"]["de"]["a"]["audit_proposal"] == full_proposal
    assert _coverage(work_root)["fail"] == 1


@pytest.mark.parametrize("old_scope,new_scope", [("full", "t-hello"), ("t-hello", "full")])
@pytest.mark.parametrize("new_proposed", [None, "Hallo!"])
def test_failure_preserves_actionable_proposal_from_other_scope(work_root, old_scope, new_scope, new_proposed):
    _workspace(work_root)
    _restricted_request(work_root)
    first = _build(work_root, **({"entry_ids": [old_scope]} if old_scope != "full" else {}))["batches"][0]
    _accept(work_root, first, {"a": _verdict(_packet(first)[1]["items"][0], "fail", "Guten Tag!")})
    entry = ledger.load(work_root)["locales"]["de"]["a"]
    original_proposal = copy.deepcopy(entry["audit_proposal"])
    original_record = copy.deepcopy(entry["audit"] if old_scope == "full" else entry["canon_audits"][old_scope])
    assert original_proposal["audit_scope"] == old_scope
    assert original_proposal["checks"] == "pass"

    next_build = _build(work_root, **({"fresh": True} if new_scope == "full" else {"entry_ids": [new_scope]}))
    next_batch = next_build["batches"][0]
    result = _accept(work_root, next_batch, {"a": _verdict(_packet(next_batch)[1]["items"][0], "fail", new_proposed)})
    assert result["failed"] == ["a"] and result["audit_proposals"] == []
    expected_conflicts = [{"id": "a", "scope": new_scope, "pending_scope": old_scope}] if new_proposed is not None else []
    assert result["audit_proposal_conflicts"] == expected_conflicts
    saved = ledger.load(work_root)["locales"]["de"]["a"]
    assert saved["audit_proposal"] == original_proposal
    assert saved["candidate"] is None
    assert (saved["audit"] if old_scope == "full" else saved["canon_audits"][old_scope]) == original_record
    new_record = saved["audit"] if new_scope == "full" else saved["canon_audits"][new_scope]
    assert new_record["scope"] == new_scope
    assert new_record["verdict"] == "fail" and new_record["proposed"] == new_proposed
    assert new_record["issues"] == _verdict(_packet(next_batch)[1]["items"][0], "fail")["issues"]
    if new_proposed is not None:
        markdown = report.build_report(work_root, "de")
        assert "saved replacement (not active): Hallo!" in markdown
        assert "resolve any outstanding proposal" in markdown

    # The other scope's finding cannot silently replace this approval route.
    accepted = packets.do_accept_audit(work_root, "de", ["a"], "operator")
    assert accepted["accepted"] == ["a"]
    candidate = ledger.load(work_root)["locales"]["de"]["a"]["candidate"]
    assert candidate["value"] == "Guten Tag!" and candidate["audit_scope"] == old_scope


def test_stale_failure_is_labelled_and_missing_target_never_claimed_reviewed(work_root):
    cfg, messages = _workspace(work_root)
    batch = _build(work_root)["batches"][0]
    _accept(work_root, batch, {"a": _verdict(_packet(batch)[1]["items"][0], "fail")})
    messages["messages"][0]["targets"].pop("de")
    lz_common.atomic_write_json(work_root / "messages.json", messages)
    coverage = _coverage(work_root, cfg, messages)
    assert coverage["total"] == 1 and coverage["stale"] == 1 and coverage["reviewed"] == 0
    markdown = report.build_report(work_root, "de")
    assert "stale full audit" in markdown
    assert "Check the greeting's intended meaning." in markdown
    assert "- reviewed: 0 / 1" in markdown
    result = _build(work_root)
    assert result["batches"] == []
    assert result["skipped"] == [{"id": "a", "reason": "no current target value for this locale"}]


def test_legacy_ledger_and_unsynced_new_ids_are_unreviewed(work_root):
    cfg, messages = _workspace(work_root)
    messages["messages"].append(make_message("b", "World", targets={"de": "Welt"}))
    messages["messages"].append(make_message("c", "Missing"))
    lz_common.atomic_write_json(work_root / "messages.json", messages)
    coverage = _coverage(work_root, cfg, messages)
    assert coverage["total"] == 3 and coverage["unreviewed"] == 3 and coverage["reviewed"] == 0
    assert coverage["ids"]["unreviewed"] == ["a", "b", "c"]
    status, _ = _status(work_root)
    assert status["audit"]["de"] == coverage


@pytest.mark.parametrize("relative_path,contents", [
    ("localize.json", "{"),
    ("messages.json", "{"),
    ("messages.json", "[]"),
    ("canon.lock.json", "{"),
    ("canon.lock.json", '{"entries": null}'),
    ("ledger/de.json", "{"),
    ("ledger/de.json", '{"locale": "de", "entries": {"a": 3}}'),
])
def test_status_stays_read_only_and_does_not_claim_coverage_from_corrupt_inputs(work_root, relative_path, contents):
    _workspace(work_root)
    _accept(work_root, _build(work_root)["batches"][0])
    (work_root / relative_path).write_text(contents, encoding="utf-8")
    before = {p.relative_to(work_root): p.read_bytes() for p in work_root.rglob("*") if p.is_file()}
    status, _ = _status(work_root)
    coverage = status.get("audit", {}).get("de")
    assert coverage is None or coverage["reviewed"] == 0
    after = {p.relative_to(work_root): p.read_bytes() for p in work_root.rglob("*") if p.is_file()}
    assert after == before
