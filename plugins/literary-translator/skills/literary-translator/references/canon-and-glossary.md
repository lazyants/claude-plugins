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

   **A machine-truncated candidate is never `accepted` (#383).** `bootstrap_names.py`
   bounds a candidate's `name` and marks the cut with a trailing
   ` [...truncated:<digest>]` marker, while occurrence lookup keys on the span's own
   UNCAPPED text (`span_match_keys()` — deliberately, see its docstring). So a canon
   entry whose `source_form` is that truncated spelling can never match an occurrence
   of itself: it is **inert** — zero occurrences, zero evidence, absent from
   `occurrence_targets`' output, and nothing anywhere reports "this entry is inert" as
   such. It fails as a green run rather than a halt.
   `glossary_TASK.template.md` therefore instructs the adjudicator to route
   any marker-bearing candidate to `review_queue`.

   **Both halves are enforced.** `glossary_preflight.py` step 6c guarantees the
   adjudicator is *told* the rule (it refuses to dispatch a durable prompt that lacks
   it), and `canon_validate._enforce_no_truncated_accepted()` refuses the answer if it
   comes back wrong — a marker-bearing item with `disposition: "accepted"` is rejected
   on ALL THREE batch entry points: the `--check-batch` gate, the
   `--merge-batches` write, and the legacy single-fragment `--batch` merge. They no
   longer each open-code the gate sequence; all three call one
   `_validate_and_enforce_batch()`, so a fourth entry point cannot silently miss a
   check. It can never reach `entries{}`. The same `source_form` as `review_queue` still
   passes: that asymmetry is the remedy, and `glossary_batch_plan.py` then excludes a
   queued form from every later batch.

   `canon_validate.py` is a `PLUGIN_BUNDLE_MEMBERS` entry, so this moves
   `plugin_bundle_hash` — but that field is one of the three inside the #491
   **machinery-only carve-out** (`assemble.py`'s `SAFE_STALE_CARVEOUT_FIELDS`,
   alongside `schema_hash` and `derivation_bundle_hash`), the set whose whole meaning
   is "can never change what the prose should say". A converged segment whose only
   drift is this field is admitted exactly like `converged`, expressly so that a
   plugin upgrade cannot strand a finished book. **Nothing re-translates.** What does
   move is resume identity: the next run in a refreshed root is a fresh `RUN_ID` with
   `resume: false`.

   **The dispatch prompt says so too.** `glossary-pass-wf.template.js` builds the
   per-batch prompt, and both of its sentences about `name` now carry the caveat: the
   field gloss no longer claims `name` is simply "the surface form as it appears in
   the source text", and the `source_form` instruction says the marker travels with
   the string and points at `glossary_TASK.md` for what that means. An earlier draft
   of this release left both alone to avoid moving `plugin_bundle_hash` — that
   reasoning was doubly wrong: the release moves that hash anyway (`canon_validate.py`
   is a bundle member), and the field is inside the machinery-only carve-out, so
   moving it re-translates nothing.

   In practice the trigger is not hostile input but source boilerplate: measured over a live French Gutenberg book,
   the single marker-bearing candidate was a 232-character all-caps run of the
   licence block, extracted with `likely_name: true`.

   **Operator note for an EXISTING durable root — `glossary_preflight.py` HALTS you
   until you migrate.** `glossary_TASK.md` is seeded once and is never
   auto-overwritten, so a project scaffolded before this rule shipped does not have
   it. Step 6c of the preflight refuses to dispatch a glossary pass whose durable
   `glossary_TASK.md` lacks the refusal sentence, the same content-axis shape #510
   uses — so the rule reaches existing roots, not only newly scaffolded ones. To
   clear it, copy the `source_form` and `disposition` bullets from the current
   `glossary_TASK.template.md` into your `glossary_TASK.md` by hand. Re-wrapping them
   to your own line width is fine; the axis matches whitespace-flattened text.
   Nothing re-translates: `glossary_preflight.py` is in neither bundle and
   `glossary_TASK.md` is in no cache-key field.

   This deliberately did **not** bump `PROMPT_CONTRACT_VERSION` to force the
   migration, even though that would also have worked: the constant is shared with
   `translate_TASK.md` and `review_TASK.md`, which are `compute_prompt_hash` inputs,
   so bumping it would re-stale every converged segment in every project — a cost far
   out of proportion to this defect. The preflight axis buys the same halt for
   nothing.

   **Copying the bullets does not reach a glossary run that is already in flight.**
   `glossary_TASK.md`'s bytes are not one of the resume digest's inputs (those are
   listed in `references/orchestration-and-batching.md` under **The resume-integrity
   gate and its digest inputs**), so a matching resume keeps each batch's
   `out_{i}_attempt_0.json`, `resume_setup.py`'s probe resume-skips it, and the merge takes what
   the OLD prompt already marked `accepted`. Recover with the stale-cached-result
   procedure in that same section — not restated here — plus the one step it does not
   cover: delete the attempt-0 fragments you want re-adjudicated before re-invoking,
   because here they are VALID and would otherwise be resume-skipped intact. They are
   exactly `${durable_root}/glossary/runs/${RUN_ID}/out_{i}_attempt_0.json`, and only
   those — leave `manifest_*`, `approved_*`, `evidence_*`, the run directory itself and
   `canon.json` alone. A fresh run is the blunt alternative: correct, but it forfeits
   every batch's resume saving.
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
   injection contract**). **#653:** that exclusion sentence is now
   incomplete on its own — a name whose queued row was DISMISSED
   (`canon_validate.py --correct` with `disposition: "dismiss"`, see
   **`--correct PATH`** below) is excluded too, even though its
   `review_queue[]` row is gone: `glossary_batch_plan.py` also reads
   `corrections[]` and excludes any `source_form` for which ANY document
   there carries `disposition: "dismiss"` — deliberately not "the most
   recent document for this source_form wins": `dismiss` adjudicates
   `review_queue[]` and `correct`/`remove` adjudicate the disjoint
   `entries{}`, so a "most recent wins" rule would let a LATER, unrelated
   entries{}-side `correct`/`remove` sharing that source_form silently
   REVOKE an earlier dismissal, reopening it to automated re-research with
   no operator saying so. A name that was never queued and is not dismissed
   sails through unaffected. `glossary_batch_plan.py --retry` is the SOLE
   way back — the only thing that reinstates either exclusion, once it is
   worth re-researching — but only that: it lifts the
   step-(1) exclusion above, never step-(2) curation. A retried name that
   comes back as a candidate with `likely_name: false`, or under
   `--min-candidate-freq`, is still dropped and reported on stderr rather
   than dispatched — the identical treatment a queued name gets today
   (`tests/glossary_batch_plan.test.py::test_retry_dropped_by_curation_emits_note`
   pins it for the queued case; a
   dismissed name inherits it unchanged). No force-inclusion path exists
   past that curation step for either kind of retried name.
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
but do not read `16N + 2` or `4N + 2` as what a run will actually spend. In the
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
  },
  corrections: { type: "array", items: <canon-correction.schema.json shape> }
}
```

`entries{}`, `review_queue`, AND `generation_hashes` are ALL THREE required
unconditionally at the top level. `corrections[]` (**#495**) is the one
OPTIONAL key: it is created on the first `--correct` call and absent from every
`canon.json` written before that mode existed, so absence must stay valid. The
merge path never creates or touches it. **#653:** its log now covers two
different targets, not one — a `correct`/`remove` document adjudicates
`entries{}`, a `dismiss` document adjudicates `review_queue[]` — `disposition`
is what tells a reader which; see `canon-correction.schema.json` below.

### `canon-correction.schema.json` — one adjudicated correction (#495, #653)

The document `--correct` reads, and — verbatim — the record it appends to
`corrections[]`. One shape for all three dispositions on purpose: separate
shapes for one decision drift apart, and the record's whole job is to show
the next operator what was adjudicated.

```
{
  source_form: { type: "string", minLength: 1 },   // correct/remove: must already be in entries{}
  disposition: { enum: ["correct", "remove", "dismiss"] },
  old_entry:   <any JSON value>,                   // correct/remove: the value the caller says is on disk
  new_entry:   <canon-entry.schema.json shape>,    // required iff correct, forbidden iff remove/dismiss
  old_item:    <any JSON value>,                   // dismiss: the review_queue[] row the caller says is on disk
  reason:      { type: "string", pattern: "\\S" }
}
```

| disposition | required | forbidden |
| --- | --- | --- |
| `correct` | `old_entry`, `new_entry` | `old_item` |
| `remove` | `old_entry` | `new_entry`, `old_item` |
| `dismiss` | `old_item` | `old_entry`, `new_entry` |

`source_form` and `reason` stay unconditionally required across all three —
`dismiss` still names which candidate it is about, in the same field
`correct`/`remove` use for the `entries{}` key. Every `corrections[]` record
written before #653 still validates: all of them carry `old_entry`, none
carry `old_item`, and `dismiss` is additive to the `enum`.

Two asymmetries carry real weight:

- **`old_entry` AND `old_item` are BOTH schema-UNCONSTRAINED, `new_entry` is
  `$ref`-validated.** All three interlock fields' job is EQUALITY against
  what is on disk, not shape — and the value most in need of correcting or
  dismissing is the one that fails its own schema. A hand-edited `canon.json`
  (the only repair route that existed before `--correct`) can put ANY JSON
  value under an `entries{}` key — `_load_canon` type-checks `entries`
  itself, never its values — and VALIDATE-ONLY exists to find exactly that;
  even `type: "object"` would be too strong for `old_entry`, refusing a
  string, an array or a `null` and leaving such a row DIAGNOSED but not
  REPAIRED, so it admits any JSON value instead. `old_item` was given a
  schema-level `oneOf` for the same two shapes once, and it was REMOVED on a
  later code review (#653): a shape check alone can only ask "is this a
  non-empty string, or an object with a non-empty-string `source_form`
  field" — it cannot ask whether that string, or that field, actually
  EQUALS the document's own `source_form`, which is the runtime check below
  and the only question that matters. The `oneOf` was strictly weaker
  because of that gap, and it produced a FALSE refusal message besides:
  `jsonschema`'s `oneOf` formatter in this codebase is written for
  `canon-batch.schema.json`'s disposition-discriminated union and reports
  "(disposition absent/unrecognized -- best match across all branches)" —
  untrue of an `old_item` shape error, since neither `old_item` branch ever
  carried a `disposition` const to begin with. `old_item` is UNCONSTRAINED
  in the schema now, exactly like `old_entry`, and the difference is that a
  dismissal additionally runs `old_item` (and every `review_queue[]` row it
  is compared against) through `_attributable_to` at RUNTIME: True only for
  a mapping whose own `source_form` field equals the document's, or a bare
  string equal to it — everything else (a mapping naming some OTHER
  source_form or missing it, a list, a number, a boolean, `null`) is False,
  and is refused naming both values, by the code that actually owns the
  rule rather than by a schema formatter that cannot describe it correctly.
  `new_entry` is what gets frozen, so it alone validates fully.
- **Neither is an open channel, despite the loose schema type either field
  carries.** Both must equal
  what is on disk — compared as canonical JSON, not with Python `==`, so the
  boolean/number collapse (`True == 1`) cannot let a correction or a
  dismissal state one value, pass, and be RECORDED as another; object key
  order stays irrelevant. For `dismiss` specifically, equality alone is not
  enough — see the runtime ATTRIBUTION check (`_attributable_to`) in
  **`--correct PATH`** below for
  why the stated row must also be attributable to the document's own
  `source_form` before it is searched for at all. So the only value that can
  ever be recorded through either field is one `canon.json` already held.
  `reason` is required and non-blank but NOT length-capped —
  `canon-entry.schema.json`'s own `note` is unbounded and rides in the same
  document via `new_entry`, so capping `reason` would bound one field while
  leaving the record exactly as open as every other operator-authored free
  text in `canon.json`.

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
The NON-stamping modes below — `--check-batch`, `--correct`,
`--verify-merged` and validate-only — resolve no sibling and accept neither
flag's obligation; do
not add either to them. `--correct` is the one that makes "stamping" narrower
than "writing": it writes `canon.json` but carries its existing stamp forward
verbatim and computes no hash at all.

1.2.0 adds three new modes to close #87 (schema-less glossary dispatch,
`references/orchestration-and-batching.md`), #90 (concurrent-batch races),
and #88 (unverified merge) — routed by `main()` on which flag is given,
alongside the original `--batch PATH` merge path (still supported and
exercised directly by existing tests; like `--merge-batches` it has since
taken **#291**'s no-op stamp conservation, **#412**'s trusted-sibling
requirement and now **#505**'s live citation attestation):

### `--check-batch PATH [--expect-source-forms-file M.json]` — one fragment, no write

The self-check invocation issued character-identically by
`batchDispatchPrompt`, `batchWaitChunkPrompt` and — since **1.16.2** —
`batchWaitRecheckPrompt` (see `references/orchestration-and-batching.md`). A
fourth issuer, `batchPrecheckPrompt`, was removed in **#724**: the same command
is now run by `resume_setup.py` itself, before the Workflow starts, so it is no
longer kept character-identical by construction and must not be assumed to be. Pass-1 per-item validation plus
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
later pass unless `--retry` names it. **#653:** a DISMISSED name's note is
different again — the row it lived on is gone from `review_queue[]`, and
`person_registry.py` never reads `corrections[]` (see the `--correct PATH`
section's `disposition: "dismiss"` bullet), so a dismissal's `reason` reaches
neither W9r prep nor anywhere else; only the `corrections[]` record itself,
read by a human, carries it forward.

So the moment this workflow returns `merged: true` — after its own
`--verify-merged` call, which is the first point the operator or the
orchestrating turn has control again — copy every such note into
`consistency_issues.md`, one line each, before the next batch starts. Only the
promotion into `style_bible.md`'s E-traps waits for a batch boundary, where
`SKILL.md`'s R9 prices it; that split is the ordering `style_bible.template.md`
already ships under E-traps.

**`--citations-reviewed` — the writer refuses to freeze an unaudited citation
(#505).** Under `--research-mode live`, both merge modes REFUSE a fragment
carrying any `basis: "established"` item unless the caller passes
`--citations-reviewed`. Nothing is written when this fires; the whole batch
merge is rejected and `canon.json` is byte-unchanged.

The gate exists because the pre-merge citation review is the ONLY thing
anywhere that opens a cited page, and it lives entirely inside the glossary
pass's Workflow control flow — while this script serves a second caller the
Workflow knows nothing about: a merge run BY HAND. That is not a hypothetical
path. This script's own module docstring records that the real historiettes-t3
project ran its glossary pass as ad hoc `glossary/TASK.md` + codex batches,
never through this pipeline, and `#505`'s own measurement — 7 of 23 citations
unusable, 4 of them well-formed product URLs on the publisher's real domain —
came from exactly such a pass. A merged row is frozen by the merge and the
downstream reviewer is forbidden to question a frozen canon form, so what
reached the writer unaudited stayed — until somebody noticed and ran
**#495**'s `--correct`, which is exactly the point: that is an act
somebody has to know to perform, and an unaudited citation announces nothing.

Three properties worth stating exactly, because each is a limit:

- **It is an attestation, not a proof.** The approved snapshot is written by
  PREPARE *before* any evidence is fetched, so "this path is an
  `approved_*.json`" says nothing about the verdict. **#723** narrowed the gap:
  the pass writes `approval_{i}_attempt_{n}.json` naming the
  sha256 of every APPROVED fragment, so the attesting operator can pick those
  exact bytes by digest instead of guessing which snapshot won — the guess that
  once merged a batch whose only recorded verdicts were rejections. **#734** then
  made the script require it: `--citations-reviewed` is refused without
  `--approval-records`, one record per merged fragment, each naming that
  fragment's digest. **That reader can only REFUSE.** No record permits anything,
  and none authorizes skipping the citation review — which stays unconditional on
  both entry points — so a copy forged in the model-writable directory buys its
  forger only the merge an honest record would have allowed, and the trusted
  point stays the operator. What it removes is narrower than it looks and worth
  stating exactly: until #734 the pass decided whether the record had been
  written by reading the recording agent's own *sentence*, so a reply that
  claimed a write that never happened merged anyway. The check moves that one
  decision onto the filesystem; it cannot make the record HONEST, because the
  same turn runs the command and reports on it. What the refusal
  converts is a SILENT freeze into a deliberate act, the same ceiling as
  `--plugin-root`/`--allow-durable-sibling` (#412) and `reject_review.py`'s
  attested `--reason` (#461).
- **It keys on `basis`, never on `disposition`.** A `review_queue` item may
  carry `basis: "established"` — the queued branch requires only `note` and
  leaves `source` unconstrained — and `_merge_batch` freezes it into
  `review_queue[]` verbatim, so an accepted-only scan would leave that door
  open. The Workflow's own reviewer scopes by `basis` for the same reason.
- **`offline` is untouched.** There `basis: "established"` is forbidden
  outright and the older backstop rejects it first, with its own message.
  Telling an offline operator to attest a review that offline cannot run would
  be worse than saying nothing.

Auditing a citation by hand goes through `scripts/fetch_citation.py`, never
`curl` — that boundary is what checks scheme and address, pins the connection
to the address it vetted, re-validates every redirect hop, and caps time,
bytes and content type. The reason is set out under "Since 1.16.1 (#347) the
stage is TWO calls per attempt" above, and it applies to a human reaching for
a terminal exactly as it applies to a judging agent holding Bash.

The Workflow passes the flag itself, on exactly the reviewed path: see
`references/orchestration-and-batching.md`'s final-merge command. That is also
why `glossary_preflight.py` gained a **script axis** — the template is
instantiated fresh from the plugin every run but executes the DURABLE copy of
`canon_validate.py`, so a durable root that has not re-run Step 0a's copy pass
would meet the new flag with `unrecognized arguments`, *after* the whole pass
had been spent. The preflight halts on that skew before dispatch instead. It
guards the documented SKILL W3 launch path, which is where preflight runs; a
caller that instantiates and runs the template directly does not get it.

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

Still the same shape it had pre-1.2.0 — though not untouched since: it takes
every cross-cutting merge precondition `--merge-batches` does, **#505**'s live
citation attestation included. It merges one glossary-pass batch result into
`canon.json` in a single call, running Pass 1 + the offline backstop + the
citation attestation + the dedup/collision merge + `generation_hashes`
stamping + the atomic write + Pass 2. Existing tests exercise this path
directly; it is not deprecated, just no longer how the Workflow template
itself drives a real multi-batch glossary pass (that's `--merge-batches`
now).

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

### `--correct PATH` — the one sanctioned route to change a FROZEN entry (#495)

`canon.json` used to be write-once in practice. `_merge_batch` raises on a
genuine cross-run collision (two different resolutions for one `source_form`),
`--init` is create-only, `--restamp-derivation` touches provenance only, and
VALIDATE-ONLY writes nothing — so a batch carrying a CORRECTED resolution was
rejected *precisely because it was a correction*. That refusal is right as a
defence against a re-adjudication silently overwriting a frozen decision, but it
left no route for the case where the frozen decision is simply WRONG. And a
canon that contradicts the text is not inert: it keeps generating false review
findings, and the cheapest way to clear those is to revert correct prose to
match a wrong canon. The guard pushed toward corrupting the deliverable.

The only available move was to replace `canon.json` by hand — a hand-edit of
exactly the artifact the whole gate chain treats as frozen, performed outside
every validation the tool owns, with nothing capturing what changed, why, or
that a human adjudicated it.

`--correct` is that route, deliberately OUT-OF-BAND rather than a relaxed merge:

- **`_merge_batch` is untouched, and there is no `--force`.** An ordinary
  re-adjudication batch carrying a differing resolution still raises the
  collision error, unchanged. Correction is a separate, explicitly named mode.
- **One entry per call, and it must state what it is changing FROM.**
  `old_entry` is refused, naming BOTH the on-disk value and the stated one, when
  it does not match — so the mode cannot be used blind against a `canon.json`
  that moved since it was read.
- **A `reason` is required.** The correction document is appended verbatim to
  `corrections[]`, so the next operator meets an adjudicated change rather than
  an unexplained diff.
- **`disposition: "correct"`** replaces the record under the same key
  (`new_entry`'s own `source_form` field must equal that key — a record filed
  under an unrelated map key is a defect `canon_adjudication_audit.py` exists to
  catch), and is REFUSED when the form is an adjudicated homonym split, through
  the same `is_split` predicate `_merge_batch`'s recollapse guard uses.
- **`disposition: "remove"`** deletes the record, and is deliberately NOT
  split-refused. `canon_adjudication_audit.py`'s BLOCKING `collapsed_split` is
  "never satisfied by an adjudication record — the underlying `canon.json` entry
  must actually be corrected", and no substituted bare entry can satisfy it, so
  removal is its only repair. Removal is also what an interpolated name with
  zero source occurrences needs. A key RENAME is a `remove` followed by an
  ordinary `--merge-batches` under the new key, never a third disposition.
- **`disposition: "dismiss"`** (#653) operates on `review_queue[]` only —
  `entries{}` is passed through untouched. It records that a human looked at
  a queued candidate and decided it is deliberately not canon-worthy, which
  otherwise has no spelling: the only way to drain a `review_queue[]` row was
  an accepted merge (`_merge_batch`'s accepted-item branch), which FREEZES an
  `entries{}` record — there was no way to say "not canon-worthy" without
  saying "canon, worded thus". It carries `old_item` instead of `old_entry` — the same
  blind-use interlock, restated against `review_queue[]` rather than
  `entries{}` — and `old_item` must be ATTRIBUTABLE to the document's own
  `source_form` before any row is searched for: either a mapping whose own
  `source_form` equals the document's (the ordinary queued shape, the same
  identity `_merge_batch`'s accept-branch filter uses,
  `q.get("source_form") != source_form`), or a string equal to it (the
  legacy bare-string row, where the string IS the name). Every other shape —
  a mapping with no `source_form` or a differing one, a list, a number, a
  `null` — is refused naming the rule, never searched for: with
  `review_queue: ["Pilou"]` on disk, a document naming
  `source_form: "Vertus"` and `old_item: "Pilou"` would otherwise match by
  whole-value equality, drop `"Pilou"`, and record a `corrections[]` entry
  saying **Vertus** was dismissed — a decision nobody made. Once attribution
  holds, every row equal to `old_item` (by `_same_json_value`) is dropped —
  two rows for one form are ordinary, not a hand-edit artifact:
  `_merge_batch` appends whenever the whole object differs, so one form
  queued by two batches for two different reasons is two rows
  (`person_registry.py:903-921` coalesces them for display), and matching on
  the whole value dismisses one reason without silently dismissing the
  other.

  It deliberately does NOT refuse a `source_form` that is also an
  `entries{}` key — the dismiss branch itself carries no such check. The two
  row shapes reach a very different outcome, though, and it is worth being
  exact about which: `_assert_no_entries_review_queue_overlap` (in
  `canon_validate.py`) builds its `queued_forms` set only from
  `isinstance(item, dict)` rows, so a DICT row duplicating an `entries{}`
  key IS an overlap that invariant catches — an INVALID state, not a
  legitimate one — but only against the document `_stamp_write_verify`
  validates, which is the POST-dismissal merged document. So dismissing
  that very row REPAIRS the overlap (the offending row is gone by the time
  Pass 2 runs); dismissing some OTHER row while leaving that dict row in
  place still fails Pass 2, and the write is refused, naming it — the same
  "reachable, not a corrupt file" boundary the malformed-row case below
  draws, applied to this invariant instead of to shape. A BARE-STRING row
  duplicating an `entries{}` key is invisible to this invariant either way
  (it only ever inspects dict rows), and is refused instead — if at all —
  by the queue-item schema shape, the separate, narrower case covered next.
  Removing the `entries{}` record itself is a different
  decision and stays `disposition: "remove"`.

  **The bare-string shape's drain capability is narrower than it looks,
  though (code review, #653).** `_stamp_write_verify` Pass-2-validates the
  WHOLE post-dismissal document before writing, and `canon-file.schema.json`
  types every `review_queue[]` item as the queued OBJECT shape — so a
  bare-string row anywhere else in the queue still fails whole-file
  validation after the dismissal, and the write is refused, naming that
  other row. Dismissing a bare-string row therefore only succeeds when it
  is the queue's LAST remaining malformed row; a queue holding several is a
  corrupt-file case, not a one-malformed-row case — the same boundary
  `--correct` already draws for `entries{}` (see "The boundary: one
  malformed row, not a corrupt file" below, and its `review_queue[]` twin
  just past it). That means the 61 live bare-string rows measured in this
  project's own corpus (`historiettes-fr-ru/tome3`) are NOT drainable by
  this mode today: that file fails whole-file validation for an unrelated
  legacy reason already (its `entries{}` records carry `canonical_ru` and
  no `source_form`), so no writing mode — `dismiss` included — can write it
  until that unrelated failure is repaired separately. The shape exists for
  the queue that HAS only one malformed row left, not for that corpus as it
  stands.

  `dismiss` is exempt from the split refusal (see the `disposition: "remove"`
  bullet above) and from `_enforce_citation_source_safety`/the offline
  backstop (see "the same content controls" bullet below), for the same
  reason `remove` is exempt from each: both constrain what may be FROZEN,
  and a dismissal freezes nothing. It WRITES but does not STAMP, also like
  `correct`/`remove`. `_content_view` (in `canon_validate.py`) treats an
  ordinary `review_queue[]`-only change as content and re-stamps, because
  `glossary_batch_plan.py` reads that array back to exclude a queued name
  from re-research (see item 3 above) — but that function's own docstring
  carves a dismissal out explicitly (a paragraph inside `_content_view`'s
  docstring, with no symbol of its own to cite): `run_correct` passes
  `preserve_stamp=True` for every disposition, so `_stamp_write_verify`
  never runs this comparison for `dismiss` at all. That is not in tension
  with the re-stamp rule — a dismissal carries the SAME exclusion forward
  into `corrections[]` instead of `review_queue[]`, so specifically the
  automated re-research exclusion `glossary_batch_plan.py` enforces does not
  move, and the existing `generation_hashes` are carried forward verbatim,
  unchanged from every other `--correct` disposition. That is narrower than
  "the file's behaviour is unchanged": it is not — `person_registry.py`'s
  `refusals[]` output changes (below), and `canon_adjudication_audit.py`'s
  category-4 enumeration is REQUIRED to change (acceptance criterion 4). The
  stamp tracks derivation provenance, not every consumer's behaviour, which
  is exactly why those two changing does not call for a re-stamp.

  **One consumer DOES see a dismissal**, and it is disclosed rather than
  argued away: `person_registry.py` turns every DICT-shaped `review_queue[]`
  row into a refusal-only unit (`person_registry.py:899-933`,
  `refusal_only: True`) and emits it in `refusals[]`
  (`person_registry.py:1994-2004`, `refused_by: "canon_review_queue"`); it
  never reads `corrections[]`. So a dismissed name stops appearing as a
  `canon_review_queue` refusal in a later W9r registry run — that is the
  intended meaning of the decision, not a side effect to suppress: the
  operator said this candidate is not canon-worthy, and the refusal list is
  where "still undecided" is reported. A bare-string row is unaffected
  either way: `person_registry.py:905-906` skips any row that is not a
  `dict`, so it was never surfaced in `refusals[]` before a dismissal
  either — dismissing it changes `review_queue[]` and the exclusion set,
  never W9r's refusal list.
- **It is subject to the same content controls as the merge path.** A
  `disposition: "correct"` runs `new_entry` through `_enforce_citation_source_safety`
  (#347's static citation boundary — a loopback/private/non-public `source` is
  refused before it can be frozen) and through the offline backstop. That
  backstop is scoped to the CLAIM, which is the `canonical_target_form`/`source`
  PAIR rather than the `basis` label: under `--research-mode offline` a
  correction that states an established claim the row did not already carry
  verbatim is refused, while correcting anything else on an established row
  (`note`, `confidence`, `category`) stays legal. Scoping it on `basis` alone
  would let a correction replace both the rendering AND its citation offline and
  keep the exemption purely because the old row also said "established". Being a second
  write path into `entries{}` is exactly why: #347 calls itself "the only place
  such a `source` can be stopped before it is frozen into `canon.json`", and a
  route that skipped it would make that claim false. `remove` is exempt from
  both — they constrain what may be FROZEN, and refusing a removal over the
  outgoing entry's own bad `source` would trap the record most worth deleting.
- **It WRITES but does not STAMP.** The existing `generation_hashes` are carried
  forward verbatim. `_content_view` excludes only the stamp, so a corrected
  entry reads as a changed document and #291's rule would restamp it — which
  would advance the particle-config/derivation-bundle provenance claim and clear
  `select_segments.py`'s derivation-state gate with nothing regenerated. Nothing
  is lost: the re-stale signal for a corrected entry is `cache_key.py`'s
  per-segment `used_terms_hash`, not these hashes. A `canon.json` whose stamp is
  absent or malformed is REFUSED rather than stamped fresh, pointing at
  `--restamp-derivation` — the one mode that may advance provenance, by name.

**The boundary: one malformed row, not a corrupt file.** `--correct` repairs a
row whose value is not a valid canon entry — that is what the unconstrained
`old_entry` above is for, and it is reachable, because the hand edit this mode
replaces can leave any JSON value under an `entries{}` key. It does NOT repair a
canon.json carrying SEVERAL such rows: `_stamp_write_verify` Pass-2-validates the
whole document before touching disk, so fixing one row still leaves a file that
fails validation and the write is refused. That is not a property of this mode —
`--merge-batches` and `--restamp-derivation` refuse the identical file with the
identical error, and the check is the gate that stops a corrupt document from
being written at all. Several malformed rows is file corruption rather than a
wrong adjudication: a different problem, with validate-only mode as its
diagnostic. Measured across the four live books: 0 malformed rows in 999
entries. Pinned as a characterization in
`tests/canon_correct_entry.test.py::test_more_than_one_malformed_row_blocks_every_writing_mode_alike`.

**`review_queue[]` draws the identical boundary, one malformed row at a time
(#653 code review).** `dismiss` repairs a bare-string queue row for the same
reason `old_entry` repairs a malformed `entries{}` row — a hand edit can put
one there. Not because nothing constrains the shape: `canon-file.schema.json`
DOES type every `review_queue[]` item as the queued OBJECT shape
(`review_queue.items` is `{"$ref": "canon-batch.schema.json#/items/oneOf/1"}`),
the same way it types `entries{}` values (`entries`'s
`additionalProperties` is `{"$ref": "canon-entry.schema.json"}`) — the schema
constrains both. What makes the row REACHABLE is the read path, not the
schema: `_load_canon` type-checks that `review_queue` itself is a LIST,
exactly as it type-checks `entries` is an OBJECT, but checks neither
collection's ITEMS — so a hand-edited bare-string row loads without
complaint and `dismiss` can name it, the same way a hand-edited malformed
`entries{}` value loads and `old_entry` can name it. Pass 2 is where the
schema does apply, on the way back OUT. The same Pass-2 whole-document
check applies: dismissing one bare-string row still leaves a file that fails
validation, and the write is refused, if ANY other row in the queue is also
malformed. Unlike `entries{}`'s measured 0-in-999, this population is NOT
empty: `historiettes-fr-ru/tome3`'s `review_queue[]` holds 61 live bare-string
rows today, so this mode cannot drain them, or write that file at all, until
its unrelated whole-file failure (`entries{}` records carrying
`canonical_ru` and no `source_form`) is separately repaired — see the
`disposition: "dismiss"` bullet above. Pinned as a characterization in
`tests/canon_dismiss_queued.test.py::test_two_malformed_queue_rows_block_dismissal_of_either`
(refused) and
`tests/canon_dismiss_queued.test.py::test_dismiss_of_the_last_malformed_queue_row_still_succeeds`
(succeeds, same fixture minus the second row) —
`::test_dismiss_drops_a_bare_string_queue_row` is the same boundary's single-row
case.

**What a correction costs.** `compute_used_terms_hash` hashes only the entries a
segment actually references, so correcting one entry re-stales exactly the
segments carrying that form and no others. Those units are admissible for
bounded re-review via `--from-converged` (since **1.25.0**), and re-review cannot
reach translate — so a correction costs bounded re-review, never
re-translation. It does change `canon.json`'s BYTES, which is a frozen input of
the skeptic pass (`canon_sha256`) and of `suspicion_scan.py`'s worklist
freshness gate: run a correction BETWEEN passes, not into a live one. (That was
equally true of the hand-edit it replaces.)

**Sizing a canon change BEFORE you apply it.** `compute_used_terms_hash` reads
`canon.json` from whatever root it is handed, so the segments a *pending*
change would re-stale are computable with no new tooling and nothing written.
Put the post-merge `canon.json` in a scratch directory beside a symlink to the
real `segments/`, and compare the field per segment against the live root:

```
CAND=$(mktemp -d)
ln -s "$DURABLE/segments" "$CAND/segments"
#  ... write the candidate (post-merge) canon to "$CAND/canon.json" ...
for pack in "$DURABLE"/segments/segpack_*.json; do
  seg=$(basename "$pack" .json); seg=${seg#segpack_}
  live=$(python3 "$DURABLE/scripts/cache_key.py" --field used_terms_hash \
           --seg "$seg" --durable-root "$DURABLE") || exit 1
  cand=$(python3 "$DURABLE/scripts/cache_key.py" --field used_terms_hash \
           --seg "$seg" --durable-root "$CAND") || exit 1
  [ "$live" = "$cand" ] || echo "$seg"
done
```

The candidate root needs nothing else — no `profile.yml`, no ownership marker,
no `manifest.json`, no `scripts/` of its own — because `used_terms_hash` is a
per-segment field, and `compute_one_field` loads the profile only for a global
one. **Check both exit statuses**, as the `|| exit 1` above does. A
`cache_key.py` that fails — a mistyped `$CAND`, a segpack the scratch root
cannot see — writes to stderr and exits non-zero; a loop that discards
that status compares a real digest against the empty string and prints EVERY
segment. A broken run and a near-total re-stale then read identically, and a
near-total re-stale is the ordinary result of a large merge, so nothing about
the output looks wrong.

The same two runs are also how you size *batching*. What batching two pending
canon changes saves over applying them one at a time is AT LEAST the OVERLAP of
their affected sets — a segment in only one set owes its re-review either way.
Run the loop once per candidate batch and intersect the outputs. (It is only
ever *more* than the overlap when the two changes partly cancel: the hash sees
the FINAL projection, so an entry added and then corrected back re-stales
nothing when the two are merged together.)

**What a dismissal costs.** `compute_used_terms_hash` projects `entries{}`
only, and a dismissal touches none of it — so no translate SEGMENT re-stales,
unlike `correct`/`remove`. That is NOT the same as costing nothing: a
dismissal still writes `review_queue[]` and appends `corrections[]`, so
`canon.json`'s BYTES change, and those bytes are the identical frozen input
named above — `canon_sha256` (the skeptic pass) and `suspicion_scan.py`'s
worklist freshness gate both see it. A dismissal belongs BETWEEN skeptic
passes, exactly like a correction, never into a live one.

stdout for `correct`/`remove`: `{"success":true,"mode":"correct","canon_path":…,
"research_mode":…,"source_form":…,"disposition":…,"entries_count":N,
"review_queue_count":N,"corrections_count":N,"generation_hashes_restamped":false}`.
`dismiss` (#653) reports the same shape PLUS one more field, `"rows_dropped":N`
— the count of `review_queue[]` rows removed (see the `disposition: "dismiss"`
bullet above for why more than one is ordinary) — since it is the one
disposition with nothing else in the payload to say how many rows moved.

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

- **`glossary.enabled: true | false`** (`profile.yml`, default `true`) is the
  PARENT master switch everything else on this page sits under — #727. Set
  to `false`, it skips the research and adjudication this whole reference
  document is about: no `glossary_batch_plan.py`, no `resume_setup.py`, no
  glossary Workflow, no canon merge, and the skeptic pass below is
  suppressed outright (Step 0 fatally refuses a profile that sets
  `glossary.enabled: false` alongside `glossary.skeptic_pass.enabled: true`,
  rather than silently letting the parent switch win). It does NOT skip the
  mandatory language smoke test: W3a's `segpack.py` re-runs
  `bootstrap_names.py`'s own candidate extractor over every segment
  regardless, and W5 acts on the resulting `new_names`, so name detection
  stays load-bearing even against an empty canon. `research_mode` below
  stays REQUIRED and is validated the same either way — it is simply inert
  while `glossary.enabled` is false, since no research ever runs to need it.
  An existing `canon.json` is never discarded: `canon_validate.py --init` is
  create-only, so a project that already has a canon keeps it and keeps
  injecting its entries into every segpack even with the glossary pass
  turned off. Turning it back on later is not free: canonizing a name that
  was previously left to each segment's own `NEW:` rendering changes every
  affected segment's `used_terms_hash`, which makes those already-translated
  segments dispatch-eligible again (`references/ledger-and-resumability.md`).
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

Everything above constrains the SHAPE of a citation, never its truth. The truth check —
what the approved snapshot guarantees, the preconditions it rests on, and the whole
pre-merge review procedure — lives in
[`pre-merge-citation-review.md`](./pre-merge-citation-review.md). Read it at the glossary
pass, when a batch is about to be merged; it binds nowhere else.

#### What the approved snapshot guarantees, and the preconditions it rests on

See [`pre-merge-citation-review.md`](./pre-merge-citation-review.md), which owns this
section in full.

## Citation cache: `canon.json` itself, no new file

`canon.json`'s `entries{}` map is already frozen, hash-versioned, and
cross-segment — a name once resolved there with `basis: "established"` plus a
verified `source` URI stays resolved. "Verified" is load-bearing and now
literal: that URI cleared the pre-merge citation review above before the
merge ever ran, which is the only point at which it could still have been
rejected. Before each glossary pass,
`scripts/glossary_batch_plan.py` (1.3.5) curates `bootstrap_names.py`'s raw
candidate list against the CURRENT `canon.json`, excluding every candidate
already resolved there — an `entries{}` key, a `review_queue[].source_form`,
OR (**#653**) a `source_form` for which ANY `corrections[]` document carries
`disposition: "dismiss"` (see item 3 above for why it is ANY, not the most
recent) — a queued or dismissed name is only re-researched when a human
passes it to `glossary_batch_plan.py --retry`, the documented explicit-request
path, the sole reopening mechanism for either exclusion. Only genuinely new candidates —
never-before-seen names, or an explicitly retried queued or dismissed entry
— are ever sent for fresh research. **Before 1.3.5 this
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

Since **#495** such an edit has a supported spelling: `canon_validate.py
--correct` (above). Everything else here still holds — the merge fatals on a
conflicting re-resolution, `--retry` cannot reinstate a resolved entry, and
`--verify-merged` and `canon_adjudication_audit.py` are both read-only about
the verdict — so a correction remains out-of-band and explicitly adjudicated,
never something a batch can reach. What this section describes is the COST of
correcting a frozen decision: the invalidation is precise, and since **1.25.0**
every segment it reaches is admissible for bounded RE-REVIEW via
`--from-converged`, which cannot reach translate. Before that release the same
edit stranded those units, which is why the older prose here called it
re-translation. It is still a real cost, and still why an accuracy decision is
reviewed BEFORE it is merged rather than after.

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
canon and refuses some malformed rows, but its categories-1-4 gate is
optional and its check is narrower than this one; it is not a substitute.
Only its category-1 **surface-variant** finding runs unconditionally, via
the mandatory pre-W3a invocation that `--advisory` cannot mask (#244), and
that catches a duplicated name surface, not a malformed row. (`--verify-merged`
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
