# Changelog

## 1.0.0 — 2026-07-08

- Initial build: engine-loop skill (codex-translate → false-green gate → codex-review → Claude-fix), frozen name/realia canon, configurable verse policy, ledger-based resumability, `gutenberg_epub`/`plain_text`/`custom` source adapters.
- Ledger-fragment/cache-key/derivation-state machinery, `plain_text` and `custom` adapters are new plugin hardening, not yet pilot-proven at scale — see `references/gotchas.md`.
- `canon_adjudication_audit.py` — new opt-in rollout gate that turns canon human-review requirements (duplicate source forms, existing merges, candidate missed-merge pairs, un-drained `review_queue[]` items) into a persisted, machine-checkable record (`canon_adjudications.json`); generalized from historiettes-t3's `audit_human_adjudications.py` onto the plugin's entity-less canon model. New plugin hardening, not yet pilot-proven at scale.
- Published as the initial release with the experimental-status caveats above documented in the marketplace README. Two release-gate items remain **open post-release follow-ups** (see plan §19 item 5): de-flaking `tests/ledger_e2e_acceptance.test.py` (a known timestamp-race — see `references/gotchas.md` §13) and a real second-project pilot run to promote the starter-preset language/adapter configs from experimental to proven.
