"""tests/sentinel_verdict_parity.test.py

Parity drift-guard (#308): the ``sentinelVerdict()`` line-oriented verdict
helper is intentionally MIRRORED byte-for-byte across all three workflow
templates (``mass-translate-wf.template.js``, ``glossary-pass-wf
.template.js``, ``skeptic-pass-wf.template.js``) rather than imported from
one shared module -- these are standalone files with no runtime imports
(see each template's own header comment on why; PLAN-308 sec2.1 pins the
exact source text, comment included, not just the function body). This file
locks that text 3-way identical so a future edit to one copy can't silently
drift from its siblings without going red.

Not part of tests/workflow_template_instantiation.test.py (that file's own
scope is instantiation/token-substitution correctness for
mass-translate-wf.template.js and glossary-pass-wf.template.js only -- it
never reads skeptic-pass-wf.template.js at all), so this lives as its own
sibling file, mirroring this project's ``*_parity.test.py`` / ``*_drift
.test.py`` naming convention (see e.g. tests/skeptic_defaults_parity.test.py,
tests/frozen_input_path_state_parity.test.py).

Reuses tests/review_prompt_schema_drift.test.py's ``_find_balanced_brace_span``
(house style -- see tests/agent_schema_top_level_object.test.py's and
tests/transient_failure_recoverable.test.py's identical reuse) to extract
the exact function span rather than a brittle line-range slice.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
MASS_TRANSLATE_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"
GLOSSARY_PASS_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"
SKEPTIC_PASS_TEMPLATE = TEMPLATES_DIR / "skeptic-pass-wf.template.js"

for _p in (MASS_TRANSLATE_TEMPLATE, GLOSSARY_PASS_TEMPLATE, SKEPTIC_PASS_TEMPLATE):
    assert _p.is_file(), f"expected template not found: {_p}"

# ---------------------------------------------------------------------------
# Reuse review_prompt_schema_drift.test.py's brace-matching helper (house
# style -- see tests/agent_schema_top_level_object.test.py's and
# tests/transient_failure_recoverable.test.py's identical reuse), rather
# than vendoring a second copy.
# ---------------------------------------------------------------------------
_DRIFT_TEST_PATH = Path(__file__).resolve().parent / "review_prompt_schema_drift.test.py"
assert _DRIFT_TEST_PATH.is_file(), f"expected sibling test file not found: {_DRIFT_TEST_PATH}"

_drift_spec = importlib.util.spec_from_file_location(
    "review_prompt_schema_drift_shared_for_sentinel_verdict_parity", _DRIFT_TEST_PATH
)
assert _drift_spec is not None and _drift_spec.loader is not None, f"could not load spec for {_DRIFT_TEST_PATH}"
_drift = importlib.util.module_from_spec(_drift_spec)
_drift_spec.loader.exec_module(_drift)

_find_balanced_brace_span = _drift._find_balanced_brace_span

# The exact leading comment line PLAN-308 sec2.1 pins -- a stable, unique
# anchor for the start of the mirrored comment+function unit.
_COMMENT_ANCHOR = "// Line-oriented sentinel verdict (#308), mirrored byte-for-byte across the"
_SIGNATURE = "function sentinelVerdict(reply, okSentinel, failSentinel) {"


def _extract_sentinel_verdict_unit(source: str, template_path: Path) -> str:
    """Returns the exact text from the mirrored comment block's first line
    through sentinelVerdict()'s own closing brace -- the WHOLE unit
    PLAN-308 sec2.1 requires be byte-identical, comment included, not just
    the function body."""
    comment_idx = source.find(_COMMENT_ANCHOR)
    assert comment_idx != -1, f"expected sentinelVerdict's mirrored comment block in {template_path}"
    sig_idx = source.find(_SIGNATURE, comment_idx)
    assert sig_idx != -1, f"expected 'function sentinelVerdict(...)' after its comment block in {template_path}"
    brace_start = source.index("{", sig_idx)
    brace_end = _find_balanced_brace_span(source, brace_start)  # index just past the closing '}'
    return source[comment_idx:brace_end]


@pytest.fixture(scope="module")
def mass_unit() -> str:
    return _extract_sentinel_verdict_unit(
        MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8"), MASS_TRANSLATE_TEMPLATE
    )


@pytest.fixture(scope="module")
def glossary_unit() -> str:
    return _extract_sentinel_verdict_unit(
        GLOSSARY_PASS_TEMPLATE.read_text(encoding="utf-8"), GLOSSARY_PASS_TEMPLATE
    )


@pytest.fixture(scope="module")
def skeptic_unit() -> str:
    return _extract_sentinel_verdict_unit(
        SKEPTIC_PASS_TEMPLATE.read_text(encoding="utf-8"), SKEPTIC_PASS_TEMPLATE
    )


def test_sentinel_verdict_is_byte_identical_across_all_three_templates(mass_unit, glossary_unit, skeptic_unit):
    assert glossary_unit == mass_unit, (
        "glossary-pass-wf.template.js's sentinelVerdict comment+body has drifted "
        "from mass-translate-wf.template.js's -- PLAN-308 sec2.1 requires all "
        "three templates carry byte-identical copies (standalone template files, "
        "no runtime imports between them)"
    )
    assert skeptic_unit == mass_unit, (
        "skeptic-pass-wf.template.js's sentinelVerdict comment+body has drifted "
        "from mass-translate-wf.template.js's -- PLAN-308 sec2.1 requires all "
        "three templates carry byte-identical copies (standalone template files, "
        "no runtime imports between them)"
    )


def test_sentinel_verdict_unit_is_nonempty_and_contains_the_function(mass_unit):
    # Guards against the extraction helper silently matching an empty/wrong
    # span (e.g. if the comment anchor text ever changes) and the equality
    # test above passing vacuously on three empty or truncated strings.
    assert "function sentinelVerdict(reply, okSentinel, failSentinel) {" in mass_unit
    assert "return lines[lines.length - 1] === okSentinel;" in mass_unit
    assert len(mass_unit) > 500


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
