"""tests/heading_like_regex_drift.test.py

Locks byte-identical duplication of the broad heading-like allowlist regex
(#210) across the two scripts that each independently define their own copy
-- ``validate_assembled.py`` (the original, WARN-only backstop,
``collect_undeclared_heading_like_warnings``) and ``validate_extraction.py``
(the new HARD W2 gate, #210 D2,
``heading_types_declared_when_heading_shaped_blocks_exist``). This plugin's
standing rule for cross-cutting helpers is "no shared util module -- they are
duplicated BYTE-IDENTICALLY, guarded by a drift test" (there is no
``heading_utils.py`` or similar shared module); this file is that drift test
for ``BROAD_HEADING_LIKE_RE``.

Both scripts are imported fresh from their real, shipped install paths (never
copied to a durable_root, mirroring how each script is actually invoked) so
this test reads the ACTUAL shipped pattern, never a hand-retyped copy of it.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
VALIDATE_EXTRACTION_PATH = SCRIPTS_DIR / "validate_extraction.py"
VALIDATE_ASSEMBLED_PATH = SCRIPTS_DIR / "validate_assembled.py"


def _load_module(name: str, path: Path):
    assert path.is_file(), f"expected a real script at {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ve = _load_module("validate_extraction_under_test_for_regex_drift", VALIDATE_EXTRACTION_PATH)
va = _load_module("validate_assembled_under_test_for_regex_drift", VALIDATE_ASSEMBLED_PATH)


def test_both_scripts_define_the_regex():
    assert hasattr(ve, "BROAD_HEADING_LIKE_RE")
    assert hasattr(va, "BROAD_HEADING_LIKE_RE")


def test_pattern_literal_is_byte_identical_across_both_scripts():
    """The heart of the drift guard: the raw pattern STRING -- the literal
    argument each script passes to ``re.compile`` -- must match exactly. A
    compiled pattern's own ``.pattern`` attribute IS that literal, evaluated
    (raw-string escaping already applied), so this is a byte-for-byte
    comparison of the two duplicated copies, not merely a behavioral one."""
    assert ve.BROAD_HEADING_LIKE_RE.pattern == va.BROAD_HEADING_LIKE_RE.pattern


def test_flags_are_identical_across_both_scripts():
    # Both copies must compile with the SAME flags (re.IGNORECASE) -- a copy
    # that silently dropped IGNORECASE would stop matching e.g. "chapter"
    # while the sibling copy still did, which the pattern-string check alone
    # would not catch (flags live outside .pattern).
    assert ve.BROAD_HEADING_LIKE_RE.flags == va.BROAD_HEADING_LIKE_RE.flags


@pytest.mark.parametrize("raw_type,expected", [
    ("CHAPTER", True), ("chapter", True), ("HEADING", True), ("TITLE", True),
    ("SECTION", True), ("SIMAN", True), ("PEREK", True), ("H3", True),
    ("HEAD", False), ("PARTICLE", False), ("PARA", False), ("PART", True),
])
def test_both_copies_agree_on_every_sample_type(raw_type, expected):
    """Behavioral cross-check on top of the literal-identity check above --
    two byte-identical patterns cannot disagree in practice, but this pins
    the CONTENT of the shared behavior too (belt-and-suspenders against a
    future edit to this test file silently losing its own bite). Also proves
    fullmatch-not-substring ("PARTICLE" must NOT trip "PART") and
    case-insensitivity ("chapter") for BOTH copies at once, and that "HEAD"
    -- the always-heading built-in -- never matches this broad allowlist."""
    assert bool(ve.BROAD_HEADING_LIKE_RE.fullmatch(raw_type)) == expected
    assert bool(va.BROAD_HEADING_LIKE_RE.fullmatch(raw_type)) == expected


def test_drift_check_is_not_vacuous_a_mutated_pattern_is_caught():
    """Mutation-proof (per schema-gate-hardening's "prove both directions"):
    the identity assertion above must be a REAL discriminator, not a
    comparison that is vacuously true (e.g. a bug comparing lengths only, or
    comparing a value against itself). A one-character mutation of the real,
    shipped pattern -- widening H[1-6] to H[1-7] -- must compare UNEQUAL to
    the sibling script's real pattern, proving the equality check above would
    actually catch a genuine divergence between the two copies."""
    real = ve.BROAD_HEADING_LIKE_RE.pattern
    mutated = real.replace("H[1-6]", "H[1-7]")
    assert mutated != real, "the replace() did not change anything -- fix this fixture"
    assert mutated != va.BROAD_HEADING_LIKE_RE.pattern


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
