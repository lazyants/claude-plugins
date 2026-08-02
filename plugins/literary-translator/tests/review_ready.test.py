"""tests/review_ready.test.py -- regression-lock for the NEW
`review_ready.py` readiness probe (CONTRACT-1.2.0-reliability.md §4,
PLAN "New readiness script `assets/scripts/review_ready.py`"), the
#97/resume-integrity fix's bounded-poll gate for the restructured review
work-call (`reviewDispatchPrompt` -> `reviewWaitPrompt` -> ...).

CLI contract this file locks down (CONTRACT §4):

    python3 review_ready.py {seg} --expect-token TOK

Exit 0 ("ready") iff ALL of:
  1. `segments/{seg}.review.json` exists and parses as JSON;
  2. it validates FULL against `review.schema.json` (which 1.2.0 makes
     require a 5th field, `dispatch_token`, a string);
  3. its `draft_sha1` field equals `draft_sha1.py {seg}`'s OWN current
     output for the on-disk draft (the review is not stale relative to a
     draft that changed since);
  4. its `dispatch_token` field equals `--expect-token` exactly (the
     review artifact belongs to THIS run, not a stale/straggler one --
     the whole reason this script exists per #97/resume-integrity).
Else exit 1, printing one JSON line `{"ready": false, "reason": "..."}`
naming the specific problem. `review_ready.py` carries the
byte-identical `_SEG_ID_RE`/`validate_seg()` pair every sibling script in
this plugin shares (draft_ready.py, draft_sha1.py, validate_draft.py,
ledger_update.py, review_artifact_check.py) and calls `validate_seg(seg)`
FIRST, before any segments/ file I/O -- an unsafe seg exits 2 with
"segment id" named on stderr, matching draft_ready.py's own convention
exactly (see that script's own `main()`).

Five cases (CONTRACT §4 + PLAN's own "NEW `review_ready.test.py`" test
list):
  1. Full-schema reject: a review.json missing a required field (here,
     `findings`) -> not ready, reason names the schema problem.
  2. sha-mismatch reject: `draft_sha1` no longer matches the CURRENT
     on-disk draft's real hash (the draft changed since the review was
     written) -> not ready.
  3. token-mismatch reject (THE new 1.2.0 case): `dispatch_token` !=
     `--expect-token` -> not ready.
  4. Positive: schema-valid, sha1-fresh, token-matching -> ready, exit 0,
     `{"ready": true}`.
  5. `validate_seg` path-safety spot-check: a subset of the SAME
     MALICIOUS_SEGS vocabulary `tests/seg_safety_positional.test.py`
     drives exhaustively (that file's own shared cross-script harness
     already covers review_ready.py's seg-safety fully once it lands, in
     its own dedicated parametrized sweep) -- this file's spot-check is
     narrower and REVIEW_READY-specific: proving the seg check happens
     BEFORE any segments/ file I/O (no segments/ or schemas/ dir is even
     created in that test), not a full re-run of the whole malicious-seg
     matrix.

House style (see plugins/literary-translator/tests/E-TESTS-BRIEF.md and
this suite's own `draft_path_convention.test.py`/`review_prompt_schema_drift
.test.py`): real files only, never reimplemented logic -- every fixture
copies the REAL shipped `review_ready.py` and `draft_sha1.py` into an
isolated `tmp_path` durable_root (`{root}/scripts/...`) so
`review_ready.py`'s own self-anchored `Path(__file__).resolve().parents[1]`
resolves against the fixture root exactly as production does, and its
`draft_sha1` cross-check is proven against the REAL `draft_sha1.py`
subprocess output (never a hand-rolled sha1 reimplementation of that
script's now-canonicalized, dispatch_token-excluded hashing algorithm --
see draft_sha1.py's own 1.2.0 module docstring for why a naive raw-byte
hash is no longer the right comparison).

Status at the time this file was written (E-TESTS-BRIEF.md): Owner C has
NOT yet landed `review_ready.py` -- every test below that needs it fails
loudly, naming the missing file (via `make_review_ready_root`'s own
assertion), rather than being silently skipped. This is the correct,
intended behavior per this suite's "fail loudly, naming the offender"
charter (see draft_path_convention.test.py's own docstring for the same
principle applied to its still-missing TASK templates), not a bug in
this test file.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC_DIR = ASSETS_DIR / "schemas"

REVIEW_READY_SRC = SCRIPTS_SRC_DIR / "review_ready.py"
DRAFT_SHA1_SRC = SCRIPTS_SRC_DIR / "draft_sha1.py"
REVIEW_SCHEMA_SRC = SCHEMAS_SRC_DIR / "review.schema.json"

# draft_sha1.py and review.schema.json are already-shipped assets this file
# needs regardless of review_ready.py's own landing status -- a genuine
# collection-blocking problem if either is missing.
for _p in (DRAFT_SHA1_SRC, REVIEW_SCHEMA_SRC):
    assert _p.is_file(), f"expected plugin asset not found: {_p}"

# review_ready.py itself is a NEW script (CONTRACT §4, Owner C's part of
# this parallel 1.2.0 build) that may not have landed yet at the time this
# file runs -- checked per-fixture below (never a module-level assert), so
# each test that needs it fails individually and loudly, naming the
# missing file, instead of one collection error hiding every case's result.


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def make_review_ready_root(tmp_path):
    """Isolated durable_root carrying REAL copies of review_ready.py (this
    file's subject) and draft_sha1.py (which review_ready.py cross-checks
    the review's draft_sha1 field against), plus the real on-disk
    review.schema.json -- so review_ready.py's own self-anchored
    DURABLE_ROOT resolves against this fixture exactly as production runs
    it."""
    assert REVIEW_READY_SRC.is_file(), (
        f"required NEW script missing: {REVIEW_READY_SRC} "
        f"(CONTRACT-1.2.0-reliability.md §4, Owner C's part of this "
        f"parallel 1.2.0 build) -- review_ready.py has not landed yet, so "
        f"this test cannot run. Not a bug in this test."
    )
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    segments_dir = root / "segments"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    segments_dir.mkdir(parents=True)
    shutil.copy2(REVIEW_READY_SRC, scripts_dir / "review_ready.py")
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    shutil.copy2(REVIEW_SCHEMA_SRC, schemas_dir / "review.schema.json")
    return root


def run_review_ready(root, seg, expect_token):
    """review_ready.py has NO legacy callers to stay backward-compatible
    with (unlike draft_ready.py's own OPTIONAL --expect-token) -- see
    tests/seg_safety_positional.test.py's own module docstring -- so every
    invocation here passes --expect-token."""
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "review_ready.py"), seg, "--expect-token", expect_token],
        capture_output=True, text=True, timeout=30,
    )


def real_draft_sha1(root, seg):
    """The REAL draft_sha1.py's own reported digest for the draft currently
    on disk at the canonical path -- never reimplemented here. 1.2.0
    changed draft_sha1.py's algorithm to a canonicalized (sorted-key,
    dispatch_token-excluded) hash that review_ready.py must reproduce
    bit-for-bit; computing the expected value this way, rather than
    hand-hashing this test's own fixture bytes, keeps this file's
    fixtures correct without duplicating that algorithm (see
    draft_sha1.py's own 1.2.0 module docstring)."""
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "draft_sha1.py"), seg],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"draft_sha1.py failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout.strip()


def write_draft(segments_dir, seg, dispatch_token):
    draft = {
        "seg": seg, "blocks": {"p1": "hello"}, "footnotes": {}, "verses": {},
        "names": [], "notes": [], "dispatch_token": dispatch_token,
    }
    (segments_dir / f"{seg}.draft.json").write_text(json.dumps(draft), encoding="utf-8")
    return draft


def write_review(segments_dir, seg, draft_sha1_value, dispatch_token,
                  findings=None, clean=True, coverage_ok=True, omit_key=None):
    review = {
        "clean": clean,
        "coverage_ok": coverage_ok,
        "findings": findings if findings is not None else [],
        "draft_sha1": draft_sha1_value,
        "dispatch_token": dispatch_token,
    }
    if omit_key is not None:
        del review[omit_key]
    (segments_dir / f"{seg}.review.json").write_text(json.dumps(review), encoding="utf-8")
    return review


# ---------------------------------------------------------------------------
# 1. Full-schema reject
# ---------------------------------------------------------------------------

def test_review_ready_rejects_review_missing_a_required_field(tmp_path):
    """A review.json missing a required field (`findings`) must be reported
    not-ready, naming the schema problem -- proving review_ready.py
    validates FULL against review.schema.json (CONTRACT §4: 'validates
    FULL against review.schema.json'), not just the handful of fields it
    happens to read for its own sha1/token cross-checks."""
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "seg02"
    token = "RUN1:seg02:r1"
    write_draft(segments_dir, seg, dispatch_token="RUN1:seg02")
    real_sha1 = real_draft_sha1(root, seg)
    write_review(segments_dir, seg, draft_sha1_value=real_sha1, dispatch_token=token, omit_key="findings")

    result = run_review_ready(root, seg, token)
    assert result.returncode == 1, (
        f"expected not-ready (exit 1) for a schema-invalid review.json, "
        f"got rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["ready"] is False
    assert "findings" in payload["reason"].lower(), (
        f"reason must name the missing/offending schema field, got: {payload['reason']!r}"
    )


# ---------------------------------------------------------------------------
# 2. sha-mismatch reject
# ---------------------------------------------------------------------------

def test_review_ready_rejects_draft_sha1_mismatch(tmp_path):
    """review.json's own draft_sha1 no longer matches draft_sha1.py's
    CURRENT output for the on-disk draft (the draft changed since the
    review was written) -- must be reported not-ready."""
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "seg03"
    token = "RUN1:seg03:r1"
    write_draft(segments_dir, seg, dispatch_token="RUN1:seg03")
    wrong_sha1 = "0" * 40
    write_review(segments_dir, seg, draft_sha1_value=wrong_sha1, dispatch_token=token)

    result = run_review_ready(root, seg, token)
    assert result.returncode == 1, (
        f"expected not-ready (exit 1) for a stale draft_sha1, got "
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["ready"] is False
    assert "sha" in payload["reason"].lower(), (
        f"reason must name the draft_sha1 mismatch, got: {payload['reason']!r}"
    )


# ---------------------------------------------------------------------------
# 3. token-mismatch reject -- THE new 1.2.0 case
# ---------------------------------------------------------------------------

def test_review_ready_rejects_dispatch_token_mismatch(tmp_path):
    """review.json's own dispatch_token differs from --expect-token, i.e.
    this review artifact belongs to a stale/different run -- THE new
    1.2.0 case, per #97/resume-integrity: this is the whole reason
    review_ready.py exists (CONTRACT §4/PLAN's 'Token bound at EVERY
    commit/consume gate')."""
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "seg04"
    write_draft(segments_dir, seg, dispatch_token="RUN1:seg04")
    real_sha1 = real_draft_sha1(root, seg)
    write_review(segments_dir, seg, draft_sha1_value=real_sha1, dispatch_token="RUN1:seg04:r1")

    result = run_review_ready(root, seg, "RUN2:seg04:r1")
    assert result.returncode == 1, (
        f"expected not-ready (exit 1) for a dispatch_token mismatch, got "
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["ready"] is False
    assert "token" in payload["reason"].lower(), (
        f"reason must name the dispatch_token mismatch, got: {payload['reason']!r}"
    )


# ---------------------------------------------------------------------------
# 4. Positive
# ---------------------------------------------------------------------------

def test_review_ready_positive_when_schema_sha_and_token_all_match(tmp_path):
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "seg01"
    token = "RUN1:seg01:r1"
    write_draft(segments_dir, seg, dispatch_token="RUN1:seg01")
    real_sha1 = real_draft_sha1(root, seg)
    write_review(segments_dir, seg, draft_sha1_value=real_sha1, dispatch_token=token)

    result = run_review_ready(root, seg, token)
    assert result.returncode == 0, (
        f"expected READY (exit 0) when schema/sha1/token all match, got "
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload == {"ready": True}


# ---------------------------------------------------------------------------
# 5. validate_seg path-safety spot-check
# ---------------------------------------------------------------------------

def _load_seg_safety_module():
    """Load tests/seg_safety_positional.test.py by file identity (never a
    reimplementation) purely to reuse its MALICIOUS_SEGS constant -- this
    is the SAME technique review_prompt_schema_drift.test.py's parser
    reuse uses, applied to a shared test vocabulary instead of a shared
    parser."""
    spec = importlib.util.spec_from_file_location(
        "seg_safety_positional_vocab_for_review_ready_test",
        Path(__file__).resolve().parent / "seg_safety_positional.test.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SEG_SAFETY_MODULE = _load_seg_safety_module()

# A representative SUBSET of the full MALICIOUS_SEGS matrix -- the trap
# case (trailing newline), a path-traversal case, a shell-metachar case, an
# empty string, and a FRONTBACK:-prefixed traversal. Full exhaustive
# coverage across every consuming script (once review_ready.py lands) is
# tests/seg_safety_positional.test.py's own dedicated job; this file's spot
# -check exists only to prove review_ready.py's specific behavior (exit 2,
# "segment id" on stderr, no stdout, no file I/O before the check).
MALICIOUS_SEGS_SPOT_CHECK = [
    "../../etc/passwd",
    "seg;rm -rf x",
    "",
    "seg01\n",
    "FRONTBACK:../x",
]
for _s in MALICIOUS_SEGS_SPOT_CHECK:
    assert _s in _SEG_SAFETY_MODULE.MALICIOUS_SEGS, (
        f"{_s!r} must be part of the SHARED MALICIOUS_SEGS vocabulary in "
        f"seg_safety_positional.test.py -- keep this spot-check subset in "
        f"sync rather than letting it drift from the canonical list"
    )


@pytest.mark.parametrize("seg", MALICIOUS_SEGS_SPOT_CHECK, ids=repr)
def test_review_ready_rejects_malicious_seg_before_any_file_io(tmp_path, seg):
    """CONTRACT §4: review_ready.py must 'carry the byte-identical
    _SEG_ID_RE+validate_seg() from draft_ready.py' and 'call
    validate_seg(seg) FIRST'. This test proves the FIRST part behaviorally
    (matching draft_ready.py's own exit-2/stderr convention exactly) and
    the ordering claim structurally: NO segments/ or schemas/ directory is
    even created in this fixture, so a review_ready.py that tried to
    build/read any path before validating seg would fail with a
    DIFFERENT error shape (e.g. a schema-file-not-found error) than the
    clean seg-validation failure asserted below -- and the seg-validation
    failure must print NOTHING to stdout, proving it never reached the
    {"ready": ...} readiness-check codepath at all."""
    assert REVIEW_READY_SRC.is_file(), (
        f"required NEW script missing: {REVIEW_READY_SRC} "
        f"(CONTRACT-1.2.0-reliability.md §4, Owner C) -- review_ready.py "
        f"has not landed yet, so this test cannot run. Not a bug in this test."
    )
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(REVIEW_READY_SRC, scripts_dir / "review_ready.py")
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    # Deliberately no segments/ dir, no schemas/ dir -- see docstring above.

    result = run_review_ready(root, seg, "placeholder-token-for-seg-safety-spot-check")
    assert result.returncode == 2, (
        f"a malicious/unsafe seg {seg!r} must be rejected with exit 2 "
        f"(usage error), got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "segment id" in result.stderr.lower(), (
        f"stderr must name the segment-id contract violation (matching "
        f"draft_ready.py's own 'Error: {{msg}}' convention), got:\n{result.stderr}"
    )
    assert result.stdout == "", (
        f"a seg-validation failure must print NOTHING to stdout (no "
        f"{{'ready': ...}} line) -- proving the failure happened before "
        f"the readiness-check codepath, got stdout:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# 6. #198 -- `--candidate-file`: the W5 codex_job.py driver FULLY validates an
#    isolated review attempt BEFORE promoting it to canonical. The option
#    overrides review_path(seg); `draft_sha1.py {seg}` STILL runs against the
#    CURRENT canonical draft (the review must reference the on-disk draft);
#    schema + --expect-token run against the candidate. Backward compatible:
#    absent option == today's canonical-path behavior.
# ---------------------------------------------------------------------------

def run_review_ready_candidate(root, seg, expect_token, candidate_file):
    return subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "review_ready.py"),
            seg,
            "--expect-token",
            expect_token,
            "--candidate-file",
            candidate_file,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_review_ready_candidate_file_valid_passes_without_canonical(tmp_path):
    """A schema-valid, sha1-fresh, token-matching review at a NON-canonical
    candidate path passes via --candidate-file even when NO canonical
    {seg}.review.json exists -- proving the option truly overrides
    review_path(seg). draft_sha1 is computed against the on-disk canonical
    draft (present here), proving that check still targets the canonical
    draft, not the candidate."""
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "seg01"
    token = "RUN1:seg01:r1"
    write_draft(segments_dir, seg, dispatch_token="RUN1:seg01")
    real_sha1 = real_draft_sha1(root, seg)
    candidate = segments_dir / f".att.{seg}.1.review.json"
    review = {
        "clean": True, "coverage_ok": True, "findings": [],
        "draft_sha1": real_sha1, "dispatch_token": token,
    }
    candidate.write_text(json.dumps(review), encoding="utf-8")
    # NO canonical {seg}.review.json -> proves the candidate override is read.

    result = run_review_ready_candidate(root, seg, token, str(candidate))

    assert result.returncode == 0, (
        f"a valid --candidate-file review must be READY even with no canonical "
        f"review on disk, got rc={result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert json.loads(result.stdout.strip()) == {"ready": True}


def test_review_ready_candidate_file_wrong_token_rejected(tmp_path):
    """The --expect-token check runs against the CANDIDATE: a review whose own
    dispatch_token differs from --expect-token must be reported not-ready,
    proving --candidate-file does not weaken the freshness gate."""
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "seg01"
    write_draft(segments_dir, seg, dispatch_token="RUN1:seg01")
    real_sha1 = real_draft_sha1(root, seg)
    candidate = segments_dir / f".att.{seg}.1.review.json"
    review = {
        "clean": True, "coverage_ok": True, "findings": [],
        "draft_sha1": real_sha1, "dispatch_token": "RUN2:seg01:r1",
    }
    candidate.write_text(json.dumps(review), encoding="utf-8")

    result = run_review_ready_candidate(root, seg, "RUN1:seg01:r1", str(candidate))

    assert result.returncode == 1, (
        f"a wrong-token --candidate-file review must be not-ready, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["ready"] is False
    assert "token" in payload["reason"].lower()


def test_review_ready_candidate_file_schema_invalid_rejected(tmp_path):
    """The full-schema check runs against the CANDIDATE: a candidate missing a
    required field (`findings`) must be reported not-ready, naming the schema
    problem."""
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "seg01"
    token = "RUN1:seg01:r1"
    write_draft(segments_dir, seg, dispatch_token="RUN1:seg01")
    real_sha1 = real_draft_sha1(root, seg)
    candidate = segments_dir / f".att.{seg}.1.review.json"
    review = {  # 'findings' omitted -> schema-invalid
        "clean": True, "coverage_ok": True,
        "draft_sha1": real_sha1, "dispatch_token": token,
    }
    candidate.write_text(json.dumps(review), encoding="utf-8")

    result = run_review_ready_candidate(root, seg, token, str(candidate))

    assert result.returncode == 1, (
        f"a schema-invalid --candidate-file review must be not-ready, got rc="
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["ready"] is False
    assert "findings" in payload["reason"].lower()


def test_review_ready_candidate_file_absent_uses_canonical(tmp_path):
    """Backward compatibility: with --candidate-file ABSENT, review_ready.py
    reads the canonical {seg}.review.json and ignores any stray attempt file.
    A BROKEN candidate file at the isolated-attempt path must NOT affect the
    result when the flag is not passed."""
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "seg01"
    token = "RUN1:seg01:r1"
    write_draft(segments_dir, seg, dispatch_token="RUN1:seg01")
    real_sha1 = real_draft_sha1(root, seg)
    write_review(segments_dir, seg, draft_sha1_value=real_sha1, dispatch_token=token)
    # A broken attempt file that MUST be ignored when the flag is absent.
    (segments_dir / f".att.{seg}.1.review.json").write_text(
        "{ not valid json", encoding="utf-8"
    )

    result = run_review_ready(root, seg, token)  # no --candidate-file

    assert result.returncode == 0, (
        f"absent --candidate-file must read the canonical review unchanged, "
        f"got rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert json.loads(result.stdout.strip()) == {"ready": True}


# ---------------------------------------------------------------------------
# 7. --durable-root PATH (LT-409, post-review correction): an explicit,
#    caller-supplied DATA root (segments/schemas) -- REPLACES self-anchoring
#    for data when given. Deliberately does NOT redirect where the
#    draft_sha1.py sibling script is found -- that is --plugin-root's own,
#    independent concern (see the dedicated section below). Byte-identical
#    to today's self-anchored behavior for both when both flags are omitted.
# ---------------------------------------------------------------------------

def run_review_ready_from(script_path, seg, expect_token, *extra_args):
    return subprocess.run(
        [sys.executable, str(script_path), seg, "--expect-token", expect_token, *extra_args],
        capture_output=True, text=True, timeout=30,
    )


def test_durable_root_flag_absent_orphan_copy_fails_self_anchored(tmp_path):
    """Negative control: an orphan copy invoked WITHOUT --durable-root
    cannot succeed via self-anchoring alone. Asserts the SPECIFIC reason,
    not merely that some failure occurred: a bare "it failed" cannot
    distinguish this correct refusal from an unrelated crash, so a future
    defect that broke the orphan-copy path for the WRONG reason would pass
    this test silently.

    CORRECTED (this docstring previously claimed the reason was "no
    schemas/ dir to even load review.schema.json from" -- FALSE, and it was
    already false before this fix, not a regression this branch caused):
    review_ready.py's own main() (review_ready.py:319-349) checks the
    review FILE's presence/non-emptiness at line 332, strictly BEFORE it
    ever reaches `_load_review_schema()` at line 339 -- so the orphan
    location's missing segments/{seg}.review.json is what's actually
    reached first, never the missing schemas/ dir. Verified by running the
    orphan copy directly and reading its real stdout, not by re-reading the
    stale comment."""
    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "review_ready.py"
    shutil.copy2(REVIEW_READY_SRC, orphan_script)

    result = run_review_ready_from(orphan_script, "segRedirect", "tok")

    assert result.returncode == 1
    payload = json.loads(result.stdout.strip())
    assert payload["ready"] is False
    assert "review file absent/empty" in (payload.get("reason") or ""), (
        f"expected the orphan copy to fail specifically because its "
        f"self-anchored segments/segRedirect.review.json does not exist; "
        f"got a different reason: {payload}"
    )


def test_durable_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility: the ordinary in-place fixture (unchanged
    from every other test in this file), invoked with no
    --durable-root/--plugin-root at all, keeps behaving exactly as before."""
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "segNoFlag"
    token = "RUN1:segNoFlag:r1"
    write_draft(segments_dir, seg, dispatch_token="RUN1:segNoFlag")
    real_sha1 = real_draft_sha1(root, seg)
    write_review(segments_dir, seg, draft_sha1_value=real_sha1, dispatch_token=token)

    result = run_review_ready(root, seg, token)

    assert result.returncode == 0
    assert json.loads(result.stdout.strip()) == {"ready": True}


# ---------------------------------------------------------------------------
# 8. --plugin-root PATH (LT-409, post-review correction): the SECURITY
#    property this flag exists for. ${durable_root}/scripts/ is a Step-0a
#    copy the codex process can write to (codex_job.py grants --write over
#    the whole durable root), so a sibling script resolved FROM durable_root
#    could be a tampered copy validating itself. --plugin-root is a
#    SEPARATE, orthogonal input that must NEVER be derived from
#    --durable-root.
# ---------------------------------------------------------------------------

_TAMPERED_DRAFT_SHA1_SRC = "#!/usr/bin/env python3\nprint('0' * 40)\n"


def poison_durable_root_draft_sha1(root):
    """Overwrites the durable-root copy of draft_sha1.py with a stand-in
    for a codex-tampered script: it always prints a FIXED, wrong digest
    regardless of the real draft content, rather than silently faking a
    match -- so a test can tell whether THIS copy ran at all, in either
    direction."""
    (root / "scripts" / "draft_sha1.py").write_text(_TAMPERED_DRAFT_SHA1_SRC, encoding="utf-8")


def make_trusted_plugin_root(tmp_path, name="trusted_plugin_install"):
    """A SEPARATE physical location holding the REAL draft_sha1.py at the
    {plugin_root}/assets/scripts/ layout SKILL.md documents for the
    plugin-anchored scripts."""
    plugin_root = tmp_path / name
    plugin_scripts_dir = plugin_root / "assets" / "scripts"
    plugin_scripts_dir.mkdir(parents=True)
    shutil.copy2(DRAFT_SHA1_SRC, plugin_scripts_dir / "draft_sha1.py")
    return plugin_root


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling(tmp_path):
    """review_ready.py runs from its OWN in-place durable-root copy
    (production's normal invocation shape) whose SIBLING draft_sha1.py has
    been POISONED to always print a fixed, wrong digest. --plugin-root
    pointing at a separate, untampered location must make it correctly
    report READY for a genuinely fresh review -- success is possible ONLY
    if the poisoned durable-root sibling was never executed."""
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "segTamper"
    token = "RUN1:segTamper:r1"
    write_draft(segments_dir, seg, dispatch_token="RUN1:segTamper")
    real_sha1 = real_draft_sha1(root, seg)
    write_review(segments_dir, seg, draft_sha1_value=real_sha1, dispatch_token=token)
    poison_durable_root_draft_sha1(root)

    plugin_root = make_trusted_plugin_root(tmp_path)

    result = run_review_ready_from(
        root / "scripts" / "review_ready.py", seg, token, "--plugin-root", str(plugin_root)
    )

    assert result.returncode == 0, (
        f"--plugin-root must bypass the poisoned durable-root draft_sha1.py "
        f"and use the trusted copy instead -- got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert json.loads(result.stdout.strip()) == {"ready": True}


def test_plugin_root_flag_absent_uses_the_poisoned_durable_root_sibling(tmp_path):
    """Negative control, and backward-compat proof in one: the SAME
    poisoned durable-root draft_sha1.py, invoked WITHOUT --plugin-root, is
    exactly what today's self-anchored lookup finds -- unchanged. It
    genuinely runs and produces a mismatch, proving the positive test's
    success above is attributable to --plugin-root specifically."""
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "segTamper2"
    token = "RUN1:segTamper2:r1"
    write_draft(segments_dir, seg, dispatch_token="RUN1:segTamper2")
    real_sha1 = real_draft_sha1(root, seg)
    write_review(segments_dir, seg, draft_sha1_value=real_sha1, dispatch_token=token)
    poison_durable_root_draft_sha1(root)

    result = run_review_ready(root, seg, token)  # no --plugin-root

    assert result.returncode == 1
    payload = json.loads(result.stdout.strip())
    assert payload["ready"] is False
    assert "sha" in payload["reason"].lower()


def test_durable_root_and_plugin_root_are_independently_resolved(tmp_path):
    """Orthogonality, end to end, from a fully orphan copy: --durable-root
    points at a DATA-only fixture with NO scripts/ directory AT ALL,
    --plugin-root points at a SEPARATE, scripts-only fixture with no data
    of its own. Success proves the two concerns are genuinely resolved
    independently, never conflated into one root."""
    data_root = tmp_path / "data_only"
    segments_dir = data_root / "segments"
    schemas_dir = data_root / "schemas"
    segments_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    shutil.copy2(REVIEW_SCHEMA_SRC, schemas_dir / "review.schema.json")
    seg = "segOrtho"
    token = "RUN1:segOrtho:r1"
    write_draft(segments_dir, seg, dispatch_token="RUN1:segOrtho")

    plugin_root = make_trusted_plugin_root(tmp_path, name="plugin_only")
    # Compute the real sha1 via the TRUSTED draft_sha1.py directly (there is
    # no scripts/ dir under data_root at all to run it from there).
    sha1_proc = subprocess.run(
        [
            sys.executable,
            str(plugin_root / "assets" / "scripts" / "draft_sha1.py"),
            seg,
            "--durable-root", str(data_root),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert sha1_proc.returncode == 0, sha1_proc.stderr
    real_sha1 = sha1_proc.stdout.strip()
    write_review(segments_dir, seg, draft_sha1_value=real_sha1, dispatch_token=token)
    assert not (data_root / "scripts").exists()

    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "review_ready.py"
    shutil.copy2(REVIEW_READY_SRC, orphan_script)

    result = run_review_ready_from(
        orphan_script,
        seg,
        token,
        "--durable-root", str(data_root),
        "--plugin-root", str(plugin_root),
    )

    assert result.returncode == 0, (
        f"durable-root (data) and plugin-root (sibling) must resolve "
        f"independently -- got rc={result.returncode}\nstdout:\n"
        f"{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert json.loads(result.stdout.strip()) == {"ready": True}


def test_plugin_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility for the split itself: --durable-root alone
    (no --plugin-root) still resolves the sibling self-anchored, exactly as
    before the split."""
    root = make_review_ready_root(tmp_path)
    segments_dir = root / "segments"
    seg = "segNoFlag2"
    token = "RUN1:segNoFlag2:r1"
    write_draft(segments_dir, seg, dispatch_token="RUN1:segNoFlag2")
    real_sha1 = real_draft_sha1(root, seg)
    write_review(segments_dir, seg, draft_sha1_value=real_sha1, dispatch_token=token)

    result = run_review_ready_from(
        root / "scripts" / "review_ready.py", seg, token, "--durable-root", str(root)
    )

    assert result.returncode == 0
    assert json.loads(result.stdout.strip()) == {"ready": True}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
