"""tests/seg_safety_positional.test.py -- regression-lock suite for the
segment-id safety allowlist shared by draft_sha1.py, draft_ready.py, and
validate_draft.py -- the three scripts that splice a manifest-supplied `seg`
string directly into filesystem paths (segments/{seg}.draft.json,
segments/segpack_{seg}.json) via a positional CLI argv, before any other
validation runs.

The vulnerability this locks shut: a malicious/custom extractor can emit a
`seg` value containing '../', an absolute path, or shell metacharacters,
letting a downstream consumer escape durable_root or inject into a shell
pipeline. The fix is `validate_seg()`, a byte-identical helper duplicated
into each of the three scripts (see each script's own module-level comment
above its definition), called immediately after the seg value is read off
sys.argv -- BEFORE any path is built.

Two levels of coverage:

1. Subprocess/integration: each of the three REAL shipped scripts, invoked
   exactly as production invokes them (`python3 <script> SEG`), must REJECT
   every malicious seg in MALICIOUS_SEGS with a non-zero exit and a stderr
   message naming the segment-id contract -- never silently building a path
   out of the raw hostile string.
2. Unit: `validate_seg()` loaded directly (via importlib, by file identity)
   out of each of the three REAL shipped modules, asserting ACCEPT for every
   valid seg shape the manifest/frontback vocabulary actually uses, and
   REJECT for the same malicious vocabulary plus the fullmatch-vs-'$' trap:
   a trailing newline ("seg01\\n") must be rejected, because re.match(r"...$",
   ...) -- unlike re.fullmatch -- also matches just before a trailing
   newline, so a naive re.match+"$" implementation would wrongly let it
   through.

Plus one cross-script consistency check: since the helper is specified as
BYTE-IDENTICAL across all three consuming scripts, the three loaded
implementations must agree on accept/reject for every seg in the shared
vocabulary -- not just each pass individually.
"""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

SCRIPT_NAMES = ["draft_sha1.py", "draft_ready.py", "validate_draft.py"]
SCRIPT_SRCS = {name: SCRIPTS_DIR / name for name in SCRIPT_NAMES}

for _name, _path in SCRIPT_SRCS.items():
    assert _path.is_file(), f"{_name} not found at {_path}"


# ---------------------------------------------------------------------------
# Shared vocabulary -- kept in one place so every coverage level below agrees
# on what is malicious vs. valid.
# ---------------------------------------------------------------------------

MALICIOUS_SEGS = [
    "../../etc/passwd",
    "/etc/passwd",
    "seg/../x",
    "seg;rm -rf x",
    "seg 01",
    "seg`whoami`",
    "FRONTBACK:../x",
    "",
    "seg01\n",  # the re.fullmatch-vs-"$" trap -- see module docstring.
]

VALID_SEGS = [
    "seg01", "seg02", "seg0", "seg001", "segA", "segC", "segAnchor",
    "seg_alpha", "seg01_reusable", "seg05_blocked_regen",
    "FRONTBACK:fm01", "FRONTBACK:cover", "FRONTBACK:back_omit",
    "FRONTBACK:never_dispatched",
]


def _seg_id(seg):
    return repr(seg)


# ---------------------------------------------------------------------------
# Level 1: subprocess/integration -- the REAL shipped scripts, invoked
# exactly as production invokes them, must refuse to touch a hostile seg.
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path, script_name):
    """Isolated durable_root: copies the REAL script into {root}/scripts/ so
    its self-anchoring `Path(__file__).resolve().parents[1]` resolves to
    THIS temp root, exactly matching production. Not actually exercised by
    the seg-rejection tests below (validate_seg fires before any path or
    profile lookup), but built anyway to mirror the sibling tests' fixture
    shape and to prove rejection holds even when a real segments/ dir --
    and, incidentally, nothing a path-traversal payload could reach -- sits
    right next to the script."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    (root / "segments").mkdir()
    shutil.copy2(SCRIPT_SRCS[script_name], scripts_dir / script_name)
    return root


def run_script(root, script_name, seg):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / script_name), seg],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
@pytest.mark.parametrize("seg", MALICIOUS_SEGS, ids=_seg_id)
def test_malicious_seg_rejected_by_real_script(tmp_path, script_name, seg):
    root = make_durable_root(tmp_path, script_name)

    result = run_script(root, script_name, seg)

    assert result.returncode != 0, (
        f"{script_name} must refuse malicious seg {seg!r}, got rc=0\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "segment id" in result.stderr.lower(), (
        f"{script_name}: expected the segment-id contract violation named "
        f"in stderr for malicious seg {seg!r}, got stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Level 2: unit -- validate_seg() loaded by file identity out of each REAL
# shipped module (never a reimplementation that could silently drift from
# the real logic).
# ---------------------------------------------------------------------------

def _load_module(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEG_MODULES = {
    name: _load_module(path, f"{name[:-len('.py')]}_seg_safety_under_test")
    for name, path in SCRIPT_SRCS.items()
}


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
@pytest.mark.parametrize("seg", VALID_SEGS)
def test_valid_seg_accepted_by_validate_seg(script_name, seg):
    validate_seg = SEG_MODULES[script_name].validate_seg
    assert validate_seg(seg) is None, (
        f"{script_name}: validate_seg({seg!r}) should ACCEPT (return None), "
        f"got {validate_seg(seg)!r}"
    )


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
@pytest.mark.parametrize("seg", MALICIOUS_SEGS, ids=_seg_id)
def test_malicious_seg_rejected_by_validate_seg(script_name, seg):
    validate_seg = SEG_MODULES[script_name].validate_seg
    assert validate_seg(seg) is not None, (
        f"{script_name}: validate_seg({seg!r}) should REJECT (return a "
        f"non-None error string), got None"
    )


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
def test_trailing_newline_trap(script_name):
    """The specific re.fullmatch-vs-'$' trap, isolated from the rest of
    MALICIOUS_SEGS: 'seg01' alone must ACCEPT, but 'seg01\\n' -- a trailing
    newline appended to an otherwise-valid seg -- must REJECT. A
    re.match(r"(?:FRONTBACK:)?[A-Za-z0-9_]+$", seg) implementation would
    WRONGLY accept the newline-suffixed form, since '$' also matches just
    before a trailing newline; only re.fullmatch is safe here."""
    validate_seg = SEG_MODULES[script_name].validate_seg
    assert validate_seg("seg01") is None
    assert validate_seg("seg01\n") is not None


# ---------------------------------------------------------------------------
# Cross-script consistency: the helper is specified as BYTE-IDENTICAL across
# all three consuming scripts -- prove the three loaded implementations
# actually agree on every seg in the shared vocabulary.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seg", VALID_SEGS + MALICIOUS_SEGS, ids=_seg_id)
def test_validate_seg_agrees_across_all_three_scripts(seg):
    results = {
        name: SEG_MODULES[name].validate_seg(seg) is None
        for name in SCRIPT_NAMES
    }
    assert len(set(results.values())) == 1, (
        f"validate_seg({seg!r}) disagrees across scripts (accept/reject "
        f"should be identical since the helper is BYTE-IDENTICAL): {results}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
