"""tests/canon_validate_plugin_root.test.py -- #412: canon_validate.py's
--plugin-root override for the cache_key.py sibling it shells out to when
STAMPING generation_hashes into canon.json (--init, --restamp-derivation,
--merge-batches, legacy --batch).

## The defect this closes

canon_validate.py is itself Step-0a-copied (not among SKILL.md's four
never-copied plugin-path scripts), so in production its own SCRIPTS_DIR --
where it resolves cache_key.py from -- IS the durable-root copy the codex
process can write to. A tampered cache_key.py there would let a poisoned
copy compute the very hashes that later gate canon reuse
(select_segments.py's derivation-state gate).

## Fixture strategy

Reuses `_canon_project_fixture.py` -- the SAME shared builder
`canon_stamp_conservation.test.py`/`canon_init_zero_candidate_bootstrap.
test.py` already use, staging the REAL cache_key.py (never a stub), so
every question this file asks about `generation_hashes` is about a
GENUINE hash, not a fixture artifact. `--init` is used as the one
operation under test throughout -- the simplest of the four writing modes
that all funnel through the SAME `_stamp_write_verify`/
`_stamp_generation_hash` call chain this override lives in.

## The poisoned-sibling proof, both halves

Per the task brief: the durable root's own copy of cache_key.py is
replaced with a tampered stand-in that always fails loudly and
distinctively (never silently fakes success), then BOTH directions are
asserted: `--plugin-root` pointing at a separate, untampered location
bypasses it, AND omitting the flag genuinely runs the poisoned copy.
Without the second half the first proves nothing -- a script that never
touched cache_key.py at all would look identical to one that correctly
routed around the poison.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _canon_project_fixture import (  # noqa: E402
    SCRIPTS_SRC,
    make_project,
    read_canon,
    run_canon_validate,
    run_script,
)


def parse_last_json_line(proc) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"expected at least one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# The poisoned-sibling fixture -- same shape select_segments.test.py/
# ledger_merge.test.py already use for their own --plugin-root batteries.
# ---------------------------------------------------------------------------

_TAMPERED_CACHE_KEY_SRC = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "sys.stderr.write('TAMPERED_CACHE_KEY_MUST_NEVER_RUN')\n"
    "sys.exit(97)\n"
)


def poison_durable_root_cache_key(root: Path) -> None:
    """Overwrites the durable-root copy of cache_key.py with a stand-in for
    a codex-tampered script: it always fails loudly and distinctively
    rather than silently faking success, so a test can tell whether THIS
    copy ran at all, in either direction."""
    (root / "scripts" / "cache_key.py").write_text(_TAMPERED_CACHE_KEY_SRC, encoding="utf-8")


def make_trusted_plugin_root(tmp_path, name="trusted_plugin_install") -> Path:
    """A SEPARATE physical location holding the REAL cache_key.py at the
    {plugin_root}/assets/scripts/ layout SKILL.md documents for the
    plugin-anchored scripts -- standing in for the plugin's actual install
    tree, physically apart from any durable_root fixture."""
    plugin_root = tmp_path / name
    plugin_scripts_dir = plugin_root / "assets" / "scripts"
    plugin_scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPTS_SRC / "cache_key.py", plugin_scripts_dir / "cache_key.py")
    return plugin_root


# ---------------------------------------------------------------------------
# Measured proof of the forwarding asymmetry -- not asserted from the task
# brief's sentence, from actually running the leaf against the flag.
# ---------------------------------------------------------------------------

def test_cache_key_py_itself_rejects_plugin_root(tmp_path):
    """cache_key.py is a LEAF: it has no siblings of its own to resolve, and
    does not accept --plugin-root at all. This is why canon_validate.py
    must never forward the flag to it -- confirmed here by actually running
    the real cache_key.py against it, not by trusting the claim."""
    root = make_project(tmp_path)

    proc = run_script(
        root, "cache_key.py", "--field", "particle_config_hash",
        "--plugin-root", "/nonexistent",
    )

    assert proc.returncode == 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "unrecognized arguments" in proc.stderr
    assert "--plugin-root" in proc.stderr


# ---------------------------------------------------------------------------
# #412 -- --plugin-root PATH, both halves of the poisoned-sibling proof.
# ---------------------------------------------------------------------------

def test_plugin_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility: the ordinary in-place fixture, invoked with
    no --plugin-root at all, behaves exactly as before."""
    root = make_project(tmp_path)

    proc = run_canon_validate(root, "--init")

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_last_json_line(proc)
    assert payload["success"] is True
    assert payload["created"] is True
    assert payload["generation_hashes_restamped"] is True


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling(tmp_path):
    """The core security property: canon_validate.py runs from its own
    in-place durable-root copy whose SIBLING cache_key.py has been
    POISONED. --plugin-root pointing at a separate, untampered location
    must make it use THAT cache_key.py instead -- a genuine stamp is
    possible ONLY if the poisoned durable-root sibling was never
    executed."""
    root = make_project(tmp_path)
    poison_durable_root_cache_key(root)
    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = run_canon_validate(root, "--init", "--plugin-root", str(plugin_root))

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL cache_key.py must succeed "
        f"even though durable_root's own copy is poisoned:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_last_json_line(proc)
    assert payload["success"] is True
    assert payload["created"] is True
    assert payload["generation_hashes_restamped"] is True

    canon = read_canon(root)
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        value = canon["generation_hashes"].get(field)
        assert isinstance(value, str) and value, (
            f"expected a genuine non-empty stamp for {field!r}, got {value!r}"
        )


def test_plugin_root_flag_absent_uses_the_poisoned_durable_root_sibling(tmp_path):
    """Negative control, and backward-compat proof in one: the SAME
    poisoned durable-root cache_key.py, invoked WITHOUT --plugin-root, is
    exactly what today's self-anchored lookup finds -- unchanged. The
    poisoned script genuinely runs and fails when the flag is omitted,
    proving the positive test's success above is attributable to
    --plugin-root specifically, not some other effect. Also proves the
    write is refused wholesale (no half-written canon.json) rather than
    silently completing with a corrupted stamp."""
    root = make_project(tmp_path)
    poison_durable_root_cache_key(root)

    proc = run_canon_validate(root, "--init")  # no --plugin-root

    assert proc.returncode == 1, (
        f"the poisoned cache_key.py must actually run and cause the write "
        f"to fail when --plugin-root is omitted:\n"
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    payload = parse_last_json_line(proc)
    assert payload["success"] is False
    assert "TAMPERED_CACHE_KEY_MUST_NEVER_RUN" in payload["error"]
    assert not (root / "canon.json").exists(), (
        "a failed stamp attempt must write NOTHING -- generation_hashes is "
        "resolved before the atomic write, never patched in afterward"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
