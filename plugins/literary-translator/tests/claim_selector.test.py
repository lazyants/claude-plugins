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
re-claim. Also #438 rounds 4/5: rewrite_draft_dispatch_token()'s refusal of
an OLD run reasserting a SUPERSEDED authorization over a segment a DIFFERENT
run now owns, unit-level (the `claimed_at` age predicate, isolated -- both
directions, a tie, and the crash-retry recovery round 4's own boolean proxy
wrongly refused) and end to end (the actual defect -- claim, re-claim by
another run, then the first run resumes and reclaims -- and the
fresh-claim-over-a-live-foreign-record case a naive predicate would have
broken instead).
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
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
    """P2 shape (POPULATIONS.md): non_converged/reason=cap, no cache_key on the
    fragment at all, stored review clean:false with findings.
    human_escalation -> reachable only via --only-segs.

    The sentinel is ABSENT by default and PRESENT via `sentinel_present=True`.
    Both are real members of this population since #537: a unit that converged,
    went stale when the contract moved and then exhausted its rounds is capped
    AND sentinel-bearing. This docstring used to say the population has NO
    sentinel, which is the premise 1.27.0 refutes."""
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
# 1b. #537 -- the converged-then-staled-then-capped unit. A capped segment MAY
#     carry a .ever_converged sentinel, and before this the intersection was
#     admissible by no profile at all: --from-cap refused on the sentinel,
#     --from-converged on the status and on the reviewed_draft_sha1 the cap
#     write erases, --from-stalled on requiring in_progress. Since assemble.py
#     refuses a book while any unit is not converged, one such unit blocked a
#     whole title.
# ---------------------------------------------------------------------------

def test_from_cap_admits_a_capped_unit_that_had_converged_and_discloses_it(tmp_path):
    """THE #537 population, end to end.

    Two independent things must both hold, and the pre-fix code fails the
    first one, which is what makes this red rather than merely new:

      (i) admission -- the sentinel branch refused every non-ABSENT state, so
          the claim gate rejected the id outright;
      (ii) clearing -- the id lands in `previously_converged` (that list is
          built from sentinel state ALONE), so without D5.2 covering
          --from-cap the unconditional fatal below it fires on this
          invocation's OWN successful admission. A fix that did only (i)
          would still exit non-zero, which is exactly what happened when
          the admission half was patched by itself in the field.

    The disclosure is asserted too, and deliberately as a stderr line rather
    than a JSON field: an operator must not have to diff the sentinel
    directory to discover that a claim admitted a unit which had converged
    once."""
    root = make_durable_root(tmp_path)
    seg = "seg14"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys, sentinel_present=True)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    assert (root / "segments" / f".ever_converged.{seg}").is_file(), (
        "precondition: this test is about a CAPPED unit that carries a sentinel"
    )

    proc = run_select(
        root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false"
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert seg in out["claims"], "the sentinel-bearing capped unit must be ADMITTED"
    assert out["claims"][seg]["profile"] == "from-cap"
    assert seg not in (out["previously_converged"] or []), (
        "D5.2 must clear a --from-cap id too, or the fatal below it fires on this "
        "invocation's own admission"
    )
    assert ".ever_converged sentinel is present" in proc.stderr, (
        f"the admission must be disclosed on stderr; stderr={proc.stderr!r}"
    )
    assert "RE-REVIEW only" in proc.stderr, (
        "the disclosure must say what the claim does and does not authorize"
    )


def test_from_cap_sentinel_disclosure_is_not_printed_when_the_claim_fails(tmp_path):
    """The disclosure names a PUBLISHED admission, so it must not be printed
    from inside the profile branch that merely decided one.

    Reachable shape: two ids requested, the second one refusable. Every claim
    in an invocation is written only after all of them pass, so a refusal on
    the sibling means the first id's record was never published -- and a
    disclosure printed at decision time would announce an admission that did
    not happen.

    Mutation that must turn this red: print inside the sentinel branch of
    evaluate_claim_admission() (where the local field patch printed it)
    instead of beside D9's post-publication disclosure."""
    root = make_durable_root(tmp_path)
    good, bad = "seg14", "seg22"
    fixture_keys = {}
    build_from_cap_segment(root, good, fixture_keys, sentinel_present=True)
    # A second --from-cap id whose ledger status is wrong for the profile:
    # refused by name, and its refusal fatals the whole invocation.
    build_from_cap_segment(root, bad, fixture_keys, ledger_status="converged", ledger_reason=None)
    write_manifest(root, [good, bad])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(
        root, "--only-segs", f"{good},{bad}", "--from-cap", f"{good},{bad}",
        "--run-id", RUN_ID, "--run-resume", "false",
    )
    assert proc.returncode != 0, "precondition: the sibling must make the invocation refuse"
    assert not (root / "runs" / RUN_ID / f".claimed.{good}").exists(), (
        "precondition: no claim record may be published when the invocation refuses"
    )
    assert ".ever_converged sentinel is present" not in proc.stderr, (
        f"an admission that never happened must not be announced; stderr={proc.stderr!r}"
    )


def test_from_cap_overlap_with_allow_retranslate_converged_is_rejected_outright(tmp_path):
    """D5.3 for --from-cap (#537). The overlap guard defines its population by
    PROFILE, and --from-cap used to be excluded from it on the same false
    premise -- "its ids never reach previously_converged". Once a
    sentinel-bearing capped unit is admissible, they do, and leaving the guard
    alone would mean this contradictory pair of flags -- which the driver
    forwards verbatim -- stopped being rejected for exactly the population the
    same change admits.

    The claimed id still could not be TRANSLATED (that refusal is
    profile-independent), so what this pins is narrower and worth naming:
    the pre-write refusal itself.

    WHAT MAKES IT RED, precisely -- the obvious statement is wrong and was
    corrected in review. A FULL revert also exits non-zero, but on the
    ADMISSION gate (the sentinel refusal that #537 removes), not on D5.3; the
    assertions that distinguish the two are `rejected outright` in the error
    and the absent claim record. So this test's real target is the
    ADMISSION-ONLY partial fix -- widening the sentinel condition while
    leaving D5.3's profile set alone -- which is exactly the shape the field
    patch had, and which exits 0 here, writes the record and re-stamps the
    draft."""
    root = make_durable_root(tmp_path)
    seg = "seg14"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys, sentinel_present=True)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(
        root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID,
        "--run-resume", "false", "--allow-retranslate-converged",
    )
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    assert seg in out["error"]
    assert "rejected outright" in out["error"].lower()
    assert not (root / "runs" / RUN_ID / f".claimed.{seg}").exists(), (
        "an overlap must write NOTHING -- the guard's whole value here is that it "
        "refuses BEFORE the record and the token are published"
    )


def test_an_unreadable_sentinel_is_still_refused_under_from_cap(tmp_path):
    """#537 widened --from-cap to PRESENT, not to 'anything that is not
    absent'. AMBIGUOUS means the sentinel could not be READ, which is evidence
    of nothing and must not become admissible because its neighbour did.

    A DIRECT evaluate_claim_admission() call, not run_select(): a dangling
    symlink at the sentinel path aborts the whole invocation at the run-level
    ambiguous-sentinel fatal long before any claim gate runs (that path is
    already owned by tests/select_segments.test.py), so an end-to-end test
    here would be green on the unfixed code and would never execute this
    branch at all."""
    root = make_durable_root(tmp_path)
    seg = "seg14"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    sentinel = root / "segments" / f".ever_converged.{seg}"
    sentinel.symlink_to(root / "segments" / "no-such-target")
    assert not sentinel.exists() and sentinel.is_symlink(), (
        "precondition: a DANGLING link -- exists() follows it and reports False"
    )

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    args = argparse.Namespace(run_id=RUN_ID, durable_root=str(root), plugin_root=None)
    record = {"status": "non_converged", "reason": "cap"}

    ok, reasons, _extras = mod.evaluate_claim_admission(
        seg, mod.CLAIM_PROFILE_FROM_CAP, record, dirs, {}, args
    )
    assert ok is False, "an unreadable sentinel must never be admitted"
    joined = " | ".join(reasons)
    assert "unreadable sentinel is not evidence" in joined, joined
    assert "ambiguous" in joined.lower(), joined


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
    # Was `"non_converged" in error or "sentinel" in error` until #537. This
    # fixture is P1-shaped, so it carries a sentinel, and the second arm used
    # to be satisfied by --from-cap's own sentinel refusal. That refusal is
    # gone for a PRESENT sentinel, leaving a one-armed OR that would survive
    # the STATUS gate being removed if any unrelated text containing
    # "sentinel" ever reached the error. Named directly instead.
    assert "non_converged" in out["error"]


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

@pytest.mark.parametrize(
    "flag_a,flag_b",
    [
        ("--from-converged", "--from-cap"),
        ("--from-cap", "--from-stalled"),
        ("--from-converged", "--from-stalled"),
    ],
    ids=["cap-converged", "cap-stalled", "converged-stalled"],
)
def test_id_named_under_both_profiles_is_fatal(tmp_path, flag_a, flag_b):
    # The collision check in parse_claim_requests() is purely syntactic --
    # which FLAGS named this id, never which population it actually
    # belongs to (see the cap/converged case above, which already names a
    # from-converged-shaped fixture under --from-cap too). So one fixture
    # shape suffices for all three pairs; what is under test is that the
    # refusal names the SPECIFIC two conflicting flags for THIS pair, not
    # a generic "named twice".
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(
        root, flag_a, seg, flag_b, seg, "--run-id", RUN_ID, "--run-resume", "false"
    )
    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert seg in out["error"]
    assert flag_a in out["error"] and flag_b in out["error"], out["error"]
    # The OTHER profile's flag must not appear -- a refusal naming all
    # three would satisfy this assert with a generic "more than one"
    # message that never actually distinguishes the pair.
    other = ({"--from-converged", "--from-cap", "--from-stalled"} - {flag_a, flag_b}).pop()
    assert other not in out["error"], out["error"]


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
    profile is not a formality: each profile is a closed condition list
    over a different population. Naming another one is a NEW authorization,
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
# alone -- and used to re-stamp the draft either way.
#
# ROUND 5: round 4's own fourth condition (`already_claimed_by_this_run`, a
# boolean proxy for "does this run's own record already exist") turned out
# to be unsound -- see rewrite_draft_dispatch_token()'s own docstring for
# the crash-retry recovery it wrongly refused. Replaced with a direct
# comparison of both sides' `claimed_at`. Unit-level tests of the predicate
# in isolation below, each pinned to exactly one condition; section 18
# proves the same thing end to end, through the real admission pipeline and
# real wall-clock `claimed_at` values.
# ---------------------------------------------------------------------------

def _touch_claim_marker(root, run_id, seg, *, claimed_at=None):
    """A REGULAR file at claimed_path(run_id, seg, runs/), holding a
    minimal but valid JSON object -- enough for BOTH claim_record.py
    readers: classify_claim_record() (lstat only, ignores the body) and
    read_claim_record() (parses the body; needs valid JSON, not a specific
    shape). `claimed_at` is optional and omitted by default for tests that
    only need PRESENCE, never the field itself -- #438 round 5's age check
    reads `claimed_at` specifically through read_claim_record(), and a
    marker written without it must (and does, per
    `_claim_record_claimed_at()`'s own contract) read back as "cannot be
    trusted", not as an error."""
    path = root / "runs" / run_id / f".claimed.{seg}"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"seg": seg, "run_id": run_id}
    if claimed_at is not None:
        body["claimed_at"] = claimed_at
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_rewrite_refuses_reasserting_a_claim_another_run_now_owns(tmp_path):
    """The regression predicate itself, in isolation: the draft's CURRENT
    token names OTHER_RUN_ID, OTHER_RUN_ID's own claim record is live AND
    NEWER than RUN_ID's own (RUN_ID's ORIGINAL claim -- exactly what a
    resumed run's own record still carries). #438 round 5's age check must
    refuse: RUN_ID cannot show its claim over `seg` is more recent than
    OTHER_RUN_ID's.

    MUTATION: change `this_claimed_at > foreign_claimed_at` to `True`
    (this run always wins the age comparison) -- observed RED below (this
    test then fails because the call succeeds and stamps RUN_ID's token)."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    before_bytes = (root / "segments" / f"{seg}.draft.json").read_bytes()
    _touch_claim_marker(root, RUN_ID, seg, claimed_at="2026-01-01T00:00:00Z")
    _touch_claim_marker(root, OTHER_RUN_ID, seg, claimed_at="2026-01-01T00:00:10Z")

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
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


def test_rewrite_allows_a_crash_retry_recovery_when_this_runs_own_claim_is_newer(tmp_path):
    """THE regression round 5 fixes. RUN_ID legitimately took `seg` from its
    live owner OTHER_RUN_ID: RUN_ID's OWN claim record was durably
    published (its `claimed_at` NEWER than OTHER_RUN_ID's), but the process
    crashed -- or its record-directory fsync merely reported failure --
    BEFORE the token rewrite ran, so the draft STILL carries OTHER_RUN_ID's
    token. On retry, RUN_ID's write_claim_record() call hits EEXIST (its own
    record already exists) and this function is called exactly as it would
    be on a genuinely fresh attempt. #438 round 4's proxy
    (`already_claimed_by_this_run=True` on this exact EEXIST branch) refused
    this unconditionally -- the defect this release fixes. Round 5's age
    check must ALLOW: RUN_ID's own claim is provably NEWER than
    OTHER_RUN_ID's.

    MUTATION: hardcode `this_run_is_newer = False` -- observed RED below
    (this test then fails because the call refuses)."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    _touch_claim_marker(root, OTHER_RUN_ID, seg, claimed_at="2026-01-01T00:00:00Z")
    _touch_claim_marker(root, RUN_ID, seg, claimed_at="2026-01-01T00:00:10Z")

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
    )
    assert ok, detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{RUN_ID}:{seg}"


def test_rewrite_refuses_on_a_claimed_at_tie(tmp_path):
    """`claimed_at` is second-resolution ISO8601 (claim_record.py's own
    field), so two claims CAN land in the same second -- and a tie does NOT
    prove this run is the later claimant. RUN_ID's and OTHER_RUN_ID's own
    claim records carry the IDENTICAL claimed_at; the age comparison's
    strict `>` must refuse rather than treat a tie as "newer enough".

    MUTATION: change the age comparison's strict `>` to `>=` -- observed
    RED below (a tie then satisfies `this_run_is_newer` and the call
    succeeds)."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    tied_at = "2026-01-01T00:00:00Z"
    _touch_claim_marker(root, OTHER_RUN_ID, seg, claimed_at=tied_at)
    _touch_claim_marker(root, RUN_ID, seg, claimed_at=tied_at)

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
    )
    assert ok is False, "a claimed_at TIE must not be read as this run being the later claimant"
    assert OTHER_RUN_ID in detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"


def test_rewrite_refuses_when_this_runs_own_claim_record_is_absent(tmp_path):
    """DEFENSIVE, not a reachable production path: rewrite_draft_dispatch_token()'s
    own docstring states that the record-first ordering GUARANTEES this
    run's own claim record for `seg` already exists by the time this call
    runs. This test removes that guarantee by hand to prove the FAILURE
    direction is still safe if it were ever violated -- with no record on
    RUN_ID's own side to read a `claimed_at` off of,
    `_claim_record_claimed_at()` returns None and the comparison cannot
    show RUN_ID is the later claimant, so it must refuse rather than treat
    a missing timestamp as "definitely newer".

    MUTATION: in `_claim_record_claimed_at()`, change the
    `state != claim_record_mod.CLAIM_PRESENT` branch's `return None` to a
    far-future sentinel timestamp -- observed RED below (this run's own
    missing record then reads as infinitely new and the call succeeds)."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    _touch_claim_marker(root, OTHER_RUN_ID, seg, claimed_at="2026-01-01T00:00:00Z")
    # No marker written for RUN_ID -- the guarantee this test deliberately violates.

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
    )
    assert ok is False, (
        "this run cannot PROVE it is the later claimant with no claim record of its own "
        "to read a claimed_at off of -- must refuse, never assume newer"
    )
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"


def test_rewrite_allows_a_fresh_claim_over_a_run_that_still_holds_a_live_claim(tmp_path):
    """The case a NAIVE 'refuse whenever the named run's claim record is
    still live' predicate would break, since a claim record is NEVER
    released: OTHER_RUN_ID's claim record is live but OLDER, and RUN_ID's
    own claim record is NEWER (a genuinely fresh, first-ever claim just
    published) -- the ordinary new-owner transition #438's --from-cap /
    --from-converged re-review exists to authorize. Must succeed even
    though the draft names a run whose own claim record is still very much
    present.

    MUTATION: hardcode `this_run_is_newer = False` -- observed RED below
    (the same mutation the crash-retry test above catches; this test names
    the ORDINARY-handoff case rather than the crash-retry one, and both
    must hold for the same code to be correct)."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    _touch_claim_marker(root, OTHER_RUN_ID, seg, claimed_at="2026-01-01T00:00:00Z")
    _touch_claim_marker(root, RUN_ID, seg, claimed_at="2026-06-01T00:00:00Z")

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
    )
    assert ok, detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{RUN_ID}:{seg}"


def test_rewrite_skips_the_ownership_check_when_the_named_run_never_claimed(tmp_path):
    """The ORDINARY claim: the draft carries the token of the run that
    originally TRANSLATED it, and that run holds no claim record at all
    (claimed_path() -> CLAIM_ABSENT). Condition 3 is false, so the check
    must not fire.

    MUTATION: change `foreign_state != claim_record.CLAIM_ABSENT` to `True`
    unconditionally -- observed RED below (with no `claimed_at` recorded
    anywhere, the forced-open age check cannot show RUN_ID is newer and
    refuses)."""
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
    )
    assert ok, detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{RUN_ID}:{seg}"


def test_rewrite_treats_an_ambiguous_foreign_claim_record_as_still_live(tmp_path):
    """claimed_path() names a DIRECTORY instead of a regular file --
    read_claim_record() reports CLAIM_AMBIGUOUS (by way of
    classify_claim_record() underneath it), never CLAIM_ABSENT, and this
    check's safe direction (per claim_record.py's own module contract) is
    to treat AMBIGUOUS as LIVE: a record this call cannot read cannot be
    ruled out as released, and (#438 round 5) its `claimed_at` cannot be
    trusted either -- `_claim_record_claimed_at()` returns None for
    anything but CLAIM_PRESENT, so the age comparison is automatically
    "not comparable" and refuses. Must refuse, exactly like a confirmed-live
    one, regardless of RUN_ID's own claim state.

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
    )
    assert ok is False, "an AMBIGUOUS foreign claim record must be treated as still live"
    assert OTHER_RUN_ID in detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"


def test_rewrite_skips_the_ownership_check_when_the_token_is_absent(tmp_path):
    """D9's lost-token recovery: the draft's CURRENT dispatch_token is
    missing entirely, so condition 1 (draft_run_id() -> None) is false and
    the whole check is skipped -- unconditionally, since that is exactly
    the shape a resumed run's OWN recovery re-claim takes.

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
    )
    assert ok, detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{RUN_ID}:{seg}"


def test_rewrite_skips_the_ownership_check_on_the_idempotent_same_run_path(tmp_path):
    """The idempotent re-stamp (D9): the draft's CURRENT token already names
    THIS run, so condition 2 is false -- must remain a no-op, never a
    refusal."""
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
    )
    assert ok, detail
    after_bytes = (root / "segments" / f"{seg}.draft.json").read_bytes()
    assert after_bytes == before_bytes


# ---------------------------------------------------------------------------
# 17b. #438 round 5 BLOCKER: `_claim_record_claimed_at()` used to trust any
# non-empty string as `claimed_at` and the age check compared the two sides
# with plain `>`, i.e. LEXICALLY. A malformed-but-non-empty value (a hand
# edit, torn write, or corrupted record) can sort lexically on EITHER side
# of a real ISO8601 timestamp -- "0" sorts BELOW every "20XX-..." string,
# while "z"/"9" sort ABOVE every one of them -- so depending on which side
# carried the malformed value, the OLD comparison could be tricked into
# ALLOWING an overwrite the age check exists to refuse. The fix parses
# `claimed_at` into a real `datetime` (matching `_claim_now_iso8601()`'s own
# format exactly) and returns None for anything that fails to parse, so a
# malformed value collapses to the same "cannot establish" as a missing
# field on EITHER side, and the comparison itself can never be won by
# corrupted text.
# ---------------------------------------------------------------------------

def test_rewrite_refuses_a_foreign_claimed_at_that_would_sort_lexically_older(tmp_path):
    """Foreign malformed value "0" sorts BELOW any real "20XX-..." timestamp
    as plain text, so under the pre-fix lexical `>` this run's own genuine
    claimed_at ("2026-01-01T00:00:00Z") compared as newer than the foreign
    "0" and the rewrite would have been ALLOWED -- silently overwriting a
    segment whose actual foreign claim state could not be established at
    all (the value doesn't even parse). Must refuse.

    MUTATION: revert `_claim_record_claimed_at()` to its pre-fix body
    (`return value if isinstance(value, str) and value else None`, no
    parsing) -- OBSERVED RED: with the mutation applied, this test fails
    because the call succeeds and stamps RUN_ID's token over the malformed
    foreign record."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    _touch_claim_marker(root, OTHER_RUN_ID, seg, claimed_at="0")
    _touch_claim_marker(root, RUN_ID, seg, claimed_at="2026-01-01T00:00:00Z")

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
    )
    assert ok is False, (
        "a foreign claim record whose claimed_at does not parse as a real timestamp must "
        "refuse, never be read as 'older, safe to take'"
    )
    assert OTHER_RUN_ID in detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"


def test_rewrite_refuses_when_this_runs_own_claimed_at_would_sort_lexically_newer_as_z(tmp_path):
    """This run's OWN record carries the malformed value "z", which sorts
    ABOVE every real "20XX-..." timestamp as plain text -- the measured
    example from the #438 round 5 report (`"z" > "2026-01-02T00:00:00Z"` is
    True under Python's string `>`). Under the pre-fix lexical comparison
    this run would have looked infinitely newer than the foreign owner's
    genuine claim and the rewrite would have been ALLOWED, even though this
    run cannot actually establish when (or whether) it claimed at all.
    Must refuse -- 'cannot establish' on this run's own side, exactly like
    a missing record.

    MUTATION: revert `_claim_record_claimed_at()` to its pre-fix body --
    OBSERVED RED: the call then succeeds and RUN_ID's token is stamped over
    OTHER_RUN_ID's live, genuinely-timestamped claim."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    _touch_claim_marker(root, OTHER_RUN_ID, seg, claimed_at="2026-01-01T00:00:00Z")
    _touch_claim_marker(root, RUN_ID, seg, claimed_at="z")

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
    )
    assert ok is False, (
        "this run's own claimed_at not parsing as a real timestamp must refuse, never be "
        "read as 'infinitely new'"
    )
    assert OTHER_RUN_ID in detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"


def test_rewrite_refuses_when_this_runs_own_claimed_at_would_sort_lexically_newer_as_9(tmp_path):
    """The report's OTHER measured example (`"9" > "2026-01-02T00:00:00Z"`
    is True) -- same shape as the "z" case above with a different malformed
    value, confirming the fix is not accidentally keyed to one specific
    string.

    MUTATION: revert `_claim_record_claimed_at()` to its pre-fix body --
    OBSERVED RED, same failure shape as the "z" case."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    _touch_claim_marker(root, OTHER_RUN_ID, seg, claimed_at="2026-01-01T00:00:00Z")
    _touch_claim_marker(root, RUN_ID, seg, claimed_at="9")

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
    )
    assert ok is False
    assert OTHER_RUN_ID in detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"


def test_rewrite_refuses_a_foreign_claimed_at_that_is_prose_not_a_timestamp(tmp_path):
    """"not a date" starts with a lowercase letter, which already sorted
    ABOVE any real "20XX-..." timestamp under the pre-fix lexical `>` --
    so this specific shape refused even before the fix (this run's own
    genuine claimed_at never lexically beat it). Included for the shape
    coverage the #438 round 5 report explicitly asked for ("a malformed
    non-empty string ... must REFUSE, not allow"), not as a mutation
    catcher: reverting `_claim_record_claimed_at()` to its pre-fix body
    does NOT turn this one red, since the lexical comparison already
    refused it by accident. Still must refuse post-fix, and for the
    PRINCIPLED reason (the value fails to parse), not the accidental one."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    _touch_claim_marker(root, OTHER_RUN_ID, seg, claimed_at="not a date")
    _touch_claim_marker(root, RUN_ID, seg, claimed_at="2026-06-01T00:00:00Z")

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
    )
    assert ok is False
    assert OTHER_RUN_ID in detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"


def test_rewrite_refuses_a_foreign_claimed_at_that_is_a_json_number_not_a_string(tmp_path):
    """A `claimed_at` written as a bare JSON number (not a string at all) is
    rejected by the `isinstance(value, str)` guard that predates this fix --
    already correct before this round, and unaffected by the parsing change
    (a non-string never reaches `datetime.strptime()`). Included because the
    #438 round 5 report explicitly listed "a number" among the malformed
    shapes to confirm, not because this fix changed it: reverting
    `_claim_record_claimed_at()` to its pre-fix body does NOT turn this one
    red."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    marker = root / "runs" / OTHER_RUN_ID / f".claimed.{seg}"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"seg": seg, "run_id": OTHER_RUN_ID, "claimed_at": 20260101000000}),
        encoding="utf-8",
    )
    _touch_claim_marker(root, RUN_ID, seg, claimed_at="2026-06-01T00:00:00Z")

    mod = _load_select_segments_module(root)
    ok, detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
    )
    assert ok is False
    assert OTHER_RUN_ID in detail
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"


def test_rewrite_refuses_on_a_tie_permanently_across_a_retry(tmp_path):
    """A `claimed_at` TIE is PERMANENT for the losing run, not something a
    retry can wait out -- confirmed by reading claim_record.py's
    write_claim_record() (O_CREAT|O_EXCL: a claim record is write-once and
    NEVER overwritten) together with run_select()'s own claim loop
    (select_segments.py's "already claimed by this run" branch), which on a
    same-run retry re-reads and reports the EXISTING record verbatim rather
    than writing a fresh one. So RUN_ID's own claimed_at is fixed at its
    FIRST claim forever, and if that fixed value ties OTHER_RUN_ID's, no
    number of retries changes anything on disk for the age check to see.

    Proven here at the rewrite_draft_dispatch_token() level, which is what
    a retry actually re-invokes: called twice in a row against the SAME
    on-disk tie, both calls refuse identically and RUN_ID's own claim
    record is byte-for-byte unchanged in between -- there is no 'the clock
    ticked over' state for a second call to observe."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    draft = clean_draft(seg)
    draft["dispatch_token"] = f"{OTHER_RUN_ID}:{seg}"
    write_draft_doc(root, seg, draft)
    tied_at = "2026-01-01T00:00:00Z"
    _touch_claim_marker(root, OTHER_RUN_ID, seg, claimed_at=tied_at)
    own_marker = _touch_claim_marker(root, RUN_ID, seg, claimed_at=tied_at)
    own_record_before = own_marker.read_bytes()

    mod = _load_select_segments_module(root)
    first_ok, first_detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
    )
    assert first_ok is False, "a claimed_at tie must refuse on the first attempt"
    assert own_marker.read_bytes() == own_record_before, (
        "RUN_ID's own claim record must not be mutated by a refused attempt -- it is "
        "write-once by design, never a place this check may write a fresh timestamp to"
    )

    second_ok, second_detail = mod.rewrite_draft_dispatch_token(
        seg,
        root,
        f"{RUN_ID}:{seg}",
        expected_content_sha1=draft_content_sha1_of(draft),
    )
    assert second_ok is False, (
        "a retry must refuse IDENTICALLY: RUN_ID's own claimed_at can never advance (its "
        "record is write-once), so nothing about the tie changed and there is no "
        "'wait for the clock' recovery"
    )
    assert own_marker.read_bytes() == own_record_before, (
        "a second, refused attempt must still leave RUN_ID's own record untouched"
    )
    on_disk = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert on_disk["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}", (
        "the draft's token must still name OTHER_RUN_ID after BOTH refused attempts"
    )


# ---------------------------------------------------------------------------
# 18. #438 round 4/5, END TO END: the same defect through the REAL admission
# pipeline and real wall-clock `claimed_at` values -- a unit test of the
# predicate alone (section 17, hand-picked timestamps) cannot catch a wiring
# mistake at the call site or a real-clock ordering assumption that only
# holds on paper; only this can.
# ---------------------------------------------------------------------------

def _cross_a_claimed_at_second_boundary():
    """Block until the wall clock's INTEGER second changes. `claimed_at`
    (claim_record.py's own field) is second-resolution ISO8601, and #438
    round 5's ownership-age check treats a TIE as REFUSE -- the safe
    direction, per rewrite_draft_dispatch_token()'s own docstring, but it
    means a SECOND real claim fired within the same wall-clock second as
    the first is refused too, not merely "not yet provably newer". This is
    not hypothetical: two admissions in this same file, each running a
    full subprocess through every real gate, were OBSERVED to land on the
    identical `claimed_at` second on this machine during mutation testing
    of the age comparison (a tie that a strict `>` correctly refused, and
    an injected `>=` mutation correctly revealed by wrongly allowing it).
    Every end-to-end test below that asserts a SECOND claim over a
    still-live FIRST one must SUCCEED therefore calls this between the two
    `run_select()` invocations, to guarantee real-clock separation rather
    than leave the assertion's outcome to how fast this machine happens to
    be on a given run."""
    start = int(time.time())
    while int(time.time()) == start:
        time.sleep(0.02)


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

    # #438 round 5: OTHER_RUN_ID's own claimed_at must be STRICTLY newer than
    # RUN_ID's for this call to succeed (see _cross_a_claimed_at_second_boundary()'s
    # own docstring for why this is not paranoia).
    _cross_a_claimed_at_second_boundary()
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

    # #438 round 5: OTHER_RUN_ID's own claimed_at must be STRICTLY newer than
    # RUN_ID's for this call to succeed (see _cross_a_claimed_at_second_boundary()'s
    # own docstring for why this is not paranoia).
    _cross_a_claimed_at_second_boundary()
    make_run_dir(root, OTHER_RUN_ID)
    second = run_select(root, "--from-converged", seg, "--run-id", OTHER_RUN_ID, "--run-resume", "false")
    assert second.returncode == 0, (
        f"OTHER_RUN_ID's FIRST claim of seg22 must succeed even though RUN_ID's own claim "
        f"record for it is still live\nstdout={second.stdout!r} stderr={second.stderr!r}"
    )
    draft = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert draft["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}"


# ---------------------------------------------------------------------------
# 19. #460 -- evaluate_open_review_loop(): a DIRTY --from-converged review is
# admitted only when the draft's OWNER run (named by its own dispatch_token)
# holds a live, self-consistent, from-converged claim record for this exact
# segment. `clean: true` was the ENTRY condition into the previously-converged
# population, not a standing one -- round 1 of a re-review overwrites the
# stored review with a DIRTY one, and before this fix that closed the door
# behind the operator: the driver's own prescribed fix turn could run, and
# then nothing could dispatch round 2 (the plain path refuses via
# previously_converged, --from-converged refused on the now-dirty review).
# This admits round 2 through the ORDINARY claim path -- authorized by the
# owner's own prior claim record, never by exempting the segment somewhere
# downstream -- so the fresh authorization lands on THIS run, not the owner.
# ---------------------------------------------------------------------------

def write_owner_claim_record(
    root,
    seg,
    *,
    owner_run_id=SOURCE_RUN_ID,
    profile="from-converged",
    seg_field=None,
    run_id_field=None,
):
    """Publish a claim record for `owner_run_id` over `seg`, through the REAL
    claim_record.py writer (build_claim_record() + write_claim_record()) --
    never hand-written JSON, so a passing test proves the record SHAPE
    evaluate_open_review_loop() actually reads rather than a shape this
    suite merely imagines it reads.

    `seg_field`/`run_id_field` default to the record's own true location
    (`seg`/`owner_run_id`) and exist so exactly one test at a time can plant
    a record whose OWN `seg` or `run_id` field disagrees with the path it
    was found at -- evaluate_open_review_loop()'s self-agreement check on
    top of mere presence, the same scoping D5.2 already applies elsewhere in
    this file. `owner_run_id` also picks WHERE the record is written
    (claimed_path()'s own directory), so a test flipping `run_id_field`
    alone plants a self-disagreeing record at the OWNER's true path, while a
    test flipping `owner_run_id` itself would (correctly) publish a
    perfectly self-consistent record the gate cannot find at all -- these
    tests use the former."""
    mod = _load_claim_record_module()
    payload = mod.build_claim_record(
        seg=seg_field if seg_field is not None else seg,
        profile=profile,
        run_id=run_id_field if run_id_field is not None else owner_run_id,
        source_run_id=owner_run_id,
        previous_dispatch_token=None,
        pre_claim_content_sha1=None,
        pre_claim_review=None,
        pre_claim_cache_key=None,
        cache_key_at_claim=None,
        cache_key_moved_fields=[],
        cache_key_movement_machinery_only=None,
        cache_key_note=None,
        operator_invocation=None,
        claimed_at="2026-01-01T00:00:00Z",
    )
    record_path = mod.claimed_path(owner_run_id, seg, root / "runs")
    ok, detail = mod.write_claim_record(record_path, payload)
    assert ok, f"test setup: could not publish the owner's own claim record: {detail}"
    return record_path


def test_dirty_review_is_admitted_when_owner_holds_a_live_from_converged_claim(tmp_path):
    """PERMIT -- the core case #460 exists for. Round 1 of a re-review left
    the stored review dirty, but the draft's owner run (SOURCE_RUN_ID, named
    by its own dispatch_token) already holds a from-converged claim record
    for this exact segment: this run's own --from-converged re-claim must be
    admitted, and the fresh authorization must be granted to THIS run, not
    silently left with the owner's."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys, review_overrides={"clean": False})
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)
    write_owner_claim_record(root, seg)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert seg in out["segs"]
    assert seg not in out["previously_converged"], (
        "an admitted --from-converged claim must clear the gate for itself, same as the "
        "clean-review happy path"
    )
    assert seg in out["claims"]
    claim = out["claims"][seg]
    assert claim["run_id"] == RUN_ID, "the fresh claim is granted to THIS run, not the owner"
    assert claim["source_run_id"] == SOURCE_RUN_ID


def test_dirty_review_refused_when_owner_holds_no_claim_record(tmp_path):
    """REFUSE -- the default gate. Nobody ever claimed this segment, so a
    dirty review is just a dirty review: exactly as true of a segment nobody
    claimed as it is of one mid re-review, and this project never opened a
    loop for it. No claim record is published at all."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys, review_overrides={"clean": False})
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    assert seg in out["error"]
    assert "holds no readable claim record" in out["error"], out["error"]
    assert "state=absent" in out["error"], (
        f"must name the ABSENT state specifically -- distinguishes this refusal from the "
        f"torn-record (AMBIGUOUS) refusal below. Got: {out['error']!r}"
    )


def test_dirty_review_refused_when_owners_claim_was_granted_under_from_cap(tmp_path):
    """REFUSE. The owner's record exists and is perfectly readable, but it
    authorizes a DIFFERENT population for a different reason (D5.2) --
    --from-cap's own re-review loop is not --from-converged's. Must refuse
    on the PROFILE clause specifically, not be swallowed by the "no record"
    reason the previous test exercises."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys, review_overrides={"clean": False})
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)
    write_owner_claim_record(root, seg, profile="from-cap")

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    # The refusal reports BOTH probes -- the draft's owner and, for D9's sake,
    # this run -- so attribution is asserted against the OWNER's clause alone.
    # Asserting over the whole string would pass on the second probe's "no
    # record" reason, which is exactly the swallowing this test exists to stop.
    owner_clause = out["error"].split("; for this run")[0]
    assert "was granted under profile 'from-cap'" in owner_clause, out["error"]
    assert "holds no readable claim record" not in owner_clause, (
        "must refuse on the PROFILE clause, not the presence clause the no-record test covers"
    )


def test_dirty_review_refused_when_the_owner_record_is_only_partially_written(tmp_path):
    """A record carrying the three fields the gate names -- seg, run_id,
    profile -- and none of the other eleven must NOT authorize. Those three are
    the ones a forger or a half-finished write would get right by reading the
    error messages; the full fourteen are what only this project's own claim
    path produces. Hand-written on purpose: the production writer cannot
    produce this shape, which is the point."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys, review_overrides={"clean": False})
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    record_dir = root / "runs" / SOURCE_RUN_ID
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / f".claimed.{seg}").write_text(
        json.dumps({"seg": seg, "run_id": SOURCE_RUN_ID, "profile": "from-converged"}),
        encoding="utf-8",
    )

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    owner_clause = out["error"].split("; for this run")[0]
    assert "is missing" in owner_clause and "fields this project's claim path writes" in owner_clause, (
        out["error"]
    )


def test_a_stale_claim_of_this_run_does_not_authorize_another_owners_dirty_review(tmp_path):
    """The widening a codex review caught, pinned as a refusal.

    This run holding a claim record proves only that it once opened a loop on
    this segment -- never that it opened THIS one. So when the draft carries a
    token naming a DIFFERENT owner, this run's own record must not stand in for
    the owner's: the draft has legitimately moved on, and a record from the
    earlier loop would otherwise authorize the new owner's dirty review with no
    forged file anywhere.

    The second probe exists only for D9's lost-token path (the test below), and
    `lost_token_recovery` is what confines it there."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys, review_overrides={"clean": False})
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)
    make_run_dir(root, RUN_ID)
    # This run holds a full, valid record -- and the draft still names
    # SOURCE_RUN_ID, which holds none.
    write_owner_claim_record(root, seg, owner_run_id=RUN_ID)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert "holds no readable claim record" in out["error"], out["error"]
    assert "for this run" not in out["error"], (
        "the second probe must not even run for a draft that still carries a token"
    )


def test_d9_lost_token_recovery_still_works_when_the_review_came_back_dirty(tmp_path):
    """D9's recovery, in the state #460 introduces: round 1 came back dirty AND
    a fix round dropped the token. `source_run_id` is then recovered from this
    run's own record and names the original TRANSLATION run, which never holds a
    from-converged claim -- so without the second probe this recovery would be
    stranded exactly where the loop is supposed to continue.

    Driven the way the existing D9 tests are: a real first claim, then the token
    dropped, then a real re-claim -- never a hand-assembled record."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    first = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    # Round 1 came back dirty, and the fix round dropped the token.
    write_review(root, seg, {"clean": False, "coverage_ok": True, "findings": [
        {"loc": "PARA:p1", "severity": "medium", "issue": "x", "suggest": "y"}
    ], "draft_sha1": "0" * 40})
    _drop_dispatch_token(root, seg)
    make_run_dir(root, RUN_ID)

    second = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "true")
    assert second.returncode == 0, f"stdout={second.stdout!r} stderr={second.stderr!r}"
    out = parse_stdout(second)
    assert seg in out["segs"]
    assert seg not in out["previously_converged"]
    assert seg in out["claims"]


def test_dirty_review_refused_when_records_own_seg_names_a_different_segment(tmp_path):
    """REFUSE. classify_claim_record()/read_claim_record() establish only "a
    regular file holding a JSON object at the right PATH" -- the record must
    also AGREE with that path. A record whose own `seg` field names
    something else is refused rather than trusted as evidence for THIS
    segment."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys, review_overrides={"clean": False})
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)
    write_owner_claim_record(root, seg, seg_field="some_other_seg")

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    assert "disagrees with its own location" in out["error"], out["error"]
    assert "seg='some_other_seg'" in out["error"], (
        f"must name the WRONG seg the record actually carries. Got: {out['error']!r}"
    )


def test_dirty_review_refused_when_records_own_run_id_names_a_different_run(tmp_path):
    """REFUSE -- the mirror of the seg check above. A record sitting at
    SOURCE_RUN_ID's own path but whose own `run_id` field names a different
    run is the other half of the self-agreement condition, and must be
    told apart from the seg-mismatch refusal by the value the message
    actually names."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys, review_overrides={"clean": False})
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)
    write_owner_claim_record(root, seg, run_id_field=OTHER_RUN_ID)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    assert "disagrees with its own location" in out["error"], out["error"]
    assert f"run_id={OTHER_RUN_ID!r}" in out["error"], (
        f"must name the WRONG run_id the record actually carries. Got: {out['error']!r}"
    )
    assert "seg='some_other_seg'" not in out["error"], (
        "must not be the seg-mismatch refusal's own reason"
    )


def test_dirty_review_refused_when_owners_claim_record_is_torn(tmp_path):
    """REFUSE. A record that classifies PRESENT but does not parse as a JSON
    object is AMBIGUOUS, not PRESENT-with-empty-contents -- the same
    discipline every other reader of claim_record.py applies (see section 14
    above). Hand-written and truncated MID-OBJECT deliberately, rather than
    a bare `{}`: `{}` parses clean as a JSON object with every key simply
    absent, which classify_claim_record() reports PRESENT and
    read_claim_record() reports PRESENT/{} -- so it would fail this test's
    OWN seg/run_id/profile checks for an unrelated reason (all three keys
    missing) and never actually exercise the AMBIGUOUS branch this test
    targets."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys, review_overrides={"clean": False})
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    mod = _load_claim_record_module()
    record_path = mod.claimed_path(SOURCE_RUN_ID, seg, root / "runs")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text('{"seg": "seg22", "profile": "from-conve', encoding="utf-8")

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    assert "holds no readable claim record" in out["error"], out["error"]
    assert "state=ambiguous" in out["error"], (
        f"must name the AMBIGUOUS state specifically -- distinguishes this refusal from the "
        f"no-record (ABSENT) refusal above. Got: {out['error']!r}"
    )


def test_clean_review_still_admitted_without_any_owner_claim_record(tmp_path):
    """UNCHANGED -- guards against the relaxation swallowing the ORIGINAL
    path. A clean:true stored review is admitted exactly as before #460,
    and without evaluate_open_review_loop() ever being consulted: no owner
    claim record is published anywhere in this test, so if the loop check
    were (wrongly) called unconditionally, this admission would refuse for
    lack of one, and this test would catch it."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)  # review clean: True, the default
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert seg in out["segs"]
    assert seg not in out["previously_converged"]
    assert seg in out["claims"]
    assert out["claims"][seg]["profile"] == "from-converged"


# ---------------------------------------------------------------------------
# 19b. evaluate_open_review_loop() called DIRECTLY, in isolation from the rest
# of evaluate_claim_admission().
#
# The end-to-end tests above prove the whole INVOCATION refuses or admits,
# but a refusal can come from any of D2's several independent chokepoints --
# under a mutation that disables the owner-claim check specifically, the
# end-to-end REFUSE tests still went red, but via the *unrelated* #438
# ownership-rewrite guard (a foreign run's own claim record makes
# rewrite_draft_dispatch_token() refuse the re-stamp), not via
# evaluate_open_review_loop() itself. That is a test passing for the wrong
# reason -- a second chokepoint firing is exactly how it happens -- so it
# does not actually pin the predicate's OWN behavior. These tests close that
# by calling the function directly and asserting on its RETURNED pair, with
# no other gate in the path to produce a right-shaped refusal for a
# different reason.
#
# `dirs` is built with resolve_dirs() -- the SAME function run()'s own call
# site uses (`dirs = resolve_dirs(args.durable_root, args.plugin_root)`) --
# rather than a hand-rolled dict shaped to only what this one function
# happens to read today.
# ---------------------------------------------------------------------------

def test_evaluate_open_review_loop_permits_a_valid_owner_claim(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    write_owner_claim_record(root, seg)

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    ok, reason = mod.evaluate_open_review_loop(
        seg, SOURCE_RUN_ID, dirs, expected_profile="from-converged"
    )
    assert ok is True
    assert reason == ""


def test_evaluate_open_review_loop_refuses_when_owner_holds_no_claim_record(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    # deliberately: no write_owner_claim_record() call.

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    ok, reason = mod.evaluate_open_review_loop(
        seg, SOURCE_RUN_ID, dirs, expected_profile="from-converged"
    )
    assert ok is False
    assert "holds no readable claim record" in reason, reason
    assert "state=absent" in reason, reason


def test_evaluate_open_review_loop_refuses_a_from_cap_owner_claim(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    write_owner_claim_record(root, seg, profile="from-cap")

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    ok, reason = mod.evaluate_open_review_loop(
        seg, SOURCE_RUN_ID, dirs, expected_profile="from-converged"
    )
    assert ok is False
    assert "was granted under profile 'from-cap'" in reason, reason
    assert "holds no readable claim record" not in reason, (
        f"must refuse on the PROFILE clause, not the presence clause. Got: {reason!r}"
    )


def test_evaluate_open_review_loop_refuses_a_seg_mismatched_record(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    write_owner_claim_record(root, seg, seg_field="some_other_seg")

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    ok, reason = mod.evaluate_open_review_loop(
        seg, SOURCE_RUN_ID, dirs, expected_profile="from-converged"
    )
    assert ok is False
    assert "disagrees with its own location" in reason, reason
    assert "seg='some_other_seg'" in reason, reason


def test_evaluate_open_review_loop_refuses_a_run_id_mismatched_record(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    write_owner_claim_record(root, seg, run_id_field=OTHER_RUN_ID)

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    ok, reason = mod.evaluate_open_review_loop(
        seg, SOURCE_RUN_ID, dirs, expected_profile="from-converged"
    )
    assert ok is False
    assert "disagrees with its own location" in reason, reason
    assert f"run_id={OTHER_RUN_ID!r}" in reason, reason
    assert "seg='some_other_seg'" not in reason, (
        "must not be the seg-mismatch refusal's own reason"
    )


def test_evaluate_open_review_loop_refuses_a_torn_owner_record(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg22"
    crmod = _load_claim_record_module()
    record_path = crmod.claimed_path(SOURCE_RUN_ID, seg, root / "runs")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text('{"seg": "seg22", "profile": "from-conve', encoding="utf-8")

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    ok, reason = mod.evaluate_open_review_loop(
        seg, SOURCE_RUN_ID, dirs, expected_profile="from-converged"
    )
    assert ok is False
    assert "holds no readable claim record" in reason, reason
    assert "state=ambiguous" in reason, reason


def test_evaluate_open_review_loop_refuses_a_none_or_empty_owner_run_id(tmp_path):
    """The caller-side precondition: `owner_run_id` is whatever S3 parsed out
    of the draft's own dispatch_token (or None, when parsing itself already
    failed upstream) -- never this run's own identity. Neither shape names
    an owner whose claim record could even be looked up."""
    root = make_durable_root(tmp_path)
    seg = "seg22"

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    for bad_owner in (None, ""):
        ok, reason = mod.evaluate_open_review_loop(
            seg, bad_owner, dirs, expected_profile="from-converged"
        )
        assert ok is False
        assert "no owner whose claim could be read" in reason, reason


def test_evaluate_open_review_loop_refuses_a_from_stalled_owner_claim_when_expecting_converged(tmp_path):
    """The #455 half of the generalization: a valid, complete, self-agreeing
    claim record is not enough on its own -- it must have been granted under
    THE CALLER'S OWN profile. A record granted under --from-stalled must not
    let --from-converged's own continuation predicate treat it as its own
    re-review loop, even though every other field agrees. Mutation this test
    exists to catch: hard-coding CLAIM_PROFILE_FROM_CONVERGED back into
    evaluate_open_review_loop() would make this pass for the wrong reason
    only by coincidence -- it would actually make the from-cap sibling above
    (also refused) indistinguishable in cause from this one; the assertion
    on the mismatched profile value is what pins the parameter is genuinely
    read, not merely present in the signature."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    write_owner_claim_record(root, seg, profile="from-stalled")

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    ok, reason = mod.evaluate_open_review_loop(
        seg, SOURCE_RUN_ID, dirs, expected_profile="from-converged"
    )
    assert ok is False
    assert "was granted under profile 'from-stalled'" in reason, reason
    assert "not 'from-converged'" in reason, reason


def test_evaluate_open_review_loop_permits_a_valid_owner_claim_under_from_stalled(tmp_path):
    """The mirror of the happy-path test above, with expected_profile
    generalized to --from-stalled's own value -- pins that the predicate is
    genuinely parameterized rather than only ever exercised with
    'from-converged' (every other test in this section passes that one
    value, which would leave a hard-coded default undetected)."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    write_owner_claim_record(root, seg, profile="from-stalled")

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    ok, reason = mod.evaluate_open_review_loop(
        seg, SOURCE_RUN_ID, dirs, expected_profile="from-stalled"
    )
    assert ok is True
    assert reason == ""


# ---------------------------------------------------------------------------
# 20. The two-owner takeover of sections 17/18, in the ONE state those guards
# cannot see: a draft carrying NO dispatch_token.
#
# #438's `claimed_at` tiebreak reads the incumbent owner OFF the draft's token,
# so it is structurally unreachable once the token is gone -- and a fix round
# that re-emits the draft drops it routinely (draft.schema.json makes the field
# optional). That leaves D9's lost-token recovery as the only gate in the path,
# and it asked exclusively about THIS run: does a readable, self-consistent,
# same-profile claim record exist at runs/<this run>/? Nothing releases a
# claim, so that record is immortal -- "I claimed this once" is a fact that
# never expires, while "I still own it" is a different fact entirely, and the
# recovery treated the first as proof of the second.
#
# The rule that answers it is evaluate_takeover_since_this_claim(), and the
# question it asks is "does anybody own this NOW", not "has anybody ever
# claimed it". The difference is the whole section: records are IMMORTAL, so
# two records for one segment is the DESIGNED residue of every sanctioned
# takeover, and the first version of this guard -- refusing on any foreign
# holder -- refused the rightful CURRENT owner its own recovery. Both
# directions are pinned below, because a rule that only ever refuses is
# indistinguishable from a correct one until something has to be admitted.
#
# Its two refusal clauses are OR-ed and are pinned SEPARATELY: the successor
# test (a foreign record whose `previous_dispatch_token` names this run) and
# the `claimed_at` comparison. They are reachable together in the ordinary
# A->B sequence, so a test that only staged that one would leave a mutant
# deleting either clause green.
#
# Every end-to-end test here drives the REAL select_segments.py and builds
# EVERY claim by actually running the selector, because the defect is in what
# one gate knows about another run's evidence; a test that hand-assembles the
# records asserts the arrangement it invented rather than the one the claim
# path produces. The one unit-level test at the end is marked as such and
# exists to reach a state the real selector cannot produce.
#
# NO FIXTURE HERE CAN TIE. `_cross_a_claimed_at_second_boundary()` runs between
# every pair of claims, and -- the part worth stating, because it is what makes
# the guarantee load-bearing rather than hopeful -- each staged claim asserts
# `returncode == 0`. A tie is REFUSED by rewrite_draft_dispatch_token()'s own
# tiebreak, so a same-second pair fails LOUDLY at the staging assertion instead
# of quietly rerouting the test to a different clause than the one it names.
# ---------------------------------------------------------------------------

# A third claimant, for staging a takeover CHAIN. Its id sorts BETWEEN RUN_ID
# and OTHER_RUN_ID while it claims LAST, which is deliberate: run ids are
# `[A-Za-z0-9][A-Za-z0-9._-]*` (no timestamp shape is enforced), the guard
# enumerates runs/ in sorted order, and sort order therefore has nothing to do
# with recency. Staging a chain whose alphabetical order disagrees with its
# claim order is what lets one test reach the `claimed_at` clause without the
# successor clause firing first on an earlier-sorting entry.
THIRD_RUN_ID = "20260810T120000Z"

def test_lost_token_recovery_refuses_when_a_newer_run_has_since_claimed(tmp_path):
    """The takeover #438 closed, surviving in D9's lost-token state. RUN_ID
    claims seg22; OTHER_RUN_ID legitimately re-claims it (both through every
    real gate); OTHER_RUN_ID's review comes back dirty and its fix round
    re-emits the draft WITHOUT the token; RUN_ID then resumes and asks for the
    sanctioned recovery. Its own record from step 1 still exists and still
    passes every condition the recovery used to check -- readable,
    self-describing, same profile -- so before this fix the recovery stamped
    the draft back to RUN_ID and handed it OTHER_RUN_ID's live review loop,
    silently, with the hand edit that loop is protecting still on disk.

    Why the draft must come out UNSTAMPED rather than merely "not claimed":
    the refusal is what tells the operator the ownership needs resolving by
    hand, and a token quietly restored to the wrong run would make the next
    dispatch look authorized to every downstream chokepoint."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    # Same #409 Step 3 precondition sections 17/18 document: a real invocation
    # always has a digest behind it, and without one a LATER run id trips over
    # RUN_ID's dispatch evidence on that unrelated gate before reaching this
    # test's own subject.
    make_run_dir(root, RUN_ID)
    first = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    run_marker = root / "runs" / RUN_ID / f".claimed.{seg}"
    run_record_before = run_marker.read_bytes()

    # OTHER_RUN_ID's claimed_at must be STRICTLY newer than RUN_ID's for this
    # legitimate re-claim to be admitted -- see _cross_a_claimed_at_second_boundary().
    _cross_a_claimed_at_second_boundary()
    make_run_dir(root, OTHER_RUN_ID)
    second = run_select(root, "--from-converged", seg, "--run-id", OTHER_RUN_ID, "--run-resume", "false")
    assert second.returncode == 0, (
        f"OTHER_RUN_ID's own re-claim over seg22 (a genuine re-review) must succeed\n"
        f"stdout={second.stdout!r} stderr={second.stderr!r}"
    )
    other_marker = root / "runs" / OTHER_RUN_ID / f".claimed.{seg}"
    other_record_before = other_marker.read_bytes()

    # OTHER_RUN_ID's fix round rewrites the draft and does not preserve the
    # token -- the event draft_ready.py's own claim note describes, and the
    # only way to reach the recovery at all.
    _drop_dispatch_token(root, seg)

    make_run_dir(root, RUN_ID)
    third = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "true")
    assert third.returncode != 0, (
        f"RUN_ID recovering a token-less draft {OTHER_RUN_ID!r} has since claimed must be "
        f"refused\nstdout={third.stdout!r} stderr={third.stderr!r}"
    )
    out = parse_stdout(third)
    assert out["success"] is False
    # Asserted on the SUCCESSOR clause specifically. D2 reports every failure
    # for the pass together, so a bare "it refused" would not distinguish this
    # from any other condition the fixture might also fail -- and this sequence
    # must be caught by the fact B RECORDED (it replaced this run's token),
    # never by the timestamp comparison, which is second-resolution and could
    # not decide a takeover that happened inside one second.
    assert f"run {OTHER_RUN_ID!r} took {seg!r} over FROM this run" in out["error"], out["error"]
    assert "records this run's token as the one it replaced" in out["error"], out["error"]

    draft_after = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert "dispatch_token" not in draft_after, (
        "a refused recovery must leave the draft exactly as the fix round left it -- "
        "never stamped back to the run that lost the segment"
    )
    assert other_marker.read_bytes() == other_record_before, (
        "OTHER_RUN_ID's durable claim record must be untouched by the refused attempt"
    )
    assert run_marker.read_bytes() == run_record_before, (
        "and RUN_ID's own record must not be rewritten either -- a refusal changes "
        "nothing, so a later hand resolution reads the original evidence"
    )


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permissions, so runs/ cannot be made unlistable",
)
def test_lost_token_recovery_refuses_when_the_runs_directory_cannot_be_listed(tmp_path):
    """COULD-NOT-LOOK is not NOBODY-HOLDS-IT. A runs/ that is not READABLE
    refuses `iterdir()` while every `.claimed.<seg>` inside it stays reachable
    BY PATH -- so any foreign claim is fully in force
    and merely invisible to the enumeration. Reporting that as "no foreign
    claim" would hand back exactly the permission the enumeration exists to
    withhold, and it is the fail-open that hides in review because absence and
    failure print identically.

    Staged with RUN_ID's own record as the only claim on disk, so no run has
    in fact taken the segment over and the only foreign-claim answer available
    is the AMBIGUOUS one -- which is what makes the assertion specific to the
    could-not-look branch rather than to any holder the enumeration found."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    make_run_dir(root, RUN_ID)
    first = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    _drop_dispatch_token(root, seg)
    make_run_dir(root, RUN_ID)

    # 0o333, not 0o111: this pass still materializes runs/ledger.json on its
    # way to the claim block, so the directory has to stay WRITABLE or the
    # refusal comes from ledger_merge.py long before the recovery is reached.
    # Write+search without read is exactly the shape that breaks iterdir()
    # while leaving every .claimed.<seg> inside reachable by path.
    runs = root / "runs"
    os.chmod(runs, 0o333)
    try:
        assert (runs / RUN_ID / f".claimed.{seg}").is_file(), (
            "fixture precondition: this run's own record must still be reachable BY PATH, "
            "or this test proves something other than an enumeration failure"
        )
        proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "true")
    finally:
        os.chmod(runs, 0o755)  # restore so tmp_path cleanup can remove it

    assert proc.returncode != 0, (
        f"an unlistable runs/ means ownership could not be established, not that nobody "
        f"owns the segment\nstdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    out = parse_stdout(proc)
    assert out["success"] is False
    assert f"the runs directory {runs} could not be listed" in out["error"], out["error"]
    assert "could-not-look is not nobody-did" in out["error"], out["error"]

    draft_after = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert "dispatch_token" not in draft_after, (
        "a refused recovery must not stamp the draft anyway"
    )


def test_lost_token_recovery_refuses_when_a_runs_entry_cannot_be_stat_d(tmp_path):
    """THE OTHER HALF OF THE PAIR ABOVE, and NOT a duplicate of it: that test
    breaks the LISTING, this one breaks the PER-ENTRY STAT. `runs/` here is an
    ordinary readable directory and `iterdir()` returns every run; what fails
    is establishing what ONE listed entry actually is. The two land on
    different branches with different reasons, and each asserts its own, so
    neither can silently answer for the other.

    The branch exists because `Path.is_dir()` SWALLOWS the stat error and
    answers False -- so an `except OSError` wrapped around it never fires, and
    "I could not look" is delivered in the same word as "it is not a run". The
    enumeration then skips the entry and can report the segment CLEAR while a
    foreign holder sits behind an entry nobody could read. Absence and failure
    printing identically, one directory level down from the case above.

    STAGED WITH A SELF-REFERENTIAL SYMLINK rather than the permission shape
    (`runs/` at 0o444, readable but not searchable) that the review reported.
    Both produce exactly the defect -- `is_dir()` False, `os.stat()` raising --
    but 0o444 is NOT reachable through the real selector: the same pass
    materializes runs/ledger.json, so a `runs/` without write+search fails in
    ledger_merge.py long before the claim block, which is measured, not
    assumed. A symlink loop leaves `runs/` fully intact and breaks exactly one
    entry, so the refusal can only come from the branch under test. It also
    needs no root guard: ELOOP is not a permission and root gets it too."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    make_run_dir(root, RUN_ID)
    first = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    _drop_dispatch_token(root, seg)
    make_run_dir(root, RUN_ID)

    # A well-formed run id (so it is not skipped by name) that sorts AFTER
    # RUN_ID, pointing at itself.
    unstattable = root / "runs" / "20260812T000000Z"
    unstattable.symlink_to(unstattable)

    assert unstattable.name in [p.name for p in (root / "runs").iterdir()], (
        "fixture precondition: the listing must SUCCEED and include this entry -- a "
        "listing that failed would put this test on the branch above instead"
    )
    assert unstattable.is_dir() is False, (
        "fixture precondition: is_dir() must answer False on the very entry it cannot "
        "stat -- that indistinguishability IS the defect, and without it this test is "
        "staging something else"
    )

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "true")
    assert proc.returncode != 0, (
        f"an entry that cannot be stat'd means ownership could not be established, not "
        f"that the entry is harmless\nstdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    out = parse_stdout(proc)
    assert out["success"] is False
    assert f"entry {unstattable} under {root / 'runs'} could not be stat'd" in out["error"], (
        out["error"]
    )
    assert "could not be listed" not in out["error"], (
        f"this test is void if the LISTING branch answered instead -- the two cases are "
        f"only distinguishable by their reasons: {out['error']}"
    )

    draft_after = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert "dispatch_token" not in draft_after, (
        "a refused recovery must not stamp the draft anyway"
    )


def test_an_unstattable_runs_entry_refuses_only_the_segment_that_consults_it(tmp_path):
    """PER-ID ISOLATION, which the admission batch states as a property and
    this guard is in a position to break. `runs/` is process-wide, not
    per-segment, so one broken entry there is the obvious candidate for a
    fault that escapes the id it belongs to and takes the pass down with it.

    Two segments in ONE invocation. Only seg22 loses its token, so only seg22
    reaches evaluate_takeover_since_this_claim() and meets the unstattable
    entry; seg14 keeps its token and never enumerates runs/ at all. The batch
    refuses -- an admission pass grants all or nothing, per D2/D5 -- but the
    REASON must be attributed to seg22 alone, and seg14 must be absent from
    claim_failures rather than carried down with it.

    That seg14 is evaluated and reported at all is the second half of the
    assertion: a fault that escaped as an exception would abort the pass and
    there would be no per-id report to read."""
    root = make_durable_root(tmp_path)
    lost, intact = "seg22", "seg14"
    fixture_keys = {}
    build_from_converged_segment(root, lost, fixture_keys)
    build_from_cap_segment(root, intact, fixture_keys)
    write_manifest(root, [lost, intact])
    write_fixture_cache_keys(root, fixture_keys)

    make_run_dir(root, RUN_ID)
    first = run_select(
        root, "--only-segs", f"{lost},{intact}",
        "--from-converged", lost, "--from-cap", intact,
        "--run-id", RUN_ID, "--run-resume", "false",
    )
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    intact_marker = root / "runs" / RUN_ID / f".claimed.{intact}"
    intact_record_before = intact_marker.read_bytes()

    _drop_dispatch_token(root, lost)
    make_run_dir(root, RUN_ID)
    unstattable = root / "runs" / "20260812T000000Z"
    unstattable.symlink_to(unstattable)

    proc = run_select(
        root, "--only-segs", f"{lost},{intact}",
        "--from-converged", lost, "--from-cap", intact,
        "--run-id", RUN_ID, "--run-resume", "true",
    )
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    assert "1 of 2 requested claim(s) refused admission" in out["error"], (
        f"ONE of the two failed -- a count that said 2 would mean the broken entry "
        f"escaped the segment that consulted it: {out['error']}"
    )
    assert list(out["claim_failures"]) == [lost], (
        f"the refusal must be attributed to the segment that enumerated runs/, and to "
        f"no other: {out['claim_failures']}"
    )
    assert "could not be stat'd" in out["claim_failures"][lost][0], out["claim_failures"]
    assert intact_marker.read_bytes() == intact_record_before, (
        "the untouched segment's own durable record must survive the refused batch "
        "byte for byte"
    )


def test_lost_token_recovery_by_the_current_owner_after_an_earlier_run_also_claimed(tmp_path):
    """The MIRROR of the takeover test, and the case that decides whether the
    ownership rule is drawn in the right place. Same first three steps -- A
    claims, B legitimately re-claims, B's fix round drops the token -- but the
    run that resumes is B, the RIGHTFUL CURRENT OWNER recovering its own loop.

    A's record is still on disk, because nothing releases a claim: a second
    record for one segment is the DESIGNED steady state after any legitimate
    re-review, and sections 17/18 stage it deliberately. So "any other run
    holds a record" is a condition that is permanently true for every segment
    that has ever changed hands -- which would make this recovery, the one the
    release exists to enable, unreachable for exactly the segments most likely
    to need it.

    THE REGRESSION GUARD FOR THE ANY-HOLDER RULE, which shipped briefly and
    would have broken this release's own headline recovery: B's admission here
    is the only assertion in the section that a refuse-everything guard cannot
    satisfy. Restore that rule and this test is the one that goes red."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    make_run_dir(root, RUN_ID)
    first = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    _cross_a_claimed_at_second_boundary()
    make_run_dir(root, OTHER_RUN_ID)
    second = run_select(root, "--from-converged", seg, "--run-id", OTHER_RUN_ID, "--run-resume", "false")
    assert second.returncode == 0, (
        f"OTHER_RUN_ID's own re-claim over seg22 (a genuine re-review) must succeed\n"
        f"stdout={second.stdout!r} stderr={second.stderr!r}"
    )
    other_marker = root / "runs" / OTHER_RUN_ID / f".claimed.{seg}"
    other_record_before = other_marker.read_bytes()

    # OTHER_RUN_ID's OWN fix round drops the token from its OWN draft.
    _drop_dispatch_token(root, seg)

    make_run_dir(root, OTHER_RUN_ID)
    third = run_select(root, "--from-converged", seg, "--run-id", OTHER_RUN_ID, "--run-resume", "true")
    assert third.returncode == 0, (
        f"the CURRENT owner must be able to recover its own token-less draft -- an earlier "
        f"run's never-released record is not a competing claim\n"
        f"stdout={third.stdout!r} stderr={third.stderr!r}"
    )
    out = parse_stdout(third)
    assert seg in out["claims"], out

    restamped = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert restamped["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}", (
        "the recovery's whole point is that the draft ends up stamped again, to its "
        "current owner"
    )
    assert other_marker.read_bytes() == other_record_before, (
        "a recovery re-establishes a token; it must not rewrite the durable record"
    )


def test_lost_token_recovery_refuses_on_claimed_at_alone_without_a_successor_record(tmp_path):
    """The `claimed_at` clause with the successor clause held OFF -- otherwise
    the two are only ever reachable together and deleting either one leaves
    every other test in this section green.

    Staged as a CHAIN: RUN_ID claims, OTHER_RUN_ID takes it over, THIRD_RUN_ID
    takes it over from OTHER_RUN_ID. RUN_ID then resumes a token-less draft.
    THIRD_RUN_ID's record cannot convict it by the successor test -- that
    record names OTHER_RUN_ID's token as the one it replaced, not RUN_ID's --
    so the only thing left that can see the takeover is that THIRD_RUN_ID
    claimed later. Which is also the honest shape of the danger: a run two
    steps down a chain is exactly the owner whose claim no single record names.

    THIRD_RUN_ID's id sorts BEFORE OTHER_RUN_ID's while it claims LAST, so the
    guard reaches it first in the sorted enumeration and this test cannot pass
    by accidentally hitting OTHER_RUN_ID's successor record instead. That
    inversion also pins the guard's own reason for examining every holder
    rather than returning on the first."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    make_run_dir(root, RUN_ID)
    first = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    _cross_a_claimed_at_second_boundary()
    make_run_dir(root, OTHER_RUN_ID)
    second = run_select(root, "--from-converged", seg, "--run-id", OTHER_RUN_ID, "--run-resume", "false")
    assert second.returncode == 0, f"stdout={second.stdout!r} stderr={second.stderr!r}"

    _cross_a_claimed_at_second_boundary()
    make_run_dir(root, THIRD_RUN_ID)
    third = run_select(root, "--from-converged", seg, "--run-id", THIRD_RUN_ID, "--run-resume", "false")
    assert third.returncode == 0, (
        f"the third link in the chain must be admitted like any other legitimate "
        f"takeover\nstdout={third.stdout!r} stderr={third.stderr!r}"
    )

    third_record = json.loads(
        (root / "runs" / THIRD_RUN_ID / f".claimed.{seg}").read_text(encoding="utf-8")
    )
    assert third_record["previous_dispatch_token"] == f"{OTHER_RUN_ID}:{seg}", (
        "fixture precondition: the LAST claimant must record OTHER_RUN_ID's token, not "
        "RUN_ID's -- otherwise the successor clause convicts and this test would prove "
        "nothing about the timestamp comparison"
    )

    _drop_dispatch_token(root, seg)
    make_run_dir(root, RUN_ID)
    resumed = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "true")
    assert resumed.returncode != 0, (
        f"RUN_ID sits two steps back in the chain and must not recover the draft\n"
        f"stdout={resumed.stdout!r} stderr={resumed.stderr!r}"
    )
    out = parse_stdout(resumed)
    assert out["success"] is False
    assert f"run {THIRD_RUN_ID!r} claimed {seg!r} at " in out["error"], out["error"]
    assert "the later claim owns the segment" in out["error"], out["error"]
    assert "took 'seg22' over FROM this run" not in out["error"], (
        f"this test is void if the successor clause fired instead: {out['error']}"
    )

    draft_after = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert "dispatch_token" not in draft_after, (
        "a refused recovery must not stamp the draft anyway"
    )


def test_a_same_second_foreign_claim_refuses_rather_than_admitting_an_unprovable_claim(tmp_path):
    """THE TIE, both with and without a successor link. `claimed_at` is
    second-resolution, so two equal stamps prove nothing in either direction --
    and the guard refuses, mirroring rewrite_draft_dispatch_token()'s own
    tiebreak instead of inverting it.

    This is the one arrangement where the comparison's DIRECTION is load
    bearing rather than cosmetic: written as `foreign > this` with no separate
    tie branch, a tie falls through and ADMITS, which is the only path in this
    guard that lets an unprovable claim win. A test that only ever stages
    strictly-ordered stamps cannot tell the two spellings apart.

    UNIT-LEVEL on purpose, because the real selector cannot produce a tie: every
    holder acquired its record while the draft still carried a token, so each
    had to win that same strict tiebreak against the incumbent, and a
    same-second claim is refused there (sections 17/18). A tied pair therefore
    implies a record this project's claim path did not write -- a restored
    backup, a hand edit, a copy from a machine with a different clock -- which
    is exactly the provenance that should not be given the benefit of the
    doubt."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    mod = _load_select_segments_module(root)
    crmod = _load_claim_record_module()

    same_second = "2026-08-09T12:00:00Z"
    this_payload = crmod.build_claim_record(**dict(
        {field: None for field in crmod.CLAIM_RECORD_FIELDS},
        seg=seg, profile="from-converged", run_id=RUN_ID,
        previous_dispatch_token=f"{SOURCE_RUN_ID}:{seg}", claimed_at=same_second))
    # A foreign holder at the SAME second whose own takeover was FROM a third
    # run -- so the successor clause has nothing to bite on.
    foreign_payload = crmod.build_claim_record(**dict(
        {field: None for field in crmod.CLAIM_RECORD_FIELDS},
        seg=seg, profile="from-converged", run_id=OTHER_RUN_ID,
        previous_dispatch_token=f"{THIRD_RUN_ID}:{seg}", claimed_at=same_second))
    tied_path = crmod.claimed_path(OTHER_RUN_ID, seg, root / "runs")
    ok, detail = crmod.write_claim_record(tied_path, foreign_payload)
    assert ok, f"test setup: could not publish the foreign record: {detail}"

    still_ours, reason = mod.evaluate_takeover_since_this_claim(
        seg, RUN_ID, this_payload, root / "runs", crmod)
    assert still_ours is False, (
        f"a tie is not evidence of ownership in either direction, and every other "
        f"cannot-establish branch in this guard refuses. reason={reason!r}"
    )
    assert "the same second as this run's own claim" in reason, reason
    assert "neither claim can be shown to precede the other" in reason, reason
    # A tie never resolves itself -- the two stamps will be equal forever -- so
    # this refusal is one of the states only a human clears, and it has to say
    # which file to look at.
    assert str(tied_path) in reason, (
        f"the tie clause must name the record it could not order against: {reason!r}"
    )
    assert "does not clear on its own" in reason, reason

    # The other half: a SECOND tied holder that DOES name this run's token as
    # the one it replaced is convicted BY NAME, with no timestamp involved --
    # the successor test is checked first precisely because no clock resolution
    # can blur it. Published under THIRD_RUN_ID rather than by rewriting the
    # record above, because write_claim_record() is O_EXCL and refuses to
    # overwrite a live claim; THIRD_RUN_ID also sorts FIRST, so the guard
    # reaches the successor record before the tied one and the clause under
    # test is the one that answers.
    successor_payload = crmod.build_claim_record(**dict(
        {field: None for field in crmod.CLAIM_RECORD_FIELDS},
        seg=seg, profile="from-converged", run_id=THIRD_RUN_ID,
        previous_dispatch_token=f"{RUN_ID}:{seg}", claimed_at=same_second))
    ok, detail = crmod.write_claim_record(
        crmod.claimed_path(THIRD_RUN_ID, seg, root / "runs"), successor_payload)
    assert ok, f"test setup: could not publish the successor record: {detail}"
    still_ours, reason = mod.evaluate_takeover_since_this_claim(
        seg, RUN_ID, this_payload, root / "runs", crmod)
    assert still_ours is False, reason
    assert f"run {THIRD_RUN_ID!r} took {seg!r} over FROM this run" in reason, reason
    assert "the same second" not in reason, (
        f"the successor clause must answer first -- it reads a fact the writer stored, "
        f"while the tie clause only reports that nothing could be established: {reason!r}"
    )


# ---------------------------------------------------------------------------
# 21. CHARACTERIZATION of the two residuals evaluate_takeover_since_this_claim()
# ships DISCLOSED. Its docstring names both; these pin them.
#
# EVERY ASSERTION BELOW DESCRIBES BEHAVIOUR THAT IS WRONG. Neither test is a
# specification -- they exist because a residual that lives only in prose is
# one refactor away from silently becoming a different residual, with nothing
# going red. If a change here makes one of these fail, that is the signal to
# read the docstring and decide deliberately, NOT to "fix" the test to match
# the new output. Closing either state needs a primitive this feature does not
# have (a lock around claim acquisition, or a claim-release), which is why they
# are disclosed rather than closed.
#
# Both are staged as END STATES. Neither races anything: the arrangement a race
# would leave behind is constructed directly, with every record written through
# the REAL claim_record.py writer so what is pinned is the shape the claim path
# actually produces.
# ---------------------------------------------------------------------------

def _claimed_at_plus(iso8601: str, seconds: int) -> str:
    """`claimed_at` shifted by `seconds`, in _claim_now_iso8601()'s own format.
    Parsed and re-emitted rather than string-spliced, so a fixture cannot
    accidentally produce a stamp the reader would reject as unparseable and
    refuse on a branch this section is not about."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(iso8601, fmt) + timedelta(seconds=seconds)).strftime(fmt)


def _record_payload(root, run_id, seg):
    crmod = _load_claim_record_module()
    state, payload, detail = crmod.read_claim_record(
        crmod.claimed_path(run_id, seg, root / "runs"))
    assert state == crmod.CLAIM_PRESENT, f"expected a readable record for {run_id}: {detail}"
    return payload


def test_concurrent_acquisition_can_invert_ownership_disclosed_residual(tmp_path):
    """RESIDUAL 1, the ADMIT direction, and it is WRONG ON PURPOSE.

    The claim record is written BEFORE the draft is re-stamped, and two direct
    `select_segments.py --from-converged --run-id` invocations share no lock.
    So two runs can both read the incumbent's token, both pass the incumbent
    check, and both publish records -- while the one whose record is EARLIER
    installs its token LAST and ends up the actual owner. `claimed_at` order
    and ownership order then disagree, and this guard believes `claimed_at`.

    Staged as that end state: OTHER_RUN_ID owns the draft (its token is on
    disk, written by the real selector) while THIRD_RUN_ID holds a strictly
    LATER record that never re-stamped anything. THIRD_RUN_ID's record names
    RUN_ID's token as what it replaced -- not OTHER_RUN_ID's -- because in the
    race both contenders read the SAME incumbent token, which is exactly why
    the successor clause cannot see this and the timestamp decides.

    Both halves are asserted, because a one-sided assertion would not notice
    the inversion being closed in only one direction: the rightful owner is
    REFUSED, and the run that never owned the segment is ADMITTED and walks
    off with the draft.

    WHY THIS SHIPS: the cost is a stolen review loop, not a lost draft. The
    sentinel and both translate chokepoints still stand between this and a
    retranslation. Reachable only from two concurrent invocations; the
    single-operator sequence this feature documents cannot produce it."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)
    crmod = _load_claim_record_module()

    make_run_dir(root, RUN_ID)
    first = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    # OTHER_RUN_ID takes over from RUN_ID for real: its record and the draft's
    # token are both written by the shipped claim path.
    _cross_a_claimed_at_second_boundary()
    make_run_dir(root, OTHER_RUN_ID)
    second = run_select(root, "--from-converged", seg, "--run-id", OTHER_RUN_ID, "--run-resume", "false")
    assert second.returncode == 0, f"stdout={second.stdout!r} stderr={second.stderr!r}"
    owner_claimed_at = _record_payload(root, OTHER_RUN_ID, seg)["claimed_at"]

    # The contender: a record LATER than the owner's, naming the SAME incumbent
    # the owner replaced, and no re-stamp behind it.
    contender = crmod.build_claim_record(**dict(
        {field: None for field in crmod.CLAIM_RECORD_FIELDS},
        seg=seg, profile="from-converged", run_id=THIRD_RUN_ID,
        previous_dispatch_token=f"{RUN_ID}:{seg}", source_run_id=RUN_ID,
        claimed_at=_claimed_at_plus(owner_claimed_at, 1)))
    ok, detail = crmod.write_claim_record(
        crmod.claimed_path(THIRD_RUN_ID, seg, root / "runs"), contender)
    assert ok, f"test setup: could not publish the contender's record: {detail}"

    draft = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert draft["dispatch_token"] == f"{OTHER_RUN_ID}:{seg}", (
        "fixture precondition: the OWNER is whoever the draft's token names, and this "
        "residual is only interesting while that disagrees with the claimed_at order"
    )

    _drop_dispatch_token(root, seg)

    # (a) The RIGHTFUL owner is refused.
    make_run_dir(root, OTHER_RUN_ID)
    owner_try = run_select(
        root, "--from-converged", seg, "--run-id", OTHER_RUN_ID, "--run-resume", "true")
    assert owner_try.returncode != 0, (
        f"DOCUMENTED-WRONG: the owner is refused here. If this now succeeds the residual "
        f"was closed -- read the docstring before touching this test\n"
        f"stdout={owner_try.stdout!r}"
    )
    out = parse_stdout(owner_try)
    assert f"run {THIRD_RUN_ID!r} claimed {seg!r} at " in out["error"], out["error"]
    assert "the later claim owns the segment" in out["error"], out["error"]

    # (b) The run that never owned it IS admitted, and takes the draft.
    make_run_dir(root, THIRD_RUN_ID)
    contender_try = run_select(
        root, "--from-converged", seg, "--run-id", THIRD_RUN_ID, "--run-resume", "true")
    assert contender_try.returncode == 0, (
        f"DOCUMENTED-WRONG: the contender is admitted here, which is the half that makes "
        f"this an inversion rather than merely a strict refusal\n"
        f"stdout={contender_try.stdout!r} stderr={contender_try.stderr!r}"
    )
    stolen = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert stolen["dispatch_token"] == f"{THIRD_RUN_ID}:{seg}", (
        "the loop is handed to the run that never owned the segment -- the concrete cost "
        "of this residual, and what makes it worth disclosing"
    )


def test_a_record_that_never_became_ownership_strands_the_owner_disclosed_residual(tmp_path):
    """RESIDUAL 2, the REFUSE direction, also WRONG ON PURPOSE.

    A claim record can outlive an attempt that never became ownership:
    write_claim_record() leaves a complete record behind when the directory
    fsync fails (run() appends to `write_failures` and continues -- there is no
    unlink), and the re-stamp that would have followed can fail on its own from
    content drift between admission and staging. The record is well-formed and
    indistinguishable from a successful claimant's, so once the rightful owner
    loses its token this guard refuses it -- permanently, since nothing
    releases a record. The segment is stranded until a human deletes one file,
    and the owner cannot simply re-claim: a token-less draft has no other way
    back in.

    BOTH SHAPES of the abandoned record are pinned, because they are answered
    by DIFFERENT clauses and a mutant deleting either would otherwise leave the
    other's test green:

      * naming the incumbent it meant to replace -> the SUCCESSOR clause;
      * not naming it (the concurrent shape) -> the TIMESTAMP clause.

    WHAT IS ASSERTED ABOUT THE MESSAGE, and why it is not decoration. The
    refusal is the operator's only handle on this state: the draft has no token
    to re-claim with, so unless the message says WHICH FILE to delete, the
    segment is stranded and the refusal reads exactly like a legitimate
    takeover. Both shapes are asserted to name the record path.

    BOTH clauses now say the refusal is permanent, and they DIVERGE on the
    remedy -- deliberately, which is why each shape asserts its own:

      * TIMESTAMP -> names the file AND says to remove it by hand. Safe,
        because a strictly-later record that never re-stamped is the residue
        case and nothing legitimate is destroyed by clearing it.
      * SUCCESSOR -> names the file, says the refusal is permanent, and
        points at recovery UNDER THE HOLDER instead of removal. This clause
        fires on every legitimate takeover too, so "delete the newer record"
        would be the exact move an older run makes to steal a segment back.

    An earlier revision of the timestamp clause said neither, and this test
    pinned that deficiency deliberately so a repair could not land unremarked.
    The repair landed mid-review; these assertions are the re-pin."""
    crmod = _load_claim_record_module()

    # --- shape 1: the abandoned record names the incumbent ------------------
    root = make_durable_root(tmp_path / "names_incumbent")
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    make_run_dir(root, RUN_ID)
    owned = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert owned.returncode == 0, f"stdout={owned.stdout!r} stderr={owned.stderr!r}"
    owner_claimed_at = _record_payload(root, RUN_ID, seg)["claimed_at"]

    abandoned_path = crmod.claimed_path(OTHER_RUN_ID, seg, root / "runs")
    abandoned = crmod.build_claim_record(**dict(
        {field: None for field in crmod.CLAIM_RECORD_FIELDS},
        seg=seg, profile="from-converged", run_id=OTHER_RUN_ID,
        previous_dispatch_token=f"{RUN_ID}:{seg}", source_run_id=RUN_ID,
        claimed_at=_claimed_at_plus(owner_claimed_at, 1)))
    ok, detail = crmod.write_claim_record(abandoned_path, abandoned)
    assert ok, f"test setup: could not publish the abandoned record: {detail}"
    assert json.loads(
        (root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8")
    )["dispatch_token"] == f"{RUN_ID}:{seg}", (
        "fixture precondition: the abandoned claimant never re-stamped, so the RIGHTFUL "
        "owner's token must still be the one on disk"
    )

    _drop_dispatch_token(root, seg)
    make_run_dir(root, RUN_ID)
    stranded = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "true")
    assert stranded.returncode != 0, (
        f"DOCUMENTED-WRONG: the rightful owner is refused by a record that never became "
        f"ownership\nstdout={stranded.stdout!r}"
    )
    out = parse_stdout(stranded)
    assert f"run {OTHER_RUN_ID!r} took {seg!r} over FROM this run" in out["error"], out["error"]
    assert str(abandoned_path) in out["error"], (
        f"the refusal must name the record it turned on, so an operator can see WHICH "
        f"claim is holding the segment: {out['error']}"
    )
    assert "refusal is permanent for this run" in out["error"], (
        f"and it must say the refusal never lifts, because nothing releases a record: "
        f"{out['error']}"
    )
    # NOT removal advice, and that is deliberate rather than an omission: this
    # same clause fires on every LEGITIMATE takeover, where deleting the newer
    # record is exactly how an older run would steal the segment back. So it
    # points at recovery under the holder instead. Asserted in its POSITIVE
    # form -- pinning "no delete advice" as an absence would go red the day
    # someone words it differently while keeping the same decision.
    assert f"recovered under {OTHER_RUN_ID!r}" in out["error"], (
        f"the successor clause must route the operator to recovery under the holder, not "
        f"to deleting a record that is usually a legitimate claim: {out['error']}"
    )

    # --- shape 2: it does not name the incumbent (the concurrent shape) -----
    root2 = make_durable_root(tmp_path / "anonymous")
    fixture_keys2 = {}
    build_from_converged_segment(root2, seg, fixture_keys2)
    write_manifest(root2, [seg])
    write_fixture_cache_keys(root2, fixture_keys2)

    make_run_dir(root2, RUN_ID)
    owned2 = run_select(root2, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert owned2.returncode == 0, f"stdout={owned2.stdout!r} stderr={owned2.stderr!r}"
    owner2_claimed_at = _record_payload(root2, RUN_ID, seg)["claimed_at"]

    anonymous_path = crmod.claimed_path(OTHER_RUN_ID, seg, root2 / "runs")
    anonymous = crmod.build_claim_record(**dict(
        {field: None for field in crmod.CLAIM_RECORD_FIELDS},
        seg=seg, profile="from-converged", run_id=OTHER_RUN_ID,
        previous_dispatch_token=f"{SOURCE_RUN_ID}:{seg}", source_run_id=SOURCE_RUN_ID,
        claimed_at=_claimed_at_plus(owner2_claimed_at, 1)))
    ok, detail = crmod.write_claim_record(anonymous_path, anonymous)
    assert ok, f"test setup: could not publish the abandoned record: {detail}"

    _drop_dispatch_token(root2, seg)
    make_run_dir(root2, RUN_ID)
    stranded2 = run_select(root2, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "true")
    assert stranded2.returncode != 0, (
        f"DOCUMENTED-WRONG: same stranding, reached by the timestamp instead\n"
        f"stdout={stranded2.stdout!r}"
    )
    out2 = parse_stdout(stranded2)
    assert f"run {OTHER_RUN_ID!r} claimed {seg!r} at " in out2["error"], out2["error"]
    assert "the later claim owns the segment" in out2["error"], out2["error"]

    # The operator's only handle on a stranded segment, asserted on the clause
    # that used to carry neither half.
    assert str(anonymous_path) in out2["error"], (
        f"the timestamp clause must name the record to delete -- without it this "
        f"refusal is indistinguishable from a legitimate takeover and the operator has "
        f"nothing to act on: {out2['error']}"
    )
    assert "does not clear on its own" in out2["error"], (
        f"and it must say the state is permanent, because a token-less draft cannot be "
        f"re-claimed to get past it: {out2['error']}"
    )


def test_an_unorderable_own_claimed_at_refuses_and_names_THIS_runs_record(tmp_path):
    """The one refusal in this guard whose remedy is a DIFFERENT FILE from
    every other one's: the problem is in the resuming run's OWN record, not in
    a foreign holder's, so an operator following any other clause's advice
    would delete the wrong file -- and deleting a foreign record here fixes
    nothing while destroying the only evidence of a legitimate takeover.

    Reached whenever this run's own `claimed_at` cannot be parsed into an
    instant: hand-edited, truncated by a partial write, or written by a build
    whose format differed. The guard cannot order ANY holder against a stamp it
    cannot read, so it refuses rather than assume nobody took over -- the same
    direction every other cannot-establish branch takes.

    Staged by corrupting only that one field of a record the REAL claim path
    wrote, so everything else about the record stays exactly as shipped and the
    refusal can only come from the ordering."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    make_run_dir(root, RUN_ID)
    first = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    own_record = root / "runs" / RUN_ID / f".claimed.{seg}"
    payload = json.loads(own_record.read_text(encoding="utf-8"))
    assert payload["claimed_at"], "fixture precondition: the real writer set a claimed_at"
    payload["claimed_at"] = "yesterday afternoon"
    own_record.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    _drop_dispatch_token(root, seg)
    make_run_dir(root, RUN_ID)
    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "true")
    assert proc.returncode != 0, (
        f"an unreadable own claimed_at means no holder can be ordered, which is not the "
        f"same as no holder existing\nstdout={proc.stdout!r}"
    )
    out = parse_stdout(proc)
    assert out["success"] is False
    assert "carries no usable 'claimed_at'" in out["error"], out["error"]
    assert str(own_record) in out["error"], (
        f"THIS RUN's own record is the file to repair, and naming a foreign one here "
        f"would send the operator to delete the wrong evidence: {out['error']}"
    )
    assert "this run's own claim record" in out["error"], (
        f"and it must be identified AS this run's own -- the path alone reads like any "
        f"other clause's: {out['error']}"
    )

    draft_after = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert "dispatch_token" not in draft_after, (
        "a refused recovery must not stamp the draft anyway"
    )


# ---------------------------------------------------------------------------
# 22. #538 -- a dispatch REFUSED on policy must not have mutated the tree.
#
# The three refusals below all fire AFTER the #438 claim block's own
# admission pass. Until 1.36.0 they also fired after its durable WRITES: the
# claim record at runs/<RUN_ID>/.claimed.<seg>, and the draft's own
# dispatch_token re-stamped to <RUN_ID>:<seg>. A refusal whose entire purpose
# is "this dispatch must not happen" therefore left the tree carrying a claim
# for a run that did nothing -- and, because ledger_update.py requires a
# draft's own token to equal expected_draft_token(run_token, seg) on the
# convergence write, it arranged for a LATER round to translate, review and
# come back clean, then fail to record the result.
#
# Each test here asserts THREE things, and the order matters: the specific
# fatal fired (a fixture that reaches an EARLIER gate would satisfy the other
# two vacuously -- an admission refusal never writes anything either), the
# claim record is absent, and the claimed draft's dispatch_token is exactly
# the bytes it carried before the invocation.
# ---------------------------------------------------------------------------

# A run id with real dispatch evidence in this project and no
# runs/<id>/input.digest -- the #409 Step 3 "skipped the gate" population.
UNGATED_RUN_ID = "20260805T000000Z"


def _write_unrelated_draft(root, seg, dispatch_token):
    """A draft nothing in `segs` names, carrying a chosen dispatch_token.

    scan_dispatching_run_ids() walks segments/*.draft.json project-wide and
    does not consult manifest.json, so this is how a fixture supplies Step 3
    evidence WITHOUT putting it on the claimed draft. That separation is the
    whole point: an unsafe or ungated token on the CLAIMED draft is refused
    by evaluate_claim_admission() (S3) before any write, so it would prove
    nothing about what a refusal AFTER the writes leaves behind.
    """
    (root / "segments" / f"{seg}.draft.json").write_text(
        json.dumps({"seg": seg, "dispatch_token": dispatch_token}, ensure_ascii=False),
        encoding="utf-8",
    )


def _assert_claim_left_no_trace(root, seg, expected_token):
    marker = root / "runs" / RUN_ID / f".claimed.{seg}"
    assert not marker.exists(), (
        f"a refused dispatch must leave NO durable claim record, found {marker}"
    )
    draft_after = json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))
    assert draft_after.get("dispatch_token") == expected_token, (
        f"a refused dispatch must leave the draft's own dispatch_token untouched -- "
        f"re-stamping it to this run makes the NEXT round's convergence write fail "
        f"(ledger_update.py's expected_draft_token check). Expected {expected_token!r}, "
        f"got {draft_after.get('dispatch_token')!r}"
    )


def test_previously_converged_refusal_leaves_no_claim_record_and_no_restamped_draft(tmp_path):
    """The claimed id is CLEARED by D5.2, so it cannot raise this refusal
    itself -- an UNCLAIMED previously-converged sibling is what makes the
    gate fire while the claim is admitted."""
    root = make_durable_root(tmp_path)
    fixture_keys = {}

    claimed_seg = "seg22"
    build_from_converged_segment(root, claimed_seg, fixture_keys)
    token_before = f"{SOURCE_RUN_ID}:{claimed_seg}"

    sibling_seg = "seg26"
    sibling_key = make_cache_key(sibling_seg)
    fixture_keys[sibling_seg] = sibling_key
    sibling_draft = clean_draft(sibling_seg)
    sibling_sha1 = draft_content_sha1_of(sibling_draft)
    sibling_draft["dispatch_token"] = f"{SOURCE_RUN_ID}:{sibling_seg}"
    write_segpack(root, sibling_seg, clean_segpack(sibling_seg))
    write_draft_doc(root, sibling_seg, sibling_draft)
    write_fragment(
        root, sibling_seg,
        converged_fragment(
            with_field(sibling_key, "style_contract_hash", "style_contract_hash-OLD"),
            sibling_sha1,
        ),
    )
    mark_ever_converged(root, sibling_seg)

    write_manifest(root, [claimed_seg, sibling_seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", claimed_seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert "previously CONVERGED segment(s) would" in out["error"], (
        f"fixture must reach the previously_converged refusal, not an earlier gate: {out['error']}"
    )
    _assert_claim_left_no_trace(root, claimed_seg, token_before)


def test_unsafe_run_id_refusal_leaves_no_claim_record_and_no_restamped_draft(tmp_path):
    root = make_durable_root(tmp_path)
    fixture_keys = {}

    claimed_seg = "seg22"
    build_from_converged_segment(root, claimed_seg, fixture_keys)
    token_before = f"{SOURCE_RUN_ID}:{claimed_seg}"

    _write_unrelated_draft(root, "seg91", "../escape:seg91")

    write_manifest(root, [claimed_seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", claimed_seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert "do not match the safe RUN_ID shape" in out["error"], (
        f"fixture must reach the unsafe_run_ids refusal, not an earlier gate: {out['error']}"
    )
    _assert_claim_left_no_trace(root, claimed_seg, token_before)


def test_runs_missing_digest_refusal_leaves_no_claim_record_and_no_restamped_draft(tmp_path):
    root = make_durable_root(tmp_path)
    fixture_keys = {}

    claimed_seg = "seg22"
    build_from_converged_segment(root, claimed_seg, fixture_keys)
    token_before = f"{SOURCE_RUN_ID}:{claimed_seg}"

    # UNGATED_RUN_ID deliberately gets no runs/<id>/ directory at all.
    _write_unrelated_draft(root, "seg92", f"{UNGATED_RUN_ID}:seg92")

    write_manifest(root, [claimed_seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", claimed_seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert "without the resume-integrity gate having run for them" in out["error"], (
        f"fixture must reach the runs_missing_digest refusal, not an earlier gate: {out['error']}"
    )
    assert UNGATED_RUN_ID in out["runs_missing_digest"], out["runs_missing_digest"]
    _assert_claim_left_no_trace(root, claimed_seg, token_before)
