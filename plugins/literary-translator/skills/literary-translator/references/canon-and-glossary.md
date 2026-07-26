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
   `canon_validate.py --research-mode <mode> --restamp-derivation`. That matters for
   a mature, zero-candidate project: it has no candidates left, so the glossary pass
   never runs, so no merge exists to re-stamp — and after a plugin upgrade that
   touches `bootstrap_names.py` or `segpack.py`, segment selection would otherwise
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
before printing `FRAGMENT {index}`; `batchWaitPrompt(batch, attempt)` is Claude,
bounded-poll, `READY`/`TIMEOUT`. Under `research_mode: live` a bounded
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

1.2.0 adds three new modes to close #87 (schema-less glossary dispatch,
`references/orchestration-and-batching.md`), #90 (concurrent-batch races),
and #88 (unverified merge) — routed by `main()` on which flag is given,
alongside the original `--batch PATH` merge path (kept working unchanged;
existing tests exercise it directly):

### `--check-batch PATH [--expect-source-forms-file M.json]` — one fragment, no write

The `batchWaitPrompt`/`batchDispatchPrompt` self-check invocation (see
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
  `research_mode == offline`, naming every offending entry — the same
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

**The reviewer is a plain Claude call, deliberately NOT codex** — no
`agentType`, no schema, sentinel-verdict shaped exactly like the precheck
and wait steps (a schema-bearing call can wedge the Workflow if the
forwarder detaches, #97), at `effort: "high"` rather than those steps'
`"low"`, since this is the one judgment call in the template rather than a
mechanical relay. Codex is what PRODUCED the citation, so a reviewer running
under a different model is a genuinely separate opinion rather than the same
reasoning re-run; `tests/bounded_poll_present.test.py` pins this template's
codex work-call set to exactly `{batchDispatchPrompt}`, which keeps it that
way. This does not loosen R1/R4: the stage AUTHORS nothing and repairs
nothing — its only two powers are approve and reject, every canon resolution
still comes from codex, and a rejection's only effect is to make codex redo
the batch. Its `effort` is likewise NOT wired to `{{EFFORT}}`, which stays
the codex dual-injection knob and nothing else.

Scope is narrow and explicit: only items whose `basis` is exactly
`established` are examined — every other basis makes no external source
claim at all — and for each one the reviewer must actually fetch the URL,
never judge it from its shape, domain reputation, or memory. Three checks:
it RESOLVES (no 404, dead host, parked domain, content-hiding login wall, or
redirect to an unrelated page); it is ABOUT THE RIGHT ENTITY (not merely a
same-named bearer); and it SUPPORTS THE CLAIMED FORM — the page actually
attests the `canonical_target_form` as an established target-language
rendering. That third one is the common failure: a page proving only that
the entity exists, or giving the name only in the source language, does not
support an `established` claim. A missing, empty, non-URL, or
search-results/query `source` rejects too, and so does an unreachable
network — an unverifiable citation is never approved on the grounds that
verification was unavailable. The verdict is **per batch, not per item**: a
single failing item rejects the whole fragment, so there is no partial
verdict to express. A fragment with no `established` items at all passes
trivially — a live-mode batch that happened to resolve everything by
transliteration or sense-translation costs one cheap approval, never a
research round.

**Every attempt gets its own fragment path** — `out_{index}_attempt_{n}.json`
from attempt 0 onward, where this used to be one fixed `out_{index}.json`.
That is not tidiness: the single path made a citation rejection
unenforceable IN PRINCIPLE. A citation-rejected fragment is still perfectly
valid STRUCTURALLY — its URL is present and URI-shaped, which is exactly why
`--check-batch` passed it — so the wait step for the regenerated fragment
would return `READY` against the REJECTED bytes the instant it looked,
whether or not the agent had rewritten anything yet, and those bytes would
sail into the merge. Per-attempt paths make that impossible by construction
rather than by timing: attempt n+1's wait polls a path that does not exist
until the fresh dispatch atomically renames it into place, and the merge is
handed only the exact attempt path the review approved. For the same reason
the verdict sentinels carry the ATTEMPT number, not just the batch index — a
verdict is a statement about one attempt path, so a stale verdict simply
fails to match. A mismatched, malformed, or absent verdict falls to the
REJECT side: a wrong reject costs one regeneration, a wrong accept costs a
permanently frozen fabricated citation.

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

Each of this template's three sites therefore now short-circuits to REJECT when
`rejectedAnywhere(reply, failSentinel)` finds the fail sentinel anywhere in the
reply as a plain substring, evaluated BEFORE `sentinelVerdict()` is consulted
at all. Substring containment is strictly easier to satisfy than line
equality, so the guard can only ADD rejections, never remove one — it moves
the failure into the fail-safe direction by construction, not by care.

The same guard is applied to `mass-translate-wf.template.js`'s translate and
review waits. Its `DRAFT_MISSING` fix check is guarded too, but in the OPPOSITE
direction and through a differently-named wrapper: there `DRAFT_MISSING` is the
OK sentinel, so gluing hides a GENUINE missing-draft report rather than faking a
pass, and `runRound` keys on `mentionedAnywhere()` — same containment test as
`rejectedAnywhere()`, which it delegates to, but a hit biases toward ACTING on
the sentinel instead of rejecting. Six guarded sites over the two templates.
`skeptic-pass-wf.template.js` mirrors this control flow and is deliberately NOT
guarded — it sits in no `cache_key.py` bundle and carries its own
`compute_skeptic_input_digest()`, so editing it would force a fresh skeptic
RUN_ID that this release does not otherwise pay. See the 1.16.0 CHANGELOG entry.

The guard buys its safety with two bounded false REDs, both worth recognizing
in a log:

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
what to read a failed run against. Of the six, only the two IN-BATCH glossary
sites recover inside the run; the three waits and mass-translate's
`DRAFT_MISSING` fix site all cost at least a re-run:

- **Citation review** — the batch regenerates to a fresh attempt and is
  reviewed again, bounded by `MAX_CITATION_RETRIES`. Automatic, same run,
  same batch.
- **Precheck** — `resumed` stays false and the batch falls through to the
  dispatch + wait it would have run had no fragment been on disk. Automatic,
  same run, same batch; the whole cost is the forfeited resume-skip saving,
  one codex dispatch plus one poll.
- **Wait** — NOT automatic, and this is the one that matters. The site returns
  `{ready: false, reason: "glossary-pass-null"}` immediately, straight out of
  `batchStep`; the enclosing attempt loop does not catch it, because this is a
  `return` and not a `continue`. That batch is over for the run, and since the
  merge is all-or-nothing it takes the whole pass with it — `merged: false`,
  `reason: "fragment-check-failed"`, nothing merged at all. Recovery here is
  an operator re-invoking the pass, not the template retrying.
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
disjoint `READY`/`TIMEOUT` set that no `CITATIONS_*` string can collide with.
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
run `canon_validate.py --research-mode <mode> --init` to bootstrap an
empty-but-stamped canon before W3a, or `segpack.py` fatals with
`canon.json not found` (#290). `--init` is create-only: it never re-stamps an
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

## Skeptic pass (RFC #215 Phase 2, opt-in + advisory)

The skeptic pass is an **opt-in, advisory-only** addition (`glossary.skeptic_pass.enabled`, default `false`): a deterministic `suspicion_scan.py` surfaces structurally-risky canon entries (over-merge participants, offline-established entries, singletons, high-dispersion names, citation-only figures, near-spelling pairs, and a globally-capped sample), then a scoped codex pass -- cloning the glossary dispatch control flow, never its identity-decision authority -- is fed bounded, whole-block windows for each flagged entity and adversarially asked to find a contradicting sentence or a genuine homonym split. Its verdict schema (`skeptic-triage.schema.json`) can express only `adverse` / `propose_split` / `propose_rescope` / `insufficient_window` -- there is deliberately no confirmation value, and no freeze/merge reader ever opens the resulting `skeptic_triage.json`. Every actual confirmation still flows through the unchanged human/codex `canon_adjudications.json` / `canon_senses.json` paths. `skeptic_report.py` is a separate, read-only advisory command that renders `skeptic_triage.json` for a human reviewer (per-entity risk context, the verdict, a quote derived fresh from the stored offsets, and evidence coverage) -- it is not a gate, it never blocks, and it runs strictly after `canon_adjudication_audit.py`, which is unchanged byte-for-byte by the skeptic pass's presence (see `tests/audit_unchanged_regression.test.py`).

Two scoping limits carry through to this reporting layer. First, **verse evidence stays block-only**: `evidence_verify` (and therefore any skeptic citation) can only authenticate an offset against `manifest.blocks{}`, never `verse.store[]` -- a citation whose window is an embedded-verse node is coerced to `insufficient_window` upstream, so `skeptic_report.py` never needs to (and cannot) derive a quote from verse text. Second, **`all_citation` is adapter-safe**: for `source.format` values with no configured citation-block-type set (i.e. anything other than `gutenberg_epub`/`plain_text` -- any `custom` adapter), the risk class is disabled fail-safe rather than guessed from tag spelling, annotated `citation_classification_unavailable` in the worklist; this never blocks the skeptic pass itself, it only means that one risk signal is honestly reported as unavailable for that project's format.
