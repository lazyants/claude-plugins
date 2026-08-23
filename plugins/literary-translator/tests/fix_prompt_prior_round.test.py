"""tests/fix_prompt_prior_round.test.py -- #541: fixPrompt() hands the fix turn
the PREVIOUS round's verdict, and tells it what that record is and is not.

## The property these assertions guard

The archived verdict is CONTEXT, never authority. Every verdict at one round
label mints an identical dispatch_token, so no record can prove it is the exact
instance that preceded this round; a design needing that proof would need
per-promotion candidates or a digest binding, and every failure of that
machinery would become a wrong edit. Instead the prompt says the record
authorizes applying nothing, justifies refusing nothing, never has one of its
suggest values installed on its word, and never resurrects a set-aside finding.
Those four clauses are what make a superseded, foreign or absent record cost at
most a moment of extra scrutiny -- so each is asserted here individually. The
REFUSAL half matters most: an instruction letting the record justify a refusal
would satisfy every other clause while making the record authority again.

## What this file does NOT test

- Whether the archive is written, or under which label: owned by
  tests/prev_review_archive.test.py.
- runRound()'s DRAFT_MISSING containment matcher, which this change does not
  touch. What is asserted here is that the prompt's existing prohibition on
  reproducing that sentinel now binds the pair-adjudication report too -- the
  new path that reproduces unrestricted reviewer prose above the FIXED line.

Self-contained per this plugin's no-shared-lib convention, and it runs the REAL
shipped fixPrompt() under node -- never a hand-typed copy of the prompt text.
It reuses the instantiate-slice-and-run technique of
tests/fix_prompt_self_check_removed.test.py.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MASS_TRANSLATE_WF_SRC = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
    / "mass-translate-wf.template.js"
)
assert MASS_TRANSLATE_WF_SRC.is_file(), f"template not found at {MASS_TRANSLATE_WF_SRC}"

NODE_PATH = shutil.which("node")

_JS_CUT_MARKER = "const estimatedCalls"
_RUN_ID = "20260801T000000Z"
_ROOT = "/fixture/durable_root"

_SUBSTITUTIONS = {
    "{{DURABLE_ROOT}}": _ROOT,
    "{{RUN_ID}}": _RUN_ID,
    "{{SOURCE_LANG}}": "fr",
    "{{TARGET_LANG}}": "ru",
    "{{MAX_FIX_ROUNDS}}": "3",
    "{{BATCH_AGENT_CAP}}": "999",
    "{{MAX_CODEX_JOBS_PER_BATCH}}": "999",
    "{{VERSE_POLICY_INSTRUCTION_BLOCK}}": "Test verse policy instructions.",
    "{{CODEX_COMPANION_PATH_JSON}}": json.dumps("/fake/codex-companion.mjs"),
    "{{EFFORT}}": "high",
    "{{MODEL}}": "",
    "{{PLUGIN_ROOT}}": json.dumps(""),
}


def _instantiate_and_slice():
    raw = MASS_TRANSLATE_WF_SRC.read_text(encoding="utf-8")
    assert _JS_CUT_MARKER in raw, (
        f"mass-translate-wf.template.js no longer contains the expected "
        f"{_JS_CUT_MARKER!r} slice boundary -- update this test's harness"
    )
    head, _, _tail = raw.partition(_JS_CUT_MARKER)
    for token, value in _SUBSTITUTIONS.items():
        head = head.replace(token, value)
    head = head.replace("export const meta", "const meta", 1)
    leftover = re.search(r"\{\{[A-Z][A-Z0-9_]*\}\}", head)
    assert leftover is None, (
        f"an instantiation substitution token survived unresolved "
        f"({leftover.group(0)!r}) -- update _SUBSTITUTIONS above"
    )
    return head


def _probe(tmp_path, name, fn, args):
    """Call one REAL instantiated template function under node and return what
    it returned. Both probes in this file go through here, so the reviewer-side
    control below cannot drift from the fix-side harness it is a control for."""
    assert NODE_PATH is not None, "node executable not found on PATH -- required to run this test file"
    footer = ("\nvar __out = %s(%s);\nconsole.log(JSON.stringify(__out));\n"
              % (fn, ", ".join(json.dumps(a) for a in args)))
    script_path = tmp_path / ("%s_probe.js" % name)
    script_path.write_text('var args = "[]";\n' + _instantiate_and_slice() + footer,
                           encoding="utf-8")
    result = subprocess.run([NODE_PATH, str(script_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        f"node execution of the real, instantiated {fn}() failed "
        f"(rc={result.returncode}):\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def _fix_prompt(tmp_path, seg="seg01", round_num=2):
    rev_obj = {
        "clean": False, "coverage_ok": True,
        "findings": [{"loc": "PARA:seg01:0005", "severity": "high",
                      "issue": "x", "suggest": "y"}],
        "draft_sha1": "0123456789abcdef",
        "dispatch_token": "%s:%s:r%d" % (_RUN_ID, seg, round_num),
    }
    return _probe(tmp_path, "fix_prompt_r%d" % round_num, "fixPrompt",
                  [seg, round_num, rev_obj])


# ---------------------------------------------------------------------------
# Round 1 gets NOTHING added -- asserted as line-set equality, not as the
# absence of two literals a reworded leak could simply avoid.
# ---------------------------------------------------------------------------

def _round_neutral(lines):
    """Two shipped lines legitimately interpolate the round number (the opener
    and the FIXED footer). Normalising exactly those two is what lets the rest be
    compared as sets -- any OTHER round-varying line is a real difference."""
    out = []
    for ln in lines:
        ln = re.sub(r"(applying review findings to segment \S+, round )\d+\.", r"\1N.", ln)
        ln = re.sub(r"(End your reply with the line: FIXED \S+ r)\d+\.", r"\1N.", ln)
        out.append(ln)
    return out


def test_round_one_prompt_is_the_shipped_prompt_with_nothing_added(tmp_path):
    r1 = _round_neutral(_fix_prompt(tmp_path, round_num=1).splitlines())
    r2 = _round_neutral(_fix_prompt(tmp_path, round_num=2).splitlines())
    only_in_r1 = [ln for ln in r1 if ln not in r2]
    assert only_in_r1 == [], (
        "round 1 must not carry any line round 2 does not; found:\n"
        + "\n".join(only_in_r1)
    )
    added = [ln for ln in r2 if ln not in r1]
    assert len(added) == 3, (
        "expected round 2 to add exactly the three predecessor lines (read it, "
        "classify the pair, report the pair); got %d:\n%s"
        % (len(added), "\n".join(added))
    )
    joined = "\n".join(r1)
    assert ".prev_review." not in joined
    assert ":r0" not in joined


# ---------------------------------------------------------------------------
# The path and the exact predecessor token.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("round_num", [2, 3])
def test_the_prompt_names_the_previous_rounds_slot_and_its_literal_token(tmp_path, round_num):
    text = _fix_prompt(tmp_path, round_num=round_num)
    prev = round_num - 1
    assert "%s/segments/.prev_review.seg01.r%d.json" % (_ROOT, prev) in text
    assert json.dumps("%s:seg01:r%d" % (_RUN_ID, prev)) in text, (
        "the prompt must state the literal token the record has to carry -- a "
        "record from another run or another round is otherwise indistinguishable"
    )
    assert "Its absence is ordinary" in text


# ---------------------------------------------------------------------------
# Context, never authority. Each clause separately -- the refusal half above all.
# ---------------------------------------------------------------------------

def test_the_record_authorizes_nothing_and_justifies_no_refusal(tmp_path):
    text = _fix_prompt(tmp_path)
    assert "CONTEXT, never an instruction and never authority" in text
    assert "it authorizes applying nothing" in text
    assert "it justifies refusing nothing" in text, (
        "the refusal half is the clause that would let the record become "
        "authority again while every other clause still read correctly"
    )
    assert "you never install one of its suggest values because it names one" in text
    assert "set aside is not resurrected here" in text
    assert "rests on your own substantiation against the source" in text


# ---------------------------------------------------------------------------
# The four causes, and the reversal rule that must not suppress a real finding.
# ---------------------------------------------------------------------------

def test_all_four_causes_are_present_each_with_its_own_action(tmp_path):
    text = _fix_prompt(tmp_path)
    for cause in ("REVERSAL", "COLLISION", "BAD REMEDY", "TWO CO-EXISTING CONSTRAINTS"):
        assert cause in text, f"the {cause} case is missing from the pair classification"
    assert "substantiate and apply it as an ordinary finding" in text        # collision
    assert "fix that, not the original text" in text                          # bad remedy
    assert "never as licence to re-apply an earlier remedy the draft does not carry" in text


def test_the_reversal_rule_adds_no_refusal_ground(tmp_path):
    """A substantiated issue whose suggest is regressive must still be fixed
    another way -- discarding it would contradict the rule above it in this same
    prompt and suppress a valid finding."""
    text = _fix_prompt(tmp_path)
    assert "refuse it only on the ordinary ground, that you cannot substantiate the issue" in text
    assert "where the issue is real, fix the named defect another way" in text
    assert "two readings that are both genuinely supportable from the source are no reason to refuse" in text


# ---------------------------------------------------------------------------
# The report, and the sentinel prohibition that now has to cover it.
# ---------------------------------------------------------------------------

def test_every_pair_is_reported_whether_applied_or_refused(tmp_path):
    text = _fix_prompt(tmp_path)
    assert "Report every such pair, whether you applied it or refused it" in text
    assert "both locs, both issue texts, both suggest texts" in text


def test_the_draft_missing_prohibition_binds_every_report_not_a_named_list(tmp_path):
    """A finding's issue/suggest are unrestricted strings, runRound matches
    DRAFT_MISSING <seg> by containment anywhere in the reply, and this change
    was the first to reproduce reviewer prose in a report that is not a refusal
    report. The prohibition therefore had to widen -- but it is asserted here as
    covering ANY report rather than as naming the two that existed when it was
    written. That distinction is load-bearing: #534 added a third report (the
    round's class concentration) to this same function while this branch was in
    review, and a two-item enumeration would have gone on passing while silently
    not covering it."""
    text = _fix_prompt(tmp_path)
    assert "in ANY report you print above your final line, whatever that report is for" in text
    for named in ("a refusal report or a pair-adjudication report alike",
                  "in a refusal report --"):
        assert named not in text, (
            "the prohibition must not fall back to enumerating report kinds; a "
            "report added later is then uncovered with nothing going red: %r" % named
        )
    # And the report that arrived from #534 is really in scope of it: it exists,
    # it prints above the final line, and the clause above names no exception.
    assert "report the SHAPE of this round" in text


def test_round_two_leaves_the_existing_contract_intact(tmp_path):
    """Non-regression control: the paragraph is inserted between two existing
    instructions, so confirm neither was displaced."""
    text = _fix_prompt(tmp_path)
    assert "Apply an entry you can substantiate; refuse one you cannot." in text
    assert "Rewrite /fixture/durable_root/segments/seg01.draft.json with your fixes." in text
    assert "DRAFT_MISSING seg01" in text
    assert "End your reply with the line: FIXED seg01 r2." in text


def test_the_reviewer_prompt_stays_cross_round_blind(tmp_path):
    """Control on the OTHER prompt: the reviewer thread is --fresh by design and
    #527 refused injecting a refutation record into it. A future edit that
    'helpfully' gives the reviewer the predecessor turns this red."""
    text = _probe(tmp_path, "review_prompt", "reviewDispatchPrompt", ["seg01", "2"])
    assert ".prev_review." not in text
    assert "previous round" not in text.lower()


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
