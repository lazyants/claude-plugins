# Changelog

## 1.10.0 — 2026-07-18

Two coordinated tracks landed together: (1) renderer/gate hardening for the source-anchored `## Mentions` appendix, flipping `output.adapter_config.obsidian.mentions_section.enabled` from opt-in (default false, 1.8.0–1.9.x) to **ON BY DEFAULT** for `output.target: obsidian` — the opt-in design existed only to protect legacy projects, and none exist; and (2) Hebrew mark/connector-insensitive `name_inventory` matching for the appendix, plus three scaffold/robustness fixes on the extractor path. Closes #240 (both halves), #238, #241, #236, #226, #205, #192, #190.

### Changed — matching (#238, #241, #226, #190, #192)

- **#238** — `bootstrap_names.py`/`language_smoke_report.py`'s `name_inventory` caseless matching route is now Hebrew niqqud/cantillation-INSENSITIVE: an unpointed inventory entry matches a pointed source occurrence and vice versa. The fold applies only to the MATCH (trie descent + `occurrence_targets.py`'s lookup key); the candidate that gets recorded, and everything downstream of it (`name_candidates.json`, the glossary pass, `canon.json`'s own key), stays the exact raw surface form as the source spells it.
- **#241** — the same route is now connector-insensitive for maqaf (U+05BE), geresh (U+05F3), and gershayim (U+05F4): `משה לייב` and `משה־לייב` are treated as the same name for matching purposes. Deliberately NOT extended to the apostrophe/hyphen connectors Latin names also use (`Jean-Baptiste`, `O'Brien` stay exact-spelled).
- New exported helper `bootstrap_names.fold_match_key()` (mirrored independently in `language_smoke_report.py`, per this train's no-shared-import convention for the two extractors) — the single #238/#241 match-key construction, applied identically on both the matcher's grouping side and `occurrence_targets.py`'s canon-lookup side.
- Two colliding `name_inventory` entries that fold to the same match key (e.g. a project accidentally lists both a space-joined and a maqaf-joined spelling of the same name) now WARN to stderr at config-load time rather than being silently redundant — never a fatal; both entries keep matching identically.
- Symmetrically, when two distinct **canon** `entries` have `source_form`s that fold to the same match key, `occurrence_targets.build()` routes their occurrences to `unresolved_homonyms` (reason `fold_match_key_collision`; crediting neither entry's `## Mentions` section until the operator disambiguates) and warns to stderr — never double-filing the same physical occurrence under both entries. This collision check takes precedence over the existing `is_split` homonym route.
- **Known, deliberate residual (A-C6):** `occ_index.production_occurrences()` (and therefore `evidence_verify.py`/`suspicion_scan.py`/`canon_adjudication_audit.py`) remain mark/connector-EXACT after this release — they were not folded this train (a scoped follow-up issue is filed). The `## Mentions` appendix (`occurrence_targets.py`) is fixed; the evidence/adjudication chain is not, yet.
- **#226** — `segpack.py` no longer pre-collapses a multi-character `⟦FNREF_N⟧`/`⟦VERSE_…⟧` sentinel to a single space before scanning for proper-noun candidates. `bootstrap_names.extract_candidates()`'s own internal masking already does this length-preservingly; the extra pre-pass was redundant and the one place in this script that could have corrupted a future span-based caller's offsets. Verified empirically byte-neutral for the candidate name/freq output on a representative multi-sentinel passage (14/14 candidates, identical rows, before vs. after).
- **#190** — the two remaining `extract.py.template` mentions in `segpack.py`'s own source comments are now scoped to `gutenberg_epub`, the only adapter that actually ships that extractor.
- **#192** — `segpack.py._verse_line_count()`'s LEGACY (pre-#92 manifest) fallback now splits on LF only (`_split_lf_lines`, a local, duplicated copy — not imported — mirroring `validate_draft.py`'s own precedent), never `str.splitlines()`, which also breaks on U+2028/U+2029/U+0085/U+000B/U+000C/U+001C–U+001E — a real `plain_text` may legitimately carry a U+2028 verse-payload join that is not a source line break. `segpack.schema.json`'s `n_line` description reworded to match (and to name `segpack.py` as the actual fallback producer).

### Changed — appendix renderer & gate (#240, #236, #205)

- **#240 — collision tally now counts `sense_translated` owners.** `render_obsidian.build_entity_index` previously excluded a `basis: sense_translated` entry from the owner tally BEFORE counting collisions, so a sense_translated entry sharing a `canonical_target_form` with a narrative entry never registered as a real collision — the narrative entry silently won the inline-link tiebreak as if uncontested. The exclusion now applies only at tiebreak-selection time, AFTER the tally: a sense_translated owner still never wins an inline link or survives as the sole owner of an all-sense_translated target, but it now correctly contributes to `collision_delink`'s >=2-owner de-link decision.
- **#240 — the gate's collision report gains `collisions[].renderer_delinked: bool`.** `validate_backlinks.py` and the renderer have always disagreed on what a "collision" is (the gate groups by `canon_senses.normalize_form` — NFC + casefold + whitespace-collapse, no basis filter; the renderer groups by NFC only, case-sensitively, excluding `sense_translated` from winning). Rather than unifying the two definitions, the gate now calls `render_obsidian.build_entity_index` directly (twice) and reports, per collision, whether the renderer actually de-links that target under `collision_delink=True` — surfacing the disagreement to the operator instead of hiding it. Exit-neutral: `warnings` is unaffected (still `len(missing)` only) — a diagnostic addition, not a stricter gate.
- **#236 — malformed-nodestream shapes are now a clean exit 2, not an uncaught exit-1 traceback (or a silently wrong answer).** A non-dict `book`, a non-list-of-strings `book.seg_order` (previously iterated CHARACTER-BY-CHARACTER if a bare string, or crashed with an `AttributeError` if it held non-string elements), a non-list `nodes`, or a present-but-non-object canon `entries` (previously silently treated as zero entities, exit 0) are all now a named, reason-carrying exit 2. Exit 1 was advisory (W9 would silently continue past a crashed gate) — these are genuine structural defects, not coverage misses.
- **#236 — marker-region parsing is now fence-aware and inline-code-aware.** A `<!-- lt:mentions:begin/end -->` marker pair (or a `[[wikilink]]`) living inside a ` ``` `/`~~~` fenced code block, or a backtick-quoted `` `[[wikilink]]` ``, no longer counts as a real region/link. **This is a two-way change on hand-edited vaults**: a forged fenced example stops satisfying coverage (`warnings` can go UP), and a real region that happens to sit alongside an unrelated fenced example stops being falsely rejected (`warnings` can go DOWN). Never affects a normally rendered vault (`render_obsidian.py` never emits fenced markers).
- **#205 — the `duplicate_source_form` category's scope is now stated honestly.** `canon_adjudication_audit.py`'s category-1 check structurally can only ever detect a NORMALIZATION-VARIANT duplicate `source_form` (e.g. case/whitespace differences), never a byte-identical one — `canon_validate.py`'s own map-key-equals-source_form write pattern makes a true identical-surface duplicate impossible to persist in the first place. `--check` now emits an unconditional warning stating this scope limit on every run where canon is present. No schema change, no operator migration, `gate_passed` semantics unchanged (docstring + warning only — Option A; a stronger risk-acceptance-gated Option B needs owner ratification and is not built here).

### Default-on flip — `mentions_section.enabled`

- **All three independent predicate copies** — `render_obsidian.py`'s, `assemble.py`'s, and `validate_backlinks.py`'s own `_effective_mentions_enabled`/`_effective_enabled` — flip atomically in one change, `... .get("enabled") is True` → `... .get("enabled") is not False`. An absent `mentions_section` block or an absent `enabled` key both now resolve to **enabled**; only an explicit `enabled: false` opts out. `enabled` must be a boolean when present — a literal `enabled: null` is rejected by `profile_validate` against `profile.schema.json` (`type: boolean`), so it is not a supported way to request the default (omit the key instead); the predicates' `is not False` handling of a stray `None` is defensive only. The `output.target != "obsidian"` short-circuit is unchanged in all three.
- `assets/profile.example.yml` gains an explicit `mentions_section: {enabled: true}` block (self-documenting; the feature would activate by absence either way).
- `assets/schemas/profile.schema.json`'s `"default"` annotation is updated to `true` for documentation honesty ONLY — there is no defaults-filling machinery anywhere in this repo; the annotation was never the mechanism and still isn't.
- **§O2a — `assemble.py`'s three new `## Mentions` preconditions (dependency import, language-config resolution, canon_senses load) fail closed unconditionally.** A broken Mentions dependency raises (halts assembly) whether the flag is explicit or merely implied by the default-on flip — matching `validate_backlinks.py`, the last W9 step, which likewise hard-halts (exit 2) on the same broken dependency. (An implied-vs-explicit graceful-skip posture was drafted and removed within this same release — never shipped — because it did not hold end to end: assembly would skip the appendix but the pipeline still halted one step later at `validate_backlinks.py`.)
- **§O2b — `assemble.py`'s `occurrence_targets.build()` call is now wrapped** in a reason-carrying `AssembleError` (`reason: mentions_occurrence_targets_failed`) instead of surfacing as the generic "unexpected error" exit 1 with no `reason` field. Always fail-closed (a build() crash is a genuine engine defect).

### Migration

No delivered or in-flight project is affected: the two French books are frozen — never re-run, re-rendered, or re-scaffolded on this or newer code — and the Hebrew re-run starts from a clean scaffold AFTER this merges. The hash mechanics for any future live project:

- **`render_version` flips** (`render_obsidian.py` bytes changed via the #240 fix + the predicate flip) — a project holding an accepted `diff_rendered_output.py` `.baseline` needs exactly one operator `--accept-baseline` re-accept on its next W9 run.
- **`derivation_bundle_hash` flips** (`bootstrap_names.py` + `segpack.py`) and **`smoke_report_contract_hash` flips** (`language_smoke_report.py`) — a resumed, not-yet-converged project's derivation stage reclassifies and its W3 language smoke test must be re-run. (For a zero-candidate project the documented `select_segments.py` regen remedy does not clear a `blocked_needs_regeneration` state; the escape is deleting `runs/ledger.d/*.json` and re-running from that point.)
- **`schema_hash` flips** (`segpack.schema.json`'s `n_line` description reworded). `schema_hash` is one of the 15 fields of each segment's composite cache key (`CACHE_KEY_FIELD_ORDER`), so a project that re-scaffolds its schema copy onto this release re-derives its converged segments' cache keys — i.e. this is NOT unconditionally "zero re-translation", it is "zero *affected projects*". No live project re-scaffolds mid-run here.
- **`plugin_bundle_hash` and `profile_semantics_hash` are unchanged** — nothing here touches `PLUGIN_BUNDLE_MEMBERS` or the `profile_semantics_hash` allowlist, so no *already-converged* segment on an unchanged schema copy is re-translated.
- **Behavioral, not just additive, for any NEW `output.target: obsidian` project from this version on:** `## Mentions` sections appear by default, AND collision de-linking engages by default (a shared `canonical_target_form`'s old tiebreak-winner inline link disappears; both/all owners are de-linked instead — a subtractive change to narrative prose, not merely an added appendix). Because the appendix is default-on, an `obsidian` project whose Mentions dependency chain (`bootstrap_names`/`canon_senses`/`occurrence_targets` under `durable_root/scripts/`, a resolvable `particle_config`, a loadable `canon_senses.json`) is broken or unprovisioned now **fails closed at W9** (assembly halts, exit 2) rather than silently producing no appendix. A hand-written profile with no `mentions_section` block at all now gets both the appendix and this fail-closed posture; write `enabled: false` explicitly to keep the pre-1.10.0 shape (no appendix, no dependency requirement).
- **Hebrew re-run:** must start only AFTER this release merges (the `derivation_bundle_hash` flip). The project-side `name_inventory` prerequisite it needs is data, not a plugin change, and is intentionally not filed as a public issue — see `references/language-pair-parameterization.md`'s new worked example.

## 1.9.0 — 2026-07-18

Hebrew / uncased-script enablement plus two robustness gaps closed on the scaffold and delivery paths. Ships an offset-safe mark-inclusive tokenizer, the `he.json` starter preset, a niqqud-aware foreign-remainder fold, the Step 0a bundle-hash marker writer, and an enforced heading-shape output contract. Closes #225, #195, #209, #194, #201.

### Added

- **#195 — `he.json` Hebrew starter preset.** Uncased script (category `Lo`): `PARTICLES: []` by design (the `Lu`-gated Pass-1 capitalization run never fires on Hebrew, so a particle list would be inert), `has_elision: false`, `ELISION_RE: null`; `STOPWORDS` is a curated 40-word list of standalone whitespace-delimited Hebrew function words (never single-letter proclitics ה/ב/כ/ל/מ/ש/ו, which fuse onto the next word and are inert in the only Hebrew consumer, `final_audit.warn_foreign_remainder`, which whitespace-splits). A shipped `he.json` alone surfaces **zero** native-script name candidates — a project must add a `name_inventory` override to surface Hebrew names. Step 0a's preset-copy pass is wired to include `he.json` (SKILL.md's explicit copy list), so a fresh Hebrew project actually receives it under `${durable_root}/languages/`.
- **#194 — `scaffold_setup.py`, Step 0a's shipped bundle-hash marker writer.** Previously prose-only, so a real run failed the `has Step 0a run for this project?` check: nothing wrote `${durable_root}/runs/.plugin_bundle_hash` (read by `cache_key.compute_plugin_bundle_hash` + `resume_setup.compute_input_digest`) or `.orchestration_bundle_hash` (read by `resume_setup`). The new plugin-path-only script computes both markers (all 13 `PLUGIN_BUNDLE_MEMBERS` hashed uniformly at `durable_root/scripts/<name>`; a locally-pinned 4-tuple `ORCHESTRATION_BUNDLE_MEMBERS`) with symlink-refusing, dir-fd-pinned, fail-closed atomic writes (unguessable temp leaf, plus an fsync + inode/size verify that refuses to publish a substituted or truncated marker), importing the member set from `cache_key` (never re-declared) so a scaffold/cache_key drift that would mass-invalidate can't arise — a drift-catcher test pins it. Excluded from Step 0a's copy sweep so it never becomes hashable bundle input.
- **#201 — enforced heading-shape output contract.** `translate_TASK.template.md` gains a neutral per-block output-format note (a heading block's value is the bare target heading text — no leading markdown `#`, no source echo, no hand-formatted numbering; the renderer supplies the level), block-model-neutral with no canned example. `validate_assembled.py` now hard-rejects a surfaced, non-empty translated heading whose text begins with a markdown heading marker (`^\s*#`) as a new `heading_leading_hash` defect, in both the default and `assembled_book` scopes, reusing the drafts/nodestream objects already built (no re-parse) and false-RED-averse (only the leading `#` is banned; bilingual/echo headings stay legitimate). No `PROMPT_CONTRACT_VERSION` bump — the template change is additive presentation guidance and the durable per-project copy drives `prompt_hash`, so existing projects are unaffected.

### Changed

- **#225 — offset-safe, mark-inclusive tokenizer.** `TOKEN_RE` (in both `bootstrap_names.py` and its drift-guarded parity twin `language_smoke_report.py`, kept byte-identical) now absorbs combining marks — Hebrew niqqud/cantillation, Arabic harakat, Latin NFD accents — INSIDE a token instead of shattering a pointed/vocalized word into one token per base letter, preserving the raw Unicode-codepoint offsets `occ_index.py`'s evidence spans bind to. The mark class is built programmatically (category-filtered over 17 curated sub-ranges) so it stays version-robust across the plugin's supported Python floor — a hardcoded literal class would spuriously reject on a pre-Unicode-14 interpreter (Python 3.9 / Unicode 13.0) where part of the Combining-Diacritical-Marks-Extended range is still unassigned. Hebrew geresh (U+05F3), gershayim (U+05F4), and maqaf (U+05BE) — name-connecting punctuation — are also treated as intra-token connectors (like the Latin apostrophe/hyphen), so inventory names such as `ז׳בוטינסקי` (geresh) and `בן־גוריון` (maqaf) stay a single token that binds back to their source spelling instead of splitting. NFC-Latin/ASCII tokenization is byte-for-byte unchanged.
- **#209 — niqqud-aware foreign-remainder fold.** `final_audit.py`'s foreign-remainder check now folds Hebrew niqqud (category `Mn` in U+0591–U+05C7) symmetrically on both compare sides, so a pointed (vocalized) draft token matches its unpointed consonantal stopword. Hebrew-scoped, not a blanket `Mn` strip — a Latin/Cyrillic combining mark such as the acute in Spanish "Sí" is preserved, never collapsed.

### Migration

Two tiers:

1. **#225 forces a double cache invalidation, both unavoidable (the parity guard forces both files to change).** `bootstrap_names.py` is a `DERIVATION_BUNDLE_MEMBER` → `derivation_bundle_hash` flips and name candidates re-derive; `language_smoke_report.py` bytes feed `smoke_report_contract_hash` → every stored `language-smoke-report.json` `pass:true` goes **stale** and existing projects **must re-run the W3 smoke test**. Because NFC-Latin/ASCII tokenization is unchanged, re-derivation reproduces identical output — the cost is compute plus a manual re-smoke, not a correctness change. No Hebrew project exists yet, so this lands before any real Hebrew run.
2. **#194 / #195 / #201 / #209 are cache-safe.** `scaffold_setup.py`, `he.json`, `validate_assembled.py`, and `final_audit.py` are outside every `*_BUNDLE_MEMBERS` / schema / render list; presets are content-hashed one-at-a-time per project with no directory enumeration or language-code enum, so adding `he.json` changes nothing for existing fr/de/es/it projects, and writing a marker is inert to a project that already has or derives one.

## 1.8.0 — 2026-07-18

Opt-in source-anchored **appendix backlink integrity** for the Obsidian adapter — a `## Mentions` occurrence-index section in each entity note, derived from the *source* occurrence index instead of scanning translated prose. Closes the appendix defects found in the SSK vol.2 he→en audit: #206 (variant target renderings get no backlink), #207-a (distinct source forms sharing one `canonical_target_form` collapse to one owner). #207-b (one spelling = N referents) is surfaced for adjudication, not silently mis-attributed; the aggregated person-index page + `index_scope` routing are designed but deferred (see follow-ups).

### Added

- **`occurrence_targets.py`** — the source-anchored occurrence engine. `build(...)` returns `{eligible_by_source_form, unresolved_homonyms}`; eligibility (block / embedded-verse / footnote origins, resolved once, verse-renderability keyed on the source block's mount claim not node kind) lives here. Split source forms route to `unresolved_homonyms`; `sense_translated` proper names ARE indexed (source-anchoring links them safely where the inline linker cannot).
- **`## Mentions` section** in Obsidian entity notes (opt-in `output.adapter_config.obsidian.mentions_section.enabled`, default false), wrapped in reserved `<!-- lt:mentions:begin/end -->` markers. `assemble.py` computes the occurrence data (it holds the manifest) and rides it inside the NodeStream; the 4-arg adapter contract is unchanged. Inline `#207-a` collision de-linking is enabled with the same flag.
- **`validate_backlinks.py`** — an advisory (non-blocking) W9 gate: Mentions-section coverage (the sole warning source) + a native-inline-backlink diagnostic. Runs after `diff_rendered_output.py`; exit 1 is advisory, exit 2 halts.

### Cache / migration

- **No converged segment re-translates.** Nothing added enters `PLUGIN_BUNDLE_MEMBERS` or the 15-field per-segment cache key, and the new `mentions_section` flag is outside `profile_semantics_hash`.
- One schema file changes (`profile.schema.json` gains the opt-in flag), so an **in-flight, not-yet-converged** mass/glossary run started before the upgrade resumes under a fresh RUN_ID (converged segments still reused) — cache-reuse is unaffected.
- The feature is **opt-in and byte-identical when off**: existing projects render exactly as 1.7.0 until they set the flag; a project that enables it re-accepts its durable-local `.baseline`.

## 1.7.0 — 2026-07-17

Delivery-gate hardening on the assemble/audit path, closing three real gaps found during the SSK vol.2 he→en remediation. Closes #208, #210, #202.

### Added

- **#202 — `validate_assembled.py`, a new union structural-completeness gate.** A standalone, self-anchored, copied-to-durable-root script (same convention as `final_audit.py`/`validate_draft.py`) that checks every declared-heading source marker `(seg, block_id)` — the union over the manifest's `heading_types` plus the always-heading built-in `HEAD` — actually surfaces as a non-empty heading, using a `Counter` (not a set) so a repeated same-key occurrence can't hide behind its surviving twin. Runs in both scopes: `assembled_book` (against the rendered nodestream, at W9 before `diff_rendered_output.py`) catches a declared heading that produced no heading node; the default `segment_drafts_and_audit` scope (at W7/W8 after `final_audit.py`) catches a source-empty declared heading and gives the cross-segment aggregate view a per-segment gate can't. The default scope also rebinds every draft read to the ledger's `reviewed_draft_sha1` before trusting it, rejecting a hand edit made between W7 review and this gate. A non-gating WARN flags an undeclared block whose type matches a broad heading-like allowlist (`HEADING|TITLE|CHAPTER|SECTION|PART|SIMAN|PEREK|H[1-6]`) — advisory only, never a permanent false-reject. Deliberately declined: a per-block length band (he→en ratios vary too widely to set one) and treating the broad allowlist as a HARD gate (too heuristic; the declared set is the non-heuristic source of truth).

### Fixed

- **#208** `final_audit.py` exited `0` on an incomplete project — the default delivery path had no deterministic completeness gate, only report-only JSON. The exit code is now `0` clean / `1` hard defects in converged drafts (unchanged priority) / `3` project incomplete (`not project_complete`, mirroring `assemble.py`'s own `assert_project_complete` predicate) — so both delivery paths are consistent and a caller can distinguish "incomplete" from "defective."
- **#210** `assemble.py`'s heading classifier keyed *only* off the literal block type `"HEAD"`, so a custom extractor's own heading tags rendered as flat prose with a raw seg-id title/filename instead of the intended heading text. The manifest gains an optional `heading_types` array (absent → byte-identical to today, since only `HEAD` is a heading); a block whose `type` is `HEAD` **or** listed in `heading_types` now classifies `heading`. Declaring a heading type is opt-in per adapter — the shipped `gutenberg_epub` adapter still emits `HEAD` and needs no change.

### Migration

Three tiers, all real, none of them "zero migration":

1. **Converged-segment caches survive.** Neither `PLUGIN_BUNDLE`/`DERIVATION`/`schema_hash` is touched by this release — a fully-converged project re-runs with zero re-translation.
2. **Resume-fresh, and — for an interrupted project — in-flight re-translation.** Step 0a copies `manifest.schema.json` into every durable `schemas/` dir, so the edited schema changes the resume-integrity digest and **every interrupted run restarts fresh** on its next Step 0a. Because a fresh run re-selects segments, an interrupted project's **`recoverable`-category** segments (in-flight `in_progress`/`pending` — the only nonterminal statuses `DEFAULT_ELIGIBLE_CATEGORIES` dispatches) **may be redispatched and retranslated**. This does **not** extend to `blocked`/`non_converged` segments — those classify `human_escalation` and stay excluded from default dispatch — nor to already-converged segments, whose caches survive per tier 1. `render_version` is **not** changed.
3. **Custom `heading_types` adopters re-accept assembled baselines.** Only a custom project that *chooses* to declare `heading_types` in its own extractor sees its already-converged segments go stale (the extractor edit changes `source_extraction_hash`) and, if it has a frozen render baseline, needs `diff_rendered_output.py --accept-baseline --force-accept-baseline` after review — headings now render as headings, changing assembled content. Shipped HEAD-only projects are byte-identical, no re-accept needed.

## 1.6.0 — 2026-07-17

Implements RFC #215 **Phase 2** (#215): surface the *invisible* failure class — a canon entity confidently mis-identified or over-merged that `review_queue` never flagged — via a deterministic structural-risk scan plus an **opt-in, advisory, adverse-only** source-grounded skeptic pass. Ships **disabled by default**; the warn→block flip is deferred to Phase 3.

### Added

- **`suspicion_scan.py` — deterministic, confidence-independent structural-risk triage.** Emits a schema-valid `suspicion_worklist.json` (`suspicion-worklist.schema.json`) flagging seven structural risk classes: `merge_participant` (over-merge, #207), `established_offline` (a frozen `basis:"established"` entry under offline research mode), `singleton`, `high_dispersion`, `all_citation` (adapter-safe — disabled fail-safe on `custom`/unknown source formats), `near_merge` (recall-preserving character-bigram blocking + `1 − difflib.SequenceMatcher.ratio()` distance, budgeted with logged truncation), and a globally-capped deterministic `sampled` spot-check. Verse is counted representation-aware (standalone `mount:"block"` owned by the block scan; embedded `mount:"embedded"` scanned from `verse.store` with citation status from the carrier block's type) so `singleton`/`all_citation` stay precise. Reuses `occ_index.production_occurrences` (never re-implements matching). The worklist is stamped with a `producer_input_digest` binding it to the exact canon/manifest/config/scanner it was built from.
- **`skeptic_setup.py` — a dedicated `kind="skeptic"` resume domain.** The skeptic analogue of `resume_setup.py` (kept **out** of `PLUGIN_BUNDLE_MEMBERS`, and `resume_setup.py` is untouched). Re-verifies the worklist's `producer_input_digest` fail-closed (a since-changed canon/manifest/particle-config/scanner can never be silently reprocessed), computes a skeptic `input_digest` over the full skeptic code + config closure, derives a skeptic `RUN_ID`, and writes per-entity assignment + aggregate manifests **before** any dispatch (provable coverage).
- **`skeptic-pass-wf.template.js` + `skeptic_ready.py` — the adverse-only skeptic pass.** Clones the glossary-pass control flow: bounded per-entity windows fed to a `codex:codex-rescue` agent adversarially framed to *find a contradicting sentence*, able only to author `adverse` / `propose_split` / `propose_rescope` / `insufficient_window` records **with byte-verified evidence** (re-authenticated through `evidence_verify`), into a new `skeptic_triage.json` (`skeptic-triage.schema.json`) whose schema **cannot express a confirmation** and which **no freeze/merge reader opens**. `skeptic_ready.py` owns `--validate-fragment` / `--merge-fragments` (one serialized atomic merge) / `--verify-merged` (fresh-read coverage + schema + evidence re-verification).
- **`skeptic_report.py` — a separate advisory summary command** rendering the triage artifact. The category-5 `canon_adjudication_audit.py` gate is **unchanged byte-for-byte** (a regression test asserts identical summary + exit code with and without `skeptic_triage.json` present).
- **Profile opt-in `glossary.skeptic_pass`** (`enabled` default false, plus `windows_per_entity`, `sample_cap`, `dispersion_threshold`, `near_threshold`, `near_cap`, `near_pair_budget`, `citation_block_types`). Defaults are the single-source-of-truth constants in `skeptic_constants.py`; a parity test asserts the schema `default:` values never drift from them.

### Migration

- **Converged segments do NOT re-translate.** Nothing added here enters `PLUGIN_BUNDLE_MEMBERS` or the 15-field per-segment cache key (`cache_key.py`): the new scripts are outside the plugin bundle, the new `glossary.skeptic_pass` profile field is outside the `profile_semantics_hash` allowlist, and `compute_schema_hash` hashes only `draft`/`review`/`segpack` schemas — so the new `*.schema.json` files are cache-key-safe. A project's already-converged drafts are byte-for-byte reused.
- **One narrow workflow-resume caveat (distinct from cache-reuse).** `resume_setup.py` folds every `*.schema.json` into its own `input_digest`, so adding the three new schema files changes that digest: an **in-flight, not-yet-converged** mass/glossary run that was started *before* this upgrade will resume under a **fresh `RUN_ID`** rather than continuing the old run's run-dir. Converged segments in that run still do not re-translate (that is governed by the per-segment cache key above); only the not-yet-done work restarts its run bookkeeping. A run begun after the upgrade is unaffected.

## 1.5.0 — 2026-07-16

Implements RFC #215 Phase 0 + Phase 1 (#204, #215): surface names the capitalization gate misses in unicameral scripts, and adjudicate a homonymous source form into distinct senses via a strict, byte-verified sidecar — `canon.json` stays a 1:1 dict.

### Added

- **#204 — caseless multiword surfacing.** `bootstrap_names.py` now does offset-preserving two-pass candidate extraction: pass 2 surfaces `name_inventory` matches invisible to the ASCII/`Lu` capitalization gate (Hebrew and other unicameral scripts). `tokenize()` returns 4-tuples `(token, preceding_char, start, end)`; `mask_sentinels` is equal-length (offset-preserving). `LanguageConfig` gains `name_inventory` (frozenset). New `occ_index.py` builds a source occurrence index over segpack manifests (`production_occurrences()` — the shared production matcher — plus `build_occurrence_records`, `iter_manifest_blocks`, `index_manifest`, and a CLI). `language_smoke_report.py` carries a drift-guarded parity implementation of the two-pass extractor.
- **Homonym-split senses sidecar (#215).** New `canon_senses.py` + `assets/schemas/canon-senses.schema.json`: a strict `canon_senses.json` sidecar (≥2 senses per split form). Loader API `load_senses(path, *, allow_absent, schema_path) -> SensesResult`, `is_split()`, `normalize_form()` (NFC + casefold + whitespace-collapse), `CanonSensesLoadError`. New `evidence_verify.py` does byte-verified, matcher-authenticated evidence checking — every sense's evidence span must be an exact byte match in the named block **and** a span the production matcher itself yields. Deterministic scripts verify evidence; humans adjudicate identity.
- **Category 5 audit gate.** `canon_adjudication_audit.py` gains a `homonym_split` category; `run_check` gains the mandatory split-evidence gate (`--particle-config`, a narrowed `--advisory` that never masks a split blocker, `collapsed_split` detection). `SKILL.md` + `orchestration-and-batching.md` add the mandatory W-step running this gate between the W3 rejoin branches and W3a.

### Changed

- `canon_validate.py`: `--merge`/`--check-batch`/`--merge-batches` refuse a batch entry that would recollapse a split form; adds `--senses-path`.
- `glossary_batch_plan.py`: split forms are excluded from glossary batch planning; adds `--senses-path`.
- `final_audit.py`: the intentional-split glossary-diff note routes to `canon_senses.json`.
- `canon_adjudication_audit.py`: the local `normalize_form` is deleted in favor of the shared `canon_senses` import.
- `cache_key.py`: `canon_senses.py` is added to `PLUGIN_BUNDLE_MEMBERS` — this bundle-hash change means in-flight runs re-translate on next resume (documented, accepted).

### Known limitation

- `TOKEN_RE` excludes Unicode category-M combining marks, so pointed Hebrew / vocalized Arabic / NFD Latin source forms do not surface and cannot authenticate evidence (loud-blocking, never silently wrong). Deferred to a separate plan-reviewed fix.

## 1.4.7 — 2026-07-16

Fixes #198: W5 mass-translate could not reliably converge because the codex translate/review dispatch was backgrounded by the `codex:codex-rescue` forwarder (which returns a stub and sometimes never launches codex), so no draft artifact appeared and every segment ended in `translate-timeout`, forcing an ad-hoc direct-codex fallback.

### Fixed

- **#198** W5 translate and review are now launched by a shipped stdlib driver, `codex_job.py`, that owns the codex-companion launch deterministically: it runs `codex-companion task --background --write --effort high`, polls `status` to a terminal state, validates the isolated attempt via the gate scripts' new `--candidate-file` mode, and atomically `os.replace`s it into the canonical path (validate-before-promote). A plain-Claude drive agent launches the driver detached (`nohup`) and returns `DISPATCHED <seg> <DISP>`; the Workflow's on-disk `draft_ready.py` + `validate_draft.py` (translate) and `review_ready.py` (review) content re-validation on the current canonical remains the sole acceptance authority. A template SEGS uniqueness guard enforces one dispatch per segment. The glossary-pass codex dispatch is unchanged.

## 1.4.6 — 2026-07-14

A validator/renderer-consistency patch closing the deferred half of #183. Closes #188.

### Fixed

- **#188** `validate_draft.py` verse-line counting is now LF-only at its two direct call sites,
  matching #183's renderer change. The `rendered`-line count (check 5) and the `_source_line_count`
  source-line count that feeds it for block-mount verses switched off `str.splitlines()` — which also
  breaks on exotic Unicode boundaries (U+2028/U+2029/U+0085/U+000B/U+000C/U+001C–U+001E) — to a shared
  LF-specific `_split_lf_lines`, so the validator and the (already LF-only) renderer split a verse's
  rendered/source text identically for block-mount verses and an exotic interior separator no longer
  counts as a line break. A stale `_source_line_count` docstring (claiming the segpack schema carries
  no `n_line`) is corrected. Behavior is unchanged for realistic `\n`-delimited input.

### Migration

- `validate_draft.py` is a `PLUGIN_BUNDLE_MEMBERS` file, so editing it flips `plugin_bundle_hash` —
  every converged segment's 15-field composite `cache_key` changes and is **re-translated once** on the
  next run. The resume-integrity digest folds `plugin_bundle_hash`, so any interrupted / in-flight run
  also **restarts fresh**. **Not affected:** `schema_hash` (no schema edited), `derivation_bundle_hash`
  (`segpack.py` untouched), `render_version`, `smoke_report_contract_hash`.

### Known residual (deferred follow-up)

- Embedded verses read their source `n_line` from the segpack field, which `segpack.py`'s
  `_verse_line_count` copies from the manifest or (when it is missing/0) derives via its own
  `splitlines()`. That runtime fallback still counts exotic separators, so for an embedded verse with a
  missing/0 manifest `n_line` and exotic-separator source, line counting is not yet LF-only. Making it
  so requires editing `segpack.py` (a `DERIVATION_BUNDLE_MEMBERS` file → re-derivation migration); the
  `segpack.schema.json` `n_line` description also needs a source-neutral rewrite. Both are tracked in a
  follow-up issue. Real-world inert (realistic input has no exotic separators).

## 1.4.5 — 2026-07-14

A documentation-accuracy patch closing two LOW-severity findings surfaced during the v1.4.3 review.
Closes #185, #186.

### Fixed

- **`segpack.schema.json` descriptions (plus a `cache_key.py` docstring and a
  `validate_extraction.py` diagnostic) no longer attribute extraction universally to
  `extract.py.template` (#185).** For a `source.format: custom` project the `manifest.json` is
  produced by the co-designed custom extractor at `scripts/custom_extractors/<value>` (not
  `extract.py.template`), and `segpack.py` builds each segpack from that manifest; the descriptions
  now attribute each fact to the component that actually produces it (the manifest/extractor vs.
  `segpack.py`).
- **`orchestration_bundle_hash` is now documented accurately as non-gating for convergence** (never
  part of the 15-field composite `cache_key`) **but gating for resume** (its marker is folded into
  `resume_setup.py`'s resume-integrity digest), across `SKILL.md`,
  `references/ledger-and-resumability.md`, `references/orchestration-and-batching.md`, and the
  `cache_key.py` / `draft_ready.py` / `review_ready.py` / `select_segments.py` comments (#186). The
  old flat "diagnostic-only" / "non-gating" / "never gated against" wording implied it had no
  runtime effect, which is false for the resume path.

### Migration

1.4.5 corrects inaccurate documentation and intentionally edits cache-key-locked surfaces. Flipped
on upgrade: `schema_hash` (`segpack.schema.json` edited) and `plugin_bundle_hash` (`cache_key.py` /
`review_ready.py` edited) — so every converged segment's 15-field composite `cache_key` changes and
is re-translated once on the next run. `orchestration_bundle_hash` also changes (`draft_ready.py` +
`select_segments.py` edited), and since the resume-integrity digest folds `plugin_bundle_hash` plus
a hash of `schemas/`, it changes too — so any interrupted / in-flight run restarts fresh. **Not
affected:** `derivation_bundle_hash` (`segpack.py` deliberately left untouched — no
`blocked_needs_regeneration`; the same-class fix there is deferred to a follow-up issue) and
`smoke_report_contract_hash` (`language_smoke_report.py` untouched). No validation or pipeline
behavior changed — only documentation strings, comments, one diagnostic message, and test prose.

## 1.4.4 — 2026-07-13

### Fixed

- **#183** `render_obsidian.py`: verse render/gloss line-splitting is now LF-only. Four sites
  (`_render_verse_block` body + gloss, `_render_verse_inline` body + gloss) switched off
  `str.splitlines()` — which also breaks on exotic Unicode boundaries (U+2028/U+2029/U+0085/
  U+000B/U+000C/U+001C–U+001E) — to a shared LF-specific `_split_lf_lines` / `_flatten_gloss`,
  so a verse rendered as a block and the same verse mounted inline now split identically and an
  exotic separator no longer creates a spurious line break. Renderer-only, consistent with #172
  and #98. (The parallel `validate_draft.py` verse-line count still uses `splitlines()`; that is
  deferred to a follow-up because it is a plugin-bundle-hash input.)

### Migration

- Editing `render_obsidian.py` flips `render_version` (it is one of the two files hashed into the
  render-baseline stamp). On the next run `assemble.py` writes a fresh candidate and the render
  diff-gate reports a **mismatch** against the frozen last-accepted baseline (the gate never
  re-renders anything itself) for any verse whose reduced markdown changed; review it and
  explicitly re-accept a replacement baseline (`--force-accept-baseline`). A candidate that is
  identical after the diff tool's line reduction instead only gets the informational
  `stale_baseline` warning. **No mass re-translation and no canon effect** — `render_obsidian.py`
  is in neither `PLUGIN_BUNDLE_MEMBERS` nor `DERIVATION_BUNDLE_MEMBERS`.

## 1.4.3 — 2026-07-13

A validation-robustness patch closing three LOW-severity findings from the v1.4.0 Hebrew→English
smoke test and the v1.4.1 documentation sweep. Closes #174, #180, #181.

### Fixed

- **`select_segments.py` no longer aborts the whole run when one segment's segpack is unreadable
  (#174).** The blocked-regeneration derivation-state gate read `segpack_{seg}.json` through
  `read_json`, which calls `fatal()` (raising `FatalError`) on a missing / corrupt /
  invalid-UTF-8 / non-object file — killing selection for every other segment too. A new
  `read_segpack_nonfatal()` catches `FileNotFoundError`, `UnicodeDecodeError` (a `ValueError`
  subclass, so not caught by `except OSError`), `OSError`, and `JSONDecodeError` (plus a non-dict
  top level) and escalates just that one segment as `human_escalation` / `segpack_read_failed`; a
  nested non-mapping `generation_hashes` is guarded the same way instead of raising an uncaught
  `AttributeError`.
- **W2 post-extraction gate no longer wedges a `custom` source on plugin upgrade (#180).** The
  `extract.py` `EXTRACTOR_CONTRACT_VERSION` drift check (`profile_validate.py`) and the self-check
  region-hash pin (`validate_extraction.py`) both ran against `extract.py` even for a `custom`
  source — but for `custom` that file is Step 0a's unadapted `extract.py.template` copy, never the
  real co-designed extractor at `scripts/custom_extractors/<value>`, so pinning it could only ever
  vacuously pass or spuriously fail on upgrade. Both checks are now format-gated OFF for
  `source.format: custom` (fail-safe: a missing/malformed `source.format` is treated as
  non-custom, so the checks stay ON); the schema-validation and derivable re-derivation checks stay
  unconditional. The managed-gate docs, `manifest.schema.json` field descriptions, and the
  source-format-adapter references are reconciled to this custom/template-based split.
- **W3 language-smoke completeness check is dedup-aware and set-coverage-based (#181).**
  `parse_checked_names` silently kept duplicate `--checked-name` entries, so the low-name-density
  branch's entry-count floor could be satisfied by repeating one name (`Alice,Alice` reads as "2
  names") while a genuine candidate went unchecked. Names are now de-duplicated (first-occurrence
  order) and the low-density branch asserts real SET COVERAGE of the candidate set, naming every
  still-uncovered candidate in its fatal message.

### Migration

No cache-key member is touched (none of `draft` / `review` / `segpack.schema.json`, nor a
`PLUGIN_BUNDLE_MEMBERS` / `DERIVATION_BUNDLE_MEMBERS` script), so **no converged segment is
re-translated** by this release. Two lower-impact hashes change automatically:

- The **resume digest** changes (`select_segments.py`, `language_smoke_report.py`, and the two
  edited `*.schema.json` files all feed it) — an interrupted / in-flight run restarts fresh on the
  next engine invocation; already-converged segments stay reusable.
- **`smoke_report_contract_hash`** changes because `language_smoke_report.py` changed — the W3
  language smoke test re-runs once on the next engine invocation.

## 1.4.2 — 2026-07-13

A rendering / validation fidelity patch closing three medium-severity bugs surfaced by a
multi-agent repo investigation. Closes #171, #172, #173.

### Fixed

- **`validate_draft.py` placeholder fidelity no longer assumes a `VERSE_` prefix (#173).** The
  prose-block (check 2) and footnote (check 4) placeholder multisets were built from a regex that
  hardcoded `⟦FNREF_N⟧` / `⟦VERSE_…⟧`. A custom source-format adapter is free to name its
  embedded-verse placeholders anything (e.g. `⟦POEM_1⟧`), so such a placeholder was invisible to
  the gate — a draft that DROPPED it passed validation (a false-green), with the loss caught only
  much later (`final_audit.py` WARN, `assemble.py` FATAL at W8). Placeholders are now matched by an
  EXACT MAP: a `⟦…⟧` span is a fidelity token only if it is a `⟦FNREF_N⟧` anchor or one of the
  segpack's own declared `verses[].placeholder` strings. (Deliberately NOT an "any `⟦…⟧` span"
  widening, which would wrongly require literal editorial prose such as `⟦variant⟧` to survive
  translation verbatim.)
- **`render_obsidian.py` no longer leaks a raw `⟦…⟧` sentinel into a segment note's title and
  filename (#171).** `_segment_title` returned the first heading node's text verbatim, so a chapter
  heading carrying a footnote anchor or verse placeholder produced `title: ⟦FNREF_1⟧` and a
  filename like `001 FNREF_1.md`, disagreeing with the correctly-resolved H2 in the note body. A
  heading's KNOWN sentinels (footnote anchors, declared verse placeholders) are now resolved to
  plain title text (footnote-reference markup stripped, no entity links); any other bracketed span
  is preserved as literal prose, and a plain heading's title/slug stays byte-identical to before.
- **`render_obsidian.py` multi-line footnote definitions and verse-block literal glosses no longer
  eject their continuation lines out of the construct (#172).** A multi-line footnote definition
  (or the blank line left after a def-embedded verse's sentinel is stripped) had its continuation
  rendered as ordinary page-body text; a multi-line `Literal:` gloss under
  `full_rhymed_plus_literal` ejected its tail out of the blockquote with a dangling `*`. Footnote
  continuations are now indented (4-space CommonMark continuation) and the gloss is flattened to a
  single blockquote line, with CRLF / CR / LF line endings normalized (LF-specific — never
  `str.splitlines()`, which would over-split U+2028 / U+2029 / NEL).

### Migration

No manifest field is hand-edited, but two byte-derived hashes change automatically:

- **`plugin_bundle_hash`** (part of the translate/review cache key) changes because
  `validate_draft.py` changed — previously-converged segments are considered stale and re-run
  translate / review / fix on the next engine invocation.
- **`render_version`** (in `diff_rendered_output.py`) changes because `render_obsidian.py` changed
  — accepted render baselines are stale and re-render on the next W8 pass.

## 1.4.1 — 2026-07-13

A documentation-and-gate hardening patch closing three LOW-severity findings from the v1.4.0
Hebrew→English smoke test. Closes #176, #177, #178.

### Fixed

- **Draft `seg`-identity gate (#178).** `validate_draft.py` and `draft_ready.py` type-checked
  `draft["seg"]` but never compared it to the requested segment CLI argument, so a
  `seg01.draft.json` carrying `"seg":"seg02"` passed `validate_draft.py seg01` (`OK`) and
  `draft_ready.py seg01 --expect-token …` (`READY`). Both scripts now reject a
  mislabeled/cross-wired draft with a clear "requested X but file carries Y" error instead of
  certifying it ready.

### Documentation

- **`plain_text` reconciled from an implied-shipped adapter to specified-but-not-yet-implemented
  (#176).** The shipped `extract.py.template` FATALs on any `source.format` other than
  `gutenberg_epub`; the reference docs (source-format-adapters, ledger-and-resumability,
  output-target-adapters, gotchas), `SKILL.md`, the marketing description, and several code
  comments all previously presented `plain_text` as a working/shipped source adapter. Every site
  is reconciled to a consistent three-status framing: `gutenberg_epub` is the one working built-in
  adapter, `custom` is supported-but-experimental expert mode, and `plain_text` is specified but
  not yet implemented (tracked by #62).
- **W3 language-smoke `pass:true` framing made honest for uncased scripts (#177).**
  `bootstrap_names.py`'s proper-noun candidate gate requires a Unicode `Lu` (uppercase) initial,
  so uncased scripts (Hebrew, Yiddish, Arabic — all `Lo`, no case) can never surface native-script
  name candidates; a `pass:true` on such a source certifies only the detector's reach, not that the
  text has no names. The reference docs and `SKILL.md` W3 now say so explicitly. Separately, the
  low-density "completeness" label is corrected: the check enforces an **entry-count,
  dedup-blind** floor (`len(checked_names) == candidate_names_total`, duplicates in
  `--checked-names` each count), not distinct-name coverage — reworded in
  `language-pair-parameterization.md` and the `language-smoke-report.schema.json` description.
  This is no longer doc-only: `language_smoke_report.py`'s own low-density fatal messages are
  reworded too, from an implied distinct-name-coverage guarantee to an explicit dedup-blind
  entry-count check, so the CLI's own output matches the corrected docs.

### Migration

- **`validate_draft.py` is a `PLUGIN_BUNDLE_MEMBERS` entry**, so the #178 seg-identity patch flips
  `plugin_bundle_hash`. In a **resumed** project, every previously-converged segment goes `stale`
  on the next run and undergoes a **fresh translate/review/fix pass** — not merely re-validation.
  This is unavoidable (the fix requires editing the script) and is a one-time cost on the first run
  after upgrading to 1.4.1.
- **`language_smoke_report.py` is also edited (#177 message reword), which flips
  `smoke_report_contract_hash`** (a sha1 of the script's own bytes). A resumed project therefore
  also re-runs its W3 language-smoke test once on the first post-upgrade run. Marginal on top of
  the re-convergence above — W3 is a cheap, deterministic pass with no codex calls.

## 1.4.0 — 2026-07-12

Sense-translated speaking-name support: a fifth canon `basis` value plus a durable-root staleness
preflight that keeps a mid-pipeline resume from hanging on a stale schema. Closes #138.

### Added

- **`basis: "sense_translated"` — a fifth canon basis value (#138).** `canon-entry.schema.json` and
  `canon-batch.schema.json` gain a fifth `basis` enum member so a speaking / meaningful name rendered
  by SENSE (its meaning) rather than transliterated can be locked in canon `entries{}` with a frozen
  `canonical_target_form`, instead of being re-parked in `review_queue` on every run. A
  `sense_translated` entry is constrained by a dedicated schema conditional — `is_proper_name: true`,
  a non-empty `note` (the sense rationale), a non-empty `canonical_target_form`, and no `source` field
  — enforced end-to-end by `canon_validate.py`; the glossary / translate / review prompts and the
  style-bible + profile seeds carry the new basis so the adjudicator can assign and lock it.
- **`glossary_preflight.py` — a W3 glossary pre-dispatch staleness gate (#138).** A new stdlib-only,
  plugin-path script run right before any glossary batch is dispatched: it compares a resumed
  project's durable copy of `canon-entry.schema.json` / `canon-batch.schema.json` / the seed
  `glossary_TASK.md` against the plugin's own shipped copies (whole-artifact, order-exact
  canonical-JSON equality, with duplicate-key rejection) and HALTS with one actionable line if the
  durable root is a stale pre-1.4.0 copy that cannot accept a `sense_translated` item — turning what
  would otherwise be an unbounded retry-until-valid hang on mid-pipeline resume into a clean "re-run
  Step 0 + 0a". Fresh on every run and never copied into the durable root (same exception class as
  `profile_validate.py`).

### Changed

- The canon/glossary reference docs, the W3 orchestration-and-batching notes, gotchas, the Obsidian
  output-adapter doc, and `render_obsidian.py` are updated for the new basis. New regression suites
  cover the enum/schema drift (`canon_enum_drift.test.py`), the preflight gate
  (`glossary_preflight.test.py`), and end-to-end `sense_translated` behaviour
  (`sense_translated_behaviour.test.py`).

## 1.3.7 — 2026-07-11

Canon-enforcement + transient-recovery + review-gate correctness cluster from the 2026-07-11 issue
sweep: closes #130, #131, #132, #133, and #135.

### Added

- **`canon_map` segpack field (#130)** — `segpack.py:build_pack` now emits a `canon_map`
  (`source_form` → frozen `canonical_target_form`) for this segment's already-canonized names,
  required in `segpack.schema.json` and enforced by `validate_segpack`, and spliced into
  `translatePrompt` and `reviewDispatchPrompt` so the frozen canon target form actually reaches
  translate/review time. The rule is to render the canonical STEM/spelling **declined as the target
  grammar requires** (a correctly inflected form of the canonical stem is correct); the reviewer flags
  only a different name, a different transliteration of the stem, an untranslated canonical name, or an
  epithet→real-surname swap.

### Fixed

- **The frozen canon was unenforced at translate/review time (#130).** The segpack carried only
  source-form name strings; `canonical_target_form` reached no prompt, so an `established`-basis name
  drifted freely and the reviewer had no target reference to check against. `canon_map` now delivers it;
  the false "use verbatim, no exceptions" descriptions in `translate_TASK.template.md`,
  `review_TASK.template.md`, and `segpack.schema.json` are corrected. No `cache_key.py` change is needed
  — `used_terms_hash` already hashes the full canon entry values, so a `canonical_target_form` edit
  already re-stales a converged segment (the bug was purely a delivery gap).
- **Transient/mechanical mass-translate failures were parked in `human_escalation` (#131).** A review
  poll timeout, or a fix call that died / hit the 64k output-token ceiling / was safety-classifier-blocked
  on an otherwise-valid draft, was recorded as a terminal `blocked`/`non_converged` ledger status that
  `select_segments.py` excludes from auto-redispatch. These now skip the terminal ledger write — the
  `in_progress` fragment classifies `recoverable` and auto-redispatches next run. A fix-call failure is
  disambiguated by a fresh `draft_ready.py` + `validate_draft.py` probe (a present, valid draft is
  recorded `fix-call-failed`/recoverable, never mislabeled `draft-missing`); because the probe call can
  itself fail transiently, an inconclusive probe is also treated recoverable, reserving terminal
  `blocked/draft-missing` for a probe-confirmed genuinely-absent/invalid draft. Only genuine content
  non-convergence (`cap`) still escalates to a human.
- **The review read→check byte-compared free-text finding bodies (#132).** `review_artifact_check.py`
  now projects each `findings[]` element to `{loc, severity}` before comparing, dropping the free-text
  `issue`/`suggest` prose, so a transcription slip in review prose no longer terminal-blocks an
  already-validated review (a slipped/dropped/fabricated finding — loc, severity, or array-length
  divergence — still fails the compare). To keep the fixer applying the REAL reviewer guidance rather
  than a lossy read-agent copy, `fixPrompt` now sources the findings it applies from the authoritative
  on-disk `review.json` (validated fresh this round by `review_ready.py`'s `dispatch_token` check),
  never the in-memory transcription — closing the gap where a substantive mis-transcription of
  `issue`/`suggest` could otherwise misdirect the fixer once the compare stopped binding those fields.
- **No authenticity gate on `findings[].loc` (#133).** A review verdict left behind by a codex call
  killed mid-judgment (real `draft_sha1`/`dispatch_token`, sentinel `loc` such as `TASK`/`PROCESS`) was
  trusted and false-blocked a clean draft. `getVerifiedReview` now rejects any verdict whose
  `findings[].loc` is not a colon-delimited structural reference (real locs are `{btype}:{seg}` / `FN:n`
  / `VERSE:vid`; block types are deliberately not a fixed enum, so only the colon shape is invariant),
  routing it to a recoverable `review-fabricated-loc` before a fix dispatches against the phantom
  finding.
- **Stale `findings` schema description (#135).** `review.schema.json` and the workflow `REVIEW_SCHEMA`
  no longer claim `findings` is "Empty when clean is true"; they state it may carry residual low/cosmetic
  items even when `clean` is true (clean is judged solely on whether any finding requires a fix round).

## 1.3.6 — 2026-07-11

Three fixes from the 2026-07-11 shipped-template audit: a HIGH deterministic convergence blocker on
every freshly scaffolded project (#129), a glossary-pass canonicalization gap (#134), and a
static-typing house-convention deviation in the shipped extractor (#136).

### Fixed

- **STYLE_CONTRACT markers now ship in `style_bible.template.md` (#129).** The seed template wraps its
  `style_contract` sections A–F in the `<!-- STYLE_CONTRACT_BEGIN -->` / `<!-- STYLE_CONTRACT_END -->`
  marker comments that `cache_key.py:compute_style_contract_hash` hard-requires. Before this fix, every
  fresh project scaffolded without them: each segment translated and reviewed cleanly and wrote a valid
  draft to disk, but the convergence-recording path FATALed on every segment (`ledger-write-failed`), so
  the batch reported "0 converged" while 40%+ of drafts were clean on disk — an opaque hard blocker whose
  root cause (two missing comment lines) was named in no operator-facing instruction. `scaffold_validate.py`
  gained a fourth W1 gate that rejects a missing / duplicated / out-of-order marker pair before any real
  translation spend — using the exact same marker byte-strings and failure conditions as the hash
  consumer, so a clean W1 pass guarantees the hash cannot later FATAL on a marker-shape problem — and
  SKILL.md now cautions operators to preserve the shipped markers.
- **Fatal-abort helpers in `extract.py.template` are annotated `-> NoReturn` (#136).** `_missing_dep` and
  `die` (both of which unconditionally `sys.exit(1)`) now carry the `NoReturn` return type every other
  shipped script already uses, so a project that lints its copied `extract.py` with Pyright no longer gets
  spurious "possibly unbound" warnings on the four optional-dependency imports (`yaml`, `jsonschema`,
  `bs4`, `lxml`).

### Changed

- **Glossary pass gains an epithet/nickname/alias canonicalization rule (#134).** Both `glossary_TASK.md`
  and the glossary-pass dispatch prompt now state that only true orthographic spelling variants of the
  same surface name may share one `canonical_target_form`; a salon nickname, epithet, sobriquet, or alias
  is resolved as its own surface form (usually `transliterated`, e.g. `Sapho` → `Сафо`) and is never given
  its referent's real-name form (never `Скюдери`), with any known identity link recorded in `note` only.
  This closes a latent trap where an epithet could be clustered onto the referent's canonical form and
  then substituted into prose during a canon reconcile. Note: a speaking-name whose correct rendering is
  a sense-translation still has no lockable `basis` in the current schema and is routed to `review_queue` —
  a lockable basis for that case is tracked as a follow-up.

## 1.3.5 — 2026-07-11

W3 glossary-pass resumability + cost curation, and a resumability-safe resolution of #91's
capitalized-elision ambiguity: closes #101, #95, and #91 from the 2026-07-09 five-agent audit. A new
curation script, `glossary_batch_plan.py`, now sits between `bootstrap_names.py` and the glossary-pass
Workflow — excluding names already resolved in `canon.json`, curating the survivors by frequency, and
force-including flagged elision pairs for adjudication.

### Added

- **`assets/scripts/glossary_batch_plan.py` — the W3 candidate→batch planner** (#101, #95, #91) —
  deterministic curation + batching of `bootstrap_names.py`'s unfiltered candidates into the
  glossary-pass Workflow's `args`/`batches` payload, run once by the orchestrating session before
  `resume_setup.py`. It excludes every candidate already resolved in `canon.json` (an `entries{}` key
  or a non-retried `review_queue[].source_form`), curates the survivors by `likely_name` and
  `--min-candidate-freq` (default 2), and force-includes flagged elision-ambiguous pairs. When every
  candidate is already resolved it emits `{"no_new_candidates": true, "batches": []}` and the
  orchestrator skips `resume_setup.py` and the Workflow entirely. Mechanical only — never an
  accuracy/identity call (the plugin-wide IRON RULE). Registered in `cache_key.py`'s
  `PLUGIN_BUNDLE_MEMBERS` (not `DERIVATION_BUNDLE_MEMBERS`): that is the bucket the glossary
  `input_digest` actually hashes, so a planner edit correctly re-stales a glossary run, and it leaves
  the canon generation stamp's semantics intact.
- **Optional `glossary.min_candidate_freq` profile key** (#95) — an integer ≥ 1 added to the existing
  `glossary` object in `profile.schema.json` as **optional** (absent → the planner's built-in default
  of 2), so existing profile-version-1 files stay valid under the object's `additionalProperties:
  false`. The orchestrating session passes its value to `glossary_batch_plan.py --min-candidate-freq`;
  the script never reads YAML itself.

### Fixed

- **W3's "exclude already-resolved candidates" rule was documented but never applied to
  `review_queue`** (#101) — the rule lived only as prose in the glossary-pass template's header,
  delegated to "the orchestrating session," which in practice only ever excluded `entries{}` keys,
  never `review_queue` entries, so every queued name was re-researched on every W3 re-run.
  `glossary_batch_plan.py` now enforces the exclusion in code against BOTH `entries{}` and
  `review_queue`, with an explicit `--retry SRC[,SRC...]` path for the documented "re-research a queued
  name only on explicit human request" case (a stale `--retry` name absent from both inputs fails
  loudly, exit 2, rather than silently no-opping).
- **W3 had no batch-cost guardrail, and W5's `batch_agent_cap` example default was stale** (#95) — the
  glossary-pass Workflow template gained a preflight cost cap (`estimatedCalls = 3 * BATCHES.length +
  2` — precheck + dispatch + wait per batch, plus the fixed merge + verify pair) that refuses to
  dispatch with `{merged: false, reason: "batch-too-large", estimatedCalls, cap}` before spending any
  agent call, reading the SAME `engine.batch_agent_cap` field W5 uses, via a new `{{BATCH_AGENT_CAP}}`
  substitution token added to the glossary template. The shipped `profile.example.yml`
  `batch_agent_cap` default moved 1000 → 3500: W5's real formula is `1 + N*(10 + 7*max_fix_rounds)` =
  `1 + N*38` at the shipped `max_fix_rounds: 4`, so the old 1000 refused any mass batch over 26
  segments; 3500 admits the issue's own ~78-segment repro (`1 + 78*38 = 2965`) with headroom. Only
  fresh Step-0a copies pick up the new default; already-seeded projects are unaffected.
- **#91 capitalized-elision ambiguity, resolved resumability-safe** (#91 — supersedes the 1.3.2 "Not
  fixed" note) — `ELISION_RE` stays lowercase-article-only **by design**; it is deliberately NOT
  widened to catch capitalized sentence-initial elisions (a prior widening attempt split fixed
  compounds like `D'Artagnan` / `L'Oréal` / `L'Aquila` / `D'Annunzio` and was reverted). Instead,
  `bootstrap_names.py`'s `collect_candidates()` now DETECTS the ambiguity without touching the
  tokenizer: for `has_elision` languages, a capitalized single-token candidate whose lowercased-first-
  char form matches the language's own `ELISION_RE` and whose stripped remainder equals another
  candidate row's `name` is tagged `elision_ambiguous: true` with `elision_stripped_form`. This is
  detection-only (the IRON RULE — scripts surface candidates, never make an identity call).
  `glossary_batch_plan.py` force-includes such a row and its stripped-form target — bypassing the
  entire step-2 predicate, both the frequency floor AND `likely_name` (a sentence-initial capitalized
  elision is `likely_name=False`, so requiring it would silently kill #91's dominant case) — and
  co-locates the pair in one batch; the glossary-pass dispatch prompt then instructs the adjudicator to
  route an `elision_ambiguous` row to `review_queue` (naming its `elision_stripped_form`) unless it is
  positively confirmed a distinct entity. The mechanism reuses each language's own `ELISION_RE`
  verbatim, so it generalizes to `fr.json` and `it.json` with no new language-config key; the two
  fixed-compound regression tests stay green (they never split).

## 1.3.4 — 2026-07-11

Verse×footnote correctness cluster, round 2: the two residual discovery/deadlock bugs surfaced while
closing #105's render half (#118), plus a medium-severity multi-verse data-loss bug found
independently while working #117 (#119).

### Fixed

- **`render_obsidian.py`'s `_render_block` rendered only `verses[0]` for a `kind:"verse"` node** (#119)
  — any 2nd+ entry in a dedicated verse block's `verses[]` was silently dropped (whole content,
  `rendered` and `gloss` both), and a footnote cited only in a dropped entry left a dangling `[^N]:`
  definition with no in-body `[^N]`. `_render_block` now loops over every entry (one shared
  `seen_in_block`, empty skip-mode entries omitted, non-empty joined as separate blockquotes).
  Defense-in-depth: `validate_draft.py` rejects this carrier shape upstream today, but
  `render_obsidian.py` is built independently of `assemble.py` and must not truncate a hand-built or
  future NodeStream.
- **`verse_policy.mode: skip` footnote deadlock** (#118 item 1) — under skip a verse's content is
  voided (`{}`), so a footnote whose sole citation site is that content could never be discovered by
  any sentinel scan, yet `validate_draft.py` check 4 still required its draft text non-empty — an
  unsatisfiable deadlock that fatally raised `orphan_footnote_def` at whole-book assembly for a
  segment that passed per-segment validation. `assemble.py`'s orphan-definition check now exempts
  such a footnote when the manifest's mode-independent `verse.store` ground truth (its `fnrefs[]`
  **or** a direct `⟦FNREF_n⟧` scan of its `plain_text`) proves the footnote is verse-cited; it is
  stripped-not-rendered so nothing dangles, and any verse embedded in the exempted footnote's own
  definition is likewise marked referenced (else `orphan_verse` false-fatals it — including across an
  arbitrarily deep skip-voided `V001→fn1→V002→fn2→…` chain, which converges via the flat exemption
  loop with no worklist).
- **Nested footnote-in-verse-in-footnote-def not discovered** (#118 item 2) — a footnote cited only
  inside a verse that is itself embedded in *another* footnote's definition (arbitrary nesting depth)
  was invisible to both `segpack.py` (never handed to the translator) and `assemble.py` (never
  validated), leaking a raw `⟦FNREF_n⟧`. `segpack.py`'s embedded-verse discovery is now a
  worklist/fixed-point over a growing frontier (the segment's own blocks **plus** every discovered
  footnote's def-block); `assemble.py`'s two footnote-embeds-verse branches are de-duplicated into
  one shared recursive helper that recurses into each def-embedded verse's content for further nested
  footnotes. Nested footnotes are referenced-only: their text lands in the book-wide `footnotes[]`
  table but never in any node's `fnrefs`, and the inner verse is stripped-not-rendered — no dangling
  `[^n]:`, no leaked sentinel.
- **An embedded verse that is the entire content of a prose block rendered as inline italic, not a
  blockquote** (#118 item 3) — when a verse placeholder is the whole text of a `kind:"prose"` block
  (the dominant real case), `_render_block` now promotes it to a blockquote matching a `mount:"block"`
  verse's presentation. Narrowly scoped: prose only (never a heading, which keeps `## ` semantics),
  exactly one verse claim, and only when the original block text is nothing but the placeholder — a
  verse genuinely embedded mid-sentence keeps the compact-italic rendering (a blockquote can't sit
  mid-paragraph).

## 1.3.3 — 2026-07-11

Output-layer polish + first-run robustness patch: closes #98, #99, #104, and partially addresses
#105 (parts a and c) from the 2026-07-09 five-agent audit.

### Fixed

- **`diff_rendered_output.py`'s baseline reader used `str.splitlines()` while the writer splits on
  `"\n"` only** (#98) — a rendered line containing a Unicode line-boundary char (U+2028, U+2029,
  U+0085/NEL, U+000B/0C, U+001C–1E) made the render/diff acceptance gate report `mismatch` forever,
  and `--accept-baseline` re-froze a form the reader split differently on every subsequent run, so
  it never converged. `_read_baseline_lines` now mirrors the writer exactly (strip one trailing
  `\n`, then `split("\n")`, empty → `[]`).
- **`render_obsidian.py`'s entity-note filename de-duplicator compared exact relpath strings** (#99)
  — two canon `source_form`s that sanitize to stems differing only in case (`IVAN` vs `Ivan`) or
  Unicode normalization form (NFC vs NFD `café`) were treated as distinct and got no disambiguation
  suffix, silently clobbering one note on a case-/normalization-insensitive filesystem (macOS APFS,
  Windows) and destabilizing a baseline frozen on a different platform. `_dedupe_path` now folds on
  NFC-normalized casefold for membership while still returning the original, case-preserving path.
- **Undeclared Python 3.10 floor in `assemble.py`** (#104a) — `AssembleError.__init__`'s `reason:
  str | None = None` parameter annotation is runtime-evaluated (no `from __future__ import
  annotations` present), so it raised `TypeError` on import under Python ≤3.9 with no explanation.
  The annotation is now a quoted forward reference (`"str | None"`). A new AST-based drift-guard
  test (`python_floor_pep604_drift.test.py`) statically scans every shipped script for a future
  unquoted/unguarded PEP-604 union so this class of regression can't silently recur.
- **Un-preflighted `import yaml` in `render_obsidian.py`** (#104b) — every other third-party import
  across the plugin's scripts wraps in a try/except printing the house "install requirements.txt"
  message; `render_obsidian.py`'s was the lone exception, raising a raw `ModuleNotFoundError`
  traceback instead. Now wrapped like its siblings, and added to `dependency_preflight.test.py`'s
  coverage (10/10 scripts).
- **`final_audit.py`'s foreign-remainder stopword check was a no-op due to punctuation** (#105a) —
  `WORD_TOKEN_RE.sub(lambda m: m.group(0), t)` returned `t` unchanged, so a stopword adjacent to
  punctuation (`"fois,"`) never matched the stopword set and the WARN-only untranslated-run advisory
  under-counted. Tokens now strip outer Unicode-punctuation-category characters and NFC-normalize
  before the stopword comparison; the stopword set is NFC-normalized on load too, so both sides
  compare in the same form regardless of the input text's or the language config's normalization.
- **Double wikilink for a name appearing in both an inline verse and its host prose** (#105c) — each
  `_render_block` call now creates exactly one `seen_in_block` set and links the fully-composed
  block text (verse-then-prose or prose-then-verse) in a single trailing pass, instead of the inline
  verse and the surrounding prose linking independently with their own first-occurrence bookkeeping.
  `_render_verse_inline` is now a pure formatter (no longer takes a `linker`), fixing a latent
  display-order inconsistency as a side effect.

### Not fixed / follow-up filed

- **#105 parts (b) and the verse-footnote residuals** (skip-mode footnote deadlock, nested
  footnote-in-verse-in-footnote-def, embedded-verse footnote inline-vs-blockquote cosmetic) remain
  open — out of scope for this patch. Tracked in a dedicated follow-up issue; #105 stays open.
- **A pre-existing bug found while working this patch, not part of the original audit:**
  `_render_block`'s `kind == "verse"` branch renders only `verses[0]`, silently dropping any
  additional verses in the same dedicated verse block (`render_obsidian.py`). Filed as a new
  follow-up issue rather than folded into this patch, since it's unrelated to #98/#99/#104/#105.

## 1.3.2 — 2026-07-10

Bugfix release: closes three open issues (#89, #100, #102) from the 2026-07-09 five-agent audit.
#91 was investigated and found to conflict with an existing, deliberate design decision — see "Not
fixed" below.

### Fixed

- **`select_segments.py` regen hint named the wrong step for a stale `derivation_bundle_hash`** (#100) —
  the hint told operators to re-run `segpack.py`, which only ever copies `derivation_bundle_hash`
  verbatim from `canon.json` and never recomputes it, leaving the segment `blocked_needs_regeneration`
  forever. The hint (and the matching doc in `references/ledger-and-resumability.md`) now correctly
  names `bootstrap_names.py` and the W3/W3a glossary pass, which is what actually regenerates
  candidates and re-stamps the hash.
- **`language_smoke_report.py` never stripped `⟦FNREF_N⟧`/`⟦VERSE_…⟧` sentinels before candidate
  extraction or density scoring** (#89) — a sentinel-adjacent name (e.g. `Bouchard⟦FNREF_5⟧`) fused
  into a garbage candidate, inflating counts and able to flip a legitimate name to `found:false`,
  false-failing the mandatory W3 smoke gate; sentinel-heavy segments could also out-score a
  legitimate high-density segment during sample selection. Both call sites now strip sentinels first,
  before the word cap is applied.
- **`canon_validate.py` had no whole-file guard against a `source_form` present in both `entries{}`
  and `review_queue[]`** (#102) — the originally-reported bug (a name accepted in one glossary batch
  and re-queued by a later batch) was already fixed in 1.2.0's `_merge_batch`, but a hand-corrupted or
  otherwise not-batch-merged `canon.json` with the same overlap still passed both schema validation
  and `--verify-merged` silently. Both `_validate_whole_file` and `run_verify_merged` (the Workflow
  template's actual disk-independent trusted gate) now reject it.

### Not fixed

- **#91 — `ELISION_RE` splitting only lowercase `d'`/`l'`** was investigated: widening the article
  class to also match capitalized, sentence-initial elisions (`L'Enclos`) turned out to conflict with
  a deliberate, already-documented design decision (see `assets/languages/README.md`) protecting fixed
  proper-noun compounds that happen to start the same way — `D'Artagnan`, `L'Aquila`, `D'Annunzio`,
  `L'Oréal` — from being wrongly split into `Artagnan`, `Aquila`, etc. No code change ships for #91 in
  this release; it needs either a curated exception mechanism or a different resolution strategy,
  which is a larger design question than this bugfix round scoped for.

## 1.3.1 — 2026-07-10

Hardens two W1-adjacent authoring gates and closes a doc-prose leak: closes #94 and #103.

### Fixed

- **Unfilled bracket placeholders never rejected `scaffold_validate.py`'s W1 gate** (#94) — the hand-adapted
  `PLAN.md`/`style_bible.md`/`consistency_issues.md`/`translate_TASK.md`/`review_TASK.md`/`glossary_TASK.md`
  could still carry unfilled `[SOURCE LANGUAGE]`/`[TARGET LANGUAGE]`/`[PROJECT TITLE / AUTHOR / PERIOD --
  fill in]` placeholders past the scaffold check. A closed-list, whitespace-normalized bracket scan now
  fatally rejects each survivor by name, without risk of blocking legitimate hand-authored editorial
  brackets (`[NOTE]`, `[SIC]`, ...). The companion ERA/DOMAIN trap-string check gained a second,
  co-occurrence-based scan that also catches a separator-mangled or partially-deleted trap example, closing
  bypasses the original exact-substring check missed.
- **Two reference docs instructed the reader to read a non-shipped `historiettes-t3` path directly**
  (#103) — `orchestration-and-batching.md` and `assembly-and-output.md` carried leftover imperative
  "read `historiettes-t3/...` directly" clauses pointing at a private, unreachable origin-project file
  (the same leak class #77 fixed in script docstrings). Both now state the same provenance as a
  descriptive fact rather than an actionable instruction. `authoring_hygiene_drift.test.py`'s drift guard
  is extended with an independent, paragraph-scoped proximity check over `references/**/*.md` so this class
  can no longer recur silently in doc prose (the existing guard only ever scanned `.py` scripts).

## 1.3.0 — 2026-07-10

Verse×footnote correctness cluster: closes five open issues (#84, #92, #93, #96, #106) and the
render half of #105. The extractor now handles poems whose stanzas lack `.line` children and verses
nested in heading-wrapping `<div>`s; footnotes cited INSIDE a verse are recorded, carried through
segpack/validate/assemble, and rendered (previously dropped or left as a dangling definition); and a
shared verse×footnote fixture corpus exercises the full extractor→segpack→validate→assemble→render
chain across seven cross-product cases.

### Fixed

- **Body-top-level fallback verse left unmounted** (#92) — a poem at the body top level fell back to a
  `NavigableString` the body walk skipped, so the verse was never mounted and the extractor self-check
  failed closed. Orphan verse runs are now grouped by their outermost parent and, when that parent
  carries a chapter heading, normalized into standalone verse block(s); otherwise mounted embedded as
  before. (Also fixes a latent nested-`.stanza` double-registration in the same fallback path.)
- **Footnotes cited inside an embedded verse were never anchored** (#93) — footnote anchors inside an
  embedded verse were not recorded in the anchor index nor scanned by the fnref uniqueness self-checks,
  so a footnote quoted only within a poem was silently dropped. Post-mount anchor registration and the
  two fnref self-checks now scan the verse store's embedded entries (guarded against unmounted verses).
- **Verse-in-footnote no longer wedges a segment** (#96) — an embedded verse (verse-in-footnote) used to
  trigger a permanent, regeneration-proof `validate_draft` source defect. Segpack now threads verse
  `mount`/`n_line` and discovers footnotes cited inside embedded verses, so the segment converges.
- **`.stanza` blocks without `.line` children** (#84) — the verse line count (`n_line`) now counts DOM
  line units (bare `<p>`, mixed, and inline-markup stanzas) rather than raw text fragments, consistent
  with the 1.2.0 verse-text preservation fix.
- **Renderer dropped a footnote cited in a standalone verse** (#105, render half) — a footnote cited
  inside a `mount=block` verse rendered its verse but dropped the footnote marker, leaving a dangling
  `[^n]:` definition with no `[^n]` reference. The verse renderer now converts the footnote sentinel so
  the reference and its definition both render. (Embedded-mount verse footnotes already rendered via the
  prose substitution path.)
- **Verse content no longer silently swallows a malformed footnote sentinel** — the verse-content
  sentinel scanner now fails closed (bracket-balance check + reject-unrecognized-sentinel) exactly like
  the block-text scanner, so a stray or truncated sentinel inside a verse aborts the build instead of
  leaking verbatim into the published output.

### Added

- **Shared verse×footnote fixture corpus** (#106) — `tests/verse_footnote_corpus.py` plus per-layer test
  files drive seven minimal EPUB fixtures (prose / embedded-verse / verse-in-footnote-def /
  standalone-verse crossed with footnote presence) through the real
  extractor→segpack→validate→assemble→render chain, regression-locking the cluster end-to-end.

### Changed

- **Extractor contract version 1 → 2** — the extractor now emits verse `mount`/`n_line` and records
  embedded-verse footnote anchors; the contract-version marker and its consumers are bumped in lockstep
  (pinned by the contract drift test), and the pinned self-check region hash is recomputed for the
  extended self-checks.

## 1.2.0 — 2026-07-10

Combined bugfix + hardening release closing eight open issues (#82–#88, #90, #97): two
EPUB-extraction correctness bugs, a name-extractor tokenizer fix, a documentation correction, a
new managed post-extraction gate that makes the extractor's self-checks tamper-evident, and a
Workflow-orchestration reliability pass over the review and glossary-pass mechanisms.

### Workflow-orchestration reliability (#87, #88, #90, #97)

Four bugs surfaced by a five-agent audit attacked the plugin's Workflow templates directly — the
engine of its primary deliverable, W3's glossary pass and W5's mass-translate:

- **#87 (schema shape):** `agent({schema})` requires a top-level `object` — the tool-use API
  never accepts a top-level `oneOf`/`allOf`/`anyOf`/`array`. The glossary batch's
  `CANON_BATCH_SCHEMA` (a top-level `array`) blocked every W3 dispatch outright with an HTTP 400;
  three top-level-`oneOf` schemas in `mass-translate-wf.template.js` blocked W5 the same way.
  Fixed by flattening every agent-facing literal to a relaxed-union `type:"object"` (branch
  discrimination moves to the still-strong on-disk schemas plus a new exact-key-set JS guard at
  each consume site — see `references/workflow-schema-validation.md`) and deleting
  `CANON_BATCH_SCHEMA` outright, since the glossary batch dispatch no longer carries a schema at
  all.
- **#97 (unbounded await):** review and the glossary-pass batch call were bare, unbounded
  `await agent()` calls to codex — a forwarder-detached job could hang the whole run indefinitely
  with zero ambient monitoring, the same failure class a real 11-teammate incident on the source
  project already proved. Only translate's original fire-and-forget-plus-bounded-poll shape was
  ever actually bounded. Fixed by generalizing that shape to review and the glossary batch:
  schema-less codex DISPATCH (writes an atomic, `{{RUN_ID}}`-scoped artifact) → bounded Claude WAIT
  poll → schema-validated Claude CONSUME (reads the artifact back). This closes the
  forwarder-hollow-return and detached-job-hang modes for every codex work-call; a *synchronous*
  codex block-and-hang on the DISPATCH `await` itself remains a residual translate already carried
  (see `references/gotchas.md`).
- **#88 (false-green merge):** the glossary batch's codex return was banked into `canon.json`
  directly, with no independent disk verification. Fixed by adding a disk-independent
  `canon_validate.py --verify-merged` step (schema `CANON_VERIFY_SCHEMA`, new) that re-derives,
  from a fresh read, that every fragment's items actually landed correctly — accept/queue
  disposition-aware, with queued-then-accepted supersession correctly treated as a pass, not a
  false-red.
- **#90 (concurrent-batch race):** concurrent glossary batches wrote to the same shared
  `canon.json`, risking silently lost updates. Fixed by fragment-per-batch (each batch writes to
  its own run-scoped path, never `canon.json` directly) plus exactly one serialized final
  `canon_validate.py --merge-batches` call as the sole writer.

`{{RUN_ID}}` is a new substitution token in both Workflow templates, resolved once per run
(`resumeFromRunId` on a verified resume, else fresh) and validated against a path-safe allowlist.
Every fire-and-forget artifact (`draft.json`, `review.json`, glossary fragments) is scoped by it
via a `dispatch_token` field, checked not just at the readiness poll but at every later point the
artifact's bytes are consumed or committed for a durable decision (the reviewer's own read, the
convergence ledger write, the batch-final completeness check) — closing a stale-straggler-from-an-
interrupted-run class of bug the pre-1.2.0 design had no mechanism to detect. Whether to resume a
run at all is now gated by a dedicated `input_digest` (args + resolved profile substitutions +
per-segment cache keys + template/script/schema bundle hashes) computed by a new deterministic
pre-workflow script, `resume_setup.py` — a digest mismatch forces a fresh run with no
`resumeFromRunId`, never a silent replay of stale cached results.

**Caveats, stated plainly:** this reliability pass is new plugin hardening, not itself
pilot-proven — the shipped test suite locks the mechanism's contracts (schema shape, token
enforcement, digest gating, estimator formula) but a real end-to-end pilot run against a live
project is still the honest gate before treating it as fully load-bearing, the same posture
`references/gotchas.md` already applies to the rest of the orchestration subsystem. The
synchronous codex block-and-hang residual named above under #97 is real and not closed by this
release.

### Fixed
- **Tokenizer trailing-apostrophe fusion in both `assets/scripts/bootstrap_names.py` and `assets/scripts/language_smoke_report.py`** (#82) — `TOKEN_RE` absorbed a trailing apostrophe into a name token, so a stray apostrophe after a name (e.g. `Fiona’ George`) fused into one bogus candidate. Connectors (`'`, `’`, `‑`, `-`) are now matched only *between* letters, so a trailing apostrophe is left unconsumed (the name is stripped, not fused); internal elision/hyphen forms (`d'Effiat`, `Saint-Simon`, `aujourd'hui`) are unaffected. The two extractor copies' `TOKEN_RE` are now pinned byte-identical by a drift guard.
- **A wrapper `<div>` around a chapter `<h2>` collapsed the whole body file to front-matter** (#83) — the body walk matched `<h2>` only as a direct child, so a `<div>`-wrapped heading was never seen, misclassifying the entire file as front-matter and silently dropping its paragraphs. Heading-bearing wrappers are now flattened (recursively, handling multi-level nesting and multiple chapters per wrapper) so the heading and its sibling body content are each classified in document order; the direct-child path stays byte-identical. A new BLOCKING self-check `body_files_yield_segments` fails closed if a body-bearing source yields zero body segments.
- **Verse stanzas made of bare `<p>`s lost their text** (#84) — a `.stanza` whose lines are bare `<p>`s (no `.line` class) produced empty `verse_plain` text, dropping the poem's words and any footnote-anchor sentinel carried in them. Each stanza now falls back to its own `get_text` the same way the no-stanza branch already did (behavior-identical when `.line` children are present). A new BLOCKING self-check `verse_plain_text_nonempty` fails closed on any empty verse entry.
- **`agent({schema})` shapes that could never pass the tool-use API** (#87) — `CANON_BATCH_SCHEMA` (top-level `array`) blocked every glossary-pass dispatch; `REVIEW_ARTIFACT_SCHEMA`/`LEDGER_WRITE_SCHEMA`/`LEDGER_MERGE_SCHEMA` (each a top-level `oneOf`) blocked mass-translate. Every agent-facing literal is now a flat, relaxed-union `type:"object"`; `CANON_BATCH_SCHEMA` is deleted outright. On-disk schemas keep their strong `oneOf`/`array` shapes unchanged; a new exact-key-set JS guard at each consume site re-establishes the branch discrimination the flat literal can't express on its own. See `references/workflow-schema-validation.md`.
- **Unbounded `await agent()` on review and the glossary-pass batch call** (#97) — a forwarder-detached codex job on either call could hang the whole run indefinitely with no visible failure, the same class of incident that already forced translate onto a bounded shape. Both now follow translate's proven dispatch → bounded-poll → disk-read pattern: a schema-less codex DISPATCH writes an atomic, `{{RUN_ID}}`-scoped artifact; a bounded Claude WAIT poll gates progress; a schema-validated Claude CONSUME call reads the result back. A synchronous codex block-and-hang on the DISPATCH `await` itself remains an accepted residual, same as translate's.
- **Glossary batch results banked into `canon.json` with no independent verification** (#88) — a codex return was trusted directly, with no disk re-check that the merge actually landed. `canon_validate.py --verify-merged` (new) re-derives, from a fresh disk read, that every fragment's items are correctly present in `canon.json` by disposition (accepted vs. queued, with queued-then-accepted supersession correctly treated as a pass).
- **Concurrent glossary batches racing on a shared `canon.json`** (#90) — silently lost updates were possible when multiple batches wrote toward the same file. Each batch now writes only to its own run-scoped fragment; exactly one serialized `canon_validate.py --merge-batches` call is the sole writer of `canon.json` per glossary pass.

### Added
- **`assets/scripts/validate_extraction.py`** (#86) — a managed post-extraction gate, run from the plugin's own install path (never copied into the durable project, so it cannot be adapted or weakened). It independently re-derives all 11 manifest-derivable self-check invariants from `manifest.json` and pins the extractor's self-check region by SHA-1, so a hand-weakened, deleted, or bypassed self-check can no longer certify a false-green extraction. Wired into `SKILL.md` as a MANDATORY post-extraction step — the pipeline advances only on its exit 0.
- **Tamper-evident self-check region in `assets/templates/extract.py.template`** (#86) — `run_self_checks` is wrapped in `# BEGIN/END SELF-CHECK REGION` sentinels pinned by `validate_extraction.py`, with a drift test (`tests/extractor_selfcheck_hash_drift.test.py`) proving the shipped region matches the pinned hash.

### Changed
- **Corrected a misleading `assets/profile.example.yml` comment** (#85) — the `plain_text.verse_detection`/`footnotes` `CHOOSE_` placeholders were documented as sitting "inertly" while another `source.format` is active; in fact Step 0's placeholder scan is format-agnostic by design and fatally rejects any surviving `CHOOSE_` value regardless of `source.format`. The comment now states the sentinels must be replaced even in an inactive block. The strict scan itself (a deliberate, name-tested backstop) is unchanged.
- **Documented the self-check region as off-limits during adaptation** (#86) — `references/source-format-adapters/gutenberg-epub.md` and `references/false-green-gate.md` now name "editing a self-check to reach green" as a false-green anti-pattern, direct genuine gaps to a plugin issue, and describe the new `validate_extraction.py` gate as the hard guarantee.

## 1.1.2 — 2026-07-09

Follow-up from #80 (deferred from the #79/1.1.1 review): closes two remaining gaps in the
deterministic proper-noun candidate extractor. No change to the translation loop's behavior.

### Fixed
- **Wrapper-masked sentence boundary in both `assets/scripts/language_smoke_report.py` and `assets/scripts/bootstrap_names.py`** (#80) — the extractor's token back-scan skipped whitespace only, so a real sentence terminator (`.`/`!`/`?`) hidden behind an intervening closing/opening quote or bracket before the next capitalized token was missed, fusing two proper nouns from adjacent sentences into one bogus candidate. The back-scan now also skips transparent wrapper punctuation (`()[]{}'’‘“«`, a set kept disjoint from `TERMINATORS`) to find the terminator behind it, so `"'I saw Fiona.' George nodded."`, `"(Fiona.) George arrived."`, and `"Fiona. « George arriva. »"` split into two candidates instead of `"Fiona George"`. The closing quotes that *do* end a sentence (`"` `”` `»`) stay in `TERMINATORS` and keep acting as boundaries. (A name wrapped at the very start of the text, e.g. `"(Fiona.) …"`, is now correctly classified sentence-initial — its `mid_sentence` flag flips to `False`; a recall-ranking nuance in `bootstrap_names.py`, not a verdict change.)
- **`bootstrap_names.py` parity with the 1.1.1 `language_smoke_report.py` fixes** (#80) — its `TERMINATORS` was the smaller `.!?:»`; it now matches `language_smoke_report.py`'s full `.!?:;»"”…—―`, gaining the em-dash (`—`, U+2014) / horizontal-bar (`―`, U+2015) dialogue-line delimiter that dominates French/Russian/Spanish literary prose, so `"Fiona. — George arriva."` splits correctly. Its particle-continuation branch also no longer bridges a terminator sitting before the trailing name (`"parla Fiona du. George arriva."` no longer fuses into `"Fiona du George"`).

### Added
- Boundary regression tests for the wrapper/guillemet/em-dash cases in both `tests/language_smoke_report.test.py` and `tests/bootstrap_names.test.py`, plus a `tokenize`-level back-scan assertion in the latter.
- **`tests/extractor_terminators_drift.test.py`** — cross-file drift guard pinning `TERMINATORS` and the new wrapper set byte-identical across `language_smoke_report.py` and `bootstrap_names.py`, so the two independent copies of the extractor can't silently diverge again (the exact drift that produced #80).

## 1.1.1 — 2026-07-09

Post-ship cleanup from two skill/plugin audits plus the open issue tracker: fixes a doc/executability contradiction and a pre-existing name-extraction bug, scrubs residual non-shipped-origin authoring directives, de-flakes the ledger e2e test, and adds drift-guards — with a cosmetic manifest tidy. No change to the translation loop's behavior beyond the name-extraction bugfix.

### Fixed
- **`SKILL.md` "who translates" contradiction** — intake step 4 now states plainly that v1 hard-locks both translate and review to `codex:codex-rescue`, with Claude only fixing/orchestrating/verifying; the "Claude translates" arrangements are reframed as the durable/reusable pattern a future engine-per-role knob would unlock, not a v1 choice. Aligned `references/operating-constellation.md` to match.
- **Cross-sentence proper-noun fusion in `assets/scripts/language_smoke_report.py`** (#78) — `extract_candidate_names()` no longer bridges a sentence boundary, so `"Fiona. George arrived quietly."` yields two candidates instead of a bogus `"Fiona George"`. The boundary guard also recognizes em-dash / horizontal-bar dialogue delimiters (`—`/`―` — the dominant sentence boundary in French/Russian/Spanish literary prose, so `"Fiona. — George arriva."` splits correctly), and the particle-continuation branch no longer bridges a terminator sitting before its trailing name. (Same sentence-boundary invariant as the already-shipped `bootstrap_names.py` guard; a title+surname straddling a period, e.g. `"Mr. Smith"`, splits identically — pre-existing behavior, not a new regression.) Removed the now-passing `xfail` on the pinned regression test.
- **Stale `{{PLUGIN_ROOT}}` in a `references/canon-and-glossary.md` error-message quote** — corrected the documented `canon_validate.py` dependency-preflight message to the bare `pip install -r requirements.txt` it actually prints, matching the code and the new Step-0 `{{PLUGIN_ROOT}}` invariant (pre-existing low-severity doc/code drift, surfaced by pre-release review).
- **Undefined `{{PLUGIN_ROOT}}` placeholder** — defined once at Step 0 as the plugin install directory (`${CLAUDE_PLUGIN_ROOT}` under Claude Code), resolving all doc/script uses; corrected a stale quoted error-string in `SKILL.md` to match `profile_validate.py`'s runtime-resolved output.
- **Residual non-shipped-origin directives** (#77) — dropped the "read it directly before changing this one" clauses pointing at the private `historiettes-t3` origin from `canon_adjudication_audit.py` and `final_audit.py` docstrings; kept the provenance line and redirected each to its in-repo authority.
- **Flaky `tests/ledger_e2e_acceptance.test.py`** (#61) — removed a racy wall-clock `timestamp` inequality assert (it tied when both writes landed in the same second-resolution tick); the surviving content checks already prove the full-replace property. `references/gotchas.md` §13 marked resolved.

### Added
- **`tests/seg_validate_drift.test.py`** (#63) — drift-guard pinning the security-critical `_SEG_ID_RE` literal byte-identical across all 8 scripts that carry it, plus the canonical `validate_seg` body across its identical group, with `review_artifact_check.py`'s documented intentional divergence explicitly exempted.
- **`tests/authoring_hygiene_drift.test.py`** — guards against re-introducing a non-shipped-origin "read it directly before changing this one" directive in any shipped script's docstring or comments, including when the phrase is hard-wrapped across a `#`-comment continuation.
- Positive-needle prose assertions in `tests/skill_prose_present.test.py` for the corrected translate/review default wording and the Step-0 `{{PLUGIN_ROOT}}` definition.

### Changed
- Trimmed the `plugin.json` / `marketplace.json` description to a tighter form (kept byte-identical between the two).
- Scoped the `SKILL.md` pre-read mandate to the six hard-rule references plus the actually-resolved source/output adapter, deferring the inert assembly/Obsidian docs to the step that needs them.

## 1.1.0 — 2026-07-08

Adds optional **book assembly + output rendering**, lifting the 1.0.0 non-goal "v1 delivers converged per-segment drafts, not an assembled book". Converged drafts can now be assembled and rendered into an output target behind a deterministic render/diff acceptance gate. All new machinery is stdlib-first, self-anchored, one-JSON-line-on-stdout under the shared 0/1/2 exit convention, `python3 -O`-clean, and fully covered by the pytest suite (grown to 676+ tests from 500+). New; not yet pilot-proven at scale.

### Added
- `assets/scripts/assemble.py` — fail-closed 3-source assembler: joins `manifest.json` (structure + global order) + per-segment `*.draft.json` (content with inline footnote/verse sentinels) + `segpack_*.json` (placeholder↔verse-id map), gated on `runs/ledger.json` (every in-scope segment `converged` + sha1-matched). Emits a target-agnostic NodeStream + anchor map to `out/.assembled/`, then dispatches the resolved output adapter. Fatals as one JSON line with a machine-matchable `reason`.
- `assets/scripts/render_obsidian.py` — the `obsidian` output adapter: renders the NodeStream into an Obsidian vault — chapter notes with folder-qualified `[[People/…|display]]` wikilinks (first occurrence per block), footnotes, verse blocks with literal glosses, and one entity note per `canon.json` entry (canon IS the entity registry; no separate entity model). Fail-closed against symlink data-loss: an ownership-marker gate + no-follow atomic writes refuse to clean or write into a directory this adapter doesn't own or that is reached via a planted symlink (`out_dir`, its parent, the leaf, and the marker all guarded).
- `assets/scripts/output_resolve.py` — target-agnostic resolution of the output adapter + `out_dir` from `profile.yml`'s `output.*`, shared by assemble and diff so neither reimplements the rule.
- `assets/scripts/diff_rendered_output.py` — deterministic render/diff acceptance gate: `--accept-baseline` freezes the current render as a `.baseline/` snapshot; a later re-render is diffed line-for-line and must match (exit 0). Same symlink-safe write discipline for the baseline.
- `assets/schemas/` + `references/output-target-adapters/` — NodeStream / adapter-result schema shapes plus normative adapter docs (`assembly-and-output.md`, `obsidian.md`).
- `SKILL.md` + `profile.example.yml` + `profile.schema.json` — `output.v1_scope: assembled_book` wiring and the `output.*` config surface (adapter target, destination, wikilinks + category-folder options).
- `tests/` — `assemble` / `output_resolve` / `render_obsidian` / `diff_rendered_output` / adapter-schema-shape suites, including adversarial symlink-safety regressions (marker + parent-`out/` + leaf-dir symlink refusal, no-follow atomic writes, non-UTF-8 marker rejection, cross-adapter marker rejection).

## 1.0.0 — 2026-07-08

- Initial build: engine-loop skill (codex-translate → false-green gate → codex-review → Claude-fix), frozen name/realia canon, configurable verse policy, ledger-based resumability, `gutenberg_epub`/`plain_text`/`custom` source adapters.
- Ledger-fragment/cache-key/derivation-state machinery, `plain_text` and `custom` adapters are new plugin hardening, not yet pilot-proven at scale — see `references/gotchas.md`.
- `canon_adjudication_audit.py` — new opt-in rollout gate that turns canon human-review requirements (duplicate source forms, existing merges, candidate missed-merge pairs, un-drained `review_queue[]` items) into a persisted, machine-checkable record (`canon_adjudications.json`); generalized from historiettes-t3's `audit_human_adjudications.py` onto the plugin's entity-less canon model. New plugin hardening, not yet pilot-proven at scale.
- Published as the initial release with the experimental-status caveats above documented in the marketplace README. Two release-gate items remain **open post-release follow-ups** (see plan §19 item 5): de-flaking `tests/ledger_e2e_acceptance.test.py` (a known timestamp-race — see `references/gotchas.md` §13) and a real second-project pilot run to promote the starter-preset language/adapter configs from experimental to proven.
