"""tests/bounded_poll_present.test.py -- regression-lock across two eras:

  1. the #97/1.2.0 reliability fix (review + glossary-batch each gained
     translate's dispatch-then-bounded-poll discipline), and
  2. the #198/1.4.7 fix (W5 mass-translate translate AND review dispatch
     stop backgrounding codex from a Workflow agent turn -- they now DRIVE
     the DETACHED codex_job.py driver via a plain-Claude dispatcher).

GLOSSARY is deliberately UNCHANGED by #198 (glossary-pass-wf.template.js
still dispatches codex fire-and-forget); only mass-translate's translate/
review pairs move to the driver model.

## mass-translate (#198): the driver-dispatch model this file now locks

Each of translate and review is a THREE-piece shape:

  * a plain-Claude DISPATCHER call site (translateStage / callReviewDispatch)
    -- `agent(<drive-prompt>, {...})` with NO `agentType` (never a codex
    dispatch) and NO `schema`, whose return is parsed ONLY to capture the
    per-dispatch DISP nonce via `parseDisp` (anchored grammar);
  * a DRIVE prompt-builder (translateDrivePrompt / reviewDrivePrompt) whose
    generated bash generates DISP, writes the codex task-file, and launches
    `codex_job.py` DETACHED (`nohup ... </dev/null >/dev/null 2>&1 &`, NO
    `setsid`, NO external `timeout` binary), returning `DISPATCHED <seg>
    <DISP>` immediately (codex writes disk, its return is not the verdict);
  * a WAIT prompt-builder (waitPrompt / reviewWaitPrompt) whose generated
    bash is an ELAPSED-TIME poll (`end=$((SECONDS + WAIT_BOUND_SEC))`, NOT
    the old `for i in $(seq 1 N)` loop) that ACCEPTs by re-validating the
    CANONICAL directly (translate: draft_ready.py --expect-token AND
    validate_draft.py; review: review_ready.py --expect-token), whose OWN
    `agent()` call site (in reviewFixLoop / getVerifiedReview) is a plain
    Claude call (no agentType).

On origin/main (old fire-and-forget shape) the mass-translate assertions
below FAIL -- there is no translateDrivePrompt/reviewDrivePrompt, the
dispatch call sites carry `agentType: "codex..."`, and the wait polls are
`for i in $(seq 1 45)` loops -- so this file is a genuine RED-before-green
regression-catcher for #198. The glossary + callFix cases stay GREEN
regardless (unchanged by #198), acting as positive controls that this
file's pattern-matching still discriminates.

## Text-extraction approach (unchanged from the 1.2.0 file)

Every prompt-builder / call-wrapper in both templates is a FLAT top-level
`function name(...) { ... }` (or `async function`) declaration, so
`extract_function_body()` slices a function's full text by LINE BOUNDARY
(its own declaration to the next top-level function declaration), never by
brace-depth counting (these templates avoid backtick template literals for
exactly this reason). Every `agent()` call site is a multi-line
`agent(promptBuilderCall, {\\n ...options...\\n })` block whose options
object is FLAT, so a non-greedy regex up to the first closing '}' is exact.
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
    file, or EOF. The slice starts at `function` -- the LEADING comment is
    excluded; a TRAILING comment (the next function's own lead comment) is
    included, so negative string checks below target a specific extracted
    LINE, never the whole slice."""
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


def has_seq_poll_loop(body):
    """The OLD `for i in $(seq 1 N)` bounded loop -- glossary still uses it;
    #198's mass-translate wait polls must NOT."""
    return re.search(r"for\s+i\s+in\s+\$\(seq\s+1\s+\d+\)", body) is not None


def has_elapsed_poll_loop(body):
    """#198's elapsed-time poll: `end=$((SECONDS + <bound>)); while true; ...`."""
    return re.search(r"end=\$\(\(SECONDS\s*\+", body) is not None and "while true" in body


def line_containing(body, needle):
    """The single source LINE of `body` that contains `needle` (asserts
    exactly one -- so negative substring checks below target that precise
    line, not an incidental mention in a neighbouring comment)."""
    hits = [ln for ln in body.splitlines() if needle in ln]
    assert len(hits) == 1, (
        f"expected exactly one line containing {needle!r}, found {len(hits)}:\n"
        + "\n".join(hits[:6])
    )
    return hits[0]


# Numeric driver-timing consts, read straight off the template so the
# "elapsed bound >= CODEX_DEADLINE_SEC" check is verified against the real
# declared values, never a hardcoded guess.
def _int_const(source, name):
    m = re.search(rf"^const\s+{re.escape(name)}\s*=\s*(\d+)\s*;", source, re.MULTILINE)
    assert m is not None, f"expected `const {name} = <int>;` in template source"
    return int(m.group(1))


def resolved_wait_bound(source):
    """WAIT_BOUND_SEC is declared as the SUM of the three timing consts --
    recompute it here from their real declared values."""
    return (
        _int_const(source, "CODEX_DEADLINE_SEC")
        + _int_const(source, "CODEX_FINALIZE_BUDGET_SEC")
        + _int_const(source, "CODEX_WAIT_GRACE_SEC")
    )


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

    assert has_seq_poll_loop("for i in $(seq 1 45); do true; done") is True
    assert has_seq_poll_loop("end=$((SECONDS + 3450)); while true; do true; done") is False

    assert has_elapsed_poll_loop("end=$((SECONDS + 3450)); while true; do true; done") is True
    assert has_elapsed_poll_loop("for i in $(seq 1 45); do true; done") is False

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

    assert line_containing("a foo b\nc bar d", "foo") == "a foo b"
    with pytest.raises(AssertionError):
        line_containing("foo\nfoo", "foo")


# ---------------------------------------------------------------------------
# #198 -- parseDisp anchored grammar (HIGH-3). The captured DISP is spliced
# into the wait command's shell path, so it MUST be validated in JS first.
# ---------------------------------------------------------------------------

def test_parse_disp_uses_anchored_exact_grammar():
    body = extract_function_body(MASS_TRANSLATE_SOURCE, "parseDisp")
    # Whole-return anchor + capture restricted to the DISP generator alphabet
    # (uuidgen hex+hyphens / $RANDOM digits).
    assert "^DISPATCHED " in body, "parseDisp must anchor the whole return on ^DISPATCHED"
    assert "([0-9A-Fa-f][0-9A-Fa-f-]*)$" in body, (
        "parseDisp's capture group must be restricted to the shell-safe DISP "
        "alphabet and anchored at end-of-input"
    )
    assert "escapeRegExp(seg)" in body, "the expected seg must be regex-escaped into the anchor"


def test_segs_uniqueness_guard_present_before_pipeline():
    """#198 (BLOCKER r10) source-lock, complementing the behavioural throw
    test in mass_translate_driver_smoke.test.py: the Set-based duplicate-seg
    throw sits AFTER the SEG_ID_RE syntax loop and BEFORE `pipeline(`."""
    guard = "duplicate segment id"
    assert guard in MASS_TRANSLATE_SOURCE, "the SEGS uniqueness guard throw must be present"
    guard_pos = MASS_TRANSLATE_SOURCE.index(guard)
    seg_id_re_pos = MASS_TRANSLATE_SOURCE.index("const SEG_ID_RE =")
    pipeline_pos = MASS_TRANSLATE_SOURCE.index("await pipeline(SEGS")
    assert seg_id_re_pos < guard_pos < pipeline_pos, (
        "the uniqueness guard must sit after the SEG_ID_RE syntax loop and "
        "before pipeline(SEGS, ...)"
    )
    assert "new Set()" in MASS_TRANSLATE_SOURCE, "the guard must be Set-based"


# ---------------------------------------------------------------------------
# mass-translate TRANSLATE pair (#198) -- driver dispatch + elapsed poll.
# ---------------------------------------------------------------------------

def test_translate_dispatch_is_plain_claude_drive_no_codex_no_schema():
    body = extract_function_body(MASS_TRANSLATE_SOURCE, "translateStage")
    options = extract_agent_call_options(body, "translateDrivePrompt(")
    assert not is_codex_dispatch(options), (
        f"#198: translate dispatch must be a plain-Claude DRIVE (no agentType), got: {options}"
    )
    assert not has_schema(options), f"translate drive must be schema-less: {options}"
    assert "parseDisp(" in body, (
        "translateStage must parse the DISPATCHED <seg> <DISP> return via parseDisp"
    )


def test_translate_drive_prompt_launches_detached_codex_job():
    body = extract_function_body(MASS_TRANSLATE_SOURCE, "translateDrivePrompt")
    assert "DISP=$(uuidgen" in body, "drive prompt must generate a per-dispatch DISP nonce"
    assert 'echo "DISPATCHED ' in body or "DISPATCHED " in body, (
        "drive prompt must echo/return DISPATCHED <seg> <DISP>"
    )
    launch = line_containing(body, "codex_job.py --kind translate")
    assert "nohup " in launch, "the driver must be launched DETACHED via nohup"
    assert "--companion '" in launch and "COMPANION" in launch, (
        "COMPANION must be spliced as a SINGLE-QUOTED bash argument"
    )
    assert "--disp " in launch, "the launch must pass --disp"
    assert "</dev/null >/dev/null 2>&1 &" in launch, "the launch must fully detach and background"
    assert "setsid" not in launch, "no setsid (stock macOS lacks it)"
    assert "timeout" not in launch and "gtimeout" not in launch, "no external timeout binary"


def test_translate_wait_is_elapsed_canonical_gate_poll():
    wait_body = extract_function_body(MASS_TRANSLATE_SOURCE, "waitPrompt")
    assert has_elapsed_poll_loop(wait_body), (
        f"#198: waitPrompt must be an elapsed-time poll, not a seq loop:\n{wait_body}"
    )
    assert not has_seq_poll_loop(wait_body), "the old `for i in $(seq 1 N)` loop must be gone"

    poll = line_containing(wait_body, "end=$((SECONDS +")
    assert "draft_ready.py" in poll and "--expect-token" in poll, (
        "translate ACCEPT must run draft_ready.py --expect-token on the canonical"
    )
    assert "validate_draft.py" in poll, (
        "translate ACCEPT must ALSO run validate_draft.py (the six quality checks)"
    )
    assert "[ $SECONDS -ge $end ] && break" in poll, "gate-then-deadline-break inside the loop"
    assert "timeout" not in poll and "gtimeout" not in poll, "no external timeout binary in the poll"
    assert "WAIT_BOUND_SEC" in poll, "the elapsed bound must be the WAIT_BOUND_SEC const"
    # fail-fast is the DISP-named sentinel, present in the body's failFast const
    assert ".codex_failed." in wait_body, "the fail-fast sentinel presence check must be present"

    # the wait POLL's own agent() call site (in reviewFixLoop) is a plain Claude call
    wrapper = extract_function_body(MASS_TRANSLATE_SOURCE, "reviewFixLoop")
    wait_call_options = extract_agent_call_options(wrapper, "waitPrompt(")
    assert not is_codex_dispatch(wait_call_options), (
        f"the wait POLL must be a Claude call (no agentType), got: {wait_call_options}"
    )


# ---------------------------------------------------------------------------
# mass-translate REVIEW pair (#198) -- driver dispatch + elapsed poll.
# ---------------------------------------------------------------------------

def test_review_dispatch_is_plain_claude_drive_no_codex_no_schema():
    body = extract_function_body(MASS_TRANSLATE_SOURCE, "callReviewDispatch")
    options = extract_agent_call_options(body, "reviewDrivePrompt(")
    assert not is_codex_dispatch(options), (
        f"#198: review dispatch must be a plain-Claude DRIVE (no agentType), got: {options}"
    )
    assert not has_schema(options), f"review drive must be schema-less: {options}"
    assert "parseDisp(" in body, (
        "callReviewDispatch must parse the DISPATCHED <seg> <DISP> return via parseDisp"
    )


def test_review_drive_prompt_launches_detached_codex_job():
    body = extract_function_body(MASS_TRANSLATE_SOURCE, "reviewDrivePrompt")
    assert "DISP=$(uuidgen" in body, "drive prompt must generate a per-dispatch DISP nonce"
    launch = line_containing(body, "codex_job.py --kind review")
    assert "nohup " in launch, "the driver must be launched DETACHED via nohup"
    assert "--companion '" in launch and "COMPANION" in launch, (
        "COMPANION must be spliced as a SINGLE-QUOTED bash argument"
    )
    assert "--disp " in launch, "the launch must pass --disp"
    assert "</dev/null >/dev/null 2>&1 &" in launch, "the launch must fully detach and background"
    assert "setsid" not in launch, "no setsid"
    assert "timeout" not in launch and "gtimeout" not in launch, "no external timeout binary"


def test_review_wait_is_elapsed_canonical_gate_poll():
    wait_body = extract_function_body(MASS_TRANSLATE_SOURCE, "reviewWaitPrompt")
    assert has_elapsed_poll_loop(wait_body), (
        f"#198: reviewWaitPrompt must be an elapsed-time poll, not a seq loop:\n{wait_body}"
    )
    assert not has_seq_poll_loop(wait_body), "the old `for i in $(seq 1 N)` loop must be gone"

    poll = line_containing(wait_body, "end=$((SECONDS +")
    assert "review_ready.py" in poll and "--expect-token" in poll, (
        "review ACCEPT must run review_ready.py --expect-token on the canonical"
    )
    assert "[ $SECONDS -ge $end ] && break" in poll, "gate-then-deadline-break inside the loop"
    assert "timeout" not in poll and "gtimeout" not in poll, "no external timeout binary in the poll"
    assert "WAIT_BOUND_SEC" in poll, "the elapsed bound must be the WAIT_BOUND_SEC const"
    assert ".codex_failed." in wait_body, "the fail-fast sentinel presence check must be present"

    wrapper = extract_function_body(MASS_TRANSLATE_SOURCE, "getVerifiedReview")
    wait_call_options = extract_agent_call_options(wrapper, "reviewWaitPrompt(")
    assert not is_codex_dispatch(wait_call_options), (
        f"the wait POLL must be a Claude call (no agentType), got: {wait_call_options}"
    )


def test_mass_translate_wait_bound_is_at_least_the_codex_deadline():
    """The elapsed bound WAIT_BOUND_SEC = DEADLINE + FINALIZE_BUDGET +
    WAIT_GRACE, so the Workflow poll never gives up before the driver can
    promote/finalize -- must be >= CODEX_DEADLINE_SEC."""
    bound = resolved_wait_bound(MASS_TRANSLATE_SOURCE)
    deadline = _int_const(MASS_TRANSLATE_SOURCE, "CODEX_DEADLINE_SEC")
    assert bound >= deadline, f"WAIT_BOUND_SEC ({bound}) must be >= CODEX_DEADLINE_SEC ({deadline})"
    # WAIT_BOUND_SEC itself is declared as the additive expression (never a
    # stale magic literal that could drift below the deadline).
    assert re.search(
        r"const\s+WAIT_BOUND_SEC\s*=\s*CODEX_DEADLINE_SEC\s*\+\s*"
        r"CODEX_FINALIZE_BUDGET_SEC\s*\+\s*CODEX_WAIT_GRACE_SEC\s*;",
        MASS_TRANSLATE_SOURCE,
    ), "WAIT_BOUND_SEC must be the additive expression, not a hardcoded number"


# ---------------------------------------------------------------------------
# glossary batch pair -- UNCHANGED by #198 (still codex fire-and-forget).
# ---------------------------------------------------------------------------

def test_glossary_batch_dispatch_is_codex_and_schema_less():
    body = extract_function_body(GLOSSARY_SOURCE, "batchStep")
    options = extract_agent_call_options(body, "batchDispatchPrompt(")
    assert is_codex_dispatch(options), f"glossary batch dispatch must be codex-pinned: {options}"
    assert not has_schema(options), f"glossary batch dispatch must be schema-less (fire-and-forget): {options}"


def test_glossary_batch_wait_is_a_bounded_poll_of_check_batch():
    wait_body = extract_function_body(GLOSSARY_SOURCE, "batchWaitPrompt")
    assert has_seq_poll_loop(wait_body), (
        f"batchWaitPrompt must contain a bounded `for i in $(seq 1 N)` poll:\n{wait_body}"
    )
    assert "canon_validate.py" in wait_body and "--check-batch" in wait_body

    wrapper = extract_function_body(GLOSSARY_SOURCE, "batchStep")
    wait_call_options = extract_agent_call_options(wrapper, "batchWaitPrompt(")
    assert not is_codex_dispatch(wait_call_options), (
        f"the wait POLL must be a Claude call (no agentType), got: {wait_call_options}"
    )


# ---------------------------------------------------------------------------
# EXEMPTION positive control: callFix/fixPrompt (unchanged by #97 AND #198).
# ---------------------------------------------------------------------------

def test_callfix_is_exempt_from_bounded_poll_requirement():
    """CONTRACT §8: 'Keep callFix/fixPrompt as-is'. callFix's dispatch has no
    agentType (a plain, unbounded, blocking Claude call) and fixPrompt's body
    deliberately has no poll loop -- a forward-detached job can't happen on a
    Claude call, and a sha-changed readiness gate would false-time-out a
    no-op fix. Proves this file discriminates on agentType, not by flagging
    every bare `await agent(...)`."""
    body = extract_function_body(MASS_TRANSLATE_SOURCE, "callFix")
    options = extract_agent_call_options(body, "fixPrompt(")
    assert "agentType" not in options, (
        f"callFix must remain a plain Claude call with no agentType: {options}"
    )

    fix_prompt_body = extract_function_body(MASS_TRANSLATE_SOURCE, "fixPrompt")
    assert not has_seq_poll_loop(fix_prompt_body) and not has_elapsed_poll_loop(fix_prompt_body), (
        "fixPrompt must NOT itself contain a poll loop -- it is a direct, "
        "unbounded, blocking Claude call, deliberately NOT restructured"
    )


# ---------------------------------------------------------------------------
# Comprehensive sweep -- #198 makes mass-translate carry ZERO codex-agentType
# dispatches (all codex work goes through the detached driver); glossary
# still has exactly its one batch dispatch. A regression lock against a
# future codex-agentType dispatch being re-introduced into mass-translate.
# ---------------------------------------------------------------------------

def test_mass_translate_has_no_codex_agenttype_dispatches():
    calls = find_all_agent_calls(MASS_TRANSLATE_SOURCE)
    codex_builders = {name for name, opts in calls if is_codex_dispatch(opts)}
    assert codex_builders == set(), (
        f"#198: mass-translate-wf.template.js must carry NO codex-agentType "
        f"agent() dispatches (translate/review now DRIVE the detached "
        f"codex_job.py driver), got {codex_builders}"
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
