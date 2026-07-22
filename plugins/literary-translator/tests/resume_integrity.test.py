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
    "run_dir": ..., "input_digest": ...}` on success or
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
import json
import shutil
import subprocess
import sys
from pathlib import Path

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

BASE_SUBST = {
    "research_mode": "live",
    "verse_policy": "skip",
    "source_lang": "fr",
    "target_lang": "en",
    "max_fix_rounds": 3,
    "batch_agent_cap": 5,
    "effort": "high",
}


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

DURABLE_ROOT = Path(__file__).resolve().parents[1]
KEYS_PATH = DURABLE_ROOT / "test_fixture_cache_keys.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    args, _ = parser.parse_known_args()
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


def make_resume_setup_root(tmp_path, plugin_bundle_hash="pbh-v1", orchestration_bundle_hash="obh-v1"):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(RESUME_SETUP_SRC, scripts_dir / "resume_setup.py")
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
    assert proc.returncode == 0, (
        f"setup should succeed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert parsed is not None, f"expected one JSON line on stdout, got: {proc.stdout!r}"
    assert parsed.get("success") is True, f"expected success:true, got: {parsed}"


def assert_resumes(proc, parsed, prior_run_id):
    assert_setup_success(proc, parsed)
    assert parsed.get("resume") is True, f"expected resume:true on a digest MATCH, got {parsed}"
    assert parsed.get("effectiveRunId") == prior_run_id, (
        f"a digest MATCH must reuse the exact prior run id -- got "
        f"{parsed.get('effectiveRunId')!r}, expected {prior_run_id!r}"
    )


def assert_fresh_no_resume(proc, parsed, prior_run_id):
    assert_setup_success(proc, parsed)
    assert parsed.get("resume") is False, f"expected resume:false on a digest mismatch, got {parsed}"
    assert parsed.get("effectiveRunId") != prior_run_id, (
        f"a digest MISMATCH must produce a FRESH run id, never reuse the "
        f"prior one (got {parsed.get('effectiveRunId')!r} == prior {prior_run_id!r})"
    )


def mass_base_payload():
    return {
        "kind": "mass",
        "args": {"segments": ["seg01", "seg02"]},
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
    assert_setup_success(proc0, parsed0)
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
    assert_setup_success(proc0, parsed0)
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
    assert_setup_success(proc0, parsed0)
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
    assert_setup_success(proc0, parsed0)
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
    assert_setup_success(proc0, parsed0)
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
    assert_setup_success(proc0, parsed0)
    run_id = parsed0["effectiveRunId"]

    proc1, parsed1 = run_resume_setup(root, with_resume_from(base_payload, run_id))
    assert_resumes(proc1, parsed1, run_id)

    perturbed = copy.deepcopy(base_payload)
    perturbed["subst"]["research_mode"] = "offline"
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
