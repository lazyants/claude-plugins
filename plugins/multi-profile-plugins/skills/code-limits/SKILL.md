---
name: code-limits
description: >-
  How much of a usage-limit window is left, when it resets, and which Claude Code profile or Codex
  home is close to exhausted, across every profile on this machine -- every discovered Claude Code
  profile under `~/.claude*` (`CLAUDE_CONFIG_DIR`) and every discovered Codex home under
  `~/.codex*` (`CODEX_HOME`).
  Use when asked how much usage-limit budget is left, when a five-hour or weekly window resets,
  which profile or account is nearly out, or how many Codex "usage limit reset" vouchers remain.
  Ships `scripts/report_limits.py`, installable on PATH as `code-limit`, one table covering both
  CLIs: Claude Code read from its on-disk usage cache by default (or live with `--live`) and Codex
  read live over `codex app-server`'s `account/rateLimits/read` JSON-RPC call. Not the sibling
  `multi-profile-plugins` skill (plugin-store topology, `installLocation` errors) or
  `multi-profile-codex` (`CODEX_HOME` architecture, seeding, config pins) -- this one is only
  about usage-limit consumption and reset timing.
---

# Usage-limit report across Claude Code and Codex profiles

## What it covers

One report across every usage-limit pool this machine draws on: every discovered Claude Code
profile under `~/.claude*` and every discovered Codex home under `~/.codex*`.

It opens with the **reset vouchers** -- the one-shot rate-limit resets the Codex TUI redeems --
because a voucher expires whether or not anyone looks, and the count alone never says when. Each
home's row carries the vendor's own title and expiry date, and the time left beside it. A home
that reported a count of zero reads `0` -- the integer it measured, never a word standing in for
it -- while one whose backend sent no voucher data at all reads `not reported`. Those are two
different facts and the report keeps them apart. The credit balance follows in the same band, labelled with the home it belongs to.

Then one **table of allowances**, ordered by candidate and then by allowance, alphabetically.
Every profile and home on this machine is on it, its rows adjacent, in the same order on every
run -- and its name printed once for the group rather than once per row.

**A row is an allowance, not a window.** An account's five-hour and weekly figures are two
readings of one quota, so they sit side by side on one line under a `5H` and a `WEEKLY` column
rather than on two rows that repeat the profile and compete with each other for the top of the
page. The columns are discovered from the data, so any other window the backend reports opens a
column of its own rather than being folded into somebody else's. Two windows of equal duration
under one pool -- a shape the Codex schema permits -- take two rows of the same column, because a
cell can hold one number and dropping the second would be silent.

**Claude Code's model-scoped weekly pool is the one thing deliberately not shown.** It read `0%`
on every account measured, and a `WEEKLY/<model>` column empty on every row but one costs the
table more width than the pool is worth. A profile carrying nothing else is the exception -- not
a shape the vendor produces, but reporting a well-formed payload as malformed over it would be
worse, so there the scoped pool is shown under the model's own display name.

On the Codex side only the pool the CLI actually spends from is shown. `rateLimitsByLimitId`
enumerates pools a person at a terminal is not asking about -- a model-specific one and a
reserve -- and the backend already says which one answers "how much can I still use here": the
top-level `rateLimits` object carries its `limitId`. A pointer naming a pool that is not in the
map keeps every pool, because showing more than was asked for is recoverable and showing none
is not.

So each row reads: where it lives, which allowance it is under the vendor's own NAME for it --
Codex sends a `limitName` beside every pool, so the column reads `GPT-5.3-Codex-Spark` and
`gpt-reserve` rather than the internal `codex_bengalfox` and `base_model_inference`; a pool the
backend leaves unnamed keeps its id, and so does one whose name another pool already uses -- then
one cell per window carrying the percentage used and how long until that window resets, and
finally where the numbers came from. A cell whose window is not current says so in its own words
and is dimmed: an expired one reads `14h ago` -- the percentage beside it describes the PREVIOUS
window -- and one the backend sends no reset time at all for reads `inactive` or `not reported`.
Claude Code's `is_active` is deliberately not part of that: it marks whichever pool is currently
BINDING, and exactly one pool per account carries it, so treating it as "no current window" grey
out five-hour figures that were entirely current -- along with the reset time beside them. A cell that gapped carries its diagnostic token in place of a figure.
Candidate-level outcomes -- a profile with no cache, one with no subscription, an unreadable
directory -- become one footnote line each, with their diagnostic token intact.

**Position means nothing, deliberately.** Ranking by consumption scattered one Codex home's
pools down the page and printed the same directory name in four places, and a rank over a mix of
current and expired windows orders numbers that are not comparable to begin with. Consumption is
read off the figure and its hue; whether a window is current is read off the cell's own words. A
stable alphabetical order also means two runs over the same data agree, and that a row does not
move because some other account's window rolled over.

## Two sources, different in kind

The Claude Code side and the Codex side are read differently, and every row says which in its
last column: `live` for Codex, and the cache's age for Claude Code.

**Claude Code is a cache on disk.** `<profile>/.claude.json` carries `cachedUsageUtilization`, a
snapshot taken at some past `fetchedAtMs`. Reading it costs nothing and needs no credential.

**Except when that snapshot's window is already over** -- then the file has nothing to say about
the present, and the report re-reads that one profile live rather than printing a percentage
about a window nobody is spending from. Signing in does not fix it on its own: the CLI rewrites
`.claude.json` at login but refreshes `cachedUsageUtilization` only after a request that carries
usage back, so a freshly authenticated profile can still be describing a window three days gone.
A retry that fails keeps the cached rows exactly as they were -- they are stale, which their
cells already say -- and states its reason as a note rather than a warning. The `SOURCE` column
is where to read which happened: `api` for a row that was refreshed, a cache age for one that
was not.

The **default profile is the exception**: its config is `~/.claude.json`, beside `~/.claude`
rather than inside it, because the path is `<CLAUDE_CONFIG_DIR or $HOME>/.claude.json` and that
profile is the one where the config dir falls back to `$HOME`. `~/.claude` is its data directory,
and a `~/.claude/.claude.json` left there by an older release is not the live config -- reading
it reports `no-usage-cache` for an account whose pools are perfectly readable one level up.

`hasAvailableSubscription: false` is read only as the REASON a cache is absent, never as a reason
to skip one that is present. Accounts ship that flag beside a full, freshly fetched `limits`
array -- including at 100% of a weekly pool -- so treating it as "not subscribed" suppressed
exactly the numbers worth reading, under a diagnostic that exits 0.

**Codex is a live, read-only RPC.** `codex app-server` speaks JSON-RPC 2.0 over stdio; the report
sends it the handshake needed to call `account/rateLimits/read` and reads the reply. There is no
cache to fall back to on the Codex side -- the number is current at the moment it is read.

## stale-after-reset

A window whose `resets_at` (or `resetsAt`) has already passed describes the PREVIOUS window --
neither current usage nor zero. The report never presents an expired window as current: the cell
reads how long ago that window reset (`14h ago`), is dimmed beside whatever current cells share
its row, and its row ranks below every row that has a current window. One legend line under the
table carries the `[stale-after-reset]` token and says the current figure needs `--live`; it is
printed only when such a cell is actually on the page.

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
instance, now prints a voucher row reading "not reported" instead of gapping the run,
and because `usedPercent` is an unbounded int32 in that schema, a pool reported past 100%
renders as given rather than being refused, which would gap the report exactly when it is most
worth reading. A shape the schema does not permit at all still gaps, as before.

## Reset vouchers

`rateLimitResetCredits.availableCount` is what the Codex TUI's own `/usage` calls "usage limit
reset available" -- a voucher that lifts a rate limit early. The report READS this count and
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
running. To redeem a voucher, use the Codex TUI's own `/usage`; this tool deliberately will
not do it.

Beside the count, the report reads the vendor's own `title` and `expiresAt` off the first
available credit and renders them in the voucher band. Both are optional in that payload and
neither may ever gap a run: a home reporting a bare count still renders, with less to say. The
expiry is worth surfacing precisely because a voucher lapses whether or not anyone is watching,
and the count on its own never says when.

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
python3 scripts/report_limits.py --color=always | less -R
```

The script ships mode 644, so it is run through `python3`, never executed directly. `--live` opts
the Claude Code side into the token path; the Codex side is always live. `--claude-profile PATH`
and `--codex-home PATH` are repeatable and, for whichever vendor at least one is given, replace
auto-discovery entirely for that vendor -- a vendor left unspecified still auto-discovers.

`--color` takes `auto` (the default), `always` or `never`. `auto` colours only when stdout is a
terminal and `NO_COLOR` is unset, so piping the report -- into a file, a pager, or this plugin's
own test suite -- yields plain text. Colour is applied to finished cells after the column widths
are computed from the plain strings, so it changes how the table looks and nothing else: strip the
escapes from an `--color=always` run and you get the `--color=never` run back, byte for byte.
A figure is red at 80% and above, yellow from 50, green below; a cell whose window is not the
current one keeps that hue but is dimmed, because it sits beside a cell that IS current and must
not read as comparable to it; a voucher count is bold, and green when there is one to spend.
There is no gauge: three window columns of bar plus number wrapped the table, and the bar never
said anything the number beside it did not.

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
a regular file whose bytes are exactly the shim the installer generates: read as bytes, matched
line by line, with the quoted path required to be in the exact form the installer emits. A symlink
never counts, whatever it points at; neither does a file quoting the marker in a comment, nor one
that keeps the `exec python3 ... "$@"` shape around a second command, nor a copy of a real shim
whose newlines became CRLF -- that last one compares equal as text while its `#!/bin/sh` line is
no longer runnable. Anything else is left untouched, named in a warning, and the run exits 1;
`--force` replaces it.

Taking a name that was free uses `link`, which fails rather than overwriting if something claimed
it in between; replacing an entry this run has classified uses `replace`. Both act on the name's
own directory entry, so neither writes through a symlink to whatever it targets. A shim that still
has the right bytes but lost its execute bits is rewritten rather than reported as already
installed -- a command that exits 126 is not an installed command.

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
