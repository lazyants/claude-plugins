"""Person-approved shared-note corrections and their downstream evidence boundary."""

from copy import deepcopy
from pathlib import Path

import pytest

import canon
import ledger
import lz_common
import packets
import report
from test_canon import run_cli
from test_packets import (
    TEMPLATES_DIR, make_canon_entry, make_cfg, make_message, make_messages,
    packet_of, research_term, setup_research_workspace, verdict_for, write_output,
)


OLD_NOTE = "point of interest"
NEW_NOTE = "Person of Interest"


def setup(root, kind="term"):
    entry = make_canon_entry(
        "t-poi", "POI", kind=kind, note=OLD_NOTE, occurrences=["named"],
        locales={} if kind == "dnt" else {"de": ("POI", "approved"), "fr": ("POI", "approved")},
    )
    if kind == "dnt":
        entry.update(approved_by="original owner", approved_at="2026-01-01")
    sibling = make_canon_entry("t-cart", "Cart", locales={"de": ("Warenkorb", "approved")})
    setup_research_workspace(root, [entry, sibling], cfg=make_cfg(target_locales=("de", "fr")),
                             messages=make_messages([
                                 make_message("named", "Individual", targets={"de": "Person", "fr": "Personne"}),
                                 make_message("substring", "Open POI", targets={"de": "POI öffnen", "fr": "Ouvrir POI"}),
                                 make_message("unrelated", "Cart", targets={"de": "Warenkorb"}),
                             ]))
    canon.freeze(root)
    for locale in ("de", "fr"):
        evidence = {
            "kind": kind, "source": "POI", "research": research_term(),
            "research_sha256": canon.research_sha256(research_term()),
            "researched": {"run": "old-research", "at": "2026-01-01"},
            "verification": {"verdict": "holds", "findings": [{"index": 0, "confirmed": True}],
                             "run": "old-verifier", "at": "2026-01-02"},
        }
        canon.save_research(root, locale, {"schema": 1, "locale": locale,
                                         "entries": {"t-poi": evidence, "t-cart": deepcopy(evidence)}})
    ledger.save(root, {"schema": 1, "locales": {
        locale: {i: {"state": "existing", "candidate": {"value": "old reviewed candidate"},
                     "audit_proposal": {"value": "old proposed wording"},
                     "notes": [{"at": "2026-01-01", "note": "prior audit"}]}
                 for i in ("named", "substring", "unrelated")}
        for locale in ("de", "fr")
    }})
    return deepcopy(canon.load(root))


def correct(root, expect=OLD_NOTE, note=NEW_NOTE):
    return canon.change_note(root, canon.load(root), "t-poi", expect, note,
                             "Owner confirmed the product definition", "alice")


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("kind", ["term", "ui_label", "dnt"])
def test_cli_corrects_only_note_and_preserves_prior_evidence(work_root, kind):
    original = setup(work_root, kind)
    old_lock = canon.load_lock(work_root)
    research_before = {loc: canon.load_research(work_root, loc) for loc in ("de", "fr")}
    ledger_before = ledger.load(work_root)
    prior_run = work_root / "runs" / "de" / "old-audit" / "output.json"
    lz_common.atomic_write_json(prior_run, {"verdicts": {"named": {"verdict": "pass"}}})
    prior_bytes = prior_run.read_bytes()

    code, result = run_cli(["change-note", "--root", str(work_root), "--entry", "t-poi",
                            "--expect", OLD_NOTE, "--note", NEW_NOTE,
                            "--reason", "Owner confirmed the product definition", "--by", "alice"])
    assert code == 0 and result["ok"]
    after = canon.load(work_root)
    revision = after["entries"][0].pop("note_revision")
    after["entries"][0]["note"] = OLD_NOTE
    assert after == original  # approvals, locale values, occurrences, siblings stay exact
    event = canon.load_history(work_root)["events"][-1]
    assert (event["action"], event["before"], event["after"], event["by"], event["impact"]) == (
        "change-note", OLD_NOTE, NEW_NOTE, "alice", "semantic")
    assert event["reason"] and event["at"] and event["note_revision"] == revision
    lock = canon.load_lock(work_root)
    assert lock["entries"][0]["sha256"] != old_lock["entries"][0]["sha256"]
    assert lock["entries"][1:] == old_lock["entries"][1:]
    assert canon.freeze(work_root) == lock
    assert prior_run.read_bytes() == prior_bytes

    for locale in ("de", "fr"):
        data = canon.load_research(work_root, locale)
        record = data["entries"]["t-poi"]
        assert event["research_before"][locale] == research_before[locale]["entries"]["t-poi"]
        assert record["research"] == event["research_before"][locale]["research"]
        assert record["verification"] == event["research_before"][locale]["verification"]
        assert canon.research_summary(record)["verdict"] is None
        assert "canon note changed" in " ".join(canon.research_flags(record))
        assert data["entries"]["t-cart"] == research_before[locale]["entries"]["t-cart"]
        current_ledger = ledger.load(work_root)["locales"][locale]
        assert current_ledger["unrelated"] == ledger_before["locales"][locale]["unrelated"]
        for msg_id in ("named", "substring"):
            assert current_ledger[msg_id]["candidate"] is None
            assert "audit_proposal" not in current_ledger[msg_id]
            assert event["ledger_before"][locale][msg_id] == ledger_before["locales"][locale][msg_id]
        request = lz_common.read_json(work_root / "runs" / locale / "canon-audit-t-poi.json", "audit")
        assert request["field"] == "note" and request["note_revision"] == revision
        assert request["occurrences"] == ["named", "substring"]
        built = packets.do_build(work_root, "audit", locale, TEMPLATES_DIR, entry_ids=["t-poi"])
        packet = packet_of(built["batches"][0])
        assert packet["canon_entry"]["field"] == "note"
        assert {i["id"] for i in packet["items"]} == {"named", "substring"}
        # One unrelated clean record remains verified; corrected evidence does not.
        text = report.build_report(work_root, locale)
        assert "researched: 2, verified: 1" in text and "canon note changed" in text


@pytest.mark.parametrize("extra", [
    ["--expect", "stale note", "--reason", "why", "--by", "alice"],
    ["--expect", OLD_NOTE, "--reason", " ", "--by", "alice"],
    ["--expect", OLD_NOTE, "--reason", "why", "--by", " "],
])
def test_refusal_is_read_only(work_root, extra):
    setup(work_root)
    before = snapshot(work_root)
    code, result = run_cli(["change-note", "--root", str(work_root), "--entry", "t-poi",
                            "--note", NEW_NOTE, *extra])
    assert code == 1 and not result["ok"]
    assert snapshot(work_root) == before


def test_unchanged_note_and_unapproved_entry_refused(work_root):
    setup(work_root)
    before = snapshot(work_root)
    with pytest.raises(SystemExit):
        correct(work_root, note=OLD_NOTE)
    assert snapshot(work_root) == before
    data = canon.load(work_root)
    for t in data["entries"][0]["translations"].values():
        t["status"] = "proposed"
    canon.save(work_root, data)
    before = snapshot(work_root)
    with pytest.raises(SystemExit):
        correct(work_root)
    assert snapshot(work_root) == before


def test_old_turns_cannot_restore_current_evidence_even_after_note_revert(work_root):
    setup(work_root)
    research_batch = packets.do_build(work_root, "research", "de", TEMPLATES_DIR,
                                      entry_ids=["t-poi"])["batches"][0]
    research_packet = packet_of(research_batch)
    verify_batch = packets.do_build(work_root, "research_verify", "de", TEMPLATES_DIR,
                                    entry_ids=["t-poi"])["batches"][0]
    verify_packet = packet_of(verify_batch)
    audit_batch = packets.do_build(work_root, "audit", "de", TEMPLATES_DIR)["batches"][0]
    audit_packet = packet_of(audit_batch)
    audit_dir = Path(audit_batch["dir"])
    output = write_output(audit_dir, {"verdicts": {
        i["id"]: {"value_sha256": i["value_sha256"], "verdict": "pass", "issues": [],
                  "proposed": None, "new_canon_candidates": []} for i in audit_packet["items"]
    }})
    first_revision = correct(work_root)["note_revision"]
    assert correct(work_root, expect=NEW_NOTE, note=OLD_NOTE)["note_revision"] != first_revision
    before = snapshot(work_root)
    result = packets.accept_research(work_root, research_packet, Path(research_batch["dir"]),
                                      {"terms": {"t-poi": research_term()}})
    assert result["stored"] == [] and result["invalid"][0]["id"] == "t-poi"
    result = packets.accept_research_verify(work_root, verify_packet, Path(verify_batch["dir"]),
                                             {"verdicts": {"t-poi": verdict_for(verify_packet["items"][0])}})
    assert result["verified"] == [] and result["missing"] == ["t-poi"]
    with pytest.raises(SystemExit):
        packets.do_accept(work_root, audit_dir, output)
    with pytest.raises(SystemExit):
        packets.do_build(work_root, "research_verify", "de", TEMPLATES_DIR, entry_ids=["t-poi"])
    assert snapshot(work_root) == before


def test_fresh_research_and_verification_can_be_current_without_losing_old_evidence(work_root):
    setup(work_root)
    event = correct(work_root)
    batch = packets.do_build(work_root, "research", "de", TEMPLATES_DIR, entry_ids=["t-poi"])["batches"][0]
    run_dir = Path(batch["dir"])
    research_output = {"terms": {"t-poi": research_term()}, "note_revisions": packet_of(batch)["note_revisions"]}
    result = packets.do_accept(work_root, run_dir, write_output(run_dir, research_output))
    assert result["stored"] == ["t-poi"]
    batch = packets.do_build(work_root, "research_verify", "de", TEMPLATES_DIR, entry_ids=["t-poi"])["batches"][0]
    item = packet_of(batch)["items"][0]
    run_dir = Path(batch["dir"])
    result = packets.do_accept(work_root, run_dir, write_output(run_dir, {
        "verdicts": {"t-poi": verdict_for(item)}, "note_revisions": packet_of(batch)["note_revisions"]}))
    assert result["verified"] == ["t-poi"]
    assert canon.research_summary(canon.load_research(work_root, "de")["entries"]["t-poi"])["verdict"] == "holds"
    assert canon.load_history(work_root)["events"][-1]["research_before"] == event["research_before"]
    # No research run or verification in the sibling locale was silently authorized/completed.
    assert canon.research_summary(canon.load_research(work_root, "fr")["entries"]["t-poi"])["verdict"] is None


def test_import_keeps_existing_nonempty_note(work_root):
    setup(work_root)
    data = canon.load(work_root)
    canon.import_candidates(data, [{"kind": "term", "source": "POI", "note": NEW_NOTE, "translations": {}}])
    assert data["entries"][0]["note"] == OLD_NOTE


def test_import_preserves_a_person_cleared_note(work_root):
    setup(work_root)
    event = correct(work_root, note="")
    data = canon.load(work_root)
    canon.import_candidates(data, [{"kind": "term", "source": "POI", "note": "model suggestion", "translations": {}}])
    assert data["entries"][0]["note"] == ""
    assert data["entries"][0]["note_revision"] == event["note_revision"]


def test_locale_wording_change_preserves_note_audit_scope_and_prior_request(work_root):
    setup(work_root)
    correct(work_root)
    request_path = work_root / "runs" / "de" / "canon-audit-t-poi.json"
    previous_request = lz_common.read_json(request_path, "audit")
    sibling_path = work_root / "runs" / "fr" / "canon-audit-t-poi.json"
    sibling_bytes = sibling_path.read_bytes()
    data = canon.load(work_root)
    event = canon.change(work_root, data, "t-poi", "de", "POI", "Person", "Owner chose wording", "bob")
    canon.save(work_root, data)
    request = lz_common.read_json(request_path, "audit")
    assert request["field"] == "note" and request["after"] == NEW_NOTE
    assert request["occurrences"] == ["named", "substring"]
    assert request["translation_change"]["after"] == "Person"
    assert event["audit_request_before"] == previous_request
    assert canon.load_history(work_root)["events"][-1]["audit_request_before"] == previous_request
    assert sibling_path.read_bytes() == sibling_bytes


def test_note_correction_never_resumes_partial_research_for_the_old_meaning(work_root):
    setup(work_root)
    records = canon.load_research(work_root, "de")
    record = records["entries"]["t-poi"]
    record["research"]["progress"] = {"status": "resource_limited", "remaining": ["old-sense query"]}
    record["research_sha256"] = canon.research_sha256(record["research"])
    record["verification"] = None
    old_findings = deepcopy(record["research"]["findings"])
    canon.save_research(work_root, "de", records)
    event = correct(work_root)
    # Default continuation cannot silently select the old semantic scope.
    assert packets.do_build(work_root, "research", "de", TEMPLATES_DIR)["selected"] == 0
    assert packets.do_build(work_root, "research_verify", "de", TEMPLATES_DIR)["selected"] == 0
    batch = packets.do_build(work_root, "research", "de", TEMPLATES_DIR, entry_ids=["t-poi"])["batches"][0]
    packet = packet_of(batch)
    assert "resume" not in packet["items"][0]
    assert packet["items"][0]["note"] == NEW_NOTE
    new_findings = [{"site": "New source", "url": "https://example.org/new-sense", "rendering": "Person",
                     "quote": "The intended person"}]
    run_dir = Path(batch["dir"])
    output = write_output(run_dir, {"note_revisions": packet["note_revisions"], "terms": {
        "t-poi": research_term(recommendation="Person", findings=new_findings)}})
    assert packets.do_accept(work_root, run_dir, output)["stored"] == ["t-poi"]
    current = canon.load_research(work_root, "de")["entries"]["t-poi"]
    assert current["research"]["findings"] == new_findings
    assert current["attempts"]  # sibling release's archival behavior is retained
    assert event["research_before"]["de"]["research"]["findings"] == old_findings


def test_rebuilt_packet_refuses_an_old_answer_without_the_revision_echo(work_root):
    setup(work_root)
    correct(work_root)
    batch = packets.do_build(work_root, "research", "de", TEMPLATES_DIR, entry_ids=["t-poi"])["batches"][0]
    run_dir = Path(batch["dir"])
    output = write_output(run_dir, {"terms": {"t-poi": research_term()}})
    before = snapshot(work_root)
    with pytest.raises(SystemExit):
        packets.do_accept(work_root, run_dir, output)
    assert snapshot(work_root) == before


def test_restricted_audit_without_literal_term_still_binds_to_note_revision(work_root):
    setup(work_root)
    # Keep just the explicit nonliteral occurrence: the enforcement subset is empty.
    data = lz_common.load_messages(work_root / "messages.json")
    data["messages"] = [m for m in data["messages"] if m["id"] == "named"]
    lz_common.atomic_write_json(work_root / "messages.json", data)
    first = correct(work_root)
    batch = packets.do_build(work_root, "audit", "de", TEMPLATES_DIR, entry_ids=["t-poi"])["batches"][0]
    packet = packet_of(batch)
    assert packet["canon"]["entries"] == []
    assert packet["note_revisions"] == {"t-poi": first["note_revision"]}
    run_dir = Path(batch["dir"])
    item = packet["items"][0]
    output = write_output(run_dir, {"note_revisions": packet["note_revisions"], "verdicts": {
        "named": {"value_sha256": item["value_sha256"], "verdict": "pass", "issues": [],
                  "proposed": None, "new_canon_candidates": []}}})
    assert packets.do_accept(work_root, run_dir, output)["passed"] == ["named"]
    correct(work_root, expect=NEW_NOTE, note="A person under investigation")
    before = snapshot(work_root)
    with pytest.raises(SystemExit):
        packets.do_accept(work_root, run_dir, output)
    assert snapshot(work_root) == before
    assert canon.load_history(work_root)["events"][-1]["audit_requests_before"]["de"]["note_revision"] == first["note_revision"]


@pytest.mark.parametrize("kind", ["translate", "audit"])
def test_nonliteral_ordinary_packets_and_following_review_keep_corrected_meaning(work_root, kind):
    setup(work_root)
    messages = lz_common.load_messages(work_root / "messages.json")
    messages["messages"] = [m for m in messages["messages"] if m["id"] == "named"]
    message = messages["messages"][0]
    if kind == "translate":
        message["targets"].pop("de")
    lz_common.atomic_write_json(work_root / "messages.json", messages)
    data = ledger.load(work_root)
    data["locales"]["de"] = {"named": {"state": "pending" if kind == "translate" else "existing",
                                       "candidate": None, "rounds": 0}}
    ledger.save(work_root, data)
    old_batch = packets.do_build(work_root, kind, "de", TEMPLATES_DIR)["batches"][0]
    old_dir = Path(old_batch["dir"])
    old_packet = packet_of(old_batch)
    assert old_packet["canon"]["entries"] == []
    if kind == "translate":
        output = {"translations": {"named": "Person"}}
    else:
        output = {"verdicts": {"named": {
            "value_sha256": old_packet["items"][0]["value_sha256"], "verdict": "fail",
            "issues": [{"kind": "meaning", "text": "Use the intended sense"}],
            "proposed": "Individuum", "new_canon_candidates": [],
        }}}
    old_output = write_output(old_dir, output)
    correction = correct(work_root)
    before = snapshot(work_root)
    with pytest.raises(SystemExit):
        packets.do_accept(work_root, old_dir, old_output)
    assert snapshot(work_root) == before

    fresh = packets.do_build(work_root, kind, "de", TEMPLATES_DIR)["batches"][0]
    fresh_packet = packet_of(fresh)
    assert fresh_packet["canon"]["entries"] == []  # no broader mechanical rule
    assert fresh_packet["canon"]["note_context"][0]["note"] == NEW_NOTE
    output["note_revisions"] = fresh_packet["note_revisions"]
    fresh_dir = Path(fresh["dir"])
    result = packets.do_accept(work_root, fresh_dir, write_output(fresh_dir, output))
    assert result["accepted" if kind == "translate" else "audit_proposals"] == ["named"]
    if kind == "audit":
        assert packets.do_accept_audit(work_root, "de", ["named"], "alice")["accepted"] == ["named"]
    candidate = ledger.load(work_root)["locales"]["de"]["named"]["candidate"]
    assert candidate["canon_sha256"] == ledger.canon_sha256(message, canon.load_lock(work_root))
    assert candidate["canon_sha256"] != lz_common.sha256_json([])
    review_batch = packets.do_build(work_root, "review", "de", TEMPLATES_DIR)["batches"][0]
    review_packet = packet_of(review_batch)
    assert review_packet["canon"]["entries"] == []
    assert review_packet["canon"]["note_context"][0]["note"] == NEW_NOTE
    assert review_packet["note_revisions"] == {"t-poi": correction["note_revision"]}
    review_dir = Path(review_batch["dir"])
    item = review_packet["items"][0]
    review_output = write_output(review_dir, {"note_revisions": review_packet["note_revisions"], "verdicts": {
        "named": {"value_sha256": item["value_sha256"], "verdict": "pass", "issues": [],
                  "proposed": None, "new_canon_candidates": []}}})
    assert packets.do_accept(work_root, review_dir, review_output)["passed"] == ["named"]
    correct(work_root, expect=NEW_NOTE, note="A person under investigation")
    before = snapshot(work_root)
    with pytest.raises(SystemExit):
        packets.do_accept(work_root, review_dir, review_output)
    assert snapshot(work_root) == before
