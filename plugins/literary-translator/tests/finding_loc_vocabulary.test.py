"""tests/finding_loc_vocabulary.test.py

Targets #539: the reviewer's finding-`loc` VOCABULARY -- the set of colon-bearing
forms a reviewer is told it may use -- as opposed to `finding_loc_authenticity
.test.py`, which targets the GATE that accepts or rejects a given loc. The two
are different questions and this plugin shipped them out of step: the gate
(`AUTHENTIC_LOC_RE`, a pure colon-shape test) accepted any prefix, while every
site that TELLS a reviewer which forms exist named only a block id, `FN:n` and
`VERSE:vid`. A draft's own top-level `notes[]` had no conforming spelling at
all, so a reviewer with a true finding about a note invented one
(`notes[14]`, `NOTES`), the colonless invention failed the gate, and
`findingsAuthentic()`'s `.every()` discarded the ENTIRE review -- valid block
findings included, and a re-review reproduced it rather than recovering them,
because the cause was a missing spelling rather than a slip. The measured
population that justifies this file is recorded once, in the 1.39.0 CHANGELOG
entry, and deliberately not restated here: a figure copied into several prose
sites drifts between them, which is what happened to four copies of it during
this fix's own review.

The fix is prose, not a branch: `AUTHENTIC_LOC_RE` already admits `NOTE:14`
unchanged. So what needs regression cover is precisely the prose -- that every
site which states the contract states the SAME contract, and that the two
sentences a reviewer's behaviour depends on are actually present.

FOUR CONTRACT SITES, and one of them binds while the others document:

  1. `mass-translate-wf.template.js`'s `reviewDispatchPrompt()` -- the RUNTIME
     prompt the reviewer agent actually receives. Its own text declares it
     self-contained and says review_TASK.md's "own field list must never
     override the fields spelled out here", so this is the only site that
     changes reviewer behaviour. Read here as the ACTUAL RENDERED prompt, by
     reusing `draft_path_convention.test.py`'s `run_prompt_probe` harness --
     never by re-implementing the prompt builder, which would assert against
     this file's idea of the prompt rather than the shipped one.
  2. the inline `REVIEW_SCHEMA` literal's `loc` description in the same file --
     what the tool-use API shows the agent alongside the prompt.
  3. `review_TASK.template.md`'s `findings[]` shape line.
  4. `review.schema.json`'s `loc` description.

The prefix set is DERIVED from each site rather than hard-coded per site: a
hand-typed membership list inside a drift test freezes exactly what it is
supposed to detect. `_loc_forms()` matches only the PARAMETRIC forms
(`FN:n`, `VERSE:{vid}`, `NOTE:n` ...), so a concrete block-id example like
`PARA:seg01:0001` is deliberately not collected -- block type is not a fixed
enum (see manifest.schema.json) and the sites legitimately differ in which
example they give.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
TEMPLATE_PATH = ASSETS / "templates" / "mass-translate-wf.template.js"
REVIEW_TASK_PATH = ASSETS / "templates" / "review_TASK.template.md"
REVIEW_SCHEMA_PATH = ASSETS / "schemas" / "review.schema.json"
DRAFT_SCHEMA_PATH = ASSETS / "schemas" / "draft.schema.json"

for _p in (TEMPLATE_PATH, REVIEW_TASK_PATH, REVIEW_SCHEMA_PATH, DRAFT_SCHEMA_PATH):
    assert _p.is_file(), f"expected shipped file not found: {_p}"

# ---------------------------------------------------------------------------
# Reuse draft_path_convention.test.py's real Node prompt harness
# (run_prompt_probe/NODE_PATH) via importlib rather than vendoring a second
# copy -- house style, same as finding_loc_authenticity.test.py's reuse of
# batch_size_estimator.test.py's harness.
# ---------------------------------------------------------------------------
_DPC_PATH = Path(__file__).resolve().parent / "draft_path_convention.test.py"
assert _DPC_PATH.is_file(), f"expected sibling test file not found: {_DPC_PATH}"
_dpc_spec = importlib.util.spec_from_file_location(
    "draft_path_convention_shared_for_finding_loc_vocabulary", _DPC_PATH
)
assert _dpc_spec is not None and _dpc_spec.loader is not None, f"could not load spec for {_DPC_PATH}"
_dpc = importlib.util.module_from_spec(_dpc_spec)
_dpc_spec.loader.exec_module(_dpc)

NODE_PATH = _dpc.NODE_PATH
run_prompt_probe = _dpc.run_prompt_probe

# Same idiom for the JS object-literal parser this suite already owns. Using it
# keeps the inline-literal extractor SCOPED to REVIEW_SCHEMA; a regex scrape
# over the whole template would match the first same-prefixed description
# anywhere in the file, and would also break on an escaped quote.
_DRIFT_PATH = Path(__file__).resolve().parent / "review_prompt_schema_drift.test.py"
assert _DRIFT_PATH.is_file(), f"expected sibling test file not found: {_DRIFT_PATH}"
_drift_spec = importlib.util.spec_from_file_location(
    "review_prompt_schema_drift_shared_for_finding_loc_vocabulary", _DRIFT_PATH
)
assert _drift_spec is not None and _drift_spec.loader is not None, f"could not load spec for {_DRIFT_PATH}"
_drift = importlib.util.module_from_spec(_drift_spec)
_drift_spec.loader.exec_module(_drift)

extract_const_object_literal = _drift.extract_const_object_literal
parse_js_object_literal = _drift.parse_js_object_literal

requires_node = pytest.mark.skipif(
    NODE_PATH is None,
    reason="node not found on PATH; rendering the REAL reviewDispatchPrompt() needs Node.js "
    "(reused from draft_path_convention.test.py -- this plugin has no hard Node.js dependency)",
)

# A parametric loc form: an uppercase prefix followed by a colon and a bare or
# braced parameter name (n / vid). Concrete examples (PARA:seg01:0001) do not
# match, by design -- see this module's docstring.
_PARAMETRIC_LOC_RE = re.compile(r"\b([A-Z][A-Z0-9_]*):(?:\{(n|N|vid)\}|(n|N|vid)\b(?!\}))")


def _loc_forms(text):
    """The set of (prefix, parameter) loc forms a piece of shipped prose spells.

    The PARAMETER is carried, not just the prefix. Comparing prefixes alone
    would let `FN:n VERSE:vid NOTE:n` and the nonsense `FN:vid VERSE:n NOTE:N`
    compare equal, so the parity check below would pass over four sites that
    disagree about what each form takes. Brace style and the `n`/`N` spelling
    are normalized away because the sites legitimately differ there
    (review.schema.json writes "FN:{n}", review_TASK.template.md writes
    "FN:n") -- those are the same form, and only the (prefix, parameter)
    pairing is the contract. Braces must BALANCE, though -- BOTH ways: a
    half-braced "FN:{n" or "FN:n}" is a typo in shipped prose, not a spelling
    variant, and an extractor that quietly accepted either would report
    agreement over it. (The closing-brace half of that was measured slipping
    through an earlier version of this pattern, which is why the negative
    lookahead is there rather than left to the word boundary.)
    """
    return {
        (prefix, (braced or bare).lower())
        for prefix, braced, bare in _PARAMETRIC_LOC_RE.findall(text)
    }


# The shipped vocabulary, in the (prefix, parameter) shape _loc_forms returns.
# Asserted per-site rather than only across sites, so a single site drifting to
# FN:vid is caught without Node -- the parity test below needs the rendered
# prompt and therefore skips when Node is absent.
_VOCABULARY = frozenset({("FN", "n"), ("VERSE", "vid"), ("NOTE", "n")})


def _inline_review_schema_loc_description():
    """The `loc` description of the inline REVIEW_SCHEMA literal in the template.

    Parsed with review_prompt_schema_drift.test.py's own literal parser -- the
    file that already owns structural parity between this literal and
    review.schema.json -- so the lookup is scoped to REVIEW_SCHEMA and walks
    the same key path as `_review_schema_loc_description()` below.
    """
    src = TEMPLATE_PATH.read_text(encoding="utf-8")
    literal = parse_js_object_literal(extract_const_object_literal(src, "REVIEW_SCHEMA"))
    desc = literal["properties"]["findings"]["items"]["properties"]["loc"]["description"]
    assert isinstance(desc, str) and desc, (
        "the inline REVIEW_SCHEMA literal's loc description is missing or empty"
    )
    return desc


def _review_task_loc_section():
    """review_TASK.template.md's findings[] shape line plus the prose under it.

    Bounded by ANCHORS at both ends -- from the fenced JSON example to the
    `clean: true` paragraph that follows the loc contract. A fixed character
    length was measured running to EOF instead, which put every later section
    of the file inside the window: a stray `FN:{n}` added anywhere below would
    then surface here as a confusing "the four sites disagree". An anchor that
    stops matching fails loudly and says what actually changed.
    """
    text = REVIEW_TASK_PATH.read_text(encoding="utf-8")
    start = text.find('{"loc":')
    assert start != -1, f"could not find the findings[] shape line in {REVIEW_TASK_PATH}"
    end = text.find("`clean: true` only if", start)
    assert end != -1, (
        f"could not find the `clean: true` paragraph after the findings[] shape line in "
        f"{REVIEW_TASK_PATH} -- the document was restructured; re-anchor this slice"
    )
    return text[start:end]


def _review_schema_loc_description():
    obj = json.loads(REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"))
    desc = obj["properties"]["findings"]["items"]["properties"]["loc"]["description"]
    assert isinstance(desc, str) and desc, "review.schema.json's loc description is missing/empty"
    return desc


@pytest.fixture(scope="module")
def rendered_prompts(tmp_path_factory):
    """Every ACTUAL rendered prompt builder's text, from real node."""
    tmp_path = tmp_path_factory.mktemp("loc_vocab_probe")
    # Not created on disk on purpose: run_prompt_probe only substitutes this
    # string into {{DURABLE_ROOT}}; it never touches the path.
    durable_root = tmp_path / "durable"
    out, _ = run_prompt_probe(
        tmp_path,
        str(durable_root),
        "seg01",
        1,
        {
            "clean": False,
            "coverage_ok": True,
            "findings": [
                {"loc": "NOTE:14", "severity": "medium", "issue": "stale note", "suggest": "correct it"}
            ],
        },
    )
    return out


@pytest.fixture(scope="module")
def rendered_review_prompt(rendered_prompts):
    """The ACTUAL rendered reviewDispatchPrompt() text."""
    return rendered_prompts["reviewDispatchPrompt"]


# ---------------------------------------------------------------------------
# 1. Every contract site spells the same forms, NOTE:n included.
#
# The RENDERED prompt is covered by the parity test below rather than by a
# fourth test here: that test asserts the exact vocabulary at every site, so a
# separate "the prompt enumerates NOTE" assertion would be strictly subsumed by
# it and skip under exactly the same @requires_node condition. These three are
# NOT subsumed -- they are the only enumeration coverage when Node is absent,
# which is why they are per-site and Node-free. Do not fold them into the
# parity test.
# ---------------------------------------------------------------------------
def test_inline_review_schema_description_spells_the_vocabulary():
    got = _loc_forms(_inline_review_schema_loc_description())
    assert _VOCABULARY <= got, (
        f"the inline REVIEW_SCHEMA loc description must spell {sorted(_VOCABULARY)} -- "
        f"found {sorted(got)}"
    )


def test_review_task_template_spells_the_vocabulary():
    got = _loc_forms(_review_task_loc_section())
    assert _VOCABULARY <= got, (
        f"review_TASK.template.md's findings[] contract must spell {sorted(_VOCABULARY)} -- "
        f"found {sorted(got)}"
    )


def test_review_schema_description_spells_the_vocabulary():
    got = _loc_forms(_review_schema_loc_description())
    assert _VOCABULARY <= got, (
        f"review.schema.json's loc description must spell {sorted(_VOCABULARY)} -- "
        f"found {sorted(got)}"
    )


# ---------------------------------------------------------------------------
# 2. The two sentences reviewer behaviour depends on are actually present.
#    Without these, either could be dropped while every enumeration assertion
#    above stays green -- and each omission has its own live consequence.
# ---------------------------------------------------------------------------
@requires_node
def test_rendered_prompt_states_the_colon_invariant(rendered_review_prompt):
    """Dropped, colonless holistic locs go on forfeiting entire reviews --
    the defect itself, merely renamed."""
    text = rendered_review_prompt.lower()
    assert "colon" in text, (
        "the rendered review prompt must state the colon-delimited invariant explicitly "
        "(a loc is never a bare or holistic token) -- the word 'colon' does not appear in it"
    )


@requires_node
def test_rendered_prompt_states_the_note_index_basis(rendered_review_prompt):
    """NOTE:n and FN:n look alike and are not the same basis: NOTE:n is a
    0-based ARRAY INDEX, FN:n is the footnote's own NUMBER. A reviewer that
    reads NOTE:n as one-based points the fix turn at the wrong note, and
    nothing downstream resolves the index to catch it."""
    text = rendered_review_prompt.lower()
    assert "0-based" in text, (
        "the rendered review prompt must state that NOTE:n is a 0-based index into notes[] -- "
        "'0-based' does not appear in it"
    )
    assert "number" in text, (
        "the rendered review prompt must contrast NOTE:n's index basis with FN:n being the "
        "footnote's own number"
    )


def test_review_task_template_states_both_sentences():
    section = _review_task_loc_section().lower()
    assert "colon" in section, (
        "review_TASK.template.md must state the colon-delimited invariant beside its findings[] shape"
    )
    assert "0-based" in section, (
        "review_TASK.template.md must state NOTE:n's 0-based index basis beside its findings[] shape"
    )


# ---------------------------------------------------------------------------
# 3. The four sites do not drift apart.
# ---------------------------------------------------------------------------
@requires_node
def test_all_four_contract_sites_enumerate_the_same_forms(rendered_review_prompt):
    """A later edit that adds a form to one site and forgets the others is the
    way this contract broke in the first place: review_TASK.template.md was
    the only site that enumerated the vocabulary at all, while the RUNTIME
    prompt -- the one that binds -- named just VERSE:{vid}."""
    sites = {
        "rendered reviewDispatchPrompt()": _loc_forms(rendered_review_prompt),
        "inline REVIEW_SCHEMA loc description": _loc_forms(
            _inline_review_schema_loc_description()
        ),
        "review_TASK.template.md": _loc_forms(_review_task_loc_section()),
        "review.schema.json": _loc_forms(_review_schema_loc_description()),
    }
    distinct = {frozenset(v) for v in sites.values()}
    assert len(distinct) == 1, (
        "the four sites that state the finding-loc contract must spell the SAME (prefix, "
        "parameter) forms; they disagree:\n"
        + "\n".join(f"  {name}: {sorted(forms)}" for name, forms in sites.items())
    )
    # And the agreed set is the shipped vocabulary, not merely self-consistent:
    # four sites that all said FN:vid would agree with each other perfectly.
    assert distinct.pop() == _VOCABULARY, (
        f"the agreed vocabulary must be exactly {sorted(_VOCABULARY)}"
    )


# ---------------------------------------------------------------------------
# 4. Every addressable draft member has a spelling -- and names[] deliberately
#    does not.
# ---------------------------------------------------------------------------
# Frozen on purpose: a NEW top-level member arriving in draft.schema.json must
# red this test, because #539 is exactly what happens when a member ships with
# no way for a reviewer to address it. The right response to a red here is to
# decide the new member's loc spelling (or to record that it needs none), never
# to extend this tuple reflexively.
_KNOWN_DRAFT_MEMBERS = frozenset(
    {"seg", "blocks", "footnotes", "verses", "names", "notes", "dispatch_token"}
)


def test_draft_schema_top_level_members_are_the_known_set():
    obj = json.loads(DRAFT_SCHEMA_PATH.read_text(encoding="utf-8"))
    got = set(obj["properties"])
    assert got == _KNOWN_DRAFT_MEMBERS, (
        f"draft.schema.json's top-level members changed: {sorted(got)} vs "
        f"{sorted(_KNOWN_DRAFT_MEMBERS)}. A new member needs a decision about how a reviewer "
        "addresses a finding about it -- see #539 for what happens when it gets none."
    )


@requires_node
def test_notes_is_addressable_and_names_is_deliberately_not(rendered_review_prompt):
    """`names[]` has the SAME structural gap `notes[]` had, and is deliberately
    left unaddressable: across both live books' measured blockings, not one is
    CONFIRMED caused by a names[] finding, while several are confirmed caused
    by a notes[] one (see the 1.39.0 CHANGELOG entry for the counts -- they are
    recorded once, there). Complexity scales with frequency x impact, so the
    spelling is not shipped until a names[] blocking is actually confirmed.
    This test is where that decision is RECORDED -- flip it, do not silently
    add NAME:n.
    """
    prefixes = {prefix for prefix, _param in _loc_forms(rendered_review_prompt)}
    assert "NOTE" in prefixes, "notes[] must be addressable after #539"
    assert "NAME" not in prefixes, (
        "NAME:n is deliberately NOT part of the shipped vocabulary (zero measured incidence). "
        "If a names[] blocking has since been measured, add NAME:n at all four contract sites "
        "and update this test and the #539 non-goal together."
    )


# ---------------------------------------------------------------------------
# 5. The FIXER can substantiate every form the REVIEWER may emit.
#
# These are two prompts and two contracts, and #532 (1.37.0) made the gap
# expensive: the fix turn now applies a finding it can substantiate and REFUSES
# one it cannot. So a loc form added to the reviewer's vocabulary without a
# matching substantiation recipe does not fail loudly -- the reviewer emits it,
# the review passes the authenticity gate, and the fixer then refuses every such
# finding on the grounds that it has no evidence rule for it. The defect #539
# closes would change shape rather than close. Caught on this PR by the MR bot
# after #532 merged underneath it, which is exactly the drift this pins.
# ---------------------------------------------------------------------------
def _substantiation_clause(fix_text):
    """fixPrompt's evidence-rules sentence ONLY.

    Scoped deliberately: `NOTE:n` also appears in the refusal-marker rule
    further down, so extracting forms from the WHOLE fix prompt made the
    assertion below pass with the evidence rule deleted -- measured, by deleting
    it. The window is the one sentence that answers "what do I check for a loc
    of this kind", which is the thing that must exist per form.
    """
    start = fix_text.index("Substantiate a finding against the source BEFORE")
    end = fix_text.index("To refuse a finding:", start)
    return fix_text[start:end]


@requires_node
def test_fix_prompt_can_substantiate_every_reviewer_loc_form(rendered_prompts):
    fix_text = rendered_prompts["fixPrompt"]
    emitted = _loc_forms(rendered_prompts["reviewDispatchPrompt"])
    substantiable = _loc_forms(_substantiation_clause(fix_text))
    missing = sorted(emitted - substantiable)
    assert not missing, (
        "the fix turn refuses a finding it cannot substantiate, so every loc form the reviewer "
        f"is told it may emit needs an evidence rule in fixPrompt -- missing: {missing}. "
        f"reviewer emits {sorted(emitted)}; fixPrompt names {sorted(substantiable)}."
    )


@requires_node
def test_fix_prompt_says_a_substantiated_note_finding_edits_the_note(rendered_prompts):
    """The adjacent trap: fixPrompt forbids writing a refusal MARKER into
    notes[]. Without a word about the applied case, that prohibition reads as
    'never touch notes[]' -- which would refuse the one repair a NOTE:n finding
    actually calls for."""
    fix_text = rendered_prompts["fixPrompt"]
    assert "correct or remove THAT note" in fix_text, (
        "fixPrompt must say what APPLYING a NOTE:n finding means -- correcting or removing that "
        "note -- or its refusal-marker prohibition reads as a blanket ban on editing notes[]"
    )
    assert "not a marker" in fix_text, (
        "fixPrompt's refusal-marker rule must distinguish itself from an ordinary applied fix "
        "to a note, or the two read as the same prohibition"
    )
