"""tests/review_rejection.test.py -- #461: a review round whose findings
are unfounded but well-formed cannot currently be discarded.
derive_next_action()'s not-clean branch (segment_dispatch_driver.py)
returns needs_fix whenever draft_matches_review, so an UNCHANGED draft
(nothing real to fix) can never advance to the next review round -- it
renders a fix prompt for a segment with nothing to fix, forever.

#527 extended what the same record reaches, and the tests for it live here
too: at the mandatory `final` round a rejection no longer buys one more
review of the same unchanged draft (a second opinion over one misleading
input is one observation, not two) -- it TERMINATES the unit as converged
on the operator's attested reason, gated on the draft not having moved and
on the verdict's own coverage_ok. So this file now covers a rejection that
ends in a durable convergence write, not only one that advances a round.

This file covers the two new components that close it:

  * reject_review.py -- the sole writer of
    segments/{seg}.review_rejected.json. Its own refusal gate (schema-
    valid + clean:false + a non-empty --reason + the review it names via
    --expect-token/--expect-verdict-digest is the one currently on disk)
    and its durability contract are tested directly via subprocess,
    exactly like tests/review_ready.test.py's own house style for a CLI
    probe script -- a LIGHTWEIGHT fixture (scripts/reject_review.py +
    scripts/claim_record.py + schemas/review.schema.json + segments/),
    since reject_review.py touches neither node nor the prompt templates.

  * derive_next_action()'s own consumption of that artifact
    (segment_dispatch_driver.py) -- tested against the SAME heavyweight
    Phase 2 fixture tests/segment_dispatch_driver.test.py's own
    _dna_setup()/phase2_project() battery already uses (real
    mass-translate-wf.template.js, real node, for the fabricated_loc gate
    every reachable review passes through), duplicated here per this
    project's "self-contained test files, no cross-file imports" house
    convention (see tests/claim_selector.test.py's own module docstring
    for the same rule stated explicitly).

THE SEAM. Producer and consumer are two files that must agree on a wire
contract nothing else checks: the record's key NAMES, its value shapes,
and the digest algorithm on both sides. Each side's own tests pass
against its own hand-built fixture while that contract is broken, so the
seam gets ONE test that hand-builds nothing at all --
test_the_record_reject_review_actually_writes_is_the_one_the_driver_
consumes runs the REAL reject_review.py (both of its modes) as a
subprocess and feeds the file it ACTUALLY wrote to the REAL
_rejection_matches()/derive_next_action(). Every other consumer test
below hand-writes the artifact on purpose, because a legitimate producer
never emits a STALE, FORGED or PARTIAL one -- those states are reachable
only by writing them directly.

TWO BLIND SPOTS THIS FILE DELIBERATELY DESIGNS AROUND, both of which
would make a refusal assertion pass for a reason unrelated to what its
name claims:

  * _rejection_matches()'s rule 8 refuses any record that is not strictly
    NEWER than the review.json it names. So every negative case here
    stamps the mtimes explicitly (_force_record_newer_than_review(),
    _rewrite_review_preserving_mtime()) rather than relying on write
    order or on a sleep: without that, a rewrite of review.json alone
    would spend the rejection and the assertion would go green with the
    digest, the key set and the seg field never consulted at all.
  * A mutation test proves nothing without its own CONTROL. Every
    refusal below is preceded, in the same test, by the assertion that
    the UNMUTATED artifact authorizes -- so a False can only come from
    the one field that changed.
"""
import fcntl
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
TEMPLATES_SRC_DIR = ASSETS_DIR / "templates"
SCHEMAS_SRC_DIR = ASSETS_DIR / "schemas"

DRIVER_SRC = SCRIPTS_SRC_DIR / "segment_dispatch_driver.py"
SELECT_SEGMENTS_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
CLAIM_RECORD_SRC = SCRIPTS_SRC_DIR / "claim_record.py"
CODEX_JOB_SRC = SCRIPTS_SRC_DIR / "codex_job.py"
DRAFT_SHA1_SRC = SCRIPTS_SRC_DIR / "draft_sha1.py"
RESUME_SETUP_SRC = SCRIPTS_SRC_DIR / "resume_setup.py"
LEDGER_UPDATE_SRC = SCRIPTS_SRC_DIR / "ledger_update.py"
REJECT_REVIEW_SRC = SCRIPTS_SRC_DIR / "reject_review.py"
MASS_TRANSLATE_TEMPLATE_SRC = TEMPLATES_SRC_DIR / "mass-translate-wf.template.js"
REVIEW_SCHEMA_SRC = SCHEMAS_SRC_DIR / "review.schema.json"

for _src in (
    DRIVER_SRC, SELECT_SEGMENTS_SRC, LEDGER_MERGE_SRC, CLAIM_RECORD_SRC, CODEX_JOB_SRC, DRAFT_SHA1_SRC,
    RESUME_SETUP_SRC, LEDGER_UPDATE_SRC, REJECT_REVIEW_SRC, MASS_TRANSLATE_TEMPLATE_SRC, REVIEW_SCHEMA_SRC,
):
    assert _src.is_file(), f"expected script/asset not found: {_src}"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The PRODUCER, loaded once as a module purely for its own pure helper
# _review_verdict_digest() -- never to bypass the CLI, which every
# behavioural test below still drives as a real subprocess. Needed because
# --expect-verdict-digest is REQUIRED even on the paths that refuse before
# any digest is compared (a clean:true review), and the read mode that hands
# an operator the value deliberately refuses for exactly those reviews. The
# real function, not a second hashlib call spelled out here: a
# reimplementation would agree with the producer by construction and could
# not notice the two drifting apart.
REJECT_MOD = _load_module(REJECT_REVIEW_SRC, "reject_review_pure_helpers")


# ---------------------------------------------------------------------------
# Lightweight fixture -- reject_review.py's own CLI, no node/template
# dependency (mirrors tests/review_ready.test.py's make_review_ready_root).
# ---------------------------------------------------------------------------

# Minimal stand-in for claim_record.py, matching the ONE part of its
# observable contract reject_review.py actually consumes: fsync_directory()
# returns None on success or a human-readable problem string on failure.
# Which of the two it does is read from a fixture file at write time, so ONE
# invocation shape can be run twice -- once with the durability step failing
# and once with it succeeding -- and the only difference between the two runs
# is the thing under test. Staged into the durable root's OWN scripts/
# directory because these runs pass no --plugin-root, so resolve_dirs()
# self-anchors `scripts_dir` to the directory reject_review.py itself lives in
# -- which is where _import_claim_record() then loads it from BY PATH. WHICH
# copy that flag selects is a different fact, owned by
# test_the_claim_record_sibling_is_loaded_from_plugin_root_not_from_beside_the_script.
FAKE_CLAIM_RECORD_PY = """#!/usr/bin/env python3
import os
from pathlib import Path

_MARKER = Path(__file__).resolve().parent.parent / "test_fixture_fsync_problem.txt"
_CALLS = Path(__file__).resolve().parent.parent / "test_fixture_fsync_calls.txt"


def fsync_directory(directory):
    # Every call is APPENDED, so a test counts what really happened instead of
    # re-deriving what should have. The record's creation and its removal are
    # two separate directory-entry changes and each needs its own sync; only a
    # count can tell one sync from two.
    with open(_CALLS, "a", encoding="utf-8") as _fh:
        _fh.write(str(directory) + "\\n")
    try:
        problem = _MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        problem = ""
    if problem:
        return problem
    dir_fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return None
"""


def make_reject_review_root(tmp_path, name="reject_review_root", claim_record_source=None):
    """A durable root holding only what reject_review.py itself resolves.
    `claim_record_source` swaps the real sibling for FAKE_CLAIM_RECORD_PY,
    which is the only way to drive the fsync-failure branch -- a genuine
    directory fsync cannot be made to fail from a test without breaking the
    filesystem underneath the whole run."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    segments_dir = root / "segments"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    segments_dir.mkdir(parents=True)
    shutil.copy2(REJECT_REVIEW_SRC, scripts_dir / "reject_review.py")
    # json_stdout.py (#369): UNCONDITIONAL -- staged on both branches, since the
    # `claim_record_source` branch below substitutes a hand-written claim_record
    # while every other staged script still loads the helper.
    shutil.copy2(REJECT_REVIEW_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    if claim_record_source is None:
        shutil.copy2(CLAIM_RECORD_SRC, scripts_dir / "claim_record.py")
    else:
        (scripts_dir / "claim_record.py").write_text(claim_record_source, encoding="utf-8")
    shutil.copy2(REVIEW_SCHEMA_SRC, schemas_dir / "review.schema.json")
    return root


def write_review_lite(segments_dir, seg, *, clean, coverage_ok=True,
                       draft_sha1="0" * 40, dispatch_token="RUN1:seg01:r1", findings=None):
    review = {
        "clean": clean,
        "coverage_ok": coverage_ok,
        "findings": findings if findings is not None else [],
        "draft_sha1": draft_sha1,
        "dispatch_token": dispatch_token,
    }
    (segments_dir / f"{seg}.review.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return review


def run_reject_review(root, seg, *, reason=None, round_label=None, expect_token=None,
                       expect_digest=None, extra_args=()):
    argv = [sys.executable, str(root / "scripts" / "reject_review.py"), seg]
    if reason is not None:
        argv += ["--reason", reason]
    if round_label is not None:
        argv += ["--round-label", round_label]
    if expect_token is not None:
        argv += ["--expect-token", expect_token]
    if expect_digest is not None:
        argv += ["--expect-verdict-digest", expect_digest]
    argv += list(extra_args)
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


# ---------------------------------------------------------------------------
# reject_review.py's own refusal gate
# ---------------------------------------------------------------------------

def test_reject_review_refuses_on_a_clean_review(tmp_path):
    """reject_review.py's own condition 1: only a clean:false review's
    findings may be set aside. A clean:true review has no unfounded
    finding to reject in the first place -- rejecting one would be a
    silent no-op the operator could mistake for having actually done
    something.

    Every REQUIRED flag is supplied, --expect-verdict-digest included and
    carrying the review's genuine digest, so the refusal that fires is the
    clean:true one and not a missing-flag complaint standing in front of it:
    the required-flag checks necessarily precede any file read, so an
    incomplete invocation would exercise argument handling and prove nothing
    at all about the gate this test is named for."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    review = write_review_lite(root / "segments", seg, clean=True, coverage_ok=True, dispatch_token=token)

    result = run_reject_review(
        root, seg, reason="the finding is wrong", round_label="1", expect_token=token,
        expect_digest=REJECT_MOD._review_verdict_digest(review),
    )
    assert result.returncode == 1, (
        f"expected a refusal (exit 1) for a clean:true review, got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is False
    assert "clean" in payload["error"].lower(), (
        f"refusal reason must name the clean:true problem, got: {payload['error']!r}"
    )
    assert not (root / "segments" / f"{seg}.review_rejected.json").exists(), (
        "a refused rejection must never leave an artifact on disk"
    )


def test_reject_review_refuses_on_a_missing_reason(tmp_path):
    """reject_review.py's own condition 2: --reason is required and must
    be non-empty after stripping whitespace -- an unaudited rejection
    (no stated why) is exactly the gap this artifact exists to close."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    review = write_review_lite(root / "segments", seg, clean=False, coverage_ok=True, dispatch_token=token)
    digest = REJECT_MOD._review_verdict_digest(review)

    # --reason omitted entirely.
    result = run_reject_review(root, seg, round_label="1", expect_token=token, expect_digest=digest)
    assert result.returncode == 1, (
        f"expected a refusal (exit 1) for a missing --reason, got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is False
    assert "reason" in payload["error"].lower(), (
        f"refusal reason must name the missing --reason, got: {payload['error']!r}"
    )
    assert not (root / "segments" / f"{seg}.review_rejected.json").exists()

    # --reason given but blank/whitespace-only -- same refusal, not a
    # different one, and not silently accepted as "a reason was supplied".
    result = run_reject_review(root, seg, reason="   ", round_label="1", expect_token=token,
                                expect_digest=digest)
    assert result.returncode == 1
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is False
    assert "reason" in payload["error"].lower()
    assert not (root / "segments" / f"{seg}.review_rejected.json").exists()


# ---------------------------------------------------------------------------
# The remaining four authorization gates, each on its own RED path.
#
# THE SHAPE EVERY ONE OF THESE FOUR USES, and why it is not optional: each
# test satisfies EVERY OTHER gate exactly, so the invocation would SUCCEED if
# the one gate it is named for were deleted. A refusal test that leaves a
# second gate also violated is answered by whichever fires first, and it then
# stays green through the deletion of the gate it claims to cover -- which is
# how four gates came to have a producer suite that never tested one of them.
# The digest supplied is always the GENUINE digest of the review on disk,
# computed by the producer's own _review_verdict_digest(), except in the one
# test whose subject IS the digest.
# ---------------------------------------------------------------------------

def test_reject_review_refuses_a_review_that_is_not_schema_valid(tmp_path):
    """Gate 1's other half: the review must validate FULLY against
    review.schema.json, not merely parse. Every other producer test here feeds
    a schema-valid review, so nothing pinned the validation itself -- a build
    that skipped it would authorize a rejection over a document no reviewer in
    this pipeline could have produced, and whose missing fields the consumer's
    own digest would then faithfully attest.

    The review here drops `draft_sha1` (a `required` property) and is
    otherwise perfect: clean:false, a dispatch_token this test's --expect-token
    matches, a round label this test's --round-label matches, and a digest
    computed by the producer's own function OVER THIS EXACT OBJECT. Nothing
    but the schema can refuse it -- which is what makes the refusal
    attributable, and what makes deleting the validation turn this test red
    instead of leaving it green.

    draft_sha1 is also the worst field to lose: it is the review's only
    binding to the bytes it judged."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    invalid = {"clean": False, "coverage_ok": True, "findings": [], "dispatch_token": token}
    (root / "segments" / f"{seg}.review.json").write_text(
        json.dumps(invalid, ensure_ascii=False), encoding="utf-8"
    )

    result = run_reject_review(
        root, seg, reason="verified unfounded", round_label="1", expect_token=token,
        expect_digest=REJECT_MOD._review_verdict_digest(invalid),
    )
    assert result.returncode == 1, (
        f"expected a refusal (exit 1) for a schema-invalid review, got "
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is False
    assert "schema-valid" in payload["error"], (
        f"the refusal must name schema validation as the problem -- otherwise "
        f"some OTHER gate answered this test: {payload['error']!r}"
    )
    assert "draft_sha1" in payload["error"], (
        f"and it must name the field that is missing, or an operator cannot act "
        f"on it: {payload['error']!r}"
    )
    assert not (root / "segments" / f"{seg}.review_rejected.json").exists()


def test_reject_review_refuses_a_wrong_expect_token(tmp_path):
    """Gate 3: --expect-token must equal the stored review's own
    dispatch_token EXACTLY -- "the review being rejected is the one currently
    on disk".

    The wrong token names round 2 of the same run and segment: well-formed,
    mintable by review_dispatch_token(), and exactly what an operator holds
    after the segment has moved on since they read it. --round-label is "1",
    agreeing with the review ON DISK rather than with the token being passed:
    the label gate is checked against the stored review, so passing "2" here
    would let the LABEL gate answer this test and it would stay green with the
    token gate deleted."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    stored_token = "RUN1:seg01:r1"
    wrong_token = "RUN1:seg01:r2"
    review = write_review_lite(root / "segments", seg, clean=False, coverage_ok=True,
                                dispatch_token=stored_token,
                                findings=[{"loc": "p1:1", "severity": "major",
                                           "issue": "x", "suggest": "y"}])

    result = run_reject_review(
        root, seg, reason="verified unfounded", round_label="1", expect_token=wrong_token,
        expect_digest=REJECT_MOD._review_verdict_digest(review),
    )
    assert result.returncode == 1, (
        f"expected a refusal (exit 1) for a wrong --expect-token, got "
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is False
    assert stored_token in payload["error"] and wrong_token in payload["error"], (
        f"the refusal must name BOTH tokens -- which one is stale decides the "
        f"operator's next move: {payload['error']!r}"
    )
    assert not (root / "segments" / f"{seg}.review_rejected.json").exists()


def test_reject_review_refuses_a_wrong_expect_verdict_digest(tmp_path):
    """Gate 4: --expect-verdict-digest must equal the digest of the review as
    it stands NOW. This is the gate the token cannot stand in for --
    review_dispatch_token() is a pure function of (run_id, seg, round_label),
    so a re-dispatched review inside the same round arrives under the
    IDENTICAL token carrying a verdict nobody read.

    The wrong digest is a PLAUSIBLE one, not an impossible constant: it is the
    genuine digest of the same review with its one finding reworded -- exactly
    the "the verdict changed under the token you named" shape this gate exists
    for. It is 64 lowercase hex by construction (asserted), so the FORMAT
    check cannot be what refuses; only the comparison can. --expect-token and
    --round-label both match the review on disk, so with this gate deleted the
    run succeeds."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    review = write_review_lite(root / "segments", seg, clean=False, coverage_ok=True,
                                dispatch_token=token,
                                findings=[{"loc": "p1:1", "severity": "major",
                                           "issue": "the source reads 'X' here",
                                           "suggest": "restore 'X'"}])
    genuine_digest = REJECT_MOD._review_verdict_digest(review)
    replaced = json.loads(json.dumps(review))
    replaced["findings"][0]["issue"] = "the source reads 'Y' here"
    wrong_digest = REJECT_MOD._review_verdict_digest(replaced)
    assert wrong_digest != genuine_digest
    assert len(wrong_digest) == 64 and all(c in "0123456789abcdef" for c in wrong_digest), (
        "the wrong digest must be well-formed, or the FORMAT check answers this "
        "test instead of the comparison it is named for"
    )

    result = run_reject_review(
        root, seg, reason="verified unfounded", round_label="1", expect_token=token,
        expect_digest=wrong_digest,
    )
    assert result.returncode == 1, (
        f"expected a refusal (exit 1) for a wrong --expect-verdict-digest, got "
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is False
    assert genuine_digest in payload["error"] and wrong_digest in payload["error"], (
        f"the refusal must name BOTH digests: {payload['error']!r}"
    )
    assert "--print-verdict-digest" in payload["error"], (
        f"and it must name the command that yields the right pair -- a required "
        f"flag over a private hash function whose refusal names no remedy is an "
        f"unusable tool: {payload['error']!r}"
    )
    assert not (root / "segments" / f"{seg}.review_rejected.json").exists()


def test_reject_review_refuses_a_round_label_the_stored_token_does_not_carry(tmp_path):
    """Gate 5: --round-label must AGREE with the label the stored review's own
    dispatch_token encodes. The field is audit-only in EFFECT -- the consumer
    never reads it back -- which is precisely why it must not be audit-only in
    TRUTH: an unchecked field an operator types freely is wrong exactly when
    it matters, and the record would then attest a round the review it names
    never belonged to.

    --expect-token and --expect-verdict-digest both match the review on disk,
    so the label is the only thing left that can refuse."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    review = write_review_lite(root / "segments", seg, clean=False, coverage_ok=True,
                                dispatch_token=token,
                                findings=[{"loc": "p1:1", "severity": "major",
                                           "issue": "x", "suggest": "y"}])

    result = run_reject_review(
        root, seg, reason="verified unfounded", round_label="2", expect_token=token,
        expect_digest=REJECT_MOD._review_verdict_digest(review),
    )
    assert result.returncode == 1, (
        f"expected a refusal (exit 1) for a --round-label the stored token does "
        f"not carry, got rc={result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is False
    assert "--round-label" in payload["error"], (
        f"the refusal must name the flag that disagrees: {payload['error']!r}"
    )
    assert "'2'" in payload["error"] and "'1'" in payload["error"], (
        f"and it must name BOTH labels -- the one passed and the one the token "
        f"carries -- so the operator can tell which of the two is wrong: "
        f"{payload['error']!r}"
    )
    assert not (root / "segments" / f"{seg}.review_rejected.json").exists()


def test_reject_review_leaves_no_record_behind_when_the_directory_fsync_fails(tmp_path):
    """Producer rule 1 of the #461 contract: a failed durability step must
    NOT leave a live authorization. The record is published with
    os.replace() BEFORE fsync_directory() is called, so a failure there
    finds a fully-formed rejection already at its final path -- and the
    reasoning that once argued for keeping it (a reader that finds no
    rejection falls back to needs_fix, therefore keeping it is fail-closed)
    is a statement about the READER, not about the record. What is at stake
    here is an AUTHORIZATION the operator was TOLD had failed, sitting live
    on disk, ready to advance an unchanged draft on the driver's next pass
    with nobody watching.

    Two runs of the IDENTICAL command, differing only in whether the staged
    claim_record.py's fsync_directory() reports a problem -- the failure
    case alone would be satisfied by a producer that never got as far as
    writing anything, and would then prove nothing about the unlink."""
    root = make_reject_review_root(tmp_path, claim_record_source=FAKE_CLAIM_RECORD_PY)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    review = write_review_lite(root / "segments", seg, clean=False, coverage_ok=True,
                                dispatch_token=token,
                                findings=[{"loc": "p1:1", "severity": "major",
                                           "issue": "x", "suggest": "y"}])
    digest = REJECT_MOD._review_verdict_digest(review)
    rejection_path = root / "segments" / f"{seg}.review_rejected.json"
    invocation = dict(reason="verified unfounded", round_label="1",
                      expect_token=token, expect_digest=digest)

    (root / "test_fixture_fsync_problem.txt").write_text(
        "simulated: the segments directory entry could not be made durable\n", encoding="utf-8"
    )
    failed = run_reject_review(root, seg, **invocation)
    assert failed.returncode == 1, (
        f"a rejection whose durability step failed must REPORT failure, got "
        f"rc={failed.returncode}\nstdout:\n{failed.stdout}\nstderr:\n{failed.stderr}"
    )
    payload = json.loads(failed.stdout.strip())
    assert payload["success"] is False
    assert "durable" in payload["error"].lower(), (
        f"the refusal must name the durability failure so the operator knows "
        f"the rejection did not take effect, got: {payload['error']!r}"
    )
    assert not rejection_path.exists(), (
        "the record was published by os.replace() before the fsync -- it must be "
        "UNLINKED again, not left behind as a live authorization this command "
        "reported as failed"
    )
    # The lock FILE is the one permitted leftover, and it is enumerated rather
    # than filtered out by a pattern: it carries no state, its presence
    # authorizes nothing, and the flock that does mean something lives on a
    # descriptor the kernel dropped when this process exited. Anything ELSE
    # here -- a temp file, a partial record -- is a real leak, which is why
    # this compares an exact list instead of asserting "no record".
    leftovers = sorted(p.name for p in (root / "segments").iterdir()
                       if p.name != f"{seg}.review.json")
    assert leftovers == [f".reject_review.{seg}.lock"], (
        f"a failed rejection may leave the (stateless) lock file and nothing "
        f"else behind, found {leftovers}"
    )

    # The control: the SAME invocation, with the durability step succeeding.
    # Without it, "no record on disk" is equally consistent with a producer
    # that refused long before it ever wrote one.
    (root / "test_fixture_fsync_problem.txt").write_text("", encoding="utf-8")
    ok = run_reject_review(root, seg, **invocation)
    assert ok.returncode == 0, (
        f"the identical invocation must SUCCEED once the directory fsync does, "
        f"otherwise the failure case above proves nothing about the fsync\n"
        f"stdout:\n{ok.stdout}\nstderr:\n{ok.stderr}"
    )
    assert json.loads(ok.stdout.strip())["success"] is True
    assert rejection_path.is_file(), "the successful run really does publish the record"


# ---------------------------------------------------------------------------
# Which claim_record.py actually gets EXECUTED, and what the record's temp
# file may collide with -- the two supply-chain facts about this producer.
# ---------------------------------------------------------------------------

# Two interchangeable stand-ins for claim_record.py, differing ONLY in the
# marker file their fsync_directory() leaves behind. Both return None, so
# either one lets the run succeed and the sole observable difference between
# them is WHICH FILE THE PROCESS REALLY RAN -- evidence that cannot be
# produced by reading reject_review.py's internals, which is the point: the
# question is not what the import statement says, it is what got executed.
# The marker lands in the durable root (the parent of the segments/ directory
# fsync_directory() is handed), so both copies write to the same place under
# the same name-modulo-identity rule.
MARKER_CLAIM_RECORD_PY = '''#!/usr/bin/env python3
from pathlib import Path

_IDENTITY = "%s"


def fsync_directory(directory):
    marker = Path(directory).parent / ("test_fixture_fsync_ran_from_" + _IDENTITY + ".txt")
    marker.write_text(_IDENTITY + "\\n", encoding="utf-8")
    return None
'''


def make_plugin_root_with_claim_record(tmp_path, identity="plugin_root", name="plugin_root"):
    """A plugin install root shaped exactly as --plugin-root's own contract
    describes it: the sibling is looked for at {PATH}/assets/scripts/
    claim_record.py (see reject_review.py's resolve_dirs())."""
    scripts_dir = tmp_path / name / "assets" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "claim_record.py").write_text(MARKER_CLAIM_RECORD_PY % identity, encoding="utf-8")
    return tmp_path / name


def test_the_claim_record_sibling_is_loaded_from_plugin_root_not_from_beside_the_script(tmp_path):
    """--plugin-root DECIDES WHICH claim_record.py IS EXECUTED, and the
    directory beside the running script does not get a vote.

    reject_review.py is a directly-run script, so sys.path[0] is its OWN
    directory -- and in production that directory is ${durable_root}/scripts/,
    a Step-0a copy other passes in this pipeline hold write access over (the
    glossary and skeptic codex passes, the manual W5 drive). A bare
    `import claim_record` therefore resolves to a file the very population
    this record is defended against can rewrite: --plugin-root would be inert
    in exactly the deployment it exists for, and a poisoned sibling would be
    imported AND EXECUTED, its no-op fsync_directory() reporting a record
    durable that never was. The fix loads scripts_dir/"claim_record.py" by
    path unconditionally, where scripts_dir comes from --plugin-root.

    Told apart by OBSERVABLE EFFECT rather than by inspecting the loaded
    module: each stand-in's fsync_directory() writes a differently-named
    marker and returns None, so both runs succeed and the only thing the two
    can be distinguished by is the marker that appeared.

    The self-anchored CONTROL runs first and on purpose: without it, "the
    sibling's marker is absent" would be equally consistent with a fake that
    never worked at all, and the real assertion below would be vacuous."""
    root = make_reject_review_root(
        tmp_path, claim_record_source=MARKER_CLAIM_RECORD_PY % "durable_sibling"
    )
    plugin_root = make_plugin_root_with_claim_record(tmp_path)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    review = write_review_lite(root / "segments", seg, clean=False, coverage_ok=True,
                                dispatch_token=token,
                                findings=[{"loc": "p1:1", "severity": "major",
                                           "issue": "x", "suggest": "y"}])
    invocation = dict(reason="verified unfounded", round_label="1", expect_token=token,
                      expect_digest=REJECT_MOD._review_verdict_digest(review))
    sibling_marker = root / "test_fixture_fsync_ran_from_durable_sibling.txt"
    plugin_marker = root / "test_fixture_fsync_ran_from_plugin_root.txt"
    rejection_path = root / "segments" / f"{seg}.review_rejected.json"

    # CONTROL -- no --plugin-root, so scripts_dir self-anchors to the script's
    # own directory and the sibling staged there IS the copy that runs.
    control = run_reject_review(root, seg, **invocation)
    assert control.returncode == 0, (
        f"the self-anchored control must succeed, got rc={control.returncode}\n"
        f"stdout:\n{control.stdout}\nstderr:\n{control.stderr}"
    )
    assert sibling_marker.is_file(), (
        "the sibling stand-in must really run when nothing redirects the lookup "
        "-- otherwise its absence below proves nothing"
    )
    assert not plugin_marker.exists()

    # Reset to a state where the write path (and therefore the sibling import)
    # is reached again: gate 6 would otherwise short-circuit the identical
    # re-run as an idempotent no-op SUCCESS that imports nothing at all, and
    # the assertion below would go green with no import having happened.
    rejection_path.unlink()
    sibling_marker.unlink()

    result = run_reject_review(root, seg, **invocation,
                               extra_args=("--plugin-root", str(plugin_root)))
    assert result.returncode == 0, (
        f"the --plugin-root run must succeed, got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert json.loads(result.stdout.strip())["success"] is True
    assert plugin_marker.is_file(), (
        f"--plugin-root must decide which claim_record.py is executed: the copy "
        f"at {plugin_root / 'assets' / 'scripts' / 'claim_record.py'} is the one "
        f"whose fsync_directory() must have run"
    )
    assert not sibling_marker.exists(), (
        "the copy sitting beside the running script must NOT have been executed "
        "-- that directory is writable by other passes in this pipeline, so a "
        "bare `import claim_record` reached by sys.path[0] would execute a "
        "poisoned sibling with --plugin-root inert"
    )
    assert rejection_path.is_file(), "and the record itself is still published"


# Plants a symlink at the temp-file name reject_review.py used BEFORE the fix
# -- `.{seg}.review_rejected.json.tmp.{pid}` -- and then BECOMES the real
# script via os.execv(), which preserves this process's pid. That is what
# makes the decoy EXACT rather than a guess sprayed across a range of
# candidate pids: the pid the pre-fix code would have formatted into the name
# is, by construction, this one. The script that runs after the exec is the
# real CLI with the real argv, self-anchoring exactly as it does in every
# other test here -- nothing about reject_review.py is stubbed or patched.
# The planted name is written to a file rather than printed, because stdout
# belongs to the exec'd script and must stay one JSON object.
OLD_NAME_DECOY_RUNNER_PY = '''#!/usr/bin/env python3
import os
import sys

segments_dir, seg, target, name_out, script = sys.argv[1:6]
decoy = os.path.join(segments_dir, ".%s.review_rejected.json.tmp.%d" % (seg, os.getpid()))
os.symlink(target, decoy)
with open(name_out, "w", encoding="utf-8") as fh:
    fh.write(decoy)
os.execv(sys.executable, [sys.executable, script] + sys.argv[6:])
'''


def run_reject_review_behind_an_old_name_decoy(root, seg, *, decoy_target, reason, round_label,
                                                expect_token, expect_digest):
    """Run the real reject_review.py CLI with a symlink already sitting at the
    pre-fix temp-file name for THAT run's own pid. Returns
    `(CompletedProcess, planted_decoy_path)`."""
    runner = root.parent / "old_name_decoy_runner.py"
    runner.write_text(OLD_NAME_DECOY_RUNNER_PY, encoding="utf-8")
    name_out = root.parent / "planted_decoy_name.txt"
    result = subprocess.run(
        [
            sys.executable, str(runner),
            str(root / "segments"), seg, str(decoy_target), str(name_out),
            str(root / "scripts" / "reject_review.py"),
            seg, "--reason", reason, "--round-label", round_label,
            "--expect-token", expect_token, "--expect-verdict-digest", expect_digest,
        ],
        capture_output=True, text=True, timeout=30,
    )
    return result, Path(name_out.read_text(encoding="utf-8").strip())


def test_the_record_is_never_written_through_a_symlink_planted_at_the_old_temp_name(tmp_path):
    """The record's temp file, pinned by the PROPERTIES that close the hole.

    THE HOLE. The temp file was `open(path.parent / f".{path.name}.tmp.
    {os.getpid()}", "wb")` -- a predictable name, no O_EXCL, no O_NOFOLLOW.
    Anything that can write in segments/ (the very population this
    authorization is defended against) could pre-plant a symlink at that name
    pointing anywhere: the record's bytes would truncate the LINK TARGET,
    possibly a hand-corrected translation outside the durable root, and
    os.replace() would then move the SYMLINK onto the record path -- after
    which the consumer's O_NOFOLLOW refuses it. The operator is told the
    rejection succeeded, nothing is authorized, and an unrelated file is
    destroyed. Now: os.open(tmp, O_CREAT|O_EXCL|O_WRONLY, 0o644) with a random
    suffix.

    WHAT EACH ASSERTION PINS:

      (b)+(c), on an unattacked run -- the record path is a REGULAR FILE
      (never a symlink) whose content is the record, and segments/ holds no
      `.tmp.` leftover afterwards. The clean run owns these because the
      attacked run below deliberately leaves a `.tmp.` entry of the TEST's
      own making in that directory.

      (a), on the attacked run -- a decoy symlink is planted at the exact
      pre-fix name for the run's own pid (see OLD_NAME_DECOY_RUNNER_PY: the
      planting process os.execv()s into the real script, so the pid is not
      guessed), and afterwards the decoy's TARGET is byte-unchanged, the
      decoy is still a symlink where the test left it (it was neither
      followed nor consumed by os.replace()), the record path is a regular
      file and not a symlink, and the only `.tmp.` entry in segments/ is the
      test's own decoy -- the run added none of its own.

    WHAT THIS TEST DOES NOT PROVE, and where that is now closed. The fixed
    temp path is `.{name}.tmp.{pid}.{os.urandom(6).hex()}`, and nothing HERE
    pre-creates a file at the path the run will actually use, so NO ASSERTION
    IN THIS TEST EXERCISES O_EXCL: a build that only randomized the name while
    keeping a plain truncating open() passes this one. What is pinned here is
    the property the exploit needed -- the old, guessable name is no longer
    used, and a pre-existing entry at it is neither followed nor renamed onto
    the record path. The exclusive-create flag itself is pinned separately, by
    test_the_records_temp_file_refuses_any_entry_planted_at_its_exact_pinned_name,
    which makes the random half deterministic from outside the process."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    review = write_review_lite(root / "segments", seg, clean=False, coverage_ok=True,
                                dispatch_token=token,
                                findings=[{"loc": "p1:1", "severity": "major",
                                           "issue": "x", "suggest": "y"}])
    digest = REJECT_MOD._review_verdict_digest(review)
    reason = "verified: the claimed source string occurs zero times in block p1"
    invocation = dict(reason=reason, round_label="1", expect_token=token, expect_digest=digest)
    segments_dir = root / "segments"
    rejection_path = segments_dir / f"{seg}.review_rejected.json"

    # (b) + (c): the unattacked run.
    clean_run = run_reject_review(root, seg, **invocation)
    assert clean_run.returncode == 0, (
        f"the unattacked run must succeed, got rc={clean_run.returncode}\n"
        f"stdout:\n{clean_run.stdout}\nstderr:\n{clean_run.stderr}"
    )
    assert json.loads(clean_run.stdout.strip())["success"] is True
    assert not rejection_path.is_symlink(), "(b) the record path must be a regular file"
    assert rejection_path.is_file()
    record = json.loads(rejection_path.read_text(encoding="utf-8"))
    assert record["reason"] == reason
    assert record["dispatch_token"] == token
    assert record["verdict_digest"] == digest
    leftovers = sorted(p.name for p in segments_dir.iterdir() if ".tmp." in p.name)
    assert leftovers == [], f"(c) a successful run must leave no temp file behind, found {leftovers}"

    # (a): the attacked run. The record is removed first so the write path is
    # reached again rather than short-circuited by gate 6's idempotent no-op,
    # which would never open a temp file at all.
    rejection_path.unlink()
    decoy_target = root / "hand_corrected_translation_outside_segments.txt"
    sentinel = "PRECIOUS: a file no rejection record may ever truncate\n"
    decoy_target.write_text(sentinel, encoding="utf-8")

    attacked, decoy_path = run_reject_review_behind_an_old_name_decoy(
        root, seg, decoy_target=decoy_target, **invocation
    )
    assert attacked.returncode == 0, (
        f"the rejection must still succeed with the decoy in place, got rc="
        f"{attacked.returncode}\nstdout:\n{attacked.stdout}\nstderr:\n{attacked.stderr}"
    )
    assert json.loads(attacked.stdout.strip())["success"] is True
    assert decoy_path.parent == segments_dir, (
        f"the decoy must have been planted in segments/, got {decoy_path}"
    )
    decoy_pid = decoy_path.name[len(f".{seg}.review_rejected.json.tmp."):]
    assert decoy_path.name.startswith(f".{seg}.review_rejected.json.tmp.") and decoy_pid.isdigit(), (
        f"the decoy must carry the PRE-FIX name shape exactly -- a bare pid, no "
        f"random suffix -- or it is not the name the old code would have used; "
        f"got {decoy_path.name!r}"
    )

    assert decoy_target.read_text(encoding="utf-8") == sentinel, (
        "(a) the record's bytes must never reach a symlink's target: the pre-fix "
        "open() on this exact name would have truncated this file and written the "
        "record into it"
    )
    assert decoy_path.is_symlink(), (
        "(a) the decoy must still be sitting where the test planted it -- the "
        "pre-fix os.replace() consumed it, renaming the SYMLINK onto the record path"
    )
    assert os.readlink(decoy_path) == str(decoy_target)
    assert not rejection_path.is_symlink(), (
        "(b) under attack: the published record must be a regular file, not the "
        "planted symlink renamed into place"
    )
    assert rejection_path.is_file()
    assert json.loads(rejection_path.read_text(encoding="utf-8"))["reason"] == reason
    temps = sorted(p.name for p in segments_dir.iterdir() if ".tmp." in p.name)
    assert temps == [decoy_path.name], (
        f"(c) under attack: the only .tmp. entry left in segments/ must be the "
        f"test's own decoy -- the run must add none of its own, found {temps}"
    )


# Makes the temp name's ONE unpredictable half deterministic FROM OUTSIDE the
# process, with no test hook anywhere in production code: `os.urandom` is
# rebound to a constant in this wrapper, and the real script is then executed
# in the SAME process by runpy under `__main__`, so write_rejection_record()'s
# `os.urandom(6).hex()` resolves through the patched module. The pid half is
# free -- the wrapper IS the process the script runs in. Both halves known,
# the wrapper computes the exact path the run will open and plants the decoy
# there before handing control over.
#
# Why runpy and not the execv trick used above: execv would REPLACE this
# interpreter and take the patched os.urandom with it. Staying in-process is
# what keeps the patch alive; `run_name="__main__"` is what still makes the
# script's own `if __name__ == "__main__"` block run, so the CLI, its argv,
# its self-anchoring off __file__ and its exit code are all the real ones.
OEXCL_TEMP_NAME_RUNNER_PY = '''#!/usr/bin/env python3
import os
import runpy
import sys

segments_dir, seg, target, name_out, kind, script = sys.argv[1:7]


def _fixed_urandom(n, _c=b"\\xab\\xcd\\xef\\x01\\x23\\x45"):
    return (_c * (n // len(_c) + 1))[:n]


os.urandom = _fixed_urandom

tmp_path = os.path.join(segments_dir, ".%s.review_rejected.json.tmp.%d.%s" % (
    seg, os.getpid(), _fixed_urandom(6).hex(),
))
if kind == "symlink":
    os.symlink(target, tmp_path)
elif kind == "regular":
    # A PLAIN FILE, not a link. O_NOFOLLOW would happily create through this
    # one; only O_EXCL refuses it. That is the discrimination this parameter
    # exists to make.
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(open(target, encoding="utf-8").read())
else:
    raise SystemExit("unknown decoy kind %r" % (kind,))
with open(name_out, "w", encoding="utf-8") as fh:
    fh.write(tmp_path)

sys.argv = [script] + sys.argv[7:]
runpy.run_path(script, run_name="__main__")
'''

PINNED_TEMP_SUFFIX = "abcdef012345"  # _fixed_urandom(6).hex() above


def run_reject_review_behind_a_pinned_temp_name_decoy(root, seg, *, decoy_target, reason,
                                                       round_label, expect_token, expect_digest,
                                                       kind="symlink"):
    """Run the real reject_review.py CLI with an entry already occupying the
    EXACT temp path that run will try to create. `kind` selects what occupies
    it: a "symlink" pointing at `decoy_target`, or a "regular" file holding a
    copy of its bytes. Returns `(CompletedProcess, planted_path)`."""
    runner = root.parent / "oexcl_temp_name_runner.py"
    runner.write_text(OEXCL_TEMP_NAME_RUNNER_PY, encoding="utf-8")
    name_out = root.parent / f"pinned_temp_name.{kind}.txt"
    result = subprocess.run(
        [
            sys.executable, str(runner),
            str(root / "segments"), seg, str(decoy_target), str(name_out), kind,
            str(root / "scripts" / "reject_review.py"),
            seg, "--reason", reason, "--round-label", round_label,
            "--expect-token", expect_token, "--expect-verdict-digest", expect_digest,
        ],
        capture_output=True, text=True, timeout=30,
    )
    return result, Path(name_out.read_text(encoding="utf-8").strip())


def test_the_records_temp_file_refuses_any_entry_planted_at_its_exact_pinned_name(tmp_path):
    """O_EXCL ITSELF -- the residual the pid-decoy test above states it cannot
    close, closed here.

    WHY IT NEEDED CLOSING. Keep the random suffix and swap
    `os.open(..., O_CREAT|O_EXCL|O_WRONLY, 0o644)` for a plain truncating
    `open(tmp_path, "wb")`, and the pid-decoy test stays GREEN -- it only ever
    plants the obsolete pid-only name. The randomness makes the attack harder
    to AIM; only the exclusive create makes it refuse. Those are different
    properties and a test that cannot tell them apart pins the weaker one.

    HOW THE NAME IS PINNED, without a test hook in production code: the
    wrapper rebinds `os.urandom` to a constant and runs the real script
    IN-PROCESS via runpy under `__main__`, so both halves of the suffix (pid,
    random) are known to the planter. `reject_review.py` is untouched -- the
    determinism comes entirely from the process it is executed in.

    WHAT EACH ASSERTION PINS:
      * exit 1 / success:false -- the exclusive create REFUSED a pre-existing
        entry at its own path. This is the assertion the plain-open mutant
        fails, and it is the whole point of the test.
      * the error names that exact path -- proof the production code really
        did build the name the decoy occupies, i.e. that the pinning worked
        rather than the run failing for some unrelated reason.
      * the sentinel is byte-unchanged -- no bytes were written THROUGH the
        symlink.
      * the planted symlink is STILL THERE, still pointing where it pointed.
        This is new, deliberate behaviour and not an oversight: the failing
        open is what would have CREATED that path, so whatever occupies it is
        not this process's to delete. Removing it would erase the attack's
        only trace and hand the next attempt a clean path.
      * no record was published.

    WHAT IT STILL DOES NOT PROVE: behaviour under a genuine concurrent race
    between two real processes. The collision here is staged, not raced -- the
    flag is what is under test, not the scheduler."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    review = write_review_lite(root / "segments", seg, clean=False, coverage_ok=True,
                                dispatch_token=token,
                                findings=[{"loc": "p1:1", "severity": "major",
                                           "issue": "x", "suggest": "y"}])
    segments_dir = root / "segments"
    rejection_path = segments_dir / f"{seg}.review_rejected.json"
    sentinel = "PRECIOUS: a file no rejection record may ever truncate\n"
    decoy_target = root / "hand_corrected_translation_outside_segments.txt"
    decoy_target.write_text(sentinel, encoding="utf-8")

    result, planted = run_reject_review_behind_a_pinned_temp_name_decoy(
        root, seg, decoy_target=decoy_target, reason="verified unfounded",
        round_label="1", expect_token=token,
        expect_digest=REJECT_MOD._review_verdict_digest(review),
    )
    assert planted.name.endswith(f".{PINNED_TEMP_SUFFIX}"), (
        f"the wrapper must have planted the decoy at the PINNED suffix, or the "
        f"name was never deterministic; got {planted.name!r}"
    )

    assert result.returncode == 1, (
        f"the exclusive create must REFUSE a pre-existing entry at its own temp "
        f"path -- a plain truncating open() would have written through it and "
        f"reported success. got rc={result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is False
    assert str(planted) in payload["error"], (
        f"the refusal must name the exact temp path -- that is what proves the "
        f"production code built the name this decoy occupies, rather than the "
        f"run having failed for an unrelated reason: {payload['error']!r}"
    )
    assert decoy_target.read_text(encoding="utf-8") == sentinel, (
        "no byte may reach the symlink's target: the whole exploit is that the "
        "record's content lands in a file outside segments/"
    )
    assert planted.is_symlink(), (
        "the planted entry must be LEFT IN PLACE -- the failing open is what "
        "would have created that path, so what occupies it is not this "
        "process's to delete, and unlinking it would erase the attack's only "
        "trace and hand the next attempt a clean path"
    )
    assert os.readlink(str(planted)) == str(decoy_target)
    assert not rejection_path.exists(), (
        "and nothing may be published when the temp file could not be created"
    )

    # A PLAIN FILE AT THE SAME PINNED PATH, because a symlink alone cannot
    # tell O_EXCL from O_NOFOLLOW. Swap the exclusive create for
    # `O_CREAT|O_NOFOLLOW|O_WRONLY|O_TRUNC` and every assertion above still
    # passes -- the symlink open fails with ELOOP, so rc is 1, the path is
    # named, the target is untouched and the link survives. That mutant then
    # truncates and publishes through a REGULAR file at the same path. Only
    # this second half separates the two flags.
    plain_target = root / "hand_corrected_translation_plain.txt"
    plain_target.write_text(sentinel, encoding="utf-8")
    plain_result, plain_planted = run_reject_review_behind_a_pinned_temp_name_decoy(
        root, seg, decoy_target=plain_target, reason="verified unfounded",
        round_label="1", expect_token=token,
        expect_digest=REJECT_MOD._review_verdict_digest(review), kind="regular",
    )
    # The refusal is asserted FIRST, before anything about the decoy's own
    # state: under the O_NOFOLLOW mutant the run SUCCEEDS, and success is what
    # then renames the decoy away, so a fixture self-check placed ahead of this
    # would fire first and report a missing decoy instead of the real defect.
    assert plain_result.returncode == 1, (
        f"the exclusive create must refuse a pre-existing REGULAR file too -- "
        f"this is the assertion an O_NOFOLLOW-only mutant fails while every "
        f"symlink assertion above still passes. got rc={plain_result.returncode}"
        f"\nstdout:\n{plain_result.stdout}\nstderr:\n{plain_result.stderr}"
    )
    assert json.loads(plain_result.stdout.strip())["success"] is False
    assert not plain_planted.is_symlink(), (
        f"the second decoy must NOT be a symlink, or it tests the same thing "
        f"the first one did; got {plain_planted!r}"
    )
    assert plain_planted.is_file() and plain_planted.read_text(encoding="utf-8") == sentinel, (
        "the plain decoy must survive untouched -- a truncating open would "
        "have replaced its bytes with the rejection record, and a successful "
        "run would have renamed it away entirely"
    )
    assert not rejection_path.exists()


def test_a_concurrent_rejection_cannot_overwrite_a_colleagues_record(tmp_path):
    """THE CONFLICT GATE'S PROMISE IS ONLY TRUE IF IT IS SERIALISED.

    Gate 6 READS the record on disk, decides from it, and publishes later with
    an unconditional os.replace(). Without a lock spanning those steps, two
    operators rejecting the same verdict with DIFFERENT reasons both see an
    absent record, both pass the gate, and the second one's replace silently
    erases the first one's -- while the gate's own refusal message promises
    the opposite: that nothing here can tell a deliberate correction from one
    operator replacing a colleague's audit trail, so it refuses rather than
    overwrite.

    DRIVEN BY HOLDING THE REAL LOCK FROM THIS TEST, not by racing two
    subprocesses and hoping the interleaving lands. A race that reproduces
    "usually" is a test that fails "sometimes"; taking the exact lock the
    production code takes makes the contended path deterministic. What this
    pins is that the critical section is entered at all and that contention
    refuses cleanly -- an implementation with no lock sails straight through
    and publishes.

    THE CONTROL is the same command with the lock released: it must succeed.
    Otherwise the refusal is consistent with a gate that refuses in any state.

    WHAT IT DOES NOT PIN: that the section's BOUNDARIES are exactly right --
    only that a second writer cannot enter while one holds it. A genuine
    two-process interleaving is not reproducible here by construction."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    review = write_review_lite(root / "segments", seg, clean=False, coverage_ok=True,
                                dispatch_token=token,
                                findings=[{"loc": "p1:1", "severity": "major",
                                           "issue": "x", "suggest": "y"}])
    digest = REJECT_MOD._review_verdict_digest(review)
    rejection_path = root / "segments" / f"{seg}.review_rejected.json"
    lock_path = root / "segments" / f".reject_review.{seg}.lock"

    # OPERATOR A publishes first, normally.
    first = run_reject_review(root, seg, reason="A: verified unfounded against the source",
                              round_label="1", expect_token=token, expect_digest=digest)
    assert first.returncode == 0, first.stdout + first.stderr
    original = rejection_path.read_bytes()

    # OPERATOR A's process is still inside the critical section: this test now
    # holds the very lock reject_review.py takes.
    held_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(held_fd, fcntl.LOCK_EX)
    try:
        second = run_reject_review(root, seg, reason="B: a different reason entirely",
                                   round_label="1", expect_token=token, expect_digest=digest,
                                   extra_args=())
        assert second.returncode == 1, (
            f"a rejection that cannot take the lock must refuse -- without the "
            f"critical section it would read, pass the conflict gate and "
            f"replace operator A's record. got rc={second.returncode}\n"
            f"stdout:\n{second.stdout}"
        )
        payload = json.loads(second.stdout.strip())
        assert payload["success"] is False
        assert str(lock_path) in payload["error"], (
            f"the refusal must name the lock, or an operator cannot tell this "
            f"from any other failure: {payload['error']!r}"
        )
        assert rejection_path.read_bytes() == original, (
            "and operator A's record must be byte-identical -- erasing it is "
            "the exact outcome the conflict gate promises cannot happen"
        )
    finally:
        fcntl.flock(held_fd, fcntl.LOCK_UN)
        os.close(held_fd)

    # CONTROL -- lock released, the SAME command now reaches the conflict gate
    # and refuses there instead, on the different reason.
    third = run_reject_review(root, seg, reason="B: a different reason entirely",
                              round_label="1", expect_token=token, expect_digest=digest)
    assert third.returncode == 1
    third_payload = json.loads(third.stdout.strip())
    assert "DIFFERENT reason" in third_payload["error"], (
        f"once the lock is free the CONFLICT gate must be what answers, not the "
        f"lock -- otherwise the refusal above was not about contention: "
        f"{third_payload['error']!r}"
    )
    assert rejection_path.read_bytes() == original


def test_the_lock_path_refuses_a_planted_symlink_and_a_planted_fifo(tmp_path):
    """THE LOCK PATH HAS THE RECORD'S PROVENANCE PROBLEM, and needs the record's
    defence.

    `.reject_review.<seg>.lock` is predictable and lives in `segments/` -- the
    directory this whole artifact's threat model says other processes can
    write. Opened with plain `O_CREAT|O_RDWR`, a planted
    `.reject_review.<seg>.lock -> /outside/target` is FOLLOWED, and O_CREAT
    then creates that external file with the operator's privileges: this
    command reaching outside the durable root, which is exactly the boundary
    the temp-record write already defends. The second half is subtler -- a
    symlink to a DIFFERENT lock inode turns serialisation advertised as
    per-path into serialisation on whatever the link names.

    TWO ENTRY KINDS, because they fail through different mechanisms and one
    does not imply the other: O_NOFOLLOW is what refuses the symlink (ELOOP at
    open), while a FIFO opens fine and is refused by the S_ISREG test on the
    DESCRIPTOR -- with O_NONBLOCK the only reason the command refuses in
    milliseconds instead of blocking forever on a reader that never comes.

    THE CONTROL is the same command with nothing planted: it must succeed, so
    neither refusal can be a gate that refuses in any state."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    review = write_review_lite(root / "segments", seg, clean=False, coverage_ok=True,
                                dispatch_token=token,
                                findings=[{"loc": "p1:1", "severity": "major",
                                           "issue": "x", "suggest": "y"}])
    invocation = dict(reason="verified unfounded", round_label="1", expect_token=token,
                      expect_digest=REJECT_MOD._review_verdict_digest(review))
    lock_path = root / "segments" / f".reject_review.{seg}.lock"
    rejection_path = root / "segments" / f"{seg}.review_rejected.json"

    # 1. SYMLINK pointing OUTSIDE the durable root, at a path that does not
    #    exist yet -- so "was it created?" answers whether the open followed.
    outside = tmp_path / "outside_the_durable_root.txt"
    assert not outside.exists()
    os.symlink(str(outside), str(lock_path))
    linked = run_reject_review(root, seg, **invocation)
    assert linked.returncode == 1, (
        f"a symlink at the lock path must REFUSE, got rc={linked.returncode}\n"
        f"stdout:\n{linked.stdout}"
    )
    assert json.loads(linked.stdout.strip())["success"] is False
    assert not outside.exists(), (
        "and O_CREAT must not have followed the link: creating that file is "
        "this command writing outside the durable root, with the operator's "
        "privileges, at a path someone else chose"
    )
    assert not rejection_path.exists()
    lock_path.unlink()

    # 2. FIFO. Opens without ELOOP, so only the S_ISREG test on the descriptor
    #    can refuse it -- and only O_NONBLOCK keeps the open from blocking.
    os.mkfifo(str(lock_path))
    fifo = run_reject_review(root, seg, **invocation)
    assert fifo.returncode == 1, (
        f"a FIFO at the lock path must REFUSE (and must not hang), got "
        f"rc={fifo.returncode}\nstdout:\n{fifo.stdout}"
    )
    assert "not a regular file" in json.loads(fifo.stdout.strip())["error"]
    assert not rejection_path.exists()
    lock_path.unlink()

    # CONTROL -- nothing planted, the same command succeeds and the lock file
    # it creates is an ordinary regular file.
    ok = run_reject_review(root, seg, **invocation)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert rejection_path.is_file()
    assert lock_path.is_file() and not lock_path.is_symlink()


def test_removing_a_record_that_cannot_authorize_is_itself_synced(tmp_path):
    """AN UNLINK IS A DIRECTORY-ENTRY CHANGE, and an unsynced one can be undone
    by a crash -- which would bring back a record this command has just told
    the operator was removed.

    The asymmetry is what makes it matter. The record's CREATION is already
    durable by the time the post-write check runs (write_rejection_record()
    fsyncs the directory before returning), so a crash between the unlink and
    a sync of that unlink restores a live authorization nobody granted, and
    the operator holds an exit-1 saying nothing remains. claim_record.py's own
    fsync_directory() states the rule this relies on.

    WHAT THIS PINS is the second sync, by COUNTING calls the fake sibling
    really received rather than by re-deriving what the code should have done:
    an ordinary success syncs once, and a post-write refusal syncs twice.
    Counting is the only way to tell them apart -- a single sync is exactly
    what an unsynced removal looks like from outside.

    WHAT IT DOES NOT PIN: that the sync durably survives a real power loss.
    Nothing runnable here can observe that; what is observable is whether the
    call is made at all, which is the thing that was missing."""
    root = make_reject_review_root(tmp_path, claim_record_source=FAKE_CLAIM_RECORD_PY)
    calls = root / "test_fixture_fsync_calls.txt"
    seg = "seg01"
    token = "RUN1:seg01:r1"
    review = write_review_lite(root / "segments", seg, clean=False, coverage_ok=True,
                                dispatch_token=token,
                                findings=[{"loc": "p1:1", "severity": "major",
                                           "issue": "x", "suggest": "y"}])
    invocation = dict(reason="verified unfounded", round_label="1", expect_token=token,
                      expect_digest=REJECT_MOD._review_verdict_digest(review))
    rejection_path = root / "segments" / f"{seg}.review_rejected.json"
    review_path = root / "segments" / f"{seg}.review.json"

    # CONTROL FIRST -- an ordinary success. One directory change, one sync.
    ok = run_reject_review(root, seg, **invocation)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert rejection_path.is_file()
    assert calls.read_text(encoding="utf-8").strip().splitlines() == [
        str(root / "segments")
    ], (
        "a plain successful write must sync the segments directory exactly "
        "once; if this is already two, the count below proves nothing"
    )

    # Now the refusal path: review.json an hour ahead, so the record written
    # cannot authorize and must be removed again.
    rejection_path.unlink()
    calls.unlink()
    future_ns = review_path.stat().st_mtime_ns + 3_600 * 1_000_000_000
    os.utime(review_path, ns=(future_ns, future_ns))

    refused = run_reject_review(root, seg, **invocation)
    assert refused.returncode == 1, (
        f"a record that cannot authorize must be reported as a failure, got "
        f"rc={refused.returncode}\nstdout:\n{refused.stdout}"
    )
    assert not rejection_path.exists()
    made = calls.read_text(encoding="utf-8").strip().splitlines()
    assert made == [str(root / "segments"), str(root / "segments")], (
        f"the write's sync AND the removal's sync must both have happened -- "
        f"one call means the record is gone from this running system while its "
        f"already-durable directory entry can come back after a crash, which is "
        f"the authorization the removal exists to prevent. got {made!r}"
    )


def test_equal_mtimes_are_spent_on_the_producer_side_exactly_as_on_the_consumers(tmp_path):
    """A TIE MUST BE SPENT ON BOTH SIDES, and nothing else in this file pins
    that on the PRODUCER.

    The consumer's rule 8 is `record_mtime_ns > review_mtime_ns` -- strict, so
    equal stamps refuse. `_rejection_outlives_review()` in reject_review.py is
    a deliberate second copy of that comparison, and the other renewal tests
    only ever exercise stamps that are strictly newer or strictly older. Relax
    the producer's `>` to `>=` and every one of them still passes, while the
    two sides now DISAGREE at exactly one point: on equal stamps the producer
    answers "still authorizes" and reports `already_recorded`, the consumer
    refuses, and the operator is back in the dead end the renewal branch was
    added to remove -- reported as a success.

    Equal stamps are not exotic here. Both files live in one directory and are
    written moments apart, and a filesystem whose mtime granularity is coarser
    than the gap between the two writes produces them by itself.

    Driven through the real CLI, and the assertion is on the OUTPUT FIELD
    rather than on the helper, because what matters is the decision the
    command actually makes."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, _ = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major", "issue": "unfounded", "suggest": "n/a"}]
    review = _dna_write_review(root, driver_mod, round_label="1", clean=False,
                                coverage_ok=True, draft_sha1=draft_sha1, findings=findings)

    read = print_verdict_digest_in(root, "seg01")
    assert read.returncode == 0, read.stdout + read.stderr
    printed = json.loads(read.stdout.strip())
    invocation = dict(reason="verified: the claimed source string occurs zero times",
                      round_label=printed["round_label"], expect_token=printed["dispatch_token"],
                      expect_digest=printed["verdict_digest"])

    first = run_reject_review_in(root, "seg01", **invocation)
    assert first.returncode == 0, first.stdout + first.stderr
    assert json.loads(first.stdout.strip())["renewed"] is False

    rejection_path = _rejection_json_path(root)
    review_path = _review_json_path(root)
    first_rejected_at = json.loads(rejection_path.read_text(encoding="utf-8"))["rejected_at"]

    # THE TIE. review.json is stamped to the record's own mtime, exactly.
    tie_ns = rejection_path.stat().st_mtime_ns
    os.utime(review_path, ns=(tie_ns, tie_ns))
    assert rejection_path.stat().st_mtime_ns == review_path.stat().st_mtime_ns, (
        "this filesystem must be able to represent the tie, or the test below "
        "is not testing what it says"
    )
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is False, (
        "baseline: the CONSUMER treats a tie as spent. Everything below is "
        "about whether the producer agrees"
    )

    # The audit stamp is forced apart so the renewal is observable even when
    # both runs land inside one second. Nothing gate 6 compares is touched.
    record = json.loads(rejection_path.read_text(encoding="utf-8"))
    record["rejected_at"] = "2020-01-01T00:00:00Z"
    rejection_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    os.utime(rejection_path, ns=(tie_ns, tie_ns))

    again = run_reject_review_in(root, "seg01", **invocation)
    assert again.returncode == 0, again.stdout + again.stderr
    payload = json.loads(again.stdout.strip())
    assert payload["renewed"] is True, (
        f"a tie is SPENT: the producer must renew, not report already_recorded. "
        f"A producer using >= answers the opposite of the consumer here, and "
        f"tells the operator the rejection stands while the driver ignores it: "
        f"{payload!r}"
    )
    assert payload.get("already_recorded") is None
    assert payload["reason"] == invocation["reason"]
    assert payload["rejected_at"] != "2020-01-01T00:00:00Z" != first_rejected_at
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True, (
        "and after the renewal the consumer honours it again"
    )


def test_the_read_mode_envelope_is_its_own_and_carries_no_rejection_path_fields(tmp_path):
    """--print-verdict-digest is a DIFFERENT command with a different envelope,
    and the `renewed`-on-every-success promise is scoped to the rejection
    path's two shapes. Pinned here so the documented split is a fact rather
    than a comment: this envelope's key set is exactly the seven the read mode
    documents, and neither `renewed` nor `already_recorded` is among them --
    a caller must not start branching on a field this command never had."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    token = "RUN1:seg01:r1"
    write_review_lite(root / "segments", seg, clean=False, coverage_ok=True,
                       dispatch_token=token,
                       findings=[{"loc": "p1:1", "severity": "major",
                                  "issue": "x", "suggest": "y"}])

    read = print_verdict_digest_in(root, seg)
    assert read.returncode == 0, read.stdout + read.stderr
    payload = json.loads(read.stdout.strip())
    assert set(payload) == {
        "success", "seg", "review_path", "dispatch_token", "verdict_digest",
        "round_label", "round_label_problem",
    }, f"the read mode's envelope must be exactly its documented keys, got {sorted(payload)}"
    assert payload["success"] is True
    assert not (root / "segments" / f"{seg}.review_rejected.json").exists(), (
        "and it remains a pure read"
    )


# ===========================================================================
# Heavyweight fixture -- duplicated from tests/segment_dispatch_driver.test.py's
# own Phase 2 harness (real segment_dispatch_driver.py/select_segments.py/
# ledger_merge.py/resume_setup.py/ledger_update.py/draft_sha1.py/claim_record.py
# /mass-translate-wf.template.js, small fakes matching the real scripts'
# OBSERVABLE CONTRACT for resolve_codex_companion.py/draft_ready.py/
# validate_draft.py/codex_job.py -- each has its own dedicated test file
# proving ITS internal correctness) -- self-contained per this project's own
# house convention rather than importing that file. This file's job is
# derive_next_action()'s OWN #461 branch, not re-proving the rest of the
# driver's state machine (already covered exhaustively in that file).
# ===========================================================================

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

FULL_PROFILE_YAML = (
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

# Controllable fake codex_job.py, accepting the REAL argv shape and APPENDING
# the raw argv it actually received to <durable_root>/
# test_fixture_argv_log.jsonl -- so a test counts what was really dispatched
# instead of re-deriving what should have been from the same code that builds
# it. A translate dispatch writes a draft; a review dispatch promotes a
# CLEAN review bound to the draft on disk, via the REAL staged draft_sha1.py
# rather than a second hand-copied hash. Trimmed relative to
# tests/segment_dispatch_driver.test.py's own copy (no failure/sleep/marker
# scenarios): the one test here that dispatches for real needs to know WHICH
# kinds ran, nothing more.
FAKE_CODEX_JOB_PY = """#!/usr/bin/env python3
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
    with open(cwd / "test_fixture_argv_log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": args.kind, "seg": args.seg, "argv": sys.argv[1:]}) + "\\n")

    segments_dir = cwd / "segments"
    draft_path = segments_dir / (args.seg + ".draft.json")
    if args.kind == "translate":
        draft = {"seg": args.seg, "blocks": {"p1": "hola"}, "dispatch_token": args.expect_token}
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
    else:
        sha1_mod = _load_real_draft_sha1()
        review = {
            "clean": True, "coverage_ok": True, "findings": [],
            "draft_sha1": sha1_mod.draft_content_sha1(draft_path),
            "dispatch_token": args.expect_token,
        }
        (segments_dir / (args.seg + ".review.json")).write_text(json.dumps(review), encoding="utf-8")

    print(json.dumps({
        "ok": True, "kind": args.kind, "seg": args.seg, "jobId": "fake-job",
        "job_status": "completed", "timed_out": False, "adopted": False,
        "reason": "promoted", "error_detail": None,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def make_durable_root(tmp_path, name="durable_root", profile_yaml=FULL_PROFILE_YAML):
    """Isolated durable_root: real segment_dispatch_driver.py +
    select_segments.py + ledger_merge.py + claim_record.py +
    reject_review.py under scripts/, a fake cache_key.py stub, empty
    manifest/runs/segments scaffolding, and a minimal profile.yml +
    ownership marker."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DRIVER_SRC, scripts_dir / "segment_dispatch_driver.py")
    shutil.copy2(SELECT_SEGMENTS_SRC, scripts_dir / "select_segments.py")
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    shutil.copy2(CLAIM_RECORD_SRC, scripts_dir / "claim_record.py")
    shutil.copy2(REJECT_REVIEW_SRC, scripts_dir / "reject_review.py")
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(REJECT_REVIEW_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    schemas_dir = root / "schemas"
    shutil.copytree(ASSETS_DIR / "schemas", schemas_dir)

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()

    profile_path = root / "profile.yml"
    profile_path.write_text(profile_yaml, encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )
    return root


def stage_phase2_sibling_scripts(scripts_dir, templates_dir):
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(RESUME_SETUP_SRC, scripts_dir / "resume_setup.py")
    shutil.copy2(LEDGER_UPDATE_SRC, scripts_dir / "ledger_update.py")
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    shutil.copy2(CLAIM_RECORD_SRC, scripts_dir / "claim_record.py")
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(CLAIM_RECORD_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    (scripts_dir / "resolve_codex_companion.py").write_text(FAKE_RESOLVE_CODEX_COMPANION_PY, encoding="utf-8")
    (scripts_dir / "draft_ready.py").write_text(FAKE_DRAFT_READY_PY, encoding="utf-8")
    (scripts_dir / "validate_draft.py").write_text(FAKE_VALIDATE_DRAFT_PY, encoding="utf-8")
    (scripts_dir / "codex_job.py").write_text(FAKE_CODEX_JOB_PY, encoding="utf-8")

    templates_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MASS_TRANSLATE_TEMPLATE_SRC, templates_dir / "mass-translate-wf.template.js")


def stage_phase2_scripts(root):
    stage_phase2_sibling_scripts(root / "scripts", root / "templates")
    (root / "runs" / ".plugin_bundle_hash").write_text("fixture-plugin-bundle-hash\n", encoding="utf-8")
    (root / "runs" / ".orchestration_bundle_hash").write_text("fixture-orchestration-bundle-hash\n", encoding="utf-8")


CACHE_KEY_FIELDS = [
    "input_sha1", "style_contract_hash", "used_terms_hash", "pipeline_version",
    "schema_hash", "prompt_hash", "agent_config_hash", "profile_semantics_hash",
    "particle_config_hash", "source_extraction_hash", "source_input_hash",
    "derivation_bundle_hash", "verse_map_hash", "note_map_hash", "plugin_bundle_hash",
]


def make_cache_key(seed):
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def write_manifest(root, seg_ids):
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": s} for s in seg_ids]}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_fixture_cache_keys(root, mapping):
    (root / "test_fixture_cache_keys.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")


def write_fixture_segpack(root, seg):
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps({"seg": seg, "blocks": [], "footnotes": [], "verses": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def phase2_project(tmp_path, n=1, name="durable_root", profile_yaml=FULL_PROFILE_YAML):
    """A fully staged durable_root, ready to run derive_next_action()
    against for real (real node, real mass-translate-wf.template.js, so
    the fabricated_loc gate every reachable review passes through actually
    runs) -- mirrors tests/segment_dispatch_driver.test.py's own
    phase2_project() fixture."""
    root = make_durable_root(tmp_path, name=name, profile_yaml=profile_yaml)
    stage_phase2_scripts(root)
    seg_ids = [f"seg{i:02d}" for i in range(1, n + 1)]
    write_manifest(root, seg_ids)
    write_fixture_cache_keys(root, {seg: make_cache_key(seg) for seg in seg_ids})
    for seg in seg_ids:
        write_fixture_segpack(root, seg)
    return root


_FIXTURE_TRANSLATE_CFG = {
    "max_fix_rounds": 2, "batch_agent_cap": 10000, "max_codex_jobs_per_batch": 400,
    "effort": "high", "model": "", "source_lang": "fr", "target_lang": "ru",
    "verse_policy": {"mode": "skip", "threshold_lines": None},
    "research_mode": "", "citation_content_types": [],
}

FIXTURE_COMPANION_PATH = "/fake/codex-companion.mjs"  # matches FAKE_RESOLVE_CODEX_COMPANION_PY's fixed output


def _load_fixture_driver(root):
    """Loads segment_dispatch_driver.py from ITS OWN staged copy under
    `root/scripts/` -- self-anchoring only resolves to `root`'s fixture
    siblings when the module itself is loaded FROM `root/scripts/
    segment_dispatch_driver.py` (byte-for-byte the same reasoning
    tests/segment_dispatch_driver.test.py's own _load_fixture_driver()
    states)."""
    return _load_module(root / "scripts" / "segment_dispatch_driver.py", "segment_dispatch_driver_rejection_fixture")


_DNA_RUN_ID = "20260101T000000Z"


def _dna_setup(root):
    driver_mod = _load_fixture_driver(root)
    ctx = driver_mod.DispatchContext(
        dirs=driver_mod.resolve_dirs(None), run_id=_DNA_RUN_ID, translate_cfg=dict(_FIXTURE_TRANSLATE_CFG),
        companion_path=FIXTURE_COMPANION_PATH, durable_root_str=None, plugin_root_str=None,
        node_bin="node", session_id="test-session",
    )
    return driver_mod, ctx


def _dna_write_draft(root, driver_mod, run_id=_DNA_RUN_ID, seg="seg01"):
    draft = {"seg": seg, "blocks": {"p1": "hola"}, "dispatch_token": driver_mod.translate_dispatch_token(run_id, seg)}
    (root / "segments" / f"{seg}.draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    return draft


def _dna_write_review(root, driver_mod, *, round_label, clean, coverage_ok, draft_sha1,
                       findings=None, run_id=_DNA_RUN_ID, seg="seg01"):
    review = {
        "clean": clean, "coverage_ok": coverage_ok, "findings": findings or [],
        "draft_sha1": draft_sha1,
        "dispatch_token": driver_mod.review_dispatch_token(run_id, seg, round_label),
    }
    (root / "segments" / f"{seg}.review.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return review


def _review_json_path(root, seg="seg01"):
    return root / "segments" / f"{seg}.review.json"


def _rejection_json_path(root, seg="seg01"):
    return root / "segments" / f"{seg}.review_rejected.json"


def _dna_write_rejection(root, seg, *, dispatch_token, verdict_digest, round_label="1",
                          reason="operator judged the finding unfounded",
                          rejected_at="2026-01-01T00:00:00Z", operator_invocation="test invocation"):
    """Hand-writes segments/{seg}.review_rejected.json directly -- used
    ONLY for the negative/stale cases below, which a legitimate
    reject_review.py invocation can never produce (its own gate refuses
    a stale token/digest or a clean:true review by construction). The one
    SEAM test in this file uses the REAL reject_review.py CLI instead --
    see this file's own module docstring for why.

    Stamped strictly newer than the review it names (see
    _force_record_newer_than_review): rule 8 would otherwise be free to
    answer these tests before the rule each of them is actually about."""
    record = {
        "seg": seg, "dispatch_token": dispatch_token, "verdict_digest": verdict_digest,
        "round_label": round_label, "reason": reason, "rejected_at": rejected_at,
        "operator_invocation": operator_invocation,
    }
    _rejection_json_path(root, seg).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    _force_record_newer_than_review(root, seg)
    return record


def _force_record_newer_than_review(root, seg="seg01"):
    """Stamp the rejection record one millisecond NEWER than the review.json
    it names, in explicit nanoseconds -- never a sleep, which trades real
    wall-clock time for a guarantee it still does not make on a
    coarse-granularity filesystem.

    _rejection_matches()'s rule 8 refuses any record that is not strictly
    newer than its review, so on a tie EVERY refusal assertion in this file
    would pass without the rule it is named for ever being reached: the key
    set, the seg field and the digest would all go unread. Making the
    ordering explicit is what keeps each of those tests about its own
    subject."""
    review_mtime_ns = _review_json_path(root, seg).stat().st_mtime_ns
    path = _rejection_json_path(root, seg)
    stamp_ns = review_mtime_ns + 1_000_000
    os.utime(path, ns=(stamp_ns, stamp_ns))
    return stamp_ns


def _rewrite_review_preserving_mtime(root, review, seg="seg01"):
    """Write `review` over segments/{seg}.review.json and restore the file's
    ORIGINAL st_mtime_ns.

    Without the restore, the rewrite alone would push review.json past the
    rejection record and rule 8 would refuse -- so a test that mutates a
    review field to prove the DIGEST covers it would go green having proved
    only that rewriting a file changes its mtime. Holding the mtime still
    leaves the digest as the one thing that can have changed."""
    path = _review_json_path(root, seg)
    st = path.stat()
    path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    return path


def _dna_dispatch_log(root):
    """Every dispatch the fake codex_job.py actually performed, read from
    the argv log it appends to -- never predicted from the code under
    test."""
    log = root / "test_fixture_argv_log.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_reject_review_in(root, seg, *, reason, round_label, expect_token, expect_digest):
    """Invokes the REAL, staged reject_review.py as a subprocess against
    `root` (self-anchored: it lives at root/scripts/reject_review.py, so
    its own DURABLE_ROOT resolves to `root`)."""
    return subprocess.run(
        [
            sys.executable, str(root / "scripts" / "reject_review.py"), seg,
            "--reason", reason, "--round-label", round_label,
            "--expect-token", expect_token, "--expect-verdict-digest", expect_digest,
        ],
        capture_output=True, text=True, timeout=30,
    )


def print_verdict_digest_in(root, seg):
    """reject_review.py's READ mode, driven exactly as an operator reaches
    it -- the only supported way to obtain the value --expect-verdict-digest
    requires."""
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "reject_review.py"), seg, "--print-verdict-digest"],
        capture_output=True, text=True, timeout=30,
    )


# ---------------------------------------------------------------------------
# The seam -- the one producer -> consumer integration test, hand-building
# nothing.
# ---------------------------------------------------------------------------

def test_the_record_reject_review_actually_writes_is_the_one_the_driver_consumes(tmp_path):
    """THE WIRE CONTRACT, end to end, with no hand-typed stand-in anywhere:
    the operator's real sequence (--print-verdict-digest, then the rejection
    itself) runs as a real subprocess, and the file reject_review.py
    ACTUALLY wrote is handed to the REAL _rejection_matches() and
    derive_next_action().

    This is the failure two independently-written sides cannot catch for
    each other: producer and consumer each pass against their own fixture
    while the record's key NAMES, its value shapes or the digest algorithm
    disagree between them. Every hand-built record elsewhere in this file
    would keep passing through such a break, because a hand-built record is
    written to the consumer's expectations by construction.

    The scenario is the actual #461 one: a not-clean review whose findings
    are unfounded (verified case: a fabricated Hebrew-source quote), on a
    draft that stays byte-IDENTICAL throughout (nothing was ever applied,
    correctly -- there was nothing real to apply). Before the rejection,
    derive_next_action() must report needs_fix (proving the #461 live-lock
    is real in this fixture); after it, the SAME call, against the SAME
    unchanged draft, must advance to a FRESH review at round "2" tagged
    cause="rejected_findings" -- never "translate", never a round anyone
    else could reach."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_before = (root / "segments" / "seg01.draft.json").read_bytes()
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{
        "loc": "p1:1", "severity": "major",
        "issue": "dropped quantifier -- source allegedly reads a phrase absent from the block",
        "suggest": "n/a (finding is fabricated)",
    }]
    review = _dna_write_review(
        root, driver_mod, round_label="1", clean=False, coverage_ok=True,
        draft_sha1=draft_sha1, findings=findings,
    )
    token = review["dispatch_token"]
    assert token == driver_mod.review_dispatch_token(_DNA_RUN_ID, "seg01", "1")

    # Sanity: the #461 live-lock is real in this fixture before any rejection.
    assert driver_mod.derive_next_action("seg01", ctx) == {
        "action": "needs_fix", "round_label": "1", "findings": findings,
    }

    # Step 1 -- the READ mode. Every value the write mode demands comes from
    # here, from ONE read, and none of them is typed into this test: a
    # digest this test computed itself would agree with the producer by
    # construction and could never notice the two sides drifting apart.
    read = print_verdict_digest_in(root, "seg01")
    assert read.returncode == 0, (
        f"--print-verdict-digest must succeed for a clean:false review, got "
        f"rc={read.returncode}\nstdout:\n{read.stdout}\nstderr:\n{read.stderr}"
    )
    printed = json.loads(read.stdout.strip())
    assert printed["success"] is True
    assert printed["round_label_problem"] is None, printed
    assert not _rejection_json_path(root).exists(), (
        "--print-verdict-digest is a PURE READ -- it must not create the record"
    )

    # Step 2 -- the write mode, fed only what the read mode printed.
    result = run_reject_review_in(
        root, "seg01",
        reason="verified: the claimed source string occurs zero times in block p1; nothing to fix",
        round_label=printed["round_label"],
        expect_token=printed["dispatch_token"],
        expect_digest=printed["verdict_digest"],
    )
    assert result.returncode == 0, (
        f"reject_review.py must succeed for a schema-valid, clean:false review with a "
        f"matching --expect-token/--expect-verdict-digest, got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert json.loads(result.stdout.strip())["success"] is True

    rejection_path = _rejection_json_path(root)
    assert rejection_path.is_file()
    record = json.loads(rejection_path.read_text(encoding="utf-8"))
    assert set(record) == set(driver_mod.REJECTION_RECORD_KEYS), (
        f"the record the producer WROTE must carry exactly the key set the "
        f"consumer pins -- got {sorted(record)}, consumer expects "
        f"{sorted(driver_mod.REJECTION_RECORD_KEYS)}"
    )
    assert record["seg"] == "seg01"
    assert record["dispatch_token"] == token
    assert record["verdict_digest"] == driver_mod._review_verdict_digest(review), (
        "the producer's digest and the consumer's must be the same function of "
        "the same review object -- this is the one assertion that fails when the "
        "two implementations drift apart"
    )
    assert record["reason"].strip() != ""
    assert rejection_path.stat().st_mtime_ns > _review_json_path(root).stat().st_mtime_ns, (
        "rule 8 needs the record strictly newer than the review it names; a tie "
        "here is a filesystem-granularity problem in this fixture, not a "
        "behaviour difference in the driver"
    )

    # The consumer accepts the producer's own file, judged directly rather
    # than only through derive_next_action()'s several other preconditions.
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True

    # The draft is UNCHANGED (never touched by the rejection), yet the
    # outcome now advances instead of looping on needs_fix.
    assert (root / "segments" / "seg01.draft.json").read_bytes() == draft_before
    action = driver_mod.derive_next_action("seg01", ctx)
    assert action == {
        "action": "review", "round_label": "2", "cause": "rejected_findings",
    }, f"expected the #461 fix to advance to a fresh round-2 review, got {action}"


# ---------------------------------------------------------------------------
# What the digest actually covers.
# ---------------------------------------------------------------------------

def test_the_verdict_digest_covers_the_whole_review_not_just_its_dispatch_token(tmp_path):
    """The digest is specified as sha256 over the WHOLE parsed review
    object, and NOTHING ELSE IN THIS FILE PINS THAT. A pair of
    implementations that hashed only `dispatch_token` -- one in
    reject_review.py, one in segment_dispatch_driver.py -- would agree with
    each other perfectly, satisfy the "64 lowercase hex" checks, satisfy
    every stale-token and forged-record test here, and quietly turn the
    binding into a duplicate of rule 5. An operator would then reject
    verdict V1 and authorize whatever V2 later appeared under the same
    token, which is the exact hole --expect-verdict-digest exists to close.

    Driven the way the hole is actually reached: a GENUINE rejection is
    produced by the real reject_review.py, and then a real field of the
    real review is changed underneath it, one field per case. The
    dispatch_token is left untouched in every case -- if the assertion
    could be satisfied by changing the token, it would be testing rule 5.

    review.json is rewritten with its mtime HELD (see
    _rewrite_review_preserving_mtime), because a rewrite alone spends the
    rejection through rule 8 -- and the identical-content control below is
    what proves the held mtime really does keep the record live, so each
    False that follows is attributable to the digest and to nothing else."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major",
                 "issue": "the source reads 'X' here", "suggest": "restore 'X'"}]
    review = _dna_write_review(root, driver_mod, round_label="1", clean=False, coverage_ok=True,
                                draft_sha1=draft_sha1, findings=findings)

    read = print_verdict_digest_in(root, "seg01")
    assert read.returncode == 0, read.stdout + read.stderr
    printed = json.loads(read.stdout.strip())
    result = run_reject_review_in(
        root, "seg01", reason="verified unfounded against the source",
        round_label=printed["round_label"], expect_token=printed["dispatch_token"],
        expect_digest=printed["verdict_digest"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True

    # CONTROL: the same bytes written again, mtime held. Still authorizes --
    # so every refusal below is caused by the CHANGED FIELD, not by the fact
    # that review.json was rewritten at all.
    _rewrite_review_preserving_mtime(root, review)
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True, (
        "the control must still authorize, or nothing below is attributable to "
        "the field that changed"
    )

    def _mutated(**changes):
        mutated = json.loads(json.dumps(review))
        for key, value in changes.items():
            mutated[key] = value
        return mutated

    # A finding's own prose -- the reviewer's actual claim, and the thing an
    # operator reads before deciding it is unfounded. Same token, same
    # verdict flags, same draft_sha1.
    reworded = _mutated(findings=[{**findings[0], "issue": "the source reads 'Y' here"}])
    _rewrite_review_preserving_mtime(root, reworded)
    assert reworded["dispatch_token"] == review["dispatch_token"]
    assert driver_mod._rejection_matches("seg01", root / "segments", reworded) is False, (
        "a rejection must stop matching once the FINDING it was filed against "
        "has been reworded -- the operator authorized a claim they read, not a "
        "dispatch_token"
    )
    assert driver_mod.derive_next_action("seg01", ctx) == {
        "action": "needs_fix", "round_label": "1", "findings": reworded["findings"],
    }, "and the driver must fall back to the ordinary needs_fix loop"

    # coverage_ok -- a verdict field the operator never judged.
    flipped = _mutated(coverage_ok=False)
    _rewrite_review_preserving_mtime(root, flipped)
    assert driver_mod._rejection_matches("seg01", root / "segments", flipped) is False, (
        "a rejection must stop matching once coverage_ok has flipped under it"
    )

    # draft_sha1 -- the review's binding to the bytes it judged.
    rebound = _mutated(draft_sha1="1" * 40)
    _rewrite_review_preserving_mtime(root, rebound)
    assert driver_mod._rejection_matches("seg01", root / "segments", rebound) is False, (
        "a rejection must stop matching once the review claims to describe a "
        "DIFFERENT draft than the one it did when the operator read it"
    )

    # And back to the original bytes: live again, so the three refusals above
    # were the mutations and not a one-way door this fixture walked through.
    _rewrite_review_preserving_mtime(root, review)
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True


# ---------------------------------------------------------------------------
# What may occupy the record's path -- shape, audit trail, provenance.
# ---------------------------------------------------------------------------

def test_a_record_that_is_not_exactly_the_pinned_seven_fields_authorizes_nothing(tmp_path):
    """Consumer rules 2-4. The pre-#461 matcher compared `dispatch_token`
    and `verdict_digest` and nothing else -- both values discoverable by
    anything that can read review.json, which is everything that can write
    next to it -- so a two-field hand-written file was a complete,
    sufficient authorization to override a genuine reviewer over a draft
    nobody re-read.

    Each case starts from the record the REAL reject_review.py wrote and
    changes exactly one thing about it, with the untouched record asserted
    to authorize first: a refusal that fires for a case is then attributable
    to that case's own change. The record is rewritten rather than the
    review, so it stays newer than review.json and rule 8 is never the
    reason for any False here."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major", "issue": "x", "suggest": "y"}]
    review = _dna_write_review(root, driver_mod, round_label="1", clean=False, coverage_ok=True,
                                draft_sha1=draft_sha1, findings=findings)

    read = print_verdict_digest_in(root, "seg01")
    assert read.returncode == 0, read.stdout + read.stderr
    printed = json.loads(read.stdout.strip())
    result = run_reject_review_in(
        root, "seg01", reason="verified unfounded", round_label=printed["round_label"],
        expect_token=printed["dispatch_token"], expect_digest=printed["verdict_digest"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rejection_path = _rejection_json_path(root)
    genuine = json.loads(rejection_path.read_text(encoding="utf-8"))
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True, (
        "the genuine record must authorize, or every refusal below is vacuous"
    )

    def _place(record):
        rejection_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        _force_record_newer_than_review(root)
        return driver_mod._rejection_matches("seg01", root / "segments", review)

    # A two-field file carrying exactly what the pre-#461 matcher compared,
    # copied verbatim out of the genuine record so both values are RIGHT.
    assert _place({
        "dispatch_token": genuine["dispatch_token"],
        "verdict_digest": genuine["verdict_digest"],
    }) is False, (
        "a hand-written two-field file must authorize NOTHING, however correct "
        "its two values -- it carries no audit trail at all"
    )
    assert driver_mod.derive_next_action("seg01", ctx) == {
        "action": "needs_fix", "round_label": "1", "findings": findings,
    }

    # An EIGHTH key. Not a harmless annotation: an unrecognised field means
    # the record came from a writer whose rules this reader does not know,
    # and a superset is no more acceptable than a subset for an artifact
    # that overrides a reviewer.
    assert _place({**genuine, "approved_by": "someone"}) is False, (
        "an extra key must refuse -- the record was produced by something "
        "whose rules the consumer cannot check"
    )

    # An empty audit trail. Rule 2 alone cannot tell this apart from a real
    # record: every pinned key is present, and the accountability is absent.
    assert _place({**genuine, "reason": ""}) is False, (
        "an authorization with an empty reason is not an authorization"
    )
    assert _place({**genuine, "reason": "   "}) is False, (
        "whitespace is not a reason either -- rule 3 strips before judging"
    )

    # A record filed under a NEIGHBOURING segment. Its token and digest are
    # this segment's, so only the `seg` field can refuse it.
    assert _place({**genuine, "seg": "seg02"}) is False, (
        "a record naming a different segment must not authorize this one"
    )

    # Restored: the genuine record still authorizes, so the five refusals
    # above were the five changes and not a fixture that went stale.
    assert _place(genuine) is True


def test_a_symlink_at_the_record_path_authorizes_nothing_even_pointing_at_a_valid_record(tmp_path):
    """Consumer rule 1. A rejection is a LOCAL fact about this segment's
    directory; a symlink is an indirection to bytes written somewhere the
    driver never validated, and the pre-#461 matcher's read_text() would
    have followed it without noticing.

    The target here is not a stub -- it is the exact file the REAL
    reject_review.py wrote, moved aside (shutil.move preserves its mtime,
    so rule 8 is not what answers this). The same bytes therefore authorize
    when they ARE the record and refuse when they are merely POINTED AT,
    which is the whole distinction rule 1 draws, and the assertion is
    round-tripped in both directions so neither half can be an accident."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major", "issue": "x", "suggest": "y"}]
    review = _dna_write_review(root, driver_mod, round_label="1", clean=False, coverage_ok=True,
                                draft_sha1=draft_sha1, findings=findings)

    read = print_verdict_digest_in(root, "seg01")
    assert read.returncode == 0, read.stdout + read.stderr
    printed = json.loads(read.stdout.strip())
    result = run_reject_review_in(
        root, "seg01", reason="verified unfounded", round_label=printed["round_label"],
        expect_token=printed["dispatch_token"], expect_digest=printed["verdict_digest"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rejection_path = _rejection_json_path(root)
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True

    elsewhere = root / "stashed_rejection.json"
    shutil.move(str(rejection_path), str(elsewhere))
    os.symlink(str(elsewhere), str(rejection_path))
    assert rejection_path.is_symlink()
    assert json.loads(rejection_path.read_text(encoding="utf-8")) == json.loads(
        elsewhere.read_text(encoding="utf-8")
    ), "the symlink really does resolve to the genuine record -- that is the point"

    assert driver_mod._rejection_matches("seg01", root / "segments", review) is False, (
        "a symlink must refuse even when it points at the very record "
        "reject_review.py wrote: the consumer judges the descriptor it opened, "
        "and O_NOFOLLOW is what makes that true"
    )
    assert driver_mod.derive_next_action("seg01", ctx) == {
        "action": "needs_fix", "round_label": "1", "findings": findings,
    }

    # The other direction: the same bytes back at the real path authorize
    # again, so the refusal above was the indirection and nothing else.
    rejection_path.unlink()
    shutil.move(str(elsewhere), str(rejection_path))
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True


# ---------------------------------------------------------------------------
# The "final" round -- where the rejection reopens a TERMINAL verdict, and
# where nothing but rule 8 can stop it repeating.
# ---------------------------------------------------------------------------

def test_a_final_round_rejection_converges_the_unit_on_the_operators_attestation(tmp_path):
    """#527: what a `final` rejection buys, after the thing it used to buy
    turned out not to work.

    It used to buy EXACTLY ONE re-review, and the segment capped anyway
    whenever the replacement verdict came back non-clean. That is a second
    opinion, and a second opinion is the one remedy this case cannot use: both
    reviewers read the SAME unchanged input, so where the INPUT is what
    misleads them the same false finding is re-derived every round and the
    unit can never converge -- nothing to apply, and a cap at the end of it.

    So a matching, unspent record over an unmoved draft now TERMINATES the
    unit as converged. The baseline below is the whole point: with no record
    on disk this exact state caps, terminally.

    THE RECORD TRAVELS WHOLE, and that is asserted rather than assumed: the
    operator's own `reason` reaches the ledger note through this field, and a
    bool would have lost it.

    NOT SPENT, unlike every other consumption in this file: rule 8 spends a
    record when review.json is rewritten, and nothing rewrites it on this
    path any more. Re-deriving returns the IDENTICAL action -- a fixed point,
    not a repeated codex spend. It lapses when the draft moves, which is the
    next test."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major",
                 "issue": "the source reads a phrase absent from the block", "suggest": "n/a"}]
    review = _dna_write_review(root, driver_mod, round_label="final", clean=False, coverage_ok=True,
                                draft_sha1=draft_sha1, findings=findings)

    # Baseline: without a rejection this segment is capped, terminally.
    assert driver_mod.derive_next_action("seg01", ctx) == {
        "action": "cap_reached", "findings": findings, "reviewed_sha1": draft_sha1,
        "reviewed_token": review["dispatch_token"],
        "reviewed_digest": driver_mod._review_verdict_digest(review),
    }

    read = print_verdict_digest_in(root, "seg01")
    assert read.returncode == 0, read.stdout + read.stderr
    printed = json.loads(read.stdout.strip())
    assert printed["round_label"] == "final", (
        f"the read mode must report the label the token carries, got {printed}"
    )
    result = run_reject_review_in(
        root, "seg01", reason="verified: the claimed source string occurs zero times",
        round_label=printed["round_label"], expect_token=printed["dispatch_token"],
        expect_digest=printed["verdict_digest"],
    )
    assert result.returncode == 0, (
        f"reject_review.py must accept a final-round rejection, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    record = json.loads(_rejection_json_path(root).read_text(encoding="utf-8"))

    action = driver_mod.derive_next_action("seg01", ctx)
    assert action == {
        "action": "converged_by_rejection", "round_label": "final", "rejection": record,
        "reviewed_sha1": draft_sha1,
        "reviewed_token": review["dispatch_token"],
        "reviewed_digest": driver_mod._review_verdict_digest(review),
    }, "a final-round rejection over an unmoved draft must converge the unit"
    assert action["rejection"]["reason"] == (
        "verified: the claimed source string occurs zero times"
    ), "the operator's own reason must reach the caller -- it is what the ledger note carries"

    assert driver_mod.derive_next_action("seg01", ctx) == action, (
        "nothing rewrites review.json on this path, so the record is not spent and "
        "the action is a fixed point -- re-driving the segment re-derives the same "
        "convergence rather than costing another codex job"
    )


def test_a_final_round_rejection_lapses_the_moment_the_draft_moves(tmp_path):
    """The scoping half of #527: the attestation is about THESE bytes.

    An operator rejected a verdict over the draft that verdict was written
    against. If the draft then moves -- a hand edit, a fix applied out of band
    -- the judgement no longer describes what is on disk, so it must not
    terminate anything. The fall-through is today's behaviour, unchanged: one
    fresh `final` review, with reopen_capped so the terminal fragment is made
    recoverable before the dispatch is spent.

    The record is NOT stale here (its token and digest still name the review
    on disk, and it is still newer than it) -- so a green here can only come
    from the draft comparison, which is the thing under test."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    draft = _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major", "issue": "unfounded", "suggest": "n/a"}]
    _dna_write_review(root, driver_mod, round_label="final", clean=False, coverage_ok=True,
                       draft_sha1=draft_sha1, findings=findings)

    printed = json.loads(print_verdict_digest_in(root, "seg01").stdout.strip())
    result = run_reject_review_in(
        root, "seg01", reason="verified unfounded against the source",
        round_label=printed["round_label"], expect_token=printed["dispatch_token"],
        expect_digest=printed["verdict_digest"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert driver_mod.derive_next_action("seg01", ctx)["action"] == "converged_by_rejection", (
        "CONTROL: over the unmoved draft this record converges, so the refusal below "
        "can only come from the edit"
    )

    moved = dict(draft, blocks={"p1": "hola de nuevo"})
    (root / "segments" / "seg01.draft.json").write_text(
        json.dumps(moved, ensure_ascii=False), encoding="utf-8")
    assert driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts") != draft_sha1

    assert driver_mod._rejection_matches("seg01", root / "segments",
                                          json.loads(_review_json_path(root).read_text(encoding="utf-8"))), (
        "the record itself must still be live, or this test proves nothing about the draft"
    )
    assert driver_mod.derive_next_action("seg01", ctx) == {
        "action": "review", "round_label": "final", "reopen_capped": True,
        "cause": "rejected_findings",
    }, "a rejection over a draft that has since moved must fall back to a fresh review"


def test_a_final_round_rejection_never_converges_an_incomplete_coverage_verdict(tmp_path):
    """The conjunct an operator's attestation cannot supply.

    reject_review.py gates on `clean` ALONE, deliberately -- its own condition
    1 says coverage being incomplete is "a different fact from findings being
    unfounded", and the operator is only ever asked to judge whether a FINDING
    is real. So this branch is reachable with coverage_ok False, and
    converging there would mark a segment done over a review that
    affirmatively reports dropped blocks/footnotes/verses
    (review.schema.json's own description of the field). A fresh review is the
    right answer to that verdict, and it is what falls through.

    Two roots rather than one mutated review: rewriting `coverage_ok` in place
    would change the verdict digest and the record would stop matching for
    rule 6 reasons, so the refusal would be green for the wrong reason. Each
    root runs the REAL reject_review.py over its own verdict; the only
    difference between them is the field under test."""
    def _drive(name, coverage_ok):
        root = phase2_project(tmp_path, n=1, name=name)
        driver_mod, ctx = _dna_setup(root)
        _dna_write_draft(root, driver_mod)
        draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
        _dna_write_review(root, driver_mod, round_label="final", clean=False, coverage_ok=coverage_ok,
                           draft_sha1=draft_sha1,
                           findings=[{"loc": "p1:1", "severity": "major",
                                      "issue": "unfounded", "suggest": "n/a"}])
        printed = json.loads(print_verdict_digest_in(root, "seg01").stdout.strip())
        result = run_reject_review_in(
            root, "seg01", reason="verified unfounded against the source",
            round_label=printed["round_label"], expect_token=printed["dispatch_token"],
            expect_digest=printed["verdict_digest"],
        )
        assert result.returncode == 0, (
            f"reject_review.py gates on clean alone, so it must accept this "
            f"rejection whatever coverage_ok says: {result.stdout}{result.stderr}"
        )
        return driver_mod.derive_next_action("seg01", ctx)

    assert _drive("root_coverage_ok", True)["action"] == "converged_by_rejection", (
        "CONTROL: with coverage_ok true the identical flow converges"
    )
    assert _drive("root_coverage_incomplete", False) == {
        "action": "review", "round_label": "final", "reopen_capped": True,
        "cause": "rejected_findings",
    }, "a coverage_ok:false verdict must never be terminated by a findings-only attestation"


def test_process_segment_converges_a_final_rejection_and_spends_no_codex_job(tmp_path):
    """What the convergence COSTS and what it leaves on disk, measured at the
    only layer that actually spends anything and actually writes.

    ZERO dispatches is the headline: the whole defect #527 names is that the
    old path spent a real codex job re-asking a question whose answer could
    not change. Counted from the fake codex_job.py's own argv log.

    The ledger fragment is asserted in full shape because it is what an
    operator and every later selector read: `converged`, the final round's
    `rounds`, the `reviewed_draft_sha1` ledger_update.py binds itself, and --
    the #527 addition -- a `note` carrying the operator's own reason. Without
    that note this fragment is indistinguishable from a reviewer's clean
    convergence, while the review.json sitting beside it still says
    clean:false: the pair would read as corruption."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_before = (root / "segments" / "seg01.draft.json").read_bytes()
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    _dna_write_review(root, driver_mod, round_label="final", clean=False, coverage_ok=True,
                       draft_sha1=draft_sha1,
                       findings=[{"loc": "p1:1", "severity": "major",
                                  "issue": "the source reads a phrase absent from the block",
                                  "suggest": "n/a"}])
    printed = json.loads(print_verdict_digest_in(root, "seg01").stdout.strip())
    reason = "verified against the source: the block is stored in visual order"
    result = run_reject_review_in(
        root, "seg01", reason=reason, round_label=printed["round_label"],
        expect_token=printed["dispatch_token"], expect_digest=printed["verdict_digest"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _dna_dispatch_log(root) == [], "nothing has been dispatched before this call"

    outcome = driver_mod.process_segment("seg01", ctx)

    assert _dna_dispatch_log(root) == [], (
        "an operator-attested convergence must spend NO codex job at all -- the "
        "re-review it replaces is the cost #527 exists to stop paying"
    )
    assert outcome == {"seg": "seg01", "converged": True, "outcome": "converged",
                       "cause": "rejected_findings"}, outcome
    assert (root / "segments" / "seg01.draft.json").read_bytes() == draft_before, (
        "the draft an operator attested is correct must not be rewritten"
    )

    fragment = json.loads((root / "runs" / "ledger.d" / "seg01.json").read_text(encoding="utf-8"))
    assert fragment["status"] == "converged"
    assert fragment["rounds"] == _FIXTURE_TRANSLATE_CFG["max_fix_rounds"] + 1, (
        f"the mandatory final round is max_fix_rounds + 1, got {fragment['rounds']}"
    )
    assert fragment["reviewed_draft_sha1"] == draft_sha1
    note = fragment.get("note") or ""
    assert reason in note, (
        f"the operator's own reason is the entire audit trail for this "
        f"convergence and must survive into the ledger: {fragment!r}"
    )
    assert "seg01.review_rejected.json" in note, (
        f"the note must name where the whole attestation lives, not only quote "
        f"part of it: {note!r}"
    )
    assert (root / "segments" / ".ever_converged.seg01").is_file(), (
        "ledger_update.py raises the durable sentinel on every convergence it "
        "records, and this one is no exception"
    )


def test_a_crash_between_the_sentinel_and_the_fragment_still_converges_on_the_retry(tmp_path):
    """The two-file convergence write's crash residue, and why it is not a
    dead end on THIS route.

    ledger_update.py raises `.ever_converged.{seg}` BEFORE it replaces the
    ledger fragment, so a process killed in between leaves the sentinel beside
    the old terminal fragment -- here, the very cap the operator was looking
    at. Nothing on this route reads the ledger (derive_next_action() derives
    from the draft, the review and the record, all three untouched by the
    crash), and the sentinel gates re-TRANSLATION, which an attested
    convergence never reaches. So the identical invocation re-derives the
    identical action and finishes the write.

    Simulated by its END STATE rather than by killing a process mid-write: the
    sentinel present, the cap fragment still on disk. That is exactly what the
    crash leaves, and it is what the retry has to cope with."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    _dna_write_review(root, driver_mod, round_label="final", clean=False, coverage_ok=True,
                       draft_sha1=draft_sha1,
                       findings=[{"loc": "p1:1", "severity": "major",
                                  "issue": "unfounded", "suggest": "n/a"}])
    printed = json.loads(print_verdict_digest_in(root, "seg01").stdout.strip())
    result = run_reject_review_in(
        root, "seg01", reason="verified unfounded against the source",
        round_label=printed["round_label"], expect_token=printed["dispatch_token"],
        expect_digest=printed["verdict_digest"],
    )
    assert result.returncode == 0, result.stdout + result.stderr

    fragment_dir = root / "runs" / "ledger.d"
    fragment_dir.mkdir(parents=True, exist_ok=True)
    (fragment_dir / "seg01.json").write_text(json.dumps(
        {"timestamp": "2026-01-01T00:00:00Z", "status": "non_converged", "reason": "cap"},
        ensure_ascii=False), encoding="utf-8")
    (root / "segments" / ".ever_converged.seg01").write_text("", encoding="utf-8")

    outcome = driver_mod.process_segment("seg01", ctx)

    assert outcome["outcome"] == "converged", outcome
    fragment = json.loads((fragment_dir / "seg01.json").read_text(encoding="utf-8"))
    assert fragment["status"] == "converged", (
        f"the retry must replace the terminal cap wholesale, got {fragment}"
    )
    assert "reason" not in fragment, (
        "ledger_update.py is a full replace, never a merge -- reason: cap must be GONE"
    )


def test_the_terminal_binding_check_serves_the_convergence_write_under_its_own_name(tmp_path):
    """The pre-write binding check #527 reuses, and the one thing the reuse
    had to get right.

    The helper is the cap fork's, generalized: derive_next_action() decides
    from the verdict it parsed, process_segment() commits in a LATER step, and
    nothing in this driver owns review.json in between. An operator-attested
    convergence is the more durable of the two terminal writes -- it raises
    the `.ever_converged` sentinel -- so it goes through the same check.

    Driven directly rather than through process_segment(), deliberately and
    with the limit stated: the substitution this refuses happens BETWEEN
    derive and write inside one call, so no test that drives the whole call
    can stage it. What is pinned here is the contract the handler depends on
    -- the check refuses a swapped verdict carrying identical provenance, and
    its message names the write it was called for rather than the cap it was
    born for. A `what` that never reached the messages would leave a
    convergence failure reported as a cap failure."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    rejected = _dna_write_review(root, driver_mod, round_label="final", clean=False, coverage_ok=True,
                                  draft_sha1=draft_sha1,
                                  findings=[{"loc": "p1:1", "severity": "major",
                                             "issue": "unfounded", "suggest": "n/a"}])
    action = {
        "action": "converged_by_rejection", "round_label": "final", "rejection": {},
        "reviewed_sha1": draft_sha1,
        "reviewed_token": rejected["dispatch_token"],
        "reviewed_digest": driver_mod._review_verdict_digest(rejected),
    }
    assert driver_mod._terminal_write_still_binds_what_was_reviewed(
        "seg01", ctx, action, what="convergence") is None, (
        "CONTROL: while the attested verdict is the one on disk, the check must pass"
    )

    # V2: same provenance -- same dispatch_token (a pure function of run, seg
    # and round label) over the same unread draft -- and a different verdict.
    _dna_write_review(root, driver_mod, round_label="final", clean=False, coverage_ok=True,
                       draft_sha1=draft_sha1,
                       findings=[{"loc": "p1:1", "severity": "major",
                                  "issue": "a DIFFERENT finding nobody attested",
                                  "suggest": "n/a"}])
    refusal = driver_mod._terminal_write_still_binds_what_was_reviewed(
        "seg01", ctx, action, what="convergence")
    assert refusal is not None, (
        "a verdict swapped for another carrying identical provenance must refuse "
        "-- the digest is the only fact that separates them"
    )
    assert "convergence decision" in refusal and "convergence write" in refusal, (
        f"the refusal must name the write it was called for: {refusal!r}"
    )
    assert "cap" not in refusal, (
        f"reporting a convergence failure as a cap failure is the exact confusion "
        f"the `what` parameter exists to prevent: {refusal!r}"
    )
    assert "cap decision" in driver_mod._terminal_write_still_binds_what_was_reviewed(
        "seg01", ctx, action), "and the default is still the cap fork's own wording"


def test_the_convergence_handler_refuses_and_writes_nothing_when_the_binding_check_fails(tmp_path):
    """That the handler CALLS the binding check, which the direct test above
    cannot show.

    Deleting the call, dropping `what="convergence"`, or ignoring the refusal
    leaves every other test in this file green: the helper returns None on the
    happy path, so its absence is invisible there. The substitution it guards
    against happens inside one process_segment() call, between the derivation
    and the write, so it cannot be staged from outside -- which is exactly why
    the seam is faked HERE and only here: derive_next_action() is replaced for
    one call by one that reports a digest no review on disk carries. Everything
    downstream of it is the real handler.

    NOTHING WRITTEN is half the assertion. A terminal write that refuses must
    leave the ledger untouched, so whatever fragment is on disk (very likely
    the cap the operator was looking at) stays the durable record and the next
    invocation re-derives from what is actually there."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    review = _dna_write_review(root, driver_mod, round_label="final", clean=False, coverage_ok=True,
                                draft_sha1=draft_sha1,
                                findings=[{"loc": "p1:1", "severity": "major",
                                           "issue": "unfounded", "suggest": "n/a"}])
    record = _dna_write_rejection(
        root, "seg01", dispatch_token=review["dispatch_token"],
        verdict_digest=driver_mod._review_verdict_digest(review), round_label="final",
    )
    assert driver_mod.derive_next_action("seg01", ctx)["action"] == "converged_by_rejection", (
        "CONTROL: the real derivation converges here, so the refusal below comes "
        "from the substituted digest and not from the fixture"
    )

    real = driver_mod.derive_next_action
    driver_mod.derive_next_action = lambda seg, ctx_: {
        "action": "converged_by_rejection", "round_label": "final", "rejection": record,
        "reviewed_sha1": draft_sha1,
        "reviewed_token": review["dispatch_token"],
        "reviewed_digest": "0" * 64,
    }
    try:
        outcome = driver_mod.process_segment("seg01", ctx)
    finally:
        driver_mod.derive_next_action = real

    assert outcome["outcome"] == "failed", outcome
    assert outcome["reason"] == "converge-write-review-moved", (
        f"the convergence write has its own refusal reason -- reporting it as the "
        f"cap's would send an operator to the wrong branch: {outcome!r}"
    )
    assert "convergence" in (outcome.get("detail") or ""), (
        f"the detail must name the write it refused: {outcome.get('detail')!r}"
    )
    assert not (root / "runs" / "ledger.d" / "seg01.json").exists(), (
        "a refused terminal write must write NOTHING -- not a converged fragment, "
        "and not a partial one"
    )
    assert not (root / "segments" / ".ever_converged.seg01").exists(), (
        "and no sentinel: it is raised inside the convergence write this refused"
    )
    assert _dna_dispatch_log(root) == [], "nothing may be dispatched on a refusal either"


def test_the_convergence_note_collapses_whitespace_and_bounds_the_operators_reason(tmp_path):
    """The note is a single JSON string other tools render whole, and the reason
    inside it is free text an operator typed -- reject_review.py requires only
    that it be non-empty, so nothing upstream bounds its length or forbids
    newlines. Both normalizations are asserted at their boundary rather than in
    the middle: 300 characters passes through untouched, 301 truncates, and the
    truncation is visible rather than silent."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, _ = _dna_setup(root)

    multiline = driver_mod._rejection_convergence_note(
        "seg01", {"reason": "verified\n  against   the source:\n\nthe phrase is absent",
                   "rejected_at": "2026-01-01T00:00:00Z"})
    assert "\n" not in multiline, f"a note carrying newlines is not one string: {multiline!r}"
    assert "verified against the source: the phrase is absent" in multiline, multiline

    budget = driver_mod.REJECTION_NOTE_REASON_BUDGET
    exact = driver_mod._rejection_convergence_note(
        "seg01", {"reason": "a" * budget, "rejected_at": "2026-01-01T00:00:00Z"})
    assert "a" * budget in exact and "..." not in exact, (
        "a reason exactly at the budget is not over it and must survive whole"
    )
    over = driver_mod._rejection_convergence_note(
        "seg01", {"reason": "a" * (budget + 1), "rejected_at": "2026-01-01T00:00:00Z"})
    assert "a" * budget not in over, "one character over the budget must truncate"
    assert "..." in over, "and the truncation must be visible, not silent"


def test_a_spent_rejection_is_renewed_by_the_identical_command_instead_of_dead_ending(tmp_path):
    """THE OPERATOR'S SECOND DECISION, at the label where it is unavoidable.

    #527 narrowed the route into this state without removing it: a `final`
    rejection over an unmoved draft with coverage_ok now converges the unit,
    so what still gets here is a rejection the driver sent back for a fresh
    review anyway -- the draft moved, or the verdict reported incomplete
    coverage -- plus the replacement that review promoted. The producer-side
    mechanics under test are unchanged, and are what this test is about.

    "final" is absorbing, so a replacement review is dispatched at the SAME
    label and review_dispatch_token() mints a byte-IDENTICAL token; a reviewer
    that independently reaches the same verdict over the same unchanged draft
    produces a byte-identical digest too. Rule 8 -- record strictly newer than
    the review it names -- is therefore the only fact separating the rejected
    verdict from its replacement, and it is what SPENDS the record. The
    operator who judges the replacement unfounded as well then had nowhere to
    go: the identical command reported `already_recorded` and rewrote nothing,
    a different --reason refused as a conflict, and the documented remedy was
    to delete the file by hand -- a remedy the tool names but cannot reach.

    Now the identical-reason path asks _rejection_outlives_review() first and
    RENEWS a record the consumer has already spent, reporting
    `renewed: true`.

    THE CONTROL COMES FIRST, in this same test: an UNSPENT record must still
    take the no-op path. Without it, "the record was rewritten" is equally
    consistent with a build that lost idempotency altogether, and the renewal
    assertion would be proving the wrong thing.

    ONE FIXTURE DETAIL, stated because it looks like a shortcut and is not:
    `rejected_at` is second-resolution UTC (now_iso8601()), so two runs inside
    one second stamp the IDENTICAL string and "the timestamp moved" would be a
    coin flip. The on-disk stamp is therefore set to a distinctly old value
    before the renewal run -- everything gate 6 actually compares
    (dispatch_token, verdict_digest, reason) is left untouched, so the branch
    reached is the same one, and the assertion becomes deterministic instead
    of racing the clock."""
    # No DispatchContext: this test judges the record through the consumer's
    # own _rejection_matches(seg, segments_dir, review), which takes its
    # arguments explicitly. derive_next_action()'s use of the rejection is
    # already covered above; what is new here is the PRODUCER's renewal branch.
    root = phase2_project(tmp_path, n=1)
    driver_mod, _ = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major",
                 "issue": "the source reads a phrase absent from the block", "suggest": "n/a"}]
    review = _dna_write_review(root, driver_mod, round_label="final", clean=False,
                                coverage_ok=True, draft_sha1=draft_sha1, findings=findings)

    read = print_verdict_digest_in(root, "seg01")
    assert read.returncode == 0, read.stdout + read.stderr
    printed = json.loads(read.stdout.strip())
    invocation = dict(
        reason="verified: the claimed source string occurs zero times in block p1",
        round_label=printed["round_label"], expect_token=printed["dispatch_token"],
        expect_digest=printed["verdict_digest"],
    )

    first = run_reject_review_in(root, "seg01", **invocation)
    assert first.returncode == 0, (
        f"the first rejection must succeed, got rc={first.returncode}\n"
        f"stdout:\n{first.stdout}\nstderr:\n{first.stderr}"
    )
    first_payload = json.loads(first.stdout.strip())
    assert first_payload["success"] is True
    assert first_payload["renewed"] is False, (
        f"the ordinary write path is not a renewal and must say so explicitly, "
        f"got {first_payload!r}"
    )
    assert "already_recorded" not in first_payload
    record1 = json.loads(_rejection_json_path(root).read_text(encoding="utf-8"))

    # CONTROL -- the record is UNSPENT (stamped strictly newer than the review
    # in explicit ns rather than trusting write order, which rule 8 reads as a
    # tie on a coarse-granularity filesystem).
    _force_record_newer_than_review(root)
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True, (
        "the control must start from a LIVE record, or the no-op below is not "
        "the no-op branch"
    )
    noop = run_reject_review_in(root, "seg01", **invocation)
    assert noop.returncode == 0, (
        f"the identical re-run over a live record must succeed, got "
        f"rc={noop.returncode}\nstdout:\n{noop.stdout}\nstderr:\n{noop.stderr}"
    )
    noop_payload = json.loads(noop.stdout.strip())
    assert noop_payload["success"] is True
    assert noop_payload["already_recorded"] is True
    # PRESENT-AND-FALSE, not absent, and subscripted rather than .get()-ed on
    # purpose. The contract is that `renewed` is on EVERY success payload so a
    # caller can branch on it without first testing whether the key exists --
    # and a payload that merely OMITS it satisfies any .get() default while
    # handing that caller a KeyError. Pinning presence is what makes this
    # assertion about the contract instead of about the value alone.
    assert noop_payload["renewed"] is False, (
        f"the no-op branch must report renewed:false explicitly, got {noop_payload!r}"
    )
    assert json.loads(_rejection_json_path(root).read_text(encoding="utf-8")) == record1, (
        "an idempotent re-run must rewrite NOTHING -- rejected_at and "
        "operator_invocation are the only trace of who first made the call"
    )
    assert noop_payload["rejected_at"] == record1["rejected_at"]
    assert noop_payload["operator_invocation"] == record1["operator_invocation"]

    # SPEND IT -- exactly as the consumer does: a replacement review lands
    # after the record. The bytes of review.json are untouched (so the token
    # and digest the invocation names still describe it, and the SAME command
    # is genuinely re-runnable); only its mtime moves past the record's.
    stale = dict(record1, rejected_at="2020-01-01T00:00:00Z")
    _rejection_json_path(root).write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
    spent_ns = _rejection_json_path(root).stat().st_mtime_ns + 1_000_000
    os.utime(_review_json_path(root), ns=(spent_ns, spent_ns))
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is False, (
        "the record must really be SPENT before the renewal run, or the branch "
        "under test is not the one reached"
    )

    renewed = run_reject_review_in(root, "seg01", **invocation)
    assert renewed.returncode == 0, (
        f"the identical command must RENEW a spent record rather than dead-end, "
        f"got rc={renewed.returncode}\nstdout:\n{renewed.stdout}\n"
        f"stderr:\n{renewed.stderr}"
    )
    renewed_payload = json.loads(renewed.stdout.strip())
    assert renewed_payload["success"] is True
    # .get(), not [] -- the dead-ended payload carries no `renewed` key at all,
    # and a bare KeyError would hide the payload that shows WHY (an
    # `already_recorded: true` over a record the consumer has already spent).
    assert renewed_payload.get("renewed") is True, (
        f"a renewal must be reported as one -- 'nothing needed doing' and 'the "
        f"previous record was spent and has been replaced' look alike from "
        f"outside and mean opposite things: {renewed_payload!r}"
    )
    assert "already_recorded" not in renewed_payload

    record2 = json.loads(_rejection_json_path(root).read_text(encoding="utf-8"))
    assert set(record2) == set(driver_mod.REJECTION_RECORD_KEYS)
    assert record2["reason"] == record1["reason"], (
        "the substantive audit content must survive a renewal -- it is what had "
        "to match byte-for-byte for this branch to be reachable at all"
    )
    assert record2["dispatch_token"] == record1["dispatch_token"]
    assert record2["verdict_digest"] == record1["verdict_digest"]
    assert record2["round_label"] == record1["round_label"]
    assert record2["rejected_at"] != stale["rejected_at"], (
        "a renewal REWRITES the record, so rejected_at moves onto this "
        "invocation -- that cost is paid deliberately and must be visible"
    )
    assert record2["operator_invocation"].strip() != ""

    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True, (
        "and the whole point: the real consumer honours the renewed record "
        "again, so the operator's second decision reaches the driver without "
        "anyone deleting a file by hand"
    )


def test_a_different_reason_over_the_same_verdict_refuses_spent_or_not(tmp_path):
    """The renewal branch must not widen into an OVERWRITE. Renewal is
    reachable only on a byte-identical --reason; a different one is still a
    conflict this script refuses, because nothing here can tell a deliberate
    correction from one operator replacing a colleague's record, and the
    reason is the entire audit value of the artifact.

    Both states are checked, because only one of them is new: the UNSPENT
    conflict is pre-existing behaviour, and the SPENT one is the state the
    renewal branch was added for -- a widening would show up there first and
    nowhere else.

    THE LAST STEP IS THE CONTROL. Re-running with the ORIGINAL reason over the
    same spent record must renew, proving the two refusals above were caused
    by the changed REASON and not by a fixture whose renewal path was
    unreachable to begin with -- in which case both refusals would have been
    vacuous."""
    # No DispatchContext, for the same reason as the renewal test above: the
    # subject is the producer's conflict gate, judged through
    # _rejection_matches(), which takes its arguments explicitly.
    root = phase2_project(tmp_path, n=1)
    driver_mod, _ = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major", "issue": "x", "suggest": "y"}]
    review = _dna_write_review(root, driver_mod, round_label="final", clean=False,
                                coverage_ok=True, draft_sha1=draft_sha1, findings=findings)

    read = print_verdict_digest_in(root, "seg01")
    assert read.returncode == 0, read.stdout + read.stderr
    printed = json.loads(read.stdout.strip())
    original_reason = "verified: the claimed source string occurs zero times in block p1"
    bindings = dict(round_label=printed["round_label"], expect_token=printed["dispatch_token"],
                    expect_digest=printed["verdict_digest"])

    first = run_reject_review_in(root, "seg01", reason=original_reason, **bindings)
    assert first.returncode == 0, first.stdout + first.stderr
    record1 = json.loads(_rejection_json_path(root).read_text(encoding="utf-8"))
    other_reason = "on reflection the finding was fine after all"

    def _refuses_with_a_different_reason(state):
        result = run_reject_review_in(root, "seg01", reason=other_reason, **bindings)
        assert result.returncode == 1, (
            f"a DIFFERENT reason over the same verdict must refuse while the "
            f"record is {state}, got rc={result.returncode}\nstdout:\n"
            f"{result.stdout}\nstderr:\n{result.stderr}"
        )
        payload = json.loads(result.stdout.strip())
        assert payload["success"] is False
        assert "DIFFERENT reason" in payload["error"], (
            f"the refusal must name the conflict, not some other gate: "
            f"{payload['error']!r}"
        )
        assert original_reason in payload["error"] and other_reason in payload["error"], (
            f"and must show both reasons, so the operator can see what they "
            f"would have destroyed: {payload['error']!r}"
        )
        on_disk = json.loads(_rejection_json_path(root).read_text(encoding="utf-8"))
        assert on_disk == record1, (
            f"a refused conflict must leave the record byte-identical while it "
            f"is {state}, got {on_disk!r}"
        )

    _force_record_newer_than_review(root)
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True
    _refuses_with_a_different_reason("still live")

    # Spend it: the state the renewal branch exists for.
    spent_ns = _rejection_json_path(root).stat().st_mtime_ns + 1_000_000
    os.utime(_review_json_path(root), ns=(spent_ns, spent_ns))
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is False
    _refuses_with_a_different_reason("already spent")

    # CONTROL: the ORIGINAL reason, same spent record -- renewal really is
    # reachable here, so the two refusals above were about the reason.
    control = run_reject_review_in(root, "seg01", reason=original_reason, **bindings)
    assert control.returncode == 0, control.stdout + control.stderr
    control_payload = json.loads(control.stdout.strip())
    assert control_payload.get("renewed") is True, (
        f"the renewal path must be reachable in this exact state, or the "
        f"refusals above prove nothing about the reason: {control_payload!r}"
    )


def test_a_record_that_still_cannot_authorize_after_the_write_refuses_and_is_removed(tmp_path):
    """SUCCESS MUST MEAN THE RECORD CAN ACTUALLY AUTHORIZE, and until the
    post-write check it did not.

    The renewal decision is made from the mtimes BEFORE the write, and the
    record that write produces is stamped with the clock as it is NOW -- which
    is not necessarily ahead of review.json. If that review carries a FUTURE
    mtime (a clock that stepped backwards, a file copied from a host whose
    clock ran ahead), the freshly written record is still older than the
    review it names, rule 8 refuses it, and the segment stays exactly as stuck
    as it was -- while the CLI reported `success: true`. Every retry reported
    success and changed nothing: the operator is told the remedy worked and
    the driver silently disagrees, which is worse than either outcome alone.

    WHAT THIS PINS: the command re-checks the same predicate the consumer will
    apply, AFTER the write, refuses when the answer is no -- naming both
    nanosecond stamps, because which way they run is what tells the operator
    this is a clock problem and not a mistake they made -- and REMOVES THE
    RECORD AGAIN.

    THE REMOVAL IS THE POINT, and an earlier version of this branch got it
    exactly backwards by keeping the record and calling it "inert while
    stale". Rule 8 compares the record against whatever review.json is on disk
    AT CONSUME TIME, not against the review this command read. Restore or
    rewrite a byte-identical review with an OLDER mtime -- same token, same
    digest, so every gate would still have passed -- and the retained record
    starts authorizing, with nobody having re-run anything and the operator
    holding an exit-1 saying it did not take effect. That is the "record
    outlives the fact it attests" shape write_rejection_record() already
    refuses one function away, on the directory-fsync path, for this reason.

    THE CONTROL IS THE SECOND HALF, and it is what makes the refusal
    attributable to the clock rather than to a gate that would refuse in any
    state: review.json's mtime is moved back behind the wall clock and the
    IDENTICAL command must then succeed and leave a record the real
    _rejection_matches() honours. It succeeds as a FRESH write
    (`renewed: false`), not as a renewal, precisely because the refused
    attempt left nothing behind to renew -- which is itself the removal being
    observed from the other side."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, _ = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major", "issue": "unfounded", "suggest": "n/a"}]
    review = _dna_write_review(root, driver_mod, round_label="1", clean=False,
                                coverage_ok=True, draft_sha1=draft_sha1, findings=findings)

    read = print_verdict_digest_in(root, "seg01")
    assert read.returncode == 0, read.stdout + read.stderr
    printed = json.loads(read.stdout.strip())
    invocation = dict(reason="verified: the claimed source string occurs zero times",
                      round_label=printed["round_label"], expect_token=printed["dispatch_token"],
                      expect_digest=printed["verdict_digest"])
    rejection_path = _rejection_json_path(root)
    review_path = _review_json_path(root)

    # review.json an hour into the FUTURE. Derived from its own current stamp
    # rather than from a wall-clock read, so the offset is exact and this test
    # never has to import a clock.
    future_ns = review_path.stat().st_mtime_ns + 3_600 * 1_000_000_000
    os.utime(review_path, ns=(future_ns, future_ns))

    refused = run_reject_review_in(root, "seg01", **invocation)
    assert refused.returncode == 1, (
        f"a record that cannot authorize must be REPORTED as a failure, not as "
        f"success, got rc={refused.returncode}\nstdout:\n{refused.stdout}\n"
        f"stderr:\n{refused.stderr}"
    )
    payload = json.loads(refused.stdout.strip())
    assert payload["success"] is False
    assert not rejection_path.exists(), (
        "the record must be REMOVED again -- a record the operator was TOLD "
        "had failed, left on disk, starts authorizing the moment review.json "
        "is replaced with an older stamp, and nobody re-ran anything"
    )
    # The record's own stamp has to come out of the MESSAGE, because the file
    # it described is deliberately gone by the time this reads it. That is the
    # removal and the naming pinned by one assertion each, from one run.
    stamped = re.search(r"record (\d+), review (\d+)", payload["error"])
    assert stamped is not None, (
        f"the refusal must name BOTH nanosecond stamps -- which way they run is "
        f"what identifies this as a clock problem: {payload['error']!r}"
    )
    record_ns = int(stamped.group(1))
    assert int(stamped.group(2)) == future_ns, (
        f"and the review stamp it names must be the one actually on disk: "
        f"{payload['error']!r}"
    )
    assert record_ns < future_ns, (
        "the whole refusal is that the record is older than the review; if the "
        "stamps do not run that way this test proved nothing"
    )
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is False, (
        "and the real consumer must agree: nothing authorizes this rejection"
    )

    # THE ATTACK THE REMOVAL EXISTS TO STOP, run against the state the refusal
    # just left. The review is replaced by a BYTE-IDENTICAL copy carrying an
    # OLDER stamp -- same dispatch_token, same digest, so every gate this
    # command applies would still pass. Had the record been kept, rule 8 would
    # now accept it and the driver would dispatch a review off a command that
    # exited 1.
    older_ns = record_ns - 1_000_000
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    os.utime(review_path, ns=(older_ns, older_ns))
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is False, (
        "a refused rejection must not become live merely because review.json "
        "was replaced with an older stamp -- there must be no record left to "
        "become live"
    )

    # CONTROL -- the same command, in a state where the clock is no longer
    # behind the review, must succeed. Otherwise the refusal above is
    # consistent with a gate that refuses in every state.
    ok = run_reject_review_in(root, "seg01", **invocation)
    assert ok.returncode == 0, (
        f"the IDENTICAL command must succeed once the clock is past the "
        f"review's stamp, or the refusal above was not about the clock at all\n"
        f"stdout:\n{ok.stdout}\nstderr:\n{ok.stderr}"
    )
    ok_payload = json.loads(ok.stdout.strip())
    assert ok_payload["success"] is True
    assert ok_payload["renewed"] is False, (
        f"and it succeeds as a FRESH write, not a renewal: the refused attempt "
        f"left nothing behind to renew, which is the removal seen from the "
        f"other side: {ok_payload!r}"
    )
    assert rejection_path.is_file()
    assert driver_mod._rejection_matches("seg01", root / "segments", review) is True, (
        "and this time the record really does authorize"
    )


def test_process_segment_spends_exactly_one_review_and_no_translate_on_a_consumed_rejection(tmp_path):
    """What a consumed rejection COSTS, measured at the only layer that
    actually spends anything. derive_next_action() returning
    {"action": "review", ...} is an in-memory decision; process_segment() is
    what turns it into codex jobs, and the guarantee #461 rests on is that a
    rejection NEVER makes a translate reachable -- a translate would
    overwrite the very draft bytes an operator has just attested are
    correct, which is the one unrecoverable outcome in this whole design.

    Counted from the fake codex_job.py's own argv log (what really ran),
    never from the actions derive_next_action() reported.

    The segment converges within this same call: the re-review at round "2"
    comes back clean against the unchanged draft, and the loop's next
    iteration records convergence with no further dispatch. That is what
    makes "exactly one" a real bound rather than a snapshot taken before the
    loop had finished spending."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_before = (root / "segments" / "seg01.draft.json").read_bytes()
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major", "issue": "unfounded", "suggest": "n/a"}]
    _dna_write_review(root, driver_mod, round_label="1", clean=False, coverage_ok=True,
                       draft_sha1=draft_sha1, findings=findings)

    read = print_verdict_digest_in(root, "seg01")
    assert read.returncode == 0, read.stdout + read.stderr
    printed = json.loads(read.stdout.strip())
    result = run_reject_review_in(
        root, "seg01", reason="verified unfounded against the source",
        round_label=printed["round_label"], expect_token=printed["dispatch_token"],
        expect_digest=printed["verdict_digest"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _dna_dispatch_log(root) == [], "nothing has been dispatched before this call"

    outcome = driver_mod.process_segment("seg01", ctx)

    dispatches = _dna_dispatch_log(root)
    kinds = [d["kind"] for d in dispatches]
    assert kinds == ["review"], (
        f"a consumed rejection must spend exactly ONE review dispatch and no "
        f"translate at all -- got {kinds}"
    )
    assert dispatches[0]["seg"] == "seg01"
    assert "--expect-token" in dispatches[0]["argv"]
    dispatched_token = dispatches[0]["argv"][dispatches[0]["argv"].index("--expect-token") + 1]
    assert dispatched_token == driver_mod.review_dispatch_token(_DNA_RUN_ID, "seg01", "2"), (
        f"the one dispatch must be the NEXT round's review, got {dispatched_token!r}"
    )
    assert (root / "segments" / "seg01.draft.json").read_bytes() == draft_before, (
        "the draft an operator attested is correct must not be rewritten"
    )
    assert outcome == {"seg": "seg01", "converged": True, "outcome": "converged"}, outcome


# ---------------------------------------------------------------------------
# Refusals -- a rejection artifact must be IGNORED (never trusted) whenever
# it cannot be freshly bound to the review currently on disk.
# ---------------------------------------------------------------------------

def test_derive_next_action_ignores_when_no_rejection_artifact_exists(tmp_path):
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major", "issue": "x", "suggest": "y"}]
    _dna_write_review(root, driver_mod, round_label="1", clean=False, coverage_ok=True,
                       draft_sha1=draft_sha1, findings=findings)
    assert not _rejection_json_path(root).exists()
    assert driver_mod.derive_next_action("seg01", ctx) == {
        "action": "needs_fix", "round_label": "1", "findings": findings,
    }


def test_derive_next_action_ignores_a_rejection_with_a_stale_dispatch_token(tmp_path):
    """A rejection recorded for a DIFFERENT round's dispatch_token (left
    behind after the segment moved on) must never swallow a genuinely new
    finding raised at the CURRENT round -- even when its verdict_digest is
    freshly (and correctly) computed against the current review, the
    stale token alone must refuse it."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major", "issue": "x", "suggest": "y"}]
    review = _dna_write_review(root, driver_mod, round_label="1", clean=False, coverage_ok=True,
                                draft_sha1=draft_sha1, findings=findings)
    stale_token = driver_mod.review_dispatch_token(_DNA_RUN_ID, "seg01", "2")
    _dna_write_rejection(
        root, "seg01",
        dispatch_token=stale_token,
        verdict_digest=driver_mod._review_verdict_digest(review),
    )
    assert driver_mod.derive_next_action("seg01", ctx) == {
        "action": "needs_fix", "round_label": "1", "findings": findings,
    }


def test_derive_next_action_ignores_a_rejection_whose_verdict_digest_describes_no_review(tmp_path):
    """Same dispatch_token (correctly matching the current review), and a
    verdict_digest that is well-formed -- 64 lowercase hex, so it passes
    every FORMAT check -- but is not the digest of anything. The
    review-was-rewritten-underneath-it case is a different fact and is owned
    by test_the_verdict_digest_covers_the_whole_review_not_just_its_
    dispatch_token, which reaches it by changing a real field of a real
    review rather than by typing an impossible constant."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major", "issue": "x", "suggest": "y"}]
    review = _dna_write_review(root, driver_mod, round_label="1", clean=False, coverage_ok=True,
                                draft_sha1=draft_sha1, findings=findings)
    _dna_write_rejection(
        root, "seg01",
        dispatch_token=review["dispatch_token"],
        verdict_digest="0" * 64,
    )
    assert driver_mod.derive_next_action("seg01", ctx) == {
        "action": "needs_fix", "round_label": "1", "findings": findings,
    }


def test_derive_next_action_ignores_a_malformed_rejection_artifact(tmp_path):
    """Not valid JSON at all -- read failure must fail toward ignoring the
    artifact (the pre-existing needs_fix behavior), never toward a crash
    or toward silently advancing."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    findings = [{"loc": "p1:1", "severity": "major", "issue": "x", "suggest": "y"}]
    _dna_write_review(root, driver_mod, round_label="1", clean=False, coverage_ok=True,
                       draft_sha1=draft_sha1, findings=findings)
    _rejection_json_path(root).write_text("not valid json {{{", encoding="utf-8")
    _force_record_newer_than_review(root)
    assert driver_mod.derive_next_action("seg01", ctx) == {
        "action": "needs_fix", "round_label": "1", "findings": findings,
    }


def test_derive_next_action_ignores_a_rejection_over_a_clean_true_review(tmp_path):
    """Defense in depth: even a rejection whose dispatch_token AND
    verdict_digest genuinely, correctly match the review currently on
    disk must be ignored when that review's own `clean` is True --
    reject_review.py's own gate can never legitimately produce this state
    (it refuses any review with clean != False), so reaching it here
    proves the artifact was NOT produced by reject_review.py (hand-edited,
    forged, or from a future writer that forgot the gate) -- and
    derive_next_action() must not trust it merely because the token and
    digest line up. clean:true + coverage_ok:false is used to reach the
    not-clean branch at all (clean:true + coverage_ok:true converges
    before ever consulting a rejection)."""
    root = phase2_project(tmp_path, n=1)
    driver_mod, ctx = _dna_setup(root)
    _dna_write_draft(root, driver_mod)
    draft_sha1 = driver_mod.current_draft_sha1("seg01", root / "segments", root / "scripts")
    review = _dna_write_review(root, driver_mod, round_label="1", clean=True, coverage_ok=False,
                                draft_sha1=draft_sha1, findings=[])
    _dna_write_rejection(
        root, "seg01",
        dispatch_token=review["dispatch_token"],
        verdict_digest=driver_mod._review_verdict_digest(review),
    )
    assert driver_mod.derive_next_action("seg01", ctx) == {
        "action": "needs_fix", "round_label": "1", "findings": [],
    }
