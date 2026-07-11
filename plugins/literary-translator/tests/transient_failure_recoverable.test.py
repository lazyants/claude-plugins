"""tests/transient_failure_recoverable.test.py

Targets #131's transient/mechanical-failure-becomes-recoverable design in
``mass-translate-wf.template.js``: the translate-timeout branch
(``reviewFixLoop``), the getVerifiedReview-blocked branch (``runRound``),
and the fix-call branch's two facets (``runRound``) must NOT record a
terminal ledger write for a transient/infra reason -- an unconditional
``recordLedgerCall`` there would overwrite the ``in_progress`` fragment
``translateStage`` already wrote with a terminal status, taking the segment
permanently out of ``select_segments.py``'s recoverable classification --
EXCEPT the genuinely-absent-draft sub-case of the fix-call branch, which
stays terminal on purpose (a real anomaly worth human attention, unchanged
from before #131).

Pure structural/string-level checks on the REAL, shipped template source --
no Node dependency (mirrors tests/review_prompt_schema_drift.test.py's own
house convention: this file locks source SHAPE as a fast, harness-
independent regression guard; tests/batch_size_estimator.test.py's own Node
harness is the authoritative end-to-end proof of the same invariants via
real call counts and ledger-write presence/absence -- see its "#131 facet
A/B/C" fixtures, e.g. ``test_blocked_fix_call_failed_terminating_subcase``).
Reuses ``review_prompt_schema_drift.test.py``'s ``_find_balanced_brace_span``
(via importlib, house style -- see ``tests/agent_schema_top_level_object
.test.py``'s identical reuse) to extract exact function/if-block spans
rather than a brittle line-range slice, so this file survives incidental
reformatting/reordering elsewhere in the template.
"""
import importlib.util
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "templates" / "mass-translate-wf.template.js"
)

# ---------------------------------------------------------------------------
# Reuse review_prompt_schema_drift.test.py's brace-matching helper and JS
# object-literal parser (house style -- see
# tests/agent_schema_top_level_object.test.py's identical reuse), rather
# than vendoring a second copy.
# ---------------------------------------------------------------------------
_DRIFT_TEST_PATH = Path(__file__).resolve().parent / "review_prompt_schema_drift.test.py"
assert _DRIFT_TEST_PATH.is_file(), f"expected sibling test file not found: {_DRIFT_TEST_PATH}"

_drift_spec = importlib.util.spec_from_file_location(
    "review_prompt_schema_drift_shared_for_transient_failure_recoverable", _DRIFT_TEST_PATH
)
assert _drift_spec is not None and _drift_spec.loader is not None, f"could not load spec for {_DRIFT_TEST_PATH}"
_drift = importlib.util.module_from_spec(_drift_spec)
_drift_spec.loader.exec_module(_drift)

_find_balanced_brace_span = _drift._find_balanced_brace_span
parse_js_object_literal = _drift.parse_js_object_literal
extract_const_object_literal = _drift.extract_const_object_literal


@pytest.fixture(scope="module")
def js_source() -> str:
    assert TEMPLATE_PATH.is_file(), f"expected {TEMPLATE_PATH}"
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _extract_function_body(source: str, signature_pattern: str) -> str:
    """Locates a function whose signature matches ``signature_pattern`` (a
    regex ending right before the opening ``{``) and returns the text
    strictly BETWEEN its outer braces."""
    m = re.search(signature_pattern, source)
    assert m, f"expected a function matching {signature_pattern!r} in {TEMPLATE_PATH}"
    brace_start = source.index("{", m.end())
    brace_end = _find_balanced_brace_span(source, brace_start)  # index just past the closing '}'
    return source[brace_start + 1 : brace_end - 1]


def _extract_if_block(function_body: str, condition_substring: str, *, context: str) -> str:
    """Within ``function_body``, locates ``condition_substring`` (verbatim)
    and returns the text strictly between the braces of the ``if`` block
    whose condition contains it -- i.e. the next balanced ``{...}`` span
    after the condition text."""
    idx = function_body.find(condition_substring)
    assert idx != -1, f"expected {condition_substring!r} inside {context} in {TEMPLATE_PATH}"
    brace_start = function_body.index("{", idx)
    brace_end = _find_balanced_brace_span(function_body, brace_start)
    return function_body[brace_start + 1 : brace_end - 1]


@pytest.fixture(scope="module")
def review_fix_loop_body(js_source: str) -> str:
    return _extract_function_body(js_source, r"async\s+function\s+reviewFixLoop\s*\([^)]*\)\s*")


@pytest.fixture(scope="module")
def run_round_body(js_source: str) -> str:
    return _extract_function_body(js_source, r"async\s+function\s+runRound\s*\([^)]*\)\s*")


@pytest.fixture(scope="module")
def draft_probe_prompt_body(js_source: str) -> str:
    return _extract_function_body(js_source, r"function\s+draftProbePrompt\s*\([^)]*\)\s*")


@pytest.fixture(scope="module")
def draft_present_and_valid_body(js_source: str) -> str:
    return _extract_function_body(js_source, r"async\s+function\s+draftPresentAndValid\s*\([^)]*\)\s*")


# ---------------------------------------------------------------------------
# (a) reviewFixLoop's translate-timeout branch: no recordLedgerCall, returns
# translate-timeout directly.
# ---------------------------------------------------------------------------

def test_translate_timeout_branch_has_no_ledger_write(review_fix_loop_body):
    branch = _extract_if_block(
        review_fix_loop_body, 'ready.indexOf("READY") === -1', context="reviewFixLoop"
    )
    assert "recordLedgerCall" not in branch, (
        "the translate-timeout branch must NOT call recordLedgerCall -- a terminal "
        "write there would overwrite the in_progress fragment and take the segment "
        "out of select_segments.py's recoverable classification (#131 facet C)"
    )
    assert 'reason: "translate-timeout"' in branch
    assert "converged: false" in branch


# ---------------------------------------------------------------------------
# (b) runRound's getVerifiedReview-blocked branch: no recordLedgerCall,
# returns the verdict's own reason directly.
# ---------------------------------------------------------------------------

def test_verified_review_blocked_branch_has_no_ledger_write(run_round_body):
    branch = _extract_if_block(
        run_round_body, 'verified.status === "blocked"', context="runRound"
    )
    assert "recordLedgerCall" not in branch, (
        "runRound's blocked branch must NOT call recordLedgerCall for ANY "
        "getVerifiedReview-blocked reason -- review-timeout/review-null/"
        "review-artifact-mismatch/review-fabricated-loc are all transient/infra, "
        "never genuine content non-convergence (#131 facet B)"
    )
    assert "reason: verified.reason" in branch
    assert "converged: false" in branch


# ---------------------------------------------------------------------------
# (c) runRound's fix-call branch: probes before concluding draft-missing;
# fix-call-failed (present) skips the ledger write, draft-missing (absent)
# still writes it.
# ---------------------------------------------------------------------------

def test_fix_call_branch_probes_before_concluding_draft_missing(run_round_body):
    branch = _extract_if_block(
        run_round_body, 'fx.indexOf("DRAFT_MISSING") !== -1', context="runRound"
    )
    assert "draftPresentAndValid(seg)" in branch, (
        "the fix-call branch must probe draftPresentAndValid(seg) before concluding "
        "genuine draft-missing -- a falsy/DRAFT_MISSING fx alone can't tell a real "
        "missing draft apart from a transient agent death/output-token-ceiling/"
        "classifier-block on an otherwise fine draft (#131 facet A)"
    )

    # Review-fix pass MAJOR correctness fix: the recoverable branch must be
    # `present !== false`, NOT a plain truthy `if (present)` -- the latter
    # would collapse `present === null` (the PROBE CALL ITSELF failing --
    # agent death/output-token ceiling/classifier block on the probe, not
    # just the fix) down to the SAME path as `present === false` (genuine
    # absence), wrongly landing a correlated outage on terminal
    # draft-missing instead of recoverable fix-call-failed.
    recoverable_block = _extract_if_block(branch, "present !== false", context="runRound's fix-call branch")
    assert "recordLedgerCall" not in recoverable_block, (
        "fix-call-failed (draft present and valid, OR the probe call itself "
        "failed inconclusively) must NOT write a terminal ledger entry -- it "
        "stays in_progress and recoverable"
    )
    assert '"fix-call-failed"' in recoverable_block

    # The genuinely-absent-draft path (present === false, falls through past
    # the recoverable block) must be UNCHANGED and still terminal.
    remainder = branch[branch.index(recoverable_block) + len(recoverable_block):]
    assert "recordLedgerCall" in remainder, (
        "the genuine draft-missing path must still write its terminal ledger "
        "entry (blocked/draft-missing -> human_escalation) -- unchanged"
    )
    assert '"draft-missing"' in remainder


# ---------------------------------------------------------------------------
# (d) draftProbePrompt runs both draft_ready.py and validate_draft.py; the
# frozen probe label/schema wiring in draftPresentAndValid matches
# CONTRACT §4.
# ---------------------------------------------------------------------------

def test_draft_probe_prompt_runs_both_gates(draft_probe_prompt_body):
    assert "draft_ready.py" in draft_probe_prompt_body
    assert "validate_draft.py" in draft_probe_prompt_body


def test_draft_present_and_valid_uses_frozen_label_and_schema(draft_present_and_valid_body):
    assert '"draft-probe:" + seg' in draft_present_and_valid_body
    assert "DRAFT_PROBE_SCHEMA" in draft_present_and_valid_body
    assert "draftProbePrompt(seg)" in draft_present_and_valid_body


# ---------------------------------------------------------------------------
# TDZ gotcha regression lock (mirrors review_prompt_schema_drift.test.py's
# own "declared above the real pipeline() call" lock, extended to
# DRAFT_PROBE_SCHEMA): a schema const declared AFTER its first use silently
# no-ops under this execution model's temporal-dead-zone semantics.
# ---------------------------------------------------------------------------

def test_draft_probe_schema_declared_above_the_real_pipeline_call(js_source):
    call_match = re.search(r"\bawait\s+pipeline\s*\(", js_source)
    assert call_match, f"expected an 'await pipeline(...)' call in {TEMPLATE_PATH}"
    pipeline_call_idx = call_match.start()

    const_match = re.search(r"\bconst\s+DRAFT_PROBE_SCHEMA\s*=", js_source)
    assert const_match, f"expected 'const DRAFT_PROBE_SCHEMA =' in {TEMPLATE_PATH}"
    assert const_match.start() < pipeline_call_idx, (
        "DRAFT_PROBE_SCHEMA is declared AFTER the real 'await pipeline(...)' call -- "
        "this silently no-ops under this execution model's temporal-dead-zone "
        "semantics (references/gotchas.md item 10); it must be declared above every "
        "use, including this call"
    )


def test_draft_probe_schema_shape(js_source):
    literal_text = extract_const_object_literal(js_source, "DRAFT_PROBE_SCHEMA")
    parsed = parse_js_object_literal(literal_text)
    assert parsed["type"] == "object"
    assert parsed["additionalProperties"] is False
    assert parsed["required"] == ["present"]
    assert parsed["properties"]["present"]["type"] == "boolean"


# ---------------------------------------------------------------------------
# Regression-catcher: the if-block extraction helper itself must not be
# vacuously true -- prove it actually finds recordLedgerCall when it's
# really there (the draft-missing remainder above already does this
# implicitly, but an explicit standalone case pins the helper's own
# correctness independent of runRound's real shape).
# ---------------------------------------------------------------------------

def test_extract_if_block_helper_finds_a_real_ledger_call(run_round_body):
    converged_branch = _extract_if_block(
        run_round_body, "rev.clean && rev.coverage_ok", context="runRound"
    )
    assert "recordLedgerCall" in converged_branch, (
        "sanity check: the converged branch DOES call recordLedgerCall -- proves "
        "the helper isn't vacuously missing every call, which would make the "
        "'not in branch' assertions above meaningless"
    )
