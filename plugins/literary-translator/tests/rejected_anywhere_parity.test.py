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
    four, mass-translate names the DRAFT_MISSING site it leaves unguarded) and
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


def _guard_comment(source: str, template_path: Path) -> str:
    """The contiguous block of ``//`` lines immediately above the guard."""
    idx = source.index(f"function {GUARD_HELPER}(reply, failSentinel) {{")
    start = idx
    for line in reversed(source[:idx].split(chr(10))[:-1]):
        if not line.startswith("//"):
            break
        start -= len(line) + 1
    comment = source[start:idx]
    assert comment.strip(), f"no comment block found above {GUARD_HELPER} in {template_path.name}"
    return comment


# Claims that are TRUE of glossary-pass and FALSE of mass-translate. Any of them
# appearing in mass-translate's comment means glossary's block was pasted across.
# Deliberately NOT including "glossary_citation_review": both templates cite that
# file legitimately, as the home of the guard's unit test, so it would fire on
# correct prose.
#
# EVERY ENTRY MUST STILL OCCUR IN GLOSSARY'S OWN COMMENT, and
# test_glossary_only_claims_still_occur_in_the_glossary_comment below is what
# makes that a checked property rather than an assumption. The reason is the
# failure this list actually suffered: it read "all three call sites" until
# #347's prepare/judge split made glossary's comment say four, at which point the
# needle occurred in NEITHER template. The absence assertion below went on
# passing -- it asserts absence from mass-translate, and a string that exists
# nowhere is absent from everything -- so a paste detector that had silently
# stopped detecting anything looked exactly like a healthy one. A needle that
# quotes prose is the fragile kind on purpose: it is the only kind that catches
# a paste of prose, so the fragility is paid for with the guard, not avoided by
# picking weaker needles.
GLOSSARY_ONLY_CLAIMS = [
    # #347/1.16.1 -- glossary guards FOUR sites (precheck, wait, citation
    # prepare, citation judge); mass-translate guards two waits. Verified
    # against both comments, not inferred from the site count.
    "at all four sites",
    "notReadyBatches",
    "MAX_CITATION_RETRIES",
]


def test_glossary_only_claims_still_occur_in_the_glossary_comment():
    """The anti-rot guard for the list above, and the reason this file did not
    notice #347 on its own.

    ``test_the_two_guard_comments_are_deliberately_not_identical`` asserts these
    claims are ABSENT from mass-translate. That assertion is vacuously true of
    any string nobody writes -- so once glossary's own prose moves, the needle
    stops matching the source it exists to detect pastes FROM, and the paste
    detector degrades to a no-op while staying green. That is the same
    false-green shape as a divergent copy of the guard: a check that cannot
    fail, indistinguishable in the log from one that has nothing to report.

    So the pairing is the point. The other test says "this claim must not appear
    over there"; this one says "it must still appear over here". Neither is
    sufficient alone, and only together do they mean "a paste would be caught".

    If this goes red, the fix is to re-read glossary's guard comment and requote
    the claim it makes NOW -- never to delete the entry, and never to relax it to
    something both templates say (which would break the other test instead)."""
    glossary_comment = _guard_comment(
        GLOSSARY_PASS_TEMPLATE.read_text(encoding="utf-8"), GLOSSARY_PASS_TEMPLATE
    )
    missing = [claim for claim in GLOSSARY_ONLY_CLAIMS if claim not in glossary_comment]
    assert not missing, (
        f"GLOSSARY_ONLY_CLAIMS entries {missing} no longer occur in "
        f"glossary-pass-wf.template.js's rejectedAnywhere() comment. They are the "
        f"needles test_the_two_guard_comments_are_deliberately_not_identical uses "
        f"to detect that comment being pasted over mass-translate's -- a needle "
        f"absent from the SOURCE matches nothing anywhere, so that test would keep "
        f"passing while detecting nothing at all. Re-read the glossary comment and "
        f"requote the claim it makes now; do not delete the entry, and do not "
        f"replace it with something mass-translate also says"
    )


def test_the_two_guard_comments_are_deliberately_not_identical():
    """The comments must NOT match, and this test exists to say so out loud.

    The obvious way to "fix" a failing parity test is to paste one template's
    block over the other's. Doing that here would ship prose describing another
    file's control flow: glossary's comment counts FOUR guarded call sites (#347
    split its citation reviewer into a prepare call and a judge call) and reasons
    about the citation review, MAX_CITATION_RETRIES and notReadyBatches, none of
    which exist in mass-translate, which guards TWO sites with different
    sentinels and different recovery. Wrong prose about a security guard is the
    same defect class the guard itself was added to close.

    So the parity contract is deliberately narrow: the FUNCTION BODY is pinned
    byte-for-byte by the test above; the comment above it is each template's
    own. (This is where this file diverges from
    tests/sentinel_verdict_parity.test.py, which is comment-INCLUSIVE because
    that function's comment is genuinely template-independent.)"""
    mass_comment = _guard_comment(
        MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8"), MASS_TRANSLATE_TEMPLATE
    )
    glossary_comment = _guard_comment(
        GLOSSARY_PASS_TEMPLATE.read_text(encoding="utf-8"), GLOSSARY_PASS_TEMPLATE
    )

    assert mass_comment != glossary_comment, (
        "the two rejectedAnywhere() comments are byte-identical. They must not "
        "be: each documents its own template's call sites, counts and recovery. "
        "If this went red because a parity failure was 'fixed' by pasting one "
        "comment over the other, revert that -- only the FUNCTION BODY is "
        "required to match"
    )

    leaked = [claim for claim in GLOSSARY_ONLY_CLAIMS if claim in mass_comment]
    assert not leaked, (
        f"mass-translate-wf.template.js's guard comment carries glossary-specific "
        f"claim(s) {leaked}, which are false in mass-translate: it guards two "
        f"sites, not four, and has no citation-review retry ladder or "
        f"notReadyBatches branch. This is what pasting glossary's comment across "
        f"looks like"
    )


DELEGATOR = "mentionedAnywhere"
_DELEGATOR_SIGNATURE = f"function {DELEGATOR}(reply, sentinel) {{"


def test_mentioned_anywhere_delegates_instead_of_reimplementing_containment():
    """The OK-direction wrapper must be a delegation, not a second containment
    implementation.

    ``mentionedAnywhere()`` exists so that runRound's DRAFT_MISSING check reads
    honestly at its call site: same containment test as ``rejectedAnywhere()``,
    opposite consequence, so it carries a name that is not false there. Its
    comment states that delegating is what keeps the containment semantics --
    including the empty/non-string sentinel guard -- in ONE place.

    Nothing asserted that. Reimplement the body as a whole-line check and every
    behavioural test in this plugin stays green while runRound's gap silently
    reopens, because no behavioural test drives that site's reply shapes. This
    file already forbids a divergent COPY of the guard across templates; this is
    the same invariant one level in -- a divergent CALLER of it.

    So the assertion is not about drift between copies. It is what makes the
    deliberate duplication of the NAME safe rather than merely intentional."""
    source = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    idx = source.find(_DELEGATOR_SIGNATURE)
    assert idx != -1, (
        f"expected `{_DELEGATOR_SIGNATURE}` in {MASS_TRANSLATE_TEMPLATE.name}"
    )
    end = source.find("\n}\n", idx)
    assert end != -1, (
        f"could not find a column-0 closing brace for {DELEGATOR} in "
        f"{MASS_TRANSLATE_TEMPLATE.name}"
    )
    body = [
        line.strip()
        for line in source[idx:end].split(chr(10))[1:]
        if line.strip() and not line.strip().startswith("//")
    ]
    assert body == [f"return {GUARD_HELPER}(reply, sentinel)"], (
        f"{DELEGATOR}() must delegate to {GUARD_HELPER}() and do nothing else, so "
        f"containment -- including the empty/non-string sentinel guard that stops "
        f'"".indexOf("") === 0 from matching every reply -- has exactly ONE '
        f"implementation. Its body is now:\n  " + "\n  ".join(body) + "\n"
        f"A reimplementation here reopens runRound's DRAFT_MISSING gluing gap with "
        f"the whole suite still green: no behavioural test drives that site."
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
