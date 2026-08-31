#!/usr/bin/env python3
"""The verdict channel is AUTHORIZATION, so every test here is a negative one.

A verdict tells `glossary_dispatch_driver.py` to write an approval record and, on
the last batch, to merge into a canon whose rows are immutable. So the property
under test is not "a good verdict works" -- it is that each of the specific ways a
bad one could slip through is refused, and that NOTHING is produced when it is.

Every assertion checks the OUTCOME (no approval record on disk, batch not ready),
never a log line: a refusal that logs and proceeds looks identical to a real one in
stderr, and it is exactly the shape this file exists to catch.

What these tests deliberately do NOT claim to cover: a hostile codex job. The
snapshot and evidence live under RUN_DIR, which every agent in the pass can write;
see the driver's module docstring and the template's approvalRecordPath() comment
for why that bound is the shipped design's own and is closed elsewhere.
"""

import importlib.util
import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
SCRIPTS = SKILL_ROOT / "assets" / "scripts"
DRIVER = SCRIPTS / "glossary_dispatch_driver.py"


def load_driver(scripts_dir: Path):
    """Imports the REAL shipped script from an isolated fixture scripts/ dir, so
    its self-anchored DURABLE_ROOT resolves against the fixture exactly as a
    deployed copy resolves against a project."""
    target = scripts_dir / "glossary_dispatch_driver.py"
    if not target.exists():
        shutil.copy2(DRIVER, target)
    spec = importlib.util.spec_from_file_location(
        f"gdd_{abs(hash(str(target)))}", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path):
    """A durable_root shaped like a real one: scripts/ beside the other dirs, so
    Path(__file__).parents[1] IS the durable root."""
    durable = tmp_path / "durable"
    (durable / "scripts").mkdir(parents=True)
    (durable / "glossary" / "runs" / "run1").mkdir(parents=True)
    session = tmp_path / "session"
    return {"durable": durable, "scripts": durable / "scripts",
            "session": session, "mod": load_driver(durable / "scripts")}


# ---------------------------------------------------------------------------
# WHERE the channel may live
# ---------------------------------------------------------------------------

def test_verdict_dir_is_required(project):
    """Not defaulted, in either direction. A default under durable_root would put
    the permitting artifact inside the model's write root; a default anywhere else
    would be a path the session does not know it has to protect."""
    with pytest.raises(SystemExit) as exc:
        project["mod"].resolve_verdict_dir(None, project["durable"])
    assert exc.value.code == 2


def test_verdict_dir_inside_durable_root_is_refused(project):
    """The whole point of the channel. Every codex job this driver dispatches can
    write under durable_root, so a verdict there would be forgeable by the very
    jobs it authorizes."""
    inside = project["durable"] / "glossary" / "verdicts"
    with pytest.raises(SystemExit) as exc:
        project["mod"].resolve_verdict_dir(str(inside), project["durable"])
    assert exc.value.code == 2
    assert not inside.exists(), "a refused verdict dir must not be created"


def test_verdict_dir_nested_deep_inside_durable_root_is_refused(project):
    """The containment test is on the RESOLVED path, not on a prefix of the string
    the operator typed -- a nested path is the same violation."""
    inside = project["durable"] / "a" / "b" / "c" / "verdicts"
    with pytest.raises(SystemExit) as exc:
        project["mod"].resolve_verdict_dir(str(inside), project["durable"])
    assert exc.value.code == 2


def test_group_writable_existing_dir_is_refused(project):
    """A reused directory another user can write is not session-owned, whatever
    its name says."""
    d = project["session"]
    d.mkdir(parents=True)
    d.chmod(0o770)
    with pytest.raises(SystemExit) as exc:
        project["mod"].resolve_verdict_dir(str(d), project["durable"])
    assert exc.value.code == 2


def test_world_writable_existing_dir_is_refused(project):
    d = project["session"]
    d.mkdir(parents=True)
    d.chmod(0o707)
    with pytest.raises(SystemExit) as exc:
        project["mod"].resolve_verdict_dir(str(d), project["durable"])
    assert exc.value.code == 2


def test_a_fresh_verdict_dir_is_created_private(project):
    d = project["mod"].resolve_verdict_dir(str(project["session"]),
                                           project["durable"])
    mode = stat.S_IMODE(os.lstat(d).st_mode)
    assert mode == 0o700, f"expected 0700, got {mode:04o}"


def test_a_symlinked_pending_leaf_is_refused_not_followed(project):
    """The precise move the pinned-dirfd + O_NOFOLLOW open exists to stop: a
    symlink planted at pending.json that redirects the write back into the model's
    write root. Refusing is what keeps A8 true; following would place the
    authorization state exactly where it must never be."""
    mod = project["mod"]
    d = mod.resolve_verdict_dir(str(project["session"]), project["durable"])
    target = project["durable"] / "glossary" / "runs" / "run1" / "planted.json"
    (d / mod.PENDING_FILENAME).symlink_to(target)
    with pytest.raises(SystemExit) as exc:
        mod.write_pending(d, {"entries": []})
    assert exc.value.code == 2
    assert not target.exists(), "the write must not have landed on the symlink target"


# ---------------------------------------------------------------------------
# WHAT the channel accepts
# ---------------------------------------------------------------------------

def _pending_entry(mod, durable, snapshot, batch=0, attempt=0, nonce="n0"):
    return {
        "key": mod.pending_key(batch, attempt),
        "durable_root": str(durable), "run_id": "run1",
        "batch": batch, "attempt": attempt, "nonce": nonce,
        "snapshot_sha256": mod._sha256_file(snapshot),
        "ok_sentinel": f"CITATIONS_OK {batch} ATTEMPT {attempt}",
        "fail_sentinel": f"CITATIONS_REJECTED {batch} ATTEMPT {attempt}",
    }


def test_pending_roundtrips_through_the_pinned_open(project):
    mod = project["mod"]
    d = mod.resolve_verdict_dir(str(project["session"]), project["durable"])
    snapshot = project["durable"] / "glossary" / "runs" / "run1" / "approved_0_attempt_0.json"
    snapshot.write_text('[{"source_form": "A"}]', encoding="utf-8")
    entry = _pending_entry(mod, project["durable"], snapshot)
    mod.write_pending(d, {"entries": [entry]})
    back = mod.read_pending(d)
    assert back["entries"] == [entry]


def test_absent_pending_is_not_an_error(project):
    """The first invocation of a run has none; refusing here would make an
    ordinary first run look like tampering."""
    mod = project["mod"]
    d = mod.resolve_verdict_dir(str(project["session"]), project["durable"])
    assert mod.read_pending(d) == {"entries": []}


def test_unreadable_pending_refuses_rather_than_guessing(project):
    """Fails CLOSED. A pending map that cannot be read must not be treated as an
    empty one: empty means "nothing is awaiting a judge", which would let a batch
    that IS awaiting one be re-driven or, worse, be treated as settled."""
    mod = project["mod"]
    d = mod.resolve_verdict_dir(str(project["session"]), project["durable"])
    (d / mod.PENDING_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        mod.read_pending(d)
    assert exc.value.code == 2


def test_nonce_is_unguessable_and_fresh_every_time(project):
    """A batch/attempt tuple is entirely predictable, so the nonce is what makes
    the binding non-forgeable -- and it must differ per PREPARE, because that is
    what invalidates a verdict after a re-fetch."""
    mod = project["mod"]
    seen = {mod.new_nonce() for _ in range(64)}
    assert len(seen) == 64
    assert all(len(n) >= 32 for n in seen)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
