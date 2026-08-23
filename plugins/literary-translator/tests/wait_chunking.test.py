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


def read_template() -> str:
    return MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")


def instantiate(*, max_fix_rounds: int, batch_agent_cap: int, max_codex_jobs_per_batch: int = 100000,
                effort: str = FIXTURE_EFFORT, model: str = FIXTURE_MODEL,
                source: str | None = None) -> str:
    """The exact one-time substitution the template's header documents
    (duplicated, not imported, so this file stays self-contained like every
    sibling). `source`, when given, replaces the on-disk read -- this is how a
    mutation-proof test drives a MUTATED string through the real control flow
    without ever writing to the template file: this worktree is shared with
    concurrently running teammates, and an on-disk mutation would corrupt
    whatever suite they are running at that moment (mirrors
    tests/wait_chunking_batch_passes.test.py's own read_template(target)-or-
    source convention). #409 stage 0 -- max_codex_jobs_per_batch defaults
    generously (matching batch_agent_cap's own default below): this file
    exercises the wait/chunk machinery, not either preflight gate."""
    text = read_template() if source is None else source
    text = text.replace("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT)
    text = text.replace("{{RUN_ID}}", FIXTURE_RUN_ID)
    text = text.replace("{{SOURCE_LANG}}", FIXTURE_SOURCE_LANG)
    text = text.replace("{{TARGET_LANG}}", FIXTURE_TARGET_LANG)
    text = text.replace("{{MAX_FIX_ROUNDS}}", str(int(max_fix_rounds)))
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    text = text.replace("{{MAX_CODEX_JOBS_PER_BATCH}}", str(int(max_codex_jobs_per_batch)))
    text = text.replace("{{VERSE_POLICY_INSTRUCTION_BLOCK}}", json.dumps(FIXTURE_VERSE_POLICY)[1:-1])
    text = text.replace("{{CODEX_COMPANION_PATH_JSON}}", json.dumps(FIXTURE_COMPANION_PATH))
    text = text.replace("{{EFFORT}}", effort)
    text = text.replace("{{MODEL}}", model)
    # #412 -- PLUGIN_ROOT: empty = not opted into the redirect. This file
    # exercises the wait/chunk machinery, not the opt-in dispatch shape.
    # #607 -- a non-empty plugin root is now REQUIRED: the fix-scope audit
    # runs only from the plugin install tree, so the W5 template refuses to
    # start without one. This fixture used to substitute the empty value as
    # the documented "redirect opt-out"; that opt-out no longer exists.
    text = text.replace("{{PLUGIN_ROOT}}", json.dumps("/fixture/plugin/literary-translator"))
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
        max_fix_rounds: int = 1, batch_agent_cap: int = 100000, timeout: int = 30,
        source: str | None = None) -> dict:
    """Returns {ok, out, stderr}. `chunk_reply`/`recheck_reply` are templates in
    which `<seg>` is substituted with the calling segment's id. `source`, when
    given, drives a MUTATED template string through the real control flow
    instead of the on-disk content -- see instantiate()'s own docstring for
    why this is never written to disk."""
    src = instantiate(max_fix_rounds=max_fix_rounds, batch_agent_cap=batch_agent_cap, source=source)
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

# Round 9 (codex/round-9-review, HIGH): this file's chunk poll had no
# whole-line WHITELIST at all -- only POLL_RE's prefix match (the elapsed
# bound) plus a per-chunk numeric-range assertion, never a check that nothing
# ELSE rides on the rest of the line. Ported from
# tests/wait_chunking_batch_passes.test.py's own round-8/9 fix for the
# identical gap (independent copy, not an import -- this project's stated
# convention), with ONE real difference this file's template forces:
#
# mass-translate's ACCEPT gate can be TWO commands joined by " && "
# (translateAcceptCmd runs `draft_ready.py --expect-token ... && validate_
# draft.py ...`; reviewAcceptCmd is a single command) -- glossary/skeptic's
# gates are always exactly one. Measured against the REAL rendered lines
# (driven through the real template under Node, not reconstructed by hand):
# allowing exactly one such chain -- never zero-or-more, never a bare "&"
# alone -- fits both shapes and still excludes every character bash needs to
# open a SECOND, unrelated chain, a loop, or a subshell. The dispatch token
# embedded in `--expect-token RUN_ID:seg` also carries a literal ":" that
# glossary/skeptic's paths never do, so ":" joins the allowed charset here;
# it has no shell meaning on its own (not a chaining or substitution
# operator), so this widens what the charset ACCEPTS without widening what a
# loop/subshell could exploit.
_SAFE_COMMAND_TOKEN = r"[A-Za-z0-9_.:/-]+(?: [A-Za-z0-9_.:/-]+)*"
_SAFE_GATE_COMMAND = _SAFE_COMMAND_TOKEN + r"(?: && " + _SAFE_COMMAND_TOKEN + r")?"
SAFE_COMMAND_RE = re.compile(_SAFE_GATE_COMMAND)

# The chunk poll line's WHOLE grammar, fullmatch. Group 1 is the elapsed
# bound; group 2 is the ACCEPT gate (held to SAFE_COMMAND_RE's own positive
# shape, not a denylist of named tokens on an opaque capture -- see
# _assert_gate_command_cannot_hide_a_loop); group 3, when present, is the
# fail-fast sentinel path (waitChunkPrompt()'s own `failFast` local -- empty
# when `disp` is empty, "an empty DISP must DISABLE fail-fast" per
# mass_translate_driver_smoke.test.py's own lock on that path). The optional
# group is what codex flagged from the OTHER side: this clause is exactly why
# a single grammar shared with glossary/skeptic (which never have it) would
# have to be bent to fit -- kept as its own grammar in this file instead.
CHUNK_POLL_GRAMMAR_RE = re.compile(
    r"^end=\$\(\(SECONDS \+ (\d+)\)\); while true; do (" + _SAFE_GATE_COMMAND + r") >/dev/null 2>&1 && exit 0;"
    r"(?: \[ -f \"([A-Za-z0-9_./-]+)\" \] && \{ echo LT_FAIL_SENTINEL; exit 1; \};)? "
    r"\[ \$SECONDS -ge \$end \] && break; slp=\$\(\(end-SECONDS\)\); "
    r"\[ \$slp -gt 20 \] && slp=20; \[ \$slp -gt 0 \] && sleep \$slp; "
    r"done; echo LT_CHUNK_BOUND; exit 1$"
)

# The tokens that indicate a re-check ACTUALLY POLLS -- see wait_chunking_
# batch_passes.test.py's own NON_POLLING_FORBIDDEN_TOKENS for the full
# rationale (kept as cheap defense-in-depth alongside SAFE_COMMAND_RE, not
# the primary defense).
NON_POLLING_FORBIDDEN_TOKENS = ("seq", "sleep", "while", "end=$((SECONDS")

# Any loop construct ANYWHERE in the prompt, not just inside the one
# recognised poll line / re-check command -- see wait_chunking_batch_passes
# .test.py's own _LOOP_CONSTRUCT_ANYWHERE_RE for the full derivation
# (structural: while/until only match when "do" and "done" both follow later
# on the SAME line, so ordinary prose using those words alone never trips
# it). Round 10 (HIGH): this used to ALSO exempt the one legitimate
# `while true; do ... done` shape BY NAME, via a `(?!\s+true\b)` lookahead
# -- the exact denylist reasoning round 8/9 removed one level down,
# reintroduced here; `while true; do sleep 20; done` pushed as its own
# separate prompt line escaped. No exception is named here any more -- the
# legitimate poll line is exempted POSITIONALLY at this regex's own call
# site instead (the line already fullmatch-verified against
# CHUNK_POLL_GRAMMAR_RE is subtracted before this scan runs), matching
# wait_chunking_batch_passes.test.py's own round-10 fix.
_LOOP_CONSTRUCT_ANYWHERE_RE = re.compile(
    r"for\s+\w+\s+in\s+\$\(\s*seq\b"
    r"|for\s*\(\("
    r"|for\s+\w+\s+in\s+\{\d+\.\.\d+\}"
    r"|\b(?:while|until)\b[^\n]*?\bdo\b[^\n]*?\bdone\b"
)


def _assert_gate_command_cannot_hide_a_loop(command: str, where: str) -> None:
    """Same three-layer property as wait_chunking_batch_passes.test.py's own
    helper of this name: (1) SAFE_COMMAND_RE -- POSITIVE, structurally
    excludes any shell control-flow construct under any keyword; (2) EVERY
    `&&`-separated part starts with "python3 " -- both translateAcceptCmd
    and reviewAcceptCmd return `PY + " " + ...`, PY == "python3"; (3)
    NON_POLLING_FORBIDDEN_TOKENS, cheap defense-in-depth. See that file's
    docstring for the full reasoning and the stated residual (an argument to
    python3 itself that somehow blocks IT is not excluded by any layer).

    Round 10 (HIGH): layer 2 used to check only `command.startswith("python3
    ")` -- the WHOLE string's prefix. That closes the whole surface in
    wait_chunking_batch_passes.test.py, where SAFE_COMMAND_RE never allows
    an `&&`. It does NOT close it here: this file's own SAFE_COMMAND_RE was
    widened for translateAcceptCmd's genuine `&&` chain, and reviewAcceptCmd
    -- which has no `&&` of its own -- passed with ` && tail -f /dev/null`
    appended: exactly one `&&`, the whole string still starts with "python3
    ", "seq"/"sleep"/"while" never appear, all three layers passed, and the
    command blocks FOREVER precisely WHEN THE GATE SUCCEEDS -- every wait
    chunk then runs to the 600 s clamp, which is #348/#352 itself. Layer 2
    now checks EVERY `&&`-separated part, not just the string's own prefix
    -- splitting on " && " is unambiguous because SAFE_COMMAND_RE's own
    character class cannot contain "&" inside a token, so it can only ever
    appear as this literal chain separator."""
    assert SAFE_COMMAND_RE.fullmatch(command), (
        f"{where} is not a flat command invocation (or the one legitimate "
        f"`&&`-chain) -- it may carry a shell construct of some kind (a "
        f"loop, a pipe, a subshell, a background job) under any spelling:\n"
        f"{command}"
    )
    parts = command.split(" && ")
    assert all(part.startswith("python3 ") for part in parts), (
        f"{where} has a part that does not start with the real gate-command "
        f"builders' own fixed prefix (\"python3 \") -- it may be an "
        f"alternate bare command chained in beside the real check:\n{command}"
    )
    for token in NON_POLLING_FORBIDDEN_TOKENS:
        assert token not in command, (
            f"{where} contains {token!r}, which would mean it polls or loops "
            f"instead of running (or being evaluated) exactly once:\n{command}"
        )


def poll_line(prompt: str) -> str:
    """The single bash poll command line of a CHUNK prompt."""
    hits = [ln for ln in prompt.splitlines() if ln.startswith("end=$((SECONDS +")]
    assert len(hits) == 1, f"expected exactly one poll command line, got {len(hits)}:\n{prompt}"
    return hits[0]


def chunk_seconds(prompt: str) -> int:
    m = POLL_RE.match(poll_line(prompt))
    assert m is not None, f"poll line does not declare an elapsed bound:\n{poll_line(prompt)}"
    return int(m.group(1))


# The chunk's ACCEPT gate: everything between the loop head and the suppressed
# `&& exit 0`. Factored out (round 6) so the primary parity test and its own
# mutation-proof controls read the SAME extraction, rather than the primary
# test inlining a `re.search` this file's helpers otherwise never repeat.
ACCEPT_RE = re.compile(r"while true; do (.*?) >/dev/null 2>&1 && exit 0;")


def accept_gate(prompt: str) -> str:
    m = ACCEPT_RE.search(poll_line(prompt))
    assert m is not None, f"chunk poll has no suppressed ACCEPT gate:\n{poll_line(prompt)}"
    return m.group(1)


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


# The re-check's own command line, ALONE on its own line: waitRecheckPromptFor()
# (shared by BOTH wait sites -- see waitRecheckPrompt()/reviewWaitRecheckPrompt())
# pushes it as `lines.push(acceptCmd + " >/dev/null 2>&1")` -- no `while true`
# wrapper, no trailing `&& exit 0;` continuation, unlike the chunk's poll line
# the ACCEPT-extraction regex above lifts a command out of. A plain end-of-line
# anchor is therefore the correct, exact extraction here, not a coincidence of
# the two prompts sharing a substring -- round-6 sibling fix, see
# tests/wait_chunking_batch_passes.test.py's own recheck_command() for the full
# derivation (mass-translate carries an independent copy of this pattern by
# this project's own stated convention, not an import).
RECHECK_COMMAND_RE = re.compile(r"^(.*) >/dev/null 2>&1$", re.MULTILINE)


def recheck_command(prompt: str) -> str:
    hits = RECHECK_COMMAND_RE.findall(prompt)
    assert len(hits) == 1, f"expected exactly one re-check command line, got {len(hits)}:\n{prompt}"
    return hits[0]


def mutate(source: str, old: str, new: str) -> str:
    """One scoped substitution, with proof it applied.

    The assertion is not decoration. A mutation that silently matched nothing
    leaves the ORIGINAL source running, the test under proof passes, and the
    pass reads as "the mutation was caught" when nothing was ever mutated --
    a false-green that looks exactly like the real thing. Mirrors
    tests/wait_chunking_batch_passes.test.py's own mutate() -- this file had
    NO mutation-testing machinery at all before round 6, which is why its own
    containment-vs-identity gap (see test_recheck_uses_the_same_accept_gate_
    as_the_chunks below) went unnoticed for longer than the sibling file's."""
    count = source.count(old)
    assert count == 1, (
        f"mutation anchor must appear exactly once, found {count}: {old[:90]!r}"
    )
    return source.replace(old, new)


# A run where the artifact lands only AFTER the whole chunk budget is spent --
# the frozen `ssk-w5-smoke-116` fixture, expressed as a fixture input.
def _late_landing_run(tmp_path, segs=("seg01",), *, source: str | None = None) -> dict:
    return run(tmp_path=tmp_path, segs=list(segs),
               chunk_reply="PENDING <seg>", recheck_reply="READY <seg>", source=source)


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


def test_the_recheck_is_a_single_non_polling_check(tmp_path):
    """The re-check is ONE immediate evaluation of the canonical gate. If it
    polled, it would be a ninth chunk and could itself hit the 600 s cap.
    Non-polling is asserted against the emitted bash, and singular against
    the call log -- for BOTH wait sites, not just the translate one.

    Round-6: this used to be test_recheck_does_not_poll, checking only
    non-polling and only the translate site's re-check ("wait-recheck:seg01"),
    never the review site's ("review-wait-recheck:seg01:r1") and never the
    CALL COUNT -- so nothing here proved the re-check runs at most once, at
    either site. Measured (mutation the team lead built, this round):
    duplicating the re-check's own agent() CALL under the same label -- an
    extra, discarded await agent(waitRecheckPrompt(seg), {...label:
    "wait-recheck:"+seg}) injected before the real one -- passed 18/18, ALL
    GREEN, before this fix. That is a genuinely different mutation from
    duplicating the emitted COMMAND LINE inside one prompt: that shape is
    caught for the WRONG reason, by recheck_command()'s own
    `len(hits) == 1` guard, and gives a false RED that says nothing about
    call count. A re-check that runs twice is precisely the #348 defect it
    can itself hit the 600 s clamp -- so an unasserted 'ONCE' here is not a
    cosmetic gap, it is the property this whole file exists to hold."""
    res = _late_landing_run(tmp_path)
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    for recheck_label in ["wait-recheck:seg01", "review-wait-recheck:seg01:r1"]:
        recheck_prompts = chunk_prompts(out, recheck_label)
        assert len(recheck_prompts) == 1, (
            f"{recheck_label} ran {len(recheck_prompts)} re-checks for one wait; "
            f"the authoritative re-check is once per wait, or it is just "
            f"another chunk"
        )
        prompt = recheck_prompts[0]
        for forbidden in ("end=$((SECONDS +", "while true", "sleep", "$(seq "):
            assert forbidden not in prompt, (
                f"{recheck_label}'s re-check polls -- found {forbidden!r}:\n{prompt}"
            )


def test_recheck_uses_the_same_accept_gate_as_the_chunks(tmp_path):
    """Composed once and shared, so the re-check can never drift into a weaker
    gate than the poll it backs up -- the failure mode would be a false GREEN
    (accepting an artifact the poll would have rejected), which is the one
    direction this pipeline cannot recover from.

    Round-6 fix: this used to read `assert accept in recheck` -- CONTAINMENT
    against the re-check's whole multi-line PROMPT, not equality against its
    own command. Blind to a strictly WIDER command: a re-check that runs
    `acceptCmd + " --candidate-file /tmp/decoy.json"` still literally CONTAINS
    the chunk's own narrower gate string as a substring, so the old assertion
    passed it -- measured directly, both label pairs below, before this fix
    landed (see test_accept_gate_parity_is_mutation_proved_against_a_widened_
    recheck_command, this file's own control for exactly that shape; ported
    from tests/wait_chunking_batch_passes.test.py's round-6 fix for the
    identical gap, itself measured, not assumed, to transfer to this file).
    Fixed by comparing against recheck_command()'s own single-line extraction
    of the re-check's ACTUAL command.

    Equality is legitimate here, not merely convenient, for BOTH label pairs
    looped below -- checked against source, not assumed to transfer just
    because it held for the sibling file: translateAcceptCmd(seg) and
    reviewAcceptCmd(seg, roundLabel) are pure functions of the SAME seg/
    roundLabel both getVerifiedReview() and reviewFixLoop's translate wait
    pass to their own chunk-loop call and their own re-check call, within one
    invocation. The review site's extra roundLabel argument (the thing that
    could have broken equality, since it is a retry index the translate path
    does not carry) is threaded through to BOTH reviewWaitPrompt() and
    reviewWaitRecheckPrompt() identically, so it does not."""
    res = _late_landing_run(tmp_path)
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]

    for chunk_label, recheck_label in [
        ("wait:seg01", "wait-recheck:seg01"),
        ("review-wait:seg01:r1", "review-wait-recheck:seg01:r1"),
    ]:
        accept = accept_gate(chunk_prompts(out, chunk_label)[0])
        recheck = chunk_prompts(out, recheck_label)[0]
        assert recheck_command(recheck) == accept, (
            f"re-check at {recheck_label} does not run the chunk's exact ACCEPT "
            f"gate (character-identical, not merely containing it).\n"
            f"chunk ACCEPT gate: {accept!r}\n"
            f"re-check command:  {recheck_command(recheck)!r}\n"
            f"re-check prompt:\n{recheck}"
        )


@pytest.mark.parametrize("shape", ["replaced", "widened"], ids=["replaced", "widened"])
def test_accept_gate_parity_is_mutation_proved(tmp_path, shape):
    """The control for the test above, and the reason this file had none
    before round 6: `mutate(` had zero hits here, so nothing proved the
    parity assertion could discriminate ANY defect, replaced or widened.
    Mirrors tests/wait_chunking_batch_passes.test.py's own two controls,
    merged into one parametrized test since both mutation shapes need the
    same mutation site and the same per-label-pair loop.

    Both mutations target waitRecheckPromptFor()'s single shared emitted
    line -- `lines.push(acceptCmd + " >/dev/null 2>&1")`, unique in this
    template (verified: one occurrence) -- rather than either site's own
    wrapper, because BOTH wrapper functions splice into this ONE shared
    builder (waitRecheckPrompt() for translate, reviewWaitRecheckPrompt() for
    review): one mutation here changes both sites' re-check commands in one
    pass, so this test genuinely measures BOTH label pairs under the SAME
    mutation rather than assuming symmetry.

    "replaced" mirrors the sibling file's REPLACE control (an unrelated,
    strictly weaker gate -- a bare file-existence test). "widened" mirrors
    its WIDEN control: `--candidate-file` is a REAL flag both draft_ready.py
    and review_ready.py accept (validates an isolated candidate file instead
    of the canonical artifact) -- not a strawman, and exactly the shape a
    plausible, easy-to-miss-in-review edit would take. The narrower gate
    string stays a literal SUBSTRING of the widened one, so only the old
    CONTAINMENT assertion, not this equality one, could ever miss it -- and
    that is asserted directly below, for the widened shape only, rather than
    only claimed here: the un-fixed `accept in recheck` form still holds even
    after the mutation, which is exactly the blind spot the round-6 fix above
    exists to close."""
    mutated_line = {
        "replaced": 'lines.push("test -f /tmp/lt-weaker-gate >/dev/null 2>&1");',
        "widened": 'lines.push(acceptCmd + " --candidate-file /tmp/lt-decoy-candidate.json >/dev/null 2>&1");',
    }[shape]
    mutant = mutate(
        read_template(),
        'lines.push(acceptCmd + " >/dev/null 2>&1");',
        mutated_line,
    )
    res = _late_landing_run(tmp_path, source=mutant)
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]
    for chunk_label, recheck_label in [
        ("wait:seg01", "wait-recheck:seg01"),
        ("review-wait:seg01:r1", "review-wait-recheck:seg01:r1"),
    ]:
        accept = accept_gate(chunk_prompts(out, chunk_label)[0])
        recheck = chunk_prompts(out, recheck_label)[0]
        assert recheck_command(recheck) != accept, (
            f"MUTATION NOT CAUGHT ({shape}): re-check at {recheck_label} still "
            f"reads as carrying the chunk's own ACCEPT gate after its command "
            f"was {shape}, so the parity assertion is not reading the "
            f"executable command.\nre-check:\n{recheck}"
        )
        if shape == "widened":
            # The blind spot the round-6 fix above exists to close, made
            # executable rather than only claimed in the docstring: the
            # UN-FIXED containment form (`accept in recheck`, against the
            # whole multi-line prompt) still holds under this exact mutation,
            # so it is demonstrably not that form doing the catching above --
            # only the equality one, against recheck_command()'s own
            # single-line extraction, can tell the two apart.
            assert accept in recheck, (
                f"widened mutation stopped containing the chunk's own ACCEPT "
                f"gate as a literal substring of the re-check prompt, so it no "
                f"longer demonstrates the old containment form's blind spot at "
                f"{recheck_label}:\n{recheck}"
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

def test_loop_construct_anywhere_actually_discriminates():
    """Round 10: _LOOP_CONSTRUCT_ANYWHERE_RE's own discrimination table --
    see wait_chunking_batch_passes.test.py's own copy of this test for the
    full rationale. The exact case that must never again be exempted:
    `while true; do sleep 20; done`, standing alone, must be CAUGHT here."""
    must_catch = [
        ("the exact round-10 escape, standing alone",
         "while true; do sleep 20; done"),
        ("canonical seq for-loop", "for i in $(seq 1 45); do true; done"),
        ("different-var-name seq for-loop", "for j in $(seq 45); do true; done"),
        ("C-style for-loop", "for ((i=0; i<45; i++)); do true; done"),
        ("brace-range for-loop", "for i in {1..45}; do true; done"),
        ("while with a non-true condition", "while :; do :; done"),
        ("until-loop", "until false; do :; done"),
    ]
    for label, shape in must_catch:
        assert _LOOP_CONSTRUCT_ANYWHERE_RE.search(shape), (
            f"{label} must be recognised as a loop construct: {shape!r}"
        )

    # Negative controls: ordinary prose without a full do...done shape.
    # Deliberately NOT included: the legitimate elapsed-time poll line --
    # this regex must catch that shape too now (it is must_catch's "while
    # true" case above); the real poll line is exempted POSITIONALLY at the
    # call site, never by this regex refusing its own keyword.
    must_not_catch = [
        "Wait until it's done before returning.",
        "This is fine for now.",
        "While you wait, do nothing else.",
        "Keep polling until the bound is reached, then return.",
        "Run this for as long as needed, until it succeeds.",
    ]
    for shape in must_not_catch:
        assert _LOOP_CONSTRUCT_ANYWHERE_RE.search(shape) is None, (
            f"benign text must not be flagged as a loop construct: {shape!r}"
        )


def test_gate_command_helper_actually_discriminates():
    """Round 10 (HIGH): _assert_gate_command_cannot_hide_a_loop's own
    discrimination table. The exact escape that must never return: a
    single-command gate (reviewAcceptCmd's own shape, no `&&` of its own)
    with ` && tail -f /dev/null` appended -- one legitimate-looking `&&`,
    the whole string starts with "python3 ", no forbidden token appears, and
    the command blocks forever exactly when the real gate succeeds. Layer
    2's old prefix-only check let this through; it must check EVERY
    `&&`-separated part now."""
    real_translate_chain = (
        "python3 /fixture/x/scripts/draft_ready.py seg01 --expect-token run:seg01"
        " && python3 /fixture/x/scripts/validate_draft.py seg01"
    )
    real_review_single = "python3 /fixture/x/scripts/review_ready.py seg01 --expect-token run:seg01:r1"

    for label, cmd in [
        ("real translate chain", real_translate_chain),
        ("real review single command", real_review_single),
    ]:
        _assert_gate_command_cannot_hide_a_loop(cmd, label)  # must not raise

    hostile = [
        ("the exact round-10 escape: single command + hostile second half",
         real_review_single + " && tail -f /dev/null"),
        ("hostile first half, real command second",
         "tail -f /dev/null && " + real_review_single),
        ("a third chained command beyond the one allowed",
         real_translate_chain + " && python3 /fixture/x/scripts/extra.py"),
        ("a loop construct riding beside the real command",
         real_review_single + " && for i in $(seq 1 45); do sleep 20; done"),
    ]
    for label, cmd in hostile:
        try:
            _assert_gate_command_cannot_hide_a_loop(cmd, label)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"{label} was NOT caught: {cmd!r}")


def test_no_single_chunk_approaches_the_bash_call_cap(tmp_path):
    """The constraint that forced chunking. 600 s is a measured hard clamp: a
    call declaring more is killed at 600 s with exit 143 no matter what timeout
    the agent passes.

    Round 9: also the whole-line WHITELIST this file never had -- any
    surviving fixed-iteration or otherwise-unbounded construct riding on the
    poll line, whatever its spelling, checked as a fullmatch against
    CHUNK_POLL_GRAMMAR_RE, not a scan for one historical shape. The
    ACCEPT-gate group that grammar treats as opaque is held to its own
    positive shape (_assert_gate_command_cannot_hide_a_loop); the re-check's
    own command is held to the same property; and a loop construct emitted
    as its OWN, unrelated prompt line -- never touching either recognised
    line/command -- is caught by a whole-prompt scan, all three at both wait
    sites (translate and review)."""
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

            line = poll_line(prompt)
            m = CHUNK_POLL_GRAMMAR_RE.fullmatch(line)
            assert m, (
                f"{label} chunk {i}'s poll line does not match the pinned "
                f"elapsed-time-poll grammar -- something else is riding on "
                f"this line, fixed-iteration or otherwise:\n{line}"
            )
            _assert_gate_command_cannot_hide_a_loop(
                m.group(2), f"{label} chunk {i}'s ACCEPT gate command"
            )

    for recheck_label in ["wait-recheck:seg01", "review-wait-recheck:seg01:r1"]:
        for prompt in chunk_prompts(out, recheck_label):
            _assert_gate_command_cannot_hide_a_loop(
                recheck_command(prompt), f"{recheck_label} re-check command"
            )

    # Round 10: the chunk prompt's own legitimate poll line -- already
    # fullmatch-verified above -- is subtracted POSITIONALLY before this scan
    # runs, rather than the scan regex exempting its keyword by name (see
    # _LOOP_CONSTRUCT_ANYWHERE_RE's own comment for the bug that shape was).
    # The re-check prompts get no such subtraction and are scanned whole:
    # their own command is held to SAFE_COMMAND_RE above, which cannot
    # contain "while"/"until" at all, so there is nothing legitimate to
    # exempt there, and poll_line() would raise on a re-check prompt anyway
    # (exactly one `end=$((SECONDS +` line is required, and a re-check
    # prompt has none) -- calling it unconditionally on every label would
    # turn this into a false RED on every re-check prompt instead of a fix.
    for label in ["wait:seg01", "review-wait:seg01:r1"]:
        for prompt in chunk_prompts(out, label):
            line = poll_line(prompt)
            assert prompt.count(line) == 1, (
                f"{label} poll line appears {prompt.count(line)}x in its own "
                f"prompt; the positional carve-out below assumes exactly one "
                f"occurrence to subtract"
            )
            remainder = prompt.replace(line, "", 1)
            hit = _LOOP_CONSTRUCT_ANYWHERE_RE.search(remainder)
            assert hit is None, (
                f"{label}'s prompt contains a loop construct "
                f"({hit.group(0)!r}) somewhere in its text, not just in the "
                f"poll line already checked above:\n{prompt}"
            )
    for recheck_label in ["wait-recheck:seg01", "review-wait-recheck:seg01:r1"]:
        for prompt in chunk_prompts(out, recheck_label):
            hit = _LOOP_CONSTRUCT_ANYWHERE_RE.search(prompt)
            assert hit is None, (
                f"{recheck_label}'s prompt contains a loop construct "
                f"({hit.group(0)!r}) somewhere in its text, not just in the "
                f"one command already checked above:\n{prompt}"
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
    PENDING guards are raw containment, evaluated BEFORE the READY test.

    Round 6 (R6SANITIZER's sweep, confirmed by bytes): the LINE SEPARATOR and
    ZERO WIDTH SPACE entries below used to be raw pasted glyphs rather than
    chr() calls -- this plugin's own no-pasted-glyph convention, which
    skeptic_report.py already follows and this file did not. Not a live bug --
    the bytes decoded to exactly what the test needed -- but a raw glyph is
    visually indistinguishable from a plain space on skim, and authoring
    tooling has silently normalised one to a plain space before. Spelled via
    chr() now: a pure-ASCII runtime call that cannot be mis-typed or
    auto-converted into the character itself the way a string-literal escape
    can.

    Also asserts the glue character actually SENT is the intended codepoint,
    for every entry, not just the two that used to be raw. Before this, only
    converged_segs() was checked -- so if an editor, a merge tool, or a
    careless re-indent ever degraded U+2028 to a plain space, the test would
    keep passing while silently testing the plain-space case twice instead of
    the line-separator case at all: a green test that has stopped covering
    its own subject."""
    glue_chars = [
        (" ", 0x20), ("\t", 0x09), ("\r", 0x0D), ("\x0b", 0x0B), ("\x0c", 0x0C),
        ("\x1c", 0x1C), ("\xa0", 0xA0),
        (chr(0x2028), 0x2028),  # LINE SEPARATOR
        (chr(0x200B), 0x200B),  # ZERO WIDTH SPACE
        ("x", 0x78),
    ]
    for glue, expected_codepoint in glue_chars:
        assert len(glue) == 1 and ord(glue) == expected_codepoint, (
            f"glue fixture degraded: expected U+{expected_codepoint:04X}, got "
            f"{glue!r}"
        )
        res = run(tmp_path=tmp_path, segs=["seg01"],
                  chunk_reply="the job died" + glue + "FAILED <seg>\nREADY <seg>",
                  recheck_reply="PENDING <seg>")
        assert res["ok"], f"run threw: {res['stderr']}"
        out = res["out"]
        assert converged_segs(out) == [], (
            f"a FAILED sentinel glued behind U+{expected_codepoint:04X} ({glue!r}) "
            f"was overridden by a trailing READY"
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
