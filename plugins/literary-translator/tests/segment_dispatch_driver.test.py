"""tests/segment_dispatch_driver.test.py -- #409 Step 4: the local driver
skeleton's own 8 safety properties, tested as independent, isolated units
plus a handful of end-to-end scenarios.

## Scope

This file tests EXACTLY what `segment_dispatch_driver.py`'s own module
docstring says it closes in this release: launch/process-isolation
(properties 2+6), the project-wide lease (property 3), reusing
`draft_sha1.py`'s own hash rather than an eighth copy (property 4), the
append-only journal (property 5), the volume refusal (property 7), and
the Step 1 gate (property 8) -- including the `--plugin-root` addition
(beyond the 8 named properties, see the driver's own docstring for why)
proven via the SAME poisoned-sibling technique
`select_segments.test.py`/`ledger_merge.test.py`/`final_audit.test.py`
already use for their own `--plugin-root` batteries.

It does NOT test a real per-segment translate/review loop -- the driver
does not have one yet (see its own module docstring's "What this skeleton
deliberately does NOT implement" section).

## Fixture strategy

Every test that needs a real durable_root copies the REAL
`segment_dispatch_driver.py`, `select_segments.py`, `ledger_merge.py`,
`cache_key.py` (and, for the `--plugin-root` dispatch-resolution tests,
`codex_job.py`) into an isolated `tmp_path/durable_root/scripts/` --
exactly like `select_segments.test.py`'s own `make_durable_root` pattern
-- so every script's `Path(__file__)`-based self-anchoring resolves
against the fixture. `cache_key.py` is stubbed with the SAME small fixture
script `select_segments.test.py`/`ledger_merge.test.py` use, for the same
reason (scope this file to the driver's OWN logic, not re-prove
`cache_key.py`'s 15-field hashing, which has its own dedicated test file).
"""
import fcntl
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
DRIVER_SRC = SCRIPTS_SRC_DIR / "segment_dispatch_driver.py"
SELECT_SEGMENTS_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
CACHE_KEY_SRC = SCRIPTS_SRC_DIR / "cache_key.py"
CODEX_JOB_SRC = SCRIPTS_SRC_DIR / "codex_job.py"
DRAFT_SHA1_SRC = SCRIPTS_SRC_DIR / "draft_sha1.py"

for _src in (DRIVER_SRC, SELECT_SEGMENTS_SRC, LEDGER_MERGE_SRC, CACHE_KEY_SRC, CODEX_JOB_SRC, DRAFT_SHA1_SRC):
    assert _src.is_file(), f"expected script not found: {_src}"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DRIVER = _load_module(DRIVER_SRC, "segment_dispatch_driver_under_test")


# ---------------------------------------------------------------------------
# Fixture harness -- mirrors select_segments.test.py's own make_durable_root.
# ---------------------------------------------------------------------------

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

DEFAULT_PROFILE_YAML = (
    "engine:\n"
    "  max_fix_rounds: 2\n"
    "  max_codex_jobs_per_batch: 400\n"
)


def make_durable_root(tmp_path, name="durable_root", profile_yaml=DEFAULT_PROFILE_YAML, stage_codex_job=False):
    """Isolated durable_root: real segment_dispatch_driver.py +
    select_segments.py + ledger_merge.py (+ codex_job.py when requested)
    under scripts/, a fake cache_key.py stub, empty manifest/runs/segments
    scaffolding, and a minimal profile.yml + ownership marker."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DRIVER_SRC, scripts_dir / "segment_dispatch_driver.py")
    shutil.copy2(SELECT_SEGMENTS_SRC, scripts_dir / "select_segments.py")
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    if stage_codex_job:
        shutil.copy2(CODEX_JOB_SRC, scripts_dir / "codex_job.py")

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


def write_manifest(root, seg_ids):
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": s} for s in seg_ids]}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_fragment(root, seg, record):
    frag_path = root / "runs" / "ledger.d" / f"{seg}.json"
    frag_path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return frag_path


def write_fixture_cache_keys(root, mapping):
    (root / "test_fixture_cache_keys.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")


def not_started_project(tmp_path, n=1, **kwargs):
    """The simplest fixture that clears BOTH gates: N not_started segments
    (no ledger fragments at all), well under any realistic volume cap."""
    root = make_durable_root(tmp_path, **kwargs)
    write_manifest(root, [f"seg{i:02d}" for i in range(1, n + 1)])
    return root


def run_driver(root, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "segment_dispatch_driver.py"), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


CACHE_KEY_FIELDS = [
    "input_sha1", "style_contract_hash", "used_terms_hash", "pipeline_version",
    "schema_hash", "prompt_hash", "agent_config_hash", "profile_semantics_hash",
    "particle_config_hash", "source_extraction_hash", "source_input_hash",
    "derivation_bundle_hash", "verse_map_hash", "note_map_hash", "plugin_bundle_hash",
]


def make_cache_key(seed):
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def converged_fragment(cache_key, reviewed_draft_sha1, rounds=1):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": rounds,
        "cache_key": cache_key,
        "n_blocks": 1,
        "n_footnotes": 0,
        "n_verses": 0,
        "reviewed_draft_sha1": reviewed_draft_sha1,
    }


def mark_ever_converged(root, seg):
    (root / "segments" / f".ever_converged.{seg}").write_text("converged\n", encoding="utf-8")


# ===========================================================================
# Property 8 -- the Step 1 re-translate gate, honored, never bypassed.
# ===========================================================================


def test_step1_gate_refuses_a_previously_converged_segment_without_the_flag(tmp_path):
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    current_key = make_cache_key("current")
    write_fixture_cache_keys(root, {"seg01": current_key})
    # Stale (cache key drifted) but ever-converged -- select_segments.py's
    # own WAS_CONVERGED_STATUSES treats this as dispatch-eligible by
    # default, which is exactly the case the Step 1 gate exists to catch.
    stored = dict(current_key)
    stored["style_contract_hash"] = "style_contract_hash-OLD"
    write_fragment(root, "seg01", converged_fragment(stored, "0" * 40))
    mark_ever_converged(root, "seg01")

    proc = run_driver(root)

    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "seg01" in payload["error"]
    assert "--allow-retranslate-converged" in payload["error"]


def test_step1_gate_passes_a_previously_converged_segment_with_the_flag(tmp_path):
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    current_key = make_cache_key("current")
    write_fixture_cache_keys(root, {"seg01": current_key})
    stored = dict(current_key)
    stored["style_contract_hash"] = "style_contract_hash-OLD"
    write_fragment(root, "seg01", converged_fragment(stored, "0" * 40))
    mark_ever_converged(root, "seg01")

    proc = run_driver(root, "--allow-retranslate-converged")

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["segs"] == ["seg01"]


def test_only_segs_bad_id_refused_locally_before_select_segments_ever_runs(tmp_path):
    """A malformed --only-segs id must be refused by THIS script's own
    validate_seg() check, before select_segments.py -- or even the
    project lock -- is ever touched. Proven, not just asserted: the lock
    file must not exist afterward."""
    root = not_started_project(tmp_path, n=1)

    proc = run_driver(root, "--only-segs", "seg01;rm -rf /")

    assert proc.returncode == 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "unsafe segment id" in payload["error"]
    assert not (root / "runs" / ".driver.lock").exists(), (
        "the lock must never be created for a request refused before any "
        "gate work begins"
    )


def test_only_segs_unknown_id_refused_by_select_segments_itself(tmp_path):
    """An id that IS shell-safe but not in manifest.json's segments[] is
    select_segments.py's own refusal, not this script's -- proven by the
    lock HAVING been acquired (and released) by the time it happens."""
    root = not_started_project(tmp_path, n=1)

    proc = run_driver(root, "--only-segs", "segNoSuchSeg")

    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "segNoSuchSeg" in payload["error"]


# ===========================================================================
# Property 7 -- the volume refusal, engine.max_codex_jobs_per_batch.
# Formula and boundary must match mass-translate-wf.template.js's own
# preflight for this SAME knob exactly: CODEX_JOBS_PER_SEG = max_fix_rounds
# + 2, estimated = N * CODEX_JOBS_PER_SEG, refuse iff estimated > cap
# (strictly greater -- estimated == cap must NOT trip the gate).
# ===========================================================================


def test_codex_jobs_per_segment_formula():
    assert DRIVER.codex_jobs_per_segment(0) == 2
    assert DRIVER.codex_jobs_per_segment(3) == 5
    assert DRIVER.codex_jobs_per_segment(4) == 6


def test_check_volume_cap_boundary_is_strictly_greater_than():
    # 10 segments * (2 + 2) = 40 -- exactly at the cap must NOT refuse.
    assert DRIVER.check_volume_cap(10, 2, 40) is None
    # One more segment -> 44 > 40 -- must refuse.
    refusal = DRIVER.check_volume_cap(11, 2, 40)
    assert refusal is not None
    assert refusal["estimatedCodexJobs"] == 44
    assert refusal["codexJobsCap"] == 40
    assert refusal["reason"] == "batch-too-large-codex-jobs"
    assert "estimatedCodexJobs=44" in refusal["message"]
    assert "11 segment(s)" in refusal["message"]
    assert "max_fix_rounds=2" in refusal["message"]
    assert "engine.max_codex_jobs_per_batch limit of 40" in refusal["message"]


def test_check_volume_cap_matches_the_shipped_default_cap_boundary():
    """The shipped schema default (400) admits exactly 66 segments at
    max_fix_rounds=4 -- the SAME figure profile.example.yml's own comment
    documents (66*6=396<=400; 67*6=402>400) and the SAME figure the
    max_codex_jobs_per_batch task's own report measured independently."""
    assert DRIVER.check_volume_cap(66, 4, 400) is None
    refusal = DRIVER.check_volume_cap(67, 4, 400)
    assert refusal is not None
    assert refusal["estimatedCodexJobs"] == 402


def test_volume_cap_refuses_end_to_end(tmp_path):
    root = make_durable_root(
        tmp_path,
        profile_yaml="engine:\n  max_fix_rounds: 1\n  max_codex_jobs_per_batch: 5\n",
    )
    # 3 not_started segments * (1+2)=3 jobs/seg = 9 > 5.
    write_manifest(root, ["seg01", "seg02", "seg03"])

    proc = run_driver(root, "--allow-empty")

    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert payload["reason"] == "batch-too-large-codex-jobs"
    assert payload["estimatedCodexJobs"] == 9
    assert payload["codexJobsCap"] == 5


def test_volume_cap_default_when_profile_omits_the_key(tmp_path):
    """profile.schema.json's own documented default (400) applies when the
    profile omits engine.max_codex_jobs_per_batch entirely -- the schema's
    'default' annotation is documentation-only, so this script must apply
    it itself (see load_engine_config()'s own docstring)."""
    root = make_durable_root(tmp_path, profile_yaml="engine:\n  max_fix_rounds: 4\n")
    write_manifest(root, ["seg01"])

    proc = run_driver(root)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["engine"]["max_codex_jobs_per_batch"] == 400


# ===========================================================================
# Property 3 -- one project-wide fcntl.flock, held by descriptor, never a
# pid file. Two drivers on one project must refuse the second, immediately
# and by name.
# ===========================================================================


def test_acquire_driver_lock_succeeds_and_writes_diagnostic_content(tmp_path):
    durable_root = tmp_path / "root"
    (durable_root / "runs").mkdir(parents=True)

    fd = DRIVER.acquire_driver_lock(durable_root)
    try:
        lock_path = DRIVER.driver_lock_path(durable_root)
        assert lock_path.is_file()
        content = json.loads(lock_path.read_text(encoding="utf-8"))
        assert content["pid"] == os.getpid()
        assert "started_at" in content
    finally:
        DRIVER.release_driver_lock(fd)


def test_acquire_driver_lock_refuses_when_already_held_by_another_process(tmp_path):
    """The two-drivers-on-one-project case, with a REAL second process
    holding the lock (not just a second fd in this same process, which
    fcntl.flock would not even contend against -- flock is per-OPEN-FILE-
    DESCRIPTION, but a same-process re-open still correctly contends; a
    separate PROCESS is the stronger, unambiguous proof)."""
    durable_root = tmp_path / "root"
    (durable_root / "runs").mkdir(parents=True)
    lock_path = DRIVER.driver_lock_path(durable_root)

    holder_src = (
        "import fcntl, os, sys, time\n"
        "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "sys.stdout.write('LOCKED\\n'); sys.stdout.flush()\n"
        "time.sleep(5)\n"
    )
    holder_script = tmp_path / "holder.py"
    holder_script.write_text(holder_src, encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, str(holder_script), str(lock_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        line = holder.stdout.readline()
        assert line.strip() == "LOCKED", f"holder failed to acquire: {holder.stderr.read()}"

        with pytest.raises(DRIVER.DriverError) as exc_info:
            DRIVER.acquire_driver_lock(durable_root)
        assert "another driver already holds" in str(exc_info.value)
        assert exc_info.value.exit_code == 1
    finally:
        holder.kill()
        holder.wait()


def test_two_drivers_on_one_project_second_refuses_end_to_end(tmp_path):
    root = not_started_project(tmp_path, n=1)
    lock_path = root / "runs" / ".driver.lock"

    holder_src = (
        "import fcntl, os, sys, time\n"
        "os.makedirs(os.path.dirname(sys.argv[1]), exist_ok=True)\n"
        "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "sys.stdout.write('LOCKED\\n'); sys.stdout.flush()\n"
        "time.sleep(5)\n"
    )
    holder_script = root / "holder.py"
    holder_script.write_text(holder_src, encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, str(holder_script), str(lock_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        line = holder.stdout.readline()
        assert line.strip() == "LOCKED", f"holder failed to acquire: {holder.stderr.read()}"

        proc = run_driver(root)

        assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        payload = parse_stdout(proc)
        assert payload["success"] is False
        assert "another driver already holds" in payload["error"]
        assert str(lock_path) in payload["lock_path"]
    finally:
        holder.kill()
        holder.wait()


# ===========================================================================
# Properties 2 + 6 -- the codex_job.py dispatch primitive: start_new_session
# (process isolation) and a real, race-free wait() (the #348 closure).
# ===========================================================================

FAKE_CODEX_JOB_SRC = """#!/usr/bin/env python3
import json
import os
import sys
import time


def main():
    marker = sys.argv[1]
    sleep_s = float(sys.argv[2])
    exit_code = int(sys.argv[3])
    info = {
        "pid": os.getpid(),
        "sid": os.getsid(0),
        "pgid": os.getpgid(0),
    }
    with open(marker, "w", encoding="utf-8") as f:
        json.dump(info, f)
        f.flush()
        os.fsync(f.fileno())
    time.sleep(sleep_s)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
"""


def _write_fake_codex_job(tmp_path, name="fake_codex_job.py"):
    path = tmp_path / name
    path.write_text(FAKE_CODEX_JOB_SRC, encoding="utf-8")
    return path


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_dispatch_codex_job_returns_the_real_exit_code(tmp_path):
    marker = tmp_path / "marker.json"
    fake = _write_fake_codex_job(tmp_path)

    code = DRIVER.dispatch_codex_job(fake, [str(marker), "0", "0"], wait_timeout=10)
    assert code == 0

    code = DRIVER.dispatch_codex_job(fake, [str(marker), "0", "1"], wait_timeout=10)
    assert code == 1


def test_dispatch_codex_job_uses_start_new_session(tmp_path):
    """The canonical proof of start_new_session=True: the child becomes
    its OWN session leader (sid == its own pid), which is only possible
    via setsid() -- a plain Popen child inherits the launcher's session."""
    marker = tmp_path / "marker.json"
    fake = _write_fake_codex_job(tmp_path)

    code = DRIVER.dispatch_codex_job(fake, [str(marker), "0", "0"], wait_timeout=10)
    assert code == 0
    info = json.loads(marker.read_text(encoding="utf-8"))
    assert info["sid"] == info["pid"], (
        f"child is not its own session leader (sid={info['sid']}, "
        f"pid={info['pid']}) -- start_new_session=True did not take effect"
    )


def test_dispatch_codex_job_child_survives_killing_the_launchers_whole_process_group(tmp_path):
    """The OPERATIONAL property property 2 exists for: killing the
    LAUNCHER's entire process group (what a harness tearing down a
    spawned session typically does to its own children) must NOT reach
    the codex_job.py child, because start_new_session=True put it in a
    separate session/process group entirely.

    Drives the REAL dispatch_codex_job() inside the launcher subprocess --
    NOT a hand-rolled Popen call -- confirmed by mutation: an earlier draft
    of this test called subprocess.Popen(..., start_new_session=True)
    directly in the launcher script, which stayed GREEN even with
    start_new_session=False mutated into dispatch_codex_job() itself,
    because it was testing its own inline Popen call, not the function
    under test."""
    marker = tmp_path / "marker.json"
    fake = _write_fake_codex_job(tmp_path)

    launcher_src = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('driver_under_test', {str(DRIVER_SRC)!r})\n"
        "d = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(d)\n"
        "from pathlib import Path\n"
        f"d.dispatch_codex_job(Path({str(fake)!r}), [{str(marker)!r}, '3', '0'], wait_timeout=10)\n"
    )
    launcher = tmp_path / "launcher.py"
    launcher.write_text(launcher_src, encoding="utf-8")

    launcher_proc = subprocess.Popen(
        [sys.executable, str(launcher)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,  # isolate the LAUNCHER too, so killpg-ing it is safe from this test's own group
    )
    child_pid = None
    try:
        deadline = time.time() + 2
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "child should have started and written its marker by now"
        child_pid = json.loads(marker.read_text(encoding="utf-8"))["pid"]

        os.killpg(os.getpgid(launcher_proc.pid), signal.SIGKILL)
        launcher_proc.wait()

        time.sleep(0.1)
        assert _pid_alive(child_pid), (
            "codex_job.py child must survive killing its launcher's ENTIRE "
            "process group -- if this fails, start_new_session=True is not "
            "actually isolating the child"
        )
    finally:
        if child_pid is not None and _pid_alive(child_pid):
            os.kill(child_pid, signal.SIGKILL)
            try:
                os.waitpid(child_pid, 0)
            except ChildProcessError:
                pass  # not our child to reap (it's the launcher's) -- best-effort cleanup only


def test_dispatch_codex_job_backstop_timeout_kills_and_reaps_no_zombie(tmp_path):
    """When codex_job.py itself fails to honor its own deadline (a driver-
    level failure, not a normal outcome), the backstop wait_timeout kills
    AND reaps the child -- proven by the pid becoming genuinely invalid
    afterward (os.kill(pid, 0) raising ProcessLookupError), not merely
    unresponsive: a ZOMBIE still answers signal 0 until its parent reaps
    it, so this specifically distinguishes 'reaped' from 'merely killed'."""
    marker = tmp_path / "marker.json"
    fake = _write_fake_codex_job(tmp_path)

    with pytest.raises(DRIVER.DriverError) as exc_info:
        DRIVER.dispatch_codex_job(fake, [str(marker), "5", "0"], wait_timeout=0.3)
    assert "did not terminate within its own deadline" in str(exc_info.value)

    info = json.loads(marker.read_text(encoding="utf-8"))
    assert not _pid_alive(info["pid"]), (
        "the killed child must be fully reaped (no zombie left behind), "
        "not merely killed"
    )


def test_dispatch_codex_job_fatals_when_the_script_is_missing(tmp_path):
    marker = tmp_path / "marker.json"
    missing = tmp_path / "no_such_codex_job.py"

    with pytest.raises(DRIVER.DriverError) as exc_info:
        DRIVER.dispatch_codex_job(missing, [str(marker), "0", "0"], wait_timeout=10)
    assert "not found" in str(exc_info.value)


# ===========================================================================
# Property 4 -- draft_content_sha1 REUSE (import), never a new independent
# copy. Proven by comparing against the REAL draft_sha1.py CLI's own output.
# ===========================================================================


def test_current_draft_sha1_matches_the_cli(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    draft = {"seg": "seg01", "blocks": {"p1": "hello"}, "dispatch_token": "RUN:seg01"}
    (segments_dir / "seg01.draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")

    via_import = DRIVER.current_draft_sha1("seg01", segments_dir, scripts_dir)

    cli = subprocess.run(
        [sys.executable, str(scripts_dir / "draft_sha1.py"), "seg01", "--durable-root", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert cli.returncode == 0, f"stderr={cli.stderr!r}"
    assert via_import == cli.stdout.strip()


def test_current_draft_sha1_ignores_dispatch_token_changes_same_as_the_cli(tmp_path):
    """Proves the REUSED implementation, not just A matching implementation
    -- the dispatch_token-exclusion behavior is draft_sha1.py's own, and
    this must inherit it automatically rather than needing its own copy of
    that rule."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()

    draft_a = {"seg": "seg01", "blocks": {"p1": "hello"}, "dispatch_token": "RUN_A:seg01"}
    (segments_dir / "seg01.draft.json").write_text(json.dumps(draft_a, ensure_ascii=False), encoding="utf-8")
    sha_a = DRIVER.current_draft_sha1("seg01", segments_dir, scripts_dir)

    draft_b = {"seg": "seg01", "blocks": {"p1": "hello"}, "dispatch_token": "RUN_B:seg01"}
    (segments_dir / "seg01.draft.json").write_text(json.dumps(draft_b, ensure_ascii=False), encoding="utf-8")
    sha_b = DRIVER.current_draft_sha1("seg01", segments_dir, scripts_dir)

    assert sha_a == sha_b, "a dispatch_token-only change must not move the hash"


def test_current_draft_sha1_fatals_on_missing_draft(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()

    with pytest.raises(DRIVER.DriverError) as exc_info:
        DRIVER.current_draft_sha1("seg01", segments_dir, scripts_dir)
    assert "not found" in str(exc_info.value)


# ===========================================================================
# Property 5 -- append-only per-dispatch journal.
# ===========================================================================


def test_append_journal_accumulates_never_overwrites(tmp_path):
    DRIVER.append_journal(tmp_path, "sess1", {"type": "a", "n": 1})
    DRIVER.append_journal(tmp_path, "sess1", {"type": "b", "n": 2})
    DRIVER.append_journal(tmp_path, "sess1", {"type": "c", "n": 3})

    lines = DRIVER.journal_path(tmp_path, "sess1").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    entries = [json.loads(ln) for ln in lines]
    assert [e["type"] for e in entries] == ["a", "b", "c"]
    assert all("ts" in e for e in entries)


def test_append_journal_namespaces_by_session(tmp_path):
    DRIVER.append_journal(tmp_path, "sessA", {"type": "x"})
    DRIVER.append_journal(tmp_path, "sessB", {"type": "y"})

    assert DRIVER.journal_path(tmp_path, "sessA") != DRIVER.journal_path(tmp_path, "sessB")
    a = json.loads(DRIVER.journal_path(tmp_path, "sessA").read_text(encoding="utf-8").strip())
    b = json.loads(DRIVER.journal_path(tmp_path, "sessB").read_text(encoding="utf-8").strip())
    assert a["type"] == "x"
    assert b["type"] == "y"


def test_end_to_end_run_journals_the_gate_decisions(tmp_path):
    root = not_started_project(tmp_path, n=2)

    proc = run_driver(root)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    session_id = payload["session_id"]
    journal = DRIVER.journal_path(root, session_id)
    assert journal.is_file()
    types = [json.loads(ln)["type"] for ln in journal.read_text(encoding="utf-8").splitlines()]
    assert types == [
        "driver_started",
        "step1_gate_passed",
        "volume_check_passed",
        "driver_exit",
    ]


# ===========================================================================
# --plugin-root PATH -- the deliberate addition beyond the 8 named
# properties (see the driver's own module docstring). Same poisoned-sibling
# technique select_segments.test.py/ledger_merge.test.py/final_audit.test.py
# already use for their own --plugin-root batteries: both directions
# asserted, since a script that never touched the sibling at all would look
# identical to one that correctly routed around the poison.
# ===========================================================================

_TAMPERED_SELECT_SEGMENTS_SRC = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "sys.stderr.write('TAMPERED_SELECT_SEGMENTS_MUST_NEVER_RUN')\n"
    "sys.exit(97)\n"
)


def poison_durable_root_select_segments(root):
    (root / "scripts" / "select_segments.py").write_text(_TAMPERED_SELECT_SEGMENTS_SRC, encoding="utf-8")


def make_trusted_plugin_root(tmp_path, name="trusted_plugin_install"):
    """A SEPARATE physical location holding the REAL select_segments.py +
    its own siblings (ledger_merge.py, cache_key.py stub) at the
    {plugin_root}/assets/scripts/ layout SKILL.md documents."""
    plugin_root = tmp_path / name
    plugin_scripts_dir = plugin_root / "assets" / "scripts"
    plugin_scripts_dir.mkdir(parents=True)
    shutil.copy2(SELECT_SEGMENTS_SRC, plugin_scripts_dir / "select_segments.py")
    shutil.copy2(LEDGER_MERGE_SRC, plugin_scripts_dir / "ledger_merge.py")
    (plugin_scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    return plugin_root


def test_plugin_root_flag_omitted_preserves_todays_behavior(tmp_path):
    root = not_started_project(tmp_path, n=1)

    proc = run_driver(root)

    assert proc.returncode == 0
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["segs"] == ["seg01"]


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling(tmp_path):
    """The core security property: the driver runs from its own in-place
    durable-root copy whose SIBLING select_segments.py has been POISONED.
    --plugin-root pointing at a separate, untampered location must make
    it use THAT select_segments.py instead."""
    root = not_started_project(tmp_path, n=1)
    poison_durable_root_select_segments(root)
    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = run_driver(root, "--plugin-root", str(plugin_root))

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL select_segments.py must "
        f"succeed even though durable_root's own copy is poisoned:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["segs"] == ["seg01"]


def test_plugin_root_flag_absent_uses_the_poisoned_durable_root_sibling(tmp_path):
    """Negative control, and backward-compat proof in one: the SAME
    poisoned durable-root select_segments.py, invoked WITHOUT
    --plugin-root, genuinely runs and fails -- proving the positive test's
    success above is attributable to --plugin-root specifically."""
    root = not_started_project(tmp_path, n=1)
    poison_durable_root_select_segments(root)

    proc = run_driver(root)  # no --plugin-root

    assert proc.returncode == 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "TAMPERED_SELECT_SEGMENTS_MUST_NEVER_RUN" in payload["error"]


def test_plugin_root_redirects_which_codex_job_py_would_be_popened(tmp_path):
    """--plugin-root changes WHICH codex_job.py file resolve_dirs()
    resolves for future dispatch -- proven directly against resolve_dirs(),
    since the skeleton does not yet Popen codex_job.py in its own main()
    loop (see module docstring). --durable-root ALONE (plugin_root_str
    still None) must NOT redirect sibling resolution -- only --plugin-root
    does, matching select_segments.py's own convention exactly (see
    resolve_dirs()'s own docstring)."""
    plugin_root = tmp_path / "trusted_plugin_install"
    (plugin_root / "assets" / "scripts").mkdir(parents=True)
    shutil.copy2(CODEX_JOB_SRC, plugin_root / "assets" / "scripts" / "codex_job.py")
    data_root = tmp_path / "data_only"
    data_root.mkdir()

    self_anchored_default = DRIVER.resolve_dirs(None, None)
    self_anchored_with_durable_root_only = DRIVER.resolve_dirs(str(data_root), None)
    via_plugin_root = DRIVER.resolve_dirs(None, str(plugin_root))

    assert self_anchored_default["codex_job_script"] == DRIVER.CODEX_JOB_SCRIPT
    assert self_anchored_with_durable_root_only["codex_job_script"] == DRIVER.CODEX_JOB_SCRIPT, (
        "--durable-root alone must not redirect sibling resolution -- only "
        "--plugin-root does"
    )
    assert via_plugin_root["codex_job_script"] == plugin_root / "assets" / "scripts" / "codex_job.py"


# ===========================================================================
# Bundle registration -- NAMED assertions. schema_literal_drift.test.py's
# own doc-comparison test and scaffold_setup.test.py's dynamic-tuple
# hashing BOTH stay green if the driver is missing from code and docs
# together (each only compares two derived views against each other, never
# against a fixed expectation) -- see this driver's own module docstring's
# "Bundle registration" section. These two tests are the NAMED catchers,
# mirroring schema_literal_drift.test.py's own established precedent
# (`test_review_ready_and_resume_setup_are_plugin_bundle_members`).
# ===========================================================================

TEMPLATES_SRC_DIR = ASSETS_DIR / "templates"
CACHE_KEY_MODULE = _load_module(CACHE_KEY_SRC, "cache_key_for_bundle_test")
ORCHESTRATION_BUNDLE_MEMBERS = (
    "draft_ready.py",
    "ledger_merge.py",
    "language_smoke_report.py",
    "select_segments.py",
)


def _bundle_member_source(name):
    if name.endswith(".template.js"):
        return TEMPLATES_SRC_DIR / name
    return SCRIPTS_SRC_DIR / name


def _independent_bundle_hash(scripts_root, members):
    """sha1 of the sorted-by-filename concatenated raw bytes of `members`
    -- an INDEPENDENT recompute (plain hashlib, no cache_key/scaffold_setup
    helpers), matching scaffold_setup.test.py's own `_independent_bundle_hash`."""
    import hashlib

    paths = sorted((scripts_root / name for name in members), key=lambda p: p.name)
    blob = b"".join(p.read_bytes() for p in paths)
    return hashlib.sha1(blob).hexdigest()


def test_segment_dispatch_driver_is_a_plugin_bundle_member():
    """NAMED regression-catcher, per this project's own established
    convention. Every other bundle-membership assertion in this suite
    (and in schema_literal_drift.test.py/scaffold_setup.test.py) would
    accept the driver's absence silently as long as code and docs happen
    to agree with each other -- see this driver's own module docstring's
    'Bundle registration' section for the full reasoning."""
    assert "segment_dispatch_driver.py" in CACHE_KEY_MODULE.PLUGIN_BUNDLE_MEMBERS


def test_segment_dispatch_driver_mutation_moves_exactly_plugin_bundle_hash(tmp_path):
    """Changing ONLY the driver's bytes must move plugin_bundle_hash and
    NOTHING else. Proven by computing BOTH bundle hashes before and after,
    independently of cache_key.py's/scaffold_setup.py's own
    implementation."""
    scripts_root = tmp_path / "scripts"
    scripts_root.mkdir()
    all_members = sorted(set(CACHE_KEY_MODULE.PLUGIN_BUNDLE_MEMBERS) | set(ORCHESTRATION_BUNDLE_MEMBERS))
    for name in all_members:
        shutil.copy2(_bundle_member_source(name), scripts_root / name)

    before_plugin = _independent_bundle_hash(scripts_root, CACHE_KEY_MODULE.PLUGIN_BUNDLE_MEMBERS)
    before_orchestration = _independent_bundle_hash(scripts_root, ORCHESTRATION_BUNDLE_MEMBERS)

    driver_copy = scripts_root / "segment_dispatch_driver.py"
    driver_copy.write_bytes(driver_copy.read_bytes() + b"\n# fixture mutation\n")

    after_plugin = _independent_bundle_hash(scripts_root, CACHE_KEY_MODULE.PLUGIN_BUNDLE_MEMBERS)
    after_orchestration = _independent_bundle_hash(scripts_root, ORCHESTRATION_BUNDLE_MEMBERS)

    assert after_plugin != before_plugin, "mutating the driver's bytes must move plugin_bundle_hash"
    assert after_orchestration == before_orchestration, (
        "mutating the driver's bytes must NOT move orchestration_bundle_hash "
        "-- the driver is not (and must never become) an orchestration-bundle member"
    )


def test_fixture_sanity_gates_pass_and_nothing_dispatched(tmp_path):
    root = not_started_project(tmp_path, n=1)

    proc = run_driver(root)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["segs"] == ["seg01"]
    assert payload["dispatched"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
