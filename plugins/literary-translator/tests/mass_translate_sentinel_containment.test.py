"""tests/mass_translate_sentinel_containment.test.py -- 1.16.0 containment guard
for mass-translate-wf.template.js's two READY/TIMEOUT sentinel sites.

WHAT IS BEING GUARDED. ``sentinelVerdict()`` recognises a fail sentinel only
when, after splitting the reply on LF and calling ``trim()`` on each line, that
sentinel is ALONE on its line. Anything else sharing the line defeats it: the
line then equals neither sentinel, the ``line === failSentinel`` rejection
trigger never fires, and a trailing clean OK line approves the reply. The fix
(shared with glossary-pass-wf.template.js) is a containment pre-check --
``rejectedAnywhere(reply, failSentinel)`` -- applied at the CALL SITES, because
``sentinelVerdict`` itself is mirrored byte-for-byte across all three workflow
templates and pinned by tests/sentinel_verdict_parity.test.py.

THE RULE IS STRUCTURAL, NOT A CHARACTER SET. Measured on the pre-guard template
through this file's own harness, one full workflow run per case:

                              prose + GLUE + FAIL      GLUE + FAIL (no prose)
    translate wait                14/15 FALSE-PASS          6/15 FALSE-PASS
    review wait                   14/15 FALSE-PASS          6/15 FALSE-PASS

THE COUNT IS A PROPERTY OF THE SHAPE, NOT OF THE CHARACTER SET, which is why
both shapes are measured over the SAME 15 characters. ``trim()`` rescues the
second shape for whatever it strips -- space, tab, CR, VT, FF, NBSP, U+2028,
U+2029 -- and nothing rescues the first, where prose sits on the line
regardless. So "any character glues it" holds only for the prose shape. Quoting
either number without its shape is how two correct measurements come to look
like a contradiction.

Which characters ``trim()`` strips was MEASURED, not eyeballed, and it does not
follow intuition: U+2028 and U+2029 ARE stripped, while U+0085 NEL -- the
character one would most naturally reach for as a line boundary -- is NOT in the
JS WhiteSpace set, and neither is ZWSP. Those three defeat the sentinel in
BOTH shapes.

WHY BOTH SHAPES ARE TESTED. The second shape's trim-stripped rows are the
NEGATIVE CONTROL for this whole file: they must block EVEN WITHOUT the guard.
Without them, every row here would go green the moment a guard exists, and
nothing would establish that these tests track the real mechanism rather than
merely detecting the guard's presence.

SITES. The two are easy to mix up, so they are named by what they wait FOR:

  * TRANSLATE wait -- ``reviewFixLoop``, label ``wait:<seg>``, blocks with
    ``reason:"translate-timeout"``. The template's own comment calls this "the
    worst of the five sites (#228)": a false pass sends the entire review/fix
    cycle over a draft that never finished translating, and nothing records the
    segment as recoverable.
  * REVIEW wait -- ``getVerifiedReview``, label
    ``review-wait:<seg>:r<round>``, blocks with ``reason:"review-timeout"``.

The third ``sentinelVerdict`` call in this template (``runRound``'s
``DRAFT_MISSING`` probe) passes a NULL fail sentinel, so a containment guard is
a no-op there by construction -- ``rejectedAnywhere`` returns false for a
non-string sentinel, pinned in tests/glossary_citation_review.test.py. It is
deliberately out of scope here; see tests/bounded_poll_present.test.py's
structural lock, which scopes itself to non-null fail sentinels for that reason.

MECHANISM. Self-contained extract-substitute-wrap-run-under-Node harness, the
house pattern (see tests/mass_translate_driver_smoke.test.py and
tests/glossary_citation_review.test.py) -- the REAL template is instantiated and
executed with a mocked ``agent()``/``pipeline()``/``log()``, and the assertions
are on the workflow's actual returned verdict. Every case runs inside ONE Node
process (a module-scoped fixture), because one process per case would add ~70s
to the suite for no extra signal.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
MASS_TRANSLATE_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"

assert MASS_TRANSLATE_TEMPLATE.is_file(), (
    f"expected plugin template not found: {MASS_TRANSLATE_TEMPLATE}"
)

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; this test executes the real mass-translate "
    "template's sentinel wiring under Node (no hard Node.js dependency for this "
    "plugin otherwise)",
)

SEG = "seg01"
TRANSLATE_WAIT_LABEL = "wait:" + SEG
# The review wait's label carries its round suffix; a bare "review-wait:seg01"
# would silently match nothing and every case would read as a clean run.
REVIEW_WAIT_LABEL = "review-wait:" + SEG + ":r1"

TRANSLATE_TIMEOUT_REASON = "translate-timeout"
REVIEW_TIMEOUT_REASON = "review-timeout"

FAIL_SENTINEL = "TIMEOUT " + SEG
OK_SENTINEL = "READY " + SEG
PROSE = "The bounded poll finished."

LF = chr(0x0A)


# ---------------------------------------------------------------------------
# Glue characters, built with chr() -- never typed as a character and never as
# a backslash-u escape, which is what a careless paste silently replaces with
# the character itself (invisible in every later diff of this file).
#
# Split by whether JS trim() strips them, because that split is exactly what
# decides the second shape's outcome. Verified by measurement, not assumed:
# U+2028 and U+2029 ARE stripped, U+0085 is NOT.
# ---------------------------------------------------------------------------

TRIM_STRIPPED = [
    ("space", chr(0x20)),
    ("tab", chr(0x09)),
    ("cr", chr(0x0D)),
    ("vt", chr(0x0B)),
    ("ff", chr(0x0C)),
    ("nbsp_u00a0", chr(0xA0)),
    ("lsep_u2028", chr(0x2028)),
    ("psep_u2029", chr(0x2029)),
]

TRIM_PRESERVED = [
    ("nel_u0085", chr(0x85)),
    ("zwsp_u200b", chr(0x200B)),
    ("fs_u001c", chr(0x1C)),
    ("letter_x", "x"),
    ("hyphen", chr(0x2D)),
    ("quote", chr(0x22)),
]

# LF is its own case: it is the ONE glue that already worked before the guard,
# in both shapes, because it genuinely ends the line.
NEWLINE_GLUE = ("lf", LF)

ALL_GLUES = TRIM_STRIPPED + TRIM_PRESERVED + [NEWLINE_GLUE]

SITES = [
    ("translate", TRANSLATE_WAIT_LABEL, TRANSLATE_TIMEOUT_REASON),
    ("review", REVIEW_WAIT_LABEL, REVIEW_TIMEOUT_REASON),
]


def prose_glued(glue: str) -> str:
    """prose + GLUE + FAIL, then a clean OK sentinel on its own final line.

    The trailing OK line is load-bearing: without it the reply cannot approve at
    all, so "the run blocked" would say nothing about whether the FAIL sentinel
    was seen. (Measured the hard way -- a first version of this fixture omitted
    it and reported a reassuring 0/12 across every site.)"""
    return PROSE + glue + FAIL_SENTINEL + LF + OK_SENTINEL


def whitespace_prefixed(glue: str) -> str:
    """GLUE + FAIL with no prose, then the same clean OK final line. For a glue
    trim() strips, the FAIL sentinel is alone on its line once trimmed and is
    therefore seen WITHOUT any guard."""
    return glue + FAIL_SENTINEL + LF + OK_SENTINEL


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_RUN_ID = "20260725T000000Z"
FIXTURE_COMPANION_PATH = "/opt/codex/codex-companion.mjs"


def instantiate() -> str:
    """The one-time substitution the template's header documents (duplicated,
    not imported, so this file stays self-contained like every sibling)."""
    text = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    for token, value in (
        ("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT),
        ("{{RUN_ID}}", FIXTURE_RUN_ID),
        ("{{SOURCE_LANG}}", "fr"),
        ("{{TARGET_LANG}}", "ru"),
        ("{{MAX_FIX_ROUNDS}}", "1"),
        ("{{BATCH_AGENT_CAP}}", "100000"),
        ("{{VERSE_POLICY_INSTRUCTION_BLOCK}}", "Render every verse literally."),
        ("{{CODEX_COMPANION_PATH_JSON}}", json.dumps(FIXTURE_COMPANION_PATH)),
        ("{{EFFORT}}", "high"),
        ("{{MODEL}}", ""),
    ):
        text = text.replace(token, value)
    assert "{{" not in text, "fixture instantiation left an unresolved token"
    return text


def _wrap(js_source: str) -> str:
    assert js_source.count("export const meta") == 1
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


# Every case runs in ONE Node process: __workflowMain__ is re-invoked per case
# with a different single-label override, and all of the template's state is
# function-scoped, so each invocation starts clean.
HARNESS = r"""
'use strict';
__WRAPPED_SOURCE__

const SEGS_ARGS = __SEGS_JSON__;
const CASES = __CASES_JSON__;
let OVERRIDES = {};

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  if (Object.prototype.hasOwnProperty.call(OVERRIDES, label)) return OVERRIDES[label];
  if (label.indexOf("ledger:") === 0) {
    const parts = label.split(":");
    const kind = parts[1];
    const s = parts[parts.length - 1];
    let status = "converged";
    if (kind === "in_progress") status = "in_progress";
    else if (kind === "blocked") status = "blocked";
    else if (kind === "cap") status = "non_converged";
    return { success: true, status: status, fragment_path: "/x/" + s + ".json", fragment_sha1: "d" };
  }
  if (label === "merge-ledger") {
    return { success: true, ledger_path: "/x/l.json", n_segments: SEGS_ARGS.length, missing_segments: [], stale_segments: [] };
  }
  const s = label.split(":")[1];
  if (label.indexOf("translate:") === 0) return "DISPATCHED " + s + " a1b2c3d4";
  if (label.indexOf("wait:") === 0) return "READY " + s;
  if (label.indexOf("review-dispatch:") === 0) return "DISPATCHED " + s + " beef1234";
  if (label.indexOf("review-wait:") === 0) return "READY " + s;
  if (label.indexOf("review-read:") === 0) return { clean: true, coverage_ok: true, findings: [], draft_sha1: "a" };
  if (label.indexOf("artifact-check:") === 0) return { match: true };
  if (label.indexOf("fix:") === 0) return "FIXED " + s;
  if (label.indexOf("draft-probe:") === 0) return { present: true };
  // Deliberately non-throwing at the top level would hide a wiring change;
  // this throw is caught per case below and surfaces as that case's error.
  throw new Error("mock agent(): unrecognized label " + label);
}

async function pipeline(items, stage1, stage2) {
  const out = [];
  for (const item of items) {
    const r1 = await stage1(item);
    out.push(await stage2(r1, item));
  }
  return out;
}
function log() {}

(async () => {
  const results = {};
  for (const c of CASES) {
    OVERRIDES = {};
    if (c.label !== null) OVERRIDES[c.label] = c.reply;
    try {
      results[c.id] = { result: await __workflowMain__(agent, pipeline, log, SEGS_ARGS) };
    } catch (err) {
      results[c.id] = { error: String((err && err.message) || err) };
    }
  }
  process.stdout.write(JSON.stringify(results));
})();
"""


def run_cases(tmp_path: Path, cases: list) -> dict:
    """Runs every case in one Node process. `cases` are {id, label, reply};
    label None means "no override" (a clean run)."""
    harness = (
        HARNESS.replace("__WRAPPED_SOURCE__", _wrap(instantiate()))
        .replace("__SEGS_JSON__", json.dumps([SEG]))
        .replace("__CASES_JSON__", json.dumps(cases))
    )
    path = tmp_path / "mass_translate_containment_harness.js"
    path.write_text(harness, encoding="utf-8")
    # NODE is only None when node is absent, in which case pytestmark's skipif
    # already skipped every test here before this call is reached.
    assert NODE is not None
    proc = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"the mass-translate containment harness failed to run:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def _case_id(site: str, shape: str, glue_name: str) -> str:
    return f"{site}__{shape}__{glue_name}"


CLEAN_RUN_ID = "clean_run"


def _all_cases() -> list:
    cases = [{"id": CLEAN_RUN_ID, "label": None, "reply": None}]
    for site, label, _reason in SITES:
        for glue_name, glue in ALL_GLUES:
            cases.append({
                "id": _case_id(site, "prose", glue_name),
                "label": label, "reply": prose_glued(glue),
            })
            cases.append({
                "id": _case_id(site, "wsonly", glue_name),
                "label": label, "reply": whitespace_prefixed(glue),
            })
    return cases


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict:
    """Every case's workflow result, from a single Node process."""
    tmp_path = tmp_path_factory.mktemp("mt_containment")
    results = run_cases(tmp_path, _all_cases())
    errored = {cid: r["error"] for cid, r in results.items() if "error" in r}
    assert not errored, f"the template threw for some cases: {errored}"
    return {cid: r["result"] for cid, r in results.items()}


def converged_segments(result: dict) -> list:
    return [r["seg"] for r in result.get("converged", [])]


def failure_reason(result: dict) -> str | None:
    failed = result.get("failed", [])
    return failed[0].get("reason") if failed else None


# ---------------------------------------------------------------------------
# The contract: a glued fail sentinel must still block, at BOTH sites.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("site,label,reason", SITES, ids=[s[0] for s in SITES])
@pytest.mark.parametrize(
    "glue_name", [n for n, _ in ALL_GLUES], ids=[n for n, _ in ALL_GLUES]
)
def test_prose_glued_timeout_still_blocks(outcomes, site, label, reason, glue_name):
    """A TIMEOUT sentinel sharing its line with prose must still be seen.

    This is the everyday shape -- an agent that writes one sentence and then the
    sentinel on the same line -- and a plain SPACE is enough to trigger it. On
    the pre-guard template 11 of these 12 cases per site let the run converge,
    which at the translate site means the whole review/fix cycle proceeds over a
    draft that never finished translating."""
    result = outcomes[_case_id(site, "prose", glue_name)]
    assert converged_segments(result) == [], (
        f"the {site} wait accepted a reply whose TIMEOUT sentinel was glued to "
        f"prose by {glue_name!r}, so the segment was reported CONVERGED; the "
        f"fail sentinel must be honoured wherever it appears. Result: {result}"
    )
    assert failure_reason(result) == reason, (
        f"the {site} wait must block with reason {reason!r}; got "
        f"{failure_reason(result)!r}. Blocking for the wrong reason would send "
        f"an operator to the wrong recovery. Result: {result}"
    )


@pytest.mark.parametrize("site,label,reason", SITES, ids=[s[0] for s in SITES])
@pytest.mark.parametrize(
    "glue_name", [n for n, _ in TRIM_PRESERVED], ids=[n for n, _ in TRIM_PRESERVED]
)
def test_whitespace_prefixed_timeout_blocks_when_trim_cannot_strip_the_glue(
    outcomes, site, label, reason, glue_name
):
    """Second shape, the half that still needs the guard: the sentinel is alone
    on its line but for one character trim() does NOT strip, so whole-line
    equality still fails."""
    result = outcomes[_case_id(site, "wsonly", glue_name)]
    assert converged_segments(result) == [], (
        f"the {site} wait accepted a TIMEOUT sentinel preceded only by "
        f"{glue_name!r} -- a character trim() does not strip, so the line never "
        f"equals the sentinel. Result: {result}"
    )
    assert failure_reason(result) == reason, (
        f"expected reason {reason!r}; got {failure_reason(result)!r}"
    )


@pytest.mark.parametrize("site,label,reason", SITES, ids=[s[0] for s in SITES])
@pytest.mark.parametrize(
    "glue_name", [n for n, _ in TRIM_STRIPPED], ids=[n for n, _ in TRIM_STRIPPED]
)
def test_trim_strippable_prefix_blocks_with_or_without_the_guard(
    outcomes, site, label, reason, glue_name
):
    """THE NEGATIVE CONTROL for this whole file.

    A TIMEOUT sentinel preceded only by whitespace trim() strips is alone on its
    line once trimmed, so sentinelVerdict sees it unaided -- these rows are green
    on the PRE-guard template too, verified by measurement.

    They are here precisely because they cannot be closed by the guard: they are
    what shows the tests above track the real mechanism -- whole-line equality
    modulo trim() -- rather than merely detecting that a guard exists. If a
    future edit makes these rows depend on the guard, the mechanism has changed
    and the file's stated rule is no longer true."""
    result = outcomes[_case_id(site, "wsonly", glue_name)]
    assert converged_segments(result) == [], (
        f"a TIMEOUT sentinel preceded only by {glue_name!r} must be seen by "
        f"trim()+whole-line equality alone, with no guard involved. This row "
        f"failing means the shared line-reading mechanism itself regressed, not "
        f"that the containment guard is missing. Result: {result}"
    )
    assert failure_reason(result) == reason, (
        f"expected reason {reason!r}; got {failure_reason(result)!r}"
    )


@pytest.mark.parametrize("site,label,reason", SITES, ids=[s[0] for s in SITES])
@pytest.mark.parametrize(
    "glue_name", [n for n, _ in TRIM_STRIPPED], ids=[n for n, _ in TRIM_STRIPPED]
)
def test_the_same_character_hides_the_sentinel_only_when_prose_shares_its_line(
    outcomes, site, label, reason, glue_name
):
    """THE PAIRED CONTROL. One character, two shapes, opposite mechanisms.

    Holding the CHARACTER fixed and varying only the SHAPE is what proves these
    tests track ``sentinelVerdict``'s actual rule -- a line is compared to the
    sentinel after ``trim()`` -- rather than merely detecting that a guard
    exists. Both halves are asserted here together, so the contrast is one fact
    rather than an inference a reader has to make across two test functions:

      * alone on its line, prefixed only by this character, ``trim()`` removes
        it and the sentinel is seen WITHOUT any guard;
      * with prose on that same line, ``trim()`` never touches the character,
        the line equals nothing, and only the containment guard sees it.

    Restricted to characters ``trim()`` actually strips, which is a measured
    property and not an eyeball one: U+2028 and U+2029 ARE stripped, while
    U+0085 NEL -- the character one would most naturally reach for as a line
    boundary -- is NOT in the JS WhiteSpace set. Building this control on U+0085
    would make both halves behave identically and prove nothing at all."""
    ws_only = outcomes[_case_id(site, "wsonly", glue_name)]
    prose = outcomes[_case_id(site, "prose", glue_name)]

    assert converged_segments(ws_only) == [], (
        f"unguarded half: a TIMEOUT alone on its line behind {glue_name!r} must "
        f"be seen by trim()+whole-line equality with no guard involved. This "
        f"failing means the shared line-reading mechanism regressed. "
        f"Result: {ws_only}"
    )
    assert converged_segments(prose) == [], (
        f"guarded half: the SAME character {glue_name!r} with prose on the "
        f"sentinel's line hides it from trim()+equality entirely, so only the "
        f"containment guard can catch it. Result: {prose}"
    )
    assert failure_reason(ws_only) == reason and failure_reason(prose) == reason, (
        f"both shapes must block with reason {reason!r}; got "
        f"{failure_reason(ws_only)!r} (alone) and {failure_reason(prose)!r} (with prose)"
    )


# ---------------------------------------------------------------------------
# The control the contract above cannot provide.
# ---------------------------------------------------------------------------

def test_a_clean_run_still_converges(outcomes):
    """A guard that rejected everything would satisfy every assertion above.

    With no override at all, both waits answer a clean READY, the review is
    clean, and the segment must converge and be reported as such."""
    result = outcomes[CLEAN_RUN_ID]
    assert converged_segments(result) == [SEG], (
        "an unglued, entirely clean run must still converge -- a containment "
        f"guard that fires on ordinary replies halts every segment. Result: {result}"
    )
    assert result.get("failed") == [], (
        f"a clean run must report no failures; got {result.get('failed')}"
    )
    assert result.get("batchComplete") is True, (
        f"a clean run must complete the batch; got {result}"
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
