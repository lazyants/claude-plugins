"""tests/review_rejection_consumer_warning.test.py -- #859: a rejection
written for a segment whose unit has left the default dispatch set is
consumed by nobody, and the operator who runs reject_review.py against it
sees only {"success": true} -- five times in a row, if they retry.

This covers ONLY the advisory reject_review.py's `main()` now adds to both
rejection-path success envelopes: `consumer_warning` (a string when the
materialized ledger at ${durable_root}/runs/ledger.json reports this
segment's status in {"blocked", "non_converged"}, else null) and
`consumer_warning_problem` (a string when that status could not be
established, else null). It is a SEPARATE file from
tests/review_rejection.test.py for organization -- that file is already
3000+ lines -- not for isolation: every root here is built fresh under this
test's own tmp_path, exactly like that file's own lightweight fixture.

The harness below (make_reject_review_root, write_review_lite,
run_reject_review, the REJECT_MOD module load) is COPIED from
tests/review_rejection.test.py rather than imported, per this project's
self-contained-test-files convention (see that file's own module docstring,
and tests/claim_selector.test.py's for the rule stated explicitly): no
cross-test imports between sibling test files.

THREE CASES A NAIVE IMPLEMENTATION WOULD PASS WITHOUT SATISFYING #859 AT
ALL, so each gets its own test rather than folding into a general case:

  * the already-recorded no-op branch (reject_review.py's gate 6, the exact
    branch five idempotent re-runs actually take) could return null/null
    while the fresh-write branch is warned correctly -- see
    test_the_same_warning_appears_on_the_fresh_write_the_no_op_and_the_renewal
    and test_the_problem_string_fallback_also_applies_to_the_already_recorded_no_op;
  * catching only (OSError, json.JSONDecodeError) around the ledger read
    passes every parametrized case in test_the_advisory_never_gates (a
    directory raises IsADirectoryError, an OSError subclass, and is in any
    case intercepted earlier by the non-regular-path check) and fails only
    on invalid UTF-8 bytes, which raise UnicodeDecodeError -- see
    test_a_unicode_decode_error_is_caught_by_the_same_broad_boundary;
  * reading module-level DURABLE_ROOT instead of dirs["durable_root"]
    passes every case above, because the harness always stages and runs the
    script inside its OWN data root -- see
    test_the_durable_root_flag_wins_over_the_scripts_own_directory, the one
    test with distinct script and data roots carrying conflicting ledger
    statuses.
"""
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths -- copied from tests/review_rejection.test.py
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC_DIR = ASSETS_DIR / "schemas"

REJECT_REVIEW_SRC = SCRIPTS_SRC_DIR / "reject_review.py"
CLAIM_RECORD_SRC = SCRIPTS_SRC_DIR / "claim_record.py"
JSON_STDOUT_SRC = SCRIPTS_SRC_DIR / "json_stdout.py"
REVIEW_SCHEMA_SRC = SCHEMAS_SRC_DIR / "review.schema.json"

for _src in (REJECT_REVIEW_SRC, CLAIM_RECORD_SRC, JSON_STDOUT_SRC, REVIEW_SCHEMA_SRC):
    assert _src.is_file(), f"expected script/asset not found: {_src}"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Loaded once for its own REJECTION_RECORD_KEYS constant -- never to bypass
# the CLI, which every test below still drives as a real subprocess.
REJECT_MOD = _load_module(REJECT_REVIEW_SRC, "reject_review_consumer_warning_pure")


# ---------------------------------------------------------------------------
# Lightweight fixture -- copied byte-for-byte in shape from
# tests/review_rejection.test.py's own make_reject_review_root /
# write_review_lite / run_reject_review.
# ---------------------------------------------------------------------------

def make_reject_review_root(tmp_path, name="reject_review_root"):
    """A durable root holding only what reject_review.py itself resolves."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    segments_dir = root / "segments"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    segments_dir.mkdir(parents=True)
    shutil.copy2(REJECT_REVIEW_SRC, scripts_dir / "reject_review.py")
    shutil.copy2(JSON_STDOUT_SRC, scripts_dir / "json_stdout.py")
    shutil.copy2(CLAIM_RECORD_SRC, scripts_dir / "claim_record.py")
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
# This file's own additions: the operator sequence and the ledger fixture.
# ---------------------------------------------------------------------------

_DEFAULT_REASON = "verified: the claimed source string occurs zero times"


def _reject(root, seg, *, reason=_DEFAULT_REASON, durable_root=None):
    """Drives the REAL operator sequence: read the verdict via
    --print-verdict-digest, then reject using exactly what that read
    returned -- mirroring tests/review_rejection.test.py's own renewal
    test. `durable_root`, when given, is passed as --durable-root on BOTH
    calls, so the rejection is driven against a data root distinct from
    wherever the script itself is staged."""
    extra = ("--durable-root", str(durable_root)) if durable_root is not None else ()
    read = run_reject_review(root, seg, extra_args=(*extra, "--print-verdict-digest"))
    assert read.returncode == 0, read.stdout + read.stderr
    printed = json.loads(read.stdout.strip())
    return run_reject_review(
        root, seg, reason=reason, round_label=printed["round_label"],
        expect_token=printed["dispatch_token"], expect_digest=printed["verdict_digest"],
        extra_args=extra,
    )


def _write_review(segments_dir, seg):
    return write_review_lite(
        segments_dir, seg, clean=False, coverage_ok=True,
        findings=[{"loc": "p1:1", "severity": "major", "issue": "x", "suggest": "y"}],
    )


def _int_constant(filename, name):
    """Value of a module-level `NAME = <int literal>` in a SHIPPED script, read
    by AST rather than executed or re-typed here. Mirrors
    tests/changelog_figures.test.py's own helper, copied per this project's
    self-contained-test convention. Restating the number in this file would
    make the assertion agree with itself instead of with the code: the bound
    the script actually applies is the only thing worth asserting against."""
    tree = ast.parse((SCRIPTS_SRC_DIR / filename).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        return int(ast.literal_eval(node.value))
    raise AssertionError(f"no module-level int constant {name!r} in {filename}")


def write_ledger(durable_root, content):
    """Writes $durable_root/runs/ledger.json as JSON. `content` is any
    JSON-serializable value, deliberately not typed to dict -- several
    tests below need a top-level shape reject_review.py must refuse to
    trust rather than crash on."""
    runs_dir = durable_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / "ledger.json"
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return path


def ledger_record(status, reason=None):
    record = {"status": status}
    if reason is not None:
        record["reason"] = reason
    return record


# ---------------------------------------------------------------------------
# 1. A capped segment is warned, and the warning states the default-dispatch
#    exclusion in its own words.
# ---------------------------------------------------------------------------

def test_a_capped_segment_is_warned_with_the_default_dispatch_exclusion(tmp_path):
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    _write_review(root / "segments", seg)
    write_ledger(root, {"segments": {seg: ledger_record("non_converged", reason="cap")}})

    result = _reject(root, seg)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is True
    warning = payload["consumer_warning"]
    assert warning is not None
    assert "outside" in warning and "dispatch" in warning.lower(), (
        f"must state the default-dispatch exclusion, not merely a status name: {warning!r}"
    )
    assert "no ordinary driver invocation reads this record" in warning, (
        f"must state that nothing consumes the record as things stand -- a message "
        f"naming the status/flag without this sentence would satisfy a name-only "
        f"assertion and fail #859's actual outcome: {warning!r}"
    )
    assert payload["consumer_warning_problem"] is None


# ---------------------------------------------------------------------------
# 2. The SAME content on all three success outcomes: fresh write,
#    already-recorded no-op, and renewal -- the case #859 actually reports.
# ---------------------------------------------------------------------------

def test_the_same_warning_appears_on_the_fresh_write_the_no_op_and_the_renewal(tmp_path):
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    _write_review(root / "segments", seg)
    write_ledger(root, {"segments": {seg: ledger_record("non_converged", reason="cap")}})

    seen = []

    def assert_warned(payload):
        warning = payload["consumer_warning"]
        assert warning is not None
        assert "no ordinary driver invocation reads this record" in warning
        # ONE ordered sentence binding all three, never three independent
        # substring probes: a control that swapped the two values -- "materializes
        # as 'cap' with reason 'non_converged'" -- satisfied every separate probe
        # while describing a state that cannot exist.
        assert (
            f"segment {seg!r} materializes as 'non_converged' with reason 'cap'"
            in warning
        ), warning
        assert payload["consumer_warning_problem"] is None
        seen.append(warning)

    fresh = _reject(root, seg)
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    fresh_payload = json.loads(fresh.stdout.strip())
    assert fresh_payload["renewed"] is False
    assert fresh_payload.get("already_recorded") is None
    assert_warned(fresh_payload)

    noop = _reject(root, seg)
    assert noop.returncode == 0, noop.stdout + noop.stderr
    noop_payload = json.loads(noop.stdout.strip())
    assert noop_payload.get("already_recorded") is True, (
        f"a repeat with the SAME reason over the SAME verdict must be the idempotent "
        f"no-op branch, not a fresh write: {noop_payload!r}"
    )
    assert_warned(noop_payload)

    # Force the tie that makes the recorded verdict SPENT (rule 8: strictly
    # newer, so a tie is not fresh) -- copied from
    # tests/review_rejection.test.py's own renewal test.
    rejection_path = root / "segments" / f"{seg}.review_rejected.json"
    review_path = root / "segments" / f"{seg}.review.json"
    tie_ns = rejection_path.stat().st_mtime_ns
    os.utime(review_path, ns=(tie_ns, tie_ns))

    renewed = _reject(root, seg)
    assert renewed.returncode == 0, renewed.stdout + renewed.stderr
    renewed_payload = json.loads(renewed.stdout.strip())
    assert renewed_payload["renewed"] is True, (
        f"the tie must produce a renewal, not another no-op: {renewed_payload!r}"
    )
    assert_warned(renewed_payload)

    # THE POINT OF THIS TEST, and the half an in-memory control defeated: three
    # warnings that each satisfy every clause above can still be three DIFFERENT
    # and individually wrong messages. Equality is what "the SAME warning on all
    # three outcomes" actually asserts.
    assert len(seen) == 3, seen
    assert seen[0] == seen[1] == seen[2], (
        f"the three success outcomes must carry the IDENTICAL warning, got {seen!r}"
    )


def test_the_problem_string_fallback_also_applies_to_the_already_recorded_no_op(tmp_path):
    """Both keys merely being PRESENT on the already-recorded branch would
    let an implementation return null/null there and preserve the defect
    for exactly the operator who hit it -- so the problem-string fallback,
    not only the warning, must reach this branch too."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    _write_review(root / "segments", seg)
    # No runs/ directory at all: the ledger has never been materialized.

    fresh = _reject(root, seg)
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    fresh_payload = json.loads(fresh.stdout.strip())
    assert fresh_payload["consumer_warning"] is None
    assert fresh_payload["consumer_warning_problem"] is not None

    noop = _reject(root, seg)
    assert noop.returncode == 0, noop.stdout + noop.stderr
    noop_payload = json.loads(noop.stdout.strip())
    assert noop_payload.get("already_recorded") is True
    assert noop_payload["consumer_warning"] is None
    assert noop_payload["consumer_warning_problem"] is not None


# ---------------------------------------------------------------------------
# 3. payload (the on-disk record) is untouched: exactly the pinned seven keys.
# ---------------------------------------------------------------------------

def test_the_on_disk_record_still_has_exactly_the_pinned_seven_keys(tmp_path):
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    _write_review(root / "segments", seg)
    write_ledger(root, {"segments": {seg: ledger_record("non_converged", reason="cap")}})

    result = _reject(root, seg)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["consumer_warning"] is not None

    on_disk = json.loads((root / "segments" / f"{seg}.review_rejected.json").read_text(encoding="utf-8"))
    assert set(on_disk) == REJECT_MOD.REJECTION_RECORD_KEYS, (
        f"the record on disk must stay exactly the pinned seven keys, got {sorted(on_disk)}"
    )
    assert "consumer_warning" not in on_disk
    assert "consumer_warning_problem" not in on_disk


# ---------------------------------------------------------------------------
# 4. A blocked segment is warned, and the text names no claim profile: none
#    of the three admits a blocked status.
# ---------------------------------------------------------------------------

def test_a_blocked_segment_is_warned_and_names_no_claim_profile(tmp_path):
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    _write_review(root / "segments", seg)
    write_ledger(root, {"segments": {seg: ledger_record("blocked")}})

    result = _reject(root, seg)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    warning = payload["consumer_warning"]
    assert warning is not None
    # The bare word "blocked" is satisfied by the warning being the string
    # "blocked", and naming no segment is satisfied by a warning about a
    # DIFFERENT one -- controls for both passed earlier drafts of this test.
    assert f"segment {seg!r} materializes as 'blocked'" in warning, warning
    assert "no ordinary driver invocation reads this record" in warning, warning
    assert "no claim profile admits a blocked segment" in warning, (
        f"the blocked branch must say WHY no route is named, not merely omit one: "
        f"{warning!r}"
    )
    for flag in ("--from-cap", "--from-converged", "--from-stalled"):
        assert flag not in warning, (
            f"a blocked segment has no claim route at all -- the text must not name "
            f"a remedy that does not exist: {warning!r}"
        )
    assert payload["consumer_warning_problem"] is None


# ---------------------------------------------------------------------------
# 5. A dispatch-eligible status gets no warning at all.
# ---------------------------------------------------------------------------

def test_an_in_progress_segment_gets_no_warning(tmp_path):
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    _write_review(root / "segments", seg)
    write_ledger(root, {"segments": {seg: ledger_record("in_progress")}})

    result = _reject(root, seg)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["consumer_warning"] is None
    assert payload["consumer_warning_problem"] is None


# ---------------------------------------------------------------------------
# 6. The advisory NEVER gates -- parametrized over every malformed ledger
#    shape this file's module docstring calls out.
# ---------------------------------------------------------------------------

def _setup_no_runs_directory(root, seg):
    pass


def _setup_ledger_json_absent(root, seg):
    (root / "runs").mkdir(parents=True)


def _setup_ledger_json_unparseable(root, seg):
    (root / "runs").mkdir(parents=True)
    (root / "runs" / "ledger.json").write_text("{not valid json", encoding="utf-8")


def _setup_segments_not_an_object(root, seg):
    write_ledger(root, {"segments": []})


def _setup_segment_record_not_an_object(root, seg):
    write_ledger(root, {"segments": {seg: []}})


def _setup_status_is_a_list(root, seg):
    write_ledger(root, {"segments": {seg: {"status": ["blocked"]}}})


def _setup_no_status_key(root, seg):
    write_ledger(root, {"segments": {seg: {}}})


def _setup_no_entry_for_this_seg(root, seg):
    write_ledger(root, {"segments": {"a_different_seg": {"status": "blocked"}}})


def _setup_ledger_json_is_a_directory(root, seg):
    (root / "runs").mkdir(parents=True)
    (root / "runs" / "ledger.json").mkdir()


def _setup_ledger_json_is_a_fifo(root, seg):
    (root / "runs").mkdir(parents=True)
    os.mkfifo(root / "runs" / "ledger.json")


def _setup_ledger_json_is_a_symlink_to_a_foreign_ledger(root, seg):
    """A symlink whose TARGET is a perfectly valid, project-shaped ledger saying
    this segment is capped. Without O_NOFOLLOW on the leaf, fstat() answers about
    the TARGET, S_ISREG passes, and that foreign ledger's own `reason` is copied
    verbatim into this command's success envelope -- reading outside the durable
    root and spoofing the advisory at once. The target is deliberately VALID and
    deliberately outside the root: a malformed one would be refused by the shape
    checks and prove nothing about following the link."""
    foreign = root.parent / "foreign_project" / "runs"
    foreign.mkdir(parents=True, exist_ok=True)
    foreign_ledger = foreign / "ledger.json"
    foreign_ledger.write_text(
        json.dumps({"segments": {seg: ledger_record("non_converged",
                                                    reason="FOREIGN-LEDGER-REASON")}}),
        encoding="utf-8",
    )
    ledger_path = root / "runs" / "ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.symlink_to(foreign_ledger)


def _setup_runs_directory_is_a_symlink_to_a_foreign_project(root, seg):
    """The same cross-root read one path component HIGHER. O_NOFOLLOW on the
    leaf pins only the final name, so pointing `runs/` itself at a foreign
    directory holding a valid project-shaped ledger reaches the identical
    boundary crossing. Pinned separately from the leaf case because the two are
    closed by different mechanisms and a single fix for one leaves the other
    live -- which is exactly how this one was found."""
    foreign = root.parent / "foreign_runs_dir"
    foreign.mkdir(parents=True, exist_ok=True)
    (foreign / "ledger.json").write_text(
        json.dumps({"segments": {seg: ledger_record("non_converged",
                                                    reason="FOREIGN-RUNS-DIR-REASON")}}),
        encoding="utf-8",
    )
    (root / "runs").symlink_to(foreign, target_is_directory=True)


_NEVER_GATES_CASES = [
    ("no_runs_directory", _setup_no_runs_directory),
    ("ledger_json_absent", _setup_ledger_json_absent),
    ("ledger_json_unparseable", _setup_ledger_json_unparseable),
    ("segments_not_an_object", _setup_segments_not_an_object),
    ("segment_record_not_an_object", _setup_segment_record_not_an_object),
    ("status_is_a_list", _setup_status_is_a_list),
    ("no_status_key", _setup_no_status_key),
    ("no_entry_for_this_seg", _setup_no_entry_for_this_seg),
    ("ledger_json_is_a_directory", _setup_ledger_json_is_a_directory),
    ("ledger_json_is_a_fifo", _setup_ledger_json_is_a_fifo),
    ("ledger_json_is_a_symlink", _setup_ledger_json_is_a_symlink_to_a_foreign_ledger),
    ("runs_directory_is_a_symlink", _setup_runs_directory_is_a_symlink_to_a_foreign_project),
]


@pytest.mark.parametrize("label,setup", _NEVER_GATES_CASES, ids=[c[0] for c in _NEVER_GATES_CASES])
def test_the_advisory_never_gates(tmp_path, label, setup):
    """Whatever shape the ledger is in, the rejection must still succeed and
    write the record -- the advisory must never become a second gate on top
    of load_rejectable_review()'s own six. Every case here reports
    consumer_warning null with a NON-null consumer_warning_problem, since
    none of them establishes a dispatch-eligible status either."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    _write_review(root / "segments", seg)
    setup(root, seg)

    result = _reject(root, seg)
    assert result.returncode == 0, (
        f"[{label}] the advisory must never gate the rejection: {result.stdout}{result.stderr}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is True
    assert (root / "segments" / f"{seg}.review_rejected.json").is_file(), (
        f"[{label}] the record must still be written"
    )
    assert payload["consumer_warning"] is None, f"[{label}]: {payload!r}"
    assert payload["consumer_warning_problem"] is not None, f"[{label}]: {payload!r}"


# ---------------------------------------------------------------------------
# 6b. The two bounds on content this script does not own, both read while the
#     rejection flock is held. Neither may refuse; each degrades in its own way.
# ---------------------------------------------------------------------------

def test_an_oversized_ledger_is_not_read_and_reports_a_problem(tmp_path):
    """The read is capped, so an arbitrarily large ledger cannot be loaded
    whole inside the lock. Over the cap the operator gets a problem string --
    the correct degradation for an advisory -- and the rejection still lands."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    _write_review(root / "segments", seg)
    limit = _int_constant("reject_review.py", "_LEDGER_READ_LIMIT_BYTES")
    ledger_path = root / "runs" / "ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    # A VALID ledger for this segment, padded past the cap with whitespace the
    # JSON grammar allows: the file is well-formed and says `non_converged`, so
    # the ONLY reason no warning comes back is the bound itself. Padding with
    # junk would have been indistinguishable from a parse failure.
    body = json.dumps({"segments": {seg: ledger_record("non_converged", reason="cap")}})
    ledger_path.write_bytes(body.encode("utf-8") + b" " * (limit + 1 - len(body)))
    assert ledger_path.stat().st_size > limit

    result = _reject(root, seg)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is True
    assert payload["consumer_warning"] is None
    assert payload["consumer_warning_problem"] is not None
    assert str(limit) in payload["consumer_warning_problem"]
    assert (root / "segments" / f"{seg}.review_rejected.json").is_file()


def test_an_oversized_reason_is_truncated_rather_than_echoed_whole(tmp_path):
    """`reason` is ledger content, and an unbounded one would put megabytes of
    it on stdout in a field nobody asked for. Truncated, not dropped: the
    warning still fires and still names the status."""
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    _write_review(root / "segments", seg)
    cap = _int_constant("reject_review.py", "_REASON_CLAUSE_LIMIT_CHARS")
    huge = "cap" + "x" * (cap * 50)
    write_ledger(root, {"segments": {seg: ledger_record("non_converged", reason=huge)}})

    result = _reject(root, seg)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    warning = payload["consumer_warning"]
    assert warning is not None
    assert "no ordinary driver invocation reads this record" in warning
    # BOUND TO THE SHIPPED CONSTANT, not merely to "shorter than the input".
    # Measured on the first draft of this test: the boilerplate is ~443 chars and
    # `huge` is ~10 000, so `len(warning) < len(huge)` passed for every cap up to
    # ~9 500 -- a regression widening 200 to 5 000 would have shipped green.
    assert huge not in warning, "the whole reason must not reach the envelope"
    assert repr(huge[:cap]) in warning, (
        f"the warning must quote exactly the first {cap} characters: {warning!r}"
    )
    assert repr(huge[:cap + 1]) not in warning, (
        f"and not one character more than {cap}: {warning!r}"
    )
    assert payload["consumer_warning_problem"] is None


# ---------------------------------------------------------------------------
# 7. A non-OSError, non-JSONDecodeError failure inside the boundary: this is
#    what proves the boundary is broad rather than a hand-picked tuple.
# ---------------------------------------------------------------------------

def test_a_unicode_decode_error_is_caught_by_the_same_broad_boundary(tmp_path):
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    _write_review(root / "segments", seg)
    (root / "runs").mkdir(parents=True)
    (root / "runs" / "ledger.json").write_bytes(b"\xff\xfe\x00 not valid utf-8")

    result = _reject(root, seg)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["success"] is True
    assert payload["consumer_warning"] is None
    assert payload["consumer_warning_problem"] is not None


# ---------------------------------------------------------------------------
# 8. The requested --durable-root wins over the script's own directory.
# ---------------------------------------------------------------------------

def test_the_durable_root_flag_wins_over_the_scripts_own_directory(tmp_path):
    script_root = make_reject_review_root(tmp_path, name="script_root")
    data_root = tmp_path / "data_root"
    (data_root / "segments").mkdir(parents=True)
    (data_root / "schemas").mkdir(parents=True)
    shutil.copy2(REVIEW_SCHEMA_SRC, data_root / "schemas" / "review.schema.json")
    seg = "seg01"
    _write_review(data_root / "segments", seg)

    # CONFLICTING statuses: the script's OWN self-anchored root says
    # dispatch-eligible; the requested --durable-root says capped.
    write_ledger(script_root, {"segments": {seg: ledger_record("in_progress")}})
    write_ledger(data_root, {"segments": {seg: ledger_record("non_converged", reason="cap")}})

    result = _reject(script_root, seg, durable_root=data_root)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    warning = payload["consumer_warning"]
    assert warning is not None, (
        f"the --durable-root ledger (non_converged/cap) must be the one consulted, "
        f"not the script's own directory (in_progress): {payload!r}"
    )
    assert "non_converged" in warning
    assert payload["consumer_warning_problem"] is None


# ---------------------------------------------------------------------------
# 9. --print-verdict-digest's envelope is untouched: exactly its documented
#    seven keys, re-asserted here as well as at
#    tests/review_rejection.test.py:1352.
# ---------------------------------------------------------------------------

def test_the_print_verdict_digest_envelope_is_unchanged(tmp_path):
    root = make_reject_review_root(tmp_path)
    seg = "seg01"
    _write_review(root / "segments", seg)
    write_ledger(root, {"segments": {seg: ledger_record("non_converged", reason="cap")}})

    read = run_reject_review(root, seg, extra_args=("--print-verdict-digest",))
    assert read.returncode == 0, read.stdout + read.stderr
    payload = json.loads(read.stdout.strip())
    assert set(payload) == {
        "success", "seg", "review_path", "dispatch_token", "verdict_digest",
        "round_label", "round_label_problem",
    }, f"--print-verdict-digest's envelope must stay exactly its documented seven keys, got {sorted(payload)}"
    assert "consumer_warning" not in payload
    assert "consumer_warning_problem" not in payload
