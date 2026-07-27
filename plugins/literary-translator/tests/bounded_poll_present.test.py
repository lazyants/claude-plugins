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
  * a WAIT, whose generated bash is an ELAPSED-TIME poll (`end=$((SECONDS +
    ...))`, NOT the old `for i in $(seq 1 N)` loop) that ACCEPTs by
    re-validating the CANONICAL directly (translate: draft_ready.py
    --expect-token AND validate_draft.py; review: review_ready.py
    --expect-token), whose OWN `agent()` call sites (in reviewFixLoop /
    getVerifiedReview) are plain Claude calls (no agentType).

## 1.16.1 (#348): the wait is CHUNKED, and this file follows the extraction

A wait was ONE agent call running ONE poll of the whole WAIT_BOUND_SEC. The
Bash tool clamps a single call at 600 000 ms regardless of the timeout asked
for, so long waits were killed and reported as timeouts with a clean artifact
sitting unread on disk. A wait is now up to WAIT_CHUNKS bounded chunk calls
followed by ONE authoritative non-polling re-check.

That moved code, so several checks below had to move with it -- re-expressed,
never dropped:

  * the poll's bash left waitPrompt / reviewWaitPrompt for the SHARED builders
    waitChunkPrompt / waitRecheckPromptFor. The #198 poll-shape assertions are
    therefore proved ONCE against the shared builder; asserting them against a
    wrapper that no longer contains any bash would assert nothing.
  * each site's ACCEPT command moved into translateAcceptCmd / reviewAcceptCmd,
    composed once and spliced by both the poll and the re-check. The per-site
    half becomes an anti-drift lock in glossary's checkBatchCmd idiom -- if the
    re-check could compose its own gate it could drift into a WEAKER one, and a
    re-check weaker than the poll it backs up is a false GREEN.
  * the wait-reply parse moved into waitChunkVerdict, the single parse site.
    1.16.0's per-call-site containment guard is re-expressed around it below.

tests/wait_chunking.test.py is the behavioural half of #348 (the real template
under Node); this file stays the source-shape half.

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


def code_lines(body):
    """`body` with every WHOLE-LINE `//` comment removed.

    Every assertion below that claims a function DOES something must run
    against this rather than against the raw slice. extract_function_body()
    deliberately keeps a TRAILING comment -- the NEXT function's own lead
    comment -- inside the previous function's slice, and these templates
    document each helper in prose right above its caller, so the raw slice
    routinely contains the very identifier the assertion is hunting for.
    `"checkBatchCmd(" in body` was satisfied for batchDispatchPrompt by the
    trailing comment "// checkBatchCmd() -- the same command DISPATCH's
    self-check issues"; the dispatch could drop its self-check entirely and
    the assertion still passed. A body that happens to have no trailing
    comment (batchPrecheckPrompt) hides that, which is exactly why the loop
    below now runs this over ALL THREE sites rather than trusting the one
    site that was proved by mutation.

    Only whole-line comments go: cutting each line at its first "//" would
    also cut inside a string literal, and these templates push
    natural-language prose that carries URLs."""
    return "\n".join(
        ln for ln in body.splitlines() if not ln.lstrip().startswith("//")
    )


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

    # code_lines() must drop a whole-line comment that MENTIONS the call while
    # keeping the call itself -- the precise discrimination the three-site
    # anti-drift lock below depends on.
    commented = (
        "function gamma(x) {\n"
        "  return helper(x);\n"
        "}\n"
        "   // helper() -- gamma() issues it (see that helper)\n"
    )
    assert "helper(" in commented  # ...the raw slice cannot tell the two apart
    stripped = code_lines(commented)
    assert "return helper(x);" in stripped
    assert "see that helper" not in stripped

    # The exact false-green shape: the ONLY occurrence is in the trailing
    # comment, so the raw slice says the call is there and code_lines() does not.
    comment_only = "function delta() {\n  return 1;\n}\n// delta() issues helper()\n"
    assert "helper(" in comment_only
    assert "helper(" not in code_lines(comment_only)
    # A string literal carrying "//" is code, not a comment, and must survive.
    assert code_lines('  push("see https://example.invalid/x");') == (
        '  push("see https://example.invalid/x");'
    )


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


# #348 -- the poll bash and the re-check bash each live in ONE shared builder
# now, spliced by both wait sites. Proved once here, then tied back to each site
# by the anti-drift lock further below.
WAIT_CHUNK_BUILDER = "waitChunkPrompt"
WAIT_RECHECK_BUILDER = "waitRecheckPromptFor"

# The gate commands as CODE. As with glossary's COMPOSED_CHECK_BATCH_LITERAL,
# the "/scripts/" path prefix is the tell that separates BUILDING a command from
# naming it in prose -- this template's prose says a bare "draft_ready.py".
GATE_SCRIPT_PATHS = (
    "/scripts/draft_ready.py",
    "/scripts/validate_draft.py",
    "/scripts/review_ready.py",
)


def test_wait_chunk_poll_keeps_the_198_elapsed_gate_shape():
    """#198's poll grammar, unchanged by #348 except for the elapsed bound (this
    chunk's slice, not the whole run's) and the terminal markers. Proved on the
    shared builder, which is where the bash now lives."""
    body = code_lines(extract_function_body(MASS_TRANSLATE_SOURCE, WAIT_CHUNK_BUILDER))
    assert has_elapsed_poll_loop(body), (
        f"#198: the chunk poll must be an elapsed-time poll, not a seq loop:\n{body}"
    )
    assert not has_seq_poll_loop(body), "the old `for i in $(seq 1 N)` loop must be gone"

    poll = line_containing(body, "end=$((SECONDS +")

    # The ACCEPT gate arrives as the acceptCmd PARAMETER -- composed by
    # translateAcceptCmd/reviewAcceptCmd and locked below -- and the redirect
    # sits between it and `&& exit 0`. That redirect is LOAD-BEARING, not
    # tidiness: without it the gate prints one `{"ready": false, ...}` line per
    # iteration, so "the marker is the last line" would be a claim about the
    # tail of a noisy stream. Asserted as one contiguous string so a redirect
    # moved elsewhere on the line cannot satisfy it.
    assert '+ acceptCmd + " >/dev/null 2>&1 && exit 0;"' in poll, (
        f"the in-loop ACCEPT gate must be the spliced acceptCmd with its output "
        f"suppressed immediately before `&& exit 0`, got: {poll}"
    )
    assert "[ $SECONDS -ge $end ] && break" in poll, "gate-then-deadline-break inside the loop"
    assert "[ $slp -gt 20 ] && slp=20" in poll, "the sleep must stay clamped"

    # ORDER: gate, then the deadline break, then the clamped sleep. A break
    # ahead of the gate would skip the final evaluation at the bound.
    accept_at = poll.index("+ acceptCmd +")
    break_at = poll.index("[ $SECONDS -ge $end ] && break")
    sleep_at = poll.index("sleep $slp")
    assert accept_at < break_at < sleep_at, (
        f"the chunk poll's gate -> deadline-break -> clamped-sleep order is broken: {poll}"
    )

    # NO separate post-loop gate INSIDE the chunk command, so exactly one gate
    # straddles this chunk's deadline (#198's rule, unchanged).
    tail = poll[poll.index("done;"):]
    assert "acceptCmd" not in tail and not any(p in tail for p in GATE_SCRIPT_PATHS), (
        f"the chunk command runs a second gate after the loop; #198 allows exactly "
        f"one gate straddling the deadline, got tail: {tail}"
    )
    assert "LT_CHUNK_BOUND" in tail, "an exhausted chunk must print its bound marker"

    assert "timeout" not in poll and "gtimeout" not in poll, "no external timeout binary in the poll"
    # fail-fast is the DISP-named sentinel, present in the body's failFast const
    assert ".codex_failed." in body, "the fail-fast sentinel presence check must be present"

    # The elapsed bound is this chunk's own slice, never a literal -- and
    # waitChunkSec() derives it from WAIT_BOUND_SEC, so the poll's bound still
    # traces back to the same const the pre-#348 one-shot poll named directly.
    assert '"end=$((SECONDS + " + waitChunkSec(chunkIndex) + ")); while true; do "' in poll, (
        f"the chunk's elapsed bound must be waitChunkSec(chunkIndex), got: {poll}"
    )
    chunk_sec_body = code_lines(extract_function_body(MASS_TRANSLATE_SOURCE, "waitChunkSec"))
    assert "WAIT_BOUND_SEC" in chunk_sec_body and "WAIT_CHUNK_SEC" in chunk_sec_body, (
        f"waitChunkSec must derive each chunk's bound from WAIT_BOUND_SEC and "
        f"WAIT_CHUNK_SEC, never from a fresh literal: {chunk_sec_body}"
    )


def test_wait_recheck_is_a_single_non_polling_gate_evaluation():
    """#348's actual fix. A polling re-check would just be a ninth chunk and
    could itself hit the per-call cap, so this one must have no loop at all --
    and it must run the gate it was handed, not one of its own."""
    body = code_lines(extract_function_body(MASS_TRANSLATE_SOURCE, WAIT_RECHECK_BUILDER))
    assert not has_elapsed_poll_loop(body), f"the re-check polls:\n{body}"
    assert not has_seq_poll_loop(body), f"the re-check polls:\n{body}"
    assert "while true" not in body, f"the re-check loops:\n{body}"
    assert "sleep" not in body, f"the re-check sleeps:\n{body}"
    gate = line_containing(body, "acceptCmd +")
    assert 'acceptCmd + " >/dev/null 2>&1"' in gate, (
        f"the re-check must run the spliced acceptCmd with its output suppressed, got: {gate}"
    )


def test_translate_accept_gate_revalidates_the_canonical_draft():
    """The translate ACCEPT gate's content, at its single composition site."""
    body = code_lines(extract_function_body(MASS_TRANSLATE_SOURCE, "translateAcceptCmd"))
    assert "draft_ready.py" in body and "--expect-token" in body, (
        "translate ACCEPT must run draft_ready.py --expect-token on the canonical"
    )
    assert "validate_draft.py" in body, (
        "translate ACCEPT must ALSO run validate_draft.py (the six quality checks)"
    )
    assert "timeout" not in body and "gtimeout" not in body, "no external timeout binary"


def test_review_accept_gate_revalidates_the_canonical_review():
    body = code_lines(extract_function_body(MASS_TRANSLATE_SOURCE, "reviewAcceptCmd"))
    assert "review_ready.py" in body and "--expect-token" in body, (
        "review ACCEPT must run review_ready.py --expect-token on the canonical"
    )
    assert "timeout" not in body and "gtimeout" not in body, "no external timeout binary"


# Every wait-path prompt builder, with the shared bash builder and the ACCEPT
# composer it must go THROUGH. The re-check entries are the point: a re-check
# that composed its own gate could drift into a WEAKER one than the poll it
# backs up, and a re-check weaker than the poll is a false GREEN -- the one
# direction this pipeline cannot recover from.
WAIT_SITE_WIRING = [
    ("waitPrompt", WAIT_CHUNK_BUILDER, "translateAcceptCmd(seg)"),
    ("waitRecheckPrompt", WAIT_RECHECK_BUILDER, "translateAcceptCmd(seg)"),
    ("reviewWaitPrompt", WAIT_CHUNK_BUILDER, "reviewAcceptCmd(seg, roundLabel)"),
    ("reviewWaitRecheckPrompt", WAIT_RECHECK_BUILDER, "reviewAcceptCmd(seg, roundLabel)"),
]


@pytest.mark.parametrize("site,shared_builder,accept_call", WAIT_SITE_WIRING)
def test_wait_site_goes_through_the_shared_builder_and_its_accept_composer(
    site, shared_builder, accept_call
):
    """1.16.1 anti-drift lock, in glossary's checkBatchCmd idiom and for the same
    reason: the poll and the re-check of one kind must issue the ACCEPT command
    character-identically, an invariant otherwise stated only in prose.

    Over code_lines(), never the raw slice -- these builders sit under long
    comments that name both the shared builder and the gate scripts, and a
    raw-slice check would be satisfied by that prose alone (the exact tautology
    the checkBatchCmd lock below was found by mutation to have had)."""
    body = code_lines(extract_function_body(MASS_TRANSLATE_SOURCE, site))
    assert shared_builder + "(" in body, (
        f"{site} must build its bash via {shared_builder}(), so the two wait sites "
        f"cannot drift apart in poll shape -- and must CALL it in code, not merely "
        f"be documented as calling it in a neighbouring comment"
    )
    assert accept_call in body, (
        f"{site} must splice the shared {accept_call}, never retype the command"
    )
    for path in GATE_SCRIPT_PATHS:
        assert path not in body, (
            f"{site} composes a {path} command itself -- that is exactly the drift "
            f"translateAcceptCmd()/reviewAcceptCmd() exist to make impossible"
        )


@pytest.mark.parametrize("builder", [WAIT_CHUNK_BUILDER, WAIT_RECHECK_BUILDER])
def test_shared_wait_builder_never_composes_an_accept_command(builder):
    """The other half of the lock: the shared builders take the gate as a
    parameter and must not know any script path. Without this, a gate hardcoded
    in the shared builder would satisfy every per-site check above while
    ignoring what each site passed in."""
    body = code_lines(extract_function_body(MASS_TRANSLATE_SOURCE, builder))
    assert "acceptCmd" in body, f"{builder} must splice its acceptCmd parameter"
    for path in GATE_SCRIPT_PATHS:
        assert path not in body, (
            f"{builder} composes a {path} command itself instead of using the "
            f"acceptCmd its caller passed"
        )


def test_review_ready_gate_is_composed_in_exactly_one_place():
    """Scope, stated rather than assumed: review_ready.py is a WAIT-ONLY gate, so
    its composition count is a whole-file property. draft_ready.py and
    validate_draft.py deliberately are NOT -- the fixer (fixPrompt) and the draft
    probe (draftProbePrompt) each legitimately compose their own -- so for those
    two the single-composer lock is the per-site one above, not a file-wide
    count."""
    composed = code_lines(MASS_TRANSLATE_SOURCE).count("/scripts/review_ready.py")
    assert composed == 1, (
        f"the review ACCEPT command must be composed in exactly ONE place, found "
        f"{composed} composition site(s)"
    )
    assert "/scripts/review_ready.py" in code_lines(
        extract_function_body(MASS_TRANSLATE_SOURCE, "reviewAcceptCmd")
    ), "that single composition site must be reviewAcceptCmd itself"


# Both wait sites' agent() call sites, chunk AND re-check, are plain Claude calls
# (no agentType) -- the re-check entries are new in 1.16.1 and would otherwise be
# an unwatched place for a codex dispatch to reappear.
WAIT_CALL_SITES = [
    ("reviewFixLoop", "waitPrompt("),
    ("reviewFixLoop", "waitRecheckPrompt("),
    ("getVerifiedReview", "reviewWaitPrompt("),
    ("getVerifiedReview", "reviewWaitRecheckPrompt("),
]


@pytest.mark.parametrize("wrapper_name,builder_call", WAIT_CALL_SITES)
def test_wait_agent_call_site_is_a_plain_claude_call(wrapper_name, builder_call):
    wrapper = extract_function_body(MASS_TRANSLATE_SOURCE, wrapper_name)
    options = extract_agent_call_options(wrapper, builder_call)
    assert not is_codex_dispatch(options), (
        f"{wrapper_name}'s {builder_call} call must be a Claude call (no "
        f"agentType), got: {options}"
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


# The review WAIT had its own copy of the translate wait's assertions until
# 1.16.1. #348 made the two sites share one poll builder and one re-check
# builder, so a second copy would now be re-asserting the same lines of bash
# twice while proving nothing about the review site specifically. Every half of
# it survives, above, in the shape the code now has:
#   poll shape / fail-fast / no-timeout-binary / elapsed bound
#       -> test_wait_chunk_poll_keeps_the_198_elapsed_gate_shape (shared builder)
#   review_ready.py --expect-token as the ACCEPT gate
#       -> test_review_accept_gate_revalidates_the_canonical_review
#   reviewWaitPrompt/reviewWaitRecheckPrompt really USE that builder and that
#   gate, rather than composing either themselves
#       -> test_wait_site_goes_through_the_shared_builder_and_its_accept_composer
#   the wait agent() call sites are plain Claude calls
#       -> test_wait_agent_call_site_is_a_plain_claude_call


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


# The three sites that must issue the --check-batch command
# character-identically (see checkBatchCmd()'s own comment in the template):
# the resume precheck, the codex dispatch's own self-check, and the wait poll.
CHECK_BATCH_CALL_SITES = ("batchPrecheckPrompt", "batchDispatchPrompt", "batchWaitPrompt")

# The command as CODE. The "/scripts/" path prefix is the tell that separates
# BUILDING the command from merely naming it in prose -- the template's prose
# always says a bare "canon_validate.py --check-batch", never the script path.
COMPOSED_CHECK_BATCH_LITERAL = "/scripts/canon_validate.py --check-batch"


def test_glossary_batch_wait_is_a_bounded_poll_of_check_batch():
    wait_body = code_lines(extract_function_body(GLOSSARY_SOURCE, "batchWaitPrompt"))
    assert has_seq_poll_loop(wait_body), (
        f"batchWaitPrompt must contain a bounded `for i in $(seq 1 N)` poll:\n{wait_body}"
    )
    # 1.16.0 extracted checkBatchCmd(), so the command's own literals no longer
    # sit inline here -- follow that indirection rather than grepping this body
    # for them. The intent is unchanged and asserted as the full CHAIN: the
    # bounded poll runs the checkBatchCmd()-built command, and that command is
    # canon_validate.py --check-batch.
    assert "checkBatchCmd(batch.index, attempt)" in wait_body, (
        "batchWaitPrompt must build its poll command from checkBatchCmd(), "
        "scoped to THIS attempt's own fragment path"
    )
    poll = line_containing(wait_body, "for i in $(seq 1")
    # Word-anchored, not a bare substring: `"checkCmd" in poll` is satisfied by
    # any identifier that merely CONTAINS it, so a poll interpolating some
    # unrelated `checkCmdFallback` would have passed while running a command
    # this test never inspected.
    assert re.search(r"\bcheckCmd\b", poll), (
        f"the bounded poll must interpolate the checkBatchCmd()-built command "
        f"itself (the exact `checkCmd` binding, not merely an identifier "
        f"containing that name), got: {poll}"
    )
    cmd_line = line_containing(
        code_lines(extract_function_body(GLOSSARY_SOURCE, "checkBatchCmd")),
        "canon_validate.py",
    )
    assert "--check-batch" in cmd_line, (
        f"the command the wait polls must be canon_validate.py --check-batch, got: {cmd_line}"
    )

    wrapper = extract_function_body(GLOSSARY_SOURCE, "batchStep")
    wait_call_options = extract_agent_call_options(wrapper, "batchWaitPrompt(")
    assert not is_codex_dispatch(wait_call_options), (
        f"the wait POLL must be a Claude call (no agentType), got: {wait_call_options}"
    )


def test_check_batch_command_is_composed_once_and_shared_by_all_three_sites():
    """1.16.0 anti-drift lock -- the whole point of extracting checkBatchCmd().
    The three sites have to issue this command character-identically (the
    dispatch prompt literally tells the agent to re-run "exactly the command
    above"), an invariant previously stated only in prose comments and enforced
    nowhere. Lock both halves: the helper really IS the canon_validate.py
    --check-batch command, and every one of the three sites goes THROUGH it
    instead of composing the command itself.

    Every check here runs over code_lines(), never the raw slice. The prose
    version of this test was tautological at one of the three sites: the
    trailing comment carried into batchDispatchPrompt's slice ("//
    checkBatchCmd() -- the same command DISPATCH's self-check issues") satisfied
    `"checkBatchCmd(" in body` on its own, so replacing the dispatch's real
    self-check line with "Then stop. Do not self-check." left this test GREEN --
    verified by mutation. The lock had been proved on batchPrecheckPrompt, which
    happens to carry no trailing comment; that one site is not evidence about
    the other two, so the assertions below are proved separately at each."""
    check_cmd_body = code_lines(extract_function_body(GLOSSARY_SOURCE, "checkBatchCmd"))
    cmd_line = line_containing(check_cmd_body, "canon_validate.py")
    assert "--check-batch" in cmd_line, (
        f"checkBatchCmd must build the canon_validate.py --check-batch command, got: {cmd_line}"
    )

    composition_sites = code_lines(GLOSSARY_SOURCE).count(COMPOSED_CHECK_BATCH_LITERAL)
    assert composition_sites == 1, (
        f"the --check-batch command must be composed in exactly ONE place, "
        f"found {composition_sites} composition site(s)"
    )
    assert COMPOSED_CHECK_BATCH_LITERAL in check_cmd_body, (
        "that single composition site must be checkBatchCmd itself"
    )

    for name in CHECK_BATCH_CALL_SITES:
        body = code_lines(extract_function_body(GLOSSARY_SOURCE, name))
        assert "checkBatchCmd(" in body, (
            f"{name} must issue the --check-batch command via checkBatchCmd(), "
            f"never by composing it itself -- and must ISSUE it in code, not "
            f"merely be documented as issuing it in a neighbouring comment"
        )
        assert "/scripts/canon_validate.py" not in body, (
            f"{name} must not compose a canon_validate.py command itself -- that "
            f"is exactly the drift checkBatchCmd() exists to make impossible"
        )


# ---------------------------------------------------------------------------
# 1.16.0 containment guard -- EVERY sentinelVerdict call site is preceded by a
# rejectedAnywhere() check on the SAME reply and the SAME fail sentinel.
#
# sentinelVerdict() splits on LF only, so a fail sentinel glued to prose by any
# other character survives whole-line equality and the rejection trigger never
# fires; measured on the pre-guard template, 15 of 16 gluing characters over
# GLUE_CHARS in tests/glossary_citation_review.test.py, in the PROSE shape
# (prose shares the sentinel's line), made all three sites falsely approve --
# that file's containment-guard section drives it end to end. The shape and the
# set are both part of the number: the same characters give a different count in
# the no-prose shape. The guard is applied
# at the CALL SITES because sentinelVerdict() itself is mirrored byte-for-byte
# across the three workflow templates and pinned by
# tests/sentinel_verdict_parity.test.py.
#
# All three sites live inside one function (batchStep), so they cannot be proved
# by three separate function bodies the way the checkBatchCmd sites were. The
# invariant is expressed structurally instead: PAIR each sentinelVerdict call
# with a rejectedAnywhere call on the same two expressions. That catches a site
# left unguarded, a guard watching the wrong reply variable, and a guard
# checking a different sentinel than the one its sentinelVerdict call uses --
# none of which a bare "rejectedAnywhere appears 3 times" count would notice.
# ---------------------------------------------------------------------------

GUARD_HELPER = "rejectedAnywhere"

_SENTINEL_VERDICT_CALL_RE = re.compile(
    r"sentinelVerdict\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)"
)
_GUARD_CALL_RE = re.compile(
    re.escape(GUARD_HELPER) + r"\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)"
)


def _normalized_code(body):
    """`body`'s CODE with every whitespace run collapsed to one space, so a call
    that wraps across lines is matched the same as one that does not."""
    return re.sub(r"\s+", " ", code_lines(body))


def test_every_glossary_sentinel_verdict_call_site_is_containment_guarded():
    """The 1.16.0 false-approval fix, locked at all FOUR sites at once.

    Runs over code_lines(), never the raw slice: batchStep's own comments
    discuss both helpers by name at length, and a `GUARD_HELPER in body` check
    against the raw text would be satisfied by that prose alone -- the exact
    tautology the checkBatchCmd lock above was found to have.

    1.16.1 (#347): three sites became four. The single citation-review agent
    that both fetched and judged was split into a PREPARE agent (runs the
    validated fetcher, ingests no page content) and a JUDGE agent (reads local
    files, retrieves nothing), so prepare contributes its own
    EVIDENCE_READY/EVIDENCE_FAILED sentinel pair. The count moved rather than
    the assertion relaxing, exactly as this test's own failure message
    instructs -- and the count is not the protection. The pairing loop below is:
    it re-checks EVERY site, so raising the number is what lets the new site be
    verified at all, and a prepare site added without a guard fails there."""
    body = extract_function_body(GLOSSARY_SOURCE, "batchStep")
    code = _normalized_code(body)

    verdict_calls = _SENTINEL_VERDICT_CALL_RE.findall(code)
    assert len(verdict_calls) == 4, (
        f"expected batchStep to hold exactly the four sentinel sites (precheck, "
        f"wait, citation prepare, citation judge); found {len(verdict_calls)}: "
        f"{verdict_calls}. If a site was added or removed, guard it and update "
        f"this count -- do not relax the assertion"
    )

    guarded = {(reply, fail) for reply, fail in _GUARD_CALL_RE.findall(code)}
    for reply, ok_sentinel, fail_sentinel in verdict_calls:
        assert (reply, fail_sentinel) in guarded, (
            f"the sentinelVerdict call on {reply!r} (ok={ok_sentinel!r}, "
            f"fail={fail_sentinel!r}) is NOT preceded by a "
            f"{GUARD_HELPER}({reply}, {fail_sentinel}) containment check. Without "
            f"it, a fail sentinel glued to prose by any character other than a "
            f"newline survives whole-line equality and this site falsely "
            f"approves. Guards actually present: {sorted(guarded)}"
        )


# ---------------------------------------------------------------------------
# The same containment lock for mass-translate-wf.template.js -- re-expressed in
# 1.16.1 around #348's SINGLE PARSE SITE.
#
# Until 1.16.1 each wait site parsed its own reply, so the invariant was stated
# per call site: every sentinelVerdict call is preceded by a rejectedAnywhere()
# check on the SAME reply and the SAME fail sentinel. #348 centralised the
# reading into waitChunkVerdict(). That is a STRONGER shape -- one place to get
# wrong instead of two -- but it invalidates the old CHECK, not merely its
# strings: neither wait function calls sentinelVerdict any more, so a per-site
# pairing assertion is vacuous there, and the completeness scan would report the
# guard as "moved" when it was centralised.
#
# So the property is re-expressed in three halves, and all three are needed:
#   (a) waitChunkVerdict is the ONLY function in the file that parses a wait
#       reply -- pinned by the same whole-file scan as before, so a NEW parse
#       site appearing anywhere still goes red;
#   (b) inside it, BOTH containment guards run BEFORE the whole-line READY test,
#       on the SAME reply that test reads. That ORDER is the entire property: it
#       preserves #228 (a fail sentinel glued behind ANY character still
#       rejects, because rejectedAnywhere is raw indexOf and never asks where
#       the sentinel sits) TOGETHER WITH #308 (READY stays whole-line equality
#       via sentinelVerdict, so a quoted-but-disavowed success form is still not
#       a success). Reversed, the guards would be dead code;
#   (c) both wait loops delegate every reply to it and neither re-implements any
#       part of the reading -- the drift that would quietly reopen the gap at
#       one site while the other stayed closed.
#
# TIMEOUT is gone from this grammar: the sentinels are READY/FAILED/PENDING, and
# the fail sentinel handed to sentinelVerdict is null, because fail-priority now
# lives in the containment guards ahead of it rather than inside sentinelVerdict.
# (a) is what keeps that from being a hidden weakening -- a null fail sentinel is
# only safe while nothing ELSE parses a reply.
#
# runRound's DRAFT_MISSING site is closed too, but by a DIFFERENT shape, and it
# gets its own assertion rather than joining the tests below. At the wait sites
# the guards are PRE-CHECKS in front of a surviving sentinelVerdict call. At
# runRound the sentinelVerdict call was REPLACED outright by mentionedAnywhere(),
# on the reasoning that containment subsumes whole-line equality -- a line that
# EQUALS the sentinel also CONTAINS it. So there is no "guard precedes
# sentinelVerdict" pair to assert there, and forcing one would make this file's
# own failure message describe the code falsely.
#
# The direction differs as well: DRAFT_MISSING is that site's OK sentinel, so
# gluing there hides a GENUINE missing-draft report rather than hiding a
# rejection. mentionedAnywhere() is a thin wrapper over rejectedAnywhere() for
# exactly that reason -- same containment test, opposite consequence, so it
# carries a name that is not false at the call site.
# ---------------------------------------------------------------------------

# #348's single wait-reply parse site, and the two loops that must delegate to it.
WAIT_PARSE_SITE = "waitChunkVerdict"
WAIT_LOOP_FUNCTIONS = ["getVerifiedReview", "reviewFixLoop"]

# The parse helpers a wait loop must NOT call itself -- every one of them is a
# way to re-implement part of the reading beside the shared site.
WAIT_REPLY_PARSE_HELPERS = ("sentinelVerdict(", GUARD_HELPER + "(", "mentionedAnywhere(")

# The helpers' own definitions match the call regexes; skip those functions.
_HELPER_DEFINITIONS = {"sentinelVerdict", GUARD_HELPER}


def _sentinel_sites_by_function(source):
    """{function_name: [(reply, ok, fail), ...]} for every top-level function
    that CALLS sentinelVerdict, over code lines only."""
    names = _TOP_LEVEL_FUNC_RE.findall(source)
    sites = {}
    for name in names:
        if name in _HELPER_DEFINITIONS:
            continue
        code = _normalized_code(extract_function_body(source, name))
        calls = _SENTINEL_VERDICT_CALL_RE.findall(code)
        if calls:
            sites[name] = calls
    return sites


def test_wait_reply_parsing_lives_only_in_the_single_parse_site():
    """(a) Completeness half, unchanged in method and re-pointed by #348: pins
    WHICH functions call sentinelVerdict, so a NEW parse site cannot appear
    without this file noticing. The two wait functions left this set in 1.16.1
    by delegating (proved in (c) below), not by dropping the check.

    runRound is deliberately NOT in this set either: its sentinelVerdict call was
    replaced by mentionedAnywhere(), so it is covered by its own test below."""
    sites = _sentinel_sites_by_function(MASS_TRANSLATE_SOURCE)
    assert sorted(sites) == [WAIT_PARSE_SITE], (
        f"the set of functions calling sentinelVerdict changed: {sorted(sites)}. "
        f"Since #348 there is exactly ONE wait-reply parse site, and its "
        f"containment guards are what make a null fail sentinel safe there -- a "
        f"new site must carry its own guards, or be justified as not needing "
        f"them, before being added here"
    )


def test_run_round_draft_missing_site_is_containment_keyed_with_no_bare_verdict():
    """runRound's fix branch, asserted in its OWN shape.

    Two halves, and the second is what makes the first mean anything: the branch
    must be keyed on mentionedAnywhere(), AND no bare sentinelVerdict call may
    survive in that function. Asserting only the presence of the containment
    call would stay green if a sentinelVerdict call were left sitting beside it,
    which is the drift that would quietly reopen the gap.

    Over code lines only -- runRound's own comment discusses both helpers by
    name at length, so a raw-slice check would be satisfied by that prose."""
    code = _normalized_code(extract_function_body(MASS_TRANSLATE_SOURCE, "runRound"))

    assert 'mentionedAnywhere(fx, "DRAFT_MISSING " + seg)' in code, (
        "runRound's fix branch must be keyed on "
        'mentionedAnywhere(fx, "DRAFT_MISSING " + seg). DRAFT_MISSING is this '
        "site's OK sentinel, so a glued report is one that goes UNRECOGNIZED -- "
        "the round then continues over a draft the fix agent just said is missing"
    )
    survivors = _SENTINEL_VERDICT_CALL_RE.findall(code)
    assert not survivors, (
        f"runRound still calls sentinelVerdict{survivors}. The DRAFT_MISSING "
        f"check was REPLACED by containment, not supplemented by it -- a "
        f"surviving whole-line-equality call here is either dead code or a "
        f"second, weaker path to the same decision"
    )


def test_wait_chunk_verdict_runs_both_guards_before_the_whole_line_ready_test():
    """(b) The 1.16.0 false-approval fix, now proved where the reading lives.

    The old per-site version paired each sentinelVerdict call with a
    rejectedAnywhere() call on the same reply and the same fail sentinel. That
    pairing survives here in a strictly tighter form -- ONE verdict call, TWO
    guards, all three on the SAME reply, and the guards positionally BEFORE the
    verdict call -- which additionally catches the reversal the old set-based
    pairing could not see: guards that exist but run too late are dead code, and
    the site falls back to whole-line equality exactly as it did pre-1.16.0.

    Over code_lines(), never the raw slice: this function's neighbouring prose
    names both helpers at length, and a raw-slice check would be satisfied by
    that prose alone -- the exact tautology the checkBatchCmd lock was found by
    mutation to have had."""
    code = _normalized_code(extract_function_body(MASS_TRANSLATE_SOURCE, WAIT_PARSE_SITE))

    verdict_calls = _SENTINEL_VERDICT_CALL_RE.findall(code)
    assert len(verdict_calls) == 1, (
        f"expected {WAIT_PARSE_SITE} to hold exactly ONE sentinelVerdict call (the "
        f"whole-line READY test); found {len(verdict_calls)}: {verdict_calls}"
    )
    reply, ok_sentinel, fail_sentinel = verdict_calls[0]
    assert ok_sentinel == '"READY " + seg', (
        f"the whole-line test must be the READY direction, got ok={ok_sentinel!r}"
    )
    assert fail_sentinel == "null", (
        f"the READY test must pass a null fail sentinel (got {fail_sentinel!r}): "
        f"fail-priority moved OUT of sentinelVerdict into the containment guards "
        f"ahead of it, and a second fail path here would be the weaker one"
    )

    guards = _GUARD_CALL_RE.findall(code)
    assert {sentinel for _r, sentinel in guards} == {'"FAILED " + seg', '"PENDING " + seg'}, (
        f"{WAIT_PARSE_SITE} must containment-guard BOTH non-ready sentinels of the "
        f"READY/FAILED/PENDING grammar; found guards on {sorted(s for _r, s in guards)}"
    )
    for guard_reply, sentinel in guards:
        assert guard_reply == reply, (
            f"the {GUARD_HELPER}(..., {sentinel}) guard watches {guard_reply!r} while "
            f"the READY test reads {reply!r} -- a guard on the wrong reply variable "
            f"never fires, and the site silently falls back to whole-line equality"
        )

    # ORDER IS THE PROPERTY. Last guard before first verdict call.
    assert code.rindex(GUARD_HELPER + "(") < code.index("sentinelVerdict("), (
        f"a {GUARD_HELPER}() containment guard runs AFTER the whole-line READY test "
        f"in {WAIT_PARSE_SITE}. Reversed, the guards are dead code for any reply the "
        f"READY test already accepts, and a fail sentinel sharing its line with prose "
        f"is never seen -- measured at 14 of 15 gluing characters over ALL_GLUES in "
        f"tests/mass_translate_sentinel_containment.test.py, in the prose shape "
        f"(prose shares the sentinel's line), a plain space among them"
    )


@pytest.mark.parametrize("function_name", WAIT_LOOP_FUNCTIONS)
def test_wait_loop_delegates_every_reply_to_the_single_parse_site(function_name):
    """(c) Each wait loop proved on its own, over code_lines() so the template's
    own prose about the shared parse cannot satisfy the assertion.

    Both of a wait's replies -- the chunk's and the post-exhaustion re-check's --
    must go through the guarded site. A re-check parsed inline would be a second,
    unguarded reading of exactly the reply that decides whether a landed artifact
    is found, which is the decision #348 exists to get right."""
    code = _normalized_code(extract_function_body(MASS_TRANSLATE_SOURCE, function_name))

    parse_calls = code.count(WAIT_PARSE_SITE + "(")
    assert parse_calls == 2, (
        f"expected {function_name} to parse exactly two wait replies through "
        f"{WAIT_PARSE_SITE}() -- the chunk reply and the re-check reply -- found "
        f"{parse_calls}. If the wait gained or lost a reply, route it through the "
        f"same site and update this count -- do not relax the assertion"
    )
    for helper in WAIT_REPLY_PARSE_HELPERS:
        assert helper not in code, (
            f"{function_name} calls {helper}) itself instead of delegating to "
            f"{WAIT_PARSE_SITE}(). #348 centralised the reading precisely so the "
            f"guard order cannot be right at one wait site and wrong at the other"
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
