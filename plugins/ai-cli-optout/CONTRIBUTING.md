# Contributing to `ai-cli-optout`

Adding a vendor, fixing a false detection, extending the confirmation-gate schema, or changing baseline flags.

## Adding a new vendor

See `skills/ai-cli-optout/SKILL.md` §"Adding a new vendor" for the minimum file shape. Before you commit, the checklist below is mandatory.

### `detect_paths` — binary paths only, not shared config

The `detect_paths[]` array is an OR fallback for when `detect_cmd` is missing from `PATH` (e.g. VS Code pre-"Install 'code' command" step). It's the most error-prone field in a vendor JSON and the one most likely to false-positive in production.

**Rule:** every `detect_paths` entry must be created *only* by the target binary. Never list a path that a *sibling* product (editor vs. CLI, Toolbox vs. standalone, shared config dir) also creates.

**Why:** Config paths are advertised for user discovery, not as binary-presence proxies. Vendors routinely share directories across products (`~/.cursor/`, `~/.config/github-copilot/`, `~/Library/Application Support/JetBrains`). A skill that treats a shared config file as a detection signal reports "vendor X is installed" when only a sibling product is installed — false opt-out attempts follow. Real example: listing `~/.cursor/cli-config.json` in `cursor-cli.json` matched whenever the Cursor editor alone was installed (the editor writes that file too).

**How to apply:**
1. For each entry, ask: *could any other installed app — especially a sibling from the same vendor — create this exact path?* If yes, drop it.
2. Prefer binary locations in alternate install roots (`~/.local/bin/cursor-agent`, `/Applications/PhpStorm.app`, `~/Applications/PhpStorm.app` for JetBrains Toolbox) over config files.
3. Never list config files (`*.json`, `*.toml`) or data directories (`~/.vendor/cache`) — they persist past uninstall and cross pollinate.
4. If in doubt, drop to `detect_cmd` alone. False-negative (skill skips an installed vendor) is a smaller failure than false-positive (skill applies opt-outs for software that isn't there).

### Forbidden shared-ancestor paths

`tests/vendor-schema.test.sh` ships a `FORBIDDEN_DETECT_PATHS` array that blocks 16 known shared / ancestor paths: `~`, `~/Library`, `~/Library/Application Support`, `~/Library/Caches`, `~/Library/Preferences`, `~/.config`, `~/.cache`, `~/.local`, `~/.local/share`, JetBrains config roots, `/Applications`, `/Library`, `/opt`, `/usr/local`, `/etc`, `/var`, `/tmp`. If you hit a new sibling-config trap that isn't covered, add the ancestor to the array and note it in `KNOWN_ISSUES.md`.

### Confirmation gate — when to use it

If an edit you're adding could break user-visible Claude Code features (not just send telemetry), mark it with `"requires_confirmation": true` AND a non-empty `"tradeoff_note"`:

```json
{
  "key": "env.SOMETHING_RISKY",
  "value": "1",
  "disables": "What it disables",
  "requires_confirmation": true,
  "tradeoff_note": "Also breaks X feature / degrades Y mode. See KNOWN_ISSUES.md §Zn."
}
```

The schema-test invariant blocks `requires_confirmation` without a populated `tradeoff_note` — no silent-consent theater. Current confirmed cases live in `vendors/anthropic.json` for `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` and `DISABLE_TELEMETRY` (both break `/remote-control` and Opus 1M per upstream `anthropics/claude-code#34178`).

## Running tests

```
bash plugins/ai-cli-optout/tests/run-all.sh
```

Two files, ~300 assertions. Required to pass before any PR merges.

## Out of scope for PRs

- Changes to `~/.claude-bm/settings.json` semantics — the `-bm` profile is a user-specific convention (bypass mode), not a distribution target.
- Linux-only OS privacy surfaces (flatpak reports, GNOME/KDE agents) — not scoped, see `KNOWN_ISSUES.md`.
- Vendor additions that can't pass the `detect_paths` rule above.
