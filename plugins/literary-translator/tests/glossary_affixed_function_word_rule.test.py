"""tests/glossary_affixed_function_word_rule.test.py -- regression-lock for
issue #407: the glossary pass had NO rule about what a source-language
function word fused onto a name does to a candidate, so one canon could give
a fused form its own entry while folding another fused form of the SAME name
into the bare entry, with both readings fully compliant. The observed pair was
Hebrew (a proclitic preserved on one place name and dropped on another), but
the rule shipped here is language-pair-agnostic, like the file it lives in.

THE RULE, and the two bounds that are load-bearing rather than decoration:

    IF the bare form the candidate appears to carry is ALSO present --
       anywhere in THIS RUN's candidate manifest (the union of every batch),
       or already in canon.json's entries{} --
    THEN disposition:"review_queue" with a note naming that bare form
    ELSE resolve it on its own merits, like any other candidate

  * PRESENCE IS RUN-WIDE, NEVER BATCH-LOCAL. `glossary_batch_plan.py`
    frequency-sorts ordinary candidates into fixed-size batches, and each
    dispatch sees only its own rows plus the pre-run canon, with fragments
    merged afterwards. A fused form and its bare counterpart therefore land in
    DIFFERENT batches routinely -- driving the real `chunk_batches()` with a
    41-row fixture puts one in batch 0 and the other in batch 1 -- so a
    batch-local presence test reads "absent" on both sides, both agents
    resolve independently, and `_merge_batch` stores both because it only
    rejects a conflict on the SAME source_form. That is precisely the defect
    #407 reports, re-admitted by the bound meant to narrow it.
  * IT OUTRANKS THE NICKNAME RULE AND IS OUTRANKED BY THE ELISION RULE. The
    nickname/epithet bullet immediately below tells the agent to resolve such
    a candidate on its own merits and reserves review_queue for one that
    resists every basis; without a stated order a fused function word over a
    nickname gets two answers. The elision rule keeps its precedence because
    #91's IRON RULE already owns that flagged population.

WHY THE TWO SURFACES ARE PINNED BY DIFFERENT METHODS, which is the whole point
of this module and was learned by having the first design defeated:

  * `glossary_TASK.template.md` is pinned on its FLATTENED SOURCE. That is
    honest here because Step 0a copies this file VERBATIM into the project
    once: its source text IS the delivered artifact.
  * `glossary-pass-wf.template.js` is pinned on the RENDERED PROMPT, never on
    its source. `_extract_function_body`-style source slicing (the technique
    tests/glossary_epithet_rule.test.py uses) cannot tell emitted text from a
    `//` comment or an `if (false)` branch INSIDE the same function, so every
    source-based pin survives an inversion that delivers nothing to the agent.
    Commenting the rule out is a one-character edit. So this module executes
    the REAL, unmodified template under Node and asserts against the actual
    text handed to `agent()` for `glossary:dispatch:0`. A commented-out or
    dead-branch rule emits nothing and cannot satisfy these assertions.

WHY THE HARNESS IS IMPORTED RATHER THAN DUPLICATED, stated because every
sibling harness in this suite duplicates on purpose. Those siblings duplicate a
FIXTURE; what is needed here is the authoritative "instantiate the real
template, run it under Node, capture what each agent() call was actually
handed" machine, and re-implementing that would be an approximation standing in
for the thing under test -- the failure mode where a green result describes the
copy rather than the shipped artifact. tests/glossary_citation_review.test.py
owns that machine; this module loads it by path and drives it.

A DROP-DETECTOR, NOT A SEMANTIC-EQUIVALENCE PROVER. Every clause is pinned as
an EXACT literal against whitespace-flattened text, never as a bag of tokens,
because a token-proximity check passes on the rule's own INVERSION -- the
inverted sentence carries the same words. That lesson is
tests/glossary_trap_routing.test.py's, measured on a mutation that survived an
earlier token-bag design, and its bounded no-carve-out scan is reused here for
the same reason: appending "Except ... fold it into the bare entry" leaves every
pinned literal present and un-inverted while revoking the rule.

RED BEFORE GREEN, measured rather than assumed. Counts in both templates before
this fix (`grep -ci`): `affixed` 0, `function word` 0. So the anchor appears
nowhere in either file and no assertion below can be vacuously satisfied by
pre-existing prose.
"""
from __future__ import annotations

import importlib.util

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
TASK_SRC = TEMPLATES_DIR / "glossary_TASK.template.md"
WF_SRC = TEMPLATES_DIR / "glossary-pass-wf.template.js"

for _p in (TASK_SRC, WF_SRC):
    assert _p.is_file(), f"expected plugin template not found: {_p}"


def _flat(text: str) -> str:
    """`text` with every whitespace run collapsed to one space.

    These docs hard-wrap at ~79 columns and the workflow's prompt strings are
    single long lines, so matching on raw text fails in BOTH directions: a
    pure re-wrap of unchanged prose turns this file red for nothing, and -- the
    direction that matters -- a re-wrapped copy of a dropped clause could slip
    past an absence check. Flattening removes wrapping from the question
    entirely."""
    return " ".join(text.split())


TASK_FLAT = _flat(TASK_SRC.read_text(encoding="utf-8"))

ANCHOR = "affixed"

# The clauses that CARRY the rule, pinned as exact literals. Reworking any of
# these sentences is a deliberate act that must be re-pinned here on purpose.
# Phrased so each pin is meaningless unless the clause it names is intact:
# PRESENCE names the run-wide bound, IDENTITY names the refusal to decide,
# NEITHER_WAY names both directions at once, QUEUE names the routing plus the
# note's content, NICKNAME names the precedence, and ABSENT names the ELSE.
TASK_PINS = {
    "presence is run-wide":
        "anywhere in this run's own candidate manifest",
    "identity call, never automatic":
        "is an identity call, and this pass never makes one automatically",
    "neither direction is allowed":
        "Never resolve it by folding it into the bare name's entry, and never "
        "resolve it as an entry of its own",
    "the defect being prevented":
        "decides it one way for one name and the other way for another",
    "queue routing names the bare form":
        "with a `note` naming the bare form you believe it carries",
    "precedence over the nickname rule":
        "takes precedence over the nickname rule immediately below",
    # The canon half of the IF. It had no pin at all, and ped-ant proved the
    # gap by deleting this branch from both surfaces with all tests still
    # green. It is the half that carries the resumed-run case: a bare form in
    # `entries{}` is EXCLUDED from the run's candidates by
    # glossary_batch_plan.py, so the manifest can never show it.
    "canon.json is the other half of the presence test":
        "or already in `canon.json`'s `entries{}`",
    # Two-sided deliberately. A one-sided ELSE ("not present anywhere in this
    # run") CONTRADICTED the canon branch on exactly the population that
    # branch exists for, and no assertion could see it.
    "the bare-absent ELSE branch negates BOTH halves":
        "present in NEITHER place -- not anywhere in this run's candidate "
        "manifest AND not in `canon.json`'s `entries{}` -- do you resolve the "
        "candidate on its own merits",
    "manifest-absence alone does not license resolving":
        "Absence from the manifest alone is never enough",
}

# Same rule, as the DISPATCH PROMPT renders it. Most of these literals happen to
# coincide with the markdown copy's today; two do not, because the prompt is
# prose inside a JS string with no backticks and spells the nickname rule in
# caps. They are pinned INDEPENDENTLY rather than shared for the case that
# matters: either surface may be honestly reworded on its own, and a shared
# constant would let that reword silently loosen the other surface's pin.
RENDERED_PINS = {
    "presence is run-wide":
        "anywhere in this run's own candidate manifest",
    "identity call, never automatic":
        "is an identity call, and this pass never makes one automatically",
    "neither direction is allowed":
        "Never resolve it by folding it into the bare name's entry, and never "
        "resolve it as an entry of its own",
    "the defect being prevented":
        "decides it one way for one name and the other way for another",
    "queue routing names the bare form":
        'disposition:"review_queue" with a note naming the bare form you '
        "believe it carries",
    # Without this pin the rendered surface can lose the precedence clause
    # while every other assertion here stays green: PRECEDENCE_PIN below names
    # the ELISION order, not this one, and the ordering test compares OFFSETS,
    # which survives the clause's deletion. Measured on that exact mutation.
    "precedence over the nickname rule":
        "so it takes precedence over the NICKNAMES rule below",
    "canon.json is the other half of the presence test":
        "or already in canon.json's entries{} --",
    "the bare-absent ELSE branch negates BOTH halves":
        "present in NEITHER place -- not anywhere in this run's candidate "
        "manifest AND not in canon.json's entries{} -- do you resolve the "
        "candidate on its own merits",
    "manifest-absence alone does not license resolving":
        "Absence from the manifest alone is never enough",
}

# Pinned separately from RENDERED_PINS because it is the clause round 3 found
# missing, and because it must be UNIQUE inside the rendered prompt: it carries
# the word "affixed", which the pre-existing ELISION AMBIGUITY line does not, so
# the neighbouring rule cannot satisfy it. Asserted by COUNT, not presence.
PRECEDENCE_PIN = (
    "settled by the ELISION AMBIGUITY rule above, before this "
    "affixed-function-word rule applies"
)

# A carve-out appended to a prohibition is the inversion a token check cannot
# see: it ADDS words rather than removing them, so every literal above still
# matches. Same marker tuple and same bounded tail-scan as
# tests/glossary_trap_routing.test.py.
CARVE_OUT_MARKERS = ("except", "other than", "unless", "apart from")
CARVE_OUT_SCAN_CHARS = 200

NEITHER_WAY_KEY = "neither direction is allowed"


def _assert_no_carve_out(flat: str, prohibition: str, *, surface: str) -> None:
    idx = flat.find(prohibition)
    assert idx != -1, (
        f"{surface}: the #407 prohibition is absent, so its carve-out scan "
        f"cannot run -- {prohibition!r}"
    )
    tail = flat[idx + len(prohibition):idx + len(prohibition) + CARVE_OUT_SCAN_CHARS]
    hit = [m for m in CARVE_OUT_MARKERS if m in tail.lower()]
    assert not hit, (
        f"{surface}: a carve-out marker {hit!r} follows the #407 prohibition "
        f"within {CARVE_OUT_SCAN_CHARS} characters. An exception appended here "
        f"revokes the rule while leaving every pinned literal intact, which is "
        f"exactly what this scan exists to catch. Tail:\n\n{tail}"
    )


# ---------------------------------------------------------------------------
# glossary_TASK.template.md -- the AUTHORITATIVE surface, seeded verbatim.
# ---------------------------------------------------------------------------

def test_task_template_carries_the_anchor():
    assert ANCHOR in TASK_FLAT.lower(), (
        f"anchor {ANCHOR!r} is absent from {TASK_SRC.name} -- the #407 "
        "affixed-function-word rule appears to be entirely gone"
    )


@pytest.mark.parametrize("clause,literal", sorted(TASK_PINS.items()))
def test_task_template_carries_each_rule_clause(clause, literal):
    assert _flat(literal) in TASK_FLAT, (
        f"{TASK_SRC.name}: the #407 rule's {clause} clause is missing its "
        f"pinned wording -- {literal!r}. A token-proximity check would pass on "
        f"this rule's own inversion, so the clause is pinned exactly; an "
        f"honest reword has to be re-pinned here deliberately."
    )


def test_task_template_prohibition_has_no_carve_out():
    _assert_no_carve_out(TASK_FLAT, _flat(TASK_PINS[NEITHER_WAY_KEY]),
                         surface=TASK_SRC.name)


# ---------------------------------------------------------------------------
# glossary-pass-wf.template.js -- asserted on what the agent is ACTUALLY
# handed, by running the real template. See the module docstring.
# ---------------------------------------------------------------------------

def _load_harness():
    """tests/glossary_citation_review.test.py, loaded by path.

    The filename is not an importable module name (it carries a dot), and the
    tests directory is not a package, so this is the supported way to reach it.
    Loading it also pulls in its `NODE` probe, which is what the skip below
    keys on."""
    path = PLUGIN_ROOT / "tests" / "glossary_citation_review.test.py"
    assert path.is_file(), f"harness module not found: {path}"
    spec = importlib.util.spec_from_file_location("_lt_glossary_harness", path)
    assert spec is not None and spec.loader is not None, (
        f"could not build an import spec for the harness module: {path}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_HARNESS = _load_harness()

# The Node skip lives in the fixture below, NOT in a module-level `pytestmark`.
# A module-level mark also disables the task-template pins and the helper
# self-tests, none of which touch Node -- so on a machine without Node the
# AUTHORITATIVE surface's coverage would vanish and read exactly like a clean
# run. Measured with `node` made unresolvable on PATH: 12 passed, 12 skipped --
# under a module-level mark the same run reported every test skipped.

DISPATCH_LABEL = "glossary:dispatch:0"


@pytest.fixture(scope="module")
def rendered_dispatch_prompt(tmp_path_factory):
    """The FLATTENED text of the first dispatch prompt the real template hands
    to `agent()`. One render, reused by every assertion below."""
    if _HARNESS.NODE is None:
        pytest.skip("node is required to render the real dispatch prompt")
    tmp = tmp_path_factory.mktemp("affixed_rule")
    batch = _HARNESS.make_batch(0, ["Alpha", "Beta"])
    result = _HARNESS.run(tmp_path=tmp, batches=[batch])
    assert result["ok"], (
        "the real glossary workflow template failed to run under Node, so no "
        f"prompt could be captured:\n{result['stderr']}"
    )
    prompts = _HARNESS.prompts_for(result["out"], DISPATCH_LABEL)
    # A zero-length list would make every `in` assertion below fail with a
    # confusing message, and a loop over it would pass vacuously -- name the
    # count explicitly instead.
    assert len(prompts) >= 1, (
        f"the template dispatched no {DISPATCH_LABEL!r} call at all, so this "
        "module asserted nothing about the rule it exists to pin"
    )
    return _flat(prompts[0])


def test_rendered_dispatch_prompt_carries_the_anchor(rendered_dispatch_prompt):
    assert ANCHOR in rendered_dispatch_prompt.lower(), (
        f"anchor {ANCHOR!r} is absent from the RENDERED {DISPATCH_LABEL} "
        "prompt. Note this is not a source check: the rule may still be "
        "present in the template's source as a comment or a dead branch and "
        "still never reach the agent, which is the case this assertion exists "
        "for."
    )


@pytest.mark.parametrize("clause,literal", sorted(RENDERED_PINS.items()))
def test_rendered_dispatch_prompt_carries_each_rule_clause(
    clause, literal, rendered_dispatch_prompt
):
    assert _flat(literal) in rendered_dispatch_prompt, (
        f"the rendered {DISPATCH_LABEL} prompt is missing the #407 rule's "
        f"{clause} clause -- {literal!r}. The agent does not receive it."
    )


def test_rendered_dispatch_prompt_carries_the_elision_precedence_exactly_once(
    rendered_dispatch_prompt
):
    """The precedence clause is the one round 3 found a test could lose
    silently: `elision_ambiguous` already occurs in the neighbouring ELISION
    line, so asserting THAT token proves nothing. This literal carries
    "affixed", which that line does not, and is asserted by count so a second
    copy (a paste that leaves the original behind) is a failure too."""
    n = rendered_dispatch_prompt.count(_flat(PRECEDENCE_PIN))
    assert n == 1, (
        f"expected the #407 elision-precedence clause exactly once in the "
        f"rendered {DISPATCH_LABEL} prompt, found {n} -- {PRECEDENCE_PIN!r}"
    )


def test_rendered_dispatch_prompt_names_the_run_wide_manifest(
    rendered_dispatch_prompt
):
    """The run-wide bound is only real if the agent is told WHERE to look.
    `manifest_all.json` is written atomically by resume_setup.py before any
    dispatch, so naming it costs no new artifact -- but a rule that says
    "this run's manifest" without a path is not actionable."""
    assert "manifest_all.json" in rendered_dispatch_prompt, (
        f"the rendered {DISPATCH_LABEL} prompt states a run-wide presence "
        "test but never names manifest_all.json, so the agent has no way to "
        "apply it and would fall back to what it can see -- its own batch, "
        "which is the batch-local test this bound exists to replace"
    )


def test_rendered_dispatch_prompt_prohibition_has_no_carve_out(
    rendered_dispatch_prompt
):
    _assert_no_carve_out(rendered_dispatch_prompt,
                         _flat(RENDERED_PINS[NEITHER_WAY_KEY]),
                         surface=f"rendered {DISPATCH_LABEL} prompt")


def test_rule_precedes_the_nickname_rule_in_the_rendered_prompt(
    rendered_dispatch_prompt
):
    """Order in the prompt is not cosmetic: the rule claims precedence over the
    nickname routing, and a reader resolves a conflict by reading forward. If
    the nickname bullet were emitted first, the precedence sentence would point
    backwards at a rule the agent has already applied."""
    affixed_at = rendered_dispatch_prompt.lower().find(ANCHOR)
    nickname_at = rendered_dispatch_prompt.find("NICKNAMES, EPITHETS, AND ALIASES")
    assert affixed_at != -1 and nickname_at != -1, (
        "both the #407 rule and the nickname rule must be present in the "
        f"rendered prompt to compare their order (affixed={affixed_at}, "
        f"nickname={nickname_at})"
    )
    assert affixed_at < nickname_at, (
        "the #407 affixed-function-word rule must be emitted BEFORE the "
        "nickname/epithet rule it takes precedence over"
    )


# ---------------------------------------------------------------------------
# The helpers themselves discriminate, proven on synthetic fixtures before the
# real templates are trusted to them.
# ---------------------------------------------------------------------------

def test_flatten_makes_a_wrapped_clause_matchable_and_is_not_vacuous():
    wrapped = "never resolve it\n  as an entry\n  of its own"
    assert "never resolve it as an entry of its own" in _flat(wrapped), (
        "_flat must make a hard-wrapped clause matchable, or every pin above "
        "goes red on a pure re-wrap"
    )
    assert "never resolve it as an entry of its own" not in _flat(
        "never resolve it as an entry of somebody else's"
    ), (
        "_flat must NOT match a differing clause -- if it did, the pins above "
        "would be satisfied by prose that says something else"
    )


def test_carve_out_scan_catches_an_appended_exception_and_spares_the_real_prose():
    prohibition = "never resolve it as an entry of its own"
    with pytest.raises(AssertionError):
        _assert_no_carve_out(
            _flat(prohibition + ", except when the bare form is obvious"),
            prohibition, surface="synthetic",
        )
    # The shipped prose continues with a precedence sentence and an ELSE
    # branch; neither may trip the scan, or this file is a false RED generator.
    _assert_no_carve_out(
        _flat(prohibition + ". This holds even when that name is a nickname or "
              "epithet with an obvious sense-rendering. Only when the bare "
              "form is present in NEITHER place -- not anywhere in this run's "
              "candidate manifest AND not in canon.json's entries{} -- do you "
              "resolve the candidate on its own merits, like any other."),
        prohibition, surface="synthetic",
    )


def test_anchor_is_absent_from_unrelated_prose():
    """Guards the ANCHOR CONSTANT, which is what the two anchor assertions and
    the ordering test all key on.

    A hand-written sentence cannot re-verify the pre-fix grep count in the
    module docstring. What it CAN do is fail the moment someone weakens ANCHOR
    to a common word: at "name" or "own" the neighbouring canon prose below
    satisfies it, and the anchor stops discriminating this rule from its
    neighbours."""
    assert ANCHOR not in _flat(
        "only true orthographic spelling variants of the same surface name "
        "may ever share one canonical_target_form"
    ).lower(), (
        f"ANCHOR {ANCHOR!r} is satisfied by unrelated canon prose, so it no "
        "longer identifies the #407 rule; pick a token this rule alone carries"
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
