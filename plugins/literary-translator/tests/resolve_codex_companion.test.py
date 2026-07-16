"""Tests for assets/scripts/resolve_codex_companion.py (#198, v1.4.7; PLAN-198 §2.2/§4).

Covers: zero candidates -> nonzero; newest-semver pick (1.0.10 > 1.0.9 > 1.0.6);
node/companion `status` failing OR hanging past the finite timeout -> nonzero; success
-> {"companion_path": "<raw>"} + exit 0; injection-safety (single-quote / control / newline
/ NUL / relative / wrong-basename REJECTED); compatibility (a legitimate space / non-ASCII
path ACCEPTED, its json.dumps is a valid JS literal, and a single-quoted-bash round-trip
recovers it exactly).

The resolver is plugin-anchored and never copied to a durable_root; these tests drive it as
a subprocess with an explicit --search-glob (so the real ~/.claude* store is never touched)
and a fake executable `node` stub, plus white-box calls into its pure helpers.
"""

import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
RESOLVER_SRC = SCRIPTS_DIR / "resolve_codex_companion.py"

assert RESOLVER_SRC.is_file(), f"expected the resolver at {RESOLVER_SRC}"

_spec = importlib.util.spec_from_file_location("resolve_codex_companion_mod", str(RESOLVER_SRC))
assert _spec is not None and _spec.loader is not None
rcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rcc)


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def make_companion(base: Path, ver: str, parent: str = "profileA") -> Path:
    """Create a fake codex-companion.mjs at .../openai-codex/codex/<ver>/scripts/."""
    d = base / parent / "plugins" / "cache" / "openai-codex" / "codex" / ver / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "codex-companion.mjs"
    f.write_text("// fake companion\n", encoding="utf-8")
    return f


def write_fake_node(tmp: Path, body: str) -> str:
    node = tmp / "fake_node.py"
    node.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    node.chmod(node.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(node)


# A node that succeeds on any invocation.
FAKE_NODE_OK = "import sys\nsys.exit(0)\n"
# A node that fails (non-zero) on any invocation.
FAKE_NODE_FAIL = "import sys\nsys.stderr.write('boom')\nsys.exit(1)\n"
# A node that hangs (sleeps) so the resolver's finite timeout must fire.
FAKE_NODE_HANG = "import time\ntime.sleep(30)\n"


def run_resolver(root: Path, glob_pat: str, node: str, timeout_sec: int = 3):
    return subprocess.run(
        [sys.executable, str(RESOLVER_SRC), "--durable-root", str(root),
         "--search-glob", glob_pat, "--node", node, "--timeout-sec", str(timeout_sec)],
        capture_output=True, text=True, timeout=60,
    )


# --------------------------------------------------------------------------- #
# white-box: pure helpers
# --------------------------------------------------------------------------- #
def test_version_key_is_numeric_not_lexical():
    a = "/x/openai-codex/codex/1.0.9/scripts/codex-companion.mjs"
    b = "/x/openai-codex/codex/1.0.10/scripts/codex-companion.mjs"
    assert rcc._version_key(b) > rcc._version_key(a)  # 1.0.10 > 1.0.9 numerically


def test_pick_newest_semver():
    paths = [
        "/x/openai-codex/codex/1.0.6/scripts/codex-companion.mjs",
        "/x/openai-codex/codex/1.0.10/scripts/codex-companion.mjs",
        "/x/openai-codex/codex/1.0.9/scripts/codex-companion.mjs",
    ]
    assert rcc.pick_newest(paths).endswith("/1.0.10/scripts/codex-companion.mjs")


@pytest.mark.parametrize("bad", [
    "rel/codex-companion.mjs",                       # not absolute
    "/abs/dir/other.mjs",                            # wrong basename
    "/abs/dir/codex-companion.mjs\n",               # newline
    "/abs/di\x00r/codex-companion.mjs",             # NUL
    "/abs/di\x07r/codex-companion.mjs",             # control char (BEL)
    "/abs/di'r/codex-companion.mjs",                # single quote
])
def test_path_is_safe_rejects(bad):
    assert rcc.path_is_safe(bad) is not None


@pytest.mark.parametrize("good", [
    "/Users/José/x/codex-companion.mjs",            # non-ASCII accepted
    "/Users/My Name/x/codex-companion.mjs",         # space accepted
    "/Users/a/codex-companion.mjs",
])
def test_path_is_safe_accepts_legit(good):
    assert rcc.path_is_safe(good) is None


# --------------------------------------------------------------------------- #
# integration: subprocess
# --------------------------------------------------------------------------- #
def test_zero_candidates_nonzero(tmp_path):
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    glob_pat = str(tmp_path / "nothing" / "**" / "codex-companion.mjs")
    proc = run_resolver(tmp_path, glob_pat, node)
    assert proc.returncode != 0
    assert "no codex-companion.mjs" in proc.stderr


def test_success_picks_newest_semver(tmp_path):
    make_companion(tmp_path, "1.0.6")
    make_companion(tmp_path, "1.0.9")
    newest = make_companion(tmp_path, "1.0.10")
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    glob_pat = str(tmp_path / "**" / "codex-companion.mjs")
    proc = run_resolver(tmp_path, glob_pat, node)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["companion_path"] == str(newest)


def test_node_status_failure_nonzero(tmp_path):
    make_companion(tmp_path, "1.0.10")
    node = write_fake_node(tmp_path, FAKE_NODE_FAIL)
    glob_pat = str(tmp_path / "**" / "codex-companion.mjs")
    proc = run_resolver(tmp_path, glob_pat, node)
    assert proc.returncode != 0
    assert "status" in proc.stderr


def test_node_hang_bounded_by_timeout(tmp_path):
    make_companion(tmp_path, "1.0.10")
    node = write_fake_node(tmp_path, FAKE_NODE_HANG)
    glob_pat = str(tmp_path / "**" / "codex-companion.mjs")
    import time
    t0 = time.monotonic()
    proc = run_resolver(tmp_path, glob_pat, node, timeout_sec=1)
    elapsed = time.monotonic() - t0
    assert proc.returncode != 0
    assert "timed out" in proc.stderr
    assert elapsed < 15  # the finite timeout fired, we did NOT wait the full 30s sleep


def test_unsafe_newest_path_rejected(tmp_path):
    """A newest candidate whose path contains a single quote is REJECTED (no fallback to an
    older safe one -- an unsafe resolved path is a hard fail-fast)."""
    make_companion(tmp_path, "1.0.6")                       # safe, older
    make_companion(tmp_path, "1.0.99", parent="pro'file")   # newest, unsafe (quote in path)
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    glob_pat = str(tmp_path / "**" / "codex-companion.mjs")
    proc = run_resolver(tmp_path, glob_pat, node)
    assert proc.returncode != 0
    assert "unsafe companion path" in proc.stderr


def test_legit_space_and_nonascii_path_accepted_and_roundtrips(tmp_path):
    """A legitimate install path with a space AND a non-ASCII char is ACCEPTED; its
    json.dumps is a valid JS string literal that recovers the exact path, and inside a
    single-quoted bash argument it survives verbatim (no injection, exact recovery)."""
    newest = make_companion(tmp_path, "1.0.7", parent="My Náme dir")
    node = write_fake_node(tmp_path, FAKE_NODE_OK)
    glob_pat = str(tmp_path / "**" / "codex-companion.mjs")
    proc = run_resolver(tmp_path, glob_pat, node)
    assert proc.returncode == 0, proc.stderr
    raw = json.loads(proc.stdout)["companion_path"]
    assert raw == str(newest)
    assert " " in raw and any(ord(c) > 127 for c in raw)

    # The orchestrator json.dumps-encodes the raw path into a JS string literal ...
    js_literal = json.dumps(raw)
    assert json.loads(js_literal) == raw            # valid, exact round-trip
    assert "'" not in raw                           # ... and the value is single-quote-safe,

    # ... so a single-quoted bash argument recovers it byte-for-byte (no word-splitting,
    # no injection). printf '%s' '<raw>' echoes the path unchanged.
    echoed = subprocess.run(
        ["bash", "-c", "printf %s " + "'" + raw + "'"],
        capture_output=True, text=True,
    )
    assert echoed.returncode == 0
    assert echoed.stdout == raw


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
