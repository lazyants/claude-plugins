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
import hashlib
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
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DURABLE_ROOT = HERE.parent
KEYS_PATH = DURABLE_ROOT / "test_fixture_cache_keys.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    parser.add_argument("--field")
    args = parser.parse_args()
    if not args.seg:
        sys.stderr.write("fake cache_key.py: test stub requires --seg\\n")
        return 1
    data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
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
            "W3/W3a (re-run bootstrap_names.py, the glossary pass, then segpack.py) "
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
            "derivation_bundle_hash, then segpack.py) "
            "before this segment can be reclassified"
        ),
    }


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
