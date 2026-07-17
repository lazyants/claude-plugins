# Version bumps, adding a plugin, and adding a vendor

A plugin's version lives in FOUR places. A bump that touches only the first two silently ships a mismatch (a real published mismatch has happened: `plugin.json` + `CHANGELOG` at the new version while `README` + `marketplace.json` stayed one patch behind).

## The four surfaces — update ALL on every version bump

1. `plugins/<name>/.claude-plugin/plugin.json` → `"version"`
2. `.claude-plugin/marketplace.json` → that plugin's `"version"` entry. NOTE: the file also has a repo-level `metadata.version` — that is SEPARATE and versioning is per-plugin, not repo-wide. A plain version bump does NOT touch `metadata.version`.
3. `CHANGELOG.md` → new top `## [<name> X.Y.Z] — YYYY-MM-DD` entry.
   - **Per-plugin CHANGELOG trap:** `literary-translator` keeps its OWN `plugins/literary-translator/CHANGELOG.md` (format `## X.Y.Z — DATE`, no `[name]` prefix). The ROOT `CHANGELOG.md` is LEGACY for it, frozen at its `## [literary-translator 1.1.0]` entry — editing the root one for a littrans bump is WRONG. Before bumping, check `git log -1 -- CHANGELOG.md` vs `git log -1 -- plugins/<name>/CHANGELOG.md` to find which surface a plugin actually uses.
4. `README.md` → BOTH the table row version cell AND the `## \`<name>\` — vX.Y.Z` section header.

### README anchor-slug gotcha (surface #4)

The README table link is `[\`<name>\`](#<name>--vXYZ)`. GitHub slugifies the header `## \`enduser-handbook\` — v1.0.3` to `enduser-handbook--v103`: backticks + em-dash dropped, spaces→`-`, dots stripped → a DOUBLE hyphen before `v`. Bumping the displayed version means editing the anchor digits too, or the table link 404s. The `## ` header must wrap the name in BACKTICKS (`## \`<name>\` — vX.Y.Z`) like every sibling — slugify strips them so the anchor still resolves, but omitting them renders the name as plain text (a review-bot drift finding).

### Surface #4 has a hidden fifth layer: the section's BODY PROSE

The section's intro paragraph and "What it covers" bullet list are easy to leave frozen at an older version while the header says the new one — this satisfies the row/header/anchor triplet yet is still stale. Update the body prose separately whenever the plugin's feature set actually changed: a bugfix or feature release should get at least one new sentence + bullet. A pure metadata/hardening bump with truly no user-visible behavior change can legitimately skip the prose update.

## Source of truth + verification

- When the surfaces disagree, `plugin.json` + `CHANGELOG` are authoritative (the bump author updates those first; `README` + `marketplace.json` are the laggards). Verify all four match before committing.
- **A commit message's "N surfaces synced" claim is NOT proof — grep each surface independently on `origin/main`.** Real releases have shipped with a surface silently skipped (e.g. `plugin.json`/`marketplace.json`/`README` bumped but the CHANGELOG entry never added). If you find a prior release skipped a surface, backfill that missing entry in the same commit that stacks your new bump.
- The shared surfaces (`README.md` plugin table + `.claude-plugin/marketplace.json`) are also MERGE-CONFLICT surfaces — every plugin's PR edits them. See `merge-and-review-bot.md` for predicting and resolving the parallel-PR conflict.
- Four surfaces + merge-to-main is the *publish*, not the *ship* — the installed copy stays stale until you refresh it. See `publishing-and-cli.md`.

## ADDING a new plugin (not just a bump): same 4 surfaces PLUS `metadata.version`

- New `marketplace.json` plugin entry, written in the siblings' EXPANDED one-key-per-line format.
- New README table row + `## \`<name>\` — v1.0.0` section.
- New `## [<name> 1.0.0] — YYYY-MM-DD` CHANGELOG entry (top, newest-first).
- The plugin's own `plugin.json`.
- **AND bump `.claude-plugin/marketplace.json` `metadata.version` by +0.1.0.** It increments one MINOR per NEW PLUGIN (1 plugin→1.1.0, 2→1.2.0, 3→1.3.0, 4→1.4.0); per-plugin VERSION bumps do NOT touch it. Trap: a run of recent commits all showing the SAME `metadata.version` misleads you into "it never bumps" — those were version-bumps; check the commits that actually ADDED a plugin.
- Plugin-add commit convention: `feat(<plugin>): … (1.0.0)` via a PR (squash-merge, review-bot; no per-plugin git tag required).

## Adding or renaming an ai-cli-optout VENDOR

After editing `vendors/<name>.json`, sweep `README` + `KNOWN_ISSUES.md` for FOUR things:

1. **Adjacent-name collisions — same brand ≠ same product; don't merge two entries.** Example: "Vercel" is two distinct products that must COEXIST — **Vercel CLI** (the `vercel` binary, opted out via env, first-class vendor) and the **Vercel Claude Code plugin** (auto-instruments bash and posts every command string to `telemetry.vercel.com`; opt-out `VERCEL_PLUGIN_TELEMETRY=off`; NOT a first-class vendor, tracked in `KNOWN_ISSUES.md`). A README refresh that keeps only one "Vercel" bullet silently deletes the pointer to the other's env var.
2. **Prose vendor count** — the "One skill, N vendors" line must match the table row count.
3. **Trade-off visibility in the table's `Kind` cell** — if any edit has `requires_confirmation: true`, surface it in the cell (e.g. `*(2 edits confirmation-gated — see warnings)*`). A bare "settings.json + env" hides the trade-off.
4. **Stale planned-coverage entries** — if the new vendor resolves a `KNOWN_ISSUES.md` bullet, update/remove it in the SAME commit.

Also, when authoring the vendor JSON itself, fetch MULTIPLE of the vendor's doc pages, not just the first — authoring from page 1 alone once omitted the opt-out env var.

Canonical text for this sweep lives at `plugins/ai-cli-optout/CONTRIBUTING.md` under "README sweep when adding or renaming a vendor" — when the lesson evolves, update it there, not here.
