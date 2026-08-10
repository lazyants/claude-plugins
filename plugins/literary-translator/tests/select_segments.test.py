"""tests/select_segments.test.py -- tests for scripts/select_segments.py.

See SKILL.md's "W5 Mass-translate" section and
references/ledger-and-resumability.md's "Derivation-state gate" /
"Recovery rules for a resumed/interrupted run" sections for the
authoritative spec this script implements. This file exercises exactly what
that spec makes select_segments.py responsible for:

  1. The full six-category classification taxonomy -- reusable, stale (with
     its `stale_reason` sub-field, covering BOTH triggers:
     `cache_key_mismatch` and `draft_sha1_mismatch`, independently and in
     combination), blocked_needs_regeneration, recoverable, not_started, and
     human_escalation -- emitted per-segment as `classification`, plus the
     full "classification report" (`counts` + `ids_by_category`).
  2. The derivation-state gate's two distinct outcomes for a cache-key
     mismatch confined to one of the four derivation-state fields
     (particle_config_hash/source_extraction_hash/source_input_hash/
     derivation_bundle_hash): blocked_needs_regeneration when the segpack's
     own `generation_hashes` hasn't caught up yet, vs. an ordinary `stale`
     reclassification once it has (self-clearing).
  3. The documented INDEPENDENCE of the draft-sha1 gate from the
     derivation-state gate: a draft_sha1_mismatch-triggered stale is NEVER
     reclassified as blocked_needs_regeneration, even when the same
     segment's cache-key mismatch happens to be confined to a
     derivation-state field.
  4. Emitted `SEGS` = not_started UNION recoverable UNION stale (excluding
     reusable/human_escalation/blocked_needs_regeneration), in candidate
     (manifest segments[]) order.
  5. `--only-segs`: intersects the emitted SEGS with an explicit id list; is
     also the SOLE mechanism to retry a human_escalation (blocked or
     non_converged) segment (an explicit, auditable override, logged in
     `overrides`); never force-includes a `reusable` segment nor a
     `blocked_needs_regeneration` one (both land in `excluded_only_segs`
     with their own documented reason instead).
  6. FATAL when any `--only-segs` id is not present in manifest.json's
     segments[] at all -- names every unrecognized id, never silently drops
     them, and never even reaches ledger_merge.py.
  7. `--allow-empty`: without it, an empty emitted SEGS is FATAL (the
     "genuine no-op confirmation run" escape hatch); with it, reported
     normally.

Following this plugin's established test convention (`ledger_merge.test.py`'s
`make_durable_root` pattern): every test copies the REAL `select_segments.py`
and `ledger_merge.py` plus the REAL `assets/schemas/*.schema.json` files into
an isolated `tmp_path` fixture root and invokes
`python3 {durable_root}/scripts/select_segments.py [flags]` exactly as it is
invoked in production, so both scripts' self-anchored `DURABLE_ROOT`
resolves against the fixture, never this repo's real assets tree.

`cache_key.py` itself is stubbed out with the same small fixture script
`ledger_merge.test.py` uses: it reads a test-controlled
`test_fixture_cache_keys.json` mapping `{seg: <15-field cache_key dict>}` and
prints the requested segment's entry verbatim. This keeps the test scoped to
select_segments.py's OWN classification logic (the real cache_key.py's
15-field hashing algorithm has its own dedicated test file,
`ledger_composite_key.test.py`) while still exercising the real subprocess
call paths both ledger_merge.py AND select_segments.py itself make to
`cache_key.py --seg <id>`.
"""
import ast
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SELECT_SCRIPT_SRC = ASSETS_DIR / "scripts" / "select_segments.py"
LEDGER_MERGE_SRC = ASSETS_DIR / "scripts" / "ledger_merge.py"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

assert SELECT_SCRIPT_SRC.is_file(), f"select_segments.py not found at {SELECT_SCRIPT_SRC}"
assert LEDGER_MERGE_SRC.is_file(), f"ledger_merge.py not found at {LEDGER_MERGE_SRC}"
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

# A fixture stand-in for the real cache_key.py -- same `--seg <id>` -> JSON
# object stdout interface, sourced from a test-controlled lookup file instead
# of real profile.yml/canon.json/segpack machinery. Verbatim copy of the
# stub `ledger_merge.test.py` uses (both ledger_merge.py AND
# select_segments.py itself shell out to this exact `--seg <id>` interface).
# Accepts an OPTIONAL --durable-root (LT-409), mirroring the real script's
# own contract: when given, it locates test_fixture_cache_keys.json under
# THAT root instead of its own self-anchored location -- so a test can
# prove select_segments.py/ledger_merge.py actually forward the flag, not
# merely tolerate an unknown arg.
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


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path):
    """Builds an isolated durable_root: copies the REAL select_segments.py
    and ledger_merge.py plus the REAL assets/schemas/*.schema.json files
    into {root}/scripts/ and {root}/schemas/, installs the fake cache_key.py
    stub alongside them, and creates empty runs/ledger.d/ and segments/
    directories.
    """
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SELECT_SCRIPT_SRC, scripts_dir / "select_segments.py")
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    schemas_dir = root / "schemas"
    shutil.copytree(SCHEMAS_SRC, schemas_dir)

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    return root


def write_manifest(root, seg_ids):
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": s} for s in seg_ids]}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_fragment(root, seg, record):
    frag_path = root / "runs" / "ledger.d" / f"{seg}.json"
    frag_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return frag_path


def write_fixture_cache_keys(root, mapping):
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
    )


def write_draft(root, seg, content: dict) -> str:
    """Writes segments/{seg}.draft.json as canonical JSON (sorted keys,
    compact separators -- byte-identical to what draft_content_sha1() in
    draft_sha1.py/ledger_update.py/select_segments.py itself would
    re-serialize) and returns its CONTENT sha1 hex digest -- exactly the
    reviewed_draft_sha1 a real converged fragment would record for this
    draft (draft_path(seg)'s exact canonical location, per
    select_segments.py's own `draft_path` helper)."""
    path = root / "segments" / f"{seg}.draft.json"
    raw = json.dumps(
        content, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha1(raw).hexdigest()


def write_segpack(root, seg, generation_hashes):
    path = root / "segments" / f"segpack_{seg}.json"
    path.write_text(
        json.dumps({"generation_hashes": generation_hashes}), encoding="utf-8"
    )


def make_cache_key(seed):
    """A full, schema-valid 15-field cache_key dict. Every field's value is
    derived from `seed` so two different seeds are guaranteed to produce a
    field-by-field mismatch in every one of the 15 fields simultaneously."""
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def with_field(key, field, value):
    """A copy of `key` with exactly one field overridden -- for constructing
    a STORED cache_key that mismatches the CURRENT one in exactly one named
    field, everything else held identical."""
    d = dict(key)
    d[field] = value
    return d


def converged_fragment(cache_key, reviewed_draft_sha1, rounds=1):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": rounds,
        "cache_key": cache_key,
        "n_blocks": 3,
        "n_footnotes": 1,
        "n_verses": 0,
        "reviewed_draft_sha1": reviewed_draft_sha1,
    }


def in_progress_fragment():
    return {"timestamp": "2026-01-01T00:00:00Z", "status": "in_progress"}


def blocked_fragment(reason="review-null"):
    return {"timestamp": "2026-01-01T00:00:00Z", "status": "blocked", "reason": reason}


def non_converged_fragment(reason="cap", rounds=4):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "non_converged",
        "reason": reason,
        "rounds": rounds,
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


# ---------------------------------------------------------------------------
# The big fixture: one manifest of 11 segments covering the full
# classification taxonomy, both stale_reason triggers, both derivation-state
# gate outcomes, the draft-sha1/derivation-state independence rule, and both
# human_escalation-triggering statuses.
# ---------------------------------------------------------------------------

SEG_IDS = [
    "seg01_reusable",
    "seg02_stale_draftonly",
    "seg03_stale_cachekey",
    "seg04_stale_both",
    "seg05_blocked_regen",
    "seg06_stale_regen_caughtup",
    "seg07_stale_draft_and_derivmismatch",
    "seg08_recoverable",
    "seg09_not_started",
    "seg10_human_blocked",
    "seg11_human_nonconverged",
]


def build_full_project(root):
    write_manifest(root, SEG_IDS)

    current_key = make_cache_key("current")
    fixture_keys = {}

    # seg01: cache key AND draft sha1 both match -> reusable.
    sha1_01 = write_draft(root, "seg01_reusable", {"text": "draft-seg01-content"})
    fixture_keys["seg01_reusable"] = current_key
    write_fragment(root, "seg01_reusable", converged_fragment(dict(current_key), sha1_01))

    # seg02: cache key matches, but the on-disk draft's sha1 no longer
    # matches reviewed_draft_sha1 (e.g. a hand-edit after review) -> stale,
    # stale_reason=[draft_sha1_mismatch] only, mismatched_fields=[].
    write_draft(root, "seg02_stale_draftonly", {"text": "draft-seg02-CURRENT-content"})
    fixture_keys["seg02_stale_draftonly"] = current_key
    write_fragment(
        root,
        "seg02_stale_draftonly",
        converged_fragment(dict(current_key), "0" * 40),
    )

    # seg03: cache key mismatches on ONE non-derivation field, draft matches
    # -> stale, stale_reason=[cache_key_mismatch] only.
    sha1_03 = write_draft(root, "seg03_stale_cachekey", {"text": "draft-seg03-content"})
    fixture_keys["seg03_stale_cachekey"] = current_key
    stored_03 = with_field(current_key, "style_contract_hash", "style_contract_hash-OLD")
    write_fragment(root, "seg03_stale_cachekey", converged_fragment(stored_03, sha1_03))

    # seg04: cache key mismatches on a non-derivation field AND the draft
    # sha1 also mismatches -> stale, stale_reason carries BOTH triggers.
    write_draft(root, "seg04_stale_both", {"text": "draft-seg04-CURRENT-content"})
    fixture_keys["seg04_stale_both"] = current_key
    stored_04 = with_field(current_key, "prompt_hash", "prompt_hash-OLD")
    write_fragment(
        root,
        "seg04_stale_both",
        converged_fragment(stored_04, "1" * 40),
    )

    # seg05: cache key mismatches on a DERIVATION-STATE field, draft
    # matches, and the segpack's own generation_hashes has NOT caught up
    # with the current value yet -> blocked_needs_regeneration.
    sha1_05 = write_draft(root, "seg05_blocked_regen", {"text": "draft-seg05-content"})
    fixture_keys["seg05_blocked_regen"] = current_key
    stored_05 = with_field(current_key, "particle_config_hash", "particle_config_hash-OLD")
    write_fragment(root, "seg05_blocked_regen", converged_fragment(stored_05, sha1_05))
    write_segpack(
        root,
        "seg05_blocked_regen",
        {"particle_config_hash": "particle_config_hash-OLD-SEGPACK-NOT-CAUGHT-UP"},
    )

    # seg06: cache key mismatches on a DERIVATION-STATE field, draft
    # matches, but the segpack HAS already caught up (its generation_hashes
    # entry matches the current value) -> self-clearing, reclassified as
    # ordinary stale, never blocked_needs_regeneration.
    sha1_06 = write_draft(root, "seg06_stale_regen_caughtup", {"text": "draft-seg06-content"})
    fixture_keys["seg06_stale_regen_caughtup"] = current_key
    stored_06 = with_field(current_key, "derivation_bundle_hash", "derivation_bundle_hash-OLD")
    write_fragment(root, "seg06_stale_regen_caughtup", converged_fragment(stored_06, sha1_06))
    write_segpack(
        root,
        "seg06_stale_regen_caughtup",
        {"derivation_bundle_hash": current_key["derivation_bundle_hash"]},
    )

    # seg07: cache key mismatches on a DERIVATION-STATE field AND the draft
    # sha1 also mismatches -> must classify as ordinary stale (the
    # draft-sha1 gate short-circuits BEFORE the derivation-state gate is
    # ever consulted), never blocked_needs_regeneration -- the two gates
    # are independent. Deliberately no segpack file is written for this
    # segment at all: if the implementation ever regressed into consulting
    # the derivation gate here, it would blow up on a missing segpack
    # instead of silently passing.
    write_draft(root, "seg07_stale_draft_and_derivmismatch", {"text": "draft-seg07-CURRENT-content"})
    fixture_keys["seg07_stale_draft_and_derivmismatch"] = current_key
    stored_07 = with_field(current_key, "source_extraction_hash", "source_extraction_hash-OLD")
    write_fragment(
        root,
        "seg07_stale_draft_and_derivmismatch",
        converged_fragment(stored_07, "2" * 40),
    )

    # seg08: in_progress fragment (interrupted prior attempt) -> recoverable,
    # treated like not_started for dispatch, counted separately.
    write_fragment(root, "seg08_recoverable", in_progress_fragment())

    # seg09: no fragment at all -> not_started.

    # seg10: blocked -> human_escalation.
    write_fragment(root, "seg10_human_blocked", blocked_fragment(reason="review-null"))

    # seg11: non_converged -> human_escalation.
    write_fragment(root, "seg11_human_nonconverged", non_converged_fragment(reason="cap", rounds=4))

    write_fixture_cache_keys(root, fixture_keys)


def setup_full_project(tmp_path):
    root = make_durable_root(tmp_path)
    build_full_project(root)
    return root


# ---------------------------------------------------------------------------
# 1. Full classification taxonomy + classification report
# ---------------------------------------------------------------------------

def test_full_classification_taxonomy_and_report(tmp_path):
    root = setup_full_project(tmp_path)

    proc = run_select(root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)

    assert set(payload.keys()) == {
        "success",
        "durable_root",
        "segs",
        "requested_only_segs",
        "classification",
        "counts",
        "ids_by_category",
        "overrides",
        "excluded_only_segs",
        # #409 Step 1. This exact-key assertion is deliberate: a consumer that
        # reads `authorizes_dispatch` must be able to trust that the key is
        # always present, so silently dropping it has to fail here.
        "authorizes_dispatch",
        "previously_converged",
        # 1.19.1 fail-closed sentinel fix: sentinel paths that were neither
        # absent nor a regular file. Always [] on this path (a non-empty set
        # refuses above), but part of the contract for the same reason
        # runs_missing_digest is -- a consumer must be able to tell a scan that
        # found nothing from a scan that never ran.
        "ambiguous_sentinels",
        # #409 Step 3 -- the resume-gate evidence, reported on the SUCCESS
        # path too and therefore part of this contract. `runs_missing_digest`
        # in particular must always be present: a consumer (or a test) that
        # can only assert "the run passed" cannot tell a check that scanned
        # and found nothing from one that scanned nothing at all, which is
        # the failure mode the whole gate exists to stop reproducing.
        # tests/resume_gate_skip_detection.test.py owns the behavior; this
        # line owns the contract.
        "runs_missing_digest",
        "runs_acknowledged_pre_gate",
        # Security fix: run ids from either evidence half that failed
        # validate_run_id() -- {run_id: reason}, never fed into a filesystem
        # path. Reported on the success path for the same reason
        # runs_missing_digest is: a consumer must be able to see the exact
        # set, not merely that the run passed.
        # tests/resume_gate_skip_detection.test.py owns the behavior.
        "unsafe_run_ids",
        "dispatching_run_ids",
        "workflow_run_ids",
        "run_id_evidence",
        "drafts_scanned",
        "drafts_untokened",
        # #438 D3: the claim authorization the driver consumes. Part of the
        # exact-key contract for the same reason the keys above are -- a
        # consumer must be able to trust the key is always present.
        "claims",
    }
    assert payload["success"] is True
    assert payload["durable_root"] == str(root)
    assert payload["requested_only_segs"] is None

    classification = payload["classification"]
    assert set(classification.keys()) == set(SEG_IDS)

    assert classification["seg01_reusable"] == {"category": "reusable"}

    assert classification["seg02_stale_draftonly"] == {
        "category": "stale",
        "stale_reason": ["draft_sha1_mismatch"],
        "mismatched_fields": [],
    }

    assert classification["seg03_stale_cachekey"] == {
        "category": "stale",
        "stale_reason": ["cache_key_mismatch"],
        "mismatched_fields": ["style_contract_hash"],
    }

    assert classification["seg04_stale_both"] == {
        "category": "stale",
        "stale_reason": ["draft_sha1_mismatch", "cache_key_mismatch"],
        "mismatched_fields": ["prompt_hash"],
    }

    assert classification["seg05_blocked_regen"] == {
        "category": "blocked_needs_regeneration",
        "pending_fields": ["particle_config_hash"],
        "message": (
            "segment 'seg05_blocked_regen' is blocked on regeneration: rerun "
            "W3/W3a (re-run bootstrap_names.py to regenerate name candidates, "
            "then the glossary pass to re-stamp canon.json's "
            "particle_config_hash -- or, on a project with no new candidates "
            "left to merge, canon_validate.py --restamp-derivation -- then "
            "segpack.py) "
            "before this segment can be reclassified"
        ),
    }

    assert classification["seg06_stale_regen_caughtup"] == {
        "category": "stale",
        "stale_reason": ["cache_key_mismatch"],
        "mismatched_fields": ["derivation_bundle_hash"],
    }

    # The independence rule: a derivation-state field mismatch combined with
    # a draft_sha1 mismatch is STILL ordinary stale, never
    # blocked_needs_regeneration.
    assert classification["seg07_stale_draft_and_derivmismatch"] == {
        "category": "stale",
        "stale_reason": ["draft_sha1_mismatch", "cache_key_mismatch"],
        "mismatched_fields": ["source_extraction_hash"],
    }

    assert classification["seg08_recoverable"] == {
        "category": "recoverable",
        "status": "in_progress",
    }

    assert classification["seg09_not_started"] == {"category": "not_started"}

    assert classification["seg10_human_blocked"] == {
        "category": "human_escalation",
        "status": "blocked",
        "reason": "review-null",
    }

    assert classification["seg11_human_nonconverged"] == {
        "category": "human_escalation",
        "status": "non_converged",
        "reason": "cap",
    }

    # --- the "classification report": counts + IDs per category ---
    assert payload["counts"] == {
        "reusable": 1,
        "stale": 5,
        "blocked_needs_regeneration": 1,
        "recoverable": 1,
        "not_started": 1,
        "human_escalation": 2,
    }
    assert payload["ids_by_category"] == {
        "reusable": ["seg01_reusable"],
        "stale": [
            "seg02_stale_draftonly",
            "seg03_stale_cachekey",
            "seg04_stale_both",
            "seg06_stale_regen_caughtup",
            "seg07_stale_draft_and_derivmismatch",
        ],
        "blocked_needs_regeneration": ["seg05_blocked_regen"],
        "recoverable": ["seg08_recoverable"],
        "not_started": ["seg09_not_started"],
        "human_escalation": ["seg10_human_blocked", "seg11_human_nonconverged"],
    }

    # --- emitted SEGS: not_started UNION recoverable UNION stale, in
    # candidate (manifest) order, excluding reusable/human_escalation/
    # blocked_needs_regeneration ---
    expected_segs = [
        "seg02_stale_draftonly",
        "seg03_stale_cachekey",
        "seg04_stale_both",
        "seg06_stale_regen_caughtup",
        "seg07_stale_draft_and_derivmismatch",
        "seg08_recoverable",
        "seg09_not_started",
    ]
    assert payload["segs"] == expected_segs
    assert payload["overrides"] == []
    assert payload["excluded_only_segs"] == []

    # Every invocation logs the requested ids alongside the actually-emitted
    # SEGS ids, to stderr, for audit.
    expected_line = f"select_segments.py: requested={SEG_IDS} emitted={expected_segs}"
    assert expected_line in proc.stderr


# ---------------------------------------------------------------------------
# 2. --only-segs: intersection, dedup/whitespace handling, and the sole
#    override mechanism for human_escalation segments.
# ---------------------------------------------------------------------------

def test_only_segs_intersects_eligible_set(tmp_path):
    root = setup_full_project(tmp_path)

    proc = run_select(root, "--only-segs", "seg02_stale_draftonly,seg09_not_started")
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["requested_only_segs"] == ["seg02_stale_draftonly", "seg09_not_started"]
    assert payload["segs"] == ["seg02_stale_draftonly", "seg09_not_started"]
    assert payload["overrides"] == []
    assert payload["excluded_only_segs"] == []


def test_only_segs_dedups_and_trims_whitespace(tmp_path):
    root = setup_full_project(tmp_path)

    proc = run_select(
        root,
        "--only-segs",
        " seg09_not_started , seg09_not_started,seg02_stale_draftonly ",
    )
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["requested_only_segs"] == ["seg09_not_started", "seg02_stale_draftonly"]
    assert payload["segs"] == ["seg09_not_started", "seg02_stale_draftonly"]


def test_only_segs_is_sole_override_for_human_escalation(tmp_path):
    root = setup_full_project(tmp_path)

    proc = run_select(
        root,
        "--only-segs",
        "seg10_human_blocked,seg11_human_nonconverged,seg02_stale_draftonly",
    )
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["segs"] == [
        "seg10_human_blocked",
        "seg11_human_nonconverged",
        "seg02_stale_draftonly",
    ]
    assert sorted(payload["overrides"]) == ["seg10_human_blocked", "seg11_human_nonconverged"]
    assert payload["excluded_only_segs"] == []


def test_only_segs_never_force_includes_reusable_or_blocked_needs_regeneration(tmp_path):
    root = setup_full_project(tmp_path)

    # Without --allow-empty: naming only a reusable id and a
    # blocked_needs_regeneration id yields an empty emitted SEGS -> FATAL.
    proc = run_select(root, "--only-segs", "seg01_reusable,seg05_blocked_regen")
    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "refusing to no-op silently" in payload["error"]
    assert "--allow-empty" in payload["error"]

    # With --allow-empty: reported normally -- both names excluded with
    # their own documented reason, neither forced in, neither counted as an
    # override.
    proc2 = run_select(
        root, "--only-segs", "seg01_reusable,seg05_blocked_regen", "--allow-empty"
    )
    assert proc2.returncode == 0, proc2.stderr
    payload2 = parse_stdout(proc2)
    assert payload2["success"] is True
    assert payload2["segs"] == []
    assert payload2["overrides"] == []
    assert payload2["excluded_only_segs"] == [
        {
            "seg": "seg01_reusable",
            "category": "reusable",
            "reason": "reusable segments are not force-redone by --only-segs",
        },
        {
            "seg": "seg05_blocked_regen",
            "category": "blocked_needs_regeneration",
            "reason": "blocked_needs_regeneration is self-clearing, never a manual-override target",
        },
    ]


# ---------------------------------------------------------------------------
# 3. FATAL when a --only-segs id is absent from manifest.json's segments[].
# ---------------------------------------------------------------------------

def test_only_segs_fatals_on_id_absent_from_manifest(tmp_path):
    root = setup_full_project(tmp_path)

    proc = run_select(
        root,
        "--only-segs",
        "seg02_stale_draftonly,seg99_unknown_a,seg100_unknown_b",
    )
    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "2 id(s)" in payload["error"]
    assert "not present in" in payload["error"]
    assert "seg99_unknown_a" in payload["error"]
    assert "seg100_unknown_b" in payload["error"]
    # Never silently dropped, and the run never even reaches ledger_merge.py
    # -- no ledger.json should have been materialized.
    assert not (root / "runs" / "ledger.json").exists()


# ---------------------------------------------------------------------------
# 4. --allow-empty escape hatch vs. the default FATAL-on-empty-SEGS
#    behavior, for a genuine whole-project no-op confirmation run (every
#    segment already reusable, nothing to do by default).
# ---------------------------------------------------------------------------

def test_default_run_fatals_on_empty_segs_unless_allow_empty(tmp_path):
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01_only"])
    key = make_cache_key("only")
    sha1 = write_draft(root, "seg01_only", {"text": "draft-content-only-segment"})
    write_fragment(root, "seg01_only", converged_fragment(dict(key), sha1))
    write_fixture_cache_keys(root, {"seg01_only": key})

    proc = run_select(root)
    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "refusing to no-op silently" in payload["error"]
    assert "--allow-empty" in payload["error"]
    # The empty-SEGS FATAL specifically folds the classification report in,
    # so the operator can see WHY nothing was selected.
    assert payload["counts"] == {
        "reusable": 1,
        "stale": 0,
        "blocked_needs_regeneration": 0,
        "recoverable": 0,
        "not_started": 0,
        "human_escalation": 0,
    }
    assert payload["ids_by_category"]["reusable"] == ["seg01_only"]
    assert payload["classification"]["seg01_only"] == {"category": "reusable"}

    proc2 = run_select(root, "--allow-empty")
    assert proc2.returncode == 0, proc2.stderr
    payload2 = parse_stdout(proc2)
    assert payload2["success"] is True
    assert payload2["segs"] == []
    assert payload2["requested_only_segs"] is None
    assert payload2["overrides"] == []
    assert payload2["excluded_only_segs"] == []


# ---------------------------------------------------------------------------
# 5. Regression: the blocked_needs_regeneration hint for derivation_bundle_hash
#    must name the step that actually re-stamps it (the W3 glossary-pass
#    merge), not segpack.py -- segpack.py only ever copies the hash verbatim
#    from canon.json and never recomputes it, so the old wording sent
#    operators into a dead-end retry loop.
# ---------------------------------------------------------------------------

def test_derivation_bundle_hash_regen_hint_names_glossary_pass(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg12_blocked_regen_derivation"
    write_manifest(root, [seg])

    current_key = make_cache_key("current")
    sha1 = write_draft(root, seg, {"text": f"draft-{seg}-content"})
    stored = with_field(current_key, "derivation_bundle_hash", "derivation_bundle_hash-OLD")
    write_fragment(root, seg, converged_fragment(stored, sha1))
    write_segpack(
        root,
        seg,
        {"derivation_bundle_hash": "derivation_bundle_hash-OLD-SEGPACK-NOT-CAUGHT-UP"},
    )
    write_fixture_cache_keys(root, {seg: current_key})

    # This project's only segment is blocked_needs_regeneration, which is
    # excluded from SEGS -- emitted SEGS is therefore empty and the run
    # needs --allow-empty to avoid the unrelated empty-SEGS FATAL (the
    # classification report is folded into that FATAL payload too, but
    # --allow-empty keeps this test's assertions scoped to the successful
    # path, matching every other classification-only test in this file).
    proc = run_select(root, "--allow-empty")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True

    assert payload["classification"][seg] == {
        "category": "blocked_needs_regeneration",
        "pending_fields": ["derivation_bundle_hash"],
        "message": (
            f"segment {seg!r} is blocked on regeneration: rerun "
            "W3/W3a (re-run bootstrap_names.py to regenerate name candidates, "
            "then the glossary pass to re-stamp canon.json's "
            "derivation_bundle_hash -- or, on a project with no new "
            "candidates left to merge, canon_validate.py "
            "--restamp-derivation -- then segpack.py) "
            "before this segment can be reclassified"
        ),
    }


# ---------------------------------------------------------------------------
# 5a. #193/#291: the hint above must ALSO name the sanctioned restamp escape.
#     The glossary pass it names only re-stamps when it actually merges
#     something, and a mature project with zero unresolved candidates skips
#     the pass entirely -- so for exactly that project the remedy the gate
#     prints was unreachable, and 1.15.0's #291 fix removed the undocumented
#     `--merge-batches <empty-batch.json>` workaround it used to have. The
#     message an operator reads AT THE MOMENT OF FAILURE must therefore name
#     `--restamp-derivation`, and must still put segpack.py last: segpack
#     copies canon.json's stamp forward, so running it before the restamp
#     just re-copies the stale value.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "field", ["derivation_bundle_hash", "particle_config_hash"], ids=lambda f: f
)
def test_w3_regen_hints_name_the_sanctioned_restamp_escape(tmp_path, field):
    """Parameterized over BOTH W3/W3a derivation-state fields on purpose.

    The zero-candidate dead-end is a property of the REMEDY (a glossary pass
    that does not run), not of which field happened to flip, so it applies
    identically to `particle_config_hash` (the particle config file's bytes
    changed) and `derivation_bundle_hash` (bootstrap_names.py/segpack.py's
    bytes changed). Fixing one hint and leaving the other is exactly the
    drift this parameterization exists to make impossible."""
    root = make_durable_root(tmp_path)
    seg = f"seg13_blocked_regen_{field}"
    write_manifest(root, [seg])

    current_key = make_cache_key("current")
    sha1 = write_draft(root, seg, {"text": f"draft-{seg}-content"})
    stored = with_field(current_key, field, f"{field}-OLD")
    write_fragment(root, seg, converged_fragment(stored, sha1))
    write_segpack(root, seg, {field: f"{field}-OLD-SEGPACK-NOT-CAUGHT-UP"})
    write_fixture_cache_keys(root, {seg: current_key})

    proc = run_select(root, "--allow-empty")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    classification = parse_stdout(proc)["classification"][seg]
    assert classification["pending_fields"] == [field]
    message = classification["message"]

    assert "--restamp-derivation" in message, (
        f"the blocked_needs_regeneration hint for {field} does not name the "
        "sanctioned restamp escape, so a mature zero-candidate project is "
        "told to run a glossary pass that will not run and has no other way "
        "out (#193/#291)"
    )
    assert "canon_validate.py" in message, "the escape is named without its script"
    # Order is load-bearing, not cosmetic.
    assert message.index("--restamp-derivation") < message.index("segpack.py"), (
        "segpack.py must come AFTER the restamp -- it copies canon.json's "
        "stamp forward, so running it first just re-copies the stale value"
    )
    # The ordinary has-candidates remedy must survive alongside the escape.
    assert "glossary pass" in message
    # The hint must name the field it is actually about.
    assert field in message


def load_select_segments_module():
    """In-process load of the REAL select_segments.py, purely to read its own
    FIELD_TO_REGEN_STEP table. Never used to execute the CLI -- every
    behavioural test in this file drives the script as a subprocess. The
    script's own directory goes on sys.path for the duration so its sibling
    imports resolve, the same idiom the other in-process suites use."""
    scripts_dir = SELECT_SCRIPT_SRC.parent
    sys.path.insert(0, str(scripts_dir))
    try:
        spec = importlib.util.spec_from_file_location(
            "select_segments_regen_table_under_test", SELECT_SCRIPT_SRC
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(scripts_dir))


def test_every_w3_regen_hint_names_the_restamp_escape():
    """Table-driven, so a W3/W3a field added LATER is covered too.

    The parameterized test above pins the two fields that exist today; this
    one pins the rule itself. Any entry whose remedy routes through the W3
    glossary pass inherits that pass's zero-candidate hole, so it must also
    name the sanctioned escape -- the exact drift that left
    `particle_config_hash` behind when `derivation_bundle_hash` was fixed."""
    module = load_select_segments_module()
    w3_fields = [f for f, step in module.FIELD_TO_REGEN_STEP.items() if "glossary pass" in step]
    assert set(w3_fields) == {"derivation_bundle_hash", "particle_config_hash"}, (
        f"the set of glossary-pass-routed regen hints changed: {sorted(w3_fields)} "
        "-- re-check that each one still needs the restamp escape"
    )
    for field in w3_fields:
        step = module.FIELD_TO_REGEN_STEP[field]
        assert "--restamp-derivation" in step, (
            f"FIELD_TO_REGEN_STEP[{field!r}] routes the operator through the W3 "
            "glossary pass but never names canon_validate.py "
            "--restamp-derivation, so a mature zero-candidate project blocked "
            "on this field has no way out (#193)"
        )
        # Generated from the one shared template, not hand-maintained twice.
        assert step == module._w3_regen_step(field)

    # The W2 half of the same class: two fields, one source of truth. They
    # need no restamp escape (the extractor re-runs them at W2, not the
    # glossary pass), but duplicated literals are how the W3 pair drifted.
    w2_fields = [f for f, step in module.FIELD_TO_REGEN_STEP.items() if step.startswith("W2 ")]
    assert set(w2_fields) == {"source_extraction_hash", "source_input_hash"}
    assert all(
        module.FIELD_TO_REGEN_STEP[f] is module._W2_REGEN_STEP for f in w2_fields
    ), "the W2 remedies are no longer one shared literal -- they can now drift apart"


# ---------------------------------------------------------------------------
# 6. Issue #174 regression: a segpack that is unreadable/corrupt/invalid at
#    the derivation-state gate must escalate ONLY the segment hitting the
#    gate, never FatalError the whole W5 preflight. read_json's
#    fatal-on-any-IO-or-parse-error contract (raises FatalError -> top-level
#    {"success": false} for the WHOLE run) is wrong for this per-segment
#    gate -- every OTHER per-segment failure in this file degrades to that
#    segment's own human_escalation instead. read_segpack_nonfatal must
#    degrade the same way, matching compute_current_cache_key()'s isolation
#    contract.
# ---------------------------------------------------------------------------

def setup_blocked_regen_and_reusable_project(tmp_path):
    """A 2-segment project: 'seg_blocked_regen' hits the derivation-state
    gate (cache-key mismatch confined to a derivation-state field, draft
    sha1 matches) -- the segpack itself is left for each test to write (or
    not write at all). 'seg_reusable_control' is an ordinary reusable
    segment, present purely to prove a segpack failure on the FIRST segment
    never takes down classification of the SECOND -- per-segment isolation,
    not just non-crash."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg_blocked_regen", "seg_reusable_control"])

    current_key = make_cache_key("current")
    fixture_keys = {}

    sha1_blocked = write_draft(root, "seg_blocked_regen", {"text": "draft-blocked-content"})
    fixture_keys["seg_blocked_regen"] = current_key
    stored_blocked = with_field(current_key, "particle_config_hash", "particle_config_hash-OLD")
    write_fragment(root, "seg_blocked_regen", converged_fragment(stored_blocked, sha1_blocked))

    sha1_control = write_draft(root, "seg_reusable_control", {"text": "draft-control-content"})
    fixture_keys["seg_reusable_control"] = current_key
    write_fragment(
        root, "seg_reusable_control", converged_fragment(dict(current_key), sha1_control)
    )

    write_fixture_cache_keys(root, fixture_keys)
    return root


def test_blocked_regen_gate_missing_segpack_escalates_single_segment_not_whole_run(tmp_path):
    root = setup_blocked_regen_and_reusable_project(tmp_path)
    # Deliberately do NOT write a segpack for seg_blocked_regen.

    proc = run_select(root, "--allow-empty")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True

    classification = payload["classification"]["seg_blocked_regen"]
    assert classification["category"] == "human_escalation"
    assert classification["status"] == "segpack_read_failed"
    assert "not found" in classification["detail"]

    # Per-segment isolation: the OTHER segment still classifies normally.
    assert payload["classification"]["seg_reusable_control"] == {"category": "reusable"}


def test_blocked_regen_gate_corrupt_segpack_escalates_single_segment(tmp_path):
    root = setup_blocked_regen_and_reusable_project(tmp_path)
    (root / "segments" / "segpack_seg_blocked_regen.json").write_text(
        "{not json", encoding="utf-8"
    )

    proc = run_select(root, "--allow-empty")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True

    classification = payload["classification"]["seg_blocked_regen"]
    assert classification["category"] == "human_escalation"
    assert classification["status"] == "segpack_read_failed"
    assert "not valid JSON" in classification["detail"]

    assert payload["classification"]["seg_reusable_control"] == {"category": "reusable"}


def test_blocked_regen_gate_invalid_utf8_segpack_escalates(tmp_path):
    root = setup_blocked_regen_and_reusable_project(tmp_path)
    (root / "segments" / "segpack_seg_blocked_regen.json").write_bytes(b"\xff\xfe")

    proc = run_select(root, "--allow-empty")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True

    classification = payload["classification"]["seg_blocked_regen"]
    assert classification["category"] == "human_escalation"
    assert classification["status"] == "segpack_read_failed"
    assert "not valid UTF-8" in classification["detail"]

    assert payload["classification"]["seg_reusable_control"] == {"category": "reusable"}


def test_blocked_regen_gate_nonmapping_generation_hashes_escalates(tmp_path):
    root = setup_blocked_regen_and_reusable_project(tmp_path)
    (root / "segments" / "segpack_seg_blocked_regen.json").write_text(
        json.dumps({"generation_hashes": ["bad"]}), encoding="utf-8"
    )

    proc = run_select(root, "--allow-empty")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True

    classification = payload["classification"]["seg_blocked_regen"]
    assert classification["category"] == "human_escalation"
    assert classification["status"] == "segpack_read_failed"
    assert "non-object 'generation_hashes'" in classification["detail"]

    assert payload["classification"]["seg_reusable_control"] == {"category": "reusable"}


# ---------------------------------------------------------------------------
# --durable-root PATH (LT-409): an explicit, caller-supplied root that
# REPLACES self-anchoring when given -- including where the ledger_merge.py
# AND cache_key.py subprocesses this script shells out to are found AND are
# themselves invoked with the same --durable-root. Byte-identical to
# today's self-anchored behavior when omitted.
# ---------------------------------------------------------------------------

def run_select_from(script_path, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(script_path), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_durable_root_flag_absent_orphan_copy_fails_self_anchored(tmp_path):
    """Negative control: an orphan copy invoked WITHOUT --durable-root
    cannot succeed via self-anchoring (no manifest.json to even read).

    The assertions name the specific reason rather than stopping at
    `success is False`, and the path assertion is the load-bearing one. A
    bare "it failed" control passes for ANY failure -- a syntax error, a
    missing dependency, or self-anchoring resolving to some entirely
    different root -- so it would keep this test green while the property it
    exists to protect quietly stopped holding, leaving the docstring as the
    only record of what was meant. Pinning that the script looked for
    manifest.json at the ORPHAN location's own parent is what proves
    self-anchoring resolved where it should have and simply found nothing
    there."""
    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "select_segments.py"
    shutil.copy2(SELECT_SCRIPT_SRC, orphan_script)

    proc = run_select_from(orphan_script, "--allow-empty")

    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "manifest.json not found" in payload["error"], (
        f"expected the self-anchored manifest lookup to be what failed, got: "
        f"{payload['error']!r}"
    )
    expected_lookup = orphan_dir.parent / "manifest.json"
    assert str(expected_lookup) in payload["error"], (
        f"self-anchoring must have resolved the durable root to the orphan "
        f"copy's own parent ({expected_lookup}); the failure names a "
        f"different path: {payload['error']!r}"
    )


def test_durable_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility: the ordinary in-place fixture, invoked with
    no --durable-root/--plugin-root at all, behaves exactly as before."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["segNoFlag"])

    proc = run_select(root)

    assert proc.returncode == 0
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["segs"] == ["segNoFlag"]


# ---------------------------------------------------------------------------
# --plugin-root PATH (LT-409, post-review correction): the SECURITY property
# this flag exists for. ${durable_root}/scripts/ is a Step-0a copy that the
# codex process can write to (codex_job.py grants --write over the whole
# durable root), so a sibling script resolved FROM durable_root could be a
# tampered copy validating itself. --plugin-root is a SEPARATE, orthogonal
# input that must NEVER be derived from --durable-root.
# ---------------------------------------------------------------------------

_TAMPERED_LEDGER_MERGE_SRC = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "sys.stderr.write('TAMPERED_LEDGER_MERGE_MUST_NEVER_RUN')\n"
    "sys.exit(97)\n"
)


def poison_durable_root_ledger_merge(root):
    """Overwrites the durable-root copy of ledger_merge.py with a stand-in
    for a codex-tampered script: it always fails loudly and distinctively
    rather than silently faking success, so a test can tell whether THIS
    copy ran at all, in either direction."""
    (root / "scripts" / "ledger_merge.py").write_text(
        _TAMPERED_LEDGER_MERGE_SRC, encoding="utf-8"
    )


def make_trusted_plugin_root(tmp_path, name="trusted_plugin_install"):
    """A SEPARATE physical location holding the REAL ledger_merge.py at the
    {plugin_root}/assets/scripts/ layout SKILL.md documents for the
    plugin-anchored scripts -- standing in for the plugin's actual install
    tree, physically apart from any durable_root fixture."""
    plugin_root = tmp_path / name
    plugin_scripts_dir = plugin_root / "assets" / "scripts"
    plugin_scripts_dir.mkdir(parents=True)
    shutil.copy2(LEDGER_MERGE_SRC, plugin_scripts_dir / "ledger_merge.py")
    (plugin_scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    return plugin_root


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling(tmp_path):
    """The core property: select_segments.py runs from its OWN in-place
    durable-root copy (production's normal invocation shape) whose SIBLING
    ledger_merge.py has been POISONED. --plugin-root pointing at a separate,
    untampered location must make it use THAT ledger_merge.py instead --
    success is possible ONLY if the poisoned durable-root sibling was never
    executed."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["segOnly"])
    poison_durable_root_ledger_merge(root)
    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = run_select(root, "--plugin-root", str(plugin_root))

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL ledger_merge.py must succeed "
        f"even though durable_root's own copy is poisoned -- a rc=1 with "
        f"the tamper sentinel below would mean the poisoned durable-root "
        f"copy ran instead of the trusted plugin-root one:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["segs"] == ["segOnly"]


def test_plugin_root_flag_absent_uses_the_poisoned_durable_root_sibling(tmp_path):
    """Negative control, and backward-compat proof in one: the SAME
    poisoned durable-root ledger_merge.py, invoked WITHOUT --plugin-root, is
    exactly what today's self-anchored lookup finds -- unchanged. The
    poisoned script genuinely runs and fails when the flag is omitted,
    proving the positive test's success above is attributable to
    --plugin-root specifically, not some other effect."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["segOnly"])
    poison_durable_root_ledger_merge(root)

    proc = run_select(root)  # no --plugin-root

    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "TAMPERED_LEDGER_MERGE_MUST_NEVER_RUN" in payload["error"]


# ---------------------------------------------------------------------------
# Coverage-gap fix: every --plugin-root test above poisons ledger_merge.py
# and uses "segOnly" with NO fragment written -- not_started, never
# converged. classify_converged_segment() (the only caller of
# compute_current_cache_key()) is never reached from any of them, so
# compute_current_cache_key()'s OWN plugin_root_str parameter was unpinned:
# its sibling parameters on the SAME function -- durable_root,
# durable_root_str, cache_key_script -- are already exercised (the
# relative-durable-root converged-segment test two sections up), but
# plugin_root_str never was. Mirrors the ledger_merge.py poisoning pattern
# exactly, one sibling over, with a CONVERGED segment and --durable-root
# OMITTED -- exercising compute_current_cache_key()'s own documented
# "durable_root_str is None but plugin_root_str IS" branch by name.
# ---------------------------------------------------------------------------

_TAMPERED_CACHE_KEY_SRC = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "sys.stderr.write('TAMPERED_CACHE_KEY_MUST_NEVER_RUN')\n"
    "sys.exit(97)\n"
)


def poison_durable_root_cache_key(root):
    """Overwrites the durable-root copy of cache_key.py with a stand-in for
    a codex-tampered script -- mirrors poison_durable_root_ledger_merge()'s
    own pattern. Leaves ledger_merge.py untouched, so a failure here is
    attributable to cache_key.py specifically, never conflated with the
    (already separately covered) ledger_merge.py redirect."""
    (root / "scripts" / "cache_key.py").write_text(_TAMPERED_CACHE_KEY_SRC, encoding="utf-8")


def test_plugin_root_flag_bypasses_a_tampered_cache_key_for_a_converged_segment(tmp_path):
    """--durable-root OMITTED entirely (self-anchored), --plugin-root
    pointing at a trusted copy of cache_key.py while durable_root's own
    copy is poisoned. Success is possible ONLY if
    compute_current_cache_key() actually resolved and ran the TRUSTED
    cache_key.py -- which requires it to have forwarded --durable-root to
    that subprocess despite --durable-root never being given to THIS
    script at all (see compute_current_cache_key()'s own docstring)."""
    root = make_durable_root(tmp_path)
    seg = "segConverged"
    write_manifest(root, [seg])
    key = make_cache_key("stable")
    sha1 = write_draft(root, seg, {"text": "stable content"})
    write_fragment(root, seg, converged_fragment(key, sha1))
    write_fixture_cache_keys(root, {seg: key})
    poison_durable_root_cache_key(root)
    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = run_select(root, "--allow-empty", "--plugin-root", str(plugin_root))

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL cache_key.py must succeed even "
        f"though durable_root's own copy is poisoned -- rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["classification"][seg]["category"] == "reusable", (
        f"the segment must classify via the TRUSTED plugin-root cache_key.py, "
        f"not escalate on the poisoned durable-root one. "
        f"{payload['classification'][seg]}"
    )


def test_plugin_root_flag_absent_uses_the_poisoned_durable_root_cache_key(tmp_path):
    """Negative control, and backward-compat proof in one: the SAME
    poisoned durable-root cache_key.py, invoked WITHOUT --plugin-root, is
    exactly what today's self-anchored lookup finds -- proving the positive
    test's success above is attributable to --plugin-root specifically."""
    root = make_durable_root(tmp_path)
    seg = "segConverged"
    write_manifest(root, [seg])
    key = make_cache_key("stable")
    sha1 = write_draft(root, seg, {"text": "stable content"})
    write_fragment(root, seg, converged_fragment(key, sha1))
    write_fixture_cache_keys(root, {seg: key})
    poison_durable_root_cache_key(root)

    proc = run_select(root, "--allow-empty")  # no --plugin-root

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["classification"][seg]["category"] == "human_escalation"
    assert "TAMPERED_CACHE_KEY_MUST_NEVER_RUN" in payload["classification"][seg]["detail"]


def test_durable_root_and_plugin_root_are_independently_resolved(tmp_path):
    """Orthogonality, end to end, from a fully orphan copy: --durable-root
    points at a DATA-only fixture with NO scripts/ directory AT ALL (so
    self-anchored/durable-root-derived sibling lookup could not possibly
    succeed), --plugin-root points at a SEPARATE, scripts-only fixture with
    no data of its own. Success proves the two concerns are genuinely
    resolved independently, never conflated into one root."""
    data_root = tmp_path / "data_only"
    data_root.mkdir()
    write_manifest(data_root, ["segOnly"])
    (data_root / "runs" / "ledger.d").mkdir(parents=True)
    schemas_dir = data_root / "schemas"
    shutil.copytree(SCHEMAS_SRC, schemas_dir)
    assert not (data_root / "scripts").exists(), (
        "fixture bug: data_root must have NO scripts/ dir at all"
    )

    plugin_root = make_trusted_plugin_root(tmp_path, name="plugin_only")

    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "select_segments.py"
    shutil.copy2(SELECT_SCRIPT_SRC, orphan_script)

    proc = run_select_from(
        orphan_script,
        "--durable-root", str(data_root),
        "--plugin-root", str(plugin_root),
    )

    assert proc.returncode == 0, (
        f"durable-root (data) and plugin-root (siblings) must resolve "
        f"independently -- got rc={proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["segs"] == ["segOnly"]
    assert payload["durable_root"] == str(data_root)
    # ledger_merge.py's own materialized ledger.json must land under the
    # DATA root, never under plugin_root.
    assert (data_root / "runs" / "ledger.json").is_file()
    assert not (plugin_root / "runs").exists()


def test_plugin_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility for the split itself: --durable-root alone
    (no --plugin-root) still resolves siblings self-anchored, exactly as
    before the split -- an in-place fixture with an UNTAMPERED
    ledger_merge.py still succeeds via --durable-root alone."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["segNoFlag"])

    proc = run_select(root, "--durable-root", str(root))

    assert proc.returncode == 0
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["segs"] == ["segNoFlag"]


# ---------------------------------------------------------------------------
# Doubled-path fix. run_ledger_merge()/compute_current_cache_key() run their
# sibling subprocess with `cwd` set to the ALREADY-RESOLVED durable_root, but
# used to forward the RAW (possibly relative) --durable-root string as that
# sibling's own --durable-root. The sibling's own resolve_dirs() does
# Path(durable_root_str).resolve(), which resolves a relative fragment
# against ITS cwd -- i.e. the already-resolved value a second time. The
# identical shape was independently confirmed in resume_setup.py and
# segment_dispatch_driver.py; --plugin-root had the same class of defect for
# a related reason (a relative override forwarded raw resolves against the
# CHILD's cwd, not the ORIGINAL invoker's cwd it was resolved against here).
# Every existing test above passes an absolute path for both flags, so none
# of them would have caught this -- these four exercise a genuinely relative
# override instead.
# ---------------------------------------------------------------------------


def test_relative_durable_root_is_not_doubled_end_to_end(tmp_path):
    """PROOF, end to end, against the REAL ledger_merge.py (not a probe
    stub): select_segments.py invoked with a genuinely RELATIVE
    --durable-root, from a cwd that is its own PARENT directory. Pre-fix,
    the raw 'durable_root' string was forwarded to ledger_merge.py, whose
    subprocess cwd is already {tmp_path}/durable_root -- so its own
    Path('durable_root').resolve() landed on
    {tmp_path}/durable_root/durable_root, which has no schemas/manifest,
    and ledger_merge.py failed outright (confirmed against this exact
    fixture at the parent commit). This drives the real subprocess boundary
    rather than asserting against source text."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["segRelative"])

    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "select_segments.py"), "--durable-root", "durable_root"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )

    assert proc.returncode == 0, (
        f"a relative --durable-root must resolve to the SAME tree as the "
        f"equivalent absolute one -- got rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["segs"] == ["segRelative"]
    assert payload["durable_root"] == str(root.resolve())
    assert (root / "runs" / "ledger.json").is_file(), (
        "ledger_merge.py must have materialized the ledger in the SAME "
        "tree select_segments.py itself resolved to, not one level deeper"
    )


def test_relative_durable_root_is_not_doubled_for_the_cache_key_sibling_end_to_end(tmp_path):
    """PROOF for the SECOND, independent call site: compute_current_cache_key()
    has its own --durable-root forwarding logic, not routed through
    _root_forward_args() at all (per this project's no-shared-lib
    convention), so it needed the identical fix applied separately. Only
    reachable by classifying a CONVERGED segment (the sole path that calls
    compute_current_cache_key() at all), against the fake cache_key.py stub
    -- which already mirrors the real script's own
    Path(durable_root).resolve() behavior, so a doubled path here means it
    looks for test_fixture_cache_keys.json one level too deep, doesn't find
    it, and cache_key.py fails -- escalating this segment to
    human_escalation instead of the reusable it actually is."""
    root = make_durable_root(tmp_path)
    seg = "segConvergedRelative"
    write_manifest(root, [seg])
    key = make_cache_key("stable")
    sha1 = write_draft(root, seg, {"text": "stable content"})
    write_fragment(root, seg, converged_fragment(key, sha1))
    write_fixture_cache_keys(root, {seg: key})

    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "select_segments.py"),
            "--durable-root",
            "durable_root",
            "--allow-empty",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["classification"][seg]["category"] == "reusable", (
        f"pre-fix, the doubled path made cache_key.py look for "
        f"test_fixture_cache_keys.json one level too deep, fail to find it, "
        f"and this segment would wrongly escalate to human_escalation "
        f"instead of classifying reusable. {payload}"
    )


def test_root_forward_args_never_forwards_a_relative_durable_root(tmp_path, monkeypatch):
    """Unit-level companion to the end-to-end proofs above, pinning
    _root_forward_args() directly: it must forward the RESOLVED
    durable_root, never the raw (possibly relative) CLI string."""
    module = load_select_segments_module()
    monkeypatch.chdir(tmp_path)
    dirs = module.resolve_dirs("some/relative/root", None)

    args = module._root_forward_args(dirs, "some/relative/root", None)

    expected = str((tmp_path / "some" / "relative" / "root").resolve())
    assert args == ["--durable-root", expected], (
        f"the forwarded value must equal the RESOLVED root exactly once, not "
        f"the raw relative string (which the sibling would resolve a SECOND "
        f"time against its own already-resolved cwd). got {args!r}, expected "
        f"['--durable-root', {expected!r}]"
    )
    assert not args[1].endswith(f"{expected}/some/relative/root"), (
        "the doubled-path shape itself, as a belt-and-suspenders check"
    )


def test_root_forward_args_never_forwards_a_relative_plugin_root(tmp_path, monkeypatch):
    """Unit-level companion for the --plugin-root half of the same fix: a
    relative override must be resolved against THIS script's own cwd (the
    same base resolve_dirs() already used for its own sibling lookup)
    BEFORE forwarding -- never passed through raw for the child to resolve
    against ITS OWN, different cwd."""
    module = load_select_segments_module()
    (tmp_path / "plugin_dir" / "assets" / "scripts").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    dirs = module.resolve_dirs(None, "plugin_dir")

    args = module._root_forward_args(dirs, None, "plugin_dir")

    assert args[0:2] == ["--durable-root", str(dirs["durable_root"])]
    expected_plugin_root = str((tmp_path / "plugin_dir").resolve())
    assert args[2:4] == ["--plugin-root", expected_plugin_root]
    assert "plugin_dir" != args[3], "must be resolved, not the raw fragment"


# ---------------------------------------------------------------------------
# #409 Step 1 -- the previously-converged re-translate gate.
#
# Why this exists: a converged segment becomes dispatch-eligible the moment any
# cache-key field moves, and a plugin upgrade moves plugin_bundle_hash for EVERY
# segment at once. Without this gate the next run silently re-translates
# finished, paid-for work. Measured at v1.17.0: 147 converged segments across
# three live projects.
#
# The predicate is the DURABLE sentinel, never the ledger status: the status is
# overwritten with `in_progress` before a re-dispatch, so a status-based guard
# does not fire on the path it exists to guard.
# ---------------------------------------------------------------------------

EVER_CONVERGED_SEG = "seg03_stale_cachekey"   # converged, then cache-key stale


def _mark_ever_converged(root, seg):
    """Raise the sentinel the way ledger_update.py does, by filename."""
    p = root / "segments" / f".ever_converged.{seg}"
    p.write_text("converged\n", encoding="utf-8")
    return p


def test_gate_refuses_by_default_when_a_previously_converged_segment_is_selected(tmp_path):
    root = setup_full_project(tmp_path)
    baseline = run_select(root)
    assert baseline.returncode == 0
    assert EVER_CONVERGED_SEG in parse_stdout(baseline)["segs"], (
        "precondition: the stale-cache-key segment must be dispatch-eligible by "
        "default, otherwise this test proves nothing"
    )

    _mark_ever_converged(root, EVER_CONVERGED_SEG)

    proc = run_select(root)
    assert proc.returncode != 0, (
        "a previously-converged segment must NOT be silently re-translated\n"
        f"stdout={proc.stdout!r}"
    )
    out = parse_stdout(proc)
    assert out["success"] is False
    assert EVER_CONVERGED_SEG in out["error"], "the refusal must name the segment"
    assert "--allow-retranslate-converged" in out["error"], (
        "the refusal must name the flag that authorizes it"
    )


def test_the_refusal_names_the_SECOND_loss_the_flag_does_not_ask_about(tmp_path):
    """#409: `--allow-retranslate-converged` authorizes one thing and costs
    two. The same cache-key move that made the converged segments stale also
    moves the resume digest, minting a fresh RUN_ID that orphans the
    dispatch_token on every NOT-yet-converged draft in the same selection --
    so those retranslate too, discarding any hand-applied fix. On a live
    project that was 21 authorized and 21 unmentioned, the silent half
    exactly the size of the half being asked about.

    Asserted against the specific numbers and the exact id set, not against
    the refusal firing at all: the refusal already fired before this text
    existed, so a `returncode != 0` assertion here would be green whether or
    not the second loss is named. The `not_yet_converged` exact-list
    assertion is what makes this a red attributable to THIS string."""
    root = setup_full_project(tmp_path)
    _mark_ever_converged(root, EVER_CONVERGED_SEG)

    proc = run_select(root)

    assert proc.returncode != 0
    out = parse_stdout(proc)
    expected_second = [s for s in parse_stdout(run_select(root, "--allow-retranslate-converged"))["segs"]
                       if s != EVER_CONVERGED_SEG]
    assert expected_second, (
        "precondition: the selection must hold at least one not-yet-converged "
        "segment, or this test proves nothing"
    )
    assert out["not_yet_converged"] == expected_second, out
    assert str(len(expected_second)) in out["error"], (
        "the operator must get the second COUNT, not just a caution"
    )
    for seg in expected_second:
        assert seg in out["error"], f"the refusal must name {seg}"
    # The condition must travel with the claim -- an unconditional warning
    # overstates (a fresh RUN_ID is not minted when the flag is passed against
    # an unchanged bundle) and an overstated warning is one people skip.
    assert "If this dispatch also mints a fresh RUN_ID" in out["error"], (
        "the second loss must be stated WITH its condition, never as a certainty"
    )


def test_no_second_loss_paragraph_when_nothing_else_is_selected(tmp_path):
    """FALSE-POSITIVE BOUND (stays green if the paragraph is deleted): when
    the converged segment is the only thing selected there is no second loss,
    and claiming one would be the overstatement the wording exists to avoid."""
    root = setup_full_project(tmp_path)
    _mark_ever_converged(root, EVER_CONVERGED_SEG)

    proc = run_select(root, "--only-segs", EVER_CONVERGED_SEG)

    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert out["not_yet_converged"] == []
    assert "THE SECOND NUMBER" not in out["error"]


def test_gate_permits_with_the_explicit_authorization_flag(tmp_path):
    root = setup_full_project(tmp_path)
    _mark_ever_converged(root, EVER_CONVERGED_SEG)

    proc = run_select(root, "--allow-retranslate-converged")
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert EVER_CONVERGED_SEG in out["segs"]
    assert out["authorizes_dispatch"] is True
    assert out["previously_converged"] == [EVER_CONVERGED_SEG]


def test_classify_only_reports_without_authorizing_a_dispatch(tmp_path):
    """final_audit.py's path: it needs the classification of a finished book --
    the normal state of which is 'many previously-converged segments' -- and
    must not be refused, because it never translates anything."""
    root = setup_full_project(tmp_path)
    _mark_ever_converged(root, EVER_CONVERGED_SEG)

    proc = run_select(root, "--classify-only")
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out["authorizes_dispatch"] is False, (
        "a classify-only call must not hand its caller a dispatch authorization"
    )
    assert out["previously_converged"] == [], (
        "classify-only does not evaluate the gate, so it reports no gate result"
    )
    assert EVER_CONVERGED_SEG in out["classification"], "the report is still produced"


def test_gate_fires_even_though_the_ledger_status_is_no_longer_converged(tmp_path):
    """THE decisive case, and the reason the predicate is a sentinel file.

    translateStage() writes `in_progress` BEFORE dispatching, and
    ledger_update.py rebuilds each fragment from scratch, so by the time a
    re-dispatch is decided the ledger no longer says `converged`. A guard
    reading the STATUS is therefore green exactly on the path it exists to
    guard. Here the fragment is overwritten with a non-converged status while
    the durable sentinel remains -- the refusal must still fire."""
    root = setup_full_project(tmp_path)
    _mark_ever_converged(root, EVER_CONVERGED_SEG)

    write_fragment(root, EVER_CONVERGED_SEG, in_progress_fragment())

    proc = run_select(root)
    assert proc.returncode != 0, (
        "the sentinel outlives the status, so the gate must still refuse; a "
        "status-based predicate would pass here and re-translate the segment\n"
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    err = parse_stdout(proc)["error"]
    assert EVER_CONVERGED_SEG in err
    assert "--allow-retranslate-converged" in err, (
        "must fail through THIS gate, not through some other fatal that happens "
        "to mention the same segment"
    )


# ---------------------------------------------------------------------------
# 1.19.1 -- the sentinel predicate is FAIL-CLOSED.
#
# The bug these cover: the two halves of the sentinel contract disagreed about
# the same path. The reader used `Path.exists()`, which FOLLOWS symlinks (a
# dangling link reads as absent) and, since Python 3.13, swallows every OSError
# and returns False (EACCES/ESTALE read as absent). The writer used
# `os.open(O_CREAT|O_EXCL)`, which raises EEXIST for ANY existing entry --
# dangling symlink and directory included -- and treated that as "already
# marked". So a segment could be recorded as converged while the dispatch gate
# saw it as unprotected, and the next cache-key move retranslated it. 1.19.1
# moves plugin_bundle_hash for every converged segment in every live project,
# which is precisely when that path gets taken.
#
# Fail-closed here points AWAY from tidiness: anything that is not a clean
# ENOENT is treated as "may have converged" and refuses, because a false
# "protected" costs one authorization and a false "absent" costs a translation.
# ---------------------------------------------------------------------------


def _sentinel_path(root, seg):
    return root / "segments" / f".ever_converged.{seg}"


def _load_script_module(name):
    """Load a script by path -- these are standalone entrypoints with no
    shared import, not an importable package. Same technique as
    test_sentinel_filename_matches_the_writer_in_ledger_update below, hoisted
    here because three tests in this section now need it."""
    path = ASSETS_DIR / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_dangling_symlink_sentinel_is_not_read_as_absent(tmp_path):
    """THE case the finding is about, reader half. `Path.exists()` follows the
    link and reports False for a dangling one, so the pre-fix gate let the
    segment through -- while the writer, whose O_CREAT|O_EXCL open gets EEXIST
    from that same link, had already reported the segment successfully marked.

    Fails on the unfixed code at `assert proc.returncode != 0`: the pre-fix
    reader classifies the dangling link as absent, no gate fires, and select
    exits 0 with the segment in `segs`."""
    root = setup_full_project(tmp_path)
    baseline = run_select(root)
    assert baseline.returncode == 0
    assert EVER_CONVERGED_SEG in parse_stdout(baseline)["segs"], (
        "precondition: the segment must be dispatch-eligible by default, "
        "otherwise this test proves nothing"
    )

    link = _sentinel_path(root, EVER_CONVERGED_SEG)
    link.symlink_to(root / "segments" / "no-such-target")
    assert not link.exists(), (
        "precondition: this must be a DANGLING link -- Path.exists() has to "
        "report False here, or the test is not exercising the reported bug"
    )
    assert link.is_symlink(), "precondition: the entry itself must be present"

    proc = run_select(root)

    assert proc.returncode != 0, (
        "a dangling symlink at the sentinel path is NOT proof the segment "
        "never converged; dispatching on it retranslates converged work\n"
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    out = parse_stdout(proc)
    assert out["ambiguous_sentinels"] == [
        {"seg": EVER_CONVERGED_SEG, "detail": "the entry is a symbolic link, not a regular file"}
    ], out
    assert "symbolic link" in out["error"], (
        "the refusal must say what is actually at the path, or an operator "
        "cannot act on it"
    )


def test_a_directory_at_the_sentinel_path_refuses_rather_than_counting_as_converged(tmp_path):
    """Writer half of the same finding, seen from the reader: `exists()` is
    True for a directory, so the pre-fix reader called it a valid sentinel and
    reported the segment as previously CONVERGED -- clearable with
    --allow-retranslate-converged, i.e. exactly one flag away from the same
    loss, and diagnosed with a message that names the wrong problem.

    Fails on the unfixed code at the `ambiguous_sentinels` assertion: pre-fix
    the run also exits non-zero, but through the previously-converged gate, so
    `ambiguous_sentinels` is missing from the payload entirely (KeyError) and
    `previously_converged` holds the segment instead. Asserting on the exit
    code alone would be green before the fix -- which is why this test does
    not."""
    root = setup_full_project(tmp_path)
    _sentinel_path(root, EVER_CONVERGED_SEG).mkdir()

    proc = run_select(root)

    assert proc.returncode != 0
    out = parse_stdout(proc)
    assert out["ambiguous_sentinels"] == [
        {"seg": EVER_CONVERGED_SEG, "detail": "the entry is a directory, not a regular file"}
    ], out
    assert "--allow-retranslate-converged does NOT clear this" in out["error"], (
        "a directory is not evidence the segment converged, so the flag that "
        "authorizes retranslating converged work must be named only to rule "
        "it OUT -- an operator who reaches for it here would authorize "
        "discarding work nobody established the state of"
    )
    assert out["previously_converged"] == [], (
        "and it must not be miscounted as a valid sentinel either -- that was "
        "the pre-fix reading, since Path.exists() is True for a directory"
    )


def test_a_non_enoent_lstat_error_is_ambiguous_not_absent(tmp_path):
    """The arm that motivated the finding: an EACCES / ESTALE / EIO on the
    lookup -- a REAL sentinel that this process simply cannot see. Simulated
    with a mode-000 parent directory, the same shape a stale NFS handle or a
    permissions accident produces.

    Since Python 3.13 `Path.exists()` swallows every OSError and returns
    False, so the pre-fix reader called such a segment 'never converged' and
    dispatched it. That divergence is asserted here directly, on one and the
    same path, rather than described.

    DELIBERATELY at the predicate level, not end-to-end, and that is not the
    awkwardness being dodged -- it is the only way this arm can prove
    anything. The lstat of `segments/.ever_converged.<seg>` can only be made
    to fail with EACCES by stripping permissions from `segments/` itself, and
    that same chmod also breaks the draft and segpack reads that
    classify_segment() performs BEFORE the sentinel loop is ever reached. An
    end-to-end version would refuse for an unrelated reason and be green
    whether or not this fix exists. The end-to-end "ambiguous means refuse"
    link is covered by the dangling-symlink and directory tests above, which
    can be isolated; this test owns the classification.

    Fails on the unfixed code at the `classify_ever_converged_sentinel`
    lookup itself (AttributeError -- the fail-closed predicate does not
    exist), and the `exists()` assertion below documents what the code did
    instead: reported absent."""
    import os as _os

    reader = _load_script_module("select_segments.py")
    locked = tmp_path / "segments"
    locked.mkdir()
    sentinel = locked / ".ever_converged.seg01"
    sentinel.write_text("converged\n", encoding="utf-8")

    _os.chmod(locked, 0o000)
    try:
        assert not sentinel.exists(), (
            "precondition: Path.exists() must report False for this EACCES "
            "lookup on this interpreter, or the test is not exercising the "
            "reported bug (on 3.8-3.12 exists() re-raised instead)"
        )
        state, detail = reader.classify_ever_converged_sentinel(sentinel)
    finally:
        _os.chmod(locked, 0o755)

    assert state == reader.SENTINEL_AMBIGUOUS, (
        "a lookup that FAILED is not a lookup that found nothing; treating "
        f"EACCES as absence retranslates a segment whose sentinel is right "
        f"there and merely unreadable -- got {state!r}"
    )
    assert "EACCES" in detail, f"the errno must reach the operator, got {detail!r}"
    assert sentinel.read_text(encoding="utf-8") == "converged\n", (
        "and the sentinel really was there the whole time"
    )


def test_an_ordinary_regular_sentinel_still_protects_and_absence_still_dispatches(tmp_path):
    """FALSE-POSITIVE BOUND for the two arms above. The fix must not turn a
    fail-closed predicate into a fail-always one: an absent sentinel (clean
    ENOENT) must still permit dispatch, and an ordinary regular sentinel must
    still land in `previously_converged` -- not in `ambiguous_sentinels`.

    Green both before and after the fix by design; it exists to catch a fix
    that over-blocks, which the discriminating tests above cannot see."""
    root = setup_full_project(tmp_path)

    permitted = run_select(root)
    assert permitted.returncode == 0, f"stderr={permitted.stderr!r}"
    out = parse_stdout(permitted)
    assert EVER_CONVERGED_SEG in out["segs"], "ENOENT must still mean 'dispatch'"
    assert out["ambiguous_sentinels"] == []

    _mark_ever_converged(root, EVER_CONVERGED_SEG)
    refused = run_select(root)
    assert refused.returncode != 0
    refused_out = parse_stdout(refused)
    assert refused_out["ambiguous_sentinels"] == [], (
        "an ordinary regular sentinel is unambiguous -- it must refuse through "
        "the previously-converged gate, not through the ambiguity gate"
    )
    assert EVER_CONVERGED_SEG in refused_out["error"]
    assert "--allow-retranslate-converged" in refused_out["error"]


def test_the_writer_refuses_to_record_convergence_for_a_dangling_symlink(tmp_path):
    """Writer half, directly. `os.open(O_CREAT|O_EXCL)` raises FileExistsError
    for a dangling symlink exactly as it does for a real sentinel, so the
    pre-fix `except FileExistsError: return True` reported the segment marked
    while nothing a reader could find had been published.

    Driven through ledger_update.py's own mark_ever_converged() rather than a
    reimplementation of it -- the point is what the shipped writer does.

    Fails on the unfixed code at `assert ok is False`: pre-fix it returns
    True. (The directory arm is asserted in the same test because it is the
    same branch and the same one-line pre-fix behavior.)"""
    writer = _load_script_module("ledger_update.py")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()

    link = segments_dir / ".ever_converged.segLINK"
    link.symlink_to(segments_dir / "no-such-target")
    ok = writer.mark_ever_converged("segLINK", segments_dir)
    assert ok is False, (
        "EEXIST from a dangling symlink is not proof of prior marking; "
        "returning True records convergence with no sentinel in place"
    )
    assert link.is_symlink(), "the writer must not have replaced the entry"

    (segments_dir / ".ever_converged.segDIR").mkdir()
    assert writer.mark_ever_converged("segDIR", segments_dir) is False, (
        "a directory is not a sentinel either"
    )

    real = segments_dir / ".ever_converged.segOK"
    real.write_text("converged\n", encoding="utf-8")
    assert writer.mark_ever_converged("segOK", segments_dir) is True, (
        "FALSE-POSITIVE BOUND: an ordinary regular sentinel must still be "
        "idempotently accepted, or the fix breaks every re-record"
    )
    assert writer.mark_ever_converged("segFRESH", segments_dir) is True, (
        "FALSE-POSITIVE BOUND: a clean create must still succeed"
    )


def test_an_oserror_with_no_errno_is_ambiguous_not_absent(tmp_path):
    """`OSError.errno` is typed `int | None` and really can be None -- pyright
    flagged the original `errno.errorcode.get(exc.errno)` for exactly that.
    The fix must not resolve the type error by making None mean absence, which
    is the one direction that destroys work, so this pins the direction rather
    than merely the absence of a crash.

    Driven with a stub path whose lstat raises a bare `OSError()`: there is no
    portable filesystem state that produces an errno-less OSError, and the
    alternative -- trusting the type annotation -- is what let it through the
    first time.

    Green before the fix only in the sense that the function did not exist;
    against a fix that reached for `cast()` or a `# type: ignore` it stays
    green, and against a fix that treated a None errno as ENOENT it FAILS at
    `assert state == reader.SENTINEL_AMBIGUOUS`."""
    reader = _load_script_module("select_segments.py")

    class _ErrnolessPath:
        def lstat(self):
            raise OSError()  # no errno, no strerror

    state, detail = reader.classify_ever_converged_sentinel(_ErrnolessPath())

    assert state == reader.SENTINEL_AMBIGUOUS, (
        "an OSError carrying no errno is the LEAST informative failure there "
        "is -- it cannot be evidence that the segment never converged"
    )
    assert "no errno" in detail, detail


SENTINEL_SCRIPTS = (
    "ledger_update.py",           # the only writer
    "select_segments.py",         # the #409 Step 1 dispatch gate
    "final_audit.py",             # the completeness carve-out
    "backfill_ever_converged.py",  # the already_sentineled scan (reader+writer)
)

def _folded_str_literals(src, skip_docstrings=False):
    """Every string a module's source CONSTRUCTS from literals alone: plain
    constants, `+` concatenations of them, and the literal parts of f-strings.

    Exists because a source-TEXT census is defeated by splitting the needle --
    `".ever_" + "converged." + seg` contains `ever_converged` nowhere in the
    source, but builds it at runtime. Folding is done here rather than with
    `ast.literal_eval`, which refuses `+` on strings (it supports binary +/-
    for NUMBERS only, to admit complex literals) and would therefore return
    nothing for exactly the shape this needs to catch -- a vacuous guard that
    looks like a working one.

    `ast.parse()` reads source only: no import, no execution, so it is safe for
    scripts that are deliberately import-free.

    KNOWN GAPS, measured rather than guessed: `%` interpolation, `.format()`,
    `"".join()` of constants, and f-string constants formatted separately are
    NOT folded, and an f-string's `{...}` holes are treated as empty, which can
    join two literal fragments that runtime would keep apart (a false POSITIVE,
    the safe direction here). So this narrows evasion to shapes no participant
    would reach for by accident; it does not make evasion impossible.

    `skip_docstrings` drops module/class/function docstrings, which is what
    separates a file that DISCUSSES the convention from one that BUILDS it.
    Without it the two are indistinguishable at file granularity, and a
    whitelisted file can host a real participant inside its own exemption."""
    import ast

    def fold(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):  # f-string: keep the literal parts
            return "".join(p for p in (fold(v) for v in node.values) if p is not None)
        if isinstance(node, ast.FormattedValue):  # the {...} holes contribute nothing
            return ""
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = fold(node.left), fold(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    try:
        tree = ast.parse(src)
    except SyntaxError:  # not our problem here -- other tests own syntax
        return []

    skipped = set()
    if skip_docstrings:
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ) or not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                skipped.add(id(first.value))

    return [
        s
        for node in ast.walk(tree)
        if id(node) not in skipped and (s := fold(node)) is not None
    ]


# The sentinel's public API. Every literal needle above looks at STRINGS; these
# are IDENTIFIERS, and a file can become a genuine participant without
# containing the token in any literal at all -- calling the helpers through an
# injected provider needs no string whatsoever. Measured: a
# `provider.classify_ever_converged_sentinel(provider.ever_converged_path(seg))`
# added to a whitelisted file passed every literal-based needle here.
SENTINEL_API_NAMES = (
    "ever_converged_path",
    "classify_ever_converged_sentinel",
    "mark_ever_converged",
)


def _names_bound_by(target):
    """Every bare name a binding target introduces, however nested: `a`,
    `(a, b)`, `[a, *rest]`."""
    return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}


def _sentinel_api_refs(src):
    """Every CODE-level use of a SENTINEL_API_NAMES identifier: definitions,
    bare-name loads, attribute accesses, and imports.

    The literal needles cannot see any of these. Note `mark_ever_converged`
    especially: the census pins `def ever_converged_path` and `def
    classify_ever_converged_sentinel` by exact spelling but never looked for the
    WRITER's definition, so a third copy of it could appear in a whitelisted
    file and change none of the five literal sets.

    Bare names count only in a LOAD context: `ever_converged_path = None`
    rebinds the name without using the API, and reporting it as participation
    would be a false red on code that is doing nothing of the kind.

    ATTRIBUTES count in EVERY context, load or store, and that asymmetry is the
    whole point. `provider.ever_converged_path = path_builder` is an injected
    provider being WIRED UP -- participation in the most direct sense -- and an
    earlier revision narrowed attributes along with bare names, which put that
    exact shape back through the census untouched in an exempted file. The
    narrowing was described in its own commit message as applying to bare names
    and silently applied to attributes too.

    ATTRIBUTE matching is deliberately by NAME ALONE, so any object's
    `.ever_converged_path` counts. That is the whole point -- reaching the
    helpers through an injected provider is exactly the participation shape the
    literal needles miss, and no receiver-type analysis is available here. The
    accepted cost: an unrelated attribute that happens to share one of these
    three names reads as participation. They are specific enough that such a
    collision is itself worth a look, which is the failure direction to prefer.

    Reports WHICH names matched, not merely that something did; the census
    collapses that to a per-file set because it asks which FILES participate,
    while the exemption check below prints the names."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    hits = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in SENTINEL_API_NAMES:
                hits.add(node.name)
        elif isinstance(node, ast.Name) and node.id in SENTINEL_API_NAMES:
            if isinstance(node.ctx, ast.Load):
                hits.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in SENTINEL_API_NAMES:
            hits.add(node.attr)  # every context -- see the docstring
        elif isinstance(node, ast.alias):
            if node.name in SENTINEL_API_NAMES or node.asname in SENTINEL_API_NAMES:
                hits.add(node.asname or node.name)
    return hits


def _api_names_bound_by(src):
    """Sentinel API names this file binds by any STATIC binding form other than
    `def` -- assignment, annotated assignment, walrus, augmented assignment, a
    `for`/`with`/`except`/`match` target, or a class definition.

    `mark_ever_converged = write_marker` publishes the writer under its public
    name while matching none of the census needles: not a `def`, not an import
    alias, not a Load of the name, and carrying no `ever_converged` literal.
    Used by the exemption role check, which is a promise not to touch the
    convention -- a stricter bar than the census's "which files use it".

    NOT exhaustive, and the earlier claim that it refused these "by any means"
    was wrong: a binding built at runtime -- `globals().update(...)`,
    `setattr()` with a computed name, a dict export consumed elsewhere -- is
    not visible to any static walk. What is enumerated is every form that
    appears in this codebase; the rest is disclosed rather than implied."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    api = set(SENTINEL_API_NAMES)
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                bound |= _names_bound_by(target) & api
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
            bound |= _names_bound_by(node.target) & api
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bound |= _names_bound_by(node.target) & api
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            bound |= _names_bound_by(node.optional_vars) & api
        elif isinstance(node, ast.ExceptHandler) and node.name in api:
            bound.add(node.name)
        elif isinstance(node, ast.MatchAs) and node.name in api:
            bound.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name in api:
            bound.add(node.name)
    return bound


# Scripts that mention `ever_converged` WITHOUT touching the marker: the
# census's loose needle would otherwise flag them forever. Membership here is
# not trust -- the census re-checks every listed name for every executable
# participation signal on every run, so a file that starts participating fails
# even though it is listed.
SENTINEL_NON_PARTICIPANTS = (
    "backfill_resume_gate_ack.py",  # mirrors the shape for `.resume_gate_ack`
    "resume_setup.py",              # cites mark_ever_converged()'s O_EXCL semantics
    # #438's claim record. It owns a SEPARATE marker (`.claimed.<seg>`) with
    # its own three-state predicate, and names `.ever_converged` only in prose
    # -- to explain why the ledger fragment was rejected as a home, and that
    # its predicate is shared by import rather than being a fifth duplicate.
    # It touches neither the marker nor any sentinel helper, which is what the
    # ROLE check below re-verifies.
    "claim_record.py",
    # #461's review-rejection tool. It owns its OWN durable record
    # (`<seg>.review_rejected.json`, a sibling of the review artifact rather
    # than a dot-prefixed marker) and names `.ever_converged` exactly once, in prose,
    # to say which gap the sentinel closes and why a rejection is a different
    # fact from a convergence. It reads no sentinel, writes no sentinel, and
    # carries no copy of the predicate -- re-verified by the ROLE check below
    # rather than taken on the strength of this comment.
    "reject_review.py",
)


def test_exactly_these_four_scripts_participate_in_the_sentinel_contract():
    """CENSUS. The drift test below pins the four copies in SENTINEL_SCRIPTS
    against each other -- and would go on passing, quietly covering three
    copies, if someone deleted one, or five, if someone added a fifth
    elsewhere in scripts/. A pairwise-agreement test cannot see its own
    population change; that is the whole reason this one exists beside it.

    Not a hypothetical shape for this codebase: `draft_content_sha1` is
    currently implemented in SEVEN scripts under assets/scripts/ (assemble,
    draft_sha1, final_audit, ledger_merge, ledger_update, select_segments,
    validate_assembled), each documented as byte-identical to the others.
    A convention duplicated N ways is one edit away from being duplicated
    N+1 ways with nothing pointing at the newcomer.

    NEEDLE CHOICE matters and was measured, not assumed. THREE executable
    needles pin the participants -- the marker f-string as actually written,
    the predicate's `def` line, and `def ever_converged_path` -- and each
    yields exactly these four today. The third is not redundant: it is
    independent of how the marker FILENAME is spelled, so a participant that
    builds the path as `".ever_converged." + seg` still trips it.

    A FOURTH, deliberately loose needle scans for the bare token
    `ever_converged` and pins the two files that legitimately mention it
    without participating (SENTINEL_NON_PARTICIPANTS). Those two are checked
    BY ROLE, not trusted by name: each is re-asserted every run to carry
    none of the executable signals. Trusting a name is how a listed file
    quietly becomes a participant -- the listing that excused its prose
    mention goes on excusing its code.

    A FIFTH needle is not a text scan at all. `_folded_str_literals()` reads
    the VALUE each literal expression evaluates to, so `".ever_" +
    "converged." + seg` is caught even though the source contains the token
    nowhere. That shape was this census's known bypass for exactly one
    revision, on the stated grounds that closing it needed import analysis
    of import-free scripts -- which was wrong, and worth recording as the
    error it was: `ast.parse()` reads source and imports nothing, so the
    rationale for leaving the hole open did not survive being checked.

    The non-participant exemptions are checked at OCCURRENCE granularity, not
    file granularity. Both listed files already appear in every file-level
    token set, because their docstrings discuss the marker -- so a file-level
    role check cannot tell "discusses the convention" from "builds it", and a
    real participant added inline to a listed file passed this whole census.
    Measured, not hypothetical. The role check therefore re-scans each listed
    file with docstrings dropped and requires ZERO remaining sites.

    A SIXTH needle reads IDENTIFIERS, not literals, and it exists because
    every one of the five above shares a blind spot none of them records:
    they all ask what a file SPELLS, and participation needs no spelling at
    all. `provider.classify_ever_converged_sentinel(provider.ever_converged_
    path(seg))` is a genuine participant containing no `ever_converged`
    literal anywhere -- measured, it passed all five, including the
    occurrence-level exemption check. `_sentinel_api_refs()` closes it, and
    incidentally closes a hole nobody had named: the `def` needles pin
    `ever_converged_path` and `classify_ever_converged_sentinel` but never
    the WRITER, so a third copy of `mark_ever_converged` could appear in a
    whitelisted file and move none of the five sets.

    WHAT THIS CENSUS DOES NOT ASK, and deliberately no longer tries to: it
    pins WHICH files participate, never what they DO with the marker. A
    participant that quietly reintroduces `ever_converged_path(seg).exists()`
    -- the raw read this release removed -- passes every needle here.

    A guard for that existed for four rounds and was removed. It tried to
    recognise a raw read through variable bindings, and across three revisions
    it was wrong on 12 of 16 constructs, then 7 of 28, then on a `match`
    capture, a walrus in a default argument, a class-scope comprehension
    iterable and a 64-deep alias chain. The narrowed syntactic replacement then
    needed a whole-file veto to avoid firing on a shadowed parameter, and that
    veto could be tripped by an UNRELATED shadow elsewhere in a participant --
    silently disabling enforcement for that whole file, which is worse than no
    guard because it looks like one. Four consecutive review rounds found their
    only defects inside it and none in the code it was watching. A tripwire
    whose own defect rate exceeds the drift it catches is not a tripwire, and
    the honest disclosure is this paragraph rather than a fifth attempt.

    What still pins the contract: the six needles below (WHO participates), the
    `inspect.getsource` identity check (all four copies byte-identical), and
    the five-state matrix (what the predicate ANSWERS).

    KNOWN LIMITS, measured rather than asserted: the folder handles `+`,
    implicit adjacency and f-string literal parts, but NOT `%`, `.format()`,
    `"".join()` of constants, or separately formatted f-string constants; and
    it treats f-string holes as empty, which can join fragments runtime keeps
    apart (a false POSITIVE -- the safe direction). A path built from
    non-literals at runtime still evades the five LITERAL needles -- but a
    participant doing that has to reach the marker somehow, and reaching it
    through the shared API trips the sixth. The residue is a file that
    reimplements the whole convention from non-literal parts under its own
    names, which is concealment, not drift. None of this makes the census
    complete, and an earlier revision of this docstring overclaimed exactly
    that.

    THE SCAN PATTERN IS ITSELF A DEPENDENCY, and a narrowed one is the
    failure this guard exists to catch, so it is checked by INDEPENDENT
    ENUMERATION rather than by a floor on the count. Measured on the tree
    that shipped 1.20.0: `*.py` scans 44 files, and the plausible typo
    `*_*.py` scans 42 -- still finds all four participants, and still
    satisfied every assertion here under the old `scanned > 10` floor. A
    floor cannot separate "scanned everything" from "scanned almost
    everything and happened to keep the needles"; walking the tree a second
    way can. `rglob` + `os.walk` rather than `glob` + `iterdir` so a
    participant added in a SUBDIRECTORY is seen by both -- the directory is
    flat today, which is exactly when that blind spot is free to close.

    Every set here is keyed by RELATIVE PATH, never basename. Two
    enumerations that both discard directory identity agree with each other
    while being wrong together: a nested copy of an existing participant
    collapses onto it, both sets stay unchanged, and the census passes. That
    is not hypothetical -- it was measured on a `nested/ledger_update.py`
    that this test passed while keying on `py.name`. Agreement between two
    mechanisms proves nothing about a property NEITHER of them records.

    Fails in BOTH directions: a fifth participant makes the scanned set a
    superset, deleting one makes it a subset, and the assertion prints the
    symmetric difference either way."""
    import os as _os

    scripts_dir = ASSETS_DIR / "scripts"
    assert scripts_dir.is_dir(), scripts_dir

    path_builders = set()
    predicate_copies = set()
    path_helpers = set()
    mentions_token = set()
    builds_token = set()
    api_refs = set()
    scanned = set()
    for py in sorted(scripts_dir.rglob("*.py")):
        src = py.read_text(encoding="utf-8")
        # RELATIVE PATH, never `py.name`: a basename discards directory
        # identity, so a nested copy of an existing participant collapses onto
        # it and every set below stays unchanged. Measured -- copying
        # ledger_update.py to scripts/nested/ledger_update.py produced a fifth
        # participant that this census PASSED while keying on basenames.
        rel = py.relative_to(scripts_dir).as_posix()
        scanned.add(rel)
        if 'f".ever_converged.{seg}"' in src:
            path_builders.add(rel)
        if "def classify_ever_converged_sentinel" in src:
            predicate_copies.add(rel)
        if "def ever_converged_path" in src:
            path_helpers.add(rel)
        if "ever_converged" in src:
            mentions_token.add(rel)
        if any("ever_converged" in s for s in _folded_str_literals(src)):
            builds_token.add(rel)
        # The only needle that reads IDENTIFIERS rather than literals. Every
        # other one can be evaded with no string at all -- see
        # _sentinel_api_refs()'s docstring.
        if _sentinel_api_refs(src):
            api_refs.add(rel)

    # The scan above is the one input every assertion below inherits, and a
    # narrowed pattern keeps them all green (see docstring). Enumerate the same
    # tree by a DIFFERENT mechanism -- os.walk plus a suffix test, no pattern
    # matching at all -- and require the two to agree exactly.
    inventory = {
        (Path(root) / f).relative_to(scripts_dir).as_posix()
        for root, _dirs, files in _os.walk(scripts_dir)
        for f in files
        if f.endswith(".py")
    }
    assert scanned == inventory, (
        f"the scan pattern and an independent walk of {scripts_dir} disagree: "
        f"{scanned ^ inventory}. The pattern is narrower (or wider) than "
        f"'every .py under this directory', so every set built from it below is "
        f"answering a different question than the one this test asks."
    )

    expected = set(SENTINEL_SCRIPTS)
    assert path_helpers == expected, (
        f"the set of scripts defining `ever_converged_path()` has changed: "
        f"{path_helpers ^ expected}. This needle is independent of how the "
        f"marker's FILENAME is spelled, so it catches a participant whose path "
        f"literal the exact needle above misses."
    )
    assert mentions_token == expected | set(SENTINEL_NON_PARTICIPANTS), (
        f"a script mentions `ever_converged` without being either a pinned "
        f"participant or a pinned non-participant: "
        f"{mentions_token ^ (expected | set(SENTINEL_NON_PARTICIPANTS))}. If it "
        f"touches the marker, it is a participant and must join SENTINEL_SCRIPTS "
        f"with the shared predicate; if it only refers to the convention in "
        f"prose, add it to SENTINEL_NON_PARTICIPANTS -- which is checked by ROLE "
        f"below, not merely trusted by name."
    )
    # The needle a raw text scan cannot be: this one sees the VALUE a literal
    # evaluates to, so `".ever_" + "converged." + seg` and an f-string are both
    # caught while neither contains the token contiguously in the source.
    assert builds_token == expected | set(SENTINEL_NON_PARTICIPANTS), (
        f"a script CONSTRUCTS a string containing `ever_converged` without being "
        f"pinned: {builds_token ^ (expected | set(SENTINEL_NON_PARTICIPANTS))}. "
        f"Concatenated or f-string spellings are caught here even when the raw "
        f"text scan above misses them."
    )
    # The needle that needs no literal at all. Note the expected set has NO
    # non-participants in it: a file may DISCUSS the marker in prose (and so sit
    # in the two sets above), but referencing the API by name is participation,
    # full stop. This is what separates the two.
    assert api_refs == expected, (
        f"the set of scripts REFERENCING the sentinel API by identifier has "
        f"changed: {api_refs ^ expected}. Every needle above this one reads "
        f"string literals, so a file that calls the helpers through an injected "
        f"provider -- or defines its own copy of the writer -- is invisible to "
        f"all of them. Add it to SENTINEL_SCRIPTS with the shared predicate, or "
        f"remove the reference."
    )
    # A name on the non-participant list is not taken on trust: each one is
    # re-checked every run for every executable participation signal. The
    # failure this closes is a listed file QUIETLY BECOMING a participant, where
    # the listing that excused its prose mention would otherwise excuse its code.
    #
    # `builds_token` cannot be used here, and that gap was a MEASURED
    # false-green, not a theoretical one: both listed files are ALREADY in
    # `builds_token` because their docstrings discuss the marker, so a real
    # participant added inline -- `(segments_dir / (".ever_" + "converged." +
    # seg)).lstat()` -- changed none of the five sets and passed every
    # assertion. File granularity cannot separate DISCUSSING the convention
    # from BUILDING it; occurrence granularity can. Hence the docstring-skipping
    # scan: for these two files the count of non-docstring token sites is 0,
    # and the mutant above makes it 1.
    for name in SENTINEL_NON_PARTICIPANTS:
        assert name not in path_builders | predicate_copies | path_helpers, (
            f"{name} is on SENTINEL_NON_PARTICIPANTS but now carries an "
            f"executable participation signal. It is a participant: move it to "
            f"SENTINEL_SCRIPTS and give it the shared predicate."
        )
        executable_sites = [
            s
            for s in _folded_str_literals(
                (scripts_dir / name).read_text(encoding="utf-8"), skip_docstrings=True
            )
            if "ever_converged" in s
        ]
        assert not executable_sites, (
            f"{name} is on SENTINEL_NON_PARTICIPANTS but builds a string "
            f"containing `ever_converged` OUTSIDE a docstring: "
            f"{executable_sites}. Its exemption covers discussing the "
            f"convention, not touching the marker -- move it to "
            f"SENTINEL_SCRIPTS and give it the shared predicate."
        )
        # The literal check above still cannot see a file that participates
        # WITHOUT a string: `provider.ever_converged_path(seg)` names the API
        # and builds nothing. Measured -- that mutant passed both the file-level
        # sets and the zero-non-docstring-literal count above.
        exempt_src = (scripts_dir / name).read_text(encoding="utf-8")
        api_used = _sentinel_api_refs(exempt_src)
        assert not api_used, (
            f"{name} is on SENTINEL_NON_PARTICIPANTS but references the sentinel "
            f"API by identifier: {sorted(api_used)}. Naming a helper is using it, "
            f"whether or not any `ever_converged` literal appears -- move it to "
            f"SENTINEL_SCRIPTS and give it the shared predicate."
        )
        # An exempted file may not BIND an API name either. A `def` is caught
        # above; an assignment is not, and `mark_ever_converged = write_marker`
        # defines the writer's public name just as effectively -- measured,
        # that shape passed every other check here. This is deliberately
        # stricter than the census-wide needle: the census asks which files USE
        # the API, while an exemption is a promise not to touch the convention
        # at all.
        #
        # It is a NAME check, not a dataflow one, and both directions are
        # approximate. It misses binding forms `_api_names_bound_by` does not
        # walk (parameters, comprehension targets, `match` star and mapping-
        # rest captures, imports -- though imports are caught upstream by
        # `_sentinel_api_refs`). And it can fire on an unrelated local that
        # merely COLLIDES with an API name, e.g. `for mark_ever_converged in
        # items`. A false red here is loud and one rename fixes it; the miss
        # is the direction that matters, and it is not closed.
        bound_api_names = sorted(_api_names_bound_by(exempt_src))
        assert not bound_api_names, (
            f"{name} is on SENTINEL_NON_PARTICIPANTS but BINDS a sentinel API "
            f"name: {bound_api_names}. Assigning the name publishes the helper "
            f"under it. If this is an unrelated local that merely collides "
            f"with an API name, rename the local; otherwise move the file to "
            f"SENTINEL_SCRIPTS."
        )
    assert path_builders == expected, (
        f"the set of scripts that BUILD the sentinel path has changed: "
        f"{path_builders ^ expected}. Add it to SENTINEL_SCRIPTS (and give it "
        f"the shared predicate), or remove it -- an unlisted participant is "
        f"invisible to the drift test below."
    )
    assert predicate_copies == expected, (
        f"the set of scripts carrying a copy of the shared predicate has "
        f"changed: {predicate_copies ^ expected}. A script that builds the "
        f"sentinel path but does NOT carry the predicate is the exact "
        f"pre-1.19.1 state: a call site free to disagree with the writer."
    )


def test_sentinel_predicate_is_identical_in_all_four_scripts(tmp_path):
    """The predicate is spelled in FOUR standalone scripts with no shared
    import, so pin all four copies against each other across the WHOLE state
    matrix -- not just the happy path. A drift test, not a second source of
    truth, same technique as the filename pin below.

    This is the test that keeps the fix from decaying back into the bug: the
    bug WAS the sentinel's readers and its writer disagreeing about one path,
    and a divergence reintroduced in any single copy is invisible to every
    other test here, because that script's own tests would still agree with
    it. Four copies means six pairs that can drift, so the comparison is
    against one reference rather than pairwise.

    NOTE the four map AMBIGUOUS to different ACTIONS on purpose (refuse,
    refuse, count, report) -- that divergence is correct and lives at the call
    sites. What must never diverge is the CLASSIFICATION, which is what this
    pins."""
    import os as _os

    modules = {name: _load_script_module(name) for name in SENTINEL_SCRIPTS}
    reference_name = SENTINEL_SCRIPTS[0]
    reference = modules[reference_name]

    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()

    absent = segments_dir / "absent"
    regular = segments_dir / "regular"
    regular.write_text("converged\n", encoding="utf-8")
    dangling = segments_dir / "dangling"
    dangling.symlink_to(segments_dir / "no-such-target")
    a_dir = segments_dir / "a_dir"
    a_dir.mkdir()
    locked_parent = segments_dir / "locked"
    locked_parent.mkdir()
    inaccessible = locked_parent / "sentinel"
    inaccessible.write_text("converged\n", encoding="utf-8")

    cases = [
        ("absent (ENOENT)", absent, reference.SENTINEL_ABSENT),
        ("ordinary regular file", regular, reference.SENTINEL_PRESENT),
        ("dangling symlink", dangling, reference.SENTINEL_AMBIGUOUS),
        ("directory", a_dir, reference.SENTINEL_AMBIGUOUS),
    ]

    _os.chmod(locked_parent, 0o000)
    try:
        cases.append(("EACCES lstat", inaccessible, reference.SENTINEL_AMBIGUOUS))
        for label, path, expected_state in cases:
            expected = reference.classify_ever_converged_sentinel(path)
            assert expected[0] == expected_state, f"{label}: got {expected!r}"
            for name, mod in modules.items():
                got = mod.classify_ever_converged_sentinel(path)
                assert got == expected, (
                    f"{label}: {name} disagrees with {reference_name} about "
                    f"the same path -- {name}={got!r} {reference_name}="
                    f"{expected!r}. That disagreement IS the 1.19.1 data-loss "
                    f"bug; it does not matter which of the two is 'right'."
                )
    finally:
        _os.chmod(locked_parent, 0o755)

    import inspect

    for name, mod in modules.items():
        assert (
            mod.SENTINEL_ABSENT,
            mod.SENTINEL_PRESENT,
            mod.SENTINEL_AMBIGUOUS,
        ) == (
            reference.SENTINEL_ABSENT,
            reference.SENTINEL_PRESENT,
            reference.SENTINEL_AMBIGUOUS,
        ), f"{name}: state names must match {reference_name}'s, not just the behavior"

        for fn in ("classify_ever_converged_sentinel", "_sentinel_entry_kind"):
            assert inspect.getsource(getattr(mod, fn)) == inspect.getsource(
                getattr(reference, fn)
            ), (
                f"{name}.{fn} has drifted from {reference_name}'s copy. The "
                f"four are meant to be textually identical: the matrix above "
                f"samples five states, so a drift in the detail strings, in "
                f"the entry-kind names, or in a branch it does not reach "
                f"would otherwise pass unseen"
            )


def test_sentinel_filename_matches_the_writer_in_ledger_update(tmp_path):
    """The convention is spelled in two standalone scripts with no shared
    import (ledger_update.py WRITES it, select_segments.py READS it). Pin them
    against each other by name so a rename in one is not a silent no-op in the
    other -- which would disable the gate while every test above still passes,
    because they would agree with the reader."""
    import importlib.util

    def _load(name):
        path = ASSETS_DIR / "scripts" / name
        spec = importlib.util.spec_from_file_location(name.replace(".", "_"), str(path))
        assert spec is not None and spec.loader is not None, f"cannot load {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    writer = _load("ledger_update.py")
    reader = _load("select_segments.py")
    segments_dir = tmp_path / "segments"
    seg = "segX"
    assert (
        writer.ever_converged_path(seg, segments_dir).name
        == reader.ever_converged_path(seg, segments_dir).name
    ), "ledger_update.py writes a sentinel select_segments.py would never find"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
