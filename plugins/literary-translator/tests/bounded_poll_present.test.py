"""tests/bounded_poll_present.test.py -- regression-lock for the #97 fix:
CONTRACT-1.2.0-reliability.md §7/§8's shared codex work-call pattern
("DISPATCH -- codex (agentType:'codex:codex-rescue' pinned), schema-less,
fire-and-forget ... WAIT -- Claude, low-effort, bounded bash poll
(translate's waitPrompt shape) of a readiness script ...").

Before 1.2.0, only translate's dispatch was bounded-polled; review and
glossary-batch were bare unbounded `await agent()` codex calls -- a
forwarder-detached job hanging on either wedged the whole run (#97). The
1.2.0 fix moved review and glossary onto translate's own
dispatch-then-bounded-poll discipline. This file locks THREE
dispatch/wait pairs across both templates, grep-driven and per-symbol
(this suite's own established style for a cross-cutting invariant like
this one -- see review_prompt_schema_drift.test.py's hand-rolled JS
object-literal parser for the same philosophy applied to schema
literals):

  translate      : translatePrompt      (mass-translate-wf.template.js, dispatched in translateStage)
                   <-> waitPrompt        (mass-translate-wf.template.js, polled in reviewFixLoop)     -- draft_ready.py
  review         : reviewDispatchPrompt (mass-translate-wf.template.js, dispatched in callReviewDispatch)
                   <-> reviewWaitPrompt  (mass-translate-wf.template.js, polled in getVerifiedReview)  -- review_ready.py
  glossary batch : batchDispatchPrompt  (glossary-pass-wf.template.js, dispatched in batchStep)
                   <-> batchWaitPrompt   (glossary-pass-wf.template.js, polled in batchStep)           -- canon_validate.py --check-batch

For each pair this file asserts, straight off the REAL shipped template
source (never a reimplementation/guess about its content):
  (a) the dispatch's own `agent()` call site sets `agentType` to a codex
      value AND carries NO `schema` option (schema-less, fire-and-forget,
      CONTRACT §7 step 1);
  (b) the paired WAIT prompt-builder function's own generated text
      contains a bounded `for i in $(seq 1 N)` poll loop invoking the
      correct readiness script, and its OWN `agent()` call site carries
      NO `agentType` (a plain Claude call, never a second codex dispatch).

Plus one EXEMPTION positive control: `callFix`/`fixPrompt` (CONTRACT §8:
"Keep callFix/fixPrompt as-is") is a direct, unbounded Claude `await
agent()` with no agentType and, deliberately, NO bounded-poll companion
-- the #97 restructure explicitly does NOT touch it (a forward-detached
job can't happen on a Claude call; a sha-changed readiness gate would
false-time-out a no-op fix). This test proves the pattern-matching in
this file is genuinely discriminating on `agentType`, not flagging every
bare `await agent(...)` call site indiscriminately.

translate's own pair is a POSITIVE CONTROL: it was already bounded
pre-1.2.0 (only review/glossary needed the #97 restructure), so it must
be GREEN against the file as it stands today regardless of whether
Owners A/B/C's own 1.2.0 changes have landed yet -- if it is somehow NOT
green, that is a self-inconsistency in THIS test file, not a
pending-owner situation.

Text-extraction approach (documented so the intent is auditable, not just
the regexes): every prompt-builder / call-wrapper function in both
templates is a FLAT, top-level, non-nested `function name(...) { ... }`
(or `async function`) declaration -- no closure here ever wraps another
named top-level function -- so `extract_function_body()` below slices a
function's full text by LINE BOUNDARY (from its own declaration to the
next top-level function declaration), never by brace-depth counting
(which would otherwise have to account for every literal '{'/'}'
appearing inside this file's plain-JS string-concatenation prompt text --
these templates deliberately avoid backtick template literals for
exactly this reason, per mass-translate-wf.template.js's own header
comment). Likewise, every `agent()` call site in both templates is
formatted as a multi-line `agent(promptBuilderCall, {\\n ...options...\\n
})` block whose options object is FLAT (no options object anywhere in
either template nests a '{'/'}'), so a non-greedy regex up to the first
closing '}' is exact.
"""
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
MASS_TRANSLATE_SRC = TEMPLATES_DIR / "mass-translate-wf.template.js"
GLOSSARY_SRC = TEMPLATES_DIR / "glossary-pass-wf.template.js"

for _p in (MASS_TRANSLATE_SRC, GLOSSARY_SRC):
    assert _p.is_file(), f"expected plugin template not found: {_p}"

MASS_TRANSLATE_SOURCE = MASS_TRANSLATE_SRC.read_text(encoding="utf-8")
GLOSSARY_SOURCE = GLOSSARY_SRC.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Text-extraction helpers
# ---------------------------------------------------------------------------

_TOP_LEVEL_FUNC_RE = re.compile(r"^(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE)


def extract_function_body(source, name):
    """Slice one top-level `function name(...) {...}` (or `async
    function`) declaration's full text, from its own declaration line up
    to (but not including) the NEXT top-level function declaration in the
    file, or EOF. See module docstring's 'Text-extraction approach' for
    why a line-boundary slice is exact here without brace-depth matching."""
    pattern = re.compile(rf"^(?:async\s+)?function\s+{re.escape(name)}\s*\(", re.MULTILINE)
    m = pattern.search(source)
    assert m is not None, f"function {name!r} not found in template source"
    start = m.start()
    m2 = _TOP_LEVEL_FUNC_RE.search(source, m.end())
    end = m2.start() if m2 else len(source)
    return source[start:end]


def extract_agent_call_options(body, prompt_builder_call_prefix):
    """Within `body`, find `agent(<prompt_builder_call_prefix>...), {
    ...options... })` and return the OPTIONS-object text verbatim (the
    agent() call's second positional argument)."""
    pattern = re.compile(
        re.escape("agent(") + re.escape(prompt_builder_call_prefix) +
        r"[^)]*\)\s*,\s*\{(.*?)\}\s*\)",
        re.DOTALL,
    )
    m = pattern.search(body)
    assert m is not None, (
        f"could not find an agent({prompt_builder_call_prefix}...) call "
        f"site in:\n{body[:400]}"
    )
    return m.group(1)


_AGENT_CALL_RE = re.compile(
    r"agent\(\s*([A-Za-z_][A-Za-z0-9_]*)\([^)]*\)\s*,\s*\{(.*?)\}\s*\)",
    re.DOTALL,
)


def find_all_agent_calls(source):
    """Every `agent(somePromptBuilder(...), {...options...})` call site in
    `source`, as (prompt_builder_name, options_text) pairs, in textual
    (program) order."""
    return [(m.group(1), m.group(2)) for m in _AGENT_CALL_RE.finditer(source)]


def is_codex_dispatch(options_text):
    return re.search(r'agentType\s*:\s*"codex[^"]*"', options_text) is not None


def has_schema(options_text):
    return re.search(r"\bschema\s*:", options_text) is not None


def has_bounded_poll_loop(body):
    return re.search(r"for\s+i\s+in\s+\$\(seq\s+1\s+\d+\)", body) is not None


# ---------------------------------------------------------------------------
# Regression-catcher: prove the helpers above actually discriminate, on
# synthetic fixtures, before trusting them against the real templates below.
# ---------------------------------------------------------------------------

def test_regression_catcher_helpers_actually_discriminate():
    codex_opts = 'agentType: "codex:codex-rescue", effort: "high", phase: "Translate", label: "x"'
    claude_opts = 'effort: "low", phase: "ReviewFix", label: "y"'
    assert is_codex_dispatch(codex_opts) is True
    assert is_codex_dispatch(claude_opts) is False

    assert has_schema('schema: REVIEW_SCHEMA, effort: "low"') is True
    assert has_schema('effort: "low", phase: "Ledger"') is False

    assert has_bounded_poll_loop("for i in $(seq 1 45); do true; done") is True
    assert has_bounded_poll_loop('return await agent(fixPrompt(seg, round, revObj), {});') is False

    synthetic = (
        "function alpha(x) {\n  return x + 1;\n}\n\n"
        "async function beta(y) {\n  return y + 2;\n}\n"
    )
    alpha_body = extract_function_body(synthetic, "alpha")
    assert "return x + 1;" in alpha_body
    assert "return y + 2;" not in alpha_body
    beta_body = extract_function_body(synthetic, "beta")
    assert "return y + 2;" in beta_body

    with pytest.raises(AssertionError):
        extract_function_body(synthetic, "does_not_exist")


# ---------------------------------------------------------------------------
# translate pair -- POSITIVE CONTROL, already bounded pre-1.2.0.
# ---------------------------------------------------------------------------

def test_translate_dispatch_is_codex_and_schema_less():
    body = extract_function_body(MASS_TRANSLATE_SOURCE, "translateStage")
    options = extract_agent_call_options(body, "translatePrompt(")
    assert is_codex_dispatch(options), f"translate dispatch must be codex-pinned: {options}"
    assert not has_schema(options), f"translate dispatch must be schema-less (fire-and-forget): {options}"


def test_translate_wait_is_a_bounded_poll_of_draft_ready():
    wait_body = extract_function_body(MASS_TRANSLATE_SOURCE, "waitPrompt")
    assert has_bounded_poll_loop(wait_body), (
        f"waitPrompt must contain a bounded `for i in $(seq 1 N)` poll:\n{wait_body}"
    )
    assert "draft_ready.py" in wait_body

    wrapper = extract_function_body(MASS_TRANSLATE_SOURCE, "reviewFixLoop")
    wait_call_options = extract_agent_call_options(wrapper, "waitPrompt(")
    assert not is_codex_dispatch(wait_call_options), (
        f"the wait POLL must be a Claude call (no agentType), got: {wait_call_options}"
    )


# ---------------------------------------------------------------------------
# review pair -- NEW in 1.2.0 (#97 restructure).
# ---------------------------------------------------------------------------

def test_review_dispatch_is_codex_and_schema_less():
    body = extract_function_body(MASS_TRANSLATE_SOURCE, "callReviewDispatch")
    options = extract_agent_call_options(body, "reviewDispatchPrompt(")
    assert is_codex_dispatch(options), f"review dispatch must be codex-pinned: {options}"
    assert not has_schema(options), f"review dispatch must be schema-less (fire-and-forget): {options}"


def test_review_wait_is_a_bounded_poll_of_review_ready():
    wait_body = extract_function_body(MASS_TRANSLATE_SOURCE, "reviewWaitPrompt")
    assert has_bounded_poll_loop(wait_body), (
        f"reviewWaitPrompt must contain a bounded `for i in $(seq 1 N)` poll:\n{wait_body}"
    )
    assert "review_ready.py" in wait_body

    wrapper = extract_function_body(MASS_TRANSLATE_SOURCE, "getVerifiedReview")
    wait_call_options = extract_agent_call_options(wrapper, "reviewWaitPrompt(")
    assert not is_codex_dispatch(wait_call_options), (
        f"the wait POLL must be a Claude call (no agentType), got: {wait_call_options}"
    )


# ---------------------------------------------------------------------------
# glossary batch pair -- NEW in 1.2.0 (#97 restructure, glossary side).
# ---------------------------------------------------------------------------

def test_glossary_batch_dispatch_is_codex_and_schema_less():
    body = extract_function_body(GLOSSARY_SOURCE, "batchStep")
    options = extract_agent_call_options(body, "batchDispatchPrompt(")
    assert is_codex_dispatch(options), f"glossary batch dispatch must be codex-pinned: {options}"
    assert not has_schema(options), f"glossary batch dispatch must be schema-less (fire-and-forget): {options}"


def test_glossary_batch_wait_is_a_bounded_poll_of_check_batch():
    wait_body = extract_function_body(GLOSSARY_SOURCE, "batchWaitPrompt")
    assert has_bounded_poll_loop(wait_body), (
        f"batchWaitPrompt must contain a bounded `for i in $(seq 1 N)` poll:\n{wait_body}"
    )
    assert "canon_validate.py" in wait_body and "--check-batch" in wait_body

    wrapper = extract_function_body(GLOSSARY_SOURCE, "batchStep")
    wait_call_options = extract_agent_call_options(wrapper, "batchWaitPrompt(")
    assert not is_codex_dispatch(wait_call_options), (
        f"the wait POLL must be a Claude call (no agentType), got: {wait_call_options}"
    )


# ---------------------------------------------------------------------------
# EXEMPTION positive control: callFix/fixPrompt.
# ---------------------------------------------------------------------------

def test_callfix_is_exempt_from_bounded_poll_requirement():
    """CONTRACT §8: 'Keep callFix/fixPrompt as-is'. Proves this file's
    pattern-matching genuinely discriminates on agentType=codex, rather
    than flagging every bare `await agent(...)` call site as needing a
    poll companion -- callFix's own dispatch has no agentType (a plain
    Claude call) and fixPrompt's own body deliberately has no poll loop."""
    body = extract_function_body(MASS_TRANSLATE_SOURCE, "callFix")
    options = extract_agent_call_options(body, "fixPrompt(")
    assert "agentType" not in options, (
        f"callFix must remain a plain Claude call with no agentType: {options}"
    )

    fix_prompt_body = extract_function_body(MASS_TRANSLATE_SOURCE, "fixPrompt")
    assert not has_bounded_poll_loop(fix_prompt_body), (
        "fixPrompt must NOT itself contain a bounded poll loop -- it is a "
        "direct, unbounded, blocking Claude call, deliberately NOT "
        "restructured by the #97 fix (see this file's module docstring)"
    )


# ---------------------------------------------------------------------------
# Comprehensive sweep -- every codex-agentType agent() call site found
# ANYWHERE in either template (not just the three named pairs above) is
# schema-less, and the exact SET of codex call sites is exactly the
# expected two (mass-translate) / one (glossary) -- a regression lock
# against a future codex call site being added without this file noticing.
# ---------------------------------------------------------------------------

def test_mass_translate_codex_dispatch_set_is_exactly_translate_and_review():
    calls = find_all_agent_calls(MASS_TRANSLATE_SOURCE)
    codex_builders = {name for name, opts in calls if is_codex_dispatch(opts)}
    assert codex_builders == {"translatePrompt", "reviewDispatchPrompt"}, (
        f"expected exactly the translate+review codex work-calls in "
        f"mass-translate-wf.template.js, got {codex_builders}"
    )


def test_glossary_codex_dispatch_set_is_exactly_batch_dispatch():
    calls = find_all_agent_calls(GLOSSARY_SOURCE)
    codex_builders = {name for name, opts in calls if is_codex_dispatch(opts)}
    assert codex_builders == {"batchDispatchPrompt"}, (
        f"expected exactly the glossary batch codex work-call in "
        f"glossary-pass-wf.template.js, got {codex_builders}"
    )


@pytest.mark.parametrize(
    "source,label",
    [(MASS_TRANSLATE_SOURCE, "mass-translate-wf.template.js"), (GLOSSARY_SOURCE, "glossary-pass-wf.template.js")],
)
def test_every_codex_dispatch_in_file_is_schema_less(source, label):
    calls = find_all_agent_calls(source)
    offenders = [name for name, opts in calls if is_codex_dispatch(opts) and has_schema(opts)]
    assert not offenders, (
        f"{label}: codex dispatch(es) unexpectedly carry a schema (must be "
        f"fire-and-forget, CONTRACT §7 step 1): {offenders}"
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
