"""tests/stale_admission.test.py -- #491 Design B: select_segments.py's
`evaluate_claim_admission()` now admits a --from-converged claim for a
converged/stale unit whose draft was NEVER hand-edited, provided a
CONTENT-affecting cache-key field moved since convergence (the Hebrew
shape -- a style-bible edit touching `style_contract_hash`, no hand-edit at
all). Before this change the comparison
`current_draft_sha1 == reviewed_draft_sha1` was an UNCONDITIONAL refusal;
now it only records a premise, and the actual admit/refuse decision is
made where the cache key is computed, further down in the same function
(the computation itself must not move -- see select_segments.py's own
comment at that site).

Covers plan tests 1-6 only (Design B / select_segments.py's own admission
gate). The Design A carve-out in assemble.py/ledger_merge.py has its own
test file. This file's tests:

  1. The Hebrew shape admits (draft unchanged, style_contract_hash moved).
  2. Machinery-only movement still refuses, with its own pinned reason.
  2b. A real-data mixed shape (from a live-tree census, a different
      affected book): plugin_bundle_hash (machinery) AND used_terms_hash
      (content) move TOGETHER -- must still admit, the row that separates
      "any moved field is machinery-only" from "any moved field is
      content-affecting".
  3. Uncomputable-baseline refusal, both directions: (a) draft unchanged,
     stored cache_key absent/not-a-dict -- refused with the SPECIFIC
     uncomputable-baseline reason, not the generic machinery-only one;
     (b) draft changed, no stored cache_key at all -- still ADMITTED
     (criterion 6: no new baseline requirement on that branch).
  4. Regression fence: the pre-existing draft-CHANGED branch is unaffected
     by this change, in both directions (admits; refuses for the
     pre-existing "no reviewed_draft_sha1 at all" reason).
  5. compute_current_cache_key() is invoked exactly ONCE per claim -- a
     direct evaluate_claim_admission() call (never the end-to-end
     run_select() convention, which legitimately reaches cache_key.py
     three times per claimed id: merge, classification, claim).
  6. Translate stays unreachable for the population this change newly
     admits: the pre-existing #450 capability check
     (claim_capability_refusal_for_translate()) still fires for a
     from-converged-claimed segment even when its draft is structurally
     invalid and derive_next_action() would otherwise route to translate.

Fixture strategy for tests 1-5: the REAL select_segments.py, ledger_merge.py,
draft_ready.py, validate_draft.py, claim_record.py, plus the REAL
assets/schemas/*.schema.json, copied into an isolated durable_root -- the
identical `make_durable_root`/`build_from_converged_segment` convention
tests/claim_selector.test.py already uses (duplicated here rather than
imported, per this project's "tests never import one another" convention).
Only cache_key.py is stubbed. Test 6 duplicates
tests/claim_forces_review_only.test.py's own Section B driver fixture
instead, since it exercises segment_dispatch_driver.py machinery this
file's other tests never touch.
"""
import argparse
import hashlib
import importlib.util
import json
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
TEMPLATES_SRC_DIR = ASSETS_DIR / "templates"

SELECT_SCRIPT_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
DRAFT_READY_SRC = SCRIPTS_SRC_DIR / "draft_ready.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
CLAIM_RECORD_SRC = SCRIPTS_SRC_DIR / "claim_record.py"
DRIVER_SRC = SCRIPTS_SRC_DIR / "segment_dispatch_driver.py"
RESUME_SETUP_SRC = SCRIPTS_SRC_DIR / "resume_setup.py"
LEDGER_UPDATE_SRC = SCRIPTS_SRC_DIR / "ledger_update.py"
DRAFT_SHA1_SRC = SCRIPTS_SRC_DIR / "draft_sha1.py"
MASS_TRANSLATE_TEMPLATE_SRC = TEMPLATES_SRC_DIR / "mass-translate-wf.template.js"

for _src in (
    SELECT_SCRIPT_SRC, LEDGER_MERGE_SRC, DRAFT_READY_SRC, VALIDATE_DRAFT_SRC,
    CLAIM_RECORD_SRC, DRIVER_SRC, RESUME_SETUP_SRC, LEDGER_UPDATE_SRC,
    DRAFT_SHA1_SRC, MASS_TRANSLATE_TEMPLATE_SRC,
):
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

# The three fields select_segments.py's own MACHINERY_ONLY_CACHE_KEY_FIELDS
# names -- restated here rather than imported, matching this project's own
# "no shared lib between self-contained scripts" convention (and this
# file's own "tests never import one another" mirror of it). A drift
# between this list and the production one is exactly what plan test 17
# (in the Design A test file) guards, not this file's job.
MACHINERY_ONLY_CACHE_KEY_FIELDS = frozenset(
    {"plugin_bundle_hash", "schema_hash", "derivation_bundle_hash"}
)

# Same fixture stand-in for cache_key.py tests/claim_selector.test.py and
# tests/ledger_merge.test.py already use -- the real `--seg <id>` -> JSON
# interface, sourced from a test-controlled lookup file.
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
# tests/validate_draft.test.py's DEFAULT_PROFILE, duplicated per house
# convention.
DEFAULT_PROFILE = {
    "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}

FN_PH = "⟦FNREF_1⟧"
V_PH_A = "⟦VERSE_vA⟧"
V_PH_B = "⟦VERSE_vB⟧"

RUN_ID = "20260811T000000Z"
OTHER_RUN_ID = "20260811T010000Z"
SOURCE_RUN_ID = "20260801T090000Z"


# ---------------------------------------------------------------------------
# Fixture harness -- tests 1-5 (select_segments.py's own admission gate)
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path):
    """Isolated durable_root carrying every REAL sibling select_segments.py's
    claim gate shells out to (validate_draft.py, draft_ready.py) or imports
    (claim_record.py), the REAL ledger_merge.py, the REAL schemas, and a
    stubbed cache_key.py -- plus profile.yml and an empty canon.json.
    Verbatim copy of tests/claim_selector.test.py's own make_durable_root()."""
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


def draft_content_sha1_of(doc: dict) -> str:
    """Independent ground-truth reimplementation of draft_content_sha1()
    (projects out dispatch_token, sorted-key compact-separator canonical
    JSON) -- an oracle, deliberately not pinned against production."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    raw = json.dumps(projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def clean_segpack(seg):
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
    source_run_dir=True,
):
    """P1 shape (tests/claim_selector.test.py's own POPULATIONS.md
    reference): converged (or stale) at least once, sentinel present,
    reviewed_draft_sha1 present and (by default) diverged from a hand
    edit, stored review clean:true. `hand_edit=False` produces #491's own
    new population: the current draft is BYTE-IDENTICAL to the converged
    one, so `current_draft_sha1 == reviewed_draft_sha1` -- the draft-
    unchanged branch this file's tests 1-3 exercise.

    Returns {"converged_sha1": ...} so a caller that overwrites the
    fragment afterward (tests 3/4) can still supply a correct
    reviewed_draft_sha1."""
    segpack = clean_segpack(seg)
    write_segpack(root, seg, segpack)

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


def _load_select_segments_module(root):
    """Loads THIS fixture's own copy of select_segments.py as an importable
    module -- byte-identical to the copy run_select() would invoke as a
    subprocess. Verbatim copy of tests/claim_selector.test.py's own
    `_load_select_segments_module()`."""
    path = root / "scripts" / "select_segments.py"
    spec = importlib.util.spec_from_file_location("select_segments_under_test_stale_admission", str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. The Hebrew shape admits.
# ---------------------------------------------------------------------------

def test_from_converged_admits_untouched_draft_with_content_key_drift(tmp_path):
    """The Hebrew shape itself: `converged`, sentinel PRESENT, current
    draft content sha1 == reviewed_draft_sha1 (never hand-edited), stored
    review clean:true, stored cache_key differing from the CURRENT one in
    style_contract_hash only (a style-bible edit).

    Mutation: restore the unconditional "still matches" refusal -> red."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(
        root, seg, fixture_keys,
        hand_edit=False,
        fragment_cache_key_overrides={"style_contract_hash": "style-contract-OLD"},
    )
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert seg in out["segs"]
    assert seg in out["claims"], out
    claim = out["claims"][seg]
    assert claim["profile"] == "from-converged"
    moved = {m["field"]: m for m in claim["cache_key_moved_fields"]}
    assert set(moved) == {"style_contract_hash"}
    assert moved["style_contract_hash"]["pre_claim"] == "style-contract-OLD"
    assert moved["style_contract_hash"]["at_claim"] == fixture_keys[seg]["style_contract_hash"]
    assert claim["cache_key_movement_machinery_only"] is False, (
        "a content-affecting field moved -- this must not read as machinery-only"
    )


# ---------------------------------------------------------------------------
# 2. Machinery-only movement still refuses, with its OWN pinned reason.
# ---------------------------------------------------------------------------

def test_from_converged_refuses_machinery_only_drift_on_untouched_draft(tmp_path):
    """The mirror of test 1: draft unchanged, but the ONLY field that moved
    is plugin_bundle_hash (machinery-only). Refused, and the refusal must
    say assembly no longer requires action -- not the generic 'ok: false'
    a presence-only check would let a mutant hide behind.

    Mutation: drop the MACHINERY_ONLY_CACHE_KEY_FIELDS subtraction -> the
    claim is wrongly ADMITTED (returncode 0), red on both assertions."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(
        root, seg, fixture_keys,
        hand_edit=False,
        fragment_cache_key_overrides={"plugin_bundle_hash": "plugin-bundle-OLD"},
    )
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["success"] is False
    assert "machinery-only" in out["error"], out["error"]
    assert "assembly no longer requires action" in out["error"], out["error"]
    assert not (root / "runs" / RUN_ID / f".claimed.{seg}").exists()


def test_from_converged_admits_mixed_machinery_and_content_drift_on_unchanged_draft(tmp_path):
    """Real-data shape (census of a live affected book): TWO fields moved
    together on an unchanged draft -- plugin_bundle_hash (machinery-only)
    AND used_terms_hash (content-affecting; the live tree's own trigger was
    an ordinary canon edit, not a style-bible one, so this also exercises a
    DIFFERENT content field than test 1's style_contract_hash).

    This is the row that separates "are ALL moved fields machinery-only"
    from "is ANY moved field content-affecting" -- the two formulations
    agree on a pure-content record (test 1) and a pure-machinery one (test
    2), and diverge only here: an implementation that refuses the instant
    ANY moved field is machinery-only (rather than filtering machinery
    fields out first and checking what is left) wrongly refuses this
    claim, even though a genuinely content-affecting field also moved.

    Mutation: filter on "any moved field is machinery-only" instead of
    filtering machinery fields OUT and checking what remains -> the claim
    is wrongly REFUSED (returncode != 0), red."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(
        root, seg, fixture_keys,
        hand_edit=False,
        fragment_cache_key_overrides={
            "plugin_bundle_hash": "plugin-bundle-OLD",
            "used_terms_hash": "used-terms-OLD",
        },
    )
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert seg in out["claims"], out
    claim = out["claims"][seg]
    moved = {m["field"] for m in claim["cache_key_moved_fields"]}
    assert moved == {"plugin_bundle_hash", "used_terms_hash"}
    assert claim["cache_key_movement_machinery_only"] is False, (
        "a content-affecting field (used_terms_hash) is among the moved set -- must not "
        "read as machinery-only just because a machinery field ALSO moved"
    )


# ---------------------------------------------------------------------------
# 3. Uncomputable-baseline refusal, both directions.
# ---------------------------------------------------------------------------

def test_from_converged_refuses_unusable_stored_key_on_unchanged_draft(tmp_path):
    """3(a): draft UNCHANGED, and the ledger record's own stored 'cache_key'
    cannot be used as a baseline -- absent entirely, and separately
    present-but-not-a-dict. Both refuse, and BOTH must name the
    UNCOMPUTABLE BASELINE specifically: the generic "machinery-only /
    nothing moved" wording (test 2's own message) would otherwise mask a
    mutant that read a missing baseline as "nothing moved" and refused for
    the WRONG reason, silently, without any test noticing.

    Mutations, both measured (applied by hand, watched red, restored) --
    real-data motivated: a census of a live tree found a materialized
    record whose only keys are reason/rounds/status/timestamp, no
    'cache_key' at all:
      (i) absent/malformed stored key read as "nothing moved" -> red on
          the specific-reason assertion (still refused, but for the
          WRONG, masking reason -- test 2's own message);
      (ii) absent/malformed stored key read as an EMPTY dict, so a naive
          field-by-field diff sees all 15 CACHE_KEY_FIELDS as "moved" (an
          artifact of the comparison, not real drift) -> the claim is
          wrongly ADMITTED -> red on `ok is False` itself, the fail-open
          direction this branch exists to prevent.

    A DIRECT evaluate_claim_admission() call, not the end-to-end
    run_select() convention: a converged FRAGMENT with a missing/malformed
    cache_key cannot reach select_segments.py's own admission gate through
    the real fragment->merge pipeline at all -- ledger_merge.py's own
    _compute_stale_segments() treats "stored cache_key isn't a dict" as
    its OWN, unrelated reason to flip a segment 'stale' (schema-legal,
    since 'stale' doesn't require cache_key), which then classifies the
    segment 'human_escalation' before any claim gate runs. This test
    hand-builds the `record` evaluate_claim_admission() itself would be
    handed, bypassing that pipeline, to reach the DEFENSIVE fail-closed
    branch directly."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    result = build_from_converged_segment(root, seg, fixture_keys, hand_edit=False)
    write_fixture_cache_keys(root, fixture_keys)
    reviewed_sha1 = result["converged_sha1"]

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    args = argparse.Namespace(run_id=RUN_ID, durable_root=str(root), plugin_root=None)

    base_record = {"status": "converged", "reviewed_draft_sha1": reviewed_sha1}

    for label, cache_key_value, absent in (
        ("absent", None, True),
        ("not-a-dict", "not-a-dict-string", False),
    ):
        record = dict(base_record)
        if not absent:
            record["cache_key"] = cache_key_value

        ok, reasons, extras = mod.evaluate_claim_admission(
            seg, mod.CLAIM_PROFILE_FROM_CONVERGED, record, dirs, {}, args
        )

        assert ok is False, f"[{label}] wrongly admitted: {extras}"
        joined = " | ".join(reasons)
        assert "not a usable stored dict" in joined, f"[{label}] {joined!r}"
        assert "no baseline to compare" in joined, f"[{label}] {joined!r}"
        assert "assembly no longer requires action" not in joined, (
            f"[{label}] refused for the WRONG (masking) reason: {joined!r}"
        )


def test_from_converged_admits_hand_edited_draft_with_no_stored_cache_key(tmp_path):
    """3(b): the DRAFT-CHANGED branch requires NO stored-key baseline --
    unmodified by this change (criterion 6, permissive-only). A
    hand-edited stale segment whose fragment carries no 'cache_key' at
    all (the #491 design note's own '--only-segs force-include' shape)
    must still be admitted.

    Mutation: a baseline required on the draft-changed branch too -> the
    claim is wrongly REFUSED (returncode != 0), red.

    End to end (not a direct call, unlike test 3(a) above): this proves
    the shape is genuinely REACHABLE through the real pipeline. A
    fragment missing 'cache_key' entirely makes ledger_merge.py's own
    _compute_stale_segments() flip it 'stale' (schema-legal -- 'stale'
    never requires cache_key), which classify_segment() in turn reports
    as 'human_escalation' -- unreachable without --only-segs' own
    force-include, exactly the reachability path the #491 design notes
    name (":1451-1458, D3 at :4331-4344")."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    result = build_from_converged_segment(root, seg, fixture_keys, hand_edit=True)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    frag = {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": 1,
        "n_blocks": 3,
        "n_footnotes": 1,
        "n_verses": 2,
        "reviewed_draft_sha1": result["converged_sha1"],
        # deliberately no "cache_key" field at all
    }
    write_fragment(root, seg, frag)

    proc = run_select(
        root, "--only-segs", seg, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false"
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert seg in out["claims"], out
    claim = out["claims"][seg]
    assert claim["pre_claim_cache_key"] is None, (
        "no stored baseline existed -- the reporting field must say so, not silently "
        "substitute an empty dict"
    )
    assert claim["cache_key_note"] is not None


# ---------------------------------------------------------------------------
# 4. Regression fence: the pre-existing draft-CHANGED branch, unaffected.
# ---------------------------------------------------------------------------

def test_draft_changed_admission_still_admits_with_a_normal_baseline(tmp_path):
    """The ordinary hand-edited-draft admission this change must leave
    completely alone. Mutation: any change to the draft-changed branch's
    own admit path -> red."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys, hand_edit=True)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    proc = run_select(root, "--from-converged", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert seg in out["claims"], out


def test_draft_changed_admission_still_refuses_with_no_baseline_field_at_all(tmp_path):
    """The pre-existing refusal for a record with NO reviewed_draft_sha1
    field at all (never possible to tell whether the draft changed) --
    unaffected by #491, which only ever WIDENS what happens once that
    field IS present and equals the current draft's sha1. Mutation: any
    change to this pre-existing refusal -> red.

    A DIRECT evaluate_claim_admission() call: a materialized 'converged'
    entry can never actually lack reviewed_draft_sha1 through the real
    fragment->merge pipeline (ledger-record-base.schema.json requires it
    together with cache_key/rounds/n_blocks/n_footnotes/n_verses whenever
    status=='converged', and ledger_merge.py carries the fragment's own
    fields forward verbatim into the materialized entry) -- this is
    pre-existing DEFENSIVE code for a hand-edited or otherwise malformed
    runs/ledger.json, reached directly rather than through a pipeline that
    cannot actually produce this shape."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    build_from_converged_segment(root, seg, fixture_keys, hand_edit=True)
    write_fixture_cache_keys(root, fixture_keys)

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))
    args = argparse.Namespace(run_id=RUN_ID, durable_root=str(root), plugin_root=None)
    record = {
        "status": "converged",
        "cache_key": fixture_keys[seg],
        # deliberately no "reviewed_draft_sha1" field at all
    }

    ok, reasons, extras = mod.evaluate_claim_admission(
        seg, mod.CLAIM_PROFILE_FROM_CONVERGED, record, dirs, {}, args
    )

    assert ok is False, f"wrongly admitted: {extras}"
    assert "the drift baseline this profile requires" in " | ".join(reasons)


# ---------------------------------------------------------------------------
# 5. compute_current_cache_key() invoked exactly once per claim.
# ---------------------------------------------------------------------------

def test_claim_side_cache_key_check_computes_current_key_exactly_once(tmp_path):
    """A DIRECT evaluate_claim_admission() call -- never the end-to-end
    run_select() convention, which legitimately reaches cache_key.py THREE
    times per claimed id (the merge, the classification pass, and the
    claim gate's own D6/D10 computation); an end-to-end invocation counter
    would therefore fail on entirely correct code. This test bypasses the
    ledger materialization step too (it hand-builds the `record` dict
    evaluate_claim_admission() itself would be handed) so the ONLY call to
    compute_current_cache_key() left in the picture is the claim gate's
    own.

    Mutation: a second claim-side compute_current_cache_key() call (e.g.
    computing it once for the new admission check and again for the
    D6/D10 reporting fields, instead of reusing one snapshot) -> red on
    the counter assertion."""
    root = make_durable_root(tmp_path)
    seg = "seg22"
    fixture_keys = {}
    result = build_from_converged_segment(root, seg, fixture_keys, hand_edit=False)
    write_manifest(root, [seg])
    real_key = fixture_keys[seg]

    mod = _load_select_segments_module(root)
    dirs = mod.resolve_dirs(str(root))

    calls = []

    def _fake_compute_current_cache_key(seg_arg, cache_key_script, durable_root, durable_root_str, plugin_root_str):
        calls.append(seg_arg)
        if len(calls) == 1:
            return dict(real_key)
        # A hypothetical second call must return a DIFFERENT key -- so a
        # mutant that calls this twice and lets the SECOND result leak
        # into the returned extras is caught by the snapshot-consistency
        # assertion below even if the bare call count were somehow missed.
        different = dict(real_key)
        different["style_contract_hash"] = "SECOND-CALL-DIFFERENT-VALUE"
        return different

    # A patch onto the WRONG name silently creates a new attribute rather
    # than erroring -- the real compute_current_cache_key() would then
    # still run, the fake would never be called, and `calls` would stay
    # empty, which len(calls) == 1 below WOULD catch (0 != 1) -- but only
    # by accident, and only because that assertion happens to be strict
    # equality. This assertion is the deliberate, load-bearing guard: it
    # fails loudly, right at the patch site, on the exact defect this
    # monkeypatch technique is prone to (a typo'd or stale attribute name
    # patching nothing), rather than relying on a later assertion to catch
    # it as a side effect.
    assert hasattr(mod, "compute_current_cache_key"), (
        "the loaded module has no 'compute_current_cache_key' attribute -- patching it now "
        "would silently create a NEW attribute the production code never calls, leaving the "
        "real function in place and this test's whole point unverified"
    )
    mod.compute_current_cache_key = _fake_compute_current_cache_key

    stored_key = dict(real_key)
    stored_key["style_contract_hash"] = "style-contract-OLD"
    record = {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": 1,
        "cache_key": stored_key,
        "n_blocks": 3,
        "n_footnotes": 1,
        "n_verses": 2,
        "reviewed_draft_sha1": result["converged_sha1"],
    }
    args = argparse.Namespace(run_id=RUN_ID, durable_root=str(root), plugin_root=None)

    ok, reasons, extras = mod.evaluate_claim_admission(
        seg, mod.CLAIM_PROFILE_FROM_CONVERGED, record, dirs, {}, args
    )

    assert ok is True, reasons
    assert len(calls) == 1, f"compute_current_cache_key() called {len(calls)} times, expected 1"
    assert extras["current_cache_key"] == real_key, (
        "extras['current_cache_key'] must be the FIRST (and only) call's own snapshot"
    )
    moved = {m["field"]: m for m in extras["cache_key_moved_fields"]}
    assert "style_contract_hash" in moved
    assert moved["style_contract_hash"]["at_claim"] == real_key["style_contract_hash"], (
        "the D6/D10 reporting diff must use the SAME snapshot the admission decision "
        "itself used -- a second, later call returning a different value would leak "
        "into this field if the computation ran twice"
    )


# ---------------------------------------------------------------------------
# 6. Translate stays unreachable for the newly-admitted population.
#
# Duplicated fixture from tests/claim_forces_review_only.test.py's own
# Section B (segment_dispatch_driver.py's process_segment() driven directly
# against a hand-built DispatchContext) -- per this project's own "tests
# never import one another" convention. This file's other tests never touch
# segment_dispatch_driver.py at all.
# ---------------------------------------------------------------------------

DRIVER_PROFILE_YAML = (
    "engine:\n"
    "  max_fix_rounds: 2\n"
    "  max_codex_jobs_per_batch: 400\n"
    "  batch_agent_cap: 10000\n"
    "  effort: high\n"
    "source:\n"
    "  language:\n"
    "    code: fr\n"
    "target:\n"
    "  language:\n"
    "    code: ru\n"
    "verse_policy:\n"
    "  mode: skip\n"
    "  threshold_lines: null\n"
)

FAKE_RESOLVE_CODEX_COMPANION_PY = """#!/usr/bin/env python3
import argparse
import json


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--durable-root", required=True)
    p.add_argument("--node", default="node")
    p.add_argument("--search-glob", action="append", default=None)
    p.add_argument("--timeout-sec", type=int, default=30)
    p.parse_args()
    print(json.dumps({"companion_path": "/fake/codex-companion.mjs"}))


if __name__ == "__main__":
    main()
"""

FAKE_DRIVER_DRAFT_READY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("seg")
    p.add_argument("--expect-token", default=None)
    p.add_argument("--candidate-file", default=None)
    p.add_argument("--durable-root", default=None)
    args = p.parse_args()
    durable_root = Path(args.durable_root).resolve() if args.durable_root else Path(__file__).resolve().parent.parent
    path = Path(args.candidate_file) if args.candidate_file else durable_root / "segments" / (args.seg + ".draft.json")
    if not path.is_file():
        print(json.dumps({"ready": False, "reason": "missing"}))
        return 1
    obj = json.loads(path.read_text(encoding="utf-8"))
    if args.expect_token is not None and obj.get("dispatch_token") != args.expect_token:
        print(json.dumps({"ready": False, "reason": "token-mismatch"}))
        return 1
    print(json.dumps({"ready": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

# Deliberately DIFFERENT from claim_forces_review_only.test.py's own fake
# validate_draft.py, which only ever fails on a MISSING file. This one
# fails on a genuinely STRUCTURAL defect (no "blocks" key) while the draft
# is still present on disk and correctly tokened -- the shape plan test 6
# actually asks for ("a claimed segment whose draft is structurally
# invalid"), distinct from claim_forces_review_only.test.py's own
# "no draft at all" scenario.
FAKE_DRIVER_VALIDATE_DRAFT_STRUCTURAL_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("seg")
    p.add_argument("--candidate-file", default=None)
    p.add_argument("--durable-root", default=None)
    args = p.parse_args()
    durable_root = Path(args.durable_root).resolve() if args.durable_root else Path(__file__).resolve().parent.parent
    path = Path(args.candidate_file) if args.candidate_file else durable_root / "segments" / (args.seg + ".draft.json")
    if not path.is_file():
        print("FAIL: draft missing")
        return 1
    obj = json.loads(path.read_text(encoding="utf-8"))
    if "blocks" not in obj:
        print("FAIL: structurally invalid -- no 'blocks'")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

FAKE_DRIVER_CODEX_JOB_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--kind", required=True)
    p.add_argument("--companion", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--seg", required=True)
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--expect-token", required=True)
    p.add_argument("--run-id", default=None)
    p.add_argument("--disp", required=True)
    p.add_argument("--deadline-sec", required=True)
    p.add_argument("--effort", default="high")
    p.add_argument("--model", default=None)
    p.add_argument("--plugin-root", default=None)
    p.add_argument("--node", default="node")
    args = p.parse_args()

    cwd = Path(args.cwd)
    argv_log_path = cwd / "test_fixture_argv_log.jsonl"
    with open(argv_log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": args.kind, "seg": args.seg, "argv": sys.argv[1:]}) + "\\n")
    segments_dir = cwd / "segments"

    if args.kind == "translate":
        draft = {"seg": args.seg, "blocks": {"p1": "hola"}, "dispatch_token": args.expect_token}
        (segments_dir / (args.seg + ".draft.json")).write_text(json.dumps(draft), encoding="utf-8")

    line = {
        "ok": True, "kind": args.kind, "seg": args.seg, "jobId": "fake-job",
        "job_status": "completed", "timed_out": False, "adopted": False,
        "reason": "promoted", "error_detail": None,
    }
    print(json.dumps(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_driver_fixture_root(tmp_path):
    """Isolated durable_root carrying: the REAL segment_dispatch_driver.py
    + claim_record.py + resume_setup.py + ledger_update.py + draft_sha1.py
    under scripts/, plus small controllable fakes for cache_key.py,
    resolve_codex_companion.py, draft_ready.py, validate_draft.py, and
    codex_job.py. Does NOT stage select_segments.py -- this test drives
    process_segment() directly and never shells out to it, exactly like
    tests/claim_forces_review_only.test.py's own Section B fixture."""
    root = tmp_path / "driver_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DRIVER_SRC, scripts_dir / "segment_dispatch_driver.py")
    shutil.copy2(CLAIM_RECORD_SRC, scripts_dir / "claim_record.py")
    shutil.copy2(RESUME_SETUP_SRC, scripts_dir / "resume_setup.py")
    shutil.copy2(LEDGER_UPDATE_SRC, scripts_dir / "ledger_update.py")
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    (scripts_dir / "resolve_codex_companion.py").write_text(FAKE_RESOLVE_CODEX_COMPANION_PY, encoding="utf-8")
    (scripts_dir / "draft_ready.py").write_text(FAKE_DRIVER_DRAFT_READY_PY, encoding="utf-8")
    (scripts_dir / "validate_draft.py").write_text(FAKE_DRIVER_VALIDATE_DRAFT_STRUCTURAL_PY, encoding="utf-8")
    (scripts_dir / "codex_job.py").write_text(FAKE_DRIVER_CODEX_JOB_PY, encoding="utf-8")

    shutil.copytree(ASSETS_DIR / "schemas", root / "schemas")

    templates_dir = root / "templates"
    templates_dir.mkdir()
    shutil.copy2(MASS_TRANSLATE_TEMPLATE_SRC, templates_dir / "mass-translate-wf.template.js")

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()

    profile_path = root / "profile.yml"
    profile_path.write_text(DRIVER_PROFILE_YAML, encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )
    return root


def write_driver_fixture_cache_keys(root, mapping):
    (root / "test_fixture_cache_keys.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")


def write_driver_fixture_segpack(root, seg):
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps({"seg": seg, "blocks": [], "footnotes": [], "verses": []}, ensure_ascii=False),
        encoding="utf-8",
    )


_DRIVER_FIXTURE_TRANSLATE_CFG = {
    "max_fix_rounds": 2, "batch_agent_cap": 10000, "max_codex_jobs_per_batch": 400,
    "effort": "high", "model": "", "source_lang": "fr", "target_lang": "ru",
    "verse_policy": {"mode": "skip", "threshold_lines": None},
    "research_mode": "", "citation_content_types": [],
}


def _load_driver_fixture_module(root):
    return _load_module(root / "scripts" / "segment_dispatch_driver.py", "segment_dispatch_driver_fixture_stale_admission")


def _driver_fixture_ctx(root, run_id, claims=None):
    driver_mod = _load_driver_fixture_module(root)
    dirs = driver_mod.resolve_dirs(None)
    ctx = driver_mod.DispatchContext(
        dirs=dirs, run_id=run_id, translate_cfg=dict(_DRIVER_FIXTURE_TRANSLATE_CFG),
        companion_path="/fake/codex-companion.mjs", durable_root_str=None, plugin_root_str=None,
        node_bin="node", session_id="test-session", claims=claims,
    )
    return driver_mod, ctx


def _driver_argv_log(root):
    log_path = root / "test_fixture_argv_log.jsonl"
    if not log_path.is_file():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_translate_stays_unreachable_for_a_from_converged_claim_with_an_invalid_draft(tmp_path):
    """Proves the population #491 newly admits still flows through the
    PRE-EXISTING #450 capability check unmodified: a segment THIS
    INVOCATION's own ctx.claims names under 'from-converged' must never
    reach codex_job.py's translate dispatch, even when derive_next_action()
    itself would otherwise route straight there (a structurally invalid
    draft -- present on disk, correctly tokened, but missing 'blocks' --
    gives draft_ok=False with no matching prior review, so
    derive_next_action() returns {"action": "translate"} at :3675). #491
    never touches this machinery; this is the regression fence proving
    that claim, read from the job log rather than merely the outcome.

    Mutation: any change that lets a from-converged-claimed segment reach
    the translate dispatch (removing or bypassing
    claim_capability_refusal_for_translate()'s call site) -> codex_job.py
    IS invoked (the fake writes an argv-log line and a fresh draft), red
    on both the outcome/reason and the empty-job-log assertions."""
    root = make_driver_fixture_root(tmp_path)
    write_driver_fixture_cache_keys(root, {"seg01": make_cache_key("seg01")})
    write_driver_fixture_segpack(root, "seg01")
    driver_mod, ctx = _driver_fixture_ctx(root, run_id="RUN-A", claims={"seg01": "from-converged"})

    token = driver_mod.translate_dispatch_token("RUN-A", "seg01")
    draft = {"seg": "seg01", "dispatch_token": token}  # deliberately no "blocks"
    (root / "segments" / "seg01.draft.json").write_text(json.dumps(draft), encoding="utf-8")

    # Setup check: draft_ready.py's own token-based check must actually
    # pass here (draft_ok's OTHER half), so the failure this test measures
    # is genuinely validate_draft.py's structural one, not a token mismatch
    # masquerading as it.
    assert driver_mod._run_gate(
        ctx.dirs["draft_ready_script"], ["seg01", "--expect-token", token], ctx, supports_plugin_root=False
    ), "setup check: draft_ready.py's own token check must pass for this fixture"

    result = driver_mod.process_segment("seg01", ctx)

    assert result["outcome"] == "failed", result
    assert result["reason"] == "invocation-claim-translate-refused", result
    assert "seg01" in result["detail"]
    assert "from-converged" in result["detail"]
    assert _driver_argv_log(root) == [], (
        "codex_job.py must never have been invoked -- translate stays unreachable for "
        "a claimed segment regardless of draft validity (:5114-5140)"
    )
    assert not (root / "runs" / "ledger.d" / "seg01.json").exists(), (
        "no ledger fragment may be written on refusal"
    )
