---
name: multi-profile-codex
description: >-
  How the Codex CLI stores credentials and config when several CODEX_HOME profiles run on one machine —
  commonly a base `~/.codex` plus alternates such as `~/.codex2` or `~/.codex3`, one per OpenAI account.
  Use when setting up or seeding a second Codex profile, choosing which CODEX_HOME an invocation should
  target, wiring a per-profile launcher, or debugging a profile that reads another profile's tree —
  sessions or plugins showing up under the wrong account, an MCP server escaping into the base home, or two
  profiles that turn out to share one usage pool.
---

# Multi-profile Codex (CODEX_HOME) architecture

## When multiple profiles matter

The Codex CLI keeps everything for one profile under a single home directory, chosen by the `CODEX_HOME`
environment variable (default `~/.codex`): `auth.json`, `config.toml`, `sessions/`, logs, and `plugins/`.
Pointing `CODEX_HOME` at a different directory is how one machine runs several OpenAI accounts side by
side. Don't assume a fixed number or naming — inspect the actual machine's topology before acting.

That isolation is genuine at the filesystem level. A fresh `CODEX_HOME` reports `Not logged in` while the
base home reports `Logged in using ChatGPT`, and `codex login` writes only into the active home.

Note that `--profile` is a DIFFERENT and much weaker mechanism: it layers `$CODEX_HOME/<name>.config.toml`
on top of the base config within one home, so it varies settings but shares one `auth.json`. For separate
accounts, `CODEX_HOME` is the only mechanism that works.

## The failure mode: a copied config.toml pins the home it came from

The obvious way to seed a new profile — copy the base `config.toml` so the new account behaves the same —
is the one that breaks it. `CODEX_HOME` only decides where files are read FROM; it does not rewrite what
they SAY. Absolute paths baked into the copied config keep pointing at the ORIGINAL home, so the new
profile loads its config from its own directory and is then sent straight back into the old one.

The paths that do this are written by the ChatGPT desktop app, not by the CLI, which is why they are easy
to miss when reading a config as a list of settings:

- **`notify`** — a helper binary under `<base-home>/computer-use/…`.
- **`[marketplaces.*] source`** — `<base-home>/.tmp/bundled-marketplaces/…`.
- **`[mcp_servers.*.env]`** — the worst of them. It carries trusted-code paths, a services blob, and
  **`CODEX_HOME` itself**. An MCP subprocess launched from the new profile therefore re-enters the base
  home, with the base home's plugin cache and trusted paths, while the CLI that spawned it is correctly
  isolated. Nothing about the CLI's own behaviour looks wrong.

Same root cause as the Claude Code shared-plugins-store class in
[the sibling skill](../multi-profile-plugins/SKILL.md): home-scoped content addressed by an absolute path
instead of resolved against the active home. The remedy differs, though — Claude Code's fix is structural
(give each profile its own store), and here it is textual (drop the pinned blocks so the CLI re-derives
them).

## Diagnose before acting

Run the bundled `scripts/inspect_codex_profiles.py` read-only against the profiles in use (it auto-detects
`~/.codex*` homes, or takes explicit ones as arguments). It reports three things:

- **Credentials** — whether two homes share one `auth.json` file (a `codex logout` in either logs out
  both), and, separately, whether two homes are logged into the SAME account. The second is the quieter
  failure: the split looks fine, every file is distinct, and the two profiles still draw on one usage pool.
- **Cross-profile config pins** — the failure above, reported as the dotted TOML key that holds the path.
- **Shared content stores** — `sessions/`, `plugins/`, `cache/` and friends resolved through symlinks, so
  one profile writing or pruning cannot silently reach another's data.

Prefer it over an ad hoc `grep` for a home path. A bare substring match reports a false positive on every
sibling profile, because `~/.codex` is a substring of `~/.codex2`; a `startswith` match fixes that and then
misses the real pins, which sit mid-string inside a `:`-joined search path or a JSON services blob. The
script matches on a path boundary, which is the only form that gets both right.

## Seeding a new profile

Copy the config, then delete every block the desktop app owns and let the CLI re-derive it: section roots
`notice`, `tui`, `marketplaces`, `plugins`, `mcp_servers`, `desktop`, `shell_environment_policy`, plus the
top-level `notify` key. What remains — `model`, `model_reasoning_effort`, `approval_policy`, `sandbox_mode`
and the rest of the preamble — is portable as-is.

Keep `[projects.*]`. Those are trust records keyed by absolute PROJECT path, they contain no reference to
any Codex home, and re-approving a long-lived machine's project list by hand is the real friction in
setting a second profile up at all.

Then verify rather than assume: seed, run the health check, and confirm the new home reports zero pins
before logging in. A seeding step that silently copied one path through looks exactly like one that
didn't.

## Selecting a profile per invocation

`CODEX_HOME` is read from the environment, so a small executable shim per profile on `PATH` is enough:

```sh
#!/bin/sh
export CODEX_HOME="$HOME/.codex2"
exec "$HOME/.local/bin/codex" "$@"
```

Prefer a shim over a shell function or alias. A function defined in a shell rc is invisible to everything
that is not that interactive shell — launchd jobs, editors, MCP servers, and agent harnesses that spawn
`codex` directly all get the base profile instead, silently and with the wrong account.

The ChatGPT desktop app stays bound to whichever home it was configured with, and pins that home in the
config blocks listed above. Profile switching is a CLI-side mechanism; the desktop app does not follow it.

## Disk

Each home carries its own sessions, logs, and plugin cache, and these are not small — a long-lived home
reaching tens of gigabytes is ordinary. Budget for that per profile, and resist the tempting fix of
symlinking `sessions/` or `plugins/` between homes: that reintroduces exactly the shared-store class the
separate homes existed to avoid, which is why the health check looks for it.
