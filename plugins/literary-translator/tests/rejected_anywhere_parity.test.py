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



# ---------------------------------------------------------------------------
# CALL-SITE ENUMERATION -- the hole the behavioural parity test above does NOT
# cover, found by an independent reviewer's mutation.
#
# test_all_three_wait_verdicts_agree_on_every_reply_shape extracts each
# template's own waitChunkVerdict()/rejectedAnywhere()/sentinelVerdict()
# DEFINITIONS and calls them directly. That proves the three definitions
# agree; it proves NOTHING about whether a template's real call sites --
# batchStep, getVerifiedReview, reviewFixLoop -- actually route their replies
# through those definitions. The reviewer proved the gap by mutation: replace
# skeptic's two `verdict = waitChunkVerdict(...)` call sites with bare
# `sentinelVerdict(...)` calls, leaving waitChunkVerdict() itself untouched
# and unused. The extraction-and-direct-call harness above still extracts the
# now-orphaned function, still calls it directly, and still agrees across
# templates -- the mutation is invisible to it, and the whole suite stays
# green while the guard the previous section pins is bypassed at runtime.
#
# So this section answers a different question: not "do the definitions
# agree" but "does every wait-verdict decision site in every shipped template
# actually CALL the guarded reader". That has to be answered by finding real
# call sites in the source, and a regex over source text is the wrong tool
# for it -- not because regexes cannot match `waitChunkVerdict(`, but because
# every guarded call site in these templates sits under a long comment that
# discusses the guard by name at length (see the precheck comment just above,
# or waitChunkVerdict()'s own header comment), so a regex with no notion of
# "this is a comment, not code" is satisfied by the PROSE and never actually
# proves the CALL exists. That is the exact tautology
# tests/bounded_poll_present.test.py's own code_lines() helper was built to
# avoid for a different lock, and it is worse here because a
# comment-vs-string-vs-code distinction is precisely what a line-oriented
# regex cannot make reliably across arbitrarily wrapped multi-line calls.
#
# The tool used instead is a minimal but real JS tokenizer: it walks the
# source once, classifies every byte as CODE, LINE COMMENT, BLOCK COMMENT,
# STRING, or TEMPLATE LITERAL (recursing into `${...}` interpolations, which
# are code again), and reports a call site only when a bare identifier is
# immediately followed -- in CODE, never in prose -- by `(`. It also
# disambiguates `/` as division/comment-start vs a regex literal, which this
# file's own templates genuinely contain (mass-translate's SEG_ID_RE and
# friends, glossary's scheme-validation regex) and which a naive scanner
# would otherwise mis-tokenize, corrupting everything read after it.
#
# It is deliberately NOT a full parser: it has no notion of scope, precedence
# or statement structure, because nothing here needs one. "Was this bare name
# really invoked here, in code" is a lexical question, and answering it
# lexically-but-correctly (unlike a regex, which answers it
# lexically-but-naively) is what closes the gap the mutation found.
# ---------------------------------------------------------------------------

_REGEX_ALLOWED_AFTER_WORD = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "case", "do", "else", "yield", "throw",
}


def _skip_string(source: str, start: int) -> int:
    """`start` is the opening quote. Returns the index just past the closing
    quote, stepping over backslash escapes so an escaped quote never ends the
    string early."""
    quote = source[start]
    i = start + 1
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return n


def _skip_template(source: str, start: int) -> int:
    """`start` is the opening backtick. Returns the index just past the
    matching closing backtick. `${...}` interpolations are code, not text --
    handed to `_skip_interpolation` rather than scanned past as if they were
    part of the literal, so a string, comment, or nested template inside an
    interpolation cannot desynchronize the backtick count."""
    i = start + 1
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i + 1
        if c == "$" and i + 1 < n and source[i + 1] == "{":
            i = _skip_interpolation(source, i + 2)
            continue
        i += 1
    return n


def _skip_interpolation(source: str, start: int) -> int:
    """`start` is just after the interpolation's own `${`. Returns the index
    just past its matching `}`, tokenizing what is inside (comments, strings,
    nested templates, balanced braces) rather than scanning for the next `}`
    blindly -- a `}` inside a nested string or object literal must not end
    the interpolation early."""
    i = start
    n = len(source)
    depth = 1
    while i < n and depth > 0:
        c = source[i]
        if c == "/" and i + 1 < n and source[i + 1] == "/":
            j = source.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and source[i + 1] == "*":
            j = source.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in ("'", '"'):
            i = _skip_string(source, i)
            continue
        if c == "`":
            i = _skip_template(source, i)
            continue
        if c == "{":
            depth += 1
            i += 1
            continue
        if c == "}":
            depth -= 1
            i += 1
            continue
        i += 1
    return i


def _skip_regex_literal(source: str, start: int):
    """`start` is the leading `/` of a suspected regex literal. Returns the
    index just past its trailing flags, or None if this does not look like a
    well-formed regex literal (conservative on purpose -- a wrong
    classification here could swallow real code as if it were inside a
    literal, hiding call sites rather than merely miscounting one)."""
    i = start + 1
    n = len(source)
    in_class = False
    while i < n:
        c = source[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            return None
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j = i + 1
            while j < n and source[j] in "gimsuy":
                j += 1
            return j
        i += 1
    return None


def _skip_ws_and_comments(source: str, i: int) -> int:
    """The next CODE position at or after `i` -- past any run of whitespace,
    `//` line comments, and `/* */` block comments. Used to look ahead from
    an identifier to whatever follows it without being fooled by a comment
    sitting between them."""
    n = len(source)
    while i < n:
        c = source[i]
        if c.isspace():
            i += 1
            continue
        if c == "/" and i + 1 < n and source[i + 1] == "/":
            j = source.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and source[i + 1] == "*":
            j = source.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        break
    return i


def js_call_sites(source: str, name: str) -> list[int]:
    """Character offsets in `source` where the bare identifier `name` is
    CALLED -- i.e. appears as a standalone token in CODE, immediately
    followed (across any whitespace/comments) by `(`, that is neither a
    member-expression call (`obj.name(...)`) nor the function's own
    declaration (`function name(...) {`).

    Deliberately narrow: it recognises exactly the call SHAPE these templates
    actually use for these helpers -- a bare top-level function name, never a
    member expression or an aliased reference (grep confirms none of
    waitChunkVerdict/rejectedAnywhere/sentinelVerdict is ever referenced any
    other way in any of the three templates). It does not build a parse
    tree; it only has to tell CODE from COMMENT/STRING/TEMPLATE, which is
    exactly the distinction a line-oriented regex cannot make reliably."""
    calls: list[int] = []
    i = 0
    n = len(source)
    prev_ends_expr = False
    prev_word = ""
    prev_is_dot = False
    while i < n:
        c = source[i]

        if c == "/" and i + 1 < n and source[i + 1] == "/":
            j = source.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and source[i + 1] == "*":
            j = source.find("*/", i + 2)
            i = n if j == -1 else j + 2
            prev_ends_expr = True
            prev_is_dot = False
            continue
        if c in ("'", '"'):
            i = _skip_string(source, i)
            prev_ends_expr = True
            prev_is_dot = False
            continue
        if c == "`":
            i = _skip_template(source, i)
            prev_ends_expr = True
            prev_is_dot = False
            continue
        if c == "/" and not (
            prev_ends_expr and prev_word not in _REGEX_ALLOWED_AFTER_WORD
        ):
            skipped = _skip_regex_literal(source, i)
            if skipped is not None:
                i = skipped
                prev_ends_expr = True
                prev_word = ""
                prev_is_dot = False
                continue
            # Not a well-formed regex literal after all -- fall through and
            # treat '/' as an ordinary operator character below.

        if c.isalpha() or c in "_$":
            j = i
            while j < n and (source[j].isalnum() or source[j] in "_$"):
                j += 1
            word = source[i:j]
            k = _skip_ws_and_comments(source, j)
            is_call = (
                word == name
                and k < n and source[k] == "("
                and not prev_is_dot          # not obj.name(...)
                and prev_word != "function"  # not `function name(...)`'s own declaration
            )
            if is_call:
                calls.append(i)
            prev_word = word
            prev_ends_expr = word not in _REGEX_ALLOWED_AFTER_WORD
            prev_is_dot = False
            i = j
            continue

        if c.isdigit():
            j = i
            while j < n and (source[j].isalnum() or source[j] in "._"):
                j += 1
            prev_ends_expr = True
            prev_word = ""
            prev_is_dot = False
            i = j
            continue

        if c == ".":
            prev_ends_expr = False
            prev_word = ""
            prev_is_dot = True
            i += 1
            continue

        if c in ")]":
            prev_ends_expr = True
            prev_word = ""
            prev_is_dot = False
        elif not c.isspace():
            prev_ends_expr = False
            prev_word = ""
            prev_is_dot = False
        # whitespace: prev_is_dot deliberately survives, so `obj . name(...)`
        # (unusual here, but legal JS) is still recognised as member access.
        i += 1

    return calls


def js_call_args(source: str, call_start: int) -> str:
    """Given an offset `js_call_sites` returned (the start of the callee
    identifier), return the raw text between that call's own outer
    parentheses.

    This is NOT a second guess over the whole file -- the call site was
    already located by the tokenizer above; this only reads the arguments of
    that one, already-confirmed, real call, by tracking paren depth through
    the same comment/string/template-aware scan. It exists so a test can ask
    "which of these several real calls to rejectedAnywhere() is the PRECHECK
    one" by reading what each call actually passes, rather than by grepping
    the whole file for a fail-sentinel spelling."""
    n = len(source)
    i = call_start
    while i < n and (source[i].isalnum() or source[i] in "_$"):
        i += 1
    i = _skip_ws_and_comments(source, i)
    assert i < n and source[i] == "(", (
        f"js_call_args() called on an offset that is not a real call site "
        f"(no '(' found): {source[call_start:call_start + 40]!r}"
    )
    open_at = i
    i += 1
    depth = 1
    while i < n and depth > 0:
        c = source[i]
        if c == "/" and i + 1 < n and source[i + 1] == "/":
            j = source.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and source[i + 1] == "*":
            j = source.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in ("'", '"'):
            i = _skip_string(source, i)
            continue
        if c == "`":
            i = _skip_template(source, i)
            continue
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            continue
        i += 1
    return source[open_at + 1:i - 1]


# ---------------------------------------------------------------------------
# Self-test: the tokenizer must actually discriminate CODE from
# COMMENT/STRING/TEMPLATE/REGEX, on fixtures built to fail a naive regex.
# Proved before trusting it against the real templates below, the same
# discipline tests/bounded_poll_present.test.py's own
# test_regression_catcher_helpers_actually_discriminate applies to its
# text-extraction helpers.
# ---------------------------------------------------------------------------

def test_js_call_sites_ignores_comments_and_strings_and_finds_real_calls():
    src = (
        "// waitChunkVerdict(fake) -- a comment MENTIONING the name\n"
        '  const s = "waitChunkVerdict(also fake)";\n'
        "  /* block comment waitChunkVerdict(fake) still fake */\n"
        "  const t = `template waitChunkVerdict(fake) still fake ${1 + 1}`;\n"
        "  verdict = waitChunkVerdict(chunkReply, batch.index)\n"
    )
    sites = js_call_sites(src, "waitChunkVerdict")
    assert len(sites) == 1, f"expected exactly one real call site, found {len(sites)}: {sites}"
    assert src[sites[0]:sites[0] + len("waitChunkVerdict")] == "waitChunkVerdict"


def test_js_call_sites_handles_multiline_and_whitespace_variation():
    src = "waitChunkVerdict(\n    chunkReply,\n    batch.index\n  )\nfn2 (x)\nfn3\t(y)"
    assert len(js_call_sites(src, "waitChunkVerdict")) == 1
    assert len(js_call_sites(src, "fn2")) == 1, "whitespace before '(' must still count as a call"
    assert len(js_call_sites(src, "fn3")) == 1, "a tab before '(' must still count as a call"


def test_js_call_sites_does_not_match_a_property_or_prefix_name():
    src = "obj.waitChunkVerdict(x)\nwaitChunkVerdictExtra(x)\nnotWaitChunkVerdict(x)"
    assert js_call_sites(src, "waitChunkVerdict") == [], (
        "must not match a member-expression call (obj.name(...)) or a name that "
        "merely CONTAINS the target as a substring"
    )


def test_js_call_sites_survives_real_regex_literals_without_desyncing():
    """Regex literals genuinely occur in these templates (mass-translate's
    SEG_ID_RE, glossary's scheme validator). A tokenizer that mis-tokenizes
    `/` as division/comment-start inside one would treat the regex body's own
    `/` as ending a comment or starting a string, corrupting everything read
    afterward -- so the call after it must still be found."""
    src = (
        'const RE = /^[a-z0-9]+\\/[a-z0-9]+$/;\n'
        "if (!RE.test(x)) { waitChunkVerdict(a, b) }\n"
    )
    sites = js_call_sites(src, "waitChunkVerdict")
    assert len(sites) == 1, f"regex literal desynced the tokenizer: found {sites}"


def test_js_call_sites_handles_division_not_mistaken_for_regex():
    """The inverse of the regex test: `a / b` after an identifier is
    division, not the start of a regex literal, and must not swallow the
    rest of the line as if it were one."""
    src = "const ratio = total / count\nwaitChunkVerdict(a, b)\n"
    sites = js_call_sites(src, "waitChunkVerdict")
    assert len(sites) == 1, f"division mistaken for regex desynced the tokenizer: found {sites}"


def test_js_call_args_reads_the_real_arguments_of_a_located_call():
    src = 'rejectedAnywhere(reply, "ABSENT " + batch.index)\nsentinelVerdict(precheck, "PRESENT " + batch.index, "ABSENT " + batch.index)'
    sites = js_call_sites(src, "rejectedAnywhere")
    assert len(sites) == 1
    args = js_call_args(src, sites[0])
    assert args == 'reply, "ABSENT " + batch.index'
    sv_sites = js_call_sites(src, "sentinelVerdict")
    sv_args = js_call_args(src, sv_sites[0])
    assert "PRESENT " in sv_args and "ABSENT " in sv_args


# ---------------------------------------------------------------------------
# THE ACTUAL LOCK -- every wait-verdict decision site in every shipped
# template really does route its reply through waitChunkVerdict().
#
# Counts independently verified before pinning: `grep -n
# "verdict = waitChunkVerdict("` over the three templates finds exactly the
# same 8 lines this tokenizer enumerates (mass-translate :1446/:1457/:1638/
# :1645, glossary :1610/:1623, skeptic :699/:709) -- cross-checked by a
# second method, not merely asserted.
# ---------------------------------------------------------------------------

WAIT_CHUNK_VERDICT_CALL_COUNTS = {
    MASS_TRANSLATE_TEMPLATE: 4,
    GLOSSARY_PASS_TEMPLATE: 2,
    SKEPTIC_PASS_TEMPLATE: 2,
}


@pytest.mark.parametrize(
    "template,expected",
    list(WAIT_CHUNK_VERDICT_CALL_COUNTS.items()),
    ids=[p.name for p in WAIT_CHUNK_VERDICT_CALL_COUNTS],
)
def test_every_wait_verdict_decision_site_calls_the_guarded_reader(template, expected):
    """The reviewer's mutation, made unable to hide: replace a real
    `waitChunkVerdict(...)` call site with a bare `sentinelVerdict(...)` call
    and this count drops -- unlike
    test_all_three_wait_verdicts_agree_on_every_reply_shape above, which
    extracts and calls waitChunkVerdict()'s own DEFINITION directly and never
    notices a call site stopped using it.

    Exact counts, not merely a floor -- independently verified above -- so
    both a lost site (guard bypassed) and a spurious extra one (e.g. a
    reply read twice, once guarded and once not) are caught, not just total
    absence."""
    source = template.read_text(encoding="utf-8")
    sites = js_call_sites(source, "waitChunkVerdict")
    assert len(sites) == expected, (
        f"{template.name}: expected exactly {expected} real call site(s) of "
        f"waitChunkVerdict(), found {len(sites)} at offsets {sites}. A dropped "
        f"count means a wait-verdict decision site stopped routing its reply "
        f"through the guarded reader -- exactly the shape of the mutation that "
        f"found this gap: replacing the call with a bare sentinelVerdict() "
        f"leaves waitChunkVerdict() itself defined and unused, invisible to "
        f"every test that only calls the definition directly"
    )


def test_wait_chunk_verdict_call_sites_sum_to_the_measured_total():
    """A plausibility floor over the whole set, independent of the per-file
    counts above: if the tokenizer silently matched nothing anywhere (a
    broken function-name constant, a glob that resolved to the wrong files),
    every per-file count would read 0 and could be misdiagnosed as a
    legitimate all-templates regression rather than a harness break. Pinning
    the total as well as each part means a harness break and a real
    regression fail with different, distinguishable shapes."""
    total = sum(
        len(js_call_sites(t.read_text(encoding="utf-8"), "waitChunkVerdict"))
        for t in WAIT_CHUNK_VERDICT_CALL_COUNTS
    )
    assert total == 8, f"expected 8 real waitChunkVerdict() call sites across all three templates, found {total}"


# ---------------------------------------------------------------------------
# THE PRECHECK GUARD -- work item 1's own gap, locked the same way so it
# cannot silently reopen. Glossary and skeptic both resume-skip a batch on a
# PRESENT/ABSENT precheck reply; only the ABSENT direction needs the
# containment guard (the PRESENT direction is sentinelVerdict()'s own
# whole-line test). mass-translate has no PRESENT/ABSENT precheck at all, so
# it is not part of this pair.
# ---------------------------------------------------------------------------

PRECHECK_GUARD_TEMPLATES = (GLOSSARY_PASS_TEMPLATE, SKEPTIC_PASS_TEMPLATE)


@pytest.mark.parametrize("template", PRECHECK_GUARD_TEMPLATES, ids=[p.name for p in PRECHECK_GUARD_TEMPLATES])
def test_precheck_absent_direction_is_containment_guarded_at_a_real_call_site(template):
    """Locates the real rejectedAnywhere() call site guarding the PRECHECK's
    ABSENT direction -- distinguished from the WAIT site's own
    rejectedAnywhere() call (which guards PENDING, not ABSENT) by reading
    each real call's own arguments via js_call_args(), never by grepping the
    whole file for a spelling.

    On the pre-1.16.2 skeptic template this goes RED: skeptic's only
    rejectedAnywhere() call site guards "PENDING " + index inside
    waitChunkVerdict, and none guards "ABSENT " + batch.index, so the list
    below is empty and the assertion fails with a count of 0 -- exactly the
    gap work item 1 closes, now locked rather than merely fixed once."""
    source = template.read_text(encoding="utf-8")
    absent_guard_sites = [
        offset
        for offset in js_call_sites(source, "rejectedAnywhere")
        if "ABSENT " in js_call_args(source, offset)
    ]
    assert len(absent_guard_sites) == 1, (
        f"{template.name}: expected exactly one real rejectedAnywhere() call "
        f"site guarding the ABSENT direction (the precheck's resume-skip "
        f"guard), found {len(absent_guard_sites)}. Without it, an ABSENT "
        f"sentinel glued to prose by any non-newline character survives "
        f"sentinelVerdict()'s whole-line fail-priority scan, and a trailing "
        f"clean PRESENT line falsely resume-skips -- measured at 15 of 16 "
        f"gluing characters for exactly this shape"
    )

    precheck_verdict_sites = [
        offset
        for offset in js_call_sites(source, "sentinelVerdict")
        if "PRESENT " in js_call_args(source, offset) and "ABSENT " in js_call_args(source, offset)
    ]
    assert len(precheck_verdict_sites) == 1, (
        f"{template.name}: expected exactly one real sentinelVerdict() call "
        f"reading the PRESENT/ABSENT precheck reply, found {len(precheck_verdict_sites)}"
    )

    # ORDER IS THE PROPERTY, as it is for every other guard in this file: the
    # containment guard must run BEFORE the whole-line verdict test it
    # protects, via `!rejectedAnywhere(...) && sentinelVerdict(...)`. Reversed
    # or unpaired, the guard is dead code for any reply the whole-line test
    # already accepts.
    assert absent_guard_sites[0] < precheck_verdict_sites[0], (
        f"{template.name}: the rejectedAnywhere() ABSENT guard at offset "
        f"{absent_guard_sites[0]} does not run BEFORE the sentinelVerdict() "
        f"precheck test at offset {precheck_verdict_sites[0]}. A guard that "
        f"runs after is dead code for every reply the whole-line test already "
        f"accepts"
    )



# ---------------------------------------------------------------------------
# COMPLETENESS -- no THIRD, unguarded sentinelVerdict() call site anywhere in
# the file, in any function this suite does not already know to check.
#
# tests/bounded_poll_present.test.py carries this class of lock for
# mass-translate (_sentinel_sites_by_function, whole-file) and glossary
# (batchStep's count pinned at 3, waitChunkVerdict's at 1 -- together also a
# whole-file account, since #352 left no other function in that file calling
# sentinelVerdict). skeptic got the SAME waitChunkVerdict machinery this
# release and had no equivalent: the two tests above prove its precheck and
# wait sites are each individually guarded, but neither proves those are the
# ONLY two sentinelVerdict() call sites in the file -- a third, unguarded one
# added anywhere (a copy-pasted citation-review-style check, a debug branch)
# would pass both of them silently.
#
# Chose to land this here rather than in bounded_poll_present.test.py: the
# tokenizer above already gives a uniform, cross-template mechanism, and
# reusing it is stronger than porting bounded_poll_present.test.py's
# per-template regex/extract_function_body machinery a third way. mass and
# glossary are included too, as an independent cross-check of the counts
# their own existing locks already pin -- agreement between two differently
# built mechanisms is worth more than either alone, and a disagreement here
# would mean one of the two methods has a blind spot the other doesn't share.
# ---------------------------------------------------------------------------

SENTINEL_VERDICT_TOTAL_COUNTS = {
    MASS_TRANSLATE_TEMPLATE: 1,   # the wait's own single parse site (waitChunkVerdict); runRound's DRAFT_MISSING site uses mentionedAnywhere(), not a direct call
    GLOSSARY_PASS_TEMPLATE: 4,    # batchStep: precheck + citation prepare + citation judge (3), plus waitChunkVerdict's own wait site (1)
    SKEPTIC_PASS_TEMPLATE: 2,     # batchStep: precheck (1), plus waitChunkVerdict's own wait site (1)
}


@pytest.mark.parametrize(
    "template,expected",
    list(SENTINEL_VERDICT_TOTAL_COUNTS.items()),
    ids=[p.name for p in SENTINEL_VERDICT_TOTAL_COUNTS],
)
def test_no_third_sentinel_verdict_call_site_exists_anywhere_in_the_file(template, expected):
    """Whole-file completeness: the total count of real sentinelVerdict() call
    sites, wherever they live, must equal the number this suite already knows
    to guard. A count that is TOO LOW means a known site went missing (a
    regression the other tests here would also catch); a count that is TOO
    HIGH means a NEW, unaudited call site was added somewhere this suite has
    never looked -- exactly the shape of the reviewer's own finding, one
    level up: not "does the known site route through the guard" but "is the
    known site the ONLY site"."""
    source = template.read_text(encoding="utf-8")
    sites = js_call_sites(source, "sentinelVerdict")
    assert len(sites) == expected, (
        f"{template.name}: expected exactly {expected} real sentinelVerdict() "
        f"call site(s) in the whole file, found {len(sites)} at offsets {sites}. "
        f"If this is a NEW, legitimate call site, it must be guarded (a "
        f"preceding rejectedAnywhere() on the same reply and fail sentinel, or "
        f"routed through waitChunkVerdict()) and added to the accounting this "
        f"test pins -- do not just bump the number"
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
