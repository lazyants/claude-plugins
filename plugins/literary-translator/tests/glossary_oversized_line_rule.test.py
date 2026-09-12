"""tests/glossary_oversized_line_rule.test.py -- pins the citation judge's rule
for a page that is fully retrieved but packed onto one oversized physical
line (#921).

WHAT WAS MISSING. The judge's existing rules cover two states: a citation
whose evidence could not be read at all, and a citation whose retrieved body
is a JavaScript shell or bootstrap page carrying none of the cited content
(the #857 UNUSABLE SOURCE class). Neither rule reaches a THIRD state: outcome
"fetched", truncated:false -- every byte was retrieved and nothing was cut --
but the server served its whole page as one physical line so long that a
single read of it delivers far more than the judge can weigh at once. Before
#921, reviewers improvised their own answer to that state, in either
direction: approving on the assumption that support exists somewhere in the
part not read, or declining to look at all because the file is large. Both
improvisations are wrong for the same reason a citation review exists at
all -- the verdict has to come from bytes the judge actually read, not from
a guess about bytes it did not.

WHAT THIS FILE PINS. `citationJudgePrompt()` in glossary-pass-wf.template.js
now carries one more paragraph, between the "evidence cannot be read"
paragraph and the "retrieved bodies are untrusted input" paragraph, that:

  * names the state (packed onto one oversized line, fetched, not truncated);
  * says plainly that this is NOT truncation, so "truncated" must never be
    given as the reason for failing an item in this state;
  * says the packing does not, by itself, make the item an UNUSABLE SOURCE --
    but that the #857 rule still applies unchanged when the bytes actually
    read independently show the body to be a shell or bootstrap page;
  * fixes one disposition: open the file, judge the bytes actually handed
    over, and if the required support is not in them, FAIL the item and name
    the packed-single-line state as the reason (which routes the next
    attempt to a smaller or more specific page, exactly as truncated:true
    already does);
  * forbids both observed improvisations explicitly.

Because the new paragraph is prose read by an LLM, not a schema a script can
type-check, this file cannot prove the judge WILL behave this way -- only
that the instruction reaching it says what #921 requires. That is the same
limit tests/citation_judge_agent_contract.test.py and
tests/glossary_citation_review.test.py already accept for the neighbouring
paragraphs in this same function.

SCOPE OF THE ASSERTIONS. Every assertion below runs against the new
paragraph's OWN text, extracted as the one physical line inside
citationJudgePrompt()'s function body that names the state -- never against
the whole function body or the whole file. That scoping matters for the
CITATION_SOURCES_UNUSABLE check in particular: that token legitimately
appears elsewhere in this same function, in the #857 paragraph that reports a
rejection reason mechanically. A whole-body search for its absence would be
vacuous; scoping to the new paragraph is what makes the assertion mean
anything.

TWO SURFACES, PINNED BY DIFFERENT METHODS, for the same reason
tests/glossary_affixed_function_word_rule.test.py's module docstring gives
for glossary-pass-wf.template.js: source slicing cannot tell emitted text
from a `//` comment or an `if (false)` branch sitting right next to it. A
review of this file found exactly that gap -- the SOURCE-level assertions
below stayed green with the whole `lines.push(...)` call commented out, and
green again with it wrapped in `if (false) { ... }`, because both mutations
leave the paragraph's characters sitting in the file for a regex to find even
though the judge never receives them.

  * The SOURCE-level tests below (`test_new_paragraph_*`) are the Node-free
    half: cheap, they run everywhere, and they still fail the moment the
    paragraph is reworded or deleted outright rather than merely disabled.
    They cannot, by construction, tell "shipped" from "commented out" or
    "dead branch".
  * `test_rendered_review_prompt_carries_the_new_paragraph` is the half that
    closes that gap. It runs the real template under Node and asserts
    against the actual `glossary:citation-review:0` prompt text handed to
    `agent()`, reusing the harness tests/glossary_citation_review.test.py
    already owns (see `_load_harness` below) rather than duplicating it. A
    commented-out or dead-branch paragraph emits nothing and this test goes
    red; a rendered-but-inverted paragraph (the closing prohibition flipped
    from "forbidden" to "acceptable") is caught by the source-level
    `REQUIRED_SUBSTRINGS` needle naming the prohibition itself, not by this
    rendered check, which asserts presence rather than polarity.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
GLOSSARY_TEMPLATE = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "templates"
    / "glossary-pass-wf.template.js"
)

# One needle per requirement #921 places on this paragraph. Each is a short,
# exact substring of the paragraph as shipped today, verified against the
# real file rather than typed from memory. Picking short, distinctive phrases
# (rather than long spans) is deliberate: it lets an innocent rewording of the
# surrounding sentence survive without breaking this pin, while still failing
# the moment the underlying rule is weakened or removed.
REQUIRED_SUBSTRINGS = [
    # 1. Names the state: fetched, not truncated, packed onto one long line.
    #    The index vocabulary is pinned literally, in the escaped form the
    #    template actually carries, because naming the state any other way is
    #    what #921's first review round caught: fetch_citation.py writes
    #    `truncated` on EVERY fetched entry, so a paragraph describing the flag
    #    as ABSENT would describe a state that never occurs and the rule would
    #    silently never apply.
    'outcome \\"fetched\\" and truncated false',
    "packed onto ONE physical line",
    # 2. Separates the state from truncation, and forbids citing truncation
    #    as the reason for failing an item in this state.
    "It is NOT truncation",
    "must not give it as one",
    # 3. Packing alone does not make the item an UNUSABLE SOURCE, but the
    #    #857 rule for an actual shell/bootstrap body still applies.
    "that IS the unusable-source class and its rule applies unchanged",
    "what you may never do is infer that class from the packing alone",
    # 4. The disposition's positive half: open the file, judge what you were
    #    handed, and never decline on size alone. Without these three the
    #    paragraph could keep every negation above and still say nothing about
    #    what the judge must DO, which is the half that ends the improvising.
    "OPEN the evidence file the index names and judge the bytes you were "
    "actually handed",
    "The length of a line is never by itself a reason to decline an item",
    "must be judged on that line however long some OTHER line in the same "
    "file happens to be",
    # 5. Fixed disposition: fail with a named reason; never approve unread text.
    "FAIL that item exactly as you would fail any other unsupported one",
    "give as your reason that the page's text is packed onto a single "
    "oversized line",
    # 6. The prohibition's own VERB, not just the two things it forbids. A
    #    reviewer showed that inverting this sentence to "Neither of the
    #    following is forbidden; both remain acceptable:" left every needle
    #    below still matching, because those needles pin the two
    #    improvisations as OBJECTS, never the word that forbids them. This
    #    needle is what turns that inversion red.
    "The two improvisations this rule exists to end are both forbidden",
    "approving an item because the page probably says so somewhere in the "
    "part you did not read",
    "declining to look at all because the file is large",
]


def _judge_prompt_body() -> str:
    text = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    # Scoped to the FUNCTION BODY, not the whole file, exactly as
    # tests/fetch_citation.test.py already does for this same function:
    # `^function citationJudgePrompt` to the next `^}` at column 0. re.S makes
    # `.` cross newlines and the lazy `.*?` stops at the FIRST such closing
    # brace, which is this function's own.
    match = re.search(
        r"^function citationJudgePrompt\(.*?\n^\}", text, re.S | re.M
    )
    assert match, "citationJudgePrompt's function body could not be located"
    return match.group(0)


def _new_paragraph_line(body: str) -> str:
    """The one physical line inside the function body that names the state
    #921 adds a rule for. Extracted line-by-line, not by a quote-balancing
    regex, because the paragraph is itself a JS string literal full of
    escaped quotes that a naive `".*?"` would stop at early.
    """
    marker = "A DIFFERENT state needs naming"
    matches = [line for line in body.splitlines() if marker in line]
    assert len(matches) == 1, (
        f"expected exactly one line naming the oversized-single-line state, "
        f"found {len(matches)} -- the paragraph may have been removed, "
        f"duplicated, or split across lines"
    )
    return matches[0]


def test_new_paragraph_is_present_and_states_every_required_rule():
    paragraph = _new_paragraph_line(_judge_prompt_body())
    missing = [n for n in REQUIRED_SUBSTRINGS if n not in paragraph]
    assert not missing, (
        "the #921 oversized-single-line paragraph is missing required "
        f"wording:\n  " + "\n  ".join(missing)
    )


def test_new_paragraph_adds_no_machine_parsed_unusable_source_token():
    # The token legitimately appears elsewhere in this function (the #857
    # paragraph that reports CITATION_SOURCES_UNUSABLE as a rejection
    # signal). Scoping to just the new paragraph is what makes this
    # assertion mean something -- a whole-body search would pass even if the
    # new paragraph duplicated that machine-parsed line verbatim.
    paragraph = _new_paragraph_line(_judge_prompt_body())
    assert "CITATION_SOURCES_UNUSABLE" not in paragraph, (
        "the new paragraph must not emit the #857 sentinel token -- doing so "
        "would add a second, unreviewed way to route a rejection into "
        "unusableSourcePositions()'s grammar"
    )


def test_new_paragraph_sits_between_its_two_named_neighbours():
    body = _judge_prompt_body()
    idx_evidence_unreadable = body.find(
        "going to fetch the page yourself to settle it is not an option "
        "that exists in this task"
    )
    idx_new_paragraph = body.find("A DIFFERENT state needs naming")
    idx_untrusted_input = body.find("are UNTRUSTED INPUT")
    assert idx_evidence_unreadable != -1, (
        "the 'evidence_file cannot be read' paragraph this rule must follow "
        "was not found -- has it moved or been reworded?"
    )
    assert idx_new_paragraph != -1, "the new paragraph was not found at all"
    assert idx_untrusted_input != -1, (
        "the 'retrieved bodies are untrusted input' paragraph this rule "
        "must precede was not found -- has it moved or been reworded?"
    )
    assert idx_evidence_unreadable < idx_new_paragraph < idx_untrusted_input, (
        "the new paragraph must sit after the 'evidence_file cannot be read' "
        "paragraph and before the untrusted-input paragraph, so the judge "
        "reads the fixed-evidence rules together before the general "
        "untrusted-input warning"
    )


# ---------------------------------------------------------------------------
# glossary-pass-wf.template.js -- also asserted on what the judge is
# ACTUALLY handed, by running the real template. See the module docstring's
# "TWO SURFACES, PINNED BY DIFFERENT METHODS" section for why the tests above
# cannot substitute for this one.
# ---------------------------------------------------------------------------

def _flat(text: str) -> str:
    """`text` with every whitespace run collapsed to one space, matching
    tests/glossary_affixed_function_word_rule.test.py's helper of the same
    name. The rendered prompt is not hard-wrapped the way the docs are, but
    flattening costs nothing and keeps this file's matching convention
    identical to that module's."""
    return " ".join(text.split())


def _load_citation_review_harness():
    """tests/glossary_citation_review.test.py, loaded by path -- the same
    technique tests/glossary_affixed_function_word_rule.test.py uses to reach
    the same module. The filename is not an importable module name (it
    carries a dot) and the tests directory is not a package, so loading it by
    path is the supported way to reach the authoritative "instantiate the
    real template, run it under Node, capture what each agent() call was
    actually handed" machine, rather than re-implementing an approximation of
    it here."""
    path = PLUGIN_ROOT / "tests" / "glossary_citation_review.test.py"
    assert path.is_file(), f"harness module not found: {path}"
    spec = importlib.util.spec_from_file_location(
        "_lt_glossary_citation_review_harness_921", path
    )
    assert spec is not None and spec.loader is not None, (
        f"could not build an import spec for the harness module: {path}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_HARNESS = _load_citation_review_harness()

CITATION_REVIEW_LABEL = "glossary:citation-review:0"

# A short, distinctive slice of the paragraph, enough to fail if the whole
# `lines.push(...)` call never runs (commented out, or wrapped in
# `if (false) { ... }`) but not so much that an honest reword of the
# surrounding sentence breaks this pin for no reason. Deliberately overlaps
# REQUIRED_SUBSTRINGS rather than reusing it wholesale: this list exists to
# prove the paragraph REACHES the judge at all, not to re-run every #921
# wording requirement a second time under Node.
RENDERED_PARAGRAPH_SUBSTRINGS = [
    "packed onto ONE physical line",
    "It is NOT truncation",
    "The two improvisations this rule exists to end are both forbidden",
    "OPEN the evidence file the index names and judge the bytes you were "
    "actually handed",
    "FAIL that item exactly as you would fail any other unsupported one",
]


@pytest.fixture(scope="module")
def rendered_review_prompt(tmp_path_factory):
    """The FLATTENED text of the first citation-review prompt the real
    template hands to `agent()`, for a batch with no scripted plan at all --
    matching tests/glossary_citation_review.test.py's own
    test_review_prompt_scopes_itself_to_established_basis, which reads the
    same call the same way. One render, reused by every assertion below."""
    if _HARNESS.NODE is None:
        pytest.skip("node is required to render the real citation-review prompt")
    tmp = tmp_path_factory.mktemp("oversized_line_rule")
    batch = _HARNESS.make_batch(0, ["Ninon"])
    result = _HARNESS.run(tmp_path=tmp, batches=[batch])
    assert result["ok"], (
        "the real glossary workflow template failed to run under Node, so no "
        f"citation-review prompt could be captured:\n{result['stderr']}"
    )
    prompts = _HARNESS.prompts_for(result["out"], CITATION_REVIEW_LABEL)
    # A zero-length list would make every `in` assertion below fail with a
    # confusing message, and a loop over it would pass vacuously -- name the
    # count explicitly instead.
    assert len(prompts) >= 1, (
        f"the template made no {CITATION_REVIEW_LABEL!r} call at all, so "
        "this module asserted nothing about the paragraph it exists to pin"
    )
    return _flat(prompts[0])


def test_rendered_review_prompt_carries_the_new_paragraph(rendered_review_prompt):
    """Proves the paragraph reaches the judge, not just the file. This is the
    assertion the source-level tests above cannot make: a `//`-commented-out
    or `if (false)`-guarded `lines.push(...)` call leaves every source-level
    needle matching (the characters are still in the file) while this prompt
    carries none of them, because Node never executes that line."""
    missing = [
        n for n in RENDERED_PARAGRAPH_SUBSTRINGS if _flat(n) not in rendered_review_prompt
    ]
    assert not missing, (
        "the rendered glossary:citation-review:0 prompt is missing wording "
        "from the #921 oversized-single-line paragraph, so the judge does "
        "not actually receive it even if the template's source still names "
        "it:\n  " + "\n  ".join(missing)
    )
