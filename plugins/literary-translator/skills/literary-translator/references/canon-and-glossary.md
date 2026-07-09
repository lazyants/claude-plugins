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
   check. It reads `PARTICLES`, `STOPWORDS`, `ELISION_RE`, and `has_elision` from
   `${durable_root}/languages/<particle_config's LITERAL value>` — never by
   reconstructing a filename from `source.language.code`, since that would ignore
   a project-local override such as `fr.local.json`. This script only surfaces
   candidates; it never decides a translation. It is source-language-parameterized
   (see `references/language-pair-parameterization.md`) via the profile's
   `source.language.particle_config`, never hardcoded to one language.
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

The glossary-pass gets **identical schema-validated-workflow-level-call discipline
as review** — canon/realia decisions are exactly as accuracy-load-bearing as
review findings. Every glossary-pass batch call MUST be:

```
agent(glossaryPrompt(batch), {agentType:'codex:codex-rescue', effort:'high', schema: CANON_BATCH_SCHEMA})
```

dispatched from `glossary-pass-wf.template.js` — **never** a raw/ad hoc
`Agent(subagent_type: "codex:codex-rescue")` call, and never nested inside another
agent's own turn. A schema-less call — even top-level, even non-nested — can return
an ambiguous background-job string instead of a real batch result; only a
schema-validated Workflow-tool `agent()` call has automatic retry-until-valid.
`CANON_BATCH_SCHEMA` matches the shipped `canon-batch.schema.json` exactly (see
below) — it is a discriminated union over each item's own `disposition` field, not
a bare array of `canon-entry.schema.json` shapes.

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

`basis ∈ {established, transliterated, title, not_a_name}`.
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

Note the field-name generalization from the source project: the proven
`historiettes-t3` reference used French/Russian-specific field names (`fr`,
`canonical_ru`); the plugin generalizes these to `source_form` and
`canonical_target_form` so the same schema works for any language pair.

### `canon-batch.schema.json` — the glossary-pass batch's real return contract

```
{ type: "array", items: { oneOf: [ACCEPTED, QUEUED] } }
```

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

## `canon_validate.py`'s two validation passes

`scripts/canon_validate.py` is the plugin-owned backstop for schema enforcement —
never trust the Workflow-level `agent(..., {schema: ...})` call's own
format-assertion behavior alone; that platform-owned enforcement is a separate
trust boundary the plugin cannot configure or guarantee.

**Two CLI modes, selected by whether `--batch PATH` is given** (both require
`--research-mode live|offline`, never defaulted, even in the mode where it has
no effect — so no call site can accidentally omit declaring the precondition):

- **MERGE mode** (`--batch PATH` given) — merges a glossary-pass batch result
  into `canon.json`, running Pass 1 + the offline backstop + the dedup/collision
  merge + `generation_hashes` stamping + the atomic write + Pass 2, exactly as
  described below.
- **VALIDATE-ONLY mode** (`--batch` omitted) — a read-only health check against
  the CURRENT, already-frozen `canon.json`: no merge, no write, and no offline
  `basis:"established"` backstop (that backstop only ever applies to NEW entries
  in an incoming `--batch`; an already-frozen `canon.json` is not retroactively
  re-litigated just because this run happens to pass `--research-mode offline`
  for other reasons). Pass 1 instead validates every EXISTING `entries{}` value
  against `canon-entry.schema.json` directly, and every existing `review_queue[]`
  item against the QUEUED shape; Pass 2 is unchanged — the loaded document is
  validated against `canon-file.schema.json`.
- **`--canon-path PATH`** (either mode, optional) overrides the default
  `${durable_root}/canon.json` location — e.g. for a one-off health check
  against a specific file, or a test fixture. Omitted → the default is used.
- Both modes print exactly one JSON line to stdout and exit 0 on success / 1 on
  failure — callers should read stdout, not rely on the exit code alone. The
  success payload always carries `mode` (`"merge"` or `"validate"`),
  `canon_path`, `research_mode`, `entries_count`, `review_queue_count`; MERGE
  mode additionally reports `batch_items`, `merged_accepted`, `merged_queued`. A
  failure payload carries `error` and, when the failure names specific items,
  `offending` (an array of strings).

- **Dependency preflight first**: wraps `import jsonschema` in a try/except,
  printing a clear "install with `pip install -r requirements.txt`"
  message and exiting non-zero on `ImportError` — never a raw traceback.
- **Pass 1 — per-item.** Constructs a validator over `canon-batch.schema.json`'s
  item shape with
  `jsonschema.Draft202012Validator(..., format_checker=jsonschema.FormatChecker())`
  explicitly (`format_checker` is
  REQUIRED — `jsonschema`'s own convenience `validate()` does not enable format
  assertions by default). In MERGE mode, re-validates every batch item
  independently against that discriminated-union item shape — not the bare
  `canon-entry.schema.json` shape, since a batch item also carries the
  `disposition` field that shape doesn't have — right after the glossary-pass
  merge step and before routing into `entries{}`/`review_queue`. In
  VALIDATE-ONLY mode, validates every EXISTING `entries{}` value against the
  bare `canon-entry.schema.json` shape instead (no `disposition` field there),
  and every existing `review_queue[]` item against the same QUEUED shape.
- **Pass 2 — whole-file.** In MERGE mode, immediately after the merge step
  writes the updated `canon.json` to disk, re-reads the WHOLE file and
  validates it against `canon-file.schema.json`. In VALIDATE-ONLY mode, the
  already-loaded document is validated against the same schema directly (no
  write happened). Either way, this pass fatally halts, naming the specific
  problem, if `entries{}` / `review_queue` / `generation_hashes.particle_config_hash`
  / `generation_hashes.derivation_bundle_hash` are missing or malformed — a
  genuinely incomplete `canon.json` (e.g. one missing `entries` or
  `review_queue` entirely) must fail loudly here, never be silently patched up
  with empty defaults before this check runs. This is the check that actually
  enforces the two `generation_hashes` fields' presence, which
  `select_segments.py`'s derivation-state gate is entirely load-bearing on.
- Reads `canon-entry.schema.json` / `canon-batch.schema.json` /
  `canon-file.schema.json` from `${durable_root}/schemas/` — never the plugin's
  own `assets/schemas/`.

Both passes are schema-driven validation, not free-text judgment — this is the
same "independent re-check, don't trust the agent's own self-report" discipline
applied everywhere else load-bearing in this plugin (e.g. the ledger's disk
re-read after `recordLedgerPrompt`).

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
  either `basis: "transliterated"` (the existing fixed practical-transcription
  rule, if mechanical transliteration is adequate) or routed into `review_queue`
  (if the name is genuinely disputed and needs a human's real research later) —
  never left with a fabricated citation, and never silently forced into
  `established` anyway. Either way, the entry's `note` carries the literal prefix
  `SOURCE_UNAVAILABLE:` — mirroring the `NEW:` note-prefix convention used for
  `new_names[]` below — so a human reviewing `canon.json`/`review_queue` later can
  find every entry that still needs real research once it becomes available.
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
verified `source` URI stays resolved. The candidate list `bootstrap_names.py` hands
to the glossary-pass batches MUST exclude every `source_form` already present in
the CURRENT `canon.json`'s `entries{}` map before dispatch — only genuinely new
candidates (never-before-seen names, or a `review_queue` entry a human has
explicitly asked to be re-resolved) are ever sent for fresh research. Without this,
every glossary-pass re-run (a second book sharing recurring historical names, or
simply re-running the mass-translate step after an interruption) would
re-research already-settled names, wasting research effort and risking a
genuinely different citation surfacing on a later run for a name the canon had
already frozen.

## `segpack.py`'s canon injection contract

Every per-segment pack gets:

- **`canon_names[]`** — locked forms the translator MUST use verbatim.
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
