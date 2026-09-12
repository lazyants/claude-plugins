"""tests/entity_markup_untaggable_prompt.test.py -- #932: translatePrompt(),
reviewDispatchPrompt() and fixPrompt() each name
`${ROOT}/entity_markup_untaggable.json` and state their own role's duty
toward it, so the W7 refusal (`assemble.py`'s
`entity_markup_canon_collision`) is knowable at translate/review/fix time
instead of only at render time.

## Why the RENDERED prompt is pinned, not the template source

A source-text grep for the pinned literals stays green even with the
`lines.push(...)` append commented out, or with the whole clause moved
inside an `if (false)` branch -- the grep only proves the characters exist
somewhere in the file, never that the running prompt-builder emits them.
This plugin's own convention (see tests/fix_prompt_prior_refusals.test.py)
is to instantiate the REAL, shipped `mass-translate-wf.template.js` under
node and assert on what the three functions actually return -- so a
disabled or dead append fails here exactly as an absent one would.

## What this file does NOT test

- The new script `entity_markup_untaggable.py` itself, or its resolution
  logic (owned by tests/entity_markup_untaggable.test.py).
- `final_audit.py`'s WARN check (owned by
  tests/final_audit_untaggable_warn.test.py).
- Any judgement about translation content.

Self-contained per this plugin's no-shared-lib convention; runs the REAL
shipped prompt functions under node, reusing the instantiate-slice-and-run
technique of tests/fix_prompt_prior_refusals.test.py /
tests/fix_prompt_prior_round.test.py.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MASS_TRANSLATE_WF_SRC = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
    / "mass-translate-wf.template.js"
)
assert MASS_TRANSLATE_WF_SRC.is_file(), f"template not found at {MASS_TRANSLATE_WF_SRC}"

NODE_PATH = shutil.which("node")

sys.path.insert(0, str(Path(__file__).parent))
from _workflow_instantiation import instantiate_mass_translate  # noqa: E402

_JS_CUT_MARKER = "const estimatedCalls"
_RUN_ID = "20260801T000000Z"
_ROOT = "/fixture/durable_root"
_SEG = "seg01"

# The one string every affected prompt must carry exactly once, and the one
# the two negative-control prompts must never carry at all.
_ARTIFACT_PATH = _ROOT + "/entity_markup_untaggable.json"

pytestmark = pytest.mark.skipif(
    NODE_PATH is None,
    reason="node executable not found on PATH -- required to run the real template",
)


def _instantiate_and_slice():
    raw = MASS_TRANSLATE_WF_SRC.read_text(encoding="utf-8")
    assert _JS_CUT_MARKER in raw, (
        f"mass-translate-wf.template.js no longer contains the expected "
        f"{_JS_CUT_MARKER!r} slice boundary -- update this test's harness"
    )
    head, _, _tail = raw.partition(_JS_CUT_MARKER)
    # PLUGIN_ROOT deliberately empty -- this harness only slices out function
    # declarations and never reaches the #607 non-empty-plugin-root refusal.
    head = instantiate_mass_translate(
        source=head,
        durable_root=_ROOT,
        run_id=_RUN_ID,
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
    return head.replace("export const meta", "const meta", 1)


def _probe(tmp_path, name, fn, args):
    """Call one REAL instantiated template function under node and return what
    it returned (a JSON-encoded string, since every function here returns the
    joined prompt text rather than a structured value)."""
    footer = ("\nvar __out = %s(%s);\nconsole.log(JSON.stringify(__out));\n"
              % (fn, ", ".join(json.dumps(a) for a in args)))
    script_path = tmp_path / ("%s_probe.js" % name)
    script_path.write_text('var args = "[]";\n' + _instantiate_and_slice() + footer,
                           encoding="utf-8")
    result = subprocess.run([NODE_PATH, str(script_path)], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, (
        f"node execution of the real, instantiated {fn}() failed "
        f"(rc={result.returncode}):\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def _translate_prompt(tmp_path):
    return _probe(tmp_path, "translate", "translatePrompt", [_SEG])


def _review_prompt(tmp_path):
    return _probe(tmp_path, "review_dispatch", "reviewDispatchPrompt", [_SEG, "1"])


def _fix_prompt(tmp_path):
    rev = {
        "clean": False, "coverage_ok": True,
        "findings": [{"loc": "PARA:seg01:0005", "severity": "high",
                      "issue": "x", "suggest": "y"}],
        "draft_sha1": "0123456789abcdef",
        "dispatch_token": "%s:%s:r1" % (_RUN_ID, _SEG),
    }
    # round 1 -- the append lives in an unconditional push, not the
    # round>=2 previous-verdict block, so it must render on round 1.
    return _probe(tmp_path, "fix_r1", "fixPrompt", [_SEG, 1, rev])


def _read_review_prompt(tmp_path):
    return _probe(tmp_path, "read_review", "readReviewPrompt", [_SEG])


# ---------------------------------------------------------------------------
# The path, exactly once, in each of the three affected prompts
# ---------------------------------------------------------------------------

def test_translate_prompt_names_the_path_exactly_once(tmp_path):
    prompt = _translate_prompt(tmp_path)
    assert prompt.count(_ARTIFACT_PATH) == 1, prompt


def test_review_dispatch_prompt_names_the_path_exactly_once(tmp_path):
    prompt = _review_prompt(tmp_path)
    assert prompt.count(_ARTIFACT_PATH) == 1, prompt


def test_fix_prompt_names_the_path_exactly_once(tmp_path):
    prompt = _fix_prompt(tmp_path)
    assert prompt.count(_ARTIFACT_PATH) == 1, prompt


# ---------------------------------------------------------------------------
# Per-role pinned literals (plan section 3's final prose)
# ---------------------------------------------------------------------------

def test_translate_prompt_pinned_literals(tmp_path):
    prompt = _translate_prompt(tmp_path)
    for fragment in (
        "leave such a name untagged",
        "never invent a ref",
        "NFC-normalized",
        "If the file is absent",
        # ref-else-tagged-text precedence, as THIS prompt renders it.
        "ref attribute when it carries one",
        # the operator-convention carve-out: a ref naming a different
        # identity under the project's own convention is untouched.
        "stated convention",
    ):
        assert fragment in prompt, f"missing {fragment!r} in translatePrompt:\n{prompt}"


def test_review_dispatch_prompt_pinned_literals(tmp_path):
    prompt = _review_prompt(tmp_path)
    for fragment in (
        "Never raise a finding asking for a tag",
        "lack of a tag as an inconsistency",
        "NFC-normalized",
        "If the file is absent",
        # ref-else-tagged-text precedence, as THIS prompt renders it.
        "ref attribute when present",
    ):
        assert fragment in prompt, f"missing {fragment!r} in reviewDispatchPrompt:\n{prompt}"


def test_fix_prompt_pinned_literals(tmp_path):
    prompt = _fix_prompt(tmp_path)
    for fragment in (
        "is refused with that reason",
        "NFC-normalized",
        "If the file is absent",
        # ref-else-tagged-text precedence, as THIS prompt renders it.
        "its ref if present",
        # the fixer's own no-invented-ref clause.
        "no ref is invented",
    ):
        assert fragment in prompt, f"missing {fragment!r} in fixPrompt:\n{prompt}"


# ---------------------------------------------------------------------------
# Negative control -- a prompt that must NOT carry it
# ---------------------------------------------------------------------------

def test_read_review_prompt_does_not_mention_it(tmp_path):
    """readReviewPrompt() is a mechanical relay of the reviewer's already-written
    verdict fields (clean/coverage_ok/findings/draft_sha1) and calls none of the
    three prompt-builder functions above, so it is a clean negative control:
    if it ever picked up the artifact path, the append landed on the wrong
    push or the wrong function entirely."""
    prompt = _read_review_prompt(tmp_path)
    assert "entity_markup_untaggable" not in prompt, prompt


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
