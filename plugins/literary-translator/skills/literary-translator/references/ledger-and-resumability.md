# Ledger and resumability

This is the mechanism that makes a mass-translate batch safely stoppable and
resumable: per-segment status tracking, a composite cache key that decides
whether a previously-converged segment can be reused or must be
re-translated, and a set of schema-validated write paths that make every
ledger write independently verifiable rather than trusted on an agent's say-so.

## Confidence-level split

Not all of this subsystem carries the same evidence behind it. Be explicit
about this when relying on it or extending it.

**Source-proven, at ~75-segment scale (the real `historiettes-t3` project):**
the engine loop itself (translate → gate → review → fix, see
`engine-loop.md`), `validate_draft.py`'s false-green checks (see
`false-green-gate.md`), and the single-file `ledger.json` **concept** — a
per-segment `status`/`reason`/`rounds` map used as a human-readable status
report.

**New hardening for this plugin, never run at scale:** the per-segment
fragment ledger (`runs/ledger.d/*.json`), the atomic tmp-write-then-rename
writer (`scripts/ledger_update.py`), the merge/stale materializer
(`scripts/ledger_merge.py`), the shared cache-key implementation
(`scripts/cache_key.py`), every schema-confirmed write path
(`recordLedgerPrompt` / `mergeLedgerPrompt`), and `engine.batch_agent_cap`'s
preflight estimator. This bucket also includes the plugin's
FRONTBACK-through-segment-loop treatment: the real project's own plan stated
that intent, but the implemented project handled front/back matter through a
separate, hand-maintained `frontmatter_ru.json`, entirely outside the
ledger/review pipeline. The real reference project ran its ~75 segments
against a single hand-maintained `ledger.json` with no fragment directory, no
atomic writer, and no composite cache key implemented in code (the cache-key
idea existed only as prose in that project's own planning doc).

This is not a reason to simplify or cut the subsystem — the concurrent-write
race a single shared `ledger.json` has under a real batch run is real, and
fragment-per-segment is the standard fix for it. It is a reason to treat it
as a careful first design, not as something already proven free of
surprises at scale. A dedicated pilot/soak on the first real plugin project
is necessary before trusting it unconditionally.

A pilot/soak alone is not sufficient. `tests/ledger_e2e_acceptance.test.py`
is a mandatory fixture (mocked agent outputs, no real agent calls) that must
pass first, in one continuous run: (1) batch 1 dispatches segments A/B/C —
B converges and C hits `max_fix_rounds` and goes `non_converged`; (2) a
simulated interruption leaves A's genuine `recoverable` fragment after its
`in_progress` write but before its terminal write; (3) B's `style_bible.md`
fixture is edited between batches, so the second classification pass must
reclassify B `stale`; (4) batch 2's `select_segments.py` asserts A is
`recoverable` (dispatched like `not_started`), B is `stale` (re-dispatched,
full-replace fragment, no stale fields surviving); (5) `--only-segs <C>`
retries the `human_escalation` segment C, re-enters `SEGS`, and its stale
terminal fragment gets replaced; (6) `ledger_merge.py --expected-segs`
completeness check passes even though `ledger.json` now accumulates fragments
from both batches; (7) a final assertion on the merged `ledger.json`'s
end-to-end correctness. This
acceptance test is a prerequisite for the pilot/soak, not a replacement for
it.

## Canonical path invariants

These are stated once, here, as invariants. Every script or template that
touches these files must follow them exactly — a ported script that
hardcodes a different path is a bug, not a faithful port.

- **`draft_path(seg) = segments/{seg}.draft.json`** — no target-language
  suffix. This is a deliberate divergence from the real source project's own
  `.ru.draft.json` naming: v1 has exactly one target language per project,
  already recorded once in `profile.yml`'s `target.language.code`, so
  repeating it in every draft filename adds no information. Every
  script/template touching a draft file — `validate_draft.py`,
  `draft_ready.py`, `ledger_update.py`, `final_audit.py`, `draft_sha1.py`,
  `assemble.py`, `ledger_merge.py`, `select_segments.py`, `codex_job.py`
  (`--kind translate` — derives the same canonical `draft.json` for its
  validate-before-promote), `review_TASK.template.md`,
  `translate_TASK.template.md`, `mass-translate-wf.template.js`,
  `validate_assembled.py` (1.6.0 — its default scope reads converged drafts
  for the union structural-completeness gate, see below) — must use
  this exact path (**13** draft-path sites as of 1.6.0).
  `tests/draft_path_convention.test.py` instantiates every one of these
  against a fixture and asserts the exact path, failing loudly and naming
  the offender if any one disagrees. **1.2.0:** the written file also
  carries a `dispatch_token` metadata field, `<RUN_ID>:<seg>` — see
  "`dispatch_token` and the resume-integrity commit-gate chain" below.
  `draft_sha1.py`/`validate_draft.py` exclude this field from the content
  hash / structural coverage (it's metadata, not translated content, the
  same treatment `review.json`'s own token gets).

- **`review_path(seg) = segments/{seg}.review.json`** — same no-suffix
  reasoning, and the `segments/` prefix is required (matches the real
  reference project exactly — never a top-level
  `${durable_root}/{seg}.review.json`). Readers/writers: the JS writer is
  `reviewDispatchPrompt` (was `reviewPrompt`, 1.2.0); JS readers are
  `readReviewPrompt` (new 1.2.0), `verifyReviewArtifactPrompt` (called
  separately after `readReviewPrompt`), and **`fixPrompt`** (a reader since
  1.3.6/#132 option b — it READS the on-disk `findings[]` rather than working
  from the in-memory `revObj`, see `references/engine-loop.md` R1). Script
  writers/readers: `review_TASK.template.md` (the codex review-task writer
  output line), `scripts/review_artifact_check.py`, `scripts/review_ready.py`,
  `scripts/ledger_merge.py`, `scripts/ledger_update.py` (reads it for the
  `reviewed_draft_sha1`/`dispatch_token` binding check at convergence), and
  `codex_job.py` (`--kind review` — derives the same canonical `review.json`
  for its validate-before-promote). `tests/draft_path_convention.test.py` is
  extended (not duplicated) to cover all these call sites — recomputed to
  **10** review-path sites for 1.4.7.
  **1.2.0:** the written file also carries `dispatch_token =
  <RUN_ID>:<seg>:r<roundLabel>` (`roundLabel` = the round number or
  `final`) — see below.

- **Script self-anchoring.** Every script copied under `scripts/` derives
  its own working root via `Path(__file__).resolve().parents[1]`, since it
  always lives at `${durable_root}/scripts/<name>.py`. A script never
  assumes its cwd equals `durable_root`. Since #409 four of them
  (`select_segments.py`, `ledger_merge.py`, `resume_setup.py`,
  `review_ready.py`) also accept two optional, independent overrides:
  `--durable-root PATH` for data, and `--plugin-root PATH` for where their
  sibling scripts resolve from — the latter deliberately never derived from
  the former, since `${durable_root}/scripts/` is writable by the codex
  process these scripts gate. Omitting both is byte-identical to plain
  self-anchoring; see `references/gotchas.md` §4 for the full rationale.
  The `{{DURABLE_ROOT}}` template token (how the calling agent finds
  and invokes the script) and this `Path(__file__)` self-anchoring (how the
  script finds everything else once it's running) are two different halves
  of the same reachability guarantee — do not conflate them. A test invokes
  a representative script from a cwd that is neither `durable_root` nor the
  script's own directory, and asserts it still correctly reads/writes under
  the real `durable_root`.

- **Durable root, never scratchpad.** Step 0a rejects any `durable_root`
  resolving under `/tmp`, `scratchpad`, or similar.

## `dispatch_token` and the resume-integrity commit-gate chain (1.2.0)

New in 1.2.0, closing the resumability half of #97/#87's fallout: once
review became its own DISPATCH artifact — written by a detached codex job
(as of 1.4.7/#198 the shipped `codex_job.py --kind review` driver, which
validate-before-promotes it; see `references/workflow-schema-validation.md`
and `references/orchestration-and-batching.md`) — `draft.json`/`review.json`
became unscoped, overwritable paths a straggler write from an OLD,
interrupted run could repopulate *after* a fresh run started — the
`{{RUN_ID}}`-derived `dispatch_token` is what closes that, and it is
checked at every point an artifact's bytes are consumed or committed for a
durable decision, not just once at the readiness poll (closing a
check-vs-use TOCTOU window):

1. **The reviewer's own dispatch** (`reviewDispatchPrompt`) asserts the
   **draft's** token equals the current run's token when it hash-first-reads
   the draft, before ever reviewing it.
2. **`readReviewPrompt`** asserts `review.json`'s token equals the current
   run's token before returning it to the JS.
3. **The per-segment convergence ledger write**
   (`recordLedgerPrompt(seg, {status:'converged', ...})` /
   `scripts/ledger_update.py`) re-asserts that **both** the on-disk draft
   AND `review.json` carry the current run's token, **and** that the
   draft's current sha1 still equals the reviewer's own recorded
   `draft_sha1` — all **before** recording `status: 'converged'`. Any
   mismatch refuses convergence outright (a structured `success:false`
   failure, never a silently-accepted stale convergence) — the same
   escape-hatch shape the pre-existing `reviewed_draft_sha1` mismatch
   check already used, now widened to cover the token too.
4. **The batch-final `merge-ledger` check**
   (`mergeLedgerPrompt`/`scripts/ledger_merge.py`) re-asserts, for **each**
   expected converged segment, that its on-disk draft + `review.json` still
   carry the current run's token AND that the draft's sha1 still matches the
   ledger-recorded `draft_sha1` — **before** reporting `batchComplete`. This
   closes the gap a per-segment check alone can't: an old-token straggler
   pair could in principle be restored *between* step 3's per-segment write
   and this batch-level check, materializing a false-green `batchComplete`
   that step 3 alone would never catch. A mismatch here means
   `success:false` — not complete, even though every individual segment's
   own convergence write already passed.
5. **Every downstream draft consumer** (assembly, `final_audit.py`, and
   — 1.6.0 — `validate_assembled.py`'s default scope) re-checks
   `draft_sha1` against the ledger-recorded value the same way step 3
   already established — unchanged from before 1.2.0, restated here only to
   close the chain: per-segment ledger → batch merge-ledger → assembly is
   the **complete** set of durable commit points, and a consistent
   old-token straggler pair restored at any single moment is rejected at
   the very next gate in this chain, never allowed to accumulate silently
   past two. `validate_assembled.py` rebinds each draft it reads to the
   ledger-recorded `reviewed_draft_sha1` before trusting it (same
   contract as this step), rejecting a hand edit made between the W7
   review and this later gate.

This chain is what `resume_setup.py`'s resume-integrity digest (see
`references/orchestration-and-batching.md`) *complements*, not duplicates:
the digest decides **whether resuming at all is safe** (an input/version
match); this chain polices **every individual artifact's freshness** even
when resuming is in principle safe — a cached-completed call under a
digest-MATCH resume still has its actual bytes re-checked at every one of
the five points above, never trusted purely because the digest matched.

`ledger_update.py` is a `plugin_bundle_hash` member (see the three-bundle-hash
table below) precisely because this token/sha-aware precondition logic is
exactly the kind of correctness-determining code that bundle exists to gate
on. `ledger_merge.py` keeps its pre-1.2.0 `orchestration_bundle_hash`
membership (diagnostic-only, never part of any segment's cache key) even
though it now also carries this batch-final token/sha re-check — its bytes
still feed the resume-integrity digest's `version` input (which reads
*both* bundle hashes, see `references/orchestration-and-batching.md`), just
never a per-segment cache-key comparison. A fix to `ledger_merge.py`'s own
re-check logic is therefore visible to the resume-integrity gate (forces a
fresh, no-resume run) but never flips an individual converged segment
`stale` on its own.

## Run-scratch files, the codex-job wait bound, and what the resume digest excludes (1.4.7, #198)

The W5 `codex_job.py` driver writes several **run-scratch** dotfiles under
`${durable_root}/segments/` (and consumes a per-dispatch task-file). These are
NOT segment artifacts and NOT convergence/resume inputs — they are excluded from
every bundle/cache-key hash, from `final_audit.py`/`assemble.py` coverage, and
from the resume-integrity digest. All but one are **ephemeral**; the exception is
called out below and must not be swept with the rest:

- `.codex_task.*.<DISP>` — the per-dispatch codex task-file (the drive agent
  writes it; the driver is its sole consumer and deletes it),
- the driver's own final-prompt temp (deleted on every path),
- `.att.<seg>.<INV>.<draft|review>.json` — since #697 this name is **no longer where the
  candidate is gated**. The ISOLATED attempt the driver validates before it
  `os.replace`-promotes it to the canonical `draft_path(seg)`/`review_path(seg)` now lives
  in a per-invocation `mkdtemp` directory OUTSIDE `durable_root`, on the same device
  (`_preflight_same_device()` enforces that live and refuses before any paid turn). What can
  still appear here under this name is what `_teardown_staging()` RELOCATES back into
  `segments/` on the two refusals that must not destroy validated bytes — a
  canonical-unreadable refusal, and a promote whose `os.replace` itself failed. It is then
  unreachable by any later run — the name embeds a per-invocation random component — and it
  is inert: since #428 the dispatch scans skip the whole dot-prefixed namespace, so a
  surviving attempt no longer perturbs their counts. It is kept for HAND recovery, which is
  the whole reason it is relocated into `segments/` rather than left in a `mkdtemp` path
  nobody would look in,
- `.att_pending.<seg>.<draft|review>.json` — the deterministic per-seg/kind PENDING
  slot `_defer_attempt()` writes when a job runs out of budget mid-flight, consumed by
  the next run's `adopt_pending()` (which re-validates it through the same candidate
  gates before promoting anything). Same suffix collision, same resolution: it is
  skipped by the dispatch scans, which matters more here than for `.att.*`, because a
  pending left by an EARLIER run CAN carry that run's `dispatch_token` (it holds a
  completed but not-yet-validated candidate, so a malformed one may carry no usable
  token at all) and a tokened one used to inject a run id with no real draft behind it
  into the resume-integrity gate's evidence. Stated precisely, because the trade is
  real and one-directional: a tokened pending IS a trace of a genuine dispatch — the
  deferring run promoted nothing, so no canonical draft carries its token — and the
  gate now deliberately stops honouring that trace rather than refusing on a run
  whose only surviving artifact is private staging state. `scan_dispatching_run_ids()`
  documents the resulting widened undetectable case,
- `.att_superseded.<draft|review>.<seg>.<INV>` — **the one DURABLE entry in this
  list** (#429). When a deferral displaces a pending occupant, the occupant is first
  given this second name with `os.link()` — a link ADDS a name and removes none, so
  the slot is never vacated — and only then is the slot overwritten. Before it, a
  candidate that had merely gone UNREADABLE between runs was destroyed by the next
  ordinary no-budget completion. Nothing re-adopts it, nothing collects it, and its
  accumulation is bounded by nothing: it exists for HAND recovery, which is why the
  name carries the kind and the segment (the payload cannot be trusted to — the
  candidate is unvalidated at defer time, and `review.schema.json` has no `seg` field
  at all). Like every other entry here it is dot-prefixed, so the #428 skip above
  covers it for free and no suffix rule has to. **Known limit, and the reason it is
  called out rather than folded into the list:** these are DURABLE and they
  ACCUMULATE, while `fixPrompt()` asks the fix agent to settle a book-scoped rule
  against "the other segments' drafts under `segments/`" — a natural-language census
  no filename convention binds. So a retained copy can in principle be read as
  evidence, and that exposure grows over time rather than passing with the next
  dispatch, which is the one respect in which this entry is worse than the transient
  slot it preserves,
- `.codex_job.<seg>.json` — the driver's HYGIENE control state (overwritten per
  dispatch; read ONLY by the driver, never by the Workflow),
- `.codex_job.<seg>.lock` — the never-unlinked kernel-`flock` sentinel that
  serializes a same-seg retry-dispatched driver against a surviving prior one,
- `.codex_failed.<seg>.<DISP>` — the empty per-dispatch fail sentinel the wait
  poll's fail-fast reads (its NAME is the whole signal).

(There is deliberately no `.gate.*` snapshot and no `.codex_disp.*` sidecar — the
per-dispatch `DISP` nonce travels only via the drive agent's `DISPATCHED <seg>
<DISP>` return line, never a file.)

**Wait bound.** The driver bounds itself to `abs_ceiling = deadline + 150 s`
(`CODEX_DEADLINE_SEC=2700` poll window + `CODEX_FINALIZE_BUDGET_SEC=150`), and
on the `pipeline()` fallback path the Workflow's own poll adds
`CODEX_WAIT_GRACE_SEC=600`, so the total W5
translate/review wait is bounded at `2700 + 150 + 600 = 3450 s` of polling plus one
final finite on-disk gate check — never an unbounded hang (the #198 failure mode).

**That bound covers the POLLING, not every syscall underneath it.** The on-disk gate
check reads the canonical entry with ordinary blocking filesystem calls — `lstat()`,
`open()`, `fstat()`, `read()`, `close()` — none of which is interruptible by the
budget checks that sit between them. Against a hung NFS or FUSE mount a single one of
those can block indefinitely, and no timer in this driver stops it. The wait arithmetic
above is exact for a responsive filesystem and is not a liveness guarantee on an
unresponsive one.
**1.16.1 (#348):** that bound is UNCHANGED; what changed is that it is now SPENT
ACROSS AGENT CALLS rather than inside one. The agent's Bash tool clamps any single
call at `BASH_CALL_CAP_SEC = 600 s` no matter what timeout the agent asks for, so
the Workflow poll runs up to `WAIT_CHUNKS = 8` chunk calls, one per
`WAIT_CHUNK_SEC = 480 s` slice. Each chunk polls only what is LEFT of the 3450 s,
so chunks 1–7 take 480 s and chunk 8 takes the remaining 90 s, summing to exactly
3450 — the chunks SPEND the declared POLLING budget, they never extend it.
Read that as a polling budget, not a wall-clock guarantee: 3450 s is the total
time the loop spends *polling*, and the elapsed time of a full wait is
necessarily somewhat longer, because eight chunk calls mean eight agent handoffs
and each chunk's final acceptance-gate invocation may straddle its own deadline.
Nothing here bounds wall-clock elapsed time to 3450 s, and the pre-1.16.1 single
call did not either. What the bound guarantees is termination — no unbounded
hang, the #198 failure mode — not a deadline. After them
comes ONE authoritative, non-polling
re-check of the canonical artifact (`WAIT_CALLS = WAIT_CHUNKS + 1 = 9` calls per
wait, worst case), so a job that finishes after the last chunk's poll ended is
still seen rather than discarded.
A timed-out dispatch leaves the segment `in_progress` and re-dispatches on the NEXT
W5 run — the ordinary ledger-resume path, no in-loop retry.

**The codex-companion path is NOT in the resume digest.** The absolute
`codex-companion.mjs` path (resolved per-machine by `resolve_codex_companion.py`
from the plugin's own install location) is an ENVIRONMENT fact, not project state:
it varies by machine / CC version and would spuriously force a fresh, no-resume run
every time a book moved machines or the plugin updated its install path. It is
therefore never folded into `resume_setup.py`'s resume-integrity digest nor any
bundle hash, and `resolve_codex_companion.py` itself is not a `PLUGIN_BUNDLE_MEMBERS`
entry either — for the same environment-fact reason, not because of where it runs
from. (This paragraph used to say it "is plugin-anchored and never copied to
`durable_root`, so it cannot be a bundle member at all". The premise was false —
the script reads no `__file__`, and globs the running config profile and `~` rather
than anything derived from its own location — and the exclusion it justified left
the self-anchored driver launch unable to dispatch; Step 0a now copies it like
every other self-anchored script. Non-membership is a deliberate allowlist
decision, unchanged.) What DOES gate resume for the driver is `codex_job.py`'s own bytes
(a `plugin_bundle_hash` member), so a driver-logic change still forces the correct
re-validation.

## Three ledger schema files

This is a real JSON Schema gotcha, and the reason there are three files
instead of two. `allOf` **intersects** constraints — it cannot widen an
enum. An `allOf` composing a narrow fragment `status` enum together with a
wider one (to add `stale`) would require an instance to satisfy *both*
enums simultaneously, which is impossible for `status: "stale"` specifically
since the narrower enum doesn't contain it. Nothing could ever validate
against a schema built that way. The fix is a shared, `status`-free base
schema plus two independent, sibling schemas that each declare their own
`status` enum — never composed against each other.

**`ledger-record-base.schema.json`** — fields common to every record. No
`status` property is declared here at all (deliberate — this is exactly
what prevents the enum-widening conflict above: referencing this base can
never create an enum conflict, since there's no `status` enum here to
intersect against):

```
{timestamp: string (REQUIRED, ISO-8601, unconditionally — the ONLY unconditional requirement),
 reason: string (optional),
 rounds: integer (optional, BARE integer — never {translate,review,fix} object — every branch of the real reviewFixLoop() returns a bare int),
 cache_key: {...15 fields, see below} (optional object),
 n_blocks, n_footnotes, n_verses: integers (optional — POPULATED BY ledger_update.py ITSELF by reading segpack_{seg}.json's array lengths, NEVER supplied by the calling agent's payload),
 reviewed_draft_sha1: string (optional — sha1 of segments/{seg}.draft.json's content, populated by ledger_update.py itself at the moment of convergence, NEVER supplied by the calling agent),
 note: string}
```

Conditional requirements are expressed via `allOf`/`if`/`then`. Each `if`
clause must include `"required": ["status"]` **inside itself** (not just
`properties.status`) — otherwise a status-absent instance vacuously
satisfies the `if` (JSON Schema's `properties` keyword only constrains a key
that is present; on an instance missing `status` entirely, the constraint
is trivially true), and the `then` branch would incorrectly fire for a
record that hasn't been given a status at all:

```json
"allOf": [
  { "if": {"required": ["status"], "properties": {"status": {"const": "converged"}}},
    "then": {"required": ["rounds", "cache_key", "n_blocks", "n_footnotes", "n_verses", "reviewed_draft_sha1"]} },
  { "if": {"required": ["status"], "properties": {"status": {"enum": ["non_converged", "blocked"]}}},
    "then": {"required": ["reason"]} }
]
```

`in_progress`/`pending` fall through with no extra requirement beyond the
base's unconditional `timestamp`.

**`ledger-fragment.schema.json`** — `allOf: [{$ref:
"ledger-record-base.schema.json"}]` plus its own `status` property:
`enum: [pending, in_progress, converged, non_converged, blocked]` — **no
`stale`, ever**. These are the only statuses `scripts/ledger_update.py`
ever writes to a fragment. Uses `unevaluatedProperties: false` (**not**
`additionalProperties: false`) — `additionalProperties: false` at this
level can't see properties satisfied by a sibling `allOf` branch (the
base's own properties) and would incorrectly reject them; `unevaluatedProperties`
is `allOf`/`$ref`-aware and correctly counts the base schema's properties as
already evaluated.

**`ledger.schema.json`** — the separate, materialized multi-record shape:

```
{"segments": {"type":"object", "additionalProperties": {"allOf": [{"$ref": "ledger-record-base.schema.json"}], "properties": {"status": {"enum": [pending, in_progress, converged, non_converged, blocked, stale]}}, "required": ["status"], "unevaluatedProperties": false}}}
```

This composes against the **same** status-free base as the fragment schema
— never against the fragment schema itself — so its wider enum (including
`stale`) never conflicts with anything. `stale` is a status
`ledger_merge.py` **computes** when it builds this map; it is never a value
found in any actual on-disk fragment.

## Composite cache key — exact 15-field structure

A segment is reused from cache only if **every one of these 15 hashes**
matches the current run's freshly-computed values **and** `status ==
converged`. A mismatch on any single field flips that segment's
materialized `status` to `stale`. How WIDE that is depends on which field
moved, and the two cases are not close: a mismatch on a per-segment field
invalidates only that segment, never the whole book, while a mismatch on a
field marked `(global)` below mismatches for every segment at once, because
the same value is written into all of their keys. This exact JSON literal is
the authoritative field list;
any other restatement of the field count/list elsewhere must match it.

```json
"cache_key": {
  "input_sha1": "...", "style_contract_hash": "...", "used_terms_hash": "...",
  "pipeline_version": "...", "schema_hash": "...", "prompt_hash": "...",
  "agent_config_hash": "...", "profile_semantics_hash": "...",
  "particle_config_hash": "...", "source_extraction_hash": "...",
  "source_input_hash": "...", "derivation_bundle_hash": "...",
  "verse_map_hash": "...", "note_map_hash": "...",
  "plugin_bundle_hash": "..."
}
```

**`scripts/cache_key.py`** is the one shared implementation computing all
15 (plus the one named exception below). CLI: `python3
{{DURABLE_ROOT}}/scripts/cache_key.py --seg <id>` prints the full JSON
object to stdout. `--field <name>` (no `--seg`) prints just one named
**global** field's current value — used at W2 by the producing extractor
(`extract.py.template` for `gutenberg_epub`/`plain_text`; the co-designed
custom extractor for `custom`, same two-phase-write obligation) and at W3
by the glossary-pass merge step to stamp their own `generation_hashes`
markers. Passing `--field` with a per-segment field name and no `--seg` is
a usage error.

Exact byte-scope per field:

- **`input_sha1`** (per-segment) — sha1 of this segment's own source
  content: the concatenated `source_html`/`plain_text` of every block in
  `segpack_{seg}.json`'s `blocks[]`, in `order_index` order.
- **`style_contract_hash`** (global) — sha1 of `style_bible.md`'s
  `style_contract` section only (sections A–F equivalent, **not** section
  G's glossary). Located via explicit markers `<!-- STYLE_CONTRACT_BEGIN -->`
  (immediately before section A) / `<!-- STYLE_CONTRACT_END -->`
  (immediately after section F, before section G). Hashes exactly the bytes
  strictly between the markers, never the markers themselves. Fails loudly
  (fatal, named) if: the begin marker is missing; the end marker is
  missing; either marker appears more than once; the end marker precedes
  the begin marker. Being GLOBAL, one edit inside the span flips every
  already-converged segment to `stale` at once. `style_contract_hash` is NOT
  in the machinery-only carve-out and never will be — that set means "can
  never change what the prose should say", which a contract can. Instead,
  `profile.yml`'s `validation.admit_contract_only_stale: true` (#533) opens a
  second, separately named acceptance path in `final_audit.py`'s
  `project_complete` and `assemble.py`'s assembly gate for a flipped unit
  whose `.ever_converged` sentinel is not absent, whose draft still matches
  its `reviewed_draft_sha1`, and whose every other moved field is
  machinery-only. Nothing is rewritten and no hash is stamped: the record
  still reads `stale`, and both gates list the admitted segment ids. Undeclared
  — or declared `false` — both gates refuse exactly as they did before.
- **`used_terms_hash`** (per-segment) — sha1 of the `canon.json` entries
  actually referenced by this segment's own `canon_names[]` **or**
  `new_names[]` list (from its segpack) that currently exist in
  `canon.json`'s `entries{}`. Includes `new_names[]`, not just
  `canon_names[]` — a name uncanonized at segpack-build time contributes
  nothing either way; the moment it's canonized elsewhere, its bytes enter
  this segment's hash for the first time, correctly flipping it stale. No
  persisted reverse index is needed — this is a live re-check each
  computation.
  Since 1.45.0 the segpack's `split_names{}` participates too (#488): an
  adjudicated homonym split is the one naming decision reaching the
  translator from OUTSIDE `canon.json` — `canon_validate.py` refuses to
  recollapse a split into a bare entry, so a split form is never in
  `entries{}` and the canon projection above cannot see it. Without it,
  adding a split, re-glossing a sense's `disambiguator`, or removing one
  would change what the translator is told while every per-segment key
  stayed put. **The hashed payload keeps its historical shape byte for byte
  whenever `split_names` is empty**, so a project with no adjudicated
  homonym sees no movement at all; only a segment genuinely carrying a
  split is reclassified. That equality is load-bearing rather than tidy:
  `used_terms_hash` is NOT in the machinery-only carve-out, so moving it
  everywhere would demand a whole-corpus re-review instead of an admitted
  stale.
- **`pipeline_version`** (global) — read directly, verbatim, from
  `project.pipeline_version` in `profile.yml`. Not computed, just copied
  through.
- **`schema_hash`** (global) — sha1 of the concatenated, filename-sorted
  bytes of `${durable_root}/schemas/draft.schema.json` +
  `review.schema.json` + `segpack.schema.json` — read from the
  project-local copy Step 0a placed, never `assets/schemas/`.
- **`prompt_hash`** (global) — sha1 of the concatenated, filename-sorted
  bytes of the project's own post-instantiation `translate_TASK.md` +
  `review_TASK.md` (the `.template` infix dropped — these are the copied,
  runtime filenames).
- **`agent_config_hash`** (global) — sha1 of canonical JSON `{effort:
  engine.effort, max_fix_rounds: engine.max_fix_rounds, model:
  engine.model}` from `profile.yml`. **`model` (#197)** is read
  defensively — `profile.get("engine", {}).get("model")`, never the
  fail-loud `profile_get` — because it's optional and real/fixture
  profiles routinely omit it; absent resolves to JSON `null`, meaning
  "codex config default (unpinned)". This folds the **requested** model
  only: codex-companion never reports back which model actually ran a
  job, so there is no resolved value to hash instead (see
  `references/operating-constellation.md`). `batch_agent_cap` is
  **deliberately excluded** — it's a
  pure orchestration/scheduling knob with zero effect on
  translator/reviewer output semantics; including it would invalidate every
  converged segment on a mere batch-size tweak. The exclusion side is only
  half of it, and until #732 it was the only half stated: changing any of the
  three fields this hash DOES fold invalidates every converged segment, and on the mass
  path it also moves `input_digest` — each segment's own `cache_key` is a
  digest `domain` member — so the same edit re-dispatches unfinished work as
  well. `profile.example.yml`'s `engine.effort` and `engine.max_fix_rounds`
  change-cost paragraphs spell out what an operator actually pays, in both
  directions (#732). The glossary pass has no
  per-segment cache key of its own, so it can't see an `engine.effort`
  change through `agent_config_hash` at all — its own resume-integrity
  digest instead gets `effort` directly via `resume_setup.py`'s
  `DIGEST_SUBST_FIELDS` (the hashed projection; `SUBST_FIELDS` beside it is
  the wider set every payload must SUPPLY — since #735 the two differ, see
  `references/orchestration-and-batching.md`'s digest definition); `model` is deliberately **not** added there, since the
  glossary pass has no model knob to begin with.
- **`profile_semantics_hash`** (global) — sha1 of canonical JSON
  `{source_lang: source.language.code, target_lang: target.language.code,
  verse_policy_mode: verse_policy.mode, verse_policy_threshold_lines:
  verse_policy.threshold_lines, apparatus_policy:
  footnotes.apparatus_policy, untranslated_sentinel:
  validation.untranslated_sentinel}` from `profile.yml` — exactly these six
  named fields, no more, no fewer. Deliberately does not duplicate
  effort/max_fix_rounds/model (that's `agent_config_hash`'s job).
- **`particle_config_hash`** (global) — sha1 of the resolved
  `particle_config` file's raw bytes — `${durable_root}/languages/<source.language.particle_config's
  literal value>` (same resolution rule as `bootstrap_names.py`, never
  reconstructed from `language.code`). Deliberately conservative: flags an
  edit as never-silent (segment flips stale), but does not auto-regenerate
  an already-built segpack — the operator must manually re-run
  `bootstrap_names.py` → glossary pass → `segpack.py` for affected segments
  first.
- **`source_extraction_hash`** (global) — sha1 of canonical JSON `{format:
  source.format, adapter_config: <ONLY the one sub-block matching the
  resolved format, never the whole adapter_config object>}`, concatenated
  with the resolved extractor file's own raw bytes
  (`${durable_root}/extract.py` for `gutenberg_epub` — and `plain_text` once
  implemented, #62 — or the resolved `adapter_config.custom.extractor_path`
  file). Same "flags, doesn't
  auto-regenerate" honesty as `particle_config_hash`.
- **`source_input_hash`** (global) — sha1 of canonical JSON `{source_path:
  <resolved source.path STRING itself>, source_bytes_sha1: <see below>}`.
  For `gutenberg_epub` (and `plain_text` once implemented, #62):
  `source_bytes_sha1` = sha1 of the source file's raw bytes. For `custom`
  (may consume multiple files): the
  extractor must emit `source_inputs: [string]` in `manifest.json` (every
  file path read, in read order); `source_bytes_sha1` = sha1 of canonical
  JSON `[{filename, sha1: <sha1 of THAT file's raw bytes>}]`, one entry per
  file, **sorted by filename**, hashing `{filename, sha1(bytes)}` pairs —
  never bare sorted-and-concatenated bytes (concatenated-bytes-only would
  let a secondary file get silently repointed at a byte-identical different
  file with no hash change — filename must be part of what's hashed, not
  just the sort key). `gutenberg_epub` (and `plain_text` once implemented,
  #62) also populate `source_inputs: [source.path]` for consistency.
  **Two-phase write**
  (chicken-and-egg: `source_inputs[]` lives inside `manifest.json` but
  `manifest.schema.json` also requires this hash to be present):
  The producing extractor (`extract.py.template` for `gutenberg_epub`/
  `plain_text`; the co-designed custom extractor for `custom`, same
  obligation) first writes a DRAFT `manifest.json`
  (`source_inputs` populated, `generation_hashes.source_extraction_hash`/
  `.source_input_hash` absent, deliberately not yet schema-valid, never
  validated at this point) → `cache_key.py --field source_input_hash`/
  `--field source_extraction_hash` read the draft's own `source_inputs[]`/
  `format`/`adapter_config` → both hashes are merged into the in-memory
  manifest object → one final validated write (tmp-write-then-`os.replace()`,
  same atomic pattern as `ledger_update.py`). `manifest.schema.json`
  validation runs only against this final write.
- **`derivation_bundle_hash`** (global) — sha1 of the sorted,
  filename-concatenated raw bytes of `bootstrap_names.py` + `segpack.py`'s
  own copies under `${durable_root}/scripts/` (**not** the
  `{filename,sha1}` pairing — that's specific to `source_input_hash`'s
  multi-file case; this one uses simple sorted-concatenation like
  `plugin_bundle_hash`, since it's just script bytes, not swappable file
  identities). Stamped into `canon.json`'s
  `generation_hashes.derivation_bundle_hash` (never `manifest.json`) via
  `--field derivation_bundle_hash`, invoked at W3 by the glossary-pass
  merge step, the same moment as `particle_config_hash`. Needs no
  two-phase write (depends on nothing inside the file it's stamped into).
  This field exists specifically to split `bootstrap_names.py`/`segpack.py`
  out of `plugin_bundle_hash` — closing the gap where a fix to either
  script would flip `plugin_bundle_hash` (→ ordinary `stale`, forcing a
  retranslate) without forcing the segpack itself to regenerate first
  (silently retranslating against stale segpack/canon data).
- **`verse_map_hash`** (per-segment) — sha1 of this segment's own
  `verses[]` array from its segpack (`vid`+`placeholder`+`parent_block` per
  entry) — catches a re-extraction that reassigns verse placeholders for
  this segment even when `input_sha1` (the underlying prose) hasn't
  changed.
- **`note_map_hash`** (per-segment) — sha1 of this segment's own
  `footnotes[]` array from its segpack (`n`+`source_text` per entry) —
  catches a footnote-apparatus re-extraction change for this segment
  specifically.
- **`plugin_bundle_hash`** (global) — sha1 of sorted,
  filename-concatenated bytes of the eighteen generic scripts that directly
  shape translate/review content (`ledger_update.py` included — its
  `reviewed_draft_sha1` binding-check logic directly determines
  correctness) plus the two workflow templates
  (`mass-translate-wf.template.js`/`glossary-pass-wf.template.js`). Never
  `bootstrap_names.py`/`segpack.py` (their own `derivation_bundle_hash`),
  and never the orchestration-only scripts (covered by the separate
  `orchestration_bundle_hash`: non-gating for convergence — never part of
  the composite cache key — but gating for resume, folded into the
  resume-integrity digest). See the exact membership list below.

**`--field smoke_report_contract_hash` is a deliberate exception** — not a
16th `cache_key` member (the 15-field JSON above is authoritative and
complete). It's sha1 of `language_smoke_report.py`'s own bytes — a
report-generator-version stamp gating W3's smoke-report reuse check, a
different category entirely, reusing `cache_key.py`'s CLI surface purely so
this one extra hash doesn't need a duplicate sha1-of-a-file implementation.
Any future addition to `--field`'s supported names must state explicitly
whether it is a composite `cache_key` member or an extra non-cache-key value
like this one.

**Keep restatements in sync.** The cache-key field list/bundle membership
is restated in several places in the shipped docs/schemas (the
`ledger-record-base.schema.json` field, `select_segments.py`/
`ledger_update.py`/`cache_key.py`'s own field handling, the bundle-membership
prose, design-decision text, and implementation steps). `used_terms_hash`
specifically has three restatement sites: its own cache-key definition, the
canon-and-glossary `new_names[]` description, and the W3 glossary-pass
workflow narrative. `draft.schema.json`'s mode-neutral-vs-`validate_draft.py`
ownership split has two restatement sites: the verse-policy table intro and
`draft.schema.json`'s own schema row. When adding or removing a cache-key
field, update all of them. Prefer deriving the expected field set
programmatically in tests (e.g. assert `cache_key.py --seg <id>`'s own
printed JSON keys equal `ledger-record-base.schema.json`'s declared
`cache_key` property set) rather than hand-typing the same list twice.

**The six-category segment classifier is a second restatement pair.**
`select_segments.py`'s full classification set — `reusable`, `stale`,
`blocked_needs_regeneration`, `recoverable`, `not_started`,
`human_escalation` — plus its `--only-segs`/`--allow-empty` CLI surface, is
restated in full here (see the classification section below) and again in
`SKILL.md`'s W5 section for the linear-workflow reader. Both restatements
are intentional and stay — SKILL.md's inline copy serves the linear-workflow
reader — but when changing the category names, their meaning, or the CLI
flags, update both sites. `final_audit.py` also hardcodes this same category
enum, so check it too when the set changes.

## The three separate bundle hashes — exact membership

Do not conflate these. They gate different things and have different
membership.

- **`plugin_bundle_hash`** (global, read from
  `${durable_root}/runs/.plugin_bundle_hash` — a marker file Step 0a writes
  once per run, not recomputed per segment) — covers exactly **eighteen
  scripts** (six pre-1.2.0, plus `review_ready.py` and `resume_setup.py`,
  new in 1.2.0, `glossary_batch_plan.py`, new in 1.3.5, `codex_job.py`,
  new in 1.4.7, `canon_senses.py`, added for RFC #215's homonym-split
  adjudication gate — it is a dependency of `canon_validate.py` and
  `glossary_batch_plan.py`, both already bundle members, so its own bytes
  must be registered too, `fetch_citation.py`, added in 1.16.1 as the
  validated retrieval boundary for the W3 citation audit (#347) -- it
  decides which citations may be fetched at all, so its bytes shape review
  content as directly as any validator, `segment_dispatch_driver.py`,
  added in #409 Step 4 as the W5 local driver — see below, and
  `claim_record.py`, added in #438 as the re-review claim predicate: it
  decides whether a segment was authorized for re-review at all, so a bug
  in it either grants an authorization nobody named or drops one the
  operator did, and `select_segments.py`, added in #446 as the script that
  owns the EXISTING dispatch gate: the Step 1 ever-converged refusal, the
  claim admission arms, and the classification every one of those decisions
  reads — `cache_key.py`'s own comment block holds why it was left out until
  then, and `refuse_finding.py`, added in #764 as the sole producer of the
  per-finding refusal record: it is NOT a decision authority — nothing in the
  driver reads that record and no gate consults it — but it is the only writer
  of durable state that `fixPrompt` splices verbatim into a turn authorized to
  rewrite the draft, and every field it writes is admitted by a bound that
  script owns, so a later tightening of those bounds must not leave records
  written under the looser rule invisible to this hash) plus the two
  workflow templates: `validate_draft.py`, `canon_validate.py`,
  `cache_key.py`, `draft_sha1.py`, `review_artifact_check.py`,
  `ledger_update.py`, `review_ready.py`, `resume_setup.py`,
  `glossary_batch_plan.py`, `codex_job.py`, `canon_senses.py`,
  `fetch_citation.py`, `segment_dispatch_driver.py`, `claim_record.py`,
  `reject_review.py`, `refuse_finding.py`, `select_segments.py`,
  `json_stdout.py`, plus
  `mass-translate-wf.template.js`/`glossary-pass-wf.template.js`.
  `json_stdout.py` (#369) is registered for exactly the reason
  `canon_senses.py` was — this is a byte-hash allowlist, so a dependency
  six members of it now load is otherwise invisible to it — and it owns the
  one-line stdout serialiser, which reaches durable content: the cache-key
  CLI's printed object is parsed back as the live cache key by the selector
  and copied verbatim into a ledger payload by the mass-translate template. These are
  scripts that directly shape extraction/translation/review/validation
  content, or determine whether a convergence verdict was correctly
  recorded — `review_ready.py` (the review-side readiness counterpart, but
  gating and correctness-critical rather than a diagnostic poll: it's what
  certifies a `review.json` safe to consume) and `resume_setup.py`
  (computes the resume-integrity digest itself) both meet that bar, as does
  `glossary_batch_plan.py` (W3's candidate→batch curation: it decides which
  candidates are dispatched to the glossary pass, so its bytes directly
  shape glossary content — note that `plugin_bundle_hash` is itself member
  15 of the cache-key composite, so a change here re-invalidates converged
  mass segments coarsely, the same as any plugin-bundle member; that is the
  accepted cost of the correct bucket, chosen because — unlike
  `derivation_bundle_hash` — it actually reaches the glossary digest and
  leaves the canon generation stamp intact), `codex_job.py` (the W5
  translate/review driver: it launches codex and VALIDATES the isolated
  attempt before atomically promoting it to canonical, so its bytes directly
  determine whether a draft/review is correctly produced and accepted — an
  old buggy driver may have wrongly accepted an artifact, so a driver-only
  change must re-invalidate converged work), and `segment_dispatch_driver.py`
  (#409 Step 4, the W5 LOCAL driver: it owns the ACCEPT decision for
  dispatched work — which segments even get dispatched, via the Step 1
  re-translate gate, and whether a lease/volume refusal is honored — the
  identical reasoning `codex_job.py` is registered under, applied to the
  process that decides what reaches `codex_job.py` in the first place).
  **Part
  of the cache key** (as `plugin_bundle_hash`) — a mismatch flips a segment
  straight to `stale`.
- **`orchestration_bundle_hash`** (global, sibling marker file
  `${durable_root}/runs/.orchestration_bundle_hash`, same computation
  timing) — covers exactly **six scripts**: `claim_record.py`,
  `draft_ready.py`, `json_stdout.py`, `ledger_merge.py`,
  `language_smoke_report.py`, `select_segments.py`.
  **Never added to the cache-key composite, never compared against any
  segment's cache key** — non-gating for convergence, but it IS folded
  into the resume-integrity digest (see below), so it gates resume: a
  changed marker forces a fresh, no-resume run. Also logged in W8's
  reporting ("processed under plugin-bundle X, orchestration-bundle Y").
- **`derivation_bundle_hash`** (part of the 15-field cache_key, see above)
  — covers exactly **two scripts**: `bootstrap_names.py`, `segpack.py`.
  Their bytes do shape content, but they need the derivation-state gate's
  regenerate-before-retranslate treatment (`blocked_needs_regeneration`,
  see below), not either simpler bundle's flip-straight-to-stale/
  never-gates treatment.

The orchestration list above is a restatement: `scaffold_setup.py`'s own
`ORCHESTRATION_BUNDLE_MEMBERS` tuple is the authority, and its
`test_orchestration_members_pinned` holds that tuple byte-for-byte. Read the
tuple if the two ever disagree. Note that **three** of its six entries sit in
`PLUGIN_BUNDLE_MEMBERS` as well, deliberately, so a change to any one of them
moves **two** hashes: it re-stales converged segments AND forces a no-resume run.
`claim_record.py` since #438 — it gates dispatch, which earns it the plugin
bundle, and `select_segments.py` imports it, so its bytes must move this marker
too. `select_segments.py` itself since #446 — it was always an
orchestration member, and it owns the dispatch gate `claim_record.py` merely
supplies the claim predicate for, so the same criterion had always applied to
it. And `json_stdout.py` since #369 — `ledger_merge.py` and
`select_segments.py` load it here, and six plugin members load it there, so
the same byte-hash-allowlist criterion that registered `canon_senses.py` applies
in both tuples at once. The remaining three (`draft_ready.py`, `ledger_merge.py`,
`language_smoke_report.py`) are orchestration-only.

`profile_validate.py` is excluded from **all three** bundles — it is never
copied to `durable_root` at all; it's always invoked from the plugin's own
install path.

Both `plugin_bundle_hash` and `orchestration_bundle_hash` are single sha1s
over the concatenated bytes of their member files, sorted by filename for
determinism: scripts/templates for `plugin_bundle_hash`, and scripts for
`orchestration_bundle_hash`, computed by Step 0a at the moment it copies
scripts into `${durable_root}/scripts/`.

**A `converged` count is bundle-relative.** A release that edits any
`PLUGIN_BUNDLE_MEMBERS` entry moves `plugin_bundle_hash` — most do; a docs-only
one does not — and the Step 0a refresh that installs it then flips the whole
converged corpus to `stale` at once, with no draft byte touched. The tally
therefore falls at each such release, while the translation never regresses and
work continues between them: one live book read 4 → 2 → 1 over three releases
in three days, another 75 `stale` / 0 `converged` on a tree whose work was
intact (#482). What it counts is "converged under the CURRENT bundle", not how
much of the book has been reviewed.

Since 1.25.0 (#491) that population no longer blocks either gate that answers
"is this book done": the always-on machinery-only carve-out lets `assemble.py`
and `final_audit.py` treat such a record like `converged` — its conditions,
which include an `.ever_converged.<seg>` sentinel that is not ABSENT and the
draft-sha1 match no carve-out relaxes, live in
`references/assembly-and-output.md` — and `final_audit.py` prints
`stale_previously_converged=` beside its completeness counts. That pair is the
answer. The raw `converged` tally is not, and neither is a count of
`.ever_converged` sentinels: the sentinel is written at convergence and never
removed by any ledger write, so it answers "did this unit ever converge", a
different question.

W5 dispatch still refuses this population, and that is where a fallen tally
costs something. A claim under `--from-converged` is refused for it (W5 owns
that rule), and a plain re-dispatch FATALs on the `.ever_converged` sentinel;
`--classify-only` reads the classification without translating. Every sentence
above rests on that sentinel: a project that converged units before the sentinel
existed has none, so until `backfill_ever_converged.py` has run there (SKILL.md's
W5 step) neither the carve-out nor this refusal applies to it — assembly refuses
the unit and a dispatch retranslates it unasked. Nor is the
carve-out a judgement that the release changed nothing a translator was told —
`PLUGIN_BUNDLE_MEMBERS` includes the two workflow templates, and their text is
where the translate and review prompts are built — and no gate makes that
judgement. Running these units against a changed instruction therefore means
authorizing `--allow-retranslate-converged`, and since 1.70.x that costs one
thing rather than two. The converged units retranslate — that is what the flag
authorizes. Every not-yet-converged draft in the same selection is still
orphaned by the fresh RUN_ID, but since #742 `segment_dispatch_driver.py`
REFUSES the whole invocation over those drafts (exit 1) instead of
retranslating them, naming each one and the run its `dispatch_token` belongs
to. The flag's own refusal text states both numbers; the second one is now a
warning about a dispatch that will refuse, not about work that will be
destroyed.

`select_segments.py`'s classification report does not split the bucket — its
`counts`/`ids_by_category` are keyed by the six flat categories — so on that
surface the distinction lives in each entry's `stale_reason` and
`mismatched_fields`, read together. A unit stale for draft drift alone carries
an EMPTY `mismatched_fields`, which satisfies "every moved field is
machinery-only" vacuously while being exactly the population assembly refuses.

**Recovering a draft the refusal named (#742).** The refusal itself enumerates
the routes — an owner-scoped pin, deleting the draft, or re-stamping it — each
with the precondition that makes it work, and it is the copy to follow: an
operator reading a halt is not reading this file. Restating them here would be
a second copy free to drift from the string the operator actually sees.

Two things the message does not have room to say. First, the MECHANICS of a
safe re-stamp: rewrite that draft's `dispatch_token` to the RUN_ID the refusal
reports and leave every other byte alone, asserting per file — before and after
— that `draft_content_sha1()`'s projection is unchanged. It excludes
`dispatch_token` by design (see `scripts/draft_sha1.py`'s own module docstring),
so it is exactly the right witness; do not re-implement the projection by hand
to check it. Second, the PROOF that it worked: a re-stamped draft is adoptable
again, and the next dispatch's journal says so — `"kind": "review"` for that
segment, with no translate job beside it.

`resumeFromRunId` is explicitly scoped to continuing the same interrupted
batch run. It is never the same mechanism as the ledger-driven
skip-if-cached/resume classification, which is re-derived from fragments,
cache keys, and `select_segments.py`.

## `scripts/ledger_update.py` — the fragment writer

Invoked shelled out from inside an agent's own turn — never directly by the
Workflow JS, which has no confirmed filesystem access. CLI: `python3
{{DURABLE_ROOT}}/scripts/ledger_update.py {seg} --payload-file <path>`. The
agent first writes its intended fields as a JSON file (no shell
interpolation of field values) to
`{{DURABLE_ROOT}}/runs/.ledger_update_payload.{seg}.{pid}.json`, then
invokes the script with just that path.

The script reads the payload and validates it against an embedded payload
sub-schema. The caller may set only: `status`, `rounds` (a **bare
integer**), `reason`, `note`, `cache_key` — deliberately never
`n_blocks`/`n_footnotes`/`n_verses`, which the script derives itself from
`segpack_{seg}.json`'s array lengths for a `converged` payload. **1.2.0:**
a `status:'converged'` payload also carries the current run's
`dispatch_token`, so the script can perform the commit-gate check below
before it commits to recording convergence. A malformed payload is refused
(non-zero exit, no write). The scratch payload file is deleted on success.

**The converged-write commit-gate check (1.2.0, point 3 of the token/sha
chain above).** Before writing `status: 'converged'` to the fragment, the
script additionally asserts: the on-disk draft's `dispatch_token` equals the
payload's token, AND `review_path(seg)`'s `dispatch_token` equals the same
token, AND (the pre-existing check, widened, see below) the draft's current
sha1 equals `review_path(seg)`'s recorded `draft_sha1`. Any one of these
three failing refuses the write outright — `{"success": false,
"error": "..."}`, never a partial or best-effort convergence record.

**Every write is a full replace, never a read-modify-write merge.** The
fragment written is built entirely fresh from: (1) `timestamp: now()`
(always regenerated); (2) `status` plus whichever other fields this payload
supplied; (3) the derived `n_blocks`/`n_footnotes`/`n_verses` when
`status: 'converged'`. The prior on-disk fragment's field *values* are
never read into the new record — only read for `os.replace()`'s
rename-target-existing check, never for content. An `in_progress` write
(payload `{status}` only) produces a fragment with no `reason`/`rounds`/
`cache_key`/segment-stats at all, even if the prior fragment had a full
`converged` shape.

Write pattern: `runs/ledger.d/{seg}.json.tmp.<pid>` → `os.replace()`
(atomic same-filesystem rename) → `runs/ledger.d/{seg}.json`.

On success, prints one JSON line to stdout matching
`ledger-write-confirmation.schema.json`'s success branch: `{"success":
true, "status": "...", "fragment_path": "...", "fragment_sha1": "<sha1 of
the just-written file>"}`. On failure: `{"success": false, "error": "..."}`
(plus optional `exit_code`/`stderr`). The two branches are not the same
shape — a failure never claims a `fragment_path`/`fragment_sha1` that
doesn't exist.

## `recordLedgerPrompt` — the schema-validated workflow-level call

`agent(recordLedgerPrompt(seg, fields), {effort:'low', schema:
LEDGER_WRITE_SCHEMA})` where `fields = {status, reason?, rounds?,
cache_key?}` (a converged call additionally threads the current run's
`dispatch_token` through to the payload — see the commit-gate check above).
No ledger write happens through any other channel. The prompt instructs the
agent to: (1) write the payload file and run `ledger_update.py`; (2)
**re-read the fragment file `ledger_update.py` claimed to write, from disk,
and compute its sha1 independently — then compare it against the
`fragment_sha1` the script's stdout claimed** (this closes the gap where a
model could echo back a fabricated or stale claim); (3) only then return
the structured response — `success:false` with a descriptive error if the
independent re-read's hash doesn't match.

`LEDGER_WRITE_SCHEMA` is now **flat** (the `#87` fix — see
`references/workflow-schema-validation.md`): `{type:"object",
additionalProperties:false, required:["success"], properties:{success:
{boolean}, status:{string}, fragment_path:{string}, fragment_sha1:{string},
error:{string}, exit_code:{integer}, stderr:{string}}}`. The on-disk
`ledger-write-confirmation.schema.json` stays the strong `oneOf` it always
was (success requires `{success: true, status, fragment_path,
fragment_sha1}`; failure requires `{success: false, error}`, plus optional
`exit_code`/`stderr`) — `ledger_update.py` itself still only ever emits one
of those two exact shapes; the flat literal only relaxes what the *agent* is
allowed to relay. The Workflow's own consume-site JS guard re-establishes
the branch discrimination the flat schema can't: a `success:true` return is
only trusted when `{status, fragment_path, fragment_sha1}` are all non-empty
strings, every key it carries is one this literal declares, and no
`error`/`exit_code`/`stderr` field carries actual **evidence of failure** —
a crossover payload like `{success:true, error:"x"}` is treated as a
failure, never a success.

The guard judges those three fields by VALUE, not by presence (**#289**).
Because the flat literal advertises them as fillable on *every* call, an
agent honestly relaying a successful run routinely volunteers
`exit_code: 0` — which is proof the script SUCCEEDED. The pre-#289 guard
rejected on presence alone and so failed segments whose fragments were
already correctly on disk, non-deterministically (whether an agent
volunteers the field is model discretion: on one live 3-segment batch, two
agents included it and were failed, the third omitted it and passed, on
identical prompts). What now counts as failure evidence: a non-empty
`error` or `stderr`, any `exit_code` other than `0`, or a wrong-typed value
for any of the three (unreadable evidence fails closed).

**JS-side payload-intent verification** (closes "wrong segment/status
silently accepted as success"): immediately after the schema-validated
`agent()` call returns `success:true`, the workflow script itself — not the
agent, not a new prompt — asserts that the returned `fragment_path`'s
segment-ID component matches the `seg` the JS originally passed in, and
that the returned `status` matches `fields.status` the JS originally
intended. This is a deterministic, code-level comparison — zero new agent
behavior, zero new schema fields, since the JS already holds both values
being compared. A mismatch returns `{seg, converged:false,
reason:'ledger-write-mismatch', detail: <naming the disagreed field(s)>}`,
never a same-channel retry.

On `success:false` from the script itself or the independent hash-verify:
this is a **workflow/run failure**, not a segment terminal status written
through the same channel. `reviewFixLoop()` does not attempt another
`recordLedgerPrompt` call for this segment — it returns `{seg,
converged:false, reason:'ledger-write-failed', detail: <error>}` directly as
this segment's Workflow `pipeline()` result. This is distinct from `blocked`
(which presumes the ledger successfully recorded that state).

## `mergeLedgerPrompt` / `ledger_merge.py` — completeness verification

Mandatory and blocking. `agent(mergeLedgerPrompt({expectedSegs: SEGS}),
{effort:'low', schema: LEDGER_MERGE_SCHEMA})` — `SEGS` is the same array
`select_segments.py` emitted, never separately hand-typed. The prompt
instructs the agent to: (1) run `python3
{{DURABLE_ROOT}}/scripts/ledger_merge.py --expected-segs <SEGS,
comma-joined>`; (2) capture stdout JSON (`{success, ledger_path,
n_segments, missing_segments, stale_segments}` on success); (3)
**independently re-read `ledger.json` and verify it's a completeness/subset
check, never exact key-set equality** — `ledger.json` accumulates fragments
across every batch ever run, so extra keys from prior batches are
explicitly allowed; only a `SEGS` name with no matching key at all is a
failure; (4) return `LEDGER_MERGE_SCHEMA` only after this independent
check.

`LEDGER_MERGE_SCHEMA` is now **flat** (the `#87` fix): `{type:"object",
additionalProperties:false, required:["success"], properties:{success:
{boolean}, ledger_path:{string}, n_segments:{integer}, missing_segments:
{array,items:string}, stale_segments:{array,items:string}, error:{string},
exit_code:{integer}, stderr:{string}}}`. `missing_segments` is a
deliberately **relaxed** union — no `maxItems` — unlike the on-disk success
branch's `{type:"array", maxItems:0}`; the JS guard is what actually
enforces emptiness on the success path. The on-disk
`ledger-merge-confirmation.schema.json` stays the strong `oneOf` it always
was (success requires `{success: true, ledger_path, n_segments,
missing_segments: [] (empty), stale_segments}`; failure requires
`{success: false, error}`, plus optional
`missing_segments`/`exit_code`/`stderr`) — the consume-site JS guard
re-establishes discrimination on the agent-relayed object: a `success:true`
return is only trusted when `{ledger_path, n_segments, missing_segments,
stale_segments}` are all present and well-typed (ledger_path a string,
n_segments an integer, `missing_segments` an EMPTY array, `stale_segments`
an array), every key it carries is one this literal declares, and no
`error`/`exit_code`/`stderr` field carries evidence of failure (same
value-based rule as the write guard above — **#289**). A benign
`exit_code: 0` never excuses a non-empty `missing_segments`: the
completeness check is what this call exists for.

**1.2.0: the batch-final commit-gate check (point 4 of the token/sha chain
above).** Before this check reports `success:true`/lets `batchComplete`
proceed, it additionally re-asserts, for **each** segment `select_segments.py`
expected converged this batch: the on-disk draft's `dispatch_token` equals
the current run's token, `review_path(seg)`'s `dispatch_token` equals the
same token, AND the draft's current sha1 still matches the
ledger-recorded `draft_sha1`. Any single segment failing this flips the
whole check to `success:false` — not complete — even if every individual
segment's own per-segment convergence write (above) already passed; this is
the gap a per-segment-only check can't close (a straggler pair restored
*between* the per-segment write and this batch-level check).

`mass-translate-wf.template.js` runs this check itself as its own final
step, right before the Workflow returns its overall result — a batch is not
complete until this passes. On `success:false`: a workflow/run failure
(`{batchComplete:false, reason:'ledger-merge-failed', detail}`), never
written through the per-segment ledger channel it exists to independently
verify.

**`scripts/ledger_merge.py`** (generic): reads every `runs/ledger.d/*.json`
fragment and materializes the single `ledger.json` matching
`ledger.schema.json`'s `segments{}` shape. Run on demand or after a batch —
never itself a write target. **Computes `stale` itself** by calling
`cache_key.py --seg <id>` per fragment and comparing against the stored
`cache_key` — marks mismatches `stale` in the materialized output only (the
on-disk fragment is never rewritten). Flags: `--expected-from-manifest
{{DURABLE_ROOT}}/manifest.json` (reads segment IDs from `manifest.json`'s
`segments[]`) or `--expected-segs seg05,seg06,...` (explicit partial-batch
list) — either enables the missing-fragment completeness check; without
either, it still materializes `ledger.json` but skips the completeness
check. **#463: it never reports a fragment directory it could not READ as
empty, and never publishes an empty ledger over a populated one.** A
`runs/ledger.d/` that is absent (ENOENT, or the name is a plain file) is
still the ordinary "nothing written yet" and still merges to an empty
ledger; any other error listing it — a permissions change, a vanished
mount — now fails the merge instead of being read as emptiness. Separately
and whatever the cause, a merge that would take a populated `ledger.json`
to ZERO segments refuses, as does one that produced zero segments while
the existing `ledger.json` cannot be read or parsed at all. No flag
overrides this: a deliberate reset is performed by deleting
`runs/ledger.json` first, which is an operator act rather than something
an accident can do. The refusal happens before the atomic replace, so a
refused merge leaves the existing ledger byte-for-byte intact.

**1.2.0:** the completeness check itself now also requires the current run's
token (threaded through the same invocation) to perform the per-segment
token/sha re-check above.

## The `recordLedgerPrompt` call sites

All in `mass-translate-wf.template.js`, all through this one
schema-validated call — no ledger write happens any other way. **1.3.6
(#131) removed two of the six sites that existed as of 1.2.0.** The
translate-timeout write, and the blocked-terminal write covering
review-timeout/review-null/review-artifact-mismatch (and, 1.3.6/#133, the
NEW review-fabricated-loc reason — see `findingsAuthentic`/
`AUTHENTIC_LOC_RE`), are GONE: every one of those reasons is
transient/mechanical (a codex agent that died mid-dispatch, an infra
hiccup, a schema-valid verdict caught referencing a phantom finding),
never genuine content non-convergence, so writing a terminal status there
would incorrectly take the segment out of `select_segments.py`'s
recoverable classification for good. Instead, the segment's `in_progress`
fragment (site 0 below) is left as the durable record, and
`select_segments.py`'s own "any non-terminal/unrecognized status ->
recoverable" rule auto-redispatches it on the next run. **Four sites
remain**:

0. **Translate-dispatch** — right before `agent(translatePrompt(seg), ...)`
   fires: `recordLedgerPrompt(seg, {status:'in_progress'})`, awaited.
   Closes the gap where an interruption between dispatch and any terminal
   write would otherwise leave zero durable record. **1.3.6:** this
   `in_progress` write is now also the ONLY durable record a
   translate-timeout, a review-timeout/review-null/review-artifact-mismatch/
   review-fabricated-loc, or a transient fix-call failure (the new
   `draftPresentAndValid` probe reports the draft present-and-valid, OR the
   probe call itself fails and returns `null` — inconclusive, never treated
   as proof of absence) leaves behind — none of those write a terminal
   status anymore.

   **#620 — `segment_dispatch_driver.py` writes this site TWICE, and only the
   second write ORIGINATES evidence.** The pre-dispatch write above never
   mints a `note` of its own: it exists to make an interruption recoverable,
   and because it happens *before* the job it can only ever prove intent — a
   driver killed between the two, a failed launch, or a `codex_job.py` that
   adopted an already-valid canonical without launching would all leave it
   standing over a draft no translate produced. It does, however, **re-state
   verbatim** a promotion note it finds already on disk (an `in_progress`
   fragment whose `note` carries the promotion prefix), because this write
   replaces the fragment wholesale: writing it bare would erase the evidence
   that had just authorized the re-translate, and a transient dispatch
   failure would then leave the draft untouched with nothing naming it — the
   next derivation halting terminally at `invalid_post_fix_draft` over a
   draft no operator ever touched. Re-stating cannot manufacture evidence:
   the string is unchanged and the canonical draft is not touched in between,
   so the reader's equality test against the *current* draft's hash comes out
   the same either way. Nothing else is carried — not the #432/#461 reopen
   note, not a rejection note, not a promotion-shaped note on a terminal
   status. So once `run_one_codex_job()`
   returns a genuine promotion (`ok` and not `adopted`, which is exactly
   `codex_job.py`'s own `promoted`, since its `finalize()` sets
   `ok = promoted or adopted`), the driver writes the fragment a second time
   with `note` = a fixed prefix followed by the **promoted draft's own content
   sha1**. `derive_next_action()`'s `if not draft_ok:` branch accepts nothing
   else as proof that an invalid, moved draft came from a translate rather
   than from an operator's hand repair — a constant marker would keep reading
   true after that repair, which is the defect. Both halves are compared:
   the prefix byte for byte, the hash against a fresh reading of the draft.
   The workflow's `translateStage()` is deliberately NOT stamped (its
   translate is a detached dispatch, so a returned DISP proves a launch and
   never a promotion), which is why a driver pickup of a template-written run
   halts there instead of re-translating. **That halt persists.**
   `invalid_post_fix_draft` writes no ledger entry, so the fragment, the
   review and the run identity are all unchanged, `select_segments.py` keeps
   classifying the fragment `recoverable`, and selection arguments stay out
   of the mass digest — so re-running with `--only-segs` reproduces the same
   halt rather than clearing it, and `--from-stalled` is refused at the claim
   guard before its ledger write. What clears it is making the draft
   structurally valid again: repair it, or delete it so the segment
   re-translates from scratch. That is the action the halt exists to demand,
   and it is the same recovery `invalid_post_fix_draft` already requires
   everywhere else it fires. Adoption is likewise never stamped — including
   `adopt_pending()`, which does replace the canonical — so an adopted
   segment halts rather than retrying, again the safe direction.
1. **Draft-missing** — a fix round's `DRAFT_MISSING` branch fires, AND
   (1.3.6/#131) the `draftPresentAndValid` probe confirms the draft is
   genuinely absent/invalid (`present === false`). **1.16.0:** that branch is
   entered on CONTAINMENT (`mentionedAnywhere()`), not whole-line equality, so
   it now also fires on a reply that merely mentions the sentinel — the probe
   is what keeps this terminal write honest, since it is the probe and not the
   branch that decides the draft is really gone:
   `recordLedgerPrompt(seg, {status:'blocked', reason:'draft-missing'})`.
   Matches the real reference's own `DRAFT_MISSING` handling, refined by
   1.3.6 to require the probe's confirmation first — a probe result of
   `true` (draft present and valid) or `null` (the probe call itself
   failed) instead ends the segment as `fix-call-failed`, reusing the
   in_progress write from site 0 with NO additional ledger write (see that
   site's note above, and `draftPresentAndValid`'s own comment in
   `mass-translate-wf.template.js` for why a `null` probe result must never
   be treated as proof of absence — a correlated outage on both the fix
   call and the probe call must stay recoverable, not fall through to a
   terminal `draft-missing` write).
2. **Converged** — `recordLedgerPrompt(seg, {status:'converged',
   rounds:<bare integer>, cache_key:{...freshly computed 15 fields...}})`
   (plus the current run's `dispatch_token`, 1.2.0). The payload does not
   include `n_blocks`/`n_footnotes`/`n_verses` (`ledger_update.py` derives
   them). **`reviewed_draft_sha1`/`dispatch_token` binding:**
   `review.schema.json` requires the reviewer's own `draft_sha1` — computed
   by the reviewer before reading the draft (hash-first-then-read narrows,
   but does not eliminate, a TOCTOU window — best-effort risk-reduction,
   not airtight closure). At the converged call site, `ledger_update.py`
   reads this value back off `review_path(seg)`, computes a fresh sha1 of
   the current on-disk draft, and compares — **and, 1.2.0, also checks
   both the draft's and `review_path(seg)`'s `dispatch_token` against the
   current run's token** (see the commit-gate chain above): **all match** →
   store `reviewed_draft_sha1` (the hash of what the reviewer most likely
   judged); **any mismatch** (draft changed in the window, or either
   artifact belongs to a different run) → refuses to write converged at
   all, returns `{success:false, error:"..."}` naming which check failed,
   which becomes `{seg, converged:false, reason:'ledger-write-failed',
   detail}` — the same escape hatch every other write failure uses.
3. **Non-converged (cap reached)** — terminal, no further automated step:
   `recordLedgerPrompt(seg, {status:'non_converged', reason:'cap',
   rounds: MAXFIX+1})`, `reviewFixLoop()` returns `{converged:false,
   reason:'cap', ...}` — full stop, human-escalation item exactly like
   `blocked`.

### The fifth terminal write, and the only one NEITHER dispatch path makes (#398)

The four sites above are all written by the Workflow template. `codex_job.py`
makes a fifth, and it is the only terminal ledger write both dispatch paths
inherit -- the Workflow `pipeline()` path and `segment_dispatch_driver.py`
alike, neither of which is changed to get it:

- **Translate-rejected** -- a `--kind translate` job whose candidate attempt
  `validate_draft.py` RAN against and rejected with **exit 1**, its contract
  for "the candidate's own content is defective" (see that script's own
  *Exit codes* section). `codex_job.py` invokes `ledger_update.py` directly
  with `{status:'blocked', reason:'translate-rejected'}`, so
  `select_segments.py` classifies the segment `human_escalation` instead of
  `recoverable` and stops auto-redispatching it. `--only-segs` still reaches
  it, exactly as the `blocked`/`draft-missing` fragment at site 1 is retried.

  Why the child and not either caller: neither caller can see the
  distinction. The template has no filesystem access at all and launches
  `codex_job.py` with `>/dev/null 2>&1`; the driver sees only the child's
  `reason` string, which reads `validate-failed` for a sandbox-publish
  failure and a non-regular attempt file as well as for a real rejection.
  The exit code is visible in exactly one process.

  **Exit 1 and nothing else.** Exit 2 (usage/environment/source
  availability), a gate that could not run at all, and every `draft_ready.py`
  rejection stay recoverable -- the segment keeps its `in_progress` fragment
  and is re-dispatched next run, exactly as before. Widening that would turn
  a transient hiccup into a segment an operator has to rescue by hand.

  **Two trigger sites, one rule (#665).** The same write is taken from either
  place a translate candidate can be gated: `validate_attempt()`, for the
  attempt this run just produced, and `adopt_pending()`, for a candidate a
  PRIOR run deferred (`reason: deferred-completed`) because it completed with
  no budget left to validate. The second route was left open by #398 and
  closed by #665: `adopt_pending()` used to report both "this pending's
  cross-run token is stale" and "this pending's content is defective" as one
  bare `False`, so `run()` could not tell them apart and fell through to
  `launch()` -- paying for a full translation the gate had already refused,
  once per run, for as long as validation kept being deferred.

  What separates the two causes is `adopt_pending()`'s gate ORDER, which
  `_adoption_gates()` owns: `draft_ready.py` carries `--expect-token` and runs
  FIRST, and the loop returns on its rejection. Reaching `validate_draft.py`
  at all therefore proves the pending's own `dispatch_token` matched the
  current run, so an exit 1 there is a same-token verdict on content -- never
  a stale token. A stale token, an exit 2, a gate that could not run, and a
  review candidate all keep the behaviour the pending slot exists for: discard
  the pending and launch fresh -- except a gate that could not run at all,
  which KEEPS it, because that is recoverable work nothing has judged.

  **What the gates judge changed with it.** `adopt_pending()` used to point
  each gate's `--candidate-file` straight at the pending slot. That name is
  deterministic and persists across runs, and every gate re-OPENS it by path,
  so there is a writable window between the two opens. (Who can write it is
  narrower than it looks in ONE respect only: since #409 the codex process this
  driver launches runs in a sandbox `_setup_sandbox()` refuses to dispatch into
  unless it is proved confined, so that one actor cannot reach `segments/`.
  #697: the property, not a population — anything that can list `segments/`
  discovers these names and anything that can write it can overwrite them.
  `codex_job.py`'s `_trusted_scripts_dir()` comment names shipped passes that
  hold write access over the whole durable root.) That was tolerable while
  every rejection was recoverable; it stopped being tolerable once a
  `validate_draft.py` exit 1 became terminal, because that script answers a
  missing or malformed candidate with exit 1 too. An ordinary truncate-and-
  rewrite in that window is indistinguishable from a content verdict.

  No re-check of the slot closes that: a type re-check passes an in-place
  overwrite, and a before/after digest passes a truncate-then-restore (both
  samples read bytes the validator never saw). So the gates now judge a
  per-invocation SNAPSHOT instead -- copied once through the same fd-pinned,
  digest-verified primitive `validate_attempt()` already uses, into the
  `.att.<seg>.<inv>...` name that carries `os.urandom(8).hex()` -- and the
  promote moves the very bytes that were judged. A snapshot that cannot be
  taken is not a verdict: the pending survives and the run launches fresh,
  exactly as when a gate could not run.

  **What the snapshot buys, stated at its real strength (#697, now closed).** It
  is no longer the DETERMINISTIC slot that persists across runs and is trivially
  derivable, so an ordinary cross-run collision stops being able to decide a
  verdict, and the deferred path is exactly as strong as the fresh one. Since
  #697 it also no longer lives in `segments/` at all: both terminal-verdict
  paths gate a candidate inside a per-invocation `mkdtemp` directory outside
  `durable_root`, so **discovery by listing `segments/` — the one channel that
  issue is about — is closed**. Exactly three things that move does NOT buy, and
  they are stated wherever it is described because the history of this issue is
  prose claiming more than the mechanism delivers: `argv` still carries the path
  to every gate subprocess for the whole gating window, so it is a *pre-verdict*
  channel; no same-uid process is excluded by a directory move; and the staging
  directory is not outside every codex write root, because codex-companion
  resolves `workspace-write` by walking up to the enclosing Git root while the
  sanctioned manual W5 drive runs with `--write` and `cwd = durable_root`. The
  paragraph below describes what the OLD in-`segments/` location depended on and
  is kept because `.att_pending.*` and the joblog still live there. Who can write
  `segments/` comes from its own mode, which the driver never sets (a bare
  `os.makedirs`, so the operator's umask decides), **not** from these
  entries' `0600` — a process with write on the directory can unlink and
  recreate any of them whatever the file mode says. A terminal verdict still
  rests on that artifact on BOTH paths. That residual is #697, open and
  parked; its measured population is zero, and its consequence is bounded --
  the segment lands `blocked`, classifies `human_escalation`, and `--only-segs`
  is the documented retry.

  The rejected pending is still discarded, as it always was -- its bytes are
  defective by the very gate that blocks the segment, and that gate's own
  output is already carried into the terminal joblog via `error_detail`
  (#399). The job reports `reason: pending-rejected`, a label of its own:
  unlike the `validate_attempt()` site, which kept `validate-failed` because
  consumers already read it, this path had no label at all -- it reported
  whatever the FRESH job then produced, which is what made the repeat
  invisible.

  **Best effort.** A failed write never changes the job's exit code, stdout
  line or `reason`; it leaves the segment in its pre-#398 recoverable state.
  The outcome is reported in the terminal joblog's `ledger_write` field,
  which is itself best-effort observability, not a guarantee.

  **Residual, disclosed rather than fixed:** `ledger_update.py` is an
  unconditional full replace with no fragment lock, while the per-segment
  flock lives inside `codex_job.py`. Two OVERLAPPING invocations over one
  `durable_root` that both preselected this segment can therefore publish a
  later `in_progress` over this `blocked` fragment. Running two dispatchers
  against one durable root is already unsupported (see SKILL.md), and
  nothing guards it in either direction.

## Derivation-state gate — the four "flag-only, needs regeneration" fields

`particle_config_hash`, `source_extraction_hash`, `source_input_hash`,
`derivation_bundle_hash` only **flag** staleness relative to a
config/extraction/source-file/derivation-script change — none of them,
alone, proves the downstream artifacts (`canon.json`/`segpack_{seg}.json`)
actually regenerated. This is closed mechanically: `manifest.json` records
`generation_hashes.source_extraction_hash`/`.source_input_hash` (stamped at
W2 by the producing extractor — `extract.py.template` for `gutenberg_epub`/
`plain_text`, the co-designed custom extractor for `custom`); `canon.json`
records
`generation_hashes.particle_config_hash`/`.derivation_bundle_hash` (stamped
at W3 by the glossary-pass merge step — never `manifest.json`, a
deliberate single-owner split); `segpack_{seg}.json` records all four,
copied directly from whatever `manifest.json`/`canon.json` currently
contain at segpack-generation time (never independently recomputed —
transitively correct proof of the whole upstream chain).

`select_segments.py`, for any `converged` segment whose current cache-key
mismatch is caused specifically by one of these four fields: reads that
segment's own segpack's `generation_hashes` and compares against current
values. **Segpack's recorded hash already matches current** (regeneration
already happened) → classify `stale` normally, safe to re-dispatch. **Does
not match** (regeneration hasn't happened) → classify
**`blocked_needs_regeneration`** — excluded from `SEGS` like
`human_escalation`, with an actionable message naming which regeneration
step is missing (re-run W2 for `source_extraction_hash`/`source_input_hash`;
re-run W3/W3a for `particle_config_hash`; re-run W3/W3a for
`derivation_bundle_hash` — `derivation_bundle_hash` covers BOTH
`bootstrap_names.py` and `segpack.py`'s own script bytes (`cache_key.py`'s
`DERIVATION_BUNDLE_MEMBERS`), so the hint names `bootstrap_names.py` first:
re-run it to regenerate `name_candidates.json`, then the glossary pass at W3
consumes those candidates and re-stamps `canon.json`'s
`derivation_bundle_hash`, then `segpack.py` at W3a copies it forward.
Skipping straight to the glossary pass when only `bootstrap_names.py`'s
bytes changed would consume stale `name_candidates.json` rows and still
re-stamp the hash, silently papering over the staleness; segpack.py alone
never recomputes it at all). This is a classification label only (computed
by
`select_segments.py`), never written to the ledger fragment's own `status`
— the underlying fragment stays `converged` throughout. No `--only-segs`
override is needed to escape it — it's self-clearing once the operator
actually reruns the regeneration step (segpack naturally re-stamps current
hashes, and the segment reclassifies to ordinary `stale` on the very next
invocation).

**The zero-candidate case (1.15.0, #193/#291).** "Reruns the regeneration
step" used to be unreachable for one real project shape: a MATURE project
whose canon is frozen with zero unresolved candidates skips the glossary
pass entirely (`glossary_batch_plan.py` emits `no_new_candidates`), and the
glossary merge is what re-stamps `canon.json` — so the mismatch had no way
to advance and the block never cleared. This applies to **both** W3/W3a
fields, since the hole is a property of the remedy rather than of which
field flipped: `derivation_bundle_hash` (a plugin upgrade touching
`bootstrap_names.py` or `segpack.py`) and `particle_config_hash` (an edit to
the resolved particle config file) dead-ended identically. Since 1.15.0 the
sanctioned escape is an explicit restamp, which re-records BOTH fields:

```
python3 ${durable_root}/scripts/canon_validate.py \
  --research-mode <profile's glossary.research_mode> --restamp-derivation \
  --plugin-root {{PLUGIN_ROOT}}
```

then re-run `segpack.py` (in that order — segpack copies `canon.json`'s
stamp forward, so running it first only re-copies the stale value). The
`blocked_needs_regeneration` hint names this directly. Note the ordinary
merge path deliberately no longer re-stamps when it changed nothing (#291),
so a content-free glossary merge is NOT a substitute for this command.

For context, `select_segments.py`'s full classification set (see also
`SKILL.md` W5) is: `reusable` (converged, every cache-key field matches,
draft sha1 still matches `reviewed_draft_sha1` — skip), `stale` (converged
but a cache-key field mismatches or the draft sha1 no longer matches —
needs a fresh pass, though a `stale` whose `.ever_converged` sentinel is
not absent, whose draft still matches `reviewed_draft_sha1` and whose every
moved field is machinery-only ships without one (see the bundle-hash section
above); records which trigger fired in a `stale_reason`
sub-field: `cache_key_mismatch` and/or `draft_sha1_mismatch`; a
`draft_sha1_mismatch`-triggered stale is never reclassified as
`blocked_needs_regeneration`, because that gate is only for the four
derivation-state cache-key fields),
`blocked_needs_regeneration` (see above), `recoverable` (`in_progress` —
treated like `not_started` for dispatch, counted separately),
`not_started` (no fragment at all), and `human_escalation` (`blocked` or
`non_converged` — excluded from automatic re-dispatch by default). `SEGS =
not_started ∪ recoverable ∪ stale`, excluding `reusable`,
`human_escalation`, and `blocked_needs_regeneration`. This same list
becomes `mergeLedgerPrompt`'s `--expected-segs` — no drift between the
dispatch decision and the completeness check.

`select_segments.py --only-segs <comma-list>` intersects emitted `SEGS`
with the named IDs for operator-paced batches, and is also the sole explicit
override for retrying a `human_escalation` segment. It fatally rejects any
ID absent from `manifest.json`'s `segments[]`, fatally rejects an empty
emitted `SEGS` unless `--allow-empty` is also passed, and logs requested
IDs beside actually-emitted IDs.

## Recovery rules for a resumed/interrupted run

- **`in_progress` found at resume** → `recoverable` category, included in
  `SEGS` like `not_started`. **Known, accepted gap: no skip-translate
  optimization exists** — `select_segments.py` does not check
  `draft_ready.py`/`validate_draft.py` and route straight to review for an
  already-complete draft; `pipeline()` unconditionally dispatches translate
  for every segment in `SEGS`. This is a deliberate v1 call, not an
  oversight: an `in_progress` fragment never stores a `cache_key`, so
  there's no baseline to detect a style-bible/canon edit made between crash
  and resume — the "wasteful" redundant translate call is precisely what
  naturally re-applies any such edit (`translatePrompt` reads
  `style_bible.md`/`canon.json` fresh on every dispatch). v1's honest
  scope is "resumable via the ledger, with a redundant but safe
  re-translation for any interrupted-but-already-drafted segment" — not
  zero-waste resumption. A real fix would need a stored, comparable
  baseline for in-flight work — deferred to v2+.
- **Delivered-but-unreviewed draft** — the same `recoverable` case as
  above, subject to the same gap (does not skip straight to review).
- **Timeout / null review** — handled inline by call sites 1/2 above.
- **Post-cap failure** — call site 4 writes `non_converged`, end of
  automated handling. Resuming does not retry automatically; it's a
  human-escalation item. The one explicit path back into automated
  dispatch, for either `non_converged` or `blocked`, is `select_segments.py
  --only-segs <id>` naming the resolved segment — an explicit, auditable
  override, logged as such, regardless of its `human_escalation`
  classification.

## A never-converged unit stranded by a killed driver fits no claim profile

A driver killed mid-flight never writes the terminal record for the segments it
was holding, so they stay `in_progress` — dispatch-eligible by default, which is
the part that hides them. For a unit that HAS converged before, that is P3's
population and `--from-stalled` is its route. For a unit that never converged, all
three profiles refuse it, each on a different fact: `--from-cap` and
`--from-converged` on the materialized status, and `--from-stalled` on the
`.ever_converged.<seg>` sentinel it requires and this unit has never had.

The way through is the absence of a guard rather than a route built for it: with
no sentinel the unit never trips Step 1's previously-converged refusal, so
`select_segments.py --only-segs <seg>` with NO claim flag at all dispatches it.
That works precisely because it never converged — luck, not design — and it is
worth knowing before an operator reaches for `--allow-retranslate-converged`,
which authorizes a re-translation this unit does not need.

**The dangerous part is the round it blocks on the way there.** D3b requires that
every emitted seg be a subset of the claimed ids on a `--from-stalled`
invocation, so one stranded, unclaimable id refuses the whole round until it is
claimed or excluded. Exclusion is the obvious move, and it is where the unit
disappears: the round then runs, converges, and its exit summary lists what it
dispatched — the excluded unit appears nowhere, so the summary reads as complete.
**Write the exclusion into an open-items record BEFORE the summary exists, not
after.** After any driver death, classify first and then read the ledger record of
every `recoverable`/`in_progress` id — the category label alone reports none of
the three independent facts (status, sentinel, `reviewed_draft_sha1`) that decide
which route the unit needs.

## Related tests

`tests/ledger_update.test.py` (fragment-replace transitions — a
`non_converged`→`in_progress` transition asserts no `reason`/`rounds`
survive; a `converged`→`in_progress` transition asserts no
`rounds`/`cache_key`/`n_blocks`/etc. survive; an object-shaped `rounds`
payload is rejected; a payload-intent mismatch is caught; 1.2.0 adds the
converged-write token/sha commit-gate cases — draft-token mismatch,
review-token mismatch, and sha mismatch each independently refuse the
write), `tests/ledger_merge.test.py` (1.2.0 adds the batch-final
per-segment token/sha re-check cases — a straggler old-token pair restored
after an individual segment's own convergence write still fails
`batchComplete`), `tests/ledger_composite_key.test.py` (one case per of the
15 hash fields, plus the two asymmetric `used_terms_hash` cases),
`tests/draft_path_convention.test.py` (repointed from the removed
`reviewPrompt`/`verifyReviewArtifactPrompt` builders to
`reviewDispatchPrompt`/`reviewWaitPrompt`/`readReviewPrompt`/`verifyReviewArtifactPrompt`,
`fixPrompt` unchanged), `tests/select_segments.test.py`
(`--only-segs`/`--allow-empty` cases), `tests/ledger_confirmation_schema.test.py`
(1.2.0, new — the flat `LEDGER_WRITE_SCHEMA`/`LEDGER_MERGE_SCHEMA` accept-side
driven via real `ledger_update.py`/`ledger_merge.py` subprocess calls,
reject-side crossover/missing/unknown fixtures against both the on-disk
strong schema and the JS-guard field sets), and
`tests/ledger_e2e_acceptance.test.py` (the mandatory 7-step mocked-batch
fixture described above) together cover this subsystem. Per the plugin's
own release gate, the plugin is not ship-ready until
`tests/ledger_e2e_acceptance.test.py` **and** a genuine pilot run against a
second real book have both actually run and passed against real data —
CI-green on synthetic fixtures alone is not sufficient. The 1.2.0
resume-integrity regression cases (a metadata-only candidate change, a
changed segment `cache_key`, a changed `.plugin_bundle_hash`/
`.orchestration_bundle_hash`, a schema-only edit, a legacy tokenless
`review.json`, and a straggler-token draft/review pair restored at each of
the five commit-gate points above) live alongside
`resume_setup.py`'s own test file — see
`references/orchestration-and-batching.md` for the digest definition they
exercise.
