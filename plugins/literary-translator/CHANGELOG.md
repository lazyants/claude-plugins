# Changelog

## 1.15.2 — 2026-07-23

Two related proper-noun-extraction bug fixes for Hebrew source text. Closes #282. Closes #283.

### Fixed — ASCII `"` acronym connector between two Hebrew letters (#282)
- `TOKEN_RE` treated ASCII `"` (U+0022) purely as a `TERMINATORS` sentence-closer, never as a
  connector, even though real Hebrew corpora overwhelmingly spell an internal-acronym gershayim
  with the ASCII quote rather than the dedicated glyph (measured: 3,815+ `"` vs zero `״` in the
  SSK vol.2 corpus). A Hebrew acronym like `מוהרנ"ת` therefore split into two tokens at
  tokenization time, and pass 2's own `TERMINATORS`-boundary refusal then blocked the trie walk
  from ever bridging them — dropping the candidate entirely (0 of 592 real occurrences; 26
  inventory keys, 655 occurrences lost total). `"` is now also a connector, but only when it
  provably sits between two Hebrew letters — the lookbehind proves the actual BASE letter (not
  just the adjacent character) via a bounded union of letter-plus-up-to-4-stacked-marks
  alternatives, since Python's stdlib `re` has no variable-width lookbehind and a naive
  single-character check would wrongly fuse a non-Hebrew letter that merely happens to be followed
  by a stray Hebrew combining mark. The condition is purely lexical -- a Hebrew base letter,
  optionally followed by up to four Hebrew marks, on the left, and a Hebrew letter immediately
  on the right (never a mark on the right side) -- not acronym-aware -- **codex round 3
  correction, wording tightened round 4**: a directly-quoted single Hebrew word
  immediately abutting another Hebrew word with no surrounding whitespace also fuses (e.g. `"מוהרנ"` touching
  the next word), a disclosed, vanishingly-rare residual (quoted words virtually always have
  surrounding whitespace/punctuation in real prose). Every other use of `"`, including a real
  Latin dialogue quote or a normally-spaced Hebrew quote, is unaffected.

### Fixed — ASCII hyphen/apostrophe/quote connector-twin fold, Hebrew-scoped (#283)
- `fold_match_key`'s unit-splitting (`NAME_CONNECTORS`) only recognized maqaf/geresh/gershayim,
  not their ASCII/Latin twins — even though `TOKEN_RE` already fuses `-`/`‑`/`'`/`’`/`"` into
  one token exactly like it fuses the Hebrew forms (the last of those, `"`, purely when it sits
  between two Hebrew letters per the #282 fix above -- **codex round 3 correction**: a lexical
  condition, not acronym-detection, so it also fires for two ordinary adjacent Hebrew words
  abutting a quote with no whitespace, e.g. `שלום"עולם`). A Hebrew compound spelled with the
  ASCII hyphen (`הבעל-שם-טוב`) never matched a maqaf-joined or space-joined inventory
  spelling, silently dropping the book's single most-frequent name's dominant surface form
  (78 occurrences) — and an ASCII-quoted acronym fused by the #282 fix (`מוהרנ"ת`)
  likewise never matched its gershayim- or space-joined equivalent. `NAME_CONNECTORS` itself
  is unchanged — a literal widening there was rejected: it flips two pinned Latin
  non-regression tests, letting a hyphen/apostrophe-written Latin inventory entry match
  space-separated text. Instead, a new, separate fold-time split applies the same five
  ASCII/Latin connector twins, but only when both neighbors are Hebrew-block letters —
  `Jean-Baptiste`/`O'Brien`/`Ångstrom` are provably unaffected.

### Migration
Editing `bootstrap_names.py` (both fixes touch it) flips `derivation_bundle_hash` — every
already-converged segment (not just ones referencing specific canon entries) routes to
`blocked_needs_regeneration`: rerun W3/W3a (bootstrap names, then glossary merge or
`--restamp-derivation`, then segpack) before re-translating -- W2 (source extraction) is not
required. For a mature, zero-candidate project, use the 1.15.0
`canon_validate.py --restamp-derivation` escape, then rerun `segpack.py`. No live Hebrew project
has converged yet, so this is currently payable, not a bricking risk today.
Editing `language_smoke_report.py` (both fixes also touch it — it has an identical, independently
buggy inventory route) is not a cache-key member — no re-translation is forced directly, but any
in-flight/interrupted run starts fresh on upgrade, and its own bytes flip
`smoke_report_contract_hash`, forcing a fresh W3 language-smoke re-run.

## 1.15.1 — 2026-07-23

Bug fix. Closes #308.

### Fixed — wait/precheck sentinel comparison no longer false-rejects a decorated reply (#308)

- Seven consume sites across the three workflow templates (`mass-translate-wf.template.js`, `glossary-pass-wf.template.js`, `skeptic-pass-wf.template.js`) gated on a whole-string exact match of a low-effort wait/precheck agent's free-text reply against a bare sentinel (`READY <seg>`, `PRESENT <i>`, `DRAFT_MISSING <seg>`). When the agent decorated the mandated sentinel with a prose preamble — observed in the 1.15.0 W5 smoke, e.g. `"The poll confirmed the review artifact is ready (exit 0).\n\nREADY seg03"` — the exact match failed and a **completed** review/translate/batch was mislabeled a timeout (`review-timeout`/`translate-timeout`) or a present fragment triggered a redundant regeneration, even though the underlying work had genuinely succeeded.
- All seven sites now route through one `sentinelVerdict(reply, okSentinel, failSentinel)` helper, mirrored byte-for-byte across all three templates: a reply is accepted iff no line anywhere in it equals the failure sentinel, AND its own last non-empty line equals the success sentinel exactly. Requiring the success sentinel to be the reply's *final* line (not just any line) means a reply that quotes the sentinel and then explicitly disavows it is still rejected, not accepted. The failure-sentinel scan still covers every line, so fail-priority on a contradictory reply is unchanged from before. This closes the false-negative dual of #228, which converted these same sites from substring checks to whole-string exact match specifically to kill a different false-positive (a `TIMEOUT` reply substring-matching a `READY` check); both directions now stay closed simultaneously. Prompts are unchanged — agents are still instructed to return exactly the bare sentinel line; the parser merely tolerates decoration around it.

### Migration

No source data, canon, or ledger content changes. Three separate hash/digest domains are touched by editing the templates, each with a distinct, bounded consequence:

1. **Per-segment cache staleness (mass only).** `mass-translate-wf.template.js` and `glossary-pass-wf.template.js` are both `PLUGIN_BUNDLE_MEMBERS` (`cache_key.py`), so this release flips `plugin_bundle_hash`. Previously-converged **mass** segments' stored cache keys no longer match and route to `stale`/re-translate at the next Step-0a refresh. No derivation regen and no mature-project brick — the same migration class as 1.14.1's `codex_job.py` edit.
2. **Run-level resume-identity invalidation (mass AND glossary).** That same `plugin_bundle_hash` is also an unconditional input to `resume_setup.py`'s run-level `compute_input_digest()` for both `kind="mass"` and `kind="glossary"` — it is not conditional on kind. So this release also invalidates resuming any in-flight, not-yet-complete **glossary** run, not only mass: `resolve_run()` mints a fresh RUN_ID instead of matching the existing input digest, restarting that run's resume bookkeeping from scratch (glossary fragments already written to disk are unaffected content-wise, but the run loses its resume identity).
3. **Skeptic run-identity invalidation (separate domain).** `skeptic-pass-wf.template.js` is edited too and, although it is in neither `PLUGIN_BUNDLE_MEMBERS` nor `ORCHESTRATION_BUNDLE_MEMBERS`, `skeptic_setup.py` reads the skeptic template's own bytes directly (`SKEPTIC_TEMPLATE_FILENAME = "skeptic-pass-wf.template.js"`) and folds them into its own dedicated `compute_skeptic_input_digest()`. So this release independently forces a fresh skeptic RUN_ID too — a third, separate resume domain from (1) and (2).

No live or mature project is affected by any of the above today — only throwaway smoke roots exist, and the pending SSK vol.2 re-run is not yet started and will scaffold fresh on this release. These three consequences are priced here for completeness and for future runs, not because this release forces an active migration.

## 1.15.0 — 2026-07-22

Found by the plugin's first live end-to-end W5 run. Three of the five fixes are consume-site correctness bugs where a gate rejected honest input, and the last two supply a recovery path for a project-bricking state that 1.9.0/1.10.0 already armed in the field. Closes #289. Closes #290. Closes #291. Closes #292. Closes #193.

### Fixed — W5 no longer reports converged segments as failed (#289)

- `mass-translate-wf.template.js`'s ledger and review-artifact guards tested for the PRESENCE of an optional field where they meant to test for EVIDENCE of failure. The flat agent-facing schemas are deliberate unions of the success and failure branches, so they declare `exit_code`/`error`/`stderr` (and `mismatch_detail`) as fillable — and an agent that has just run `ledger_update.py` truthfully returns `exit_code: 0`. `FAILURE_ONLY_KEYS.some((k) => k in raw)` then read that proof of success as proof of failure. In the first live W5 run all three segments converged and merged correctly on disk while the workflow reported two of them `ledger-write-failed` and the batch `ledger-merge-failed`; the only segment that passed was the one whose agent happened to omit the field, so the gate's verdict was a coin flip on a key nobody asked for.
- The three consume-site guards (`ledgerWriteSucceeded`, `ledgerMergeSucceeded`, and the review-artifact check, now `artifactCheckMatched`) route through one `hasFailureEvidence()` helper reading one `NO_FAILURE_EVIDENCE` table of per-field benign-value predicates: `exit_code` is benign only at exactly `0`, a text field only at exactly `""` — never by interpreting its content, since judging whether `"none"` means "fine" is natural-language interpretation and does not belong in a gate. A field with no table entry counts as evidence, so the failure direction stays closed — but silently, which is the #289 symptom itself, so `tests/ledger_confirmation_schema.test.py` now asserts the table's key set covers every declared evidence key and fails the build on an evidence key with no row. That coverage assertion is itself pinned in both directions (it catches a row-less key, and accepts a new key that declares one).
- The class lock is stated as its inverse rather than as a blacklist of spellings. An earlier form searched for `"<field>" in <obj>` — a quoted literal — which would NOT have caught #289 itself, since two of the three sites tested a loop variable (`FAILURE_ONLY_KEYS.some((k) => k in raw)`). The lock now asserts the JS `in` operator appears in the template's *code* only inside `hasFailureEvidence()`, so every spelling and every site, including one that does not exist yet, fails by construction. The bridge test that cross-checks the JS literals against their Python mirror no longer `pytest.skip`s when extraction fails — on a machine without node it was the only non-node layer, so a skip there meant a fully green run could hide arbitrary drift.
- `hasOnlyKeys()` is checked against each schema's full declared key set rather than the success keys alone — rejecting an already-value-checked `exit_code: 0` as an "unexpected key" was the same defect wearing a second hat. A key neither branch declares is still fatal.
- The review-artifact guard additionally gained the allowed-key check its two siblings always had: an undeclared key previously sailed through as a match. That is a false-GREEN, fixed in the same pass as the false-RED.

### Added — `canon_validate.py --init`, bootstrapping canon.json on the zero-candidate SKIP path (#290)

- W3's `{"no_new_candidates": true, "batches": []}` SKIP branch is the one path that never reaches the glossary merge — and the merge was the only writer of `canon.json` — so every uncased-script project (Hebrew/Yiddish/Arabic/CJK) that ships no `name_inventory` reached `candidates: 0` by construction, followed SKILL.md exactly, and died at W3a with `FATAL: canon.json not found`. New `--init` writes an empty-but-stamped `canon.json` (`entries: {}`, `review_queue: []`) through the same `_stamp_write_verify` every merge uses, so its `generation_hashes` are genuine `cache_key.py` values — exactly what `segpack.py` copies into every pack — not a stub. SKILL.md's W3 SKIP branch and `references/canon-and-glossary.md` now carry the command.
- `--init` is create-only: an existing `canon.json` is left byte-untouched and reported `"created": false`, exit 0, never re-stamped — re-stamping would clear `select_segments.py`'s derivation-state gate without regenerating anything. It rejects `--batch`/`--expect-source-forms-file` rather than silently ignoring them.

### Fixed — a merge that changes nothing no longer moves `generation_hashes` (#291, #292)

- `canon.json`'s two `generation_hashes` are a claim that its CONTENT was produced under a given derivation state; `segpack.py` copies them into every pack and `select_segments.py`'s gate compares that copy against a fresh `cache_key.py` value. `_stamp_write_verify` re-stamped unconditionally, so any merge advanced that claim — letting a content-free merge clear `blocked_needs_regeneration` with nothing regenerated. The hole was not limited to an empty fragment: `_merge_batch` treats an identical re-submission as a silent no-op, so a fully populated fragment of already-merged items changed nothing either while still reporting `merged_accepted > 0`. The check now keys on whether the merged document differs from disk, covering `--merge-batches` and legacy `--batch` alike; a `review_queue[]`-only change counts as a change, and a missing/empty prior stamp is re-stamped rather than preserved. All four writing modes now report `generation_hashes_restamped` with one meaning, and `--restamp-derivation` keeps `generation_hashes_changed` as its extra detail — unified while these fields are still unreleased and have no consumers.
- `--batch` alongside `--check-batch`/`--merge-batches`/`--init`/`--restamp-derivation` is now a usage error instead of being silently ignored while returning `"success": true` (#292). The two legitimate shapes — `--batch` alone, and under `--verify-merged` — are unchanged; no shipped caller passed the rejected combination. The three MODE-CONFLICT guards — mutual exclusion, `--batch` compatibility, and fragment-flag rejection — are now comprehensions over one introspectable `MODE_SPECS` table, replacing three parallel guards, a hand-maintained subset tuple of them, and a `!= "--verify-merged"` magic string. A bidirectional drift test fails on a parser flag missing from the table (which would get no guards at all) and on a table row with no parser flag (a typo that would silently never match), so the table row cannot be forgotten. It does **not** make a new mode a one-line change: adding one is still three edits — a table row, an `add_argument()`, and a dispatch branch — and two of the five cross-flag guards in `main()` remain hardcoded, both expressing a requires-relation between two named flags (`--verify-merged` requires `--batch`; `--batch` is repeatable only under it) rather than a per-mode property. Each would be a column with exactly one meaningful row, so they are left out deliberately and the code says so.
- The legacy bare-`--batch` merge is now a `MODE_SPECS` row too, carrying `dest=None` since no single flag selects it. Outside the table it selected no spec and therefore escaped every table-driven guard — which is exactly how it came to accept `--expect-source-forms-file`, ignore it, and return `{"success": true}` with coverage never enforced. That is a worse shape than #292, because the ignored flag is a *verification* flag: the caller is told coverage was checked when it was not. It is now rejected with the same message every other refusing mode gives, and closing it added **zero** new guards. The drift test pins that exactly one dest-less row exists, so a later row cannot dodge completeness checking by omitting its dest.

### Added — `--restamp-derivation`, a sanctioned escape from `blocked_needs_regeneration` (#193)

- Required by the fix above: #193 records `canon_validate.py --merge-batches <empty-batch.json>` as its only, unsanctioned escape, and #291 removes it. Same operation, now explicit, Pass-1 validated, refusing when there is no canon (pointing at `--init`), and reporting which fields moved.
- `select_segments.py`'s `blocked_needs_regeneration` hint now names it for **both** glossary-pass-routed derivation-state fields (`derivation_bundle_hash` and `particle_config_hash`), generated from one template rather than two hand-maintained strings — that drift is exactly why the escape reached one field and not the other. Ordered so `segpack.py` runs last, since it copies canon.json's stamp forward rather than recomputing it. `references/ledger-and-resumability.md`'s "self-clearing once the operator reruns the regeneration step" claim is corrected for the zero-candidate case.
- The full blocked → `--restamp-derivation` → `segpack.py` → cleared recovery is pinned end to end by `tests/derivation_gate_recovery_e2e.test.py`, driving the real scripts and the real `cache_key.py` with no stub in the chain, including a step asserting the pre-1.15.0 empty-merge bypass no longer works.
- **Remedial, not preventative.** `bootstrap_names.py` changed in 1.9.0 and both `DERIVATION_BUNDLE_MEMBERS` changed again in 1.10.0, so `derivation_bundle_hash` has already flipped twice in the field — a mature zero-candidate project upgrading past either release was already blocked with no sanctioned recovery. This release supplies the way out; it does not prevent the state.
- **Limitation, stated rather than hidden:** `--restamp-derivation` is an operator-trusted override — it does not itself verify the "no new candidates left to merge" precondition it documents. Enforcing that would couple `canon_validate.py` to `glossary_batch_plan.py`'s selection logic, which this codebase deliberately keeps apart.

### Migration

`canon_validate.py` and `mass-translate-wf.template.js` are both `PLUGIN_BUNDLE_MEMBERS` (`cache_key.py:103-117`), so this release moves `plugin_bundle_hash` — a 15-field `cache_key` composite member. At the next Step-0a bundle refresh, every converged **mass** segment of an in-flight project routes to `stale` and re-translates only. It does **not** route to `blocked_needs_regeneration`: neither `DERIVATION_BUNDLE_MEMBERS` file (`bootstrap_names.py`, `segpack.py`) is touched, so this release arms no regeneration brick of its own — and `select_segments.py` is not a bundle member at all, so the hint change carries no cache cost. This is the standard, unavoidable cost of editing any plugin-bundle script.

## 1.14.1 — 2026-07-22

Fixes a rare wasted-work edge in the W5 codex-job driver: a codex attempt that completed just as the finalize tail was exhausted used to be silently discarded and re-launched from scratch on the next dispatch. Closes #213.

### Fixed — preserve a completed-but-unvalidated attempt at the finalize tail (#213)

- `codex_job.py`'s `CodexJob.run()` treated "`completed` but no budget left to validate" the same as any other non-promotable outcome and let `finalize()` `_silent_remove` the completed attempt outright. Because every run mints a fresh random `inv`-scoped attempt path, the discarded work was unrecoverable and the next W5 dispatch re-launched codex from scratch.
- The tail-exhausted case now atomically defers the completed attempt into a deterministic per-seg/kind pending slot (`segments/.att_pending.<seg>.<draft|review>.json`) via a new `_defer_attempt()`. A new pre-launch `adopt_pending()` step re-validates any pending attempt through the SAME kind-specific candidate gates used for a live attempt — which enforce `--expect-token` against the candidate's own `dispatch_token`, so a stale attempt from a different run is rejected — and only on a full pass atomically promotes it to canonical. Never-promote-unvalidated is preserved; a gate that could not run (exhausted budget) leaves the pending intact rather than deleting recoverable work, and every non-promotion outcome still falls through to a fresh `launch()` (no starvation). A non-regular entry forged onto the deterministic slot is cleared so it cannot permanently block the deferral.
- No consumer-visible CLI or schema change. Fixes a rare, bounded efficiency edge — never a correctness bug; the driver always failed safe, it just paid an avoidable re-launch.

### Migration

`codex_job.py` is a `PLUGIN_BUNDLE_MEMBERS` script (`cache_key.py:113`), so this edit moves `plugin_bundle_hash` — a 15-field `cache_key` composite member. At the next Step-0a bundle refresh, every converged **mass** segment of an in-flight project routes to `stale` and re-translates only. This is NOT `blocked_needs_regeneration`: `codex_job.py` is plugin-bundle, not derivation-bundle, so there is no W2/W3 regeneration and no mature-project brick. This is the standard, unavoidable cost of editing any plugin-bundle script — not a "zero migration" change.

## 1.14.0 — 2026-07-22

Finishes #210 and advances #202. Custom extractors can now declare per-heading-type markdown levels, an undeclared heading type fails loudly at W2 instead of silently shipping mis-titled files, and `output-coverage` gains an opt-in within-cohort ratio-outlier surfacer. Closes #210. Refs #202 — **this release does not close it**; see the stated limitation below.

### Added — heading levels (#210)

- New optional `manifest.heading_levels` maps a declared heading type to a markdown level 1-6; previously every heading rendered as `##` regardless of type. Keys are cross-validated against `heading_types ∪ {"HEAD"}`, and that guard runs independently in both `assemble.py` and the W2 gate — `assemble.py` is reachable on a resumed project, so it cannot rely on W2 having run.
- `render_obsidian.py` renders the declared level, with a defensive clamp to 2 for anything malformed or absent (`0`, `7`, `"3"`, `True`, `None`, missing). Output is **byte-identical** for any project that does not declare `heading_levels`.
- Every assembled node now carries a `level` key — including the frontback-regenerate placeholder — so the BlockNode contract in `references/assembly-and-output.md` holds for every node a consumer can encounter.

### Added — fail-loud undeclared heading types (#210)

- Extraction now FAILS when `manifest.json` omits `heading_types` entirely **and** at least one block type is heading-shaped (`HEADING|TITLE|CHAPTER|SECTION|PART|SIMAN|PEREK|H1-H6`, case-insensitive, full-match). The error names the offending types and both remedies.
- The opt-out is an explicit `heading_types: []` — a declared stance that this source has no heading blocks.
- **Shipped adapters are unaffected**: `HEAD` deliberately does not match the heading-shaped pattern, so a Gutenberg-shaped manifest with no `heading_types` key still passes. This is a property of the pattern itself, not of a fixture.

### Added — output-coverage ratio-outlier surfacer (Refs #202)

- New **opt-in** `validation.conservation_ratio_band`. Absent or `null` means the lane does not run and `output-coverage` behaves exactly as in 1.11.0.
- Groups blocks into cohorts by raw manifest type and compares each block only against **its own cohort's** measured out/source word-ratio distribution — never a cross-language-pair or project-wide absolute threshold, a shape this plugin refuses on record. WARN-only, exit 0; it surfaces candidates for the W5/W7 reviewer and never decides that a block is truncated.
- Reports `coverage_distribution` per cohort with a full exclusion accounting (`excluded_floor_flagged`, `excluded_below_min_source_words`, `excluded_zero_output`) so a reader can always see how much of a cohort the statistic did not cover.
- **Stated limitation — this does NOT close #202.** A within-cohort fence measures deviation *from* a cohort, never truncation *of* one: if every block in a cohort is truncated equally, the median is the truncated ratio and nothing is an outlier. Detecting that needs a reference outside the audited population, and neither candidate exists here — per-language-pair priors are refused by this plugin, and prose blocks carry no translation-invariant anchor. The limitation is pinned by a characterization test, not merely documented.

### Fixed — CHANGELOG closure claims

- The 1.7.0 and 1.11.0 entries claimed to close #210 and #202. Neither was closed: GitHub binds a `Closes` keyword to the **first** issue reference only, so trailing references in a `Closes #a, #b, #c` list never auto-close. Both entries are corrected in place with a dated note.

## 1.13.0 — 2026-07-22

Honest close-out of #206/#207: retires the inline linker's homonym-collision tiebreak from production and reconciles the doc claims it left stale. Closes #206, #207.

### Fixed — collision de-linking now applies to every obsidian render (#207)

- `render_obsidian.py`'s `render()` now calls `build_entity_index(..., collision_delink=True)` regardless of `output.adapter_config.obsidian.mentions_section.enabled` — decoupled from the appendix flag, but still gated on `output.target == "obsidian"` like the rest of this adapter (the non-obsidian `custom` CLI path is unchanged). A `canonical_target_form` shared by ≥2 canon entries is NEVER inline-linked on any obsidian render, appendix on or off — the shortest-`source_form` tiebreak that used to pick a (possibly wrong) winner is gone from production, including on the `enabled: false` opt-out path, which previously still misattributed.
- `build_entity_index()`'s signature and its `collision_delink=False` default are unchanged — the tiebreak survives only as that default's documented behavior for direct callers and tests; the renderer no longer reaches it.
- **Migration:** this edit flips `render_obsidian.py`'s `render_version`. For every appendix-on project (the default, and every known real project) rendered output is byte-identical to 1.11.0 — the diff gate reports a stale-version WARNING (exit 0), a routine re-accept, not a content mismatch. Only an appendix-OFF (`enabled: false`) project with an actual homonym collision sees a genuine content diff (de-linked instead of misattributed) and needs a reviewed `--force-accept-baseline`. No re-translation in either case.

### Added — orphaned-homonym diagnostic (#207)

- `validate_backlinks.py`'s exit-neutral `collisions[]` diagnostic gains `orphaned_owners: [source_form]` — the subset of a collision's owners with ≥1 expected source occurrence that have NO backlink anywhere in the rendered vault: neither an actually-emitted inline `[[…]]` link nor a `## Mentions` appendix link. Both link types are read from the ACTUAL emitted segment notes (the inline side reuses the same rendered-note scan as the inline advisory; the appendix side reuses the coverage scan), never from linker eligibility — so an owner whose target is eligible in `build_entity_index` but never occurs in the rendered prose (no link emitted) is correctly flagged, while one whose target the renderer actually inline-links, or that is de-linked as a genuine ≥2-owner NFC-exact collision, or a `sense_translated` name never auto-linked, is classified by what the vault actually contains. (An owner the gate groups into a collision only by case-fold — e.g. `"Peter"` vs `"peter"`, distinct to the renderer — is judged on its own emitted links, not the fold.) `owners` itself is unchanged (still a list of raw `source_form` strings); `warnings` stays `== len(missing)` (Metric-1 remains the sole `warnings` source — this is an additive, exit-neutral rollup, not a second warning source). On the `enabled: false` path the gate still short-circuits to a disabled report and computes nothing, so a homonym orphaned there is not surfaced by this diagnostic — see `references/output-target-adapters/obsidian.md`.

### Docs (#206)

- Reconciled every stale doc claim that predated the appendix-flag-independent collision de-linking: `obsidian.md`'s tiebreak section, its "backlinks are the occurrence index" framing (native inline backlinks are now documented as a best-effort, verbatim-same-surface reading affordance; the default-on source-anchored `## Mentions` section is the authoritative, variant-immune, homonym-split occurrence index, verified by `validate_backlinks.py`), the `enabled: false` byte-identical claim, and the collision-de-linking-is-predicate-gated claim — plus matching corrections in `assembly-and-output.md`, `profile.example.yml`, and `output-target-adapters/README.md`.

## 1.12.0 — 2026-07-22

Makes the codex reasoning **effort** a real, per-project knob and adds an optional codex **model** pin — both first-class `profile.yml` inputs under `engine:`, threaded into the W5 (mass translate/review/fix) and glossary codex dispatch, and folded into the run's cache/resume identity so a re-run at a different `(model, effort)` no longer silently reuses artifacts. Before this, `engine.effort` was schema-pinned to `const: "high"`, the profile value never actually reached codex (the W5 driver launched with `codex_job.py`'s own `--effort high` default, and the prose openers hard-coded `"Effort: high."`), and there was no model knob at all. Closes #197.

### Added

- **`engine.effort` is now a configurable enum** — `low | medium | high | xhigh` (default `high`). Excludes `max` (codex-companion's whitelist rejects it — it throws) and `none`/`minimal` (nonsensical for accuracy work). The value is threaded into every accuracy-bearing codex call it nominally governs: the W5 codex translate/review dispatch (as a real `codex_job.py --effort` flag AND the `"Effort: …"` task openers), the Claude fix step (its `agent()` effort opt + opener), and the glossary codex pass (its opener + forwarder opt).
- **Optional `engine.model`** — a codex model id (e.g. `gpt-5.3-codex`), pinned per project and threaded to the **W5 codex dispatch only** via `codex_job.py --model` (single-quoted; shell-safe pattern `^[A-Za-z0-9][A-Za-z0-9._-]*$`; omitted entirely when unset → codex uses its config default). Not threaded to the glossary/fix paths, where a codex model id is not meaningful (they run through `codex:codex-rescue` / plain-Claude `agent()`, whose model is the Claude forwarder's, not codex's).

### Changed

- **`agent_config_hash` (cache key) now folds `{effort, max_fix_rounds, model}`** (was `{effort, max_fix_rounds}`). The folded model is the **requested** value (unset → `null`): codex-companion never reports the *resolved* model, so provenance is honestly the requested pin, not the effective one.
- **The glossary resume-integrity digest now carries `effort`** (`resume_setup.SUBST_FIELDS` gains `effort`). The mass digest already carried effort/model via the per-segment cache key; `model` is deliberately NOT added to `SUBST_FIELDS`, because the glossary pass has no model knob — adding it would encode a false dependency.
- `profile.example.yml`'s `engine.effort` comment is corrected (it previously overstated that the driver already passed the profile value as a real `--effort` flag) and gains a commented `# model:` example.

### Security

- **Sink-side allowlist guard for `EFFORT`/`MODEL`** (`mass-translate-wf.template.js`). Both values are spliced into the detached `codex_job.py` dispatch shell command — `EFFORT` unquoted, `MODEL` single-quoted — so the workflow now re-validates each against its schema allowlist (`^(low|medium|high|xhigh)$` and `^[A-Za-z0-9][A-Za-z0-9._-]*$`; empty `MODEL` = unset) and throws before building any command, mirroring the existing `SEG_ID_RE` / `parseDisp` guards. This makes shell-safety independent of whether `profile.yml`'s Step-0 schema validation actually ran, closing the resume-path / hand-edited-profile bypass window. Covered by real node-execution tests in `seg_safety_source_and_workflow.test.py`.

### Migration

Any existing project fully re-translates on upgrade — and this is forced regardless of the identity change: (1) `agent_config_hash` gains `model`, moving a GLOBAL `cache_key` field → every converged segment stales; (2) both the W5 and glossary templates are `PLUGIN_BUNDLE_MEMBERS`, so editing them moves `plugin_bundle_hash` (also GLOBAL) → every segment stales anyway (subsuming #1); (3) `SUBST_FIELDS` gains `effort`, so the resume digest value changes (moved digest, nothing extra to run). No delivered or in-flight project is affected: the frozen books are never re-run, and any new run starts from a clean scaffold on this code.

## 1.11.0 — 2026-07-19

Closes the A-C6 residual 1.10.0 shipped knowingly: the evidence/adjudication chain is now mark/connector-insensitive too, so the `## Mentions` appendix and the evidence chain finally agree on what counts as the same Hebrew name. Alongside it: exact-match sentinel comparison across the two remaining workflow templates, a new content-conservation gate, and a required style-contract slot for embedded third-language text. Closes #243, #228, #196, #203. Advances #202 (the output-coverage structural half; the per-block anti-truncation half was deferred).

> **Correction (1.12.0):** this line originally read "Closes … #202 (output-coverage half) …". The parenthetical was accurate about scope but the `Closes` verb was not — #202 was never closed and is still open after 1.12.0.

### Fixed — fold-aware evidence chain (#243)

- `occ_index.production_occurrences()` and `occ_index.index_manifest()` now compare `bootstrap_names.fold_match_key()` on BOTH sides, as `occurrence_targets.py` has since 1.10.0. `evidence_verify._group_production_spans_by_name()` — an independent second copy of the same grouping, and the hot path production actually takes — was folded in the same change; its docstring's "so the two never drift" promise is now enforced by a parity test rather than by convention.
- **Fail-closed on ambiguity, never double-filing.** Folding is many-to-one: distinct raw canon forms (pointed/unpointed, maqaf/space) can share one match key. A span whose folded key covers two or more distinct canon source forms is credited to NEITHER, mirroring `occurrence_targets.build()`'s `unresolved_homonyms` route. Emitted values stay unfolded — the raw `source_form` and the raw pointed `quote` are untouched; folding is a lookup key only.
- **Two distinct universes, deliberately.** *Competitors* (who participates in ambiguity detection) is the union of `canon.json` entries and ALL `canon_senses.json` forms, split-only included — a split-only form is a competitor but never an output row. *Eligible-for-output* stays each consumer's own projection. Collisions are computed AFTER that projection, so a form colliding only with an out-of-scope entry keeps its ordinary counters.
- **New risk class `fold_collision`** (`skeptic_constants.py`, `suspicion-worklist.schema.json` enum — eight classes now). `suspicion_scan.build_worklist()` no longer silently combines a colliding form's block-origin and verse-origin occurrence counts (which disagreed in opposite directions: block occurrences zeroed, one physical verse span double-filed to both siblings); colliding forms skip occurrence collection entirely and route to an always-flagged bucket. One row per `source_form` — two colliding forms produce two rows.
- `skeptic_ready.py`'s `_evidence_failure_reason()`/`_coerce_record()` — the mandatory triage-coercion path, which reached `verify_evidence()` through its collision-unaware `production_spans_by_form is None` fallback — now fail a colliding form unconditionally. `run_verify_merged()` and `run_validate_fragment()` both PROJECT the competitor universe rather than merely accepting a canon path. `run_validate_fragment()` does a plain fresh read of `canon.json`/`canon_senses.json` (no H1 check, no aggregate visibility to reuse). `run_verify_merged()` instead reuses the exact `(state, bytes)` snapshot the H1 tamper check already captured for each of `canon.json` and `canon_senses.json` — resolved independently, since either stamp can be absent on its own — so the tamper comparison and the competitor projection can never disagree about which on-disk version they each describe; `--canon` is now actually passed by the validate-fragment branch, and both modes accept `--senses-path` (same `DEFAULT_SENSES_PATH`/`allow_absent` convention as `canon_adjudication_audit.py`). This is what makes a collision detectable when the two sibling forms land in DIFFERENT batches.
- **Freshness:** `compute_producer_input_digest()` gains `senses_bytes` (third, after `manifest_bytes`). `canon_senses.json` became an authoritative data input with this release, and without folding its bytes into the digest a curator editing a split-only form between scans would leave the digest unchanged and a stale competitor universe would be certified fresh. An absent sidecar (`b""`) and a schema-valid logically-empty one hash differently.
- **Tamper:** the H1 frozen-input tripwire gains a third stamp, `senses_sha256` (`skeptic-assignment.schema.json`, optional — older aggregates still validate). A sidecar mutated between `skeptic_setup.py` and verification now HALTs exactly as a mutated `canon.json` does.
- **The tripwire now covers every path that concludes a verdict, not just the merged one.** It was previously reachable only through `--verify-merged`; when no batch produced a ready fragment the pipeline returned an ordinary advisory `fragment-check-failed` and never called verification at all, so a frozen input tampered after stamping but before any fragment validated went unreported. The check is now hoisted into a shared `frozen_input_check()` and a new `skeptic_ready.py --check-frozen-inputs` mode (byte-only; tolerant of a missing or malformed aggregate), which `skeptic-pass-wf.template.js` runs unconditionally in its not-ready-batches branch *before* deciding the outcome — so the advisory result is unreachable while a tamper is present. The hash itself is now state-tagged, so an absent file, an empty regular file and a directory no longer share one digest. Stamper and verifier alike ultimately reduce to one function, `compute_frozen_input_hash_from_state(state, content)`, so the formula itself cannot drift — but WHEN and HOW each side reads the bytes it hashes differs, and the difference is load-bearing. The stamper always calls the core directly on the `(state, bytes)` pair it captured ONCE at derivation time; a later re-read at stamp-write time would instead record whatever is on disk when the aggregate is written rather than the snapshot the assignments and freshness check were derived from, silently adopting any mutation in that window as the trusted state. Verifiers are not uniform either: `canon.json` and `canon_senses.json` are now hashed from a captured snapshot too, the same one the downstream competitor-universe parse (above) goes on to reuse, so that tamper comparison and that parse can never independently disagree about which on-disk version each one describes. `manifest.json` now goes through that exact same gated capture, off the same table as the other two — it used to be wired in as a separate hand-written call that captured its own snapshot outside the read-failure tolerance gate, so a stamped `manifest.json` could still raise raw out of `--check-frozen-inputs` despite that mode's own "never crashes" contract; folding it into the shared table removes the capacity for a future fourth frozen input to reopen that gap the same way, since there is no longer a read path *inside `frozen_input_check()` itself* that skips the gate. (`_resolve_competitors()` still does its own deliberate fresh read of `canon.json`/`canon_senses.json` outside this gate when no H1-approved snapshot exists to reuse for that input — a separate, intentional fallback, not a gap this round left open.) A `FROZEN_INPUT_SPECS` entry (`skeptic_constants.py`) also only binds the stamper and this verifier table — `compute_producer_input_digest()`/`compute_skeptic_input_digest()` keep a fixed canon/manifest/senses signature unrelated to that tuple, so a future fourth frozen input still needs a hand-added parameter in both before a change to it can be hashed at all, not just get tamper-checked. That part of the gap shipped SILENT in an earlier round of this same release: a spec added to the tuple with no matching signature parameter (or vice versa) would leave both digests unchanged with no error at all. Both digest functions' BODIES (not their signatures, and not any call site) now build a `{key: (state, bytes)}` map from the parameters they already receive and assert its key set equals `FROZEN_INPUT_SPECS`'s key set before hashing anything, so that same mismatch now raises `AssertionError` instead — verified byte-identical to the prior formula on a fixed fixture, so this is a hardening, not a digest-compatibility change; no migration entry below.
- `canon_senses.py` hosts the shared `fold_collision_map()` helper — chosen because it already sits in both freshness closures and the plugin bundle, and is NOT a derivation-bundle member. Its `bootstrap_names` import is **lazy, inside the function**, so the module keeps its long-standing project-dependency-LEAF property: `normalize_form`/`load_senses` stay importable from any context, and the helper raises (never `sys.exit`) when `bootstrap_names.py` is absent.

### Fixed — sentinel exact-match (#228)

- Five remaining substring sentinel checks across `glossary-pass-wf.template.js` (precheck, wait) and `mass-translate-wf.template.js` (review-wait, fix-call, translate-wait) now compare the full discriminated reply exactly (`String(x).trim() === "READY " + seg`), as `skeptic-pass-wf.template.js` has since #227. A reply containing `NOT_READY` — or a `READY` line about a DIFFERENT segment, which these sites could not distinguish at all — no longer passes. Prompts unchanged; they already required the discriminated form.
- The fix-call site deliberately keeps its `!fx ||` disjunct: a falsy reply and `DRAFT_MISSING` are both routed to the #131 draft probe, and collapsing them would let a dead fix call read as an ordinary review round.
- `glossary-pass-wf.template.js` gains its first executing test harness (`tests/glossary_pipeline_e2e.test.py`) — every prior test of that template parsed its source as text, which is the blind spot that let #228 survive.

### Added — content conservation (#196, #202 output-coverage half)

- New `scripts/validate_conservation.py`, two subcommands. `wrapper-conservation` (HARD, after W2) checks a hand-wrapped source against a preserved pre-wrap baseline via an operator-declared provenance map, catching dropped, duplicated, reordered and hollowed spans; opt-in through `source.conservation` and a documented SKIP when unconfigured. `output-coverage` (WARN-only, W7/W9) flags hollowed output blocks against a non-empty source.
- v1 is an absolute FLOOR, not a length band. `validate_assembled.py`'s own docstring rejects per-block length bands because source/target ratios vary too wildly across language pairs; a calibrated band is deferred until a measured distribution exists. Population is `segments[].block_ids[]` only — matching `collect_source_markers()`, which naturally excludes frontback `omit` blocks and is safe for `regenerate` ones.

### Added — required third-language convention (#203)

- `style_bible.template.md` section E gains a required `embedded-third-language-convention` fill slot: romanize or translate, gloss format, how the kept original is set off. A project can no longer start with the convention undefined.

### Migration

Four items, all free on a fresh run. Two require an operator to actually do something on an existing project: **2** (re-run the skeptic pipeline) and **3** (hand-edit `style_bible.md`). The other two are automatic — nothing to run — but change what a digest reports, so don't read a moved number as a problem: **1** (the cache key) and **4** (the resume digest, via the schema hash). The two format-only digest changes under **2** (H1 stamps, `producer_input_digest`/`skeptic_input_digest`) are compatibility caveats on that action, not separate items — they explain why a leftover pre-upgrade skeptic artifact can't just be reused, not something extra to run.

1. **Full re-translation.** Both `mass-translate-wf.template.js` and `glossary-pass-wf.template.js` are `PLUGIN_BUNDLE_MEMBERS`, and `plugin_bundle_hash` is a `CACHE_KEY_FIELD_ORDER` field — every converged segment's cache key moves. Nothing to do by hand; the normal pipeline re-derives it.

2. **Re-run the suspicion scan, then the skeptic pass, then re-accept the canon audit** (only if this project has the opt-in skeptic pass enabled). `occ_index.py`/`evidence_verify.py`/`canon_senses.py` sit in the producer and skeptic code closures, and `senses_bytes` changes `producer_input_digest` directly.
   - *Digest format changed — don't try to reuse a leftover run.* Path state (absent / regular / irregular) now enters `producer_input_digest` AND `skeptic_input_digest` alongside each file's bytes, not just the H1 stamps: without it, a sidecar going from absent to a zero-byte regular file left the content `b""` in both cases, so both digests matched, a resume proceeded, and it then **overwrote** the H1 stamp with the new state — after which the tripwire compared the mutated file against its own freshly-rewritten stamp and found nothing wrong. Making state part of what "the same inputs" means is what stops resume laundering that mutation, but it also means a pre-upgrade worklist or skeptic run simply won't match post-upgrade and must be regenerated fresh — not a content change, just don't expect the old artifact to still validate.
   - *Don't cross-verify old run directories.* Running `--verify-merged` or the new `--check-frozen-inputs` against an OLD run directory's `assignments.json` (stamped with the pre-upgrade `compute_frozen_input_hash_from_state` shape) reads a **false** `frozen_input_mismatch: true` and FATAL-HALTs on a hash-format difference, not a genuine tamper. The normal pipeline cannot hit this on its own — `skeptic_setup.py` hashes the skeptic scripts into its own code closure, so upgrading already forces a fresh `RUN_ID` before any stale artifact reaches verification — the trap is only in a manual invocation against an old directory.
   - *The audit verdict can change WITHOUT any hash moving.* Folding turns previously-unverifiable evidence into passes and newly surfaces genuine homonyms — re-review the verdict regardless of what the digests say.

3. **Manually insert the new `style_bible.md` marker block.** `style_contract_hash` moved because the new required slot lives inside the `STYLE_CONTRACT_BEGIN/END` span, but the slot does NOT reach an existing project by re-scaffolding: `style_bible.md` is copied only when absent and never refreshed, so the marker block has to be added by hand.

4. **Nothing to run, but the resume digest itself will look different.** `profile.schema.json`, `suspicion-worklist.schema.json` and `skeptic-assignment.schema.json` all changed, and both `resume_setup._schemas_dir_hash()` and `skeptic_setup.py`'s own independent glob hash every `*.schema.json` — so the next ordinary resume/refresh reports a moved digest even on a project that never touches the skeptic pass at all. `cache_key.compute_schema_hash()` is unaffected (draft/review/segpack only), so this never forces a re-translation on its own.

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
- **#236 — marker-region parsing is now fence-aware and inline-code-aware.** A `<!-- lt:mentions:begin/end -->` marker pair (or a `[[wikilink]]`) living inside a ` ``` `/`~~~` fenced code block, or a backtick-quoted `` `[[wikilink]]` ``, no longer counts as a real region/link. **This is a two-way change on hand-edited vaults**: a forged fenced example stops satisfying coverage (`warnings` can go UP), and a real region that happens to sit alongside an unrelated fenced example stops being falsely rejected (`warnings` can go DOWN). Never affects a normally rendered vault (`render_obsidian.py` never emits fenced markers). Fence-delimiter recognition also honors CommonMark's ≤3-column indentation limit (tabs expand to the next 4-column stop): a 4+-column-indented ` ``` `/`~~~` line is indented code, never a fence, so it can neither open a spurious fence that masks a real marker pair sitting right after an indented code block, nor be mistaken for a delimiter inside an open one.
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

Delivery-gate hardening on the assemble/audit path, closing three real gaps found during the SSK vol.2 he→en remediation. Closes #208. Advances #210 (heading-shape output contract, but heading LEVEL and the undeclared-type gate both remained) and #202 (structural-completeness checks, but no per-block anti-truncation lane).

> **Correction (1.12.0):** this line originally read "Closes #208, #210, #202". Only #208 was actually closed — GitHub binds a `Closes` keyword to the FIRST issue reference only, so #210 and #202 were never auto-closed, and neither was finished in 1.7.0. #210 is closed by 1.12.0; #202 remains open.

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
