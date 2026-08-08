"""tests/claim_selector.test.py -- tests for select_segments.py's #438 claim
admission gate (PLAN.md D1-D6, D9, D10; POPULATIONS.md P1/P2).

Every test builds an isolated durable_root (the REAL select_segments.py,
ledger_merge.py, draft_ready.py, validate_draft.py, claim_record.py, plus the
REAL assets/schemas/*.schema.json, copied into {root}/scripts/ and
{root}/schemas/ -- the same `make_durable_root` convention
tests/select_segments.test.py already uses) and invokes the ACTUAL
select_segments.py as a subprocess, exactly as production does. S1/S2 run
for REAL (validate_draft.py / draft_ready.py are not stubbed) against a
minimal-but-genuinely-valid draft/segpack pair -- only cache_key.py is
stubbed (same fixture stub tests/select_segments.test.py and
tests/ledger_merge.test.py already use), since its own 15-field hashing
algorithm has its own dedicated test file.

Covers: both profiles against fixtures with the REAL shape of each
population (P1 = vol2-style hand-edited-after-clean-convergence, P2 =
tome1-style hand-edited-after-cap); a segment refused BY NAME under the
wrong profile; --run-id absent is FATAL; the one-pass multi-failure report;
D6 refusing on each of the three difference kinds (newly present, newly
absent, differing form) under BOTH profiles, and admitting again once the
segpack is regenerated; the D5 clearance covering exactly its own
successfully-admitted ids; the D5.3 overlap rejection with
--allow-retranslate-converged; a colon-bearing segment id
(FRONTBACK:errata_02) end to end; and the claim record's own idempotent
re-claim. Also #438 round 4: rewrite_draft_dispatch_token()'s refusal of an
OLD run reasserting a SUPERSEDED authorization over a segment a DIFFERENT
run now owns, unit-level (all four predicate conditions, isolated) and end
to end (the actual defect -- claim, re-claim by another run, then the first
run resumes and reclaims -- and the fresh-claim-over-a-live-foreign-record
case a naive predicate would have broken instead).
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

SELECT_SCRIPT_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
DRAFT_READY_SRC = SCRIPTS_SRC_DIR / "draft_ready.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
CLAIM_RECORD_SRC = SCRIPTS_SRC_DIR / "claim_record.py"

for _src in (SELECT_SCRIPT_SRC, LEDGER_MERGE_SRC, DRAFT_READY_SRC, VALIDATE_DRAFT_SRC, CLAIM_RECORD_SRC):
    assert _src.is_file(), f"required sibling script not found at {_src}"
assert SCHEMAS_SRC.is_dir(), f"schemas dir not found at {SCHEMAS_SRC}"

CACHE_KEY_FIELDS = [
    "input_sha1",
    "style_contract_hash",
    "used_terms_hash",
    "pipeline_version",
    "schema_hash",
    "prompt_hash",
    "agent_config_hash",
    "profile_semantics_hash",
    "particle_config_hash",
    "source_extraction_hash",
    "source_input_hash",
    "derivation_bundle_hash",
    "verse_map_hash",
    "note_map_hash",
    "plugin_bundle_hash",
]

# Same fixture stand-in for cache_key.py that tests/select_segments.test.py
# and tests/ledger_merge.test.py already use -- the real `--seg <id>` -> JSON
# interface, sourced from a test-controlled lookup file. select_segments.py
# AND ledger_merge.py both shell out to this exact interface, and this
# suite's own D6 cache-key-diff reporting does too.
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    parser.add_argument("--field")
    parser.add_argument("--durable-root", default=None)
    args = parser.parse_args()
    if args.durable_root:
        durable_root = Path(args.durable_root).resolve()
    else:
        durable_root = Path(__file__).resolve().parent.parent
    keys_path = durable_root / "test_fixture_cache_keys.json"
    if not args.seg:
        sys.stderr.write("fake cache_key.py: test stub requires --seg\\n")
        return 1
    data = json.loads(keys_path.read_text(encoding="utf-8"))
    if args.seg not in data:
        sys.stderr.write(f"fake cache_key.py: no fixture key for {args.seg}\\n")
        return 1
    print(json.dumps(data[args.seg]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

# A minimal but genuinely valid profile.yml -- validate_draft.py's own
# load_profile()/ProfileConfig requires all three sections. Verbatim copy of
# tests/validate_draft.test.py's DEFAULT_PROFILE (a proven-good fixture),
# duplicated here per this suite's own house convention of self-contained
# test files rather than cross-file imports.
DEFAULT_PROFILE = {
    "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}

FN_PH = "⟦FNREF_1⟧"
V_PH_A = "⟦VERSE_vA⟧"
V_PH_B = "⟦VERSE_vB⟧"


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path):
    """Isolated durable_root carrying every REAL sibling select_segments.py's
    claim gate shells out to (validate_draft.py, draft_ready.py) or imports
    (claim_record.py), the REAL ledger_merge.py, the REAL schemas, and a
    stubbed cache_key.py -- plus profile.yml and an empty canon.json."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name, src in (
        ("select_segments.py", SELECT_SCRIPT_SRC),
        ("ledger_merge.py", LEDGER_MERGE_SRC),
        ("draft_ready.py", DRAFT_READY_SRC),
        ("validate_draft.py", VALIDATE_DRAFT_SRC),
        ("claim_record.py", CLAIM_RECORD_SRC),
    ):
        shutil.copy2(src, scripts_dir / name)
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    shutil.copytree(SCHEMAS_SRC, root / "schemas")

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    profile_path = root / "profile.yml"
    profile_path.write_text(yaml.safe_dump(DEFAULT_PROFILE, sort_keys=False), encoding="utf-8")
    # validate_draft.py's load_profile() refuses without this ownership
    # marker (Step 0a's own artifact) -- see tests/validate_draft.test.py's
    # own make_durable_root() for the identical shape.
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )
    write_canon(root, {})
    return root


def write_manifest(root, seg_ids):
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": s} for s in seg_ids]}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_fragment(root, seg, record):
    frag_path = root / "runs" / "ledger.d" / f"{seg}.json"
    frag_path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def write_fixture_cache_keys(root, mapping):
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
    )


def write_canon(root, entries):
    (root / "canon.json").write_text(
        json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8"
    )


def make_cache_key(seed):
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def with_field(key, field, value):
    d = dict(key)
    d[field] = value
    return d


def draft_content_sha1_of(doc: dict) -> str:
    """Independent ground-truth reimplementation of draft_content_sha1()
    (projects out dispatch_token, sorted-key compact-separator canonical
    JSON) -- an oracle, deliberately not pinned against production, per
    PLAN.md D4's own inventory of the seven inline copies plus these test
    reimplementations."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    raw = json.dumps(projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def clean_segpack(seg):
    """One prose block with a footnote anchor, two standalone verses each
    parented to their own block -- the same proven-valid shape
    tests/validate_draft.test.py's own clean_segpack() uses (verbatim,
    parameterized by seg), so S1 (validate_draft.py) genuinely passes."""
    return {
        "seg": seg,
        "blocks": [
            {"id": "p1", "order_index": 0, "source_html": f"<p>Some prose with a note {FN_PH} attached.</p>"},
            {"id": "vblockA", "order_index": 1, "source_html": "<p>Premiere ligne<br/>Deuxieme ligne</p>"},
            {"id": "vblockB", "order_index": 2, "source_html": "<p>Autre premiere<br/>Autre deuxieme</p>"},
        ],
        "footnotes": [{"n": 1, "source_text": "Une note en francais."}],
        "verses": [
            {"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"},
            {"vid": "vB", "placeholder": V_PH_B, "parent_block": "vblockB"},
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
        "canon_map": {},
        "generation_hashes": {
            "source_extraction_hash": "sxh-0",
            "source_input_hash": "sih-0",
            "particle_config_hash": "pch-0",
            "derivation_bundle_hash": "dbh-0",
        },
    }


def clean_draft(seg):
    return {
        "seg": seg,
        "blocks": {
            "p1": f"Some translated prose with a note {FN_PH} attached.",
            "vblockA": V_PH_A,
            "vblockB": V_PH_B,
        },
        "footnotes": {"1": "A translated note in English."},
        "verses": {
            "vA": {
                "rendered": "First line rendered so\nSecond line rendered so",
                "literal_gloss": "The first line means one thing, the second means another",
            },
            "vB": {
                "rendered": "Another line rendered here\nAnother second line here",
                "literal_gloss": "This gloss says something different from the rendering above",
            },
        },
        "names": [],
        "notes": [],
    }


def write_segpack(root, seg, segpack):
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )


def write_draft_doc(root, seg, draft):
    (root / "segments" / f"{seg}.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )


def write_review(root, seg, review):
    (root / "segments" / f"{seg}.review.json").write_text(
        json.dumps(review, ensure_ascii=False), encoding="utf-8"
    )


def mark_ever_converged(root, seg):
    (root / "segments" / f".ever_converged.{seg}").write_text("converged\n", encoding="utf-8")


def make_run_dir(root, run_id):
    """The SOURCE run's own runs/<run_id>/ directory -- S3 requires it to
    exist. Also writes a stub input.digest so the UNRELATED #409 Step 3
    resume-integrity gate (which scans every draft's dispatch_token,
    project-wide, regardless of `segs`) does not fatal every fixture whose
    draft happens to carry a dispatch_token from a "prior" run -- that gate
    is not this suite's subject, so it must stay silently satisfied."""
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    digest_path = run_dir / "input.digest"
    if not digest_path.exists():
        digest_path.write_text(json.dumps({"digest": f"stub-{run_id}"}), encoding="utf-8")


def converged_fragment(cache_key, reviewed_draft_sha1, rounds=1):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": rounds,
        "cache_key": cache_key,
        "n_blocks": 3,
        "n_footnotes": 1,
        "n_verses": 2,
        "reviewed_draft_sha1": reviewed_draft_sha1,
    }


def non_converged_fragment(reason="cap", rounds=4):
    return {"timestamp": "2026-01-01T00:00:00Z", "status": "non_converged", "reason": reason, "rounds": rounds}


def run_select(root, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "select_segments.py"), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


# ---------------------------------------------------------------------------
# Population builders -- P1 (--from-converged) and P2 (--from-cap) shapes.
# ---------------------------------------------------------------------------

RUN_ID = "20260810T000000Z"
SOURCE_RUN_ID = "20260801T090000Z"
# A second, later, distinct claiming run -- #438 round 4's own two-owners
# scenario (sections 17/18): RUN_ID claims, OTHER_RUN_ID legitimately
# re-claims, RUN_ID resumes and must not be able to reclaim it back.
OTHER_RUN_ID = "20260811T000000Z"


def build_from_converged_segment(
    root,
    seg,
    fixture_keys: dict,
    *,
    source_run_id=SOURCE_RUN_ID,
    hand_edit=True,
    sentinel_present=True,
    ledger_status="converged",
    review_overrides=None,
    fragment_cache_key_overrides=None,
    names=None,
    canon_map=None,
    canon_entries=None,
    source_run_dir=True,
):
    """P1 shape (POPULATIONS.md): converged (or stale) at least once,
    sentinel present, reviewed_draft_sha1 present and (by default) diverged
    from a hand edit, stored review clean:true. Every keyword lets one test
    flip exactly one axis away from the admitting default."""
    names = names if names is not None else []
    canon_map = canon_map if canon_map is not None else {}

    segpack = clean_segpack(seg)
    segpack["names"] = names
    segpack["canon_map"] = dict(canon_map)
    write_segpack(root, seg, segpack)

    write_canon(root, canon_entries if canon_entries is not None else {})

    converged_draft = clean_draft(seg)
    converged_sha1 = draft_content_sha1_of(converged_draft)

    if hand_edit:
        current_draft = dict(converged_draft)
        current_draft["blocks"] = dict(converged_draft["blocks"])
        current_draft["blocks"]["p1"] = converged_draft["blocks"]["p1"] + " Hand-edited by the operator."
    else:
        current_draft = dict(converged_draft)
    current_draft["dispatch_token"] = f"{source_run_id}:{seg}"
    write_draft_doc(root, seg, current_draft)

    if source_run_dir:
        make_run_dir(root, source_run_id)

    ck = make_cache_key(seg)
    fixture_keys[seg] = ck

    review = {"clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "0" * 40}
    if review_overrides:
        review.update(review_overrides)
    write_review(root, seg, review)

    if sentinel_present:
        mark_ever_converged(root, seg)

    frag_cache_key = dict(ck)
    if fragment_cache_key_overrides:
        frag_cache_key.update(fragment_cache_key_overrides)
    write_fragment(root, seg, {**converged_fragment(frag_cache_key, converged_sha1), "status": ledger_status})

    return {"converged_sha1": converged_sha1}


def build_from_cap_segment(
    root,
    seg,
    fixture_keys: dict,
    *,
    source_run_id=SOURCE_RUN_ID,
    sentinel_present=False,
    ledger_status="non_converged",
    ledger_reason="cap",
    review_overrides=None,
    names=None,
    canon_map=None,
    canon_entries=None,
    source_run_dir=True,
):
    """P2 shape (POPULATIONS.md): non_converged/reason=cap, NO sentinel, no
    cache_key on the fragment at all, stored review clean:false with
    findings. human_escalation -> reachable only via --only-segs."""
    names = names if names is not None else []
    canon_map = canon_map if canon_map is not None else {}

    segpack = clean_segpack(seg)
    segpack["names"] = names
    segpack["canon_map"] = dict(canon_map)
    write_segpack(root, seg, segpack)

    write_canon(root, canon_entries if canon_entries is not None else {})

    draft = clean_draft(seg)
    draft["blocks"] = dict(draft["blocks"])
    draft["blocks"]["p1"] = draft["blocks"]["p1"] + " Hand-fixed after the cap."
    draft["dispatch_token"] = f"{source_run_id}:{seg}"
    write_draft_doc(root, seg, draft)

    if source_run_dir:
        make_run_dir(root, source_run_id)

    ck = make_cache_key(seg)
    fixture_keys[seg] = ck

    review = {
        "clean": False,
        "coverage_ok": True,
        "findings": [{"loc": "p1", "severity": "medium", "issue": "awkward phrasing", "suggest": "rephrase"}],
        "draft_sha1": "0" * 40,
    }
    if review_overrides:
        review.update(review_overrides)
    write_review(root, seg, review)

    if sentinel_present:
        mark_ever_converged(root, seg)

    frag = {"timestamp": "2026-01-01T00:00:00Z", "status": ledger_status, "reason": ledger_reason, "rounds": 4}
    write_fragment(root, seg, frag)


# ---------------------------------------------------------------------------
# 1. Happy path, both profiles.
# ---------------------------------------------------------------------------

def test_from_converged_happy_path_claims_and_clears_previously_converged(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)

    assert seg in out["segs"]
    assert seg not in out["previously_converged"], (
        "D5.2: a successfully-admitted --from-converged claim must clear the gate for itself"
    )
    assert seg in out["claims"]
    claim = out["claims"][seg]
    assert claim["profile"] == "from-converged"
    assert claim["run_id"] == RUN_ID
    assert claim["source_run_id"] == SOURCE_RUN_ID
    assert claim["previous_dispatch_token"] == f"{SOURCE_RUN_ID}:{seg}"
    assert claim["cache_key_at_claim"] == fixture_keys[seg]
    assert claim["pre_claim_review"]["clean"] is True

    marker = root / "runs" / RUN_ID / f".claimed.{seg}"
    assert marker.is_file(), "the durable claim record must actually be written to disk"
    on_disk = json.loads(marker.read_text(encoding="utf-8"))
    assert on_disk["profile"] == "from-converged"
    assert on_disk["run_id"] == RUN_ID
    # The reported authorization and the durable one are the SAME object,
    # not two renderings of it. The reporting spread this replaced put four
    # freshly recomputed fields on the JSON that the marker file did not
    # carry -- so an operator reading the marker after the fact and a
    # consumer reading this JSON could see different evidence for the same
    # claim.
    assert claim == on_disk, (
        "the `claims` entry must be exactly the record on disk, field for field"
    )
    # And the D6/D10 evidence is IN the record, not merely alongside it.
    for field in (
        "pre_claim_review",
        "pre_claim_cache_key",
        "cache_key_at_claim",
        "cache_key_moved_fields",
        "cache_key_movement_machinery_only",
        "cache_key_note",
    ):
        assert field in on_disk, f"{field!r} must be written to the marker file itself"


def test_from_cap_happy_path_requires_only_segs_and_claims(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg14"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    # human_escalation -- unreachable without --only-segs.
    bare = run_select(root, "--allow-empty")
    assert bare.returncode == 0
    assert seg not in parse_stdout(bare)["segs"], (
        "precondition: a --from-cap population id must NOT be default-eligible"
    )

    proc = run_select(root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert seg in out["segs"]
    assert seg in out["claims"]
    assert out["claims"][seg]["profile"] == "from-cap"
    assert out["claims"][seg]["cache_key_note"] is not None, (
        "D6: --from-cap has no historical cache_key baseline to diff against; recorded as a note"
    )
    # The trap the note exists to disarm, pinned: an empty moved-fields list
    # means EITHER "nothing moved" OR "there was no baseline to diff", and
    # `pre_claim_cache_key is None` is the field that tells them apart.
    assert out["claims"][seg]["pre_claim_cache_key"] is None, (
        "a --from-cap fragment carries no cache_key, so the pre-claim endpoint must be "
        "null -- not an empty dict, which would read as 'a baseline existed and was empty'"
    )
    assert out["claims"][seg]["cache_key_moved_fields"] == []
    assert out["claims"][seg]["cache_key_movement_machinery_only"] is None, (
        "tri-state: None means 'no movement to characterise', a different fact from False"
    )
    assert out["claims"][seg]["cache_key_at_claim"] == fixture_keys[seg]


# ---------------------------------------------------------------------------
# 2. Wrong profile, refused BY NAME, in both directions.
# ---------------------------------------------------------------------------

def test_p1_shaped_segment_refused_by_name_under_from_cap(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert out["success"] is False
    assert seg in out["error"] and "--from-cap" in out["error"]
    assert "non_converged" in out["error"] or "sentinel" in out["error"]


def test_p2_shaped_segment_refused_by_name_under_from_converged(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg14"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--only-segs", seg, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert out["success"] is False
    assert seg in out["error"] and "--from-converged" in out["error"]


# ---------------------------------------------------------------------------
# 3. --run-id absent is FATAL, never a silent "unclaimed".
# ---------------------------------------------------------------------------

def test_run_id_absent_is_fatal_not_silently_unclaimed(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg)
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert out["success"] is False
    assert "--run-id" in out["error"]
    assert not (root / "runs" / RUN_ID).exists(), (
        "no claim record must be written when --run-id was never given"
    )


# ---------------------------------------------------------------------------
# 4. One-pass, every failure reported together.
# ---------------------------------------------------------------------------

def test_multiple_claim_failures_reported_in_one_pass(tmp_path):
    root = make_durable_root(tmp_path)
    fixture_keys = {}

    seg_a = "seg22"  # will fail: requested --from-converged but no sentinel
    build_from_converged_segment(root, seg_a, fixture_keys, sentinel_present=False)

    seg_b = "seg14"  # will fail: requested --from-cap but review is clean
    build_from_cap_segment(root, seg_b, fixture_keys, review_overrides={"clean": True, "findings": []})

    write_manifest(root, [seg_a, seg_b])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(
        root,
        "--only-segs", f"{seg_a},{seg_b}",
        "--from-converged", seg_a,
        "--from-cap", seg_b,
        "--run-id", RUN_ID, "--run-resume", "false",
    )
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert out["success"] is False
    # Both failures must be visible in the SAME refusal -- never require two
    # round trips to learn two problems (D2).
    assert seg_a in out["error"] and seg_b in out["error"]
    assert seg_a in out["claim_failures"] and seg_b in out["claim_failures"]
    assert not (root / "runs" / RUN_ID / f".claimed.{seg_a}").exists(), (
        "a failed batch must write NOTHING, including for the id that failed for a different reason"
    )
    assert not (root / "runs" / RUN_ID / f".claimed.{seg_b}").exists()


# ---------------------------------------------------------------------------
# 5. D6 -- fresh-segpack precondition, all three difference kinds, both
#    profiles, then admits again once the segpack is regenerated.
# ---------------------------------------------------------------------------

def test_d6_refuses_on_differing_target_form(tmp_path):
    """The live seg18 shape: segpack has a frozen "Mistress Adil", current
    canon.json now says "Mrs. Adil"."""
    root = make_durable_root(tmp_path)
    seg = "seg18"
    fixture_keys = {}
    name = "מרת אדיל"  # מרת אדיל
    build_from_converged_segment(
        root, seg, fixture_keys,
        names=[name],
        canon_map={name: "Mistress Adil"},
        canon_entries={name: {"canonical_target_form": "Mrs. Adil"}},
    )
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert "D6" in out["error"]
    assert "Mistress Adil" in out["error"] and "Mrs. Adil" in out["error"]
    assert not (root / "runs" / RUN_ID / f".claimed.{seg}").exists()


def test_d6_refuses_on_newly_present_target(tmp_path):
    """A name with NO canon_map entry in the segpack (stored None), but the
    CURRENT canon now supplies one -- the case an implementation that only
    intersects canon_map's own keys would miss (codex round 2)."""
    root = make_durable_root(tmp_path)
    seg = "seg18"
    fixture_keys = {}
    name = "מרת פיגא"  # מרת פיגא
    build_from_converged_segment(
        root, seg, fixture_keys,
        names=[name],
        canon_map={},  # segpack never mapped it
        canon_entries={name: {"canonical_target_form": "Mrs. Feiga"}},
    )
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert "D6" in out["error"]
    assert "Mrs. Feiga" in out["error"]


def test_d6_refuses_on_newly_absent_target(tmp_path):
    """The segpack's frozen canon_map has a target; the current canon.json
    no longer has an entry for that name at all."""
    root = make_durable_root(tmp_path)
    seg = "seg18"
    fixture_keys = {}
    name = "מרת אדיל"
    build_from_converged_segment(
        root, seg, fixture_keys,
        names=[name],
        canon_map={name: "Mistress Adil"},
        canon_entries={},  # current canon dropped the entry entirely
    )
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert "D6" in out["error"]
    assert "Mistress Adil" in out["error"]


def test_d6_admits_again_once_segpack_regenerated(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg18"
    fixture_keys = {}
    name = "מרת אדיל"
    build_from_converged_segment(
        root, seg, fixture_keys,
        names=[name],
        canon_map={name: "Mrs. Adil"},  # already matches -- "regenerated"
        canon_entries={name: {"canonical_target_form": "Mrs. Adil"}},
    )
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert seg in out["claims"]


def test_d6_applies_under_from_cap_too(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg14"
    fixture_keys = {}
    name = "מרת אדיל"
    build_from_cap_segment(
        root, seg, fixture_keys,
        names=[name],
        canon_map={name: "Mistress Adil"},
        canon_entries={name: {"canonical_target_form": "Mrs. Adil"}},
    )
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert "D6" in out["error"]


# ---------------------------------------------------------------------------
# 6. D5.2 -- clearance covers exactly its own successfully-admitted ids.
# ---------------------------------------------------------------------------

def test_clearance_covers_only_the_claimed_id_not_a_sibling(tmp_path):
    root = make_durable_root(tmp_path)
    fixture_keys = {}

    claimed_seg = "seg22"
    build_from_converged_segment(root, claimed_seg, fixture_keys)

    # A SIBLING previously-converged segment that is NOT claimed at all --
    # ordinary stale-by-cache-key-drift, sentinel present, draft untouched.
    sibling_seg = "seg26"
    sibling_key = make_cache_key(sibling_seg)
    fixture_keys[sibling_seg] = sibling_key
    sibling_draft = clean_draft(sibling_seg)
    sibling_sha1 = draft_content_sha1_of(sibling_draft)
    sibling_draft["dispatch_token"] = f"{SOURCE_RUN_ID}:{sibling_seg}"
    write_segpack(root, sibling_seg, clean_segpack(sibling_seg))
    write_draft_doc(root, sibling_seg, sibling_draft)
    stored_key = with_field(sibling_key, "style_contract_hash", "style_contract_hash-OLD")
    write_fragment(root, sibling_seg, converged_fragment(stored_key, sibling_sha1))
    mark_ever_converged(root, sibling_seg)

    write_manifest(root, [claimed_seg, sibling_seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", claimed_seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, (
        "the sibling is still previously_converged and was never claimed or authorized -- "
        "the whole invocation must still refuse"
    )
    out = parse_stdout(proc)
    assert claimed_seg not in out["previously_converged"], (
        "D5.2: the successfully-admitted id must be cleared even though the WHOLE run refuses "
        "on its sibling"
    )
    assert sibling_seg in out["previously_converged"]
    assert sibling_seg in out["error"]
    assert claimed_seg not in out["error"].split("Refusing")[0] or claimed_seg not in (
        out.get("previously_converged") or []
    )


# ---------------------------------------------------------------------------
# 7. D5.3 -- overlap with --allow-retranslate-converged is REJECTED
#    OUTRIGHT, not resolved by precedence.
# ---------------------------------------------------------------------------

def test_overlap_with_allow_retranslate_converged_is_rejected_outright(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(
        root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false", "--allow-retranslate-converged"
    )
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert out["success"] is False
    assert seg in out["error"]
    assert "Rejected outright" in out["error"] or "rejected outright" in out["error"].lower()
    assert not (root / "runs" / RUN_ID / f".claimed.{seg}").exists(), (
        "an overlap must write NOTHING -- it is not resolved by precedence in either direction"
    )


# ---------------------------------------------------------------------------
# 8. Colon-bearing segment id, end to end, under --from-cap (P3's own shape
#    reused as a --from-cap population member -- colons are real and reach
#    filenames: runs/ledger.d/FRONTBACK:errata_02.json already exists on
#    disk in production).
# ---------------------------------------------------------------------------

def test_colon_bearing_segment_id_end_to_end(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "FRONTBACK:errata_02"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert seg in out["claims"]

    marker = root / "runs" / RUN_ID / f".claimed.{seg}"
    assert marker.is_file(), f"expected a claim record at {marker}"
    on_disk = json.loads(marker.read_text(encoding="utf-8"))
    assert on_disk["seg"] == seg


# ---------------------------------------------------------------------------
# 9. Malformed / duplicate / non-subset authorization.
# ---------------------------------------------------------------------------

def test_id_named_under_both_profiles_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(
        root, "--from-converged", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false"
    )
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert seg in out["error"]
    assert "--from-converged" in out["error"] and "--from-cap" in out["error"]


def test_claim_id_not_in_emitted_segs_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg14"  # human_escalation -- not in default segs, and --only-segs is not passed
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--allow-empty", "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert seg in out["error"]
    assert "subset" in out["error"] or "not in this invocation" in out["error"]


def test_unknown_run_id_shape_is_fatal(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", "../etc")
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert "run id" in out["error"]


# ---------------------------------------------------------------------------
# 10. Idempotent re-claim: the SAME authorization reapplied, not a new one.
# ---------------------------------------------------------------------------

def test_reclaiming_the_same_id_in_the_same_run_is_idempotent(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    first = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    first_record = json.loads((root / "runs" / RUN_ID / f".claimed.{seg}").read_text(encoding="utf-8"))

    # The first call's own claim rewrote seg's draft dispatch_token to
    # RUN_ID:seg, so RUN_ID now genuinely has dispatch evidence -- a REAL
    # second invocation of the same run only ever reaches this point because
    # resume_setup.py ran again first, matched RUN_ID's existing digest, and
    # reported a genuine resume (never --run-resume false a second time,
    # which would now correctly refuse per the #409 Step 3 fresh-evidence
    # check -- see section 13). Simulate that real precondition rather than
    # asserting a shape resume_setup.py itself would never produce.
    make_run_dir(root, RUN_ID)
    second = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "true")
    assert second.returncode == 0, (
        f"a re-claim of an already-claimed id in the SAME run must succeed, not fatal\n"
        f"stdout={second.stdout!r} stderr={second.stderr!r}"
    )
    out = parse_stdout(second)
    assert seg in out["claims"]
    second_record = json.loads((root / "runs" / RUN_ID / f".claimed.{seg}").read_text(encoding="utf-8"))
    assert second_record == first_record, (
        "the SAME authorization reapplied must not silently overwrite the original claimed_at/"
        "cache_key/pre_claim_content_sha1"
    )


# ---------------------------------------------------------------------------
# 11. --classify-only may not be combined with a claim (it promises a
#     read-only report; a claim writes a durable record).
# ---------------------------------------------------------------------------

def test_classify_only_rejects_a_combined_claim(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--classify-only", "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert "--classify-only" in out["error"]
    assert not (root / "runs" / RUN_ID).exists()


# ---------------------------------------------------------------------------
# 12. rewrite_draft_dispatch_token() -- the actual "claim the draft" state
# change (D4). Tested as a STANDALONE unit against the function directly
# (loaded via importlib, the same technique select_segments.test.py's own
# test_sentinel_predicate_is_identical_in_all_four_scripts uses), ALONGSIDE
# the end-to-end coverage the claim tests above already give it: run()'s
# claim block calls it for real, immediately after the record write.
#
# HISTORY WORTH KEEPING, because it explains the shape of the code rather
# than merely what it once was: wiring this call in self-tripped twice. The
# rewrite mutates a draft on disk inside the SAME single-phase invocation,
# and #409's Step 3 gate then read that freshly-rewritten draft back as
# "dispatch evidence for run_id" and refused the invocation for evidence it
# had just manufactured. Reordering only moved the defect; what closed it
# was making Step 3's evidence a one-shot SNAPSHOT taken before the claim
# block writes anything (section 13's own test pins exactly that). The
# earlier TWO-PHASE validate/commit design that this comment used to
# describe was REFUTED and ABANDONED -- there is no commit phase owned
# elsewhere, and nothing here is waiting on one.
#
# These tests pin the function's OWN contract, which is now wider than "swap
# one field": it refuses a draft that is not the one this invocation
# admitted (the TOCTOU between admission and stamp), refuses again when the
# draft moves LATER -- after the staged bytes were hashed but before the
# rename installs them -- refuses rather than follows a symlink planted at
# its predictable temp path, and fails the rewrite when the draft's directory
# entry cannot be made durable.
# ---------------------------------------------------------------------------

def _load_select_segments_module(root):
    """Loads THIS fixture's own copy of select_segments.py (not the repo's
    source) as an importable module, so its self-anchored DURABLE_ROOT and
    sibling-script resolution match the fixture root the test already
    built -- byte-identical to the copy `run_select()` would invoke as a
    subprocess."""
    path = root / "scripts" / "select_segments.py"
    spec = importlib.util.spec_from_file_location("select_segments_under_test_claim", str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rewrite_draft_dispatch_token_changes_only_the_token(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{SOURCE_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    before_sha1 = draft_content_sha1_of(draft)

    mod = _load_select_segments_module(root)
    new_token = mod.draft_dispatch_token_for(RUN_ID, seg)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg, root, new_token, expected_content_sha1=before_sha1
    )
    assert ok, detail

    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == new_token
    assert draft_content_sha1_of(on_disk) == before_sha1, (
        "draft_content_sha1() must be UNCHANGED -- it projects dispatch_token out before "
        "hashing, so an unchanged value here is what proves no OTHER field moved"
    )
    # And the projected content is not merely hash-equal by coincidence --
    # every OTHER field's VALUE must be byte-identical to the original.
    on_disk_without_token = {k: v for k, v in on_disk.items() if k != "dispatch_token"}
    draft_without_token = {k: v for k, v in draft.items() if k != "dispatch_token"}
    assert on_disk_without_token == draft_without_token


def test_rewrite_draft_dispatch_token_is_idempotent(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    new_token = f"{RUN_ID}:{seg}"
    draft["dispatch_token"] = new_token
    write_draft_doc(root, seg, draft)
    before_bytes = (root / "segments" / f"{seg}.draft.json").read_bytes()

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg, root, new_token, expected_content_sha1=draft_content_sha1_of(draft)
    )
    assert ok, detail

    after_bytes = (root / "segments" / f"{seg}.draft.json").read_bytes()
    assert after_bytes == before_bytes, (
        "a draft already carrying the target token must be a true no-op -- a re-claim in "
        "the same run must not be mistaken for a second authorization (D9)"
    )
    assert _staged_temp_files(root) == [], (
        "the no-op path stages a file to prove identity and must remove it again"
    )


def test_rewrite_draft_dispatch_token_refuses_on_missing_draft(tmp_path):
    root = make_durable_root(tmp_path)
    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        "seg_missing", root, f"{RUN_ID}:seg_missing", expected_content_sha1="0" * 40
    )
    assert ok is False
    assert detail


def _staged_temp_files(root):
    """Every `<name>.tmp.<pid>` left behind under segments/. A refusal that
    leaks its staged file would be invisible to an assertion about the draft
    alone, and the staged file is a full copy of a draft."""
    return sorted(p.name for p in (root / "segments").iterdir() if ".tmp." in p.name)


def test_rewrite_refuses_a_draft_that_changed_since_admission(tmp_path):
    """M6(a): admission hashes ONE draft and the claim record preserves that
    observation; the rewrite used to re-read whatever occupied the path at
    that later moment and stamp it regardless. In between sit S1's and S2's
    subprocesses, the segpack scan, a cache_key.py subprocess and the claim
    record's own write -- a wide enough window for a concurrent writer, or a
    parallel fix round, to swap the file. A draft that never passed the
    admission gates must not receive the claiming run's dispatch_token."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    admitted = clean_draft(seg)
    admitted["dispatch_token"] = f"{SOURCE_RUN_ID}:{seg}"
    admitted_sha1 = draft_content_sha1_of(admitted)

    # What is ACTUALLY on disk when the rewrite runs: a different draft.
    swapped = dict(admitted)
    swapped["blocks"] = dict(admitted["blocks"], p1="Something else entirely.")
    write_draft_doc(root, seg, swapped)
    assert draft_content_sha1_of(swapped) != admitted_sha1, (
        "precondition: the two fixtures must genuinely differ in content"
    )

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg, root, f"{RUN_ID}:{seg}", expected_content_sha1=admitted_sha1
    )
    assert ok is False, "a draft that is not the admitted one must be refused"
    assert admitted_sha1 in detail and draft_content_sha1_of(swapped) in detail, (
        f"the refusal must NAME the drift -- both the hash admission gated and the hash "
        f"actually found. Got: {detail}"
    )

    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{SOURCE_RUN_ID}:{seg}", (
        "nothing may be installed on the refusal path -- the draft must still carry the "
        "OLD token, so every existing gate goes on refusing it"
    )
    assert on_disk == swapped, "the refused draft's own content must be untouched"
    assert _staged_temp_files(root) == []


def test_rewrite_refuses_a_draft_edited_after_staging_but_before_install(tmp_path):
    """The LATER window the test above structurally cannot reach (second code
    review, #438): it swaps the draft BEFORE the rewrite reads it, so the
    staged bytes carry the swap and the staged-hash comparison catches it.
    An edit landing AFTER that read -- while the temp file is being written,
    fsynced and hashed -- never touches the staged bytes at all. The staged
    check therefore passes, and an unconditional os.replace() then discards a
    hand edit newer than the one admission gated. That is the exact loss this
    whole release exists to prevent, so the rewrite re-reads the canonical
    draft immediately before the rename and refuses if it moved.

    HOW THE WINDOW IS DRIVEN, and why this is not a flaky race: the window is
    internal to one function and a few syscalls wide, so waiting for a real
    concurrent writer to land inside it would be a coin flip. Instead the
    test lands the edit ON the seam the production code itself uses -- its
    first draft_content_sha1() call, the staged-file hash -- which is
    deterministic and pins the ORDER as well: the `hashed` assertion (second
    call reads the CANONICAL draft, not the temp file a second time) is what
    separates a real last-moment check from another look at bytes that
    cannot have changed.

    WHAT THIS TEST DOES NOT PROVE, deliberately: not that the window is
    closed. The gap between that final hash and os.replace() remains open,
    and no test in this file can assert otherwise -- see the function's own
    "THE RESIDUAL, STATED NARROWLY" paragraph."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    admitted = clean_draft(seg)
    admitted["dispatch_token"] = f"{SOURCE_RUN_ID}:{seg}"
    write_draft_doc(root, seg, admitted)
    admitted_sha1 = draft_content_sha1_of(admitted)

    concurrent = dict(admitted)
    concurrent["blocks"] = dict(admitted["blocks"], p1="A hand edit that landed mid-claim.")
    concurrent_sha1 = draft_content_sha1_of(concurrent)
    assert concurrent_sha1 != admitted_sha1, (
        "precondition: the mid-claim edit must genuinely change the draft's content"
    )

    mod = _load_select_segments_module(root)
    real_draft_content_sha1 = mod.draft_content_sha1
    hashed = []

    def hash_and_land_a_concurrent_edit(path):
        """Stands in for a writer that wins the staging window: the FIRST
        call is the staged-file hash, so the canonical draft is edited the
        moment that hash has been taken."""
        hashed.append(Path(path).name)
        result = real_draft_content_sha1(path)
        if len(hashed) == 1:
            write_draft_doc(root, seg, concurrent)
        return result

    mod.draft_content_sha1 = hash_and_land_a_concurrent_edit

    ok, detail = mod.rewrite_draft_dispatch_token(
        seg, root, f"{RUN_ID}:{seg}", expected_content_sha1=admitted_sha1
    )

    assert ok is False, (
        "a draft edited after the staged bytes were hashed must be refused, not "
        "overwritten by the rename"
    )
    assert concurrent_sha1 in detail and admitted_sha1 in detail, (
        f"the refusal must NAME the drift -- what is on disk now and what admission "
        f"gated. Got: {detail}"
    )

    draft_file = root / "segments" / f"{seg}.draft.json"
    on_disk = json.loads(draft_file.read_text(encoding="utf-8"))
    assert on_disk == concurrent, (
        "THE POINT OF THE WHOLE FEATURE: the newer hand edit must survive intact. "
        "Without the last-moment re-read the rename installs the admitted bytes over "
        "it and the edit is gone with nothing on disk recording that it existed"
    )
    assert on_disk["dispatch_token"] == f"{SOURCE_RUN_ID}:{seg}", (
        "and nothing may be authorized either -- the draft must still carry the OLD "
        "token so every existing gate goes on refusing it"
    )
    assert _staged_temp_files(root) == [], "the refusal must not leak its staged copy"
    assert hashed == [f"{seg}.draft.json.tmp.{os.getpid()}", f"{seg}.draft.json"], (
        f"the two hashes must be of DIFFERENT files, in this order: the staged temp "
        f"file (what goes in), then the canonical draft (what would be overwritten). "
        f"A second hash of the temp file would re-answer the question already "
        f"answered. Got: {hashed}"
    )


def test_rewrite_still_checks_identity_on_the_idempotent_path(tmp_path):
    """The no-op path affirms an authorization too. A draft already carrying
    this run's token, whose CONTENT is not what admission gated, must be
    refused rather than silently reported as claimed -- otherwise the
    identity guarantee holds on one path and not the other, and the weaker
    path is the one a re-claim takes."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    before_bytes = (root / "segments" / f"{seg}.draft.json").read_bytes()

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg, root, f"{RUN_ID}:{seg}", expected_content_sha1="0" * 40
    )
    assert ok is False, detail
    assert "0" * 40 in detail
    assert (root / "segments" / f"{seg}.draft.json").read_bytes() == before_bytes
    assert _staged_temp_files(root) == []


def test_rewrite_requires_the_admitted_content_sha1(tmp_path):
    """The baseline is REQUIRED, not optional-with-a-skip. A caller that
    cannot say what it admitted has nothing to check the draft against, and
    the honest answer is a refusal rather than an unguarded rewrite."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{SOURCE_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    mod = _load_select_segments_module(root)

    for missing in (None, "", 0):
        ok, detail = mod.rewrite_draft_dispatch_token(
            seg, root, f"{RUN_ID}:{seg}", expected_content_sha1=missing
        )
        assert ok is False, f"expected_content_sha1={missing!r} must refuse, got {detail!r}"
        assert "nothing to check the draft against" in detail

    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{SOURCE_RUN_ID}:{seg}"

    # And it is keyword-only and has no default: omitting it entirely is a
    # TypeError at the call site, never a rewrite with the check skipped.
    with pytest.raises(TypeError):
        mod.rewrite_draft_dispatch_token(seg, root, f"{RUN_ID}:{seg}")


def test_rewrite_refuses_a_symlink_planted_at_its_temp_path(tmp_path):
    """M6(b): the temp file's name is predictable (`<draft>.tmp.<pid>`) and
    used to be opened with a plain open(..., "w"), which FOLLOWS a symlink
    sitting at that name and truncates whatever it points at -- before the
    file is ever installed as the draft. O_CREAT|O_EXCL|O_NOFOLLOW makes a
    pre-existing entry of any kind a refusal instead of a target."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{SOURCE_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)

    victim = root / "segments" / "unrelated_precious.json"
    victim.write_text('{"do": "not truncate me"}', encoding="utf-8")
    victim_before = victim.read_bytes()

    draft_file = root / "segments" / f"{seg}.draft.json"
    planted = root / "segments" / f"{draft_file.name}.tmp.{os.getpid()}"
    planted.symlink_to(victim)

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg, root, f"{RUN_ID}:{seg}", expected_content_sha1=draft_content_sha1_of(draft)
    )
    assert ok is False, "an entry already occupying the temp path must refuse the rewrite"
    assert victim.read_bytes() == victim_before, (
        "the symlink target must not be truncated -- this is the whole defect"
    )
    on_disk = json.loads(draft_file.read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{SOURCE_RUN_ID}:{seg}", (
        "and nothing may be installed as the draft either"
    )
    assert planted.is_symlink(), (
        "the planted entry is not ours to remove -- refusing must not clean up an "
        "artifact this process did not create"
    )


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permissions, so the fsync cannot be made to fail",
)
def test_rewrite_fails_when_the_drafts_directory_entry_cannot_be_made_durable(tmp_path):
    """B2: fsync on the temp file commits its CONTENTS; the rename that makes
    those contents findable as `{seg}.draft.json` is a directory-entry change
    an unsynced directory can lose. Paired with claim_record.py's own fsync of
    runs/<run_id>/, that is what makes record-first survive a power loss --
    without it a crash can keep the new token and lose the record, the one
    state D8's guard cannot refuse because it sees no record and reads
    "unclaimed".

    Observed WITHOUT patching anything: mode 0o333 on segments/ leaves the
    draft readable by name and the rename possible (execute + write), but
    makes os.open(dir, O_RDONLY) -- what fsync_directory() does -- fail with
    EACCES. Delete the fsync_directory() call from the production function
    and this test goes green with ok=True, which is exactly what it is for."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{SOURCE_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    mod = _load_select_segments_module(root)

    segments_dir = root / "segments"
    os.chmod(segments_dir, 0o333)
    try:
        ok, detail = mod.rewrite_draft_dispatch_token(
            seg, root, f"{RUN_ID}:{seg}", expected_content_sha1=draft_content_sha1_of(draft)
        )
    finally:
        os.chmod(segments_dir, 0o755)

    assert ok is False, (
        "a directory entry this code cannot prove durable must FAIL the rewrite, never "
        "be shrugged off as best-effort"
    )
    assert "directory entry is not durable" in detail, detail
    # The composed shape claim_record.fsync_directory() promises its callers:
    # the caller's own clause, then the helper's subordinate clause.
    assert detail.startswith("the draft was re-stamped but "), detail


# ---------------------------------------------------------------------------
# 13. #409 Step 3 fresh-evidence provenance fix -- GENERAL Step 3 infra, NOT
# claim-specific. Found while wiring the claim's own --run-id/single-phase
# ordering (a digest existing for THIS invocation's own --run-id only proves
# resume_setup.py ran just now under single-phase ordering, never that any
# PRE-EXISTING evidence bearing that same id was ever gated), but the defect
# predates #438 and the fix lives in the plain Step 3 gate, reachable with no
# --from-converged/--from-cap flag at all. Its own dedicated test, deliberately
# not folded into any claim-admission test above.
# ---------------------------------------------------------------------------

def _write_phantom_evidence_draft(root, run_id, phantom_seg="phantom_seg"):
    """A minimal draft carrying a dispatch_token for `run_id`, NOT a member
    of manifest.json and never claimed through this suite's usual fixture
    builders -- scan_dispatching_run_ids() scans every *.draft.json under
    segments/ unconditionally, regardless of manifest membership, so this is
    sufficient to manufacture "pre-existing dispatch evidence bearing this
    exact id" without needing a full P1/P2 population."""
    write_draft_doc(root, phantom_seg, {"seg": phantom_seg, "dispatch_token": f"{run_id}:{phantom_seg}"})


def test_step3_refuses_a_fresh_run_id_that_already_has_dispatch_evidence(tmp_path):
    """The refuse arm: resume_setup.py reports this run id as FRESH
    (--run-resume false), but this project already has dispatch evidence
    bearing that EXACT id (a phantom draft's dispatch_token). A fresh id
    colliding with pre-existing evidence must be refused, not laundered
    through by the digest resume_setup.py itself just wrote.

    This arm deliberately carries NO runs/<RUN_ID>/input.digest, which is one
    state and NOT the one the production path reaches -- under single-phase
    ordering resume_setup.py has already written the digest for the id it
    just minted by the time this script runs. That state is the test
    immediately below, and it is the one that pins the guard; on its own this
    arm cannot tell a refusal keyed on the collision apart from one keyed on
    the missing digest."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    _write_phantom_evidence_draft(root, RUN_ID)

    proc = run_select(root, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    assert RUN_ID in out["error"]
    assert "FRESH" in out["error"], out["error"]
    assert "drafts" in out["error"], out["error"]


def test_step3_refuses_a_fresh_run_id_colliding_with_evidence_even_with_a_digest(tmp_path):
    """THE arm that matches production (second code review, #438). Identical
    to the one above except that runs/<RUN_ID>/input.digest EXISTS -- which
    is not an exotic variant but the ONLY state select_segments.py is ever
    reached in under single-phase ordering: resume_setup.py runs first and
    writes the digest for the id it just minted, then this script is invoked
    with that id.

    Why the arm above cannot stand in for it: with no digest present, RUN_ID
    also lands in `runs_missing_digest`, so a guard narrowed to "fresh id,
    colliding evidence AND no digest" still refuses there and the test stays
    green while the production state -- fresh id, colliding evidence, digest
    present -- sails straight through. The guard must key on the COLLISION,
    never on the digest's absence, because the digest resume_setup.py just
    wrote proves only that resume_setup.py ran as part of THIS invocation and
    nothing whatever about whether the pre-existing evidence was ever gated.
    That is the entire premise of the fix."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    _write_phantom_evidence_draft(root, RUN_ID)
    # Exactly what resume_setup.py leaves behind for a freshly-minted id.
    make_run_dir(root, RUN_ID)
    assert (root / "runs" / RUN_ID / "input.digest").is_file(), (
        "precondition: the digest resume_setup.py writes for the id it minted"
    )

    proc = run_select(root, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    assert RUN_ID in out["error"]
    assert "FRESH" in out["error"], out["error"]
    assert "drafts" in out["error"], out["error"]
    # And it is THIS refusal, not the #409 runs-missing-digest one -- which
    # cannot fire at all now that the digest is present. Asserting the
    # distinguishing word keeps a future re-wording of the other refusal from
    # making this test pass for the wrong reason.
    assert "launder" in out["error"], out["error"]


def test_step3_admits_a_genuinely_resumed_run_id_with_the_same_evidence_shape(tmp_path):
    """The mirror admit arm: the IDENTICAL evidence shape (same phantom
    draft, same run id), but resume_setup.py reports a genuine RESUME
    (--run-resume true) -- which is only reportable once resume_setup.py has
    already matched and written this id's own input.digest. The fresh-
    evidence check must not fire on this arm at all; the pre-existing
    #409 Step 3 digest-presence check then passes normally."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    _write_phantom_evidence_draft(root, RUN_ID)
    make_run_dir(root, RUN_ID)  # the digest a genuine resume implies already exists

    proc = run_select(root, "--run-id", RUN_ID, "--run-resume", "true")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is True, out


def test_step3_admits_a_fresh_claim_that_rewrites_its_own_evidence(tmp_path):
    """THE snapshot-vs-live-rescan pin (team-lead, #438 review): a real claim
    -- with rewrite_draft_dispatch_token() wired in for real -- under
    --run-id/--run-resume false, with NO pre-existing evidence for RUN_ID
    anywhere, must SUCCEED. This is the exact case that failed three times
    under three different names before the evidence scan became a one-time
    snapshot (r13's digest-laundering finding, then runs_missing_digest
    self-tripping on the first wiring attempt, then this same fresh-evidence
    check self-tripping on the second): the claim's OWN write (the draft's
    dispatch_token, rewritten to RUN_ID:seg) must never be read back by Step
    3 as "dispatch evidence" within the SAME invocation. If a future edit
    reintroduces a live re-scan anywhere downstream of the snapshot, this is
    the test that goes red -- not one of the two arms above, which never
    exercise the claim block's own write at all."""
    root = make_durable_root(tmp_path)
    seg = "seg30"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is True, out
    assert seg in out["claims"], out

    # Proves the claim's write actually happened -- a silently-skipped
    # rewrite would make this test pass for the wrong reason (nothing to
    # self-refuse against).
    restamped = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert restamped["dispatch_token"] == f"{RUN_ID}:{seg}", restamped


def test_run_resume_true_with_no_digest_at_all_is_fatal(tmp_path):
    """The one cheap check that IS derivable on the --run-resume true branch:
    a genuinely RESUMED run must already have runs/<RUN_ID>/input.digest --
    that is the literal precondition resume_setup.py's own resume match
    requires. No phantom evidence needed here; the digest's absence alone is
    incoherent with an attested resume, most likely a malformed or stale
    relay of resume_setup.py's own 'resume' field."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])

    proc = run_select(root, "--run-id", RUN_ID, "--run-resume", "true")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    assert RUN_ID in out["error"]
    assert "input.digest" in out["error"], out["error"]


def test_run_resume_true_with_pre_existing_evidence_is_a_known_admitted_residual(tmp_path):
    """KNOWN RESIDUAL, disclosed rather than closed (team-lead, #438 review) --
    this is a characterization test, not a regression guard: it pins that the
    fresh-evidence check is ONE-SIDED, on purpose, because there is no way to
    close it from inside this script.

    Fixture-identical to test_step3_admits_a_genuinely_resumed_run_id_with_
    the_same_evidence_shape above (pre-existing dispatch evidence bearing
    RUN_ID, plus an input.digest for RUN_ID) -- and that is exactly the
    point: a genuine resume and an attested-but-false "--run-resume true"
    covering pre-existing evidence are INDISTINGUISHABLE from inside
    select_segments.py, because resume_setup.py writes a fresh run's digest
    with the same shape a resumed run's digest has, and 'the digest existed
    BEFORE this pipeline ran' is not observable after the fact. --run-resume
    is a RELAY of resume_setup.py's own field, not something this script
    re-derives -- a caller that mis-relays or lies defeats the check
    completely. If this test ever starts failing (i.e. the case becomes
    REFUSED), it means someone found a real discriminator and closed the
    hole -- update this docstring, don't just re-green the assertion."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    _write_phantom_evidence_draft(root, RUN_ID)
    make_run_dir(root, RUN_ID)

    proc = run_select(root, "--run-id", RUN_ID, "--run-resume", "true")
    assert proc.returncode == 0, (
        f"documents the known residual: pre-existing evidence + an attested "
        f"(possibly false) --run-resume true is currently ADMITTED, because "
        f"nothing inside this script can tell it apart from a real resume.\n"
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    out = parse_stdout(proc)
    assert out["success"] is True, out


# ---------------------------------------------------------------------------
# 13a. --run-id and --run-resume are a PAIR, enforced by THIS script.
#
# The dispatch driver enforces the same pairing on its own side and has its
# own tests for it (tests/claim_driver.test.py) -- which is exactly why these
# exist rather than being a duplicate of them: every CLI-level test in that
# file runs against a FAKE select_segments.py, whose own comment states it
# does not reproduce this refusal. So the driver's tests exercise the
# DRIVER's guard and nothing else, and deleting the selector's would leave
# every one of them green while the selector silently accepted a --run-id
# with no --run-resume from any other caller (a hand-run invocation,
# SKILL.md's own W5 recipe). A guard nothing can turn red is not a guard.
#
# What the selector actually does with an unpaired flag is the reason the
# pairing matters rather than being tidiness: --run-resume is the ONLY thing
# that tells a legitimately resumed run id from a freshly-minted one, and
# both of the checks that read it compare against a string. With the flag
# absent, `args.run_resume == "false"` and `args.run_resume == "true"` are
# both False, so BOTH refusals -- the fresh-evidence collision refusal and
# the resumed-run digest-presence refusal -- silently do not apply, and a
# --run-id alone buys dispatch authorization with neither check ever having
# run. That is a bypass, not a defaulting question, which is why it fatals
# instead of assuming either value.
#
# Both use --allow-empty so the guard is the ONLY thing that can fail the
# run: without it, deleting the guard would still exit non-zero on the empty
# selection, and the test would go on passing for an unrelated reason.
# ---------------------------------------------------------------------------

def test_run_id_without_run_resume_is_fatal(tmp_path):
    """One half of the pair, given alone. No claim flags at all -- the
    pairing rule is general Step 3 infrastructure and must fire on a plain
    invocation, not only when --from-converged/--from-cap is involved."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])

    proc = run_select(root, "--allow-empty", "--run-id", RUN_ID)
    assert proc.returncode != 0, (
        f"--run-id alone must be refused, never treated as 'resume status not "
        f"applicable'. stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    out = parse_stdout(proc)
    assert out["success"] is False
    assert "--run-id" in out["error"] and "--run-resume" in out["error"], out["error"]
    assert "TOGETHER" in out["error"], out["error"]


def test_run_resume_without_run_id_is_fatal(tmp_path):
    """The other half, and not a mirror-image formality: this direction is
    the quieter failure. --run-resume names no run id of its own, so a
    caller that dropped --run-id would otherwise get a completely
    unauthorized-run-id invocation that LOOKS attested, with the operator's
    'true'/'false' silently applying to nothing."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])

    for resume_value in ("true", "false"):
        proc = run_select(root, "--allow-empty", "--run-resume", resume_value)
        assert proc.returncode != 0, (
            f"--run-resume {resume_value} alone must be refused. "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        out = parse_stdout(proc)
        assert out["success"] is False
        assert "--run-id" in out["error"] and "--run-resume" in out["error"], out["error"]
        assert "TOGETHER" in out["error"], out["error"]


# ---------------------------------------------------------------------------
# 14. A zero-length / torn claim record (crash between the O_EXCL create and
# the fsync in claim_record.write_claim_record()) must classify AMBIGUOUS via
# read_claim_record() -- never PRESENT-with-empty-contents, and never
# silently treated as "not claimed" by way of being unparseable in some OTHER
# direction. Pure claim_record.py unit test; select_segments.py is not
# involved, since every one of its own call sites already treats AMBIGUOUS as
# "do not claim" by construction (claim_record's own module contract) and a
# fixture that could distinguish "gate refuses" from "gate never got called"
# would test claim_record.py a second time through a heavier lens.
# ---------------------------------------------------------------------------

def _load_claim_record_module():
    spec = importlib.util.spec_from_file_location("claim_record_under_test_torn", str(CLAIM_RECORD_SRC))
    assert spec is not None and spec.loader is not None, f"cannot load {CLAIM_RECORD_SRC}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_zero_length_claim_record_classifies_ambiguous_not_present(tmp_path):
    mod = _load_claim_record_module()
    runs_dir = tmp_path / "runs"
    path = mod.claimed_path(RUN_ID, "seg01", runs_dir)
    path.parent.mkdir(parents=True)
    path.touch()  # simulates a crash between O_EXCL create and the fsync'd write
    assert path.stat().st_size == 0

    state, payload, detail = mod.read_claim_record(path)
    assert state == mod.CLAIM_AMBIGUOUS, (
        f"a zero-length claim record must classify AMBIGUOUS, got {state!r} (detail={detail!r})"
    )
    assert state != mod.CLAIM_PRESENT
    assert payload is None
    assert detail


def test_torn_claim_record_with_partial_json_classifies_ambiguous(tmp_path):
    """A second torn shape: a crash mid-write leaves a truncated but
    non-empty body (valid file, invalid JSON) -- distinct from the
    zero-length case above, and must land on the identical AMBIGUOUS side."""
    mod = _load_claim_record_module()
    runs_dir = tmp_path / "runs"
    path = mod.claimed_path(RUN_ID, "seg01", runs_dir)
    path.parent.mkdir(parents=True)
    path.write_text('{"seg": "seg01", "profile": "from-conve', encoding="utf-8")

    state, payload, detail = mod.read_claim_record(path)
    assert state == mod.CLAIM_AMBIGUOUS
    assert payload is None
    assert detail


def test_build_claim_record_emits_exactly_the_declared_field_set_in_order(tmp_path):
    """Drift pin for the record's SHAPE, in one assertion: the builder's own
    output keys, and their ORDER, must equal CLAIM_RECORD_FIELDS.

    Two separate definitions of the field set exist by design (the tuple,
    then a literal dict in the builder) so that the tuple can be read by a
    drift test without executing the builder -- which is exactly the
    arrangement that lets them disagree. write_claim_record() serialises with
    sort_keys=False, so the ORDER is not cosmetic either: it is the order an
    operator reads the marker file in, and `pre_claim_*` fields sitting
    beside their `*_at_claim` counterparts is what makes the two endpoints
    legible at a glance.

    Passing every field as None is deliberate: this test is about the SHAPE,
    and using None throughout means it cannot accidentally pass because some
    value happened to be positioned correctly. It also re-asserts that every
    parameter is keyword-addressable and required -- a field added to the
    tuple and not to the signature is a TypeError right here."""
    mod = _load_claim_record_module()
    payload = mod.build_claim_record(**{field: None for field in mod.CLAIM_RECORD_FIELDS})
    assert tuple(payload) == mod.CLAIM_RECORD_FIELDS, (
        f"the builder's key set/order has drifted from CLAIM_RECORD_FIELDS.\n"
        f"builder: {tuple(payload)}\ndeclared: {mod.CLAIM_RECORD_FIELDS}"
    )
    # And the declaration itself is not silently empty or duplicated.
    assert len(set(mod.CLAIM_RECORD_FIELDS)) == len(mod.CLAIM_RECORD_FIELDS) >= 14


# ---------------------------------------------------------------------------
# 15. D9's LOST-TOKEN RECOVERY -- the remedy draft_ready.py advertises, made
# reachable.
#
# draft_ready.py's _claim_note() tells the operator, in as many words, to
# "re-claim {seg} under the same profile to restore it" once it finds a claim
# record for this run and no matching token on the draft. That instruction
# named a command that could not run: S3 refused a token-less draft before
# the claim block ever consulted an existing record, so the sanctioned
# recovery was unreachable and D9's residual was not in fact
# re-establishable.
#
# Every test here drives the REAL select_segments.py end to end, because the
# defect was in the ORDER two gates ran, not in either gate's logic -- a unit
# test of the recovery predicate alone would have passed against the broken
# build.
# ---------------------------------------------------------------------------

def _drop_dispatch_token(root, seg):
    """Simulate the event draft_ready.py's note describes: a fix round
    rewrote the draft and did not preserve `dispatch_token` byte for byte.
    Dropping the field entirely is the realistic shape -- draft.schema.json
    makes `dispatch_token` optional, so a draft re-emitted against the schema
    simply loses it."""
    path = root / "segments" / f"{seg}.draft.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.pop("dispatch_token", None)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert "dispatch_token" not in json.loads(path.read_text(encoding="utf-8"))


def test_lost_token_is_recoverable_by_reclaiming_under_the_same_profile(tmp_path):
    """The advertised remedy, executed. Claim the segment, lose the token,
    re-claim under the SAME profile in the SAME run -- and the draft comes
    back stamped, off the durable record this run itself wrote.

    The original claim record must survive UNCHANGED: a recovery restores a
    token, it does not mint a second authorization (D9)."""
    root = make_durable_root(tmp_path)
    seg = "seg14"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    first = run_select(
        root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false"
    )
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    marker = root / "runs" / RUN_ID / f".claimed.{seg}"
    record_before = marker.read_bytes()

    _drop_dispatch_token(root, seg)

    # A real second invocation of the same run reaches this point only after
    # resume_setup.py ran again and matched RUN_ID's existing digest -- the
    # same precondition section 10's idempotent re-claim simulates.
    make_run_dir(root, RUN_ID)
    second = run_select(
        root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "true"
    )
    assert second.returncode == 0, (
        f"the recovery draft_ready.py advertises must actually run\n"
        f"stdout={second.stdout!r} stderr={second.stderr!r}"
    )
    out = parse_stdout(second)
    assert seg in out["claims"], out

    restamped = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert restamped["dispatch_token"] == f"{RUN_ID}:{seg}", (
        "the recovery's whole point is that the draft ends up stamped again"
    )
    assert marker.read_bytes() == record_before, (
        "a recovery re-establishes a token; it must not rewrite the durable record, "
        "which is the only account of what the draft looked like at the ORIGINAL claim"
    )
    assert "lost-token recovery" in second.stderr, (
        f"a recovery must never be silent -- the operator has to see that the tool "
        f"noticed. stderr={second.stderr!r}"
    )


def test_missing_token_with_no_claim_record_is_still_refused(tmp_path):
    """The hole the recovery must NOT open. Same token-less draft, same
    profile, same everything -- but this run never claimed the segment, so
    there is no record and nothing distinguishes it from an unclaimed draft
    that never had a token. Refused exactly as before."""
    root = make_durable_root(tmp_path)
    seg = "seg14"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)
    _drop_dispatch_token(root, seg)

    proc = run_select(
        root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false"
    )
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    assert "dispatch_token" in out["error"]
    assert "holds no claim record" in out["error"], out["error"]
    assert not (root / "runs" / RUN_ID / f".claimed.{seg}").exists(), (
        "a refused claim must leave no record behind"
    )


def test_lost_token_recovery_refuses_under_a_different_profile(tmp_path):
    """"Re-claim under the SAME profile" is the literal instruction, and the
    profile is not a formality: the two profiles are closed condition lists
    over different populations. Naming the other one is a NEW authorization,
    and a new authorization needs a draft this gate can still read a token
    from."""
    root = make_durable_root(tmp_path)
    seg = "seg14"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    first = run_select(
        root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false"
    )
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    _drop_dispatch_token(root, seg)
    make_run_dir(root, RUN_ID)

    proc = run_select(
        root, "--only-segs", seg, "--from-converged", seg, "--run-id", RUN_ID,
        "--run-resume", "true",
    )
    assert proc.returncode != 0
    out = parse_stdout(proc)
    # Asserted on the PROFILE clause specifically: this fixture would fail
    # --from-converged's own sentinel/status conditions as well, and D2
    # reports every failure together, so a bare "it refused" would not
    # distinguish the profile check from those.
    assert "written under profile 'from-cap', not the requested 'from-converged'" in out["error"], (
        out["error"]
    )


def test_lost_token_recovery_refuses_on_an_unreadable_claim_record(tmp_path):
    """AMBIGUOUS is not "claimed". A torn record (a crash between the O_EXCL
    create and the fsync'd write) carries no readable profile or provenance,
    and claim_record.py's discipline for every reader is that an unreadable
    record means NOT claimed -- never assumed claimed, which here would mean
    re-stamping a draft on the strength of a record nobody can read."""
    root = make_durable_root(tmp_path)
    seg = "seg14"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    first = run_select(
        root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false"
    )
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    _drop_dispatch_token(root, seg)
    make_run_dir(root, RUN_ID)

    marker = root / "runs" / RUN_ID / f".claimed.{seg}"
    marker.write_text('{"seg": "seg14", "profile": "from-c', encoding="utf-8")

    proc = run_select(
        root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "true"
    )
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert "unreadable" in out["error"], out["error"]
    assert "treated as NOT claimed" in out["error"], out["error"]
    draft = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert "dispatch_token" not in draft, (
        "a refused recovery must not stamp the draft anyway"
    )


def test_a_malformed_but_present_token_is_not_treated_as_a_lost_one(tmp_path):
    """The recovery is deliberately narrow: ABSENT, never merely malformed.
    A dropped field is what a schema-shaped rewrite produces; a garbled
    non-empty token is a different event -- a cross-run collision, a hand
    edit, a partial write -- and reading it as "lost" would let the recovery
    answer a question it has no evidence about. S3's malformed-token branch
    keeps refusing it even with this run's own claim record in place."""
    root = make_durable_root(tmp_path)
    seg = "seg14"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    first = run_select(
        root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false"
    )
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    path = root / "segments" / f"{seg}.draft.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["dispatch_token"] = "no-colon-here"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    make_run_dir(root, RUN_ID)

    proc = run_select(
        root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "true"
    )
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert "malformed" in out["error"], out["error"]


# ---------------------------------------------------------------------------
# 16. D6's cache-key evidence, as written INTO the record rather than spliced
# onto the report. The two endpoints and the per-field diff between them are
# the justification for a claim that voids a review; a record that omits them
# leaves stdout from an exited process as the only account of it.
# ---------------------------------------------------------------------------

def test_moved_cache_key_fields_name_both_endpoints_in_the_record(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(
        root,
        seg,
        fixture_keys,
        fragment_cache_key_overrides={"style_contract_hash": "style_contract_hash-OLD"},
    )
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    record = json.loads(
        (root / "runs" / RUN_ID / f".claimed.{seg}").read_text(encoding="utf-8")
    )

    assert record["cache_key_moved_fields"] == [
        {
            "field": "style_contract_hash",
            "pre_claim": "style_contract_hash-OLD",
            "at_claim": fixture_keys[seg]["style_contract_hash"],
        }
    ], record["cache_key_moved_fields"]
    assert record["cache_key_movement_machinery_only"] is False, (
        "style_contract_hash is PROSE-bearing, not machinery -- the distinction D6 "
        "records is worthless if every movement reads as machinery"
    )
    assert record["cache_key_note"] is None, (
        "a note explains the ABSENCE of a baseline; here one existed"
    )
    assert record["pre_claim_cache_key"]["style_contract_hash"] == "style_contract_hash-OLD"
    assert record["cache_key_at_claim"] == fixture_keys[seg]
    assert record["pre_claim_review"] == {
        "dispatch_token": None,
        "clean": True,
        "coverage_ok": True,
        "findings_count": 0,
    }, record["pre_claim_review"]


# ---------------------------------------------------------------------------
# 17. #438 round 4: rewrite_draft_dispatch_token()'s refusal of an OLD run
# REASSERTING a SUPERSEDED authorization -- the third review round found the
# same defect class a third time, always through the same mechanism:
# ownership is recorded in TWO places (the per-run claim record and the
# draft's global, mutable dispatch_token), a run's own claim record is NEVER
# released, so run()'s "already claimed by this run" EEXIST branch cannot
# tell a resumed OLD claim apart from a genuinely reapplied one by disk state
# alone -- and used to re-stamp the draft either way. Unit-level tests of the
# four-part predicate in isolation, each pinned to exactly one condition;
# section 18 below proves the same thing end to end, through the real
# admission pipeline and run()'s own wiring of `already_claimed_by_this_run`.
# ---------------------------------------------------------------------------

def _touch_claim_marker(root, run_id, seg):
    """A REGULAR file at claimed_path(run_id, seg, runs/) -- enough for
    classify_claim_record() to read CLAIM_PRESENT, since it only lstat()s
    and never parses the body. Content is irrelevant to the ownership check
    under test, which calls classify_claim_record(), never
    read_claim_record()."""
    path = root / "runs" / run_id / f".claimed.{seg}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seg": seg, "run_id": run_id}), encoding="utf-8")
    return path


def test_rewrite_refuses_reasserting_a_claim_another_run_now_owns(tmp_path):
    """The regression predicate itself, in isolation: the draft's CURRENT
    token names OTHER_RUN_ID, OTHER_RUN_ID's own claim record is still live,
    and `already_claimed_by_this_run=True` (exactly what run() passes on the
    "already claimed by this run" EEXIST branch -- this run's OWN record for
    `seg` already existed before this call). All four predicate conditions
    hold, so this must refuse.

    MUTATION: delete the `and already_claimed_by_this_run` clause from the
    `if` in rewrite_draft_dispatch_token() -- observed RED below (this test
    then fails because the call succeeds and stamps RUN_ID's token)."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    before_bytes = (root / "segments" / f"{seg}.draft.json").read_bytes()
    _touch_claim_marker(root, OTHER_RUN_ID, seg)

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
        already_claimed_by_this_run=True,
    )
    assert ok is False, "an old run reasserting a superseded authorization must be refused"
    assert OTHER_RUN_ID in detail, (
        f"the refusal must NAME the run that currently owns the segment. Got: {detail}"
    )
    assert seg in detail
    assert "OWNED BY RUN" in detail, f"must say OWNED, not merely mismatched. Got: {detail}"

    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}", (
        "nothing may be installed -- the segment's current owner's token must survive"
    )
    assert (root / "segments" / f"{seg}.draft.json").read_bytes() == before_bytes
    assert _staged_temp_files(root) == [], "an early refusal must never even stage a temp file"


def test_rewrite_allows_a_fresh_claim_over_a_run_that_still_holds_a_live_claim(tmp_path):
    """The case a NAIVE 'refuse whenever the named run's claim record is
    still live' predicate would break, since a claim record is NEVER
    released: `already_claimed_by_this_run=False` (this run's OWN first
    claim of `seg` -- exactly what run() passes on the fresh
    `published=True` branch) must succeed even though the draft names a run
    whose own claim record is still very much present.

    MUTATION: drop condition 4 entirely (refuse on a live foreign claim
    alone, ignoring `already_claimed_by_this_run`) -- observed RED below."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    _touch_claim_marker(root, OTHER_RUN_ID, seg)

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
        already_claimed_by_this_run=False,
    )
    assert ok, detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{RUN_ID}:{seg}"


def test_rewrite_skips_the_ownership_check_when_the_named_run_never_claimed(tmp_path):
    """The ORDINARY claim: the draft carries the token of the run that
    originally TRANSLATED it, and that run holds no claim record at all
    (claimed_path() -> CLAIM_ABSENT). Condition 3 is false, so the check
    must not fire even with already_claimed_by_this_run=True.

    MUTATION: change `foreign_state != claim_record.CLAIM_ABSENT` to `True`
    unconditionally -- observed RED below."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{SOURCE_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    # No marker written for SOURCE_RUN_ID -- CLAIM_ABSENT is the point.
    assert not (root / "runs" / SOURCE_RUN_ID / f".claimed.{seg}").exists()

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
        already_claimed_by_this_run=True,
    )
    assert ok, detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{RUN_ID}:{seg}"


def test_rewrite_treats_an_ambiguous_foreign_claim_record_as_still_live(tmp_path):
    """claimed_path() names a DIRECTORY instead of a regular file --
    classify_claim_record() reports CLAIM_AMBIGUOUS, never CLAIM_ABSENT, and
    this check's safe direction (per claim_record.py's own module contract)
    is to treat AMBIGUOUS as LIVE: a record this call cannot read cannot be
    ruled out as released. Must refuse, exactly like a confirmed-live one.

    MUTATION: change `foreign_state != claim_record.CLAIM_ABSENT` to
    `foreign_state == claim_record.CLAIM_PRESENT` -- observed RED below."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    marker = root / "runs" / OTHER_RUN_ID / f".claimed.{seg}"
    marker.mkdir(parents=True)  # a directory, not a regular file -> AMBIGUOUS

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
        already_claimed_by_this_run=True,
    )
    assert ok is False, "an AMBIGUOUS foreign claim record must be treated as still live"
    assert OTHER_RUN_ID in detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"


def test_rewrite_skips_the_ownership_check_when_the_token_is_absent(tmp_path):
    """D9's lost-token recovery: the draft's CURRENT dispatch_token is
    missing entirely, so condition 1 (draft_run_id() -> None) is false and
    the whole check is skipped -- unconditionally, even with
    already_claimed_by_this_run=True, since that is exactly the shape a
    resumed run's OWN recovery re-claim takes.

    MUTATION: fall back `current_owner` to this_run_id's own complement
    (treat a missing token as "owned by someone, refuse anyway") instead of
    None when draft_run_id() cannot parse the current token -- observed RED
    below."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    # No dispatch_token at all.
    write_draft_doc(root, seg, draft)

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
        already_claimed_by_this_run=True,
    )
    assert ok, detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{RUN_ID}:{seg}"


def test_rewrite_skips_the_ownership_check_on_the_idempotent_same_run_path(tmp_path):
    """The idempotent re-stamp (D9): the draft's CURRENT token already names
    THIS run, so condition 2 is false regardless of
    already_claimed_by_this_run -- must remain a no-op, never a refusal."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    before_bytes = (root / "segments" / f"{seg}.draft.json").read_bytes()

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
        already_claimed_by_this_run=True,
    )
    assert ok, detail
    after_bytes = (root / "segments" / f"{seg}.draft.json").read_bytes()
    assert after_bytes == before_bytes


# ---------------------------------------------------------------------------
# 18. #438 round 4, END TO END: the same defect through the REAL admission
# pipeline and run()'s own wiring of `already_claimed_by_this_run` (never
# reconstructed inside rewrite_draft_dispatch_token(), only threaded through
# from write_claim_record()'s own `published` result -- see that call site's
# comment) -- a unit test of the predicate alone (section 17) cannot catch a
# wiring mistake at the call site; only this can.
# ---------------------------------------------------------------------------

def test_an_old_run_may_not_reclaim_a_segment_a_newer_run_now_owns(tmp_path):
    """THE regression test for the reported defect. RUN_ID claims seg22;
    OTHER_RUN_ID legitimately re-claims it (a genuine re-review -- both
    admissions pass every REAL gate, S1/S2 subprocesses included); RUN_ID
    then resumes and attempts to claim seg22 again. Before this fix: RUN_ID's
    own claim record already exists, so the write hits the "already claimed
    by this run" EEXIST branch, which used to re-stamp the draft
    unconditionally -- RUN_ID silently takes the segment back from
    OTHER_RUN_ID with no warning and nothing else changed. Must now be
    refused, and OTHER_RUN_ID's token and claim record must be untouched."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    # resume_setup.py mints a fresh input.digest for a run BEFORE it ever
    # dispatches (that is true of every real invocation, fresh or resumed --
    # see #409 Step 3's own scan). Without it, a SECOND invocation from a
    # DIFFERENT run id sees RUN_ID's own draft evidence with no digest behind
    # it and refuses on that unrelated gate before this test's own subject
    # is ever reached.
    make_run_dir(root, RUN_ID)
    first = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    draft_after_first = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert draft_after_first["dispatch_token"] == f"{RUN_ID}:{seg}"

    make_run_dir(root, OTHER_RUN_ID)
    second = run_select(root, "--from-converged", seg, "--run-id", OTHER_RUN_ID, "--run-resume", "false")
    assert second.returncode == 0, (
        f"OTHER_RUN_ID's own fresh re-claim over seg22 (a genuine re-review) must succeed\n"
        f"stdout={second.stdout!r} stderr={second.stderr!r}"
    )
    draft_after_second = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert draft_after_second["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"
    other_marker = root / "runs" / OTHER_RUN_ID / f".claimed.{seg}"
    other_record_before = other_marker.read_bytes()

    # Same precondition sections 10/15 already use for a real resumed run.
    make_run_dir(root, RUN_ID)
    third = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "true")
    assert third.returncode != 0, (
        f"RUN_ID reasserting its OLD authorization must be refused -- {OTHER_RUN_ID!r} "
        f"currently owns this segment\nstdout={third.stdout!r} stderr={third.stderr!r}"
    )
    out = parse_stdout(third)
    assert out["success"] is False
    assert OTHER_RUN_ID in out["error"], out["error"]
    assert seg in out["error"], out["error"]

    draft_after_third = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert draft_after_third["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}", (
        "the refusal must be REAL: OTHER_RUN_ID's token must still be on disk, never "
        "reverted back to RUN_ID's"
    )
    assert other_marker.read_bytes() == other_record_before, (
        "OTHER_RUN_ID's own durable claim record must be untouched by the refused attempt"
    )


def test_fresh_claim_by_a_new_run_succeeds_even_though_the_named_run_still_holds_a_claim(tmp_path):
    """The case a naive 'refuse on any live foreign claim' predicate would
    have broken, proven through the real pipeline: RUN_ID's own claim record
    for seg22 is never released, so it stays live forever. OTHER_RUN_ID's
    FIRST, genuinely fresh claim over the same segment -- the ordinary
    new-owner / re-review transition -- must still succeed."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    # See the sibling test above for why this precedes the FIRST call too:
    # a later, different run id's own invocation otherwise trips over RUN_ID's
    # undocumented dispatch evidence on the unrelated #409 Step 3 gate.
    make_run_dir(root, RUN_ID)
    first = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    assert (root / "runs" / RUN_ID / f".claimed.{seg}").is_file(), (
        "precondition: RUN_ID's own claim record must exist and (by design) is never released"
    )

    make_run_dir(root, OTHER_RUN_ID)
    second = run_select(root, "--from-converged", seg, "--run-id", OTHER_RUN_ID, "--run-resume", "false")
    assert second.returncode == 0, (
        f"OTHER_RUN_ID's FIRST claim of seg22 must succeed even though RUN_ID's own claim "
        f"record for it is still live\nstdout={second.stdout!r} stderr={second.stderr!r}"
    )
    draft = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert draft["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"
