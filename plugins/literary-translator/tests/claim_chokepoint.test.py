"""tests/claim_chokepoint.test.py -- #438 D8 regression lock: codex_job.py's OWN
chokepoint refusal for a translate launch against a CLAIMED segment.

Why this file exists (D8, PLAN.md): the default W5 dispatch path
(`mass-translate-wf.template.js`) has NO claim-aware guard of its own --
`translateStage` is nine flagless lines that unconditionally invoke
`codex_job.py --kind translate`. So the refusal that keeps a claimed segment
from ever being re-translated has to live HERE, inside codex_job.py itself.

WHERE EXACTLY, AND WHY THIS FILE'S FIRST ANSWER WAS WRONG. Until 1.21.0 this
docstring said the guard sat "immediately before `launch()`, the only route
in codex_job.py that can overwrite a canonical draft". The premise was false,
and the same file already contradicted it: `_canonical_replaceable()`'s own
docstring names TWO write sites it guards, and the second one is
`adopt_pending()`, which ends in `os.replace(self.pending, self.canonical)`.
The guard now sits between `safe_adopt()` and `adopt_pending()` -- above BOTH
remaining routes that can overwrite a canonical draft, and still below the
one that makes the healthy case work. The regression lock for that placement
is `test_claimed_segment_with_a_valid_pending_keeps_the_claimed_drafts_bytes`
below, which is also the only test in this file that watches real bytes being
destroyed rather than a return code changing.

THE DIRECTION THAT MATTERS MOST IS THE NEGATIVE ONE (asserted first, and in
every case below): a HEALTHY claimed segment already flows correctly TODAY
via `safe_adopt()` -- the translate "dispatch" degrades into a no-op adoption,
and it returns 0 having launched nothing, long before reaching the new guard.
A guard placed naively right after `--kind` parsing (rather than below
`safe_adopt()`) would refuse that already-working flow. Every test here
invokes `codex_job.py --kind translate` DIRECTLY (constructing a real
`CodexJob` and calling its real `.run()`, in-process -- never through
`segment_dispatch_driver.py`, which already had its own derived-action check
and would prove nothing about this, the DEFAULT path's only guard).

Gate scripts (`draft_ready.py`/`validate_draft.py`) are STUBBED via a
monkeypatched `_gate` -- this file's subject is the CHOKEPOINT'S OWN
placement and three-state read discipline relative to `safe_adopt()` /
`adopt_pending()` / `launch()`, never the real gate scripts' content checks
(covered in tests/draft_ready.test.py / tests/validate_draft.test.py). This
mirrors codex_job_driver.test.py's own white-box layer (`_gate_recorder`).

Claim records are written through claim_record.py's OWN `build_claim_record`
+ `write_claim_record` -- never hand-rolled JSON -- so this file's fixtures
exercise the real write discipline (exclusive create, directory fsync) rather
than a stand-in for it. See `_write_claim()` for exactly which field-set
drift that does and does not catch.

THAT FIXTURE CHOICE IS ALSO WHY THIS FILE CARRIES A SECOND, SMALLER SUBJECT
at the bottom: claim_record.py's own write- and read-FAILURE verdicts. There
is no dedicated claim_record.test.py; the module's unit tests are distributed
across the claim_* files by subject, the same way claim_selector.test.py owns
the torn-record read (its `test_zero_length_claim_record_classifies_ambiguous_
not_present` / `test_torn_claim_record_with_partial_json_classifies_ambiguous`
pair) and claim_prompt_contract.test.py owns the healthy already-claimed
re-write (`test_write_claim_record_is_exclusive_so_a_reclaim_cannot_rewrite_
the_baseline`). Cross-file references here are by test NAME and never by line
number, deliberately: those files are edited on the same branch as this one and
a line citation is stale before the round it was written in ends. What lands HERE is the pair
`_write_claim()` above silently depends on being true -- that
write_claim_record() FAILS rather than reporting a durability it never
established, and that read_claim_record() RETURNS a verdict rather than
raising one. Those tests construct no CodexJob at all and say so
individually; they are grouped under their own banner comment below.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
CLAIM_RECORD_SRC = SCRIPTS_DIR / "claim_record.py"
DRIVER_SRC = SCRIPTS_DIR / "codex_job.py"

assert CLAIM_RECORD_SRC.is_file(), f"expected claim_record.py at {CLAIM_RECORD_SRC}"
assert DRIVER_SRC.is_file(), f"expected the driver at {DRIVER_SRC}"


def _load_module(name, path):
    """Load a shipped script as an importable module, registered in
    sys.modules under `name` -- so an intra-directory `import claim_record`
    inside codex_job.py (loaded next, below) resolves to THIS SAME module
    object. The identical idiom tests/scaffold_setup.test.py already uses for
    cache_key.py's own sibling import."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


claim_record = _load_module("claim_record", CLAIM_RECORD_SRC)
codex_job = _load_module("codex_job_chokepoint_mod", DRIVER_SRC)


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def _mkjob(tmp_path, seg="c001", tok="RUN1:c001", disp="d1", run_id="RUN1",
           deadline=100, poll=1):
    """A translate CodexJob rooted at tmp_path/durable, with --run-id set --
    the CLI's own `main()` makes --run-id fatal-if-absent (a separate test
    below), but a caller constructing CodexJob() directly must set it too, or
    the D8 guard is a no-op by construction (see codex_job.py's own __init__
    docstring for why that default exists)."""
    root = tmp_path / "durable"
    (root / "segments").mkdir(parents=True, exist_ok=True)
    companion = tmp_path / "codex-companion.mjs"
    companion.write_text("// fake, never actually launched in this file\n", encoding="utf-8")
    prompt_file = tmp_path / ("prompt.%s.txt" % disp)
    prompt_text = codex_job.JOB_OUT_PLACEHOLDER + "\n"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    return codex_job.CodexJob(
        kind="translate", seg=seg, tok=tok, disp=disp, root=str(root),
        companion=str(companion), prompt_text=prompt_text, prompt_file=str(prompt_file),
        deadline_sec=deadline, poll_sec=poll, effort="high", node="node", run_id=run_id,
    )


def _write_draft(job):
    """A schema-shaped draft at job.canonical, dispatch_token == job.tok (the
    token a HEALTHY claim would have re-stamped it to -- see D4's own
    verified-property note that draft_content_sha1() projects this field
    out, so the token alone never changes a draft's content identity)."""
    doc = {
        "seg": job.seg, "blocks": {"p1": "hello"}, "footnotes": {}, "verses": {},
        "names": [], "notes": [], "dispatch_token": job.tok,
    }
    Path(job.canonical).parent.mkdir(parents=True, exist_ok=True)
    Path(job.canonical).write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _write_claim(root, run_id, seg, profile="from-converged"):
    """A real claim record via claim_record.py's own writer (never hand-rolled
    JSON) at runs/<run_id>/.claimed.<seg> -- exactly what a genuine D4 claim
    leaves on disk.

    The payload is every declared field defaulted to None, overridden with the
    handful whose values this file's own assertions or narrative depend on.
    Built from CLAIM_RECORD_FIELDS rather than by spelling all fourteen out,
    because nothing in this file ever reads a record FIELD -- the chokepoint's
    own predicate, classify_claim_record(), is an lstat and never opens the
    file -- so a hand-listed field set here would couple these tests to
    evidence they never consult.

    Be precise about which drift this still catches, since the shape looks
    like it catches both: a field REMOVED from build_claim_record() fails here
    immediately (an unexpected keyword argument), while a field ADDED to
    CLAIM_RECORD_FIELDS is absorbed as None and is silently fine. That is the
    correct trade for a fixture, but it is not the "any field-set drift shows
    up here" this file used to claim; the field set itself is pinned by
    claim_record's own dedicated tests, not by this one."""
    runs_dir = Path(root) / "runs"
    path = claim_record.claimed_path(run_id, seg, runs_dir)
    payload = claim_record.build_claim_record(**dict(
        {field: None for field in claim_record.CLAIM_RECORD_FIELDS},
        seg=seg, profile=profile, run_id=run_id, source_run_id="SOURCE-RUN-0",
        previous_dispatch_token="SOURCE-RUN-0:%s" % seg,
        pre_claim_content_sha1="0" * 40,
        operator_invocation="pytest tests/claim_chokepoint.test.py",
        claimed_at="2026-08-08T00:00:00Z",
    ))
    ok, detail = claim_record.write_claim_record(path, payload)
    assert ok, detail
    return path


def _stub_gate(pass_names=("draft_ready.py", "validate_draft.py")):
    """A `job._gate` replacement: exit 0 for every script name in
    `pass_names`, exit 1 for anything else -- deterministic and fast, and
    never a claim about the REAL gate scripts' own content checks (covered
    elsewhere; see this file's module docstring)."""
    def _gate(args, timeout):
        return SimpleNamespace(returncode=0 if args[0] in pass_names else 1,
                               stdout="", stderr="")
    return _gate


def _recording_gate(record, *, canonical_rejects=()):
    """A `job._gate` replacement that tells the CANONICAL gate calls apart
    from the CANDIDATE ones and records every call it serves.

    The two are distinguished exactly the way codex_job.py itself builds
    them: `safe_adopt()` runs `[script, seg, ...]` against the canonical
    draft, while `adopt_pending()` appends `--candidate-file <pending>`. So a
    scenario where the canonical draft is invalid but a deferred attempt
    would validate cleanly -- the only state in which adopt_pending() can
    destroy a claimed draft -- is expressible without stubbing out
    safe_adopt() or adopt_pending() themselves, which is the point: this
    file's subject is WHERE the guard sits relative to those two real
    methods, and a test that replaces either of them can no longer observe
    that.

    `record["calls"]` is the ordered list of (script, is_candidate) pairs
    actually served. Never a claim about the real gate scripts' own content
    checks (see this file's module docstring)."""
    def _gate(args, timeout):
        is_candidate = "--candidate-file" in args
        record["calls"].append((args[0], is_candidate))
        if not is_candidate and args[0] in canonical_rejects:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return _gate


def _spy_launch(record):
    def _launch():
        record["called"] = True
        return True
    return _launch


# --------------------------------------------------------------------------- #
# D8's chokepoint, in BOTH directions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("seg", ["c001", "FRONTBACK:errata_02"])
def test_healthy_claimed_segment_adopts_and_returns_0_launching_nothing(
        tmp_path, monkeypatch, seg):
    """Direction 1 -- the one a naive guard would break. A HEALTHY claimed
    segment must ADOPT via safe_adopt() and return 0, launching nothing, with
    the canonical draft's bytes byte-identical. A guard placed immediately
    after --kind parsing (rather than below safe_adopt(), where it actually
    sits) would refuse this already-working flow instead. Covers a
    colon-bearing segment id (D1: segment ids contain colons in both books,
    and the claim record filename must tolerate it)."""
    tok = "RUN1:%s" % seg
    job = _mkjob(tmp_path, seg=seg, tok=tok, run_id="RUN1")
    _write_draft(job)
    original_bytes = Path(job.canonical).read_bytes()
    _write_claim(job.root, "RUN1", seg)

    monkeypatch.setattr(job, "_gate", _stub_gate())
    launch_record = {"called": False}
    monkeypatch.setattr(job, "launch", _spy_launch(launch_record))

    rc = job.run()

    assert rc == 0
    assert job.adopted is True
    assert job.promoted is False
    assert job.reason == "adopted"
    assert launch_record["called"] is False, \
        "a healthy claimed segment must never reach launch()"
    assert Path(job.canonical).read_bytes() == original_bytes, \
        "the canonical draft must be byte-identical after a healthy adopt"


@pytest.mark.parametrize("seg", ["c001", "FRONTBACK:errata_02"])
def test_claimed_segment_with_absent_draft_refuses_before_launch(
        tmp_path, monkeypatch, seg):
    """Direction 2 -- the case that actually reaches the destructive route.
    A claimed segment whose draft is ABSENT at dispatch time: safe_adopt()
    returns False on `os.path.exists(self.canonical)` alone, adopt_pending()
    finds nothing either, and control must refuse fatally BEFORE launch(),
    naming the segment and the claim."""
    tok = "RUN1:%s" % seg
    job = _mkjob(tmp_path, seg=seg, tok=tok, run_id="RUN1")
    # no draft written -- job.canonical is absent
    claim_path = _write_claim(job.root, "RUN1", seg)

    monkeypatch.setattr(job, "_gate", _stub_gate())
    launch_record = {"called": False}
    monkeypatch.setattr(job, "launch", _spy_launch(launch_record))

    rc = job.run()

    assert rc == 1
    assert job.reason == "claimed-segment-refused"
    assert launch_record["called"] is False
    assert seg in job.error_detail, "the refusal must name the segment"
    assert str(claim_path) in job.error_detail, "the refusal must name the claim"


def test_claimed_segment_with_invalid_draft_refuses_before_launch(tmp_path, monkeypatch):
    """Direction 2, the other trigger: draft PRESENT but failing
    validate_draft.py's own gate (draft_ready.py's token check still passes).
    safe_adopt() must return False here too, reaching the same chokepoint."""
    job = _mkjob(tmp_path, seg="c001", tok="RUN1:c001", run_id="RUN1")
    _write_draft(job)
    _write_claim(job.root, "RUN1", "c001")

    # draft_ready.py passes (token matches); validate_draft.py REJECTS.
    monkeypatch.setattr(job, "_gate", _stub_gate(pass_names=("draft_ready.py",)))
    launch_record = {"called": False}
    monkeypatch.setattr(job, "launch", _spy_launch(launch_record))

    rc = job.run()

    assert rc == 1
    assert job.reason == "claimed-segment-refused"
    assert launch_record["called"] is False


def test_claimed_segment_with_a_valid_pending_keeps_the_claimed_drafts_bytes(
        tmp_path, monkeypatch):
    """Direction 2, the case that actually DESTROYS bytes -- and the reason
    the guard moved above adopt_pending().

    Every other refusal test in this file proves a return code. This one
    proves the thing the return code exists to protect: the claimed draft's
    own bytes, on disk, after run() returns. The state is the exact one D8
    exists for -- a segment claimed by this run whose canonical draft no
    longer validates, so safe_adopt() fails -- with one addition that makes
    it destructive: a SAME-RUN deferred attempt is sitting in the pending
    slot and would pass every candidate gate. (A CROSS-run pending is
    already refused by those gates' own --expect-token check, which is why
    the reachable case is same-run.)

    With the guard below adopt_pending(), that attempt is promoted by
    `os.replace(self.pending, self.canonical)` over the claimed draft, and
    the hand-edited content the claim exists to preserve -- the content
    `pre_claim_content_sha1` is the only durable record of -- is gone before
    anything ever consults the claim record. Nothing downstream can recover
    it: the bytes are not backed up anywhere, and the promoted attempt
    carries the same dispatch_token, so every later gate reports a perfectly
    healthy segment.

    THE MUTATION THAT MAKES THIS FAIL: move the
    `self._refuse_claimed_translate()` block in codex_job.py's run() back
    below the `if self.adopt_pending():` line. Then rc becomes 0, reason
    becomes "adopted-pending", the canonical holds `pending_text` instead of
    `claimed_text`, the pending file is consumed, and two candidate gate
    calls appear in the record -- five independent assertion failures, one
    of which (the canonical's bytes) is the operator-visible damage itself.
    Measured against a scratch copy of codex_job.py carrying exactly that
    swap: all five fire.

    Nothing here is stubbed except `_gate` and `launch`: safe_adopt() and
    adopt_pending() are the REAL methods, so the ordering being asserted is
    the real one and not a rearrangement of test doubles."""
    seg = "c001"
    job = _mkjob(tmp_path, seg=seg, tok="RUN1:%s" % seg, run_id="RUN1")
    claimed_text = json.dumps({
        "seg": seg, "blocks": {"p1": "hand-fixed after the cap"}, "footnotes": {},
        "verses": {}, "names": [], "notes": [], "dispatch_token": job.tok,
    }, indent=2)
    Path(job.canonical).parent.mkdir(parents=True, exist_ok=True)
    Path(job.canonical).write_text(claimed_text, encoding="utf-8")
    # A deferred attempt from a prior dispatch of THIS run: same token (so the
    # candidate gates' own --expect-token check would pass), different bytes.
    pending_text = json.dumps({
        "seg": seg, "blocks": {"p1": "a fresh machine translation"}, "footnotes": {},
        "verses": {}, "names": [], "notes": [], "dispatch_token": job.tok,
    }, indent=2)
    Path(job.pending).write_text(pending_text, encoding="utf-8")
    claim_path = _write_claim(job.root, "RUN1", seg)

    # The canonical draft fails validate_draft.py (so safe_adopt() returns
    # False and control reaches the chokepoint); the pending CANDIDATE passes
    # both gates, so adopt_pending() would promote it if it were ever reached.
    gate_record = {"calls": []}
    monkeypatch.setattr(
        job, "_gate", _recording_gate(gate_record, canonical_rejects=("validate_draft.py",)))
    launch_record = {"called": False}
    monkeypatch.setattr(job, "launch", _spy_launch(launch_record))

    rc = job.run()

    assert rc == 1
    assert job.reason == "claimed-segment-refused"
    assert Path(job.canonical).read_text(encoding="utf-8") == claimed_text, (
        "the claimed draft's bytes were overwritten -- adopt_pending() promoted a "
        "deferred attempt over a claimed segment, destroying the very content "
        "pre_claim_content_sha1 was recorded to protect"
    )
    assert Path(job.pending).read_text(encoding="utf-8") == pending_text, (
        "a refused claim must leave the deferred attempt exactly as it found it -- "
        "neither promoted nor discarded"
    )
    assert not any(is_candidate for _script, is_candidate in gate_record["calls"]), (
        "adopt_pending() ran its own candidate gates, so it was REACHED -- the guard "
        "is below it again: %r" % (gate_record["calls"],)
    )
    assert launch_record["called"] is False
    assert seg in job.error_detail
    assert str(claim_path) in job.error_detail


def test_unclaimed_segment_with_a_valid_pending_still_adopts_it(tmp_path, monkeypatch):
    """The control the test above cannot do without: moving the guard ABOVE
    adopt_pending() must not disable adoption for an UNCLAIMED segment.
    Identical fixture -- same invalid canonical, same valid same-run pending
    -- with the claim record simply absent. A guard that refused
    unconditionally, or one that read CLAIM_ABSENT as "cannot rule out a
    claim", would satisfy every assertion in the test above for entirely the
    wrong reason and fails here instead."""
    seg = "c001"
    job = _mkjob(tmp_path, seg=seg, tok="RUN1:%s" % seg, run_id="RUN1")
    Path(job.canonical).parent.mkdir(parents=True, exist_ok=True)
    Path(job.canonical).write_text(
        json.dumps({"seg": seg, "dispatch_token": job.tok}), encoding="utf-8")
    pending_text = json.dumps({"seg": seg, "dispatch_token": job.tok, "promoted": True})
    Path(job.pending).write_text(pending_text, encoding="utf-8")
    # no claim record at all

    gate_record = {"calls": []}
    monkeypatch.setattr(
        job, "_gate", _recording_gate(gate_record, canonical_rejects=("validate_draft.py",)))
    launch_record = {"called": False}
    monkeypatch.setattr(job, "launch", _spy_launch(launch_record))

    rc = job.run()

    assert rc == 0
    assert job.reason == "adopted-pending"
    assert Path(job.canonical).read_text(encoding="utf-8") == pending_text, (
        "the deferred attempt must be promoted over the unclaimed canonical"
    )
    assert not Path(job.pending).exists(), "a promoted pending is consumed"
    assert launch_record["called"] is False


def test_unclaimed_segment_still_translates_normally(tmp_path, monkeypatch):
    """Direction 3: an UNCLAIMED segment (no claim record at all -- CLAIM_ABSENT)
    must still reach launch(). A guard that refuses everything would pass the
    two REFUSE cases above for the wrong reason; this is the test that catches
    it."""
    job = _mkjob(tmp_path, seg="c001", tok="RUN1:c001", run_id="RUN1")
    # no draft, no claim record -- ordinary unclaimed dispatch

    launch_record = {"called": False}
    def spy_launch():
        launch_record["called"] = True
        return False   # a doomed launch fails harmlessly; only reachability matters here
    monkeypatch.setattr(job, "launch", spy_launch)

    rc = job.run()

    assert launch_record["called"] is True, "an unclaimed segment must reach launch()"
    assert job.reason != "claimed-segment-refused"


def test_claim_record_directory_refuses_not_proceeds(tmp_path, monkeypatch):
    """Direction 4 -- the fail-open trap this plan is explicitly paranoid
    about. The claim record path occupied by a NON-REGULAR entry (a
    directory) must REFUSE, never be read as "no record here, proceed".
    Path.exists() returns False for a mis-typed entry exactly like it does
    for a genuinely absent one -- claim_record.py's three-state predicate
    exists precisely to close that (see its own module docstring: "a
    refusal keyed on presence fails OPEN by default"). No draft is written,
    so safe_adopt()/adopt_pending() both fail and control reaches the
    chokepoint exactly like the absent-draft case above."""
    job = _mkjob(tmp_path, seg="c001", tok="RUN1:c001", run_id="RUN1")

    claim_path = claim_record.claimed_path("RUN1", "c001", Path(job.root) / "runs")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.mkdir()  # a DIRECTORY, not a regular file -> CLAIM_AMBIGUOUS

    launch_record = {"called": False}
    monkeypatch.setattr(job, "launch", _spy_launch(launch_record))

    rc = job.run()

    assert rc == 1
    assert job.reason == "claimed-segment-refused"
    assert launch_record["called"] is False


def test_claim_record_permission_denied_refuses_not_proceeds(tmp_path, monkeypatch):
    """Direction 4, the other named trigger: the claim record itself is a
    perfectly regular file, but its PARENT directory is unreadable, so
    lstat() on the record raises PermissionError (not FileNotFoundError).
    classify_claim_record() must map that to CLAIM_AMBIGUOUS, never
    CLAIM_ABSENT -- and the chokepoint must still refuse."""
    job = _mkjob(tmp_path, seg="c001", tok="RUN1:c001", run_id="RUN1")

    claim_path = claim_record.claimed_path("RUN1", "c001", Path(job.root) / "runs")
    claim_dir = claim_path.parent
    claim_dir.mkdir(parents=True, exist_ok=True)
    claim_path.write_text("{}", encoding="utf-8")
    os.chmod(claim_dir, 0o000)  # lstat() on the child now fails with EACCES

    launch_record = {"called": False}
    monkeypatch.setattr(job, "launch", _spy_launch(launch_record))
    try:
        rc = job.run()
    finally:
        os.chmod(claim_dir, 0o755)  # restore so tmp_path cleanup can remove it

    assert rc == 1
    assert job.reason == "claimed-segment-refused"
    assert launch_record["called"] is False


# --------------------------------------------------------------------------- #
# --run-id: FATAL if absent, never "unclaimed" (D8's own test-list sibling item)
# --------------------------------------------------------------------------- #
def _translate_argv(tmp_path, run_id="RUN1", **over):
    root = tmp_path / "durable"
    (root / "segments").mkdir(parents=True, exist_ok=True)
    companion = tmp_path / "codex-companion.mjs"
    companion.write_text("// fake\n", encoding="utf-8")
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text(codex_job.JOB_OUT_PLACEHOLDER + "\n", encoding="utf-8")
    d = dict(kind="translate", companion=str(companion), cwd=str(root), seg="c001",
             prompt_file=str(prompt_file), expect_token="RUN1:c001", disp="d1",
             deadline_sec="600", run_id=run_id)
    d.update(over)
    argv = ["--kind", d["kind"], "--companion", d["companion"], "--cwd", d["cwd"],
            "--seg", d["seg"], "--prompt-file", d["prompt_file"],
            "--expect-token", d["expect_token"], "--disp", d["disp"],
            "--deadline-sec", d["deadline_sec"], "--node", "node"]
    if d["run_id"] is not None:
        argv += ["--run-id", d["run_id"]]
    return argv


def test_run_id_absent_is_fatal_not_unclaimed(tmp_path):
    """Omitting --run-id entirely must refuse at usage time (exit 2) --
    never a silent "cannot look up a claim, so proceed as unclaimed"
    default, which is exactly the shape a RUN_ID derived from a possibly-
    malformed --expect-token would produce (the reason D8 requires --run-id
    to be PASSED, never derived)."""
    rc = codex_job.main(_translate_argv(tmp_path, run_id=None))
    assert rc == 2


@pytest.mark.parametrize("bad_run_id", ["", "   "])
def test_run_id_empty_or_whitespace_is_also_fatal(tmp_path, bad_run_id):
    """An empty/whitespace-only --run-id is the same silent-degradation trap
    as an absent one: Path("runs") / "" is Path("runs") unchanged, which
    would mis-resolve every claim lookup to "not found" -> "not claimed"."""
    rc = codex_job.main(_translate_argv(tmp_path, run_id=bad_run_id))
    assert rc == 2


def test_run_id_present_is_not_fatal(tmp_path):
    """Control: a well-formed --run-id must parse cleanly (isolating the two
    FATAL tests above from a false-positive that would refuse on some OTHER
    unrelated usage error). Checked at the parse layer only -- unlike the
    two tests above, a SUCCESSFUL parse falls through into job.run(), which
    would spawn a real git/node subprocess; nothing here needs that to prove
    --run-id itself is not the problem."""
    argv = _translate_argv(tmp_path, run_id="RUN1")
    args = codex_job._build_parser().parse_args(argv)
    assert args.run_id == "RUN1"


# --------------------------------------------------------------------------- #
# claim_record.py's OWN failure verdicts.
#
# No CodexJob is constructed below this line -- see the module docstring for
# why these live in this file rather than a claim_record.test.py that does not
# exist. Each one covers a failure path of the writer/reader that `_write_claim()`
# above depends on and, by asserting on success, can never itself exercise.
# --------------------------------------------------------------------------- #

# Root bypasses directory permission bits entirely, so the two injections
# below (mode 0o333 on the record's directory) would simply not fail there and
# the tests would pass without ever reaching what they are about. Skipping is
# right and weakening the assertion is not: a green that proves nothing is the
# exact shape these tests exist to catch in the production code.
_needs_unprivileged_uid = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permissions, so the fsync cannot be made to fail",
)


def _claim_payload(seg, run_id, profile="from-converged"):
    """The same every-field-defaulted-to-None payload `_write_claim()` builds,
    without the writing. These tests call write_claim_record() themselves
    because its RETURN VALUE is the subject, and `_write_claim()` asserts that
    value away (`assert ok, detail`) on its way to a claim record."""
    return claim_record.build_claim_record(**dict(
        {field: None for field in claim_record.CLAIM_RECORD_FIELDS},
        seg=seg, profile=profile, run_id=run_id, source_run_id="SOURCE-RUN-0",
        previous_dispatch_token="SOURCE-RUN-0:%s" % seg,
        pre_claim_content_sha1="0" * 40,
        operator_invocation="pytest tests/claim_chokepoint.test.py",
        claimed_at="2026-08-08T00:00:00Z",
    ))


@_needs_unprivileged_uid
def test_a_fresh_claim_write_fails_when_its_directory_cannot_be_synced(tmp_path):
    """write_claim_record()'s FRESH-WRITE path: a record whose directory entry
    cannot be proven durable is a FAILED write, never a success with a
    footnote.

    Why that matters more here than in an ordinary writer: the caller reads
    True as permission to re-stamp the draft's dispatch_token (the claim block
    in select_segments.py's run(), immediately after `if published:`), and
    record-first ordering is the entire reason those two happen in that order.
    fsync on the FILE commits the record's contents; only fsync on the
    DIRECTORY commits the entry that makes those contents findable after a
    power loss. Returning True without the second one hands back precisely the
    state D8's guard cannot refuse -- a re-stamped token with no record, which
    every reader classifies ABSENT and reads as "unclaimed".

    THE INJECTION IS REAL, NOT A DOUBLE. Mode 0o333 on runs/<run_id>/ keeps
    write+execute, so `os.open(path, O_CREAT|O_EXCL|O_WRONLY)` still creates
    the record and its body is still written and fsynced; it takes away read,
    so `os.open(dir, O_RDONLY)` -- which is all fsync_directory() does -- fails
    with EACCES. Nothing is monkeypatched, so a fsync_directory() that quietly
    stopped failing is caught here too, whereas a stubbed one would only prove
    that write_claim_record() forwards a stub's return value. Same technique as
    claim_selector.test.py's draft-directory sibling
    (`test_rewrite_fails_when_the_drafts_directory_entry_cannot_be_made_durable`),
    which covers the OTHER half of the record-first ordering.

    THE MUTATION THAT MAKES THIS FAIL: delete the `sync_problem =
    fsync_directory(path.parent)` block at the END of write_claim_record() --
    the one after the body is written -- leaving `return (True, "")`. The call
    then reports a fresh success and the first assertion fires. Deleting the
    OTHER fsync_directory() call (the EEXIST one, inside the
    `except FileExistsError` handler) leaves this test GREEN, which is why the
    already-claimed sibling below is a separate test and not a parametrisation.
    Measured: applied each deletion in turn to a scratch copy of claim_record.py
    and ran both test bodies against it -- each deletion reddens exactly its own
    test."""
    path = claim_record.claimed_path("RUN1", "c001", tmp_path / "durable" / "runs")
    path.parent.mkdir(parents=True, exist_ok=True)

    os.chmod(path.parent, 0o333)
    try:
        ok, detail = claim_record.write_claim_record(path, _claim_payload("c001", "RUN1"))
    finally:
        os.chmod(path.parent, 0o755)  # restore so tmp_path cleanup can remove it

    assert ok is False, (
        "a directory entry this code cannot prove durable must FAIL the claim write; "
        "reporting success would authorize the token re-stamp that record-first "
        "ordering exists to keep behind a durable record"
    )
    assert "directory entry is not durable" in detail, detail
    assert detail.startswith("the claim record was written but "), detail
    # The record STAYS on disk -- the deliberate difference from the
    # partial-write path, which unlinks. Here the contents are complete and
    # fsynced and only the entry's durability is unproven, and deleting a valid
    # record is the fail-OPEN direction for every reader of it: both D8 guards
    # refuse on PRESENT and let a translate through on ABSENT.
    state, _ = claim_record.classify_claim_record(path)
    assert state == claim_record.CLAIM_PRESENT, (
        "the complete record must not be unlinked to settle a durability doubt -- "
        "removing it deletes the guard, got state=%r" % (state,)
    )
    assert json.loads(path.read_text(encoding="utf-8"))["seg"] == "c001"


@_needs_unprivileged_uid
def test_an_already_claimed_write_fails_when_its_directory_cannot_be_synced(tmp_path):
    """write_claim_record()'s ALREADY-CLAIMED (EEXIST) path, which is a SECOND
    fsync call site and not a redundant copy of the first.

    The state it exists for is a RETRY after the failure the test above
    measures: attempt one created the record, failed its directory sync, and
    deliberately did not unlink it. Attempt two therefore lands on EEXIST.
    Without a sync of its own that branch would answer the literal "already
    claimed by this run" -- which select_segments.py compares against exactly,
    and treats as an idempotent re-application that still permits the token
    re-stamp -- having never established the durability attempt one failed to.
    The same crash then produces the same token-without-record state, one retry
    later and with a clean-looking report.

    So the assertion carrying this test is the one on the LITERAL: the branch
    must not merely return False, it must return a detail that MISSES the
    caller's string compare, or the caller reads "already claimed" and proceeds
    regardless of the False.

    THE MUTATION THAT MAKES THIS FAIL: delete the `sync_problem =
    fsync_directory(path.parent)` block inside the `except FileExistsError`
    handler, leaving the branch as `return (False, "already claimed by this
    run")`. Both the literal assertion and the "not durable" one fire.
    Deleting the fresh-write fsync instead leaves this test GREEN -- its first
    write happens before the directory is locked down and is a healthy one.
    Measured against scratch copies carrying each deletion; each reddens only
    its own test.

    The HEALTHY shape of this same branch -- exact literal, on-disk record
    untouched -- is the subject of claim_prompt_contract.test.py's
    `test_write_claim_record_is_exclusive_so_a_reclaim_cannot_rewrite_the_baseline`,
    so it is not restated here; this test asserts only that the record
    survives, which is what makes the retry it describes possible at all."""
    path = claim_record.claimed_path("RUN1", "c001", tmp_path / "durable" / "runs")
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, detail = claim_record.write_claim_record(path, _claim_payload("c001", "RUN1"))
    assert ok is True, detail  # arrange, not assert: a healthy first claim
    before = path.read_bytes()

    os.chmod(path.parent, 0o333)
    try:
        ok2, detail2 = claim_record.write_claim_record(path, _claim_payload("c001", "RUN1"))
    finally:
        os.chmod(path.parent, 0o755)

    assert ok2 is False
    assert detail2 != "already claimed by this run", (
        "the EEXIST branch reported the idempotent literal its caller compares "
        "against, so the caller re-stamps the draft's dispatch_token on the strength "
        "of a record whose directory entry was never made durable -- the retry that "
        "silently re-creates token-without-record"
    )
    assert "directory entry is not durable" in detail2, detail2
    assert detail2.startswith("this run had already claimed this segment, but "), detail2
    assert path.read_bytes() == before, (
        "a failed sync on the EEXIST path must not touch the existing record -- it is "
        "the only account of the true pre-claim baseline"
    )


def test_a_claim_record_with_invalid_utf8_returns_ambiguous_rather_than_raising(tmp_path):
    """read_claim_record() must RETURN CLAIM_AMBIGUOUS for a record whose bytes
    are not valid UTF-8 -- the verdict its own docstring promises for anything
    that "classifies PRESENT but does not parse".

    UnicodeDecodeError subclasses ValueError, NOT OSError, so the `except
    OSError` guarding the read let it escape: the call RAISED instead of
    returning, and the documented invariant was false for exactly the one
    failure shape a reader cannot see coming. The direction was safe -- nothing
    downstream grants a claim on a traceback -- and the blast radius was not:
    select_segments.py reads this unguarded in evaluate_lost_token_recovery()
    and again when re-reading an already-claimed record, so ONE segment's
    malformed record took the whole admission batch down with it, contradicting
    the per-id isolation that file states for every other unreadable artifact.

    No assertion is needed for the raise itself: an escaping exception fails
    this test by escaping, which is the defect seen from a caller's side. The
    assertions are about the VERDICT (what callers branch on) and about the
    detail naming the file -- UnicodeDecodeError's own message carries the
    offending byte and no filename, so a detail that merely forwarded it would
    leave an operator with nothing to open.

    THE MUTATION THAT MAKES THIS FAIL: delete the `except UnicodeDecodeError`
    clause from read_claim_record(). Measured on a scratch copy: the call
    raises `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in
    position 28` and the test errors out.

    The classifier assertion comes FIRST because it is the contrast the module
    docstring's "EXISTENCE IS NOT VALIDITY" split rests on: classify_claim_record()
    is an lstat, reports PRESENT for these bytes, and never opens the file -- so
    both D8 guards (which use the classifier alone) still refuse, which is why
    this defect never reached them, while any consumer about to believe a FIELD
    must come through the reader and take the AMBIGUOUS verdict."""
    path = claim_record.claimed_path("RUN1", "c001", tmp_path / "durable" / "runs")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"seg": "c001", "profile": "\xff\xfe not utf-8"}')

    state, _detail = claim_record.classify_claim_record(path)
    assert state == claim_record.CLAIM_PRESENT, (
        "the lstat-only classifier must still see a regular file here -- if it did "
        "not, this fixture would be proving something other than a decode failure"
    )

    state, payload, detail = claim_record.read_claim_record(path)

    assert state == claim_record.CLAIM_AMBIGUOUS, (
        "a record whose bytes are not valid UTF-8 must be reported AMBIGUOUS, the "
        "safe direction every reader maps to 'not claimed', got %r" % (state,)
    )
    assert payload is None, payload
    assert "not valid UTF-8" in detail, detail
    assert str(path) in detail, (
        "the detail must name the file: UnicodeDecodeError carries the offending "
        "byte and no filename, so %r on its own is not actionable" % (detail,)
    )


def test_an_unencodable_claim_record_leaves_NO_entry_rather_than_a_zero_byte_one(tmp_path):
    """write_claim_record() must RETURN a verdict for a payload that cannot be
    encoded as UTF-8, and must leave the claim path ABSENT.

    The WRITE-side twin of the decode bug pinned by the test above, and it
    failed strictly worse. `json.dumps(..., ensure_ascii=False)` happily
    returns a str containing a LONE SURROGATE, because `json.loads()` decodes
    a "\\ud800" escape into one and `dispatch_token` -- hence
    `previous_dispatch_token` in the record -- is an arbitrary string that no
    schema rejects and that no content hash inspects (draft_content_sha1()
    projects dispatch_token out, which is the whole point of that projection).
    Encoding that str raises UnicodeEncodeError, a ValueError and NOT an
    OSError, so the `except OSError` on the write never caught it.

    Why worse than the read side: the exclusive `os.open` had ALREADY created
    the entry by the time the encode ran, so the exception escaped the function
    -- breaking its "returns a verdict, does not raise" contract -- and left a
    ZERO-BYTE regular file behind. That file lands on precisely the pair no
    gate can recover from: classify_claim_record() is lstat + S_ISREG and
    reports PRESENT without opening it, while read_claim_record() reports
    AMBIGUOUS. Every subsequent attempt therefore sees a claim that exists and
    cannot be parsed, and O_CREAT|O_EXCL refuses to overwrite it -- so the
    segment is permanently unclaimable until a human deletes the file.

    The fix is an ORDERING, not a handler: the encode happens before the path
    is created, so the failure is unreachable rather than cleaned up, and the
    state stays ABSENT -- which every gate already refuses safely.

    THE MUTATION THAT MAKES THIS FAIL: move the `body.encode("utf-8")` back
    inside the write block (i.e. restore `os.fdopen(fd, "w", encoding="utf-8")`
    + `handle.write(body)`). Measured on a scratch copy: write_claim_record()
    raises `UnicodeEncodeError: 'utf-8' codec can't encode character '\\ud800'
    in position 132: surrogates not allowed`, the test errors out on the call,
    and the path is left present at size 0.

    The ABSENCE assertion is the load-bearing one -- a returned (False, detail)
    that still left the entry behind would strand the segment just the same."""
    path = claim_record.claimed_path("RUN1", "c001", tmp_path / "durable" / "runs")
    payload = claim_record.build_claim_record(**dict(
        {field: None for field in claim_record.CLAIM_RECORD_FIELDS},
        seg="c001", profile="from-cap", run_id="RUN1",
        claimed_at="2026-08-08T09:00:00Z",
        # A lone surrogate, exactly as json.loads('"\\ud800"') yields it.
        previous_dispatch_token="old:c001:\ud800",
    ))

    ok, detail = claim_record.write_claim_record(path, payload)

    assert ok is False, f"an unencodable payload must not report success: {detail!r}"
    assert not path.exists(), (
        "the claim path must be ABSENT after an encode failure -- a zero-byte "
        "record classifies PRESENT and reads AMBIGUOUS, which makes the segment "
        "unclaimable forever"
    )
    state, _ = claim_record.classify_claim_record(path)
    assert state == claim_record.CLAIM_ABSENT, (
        f"the lstat classifier must see nothing here, got {state!r}"
    )
    assert "encode" in detail, (
        f"the detail must say the record could not be encoded, so an operator is "
        f"not sent looking for a disk fault: {detail!r}"
    )


def test_a_normal_claim_record_is_written_byte_identically_after_the_encode_move(tmp_path):
    """The encode-first fix must not change the bytes on disk. The write moved
    from text mode (`os.fdopen(fd, "w", encoding="utf-8")`) to binary over
    pre-encoded bytes, and a silent difference there -- a newline translation,
    a BOM, a changed separator -- would invalidate every pre-claim baseline
    this record exists to carry, without any test noticing.

    Asserted against the serialization the record's own reader expects, not
    against a frozen literal, so this stays true when a field is added."""
    path = claim_record.claimed_path("RUN1", "c001", tmp_path / "durable" / "runs")
    payload = claim_record.build_claim_record(**dict(
        {field: None for field in claim_record.CLAIM_RECORD_FIELDS},
        seg="c001", profile="from-cap", run_id="RUN1",
        claimed_at="2026-08-08T09:00:00Z",
        previous_dispatch_token="old:c001",
    ))

    ok, detail = claim_record.write_claim_record(path, payload)
    assert ok, detail

    expected = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    assert path.read_bytes() == expected.encode("utf-8"), (
        "binary-mode write must reproduce the text-mode bytes exactly"
    )
    state, read_back, _ = claim_record.read_claim_record(path)
    assert state == claim_record.CLAIM_PRESENT and read_back == payload


# --------------------------------------------------------------------------- #
# D8's CROSS-RUN half at the DEFAULT chokepoint. codex_job.py is launched
# directly by mass-translate-wf.template.js, so this guard -- not the optional
# dispatch driver's -- is the one on the shipped path. It had the identical
# self-namespace defect: _claim_state() built runs/<self.run_id>/.claimed.<seg>
# and CLAIM_ABSENT permitted the translate.
# --------------------------------------------------------------------------- #


def _hold_claim(job, run_id, seg=None):
    """Publishes a real claim record for `run_id`, via the shipped writer."""
    seg = seg or job.seg
    path = claim_record.claimed_path(run_id, seg, Path(job.root) / "runs")
    payload = claim_record.build_claim_record(**dict(
        {field: None for field in claim_record.CLAIM_RECORD_FIELDS},
        seg=seg, profile="from-cap", run_id=run_id,
        claimed_at="2026-08-08T09:00:00Z"))
    ok, detail = claim_record.write_claim_record(path, payload)
    assert ok, detail
    return path


def _write_draft_owned_by(job, owner_token):
    """A schema-shaped draft stamped for somebody else."""
    doc = {"seg": job.seg, "blocks": {"p1": "HAND EDIT OWNED BY RUN A"}, "footnotes": {},
           "verses": {}, "names": [], "notes": []}
    if owner_token is not None:
        doc["dispatch_token"] = owner_token
    Path(job.canonical).parent.mkdir(parents=True, exist_ok=True)
    Path(job.canonical).write_text(json.dumps(doc, indent=2), encoding="utf-8")


def test_default_chokepoint_refuses_a_translate_over_a_draft_another_run_owns(tmp_path):
    """THE DEFAULT-PATH DEFECT. Run A holds a live claim on c001 and the
    canonical draft is stamped for A. Run B launches codex_job.py directly --
    which is what the shipped Workflow template does -- with its own
    --expect-token and --run-id, so the token/run-id consistency check agrees
    and tells us nothing. safe_adopt() rejects A's draft (foreign token), B's
    own claim namespace reads ABSENT, and before this the translate reached
    launch(), promoting a fresh machine translation over A's hand edit.

    THE MUTATION THAT MAKES THIS FAIL: drop the foreign_owner_refusal() call
    from the CLAIM_ABSENT branch of _refuse_claimed_translate() (restore the
    bare `return False, None, None, None`). Measured: this test's `refuse`
    assertion fails while the unclaimed-segment tests above stay green."""
    job = _mkjob(tmp_path, seg="c001", tok="RUN-B:c001", run_id="RUN-B")
    _hold_claim(job, "RUN-A")
    _write_draft_owned_by(job, "RUN-A:c001")

    refuse, state, detail, _path = job._refuse_claimed_translate()

    assert refuse is True, (
        "run B must not translate over a draft run A holds a live claim on -- "
        "this is the DEFAULT dispatch path, not the optional driver's"
    )
    assert "RUN-A" in (detail or ""), f"the refusal must name the owner: {detail!r}"
    assert "#438 D8" in (detail or ""), detail


def test_default_chokepoint_refuses_a_TOKENLESS_draft_another_run_holds(tmp_path):
    """D9's lost-token state at the default chokepoint: A claimed c001 and a
    later fix round dropped the token from the draft, so A's claim record is
    the only surviving evidence of ownership."""
    job = _mkjob(tmp_path, seg="c001", tok="RUN-B:c001", run_id="RUN-B")
    _hold_claim(job, "RUN-A")
    _write_draft_owned_by(job, None)

    refuse, _state, detail, _path = job._refuse_claimed_translate()

    assert refuse is True, "a tokenless draft A still holds a claim on must not be overwritten"
    assert "RUN-A" in (detail or ""), detail


def test_default_chokepoint_still_allows_an_ordinary_unclaimed_translate(tmp_path):
    """The no-regression boundary. No claim record anywhere and a draft stamped
    for this run -- the ordinary case every normal dispatch is in. A guard that
    refused here would stop the whole pipeline, which is a worse defect than
    the one above."""
    job = _mkjob(tmp_path, seg="c001", tok="RUN-B:c001", run_id="RUN-B")
    _write_draft_owned_by(job, "RUN-B:c001")

    refuse, _state, detail, _path = job._refuse_claimed_translate()

    assert refuse is False, f"an unclaimed segment must still translate: {detail!r}"


def test_default_chokepoint_still_allows_a_first_translation_with_no_draft(tmp_path):
    """No draft on disk at all -- nothing to overwrite."""
    job = _mkjob(tmp_path, seg="c001", tok="RUN-B:c001", run_id="RUN-B")
    refuse, _state, detail, _path = job._refuse_claimed_translate()
    assert refuse is False, f"the first translation must not be blocked: {detail!r}"
