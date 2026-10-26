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
import checks  # noqa: E402
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


def test_import_keeps_earlier_current_when_a_later_proposal_carries_none():
    """A review turn's `new_canon_candidates` reach `import` with
    `"current": []`; replacing the still-proposed value must not wipe the
    renderings an earlier canon batch found (#981). A rendering seen twice
    is listed once."""
    canon_data = {"schema": 1, "entries": []}
    canon.import_candidates(canon_data, [
        make_candidate_input(translations={"de": {"proposed": "Warenkorb", "current": ["Im Warenkorb", "Korb"]}}),
    ])
    canon.import_candidates(canon_data, [
        make_candidate_input(translations={"de": {"proposed": "Einkaufswagen", "current": []}}),
    ])
    canon.import_candidates(canon_data, [
        make_candidate_input(translations={"de": {"proposed": "Einkaufswagen", "current": ["Korb"]}}),
    ])
    translation = canon_data["entries"][0]["translations"]["de"]
    assert translation["value"] == "Einkaufswagen"
    assert translation["current"] == ["Im Warenkorb", "Korb"]


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


def make_research_record(confidence="high", verdict=None, confirmed=(), findings=1, better=""):
    """A research record shaped as `packets.py accept` stores it. `verdict`
    None leaves it unverified; otherwise `confirmed` lists one bool per
    finding."""
    research = {
        "recommendation": "Warenkorb", "confidence": confidence, "note": "n",
        "findings": [
            {"site": f"s{i}", "url": f"https://example.org/{i}", "rendering": "Warenkorb", "quote": "q"}
            for i in range(findings)
        ],
    }
    verification = None
    if verdict is not None:
        verification = {
            "research_sha256": canon.research_sha256(research),
            "findings": [{"index": i, "confirmed": c, "detail": "d"} for i, c in enumerate(confirmed)],
            "verdict": verdict, "problems": "", "better": better, "run": "r", "at": "2026-01-01T00:00:00Z",
        }
    return {
        "kind": "term", "source": "Cart", "research": research,
        "research_sha256": canon.research_sha256(research),
        "researched": {"run": "r", "at": "2026-01-01T00:00:00Z"}, "verification": verification,
    }


def save_research_record(root, record, entry_id="t-cart", locale="de"):
    data = canon.load_research(root, locale)
    data["entries"][entry_id] = record
    canon.save_research(root, locale, data)


def test_approve_with_a_verified_research_record_adds_the_summary(work_root):
    save_research_record(work_root, make_research_record(
        verdict="weak", confirmed=[True, False, False], findings=3, better="Einkaufswagen"))
    canon_data = {"schema": 1, "entries": [make_entry(translations={"de": proposed("Warenkorb")})]}
    result = canon.approve(work_root, canon_data, "t-cart", "de", None, "alice")
    expected = {"recommendation": "Warenkorb", "confidence": "high", "findings": 3,
                "confirmed": 1, "verdict": "weak", "better": "Einkaufswagen"}
    assert result["research"] == expected
    assert canon.load_history(work_root)["events"][0]["research"] == expected


def test_approve_with_an_unverified_research_record_adds_a_summary_with_none_fields(work_root):
    save_research_record(work_root, make_research_record())
    canon_data = {"schema": 1, "entries": [make_entry(translations={"de": proposed("Warenkorb")})]}
    result = canon.approve(work_root, canon_data, "t-cart", "de", None, "alice")
    assert result["research"]["confirmed"] is None
    assert result["research"]["verdict"] is None
    assert canon.load_history(work_root)["events"][0]["research"] == result["research"]


def test_approve_never_refuses_a_refuted_recommendation(work_root):
    save_research_record(work_root, make_research_record(verdict="refuted", confirmed=[False]))
    canon_data = {"schema": 1, "entries": [make_entry(translations={"de": proposed("Warenkorb")})]}
    canon.approve(work_root, canon_data, "t-cart", "de", None, "alice")
    assert canon_data["entries"][0]["translations"]["de"]["status"] == "approved"


def test_approve_without_a_research_record_adds_no_research_key(work_root):
    # A record for ANOTHER entry and for ANOTHER locale must not leak in.
    save_research_record(work_root, make_research_record(), entry_id="t-other")
    save_research_record(work_root, make_research_record(), locale="ru")
    canon_data = {"schema": 1, "entries": [make_entry(translations={"de": proposed("Warenkorb")})]}
    result = canon.approve(work_root, canon_data, "t-cart", "de", None, "alice")
    assert "research" not in result
    assert "research" not in canon.load_history(work_root)["events"][0]


# --- research_summary / research_flags -------------------------------------


def test_research_flags_unverified():
    assert canon.research_flags(make_research_record()) == ["not verified"]


@pytest.mark.parametrize("verdict, flag", [("weak", "verdict weak"), ("refuted", "verdict refuted")])
def test_research_flags_a_verdict_other_than_holds(verdict, flag):
    record = make_research_record(verdict=verdict, confirmed=[True])
    assert canon.research_flags(record) == [flag]


def test_research_flags_low_confidence():
    record = make_research_record(confidence="low", verdict="holds", confirmed=[True])
    assert canon.research_flags(record) == ["low confidence"]


def test_research_flags_a_verified_record_with_zero_confirmed_findings():
    record = make_research_record(verdict="holds", confirmed=[False, False], findings=2)
    assert canon.research_flags(record) == ["no confirmed source"]


def test_research_flags_a_verified_record_with_no_findings_at_all():
    record = make_research_record(confidence="low", verdict="holds", confirmed=[], findings=0)
    assert canon.research_flags(record) == ["low confidence", "no confirmed source"]


def test_research_flags_collects_every_reason():
    record = make_research_record(confidence="low", verdict="refuted", confirmed=[False])
    assert canon.research_flags(record) == ["verdict refuted", "low confidence", "no confirmed source"]


def test_research_flags_empty_for_a_clean_record():
    record = make_research_record(confidence="high", verdict="holds", confirmed=[True, False], findings=2)
    assert canon.research_flags(record) == []
    summary = canon.research_summary(record)
    assert summary["confirmed"] == 1
    assert summary["findings"] == 2
    assert summary["better"] is None  # an empty `better` is reported as None


def test_research_summary_of_an_unverified_record():
    assert canon.research_summary(make_research_record(findings=2)) == {
        "recommendation": "Warenkorb", "confidence": "high", "findings": 2,
        "confirmed": None, "verdict": None, "better": None,
    }


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


def test_freeze_output_is_byte_identical_with_and_without_a_research_file(work_root):
    canon_data = {"schema": 1, "entries": [
        make_entry(entry_id="t-cart", translations={"de": approved("Warenkorb")}),
        make_entry(entry_id="d-brand", kind="dnt", source="Kinprove"),
    ]}
    canon_data["entries"][1]["approved_by"] = "alice"
    canon_data["entries"][1]["approved_at"] = "2026-01-01T00:00:00Z"
    canon.save(work_root, canon_data)

    canon.freeze(work_root)
    lock_path = work_root / "canon.lock.json"
    without_research = lock_path.read_bytes()
    assert canon.load_lock(work_root)["entries"], "the lock must be non-empty for this comparison to bite"

    save_research_record(work_root, make_research_record(verdict="weak", confirmed=[False]))
    canon.freeze(work_root)
    assert lock_path.read_bytes() == without_research


# --- change ------------------------------------------------------------


@pytest.mark.parametrize("invalid", ["missing_scope", "unapproved_member"])
def test_change_cli_invalid_group_preserves_canon_lock_and_history(work_root, invalid):
    canon_data = {"schema": 1, "entries": [
        make_entry(occurrences=["m1"], translations={"de": approved("Warenkorb")}),
        make_entry("t-save", source="Save", translations={"de": approved("Speichern")}),
    ], "groups": [{
        "id": "checkout", "locale": "de", "entry_ids": ["t-cart", "t-save"],
        "note": "Checkout terminology", "scope": "Cart and save button",
        "approved_by": "alice", "approved_at": "2026-01-01T00:00:00Z",
    }]}
    canon.save(work_root, canon_data)
    canon.freeze(work_root)
    lz_common.atomic_write_json(work_root / "canon.history.json", {"schema": 1, "events": [{
        "action": "approve", "entry": "t-cart", "locale": "de", "before": None,
        "after": "Warenkorb", "by": "alice", "at": "2026-01-01T00:00:00Z",
    }]})
    # A hand-maintained group can become incomplete between freeze and change.
    if invalid == "missing_scope":
        del canon_data["groups"][0]["scope"]
    else:
        canon_data["entries"][1]["translations"]["de"]["status"] = "proposed"
    canon.save(work_root, canon_data)
    paths = [work_root / name for name in ("canon.json", "canon.lock.json", "canon.history.json")]
    before = {path: path.read_bytes() for path in paths}

    code, result = run_cli([
        "change", "--root", str(work_root), "--entry", "t-cart", "--locale", "de",
        "--expect", "Warenkorb", "--value", "Einkaufskorb", "--reason", "owner correction", "--by", "bob",
    ])

    assert code == lz_common.EXIT_CANNOT
    assert not result["ok"]
    assert ("scope" if invalid == "missing_scope" else "not approved") in result["error"]
    assert {path: path.read_bytes() for path in paths} == before
    assert not (work_root / "runs/de/canon-audit-t-cart.json").exists()


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


def test_cli_import_unions_current_across_canon_batches(work_root):
    """#981: the canon turn runs in batches and a product term arrives from
    many of them. Every batch's current renderings must stay visible beside
    the proposal; the proposal itself is the latest batch's."""
    batches = {
        "batch-001.json": ("web:a", "Treffer", ["Treffer"]),
        "batch-007.json": ("web:b", "Übereinstimmung", ["Übereinstimmung", "Match"]),
    }
    results = []
    for name, (occurrence, proposal, current) in batches.items():
        path = work_root / name
        path.write_text(json.dumps({"candidates": [{
            "kind": "term", "source": "match", "note": "",
            "occurrences": [occurrence],
            "translations": {"de": {"proposed": proposal, "current": current}},
        }]}), encoding="utf-8")
        code, result = run_cli(["import", "--root", str(work_root), "--file", str(path)])
        assert code == 0 and result["ok"] is True
        results.append(result)
    assert results[0]["added"] == ["t-match"] and results[1]["merged"] == ["t-match"]

    entry = canon.load(work_root)["entries"][0]
    assert entry["occurrences"] == ["web:a", "web:b"]
    assert entry["translations"]["de"]["value"] == "Übereinstimmung"
    assert entry["translations"]["de"]["current"] == ["Treffer", "Übereinstimmung", "Match"]


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


# --- ui_label / dnt coherence (#1016) ---------------------------------------


def make_message(msg_id, source):
    return {
        "id": msg_id, "source": source, "targets": {"ru": None},
        "context": {"file": "app.json", "key": msg_id},
    }


def write_messages(root, messages):
    (Path(root) / "messages.json").write_text(
        json.dumps({"schema": 1, "files": ["app.json"], "messages": messages}), encoding="utf-8",
    )


def coherence_canon(value="Установить расширение", approve_label=True, approve_dnt=True, locale="ru"):
    label = make_entry(
        "u-add", kind="ui_label", source="Add to BrowserX",
        translations={locale: approved(value) if approve_label else proposed(value)},
    )
    dnt = make_entry("d-brand", kind="dnt", source="BrowserX", occurrences=["app:install"])
    if approve_dnt:
        dnt["approved_by"] = "alice"
        dnt["approved_at"] = "2026-01-01T00:00:00Z"
    return {"schema": 1, "entries": [label, dnt]}


def test_freeze_refuses_a_ui_label_that_drops_an_approved_dnt(work_root, capsys):
    write_messages(work_root, [make_message("app:install", "Add to BrowserX")])
    canon.save(work_root, coherence_canon())
    lock_path = work_root / "canon.lock.json"
    lock_path.write_text('{"schema": 1, "entries": []}\n', encoding="utf-8")
    before = lock_path.read_bytes()

    with pytest.raises(SystemExit) as exc:
        canon.freeze(work_root)

    assert exc.value.code == lz_common.EXIT_FAIL
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    conflict = payload["conflicts"][0]
    assert conflict["locale"] == "ru" and conflict["ui_label"] == "u-add"
    assert conflict["dnt"] == "d-brand" and conflict["occurrence"] == "app:install"
    assert conflict["value"] == "Установить расширение"
    assert "'BrowserX'" in conflict["detail"] and "d-brand" in conflict["detail"]
    assert lock_path.read_bytes() == before


def test_cli_freeze_refuses_the_conflict_and_writes_no_lock(work_root):
    write_messages(work_root, [make_message("app:install", "Add to BrowserX")])
    canon.save(work_root, coherence_canon())

    code, result = run_cli(["freeze", "--root", str(work_root)])

    assert code == 1 and result["ok"] is False and result["conflicts"][0]["dnt"] == "d-brand"
    assert not (work_root / "canon.lock.json").exists()


def test_freeze_accepts_a_translation_that_contains_the_brand(work_root):
    write_messages(work_root, [make_message("app:install", "Add to BrowserX")])
    canon.save(work_root, coherence_canon(value="Добавить в BrowserX"))

    lock = canon.freeze(work_root)

    assert {e["id"] for e in lock["entries"]} == {"u-add", "d-brand"}
    assert canon.load_lock(work_root) == lock
    # Producer/consumer: the frozen lock feeds the real candidate checks.
    message = make_message("app:install", "Add to BrowserX")
    problems = checks.check_candidate(
        message, "ru", "Добавить в BrowserX", {"ok": True, "tokens": []}, {"ok": True, "tokens": []},
        lock, {"allow_identical": []},
    )
    assert problems == []


def test_freeze_does_not_treat_a_longer_message_mentioning_the_label_as_the_label(work_root):
    source = "Click Add to BrowserX to continue"
    write_messages(work_root, [make_message("app:install", source)])
    canon.save(work_root, coherence_canon())

    lock = canon.freeze(work_root)

    assert len(lock["entries"]) == 2
    # The full translation keeps the brand outside the translated label.
    problems = checks.check_candidate(
        make_message("app:install", source), "ru", "Нажмите «Установить расширение» в BrowserX, чтобы продолжить",
        {"ok": True, "tokens": []}, {"ok": True, "tokens": []}, lock, {"allow_identical": []},
    )
    assert problems == []


def test_freeze_refuses_a_malformed_snapshot_and_keeps_the_lock(work_root, capsys):
    (work_root / "messages.json").write_text('{"schema": 1, "files": [], "messages": "nope"}', encoding="utf-8")
    canon.save(work_root, coherence_canon())
    lock_path = work_root / "canon.lock.json"
    lock_path.write_text('{"schema": 1, "entries": []}\n', encoding="utf-8")
    before = lock_path.read_bytes()

    with pytest.raises(SystemExit) as exc:
        canon.freeze(work_root)

    assert exc.value.code == lz_common.EXIT_CANNOT
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["error"] == "messages.json is invalid: messages must be a list"
    assert lock_path.read_bytes() == before


def test_freeze_skips_a_plural_message_with_the_label_source(work_root):
    message = {
        "id": "app:install", "source": {"forms": ["Add to BrowserX", "Add to BrowserX"]},
        "plural": {
            "source_labels": [{"label": "one", "exact": False}, {"label": "other", "exact": False}],
            "general_index": 1, "count_arguments": ["n"],
            "target_labels": {"ru": [{"label": "other", "exact": False}]},
        },
        "targets": {"ru": None}, "context": {"file": "app.json", "key": "app:install"},
    }
    write_messages(work_root, [message])
    canon.save(work_root, coherence_canon())

    assert len(canon.freeze(work_root)["entries"]) == 2


@pytest.mark.parametrize("kwargs", [{"approve_label": False}, {"approve_dnt": False}])
def test_freeze_ignores_an_unapproved_label_or_dnt(work_root, kwargs):
    write_messages(work_root, [make_message("app:install", "Add to BrowserX")])
    canon.save(work_root, coherence_canon(**kwargs))

    canon.freeze(work_root)


def test_freeze_checks_a_label_locale_independently_of_other_locales(work_root):
    write_messages(work_root, [make_message("app:install", "Add to BrowserX")])
    canon_data = coherence_canon(value="Добавить в BrowserX")
    canon_data["entries"][0]["translations"]["de"] = approved("Erweiterung installieren")
    canon.save(work_root, canon_data)

    with pytest.raises(SystemExit):
        canon.freeze(work_root)


def test_freeze_ignores_a_dnt_occurrence_absent_from_the_snapshot(work_root):
    write_messages(work_root, [make_message("app:other", "Add to BrowserX")])
    canon.save(work_root, coherence_canon())

    assert len(canon.freeze(work_root)["entries"]) == 2


def test_freeze_needs_the_snapshot_when_a_label_and_a_dnt_both_lock(work_root):
    canon.save(work_root, coherence_canon())

    with pytest.raises(SystemExit) as exc:
        canon.freeze(work_root)

    assert exc.value.code == lz_common.EXIT_CANNOT
    assert not (work_root / "canon.lock.json").exists()


def test_freeze_and_load_lock_need_no_snapshot_for_an_empty_scope_dnt(work_root):
    canon_data = coherence_canon()
    canon_data["entries"][1]["occurrences"] = []
    canon.save(work_root, canon_data)

    lock = canon.freeze(work_root)

    assert {e["id"] for e in lock["entries"]} == {"u-add", "d-brand"}
    assert not (work_root / "messages.json").exists()
    assert canon.load_lock(work_root) == lock


def test_freeze_without_both_kinds_does_not_read_the_snapshot(work_root):
    dnt_only = coherence_canon()
    dnt_only["entries"] = dnt_only["entries"][1:]
    canon.save(work_root, dnt_only)

    assert len(canon.freeze(work_root)["entries"]) == 1


def test_load_lock_refuses_an_already_frozen_conflict(work_root, capsys):
    write_messages(work_root, [make_message("app:install", "Add to BrowserX")])
    lock = canon._build_lock(coherence_canon())
    (work_root / "canon.lock.json").write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        canon.load_lock(work_root)

    assert exc.value.code == lz_common.EXIT_FAIL
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["conflicts"][0]["occurrence"] == "app:install"


def test_change_refusal_leaves_object_canon_lock_and_history_unchanged(work_root):
    write_messages(work_root, [make_message("app:install", "Add to BrowserX")])
    canon_data = coherence_canon(value="Добавить в BrowserX")
    canon.save(work_root, canon_data)
    canon.freeze(work_root)
    canon_bytes = (work_root / "canon.json").read_bytes()
    lock_bytes = (work_root / "canon.lock.json").read_bytes()
    snapshot = json.loads(json.dumps(canon_data))

    with pytest.raises(SystemExit) as exc:
        canon.change(
            work_root, canon_data, "u-add", "ru", "Добавить в BrowserX",
            "Установить расширение", "reword", "bob",
        )

    assert exc.value.code == lz_common.EXIT_FAIL
    assert canon_data == snapshot
    assert (work_root / "canon.json").read_bytes() == canon_bytes
    assert (work_root / "canon.lock.json").read_bytes() == lock_bytes
    assert canon.load_history(work_root) == {"schema": 1, "events": []}
    assert not (work_root / "runs").exists()


def conflicted_two_locale_root(root):
    write_messages(root, [make_message("app:install", "Add to BrowserX")])
    canon_data = coherence_canon()
    canon_data["entries"][0]["translations"]["de"] = approved("Erweiterung installieren")
    canon.save(root, canon_data)
    # A legacy lock written before the coherence check existed.
    (root / "canon.lock.json").write_text(json.dumps(canon._build_lock(canon_data)), encoding="utf-8")


def change_args(root, locale, expect, value, reason, entry="u-add"):
    return [
        "change", "--root", str(root), "--entry", entry, "--locale", locale,
        "--expect", expect, "--value", value, "--reason", reason, "--by", "bob",
    ]


def test_cli_change_repairs_two_conflicting_locales_one_at_a_time(work_root):
    conflicted_two_locale_root(work_root)
    old_lock = (work_root / "canon.lock.json").read_bytes()

    code, first = run_cli(change_args(
        work_root, "ru", "Установить расширение", "Добавить в BrowserX", "keep the brand",
    ))

    assert code == 0 and first["ok"] is True and first["frozen"] is False
    assert [(c["ui_label"], c["locale"], c["dnt"]) for c in first["conflicts"]] == [("u-add", "de", "d-brand")]
    assert canon.load(work_root)["entries"][0]["translations"]["ru"]["value"] == "Добавить в BrowserX"
    events = canon.load_history(work_root)["events"]
    assert [(e["action"], e["locale"], e["by"], e["reason"]) for e in events] == [
        ("change", "ru", "bob", "keep the brand"),
    ]
    assert (work_root / "canon.lock.json").read_bytes() == old_lock
    with pytest.raises(SystemExit):
        canon.load_lock(work_root)
    assert (work_root / "runs" / "ru" / "canon-audit-u-add.json").is_file()

    code, second = run_cli(change_args(
        work_root, "de", "Erweiterung installieren", "BrowserX-Erweiterung installieren", "keep the brand",
    ))

    assert code == 0 and second["frozen"] is True and second["conflicts"] == []
    lock = canon.load_lock(work_root)
    values = {loc: t["value"] for loc, t in lock["entries"][0]["translations"].items()}
    assert values == {"ru": "Добавить в BrowserX", "de": "BrowserX-Erweiterung installieren"}
    assert [e["locale"] for e in canon.load_history(work_root)["events"]] == ["ru", "de"]
    assert (work_root / "runs" / "ru" / "canon-audit-u-add.json").is_file()
    assert (work_root / "runs" / "de" / "canon-audit-u-add.json").is_file()


def test_cli_change_still_refuses_an_invalid_value_while_others_conflict(work_root):
    conflicted_two_locale_root(work_root)
    old_lock = (work_root / "canon.lock.json").read_bytes()
    old_canon = (work_root / "canon.json").read_bytes()

    code, result = run_cli(change_args(work_root, "ru", "Установить расширение", "Установить", "still bad"))

    assert code == 1 and result["ok"] is False
    assert [(c["locale"], c["ui_label"]) for c in result["conflicts"]] == [("ru", "u-add")]
    assert (work_root / "canon.json").read_bytes() == old_canon
    assert (work_root / "canon.lock.json").read_bytes() == old_lock
    assert canon.load_history(work_root) == {"schema": 1, "events": []}
    assert not (work_root / "runs").exists()


def test_cli_change_repairs_two_distinct_conflicting_labels(work_root):
    write_messages(work_root, [
        make_message("app:install", "Add to BrowserX"), make_message("app:open", "Open BrowserX"),
    ])
    canon_data = coherence_canon()
    canon_data["entries"][1]["occurrences"] = ["app:install", "app:open"]
    canon_data["entries"].append(make_entry(
        "u-open", kind="ui_label", source="Open BrowserX", translations={"ru": approved("Открыть")},
    ))
    canon.save(work_root, canon_data)
    (work_root / "canon.lock.json").write_text(json.dumps(canon._build_lock(canon_data)), encoding="utf-8")

    code, first = run_cli(change_args(work_root, "ru", "Установить расширение", "Добавить в BrowserX", "r1"))

    assert code == 0 and first["frozen"] is False
    assert [(c["ui_label"], c["occurrence"]) for c in first["conflicts"]] == [("u-open", "app:open")]

    code, second = run_cli(change_args(work_root, "ru", "Открыть", "Открыть BrowserX", "r2", entry="u-open"))

    assert code == 0 and second["frozen"] is True
    assert len(canon.load_lock(work_root)["entries"]) == 3
