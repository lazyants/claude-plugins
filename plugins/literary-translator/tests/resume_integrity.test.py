"""tests/resume_integrity.test.py -- regression-lock suite for the
resume-integrity subsystem (1.2.0 reliability build, issues #87 #88 #90
#97). See CONTRACT-1.2.0-reliability.md §6 ("Resume-integrity digest
inputs") and the approved plan (`golden-dancing-kurzweil.md`)'s "Freshness
by construction", "Resume-integrity gate", "Digest definition", and "Token
bound at EVERY commit/consume gate" sections -- the plan's prose is the
primary spec for this file; the CONTRACT §6 block is a terser restatement.

The property under test, end to end: resuming a run under
`resumeFromRunId` is only safe when EVERY input that could change a cached
agent's output is byte-identical to the prior attempt (args, resolved
profile substitutions, per-segment/glossary domain data, and every
version-gating durable byte: plugin bundle, orchestration bundle,
schemas/). Anything else must force a FRESH run with a fresh RUN_ID and NO
resume, with the prior attempt's stale artifacts naturally unreferenced
(never replayed or certified as if they belonged to the new run). Twelve
cases, matching this file's dispatch brief 1:1:

  1-6.  `resume_setup.py`'s `input_digest` gate (CONTRACT §6): a
        metadata-only candidate change, a changed mass-kind segment
        `cache_key.py` composite, a changed `.plugin_bundle_hash`, a
        changed `.orchestration_bundle_hash`, a schema-only edit under
        `schemas/`, and a `research_mode` live->offline flip (a profile
        substitution with NO byte-hash change anywhere) each force a fresh
        no-resume run.
  7.    `review_ready.py --expect-token` rejects a legacy pre-1.2.0
        4-field `review.json` (no `dispatch_token` at all) via its own
        `review.schema.json` validation, independent of any token check.
  8.    `draft_ready.py --expect-token`: backward-compatible when omitted;
        rejects a straggler old-run draft when supplied.
  9.    `draft_sha1.py` is stable across a token-only change (regression-
        catcher proves it still reacts to a real content change).
  10.   A straggler overwrite in the poll->read window is rejected by a
        read-time re-check (approximated at the unit level -- see below).
  11.   `ledger_update.py`'s per-segment convergence token precondition.
  12.   `ledger_merge.py`'s batch-final per-segment token/sha re-check.

CLI-SHAPE PROVENANCE / TIMELINE NOTE: this plugin's 1.2.0 build is a
5-owner contract-first parallel build. Owner C (scripts) was still
mid-flight when this file's FIRST draft was written -- `resume_setup.py`
and `review_ready.py` did not exist on disk yet, and this file originally
drove them against a lead-pinned coordination guess for the CLI shape
(documented in the dispatch brief as filling a genuine CONTRACT gap, not a
literal quote). Both scripts landed while this file was still being
written; this revision drives their REAL, on-disk interfaces instead
(confirmed by reading both scripts in full):

  * `resume_setup.py --payload-file PATH` -- NOT the brief's guessed
    `--kind/--args-file/--resume-from-run-id` three-flag shape. The single
    payload JSON file carries `kind`, `args`, `subst` (all 6 fields
    required), `resume_from_run_id` (optional/nullable), and kind-specific
    fields: `segs` (mass -- a list of segment ids; resume_setup.py computes
    each one's 15-field composite itself by shelling to `cache_key.py
    --seg <id>`, never trusting a caller-supplied composite) or
    `glossary_rule` + `batches` (glossary). Prints
    `{"success": true, "effectiveRunId": ..., "resume": true|false,
    "run_dir": ..., "input_digest": ...}` on success -- plus, for
    `kind="glossary"` only, a sixth `resumed_batch_indices` key (#724 A: the
    batches whose attempt-0 fragment already passes `--check-batch`, decided
    here because the Workflow's own precheck agent cost one subagent bootstrap
    per batch to answer it in prose) -- or
    `{"success": false, "error": ...}` on failure; exit 0/1 respectively,
    but the script's own docstring says to read stdout, not rely on the
    exit code alone -- a digest MISMATCH (fresh run launched) is still
    `success: true`, only a genuine setup failure is `success: false`.
    Cases 1-6 stub `cache_key.py` with a small fixture script reading a
    test-controlled `test_fixture_cache_keys.json` (same pattern
    `ledger_merge.test.py` already uses), scoping these tests to
    resume_setup.py's OWN digest-assembly/resume-decision logic rather
    than cache_key.py's own 15-field hashing algorithm (which has its own
    dedicated test file, `ledger_composite_key.test.py`).
  * `review_ready.py {seg} --expect-token TOK` (`--expect-token` is
    REQUIRED here, unlike `draft_ready.py`'s optional one) -- matches the
    brief's pin closely; confirmed against the real, now-landed script.
  * `ledger_update.py {seg} --payload-file PATH` -- the payload JSON's
    OPTIONAL `run_token` field (a bare RUN_ID string, alongside `status`)
    is the token precondition input; there is no `--expect-token`/
    `--run-token` CLI flag on this script.
  * `ledger_merge.py --expected-segs SEG[,SEG...] --run-token RUN_ID
    --skip-stale-check` -- a bare RUN_ID CLI flag (no payload file, unlike
    `ledger_update.py`).
  * Both scripts compare via a shared pair of helpers, byte-identical in
    both files: `expected_draft_token(run_token, seg) = f"{run_token}:{seg}"`
    (reconstructs the FULL expected draft-form token, not just a bare
    RUN_ID prefix -- this also catches a same-run-but-wrong-segment token,
    e.g. a corrupted/misplaced draft carrying some OTHER segment's token
    under the same run) and `review_token_matches(review_token,
    draft_token)` (a `f"{draft_token}:r"` PREFIX match, since review's
    token carries a round-label suffix the draft's own form does not).
    The draft's own `dispatch_token` must equal `expected_draft_token(...)`
    EXACTLY; review.json's must satisfy `review_token_matches(...)`
    against that same expected value.

CLI HISTORY (kept as a paper trail -- Owner C's scripts landed and were
revised repeatedly while this file was being written, converging on the
final shape only shortly before this revision, including one live
mid-edit window this file's own verification run caught as a transient
`NameError: name 'run_id_component' is not defined` -- gone by the next
run): the very first `ledger_update.py`/`ledger_merge.py` on-disk revision
this file drove compared a single `--expect-token` value via PLAIN STRING
EQUALITY against BOTH the draft's and review's `dispatch_token` -- two
shapes that can never be equal to each other by construction (confirmed
via a direct subprocess repro before any test code was written), so
convergence could never be recorded once the flag was supplied, even in
the genuinely-nothing-is-stale case. A second revision fixed the review
side with a `review_token.startswith(f"{expect_token}:r")` prefix match
while keeping `--expect-token` as a CLI flag on both scripts and comparing
the draft side by bare RUN_ID-prefix equality. The FINAL, current-on-disk
design (confirmed by reading both scripts in full just before writing this
paragraph) is the one described above -- it matches the CONTRACT's own
"INTEGRATION ADDENDA" section (payload `run_token` field for
`ledger_update.py`; bare `--run-token` CLI flag for `ledger_merge.py`) and
is also what Owner B's actual landed `mass-translate-wf.template.js`
already calls, refined further to reconstruct the full per-segment
expected token rather than comparing a bare RUN_ID prefix. Cases 11 and
12's positive controls assert this final design directly (no longer
`xfail`).

STILL-OPEN divergence, confirmed while writing case 12: the brief's pinned
expectation that a batch-final token/sha mismatch is "folded into
`missing_segments` in the FAILURE payload" does not match the landed
`ledger_merge.py`: the `merge()` function's batch-final re-verification
`raise LedgerMergeError(...)` (over the `reassert_errors` list) does not
pass a `missing_segments=` kwarg, so `main()`'s
`if e.missing_segments is not None` guard never adds the key -- the
FAILURE payload has `success: false` plus a free-text `error` string
naming the segment in prose, with no `missing_segments` array at all. The
LOAD-BEARING safety property -- `success: true` never materializes over a
stale/foreign-token segment -- IS correctly implemented; only this
packaging detail differs from the pinned expectation. See
`test_case12_stale_token_pair_surfaces_in_missing_segments` (xfail) versus
`test_case12_stale_token_pair_never_reports_success_true` (passes).

House style: every fixture copies the REAL shipped script into an
isolated `tmp_path` durable_root (so the script's own self-anchored
`Path(__file__).resolve().parents[1]` resolves to the fixture root exactly
as production does) and invokes it via a real `subprocess.run`. Nothing in
this file reimplements a script's own hashing/validation logic and asserts
against that reimplementation -- the one stub (`cache_key.py` in cases
1-6) stands in for an UNRELATED dependency with its own dedicated test
file, matching `ledger_merge.test.py`'s established convention, never the
script actually under test in that case.
"""
import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SCHEMAS_DIR = ASSETS_DIR / "schemas"
TEMPLATES_DIR = ASSETS_DIR / "templates"

RESUME_SETUP_SRC = SCRIPTS_DIR / "resume_setup.py"
REVIEW_READY_SRC = SCRIPTS_DIR / "review_ready.py"
DRAFT_READY_SRC = SCRIPTS_DIR / "draft_ready.py"
DRAFT_SHA1_SRC = SCRIPTS_DIR / "draft_sha1.py"
LEDGER_UPDATE_SRC = SCRIPTS_DIR / "ledger_update.py"
LEDGER_MERGE_SRC = SCRIPTS_DIR / "ledger_merge.py"

# All six scripts this file drives are load-bearing for the whole file's
# fixtures -- a hard collection-time assert, matching this codebase's
# established convention.
assert RESUME_SETUP_SRC.is_file(), f"resume_setup.py not found at {RESUME_SETUP_SRC}"
assert REVIEW_READY_SRC.is_file(), f"review_ready.py not found at {REVIEW_READY_SRC}"
assert DRAFT_READY_SRC.is_file(), f"draft_ready.py not found at {DRAFT_READY_SRC}"
assert DRAFT_SHA1_SRC.is_file(), f"draft_sha1.py not found at {DRAFT_SHA1_SRC}"
assert LEDGER_UPDATE_SRC.is_file(), f"ledger_update.py not found at {LEDGER_UPDATE_SRC}"
assert LEDGER_MERGE_SRC.is_file(), f"ledger_merge.py not found at {LEDGER_MERGE_SRC}"
assert (SCHEMAS_DIR / "review.schema.json").is_file()
assert (SCHEMAS_DIR / "ledger-record-base.schema.json").is_file()
assert (SCHEMAS_DIR / "ledger-fragment.schema.json").is_file()


# ---------------------------------------------------------------------------
# Shared, generic helpers
# ---------------------------------------------------------------------------

class _NotSet:
    def __repr__(self):
        return "<NOTSET>"


NOTSET = _NotSet()

# The authoritative 15-field cache-key list (references/ledger-and-
# resumability.md; mirrored verbatim in ledger_update.test.py/
# ledger_merge.test.py as plain data, never reimplemented logic).
CACHE_KEY_FIELDS = [
    "input_sha1", "style_contract_hash", "used_terms_hash", "pipeline_version",
    "schema_hash", "prompt_hash", "agent_config_hash", "profile_semantics_hash",
    "particle_config_hash", "source_extraction_hash", "source_input_hash",
    "derivation_bundle_hash", "verse_map_hash", "note_map_hash", "plugin_bundle_hash",
]

# #413 -- the `subst` payload resume_setup.py's SUBST_FIELDS requires. Kept in
# tests/_resume_subst_fixture.py because this file and resume_integrity.test.py
# carried byte-identical copies of it, comment included. That module explains
# why it is NOT tests/_workflow_instantiation.py's token map, which is a
# different contract on the other side of the same step.
from _resume_subst_fixture import BASE_SUBST  # noqa: E402


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def make_cache_key_composite(seed):
    """A full, schema-shaped 15-field cache_key dict, every field derived
    from `seed` so two different seeds are guaranteed to diverge."""
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def make_segpack(blocks=None, footnotes=None, verses=None):
    return {
        "blocks": blocks if blocks is not None else [],
        "footnotes": footnotes if footnotes is not None else [],
        "verses": verses if verses is not None else [],
    }


def make_draft(seg, dispatch_token=NOTSET, blocks=None, footnotes=None,
                verses=None, names=None, notes=None):
    draft = {
        "seg": seg,
        "blocks": blocks if blocks is not None else {},
        "footnotes": footnotes if footnotes is not None else {},
        "verses": verses if verses is not None else {},
        "names": names if names is not None else [],
        "notes": notes if notes is not None else [],
    }
    if dispatch_token is not NOTSET:
        draft["dispatch_token"] = dispatch_token
    return draft


def make_review(draft_sha1, dispatch_token=NOTSET, clean=True, coverage_ok=True, findings=None):
    review = {
        "clean": clean,
        "coverage_ok": coverage_ok,
        "findings": findings if findings is not None else [],
        "draft_sha1": draft_sha1,
    }
    if dispatch_token is not NOTSET:
        review["dispatch_token"] = dispatch_token
    return review


def compute_real_draft_sha1(root, seg, timeout=30):
    """Shells out to the REAL draft_sha1.py (must already be copied into
    {root}/scripts/) to compute the correct content sha1 for a draft
    already written to {root}/segments/{seg}.draft.json -- used only to
    build CORRECT review.json fixtures, never to reimplement the hash."""
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "draft_sha1.py"), seg],
        capture_output=True, text=True, cwd=str(root), timeout=timeout,
    )
    assert proc.returncode == 0, (
        f"draft_sha1.py failed while building a test fixture for seg "
        f"{seg!r}: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return proc.stdout.strip()


def parse_one_json_line(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line on stdout, got {len(lines)}:\n"
        f"{proc.stdout!r}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


# ===========================================================================
# Cases 1-6: resume_setup.py's input_digest gate (CONTRACT §6)
# ===========================================================================

# A stub cache_key.py -- resume_setup.py shells out to the real one via
# `cache_key.py --seg <id>` to compute each mass-kind segment's 15-field
# composite ITSELF (never trusting a caller-supplied value, closing a
# staleness/TOCTOU gap). This stub reads a test-controlled
# test_fixture_cache_keys.json mapping {seg: <15-field dict>} and prints
# the requested segment's entry verbatim -- the same pattern
# ledger_merge.test.py already uses for the same script, scoping these
# tests to resume_setup.py's OWN digest-assembly/resume-decision logic.
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    parser.add_argument("--durable-root", default=None)
    args, _ = parser.parse_known_args()
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


def make_resume_setup_root(
    tmp_path, plugin_bundle_hash="pbh-v1", orchestration_bundle_hash="obh-v1", name="durable_root",
    mass_segs=("seg01", "seg02"),
):
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(RESUME_SETUP_SRC, scripts_dir / "resume_setup.py")
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(RESUME_SETUP_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    # version.schemas is documented as a hash of the WHOLE schemas/ dir;
    # this file does not need real schema semantics, only real, mutable
    # bytes under that path for case 5 to perturb.
    schemas_dir = root / "schemas"
    schemas_dir.mkdir(parents=True)
    write_json(schemas_dir / "dummy_a.schema.json", {"type": "object", "title": "dummy A"})
    write_json(schemas_dir / "dummy_b.schema.json", {"type": "object", "title": "dummy B"})

    runs_dir = root / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / ".plugin_bundle_hash").write_text(plugin_bundle_hash, encoding="utf-8")
    (runs_dir / ".orchestration_bundle_hash").write_text(orchestration_bundle_hash, encoding="utf-8")

    # LT-409: the mass-kind digest domain now comes from manifest.json's own
    # segments[] (resume_setup.py's _load_manifest_seg_ids()), never from a
    # caller-supplied 'segs' payload field -- every mass-kind test in this
    # file shares the SAME seg01/seg02 pair mass_base_cache_keys() also
    # uses, matched here by default. `mass_segs=()` (empty) is a genuine
    # opt-out for a test that wants no manifest.json at all (e.g. the
    # orphan-copy negative control, which builds its own root by hand).
    if mass_segs:
        write_json(root / "manifest.json", {"segments": [{"seg": s} for s in mass_segs]})
    return root


def write_fixture_cache_keys(root, mapping):
    write_json(root / "test_fixture_cache_keys.json", mapping)


def run_resume_setup(root, payload_obj, timeout=30):
    payload_path = root / "scratch_resume_payload.json"
    write_json(payload_path, payload_obj)
    cmd = [sys.executable, str(root / "scripts" / "resume_setup.py"), "--payload-file", str(payload_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), timeout=timeout)
    parsed = None
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) == 1:
        try:
            parsed = json.loads(lines[0])
        except json.JSONDecodeError:
            parsed = None
    return proc, parsed


def with_resume_from(payload, run_id):
    p = copy.deepcopy(payload)
    p["resume_from_run_id"] = run_id
    return p


def assert_setup_success(proc, parsed):
    """Returns `parsed`, narrowed to non-None (pyright cannot see the
    `assert parsed is not None` below across a function-call boundary, so
    every caller reassigns its own `parsed` variable to this return value
    -- `parsedN = assert_setup_success(procN, parsedN)` -- rather than
    subscripting the pre-call, still-Optional variable)."""
    assert proc.returncode == 0, (
        f"setup should succeed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert parsed is not None, f"expected one JSON line on stdout, got: {proc.stdout!r}"
    assert parsed.get("success") is True, f"expected success:true, got: {parsed}"
    return parsed


def assert_resumes(proc, parsed, prior_run_id):
    parsed = assert_setup_success(proc, parsed)
    assert parsed.get("resume") is True, f"expected resume:true on a digest MATCH, got {parsed}"
    assert parsed.get("effectiveRunId") == prior_run_id, (
        f"a digest MATCH must reuse the exact prior run id -- got "
        f"{parsed.get('effectiveRunId')!r}, expected {prior_run_id!r}"
    )


def assert_fresh_no_resume(proc, parsed, prior_run_id):
    parsed = assert_setup_success(proc, parsed)
    assert parsed.get("resume") is False, f"expected resume:false on a digest mismatch, got {parsed}"
    assert parsed.get("effectiveRunId") != prior_run_id, (
        f"a digest MISMATCH must produce a FRESH run id, never reuse the "
        f"prior one (got {parsed.get('effectiveRunId')!r} == prior {prior_run_id!r})"
    )


def mass_base_payload():
    """LT-409: `args` is now PINNED to {} for kind="mass" -- resume_setup.py
    hard-rejects anything else (see its own module docstring's `args`
    paragraph). `segs` is kept here, unread, purely to prove the
    DEPRECATED-but-still-accepted field genuinely does nothing -- the
    digest domain comes from manifest.json instead (written by
    make_resume_setup_root(), matching this exact seg01/seg02 pair)."""
    return {
        "kind": "mass",
        "args": {},
        "subst": dict(BASE_SUBST),
        "segs": ["seg01", "seg02"],
    }


def mass_base_cache_keys():
    return {"seg01": make_cache_key_composite("s1"), "seg02": make_cache_key_composite("s2")}


def test_case1_metadata_only_candidate_change_forces_fresh_run(tmp_path):
    """PLAN: 'a metadata-only candidate change' still forces a fresh run,
    because `args` is hashed WHOLESALE (CONTRACT §6), not selectively --
    even a field nothing else (subst/domain) reads must still flip the
    digest. Uses glossary kind since 'candidate' is glossary vocabulary."""
    root = make_resume_setup_root(tmp_path)
    base_payload = {
        "kind": "glossary",
        "args": {"candidates": [{"name": "Alice Smith", "note": "benign annotation v1"}]},
        "subst": dict(BASE_SUBST),
        "glossary_rule": "strict",
        "batches": [{"index": 0, "names": ["Alice Smith"]}],
    }
    proc0, parsed0 = run_resume_setup(root, base_payload)
    parsed0 = assert_setup_success(proc0, parsed0)
    assert parsed0["resume"] is False  # first-ever run, nothing to resume
    run_id = parsed0["effectiveRunId"]

    # Sanity: identical payload resumes -- proves this fixture can
    # genuinely MATCH, so the mismatch assertion below isn't vacuously
    # true under a naive "always fresh" stand-in.
    proc1, parsed1 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_resumes(proc1, parsed1, run_id)

    perturbed = copy.deepcopy(base_payload)
    perturbed["args"]["candidates"][0]["note"] = "benign annotation v2"
    proc2, parsed2 = run_resume_setup(root, with_resume_from(perturbed, run_id))
    assert_fresh_no_resume(proc2, parsed2, run_id)


def test_case2_changed_segment_cache_key_composite_forces_fresh_run(tmp_path):
    """A changed segment cache_key.py 15-field composite (mass kind) --
    stands in for e.g. touching that segment's segpack content -- forces a
    fresh run. CONTRACT §6: domain = {seg: 15-field composite per seg},
    computed by resume_setup.py itself via cache_key.py --seg <id>."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    base_payload = mass_base_payload()

    proc0, parsed0 = run_resume_setup(root, base_payload)
    parsed0 = assert_setup_success(proc0, parsed0)
    run_id = parsed0["effectiveRunId"]

    proc1, parsed1 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_resumes(proc1, parsed1, run_id)

    perturbed_keys = mass_base_cache_keys()
    perturbed_keys["seg01"]["input_sha1"] = "input_sha1-CHANGED"
    write_fixture_cache_keys(root, perturbed_keys)
    proc2, parsed2 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_fresh_no_resume(proc2, parsed2, run_id)


def test_case3_changed_plugin_bundle_hash_forces_fresh_run(tmp_path):
    """A changed .plugin_bundle_hash marker (templates changed) forces a
    fresh run even though args/subst/domain are byte-identical."""
    root = make_resume_setup_root(tmp_path, plugin_bundle_hash="pbh-A")
    write_fixture_cache_keys(root, mass_base_cache_keys())
    base_payload = mass_base_payload()

    proc0, parsed0 = run_resume_setup(root, base_payload)
    parsed0 = assert_setup_success(proc0, parsed0)
    run_id = parsed0["effectiveRunId"]

    proc1, parsed1 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_resumes(proc1, parsed1, run_id)

    (root / "runs" / ".plugin_bundle_hash").write_text("pbh-B", encoding="utf-8")
    proc2, parsed2 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_fresh_no_resume(proc2, parsed2, run_id)


def test_case4_changed_orchestration_bundle_hash_forces_fresh_run(tmp_path):
    """A changed .orchestration_bundle_hash marker forces a fresh run.
    PLAN: orchestration_bundle_hash covers scripts plugin_bundle_hash
    EXCLUDES (draft_ready.py, ledger_merge.py, ...) -- this test does not
    recompute a real bundle hash (that's an upstream, out-of-scope step);
    it exercises resume_setup.py's OWN consumption of the stored marker
    value, proving a changed marker -- for WHATEVER underlying reason,
    including a hypothetical draft_ready.py/ledger_merge.py byte edit --
    forces a fresh run, independent of .plugin_bundle_hash staying put."""
    root = make_resume_setup_root(tmp_path, orchestration_bundle_hash="obh-A")
    write_fixture_cache_keys(root, mass_base_cache_keys())
    base_payload = mass_base_payload()

    proc0, parsed0 = run_resume_setup(root, base_payload)
    parsed0 = assert_setup_success(proc0, parsed0)
    run_id = parsed0["effectiveRunId"]

    proc1, parsed1 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_resumes(proc1, parsed1, run_id)

    (root / "runs" / ".orchestration_bundle_hash").write_text("obh-B", encoding="utf-8")
    proc2, parsed2 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_fresh_no_resume(proc2, parsed2, run_id)


def test_case5_schema_only_edit_forces_fresh_run(tmp_path):
    """PLAN [cx9#2]: a schema-only edit under schemas/ forces a fresh
    no-resume run. version.schemas is a hash of the WHOLE schemas/ dir, so
    touching one file's bytes must change it even though neither bundle
    hash marker changed."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    base_payload = mass_base_payload()

    proc0, parsed0 = run_resume_setup(root, base_payload)
    parsed0 = assert_setup_success(proc0, parsed0)
    run_id = parsed0["effectiveRunId"]

    proc1, parsed1 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_resumes(proc1, parsed1, run_id)

    schema_path = root / "schemas" / "dummy_a.schema.json"
    schema_path.write_text(schema_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    proc2, parsed2 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_fresh_no_resume(proc2, parsed2, run_id)


def test_case6_research_mode_flip_forces_fresh_run_no_byte_change(tmp_path):
    """PLAN [cx10#2]: a research_mode live->offline flip is a PROFILE
    SUBSTITUTION with NO byte-hash change anywhere on disk -- args, domain
    (cache_key fixture file), the two bundle markers, and schemas/ are all
    byte-identical to the baseline call. This is the load-bearing case
    proving resume_setup.py actually reads the resolved `subst` dict, not
    just file hashes -- an implementation that only hashed files would
    wrongly resume here."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    base_payload = mass_base_payload()
    base_payload["subst"]["research_mode"] = "live"

    proc0, parsed0 = run_resume_setup(root, base_payload)
    parsed0 = assert_setup_success(proc0, parsed0)
    run_id = parsed0["effectiveRunId"]

    proc1, parsed1 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_resumes(proc1, parsed1, run_id)

    perturbed = copy.deepcopy(base_payload)
    perturbed["subst"]["research_mode"] = "offline"
    proc2, parsed2 = run_resume_setup(root, with_resume_from(perturbed, run_id))
    assert_fresh_no_resume(proc2, parsed2, run_id)


def test_case6b_citation_content_types_change_forces_fresh_run_no_byte_change(tmp_path):
    """1.16.1 (#347), same shape as case 6 and found the same way it would have
    been missed: a `citation_content_types` change is a PROFILE SUBSTITUTION with
    no byte-hash change anywhere on disk.

    Widening ["text/"] to ["text/", "application/pdf"] makes the retrieval
    boundary admit pages it previously refused, so every cached citation verdict
    was taken under a policy that no longer applies. Codex measured two IDENTICAL
    digests across exactly this change in the 1.16.1 round-3 review -- meaning a
    resumed run would have reused those verdicts while reporting them as current,
    which is this release's own stated anti-goal (a profile setting that silently
    does not take effect).
    """
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    base_payload = mass_base_payload()
    base_payload["subst"]["citation_content_types"] = "text/"

    proc0, parsed0 = run_resume_setup(root, base_payload)
    parsed0 = assert_setup_success(proc0, parsed0)
    run_id = parsed0["effectiveRunId"]

    proc1, parsed1 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_resumes(proc1, parsed1, run_id)

    perturbed = copy.deepcopy(base_payload)
    perturbed["subst"]["citation_content_types"] = "text/,application/pdf"
    proc2, parsed2 = run_resume_setup(root, with_resume_from(perturbed, run_id))
    assert_fresh_no_resume(proc2, parsed2, run_id)


# ===========================================================================
# Case 7: review_ready.py rejects a legacy tokenless review.json
# ===========================================================================

def make_review_ready_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REVIEW_READY_SRC, scripts_dir / "review_ready.py")
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(DRAFT_SHA1_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    schemas_dir = root / "schemas"
    schemas_dir.mkdir(parents=True)
    shutil.copy2(SCHEMAS_DIR / "review.schema.json", schemas_dir / "review.schema.json")
    (root / "segments").mkdir(parents=True)
    return root


def run_review_ready(root, seg, expect_token, timeout=30):
    cmd = [sys.executable, str(root / "scripts" / "review_ready.py"), seg, "--expect-token", expect_token]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), timeout=timeout)


def test_case7_legacy_tokenless_review_json_rejected_by_review_ready(tmp_path):
    """A pre-1.2.0, 4-field review.json (clean/coverage_ok/findings/
    draft_sha1, no dispatch_token at all) is rejected by review_ready.py's
    own full review.schema.json validation -- dispatch_token is now
    required -- independent of the --expect-token check, since a legacy
    file structurally can't carry the field to compare in the first place."""
    root = make_review_ready_root(tmp_path)
    seg = "seg01"
    write_json(root / "segments" / f"{seg}.draft.json", make_draft(seg, dispatch_token="RUN_NEW:" + seg))
    real_sha1 = compute_real_draft_sha1(root, seg)

    legacy_review = {
        "clean": True,
        "coverage_ok": True,
        "findings": [],
        "draft_sha1": real_sha1,
        # dispatch_token deliberately ABSENT -- pre-1.2.0 shape.
    }
    write_json(root / "segments" / f"{seg}.review.json", legacy_review)

    proc = run_review_ready(root, seg, expect_token="RUN_NEW:" + seg + ":rfinal")
    assert proc.returncode != 0, (
        f"a legacy 4-field review.json must be rejected by "
        f"review_ready.py's own schema validation; got rc=0, "
        f"stdout={proc.stdout!r}"
    )


# ===========================================================================
# Case 8: draft_ready.py --expect-token
# ===========================================================================

def make_draft_ready_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DRAFT_READY_SRC, scripts_dir / "draft_ready.py")
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(DRAFT_READY_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    (root / "segments").mkdir(parents=True)
    return root


def run_draft_ready(root, seg, expect_token=None, timeout=30):
    cmd = [sys.executable, str(root / "scripts" / "draft_ready.py"), seg]
    if expect_token is not None:
        cmd += ["--expect-token", expect_token]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), timeout=timeout)


def write_segpack(root, seg, **kwargs):
    write_json(root / "segments" / f"segpack_{seg}.json", make_segpack(**kwargs))


def write_draft(root, seg, draft_obj):
    write_json(root / "segments" / f"{seg}.draft.json", draft_obj)


def test_case8a_draft_ready_omitted_expect_token_preserves_legacy_behavior(tmp_path):
    """Omitting --expect-token entirely preserves the pre-1.2.0 behavior:
    a legacy draft with no dispatch_token key at all is still READY (the
    hand-rolled check_draft_structure() self-check does not require
    dispatch_token -- only the on-disk draft.schema.json does)."""
    root = make_draft_ready_root(tmp_path)
    seg = "seg01"
    write_segpack(root, seg)
    write_draft(root, seg, make_draft(seg))  # NOTSET -- no dispatch_token key

    proc = run_draft_ready(root, seg)  # no --expect-token
    assert proc.returncode == 0, (
        f"omitting --expect-token must preserve pre-1.2.0 behavior -- a "
        f"legacy tokenless draft must still be READY. rc={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_case8b_draft_ready_expect_token_rejects_straggler_old_run_draft(tmp_path):
    """Supplying --expect-token enforces the token match: a delayed OLD-run
    translator that finishes writing AFTER a fresh run has already started
    (overwriting the unscoped draft path with its stale token) is never
    READY for the current run."""
    root = make_draft_ready_root(tmp_path)
    seg = "seg01"
    write_segpack(root, seg)
    new_token = "RUN_NEW:" + seg
    old_token = "RUN_OLD:" + seg

    write_draft(root, seg, make_draft(seg, dispatch_token=new_token))
    proc_fresh = run_draft_ready(root, seg, expect_token=new_token)
    assert proc_fresh.returncode == 0, (
        f"a draft carrying the CURRENT run's token must be READY when "
        f"--expect-token matches. rc={proc_fresh.returncode}\nstdout={proc_fresh.stdout}"
    )

    # The delayed old-run translator's write lands here, AFTER the fresh
    # run's own draft was already confirmed ready above.
    write_draft(root, seg, make_draft(seg, dispatch_token=old_token))
    proc_stale = run_draft_ready(root, seg, expect_token=new_token)
    assert proc_stale.returncode != 0, (
        f"a straggler draft from a DIFFERENT (old) run must never be "
        f"READY for the current run, even though it is otherwise "
        f"structurally complete. rc={proc_stale.returncode}\nstdout={proc_stale.stdout}"
    )


# ===========================================================================
# Case 9: draft_sha1.py is stable across a token-only change
# ===========================================================================

def make_draft_sha1_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(DRAFT_SHA1_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    (root / "segments").mkdir(parents=True)
    return root


def run_draft_sha1(root, seg, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "draft_sha1.py"), seg],
        capture_output=True, text=True, cwd=str(root), timeout=timeout,
    )


def test_case9_draft_sha1_stable_across_token_only_change(tmp_path):
    """CONTRACT §2: draft_sha1.py EXCLUDES dispatch_token from the content
    hash -- two drafts differing ONLY in dispatch_token must hash
    identically. Regression-catcher: also proves the hash DOES react to a
    real content change (so the stability assertion above isn't vacuously
    true because the script ignores everything)."""
    root = make_draft_sha1_root(tmp_path)
    seg = "seg01"
    shared_content = dict(blocks={"b1": "hello"}, footnotes={}, verses={}, names=[], notes=["a note"])

    write_draft(root, seg, make_draft(seg, dispatch_token="RUN_A:" + seg, **shared_content))
    proc_a = run_draft_sha1(root, seg)
    assert proc_a.returncode == 0, proc_a.stderr
    sha_a = proc_a.stdout.strip()

    write_draft(root, seg, make_draft(seg, dispatch_token="RUN_B:" + seg, **shared_content))
    proc_b = run_draft_sha1(root, seg)
    assert proc_b.returncode == 0, proc_b.stderr
    sha_b = proc_b.stdout.strip()

    assert sha_a == sha_b, (
        f"draft_sha1.py must be stable across a token-only change -- got "
        f"{sha_a!r} vs {sha_b!r} for byte-identical content under "
        f"different dispatch_tokens"
    )

    changed_content = dict(shared_content)
    changed_content["blocks"] = {"b1": "hello, CHANGED"}
    write_draft(root, seg, make_draft(seg, dispatch_token="RUN_B:" + seg, **changed_content))
    proc_c = run_draft_sha1(root, seg)
    assert proc_c.returncode == 0, proc_c.stderr
    sha_c = proc_c.stdout.strip()
    assert sha_c != sha_b, (
        "draft_sha1.py must change when the actual translated content "
        "changes -- otherwise the token-stability assertion above would "
        "be vacuously true (a hash that ignores everything is also 'stable')"
    )


# ===========================================================================
# Case 10: straggler overwrite in the poll->read window (TOCTOU approximation)
# ===========================================================================
#
# True concurrent-process races on the filesystem are not practically
# unit-testable. What IS testable, and what these two tests lock down: the
# gate has no "sticky" memory of a past READY verdict -- each invocation
# re-validates the CURRENT on-disk bytes from scratch. So a caller that
# re-invokes the same readiness probe (or an equivalent read-time check)
# immediately before consuming an artifact's bytes is protected, closing
# the window described in PLAN [cx10#1] as far as a static precondition
# test can. This does NOT prove there is no gap between the moment a
# bounded-poll loop last observed READY and the moment the consumer reads
# the bytes -- only that the check itself, if re-run at read time, would
# have caught a straggler that landed in that gap.

def test_case10a_draft_side_toctou_approximation_rejects_straggler(tmp_path):
    root = make_draft_ready_root(tmp_path)
    seg = "seg01"
    write_segpack(root, seg)
    new_token = "RUN_NEW:" + seg
    old_token = "RUN_OLD:" + seg

    write_draft(root, seg, make_draft(seg, dispatch_token=new_token))
    poll_result = run_draft_ready(root, seg, expect_token=new_token)
    assert poll_result.returncode == 0  # the bounded poll reports READY

    # Between the poll reporting READY and the consumer reading the bytes,
    # an old-run straggler translator finishes and overwrites the SAME
    # unscoped path with its old token.
    write_draft(root, seg, make_draft(seg, dispatch_token=old_token))

    read_time_check = run_draft_ready(root, seg, expect_token=new_token)
    assert read_time_check.returncode != 0, (
        "a straggler overwrite in the poll-to-read window must still be "
        "rejected at the point of consumption -- a stale poll verdict is "
        "never sufficient on its own"
    )


def test_case10b_review_side_toctou_approximation_rejects_straggler(tmp_path):
    """Review-side echo of case 10a -- see this section's banner comment
    for the approximation caveat."""
    root = make_review_ready_root(tmp_path)
    seg = "seg01"
    new_draft_token = "RUN_NEW:" + seg
    new_review_token = new_draft_token + ":rfinal"
    old_review_token = "RUN_OLD:" + seg + ":rfinal"

    write_json(root / "segments" / f"{seg}.draft.json", make_draft(seg, dispatch_token=new_draft_token))
    real_sha1 = compute_real_draft_sha1(root, seg)
    write_json(root / "segments" / f"{seg}.review.json", make_review(real_sha1, dispatch_token=new_review_token))

    poll_result = run_review_ready(root, seg, expect_token=new_review_token)
    assert poll_result.returncode == 0

    # A straggler restores an OLD-run review.json in the poll-to-read window.
    write_json(root / "segments" / f"{seg}.review.json", make_review(real_sha1, dispatch_token=old_review_token))

    read_time_check = run_review_ready(root, seg, expect_token=new_review_token)
    assert read_time_check.returncode != 0, (
        "a straggler review.json restored in the poll-to-read window must "
        "still be rejected at read time"
    )


# ===========================================================================
# Case 11: ledger_update.py's per-segment convergence token precondition
# ===========================================================================

def make_ledger_update_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(LEDGER_UPDATE_SRC, scripts_dir / "ledger_update.py")
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")  # fixture helper only
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(DRAFT_SHA1_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    schemas_dir = root / "schemas"
    schemas_dir.mkdir(parents=True)
    shutil.copy2(SCHEMAS_DIR / "ledger-record-base.schema.json", schemas_dir / "ledger-record-base.schema.json")
    shutil.copy2(SCHEMAS_DIR / "ledger-fragment.schema.json", schemas_dir / "ledger-fragment.schema.json")
    (root / "segments").mkdir(parents=True)
    (root / "runs").mkdir(parents=True)
    return root


def write_payload_file(root, name, payload):
    path = root / "runs" / f".payload_{name}.json"
    write_json(path, payload)
    return path


def run_ledger_update(root, seg, payload_path, timeout=30):
    """No --expect-token/--run-token CLI flag on this script -- the token
    precondition input is the payload file's own OPTIONAL `run_token`
    field (a bare RUN_ID), embedded by the caller before invoking this."""
    cmd = [sys.executable, str(root / "scripts" / "ledger_update.py"), seg, "--payload-file", str(payload_path)]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), timeout=timeout)


def converged_payload(run_token=None):
    """ledger-record-base.schema.json requires `cache_key` and `rounds`
    whenever status=='converged' (an if/then conditional) -- both must be
    present in the payload for validate_final_fragment() to accept the
    write once enrich_converged_fields() has populated the rest, or the
    fixture would fail schema validation regardless of the token check."""
    payload = {"status": "converged", "rounds": 1, "cache_key": make_cache_key_composite("c11")}
    if run_token is not None:
        payload["run_token"] = run_token
    return payload


def seed_case11_fixture(root, seg, draft_token, review_token):
    write_json(root / "segments" / f"segpack_{seg}.json", make_segpack())
    write_json(root / "segments" / f"{seg}.draft.json", make_draft(seg, dispatch_token=draft_token))
    real_sha1 = compute_real_draft_sha1(root, seg)
    write_json(root / "segments" / f"{seg}.review.json", make_review(real_sha1, dispatch_token=review_token))
    return real_sha1


def test_case11_positive_control_matching_tokens_converges(tmp_path):
    """Positive control: `run_token` (in the payload) is the bare RUN_ID.
    The draft's dispatch_token must equal expected_draft_token(run_token,
    seg) = "<run_token>:<seg>" EXACTLY; review.json's must satisfy
    review_token_matches() -- a "<run_token>:<seg>:r" PREFIX match. Both
    genuinely belong to the current run+segment, so convergence must be
    recorded."""
    root = make_ledger_update_root(tmp_path)
    seg = "seg01"
    run_id = "RUN2026"
    draft_token = f"{run_id}:{seg}"
    review_token = f"{run_id}:{seg}:rfinal"
    seed_case11_fixture(root, seg, draft_token, review_token)
    payload_path = write_payload_file(root, "p11pos", converged_payload(run_id))

    result = run_ledger_update(root, seg, payload_path)
    stdout = parse_one_json_line(result)
    assert stdout.get("success") is True, (
        f"both the draft's and review's dispatch_token match the expected "
        f"draft token for the current run_token -- convergence must be "
        f"recorded. Got: {stdout}"
    )


def test_case11_draft_token_stale_review_token_fresh_refuses(tmp_path):
    root = make_ledger_update_root(tmp_path)
    seg = "seg01"
    run_id = "RUN2026"
    draft_token_stale = "RUN_OLD:seg01"  # != expected_draft_token(run_id, seg)
    review_token_fresh = f"{run_id}:{seg}:rfinal"  # matches review_token_matches()
    seed_case11_fixture(root, seg, draft_token_stale, review_token_fresh)
    payload_path = write_payload_file(root, "p11a", converged_payload(run_id))

    # The review side matches cleanly, isolating the draft-side mismatch as
    # the sole cause of refusal.
    result = run_ledger_update(root, seg, payload_path)
    stdout = parse_one_json_line(result)
    assert stdout.get("success") is False, (
        f"a STALE draft token must refuse convergence even when the "
        f"review's token is fresh. Got: {stdout}"
    )
    assert "draft" in stdout.get("error", "").lower(), (
        f"the refusal should name the draft as the mismatching artifact: {stdout}"
    )


def test_case11_draft_token_fresh_review_token_stale_refuses(tmp_path):
    root = make_ledger_update_root(tmp_path)
    seg = "seg01"
    run_id = "RUN2026"
    draft_token = f"{run_id}:{seg}"  # == expected_draft_token(run_id, seg)
    review_token_stale = "RUN_OLD:seg01:rfinal"  # fails review_token_matches()
    seed_case11_fixture(root, seg, draft_token, review_token_stale)
    payload_path = write_payload_file(root, "p11b", converged_payload(run_id))

    result = run_ledger_update(root, seg, payload_path)
    stdout = parse_one_json_line(result)
    assert stdout.get("success") is False, (
        f"a STALE review token must refuse convergence even when the "
        f"draft's token is fresh. Got: {stdout}"
    )
    assert "review" in stdout.get("error", "").lower(), (
        f"the refusal should name the review artifact as the mismatching "
        f"artifact: {stdout}"
    )


# ===========================================================================
# Case 12: ledger_merge.py's batch-final per-segment token/sha re-check
# ===========================================================================

def make_ledger_merge_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")  # fixture helper only
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(DRAFT_SHA1_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    schemas_dir = root / "schemas"
    shutil.copytree(SCHEMAS_DIR, schemas_dir)  # ledger_merge.py globs *.schema.json
    (root / "segments").mkdir(parents=True)
    (root / "runs" / "ledger.d").mkdir(parents=True)
    return root


def run_ledger_merge(root, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "ledger_merge.py"), *extra_args],
        capture_output=True, text=True, cwd=str(root), timeout=timeout,
    )


def write_ledger_fragment(root, seg, fragment):
    write_json(root / "runs" / "ledger.d" / f"{seg}.json", fragment)


def make_converged_fragment(reviewed_draft_sha1, cache_key=None, rounds=1):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": rounds,
        "cache_key": cache_key or make_cache_key_composite("m1"),
        "n_blocks": 0,
        "n_footnotes": 0,
        "n_verses": 0,
        "reviewed_draft_sha1": reviewed_draft_sha1,
    }


def test_case12_positive_control_matching_tokens_reports_success(tmp_path):
    """Positive control, symmetric to case 11's: --run-token is the bare
    RUN_ID; ledger_merge.py's _reassert_token_and_sha() applies the same
    expected_draft_token()/review_token_matches() pair to the draft's and
    review.json's own dispatch_token -- both genuinely belong to the
    current run+segment and the draft content hasn't drifted, so the batch
    merge must report success."""
    root = make_ledger_merge_root(tmp_path)
    seg = "seg01"
    run_id = "RUN2026"
    draft_token = f"{run_id}:{seg}"
    review_token = f"{run_id}:{seg}:rfinal"
    write_json(root / "segments" / f"{seg}.draft.json", make_draft(seg, dispatch_token=draft_token))
    real_sha1 = compute_real_draft_sha1(root, seg)
    write_json(root / "segments" / f"{seg}.review.json", make_review(real_sha1, dispatch_token=review_token))
    write_ledger_fragment(root, seg, make_converged_fragment(real_sha1))

    result = run_ledger_merge(
        root, "--expected-segs", seg, "--run-token", run_id, "--skip-stale-check",
    )
    stdout = parse_one_json_line(result)
    assert stdout.get("success") is True, (
        f"both draft's and review's dispatch_token RUN_ID component equal "
        f"the current run_token, and the draft content hasn't drifted -- "
        f"the batch merge must report success. Got: {stdout}"
    )


def test_case12_stale_token_pair_never_reports_success_true(tmp_path):
    """The LOAD-BEARING safety guarantee (PLAN [cx12#1]): an old-token
    straggler pair restored between the per-segment convergence write and
    the batch merge-ledger must NEVER let success:true (batchComplete)
    materialize, even though the fragment itself correctly recorded
    convergence earlier under the (then-current) token."""
    root = make_ledger_merge_root(tmp_path)
    seg = "seg01"
    old_draft_token = "RUN_OLD:" + seg
    old_review_token = "RUN_OLD:" + seg + ":rfinal"
    fresh_run_id = "RUN2026"  # the CURRENT batch's run token

    write_json(root / "segments" / f"{seg}.draft.json", make_draft(seg, dispatch_token=old_draft_token))
    real_sha1 = compute_real_draft_sha1(root, seg)
    write_json(root / "segments" / f"{seg}.review.json", make_review(real_sha1, dispatch_token=old_review_token))
    write_ledger_fragment(root, seg, make_converged_fragment(real_sha1))

    result = run_ledger_merge(
        root, "--expected-segs", seg, "--run-token", fresh_run_id, "--skip-stale-check",
    )
    stdout = parse_one_json_line(result)
    assert stdout.get("success") is False, (
        f"an old-run straggler draft+review pair restored on disk must "
        f"never let batchComplete materialize. Got: {stdout}"
    )


@pytest.mark.xfail(strict=True, reason=(
    "the brief's PINNED expectation ('folds it into missing_segments in "
    "the FAILURE payload') does not match the landed ledger_merge.py: the "
    "batch-final re-verification raise in merge() constructs "
    "LedgerMergeError WITHOUT a missing_segments= kwarg, so main()'s "
    "'if e.missing_segments is not None' guard never adds the key -- the "
    "FAILURE payload has success:false plus a free-text `error` string "
    "naming the segment in prose, but no missing_segments array. The "
    "core safety property (never success:true) IS upheld -- see "
    "test_case12_stale_token_pair_never_reports_success_true -- only "
    "this packaging detail is unimplemented."
))
def test_case12_stale_token_pair_surfaces_in_missing_segments(tmp_path):
    root = make_ledger_merge_root(tmp_path)
    seg = "seg01"
    old_draft_token = "RUN_OLD:" + seg
    old_review_token = "RUN_OLD:" + seg + ":rfinal"
    fresh_run_id = "RUN2026"

    write_json(root / "segments" / f"{seg}.draft.json", make_draft(seg, dispatch_token=old_draft_token))
    real_sha1 = compute_real_draft_sha1(root, seg)
    write_json(root / "segments" / f"{seg}.review.json", make_review(real_sha1, dispatch_token=old_review_token))
    write_ledger_fragment(root, seg, make_converged_fragment(real_sha1))

    result = run_ledger_merge(
        root, "--expected-segs", seg, "--run-token", fresh_run_id, "--skip-stale-check",
    )
    stdout = parse_one_json_line(result)
    assert stdout.get("success") is False
    assert seg in stdout.get("missing_segments", []), (
        f"a stale/foreign-token segment should be folded into "
        f"missing_segments per the brief's pinned packaging, got: {stdout}"
    )


# ===========================================================================
# --durable-root PATH (LT-409, post-review correction): an explicit,
# caller-supplied DATA root (schemas/runs) -- REPLACES self-anchoring for
# data when given. Deliberately does NOT redirect where the cache_key.py
# sibling script is found -- that is --plugin-root's own, independent
# concern (see the dedicated section below). Byte-identical to today's
# self-anchored behavior for both when both flags are omitted.
# ===========================================================================

def run_resume_setup_from(script_path, payload_obj, tmp_dir, *extra_args, timeout=30, cwd=None):
    """`cwd=None` (the default) preserves every pre-existing caller's
    behavior exactly (subprocess.run() with no cwd= inherits the test
    process's own cwd) -- only a caller that needs to control the SUBPROCESS's
    own working directory (e.g. to exercise a caller-relative --durable-root
    end to end) passes one explicitly."""
    payload_path = tmp_dir / "scratch_resume_payload.json"
    write_json(payload_path, payload_obj)
    cmd = [sys.executable, str(script_path), "--payload-file", str(payload_path), *extra_args]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        cwd=str(cwd) if cwd is not None else None,
    )
    parsed = None
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) == 1:
        try:
            parsed = json.loads(lines[0])
        except json.JSONDecodeError:
            parsed = None
    return proc, parsed


def test_durable_root_flag_absent_orphan_copy_fails_self_anchored(tmp_path):
    """Negative control: an orphan copy invoked WITHOUT --durable-root
    cannot succeed via self-anchoring -- it self-anchors to a location with
    no manifest.json, no runs/ dir, no plugin_bundle_hash marker. Asserts
    the SPECIFIC reason (manifest.json, the first of those
    compute_input_digest()'s mass branch reads as of LT-409 -- earlier it
    was the plugin_bundle_hash marker, moved here when the digest domain
    moved to manifest.json), not merely that some failure occurred: a bare
    "it failed" assertion cannot tell a correct self-anchoring refusal
    apart from an unrelated crash, so a future defect that broke the
    orphan-copy path for the WRONG reason would pass this test silently.
    See resume_integrity.test.py's own review history for why this
    specificity matters here."""
    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "resume_setup.py"
    shutil.copy2(RESUME_SETUP_SRC, orphan_script)
    # json_stdout.py (#369): this fixture stages ONE script on purpose, and
    # the property under test needs it to START -- without its sibling it
    # would exit on the missing helper instead, which is a different test.
    shutil.copy2(RESUME_SETUP_SRC.parent / "json_stdout.py", orphan_script.parent / "json_stdout.py")

    proc, parsed = run_resume_setup_from(orphan_script, mass_base_payload(), tmp_path)

    assert proc.returncode != 0
    assert parsed is not None and parsed.get("success") is False
    assert "manifest.json" in (parsed.get("error") or ""), (
        f"expected the orphan copy to fail specifically on its missing "
        f"manifest.json -- got a different reason, which means either the "
        f"validation order changed (update this assertion to match) or "
        f"something else broke the orphan-copy path: {parsed}"
    )


def test_durable_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility: the ordinary in-place fixture, invoked with
    no --durable-root/--plugin-root at all, behaves exactly as before."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())

    proc, parsed = run_resume_setup(root, mass_base_payload())

    parsed = assert_setup_success(proc, parsed)
    assert parsed.get("resume") is False


# ---------------------------------------------------------------------------
# A RELATIVE --durable-root must be resolved exactly ONCE (LT-409 post-review
# fix). resolve_dirs() already resolves it correctly against resume_setup.py's
# OWN cwd -- but _cache_key_for_seg() then runs the cache_key.py subprocess
# with cwd SET TO that already-resolved root while (pre-fix) forwarding the
# ORIGINAL, still-relative string as the subprocess's own --durable-root.
# cache_key.py resolves ITS --durable-root against ITS OWN cwd (the
# already-resolved root) -- joining the relative fragment onto the root a
# second time.
# ---------------------------------------------------------------------------

PATH_PROBE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    parser.add_argument("--durable-root", default=None)
    args, _ = parser.parse_known_args()
    if args.durable_root:
        resolved = Path(args.durable_root).resolve()
    else:
        resolved = Path(__file__).resolve().parent.parent
    # Record what THIS invocation resolved --durable-root to. __file__ is
    # this stub's own FIXED on-disk location, unaffected by any doubling bug
    # in the --durable-root VALUE it receives, so the probe file's own path
    # is trustworthy regardless of what is under test.
    probe_path = Path(__file__).resolve().parent.parent / "cache_key_probe.jsonl"
    with open(probe_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"seg": args.seg, "resolved_durable_root": str(resolved)}) + "\\n")
    if not args.seg:
        sys.stderr.write("path-probe cache_key.py: test stub requires --seg\\n")
        return 1
    # Always succeeds with a real-shaped (if fake) composite, regardless of
    # what it resolved -- decouples "did resume_setup.py notice a problem"
    # from "did cache_key.py read the RIGHT tree", so the doubled-path defect
    # is caught by a direct path comparison even in a build where it happens
    # not to crash (e.g. the doubled directory exists for an unrelated
    # reason) -- the more dangerous, silent failure mode.
    print(json.dumps({"probe": True, "seg": args.seg}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def test_relative_durable_root_is_not_double_resolved_for_cache_key_subprocess(tmp_path):
    """Caller runs from an outer cwd (e.g. `cd /repo`) with a RELATIVE
    --durable-root (e.g. `projects/book`) -- the exact shape every real
    caller of this script COULD use, even though every other test in this
    file happens to pass an absolute one."""
    outer = tmp_path  # stands in for the caller's own cwd, e.g. "/repo"
    root = make_resume_setup_root(outer, name="projects/book")
    (root / "scripts" / "cache_key.py").write_text(PATH_PROBE_CACHE_KEY_PY, encoding="utf-8")
    probe_path = root / "cache_key_probe.jsonl"
    assert not probe_path.exists()  # fixture sanity: nothing recorded yet

    proc, parsed = run_resume_setup_from(
        root / "scripts" / "resume_setup.py",
        mass_base_payload(),
        root,
        "--durable-root", "projects/book",  # RELATIVE, relative to `outer`
        cwd=outer,
    )

    assert probe_path.is_file(), (
        f"the probe stub never ran -- resume_setup.py must have failed "
        f"before even shelling out to cache_key.py: rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    probe_lines = [
        json.loads(ln) for ln in probe_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert probe_lines, "probe file exists but recorded no invocations"
    for entry in probe_lines:
        assert entry["resolved_durable_root"] == str(root), (
            f"cache_key.py's own --durable-root resolution must land on the "
            f"SAME root resume_setup.py itself resolved ({root}) -- got "
            f"{entry['resolved_durable_root']!r} for seg {entry['seg']!r}. A "
            f"doubled path here (the relative fragment 'projects/book' "
            f"joined onto root a second time) means the raw relative "
            f"string was forwarded verbatim into a subprocess whose cwd is "
            f"already that resolved root."
        )

    # The probe stub never fails, so resume_setup.py itself must have
    # reported success -- proving the wrong-tree read (pre-fix) would have
    # been entirely SILENT: a caller reading only {"success": true, ...}
    # would never learn its digest was computed from the wrong directory.
    parsed = assert_setup_success(proc, parsed)


# ---------------------------------------------------------------------------
# --plugin-root PATH (LT-409, post-review correction): the SECURITY property
# this flag exists for. ${durable_root}/scripts/ is a Step-0a copy that the
# codex process can write to (codex_job.py grants --write over the whole
# durable root), so a sibling script resolved FROM durable_root could be a
# tampered copy validating itself. --plugin-root is a SEPARATE, orthogonal
# input that must NEVER be derived from --durable-root.
# ---------------------------------------------------------------------------

def tampered_cache_key_py_src(fixed_key: dict) -> str:
    """A fake cache_key.py that ALWAYS echoes back `fixed_key` regardless of
    --seg/--durable-root, simulating a codex-tampered copy designed to
    report a constant value rather than genuinely recomputing anything."""
    return (
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        f"print(json.dumps({fixed_key!r}))\n"
        "sys.exit(0)\n"
    )


def make_trusted_plugin_root(tmp_path, name="trusted_plugin_install"):
    """A SEPARATE physical location holding the REAL (test-stub)
    cache_key.py at the {plugin_root}/assets/scripts/ layout SKILL.md
    documents for the plugin-anchored scripts."""
    plugin_root = tmp_path / name
    plugin_scripts_dir = plugin_root / "assets" / "scripts"
    plugin_scripts_dir.mkdir(parents=True)
    (plugin_scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    return plugin_root


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling(tmp_path):
    """resume_setup.py runs from its OWN in-place durable-root copy
    (production's normal invocation shape) whose SIBLING cache_key.py has
    been TAMPERED to always report a FIXED, wrong composite key regardless
    of the real segpack/canon state -- simulating a codex-compromised copy.
    --plugin-root pointing at a separate, untampered location must make the
    resulting input_digest reflect the REAL fixture keys instead -- proving
    the poisoned durable-root sibling was never consulted."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    poisoned_key = make_cache_key_composite("POISONED")
    (root / "scripts" / "cache_key.py").write_text(
        tampered_cache_key_py_src(poisoned_key), encoding="utf-8"
    )
    plugin_root = make_trusted_plugin_root(tmp_path)
    payload = mass_base_payload()

    proc_trusted, parsed_trusted = run_resume_setup_from(
        root / "scripts" / "resume_setup.py", payload, tmp_path, "--plugin-root", str(plugin_root)
    )
    proc_poisoned, parsed_poisoned = run_resume_setup(root, payload)  # no --plugin-root

    assert proc_trusted.returncode == 0, f"stdout={proc_trusted.stdout}\nstderr={proc_trusted.stderr}"
    parsed_trusted = assert_setup_success(proc_trusted, parsed_trusted)
    parsed_poisoned = assert_setup_success(proc_poisoned, parsed_poisoned)
    assert parsed_trusted["input_digest"] != parsed_poisoned["input_digest"], (
        "the trusted plugin-root cache_key.py must have produced a "
        "DIFFERENT digest than the poisoned durable-root sibling -- if "
        "they matched, the poisoned copy's constant answer was used either "
        f"way: trusted={parsed_trusted}\npoisoned={parsed_poisoned}"
    )


def test_durable_root_and_plugin_root_are_independently_resolved(tmp_path):
    """Orthogonality, end to end, from a fully orphan copy: --durable-root
    points at a DATA-only fixture with NO scripts/ directory AT ALL,
    --plugin-root points at a SEPARATE, scripts-only fixture with no data
    of its own. Success proves the two concerns are genuinely resolved
    independently, never conflated into one root."""
    data_root = make_resume_setup_root(tmp_path, name="data_only")
    write_fixture_cache_keys(data_root, mass_base_cache_keys())
    # Remove the scripts/ dir make_resume_setup_root created -- this fixture
    # must have NO sibling scripts of its own at all.
    shutil.rmtree(data_root / "scripts")
    assert not (data_root / "scripts").exists()

    plugin_root = make_trusted_plugin_root(tmp_path, name="plugin_only")

    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "resume_setup.py"
    shutil.copy2(RESUME_SETUP_SRC, orphan_script)
    # json_stdout.py (#369): this fixture stages ONE script on purpose, and
    # the property under test needs it to START -- without its sibling it
    # would exit on the missing helper instead, which is a different test.
    shutil.copy2(RESUME_SETUP_SRC.parent / "json_stdout.py", orphan_script.parent / "json_stdout.py")

    proc, parsed = run_resume_setup_from(
        orphan_script,
        mass_base_payload(),
        tmp_path,
        "--durable-root", str(data_root),
        "--plugin-root", str(plugin_root),
    )

    assert proc.returncode == 0, (
        f"durable-root (data) and plugin-root (sibling) must resolve "
        f"independently -- got rc={proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    parsed = assert_setup_success(proc, parsed)
    assert (data_root / "runs" / parsed["effectiveRunId"] / "input.digest").is_file()
    assert not (plugin_root / "runs").exists()


def test_plugin_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility for the split itself: --durable-root alone
    (no --plugin-root) still resolves the sibling self-anchored, exactly as
    before the split."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())

    proc, parsed = run_resume_setup_from(
        root / "scripts" / "resume_setup.py",
        mass_base_payload(),
        tmp_path,
        "--durable-root", str(root),
    )

    parsed = assert_setup_success(proc, parsed)
    assert parsed.get("resume") is False


# ---------------------------------------------------------------------------
# payload['plugin_root'] (#412): a TOP-LEVEL, optional field -- the SAME
# value the orchestrating session substitutes into the Workflow template's
# own {{PLUGIN_ROOT}} token, recorded here for the producer-side contract but
# deliberately NEVER folded into input_digest. See the module docstring's
# payload-shape block and SUBST_FIELDS's own comment for the full reasoning
# (a filesystem path is not a semantic value, and hashing a raw absolute
# path would make the digest non-portable across two operators' checkouts).
# ---------------------------------------------------------------------------
def test_payload_plugin_root_field_omitted_preserves_todays_behavior(tmp_path):
    """A payload built before #412 (no 'plugin_root' key at all) must keep
    working unchanged -- this field is optional, defaulting to ""."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    assert "plugin_root" not in payload  # fixture sanity: genuinely absent

    proc, parsed = run_resume_setup(root, payload)

    parsed = assert_setup_success(proc, parsed)


def test_payload_plugin_root_field_accepted_when_present(tmp_path):
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    payload["plugin_root"] = "/some/plugin/install/root"

    proc, parsed = run_resume_setup(root, payload)

    parsed = assert_setup_success(proc, parsed)


def test_payload_plugin_root_field_wrong_type_rejected(tmp_path):
    """Fail loudly (never silently coerced or ignored) on a malformed
    'plugin_root' -- e.g. a caller that accidentally sends a number or an
    object instead of a path string."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    payload["plugin_root"] = 12345

    proc, parsed = run_resume_setup(root, payload)

    assert proc.returncode != 0
    assert parsed is not None and parsed.get("success") is False
    assert "plugin_root" in (parsed.get("error") or ""), (
        f"error message should name the offending field; got: {parsed}"
    )


def test_payload_plugin_root_field_never_changes_input_digest(tmp_path):
    """The load-bearing property of the SUBST_FIELDS exclusion decision:
    two payloads, identical in every OTHER respect, differing ONLY in
    'plugin_root', must produce the EXACT SAME input_digest -- proving this
    field is genuinely excluded from hashing, not merely undocumented."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload_a = mass_base_payload()
    payload_a["plugin_root"] = "/path/one/install"
    payload_b = mass_base_payload()
    payload_b["plugin_root"] = "/completely/different/path/two"

    proc_a, parsed_a = run_resume_setup(root, payload_a)
    # A fresh root for the second call -- run_resume_setup's own root already
    # recorded run_dir/input.digest for payload_a's (matching) digest, and a
    # SECOND resolve_run against the SAME root with an identical digest would
    # resume rather than compute a fresh one to compare -- this test wants
    # two INDEPENDENT digest computations, not a resume decision.
    root_b = make_resume_setup_root(tmp_path, name="durable_root_b")
    write_fixture_cache_keys(root_b, mass_base_cache_keys())
    proc_b, parsed_b = run_resume_setup(root_b, payload_b)

    parsed_a = assert_setup_success(proc_a, parsed_a)
    parsed_b = assert_setup_success(proc_b, parsed_b)
    assert parsed_a["input_digest"] == parsed_b["input_digest"], (
        f"plugin_root must never affect input_digest -- got "
        f"{parsed_a['input_digest']!r} vs {parsed_b['input_digest']!r}"
    )


def test_payload_plugin_root_absent_and_empty_produce_the_same_digest(tmp_path):
    """Companion to the above: omitting 'plugin_root' entirely and setting
    it to "" explicitly (both mean 'no redirect') must ALSO produce
    identical digests -- the field's absence is not itself a distinct value
    from its documented default."""
    root_a = make_resume_setup_root(tmp_path, name="durable_root_omitted")
    write_fixture_cache_keys(root_a, mass_base_cache_keys())
    payload_omitted = mass_base_payload()
    assert "plugin_root" not in payload_omitted

    root_b = make_resume_setup_root(tmp_path, name="durable_root_empty")
    write_fixture_cache_keys(root_b, mass_base_cache_keys())
    payload_empty = mass_base_payload()
    payload_empty["plugin_root"] = ""

    proc_a, parsed_a = run_resume_setup(root_a, payload_omitted)
    proc_b, parsed_b = run_resume_setup(root_b, payload_empty)

    parsed_a = assert_setup_success(proc_a, parsed_a)
    parsed_b = assert_setup_success(proc_b, parsed_b)
    assert parsed_a["input_digest"] == parsed_b["input_digest"]


# ---------------------------------------------------------------------------
# #735: subst['max_codex_jobs_per_batch'] -- a REQUIRED payload field that is
# deliberately NOT hashed. Unlike `plugin_root` above (a TOP-LEVEL field), this
# one lives INSIDE `subst`, so the accepted set and the hashed set are no longer
# the same object: SUBST_FIELDS stays the producer-side contract every payload
# must satisfy, and DIGEST_SUBST_FIELDS is the subset compute_input_digest()
# actually projects. The knob is a preflight VOLUME CAP -- it decides whether a
# batch is refused before any dispatch and reaches no agent prompt and no
# translation, review or ledger artifact (the preflight's own refusal result
# and the driver's session journal do record it, as diagnostics ABOUT the run
# rather than a cached result a later run could reuse) -- so hashing it made
# the ONE knob most likely to
# need adjusting once a book's real shape is known also the one that punished
# adjusting it (fresh RUN_ID -> DRAFT_TOKEN_MISMATCH on every draft in flight).
# Same reasoning `agent_config_hash` already applies to batch_agent_cap.
# ---------------------------------------------------------------------------

# Every member DIGEST_SUBST_FIELDS still projects, each mapped to a value that
# differs from BASE_SUBST's. Deliberately NOT a hand-picked sample: the drift
# pin below asserts this table's key set EQUALS the projection, so a field
# added to SUBST_FIELDS later cannot slip past the behavioural test by simply
# not having been thought of here.
_HASHED_SUBST_PROBES = {
    "research_mode": "offline",
    "verse_policy": "prose",
    "source_lang": "de",
    "target_lang": "es",
    "max_fix_rounds": 9,
    "batch_agent_cap": 7,
    "effort": "xhigh",
    "citation_content_types": "text/,application/pdf",
}


def _glossary_base_payload():
    """The glossary twin of mass_base_payload(). Both digest KINDS share one
    `subst` projection, so every property below is asserted against both."""
    return {
        "kind": "glossary",
        "args": {"candidates": [{"name": "Alice Smith"}]},
        "subst": dict(BASE_SUBST),
        "glossary_rule": "strict",
        "batches": [{"index": 0, "names": ["Alice Smith"]}],
    }


def _digest_with_subst(tmp_path, name, kind, subst_overrides):
    """One INDEPENDENT digest computation, in its own durable root. A fresh
    root per call, so no two computations here can interact through a root's
    recorded run dirs at all -- this file's digest comparisons want two
    genuine computations and nothing else. (These payloads offer no resume
    candidate, so neither call could resume in any case; the isolation is what
    makes that irrelevant rather than something to reason about.)"""
    root = make_resume_setup_root(tmp_path, name=name)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload() if kind == "mass" else _glossary_base_payload()
    payload["subst"] = {**payload["subst"], **subst_overrides}
    proc, parsed = run_resume_setup(root, payload)
    parsed = assert_setup_success(proc, parsed)
    return parsed["input_digest"]


@pytest.mark.parametrize("kind", ["mass", "glossary"])
def test_max_codex_jobs_per_batch_never_changes_input_digest(tmp_path, kind):
    """The load-bearing property of the #735 exclusion: two payloads identical
    in every other respect, differing ONLY in the volume cap, must produce the
    EXACT SAME input_digest -- so raising the cap mid-book neither mints a
    fresh RUN_ID nor orphans a draft in flight.

    Parametrized over BOTH kinds because `subst` is projected once, AFTER the
    mass/glossary domain split: an implementation projecting a different set
    per kind would satisfy a mass-only test and still collide on the other
    branch."""
    low = _digest_with_subst(
        tmp_path, f"durable_root_cap_low_{kind}", kind, {"max_codex_jobs_per_batch": 400}
    )
    high = _digest_with_subst(
        tmp_path, f"durable_root_cap_high_{kind}", kind, {"max_codex_jobs_per_batch": 4000}
    )

    assert low == high, (
        f"engine.max_codex_jobs_per_batch must never affect a {kind} input_digest -- got "
        f"{low!r} vs {high!r}"
    )


def test_max_codex_jobs_per_batch_is_still_a_required_payload_field(tmp_path):
    """Narrowed at the DIGEST, never deleted from the contract. A payload
    omitting the field must still be refused BY NAME -- otherwise this change
    would have silently turned a required producer-side field optional."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    del payload["subst"]["max_codex_jobs_per_batch"]

    proc, parsed = run_resume_setup(root, payload)

    assert proc.returncode != 0
    assert parsed is not None and parsed.get("success") is False
    assert "max_codex_jobs_per_batch" in (parsed.get("error") or ""), (
        f"the refusal must name the missing field; got: {parsed}"
    )


@pytest.mark.parametrize("kind", ["mass", "glossary"])
@pytest.mark.parametrize("field", sorted(_HASHED_SUBST_PROBES))
def test_every_other_subst_field_still_moves_the_input_digest(tmp_path, field, kind):
    """The other side of the guard, over the WHOLE projection and BOTH kinds
    rather than a chosen few of either. Both axes exist because of a specific
    mutation that would otherwise stay green: narrowing the projection by one
    extra field is invisible to a hand-picked subset that happens not to list
    it, and narrowing it for one KIND only is invisible to a mass-only test --
    and `target_lang` reaches the glossary template too, so that second
    mutation is a real collision rather than a theoretical one."""
    base = _digest_with_subst(tmp_path, f"durable_root_base_{kind}_{field}", kind, {})
    moved = _digest_with_subst(
        tmp_path, f"durable_root_moved_{kind}_{field}", kind, {field: _HASHED_SUBST_PROBES[field]}
    )

    assert moved != base, (
        f"subst[{field!r}] is still a hashed digest field -- changing it must "
        f"change a {kind} input_digest, got {moved!r} for both"
    )


def test_digest_projection_is_subst_fields_minus_exactly_the_volume_cap(tmp_path):
    """The drift pin, DERIVED rather than hand-typed: a literal expected set
    would freeze the very membership it exists to detect. Two properties, and
    the second is what keeps the parametrized test above honest -- a field
    added to SUBST_FIELDS in a later release enters DIGEST_SUBST_FIELDS
    automatically and immediately fails this assertion until it is given a
    probe value, rather than being silently untested."""
    root = make_resume_setup_root(tmp_path)
    module = _load_resume_setup_module(root)

    assert module.DIGEST_SUBST_FIELDS == module.SUBST_FIELDS - {"max_codex_jobs_per_batch"}
    assert set(_HASHED_SUBST_PROBES) == set(module.DIGEST_SUBST_FIELDS), (
        "every hashed subst field needs a probe value in _HASHED_SUBST_PROBES"
    )


# ===========================================================================
# LT-409: the manifest-derived mass digest domain, `args={}` pinning, and
# the plural `resume_from_run_ids` field. See resume_setup.py's own module
# docstring for the full contract this section locks down.
# ===========================================================================


def test_mass_args_must_be_empty_object(tmp_path):
    """`args` is PINNED to {} for kind="mass" -- resume_setup.py rejects
    anything else outright, rather than silently hashing an ambiguous
    value (the SEGS list -- what the field used to carry before this fix,
    and the shape #409's own driver docstring documents as the identical
    shrinking-domain defect one level up from `segs` itself)."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    payload["args"] = {"segments": ["seg01", "seg02"]}

    proc, parsed = run_resume_setup(root, payload)

    assert proc.returncode != 0
    assert parsed is not None and parsed.get("success") is False
    assert "args" in (parsed.get("error") or ""), (
        f"error message should name the offending field; got: {parsed}"
    )


@pytest.mark.parametrize("bad_args", [None, [], "seg01", 0, False])
def test_mass_args_rejects_every_non_empty_dict_shape(tmp_path, bad_args):
    """Every one of the three previously-plausible readings (omitted ->
    None, the eligible list, or any other JSON value) is now a hard
    failure -- only the literal {} is accepted."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    if bad_args is None:
        del payload["args"]
    else:
        payload["args"] = bad_args

    proc, parsed = run_resume_setup(root, payload)

    assert proc.returncode != 0
    assert parsed is not None and parsed.get("success") is False


def test_mass_args_empty_object_accepted(tmp_path):
    """Positive control for the two tests above: the ONE legal value, {},
    is accepted and setup succeeds."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    assert payload["args"] == {}

    proc, parsed = run_resume_setup(root, payload)

    parsed = assert_setup_success(proc, parsed)


def test_mass_segs_field_is_ignored_entirely(tmp_path):
    """The deprecated 'segs' field must not affect input_digest AT ALL --
    proven by setting it to something the PRE-LT-409 code would have
    rejected outright (an empty list) in one payload, and confirming setup
    still succeeds with the exact SAME digest as an ordinary payload whose
    'segs' matches the manifest."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload_a = mass_base_payload()
    proc_a, parsed_a = run_resume_setup(root, payload_a)
    parsed_a = assert_setup_success(proc_a, parsed_a)

    root_b = make_resume_setup_root(tmp_path, name="durable_root_b")
    write_fixture_cache_keys(root_b, mass_base_cache_keys())
    payload_b = mass_base_payload()
    payload_b["segs"] = []  # would have been rejected outright pre-LT-409
    proc_b, parsed_b = run_resume_setup(root_b, payload_b)
    parsed_b = assert_setup_success(proc_b, parsed_b)

    assert parsed_a["input_digest"] == parsed_b["input_digest"], (
        "the deprecated 'segs' field must never affect input_digest"
    )


def test_mass_segs_field_omitted_still_works(tmp_path):
    """'segs' is optional now -- omitting it entirely behaves identically
    to supplying it (both backward- and forward-compatible)."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    del payload["segs"]

    proc, parsed = run_resume_setup(root, payload)

    parsed = assert_setup_success(proc, parsed)


def test_mass_domain_now_comes_from_manifest_not_segs(tmp_path):
    """The core LT-409 fix, proven two ways in one test: (a) 'segs' naming
    a segment absent from cache_key.py's own fixture data does not matter
    at all -- setup still succeeds against the REAL manifest set; (b)
    editing manifest.json itself (growing it) DOES change the digest, even
    with 'segs' held byte-identical throughout."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(
        root, {**mass_base_cache_keys(), "seg03": make_cache_key_composite("s3")}
    )
    payload = mass_base_payload()
    payload["segs"] = ["this-segment-does-not-exist-in-cache-keys"]  # ignored -> harmless
    proc0, parsed0 = run_resume_setup(root, payload)
    parsed0 = assert_setup_success(proc0, parsed0)
    run_id = parsed0["effectiveRunId"]

    # Resume against the identical manifest -> matches.
    proc1, parsed1 = run_resume_setup(root, with_resume_from(payload, run_id))
    assert_resumes(proc1, parsed1, run_id)

    # Grow the manifest (a real W2/W3-shaped change) -> must force fresh,
    # even though the payload (including the bogus 'segs') is unchanged.
    write_json(
        root / "manifest.json",
        {"segments": [{"seg": "seg01"}, {"seg": "seg02"}, {"seg": "seg03"}]},
    )
    proc2, parsed2 = run_resume_setup(root, with_resume_from(payload, run_id))
    assert_fresh_no_resume(proc2, parsed2, run_id)


def test_mass_domain_stable_when_only_segs_shrinks_not_manifest(tmp_path):
    """The #392 regression this whole fix targets, reproduced directly:
    'segs' shrinking (simulating select_segments.py's ELIGIBLE list losing
    a segment the instant it converges) must NOT force a fresh run when
    manifest.json itself is unchanged -- this is the exact failure mode
    that used to discard in-flight fix work on every single convergence."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    proc0, parsed0 = run_resume_setup(root, payload)
    parsed0 = assert_setup_success(proc0, parsed0)
    run_id = parsed0["effectiveRunId"]

    shrunk = copy.deepcopy(payload)
    shrunk["segs"] = ["seg01"]  # simulates seg02 having just converged
    proc1, parsed1 = run_resume_setup(root, with_resume_from(shrunk, run_id))
    assert_resumes(proc1, parsed1, run_id)


def test_mass_manifest_missing_fails_loudly(tmp_path):
    root = make_resume_setup_root(tmp_path, mass_segs=())  # no manifest.json written
    write_fixture_cache_keys(root, mass_base_cache_keys())

    proc, parsed = run_resume_setup(root, mass_base_payload())

    assert proc.returncode != 0
    assert parsed is not None and parsed.get("success") is False
    assert "manifest.json" in (parsed.get("error") or "")


def test_mass_manifest_malformed_entry_fails_loudly(tmp_path):
    """manifest.json's segments[] entries must be objects with their own
    'seg' string field -- a bare string is rejected, never silently
    coerced (matching select_segments.py's own load_candidate_segments())."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    write_json(root / "manifest.json", {"segments": ["seg01"]})

    proc, parsed = run_resume_setup(root, mass_base_payload())

    assert proc.returncode != 0
    assert parsed is not None and parsed.get("success") is False


def test_resume_from_run_ids_plural_tries_each_in_order_until_match(tmp_path):
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    proc0, parsed0 = run_resume_setup(root, payload)
    parsed0 = assert_setup_success(proc0, parsed0)
    run_id = parsed0["effectiveRunId"]

    p = copy.deepcopy(payload)
    p["resume_from_run_ids"] = ["nonexistent-1", "nonexistent-2", run_id]
    proc1, parsed1 = run_resume_setup(root, p)
    assert_resumes(proc1, parsed1, run_id)


def test_resume_from_run_ids_no_candidate_matches_mints_fresh(tmp_path):
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    payload["resume_from_run_ids"] = ["nope-1", "nope-2"]

    proc, parsed = run_resume_setup(root, payload)

    parsed = assert_setup_success(proc, parsed)
    assert parsed.get("resume") is False


def test_resume_from_run_ids_empty_list_behaves_like_first_run(tmp_path):
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    payload["resume_from_run_ids"] = []

    proc, parsed = run_resume_setup(root, payload)

    parsed = assert_setup_success(proc, parsed)
    assert parsed.get("resume") is False


def test_resume_from_run_id_and_run_ids_together_rejected(tmp_path):
    """Supplying BOTH the deprecated singular and the new plural field is a
    hard error, never a silently-resolved ambiguity."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    payload["resume_from_run_id"] = "some-run-id"
    payload["resume_from_run_ids"] = ["some-run-id"]

    proc, parsed = run_resume_setup(root, payload)

    assert proc.returncode != 0
    assert parsed is not None and parsed.get("success") is False


def test_resume_from_run_ids_invalid_entry_rejected(tmp_path):
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    payload["resume_from_run_ids"] = ["../escape"]

    proc, parsed = run_resume_setup(root, payload)

    assert proc.returncode != 0
    assert parsed is not None and parsed.get("success") is False


def test_resume_from_run_id_singular_still_works_alone(tmp_path):
    """The deprecated singular field, used alone (no plural field at all),
    must still work exactly as before -- backward compatibility for one
    release."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    payload = mass_base_payload()
    proc0, parsed0 = run_resume_setup(root, payload)
    parsed0 = assert_setup_success(proc0, parsed0)
    run_id = parsed0["effectiveRunId"]

    proc1, parsed1 = run_resume_setup(root, with_resume_from(payload, run_id))
    assert_resumes(proc1, parsed1, run_id)


def test_resume_from_run_ids_computes_domain_exactly_once_regardless_of_candidate_count(tmp_path):
    """LT-409: the entire point of the plural field. compute_input_digest()
    -- and therefore each manifest segment's cache_key.py subprocess spawn
    -- must run EXACTLY ONCE per resume_setup.py invocation, no matter how
    many candidates are offered in 'resume_from_run_ids'. Proven by
    counting REAL cache_key.py spawns via a fixture stub that appends one
    line per invocation: 2 manifest segments x 5 offered (non-matching)
    candidates would be 10 spawns under the old per-candidate-process
    design; this asserts exactly 2."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    counting_cache_key = (
        "#!/usr/bin/env python3\n"
        "import argparse, json, sys\n"
        "from pathlib import Path\n"
        "durable_root = Path(__file__).resolve().parent.parent\n"
        "with open(durable_root / 'spawn_count.log', 'a', encoding='utf-8') as fh:\n"
        "    fh.write('x\\n')\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--seg')\n"
        "p.add_argument('--durable-root', default=None)\n"
        "a, _ = p.parse_known_args()\n"
        "data = json.loads((durable_root / 'test_fixture_cache_keys.json').read_text(encoding='utf-8'))\n"
        "print(json.dumps(data[a.seg]))\n"
    )
    (root / "scripts" / "cache_key.py").write_text(counting_cache_key, encoding="utf-8")

    payload = mass_base_payload()
    payload["resume_from_run_ids"] = ["nope-1", "nope-2", "nope-3", "nope-4", "nope-5"]

    proc, parsed = run_resume_setup(root, payload)

    parsed = assert_setup_success(proc, parsed)
    spawn_log = root / "spawn_count.log"
    lines = spawn_log.read_text(encoding="utf-8").splitlines() if spawn_log.is_file() else []
    assert len(lines) == 2, (
        f"expected exactly 2 cache_key.py spawns (one per manifest segment, "
        f"regardless of 5 offered non-matching candidates), got {len(lines)}"
    )


# ===========================================================================
# The fresh-RUN_ID mint is a check-then-create split across TWO separate
# function calls -- resolve_run()'s own existence check, write_run_dir()'s
# own directory/digest creation -- never one atomic step. Two concurrent
# resume_setup.py invocations against the SAME durable_root (e.g. one
# kind="mass", one kind="glossary" -- exactly the shape SKILL.md's own
# "Default dispatch path" section warns is unguarded: "never run the two
# against the same durable_root concurrently -- nothing in either path
# guards against that") can both observe "this candidate id is not yet
# taken" before EITHER creates anything. codex flagged this mechanism from
# source without demonstrating it. This section demonstrates it directly,
# in two parts, then locks down the fix.
#
#   Part 1 (test_resolve_run_alone...): resolve_run() creates NOTHING on
#   disk -- it only reads. So the collision precondition needs NO threading
#   at all: two plain, sequential calls for two different payloads already
#   return the identical fresh id whenever fresh_run_id() collides. This
#   documents an existing, INTENTIONAL non-atomicity (resolve_run() is a
#   cheap pre-filter, never the authority) -- it stays green before and
#   after the fix below, because resolve_run() itself is not what changes.
#
#   Part 2 (test_concurrent_write_run_dir...): whether the shared id becomes
#   DANGEROUS depends on how the two callers' write_run_dir() calls
#   interleave. This file's own case 10 (above) calls a true OS-level
#   process race "not practically unit-testable" -- but resolve_run()/
#   write_run_dir() are plain, direct-callable functions (unlike
#   draft_ready.py/review_ready.py's subprocess-only surface), so the
#   interleaving CAN be forced deterministically: a barrier inserted at the
#   exact seam between write_run_dir()'s digest_path.exists() check and its
#   write forces both threads to have already passed that check (both
#   independently saw "not yet written") before either is allowed to write
#   -- without changing what either call actually DOES. This is what a true
#   OS-level race could produce on an unlucky interleaving; the barrier only
#   removes the luck.
# ===========================================================================


def _load_module(name, path):
    """Imports a script as an in-process module -- the established pattern
    for direct-function-call unit testing elsewhere in this test suite
    (e.g. orchestration_hash_resume_gating.test.py's own `_load_module`).
    A deliberate departure from THIS file's otherwise subprocess-only house
    style (see the section banner above): forcing a genuine two-caller
    interleaving deterministically needs direct function calls to patch a
    synchronization point into, which a subprocess boundary would hide."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_resume_setup_module(root):
    """Imports the copy of resume_setup.py make_resume_setup_root() already
    placed at {root}/scripts/resume_setup.py -- so the module's own
    self-anchored CACHE_KEY_SCRIPT constant resolves to THIS fixture's
    cache_key.py stub, exactly like every subprocess-based test in this file
    relies on. Importing the ORIGINAL shipped file directly would instead
    self-anchor to the REAL plugin's own cache_key.py, which this fixture's
    lightweight stub is standing in for."""
    return _load_module("resume_setup_under_test_race", root / "scripts" / "resume_setup.py")


def _glossary_payload_for_race():
    return {
        "kind": "glossary",
        "args": {"candidates": []},
        "subst": dict(BASE_SUBST),
        "glossary_rule": "strict",
        "batches": [{"index": 0, "names": ["Alice"]}],
    }


def test_resolve_run_alone_never_creates_so_two_calls_can_share_a_fresh_id(tmp_path):
    """Part 1 -- see section banner above. Zero synchronization: resolve_run()
    only reads, so two SEQUENTIAL calls for two different payloads (no
    threading) already return the identical id whenever fresh_run_id()
    collides."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    resume_setup = _load_resume_setup_module(root)

    resume_setup.fresh_run_id = lambda: "20260802T120000Z"
    dirs = resume_setup.resolve_dirs(None)

    mass_run_id, mass_resume, mass_digest = resume_setup.resolve_run(mass_base_payload(), dirs)
    glossary_run_id, glossary_resume, glossary_digest = resume_setup.resolve_run(
        _glossary_payload_for_race(), dirs
    )

    assert mass_resume is False
    assert glossary_resume is False
    assert mass_run_id == glossary_run_id == "20260802T120000Z", (
        "resolve_run() creates nothing on disk, so two calls for two "
        "different payloads -- even called back-to-back with no threading "
        "at all -- see the SAME 'not yet taken' fresh id whenever "
        "fresh_run_id() collides; the check-then-create split is not "
        "atomic by construction, independent of any true OS-level race"
    )
    assert mass_digest != glossary_digest  # fixture sanity: genuinely different payloads


def test_concurrent_write_run_dir_calls_do_not_silently_clobber_input_digest(tmp_path):
    """Part 2 -- see section banner above. Forces the dangerous interleaving
    deterministically via a barrier at write_run_dir()'s own TOCTOU seam,
    then asserts the SAFE invariant a fix must provide: exactly one of the
    two concurrent claimants wins, the loser is refused BEFORE it can create
    any further side effect (specifically its glossary/runs/<id>/ sibling --
    the exact artifact segment_dispatch_driver.py's own
    _resumable_run_id_candidates() uses to drop a run id as a mass-resume
    candidate), and the surviving input.digest is exactly the winner's, never
    silently replaced by the loser's."""
    root = make_resume_setup_root(tmp_path)
    write_fixture_cache_keys(root, mass_base_cache_keys())
    resume_setup = _load_resume_setup_module(root)
    resume_setup.fresh_run_id = lambda: "20260802T130000Z"
    dirs = resume_setup.resolve_dirs(None)

    mass_payload = mass_base_payload()
    glossary_payload = _glossary_payload_for_race()

    mass_run_id, mass_resume, mass_digest = resume_setup.resolve_run(mass_payload, dirs)
    glossary_run_id, glossary_resume, glossary_digest = resume_setup.resolve_run(glossary_payload, dirs)
    assert mass_run_id == glossary_run_id, "fixture sanity: the Part-1 collision must reproduce here too"
    assert mass_digest != glossary_digest  # fixture sanity: genuinely different payloads

    barrier = threading.Barrier(2, timeout=5)
    real_atomic_write_text = resume_setup._atomic_write_text

    def synced_atomic_write_text(path, text):
        if path.name == "input.digest":
            # Both threads only ever reach here AFTER their own
            # write_run_dir() has already evaluated digest_path.exists() as
            # False (that check runs strictly before this call) -- waiting
            # on the barrier here guarantees BOTH threads passed that check
            # before EITHER is allowed to actually write, which is exactly
            # what an unlucky true OS-level interleaving could also produce.
            barrier.wait()
        return real_atomic_write_text(path, text)

    resume_setup._atomic_write_text = synced_atomic_write_text

    # _atomic_write_text() names its own tmp file from os.getpid() -- correct
    # for two real, DISTINCT OS processes, but two THREADS in this one test
    # process share a single pid, so their tmp files would collide on name
    # (one thread's os.replace() would then race-delete out from under the
    # other's, raising a bare FileNotFoundError that has nothing to do with
    # the digest_path race under test). threading.get_ident() gives each
    # thread the same kind of per-caller uniqueness a distinct PID would --
    # patched narrowly for just this race and restored immediately after.
    real_getpid = resume_setup.os.getpid
    resume_setup.os.getpid = threading.get_ident

    errors = {}
    run_dirs = {}

    def call(kind, run_id, resume, digest, payload):
        try:
            run_dirs[kind] = resume_setup.write_run_dir(run_id, resume, digest, kind, payload, dirs)
        except Exception as exc:  # noqa: BLE001 -- captured for the assertions below, not re-raised
            errors[kind] = exc

    t_mass = threading.Thread(target=call, args=("mass", mass_run_id, mass_resume, mass_digest, mass_payload))
    t_glossary = threading.Thread(
        target=call, args=("glossary", glossary_run_id, glossary_resume, glossary_digest, glossary_payload)
    )
    try:
        t_mass.start()
        t_glossary.start()
        t_mass.join(timeout=10)
        t_glossary.join(timeout=10)
    finally:
        resume_setup.os.getpid = real_getpid

    assert not t_mass.is_alive() and not t_glossary.is_alive(), "a thread deadlocked on the barrier -- fixture bug"

    assert len(errors) == 1, (
        f"exactly ONE of the two concurrent claimants must be refused (never "
        f"zero -- a silent clobber -- and never two) -- got errors={errors!r}, "
        f"successful={sorted(run_dirs)!r}"
    )
    loser_kind = next(iter(errors))
    winner_kind = "glossary" if loser_kind == "mass" else "mass"
    assert isinstance(errors[loser_kind], resume_setup.ResumeSetupError), (
        f"the loser must be refused via the script's own structured "
        f"ResumeSetupError, not an unrelated crash: {errors[loser_kind]!r}"
    )
    assert "input.digest" in str(errors[loser_kind])

    winner_digest = mass_digest if winner_kind == "mass" else glossary_digest
    final_digest = (dirs["runs_dir"] / mass_run_id / "input.digest").read_text(encoding="utf-8").strip()
    assert final_digest == winner_digest, (
        "the surviving input.digest must be exactly the WINNER's -- never "
        "silently replaced by the loser's, and never a torn/partial write"
    )

    # The dangerous downstream consequence codex named: a caller refused by
    # the atomic claim must not go on to create ITS glossary sibling --
    # that sibling is what makes segment_dispatch_driver.py's own
    # _resumable_run_id_candidates() drop this run id as a mass-resume
    # candidate later, orphaning a genuinely-resumable mass run.
    assert not (root / "glossary" / "runs" / mass_run_id).exists() or winner_kind == "glossary", (
        "a glossary sibling may only exist if glossary genuinely WON the "
        "claim -- a refused glossary loser must never reach the point where "
        "it creates one"
    )
