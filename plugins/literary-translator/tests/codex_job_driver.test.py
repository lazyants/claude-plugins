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

#409 SANDBOX HARDENING: codex is now launched with `--cwd` pointed at a per-invocation
write-isolated sandbox (never self.root/durable_root -- see codex_job.py's module
docstring), so FAKE_NODE's `task`/`status`/`cancel` handlers below receive that sandbox
path as `cwd`, not root. FAKE_NODE additionally enforces --cwd consistency between `task`
and any later `status`/`cancel` for the SAME jobId (writing a small per-job marker at
launch and checking it on lookup) -- this is what makes the SUBPROCESS suite actually
exercise the real requirement (poll()/hygiene() MUST pass the exact --cwd the job was
launched with, matching codex-companion's own workspaceRoot-keyed job store) rather than
passing vacuously regardless of which cwd the driver happens to send.
"""

import errno
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"
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
# #412: accept (and use if given) --durable-root -- codex_job.py's _gate() now
# forwards it to draft_ready.py too (it joined lane A's --durable-root contract
# alongside review_ready.py); the stub must not choke on an unrecognized flag
# the same way the real draft_ready.py must not.
p.add_argument("--durable-root", dest="dr", default=None)
a = p.parse_args()
root = os.path.abspath(a.dr) if a.dr else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
# #412: same --durable-root adoption as STUB_DRAFT_READY above.
p.add_argument("--durable-root", dest="dr", default=None)
a = p.parse_args()
root = os.path.abspath(a.dr) if a.dr else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
# LT-409: accept (and ignore, or use if given) --durable-root -- codex_job.py's _gate()
# now forwards it to review_ready.py per lane A's confirmed contract; the stub must not
# choke on an unrecognized flag the same way the real review_ready.py must not.
p.add_argument("--durable-root", dest="dr", default=None)
a = p.parse_args()
root = os.path.abspath(a.dr) if a.dr else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

# #409: mimic codex-companion's real workspaceRoot-keyed job store closely enough that a
# status/cancel call with the WRONG --cwd genuinely fails to find the job -- otherwise this
# stub would pass regardless of whether the driver propagates the sandbox cwd correctly.
def job_cwd_marker(jid):
    return os.environ["CJ_STATE"] + ".jobcwd." + jid

if sub == "task":
    log({"sub": "task", "cwd": cwd, "prompt_file": opt("--prompt-file"),
         "write": "--write" in rest, "fresh": "--fresh" in rest, "effort": opt("--effort")})
    if state.get("task_returncode", 0):
        # #400: task_stderr lets a test simulate the companion's own thrown-error
        # text (e.g. an auth/quota message) on a launch failure, instead of the
        # generic "task boom" placeholder.
        sys.stderr.write(state.get("task_stderr", "task boom"))
        sys.exit(state["task_returncode"])
    seg = state["seg"]; tok = state["tok"]; kind = state["kind"]
    mode = state.get("attempt_mode", "valid")
    pf = opt("--prompt-file")
    att = None
    if pf and os.path.exists(pf):
        text = open(pf, encoding="utf-8").read()
        m = re.search(r"(/\S+/attempt\.\S+\.json)", text)
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
        # #409: forges what it BELIEVES is the canonical, relative to its OWN --cwd (the
        # only location a real write-confined codex could ever land a write) -- with cwd
        # now the sandbox, this lands harmlessly inside it, nowhere near the real canonical.
        canon = os.path.join(cwd, "segments", "%s.%s.json" % (seg, ext))
        os.makedirs(os.path.dirname(canon), exist_ok=True)
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
    jid = state.get("jobId", "job-1")
    with open(job_cwd_marker(jid), "w", encoding="utf-8") as f:
        f.write(cwd or "")
    print(json.dumps({"jobId": jid, "status": "queued"}))
    sys.exit(0)

if sub == "status":
    jid = positional()
    log({"sub": "status", "cwd": cwd, "jobId": jid})
    try:
        launched_cwd = open(job_cwd_marker(jid), encoding="utf-8").read()
    except OSError:
        launched_cwd = None
    if launched_cwd is not None and launched_cwd != cwd:
        sys.stderr.write("No job found for \"%s\".\n" % jid)
        sys.exit(1)
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
    job_obj = {"status": st, "workspaceRoot": ws}
    # #400: status_error_message mimics the companion's own job-store field
    # (job.errorMessage, persisted when its tracked-job runner catches a thrown
    # exception -- e.g. quota/auth -- verified directly against the installed
    # codex-companion.mjs's lib/tracked-jobs.mjs), so a test can simulate a
    # failure that carries a real cause instead of a bare status string.
    err = state.get("status_error_message")
    if err:
        job_obj["errorMessage"] = err
    print(json.dumps({"job": job_obj}))
    sys.exit(0)

if sub == "cancel":
    jid = positional()
    log({"sub": "cancel", "cwd": cwd, "jobId": jid})
    try:
        launched_cwd = open(job_cwd_marker(jid), encoding="utf-8").read()
    except OSError:
        launched_cwd = None
    if launched_cwd is not None and launched_cwd != cwd:
        sys.stderr.write("No active job found for \"%s\".\n" % jid)
        sys.exit(1)
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


def _seed_sandbox(tmp_path, job, content=None, mode="file"):
    """#409: tests that used to write directly to job.attempt (the STAGING slot) now need
    to seed job.sandbox_dir/job.sandbox_attempt instead -- codex's own output never lands
    in staging directly anymore; validate_attempt()/_defer_attempt() PUBLISH it there via
    the fd-pinned copy. Mirrors what CodexJob._setup_sandbox() does for real, just without
    the git-confinement check (irrelevant to these white-box, non-dispatching tests)."""
    sbx = tmp_path / ("sandbox_%s_%s" % (job.seg, job.inv))
    sbx.mkdir(parents=True, exist_ok=True)
    job.sandbox_dir = str(sbx)
    ext = "draft" if job.kind == "translate" else "review"
    job.sandbox_attempt = str(sbx / ("attempt.%s.json" % ext))
    if mode == "file":
        Path(job.sandbox_attempt).write_text(
            "{}" if content is None else content, encoding="utf-8")
    elif mode == "symlink":
        target = Path(job.sandbox_attempt + ".target")
        target.write_text("{}" if content is None else content, encoding="utf-8")
        os.symlink(target, job.sandbox_attempt)
    elif mode == "fifo":
        os.mkfifo(job.sandbox_attempt)
    elif mode == "absent":
        pass
    return job.sandbox_attempt


# --------------------------------------------------------------------------- #
# LT-409 relay: two independent seams in _gate() -- the DATA root
# (_durable_root_args, lane A's confirmed --durable-root contract) and the TRUSTED
# EXECUTABLE directory (_trusted_scripts_dir, lane A's pending mechanism). Never
# collapse them even though they hold the same value today.
# --------------------------------------------------------------------------- #
def test_trusted_scripts_dir_defaults_to_module_scripts_dir(tmp_path):
    job = _mkjob(tmp_path)
    assert job._trusted_scripts_dir() == codex_job.SCRIPTS_DIR


def test_durable_root_args_for_all_three_contract_scripts(tmp_path):
    """#412: draft_ready.py/validate_draft.py joined review_ready.py under lane A's
    --durable-root contract (the OTHER agent landed --durable-root support on both,
    67 tests green) -- this driver adopting the redirect required it: moving
    _trusted_scripts_dir() to the plugin install path only works if every script it
    resolves from there can still find the real durable_root's segments/ (see the
    seam comment above _DURABLE_ROOT_CONTRACT_SCRIPTS). All three now get it."""
    job = _mkjob(tmp_path)
    assert job._durable_root_args("review_ready.py") == ["--durable-root", job.root]
    assert job._durable_root_args("draft_ready.py") == ["--durable-root", job.root]
    assert job._durable_root_args("validate_draft.py") == ["--durable-root", job.root]


def test_gate_forwards_durable_root_to_all_three_contract_scripts(tmp_path, monkeypatch):
    """End-to-end through _gate() (not the raw helper): the argv actually built for
    each of the three contract scripts carries --durable-root <job.root>."""
    job = _mkjob(tmp_path)
    captured = {}

    def fake_run(argv, timeout):
        captured[os.path.basename(argv[1])] = argv
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(job, "_run", fake_run)

    for script in ("review_ready.py", "draft_ready.py", "validate_draft.py"):
        job._gate([script, job.seg, "--expect-token", job.tok], 10)
        argv = captured[script]
        assert "--durable-root" in argv, f"{script} must carry --durable-root"
        assert argv[argv.index("--durable-root") + 1] == job.root


def test_preflight_same_device_passes_on_real_layout(tmp_path):
    """Positive control: segdir/attempt/pending are ONE directory today, so the real
    os.stat()-based check must pass on an ordinary checkout."""
    job = _mkjob(tmp_path)
    assert job._preflight_same_device() is True


def test_preflight_same_device_refuses_on_mismatch(tmp_path, monkeypatch):
    """#409 property 3: a private-staging directory on a DIFFERENT filesystem than
    segments/ must refuse before any dispatch -- a cross-device os.replace() at promote
    time is not atomic. Hard to fabricate two REAL filesystems in a portable unit test, so
    this pins the check's own logic by mocking os.stat's st_dev. segdir and
    dirname(attempt)/dirname(pending) are literally the SAME path string in this fixture
    (attempt/pending live directly in segdir), so the three os.stat() calls inside
    _preflight_same_device cannot be told apart by PATH -- distinguish by CALL ORDER
    instead (the method's own source stats segdir first, then attempt's dir, then
    pending's dir) and bump only the first."""
    job = _mkjob(tmp_path)
    real_stat = os.stat
    calls = {"n": 0}

    def fake_stat(path, *a, **kw):
        st = real_stat(path, *a, **kw)
        calls["n"] += 1
        if calls["n"] == 1:   # the segdir stat, per _preflight_same_device's own order
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev + 1, st.st_nlink,
                                   st.st_uid, st.st_gid, st.st_size, st.st_atime,
                                   st.st_mtime, st.st_ctime))
        return st
    monkeypatch.setattr(os, "stat", fake_stat)
    assert job._preflight_same_device() is False
    assert calls["n"] >= 1, "the check must actually call os.stat"


def test_run_refuses_dispatch_on_device_mismatch(tmp_path, monkeypatch):
    """End-to-end through run(): a device-mismatch preflight failure refuses BEFORE the
    sandbox is even created (no real codex turn spent) -- reason is diagnosable, exit 1,
    fail sentinel written, canonical never touched."""
    job = _mkjob(tmp_path)
    monkeypatch.setattr(job, "_preflight_same_device", lambda: False)
    setup_called = {"v": False}
    monkeypatch.setattr(job, "_setup_sandbox",
                        lambda: setup_called.__setitem__("v", True) or True)
    rc = job.run()
    assert rc == 1
    assert job.reason == "device-mismatch"
    assert setup_called["v"] is False   # refused BEFORE even attempting the sandbox
    assert os.path.exists(job.fail_sentinel)
    assert not os.path.exists(job.canonical)


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
    """A job that completes with abs_remaining() <= FINALIZE_TAIL must NOT promote. Its
    fake_launch writes NO attempt file, so there is nothing for _defer_attempt() to
    preserve -- reason stays "job-completed" (contrast the DEFER case in
    test_run_defers_completed_attempt_when_budget_exhausted, below, whose fake_launch DOES
    write an attempt). adopt_pending is monkeypatched to False so this test exercises the
    launch path deterministically regardless of adopt_pending's own real implementation."""
    job = _mkjob(tmp_path, deadline=5)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    monkeypatch.setattr(job, "adopt_pending", lambda: False)

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
    assert job.reason == "job-completed"          # nothing to defer -> unchanged reason
    assert not os.path.exists(job.pending)


# --------------------------------------------------------------------------- #
# in-process white-box: adopt_pending / _defer_attempt (#213)
# --------------------------------------------------------------------------- #
def _gate_none(args, timeout):
    """Stub `_gate` that always returns None, simulating _run's own no-budget/timeout/
    spawn-fail skip contract (a gate that could NOT run, as opposed to one that ran and
    rejected)."""
    return None


def test_run_defers_completed_attempt_when_budget_exhausted(tmp_path, monkeypatch):
    """RED-before-green proof for #213. Mirrors test_run_refuses_promote_when_budget_exhausted
    above, but fake_launch WRITES job.sandbox_attempt (a completed, token-matching candidate,
    landed where codex's own output ACTUALLY lands under #409) before the finalize budget is
    exhausted -- so instead of discarding it, run() must PUBLISH it out and DEFER it to
    job.pending for a future dispatch's adopt_pending() to validate + adopt. On the pre-#213
    driver this fails outright (no `job.pending` attribute) and, more importantly, would
    discard the completed work (finalize()'s _silent_remove(self.attempt))."""
    job = _mkjob(tmp_path, deadline=5)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    monkeypatch.setattr(job, "adopt_pending", lambda: False)

    def fake_launch():
        job.jobId = "J"
        Path(job.sandbox_attempt).write_text(json.dumps(
            {"dispatch_token": job.tok, "seg": job.seg, "structure_ok": True, "quality_ok": True}),
            encoding="utf-8")
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
    assert job.reason == "deferred-completed"
    assert not os.path.exists(job.attempt)        # NOT left at the random per-inv path
    assert os.path.exists(job.pending)             # instead DEFERRED to the deterministic slot


def test_adopt_pending_promotes_valid(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.adopt_pending() is True
    assert calls == ["draft_ready.py", "validate_draft.py"]  # order: ready THEN quality
    assert os.path.exists(job.canonical)
    assert not os.path.exists(job.pending)


def test_adopt_pending_rejects_and_discards(tmp_path, monkeypatch):
    """A gate that RAN and REJECTED the candidate (bad content / stale cross-run token) ->
    the pending is DISCARDED, not left to poison every future dispatch."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 1})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.adopt_pending() is False
    assert calls == ["draft_ready.py"]
    assert not os.path.exists(job.canonical)
    assert not os.path.exists(job.pending)


def test_adopt_pending_no_budget_preserves_pending(tmp_path, monkeypatch):
    """MAJOR-1 guard: a gate that could NOT run (proc is None, e.g. exhausted budget) must
    NOT be treated as a rejection -- the pending survives for a future dispatch to retry.
    Fails on a naive `_ok()`-only implementation that can't distinguish None from rc!=0."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(job, "_gate", _gate_none)
    assert job.adopt_pending() is False
    assert not os.path.exists(job.canonical)
    assert os.path.exists(job.pending)             # NOT deleted


def test_adopt_pending_absent_is_false(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.adopt_pending() is False
    assert calls == []


def test_adopt_pending_symlink_cleared(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    target = Path(job.pending + ".target")
    target.write_text("{}", encoding="utf-8")
    os.symlink(target, job.pending)
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.adopt_pending() is False
    assert calls == []
    assert not os.path.lexists(job.pending)        # the symlink itself is gone, not followed


def test_adopt_pending_forged_directory_cleared(tmp_path, monkeypatch):
    """MAJOR-2 guard: a forged DIRECTORY squatting on the deterministic pending slot is
    removed (recursively) rather than left to permanently block every future adopt."""
    job = _mkjob(tmp_path, kind="translate")
    os.mkdir(job.pending)
    (Path(job.pending) / "child").write_text("junk", encoding="utf-8")
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.adopt_pending() is False
    assert calls == []
    assert not os.path.exists(job.pending)


def test_defer_over_forged_directory_preserves_attempt(tmp_path):
    """MAJOR-2 guard, defer side: a forged directory at the deterministic pending slot must
    not brick deferral -- _defer_attempt() clears it first, so the completed attempt is
    still preserved (no work loss) against an adversarial pre-existing dir. #409: the
    completed candidate is seeded in the SANDBOX (where codex actually writes it now);
    _defer_attempt() must PUBLISH it out before the existing pending-slot logic runs."""
    job = _mkjob(tmp_path, kind="translate")
    os.mkdir(job.pending)
    (Path(job.pending) / "child").write_text("junk", encoding="utf-8")
    _seed_sandbox(tmp_path, job, content="{}")
    assert job._defer_attempt() is True
    assert not os.path.isdir(job.pending)
    assert os.path.isfile(job.pending)
    assert not os.path.exists(job.attempt)


def test_defer_supersedes_existing_pending_with_newer(tmp_path):
    """#213 review: the single per-seg/kind slot deliberately retains the MOST RECENT
    completed attempt (last-writer-wins) -- it never sticks on a stale/invalid pending.
    Regression pin, not a RED proof (this is the driver's own restored behavior). #409:
    the "NEW" candidate is seeded in the sandbox, matching where codex actually writes it."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text(json.dumps({"marker": "OLD"}), encoding="utf-8")
    _seed_sandbox(tmp_path, job, content=json.dumps({"marker": "NEW"}))
    assert job._defer_attempt() is True
    assert json.loads(Path(job.pending).read_text())["marker"] == "NEW"   # superseded
    assert not os.path.exists(job.attempt)          # moved into the slot


def test_run_adopts_pending_before_launch(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    Path(job.pending).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)
    launch_called = {"v": False}

    def spy_launch():
        launch_called["v"] = True
        return True
    monkeypatch.setattr(job, "launch", spy_launch)
    rc = job.run()
    assert rc == 0
    assert job.adopted is True
    assert job.reason == "adopted-pending"
    assert launch_called["v"] is False              # NEVER launched -- adopted the pending instead
    assert os.path.exists(job.canonical)
    assert not os.path.exists(job.pending)


def test_run_no_budget_adopt_falls_through_to_launch(tmp_path, monkeypatch):
    """MINOR-1 guard (round 2): adopt_pending() returning False (e.g. no-budget, pending
    preserved) must NOT starve launch() -- run() always falls through to attempt a fresh
    launch, so a zero-budget dispatch cannot wedge into a never-launch/never-adopt loop."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    Path(job.pending).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(job, "adopt_pending", lambda: False)
    launch_called = {"v": False}

    def spy_launch():
        launch_called["v"] = True
        return False   # a doomed launch under zero budget fails harmlessly -- no worse than today
    monkeypatch.setattr(job, "launch", spy_launch)
    rc = job.run()
    assert rc == 1
    assert launch_called["v"] is True               # launch WAS attempted -- no starvation
    assert os.path.exists(job.pending)               # adopt_pending's False path never deleted it


def test_run_safe_adopt_cleans_stale_pending(tmp_path, monkeypatch):
    """A stale deferred pending is moot once safe_adopt() finds an already-valid canonical --
    it is removed so it never lingers to be (mis)adopted by a later run (leak-free)."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: True)
    Path(job.pending).write_text("{}", encoding="utf-8")
    rc = job.run()
    assert rc == 0
    assert job.adopted is True
    assert job.reason == "adopted"
    assert not os.path.exists(job.pending)


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
    _seed_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is True
    assert calls == ["draft_ready.py", "validate_draft.py"]  # order: ready THEN quality
    assert os.path.exists(job.attempt)          # PUBLISHED into staging before gating


def test_validate_attempt_translate_wrong_token_short_circuits(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    _seed_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({"draft_ready.py": 1})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is False
    assert calls == ["draft_ready.py"]  # validate_draft not reached


def test_validate_attempt_translate_quality_defect(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    _seed_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 1})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is False
    assert calls == ["draft_ready.py", "validate_draft.py"]


# --------------------------------------------------------------------------- #
# #399: a REJECTING gate's own output must be captured into error_detail
# (reusing the #400 plumbing) rather than discarded, both from
# validate_attempt()'s own gate calls and from adopt_pending()'s.
# --------------------------------------------------------------------------- #
def _gate_recorder_with_output(results):
    """Like _gate_recorder, but `results` maps gate name -> (returncode,
    stdout, stderr) so a test can inspect the CONTENT a rejecting gate
    printed, not just whether it rejected."""
    calls = []

    def _gate(args, timeout):
        calls.append(args[0])
        rc, out, err = results.get(args[0], (0, "", ""))
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)
    return _gate, calls


def test_validate_attempt_captures_rejecting_gate_output(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    _seed_sandbox(tmp_path, job)
    assert job.error_detail is None   # precondition
    gate, calls = _gate_recorder_with_output({
        "draft_ready.py": (0, "", ""),
        "validate_draft.py": (1, "[c001] FAIL: [FN:1] empty translation", ""),
    })
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is False
    assert job.error_detail == "validate_draft.py: [c001] FAIL: [FN:1] empty translation", (
        f"expected the rejecting gate's own name + output captured, got "
        f"{job.error_detail!r}"
    )


def test_validate_attempt_pass_leaves_error_detail_none(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    _seed_sandbox(tmp_path, job)
    gate, calls = _gate_recorder_with_output({
        "draft_ready.py": (0, "", ""),
        "validate_draft.py": (0, "[c001] OK", ""),
    })
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is True
    assert job.error_detail is None, "a PASSING gate's output must never be captured"


def test_capture_gate_rejection_combines_stdout_and_stderr(tmp_path):
    job = _mkjob(tmp_path)
    proc = SimpleNamespace(returncode=1, stdout="stdout line", stderr="stderr line")
    job._capture_gate_rejection("validate_draft.py", proc)
    assert job.error_detail == "validate_draft.py: stdout line\nstderr line"


def test_capture_gate_rejection_empty_output_leaves_error_detail_none(tmp_path):
    job = _mkjob(tmp_path)
    proc = SimpleNamespace(returncode=1, stdout="", stderr="")
    job._capture_gate_rejection("validate_draft.py", proc)
    assert job.error_detail is None, "nothing to capture -- must not fabricate a value"


def test_capture_gate_rejection_truncates_with_explicit_bound_marker(tmp_path):
    job = _mkjob(tmp_path)
    long_output = "X" * (job._GATE_OUTPUT_CAP + 500)
    proc = SimpleNamespace(returncode=1, stdout=long_output, stderr="")
    job._capture_gate_rejection("validate_draft.py", proc)
    assert job.error_detail is not None
    # The captured value stays a bounded artifact (this lands in the durable
    # joblog): the raw payload before the "gate_name: " prefix + truncation
    # marker must not exceed _GATE_OUTPUT_CAP chars.
    prefix = "validate_draft.py: "
    assert job.error_detail.startswith(prefix)
    payload = job.error_detail[len(prefix):]
    assert len(payload) <= job._GATE_OUTPUT_CAP + len("... [truncated at %d chars]" % job._GATE_OUTPUT_CAP)
    assert ("... [truncated at %d chars]" % job._GATE_OUTPUT_CAP) in job.error_detail, (
        "truncation must carry an EXPLICIT marker naming the exact bound, "
        "never a silent cut"
    )
    assert long_output not in job.error_detail, "the full untruncated text must not survive"


def test_adopt_pending_captures_rejecting_gate_output(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text("{}", encoding="utf-8")
    assert job.error_detail is None   # precondition
    gate, calls = _gate_recorder_with_output({
        "draft_ready.py": (0, "", ""),
        "validate_draft.py": (1, "[c001] FAIL: dangling FNREF_2", ""),
    })
    monkeypatch.setattr(job, "_gate", gate)
    assert job.adopt_pending() is False
    assert job.error_detail == "validate_draft.py: [c001] FAIL: dangling FNREF_2"
    assert not os.path.exists(job.pending)   # still discarded, per #213's existing contract


def test_validate_attempt_review_uses_review_ready(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="review")
    _seed_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({"review_ready.py": 0})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is True
    assert calls == ["review_ready.py"]


def test_validate_attempt_no_sandbox_output_refused(tmp_path, monkeypatch):
    """#409: nothing was ever written into the sandbox (e.g. codex crashed before writing
    its attempt) -- _publish_from_sandbox must refuse cleanly, never crash, and no gate runs."""
    job = _mkjob(tmp_path)
    _seed_sandbox(tmp_path, job, mode="absent")
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is False
    assert calls == []
    assert not os.path.exists(job.attempt)


def test_validate_attempt_symlink_refused(tmp_path, monkeypatch):
    """A symlink AT the sandbox attempt path -- even pointing at a sibling file still
    INSIDE the sandbox -- is refused by _publish_from_sandbox's O_NOFOLLOW open. Write-
    confinement stops WHERE codex can write, not what a symlink's target string names, so
    this refusal is load-bearing (see the escape tests below for a target OUTSIDE the
    sandbox, which is the sharper version of this same primitive)."""
    job = _mkjob(tmp_path)
    _seed_sandbox(tmp_path, job, mode="symlink")
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.validate_attempt() is False   # O_NOFOLLOW open fails
    assert calls == []                       # no gate ever runs on a symlink
    assert not os.path.exists(job.attempt)   # nothing published into staging


def test_validate_attempt_non_regular_refused(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    _seed_sandbox(tmp_path, job, mode="fifo")
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


# --------------------------------------------------------------------------- #
# #400: poll() must capture the companion's own job.errorMessage rather than
# discarding it -- verified against the REAL installed codex-companion.mjs's
# lib/tracked-jobs.mjs: errorMessage is persisted on the job record ONLY when
# its tracked-job runner catches a thrown exception (auth/quota/etc, not a
# content defect), which is exactly the "N unrelated per-segment content
# failures instead of one cause" signal #400 reports as missing.
# --------------------------------------------------------------------------- #
def _status_runner_with_error(statuses, error_message, record):
    """Like _status_runner, but the LAST status in the sequence carries an
    errorMessage field -- mirroring the real companion, which only ever sets
    it on the terminal record."""
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
            job = {"status": last["v"]}
            if last["v"] == "failed" and error_message is not None:
                job["errorMessage"] = error_message
            return SimpleNamespace(returncode=0, stdout=json.dumps({"job": job}))
        return SimpleNamespace(returncode=0, stdout="{}")
    return _run


def test_poll_captures_error_message_on_failed_job(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, deadline=100, poll=0)
    job.jobId = "J"
    assert job.error_detail is None   # precondition: nothing captured yet
    rec = []
    monkeypatch.setattr(job, "_run", _status_runner_with_error(
        ["queued", "running", "failed"], "quota exceeded: retry after 3600s", rec))
    job.poll()
    assert job.job_status == "failed"
    assert job.error_detail == "quota exceeded: retry after 3600s"


def test_poll_leaves_error_detail_none_when_companion_omits_it(tmp_path, monkeypatch):
    """Fail-safe/no-false-positive companion: a job.errorMessage the companion
    never set must not be invented -- error_detail stays None, never "None"
    the string or any other placeholder."""
    job = _mkjob(tmp_path, deadline=100, poll=0)
    job.jobId = "J"
    rec = []
    monkeypatch.setattr(job, "_run", _status_runner_with_error(
        ["completed"], None, rec))
    job.poll()
    assert job.job_status == "completed"
    assert job.error_detail is None


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
# in-process white-box: hygiene guard (case v) -- #409: keyed by the prior joblog's OWN
# recorded jobCwd (its per-invocation sandbox), never by self.root/durable_root.
# --------------------------------------------------------------------------- #
def _hygiene_job(tmp_path, prior_jobid="jobP", prior_status="launched", prior_cwd="SET"):
    job = _mkjob(tmp_path)
    rec = {"jobId": prior_jobid, "status": prior_status}
    if prior_cwd == "SET":
        prior_cwd = str(tmp_path / "prior_sandbox")
    if prior_cwd is not None:
        rec["jobCwd"] = prior_cwd
    Path(job.joblog).write_text(json.dumps(rec), encoding="utf-8")
    return job, rec.get("jobCwd")


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
    job, prior_cwd = _hygiene_job(tmp_path)
    cancels = []
    monkeypatch.setattr(job, "_run", _hygiene_runner(prior_cwd, "running", cancels))
    job.hygiene()
    assert cancels == ["jobP"]


def test_hygiene_skips_mismatched_ws(tmp_path, monkeypatch):
    job, prior_cwd = _hygiene_job(tmp_path)
    cancels = []
    monkeypatch.setattr(job, "_run", _hygiene_runner("/some/other/root", "running", cancels))
    job.hygiene()
    assert cancels == []   # forged/cross-store jobId is never cancelled


def test_hygiene_skips_inactive_job(tmp_path, monkeypatch):
    job, prior_cwd = _hygiene_job(tmp_path)
    cancels = []
    monkeypatch.setattr(job, "_run", _hygiene_runner(prior_cwd, "completed", cancels))
    job.hygiene()
    assert cancels == []


def test_hygiene_skips_terminal_joblog(tmp_path, monkeypatch):
    job, prior_cwd = _hygiene_job(tmp_path, prior_status="terminal")
    cancels = []
    monkeypatch.setattr(job, "_run", _hygiene_runner(prior_cwd, "running", cancels))
    job.hygiene()
    assert cancels == []


def test_hygiene_skips_missing_job_cwd(tmp_path, monkeypatch):
    """#409: an old-format joblog (written before #409, or a prior run that never reached
    launch()'s jobCwd write) has no recorded sandbox to query -- hygiene() cannot locate the
    job at all, so it must NOT guess self.root or any other fallback; it just skips."""
    job, _ = _hygiene_job(tmp_path, prior_cwd=None)
    cancels = []
    ran = {"v": False}

    def _run(argv, timeout):
        ran["v"] = True
        return SimpleNamespace(returncode=0, stdout="{}")
    monkeypatch.setattr(job, "_run", _run)
    job.hygiene()
    assert cancels == []
    assert ran["v"] is False   # never even attempted a status lookup with no cwd to query


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
    job.sandbox_dir = str(tmp_path / "sandbox")
    monkeypatch.setattr(job, "_run",
                        lambda argv, timeout: SimpleNamespace(returncode=0, stdout="{}"))
    assert job.launch() is False


# --------------------------------------------------------------------------- #
# #400: launch() must capture the companion's own stderr on a launch failure
# (non-zero exit, or a proc that exists but yields no usable jobId) rather
# than silently discarding it -- the other half of #400 alongside poll()'s
# errorMessage capture above.
# --------------------------------------------------------------------------- #
def test_launch_captures_stderr_on_companion_nonzero_exit(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    job.sandbox_dir = str(tmp_path / "sandbox")
    assert job.error_detail is None   # precondition
    monkeypatch.setattr(job, "_run", lambda argv, timeout: SimpleNamespace(
        returncode=1, stdout="", stderr="Error: rate limit exceeded, retry in 60s"))
    assert job.launch() is False
    assert job.error_detail == "Error: rate limit exceeded, retry in 60s"


def test_launch_captures_stderr_on_run_returning_none(tmp_path, monkeypatch):
    """_run() itself returns None on a timeout or spawn failure (see its own
    docstring) -- launch() must not crash reading .stderr off that None, and
    must leave error_detail None (nothing to capture)."""
    job = _mkjob(tmp_path)
    job.sandbox_dir = str(tmp_path / "sandbox")
    monkeypatch.setattr(job, "_run", lambda argv, timeout: None)
    assert job.launch() is False
    assert job.error_detail is None


def test_launch_captures_stderr_when_jobid_missing(tmp_path, monkeypatch):
    """The companion exits 0 but the JSON carries no jobId -- a distinct
    failure mode from a non-zero exit, still worth whatever stderr came with
    it (usually empty on a clean exit, but never silently dropped if not)."""
    job = _mkjob(tmp_path)
    job.sandbox_dir = str(tmp_path / "sandbox")
    monkeypatch.setattr(job, "_run", lambda argv, timeout: SimpleNamespace(
        returncode=0, stdout="{}", stderr="warning: unexpected response shape"))
    assert job.launch() is False
    assert job.error_detail == "warning: unexpected response shape"


def test_launch_parses_jobid_and_writes_launched_joblog(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    job.sandbox_dir = str(tmp_path / "sandbox")
    monkeypatch.setattr(job, "_run", lambda argv, timeout: SimpleNamespace(
        returncode=0, stdout=json.dumps({"jobId": "job-77", "status": "queued"})))
    assert job.launch() is True
    assert job.jobId == "job-77"
    rec = json.loads(Path(job.joblog).read_text())
    assert rec["jobId"] == "job-77" and rec["status"] == "launched"
    assert rec["jobCwd"] == job.sandbox_dir   # #409: recorded so hygiene() can find it later


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
    job.sandbox_dir = str(tmp_path / "sandbox")
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
    # #409: --cwd is the SANDBOX, never job.root/durable_root.
    assert argv[argv.index("--cwd") + 1] == job.sandbox_dir
    assert job.sandbox_dir != job.root


def test_launch_argv_includes_model_when_set(tmp_path, monkeypatch):
    """#197 -- a pinned CodexJob.model threads to the internal codex `task`
    launch argv as a real --model flag."""
    job = _mkjob(tmp_path)
    job.model = "gpt-5.3-codex"
    job.final_prompt = str(tmp_path / "fp.txt")
    job.sandbox_dir = str(tmp_path / "sandbox")
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
    job.sandbox_dir = str(tmp_path / "sandbox")
    captured = {}

    def fake_run(argv, timeout):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=json.dumps({"jobId": "j1"}))
    monkeypatch.setattr(job, "_run", fake_run)
    assert job.launch() is True
    assert "--model" not in captured["argv"]


def test_launch_argv_passes_through_a_non_default_effort(tmp_path, monkeypatch):
    """Coverage-gap fix: every OTHER --effort assertion in this file (see
    test_default_launch_argv_is_write_and_high_effort_with_8_flags_only,
    _mkjob's own effort="high" default, and every other _mkjob() caller)
    uses "high" -- CodexJob's own CLI default (_build_parser()'s --effort
    default="high") -- so none of them can tell self.effort being genuinely
    FORWARDED apart from --effort "high" being hardcoded regardless of
    self.effort's actual value. This is the identical "fixture value
    byte-identical to the parameter's own default" shape already found
    elsewhere in this branch (node_bin, current_draft_sha1's scripts_dir).
    Confirmed by mutation: hardcoding launch()'s `argv += ["--effort",
    self.effort]` to `argv += ["--effort", "high"]` survives every existing
    effort assertion in this file; only a value that is neither "high" nor
    None (unlike --model, which already has this exact pair) can catch it."""
    job = _mkjob(tmp_path)
    job.effort = "medium"
    job.final_prompt = str(tmp_path / "fp.txt")
    job.sandbox_dir = str(tmp_path / "sandbox")
    captured = {}

    def fake_run(argv, timeout):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=json.dumps({"jobId": "j1"}))
    monkeypatch.setattr(job, "_run", fake_run)
    assert job.launch() is True
    argv = captured["argv"]
    assert argv[argv.index("--effort") + 1] == "medium", (
        f"self.effort must be forwarded VERBATIM, not hardcoded to the "
        f"CLI's own default -- argv: {argv}"
    )


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
    # #398: "reason" used to be computed by run() and thrown away at
    # finalize()'s terminal joblog write -- job_status "failed" (with no
    # timeout, no completion) falls to run()'s final else branch,
    # self.reason = "job-%s" % self.job_status.
    assert jl["reason"] == "job-failed"
    assert line["reason"] == "job-failed"   # stdout already carried this; unaffected by the fix


# --------------------------------------------------------------------------- #
# #398: end-to-end -- a gate-REJECTED translate attempt (validate_draft.py
# FAILs on the promoted-but-rejected candidate) must record ITS OWN precise
# reason ("validate-failed"), not get relabeled generically later. This is
# the exact "label half of #398" scenario from the issue: before this fix,
# run() computed "validate-failed" correctly but finalize()'s joblog write
# dropped it -- the ONLY durable record once the driver is launched detached
# with stdout redirected to /dev/null (see mass-translate-wf.template.js's
# nohup dispatch), which run() itself never simulates, so this test asserts
# directly against the joblog file, the real durable surface.
# --------------------------------------------------------------------------- #
def test_terminal_joblog_carries_precise_reason_on_gate_rejection(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="invalid_quality",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False
    jl = json.loads((root / "segments" / ".codex_job.c001.json").read_text())
    assert jl["reason"] == "validate-failed", (
        "a gate-REJECTED attempt must record its own precise reason in the "
        "joblog, not fall through to a generic timeout-shaped label"
    )
    assert line["reason"] == "validate-failed"


# --------------------------------------------------------------------------- #
# #400: end-to-end -- a job that fails with the companion's own errorMessage
# (e.g. a quota/auth error) must carry that text into BOTH the stdout line
# and the terminal joblog, never just be reported as a bare "failed" status
# indistinguishable from any other cause.
# --------------------------------------------------------------------------- #
def test_terminal_joblog_and_line_carry_error_detail_from_companion(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="none",
                                   status_seq=["failed"],
                                   status_error_message="quota exceeded: retry after 3600s"))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False
    assert line["error_detail"] == "quota exceeded: retry after 3600s"
    jl = json.loads((root / "segments" / ".codex_job.c001.json").read_text())
    assert jl["error_detail"] == "quota exceeded: retry after 3600s", (
        "the joblog is the ONLY durable record once the driver runs detached "
        "with stdout redirected to /dev/null -- the stdout line alone is not enough"
    )


def test_terminal_joblog_and_line_error_detail_absent_when_companion_silent(tmp_path):
    """Companion control: a plain failure with no errorMessage at all must
    leave error_detail null/absent everywhere, never a fabricated value."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="none",
                                   status_seq=["failed"]))
    line = parse_line(proc)
    assert line["error_detail"] is None
    jl = json.loads((root / "segments" / ".codex_job.c001.json").read_text())
    assert jl["error_detail"] is None


# --------------------------------------------------------------------------- #
# #399: end-to-end -- a REJECTING gate's own output (via the real STUB
# validate_draft.py, not a hand-typed stand-in) must reach both the stdout
# line and the terminal joblog, so a rejection can be diagnosed without
# re-running the whole translation.
# --------------------------------------------------------------------------- #
def test_terminal_joblog_and_line_carry_rejecting_gate_output(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="invalid_quality",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False
    # STUB_VALIDATE_DRAFT (this file's own gate stub, honoring the real
    # validate_draft.py candidate-file CLI) prints exactly this on rejection.
    assert line["error_detail"] == "validate_draft.py: [c001] FAIL (quality)", (
        f"expected the rejecting gate's own name + printed output; got "
        f"{line['error_detail']!r}"
    )
    jl = json.loads((root / "segments" / ".codex_job.c001.json").read_text())
    assert jl["error_detail"] == "validate_draft.py: [c001] FAIL (quality)", (
        "the joblog is the durable sink once the driver runs detached with "
        "stdout redirected to /dev/null -- must carry this too"
    )


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
    """Run from an unrelated cwd; every fake-node (task/status/cancel) call for the SAME
    job must be bound to the SAME --cwd (the job's own sandbox, #409) regardless of the
    shell's actual cwd -- codex-companion's job store is keyed by exactly this value, so
    any call using a DIFFERENT cwd would simply never find the job (see FAKE_NODE's own
    jobCwd-marker enforcement). That sandbox is neither the shell's cwd NOR the durable
    root -- the whole point of #409 is that it is NEITHER of those."""
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
    cwds = {c["cwd"] for c in calls}
    assert len(cwds) == 1, "task/status/cancel for one job must share exactly one --cwd"
    (job_cwd,) = cwds
    assert job_cwd != str(other)
    assert job_cwd != str(root)


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


# --------------------------------------------------------------------------- #
# in-process: MINOR-3 -- adopt_pending() against the REAL review_ready.py gate
# (not this file's own honour-the-CLI stub), proving the cross-run dispatch_token
# reject holds through the ACTUAL shipped validation logic.
# --------------------------------------------------------------------------- #
REAL_GATE_SCRIPTS = ("codex_job.py", "draft_ready.py", "validate_draft.py",
                     "review_ready.py", "draft_sha1.py")


def make_real_gate_root(tmp_path):
    """A durable_root carrying REAL (not stub) copies of codex_job.py and every gate
    script it dispatches to, plus review.schema.json -- so a CodexJob loaded FROM this
    root's own scripts/codex_job.py has its self-anchored SCRIPTS_DIR resolve here too,
    letting adopt_pending()'s candidate-file gate run the actual shipped review_ready.py
    (same self-anchoring trick build_root() uses for the SUBPROCESS harness above, just
    with real gates instead of stubs)."""
    root = tmp_path / "durable_realgate"
    scripts = root / "scripts"
    schemas = root / "schemas"
    (root / "segments").mkdir(parents=True)
    scripts.mkdir()
    schemas.mkdir()
    for name in REAL_GATE_SCRIPTS:
        shutil.copy2(SCRIPTS_DIR / name, scripts / name)
    shutil.copy2(SCHEMAS_SRC_DIR / "review.schema.json", schemas / "review.schema.json")
    return root


def _load_codex_job_copy(scripts_dir):
    """Import the copy of codex_job.py at `scripts_dir` (NOT this test file's own top-level
    `codex_job` import of the shipped assets/scripts/ original) under a distinct module
    name, so its module-level SCRIPTS_DIR constant self-anchors to `scripts_dir` -- exactly
    as production's Step-0a-copied driver self-anchors to <durable_root>/scripts/."""
    src = scripts_dir / "codex_job.py"
    spec = importlib.util.spec_from_file_location("codex_job_realgate_mod", str(src))
    assert spec is not None and spec.loader is not None, f"could not load spec for {src}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_draft_for_real_gate(segments_dir, seg, dispatch_token):
    draft = {"seg": seg, "blocks": {"p1": "hello"}, "footnotes": {}, "verses": {},
             "names": [], "notes": [], "dispatch_token": dispatch_token}
    (segments_dir / f"{seg}.draft.json").write_text(json.dumps(draft), encoding="utf-8")


def real_draft_sha1_for_root(root, seg):
    """The REAL draft_sha1.py's own reported digest for the on-disk draft -- never
    reimplemented here (same technique as tests/review_ready.test.py's own helper of the
    same purpose)."""
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "draft_sha1.py"), seg],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"draft_sha1.py failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout.strip()


def test_adopt_pending_review_real_gate_rejects_token_mismatch(tmp_path):
    """MINOR-3: drive adopt_pending()'s REVIEW-kind candidate gate through the REAL shipped
    review_ready.py (not this file's own STUB_REVIEW_READY) with a pending whose own
    dispatch_token mismatches the run's --expect-token -- proving design §2's "cross-run
    token safety is automatic" claim against the ACTUAL gate script, not just the stub honour
    -ing the frozen CLI. A gate that RAN and REJECTED must discard the pending and promote
    nothing (same MAJOR-1-adjacent contract as test_adopt_pending_rejects_and_discards,
    exercised here through the real dependency instead of a recorder stub)."""
    root = make_real_gate_root(tmp_path)
    segments_dir = root / "segments"
    seg, run_tok = "seg01", "RUN1:seg01:r1"
    write_draft_for_real_gate(segments_dir, seg, dispatch_token="RUN1:seg01")
    real_sha1 = real_draft_sha1_for_root(root, seg)

    mod = _load_codex_job_copy(root / "scripts")
    job = mod.CodexJob(
        kind="review", seg=seg, tok=run_tok, disp="D1", root=str(root),
        companion="unused", prompt_text=PROMPT_ONE, prompt_file="unused",
        deadline_sec=60, poll_sec=1, effort="high", node="node",
    )
    review = {
        "clean": True, "coverage_ok": True, "findings": [],
        "draft_sha1": real_sha1, "dispatch_token": "RUN2:seg01:r1",   # MISMATCHED vs run_tok
    }
    Path(job.pending).write_text(json.dumps(review), encoding="utf-8")

    assert job.adopt_pending() is False
    assert not os.path.exists(job.pending)         # gate RAN and REJECTED -> discarded
    assert not os.path.exists(job.canonical)


# --------------------------------------------------------------------------- #
# #409 ESCAPE TESTS -- each demonstrates a concrete escape, watches it succeed
# UNCAUGHT against a naive/pre-#409 shape, then proves the real primitive closes it.
# --------------------------------------------------------------------------- #
def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def test_escape_A_naive_copy_follows_symlink_out_of_sandbox(tmp_path):
    """ESCAPE A (property 1: fd-pinned, digest-verified copy, never a path re-checked-
    then-reused). A malicious/buggy codex can WRITE a symlink at its own attempt path
    (write-confinement restricts WHERE writes land, not what a symlink's target string
    names) pointing at ANY file it can read elsewhere on the machine -- including, in this
    repo, a driver-internal file like the joblog that must never be treated as "codex's own
    output".

    RED: a naive path-based copy (`shutil.copy`, gated only by `os.path.isfile` -- the
    OLD shape, before the sandbox->staging copy existed at all) FOLLOWS the symlink and
    happily lands the OUTSIDE file's content in staging, uncaught -- watch this actually
    happen below.

    GREEN: the real `_publish_from_sandbox` refuses outright (O_NOFOLLOW on the open),
    and no destination file is ever created."""
    job = _mkjob(tmp_path)
    sbx = tmp_path / "sandbox"
    sbx.mkdir()
    job.sandbox_dir = str(sbx)
    job.sandbox_attempt = str(sbx / "attempt.draft.json")

    # A file OUTSIDE the sandbox that must NEVER be treated as codex's own output --
    # standing in for something sensitive like this driver's own joblog.
    outside_secret = tmp_path / "outside_secret.json"
    outside_secret.write_text(json.dumps({"dispatch_token": job.tok, "seg": job.seg,
                                          "structure_ok": True, "quality_ok": True,
                                          "planted": "NOT codex's real output"}),
                              encoding="utf-8")
    os.symlink(outside_secret, job.sandbox_attempt)

    # RED: prove the escape is REAL against a naive path-based copy -- this is exactly
    # the shape codex_job.py would have used if the sandbox->staging step were built as
    # a plain shutil.copy instead of _publish_from_sandbox.
    naive_dst = tmp_path / "naive_staging.json"
    assert os.path.isfile(job.sandbox_attempt)   # a naive isfile() check ALSO follows symlinks
    shutil.copy(job.sandbox_attempt, naive_dst)  # <-- the escape: FOLLOWS the symlink
    assert naive_dst.read_text() == outside_secret.read_text()
    assert "NOT codex's real output" in naive_dst.read_text()   # uncaught exfiltration

    # GREEN: the real primitive refuses.
    assert job._publish_from_sandbox(job.sandbox_attempt, job.attempt) is False
    assert not os.path.exists(job.attempt)
    assert outside_secret.read_text()   # the outside file itself is untouched either way


def test_escape_A_publish_refuses_mutation_between_fstats(tmp_path, monkeypatch):
    """ESCAPE A, second half (property 1: identity+digest BEFORE as well as after). A
    writer still mutating the sandbox attempt file underneath the read must not slip a
    different digest through -- simulate it by having the SECOND fstat() inside
    _publish_from_sandbox report a different size than the first (a writer appended/
    truncated between the two checks), and require the publish refuse."""
    job = _mkjob(tmp_path)
    sbx = tmp_path / "sandbox"
    sbx.mkdir()
    job.sandbox_dir = str(sbx)
    job.sandbox_attempt = str(sbx / "attempt.draft.json")
    Path(job.sandbox_attempt).write_text("{}", encoding="utf-8")

    real_fstat = os.fstat
    calls = {"n": 0}

    def flaky_fstat(fd):
        calls["n"] += 1
        st = real_fstat(fd)
        if calls["n"] == 2:  # the SECOND fstat in the source fstat-sandwich
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                                   st.st_uid, st.st_gid, st.st_size + 1, st.st_atime,
                                   st.st_mtime, st.st_ctime))
        return st
    monkeypatch.setattr(os, "fstat", flaky_fstat)
    assert job._publish_from_sandbox(job.sandbox_attempt, job.attempt) is False
    assert not os.path.exists(job.attempt)


@pytest.mark.skipif(not _HAS_GIT, reason="git unavailable")
def test_escape_B_sandbox_cwd_resolves_outside_git_root_resolves_inside(tmp_path):
    """ESCAPE B (property 2/4: write-confinement by directory placement, categorically
    different from a by-path gate). codex-companion resolves its OWN `workspace-write`
    sandbox root by walking UP from `--cwd` looking for a git repo
    (lib/workspace.mjs:resolveWorkspaceRoot / lib/git.mjs:ensureGitRepository -- read
    directly from the installed companion, not assumed): `git -C <cwd> rev-parse
    --show-toplevel`.

    RED: nest durable_root inside a real git repo (the SUPPORTED durable_root==project_root
    configuration, SKILL.md:192) and show that `--cwd durable_root` (the OLD design)
    resolves to the OUTER repo toplevel -- i.e. codex would be granted workspace-write over
    the WHOLE repo (scripts/, segments/, the lock, the joblog), not just durable_root.

    GREEN: `_setup_sandbox()`'s chosen directory resolves to ITSELF -- no enclosing repo
    at all -- so the real codex-companion sandbox this driver launches into is provably
    confined to that one throwaway directory."""
    repo = tmp_path / "repo"
    (repo / "durable" / "segments").mkdir(parents=True)
    (repo / "durable" / "scripts").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    # RED: the OLD design's --cwd (durable_root) resolves to the OUTER repo.
    old_style = subprocess.run(["git", "-C", str(repo / "durable"), "rev-parse",
                                "--show-toplevel"], capture_output=True, text=True, timeout=30)
    assert old_style.returncode == 0
    assert os.path.realpath(old_style.stdout.strip()) == os.path.realpath(str(repo))
    assert os.path.realpath(old_style.stdout.strip()) != os.path.realpath(str(repo / "durable"))
    # i.e. resolveWorkspaceRoot(durable_root) != durable_root -- the vulnerability itself.

    job = codex_job.CodexJob(
        kind="translate", seg="c001", tok="RUN:c001", disp="d1",
        root=str(repo / "durable"), companion=_companion_file(tmp_path),
        prompt_text=PROMPT_ONE, prompt_file=_prompt_file(tmp_path), deadline_sec=60,
        poll_sec=1, effort=None, node="node")
    assert job._setup_sandbox() is True

    # GREEN: the sandbox resolves to ITSELF.
    new_style = subprocess.run(["git", "-C", job.sandbox_dir, "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True, timeout=30)
    assert new_style.returncode != 0   # NOT inside any git repo at all
    assert job.sandbox_dir != job.root
    assert not job.sandbox_dir.startswith(job.root + os.sep)   # not even a subdirectory of it
    shutil.rmtree(job.sandbox_dir, ignore_errors=True)


def test_escape_B2_sandbox_inside_repo_would_be_refused(tmp_path):
    """The confinement check ITSELF, isolated: if a sandbox candidate genuinely sits inside
    a git working tree (the failure this whole mechanism exists to prevent -- e.g. a future
    edit that carelessly nests the sandbox under durable_root again), _sandbox_is_confined
    must say so, so _setup_sandbox refuses to dispatch rather than silently widening codex's
    write access back to the whole repo."""
    if not _HAS_GIT:
        pytest.skip("git unavailable")
    repo = tmp_path / "repo2"
    nested = repo / "nested" / "sandbox"
    nested.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    job = _mkjob(tmp_path)
    assert job._sandbox_is_confined(str(nested)) is False


def test_timeout_never_reads_stragglers_from_abandoned_sandbox(tmp_path, monkeypatch):
    """#409 property 2 (isolation, not proof of kill): simulate a codex turn that outlives
    our best-effort cancel and writes a LATE, fully-valid-looking attempt into the sandbox
    AFTER poll() has already given up. run() must never come back and read it -- the
    straggler is neutralised by finalize()'s unconditional sandbox rmtree, not by any
    process-kill this driver cannot actually perform (the detached codex worker runs in its
    own session; codex-companion's own cancel is best-effort, see the module docstring)."""
    job = _mkjob(tmp_path, kind="translate", deadline=5)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    monkeypatch.setattr(job, "adopt_pending", lambda: False)

    def fake_launch():
        job.jobId = "J"
        return True
    monkeypatch.setattr(job, "launch", fake_launch)

    def fake_poll():
        # Simulate the straggler: AFTER giving up, something writes a late, valid-looking
        # attempt into the sandbox -- exactly what a surviving zombie codex turn would do.
        job.timed_out = True
        job.job_status = None
        Path(job.sandbox_attempt).write_text(json.dumps(
            {"dispatch_token": job.tok, "seg": job.seg, "structure_ok": True,
             "quality_ok": True}), encoding="utf-8")
    monkeypatch.setattr(job, "poll", fake_poll)

    sandbox_dir_seen = {}

    real_finalize = job.finalize

    def spy_finalize():
        sandbox_dir_seen["dir"] = job.sandbox_dir
        real_finalize()
    monkeypatch.setattr(job, "finalize", spy_finalize)

    rc = job.run()
    assert rc == 1
    assert job.reason == "timed-out"
    assert job.promoted is False
    assert not os.path.exists(job.canonical)      # the late write never got promoted
    assert not os.path.exists(job.attempt)        # never even PUBLISHED into staging
    assert sandbox_dir_seen["dir"], "sandbox was never set up"
    assert not os.path.exists(sandbox_dir_seen["dir"])   # abandoned + removed, straggler moot


# ---------------------------------------------------------------------------
# #409: the confinement probe must FAIL CLOSED on a no-verdict result.
#
# The bug this pins: _sandbox_is_confined used to be `not _ok(self._run(...))`, and
# _run() returns None for a timeout and for a spawn failure as well as for "git ran
# and said no repository". Because absence-of-a-repository is the SUCCESS condition
# here, that None collapsed every no-verdict probe into "confined" and dispatched --
# handing codex the enclosing repository the check exists to deny, since the
# companion's own probe is unbounded and would still find it.
#
# Each branch is asserted separately so a future collapse of the four outcomes back
# into a boolean cannot pass: two of them mean "go", two mean "refuse", and a test
# that only checked the happy path would not notice the difference.
# ---------------------------------------------------------------------------


def _probe_job(tmp_path, monkeypatch, outcome):
    job = _mkjob(tmp_path)
    monkeypatch.setattr(codex_job.CodexJob, "_probe_enclosing_repo",
                        lambda self, path: outcome)
    return job


@pytest.mark.parametrize("outcome,expected_confined", [
    (codex_job.CodexJob._PROBE_STANDALONE, True),    # git ran, no repo -> safe
    (codex_job.CodexJob._PROBE_GIT_ABSENT, True),    # companion degrades identically
    (codex_job.CodexJob._PROBE_ENCLOSED, False),     # an enclosing repo exists
    (codex_job.CodexJob._PROBE_NO_VERDICT, False),   # THE REGRESSION: no verdict -> refuse
])
def test_confinement_scores_each_probe_outcome(tmp_path, monkeypatch, outcome, expected_confined):
    job = _probe_job(tmp_path, monkeypatch, outcome)
    assert job._sandbox_is_confined(str(tmp_path)) is expected_confined, (
        f"probe outcome {outcome!r} must score confined={expected_confined}"
    )


def test_probe_reports_no_verdict_on_timeout_and_on_skip(tmp_path, monkeypatch):
    """A timed-out probe must be distinguishable from 'git said no repository' --
    the whole point of not routing this through _run()."""
    job = _mkjob(tmp_path)

    def _boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="git", timeout=0.01)

    monkeypatch.setattr(codex_job.subprocess, "run", _boom)
    assert job._probe_enclosing_repo(str(tmp_path)) == codex_job.CodexJob._PROBE_NO_VERDICT
    assert job._sandbox_is_confined(str(tmp_path)) is False

    monkeypatch.setattr(codex_job.CodexJob, "poll_timeout", lambda self: 0)
    assert job._probe_enclosing_repo(str(tmp_path)) == codex_job.CodexJob._PROBE_NO_VERDICT


def test_probe_reports_git_absent_distinctly(tmp_path, monkeypatch):
    """git-not-installed is the ONE no-result case that still licenses a dispatch, and
    only because the companion's resolver degrades the same way."""
    job = _mkjob(tmp_path)

    def _enoent(*a, **kw):
        raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(codex_job.subprocess, "run", _enoent)
    assert job._probe_enclosing_repo(str(tmp_path)) == codex_job.CodexJob._PROBE_GIT_ABSENT
    assert job._sandbox_is_confined(str(tmp_path)) is True


# --------------------------------------------------------------------------- #
# #412: --plugin-root redirect -- the poisoned-sibling property. The module
# docstring's own seam comment (above _DURABLE_ROOT_CONTRACT_SCRIPTS) states
# the vulnerability: ${durable_root}/scripts/ (this fixture's own
# root/scripts/) is codex-writable (--write over the whole durable root), so
# a codex-tampered copy of a gate script there could validate its own bad
# output. This section proves BOTH halves, per the dispatch brief -- neither
# alone proves anything:
#   1. --plugin-root pointing at a SEPARATE, trusted location bypasses a
#      poisoned durable-root sibling entirely (without this half, the
#      redirect could just be silently ignored).
#   2. Omitting --plugin-root genuinely falls prey to the poisoned copy
#      (without this half, "the redirect works" could be true merely
#      because the poisoned stub itself is broken/inert some other way,
#      never actually exercised).
# One test per gate script _gate() invokes, each isolating ONLY that gate's
# own defect (the other gate in a two-gate check, e.g. draft_ready.py
# alongside validate_draft.py, is left un-poisoned and passing, so a false
# pass/fail can only be attributed to the ONE gate under test).
# --------------------------------------------------------------------------- #
POISONED_GATE_SRC = '''#!/usr/bin/env python3
import sys
print("[POISONED] always OK -- ignores its own --candidate-file content")
sys.exit(0)
'''


def _poison_durable_root_gate(root, script_name):
    """Overwrites root/scripts/<script_name> (this fixture's own durable-root
    COPY -- exactly where the module docstring's seam comment says codex's
    own --write access reaches) with a stub that ALWAYS exits 0 regardless
    of its --candidate-file's actual content, simulating a codex-tampered
    gate designed to validate anything, including its own bad output."""
    (root / "scripts" / script_name).write_text(POISONED_GATE_SRC, encoding="utf-8")


def _make_trusted_plugin_root(tmp_path, name="trusted_plugin_root"):
    """A SEPARATE physical location holding the REAL (test-stub) gate
    scripts at the {plugin_root}/assets/scripts/ layout SKILL.md documents
    for the plugin-anchored scripts -- mirrors
    tests/resume_integrity.test.py's own make_trusted_plugin_root() for the
    identical concept, one seam over (that file's redirect protects
    cache_key.py; this one protects the three candidate-file gates)."""
    plugin_root = tmp_path / name
    plugin_scripts_dir = plugin_root / "assets" / "scripts"
    plugin_scripts_dir.mkdir(parents=True)
    (plugin_scripts_dir / "draft_ready.py").write_text(STUB_DRAFT_READY, encoding="utf-8")
    (plugin_scripts_dir / "validate_draft.py").write_text(STUB_VALIDATE_DRAFT, encoding="utf-8")
    (plugin_scripts_dir / "review_ready.py").write_text(STUB_REVIEW_READY, encoding="utf-8")
    return plugin_root


def test_plugin_root_redirect_bypasses_poisoned_draft_ready(tmp_path):
    """draft_ready.py half: a WRONG-TOKEN attempt (structure_ok/quality_ok
    both True -- the ONLY defect is the dispatch_token, which ONLY
    draft_ready.py checks; validate_draft.py's own stub would pass this
    candidate regardless, isolating this test to draft_ready.py alone)."""
    root, companion, node = build_root(tmp_path)
    _poison_durable_root_gate(root, "draft_ready.py")
    plugin_root = _make_trusted_plugin_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    state = base_state(seg, tok, "translate", attempt_mode="invalid_token", status_seq=["completed"])

    proc_poisoned = spawn_driver(root, companion, node, seg, tok, "translate", "D1", state)
    line_poisoned = parse_line(proc_poisoned)
    proc_trusted = spawn_driver(root, companion, node, seg, tok, "translate", "D2", state,
                                extra_args=["--plugin-root", str(plugin_root)])
    line_trusted = parse_line(proc_trusted)

    assert line_poisoned["ok"] is True, (
        f"FAIL-SAFE CONTROL: without --plugin-root, the poisoned durable-root "
        f"draft_ready.py must genuinely be consulted and WRONGLY accept a "
        f"wrong-token attempt -- got {line_poisoned}"
    )
    assert line_trusted["ok"] is False, (
        f"--plugin-root must bypass the poisoned durable-root sibling "
        f"entirely and use the TRUSTED copy, which correctly REJECTS a "
        f"wrong-token attempt -- got {line_trusted}"
    )


def test_plugin_root_redirect_bypasses_poisoned_validate_draft(tmp_path):
    """validate_draft.py half: a quality-defective attempt (structure_ok/
    token both correct -- the ONLY defect is quality_ok, which ONLY
    validate_draft.py checks; draft_ready.py's own (un-poisoned, trusted-by-
    construction here) stub would pass this candidate regardless)."""
    root, companion, node = build_root(tmp_path)
    _poison_durable_root_gate(root, "validate_draft.py")
    plugin_root = _make_trusted_plugin_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    state = base_state(seg, tok, "translate", attempt_mode="invalid_quality", status_seq=["completed"])

    proc_poisoned = spawn_driver(root, companion, node, seg, tok, "translate", "D1", state)
    line_poisoned = parse_line(proc_poisoned)
    proc_trusted = spawn_driver(root, companion, node, seg, tok, "translate", "D2", state,
                                extra_args=["--plugin-root", str(plugin_root)])
    line_trusted = parse_line(proc_trusted)

    assert line_poisoned["ok"] is True, (
        f"FAIL-SAFE CONTROL: without --plugin-root, the poisoned durable-root "
        f"validate_draft.py must genuinely be consulted and WRONGLY accept a "
        f"quality-defective attempt -- got {line_poisoned}"
    )
    assert line_trusted["ok"] is False, (
        f"--plugin-root must bypass the poisoned durable-root sibling "
        f"entirely and use the TRUSTED copy, which correctly REJECTS a "
        f"quality-defective attempt -- got {line_trusted}"
    )


def test_plugin_root_redirect_bypasses_poisoned_review_ready(tmp_path):
    """review_ready.py half: kind="review"'s own SOLE gate (no second check
    to isolate against, unlike translate's draft_ready.py+validate_draft.py
    pair) -- a schema-defective attempt."""
    root, companion, node = build_root(tmp_path)
    _poison_durable_root_gate(root, "review_ready.py")
    plugin_root = _make_trusted_plugin_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    state = base_state(seg, tok, "review", attempt_mode="invalid_schema", status_seq=["completed"])

    proc_poisoned = spawn_driver(root, companion, node, seg, tok, "review", "D1", state)
    line_poisoned = parse_line(proc_poisoned)
    proc_trusted = spawn_driver(root, companion, node, seg, tok, "review", "D2", state,
                                extra_args=["--plugin-root", str(plugin_root)])
    line_trusted = parse_line(proc_trusted)

    assert line_poisoned["ok"] is True, (
        f"FAIL-SAFE CONTROL: without --plugin-root, the poisoned durable-root "
        f"review_ready.py must genuinely be consulted and WRONGLY accept a "
        f"schema-defective attempt -- got {line_poisoned}"
    )
    assert line_trusted["ok"] is False, (
        f"--plugin-root must bypass the poisoned durable-root sibling "
        f"entirely and use the TRUSTED copy, which correctly REJECTS a "
        f"schema-defective attempt -- got {line_trusted}"
    )


def test_plugin_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility for the redirect itself: a driver invoked with
    NO --plugin-root at all (not even an empty string -- the flag genuinely
    absent from argv) behaves exactly as before #412, using the CLEAN
    (un-poisoned) durable-root copy successfully."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 0 and line["ok"] is True


def test_plugin_root_misconfigured_fails_loudly_at_usage_time(tmp_path):
    """A --plugin-root that does not resolve to a directory containing
    assets/scripts/ must fail LOUDLY at usage time (exit 2, no stdout JSON
    line at all) rather than silently falling through _gate()'s own
    OSError->None handling later, which would be indistinguishable from an
    ordinary 'gate ran out of budget' case."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    bogus_plugin_root = str(tmp_path / "does_not_exist_at_all")
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["completed"]),
                        extra_args=["--plugin-root", bogus_plugin_root])
    assert proc.returncode == 2, (
        f"a misconfigured --plugin-root must fail at usage time (exit 2), "
        f"not silently degrade -- rc={proc.returncode}\nstdout={proc.stdout!r}\n"
        f"stderr:\n{proc.stderr}"
    )
    assert not proc.stdout.strip(), "a usage error must print NO stdout JSON line"
    # Specifically THIS validation, not merely argparse rejecting an
    # unrecognized --plugin-root flag on a pre-#412 driver (which would
    # ALSO exit 2 with no stdout, for a completely different reason --
    # vacuously satisfying the two asserts above without ever exercising
    # main()'s own resolved-directory check).
    assert "assets/scripts" in proc.stderr, (
        f"expected this driver's OWN --plugin-root resolution error "
        f"(naming assets/scripts/), not a generic argparse rejection; "
        f"stderr:\n{proc.stderr}"
    )


# --------------------------------------------------------------------------- #
# Trust-boundary fix: an EMPTY --plugin-root used to be silently treated as
# "not given" (CodexJob.__init__ tested it for TRUTHINESS, `if plugin_root`,
# so an is-not-None-but-falsy "" fell through to SCRIPTS_DIR -- the
# self-anchored, codex-WRITABLE durable-root copy), even though main()'s own
# pre-flight directory check operates on `os.path.realpath("")` (the CURRENT
# WORKING DIRECTORY, a value distinct from the empty string actually passed)
# and could pass. The two tests immediately above
# (test_plugin_root_flag_omitted_preserves_todays_behavior /
# test_plugin_root_misconfigured_fails_loudly_at_usage_time) do not cover
# this: the first explicitly excludes "" from its own scope (see its own
# docstring), and the second uses a NONEXISTENT path, which fails the
# directory check for an unrelated reason. This is the missing axis.
# --------------------------------------------------------------------------- #


def test_plugin_root_empty_string_fails_loudly_rather_than_silently_using_poisoned_copy(tmp_path):
    """PROOF, and the adversarial "attack it" case: reproduces the exact
    exploit -- root's own draft_ready.py POISONED (per
    test_plugin_root_redirect_bypasses_poisoned_draft_ready's own fixture),
    root/assets/scripts/ present (so main()'s pre-#412-fix directory check
    would have passed), --plugin-root passed as "". Confirmed pre-fix (this
    exact fixture, against the parent commit's copy of the driver): exit 0,
    ok=true -- the poisoned copy was silently consulted and wrongly accepted
    a wrong-token attempt, exactly what --plugin-root exists to prevent.
    Post-fix: exit 2, no stdout JSON, before any gate ever runs."""
    root, companion, node = build_root(tmp_path)
    _poison_durable_root_gate(root, "draft_ready.py")
    (root / "assets" / "scripts").mkdir(parents=True)
    seg, tok = "c001", "RUN:c001"
    state = base_state(seg, tok, "translate", attempt_mode="invalid_token", status_seq=["completed"])

    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1", state,
                        extra_args=["--plugin-root", ""])

    assert proc.returncode == 2, (
        f"an empty --plugin-root must fail at usage time (exit 2), not "
        f"silently fall back to the (here, POISONED) self-anchored copy -- "
        f"rc={proc.returncode}\nstdout={proc.stdout!r}\nstderr:\n{proc.stderr}"
    )
    assert not proc.stdout.strip(), (
        "a usage error must print NO stdout JSON line -- in particular "
        "never an ok:true line, which would mean the poisoned gate ran"
    )
    assert "empty" in proc.stderr or "whitespace" in proc.stderr, (
        f"expected the dedicated empty/whitespace error, not the generic "
        f"'does not resolve to a directory' one (that message would be "
        f"technically true here too, since main() resolves \"\" to cwd "
        f"which DOES have assets/scripts/ -- the dedicated message is what "
        f"proves this exact check fired); stderr:\n{proc.stderr}"
    )


def test_plugin_root_whitespace_only_also_fails_loudly(tmp_path):
    """The `.strip()` half of the same check: a few spaces are just as
    silently-falls-back-worthy as a bare empty string, and just as
    plausible a template-substitution artifact."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["completed"]),
                        extra_args=["--plugin-root", "   "])

    assert proc.returncode == 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert not proc.stdout.strip()
    assert "empty" in proc.stderr or "whitespace" in proc.stderr


# --------------------------------------------------------------------------- #
# Consistency fix: _write_joblog()'s os.write() return value used to be
# discarded, unlike _publish_from_sandbox()'s own identically-shaped
# temp-file write two functions away, which already checks it. POSIX
# write() may write fewer bytes than requested; without the check, a short
# write published a TRUNCATED, invalid-JSON joblog at the trusted final
# name -- jobId/jobCwd are what hygiene()'s cancel-a-stale-prior-job path
# (and a human debugging a crash) both read from there.
# --------------------------------------------------------------------------- #


def test_write_joblog_short_write_never_publishes_a_truncated_joblog(tmp_path, monkeypatch):
    """Simulates a short write (one byte less than requested, the way an
    interrupted/ENOSPC write would look) by monkeypatching codex_job.os.write.
    Confirmed pre-fix (this exact fixture, against the parent commit's copy):
    the joblog ends up on disk containing invalid, truncated JSON. Post-fix:
    nothing is published at the final name, and the temp scratch file is
    cleaned up."""
    job = _mkjob(tmp_path)
    real_write = codex_job.os.write

    def short_write(fd, data):
        return real_write(fd, data[:-1]) if len(data) > 1 else real_write(fd, data)

    monkeypatch.setattr(codex_job.os, "write", short_write)
    job._write_joblog({"jobId": "job-1", "kind": "translate", "seg": "c001", "status": "launched"})

    assert not os.path.exists(job.joblog), (
        "a short write must never leave anything at the final joblog name "
        "-- hygiene()'s cancel-a-stale-prior-job path and a human reading "
        "this file after a crash both trust it"
    )
    leftovers = list(Path(job.segdir).glob(".codex_job.*.tmp"))
    assert leftovers == [], f"temp scratch file(s) left behind: {leftovers}"


def test_write_joblog_succeeds_normally_when_the_write_is_not_short(tmp_path):
    """False-positive bound for the fix above: an ordinary, unpatched write
    still publishes correctly -- the new length check must not reject a
    genuinely complete write."""
    job = _mkjob(tmp_path)
    job._write_joblog({"jobId": "job-1", "kind": "translate", "seg": "c001", "status": "launched"})

    assert os.path.exists(job.joblog)
    assert json.loads(Path(job.joblog).read_text(encoding="utf-8"))["jobId"] == "job-1"


# --------------------------------------------------------------------------- #
# Coverage-gap close: main()'s `poll_sec = args.poll_sec if args.poll_sec > 0
# else 15` clamp was never exercised through the real CLI. Every white-box
# test using poll=0 (_mkjob(tmp_path, deadline=100, poll=0), several places
# above) constructs CodexJob directly, bypassing main()'s own argv-parsing
# clamp entirely -- so args.poll_sec <= 0 was never reached through that
# path. If this clamp broke, `--poll-sec 0` would make poll()'s
# `time.sleep(min(self.poll_sec, rem))` sleep zero seconds between
# iterations: a tight busy-loop hammering the companion's `status`
# subprocess until the deadline. Not a security bypass -- a self-inflicted
# resource/DoS-on-yourself concern -- but a real, previously-untested branch
# on the last unpinned CLI parameter on this file.
# --------------------------------------------------------------------------- #


def test_poll_sec_zero_is_clamped_rather_than_busy_looping(tmp_path):
    """PROOF, via an OBSERVABLE consequence rather than an assertion on a
    value (a value-identity assertion can't reach this: poll_sec is never
    echoed into argv or output, only used as a sleep duration). Drives the
    REAL driver via subprocess with --poll-sec 0 and a short deadline, the
    job kept permanently NON-terminal (status_seq=["queued"] -- FAKE_NODE's
    own counter clamps to the last element of a 1-item sequence, so every
    poll keeps seeing "queued"), and counts `status` calls in the existing
    call_log mechanism. Measured directly against both sides before writing
    this assertion: the clamped/shipped behavior produces exactly 1 status
    call in a 3-second deadline (poll_sec=15 > any remaining budget that
    short, so the one sleep runs out the clock); a `poll_sec = args.poll_sec`
    mutation (dropping the clamp) produces 143 in the SAME window and same
    fixture -- a two-orders-of-magnitude margin, chosen for headroom, not
    tuned to the boundary."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    state = base_state(seg, tok, "translate", status_seq=["queued"])

    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1", state,
                        deadline=3, poll=0)

    call_log = root / "calls.D1.log"
    status_calls = 0
    if call_log.exists():
        for line in call_log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                if entry.get("sub") == "status":
                    status_calls += 1

    assert status_calls <= 10, (
        f"--poll-sec 0 must be CLAMPED to a sane default, not passed through "
        f"literally -- {status_calls} status calls in a 3-second window "
        f"looks like an unclamped busy-loop (measured: clamped=1, "
        f"unclamped=143, for this identical fixture)."
    )
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"


# --------------------------------------------------------------------------- #
# _canonical_replaceable() -- os.replace(..., self.canonical) used to run
# unconditionally at both write sites (adopt_pending(), run()'s promote step),
# with no check that the canonical entry was even safe to overwrite. This guard
# closes that: absence (ENOENT) is the only state that licenses a replace: any
# OTHER lookup failure -- permission denied, a transient I/O error, a dangling
# symlink -- must read as "present, do not touch", never as "nothing here".
# --------------------------------------------------------------------------- #
def test_canonical_replaceable_true_when_genuinely_absent(tmp_path):
    """CONTROL -- the discriminating half. Without this pinned, an implementation that
    simply refuses on ANY exception would also pass the EACCES/ENOTDIR cases below, and
    would then refuse every legitimate first-ever translate."""
    job = _mkjob(tmp_path)
    assert not os.path.exists(job.canonical), "premise: nothing has created it yet"
    assert job._canonical_replaceable() is True


def test_canonical_replaceable_false_on_eacces(tmp_path):
    """A present, real file whose PARENT directory has had its search bit removed:
    os.lstat() cannot even resolve the path and raises PermissionError -- a genuine
    EACCES, not a fixture artifact. Must return False (present, not replaceable), not
    the True a genuinely absent canonical returns."""
    locked_dir = tmp_path / "locked"
    locked_dir.mkdir()
    canonical = locked_dir / "canonical.json"
    canonical.write_text('{"staged_digest": "deadbeef"}', encoding="utf-8")
    job = _mkjob(tmp_path)
    job.canonical = str(canonical)
    os.chmod(locked_dir, 0o000)
    try:
        result = job._canonical_replaceable()
    finally:
        os.chmod(locked_dir, 0o755)   # restore -- tmp_path teardown must be able to search it
    assert result is False


def test_canonical_replaceable_false_on_enotdir(tmp_path):
    """A path component that is a regular file, not a directory: os.lstat() raises
    NotADirectoryError -- distinct from both FileNotFoundError and PermissionError, and
    reached with no permission trickery (so also immune to root/sandbox quirks)."""
    blocker_file = tmp_path / "blocker_file"
    blocker_file.write_text("this is a regular file, not a directory", encoding="utf-8")
    job = _mkjob(tmp_path)
    job.canonical = str(blocker_file / "canonical.json")
    assert job._canonical_replaceable() is False


def test_canonical_replaceable_false_on_arbitrary_oserror_class(tmp_path, monkeypatch):
    """codex_job.py's own code never inspects exc.errno anywhere in this method -- the
    only branching is FileNotFoundError vs. everything else -- so ONE synthetic,
    arbitrary-errno OSError from a faulted os.lstat is a COMPLETE proof the catch-all
    branch is reached for ANY non-ENOENT failure, not a sample from a larger space (an
    implementation that special-cased only PermissionError/NotADirectoryError, still
    treating ESTALE/EIO/ELOOP as absence, would pass the two tests above and fail only
    this one). Faults only job's REAL, unmodified, production self.canonical (never
    reassigned outside segdir, unlike the fixtures above)."""
    job = _mkjob(tmp_path)
    real_lstat = os.lstat

    def fake_lstat(path, *a, **kw):
        if os.fspath(path) == job.canonical:
            raise OSError(errno.ESTALE, "Stale file handle", path)
        return real_lstat(path, *a, **kw)
    monkeypatch.setattr(os, "lstat", fake_lstat)

    assert job._canonical_replaceable() is False


def test_is_regular_false_when_fstat_raises(tmp_path, monkeypatch):
    """PROPERTY: _is_regular() must return False, never raise, if fstat() fails on an fd
    that just opened successfully -- for ANY errno, not one specific one. Before this
    fix, fstat() and close() were entirely unguarded: an OSError from either (a stale
    file handle or a transient I/O error on a network/FUSE filesystem, both real even
    though open() just succeeded) propagated straight out of the method uncaught, past
    every caller's own "False means do not proceed" check."""
    f = tmp_path / "candidate.txt"
    f.write_text("x", encoding="utf-8")
    job = _mkjob(tmp_path)

    def fake_fstat(fd):
        raise OSError(errno.ESTALE, "Stale file handle")
    monkeypatch.setattr(os, "fstat", fake_fstat)

    assert job._is_regular(str(f)) is False


def test_is_regular_survives_close_raising(tmp_path, monkeypatch):
    """PROPERTY: a close() failure (a real, documented failure mode on network/FUSE
    filesystems -- a delayed write-back error can surface at close time) must never
    escape OR corrupt the already-computed answer. fstat() already told the truth about
    the file BEFORE close() ran; close()'s own failure is irrelevant to that answer's
    correctness and must not be allowed to override or mask it."""
    f = tmp_path / "candidate.txt"
    f.write_text("x", encoding="utf-8")
    job = _mkjob(tmp_path)

    def fake_close(fd):
        raise OSError(errno.EIO, "Input/output error")
    monkeypatch.setattr(os, "close", fake_close)

    assert job._is_regular(str(f)) is True, "fstat()'s own TRUE answer must survive close()'s own failure"


def test_canonical_replaceable_false_when_fstat_raises_on_the_open_fd(tmp_path, monkeypatch):
    """The same escape one layer up: let os.lstat(self.canonical) and _is_regular()'s own
    os.open() BOTH succeed (so _canonical_replaceable() reaches its
    `return self._is_regular(...)` line, never its own except-OSError branch), then
    fault fstat(). Proves the exception does not propagate out of
    _canonical_replaceable() either, not just _is_regular() as a standalone unit."""
    job = _mkjob(tmp_path)
    Path(job.canonical).write_text("{}", encoding="utf-8")

    def fake_fstat(fd):
        raise OSError(errno.ESTALE, "Stale file handle")
    monkeypatch.setattr(os, "fstat", fake_fstat)

    assert job._canonical_replaceable() is False


def test_is_regular_false_when_read_raises(tmp_path, monkeypatch):
    """PROPERTY: open() and fstat() succeeding is not enough to trust a descriptor --
    a stale file handle or transient I/O error on the very FIRST READ (real on
    network/FUSE filesystems and damaged storage, even after both metadata calls
    already succeeded) must also refuse, in the SAME branch as an fstat() failure --
    one answer for "could not stat it" and "could not read it", not two."""
    f = tmp_path / "candidate.txt"
    f.write_text("real content", encoding="utf-8")
    job = _mkjob(tmp_path)

    def fake_read(fd, n):
        raise OSError(errno.ESTALE, "Stale file handle")
    monkeypatch.setattr(os, "read", fake_read)

    assert job._is_regular(str(f)) is False


def test_is_regular_true_on_genuinely_empty_file(tmp_path):
    """CONTROL for the read fix: os.read() on an empty regular file returns b"" --
    falsy, but NOT an error. A wrong implementation that treats a falsy read result as
    a failure would refuse every legitimately empty canonical; this pins that it must
    not."""
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    job = _mkjob(tmp_path)
    assert job._is_regular(str(f)) is True


def test_canonical_replaceable_false_when_read_raises_on_the_open_fd(tmp_path, monkeypatch):
    """The same escape one layer up as the fstat-chain test above: let lstat() and
    open()/fstat() all succeed, then fault the read. Proves the refusal reaches
    _canonical_replaceable() too, not just _is_regular() as a standalone unit."""
    job = _mkjob(tmp_path)
    Path(job.canonical).write_text("{}", encoding="utf-8")

    def fake_read(fd, n):
        raise OSError(errno.ESTALE, "Stale file handle")
    monkeypatch.setattr(os, "read", fake_read)

    assert job._canonical_replaceable() is False


def test_preflight_refuses_before_launch_when_canonical_eacces(tmp_path, monkeypatch):
    """Call site #1: run()'s preflight, BEFORE spending a codex turn. If the canonical
    entry exists but cannot be observed, launching a fresh codex turn buys nothing --
    no path through this run can ever promote successfully."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    locked_dir = tmp_path / "locked_preflight"
    locked_dir.mkdir()
    canonical = locked_dir / "canonical.json"
    canonical.write_text("{}", encoding="utf-8")
    job.canonical = str(canonical)
    os.chmod(locked_dir, 0o000)

    launch_calls = {"n": 0}

    def spy_launch():
        launch_calls["n"] += 1
        job.jobId = "J"
        return True
    monkeypatch.setattr(job, "launch", spy_launch)
    monkeypatch.setattr(job, "poll", lambda: setattr(job, "job_status", "completed"))

    try:
        rc = job.run()
    finally:
        os.chmod(locked_dir, 0o755)

    assert rc == 1
    assert launch_calls["n"] == 0, "no codex turn may be spent when the canonical cannot even be observed"
    assert job.reason == "canonical-unreadable"
    assert job.canonical_unreadable is True
    assert not job.holds_lock, "refused before the flock lease -- same shape as device-mismatch"


def test_promote_refuses_when_canonical_turns_eacces_after_preflight(tmp_path, monkeypatch):
    """Call site #2: run()'s promote branch, immediately before os.replace(). The
    preflight passes against the real, absent job.canonical; a validate_attempt() stub
    then repoints job.canonical at a locked directory BEFORE this call site's own guard
    ever runs -- simulating the canonical turning unreadable DURING this run, sometime
    after the preflight already passed it, and confirming the SECOND observation (not
    just the preflight's first one) correctly sees that and refuses. The real
    _canonical_replaceable() -- not a stub -- must still refuse, and the validated
    candidate must survive.

    NOT a test of the check-then-replace race (the guard observing safe, then a writer
    publishing something new in the window before os.replace() acts): the mutation here
    happens BEFORE this guard's own check, so the guard genuinely sees the bad state
    and correctly refuses -- an implementation that never closed that race would still
    pass this test. See
    test_canonical_replaceable_check_then_replace_window_is_a_known_unclosed_race,
    below, for the one that actually exercises that window."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)

    locked_dir = tmp_path / "locked_promote"
    locked_dir.mkdir()
    locked_canonical = locked_dir / "canonical.json"
    locked_canonical.write_text('{"marker":"present-but-locked"}', encoding="utf-8")

    def spy_launch():
        job.jobId = "J"
        return True

    def fake_poll():
        job.job_status = "completed"

    def fake_validate_attempt():
        Path(job.attempt).write_text('{"marker":"validated"}', encoding="utf-8")
        job.canonical = str(locked_canonical)          # the race: repoints mid-run
        os.chmod(locked_dir, 0o000)
        return True

    monkeypatch.setattr(job, "launch", spy_launch)
    monkeypatch.setattr(job, "poll", fake_poll)
    monkeypatch.setattr(job, "validate_attempt", fake_validate_attempt)

    try:
        rc = job.run()
    finally:
        os.chmod(locked_dir, 0o755)

    assert rc == 1
    assert job.reason == "canonical-unreadable"
    assert job.canonical_unreadable is True
    assert not os.path.exists(job.attempt), (
        "os.replace() must never fire when the canonical it would overwrite could not "
        "be observed -- but the validated candidate must not just sit at its own "
        "never-revisited random path either"
    )
    assert job.canonical_unreadable_parked is True
    assert Path(job.pending).read_text(encoding="utf-8") == '{"marker":"validated"}', (
        "the validated candidate must be relocated into self.pending -- the "
        "deterministic slot a future adopt_pending() will re-validate and retry from "
        "-- not stranded at a path nothing ever revisits again"
    )


def test_canonical_replaceable_check_then_replace_window_is_a_known_unclosed_race(
    tmp_path, monkeypatch
):
    """Documents a KNOWN, ACCEPTED limit -- not a regression, and this test is not
    asking for it to change. _canonical_replaceable()'s own observation and the
    os.replace() that follows it are two separate syscalls, not one atomic operation:
    a writer that publishes a NEW canonical in the window BETWEEN them is never
    observed, and os.replace() destroys it anyway. The per-segment flock only
    serialises COOPERATING codex_job.py processes against each other; it does nothing
    against a non-cooperating writer.

    Different from every OTHER test in this file that exercises a "bad" canonical:
    those all mutate it BEFORE the guard's own check runs, so the guard genuinely
    observes the bad state and correctly refuses -- proving the guard works, not that
    the window is closed. This test mutates AFTER the check returns its REAL, honest
    answer (wrapping the actual method rather than stubbing a fake result), racing a
    writer in during the one window the guard structurally cannot see.

    If this test ever starts FAILING -- the raced content survives -- that is real
    news: the implementation gained an atomicity property this comment says it does
    not have. Update this docstring and the design's own known-limits documentation
    then; do not just delete the test."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)

    real_check = job._canonical_replaceable
    raced_writer_content = '{"marker":"raced-writer-published-this"}'

    def racing_check():
        result = real_check()  # the REAL answer, honestly observed
        if result:
            # A non-cooperating writer publishing a new canonical in the window
            # between this check returning and os.replace() acting on its answer.
            Path(job.canonical).write_text(raced_writer_content, encoding="utf-8")
        return result
    monkeypatch.setattr(job, "_canonical_replaceable", racing_check)

    def spy_launch():
        job.jobId = "J"
        return True

    def fake_poll():
        job.job_status = "completed"

    def fake_validate_attempt():
        Path(job.attempt).write_text('{"marker":"validated"}', encoding="utf-8")
        return True

    monkeypatch.setattr(job, "launch", spy_launch)
    monkeypatch.setattr(job, "poll", fake_poll)
    monkeypatch.setattr(job, "validate_attempt", fake_validate_attempt)

    rc = job.run()

    assert rc == 0
    assert job.promoted is True
    assert Path(job.canonical).read_text(encoding="utf-8") != raced_writer_content, (
        "the KNOWN, ACCEPTED limit: the raced writer's content is gone, overwritten "
        "by a promote whose own check never observed it. If this assertion starts "
        "failing, the race window has closed -- that is real news, not a broken test"
    )


def test_adopt_pending_refuses_when_canonical_cannot_be_observed(tmp_path, monkeypatch):
    """Call site #3: adopt_pending()'s own os.replace(self.pending, self.canonical).
    Its own gates run with --candidate-file self.pending and never touch self.canonical
    at all, so nothing upstream of this call re-observes it -- same guard, same class of
    defect, exercised here in isolation from the full launch/poll/validate machinery."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text("{}", encoding="utf-8")
    target = Path(job.canonical + ".target")
    target.write_text("{}", encoding="utf-8")
    os.symlink(target, job.canonical)
    target.unlink()   # dangling
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)

    assert job.adopt_pending() is False
    assert calls == ["draft_ready.py", "validate_draft.py"], (
        "both gates ran and passed -- the refusal must come from the canonical check, "
        "not from a gate rejection"
    )
    assert job.reason == "canonical-unreadable"
    assert os.path.lexists(job.canonical), "the dangling symlink must survive"
    assert not os.path.exists(job.canonical)
    assert os.path.exists(job.pending), "recoverable work must not be discarded"


def test_run_refuses_immediately_when_adopt_pending_hits_canonical_unreadable(tmp_path, monkeypatch):
    """PROPERTY: when adopt_pending()'s own guard refuses a candidate that passed every
    gate -- NOT "no usable pending", the only other reason it returns False -- run()
    must stop right there, never fall through to launch(). Falling through would spend
    a fresh paid turn that can never succeed either (the canonical is still unreadable),
    and if that fresh completion then landed in the no-budget branch,
    _defer_attempt()'s own documented last-writer-wins semantics would overwrite the
    still-good pending candidate with the new, unvalidated one -- destroying validated
    work to make room for work nobody has checked yet."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    Path(job.pending).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)

    # run()'s OWN preflight lstat()s this exact same self.canonical path first -- must
    # be let through genuinely (as absent) or the run never reaches adopt_pending() at
    # all. Only the SECOND observation, adopt_pending()'s own, is faulted.
    lstat_calls = {"n": 0}
    real_lstat = os.lstat

    def fake_lstat(path, *a, **kw):
        if os.fspath(path) == job.canonical:
            lstat_calls["n"] += 1
            if lstat_calls["n"] >= 2:
                raise OSError(errno.EIO, "Input/output error", path)
        return real_lstat(path, *a, **kw)
    monkeypatch.setattr(os, "lstat", fake_lstat)

    launch_calls = {"n": 0}

    def spy_launch():
        launch_calls["n"] += 1
        job.jobId = "J"
        return True
    monkeypatch.setattr(job, "launch", spy_launch)

    rc = job.run()

    assert rc == 1
    assert launch_calls["n"] == 0, (
        "no fresh paid turn may be spent once adopt_pending() has already found a "
        "blocked-but-validated candidate"
    )
    assert job.reason == "canonical-unreadable"
    assert job.canonical_unreadable is True
    assert os.path.exists(job.pending), "the validated pending candidate must survive, untouched"
    assert Path(job.pending).read_text(encoding="utf-8") == "{}"


def test_relocate_at_final_promote_safely_supersedes_an_unconfirmed_pending(tmp_path, monkeypatch):
    """The one reachable case where self.pending can hold something at the moment the
    FINAL promote refusal's relocate fires: adopt_pending()'s own gate call returned
    None (a budget/timeout/spawn failure -- "could not validate", not "ran and
    rejected" or "ran and passed"), which leaves self.pending UNTOUCHED and does not
    set self.canonical_unreadable, so run() falls through past the early return above
    into a fresh launch. Proves the relocate never destroys anything MORE validated
    than what replaces it: whatever was sitting in self.pending here never reached a
    pass/fail verdict in THIS run at all, while the fresh attempt that supersedes it
    has just passed every gate in the SAME run -- matching, never exceeding,
    _defer_attempt()'s own already-accepted "always refresh the slot, newest
    completion wins" bound for the sibling no-budget case."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    Path(job.pending).write_text('{"marker":"unconfirmed-this-run"}', encoding="utf-8")

    def gate_none(args, timeout):
        return None  # simulates adopt_pending()'s own gate call exhausting budget
    monkeypatch.setattr(job, "_gate", gate_none)

    locked_dir = tmp_path / "locked_scenario2_caseb"
    locked_dir.mkdir()
    locked_canonical = locked_dir / "canonical.json"
    locked_canonical.write_text("{}", encoding="utf-8")

    def spy_launch():
        job.jobId = "J"
        return True

    def fake_poll():
        job.job_status = "completed"

    def fake_validate_attempt():
        Path(job.attempt).write_text('{"marker":"fresh-and-validated"}', encoding="utf-8")
        job.canonical = str(locked_canonical)          # the race: repoints mid-run
        os.chmod(locked_dir, 0o000)
        return True

    monkeypatch.setattr(job, "launch", spy_launch)
    monkeypatch.setattr(job, "poll", fake_poll)
    monkeypatch.setattr(job, "validate_attempt", fake_validate_attempt)

    try:
        rc = job.run()
    finally:
        os.chmod(locked_dir, 0o755)

    assert rc == 1
    assert job.reason == "canonical-unreadable"
    assert job.canonical_unreadable is True
    assert job.canonical_unreadable_parked is True
    assert Path(job.pending).read_text(encoding="utf-8") == '{"marker":"fresh-and-validated"}', (
        "the fresh, gate-validated attempt must supersede the unconfirmed pending -- not "
        "because destroying it is free, but because the fresh candidate is strictly MORE "
        "validated than what it replaces"
    )


class _ClosedStderr:
    """Stand-in for a closed TEXT stream (as opposed to a closed underlying fd, which
    raises BrokenPipeError instead)."""
    def write(self, *a, **kw):
        raise ValueError("I/O operation on closed file")


class _BrokenPipeStderr:
    def write(self, *a, **kw):
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")


class _NeverSeenBeforeError(Exception):
    """A type nothing in codex_job.py enumerates anywhere. See the parametrized test
    below for why THIS specific case is the one that actually pins the property, not
    just one more known instance of it."""


class _ArbitraryExplodingStderr:
    def write(self, *a, **kw):
        raise _NeverSeenBeforeError("an arbitrary, unenumerated write failure")


@pytest.mark.parametrize("stderr_stand_in,label", [
    (None, "stderr is None (fd 2 closed at interpreter startup) -> AttributeError"),
    (_ClosedStderr(), "stderr is a CLOSED text stream -> ValueError"),
    (_BrokenPipeStderr(), "stderr's underlying pipe is broken -> BrokenPipeError"),
    (_ArbitraryExplodingStderr(), "an arbitrary, UNENUMERATED exception type -- this is "
     "the case that actually pins the property (\"no failure of the diagnostic can "
     "change the outcome\"); the three cases above only pin three known INSTANCES of "
     "it, and an implementation narrowed to `except (OSError, AttributeError, "
     "ValueError, BrokenPipeError)` would still pass all three and fail only this one"),
])
def test_canonical_replaceable_false_survives_any_stderr_failure(
    tmp_path, monkeypatch, stderr_stand_in, label
):
    """PROPERTY: no failure of the diagnostic write inside _canonical_replaceable()'s
    except branch can change its return value. NOT "OSError from the write is
    tolerated" -- `sys.stderr` being None (AttributeError) or a closed TEXT stream
    (ValueError) are both real, distinct failure modes a bare `except OSError` would
    not catch, and either would let the exception escape past the caller's own
    protective state. A wrong implementation that enumerates known exception types,
    however many, is still "sampling", not "pinning" -- see the fourth parametrize
    case."""
    job = _mkjob(tmp_path)
    locked_dir = tmp_path / "locked_stderr_test"
    locked_dir.mkdir()
    candidate = locked_dir / "canonical.json"
    candidate.write_text("{}", encoding="utf-8")
    job.canonical = str(candidate)
    os.chmod(locked_dir, 0o000)
    monkeypatch.setattr(sys, "stderr", stderr_stand_in)
    try:
        result = job._canonical_replaceable()
    finally:
        os.chmod(locked_dir, 0o755)
    assert result is False, label


def test_promote_refuses_when_stderr_raises_an_arbitrary_unenumerated_exception(
    tmp_path, monkeypatch
):
    """The e2e counterpart of the property-pinning parametrize case above, through the
    FULL run() path (not just the direct method): proves the run()-level guarantee
    (refusal happens, the flag is set, the attempt survives) holds for a stderr failure
    nothing in codex_job.py could have specifically enumerated -- not merely that the
    one reported BrokenPipeError instance is handled."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(sys, "stderr", _ArbitraryExplodingStderr())

    locked_dir = tmp_path / "locked_promote_arbitrary"
    locked_dir.mkdir()
    locked_canonical = locked_dir / "canonical.json"
    locked_canonical.write_text('{"marker":"present-but-locked"}', encoding="utf-8")

    def spy_launch():
        job.jobId = "J"
        return True

    def fake_poll():
        job.job_status = "completed"

    def fake_validate_attempt():
        Path(job.attempt).write_text('{"marker":"validated"}', encoding="utf-8")
        job.canonical = str(locked_canonical)          # the race: repoints mid-run
        os.chmod(locked_dir, 0o000)
        return True

    monkeypatch.setattr(job, "launch", spy_launch)
    monkeypatch.setattr(job, "poll", fake_poll)
    monkeypatch.setattr(job, "validate_attempt", fake_validate_attempt)

    try:
        rc = job.run()
    finally:
        os.chmod(locked_dir, 0o755)

    assert rc == 1
    assert job.reason == "canonical-unreadable"
    assert job.canonical_unreadable is True
    assert job.canonical_unreadable_parked is True
    assert not os.path.exists(job.attempt)
    assert Path(job.pending).read_text(encoding="utf-8") == '{"marker":"validated"}', (
        "the validated candidate must survive a stderr failure of a type nothing in "
        "codex_job.py specifically enumerates -- relocated into self.pending, not left "
        "at its own never-revisited path"
    )


def test_canonical_unreadable_detail_survives_overwritten_error_detail_in_finalize(
    tmp_path, monkeypatch
):
    """PROPERTY: finalize()'s reported error_detail must come from
    self.canonical_unreadable_detail, not bare self.error_detail, whenever
    self.canonical_unreadable is set -- the same "the flag is authoritative, the
    string is not" guarantee self.canonical_unreadable already gets for self.reason,
    extended to its diagnostic payload.

    Exercised directly against finalize(), not through a full run(): the ORIGINAL
    version of this test drove it end-to-end (adopt_pending()'s own refusal, then a
    real launch() failure overwriting self.error_detail on the fall-through to a
    fresh launch) -- but the early return this same round adds right after
    adopt_pending() (see test_run_refuses_immediately_when_adopt_pending_hits_
    canonical_unreadable, above) means run() no longer reaches launch() at all once
    adopt_pending() has refused, closing off that specific overwrite path as a side
    effect. Nothing else in run()'s current control flow runs after any of the three
    canonical-unreadable refusal sites either (each is either an immediate return or
    the terminal action in its branch), so no LIVE path currently exercises the
    overwrite. The field and the preference logic stay -- cheap insurance against a
    future change reopening one -- and this test pins the mechanism itself directly,
    independent of whether today's control flow happens to reach it."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    job.canonical_unreadable = True
    job.canonical_unreadable_detail = "canonical unreadable: original diagnostic, EIO"
    job.error_detail = "companion stderr: an unrelated later overwrite"  # simulates
    # whatever ELSE might, in a future control-flow change, run after the refusal and
    # touch self.error_detail -- the mechanism must not assume today's reachability
    # holds_lock is only ever True after a real flock acquire in run(); set directly
    # here so finalize()'s own joblog write (holds_lock-gated) actually runs, giving
    # this test something durable to read back rather than only the stdout line.
    job.holds_lock = True

    job.finalize()

    assert job.canonical_unreadable_detail == "canonical unreadable: original diagnostic, EIO", (
        "finalize() must never mutate the snapshot itself"
    )
    joblog = job.read_joblog()
    assert joblog is not None, "finalize() must have written the joblog (holds_lock is True)"
    assert joblog["error_detail"] == "canonical unreadable: original diagnostic, EIO", (
        "the ORIGINAL canonical-unreadable diagnostic must survive under its own "
        "dedicated field even though self.error_detail was overwritten afterward"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
