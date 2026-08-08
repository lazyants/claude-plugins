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
re-claim.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

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
    assert claim["cache_key"] == fixture_keys[seg]
    assert claim["pre_claim_review"]["clean"] is True

    marker = root / "runs" / RUN_ID / f".claimed.{seg}"
    assert marker.is_file(), "the durable claim record must actually be written to disk"
    on_disk = json.loads(marker.read_text(encoding="utf-8"))
    assert on_disk["profile"] == "from-converged"
    assert on_disk["run_id"] == RUN_ID


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
# test_sentinel_predicate_is_identical_in_all_four_scripts uses), never
# wired into run_select() end to end: the call site in run() is deliberately
# NOT invoking it yet. D1a is now SETTLED as single-phase (not the two-phase
# split this docstring originally described), and the #409 Step 3
# fresh-evidence fix (section 13 below) closes the hole that ordering
# inversion opened for PRE-EXISTING outside evidence -- but re-wiring this
# call was re-verified empirically (wired in, ran this file's own suite, 5
# tests failed) to STILL self-trip: rewriting the token here mutates the
# draft on disk inside this same single-phase invocation, and the Step 3
# scan later in the same run() call then sees that freshly-rewritten draft
# as new evidence for run_id and the fresh-evidence check refuses its own
# invocation. Same bug class as the original ordering hazard, relocated
# rather than closed by the Step 3 fix alone. See the NOTE comment at the
# (currently unreached) call site in run() for the full account and the
# proposed fix (hoisting the Step 3 evidence scan above the claim block).
# These tests pin the function's OWN contract so it is ready the moment
# that control-flow question is settled and it gets wired in.
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
    ok, detail = mod.rewrite_draft_dispatch_token(seg, root, new_token)
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
    ok, detail = mod.rewrite_draft_dispatch_token(seg, root, new_token)
    assert ok, detail

    after_bytes = (root / "segments" / f"{seg}.draft.json").read_bytes()
    assert after_bytes == before_bytes, (
        "a draft already carrying the target token must be a true no-op -- a re-claim in "
        "the same run must not be mistaken for a second authorization (D9)"
    )


def test_rewrite_draft_dispatch_token_refuses_on_missing_draft(tmp_path):
    root = make_durable_root(tmp_path)
    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token("seg_missing", root, f"{RUN_ID}:seg_missing")
    assert ok is False
    assert detail


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
    bearing that EXACT id (a phantom draft's dispatch_token) -- and no
    input.digest exists for it (the fresh-mint case never writes a digest
    ahead of resume_setup.py actually running). A fresh id colliding with
    pre-existing evidence must be refused, not laundered through by the
    digest resume_setup.py itself just wrote."""
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
