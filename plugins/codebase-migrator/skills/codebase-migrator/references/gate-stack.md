# The gate stack — R0 through R5

Per unit, per attempt. R0, R2 and R3 are deterministic and run with no LLM anywhere inside
them; R1, R4 and R5 are the only codex turns. See `SKILL.md` §4 for the exact commands and
loop order; this document is the mechanics behind each rung.

## R0 — eligibility (`ledger.py eligible --root R --unit U`)

Exit 0 only if every one of these holds, each failure named:

- the inventory marks `U` **statically** eligible (no `uncontrolled_input`, `io` or
  `dynamic_call` flag, in `U` or in any unit in its transitive closure — `imports_units` plus
  any executable ancestor package unit, below), **and the inventory is current** —
  `inventory.json`'s `source_sha256` matches the live bytes for every unit in that closure;
- every public symbol of `U`, and every symbol in `U`'s `imported_symbols`, has a frozen row
  (`imported_symbols` covers `from pkg.mod import name` directly, and `from pkg import mod`,
  `import pkg.mod as mod` or a plain `import pkg.mod` followed by `mod.name` / `pkg.mod.name`
  anywhere in the file — a whole-module import names no symbol by itself, so every attribute
  chain on it has to be walked too); any imported symbol whose unit
  is not yet ported must be a `one_to_one` row (a shim can only re-export a 1:1 name);
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
5. **`no_direct_legacy_import`** and **`target_self_contained`** (below) are computed
   **together**, over the same walk: every file
   `inventory.closure_files(target_root, target_package, target_module(U))` names — U's target
   module, every private helper it reaches, and every *existing* ancestor package `__init__.py`
   along the way, whether or not that ancestor is
   itself a discovered unit (an executable `shop2/__init__.py` runs at import time just like any
   other file the closure reaches, so both checks must see it). Each problem names its own file,
   not just the module.
   - `no_direct_legacy_import`: no file in that closure imports the legacy package or any of its
     submodules directly, **except** a file that is itself a bridge shim (first line exactly
     `# codebase-migrator: shim for <unit>`) — a shim's whole body is a direct legacy import by
     design (`from <legacy> import <name> as <name>`), so without this exemption an unported
     dependency would fail every unit that still depends on it, which is most of them. **U's own
     target file is never exempted**, even if it happened to look like a shim — `target_present`
     already refuses that case before this check runs, so a port can never disguise itself as one.
   - `target_self_contained`: no file in the closure — shims included — has an
     `uncontrolled_input`, `io` or `dynamic_call` flag. A shim's own body carries no such flag
     either way, but a private helper a shim's dependents still reach must stay self-contained,
     so shims get no exemption here. This is a static check; whether the target keeps persistent
     state is R3's job, not R2's — a static rule for hidden state has to enumerate syntax shapes
     and always misses one, while a runtime snapshot compares values and sees all of them the
     same way.
6. **`protected_intact`** — every `net.lock.json` entry's `nets/`/`cases/` digests, and every
   `registry.lock.json` row's digest, match what is on disk; `inventory.json` is readable.
7. **`all_rows_frozen`** — every public symbol of `U` in the inventory has a frozen row.

Exit 0 only if every check passes. The stdout object (`ok`, `unit`, `checks`, `problems`) is
persisted verbatim to `runs/U/r2.json`, with the same three fields R3 appends —
`target_sha256`, `cache_key`, `key_sha256` — computed the same way and checked the same way by
`ledger.py converge`.

## R3 — differential gate (`diff_gate.py --root R --unit U`)

Replays `U`'s captured cases against the **assembled** target-plus-shim system, twice — once
per capture environment — and compares.

**Preconditions**, each refusing by name: the net and cases digests on disk must match
`net.lock.json`; the target file must exist and not be a shim; the **live** legacy closure
digest for `U` must still equal the net's `legacy_closure_sha256` — drift since capture, naming
every changed file (`changed_legacy_files`) and the recovery: `inventory.py`,
`ledger.py accept-drift`, `net_capture.py`.

**One closure, one file set, used everywhere.** `inventory.closure_files(base, package, module)`
returns every file Python's own import machinery would execute when importing `module`: the file
of each module in its static import closure, plus every *existing* ancestor package
`__init__.py` along the way — whether or not that ancestor is itself a discovered unit. Importing
`a.b.c` always runs `a/__init__.py` then `a/b/__init__.py` first, a docstring-only or otherwise
trivial one included, so a closure built only from discovered units would silently miss files
that genuinely execute at import time. **This is computed to a fixpoint, not a single pass**: an
ancestor's own `__init__.py` can itself import things (`from . import helpers` is a common
pattern), and those imports run on every import of the package too, so every module added to the
closure — ancestor or not — is itself queued and walked for its own imports and its own
ancestors, repeating until nothing new is discovered. A package `pkgf/__init__.py` doing
`from . import helpers` therefore puts `helpers.py` in the closure of `pkgf.sibling` as well,
even though `sibling.py` never imports `helpers` itself — the ancestor did, and the ancestor
always runs. This one function, applied to the legacy side and the
target side, is what the net binding, the cache key (both `legacy_closure_sha256` and
`target_closure_sha256`), R0's drift check, R2's `no_direct_legacy_import`/
`target_self_contained` walk, and R3's route rule all use — there is no separate module-only
closure and no separate "non-unit ancestor init" exemption anywhere in this list; a file is
either in the set or it is not. (`cm_common.unit_closure`, a *dotted-name* closure over
`imports_units` plus ancestor package **units** only, still exists and is used where a set of
unit names rather than files is what's needed — the inventory-staleness check and static
eligibility propagation below.) A flag on an executable ancestor unit — a clock read, an I/O
call, a dynamic lookup — makes every descendant unit statically ineligible (`inventory.py`),
naming the ancestor as an "ineligible dependency", exactly as an explicit `import` of a flagged
dependency would: there is no way to import a submodule without running its ancestor packages'
code first, so the ancestor's flag is unavoidably inherited.

**Route rule**, checked on the import route and on every case's route, both recorded as paths
relative to the replay stage, in this priority order:

- the staged **legacy** file of `U` must never appear, at any depth, in either route — a port
  that calls back into its own legacy implementation supplied the behavior instead of the port,
  and is refused regardless of how well the values match;
- otherwise, any staged legacy file is legal exactly when it is a member of
  `inventory.closure_files` for `U` (minus `U`'s own file, checked first above) — a dependency's
  shim being imported, or an ancestor package `__init__.py` Python had to run regardless of
  whether it is a unit;
- anything else is a violation, named as reaching a legacy file outside the dependency closure.

Membership is now pure set membership — there is no separate case for a "docstring-only ancestor
init," because such a file is simply already in the closure set like any other member.

Independently of the legacy-side checks above, the staged **target** file of `U` must be
present in every case's route (the target was actually entered), and `crossed_shims` — the set
of dependency units seen in case routes — is reported so W4 can prove the seam actually crossed
on at least one case, or record that the pilot has no unported dependency.

**Every case id must be unique, checked at three different points with two different
remedies.** `net_capture.py` refuses (naming every duplicate) if `cases/U.json` itself holds two
cases sharing an id — checked before any capture runs. `diff_gate.py` refuses the same way if
`nets/U.json`'s stored observations somehow hold a duplicate — a defensive re-check, since a
net built by a duplicate-free capture should never have one. Neither is a merge, so neither has
anywhere lighter to fall back to: the comparison pipeline keys every observation by `case_id`
(`compare_captures`, the legacy-vs-target lookup in R3), and a duplicate would silently collapse
two distinct observations into one. **`sandbox.py dispatch --kind cases` treats a repeated id
differently depending on where it collides**, because that step is a merge, not a whole-file
check: a proposed id that only matches one **already in** `cases/U.json` is not an error and is
silently skipped (the existing case is kept, exactly as for any other already-seen id); a
proposed id that repeats **within the same batch** is refused outright, naming every id
involved, and nothing from that dispatch is promoted — silently keeping one occurrence would
pick an arbitrary winner, and keeping both would let the second overwrite the first's stored
inputs the moment they are both appended.

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
defect it was supposed to remove is still there. Before any of that, every declared exception is
**validated**: its case id must exist in the net, `expected` must be a non-empty object, and
every key in it must be one of the behavioral channels — a misspelled channel would otherwise add
a key the comparison never reads, and a stale case id would never apply. Any invalid entry
refuses the whole run, naming each case id and why (`invalid_exception_ids`). Each valid entry is
then checked for being a **no-op**: an `expected` with no fields, or one whose every declared channel
already equals what legacy produces, would let a target that never fixed the bug pass anyway —
the exception would then verify nothing. Any no-op entry refuses the whole run, naming every
affected case id (`no_op_exception_ids`), rather than silently accepting a declared exception
that can never fail. Each mismatch is reported as `{"case_id",
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
`"tamper:sys.setprofile"`, and the case is never clean. **There is deliberately no way to pause
tracing mid-window.** An earlier design let observed code reach a module-level flag through
`sys.modules["__main__"]` (the harness *is* `__main__`) and silence the tracer around a call
while the audit hook's own settrace/setprofile call counters — the only thing the tamper check
reads — stayed unchanged: exactly the kind of enforcement point an attacker could talk the way
around (M7's "capability, not instruction" rule, `SKILL.md` §6, applied one level deeper, inside
the harness itself). A snapshot needed between two windows is now always taken by the caller outside
both, never by pausing one. Concretely, the harness runs the target module's own import as one
window, then the preload list as a **second, separate** window, with `state_snapshot()` taken
between the two (never inside either) to get the preload step's before/after picture; each case
then runs in its own window. A tampered import **or** preload window denies every case in that
run, not just the one that tampered — the two windows' denials and tampers are combined before
being added to every case.

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

**State mode encodes differently from the table above**, because the state snapshot's job is the
opposite of the behavioral channels': never say "unsupported" about something that might be
mutable state. A module, a class or a function defined in the stage each get their own dedicated
encoding (module name; class `dict` with dunders included; function defaults/closure/attrs —
`SKILL.md`'s scope statements and M3 cover why). For everything else state mode would otherwise
have called `$unsupported` — a bound method, a builtin method like `[].append`, a
`functools.partial`, or a plain custom instance — it descends into whatever CPython's own garbage
collector reports as that object's referents (`gc.get_referents`), encoding each one recursively
under `{"$ref_graph": "<type qualname>", "$id": n, "items": [...], "repr_sha256": <sha256 of
repr(value)>}`; a plain instance's referents already include its `__dict__`/slot values, so this
single generic mechanism replaces what used to be a growing list of type-specific rules — the
same three review rounds each found one more shape it missed (function attributes, then class
dunders, then bound methods) before this fallback was added. The `repr_sha256` closes the one
gap referents alone cannot: a C-level immutable value type (`decimal.Decimal`, for one) can hold
its value with no referents at all — `gc.get_referents(Decimal("1"))` is just its class, which
the skip list drops — so a rebind from `Decimal("1")` to `Decimal("2")` would otherwise encode
identically; `repr()` is the one thing that reliably reflects such a value, and a `repr()` that
itself raises reports as a fixed sentinel rather than propagating. Only `type`, code and frame
objects are skipped outright, as pure interpreter bookkeeping with no behavior of their own.

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
