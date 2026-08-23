"""tests/fix_prompt_self_check_removed.test.py -- #409 Step 3: fixPrompt() in
mass-translate-wf.template.js must no longer ask the fixer to run
validate_draft.py on its own edit and self-certify the result.

## Why this is a defect worth a dedicated regression lock

Before this fix, fixPrompt's own text told the fixer: "run validate_draft.py
... and confirm it prints OK -- if your own edit broke coverage or a
placeholder, repair it and rewrite the file again until it prints OK." That
instruction was dead text: runRound's own handling of callFix's return value
(`fx`) only ever scans it for the literal DRAFT_MISSING sentinel (see
runRound's own comment block in mass-translate-wf.template.js) -- nothing
downstream ever parsed, checked, or acted on a self-reported "OK". Worse than
merely unenforced, it read as an assurance the pipeline provides while
providing none -- exactly the "a deterministic gate must not be executed by
the party it is checking" defect #409 Step 3 exists to close.

This file is deliberately self-contained (this plugin's "no shared lib
between self-contained scripts/tests" convention -- see
tests/draft_path_convention.test.py's own module docstring for the same
rule applied to prompt-builder harnesses) rather than editing an existing
shared multi-purpose test file. It reuses the SAME instantiate-slice-and-run
technique tests/draft_path_convention.test.py's run_prompt_probe() already
uses (extract the REAL, currently-shipped template, substitute its tokens,
run the real fixPrompt() function under node) -- never a hand-typed
reimplementation of the prompt text.

## What this does NOT test

- The `estimatedCalls` batch-size formula: tests/batch_size_estimator.test.py
  already owns that, and its own `test_estimator_formula_matches_closed_form`
  /`test_estimator_boundary_exactly_at_cap_permits_dispatch_and_converges`
  fixtures were re-run against this exact change and still pass -- this
  prompt-text-only edit adds no new agent() call and changes no call count.
- DRAFT_MISSING handling: unaffected by this change (still parsed, still
  load-bearing) -- covered here only as a non-regression control assertion,
  not as this file's own primary target.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MASS_TRANSLATE_WF_SRC = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
    / "mass-translate-wf.template.js"
)
assert MASS_TRANSLATE_WF_SRC.is_file(), f"template not found at {MASS_TRANSLATE_WF_SRC}"

NODE_PATH = shutil.which("node")

# Same slice boundary tests/draft_path_convention.test.py's own
# _JS_CUT_MARKER uses -- fixPrompt is a function DECLARATION, defined well
# before this marker, so slicing here (rather than instantiating the WHOLE
# file, which requires the Workflow-tool-shaped async wrapper
# tests/batch_size_estimator.test.py's own _wrap_for_execution() builds) is
# sufficient and keeps this file simpler.
_JS_CUT_MARKER = "const estimatedCalls"

# Same minimal substitutions tests/draft_path_convention.test.py's own
# _instantiate_and_slice_js() uses -- every token that appears before the
# cut marker must resolve; the exact values are irrelevant to this file's
# own prompt-TEXT assertions (they only check for the presence/absence of
# specific substrings in fixPrompt's returned string).
_SUBSTITUTIONS = {
    "{{DURABLE_ROOT}}": "/fixture/durable_root",
    "{{RUN_ID}}": "20260801T000000Z",
    "{{SOURCE_LANG}}": "fr",
    "{{TARGET_LANG}}": "ru",
    "{{MAX_FIX_ROUNDS}}": "3",
    "{{BATCH_AGENT_CAP}}": "999",
    "{{MAX_CODEX_JOBS_PER_BATCH}}": "999",
    "{{VERSE_POLICY_INSTRUCTION_BLOCK}}": "Test verse policy instructions.",
    "{{CODEX_COMPANION_PATH_JSON}}": json.dumps("/fake/codex-companion.mjs"),
    "{{EFFORT}}": "high",
    "{{MODEL}}": "",
    # #412/#607 -- PLUGIN_ROOT must be a real path: since #607 the template
    # refuses an empty one before dispatch. fixPrompt's self-check text does
    # not depend on the value; it only needs to resolve to something valid.
    "{{PLUGIN_ROOT}}": json.dumps("/fixture/plugin/literary-translator"),
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


def _fix_prompt_text(tmp_path, seg="seg01", round_num=2, rev_obj=None):
    assert NODE_PATH is not None, "node executable not found on PATH -- required to run this test file"
    head = _instantiate_and_slice()
    rev_obj = rev_obj if rev_obj is not None else {
        "clean": False, "coverage_ok": True,
        "findings": [{"loc": "p1", "severity": "minor", "issue": "x", "suggest": "y"}],
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


def test_fix_prompt_no_longer_asks_fixer_to_self_certify_coverage(tmp_path):
    text = _fix_prompt_text(tmp_path)
    assert "confirm it prints OK" not in text, (
        "fixPrompt must no longer ask the fixer to confirm validate_draft.py "
        "prints OK -- that self-report was never parsed by anything "
        "downstream (runRound only scans callFix's return for the literal "
        "DRAFT_MISSING sentinel), so it was dead text implying an assurance "
        "the pipeline does not actually provide"
    )
    assert "validate_draft.py" not in text, (
        "fixPrompt's own prompt text must not mention validate_draft.py at "
        "all anymore -- the ONLY reference was the dead self-check "
        "instruction this fix removes; any remaining mention would mean "
        "the self-check text (or something equivalent) survived elsewhere "
        "in this prompt"
    )
    assert "repair it and rewrite the file again until it prints OK" not in text, (
        "the self-certification loop instruction must be fully gone, not "
        "just the leading clause"
    )


def test_fix_prompt_still_instructs_rewriting_the_draft(tmp_path):
    """Non-regression control: the dead self-check clause hung off the SAME
    sentence as the (still necessary) draft-rewrite instruction -- confirm
    the rewrite instruction survived the edit intact."""
    text = _fix_prompt_text(tmp_path)
    assert "Rewrite /fixture/durable_root/segments/seg01.draft.json with your fixes." in text, (
        f"fixPrompt must still instruct rewriting the canonical draft path "
        f"with the fixer's edits; not found in:\n{text}"
    )


def test_fix_prompt_draft_missing_handling_unaffected(tmp_path):
    """Non-regression control: DRAFT_MISSING IS parsed (runRound scans for
    it) and load-bearing -- this edit must not have touched it."""
    text = _fix_prompt_text(tmp_path)
    assert "DRAFT_MISSING seg01" in text, (
        f"fixPrompt must still instruct returning the DRAFT_MISSING sentinel "
        f"when the draft is missing/not ready; not found in:\n{text}"
    )
    assert "do not translate it yourself" in text


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
