# literary-translator — which hash surface does this file feed? (migration-impact map)

**Open this before editing ANY `literary-translator` schema or script — even a one-line `description`
annotation.** "It's just a docs/description edit, zero migration" is the trap; codex has out-found that
claim more than once in a single plan-review loop. Five separate hash surfaces exist, with very
different blast radii. Verify membership against the source (`cache_key.py`, `resume_setup.py`,
`diff_rendered_output.py`, `select_segments.py`), never assume.

Table of contents:
- Surface 1 — cache_key composite → mass re-translation
- Surface 2 — resume digest → fresh-resume
- Surface 3 — render_version → render-baseline re-accept
- Surface 4 — migration-inert
- Surface 5 — canon.json DATA → used_terms_hash → use a sidecar
- schema_hash is NOT regeneration-gated
- Derivation regen DEAD-ENDS for mature/zero-candidate projects
- The discipline

## Surface 1 — the 15-field `cache_key` composite → MASS RE-TRANSLATION

Editing a member invalidates every converged segment. Members whose BYTES feed it:
- `schema_hash` = `compute_schema_hash`, a sha1 of ONLY **`draft.schema.json` + `review.schema.json` +
  `segpack.schema.json`** (`cache_key.py` ~:339-347). Editing any byte — including a `description` — of
  those three flips it. `manifest.schema.json` / `language-smoke-report.schema.json` are NOT here.
- `plugin_bundle_hash` = the `PLUGIN_BUNDLE_MEMBERS` tuple (`cache_key.py` ~:93-105):
  `validate_draft.py, canon_validate.py, cache_key.py, draft_sha1.py, review_artifact_check.py,
  ledger_update.py, review_ready.py, resume_setup.py, glossary_batch_plan.py`,
  `mass-translate-wf.template.js`, `glossary-pass-wf.template.js`.
- `derivation_bundle_hash` = `DERIVATION_BUNDLE_MEMBERS` = `bootstrap_names.py` + `segpack.py`.

**Within surface 1, members do NOT migrate equally.** `plugin_bundle_hash` and `schema_hash` route a
mismatched converged segment to `stale` → **re-translate only**. But
`derivation_bundle_hash ∈ DERIVATION_STATE_FIELDS` (`select_segments.py:186-193`, alongside
`particle_config_hash` / `source_extraction_hash` / `source_input_hash`) → routes to
**`blocked_needs_regeneration`** (`select_segments.py:527`): rerun **W2/W3/W3a**
(`bootstrap_names.py` → glossary pass → `segpack.py`) FIRST, THEN re-translate — strictly HEAVIER.
"Cache-key member = mass re-translation" is the FLOOR; a derivation-bundle edit costs
regen-THEN-retranslate. The hash itself is a raw-byte SHA1 of the concatenated members
(`compute_derivation_bundle_hash`, `cache_key.py:487-490`) — no comment-stripping, so even a comment
reword flips it.

**Batching rule — a `derivation_bundle_hash` flip SUBSUMES a `plugin_bundle_hash` / `schema_hash` flip
(one-directional).** Once `blocked_needs_regeneration` is already forcing full regen + re-translation, a
co-occurring plugin-bundle/schema change adds ZERO marginal migration cost, so a release that already
pays a derivation migration is a free home for any deferred plugin-bundle/schema doc/tech-debt. The
reverse is FALSE: adding a `segpack.py` / `bootstrap_names.py` edit to a plugin-bundle-only release
newly escalates every converged segment from `stale` to `blocked_needs_regeneration` — a real cost
BUMP, not free. (But see the DEAD-END section below before pricing a derivation migration as
payable at all.)

## Surface 2 — the RESUME digest → an INTERRUPTED run starts FRESH on upgrade

NOT re-translation — converged segments stay reusable via surface 1; only in-flight/unmerged work
redoes. `resume_setup.py` folds in:
- `_schemas_dir_hash` = sha256 of **EVERY `*.schema.json`** in `schemas/` (`resume_setup.py`
  ~:207-216) — so `manifest.schema.json` and `language-smoke-report.schema.json` DO matter here even
  though they miss surface 1.
- `.orchestration_bundle_hash` — covers the orchestration-only scripts (`select_segments.py`,
  `language_smoke_report.py`, `draft_ready.py`, …). SKILL.md / `ledger-and-resumability.md` call this
  "non-gating/provenance-only" — TRUE for convergence, MISLEADING for resume (it gates resume).
- Mass vs glossary restart differ: mass reuses converged segments (cheap); an interrupted glossary pass
  abandons RUN_ID-scoped unmerged fragments (re-dispatches its batches).
- Separately, `language_smoke_report.py` bytes flip `smoke_report_contract_hash` → forces the W3
  language smoke test to re-run.

## Surface 3 — the `render_version` render-baseline stamp → a RENDER-BASELINE RE-ACCEPT

NOT re-translation, NOT fresh-resume — localized to the Obsidian render/diff gate. `render_version` =
sha of the `_RENDER_VERSION_FILES` tuple = **`render_obsidian.py` + `diff_rendered_output.py`**
(`diff_rendered_output.py:106`, hashed `:247`, stale-check `:474`). Editing either flips it. On the next
`assemble.py` run the render diff-gate writes a fresh candidate and reports a **mismatch** against the
frozen last-accepted baseline for any verse whose REDUCED markdown changed — the gate NEVER re-renders
live, and the compare is on reduced md lines (CRLF/CR-normalized, rstripped, trailing-blank-dropped),
NOT bytes (`:118-121`). A content mismatch returns BEFORE the stale-version check; re-accept a
replacement baseline via `--accept-baseline --force-accept-baseline` (`:445-452`). Both files are in
NEITHER cache_key NOR the resume digest → a genuinely separate surface.

## Surface 4 — migration-INERT

Not a schema, not a bundle member, never copied to durable_root: `profile_validate.py`,
`validate_extraction.py` (run only from the plugin path). Editing them touches neither the cache key nor
the resume digest.

## Surface 5 — editing canon.json DATA (not a file's bytes): `used_terms_hash`

The four surfaces above are about editing a SCHEMA or SCRIPT **file**. A distinct surface is editing
`canon.json` **content**: `used_terms_hash` (15-field cache-key field #3, `cache_key.py:562-567`) hashes
the **WHOLE referenced canon ENTRY object** (`{name: entries[name]}` for every name a segment
references, via `canonical_json_bytes`). So **adding ANY field to a canon `entries{}` record
re-translates every converged segment that references that `source_form`** — even a purely descriptive
field the translator ignores; blast radius = exactly the referencing segments
(`ledger_composite_key.test.py` `test_used_terms_hash_exact_scope` proves an UNreferenced entry moves
nothing).

**Corollary (the reusable design rule): to enrich canon with adjudication/annotation data WITHOUT
re-translating, put it in a SIDE-STORE keyed by `source_form`** — a sibling file
(`canon_adjudications.json` is the existing precedent, or a new `canon_senses.json`) — **NEVER in the
`entries{}` body.** No sidecar file is among the 15 cache-key fields (`schema_hash` = draft/review/
segpack schemas only, `cache_key.py:342`), so sidecar DATA edits are cache-neutral. This is why a
homonym-split / evidence design stores `senses[]` / evidence in a sidecar, not in canon entries.
Belt-and-braces: `CANON_ENTRY_FIELDS` (`canon_validate.py:170-179`) is the projection
`_entry_from_accepted_item` writes, so a stray field on an accepted batch item is silently STRIPPED —
you cannot accidentally leak entry-body data in via a merge. Note the code fix that READS the sidecar is
still a bundle-member edit → surface-1 upgrade re-translation; "cache-neutral" is about the DATA
enrichment, never the code change.

## `schema_hash` is NOT regeneration-gated; a schema-description edit is free-on-top

Only particle / extraction / input / **derivation** hash mismatches trigger
`blocked_needs_regeneration` (`select_segments.py:181`); a `schema_hash`-only mismatch is ordinary
`stale` (`select_segments.py:539`). So editing a cache-key schema's **description** on top of a release
that ALREADY flips `plugin_bundle_hash` is **zero marginal workload** — the 15-field composite
`cache_key` is already invalidated, so it's the same single re-translation, not a second one. Corollary:
a schema-description fix rides FREE on any already-cache-key-flipping release (and a
`derivation_bundle_hash` release re-translates downstream anyway, so a schema edit is free-on-top there
too). BUT **"migration-free" ≠ "review-free":** a schema-description reword can still be a
producer-attribution REWRITE (needs a source-neutral + positive-attribution test), which is its own
review surface — weigh that, not just the hash cost, when deciding to fold it in. Also:
`derivation_bundle_hash` regeneration is **W3/W3a** (NOT W2 — W2 is source-extraction/input-hash only).

## Derivation regen DEAD-ENDS for a mature/zero-candidate project → segpack.py/bootstrap_names.py edits are BLOCKED

The "rerun W3/W3a then re-translate" path above is **NOT actually reachable** for a fully-converged
project (canon frozen, **zero unresolved glossary candidates**). The ONLY writer of `canon.json`'s
`derivation_bundle_hash` is glossary **MERGE** mode (`canon_validate.py:826` `_stamp_write_verify`,
callers `run_merge` / `run_merge_batches` only; `run_validate_only:998` doesn't write). The glossary
pass **SKIPS entirely** when there are no candidates (`glossary_batch_plan.py:496`, a *tested* supported
state) → canon is never restamped → segpack rebuild copies the stale hash verbatim (`segpack.py:437`) →
`select_segments` stays `blocked_needs_regeneration` **forever** (`:522`). There is **no documented
escape hatch** ("never a manual-override target", `select_segments.py:635` / `SKILL.md:661`).

**CONSEQUENCE: do NOT plan a "batch the segpack.py edits to pay one derivation migration" release** —
that migration BRICKS mature projects. The earlier "pay the derivation migration once" batching framing
assumes the path works; it does NOT for mature projects (canon frozen, no candidates). Active first-pass
projects are unaffected (always have candidates → merge restamps naturally). Undocumented/unsanctioned
workaround: `run_merge_batches` restamps unconditionally (`canon_validate.py:920`) → a
`--merge-batches <empty-batch>` forces a restamp. A `segpack.py`-touching change therefore stays blocked
until a sanctioned restamp / force-accept path exists.

## The discipline

Before writing "zero migration" in a plan/CHANGELOG for a littrans edit, check the file against ALL
FOUR file-surfaces (surface 5 applies to canon DATA edits):
- (a) is it one of the 3 `compute_schema_hash` schemas or a `PLUGIN_BUNDLE_MEMBERS` /
  `DERIVATION_BUNDLE_MEMBERS` script? → cache-key → re-translation.
- (b) is it ANY `*.schema.json` or an orchestration-bundle script? → resume digest → fresh-resume.
- (c) is it `render_obsidian.py` or `diff_rendered_output.py`? → `render_version` → render-baseline
  re-accept (`--force-accept-baseline`).
- (d) none? → inert.

Disclose (a), (b), and (c) in the CHANGELOG; only (d) is truly free. A false-for-custom claim living in
a surface-1 schema (e.g. `segpack.schema.json`'s `extract.py.template` attributions) is therefore a
DEFERRED follow-up, NOT a "free doc fix" — fixing it needs a `schema_hash` migration = full
re-translation. (This file is the "what does editing a hashed file COST" map; the schema-gate-hardening
skill is the complementary gate-AUTHORING-traps map.)
