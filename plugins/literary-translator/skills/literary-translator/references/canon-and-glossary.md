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

Per batch: `batchDispatchPrompt(batch)` is codex, `agentType:'codex:codex-rescue'`,
`effort:'high'`, **schema-less**, fire-and-forget — it writes the run-scoped
fragment `${durable_root}/glossary/runs/{{RUN_ID}}/out_{index}.json`
atomically and self-validates it via `canon_validate.py --check-batch`
before printing `FRAGMENT {index}`; `batchWaitPrompt(batch)` is Claude,
bounded-poll, `READY`/`TIMEOUT`. Two final calls run once, after every
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

## Citation cache: `canon.json` itself, no new file

`canon.json`'s `entries{}` map is already frozen, hash-versioned, and
cross-segment — a name once resolved there with `basis: "established"` plus a
verified `source` URI stays resolved. Before each glossary pass,
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
`batches` list, which is why the marker exists).

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
