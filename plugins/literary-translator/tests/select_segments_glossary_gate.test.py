"""tests/select_segments_glossary_gate.test.py -- #820: the W5 admission
gate that refuses select_segments.py's dispatch authorization while a
project's W3 glossary pass has a run directory on disk AND
glossary_batch_plan.py still reports outstanding candidates for it.

See PLAN-820.md (the plan this file was written against) for the full
predicate, the `--senses-path` self-anchoring hazard it closes, and the
round 1-3 codex adjudications. In one line: refuse only when (1) at least
one `{durable_root}/glossary/runs/<RUN_ID>/` directory exists, AND (2)
`glossary_batch_plan.py --name-candidates ... --canon ...` (run against
THAT project's own data, never the planner's own self-anchored defaults)
reports `no_new_candidates: false`. `glossary.enabled: false` and "no run
directory at all" both short-circuit to admission with no flag needed --
the two cases the issue's filer required to keep working unflagged.
`--allow-unmerged-glossary` is the operator's explicit override.

## Scope

This file owns the WIRE CONTRACT for the new gate: the exact refusal
payload shape (`reason`/`glossaryRunId`/`outstandingBatches`/
`outstandingCandidates`), the two profile-driven short-circuits, the
`min_candidate_freq` pass-through, the `--plugin-root`/`--durable-root`
root-binding hazard (a planner that self-anchors its OWN data instead of
reading the target project's), the definitive-absence probe on the
`canon_senses.json` sidecar (the leak a naive "omit --senses-path when
absent" implementation reopens), `--classify-only`'s exemption, and the
driver-side forwarding of `--allow-unmerged-glossary`. It does NOT
re-prove glossary_batch_plan.py's own curation logic (freq/likely_name/
elision/dismissal semantics have their own dedicated test files) or
select_segments.py's classification taxonomy (select_segments.test.py's
job) -- every fixture below uses the simplest possible project (one
not_started segment) so `segs` is a fixed, boring `["seg01"]` on every
admitted path and the assertions can stay on the glossary gate itself.

## Fixture strategy

Every test drives the REAL, shipped `select_segments.py` (and, for the
`--plugin-root` split-root cases and the driver test, the REAL
`glossary_batch_plan.py`/`canon_senses.py`/`segment_dispatch_driver.py` too)
as a subprocess against an isolated fixture tree -- never a hand-built
stand-in for the gate or the planner. Two root shapes are used:

  * `make_full_project()` -- a single SELF-ANCHORED durable_root, matching
    `select_segments.test.py`'s own `make_durable_root` pattern, with
    `glossary_batch_plan.py`/`canon_senses.py` staged alongside
    `select_segments.py` under `{root}/scripts/` (durable_root's own
    flattened Step-0a-copy layout) via `tests/_senses_fixture.py`'s
    sanctioned `stage_consumer()`. Used for cases 1-8, 10 and the driver
    test (11), where `--plugin-root` is never passed.
  * `make_plugin_root()` / `make_data_root()` -- a PLUGIN-layout root ("A",
    `{root}/assets/scripts/...`, mirroring the real repo's own
    `skills/literary-translator/assets/scripts/` layout and what
    `resolve_dirs(--plugin-root=A)` resolves siblings against) paired with
    a bare DATA root ("B", no scripts at all) invoked as
    `select_segments.py --durable-root B --plugin-root A`. Used for cases
    9/9b, which exist specifically to prove the gate binds the planner's
    THREE data inputs (name_candidates.json/canon.json/canon_senses.json)
    to B, never to wherever the planner script itself happens to live.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
TEMPLATES_SRC_DIR = ASSETS_DIR / "templates"
SCHEMAS_SRC_DIR = ASSETS_DIR / "schemas"

SELECT_SEGMENTS_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
JSON_STDOUT_SRC = SCRIPTS_SRC_DIR / "json_stdout.py"
GLOSSARY_BATCH_PLAN_SRC = SCRIPTS_SRC_DIR / "glossary_batch_plan.py"
CANON_SENSES_SRC = SCRIPTS_SRC_DIR / "canon_senses.py"
DRAFT_READY_SRC = SCRIPTS_SRC_DIR / "draft_ready.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
DRIVER_SRC = SCRIPTS_SRC_DIR / "segment_dispatch_driver.py"
CLAIM_RECORD_SRC = SCRIPTS_SRC_DIR / "claim_record.py"
RESUME_SETUP_SRC = SCRIPTS_SRC_DIR / "resume_setup.py"
LEDGER_UPDATE_SRC = SCRIPTS_SRC_DIR / "ledger_update.py"
DRAFT_SHA1_SRC = SCRIPTS_SRC_DIR / "draft_sha1.py"
MASS_TRANSLATE_TEMPLATE_SRC = TEMPLATES_SRC_DIR / "mass-translate-wf.template.js"

for _src in (
    SELECT_SEGMENTS_SRC, LEDGER_MERGE_SRC, JSON_STDOUT_SRC, GLOSSARY_BATCH_PLAN_SRC,
    CANON_SENSES_SRC, DRAFT_READY_SRC, VALIDATE_DRAFT_SRC, DRIVER_SRC, CLAIM_RECORD_SRC,
    RESUME_SETUP_SRC, LEDGER_UPDATE_SRC, DRAFT_SHA1_SRC, MASS_TRANSLATE_TEMPLATE_SRC,
):
    assert _src.is_file(), f"expected script not found: {_src}"
assert SCHEMAS_SRC_DIR.is_dir(), f"schemas dir not found at {SCHEMAS_SRC_DIR}"

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _senses_fixture import stage_consumer  # noqa: E402


# ---------------------------------------------------------------------------
# 9c. Plugin invariant, asserted directly (no subprocess, no fixture): the
# shipped plugin carries no assets/canon_senses.json, so the ordinary
# `--plugin-root` path (every real invocation) can never trip the
# definitive-absence probe on the planner's own self-anchored default. If
# this ever regressed, cases 9/9b below would stop meaning what they claim
# to mean (a fixture-only hazard rather than one the shipped tree can hit),
# so it is pinned here rather than left to be discovered as a side effect.
# ---------------------------------------------------------------------------

def test_9c_shipped_plugin_ships_no_default_canon_senses_json():
    assert not (ASSETS_DIR / "canon_senses.json").is_file(), (
        "the shipped plugin now carries assets/canon_senses.json -- the "
        "ordinary --plugin-root path can trip the glossary gate's "
        "definitive-absence probe on a real project; cases 9/9b in this "
        "file need to be re-read against that."
    )


# ---------------------------------------------------------------------------
# Fixture harness -- FAKE_CACHE_KEY_PY is the verbatim stub
# select_segments.test.py/segment_dispatch_driver.test.py already use (never
# invoked by any fixture below -- every project here has exactly one
# not_started segment, so classify_segment() never shells out to it -- but
# resolve_dirs() always computes cache_key_script's path, so the file must
# exist).
# ---------------------------------------------------------------------------

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


def _stage_scripts(scripts_dir: Path, schemas_dir: Path) -> None:
    """The common core every fixture root needs: select_segments.py,
    ledger_merge.py, the cache_key.py stub, json_stdout.py, the full real
    schemas set, and (via stage_consumer) glossary_batch_plan.py +
    canon_senses.py + canon-senses.schema.json -- REAL files throughout,
    never a hand-built stand-in for the gate or the planner it shells out
    to."""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SCHEMAS_SRC_DIR, schemas_dir, dirs_exist_ok=True)
    shutil.copy2(SELECT_SEGMENTS_SRC, scripts_dir / "select_segments.py")
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    shutil.copy2(JSON_STDOUT_SRC, scripts_dir / "json_stdout.py")
    shutil.copy2(DRAFT_READY_SRC, scripts_dir / "draft_ready.py")
    shutil.copy2(VALIDATE_DRAFT_SRC, scripts_dir / "validate_draft.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    # stage_consumer() stages glossary_batch_plan.py + canon_senses.py +
    # json_stdout.py (idempotent re-copy, harmless) + canon-senses.schema.json
    # (already present from the full schemas copy above; also harmless) into
    # exactly `scripts_dir`/`schemas_dir`, whatever root they belong to --
    # see this module's own docstring for why that lets the SAME call stage
    # either durable_root's flattened `scripts/` or a plugin root's
    # `assets/scripts/`.
    stage_consumer(scripts_dir.parent, "glossary_batch_plan.py")


def write_profile(root: Path, glossary_yaml: str) -> Path:
    """Writes profile.yml + the ownership marker load_glossary_config() (the
    #820 gate's own profile reader, matching segment_dispatch_driver.py's
    load_engine_config()'s marker -> owner_profile_path -> yaml.safe_load
    idiom) resolves it through."""
    profile_path = root / "profile.yml"
    profile_path.write_text(glossary_yaml, encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )
    return profile_path


def glossary_profile_yaml(*, enabled=True, omit_enabled_key=False, min_candidate_freq=None) -> str:
    lines = ["glossary:"]
    if not omit_enabled_key:
        lines.append(f"  enabled: {'true' if enabled else 'false'}")
    lines.append("  research_mode: offline")
    if min_candidate_freq is not None:
        lines.append(f"  min_candidate_freq: {min_candidate_freq}")
    return "\n".join(lines) + "\n"


DEFAULT_GLOSSARY_PROFILE_YAML = glossary_profile_yaml()


def write_manifest(root: Path, seg_ids) -> None:
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": s} for s in seg_ids]}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_canon(root: Path, entries=None) -> None:
    (root / "canon.json").write_text(
        json.dumps({"entries": entries or {}}, ensure_ascii=False), encoding="utf-8"
    )


def write_name_candidates(root: Path, candidates) -> None:
    (root / "name_candidates.json").write_text(
        json.dumps({"candidates": candidates}, ensure_ascii=False), encoding="utf-8"
    )


def outstanding_candidates(names=("Fiona", "Gilbert"), freq=5):
    """Two unrelated (no elision/co-location adjacency) likely-name
    candidates at a freq comfortably above glossary_batch_plan.py's own
    DEFAULT_MIN_CANDIDATE_FREQ (2) -- chunk_batches() places both in ONE
    batch (DEFAULT_BATCH_SIZE=40), so this fixture yields exactly
    outstandingBatches=1, outstandingCandidates=2, distinguishing the two
    counts from each other in every assertion below."""
    return [{"name": n, "freq": freq, "likely_name": True} for n in names]


def make_glossary_run(root: Path, run_id="R") -> Path:
    run_dir = root / "glossary" / "runs" / run_id
    run_dir.mkdir(parents=True)
    return run_dir


def make_full_project(tmp_path, name="durable_root", glossary_yaml=DEFAULT_GLOSSARY_PROFILE_YAML):
    """A self-anchored durable_root: select_segments.py + ledger_merge.py +
    glossary_batch_plan.py + canon_senses.py under {root}/scripts/, the
    real schemas, profile.yml + ownership marker carrying `glossary_yaml`,
    one not_started segment ("seg01"), an empty runs/ledger.d/ and
    segments/, and an empty canon.json. Callers add name_candidates.json /
    a glossary run dir / a canon_senses.json sidecar as each case needs."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    _stage_scripts(scripts_dir, schemas_dir)
    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    write_manifest(root, ["seg01"])
    write_canon(root)
    write_profile(root, glossary_yaml)
    return root


def make_plugin_root(tmp_path, name="plugin_root"):
    """A PLUGIN-layout root ("A"): {root}/assets/scripts/ +
    {root}/assets/schemas/, mirroring resolve_dirs(--plugin-root=A)'s own
    `{plugin_root}/assets/scripts/<name>.py` resolution -- the SAME layout
    the real repo ships. select_segments.py is staged here too (not inside
    the tested durable_root "B"), so it is invoked exactly the way
    production does: `A/assets/scripts/select_segments.py --durable-root B
    --plugin-root A`."""
    root = tmp_path / name
    scripts_dir = root / "assets" / "scripts"
    schemas_dir = root / "assets" / "schemas"
    _stage_scripts(scripts_dir, schemas_dir)
    return root


def make_data_root(tmp_path, name="durable_root_b", glossary_yaml=DEFAULT_GLOSSARY_PROFILE_YAML):
    """A bare DATA root ("B"): NO scripts/ dir -- every sibling script
    select_segments.py needs is resolved from the paired plugin root via
    --plugin-root, proving nothing falls back to a self-anchored copy that
    was never staged here. It DOES need its own schemas/ copy, matching
    Step 0a's real layout (schemas are copied to {durable_root}/schemas/
    independently of where the scripts themselves live): ledger_merge.py,
    invoked with --durable-root B --plugin-root A, still resolves its own
    schema files under B, not A."""
    root = tmp_path / name
    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    shutil.copytree(SCHEMAS_SRC_DIR, root / "schemas")
    write_manifest(root, ["seg01"])
    write_canon(root)
    write_profile(root, glossary_yaml)
    return root


def run_select(script_path: Path, *args, cwd: Path, timeout=30):
    return subprocess.run(
        [sys.executable, str(script_path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd),
    )


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def assert_admitted_with_segs(payload):
    assert payload["success"] is True, payload
    assert payload["segs"] == ["seg01"], payload


def assert_glossary_refusal(payload, reason):
    assert payload["success"] is False, payload
    assert payload["reason"] == reason, payload
    assert "segs" not in payload, payload


# ---------------------------------------------------------------------------
# 1. Run dir exists + outstanding candidates -> refuse, naming the run id
#    and both counts.
# ---------------------------------------------------------------------------

def test_1_refuses_when_run_dir_exists_and_candidates_are_outstanding(tmp_path):
    root = make_full_project(tmp_path)
    write_name_candidates(root, outstanding_candidates())
    make_glossary_run(root, "R")

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-pass-unmerged")
    assert payload["glossaryRunId"] == "R", payload
    assert payload["outstandingBatches"] == 1, payload
    assert payload["outstandingCandidates"] == 2, payload
    # No substring check on payload["error"] here: the run id ("R") and the
    # counts are single characters/digits, far too generic to distinguish
    # this refusal's message from an unrelated one -- the structural
    # asserts above (reason/glossaryRunId/outstandingBatches/
    # outstandingCandidates) already pin exactly what the message claims to
    # name, and pin it exactly rather than by substring.


# ---------------------------------------------------------------------------
# 2. Run dir exists + planner reports no_new_candidates:true -> admitted.
# ---------------------------------------------------------------------------

def test_2_admits_when_planner_reports_no_new_candidates(tmp_path):
    root = make_full_project(tmp_path)
    write_name_candidates(root, [])  # -> {"no_new_candidates": true, "batches": []}
    run_dir = make_glossary_run(root, "R")
    # A real run dir always carries a frozen manifest_all.json (resume_setup.py's
    # write_glossary_manifests() writes it the moment a run starts) -- an
    # empty frozen plan here, matching "this run queued zero candidates".
    # Needed since the #820 follow-up (frozen-plan gate, see the block below
    # test 11) fails CLOSED on a run dir whose frozen plan cannot be read.
    write_glossary_manifest_all(run_dir, [])

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_admitted_with_segs(payload)


# ---------------------------------------------------------------------------
# 3. NO glossary/runs dir at all + (irrelevant) outstanding candidates ->
#    admitted with no flag -- the issue filer's own must-pass case.
# ---------------------------------------------------------------------------

def test_3_admits_with_no_run_dir_at_all(tmp_path):
    root = make_full_project(tmp_path)
    write_name_candidates(root, outstanding_candidates())
    # Deliberately no make_glossary_run() call: no glossary/ directory at
    # all, not even an empty glossary/runs/.

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_admitted_with_segs(payload)


# ---------------------------------------------------------------------------
# 4. glossary.enabled:false + run dir + outstanding candidates -> admitted
#    with no flag -- the issue filer's second must-pass case.
# ---------------------------------------------------------------------------

def test_4_admits_when_glossary_disabled(tmp_path):
    root = make_full_project(tmp_path, glossary_yaml=glossary_profile_yaml(enabled=False))
    write_name_candidates(root, outstanding_candidates())
    make_glossary_run(root, "R")

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_admitted_with_segs(payload)


# ---------------------------------------------------------------------------
# 5. --allow-unmerged-glossary over case 1's exact fixture -> admitted.
# ---------------------------------------------------------------------------

def test_5_allow_unmerged_glossary_admits_over_an_outstanding_run(tmp_path):
    root = make_full_project(tmp_path)
    write_name_candidates(root, outstanding_candidates())
    make_glossary_run(root, "R")

    proc = run_select(
        root / "scripts" / "select_segments.py", "--allow-unmerged-glossary", cwd=root
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_admitted_with_segs(payload)


# ---------------------------------------------------------------------------
# 6. glossary.min_candidate_freq reaches the planner: a freq-2 candidate is
#    OUTSTANDING under the planner's own default (2) but EXCLUDED under a
#    profile override of 3 -- proving the profile value, not the child's
#    own default, is what actually governs admission.
# ---------------------------------------------------------------------------

def test_6_min_candidate_freq_from_profile_reaches_the_child(tmp_path):
    root = make_full_project(
        tmp_path, glossary_yaml=glossary_profile_yaml(min_candidate_freq=3)
    )
    write_name_candidates(root, [{"name": "Bob", "freq": 2, "likely_name": True}])
    run_dir = make_glossary_run(root, "R")
    # This run's OWN frozen plan never queued "Bob" (unlike test 12/14,
    # which deliberately freeze "Bob" under an earlier, lower threshold) --
    # required so the #820 follow-up's frozen-plan condition (see the block
    # below test 11) doesn't itself refuse here, keeping this test's own
    # point (the live threshold reaches the child) isolated from that one.
    write_glossary_manifest_all(run_dir, [])

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_admitted_with_segs(payload)


# ---------------------------------------------------------------------------
# 7. glossary.enabled OMITTED (only research_mode present) -> treated as
#    enabled, i.e. case 1's refusal. NOT the same as an absent `glossary`
#    block entirely (a schema error, out of scope).
# ---------------------------------------------------------------------------

def test_7_absent_enabled_key_is_treated_as_enabled(tmp_path):
    root = make_full_project(
        tmp_path, glossary_yaml=glossary_profile_yaml(omit_enabled_key=True)
    )
    write_name_candidates(root, outstanding_candidates())
    make_glossary_run(root, "R")

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-pass-unmerged")


# ---------------------------------------------------------------------------
# 8. glossary-enabled project, run dir present, but NO name_candidates.json
#    -> the planner is unusable; its own "run bootstrap_names.py" sentence
#    is relayed.
# ---------------------------------------------------------------------------

def test_8_missing_name_candidates_reports_check_unavailable(tmp_path):
    root = make_full_project(tmp_path)
    make_glossary_run(root, "R")
    # Deliberately no write_name_candidates() call.

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False, payload
    assert payload["reason"] == "glossary-check-unavailable", payload
    assert "segs" not in payload, payload
    assert "detail" in payload, payload
    assert "name_candidates.json" in payload["detail"], payload
    assert "run bootstrap_names.py" in payload["detail"], payload


# ---------------------------------------------------------------------------
# 9. SEPARATE ROOTS: the planner's three data inputs must be bound to the
#    DATA root (B), never to wherever the planner script itself happens to
#    live (A, the plugin root). A is seeded with CLEAN decoy data at the
#    planner's own real self-anchor (glossary_batch_plan.py staged at
#    A/assets/scripts/glossary_batch_plan.py self-anchors to
#    A/assets/{name_candidates,canon}.json); B carries the outstanding
#    project. An implementation that lets the planner read its own
#    self-anchored defaults instead of B's explicit paths admits here
#    (reads A's clean data); the correct one refuses over B.
# ---------------------------------------------------------------------------

def test_9_binds_planner_inputs_to_the_durable_root_not_the_plugin_root(tmp_path):
    plugin_root = make_plugin_root(tmp_path, name="A")
    # A's own self-anchored data (glossary_batch_plan.py's DEFAULT_NAME_CANDIDATES/
    # DEFAULT_CANON resolve to A/assets/{name_candidates,canon}.json): clean,
    # zero outstanding candidates.
    write_name_candidates(plugin_root / "assets", [])
    write_canon(plugin_root / "assets")

    data_root = make_data_root(tmp_path, name="B")
    write_name_candidates(data_root, outstanding_candidates())
    make_glossary_run(data_root, "R")

    proc = run_select(
        plugin_root / "assets" / "scripts" / "select_segments.py",
        "--durable-root", str(data_root),
        "--plugin-root", str(plugin_root),
        cwd=plugin_root,
    )
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-pass-unmerged")
    assert payload["glossaryRunId"] == "R", payload
    assert payload["outstandingCandidates"] == 2, payload


def _probe_naive_self_anchoring_is_the_real_hazard(tmp_path):
    """Not a pytest test -- a standalone confirmation, run once here and
    reported to the lead, that test 9's fixture genuinely discriminates: if
    the gate invoked glossary_batch_plan.py with NO explicit
    --name-candidates/--canon (i.e. let it self-anchor to wherever the
    script physically sits, which is A/assets when staged at
    A/assets/scripts/glossary_batch_plan.py per resolve_dirs()'s own
    --plugin-root layout), it would read A's CLEAN decoy data and report
    no_new_candidates:true -- the wrong answer for a check that is supposed
    to be about B. Confirmed by invoking the real, staged planner directly
    both ways against test 9's own fixture tree."""
    plugin_root = make_plugin_root(tmp_path, name="A")
    write_name_candidates(plugin_root / "assets", [])
    write_canon(plugin_root / "assets")
    data_root = make_data_root(tmp_path, name="B")
    write_name_candidates(data_root, outstanding_candidates())

    planner = plugin_root / "assets" / "scripts" / "glossary_batch_plan.py"

    # Self-anchored (no explicit --name-candidates/--canon): reads A's own
    # DEFAULT_NAME_CANDIDATES/DEFAULT_CANON (A/assets/*), which are clean.
    naive = subprocess.run(
        [sys.executable, str(planner)], capture_output=True, text=True, timeout=30,
        cwd=str(plugin_root),
    )
    naive_payload = json.loads(naive.stdout.strip().splitlines()[-1])
    assert naive_payload["no_new_candidates"] is True, naive_payload

    # Explicit, bound to B: reads the actually-outstanding data.
    bound = subprocess.run(
        [
            sys.executable, str(planner),
            "--name-candidates", str(data_root / "name_candidates.json"),
            "--canon", str(data_root / "canon.json"),
        ],
        capture_output=True, text=True, timeout=30, cwd=str(plugin_root),
    )
    bound_payload = json.loads(bound.stdout.strip().splitlines()[-1])
    assert bound_payload["no_new_candidates"] is False, bound_payload


# ---------------------------------------------------------------------------
# 9b. SIDECAR LEAK: B has no canon_senses.json; A (the plugin root, where
#     the planner script physically lives) carries one marking B's only
#     outstanding candidate as an adjudicated split. Omitting --senses-path
#     unconditionally when B's copy is absent lets the planner fall back to
#     ITS OWN self-anchored default (A/assets/canon_senses.json), silently
#     excluding the candidate and wrongly admitting. The definitive-absence
#     probe on A's default path closes this -- refuses, naming both paths.
# ---------------------------------------------------------------------------

def _valid_evidence(**overrides):
    evidence = {
        "block": "PARA:seg01:0001",
        "seg": "seg01",
        "char_start": 10,
        "char_end": 16,
        "context_start": 0,
        "context_end": 40,
        "sha256": "a" * 64,
    }
    evidence.update(overrides)
    return evidence


def _valid_sense(sense_id, disambiguator="a sense", index_scope="narrative"):
    return {
        "sense_id": sense_id,
        "disambiguator": disambiguator,
        "index_scope": index_scope,
        "evidence": _valid_evidence(),
    }


def write_split_senses(path: Path, source_form: str) -> None:
    doc = {
        "schema_version": 1,
        "entries_by_source_form": {
            source_form: {"senses": [_valid_sense("s1"), _valid_sense("s2")]}
        },
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_9b_probes_the_plugin_roots_default_senses_sidecar_for_a_leak(tmp_path):
    plugin_root = make_plugin_root(tmp_path, name="A")
    write_name_candidates(plugin_root / "assets", [])
    write_canon(plugin_root / "assets")
    # A's own self-anchored default sidecar (glossary_batch_plan.py's
    # DEFAULT_SENSES_PATH = <script>.resolve().parents[1] / "canon_senses.json"
    # = A/assets/canon_senses.json), marking "Fiona" -- B's only outstanding
    # candidate -- as an adjudicated split.
    write_split_senses(plugin_root / "assets" / "canon_senses.json", "Fiona")

    data_root = make_data_root(tmp_path, name="B")
    write_name_candidates(data_root, [{"name": "Fiona", "freq": 5, "likely_name": True}])
    make_glossary_run(data_root, "R")
    # Deliberately no canon_senses.json at all in B.

    proc = run_select(
        plugin_root / "assets" / "scripts" / "select_segments.py",
        "--durable-root", str(data_root),
        "--plugin-root", str(plugin_root),
        cwd=plugin_root,
    )
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False, payload
    assert payload["reason"] == "glossary-senses-indeterminate", payload
    assert "segs" not in payload, payload
    blob = json.dumps(payload)
    assert str(data_root / "canon_senses.json") in blob, payload
    assert str(plugin_root / "assets" / "canon_senses.json") in blob, payload


def _probe_naive_omit_when_absent_is_the_real_leak(tmp_path):
    """Not a pytest test -- confirmation that 9b's fixture genuinely
    discriminates: with B's canon_senses.json absent and --senses-path
    simply OMITTED (the naive "target absent -> don't pass it" reading),
    the real planner falls back to ITS OWN self-anchored default -- A's
    sidecar, staged where the script itself lives -- and reads the
    adjudicated split, wrongly excluding "Fiona" and reporting
    no_new_candidates:true."""
    plugin_root = make_plugin_root(tmp_path, name="A")
    write_split_senses(plugin_root / "assets" / "canon_senses.json", "Fiona")
    data_root = make_data_root(tmp_path, name="B")
    write_name_candidates(data_root, [{"name": "Fiona", "freq": 5, "likely_name": True}])

    planner = plugin_root / "assets" / "scripts" / "glossary_batch_plan.py"
    naive = subprocess.run(
        [
            sys.executable, str(planner),
            "--name-candidates", str(data_root / "name_candidates.json"),
            "--canon", str(data_root / "canon.json"),
            # no --senses-path at all
        ],
        capture_output=True, text=True, timeout=30, cwd=str(plugin_root),
    )
    payload = json.loads(naive.stdout.strip().splitlines()[-1])
    assert payload["no_new_candidates"] is True, payload  # the leak: wrongly excluded


# ---------------------------------------------------------------------------
# 9d-i / 9d-ii. UNREADABLE DURABLE SIDECAR, split into two tests because the
# two real triggers land on DIFFERENT reasons -- corrected after the lead's
# own premise check found the original single-test brief wrong (a dangling
# symlink does NOT make os.lstat() raise; lstat inspects the link's own
# dirent and never follows the final component, so it SUCCEEDS regardless
# of what the link points to):
#
#   9d-i  (real filesystem, a dangling symlink): os.lstat() on the durable
#         sidecar SUCCEEDS -- genuinely "present" at resolve_glossary_
#         senses_arg()'s own definitive-stat layer (select_segments.py:
#         4411-4502, branch 2: "Present (of any kind lstat can see) -> pass
#         --senses-path explicitly"). So the gate correctly forwards
#         `--senses-path` to glossary_batch_plan.py, and it is THAT
#         script's own stricter `load_senses()`/`_path_state()` (which
#         classifies a dangling symlink as "irregular", a BLOCK regardless
#         of allow_absent) that refuses one layer down -- surfacing here as
#         "glossary-check-unavailable", never "glossary-senses-
#         indeterminate". What this test pins is the property that
#         actually matters: the dangling symlink must NEVER be silently
#         read as absent and fall through to a false admission -- it must
#         refuse loudly, naming the path.
#   9d-ii (mocked os.lstat): the reason 9d-i does NOT produce --
#         "glossary-senses-indeterminate" -- needs a GENUINE non-ENOENT
#         OSError out of the gate's own `os.lstat({durable_root}/
#         canon_senses.json)` call (EACCES is the natural one). This is
#         DELIBERATELY not a real-filesystem test: lstat never follows the
#         final path component (so a dangling/looping symlink there can't
#         produce it), and resolve_dirs() already canonicalizes the durable
#         root's own ancestry before this call, so there is no portable,
#         non-root filesystem state that reaches this branch. Mocking
#         os.lstat in the CHILD PROCESS (via a small wrapper that patches
#         os.lstat before runpy-executing the real select_segments.py, so
#         the gate's own subprocess-based contract stays exercised end to
#         end rather than calling an internal function in-process) is the
#         honest way in here, not a shortcut -- a future reader should not
#         "fix" this into a real-filesystem test, because no such fixture
#         can reach this branch.
# ---------------------------------------------------------------------------

def test_9d_i_dangling_symlink_is_present_and_refuses_via_check_unavailable(tmp_path):
    root = make_full_project(tmp_path)
    write_name_candidates(root, outstanding_candidates())
    make_glossary_run(root, "R")
    sidecar_path = root / "canon_senses.json"
    sidecar_path.symlink_to(root / "nonexistent_target_for_dangling_symlink.json")

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False, payload
    assert payload["reason"] == "glossary-check-unavailable", payload
    assert "segs" not in payload, payload
    assert str(sidecar_path) in payload["error"], payload
    assert "not a regular file" in payload["error"], payload


MOCK_LSTAT_EACCES_WRAPPER_PY = """#!/usr/bin/env python3
# Patches os.lstat to raise a genuine non-ENOENT OSError (EACCES) for one
# exact path (given via MOCK_LSTAT_EACCES_PATH), then runpy-executes the
# REAL select_segments.py as __main__ in this SAME process -- so its own
# `os.lstat(...)` calls (a plain dotted `os.lstat`, not a `from os import
# lstat`) hit the patched function, and the gate is still driven as a real
# subprocess end to end, never called in-process.
import os
import runpy
import sys

_target = os.environ["MOCK_LSTAT_EACCES_PATH"]
_real_lstat = os.lstat


def _patched_lstat(path, *args, **kwargs):
    if os.fspath(path) == _target:
        raise PermissionError(13, "Permission denied", _target)
    return _real_lstat(path, *args, **kwargs)


os.lstat = _patched_lstat

script = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(script, run_name="__main__")
"""


def test_9d_ii_genuine_lstat_error_refuses_as_senses_indeterminate(tmp_path):
    root = make_full_project(tmp_path)
    write_name_candidates(root, outstanding_candidates())
    make_glossary_run(root, "R")
    sidecar_path = root / "canon_senses.json"
    # No file at all here -- the mock is what makes os.lstat(sidecar_path)
    # raise; a real file would also work, but omitting it proves the
    # refusal comes from the (mocked) lstat error, not from anything about
    # the file's actual content.

    wrapper_path = tmp_path / "mock_lstat_eacces_wrapper.py"
    wrapper_path.write_text(MOCK_LSTAT_EACCES_WRAPPER_PY, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(wrapper_path), str(root / "scripts" / "select_segments.py")],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(root),
        env={**os.environ, "MOCK_LSTAT_EACCES_PATH": str(sidecar_path)},
    )
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False, payload
    assert payload["reason"] == "glossary-senses-indeterminate", payload
    assert "segs" not in payload, payload
    assert str(sidecar_path) in payload["error"], payload


def _probe_naive_path_exists_reads_dangling_symlink_as_absent(tmp_path):
    """Not a pytest test -- confirms Path.exists()/Path.is_file() are
    exactly the wrong primitive here: both silently swallow the dangling
    symlink and report it as though nothing were there."""
    root = tmp_path / "probe_root"
    root.mkdir()
    sidecar_path = root / "canon_senses.json"
    sidecar_path.symlink_to(root / "nonexistent_target.json")
    assert sidecar_path.exists() is False, "Path.exists() should read the dangling symlink as absent"
    assert sidecar_path.is_file() is False, "Path.is_file() should read the dangling symlink as absent"


# ---------------------------------------------------------------------------
# 10. --classify-only over case 1's exact fixture -> no refusal; the
#     classification report is produced as usual (authorizes_dispatch
#     false, but `segs` still populated).
# ---------------------------------------------------------------------------

def test_10_classify_only_is_never_refused_by_the_glossary_gate(tmp_path):
    root = make_full_project(tmp_path)
    write_name_candidates(root, outstanding_candidates())
    make_glossary_run(root, "R")

    proc = run_select(root / "scripts" / "select_segments.py", "--classify-only", cwd=root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True, payload
    assert payload["authorizes_dispatch"] is False, payload
    assert payload["segs"] == ["seg01"], payload


# ---------------------------------------------------------------------------
# 11. DRIVER INTEGRATION: the cross-file seam. Drives the REAL
#     segment_dispatch_driver.py end to end (Phase 2 machinery staged, same
#     recipe as segment_dispatch_driver.test.py's own phase2_project()) --
#     (a) without the flag it refuses because select_segments.py's own gate
#     did, relaying that refusal; (b) with --allow-unmerged-glossary it gets
#     PAST the gate and dispatches the segment for real (proven by the fake
#     codex_job.py's own argv log going from empty to non-empty, not merely
#     by a different exit code).
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
    "glossary:\n"
    "  enabled: true\n"
    "  research_mode: offline\n"
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

FAKE_DRAFT_READY_PY = """#!/usr/bin/env python3
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

FAKE_VALIDATE_DRAFT_PY = """#!/usr/bin/env python3
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
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

FAKE_CODEX_JOB_PHASE2_PY = """#!/usr/bin/env python3
import argparse
import importlib.util
import json
import sys
from pathlib import Path


def _load_real_draft_sha1():
    path = Path(__file__).resolve().parent / "draft_sha1.py"
    spec = importlib.util.spec_from_file_location("draft_sha1_fixture", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--kind", required=True)
    p.add_argument("--run-id", default=None)
    p.add_argument("--companion", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--seg", required=True)
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--expect-token", required=True)
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
    else:
        draft_path = segments_dir / (args.seg + ".draft.json")
        sha1_mod = _load_real_draft_sha1()
        review = {
            "clean": True, "coverage_ok": True, "findings": [],
            "draft_sha1": sha1_mod.draft_content_sha1(draft_path),
            "dispatch_token": args.expect_token,
        }
        (segments_dir / (args.seg + ".review.json")).write_text(json.dumps(review), encoding="utf-8")

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


def stage_phase2_sibling_scripts(scripts_dir: Path, templates_dir: Path) -> None:
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(RESUME_SETUP_SRC, scripts_dir / "resume_setup.py")
    shutil.copy2(LEDGER_UPDATE_SRC, scripts_dir / "ledger_update.py")
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    shutil.copy2(CLAIM_RECORD_SRC, scripts_dir / "claim_record.py")
    shutil.copy2(JSON_STDOUT_SRC, scripts_dir / "json_stdout.py")
    (scripts_dir / "resolve_codex_companion.py").write_text(FAKE_RESOLVE_CODEX_COMPANION_PY, encoding="utf-8")
    # These two overwrite the REAL draft_ready.py/validate_draft.py that
    # _stage_scripts() already staged: Phase 2 needs the FAKE, controllable
    # versions (the real ones need a fully-staged segpack/particle-config
    # pipeline this file's fixtures deliberately don't build).
    (scripts_dir / "draft_ready.py").write_text(FAKE_DRAFT_READY_PY, encoding="utf-8")
    (scripts_dir / "validate_draft.py").write_text(FAKE_VALIDATE_DRAFT_PY, encoding="utf-8")
    (scripts_dir / "codex_job.py").write_text(FAKE_CODEX_JOB_PHASE2_PY, encoding="utf-8")

    templates_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MASS_TRANSLATE_TEMPLATE_SRC, templates_dir / "mass-translate-wf.template.js")


def make_driver_project(tmp_path, name="driver_root"):
    root = make_full_project(tmp_path, name=name, glossary_yaml=DRIVER_PROFILE_YAML)
    shutil.copy2(DRIVER_SRC, root / "scripts" / "segment_dispatch_driver.py")
    stage_phase2_sibling_scripts(root / "scripts", root / "templates")
    (root / "runs" / ".plugin_bundle_hash").write_text("fixture-plugin-bundle-hash\n", encoding="utf-8")
    (root / "runs" / ".orchestration_bundle_hash").write_text("fixture-orchestration-bundle-hash\n", encoding="utf-8")
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps({"seg01": {f: f"{f}-seed" for f in (
            "input_sha1", "style_contract_hash", "used_terms_hash", "pipeline_version",
            "schema_hash", "prompt_hash", "agent_config_hash", "profile_semantics_hash",
            "particle_config_hash", "source_extraction_hash", "source_input_hash",
            "derivation_bundle_hash", "verse_map_hash", "note_map_hash", "plugin_bundle_hash",
        )}}),
        encoding="utf-8",
    )
    (root / "segments" / "segpack_seg01.json").write_text(
        json.dumps({"seg": "seg01", "blocks": [], "footnotes": [], "verses": []}), encoding="utf-8"
    )
    return root


def run_driver(root: Path, *args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "segment_dispatch_driver.py"), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )


def read_argv_log(root: Path):
    log_path = root / "test_fixture_argv_log.jsonl"
    if not log_path.is_file():
        return []
    return [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_11_driver_forwards_the_flag_and_gets_past_the_selector_gate(tmp_path):
    root = make_driver_project(tmp_path)
    write_name_candidates(root, outstanding_candidates())
    make_glossary_run(root, "R")

    # (a) Without the flag: the driver refuses because select_segments.py's
    # own gate did -- relaying that refusal, dispatching nothing.
    proc_without = run_driver(root, timeout=60)
    assert proc_without.returncode == 1, f"stdout={proc_without.stdout!r} stderr={proc_without.stderr!r}"
    payload_without = parse_stdout(proc_without)
    assert payload_without["success"] is False, payload_without
    # segment_dispatch_driver.py's own Step-1-gate fatal() (run(), the
    # `if not select_result.get("success")` branch) forwards only
    # `classification`/`counts`/`eligible_not_dispatched` as structured
    # extras -- it does NOT re-surface select_segments.py's `reason`/
    # `glossaryRunId`/`outstandingBatches`/`outstandingCandidates` as
    # top-level driver payload keys, only inline inside the "error" string
    # ("Step 1 gate refused: " + the selector's own message). So the
    # structural assert the lead's own PINNED CONTRACT would suggest
    # (`payload_without["reason"] == ...`) is not available at this layer;
    # asserting on the message's own DISTINCTIVE phrasing is the strongest
    # check this layer supports.
    assert "outstanding glossary batch(es)" in payload_without["error"], payload_without
    assert "(newest: R)" in payload_without["error"], payload_without
    assert read_argv_log(root) == [], "nothing may be dispatched while the gate refuses"

    # (b) With --allow-unmerged-glossary: gets past the gate and dispatches
    # seg01 for real, all the way to convergence -- proven by the fake
    # codex_job.py's argv log going from empty to non-empty.
    proc_with = run_driver(root, "--allow-unmerged-glossary", timeout=90)
    assert proc_with.returncode == 0, f"stdout={proc_with.stdout!r} stderr={proc_with.stderr!r}"
    payload_with = parse_stdout(proc_with)
    assert payload_with["success"] is True, payload_with
    argv_log = read_argv_log(root)
    assert argv_log, "expected the driver to actually dispatch seg01 once past the glossary gate"
    assert any(entry["seg"] == "seg01" for entry in argv_log), argv_log


# ---------------------------------------------------------------------------
# #820 follow-up (frozen-plan gate): a PR review bot found the two-condition
# predicate above fail-open. CONDITION 2 (`check_glossary_current()`) asks
# glossary_batch_plan.py "is there outstanding work" using the LIVE
# profile.yml threshold and the CURRENT name_candidates.json. A W3 pass
# freezes its own batch plan into `{run_dir}/manifest_all.json`
# (resume_setup.py's `write_glossary_manifests()`) the moment it starts. So a
# run planned at min_candidate_freq:2 holds a frequency-2 name; raise the
# live profile to 3 before W5, and the planner drops that name from its
# LIVE-threshold report, reports no_new_candidates:true, and the gate wrongly
# ADMITS -- while the in-flight W3 pass can still merge that name
# afterwards. Regenerating name_candidates.json mid-pass is the same class
# of hole.
#
# The fix adds a THIRD condition, evaluated only once a run directory is
# known to exist (i.e. only when CONDITION 1 has already found at least one
# run dir): union the names from every run's manifest_all.json, call the
# planner a SECOND time with --min-candidate-freq 1 (so no frozen name can
# vanish because the live threshold moved), intersect the planner's
# batches[].names with that frozen set, and refuse when the intersection is
# non-empty. manifest_all.json is itself built from the planner's own
# batches[].names (write_glossary_manifests()), so the two name sets are
# identical in provenance and form -- this is not a second, independently
# invented predicate, it is the same one re-run against a threshold that
# cannot silently exclude a name the frozen plan already committed to.
#
# New reason "glossary-run-plan-outstanding" (outstandingCandidates = the
# size of the intersection, glossaryRunId = the newest run id,
# outstandingBatches = None -- the frozen set is a union across runs, so no
# single batch number can honestly be attributed to it). A frozen plan that
# cannot be read at all (manifest_all.json missing/unreadable/not a JSON
# array of strings, in a run dir that exists) fails CLOSED with reason
# "glossary-run-plan-unreadable", naming the offending path -- never
# silently treated as "no frozen names". The floor-1 planner subprocess
# itself failing/timing out/emitting unparseable output reuses the EXISTING
# "glossary-check-unavailable" reason (same shape CONDITION 2 already uses
# for its own subprocess failures).
# ---------------------------------------------------------------------------


def write_glossary_manifest_all(run_dir: Path, names) -> None:
    """Writes {run_dir}/manifest_all.json in exactly the shape
    resume_setup.py's write_glossary_manifests() produces: a sorted,
    deduped JSON array of source-form strings (never the per-batch
    manifest_{index}.json files, which this gate's new condition never
    reads)."""
    (run_dir / "manifest_all.json").write_text(
        json.dumps(sorted(set(names)), ensure_ascii=False), encoding="utf-8"
    )


def write_glossary_run_fragments(run_dir: Path, batches: dict) -> None:
    """Writes a STRUCTURALLY COMPLETE run for CONDITION 3a/3b's two-part
    predicate (team-lead's revision after freeze-tests's own analysis
    showed 3b's intersection alone could never catch a name that vanished
    from a regenerated name_candidates.json -- see 2a/2b/2c below and the
    residual-limitation test after them). `batches` is `{index: [names,
    ...]}`. For each entry, writes manifest_{index}.json (sorted, deduped
    -- exactly resume_setup.py's write_glossary_manifests() shape) PLUS a
    matching out_{index}_attempt_0.json fragment (existence only -- 3a
    never reads its content, see check_glossary_run_plan_outstanding's own
    docstring/select_segments.py), and manifest_all.json (the union across
    every batch, deduped, sorted -- what 3b's frozen-set intersection
    reads).

    ORDERING TRAP, called out explicitly per team-lead's brief: ANY run dir
    this suite builds with an outstanding frozen name must be structurally
    COMPLETE this way, or CONDITION 3a refuses FIRST with
    "glossary-run-incomplete", masking whatever the test actually means to
    pin at 3b -- unless the test is deliberately ABOUT 3a itself (2a/2b),
    which write manifest_{index}.json directly and omit the fragment on
    purpose instead of calling this helper."""
    all_names = []
    for index, names in batches.items():
        (run_dir / f"manifest_{index}.json").write_text(
            json.dumps(sorted(set(names)), ensure_ascii=False), encoding="utf-8"
        )
        (run_dir / f"out_{index}_attempt_0.json").write_text("{}", encoding="utf-8")
        all_names.extend(names)
    (run_dir / "manifest_all.json").write_text(
        json.dumps(sorted(set(all_names)), ensure_ascii=False), encoding="utf-8"
    )


def _run_planner_directly(root: Path, *, min_candidate_freq: int) -> dict:
    """Invokes the REAL, staged glossary_batch_plan.py directly (never the
    gate) against `root`'s own name_candidates.json/canon.json, at the given
    threshold -- used only to PROVE, independently of select_segments.py,
    what the live-threshold call alone would have reported. Mirrors
    test_9's `_probe_naive_self_anchoring_is_the_real_hazard` pattern: a
    probe function, not a pytest test in its own right."""
    proc = subprocess.run(
        [
            sys.executable, "-B", str(root / "scripts" / "glossary_batch_plan.py"),
            "--name-candidates", str(root / "name_candidates.json"),
            "--canon", str(root / "canon.json"),
            "--min-candidate-freq", str(min_candidate_freq),
        ],
        capture_output=True, text=True, timeout=30, cwd=str(root),
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# 12. The bot's exact reproduction, and the most important test in this
#     block: a run froze "Bob" at freq 2; the live profile is then raised to
#     min_candidate_freq:3 with name_candidates.json UNCHANGED. The
#     pre-fix, CONDITION-2-only gate asks the planner ONLY at the live
#     threshold (3), "Bob" (freq 2) drops out, no_new_candidates:true, and
#     it admits -- proven directly below by calling the real planner at the
#     live threshold before ever invoking the gate. The fixed gate's THIRD
#     condition (floor-1 call + frozen-set intersection) must still see
#     "Bob" and refuse.
# ---------------------------------------------------------------------------

def test_12_refuses_when_a_raised_live_threshold_hides_a_frozen_low_freq_name(tmp_path):
    root = make_full_project(
        tmp_path, glossary_yaml=glossary_profile_yaml(min_candidate_freq=3)
    )
    write_name_candidates(root, [{"name": "Bob", "freq": 2, "likely_name": True}])
    run_dir = make_glossary_run(root, "R")
    # Structurally COMPLETE (every manifest_{index}.json has its matching
    # out_{index}_attempt_0.json) -- required so CONDITION 3a passes and this
    # test actually reaches 3b, the predicate it means to pin. See 2b below
    # for the companion test proving 3a fires FIRST when the run is NOT
    # complete.
    write_glossary_run_fragments(run_dir, {0: ["Bob"]})

    # Proves this test provably covers the regression: the OLD (pre-fix)
    # two-condition logic's own CONDITION 2 -- the planner called ONLY at
    # the live threshold -- would have reported no_new_candidates:true here,
    # i.e. admitted. "Bob" is invisible to that call, not merely under a
    # generic threshold.
    live_threshold_payload = _run_planner_directly(root, min_candidate_freq=3)
    assert live_threshold_payload["no_new_candidates"] is True, live_threshold_payload

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-run-plan-outstanding")
    assert payload["glossaryRunId"] == "R", payload
    assert payload["outstandingCandidates"] == 1, payload
    assert payload["outstandingBatches"] is None, payload


# ---------------------------------------------------------------------------
# 13. INTERSECTION MUST NOT OVER-REFUSE: a run's frozen names are ALL
#     already merged into canon.json, plus an UNRELATED low-frequency
#     straggler that no run ever froze and that sits below the live
#     threshold. Without intersecting against the frozen set, a bare
#     "floor-1 planner call reports any batch at all" rule would refuse
#     here FOREVER (the straggler always resurfaces at floor 1) -- this
#     test is what pins that the fix does not brick a fully-merged project.
# ---------------------------------------------------------------------------

def test_13_intersection_does_not_over_refuse_a_fully_merged_run(tmp_path):
    root = make_full_project(tmp_path)  # default min_candidate_freq (2)
    run_dir = make_glossary_run(root, "R")
    # Structurally complete -- see write_glossary_run_fragments()'s own
    # ORDERING TRAP note: without this, 3a would refuse first and this test
    # would pass for the wrong reason.
    write_glossary_run_fragments(run_dir, {0: ["Fiona", "Gilbert"]})
    write_canon(root, entries={"Fiona": {}, "Gilbert": {}})
    # Never frozen by any run, and below the LIVE threshold (2) -- excluded
    # by CONDITION 2 too -- but ABOVE floor 1, so it resurfaces in the
    # floor-1 planner call CONDITION 3 makes. It must be filtered OUT by
    # the frozen-set intersection, not treated as outstanding.
    write_name_candidates(root, [{"name": "Zelda", "freq": 1, "likely_name": True}])

    # Confirms the straggler really does resurface at floor 1 -- i.e. this
    # fixture genuinely exercises the intersection, not an empty planner
    # report that would pass for an unrelated reason.
    floor1_payload = _run_planner_directly(root, min_candidate_freq=1)
    assert floor1_payload["no_new_candidates"] is False, floor1_payload
    floor1_names = {n for b in floor1_payload["batches"] for n in b["names"]}
    assert floor1_names == {"Zelda"}, floor1_payload

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_admitted_with_segs(payload)


# ---------------------------------------------------------------------------
# 14. AN OLDER UNFINISHED RUN STILL COUNTS: two run directories, R1 (older)
#     and R2 (newer, lexicographically -- validate_run_id()'s permissive
#     alnum shape sorts "R2" after "R1"). R2 is fully merged; R1 still has
#     an outstanding frozen name that the LIVE threshold (raised, exactly
#     like test 12) hides from CONDITION 2. If the fix only unioned the
#     NEWEST run's manifest_all.json instead of every run's, this would
#     wrongly admit -- the union across ALL runs, not just the newest, is
#     the point.
# ---------------------------------------------------------------------------

def test_14_an_older_runs_frozen_name_still_counts_via_the_union(tmp_path):
    root = make_full_project(
        tmp_path, glossary_yaml=glossary_profile_yaml(min_candidate_freq=3)
    )
    # Both runs structurally complete -- see write_glossary_run_fragments()'s
    # own ORDERING TRAP note.
    r1 = make_glossary_run(root, "R1")
    write_glossary_run_fragments(r1, {0: ["Fiona"]})
    r2 = make_glossary_run(root, "R2")
    write_glossary_run_fragments(r2, {0: ["Gilbert"]})
    write_canon(root, entries={"Gilbert": {}})  # R2's own frozen name: merged
    # "Fiona" (R1's frozen name): still a candidate, at a freq the raised
    # live threshold (3) hides from CONDITION 2, exactly like test 12.
    write_name_candidates(root, [{"name": "Fiona", "freq": 2, "likely_name": True}])

    live_threshold_payload = _run_planner_directly(root, min_candidate_freq=3)
    assert live_threshold_payload["no_new_candidates"] is True, live_threshold_payload

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-run-plan-outstanding")
    assert payload["glossaryRunId"] == "R2", payload  # newest run id, per contract
    assert payload["outstandingCandidates"] == 1, payload


# ---------------------------------------------------------------------------
# 15a/15b. FAIL-CLOSED ON AN UNREADABLE FROZEN PLAN: manifest_all.json
#     absent from an existing run dir (15a), and present but holding a JSON
#     object rather than an array (15b). Both must refuse with
#     "glossary-run-plan-unreadable", naming the offending path -- never
#     silently read as "no frozen names" (which would be a false admission
#     on exactly the class of hole this whole condition exists to close).
# ---------------------------------------------------------------------------

def test_15a_refuses_when_manifest_all_is_missing(tmp_path):
    root = make_full_project(tmp_path)
    write_name_candidates(root, [])
    make_glossary_run(root, "R")
    manifest_path = root / "glossary" / "runs" / "R" / "manifest_all.json"
    # Deliberately no write_glossary_manifest_all() call -- the run dir
    # exists (CONDITION 1 sees it) but never froze a plan.
    assert not manifest_path.exists()

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-run-plan-unreadable")
    blob = json.dumps(payload)
    assert str(manifest_path) in blob, payload


def test_15b_refuses_when_manifest_all_is_not_an_array(tmp_path):
    root = make_full_project(tmp_path)
    write_name_candidates(root, [])
    make_glossary_run(root, "R")
    manifest_path = root / "glossary" / "runs" / "R" / "manifest_all.json"
    manifest_path.write_text(json.dumps({"not": "an array"}), encoding="utf-8")

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-run-plan-unreadable")
    blob = json.dumps(payload)
    assert str(manifest_path) in blob, payload


# ---------------------------------------------------------------------------
# 16. --allow-unmerged-glossary still overrides the NEW condition, exactly
#     as it already does for CONDITION 2 (test 5) -- test 12's exact
#     fixture, with the flag added.
# ---------------------------------------------------------------------------

def test_16_allow_unmerged_glossary_admits_over_the_new_condition(tmp_path):
    root = make_full_project(
        tmp_path, glossary_yaml=glossary_profile_yaml(min_candidate_freq=3)
    )
    write_name_candidates(root, [{"name": "Bob", "freq": 2, "likely_name": True}])
    run_dir = make_glossary_run(root, "R")
    write_glossary_run_fragments(run_dir, {0: ["Bob"]})  # test_12's exact fixture

    proc = run_select(
        root / "scripts" / "select_segments.py", "--allow-unmerged-glossary", cwd=root
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_admitted_with_segs(payload)


# ---------------------------------------------------------------------------
# 17. --classify-only stays a pure read and never triggers the new
#     condition, exactly as it already never triggers CONDITION 2 (test
#     10) -- test 12's exact fixture, classified instead of dispatched.
# ---------------------------------------------------------------------------

def test_17_classify_only_never_triggers_the_new_condition(tmp_path):
    root = make_full_project(
        tmp_path, glossary_yaml=glossary_profile_yaml(min_candidate_freq=3)
    )
    write_name_candidates(root, [{"name": "Bob", "freq": 2, "likely_name": True}])
    make_glossary_run(root, "R")
    write_glossary_manifest_all(root / "glossary" / "runs" / "R", ["Bob"])

    proc = run_select(root / "scripts" / "select_segments.py", "--classify-only", cwd=root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True, payload
    assert payload["authorizes_dispatch"] is False, payload
    assert payload["segs"] == ["seg01"], payload


# ---------------------------------------------------------------------------
# #820 follow-up, ROUND 2 (design revision): freeze-tests's own analysis of
# item-2 ("candidate-file drift") -- confirmed empirically against the
# then-shipped code, not just from reading glossary_batch_plan.py's
# source -- proved the original two-part CONDITION 3 design (union
# manifest_all.json across runs, intersect with a floor-1 planner call)
# could NEVER catch a frozen name that was entirely REMOVED from a
# regenerated name_candidates.json: glossary_batch_plan.py's own `rows`/
# `candidate_names` are sourced from that file alone, so a name absent from
# it can never resurface in the planner's own batches[].names, whatever the
# floor.
#
# The fix: CONDITION 3 is now TWO parts, in this order:
#
#   3a -- STRUCTURAL COMPLETION (new, primary). For every run dir, every
#   manifest_{index}.json (per-batch, written by resume_setup.py's
#   write_glossary_manifests()) must have a matching out_{index}_attempt_0.json
#   (the same fragment probe_resumed_batches() -- resume_setup.py -- itself
#   polls for; existence only, no canon_validate.py --check-batch here).
#   Any missing fragment means that batch was never adjudicated -> refuse,
#   reason "glossary-run-incomplete", outstandingBatches = the number of
#   missing fragments, outstandingCandidates = None, glossaryRunId = newest
#   run id. Critically, 3a never reads name_candidates.json at all, so it
#   closes the drift hole 3b's intersection alone could not.
#
#   3b -- the ORIGINAL union+intersect design (see the block above test 12),
#   unchanged, reason "glossary-run-plan-outstanding". Evaluated only after
#   3a passes for every run.
#
# 3a is evaluated FIRST. Every fixture elsewhere in this file that means to
# exercise 3b (tests 12-14, 16) must therefore be structurally COMPLETE
# (write_glossary_run_fragments(), not the bare write_glossary_manifest_all())
# or 3a would refuse first and the test would pass for the wrong reason --
# see write_glossary_run_fragments()'s own ORDERING TRAP docstring note.
# ---------------------------------------------------------------------------

def test_2a_incomplete_run_refuses_even_when_the_name_left_name_candidates(tmp_path):
    """The scenario freeze-tests's analysis showed the ORIGINAL 3b-only
    design could never reach: "Fiona" was frozen into ONE batch
    (manifest_0.json), that batch was never adjudicated (no
    out_0_attempt_0.json), and "Fiona" has since vanished entirely from a
    regenerated name_candidates.json. 3a never consults
    name_candidates.json, so it still catches this -- proving 3a, not 3b,
    is what closes the drift hole."""
    root = make_full_project(tmp_path)
    run_dir = make_glossary_run(root, "R")
    (run_dir / "manifest_0.json").write_text(json.dumps(["Fiona"]), encoding="utf-8")
    (run_dir / "manifest_all.json").write_text(json.dumps(["Fiona"]), encoding="utf-8")
    # Deliberately no out_0_attempt_0.json -- this batch was never adjudicated.
    write_name_candidates(root, [])  # "Fiona" is gone from the regenerated file

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-run-incomplete")
    assert payload["glossaryRunId"] == "R", payload
    assert payload["outstandingBatches"] == 1, payload
    assert payload["outstandingCandidates"] is None, payload


def test_2b_threshold_drift_on_an_unfinished_run_hits_3a_first(tmp_path):
    """Same shape as test 12 (a raised live threshold hides a frozen
    low-freq name from CONDITION 2) but structurally INCOMPLETE -- no
    out_0_attempt_0.json. Pins the evaluation ORDER: 3a fires before 3b, so
    the reason is "glossary-run-incomplete", never
    "glossary-run-plan-outstanding", even though 3b's own predicate would
    ALSO independently refuse here (test 12 proves that half)."""
    root = make_full_project(
        tmp_path, glossary_yaml=glossary_profile_yaml(min_candidate_freq=3)
    )
    write_name_candidates(root, [{"name": "Bob", "freq": 2, "likely_name": True}])
    run_dir = make_glossary_run(root, "R")
    (run_dir / "manifest_0.json").write_text(json.dumps(["Bob"]), encoding="utf-8")
    (run_dir / "manifest_all.json").write_text(json.dumps(["Bob"]), encoding="utf-8")
    # Deliberately no out_0_attempt_0.json.

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-run-incomplete")
    assert payload["outstandingBatches"] == 1, payload
    assert payload["outstandingCandidates"] is None, payload


def test_2c_a_structurally_complete_run_still_reaches_3b(tmp_path):
    """Deliberately its OWN minimal fixture (own name/threshold, not a
    literal copy of test 12's) so this "3b is reachable, not dead code once
    3a passes" pin does not silently drift along with test 12's own bot-
    reproduction numbers if those are ever adjusted for unrelated reasons.
    Mechanically it is the same trick test 12 and 2b use (a raised live
    threshold hides a frozen low-freq name from CONDITION 2) because that
    is the only mechanism this whole predicate has for making CONDITION 2
    admit while a frozen name remains genuinely outstanding -- see the
    block above test 12 for why."""
    root = make_full_project(
        tmp_path, glossary_yaml=glossary_profile_yaml(min_candidate_freq=3)
    )
    write_name_candidates(root, [{"name": "Zelda", "freq": 2, "likely_name": True}])
    run_dir = make_glossary_run(root, "R")
    write_glossary_run_fragments(run_dir, {0: ["Zelda"]})  # structurally complete -> 3a passes

    live_threshold_payload = _run_planner_directly(root, min_candidate_freq=3)
    assert live_threshold_payload["no_new_candidates"] is True, live_threshold_payload

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-run-plan-outstanding")
    assert payload["outstandingCandidates"] == 1, payload
    assert payload["outstandingBatches"] is None, payload


# ---------------------------------------------------------------------------
# 18. KNOWN LIMITATION, deliberately NOT closed (team-lead's own call, not a
#     defect this file asserts should be fixed): a run whose batches ALL
#     completed (3a passes), whose frozen names were NEVER merged into
#     canon.json, AND whose names were then removed from a regenerated
#     name_candidates.json. 3b's intersection can only ever contain names
#     the CURRENT name_candidates.json still holds (glossary_batch_plan.py's
#     own `rows`/`candidate_names` are sourced from that file alone), so a
#     name that is BOTH unmerged AND gone from that file cannot resurface at
#     3b either -- closing it needs canon-membership reasoning inside the
#     gate itself, which the module's own "makes no accuracy/identity call
#     of its own" iron rule forbids. Pinned here as documented, ACCEPTED
#     current behaviour -- not a claim that it is safe, only that it is what
#     ships, so any future change to it is a deliberate decision rather than
#     a silent regression this suite would otherwise miss.
# ---------------------------------------------------------------------------

def test_18_known_limitation_unmerged_name_gone_from_candidates_still_admits(tmp_path):
    root = make_full_project(tmp_path)
    run_dir = make_glossary_run(root, "R")
    write_glossary_run_fragments(run_dir, {0: ["Fiona"]})  # structurally complete
    write_name_candidates(root, [])  # "Fiona" removed from the regenerated file
    # "Fiona" never merged: canon.json stays empty (make_full_project's default).

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_admitted_with_segs(payload)


# ---------------------------------------------------------------------------
# 2d/2e. FAIL CLOSED, never SKIP, on the two forms of "the frozen plan
# itself could not be established" that 15a/15b (manifest_all.json missing/
# malformed) don't cover: a run dir whose own contents cannot be
# enumerated, and a manifest_{index}.json filename whose index does not
# parse. Team-lead's ruling (after the "would this need a 4th reason"
# question): reuse "glossary-run-plan-unreadable" for BOTH, do not add a
# new one -- these are the SAME fact as 15a/15b ("we could not establish
# what this run froze"), never "glossary-run-incomplete" (that reason is
# for a plan that WAS read and is determinately unfinished, not one that
# could not be read at all) and never "glossary-check-unavailable" (scoped
# to the glossary_batch_plan.py SUBPROCESS; 3a's own discovery runs no
# subprocess).
#
# PROVISIONAL: 3a has not landed as this is written (no "glossary-run-
# incomplete"/`attempt_0` in select_segments.py yet), so the exact
# discovery mechanism (does it glob broadly and then fail to parse an
# index, or scope the pattern narrowly enough that a mis-shaped filename is
# invisible to it and silently admits?) is unverified. These two tests
# ENCODE team-lead's requirement ("must refuse rather than skip") as a
# contract; if 3a's actual discovery narrows its glob so a file like
# "manifest_abc.json" is never seen at all, test_2e will stay red for a
# reason distinct from "3a hasn't landed yet" and that gap should be
# reported, not silently accepted.
# ---------------------------------------------------------------------------

# ATTRIBUTION CONFIRMED (post-3a landing): the refusal now comes from 3a's
# own `run_dir.iterdir()` call in check_glossary_run_structural_completion()
# -- verified directly against the shipped payload's "error" text ("...
# directory {run_dir} could not be enumerated ([Errno 13] Permission
# denied...") -- not from 3b's manifest_all.json read, whose own message
# reads differently ("could not read manifest_all.json at ..."). Before 3a
# landed this test passed for the WRONG reason (3b's manifest_all.json read
# also lives inside run_dir and hit the same PermissionError first); now
# that 3a runs first and 3a's own message is what's on the wire, this test
# genuinely pins 3a's enumeration failure.
def test_2d_refuses_when_a_run_dir_cannot_be_enumerated(tmp_path):
    root = make_full_project(tmp_path)
    write_name_candidates(root, [])  # -> CONDITION 2 admits, reaching CONDITION 3
    run_dir = make_glossary_run(root, "R")
    (run_dir / "manifest_0.json").write_text(json.dumps(["Fiona"]), encoding="utf-8")
    (run_dir / "out_0_attempt_0.json").write_text("{}", encoding="utf-8")
    (run_dir / "manifest_all.json").write_text(json.dumps(["Fiona"]), encoding="utf-8")
    os.chmod(run_dir, 0o000)  # blocks even the owner's own iterdir()/listdir()
    try:
        proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    finally:
        os.chmod(run_dir, 0o755)  # restore so pytest's tmp_path cleanup can proceed

    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-run-plan-unreadable")
    blob = json.dumps(payload)
    assert str(run_dir) in blob, payload
    # Pins the ATTRIBUTION, not just the reason string: 3a's own
    # `run_dir.iterdir()` failure says "could not be enumerated"; 3b's
    # manifest_all.json read failure -- which would ALSO trip over this
    # same chmod, since manifest_all.json lives inside run_dir too -- says
    # "could not read manifest_all.json" instead. Without this, 3b landing
    # first (as it briefly did) or a future reordering could make this test
    # pass again for the wrong reason, silently.
    assert "could not be enumerated" in payload["error"], payload
    assert "manifest_all.json" not in payload["error"], payload


def test_2e_refuses_when_a_manifest_filenames_index_will_not_parse(tmp_path):
    root = make_full_project(tmp_path)
    write_name_candidates(root, [])  # -> CONDITION 2 admits, reaching CONDITION 3
    run_dir = make_glossary_run(root, "R")
    # "abc" is not an integer batch index -- a malformed manifest filename,
    # never silently skipped.
    manifest_path = run_dir / "manifest_abc.json"
    manifest_path.write_text(json.dumps(["Zoe"]), encoding="utf-8")
    (run_dir / "manifest_all.json").write_text(json.dumps(["Zoe"]), encoding="utf-8")

    proc = run_select(root / "scripts" / "select_segments.py", cwd=root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert_glossary_refusal(payload, "glossary-run-plan-unreadable")
    blob = json.dumps(payload)
    assert str(manifest_path) in blob, payload


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
