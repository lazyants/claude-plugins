"""Tests for assets/scripts/codex_job.py -- the shipped validate-before-promote codex-job
driver (#198, v1.4.7; PLAN-198 §2.1/§4).

Two layers:
  * WHITE-BOX (in-process): import the driver module, monkeypatch its subprocess runner
    (_run) / gate runner (_gate) / time helpers, and unit-test the branch logic
    (poll-terminal, validate-then-atomic-promote, flock-acquire, abs-remaining/finalize-tail
    reservation, fail-sentinel write, hygiene guard, safe-adoption, usage/JOB_OUT).
  * SUBPROCESS integration: drive the SHIPPED script from a tmp_path durable_root (codex_job
    copied into <root>/scripts/ alongside STUB gate scripts that honour the FROZEN
    candidate-file CLI, plus a fake executable `node` stub simulating the task/status/cancel
    state machine). This exercises the real launch->poll->validate->atomic-promote path,
    isolation, cwd binding, deadline/cancel, adoption, per-seg flock serialization, and
    forged-artifact rejection -- all node-free and lane-independent.

Why STUB gates (not the real draft_ready.py/validate_draft.py/review_ready.py): the driver
only depends on the FROZEN candidate-file CLI contract (arg order + exit-code semantics), so
stubs that honour it exercise the driver's orchestration deterministically without racing
lane B's concurrent edits in the shared checkout. Real-gate end-to-end coverage lives in
lane C's mass_translate_driver_smoke.test.py + the full suite at integration.

Red-before-green for a NEW module is carried by the discriminating assertions: a no-op /
always-promote / never-sentinel driver fails these (invalid attempts are NOT promoted, a
failure writes exactly the empty per-DISP sentinel, promotion is one atomic rename with no
.bak.*, a lease-loser never clobbers the holder's joblog).
"""

import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
DRIVER_SRC = SCRIPTS_DIR / "codex_job.py"

assert DRIVER_SRC.is_file(), f"expected the driver at {DRIVER_SRC}"

_spec = importlib.util.spec_from_file_location("codex_job_mod", str(DRIVER_SRC))
assert _spec is not None and _spec.loader is not None
codex_job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(codex_job)

try:
    import fcntl  # noqa: F401
    _HAS_FLOCK = True
except ImportError:  # pragma: no cover - non-POSIX
    _HAS_FLOCK = False

skip_no_flock = pytest.mark.skipif(not _HAS_FLOCK, reason="fcntl.flock unavailable")
_HAS_GIT = bool(__import__("shutil").which("git"))


# --------------------------------------------------------------------------- #
# stub gate scripts (frozen candidate-file CLI) + fake node
# --------------------------------------------------------------------------- #
STUB_DRAFT_READY = '''#!/usr/bin/env python3
import argparse, json, os, sys
p = argparse.ArgumentParser()
p.add_argument("seg")
p.add_argument("--expect-token", dest="tok", default=None)
p.add_argument("--candidate-file", dest="cf", default=None)
a = p.parse_args()
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = a.cf if a.cf else os.path.join(root, "segments", a.seg + ".draft.json")
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as e:
    print("not ready: %s" % e); sys.exit(1)
if not isinstance(d, dict) or not d.get("structure_ok"):
    print("not ready: structure"); sys.exit(1)
if a.tok is not None and d.get("dispatch_token") != a.tok:
    print("not ready: token"); sys.exit(1)
print("[%s] READY" % a.seg); sys.exit(0)
'''

STUB_VALIDATE_DRAFT = '''#!/usr/bin/env python3
import argparse, json, os, sys
p = argparse.ArgumentParser()
p.add_argument("seg")
p.add_argument("--candidate-file", dest="cf", default=None)
a = p.parse_args()
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = a.cf if a.cf else os.path.join(root, "segments", a.seg + ".draft.json")
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as e:
    print("FAIL: %s" % e); sys.exit(1)
if not isinstance(d, dict) or not d.get("quality_ok"):
    print("[%s] FAIL (quality)" % a.seg); sys.exit(1)
print("[%s] OK" % a.seg); sys.exit(0)
'''

STUB_REVIEW_READY = '''#!/usr/bin/env python3
import argparse, json, os, sys
p = argparse.ArgumentParser()
p.add_argument("seg")
p.add_argument("--expect-token", dest="tok", default=None)
p.add_argument("--candidate-file", dest="cf", default=None)
a = p.parse_args()
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = a.cf if a.cf else os.path.join(root, "segments", a.seg + ".review.json")
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as e:
    print(json.dumps({"ready": False, "reason": str(e)})); sys.exit(1)
ok = isinstance(d, dict) and d.get("schema_ok") and (a.tok is None or d.get("dispatch_token") == a.tok)
print(json.dumps({"ready": bool(ok)})); sys.exit(0 if ok else 1)
'''

STUB_DRAFT_SHA1 = '''#!/usr/bin/env python3
import sys
print("deadbeef"); sys.exit(0)
'''

FAKE_NODE = r'''#!/usr/bin/env python3
import json, os, re, sys, time
state = json.load(open(os.environ["CJ_STATE"], encoding="utf-8"))
argv = sys.argv[1:]                 # companion, subcmd, *rest
sub = argv[1] if len(argv) > 1 else ""
rest = argv[2:]

def opt(name):
    if name in rest:
        i = rest.index(name)
        return rest[i + 1] if i + 1 < len(rest) else None
    return None

def positional():
    for tok in rest:
        if not tok.startswith("--"):
            return tok
    return None

def log(entry):
    cl = state.get("call_log")
    if cl:
        with open(cl, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

cwd = opt("--cwd")

if sub == "task":
    log({"sub": "task", "cwd": cwd, "prompt_file": opt("--prompt-file"),
         "write": "--write" in rest, "fresh": "--fresh" in rest, "effort": opt("--effort")})
    if state.get("task_returncode", 0):
        sys.stderr.write("task boom")
        sys.exit(state["task_returncode"])
    seg = state["seg"]; tok = state["tok"]; kind = state["kind"]
    mode = state.get("attempt_mode", "valid")
    pf = opt("--prompt-file")
    att = None
    if pf and os.path.exists(pf):
        text = open(pf, encoding="utf-8").read()
        m = re.search(r"(/\S+\.att\.\S+\.json)", text)
        att = m.group(1) if m else None

    def payload(good_tok=True, quality=True, schema=True):
        if kind == "translate":
            return {"dispatch_token": tok if good_tok else tok + "_WRONG",
                    "seg": seg, "structure_ok": True, "quality_ok": quality}
        return {"dispatch_token": tok if good_tok else tok + "_WRONG",
                "schema_ok": schema, "draft_sha1": "deadbeef"}

    ext = "draft" if kind == "translate" else "review"
    if mode == "none":
        pass
    elif mode == "canonical_forge":
        canon = os.path.join(cwd, "segments", "%s.%s.json" % (seg, ext))
        json.dump(payload(), open(canon, "w", encoding="utf-8"))
    elif mode == "symlink" and att:
        target = att + ".target"
        json.dump(payload(), open(target, "w", encoding="utf-8"))
        try:
            os.symlink(target, att)
        except OSError:
            pass
    elif att:
        obj = {"valid": payload(True, True, True),
               "invalid_token": payload(False, True, True),
               "invalid_quality": payload(True, False, True),
               "invalid_schema": payload(True, True, False)}.get(mode, payload())
        json.dump(obj, open(att, "w", encoding="utf-8"))

    if state.get("no_jobid"):
        print(json.dumps({"status": "queued"}))
        sys.exit(0)
    print(json.dumps({"jobId": state.get("jobId", "job-1"), "status": "queued"}))
    sys.exit(0)

if sub == "status":
    log({"sub": "status", "cwd": cwd, "jobId": positional()})
    sleep = float(state.get("status_sleep", 0) or 0)
    if sleep:
        time.sleep(sleep)
    seq = state.get("status_seq", ["completed"])
    ctr_path = os.environ["CJ_STATE"] + ".ctr"
    try:
        n = int(open(ctr_path).read().strip())
    except Exception:
        n = 0
    with open(ctr_path, "w") as f:
        f.write(str(n + 1))
    st = seq[min(n, len(seq) - 1)]
    ws = state.get("status_ws", cwd)
    print(json.dumps({"job": {"status": st, "workspaceRoot": ws}}))
    sys.exit(0)

if sub == "cancel":
    jid = positional()
    log({"sub": "cancel", "cwd": cwd, "jobId": jid})
    cl = state.get("cancel_log")
    if cl:
        with open(cl, "a", encoding="utf-8") as f:
            f.write((jid or "") + "\n")
    print(json.dumps({}))
    sys.exit(0)

sys.exit(0)
'''

PROMPT_ONE = "Write your JSON ONLY to ⟦JOB_OUT⟧ and return DONE.\n"


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def _chmodx(path: Path):
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def build_root(tmp_path: Path):
    """A durable_root with segments/ + scripts/ (driver copied in, stub gates + fake node)."""
    root = tmp_path / "durable"
    (root / "segments").mkdir(parents=True)
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "codex_job.py").write_text(DRIVER_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    (scripts / "draft_ready.py").write_text(STUB_DRAFT_READY, encoding="utf-8")
    (scripts / "validate_draft.py").write_text(STUB_VALIDATE_DRAFT, encoding="utf-8")
    (scripts / "review_ready.py").write_text(STUB_REVIEW_READY, encoding="utf-8")
    (scripts / "draft_sha1.py").write_text(STUB_DRAFT_SHA1, encoding="utf-8")
    companion = root / "codex-companion.mjs"
    companion.write_text("// fake\n", encoding="utf-8")
    fake_node = root / "fake_node.py"
    fake_node.write_text(FAKE_NODE, encoding="utf-8")
    _chmodx(fake_node)
    return root, str(companion), str(fake_node)


def base_state(seg, tok, kind, **kw):
    s = {"seg": seg, "tok": tok, "kind": kind}
    s.update(kw)
    return s


def spawn_driver(root, companion, fake_node, seg, tok, kind, disp, state,
                 deadline=8, poll=1, popen=False, run_cwd=None, extra_args=None):
    """Materialize per-DISP state + prompt-file, then run (or Popen) the shipped driver."""
    seg_dir = root / "segments"
    state_file = root / ("state.%s.json" % disp)
    state = dict(state)
    state.setdefault("call_log", str(root / ("calls.%s.log" % disp)))
    state.setdefault("cancel_log", str(root / ("cancel.%s.log" % disp)))
    state_file.write_text(json.dumps(state), encoding="utf-8")
    ctr = Path(str(state_file) + ".ctr")
    if ctr.exists():
        ctr.unlink()
    prompt = seg_dir / (".codex_task.%s.%s.%s" % (kind, seg, disp))
    prompt.write_text(PROMPT_ONE, encoding="utf-8")
    # Mimic lane C's dispatch: the 8 FROZEN flags only (+ test-only --poll-sec/--node).
    # NO --write/--fresh/--effort -> the driver must add workspace-write + fresh + effort
    # to the internal codex launch itself.
    argv = [
        sys.executable, str(root / "scripts" / "codex_job.py"),
        "--kind", kind, "--companion", companion, "--cwd", str(root), "--seg", seg,
        "--prompt-file", str(prompt), "--expect-token", tok, "--disp", disp,
        "--deadline-sec", str(deadline), "--poll-sec", str(poll),
        "--node", fake_node,
    ]
    if extra_args:
        argv += extra_args
    env = dict(os.environ, CJ_STATE=str(state_file))
    cwd = run_cwd or str(root)
    if popen:
        return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=cwd, env=env)
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, cwd=cwd, env=env)


def parse_line(proc):
    return json.loads(proc.stdout.strip().splitlines()[-1])


def sentinel_path(root, seg, disp):
    return root / "segments" / (".codex_failed.%s.%s" % (seg, disp))


def wait_for(path, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if os.path.exists(path):
            return True
        time.sleep(0.03)
    return False


def read_calls(root, disp):
    p = root / ("calls.%s.log" % disp)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# --------------------------------------------------------------------------- #
# in-process white-box: usage / args (case k, j)
# --------------------------------------------------------------------------- #
_pf_ctr = [0]


def _prompt_file(tmp_path, text=PROMPT_ONE):
    _pf_ctr[0] += 1
    p = tmp_path / ("prompt_%d.txt" % _pf_ctr[0])   # unique so a default never overwrites an override
    p.write_text(text, encoding="utf-8")
    return str(p)


def _companion_file(tmp_path):
    c = tmp_path / "codex-companion.mjs"
    c.write_text("//\n", encoding="utf-8")
    return str(c)


def _argv(tmp_path, **over):
    d = dict(kind="translate", companion=_companion_file(tmp_path), cwd=str(tmp_path),
             seg="c001", prompt_file=_prompt_file(tmp_path), expect_token="RUN:c001",
             disp="d1", deadline_sec="600")
    d.update(over)
    return ["--kind", d["kind"], "--companion", d["companion"], "--cwd", d["cwd"],
            "--seg", d["seg"], "--prompt-file", d["prompt_file"],
            "--expect-token", d["expect_token"], "--disp", d["disp"],
            "--deadline-sec", d["deadline_sec"], "--node", "node"]


def test_usage_bad_seg(tmp_path):
    assert codex_job.main(_argv(tmp_path, seg="bad-seg")) == 2


def test_usage_missing_companion(tmp_path):
    assert codex_job.main(_argv(tmp_path, companion=str(tmp_path / "nope.mjs"))) == 2


def test_usage_missing_prompt_file(tmp_path):
    argv = _argv(tmp_path)
    i = argv.index("--prompt-file")
    argv[i + 1] = str(tmp_path / "nope.txt")
    assert codex_job.main(argv) == 2


def test_usage_nonpositive_deadline(tmp_path):
    assert codex_job.main(_argv(tmp_path, deadline_sec="0")) == 2
    assert codex_job.main(_argv(tmp_path, deadline_sec="-5")) == 2


@pytest.mark.parametrize("bad_disp", ["a/b", ".", "..", "a b", ""])
def test_usage_bad_disp(tmp_path, bad_disp):
    assert codex_job.main(_argv(tmp_path, disp=bad_disp)) == 2


def test_usage_bad_kind(tmp_path):
    with pytest.raises(SystemExit) as ei:   # argparse choices -> exit 2
        codex_job.main(_argv(tmp_path, kind="frobnicate"))
    assert ei.value.code == 2


@pytest.mark.parametrize("text", ["no placeholder here\n",
                                  "two ⟦JOB_OUT⟧ and ⟦JOB_OUT⟧ here\n"])
def test_job_out_count_must_be_exactly_one(tmp_path, text):
    assert codex_job.main(_argv(tmp_path, prompt_file=_prompt_file(tmp_path, text))) == 2


# --------------------------------------------------------------------------- #
# in-process white-box: time ceilings + finalize-tail (case o)
# --------------------------------------------------------------------------- #
def _mkjob(tmp_path, kind="translate", seg="c001", tok="RUN:c001", disp="d1",
           deadline=100, poll=1):
    seg_dir = tmp_path / "durable" / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "durable"
    companion = _companion_file(tmp_path)
    return codex_job.CodexJob(
        kind=kind, seg=seg, tok=tok, disp=disp, root=str(root), companion=companion,
        prompt_text=PROMPT_ONE, prompt_file=_prompt_file(tmp_path), deadline_sec=deadline,
        poll_sec=poll, effort="high", node="node")


def test_time_ceilings(tmp_path):
    job = _mkjob(tmp_path, deadline=100)
    assert 95 < job.poll_remaining() <= 100
    # abs_ceiling = poll_deadline + 150
    assert 245 < job.abs_remaining() <= 250
    # poll_timeout is capped by PER_CALL_CAP even when poll_remaining is huge
    assert job.poll_timeout() == codex_job.PER_CALL_CAP


def test_finalize_timeout_reserves_tail(tmp_path):
    job = _mkjob(tmp_path, deadline=100)
    now = time.monotonic()
    # abs_remaining ~= 15 -> finalize_timeout ~= 5 (min(90, 15 - FINALIZE_TAIL))
    job.abs_ceiling = now + 15
    assert 3.5 < job.finalize_timeout() < 5.5
    # abs_remaining ~= FINALIZE_TAIL -> finalize_timeout clamps to 0 (refuse to begin)
    job.abs_ceiling = now + codex_job.FINALIZE_TAIL
    assert job.finalize_timeout() == 0.0
    job.abs_ceiling = now + (codex_job.FINALIZE_TAIL - 3)
    assert job.finalize_timeout() == 0.0


def test_run_refuses_promote_when_budget_exhausted(tmp_path, monkeypatch):
    """A job that completes with abs_remaining() <= FINALIZE_TAIL must NOT promote."""
    job = _mkjob(tmp_path, deadline=5)
    monkeypatch.setattr(job, "resolve_expected_ws_root", lambda: job.root)
    monkeypatch.setattr(job, "hygiene", lambda ws: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)

    def fake_launch():
        job.jobId = "J"
        return True
    monkeypatch.setattr(job, "launch", fake_launch)

    def fake_poll():
        job.job_status = "completed"
    monkeypatch.setattr(job, "poll", fake_poll)
    # Exhaust the finalize budget so the promote guard refuses to begin.
    monkeypatch.setattr(job, "abs_remaining", lambda: 2.0)
    validated = {"called": False}
    monkeypatch.setattr(job, "validate_attempt",
                        lambda: validated.__setitem__("called", True) or True)
    rc = job.run()
    assert rc == 1
    assert job.promoted is False
    assert validated["called"] is False          # refused to even BEGIN validation/promote
    assert not os.path.exists(job.canonical)      # canonical never created
    assert os.path.exists(job.fail_sentinel)


# --------------------------------------------------------------------------- #
# in-process white-box: validate_attempt order + defects (cases e, s, n)
# --------------------------------------------------------------------------- #
def _gate_recorder(results):
    calls = []

    def _gate(args, timeout):
        calls.append(args[0])
        rc = results.get(args[0], 0)
        return SimpleNamespace(returncode=rc, stdout="")
    return _gate, calls


def test_validate_attempt_translate_pass(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    Path(job.attempt).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is True
    assert calls == ["draft_ready.py", "validate_draft.py"]  # order: ready THEN quality


def test_validate_attempt_translate_wrong_token_short_circuits(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    Path(job.attempt).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 1})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is False
    assert calls == ["draft_ready.py"]  # validate_draft not reached


def test_validate_attempt_translate_quality_defect(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    Path(job.attempt).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 1})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is False
    assert calls == ["draft_ready.py", "validate_draft.py"]


def test_validate_attempt_review_uses_review_ready(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="review")
    Path(job.attempt).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder({"review_ready.py": 0})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is True
    assert calls == ["review_ready.py"]


def test_validate_attempt_symlink_refused(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    target = Path(job.attempt + ".target")
    target.write_text("{}", encoding="utf-8")
    os.symlink(target, job.attempt)
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is False   # O_NOFOLLOW open fails
    assert calls == []                       # no gate ever runs on a symlink


def test_validate_attempt_non_regular_refused(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    os.mkfifo(job.attempt)
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is False   # not S_ISREG
    assert calls == []


# --------------------------------------------------------------------------- #
# in-process white-box: poll to terminal / deadline-cancel (cases b/c)
# --------------------------------------------------------------------------- #
def _status_runner(statuses, record):
    it = iter(statuses)
    last = {"v": None}

    def _run(argv, timeout):
        sub = argv[2] if len(argv) > 2 else ""
        record.append((sub, timeout))
        if sub == "status":
            try:
                last["v"] = next(it)
            except StopIteration:
                pass
            return SimpleNamespace(returncode=0,
                                   stdout=json.dumps({"job": {"status": last["v"]}}))
        return SimpleNamespace(returncode=0, stdout="{}")
    return _run


def test_poll_reaches_completed(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, deadline=100, poll=0)
    job.jobId = "J"
    rec = []
    monkeypatch.setattr(job, "_run", _status_runner(["queued", "running", "completed"], rec))
    job.poll()
    assert job.job_status == "completed"
    assert job.timed_out is False
    assert not any(sub == "cancel" for sub, _ in rec)


def test_poll_failed_is_terminal_no_cancel(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, deadline=100, poll=0)
    job.jobId = "J"
    rec = []
    monkeypatch.setattr(job, "_run", _status_runner(["failed"], rec))
    job.poll()
    assert job.job_status == "failed"
    assert job.timed_out is False


def test_poll_deadline_cancels_and_times_out(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, deadline=100, poll=0)
    job.jobId = "J"
    job.poll_deadline = time.monotonic() - 1   # already past
    rec = []
    monkeypatch.setattr(job, "_run", _status_runner(["running"], rec))
    job.poll()
    assert job.timed_out is True
    assert any(sub == "cancel" for sub, _ in rec)


# --------------------------------------------------------------------------- #
# in-process white-box: hygiene guard (case v)
# --------------------------------------------------------------------------- #
def _hygiene_job(tmp_path, prior_jobid="jobP", prior_status="launched"):
    job = _mkjob(tmp_path)
    Path(job.joblog).write_text(json.dumps(
        {"jobId": prior_jobid, "status": prior_status}), encoding="utf-8")
    return job


def _hygiene_runner(status_ws, status_state, cancels):
    def _run(argv, timeout):
        sub = argv[2] if len(argv) > 2 else ""
        if sub == "status":
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"job": {"status": status_state, "workspaceRoot": status_ws}}))
        if sub == "cancel":
            cancels.append(argv[3])
            return SimpleNamespace(returncode=0, stdout="{}")
        return SimpleNamespace(returncode=0, stdout="{}")
    return _run


def test_hygiene_cancels_matching_ws_active(tmp_path, monkeypatch):
    job = _hygiene_job(tmp_path)
    cancels = []
    monkeypatch.setattr(job, "_run", _hygiene_runner(job.root, "running", cancels))
    job.hygiene(job.root)
    assert cancels == ["jobP"]


def test_hygiene_skips_mismatched_ws(tmp_path, monkeypatch):
    job = _hygiene_job(tmp_path)
    cancels = []
    monkeypatch.setattr(job, "_run", _hygiene_runner("/some/other/root", "running", cancels))
    job.hygiene(job.root)
    assert cancels == []   # forged/cross-store jobId is never cancelled


def test_hygiene_skips_inactive_job(tmp_path, monkeypatch):
    job = _hygiene_job(tmp_path)
    cancels = []
    monkeypatch.setattr(job, "_run", _hygiene_runner(job.root, "completed", cancels))
    job.hygiene(job.root)
    assert cancels == []


def test_hygiene_skips_terminal_joblog(tmp_path, monkeypatch):
    job = _hygiene_job(tmp_path, prior_status="terminal")
    cancels = []
    monkeypatch.setattr(job, "_run", _hygiene_runner(job.root, "running", cancels))
    job.hygiene(job.root)
    assert cancels == []


@pytest.mark.skipif(not _HAS_GIT, reason="git unavailable")
def test_expected_ws_root_is_git_toplevel_not_durable_root(tmp_path):
    """Nested-git regression: expected_ws_root is the git TOPLEVEL, not the durable_root
    subdir -- the old `== durable_root` guard silently disabled hygiene here."""
    repo = tmp_path / "repo"
    (repo / "durable" / "segments").mkdir(parents=True)
    (repo / "durable" / "scripts").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    job = codex_job.CodexJob(
        kind="translate", seg="c001", tok="RUN:c001", disp="d1",
        root=str(repo / "durable"), companion=_companion_file(tmp_path),
        prompt_text=PROMPT_ONE, prompt_file=_prompt_file(tmp_path), deadline_sec=60,
        poll_sec=1, effort=None, node="node")
    expected = job.resolve_expected_ws_root()
    assert expected == os.path.realpath(str(repo))
    assert expected != job.root   # NOT the durable_root subdir


# --------------------------------------------------------------------------- #
# in-process white-box: fail-sentinel forged-entry safety (case w driver-side)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["regular", "symlink", "fifo", "dir"])
def test_fail_sentinel_survives_forged_entry(tmp_path, kind):
    job = _mkjob(tmp_path)
    sp = job.fail_sentinel
    if kind == "regular":
        Path(sp).write_text("x", encoding="utf-8")
    elif kind == "symlink":
        tgt = Path(sp + ".t"); tgt.write_text("x", encoding="utf-8")
        os.symlink(tgt, sp)
    elif kind == "fifo":
        os.mkfifo(sp)
    else:
        os.mkdir(sp)
    # Must NOT raise, block, or follow the entry.
    job._write_fail_sentinel()


def test_fail_sentinel_and_scoping_by_disp(tmp_path):
    j1 = _mkjob(tmp_path, disp="D1")
    j2 = _mkjob(tmp_path, disp="D2")
    assert j1.fail_sentinel.endswith(".codex_failed.c001.D1")
    assert j2.fail_sentinel.endswith(".codex_failed.c001.D2")
    assert j1.fail_sentinel != j2.fail_sentinel   # per-dispatch scoping


# --------------------------------------------------------------------------- #
# in-process white-box: safe adoption (case h)
# --------------------------------------------------------------------------- #
def test_safe_adopt_translate_valid_canonical(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    Path(job.canonical).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.safe_adopt() is True
    assert calls == ["draft_ready.py", "validate_draft.py"]


def test_safe_adopt_absent_canonical(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.safe_adopt() is False
    assert calls == []


# --------------------------------------------------------------------------- #
# in-process white-box: launch parsing (case d)
# --------------------------------------------------------------------------- #
def test_launch_no_jobid_returns_false(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    monkeypatch.setattr(job, "_run",
                        lambda argv, timeout: SimpleNamespace(returncode=0, stdout="{}"))
    assert job.launch() is False


def test_launch_parses_jobid_and_writes_launched_joblog(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    monkeypatch.setattr(job, "_run", lambda argv, timeout: SimpleNamespace(
        returncode=0, stdout=json.dumps({"jobId": "job-77", "status": "queued"})))
    assert job.launch() is True
    assert job.jobId == "job-77"
    rec = json.loads(Path(job.joblog).read_text())
    assert rec["jobId"] == "job-77" and rec["status"] == "launched"


def test_default_launch_argv_is_write_and_high_effort_with_8_flags_only(tmp_path, monkeypatch):
    """#198 regression (DIRECT white-box on the built argv): parsing lane C's 8-flag-only
    invocation yields effort defaulting to "high" and write=False on the CLI, yet the codex
    `task` argv the driver BUILDS still contains --write, --fresh, AND --effort high. This is
    the assertion that would have caught a read-only internal launch (== #198 unfixed)."""
    args = codex_job._build_parser().parse_args([
        "--kind", "translate", "--companion", _companion_file(tmp_path), "--cwd", str(tmp_path),
        "--seg", "c001", "--prompt-file", _prompt_file(tmp_path), "--expect-token", "RUN:c001",
        "--disp", "D1", "--deadline-sec", "600"])
    assert args.effort == "high"    # default -- NO --effort on the CLI
    assert args.write is False       # --write NOT on the CLI, yet the internal launch adds it
    job = _mkjob(tmp_path)
    job.effort = args.effort
    job.final_prompt = str(tmp_path / "fp.txt")
    captured = {}

    def fake_run(argv, timeout):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=json.dumps({"jobId": "j1"}))
    monkeypatch.setattr(job, "_run", fake_run)
    assert job.launch() is True
    argv = captured["argv"]
    assert "--write" in argv and "--fresh" in argv
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[2] == "task" and "--background" in argv and "--json" in argv


def test_launch_argv_includes_model_when_set(tmp_path, monkeypatch):
    """#197 -- a pinned CodexJob.model threads to the internal codex `task`
    launch argv as a real --model flag."""
    job = _mkjob(tmp_path)
    job.model = "gpt-5.3-codex"
    job.final_prompt = str(tmp_path / "fp.txt")
    captured = {}

    def fake_run(argv, timeout):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=json.dumps({"jobId": "j1"}))
    monkeypatch.setattr(job, "_run", fake_run)
    assert job.launch() is True
    argv = captured["argv"]
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "gpt-5.3-codex"


def test_launch_argv_omits_model_when_unset(tmp_path, monkeypatch):
    """#197 -- positive control: CodexJob's `model` keyword defaults to None
    (see _mkjob, which never passes it), and the internal launch argv then
    carries NO --model flag at all."""
    job = _mkjob(tmp_path)
    assert job.model is None
    job.final_prompt = str(tmp_path / "fp.txt")
    captured = {}

    def fake_run(argv, timeout):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=json.dumps({"jobId": "j1"}))
    monkeypatch.setattr(job, "_run", fake_run)
    assert job.launch() is True
    assert "--model" not in captured["argv"]


# --------------------------------------------------------------------------- #
# SUBPROCESS integration (fake node + stub gates)
# --------------------------------------------------------------------------- #
def test_e2e_promote_translate(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["running", "completed"]))
    line = parse_line(proc)
    assert proc.returncode == 0 and line["ok"] is True and line["adopted"] is False
    canon = root / "segments" / "c001.draft.json"
    assert canon.exists()
    d = json.loads(canon.read_text())
    assert d["dispatch_token"] == tok and d["quality_ok"] is True
    # atomic promote: no attempt leftover, no .bak.*, no fail sentinel
    assert not sentinel_path(root, seg, "D1").exists()
    assert not list((root / "segments").glob(".att.*"))
    assert not list((root / "segments").glob(".bak.*"))
    # terminal hygiene joblog recorded ok:true
    jl = json.loads((root / "segments" / ".codex_job.c001.json").read_text())
    assert jl["status"] == "terminal" and jl["ok"] is True
    # the caller's task-file was cleaned
    assert not (root / "segments" / ".codex_task.translate.c001.D1").exists()


def test_isolation_reject_invalid_quality_preserves_canonical(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    canon = root / "segments" / "c001.draft.json"
    prior = {"prior": "canonical", "structure_ok": True}
    canon.write_text(json.dumps(prior), encoding="utf-8")
    before = canon.read_bytes()
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="invalid_quality",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False
    assert canon.read_bytes() == before              # canonical byte-identical (not promoted)
    assert sentinel_path(root, seg, "D1").exists()
    assert not list((root / "segments").glob(".att.*"))  # attempt cleaned


def test_wrong_token_attempt_not_promoted(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="invalid_token",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False
    assert not (root / "segments" / "c001.draft.json").exists()
    assert sentinel_path(root, seg, "D1").exists()


def test_failed_job_writes_sentinel_and_terminal_joblog(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="none",
                                   status_seq=["failed"]))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False and line["job_status"] == "failed"
    assert sentinel_path(root, seg, "D1").exists()
    jl = json.loads((root / "segments" / ".codex_job.c001.json").read_text())
    assert jl["status"] == "terminal" and jl["ok"] is False


def test_deadline_exceeded_cancels(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["running"], jobId="jobT"),
                        deadline=2, poll=1)
    line = parse_line(proc)
    assert proc.returncode == 1 and line["timed_out"] is True
    cancels = (root / "cancel.D1.log")
    assert cancels.exists() and "jobT" in cancels.read_text()
    assert sentinel_path(root, seg, "D1").exists()


def test_launch_no_jobid_subprocess(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="none",
                                   no_jobid=True))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False
    assert sentinel_path(root, seg, "D1").exists()


def test_cwd_binding_every_call(tmp_path):
    """Run from an unrelated cwd; assert every fake-node call received --cwd <root>."""
    root, companion, node = build_root(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["completed"]),
                        run_cwd=str(other))
    assert proc.returncode == 0
    calls = read_calls(root, "D1")
    assert calls, "expected fake-node calls to be logged"
    assert all(c["cwd"] == str(root) for c in calls)


def test_internal_launch_always_write_and_effort_high(tmp_path):
    """#198 regression: invoked with the 8 frozen flags ONLY (lane C's form, NO
    --write/--fresh/--effort), the internal codex `task` launch STILL carries --write
    (workspace-write so codex can write its ⟦JOB_OUT⟧ attempt) and --effort high."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["completed"]))
    assert proc.returncode == 0
    task_calls = [c for c in read_calls(root, "D1") if c["sub"] == "task"]
    assert task_calls, "the driver must have launched codex"
    assert task_calls[0]["write"] is True          # workspace-write (the #198 fix)
    assert task_calls[0]["effort"] == "high"        # effort conveyed as a real flag


def test_hung_status_bounded_by_deadline(tmp_path):
    """A status call that sleeps past the per-call cap does not run past deadline+150."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    t0 = time.monotonic()
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["running"], status_sleep=30, jobId="jobH"),
                        deadline=2, poll=1)
    elapsed = time.monotonic() - t0
    line = parse_line(proc)
    assert line["timed_out"] is True
    assert elapsed < 2 + codex_job.CODEX_FINALIZE_BUDGET_SEC   # never past abs_ceiling
    assert elapsed < 60                                        # and nowhere near the 30s*N sleep sum


def test_forged_canonical_no_attempt_not_promoted(tmp_path):
    """A fake codex that writes the canonical DIRECTLY (never its attempt): the driver does
    NOT promote (attempt missing) and writes the fail sentinel."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="canonical_forge",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False
    assert sentinel_path(root, seg, "D1").exists()
    assert not list((root / "segments").glob(".att.*"))


def test_symlink_attempt_refused_subprocess(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    canon = root / "segments" / "c001.draft.json"
    canon.write_text(json.dumps({"prior": True}), encoding="utf-8")
    before = canon.read_bytes()
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="symlink",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False
    assert canon.read_bytes() == before          # canonical untouched
    assert sentinel_path(root, seg, "D1").exists()


def test_adoption_preexisting_valid_canonical(tmp_path):
    """A pre-existing valid same-token canonical -> adopt, never launch, no sentinel."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    canon = root / "segments" / "c001.draft.json"
    canon.write_text(json.dumps(
        {"dispatch_token": tok, "seg": seg, "structure_ok": True, "quality_ok": True}),
        encoding="utf-8")
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="none",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 0 and line["adopted"] is True and line["ok"] is True
    assert not sentinel_path(root, seg, "D1").exists()
    calls = read_calls(root, "D1")
    assert not any(c["sub"] == "task" for c in calls)   # NEVER launched


def test_review_kind_promote(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001:r1"
    proc = spawn_driver(root, companion, node, seg, tok, "review", "D1",
                        base_state(seg, tok, "review", attempt_mode="valid",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 0 and line["ok"] is True
    assert (root / "segments" / "c001.review.json").exists()


# --------------------------------------------------------------------------- #
# SUBPROCESS: per-seg flock serialization (cases l1, l2, m)
# --------------------------------------------------------------------------- #
@skip_no_flock
def test_flock_hold_past_deadline_lease_held_joblog_protected(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    # Holder: acquires the lock, launches (writes launched joblog jobHold), then holds it
    # across a long IN-PROCESS poll sleep (poll=8) -> no subprocess orphan, no external sleep.
    holder = spawn_driver(
        root, companion, node, seg, tok, "translate", "HOLD",
        base_state(seg, tok, "translate", attempt_mode="valid",
                   status_seq=["running", "running", "running"], jobId="jobHold"),
        deadline=30, poll=8, popen=True)
    assert isinstance(holder, subprocess.Popen)
    joblog = root / "segments" / ".codex_job.c001.json"
    assert wait_for(joblog, timeout=10), "holder never acquired the lock / wrote its joblog"
    assert json.loads(joblog.read_text())["jobId"] == "jobHold"
    try:
        # Contender: short window; the lock is held, so it must give up as lease-held.
        contender = spawn_driver(
            root, companion, node, seg, tok, "translate", "CONT",
            base_state(seg, tok, "translate", attempt_mode="valid", status_seq=["completed"]),
            deadline=1, poll=1)
        cline = parse_line(contender)
        assert contender.returncode == 1 and cline["reason"] == "lease-held"
        assert sentinel_path(root, seg, "CONT").exists()        # its OWN sentinel
        # HIGH-3 r8: the holder's joblog is NOT clobbered by the lease-loser.
        assert json.loads(joblog.read_text())["jobId"] == "jobHold"
    finally:
        holder.kill()
        holder.wait(timeout=10)


@skip_no_flock
def test_flock_loser_adopts_after_holder_releases(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    # Holder holds ~2s (one IN-PROCESS poll sleep) then promotes a valid canonical + releases.
    holder = spawn_driver(
        root, companion, node, seg, tok, "translate", "HOLD",
        base_state(seg, tok, "translate", attempt_mode="valid",
                   status_seq=["running", "completed"], jobId="jobHold"),
        deadline=30, poll=2, popen=True)
    assert isinstance(holder, subprocess.Popen)
    assert wait_for(root / "segments" / ".codex_job.c001.json", timeout=10)
    # Contender starts while the holder still holds; long enough to acquire after release,
    # then it finds the promoted valid canonical and ADOPTS (no second launch).
    contender = spawn_driver(
        root, companion, node, seg, tok, "translate", "CONT",
        base_state(seg, tok, "translate", attempt_mode="none", status_seq=["completed"]),
        deadline=20, poll=1)
    holder.wait(timeout=30)
    holder_out = holder.stdout.read() if holder.stdout is not None else ""
    hline = parse_line(SimpleNamespace(stdout=holder_out))
    cline = parse_line(contender)
    assert hline["ok"] is True and hline["adopted"] is False   # holder promoted
    assert contender.returncode == 0 and cline["adopted"] is True
    assert not sentinel_path(root, seg, "CONT").exists()
    calls = read_calls(root, "CONT")
    assert not any(c["sub"] == "task" for c in calls)          # loser never launched
    assert not list((root / "segments").glob(".att.*"))        # neither left an orphan attempt


@skip_no_flock
def test_flock_auto_release_on_holder_sigkill(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    # Holder holds the lock across a long IN-PROCESS poll sleep (poll=30), then we SIGKILL it
    # mid-sleep -> no subprocess grandchild is left running.
    holder = spawn_driver(
        root, companion, node, seg, tok, "translate", "HOLD",
        base_state(seg, tok, "translate", attempt_mode="valid",
                   status_seq=["running"], jobId="jobHold"),
        deadline=60, poll=30, popen=True)
    assert isinstance(holder, subprocess.Popen)
    assert wait_for(root / "segments" / ".codex_job.c001.json", timeout=10)
    holder.send_signal(signal.SIGKILL)
    holder.wait(timeout=10)
    # A fresh driver acquires the auto-released lock (no pid/age logic, no deadlock) and runs.
    t0 = time.monotonic()
    contender = spawn_driver(
        root, companion, node, seg, tok, "translate", "CONT",
        base_state(seg, tok, "translate", attempt_mode="valid", status_seq=["completed"]),
        deadline=15, poll=1)
    elapsed = time.monotonic() - t0
    cline = parse_line(contender)
    assert contender.returncode == 0 and cline["ok"] is True   # acquired + promoted
    assert cline["reason"] != "lease-held"
    assert elapsed < 15                                         # no deadlock waiting on a dead holder
    assert read_calls(root, "CONT")                            # it did launch (held the lock)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
