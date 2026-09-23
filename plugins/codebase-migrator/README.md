# codebase-migrator

**Status: experimental, 0.1.0, pilot-unproven.** This plugin has not yet been run end to end on
a real migration; the test suite exercises its mechanics on small fixtures, not the design as a
whole.

Ports a same-language Python codebase to a new package one unit at a time, behind an
in-process strangler seam. Every port is gated by a differential test built by executing the
legacy code itself: codex proposes candidate inputs, the legacy code produces the expected
output, codex ports and reviews the unit, and a deterministic gate accepts the port only when
its observed behavior, its effect on persistent state, and its call route all match. See
`skills/codebase-migrator/SKILL.md` for the full run.

## v0.1 scope

- **Python to Python only.** A unit is one legacy module file. In scope: units that are
  deterministic, directly callable, and hold no state outside the call. Out of scope: anything
  that keeps state in a global, a module-level cache, a closure cell, a file, or a socket — such
  units are refused as ineligible and ported by hand.
- The behavioral net is a generated golden master only — existing tests and recorded traffic are
  not yet supported net sources.
- The output is a ported system running behind a strangler seam, not a finished cutover.

Full non-goals and the reasoning behind them are in `SKILL.md` §0 and its references.

## Requirements

- Python 3.11 or later. The plugin's runtime is standard-library only — no third-party
  dependency is installed alongside it.
- The `codex` CLI, reachable as the binary named in `migration.json`'s `codex_bin` key (default
  `codex`), for every port, review and cases-proposal turn. There is no LLM anywhere in the
  deterministic gates themselves.
- `pytest`, for running this plugin's own test suite (not required to run a migration).

## Running the tests

From the plugin directory:

```sh
python3 -m pytest tests -q
```

Or via the repo's runner, which also asserts the suite actually collected at least one test
before reporting a result:

```sh
bash tests/run-all.sh
```

CI (`.github/workflows/codebase-migrator.yml`) runs this same suite on Python 3.11 and 3.14; it
never invokes a real `codex` binary — the write-boundary probe against the real CLI is a runtime
check documented in `references/write-boundary.md`, not part of the test suite.
