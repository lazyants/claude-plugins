"""Audit completion is recoverable from accepted, snapshot-bound ledger records.

These tests feed real packet builds and accepts into reloaded state, the status
subprocess and the Markdown report. They exercise bookkeeping, not translation
quality: verdicts are explicit fixture inputs and the toy parser checks syntax.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

import adapter_client
import ledger
import lz_common
import packets
import report
from test_packets import (
    SCRIPTS_DIR,
    TEMPLATES_DIR,
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
        "audit_sha256": item["audit_sha256"],
        "verdict": verdict,
        "issues": [] if verdict == "pass" else [{"kind": "meaning", "text": "Check the greeting's intended meaning."}],
        "proposed": proposed,
        "new_canon_candidates": [],
    }


def _accept(root, batch, verdicts=None):
    run_dir, packet = _packet(batch)
    if verdicts is None:
        verdicts = {item["id"]: _verdict(item) for item in packet["items"]}
    output_path = run_dir / "answer.json"
    lz_common.atomic_write_json(output_path, {"verdicts": verdicts})
    return packets.do_accept(root, run_dir, output_path)


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


@pytest.mark.parametrize("changed", ["source", "context", "style", "canon"])
def test_old_answer_cannot_cover_rebuilt_packet_with_same_target(work_root, changed):
    canon_lock = {"schema": 1, "entries": [_canon_entry()]}
    cfg, messages = _workspace(work_root, canon_lock=canon_lock)
    first = _build(work_root)["batches"][0]
    first_dir, first_packet = _packet(first)
    first_item = first_packet["items"][0]
    retained_answer = _verdict(first_item)
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
    assert rebuilt_item["audit_sha256"] != first_item["audit_sha256"]
    before = ledger.load(work_root)
    refused = _accept(work_root, rebuilt, {"a": retained_answer})
    assert refused["passed"] == [] and refused["missing"] == ["a"]
    assert ledger.load(work_root) == before
    assert _coverage(work_root)["reviewed"] == 0
    accepted = _accept(work_root, rebuilt, {"a": _verdict(rebuilt_item)})
    assert accepted["passed"] == ["a"] and accepted["missing"] == []
    assert _coverage(work_root)["reviewed"] == 1


@pytest.mark.parametrize("binding", ["missing", "wrong"])
def test_invalid_reply_binding_preserves_previous_verdict_and_proposal(work_root, binding):
    _workspace(work_root)
    first = _build(work_root)["batches"][0]
    _accept(work_root, first, {"a": _verdict(_packet(first)[1]["items"][0], "fail", "Hallo!")})
    fresh = _build(work_root, fresh=True)["batches"][0]
    answer = _verdict(_packet(fresh)[1]["items"][0])
    if binding == "missing":
        answer.pop("audit_sha256")
    else:
        answer["audit_sha256"] = "0" * 64
    before = ledger.load(work_root)
    result = _accept(work_root, fresh, {"a": answer})
    assert result["passed"] == [] and result["missing"] == ["a"]
    assert ledger.load(work_root) == before
    assert _coverage(work_root)["fail"] == 1


@pytest.mark.parametrize("old_scope,new_scope", [("full", "restricted"), ("restricted", "full")])
def test_audit_response_binding_cannot_cross_full_and_restricted_scopes(work_root, old_scope, new_scope):
    _workspace(work_root)
    _restricted_request(work_root)
    first = _build(work_root, **({"entry_ids": ["t-hello"]} if old_scope == "restricted" else {}))["batches"][0]
    first_item = _packet(first)[1]["items"][0]
    current = _build(work_root, **({"entry_ids": ["t-hello"]} if new_scope == "restricted" else {}))["batches"][0]
    current_item = _packet(current)[1]["items"][0]
    assert first_item["value_sha256"] == current_item["value_sha256"]
    assert first_item["audit_sha256"] != current_item["audit_sha256"]
    before = ledger.load(work_root)
    result = _accept(work_root, current, {"a": _verdict(first_item)})
    assert result["missing"] == ["a"] and result["passed"] == []
    assert ledger.load(work_root) == before
    accepted = _accept(work_root, current, {"a": _verdict(current_item)})
    assert accepted["passed"] == ["a"]
    assert _coverage(work_root)["reviewed"] == (1 if new_scope == "full" else 0)


def test_legacy_packet_without_binding_requires_rebuild_and_cannot_gain_coverage(work_root):
    _workspace(work_root)
    batch = _build(work_root)["batches"][0]
    run_dir, packet = _packet(batch)
    answer = _verdict(packet["items"][0])
    packet["items"][0].pop("audit_sha256")
    lz_common.atomic_write_json(run_dir / "packet.json", packet)
    before = ledger.load(work_root)
    result = _accept(work_root, batch, {"a": answer})
    assert result["passed"] == [] and result["missing"] == ["a"]
    assert ledger.load(work_root) == before
    assert _coverage(work_root)["reviewed"] == 0
    rebuilt = _build(work_root)["batches"][0]
    assert _accept(work_root, rebuilt)["passed"] == ["a"]
    assert _coverage(work_root)["reviewed"] == 1


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
    lz_common.atomic_write_json(work_root / "canon.lock.json", {
        "schema": 1, "entries": [_canon_entry("t-save", "Save", "Speichern")],
    })
    assert _coverage(work_root)["reviewed"] == 1
    assert _build(work_root)["selected"] == 0


@pytest.mark.parametrize("verdict,proposed", [("pass", None), ("fail", "Hallo!")])
def test_accept_uses_frozen_packet_style_after_config_changes(work_root, verdict, proposed):
    cfg, _ = _workspace(work_root)
    old_style_hash = ledger.style_sha256(cfg, "de")
    batch = _build(work_root)["batches"][0]
    item = _packet(batch)[1]["items"][0]
    cfg["style"]["de"]["notes"] = "Changed while the audit turn was running"
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    _accept(work_root, batch, {"a": _verdict(item, verdict, proposed)})
    entry = ledger.load(work_root)["locales"]["de"]["a"]
    assert entry["audit"]["snapshot"]["style_sha256"] == old_style_hash
    if proposed is not None:
        assert entry["audit_proposal"]["style_sha256"] == old_style_hash
    assert _coverage(work_root)["stale"] == 1
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
    assert not entry.get("audit_proposal")
    assert entry["candidate"] is None
    assert "Hallo!" not in report.build_report(work_root, "de")
    result = packets.do_accept_audit(work_root, "de", ["a"], "operator")
    assert result["accepted"] == []
    assert result["failed"][0]["reason"] == "no stored audit proposal"
    coverage = _coverage(work_root)
    assert coverage["reviewed"] == 1
    assert coverage["unresolved"] == (1 if next_verdict == "fail" else 0)


def test_restricted_pass_does_not_retire_full_audit_proposal(work_root):
    _workspace(work_root)
    first = _build(work_root)["batches"][0]
    _accept(work_root, first, {"a": _verdict(_packet(first)[1]["items"][0], "fail", "Hallo!")})
    full_proposal = copy.deepcopy(ledger.load(work_root)["locales"]["de"]["a"]["audit_proposal"])
    _restricted_request(work_root)
    _accept(work_root, _build(work_root, entry_ids=["t-hello"])["batches"][0])
    assert ledger.load(work_root)["locales"]["de"]["a"]["audit_proposal"] == full_proposal
    assert _coverage(work_root)["fail"] == 1


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
