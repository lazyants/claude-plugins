---
name: code-limits
description: >-
  How much of a usage-limit window is left, when it resets, and which Claude Code profile or Codex
  home is close to exhausted, across every profile on this machine -- every discovered Claude Code
  profile under `~/.claude*` (`CLAUDE_CONFIG_DIR`) and every discovered Codex home under
  `~/.codex*` (`CODEX_HOME`).
  Use when asked how much usage-limit budget is left, when a five-hour or weekly window resets,
  which profile or account is nearly out, or how many Codex "usage limit reset" coupons remain.
  Ships `scripts/report_limits.py`, installable on PATH as `code-limit`, one table covering both CLIs: Claude Code read from its
  on-disk usage cache by default (or live with `--live`) and Codex read live over `codex
  app-server`'s `account/rateLimits/read` JSON-RPC call. Not the sibling `multi-profile-plugins`
  skill (plugin-store topology, `installLocation` errors) or `multi-profile-codex` (`CODEX_HOME`
  architecture, seeding, config pins) -- this one is only about usage-limit consumption and reset
  timing.
---

# Usage-limit report across Claude Code and Codex profiles

## What it covers

One report, one table, across every usage-limit pool this machine draws on: every discovered
Claude Code profile under `~/.claude*` and every discovered Codex home under `~/.codex*`. Each
usage-window row states how much of the window is used, when that window resets, and how fresh
the number is. The reset-coupon count and the credit balance render as a plain info line
instead: neither carries a reset time, and neither names its own source -- only the group
heading above it (`Claude Code` or `Codex`) does.

## Two sources, different in kind

The Claude Code side and the Codex side are read differently. Every row prints under one of the
two group headings, `Claude Code` or `Codex`, that say which.

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
keychain read prompts the user, because the process doing the reading is not `claude` itself.
Both sources hold the SAME object and go through one extractor: the Keychain item stores the
whole credential JSON, not a bare token, so the access token is parsed out of it and its expiry
checked exactly as the file's is. Which item is asked for was measured, and the default profile
is a special case -- `~/.claude` keeps its live credential under the unsuffixed
`Claude Code-credentials`, while every other config directory uses a name suffixed with the
first 8 hex of the SHA-256 of its absolute path. A profile reached by a different spelling of
the same directory, or through a separate secure-storage override, hashes to a name that simply
is not there, so it gaps as `token-absent` rather than reading another account's item. A
live call that fails is reported as a gap for that profile, with its diagnostic code -- it never
falls back to the cache, because a live run that quietly degraded would print exactly what a
successful one prints.

## What the report touches

Stated plainly, not softened: the script itself writes nothing, but `codex app-server` opens and
migrates its own state databases under each `CODEX_HOME` exactly as any other `codex` invocation
does. Measured around a single `account/rateLimits/read` call against one home: 5 516 files
before, 5 521 after -- new `-wal`/`-shm` companions and migrated sqlite state. Contention with a
Codex client running concurrently against the same home is an accepted, unmeasured risk.

## Absent and null, per the vendor's schema

The Codex reply is checked against what the vendor's own schema
(`codex-rs/app-server-protocol/schema/json/v2/GetAccountRateLimitsResponse.json`) actually
requires, not against what would be convenient to require: only `rateLimits` is mandatory, and
fields like `rateLimitResetCredits`, `windowDurationMins`, `resetsAt`, and `limitId` are all
declared nullable or optional. A value the schema allows to be absent or null never gaps -- a
check a profile's owner could never clear would be worse than the absence it reports -- but what
renders instead varies by field: some rows still report normally under a different label, some
print an explicit absence, and some produce no row at all. An omitted `rateLimitResetCredits`, for
instance, now prints a `reset coupons` row reading "not reported" instead of gapping the run,
and because `usedPercent` is an unbounded int32 in that schema, a pool reported past 100%
renders as given rather than being refused, which would gap the report exactly when it is most
worth reading. A shape the schema does not permit at all still gaps, as before.

## Reset coupons

`rateLimitResetCredits.availableCount` is what the Codex TUI's own `/usage` calls "usage limit
reset available" -- a coupon that lifts a rate limit early. The report READS this count and
prints it.

It never redeems one, and the guarantee is about what can be SENT. The module writes to the
child on exactly one line, and that line writes a frame from `_FRAMES` and nothing else;
`_FRAMES` decodes at runtime to exactly the three read-only methods, in order, so those bytes
are the only ones ever written to the child's stdin. (It also receives `CODEX_HOME` in its
environment, and nothing else from here.) The suite's `ast` walk of every `"method"` literal
in the source is a second, static check on top of that -- it catches an extra method-bearing
dict appearing anywhere in the file, but on its own says nothing about a method assembled some
other way (`dict([("method", operation)])` would not be a literal), which is why it is a CI gate
rather than a runtime guard: it stops a fourth method from being merged, not one already
running. To redeem a reset coupon, use the Codex TUI's own `/usage`; this tool deliberately will
not do it.

An omitted or null `rateLimitResetCredits` renders as a known absence -- the row reads "not
reported" and the run stays clean -- because the vendor's schema allows the field to be absent.
A non-null value that is not an object, or an `availableCount` that is not a non-negative
integer, is a shape the schema does not permit, and that still gaps the row and exits 1.

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

## `code-limit`, the installed command

The same report, as one command on `PATH`, taking the same options and returning the same exit
codes:

```
code-limit
code-limit --live
code-limit --claude-profile ~/.claude2 --codex-home ~/.codex3
```

Install it with the setup step this skill ships:

```
python3 scripts/install_code_limit.py                       # into ~/.local/bin
python3 scripts/install_code_limit.py --bin-dir ~/bin
```

What lands on `PATH` is a three-line shell file whose only statement is
`exec python3 '<...>/scripts/report_limits.py' "$@"`. There is no second copy of the report and
nothing for one to drift from: `exec` hands the process over, so the report's own exit status is
the command's and stdin, stdout and stderr pass straight through. Re-running the installer is how
you refresh it after the plugin moves; an already-current shim is reported as such and rewritten
identically.

**Run the installer from a version-stable copy.** Claude Code keeps plugins at
`<CONFIG_DIR>/plugins/cache/<marketplace>/<plugin>/<version>/`, and startup GC deletes any version
directory the acting profile's catalogue stops referencing -- the sibling `multi-profile-plugins`
skill documents that mechanism. A command installed out of there names one version: after an
update it keeps running the old report while that directory survives, then breaks when it is
collected, and neither is announced. The installer therefore refuses that source outright and
names what to use instead -- the marketplace checkout at
`~/.claude/plugins/marketplaces/<marketplace>/.../skills/code-limits/scripts/`, or a clone of the
repo.

**The interpreter is resolved at run time, not baked.** The shim calls `python3` off `PATH`,
exactly as this skill's own `python3 scripts/report_limits.py` invocation does, because every
absolute interpreter path available at install time is less durable than the command is meant to
be -- a Homebrew Cellar path carries a patch version, a virtualenv gets deleted. The report needs
**Python 3.11+**, so that is what `python3` must resolve to in the shell you run `code-limit` from.
The installer prints which `python3` it found and that binary's version; it does not refuse on it,
because it cannot bind what your shell will resolve later.

**Nothing you own is overwritten silently.** `code-limit`, `claude-limit` and `claude-limits` are
names that may already belong to something else. An entry counts as this plugin's only when it is
a regular file whose whole content is exactly the shim the installer generates -- a symlink never
counts, whatever it points at, and neither does a file that merely quotes the marker comment.
Anything else is left untouched, named in a warning, and the run exits 1; `--force` replaces it.
The replacement is one `os.replace`, which swaps the name's own directory entry rather than
writing through a symlink to whatever it targets.

**Legacy `claude-limit` / `claude-limits`.** The installer manages those two names when they
already exist in the chosen directory -- it never manufactures them for someone who never had
them. It reaches only inside `--bin-dir`, so check the rest of your `PATH` by hand:

```
command -v code-limit claude-limit claude-limits
```

If either legacy name resolves somewhere else, re-run the installer with that directory and
`--force`, or delete the old command. Leaving it is the one outcome to avoid: it keeps answering
with whatever provider-specific logic it had, which is the gap this command closes.

To uninstall, remove the files -- they are the whole installation.
