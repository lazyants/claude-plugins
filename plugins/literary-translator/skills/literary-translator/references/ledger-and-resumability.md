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
  `review_TASK.template.md`, `translate_TASK.template.md`,
  `mass-translate-wf.template.js` — must use this exact path.
  `tests/draft_path_convention.test.py` instantiates every one of these
  against a fixture and asserts the exact path, failing loudly and naming
  the offender if any one disagrees.

- **`review_path(seg) = segments/{seg}.review.json`** — same no-suffix
  reasoning, and the `segments/` prefix is required (matches the real
  reference project exactly — never a top-level
  `${durable_root}/{seg}.review.json`). Readers: `reviewPrompt` (writes it),
  `verifyReviewArtifactPrompt` (reads it), `scripts/review_artifact_check.py`
  (reads it), `scripts/ledger_update.py` (reads it for the
  `reviewed_draft_sha1` binding check). **`fixPrompt` is deliberately not one
  of this file's readers** — it works from the JS-in-memory `revObj`
  directly instead. `tests/draft_path_convention.test.py` is extended (not
  duplicated) to cover these four call sites too.

- **Script self-anchoring.** Every script copied under `scripts/` derives
  its own working root via `Path(__file__).resolve().parents[1]`, since it
  always lives at `${durable_root}/scripts/<name>.py`. A script never
  assumes its cwd equals `durable_root`, and never takes a `--durable-root`
  flag. The `{{DURABLE_ROOT}}` template token (how the calling agent finds
  and invokes the script) and this `Path(__file__)` self-anchoring (how the
  script finds everything else once it's running) are two different halves
  of the same reachability guarantee — do not conflate them. A test invokes
  a representative script from a cwd that is neither `durable_root` nor the
  script's own directory, and asserts it still correctly reads/writes under
  the real `durable_root`.

- **Durable root, never scratchpad.** Step 0a rejects any `durable_root`
  resolving under `/tmp`, `scratchpad`, or similar.

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
materialized `status` to `stale` — it invalidates only that segment, never
the whole book. This exact JSON literal is the authoritative field list;
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
**global** field's current value — used by `extract.py.template` (W2) and
the glossary-pass merge step (W3) to stamp their own `generation_hashes`
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
  the begin marker.
- **`used_terms_hash`** (per-segment) — sha1 of the `canon.json` entries
  actually referenced by this segment's own `canon_names[]` **or**
  `new_names[]` list (from its segpack) that currently exist in
  `canon.json`'s `entries{}`. Includes `new_names[]`, not just
  `canon_names[]` — a name uncanonized at segpack-build time contributes
  nothing either way; the moment it's canonized elsewhere, its bytes enter
  this segment's hash for the first time, correctly flipping it stale. No
  persisted reverse index is needed — this is a live re-check each
  computation.
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
  engine.effort, max_fix_rounds: engine.max_fix_rounds}` from
  `profile.yml`. `batch_agent_cap` is **deliberately excluded** — it's a
  pure orchestration/scheduling knob with zero effect on
  translator/reviewer output semantics; including it would invalidate every
  converged segment on a mere batch-size tweak.
- **`profile_semantics_hash`** (global) — sha1 of canonical JSON
  `{source_lang: source.language.code, target_lang: target.language.code,
  verse_policy_mode: verse_policy.mode, verse_policy_threshold_lines:
  verse_policy.threshold_lines, apparatus_policy:
  footnotes.apparatus_policy, untranslated_sentinel:
  validation.untranslated_sentinel}` from `profile.yml` — exactly these six
  named fields, no more, no fewer. Deliberately does not duplicate
  effort/max_fix_rounds (that's `agent_config_hash`'s job).
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
  (`${durable_root}/extract.py` for `gutenberg_epub`/`plain_text`, or the
  resolved `adapter_config.custom.extractor_path` file). Same "flags, doesn't
  auto-regenerate" honesty as `particle_config_hash`.
- **`source_input_hash`** (global) — sha1 of canonical JSON `{source_path:
  <resolved source.path STRING itself>, source_bytes_sha1: <see below>}`.
  For `gutenberg_epub`/`plain_text`: `source_bytes_sha1` = sha1 of the
  source file's raw bytes. For `custom` (may consume multiple files): the
  extractor must emit `source_inputs: [string]` in `manifest.json` (every
  file path read, in read order); `source_bytes_sha1` = sha1 of canonical
  JSON `[{filename, sha1: <sha1 of THAT file's raw bytes>}]`, one entry per
  file, **sorted by filename**, hashing `{filename, sha1(bytes)}` pairs —
  never bare sorted-and-concatenated bytes (concatenated-bytes-only would
  let a secondary file get silently repointed at a byte-identical different
  file with no hash change — filename must be part of what's hashed, not
  just the sort key). `gutenberg_epub`/`plain_text` also populate
  `source_inputs: [source.path]` for consistency. **Two-phase write**
  (chicken-and-egg: `source_inputs[]` lives inside `manifest.json` but
  `manifest.schema.json` also requires this hash to be present):
  `extract.py.template` first writes a DRAFT `manifest.json`
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
  filename-concatenated bytes of the six generic scripts that directly
  shape translate/review content (`ledger_update.py` included — its
  `reviewed_draft_sha1` binding-check logic directly determines
  correctness) plus the two workflow templates
  (`mass-translate-wf.template.js`/`glossary-pass-wf.template.js`). Never
  `bootstrap_names.py`/`segpack.py` (their own `derivation_bundle_hash`),
  and never the four orchestration-only scripts (covered by the separate,
  non-gating `orchestration_bundle_hash` instead). See the exact membership
  list below.

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
  once per run, not recomputed per segment) — covers exactly **six
  scripts** plus the two workflow templates: `validate_draft.py`,
  `canon_validate.py`, `cache_key.py`, `draft_sha1.py`,
  `review_artifact_check.py`, `ledger_update.py`, plus
  `mass-translate-wf.template.js`/`glossary-pass-wf.template.js`. These are
  scripts that directly shape extraction/translation/review/validation
  content, or determine whether a convergence verdict was correctly
  recorded. **Part of the cache key** (as `plugin_bundle_hash`) — a mismatch
  flips a segment straight to `stale`.
- **`orchestration_bundle_hash`** (global, sibling marker file
  `${durable_root}/runs/.orchestration_bundle_hash`, same computation
  timing) — covers exactly **four scripts**: `draft_ready.py`,
  `ledger_merge.py`, `language_smoke_report.py`, `select_segments.py`.
  **Never added to the cache-key composite, never compared against any
  segment's cache key** — purely diagnostic/provenance, logged in W8's
  reporting ("processed under plugin-bundle X, orchestration-bundle Y").
- **`derivation_bundle_hash`** (part of the 15-field cache_key, see above)
  — covers exactly **two scripts**: `bootstrap_names.py`, `segpack.py`.
  Their bytes do shape content, but they need the derivation-state gate's
  regenerate-before-retranslate treatment (`blocked_needs_regeneration`,
  see below), not either simpler bundle's flip-straight-to-stale/
  never-gates treatment.

`profile_validate.py` is excluded from **all three** bundles — it is never
copied to `durable_root` at all; it's always invoked from the plugin's own
install path.

Both `plugin_bundle_hash` and `orchestration_bundle_hash` are single sha1s
over the concatenated bytes of their member files, sorted by filename for
determinism: scripts/templates for `plugin_bundle_hash`, and scripts for
`orchestration_bundle_hash`, computed by Step 0a at the moment it copies
scripts into `${durable_root}/scripts/`.

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
`segpack_{seg}.json`'s array lengths for a `converged` payload. A malformed
payload is refused (non-zero exit, no write). The scratch payload file is
deleted on success.

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
cache_key?}`. No ledger write happens through any other channel. The prompt
instructs the agent to: (1) write the payload file and run
`ledger_update.py`; (2) **re-read the fragment file `ledger_update.py`
claimed to write, from disk, and compute its sha1 independently — then
compare it against the `fragment_sha1` the script's stdout claimed** (this
closes the gap where a model could echo back a fabricated or stale claim);
(3) only then return the structured response — `success:false` with a
descriptive error if the independent re-read's hash doesn't match.

`LEDGER_WRITE_SCHEMA` (matches `ledger-write-confirmation.schema.json`) is a
real `oneOf`: the success branch requires `{success: true, status: string,
fragment_path: string, fragment_sha1: string}`; the failure branch requires
`{success: false, error: string}` (plus optional `exit_code`/`stderr`) —
must not also require a fragment path/hash that was never written.

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

`LEDGER_MERGE_SCHEMA` (`ledger-merge-confirmation.schema.json`) is a real
`oneOf`: success requires `{success: true, ledger_path: string, n_segments:
integer, missing_segments: array (empty), stale_segments: [string]}`,
`additionalProperties:false`; failure requires `{success: false, error:
string}` (plus optional `missing_segments`/`exit_code`/`stderr`),
`additionalProperties:false`.

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
check.

## The five `recordLedgerPrompt` call sites

All in `mass-translate-wf.template.js`, all through this one
schema-validated call — no ledger write happens any other way.

0. **Translate-dispatch** — right before `agent(translatePrompt(seg), ...)`
   fires: `recordLedgerPrompt(seg, {status:'in_progress'})`, awaited.
   Closes the gap where an interruption between dispatch and any terminal
   write would otherwise leave zero durable record.
1. **Translate-timeout** — on `waitPrompt` returning `TIMEOUT {seg}`:
   `recordLedgerPrompt(seg, {status:'non_converged', reason:'translate-timeout'})`.
2. **Review-null (after one retry) or draft-missing mid-fix** —
   `{status:'blocked', reason:'review-null'}` when the schema-validated
   review call returns null twice (retry once, still null — a deliberate
   plan addition beyond the real reference script, which blocks on the
   first null with no retry); `{status:'blocked', reason:'draft-missing'}`
   when a fix round's `DRAFT_MISSING` branch fires (matches the real
   reference exactly). A third blocked reason, `review-artifact-mismatch`,
   fires after one retry of the review-artifact verification gate also
   reports a mismatch (same retry-once-then-blocked pattern).
3. **Converged** — `recordLedgerPrompt(seg, {status:'converged',
   rounds:<bare integer>, cache_key:{...freshly computed 15 fields...}})`.
   The payload does not include `n_blocks`/`n_footnotes`/`n_verses`
   (`ledger_update.py` derives them). **`reviewed_draft_sha1` binding:**
   `review.schema.json` requires the reviewer's own `draft_sha1` —
   computed by the reviewer before reading the draft (hash-first-then-read
   narrows, but does not eliminate, a TOCTOU window — best-effort
   risk-reduction, not airtight closure). At the converged call site,
   `ledger_update.py` reads this value back off `review_path(seg)`,
   computes a fresh sha1 of the current on-disk draft, and compares:
   **match** → store as `reviewed_draft_sha1` (the hash of what the
   reviewer most likely judged); **mismatch** (draft changed in the window)
   → refuses to write converged at all, returns `{success:false,
   error:"draft changed since review; cannot record convergence"}`, which
   becomes `{seg, converged:false, reason:'ledger-write-failed', detail}` —
   the same escape hatch every other write failure uses.
4. **Non-converged (cap reached)** — terminal, no further automated step:
   `recordLedgerPrompt(seg, {status:'non_converged', reason:'cap',
   rounds: MAXFIX+1})`, `reviewFixLoop()` returns `{converged:false,
   reason:'cap', ...}` — full stop, human-escalation item exactly like
   `blocked`.

## Derivation-state gate — the four "flag-only, needs regeneration" fields

`particle_config_hash`, `source_extraction_hash`, `source_input_hash`,
`derivation_bundle_hash` only **flag** staleness relative to a
config/extraction/source-file/derivation-script change — none of them,
alone, proves the downstream artifacts (`canon.json`/`segpack_{seg}.json`)
actually regenerated. This is closed mechanically: `manifest.json` records
`generation_hashes.source_extraction_hash`/`.source_input_hash` (stamped at
W2 by `extract.py.template`); `canon.json` records
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
re-run W3/W3a for `particle_config_hash`; re-run W3a for
`derivation_bundle_hash`). This is a classification label only (computed by
`select_segments.py`), never written to the ledger fragment's own `status`
— the underlying fragment stays `converged` throughout. No `--only-segs`
override is needed to escape it — it's self-clearing once the operator
actually reruns the regeneration step (segpack naturally re-stamps current
hashes, and the segment reclassifies to ordinary `stale` on the very next
invocation).

For context, `select_segments.py`'s full classification set (see also
`SKILL.md` W5) is: `reusable` (converged, every cache-key field matches,
draft sha1 still matches `reviewed_draft_sha1` — skip), `stale` (converged
but a cache-key field mismatches or the draft sha1 no longer matches —
needs a fresh pass; records which trigger fired in a `stale_reason`
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

## Related tests

`tests/ledger_update.test.py` (fragment-replace transitions — a
`non_converged`→`in_progress` transition asserts no `reason`/`rounds`
survive; a `converged`→`in_progress` transition asserts no
`rounds`/`cache_key`/`n_blocks`/etc. survive; an object-shaped `rounds`
payload is rejected; a payload-intent mismatch is caught),
`tests/ledger_merge.test.py`, `tests/ledger_composite_key.test.py` (one
case per of the 15 hash fields, plus the two asymmetric `used_terms_hash`
cases), `tests/draft_path_convention.test.py`, `tests/select_segments.test.py`
(`--only-segs`/`--allow-empty` cases), and
`tests/ledger_e2e_acceptance.test.py` (the mandatory 7-step mocked-batch
fixture described above) together cover this subsystem. Per the plugin's
own release gate, the plugin is not ship-ready until
`tests/ledger_e2e_acceptance.test.py` **and** a genuine pilot run against a
second real book have both actually run and passed against real data —
CI-green on synthetic fixtures alone is not sufficient.
