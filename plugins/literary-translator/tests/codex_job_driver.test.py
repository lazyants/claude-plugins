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
# #438: codex_job.py's own `import claim_record` (a plugin-path sibling,
# resolved via its own sys.path.insert(SCRIPTS_DIR) -- see codex_job.py's
# module comment) needs the real claim_record.py physically present
# wherever a COPY of codex_job.py is staged for a real subprocess to run --
# the in-process `exec_module` load just below this constant block gets it
# for free (it loads codex_job.py from its REAL location, where
# claim_record.py already lives), but a copy staged into a tmp_path
# scripts/ dir for the SUBPROCESS suite below does not, and needs it copied
# alongside explicitly (see build_root()/make_real_gate_root()).
CLAIM_RECORD_SRC = SCRIPTS_DIR / "claim_record.py"

assert DRIVER_SRC.is_file(), f"expected the driver at {DRIVER_SRC}"
assert CLAIM_RECORD_SRC.is_file(), f"expected claim_record.py at {CLAIM_RECORD_SRC}"

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
# #398: the real validate_draft.py splits its non-zero exits -- 1 is a verdict on the
# CANDIDATE's content, 2 is usage/environment/source-availability (a missing segpack, an
# unreadable profile.yml, an internal error). codex_job.py acts terminally on 1 and ONLY on
# 1, so this stub has to be able to say 2; before #398 it could only ever say 1, and a test
# bed that cannot express the distinction cannot pin it.
if isinstance(d, dict) and d.get("validator_env_fail"):
    print("ERROR: simulated environment failure", file=sys.stderr); sys.exit(2)
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

    def payload(good_tok=True, quality=True, schema=True, env_fail=False):
        if kind == "translate":
            return {"dispatch_token": tok if good_tok else tok + "_WRONG",
                    "seg": seg, "structure_ok": True, "quality_ok": quality,
                    # #398: passes draft_ready.py, then makes validate_draft.py exit 2
                    # rather than 1 -- the environment side of the new boundary.
                    "validator_env_fail": env_fail}
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
               "invalid_schema": payload(True, True, False),
               "validator_env_failure": payload(True, True, True, env_fail=True)}.get(mode, payload())
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
    # #438: codex_job.py's own `import claim_record` needs the real sibling
    # physically present next to this staged copy -- see CLAIM_RECORD_SRC's
    # own comment above.
    (scripts / "claim_record.py").write_text(CLAIM_RECORD_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    (scripts / "draft_ready.py").write_text(STUB_DRAFT_READY, encoding="utf-8")
    (scripts / "validate_draft.py").write_text(STUB_VALIDATE_DRAFT, encoding="utf-8")
    (scripts / "review_ready.py").write_text(STUB_REVIEW_READY, encoding="utf-8")
    (scripts / "draft_sha1.py").write_text(STUB_DRAFT_SHA1, encoding="utf-8")
    stage_ledger_writer(root)
    companion = root / "codex-companion.mjs"
    companion.write_text("// fake\n", encoding="utf-8")
    fake_node = root / "fake_node.py"
    fake_node.write_text(FAKE_NODE, encoding="utf-8")
    _chmodx(fake_node)
    return root, str(companion), str(fake_node)


# #398: the REAL ledger_update.py plus the two schemas it loads, staged into a fixture root
# so codex_job.py's new terminal write goes through the shipped writer -- schema validation
# included -- rather than a stub that would certify an invented payload shape. Staged into
# EVERY build_root() fixture on purpose: a "no fragment was written" assertion is worthless
# if it can pass merely because the writer was absent.
LEDGER_WRITER_SRC = SCRIPTS_DIR / "ledger_update.py"
LEDGER_SCHEMAS = ("ledger-record-base.schema.json", "ledger-fragment.schema.json")


def stage_ledger_writer(root):
    """Stage ledger_update.py + its schemas + runs/ under `root`, in the durable-root layout
    (scripts/ + schemas/). The separate-roots test below does NOT reuse this: a plugin
    installation uses assets/scripts + assets/schemas, a different shape, so it stages its
    own."""
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LEDGER_WRITER_SRC, scripts_dir / "ledger_update.py")
    schemas = root / "schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    for name in LEDGER_SCHEMAS:
        shutil.copy2(SCHEMAS_SRC_DIR / name, schemas / name)
    (root / "runs").mkdir(parents=True, exist_ok=True)


def fragment_path(root, seg):
    return root / "runs" / "ledger.d" / ("%s.json" % seg)


def read_fragment(root, seg):
    return json.loads(fragment_path(root, seg).read_text(encoding="utf-8"))


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
    # Mimic lane C's dispatch: the 9 FROZEN flags only (+ test-only --poll-sec/--node).
    # NO --write/--fresh/--effort -> the driver must add workspace-write + fresh + effort
    # to the internal codex launch itself.
    #
    # #438: --run-id joined the frozen set once codex_job.py's own main() made it
    # fatal-if-absent -- see codex_job.py's own comment on why it is hand-validated
    # rather than argparse `required=True`. This is a hand-maintained registration
    # surface (it must be edited whenever codex_job.py's CLI gains a new mandatory
    # flag) and it drifted silently exactly once already -- see
    # test_spawn_driver_argv_covers_every_mandatory_codex_job_flag below, which pins
    # it against codex_job.py's own CLI so the NEXT drift fails loudly, by name,
    # instead of as an opaque downstream error in every subprocess test in this file.
    # RUN_ID is derived from `tok`'s own leading `RUN_ID:seg[...]` shape here ONLY
    # because this fixture already threads a synthetic `tok` through every caller --
    # codex_job.py itself must never derive it this way (see its own comment: a
    # malformed --expect-token would then read as "no claim record" -> "not claimed"
    # -> proceed, the exact silent-degradation shape #438 exists to refuse).
    argv = [
        sys.executable, str(root / "scripts" / "codex_job.py"),
        "--kind", kind, "--companion", companion, "--cwd", str(root), "--seg", seg,
        "--prompt-file", str(prompt), "--expect-token", tok,
        "--run-id", tok.split(":", 1)[0], "--disp", disp,
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


def test_spawn_driver_argv_covers_every_mandatory_codex_job_flag(tmp_path):
    """Registration-drift guard for spawn_driver()'s own "9 FROZEN flags" argv
    (see its own comment) -- a hand-maintained list that must be edited
    whenever codex_job.py's CLI gains a new mandatory flag. #438's own
    --run-id drifted against this exact list once already: it landed in
    codex_job.py, and every SUBPROCESS test in this file then failed with
    an opaque downstream error (an IndexError two frames from the actual
    cause) rather than a message naming the missing flag. This test fails
    LOUDLY and by name instead.

    Inspects the REAL argv spawn_driver() constructs (via subprocess.Popen's
    own recorded `.args` -- never a second reimplementation of the flag
    list) against the UNION of codex_job.py's own argparse `required=True`
    dests (introspected directly from the real parser, so THAT half can
    never drift silently) plus the hand-validated-fatal dests main() checks
    in its own body. `--run-id` is optional at the argparse layer but fatal
    if absent in main() (see codex_job.py's own comment on why), so it
    cannot be introspected from argparse and is named here explicitly --
    if a FUTURE flag joins that same hand-validated-fatal category, it must
    be added to `_HAND_VALIDATED_FATAL_DESTS` alongside it or this test's
    own coverage silently narrows."""
    _HAND_VALIDATED_FATAL_DESTS = {"run_id"}
    required_dests = {
        action.dest
        for action in codex_job._build_parser()._actions
        if getattr(action, "required", False)
    }
    mandatory_dests = required_dests | _HAND_VALIDATED_FATAL_DESTS
    mandatory_flags = {"--" + d.replace("_", "-") for d in mandatory_dests}

    root, companion, node = build_root(tmp_path)
    proc = spawn_driver(root, companion, node, "c001", "RUN1:c001", "translate", "Dspy",
                        base_state("c001", "RUN1:c001", "translate", status_seq=["queued"]),
                        popen=True)
    try:
        flags_present = {a for a in proc.args if isinstance(a, str) and a.startswith("--")}
        missing = mandatory_flags - flags_present
        assert not missing, (
            f"spawn_driver()'s argv is missing mandatory codex_job.py flag(s) "
            f"{sorted(missing)} -- codex_job.py's CLI contract moved and this "
            f"fixture did not follow; see this test's own docstring."
        )
    finally:
        proc.kill()
        proc.wait(timeout=10)


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
    """#438: `--run-id` is part of the DEFAULT argv, and defaults to the run
    component of the default `--expect-token` so the two agree.

    It was absent before, which made every usage test below pass for the WRONG
    reason once main() started refusing a missing --run-id: `test_usage_missing_
    companion` (say) got its exit 2 from the --run-id check, several checks
    earlier, and would have kept passing even if the companion check had been
    deleted outright. A caller that wants to exercise the run-id checks
    themselves overrides `run_id`; `run_id=None` omits the flag entirely."""
    d = dict(kind="translate", companion=_companion_file(tmp_path), cwd=str(tmp_path),
             seg="c001", prompt_file=_prompt_file(tmp_path), expect_token="RUN:c001",
             disp="d1", deadline_sec="600", run_id="RUN")
    d.update(over)
    argv = ["--kind", d["kind"], "--companion", d["companion"], "--cwd", d["cwd"],
            "--seg", d["seg"], "--prompt-file", d["prompt_file"],
            "--expect-token", d["expect_token"], "--disp", d["disp"],
            "--deadline-sec", d["deadline_sec"], "--node", "node"]
    if d["run_id"] is not None:
        argv += ["--run-id", d["run_id"]]
    return argv


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
# in-process white-box: --run-id SHAPE, and its consistency with --expect-token
# (#438 M3). main() previously checked only that --run-id was non-blank, then
# handed the raw value to claim_record.claimed_path(); and nothing tied the
# claim NAMESPACE (--run-id) to the run the token dispatches for.
#
# THREE checks live here now, in main()'s own order: the --run-id shape check,
# then "--expect-token must carry a run component at all", then "that component
# must equal --run-id". The middle one arrived last and is the reason the tests
# below deliberately leave ONE later defect in an otherwise-valid argv: all
# three exit 2, so only the MESSAGE says which of them answered, and a check
# that stops firing is otherwise invisible behind the next one down.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_run_id", ["../x", "/tmp/elsewhere", "z..poison",
                                        "a/../..", " RUN", "."])
def test_usage_run_id_shape_is_fatal_not_a_traceback(tmp_path, capsys, bad_run_id):
    """A --run-id that is not usable as a single path component must exit 2
    with a message naming the FLAG. The --expect-token is built to AGREE with
    the bad value on purpose: with an agreeing token, the namespace check
    below cannot fire, so the only thing that can produce the 2 is the shape
    check itself. Delete that check and the value reaches
    claim_record.claimed_path(), which now raises -- a refusal reached through
    an exception, reported as `reason: error: ValueError(...)`, is not an
    acceptable answer to a mistyped flag and is not exit 2 either."""
    argv = _argv(tmp_path, run_id=bad_run_id, expect_token="%s:c001" % bad_run_id)
    assert codex_job.main(argv) == 2
    err = capsys.readouterr().err
    assert "--run-id" in err, err
    assert "--expect-token" not in err, (
        "this must be refused by the SHAPE check, not by the namespace check -- "
        "got: %s" % err
    )


@pytest.mark.parametrize("token", ["RUN-A:c001", "RUN-A:c001:r2"])
def test_usage_expect_token_and_run_id_must_name_the_same_run(tmp_path, capsys, token):
    """The claim namespace must be the run the token dispatches for. With a
    claim on record under RUN-A, `--expect-token RUN-A:c001 --run-id RUN-B`
    looked the claim up under RUN-B, found nothing, and read CLAIM_ABSENT as
    "not claimed" -- so the D8 chokepoint could be walked straight past by a
    direct invocation. Both values here are individually valid, so nothing
    EARLIER in main() can produce this exit 2: delete the namespace check and
    main() falls through into job.run(). Covers the review-shaped
    RUN:seg:r<label> token too, whose run component is still everything before
    the FIRST colon."""
    assert codex_job.main(_argv(tmp_path, expect_token=token, run_id="RUN-B")) == 2
    err = capsys.readouterr().err
    assert "--expect-token" in err and "RUN-A" in err and "RUN-B" in err, err


@pytest.mark.parametrize("token", ["RUN:c001", "RUN:c001:r2"])
def test_usage_agreeing_token_and_run_id_clear_every_new_check(tmp_path, capsys, token):
    """The control the two tests above and the one below all need: a well-formed
    token whose run component AGREES with --run-id must PASS all three checks
    this block added (--run-id shape, token-carries-a-run-component,
    token-vs---run-id) rather than being cleared by checks that refuse
    everything. Proven by leaving exactly one LATER defect in the argv (a
    --companion that does not exist) and asserting the exit 2 comes from THAT
    check -- main() cannot reach the companion check without having cleared all
    three, every one of which sits above it.

    BOTH legitimate token shapes are covered. The run component is everything
    before the FIRST colon, so a review token has one colon more than the check
    needs and must still clear it: were the check to demand exactly one colon,
    every review dispatch the shipped template makes
    (mass-translate-wf.template.js:1313 builds RUN_ID + ":" + seg + ":r" +
    roundLabel, segment_dispatch_driver.py's review_dispatch_token() the same
    shape) would be refused at the chokepoint, which no test asserting only the
    translate form would notice."""
    argv = _argv(tmp_path, run_id="RUN", expect_token=token,
                 companion=str(tmp_path / "definitely_not_here.mjs"))
    assert codex_job.main(argv) == 2
    err = capsys.readouterr().err
    assert "--companion" in err, err
    assert "--run-id" not in err and "--expect-token" not in err, err


@pytest.mark.parametrize("token", ["notoken", "BOGUS", ":c001", "", ":"])
def test_usage_token_with_no_run_component_is_refused(tmp_path, capsys, token):
    """A token carrying NO run component -- no colon at all, or an empty leading
    component -- is FATAL, not skipped.

    This test replaces one that asserted the OPPOSITE (it was named
    `..._is_left_alone`). That test pinned a deliberate skip whose stated
    rationale was "the gates already refuse a malformed token on their own":
    true of ADOPTING the existing draft, false of DESTROYING it. Refusing the
    existing draft is exactly what makes run() treat the segment as needing work
    and launch a fresh translate, whose post-launch os.replace overwrites the
    claimed draft and the pre_claim_content_sha1 baseline taken from it; the
    gates then re-run against codex's NEW attempt, whose dispatch_token is
    whatever the prompt told it to stamp, so the malformed token never has to
    satisfy anything. Meanwhile --run-id went entirely unexamined and was free
    to name a foreign claim namespace, where the lookup finds nothing,
    CLAIM_ABSENT reads as "not claimed", and the D8 chokepoint is walked. The
    non-derivation rule is untouched by refusing here: it governs where RUN_ID
    comes FROM (the caller, always), not whether a malformed token is tolerated.

    The argv carries exactly one LATER defect (a --companion that does not
    exist), and that is what makes this a real assertion in both directions.
    Restore the lenient predicate and the argv is STILL refused with exit 2 --
    by the companion check, several checks lower -- so the exit code alone
    proves nothing here and the MESSAGE is the assertion. The same defect is
    what proves this refusal is REACHABLE: nothing above it can see a malformed
    token (argparse only makes --expect-token `required`, it never
    pattern-checks the value; validate_seg reads --seg, not the token), so if
    something earlier had rejected these argvs the message would name --kind or
    --seg, and if nothing had, it would name --companion."""
    argv = _argv(tmp_path, run_id="RUN", expect_token=token,
                 companion=str(tmp_path / "definitely_not_here.mjs"))
    assert codex_job.main(argv) == 2
    err = capsys.readouterr().err
    assert "--expect-token" in err and "run component" in err, err
    assert "--companion" not in err, (
        "the token check must fire ABOVE the companion check -- a --companion "
        "message here means a token with no run component was waved through "
        "again, leaving --run-id free to name a foreign claim namespace: %s" % err
    )
    assert "--seg" not in err, (
        "refused by an EARLIER check than the one under test -- got: %s" % err
    )


# --------------------------------------------------------------------------- #
# in-process white-box: time ceilings + finalize-tail (case o)
# --------------------------------------------------------------------------- #
def _mkjob(tmp_path, kind="translate", seg="c001", tok="RUN:c001", disp="d1",
           deadline=100, poll=1, run_id=None):
    """`run_id` defaults to None -- the pre-#438 white-box shape every existing
    caller below relies on, in which the D8 claim guard is a no-op by
    construction (see codex_job.py's own __init__ docstring). Pass it explicitly
    to exercise the guard."""
    seg_dir = tmp_path / "durable" / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "durable"
    companion = _companion_file(tmp_path)
    return codex_job.CodexJob(
        kind=kind, seg=seg, tok=tok, disp=disp, root=str(root), companion=companion,
        prompt_text=PROMPT_ONE, prompt_file=_prompt_file(tmp_path), deadline_sec=deadline,
        poll_sec=poll, effort="high", node="node", run_id=run_id)


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
    """Positive control: on an ordinary checkout the real os.stat()-based check passes.

    #697 changed what it is checking. segdir and dirname(attempt) used to be ONE directory,
    which made this a regression guard against a hypothetical; the gated attempt now stages
    in a mkdtemp directory beside durable_root, so the two are genuinely different paths
    that merely happen to share a filesystem. The check is live from here on."""
    job = _mkjob(tmp_path)
    assert job._ensure_staging() is True
    assert os.path.dirname(job.attempt) != job.segdir, (
        "fixture precondition: after #697 the staging directory must not be segdir, "
        "otherwise this test silently returns to guarding a hypothetical"
    )
    assert job._preflight_same_device() is True


def test_preflight_same_device_refuses_before_staging_exists(tmp_path):
    """_ensure_staging() has not run, so there is no staging directory to compare. A
    refusal, never a TypeError out of os.path.dirname(None) past every caller's own
    "False means do not proceed" check."""
    job = _mkjob(tmp_path)
    assert job.attempt is None
    assert job._preflight_same_device() is False


def test_preflight_same_device_refuses_on_mismatch(tmp_path, monkeypatch):
    """#409 property 3: a private-staging directory on a DIFFERENT filesystem than
    segments/ must refuse before any dispatch -- a cross-device os.replace() at promote
    time is not atomic. Hard to fabricate two REAL filesystems in a portable unit test, so
    this pins the check's own logic by mocking os.stat's st_dev.

    #697: the mismatch is injected on the STAGING directory by PATH, which is the layout
    that is now real -- the gated attempt stages outside durable_root while the canonical
    it is renamed onto lives in segments/. Before #697 the three stat() targets were one
    path string and could only be told apart by CALL ORDER, which pinned the method's
    statement order rather than its subject."""
    job = _mkjob(tmp_path)
    assert job._ensure_staging() is True
    staging = os.path.dirname(job.attempt)
    assert staging != job.segdir
    real_stat = os.stat
    seen = {"staging": 0}

    def fake_stat(path, *a, **kw):
        st = real_stat(path, *a, **kw)
        if os.fspath(path) == staging:
            seen["staging"] += 1
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev + 1, st.st_nlink,
                                   st.st_uid, st.st_gid, st.st_size, st.st_atime,
                                   st.st_mtime, st.st_ctime))
        return st
    monkeypatch.setattr(os, "stat", fake_stat)
    assert job._preflight_same_device() is False
    assert seen["staging"] >= 1, "the check must actually stat the staging directory"


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


def test_run_preflight_canonical_check_is_bounded_by_finalize_timeout_not_the_whole_job_ceiling(
    tmp_path, monkeypatch
):
    """run()'s preflight _canonical_replaceable() call (immediately after the
    device-mismatch check, BEFORE the flock is even acquired) must reserve
    FINALIZE_TAIL out of the whole job's abs_remaining() ceiling, exactly like the
    LATER _canonical_replaceable() call in the promote branch already does (both guard
    the same os.replace(..., self.canonical) risk) -- not consult abs_remaining()
    directly, which reserves nothing and would let this drain eat into the 150s
    finalize budget before a single paid codex turn is even spent.

    Constructs a job whose abs_remaining() is small but still POSITIVE (5s) -- below
    FINALIZE_TAIL (10s), so finalize_timeout() clamps to 0.0 (see
    test_finalize_timeout_reserves_tail, above) while abs_remaining() itself would
    still look like "plenty of budget" to a caller that consulted it directly. A
    correctly-bounded preflight refuses immediately, before ANY of hygiene/safe_adopt/
    adopt_pending/launch runs; a caller sharing the whole-job ceiling proceeds past
    the preflight and reaches launch() instead."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    Path(job.canonical).write_text('{"marker":"present-and-readable"}', encoding="utf-8")
    monkeypatch.setattr(job, "abs_remaining", lambda: 5.0)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    monkeypatch.setattr(job, "adopt_pending", lambda: False)

    launch_calls = {"n": 0}

    def spy_launch():
        launch_calls["n"] += 1
        job.jobId = "J"
        return True
    monkeypatch.setattr(job, "launch", spy_launch)

    def fake_poll():
        job.job_status = "completed"
    monkeypatch.setattr(job, "poll", fake_poll)

    rc = job.run()

    assert rc == 1
    assert job.reason == "canonical-unreadable"
    assert job.canonical_unreadable is True
    assert launch_calls["n"] == 0, (
        "no fresh paid turn may be spent once the preflight's own canonical check has "
        "refused -- a version sharing abs_remaining() directly would see 5.0 > 0, pass "
        "the preflight, and reach launch() anyway"
    )
    assert Path(job.canonical).read_text(encoding="utf-8") == '{"marker":"present-and-readable"}', (
        "the canonical must survive untouched -- this is a refusal, not a promote"
    )


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

    # Exhaust the finalize budget only from poll() onward: run()'s own preflight
    # canonical check is ALSO bounded by finalize_timeout (-> abs_remaining, see
    # test_run_preflight_canonical_check_is_bounded_by_finalize_timeout_not_the_whole_job_ceiling,
    # above) and must see the real, comfortably-positive budget a fresh job actually
    # has at that early point -- a flat constant for the whole run() would trip THAT
    # guard instead of the promote guard this test means to exercise.
    budget = {"exhausted": False}
    monkeypatch.setattr(job, "abs_remaining", lambda: 2.0 if budget["exhausted"] else 200.0)

    def fake_poll():
        job.job_status = "completed"
        budget["exhausted"] = True
    monkeypatch.setattr(job, "poll", fake_poll)
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

    # Exhaust the finalize budget only from poll() onward -- see
    # test_run_refuses_promote_when_budget_exhausted's own comment on this same
    # pattern, above: a flat constant for the whole run() would also trip the
    # preflight canonical check, which is not what this test means to exercise.
    budget = {"exhausted": False}
    monkeypatch.setattr(job, "abs_remaining", lambda: 2.0 if budget["exhausted"] else 200.0)

    def fake_poll():
        job.job_status = "completed"
        budget["exhausted"] = True
    monkeypatch.setattr(job, "poll", fake_poll)
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
    # #483: run() records the canonical's authorization as its first act, and this test
    # calls adopt_pending() directly, so it states that baseline itself. The value is the
    # honest one for this fixture -- the canonical is absent, which the predicate
    # establishes as "no token" -- rather than a blanket permit: seeding it THROUGH the
    # shipped predicate means a change to what an absent canonical means shows up here.
    job.canonical_authority = job._canonical_authority(job.poll_remaining)
    assert job.canonical_authority == (True, None), "premise: absent reads as no token"
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
    # #665: the same distinction, now on the OTHER axis. A gate that could not run must not
    # set the content-rejection flag either -- run() acts TERMINALLY on it, so an
    # implementation that set it here would block a segment whose gate merely ran out of
    # budget, while this test's own pending assertion above still passed.
    assert job.translate_content_rejected is False


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
    Regression pin, not a RED proof (this is the driver's own established behavior).
    #409: the "NEW" candidate is seeded in the sandbox, matching where codex actually
    writes it."""
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
    """MINOR-1 guard: adopt_pending() returning False (e.g. no-budget, pending
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


def _write_claim(job, run_id=None, profile="from-converged"):
    """A REAL claim record at runs/<run_id>/.claimed.<seg>, written through
    claim_record.py's own builder + writer -- never hand-rolled JSON, so a
    drift in that module's field set or write discipline surfaces here too.

    The payload is every declared field defaulted to None, overridden with the
    few this file's assertions read. Deliberately built from
    CLAIM_RECORD_FIELDS rather than by naming all of them: nothing in a claim
    ORDERING test depends on what the record SAYS (classify_claim_record() is
    an lstat and never opens the file), so spelling the full field list here
    would couple these tests to a field set they never read, while this shape
    still fails loudly if a field is REMOVED from the builder.

    `codex_job.claim_record` -- the module object the DRIVER itself imported --
    is used rather than a second independent load, so the record these tests
    write and the record the guard reads can never be produced by two different
    copies of the module."""
    cr = codex_job.claim_record
    run_id = job.run_id if run_id is None else run_id
    path = cr.claimed_path(run_id, job.seg, Path(job.root) / "runs")
    payload = cr.build_claim_record(**dict(
        {field: None for field in cr.CLAIM_RECORD_FIELDS},
        seg=job.seg, profile=profile, run_id=run_id,
        operator_invocation="pytest tests/codex_job_driver.test.py",
        claimed_at="2026-08-08T00:00:00Z"))
    ok, detail = cr.write_claim_record(path, payload)
    assert ok, detail
    return path


def test_claimed_segment_refuses_before_adopt_pending_can_promote(tmp_path, monkeypatch):
    """#438 F1: adopt_pending() is a SECOND route that overwrites the canonical
    draft (os.replace(self.pending, self.canonical)), so the D8 claim guard has
    to sit ABOVE it, not merely above launch().

    The state exercised is exactly the one D8 exists for: a CLAIMED segment
    whose draft is missing/invalid, so safe_adopt() fails. A same-run deferred
    attempt is sitting in the pending slot and would pass every candidate gate
    (a CROSS-run one is already refused by the gates' own --expect-token check,
    which is why the reachable case is same-run). With the guard below
    adopt_pending(), that attempt is promoted over the claimed draft and the
    pre_claim_content_sha1 baseline the claim exists to preserve is destroyed.

    `calls == []` is the ORDERING assertion, and the one that makes this test
    about placement rather than outcome: adopt_pending() cannot run a single
    gate without being reached, so an empty call log proves the refusal
    happened first. Move the guard back below adopt_pending() and this test
    reports rc 0 / reason 'adopted-pending' / a promoted canonical / a consumed
    pending / two recorded gate calls -- five independent failures."""
    job = _mkjob(tmp_path, kind="translate", deadline=100, run_id="RUN")
    claim_path = _write_claim(job)
    Path(job.pending).write_text(
        json.dumps({"dispatch_token": job.tok, "seg": job.seg}), encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    monkeypatch.setattr(job, "_gate", gate)
    launched = {"v": False}

    def spy_launch():
        launched["v"] = True
        return True
    monkeypatch.setattr(job, "launch", spy_launch)

    rc = job.run()

    assert rc == 1
    assert job.reason == "claimed-segment-refused"
    assert calls == [], (
        "adopt_pending() ran its candidate gates -- the D8 guard is BELOW it "
        "again, and a same-run deferred attempt can be promoted over a claimed "
        "draft: %r" % (calls,)
    )
    assert os.path.exists(job.pending), "a refused claim must not consume the pending"
    assert not os.path.exists(job.canonical), "the claimed draft's slot must be untouched"
    assert launched["v"] is False
    assert job.seg in job.error_detail and str(claim_path) in job.error_detail


def test_unclaimed_segment_with_a_run_id_still_adopts_a_valid_pending(tmp_path, monkeypatch):
    """The control the test above needs: moving the guard UP must not break the
    #213 adoption path for the ordinary case. Same fixture, same run_id wired in
    -- only the claim record is absent -- and adopt_pending() must still run its
    gates and promote. A guard that refused unconditionally, or one that read
    CLAIM_ABSENT as "cannot rule out a claim", would pass the refusal test above
    for entirely the wrong reason and fails here."""
    job = _mkjob(tmp_path, kind="translate", deadline=100, run_id="RUN")
    Path(job.pending).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    monkeypatch.setattr(job, "_gate", gate)
    launched = {"v": False}

    def spy_launch():
        launched["v"] = True
        return True
    monkeypatch.setattr(job, "launch", spy_launch)

    rc = job.run()

    assert rc == 0
    assert job.reason == "adopted-pending"
    assert calls == ["draft_ready.py", "validate_draft.py"]
    assert launched["v"] is False
    assert os.path.exists(job.canonical)
    assert not os.path.exists(job.pending)


def test_unusable_run_id_refuses_the_translate_rather_than_crashing(tmp_path, monkeypatch):
    """claim_record.claimed_path() now RAISES on a run id it cannot safely turn
    into a path. main() rejects such a value at usage time, but a caller that
    constructs CodexJob() directly can still reach the guard with one -- and the
    guard owns the answer: an unusable run id means the claim state cannot be
    determined AT ALL, which is strictly worse than an unreadable record, so it
    REFUSES. Let the ValueError escape instead and run()'s generic handler turns
    a deliberate refusal into `reason: error: ValueError(...)`, which reads like
    a driver crash; map it to "not claimed" instead and launch() overwrites a
    draft nobody checked the claim state of."""
    job = _mkjob(tmp_path, kind="translate", deadline=100, run_id="../x")
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    monkeypatch.setattr(job, "_gate", gate)
    launched = {"v": False}

    def spy_launch():
        launched["v"] = True
        return True
    monkeypatch.setattr(job, "launch", spy_launch)

    rc = job.run()

    assert rc == 1
    assert job.reason == "claimed-segment-refused", (
        "an unusable run id must be a REFUSAL, not a crash reported as one: %r"
        % (job.reason,)
    )
    assert launched["v"] is False
    assert calls == []
    assert "../x" in job.error_detail and "cannot be ruled out" in job.error_detail


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


# --------------------------------------------------------------------------- #
# #399, second half: safe_adopt() -- the gate that refuses a PRE-EXISTING
# canonical -- records its own output in job.adopt_rejection, a field nothing
# else in the run may overwrite. Not error_detail: safe_adopt() runs first and
# four later stages write error_detail, and a refused adoption is not an error
# of a run that goes on to finish ok.
# --------------------------------------------------------------------------- #
def test_safe_adopt_translate_ready_gate_rejection_captured(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    Path(job.canonical).write_text("{}", encoding="utf-8")
    assert job.adopt_rejection is None   # precondition
    gate, calls = _gate_recorder_with_output({
        "draft_ready.py": (1, "", "[c001] token mismatch: expected RUN:c001"),
    })
    monkeypatch.setattr(job, "_gate", gate)
    assert job.safe_adopt() is False
    assert job.adopt_rejection == "draft_ready.py: [c001] token mismatch: expected RUN:c001"
    assert calls == ["draft_ready.py"]       # quality gate not reached
    assert job.error_detail is None, (
        "an adoption refusal must NOT ride error_detail -- that field belongs to the "
        "stages that run after this one"
    )


def test_safe_adopt_translate_quality_gate_rejection_captured(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    Path(job.canonical).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder_with_output({
        "draft_ready.py": (0, "", ""),
        "validate_draft.py": (1, "[seg66] FAIL (1 defects):\n   - [PARA:seg66:0100] empty translation", ""),
    })
    monkeypatch.setattr(job, "_gate", gate)
    assert job.safe_adopt() is False
    assert job.adopt_rejection == (
        "validate_draft.py: [seg66] FAIL (1 defects):\n   - [PARA:seg66:0100] empty translation"
    )
    assert calls == ["draft_ready.py", "validate_draft.py"]
    assert job.error_detail is None


def test_safe_adopt_review_gate_rejection_captured(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="review")
    Path(job.canonical).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder_with_output({
        "review_ready.py": (1, "[c001] review is stale: draft_sha1 mismatch", ""),
    })
    monkeypatch.setattr(job, "_gate", gate)
    assert job.safe_adopt() is False
    assert job.adopt_rejection == "review_ready.py: [c001] review is stale: draft_sha1 mismatch"
    assert calls == ["review_ready.py"]


def test_safe_adopt_pass_never_captures_despite_loud_gates(tmp_path, monkeypatch):
    """The PASSING gates here print conspicuously on both streams. An
    implementation that captured unconditionally would be invisible to a test
    whose passing gates print nothing (the older helper's default), so the
    noise is what makes this a real mutation check."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.canonical).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder_with_output({
        "draft_ready.py": (0, "[c001] OK: token matches", "warning: 2 notes[] entries"),
        "validate_draft.py": (0, "[c001] OK (0 defects)", "checked 41 blocks"),
    })
    monkeypatch.setattr(job, "_gate", gate)
    assert job.safe_adopt() is True
    assert calls == ["draft_ready.py", "validate_draft.py"]
    assert job.adopt_rejection is None, "a PASSING gate's output must never be captured"
    assert job.error_detail is None


def test_safe_adopt_absent_canonical_captures_nothing(tmp_path, monkeypatch):
    """No canonical is the ORDINARY first-translation path, not a refusal: no gate
    runs, so there is no gate output to attribute and nothing to diagnose. (This
    test deliberately does NOT create the canonical the others do.)"""
    job = _mkjob(tmp_path, kind="translate")
    gate, calls = _gate_recorder_with_output({})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.safe_adopt() is False
    assert calls == []
    assert job.adopt_rejection is None


def test_safe_adopt_unrunnable_gate_captures_nothing(tmp_path, monkeypatch):
    """A gate that could not RUN at all (_gate -> None: exhausted budget, timeout,
    spawn failure) refuses the adoption like any other failure, but there is no
    output to record -- and inventing one would report a refusal that never
    happened."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.canonical).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(job, "_gate", _gate_none)   # the file's own could-not-run stub
    assert job.safe_adopt() is False
    assert job.adopt_rejection is None


def test_safe_adopt_rejection_is_truncated_with_explicit_bound_marker(tmp_path, monkeypatch):
    """The adoption refusal lands in the durable joblog, so it is bounded by the
    same _GATE_OUTPUT_CAP as every other captured gate output."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.canonical).write_text("{}", encoding="utf-8")
    long_output = "X" * (job._GATE_OUTPUT_CAP + 500)
    gate, calls = _gate_recorder_with_output({"draft_ready.py": (1, long_output, "")})
    monkeypatch.setattr(job, "_gate", gate)
    assert job.safe_adopt() is False
    assert job.adopt_rejection is not None
    assert ("... [truncated at %d chars]" % job._GATE_OUTPUT_CAP) in job.adopt_rejection
    assert long_output not in job.adopt_rejection


def test_capture_gate_rejection_empty_output_never_clears_an_existing_detail(tmp_path):
    """A gate that printed NOTHING leaves error_detail exactly as it was. The
    compound path is real: adopt_pending() captures a rejected pending's output,
    run() launches fresh, and the fresh attempt is then rejected by a SILENT gate
    -- clearing here would destroy the one diagnostic the operator has, and the
    empty-output test above cannot see it (it starts from None)."""
    job = _mkjob(tmp_path)
    job.error_detail = "validate_draft.py: [c001] FAIL: dangling FNREF_2"
    job._capture_gate_rejection("draft_ready.py", SimpleNamespace(
        returncode=1, stdout="", stderr=""))
    assert job.error_detail == "validate_draft.py: [c001] FAIL: dangling FNREF_2"


def test_run_adopt_rejection_survives_a_later_launch_failure_into_the_joblog(tmp_path, monkeypatch):
    """END-TO-END: the captured refusal must reach the DURABLE terminal joblog --
    the only record that survives the detached `nohup ... >/dev/null` launch --
    and must still be there after a later stage has written its OWN diagnostic
    into error_detail. safe_adopt() runs for real here; only hygiene() and the
    launch subprocess are stubbed."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    Path(job.canonical).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(job, "hygiene", lambda: None)
    gate, calls = _gate_recorder_with_output({
        "draft_ready.py": (1, "[c001] token mismatch: expected RUN:c001", ""),
    })
    monkeypatch.setattr(job, "_gate", gate)
    # Real launch(), stubbed subprocess: this is what actually sets error_detail
    # (a bare `launch -> False` patch would leave it None and prove nothing).
    monkeypatch.setattr(job, "_run", lambda argv, timeout: SimpleNamespace(
        returncode=1, stdout="", stderr="companion: usage limit reached"))
    assert job.run() == 1
    assert job.reason == "launch-failed"
    assert job.error_detail == "companion: usage limit reached"   # the LATER stage wrote this
    rec = json.loads(Path(job.joblog).read_text(encoding="utf-8"))
    assert rec["status"] == "terminal"
    assert rec["error_detail"] == "companion: usage limit reached"
    assert rec["adopt_rejection"] == "draft_ready.py: [c001] token mismatch: expected RUN:c001", (
        "the adoption refusal must survive every later stage and reach the durable "
        f"joblog, got {rec.get('adopt_rejection')!r}"
    )


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
    """#430: deadline=8 rather than a 2s bound. The launch spawn must finish INSIDE the
    deadline for a jobId to exist to cancel, so a 2s budget makes this assert the
    machine's spawn latency as much as the driver's cancel-on-deadline behaviour. The
    fake node never leaves `running`, so an 8s deadline proves the same property."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["running"], jobId="jobT"),
                        deadline=8, poll=1)
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
    """A status call that sleeps past the per-call cap is KILLED at the poll deadline, and
    the job does not run past deadline+150.

    #430: what proves the clip fired is the fake node's OWN completion marker, not the wall
    clock. FAKE_NODE's status branch bumps `<CJ_STATE>.ctr` only AFTER its sleep returns, and
    spawn_driver unlinks that file before the run -- so the counter exists if and only if a
    status call was allowed to sleep to completion. A wall-clock ceiling cannot carry this:
    it has to sit far enough above the real runtime to survive a loaded machine, and anything
    that loose also admits a status call clipped at the WRONG bound (a cap of 31s against a
    30s sleep runs 30s and lands well inside any ceiling generous enough to be stable). The
    two elapsed assertions below stay, deliberately loose, for the abs_ceiling property only."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    state_file = root / "state.D1.json"
    completed_status_calls = Path(str(state_file) + ".ctr")
    t0 = time.monotonic()
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["running"], status_sleep=30, jobId="jobH"),
                        deadline=8, poll=1)
    elapsed = time.monotonic() - t0
    line = parse_line(proc)
    assert line["timed_out"] is True
    assert not completed_status_calls.exists(), (
        "the hung status call ran to completion -- the driver did not clip the subprocess to "
        "its poll deadline (poll_timeout()), so nothing here bounds a status call that hangs"
    )
    assert elapsed < 8 + codex_job.CODEX_FINALIZE_BUDGET_SEC   # never past abs_ceiling
    assert elapsed < 120                                       # and nowhere near the 30s*N sleep sum


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
REAL_GATE_SCRIPTS = ("codex_job.py", "claim_record.py", "draft_ready.py", "validate_draft.py",
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
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(SCRIPTS_DIR / "json_stdout.py", scripts / "json_stdout.py")
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
    assert job._ensure_staging() is True   # #697: names job.attempt; see its docstring
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
    assert job._ensure_staging() is True   # #697: names job.attempt; see its docstring
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
    configuration, SKILL.md:297-299) and show that `--cwd durable_root` (the OLD design)
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
    assert job._canonical_replaceable(job.abs_remaining) is True


def test_canonical_replaceable_false_when_absent_but_phase_budget_is_exhausted(tmp_path):
    """The ENOENT (genuinely absent) branch used to return True unconditionally,
    without ever consulting remaining_fn() -- so a caller whose phase budget was
    ALREADY exhausted would still get a bare `True` here and spend its os.replace()
    anyway, on the strength of a deadline this method never actually checked. Pins
    that the absent case is bounded by the SAME phase deadline every other branch
    already respects."""
    job = _mkjob(tmp_path)
    assert not os.path.exists(job.canonical), "premise: nothing has created it yet"
    assert job._canonical_replaceable(lambda: -0.5) is False


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
        result = job._canonical_replaceable(job.abs_remaining)
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
    assert job._canonical_replaceable(job.abs_remaining) is False


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

    assert job._canonical_replaceable(job.abs_remaining) is False


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

    assert job._is_regular(str(f), job.abs_remaining) is False


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

    assert job._is_regular(str(f), job.abs_remaining) is True, "fstat()'s own TRUE answer must survive close()'s own failure"


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

    assert job._canonical_replaceable(job.abs_remaining) is False


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

    assert job._is_regular(str(f), job.abs_remaining) is False


def test_is_regular_true_on_genuinely_empty_file(tmp_path):
    """CONTROL for the read fix: os.read() on an empty regular file returns b"" --
    falsy, but NOT an error. A wrong implementation that treats a falsy read result as
    a failure would refuse every legitimately empty canonical; this pins that it must
    not."""
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    job = _mkjob(tmp_path)
    assert job._is_regular(str(f), job.abs_remaining) is True


def test_is_regular_false_when_a_later_read_fails_after_a_successful_prefix(tmp_path, monkeypatch):
    """THE discriminating regression a guard that stops after its FIRST successful read
    cannot see: a regular file that serves a GOOD prefix and then fails partway through --
    real on NFS/FUSE and damaged storage, where a later page or extent can EIO/ESTALE
    even though the file opened, fstat'd, and started reading fine. A guard that reads
    only the first chunk, then stops, answers True here; only draining to EOF -- every
    os.read() call checked, not just the first -- catches it. Faults the SECOND os.read()
    call specifically, letting the first one return real, non-empty bytes, so the loop is
    provably still running when the failure hits."""
    f = tmp_path / "candidate.txt"
    f.write_text("x", encoding="utf-8")
    job = _mkjob(tmp_path)

    calls = {"n": 0}

    def fake_read(fd, n):
        calls["n"] += 1
        if calls["n"] == 1:
            return b"a good prefix"  # a genuine-looking successful read -- non-empty,
            # so the drain loop must continue rather than stop here
        raise OSError(errno.EIO, "Input/output error")  # fails on the NEXT read
    monkeypatch.setattr(os, "read", fake_read)

    assert job._is_regular(str(f), job.abs_remaining) is False
    assert calls["n"] == 2, (
        "premise: a second read must actually have been attempted -- an implementation "
        "that stops checking after its first successful read would never reach it, "
        "and this test would not distinguish the two"
    )


def test_is_regular_false_when_file_exceeds_the_byte_ceiling(tmp_path, monkeypatch):
    """HUGE bound: _MAX_REGULAR_READ_BYTES caps a file whose fstat()-reported st_size
    ALONE already exceeds the ceiling, refused by the upfront st_size check before a
    single byte is read -- called AFTER this process holds the per-segment flock lease,
    so an oversized file must not cost unbounded time to confirm. Shrinks the ceiling
    (real content stays tiny) rather than writing 64 MiB of real bytes to stay fast; a
    vacuous implementation that never checks size at all -- the exact defect this pins --
    would still return True here."""
    f = tmp_path / "huge.txt"
    f.write_bytes(b"x" * 100)
    job = _mkjob(tmp_path)
    monkeypatch.setattr(codex_job, "_MAX_REGULAR_READ_BYTES", 50)

    assert job._is_regular(str(f), job.abs_remaining) is False


def test_is_regular_true_at_exactly_the_byte_ceiling(tmp_path, monkeypatch):
    """CONTROL for the HUGE bound: a file whose size sits exactly AT the ceiling, not
    over it, must still pass -- pins `>`, not `>=`, an off-by-one a careless
    implementation of the check above could introduce."""
    f = tmp_path / "at_ceiling.txt"
    f.write_bytes(b"x" * 50)
    job = _mkjob(tmp_path)
    monkeypatch.setattr(codex_job, "_MAX_REGULAR_READ_BYTES", 50)

    assert job._is_regular(str(f), job.abs_remaining) is True


def test_is_regular_false_when_actual_bytes_read_exceed_the_stale_fstat_snapshot(
    tmp_path, monkeypatch
):
    """GROWING bound: st_size is a snapshot taken once, at fstat() time, and can be
    stale by the time the drain loop finishes -- a file that keeps growing while it is
    being read must be caught by the RUNNING byte counter inside the loop, not by
    re-trusting the (by-then-stale) st_size a second time. A real growing file cannot
    be fabricated portably in a unit test, so this fakes fstat() to report a size
    UNDER the (shrunk) ceiling -- the stale, since-outgrown snapshot, which alone would
    let the file straight through -- while os.read() keeps serving real chunks whose
    CUMULATIVE total exceeds the ceiling. Only an implementation that tracks actual
    bytes read, not just the fstat() snapshot, refuses here."""
    f = tmp_path / "growing.txt"
    f.write_text("irrelevant -- os.fstat/os.read are both faked below", encoding="utf-8")
    job = _mkjob(tmp_path)
    monkeypatch.setattr(codex_job, "_MAX_REGULAR_READ_BYTES", 10)

    real_fstat = os.fstat

    def fake_fstat(fd):
        st = real_fstat(fd)
        # Report a size UNDER the shrunk ceiling -- the stale, since-outgrown
        # snapshot -- so the upfront HUGE check alone would let this through; only
        # the running counter inside the drain loop can catch it from here.
        return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                                st.st_uid, st.st_gid, 1, st.st_atime,
                                st.st_mtime, st.st_ctime))
    monkeypatch.setattr(os, "fstat", fake_fstat)

    calls = {"n": 0}

    def fake_read(fd, n):
        calls["n"] += 1
        if calls["n"] <= 2:
            return b"123456"  # 6 bytes/call -- two calls already exceed the ceiling of 10
        return b""  # never reached if the fix is correct
    monkeypatch.setattr(os, "read", fake_read)

    assert job._is_regular(str(f), job.abs_remaining) is False
    assert calls["n"] == 2, (
        "must refuse the INSTANT the running total exceeds the ceiling, on the very "
        "read call that crosses it -- not after draining further to EOF regardless"
    )


def test_is_regular_false_when_the_phase_budget_is_exhausted_by_the_time_eof_is_reached(
    tmp_path
):
    """Reaching EOF itself takes real wall-clock time (the read that returns b"" still has
    to complete), so a file that consumes the CALLER's entire phase budget confirming EOF
    must not be reported trustworthy just because every byte was eventually read. The
    loop's own per-iteration check never runs again after the break EOF causes, so this
    specifically pins the standalone POST-loop re-check, not the in-loop ones the
    HUGE/GROWING tests above already cover.

    A remaining_fn that returns a POSITIVE budget for the two in-loop calls (one before
    the read that returns real content, one before the read that returns EOF) and only
    goes non-positive on the THIRD call -- the post-loop re-check -- isolates that
    specific check: an implementation missing it would only ever call remaining_fn
    twice, and this test's own call-count assertion would catch that even before the
    boolean result does."""
    f = tmp_path / "candidate.txt"
    f.write_text("x", encoding="utf-8")
    job = _mkjob(tmp_path)

    calls = {"n": 0}

    def fake_remaining():
        calls["n"] += 1
        return -0.5 if calls["n"] >= 3 else 5.0

    assert job._is_regular(str(f), fake_remaining) is False
    assert calls["n"] == 3, (
        "premise: the post-EOF re-check must actually run as its own THIRD call to "
        "remaining_fn -- an implementation missing it would call remaining_fn only "
        "twice (both inside the loop) and never distinguish itself from this test"
    )


def test_adopt_pending_is_bounded_by_the_poll_window_not_the_whole_job_ceiling(
    tmp_path, monkeypatch
):
    """adopt_pending() is a poll-window operation, per its own docstring, and must bound
    its _is_regular()/_canonical_replaceable() calls with self.poll_remaining, never
    self.abs_remaining() -- the WHOLE JOB's ceiling, which extends CODEX_FINALIZE_BUDGET_SEC
    past the poll deadline. Sharing that wider ceiling would let it eat into the 150s
    finalize budget while holding the per-segment lease. Constructs a job whose poll
    window has ALREADY elapsed but whose abs_ceiling is still comfortably positive --
    exactly the gap an abs_remaining()-bounded version would use -- and confirms
    adopt_pending() refuses immediately, never even calling a gate, rather than reading
    self.pending on the strength of the still-positive abs_remaining()."""
    job = _mkjob(tmp_path, kind="translate", deadline=-1)
    assert job.poll_remaining() <= 0, "premise: the poll window has already elapsed"
    assert job.abs_remaining() > 100, "premise: the whole job's ceiling has NOT"
    Path(job.pending).write_text("{}", encoding="utf-8")

    gate_calls = {"n": 0}

    def spy_gate(args, timeout):
        gate_calls["n"] += 1
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(job, "_gate", spy_gate)

    assert job.adopt_pending() is False
    assert gate_calls["n"] == 0, (
        "no gate may run once the POLL window (not the whole job's abs_ceiling) is "
        "exhausted -- adopt_pending() is a poll-window operation, and reading "
        "self.pending itself must already refuse before ever reaching a gate call"
    )
    assert Path(job.pending).read_text(encoding="utf-8") == "{}", (
        "a deadline-exhausted refusal on a GENUINELY regular file must not discard "
        "it -- the same slot survives for a future run with actual budget to retry"
    )


def test_canonical_replaceable_false_when_read_raises_on_the_open_fd(tmp_path, monkeypatch):
    """The same escape one layer up as the fstat-chain test above: let lstat() and
    open()/fstat() all succeed, then fault the read. Proves the refusal reaches
    _canonical_replaceable() too, not just _is_regular() as a standalone unit."""
    job = _mkjob(tmp_path)
    Path(job.canonical).write_text("{}", encoding="utf-8")

    def fake_read(fd, n):
        raise OSError(errno.ESTALE, "Stale file handle")
    monkeypatch.setattr(os, "read", fake_read)

    assert job._canonical_replaceable(job.abs_remaining) is False


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
    candidate must survive: since #697 _teardown_staging() relocates it out of the staging
    directory into segments/ under self.preserved_attempt, because the mkdtemp path it was
    gated in is not somewhere an operator would look. Nothing later revisits either path,
    which is a disclosed limit, not a defect this release closes.

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
    assert os.path.exists(job.preserved_attempt), (
        "os.replace() must never fire when the canonical it would overwrite could not "
        "be observed -- the validated candidate survives instead. #697: it survives in "
        "segments/ under job.preserved_attempt, because the staging directory it was "
        "gated in is a mkdtemp path outside durable_root that no operator would look in"
    )
    assert not os.path.exists(job.attempt)
    assert not os.path.exists(job.staging_dir), "staging is torn down once the bytes moved"


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
    then; do not just delete the test.

    #483 NARROWED WHAT "NEVER OBSERVED" MEANS HERE, without closing this window, and
    this paragraph is that instruction being honoured. The promote now re-reads the
    canonical's `dispatch_token` before the archive/replace pair, so a raced writer that
    MOVES the token is seen and the promotion is refused -- pinned by
    test_a_raced_writer_that_moves_the_token_in_the_check_replace_window_is_refused.
    This test still passes, and still documents a real limit, because its raced writer
    publishes content carrying NO dispatch_token: the authorization did not move, so the
    late check has nothing to compare against and the content race is destroyed exactly
    as before. What remains open is the CONTENT half of the window, which no token
    comparison can close."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)

    real_check = job._canonical_replaceable
    raced_writer_content = '{"marker":"raced-writer-published-this"}'

    def racing_check(remaining_fn):
        result = real_check(remaining_fn)  # the REAL answer, honestly observed
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
    # all. Only adopt_pending()'s OWN observation is faulted.
    #
    # ARMED AT THE SEAM, never by an absolute call count. Counting observations makes
    # this test a hostage to how many any earlier phase happens to make: #483 added one
    # ahead of the preflight, and under the old `n >= 2` rule that faulted the PREFLIGHT
    # instead -- run() then refused one phase too early, every assertion below still
    # held, and the test went on passing while proving nothing about adopt_pending() at
    # all. The gates are the seam: they run inside adopt_pending() and nowhere else, so
    # arming on the last of them fires on exactly the observation this test is about,
    # whatever any future phase adds above it.
    armed = {"v": False}
    real_lstat = os.lstat

    def fake_lstat(path, *a, **kw):
        if armed["v"] and os.fspath(path) == job.canonical:
            raise OSError(errno.EIO, "Input/output error", path)
        return real_lstat(path, *a, **kw)
    monkeypatch.setattr(os, "lstat", fake_lstat)

    real_gate = gate

    def gate_then_arm(argv, timeout):
        proc = real_gate(argv, timeout)
        if argv[0] == "validate_draft.py":     # adopt_pending()'s LAST gate
            armed["v"] = True
        return proc
    monkeypatch.setattr(job, "_gate", gate_then_arm)

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
    assert calls == ["draft_ready.py", "validate_draft.py"], (
        "adopt_pending() must have RUN its gates -- otherwise the refusal came from an "
        "earlier phase and nothing below is about this call site: %r" % (calls,)
    )
    assert job.reason == "canonical-unreadable"
    assert job.canonical_unreadable is True
    assert os.path.exists(job.pending), "the validated pending candidate must survive, untouched"
    assert Path(job.pending).read_text(encoding="utf-8") == "{}"


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
        result = job._canonical_replaceable(job.abs_remaining)
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
    assert os.path.exists(job.preserved_attempt), (
        "os.replace() must never fire when the canonical it would overwrite could not "
        "be observed -- the validated candidate survives in segments/ under "
        "job.preserved_attempt (#697), even when the diagnostic write that reaches this "
        "refusal itself fails with a type nothing in codex_job.py specifically enumerates"
    )
    assert not os.path.exists(job.attempt)
    assert not os.path.exists(job.staging_dir)



# ---- #429: a deferral must not destroy the pending slot's occupant -----------
#
# _defer_attempt() used to os.replace() the fresh attempt straight onto the slot, so a
# previously validated candidate that had merely gone UNREADABLE between runs was destroyed
# by the next ordinary no-budget completion (adopt_pending() refuses such an occupant at its
# first _is_regular() check and _clear_nonregular() deliberately leaves regular inodes alone,
# so nothing else touched it). The fix LINKS the occupant to a second name before replacing:
# a link adds a name and never removes one, so the slot itself is never vacated and
# os.replace() stays the only mutation of it.


def _superseded_links(job):
    """Every superseded link this job's segments dir currently holds."""
    return sorted(Path(job.segdir).glob(".att_superseded.*"))


def _fault_pending_link(monkeypatch, job, exc):
    """Fault ONLY the pending -> superseded link, so the failure under test is attributable to
    that one call and stays so if a second os.link caller is ever added. The narrowing matters
    here in a way a blanket patch cannot be trusted to: _publish_from_sandbox() runs FIRST and
    relocates the candidate itself, so faulting a primitive it also uses (its own fd-pinned
    os.rename()) makes PUBLICATION fail instead -- the function then returns False with the
    occupant untouched, satisfying every assertion below without ever reaching the branch under
    test. Publication does not call os.link, so only the link below is affected."""
    real = os.link

    def faulting(src, dst, *a, **kw):
        if src == job.pending:
            raise exc
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "link", faulting)


def test_defer_preserves_superseded_pending(tmp_path):
    """#429 RED-before-green: the displaced occupant's bytes must survive the deferral."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text(json.dumps({"marker": "OLD"}), encoding="utf-8")
    _seed_sandbox(tmp_path, job, content=json.dumps({"marker": "NEW"}))
    assert job._defer_attempt() is True
    assert json.loads(Path(job.pending).read_text())["marker"] == "NEW"
    superseded = _superseded_links(job)
    assert len(superseded) == 1, superseded          # exactly one, not zero and not a pile
    assert json.loads(superseded[0].read_text())["marker"] == "OLD"


def _load_scanner(module_name, filename):
    """Load a sibling script's REAL scan_dispatching_run_ids(). Driving the shipped function
    is the point: an earlier version of the test below re-implemented each scan's filter by
    hand, which froze a copy of code this module does not own -- and #428 then changed both
    scans from a `.draft.json` suffix test to skipping the whole dot-prefixed namespace,
    which a hand-copied filter would have gone on passing against forever."""
    spec = importlib.util.spec_from_file_location(module_name, str(SCRIPTS_DIR / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.scan_dispatching_run_ids


def test_defer_superseded_is_invisible_to_both_real_dispatch_scans(tmp_path):
    """The preserved copy must not be read as a real segment draft by either scan that
    enumerates segments/. Both are CALLED here, not imitated. The existence assertion is
    load-bearing: without it every assertion below passes vacuously on code that creates no
    preserved copy at all."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text(json.dumps({"marker": "OLD"}), encoding="utf-8")
    _seed_sandbox(tmp_path, job, content=json.dumps({"marker": "NEW"}))
    assert job._defer_attempt() is True
    links = _superseded_links(job)
    assert len(links) == 1                          # it EXISTS, so the rest is not vacuous

    segdir = Path(job.segdir)
    # A real canonical draft, so each scan has something it MUST still count -- otherwise a
    # scan that counted nothing at all would satisfy the assertions below.
    (segdir / "c001.draft.json").write_text(
        json.dumps({"dispatch_token": "RUN:c001"}), encoding="utf-8")

    for name, filename in (("select_segments", "select_segments.py"),
                           ("backfill", "backfill_resume_gate_ack.py")):
        scan = _load_scanner("scan_%s_mod" % name, filename)
        result = scan(segdir)
        assert result["drafts_scanned"] == 1, (name, result)   # the canonical one, and only it
        assert list(result["by_run_id"]) == ["RUN"], (name, result)
        attributed = result["by_run_id"]["RUN"]
        assert len(attributed) == 1, (name, result)            # not duplicated by the copy
        assert not any(links[0].name in str(e) for e in attributed), (name, result)


def test_defer_without_occupant_leaves_no_superseded_link(tmp_path):
    """The ordinary path -- an empty slot -- must create no garbage and must still defer."""
    job = _mkjob(tmp_path, kind="translate")
    assert not os.path.exists(job.pending)
    _seed_sandbox(tmp_path, job, content=json.dumps({"marker": "NEW"}))
    assert job._defer_attempt() is True                 # FileNotFoundError -> nothing to preserve
    assert json.loads(Path(job.pending).read_text())["marker"] == "NEW"
    assert _superseded_links(job) == []


@pytest.mark.parametrize("exc", [PermissionError(13, "denied"), NotImplementedError()])
def test_defer_refuses_when_occupant_cannot_be_preserved(tmp_path, monkeypatch, exc):
    """Could-not-preserve REFUSES rather than destroying. Refusing sacrifices the fresh
    candidate, which is unvalidated by construction here (the defer fires precisely because no
    budget remained to gate it) -- the less-established artifact, which is the correct trade at
    THIS site. NotImplementedError is parametrized because it is what an unsupported
    follow_symlinks raises and it is NOT an OSError: a handler catching OSError alone lets it
    escape to run()'s generic catch with no error_detail at all."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text(json.dumps({"marker": "OLD"}), encoding="utf-8")
    _seed_sandbox(tmp_path, job, content=json.dumps({"marker": "NEW"}))
    _fault_pending_link(monkeypatch, job, exc)
    assert job._defer_attempt() is False
    assert os.path.exists(job.attempt)                  # publication DID happen: the fault
    assert json.loads(Path(job.attempt).read_text())["marker"] == "NEW"   # reached the branch
    assert json.loads(Path(job.pending).read_text())["marker"] == "OLD"   # occupant intact
    assert _superseded_links(job) == []
    assert job.error_detail                             # the refusal is diagnosable, not opaque


def test_defer_leaves_occupant_in_slot_when_replace_fails(tmp_path, monkeypatch):
    """The link is ADDITIVE, so a failing os.replace() after it leaves the occupant exactly
    where a later run looks -- no restore branch does this, and a rename-based preserve would
    have left the slot EMPTY with both candidates unreachable."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text(json.dumps({"marker": "OLD"}), encoding="utf-8")
    _seed_sandbox(tmp_path, job, content=json.dumps({"marker": "NEW"}))

    def boom(src, dst):
        raise OSError(5, "EIO")
    monkeypatch.setattr(os, "replace", boom)

    assert job._defer_attempt() is False
    assert json.loads(Path(job.pending).read_text())["marker"] == "OLD"   # STILL in the slot
    superseded = _superseded_links(job)
    assert len(superseded) == 1
    assert json.loads(superseded[0].read_text())["marker"] == "OLD"
    assert job.error_detail


def test_defer_superseded_name_carries_kind_and_seg(tmp_path):
    """Hand recovery cannot read identity out of the payload: the candidate is UNVALIDATED at
    defer time, and review.schema.json has no `seg` property at all. The NAME therefore has to
    carry both, and `inv` keeps a second deferral of the same segment from overwriting the
    first."""
    job = _mkjob(tmp_path, kind="review", seg="c042")
    Path(job.pending).write_text(json.dumps({"marker": "OLD"}), encoding="utf-8")
    _seed_sandbox(tmp_path, job, content=json.dumps({"marker": "NEW"}))
    assert job._defer_attempt() is True
    name = _superseded_links(job)[0].name
    assert name == ".att_superseded.review.c042.%s" % job.inv


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# =========================================================================== #
# #398 -- a gate-REJECTED translate must not be auto-redispatched forever.
#
# codex_job.py is the only component BOTH dispatch paths share: the Workflow
# template has no filesystem access and launches this script with stdout
# discarded, and segment_dispatch_driver.py sees only the `reason` string,
# which collapses a content rejection, a sandbox-publish failure, a
# non-regular attempt and a gate that could not run into one spelling. So the
# terminal ledger write lives here, keyed on validate_draft.py's exit 1 -- its
# contract for "the CANDIDATE's content is defective" -- and on nothing else.
#
# These tests are deliberately lopsided: ONE positive, and a table of
# negatives. The expensive mistake is not failing to write the fragment; it is
# writing it for a mechanical failure, which turns a transient hiccup into a
# segment an operator must rescue by hand.
# =========================================================================== #
def test_content_gate_exit_1_writes_a_terminal_blocked_ledger_fragment(tmp_path):
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="invalid_quality",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False

    fragment = read_fragment(root, seg)
    assert fragment["status"] == "blocked", fragment
    assert fragment["reason"] == "translate-rejected", fragment
    assert fragment["timestamp"]                       # written by the real ledger_update.py
    # The payload carries no `detail`/`error_detail`: ledger_update.py validates against a
    # schema derived with additionalProperties:false, so an extra key would have made this
    # write FAIL rather than carry more information.
    assert set(fragment) <= {"timestamp", "status", "reason"}, fragment

    # The child's OWN report is unchanged -- the driver and every existing test still read
    # `validate-failed`. What #398 adds is the durable consequence, not a new label.
    jl = json.loads((root / "segments" / (".codex_job.%s.json" % seg)).read_text())
    assert line["reason"] == "validate-failed"
    assert jl["reason"] == "validate-failed"
    assert jl["ledger_write"] == "ok"

    # The scratch payload is cleaned up, and would have been inert anyway (dot-prefixed).
    leftovers = list((root / "segments").glob(".codex_ledger_payload.*"))
    assert leftovers == [], leftovers


@pytest.mark.parametrize("label,kind,expected_reason,state_kw", [
    # validate_draft.py RAN but reported usage/environment/source-availability, not a
    # verdict on the candidate. This is the row that makes exit 1 a discriminator rather
    # than "any non-zero".
    ("validator_environment_failure", "translate", "validate-failed",
     dict(attempt_mode="validator_env_failure", status_seq=["completed"])),
    # draft_ready.py rejects first and returns before validate_draft.py runs at all.
    ("draft_ready_rejection", "translate", "validate-failed",
     dict(attempt_mode="invalid_token", status_seq=["completed"])),
    # Nothing was produced to validate.
    ("no_attempt_written", "translate", "validate-failed",
     dict(attempt_mode="none", status_seq=["completed"])),
    # A non-regular sandbox output: refused by the publish primitive, before any gate.
    ("non_regular_attempt", "translate", "validate-failed",
     dict(attempt_mode="symlink", status_seq=["completed"])),
    # The job itself failed -- no candidate, no gate, nothing about content.
    ("job_failed", "translate", "job-failed",
     dict(attempt_mode="none", status_seq=["failed"])),
    # The dispatch never launched.
    ("launch_failed", "translate", "launch-failed",
     dict(attempt_mode="none", task_returncode=1)),
    # A rejected REVIEW candidate is explicitly out of scope: the fragment belongs to the
    # translate stage, and blocking a segment over a review candidate would strand work
    # that already has a good draft.
    ("review_kind_rejection", "review", "validate-failed",
     dict(attempt_mode="invalid_token", status_seq=["completed"])),
])
def test_no_ledger_fragment_for_any_non_content_failure(
        tmp_path, label, kind, expected_reason, state_kw):
    """The two-sided half of #398, and the one that would catch the tempting wrong
    implementation: keying the write on `reason == "validate-failed"` instead of on the
    content-gate flag. FIVE of these seven rows end with that reason -- every translate row
    except job_failed and launch_failed, plus the review row -- so such an implementation
    writes a fragment in five of them and goes red five times. The other two are not
    exempt by accident: run() reports `job-failed` for a failed job and `launch-failed`
    for a failed dispatch, so those rows guard a different mistake.

    Each row also asserts the reason it actually reached, so a row cannot quietly pass by
    failing EARLIER than its advertised path and never exercising it -- absence of a
    fragment is not evidence when the run never got where the row says it went.

    build_root() stages the REAL ledger_update.py and its schemas, so "no fragment" cannot
    pass merely because the writer was missing."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, kind, "D1",
                        base_state(seg, tok, kind, **state_kw))

    assert parse_line(proc)["reason"] == expected_reason, (
        f"{label}: this row must reach its advertised failure path"
    )
    assert not fragment_path(root, seg).exists(), (
        f"{label}: a non-content failure must leave the segment recoverable, but a terminal "
        f"blocked fragment was written"
    )
    # An ordinary job never attempted a write, so its joblog shape is unchanged -- the new
    # key is present only when there is something to report.
    jl = json.loads((root / "segments" / (".codex_job.%s.json" % seg)).read_text())
    assert "ledger_write" not in jl, jl


def test_a_gate_that_could_not_run_does_not_set_the_content_rejection_flag(tmp_path, monkeypatch):
    """`proc is None` is _run()'s own no-budget / timeout / spawn-fail contract. It is NOT a
    verdict, and it is the one shape the subprocess bed above cannot produce on demand."""
    job = _mkjob(tmp_path, kind="translate")
    _seed_sandbox(tmp_path, job)
    monkeypatch.setattr(job, "_gate", _gate_none)

    assert job.validate_attempt() is False
    assert job.translate_content_rejected is False


def test_validate_draft_exit_2_does_not_set_the_content_rejection_flag(tmp_path, monkeypatch):
    """Directly at the boundary: the gate RAN, returned non-zero, and still must not be read
    as a content verdict, because 2 is validate_draft.py's environment code."""
    job = _mkjob(tmp_path, kind="translate")
    _seed_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 2})
    monkeypatch.setattr(job, "_gate", gate)

    assert job.validate_attempt() is False
    assert calls == ["draft_ready.py", "validate_draft.py"]
    assert job.translate_content_rejected is False


def test_validate_draft_exit_1_sets_the_content_rejection_flag(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="translate")
    _seed_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 1})
    monkeypatch.setattr(job, "_gate", gate)

    assert job.validate_attempt() is False
    assert job.translate_content_rejected is True


def test_a_rejected_review_candidate_never_sets_the_content_rejection_flag(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, kind="review")
    _seed_sandbox(tmp_path, job)
    gate, _calls = _gate_recorder({"review_ready.py": 1})
    monkeypatch.setattr(job, "_gate", gate)

    assert job.validate_attempt() is False
    assert job.translate_content_rejected is False


def test_ledger_write_uses_a_fixed_timeout_not_the_finalize_budget(tmp_path, monkeypatch):
    """By the time a rejection is known, finalize_timeout() can legitimately be 0.0 -- and
    _run() SKIPS a subprocess whose timeout is non-positive, which would turn a genuine
    rejection into no write at all, silently. The write therefore carries its own fixed
    bound. This asserts the VALUE reaching _gate(), not merely that a file appeared: a
    remaining-budget implementation can still pass on a fast machine."""
    job = _mkjob(tmp_path, kind="translate")
    monkeypatch.setattr(job, "finalize_timeout", lambda: 0.0)
    seen = []

    def _gate(args, timeout):
        seen.append((args[0], timeout))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(job, "_gate", _gate)
    job._record_translate_rejected()

    assert seen == [("ledger_update.py", codex_job.CodexJob._LEDGER_WRITE_TIMEOUT_SEC)]
    assert job.ledger_write == "ok"


def test_ledger_update_is_registered_for_durable_root_forwarding(tmp_path):
    """Without this registration _gate() passes no --durable-root, and ledger_update.py
    self-anchors to its own installation tree -- writing the fragment under the PLUGIN root
    on exactly the launches production makes. The argv shape is asserted, not just the
    membership, so a rename of the flag is caught too."""
    job = _mkjob(tmp_path, kind="translate")
    assert "ledger_update.py" in codex_job.CodexJob._DURABLE_ROOT_CONTRACT_SCRIPTS
    assert job._durable_root_args("ledger_update.py") == ["--durable-root", job.root]


def test_a_failing_ledger_write_does_not_change_the_job_outcome(tmp_path):
    """The write is bookkeeping; the job's own report is the contract. A writer that exits
    non-zero must leave exit code, stdout line and joblog reason byte-identical to the
    successful case -- the segment simply stays in its pre-#398 recoverable state, which is
    a return to the old behaviour, never a new failure mode."""
    root, companion, node = build_root(tmp_path)
    # STDOUT, matching ledger_update.py's own emit_failure(): a stub that failed on stderr
    # would bake in the stream-preference mistake _gate_rejection_text() (#399) avoids.
    (root / "scripts" / "ledger_update.py").write_text(
        "#!/usr/bin/env python3\nimport sys\nprint('{\"success\": false, \"error\": \"boom\"}')"
        "\nsys.exit(3)\n",
        encoding="utf-8")
    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="invalid_quality",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False
    assert line["reason"] == "validate-failed"

    jl = json.loads((root / "segments" / (".codex_job.%s.json" % seg)).read_text())
    assert jl["reason"] == "validate-failed"
    # "not-confirmed", never "failed": ledger_update.py commits at an os.replace() and can
    # still fail AFTER that, so a non-zero exit does not prove the fragment is absent.
    assert jl["ledger_write"].startswith("not-confirmed:"), jl["ledger_write"]
    # The writer's own structured error text is what lands there -- not the empty stderr a
    # stderr-first harvest would have picked up.
    assert "boom" in jl["ledger_write"], jl["ledger_write"]
    assert not fragment_path(root, seg).exists()
    assert list((root / "segments").glob(".codex_ledger_payload.*")) == []


def test_fragment_lands_under_the_durable_root_not_the_plugin_root(tmp_path):
    """The one end-to-end test with PHYSICALLY SEPARATE roots. Every other fixture in this
    file stages the writer beside the data, so ledger_update.py's own self-anchoring lands
    in the right place whether or not --durable-root was forwarded -- i.e. they would pass
    with the registration removed. This one cannot."""
    root, companion, node = build_root(tmp_path)
    plugin_root = tmp_path / "plugin_install"
    plugin_scripts = plugin_root / "assets" / "scripts"
    plugin_scripts.mkdir(parents=True)
    for name in ("draft_ready.py", "validate_draft.py", "review_ready.py", "draft_sha1.py"):
        shutil.copy2(root / "scripts" / name, plugin_scripts / name)
    # The writer and its schemas live under the PLUGIN root's own layout; the data root
    # keeps only runs/, where the fragment must land.
    shutil.copy2(LEDGER_WRITER_SRC, plugin_scripts / "ledger_update.py")
    plugin_schemas = plugin_root / "assets" / "schemas"
    plugin_schemas.mkdir(parents=True)
    for name in LEDGER_SCHEMAS:
        shutil.copy2(SCHEMAS_SRC_DIR / name, plugin_schemas / name)
    (root / "scripts" / "ledger_update.py").unlink()

    seg, tok = "c001", "RUN:c001"
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="invalid_quality",
                                   status_seq=["completed"]),
                        extra_args=["--plugin-root", str(plugin_root)])
    assert proc.returncode == 1

    fragment = read_fragment(root, seg)
    assert fragment["status"] == "blocked" and fragment["reason"] == "translate-rejected"
    assert not (plugin_root / "runs").exists(), (
        "the fragment must never land under the plugin installation tree"
    )


# =========================================================================== #
# #665 -- a DEFERRED candidate the gate later rejects on CONTENT must not be
# silently replaced by a fresh paid job.
#
# #398 closed the direct route (a fresh attempt rejected in validate_attempt())
# and deliberately left this one: adopt_pending() returned a bare False for
# BOTH "the pending's cross-run token is stale" and "the pending's content is
# defective", run() could not tell them apart, and fell through to launch() --
# spending a full translation the gate has already permanently refused.
#
# The split is decided by WHICH gate rejected, and adopt_pending()'s gate ORDER
# is what makes that readable: _adoption_gates() runs draft_ready.py
# --expect-token FIRST and the loop returns on its rejection, so reaching
# validate_draft.py at all proves the pending's own dispatch_token already
# matched THIS run. A validate_draft.py exit 1 there is therefore a same-token
# verdict on content, never a stale-token one.
#
# Same lopsided shape as #398's own block: the expensive mistake is not missing
# a rejection, it is blocking a segment whose gate merely ran out of budget or
# whose token was simply stale.
# =========================================================================== #
def test_adopt_pending_content_rejection_sets_the_flag_and_discards(tmp_path, monkeypatch):
    """validate_draft.py exit 1 against a pending whose token already passed draft_ready.py
    -> the flag run() acts terminally on is set, and the pending is discarded exactly as
    before (its bytes are defective by the very gate that blocks the segment; the gate's own
    output is already captured into error_detail by #399)."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder_with_output({
        "draft_ready.py": (0, "[c001] READY", ""),
        "validate_draft.py": (1, "[c001] FAIL (quality)", ""),
    })
    monkeypatch.setattr(job, "_gate", gate)
    assert job.adopt_pending() is False
    assert calls == ["draft_ready.py", "validate_draft.py"]
    assert job.translate_content_rejected is True
    assert not os.path.exists(job.pending)
    assert not os.path.exists(job.canonical)
    assert job.error_detail == "validate_draft.py: [c001] FAIL (quality)"


@pytest.mark.parametrize("label,kind,results,expected_calls", [
    # validate_draft.py RAN but reported usage/environment/source availability, not a verdict
    # on the candidate. This is the row that makes exit 1 a discriminator rather than "any
    # non-zero" -- and it asserts the DISCARD too, because an implementation that starts
    # preserving an exit-2 pending has changed today's slot behaviour while still passing
    # every flag assertion.
    ("validate_draft_exit_2", "translate",
     {"draft_ready.py": (0, "", ""), "validate_draft.py": (2, "", "ERROR: no segpack")},
     ["draft_ready.py", "validate_draft.py"]),
    # The stale-cross-run-token case, which is what the pending slot exists to survive:
    # draft_ready.py rejects and validate_draft.py never runs at all.
    # This row is also what pins the GATE-NAME half of the discriminator: draft_ready.py
    # rejects with exit 1, the very code validate_draft.py's contract reserves for a content
    # verdict, so an implementation that dropped the name conjunct and read "exit 1 from any
    # gate" would terminally block a segment over a stale token and go red here.
    ("draft_ready_rejection", "translate",
     {"draft_ready.py": (1, "not ready: token", "")},
     ["draft_ready.py"]),
    # A review candidate never reaches validate_draft.py in the first place; blocking a
    # segment over a review pending would strand work that already has a good draft.
    ("review_kind_rejection", "review",
     {"review_ready.py": (1, '{"ready": false}', "")},
     ["review_ready.py"]),
])
def test_adopt_pending_non_content_rejection_leaves_the_flag_false(
        tmp_path, monkeypatch, label, kind, results, expected_calls):
    """The two-sided half. Each row also pins the exact call sequence, so a row cannot pass by
    rejecting EARLIER than it advertises and never exercising the gate it is named for."""
    job = _mkjob(tmp_path, kind=kind)
    Path(job.pending).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder_with_output(results)
    monkeypatch.setattr(job, "_gate", gate)
    assert job.adopt_pending() is False
    assert calls == expected_calls, "%s: this row never reached its advertised gate" % label
    assert job.translate_content_rejected is False, (
        "%s: a non-content rejection must not arm the terminal write" % label
    )
    assert not os.path.exists(job.pending), (
        "%s: a gate that RAN and rejected still discards the pending -- unchanged by #665"
        % label
    )


@pytest.mark.parametrize("race", ["unlink", "overwrite", "aba"])
def test_a_concurrent_write_to_the_pending_cannot_change_what_the_gates_judge(
        tmp_path, monkeypatch, race):
    """The terminal verdict must not rest on the DETERMINISTIC cross-run slot.

    `self.pending` is a DETERMINISTIC name that persists across runs, and every gate
    re-OPENS its --candidate-file BY PATH, so gating that name directly means two
    independent opens with a writable window between them. validate_draft.py answers a
    MISSING OR MALFORMED candidate with exit 1, the same code its contract reserves for a
    content verdict, so a write landing in that window is indistinguishable from one -- and
    since #665 acts on it TERMINALLY, the segment lands `blocked`, classifies
    human_escalation, and needs an explicit --only-segs to be retried at all (terminal by
    default, not unrecoverable -- #697 corrected an earlier "permanently" here).

    WHO can write it: #409 excludes EXACTLY ONE actor, the codex process this driver
    launches, which runs in a mkdtemp sandbox _setup_sandbox() refuses to dispatch into
    unless _sandbox_is_confined() proves it standalone. It excludes nothing else. #697
    replaced the roster that used to stand here -- operator's hand, second dispatcher,
    pre-#409 straggler -- because three review rounds each found a writer it had missed;
    codex_job.py's module header now states the PROPERTY instead: anything that can list
    segments/ discovers these names, anything that can write it can overwrite them. This
    test does not depend on which actor: it simulates the write at the seam.

    SCOPE, and read this before trusting it (#697). These three rows pin isolation from
    `self.pending` ONLY. The race callback below writes `job.pending`, never the `.att.*`
    snapshot the gates are actually handed, so nothing here attacks that snapshot -- and
    the snapshot the gates are actually handed. That snapshot is no longer reachable the
    way this test's callback works either: since #697 it stages outside durable_root, which
    test_the_gated_snapshot_is_not_discoverable_by_listing_segments below pins.

    The three rows are a history of guards that did not hold, kept because each still
    describes a real concurrent write and the design has to survive all three:
      * `unlink`    -- caught by any re-check at all;
      * `overwrite` -- still a perfectly regular readable file, only its bytes changed, so a
                       type-only re-check passes it through;
      * `aba`       -- truncate, let the validator read the partial JSON, then RESTORE the
                       original bytes before returning. A before/after digest samples the
                       same value at both ends and passes. Not adversarial and not a hash
                       collision: an ordinary truncate-and-rewrite does it.
    The first two were reproduced against by the MR reviewer. What holds is not a third
    guard but a different artifact: the gates judge a per-invocation snapshot carrying
    os.urandom(8) in its name, so this test asserts the strongest available property --
    every gate is handed a path that is NOT self.pending, and the bytes at that path are
    the ORIGINAL ones no matter what the row does to the slot."""
    job = _mkjob(tmp_path, kind="translate")
    original = json.dumps({"draft": "the bytes a prior run deferred"})
    Path(job.pending).write_text(original, encoding="utf-8")
    seen = []

    def racing_gate(args, timeout):
        candidate = args[args.index("--candidate-file") + 1]
        seen.append((args[0], candidate, Path(candidate).read_text(encoding="utf-8")))
        if args[0] == "validate_draft.py":
            if race == "unlink":
                os.unlink(job.pending)                                    # straggler removes it
            else:
                Path(job.pending).write_text("{", encoding="utf-8")       # ... or truncates it
                if race == "aba":
                    # ... and finishes rewriting it before the validator's process exits
                    Path(job.pending).write_text(original, encoding="utf-8")
            return SimpleNamespace(returncode=1, stdout="FAIL: candidate missing", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(job, "_gate", racing_gate)

    assert job.adopt_pending() is False
    assert [g for g, _, _ in seen] == ["draft_ready.py", "validate_draft.py"]
    for gate, candidate, content in seen:
        assert candidate != job.pending, (
            "%s: %s was pointed straight at the deterministic slot, so a straggler write "
            "between the two gate opens decides a TERMINAL verdict" % (race, gate)
        )
        assert content == original, (
            "%s: %s judged bytes that are not the ones this run snapshotted" % (race, gate)
        )
    # The straggler's writes went to the slot and changed nothing that was judged, so the
    # gate's exit 1 IS a genuine content verdict on the snapshot and stays terminal.
    assert job.translate_content_rejected is True
    assert not os.path.exists(job.attempt), (
        "the snapshot is this invocation's to clean up -- lifecycle ownership, not exclusive "
        "access; see codex_job.py's module header (#697)"
    )


def test_the_gated_snapshot_is_not_discoverable_by_listing_segments(tmp_path, monkeypatch):
    """#697 CLOSED. This REPLACES the known-limit test that pinned it open, as that test's
    own docstring instructed -- it asserted the defect (a foreign write at the gating seam
    decided a terminal verdict), and it was written to go RED on exactly this relocation.

    What changed: the gated candidate no longer lives in segments/. It stages in a
    per-invocation mkdtemp directory beside durable_root, so the discovery move a foreign
    writer must actually make -- LIST the directory it is pointed at and overwrite what it
    finds -- returns nothing. The stub below performs that move rather than reading the path
    it was handed, which is the whole point: taking args[--candidate-file] would corrupt the
    relocated path too and this test would pass while proving nothing about enumerability.

    SCOPE: exactly ONE channel is closed, and this test pins that one. What the relocation
    does NOT buy is stated once, in codex_job.py's module header -- read it there rather
    than trusting a restatement here, which is exactly how #697's prose drifted before."""
    job = _mkjob(tmp_path, kind="translate")
    original = json.dumps({"draft": "the bytes a prior run deferred"})
    Path(job.pending).write_text(original, encoding="utf-8")
    job.canonical_authority = job._canonical_authority(job.poll_remaining)
    assert job.canonical_authority == (True, None), "premise: absent reads as no token"
    seen = {}

    def racing_gate(args, timeout):
        if args[0] != "validate_draft.py":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        handed = args[args.index("--candidate-file") + 1]
        seen["handed"] = handed
        # ENUMERATE, exactly as the replaced test did. This is the residual's own move.
        seen["found"] = sorted(
            p.name for p in Path(job.segdir).glob(".att.%s.*.json" % job.seg))
        seen["before"] = Path(handed).read_text(encoding="utf-8")
        # The writer has nothing to overwrite, so its write lands on the name the OLD
        # layout used -- job.preserved_attempt is byte-identical to the string self.attempt
        # carried before #697. Doing the write for real is what keeps the final assertion
        # from being satisfiable by a stub that merely returned success.
        decoy = Path(job.preserved_attempt)
        decoy.write_text("{", encoding="utf-8")
        seen["decoy"] = str(decoy)
        seen["after"] = Path(handed).read_text(encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(job, "_gate", racing_gate)

    assert job.adopt_pending() is True

    # (a) nothing to find by listing segments/ -- the closed channel, in one assertion.
    assert seen["found"] == [], (
        "a .att.* candidate is discoverable by listing segments/ again; #697's relocation "
        "has been undone: %r" % (seen["found"],)
    )
    # (b) the foreign write really happened, and (c) it did not reach the judged bytes.
    assert Path(seen["decoy"]).read_text(encoding="utf-8") == "{"
    assert seen["decoy"] != seen["handed"]
    assert seen["before"] == original
    assert seen["after"] == original, "the bytes the gate judged were mutated underneath it"
    # (d) the judged artifact is outside the durable root entirely.
    assert not seen["handed"].startswith(job.root + os.sep), (
        "the gated candidate is still inside durable_root: %s" % seen["handed"]
    )
    # (e) ... so this is an ordinary PASS, not a verdict manufactured by a foreign write.
    assert job.translate_content_rejected is False
    assert json.loads(Path(job.canonical).read_text(encoding="utf-8")) == json.loads(original)
    assert not os.path.exists(job.pending)


def test_a_pending_that_cannot_be_snapshotted_is_recoverable(tmp_path, monkeypatch):
    """A snapshot that cannot be taken -- unreadable, non-regular, or a writer still mutating
    it underneath the fd-pinned read (_publish_from_sandbox refuses all three) -- means
    NOTHING was judged. So nothing is discarded and no gate runs: the pending survives and
    the run launches fresh, exactly as it does when a gate could not run."""
    job = _mkjob(tmp_path, kind="translate")
    Path(job.pending).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)
    monkeypatch.setattr(job, "_publish_from_sandbox", lambda src, dst: False)

    assert job.adopt_pending() is False
    assert calls == [], "no gate may run against an artifact that was never snapshotted"
    assert job.translate_content_rejected is False
    assert os.path.exists(job.pending), "unjudged work is recoverable, never discarded"
    assert not os.path.exists(job.canonical)


def test_run_stops_before_launch_on_a_content_rejected_pending(tmp_path, monkeypatch):
    """The behaviour #665 is filed for, at run() level: no fresh codex turn is launched, and
    the terminal ledger write is taken. On the pre-#665 driver launch() IS reached."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    Path(job.pending).write_text("{}", encoding="utf-8")
    gate, calls = _gate_recorder_with_output({
        "draft_ready.py": (0, "", ""),
        "validate_draft.py": (1, "[c001] FAIL (quality)", ""),
    })
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    monkeypatch.setattr(job, "_gate", gate)
    recorded = {"v": False}

    def spy_record():
        recorded["v"] = True
    monkeypatch.setattr(job, "_record_translate_rejected", spy_record)
    launched = {"v": False}

    def spy_launch():
        launched["v"] = True
        return True
    monkeypatch.setattr(job, "launch", spy_launch)

    rc = job.run()

    assert rc == 1
    assert launched["v"] is False, "a content-rejected pending must not buy a fresh turn"
    assert recorded["v"] is True
    assert job.reason == "pending-rejected"


def test_run_still_launches_when_the_pending_gate_could_not_run(tmp_path, monkeypatch):
    """The control for the test above, driving the REAL adopt_pending() rather than
    monkeypatching it away (which is what every existing launch-fallthrough test in this file
    does, and why none of them can catch this): a gate that could not run at all leaves the
    pending intact, launches fresh, and takes no terminal write."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    Path(job.pending).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    monkeypatch.setattr(job, "_gate", _gate_none)
    recorded = {"v": False}

    def spy_record():
        recorded["v"] = True
    monkeypatch.setattr(job, "_record_translate_rejected", spy_record)
    launched = {"v": False}

    def spy_launch():
        launched["v"] = True
        return False                 # stop right after: this test is about REACHING launch()
    monkeypatch.setattr(job, "launch", spy_launch)

    rc = job.run()

    assert rc == 1
    assert launched["v"] is True
    assert recorded["v"] is False
    assert job.reason == "launch-failed"
    assert os.path.exists(job.pending), "an unvalidatable pending is recoverable work"


def _seed_pending(root, seg, payload):
    """Write the deterministic per-seg/kind pending slot a PRIOR run's _defer_attempt() would
    have left behind -- the state #665 is about. The name is codex_job.py's own
    `.att_pending.<seg>.<ext>.json`; nothing in hygiene(), safe_adopt() or the D8 claim guard
    touches it before adopt_pending() runs."""
    path = root / "segments" / (".att_pending.%s.draft.json" % seg)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_deferred_pending_rejected_on_content_is_not_replaced_by_a_fresh_job(tmp_path):
    """End to end, through the shipped driver and the REAL ledger_update.py: a deferred
    candidate whose content the gate rejects leaves a terminal fragment and buys no new turn."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    pending = _seed_pending(root, seg, {"dispatch_token": tok, "seg": seg,
                                        "structure_ok": True, "quality_ok": False})
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["completed"]))
    line = parse_line(proc)
    assert proc.returncode == 1 and line["ok"] is False
    assert line["reason"] == "pending-rejected"

    assert [c for c in read_calls(root, "D1") if c["sub"] == "task"] == [], (
        "a fresh codex turn was launched over a permanently rejected candidate -- the exact "
        "unbounded spend #665 is filed for"
    )
    fragment = read_fragment(root, seg)
    assert fragment["status"] == "blocked", fragment
    assert fragment["reason"] == "translate-rejected", fragment
    assert set(fragment) <= {"timestamp", "status", "reason"}, fragment

    jl = json.loads((root / "segments" / (".codex_job.%s.json" % seg)).read_text())
    assert jl["reason"] == "pending-rejected"
    assert jl["ledger_write"] == "ok"
    assert "validate_draft.py" in (jl["error_detail"] or "")
    assert not pending.exists()
    assert list((root / "segments").glob(".codex_ledger_payload.*")) == []


@pytest.mark.parametrize("label,payload", [
    # exit 2 from validate_draft.py: environment, not a verdict on the candidate.
    ("validator_environment_failure",
     {"dispatch_token": "RUN:c001", "seg": "c001", "structure_ok": True, "quality_ok": True,
      "validator_env_fail": True}),
    # A pending left by an OLDER run, whose dispatch_token no longer matches: draft_ready.py
    # rejects it and the slot is recycled by a fresh turn. That is what the slot is FOR.
    ("stale_cross_run_token",
     {"dispatch_token": "OLDRUN:c001", "seg": "c001", "structure_ok": True,
      "quality_ok": True}),
])
def test_a_non_content_pending_rejection_still_buys_a_fresh_job(tmp_path, label, payload):
    """The two-sided half of the end-to-end row above. A `reason`-keyed implementation, or one
    keyed on "any non-zero from any gate", blocks these segments and goes red here."""
    root, companion, node = build_root(tmp_path)
    seg, tok = "c001", "RUN:c001"
    _seed_pending(root, seg, payload)
    proc = spawn_driver(root, companion, node, seg, tok, "translate", "D1",
                        base_state(seg, tok, "translate", attempt_mode="valid",
                                   status_seq=["completed"]))
    line = parse_line(proc)

    assert len([c for c in read_calls(root, "D1") if c["sub"] == "task"]) == 1, (
        "%s: the rejected pending was recycled, so a fresh turn is exactly what must happen"
        % label
    )
    assert line["reason"] == "promoted", line          # and that fresh turn succeeded
    assert not fragment_path(root, seg).exists(), (
        "%s: a recoverable rejection must never leave a terminal blocked fragment" % label
    )


# --------------------------------------------------------------------------- #
# #483: the late authorization re-check.
#
# The defect: both promote sites (adopt_pending()'s os.replace(pending, canonical)
# and run()'s os.replace(attempt, canonical)) used to be unconditional once their
# local gates passed. A job validated its candidate against the token it read when
# it started and promoted against whatever the canonical held at finalize time, so
# a concurrent re-stamp -- a select_segments.py --from-cap/--from-converged claim
# takes NO per-segment lease -- was silently overwritten, putting the OLD token
# back with the OLD bytes. The downstream ACCEPT gate expects exactly that old
# token, so it reads as a green run.
#
# ORDERING IS THE WHOLE FINDING. Every positive test below moves the token AFTER
# the gates the driver already runs and BEFORE the replace, by injecting into the
# very seam the driver passes through -- never by pre-seeding a canonical, which
# would prove nothing about when the check happens.
# --------------------------------------------------------------------------- #
def _write_canonical(job, token, **extra):
    """A canonical artifact carrying `token`, or carrying NO dispatch_token when
    `token` is None (a real state: the field is optional in the draft schema, so a
    re-emitted draft can lose it)."""
    doc = {"seg": job.seg}
    doc.update(extra)
    if token is not None:
        doc["dispatch_token"] = token
    Path(job.canonical).write_text(json.dumps(doc), encoding="utf-8")


def _promote_run(job, monkeypatch, on_validate=None):
    """Drive job.run() all the way to its post-validation promote, with the codex
    turn stubbed out. `on_validate` runs INSIDE validate_attempt(), i.e. after every
    gate and immediately before the promote branch -- the exact window #483 is about.
    Returns nothing; the caller asserts on the job."""
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    monkeypatch.setattr(job, "adopt_pending", lambda: False)

    def spy_launch():
        job.jobId = "J"
        return True

    def fake_poll():
        job.job_status = "completed"

    def fake_validate_attempt():
        Path(job.attempt).write_text(
            json.dumps({"seg": job.seg, "dispatch_token": job.tok}), encoding="utf-8")
        if on_validate is not None:
            on_validate()
        return True

    monkeypatch.setattr(job, "launch", spy_launch)
    monkeypatch.setattr(job, "poll", fake_poll)
    monkeypatch.setattr(job, "validate_attempt", fake_validate_attempt)


def test_promote_refuses_when_the_token_moves_after_validation(tmp_path, monkeypatch):
    """THE issue's own scenario, at run()'s promote site: the job starts against
    token A, a concurrent claim re-stamps the canonical to B while the codex turn
    runs, and the validated A-candidate must NOT go back over it.

    The move is injected inside validate_attempt() so it lands after the candidate
    gates and before the promote branch. Delete the _authorization_moved() call at
    that site and this test reports rc 0, reason 'promoted', and a canonical holding
    A's token again -- three independent failures."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    _write_canonical(job, job.tok, marker="claimed-then-restamped")
    _promote_run(job, monkeypatch,
                 on_validate=lambda: _write_canonical(job, "OTHERRUN:c001",
                                                      marker="claimed-then-restamped"))

    rc = job.run()

    assert rc == 1
    assert job.promoted is False
    assert job.reason == "authorization-moved"
    doc = json.loads(Path(job.canonical).read_text(encoding="utf-8"))
    assert doc["dispatch_token"] == "OTHERRUN:c001", (
        "the racing claimant's token must survive -- a promote here would restore "
        "this job's own older token along with older bytes"
    )
    assert job.tok in job.error_detail and "OTHERRUN:c001" in job.error_detail


def test_promote_still_succeeds_over_a_stable_foreign_token(tmp_path, monkeypatch):
    """THE control that fixes the shape of the check: an ordinary re-translation
    finds ANOTHER run's token on the canonical for the whole of its job (run B
    supplies --expect-token B:seg while the draft is still stamped A -- see
    _refuse_claimed_translate()'s own account of that flow), and it must still
    promote. Tighten the comparison to `== self.tok` and this test goes red while
    every refusal test above stays green, which is exactly the wrong invariant
    shipping unnoticed."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    _write_canonical(job, "PREVIOUSRUN:c001")
    _promote_run(job, monkeypatch)

    rc = job.run()

    assert rc == 0
    assert job.reason == "promoted"
    assert job.promoted is True
    doc = json.loads(Path(job.canonical).read_text(encoding="utf-8"))
    assert doc["dispatch_token"] == job.tok


def test_review_promote_over_a_previous_round_verdict_is_not_refused(tmp_path, monkeypatch):
    """The second false-RED counterexample: a round-2 review promotes over the
    round-1 verdict, whose dispatch_token carries the r1 label and therefore
    differs from this job's --expect-token throughout. Unchanged during the job, so
    nothing moved and nothing may be refused."""
    job = _mkjob(tmp_path, kind="review", tok="RUN:c001:r2", deadline=100)
    _write_canonical(job, "RUN:c001:r1", clean=True)
    _promote_run(job, monkeypatch)

    rc = job.run()

    assert rc == 0
    assert job.reason == "promoted"
    doc = json.loads(Path(job.canonical).read_text(encoding="utf-8"))
    assert doc["dispatch_token"] == "RUN:c001:r2"


def test_promote_refuses_when_the_token_is_deleted_under_the_job(tmp_path, monkeypatch):
    """present -> absent is a MOVE, not a no-op. A fix round that re-emits the draft
    legitimately drops `dispatch_token` (the field is optional in the schema), so
    the canonical this job started against is no longer the artifact it observed.
    A guard that only compared two strings, treating a missing token as 'nothing to
    compare', would permit this."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    _write_canonical(job, job.tok)
    _promote_run(job, monkeypatch, on_validate=lambda: _write_canonical(job, None))

    rc = job.run()

    assert rc == 1
    assert job.reason == "authorization-moved"
    doc = json.loads(Path(job.canonical).read_text(encoding="utf-8"))
    assert "dispatch_token" not in doc


def test_promote_refuses_when_the_canonical_cannot_be_parsed_at_promote_time(
        tmp_path, monkeypatch):
    """An observation that cannot be ESTABLISHED refuses. Unparsable JSON is the
    cheapest reachable instance of that whole row class; every other row is covered
    against the predicate itself below. A guard that mapped 'could not read' to 'no
    token' would compare None against None here and promote."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    _write_canonical(job, job.tok)

    def corrupt():
        Path(job.canonical).write_text("{not json at all", encoding="utf-8")
    _promote_run(job, monkeypatch, on_validate=corrupt)

    rc = job.run()

    assert rc == 1
    assert job.reason == "authorization-moved"
    assert Path(job.canonical).read_text(encoding="utf-8") == "{not json at all"


@pytest.mark.parametrize("seam", ["makedirs", "preflight", "flock"])
def test_the_authorization_baseline_is_run_s_very_first_act(tmp_path, monkeypatch, seam):
    """PLACEMENT, pinned from three sides. The baseline is worth exactly the window
    it precedes: a token that moved BEFORE it is recorded as this job's own
    legitimate starting state, and nothing downstream ever questions it (safe_adopt()
    only rejects the foreign draft, and foreign_owner_refusal() explicitly permits a
    foreign-token draft whose owner holds no claim record).

    Each seam fails a different wrong placement, which is why one is not enough:

      * `flock`  -- _acquire_flock() retries for the whole poll window, so a
        baseline taken after it can trail this job's dispatch by minutes.
      * `preflight` -- fails any placement after the device/canonical preflight.
      * `makedirs` -- run()'s literal first call, and the ONLY seam that fails a
        baseline taken between makedirs and the preflight. Without this case an
        implementation can snapshot one line too late and pass the other two.
    """
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    Path(job.segdir).mkdir(parents=True, exist_ok=True)
    _write_canonical(job, job.tok)

    def move_token():
        _write_canonical(job, "OTHERRUN:c001")

    if seam == "makedirs":
        real_makedirs = os.makedirs

        def fake_makedirs(path, *a, **kw):
            result = real_makedirs(path, *a, **kw)
            if os.fspath(path) == job.segdir:
                move_token()
            return result
        monkeypatch.setattr(os, "makedirs", fake_makedirs)
    elif seam == "preflight":
        real_preflight = job._preflight_same_device

        def fake_preflight():
            result = real_preflight()
            move_token()
            return result
        monkeypatch.setattr(job, "_preflight_same_device", fake_preflight)
    else:
        real_flock = job._acquire_flock

        def fake_flock(fd):
            result = real_flock(fd)
            move_token()
            return result
        monkeypatch.setattr(job, "_acquire_flock", fake_flock)

    _promote_run(job, monkeypatch)

    rc = job.run()

    assert rc == 1, (
        "a token that moved at the %r seam was baselined as legitimate -- the "
        "snapshot is taken too late" % (seam,)
    )
    assert job.reason == "authorization-moved"
    assert job.promoted is False
    doc = json.loads(Path(job.canonical).read_text(encoding="utf-8"))
    assert doc["dispatch_token"] == "OTHERRUN:c001"


# --------------------------------------------------------------------------- #
# #483: every row of _canonical_authority()'s own table, against the SHIPPED
# predicate. Without these, a mutation collapsing every delegated None to an
# ESTABLISHED "(True, None)" passes every promote-path test above whenever both
# observations happen to be None -- the fail-open shape the predicate exists to
# close, shipping under a green suite.
# --------------------------------------------------------------------------- #
def _authority_of(job, remaining=30.0):
    return job._canonical_authority(lambda: remaining)


def test_authority_row_readable_object_with_a_string_token(tmp_path):
    job = _mkjob(tmp_path)
    _write_canonical(job, "RUN:c001")
    assert _authority_of(job) == (True, "RUN:c001")


@pytest.mark.parametrize("token_value", [None, 7, ["RUN:c001"], {"a": 1}, True])
def test_authority_row_readable_object_without_a_usable_token(tmp_path, token_value):
    """ESTABLISHED, token None -- the file was read and it names no run. A real
    state: dispatch_token is optional in the draft schema, so a re-emitted draft can
    lose it, and a non-str value is not a token either."""
    job = _mkjob(tmp_path)
    doc = {"seg": job.seg}
    if token_value is not None:
        doc["dispatch_token"] = token_value
    Path(job.canonical).write_text(json.dumps(doc), encoding="utf-8")
    assert _authority_of(job) == (True, None)


def test_authority_row_definitively_absent(tmp_path):
    """The ONE row where a failed read still counts as established -- and it is
    established by the PRE-probe, never by the read's own None."""
    job = _mkjob(tmp_path)
    assert not os.path.exists(job.canonical)
    assert _authority_of(job) == (True, None)


def test_authority_row_non_regular_entry_is_unestablished(tmp_path):
    job = _mkjob(tmp_path)
    os.mkfifo(job.canonical)
    assert _authority_of(job) == (False, None)


def test_authority_row_unreadable_entry_is_unestablished(tmp_path):
    job = _mkjob(tmp_path)
    _write_canonical(job, "RUN:c001")
    os.chmod(job.canonical, 0o000)
    try:
        assert _authority_of(job) == (False, None)
    finally:
        os.chmod(job.canonical, 0o644)


def test_authority_row_oversized_entry_is_unestablished(tmp_path):
    job = _mkjob(tmp_path)
    Path(job.canonical).write_bytes(b"x" * (codex_job._MAX_REGULAR_READ_BYTES + 1))
    assert _authority_of(job) == (False, None)


@pytest.mark.parametrize("body", ["{not json", "[]", '"a string"', "17", ""])
def test_authority_row_unparsable_or_non_object_is_unestablished(tmp_path, body):
    job = _mkjob(tmp_path)
    Path(job.canonical).write_text(body, encoding="utf-8")
    assert _authority_of(job) == (False, None)


@pytest.mark.parametrize("exists", [True, False])
def test_authority_row_exhausted_budget_is_unestablished(tmp_path, exists):
    """Including the ABSENT case, which is the one that needs its own check:
    _read_regular_bounded() opens and fstats BEFORE its first budget test and
    returns None for ENOENT without ever consulting the budget, so without an entry
    check an absent canonical would read as established with no deadline honoured at
    all -- and both observations would then compare equal and permit a promotion
    after the caller's phase expired."""
    job = _mkjob(tmp_path)
    if exists:
        _write_canonical(job, "RUN:c001")
    assert _authority_of(job, remaining=0.0) == (False, None)


def test_a_symlinked_canonical_is_unestablished(tmp_path):
    """O_NOFOLLOW in the delegated read: a symlink planted at the canonical path is
    refused rather than followed, so its target's token never becomes this job's
    baseline."""
    job = _mkjob(tmp_path)
    target = Path(job.canonical + ".target")
    target.write_text(json.dumps({"dispatch_token": "RUN:c001"}), encoding="utf-8")
    os.symlink(target, job.canonical)
    assert _authority_of(job) == (False, None)


# --------------------------------------------------------------------------- #
# #483 at the OTHER promote site: adopt_pending()'s os.replace(pending, canonical).
# --------------------------------------------------------------------------- #
def test_adopt_pending_refuses_when_the_token_moves_while_its_gates_run(tmp_path, monkeypatch):
    """The adoption gates are where the real time passes at this site, so that is
    where the move is injected -- after the candidate has passed everything and
    before the replace. The pending must SURVIVE: it passed every gate, and what
    changed is who owns the slot it would land in, so a future dispatch under the
    current authorization can still re-gate it."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    _write_canonical(job, job.tok)
    job.canonical_authority = job._canonical_authority(job.poll_remaining)
    assert job.canonical_authority == (True, job.tok), "premise: the baseline was taken"
    Path(job.pending).write_text("{}", encoding="utf-8")

    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})

    def gate_then_move(argv, timeout):
        proc = gate(argv, timeout)
        if argv[0] == "validate_draft.py":          # the LAST gate: move after it passes
            _write_canonical(job, "OTHERRUN:c001")
        return proc
    monkeypatch.setattr(job, "_gate", gate_then_move)

    assert job.adopt_pending() is False
    assert calls == ["draft_ready.py", "validate_draft.py"], (
        "the gates must have RUN -- otherwise this proves nothing about the window "
        "between them and the replace: %r" % (calls,)
    )
    assert job.authorization_moved is True
    assert job.reason == "authorization-moved"
    assert os.path.exists(job.pending), "a refused promotion must not consume the pending"
    doc = json.loads(Path(job.canonical).read_text(encoding="utf-8"))
    assert doc["dispatch_token"] == "OTHERRUN:c001"


def test_a_promote_without_a_baseline_refuses(tmp_path, monkeypatch):
    """The field's initial value is the UNESTABLISHED one, so a promote path reached
    without run() having taken a baseline refuses instead of comparing against an
    assumption. Flip that initial value to an established `(True, None)` -- the
    obvious "harmless default" -- and this test goes red while nothing else does."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    assert job.canonical_authority == (False, None), "premise: no baseline taken"
    _write_canonical(job, job.tok)
    Path(job.pending).write_text("{}", encoding="utf-8")
    gate, _calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)

    assert job.adopt_pending() is False
    assert job.reason == "authorization-moved"
    assert "baseline" in job.error_detail
    assert os.path.exists(job.pending)


def test_run_stops_without_launching_after_an_adopt_pending_authorization_refusal(
        tmp_path, monkeypatch):
    """run() must not fall through to launch() after this refusal. A fresh paid codex
    turn cannot succeed either -- its own promote re-checks the same authorization and
    refuses the same way -- and a completion landing in the no-budget branch would put
    an unvalidated attempt into the pending slot the refusal deliberately kept.

    rc and reason are asserted alongside "launch not called" on purpose: a SUCCESSFUL
    adoption also skips launch(), so the spy alone cannot tell the two apart."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    _write_canonical(job, job.tok)
    Path(job.pending).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)

    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})

    def gate_then_move(argv, timeout):
        proc = gate(argv, timeout)
        if argv[0] == "validate_draft.py":
            _write_canonical(job, "OTHERRUN:c001")
        return proc
    monkeypatch.setattr(job, "_gate", gate_then_move)

    launched = {"v": False}

    def spy_launch():
        launched["v"] = True
        return True
    monkeypatch.setattr(job, "launch", spy_launch)

    rc = job.run()

    assert rc == 1
    assert job.reason == "authorization-moved"
    assert launched["v"] is False, "a paid turn was spent on a segment already taken over"
    assert calls == ["draft_ready.py", "validate_draft.py"]
    assert os.path.exists(job.pending)


def test_run_driven_pending_adoption_over_a_stable_foreign_token_still_promotes(
        tmp_path, monkeypatch):
    """The control for the two tests above, and the one that proves run() actually
    TAKES the baseline: the canonical carries a foreign run's token for the whole
    job, unchanged. It can only adopt if the baseline was recorded -- with no
    snapshot the initial unestablished value refuses -- and it must adopt, because
    nothing moved."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    _write_canonical(job, "PREVIOUSRUN:c001")
    Path(job.pending).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)
    monkeypatch.setattr(job, "launch", lambda: pytest.fail("must not launch"))

    rc = job.run()

    assert rc == 0
    assert job.reason == "adopted-pending"
    assert calls == ["draft_ready.py", "validate_draft.py"]
    assert os.path.exists(job.canonical)
    assert not os.path.exists(job.pending)


def test_a_raced_writer_that_moves_the_token_in_the_check_replace_window_is_refused(
        tmp_path, monkeypatch):
    """The mirror of test_canonical_replaceable_check_then_replace_window_is_a_known
    _unclosed_race above, and the reason that test's docstring now carries a
    narrowing clause. Same injection -- a writer publishing after the REAL
    _canonical_replaceable() answer, in the window that guard structurally cannot
    see -- but the raced content carries a DIFFERENT dispatch_token. That is the
    half of the window #483 closes: the promote's own late observation sees the
    moved token and refuses.

    Injected only on the FINAL promote's own check, never on run()'s preflight:
    injecting on every successful call would fire at preflight and prove nothing
    about this window. With adopt_pending() stubbed out, that is call TWO -- the
    preflight is call one -- and the count is asserted below rather than assumed,
    so a future phase that adds a replaceability check fails here loudly instead of
    silently relocating the injection."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    _write_canonical(job, job.tok)
    monkeypatch.setattr(job, "hygiene", lambda: None)

    real_check = job._canonical_replaceable
    calls = {"n": 0}

    def racing_check(remaining_fn):
        result = real_check(remaining_fn)
        calls["n"] += 1
        if result and calls["n"] == 2:      # preflight is 1; the promote's own is 2
            _write_canonical(job, "OTHERRUN:c001")
        return result
    monkeypatch.setattr(job, "_canonical_replaceable", racing_check)
    _promote_run(job, monkeypatch)
    monkeypatch.setattr(job, "adopt_pending", lambda: False)

    rc = job.run()

    assert calls["n"] >= 2, "the promote's own replaceability check never ran"
    assert rc == 1
    assert job.reason == "authorization-moved"
    doc = json.loads(Path(job.canonical).read_text(encoding="utf-8"))
    assert doc["dispatch_token"] == "OTHERRUN:c001", (
        "the raced writer's token must survive: this is the half of the "
        "check-then-replace window #483 closes"
    )


class _ExpiringBudget:
    """A remaining_fn that is positive for its first `live` calls and expired after --
    the shape a real phase budget takes when it runs out MID-observation. Callable, so
    it drops straight into _canonical_authority()'s own parameter."""

    def __init__(self, live=1, value=30.0):
        self.calls = 0
        self.live = live
        self.value = value

    def __call__(self):
        self.calls += 1
        return self.value if self.calls <= self.live else -1.0


@pytest.mark.parametrize("exists", [True, False])
def test_authority_refuses_when_the_budget_expires_mid_observation(tmp_path, exists):
    """The entry check alone is not enough: it runs BEFORE the lstat, the read, the
    decode and the parse, all of which consume real time. An observation that only
    finished after its phase expired must not authorize a promotion -- it would let the
    promote encroach on the tail this driver reserves for its terminal stdout line, fail
    sentinel and joblog.

    THE ABSENT ARM IS THE ONE THAT DISCRIMINATES, and saying so is the point of this
    note: a PRESENT canonical refuses even without the new re-checks, because
    _read_regular_bounded() consults the budget itself and returns None, which the
    pre-probe then classifies as unestablished anyway. Delete the re-checks and only the
    `exists=False` case goes red. The present arm is kept as a guard on that delegated
    path rather than as proof of this one -- a test that looks like it pins a property
    while a lower layer is really doing the work is exactly the vacuity worth naming."""
    job = _mkjob(tmp_path)
    if exists:
        _write_canonical(job, "RUN:c001")
    budget = _ExpiringBudget(live=1)
    assert job._canonical_authority(budget) == (False, None)
    assert budget.calls > 1, "the budget was never re-consulted after the entry check"


def test_authority_refuses_when_an_entry_appears_after_an_absent_pre_probe(
        tmp_path, monkeypatch):
    """The internal window: the pre-probe genuinely saw nothing, and a non-cooperating
    writer published between it and the read. If that entry then fails to read, calling
    it established-absence would compare EQUAL to a tokenless baseline and permit the
    promotion -- absence claimed from a moment that had already passed.

    The entry is published from inside the delegated read itself, which is the only way
    to land in that window deterministically. It is a FIFO, so the read that follows
    genuinely fails rather than being faked."""
    job = _mkjob(tmp_path)
    assert not os.path.exists(job.canonical), "premise: the pre-probe must see nothing"

    real_read = job._read_regular_bounded

    def read_after_a_writer_lands(path, remaining_fn):
        if os.fspath(path) == job.canonical and not os.path.exists(job.canonical):
            os.mkfifo(job.canonical)          # published inside the window
        return real_read(path, remaining_fn)
    monkeypatch.setattr(job, "_read_regular_bounded", read_after_a_writer_lands)

    assert job._canonical_authority(lambda: 30.0) == (False, None), (
        "an entry that appeared after the pre-probe and could not be read must be "
        "UNESTABLISHED -- claiming absence here permits a promotion over it"
    )
    assert os.path.exists(job.canonical), "premise: the fixture's writer really landed"


def test_a_moved_token_message_stays_inside_the_durable_joblog_budget(tmp_path, monkeypatch):
    """error_detail lands in the DURABLE joblog under the operator's root and in the
    stdout line, which is why this file caps every other text that reaches it
    (_GATE_OUTPUT_CAP: "this text lands in the durable joblog, so it must stay
    bounded"). A dispatch_token is untrusted text out of a JSON file the codex process
    this driver launches can write, and the bounded read admits anything under 64 MiB --
    so an uncapped token would put the whole thing on disk on every refusal.

    The token here is large but well under the read ceiling, so it is genuinely read,
    genuinely established, and genuinely compared: the refusal is real and only its
    MESSAGE is bounded."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    _write_canonical(job, job.tok)
    huge = "OTHERRUN:" + ("z" * 200_000)
    _promote_run(job, monkeypatch, on_validate=lambda: _write_canonical(job, huge))

    rc = job.run()

    assert rc == 1
    assert job.reason == "authorization-moved"
    assert len(job.error_detail) < 3 * codex_job.CodexJob._GATE_OUTPUT_CAP, (
        "the refusal message is unbounded: %d chars" % len(job.error_detail)
    )
    assert "truncated at" in job.error_detail
    # ...and the guard still compared the FULL token, never a truncated one: two
    # oversized tokens sharing a prefix must not read as equal.
    assert job.canonical_authority == (True, job.tok)


def test_an_unestablished_baseline_refuses_before_spending_a_codex_turn(tmp_path, monkeypatch):
    """A canonical that is present, regular, readable and in budget but whose bytes are
    not a JSON object passes _canonical_replaceable() and would previously have been
    promoted over. It is now refused -- and refused at the TOP of run(), because every
    promote this run could reach would refuse on the same reading, so launching a codex
    turn first buys nothing but cost.

    Delete the early return and this test still ends in a refusal, but `launched` flips
    to True and the reason changes: a paid turn burned on every retry, forever, on a
    segment nothing can repair."""
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    Path(job.canonical).write_text("{ truncated write from a straggler", encoding="utf-8")
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)

    launched = {"v": False}

    def spy_launch():
        launched["v"] = True
        return True
    monkeypatch.setattr(job, "launch", spy_launch)

    rc = job.run()

    assert rc == 1
    assert launched["v"] is False, "a paid codex turn was spent on an unreadable baseline"
    assert job.reason == "authorization-unestablished"
    assert job.canonical in job.error_detail
    assert Path(job.canonical).read_text(encoding="utf-8").startswith("{ truncated")


# ---- #697: the gated candidate stages OUTSIDE durable_root -------------------
#
# The relocation closes exactly ONE channel -- discovery by listing segments/ -- and the
# tests below split along the two things that actually had to be got right for it:
# WHERE the judged artifact lives, and what happens to it when a now-CROSS-DIRECTORY
# os.replace() fails. The second half is the expensive half: making a same-directory
# rename a cross-directory one adds failure causes the st_dev preflight cannot see, and
# the candidate at stake has already been paid for and gated.


def _fail_replace(monkeypatch, predicate):
    """Make os.replace() raise EXDEV for the renames `predicate(src, dst)` selects, and pass
    every other one through -- _write_joblog() and the promote both use os.replace, and a
    blanket failure would take out the joblog these tests read.

    `predicate` has no default ON PURPOSE: which renames fail is precisely what separates
    the promote-fails case from the promote-AND-preservation-fails case below, so it must
    be spelled at every call site."""
    real_replace = os.replace

    def exdev(src, dst, *a, **kw):
        if predicate(src, dst):
            raise OSError(18, "Invalid cross-device link")
        return real_replace(src, dst, *a, **kw)
    monkeypatch.setattr(os, "replace", exdev)


def _staging_dirs(tmp_path):
    """Every staging directory this driver could have left beside the durable root.
    _mkjob() roots the job at tmp_path/durable, so dirname(root) is tmp_path."""
    return sorted(p.name for p in Path(tmp_path).glob(".ltcj-stg.*"))


def test_the_fresh_path_hands_its_gates_a_candidate_outside_durable_root(tmp_path, monkeypatch):
    """validate_attempt() is the other terminal-verdict path (adopt_pending() is pinned by
    test_the_gated_snapshot_is_not_discoverable_by_listing_segments). Both had to move or
    the weaker one still decides terminally, which is the whole reason #697 names both."""
    job = _mkjob(tmp_path, kind="translate")
    _seed_sandbox(tmp_path, job)
    handed = []

    def recording_gate(args, timeout):
        handed.append(args[args.index("--candidate-file") + 1])
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(job, "_gate", recording_gate)

    assert job.validate_attempt() is True
    assert handed, "no gate was handed a candidate at all -- this test would prove nothing"
    for path in handed:
        assert not path.startswith(job.root + os.sep), (
            "a gate is still judging a candidate inside durable_root: %s" % path)
        assert os.path.dirname(path) == job.staging_dir
    assert not job.staging_dir.startswith(job.root + os.sep)


def test_run_refuses_when_no_staging_directory_can_be_created(tmp_path, monkeypatch):
    """An unwritable parent means the candidate would have to be gated in segments/ again.
    Refuse loudly BEFORE the flock and before a paid turn -- never fall back into segdir,
    which would silently un-ship this fix while every other test stayed green."""
    parent = tmp_path / "readonly-parent"
    (parent / "durable" / "segments").mkdir(parents=True)
    job = codex_job.CodexJob(
        kind="translate", seg="c001", tok="RUN:c001", disp="d1",
        root=str(parent / "durable"), companion=_companion_file(tmp_path),
        prompt_text=PROMPT_ONE, prompt_file=_prompt_file(tmp_path), deadline_sec=100,
        poll_sec=1, effort="high", node="node")
    monkeypatch.setattr(job, "hygiene", lambda: None)
    launched = {"n": 0}
    monkeypatch.setattr(job, "launch", lambda: launched.__setitem__("n", launched["n"] + 1) or True)

    os.chmod(parent, 0o500)
    try:
        rc = job.run()
    finally:
        os.chmod(parent, 0o755)

    assert rc == 1
    assert job.reason == "staging-unavailable"
    assert job.attempt is None
    assert launched["n"] == 0, "a paid codex turn was spent with nowhere safe to gate its output"
    assert not job.holds_lock, "refused before the flock lease -- same shape as device-mismatch"
    assert job.error_detail and "staging setup failed" in job.error_detail


def test_a_promoted_run_leaves_no_staging_directory_behind(tmp_path, monkeypatch):
    """The directory is WITNESSED while the run is in flight, from inside the gate stub that
    is handed the candidate. Without that, this test would pass just as well against a build
    that never created a staging directory at all -- which is exactly what it must not do."""
    job = _mkjob(tmp_path, kind="translate")
    monkeypatch.setattr(job, "hygiene", lambda: None)
    Path(job.pending).write_text(json.dumps({"draft": "deferred"}), encoding="utf-8")
    witness = {}

    def witnessing_gate(args, timeout):
        handed = args[args.index("--candidate-file") + 1]
        witness["dir_live"] = os.path.isdir(job.staging_dir)
        witness["candidate_inside"] = os.path.dirname(handed) == job.staging_dir
        witness["candidate_present"] = os.path.exists(handed)
        return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr(job, "_gate", witnessing_gate)
    monkeypatch.setattr(job, "launch", lambda: pytest.fail("adoption should have promoted"))

    assert job.run() == 0
    assert witness == {"dir_live": True, "candidate_inside": True, "candidate_present": True}
    assert job.adopted is True
    assert _staging_dirs(tmp_path) == []
    assert not list(Path(job.segdir).glob(".att.*")), "no candidate left inside segments/"


def test_a_failed_fresh_promote_preserves_the_validated_candidate(tmp_path, monkeypatch):
    """The promote is a CROSS-DIRECTORY rename since #697, so it can fail for causes the
    st_dev preflight cannot see (EXDEV across a bind mount presenting an equal st_dev). The
    bytes at stake passed every gate and were paid for, so they are relocated into segments/
    under preserved_attempt rather than discarded with the staging directory."""
    job = _mkjob(tmp_path, kind="translate")
    monkeypatch.setattr(job, "hygiene", lambda: None)
    payload = json.dumps({"draft": "gated and paid for"})

    def fake_validate_attempt():
        assert job._ensure_staging() is True
        Path(job.attempt).write_text(payload, encoding="utf-8")
        return True
    monkeypatch.setattr(job, "validate_attempt", fake_validate_attempt)
    monkeypatch.setattr(job, "launch", lambda: setattr(job, "jobId", "J") or True)
    monkeypatch.setattr(job, "poll", lambda: setattr(job, "job_status", "completed"))

    _fail_replace(monkeypatch, lambda src, dst: src == job.attempt and dst == job.canonical)

    rc = job.run()

    assert rc == 1
    assert job.reason == "promote-failed"
    assert job.rename_failed is True
    assert job.promoted is False
    assert not os.path.exists(job.canonical), "nothing may land in the canonical"
    assert Path(job.preserved_attempt).read_text(encoding="utf-8") == payload, (
        "a fully gated candidate was discarded because its promote rename failed")
    assert _staging_dirs(tmp_path) == []


def test_a_failed_adoption_promote_stops_the_run_and_keeps_the_pending(tmp_path, monkeypatch):
    """adopt_pending()'s tail deletes self.pending and returns True, and run() reads that
    True as `adopted`. A promote failure that merely recorded a flag and fell through would
    therefore report exit 0 / reason adopted-pending having promoted nothing AND having just
    deleted the bytes -- so the handler returns False, and run() STOPS on rename_failed
    rather than spending a fresh turn whose completion could overwrite the intact pending."""
    job = _mkjob(tmp_path, kind="translate")
    monkeypatch.setattr(job, "hygiene", lambda: None)
    deferred = json.dumps({"draft": "a prior run's completed attempt"})
    Path(job.pending).write_text(deferred, encoding="utf-8")
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job, "_gate", gate)
    monkeypatch.setattr(job, "launch", lambda: pytest.fail("no fresh turn may be launched"))

    _fail_replace(monkeypatch, lambda src, dst: src == job.attempt and dst == job.canonical)

    rc = job.run()

    assert rc == 1
    assert job.adopted is False and job.ok is False
    assert job.reason == "promote-failed"
    assert job.rename_failed is True
    assert calls == ["draft_ready.py", "validate_draft.py"]
    assert not os.path.exists(job.canonical)
    assert Path(job.pending).read_text(encoding="utf-8") == deferred, (
        "the pending is the copy a later run consults; a failed promote must not consume it")
    assert _staging_dirs(tmp_path) == []


def test_a_failed_preservation_reports_where_the_bytes_survived(tmp_path, monkeypatch):
    """Every path that reaches the preserve arm has ALREADY written error_detail -- the
    promote handler does it itself -- so a set-if-None diagnostic would silently drop the
    one record of where the surviving candidate is. It appends instead, and names the path,
    which is the one place this driver deliberately publishes a staging location."""
    job = _mkjob(tmp_path, kind="translate")
    monkeypatch.setattr(job, "hygiene", lambda: None)
    payload = json.dumps({"draft": "gated and paid for"})

    def fake_validate_attempt():
        assert job._ensure_staging() is True
        Path(job.attempt).write_text(payload, encoding="utf-8")
        return True
    monkeypatch.setattr(job, "validate_attempt", fake_validate_attempt)
    monkeypatch.setattr(job, "launch", lambda: setattr(job, "jobId", "J") or True)
    monkeypatch.setattr(job, "poll", lambda: setattr(job, "job_status", "completed"))

    # BOTH the promote and the preservation -- that widened predicate IS this test.
    _fail_replace(monkeypatch, lambda src, dst: src == job.attempt)

    rc = job.run()

    assert rc == 1
    assert Path(job.attempt).read_text(encoding="utf-8") == payload, "bytes must not be destroyed"
    assert "promote replace failed" in job.error_detail, "the earlier detail survived"
    assert "staging preserve failed" in job.error_detail
    assert job.attempt in job.error_detail, "the surviving path must be recoverable by hand"
    record = json.loads(Path(job.joblog).read_text(encoding="utf-8"))
    assert record["status"] == "terminal"
    assert job.attempt in record["error_detail"], "the joblog is the only durable carrier"
    assert _staging_dirs(tmp_path) == [os.path.basename(job.staging_dir)], (
        "the directory holding surviving bytes must NOT be removed")


def test_the_two_refusals_with_no_candidate_on_disk_leave_no_staging_litter(tmp_path, monkeypatch):
    """canonical_unreadable is set on three branches and only ONE has an attempt on disk.
    Preserving unconditionally would write a lying diagnostic on the other two and leak an
    empty staging directory on every retry -- a per-dispatch accumulation beside the
    operator's durable root."""
    # Branch 1: run()'s pre-dispatch canonical preflight, before anything is published.
    job = _mkjob(tmp_path, kind="translate", deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    locked_dir = tmp_path / "locked_pre"
    locked_dir.mkdir()
    (locked_dir / "canonical.json").write_text('{"marker":"locked"}', encoding="utf-8")
    job.canonical = str(locked_dir / "canonical.json")
    os.chmod(locked_dir, 0o000)
    monkeypatch.setattr(job, "launch", lambda: pytest.fail("no turn may be spent"))
    try:
        rc = job.run()
    finally:
        os.chmod(locked_dir, 0o755)
    assert rc == 1 and job.canonical_unreadable is True
    assert not os.path.exists(job.attempt), "premise: nothing was ever published here"
    assert "staging preserve failed" not in (job.error_detail or "")
    assert _staging_dirs(tmp_path) == []

    # Branch 2: adopt_pending()'s own refusal, which removes its snapshot deliberately.
    job2 = _mkjob(tmp_path, kind="translate")
    Path(job2.pending).write_text(json.dumps({"draft": "deferred"}), encoding="utf-8")
    job2.canonical_authority = job2._canonical_authority(job2.poll_remaining)
    gate, _ = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0})
    monkeypatch.setattr(job2, "_gate", gate)
    monkeypatch.setattr(job2, "_canonical_replaceable", lambda remaining_fn: False)
    assert job2.adopt_pending() is False
    assert job2.canonical_unreadable is True
    assert not os.path.exists(job2.attempt), "premise: the snapshot is removed by that branch"
    job2.finalize()
    assert "staging preserve failed" not in (job2.error_detail or "")
    assert _staging_dirs(tmp_path) == []
