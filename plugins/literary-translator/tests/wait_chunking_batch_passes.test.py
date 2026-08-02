"""tests/wait_chunking_batch_passes.test.py -- #352 regression lock for the
glossary-pass and skeptic-pass waits.

THE BUG, in the shape it was measured.

`glossary-pass-wf.template.js` and `skeptic-pass-wf.template.js` each spent a
whole 900 s fragment wait inside ONE `agent()` call running ONE bash poll --
`seq 1 45` iterations with a 20 s sleep each. The agent's Bash tool CLAMPS a
single call at 600 000 ms regardless of the timeout the agent asks for: a
measured hard clamp, not a default (the failing call requested
`timeout: 3600000` and still came back `Exit code 143 / Command timed out after
10m 0s`). So every such wait was killed at ~600 s, the agent reported a
non-ready sentinel, and the batch was declared `glossary-pass-null` /
`skeptic-pass-null` -- *even when codex had written a complete, valid fragment
to disk moments later*. Nothing ever re-read it.

This is #348's defect, in the two templates #348 did not touch. `mass-translate`
was fixed in 1.16.1; these two kept the over-cap single poll until 1.16.2.

THE TWO PROPERTIES, and why neither alone is the fix.

1.  CHUNKING -- no single wait call may approach the 600 s cap. Asserted per
    chunk AND as a SUM: the chunk bounds must add up to EXACTLY the declared
    `WAIT_BOUND_SEC`, never more. A flat per-chunk constant would not SPEND the
    declared bound, it would silently EXTEND it (2 x 480 = 960 s against a
    declared 900 s), breaking the one contract WAIT_BOUND_SEC exists to state
    and falsifying every doc that quotes it. "Each chunk is under the cap" would
    pass that regression.

2.  THE AUTHORITATIVE RE-CHECK -- after the chunk budget is spent, the same
    ACCEPT gate runs ONCE more, non-polling, before the batch is declared
    not-ready. This is the half that actually recovers work. Chunking alone
    would have turned the observed 600 s kill into a success by accident while
    leaving the real hole open: a codex batch that finishes after the last
    chunk's poll ended has a complete, gate-valid fragment on disk that nothing
    ever reads.

WHERE THE EXPECTATIONS COME FROM.

Every duration, sentinel and command asserted below is extracted from the
prompts the REAL templates EMIT under Node, never from a hand-written fixture
and never from a helper's arithmetic. That distinction is the point of the file:
a build with a correct-but-unused `waitChunkSec()` beside a hard-coded 900 s
loop would satisfy any assertion made against the helper, and would ship exactly
the bug. `WAIT_BOUND_SEC` is the one number restated here as an independent
literal (EXPECTED_WAIT_BOUND_SEC), so the sum assertion cannot become a
tautology against a self-consistent pair of wrong constants.

RELATION TO THE SIBLING FILES.

  * tests/wait_chunking.test.py is the same lock for mass-translate-wf (#348).
    Deliberately a separate file with its own harness: that template's wait
    grammar has three verdicts (a detached codex_job.py driver can report
    FAILED), these two have exactly two, and its call sites are per-segment
    rather than per-batch.
  * tests/bounded_poll_present.test.py owns the STRUCTURAL half -- which builder
    composes which command, which call site is guarded, in what order. It reads
    source text. This file reads emitted output and observed control flow, and
    the two catch different things: a shared builder that is never called, and a
    called builder that emits the wrong bytes.
  * tests/batch_size_estimator.test.py owns the glossary preflight arithmetic in
    depth. What lives here is only the CROSS-GATE agreement -- the two
    templates' expressions and skeptic_setup.py's constants are three copies of
    one fact, and nothing else compares them to each other.

Harness note. Self-contained, like every sibling. It records EVERY prompt per
label as a LIST, because a wait's chunk calls deliberately reuse the existing
`<pass>:wait:<index>` label and a dict keyed by label would keep only the last
one -- which is exactly the per-chunk sequence these tests are about.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
TEMPLATES_DIR = ASSETS_DIR / "templates"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
GLOSSARY_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"
SKEPTIC_TEMPLATE = TEMPLATES_DIR / "skeptic-pass-wf.template.js"
SKEPTIC_SETUP_SCRIPT = SCRIPTS_DIR / "skeptic_setup.py"
PROFILE_EXAMPLE = ASSETS_DIR / "profile.example.yml"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; this test executes the real glossary-pass "
    "and skeptic-pass workflow templates' wait wiring under Node (no hard "
    "Node.js dependency for this plugin otherwise)",
)

# The measured Bash-tool clamp this whole file exists because of. A chunk that
# declared MORE than this many seconds would be killed mid-poll -- which is #352.
BASH_CALL_CAP_SEC = 600
# Both templates' shipped WAIT_BOUND_SEC, restated here as an INDEPENDENT
# literal on purpose: re-deriving it from a template would make the sum
# assertion below tautological (it would pass for any self-consistent pair of
# constants, including a pair that extends the bound past what every doc
# promises).
EXPECTED_WAIT_BOUND_SEC = 900
# What ONE wait costs in agent calls, worst case: WAIT_CHUNKS bounded chunks
# plus the one authoritative re-check. Independent literal for the same reason.
EXPECTED_WAIT_CALLS = 3

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_RUN_ID = "20260728T000000Z"


# ---------------------------------------------------------------------------
# Template instantiation. `source` is a parameter rather than always being read
# from disk, so the same test body can be driven against a MUTANT or against
# the pre-fix content read from git's object store at a FROZEN baseline commit
# (`PRE_RELEASE_BASELINE`, never `HEAD:` -- see read_template_at_baseline()
# below for why) -- which is how every gate in this file was watched failing.
# Mutating the file ON DISK is deliberately never done: this worktree is
# shared with concurrently running teammates, and an on-disk mutation would
# corrupt whatever suite they are running at that moment.
# ---------------------------------------------------------------------------

def instantiate_glossary(source: str, *, batch_agent_cap: int = 100000,
                         research_mode: str = "live") -> str:
    text = source
    text = text.replace("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT)
    text = text.replace("{{RUN_ID}}", FIXTURE_RUN_ID)
    text = text.replace("{{SOURCE_LANG}}", "French")
    text = text.replace("{{TARGET_LANG}}", "Russian")
    text = text.replace("{{RESEARCH_MODE}}", research_mode)
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    text = text.replace("{{EFFORT}}", "high")
    text = text.replace("{{CITATION_CONTENT_TYPES}}", "")
    assert "{{" not in text, "glossary fixture instantiation left an unresolved token"
    return text


def instantiate_skeptic(source: str, *, batch_agent_cap: int = 100000,
                        research_mode: str = "live") -> str:
    # research_mode is accepted and ignored: the skeptic pass has no citation
    # review and therefore no mode knob. Kept in the signature so the two
    # targets stay call-compatible and the shared test bodies need no branching.
    del research_mode
    text = source
    text = text.replace("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT)
    text = text.replace("{{RUN_ID}}", FIXTURE_RUN_ID)
    text = text.replace("{{SOURCE_LANG}}", "French")
    text = text.replace("{{PARTICLE_CONFIG}}", "fr.json")
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    assert "{{" not in text, "skeptic fixture instantiation left an unresolved token"
    return text


def _wrap(js_source: str) -> str:
    assert js_source.count("export const meta") == 1
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


# ---------------------------------------------------------------------------
# The mock. CHUNK_REPLIES is a LIST indexed by this wait's own chunk ordinal
# (its LAST entry repeating for any further chunk), and RECHECK_REPLY answers
# the re-check. The chunk ordinal resets on every dispatch, because a wait
# always follows one -- taken from the templates' real control flow rather than
# from any assumption about how many calls a wait spends. Which is the whole
# point: the chunk COUNT is a template constant under test, not a fixture input,
# so no fixture here may state it.
# ---------------------------------------------------------------------------
HARNESS = r"""
'use strict';
__WRAPPED_SOURCE__

const BATCHES_ARGS = __BATCHES_JSON__;
const CHUNK_REPLIES = __CHUNK_REPLIES_JSON__;
const RECHECK_REPLY = __RECHECK_REPLY_JSON__;
const PASS = __PASS_JSON__;

const promptsByLabel = {};
const callsLog = [];
let chunkOrdinal = 0;

function record(label, promptText) {
  if (!Object.prototype.hasOwnProperty.call(promptsByLabel, label)) promptsByLabel[label] = [];
  promptsByLabel[label].push(typeof promptText === "string" ? promptText : String(promptText));
}

// A wait call is a RE-CHECK iff its label contains "wait-recheck:"; every other
// wait-shaped label is a chunk. Written as containment rather than equality so
// it stays correct for both passes' label spellings without a per-target copy.
function waitKind(label) {
  if (label.indexOf(":wait-recheck:") !== -1) return "recheck";
  if (label.indexOf(":wait:") !== -1) return "chunk";
  return null;
}

// Attempt ordinals for the glossary citation review's two sentinels. Tracked
// rather than hard-coded to 0 so a fixture that drives the retry ladder still
// gets attempt-correct sentinels by construction.
const prepareCounts = {};
const reviewCounts = {};

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  record(label, promptText);
  callsLog.push({ label: label, agentType: opts.agentType || null, hasSchema: !!opts.schema });

  const parts = label.split(":");
  const idx = parts[parts.length - 1];

  const kind = waitKind(label);
  if (kind === "chunk") {
    const i = chunkOrdinal;
    chunkOrdinal += 1;
    const tmpl = (i < CHUNK_REPLIES.length) ? CHUNK_REPLIES[i] : CHUNK_REPLIES[CHUNK_REPLIES.length - 1];
    if (tmpl === null) return null;
    // split/join, NOT replace(): String.replace with a string pattern
    // substitutes only the FIRST occurrence, and some fixtures below carry the
    // placeholder twice. A first-only substitution would leave the second one
    // literal, so the test would still run -- while exercising a reply shape it
    // never meant to.
    return tmpl.split("<idx>").join(idx);
  }
  if (kind === "recheck") {
    if (RECHECK_REPLY === null) return null;
    return RECHECK_REPLY.split("<idx>").join(idx);
  }

  if (label === PASS + ":merge") return "MERGED (mock)";
  if (label === PASS + ":verify") return { verified: true, frozen_input_mismatch: false };
  if (label === PASS + ":frozen-check") return { frozen_input_mismatch: false };

  const step = parts[1];
  if (step === "precheck") return "ABSENT " + idx;
  if (step === "dispatch") {
    chunkOrdinal = 0;   // a new wait begins
    return "FRAGMENT " + idx;
  }
  if (step === "citation-prepare") {
    const attempt = prepareCounts[idx] || 0;
    prepareCounts[idx] = attempt + 1;
    return "EVIDENCE_READY " + idx + " ATTEMPT " + attempt;
  }
  if (step === "citation-review") {
    const attempt = reviewCounts[idx] || 0;
    reviewCounts[idx] = attempt + 1;
    return "CITATIONS_OK " + idx + " ATTEMPT " + attempt;
  }
  throw new Error("mock agent(): unrecognized label " + label);
}

async function pipeline(items, stage) {
  const out = [];
  for (const item of items) {
    out.push(await stage(item));
  }
  return out;
}
function log() {}

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, BATCHES_ARGS);
    process.stdout.write(JSON.stringify({
      result: result, calls: callsLog, promptsByLabel: promptsByLabel,
    }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.message || String(err)) + "\n");
    // The ordering half of a startup guard's claim -- "the throw happens
    // BEFORE anything is dispatched" -- is otherwise unassertable from the
    // Python side: run() below discards `out` on this exact path, so the call
    // log recorded up to the moment of the throw would vanish with it unless
    // it rides out on stderr instead. Labels only, not the full callsLog
    // objects: this line is meant to be greppable evidence of ordering, not a
    // second copy of the harness's own JSON contract.
    process.stderr.write(
      "HARNESS_CALLS_BEFORE_THROW: " + JSON.stringify(callsLog.map((c) => c.label)) + "\n"
    );
    process.exit(1);
  }
})();
"""


def glossary_batches(n: int = 1) -> list:
    return [
        {
            "index": i,
            "candidates": [{
                "name": f"Cand{i}", "freq": 3, "mid_sentence": False, "multiword": False,
                "abbrev": False, "n_segments": 2, "likely_name": True,
            }],
        }
        for i in range(n)
    ]


def skeptic_batches(n: int = 1) -> list:
    return [
        {
            "index": i,
            "assignments": [{
                "assignment_id": f"a{i}", "source_form": f"Cand{i}",
                "risk_classes": ["high_dispersion"], "windows": [], "windows_truncated": False,
            }],
        }
        for i in range(n)
    ]


class Target:
    """One workflow template, with everything a shared test body needs to drive
    it and to say what "the batch proceeded" means for it."""

    def __init__(self, *, name, pass_prefix, template, instantiate, batches,
                 ready_label, proceeded, not_ready_reason, ready_mutation):
        self.name = name
        self.pass_prefix = pass_prefix
        self.template = template
        self.instantiate = instantiate
        self.batches = batches
        # The label whose presence proves this batch got PAST the wait and on
        # into the pass's next real step. Different per target on purpose: a
        # shared "merged is true" check would be satisfied by a template that
        # skipped the wait entirely.
        self.ready_label = ready_label
        self.proceeded = proceeded
        self.not_ready_reason = not_ready_reason
        # Inverts the fail-safe default of waitChunkVerdict, so an ambiguous or
        # cut-short reply resolves to READY instead of PENDING. Target-specific
        # because the two templates express the default differently (an explicit
        # fallback return vs a ternary's else branch).
        self.ready_mutation = ready_mutation

    @property
    def chunk_label(self):
        return f"{self.pass_prefix}:wait:0"

    @property
    def recheck_label(self):
        return f"{self.pass_prefix}:wait-recheck:0"

    def __repr__(self):
        return f"<Target {self.name}>"


# Inverts the fail-safe default of waitChunkVerdict, so an ambiguous or
# cut-short reply resolves to READY instead of PENDING.
#
# ONE table for both targets since 1.16.2's post-review round ported the
# containment guard into the skeptic template: its waitChunkVerdict stopped
# being a ternary over sentinelVerdict's own fail sentinel and became the same
# guard-then-whole-line-READY shape glossary has, with the fail-safe default as
# an explicit trailing return. Per-target literals were right while the shapes
# genuinely differed and are wrong now -- two copies of one string is a drift
# waiting to happen, and mutate() asserting exactly one match is what turned the
# stale copy into a loud failure rather than a silent no-op mutation.
PENDING_DEFAULT_MUTATION = ('  return "pending"\n}', '  return "ready"\n}')


def _glossary_proceeded(out: dict) -> bool:
    """The glossary batch reached the citation review AND the merge."""
    labels = [c["label"] for c in out["calls"]]
    return (
        "glossary:citation-review:0" in labels
        and "glossary:merge" in labels
        and out["result"].get("merged") is True
    )


def _skeptic_proceeded(out: dict) -> bool:
    labels = [c["label"] for c in out["calls"]]
    return "skeptic:merge" in labels and out["result"].get("merged") is True


GLOSSARY = Target(
    name="glossary",
    pass_prefix="glossary",
    template=GLOSSARY_TEMPLATE,
    instantiate=instantiate_glossary,
    batches=glossary_batches,
    ready_label="glossary:citation-review:0",
    proceeded=_glossary_proceeded,
    not_ready_reason="glossary-pass-null",
    ready_mutation=PENDING_DEFAULT_MUTATION,
)

SKEPTIC = Target(
    name="skeptic",
    pass_prefix="skeptic",
    template=SKEPTIC_TEMPLATE,
    instantiate=instantiate_skeptic,
    batches=skeptic_batches,
    ready_label="skeptic:merge",
    proceeded=_skeptic_proceeded,
    not_ready_reason="skeptic-pass-null",
    ready_mutation=PENDING_DEFAULT_MUTATION,
)

TARGETS = [GLOSSARY, SKEPTIC]
TARGET_IDS = [t.name for t in TARGETS]


# ---------------------------------------------------------------------------
# Source access + mutation
# ---------------------------------------------------------------------------

def read_template(target: Target) -> str:
    return target.template.read_text(encoding="utf-8")


# The 1.16.1 release merge (#359) -- the last commit BEFORE #352. FROZEN, and
# for the same reason tests/retired_wording_pins.test.py's baseline is frozen.
#
# This read said `HEAD:` until a review round caught it. That was correct for
# exactly as long as 1.16.2 stayed uncommitted, and then quietly stopped being a
# pre-fix read at all: HEAD became the release commit, read_template_at_head()
# started returning POST-fix content, and the four red-evidence tests below
# degraded into skips -- still green, still reporting nothing. A reference that
# MOVES WITH THE THING UNDER TEST is not a baseline, whichever file it lives in.
#
# Frozen, those tests RUN permanently instead of skipping, and the red evidence
# for #352 stays executable rather than living only in a commit message.
PRE_RELEASE_BASELINE = "4343994b9de4f6fe979e6e5af711ed9ab11c4381"


def read_template_at_baseline(target: Target) -> str:
    """The template's PRE-#352 content, out of git's object store at the frozen
    baseline.

    Never the working tree and never a symbolic ref: teammates hold uncommitted
    edits to these paths, and HEAD advances past the change the moment it lands.
    """
    rel = target.template.relative_to(REPO_ROOT)
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{PRE_RELEASE_BASELINE}:{rel.as_posix()}"],
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    assert proc.returncode == 0, (
        f"git show failed for {rel} at the frozen baseline "
        f"{PRE_RELEASE_BASELINE[:12]}: {proc.stderr}\n"
        f"Do NOT re-point this at HEAD to make it pass -- that is the defect this "
        f"constant was frozen to remove."
    )
    return proc.stdout


def mutate(source: str, old: str, new: str) -> str:
    """One scoped substitution, with proof it applied.

    The assertion is not decoration. A mutation that silently matched nothing
    leaves the ORIGINAL source running, the test under proof passes, and the
    pass reads as "the mutation was caught" when nothing was ever mutated --
    a false-green that looks exactly like the real thing."""
    count = source.count(old)
    assert count == 1, (
        f"mutation anchor must appear exactly once, found {count}: {old[:90]!r}"
    )
    return source.replace(old, new)


def run(target: Target, *, tmp_path: Path, chunk_replies, recheck_reply,
        source: str | None = None, batches: list | None = None,
        batch_agent_cap: int = 100000, research_mode: str = "live",
        timeout: int = 30) -> dict:
    """Returns {ok, out, stderr, calls_before_throw}. `<idx>` in a reply is
    substituted with the calling batch's index. ok=False (with stderr) when the
    template threw before producing stdout -- the startup-guard path.
    calls_before_throw is the list of agent() call labels the harness itself
    recorded before that throw (empty when the throw preceded every call, which
    is what a startup guard must do) -- [] when the run succeeded, and None
    when the process never reached the harness's own catch block at all (e.g.
    a syntax error in the instantiated source), so there is nothing to report."""
    if isinstance(chunk_replies, str) or chunk_replies is None:
        chunk_replies = [chunk_replies]
    src = target.instantiate(
        read_template(target) if source is None else source,
        batch_agent_cap=batch_agent_cap, research_mode=research_mode,
    )
    harness = (
        HARNESS.replace("__WRAPPED_SOURCE__", _wrap(src))
        .replace("__BATCHES_JSON__", json.dumps(target.batches() if batches is None else batches))
        .replace("__CHUNK_REPLIES_JSON__", json.dumps(list(chunk_replies)))
        .replace("__RECHECK_REPLY_JSON__", json.dumps(recheck_reply))
        .replace("__PASS_JSON__", json.dumps(target.pass_prefix))
    )
    p = tmp_path / f"{target.name}_wait_harness.js"
    p.write_text(harness, encoding="utf-8")
    assert NODE is not None
    proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        m = re.search(r"^HARNESS_CALLS_BEFORE_THROW: (.*)$", proc.stderr, re.MULTILINE)
        calls_before_throw = json.loads(m.group(1)) if m else None
        return {
            "ok": False, "out": None, "stderr": proc.stderr,
            "calls_before_throw": calls_before_throw,
        }
    return {"ok": True, "out": json.loads(proc.stdout), "stderr": proc.stderr, "calls_before_throw": []}


# ---------------------------------------------------------------------------
# Emitted-output readers. Everything asserted in this file comes through one of
# these -- never through a template constant, and never through a helper's
# arithmetic re-implemented in Python.
# ---------------------------------------------------------------------------

CHUNK_ACCEPT_PATTERN = r"while true; do (.*?) >/dev/null 2>&1 && exit 0;"

# Every test file that needs to lift a chunk's ACCEPT gate back out of a
# rendered prompt carries its OWN copy of this pattern. That is deliberate and
# it is this project's stated convention (pytest.ini's own note, plus the
# "duplicated here, not imported, so this file stays self-contained" comment in
# a dozen siblings) -- a shared helper would mean one wrong edit silently
# changes what four files assert, and the independent copies are what make a
# drift visible at all.
#
# A review round proposed centralising it in tests/conftest.py instead. That is
# a real trade rather than an obvious win, so it is recorded rather than taken:
# conftest IS importable (the convention's stated reason -- `*.test.py` files
# are not importable by dotted name under --import-mode=importlib -- does not
# apply to it), but centralising removes the redundancy that makes drift
# detectable, in exchange for removing four lines of duplication.
#
# The parity test below is this repo's own answer to that trade, and the same
# one tests/sentinel_verdict_parity.test.py and
# tests/rejected_anywhere_parity.test.py already give for duplicated template
# code: keep the copies, and assert they agree.
CHUNK_ACCEPT_COPIES = (
    Path(__file__).resolve().parent / "wait_chunking.test.py",
    Path(__file__).resolve().parent / "glossary_approve_to_integration.test.py",
    Path(__file__).resolve().parent / "glossary_snapshot_ordering.test.py",
)


def test_every_copy_of_the_chunk_accept_pattern_agrees():
    """One grammar, four copies, no drift.

    The pattern encodes the emitted chunk poll's shape. A copy that drifts stops
    extracting the ACCEPT gate correctly in its own file -- and does so
    SILENTLY, because a regex that matches nothing makes its test fail with
    "no suppressed ACCEPT gate" rather than "your pattern is stale", which reads
    like a template regression and sends the reader to the wrong file."""
    missing = [p.name for p in CHUNK_ACCEPT_COPIES if not p.is_file()]
    assert not missing, f"expected sibling test files not found: {missing}"

    for path in CHUNK_ACCEPT_COPIES:
        text = path.read_text(encoding="utf-8")
        assert CHUNK_ACCEPT_PATTERN in text, (
            f"{path.name} no longer carries the chunk-ACCEPT pattern this file "
            f"pins:\n  {CHUNK_ACCEPT_PATTERN}\n"
            f"If the emitted chunk grammar really changed, update EVERY copy "
            f"together -- they are independent by convention, so a partial update "
            f"leaves the un-updated files silently extracting nothing."
        )


POLL_RE = re.compile(r"^end=\$\(\(SECONDS \+ (\d+)\)\);")
# The chunk's ACCEPT gate: everything between the loop head and the suppressed
# `&& exit 0`. The `>/dev/null 2>&1` is part of the emitted POLL, not of the
# gate contract, so it is matched rather than captured.
ACCEPT_RE = re.compile(CHUNK_ACCEPT_PATTERN)
# The bound the chunk prompt's own PROSE declares ("...this batch's total 900s
# wait..."), read back out so the declared bound and the spent bound can be
# compared without either being taken on trust.
DECLARED_BOUND_RE = re.compile(r"total (\d+)s wait")

# The syntax a FLAT command-line invocation can use: letters, digits, and the
# punctuation an absolute path/flag/filename needs (`.` `/` `_` `-`), tokens
# separated by single spaces. Deliberately excludes every character bash
# needs to CHAIN statements or open a subshell -- `;` `&` `|` backtick `$`
# `(` `)` quotes -- so no loop (under ANY keyword: `while`, `for`, `until`,
# spelled however), conditional, pipe, background job, or command
# substitution can be BUILT from it at all, rather than naming the ones
# anyone happened to think of. Shared between CHUNK_POLL_GRAMMAR_RE's own
# opaque group below and SAFE_COMMAND_RE, so a future widening of one is a
# widening of both, not a second copy to keep in sync by hand --
# NON_POLLING_FORBIDDEN_TOKENS below is the lesson this fragment applies
# before it could repeat a second time in the same file.
_SAFE_COMMAND_TOKEN = r"[A-Za-z0-9_./-]+(?: [A-Za-z0-9_./-]+)*"

# A re-check command (recheck_command()'s own single-line extraction) must
# match this shape WHOLESALE -- see _assert_gate_command_cannot_hide_a_loop.
SAFE_COMMAND_RE = re.compile(_SAFE_COMMAND_TOKEN)

# The chunk poll line's WHOLE grammar, as a WHITELIST rather than a scan for
# one bad spelling. Anchored both ends (fullmatch), so anything riding on this
# line beyond the one elapsed-time loop it describes -- a `seq N`-style
# fixed-iteration loop appended, inserted, or wrapped around it, under
# whatever argument count or spelling -- fails to match, instead of only the
# one `seq 1 N ... sleep M` shape that shipped pre-1.16.2. Group 1 is the
# elapsed bound (already read by POLL_RE/chunk_seconds()); group 2 is the
# ACCEPT gate command, captured rather than matched so its own text is held
# to the SAME positive shape as SAFE_COMMAND_RE above (built from the same
# fragment on purpose) -- see _assert_gate_command_cannot_hide_a_loop, called
# from test_no_emitted_poll_can_exceed_the_bash_call_cap.
#
# Round 8: group 2 used to be an unrestricted `(.+)`, checked afterward by a
# denylist of named tokens (`seq`/`sleep`/`while true`) -- codex's own
# independent review broke that with three constructs containing none of
# those words (`while :; do :; done;`, `for ((;;)); do :; done;`,
# `until false; do :; done;`), all of which fullmatched the outer grammar and
# hit zero banned tokens. The denylist had only moved one level inward, not
# been removed. `_SAFE_COMMAND_TOKEN` closes the category structurally
# instead: none of the three (or any other loop keyword) can be BUILT without
# at least one of `;` `&` `|` to sequence a header, body and terminator on one
# line, and none of those characters can appear in this group any more.
#
# Because the excluded characters are exactly the ones the outer grammar's
# own fixed suffix (` >/dev/null 2>&1 && exit 0; ...`) is made of, greedy vs
# lazy is now moot here: there is exactly one way to split "as many
# safe-charset tokens as possible" from the mandatory literal suffix that
# follows, regardless of the quantifier's greediness, because no safe token
# can ever consume a character the suffix needs. (CHUNK_ACCEPT_PATTERN's own
# `(.*?)` stays lazy for its own, different job -- reading the gate back OUT
# for comparison elsewhere in this file -- composing one pattern from the
# other would still force one job's requirement onto the other for no gain.)
CHUNK_POLL_GRAMMAR_RE = re.compile(
    r"^end=\$\(\(SECONDS \+ (\d+)\)\); while true; do (" + _SAFE_COMMAND_TOKEN + r") >/dev/null 2>&1 && exit 0; "
    r"\[ \$SECONDS -ge \$end \] && break; slp=\$\(\(end-SECONDS\)\); "
    r"\[ \$slp -gt 20 \] && slp=20; \[ \$slp -gt 0 \] && sleep \$slp; "
    r"done; echo LT_CHUNK_BOUND; exit 1$"
)

# The tokens that indicate a re-check ACTUALLY POLLS -- shared by every check
# below that asks "is this re-check non-polling", rather than each carrying
# its own hand-picked tuple. That used to be two copies (round 8's own
# simplifier review caught it): one used "$(seq " and "sleep " (narrower --
# require the literal "$(" prefix / a trailing space), the other bare "seq"
# and "sleep" (broader), and neither knew the other existed, so they had
# already drifted apart by the time anyone looked. Unlike CHUNK_ACCEPT_
# PATTERN's deliberate per-FILE independent copies just above -- this
# project's stated convention for guarding against SILENT cross-file drift,
# where a shared helper would let one wrong edit invisibly change what FOUR
# SEPARATE FILES assert -- these checks live in this ONE file, where a shared
# constant is not hidden from any call site's reader and any test run already
# exercises them together. There is no drift-visibility benefit to keeping
# them apart here, only the accidental divergence that just happened.
#
# Kept as CHEAP, LEGIBLE, defense-in-depth ON TOP OF SAFE_COMMAND_RE + the
# "python3 " prefix check below (_assert_gate_command_cannot_hide_a_loop),
# not as the primary defense any more: this bare-word scan alone still
# misses `for ((;;)); do :; done;` and `until false; do :; done;` (neither
# contains "seq", "sleep", or the bare "while" this tuple DOES catch) --
# measured, the same escape codex found in the chunk-poll grammar's own
# belt-and-suspenders check, independently present here too.
NON_POLLING_FORBIDDEN_TOKENS = ("seq", "sleep", "while", "end=$((SECONDS")

# Round 9 (codex, HIGH): CHUNK_POLL_GRAMMAR_RE/_assert_gate_command_cannot_
# hide_a_loop above both operate on ONE extracted line/command -- but the
# pre-round-8 code they replaced scanned the WHOLE rendered prompt
# (`re.findall(r"seq 1 (\d+)\).*?sleep (\d+)", prompt)`), so a fixed-
# iteration or otherwise-unbounded construct emitted as a SEPARATE line
# elsewhere in the prompt -- never touching the recognised poll line at all
# -- was visible to the old code and is invisible to the new. Measured: a
# `for i in $(seq 1 45); do sleep 20; done` pushed as its OWN, unrelated
# prompt line (the real poll line left untouched) passes clean today; the
# pre-round-8 code caught the identical injection.
#
# This restores that WHOLE-PROMPT coverage, widened the same way
# _SAFE_COMMAND_TOKEN widened the single-line check, rather than narrowing
# back to the one historical spelling. Structural, not a denylist of loop
# keywords alone: `while`/`until` only match when "do" and "done" both
# follow LATER ON THE SAME LINE, so ordinary prose using those words on
# their own ("wait until it's done", "while you wait") -- verified against
# every line of the real glossary/skeptic chunk and re-check prompts, and
# against five representative benign sentences using all three words --
# never trips it.
#
# Round 10 (HIGH): this regex used to ALSO exempt the one legitimate
# `while true; do ... done` shape BY NAME, via a `(?!\s+true\b)` lookahead --
# the exact denylist reasoning round 8/9 spent two rounds removing,
# reintroduced one level up. `while true; do sleep 20; done` pushed as its
# OWN, separate prompt line (the real poll line untouched) escaped: only
# that one spelling, because it is the one the legitimate poll line already
# uses -- the spelling a copy-paste regression is most likely to produce.
# This regex now names NO exception at all; the legitimate poll line is
# instead exempted POSITIONALLY, at the one call site below that has
# already fullmatched it against CHUNK_POLL_GRAMMAR_RE -- see the comment
# there. Simply deleting the old lookahead without adding that positional
# carve-out is a FALSE RED, not a fix: on the unmutated tree the legitimate
# poll line would then trip this scan on itself (measured).
_LOOP_CONSTRUCT_ANYWHERE_RE = re.compile(
    r"for\s+\w+\s+in\s+\$\(\s*seq\b"                                  # any seq-based for-loop
    r"|for\s*\(\("                                                     # C-style for ((init; cond; incr))
    r"|for\s+\w+\s+in\s+\{\d+\.\.\d+\}"                                # brace-range for X in {N..M}
    r"|\b(?:while|until)\b[^\n]*?\bdo\b[^\n]*?\bdone\b"                # any while/until...do...done, no exception
)


def _assert_gate_command_cannot_hide_a_loop(command: str, where: str) -> None:
    """Every place in this file that extracts an ACCEPT/re-check command and
    asks "could this be hiding a construct that defeats the bounded poll"
    calls this, rather than each running its own scan. Three layers, in order
    of how much they structurally close rather than merely happen to catch:

    1. SAFE_COMMAND_RE -- POSITIVE. `command` must be a flat, space-separated
       sequence of safe-charset tokens, with none of the characters bash
       needs to CHAIN statements or open a subshell. This closes the whole
       CATEGORY of shell control-flow constructs -- a loop under any
       keyword, a pipe, a background job, a command substitution --
       structurally, not by naming the ones anyone happened to think of.
    2. `command.startswith("python3 ")` -- both real gate-command builders
       (checkBatchCmd/checkCommand in glossary/skeptic) return `PY + " " +
       ...`, and PY == "python3" in both. This closes the residual layer 1
       alone leaves open: a BARE alternate command needing no shell
       metacharacter at all (`yes`, `tail -f <path>`, `sleep 999`)
       substituted whole in place of the real check. Whatever follows
       "python3 " is then only ever python3's own argv, never an
       independently executable shell word, as long as no shell
       metacharacter reaches it -- which layer 1 already guarantees.
    3. NON_POLLING_FORBIDDEN_TOKENS -- residual, cheap, defense-in-depth:
       catches the single most likely accidental regression (someone
       literally writing `sleep N` or reintroducing a `seq` loop) fast and
       legibly, even though layers 1+2 already structurally exclude it.

    KNOWN RESIDUAL, not attempted: an argument to python3 itself that somehow
    causes IT to block (e.g. `python3 -` reading an inherited stdin) is not
    excluded by any of the three layers -- a materially narrower and
    different risk than "an alternate shell construct", not the property
    this file's #352 lock is about."""
    assert SAFE_COMMAND_RE.fullmatch(command), (
        f"{where} is not a flat command invocation -- it may carry a shell "
        f"construct of some kind (a loop, a pipe, a subshell, a background "
        f"job) under any spelling:\n{command}"
    )
    assert command.startswith("python3 "), (
        f"{where} does not start with the real gate-command builders' own "
        f"fixed prefix (\"python3 \") -- it may be an alternate bare command "
        f"substituted whole in place of the real check:\n{command}"
    )
    for token in NON_POLLING_FORBIDDEN_TOKENS:
        assert token not in command, (
            f"{where} contains {token!r}, which would mean it polls or loops "
            f"instead of running (or being evaluated) exactly once:\n{command}"
        )


def labels(out: dict) -> list:
    return [c["label"] for c in out["calls"]]


def prompts(out: dict, label: str) -> list:
    assert label in out["promptsByLabel"], (
        f"no calls recorded at label {label!r}; labels seen: {sorted(set(labels(out)))}"
    )
    return out["promptsByLabel"][label]


def poll_line(prompt: str) -> str:
    """The single bash poll command line of a CHUNK prompt."""
    hits = [ln for ln in prompt.splitlines() if ln.startswith("end=$((SECONDS +")]
    assert len(hits) == 1, f"expected exactly one poll command line, got {len(hits)}:\n{prompt}"
    return hits[0]


def chunk_seconds(prompt: str) -> int:
    m = POLL_RE.match(poll_line(prompt))
    assert m is not None, f"poll line does not declare an elapsed bound:\n{poll_line(prompt)}"
    return int(m.group(1))


def accept_gate(prompt: str) -> str:
    m = ACCEPT_RE.search(poll_line(prompt))
    assert m is not None, f"chunk poll has no suppressed ACCEPT gate:\n{poll_line(prompt)}"
    return m.group(1)


# The re-check's own command line, ALONE on its own line: `batchWaitRecheckPrompt()`
# (both templates) pushes it as `lines.push(checkCmd + " >/dev/null 2>&1")` --
# no `while true` wrapper, no trailing `&& exit 0;` continuation, unlike the
# chunk's poll line ACCEPT_RE lifts a command out of. So a plain end-of-line
# anchor is the CORRECT, exact extraction here, not a coincidence of the two
# prompts sharing a substring -- and it is what makes an equality comparison
# against accept_gate()'s own extraction meaningful rather than comparing a
# substring to the wrong-shaped haystack.
RECHECK_COMMAND_RE = re.compile(r"^(.*) >/dev/null 2>&1$", re.MULTILINE)


def recheck_command(prompt: str) -> str:
    hits = RECHECK_COMMAND_RE.findall(prompt)
    assert len(hits) == 1, f"expected exactly one re-check command line, got {len(hits)}:\n{prompt}"
    return hits[0]


def declared_bound(prompt: str) -> int:
    m = DECLARED_BOUND_RE.search(prompt)
    assert m is not None, f"chunk prompt does not state the wait's total bound:\n{prompt}"
    return int(m.group(1))


# A run where the fragment lands only AFTER the whole chunk budget is spent:
# every chunk PENDING, the authoritative re-check READY. This is the frozen
# #352 report expressed as a fixture, and the default fixture for the shape
# tests below -- it is the ONE path that renders every chunk.
def exhausted_run(target: Target, tmp_path: Path, **kw) -> dict:
    res = run(target, tmp_path=tmp_path, chunk_replies=["PENDING <idx>"],
              recheck_reply="READY <idx>", **kw)
    assert res["ok"], f"run threw: {res['stderr']}"
    return res["out"]


# ===========================================================================
# 1 + 2. Chunking, read off the EMITTED prompts, against an independently
#        pinned bound.
# ===========================================================================

@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_emitted_chunk_bounds_sum_to_exactly_the_declared_wait_bound(target, tmp_path):
    """The property a per-chunk cap check would MISS, and the reason this file
    reads emitted text rather than calling waitChunkSec() itself.

    A build carrying a correct `waitChunkSec()` beside a chunk prompt that
    hard-codes 900 would pass every arithmetic test written against the helper
    and would ship exactly #352. So the durations are lifted out of the rendered
    bash, and their sum is compared against EXPECTED_WAIT_BOUND_SEC -- a literal
    declared at the top of this file, not read back from the template. Compared
    against the template's own constant this assertion would hold for any
    self-consistent pair of values, including flat chunks that EXTEND the bound
    (2 x 480 = 960 s) rather than spending it."""
    out = exhausted_run(target, tmp_path)
    durations = [chunk_seconds(p) for p in prompts(out, target.chunk_label)]

    assert len(durations) > 1, (
        f"{target.name}'s wait was not chunked at all -- one call means one poll, "
        f"which is #352"
    )
    total = sum(durations)
    assert total == EXPECTED_WAIT_BOUND_SEC, (
        f"{target.name}'s chunk bounds sum to {total}s ({durations}), not the "
        f"declared bound {EXPECTED_WAIT_BOUND_SEC}s. Under-spending silently "
        f"shortens every wait; over-spending silently extends it and falsifies "
        f"every doc that quotes the bound"
    )


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_the_declared_bound_the_prompt_states_is_the_bound_it_spends(target, tmp_path):
    """`WAIT_BOUND_SEC` pinned INDEPENDENTLY, at the two points the test above
    does not reach: the number each chunk prompt TELLS THE AGENT it is a slice
    of, and the number the re-check tells the agent has been spent.

    The third point of agreement -- the sum of what the chunks actually poll for
    -- is asserted once, in test_emitted_chunk_bounds_sum_to_exactly_the_declared
    _wait_bound above, against this same literal. It was duplicated here until a
    review round pointed out that a second copy adds no discrimination: both
    compare the same emitted durations to the same constant, so the copy can
    only ever fail alongside the original.

    Separate from that test rather than folded into it. That one proves the
    chunks SPEND 900 s; this one proves 900 s is also what the prompts CLAIM. A
    template that changed its bound consistently everywhere would pass the sum
    test forever -- the prose and the emitted bounds would simply agree with
    each other at the new value, and only the independent literal notices."""
    out = exhausted_run(target, tmp_path)
    chunk_prompts = prompts(out, target.chunk_label)

    stated = {declared_bound(p) for p in chunk_prompts}
    assert stated == {EXPECTED_WAIT_BOUND_SEC}, (
        f"{target.name}'s chunk prompts state the total wait as {sorted(stated)}s, "
        f"not {EXPECTED_WAIT_BOUND_SEC}s"
    )

    # The re-check names it too, and must name the SAME number: its whole
    # message to the agent is "that budget is spent, look once more".
    recheck = prompts(out, target.recheck_label)[0]
    assert str(EXPECTED_WAIT_BOUND_SEC) + "s wait budget" in recheck, (
        f"{target.name}'s re-check does not state the spent budget as "
        f"{EXPECTED_WAIT_BOUND_SEC}s:\n{recheck}"
    )


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_each_chunk_polls_for_what_is_left_of_the_budget_not_a_flat_slice(target, tmp_path):
    """The SHAPE of the split, derived entirely from emitted values.

    Chunk i polls for `min(chunk_size, remaining)`, where chunk_size is read off
    chunk 1 and remaining is what EXPECTED_WAIT_BOUND_SEC leaves after the
    earlier chunks. Reconstructed from the first emitted duration rather than
    from the template's WAIT_CHUNK_SEC, so the assertion stays independent of
    the constant it is checking the use of.

    This is what separates "the bounds happen to sum right" from "the last chunk
    is the remainder". Both are true today; only the second stays true if the
    bound stops dividing evenly."""
    out = exhausted_run(target, tmp_path)
    durations = [chunk_seconds(p) for p in prompts(out, target.chunk_label)]
    chunk_size = durations[0]

    expected = []
    remaining = EXPECTED_WAIT_BOUND_SEC
    while remaining > 0:
        expected.append(min(chunk_size, remaining))
        remaining -= expected[-1]
    # List equality covers the chunk COUNT as well as the per-chunk bounds: two
    # sequences of different length are never equal. An explicit length check
    # sat here until a review round pointed out it could not fail.
    assert durations == expected, (
        f"{target.name}'s chunks poll {durations} ({len(durations)} chunk(s)), not "
        f"the remaining-budget sequence {expected} ({len(expected)} chunk(s)) "
        f"implied by a {chunk_size}s chunk against a {EXPECTED_WAIT_BOUND_SEC}s bound"
    )


# ===========================================================================
# 4c. THE REGRESSION CATCHER. No emitted poll may exceed the clamp.
# ===========================================================================

def test_loop_construct_anywhere_actually_discriminates():
    """Round 10: _LOOP_CONSTRUCT_ANYWHERE_RE's own discrimination table, on
    synthetic fixtures, proven BEFORE trusting it against real templates
    below -- the gap round 10 found. The exact case that must never again be
    exempted: `while true; do sleep 20; done`, standing alone (as it would
    if pushed as a SEPARATE prompt line, never touching the recognised poll
    line at all), must be CAUGHT here -- a keyword-based carve-out for
    "while true" specifically is what let it through once already."""
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

    # Negative controls: ordinary prose using the same keywords without a
    # full do...done shape on one line. Deliberately NOT included here: the
    # legitimate elapsed-time poll line itself (`end=$((SECONDS + N)); while
    # true; do ... done`) -- this regex must catch THAT shape too now (it is
    # exactly must_catch's "while true" case above), the same as any other
    # while/until loop. The real poll line is exempted at the call site,
    # POSITIONALLY (subtracted before this regex ever sees it), never by this
    # regex refusing to recognise its own keyword -- see
    # test_no_emitted_poll_can_exceed_the_bash_call_cap's own comment for
    # why a nominal exemption here is exactly the round-10 bug.
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


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_no_emitted_poll_can_exceed_the_bash_call_cap(target, tmp_path):
    """#352 itself, stated as the one thing that must never be emitted again.

    Three separate ways an over-cap call could be asked for, all checked against
    what the prompt really says:
      * the chunk's own elapsed bound,
      * the bash-tool timeout the chunk INSTRUCTS the agent to pass (asking for
        one it cannot get would mean the chunk bound is not the real bound),
      * any surviving fixed-iteration or otherwise-unbounded construct riding
        on the poll line -- whatever its spelling -- whose real duration is
        invisible to both of the above, and is exactly the shape the
        pre-1.16.2 poll had. Checked as a WHITELIST of the poll line's whole
        grammar (CHUNK_POLL_GRAMMAR_RE, fullmatch), not a scan for the one
        `seq 1 N ... sleep M` spelling that shipped pre-1.16.2.

        That grammar's own ACCEPT-gate group is held to a POSITIVE shape too
        (_assert_gate_command_cannot_hide_a_loop, SAFE_COMMAND_RE), not a
        denylist of named tokens on an otherwise-opaque capture: round 8's
        first attempt at this file got exactly that wrong, and an
        independent review broke it with three named-token-free
        constructs -- see that function's own docstring for the full
        three-layer property this now asserts, and its own stated residual.

      * (round 9) a construct emitted as its OWN, SEPARATE prompt line,
        never touching the recognised poll line at all -- the two checks
        above only ever look at ONE extracted line/command each, where the
        pre-round-8 code scanned the WHOLE prompt. _LOOP_CONSTRUCT_ANYWHERE_RE
        restores that scope, widened rather than reverted to the one
        historical spelling.
    """
    out = exhausted_run(target, tmp_path)
    for i, prompt in enumerate(prompts(out, target.chunk_label), start=1):
        sec = chunk_seconds(prompt)
        assert 0 < sec < BASH_CALL_CAP_SEC, (
            f"{target.name} chunk {i} declares {sec}s, which the Bash tool would "
            f"clamp at {BASH_CALL_CAP_SEC}s -- this is #352"
        )

        ms = [int(x) for x in re.findall(r"(\d+) ?ms\b", prompt)]
        assert ms, f"{target.name} chunk {i} names no bash-tool timeout:\n{prompt}"
        for value in ms:
            assert value <= BASH_CALL_CAP_SEC * 1000, (
                f"{target.name} chunk {i} instructs a {value} ms tool timeout, above "
                f"the {BASH_CALL_CAP_SEC * 1000} ms clamp"
            )

        line = poll_line(prompt)
        m = CHUNK_POLL_GRAMMAR_RE.fullmatch(line)
        assert m, (
            f"{target.name} chunk {i}'s poll line does not match the pinned "
            f"elapsed-time-poll grammar -- something else is riding on this "
            f"line, fixed-iteration or otherwise:\n{line}"
        )
        # The one sub-part CHUNK_POLL_GRAMMAR_RE itself treats as opaque (the
        # ACCEPT gate command) is held to its own positive shape here -- see
        # _assert_gate_command_cannot_hide_a_loop's own docstring for why this
        # is three layers, not a token scan alone.
        gate_cmd = m.group(2)
        _assert_gate_command_cannot_hide_a_loop(
            gate_cmd, f"{target.name} chunk {i}'s ACCEPT gate command"
        )

    # The re-check is non-polling by construction (batchWaitRecheckPrompt's own
    # comment: no `end=`, no loop, no sleep) -- so its whole command line is
    # held to the SAME property as the chunk's own ACCEPT gate above, not just
    # the one fixed-iteration spelling the chunk poll line's grammar pins
    # against. This is the call that runs on the recovery path nobody watches.
    for prompt in prompts(out, target.recheck_label):
        command = recheck_command(prompt)
        _assert_gate_command_cannot_hide_a_loop(
            command, f"{target.name} re-check command"
        )

    # Round 9: a loop construct emitted as its OWN, unrelated prompt line --
    # never touching the poll line CHUNK_POLL_GRAMMAR_RE recognises, and never
    # touching the re-check's own single command line -- is invisible to
    # every check above, each of which only ever looks at ONE already-
    # identified line/command. Scanned over the WHOLE prompt at both wait
    # sites, restoring the coverage the pre-round-8 code had (it scanned the
    # whole prompt too) while still catching every spelling round 8's own fix
    # widened for, not just the one historical instance.
    #
    # Round 10: the chunk prompt's own legitimate poll line -- already
    # fullmatch-verified above against CHUNK_POLL_GRAMMAR_RE -- is subtracted
    # from the text POSITIONALLY before this scan runs, rather than the scan
    # regex exempting its keyword by name (that was the round-10 bug: see
    # _LOOP_CONSTRUCT_ANYWHERE_RE's own comment). The re-check prompt gets no
    # such subtraction: its own command is held to SAFE_COMMAND_RE above,
    # which cannot contain "while"/"until" at all, so there is nothing
    # legitimate to exempt there, and poll_line() would raise on a re-check
    # prompt anyway (it asserts exactly one `end=$((SECONDS +` line, and a
    # re-check prompt has none) -- calling it unconditionally on both labels
    # would turn this into a false RED on every re-check prompt instead of a
    # fix.
    for prompt in prompts(out, target.chunk_label):
        line = poll_line(prompt)
        assert prompt.count(line) == 1, (
            f"{target.name} chunk poll line appears {prompt.count(line)}x in "
            f"its own prompt; the positional carve-out below assumes exactly "
            f"one occurrence to subtract"
        )
        remainder = prompt.replace(line, "", 1)
        hit = _LOOP_CONSTRUCT_ANYWHERE_RE.search(remainder)
        assert hit is None, (
            f"{target.name} {target.chunk_label}'s prompt contains a loop "
            f"construct ({hit.group(0)!r}) somewhere in its text, not just "
            f"in the poll line already checked above:\n{prompt}"
        )
    for prompt in prompts(out, target.recheck_label):
        hit = _LOOP_CONSTRUCT_ANYWHERE_RE.search(prompt)
        assert hit is None, (
            f"{target.name} {target.recheck_label}'s prompt contains a loop "
            f"construct ({hit.group(0)!r}) somewhere in its text, not just "
            f"in the one command already checked above:\n{prompt}"
        )

    # The chunk's in-loop gate output must stay suppressed. Without it the gate
    # prints one JSON line per iteration, and "the marker is the last line"
    # becomes a claim about the tail of a noisy stream rather than about a chunk
    # that emits zero or one line.
    for i, prompt in enumerate(prompts(out, target.chunk_label), start=1):
        assert ">/dev/null 2>&1 && exit 0;" in poll_line(prompt), (
            f"{target.name} chunk {i} does not suppress its in-loop ACCEPT output:\n"
            f"{poll_line(prompt)}"
        )


# ===========================================================================
# 3. LATE LANDING CHANGES THE OUTCOME -- the fix, per target caller.
# ===========================================================================

@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_a_fragment_landing_after_the_chunk_budget_still_proceeds(target, tmp_path):
    """THE regression, driven end to end at each caller.

    Every chunk says PENDING; the ACCEPT gate passes only at the
    post-exhaustion re-check. On the unfixed template this batch is reported
    not-ready with a complete, gate-valid fragment sitting unread on disk.

    Asserted at the CALLER, not at the wait: what has to change is what the
    batch goes on to do -- glossary into the citation review and the merge,
    skeptic into the merge. A wait that returned "ready" into a caller that
    ignored it would satisfy any assertion made about the wait alone."""
    out = exhausted_run(target, tmp_path)

    assert target.proceeded(out), (
        f"{target.name}: a batch whose fragment landed after the last wait chunk did "
        f"not proceed; result={out['result']}, calls={labels(out)}"
    )
    assert target.ready_label in labels(out), (
        f"{target.name} never reached {target.ready_label}; calls={labels(out)}"
    )
    assert out["result"].get("reason") is None, (
        f"{target.name} reported reason={out['result'].get('reason')!r} on a batch "
        f"whose fragment did land"
    )


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_a_recheck_that_is_still_not_ready_reports_not_ready_as_before(target, tmp_path):
    """The re-check ADDS a chance to succeed; it must not remove the failure.
    The reason strings are unchanged on purpose -- the recovery docs key off
    them, and #352 is not a licence to relabel every not-ready batch."""
    res = run(target, tmp_path=tmp_path, chunk_replies=["PENDING <idx>"],
              recheck_reply="PENDING <idx>")
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]

    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [0]
    assert target.ready_label not in labels(out), (
        f"{target.name} proceeded past a wait that never became ready; calls={labels(out)}"
    )


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_the_recheck_verdict_and_not_the_loop_decides_the_batch(target, tmp_path):
    """MUTATION-PROVED, and the mutation is what makes the test above mean
    something. Ignoring the re-check's own answer -- running it, then deciding
    on the verdict the chunk loop ended with -- looks perfectly healthy: the
    re-check still appears in the call log, still issues the right command, and
    every structural assertion in this file still passes. Only the OUTCOME
    differs, and only on the path that matters."""
    mutant = mutate(
        read_template(target),
        "verdict = waitChunkVerdict(recheck, batch.index)",
        'verdict = "pending"',
    )
    res = run(target, tmp_path=tmp_path, chunk_replies=["PENDING <idx>"],
              recheck_reply="READY <idx>", source=mutant)
    assert res["ok"], f"mutant run threw: {res['stderr']}"
    out = res["out"]

    assert target.recheck_label in labels(out), (
        "the mutation should leave the re-check CALL in place -- if it did not, "
        "this proves nothing about whose verdict decides"
    )
    assert not target.proceeded(out), (
        f"MUTATION NOT CAUGHT: {target.name} still proceeded with the re-check's "
        f"verdict discarded, so nothing here is actually testing that the "
        f"re-check decides the batch; result={out['result']}"
    )


# ===========================================================================
# 4a. A READY CHUNK STOPS IMMEDIATELY -- at EVERY chunk index.
# ===========================================================================

def _ready_at(chunk_index: int, total_hint: int = 8) -> list:
    """Chunk replies where chunk `chunk_index` (1-based) is the first READY.

    `total_hint` only pads the list; the LAST entry repeats for any further
    chunk, so the fixture never states how many chunks the template makes --
    which is a template constant under test, not a fixture input."""
    return ["PENDING <idx>"] * (chunk_index - 1) + ["READY <idx>"] * total_hint


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_a_ready_chunk_stops_the_wait_at_every_chunk_index(target, tmp_path):
    """Parameterized over EVERY chunk index the template actually emits, which
    is discovered from a run rather than assumed.

    The sibling W5 lock only ever drives first-chunk success, and copying its
    coverage would inherit the hole: with two chunks configured, a final-chunk
    off-by-one passes every other test in this file. The defect this catches is
    conditioning the re-check on "the loop reached its final index" instead of
    on the VERDICT -- which reads as equivalent and is not. A batch whose
    fragment validated in the LAST chunk would then run the re-check anyway,
    spending an extra agent call on the NORMAL path to re-ask a question already
    answered -- and, worse here than in W5, that call re-runs a WRITE-CAPABLE,
    non-idempotent gate over a fragment that has already validated.

    WAIT_CALLS IS A CEILING, NOT A COST, and this is where that is proved. A
    wait costs ONE call when chunk 1 answers READY, two when chunk 2 does, and
    only three when the budget is exhausted and the re-check runs. So the total
    number of wait calls is asserted here per scenario -- not merely the chunk
    count, and not merely "no re-check fired". A build that ran every chunk
    regardless and simply ignored the later replies would satisfy a "no
    re-check" assertion while tripling what a healthy wait really spends, and
    the preflight ceiling would stop being an over-estimate of anything."""
    n_chunks = len(prompts(exhausted_run(target, tmp_path), target.chunk_label))
    assert n_chunks >= 2, f"{target.name} emits only {n_chunks} chunk(s); nothing to sweep"

    for k in range(1, n_chunks + 1):
        res = run(target, tmp_path=tmp_path, chunk_replies=_ready_at(k),
                  recheck_reply="PENDING <idx>")
        assert res["ok"], f"run threw at chunk {k}: {res['stderr']}"
        out = res["out"]

        assert len(prompts(out, target.chunk_label)) == k, (
            f"{target.name}: a READY at chunk {k} did not stop the loop -- "
            f"{len(prompts(out, target.chunk_label))} chunk(s) ran"
        )
        assert target.recheck_label not in labels(out), (
            f"{target.name}: a READY at chunk {k} still triggered the authoritative "
            f"re-check. On the normal path that spends an extra agent call to "
            f"re-ask an answered question, and re-runs a write-capable, "
            f"non-idempotent gate over a fragment that already validated; "
            f"calls={labels(out)}"
        )
        # The COST of this wait, stated as a number: k calls, no more.
        spent = len([lbl for lbl in labels(out)
                     if lbl in (target.chunk_label, target.recheck_label)])
        assert spent == k, (
            f"{target.name}: a wait answered at chunk {k} spent {spent} agent "
            f"call(s), not {k}. WAIT_CALLS ({EXPECTED_WAIT_CALLS}) is the CEILING a "
            f"preflight charges for the exhaustion path, never what an ordinary "
            f"wait costs; calls={labels(out)}"
        )
        # A "spent < ceiling" disjunct sat here and could not fail: the
        # assertion above already pins spent == k, and k only ranges over the
        # chunk indices, every one of which is below the ceiling by
        # construction. The property it was reaching for -- that the ordinary
        # path really is cheaper than exhaustion -- is asserted where it can
        # actually fail, in test_the_full_wait_cost_ladder_is_one_two_or_three_calls,
        # which measures the exhaustion path alongside the chunk paths.
        assert target.proceeded(out), (
            f"{target.name}: a READY at chunk {k} did not let the batch proceed; "
            f"result={out['result']}"
        )


# ===========================================================================
# 4b + 5. The re-check: one non-polling evaluation of the SAME gate.
# ===========================================================================

@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_the_recheck_is_a_single_non_polling_check(target, tmp_path):
    """A polling re-check would just be one more chunk and could itself hit the
    600 s cap -- which is the defect, not the fix. Non-polling is asserted
    against the emitted bash, and singular against the call log."""
    out = exhausted_run(target, tmp_path)
    recheck_prompts = prompts(out, target.recheck_label)

    assert len(recheck_prompts) == 1, (
        f"{target.name} ran {len(recheck_prompts)} re-checks for one wait; the "
        f"authoritative re-check is once per wait, or it is just another chunk"
    )
    prompt = recheck_prompts[0]
    for forbidden in NON_POLLING_FORBIDDEN_TOKENS:
        assert forbidden not in prompt, (
            f"{target.name}'s re-check polls -- found {forbidden!r}:\n{prompt}"
        )
    # The command line itself, held to the SAME positive shape as the chunk's
    # own ACCEPT gate (test_no_emitted_poll_can_exceed_the_bash_call_cap) --
    # the token scan above catches the tokens it names, this closes the
    # category regardless of spelling. Complementary, not redundant: the scan
    # above covers the WHOLE prompt (a construct anywhere in the rendered
    # text), this covers the command's own exact shape.
    _assert_gate_command_cannot_hide_a_loop(
        recheck_command(prompt), f"{target.name}'s re-check command"
    )


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_the_recheck_runs_the_chunks_exact_accept_gate(target, tmp_path):
    """Composed once and spliced twice, so the re-check can never drift into a
    weaker gate than the poll it backs up.

    The failure mode is a false GREEN -- accepting a fragment the poll would
    have rejected -- which is the one direction neither pass can recover from,
    and it would only ever fire on the exhaustion path, where nobody is looking.

    Character-identical is the assertion, not merely "mentions --check-batch":
    a gate that asked a NARROWER question would still name the same script.

    Round-6 fix: this used to read `assert gate in recheck` -- CONTAINMENT
    against the re-check's whole multi-line PROMPT, not equality against its
    own command. That is blind to a strictly WIDER command: a re-check that
    runs `checkCmd + " --research-mode offline"` still literally CONTAINS the
    chunk's own narrower gate string as a substring, so the old assertion
    passed it -- measured directly, both templates, before this fix landed
    (see test_accept_gate_parity_is_mutation_proved_against_a_widened_recheck_
    command below, this file's own second control for exactly that shape).
    Fixed by comparing against recheck_command()'s own single-line extraction
    of the re-check's ACTUAL command, which is what "character-identical"
    already meant here -- the docstring was right; the assertion was not."""
    out = exhausted_run(target, tmp_path)
    gates = {accept_gate(p) for p in prompts(out, target.chunk_label)}
    assert len(gates) == 1, (
        f"{target.name}'s own chunks issue different ACCEPT gates: {sorted(gates)}"
    )
    gate = gates.pop()
    recheck = prompts(out, target.recheck_label)[0]
    assert recheck_command(recheck) == gate, (
        f"{target.name}'s re-check does not run the chunks' exact ACCEPT gate "
        f"(character-identical, not merely containing it).\n"
        f"chunk ACCEPT gate:  {gate!r}\n"
        f"re-check command:   {recheck_command(recheck)!r}\n"
        f"re-check prompt:\n{recheck}"
    )


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_accept_gate_parity_is_mutation_proved(target, tmp_path):
    """The control for the test above, and it mutates the EXECUTABLE COMMAND
    rather than any prose around it.

    A mutation that only changed a comment or an instruction line would vary the
    wrong condition: the parity assertion reads the command the agent is told to
    RUN, so only a changed command can prove it discriminates. Here the
    re-check's command becomes a strictly weaker gate -- a bare file-existence
    test -- which is precisely the drift this pairing exists to make impossible.

    Round-6: reads recheck_command()'s own single-line extraction, matching
    the (now equality) primary assertion above, rather than testing containment
    against the whole prompt -- so both this control and the widening one
    below check the SAME derived quantity for two different mutation shapes,
    instead of two different notions of "differs"."""
    mutant = mutate(
        read_template(target),
        'lines.push(checkCmd + " >/dev/null 2>&1")',
        'lines.push("test -f /tmp/lt-weaker-gate >/dev/null 2>&1")',
    )
    out = exhausted_run(target, tmp_path, source=mutant)
    gate = accept_gate(prompts(out, target.chunk_label)[0])
    recheck = prompts(out, target.recheck_label)[0]
    assert recheck_command(recheck) != gate, (
        f"MUTATION NOT CAUGHT: {target.name}'s re-check still carries the chunks' "
        f"ACCEPT gate after its command was replaced with a weaker one, so the "
        f"parity assertion is not reading the executable command.\nre-check:\n{recheck}"
    )


# A per-target flag that widens the re-check's command into a strict SUPERSET
# of the chunk's own ACCEPT gate -- syntactically valid, and not an arbitrary
# choice: both are flags these scripts' own CLIs genuinely accept elsewhere
# (skeptic_ready.py's --senses-path, glossary's --research-mode), so this is
# the shape a plausible, easy-to-miss-in-review edit would actually take, not
# a strawman. This is exactly what the OLD containment assertion
# (`gate in recheck`) could never catch: the narrower gate string stays a
# literal SUBSTRING of the widened one, so containment reads it as a match.
WIDENING_FLAG = {
    "glossary": " --research-mode offline",
    "skeptic": " --senses-path /dev/null",
}


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_accept_gate_parity_is_mutation_proved_against_a_widened_recheck_command(target, tmp_path):
    """The SECOND control the equality fix above needs, and the one
    test_accept_gate_parity_is_mutation_proved just above cannot stand in for.
    That control REPLACES the re-check's command outright, which even the OLD
    containment assertion caught (an unrelated string is not a substring of
    the recheck prompt either). A command that only ADDS a flag stays a
    superset of the chunk's own gate, so the OLD assertion passed it --
    measured directly against this repo's HEAD before this fix landed, both
    targets, all green. Only the equality fix above closes it.

    Mutates the checkCmd BINDING inside batchWaitRecheckPrompt specifically,
    not batchWaitChunkPrompt's identically-worded line. Both templates carry
    TWO occurrences of a `const checkCmd = check*Cmd(...)`-shaped binding (one
    per function), so anchoring on that binding by itself would be ambiguous
    -- mutate() would refuse it (count != 1) or, worse, silently hit the wrong
    one if the anchor happened to be unique for the wrong reason. The anchor
    used here is the re-check's own OUTPUT line,
    `lines.push(checkCmd + " >/dev/null 2>&1")`, which is unique to
    batchWaitRecheckPrompt in both templates (the chunk builder's own poll
    line embeds the same checkCmd inside a longer `while true; do ...`
    string, never this exact substring) -- verified before relying on it."""
    anchor = 'lines.push(checkCmd + " >/dev/null 2>&1")'
    widened = f'lines.push(checkCmd + "{WIDENING_FLAG[target.name]} >/dev/null 2>&1")'
    mutant = mutate(read_template(target), anchor, widened)
    out = exhausted_run(target, tmp_path, source=mutant)
    gate = accept_gate(prompts(out, target.chunk_label)[0])
    recheck = prompts(out, target.recheck_label)[0]
    assert recheck_command(recheck) != gate, (
        f"MUTATION NOT CAUGHT: {target.name}'s re-check command was widened by "
        f"adding {WIDENING_FLAG[target.name]!r}, and the parity check still "
        f"treated it as matching the chunk's own ACCEPT gate.\n"
        f"chunk ACCEPT gate: {gate!r}\n"
        f"re-check command:  {recheck_command(recheck)!r}"
    )


# ===========================================================================
# 8. A CUT-SHORT CHUNK IS PENDING -- the most important behavioural property.
# ===========================================================================

# Every way a chunk can come back saying nothing useful. Each must cost one
# chunk of waiting and nothing else -- never the batch.
AMBIGUOUS_CHUNK_REPLIES = {
    "null_reply": None,
    "empty_reply": "",
    "whitespace_only": "   \n\n  ",
    "tool_killed": "Exit code 143\nCommand timed out after 10m 0s",
    "malformed_prose": "I ran the command but I am not sure what it printed.",
    "wrong_sentinel": "DONE <idx>",
    "other_batch_ready": "READY 7",
    "quoted_but_disavowed_ready": (
        "Quoting the requested success form:\nREADY <idx>\nThat is not my verdict."
    ),
}


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
@pytest.mark.parametrize("shape", sorted(AMBIGUOUS_CHUNK_REPLIES), ids=sorted(AMBIGUOUS_CHUNK_REPLIES))
def test_a_cut_short_chunk_is_pending_and_never_terminates_the_wait(target, shape, tmp_path):
    """The single most important behavioural property of #352, at both callers.

    A null return, an unparseable reply, or one from a chunk the tool killed is
    NOT evidence that the fragment failed -- only that this chunk learned
    nothing. Before 1.16.2 every non-READY reply was terminal, so one ambiguous
    reply ended the whole wait and lost the batch. Now it costs exactly one
    chunk: the remaining chunks still poll, the authoritative re-check still
    runs, and its verdict still decides.

    Three things are asserted, and the third is the one with teeth: the wait
    CONTINUES (the next chunk runs), the re-check RUNS, and the batch PROCEEDS
    on the re-check's READY. A template that swallowed the ambiguity and
    returned not-ready would satisfy the first two."""
    reply = AMBIGUOUS_CHUNK_REPLIES[shape]
    res = run(target, tmp_path=tmp_path, chunk_replies=[reply], recheck_reply="READY <idx>")
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]

    n_chunks = len(prompts(out, target.chunk_label))
    assert n_chunks > 1, (
        f"{target.name}: a {shape} chunk ended the chunk loop after {n_chunks} call(s) "
        f"-- an ambiguous reply must cost one chunk of waiting, not the budget"
    )
    assert target.recheck_label in labels(out), (
        f"{target.name}: a {shape} chunk skipped the authoritative re-check; "
        f"calls={labels(out)}"
    )
    assert target.proceeded(out), (
        f"{target.name}: a {shape} chunk lost the batch even though the re-check "
        f"found the fragment; result={out['result']}"
    )


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_the_pending_default_is_mutation_proved(target, tmp_path):
    """The control for the sweep above: invert the fail-safe default so an
    ambiguous reply resolves to READY, and the sweep's own assertions must fail.

    Without this, "a null reply keeps polling" and "a null reply is read as
    ready and stops" would be told apart only by the chunk COUNT -- and a reader
    could reasonably believe the sweep above was already proving the direction."""
    old, new = target.ready_mutation
    mutant = mutate(read_template(target), old, new)
    res = run(target, tmp_path=tmp_path, chunk_replies=[None], recheck_reply="READY <idx>",
              source=mutant)
    assert res["ok"], f"mutant run threw: {res['stderr']}"
    out = res["out"]

    assert len(prompts(out, target.chunk_label)) == 1, (
        f"MUTATION NOT CAUGHT: {target.name} kept polling after a null chunk even "
        f"with the fail-safe default inverted, so the sweep above is not actually "
        f"discriminating on the verdict; calls={labels(out)}"
    )
    assert target.recheck_label not in labels(out), (
        f"MUTATION NOT CAUGHT: {target.name} still ran the re-check after a null "
        f"chunk was made to resolve READY"
    )


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_a_prose_decorated_ready_still_ends_the_wait_on_the_first_chunk(target, tmp_path):
    """The opposite direction, and the happy path: #308's prose-preamble
    tolerance must survive the rename. The common case must not have become
    slower or stricter -- one chunk, no re-check."""
    res = run(target, tmp_path=tmp_path,
              chunk_replies=["The poll confirmed the fragment (exit 0).\n\nREADY <idx>"],
              recheck_reply="PENDING <idx>")
    assert res["ok"], f"run threw: {res['stderr']}"
    out = res["out"]

    assert len(prompts(out, target.chunk_label)) == 1, (
        f"{target.name}: a decorated READY first chunk kept polling"
    )
    assert target.recheck_label not in labels(out), (
        f"{target.name}: a READY chunk triggered a needless re-check"
    )
    assert target.proceeded(out)


# ===========================================================================
# 6. STARTUP GUARDS -- both, per template, by pushing each constant past its
#    bound.
# ===========================================================================

GUARD_MUTATIONS = {
    # The chunk would instruct a tool timeout the Bash tool cannot grant, so the
    # chunk's declared bound would stop being the real bound: #352 exactly.
    "tool_timeout_over_the_clamp": (
        "const WAIT_CHUNK_TOOL_TIMEOUT_MS = 540000",
        "const WAIT_CHUNK_TOOL_TIMEOUT_MS = 660000",
        "exceeds the measured Bash per-call clamp",
    ),
    # The poll would be killed before it could reach its own elapsed bound and
    # print its marker, so "the marker is the last line" would stop holding.
    "chunk_leaves_no_headroom": (
        "const WAIT_CHUNK_SEC = 480",
        "const WAIT_CHUNK_SEC = 540",
        "leaves no headroom under",
    ),
}


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
@pytest.mark.parametrize("guard", sorted(GUARD_MUTATIONS), ids=sorted(GUARD_MUTATIONS))
def test_startup_guard_fires_when_its_constant_passes_its_bound(target, guard, tmp_path):
    """The guards are the reason no emitted poll can exceed the clamp BY
    CONSTRUCTION rather than by review -- but a guard nobody has watched fire is
    a comment with a `throw` in it.

    Each is proved by pushing exactly the constant it watches past exactly its
    bound, and the throw must happen BEFORE anything is dispatched: a guard that
    fired after the first batch went out would have already spent the call it
    exists to prevent -- asserted directly below against the harness's own
    call log, not merely claimed here: `not res["ok"]` and a message match tell
    you THAT it threw, never WHEN, because run() discards `out` -- the only
    other carrier of the call log -- on this exact path."""
    old, new, needle = GUARD_MUTATIONS[guard]
    mutant = mutate(read_template(target), old, new)
    res = run(target, tmp_path=tmp_path, chunk_replies=["READY <idx>"],
              recheck_reply="READY <idx>", source=mutant)

    assert not res["ok"], (
        f"{target.name}'s {guard} guard did not fire; the template ran to completion "
        f"with a constant past its bound"
    )
    assert needle in res["stderr"], (
        f"{target.name}'s {guard} guard fired with an unexpected message "
        f"(expected {needle!r}):\n{res['stderr']}"
    )
    # None and [] are DIFFERENT outcomes, run()'s own docstring says so, and a
    # single `== []` assertion covering both misreports the None one: it
    # would fail with "fired only after None had already been dispatched",
    # which describes the opposite of what actually happened (the harness
    # never reached its own catch block at all, so there is no dispatch
    # record to report either way -- not "nothing" as in an empty list, but
    # "unknown"). Split so each outcome gets an accurate message.
    assert res["calls_before_throw"] is not None, (
        f"{target.name}'s {guard} guard: the harness process never reached "
        f"its own try/catch at all (a syntax error in the mutated source is "
        f"the likely cause, not the guard itself) -- there is no dispatch "
        f"record to check ordering against:\n{res['stderr']}"
    )
    assert res["calls_before_throw"] == [], (
        f"{target.name}'s {guard} guard fired only after "
        f"{res['calls_before_throw']} had already been dispatched -- the throw "
        f"must happen before anything is dispatched, not merely before the run "
        f"finishes"
    )


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_the_shipped_constants_sit_inside_both_guards(target, tmp_path):
    """The positive control the two mutation tests above cannot give: a guard
    that threw unconditionally would pass both of them. The shipped template
    must RUN."""
    res = run(target, tmp_path=tmp_path, chunk_replies=["READY <idx>"],
              recheck_reply="READY <idx>")
    assert res["ok"], (
        f"{target.name}'s shipped constants trip one of its own startup guards:\n"
        f"{res['stderr']}"
    )


# ===========================================================================
# 7. ESTIMATOR ARITHMETIC AT ALL THREE GATES.
# ===========================================================================

def source_carries(source: str, expression: str) -> bool:
    """Is `expression` present in `source`, ignoring how it is WRAPPED?

    Every declaration this file pins is a source expression, and a source
    expression can be re-wrapped without changing meaning -- black/prettier do
    it, and so does a human widening a line. An exact-substring pin turns that
    cosmetic edit into a red test whose message says the estimator has "drifted
    from skeptic_setup.py", which would be false and would send whoever hits it
    looking for a bug that does not exist.

    Whitespace-collapsing both sides keeps the pin (the tokens and their order
    still have to match exactly) and drops the part that was never the point.
    Mirrors tests/retired_wording_pins.test.py's normalize()."""
    flat = " ".join(source.split())
    return " ".join(expression.split()) in flat


def _template_wait_calls(source: str) -> int:
    """WAIT_CALLS as the template itself derives it, from its own two declared
    constants -- never from a rendered literal, which a hand-edited
    `const WAIT_CALLS = 3` would satisfy while silently ceasing to track the
    chunk size."""
    consts = {}
    for name in ("WAIT_BOUND_SEC", "WAIT_CHUNK_SEC"):
        m = re.search(rf"^const {name} = (\d+)", source, re.MULTILINE)
        assert m, f"template no longer declares a const {name}"
        consts[name] = int(m.group(1))
    assert source_carries(source, "const WAIT_CHUNKS = Math.ceil(WAIT_BOUND_SEC / WAIT_CHUNK_SEC)"), (
        "template no longer derives WAIT_CHUNKS from its own bound and chunk size"
    )
    assert source_carries(source, "const WAIT_CALLS = WAIT_CHUNKS + 1"), (
        "template no longer derives WAIT_CALLS as WAIT_CHUNKS + 1"
    )
    return -(-consts["WAIT_BOUND_SEC"] // consts["WAIT_CHUNK_SEC"]) + 1


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_the_full_wait_cost_ladder_is_one_two_or_three_calls(target, tmp_path):
    """The whole cost ladder in one place, so "a wait costs WAIT_CALLS" can
    never be read out of this file.

    It costs ONE call on the ordinary path, N when chunk N answers, and only the
    full WAIT_CALLS when the budget is exhausted and the authoritative re-check
    runs. The estimator charges the ceiling -- which is what a preflight should
    do -- and the gap between the ceiling and the ordinary cost is a property
    worth pinning in its own right: #352 WIDENED it, and a build that narrowed
    it back would be spending the ceiling on every healthy batch."""
    n_chunks = len(prompts(exhausted_run(target, tmp_path), target.chunk_label))

    def spent(chunk_replies, recheck_reply):
        res = run(target, tmp_path=tmp_path, chunk_replies=chunk_replies,
                  recheck_reply=recheck_reply)
        assert res["ok"], res["stderr"]
        return len([lbl for lbl in labels(res["out"])
                    if lbl in (target.chunk_label, target.recheck_label)])

    # Int-keyed only -- the exhaustion case gets its own variable rather than a
    # string key smuggled into this dict, so the dict's key type stays honest.
    ladder = {k: spent(_ready_at(k), "PENDING <idx>") for k in range(1, n_chunks + 1)}
    exhausted_calls = spent(["PENDING <idx>"], "READY <idx>")

    assert ladder[1] == 1, (
        f"{target.name}: the ORDINARY path -- the fragment already there when the "
        f"first chunk looks -- must cost ONE agent call, spent {ladder[1]}"
    )
    for k in range(1, n_chunks + 1):
        assert ladder[k] == k, f"{target.name}: chunk-{k} answer spent {ladder[k]} calls"
    assert exhausted_calls == EXPECTED_WAIT_CALLS == n_chunks + 1, (
        f"{target.name}: the exhaustion path spent {exhausted_calls} calls; the "
        f"ceiling is {EXPECTED_WAIT_CALLS} == {n_chunks} chunk(s) + 1 re-check"
    )
    assert ladder[1] < exhausted_calls, (
        f"{target.name}: the ordinary path costs the same as exhaustion, so the "
        f"preflight ceiling is no longer an over-estimate of anything"
    )


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_template_wait_calls_matches_what_a_worst_case_wait_actually_spends(target, tmp_path):
    """The declared WAIT_CALLS against the MEASURED one. Two different things --
    one is what the preflight charges, the other is what the state machine
    spends -- and a formula can be internally consistent and still be wrong
    about the code."""
    declared = _template_wait_calls(read_template(target))
    assert declared == EXPECTED_WAIT_CALLS, (
        f"{target.name} declares {declared} calls per wait, not {EXPECTED_WAIT_CALLS}"
    )

    out = exhausted_run(target, tmp_path)
    spent = len(prompts(out, target.chunk_label)) + len(prompts(out, target.recheck_label))
    assert spent == declared, (
        f"{target.name}'s worst-case wait SPENDS {spent} agent calls while its "
        f"preflight CHARGES {declared} -- under-charging lets a run start and then "
        f"blow engine.batch_agent_cap mid-flight"
    )


def test_skeptic_setup_estimator_matches_the_template_and_the_shipped_ladder():
    """The three-gate seam. skeptic_setup.py refuses a run BEFORE the Workflow
    ever starts, and the skeptic template refuses it again at dispatch. Leave
    one behind and one of them refuses a batch the other admits -- after the
    setup script has already written this run's manifests.

    Compared symbolically at both ends: the script's PER_BATCH_CALLS is read as
    its own expression, the template's as its own, and the wait term each is
    built from is re-derived from that file's own declared constants. Comparing
    rendered totals would agree for any pair of files that drifted together."""
    setup_src = SKEPTIC_SETUP_SCRIPT.read_text(encoding="utf-8")

    setup_consts = {}
    for name in ("WAIT_BOUND_SEC", "WAIT_CHUNK_SEC"):
        m = re.search(rf"^{name} = (\d+)", setup_src, re.MULTILINE)
        assert m, f"skeptic_setup.py no longer declares {name}"
        setup_consts[name] = int(m.group(1))
    assert source_carries(setup_src, "WAIT_CHUNKS = (WAIT_BOUND_SEC + WAIT_CHUNK_SEC - 1) // WAIT_CHUNK_SEC"), (
        "skeptic_setup.py no longer derives WAIT_CHUNKS by ceil-div from its own constants"
    )
    assert source_carries(setup_src, "WAIT_CALLS = WAIT_CHUNKS + 1")
    assert source_carries(setup_src, "PER_BATCH_CALLS = 2 + WAIT_CALLS"), (
        "skeptic_setup.py's per-batch term is no longer precheck + dispatch + wait"
    )
    assert source_carries(setup_src, "FIXED_RUN_CALLS = 2")

    setup_wait_calls = (
        -(-setup_consts["WAIT_BOUND_SEC"] // setup_consts["WAIT_CHUNK_SEC"]) + 1
    )
    template_wait_calls = _template_wait_calls(read_template(SKEPTIC))
    assert setup_wait_calls == template_wait_calls == EXPECTED_WAIT_CALLS, (
        f"skeptic_setup.py implies {setup_wait_calls} calls per wait and "
        f"skeptic-pass-wf.template.js implies {template_wait_calls}; this file "
        f"expects {EXPECTED_WAIT_CALLS}"
    )
    # ...and the template's own preflight expression really is the same term.
    assert source_carries(read_template(SKEPTIC),
                          "const estimatedCalls = (2 + WAIT_CALLS) * BATCHES.length + 2"), (
        "the skeptic template's preflight is no longer (2 + WAIT_CALLS)*N + 2, so it "
        "has drifted from skeptic_setup.py's PER_BATCH_CALLS/FIXED_RUN_CALLS"
    )


@pytest.mark.parametrize(
    "research_mode,per_batch",
    [("live", 19), ("offline", 5)],
    ids=["live", "offline"],
)
def test_glossary_preflight_refuses_one_call_over_its_own_ladder(research_mode, per_batch, tmp_path):
    """The glossary gate, EXECUTED rather than parsed, at the boundary in both
    modes. tests/batch_size_estimator.test.py owns the formula in depth; what is
    here is the boundary behaviour of the two modes side by side, because #352
    is the release that moved BOTH -- the earlier ones moved only live, and a
    fix applied to one branch and not the other is the shape half this plugin's
    estimator bugs have taken."""
    batches = glossary_batches(2)
    estimated = per_batch * len(batches) + 2

    over = run(GLOSSARY, tmp_path=tmp_path, chunk_replies=["READY <idx>"],
               recheck_reply="READY <idx>", batches=batches,
               batch_agent_cap=estimated - 1, research_mode=research_mode)
    assert over["ok"], over["stderr"]
    assert over["out"]["result"] == {
        "merged": False, "reason": "batch-too-large",
        "estimatedCalls": estimated, "cap": estimated - 1,
    }
    assert over["out"]["calls"] == [], "a refused run must dispatch nothing at all"

    at_cap = run(GLOSSARY, tmp_path=tmp_path, chunk_replies=["READY <idx>"],
                 recheck_reply="READY <idx>", batches=batches,
                 batch_agent_cap=estimated, research_mode=research_mode)
    assert at_cap["ok"], at_cap["stderr"]
    assert at_cap["out"]["result"]["merged"] is True, (
        "estimatedCalls == cap must NOT trip the gate (the check is '>', not '>=')"
    )


def test_skeptic_preflight_refuses_one_call_over_its_own_ladder(tmp_path):
    """The same boundary at the skeptic gate, executed."""
    batches = skeptic_batches(2)
    estimated = 5 * len(batches) + 2

    over = run(SKEPTIC, tmp_path=tmp_path, chunk_replies=["READY <idx>"],
               recheck_reply="READY <idx>", batches=batches, batch_agent_cap=estimated - 1)
    assert over["ok"], over["stderr"]
    assert over["out"]["result"] == {
        "merged": False, "reason": "batch-too-large",
        "estimatedCalls": estimated, "cap": estimated - 1,
    }
    assert over["out"]["calls"] == []

    at_cap = run(SKEPTIC, tmp_path=tmp_path, chunk_replies=["READY <idx>"],
                 recheck_reply="READY <idx>", batches=batches, batch_agent_cap=estimated)
    assert at_cap["ok"], at_cap["stderr"]
    assert at_cap["out"]["result"]["merged"] is True


# The shipped engine.batch_agent_cap, and what each raised ladder now admits.
# Derived here, then compared against the operator-facing table in
# profile.example.yml -- these numbers are the ones an operator sizes a real
# book batch against. #352 moved every per-batch FORMULA (13N+2/3N+2 ->
# 19N+2/5N+2); #409 step 2 then moved the shipped CAP itself (3500 -> 10000)
# without touching either formula, which rescales every max-batch figure by
# that same factor -- (10000-2)//per_batch, not a copy of what the code
# happens to print today.
SHIPPED_BATCH_AGENT_CAP = 10000
LADDER_MAX_BATCHES = {
    "glossary live": (19, 526),      # (10000-2)//19 = 9998//19 = 526
    "glossary offline": (5, 1999),   # (10000-2)//5  = 9998//5  = 1999
    "skeptic (both)": (5, 1999),     # same 5N+2 formula as glossary offline
}


@pytest.mark.parametrize("ladder", sorted(LADDER_MAX_BATCHES), ids=sorted(LADDER_MAX_BATCHES))
def test_the_shipped_cap_still_admits_the_documented_batch_count(ladder):
    """The gates are only as useful as the cap they are checked against, and a
    raised ladder silently shrinks what a project can run. Each figure is
    re-derived here and then required to appear in profile.example.yml's own
    table -- so an operator reading the profile and an operator reading this
    suite are told the same thing, and a ladder change that skipped the docs
    goes red here rather than being discovered mid-book."""
    per_batch, documented_max = LADDER_MAX_BATCHES[ladder]
    derived_max = (SHIPPED_BATCH_AGENT_CAP - 2) // per_batch
    assert derived_max == documented_max, (
        f"{ladder}: {per_batch}N+2 against a cap of {SHIPPED_BATCH_AGENT_CAP} admits "
        f"{derived_max} batches, not the {documented_max} this test expects"
    )
    # ...and that boundary really is a boundary.
    assert derived_max * per_batch + 2 <= SHIPPED_BATCH_AGENT_CAP
    assert (derived_max + 1) * per_batch + 2 > SHIPPED_BATCH_AGENT_CAP

    profile = PROFILE_EXAMPLE.read_text(encoding="utf-8")
    assert f"batch_agent_cap: {SHIPPED_BATCH_AGENT_CAP}" in profile, (
        f"profile.example.yml no longer ships batch_agent_cap: {SHIPPED_BATCH_AGENT_CAP}, "
        f"so every max-batch figure in this test is about a cap nobody runs"
    )
    # Whitespace-collapsed: the profile's table is hard-wrapped prose, and a
    # line-oriented needle would miss a row that happened to wrap.
    flat = " ".join(profile.split())
    assert f"{per_batch}N+2" in flat, (
        f"profile.example.yml's ladder table does not carry {per_batch}N+2 for {ladder}"
    )
    assert f"-> {documented_max}" in flat, (
        f"profile.example.yml's ladder table does not carry the post-1.16.2-"
        f"formula, post-#409-step-2-cap max-batch figure {documented_max} "
        f"for {ladder}"
    )


# ===========================================================================
# RED EVIDENCE, kept as tests rather than as a note in a report.
#
# Every gate above was watched failing before it passed. Two of those reds are
# reproducible on demand and so are kept executable here: the pre-fix templates
# are still in git, and running the two headline assertions against a FROZEN
# baseline commit (`PRE_RELEASE_BASELINE`, the 1.16.1 release merge -- see
# read_template_at_baseline() above) is a stronger, more durable statement than
# any transcript of a one-off revert -- and it costs nothing, because it reads
# git's object store rather than the working tree, which teammates are
# concurrently editing.
#
# The baseline is FROZEN rather than `HEAD:`, and deliberately does NOT skip.
# An earlier version of this file read `HEAD:` and treated "the baseline
# already has the fix" as a reason to skip; that was briefly correct while
# 1.16.2 stayed uncommitted, then quietly stopped being a pre-fix read at all
# the moment HEAD carried the release, and the four red-evidence tests below
# degraded into skips that stayed green while asserting nothing. Under a
# FROZEN baseline that failure mode cannot recur the same way: "the baseline
# already has the fix" can only mean the pinned SHA is wrong, since a frozen
# commit does not itself drift, so `_baseline_is_prefix()` below is a hard
# ASSERT, never a skip -- skipping on it would hide exactly the thing worth
# knowing.
# ===========================================================================

def _baseline_is_prefix(target: Target) -> bool:
    """The frozen baseline must still carry the pre-#352 single-shot poll.

    Asserted rather than used as a skip condition. Under the old moving `HEAD:`
    read this was a real question -- HEAD advanced past the change and the reds
    below had to stand down. Frozen, a False here does not mean "this red is now
    historical", it means the baseline SHA is wrong, and skipping on it would
    hide exactly that."""
    return "batchWaitChunkPrompt" not in read_template_at_baseline(target)


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_red_the_pre_fix_template_emits_a_poll_over_the_clamp(target, tmp_path):
    """RED EVIDENCE for the cap gate, run against the real pre-fix source.

    The pre-1.16.2 wait emitted ONE `seq 1 45` x `sleep 20` poll: 900 s in a
    single bash call, against a measured 600 s clamp. That is #352, and it is
    what this file's cap assertions must reject."""
    assert _baseline_is_prefix(target), (
        f"the frozen baseline {PRE_RELEASE_BASELINE[:12]} already chunks "
        f"{target.name}'s wait, so it is not the pre-#352 tree and every red in "
        f"this section is about the wrong commit"
    )
    src = target.instantiate(read_template_at_baseline(target))
    over_cap = [
        (int(iters), int(sleep_s))
        for iters, sleep_s in re.findall(r"seq 1 (\d+)\).*?sleep (\d+)", src)
        if int(iters) * int(sleep_s) > BASH_CALL_CAP_SEC
    ]
    assert over_cap, (
        f"expected the pre-fix {target.name} template to carry a fixed-iteration poll "
        f"whose product exceeds the {BASH_CALL_CAP_SEC}s clamp; found none"
    )


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_red_the_pre_fix_template_loses_a_late_landing_fragment(target, tmp_path):
    """RED EVIDENCE for the fix itself, driven end to end on the pre-fix source.

    Every chunk PENDING, the fragment landing only in time for a re-check that
    does not exist yet. The pre-fix template reports the batch not-ready over a
    complete, gate-valid fragment -- the exact loss #352 describes."""
    assert _baseline_is_prefix(target), (
        f"the frozen baseline {PRE_RELEASE_BASELINE[:12]} already has the "
        f"authoritative re-check, so it is not the pre-#352 tree"
    )
    res = run(target, tmp_path=tmp_path, chunk_replies=["PENDING <idx>"],
              recheck_reply="READY <idx>", source=read_template_at_baseline(target))
    assert res["ok"], f"pre-fix run threw: {res['stderr']}"
    out = res["out"]
    assert target.recheck_label not in labels(out), (
        "the pre-fix template is not supposed to have an authoritative re-check"
    )
    assert not target.proceeded(out), (
        f"the pre-fix {target.name} template converged on a late-landing fragment, so "
        f"this file's headline test would have passed before the fix"
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
