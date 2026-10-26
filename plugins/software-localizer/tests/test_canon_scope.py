"""Reviewed DNT scope changes through the real CLI and its downstream consumers."""

import copy
import json
from pathlib import Path

import pytest

import canon
import checks
import ledger
import lz_common
import packets
from test_canon import approved, make_entry, run_cli
from test_packets import TEMPLATES_DIR, make_cfg, make_message, make_messages, setup_workspace


@pytest.fixture
def workspace(work_root):
    messages = make_messages([
        make_message("old", "Upload data", targets={"de": "Daten hochladen", "ru": "Upload"}),
        make_message("new", "Download data", targets={"de": "Daten laden", "ru": "Download"}),
        make_message("pending", "Next", targets={"de": "Weiter"}),
        make_message("text", "SampleBrand settings", targets={"de": "SampleBrand Einstellungen", "ru": "SampleBrand"}),
        make_message("other", "Cart", targets={"de": "Warenkorb", "ru": "Cart"}),
    ])
    cfg = setup_workspace(work_root, make_cfg(target_locales=("de", "ru")), messages)
    dnt = make_entry("d-brand", kind="dnt", source="SampleBrand", note="Original evidence",
                     occurrences=["old", "deleted"])
    sibling = make_entry(translations={"de": approved("Warenkorb"), "ru": approved("Cart")},
                         occurrences=["other"])
    data = {"schema": 1, "entries": [dnt, sibling]}
    canon.approve(work_root, data, "d-brand", None, None, "fixture-person")
    canon.save(work_root, data)
    canon.freeze(work_root, data)
    lz_common.atomic_write_json(work_root / "canon.research" / "de.json", {"retained": "raw evidence"})
    return work_root, cfg, messages


def inspect_scope(root):
    code, payload = run_cli(["scope", "--root", str(root), "--entry", "d-brand"])
    assert code == 0, payload
    return payload


def decide(root, *delta, expected=None, by="fixture-person", reason="Reviewed explicit scope"):
    expected = expected if expected is not None else inspect_scope(root)["entry_sha256"]
    return run_cli([
        "change-scope", "--root", str(root), "--entry", "d-brand",
        "--expect-sha256", expected, "--by", by, "--reason", reason, *delta,
    ])


def bytes_under(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def import_pending(root, *ids):
    data = canon.load(root)
    canon.import_candidates(data, [{"kind": "dnt", "source": "SampleBrand", "occurrences": list(ids)}])
    canon.save(root, data)
    return canon.freeze(root, data)


def test_cli_scope_change_history_readback_and_preservation(workspace):
    root, cfg, messages = workspace
    previous_data = canon.load(root)
    previous_history = canon.load_history(root)["events"]
    previous_lock = canon.load_lock(root)
    evidence = (root / "canon.research" / "de.json").read_bytes()
    snapshot = inspect_scope(root)
    assert snapshot["active"] == ["old", "deleted"]
    assert snapshot["pending"] == []

    code, event = decide(root, "--add", "new", "--remove", "old")
    assert code == 0, event
    assert event["before"] == ["old", "deleted"]
    assert event["after"] == ["deleted", "new"]
    assert event["stored_before"] == ["old", "deleted"]
    assert event["stored_after"] == ["deleted", "new"]
    assert event["additions"] == ["new"] and event["removals"] == ["old"]
    assert event["entry_sha256_before"] == snapshot["entry_sha256"]
    assert event["entry_sha256_after"] == inspect_scope(root)["entry_sha256"]
    assert event["by"] == "fixture-person" and event["reason"] and event["at"]
    stored_event = {k: v for k, v in event.items() if k != "ok"}
    assert canon.load_history(root)["events"] == previous_history + [stored_event]
    current = canon.load(root)
    unchanged = {k: v for k, v in current["entries"][0].items()
                 if k not in {"occurrences", "approved_occurrences", "scope_approved_by", "scope_approved_at"}}
    assert unchanged == {k: v for k, v in previous_data["entries"][0].items()
                         if k not in {"occurrences", "approved_occurrences"}}
    assert current["entries"][1] == previous_data["entries"][1]
    assert canon.load_lock(root)["entries"][1] == previous_lock["entries"][1]
    assert (root / "canon.research" / "de.json").read_bytes() == evidence

    # The real strict gate still rejects only active occurrences; it never
    # assumes that lack of a brand in source copy means its scope is wrong.
    parsed = {"ok": True, "tokens": []}
    by_id = {m["id"]: m for m in messages["messages"]}
    lock = canon.load_lock(root)
    assert checks.check_candidate(by_id["old"], "de", "Daten hochladen", parsed, parsed, lock, {}) == []
    problems = checks.check_candidate(by_id["new"], "de", "Daten laden", parsed, parsed, lock, {})
    assert [p["check"] for p in problems] == ["dnt"]


def test_imported_expansion_requires_selected_scope_decision(workspace):
    root, _, messages = workspace
    original_lock = canon.load_lock(root)
    history_bytes = (root / "canon.history.json").read_bytes()
    after_import = import_pending(root, "old", "new", "pending")
    assert after_import == original_lock
    assert (root / "canon.history.json").read_bytes() == history_bytes
    status = inspect_scope(root)
    assert status["active"] == ["old", "deleted"]
    assert status["pending"] == ["new", "pending"]
    # A subset import neither deletes old occurrences nor approves pending ones.
    assert import_pending(root, "old") == original_lock
    assert inspect_scope(root)["pending"] == ["new", "pending"]
    code, _ = decide(root, "--add", "new")
    assert code == 0
    assert inspect_scope(root)["active"] == ["old", "deleted", "new"]
    assert inspect_scope(root)["pending"] == ["pending"]
    assert canon.load_lock(root)["entries"][0]["occurrences"] == ["old", "deleted", "new"]
    assert ledger.canon_sha256(messages["messages"][1], original_lock) != ledger.canon_sha256(
        messages["messages"][1], canon.load_lock(root))


def test_reject_pending_proposal_does_not_change_active_lock(workspace):
    root, _, _ = workspace
    before = import_pending(root, "new", "pending")
    code, event = decide(root, "--remove", "new")
    assert code == 0
    assert event["before"] == event["after"] == ["old", "deleted"]
    assert event["affected_occurrences"] == []
    assert inspect_scope(root)["pending"] == ["pending"]
    assert canon.load_lock(root) == before
    assert not (root / "runs" / "de" / "canon-audit-d-brand.json").exists()


def test_legacy_approval_captures_preimport_scope(workspace):
    root, _, _ = workspace
    data = canon.load(root)
    del data["entries"][0]["approved_occurrences"]
    canon.save(root, data)
    before = canon.freeze(root, data)
    assert import_pending(root, "new") == before
    assert inspect_scope(root)["active"] == ["old", "deleted"]
    assert inspect_scope(root)["pending"] == ["new"]


def test_removing_entire_scope_is_empty_not_a_legacy_fallback(workspace):
    root, _, _ = workspace
    import_pending(root, "pending")
    assert decide(root, "--remove", "old", "--remove", "deleted")[0] == 0
    assert canon.load_lock(root)["entries"][0]["occurrences"] == []
    assert inspect_scope(root)["pending"] == ["pending"]


@pytest.mark.parametrize("delta,extra", [
    ([], {}),
    (["--add", "old"], {}),
    (["--remove", "absent"], {}),
    (["--add", "unknown"], {}),
    (["--add", "new", "--add", "new"], {}),
    (["--remove", "old", "--remove", "old"], {}),
    (["--add", "new", "--remove", "new"], {}),
    (["--add", " "], {}),
    (["--add", "new"], {"by": " "}),
    (["--add", "new"], {"reason": " "}),
    (["--add", "new"], {"expected": "0" * 64}),
])
def test_invalid_or_stale_decisions_do_not_write(workspace, delta, extra):
    root, _, _ = workspace
    before = bytes_under(root)
    code, payload = decide(root, *delta, **extra)
    assert code == 1 and payload["ok"] is False
    assert bytes_under(root) == before


def test_import_makes_old_expected_entry_hash_stale(workspace):
    root, _, _ = workspace
    expected = inspect_scope(root)["entry_sha256"]
    import_pending(root, "new")
    before = bytes_under(root)
    code, payload = decide(root, "--add", "new", expected=expected)
    assert code == 1 and "expect-sha256" in payload["error"]
    assert bytes_under(root) == before


def test_scope_change_invalid_group_preserves_files_and_history(workspace):
    root, _, _ = workspace
    data = canon.load(root)
    data["groups"] = [{
        "id": "brand", "locale": "de", "entry_ids": ["d-brand", "t-cart"],
        "note": "Approved related decisions", "scope": "Brand and cart terminology",
        "approved_by": "fixture-person", "approved_at": "2026-01-01T00:00:00Z",
    }]
    canon.save(root, data)
    canon.freeze(root, data)
    del data["groups"][0]["scope"]
    canon.save(root, data)
    before = bytes_under(root)

    code, payload = decide(root, "--add", "new")

    assert code == 2 and "scope" in payload["error"]
    assert bytes_under(root) == before


def test_audit_context_uses_only_approved_dnt_occurrences(workspace):
    root, _, messages = workspace
    lock = import_pending(root, "new")
    message = next(m for m in messages["messages"] if m["id"] == "new")
    assert packets.filter_canon_lock(lock, [message]) == {"entries": []}
    assert packets.audit_canon_context(lock, [message], "de") == {}

    assert decide(root, "--add", "new")[0] == 0
    lock = canon.load_lock(root)
    assert [e["id"] for e in packets.filter_canon_lock(lock, [message])["entries"]] == ["d-brand"]
    # Approved occurrences already present in canon need no duplicate context.
    assert packets.audit_canon_context(lock, [message], "de") == {}


def test_reapprove_cannot_activate_imported_scope(workspace):
    root, _, _ = workspace
    import_pending(root, "new")
    before = bytes_under(root)
    code, payload = run_cli(["approve", "--root", str(root), "--entry", "d-brand", "--by", "fixture-person"])
    assert code == 1 and "change-scope" in payload["error"]
    assert bytes_under(root) == before


@pytest.mark.parametrize("entry_id", ["t-cart", "absent", "d-unapproved"])
def test_scope_requires_approved_dnt(workspace, entry_id):
    root, _, _ = workspace
    data = canon.load(root)
    data["entries"].append(make_entry("d-unapproved", kind="dnt", source="Pending"))
    canon.save(root, data)
    before = bytes_under(root)
    code, payload = run_cli(["scope", "--root", str(root), "--entry", entry_id])
    assert code == 1 and payload["ok"] is False
    assert bytes_under(root) == before


def test_scope_change_audit_packets_cover_all_locales_and_removed_ids(workspace):
    root, _, _ = workspace
    code, event = decide(root, "--add", "new", "--add", "pending", "--remove", "old")
    assert code == 0
    assert event["affected_occurrences"] == ["old", "deleted", "new", "pending", "text"]
    for locale in ("de", "ru"):
        request = lz_common.read_json(root / "runs" / locale / "canon-audit-d-brand.json", "request")
        assert request["occurrences"] == event["affected_occurrences"]
        assert request["scope_change"] == {"before": event["before"], "after": event["after"]}
        result = packets.do_build(root, "audit", locale, TEMPLATES_DIR, ["d-brand"])
        skipped = {s["id"]: s["reason"] for s in result["skipped"]}
        assert skipped["deleted"] == "id not present in messages.json"
        if locale == "ru":
            assert skipped["pending"] == "no current target value for this locale"
        batch = Path(result["batches"][0]["dir"])
        packet = json.loads((batch / "packet.json").read_text())
        ids = {i["id"] for i in packet["items"]}
        assert {"old", "new", "text"} <= ids and "other" not in ids
        assert packet["canon_entry"]["scope_change"] == request["scope_change"]
        assert "do not insert a brand" in (batch / "prompt.md").read_text()


def test_scope_change_invalidates_real_ledger_candidates(workspace):
    root, cfg, messages = workspace
    previous_lock = canon.load_lock(root)
    ledger.sync(root, cfg, messages, lambda items: {}, previous_lock)
    data = ledger.load(root)
    for locale, entries in data["locales"].items():
        for message in messages["messages"]:
            entry = entries[message["id"]]
            entry["candidate"] = {
                "source_sha256": entry["source_sha256"], "context_sha256": entry["context_sha256"],
                "style_sha256": entry["style_sha256"],
                "canon_sha256": ledger.canon_sha256(message, previous_lock),
                "value": "Reviewed candidate", "verdict": {"verdict": "pass"},
            }
    ledger.save(root, data)
    unrelated = copy.deepcopy({loc: entries["other"] for loc, entries in data["locales"].items()})
    assert decide(root, "--add", "new", "--remove", "old")[0] == 0
    ledger.sync(root, cfg, messages, lambda items: {}, canon.load_lock(root))
    current = ledger.load(root)
    for locale in ("de", "ru"):
        entries = current["locales"][locale]
        for msg_id in ("old", "new", "text"):
            assert entries[msg_id]["candidate"] is None
        assert entries["other"] == unrelated[locale]


@pytest.mark.parametrize("missing", ["messages.json", "localize.json"])
def test_unavailable_impact_inputs_refuse_before_mutation(workspace, missing):
    root, _, _ = workspace
    (root / missing).unlink()
    before = bytes_under(root)
    code, payload = decide(root, "--remove", "old")
    assert code == 2 and payload["ok"] is False
    assert bytes_under(root) == before


# --- ui_label coherence (#1016) ------------------------------------------------

BRAND_LABELS = {  # message id -> (label id, source, approved ru value)
    # m1's source has no brand on purpose: DNT scope is explicit occurrences, so
    # only the ids in scope (not source-text matching) can bring m1 into an audit.
    "m1": ("u-add", "Install extension", "Установить расширение"),
    "m2": ("u-open", "Open BrowserX", "Открыть"),
    "m3": ("u-close", "Close BrowserX", "Закрыть BrowserX"),
}


def coherence_workspace(work_root, active, stored=None, bad=("m1", "m2")):
    """A DNT `BrowserX` over three exact whole-label messages. `bad` labels
    omit the brand. The lock is written raw, as a pre-coherence install had."""
    messages = make_messages([
        make_message(mid, src, targets={"de": src, "ru": src}) for mid, (_, src, _) in BRAND_LABELS.items()
    ])
    setup_workspace(work_root, make_cfg(target_locales=("de", "ru")), messages)
    labels = []
    for mid, (lid, src, value) in BRAND_LABELS.items():
        if mid not in bad and "BrowserX" not in value:
            value += " BrowserX"
        labels.append(make_entry(lid, kind="ui_label", source=src, translations={"ru": approved(value)}))
    dnt = make_entry("d-brand", kind="dnt", source="BrowserX", occurrences=list(stored or active))
    data = {"schema": 1, "entries": [*labels, dnt]}
    canon.approve(work_root, data, "d-brand", None, None, "fixture-person")
    if stored is not None:
        dnt["approved_occurrences"] = list(active)
    canon.save(work_root, data)
    lz_common.atomic_write_json(work_root / "canon.lock.json", canon._build_lock(data))
    return data


def scope_events(root):
    return [e for e in canon.load_history(root)["events"] if e["action"] == "change-scope"]


def test_freeze_ignores_a_pending_only_dnt_occurrence_but_enforces_legacy_scope(work_root):
    data = coherence_workspace(work_root, active=[], stored=["m1"])

    lock = canon.freeze(work_root, data)
    assert [e["occurrences"] for e in lock["entries"] if e["kind"] == "dnt"] == [[]]

    del data["entries"][-1]["approved_occurrences"]  # legacy approval: stored scope is active
    code, result = run_cli(["freeze", "--root", str(work_root)])
    assert code == 0  # canon.json still holds the explicit empty snapshot
    canon.save(work_root, data)
    code, result = run_cli(["freeze", "--root", str(work_root)])
    assert code == 1 and result["conflicts"][0]["occurrence"] == "m1"


def test_cli_scope_add_that_creates_a_conflict_is_refused_before_any_write(work_root):
    data = coherence_workspace(work_root, active=["m3"], stored=["m3", "m1"], bad=("m1",))
    (work_root / "canon.lock.json").write_bytes(
        json.dumps(canon._build_lock(data), indent=2, sort_keys=True).encode() + b"\n",
    )
    assert canon.load_lock(work_root)  # coherent before the delta
    before = bytes_under(work_root)
    in_memory = copy.deepcopy(canon.load(work_root))
    held = copy.deepcopy(in_memory)

    code, result = decide(work_root, "--add", "m1")

    assert code == 1 and result["ok"] is False
    conflict = result["conflicts"][0]
    assert (conflict["ui_label"], conflict["locale"], conflict["dnt"], conflict["occurrence"]) == (
        "u-add", "ru", "d-brand", "m1",
    )
    assert bytes_under(work_root) == before
    with pytest.raises(SystemExit):
        canon.change_scope(
            work_root, held, "d-brand", canon.scope(held, "d-brand")["entry_sha256"],
            ["m1"], [], "Reviewed explicit scope", "fixture-person",
        )
    assert held == in_memory


def test_cli_scope_removals_repair_two_legacy_conflicts_one_at_a_time(work_root):
    coherence_workspace(work_root, active=["m1", "m2", "m3"])
    old_lock = (work_root / "canon.lock.json").read_bytes()

    code, first = decide(work_root, "--remove", "m1")

    assert code == 0 and first["frozen"] is False
    assert [(c["ui_label"], c["locale"], c["occurrence"]) for c in first["conflicts"]] == [("u-open", "ru", "m2")]
    assert (work_root / "canon.lock.json").read_bytes() == old_lock
    with pytest.raises(SystemExit):
        canon.load_lock(work_root)
    events = scope_events(work_root)
    assert [(e["by"], e["removals"]) for e in events] == [("fixture-person", ["m1"])]
    assert events[0]["reason"] == "Reviewed explicit scope"
    assert (work_root / "runs" / "de" / "canon-audit-d-brand.json").is_file()
    assert (work_root / "runs" / "ru" / "canon-audit-d-brand.json").is_file()

    code, second = decide(work_root, "--remove", "m2")

    assert code == 0 and "frozen" not in second and "conflicts" not in second
    lock = canon.load_lock(work_root)
    assert [e["occurrences"] for e in lock["entries"] if e["kind"] == "dnt"] == [["m3"]]
    assert [e["removals"] for e in scope_events(work_root)] == [["m1"], ["m2"]]
    # The history event keeps this decision's own before/after; the audit
    # request carries the net scope, so the first removal is still audited.
    assert (second["before"], second["after"]) == (["m2", "m3"], ["m3"])
    for locale in ("de", "ru"):
        request = lz_common.read_json(work_root / "runs" / locale / "canon-audit-d-brand.json", "request")
        assert set(request["occurrences"]) == {"m1", "m2", "m3"}
        assert set(request["scope_change"]["before"]) == {"m1", "m2", "m3"}
        assert request["scope_change"]["after"] == ["m3"]
        result = packets.do_build(work_root, "audit", locale, TEMPLATES_DIR, ["d-brand"])
        packet = json.loads((Path(result["batches"][0]["dir"]) / "packet.json").read_text())
        assert {i["id"] for i in packet["items"]} == {"m1", "m2", "m3"}
        assert packet["canon_entry"]["scope_change"] == request["scope_change"]


def test_cli_scope_correction_is_recorded_beside_an_unrelated_conflict(work_root):
    coherence_workspace(work_root, active=["m1", "m3"], bad=("m1",))
    old_lock = (work_root / "canon.lock.json").read_bytes()

    code, result = decide(work_root, "--remove", "m3")

    assert code == 0 and result["frozen"] is False
    assert [c["occurrence"] for c in result["conflicts"]] == ["m1"]
    assert (work_root / "canon.lock.json").read_bytes() == old_lock
    assert scope_events(work_root)[-1]["removals"] == ["m3"]
    code, frozen = run_cli(["freeze", "--root", str(work_root)])
    assert code == 1 and frozen["conflicts"][0]["occurrence"] == "m1"
