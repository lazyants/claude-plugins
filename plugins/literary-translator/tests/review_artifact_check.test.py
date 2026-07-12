"""tests/review_artifact_check.test.py -- regression-lock suite for
scripts/review_artifact_check.py, the review-artifact gate's deterministic
core (see references/workflow-schema-validation.md, "The review-artifact
gate", and the plan's §13 "Review-artifact gate" narrative).

Each test builds an isolated durable_root fixture (the REAL
review_artifact_check.py copied into {root}/scripts/, so its
Path(__file__)-based self-anchoring resolves against the isolated root
rather than this repo's real assets/scripts directory) plus a
segments/{seg}.review.json "on-disk artifact" and an --expected-file
scratch file, then invokes the script exactly as production does:

    python3 {durable_root}/scripts/review_artifact_check.py {seg} \
        --expected-file <path>

Covers:
  - canonicalizing review_path(seg) and the written --expected-file
    (sorted-key JSON) and byte-comparing them -- match:true regardless of
    on-disk key order/whitespace, match:false with a named mismatch_detail
    otherwise (scalar diff, missing key, array-length diff);
  - genuine script-level failures (missing file, invalid JSON, unsafe seg)
    -- named stderr line, non-zero exit, no {"match": ...} line printed;
  - the two documented residual-risk cases named in the build-order list:
      1. a stale-vs-fresh mismatch (one artifact updated, the other not)
         -- the gate MUST catch this as match:false;
      2. both-stale-but-agreeing -- both artifacts are stale relative to
         the TRUE current revObj, but byte-identical to EACH OTHER, so the
         gate reports match:true. This is not a bug: it is the documented,
         intentional limit of what a byte-comparison gate can prove (it
         proves two writes agree with each other, never that either one is
         the true revObj an LLM agent was supposed to write verbatim).
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SRC = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "scripts"
    / "review_artifact_check.py"
)

assert SCRIPT_SRC.is_file(), f"review_artifact_check.py not found at {SCRIPT_SRC}"

# A realistic review.schema.json-shaped revObj: {clean, coverage_ok,
# findings: [...], draft_sha1} -- matches what reviewPrompt actually writes
# to review_path(seg) and what verifyReviewArtifactPrompt splices, verbatim,
# into --expected-file.
FRESH_REVOBJ = {
    "clean": True,
    "coverage_ok": True,
    "findings": [],
    "draft_sha1": "aa11bb22cc33dd44ee55ff6677889900aabbccdd",
}

STALE_REVOBJ = {
    "clean": False,
    "coverage_ok": True,
    "findings": [
        {
            "loc": "p3",
            "severity": "minor",
            "issue": "awkward phrasing",
            "suggest": "rephrase for flow",
        }
    ],
    "draft_sha1": "00112233445566778899aabbccddeeff0011223",
}


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path):
    """Build an isolated durable_root: copies the REAL
    review_artifact_check.py into {root}/scripts/ (so its self-anchoring
    `Path(__file__).resolve().parents[1]` resolves to THIS temp root,
    exactly matching how it is actually invoked in production -- never
    assumes cwd == durable_root, never takes a --durable-root flag) and
    creates segments/. review_artifact_check.py reads no profile.yml and
    checks no ownership marker, so neither is needed here."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SRC, scripts_dir / "review_artifact_check.py")
    (root / "segments").mkdir()
    return root


def write_review_artifact(root, seg, obj, indent=None):
    """Writes the on-disk review_path(seg) = segments/{seg}.review.json."""
    path = root / "segments" / f"{seg}.review.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    return path


def write_expected_file(tmp_path, obj, name="expected.json", indent=None):
    """Writes the --expected-file scratch file the calling agent is
    instructed to write revObj's canonical-JSON text to, verbatim."""
    path = tmp_path / name
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    return path


def run_check(root, seg, expected_file_path):
    return subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "review_artifact_check.py"),
            seg,
            "--expected-file",
            str(expected_file_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def parse_match_line(stdout):
    """The script prints exactly one JSON line on stdout for every normal
    (match:true or match:false) outcome."""
    lines = [l for l in stdout.splitlines() if l.strip()]
    assert len(lines) == 1, f"expected exactly one stdout line, got:\n{stdout!r}"
    return json.loads(lines[0])


# ---------------------------------------------------------------------------
# 1. Canonicalization: sorted-key JSON, byte-for-byte compare -- key order
#    and whitespace formatting on disk must never, by themselves, count as
#    a mismatch.
# ---------------------------------------------------------------------------

def test_match_true_identical_content_different_key_order_and_whitespace(tmp_path):
    root = make_durable_root(tmp_path)
    write_review_artifact(root, "seg01", FRESH_REVOBJ, indent=None)  # compact

    # Same semantic content, reversed key order, pretty-printed -- proves the
    # comparison is over PARSED, sorted-key-canonicalized values, not raw
    # bytes of either file as originally written.
    reordered = {
        "draft_sha1": FRESH_REVOBJ["draft_sha1"],
        "findings": [],
        "coverage_ok": True,
        "clean": True,
    }
    expected_path = write_expected_file(tmp_path, reordered, indent=2)

    result = run_check(root, "seg01", expected_path)

    assert result.returncode == 0, (
        f"expected match, got rc={result.returncode}\nstderr:\n{result.stderr}"
    )
    assert parse_match_line(result.stdout) == {"match": True}


# ---------------------------------------------------------------------------
# 2. match:false -- scalar field differs, mismatch_detail names the field
#    and both values.
# ---------------------------------------------------------------------------

def test_match_false_scalar_diff_names_field_and_values(tmp_path):
    root = make_durable_root(tmp_path)
    write_review_artifact(root, "seg01", FRESH_REVOBJ)

    mutated = dict(FRESH_REVOBJ)
    mutated["clean"] = False  # the only change from the on-disk artifact
    expected_path = write_expected_file(tmp_path, mutated)

    result = run_check(root, "seg01", expected_path)

    # A mismatch is a NORMAL, expected outcome -- exit 0, not a script failure.
    assert result.returncode == 0, (
        f"match:false must still exit 0, got rc={result.returncode}\n"
        f"stderr:\n{result.stderr}"
    )
    payload = parse_match_line(result.stdout)
    assert payload["match"] is False
    detail = payload["mismatch_detail"]
    assert "clean" in detail
    assert "true" in detail and "false" in detail


# ---------------------------------------------------------------------------
# 3. match:false -- a key present on one side is entirely missing on the
#    other (e.g. an older/newer revObj shape), mismatch_detail names which
#    key and which side has it.
# ---------------------------------------------------------------------------

def test_match_false_missing_key_names_which_side(tmp_path):
    root = make_durable_root(tmp_path)
    disk_obj = {"clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "x"}
    write_review_artifact(root, "seg01", disk_obj)

    expected_obj = {"clean": True, "coverage_ok": True, "findings": []}  # draft_sha1 missing
    expected_path = write_expected_file(tmp_path, expected_obj)

    result = run_check(root, "seg01", expected_path)

    assert result.returncode == 0
    payload = parse_match_line(result.stdout)
    assert payload["match"] is False
    detail = payload["mismatch_detail"]
    assert "draft_sha1" in detail
    assert "missing from expected-file" in detail


# ---------------------------------------------------------------------------
# 4. match:false -- findings[] array length differs.
# ---------------------------------------------------------------------------

def test_match_false_findings_array_length_differs(tmp_path):
    root = make_durable_root(tmp_path)
    disk_obj = dict(
        FRESH_REVOBJ,
        findings=[{"loc": "p1", "severity": "minor", "issue": "x", "suggest": "y"}],
    )
    write_review_artifact(root, "seg01", disk_obj)

    expected_obj = dict(FRESH_REVOBJ, findings=[])
    expected_path = write_expected_file(tmp_path, expected_obj)

    result = run_check(root, "seg01", expected_path)

    assert result.returncode == 0
    payload = parse_match_line(result.stdout)
    assert payload["match"] is False
    detail = payload["mismatch_detail"]
    assert "findings" in detail
    assert "array length differs" in detail
    assert "on-disk=1" in detail
    assert "expected-file=0" in detail


# ---------------------------------------------------------------------------
# 5. Genuine script-level failures: missing files, invalid JSON. Never a
#    {"match": ...}-shaped line, always a named stderr line, non-zero exit.
# ---------------------------------------------------------------------------

def test_error_review_artifact_missing(tmp_path):
    root = make_durable_root(tmp_path)
    expected_path = write_expected_file(tmp_path, FRESH_REVOBJ)
    # segments/seg01.review.json is never written.

    result = run_check(root, "seg01", expected_path)

    assert result.returncode != 0
    assert result.stdout.strip() == "", (
        f"a script-level failure must never print a match-shaped line, got:\n"
        f"{result.stdout!r}"
    )
    assert result.stderr.startswith("Error:")
    assert "not found" in result.stderr


def test_error_expected_file_missing(tmp_path):
    root = make_durable_root(tmp_path)
    write_review_artifact(root, "seg01", FRESH_REVOBJ)
    missing_expected = tmp_path / "does_not_exist.json"

    result = run_check(root, "seg01", missing_expected)

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert result.stderr.startswith("Error:")
    assert "Expected-file" in result.stderr
    assert "not found" in result.stderr


def test_error_review_artifact_invalid_json(tmp_path):
    root = make_durable_root(tmp_path)
    (root / "segments" / "seg01.review.json").write_text("{not valid json", encoding="utf-8")
    expected_path = write_expected_file(tmp_path, FRESH_REVOBJ)

    result = run_check(root, "seg01", expected_path)

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "not valid JSON" in result.stderr


def test_error_expected_file_invalid_json(tmp_path):
    root = make_durable_root(tmp_path)
    write_review_artifact(root, "seg01", FRESH_REVOBJ)
    expected_path = tmp_path / "expected.json"
    expected_path.write_text("[1, 2,", encoding="utf-8")

    result = run_check(root, "seg01", expected_path)

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "not valid JSON" in result.stderr


# ---------------------------------------------------------------------------
# 6. Unsafe seg values -- review_path(seg) must never be allowed to escape
#    segments/ (pathlib's `/` operator silently discards the SEGMENTS_DIR
#    prefix for an absolute seg). Each check must fire BEFORE any file I/O.
# ---------------------------------------------------------------------------

def test_error_seg_absolute_path(tmp_path):
    root = make_durable_root(tmp_path)
    expected_path = write_expected_file(tmp_path, FRESH_REVOBJ)

    result = run_check(root, "/etc/passwd", expected_path)

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "absolute path" in result.stderr


def test_error_seg_path_separator(tmp_path):
    root = make_durable_root(tmp_path)
    expected_path = write_expected_file(tmp_path, FRESH_REVOBJ)

    result = run_check(root, "foo/bar", expected_path)

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "path separator" in result.stderr


def test_error_seg_dotdot_component(tmp_path):
    root = make_durable_root(tmp_path)
    expected_path = write_expected_file(tmp_path, FRESH_REVOBJ)

    result = run_check(root, "..", expected_path)

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "'..' path component" in result.stderr


def test_error_seg_empty(tmp_path):
    root = make_durable_root(tmp_path)
    expected_path = write_expected_file(tmp_path, FRESH_REVOBJ)

    result = run_check(root, "", expected_path)

    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "must not be empty" in result.stderr


# ---------------------------------------------------------------------------
# 7. Documented residual-risk case #1: a STALE-vs-FRESH mismatch -- one
#    artifact was updated (e.g. a fresh review retry), the other was not.
#    The gate MUST catch this: it is exactly the drift the review-artifact
#    gate exists to detect (references/workflow-schema-validation.md /
#    plan §13 "Review-artifact gate").
# ---------------------------------------------------------------------------

def test_stale_vs_fresh_mismatch_is_detected(tmp_path):
    """Simulates: reviewPrompt's earlier write left a STALE revObj sitting at
    review_path(seg) (e.g. from a round that produced findings), but the
    verifyReviewArtifactPrompt call under test is splicing a FRESH, updated
    revObj (e.g. a subsequent retry's clean result) into --expected-file --
    one artifact updated, the other not. This is a real, catchable drift:
    the gate must report match:false so the caller retries the review call
    once more and, if it still mismatches, goes `blocked` with reason
    `review-artifact-mismatch`."""
    root = make_durable_root(tmp_path)
    write_review_artifact(root, "seg01", STALE_REVOBJ)  # never refreshed on disk
    expected_path = write_expected_file(tmp_path, FRESH_REVOBJ)  # the CURRENT revObj

    result = run_check(root, "seg01", expected_path)

    assert result.returncode == 0
    payload = parse_match_line(result.stdout)
    assert payload["match"] is False
    assert isinstance(payload.get("mismatch_detail"), str) and payload["mismatch_detail"]


# ---------------------------------------------------------------------------
# 8. Documented residual-risk case #2: BOTH artifacts stale, but agreeing
#    (byte-identical to EACH OTHER). This is the KNOWN, ACCEPTED residual
#    limit called out in review_artifact_check.py's own module docstring
#    and the plan's §13 narrative: --expected-file is written by an LLM
#    agent, not the JS or this script -- if that agent ever writes something
#    OTHER than the true current revObj to BOTH review_path(seg) (as
#    reviewPrompt's side effect) and --expected-file (verifyReviewArtifactPrompt's
#    own scratch write), the deterministic byte-comparison still "correctly"
#    reports match:true. This test locks in that this is the SCRIPT'S actual,
#    intentional behavior -- it proves two writes agree with each other, never
#    that either one is the true revObj -- and must NOT be "fixed" by weakening
#    it into a false failure.
# ---------------------------------------------------------------------------

def test_both_stale_but_agreeing_reports_match_true(tmp_path):
    root = make_durable_root(tmp_path)
    write_review_artifact(root, "seg01", STALE_REVOBJ)
    expected_path = write_expected_file(tmp_path, STALE_REVOBJ)  # same stale content

    result = run_check(root, "seg01", expected_path)

    assert result.returncode == 0
    payload = parse_match_line(result.stdout)
    # This match:true does NOT prove STALE_REVOBJ is the TRUE current revObj --
    # only that the two on-disk artifacts agree with each other. That gap is
    # confined to the LATER ledger-binding/audit-trail question (see
    # ledger_update.py's reviewed_draft_sha1 check), never a
    # wrong-findings-reach-the-fix-step question -- 1.3.6 (#132 option b):
    # fixPrompt now reads review_path(seg) itself, fresh and token-validated
    # by review_ready.py this same round, independent of whatever this
    # script's own compare concluded here.
    assert payload == {"match": True}


# ---------------------------------------------------------------------------
# 9. 1.3.6 (#132): the per-finding compare projects each findings[] element
#    down to {loc, severity}, dropping the free-text issue/suggest bodies --
#    review_ready.py already guarantees the on-disk artifact is schema-valid,
#    draft_sha1-fresh, and dispatch_token-matched by the time this script
#    runs, so a transcription slip confined to prose must no longer
#    terminal-block a valid review. The structural binding this compare
#    keeps -- loc, severity, and the findings array's own length (already
#    covered by test 4 above) -- is no longer what protects the fixer
#    (option b: fixPrompt reads review_path(seg) itself for that); it now
#    protects ledger_update.py's later reviewed_draft_sha1/dispatch_token
#    binding check and audit-trail inspection instead, so it must still
#    catch a real divergence.
# ---------------------------------------------------------------------------

def test_match_true_findings_differ_only_in_issue_suggest_text(tmp_path):
    """Two verdicts agree on clean/coverage_ok/draft_sha1 and on every
    finding's loc+severity, but the free-text issue/suggest bodies differ --
    an immaterial transcription slip in prose, not a structural divergence.
    #132 narrows the compare so this no longer reports match:false."""
    root = make_durable_root(tmp_path)
    disk_obj = dict(
        FRESH_REVOBJ,
        clean=False,
        findings=[
            {
                "loc": "PARA:seg01:0001",
                "severity": "minor",
                "issue": "awkward phrasing here",
                "suggest": "rephrase for flow and rhythm",
            }
        ],
    )
    write_review_artifact(root, "seg01", disk_obj)

    expected_obj = dict(
        FRESH_REVOBJ,
        clean=False,
        findings=[
            {
                "loc": "PARA:seg01:0001",
                "severity": "minor",
                "issue": "phrasing feels off",  # different prose, same finding
                "suggest": "consider a smoother rendering",  # different prose
            }
        ],
    )
    expected_path = write_expected_file(tmp_path, expected_obj)

    result = run_check(root, "seg01", expected_path)

    assert result.returncode == 0, (
        f"expected match, got rc={result.returncode}\nstderr:\n{result.stderr}"
    )
    assert parse_match_line(result.stdout) == {"match": True}


def test_match_false_findings_differ_in_loc(tmp_path):
    """A real loc divergence (a slipped/fabricated/misattributed finding)
    must still fail the compare -- the free-text projection narrows nothing
    about the structural loc/severity binding."""
    root = make_durable_root(tmp_path)
    disk_obj = dict(
        FRESH_REVOBJ,
        clean=False,
        findings=[{"loc": "PARA:seg01:0001", "severity": "minor", "issue": "x", "suggest": "y"}],
    )
    write_review_artifact(root, "seg01", disk_obj)

    expected_obj = dict(
        FRESH_REVOBJ,
        clean=False,
        findings=[{"loc": "PARA:seg01:0002", "severity": "minor", "issue": "x", "suggest": "y"}],
    )
    expected_path = write_expected_file(tmp_path, expected_obj)

    result = run_check(root, "seg01", expected_path)

    assert result.returncode == 0
    payload = parse_match_line(result.stdout)
    assert payload["match"] is False
    assert "loc" in payload["mismatch_detail"]


def test_match_false_findings_differ_in_severity(tmp_path):
    """A real severity divergence must still fail the compare."""
    root = make_durable_root(tmp_path)
    disk_obj = dict(
        FRESH_REVOBJ,
        clean=False,
        findings=[{"loc": "PARA:seg01:0001", "severity": "minor", "issue": "x", "suggest": "y"}],
    )
    write_review_artifact(root, "seg01", disk_obj)

    expected_obj = dict(
        FRESH_REVOBJ,
        clean=False,
        findings=[{"loc": "PARA:seg01:0001", "severity": "major", "issue": "x", "suggest": "y"}],
    )
    expected_path = write_expected_file(tmp_path, expected_obj)

    result = run_check(root, "seg01", expected_path)

    assert result.returncode == 0
    payload = parse_match_line(result.stdout)
    assert payload["match"] is False
    assert "severity" in payload["mismatch_detail"]


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
