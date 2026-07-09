# Changelog

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
