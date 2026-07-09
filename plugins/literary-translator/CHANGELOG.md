# Changelog

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
