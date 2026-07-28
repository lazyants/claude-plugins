"""tests/rejected_anywhere_parity.test.py

Parity drift-guard for the 1.16.0 containment guard, mirroring
tests/sentinel_verdict_parity.test.py's job for ``sentinelVerdict()``.

``rejectedAnywhere()`` is the fix for the sentinel-gluing false approval:
``sentinelVerdict()`` recognises a fail sentinel only when it is alone on its
LF-delimited line after ``trim()``, so anything else sharing that line defeats
the rejection trigger while a trailing clean OK line approves the reply. The
guard is applied at the CALL SITES precisely because ``sentinelVerdict`` itself
must stay byte-identical across all three templates.

ALL THREE templates carry the guard as of 1.16.2 (#352) --
``mass-translate-wf.template.js``, ``glossary-pass-wf.template.js`` and
``skeptic-pass-wf.template.js``.

Until 1.16.2 skeptic did not, and this file asserted that absence as a
deliberate decision. THE RATIONALE IT RECORDED WAS FALSE, and correcting it
matters more than the flipped assertion does. It said guarding skeptic "would
flip a bundle hash this release promises is untouched". Measured against the
release: ``skeptic-pass-wf.template.js`` is indeed absent from ``cache_key.py``'s
``PLUGIN_BUNDLE_MEMBERS`` (14 entries, none of them skeptic), so editing it
triggers no re-translation -- but it IS inside the skeptic code closure, so it
forces a fresh skeptic RUN_ID, and 1.16.2 changed 234 lines in that file
anyway. The cost the docstring was protecting had already been paid by the same
release that was invoking it as a reason not to pay it.

What the unguarded skeptic copy actually cost, measured before the port: a
``PENDING`` sentinel glued to prose by any non-newline character was overridden
by a trailing ``READY`` in 6 of 6 probe characters for skeptic and 0 of 6 for
glossary -- a false GREEN carrying an unproven fragment into the merge.

So the check below is INVERTED rather than deleted: it now asserts the three
copies AGREE. It is also strengthened from textual to BEHAVIOURAL, because
text-identity is the weak form -- it cannot see two differently-written
functions that behave the same, and more importantly it cannot see two
call-sites that wire an identical guard differently. The parity run executes
each template's REAL ``waitChunkVerdict()`` over one shared table of reply
shapes and requires identical verdicts, with the glued-``PENDING`` case that
was the real defect among them.

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

import json
import re
import shutil
import subprocess
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


@pytest.fixture(scope="module")
def skeptic_guard() -> str:
    return extract_guard_function(
        SKEPTIC_PASS_TEMPLATE.read_text(encoding="utf-8"), SKEPTIC_PASS_TEMPLATE
    )


def test_rejected_anywhere_is_byte_identical_across_all_three_templates(
    mass_guard, glossary_guard, skeptic_guard
):
    """The whole point of this file: one guard, three copies, no drift.

    A divergence is invisible to every other test -- each template's own suite
    exercises only its own copy -- and the weaker copy would silently become the
    one deciding whether a rejected verdict is honoured.

    Extended from two copies to three in 1.16.2 (#352), which is exactly what
    this test's own pre-1.16.2 sibling instructed whoever ported the guard into
    skeptic to do."""
    copies = {
        MASS_TRANSLATE_TEMPLATE.name: mass_guard,
        GLOSSARY_PASS_TEMPLATE.name: glossary_guard,
        SKEPTIC_PASS_TEMPLATE.name: skeptic_guard,
    }
    assert len(set(copies.values())) == 1, (
        "the templates' rejectedAnywhere() copies have drifted. All three guard "
        "sentinel sites with this function and none imports another (standalone "
        "template files, no runtime imports), so the copies must stay "
        "byte-identical:\n\n"
        + "\n\n".join(f"{name}:\n{text}" for name, text in copies.items())
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
        "implementation. Its body is now:\n  " + "\n  ".join(body) + "\n"
        "A reimplementation here reopens runRound's DRAFT_MISSING gluing gap with "
        "the whole suite still green: no behavioural test drives that site."
    )


def test_skeptic_template_carries_the_guard_too():
    """1.16.2 (#352): the asymmetry is gone, and its absence is now the thing
    checked.

    This test used to assert the opposite. Inverting it rather than deleting it
    keeps the decision recorded: if skeptic ever loses the guard again, the
    reply shapes in the parity run below stop agreeing and BOTH tests report
    it -- one structurally, one behaviourally."""
    source = SKEPTIC_PASS_TEMPLATE.read_text(encoding="utf-8")
    assert GUARD_HELPER in source, (
        f"skeptic-pass-wf.template.js no longer defines {GUARD_HELPER}. Its "
        f"waitChunkVerdict then falls back to sentinelVerdict's whole-line "
        f"equality, and a PENDING sentinel sharing its line with prose is "
        f"overridden by a trailing READY -- measured at 6 of 6 gluing characters "
        f"before the guard was ported, against 0 of 6 for glossary. That is a "
        f"false GREEN: an unproven fragment reaches the merge"
    )


# ---------------------------------------------------------------------------
# BEHAVIOURAL parity: the three real waitChunkVerdict() functions, one shared
# table of reply shapes, identical verdicts required.
#
# Strictly stronger than the text comparison above, and it is what would have
# caught the skeptic divergence. Text identity of rejectedAnywhere() was TRUE of
# mass-translate and glossary throughout, and told nobody that skeptic's wait
# site never called it -- the drift was at the CALL SITE, which no comparison of
# the helper's own bytes can reach.
#
# Executed rather than parsed, for the same reason the wait tests read emitted
# prompts instead of helper arithmetic: a correct-but-uncalled guard is exactly
# the shape that passes every structural check and ships the bug.
# ---------------------------------------------------------------------------

NODE = shutil.which("node")

# `<idx>` is substituted with the batch index the verdict is asked about, so one
# table drives all three templates whatever they call their index variable.
PARITY_REPLY_SHAPES = {
    "clean_ready": "READY <idx>",
    "clean_pending": "PENDING <idx>",
    "decorated_ready": "The poll confirmed the fragment (exit 0).\n\nREADY <idx>",
    # THE DEFECT. A PENDING sentinel glued to prose by a non-newline character,
    # with a trailing clean READY. Whole-line equality misses the PENDING; raw
    # containment catches it. This row is the one that used to diverge.
    "glued_pending_space": "the chunk was cut short PENDING <idx>\nREADY <idx>",
    "glued_pending_tab": "the chunk was cut short\tPENDING <idx>\nREADY <idx>",
    "glued_pending_cr": "the chunk was cut short\rPENDING <idx>\nREADY <idx>",
    "glued_pending_nbsp": "the chunk was cut short\xa0PENDING <idx>\nREADY <idx>",
    "glued_pending_zwsp": "the chunk was cut short​PENDING <idx>\nREADY <idx>",
    "glued_pending_letter": "cut shortxPENDING <idx>\nREADY <idx>",
    "fail_priority_lf": "PENDING <idx>\nREADY <idx>",
    "quoted_disavowed_ready": (
        "Quoting the requested success form:\nREADY <idx>\nThat is not my verdict."
    ),
    "other_batch_ready": "READY 7",
    "empty": "",
    "whitespace_only": "   \n\n  ",
    "tool_killed": "Exit code 143\nCommand timed out after 10m 0s",
    "unparseable": "I ran the command but I am not sure what it printed.",
}

_VERDICT_FNS = ("sentinelVerdict", "rejectedAnywhere", "waitChunkVerdict")


def extract_named_function(source: str, name: str, template_path: Path) -> str:
    """One top-level `function <name>(...) {` through its own column-0 brace."""
    m = re.search(rf"^function {re.escape(name)}\(", source, re.MULTILINE)
    assert m is not None, f"expected `function {name}(` in {template_path.name}"
    end = source.find("\n}\n", m.end())
    assert end != -1, (
        f"could not find a column-0 closing brace for {name} in {template_path.name}"
    )
    return source[m.start():end + 3]


@pytest.mark.skipif(NODE is None, reason="node not found on PATH")
@pytest.mark.parametrize("shape", sorted(PARITY_REPLY_SHAPES), ids=sorted(PARITY_REPLY_SHAPES))
def test_all_three_wait_verdicts_agree_on_every_reply_shape(shape, tmp_path):
    """Each template's REAL wait-reply reader, over the same reply, must return
    the same verdict.

    Note what is NOT asserted: which verdict is correct. That belongs to each
    template's own behavioural suite. What this file owns is that the three
    cannot DISAGREE -- because a disagreement is invisible to all three of those
    suites, each of which passes against its own copy."""
    reply = PARITY_REPLY_SHAPES[shape]
    verdicts = {}
    for path in (MASS_TRANSLATE_TEMPLATE, GLOSSARY_PASS_TEMPLATE, SKEPTIC_PASS_TEMPLATE):
        source = path.read_text(encoding="utf-8")
        fns = "\n".join(
            extract_named_function(source, n, path)
            for n in _VERDICT_FNS
            if re.search(rf"^function {n}\(", source, re.MULTILINE)
        )
        # mass-translate's index variable is a seg id; the other two use a batch
        # index. The shared table names neither -- it asks each function about
        # the index it is handed.
        harness = (
            fns
            + "\nconst reply = " + json.dumps(reply.replace("<idx>", "0"))
            + ";\nprocess.stdout.write(String(waitChunkVerdict(reply, \"0\")));\n"
        )
        p = tmp_path / f"parity_{path.stem}_{shape}.js"
        p.write_text(harness, encoding="utf-8")
        assert NODE is not None
        proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, (
            f"{path.name}'s waitChunkVerdict threw on {shape}: {proc.stderr}"
        )
        verdicts[path.name] = proc.stdout.strip()

    assert len(set(verdicts.values())) == 1, (
        f"the three templates' waitChunkVerdict() DISAGREE on reply shape "
        f"{shape!r}: {verdicts}\n"
        f"Reply: {reply!r}\n"
        f"A divergence here is invisible to every other test in the suite -- each "
        f"template's own behavioural tests pass against its own copy -- and the "
        f"weakest copy is the one that decides whether an unproven fragment "
        f"reaches the merge. This is the exact shape that let skeptic read a "
        f"glued PENDING as READY until 1.16.2."
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
