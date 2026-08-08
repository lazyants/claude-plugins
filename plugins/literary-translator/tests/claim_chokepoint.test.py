"""tests/claim_chokepoint.test.py -- #438 D8 regression lock: codex_job.py's OWN
chokepoint refusal for a translate launch against a CLAIMED segment.

Why this file exists (D8, PLAN.md): the default W5 dispatch path
(`mass-translate-wf.template.js`) has NO claim-aware guard of its own --
`translateStage` is nine flagless lines that unconditionally invoke
`codex_job.py --kind translate`. So the refusal that keeps a claimed segment
from ever being re-translated has to live HERE, immediately before `launch()`
(`:1323`), the only route in codex_job.py that can overwrite a canonical draft.

THE DIRECTION THAT MATTERS MOST IS THE NEGATIVE ONE (asserted first, and in
every case below): a HEALTHY claimed segment already flows correctly TODAY
via `safe_adopt()` -- the translate "dispatch" degrades into a no-op adoption,
and it returns 0 having launched nothing, long before reaching the new guard.
A guard placed naively right after `--kind` parsing (rather than immediately
before `launch()`) would refuse that already-working flow. Every test here
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
+ `write_claim_record` -- never hand-rolled JSON -- so a fixture drift in
claim_record's own field set or write discipline shows up here too.
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
    leaves on disk."""
    runs_dir = Path(root) / "runs"
    path = claim_record.claimed_path(run_id, seg, runs_dir)
    payload = claim_record.build_claim_record(
        seg=seg, profile=profile, run_id=run_id, source_run_id="SOURCE-RUN-0",
        previous_dispatch_token="SOURCE-RUN-0:%s" % seg,
        pre_claim_content_sha1="0" * 40,
        operator_invocation="pytest tests/claim_chokepoint.test.py",
        cache_key={}, claimed_at="2026-08-08T00:00:00Z",
    )
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
    after --kind parsing (rather than immediately before launch()) would
    refuse this already-working flow instead. Covers a colon-bearing segment
    id (D1: segment ids contain colons in both books, and the claim record
    filename must tolerate it)."""
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
