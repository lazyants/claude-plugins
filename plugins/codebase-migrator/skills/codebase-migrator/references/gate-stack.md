# The gate stack — R0 through R5

Per unit, per attempt. R0, R2 and R3 are deterministic and run with no LLM anywhere inside
them; R1, R4 and R5 are the only codex turns. See `SKILL.md` §4 for the exact commands and
loop order; this document is the mechanics behind each rung.

## R0 — eligibility (`ledger.py eligible --root R --unit U`)

Exit 0 only if every one of these holds, each failure named:

- the inventory marks `U` **statically** eligible (no `uncontrolled_input`, `io` or
  `dynamic_call` flag, in `U` or in any unit in its transitive `imports_units` closure), **and
  the inventory is current** — `inventory.json`'s `source_sha256` matches the live bytes for
  every unit in that closure;
- every public symbol of `U`, and every symbol in `U`'s `imported_symbols`, has a frozen row;
  any imported symbol whose unit is not yet ported must be a `one_to_one` row (a shim can only
  re-export a 1:1 name);
- `net.lock.json` has an entry for `U` with `deterministic: true`, `stateful: false`, and
  `coverage_pct >= coverage_floor_pct`; the on-disk `nets/U.json` and `cases/U.json` digests
  match that entry;
- the **live** legacy closure digest for `U` equals the net's recorded `legacy_closure_sha256`
  — no drift in `U` or any dependency since the net was captured;
- `conventions.md` no longer contains the `CHOOSE_CONVENTIONS` sentinel.

R0 is re-checked by `ledger.py converge` and is a precondition of every `sandbox.py dispatch
--kind port|fix|review`. It is the **static** half of eligibility; the **dynamic** half — no
persistent state changed by any observed case — is `net_capture.py`'s `stateful` verdict,
folded into the `net.lock.json` check above.

`net_capture.py` persists every run — success or refusal — to `runs/U/net_capture.json`,
including the `stateful`/`nondeterministic`/`below_floor` payload the run failed with. A
below-floor refusal's `uncovered_lines` there is what `sandbox.py dispatch --kind cases` reads
into that turn's `uncovered_lines.json` pack input, so the coverage-floor loop (`SKILL.md` W3b/
W4c) actually points the next `cases` turn at what the previous capture missed, rather than
repeating a blind guess.

`--kind cases` is dispatched under a lighter precondition, since a net cannot exist before its
cases do: only the unit's **static** eligibility (from `inventory.json`) and a frozen row for
every one of its public symbols are required — no net, no coverage floor, no determinism
verdict.

## R1 — port (codex, one turn)

`sandbox.py dispatch --root R --unit U --kind port`. Writes the target unit against its pack
(`source.py`, the frozen rows it needs, `conventions.md`, the fidelity/naming policy) inside the
write boundary (`references/write-boundary.md`). No LLM judges its own output here — R2 and R3
do that next.

## R2 — mechanical gate (`unit_gate.py --root R --unit U`)

No LLM, no execution of the target code. Checks, each named in the `checks` object:

1. **`target_present`** — `target_file(U)` exists and is not a shim.
2. **`compiles`** — `compile(source, path, "exec")` succeeds. No import, no execution.
3. **`surface_matches`** — the target module's public top-level names equal, exactly, the union
   of `targets` across `U`'s frozen rows. Extra and missing names are listed separately.
4. **`no_stubs`** — no function/method body that is only `pass`, `...`, or
   `raise NotImplementedError(...)`; no `TODO`/`FIXME`/`XXX` in any comment token; no `except`
   handler whose body is only `pass` or `...`.
5. **`no_direct_legacy_import`** — the target module never imports the legacy package or any of
   its submodules directly; other units are reached only through the target package (i.e.
   through a shim or a real port).
6. **`protected_intact`** — every `net.lock.json` entry's `nets/`/`cases/` digests, and every
   `registry.lock.json` row's digest, match what is on disk; `inventory.json` is readable.
7. **`all_rows_frozen`** — every public symbol of `U` in the inventory has a frozen row.
8. **`target_self_contained`** — running the same static analysis `inventory.py` uses on **every
   module in the target's own import closure** (`U`'s target module plus every private helper it
   reaches) finds no `uncontrolled_input`, `io` or `dynamic_call` flag, each named with its
   module. This is a static check; whether the target keeps persistent state is R3's job, not
   R2's — a static rule for hidden state has to enumerate syntax shapes and always misses one,
   while a runtime snapshot compares values and sees all of them the same way.

Exit 0 only if every check passes. The stdout object (`ok`, `unit`, `checks`, `problems`) is
persisted verbatim to `runs/U/r2.json`, with the same three fields R3 appends —
`target_sha256`, `cache_key`, `key_sha256` — computed the same way and checked the same way by
`ledger.py converge`.

## R3 — differential gate (`diff_gate.py --root R --unit U`)

Replays `U`'s captured cases against the **assembled** target-plus-shim system, twice — once
per capture environment — and compares.

**Preconditions**, each refusing by name: the net and cases digests on disk must match
`net.lock.json`; the target file must exist and not be a shim; the **live** legacy closure
digest for `U` must still equal the net's `legacy_closure_sha256` (drift since capture, naming
the changed unit and the recovery: `inventory.py`, `ledger.py accept-drift`,
`net_capture.py`).

**Route rule**, checked on the import route and on every case's route, both recorded as paths
relative to the replay stage:

- the staged **legacy** file of `U` must never appear, at any depth, in either route — a port
  that calls back into its own legacy implementation supplied the behavior instead of the port,
  and is refused regardless of how well the values match;
- any *other* staged legacy file that appears must belong to a unit in `U`'s transitive
  `imports_units` — a dependency's shim being imported, which is legal;
- **a legacy file that is not itself a unit is exempt from the route rule when it is an ancestor
  package `__init__.py`** of a unit in `U`'s closure or of `U` itself — for example
  `legacy/shop/__init__.py`, which holds only a docstring and so is never a unit (`inventory.py`
  never lists it), but which Python still executes every time it imports `shop.pricing`. That
  execution is a mechanical side effect of the import, not a reach the port chose to make, so it
  is not counted as touching the unit's own file and not counted as an out-of-closure violation
  either;
- the staged **target** file of `U` must be present in every case's route (the target was
  actually entered);
- `crossed_shims` — the set of dependency units seen in case routes — is reported so W4 can
  prove the seam actually crossed on at least one case, or record that the pilot has no
  unported dependency.

**Three counts**, which must all agree and be non-zero: `cases_in_corpus` (observations in the
net), `cases_executed` (ran cleanly — `status: ok`, empty `denied`, **and empty
`state_changes`** — in both environments; a port that keeps state between calls fails here even
if its first call matched), `cases_counted` (executed, and the route rule held in both
environments). `cases_counted == 0`, or below `cases_executed`, means the port was never really
exercised.

**Comparison**, per counted case, per environment, on the behavioral channels only (below) —
target vs. the net's recorded legacy observation, with target `$obj` qualnames and
`error.type` mapped back to source names through the frozen rows first (so a faithful port of a
custom exception class compares equal to the source class it replaces). Under
`bug_for_bug_with_exceptions`, cases listed in `exceptions.json` compare against their declared
expectation instead of legacy — and a listed case that still matches legacy fails too, since the
defect it was supposed to remove is still there. Each mismatch is reported as `{"case_id",
"channel", "legacy", "target"}`, where `channel` is the environment and the channel together —
`"A:return"`, `"B:error"` — since the same channel can mismatch in one environment and not the
other; the first 20 are kept in full, and `mismatch_count` always carries the true total even
when the list was truncated.

Exit 0 only when all three counts agree, are non-zero, and there is no mismatch. The stdout
object is persisted verbatim to `runs/U/r3.json`, with three fields appended: `target_sha256`
(the port's digest at the time of this run), `cache_key` (the full key from `ledger.py`'s
`cache_key()`), and `key_sha256` (its digest) — `ledger.py converge` later requires both
`target_sha256` and `key_sha256` on this file to equal the unit's *current* values before it will
accept R3 as still valid.

## R4 — review (codex, read-only)

`sandbox.py dispatch --root R --unit U --kind review --round N`. Judges idiom against
`conventions.md`, security, concurrency, and error paths the net's cases do not reach —
**never** general correctness, which R2/R3 already own. Output is exactly one JSON object,
`{"findings": [...]}`; there is no field for approving the unit (M10) — silence about a defect
is not the same thing as a pass.

## R5 — fix, or an attested refusal

`sandbox.py dispatch --root R --unit U --kind fix --round N` — a second write-capable turn under
the same write boundary as R1. Applies only the findings admitted for round `N`
(`ledger.py admit`); a finding may instead be refused (`ledger.py refuse --reason ...`), which is
a durable record, not a silent drop — the next review round is told what was already decided
(M4, M5). Loops back to R2.

Cap the round count at `max_fix_rounds + 1` (`ledger.py converge` enforces this). On exhaustion,
escalate (`ledger.py set --state escalated`) with the measured state and priced routes — never
ship a capped unit silently.

## The observation contract

Every comparison above — R3's, and the state check inside R2/`net_capture.py` — is built from
one closed set of channels, because the eligibility invariant (`SKILL.md` §0.1: everything
mutable a unit can touch is reachable from its arguments, its receiver, or its return) leaves
nowhere else for a call's effects to go.

**Behavioral channels** (compared): `status`, `return`, `error`, `receiver_after`, `args_after`,
`kwargs_after`, `stdout`, `stderr`.

**Gates, not channels** (any non-empty value means the case is never clean, in capture or
replay): `state_changes`, `denied`, `harness_error`.

`denied` also carries the **tamper check**: every traced window is bracketed by exactly one
start and one stop of the harness's own tracer (`sys.settrace` for capture) or profiler
(`sys.setprofile` for replay). A hidden counter installed alongside the audit hook records every
call to either function; if the window's own start/stop pair does not account for the whole
delta — for example code inside the window calls `sys.setprofile(None)` and restores it, or
calls into the wrong tracer — the case's `denied` list gains `"tamper:sys.settrace"` or
`"tamper:sys.setprofile"`, and the case is never clean. A tampered **import window** denies
every case in that run, not just the one that tampered.

### Canonical encoding

Every value observed — arguments after the call, the receiver after the call, the return value
— is encoded the same way, so a legacy observation and a target observation are always
comparable byte for byte:

| value | encoding |
|---|---|
| `None`, `bool`, `int`, `str` | JSON native |
| `float` | `{"$float": repr(v)}` |
| `bytes` | `{"$bytes": <hex>}` |
| `tuple` | `{"$tuple": [...]}` |
| `list` | `{"$list": [...], "$id": n}` |
| `dict` | `{"$dict": [[k, v], ...], "$id": n}` — insertion order preserved, because order is observable behavior |
| `set` / `frozenset` | `{"$set": <sorted>, "$id": n}` (`$frozenset`, no `$id`, for a frozenset) |
| `bytearray` | `{"$bytearray": <hex>, "$id": n}` |
| an object with `__dict__`/`__slots__` | `{"$obj": "module:qualname", "$state": <encoded attrs>, "$id": n}` |
| a mutable already seen in this observation | `{"$ref": n}` |
| anything else (function, class, module, generator, file, ...), or depth over 64 | `{"$unsupported": "<type or 'depth'>"}` |

`$id` values are assigned in first-visit order across one whole observation, so **aliasing is
part of the encoding**: a call that returns its own (mutated) argument encodes the return as a
`$ref` back to the argument; a port that returns an equal copy encodes a fresh value with a new
`$id`, and the two observations differ even though every value looks equal. This is why a
value-only comparison is not enough — a legacy unit that returns its input and a port that
returns a copy serialize identically until a caller later mutates one and not the other.

A case whose observation contains `$unsupported` anywhere in its behavioral channels is dropped
from the net, named by id, and any line reached only by that case is reported uncovered.

### Determinism

Every capture and every replay runs twice, once per environment below, and every comparison
requires both runs to agree:

| | environment `A` | environment `B` |
|---|---|---|
| hash seed | `0` | `1` |
| time zone | UTC | Asia/Kolkata |
| random seed | `0` | `1` |
| patched clock (seconds) | `1700000000.0` | `1703200000.25` |

A unit whose behavior depends on the clock, the random seed, set-iteration order, or the local
time zone — however the dependency is spelled, including through a module-level alias such as
`clock = time.time` — diverges between the two runs and is caught by this comparison even when
no static flag names the dependency. `datetime`'s own clock is the one residual: it is not
patched (`SKILL.md` §7).

### Why there is no separate collaborator-call channel in v0.1

Every effect a call can have falls into one of four buckets, and each is already covered: (a) on
its return, error, arguments, receiver or streams — compared directly; (b) on persistent state
reachable from the loaded modules — snapshotted before/after every case, any change is a
refusal; (c) outside the Python object graph (files, processes, sockets, threads) — denied at
runtime and flagged statically; (d) inputs from outside the call (clock, randomness, the
environment) — flagged statically and exposed by the determinism comparison. A collaborator's
effects always land in (a) or get refused through (b)–(d), so no fifth channel is needed. The
residual is state the snapshot genuinely cannot read: C-level internals and anything reachable
only through a C registry.
