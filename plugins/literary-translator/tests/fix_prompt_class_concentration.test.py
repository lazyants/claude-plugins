"""tests/fix_prompt_class_concentration.test.py -- #534: fixPrompt() must report
the round's per-class concentration, and must state in the same breath that the
report grants it no authority over loci no finding named.

## Why the second half is the one that matters

Convergence here is per-segment and one-way: a reviewer sees exactly one
segment, so "is this wrong anywhere else?" is a question it cannot ask, and once
a unit is `converged` no automated step reopens it or cross-compares it (W6,
the operator's own consistency pass, is the one shipped step that does). #534
measured the
consequence on two live books -- three defect instances shipped inside CONVERGED
units in a single day, each surfaced only because something unrelated caused a
book-wide look. `runRound` does hold the whole findings array, but `findings[]`
carries no rule identity (review.schema.json is loc/severity/issue/suggest), so
no code can count a class. The fix turn is the only turn that reads every
finding's prose with the style contract already in hand, so it is the one that
can say `<rule>: N of M findings this round`.

That number is also precisely the observation that invites a sweep, and a sweep
is the worse failure: the enumerated class runs an order of magnitude larger
than its defect set, and most of the remainder is correct under a DIFFERENT rule
of the same contract, so a uniform sweep fails hardest exactly where the book is
most consistent. The field measurements behind that live in CHANGELOG.md's
1.62.0 entry -- deliberately not copied here, since nothing can check that two
hand-copied sets of digits still agree.

An earlier revision of #534 had the fix turn enumerate the dominant class across
its segment and edit the unnamed sites itself. It was cut: `findings[]` carries
no rule identity (review.schema.json is loc/severity/issue/suggest and nothing
else), so the fixer would have invented the class labels, authorized itself from
its own labels, chosen the candidates, judged its own evidence and written the
edits -- in a step that exists in the first place because a reviewer's findings
are unchecked prose (#532). Enumeration belongs to the operator at W6.

So this file's load-bearing assertion is not that the report exists. It is
`test_fix_prompt_report_grants_no_authority_over_unnamed_loci`, and its two
halves are asserted separately on purpose: a test that only checks the clause is
PRESENT stays green when the clause's consequence is negated, which is the same
blind spot the defect has.

## Mutation record (every pin was watched failing on its OWN clause)

Measured against this tree, not asserted:

- delete the grouping lines.push
  -> concentration RED, report-channel RED, authority GREEN
- delete only the no-licence lines.push
  -> authority RED, report-channel RED, concentration GREEN
- keep the "a report and not a licence" sentence and NEGATE its consequence
  ("you may also edit a site no finding named where it plainly instantiates the
  same rule ...")
  -> authority RED, everything else GREEN      <- the pin that carries this file
- replace ", and give the counts as <rule>: N of M findings this round" with
  ", and list the rules you found"
  -> concentration RED, everything else GREEN
- the shipped-rules control test stays GREEN under all four.

## What this does NOT test

- That an actual fix turn obeys the instruction. Nothing mechanical parses the
  fixer's reply (runRound scans it only for the DRAFT_MISSING sentinel), so this
  is a prompt-TEXT contract, exactly like the refusal report beside it.
- W6's operator rule prose in SKILL.md, which
  `tests/skill_doc_class_sweep_rule.test.py` owns.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MASS_TRANSLATE_WF_SRC = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
    / "mass-translate-wf.template.js"
)
assert MASS_TRANSLATE_WF_SRC.is_file(), f"template not found at {MASS_TRANSLATE_WF_SRC}"

NODE_PATH = shutil.which("node")

sys.path.insert(0, str(Path(__file__).parent))
from _workflow_instantiation import instantiate_mass_translate  # noqa: E402

# Same slice boundary tests/fix_prompt_self_check_removed.test.py uses -- fixPrompt
# is a function DECLARATION defined well before this marker, so slicing here avoids
# needing the Workflow-tool-shaped async wrapper the whole file would require.
_JS_CUT_MARKER = "const estimatedCalls"


def _instantiate_and_slice():
    raw = MASS_TRANSLATE_WF_SRC.read_text(encoding="utf-8")
    assert _JS_CUT_MARKER in raw, (
        f"mass-translate-wf.template.js no longer contains the expected "
        f"{_JS_CUT_MARKER!r} slice boundary -- update this test's harness"
    )
    head, _, _tail = raw.partition(_JS_CUT_MARKER)
    # PLUGIN_ROOT is deliberately empty -- this harness only slices out
    # function declarations and never reaches the #607 non-empty-plugin-root
    # refusal.
    head = instantiate_mass_translate(
        source=head,
        durable_root="/fixture/durable_root",
        run_id="20260801T000000Z",
        source_lang="fr",
        target_lang="ru",
        max_fix_rounds=3,
        batch_agent_cap=999,
        max_codex_jobs_per_batch=999,
        verse_policy_instruction_block="Test verse policy instructions.",
        codex_companion_path_json="/fake/codex-companion.mjs",
        effort="high",
        model="",
        plugin_root="",
    )
    head = head.replace("export const meta", "const meta", 1)
    return head


def _fix_prompt_text(tmp_path, seg="seg01", round_num=2, rev_obj=None):
    assert NODE_PATH is not None, "node executable not found on PATH -- required to run this test file"
    head = _instantiate_and_slice()
    rev_obj = rev_obj if rev_obj is not None else {
        "clean": False, "coverage_ok": True,
        "findings": [{"loc": "PARA:seg01:0001", "severity": "minor", "issue": "x", "suggest": "y"}],
        "draft_sha1": "0123456789abcdef",
    }
    footer = (
        "\nvar __out = fixPrompt(" + json.dumps(seg) + ", " + json.dumps(round_num) + ", "
        + json.dumps(rev_obj) + ");\n"
        "console.log(JSON.stringify(__out));\n"
    )
    script = 'var args = "[]";\n' + head + footer
    script_path = tmp_path / "fix_prompt_probe.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run([NODE_PATH, str(script_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        f"node execution of the real, instantiated fixPrompt() failed "
        f"(rc={result.returncode}):\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_fix_prompt_asks_for_the_rounds_per_class_concentration(tmp_path):
    """The SHARE, not just the labels: `<rule>: N of M findings this round`."""
    text = _fix_prompt_text(tmp_path)
    assert "<rule>: N of M findings this round" in text, (
        "fixPrompt must ask for the round's per-class concentration in the "
        "N-of-M form -- a grouping without the share does not tell the operator "
        "that one rule DOMINATES the round, which is the whole signal (#534). "
        f"Prompt was:\n{text}"
    )
    assert "style_bible.md each one instantiates" in text, (
        "the grouping key must be the rule in style_bible.md that a finding "
        "instantiates -- findings[] carries no rule identity of its own "
        "(review.schema.json: loc/severity/issue/suggest), so the prompt has to "
        "name what to group BY"
    )
    assert "instantiates no rule" in text, (
        "the prompt must license 'this finding instantiates no rule' as a real "
        "answer -- without it, every one-off slip gets forced into some class "
        "and the concentration number becomes noise"
    )


def test_fix_prompt_report_grants_no_authority_over_unnamed_loci(tmp_path):
    """THE load-bearing pin of #534.

    Asserted in two independent halves, because the failure mode is a clause
    that is PRESENT while its consequence is negated -- a presence-only check
    shares that blind spot exactly.
    """
    text = _fix_prompt_text(tmp_path)

    # Half 1: the report is declared non-authorizing.
    assert "a report and not a licence" in text, (
        "the instruction that produces the concentration number must say, in "
        "the same breath, that it is not a licence -- the number is precisely "
        "the observation that invites a sweep, and a sweep is the worse failure "
        "(#534 measured 109 occurrences for ~10 defects, and 66 sites of one "
        "enumerated class that were compliant with a DIFFERENT rule)"
    )

    # Half 2: the CONSEQUENCE. This is the half that stays green if someone
    # keeps the sentence above and negates what it implies.
    assert "never edit a site no finding named" in text, (
        "fixPrompt must state the consequence explicitly: the fix turn edits "
        "only loci a finding named. Keeping the 'not a licence' sentence while "
        "dropping this one is exactly the mutation this test exists to catch"
    )
    assert "not even one that plainly instantiates the same rule as a finding you just applied" in text, (
        "the no-edit rule must survive the ONE case that will actually arise -- "
        "an unnamed site that obviously belongs to the class just fixed. That "
        "is the site a sweep starts at, so leaving it to inference is leaving "
        "the property unstated"
    )
    assert "operator's to enumerate and adjudicate site by site" in text, (
        "the prompt must say where the enumeration DOES belong (the operator, "
        "W6), and why -- it reaches segments this call was not given, and "
        "touching a converged unit stales it"
    )


def test_fix_prompt_report_channel_is_the_reply_never_the_draft(tmp_path):
    """The report goes where the refusal report goes, and nowhere else.

    `notes[]` is the translator's channel and the shipped #532 rule already
    forbids writing a marker there; a concentration report written into the
    draft would also move draft_sha1, which derive_next_action() reads as
    ADVANCE.
    """
    text = _fix_prompt_text(tmp_path)
    assert "on the lines above the FIXED line" in text, (
        "the concentration report must be placed above the FIXED sentinel, the "
        "same operator channel the refusal report already uses"
    )
    assert "Record none of this in the draft" in text, (
        "the prompt must forbid recording the report in the draft"
    )
    assert "Record none of this in the draft either -- notes[] is the translator's channel" in text, (
        "and must say WHY, inside the new clause. Measured: the bare phrase "
        "\"notes[] is the translator's channel\" is already carried by the "
        "shipped #532 refusal line at origin/main, so pinning it alone stays "
        "green when this entire clause is deleted"
    )
    assert "nothing downstream parses it" in text, (
        "the prompt must not imply the report is consumed by anything: runRound "
        "scans callFix's return only for the DRAFT_MISSING sentinel"
    )
    assert "under the same DRAFT_MISSING prohibition stated above" in text, (
        "and the new report must be brought under that sentinel prohibition "
        "explicitly. The shipped rule forbidding the string is worded about "
        "'a refusal report'; this release adds a SECOND free-prose channel "
        "above the FIXED line, and a class label ultimately derives from "
        "reviewer-authored prose nothing has checked. runRound matches "
        "DRAFT_MISSING by containment (mentionedAnywhere), so an echoed label "
        "would read as a failed fix call -- recoverable, but a wasted re-run"
    )


def test_shipped_fix_turn_rules_are_unmodified(tmp_path):
    """Control: #534 appends two lines. The one shipped clause that no other
    test file owns must survive them.

    The rest of fixPrompt's contract is already pinned against this same
    rendered template -- "Apply an entry you can substantiate; refuse one you
    cannot." by tests/segment_dispatch_driver.test.py, and the DRAFT_MISSING
    sentinel plus the rewrite target by
    tests/fix_prompt_self_check_removed.test.py -- so repeating them here would
    buy a second copy of an existing pin, not a second check.
    """
    text = _fix_prompt_text(tmp_path)
    assert "a form that resolves in neither is not canon and the finding is refused" in text, (
        "#529's canon-authority scoping must survive unchanged -- measured, no "
        "other test file pins this clause"
    )


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
