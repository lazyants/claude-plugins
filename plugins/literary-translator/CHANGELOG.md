# Changelog

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
