"""Tests for `canon.py`: import merge rules, approve, freeze contents,
change's `--expect` guard and its restricted-audit request, plus one
real-subprocess round trip through the CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "skills" / "software-localizer" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import canon  # noqa: E402
import lz_common  # noqa: E402


# --- helpers -----------------------------------------------------------


def make_candidate_input(kind="term", source="Cart", note="", occurrences=None, translations=None):
    return {
        "kind": kind, "source": source, "note": note,
        "occurrences": list(occurrences or []),
        "translations": translations or {},
    }


def make_entry(entry_id="t-cart", kind="term", source="Cart", note="", occurrences=None, translations=None):
    return {
        "id": entry_id, "kind": kind, "source": source, "note": note,
        "occurrences": list(occurrences or []),
        "translations": translations or {},
    }


def proposed(value):
    return {"value": value, "status": "proposed", "approved_by": None, "approved_at": None}


def approved(value, by="alice", at="2026-01-01T00:00:00Z"):
    return {"value": value, "status": "approved", "approved_by": by, "approved_at": at}


def run_cli(args):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "canon.py"), *args],
        capture_output=True, text=True,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one JSON line; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return proc.returncode, json.loads(lines[0])


# --- load / save / load_lock / load_history --------------------------


def test_load_returns_empty_shape_when_no_file(work_root):
    assert canon.load(work_root) == {"schema": 1, "entries": []}


def test_load_lock_empty_when_none_frozen(work_root):
    assert canon.load_lock(work_root) == {"schema": 1, "entries": []}


def test_load_history_empty_when_none_recorded(work_root):
    assert canon.load_history(work_root) == {"schema": 1, "events": []}


# --- import: creation, merge by kind+source -----------------------------


def test_import_creates_a_new_proposed_entry():
    canon_data = {"schema": 1, "entries": []}
    result = canon.import_candidates(canon_data, [
        make_candidate_input(occurrences=["m1"], translations={"de": {"proposed": "Warenkorb", "current": []}}),
    ])
    assert len(canon_data["entries"]) == 1
    entry = canon_data["entries"][0]
    assert entry["kind"] == "term"
    assert entry["source"] == "Cart"
    assert entry["occurrences"] == ["m1"]
    assert entry["translations"]["de"] == proposed("Warenkorb")
    assert result == {"added": [entry["id"]], "merged": [], "skipped": []}


def test_import_merges_by_kind_and_source_unions_occurrences():
    canon_data = {"schema": 1, "entries": []}
    canon.import_candidates(canon_data, [make_candidate_input(occurrences=["m1"])])
    entry_id = canon_data["entries"][0]["id"]

    result = canon.import_candidates(canon_data, [make_candidate_input(occurrences=["m1", "m2"])])

    assert len(canon_data["entries"]) == 1
    assert canon_data["entries"][0]["occurrences"] == ["m1", "m2"]
    assert result == {"added": [], "merged": [entry_id], "skipped": []}


def test_import_different_kind_same_source_are_separate_entries():
    canon_data = {"schema": 1, "entries": []}
    canon.import_candidates(canon_data, [make_candidate_input(kind="term", source="Save")])
    canon.import_candidates(canon_data, [make_candidate_input(kind="ui_label", source="Save")])

    assert len(canon_data["entries"]) == 2
    assert {e["kind"] for e in canon_data["entries"]} == {"term", "ui_label"}
    assert len({e["id"] for e in canon_data["entries"]}) == 2  # ids do not collide


def test_import_fills_a_blank_note_but_never_clobbers_an_existing_one():
    canon_data = {"schema": 1, "entries": []}
    canon.import_candidates(canon_data, [make_candidate_input(note="")])
    canon.import_candidates(canon_data, [make_candidate_input(note="a shopping cart")])
    assert canon_data["entries"][0]["note"] == "a shopping cart"

    canon.import_candidates(canon_data, [make_candidate_input(note="a different note")])
    assert canon_data["entries"][0]["note"] == "a shopping cart"


def test_import_never_overwrites_an_approved_translation():
    canon_data = {"schema": 1, "entries": []}
    canon.import_candidates(canon_data, [
        make_candidate_input(translations={"de": {"proposed": "Warenkorb", "current": []}}),
    ])
    canon_data["entries"][0]["translations"]["de"] = approved("Einkaufskorb")

    canon.import_candidates(canon_data, [
        make_candidate_input(translations={"de": {"proposed": "Warenkorb (v2)", "current": []}}),
    ])

    assert canon_data["entries"][0]["translations"]["de"] == approved("Einkaufskorb")


def test_import_updates_a_still_proposed_translation():
    canon_data = {"schema": 1, "entries": []}
    canon.import_candidates(canon_data, [
        make_candidate_input(translations={"de": {"proposed": "Warenkorb", "current": []}}),
    ])
    canon.import_candidates(canon_data, [
        make_candidate_input(translations={"de": {"proposed": "Warenkorb (revised)", "current": []}}),
    ])
    assert canon_data["entries"][0]["translations"]["de"]["value"] == "Warenkorb (revised)"
    assert canon_data["entries"][0]["translations"]["de"]["status"] == "proposed"


def test_import_keeps_a_nonempty_current_list_for_display():
    """`current` (the locale's live renderings a canon turn saw at proposal
    time) is display-only and must survive `import`: dropping it would let
    an audit-mode proposal reach approval with no side-by-side evidence of
    what it was replacing. An empty `current` is treated as nothing to
    display (see `test_import_creates_a_new_proposed_entry`, which passes
    `"current": []` and asserts the exact `proposed()` shape without it)."""
    canon_data = {"schema": 1, "entries": []}
    canon.import_candidates(canon_data, [
        make_candidate_input(translations={"de": {"proposed": "Warenkorb", "current": ["Im Warenkorb"]}}),
    ])
    assert canon_data["entries"][0]["translations"]["de"]["current"] == ["Im Warenkorb"]


def test_import_drops_a_malformed_current_rather_than_the_whole_candidate():
    canon_data = {"schema": 1, "entries": []}
    canon.import_candidates(canon_data, [
        make_candidate_input(translations={"de": {"proposed": "Warenkorb", "current": [123]}}),
    ])
    entry_translation = canon_data["entries"][0]["translations"]["de"]
    assert "current" not in entry_translation
    assert entry_translation["value"] == "Warenkorb"


def test_import_rejects_an_unknown_kind():
    canon_data = {"schema": 1, "entries": []}
    with pytest.raises(ValueError):
        canon.import_candidates(canon_data, [make_candidate_input(kind="bogus")])


def test_import_rejects_an_empty_source():
    canon_data = {"schema": 1, "entries": []}
    with pytest.raises(ValueError):
        canon.import_candidates(canon_data, [make_candidate_input(source="")])


def test_import_skips_a_candidate_with_non_string_occurrences_and_reports_it():
    """Item 3 of the review fix: `occurrences` containing a non-string id
    (e.g. `[123]`) is not a cannot-run condition like a bad `kind`/`source`
    -- it is skipped and reported, a defence for a hand-written import file
    (packets.py's own canon-turn acceptance already rejects this shape
    before it reaches disk)."""
    canon_data = {"schema": 1, "entries": []}
    result = canon.import_candidates(canon_data, [make_candidate_input(occurrences=[123])])
    assert canon_data["entries"] == []
    assert result == {
        "added": [], "merged": [],
        "skipped": [{"kind": "term", "source": "Cart", "reason": "occurrences must be a list of strings"}],
    }


def test_import_skips_a_candidate_whose_occurrences_is_not_a_list():
    canon_data = {"schema": 1, "entries": []}
    result = canon.import_candidates(canon_data, [
        {"kind": "term", "source": "Cart", "note": "", "occurrences": "m1", "translations": {}},
    ])
    assert canon_data["entries"] == []
    assert len(result["skipped"]) == 1


def test_import_mixed_valid_and_non_string_occurrences_candidates():
    canon_data = {"schema": 1, "entries": []}
    result = canon.import_candidates(canon_data, [
        make_candidate_input(source="Cart", occurrences=["m1"]),
        make_candidate_input(source="Checkout", occurrences=[123]),
    ])
    assert len(canon_data["entries"]) == 1
    assert canon_data["entries"][0]["source"] == "Cart"
    assert len(result["added"]) == 1
    assert len(result["skipped"]) == 1


# --- approve -------------------------------------------------------------


def test_approve_sets_value_from_the_proposal(work_root):
    canon_data = {"schema": 1, "entries": [make_entry(translations={"de": proposed("Warenkorb")})]}
    result = canon.approve(work_root, canon_data, "t-cart", "de", None, "alice")
    entry_t = canon_data["entries"][0]["translations"]["de"]
    assert entry_t["status"] == "approved"
    assert entry_t["value"] == "Warenkorb"
    assert entry_t["approved_by"] == "alice"
    assert result["value"] == "Warenkorb"


def test_approve_value_overrides_the_proposal(work_root):
    canon_data = {"schema": 1, "entries": [make_entry(translations={"de": proposed("Warenkorb")})]}
    canon.approve(work_root, canon_data, "t-cart", "de", "Einkaufswagen", "alice")
    assert canon_data["entries"][0]["translations"]["de"]["value"] == "Einkaufswagen"


def test_approve_without_a_prior_proposal_requires_a_value(work_root):
    canon_data = {"schema": 1, "entries": [make_entry(translations={})]}
    with pytest.raises(SystemExit) as exc:
        canon.approve(work_root, canon_data, "t-cart", "de", None, "alice")
    assert exc.value.code == lz_common.EXIT_FAIL


def test_approve_refuses_an_already_approved_translation(work_root):
    canon_data = {"schema": 1, "entries": [make_entry(translations={"de": approved("Warenkorb", by="bob")})]}
    with pytest.raises(SystemExit) as exc:
        canon.approve(work_root, canon_data, "t-cart", "de", None, "alice")
    assert exc.value.code == lz_common.EXIT_FAIL
    assert canon_data["entries"][0]["translations"]["de"]["approved_by"] == "bob"  # untouched


def test_approve_requires_locale_for_a_non_dnt_entry(work_root):
    canon_data = {"schema": 1, "entries": [make_entry(translations={})]}
    with pytest.raises(SystemExit):
        canon.approve(work_root, canon_data, "t-cart", None, None, "alice")


def test_approve_dnt_entry_as_a_whole(work_root):
    canon_data = {"schema": 1, "entries": [make_entry("d-brand", kind="dnt", source="Kinprove")]}
    result = canon.approve(work_root, canon_data, "d-brand", None, None, "alice")
    entry = canon_data["entries"][0]
    assert entry["approved_by"] == "alice"
    assert entry["approved_at"]
    assert result == {"entry": "d-brand", "kind": "dnt"}


def test_approve_dnt_refuses_locale_or_value(work_root):
    canon_data = {"schema": 1, "entries": [make_entry("d-brand", kind="dnt", source="Kinprove")]}
    with pytest.raises(SystemExit):
        canon.approve(work_root, canon_data, "d-brand", "de", None, "alice")


def test_approve_appends_to_history(work_root):
    canon_data = {"schema": 1, "entries": [make_entry(translations={"de": proposed("Warenkorb")})]}
    canon.approve(work_root, canon_data, "t-cart", "de", None, "alice")
    events = canon.load_history(work_root)["events"]
    assert len(events) == 1
    assert events[0]["action"] == "approve"
    assert events[0]["entry"] == "t-cart"
    assert events[0]["after"] == "Warenkorb"


# --- freeze ----------------------------------------------------------------


def test_freeze_includes_an_approved_dnt_entry(work_root):
    entry = make_entry("d-brand", kind="dnt", source="Kinprove", occurrences=["m1"])
    entry["approved_by"] = "alice"
    entry["approved_at"] = "2026-01-01T00:00:00Z"
    canon_data = {"schema": 1, "entries": [entry]}
    canon.save(work_root, canon_data)

    lock = canon.freeze(work_root)

    assert len(lock["entries"]) == 1
    locked = lock["entries"][0]
    assert locked["id"] == "d-brand"
    assert locked["kind"] == "dnt"
    assert locked["occurrences"] == ["m1"]
    assert "translations" not in locked
    assert "sha256" in locked and isinstance(locked["sha256"], str)


def test_freeze_excludes_an_unapproved_dnt_entry(work_root):
    """A model-proposed `dnt` (`import`ed but never `approve`d) must not
    lock: `freeze` only carries a `dnt` entry a person has actually
    approved (security-review observation)."""
    canon_data = {"schema": 1, "entries": [make_entry("d-brand", kind="dnt", source="Kinprove", occurrences=["m1"])]}
    canon.save(work_root, canon_data)

    lock = canon.freeze(work_root)

    assert lock["entries"] == []


def test_freeze_includes_only_approved_translations_on_disk(work_root):
    canon_data = {"schema": 1, "entries": [make_entry(translations={
        "de": approved("Warenkorb"),
        "ru": proposed("Корзина"),
    })]}
    canon.save(work_root, canon_data)

    lock = canon.freeze(work_root)

    assert len(lock["entries"]) == 1
    locked_translations = lock["entries"][0]["translations"]
    assert set(locked_translations) == {"de"}
    assert locked_translations["de"]["value"] == "Warenkorb"


def test_freeze_excludes_current_from_a_locked_translation(work_root):
    """Review round 3, item 5: `current` is display-only and must never
    reach `canon.lock.json`, which packets embed into every turn."""
    approved_with_current = approved("Warenkorb")
    approved_with_current["current"] = ["Im Warenkorb"]
    canon_data = {"schema": 1, "entries": [make_entry(translations={"de": approved_with_current})]}
    canon.save(work_root, canon_data)

    lock = canon.freeze(work_root)

    locked_translation = lock["entries"][0]["translations"]["de"]
    assert "current" not in locked_translation
    assert locked_translation["value"] == "Warenkorb"


def test_freeze_excludes_an_entry_with_no_approved_translation(work_root):
    canon_data = {"schema": 1, "entries": [make_entry(translations={"de": proposed("Warenkorb")})]}
    canon.save(work_root, canon_data)
    lock = canon.freeze(work_root)
    assert lock["entries"] == []


def test_freeze_writes_canon_lock_json(work_root):
    canon_data = {"schema": 1, "entries": [make_entry("d-brand", kind="dnt", source="Kinprove")]}
    canon.save(work_root, canon_data)
    canon.freeze(work_root)
    on_disk = json.loads((work_root / "canon.lock.json").read_text(encoding="utf-8"))
    assert on_disk == canon.load_lock(work_root)


# --- change ------------------------------------------------------------


def test_change_refuses_a_wrong_expect(work_root):
    canon_data = {"schema": 1, "entries": [make_entry(occurrences=["m1"], translations={"de": approved("Warenkorb")})]}
    canon.save(work_root, canon_data)

    with pytest.raises(SystemExit) as exc:
        canon.change(work_root, canon_data, "t-cart", "de", "WRONG", "Einkaufskorb", "typo fix", "bob")

    assert exc.value.code == lz_common.EXIT_FAIL
    assert canon_data["entries"][0]["translations"]["de"]["value"] == "Warenkorb"  # untouched
    assert not (work_root / "runs").exists()  # no audit request, no history, on a refusal


def test_change_refuses_when_the_locale_was_never_approved(work_root):
    canon_data = {"schema": 1, "entries": [make_entry(translations={})]}
    canon.save(work_root, canon_data)
    with pytest.raises(SystemExit):
        canon.change(work_root, canon_data, "t-cart", "de", "Warenkorb", "Einkaufskorb", "reason", "bob")


def test_change_refuses_a_dnt_entry(work_root):
    canon_data = {"schema": 1, "entries": [make_entry("d-brand", kind="dnt", source="Kinprove")]}
    canon.save(work_root, canon_data)
    with pytest.raises(SystemExit):
        canon.change(work_root, canon_data, "d-brand", "de", "x", "y", "reason", "bob")


def test_change_updates_value_writes_audit_request_history_and_refreezes(work_root):
    canon_data = {"schema": 1, "entries": [
        make_entry(occurrences=["m1", "m2"], translations={"de": approved("Warenkorb")}),
    ]}
    canon.save(work_root, canon_data)

    event = canon.change(work_root, canon_data, "t-cart", "de", "Warenkorb", "Einkaufskorb", "typo fix", "bob")

    entry_t = canon_data["entries"][0]["translations"]["de"]
    assert entry_t["value"] == "Einkaufskorb"
    assert entry_t["status"] == "approved"
    assert entry_t["approved_by"] == "bob"
    assert event["before"] == "Warenkorb"
    assert event["after"] == "Einkaufskorb"
    assert event["reason"] == "typo fix"

    audit_path = work_root / "runs" / "de" / "canon-audit-t-cart.json"
    assert audit_path.is_file()
    audit_request = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit_request["entry"] == "t-cart"
    assert audit_request["locale"] == "de"
    assert audit_request["before"] == "Warenkorb"
    assert audit_request["after"] == "Einkaufskorb"
    assert audit_request["reason"] == "typo fix"
    assert audit_request["occurrences"] == ["m1", "m2"]

    history_events = canon.load_history(work_root)["events"]
    assert history_events[-1]["action"] == "change"

    lock = canon.load_lock(work_root)
    assert lock["entries"][0]["translations"]["de"]["value"] == "Einkaufskorb"


# --- CLI (real subprocess) --------------------------------------------------


def test_cli_import_approve_freeze_change_roundtrip(work_root):
    candidates_file = work_root / "canon_turn_output.json"
    candidates_file.write_text(json.dumps({
        "candidates": [{
            "kind": "term", "source": "Cart", "note": "shopping cart",
            "occurrences": ["m1"],
            "translations": {"de": {"proposed": "Warenkorb", "current": []}},
        }],
    }), encoding="utf-8")

    code, result = run_cli(["import", "--root", str(work_root), "--file", str(candidates_file)])
    assert code == 0 and result["ok"] is True
    entry_id = result["added"][0]

    code, result = run_cli([
        "approve", "--root", str(work_root), "--entry", entry_id, "--locale", "de", "--by", "alice",
    ])
    assert code == 0 and result["ok"] is True

    code, result = run_cli(["freeze", "--root", str(work_root)])
    assert code == 0 and result["ok"] is True and result["entries"] == 1

    code, result = run_cli([
        "change", "--root", str(work_root), "--entry", entry_id, "--locale", "de",
        "--expect", "Warenkorb", "--value", "Einkaufskorb", "--reason", "clearer", "--by", "bob",
    ])
    assert code == 0 and result["ok"] is True

    lock = canon.load_lock(work_root)
    assert lock["entries"][0]["translations"]["de"]["value"] == "Einkaufskorb"
    assert (work_root / "runs" / "de" / f"canon-audit-{entry_id}.json").is_file()

    code, result = run_cli([
        "change", "--root", str(work_root), "--entry", entry_id, "--locale", "de",
        "--expect", "WRONG", "--value", "Nope", "--reason", "x", "--by", "carol",
    ])
    assert code == 1 and result["ok"] is False
