---
name: multi-profile-plugins
description: >-
  How Claude Code stores plugins and config when multiple CLAUDE_CONFIG_DIR profiles are in use — commonly a
  base `~/.claude` plus one or more extras such as `~/.claude2`, `~/.claude3`, or a separate profile the
  desktop app runs independently of the CLI. Use when running any `claude plugin …` CLI op (install/update/
  remove a plugin, add or refresh a marketplace) in a multi-profile setup, choosing which CLAUDE_CONFIG_DIR to
  target, debugging a "corrupted installLocation" error, or reasoning about why a plugin or marketplace
  appears — or gets GC-deleted — in one profile but not another.
---

# Multi-profile plugins & config-dir architecture

## When multiple profiles matter

Claude Code selects its config directory via `CLAUDE_CONFIG_DIR` (default `~/.claude`). A single machine can
run several profiles side by side — for example a base `~/.claude` plus `~/.claude2`, `~/.claude3`, or a
profile the desktop app uses independently of the CLI. Each profile has its own `settings.json` and
`extraKnownMarketplaces`, and — depending on how the profiles were set up — either its own plugin store or
one shared with other profiles. Don't assume a fixed number or naming of profiles; inspect the actual
machine's topology before acting.

## The failure mode: a SHARED plugins store

Some setups make several profiles share one `plugins/` directory — commonly via symlinks, so installing a
plugin from any profile appears to "just work" everywhere. Sharing that directory causes two recurring
problems:

- **"Corrupted installLocation" churn.** `known_marketplaces.json` is a cache written under
  `<CONFIG_DIR>/plugins/`. A `claude plugin` op from one profile re-stamps this shared file's
  `installLocation` prefix to that profile's own `<CONFIG_DIR>`. Every OTHER profile sharing the same file
  then fails validation on its next use: `Marketplace 'X' has a corrupted installLocation …`. See
  [references/cli-mechanism.md](references/cli-mechanism.md) for the exact check that trips.
- **Cross-profile GC / uninstall.** Plugin installs are tracked per-profile in `installed_plugins.json` (the
  "catalog"), but startup garbage-collection and `claude plugin uninstall` act on the shared `cache/` and
  `data/` directories. If two profiles' catalogs diverge, one profile's GC can orphan-then-delete a plugin
  version the OTHER profile still has installed, because from that profile's own catalog it looks
  unreferenced.

Both problems have the same root cause: content that is CATALOG-scoped (per-profile) living in a directory
that is INODE-shared (cross-profile).

## Diagnose before acting

Run the bundled `scripts/inspect_profiles.py` read-only against the profiles in use (it auto-detects
`~/.claude*` profiles, or takes explicit ones as arguments). It reports: which `plugins/` dirs are real vs.
symlinked, whether any profiles share a `known_marketplaces.json` (the churn risk), and whether any
registry points into another profile's `plugins/` tree (a cross-profile leak). Use it to confirm whether a
given machine has this shared-store problem at all, and to re-check after any remediation — verify the
topology, don't assume it.

Prefer an exact path-prefix comparison over an ad hoc `grep` for a config-dir path — a naive substring match
reports a false positive whenever one profile's directory name is a prefix of another's (e.g. `~/.claude` is
a substring of `~/.claude2`).

## If you see "corrupted installLocation"

Do not hand-edit the `installLocation` string in `known_marketplaces.json` as a permanent fix — it treats
the symptom, and the churn returns on the next cross-profile op. Diagnose first (previous section): if
`plugins/` is shared across the affected profiles, the durable fix is structural, not a registry edit.

## The structural fix: independent plugin stores per profile

The durable fix is to give every profile that shares a store its OWN independent `plugins/` directory — its
own `marketplaces/`, `cache/`, `data/`, install manifests, catalog, and `known_marketplaces.json` registry —
rather than a shared directory, or a shared directory with only the registry file de-symlinked. Once a
profile's plugin content is genuinely independent:

- A `claude plugin …` op from that profile only ever touches its own registry — no more cross-profile
  "corrupted installLocation".
- Startup GC and `uninstall` only ever act within that profile's own `cache/` and `data/`, so they can no
  longer prune or delete another profile's plugins.
- Plugin content becomes genuinely per-profile: installing, updating, or removing a plugin, or adding a
  marketplace, has to be repeated once per profile you want it in — a change in one profile does not
  propagate to another.

This skill covers the reasoning and a read-only diagnostic (`scripts/inspect_profiles.py`); it does not
perform an automated migration. Moving from a shared store to independent stores touches live plugin data and should be planned
and executed deliberately for the specific machine — back up each affected profile's existing `plugins/`
directory before making any structural change.

Other directories that are commonly shared on purpose, and are fine to leave shared, include `skills/`,
`agents/`, `commands/`, `hooks/`, and `CLAUDE.md`. Those are read at invocation time rather than
registry-validated, so they don't have this failure mode.

## A marketplace declared differently per profile

It's a legitimate pattern for the same marketplace name to resolve to a different source in different
profiles — for example a local `directory` source in a profile used to develop that marketplace's own repo,
and a `github` source in profiles that just consume it. This is a deliberate per-profile choice made via
each profile's own `settings.json` → `extraKnownMarketplaces`; don't "normalize" the sources to match unless
that is the actual intent.

## Deep mechanism

The reverse-engineered validation and garbage-collection internals — the prefix check that causes the
corrupted-installLocation error, the catalog-scoped GC that forces content independence rather than just a
de-shared registry file, and the per-config-dir cache model — are in
[references/cli-mechanism.md](references/cli-mechanism.md). Read it to reason about *why* the CLI behaves
this way, or when diagnosing an unexpected GC event or registry rewrite.
