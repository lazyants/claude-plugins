# Canon and glossary

The canon (`canon.json`) is a **frozen, hash-versioned, cross-segment** name/realia
glossary. It is never re-decided per segment, and it is never decided by Claude
directly — every accuracy decision it records goes through a codex agent, exactly
like translation and review.

## Why a separate, frozen artifact

A book's proper names, titles, and realia terms (place names, honorifics,
institutions) must translate identically everywhere they appear, independent of
which segment happens to be in context at the moment. Re-deciding "how do we render
this name" inside each segment's own translation pass would silently drift across
75+ segments translated in different runs, by different agent invocations, weeks
apart. `canon.json` exists so that decision is made once, validated, frozen, and
then injected into every segment that needs it — never re-litigated.

**That freeze is book-local, and only book-local.** `canon.json` lives in one
book's `durable_root` and every shipped reader resolves exactly one of them —
`final_audit.py`'s cross-segment `warn_glossary_diff` reads this root's
`canon.json` and the converged segments beside it, so a name rendered one way in
volume 1 and another way in volume 2 is invisible to it. **The previous volume's
`canon.json` is not an input to the next one**: `SKILL.md`'s R10 lists it under
*Never copied*, because it is book-shaped — duplicate spellings that resolved to
one target *in that book*, a `review_queue` left unfrozen for *that book's*
cast.

What does outlive a book is R10's third legitimate input: a **cross-volume name
or person registry kept in the series' own directory**. That is the sanctioned
home for a name that recurs across volumes, and the thing to consult when the
next volume's glossary pass surfaces it again — re-decided into that volume's
own canon through that volume's own pass, never copied in. The route is
operator-driven end to end: no script seeds W3's candidates from such a
registry, and nothing diffs one volume's canon against another's, so
cross-volume consistency is an adjudication the operator makes, not a check the
pipeline runs.

## Bootstrap sequence

Canon population is not "paste the whole book into context and ask for a glossary"
— that does not scale past a short text. The sequence instead is:

1. **Deterministic candidate extraction** — `bootstrap_names.py` (no LLM,
   frequency-ranked). Its generic core is the tokenizer, run-building algorithm,
   frequency/mid-sentence/multiword scoring, and Unicode-category capitalization
   check. It reads the four required keys — `PARTICLES`, `STOPWORDS`, `ELISION_RE`,
   `has_elision` — plus an optional fifth, `name_inventory`, from
   `${durable_root}/languages/<particle_config's LITERAL value>` — never by
   reconstructing a filename from `source.language.code`, since that would ignore
   a project-local override such as `fr.local.json`. This script only surfaces
   candidates; it never decides a translation. It is source-language-parameterized
   (see `references/language-pair-parameterization.md`) via the profile's
   `source.language.particle_config`, never hardcoded to one language. Its raw,
   unfiltered output is then curated + batched by `scripts/glossary_batch_plan.py`
   (1.3.5) — excluding names already resolved in `canon.json`, applying the
   frequency floor, and force-including flagged elision pairs — before the codex
   pass ever sees it; see the **Citation cache** section below for the exclusion
   contract this enforces (#101).
2. **A codex-glossary-pass**, batched, using the Step-0a-copied
   `${durable_root}/glossary_TASK.md`. Whether an established target-language form
   exists, whether a candidate is a title that needs unpacking, or whether it is
   not actually a proper name at all is an **accuracy** decision — therefore it
   must be codex, never Claude, exactly like translation/review.
3. **Merge** with dedup + collision checks into the canonical `entries{}` map, plus
   a `review_queue` for low-confidence/disputed cases. Routing is driven by each
   batch item's own `disposition` field (`"accepted"` vs `"review_queue"`) — never
   inferred after the fact from `basis`/`confidence`.

   **A form known to be SPLITTING, with none of its senses resolved yet, is a
   `review_queue[]` item too — it needs no third home and no project-local
   sidecar of splitting forms.** The QUEUED shape (`canon-batch.schema.json`'s
   `items.oneOf[1]`) requires only `source_form`, `is_proper_name`,
   `disposition` and `note` — the resolution fields stay optional and absent,
   and `additionalProperties: false` leaves no other slot — so the evidence
   that the form splits goes in the `note`, and nothing has to be resolved to
   record it. `canon_senses.json` is not the place: its `is_split` predicate
   needs >=2 ADJUDICATED senses, which is exactly what this form does not have
   yet. Queueing it is what makes the pipeline leave it alone —
   `glossary_batch_plan.py` excludes every `review_queue[].source_form` from
   every batch, so the form stops being proposed for single-target
   adjudication, and `segpack.py` never reads `review_queue` at all, so no
   fixed target for it ever reaches a translate prompt (it surfaces
   per-segment through `new_names[]` instead — see **`segpack.py`'s canon
   injection contract**). `glossary_batch_plan.py --retry` is the one thing
   that reinstates it, once its senses are worth researching.
4. **Hash stamping.** The merge step records `generation_hashes.particle_config_hash`
   AND `generation_hashes.derivation_bundle_hash` into `canon.json` at the moment of
   merge, via `cache_key.py --field particle_config_hash` / `--field
   derivation_bundle_hash`. This is the mechanism `select_segments.py`'s
   derivation-state gate depends on to know whether a `particle_config` change, or a
   `bootstrap_names.py`/`segpack.py` script fix, has actually been regenerated
   through yet (see `references/ledger-and-resumability.md` for the full
   derivation-state gate).

   **1.15.0 (#291) — a merge that changes nothing does NOT re-stamp.** These two
   hashes are a claim that this canon's CONTENT was produced under that derivation
   state, so a merge only advances them when the merged document actually differs
   from what is already on disk. This is deliberately keyed on the DOCUMENT, not on
   the fragment's item count: `_merge_batch` treats an identical re-submission as a
   silent no-op, so a fully populated fragment of already-merged items changes
   nothing either — and still reports `merged_accepted > 0`. Before 1.15.0 every
   merge re-stamped unconditionally, which let a content-free merge clear
   `blocked_needs_regeneration` without anything having been regenerated (segments
   then read as caught-up and stale output ships). A `review_queue[]`-only change
   DOES count as a change and does re-stamp — that array is schema-required content
   this file's own consumers read back. **Every mode that writes canon.json**
   — `--merge-batches`, legacy `--batch`, `--init` and `--restamp-derivation`
   alike — reports the same `generation_hashes_restamped` boolean, so a
   conserved stamp is visible rather than silent and a caller can ask "did the
   provenance move?" without branching on which mode ran.
   `--restamp-derivation` additionally reports `generation_hashes_changed`,
   naming which of the two fields actually differed.

   The deliberate way to advance provenance on an UNCHANGED canon is
   `canon_validate.py --research-mode <mode> --restamp-derivation --plugin-root
   {{PLUGIN_ROOT}}`. That matters for a mature, zero-candidate project: it has no
   candidates left, so the glossary pass never runs, so no merge exists to re-stamp —
   and after a plugin upgrade that touches `bootstrap_names.py` or `segpack.py`,
   segment selection would otherwise
   stay blocked with no recourse (issue #193, which records the pre-1.15.0
   `--merge-batches <empty-batch.json>` trick as its only, explicitly unsanctioned
   escape). The operation is unchanged; what changed is that it is now explicit,
   named, validated, and reports which fields moved, instead of happening silently
   as a side effect of a command whose stated job was merging fragments.

**Enforced, not just claimed.** The merge step's final action validates the WHOLE
just-written `canon.json` against `canon-file.schema.json`, which requires
`entries{}`, `review_queue`, AND both `generation_hashes` fields present
unconditionally. A merge that skipped either stamp fails loudly at merge time,
rather than leaving a silently-incomplete `canon.json` for `select_segments.py` to
discover has no `generation_hashes` at all, later, with no clear point of failure.

## Glossary-pass call discipline

The glossary-pass gets the **identical dispatch → bounded-wait →
schema-validated-consume discipline review uses** (1.2.0, closing #87/#88/
#90/#97 — see `references/workflow-schema-validation.md` and
`references/orchestration-and-batching.md` for the full mechanics; this
section covers only the glossary-specific parts of that shared pattern) —
canon/realia decisions are exactly as accuracy-load-bearing as review
findings, and codex accuracy-bearing calls in this plugin are never bare,
never nested, and never trusted on their own in-turn say-so.

Per batch: `batchDispatchPrompt(batch, attempt, rejectionReason)` is codex,
`agentType:'codex:codex-rescue'`,
`effort: engine.effort` (#197 — a configurable enum, default `high`, dual-injected
alongside the TASK opener's own `Effort: <value>.` line; see
`references/ledger-and-resumability.md`'s dual-injection rule), **schema-less**, fire-and-forget — it writes the run-scoped
fragment `${durable_root}/glossary/runs/{{RUN_ID}}/out_{index}_attempt_{n}.json`
atomically and self-validates it via `canon_validate.py --check-batch`
before printing `FRAGMENT {index}`; the wait is Claude,
bounded-poll, `READY`/`PENDING` — since **1.16.2** it is spent across
several bounded agent calls rather than one, so the single `batchWaitPrompt()`
became `batchWaitChunkPrompt(batch, attempt, chunkIndex)` plus one
`batchWaitRecheckPrompt(batch, attempt)`, and `TIMEOUT` is no longer a sentinel any
agent returns (see **The chunked wait** below). Under `research_mode: live` a bounded
citation-review stage then gates whether that batch counts as ready at all,
still inside `batchStep` — see **Pre-merge citation review** below for the
stage and for why a citation gets exactly one chance, here, before the
merge. Two final calls run once, after every
fragment is `READY`, never per-batch: a merge call
(`canon_validate.py --merge-batches`, no schema — the single serialized
writer) and a disk-verify call (`canon_validate.py --verify-merged`,
`schema: CANON_VERIFY_SCHEMA`, flat, new). The pre-1.2.0 shape — a single
schema-validated `agent(glossaryPrompt(batch), {agentType:'codex:codex-rescue',
schema: CANON_BATCH_SCHEMA})` call per batch, banking its return directly —
is gone: `CANON_BATCH_SCHEMA` was a top-level `array`, which the tool-use
API's `agent()` schema param can never accept (#87 — see
`references/workflow-schema-validation.md`), and banking an un-verified
codex return risked a false-green merge (#88).

This Workflow template is **new plugin hardening, not itself source-proven**. The
real reference project ran its glossary pass as ad hoc `glossary/TASK.md` plus
codex batches producing `glossary/out_*.json`, not as a schema-validated Workflow
script. The first real plugin project should pilot this template on one small
batch and manually verify the `canon.json` merge output before treating it as
fully load-bearing.

### The chunked wait (**1.16.2**, #352)

The wait is a **budget spent across several bounded agent calls**, never one
long call. Until 1.16.2 this template polled its whole 900 s budget inside a
single `agent()` call running `for i in $(seq 1 45); do … sleep 20; done` —
against a **measured** hard clamp of 600 000 ms on any one Bash call, which the
agent cannot raise by asking for a longer timeout. The 900 s poll was therefore
killed at 600 s, and the kill was reported as a timeout while a perfectly valid
fragment could be sitting unread on disk. This is the same defect
`mass-translate-wf.template.js` fixed for its own waits in 1.16.1 (#348); 1.16.2
ports that fix here and to the skeptic pass, so all three templates now share one
shape. The budget is unchanged at 900 s — what changed is how it is spent.

Concretely: chunks of `WAIT_CHUNK_SEC = 480` s, with chunk *i* polling whatever
is LEFT of the budget rather than a flat 480 s, so the chunk bounds **sum to the
declared budget exactly** — for 900 s that is two chunks of 480 s and 420 s.
Flat chunks would silently EXTEND the budget instead of spending it, falsifying
every doc that quotes the 900 s figure. Each chunk asks for a tool timeout of
540 s, comfortably under the 600 s clamp — but that bounds the CALL, not the
poll. The loop tests its own deadline only BETWEEN iterations, after that
iteration's validation command has already run, so a validation begun just
before the chunk's own elapsed bound can run on past it, up to the tool's own
540 s ceiling, before the marker is ever printed: two nominal chunks of 480 s
and 420 s can therefore consume up to 540 s and 540 s of wall clock apiece, not
480 s and 420 s. What IS guaranteed, and is the property #352 exists to hold:
no single call can exceed its declared 540 s timeout, safely under the
measured 600 s clamp, and the wait always terminates, in at most
`WAIT_CHUNKS + 1` calls. The declared 900 s is the polling budget those calls
divide up, not a wall-clock deadline the wait is certain to land inside.

**The sentinel pair is `READY`/`PENDING` — exactly two tokens — and `TIMEOUT`
is no longer a sentinel at all.** A chunk returns `READY {index}` the instant
the fragment validates, which ends the wait immediately; anything else — the
chunk spending its bound, an ambiguous reply, a tool error — resolves to
`PENDING {index}` and the next chunk runs. Resolving ambiguity DOWN to
`PENDING` is deliberate and fail-safe: at worst it costs one more chunk of
waiting, bounded by the chunk count, whereas resolving it up to `READY` would
hand the merge unvalidated bytes.

Two tokens, not three: W5's chunked wait has a third, `FAILED`, which is the
detached `codex_job.py` driver's own fail sentinel. **These waits have no
equivalent and must not pretend to** — they poll a fragment on disk written by
an agent, with no external driver to report its own failure.

`PENDING` rather than `NOTREADY` is also deliberate: guarding the `READY`
direction by containment stays available under that spelling — and since
**1.16.2** that option is TAKEN, not merely kept open, in every template — and
it avoids a reader-facing trap in which two of the vocabulary's tokens differ
only by a prefix.

**Read the verdict in the right direction.** The parse is asymmetric on
purpose: `PENDING {index}` is tested FIRST and by CONTAINMENT (a hit anywhere
in the reply), and only then is `READY {index}` accepted, by whole-line
equality. That is false-RED-only by construction — a stray mention of
`PENDING` biases away from `READY`, and `READY` can never be manufactured by
a reply that merely discusses it. A description that says "the reply is
checked for `READY`" has the direction backwards.

**All three templates now share that ordering**, which they did not before
**1.16.2**: the skeptic pass read its wait through a bare whole-line
`sentinelVerdict()` with no containment guard, so a reply of
`PENDING 0 (not READY)` followed by a clean `READY 0` line resolved to
`ready` there while resolving to `pending` in the glossary — the skeptic was
the permissive side of a false-GREEN boundary. Porting `rejectedAnywhere()`
into it closed that, and the two now return identical verdicts on that reply
and on every other case in the guard's own test set. The stake rose with the
chunking rather than staying flat: before 1.16.2 one reply per batch was read
this way, and a wait now reads up to `WAIT_CALLS` of them, so a permissive
parse would get three chances at the same batch instead of one.

The re-check runs on exactly ONE condition: **the chunk loop ended with a
verdict that is not `ready`** — the budget was spent, or a reply was ambiguous,
null or tool-killed and so resolved to `PENDING`. There is no early fail
sentinel to trigger it, because these waits have no `FAILED` verdict to raise.
When that condition holds, exactly
ONE authoritative, **non-polling** re-check runs before anything is declared a
timeout, because the fragment may have landed after the last chunk's poll ended.
It runs the same accept command once, returns immediately, and answers with the
same `READY`/`PENDING` grammar, parsed by the same verdict function as the
chunks; sharing the parse site is what stops the re-check from drifting into a
weaker gate than the poll it backs up. **A timeout is thus a conclusion the
orchestrator draws after the re-check also says `PENDING` — not a word any agent
returns.** An exhausted chunk loop is not, on its own, a timeout.

Keep the WIRE GRAMMAR and the OUTCOME separate when reading or writing about
this — the pre-1.16.2 docs conflated them, which is most of why the sentinel
change touched so many sentences. The grammar is `READY`/`PENDING`. The
outcome has a different name at every call site, and **on this path there is
no timeout outcome either**: a batch whose re-check still says `PENDING` is
simply never ready, and the pass ends with
`reason: "fragment-check-failed"` — distinct from
`citation-review-exhausted`, which means the fragments WERE valid but their
citations did not survive review. That is a real asymmetry with W5, which
does name a timeout outcome (`reason:"review-timeout"` / `"translate-timeout"`,
load-bearing because `select_segments.py` keys its recoverable
reclassification off those strings). Do not import W5's vocabulary here.

The cost consequence is the one operators feel, and it is a **CEILING, not a
runtime cost**: a wait now costs anywhere from 1 to `chunks + 1` — **1 to 3**
here — because a `READY` in any chunk leaves the loop on the spot AND
suppresses the re-check, which is gated on the verdict rather than on the loop
index. A fragment that validates in chunk 1 spends exactly 1 call; only a wait
that exhausts every chunk and still needs the re-check spends all 3. The
estimator computes the worst case, which is what a preflight gate should do —
but do not read `19N + 2` or `5N + 2` as what a run will actually spend. In the
skeptic pass the early exit is an **economy** requirement rather than a
correctness one: each extra chunk would spend another agent call re-running
`--validate-fragment` over a fragment that has already validated. That gate is
write-capable but idempotent as of #368, so the extra call is wasted, not
destructive. See
`references/orchestration-and-batching.md`'s **Preflight cost
cap** for the arithmetic, stated once there.

## Schema shapes

Three distinct JSON Schema files govern the canon data contract — do not conflate
them. Every shipped schema, including these three, declares:

```
"$schema": "https://json-schema.org/draft/2020-12/schema"
```

### `canon-entry.schema.json` — one resolved, ACCEPTED entry

```
{ source_form, is_proper_name, canonical_target_form, basis, source, confidence, note }
```

`basis ∈ {established, transliterated, title, not_a_name, sense_translated}`.
`confidence` is also schema-constrained as an enum, not free text.

- **`established`** requires a cited reference. This is schema-enforced, not just
  prose convention: `if basis == "established", then source` is REQUIRED and must
  be `{type: "string", format: "uri", minLength: 1}`. A glossary-pass batch entry
  claiming `basis: "established"` with an empty or non-URI `source` fails schema
  validation outright.
- **`transliterated`** applies a single fixed source→target practical-transcription
  rule for the whole book. That rule is documented in THIS project's own style
  bible (`style_bible.md`, section C-translit) — it is language-pair-specific data,
  never plugin code (see `references/language-pair-parameterization.md`).
- **`sense_translated`** (1.4.0) is a proper name whose correct rendering is a
  deliberate *sense*-translation rather than a citable form or a mechanical
  transliteration — a genuine speaking name (`style_bible.md` section C). Also
  schema-enforced: `note` and `is_proper_name` are REQUIRED, `is_proper_name`
  must be `true` (excluding it from adjudication would let a common-noun
  candidate freeze and be delivered by the basis-blind `segpack.py` while
  falling outside every adjudication category), `canonical_target_form` and
  `note` must contain non-whitespace content (`"pattern": "\\S"`, not merely
  `minLength:1`), and `source` is FORBIDDEN (`false`) — a project-specific
  editorial rendering has no citable reference to record. **Legal under
  `research_mode: offline`**, exactly like `transliterated`: no external
  citation is ever claimed. **Precedence:** `established` wins whenever a
  citable conventional target form genuinely exists (cite it via `source`);
  `sense_translated` is reserved for a rendering that makes no established-form
  claim at all. Frozen the same way every other basis is — emitted directly
  with `disposition:"accepted"`, no separate human sign-off (the glossary
  agent's judgment, adjudication dedup, and `review_queue` for genuinely
  disputed names are this basis's quality controls, same as every other).

Note the field-name generalization from the source project: the proven
`historiettes-t3` reference used French/Russian-specific field names (`fr`,
`canonical_ru`); the plugin generalizes these to `source_form` and
`canonical_target_form` so the same schema works for any language pair.

**A recurring COMMON-NOUN term of art is out of canon scope, by design — and it
has a home.** An office title or institutional realia that must render one way
for the whole book (`président` of an Ancien-Régime sovereign court) is not a
name, and nothing here can freeze it: the candidate extractor surfaces
capitalized forms, the `sense_translated` constraint above deliberately walls
common nouns out, and the shipped adjudication contract gives an
`is_proper_name:false` entry `disposition:"review_queue"`, never `"accepted"`.
That is the correct boundary, not a gap to close in canon — widening it would
re-open exactly the leak the `is_proper_name: const true` rule closes, a
common-noun candidate frozen and delivered by the basis-blind `segpack.py`.
Such a term is PINNED in `style_bible.md` section C's title/honorific mapping,
which is delivered in full to every translate and review job. Its
machine-checkable twin is `profile.yml`'s `validation.terms` (#199), an opt-in
list of bare `source_form`/`target_form` pairs that `final_audit.py`'s WARN 6
counts carrier by carrier at W7 — each block, each footnote definition and each
delivered verse field on its own. Before that existed, drift of such a term was
invisible to every gate — `final_audit.py`'s cross-segment glossary-diff keys on
`canon.entries` and each draft's own `names[]`, both proper-name channels — and
a single volume shipped one court office under two target words.

### `canon-batch.schema.json` — one fragment's real content contract

```
{ type: "array", items: { oneOf: [ACCEPTED, QUEUED] } }
```

**Never an agent-facing `schema:` param as of 1.2.0** (that was the pre-1.2.0
`CANON_BATCH_SCHEMA`, deleted for `#87` — a top-level `array` can't be an
`agent()` schema at all; see `references/workflow-schema-validation.md`).
This shape now governs exactly one thing: what
`canon_validate.py --check-batch <fragment>` validates a codex-written
fragment file against, on disk, after the fact — never what an `agent()`
call is asked to return.

Every item REQUIRES `source_form`, `is_proper_name`, and `disposition:
"accepted" | "review_queue"`:

- `disposition: "accepted"` → `then required: [canonical_target_form, basis,
  confidence]` (the full `canon-entry.schema.json` shape; `basis`'s own
  `established` → URI conditional still applies).
- `disposition: "review_queue"` → `then required: [note]` — a queued/disputed
  candidate is not yet resolved, so `canonical_target_form`/`basis`/`source`/
  `confidence` are all optional/absent, but `note` is mandatory and must explain
  why it is queued (e.g. the `SOURCE_UNAVAILABLE:` prefix below, or a
  dispute-reason).

### `canon-file.schema.json` — the WHOLE `canon.json` file

```
{
  entries: { type: "object", additionalProperties: <canon-entry.schema.json shape> },
  review_queue: { type: "array", items: <QUEUED shape from canon-batch.schema.json> },
  generation_hashes: {
    type: "object",
    required: ["particle_config_hash", "derivation_bundle_hash"],
    properties: {
      particle_config_hash: { type: "string" },
      derivation_bundle_hash: { type: "string" }
    }
  }
}
```

`entries{}`, `review_queue`, AND `generation_hashes` are ALL THREE required
unconditionally at the top level.

## `canon_validate.py`'s CLI modes

`scripts/canon_validate.py` is the plugin-owned backstop for schema enforcement —
never trust the Workflow-level `agent(...)` call's own say-so alone; the
DISPATCH → WAIT → CONSUME pattern's whole point is that codex's own output
is not trusted until an independent, deterministic script re-checks it (see
`references/workflow-schema-validation.md`). **`--research-mode
live|offline` is required on every mode**, never defaulted, even in a mode
where it has no effect — so no call site can accidentally omit declaring
the precondition. `--canon-path PATH` (every mode, optional) overrides the
default `${durable_root}/canon.json` location. Every mode prints exactly one
JSON line to stdout and exits 0 on success / 1 on failure — callers should
read stdout, not rely on the exit code alone.

**#412 — a second requirement, on the STAMPING modes only.** The four modes
that write `canon.json`'s `generation_hashes` — `--init`,
`--restamp-derivation`, `--merge-batches` and the legacy bare `--batch`
merge — additionally refuse to run, with an argparse error (exit `2`),
unless given either `--plugin-root PATH` or the explicit escape hatch
`--allow-durable-sibling`; passing both is itself an error. Stamping shells
out to a sibling `cache_key.py`, and left to self-anchor that sibling comes
out of `${durable_root}/scripts/`, which the codex processes this pipeline
launches hold `--write` over — so a silently self-anchored lookup could
stamp through a tampered copy and forge the very hashes that later gate
canon reuse. `--plugin-root` names the plugin's own install tree and
resolves the sibling as `{PATH}/assets/scripts/cache_key.py`;
`--allow-durable-sibling` accepts the durable sibling knowingly, for a
hand-run recovery with no orchestrating session to supply a plugin root.
The three NON-stamping modes below — `--check-batch`, `--verify-merged` and
validate-only — resolve no sibling and accept neither flag's obligation; do
not add either to them.

1.2.0 adds three new modes to close #87 (schema-less glossary dispatch,
`references/orchestration-and-batching.md`), #90 (concurrent-batch races),
and #88 (unverified merge) — routed by `main()` on which flag is given,
alongside the original `--batch PATH` merge path (kept working unchanged;
existing tests exercise it directly):

### `--check-batch PATH [--expect-source-forms-file M.json]` — one fragment, no write

The self-check invocation issued character-identically by
`batchPrecheckPrompt`, `batchDispatchPrompt`, `batchWaitChunkPrompt` and —
since **1.16.2** — `batchWaitRecheckPrompt` (see
`references/orchestration-and-batching.md`). Pass-1 per-item validation plus
the offline backstop on the ONE fragment at `PATH` — never touches
`canon.json`, never writes anything. When `--expect-source-forms-file` is
given (a JSON array of candidate names, read from a **file**, never argv —
so a multiword/apostrophe name is never a shell-quoting hazard), asserts the
fragment's item `source_form`s **exactly** equal the manifest set: no
missing, no extra. stdout: `{"success":true,"mode":"check_batch",
"source_forms":N}` or `{"success":false,"error":"...","offending":[...]}`.

### `--merge-batches P1 P2 …` — the single serialized writer (closes #90)

One process, run once per glossary pass, never per-batch. Loads `canon.json`
once; validates **all** given fragments (Pass 1 + the offline backstop)
**first**, before merging any of them; threads
`acc = _merge_batch(acc, frag)` across the fragments **in the given
argument order**; stamps `generation_hashes`; runs the whole-file Pass 2 on
the **in-memory** accumulator **before** the atomic write (catching a
corrupt merge before it ever touches disk, not after); one atomic write;
re-reads the file post-write and re-validates it, **without** re-injecting
`generation_hashes` defaults (an earlier revision's
`on_disk.setdefault("generation_hashes", …)` masked a dropped-hash
corruption from this very re-read — removed). `_merge_batch` itself gained
a guard on its review-queue-append branch (`if source_form in entries:
continue`) so an item already accepted under one fragment doesn't also land
in `review_queue` from a later one. stdout:
`{"success":true,"mode":"merge_batches","entries_count":N,
"review_queue_count":N,...}` or a failure line naming the offending
fragment/item.

Concurrency is closed by **being** this one process, not by locking: every
batch writes to its own run-scoped fragment path (never `canon.json`
directly), and exactly one `--merge-batches` call — after every fragment is
confirmed `READY` — is the sole writer of `canon.json` for this glossary
pass. The docstring states this precisely as single-writer-by-operational-
precondition, not a locking guarantee.

**A trap this pass discovers rides its own `note`, and is collected the moment
the pass returns.** A word-sense or realia discovery — a title or place name
whose period sense differs from its modern one — is recorded by the glossary
agent in that candidate's own `note`, in the run-scoped fragment, and the merge
carries an accepted item's note into `canon.json` along with the entry.
`glossary_TASK.template.md` forbids the agent every other write, and
`style_bible.md` above all: its E-traps section sits inside the style_contract
span, so an agent append there would be an unreviewed edit to the authority
every translate, review and fix turn reads, and would move
`style_contract_hash` — flipping every already-converged segment to `stale`
(#510).

What `canon.json` gives such a note is durability and a READER, but never the
reader who would act on it. An accepted entry's note IS published verbatim —
`render_obsidian.py` prints it in that entity's page, in the YAML frontmatter
and again in the body — which is why the task template tells the agent to keep
a note publication-safe. What a note does not do is travel in a prompt:
`segpack.py` builds `canon_names` from `entries{}` keys and `canon_map` from
their non-empty `canonical_target_form` values, and carries no `note` field at
all, so a translate or review turn is never shown one through its read list.
Two turns can still reach one, both deliberately: a FIX turn is told to settle
an unresolvable canon claim against `canon.json` itself
(`mass-translate-wf.template.js`'s fix prompt), so it may read a note in
passing though nothing asks it to act on one; and the opt-in W9r registry prep
projects `review_queue` notes into its own model input
(`person_registry.py --prep`). A queued note reaches nothing else —
`glossary_batch_plan.py`'s selection excludes a queued `source_form` from every
later pass unless `--retry` names it.

So the moment this workflow returns `merged: true` — after its own
`--verify-merged` call, which is the first point the operator or the
orchestrating turn has control again — copy every such note into
`consistency_issues.md`, one line each, before the next batch starts. Only the
promotion into `style_bible.md`'s E-traps waits for a batch boundary, where
`SKILL.md`'s R9 prices it; that split is the ordering `style_bible.template.md`
already ships under E-traps.

### `--verify-merged --batch f1 --batch f2 … [--expect-source-forms-file M.json]` — disk-independent re-check (closes #88)

The glossary disk-verify call's own invocation (`schema: CANON_VERIFY_SCHEMA`
in the Workflow — see `references/workflow-schema-validation.md`). Reads
`canon.json` and every named fragment **fresh from disk** — no dependency on
what `--merge-batches` believes it just wrote. Per fragment item, checked
**by disposition**: `accepted` → `canon["entries"][sf] ==
_entry_from_accepted_item(item)` (exact equality, not "a key exists");
`review_queue` → the exact queued object is present in
`canon["review_queue"]` **OR** its `source_form` is already a key in
`canon["entries"]` (accept-supersedes — an item queued in one fragment and
independently accepted by a later one is correct, not a missing-item false
positive). When `--expect-source-forms-file` is given, also asserts exact
manifest coverage. stdout: `{"verified":true}` or `{"verified":false,
"missing":["sf1",...]}` — matching `CANON_VERIFY_SCHEMA`'s relay contract
exactly. `merged: true` in the Workflow's own return is gated on both this
script reporting `verified:true` with an empty `missing[]` **and** the
JS-side exact-key-set guard confirming it (see
`references/ledger-and-resumability.md` for the guard-field-set discipline
applied identically to the ledger literals).

### `--batch PATH` — the original single-fragment merge path (kept)

Unchanged from pre-1.2.0: merges one glossary-pass batch result into
`canon.json` in a single call, running Pass 1 + the offline backstop + the
dedup/collision merge + `generation_hashes` stamping + the atomic write +
Pass 2. Existing tests exercise this path directly; it is not deprecated,
just no longer how the Workflow template itself drives a real multi-batch
glossary pass (that's `--merge-batches` now).

### `--batch` omitted entirely — VALIDATE-ONLY mode (kept)

A read-only health check against the CURRENT, already-frozen `canon.json`:
no merge, no write, and no offline `basis:"established"` backstop (that
backstop only ever applies to NEW entries in an incoming batch; an
already-frozen `canon.json` is not retroactively re-litigated just because
this run happens to pass `--research-mode offline` for other reasons).
Pass 1 instead validates every EXISTING `entries{}` value against
`canon-entry.schema.json` directly, and every existing `review_queue[]`
item against the QUEUED shape; Pass 2 is unchanged — the loaded document is
validated against `canon-file.schema.json`.

### Shared machinery across every mode

- **Dependency preflight first**: wraps `import jsonschema` in a try/except,
  printing a clear "install with `pip install -r requirements.txt`"
  message and exiting non-zero on `ImportError` — never a raw traceback.
- **Pass 1 — per-item**, whichever mode is active. Constructs a validator
  over `canon-batch.schema.json`'s item shape with
  `jsonschema.Draft202012Validator(..., format_checker=jsonschema.FormatChecker())`
  explicitly (`format_checker` is REQUIRED — `jsonschema`'s own convenience
  `validate()` does not enable format assertions by default).
- **Pass 2 — whole-file.** Fatally halts, naming the specific problem, if
  `entries{}` / `review_queue` / `generation_hashes.particle_config_hash`
  / `generation_hashes.derivation_bundle_hash` are missing or malformed — a
  genuinely incomplete `canon.json` (e.g. one missing `entries` or
  `review_queue` entirely) must fail loudly here, never be silently patched
  up with empty defaults before this check runs. This is the check that
  actually enforces the two `generation_hashes` fields' presence, which
  `select_segments.py`'s derivation-state gate is entirely load-bearing on.
- Reads `canon-entry.schema.json` / `canon-batch.schema.json` /
  `canon-file.schema.json` from `${durable_root}/schemas/` — never the
  plugin's own `assets/schemas/`.
- The module docstring no longer mentions `CANON_BATCH_SCHEMA` as an
  agent-facing schema anywhere (STATUS/MERGE/Usage sections) — only as the
  on-disk fragment-content shape `--check-batch` validates against.

Every mode's passes are schema-driven validation, not free-text judgment —
this is the same "independent re-check, don't trust the agent's own
self-report" discipline applied everywhere else load-bearing in this plugin
(e.g. the ledger's disk re-read after `recordLedgerPrompt`).

## Research preflight and offline-fallback policy for `basis: "established"`

Claiming `basis: "established"` means a real, cited reference exists for the
target-language form. That claim is only trustworthy if the glossary-pass agent's
environment actually had working web/research access on this run — so the plugin
makes that precondition an explicit, human-declared profile setting rather than
something a script silently probes (and potentially gets wrong).

- **`glossary.research_mode: live | offline`** (`profile.yml`, REQUIRED, no
  default) is the explicit, human-set precondition for whether THIS run's
  glossary-pass agent has real web/research access. The orchestrating Claude
  session declares this — it is never auto-detected, exactly like
  `verse_policy.mode` or `apparatus_policy`.
- **`research_mode: offline` forbids `basis: "established"` outright.** Every
  candidate that would otherwise warrant `established` must instead be assigned
  `basis: "transliterated"` (the existing fixed practical-transcription
  rule, if mechanical transliteration is adequate), `basis: "sense_translated"`
  (1.4.0 — if the candidate is a genuine speaking name and the correct
  rendering is a deliberate sense-translation rather than a citable form; see
  the precedence rule above), or routed into `review_queue` (if the name is
  genuinely disputed and needs a human's real research later) —
  never left with a fabricated citation, and never silently forced into
  `established` anyway. The `transliterated`/`review_queue` outcomes carry the
  literal note prefix `SOURCE_UNAVAILABLE:` — mirroring the `NEW:` note-prefix
  convention used for `new_names[]` below — so a human reviewing
  `canon.json`/`review_queue` later can find every entry that still needs real
  research once it becomes available. `sense_translated` carries no such
  prefix and is unaffected by `research_mode` either way: it never claims a
  citable source in `live` mode any more than in `offline` mode.
- **`scripts/canon_validate.py`'s merge-time backstop FATALLY REJECTS** the whole
  batch merge if ANY entry claims `basis: "established"` while
  `research_mode == offline`, naming the offending entries (bounded to the first 8, with a count of the rest) — the same
  "don't trust the agent's own compliance, independently re-check" discipline
  applied to the URI-format assertion itself.
- **`--research-mode live|offline` is a REQUIRED CLI argument** to
  `canon_validate.py`, never defaulted. The value is `profile.yml`'s
  `glossary.research_mode`, resolved once by the orchestrating Claude session at
  `glossary-pass-wf.template.js` instantiation time and spliced in as the
  `{{RESEARCH_MODE}}` token (same mechanism as `{{DURABLE_ROOT}}`), then passed
  through literally by the merge-step agent's shelled-out invocation — this script
  never parses YAML itself.
- **`{{EFFORT}}` (#197)** is the same substitution mechanism applied to
  `profile.yml`'s `engine.effort`: resolved once at
  `glossary-pass-wf.template.js` instantiation time and spliced in as a
  plain quoted string, it drives BOTH the batch dispatch codex TASK
  opener's own `Effort: <value>.` line and `batchDispatchPrompt`'s
  `agent()` `effort` option, always from this one value (see
  `references/ledger-and-resumability.md`'s dual-injection rule). There is
  no `{{MODEL}}` token here — a codex model id does not thread to the
  glossary pass (see `assets/profile.example.yml`).

### Pre-merge citation review

Everything above constrains the SHAPE of a citation, never its truth.
`canon-entry.schema.json` requires `source` when `basis == "established"`
and asserts `format: "uri"` plus `minLength: 1` on it; `--check-batch` runs
that same per-item shape check plus the offline backstop. No path opens the
URL, and none asks whether the cited reference actually attests the
`canonical_target_form` it was offered for. A fabricated but well-formed URI
cleared every check the pipeline had before this stage.

So the glossary pass reviews each `basis: "established"` citation itself,
**inside `batchStep`, before that batch counts as ready** — and therefore
before any fragment reaches `--merge-batches`.

**Since 1.16.1 (#347) the stage is TWO calls per attempt, not one.** Until
then a single agent both fetched every `source` URL and judged what came
back, which is two defects sharing one call. The SSRF half is closed by
`scripts/fetch_citation.py` — an http/https scheme allowlist, no embedded
credentials, every resolved address checked, the connection pinned to the
address it vetted, every redirect hop re-validated, and caps on time, bytes
and content type. The PROMPT-INJECTION half cannot be closed the same way,
and the first attempt to — telling that same agent to fetch only through the
helper — was rejected in review, correctly: the reviewer holds Bash and
ingests attacker-authorable page text, so a hostile citation page can simply
instruct it to curl something else. A rule the attacker can talk the enforcer
out of is not an enforcement point. So retrieval moved OUT of the judging
agent rather than being fenced inside it. PREPARE runs exactly two commands —
the `--approve-to` snapshot below, then `fetch_citation.py --batch` over that
snapshot — and reads only the one line of locally generated JSON each of them
prints; it never opens the snapshot or an evidence file, so nothing it
ingests was authored outside this project, and an agent that reads no
attacker text cannot be talked out of anything. If the snapshot command fails
it stops there rather than fetching, and no judge call is spent. The JUDGE
reads local files only — the snapshot, the evidence `index.json`, and exactly
the bodies that index names as an `evidence_file` — and needs no network at
all; it is handed no fragment path, not even inside prose forbidding a read
of it, because a prompt-injected judge should have to guess that string
rather than be given it.

**The claim the split supports, and no wider one:** in the citation audit path
retrieval happens only through `fetch_citation.py`, launched by an agent that
never reads the retrieved bytes, and the agent that judges neither performs
retrieval nor holds a tool that could. It does NOT make the pass SSRF-free:
the batch dispatch still does open web research by design under
`research_mode: live`. That one is accepted by design and documented rather
than quietly covered (#353); overclaiming here would be worse than the
original bug, because the next reader would stop looking.

**The judge's capability, not just its instructions (#353).** Until then the
split had removed the judge's REASON to fetch and its INPUT for fetching, and
said so at exactly that width, because it had not removed the CAPABILITY: the
judge could still run a command while reading attacker-authored page bodies.
It is now dispatched as `agentType: "literary-translator:citation-judge"`, a
plugin agent whose frontmatter grants `tools: Read` and nothing else, so the
boundary is the harness's rather than the prompt's. An agentType that cannot be resolved is fail-closed — no
fallback to a full-tool agent, and a batch whose verdict never arrives is not
approved.

**Neither half is codex, and neither carries a schema** — the judge's
`agentType` names the tool-restricted Claude agent above, never a codex
dispatch, and both calls are sentinel-verdict shaped exactly like the precheck
and wait steps (a schema-bearing call can wedge the Workflow if the
forwarder detaches, #97). Codex is what PRODUCED the citation, so a reviewer
running under a different model is a genuinely separate opinion rather than
the same reasoning re-run; `tests/bounded_poll_present.test.py` pins this
template's codex work-call set to exactly `{batchDispatchPrompt}`, which
keeps it that way. This does not loosen R1/R4: the stage AUTHORS nothing and
repairs nothing — its only two powers are approve and reject, every canon
resolution still comes from codex, and a rejection's only effect is to make
codex redo the batch. The two efforts differ deliberately: PREPARE takes the
precheck's and wait's `"low"`, being mechanical — run two commands, relay
which succeeded — while the JUDGE keeps `"high"` as the one judgment call in
the template. Neither is wired to `{{EFFORT}}`, which stays the codex
dual-injection knob and nothing else.

Scope is narrow and explicit: only items whose `basis` is exactly
`established` are examined — every other basis makes no external source
claim at all — and for each one the judge decides from that item's retrieved
body alone, never from the URL's shape, its domain's reputation, or its own
memory of what lives at that address. Three checks: it RESOLVES (the index
records that item's outcome as `fetched` and the body is the reference page
itself — not a 404, a parked domain, a content-hiding login wall, or plainly
a different page than the URL promised; an outcome of `refused:<reason>` or
`http_error:<code>` FAILS this check, because nothing was retrieved and so
nothing supports the claim); it is ABOUT THE RIGHT ENTITY (not merely a
same-named bearer); and it SUPPORTS THE CLAIMED FORM — the page actually
attests the `canonical_target_form` as an established target-language
rendering. That third one is the common failure: a page proving only that
the entity exists, or giving the name only in the source language, does not
support an `established` claim. A missing, empty, non-URL, or
search-results/query `source` rejects too, and so does evidence the judge
cannot read — an unverifiable citation is never approved on the grounds that
verification was unavailable, and going to fetch the page itself to settle
it is not an option that task has. `index.json` deliberately covers EVERY
item carrying a `source`, not only the `established` ones, so entries
outside the judge's scope are expected rather than a defect. The verdict is
**per batch, not per item**: a single failing item rejects the whole
fragment, so there is no partial verdict to express. A fragment with no
`established` items at all passes trivially — a live-mode batch that
happened to resolve everything by transliteration or sense-translation costs
one cheap prepare-and-approve pair, never a research round.

**Every attempt gets its own fragment path** — `out_{index}_attempt_{n}.json`
from attempt 0 onward, where this used to be one fixed `out_{index}.json`.
That is not tidiness: the single path made a citation rejection
unenforceable IN PRINCIPLE. A citation-rejected fragment is still perfectly
valid STRUCTURALLY — its URL is present and URI-shaped, which is exactly why
`--check-batch` passed it — so the wait step for the regenerated fragment
would return `READY` against the REJECTED bytes the instant it looked,
whether or not the agent had rewritten anything yet, and those bytes would
sail into the merge. Per-attempt paths make that impossible by construction
rather than by timing — but only together with the pre-run wipe, and only for
the WITHIN-run case. Attempt n+1's wait polls a path that does not exist
until the fresh dispatch atomically renames it into place: inside one run
because the path is attempt-scoped, and across runs because
`resume_setup.py` wipes stale fragments before the run starts (**1.16.0**).
It reuses the same `RUN_ID` on a digest-match resume and nothing deleted
fragments, so before that wipe a prior run's `out_{index}_attempt_{n}.json`
sat at exactly the path the new run would poll, and `--check-batch` — which
has no mtime, no token and no freshness notion at all — passed on those bytes
at once, so the reviewer audited the previous run's fragment. The wipe is
conditioned on the resume flag `resume_setup.py` already computes: a
**fresh** run wipes ALL `out_*` and `approved_*` attempts including attempt 0
(fresh-ID uniqueness only checks `runs/<RUN_ID>`, so an orphaned
`glossary/runs/<RUN_ID>` directory can outlive its identity directory and
collide on the one-second timestamp), while a **resume** wipes `n >= 1` and
every snapshot but keeps attempt 0, which the resume-skip optimisation
depends on wholly and which is citation-reviewed either way. Every
`evidence_*_attempt_*` DIRECTORY goes unconditionally under both flags,
attempt 0 included (**1.16.1**): evidence is an OUTPUT of the citation
review, re-produced by the prepare step before anything judges it, so a
surviving copy is never useful and is potentially wrong — it follows the
`approved_*` rule, not the `out_*` one.

For the same reason
the verdict sentinels carry the ATTEMPT number, not just the batch index — a
verdict is a statement about one attempt path, so a stale verdict simply
fails to match. A mismatched, malformed, or absent verdict falls to the
REJECT side, which is still the right direction but not a cheap one here: a
wrong reject costs one regeneration if the ladder then clears, and the WHOLE
RUN if it does not — the attempts exhaust to `citation-review-exhausted` and,
the merge being all-or-nothing, ZERO batches merge. A wrong accept costs a
permanently frozen fabricated citation, which is worse and unrepairable,
so the direction stands; the cost of being wrong is what is bounded loosely.

**What the merge is handed is the approved SNAPSHOT, not the attempt path**
(**1.16.0**) — because approval binding a path rather than bytes was not
enough even inside a single run, and needed no adversary to fail. The
dispatch is a fire-and-forget `codex:codex-rescue` job whose own prompt tells
it to rewrite the attempt fragment until that fragment's self-check passes,
and the codex job outlives the awaited call — which is *why* the wait poll
exists at all — so several atomic renames onto the reviewed path are ordinary
expected behaviour. `pipeline()` then waits for every batch before the one
`--merge-batches`, so an approved fragment sits un-rechecked while its
siblings climb their retry ladders; `--merge-batches` fresh-reads from disk
and knows nothing of the citation review, and `--verify-merged` re-reads too
but checks shape and coverage, never citations.

So the fragment's own `--check-batch` validation is re-run with
`--approve-to` as PREPARE's first command, before anything is fetched and
long before anything is judged: that invocation copies the exact bytes it
just validated — one `read_bytes()` from the read that validated them, no
second read, no window — to a create-once
`approved_{index}_attempt_{n}.json`, `fetch_citation.py` then takes its URLs
from THAT, and the judge audits THAT. The ordering is the whole fix and
cannot be reversed: snapshotting *after* the audit leaves a producer free to
replace validated-bytes-A with structurally-valid-bytes-B between the
reviewer's read and the copy, and fetching from the mutable attempt path has
the same defect one layer out — the URLs retrieved would be ones no reviewer
ever approved. On `CITATIONS_OK` the merge consumes the snapshot, so within
one run the bytes audited, the bytes approved and the bytes merged are one
object by identity, and a post-snapshot rewrite of `out_*` reaches nothing
anyone reads — the defect is unrepresentable rather than detected, with no
hash to compare and no window to keep short. That "within one run" is
load-bearing and rests on preconditions; the next section states them once,
and every other mention of this guarantee points there instead of restating
them.

The snapshot stays inside PREPARE's own turn rather than becoming a step of
its own, but since **1.16.1** the reason is no longer cost: the split already
spends the extra call, taking the live ceiling from
`1 + 3*(MAX_CITATION_RETRIES+1)` to `1 + 4*(MAX_CITATION_RETRIES+1)` — and
**1.16.2** took it further still, to
`1 + (3 + WAIT_CALLS)*(MAX_CITATION_RETRIES+1)` (**19** at the shipped
`WAIT_CALLS = 3`), when the wait itself stopped being reliably one agent call
— `WAIT_CALLS` is its worst case, not its price (see
**The chunked wait** above). What
survives is the structural reason, which was always the stronger one — this
is the ONE point both entry points into the review loop converge on. Putting
the snapshot in the wait step instead would silently skip it on every
resume-skipped batch, because that path runs neither the dispatch nor the
wait, and a resumed, never-reviewed fragment is precisely the case this whole
stage exists for. Prepare sits at that convergence point, so both entry
points get a snapshot and evidence alike.

#### What the approved snapshot guarantees, and the preconditions it rests on

This is the canonical statement of the property, and the only place its
qualifiers belong. Everywhere else that mentions it — `SKILL.md`, the other
`references/`, the workflow templates, `canon_validate.py` — states it in
short form and cites this heading by name instead of re-deriving its own
qualifiers, because a guarantee restated in six places is a guarantee that
drifts in six places. Correct it here.

**What holds.** Within one run the snapshot is published CREATE-ONCE: the
validated bytes go to a unique temp path, which `os.link()` then links into
place. What that buys is exclusive CREATION and nothing more — a second
`--approve-to` cannot publish over the entry: IDENTICAL bytes are an
idempotent no-op, and DIFFERENT bytes — a repeated `--check-batch
--approve-to`, or two overlapping reviewer dispatches for the same batch and
attempt — fail closed and name the path, leaving the already-audited copy
byte-untouched.

It does NOT make the published file immutable. Once created, the snapshot is
an ordinary writable file; `os.link()` never runs against it again, and any
process holding the path can truncate or rewrite it in place. "The bytes the
citation reviewer audits are the bytes the merge consumes" is therefore a
conclusion drawn FROM the three preconditions below, not something the
filesystem enforces by itself.

**Precondition A: one live run per glossary run DIRECTORY** — filesystem
identity, not string identity, and the two are not the same thing.
`RUN_ID_RE` is `[A-Za-z0-9][A-Za-z0-9._-]*` and `resolve_run()` returns the
caller's own spelling unnormalised, so `abc` and `ABC` are both valid and
distinct as RUN_ID strings; on a case-insensitive filesystem (the macOS
default) they name ONE `glossary/runs/<RUN_ID>/` directory. A precondition
worded as "one live run per RUN_ID string" would therefore not be enough: what
matters is what the filesystem resolves the path to.

**Precondition B: `durable_root` on a hardlink-capable filesystem.**
`os.link()` is what makes creation exclusive, so a filesystem that cannot
provide it (some SMB/FAT mounts) makes the publish FAIL, loudly and by name.
It deliberately does not fall back to an overwriting write, which would
silently restore the duplicate-approval race on precisely the setups nobody
tests on.

**Precondition C: nothing writes the snapshot path out of band.**
`--approve-to` is the only writer — by INSTRUCTION, not by enforcement, which
is why it belongs here rather than under what holds. Two agents hold the
path since 1.16.1, and neither sentence below is enforced by anything.
PREPARE runs the command that publishes it and is told "You must not create,
modify, or delete any file yourself. The only changes this task may produce
are the ones those two commands make on their own"; it reads no retrieved
bytes, so nothing it ingests can argue it past that. The JUDGE is the one
handed the path while reading untrusted fetched pages, and it is told "You
must not create, modify, or delete any file, in this directory or anywhere
else" — which the split does not enforce either: it removed the judge's
REASON to run a command, not its Bash tool. A
process that rewrites the path AFTER the audit and then returns
`CITATIONS_OK` defeats the property with preconditions A and B both intact,
because the merge fresh-reads whatever the path holds at merge time.

**What is NOT claimed.** No lock is taken, and the snapshot carries no
run-identity binding — nothing in it records which run produced it. The
property is OPERATIONAL, the same species as `canon.json`'s single-writer note
in `canon_validate.py`: it holds because the orchestrator runs one glossary
pass per run directory at a time, not because anything here locks a file. What
ENDS it is the run-start wipe — `resume_setup.py`'s
`_wipe_stale_glossary_fragments` unlinks every `approved_*` (its keep rule
spares `out_*_attempt_0`, and only on a resume) — so a second run starting on
a live run's directory deletes the audited snapshot and reopens the slot, and
the first run's already-issued `CITATIONS_OK` would then merge bytes nobody
audited. The wipe is deliberate and stays: it exists so a fresh run cannot
adopt an orphaned directory's stale attempt. That makes this a bounded
precondition, not an unnoticed defect.

**Evidence status.** The guarantee rests on `os.link()`'s create-once
semantics, not on any test — and the tests around it each touch less than
their names suggest, so read them for what they actually exercise. The
concurrent-writer test in `tests/canon_approve_to.test.py` starts eight
processes that call `_write_approved_snapshot` DIRECTLY, not through
`--approve-to`, and asserts that exactly one of them wins; it races the helper
rather than the CLI on purpose, since the window is microseconds wide and full
CLI runs would sample it only by luck. So it CAN catch this helper regressing
to a check-then-act publish, and has caught one across separate runs — but
sampling is what makes such a regression likely to be caught, not certain to
be. It says nothing about the CLI: that path is covered separately, by the
sequential `--check-batch … --approve-to` tests for its behaviour, and by a
wiring test pinning the CLI to publishing THROUGH this helper. Take each for
what it is.

Fail-closed follows from the snapshot being attempt-scoped as well: if the
winning attempt was never approved, the `approved_{index}_attempt_{n}.json`
the merge names does not exist and the merge dies on a missing file before
any `canon.json` write, while a rejected earlier attempt's snapshot sits at a
path the merge never names and so cannot satisfy it either. Under `offline`
no `established` item is legal, so no reviewer runs and no snapshot is
produced — the merge consumes the ATTEMPT path there. That is an explicit
branch, not a global rename: "the merge always consumes approved paths" would
make every offline merge fail on a missing file.

**The containment guard, and why line equality alone was not enough.**
`sentinelVerdict()` decides on whole-LINE equality: it sees a fail sentinel
only when that sentinel's line, after `String.prototype.trim()`, equals the
sentinel exactly — nothing else may share the line except what `trim()`
strips. In the realistic failure shape, a reviewer writing its finding and
then the sentinel on the SAME line, that prose is on the line regardless, so
ANY glue character hides the sentinel, a plain space included: **15 of 16 over
`GLUE_CHARS` in `tests/glossary_citation_review.test.py`, prose sharing the
sentinel's line**. Only a line feed puts the sentinel on a line of its own
(CRLF is safe for the same reason, and is deliberately not in that table).

With the sentinel ALONE on its line the same table splits, which is worth
knowing before "simplifying" anything here: **7 of 16 over `GLUE_CHARS` in
`tests/glossary_citation_review.test.py`, sentinel alone on its line**. `trim()`
reaches a line's two ends and so strips a space, tab, VT, FF, CR, NBSP, U+2028
and U+2029; those still match and still reject correctly. The 7 survivors — the
C0 separators U+001C–U+001F, NEL U+0085, a zero-width space, and any ordinary
character — hide the sentinel with or without prose. Do not reason about that
set by eye: U+0085 is not `trim()`-strippable in JS while U+2028 and U+2029 are.

**Always publish a gluing count with both its SHAPE and its SET**, naming the
set by constant and file as above. The same guard measured over a different
table, or over a different reply shape, yields a different and equally correct
number, and a bare count reads as a contradiction between surfaces that do
not actually disagree. This release publishes four, one per (set, shape)
pair — the two above, plus **14 of 15 over `ALL_GLUES` in
`tests/mass_translate_sentinel_containment.test.py`, prose sharing the
sentinel's line**, and **6 of 15 over that same set, sentinel alone on its
line**.

None of the four restates another, because the two sets are genuinely
different: they share 13 characters, `GLUE_CHARS` adds the C0 separators
U+001D–U+001F, and `ALL_GLUES` adds an ASCII hyphen and quote. `trim()`
rescues the same nine characters in each, so the alone-shape counts fall out
of what each set adds beyond the shared 13 — three unrescued characters
against two, hence 7 of 16 over `GLUE_CHARS` against 6 of 15 over
`ALL_GLUES`, both with the sentinel alone on its line.

Enumerate the four rather than asserting how many there are: a count OF the
release's own published counts is self-referential, goes stale the moment
another is added, and looks no different from a correct one at a glance —
which is exactly how "three" survived here past the fourth.

The end state is identical either way: the fail scan skips the sentinel, a
trailing clean OK line then approves the batch, and a reply carrying BOTH
verdicts silently resolves to the approving one.

Each of this template's four sites — precheck, wait, prepare and judge —
therefore now short-circuits to REJECT when `rejectedAnywhere(reply,
failSentinel)` finds the fail sentinel anywhere in the reply as a plain
substring, evaluated BEFORE `sentinelVerdict()` is consulted
at all. Substring containment is strictly easier to satisfy than line
equality, so the guard can only ADD rejections, never remove one — it moves
the failure into the fail-safe direction by construction, not by care.

The same guard is applied to `mass-translate-wf.template.js`'s translate and
review waits. Its `DRAFT_MISSING` fix check is guarded too, but in the OPPOSITE
direction and through a differently-named wrapper: there `DRAFT_MISSING` is the
OK sentinel, so gluing hides a GENUINE missing-draft report rather than faking a
pass, and `runRound` keys on `mentionedAnywhere()` — same containment test as
`rejectedAnywhere()`, which it delegates to, but a hit biases toward ACTING on
the sentinel instead of rejecting. Seven guarded sites over the two templates.
`skeptic-pass-wf.template.js` mirrors this control flow and is deliberately NOT
guarded — it sits in no `cache_key.py` bundle and carries its own
`compute_skeptic_input_digest()`, so editing it would force a fresh skeptic
RUN_ID that this release does not otherwise pay. See the 1.16.0 CHANGELOG entry.

The guard buys its safety with two false REDs, both worth recognizing in a
log. Neither is *bounded* in the sense that word invites: what a bound applies
to below is the number of attempts, never the cause of the reject.

- A reply that merely MENTIONS the fail sentinel while approving — "this is
  not a `CITATIONS_REJECTED 0 ATTEMPT 0` case" — now rejects.
- A sentinel can be a substring of a longer-indexed sibling: `ABSENT 1` occurs
  inside `ABSENT 10`. So a precheck or wait reply for batch 1 that quotes
  batch 10's sentinel takes the reject branch. The citation verdict is NOT
  exposed to this at shipped settings, because its sentinels end in
  ` ATTEMPT <n>`, which terminates the batch index —
  `CITATIONS_REJECTED 1 ATTEMPT 0` is not a substring of
  `CITATIONS_REJECTED 10 ATTEMPT 0`. The attempt number can collide the same
  way (`ATTEMPT 1` inside `ATTEMPT 10`), which the shipped
  `MAX_CITATION_RETRIES = 2` keeps unreachable; raising it to 10 or more would
  make it reachable.

**A false REJECT does not cost the same at every site**, and the difference is
what to read a failed run against. Of the seven, exactly ONE recovers
DETERMINISTICALLY inside the run — the precheck. At every other site the
trigger is the reply's PHRASING rather than the data, so whatever retry the
site gets — the citation ladder's next attempt in-run, a later run for the
other four — is another roll of the same die and not a repair:

- **Precheck** — `resumed` stays false and the batch falls through to the
  dispatch + wait it would have run had no fragment been on disk. Automatic,
  same run, same batch; the whole cost is the forfeited resume-skip saving,
  one codex dispatch plus one wait call — the fragment really is on disk and
  valid, which is what made the rejection false, so the wait's FIRST chunk
  validates it at once and returns `READY` without ever reaching a second
  chunk or the re-check. This is the only genuine repair of the
  seven, and it is genuine precisely because the fall-through path is correct
  regardless of WHY the precheck reported `ABSENT`.
- **Evidence prepare (1.16.1)** — joins the citation ladder below rather than
  falling through: a false hit on `EVIDENCE_FAILED` skips the judge call
  entirely, carries prepare's own reply forward as the next attempt's
  regeneration constraint, and still counts against `MAX_CITATION_RETRIES`,
  so that attempt costs `2 + WAIT_CALLS` calls rather than the ladder's
  `3 + WAIT_CALLS` — 5 rather than 6 at the shipped `WAIT_CALLS = 3`. Not a repair
  either, and for the same reason as the review below — the ladder varies the
  FRAGMENT, while what tripped the guard was prepare's WORDING.
- **Citation review — NOT RELIABLY self-recovering, however much its retry
  ladder looks like it.** The batch does regenerate to a fresh attempt and get
  reviewed again, bounded by `MAX_CITATION_RETRIES`. But the ladder varies the
  FRAGMENT while the guard was tripped by the reviewer's WORDING, and every
  prompt that owns a fail sentinel prints that sentinel verbatim in its own
  instructions — so a reviewer reasoning about its verdict in prose is an
  ordinary output, and the next attempt's reviewer reads the same invitation
  to do it again. It may decline it, and that attempt then merges in the same
  run — but that is a re-roll landing well, not a repair, since nothing the
  ladder varies addresses what tripped the guard. Burning all
  `MAX_CITATION_RETRIES + 1` attempts returns `citation-review-exhausted`, and
  the merge being all-or-nothing, **zero** batches merge: the run produces
  nothing while the data may have been fine throughout. What the bound buys is
  termination, not recovery: nothing about the trigger is per-run state, so
  re-invoking the pass is another re-roll rather than a reliable repair.
  **Telling the causes apart is what an operator actually needs**, and it
  is readable off the reply, which is why the exhaustion message states all
  three instead of one. The judge's prompt requires a genuine rejection to
  list, above its verdict line, one line per offending item naming that item's
  `source_form`, its `source` URL, and which of the three checks it failed and
  how; `batchStep` hands that reply to the next attempt as its regeneration
  constraint and returns it as `lastRejection`, so the text is there to read.
  A `lastRejection` naming specific `source_form` values with their URLs is a
  data problem — route those candidates to `disposition: "review_queue"` or
  supply real sources, then re-run. A `lastRejection` that instead reads as an
  approval, discusses the `CITATIONS_REJECTED` sentinel rather than any
  citation, or is the fixed no-findings placeholder is the guard misfiring:
  nothing in the data needs editing, the attempt fragments and their approved
  snapshots are on disk to inspect, and the right response is to treat it as a
  review-prompt defect and report it — not to re-run and not to hand-edit
  candidates. Since **1.16.1** a third cause reaches this same return: a
  `lastRejection` quoting a failing command rather than discussing any
  citation — `canon_validate.py --check-batch --approve-to`, or
  `scripts/fetch_citation.py` — is an environment or tooling fault, not a
  fact about the candidates. Run that exact command by hand and read its
  error; a fetcher that cannot reach the network at all fails every batch
  identically, which is the quickest way to tell this case from the other two.
- **Wait** — NOT automatic, and this is the one that matters. The site returns
  `{ready: false, reason: "glossary-pass-null"}` immediately, straight out of
  `batchStep`; the enclosing attempt loop does not catch it, because this is a
  `return` and not a `continue`. That batch is over for the run, and since the
  merge is all-or-nothing it takes the whole pass with it — `merged: false`,
  `reason: "fragment-check-failed"`, nothing merged at all. Recovery here is
  an operator re-invoking the pass, not the template retrying — and that
  re-invocation must NOT pass `resumeFromRunId`, or it replays this batch's
  cached replies unchanged; see `references/orchestration-and-batching.md`'s
  **Exception — a MATCH whose cached result is a non-answer (#404).**
- **Mass-translate's three sites**, for completeness, since they carry the same
  containment test: its review wait blocks that segment for the run
  (`reason: "review-timeout"`); its translate wait returns the deliberately
  non-terminal `reason: "translate-timeout"`, which `select_segments.py` treats
  as recoverable and auto-redispatches next run; and its `DRAFT_MISSING` fix
  site, on a false hit, probes via `draftPresentAndValid()`, finds the draft
  present, and returns `reason: "fix-call-failed"` with no terminal ledger
  write — also auto-redispatched. Those last two are the cheapest false REDs of
  the six.

Regeneration is bounded by `MAX_CITATION_RETRIES`, and the next attempt's
dispatch prompt is handed the rejecting reviewer's own findings (minus the
verdict sentinel lines) as its regeneration constraint. Dropping those lines
is PROMPT HYGIENE, and claiming anything stronger would be false: a leaked
sentinel reaches no parser at all. The dispatch call's own reply is
DISCARDED — its `await agent(...)` is not assigned to anything — and the only
reply sentinel-parsed anywhere near it is the separate wait step's, over a
disjoint `READY`/`PENDING` set that no `CITATIONS_*` string can collide with.
So a leak cannot corrupt the state machine or route a rejected fragment into
the merge. It is still worth stripping: that prompt is meant to hand the next
attempt the reviewer's findings and nothing else. Exhausting the
budget returns `merged: false` with `reason: "citation-review-exhausted"` —
deliberately a DISTINCT reason from `fragment-check-failed`, because "a
fragment never became structurally valid" and "the fragments were valid but
their citations did not survive review" are different operator problems with
different remedies. Either way nothing is merged.

Under `research_mode: offline` the stage is a no-op: `established` is
forbidden outright there (above), so there is no citation to review.

**Why it must be PRE-merge: a merged row cannot be repaired.** This is the
load-bearing rationale, not a preference for failing early. Once a
`source_form` is a key in `canon.json`'s `entries{}`, every shipped path
that could plausibly change it is closed:

- `canon_validate.py` is the only script in the plugin that writes
  `canon.json` at all, and the merge is the only one of its modes that can
  write an `entries{}` row. There is no amend, override, or correct mode. The
  two other writing modes reach the same single `_atomic_write_json` call
  site but cannot touch a resolved entry: `--init` is create-only (an
  existing canon.json is left byte-untouched and is not even read), and
  `--restamp-derivation` moves only the two `generation_hashes` fields.
- **A conflicting re-merge is fatal, not a fix.** `_merge_batch` raises on a
  genuine cross-run collision — two different resolutions claimed for the
  same `source_form` — naming both the old and the new value, and the whole
  merge is refused. An IDENTICAL re-submission is a silent no-op. So
  re-running the glossary pass with a corrected citation does not overwrite
  the wrong one; it fails the merge.
- **The glossary pass cannot even re-ask.** `glossary_batch_plan.py` drops
  every candidate already present as an `entries{}` key before the codex
  pass ever sees it (the Citation cache section below), and `--retry`
  overrides ONLY the `review_queue` exclusion — it cannot reinstate an
  already-resolved entry, and says so in its own diagnostic.
- **`--verify-merged` reports, it does not repair.** It fresh-reads
  `canon.json` and every named fragment and returns `{verified, missing[]}`.
  It is disk-independent and writes nothing at all — it can only tell you
  that the merged canon disagrees with the fragments, never reconcile them.
- **`canon_adjudication_audit.py` blocks, it does not repair.** Its own IRON
  RULE is explicit: it mechanically enumerates every item a human or a
  schema-validated codex workflow must sign off and cross-checks the
  recorded verdicts against canon.json's current state — and it never writes
  a verdict or a risk-acceptance itself.
- **The skeptic pass is post-merge, opt-in, and advisory-only.** Its
  `established_offline` risk class exists precisely because
  `canon_validate.py`'s offline backstop only checks INCOMING batches and
  never re-scans an already-frozen canon — and that class contributes nothing
  under `live` (`suspicion_scan.py`'s `_established_offline_forms()` returns
  an empty set unless `research_mode == "offline"`). A `live` `established`
  entry can still be flagged by the basis-blind classes — `singleton`,
  `all_citation`, `near_merge`, `merge_participant`, `high_dispersion`,
  `fold_collision`, `sampled` — whose only scope filter drops
  `is_proper_name: false` / `basis: "not_a_name"`. None of that repairs
  anything, which is the point here: no freeze/merge reader ever opens
  `skeptic_triage.json`, and its verdict schema cannot express a
  confirmation, let alone a repair.

What remains is a hand edit of `canon.json` outside every shipped tool. That
is a real option for a human, and it is exactly the expensive one this stage
exists to avoid — see **Retroactive canon edits invalidate precisely** below
for what it costs: every segment whose `used_terms_hash` covers that term
goes stale and is re-translated.

**Why in-batch, rather than "after all batches, before the merge".** There
is no such window. `glossary-pass-wf.template.js` runs
`pipeline(BATCHES, batchStep)` and then, in the SAME Workflow call, the
`--merge-batches` and `--verify-merged` steps — nothing pauses between the
last fragment becoming ready and `canon.json` being written. Pre-merge
therefore has to mean pre-READY, inside `batchStep`.

## Citation cache: `canon.json` itself, no new file

`canon.json`'s `entries{}` map is already frozen, hash-versioned, and
cross-segment — a name once resolved there with `basis: "established"` plus a
verified `source` URI stays resolved. "Verified" is load-bearing and now
literal: that URI cleared the pre-merge citation review above before the
merge ever ran, which is the only point at which it could still have been
rejected. Before each glossary pass,
`scripts/glossary_batch_plan.py` (1.3.5) curates `bootstrap_names.py`'s raw
candidate list against the CURRENT `canon.json`, excluding every candidate
already resolved there — both an `entries{}` key AND a
`review_queue[].source_form` (a queued name is only re-researched when a human
passes it to `glossary_batch_plan.py --retry`, the documented explicit-request
path). Only genuinely new candidates — never-before-seen names, or an explicitly
retried queued entry — are ever sent for fresh research. **Before 1.3.5 this
filter was prose only** (this very section, and the glossary-pass template's
header comment), delegated to "the orchestrating session," which in practice
excluded `entries{}` but never `review_queue` — so every queued name was
re-researched on every re-run (#101). Without the exclusion, every glossary-pass
re-run (a second book sharing recurring historical names, or simply re-running
the mass-translate step after an interruption) would re-research already-settled
names, wasting research effort and risking a genuinely different citation
surfacing on a later run for a name the canon had already frozen. When the
curated list is legitimately empty (every candidate already resolved),
`glossary_batch_plan.py` emits `{"no_new_candidates": true, "batches": []}` and
the orchestrating session skips `resume_setup.py` and the Workflow dispatch
entirely — nothing to research this run (`resume_setup.py` rejects an empty
`batches` list, which is why the marker exists). The same marker is also the
NORMAL first-run outcome on an uncased-script source whose preset ships no
`name_inventory` — `bootstrap_names.py`'s `Lu`-gated detector has nothing to
find there, so the curated list is empty because there were never candidates,
not because they were all resolved. Either way this branch skips the merge,
and the merge is the only writer of `canon.json` — so the SKIP branch must
run `canon_validate.py --research-mode <mode> --init --plugin-root
{{PLUGIN_ROOT}}` to bootstrap an empty-but-stamped canon before W3a, or
`segpack.py` fatals with `canon.json not found` (#290). `--init` is
create-only: it never re-stamps an
existing `canon.json`, since `select_segments.py`'s derivation-state gate
reads exactly the two hashes it would overwrite.

## `segpack.py`'s canon injection contract

Every per-segment pack gets:

- **`canon_names[]`** — locked forms the translator MUST use verbatim.
  Populated from `canon.json`'s `entries{}` map and never from
  `review_queue[]` — a queued, not-yet-resolved candidate has no frozen
  `canonical_target_form` to inject, so it can only ever surface to the
  translator via `new_names[]` (improvised, per-segment) until it is drained
  into `entries{}` by a later glossary pass.
- **`new_names[]`** — not yet canonized; the translator resolves by context and
  flags `NEW:` in its own `notes[]`, per the shipped task templates.

`new_names[]` is invalidation-load-bearing too, not just informational: a name
sitting in `new_names[]` at build time that gets canonized *later* by a glossary
pass on a different segment must invalidate this segment's own cache-hit
eligibility, exactly the same as a locked `canon_names[]` term would. Concretely,
`used_terms_hash` covers BOTH lists — canonizing a name correctly invalidates every
segment that had it as EITHER a locked term or an improvised candidate, never just
the former.

There is **no `canon_hash` field** and no whole-canon hash anywhere in the
cache/reuse path. The glossary-pass merge updates `canon.json` in place; the next
`cache_key.py` run recomputes each affected segment's own per-segment
`used_terms_hash`, so only segments whose own `canon_names[]` or `new_names[]`
references changed go stale.

**Retroactive canon edits invalidate precisely**, with the same effect as a
`term → [segment_ids]` index, but no such reverse index is persisted or maintained.
The precision falls out of recomputing `used_terms_hash` per segment against that
segment's own `canon_names[]` OR `new_names[]`, limited to terms currently present
in `canon.json`'s `entries{}`. A name a segment's own translator only ever
improvised, never yet locked, still counts as "used" by that segment for
invalidation purposes the moment it is later canonized.

Such an edit is a HAND edit, outside every shipped script — no plugin tool
rewrites an existing `entries{}` row (see **Pre-merge citation review**
above: the merge fatals on a conflicting re-resolution, `--retry` cannot
reinstate a resolved entry, and `--verify-merged` and
`canon_adjudication_audit.py` are both read-only about the verdict). What
this section describes is therefore the COST of correcting a frozen
decision, not a supported correction path: the invalidation is precise, but
every segment it reaches is re-translated. That cost is why an accuracy
decision is reviewed BEFORE it is merged, never after.

**Two obligations come with that hand edit, and skipping either can let wrong
text ship under a correct-looking hash.**

*Validate the file you just edited.* `canon_validate.py` run with
`--research-mode offline` and NO batch flag is its VALIDATE-ONLY mode: no
merge, no write, Pass 1 over every `entries{}` value against
`canon-entry.schema.json` and Pass 2 over the whole document. No ordinary W5 or
assembly step re-checks a hand edit — `segpack.py`'s canon injection copies any
entry object carrying a non-empty `canonical_target_form` into `canon_map`
without looking at the schema's required
`source_form`/`is_proper_name`/`basis`/`confidence` — so an unvalidated
malformed row reaches the reviewer's authoritative map and then assembly. The
opt-in Deliver-time `canon_adjudication_audit.py` does re-read the current
canon and refuses some malformed rows, but it is optional and its check is
narrower than this one; it is not a substitute. (`--verify-merged`
is a different mode and cannot stand in for this one: it requires `--batch`
fragments.)

*Regenerate the segpacks before the next `select_segments.py` run.* A
segment's `canon_map` was FROZEN when its segpack was built at W3a, while
`cache_key.py`'s `compute_used_terms_hash()` reads the LIVE `canon.json`. So
the edit by itself flips every affected segment to `stale` without changing
what any prompt will see: ordinary W5 then re-translates those segments
against the OLD frozen target form and records the NEW cache key — a durable
false green that reaches the assembled book. Re-run `scripts/segpack.py` from
the durable copy for every affected segment (or `--all`, which walks every
`manifest.json` `segments[]` entry; the builder is deterministic) before
selection runs again. Only the CLAIM profiles guard this today:
`evaluate_fresh_segpack_precondition()` is called from
`evaluate_claim_admission()`'s D6 and from nowhere else, so an ordinary
re-selection of a `stale` segment is unguarded. Note that regeneration is not
itself what invalidates anything — `compute_input_sha1()` hashes only the
segpack's blocks — it is what makes the corrected form visible to the
translator and the reviewer.

## Skeptic pass (RFC #215 Phase 2, opt-in + advisory)

The skeptic pass is an **opt-in, advisory-only** addition (`glossary.skeptic_pass.enabled`, default `false`): a deterministic `suspicion_scan.py` surfaces structurally-risky canon entries (over-merge participants, offline-established entries, singletons, high-dispersion names, citation-only figures, near-spelling pairs, and a globally-capped sample), then a scoped codex pass -- cloning the glossary dispatch control flow, never its identity-decision authority -- is fed bounded, whole-block windows for each flagged entity and adversarially asked to find a contradicting sentence or a genuine homonym split. Its verdict schema (`skeptic-triage.schema.json`) can express only `adverse` / `propose_split` / `propose_rescope` / `insufficient_window` -- there is deliberately no confirmation value, and no freeze/merge reader ever opens the resulting `skeptic_triage.json`. Every actual confirmation still flows through the unchanged human/codex `canon_adjudications.json` / `canon_senses.json` paths. `skeptic_report.py` is a separate, read-only advisory command that renders `skeptic_triage.json` for a human reviewer (per-entity risk context, the verdict, a quote derived fresh from the stored offsets, and evidence coverage) -- it is not a gate, it never blocks, and it runs strictly after `canon_adjudication_audit.py`, which is unchanged byte-for-byte by the skeptic pass's presence (see `tests/audit_unchanged_regression.test.py`).

Two scoping limits carry through to this reporting layer. First, **verse evidence stays block-only**: `evidence_verify` (and therefore any skeptic citation) can only authenticate an offset against `manifest.blocks{}`, never `verse.store[]` -- a citation whose window is an embedded-verse node can never byte-verify, so `skeptic_ready.py` DROPS it upstream and `skeptic_report.py` never needs to (and cannot) derive a quote from verse text. Dropping the citation is not the same as coercing the record, and the difference matters for what the report renders: `adverse`/`propose_rescope` lose their single required citation and the record really does coerce to `insufficient_window`, but a `propose_split` merely loses that referent and KEEPS its verdict as long as >=2 byte-verified referents survive. `evidence_coverage` DOES durably record that pruning (#368): `cited` is monotone — the maximum of the value already stored and the referent count at the current invocation — so although `--validate-fragment` rewrites the fragment in place and the normal path validates at least twice, a second validation reproduces the first's values rather than recounting the pruned list. A `2/3` therefore survives to the report; what it still cannot survive is an agent editing the fragment between two validations. Either way every referent the report can still see is block-anchored and byte-verified, which is what makes the quote derivation safe. Second, **`all_citation` is adapter-safe**: for `source.format` values with no configured citation-block-type set (i.e. anything other than `gutenberg_epub`/`plain_text` -- any `custom` adapter), the risk class is disabled fail-safe rather than guessed from tag spelling, annotated `citation_classification_unavailable` in the worklist; this never blocks the skeptic pass itself, it only means that one risk signal is honestly reported as unavailable for that project's format.

## `canon_link_groups.json` — recording that N canon forms are ONE referent (1.32.0, #588)

A third optional sidecar beside `canon_senses.json`, and the same shape of
thing: a place to record a decision `canon.json`'s 1:1 name dictionary
cannot express, without touching a hashed field.

`canon.json` maps one `source_form` to one `canonical_target_form`. Two
spellings of one person — the same name with and without maqaf, or with
different niqqud — are therefore **two entries sharing one target**, which
is indistinguishable from two different people sharing one target. The
obsidian adapter resolves that ambiguity the safe way and **de-links the
shared target entirely** (#206/#207): a click landing on the wrong entity's
note is worse than no link. In a pointed-script corpus the "two spellings,
one person" case is the normal one, so the book's most-named figures lose
every inline link they have. Measured in one delivered vault: 1373 unlinked
occurrences against 537 emitted links, every gate green.

`canon_link_groups.json` is where an identity call **made upstream** is
recorded so the renderer can act on it:

```json
{"schema_version": 1,
 "groups": [{"primary": "משה לייב",
             "members": ["משה לייב", "משה־לייב"],
             "note": "same man, with and without maqaf — adjudicated W7"}]}
```

`scripts/canon_link_groups.py` is the one runtime-validating loader
(`load_link_groups(path, entries) -> {member: primary}`); the full renderer
semantics — what a group does and, more importantly, the things it
deliberately does not do — live in
`references/output-target-adapters/obsidian.md`.

Since 1.58.0 (#497) a group has a **second** effect, documented in the same
place: when its members collide on a `#238/#241` fold key, that key's
source-anchored `## Mentions` occurrences are credited to the group's
**primary**. Before it, a fold collision withheld every member's occurrences —
27 canon forms and 2 390 records on the live he→en volume, with the coverage
gate reporting zero warnings. The crediting is all-or-nothing over the whole
fold key and never touches a form outside a collision.

**Why this is a sidecar and not a canon field.** `cache_key.compute_used_terms_hash`
hashes the WHOLE referenced canon ENTRY object, so adding any field to
`canon['entries'][name]` re-translates every converged segment that
references that name (see `hash-migration-impact.md`'s sidecar rule). A
sibling file stays outside all 15 cache-key fields, so a finished book can
adopt a group for **zero re-translation** — which is the entire point.

**The iron rule applies unchanged.** No script decides membership. `note` is
REQUIRED and non-blank precisely because the file records a call it does not
make: a group with no stated reason is indistinguishable from a mistake. The
decision itself comes from the same places every other identity decision
does — a human, or a codex adjudication pass — never from a matcher over
spellings. Membership is **byte-exact** against `canon['entries']` keys:
never folded, never NFC-normalized, and a member that is not a key is a hard
load error rather than a tolerated no-op, because a silent no-op is exactly
the failure that would leave an operator believing their pass was applied.
