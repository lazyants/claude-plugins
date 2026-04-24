# Known Issues

Tracked caveats and planned coverage for `ai-cli-optout`. Resolved blockers are in [`CHANGELOG.md`](./CHANGELOG.md), not here.

## Documented caveats (not bugs)

### C1 — Apple Intelligence no-op on non-MDM Macs
`com.apple.applicationaccess allow*` keys are MDM-only; `defaults write` silently succeeds but does not change state on unmanaged Macs. The skill surfaces these as `manual_only_items[]` with the System Settings path — that's the current supported workaround.

### C2 — GrowthBook kill-switch disables features, not just telemetry
`DISABLE_TELEMETRY=1` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` flip the same GrowthBook kill-switch that controls feature entitlements — so any Claude Code capability delivered behind a remote feature flag silently disappears once either var is set.

- **Confirmed:** Opus 4.6 1M-context default model on Max / Team / Enterprise plans. Upstream: <https://github.com/anthropics/claude-code/issues/34178>.
- **Confirmed (2026-04-24):** `/remote-control` slash command. Responds with `Unknown command: /remote-control` while `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` is set in `~/.claude/settings.json` `env` block; returns after that single key is removed and Claude Code is restarted. Shell-level `env -u` is insufficient — `settings.json` values override. Remove the JSON key, then restart.

  **Isolated attribution (2026-04-24):** BOTH `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` **and** `DISABLE_TELEMETRY=1` are independently sufficient to break `/remote-control`. Verified by bisection:
  1. Only `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` set → `/remote-control` gone.
  2. Only the 4 narrower vars set (`DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY`, `DISABLE_FEEDBACK_COMMAND`) + `skipWebFetchPreflight` — `/remote-control` still gone.
  3. Removing only `DISABLE_TELEMETRY` from step 2 → `/remote-control` returns.

  Safe narrow opt-out that keeps `/remote-control`: `DISABLE_ERROR_REPORTING`, `DISABLE_FEEDBACK_COMMAND`, `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY`, `skipWebFetchPreflight`. The remaining 3 env vars + `skipWebFetchPreflight` were verified harmless to `/remote-control` in step 3.

  **Mitigation in skill (v1.0.3+):** both `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` and both `DISABLE_TELEMETRY` edits in `vendors/anthropic.json` now carry `requires_confirmation: true` with a `tradeoff_note` citing `/remote-control` and Opus 1M. `SKILL.md` step 3(b) surfaces the note and asks the user for explicit confirmation before applying. Users who decline get the safe narrow opt-out automatically; users who accept have been shown the cost. A schema invariant in `tests/vendor-schema.test.sh` blocks any future `requires_confirmation: true` edit from shipping without a populated `tradeoff_note`.

GrowthBook-gated features may fail similarly in the future. The skill now surfaces this trade-off before applying either var (see the `TRADE-OFF` line in `vendors/anthropic.json` `notes[]`). Users who need a disappearing feature should narrow the opt-out: drop these two vars and keep `DISABLE_ERROR_REPORTING`, `DISABLE_FEEDBACK_COMMAND`, `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY` — not known / documented to touch GrowthBook.

### C3 — Windows Home: `AllowTelemetry=0` unsupported
Floor is `Required`. `windows-privacy.json` reflects this; no fix planned.

## Planned coverage additions

- **Vercel Claude Code plugin** — `VERCEL_PLUGIN_TELEMETRY=off` env var disables bash-command telemetry to `telemetry.vercel.com`. Needs detection that distinguishes the Vercel plugin from other Claude Code plugins; tracked for a follow-up minor release.
- **Windsurf / Codeium**, **Zed**, **Ollama** — under consideration; PRs welcome.
- **Linux privacy surfaces** (flatpak reports, GNOME / KDE feedback agents) — not scoped.
