# Durable state and resume

## The durable root

`scaffold.py --root R` owns the layout (`SKILL.md` §1.1) and is the **only** script that reads
or writes the ownership marker (`.codebase-migrator-root.json`): it is what decides `fresh` /
`resumed` / `ambiguous` (refuses without `--adopt`) / fatal (unreadable or wrong-schema marker)
for a given root, so a second `scaffold.py` run never adopts a workspace silently. Every other
script does **not** check the marker itself — it only requires `--root` to name an existing
directory and `migration.json` in it to validate (`cm_common.resolve_root` +
`cm_common.load_config`), so running, say, `inventory.py --root R` against a directory that was
never scaffolded fails on a missing or invalid `migration.json`, not on a missing marker. A run
is always resumable: killing it at any point and re-running the same command from `SKILL.md` §4
picks up from whatever the ledger and the on-disk artifacts already record.

## Ledger states

`ledger.json` holds one entry per unit: `{"state", "cache_key", "key_sha256", "updated",
"reason"}`. States:

| State | Meaning | Set by |
|---|---|---|
| `pending` | not yet dispatched, or reset after drift acceptance | default; `ledger.py accept-drift` |
| `drafted` | a port exists but has not converged | (informational; no script sets it directly in v0.1) |
| `converged` | R0 held, R2 and R3 passed against the current target bytes, every finding in the latest review is refused, round count within cap | `ledger.py converge` only |
| `stale` | a converged unit's cache key moved on a field other than conventions | `ledger.py classify` |
| `stale_by_convention` | a converged unit's cache key moved **only** because `conventions.md` changed | `ledger.py classify` |
| `blocked` | operator-set, for a unit waiting on something outside the pipeline | `ledger.py set` |
| `escalated` | fix rounds exhausted with findings still open | `ledger.py set --state escalated` |
| `ineligible` | a static flag, `stateful`, or `nondeterministic` verdict rules the unit out | `ledger.py set --state ineligible --reason ...` |

`ledger.py set` can move a unit to any state **except** `converged` — convergence is only ever
granted by `ledger.py converge`'s own checks, never asserted directly. This is the same
authority rule as the gates themselves: an operator can name a unit `blocked` or `ineligible`,
but cannot declare it done.

**A stale unit is not a re-dispatch.** `stale` and `stale_by_convention` are bookkeeping, not a
queue: nothing in v0.1 automatically re-runs a stale unit's gates. Re-porting or re-reviewing a
converged unit is always an operator decision (`SKILL.md` §3) — a conventions edit legitimately
moves every unit's cache key at once, and treating that as "every unit needs redoing" would
contradict the entire point of converging units one at a time. A unit gone `stale` naming only
`target_closure_sha256` did not itself change — a dependency it imports through the target
package was ported, or a private helper it uses was edited — so the fix is to re-run
`unit_gate.py` and `diff_gate.py` against the current target-side tree and `ledger.py converge`
again, not to dispatch a new port turn for `U`.

## The cache key

`ledger.py key --root R --unit U` computes and prints `cache_key(root, cfg, unit)`, a dict with
these fields, in order, every path taken relative to the durable root:

1. `legacy_closure_sha256` — the live legacy tree's digest over `U` plus its transitive
   `imports_units`, plus any ancestor package unit Python executes on the way to importing `U`
   (an ancestor whose `__init__.py` carries real code, not just a docstring —
   `references/gate-stack.md`'s R3 section). A change to that ancestor's code moves this digest
   exactly as a change to an explicitly-imported dependency would.
2. `rows_sha256` — the frozen rows of `U`'s own public symbols plus its `imported_symbols`,
   sorted by source.
3. `conventions_sha256` — the digest of `conventions.md` as it stands right now.
4. `fidelity_sha256` — the fidelity policy plus any `exceptions.json` entries for `U`.
5. `net_sha256` and `cases_sha256` — both read from `net.lock.json` (`null` if `U` has no entry
   yet).
6. `templates_sha256` — the digest of the four prompt templates together.
7. `target_closure_sha256` — the digest of every file in `U`'s **target-side** import closure
   (`inventory.import_closure` walked from `target_module(U)` under `target_root`): every
   dependency's shim-or-port file `U`'s port actually imports, plus every private target-side
   helper it reaches. This is what catches the two changes `legacy_closure_sha256` cannot see,
   because neither touches `U`'s own legacy bytes or its target file: a dependency's shim
   becoming a real port, and an edit to a private helper module `U`'s port imports.
8. `adapter` — the literal string identifying the Python-to-Python adapter.
9. `plugin_sha256` — the digest of every script in the plugin.

**Never hashed:** absolute paths, schema descriptions, timestamps. Two known literary-translator
mistakes this deliberately avoids: hashing a schema's prose description (which re-translated
every converged segment there over a wording edit with no behavioral meaning), and hashing the
durable root's own absolute path (which invalidated every converged segment on a simple project
move). Everything above is either a relative-path digest or a policy/version string.

`ledger.py classify --root R` recomputes this key for every `converged` unit and compares it to
the key stored at convergence time: identical → stays `converged`; only field 3 differs →
`stale_by_convention`; anything else differs → `stale`, naming which fields moved.

## Drift acceptance

The net's recorded `legacy_closure_sha256` **is** the baseline for a unit — there is no separate
pinned-commit file. `ledger.py eligible` and `diff_gate.py` both refuse the moment the live
legacy closure digest stops matching it, naming the changed unit.

`ledger.py accept-drift --root R --unit U --operator NAME --reason T`:

- refuses unless the inventory is already current for `U`'s closure — the static verdict must
  reflect the new source first, so a dependency that just gained a clock read or a file write is
  caught by R0 before anything is re-captured;
- appends `{"unit", "old_closure_sha256", "new_closure_sha256", "operator", "reason", "at"}` to
  `drift_log.json` (append-only — every accepted drift is a permanent record, never a silent
  overwrite);
- **removes `U` from `net.lock.json`**, so `ledger.py eligible` refuses `U` again until
  `net_capture.py` re-captures it — which also re-runs the state and determinism probes against
  the new code, not just a digest comparison. A unit that gained a global or a clock dependency
  since its last capture is caught here, not silently re-accepted;
- sets `U`'s ledger state to `pending`.

This is deliberately the only path through a drift refusal: there is no way to keep an old net
valid against new source, and no way to accept drift without re-measuring the unit from scratch.

## The stdout and exit-code contract

Every script builds its top-level parser with `cm_common.make_parser`, whose `argparse.
ArgumentParser` subclass overrides `error()` so a bad CLI invocation — an unknown flag, a
missing required one, an invalid choice — still emits exactly one JSON line to stdout
(`{"ok": false, "error": "<message>"}`), the same as any other refusal, and exits 2. Nothing in
this plugin ever lets argparse's own default behavior (usage text on stderr, a bare `sys.exit(2)`
with no JSON at all) reach the caller, so a script's stdout is always machine-parseable JSON,
never conditionally empty depending on how it was invoked.

## Atomic checkpoint

Every write to `ledger.json`, `registry.json`/`registry.lock.json`, `net.lock.json`, and every
`runs/` artifact goes through `cm_common.atomic_write_json`/`atomic_write_text`: write to a
temporary file in the same directory, flush, `fsync`, then `os.replace`. State is written
**inside** each loop step, before the next dispatch — never batched to the end of a run.
Literary-translator's own "just re-run to resume" recovery advice was false for exactly as long
as its save call lived at the end of its main loop instead of inside it (LT 1.99.1); v0.1 does
not repeat that.

## The read-only status reporter

`status.py --root R` never writes anything — verified by a test that compares the tree's
digests before and after running it. `units` and `in_ledger` are deliberately two different
counts: `units` is `inventory.json`'s unit count — the denominator for "N of units converged" —
while `in_ledger` is how many units `ledger.json` actually has an entry for, which is smaller
until something first sets a unit's state; a unit can be fully discovered by `inventory.py` and
still be missing from the ledger entirely. Beyond those two, it reports counts by ledger state,
eligible/ineligible counts, netted units, frozen row count, shim and port counts, and the last
recorded write-boundary probe verdict. A missing input is reported as `absent` for that field
specifically, never folded into a zero — a zero must always mean "measured and found to be
zero," not "not looked at."

## What is not durable

Every dispatch stage (`sandbox.py probe`/`dispatch`) lives in a fresh `tempfile.mkdtemp`
directory outside the durable root and outside any git worktree, and nothing survives there
between dispatches. Anything from a stage that matters — a promoted `target.py`, a review's
findings, a merged case list, the journal line — is copied into the durable tree in the same
step that produced it, per `write-boundary.md`'s promotion rules. If a dispatch is interrupted
before promotion, nothing is lost from the durable side: the unit's state simply did not
advance, and the next `SKILL.md` §4 command for that unit re-dispatches into a fresh stage.

## Deferred, not built in v0.1

v0.1 dispatches every unit sequentially, so it needs none of the machinery a concurrent,
parallel-dispatch version would: no claim records, no `flock` leases, no per-worker ledger
fragments, no create-once (`O_CREAT|O_EXCL`) publication. These are the right answers to
concurrent writers contending for the same unit and the wrong cost to pay for a sequential
pilot.
