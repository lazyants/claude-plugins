"""tests/mass_translate_sentinel_containment.test.py -- containment guard for
mass-translate-wf.template.js's two chunked wait sites.

WHAT IS BEING GUARDED. Both wait sites parse every reply through ONE function,
``waitChunkVerdict(reply, seg)``, whose order is load-bearing::

    if (rejectedAnywhere(reply, "FAILED " + seg)) return "failed";
    if (rejectedAnywhere(reply, "PENDING " + seg)) return "pending";
    if (sentinelVerdict(reply, "READY " + seg, null)) return "ready";
    return "pending";

The two ``rejectedAnywhere`` calls are raw containment -- they never ask WHERE
the sentinel sits -- and they run BEFORE the whole-line ``READY`` test. That
order is the entire false-GREEN boundary this file exists to hold: a reply that
reports failure and then, for any reason, ends with a clean ``READY <seg>`` line
must never be read as ready.

#348 CHANGED THE GRAMMAR, AND CHANGED WHY THE GUARD IS LOAD-BEARING. The wait
sites used to answer ``READY <seg>`` / ``TIMEOUT <seg>`` in a single ``agent()``
call. They now answer ``READY <seg>`` / ``FAILED <seg>`` / ``PENDING <seg>``
across up to ``WAIT_CHUNKS`` bounded chunk calls followed by ONE authoritative
non-polling re-check; ``TIMEOUT <seg>`` is gone from these sites entirely. The
rule this file protects is unchanged in meaning -- a glued failure sentinel
never lets a segment converge -- and is simply re-pointed at the two new
fail-direction sentinels over the same glue table.

What DID change is the mechanism underneath, and it moved in the direction of
MORE exposure, not less. ``waitChunkVerdict`` passes ``null`` as
``sentinelVerdict``'s ``failSentinel``, so ``sentinelVerdict``'s own
fail-priority line scan DOES NOT RUN AT THESE SITES AT ALL. Before, containment
was a pre-check that caught what the line scan missed; now it is the ONLY
fail-direction mechanism there is. Measured through this file's own harness, one
full workflow run per case, with both ``rejectedAnywhere`` guards deleted from
the shipped template and nothing else altered, over ALL_GLUES (15 characters,
defined below):

                                  prose + GLUE + FAIL     GLUE + FAIL (no prose)
    translate wait, FAILED             15/15 FALSE-PASS         15/15 FALSE-PASS
    translate wait, PENDING            15/15 FALSE-PASS         15/15 FALSE-PASS
    review wait,    FAILED             15/15 FALSE-PASS         15/15 FALSE-PASS
    review wait,    PENDING            15/15 FALSE-PASS         15/15 FALSE-PASS

EVERY CELL. Including LF, and including every character ``trim()`` strips. There
is no shape that rescues a fail sentinel here any more, because nothing is
looking at lines in the fail direction.

THAT IS WHY THE OLD NUMBERS IN THIS FILE MOVED, AND THE OLD ONES WERE NOT WRONG.
Re-measured on a third variant of the template -- both guards deleted AND
``sentinelVerdict`` handed ``"FAILED " + seg`` as its ``failSentinel``, i.e. the
pre-#348 mechanism expressed in the new grammar -- this same harness reproduces
the figures this file used to publish exactly: 14/15 false-pass in the prose
shape, 6/15 in the no-prose shape. Those rows were a property of a line scan
that the shipped wait sites no longer perform. Quoting them against the current
template is what would be wrong, not the measurement that produced them.

THE CONSEQUENCE FOR THIS FILE'S STRUCTURE, stated plainly because it is the one
thing a reader diffing against the pre-#348 version must not misread as a
weakening. The no-prose rows over trim-strippable glue used to be this file's
NEGATIVE CONTROL: they blocked WITHOUT any guard, which is what established that
these tests track the real mechanism rather than merely detecting that a guard
exists. They no longer have that property -- measured, 15/15 above -- so they
have moved from the control set INTO the contract set. Not one fixture was
dropped and not one glue left the table; the rows simply became stricter.

THE NEGATIVE CONTROL IS RE-POINTED, NOT DELETED, at the READY direction, where
``sentinelVerdict``'s whole-line-equality-modulo-``trim()`` rule is still the
live mechanism and where NO containment guard can possibly participate, because
those fixtures contain no ``FAILED``/``PENDING`` text at all. Measured across
all three template variants above -- shipped, guards deleted, and pre-#348
mechanism -- these rows are byte-identical in outcome, which is the direct
evidence that they are guard-independent:

                                  prose + GLUE + READY    GLUE + READY (no prose)
    both sites, every variant          1/15 converge            9/15 converge

The 1 is LF, the only glue that genuinely ends the line, leaving ``READY <seg>``
as the last line and exercising #308's deliberate prose-PREAMBLE tolerance. The
9 are the 8 characters ``trim()`` strips plus LF -- ``SENTINEL_ISOLATING`` below
-- reaching the same end state by ``sentinelVerdict``'s two different routes.
The remaining 6 are the characters ``trim()`` does not strip, which leave the
line equal to nothing and correctly deny the READY.

Which characters ``trim()`` strips was MEASURED, not eyeballed, and it does not
follow intuition: U+2028 and U+2029 ARE stripped, while U+0085 NEL -- the
character one would most naturally reach for as a line boundary -- is NOT in the
JS WhiteSpace set, and neither is ZWSP.

SITES. The two are easy to mix up, so they are named by what they wait FOR, and
each now has FOUR labels' worth of surface rather than one:

  * TRANSLATE wait -- ``reviewFixLoop``. Chunks at ``wait:<seg>`` (the label is
    deliberately REUSED across chunks), re-check at ``wait-recheck:<seg>``.
    Blocks with ``reason:"translate-timeout"``. The template's own comment calls
    this "the worse of the two sites to get wrong in EITHER direction": a false
    GREEN sends the entire review/fix cycle over a draft that never finished
    translating, and nothing records the segment as recoverable.
  * REVIEW wait -- ``getVerifiedReview``. Chunks at
    ``review-wait:<seg>:r<round>``, re-check at
    ``review-wait-recheck:<seg>:r<round>``. Blocks with
    ``reason:"review-timeout"``.

THREE CALL POSITIONS ARE COVERED, because a guard that holds at the first call
and nowhere else would be a false all-clear: the FIRST chunk, a MID-LOOP chunk
(chunk 3, with the loop proven to have reached it by call count, never inferred
from the fixture alone), and the RE-CHECK -- a call position that did not exist
before #348 and whose reply is parsed by the very same function.

``runRound``'s ``DRAFT_MISSING`` probe is not a wait-site call and is out of
scope here: it is an OK-DIRECTION containment check (``mentionedAnywhere``),
where gluing hides a genuine report rather than a rejection. Its behavioural
coverage lives in tests/mass_translate_driver_smoke.test.py.

tests/wait_chunking.test.py is the sibling lock for #348's own two properties
(no chunk approaches the Bash-tool cap; the bounds SUM to WAIT_BOUND_SEC; the
re-check runs and does not poll). It carries a few grammar cases of its own on a
short inline glue list; THIS file is the exhaustive one, and the two are
deliberately independent copies.

MECHANISM. Self-contained extract-substitute-wrap-run-under-Node harness, the
house pattern (see tests/mass_translate_driver_smoke.test.py and
tests/wait_chunking.test.py) -- the REAL template is instantiated and executed
with a mocked ``agent()``/``pipeline()``/``log()``, and the assertions are on the
workflow's actual returned verdict, never on its source text. Every case runs
inside ONE Node process (a module-scoped fixture), because one process per case
would add minutes to the suite for no extra signal.
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

# Chunk labels are REUSED across every chunk of one wait -- that is the
# template's deliberate choice (#348 kept the pre-existing labels so label-keyed
# mocks keep matching), and it is why the harness below counts calls per label
# instead of keying a dict by label, which would keep only the last one.
TRANSLATE_WAIT_LABEL = "wait:" + SEG
TRANSLATE_RECHECK_LABEL = "wait-recheck:" + SEG
# The review labels carry the round suffix; a bare "review-wait:seg01" would
# silently match nothing and every review case would read as a clean run.
REVIEW_WAIT_LABEL = "review-wait:" + SEG + ":r1"
REVIEW_RECHECK_LABEL = "review-wait-recheck:" + SEG + ":r1"

TRANSLATE_TIMEOUT_REASON = "translate-timeout"
REVIEW_TIMEOUT_REASON = "review-timeout"

# The two fail-direction sentinels of the #348 grammar. Both are guarded by raw
# containment and both are tested over the full glue table: a reply misread as
# READY is the same false green whichever of them was glued.
FAILED_SENTINEL = "FAILED " + SEG
PENDING_SENTINEL = "PENDING " + SEG
OK_SENTINEL = "READY " + SEG

# Named for what each costs when it is misread, not for its spelling -- the two
# behave identically at the boundary but differ in what the loop does next, and
# the mid-loop test below asserts that difference.
FAIL_SENTINELS = [
    ("failed", FAILED_SENTINEL),
    ("pending", PENDING_SENTINEL),
]

PROSE = "The bounded poll finished."

LF = chr(0x0A)


# ---------------------------------------------------------------------------
# Glue characters, built with chr() -- never typed as a character and never as
# a backslash-u escape, which is what a careless paste silently replaces with
# the character itself (invisible in every later diff of this file).
#
# Split by whether JS trim() strips them. That split no longer decides anything
# in the FAIL direction (see the module docstring: containment is shape-blind
# and is now the only fail-direction mechanism), but it still decides the READY
# direction exactly, which is where the negative control now lives. The
# partition is kept for that reason and because it carries information a flat
# list does not. Verified by measurement, not assumed: U+2028 and U+2029 ARE
# stripped, U+0085 is NOT.
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

# LF is its own case: it is the ONE glue that genuinely ends the line, so it is
# the only one that ever behaved differently from the rest -- and, since #348
# removed the line scan from the fail direction, the only one whose behaviour
# CHANGED there.
NEWLINE_GLUE = ("lf", LF)

ALL_GLUES = TRIM_STRIPPED + TRIM_PRESERVED + [NEWLINE_GLUE]

# Glues that leave a sentinel ALONE on its line, by either of the two mechanisms
# sentinelVerdict has: LF isolates it by SPLITTING, the rest by being TRIMMED
# away. Both routes end at the same place -- a line that equals the sentinel.
# In the READY direction, where sentinelVerdict is still the live mechanism,
# that means these are exactly the glues a READY survives.
SENTINEL_ISOLATING = TRIM_STRIPPED + [NEWLINE_GLUE]

SITES = [
    ("translate", TRANSLATE_WAIT_LABEL, TRANSLATE_RECHECK_LABEL, TRANSLATE_TIMEOUT_REASON),
    ("review", REVIEW_WAIT_LABEL, REVIEW_RECHECK_LABEL, REVIEW_TIMEOUT_REASON),
]
SITE_IDS = [s[0] for s in SITES]

GLUE_IDS = [n for n, _ in ALL_GLUES]
SENTINEL_IDS = [n for n, _ in FAIL_SENTINELS]


def prose_glued(sentinel: str, glue: str) -> str:
    """prose + GLUE + FAIL, then a clean OK sentinel on its own final line.

    The trailing OK line is load-bearing: without it the reply cannot approve at
    all, so "the run blocked" would say nothing about whether the FAIL sentinel
    was seen. (Measured the hard way -- a first version of this fixture omitted
    it and reported a reassuring all-clear at every site and every character.)"""
    return PROSE + glue + sentinel + LF + OK_SENTINEL


def whitespace_prefixed(sentinel: str, glue: str) -> str:
    """GLUE + FAIL with no prose, then the same clean OK final line.

    Pre-#348 this shape was rescued by trim() for the eight strippable glues.
    It is not rescued any more -- the fail scan it relied on is not called at
    these sites -- so it is now a contract row like the prose shape, and it is
    kept precisely to record that the two shapes have converged."""
    return glue + sentinel + LF + OK_SENTINEL


def prose_glued_ready(glue: str) -> str:
    """prose + GLUE + READY, with NO fail-direction sentinel anywhere.

    The READY-direction counterpart, and the reason it is a clean control: with
    no ``FAILED``/``PENDING`` text in the reply, neither containment guard can
    fire, so the outcome is decided by ``sentinelVerdict`` alone."""
    return PROSE + glue + OK_SENTINEL


def whitespace_prefixed_ready(glue: str) -> str:
    """GLUE + READY, no prose, no fail-direction sentinel anywhere."""
    return glue + OK_SENTINEL


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
        # #409 stage 0 -- generously above any need in this file, same
        # reasoning as BATCH_AGENT_CAP above: this file exercises sentinel/
        # glue-character containment, not either preflight gate.
        ("{{MAX_CODEX_JOBS_PER_BATCH}}", "100000"),
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
# with a different injection, and all of the template's state is function-scoped,
# so each invocation starts clean.
#
# WHY THE DEFAULT RE-CHECK REPLY IS "PENDING", which is the single most
# consequential fixture decision in this file. The re-check is an INDEPENDENT
# authoritative gate on the canonical artifact, at its own label. If it answered
# READY by default it would rescue every case here, and every assertion below
# would be measuring the re-check instead of the guard. Answering PENDING models
# the only world in which the containment question is even askable: the artifact
# genuinely never landed, so the glued failure report was TRUE. If the artifact
# HAD landed, converging would be correct -- that is #348's whole point, and
# tests/wait_chunking.test.py holds that direction.
HARNESS = r"""
'use strict';
__WRAPPED_SOURCE__

const SEGS_ARGS = __SEGS_JSON__;
const CASES = __CASES_JSON__;
let CASE = null;
const counts = {};

// A wait call is a RE-CHECK iff its label contains "-recheck:"; every other
// wait-shaped label is a chunk. Written as containment rather than a prefix
// test on purpose -- the review site's label is
// "review-wait-recheck:<seg>:r<round>", so a prefix test against
// "wait-recheck:" would misclassify it as a chunk and the fixture would
// silently answer the wrong thing.
function waitKind(label) {
  if (label.indexOf("-recheck:") !== -1) return "recheck";
  if (label.indexOf("wait:") === 0 || label.indexOf("review-wait:") === 0) return "chunk";
  return null;
}

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  counts[label] = (counts[label] || 0) + 1;
  const s = label.split(":")[1];

  const kind = waitKind(label);
  if (kind !== null) {
    // The site under test. chunkAt selects WHICH chunk call receives the
    // injected reply (null = all of them); the other chunks answer PENDING so
    // the loop keeps going and actually reaches the injected position.
    if (kind === "chunk" && label === CASE.chunkLabel && CASE.chunkReply !== null) {
      if (CASE.chunkAt === null || CASE.chunkAt === counts[label]) return CASE.chunkReply;
      return "PENDING " + s;
    }
    if (kind === "recheck" && label === CASE.recheckLabel && CASE.recheckReply !== null) {
      return CASE.recheckReply;
    }
    // Any wait not under test: chunks answer a clean READY so the run reaches
    // the site that IS under test; re-checks answer PENDING (see the comment
    // above the harness for why that default is the load-bearing one).
    return kind === "chunk" ? "READY " + s : "PENDING " + s;
  }

  if (label.indexOf("ledger:") === 0) {
    const parts = label.split(":");
    const kind2 = parts[1];
    const seg = parts[parts.length - 1];
    let status = "converged";
    if (kind2 === "in_progress") status = "in_progress";
    else if (kind2 === "blocked") status = "blocked";
    else if (kind2 === "cap") status = "non_converged";
    return { success: true, status: status, fragment_path: "/x/" + seg + ".json", fragment_sha1: "d" };
  }
  if (label === "merge-ledger") {
    return { success: true, ledger_path: "/x/l.json", n_segments: SEGS_ARGS.length, missing_segments: [], stale_segments: [] };
  }
  if (label.indexOf("translate:") === 0) return "DISPATCHED " + s + " a1b2c3d4";
  if (label.indexOf("review-dispatch:") === 0) return "DISPATCHED " + s + " beef1234";
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
    CASE = c;
    for (const k of Object.keys(counts)) delete counts[k];
    try {
      const result = await __workflowMain__(agent, pipeline, log, SEGS_ARGS);
      // The per-label call COUNTS ride along with every result: a fixture that
      // targets "chunk 3" and silently never reaches chunk 3 produces exactly
      // the same blocked verdict as one that does, so the tests assert the
      // position was reached rather than inferring it.
      results[c.id] = { result: result, counts: JSON.parse(JSON.stringify(counts)) };
    } catch (err) {
      results[c.id] = { error: String((err && err.message) || err) };
    }
  }
  process.stdout.write(JSON.stringify(results));
})();
"""


def run_cases(tmp_path: Path, cases: list) -> dict:
    """Runs every case in one Node process."""
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
    proc = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, (
        f"the mass-translate containment harness failed to run:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def _case(case_id: str, *, chunk_label=None, chunk_reply=None, chunk_at=None,
          recheck_label=None, recheck_reply=None) -> dict:
    return {
        "id": case_id,
        "chunkLabel": chunk_label,
        "chunkReply": chunk_reply,
        "chunkAt": chunk_at,
        "recheckLabel": recheck_label,
        "recheckReply": recheck_reply,
    }


def _case_id(site: str, position: str, sentinel_name: str, shape: str, glue_name: str) -> str:
    return f"{site}__{position}__{sentinel_name}__{shape}__{glue_name}"


CLEAN_RUN_ID = "clean_run"

# The chunk index the mid-loop fixtures inject at. 3 is deliberate: it is past
# the first call (the only position that existed before #348) and, on the
# shipped WAIT_CHUNKS of 8, comfortably inside the loop rather than at its edge.
# Nothing here hard-codes 8 -- the tests assert the loop REACHED chunk 3 by call
# count, which is what makes the fixture non-vacuous if the chunk count ever
# changes.
MID_LOOP_CHUNK = 3


def _all_cases() -> list:
    cases = [_case(CLEAN_RUN_ID)]
    for site, chunk_label, recheck_label, _reason in SITES:
        for sentinel_name, sentinel in FAIL_SENTINELS:
            for glue_name, glue in ALL_GLUES:
                # Position 1: the injected reply is what EVERY chunk returns, so
                # the first call carries it. Both shapes.
                cases.append(_case(
                    _case_id(site, "first", sentinel_name, "prose", glue_name),
                    chunk_label=chunk_label, chunk_reply=prose_glued(sentinel, glue),
                    recheck_label=recheck_label, recheck_reply=PENDING_SENTINEL,
                ))
                cases.append(_case(
                    _case_id(site, "first", sentinel_name, "wsonly", glue_name),
                    chunk_label=chunk_label, chunk_reply=whitespace_prefixed(sentinel, glue),
                    recheck_label=recheck_label, recheck_reply=PENDING_SENTINEL,
                ))
                # Position 2: a MID-LOOP chunk. Only the prose shape, because
                # the two shapes are now measurably equivalent in the fail
                # direction (module docstring); the first-call rows above keep
                # both as the record of that equivalence.
                cases.append(_case(
                    _case_id(site, "midloop", sentinel_name, "prose", glue_name),
                    chunk_label=chunk_label, chunk_reply=prose_glued(sentinel, glue),
                    chunk_at=MID_LOOP_CHUNK,
                    recheck_label=recheck_label, recheck_reply=PENDING_SENTINEL,
                ))
                # Position 3: the RE-CHECK reply. Every chunk answers PENDING so
                # the budget is spent and the re-check is actually reached.
                cases.append(_case(
                    _case_id(site, "recheck", sentinel_name, "prose", glue_name),
                    chunk_label=chunk_label, chunk_reply=PENDING_SENTINEL,
                    recheck_label=recheck_label, recheck_reply=prose_glued(sentinel, glue),
                ))
        # The READY-direction control rows: no fail-direction sentinel anywhere,
        # so no containment guard can participate in the outcome.
        for glue_name, glue in ALL_GLUES:
            cases.append(_case(
                _case_id(site, "first", "ready", "prose", glue_name),
                chunk_label=chunk_label, chunk_reply=prose_glued_ready(glue),
                recheck_label=recheck_label, recheck_reply=PENDING_SENTINEL,
            ))
            cases.append(_case(
                _case_id(site, "first", "ready", "wsonly", glue_name),
                chunk_label=chunk_label, chunk_reply=whitespace_prefixed_ready(glue),
                recheck_label=recheck_label, recheck_reply=PENDING_SENTINEL,
            ))
    return cases


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict:
    """Every case's workflow result and per-label call counts, from a single
    Node process."""
    tmp_path = tmp_path_factory.mktemp("mt_containment")
    results = run_cases(tmp_path, _all_cases())
    errored = {cid: r["error"] for cid, r in results.items() if "error" in r}
    assert not errored, f"the template threw for some cases: {errored}"
    return results


def converged_segments(result: dict) -> list:
    return [r["seg"] for r in result.get("converged", [])]


def failure_reason(result: dict) -> str | None:
    failed = result.get("failed", [])
    return failed[0].get("reason") if failed else None


def calls_at(entry: dict, label: str) -> int:
    """How many times the mocked agent() was invoked at `label` in this case."""
    return entry["counts"].get(label, 0)


# ---------------------------------------------------------------------------
# The contract: a glued fail-direction sentinel must still block, at BOTH sites,
# for BOTH sentinels, at EVERY call position.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("site,label,recheck_label,reason", SITES, ids=SITE_IDS)
@pytest.mark.parametrize("sentinel_name,sentinel", FAIL_SENTINELS, ids=SENTINEL_IDS)
@pytest.mark.parametrize("glue_name", GLUE_IDS, ids=GLUE_IDS)
def test_prose_glued_fail_sentinel_still_blocks(
    outcomes, site, label, recheck_label, reason, sentinel_name, sentinel, glue_name
):
    """THE RULE. A fail-direction sentinel sharing its line with prose must still
    be seen.

    This is the everyday shape -- an agent that writes one sentence and then the
    sentinel on the same line -- and a plain SPACE is enough to trigger it. With
    the containment guards removed, all 15 of these cases per site and per
    sentinel let the run converge (measured; module docstring), which at the
    translate site means the whole review/fix cycle proceeds over a draft that
    never finished translating."""
    entry = outcomes[_case_id(site, "first", sentinel_name, "prose", glue_name)]
    result = entry["result"]
    assert converged_segments(result) == [], (
        f"the {site} wait accepted a reply whose {sentinel!r} sentinel was glued "
        f"to prose by {glue_name!r}, so the segment was reported CONVERGED; the "
        f"fail sentinel must be honoured wherever it appears. Result: {result}"
    )
    assert failure_reason(result) == reason, (
        f"the {site} wait must block with reason {reason!r}; got "
        f"{failure_reason(result)!r}. Blocking for the wrong reason would send "
        f"an operator to the wrong recovery. Result: {result}"
    )


@pytest.mark.parametrize("site,label,recheck_label,reason", SITES, ids=SITE_IDS)
@pytest.mark.parametrize("sentinel_name,sentinel", FAIL_SENTINELS, ids=SENTINEL_IDS)
@pytest.mark.parametrize(
    "glue_name", [n for n, _ in TRIM_PRESERVED], ids=[n for n, _ in TRIM_PRESERVED]
)
def test_fail_sentinel_blocks_when_trim_cannot_strip_the_glue(
    outcomes, site, label, recheck_label, reason, sentinel_name, sentinel, glue_name
):
    """Second shape, the half that ALWAYS needed the guard: the sentinel is
    alone on its line but for one character trim() does not strip, so whole-line
    equality could never have rescued it under any grammar."""
    entry = outcomes[_case_id(site, "first", sentinel_name, "wsonly", glue_name)]
    result = entry["result"]
    assert converged_segments(result) == [], (
        f"the {site} wait accepted a {sentinel!r} sentinel preceded only by "
        f"{glue_name!r} -- a character trim() does not strip, so the line never "
        f"equals the sentinel. Result: {result}"
    )
    assert failure_reason(result) == reason, (
        f"expected reason {reason!r}; got {failure_reason(result)!r}"
    )


@pytest.mark.parametrize("site,label,recheck_label,reason", SITES, ids=SITE_IDS)
@pytest.mark.parametrize("sentinel_name,sentinel", FAIL_SENTINELS, ids=SENTINEL_IDS)
@pytest.mark.parametrize(
    "glue_name", [n for n, _ in SENTINEL_ISOLATING], ids=[n for n, _ in SENTINEL_ISOLATING]
)
def test_line_isolated_fail_sentinel_now_blocks_only_because_of_the_guard(
    outcomes, site, label, recheck_label, reason, sentinel_name, sentinel, glue_name
):
    """THE ROWS THAT CHANGED MEANING AT #348, kept for exactly that reason.

    A fail sentinel left ALONE on its line -- by trim() stripping the glue, or by
    LF splitting it off -- used to be caught by sentinelVerdict unaided. These
    were this file's negative control: green on the pre-guard template too,
    verified by measurement, which is what established that the tests tracked the
    real mechanism rather than merely detecting that a guard existed.

    THAT IS NO LONGER TRUE, and pretending otherwise would be the dangerous
    reading. waitChunkVerdict passes null as sentinelVerdict's failSentinel, so
    the line scan these rows relied on is not called at these sites at all.
    Re-measured with both guards deleted, every one of these rows FALSE-PASSES,
    LF included. They are contract rows now, not controls -- stricter than
    before, not weaker -- and the negative control moved to the READY direction
    below, where a guard-independent mechanism still exists.

    Keeping them as their own function rather than folding them into the rows
    above preserves the partition a reader needs to see that this file's old
    published numbers (14/15 and 6/15) and its new ones (15/15) are the same
    measurement of two different mechanisms, not a contradiction."""
    entry = outcomes[_case_id(site, "first", sentinel_name, "wsonly", glue_name)]
    result = entry["result"]
    assert converged_segments(result) == [], (
        f"a {sentinel!r} sentinel preceded only by {glue_name!r} converged. Since "
        f"#348 nothing but the containment guard can see it here, so this row "
        f"failing means the guard itself regressed. Result: {result}"
    )
    assert failure_reason(result) == reason, (
        f"expected reason {reason!r}; got {failure_reason(result)!r}"
    )


# ---------------------------------------------------------------------------
# Call positions that did not exist before #348.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("site,label,recheck_label,reason", SITES, ids=SITE_IDS)
@pytest.mark.parametrize("sentinel_name,sentinel", FAIL_SENTINELS, ids=SENTINEL_IDS)
@pytest.mark.parametrize("glue_name", GLUE_IDS, ids=GLUE_IDS)
def test_fail_sentinel_glued_in_a_mid_loop_chunk_still_blocks(
    outcomes, site, label, recheck_label, reason, sentinel_name, sentinel, glue_name
):
    """A guard that holds on the FIRST call and nowhere else is a false
    all-clear. Before #348 there was only one wait call per site, so "the first
    reply" and "the reply" were the same thing; now there are up to WAIT_CHUNKS
    of them, every one parsed by the same function.

    THE CALL COUNT IS ASSERTED, NOT ASSUMED. A fixture aimed at chunk 3 that
    never reaches chunk 3 blocks for the wrong reason and looks identical to one
    that works -- all-PENDING chunks time out too. So each row proves the loop
    actually got there, and the two sentinels are told apart by what the loop
    does NEXT, which is the behavioural difference between them:

      * FAILED stops the loop where it was injected -- exactly 3 chunk calls.
      * PENDING does not; the loop keeps spending its budget past chunk 3.

    Both then fall to the re-check, which answers PENDING here, so both block."""
    entry = outcomes[_case_id(site, "midloop", sentinel_name, "prose", glue_name)]
    result = entry["result"]
    n_chunks = calls_at(entry, label)

    if sentinel_name == "failed":
        assert n_chunks == MID_LOOP_CHUNK, (
            f"the {site} chunk loop made {n_chunks} calls; the glued FAILED was "
            f"injected at chunk {MID_LOOP_CHUNK} and must stop the loop exactly "
            f"there. A lower count means the fixture never reached the injected "
            f"chunk and this row proves nothing about mid-loop behaviour."
        )
    else:
        assert n_chunks > MID_LOOP_CHUNK, (
            f"the {site} chunk loop made {n_chunks} calls; a glued PENDING at "
            f"chunk {MID_LOOP_CHUNK} must neither stop the loop nor be read as "
            f"READY, so the loop has to continue past it. A count of "
            f"{MID_LOOP_CHUNK} or less means the injected chunk was never "
            f"reached or was mistaken for a terminal verdict."
        )

    assert converged_segments(result) == [], (
        f"the {site} wait accepted a {sentinel!r} sentinel glued by {glue_name!r} "
        f"in chunk {MID_LOOP_CHUNK}; a guard that only holds on the first chunk "
        f"is not a guard. Result: {result}"
    )
    assert failure_reason(result) == reason, (
        f"expected reason {reason!r}; got {failure_reason(result)!r}"
    )


@pytest.mark.parametrize("site,label,recheck_label,reason", SITES, ids=SITE_IDS)
@pytest.mark.parametrize("sentinel_name,sentinel", FAIL_SENTINELS, ids=SENTINEL_IDS)
@pytest.mark.parametrize("glue_name", GLUE_IDS, ids=GLUE_IDS)
def test_fail_sentinel_glued_in_the_recheck_still_blocks(
    outcomes, site, label, recheck_label, reason, sentinel_name, sentinel, glue_name
):
    """The re-check is a NEW call position, at a NEW label, and it is the LAST
    word on the segment -- nothing re-reads it, so a false green here is
    unrecoverable in a way a false green in chunk 1 is not (chunk 1 is still
    followed by the re-check).

    It shares waitChunkVerdict with the chunks, which is the point: this row is
    what makes that sharing load-bearing rather than incidental. If the re-check
    ever grew its own inline reading -- an indexOf on READY, say -- these rows go
    red while every chunk row stays green.

    Every chunk answers PENDING so the budget is genuinely spent and the
    re-check is genuinely reached; the call count asserts that rather than
    trusting it."""
    entry = outcomes[_case_id(site, "recheck", sentinel_name, "prose", glue_name)]
    result = entry["result"]
    assert calls_at(entry, recheck_label) == 1, (
        f"the {site} re-check at {recheck_label!r} was called "
        f"{calls_at(entry, recheck_label)} times, not once; this row cannot say "
        f"anything about the re-check's parsing unless the re-check ran"
    )
    assert converged_segments(result) == [], (
        f"the {site} re-check accepted a reply whose {sentinel!r} sentinel was "
        f"glued to prose by {glue_name!r}. The re-check is the last word on the "
        f"segment -- a false green here is never revisited. Result: {result}"
    )
    assert failure_reason(result) == reason, (
        f"expected reason {reason!r}; got {failure_reason(result)!r}"
    )


# ---------------------------------------------------------------------------
# THE NEGATIVE CONTROL, re-pointed at the READY direction.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("site,label,recheck_label,reason", SITES, ids=SITE_IDS)
@pytest.mark.parametrize(
    "glue_name", [n for n, _ in SENTINEL_ISOLATING], ids=[n for n, _ in SENTINEL_ISOLATING]
)
def test_ready_alone_on_its_line_converges_with_no_guard_involved(
    outcomes, site, label, recheck_label, reason, glue_name
):
    """THE NEGATIVE CONTROL for this whole file, in the one direction where a
    guard-independent mechanism still exists.

    These fixtures contain NO fail-direction sentinel at all, so neither
    containment guard can fire -- the outcome is decided entirely by
    sentinelVerdict's whole-line equality modulo trim(). Verified by measurement
    rather than by argument: across three variants of the template -- shipped,
    both guards deleted, and the pre-#348 mechanism -- these rows return
    byte-identical outcomes.

    That is what keeps this file honest. Every other test here would go green the
    moment any guard exists; these rows would not change at all, so they are what
    establishes that the suite tracks the real reading mechanism rather than
    merely detecting a guard's presence. If a future edit makes these rows depend
    on a guard, the mechanism has changed and this file's stated rule is no
    longer true.

    A READY left ALONE on its line -- by trim() stripping the glue, or by LF
    splitting it off -- must be ACCEPTED. This is the direction where a
    regression costs throughput rather than correctness, which is exactly why it
    needs its own row: an over-eager guard that rejected these would halt every
    segment while satisfying every must-block assertion above."""
    entry = outcomes[_case_id(site, "first", "ready", "wsonly", glue_name)]
    result = entry["result"]
    assert converged_segments(result) == [SEG], (
        f"a clean READY preceded only by {glue_name!r} was NOT accepted at the "
        f"{site} wait. trim() strips this character (or LF ends the line), so "
        f"the line equals the sentinel and sentinelVerdict must accept it with "
        f"no guard involved. Result: {result}"
    )


@pytest.mark.parametrize("site,label,recheck_label,reason", SITES, ids=SITE_IDS)
@pytest.mark.parametrize(
    "glue_name", [n for n, _ in TRIM_PRESERVED], ids=[n for n, _ in TRIM_PRESERVED]
)
def test_ready_behind_an_unstrippable_glue_is_not_a_ready(
    outcomes, site, label, recheck_label, reason, glue_name
):
    """The other half of the control, still with no fail sentinel anywhere: a
    READY behind a character trim() does NOT strip leaves the line equal to
    nothing, so it is not a READY.

    #308's boundary in the OK direction, and the reason waitChunkVerdict's
    fall-through is PENDING rather than READY: anything not unambiguously ready
    costs at worst one more chunk of waiting, bounded by the chunk count, with
    the authoritative re-check still to come."""
    entry = outcomes[_case_id(site, "first", "ready", "wsonly", glue_name)]
    result = entry["result"]
    assert converged_segments(result) == [], (
        f"a READY behind {glue_name!r} -- which trim() does not strip -- was "
        f"accepted at the {site} wait, so a line equal to nothing approved a "
        f"segment. Result: {result}"
    )
    assert failure_reason(result) == reason, (
        f"expected reason {reason!r}; got {failure_reason(result)!r}"
    )


@pytest.mark.parametrize("site,label,recheck_label,reason", SITES, ids=SITE_IDS)
@pytest.mark.parametrize(
    "glue_name", [n for n, _ in TRIM_STRIPPED], ids=[n for n, _ in TRIM_STRIPPED]
)
def test_the_same_character_decides_a_ready_only_by_the_shape_around_it(
    outcomes, site, label, recheck_label, reason, glue_name
):
    """THE PAIRED CONTROL. One character, two shapes, opposite outcomes, and no
    containment guard anywhere near either of them.

    Holding the CHARACTER fixed and varying only the SHAPE is what proves these
    tests track sentinelVerdict's actual rule -- a line is compared to the
    sentinel after trim() -- rather than merely detecting that a guard exists.
    Both halves are asserted here together, so the contrast is one fact rather
    than an inference a reader has to make across two test functions:

      * alone on its line, prefixed only by this character, trim() removes it,
        the line equals the sentinel, and the READY is ACCEPTED;
      * with prose on that same line, trim() never touches the character, the
        line equals nothing, and the READY is DENIED.

    It moved to the READY direction at #348, and that move is the whole point.
    Pre-#348 the same contrast lived in the fail direction; the shipped template
    no longer scans lines for a fail sentinel at these sites, so the fail
    direction has no shape-dependence left to contrast. Asserting it there today
    would be asserting a mechanism that is not running.

    ITS VALUE IS DOCUMENTARY, AND IT IS NOT THE FIRST LINE OF DEFENCE. Every
    fixture it reads is already read, with identical expectations, by the two
    tests above. So it can never be the only thing that goes red, and deleting it
    would lose no detection. It is kept anyway, deliberately: it is the ONLY
    place the shape-dependence is asserted as ONE fact rather than inferred by a
    reader comparing two functions with different parameter sets. That inference
    is exactly what failed earlier in this plugin's history, when two correct
    measurements over different shapes were restated without their shapes and
    read as contradicting each other.

    Restricted to characters trim() actually strips, which is a measured property
    and not an eyeball one: U+2028 and U+2029 ARE stripped, while U+0085 NEL --
    the character one would most naturally reach for as a line boundary -- is NOT
    in the JS WhiteSpace set. Building this control on U+0085 would make both
    halves behave identically and prove nothing at all. LF is excluded for the
    opposite reason: it ends the line in BOTH shapes, so it too would show no
    contrast."""
    ws_only = outcomes[_case_id(site, "first", "ready", "wsonly", glue_name)]["result"]
    prose = outcomes[_case_id(site, "first", "ready", "prose", glue_name)]["result"]

    assert converged_segments(ws_only) == [SEG], (
        f"alone-on-its-line half: a READY behind {glue_name!r} must be accepted "
        f"-- trim() strips this character, so the line equals the sentinel. This "
        f"failing means the shared line-reading mechanism regressed. "
        f"Result: {ws_only}"
    )
    assert converged_segments(prose) == [], (
        f"prose half: the SAME character {glue_name!r} with prose on the READY's "
        f"line leaves it equal to nothing, so it must NOT be accepted. "
        f"Result: {prose}"
    )
    assert failure_reason(prose) == reason, (
        f"the denied half must block with reason {reason!r}; got "
        f"{failure_reason(prose)!r}"
    )


@pytest.mark.parametrize("site,label,recheck_label,reason", SITES, ids=SITE_IDS)
def test_a_prose_preamble_before_a_ready_is_still_tolerated(
    outcomes, site, label, recheck_label, reason
):
    """#308's deliberate tolerance, and the one glue that is not a glue.

    LF genuinely ends the line, so prose + LF + READY leaves READY as the last
    non-empty trimmed line and the reply is ACCEPTED. This is the real observed
    shape an agent produces ("...exit 0.\\n\\nREADY seg01") and rejecting it is
    what #308 was filed about. It is asserted separately from the paired control
    above because it is the row where the two shapes deliberately AGREE."""
    entry = outcomes[_case_id(site, "first", "ready", "prose", "lf")]
    result = entry["result"]
    assert converged_segments(result) == [SEG], (
        f"a prose preamble followed by LF and a clean READY was rejected at the "
        f"{site} wait; #308 exists because that mislabels completed work as a "
        f"timeout. Result: {result}"
    )


# ---------------------------------------------------------------------------
# The control the contract above cannot provide.
# ---------------------------------------------------------------------------

def test_a_clean_run_still_converges(outcomes):
    """A guard that rejected everything would satisfy every must-block assertion
    in this file.

    With no injection at all, every chunk answers a clean READY on its first
    call, the review is clean, and the segment must converge and be reported as
    such -- without any re-check running, since nothing was ever not-ready."""
    entry = outcomes[CLEAN_RUN_ID]
    result = entry["result"]
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
    for wait_label, recheck_label in (
        (TRANSLATE_WAIT_LABEL, TRANSLATE_RECHECK_LABEL),
        (REVIEW_WAIT_LABEL, REVIEW_RECHECK_LABEL),
    ):
        assert calls_at(entry, wait_label) == 1, (
            f"a READY first chunk must stop the loop at {wait_label!r}; got "
            f"{calls_at(entry, wait_label)} calls"
        )
        assert calls_at(entry, recheck_label) == 0, (
            f"a READY chunk must not trigger the re-check at {recheck_label!r}; "
            f"got {calls_at(entry, recheck_label)} calls"
        )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
