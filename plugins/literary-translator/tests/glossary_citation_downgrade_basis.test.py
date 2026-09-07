#!/usr/bin/env python3
"""#901 -- what the two CITATION-DOWNGRADE prompts say `basis:"transliterated"` means.

These are the two rungs a `live` project lands on when a citation dies: the
regeneration branch of `batchDispatchPrompt()` (a batch the citation reviewer
rejected) and `batchRepairPrompt()` (individual rows whose source URL did not
retrieve, or retrieved a body that attests nothing). Both tell the model to
downgrade the affected item off `basis:"established"`, and both used to gate
that downgrade on the project's practical-transcription rule being "enough on
its own".

WHY THAT WORDING WAS A DEFECT. "Enough on its own" reads as *mechanical
letter-by-letter transcription suffices*. So a name whose form the project's
own `style_bible.md` section C-translit rule already settles -- with a
widely-used target-language form, where that rule prefers one -- is not
recognised as covered, and the model takes one of the other branches instead:
it queues the row for research nobody needs to do, or it respells a settled
name letter by letter. The population is not rare. On one live 22-batch
volume, 29 of 143 established citations did not retrieve; in the run that
produced #891, 11 of 64 cited URLs were already dead. Every one of those rows
reaches one of these two prompts.

WHAT IS ASSERTED, AND OVER WHAT. The prompts are JavaScript builders, so these
assertions run over the RENDERED string, never over the template's source
bytes -- a source-byte grep would pass on a clause the builder never emits.
Node is therefore required, not optional: a run that executed no builder is a
false pass, which is why the last test asserts the KEY SET the template
returned -- a builder that vanished or was renamed fails there rather than
quietly reducing what is covered.

One thing these assertions are NOT redundant with, despite pinning the same
bytes: `_DEFAULT_REPAIR_PROMPT_BASELINE` in
`tests/glossary_dispatch_driver.test.py` pins the repair prompt whole, but that
baseline is REGENERATED whenever the prompt legitimately changes -- so a
regression to this clause would ship with a matching regenerated baseline and
pass. Pinning the intent separately is what makes the regression visible.

WHY THE NEGATIVE ASSERTION IS SCOPED, NOT WHOLE-PROMPT. The regeneration
rendering CONTAINS the entire dispatch prompt, including its own
`research_mode` paragraph. That paragraph is a different site with its own
issue (#891), fixed separately in 1.109.0. A whole-prompt "is enough on its
own" not-in assertion would therefore be asserting something about #891's line
as well as this one: it would pass today, and it would go red for a reason
outside this file's subject if that sibling line ever moved. The negative stays
scoped to the regeneration paragraph these two sites own, and the
non-regeneration branch is rendered too, to prove that paragraph exists only
when a rejection reason is passed.
"""

import importlib.util
import shutil

import pytest

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
DRIVER = SKILL_ROOT / "assets" / "scripts" / "glossary_dispatch_driver.py"
JSON_STDOUT = SKILL_ROOT / "assets" / "scripts" / "json_stdout.py"
TEMPLATE = SKILL_ROOT / "assets" / "templates" / "glossary-pass-wf.template.js"

NODE = shutil.which("node")
BATCH = {"index": 0, "candidates": [{"name": "Alpha", "freq": 2}]}
ROWS = [{"source_form": "Alpha", "basis": "established",
         "disposition": "accepted", "source": "https://dead.test/a"}]
REJECTION = "item 1: the cited page does not contain the claimed form."

# The corrected reading, in the three parts that carry it. Each is asserted
# separately so a partial regression names which half was lost.
SETTLES = "settles the form"
DEFERS_TO_THE_PROJECT = "read with section C's naming rule"
# The exception that IS the fix. Without it "settles the form" alone is still
# readable as mechanical transcription, so deleting this half -- or flipping
# `including` to `excluding` -- reinstates the whole defect while leaving the
# other three assertions green (codex code round 1, MINOR 1, admitted).
INCLUDES_WIDELY_USED = "including a widely-used target-language form"
# ...and its qualification, so the exception cannot be widened into a blanket
# licence for a form the project's own rule does not settle.
PREFERS_IT = "where that rule prefers it"
NOT_LETTER_BY_LETTER = "is never a letter-by-letter obligation overriding them"
RETIRED = "is enough on its own"

# The regeneration paragraph these two sites own. It is emitted only inside
# `if (rejectionReason)`.
REGEN_PARAGRAPH_OPENER = "Fix precisely what the reviewer named."


@pytest.fixture
def mod(tmp_path):
    """The shipped driver, imported from a staged scripts/ dir exactly as
    tests/glossary_dispatch_driver.test.py stages it -- json_stdout.py included,
    since the driver loads that sibling by exact path at import time."""
    scripts = tmp_path / "durable" / "scripts"
    scripts.mkdir(parents=True)
    target = scripts / "glossary_dispatch_driver.py"
    shutil.copy2(DRIVER, target)
    shutil.copy2(JSON_STDOUT, target.parent / "json_stdout.py")
    spec = importlib.util.spec_from_file_location(
        f"gcdb_h{abs(hash(str(target)))}", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def subst(**over):
    base = dict(durable_root="/durable", source_lang="he", target_lang="en",
                research_mode="live", run_id="runX", effort="high",
                citation_content_types="text/html", batch_agent_cap=10 ** 9,
                plugin_root="/plugin", resumed_batch_indices=[])
    base.update(over)
    return base


def render(mod):
    """All three renderings in ONE call_template_functions() invocation, so they
    come from the same executed copy of the template rather than from three
    invocations that could disagree."""
    return mod.call_template_functions(
        TEMPLATE, subst(), [BATCH],
        [{"key": "repair", "fn": "batchRepairPrompt",
          "args": [BATCH, 0, ROWS, "/private/tmp/ltgd.x/repair_0_attempt_0.json"]},
         {"key": "regen", "fn": "batchDispatchPrompt",
          "args": [BATCH, 1, REJECTION, "/private/tmp/ltgd.x/batch_0.json"]},
         {"key": "plain", "fn": "batchDispatchPrompt",
          "args": [BATCH, 0, None, "/private/tmp/ltgd.x/batch_0.json"]}],
        NODE)


def regen_paragraph(prompt):
    """The one rendered line that opens with the regeneration instruction.
    Asserting there is EXACTLY one is what makes the scoped negative below a
    real check: if the opener ever moved or was duplicated, a `next()` over an
    empty or ambiguous match would otherwise silently narrow what is tested."""
    hits = [line for line in prompt.split("\n")
            if line.startswith(REGEN_PARAGRAPH_OPENER)]
    assert len(hits) == 1, (
        f"expected exactly one line opening with {REGEN_PARAGRAPH_OPENER!r}, "
        f"found {len(hits)}")
    return hits[0]


@pytest.mark.skipif(NODE is None, reason="node required")
def test_the_repair_prompt_says_the_project_rule_settles_the_form(mod):
    """batchRepairPrompt(): the per-item repair rung. Its downgrade clause is the
    whole prompt's only statement of what `transliterated` means, so the negative
    can safely be whole-prompt here."""
    prompt = render(mod)["repair"]
    assert SETTLES in prompt
    assert DEFERS_TO_THE_PROJECT in prompt
    assert INCLUDES_WIDELY_USED in prompt
    assert PREFERS_IT in prompt
    assert NOT_LETTER_BY_LETTER in prompt
    assert RETIRED not in prompt, (
        "the repair prompt must not tell the model that transliteration has to "
        "be enough on its own -- that is the #901 defect")


@pytest.mark.skipif(NODE is None, reason="node required")
def test_the_regeneration_paragraph_says_the_project_rule_settles_the_form(mod):
    """batchDispatchPrompt()'s `if (rejectionReason)` branch. Scoped to the
    paragraph this issue owns: the rest of the rendering is the ordinary dispatch
    prompt, whose own research_mode paragraph is #891's site, not this one --
    same wording since 1.109.0, but not this file's to assert."""
    paragraph = regen_paragraph(render(mod)["regen"])
    assert SETTLES in paragraph
    assert DEFERS_TO_THE_PROJECT in paragraph
    assert INCLUDES_WIDELY_USED in paragraph
    assert PREFERS_IT in paragraph
    assert NOT_LETTER_BY_LETTER in paragraph
    assert RETIRED not in paragraph, (
        "the regeneration paragraph must not tell the model that "
        "transliteration has to be enough on its own -- that is the #901 defect")


@pytest.mark.skipif(NODE is None, reason="node required")
def test_the_regeneration_paragraph_is_absent_without_a_rejection_reason(mod):
    """The anti-vacuity half. If the opener appeared in every dispatch prompt,
    the scoped test above would be checking a paragraph that is not the
    regeneration branch at all, and a regression inside `if (rejectionReason)`
    would go unnoticed."""
    assert REGEN_PARAGRAPH_OPENER not in render(mod)["plain"]


@pytest.mark.skipif(NODE is None, reason="node required")
def test_every_clause_is_carried_by_both_downgrade_sites(mod):
    """#901 names TWO sites, and the two tests above check them one at a time. A
    clause that regressed at one site only would leave one of them green, and a
    reader comparing the two tests has to hold both lists in their head to see
    it. This states the property directly: each of the five clauses is present
    at BOTH sites, not at whichever one happens to be read first.

    What is asserted about the render itself is the KEY SET the template
    actually returned, never `len(carriers)` -- carriers is a two-element
    literal, so its length is a fact about this file and no mutation of the
    template could ever move it (closing simplifier pass, finding 2, admitted).
    The key set can move: a builder that vanished or was renamed fails here."""
    built = render(mod)
    assert set(built) == {"repair", "regen", "plain"}, (
        "every builder named in render() must have returned a value -- a "
        "missing key means a prompt this test claims to cover was never built")
    carriers = [built["repair"], regen_paragraph(built["regen"])]
    for clause in (SETTLES, DEFERS_TO_THE_PROJECT, INCLUDES_WIDELY_USED,
                   PREFERS_IT, NOT_LETTER_BY_LETTER):
        assert sum(clause in text for text in carriers) == 2, (
            f"both downgrade sites must carry {clause!r}")
