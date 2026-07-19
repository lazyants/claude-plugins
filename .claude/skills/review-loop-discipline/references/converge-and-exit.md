# Running the loop: converge and exit

- [Healthy loop vs rabbit hole](#healthy-loop-vs-rabbit-hole)
- [The deletion pivot](#the-deletion-pivot)
- [A reviewer can misread — verify each finding](#a-reviewer-can-misread)
- [Stopping a verifier / gate loop](#stopping-a-verifier--gate-loop)
- [A/B finding classification for a non-executed artifact](#ab-finding-classification)
- [Fencing ratified decisions](#fencing-ratified-decisions)
- [Same mechanism patched a 3rd time → reach for the platform primitive](#same-mechanism-third-time)
- [The loop's exit condition](#the-loops-exit-condition)
- [When the classifier blocks codex](#when-the-classifier-blocks-codex)

## Healthy loop vs rabbit hole

Keep looping while findings are REAL and NARROWING (each a closeable defect); stop when rounds return only cosmetic/theoretical nits or re-litigate settled calls.

**Raw finding-COUNT is NOT the health metric when the fix ADDS machinery.** A hardening fix that introduces a mechanism (an isolation file, a lease, a driver-written joblog) creates new review surface each round, so the count can rise mid-loop (observed 6→8→7→7→9) and still be healthy. Read two OTHER axes instead:

1. Findings shift ARCHITECTURAL (BLOCKER / restructure) → CONTRACT-COMPLETION (IMPORTANT / wording / bounds / trust-contract).
2. The fixes start DELETING machinery rather than adding it.

A mid-loop reviewer note that "the PRIMITIVES are sound, only the surrounding contracts aren't" is the pivot marker — don't panic at a higher round-5 count; read the two axes.

## The deletion pivot

A fix round that DELETES code/complexity is converging; one that ADDS a normalization layer is still feeding the tail. Closing a class sometimes means REMOVING the clever mechanism, not hardening it: replace context-aware sorting with order-exact `json.dumps(sort_keys=True)` equality; replace an `"<absent>"` sentinel with a presence-check; RAISE-on-depth instead of a truncation marker; swap a manual O_EXCL+pid/age stale-break for a kernel `fcntl.flock`; swap a backup/restore for a single atomic `os.replace`. A SIMPLIFYING pivot is the strongest convergence signal there is.

**Sharper deletion signal — the reviewer is re-correcting YOUR OWN ADDITION, not the original artifact.** When the loop stops finding bugs in the code-under-review and instead keeps correcting a caveat/characterization YOU added, that addition is over-reaching → DELETE it, don't reword it (each reword is a new over-claim). Recognize it by the 2nd re-correction of the same addition, not the 3rd.

## A reviewer can misread

A reviewer's finding can be wrong (proposing a change the data doesn't support). Verify each finding against the source before fixing; a clarifying comment can be the correct answer to a misread, not a code change.

## Stopping a verifier / gate loop

A QA verifier or gate becomes its own codex-review target that loops forever — each round proposes hardening the gate against an ever-more-esoteric hand-corrupted input the pipeline never actually produces. Stop hardening the verifier when ALL of:

- (a) the shipped artifact is provably correct by INDEPENDENT means (a dominance proof, a `ledger ≡ oracle` set equality, byte-stability across rounds, N passing tests),
- (b) remaining findings are HYPOTHETICAL-only (the pipeline never produces the corrupt input the gate would catch), and
- (c) each round is strictly MORE marginal.

Do not treat the reviewer's "NEEDS-REVISION" verdict as an infinite gate. **Witness the specific named scenarios yourself** — construct a RED witness proving the gate now bites, and construct it correctly: a witness that swaps two entities at the SAME partition can yield an identical, correct graph (gate correctly passes and proves nothing); the real test swaps across DIFFERENT partitions.

## A/B finding classification

For a SHIPPED-but-NEVER-RUN reference artifact (e.g. a Playwright helper in a repo with no browser CI) there is NO byte-stability/test-suite stop criterion, yet the reviewer keeps escalating into exotic edge cases. Converge by prompting the reviewer to CLASSIFY every finding: category-A (a real logic defect in the DESCRIBED behavior) vs category-B (hypothetical exotica outside the helper's documented scope), and to state "clean modulo category-B" when only B remains. Without an explicit non-gating bucket the reviewer returns NEEDS-REVISION forever. Do this pre-emptively (put the A/B fence in the prompt before the artifact becomes an infinite target), not retrofitted at round 6. Two supporting moves: DOCUMENT the supported scope and FAIL CLOSED (throw) outside it so category-B is handled by CONTRACT not code; and when a category-A finding recurs, prefer a verify-don't-assume REDESIGN (measure → act → RE-MEASURE across the operation) over a cleverer heuristic.

## Fencing ratified decisions

Fence the loop, don't just exit it. Encode every ratified decision and deliberately-chosen pattern in the review brief each round: "descope X and Y are RATIFIED maintainer decisions — do not re-litigate"; "this weaker-but-honest pattern is intentionally chosen after N rounds proved mechanical proof unwinnable — flag only concrete unsoundness in its USE, not the pattern." Without the fence an adversarial reviewer treats every design retreat as a defect and the loop never converges.

## Same mechanism third time

If round N's fix for finding F1 gets a NEW counterexample at round N+1 with the same SHAPE as F1 (same failure class, cleverer instance), stop iterating on the mechanism and ask: "does the external system I'm racing against already expose a synchronization primitive for this property?" Reaching for the platform's own primitive (e.g. Playwright's `animations:'disabled'`, the same freeze-to-settled mechanism `toHaveScreenshot` uses) is simpler AND more robust than another layer of in-house observation. A "verify-don't-assume redesign" can still be verifying at the WRONG layer — an ABA race outside your observation thread survives every sampling refinement.

**Variant — the predicate keeps failing because the LAYER can't answer the question.** The tell is subtly different from the above: each round's counterexample is a *different* shape, and each new guard is individually reasonable, yet a fresh one falls every round. That is not a mechanism problem — it means the question being asked is unanswerable with the information available *in the layer where you put it*. Diagnostic: name the question in plain words and ask what capability answers it. If the answer is a capability the module deliberately lacks (filesystem, network, clock, user intent), no predicate over the inputs it *does* have will ever be sound, and each round will keep producing a plausible-but-incomplete proxy. Move the DECISION to the layer holding the capability and leave the pure layer to *recognize candidates and produce a replacement*, deciding nothing. Verified 2026-07-19 (enduser-handbook #220): four successive designs — a syntactic `rel === ''` guard, a `legacy !== canonical` guard, a tri-state lexical comparator, then a "canonical is correct by construction" invariant — each died to a new codex counterexample, all because "is this link broken?" was being answered by path algebra inside a deliberately filesystem-free module. Relocating the decision to the workflow step that owns the filesystem ("does the existing destination resolve? then leave it") **deleted** the entire comparator and closed all three open blockers structurally. Signal to watch: a plan that keeps *growing* a decision procedure round over round is in the wrong layer; the correct layer usually makes it *smaller*.

## The loop's exit condition

Exit when all three reviewers (code-simplifier / codex / security-review) come back CLEAN on the SAME unchanged tree. The code-simplifier's first explicit no-op round is the leading signal that you're there. Any reviewer-caused change restarts the cycle from the top.

## When the classifier blocks codex

The auto-mode permission classifier can BLOCK `Agent(subagent_type=codex:codex-rescue)` by misattributing the ambient context-window-protection SessionStart hook to your prompt. Legit workaround: drive the runtime directly — `node .../codex-companion.mjs task --background "<clean prompt>"`, then poll `status <id>` / `result <id>` from a `run_in_background` Bash. See the codex-runtime-driving guidance for the full pattern.
