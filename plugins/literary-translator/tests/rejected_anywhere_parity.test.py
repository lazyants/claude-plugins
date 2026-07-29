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

import importlib.util
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


def _scan_code(
    source: str, i: int, n: int, name: str, calls: list[int], stop_at_brace_depth_zero: bool
) -> int:
    """The tokenizer's one real scanning loop. Walks `source[i:n)` as CODE,
    appending the offset of every real call to `name` into `calls`, and
    returns the index it stopped at.

    `stop_at_brace_depth_zero` is what makes this function do double duty:
    False for a top-level or whole-function scan (walks all the way to `n`
    and returns it); True when scanning the INSIDE of a `${...}` template
    interpolation, where an unmatched `}` (brace depth returning to zero)
    ends the interpolation and must end the scan too -- mirroring
    `_skip_interpolation`'s own depth tracking so a nested object/block
    literal inside the interpolation does not end it early. In that mode the
    return value is the index just past that closing `}`.

    This single loop, called both at the top level and (via `_scan_template`,
    recursively) inside every interpolation, is what makes `js_call_sites`'s
    own claim ("recursing into `${...}` interpolations, which are code
    again") actually true rather than aspirational: a call written inside an
    interpolation is found by the SAME call-detection code that finds one
    anywhere else, not silently skipped while the enclosing template literal
    is jumped past whole. `_skip_template`/`_skip_interpolation` above still
    exist and are still used by `js_call_args` -- they only need to find the
    matching backtick/brace, not call sites, so they stay the cheaper,
    non-recursing-for-calls tool for that job."""
    depth = 0  # only meaningful when stop_at_brace_depth_zero
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
            # Transparent, like the line-comment branch above: leave
            # prev_ends_expr/prev_word/prev_is_dot exactly as they were
            # BEFORE the comment, not force-set. Round-4 codex finding (C3):
            # forcing prev_ends_expr=True here made `const r = /* gap */ /`/`
            # -- a regex literal split from its `=` only by a comment --
            # misread the following `/` as division (since regex-detection
            # requires prev_ends_expr False), so the stray `/` was treated as
            # an ordinary operator and the backtick right after it was then
            # read as an OPENING template literal with no matching close,
            # corrupting everything read afterward, including the real call
            # this exists to find. A comment carries no grammatical state of
            # its own; whatever ended before it (or didn't) must still be
            # true after it.
            j = source.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in ("'", '"'):
            i = _skip_string(source, i)
            prev_ends_expr = True
            prev_is_dot = False
            continue
        if c == "`":
            i = _scan_template(source, i, name, calls)
            prev_ends_expr = True
            prev_is_dot = False
            continue
        if c in "+-" and i + 1 < n and source[i + 1] == c:
            # `++`/`--`. POSTFIX (prev_ends_expr already True -- follows an
            # identifier/`)`/`]`/number) leaves the value it operated on in
            # expression position, so a `/` right after it must still read as
            # division, not a regex start: prev_ends_expr is left AS IS,
            # unlike the generic operator fallback below, which unconditionally
            # resets it to False. Round-4 codex finding (C3): that
            # unconditional reset is what made `a++ / waitChunkVerdict(real) /
            # b` misread the postfix `++` as ending expression position,
            # sending the following `/` down the regex-literal path, which
            # then swallowed the real call as if it were inside the "regex"
            # body. PREFIX (`++a`, prev_ends_expr already False) does not end
            # an expression either way, so it is left unchanged too --
            # matching the generic fallback's own behaviour for this case,
            # which is why prefix needed no special handling to begin with.
            prev_word = ""
            prev_is_dot = False
            i += 2
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

        if stop_at_brace_depth_zero and c == "{":
            depth += 1
        elif stop_at_brace_depth_zero and c == "}":
            if depth == 0:
                return i + 1
            depth -= 1

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

    return n


def _scan_template(source: str, start: int, name: str, calls: list[int]) -> int:
    """`js_call_sites`'s own template walker: same backtick-matching job as
    `_skip_template`, but every `${...}` interpolation is handed to
    `_scan_code` (in brace-depth-stopping mode) instead of `_skip_interpolation`
    -- so a real call to `name` written inside an interpolation is appended to
    `calls` on the way past, rather than being skipped over along with the
    rest of the template text. Nested templates inside an interpolation
    recurse back into this same function via `_scan_code`'s own backtick
    handling, so depth is unbounded, matching `_skip_template`/
    `_skip_interpolation`'s mutual recursion."""
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
            i = _scan_code(source, i + 2, n, name, calls, stop_at_brace_depth_zero=True)
            continue
        i += 1
    return n


def js_call_sites(source: str, name: str) -> list[int]:
    """Character offsets in `source` where the bare identifier `name` is
    CALLED -- i.e. appears as a standalone token in CODE, immediately
    followed (across any whitespace/comments) by `(`, that is neither a
    member-expression call (`obj.name(...)`) nor the function's own
    declaration (`function name(...) {`). Includes calls written inside a
    template literal's `${...}` interpolation: that content is CODE again,
    scanned by the same `_scan_code` loop used everywhere else, not skipped
    past as part of the literal's text (see `_scan_template`).

    Deliberately narrow: it recognises exactly the call SHAPE these templates
    actually use for these helpers -- a bare top-level function name, never a
    member expression or an aliased reference (grep confirms none of
    waitChunkVerdict/rejectedAnywhere/sentinelVerdict is ever referenced any
    other way in any of the three templates). It does not build a parse
    tree; it only has to tell CODE from COMMENT/STRING/TEMPLATE, which is
    exactly the distinction a line-oriented regex cannot make reliably."""
    calls: list[int] = []
    _scan_code(source, 0, len(source), name, calls, stop_at_brace_depth_zero=False)
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


def js_call_end(source: str, call_start: int) -> int:
    """Given an offset `js_call_sites` returned, return the index just past
    that call's own closing parenthesis.

    Mirrors `js_call_args()`'s own paren-depth walk, but returns the boundary
    instead of the argument text -- kept as a separate walk rather than a
    refactor of `js_call_args()` so an already-tested function is not
    disturbed. It exists so a test can ask what happens immediately AFTER one
    real, already-located call -- e.g. whether it is followed by `&&` and
    then another real call, with nothing else between them -- which is what
    distinguishes a guard actually WIRED into the expression it protects from
    two calls that merely occur in the same function with one offset smaller
    than the other."""
    n = len(source)
    i = call_start
    while i < n and (source[i].isalnum() or source[i] in "_$"):
        i += 1
    i = _skip_ws_and_comments(source, i)
    assert i < n and source[i] == "(", (
        f"js_call_end() called on an offset that is not a real call site "
        f"(no '(' found): {source[call_start:call_start + 40]!r}"
    )
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
    return i


def _negation_index(source: str, call_start: int):
    """Index of the `!` immediately negating the call at `call_start`
    (skipping only whitespace backward), or `None` if the call is not written
    as `!name(...)`. Returns the index rather than a bool so a caller can
    slice from it (see `extract_precheck_decision_expression`)."""
    i = call_start - 1
    while i >= 0 and source[i].isspace():
        i -= 1
    return i if i >= 0 and source[i] == "!" else None


def _immediately_ands_into(source: str, call_end: int, next_call_start: int) -> bool:
    """True when, reading forward from `call_end` (an index just past a
    call's own closing paren, e.g. from `js_call_end`), the only thing before
    `next_call_start` is `&&` and whitespace/comments -- i.e. the two calls
    are joined in ONE boolean expression via `&&`, immediately adjacent, not
    merely two calls whose offsets happen to be ordered.

    This is the check that closes the escape a same-file mutation found: two
    real calls to the right helpers, in the right order, are NOT enough --
    an earlier, unrelated, unused sibling guard call satisfies "order" while
    contributing nothing to the actual decision. Only a guard that `&&`s
    directly into the verdict it protects is doing any guarding."""
    i = _skip_ws_and_comments(source, call_end)
    if source[i:i + 2] != "&&":
        return False
    i = _skip_ws_and_comments(source, i + 2)
    return i == next_call_start


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


def test_js_call_sites_handles_postfix_increment_before_division():
    """Round-4 codex finding (C3), measured against the pre-fix tokenizer
    (`git show` of this file at the commit before this test was added): `a++`
    ends an expression the same way a bare identifier does, so a `/` right
    after it must read as division. The pre-fix tokenizer's generic operator
    fallback unconditionally reset prev_ends_expr on EVERY non-space,
    non-word character -- including each `+` of a postfix `++` -- so the `/`
    that followed was misread as a possible regex start, and
    `_skip_regex_literal` then consumed everything up to the NEXT `/` as if
    it were the regex body, swallowing the real call site whole."""
    src = "const x = a++ / waitChunkVerdict(real) / b;\n"
    sites = js_call_sites(src, "waitChunkVerdict")
    assert len(sites) == 1, (
        f"postfix `++` before `/` desynced the tokenizer: found {sites}. If "
        f"empty, the regex/division disambiguation has regressed to treating "
        f"the `/` after `a++` as a possible regex start again"
    )

    # Prefix `++a` must still work exactly as before -- prefix does not end
    # an expression either way, so the fix must not perturb it.
    src_prefix = "let y = ++a / waitChunkVerdict(real);\n"
    assert len(js_call_sites(src_prefix, "waitChunkVerdict")) == 1

    # Postfix `--` (the sibling operator) must be handled the same way.
    src_decr = "const z = a-- / waitChunkVerdict(real) / b;\n"
    assert len(js_call_sites(src_decr, "waitChunkVerdict")) == 1


def test_js_call_sites_treats_block_comments_as_transparent():
    """Round-4 codex finding (C3), measured against the pre-fix tokenizer:
    the block-comment branch force-set prev_ends_expr=True after every `/*
    ... */`, unlike the line-comment branch just above it (which correctly
    leaves prev_ends_expr untouched, transparent to whatever came before).
    That inconsistency made `const r = /* gap */ /`/` -- a regex literal
    split from its `=` only by a comment -- misread the `/` right after the
    comment as division (regex-detection requires prev_ends_expr False), so
    the stray `/` fell through as an ordinary operator and the backtick
    immediately after it was then read as an OPENING template literal with
    no matching close, corrupting everything scanned afterward, including
    the real call this test drives."""
    src = "const r = /* gap */ /`/; waitChunkVerdict(real);\n"
    sites = js_call_sites(src, "waitChunkVerdict")
    assert len(sites) == 1, (
        f"a block comment before a regex literal desynced the tokenizer: "
        f"found {sites}. If empty, block comments have regressed to forcing "
        f"prev_ends_expr=True instead of staying transparent"
    )

    # Round-5 finding F3: the fixture that used to stand here for the OPPOSITE
    # overcorrection (a block comment sitting where DIVISION is expected,
    # wrongly forcing prev_ends_expr=False) put its target call on the
    # FOLLOWING line: "const ratio = total /* gap */ / count\nwaitChunkVerdict(a, b)\n".
    # That cannot discriminate the mutation it claims to guard: under a
    # force-False bug the `/` after the comment tries `_skip_regex_literal`,
    # which immediately hits the `\n` before the next `/` and returns None
    # (a well-formed regex literal cannot span a line) -- so it falls through
    # to "ordinary operator" exactly as the correct, transparent path does,
    # and the call on the next line is found either way. Green whether the
    # bug is present or not is not a lock on anything.
    #
    # Codex's discriminating fixture keeps the call on the SAME line, between
    # the two division slashes, so a wrongly-started regex literal actually
    # swallows it: correctly (prev_ends_expr transparent, staying True from
    # "total"), the `/` right after the comment is read as ordinary division
    # and the scan proceeds character-by-character through the call,
    # finding it; under the force-False bug, that same `/` triggers
    # `_skip_regex_literal`, which -- with no `\n` in the way this time --
    # runs all the way to the SECOND `/` (before " count") and swallows
    # " waitChunkVerdict(real) " whole as if it were regex body text, so the
    # call is never scanned as code at all.
    src_division = "total /* gap */ / waitChunkVerdict(real) / count\n"
    sites_division = js_call_sites(src_division, "waitChunkVerdict")
    assert len(sites_division) == 1, (
        f"a block comment sitting where division is expected desynced the "
        f"tokenizer: found {sites_division}. If empty, block comments have "
        f"regressed to forcing prev_ends_expr=False instead of staying "
        f"transparent, and the '/' right after the comment was misread as "
        f"the start of a regex literal that swallowed the real call whole"
    )


def test_js_call_sites_recurses_into_template_interpolations():
    """The claim this file's own tokenizer overview makes ("recursing into
    `${...}` interpolations, which are code again") must be true, not
    aspirational. Measured directly against the pre-fix tokenizer (`git show`
    of this file at the commit before this test was added): calling the OLD
    `js_call_sites` on a call written inside a `${...}` interpolation returned
    `[]` -- `_skip_template` jumped straight past the whole literal via
    `_skip_interpolation`, which matches braces/strings/templates correctly
    but never reports a call site back to its caller. Since exhaustiveness is
    this gate's entire purpose (the whole-file completeness tests two
    sections below rely on it), a call site hiding inside an interpolation
    would silently undercount every one of them."""
    src = "const t = `plain ${waitChunkVerdict(a, b)} text`;\n"
    sites = js_call_sites(src, "waitChunkVerdict")
    assert len(sites) == 1, (
        f"expected the call inside the `${{...}}` interpolation to be found, "
        f"got {sites}. If this is empty, the tokenizer has regressed to "
        f"skipping interpolations wholesale instead of scanning them as code"
    )
    assert src[sites[0]:sites[0] + len("waitChunkVerdict")] == "waitChunkVerdict"

    # A nested template inside the interpolation must not desync the scan,
    # and a call inside THAT nested template must also be found -- unbounded
    # recursion, not one level deep.
    src_nested = "const t = `outer ${ `inner ${rejectedAnywhere(x, y)}` } end`;\n"
    nested_sites = js_call_sites(src_nested, "rejectedAnywhere")
    assert len(nested_sites) == 1, (
        f"expected the call inside a NESTED interpolation to be found too, "
        f"got {nested_sites}"
    )

    # And a call OUTSIDE the template, after it, must still be found -- the
    # scan must correctly return to top-level CODE once the template closes.
    src_after = "const t = `${waitChunkVerdict(a, b)}`;\nrejectedAnywhere(x, y)\n"
    assert len(js_call_sites(src_after, "waitChunkVerdict")) == 1
    assert len(js_call_sites(src_after, "rejectedAnywhere")) == 1


def test_js_call_args_reads_the_real_arguments_of_a_located_call():
    src = 'rejectedAnywhere(reply, "ABSENT " + batch.index)\nsentinelVerdict(precheck, "PRESENT " + batch.index, "ABSENT " + batch.index)'
    sites = js_call_sites(src, "rejectedAnywhere")
    assert len(sites) == 1
    args = js_call_args(src, sites[0])
    assert args == 'reply, "ABSENT " + batch.index'
    sv_sites = js_call_sites(src, "sentinelVerdict")
    sv_args = js_call_args(src, sv_sites[0])
    assert "PRESENT " in sv_args and "ABSENT " in sv_args


def test_js_call_end_finds_the_real_closing_paren_across_lines():
    src = (
        "sentinelVerdict(\n  precheck,\n  \"PRESENT \" + batch.index,\n"
        "  \"ABSENT \" + batch.index\n)\nafter()"
    )
    sites = js_call_sites(src, "sentinelVerdict")
    assert len(sites) == 1
    end = js_call_end(src, sites[0])
    assert src[end:end + 6] == "\nafter", (
        f"expected the end offset to land just past the call's own closing "
        f"paren, got context {src[max(end - 5, 0):end + 10]!r}"
    )


def test_negation_and_and_adjacency_helpers_pair_a_real_guard_and_verdict():
    src = "if (!guardFn(x) &&\n    verdictFn(x)) {}"
    guard_start = src.index("guardFn")
    verdict_start = src.index("verdictFn")
    assert _negation_index(src, guard_start) == src.index("!")
    guard_end = js_call_end(src, guard_start)
    assert _immediately_ands_into(src, guard_end, verdict_start)


def test_negation_and_and_adjacency_helpers_reject_an_unrelated_sibling_guard():
    """The exact escape C1 closes, reduced to the helpers that close it: an
    earlier, un-negated, unused sibling guard call satisfies neither
    predicate against a verdict call it has no `&&` relationship with, even
    though its offset is smaller (the property the old, offset-only check
    could not tell apart from a real guard)."""
    src = "guardFn(y)\nsomeOtherStatement()\nif (verdictFn(x)) {}"
    unrelated_guard = src.index("guardFn")
    verdict_start = src.index("verdictFn")
    assert _negation_index(src, unrelated_guard) is None
    guard_end = js_call_end(src, unrelated_guard)
    assert not _immediately_ands_into(src, guard_end, verdict_start)


# ---------------------------------------------------------------------------
# THE ACTUAL LOCK -- every wait-verdict decision site in every shipped
# template really does route its reply through waitChunkVerdict().
#
# Counts independently verified before pinning: `grep -n
# "verdict = waitChunkVerdict("` over the three templates finds exactly the
# same 8 lines this tokenizer enumerates -- cross-checked by a second method,
# not merely asserted. Exact line numbers are deliberately NOT cited here:
# this file's own test history shows they drift with every unrelated edit to
# a template's prose (a stale citation was itself a finding this round), so
# the total (8) and the per-template counts below are the load-bearing,
# checked claims; re-run the grep above for current line numbers rather than
# trusting any pinned here.
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
    gap work item 1 closes, now locked rather than merely fixed once.

    SAME EXPRESSION, not merely earlier offset, is what is checked below. An
    independent reviewer measured the escape in the ordering-only version of
    this test: delete the real guard from the precheck expression, then add
    an earlier, UNUSED sibling `rejectedAnywhere("ABSENT " + ...)` call
    anywhere else in the file. Both counts above stay at 1 and the deleted
    guard's replacement sibling's offset is still smaller than the verdict's
    -- ordering alone cannot tell a guard that is actually wired into the
    decision from one that merely occurs earlier in the file. The two checks
    below replace pure offset-ordering with same-statement pairing:
    `!rejectedAnywhere(...) && sentinelVerdict(...)`, negated and `&&`-joined
    with nothing else between the guard's own closing paren and the verdict
    call's own opening paren. See
    test_precheck_decision_expressions_agree_and_never_resume_on_a_mentioned_absent
    below for the complementary BEHAVIOURAL lock: this test proves the wiring
    is textually right; that one proves it actually DECIDES right by running
    it."""
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

    guard_start = absent_guard_sites[0]
    verdict_start = precheck_verdict_sites[0]

    # SAME-STATEMENT PAIRING, not offset ordering: the guard must be written
    # `!rejectedAnywhere(...)`, immediately negated --
    assert _negation_index(source, guard_start) is not None, (
        f"{template.name}: the rejectedAnywhere() ABSENT guard call at offset "
        f"{guard_start} is not immediately negated with `!`. An un-negated "
        f"guard call taking the resume-skip branch when it returns true is "
        f"backwards -- a TRUE result from rejectedAnywhere() means the reply "
        f"DID mention ABSENT, which is exactly when resuming must NOT happen"
    )
    guard_end = js_call_end(source, guard_start)
    # -- and it must `&&` directly into the sentinelVerdict() call it
    # protects, with nothing else between them. This is what an unrelated
    # earlier sibling guard call cannot satisfy: its own closing paren is not
    # immediately followed by `&& sentinelVerdict(...)`, however much earlier
    # its offset sits than the verdict's.
    assert _immediately_ands_into(source, guard_end, verdict_start), (
        f"{template.name}: the rejectedAnywhere() ABSENT guard at offset "
        f"{guard_start} does not `&&` directly into the sentinelVerdict() "
        f"precheck verdict at offset {verdict_start}, with nothing else "
        f"between them. Offset ordering alone is satisfiable by an unrelated, "
        f"UNUSED sibling rejectedAnywhere('ABSENT ' ...) call placed anywhere "
        f"earlier in the file -- SAME EXPRESSION, not merely earlier offset, "
        f"is the property that actually wires the guard to the decision it "
        f"protects"
    )


def extract_precheck_decision_expression(source: str, template_path: Path) -> str:
    """The real `!rejectedAnywhere(...) && sentinelVerdict(...)` precheck
    decision expression, read directly out of the template rather than
    reconstructed by hand: from the `!` immediately negating the real
    rejectedAnywhere() ABSENT guard call, through the closing paren of the
    real sentinelVerdict() call it `&&`s into. Both templates write this
    expression inside a different surrounding statement -- glossary as
    `const resumed = <expr>`, skeptic as `if (<expr>) {` -- so this reads the
    EXPRESSION itself, independent of the statement shape around it, which is
    what lets one harness (below) drive both templates' real decisions
    through node."""
    guard_sites = [
        offset
        for offset in js_call_sites(source, GUARD_HELPER)
        if "ABSENT " in js_call_args(source, offset)
    ]
    assert len(guard_sites) == 1, (
        f"{template_path.name}: expected exactly one real {GUARD_HELPER}() "
        f"call site guarding the ABSENT direction, found {len(guard_sites)}"
    )
    verdict_sites = [
        offset
        for offset in js_call_sites(source, "sentinelVerdict")
        if "PRESENT " in js_call_args(source, offset) and "ABSENT " in js_call_args(source, offset)
    ]
    assert len(verdict_sites) == 1, (
        f"{template_path.name}: expected exactly one real sentinelVerdict() "
        f"call reading the PRESENT/ABSENT precheck reply, found {len(verdict_sites)}"
    )
    guard_start = guard_sites[0]
    verdict_start = verdict_sites[0]
    neg = _negation_index(source, guard_start)
    assert neg is not None, (
        f"{template_path.name}: the {GUARD_HELPER}() ABSENT guard at offset "
        f"{guard_start} is not immediately negated with `!`, so it cannot be "
        f"read as a self-contained `!{GUARD_HELPER}(...) && sentinelVerdict(...)` "
        f"expression"
    )
    verdict_end = js_call_end(source, verdict_start)
    return source[neg:verdict_end]


# PRECHECK_REPLY_SHAPES is mechanically DERIVED from the already-shipped
# PARITY_REPLY_SHAPES table above (the wait site's own reply-shape table),
# substituting the precheck's PRESENT/ABSENT sentinel vocabulary for the wait
# site's READY/PENDING -- not a second, hand-picked set. A hand-picked set is
# itself a finding this round (F3: "eleven reply shapes" named a set that
# existed nowhere in the shipped code): deriving this one from the shipped
# table means every glue character and edge case the wait site already covers
# -- space/tab/CR/nbsp/zwsp/letter gluing, decorated replies, other-batch
# replies, empty/whitespace-only/tool-killed/unparseable replies -- is
# exercised here too, traceably, rather than re-invented.
PRECHECK_REPLY_SHAPES = {
    shape: reply.replace("READY", "PRESENT").replace("PENDING", "ABSENT")
    for shape, reply in PARITY_REPLY_SHAPES.items()
}


def _run_precheck_decision(template: Path, source: str, shape: str, reply: str, tmp_path: Path) -> bool:
    """Runs a template's REAL, extracted precheck decision expression (see
    extract_precheck_decision_expression) under node against one `reply`, and
    returns the boolean it evaluates to. Mirrors
    test_all_three_wait_verdicts_agree_on_every_reply_shape's own
    extract-and-execute harness, one section up, for the precheck decision
    instead of the wait decision."""
    expr = extract_precheck_decision_expression(source, template)
    fns = "\n".join(
        extract_named_function(source, n, template)
        for n in ("sentinelVerdict", GUARD_HELPER)
        if re.search(rf"^function {n}\(", source, re.MULTILINE)
    )
    harness = (
        fns
        + "\nconst precheck = " + json.dumps(reply)
        + ";\nconst batch = { index: 0 };"
        + "\nprocess.stdout.write(String(!!(" + expr + ")));\n"
    )
    p = tmp_path / f"precheck_{template.stem}_{shape}.js"
    p.write_text(harness, encoding="utf-8")
    assert NODE is not None
    proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, (
        f"{template.name}'s precheck decision expression threw on {shape}: "
        f"{proc.stderr}\nexpression: {expr}"
    )
    # Node's String(!!(...)) prints JS's lowercase "true"/"false" -- NOT
    # Python's capitalized True/False. Caught by running this against the
    # real 190ac36 pre-fix skeptic template: a "True" comparison here made
    # every shape read as `resumed=False` regardless of what the expression
    # actually evaluated to, silently defeating both properties this test
    # checks (agreement and containment) at once.
    return proc.stdout.strip() == "true"


@pytest.mark.skipif(NODE is None, reason="node not found on PATH")
@pytest.mark.parametrize("shape", sorted(PRECHECK_REPLY_SHAPES), ids=sorted(PRECHECK_REPLY_SHAPES))
def test_precheck_decision_expressions_agree_and_never_resume_on_a_mentioned_absent(shape, tmp_path):
    """BEHAVIOURAL lock for work item 1's own gap (C1) -- the complement to
    test_precheck_absent_direction_is_containment_guarded_at_a_real_call_site
    above, the same way test_all_three_wait_verdicts_agree_on_every_reply_shape
    complements the wait site's structural call-count tests.

    Why the structural test is not enough on its own, even tightened to
    same-statement pairing: it proves the WIRING is textually right. It
    cannot prove the wired-together expression actually DECIDES right --
    that requires running it. This test extracts the real decision
    expression (verbatim, via extract_precheck_decision_expression) and
    drives it under node for every shape in PRECHECK_REPLY_SHAPES.

    Two properties are checked, deliberately different in what they can
    prove:

    1. AGREEMENT. glossary's precheck guard predates this branch and every
       commit on it (verified back to the merge base, 4343994); skeptic's
       is this release's own fix (absent through 190ac36, the last commit
       before it landed -- `git show 190ac36:<path to skeptic's template>`
       has no `!rejectedAnywhere(precheck, "ABSENT " + ...)` anywhere). So
       requiring the two templates' real decisions to agree is a genuine
       regression lock: it goes RED against 190ac36's skeptic template on
       every ABSENT-mentioning shape below, because glossary correctly
       refuses to resume-skip while pre-fix skeptic's un-guarded
       sentinelVerdict() whole-line test resume-skips on the shape's
       trailing clean PRESENT line. It goes GREEN on the current tree.
    2. CONTAINMENT, independent of agreement and NOT a reimplementation of
       sentinelVerdict()'s own fail-priority scan: a reply that mentions
       "ABSENT" anywhere must never resume-skip, full stop. That is the
       guarantee this guard exists to provide, and it is statable without
       re-deriving sentinelVerdict()'s line-splitting algorithm -- so a
       second, independently-reasoned property, not merely a restatement of
       the first."""
    reply = PRECHECK_REPLY_SHAPES[shape].replace("<idx>", "0")
    resumed = {}
    for template in PRECHECK_GUARD_TEMPLATES:
        source = template.read_text(encoding="utf-8")
        resumed[template.name] = _run_precheck_decision(template, source, shape, reply, tmp_path)

    assert len(set(resumed.values())) == 1, (
        f"the precheck decision expressions in "
        f"{[t.name for t in PRECHECK_GUARD_TEMPLATES]} DISAGREE on reply shape "
        f"{shape!r}: {resumed}\nReply: {reply!r}\n"
        f"glossary's guard predates this branch; skeptic's is this release's "
        f"own fix. A disagreement here is exactly the historical gap: skeptic "
        f"resume-skipping a reply glossary correctly rejects"
    )

    if "ABSENT" in reply:
        resuming = [name for name, v in resumed.items() if v]
        assert not resuming, (
            f"a precheck reply mentioning ABSENT anywhere ({reply!r}, shape "
            f"{shape!r}) resume-skipped in {resuming}. This is the actual "
            f"defect this guard exists to close: a fragment reported ABSENT "
            f"must never be treated as complete, however a trailing line is "
            f"decorated"
        )


# ---------------------------------------------------------------------------
# THE FULL GLUE_CHARS POPULATION -- PRECHECK_REPLY_SHAPES above samples only
# 6 glue characters (space/tab/cr/nbsp/zwsp/letter); the release notes state
# the precheck figure as "15 of the 16 characters ... over the shared
# GLUE_CHARS set" and point a reader at this file's precheck coverage for it.
# A gate that samples 6 of 16 does not lock a claim about all 16 -- the same
# claim-stronger-than-the-check-that-backs-it defect this release exists to
# close, one level up from the individual claim fixes. This section extends
# coverage to the full population, ADDITIONAL to (not a replacement for)
# PRECHECK_REPLY_SHAPES above.
#
# GLUE_CHARS is loaded from tests/glossary_citation_review.test.py rather
# than hand-copied here: a second, independently-typed copy of a
# security-relevant character population is exactly the kind of duplicate
# this file's whole existence argues against (see the module docstring's
# "WHY THIS FILE EXISTS AT ALL"). Loading the real object means a future
# change to the shipped population is inherited automatically instead of
# silently diverging from a frozen copy.
# ---------------------------------------------------------------------------


def _load_glue_chars() -> list[tuple[str, str]]:
    """Dynamically imports tests/glossary_citation_review.test.py (via
    importlib, the pattern this test suite already uses elsewhere to reach
    another file's module-level objects) and returns its GLUE_CHARS list.

    Plain `import` cannot reach it: the file is named with two dots
    (`glossary_citation_review.test.py`), which is not an importable module
    path. Executing the whole module has a real cost -- every top-level
    statement in that ~2200-line file runs -- but inspection before relying
    on this found nothing expensive: constant assignments, function/test
    defs (bodies don't run at import time), and one cheap module-level
    `assert ... .is_file()` that is already true independent of anything
    this file does."""
    path = PLUGIN_ROOT / "tests" / "glossary_citation_review.test.py"
    assert path.is_file(), f"expected sibling test file not found: {path}"
    spec = importlib.util.spec_from_file_location(
        "_glossary_citation_review_for_glue_chars", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GLUE_CHARS


GLUE_CHARS = _load_glue_chars()

# Runtime-value guards, not a source/byte scan: a dict or list literal whose
# entries silently collapsed to the same character would still LOOK like 16
# lines of source. Checked here, at load time, rather than trusted from the
# sibling file's own claim to have 16 entries.
assert len(GLUE_CHARS) == 16, (
    f"tests/glossary_citation_review.test.py's GLUE_CHARS no longer has 16 "
    f"entries (found {len(GLUE_CHARS)}). The precheck glue coverage below is "
    f"derived from this population, so a change in its size must be a "
    f"deliberate, visible one -- update this count to match, do not silently "
    f"adapt to whatever it now is"
)
for _glue_name, _glue_char in GLUE_CHARS:
    assert isinstance(_glue_char, str) and len(_glue_char) == 1, (
        f"GLUE_CHARS entry {_glue_name!r} is not a single codepoint: "
        f"{_glue_char!r} (len {len(_glue_char)}). A collapsed or dropped "
        f"character here would silently shrink the population this gate "
        f"drives while every name still prints"
    )
del _glue_name, _glue_char

# Same glued-ABSENT-then-clean-PRESENT shape PRECHECK_REPLY_SHAPES's own
# glued_* entries use (the measured defect direction), parameterized over
# the full population instead of the 6-character sample.
PRECHECK_GLUE_REPLY_SHAPES = {
    f"glue_{name}": f"the chunk was cut short{char}ABSENT <idx>\nPRESENT <idx>"
    for name, char in GLUE_CHARS
}

# IDENTITY, not shape (round 4, C2). Every check above -- 16 entries, each a
# single codepoint, 16 distinct values -- passes UNCHANGED if GLUE_CHARS's
# "lsep_u2028" entry is silently repointed from chr(0x2028) to the plain
# letter "y": still 16 entries, still a single codepoint each, still 16
# distinct values. Every reply PRECHECK_GLUE_REPLY_SHAPES generates still
# contains "ABSENT" regardless of which character glued it, so the
# behavioral assertions stay green too -- a codepoint-specific regression
# would lose its ONLY coverage in total silence, with every guard above and
# every downstream test reporting a clean 16 of 16.
GLUE_CHARS_BY_NAME = dict(GLUE_CHARS)
assert len(GLUE_CHARS_BY_NAME) == len(GLUE_CHARS), (
    f"GLUE_CHARS has a duplicate name -- dict(GLUE_CHARS) collapsed "
    f"{len(GLUE_CHARS)} entries down to {len(GLUE_CHARS_BY_NAME)}"
)

# Round-5 finding F2: round 4 pinned only the four members with non-obvious
# semantics -- U+2028/U+2029 are treated as line breaks by str.splitlines()
# but NOT by split("\n") (what sentinelVerdict() actually calls), U+0085 is
# NOT stripped by trim() the way U+2028/U+2029 are, and zwsp (U+200B) is
# invisible in a diff or terminal, the least likely substitution to be caught
# by eye. But codex measured that the SAME class of mutation also defeats the
# release notes' own historical claim through any of the OTHER twelve names:
# changing `("lf", chr(0x0A))` to `("lf", "y")` preserves every shape-only
# guard above -- still 16 entries, still single codepoints, still 16 distinct
# values, and every PRECHECK_GLUE_REPLY_SHAPES entry still contains "ABSENT"
# so the behavioural assertions stay green too -- while silently flipping the
# release notes' "lf is the sole non-offender, 15 of 16 falsely resume-skip"
# claim to "16 of 16 non-offenders" (lf's pre-fix good behaviour was the
# reason it was excluded from the glued_* shapes in PRECHECK_REPLY_SHAPES to
# begin with; a repointed "lf" entry would no longer be testing lf at all).
# So the full name-to-codepoint mapping is pinned, not just the four
# non-obvious ones -- every member is something a claim in this release's own
# notes is actually about.
REQUIRED_GLUE_IDENTITIES = {
    "space": 0x20,
    "tab": 0x09,
    "lf": 0x0A,
    "cr": 0x0D,
    "vt": 0x0B,
    "ff": 0x0C,
    "fs_u001c": 0x1C,
    "gs_u001d": 0x1D,
    "rs_u001e": 0x1E,
    "us_u001f": 0x1F,
    "nbsp_u00a0": 0xA0,
    "nel_u0085": 0x0085,
    "lsep_u2028": 0x2028,
    "psep_u2029": 0x2029,
    "zwsp_u200b": 0x200B,
    "letter_x": ord("x"),
}


def test_glue_chars_pins_the_specific_high_risk_codepoints():
    """IDENTITY, not shape. `test_precheck_glue_reply_shapes_cover_all_sixteen_glue_chars`
    below and the module-level guards above prove the population has 16
    entries, each a genuine single codepoint, all 16 distinct -- and codex
    round 4 measured that every one of those properties survives a mutation
    that replaces `("lsep_u2028", chr(0x2028))` with `("lsep_u2028", "y")`.
    That mutation is invisible to shape-only checks because "y" is also a
    single, distinct codepoint; it is only visible by looking up the NAME and
    checking it maps to the codepoint the name claims.

    Round-5 finding F2 extended this from 4 pinned names to all 16 -- see
    the module-level comment above REQUIRED_GLUE_IDENTITIES for why the other
    twelve are not "obvious semantics, safe to leave unpinned": the SAME
    repointing mutation applied to any of them survives every check that
    existed before this extension."""
    for name, expected_codepoint in REQUIRED_GLUE_IDENTITIES.items():
        assert name in GLUE_CHARS_BY_NAME, (
            f"expected a GLUE_CHARS entry named {name!r}, not found. Names "
            f"present: {sorted(GLUE_CHARS_BY_NAME)}"
        )
        actual = GLUE_CHARS_BY_NAME[name]
        assert ord(actual) == expected_codepoint, (
            f"GLUE_CHARS[{name!r}] is U+{ord(actual):04X} ({actual!r}), "
            f"expected U+{expected_codepoint:04X}. A silent repointing to an "
            f"arbitrary stand-in character (codex round 4's own mutation: "
            f"chr(0x2028) -> \"y\") passes every shape-only check in this "
            f"file -- 16 entries, single codepoints, 16 distinct -- while "
            f"this specific codepoint's coverage disappears entirely"
        )


def test_precheck_glue_reply_shapes_cover_all_sixteen_glue_chars():
    """Runtime-length guard, per the lesson behind this section: a dict built
    from two colliding literal characters silently keeps only the LAST
    value, so a source scan of this file would show 16 comprehension lines
    while the collection actually driving the test below silently became
    fewer. Assert the DERIVED collection's length and distinctness, not
    merely GLUE_CHARS's own claimed size."""
    assert len(PRECHECK_GLUE_REPLY_SHAPES) == 16, (
        f"expected 16 precheck glue reply shapes derived from GLUE_CHARS, "
        f"found {len(PRECHECK_GLUE_REPLY_SHAPES)} -- a collapsed name would "
        f"silently drop entries here even though GLUE_CHARS itself still has "
        f"16"
    )
    distinct_glue_chars = {char for _, char in GLUE_CHARS}
    assert len(distinct_glue_chars) == 16, (
        f"two or more GLUE_CHARS entries share the same runtime codepoint: "
        f"only {len(distinct_glue_chars)} distinct character(s) across 16 "
        f"names. This is exactly the collapse a literal-glyph paste through a "
        f"tool call can cause -- two DIFFERENT separators silently becoming "
        f"the same character while every name still prints"
    )


@pytest.mark.skipif(NODE is None, reason="node not found on PATH")
@pytest.mark.parametrize(
    "shape", sorted(PRECHECK_GLUE_REPLY_SHAPES), ids=sorted(PRECHECK_GLUE_REPLY_SHAPES)
)
def test_precheck_decisions_never_resume_across_the_full_glue_chars_population(shape, tmp_path):
    """The claim this release's own notes make ("15 of the 16 characters ...
    over the shared GLUE_CHARS set") is about ALL 16 members of the real,
    shipped population, not the 6-character sample
    test_precheck_decision_expressions_agree_and_never_resume_on_a_mentioned_absent
    above happens to cover. This test locks the claim against the population
    it actually names: 0 of 16 resume-skips on the current (guarded) tree,
    and agreement between glossary and skeptic on all 16.

    RED evidence (measured once against 190ac36, the last commit before
    skeptic's precheck guard landed, via `git show`; not re-run here as an
    automated test -- this suite does not read git history at test time):
    15 of 16 GLUE_CHARS members resume-skip an ABSENT-glued reply through
    skeptic's pre-fix, unguarded `sentinelVerdict()` alone. The one
    non-offender is `lf` (U+000A): gluing with a newline puts "ABSENT 0" on
    its OWN trimmed line, which sentinelVerdict()'s fail-priority scan
    already rejects even without the containment guard -- the same reason
    this file's PARITY_REPLY_SHAPES never included an lf-glued case as "the
    defect" shape. 15 is the number the release notes state; this test does
    not re-derive it at run time, only locks that the CURRENT tree resumes
    zero times, whatever the historical count was."""
    reply = PRECHECK_GLUE_REPLY_SHAPES[shape].replace("<idx>", "0")
    resumed = {}
    for template in PRECHECK_GUARD_TEMPLATES:
        source = template.read_text(encoding="utf-8")
        resumed[template.name] = _run_precheck_decision(template, source, shape, reply, tmp_path)

    assert len(set(resumed.values())) == 1, (
        f"the precheck decision expressions in "
        f"{[t.name for t in PRECHECK_GUARD_TEMPLATES]} DISAGREE on GLUE_CHARS "
        f"shape {shape!r}: {resumed}\nReply: {reply!r}"
    )
    assert not any(resumed.values()), (
        f"a precheck reply with ABSENT glued to prose by GLUE_CHARS member "
        f"{shape!r} ({reply!r}) resume-skipped. The release notes' \"0 of 16\" "
        f"claim over GLUE_CHARS does not hold for this member"
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
