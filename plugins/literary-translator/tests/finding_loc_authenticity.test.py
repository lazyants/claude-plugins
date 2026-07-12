"""tests/finding_loc_authenticity.test.py

Targets #133's finding-loc authenticity gate inside
`mass-translate-wf.template.js` -- the `AUTHENTIC_LOC_RE` regex constant and
its `findingsAuthentic(rev)` guard, consumed by `getVerifiedReview` right
after a review artifact matches (see that file's own comment block, declared
near `SEG_ID_RE`). A schema-valid review verdict can still carry a
fabricated finding if the reviewer agent died mid-judgment after it had
already obtained a real draft_sha1/dispatch_token but before it ever
inspected the actual draft content -- what it leaves behind is a
clean-looking verdict whose finding(s) reference an abstract, colonless
infra sentinel (TASK/PROCESS/SYSTEM/RUN) rather than any real content
location. A genuine loc is ALWAYS a colon-delimited structural reference
(a block id like "PARA:seg01:0001" or the shorter "HEAD:seg01" some
adapters emit -- block type is deliberately NOT a fixed enum, see
manifest.schema.json -- or "FN:n", or "VERSE:vid"), so the gate leans on
the ":" shape alone: colon-shaped locs are accepted (whatever the prefix),
bare colonless tokens are rejected. Residual false-block (a healthy
reviewer emitting a colonless holistic loc like "overall") stays
recoverable via #131's blanket blocked-branch ledger-skip, never a terminal
escalation -- see mass-translate-wf.template.js's own comment for the full
fail-safe rationale.

Two layers:
  1. Structural/string-level (Node-independent, pure Python): extracts the
     literal `AUTHENTIC_LOC_RE` pattern text from the real, shipped
     template source and drives it directly via Python's `re` module -- the
     pattern (`^[^\\s:]+:.+$`) uses no lookaround/backreference/JS-only
     escape, so it is byte-identical, valid Python `re` syntax too. This
     locks the regex's own accept/reject boundary in isolation, with no
     Node dependency at all (mirrors review_prompt_schema_drift.test.py's
     own house convention of parsing JS literal syntax with plain Python).
  2. End-to-end (Node-dependent, skipped if node is not on PATH): reuses
     tests/batch_size_estimator.test.py's own Node harness (via
     `importlib.util.spec_from_file_location` -- house style, see that
     file's own module docstring and tests/agent_schema_top_level_object
     .test.py's identical reuse of tests/review_prompt_schema_drift.test.py,
     rather than vendoring a second copy of the harness) to prove the
     gate's real effect on `getVerifiedReview`'s control flow: every named
     bare sentinel routes to blocked/review-fabricated-loc with the
     terminal ledger write skipped (#131 makes it recoverable), a range of
     real colon-form locs are never blocked on authenticity grounds (they
     reach the ordinary non-clean outcome instead), and a clean (empty
     findings) verdict is never blocked either.
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
# Reuse batch_size_estimator.test.py's Node harness (run_workflow/
# blocked_plan/review_obj/review_obj_fabricated_loc/match_true/
# bucket_calls_by_segment/NODE) via importlib, rather than vendoring a
# second copy of it -- house style, see this module's own docstring.
# ---------------------------------------------------------------------------
_ESTIMATOR_TEST_PATH = Path(__file__).resolve().parent / "batch_size_estimator.test.py"
assert _ESTIMATOR_TEST_PATH.is_file(), f"expected sibling test file not found: {_ESTIMATOR_TEST_PATH}"

_estimator_spec = importlib.util.spec_from_file_location(
    "batch_size_estimator_shared_for_finding_loc_authenticity", _ESTIMATOR_TEST_PATH
)
assert _estimator_spec is not None and _estimator_spec.loader is not None, (
    f"could not load spec for {_ESTIMATOR_TEST_PATH}"
)
_estimator = importlib.util.module_from_spec(_estimator_spec)
_estimator_spec.loader.exec_module(_estimator)

NODE = _estimator.NODE
run_workflow = _estimator.run_workflow
review_obj = _estimator.review_obj
review_obj_fabricated_loc = _estimator.review_obj_fabricated_loc
match_true = _estimator.match_true
bucket_calls_by_segment = _estimator.bucket_calls_by_segment

requires_node = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; the end-to-end layer needs Node.js to actually "
    "execute the workflow template's real control flow (reused from "
    "batch_size_estimator.test.py -- this plugin has no hard Node.js dependency "
    "otherwise)",
)


# ---------------------------------------------------------------------------
# Layer 1: pure-Python regex extraction + direct accept/reject lock.
# ---------------------------------------------------------------------------

def _extract_js_regex_literal(source: str, const_name: str) -> str:
    """Locates ``const <const_name> = /<pattern>/;`` (no trailing JS regex
    flags -- AUTHENTIC_LOC_RE's own declaration has none) and returns just
    the pattern text between the slashes. Never hand-retypes the pattern:
    this extracts the REAL literal text from the shipped file, the same
    "lock the real source, don't reimplement its logic" discipline
    tests/review_prompt_schema_drift.test.py's own JS-literal parser
    follows for object literals."""
    m = re.search(r"const\s+" + re.escape(const_name) + r"\s*=\s*/(.+)/;", source)
    assert m, f"expected 'const {const_name} = /pattern/;' (no trailing flags) in {TEMPLATE_PATH}"
    return m.group(1)


@pytest.fixture(scope="module")
def js_source() -> str:
    assert TEMPLATE_PATH.is_file(), f"expected {TEMPLATE_PATH}"
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def authentic_loc_re(js_source: str) -> re.Pattern:
    pattern_text = _extract_js_regex_literal(js_source, "AUTHENTIC_LOC_RE")
    return re.compile(pattern_text)


def test_authentic_loc_re_extracted_and_nonempty(authentic_loc_re):
    assert authentic_loc_re.pattern


@pytest.mark.parametrize(
    "loc",
    [
        "PARA:seg01:0001",  # extract.py.template's own real block-id shape
        "HEAD:seg01",  # the shorter block-id shape some adapters emit
        "FN:3",
        "VERSE:vid",
        "QUOTE:seg02:0007",
        "custom_adapter_block_type:seg09:0002",  # block type is NOT a fixed enum
    ],
)
def test_real_colon_form_locs_are_authentic(authentic_loc_re, loc):
    assert authentic_loc_re.match(loc), f"{loc!r} is a genuine colon-shape loc and must be accepted"


@pytest.mark.parametrize("sentinel", ["TASK", "PROCESS", "SYSTEM", "RUN"])
def test_bare_infra_sentinels_are_rejected(authentic_loc_re, sentinel):
    assert not authentic_loc_re.match(sentinel), (
        f"{sentinel!r} is a bare, colonless infra sentinel and must be rejected"
    )


def test_empty_string_loc_rejected(authentic_loc_re):
    assert not authentic_loc_re.match("")


def test_loc_with_leading_space_before_first_colon_rejected(authentic_loc_re):
    """A space anywhere before the first colon breaks the required
    contiguous non-space-non-colon run -- not a real shape any adapter
    emits, but confirms the gate's own whitespace exclusion is load-bearing,
    not accidental."""
    assert not authentic_loc_re.match("VERSE 12:3")


def test_loc_starting_with_colon_rejected(authentic_loc_re):
    assert not authentic_loc_re.match(":vid")


# ---------------------------------------------------------------------------
# Layer 2: end-to-end via the real control flow (getVerifiedReview inside
# the Node harness) -- authoritative proof the regex's accept/reject
# boundary above actually reaches the reviewFixLoop/runRound branching, not
# just a standalone pattern check.
# ---------------------------------------------------------------------------

@requires_node
@pytest.mark.parametrize("sentinel", ["TASK", "PROCESS", "SYSTEM", "RUN"])
def test_end_to_end_bare_sentinels_route_to_fabricated_loc_and_are_recoverable(tmp_path, sentinel):
    seg = "segAuthReject"
    max_fix_rounds = 0  # drives straight to the single final confirming round
    plan = {
        seg: {
            "wait": f"READY {seg}",
            "reviewWaits": [f"READY {seg}"],
            "reviews": [review_obj_fabricated_loc(sentinel)],
            "artifactChecks": [match_true()],
            "fixes": [],
        }
    }
    out = run_workflow(
        tmp_path=tmp_path, max_fix_rounds=max_fix_rounds, batch_agent_cap=10_000,
        segs=[seg], plan=plan,
    )

    result = out["result"]
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == seg
    assert failed["converged"] is False
    assert failed["reason"] == "review-fabricated-loc"

    per_seg, _ = bucket_calls_by_segment(out["calls"])
    labels = [c["label"] for c in per_seg[seg]]
    # "ledger:in_progress:*" (translateStage's own unconditional write) is
    # expected -- only the TERMINAL "ledger:blocked:*" write must be absent
    # (#131 facet B makes review-fabricated-loc recoverable for free).
    assert not any(label.startswith("ledger:blocked:") for label in labels), (
        f"sentinel {sentinel!r} must not write a terminal ledger entry -- "
        f"the segment stays in_progress and recoverable"
    )


@requires_node
@pytest.mark.parametrize(
    "loc",
    [
        "PARA:seg01:0001",
        "HEAD:seg01",
        "FN:3",
        "VERSE:vid",
        "custom_adapter_block_type:seg09:0002",
    ],
)
def test_end_to_end_real_colon_form_locs_never_blocked_for_authenticity(tmp_path, loc):
    """A real colon-form loc must never be treated as fabricated -- with
    max_fix_rounds=0 the segment's one (final, non-clean) review point ends
    the ordinary way (non_converged/cap), never review-fabricated-loc."""
    seg = "segAuthAccept"
    max_fix_rounds = 0
    review_with_loc = {
        "clean": False,
        "coverage_ok": True,
        "findings": [{"loc": loc, "severity": "minor", "issue": "x", "suggest": "y"}],
        "draft_sha1": "a" * 40,
    }
    plan = {
        seg: {
            "wait": f"READY {seg}",
            "reviewWaits": [f"READY {seg}"],
            "reviews": [review_with_loc],
            "artifactChecks": [match_true()],
            "fixes": [],
        }
    }
    out = run_workflow(
        tmp_path=tmp_path, max_fix_rounds=max_fix_rounds, batch_agent_cap=10_000,
        segs=[seg], plan=plan,
    )

    result = out["result"]
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == seg
    assert failed["reason"] == "cap", (
        f"a real colon-form loc {loc!r} must reach the normal cap outcome, "
        f"never review-fabricated-loc (got reason={failed['reason']!r})"
    )


@requires_node
def test_end_to_end_clean_verdict_never_blocked(tmp_path):
    """A clean (empty findings) verdict is never blocked on authenticity
    grounds -- findingsAuthentic's own short-circuit ("clean/empty verdict
    -> authentic") means it converges normally."""
    seg = "segAuthClean"
    max_fix_rounds = 0
    plan = {
        seg: {
            "wait": f"READY {seg}",
            "reviewWaits": [f"READY {seg}"],
            "reviews": [review_obj(clean=True)],
            "artifactChecks": [match_true()],
            "fixes": [],
        }
    }
    out = run_workflow(
        tmp_path=tmp_path, max_fix_rounds=max_fix_rounds, batch_agent_cap=10_000,
        segs=[seg], plan=plan,
    )

    result = out["result"]
    assert [r["seg"] for r in result["converged"]] == [seg]
    assert result["failed"] == []
