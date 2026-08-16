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
- Derivation regen for a mature/zero-candidate project — sanctioned escape since 1.15.0
- The discipline

## Surface 1 — the 15-field `cache_key` composite → MASS RE-TRANSLATION

Editing a member invalidates every converged segment. Members whose BYTES feed it:
- `schema_hash` = `compute_schema_hash`, a sha1 of ONLY **`draft.schema.json` + `review.schema.json` +
  `segpack.schema.json`** (`cache_key.py` ~:351-360). Editing any byte — including a `description` — of
  those three flips it. `manifest.schema.json` / `language-smoke-report.schema.json` are NOT here.
- `plugin_bundle_hash` = the `PLUGIN_BUNDLE_MEMBERS` tuple in `cache_key.py` — **verify against that
  tuple directly, never trust an enumeration copied into a doc**, so neither the members nor a line
  range are restated here. The copy that used to live in this bullet drifted twice — first omitting
  `codex_job.py`/`canon_senses.py` (lazyants/claude-plugins#281), then again as later releases
  appended members — each time pricing an unlisted member's edit as inert when it flips this hash.
- `derivation_bundle_hash` = `DERIVATION_BUNDLE_MEMBERS` = `bootstrap_names.py` + `segpack.py`.

**`source_input_hash` covers the source's ABSOLUTE PATH, not only its bytes — so MOVING a
`durable_root` invalidates every segment while nothing about the book changed.**
`compute_source_input_hash()` returns `sha1(canonical_json({"source_path": <profile
source.path>, "source_bytes_sha1": …}))`, and `source.path` in `profile.yml` is absolute. Verified
2026-07-31 by relocating a root: `select_segments.py` (no `--only-segs`, its default full-set
run — there is no `--all` flag) then reported every segment `blocked_needs_regeneration` with
`pending_fields: ["source_input_hash"]`, remedy W2. Leaving the old path in `profile.yml` instead
is strictly worse — it points at a file that no longer exists.
**So before relocating, renaming, or re-homing a `durable_root`, read `runs/ledger.json` and count
`converged` segments — that count IS the cost**, and it is state no test and no reviewer can see.
Zero converged means the move is free.

**Within surface 1, members do NOT migrate equally.** `plugin_bundle_hash` and `schema_hash` route a
mismatched converged segment to `stale` → **re-translate only**. But
`derivation_bundle_hash ∈ DERIVATION_STATE_FIELDS` (`select_segments.py:186-193`, alongside
`particle_config_hash` / `source_extraction_hash` / `source_input_hash`) → routes to
**`blocked_needs_regeneration`** (`select_segments.py:572`): rerun **W3/W3a only**
(`bootstrap_names.py` → glossary pass → `segpack.py`) FIRST, THEN re-translate — strictly HEAVIER.
`derivation_bundle_hash` and `particle_config_hash` both route through `_w3_regen_step`
(`select_segments.py:195-228`) — **W3/W3a, never W2**; W2 only reruns for `source_extraction_hash` /
`source_input_hash` (`_W2_REGEN_STEP`, `select_segments.py:237`). Verify this membership/remedy mapping
against `select_segments.py` itself before restating it — this exact doc's own remedy text drifted to
an overstated "W2/W3/W3a" once already (codex round-3 finding, 2026-07-23, #282/#283 plan review),
verified wrong against `select_segments.py`'s field→function mapping directly; the enumeration and remedy
text here can drift again and must be re-checked, not trusted at face value.
"Cache-key member = mass re-translation" is the FLOOR; a derivation-bundle edit costs
regen-THEN-retranslate. The hash itself is a raw-byte SHA1 of the concatenated members
(`compute_derivation_bundle_hash`, `cache_key.py:503-506`) — no comment-stripping, so even a comment
reword flips it.

**Batching rule — a `derivation_bundle_hash` flip SUBSUMES a `plugin_bundle_hash` / `schema_hash` flip
(one-directional).** Once `blocked_needs_regeneration` is already forcing full regen + re-translation, a
co-occurring plugin-bundle/schema change adds ZERO marginal migration cost, so a release that already
pays a derivation migration is a free home for any deferred plugin-bundle/schema doc/tech-debt. The
reverse is FALSE: adding a `segpack.py` / `bootstrap_names.py` edit to a plugin-bundle-only release
newly escalates every converged segment from `stale` to `blocked_needs_regeneration` — a real cost
BUMP, not free. (But see the derivation-regen recovery-cost section below before pricing a derivation
migration as cheap — it needs an explicit sanctioned recovery step, not automatic.)

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
`canon.json` **content**: `used_terms_hash` (15-field cache-key field #3, `cache_key.py:577-583`) hashes
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
segpack schemas only, `cache_key.py:353-356`), so sidecar DATA edits are cache-neutral. This is why a
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

## Derivation regen for a mature/zero-candidate project — sanctioned escape since 1.15.0 (#193/#291)

**STALE-CLAIM WARNING:** an earlier version of this doc said `blocked_needs_regeneration` had "no
documented escape hatch" and stayed blocked "forever" for a mature project. That was true only through
1.14.x. **1.15.0 (2026-07-22) shipped a sanctioned fix (CHANGELOG "`--restamp-derivation`, a sanctioned
escape from `blocked_needs_regeneration` (#193)") — do not cite the old "forever/no escape hatch" claim
again.**

The underlying trap is unchanged: the "rerun W3/W3a then re-translate" path is **not reachable via the
glossary pass alone** for a fully-converged project (canon frozen, **zero unresolved glossary
candidates**). The ONLY writer of `canon.json`'s `derivation_bundle_hash` was glossary **MERGE** mode
(`_stamp_write_verify`, `canon_validate.py:1140-1201`; callers `run_merge` / `run_merge_batches` /
`run_init` / `run_restamp_derivation`). The glossary pass **SKIPS entirely** when there are no candidates
(`glossary_batch_plan.py:571-575`, a *tested* supported state, prints `{"no_new_candidates": true,
"batches": []}`) → canon was never restamped → segpack rebuild copies the stale hash **verbatim, never
recomputed** (`segpack.py:472-483`) → `select_segments` stayed `blocked_needs_regeneration`.

**The sanctioned escape: `canon_validate.py --restamp-derivation`, then rerun `segpack.py`.**
`run_restamp_derivation` (`canon_validate.py:1252-1304`) re-records the CURRENT `particle_config_hash` /
`derivation_bundle_hash` onto an existing `canon.json` **without touching its entries**
(`force_restamp=True` into the shared `_stamp_write_verify`). `select_segments.py`'s own
`blocked_needs_regeneration` hint already names it (`_w3_regen_step`, `select_segments.py:195-228`:
"...or, on a project with no new candidates left to merge, canon_validate.py --restamp-derivation --
then segpack.py"). The full blocked → `--restamp-derivation` → `segpack.py` → cleared recovery is pinned
end to end by `tests/derivation_gate_recovery_e2e.test.py` against the real scripts, no stub. Restamping
alone still only reaches ordinary `stale` (re-translate), **never `reusable`** — the underlying file
bytes genuinely changed, so `cache_key_mismatch` stays true even once the derivation-state fields
themselves catch up (`classify_converged_segment`'s fall-through, `select_segments.py:540-587`, esp.
`:583-587`). **Limitation, stated in the CHANGELOG rather than hidden:** `--restamp-derivation` is
operator-trusted — it does NOT itself verify the "no new candidates left to merge" precondition it
documents.

**Superseded workaround — do NOT cite this anymore.** The prior undocumented/unsanctioned trick
("`run_merge_batches` restamps unconditionally → a `--merge-batches <empty-batch>` forces a restamp",
issue #193) is exactly what 1.15.0/#291 **removed**: `_stamp_write_verify` no longer restamps
unconditionally (`canon_validate.py:1157-1174`, "a merge that changes nothing no longer moves
`generation_hashes`"), and `derivation_gate_recovery_e2e.test.py` has a step asserting "the pre-1.15.0
empty-merge bypass no longer works." Cite `--restamp-derivation` only.

**A separate, heavier, NON-sanctioned alternative also exists (verified independently, not a documented
plugin feature):** segment classification is gated on ledger-FRAGMENT PRESENCE, not on hash content.
`_read_fragments()` (`ledger_merge.py:182-205`) returns `{}` when `runs/ledger.d/` is missing, and
otherwise keys each fragment by its filename stem (one file per segment, `runs/ledger.d/{seg}.json`);
`classify_segment()` (`select_segments.py:590-593`) returns `{"category": "not_started"}` whenever a
segment has NO record in the merged ledger at all. Deleting a blocked segment's
`runs/ledger.d/{seg}.json` fragment therefore ALSO clears `blocked_needs_regeneration` — not via any
restamp, but by discarding that segment's entire convergence record, forcing a full FRESH re-translation
(heavier than the `stale` outcome `--restamp-derivation` produces: a `not_started` segment carries no
`reviewed_draft_sha1` baseline at all). Prefer `--restamp-derivation` for a normal recovery; treat
ledger-fragment deletion as a last-resort manual technique, not a sanctioned recovery path.

**CONSEQUENCE (revised): a `segpack.py`/`bootstrap_names.py` edit on a mature/zero-candidate project no
longer bricks it** — it now costs an explicit sanctioned recovery step (`--restamp-derivation` then
`segpack.py`) on top of the re-translation Surface 1 already prices in, not an unrecoverable block. That
recovery step is still a REAL, disclose-worthy cost (an explicit operator action, not automatic), so
"batch the segpack.py edits to pay one derivation migration" is still worth flagging in a CHANGELOG/plan
— just never as a permanent brick anymore (CHANGELOG 1.15.0, #193/#291: "Remedial, not preventative...
this release supplies the way out; it does not prevent the state"). Active first-pass projects are
unaffected either way (always have candidates → merge restamps naturally, no `--restamp-derivation`
needed).

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
