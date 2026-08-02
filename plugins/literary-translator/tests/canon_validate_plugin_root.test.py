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
GENUINE hash, not a fixture artifact. `--init` is used as the simplest of
the four writing modes to establish the proof shape below; the same shape
is then re-run for the other three (`--restamp-derivation`, legacy
`--batch` / `run_merge`, and `--merge-batches`) further down, since all
four funnel through the SAME `_stamp_write_verify`/`_stamp_generation_hash`
call chain this override lives in -- and, until this file grew these
extra cases, only `--init`'s own leg of that chain was ever exercised
with `--plugin-root` at all.

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
    accepted_item,
    make_project,
    read_canon,
    run_canon_init,
    run_canon_validate,
    run_script,
    write_fragment,
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


# ---------------------------------------------------------------------------
# #412 gap closure -- the same poisoned-sibling proof for the other three
# `_stamp_write_verify` callers. Until now only `--init` (above) exercised
# `--plugin-root` at all: a whole-suite search found `--plugin-root` next to
# `--restamp-derivation`, `--batch`, or `--merge-batches` in NO test file.
# `resolve_cache_key_script`/`_stamp_generation_hash` are shared code, but
# each of these three threads its OWN `plugin_root_str` parameter through
# from `main()`'s dispatch (canon_validate.py:2609-2646) to its own
# `_stamp_write_verify` call -- a regression in any one of those three
# threading sites would not be caught by the `--init` test above.
# ---------------------------------------------------------------------------


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling_on_restamp_derivation(tmp_path):
    """Same core property as the --init test above, for `run_restamp_derivation`
    (~:2030). This mode requires an EXISTING canon.json, so bootstrap happens
    BEFORE the durable root is poisoned -- --init itself needs a working
    cache_key.py to succeed."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    poison_durable_root_cache_key(root)
    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = run_canon_validate(root, "--restamp-derivation", "--plugin-root", str(plugin_root))

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL cache_key.py must let "
        f"--restamp-derivation succeed even though durable_root's own copy "
        f"is poisoned:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_last_json_line(proc)
    assert payload["success"] is True
    assert payload["mode"] == "restamp_derivation"
    assert payload["generation_hashes_restamped"] is True

    canon = read_canon(root)
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        value = canon["generation_hashes"].get(field)
        assert isinstance(value, str) and value, (
            f"expected a genuine non-empty stamp for {field!r}, got {value!r}"
        )


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling_on_merge(tmp_path):
    """Same proof for `run_merge` (legacy `--batch`, ~:2092). No prior
    canon.json is needed: `_load_canon` returns a fresh skeleton for a
    missing file, and `_preservable_prior` returns None for a missing file
    too, so this first merge always restamps -- exactly like --init's own
    bootstrap does. A real accepted item (not an empty fragment) is used so
    this also proves the override is honored on a merge that actually
    writes content, not just on a content-free write."""
    root = make_project(tmp_path)
    poison_durable_root_cache_key(root)
    plugin_root = make_trusted_plugin_root(tmp_path)
    frag = write_fragment(root, [accepted_item("אברהם", "Abraham")])

    proc = run_canon_validate(root, "--batch", str(frag), "--plugin-root", str(plugin_root))

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL cache_key.py must let a legacy "
        f"--batch merge succeed even though durable_root's own copy is "
        f"poisoned:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_last_json_line(proc)
    assert payload["success"] is True
    assert payload["mode"] == "merge"
    assert payload["merged_accepted"] == 1
    assert payload["generation_hashes_restamped"] is True

    canon = read_canon(root)
    assert "אברהם" in canon["entries"]
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        value = canon["generation_hashes"].get(field)
        assert isinstance(value, str) and value, (
            f"expected a genuine non-empty stamp for {field!r}, got {value!r}"
        )


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling_on_merge_batches(tmp_path):
    """Same proof for `run_merge_batches` (~:2201), across TWO fragments in
    one call -- its distinguishing shape from legacy --batch, and the mode
    #193/#291 name as the modern replacement for the unsanctioned restamp
    trick."""
    root = make_project(tmp_path)
    poison_durable_root_cache_key(root)
    plugin_root = make_trusted_plugin_root(tmp_path)
    frag1 = write_fragment(root, [accepted_item("אברהם", "Abraham")], "f1.json")
    frag2 = write_fragment(root, [accepted_item("רבקה", "Rebecca")], "f2.json")

    proc = run_canon_validate(
        root, "--merge-batches", str(frag1), str(frag2), "--plugin-root", str(plugin_root)
    )

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL cache_key.py must let "
        f"--merge-batches succeed even though durable_root's own copy is "
        f"poisoned:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_last_json_line(proc)
    assert payload["success"] is True
    assert payload["mode"] == "merge_batches"
    assert payload["generation_hashes_restamped"] is True

    canon = read_canon(root)
    assert "אברהם" in canon["entries"] and "רבקה" in canon["entries"]
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        value = canon["generation_hashes"].get(field)
        assert isinstance(value, str) and value, (
            f"expected a genuine non-empty stamp for {field!r}, got {value!r}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
