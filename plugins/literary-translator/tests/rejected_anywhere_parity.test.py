"""tests/rejected_anywhere_parity.test.py

Parity drift-guard for the 1.16.0 containment guard, mirroring
tests/sentinel_verdict_parity.test.py's job for ``sentinelVerdict()``.

``rejectedAnywhere()`` is the fix for the sentinel-gluing false approval:
``sentinelVerdict()`` recognises a fail sentinel only when it is alone on its
LF-delimited line after ``trim()``, so anything else sharing that line defeats
the rejection trigger while a trailing clean OK line approves the reply. The
guard is applied at the CALL SITES precisely because ``sentinelVerdict`` itself
must stay byte-identical across all three templates.

TWO templates carry the guard -- ``glossary-pass-wf.template.js`` and
``mass-translate-wf.template.js``. ``skeptic-pass-wf.template.js`` deliberately
does NOT: it is outside this release's bundle-hash move (``cache_key.py``'s
``PLUGIN_BUNDLE_MEMBERS`` lists the other two and never mentions skeptic), and
adding it there would flip a hash this release promises is untouched. That
asymmetry is checked below rather than merely described, so "skeptic was left
out" stays a recorded decision instead of drifting into an oversight nobody
can distinguish from one.

WHY THIS FILE EXISTS AT ALL. Two independently-maintained copies of a security
guard are worse than one: the copies can drift, and the weaker copy is then the
one that decides. Nothing else in the suite compares them -- each template's own
behavioural tests pass against its own copy, so a divergence would be invisible
in both. That is the same false-green shape the guard itself was written to
close.

Follows this project's ``*_parity.test.py`` naming convention (see
tests/sentinel_verdict_parity.test.py, tests/skeptic_defaults_parity.test.py,
tests/frozen_input_path_state_parity.test.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
MASS_TRANSLATE_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"
GLOSSARY_PASS_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"
SKEPTIC_PASS_TEMPLATE = TEMPLATES_DIR / "skeptic-pass-wf.template.js"

for _p in (MASS_TRANSLATE_TEMPLATE, GLOSSARY_PASS_TEMPLATE, SKEPTIC_PASS_TEMPLATE):
    assert _p.is_file(), f"expected template not found: {_p}"

GUARD_HELPER = "rejectedAnywhere"

_SIGNATURE_RE = re.compile(
    r"^function " + re.escape(GUARD_HELPER) + r"\(reply, failSentinel\) \{$", re.MULTILINE
)


def extract_guard_function(source: str, template_path: Path) -> str:
    """The exact text of ``rejectedAnywhere()``'s declaration through its own
    closing brace.

    Sliced to the first COLUMN-0 closing brace after the signature: these
    templates keep every top-level function flat and indent all body lines, so
    that brace is the function's own. The BODY is what has to be identical --
    the leading comment above it is deliberately NOT included, because each
    template's comment reasons about its own call sites (glossary names its
    three, mass-translate names the DRAFT_MISSING site it leaves unguarded) and
    forcing those to match would be forcing them to be wrong."""
    m = _SIGNATURE_RE.search(source)
    assert m is not None, (
        f"expected `function {GUARD_HELPER}(reply, failSentinel) {{` in "
        f"{template_path.name}"
    )
    end = source.find("\n}\n", m.end())
    assert end != -1, (
        f"could not find a column-0 closing brace for {GUARD_HELPER} in "
        f"{template_path.name}"
    )
    return source[m.start():end + 3]


@pytest.fixture(scope="module")
def mass_guard() -> str:
    return extract_guard_function(
        MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8"), MASS_TRANSLATE_TEMPLATE
    )


@pytest.fixture(scope="module")
def glossary_guard() -> str:
    return extract_guard_function(
        GLOSSARY_PASS_TEMPLATE.read_text(encoding="utf-8"), GLOSSARY_PASS_TEMPLATE
    )


def test_rejected_anywhere_is_byte_identical_across_both_templates(mass_guard, glossary_guard):
    """The whole point of this file: one guard, two copies, no drift.

    A divergence is invisible to every other test -- each template's own suite
    exercises only its own copy -- and the weaker copy would silently become the
    one deciding whether a rejected verdict is honoured."""
    assert mass_guard == glossary_guard, (
        "mass-translate-wf.template.js's rejectedAnywhere() has drifted from "
        "glossary-pass-wf.template.js's. Both templates guard sentinel sites with "
        "this function and neither imports the other (standalone template files, "
        "no runtime imports), so the two copies must stay byte-identical:\n\n"
        f"mass-translate:\n{mass_guard}\n\nglossary-pass:\n{glossary_guard}"
    )


def test_extracted_guard_is_the_real_function_not_an_empty_span(mass_guard):
    """Guards the equality above against passing vacuously on two empty or
    truncated strings if the signature or brace convention ever changes."""
    assert mass_guard.startswith(f"function {GUARD_HELPER}(reply, failSentinel) {{")
    assert mass_guard.rstrip().endswith("}"), (
        f"the extracted guard does not end at a closing brace:\n{mass_guard}"
    )
    # The two behaviours the unit test in tests/glossary_citation_review.test.py
    # pins -- containment, and the degenerate-sentinel bail-out -- must both be
    # present in the text being compared, or this file could compare two copies
    # of something that is no longer the guard.
    assert "indexOf(failSentinel)" in mass_guard, (
        f"the extracted text is not a containment check:\n{mass_guard}"
    )
    assert "failSentinel.length === 0" in mass_guard, (
        "the extracted guard has lost its degenerate-sentinel bail-out; an empty "
        f"sentinel would then match every reply:\n{mass_guard}"
    )


def test_skeptic_template_deliberately_carries_no_guard():
    """The asymmetry, checked rather than assumed.

    skeptic-pass-wf.template.js is outside this release's PLUGIN_BUNDLE_MEMBERS
    hash move, so guarding it would flip a bundle hash the release states is
    untouched. If the guard is ever added there, this test is the prompt to
    re-price that decision and extend the parity check above to three copies --
    it is NOT a reason to delete this test."""
    source = SKEPTIC_PASS_TEMPLATE.read_text(encoding="utf-8")
    assert GUARD_HELPER not in source, (
        f"skeptic-pass-wf.template.js now mentions {GUARD_HELPER}. That is a "
        f"deliberate scope change, not a routine edit: it moves a template that "
        f"this release promises is untouched. Re-price the bundle-hash cost, then "
        f"extend test_rejected_anywhere_is_byte_identical_across_both_templates to "
        f"cover all three copies"
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
