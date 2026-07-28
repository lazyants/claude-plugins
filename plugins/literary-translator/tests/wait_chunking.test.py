"""tests/wait_chunking.test.py -- #348 regression lock for the W5 wait cap.

THE BUG THIS FILE LOCKS, in the exact shape it was observed on disk.

`mass-translate-wf.template.js` spent its whole `WAIT_BOUND_SEC` (3450 s) wait
inside ONE `agent()` call running ONE bash poll. The agent's Bash tool clamps a
single call at 600 000 ms REGARDLESS of the timeout the agent asks for -- a
measured hard clamp, not a default (the failing call requested
`timeout: 3600000` and still came back `Exit code 143 / Command timed out after
10m 0s`). So every wait longer than ~600 s was killed mid-poll, the agent
reported the failure sentinel, and the segment was declared
`translate-timeout` / `review-timeout` -- *even when the codex job had already
promoted a clean canonical artifact seconds later*. Nothing ever re-read it.

Measured, all three `seg03` waits of the P1 gate run: 511 s -> READY, 311 s ->
READY, 610 s -> TIMEOUT. The 610 s one left `segments/seg03.review.json` clean
and complete on disk beside a ledger saying `in_progress`. That frozen fixture
lives in `../ssk-w5-smoke-116/` and is the end-to-end half of this red; the
tests below are the automated half.

THE TWO PROPERTIES, and why both are needed.

1.  CHUNKING -- no single wait call may approach the 600 s cap. Asserted per
    chunk AND as a sum: the chunk bounds must add up to EXACTLY
    `WAIT_BOUND_SEC`, never more. A flat `WAIT_CHUNK_SEC` per chunk would not
    *spend* the declared bound, it would silently EXTEND it, breaking the one
    contract `WAIT_BOUND_SEC` exists to state. Asserting only "each chunk is
    under the cap" would pass that regression.

2.  THE AUTHORITATIVE RE-CHECK -- after the chunk budget is exhausted, the
    canonical gate runs ONCE more, non-polling, before any timeout is
    declared. This is the actual #348 fix: chunking alone would have turned the
    observed 610 s failure into a success by accident (610 < 3450 either way),
    while leaving the real defect -- *a finished artifact is never re-read* --
    completely intact. A run whose artifact lands after the last chunk still
    converges only because of the re-check.

Both are exercised end-to-end through the REAL template under Node, not by
reading its source: the mock answers `PENDING` to every chunk at both wait
sites and makes the ACCEPT gate succeed only at the re-check. On the unfixed
template that run reports `translate-timeout`; after the fix it converges.

Harness note. This file carries its OWN self-contained harness rather than
importing a sibling's. Under `pytest.ini`'s `python_files = *.test.py` +
`--import-mode=importlib` a test file is not importable by dotted name, so
cross-file reuse would need an explicit loader; three sibling harnesses already
exist as deliberate independent copies. This one differs from all of them in
one way that matters: it records EVERY prompt per label as a LIST, because
chunk calls deliberately reuse the labels `wait:<seg>` /
`review-wait:<seg>:r<round>` and a dict keyed by label would keep only the last
one -- exactly the per-chunk sequence these tests are about.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
MASS_TRANSLATE_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; this test executes the real workflow "
    "template's wait wiring under Node (no hard Node.js dependency for this "
    "plugin otherwise)",
)

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_RUN_ID = "20260727T000000Z"
FIXTURE_SOURCE_LANG = "he"
FIXTURE_TARGET_LANG = "en"
FIXTURE_VERSE_POLICY = "Render every verse literally, line by line."
FIXTURE_COMPANION_PATH = "/opt/codex/1.0.10/codex-companion.mjs"
FIXTURE_EFFORT = "xhigh"
FIXTURE_MODEL = ""

# The measured Bash-tool clamp this whole file exists because of. A chunk that
# declared MORE than this many seconds would be killed mid-poll, which is #348.
BASH_CALL_CAP_SEC = 600
# WAIT_BOUND_SEC's shipped value, restated here as an INDEPENDENT literal on
# purpose: re-deriving it from the template would make the sum assertion below
# tautological (it would pass for any self-consistent pair of constants).
EXPECTED_WAIT_BOUND_SEC = 3450


def instantiate(*, max_fix_rounds: int, batch_agent_cap: int,
                effort: str = FIXTURE_EFFORT, model: str = FIXTURE_MODEL) -> str:
    """The exact one-time substitution the template's header documents
    (duplicated, not imported, so this file stays self-contained like every
    sibling)."""
    text = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT)
    text = text.replace("{{RUN_ID}}", FIXTURE_RUN_ID)
    text = text.replace("{{SOURCE_LANG}}", FIXTURE_SOURCE_LANG)
    text = text.replace("{{TARGET_LANG}}", FIXTURE_TARGET_LANG)
    text = text.replace("{{MAX_FIX_ROUNDS}}", str(int(max_fix_rounds)))
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    text = text.replace("{{VERSE_POLICY_INSTRUCTION_BLOCK}}", json.dumps(FIXTURE_VERSE_POLICY)[1:-1])
    text = text.replace("{{CODEX_COMPANION_PATH_JSON}}", json.dumps(FIXTURE_COMPANION_PATH))
    text = text.replace("{{EFFORT}}", effort)
    text = text.replace("{{MODEL}}", model)
    assert "{{" not in text, "fixture instantiation left an unresolved token"
    return text


def _wrap(js_source: str) -> str:
    assert js_source.count("export const meta") == 1
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


# WAIT_REPLIES is keyed by the wait KIND ("chunk" / "recheck") rather than by
# label, so a test can say "every chunk is PENDING, the re-check is READY"
# without knowing how many chunks the template will make -- which is the whole
# point: the chunk COUNT is a template constant under test, not a fixture input.
HARNESS = r"""
'use strict';
__WRAPPED_SOURCE__

const SEGS_ARGS = __SEGS_JSON__;
const WAIT_REPLIES = __WAIT_REPLIES_JSON__;
const promptsByLabel = {};
const callsLog = [];

function record(label, promptText) {
  if (!Object.prototype.hasOwnProperty.call(promptsByLabel, label)) promptsByLabel[label] = [];
  promptsByLabel[label].push(promptText);
}

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
  record(label, promptText);
  callsLog.push({ label: label, agentType: opts.agentType || null, hasSchema: !!opts.schema });

  const seg = label.split(":")[1];

  const kind = waitKind(label);
  if (kind !== null) {
    const tmpl = WAIT_REPLIES[kind];
    if (tmpl === null || tmpl === undefined) return null;
    // split/join, NOT replace(): String.replace with a string pattern
    // substitutes only the FIRST occurrence, and the glued-sentinel fixtures
    // below deliberately carry two ("...FAILED <seg>\nREADY <seg>"). A
    // first-only substitution would leave the second one literal, so the test
    // would still pass -- while exercising a reply shape it never meant to.
    return tmpl.split("<seg>").join(seg);
  }

  if (label.indexOf("ledger:") === 0) {
    const parts = label.split(":");
    const k = parts[1];
    const s = parts[parts.length - 1];
    let status = "converged";
    if (k === "in_progress") status = "in_progress";
    else if (k === "blocked") status = "blocked";
    else if (k === "cap") status = "non_converged";
    return { success: true, status: status, fragment_path: "/x/" + s + ".json", fragment_sha1: "d" };
  }
  if (label === "merge-ledger") {
    return { success: true, ledger_path: "/x/l.json", n_segments: SEGS_ARGS.length, missing_segments: [], stale_segments: [] };
  }
  if (label.indexOf("translate:") === 0) return "DISPATCHED " + seg + " a1b2c3d4";
  if (label.indexOf("review-dispatch:") === 0) return "DISPATCHED " + seg + " beef1234";
  if (label.indexOf("review-read:") === 0) return { clean: true, coverage_ok: true, findings: [], draft_sha1: "a" };
  if (label.indexOf("artifact-check:") === 0) return { match: true };
  if (label.indexOf("fix:") === 0) return "FIXED " + seg;
  if (label.indexOf("draft-probe:") === 0) return { present: true };
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
  try {
    const result = await __workflowMain__(agent, pipeline, log, SEGS_ARGS);
    process.stdout.write(JSON.stringify({ result: result, calls: callsLog, promptsByLabel: promptsByLabel }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.message || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def run(*, tmp_path: Path, segs: list, chunk_reply: str | None, recheck_reply: str | None,
        max_fix_rounds: int = 1, batch_agent_cap: int = 100000, timeout: int = 30) -> dict:
    """Returns {ok, out, stderr}. `chunk_reply`/`recheck_reply` are templates in
    which `<seg>` is substituted with the calling segment's id."""
    src = instantiate(max_fix_rounds=max_fix_rounds, batch_agent_cap=batch_agent_cap)
    harness = (
        HARNESS.replace("__WRAPPED_SOURCE__", _wrap(src))
        .replace("__SEGS_JSON__", json.dumps(segs))
        .replace("__WAIT_REPLIES_JSON__", json.dumps({"chunk": chunk_reply, "recheck": recheck_reply}))
    )
    p = tmp_path / "wait_chunking_harness.js"
    p.write_text(harness, encoding="utf-8")
    assert NODE is not None
    proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        return {"ok": False, "out": None, "stderr": proc.stderr}
    return {"ok": True, "out": json.loads(proc.stdout), "stderr": proc.stderr}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

POLL_RE = re.compile(r"^end=\$\(\(SECONDS \+ (\d+)\)\);")


def poll_line(prompt: str) -> str:
    """The single bash poll command line of a CHUNK prompt."""
    hits = [ln for ln in prompt.splitlines() if ln.startswith("end=$((SECONDS +")]
    assert len(hits) == 1, f"expected exactly one poll command line, got {len(hits)}:\n{prompt}"
    return hits[0]


def chunk_seconds(prompt: str) -> int:
    m = POLL_RE.match(poll_line(prompt))
    assert m is not None, f"poll line does not declare an elapsed bound:\n{poll_line(prompt)}"
    return int(m.group(1))


def labels(out: dict) -> list:
    return [c["label"] for c in out["calls"]]


def converged_segs(out: dict) -> list:
    """The workflow returns `converged` as a list of per-segment RECORDS
    ({seg, converged, rounds}), not bare ids -- comparing the list against
    ["seg01"] silently fails for the wrong reason."""
    return [e["seg"] for e in out["result"]["converged"]]


def chunk_prompts(out: dict, label: str) -> list:
    assert label in out["promptsByLabel"], (
        f"no calls recorded at label {label!r}; labels seen: {sorted(set(labels(out)))}"
    )
    return out["promptsByLabel"][label]


# A run where the artifact lands only AFTER the whole chunk budget is spent --
# the frozen `ssk-w5-smoke-116` fixture, expressed as a fixture input.
def _late_landing_run(tmp_path, segs=("seg01",)) -> dict:
    return run(tmp_path=tmp_path, segs=list(segs),
               chunk_reply="PENDING <seg>", recheck_reply="READY <seg>")


# ---------------------------------------------------------------------------
# 1. The actual #348 fix: an artifact that lands after the last chunk is READ
# ---------------------------------------------------------------------------

def test_artifact_landing_after_the_chunk_budget_still_converges(tmp_path):
    """THE regression. Every chunk says PENDING; the canonical gate passes only
    at the post-exhaustion re-check. On the unfixed template this returns
    translate-timeout with a clean artifact sitting unread on disk."""
    res = _late_landing_run(tmp_path)
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    assert converged_segs(out) == ["seg01"], (
        "a segment whose artifact landed after the last wait chunk did not converge; "
        f"result={out['result']}"
    )
    assert out["result"]["failed"] == []


def test_recheck_runs_at_both_wait_sites(tmp_path):
    """The translate wait and the review wait each get their own re-check --
    #348 was reported against the review site, but the translate site has the
    identical shape and the worse consequence."""
    res = _late_landing_run(tmp_path)
    assert res["ok"], f"run threw: {res['stderr']}"
    seen = set(labels(res["out"]))
    assert "wait-recheck:seg01" in seen, f"no translate-wait re-check; labels={sorted(seen)}"
    assert any(l.startswith("review-wait-recheck:seg01:r") for l in seen), (
        f"no review-wait re-check; labels={sorted(seen)}"
    )


def test_recheck_that_is_still_not_ready_times_out_as_before(tmp_path):
    """The re-check ADDS a chance to succeed; it must not remove the timeout.
    Reason strings are unchanged, because select_segments.py's
    'non-terminal -> recoverable' rule and every recovery doc key off them."""
    res = run(tmp_path=tmp_path, segs=["seg01"],
              chunk_reply="PENDING <seg>", recheck_reply="PENDING <seg>")
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    assert converged_segs(out) == []
    assert len(out["result"]["failed"]) == 1
    assert out["result"]["failed"][0]["reason"] == "translate-timeout"


def test_recheck_does_not_poll(tmp_path):
    """The re-check is ONE immediate evaluation of the canonical gate. If it
    polled, it would be a ninth chunk and could itself hit the 600 s cap."""
    res = _late_landing_run(tmp_path)
    assert res["ok"], f"run threw: {res['stderr']}"
    for prompt in chunk_prompts(res["out"], "wait-recheck:seg01"):
        assert "end=$((SECONDS +" not in prompt, f"re-check polls:\n{prompt}"
        assert "sleep" not in prompt, f"re-check sleeps:\n{prompt}"
        assert "while true" not in prompt, f"re-check loops:\n{prompt}"


def test_recheck_uses_the_same_accept_gate_as_the_chunks(tmp_path):
    """Composed once and shared, so the re-check can never drift into a weaker
    gate than the poll it backs up -- the failure mode would be a false GREEN
    (accepting an artifact the poll would have rejected), which is the one
    direction this pipeline cannot recover from."""
    res = _late_landing_run(tmp_path)
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]

    for chunk_label, recheck_label in [
        ("wait:seg01", "wait-recheck:seg01"),
        ("review-wait:seg01:r1", "review-wait-recheck:seg01:r1"),
    ]:
        chunk = poll_line(chunk_prompts(out, chunk_label)[0])
        # ACCEPT is everything between the loop head and the first `&& exit 0`.
        m = re.search(r"while true; do (.*?) >/dev/null 2>&1 && exit 0;", chunk)
        assert m is not None, f"chunk poll has no suppressed ACCEPT gate:\n{chunk}"
        accept = m.group(1)
        recheck = chunk_prompts(out, recheck_label)[0]
        assert accept in recheck, (
            f"re-check at {recheck_label} does not run the chunk's exact ACCEPT gate.\n"
            f"chunk ACCEPT: {accept}\nre-check prompt:\n{recheck}"
        )


def test_recheck_still_runs_after_a_fail_sentinel(tmp_path):
    """A FAILED chunk ends the polling early but NOT the segment: the template's
    own rule is that a valid canonical always wins over any sentinel, and the
    fail sentinel only means the driver did not promote -- not that nothing
    landed."""
    res = run(tmp_path=tmp_path, segs=["seg01"],
              chunk_reply="FAILED <seg>", recheck_reply="READY <seg>")
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    assert "wait-recheck:seg01" in labels(out), "a FAILED chunk skipped the re-check"
    assert converged_segs(out) == ["seg01"]
    # ...and it did so WITHOUT burning the remaining chunk budget.
    assert len(chunk_prompts(out, "wait:seg01")) == 1, (
        "a FAILED chunk did not stop the chunk loop"
    )


# ---------------------------------------------------------------------------
# 2. Chunking: no call approaches the cap, and the bound is SPENT not EXTENDED
# ---------------------------------------------------------------------------

def test_no_single_chunk_approaches_the_bash_call_cap(tmp_path):
    """The constraint that forced chunking. 600 s is a measured hard clamp: a
    call declaring more is killed at 600 s with exit 143 no matter what timeout
    the agent passes."""
    res = _late_landing_run(tmp_path)
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    for label in ["wait:seg01", "review-wait:seg01:r1"]:
        for i, prompt in enumerate(chunk_prompts(out, label), start=1):
            sec = chunk_seconds(prompt)
            assert 0 < sec < BASH_CALL_CAP_SEC, (
                f"{label} chunk {i} declares {sec}s, which the Bash tool would clamp "
                f"at {BASH_CALL_CAP_SEC}s -- this is #348"
            )


def test_chunk_bounds_sum_to_exactly_the_declared_wait_bound(tmp_path):
    """The property a per-chunk cap check would MISS. Flat chunks of
    WAIT_CHUNK_SEC each would poll for longer than WAIT_BOUND_SEC -- not
    spending the declared bound but silently extending it, which breaks the one
    contract WAIT_BOUND_SEC exists to state and would make every downstream
    'the wait is bounded by 3450 s' doc false."""
    res = _late_landing_run(tmp_path)
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    for label in ["wait:seg01", "review-wait:seg01:r1"]:
        total = sum(chunk_seconds(p) for p in chunk_prompts(out, label))
        assert total == EXPECTED_WAIT_BOUND_SEC, (
            f"{label} chunks sum to {total}s, not the declared bound "
            f"{EXPECTED_WAIT_BOUND_SEC}s"
        )


def test_both_wait_sites_chunk_identically(tmp_path):
    """The two waits are deliberately parallel; a fix applied to one and not the
    other is the shape half this plugin's wait bugs have taken."""
    res = _late_landing_run(tmp_path)
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    translate = [chunk_seconds(p) for p in chunk_prompts(out, "wait:seg01")]
    review = [chunk_seconds(p) for p in chunk_prompts(out, "review-wait:seg01:r1")]
    assert len(translate) > 1, "the translate wait was not chunked at all"
    assert translate == review, (
        f"the two wait sites chunk differently: translate={translate} review={review}"
    )


def test_chunk_accept_gate_output_is_suppressed(tmp_path):
    """Without this the gate prints one `{"ready": false, ...}` line per
    iteration (~30 measured in the #348 transcript), so 'the marker is the last
    line' would be a claim about the tail of a noisy stream. Suppressed, a chunk
    emits zero or one line and that line is the marker. The gate's exit status
    -- the only thing acted on -- is unaffected."""
    res = _late_landing_run(tmp_path)
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    for label in ["wait:seg01", "review-wait:seg01:r1"]:
        for i, prompt in enumerate(chunk_prompts(out, label), start=1):
            line = poll_line(prompt)
            assert ">/dev/null 2>&1 && exit 0;" in line, (
                f"{label} chunk {i} does not suppress its in-loop ACCEPT output:\n{line}"
            )


def test_chunk_tool_timeout_instruction_fits_under_the_cap(tmp_path):
    """A chunk tells the agent what bash-tool timeout to pass. If that number
    exceeded the clamp the agent would be asking for something it cannot get and
    the chunk bound would stop being the real bound."""
    res = _late_landing_run(tmp_path)
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    prompt = chunk_prompts(out, "wait:seg01")[0]
    ms = [int(x) for x in re.findall(r"(\d+) ?ms\b", prompt)]
    assert ms, f"chunk prompt names no bash-tool timeout:\n{prompt}"
    for value in ms:
        assert value <= BASH_CALL_CAP_SEC * 1000, (
            f"chunk instructs a {value} ms tool timeout, above the {BASH_CALL_CAP_SEC * 1000} ms clamp"
        )


# ---------------------------------------------------------------------------
# 3. The new three-sentinel grammar keeps every #228/#308 property
# ---------------------------------------------------------------------------

def test_a_glued_fail_sentinel_never_converges(tmp_path):
    """#228's rule, re-pointed at the new grammar. `split("\\n")` breaks on LF
    and nothing else, so ANY character between prose and the sentinel keeps them
    on one line and defeats whole-line equality -- which is why the FAILED and
    PENDING guards are raw containment, evaluated BEFORE the READY test."""
    for glue in [" ", "\t", "\r", "\x0b", "\x0c", "\x1c", "\xa0", " ", "​", "x"]:
        res = run(tmp_path=tmp_path, segs=["seg01"],
                  chunk_reply="the job died" + glue + "FAILED <seg>\nREADY <seg>",
                  recheck_reply="PENDING <seg>")
        assert res["ok"], f"run threw: {res['stderr']}"
        out = res["out"]
        assert converged_segs(out) == [], (
            f"a FAILED sentinel glued behind {glue!r} was overridden by a trailing READY"
        )


def test_an_unparseable_chunk_reply_is_never_read_as_ready(tmp_path):
    """Fail-safe direction: anything not unambiguously READY costs at worst one
    more chunk of waiting, bounded by the chunk count."""
    res = run(tmp_path=tmp_path, segs=["seg01"],
              chunk_reply=None, recheck_reply="PENDING <seg>")
    assert res["ok"], f"run threw: {res['stderr']}"
    assert converged_segs(res["out"]) == []
    assert res["out"]["result"]["failed"][0]["reason"] == "translate-timeout"


def test_quoted_but_disavowed_ready_is_not_a_ready(tmp_path):
    """#308's boundary, unchanged: the LAST non-empty trimmed line decides, so a
    quoted success form the agent's own later prose overrides is rejected."""
    res = run(tmp_path=tmp_path, segs=["seg01"],
              chunk_reply="Quoting the requested success form:\nREADY <seg>\nThat is not my verdict.",
              recheck_reply="PENDING <seg>")
    assert res["ok"], f"run threw: {res['stderr']}"
    assert converged_segs(res["out"]) == []


def test_a_prose_decorated_ready_still_converges_on_the_first_chunk(tmp_path):
    """#308's other direction, and the happy path: the common case must not have
    become slower or stricter. One chunk, no re-check."""
    res = run(tmp_path=tmp_path, segs=["seg01"],
              chunk_reply="The poll confirmed the artifact (exit 0).\n\nREADY <seg>",
              recheck_reply="PENDING <seg>")
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    assert converged_segs(out) == ["seg01"]
    assert len(chunk_prompts(out, "wait:seg01")) == 1, "a READY first chunk kept polling"
    assert "wait-recheck:seg01" not in labels(out), "a READY chunk triggered a needless re-check"


def test_sibling_segment_id_prefix_collision_is_false_red_only(tmp_path):
    """SEG_ID_RE permits one id to prefix another, and the FAILED/PENDING guards
    are raw containment, so `FAILED seg10` contains `FAILED seg1`. Recorded, not
    assumed: it is false-RED only -- READY stays whole-line equality, so no
    false green -- and the re-check still runs, so the cost is bounded."""
    res = run(tmp_path=tmp_path, segs=["seg1"],
              chunk_reply="FAILED seg10", recheck_reply="READY <seg>")
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    # The collision fires (seg1's loop stops early)...
    assert len(chunk_prompts(out, "wait:seg1")) == 1
    # ...and the authoritative re-check keeps it from costing the segment.
    assert converged_segs(out) == ["seg1"]
