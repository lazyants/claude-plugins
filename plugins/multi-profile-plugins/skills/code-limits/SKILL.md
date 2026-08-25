---
name: code-limits
description: >-
  How much of a usage-limit window is left, when it resets, and which Claude Code profile or Codex
  home is close to exhausted, across every profile on this machine -- the 4 Claude Code profiles
  under `~/.claude*` (`CLAUDE_CONFIG_DIR`) and the 3 Codex homes under `~/.codex*` (`CODEX_HOME`).
  Use when asked how much usage-limit budget is left, when a five-hour or weekly window resets,
  which profile or account is nearly out, or how many Codex "usage limit reset" coupons remain.
  Ships `scripts/report_limits.py`, one table covering both CLIs: Claude Code read from its
  on-disk usage cache by default (or live with `--live`) and Codex read live over `codex
  app-server`'s `account/rateLimits/read` JSON-RPC call. Not the sibling `multi-profile-plugins`
  skill (plugin-store topology, `installLocation` errors) or `multi-profile-codex` (`CODEX_HOME`
  architecture, seeding, config pins) -- this one is only about usage-limit consumption and reset
  timing.
---

# Usage-limit report across Claude Code and Codex profiles

## What it covers

One report, one table, across every usage-limit pool this machine draws on: the 4 Claude Code
profiles under `~/.claude*` and the 3 Codex homes under `~/.codex*`. Each row states how much of
the window is used, when that window resets, and how fresh the number is.

## Two sources, different in kind

The Claude Code side and the Codex side are read differently, and every row says which.

**Claude Code is a cache on disk.** `<profile>/.claude.json` carries `cachedUsageUtilization`, a
snapshot taken at some past `fetchedAtMs`. Reading it costs nothing and needs no credential, but
the snapshot goes stale the moment its window rolls over. The default mode reads only this file.

**Codex is a live, read-only RPC.** `codex app-server` speaks JSON-RPC 2.0 over stdio; the report
sends it the handshake needed to call `account/rateLimits/read` and reads the reply. There is no
cache to fall back to on the Codex side -- the number is current at the moment it is read.

## stale-after-reset

A window whose `resets_at` (or `resetsAt`) has already passed describes the PREVIOUS window --
neither current usage nor zero. The report never presents an expired window as current: a row in
this state renders as `stale-after-reset`, shows the previous window's percentage labelled as
such together with that window's own reset time, and says the current window is unknown without
`--live`.

## `--live`, Claude Code side

`--live` makes a Claude Code row call `GET https://api.anthropic.com/api/oauth/usage` instead of
reading the cache. It reads the profile's OAuth token from `.credentials.json` when that file is
present, and otherwise from the macOS Keychain item the profile's config directory maps to; the
keychain read prompts the user, because the process doing the reading is not `claude` itself. A
live call that fails is reported as a gap for that profile, with its diagnostic code -- it never
falls back to the cache, because a live run that quietly degraded would print exactly what a
successful one prints.

## What the report touches

Stated plainly, not softened: the script itself writes nothing, but `codex app-server` opens and
migrates its own state databases under each `CODEX_HOME` exactly as any other `codex` invocation
does. Measured around a single `account/rateLimits/read` call against one home: 5 516 files
before, 5 521 after -- new `-wal`/`-shm` companions and migrated sqlite state. Contention with a
Codex client running concurrently against the same home is an accepted, unmeasured risk.

## Reset coupons

`rateLimitResetCredits.availableCount` is what the Codex TUI's own `/usage` calls "usage limit
reset available" -- a coupon that lifts a rate limit early. The report READS this count and
prints it. It never redeems one: the redeeming RPC is a different JSON-RPC method, and this
module cannot name it -- there is no `method` parameter anywhere in the module that could carry
that, or any other, method name. To redeem a reset coupon, use the Codex TUI's own `/usage`; this
tool deliberately will not do it.

## Exit contract

Exit 0 only when every candidate (each `~/.claude*` profile, each `~/.codex*` home) and every
record within it reached one of two terminal states: `reported` or `known-no-current-value`. Any
gap -- an unreadable candidate, a malformed field, a failed RPC, a child process that never
replies -- means exit 1, with a warning naming which check did not run.

## Invocation

```
python3 scripts/report_limits.py
python3 scripts/report_limits.py --live
python3 scripts/report_limits.py --claude-profile ~/.claude2 --codex-home ~/.codex3
```

The script ships mode 644, so it is run through `python3`, never executed directly. `--live` opts
the Claude Code side into the token path; the Codex side is always live. `--claude-profile PATH`
and `--codex-home PATH` are repeatable and, for whichever vendor at least one is given, replace
auto-discovery entirely for that vendor -- a vendor left unspecified still auto-discovers.
