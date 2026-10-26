"""Current-value checks use the actual accepted test adapter, never fake parses."""

import copy
from pathlib import Path

import pytest

import ledger
import lz_common
import packets
import report
from handoff_fixture import record_agent_turn
from test_packets import (
    FIXTURES_DIR,
    TEMPLATES_DIR,
    make_cfg,
    make_entry,
    make_message,
    make_messages,
    make_plural_message,
    setup_workspace,
)


def _workspace(root, message=None, canon_lock=None, offline=False):
    cfg = make_cfg()
    adapter = root / "adapter" / "toy_adapter.py"
    adapter.parent.mkdir()
    source = (FIXTURES_DIR / "toy_adapter.py").read_text(encoding="utf-8")
    if offline:
        # Keep the real fixture's tokenizer; fail its process before invocation.
        source = source.replace('if __name__ == "__main__":',
                                'if __name__ == "__main__" and "parse" in sys.argv:\n'
                                '    sys.exit("parser offline")\n\nif __name__ == "__main__":')
    adapter.write_text(source, encoding="utf-8")
    cfg["adapter"].update(argv=[cfg["adapter"]["argv"][0], str(adapter)], code_dir=str(adapter.parent))
    message = message or make_message("a", "Hello {name}", targets={"de": "Hallo"})
    collected = make_messages([message])
    cfg = setup_workspace(root, cfg, collected, {"de": {"a": make_entry("existing")}}, canon_lock)
    return cfg, collected, adapter


def _build(root, **kwargs):
    batch = packets.do_build(root, "audit", "de", TEMPLATES_DIR, **kwargs)["batches"][0]
    run_dir = Path(batch["dir"])
    return run_dir, lz_common.read_json(run_dir / "packet.json", "packet")


def _accept(root, run_dir, packet, verdict="pass", proposed=None, issues=None):
    output = {"verdicts": {"a": {
        "value_sha256": packet["items"][0]["value_sha256"], "verdict": verdict,
        "issues": issues or ([] if verdict == "pass" else [{"kind": "meaning", "text": "Owner clarification needed."}]),
        "proposed": proposed, "new_canon_candidates": [],
    }}}
    output_path = run_dir / "answer.json"
    lz_common.atomic_write_json(output_path, output)
    record_agent_turn(run_dir, output_path)
    return packets.do_accept(root, run_dir, output_path)


def _record(root):
    return ledger.load(root)["locales"]["de"]["a"]["audit"]


def _report(root):
    result = report.do_report(root, "de")
    return Path(result["report"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("verdict", ["pass", "fail"])
def test_placeholder_observation_survives_model_pass_or_null_replacement(work_root, verdict):
    _workspace(work_root)
    run_dir, packet = _build(work_root)
    before = ledger.load(work_root)
    checks = packet["items"][0]["current_checks"]
    assert checks["status"] == "observations"
    assert {p["check"] for p in checks["problems"]} == {"arguments"}
    assert '"current_checks"' in (run_dir / "prompt.md").read_text(encoding="utf-8")
    accepted = _accept(work_root, run_dir, packet, verdict)
    assert accepted["passed" if verdict == "pass" else "failed"] == ["a"]
    record = _record(work_root)
    assert record["verdict"] == verdict
    assert record["proposed"] is None
    assert record["current_checks"] == checks
    assert record["check_inputs"] == packet["check_inputs"]
    entry = ledger.load(work_root)["locales"]["de"]["a"]
    assert {key: entry[key] for key in before["locales"]["de"]["a"]} == before["locales"]["de"]["a"]
    assert ledger.exportable(ledger.load(work_root), "de") == []
    text = _report(work_root)
    assert f"verdict: {verdict} (current full audit)" in text
    assert "current-value checks: observations (current)" in text
    assert "current-value observation: arguments: missing ['{name}']" in text
    assert "owner hold:" in text


def test_clean_current_value_has_separate_mechanical_coverage(work_root):
    _workspace(work_root, make_message("a", "Hello {name}", targets={"de": "Hallo {name}"}))
    run_dir, packet = _build(work_root)
    _accept(work_root, run_dir, packet)
    assert _record(work_root)["current_checks"] == {"status": "pass", "problems": [], "unavailable": []}
    text = _report(work_root)
    mechanical = text.split("## Current-value mechanical coverage")[1].split("## Exported")[0]
    assert "- pass: 1" in mechanical and "- observations: 0" in mechanical
    assert packets.do_build(work_root, "audit", "de", TEMPLATES_DIR)["selected"] == 0


def test_clean_replacement_does_not_erase_current_defect(work_root):
    _workspace(work_root)
    run_dir, packet = _build(work_root)
    _accept(work_root, run_dir, packet, "fail", "Hallo {name}")
    record = _record(work_root)
    assert record["current_checks"]["status"] == "observations"
    assert record["proposal_checks"]["status"] == "pass"
    entry = ledger.load(work_root)["locales"]["de"]["a"]
    assert entry["audit_proposal"]["checks"] == "pass" and entry["candidate"] is None
    text = _report(work_root)
    assert "current-value checks: observations" in text
    assert "replacement checks: pass" in text
    assert "current-value observation: arguments:" in text


def test_model_pass_with_a_proposal_keeps_its_checks_separate(work_root):
    _workspace(work_root)
    run_dir, packet = _build(work_root)
    _accept(work_root, run_dir, packet, proposed="Hallo {name}")
    record = _record(work_root)
    assert record["verdict"] == "pass"
    assert record["current_checks"]["status"] == "observations"
    assert record["proposal_checks"]["status"] == "pass"
    assert not ledger.load(work_root)["locales"]["de"]["a"].get("audit_proposal")


@pytest.mark.parametrize("source,target,expected", [
    ("Hello ", "Hallo", {"whitespace"}),
    ("ID", "ID", {"identical"}),
    ("Hello", " ", {"whitespace", "empty"}),
    ("Hello", "Hallo!", {"max_length"}),
])
def test_parser_independent_observations_are_holds(work_root, source, target, expected):
    message = make_message("a", source, targets={"de": target})
    if "max_length" in expected:
        message["context"]["max_length"] = 3
    _workspace(work_root, message)
    run_dir, packet = _build(work_root)
    _accept(work_root, run_dir, packet)
    assert {p["check"] for p in _record(work_root)["current_checks"]["problems"]} == expected
    assert "owner hold:" in _report(work_root)
    assert lz_common.read_json(work_root / "messages.json", "messages")["messages"][0] == message


def test_legacy_model_only_record_does_not_claim_mechanical_coverage(work_root):
    _workspace(work_root)
    run_dir, packet = _build(work_root)
    _accept(work_root, run_dir, packet)
    saved = ledger.load(work_root)
    record = saved["locales"]["de"]["a"]["audit"]
    del record["current_checks"], record["check_inputs"]
    ledger.save(work_root, saved)
    text = _report(work_root)
    assert "current-value checks: unreviewed (legacy audit)" in text
    mechanical = text.split("## Current-value mechanical coverage")[1].split("## Exported")[0]
    assert "- unreviewed: 1" in mechanical
    assert packets.do_build(work_root, "audit", "de", TEMPLATES_DIR)["selected"] == 1


def test_restricted_audit_keeps_mechanical_observations_out_of_full_coverage(work_root):
    _workspace(work_root)
    lz_common.atomic_write_json(work_root / "runs" / "de" / "canon-audit-t-test.json", {
        "entry": "t-test", "occurrences": ["a"], "source": "Hello",
    })
    run_dir, packet = _build(work_root, entry_ids=["t-test"])
    _accept(work_root, run_dir, packet)
    entry = ledger.load(work_root)["locales"]["de"]["a"]
    assert "audit" not in entry
    assert entry["canon_audits"]["t-test"]["current_checks"]["status"] == "observations"
    text = _report(work_root)
    assert "current restricted canon audit t-test" in text
    assert "current-value observation: arguments:" in text
    assert "- unreviewed: 1" in text.split("## Current-value mechanical coverage")[1].split("## Exported")[0]


def test_legacy_dnt_scope_conflict_stays_an_owner_hold(work_root):
    message = make_message("a", "Family tree", targets={"de": "Stammbaum"})
    lock = {"schema": 1, "entries": [{"id": "d-brand", "kind": "dnt", "source": "Acme", "occurrences": ["a"]}]}
    _workspace(work_root, message, lock)
    run_dir, packet = _build(work_root)
    issue = {"kind": "canon", "text": "Owner hold: the legacy occurrence names a source without this brand."}
    _accept(work_root, run_dir, packet, issues=[issue])
    record = _record(work_root)
    assert record["verdict"] == "pass" and record["issues"] == [issue]
    assert {p["check"] for p in record["current_checks"]["problems"]} == {"dnt"}
    entry = ledger.load(work_root)["locales"]["de"]["a"]
    assert entry["candidate"] is None and not entry.get("audit_proposal")
    assert lz_common.read_json(work_root / "canon.lock.json", "lock") == lock
    assert lz_common.read_json(work_root / "messages.json", "messages")["messages"][0] == message
    text = _report(work_root)
    assert "current-value observation: dnt:" in text
    assert "Owner hold: the legacy occurrence" in text and "owner hold: adjudicate" in text


@pytest.mark.parametrize("source,target,offline", [
    ("Hello {name", "Hallo", False), ("Hello", "Hallo {", False), ("Hello ", "Hallo", True),
])
def test_unavailable_parser_coverage_is_persisted_and_never_clean(work_root, source, target, offline):
    _workspace(work_root, make_message("a", source, targets={"de": target}), offline=offline)
    run_dir, packet = _build(work_root)
    assert packet["items"][0]["current_checks"]["status"] == "unavailable"
    _accept(work_root, run_dir, packet, "fail", "Guten Tag")
    record = _record(work_root)
    assert record["current_checks"]["status"] == "unavailable"
    assert record["current_checks"]["unavailable"]
    if offline:
        assert {p["check"] for p in record["current_checks"]["problems"]} == {"whitespace"}
    if offline or "{" in source:
        assert record["proposal_checks"]["status"] == "unavailable"
        assert ledger.load(work_root)["locales"]["de"]["a"]["audit_proposal"]["checks"] == "unavailable"
    assert "current-value coverage unavailable:" in _report(work_root)
    assert packets.do_build(work_root, "audit", "de", TEMPLATES_DIR)["selected"] == 1


@pytest.mark.parametrize("changed", ["source", "target", "context", "style", "canon", "adapter", "options", "allow_identical"])
def test_changed_inputs_visibly_stale_saved_observations(work_root, changed):
    cfg, collected, adapter = _workspace(work_root)
    run_dir, packet = _build(work_root)
    _accept(work_root, run_dir, packet)
    saved = copy.deepcopy(ledger.load(work_root))
    if changed == "source":
        collected["messages"][0]["source"] += "!"
    elif changed == "target":
        collected["messages"][0]["targets"]["de"] += "!"
    elif changed == "context":
        collected["messages"][0]["context"]["comment"] = "A fragment needing caller context."
    elif changed == "style":
        cfg["style"]["de"]["notes"] = "Use a formal greeting."
    elif changed == "canon":
        lz_common.atomic_write_json(work_root / "canon.lock.json", {"schema": 1, "entries": [
            {"id": "d-brand", "kind": "dnt", "source": "Acme", "occurrences": ["a"]},
        ]})
    elif changed == "adapter":
        adapter.write_text(adapter.read_text(encoding="utf-8") + "\n# new parser revision\n", encoding="utf-8")
    elif changed == "options":
        cfg["adapter"]["options"]["new_parser_option"] = True
    else:
        cfg["allow_identical"] = ["a"]
    lz_common.atomic_write_json(work_root / "messages.json", collected)
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    text = _report(work_root)
    assert "current-value checks: observations (stale)" in text
    model_freshness = "current" if changed in ("adapter", "options", "allow_identical") else "stale"
    assert f"verdict: pass ({model_freshness} full audit)" in text
    assert "- stale: 1" in text.split("## Current-value mechanical coverage")[1].split("## Exported")[0]
    assert ledger.load(work_root) == saved
    # Model provenance can remain current when only parser/check inputs moved;
    # the ordinary build must nevertheless offer the missing mechanical work.
    lz_common.atomic_write_json(work_root / "adapter.lock.json", lz_common.adapter_digest(work_root, cfg))
    assert packets.do_build(work_root, "audit", "de", TEMPLATES_DIR)["selected"] == 1


def test_check_input_drift_keeps_model_issues_current_and_replacement_checks_stale(work_root):
    cfg, _, _ = _workspace(work_root)
    run_dir, packet = _build(work_root)
    _accept(work_root, run_dir, packet, "fail", "Hallo {name}")
    cfg["allow_identical"] = ["a"]
    lz_common.atomic_write_json(work_root / "localize.json", cfg)
    text = _report(work_root)
    assert "verdict: fail (current full audit)" in text
    assert "current-value checks: observations (stale)" in text
    assert "replacement checks: pass (stale)" in text
    assert "  - issue: meaning: Owner clarification needed." in text
    assert "saved issue:" not in text
    assert "proposal snapshot: stale" in text


def test_old_packet_cannot_overwrite_checks_after_adapter_reacceptance(work_root):
    cfg, _, adapter = _workspace(work_root)
    run_dir, packet = _build(work_root)
    _accept(work_root, run_dir, packet)
    saved = ledger.load(work_root)
    adapter.write_text(adapter.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    lz_common.atomic_write_json(work_root / "adapter.lock.json", lz_common.adapter_digest(work_root, cfg))
    assert _accept(work_root, run_dir, packet)["missing"] == ["a"]
    assert ledger.load(work_root) == saved


def test_plural_structure_is_checked_on_every_current_form(work_root):
    message = make_plural_message(
        "a", ["One @:unit", "{count} @:unit"],
        {"de": [{"label": "one", "exact": False}, {"label": "other", "exact": False}]},
        general_index=1, targets={"de": {"forms": ["{count} Einheiten", "{count} Einheiten"]}},
    )
    _workspace(work_root, message)
    run_dir, packet = _build(work_root)
    _accept(work_root, run_dir, packet)
    checks = _record(work_root)["current_checks"]
    assert checks["status"] == "observations"
    assert {p["check"] for p in checks["problems"]} == {"structure"}
    assert len(checks["problems"]) == 2
    assert "current-value observation: structure:" in _report(work_root)


def test_invalid_current_form_shape_is_blocked_before_packet_build(work_root, capsys):
    message = make_plural_message(
        "a", ["One", "Many"],
        {"de": [{"label": "one", "exact": False}, {"label": "other", "exact": False}]},
        general_index=1, targets={"de": {"forms": ["Ein"]}},
    )
    _workspace(work_root, message)
    before = ledger.load(work_root)
    with pytest.raises(SystemExit) as exc:
        _build(work_root)
    assert exc.value.code == lz_common.EXIT_CANNOT
    assert "forms length must equal" in capsys.readouterr().out
    assert ledger.load(work_root) == before
