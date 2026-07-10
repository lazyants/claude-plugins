# Changelog

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
