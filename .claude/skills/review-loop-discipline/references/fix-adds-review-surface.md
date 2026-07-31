# A fix that adds machinery spawns its own review surface

A fix that inserts new machinery — a validation layer, a new pipeline script, a guarantee in your design prose — is not a one-line change. It creates predictable follow-on hazards the next review round WILL find. Handle them in the SAME round.

## Adding a validation LAYER

This fires for anything that now runs in FRONT of checks that already existed — a schema check, a new gate stage, an allowlist, a coverage check, **and equally an existing gate function reused at a second call site.** That last shape is the one that slips through: calling a reviewed, well-tested gate from one more entrypoint reads like wiring, not like inserting a layer, and the hazards below land in full anyway.

Inserting such a layer creates three follow-on hazards:

1. **Vacuous negative tests.** Existing bypass/violation tests may now trip the NEW layer before ever reaching the check they were written to prove — they still pass, but for the wrong reason (e.g. a test with `block_ids: []` fails schema `minItems:1` before the intended check runs). Re-audit EVERY negative test: each must construct input that is VALID at the new layer but violates ONLY its target invariant. Use a shared fixture-builder so this stays true.

   **Re-auditing by READING is what fails here, so measure it instead: diff the mutation-survivor set against the pre-change commit.** The suite stays fully green, and the fixtures still look correct — what changed is invisible from the test file, because the input now dies upstream and the downstream guard is simply never asked. The mechanical tell is a mutant that DIED before the layer existed and SURVIVES after: its test kept passing while its guard stopped being exercised. Run the same matrix against a worktree at the prior commit and compare the two survivor lists; anything newly surviving is coverage the layer silently retired. (A guard's coverage moving is not the same as the guard becoming redundant — the layer usually refuses only what is present at CALL time, while the guard still owns the window that opens mid-run. Fix by moving the fixture's plant into that window, not by deleting either one.)

   Watch for the inverse while you are in there: a mutant that suddenly kills far MORE tests than its blast radius can explain is usually no longer expressing the property it names — a refactor turned it into a type error. Re-read it before believing the kill.

   A second measurement is worth making at the same time, because branch coverage does not imply BOUNDARY coverage: a comparison can be executed by dozens of tests while none of them straddles its edge, so `<` and `<=` are interchangeable and both mutants live. Instrumenting the branch to throw names every test that reaches it in one run; if that list is long and the boundary mutant still survives, the edge is the thing with no test, not the branch.
2. **The layer's OWN failure modes.** The validator itself can get bad input — a malformed bundled schema crashing `Draft202012Validator(schema)` with a traceback instead of a clean exit. Add the layer's bad-input → clean-error path (a `check_schema()` guard), never a traceback.
3. **What the layer structurally CANNOT express.** JSON Schema can't express cross-references, so a dangling `block_ids` ref / `block.id != dict key` passes green until an explicit referential-integrity check is added. Enumerate the layer's blind spots (cross-refs, referential integrity, ordering) and add explicit checks.

## New pipeline script must satisfy the pipeline's OTHER invariants

A new script slotted into an existing multi-stage pipeline must satisfy the pipeline's other existing degenerate-case invariants, not just its own logic — and these are invisible from the new script in isolation; only tracing how it's actually CALLED surfaces them. Before finalizing a design for new pipeline-integrated code, check it against:

- (a) empty/degenerate-input handling the pipeline's siblings already support (e.g. a downstream step that rejects an empty array which the "everything already known" rerun would produce),
- (b) missing/fresh-state handling (a prompt that unconditionally reads a file the pipeline supports being absent),
- (c) any closed enumerated membership set the new code should JOIN (a fixed derivation-bundle tuple, a cache-key set, a drift-test manifest).

## Guarantee-words and impossible guarantees

GUARANTEE-words in your OWN design prose are over-claim red flags. Before dispatching a hardening plan, grep your own prose for `guaranteed` / `never` / `always` / `unforgeable` / `quiescent` / `isolated` / `atomic` / `sole` / `cannot`, and demand a specific enforcing mechanism for each — reword any that are merely best-effort/defense-in-depth BEFORE the reviewer does. Self-review catches the LOCAL over-claim in a single sentence but MISSES the load-bearing one hiding in a global assumption, so specifically stress-test every "X is isolated/quiescent/can't-happen" against "what if a CONCURRENT actor with broad access does it anyway?" (path-isolation ≠ quiescence under a shared-root writer).

**The impossible-guarantee trap:** defending an architecturally-impossible guarantee with a SECOND mechanism is exactly what the deletion pivot prevents. You cannot defend property P against an actor holding the SAME access as your enforcement mechanism (a by-path gate vs. an in-root `--write` process that can write any path the gate reads) — a snapshot fed to both validators is itself writable/swappable, and adds a new hang vector. No clever by-path/advisory machinery closes it; only a CATEGORICALLY DIFFERENT capability (write-confinement / sandbox / privilege-separation) does. The tell: you're adding your SECOND mechanism to defend the SAME guarantee against the SAME co-privileged actor and the reviewer keeps picking it apart. Front-load the check — ask "does the attacker share my mechanism's access?" the FIRST time a robustness over-claim is flagged, not after building twice. If yes, WITHDRAW the guarantee to the trusted-actor (supported) model where it genuinely holds and mark the full close as an explicit OUT-OF-SCOPE categorically-different mechanism. Never a bigger gate.

Note: each round's own fix-SPEC is the next round's potential bug source — scrutinize the fix design adversarially before dispatching it.
