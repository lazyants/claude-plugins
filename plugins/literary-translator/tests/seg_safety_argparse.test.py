"""tests/seg_safety_argparse.test.py -- regression-lock suite for the
segment-id path/shell-injection guard shared by scripts/cache_key.py and
scripts/ledger_update.py.

Both scripts splice a caller-supplied `seg` value into filesystem paths
(`segments/segpack_{seg}.json`, `segments/{seg}.draft.json`,
`segments/{seg}.review.json`) and, downstream, into workflow shell
commands. A malicious `seg` containing `../`, an absolute path, or shell
metacharacters could escape durable_root or inject into the pipeline. Both
scripts now validate `seg` against an identical allowlist
(`validate_seg()`, matching `(FRONTBACK:)?[A-Za-z0-9_]+` via
`re.fullmatch`) immediately after argument parsing, before any path is
built.

Two layers of coverage:

  1. Direct unit tests -- import each script's real, currently-shipped
     `validate_seg()` (via importlib, from its real shipped path, never
     reimplemented) and call it directly with the full accept/reject
     vocabulary, including the re.fullmatch-vs-trailing-newline trap
     ("seg01\\n" -- re.match(r"...$", ...) would wrongly accept this since
     "$" also matches just before a trailing newline; re.fullmatch does
     not).
  2. Integration tests -- invoke each real script as a subprocess with a
     malicious --seg/positional seg and assert a non-zero exit plus an
     error message naming the segment-id problem, using each script's own
     already-established fatal-output convention (cache_key.py: a plain
     "ERROR: ..." line on stderr via its `fail()` helper; ledger_update.py:
     a `{"success": false, "error": ...}` JSON line on stdout via its
     `emit_failure()` helper).
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"
LEDGER_UPDATE_SCRIPT = SCRIPTS_DIR / "ledger_update.py"

assert CACHE_KEY_SCRIPT.is_file(), f"cache_key.py not found at {CACHE_KEY_SCRIPT}"
assert LEDGER_UPDATE_SCRIPT.is_file(), f"ledger_update.py not found at {LEDGER_UPDATE_SCRIPT}"

# The shared accept/reject vocabulary both scripts' validate_seg() must
# agree on.
VALID_SEGS = (
    "seg01",
    "seg0",
    "seg001",
    "segA",
    "segC",
    "segAnchor",
    "seg_alpha",
    "seg01_reusable",
    "seg05_blocked_regen",
    "FRONTBACK:fm01",
    "FRONTBACK:cover",
    "FRONTBACK:back_omit",
)

INVALID_SEGS = (
    "../../etc/passwd",
    "/etc/passwd",
    "seg/../x",
    "seg;rm -rf x",
    "seg 01",
    "FRONTBACK:../x",
    "",
    "seg01\n",  # the re.fullmatch-vs-trailing-newline trap
    "seg\\x",
    "seg|x",
    "seg&x",
    "seg$x",
    "seg`x`",
    "seg(x)",
    "seg<x>",
    "seg*x",
    "seg?x",
    "seg~x",
    "seg#x",
    "seg'x'",
    'seg"x"',
    "seg.x",
    "..",
)


def _load_module(name: str, path: Path):
    """Imports a script fresh from its real shipped path via importlib --
    never reimplemented -- matching this test suite's established pattern
    (see durable_root_reachability.test.py's identical use for
    cache_key.py). Module-level code in both scripts is pure Path
    arithmetic with no file IO, so this is safe without any durable_root
    fixture."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CACHE_KEY_MODULE = _load_module("cache_key_seg_safety_under_test", CACHE_KEY_SCRIPT)
LEDGER_UPDATE_MODULE = _load_module("ledger_update_seg_safety_under_test", LEDGER_UPDATE_SCRIPT)


# ---------------------------------------------------------------------------
# 1. Direct unit tests -- validate_seg() itself, both scripts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seg", VALID_SEGS)
def test_cache_key_validate_seg_accepts_valid(seg):
    assert CACHE_KEY_MODULE.validate_seg(seg) is None


@pytest.mark.parametrize("seg", INVALID_SEGS)
def test_cache_key_validate_seg_rejects_invalid(seg):
    assert CACHE_KEY_MODULE.validate_seg(seg) is not None, (
        f"expected {seg!r} to be rejected"
    )


@pytest.mark.parametrize("seg", VALID_SEGS)
def test_ledger_update_validate_seg_accepts_valid(seg):
    assert LEDGER_UPDATE_MODULE.validate_seg(seg) is None


@pytest.mark.parametrize("seg", INVALID_SEGS)
def test_ledger_update_validate_seg_rejects_invalid(seg):
    assert LEDGER_UPDATE_MODULE.validate_seg(seg) is not None, (
        f"expected {seg!r} to be rejected"
    )


def test_trailing_newline_trap_is_specifically_a_fullmatch_case():
    """Names the trap directly: a naive `re.match(pattern + "$", seg)`
    would wrongly accept "seg01\\n" because "$" also matches just before a
    trailing newline. Both scripts' validate_seg() must reject it."""
    assert CACHE_KEY_MODULE.validate_seg("seg01\n") is not None
    assert LEDGER_UPDATE_MODULE.validate_seg("seg01\n") is not None


# ---------------------------------------------------------------------------
# 2. Integration tests -- real subprocess invocation, malicious seg.
# ---------------------------------------------------------------------------


def make_cache_key_root(tmp_path) -> Path:
    """Minimal fixture: cache_key.py's self-anchoring (`Path(__file__).
    resolve().parents[1]`) only needs the script copied to
    {root}/scripts/cache_key.py -- validate_seg() rejects a malicious --seg
    before any other durable_root file is ever touched, so no further
    fixture content is required."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(CACHE_KEY_SCRIPT, scripts_dir / "cache_key.py")
    return root


def make_ledger_update_root(tmp_path) -> Path:
    """Same minimal-fixture reasoning as make_cache_key_root() above:
    ledger_update.py's validate_seg() check runs immediately after
    argparse, before payload_path is even checked for existence."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(LEDGER_UPDATE_SCRIPT, scripts_dir / "ledger_update.py")
    return root


@pytest.mark.parametrize("seg", INVALID_SEGS)
def test_cache_key_cli_rejects_malicious_seg(tmp_path, seg):
    root = make_cache_key_root(tmp_path)
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "cache_key.py"), "--seg", seg],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"expected a malicious --seg {seg!r} to be rejected, got rc=0\n"
        f"stdout:\n{result.stdout}"
    )
    assert "segment id" in result.stderr, (
        f"expected cache_key.py's fail() to name the segment-id problem on "
        f"stderr for --seg {seg!r}, got stderr:\n{result.stderr}"
    )


@pytest.mark.parametrize("seg", INVALID_SEGS)
def test_ledger_update_cli_rejects_malicious_seg(tmp_path, seg):
    root = make_ledger_update_root(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "ledger_update.py"),
            seg,
            "--payload-file",
            str(tmp_path / "nonexistent-payload.json"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        f"expected a malicious seg {seg!r} to be rejected, got rc=0\n"
        f"stdout:\n{result.stdout}"
    )
    stdout = json.loads(result.stdout.strip())
    assert stdout["success"] is False
    assert "segment id" in stdout["error"], (
        f"expected ledger_update.py's emit_failure() to name the "
        f"segment-id problem for seg {seg!r}, got: {stdout['error']}"
    )
    # Never claims a fragment write that never happened.
    assert "fragment_path" not in stdout
    assert "fragment_sha1" not in stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
