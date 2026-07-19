"""Parity gate: canon_senses.py's own ``_path_state`` and
suspicion_scan.py's own ``_frozen_input_path_state`` MUST classify every
path identically.

Why this test exists: the two functions are a DELIBERATE duplicate --
suspicion_scan.py's own docstring on `_frozen_input_path_state` says so
explicitly, mirroring this plugin's existing "duplicate a tiny primitive
rather than import a sibling module's private name" convention (e.g.
`skeptic_setup.py`'s own `RUN_ID_RE`). That is a defensible choice (it
preserves each module's own leaf-dependency property rather than adding a
cross-module import), but it also means nothing short of a test binds the
two bodies together going forward. The stakes are no longer cosmetic:
`suspicion_scan.compute_frozen_input_hash` folds `_frozen_input_path_state`'s
own state tag directly into H1's tamper-detection hash (#243/#227) --
`canon_senses._path_state` does not feed a hash today, but a silent
divergence between the two (someone "fixing" one without noticing its twin)
would change what a REAL security-relevant hash means with no test anywhere
failing. This file's whole job is closing that hole: it asserts the two
functions AGREE on every state, not merely that each classifies correctly
on its own -- an independent pin of each one's expected value would not
catch a mutation that broke both identically, which is exactly the failure
mode a "duplicate, not import" design is most exposed to.

Standalone-script loader mirrors tests/occ_index.test.py:1-45 /
tests/suspicion_scan.test.py's own ``_load_module`` (suspicion_scan.py does
top-level ``from X import ...`` for its sibling scripts, so SCRIPTS_DIR must
be on sys.path around its load; canon_senses.py has no sibling imports and
needs no such scaffolding, mirroring tests/canon_senses.test.py's own
loader).
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
CANON_SENSES_SCRIPT = SCRIPTS_DIR / "canon_senses.py"
SUSPICION_SCAN_SCRIPT = SCRIPTS_DIR / "suspicion_scan.py"

assert CANON_SENSES_SCRIPT.is_file(), f"canon_senses.py not found at {CANON_SENSES_SCRIPT}"
assert SUSPICION_SCAN_SCRIPT.is_file(), f"suspicion_scan.py not found at {SUSPICION_SCAN_SCRIPT}"


def _load_module(name: str, path: Path, extra_sys_path: "Path | None" = None):
    if extra_sys_path is not None:
        sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if extra_sys_path is not None:
            sys.path.remove(str(extra_sys_path))


cs = _load_module("canon_senses_for_parity_test", CANON_SENSES_SCRIPT)
ss = _load_module("suspicion_scan_for_parity_test", SUSPICION_SCAN_SCRIPT, SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixture builders -- one per path state the codex review named explicitly:
# absent, regular, directory, symlink-to-regular-file, dangling symlink.
# ---------------------------------------------------------------------------


def _absent(tmp_path) -> Path:
    return tmp_path / "does_not_exist.txt"


def _regular(tmp_path) -> Path:
    p = tmp_path / "regular.txt"
    p.write_text("some frozen-input content", encoding="utf-8")
    return p


def _directory(tmp_path) -> Path:
    p = tmp_path / "a_directory"
    p.mkdir()
    return p


def _symlink_to_regular_file(tmp_path) -> Path:
    target = tmp_path / "symlink_target.txt"
    target.write_text("target content", encoding="utf-8")
    link = tmp_path / "symlink_to_regular.txt"
    os.symlink(target, link)
    return link


def _dangling_symlink(tmp_path) -> Path:
    missing_target = tmp_path / "never_created.txt"
    link = tmp_path / "dangling_symlink.txt"
    os.symlink(missing_target, link)
    return link


CASES = {
    "absent": (_absent, "absent"),
    "regular": (_regular, "regular"),
    "directory": (_directory, "irregular"),
    "symlink_to_regular_file": (_symlink_to_regular_file, "regular"),
    "dangling_symlink": (_dangling_symlink, "irregular"),
}


@pytest.mark.parametrize("case_name", sorted(CASES))
def test_path_state_classifiers_agree(tmp_path, case_name):
    """Fails if canon_senses._path_state and
    suspicion_scan._frozen_input_path_state ever return DIFFERENT values
    for the same path -- the mutation this guards against is exactly a
    one-sided edit to either function's own body (e.g. someone "fixing" a
    symlink-to-directory edge case in only one copy)."""
    build, expected = CASES[case_name]
    path = build(tmp_path)

    cs_state = cs._path_state(path)
    ss_state = ss._frozen_input_path_state(path)

    assert cs_state == ss_state, (
        f"canon_senses._path_state({path!r}) = {cs_state!r} but "
        f"suspicion_scan._frozen_input_path_state({path!r}) = {ss_state!r} -- "
        f"the two classifiers have DIVERGED"
    )
    # Also pin the expected value itself -- agreement alone would not catch
    # both functions drifting IDENTICALLY away from the correct answer.
    assert cs_state == expected, f"expected {expected!r} for case {case_name!r}, got {cs_state!r}"
