"""tests/extractor_selfcheck_hash_drift.test.py

Locks the #86 SELF-CHECK REGION pin: ``extract.py.template``'s round-trip
self-check suite (``run_self_checks``) is wrapped in exact BEGIN/END sentinel
lines, and its normalized SHA-1 is pinned by ``validate_extraction.py``'s
``CURRENT_EXTRACTOR_SELFCHECK_HASH`` constant. Editing a self-check to reach
green (a false-green anti-pattern) shifts the hash and trips both this drift
test and the post-extraction ``validate_extraction.py`` gate.

Normalization is NOT reimplemented here -- ``selfcheck_region_hash`` and the
sentinel prefixes are imported from ``validate_extraction`` (T3's module), so
this test and the runtime gate agree by construction.

Cross-team dependency (see the 1.2.0 build contract, §Hash protocol): this file
imports ``validate_extraction`` (owned by teammate LT-Gate) and compares against
``CURRENT_EXTRACTOR_SELFCHECK_HASH`` (filled by the LEAD after the sentinel
region is finalized). Until that module exists AND the hash is filled, this file
error-collects / ``test_region_hash_matches_pinned_constant`` fails -- EXPECTED,
and resolved by the LEAD's hash-fill. No hash value is hardcoded here.

Collection note: run with ``python3 -m pytest --import-mode=importlib
tests/extractor_selfcheck_hash_drift.test.py`` (configured project-wide via
pytest.ini).
"""
import ast
import importlib.util
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
TEMPLATE_PATH = SKILL_ROOT / "assets" / "templates" / "extract.py.template"
VALIDATE_EXTRACTION_PATH = SKILL_ROOT / "assets" / "scripts" / "validate_extraction.py"

assert TEMPLATE_PATH.is_file(), f"extract.py.template not found at {TEMPLATE_PATH}"

TEMPLATE_TEXT = TEMPLATE_PATH.read_text(encoding="utf-8")


def _load_validate_extraction():
    """Imports validate_extraction.py fresh from its real, shipped install path
    under assets/scripts/ (not a package on sys.path). Owned by teammate
    LT-Gate; until it exists this raises, error-collecting this file."""
    spec = importlib.util.spec_from_file_location(
        "validate_extraction_under_test", VALIDATE_EXTRACTION_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"could not load spec for {VALIDATE_EXTRACTION_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ve = _load_validate_extraction()


def _sentinel_line_indices():
    lines = TEMPLATE_TEXT.split("\n")
    begins = [i for i, ln in enumerate(lines) if ln.startswith(ve.BEGIN_SENTINEL_PREFIX)]
    ends = [i for i, ln in enumerate(lines) if ln.startswith(ve.END_SENTINEL_PREFIX)]
    return lines, begins, ends


def test_exactly_one_begin_and_one_end_sentinel():
    _lines, begins, ends = _sentinel_line_indices()
    assert len(begins) == 1, f"expected exactly one BEGIN sentinel, found {len(begins)}"
    assert len(ends) == 1, f"expected exactly one END sentinel, found {len(ends)}"
    assert begins[0] < ends[0], "BEGIN sentinel must precede END sentinel"


def test_region_hash_matches_pinned_constant():
    """The heart of the pin: the template's live region hash must equal the
    constant validate_extraction.py ships. Fails until the LEAD fills the
    PENDING placeholder with the real hash (§Hash protocol)."""
    assert ve.selfcheck_region_hash(TEMPLATE_TEXT) == ve.CURRENT_EXTRACTOR_SELFCHECK_HASH


def test_every_chk_call_is_inside_the_region():
    begin_pos = TEMPLATE_TEXT.index(ve.BEGIN_SENTINEL_PREFIX)
    end_pos = TEMPLATE_TEXT.index(ve.END_SENTINEL_PREFIX)
    assert begin_pos < end_pos

    chk_calls = list(re.finditer(r"chk\(", TEMPLATE_TEXT))
    assert chk_calls, "expected chk(...) self-check calls in the template"
    outside = [m.start() for m in chk_calls if not (begin_pos < m.start() < end_pos)]
    assert not outside, f"chk( calls found outside the self-check region at offsets {outside}"

    region = TEMPLATE_TEXT[begin_pos:end_pos]
    # both #83/#84 checks are part of the pinned region
    assert '"body_files_yield_segments"' in region
    assert '"verse_plain_text_nonempty"' in region


def test_no_adapt_point_marker_inside_region():
    begin_pos = TEMPLATE_TEXT.index(ve.BEGIN_SENTINEL_PREFIX)
    end_pos = TEMPLATE_TEXT.index(ve.END_SENTINEL_PREFIX)
    region = TEMPLATE_TEXT[begin_pos:end_pos]
    assert "# ADAPT-POINT" not in region, (
        "an ADAPT-POINT (an intended per-project edit site) must never fall "
        "inside the DO-NOT-EDIT self-check region"
    )


def test_marker_regexes_match_validate_extraction():
    """#86 drift-pin: validate_extraction.py copies FNREF_RE / BODY_REF_MARKER_RE
    verbatim from extract.py.template (they live OUTSIDE the pinned self-check
    region, so the region hash does NOT guard them). Assert the two pattern
    literals are byte-identical between the template (parsed from its source
    literals) and the validate_extraction module so they cannot silently
    diverge -- a divergence would make the gate re-derive markers with a
    different regex than the extractor emits them."""
    literals = {}
    for node in ast.walk(ast.parse(TEMPLATE_TEXT)):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in ("FNREF_RE", "BODY_REF_MARKER_RE")
            and isinstance(node.value, ast.Call)
            and node.value.args
            and isinstance(node.value.args[0], ast.Constant)
            and isinstance(node.value.args[0].value, str)
        ):
            literals[node.targets[0].id] = node.value.args[0].value

    assert set(literals) == {"FNREF_RE", "BODY_REF_MARKER_RE"}, (
        f"could not parse both marker-regex literals from the template; found {sorted(literals)}"
    )
    assert literals["FNREF_RE"] == ve.FNREF_RE.pattern, (
        literals["FNREF_RE"],
        ve.FNREF_RE.pattern,
    )
    assert literals["BODY_REF_MARKER_RE"] == ve.BODY_REF_MARKER_RE.pattern, (
        literals["BODY_REF_MARKER_RE"],
        ve.BODY_REF_MARKER_RE.pattern,
    )


def test_crlf_and_trailing_whitespace_do_not_change_hash():
    """The normalization rstrips each region line, so a CRLF / trailing-space
    only diff of the region must leave the hash unchanged (it must pin the
    self-check LOGIC, not incidental line endings or editor whitespace)."""
    lines, begins, ends = _sentinel_line_indices()
    b, e = begins[0], ends[0]

    mutated = list(lines)
    for i in range(b + 1, e):
        mutated[i] = mutated[i] + "   \r"  # trailing spaces + carriage return
    mutated_text = "\n".join(mutated)

    assert ve.selfcheck_region_hash(mutated_text) == ve.selfcheck_region_hash(TEMPLATE_TEXT)
