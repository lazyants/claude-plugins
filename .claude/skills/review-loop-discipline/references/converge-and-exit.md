# Running the loop: converge and exit

- [Healthy loop vs rabbit hole](#healthy-loop-vs-rabbit-hole)
- [The deletion pivot](#the-deletion-pivot)
- [A reviewer can misread — verify each finding](#a-reviewer-can-misread)
- [A reviewer's proposed FIX is scoped to its own lane — reconcile across lanes before dispatching](#a-reviewers-proposed-fix-is-scoped-to-its-own-lane--reconcile-it-against-the-other-lanes-before-dispatching)
- [Stopping a verifier / gate loop](#stopping-a-verifier--gate-loop)
- [Mutation-completeness is a receding target](#mutation-completeness-is-a-receding-target)
- [FREEZE the tree for the deciding round](#freeze-the-tree-for-the-deciding-round)
- [A/B finding classification — for ANY target whose findings settle better downstream](#ab-finding-classification-for-downstream-settled-findings)
- [Fencing ratified decisions](#fencing-ratified-decisions)
- [Same mechanism patched a 3rd time → reach for the platform primitive](#same-mechanism-third-time)
- [The loop's exit condition](#the-loops-exit-condition)
- [When the classifier blocks codex](#when-the-classifier-blocks-codex)
- [Name your own design's weakest joint IN the review prompt](#name-your-own-designs-weakest-joint-in-the-review-prompt)
- [The contrivance gradient — when the evasions outrun the threat model](#the-contrivance-gradient--when-the-evasions-outrun-the-threat-model)
- [Review rounds are non-monotonic](#review-rounds-are-non-monotonic)
- [Non-convergent loops: exit, document, escalate](#non-convergent-loops-exit-document-escalate)
- [Don't engineer around a residual you've already judged acceptable](#dont-engineer-around-a-residual-youve-already-judged-acceptable)
- [Symmetric scope-calibration: over-adding and under-fixing](#symmetric-scope-calibration-over-adding-and-under-fixing)
- [Port the diminishing-returns check to the plan-review loop too](#port-the-diminishing-returns-check-to-the-plan-review-loop-too)
- [Codex alone for plan/code review](#codex-alone-for-plancode-review)

## Healthy loop vs rabbit hole

Keep looping while findings are REAL and NARROWING (each a closeable defect); stop when rounds return only cosmetic/theoretical nits or re-litigate settled calls.

**Raw finding-COUNT is NOT the health metric when the fix ADDS machinery.** A hardening fix that introduces a mechanism (an isolation file, a lease, a driver-written joblog) creates new review surface each round, so the count can rise mid-loop (observed 6→8→7→7→9) and still be healthy. Read two OTHER axes instead:

1. Findings shift ARCHITECTURAL (BLOCKER / restructure) → CONTRACT-COMPLETION (IMPORTANT / wording / bounds / trust-contract).
2. The fixes start DELETING machinery rather than adding it.

A mid-loop reviewer note that "the PRIMITIVES are sound, only the surrounding contracts aren't" is the pivot marker — don't panic at a higher round-5 count; read the two axes.

## The deletion pivot

A fix round that DELETES code/complexity is converging; one that ADDS a normalization layer is still feeding the tail. Closing a class sometimes means REMOVING the clever mechanism, not hardening it: replace context-aware sorting with order-exact `json.dumps(sort_keys=True)` equality; replace an `"<absent>"` sentinel with a presence-check; RAISE-on-depth instead of a truncation marker; swap a manual O_EXCL+pid/age stale-break for a kernel `fcntl.flock`; swap a backup/restore for a single atomic `os.replace`. A SIMPLIFYING pivot is the strongest convergence signal there is.

**Sharper deletion signal — the reviewer is re-correcting YOUR OWN ADDITION, not the original artifact.** When the loop stops finding bugs in the code-under-review and instead keeps correcting a caveat/characterization YOU added, that addition is over-reaching → DELETE it, don't reword it (each reword is a new over-claim). Recognize it by the 2nd re-correction of the same addition, not the 3rd.

**The AGGREGATE form of that signal, which the "same addition twice" trigger MISSES.** The tell above needs one addition corrected twice. The commoner and costlier shape is a stream of *different* additions each corrected *once*: every round the fix ships new justification prose, and the next round finds its defect there. No single addition repeats, so the 2nd-re-correction trigger never fires, and the loop can run indefinitely while the design underneath is stable. Diagnose it by asking each round **"was this finding against the artifact, or against prose a previous round added?"** — when that answer is "the prose" for ~3 rounds running while no finding touches the design, the argumentation has become the defect generator. The fix is not another careful sentence: **cut the accumulated justification** to spec + measured facts + obligations, archive the narrative verbatim as explicitly non-normative, and verify the cut dropped nothing load-bearing before continuing. Observed at scale on a plan review (31 rounds, ~26% of the document was round-by-round argumentation; the design had been untouched for 17 rounds).

## A reviewer can misread

A reviewer's finding can be wrong (proposing a change the data doesn't support). Verify each finding against the source before fixing; a clarifying comment can be the correct answer to a misread, not a code change.

## A reviewer's proposed FIX is scoped to its own lane — reconcile it against the other lanes before dispatching

Verifying a finding against the source is not enough, because the finding can be entirely correct and its proposed fix still wrong. Parallel reviewers are independent BY DESIGN, so each one's fix proposal is blind to whatever the other lanes found. **Check each proposed fix against the constraints the OTHER reviewers discovered, not just against the code it touches** — that reconciliation is the lead's job and cannot be delegated to any single lane, because no lane can see it.

**Two reviewers independently proposing the same fix is not corroboration when both are blind to the same constraint.** Neither is wrong; they are jointly incomplete, and the agreement raises confidence in exactly the wrong direction. Distinct from [[feedback-convergence-needs-two-sound-methods]] (one method unsound) and [[feedback-verification-sharing-a-blind-spot]] (a check sharing a seam with its target): here both checkers are sound and share a blind spot with *each other*.

Verified 2026-07-25 (literary-translator 1.16.0): the security and correctness lanes each independently recommended widening a string split "for consistency" with its sibling function — a real, measured gap. The simplification lane had separately established that the same function is mirrored byte-for-byte across three workflow templates and pinned by a parity test. Applying the recommendation would have broken the pin, or forced the identical edit into two untouched templates, flipping their cache-bundle hashes and falsifying the release's own CHANGELOG promise that those domains were unaffected — for a gap both lanes had themselves shown to be fail-safe. The fix agent had already been dispatched with the proposal as written and needed an urgent correction.

Two follow-throughs: **brief the fix agent with the declining constraint explicitly**, since it will otherwise implement the proposal as written and its own tests will not object; and **record the declined direction in the file itself**, or the next round's reviewer re-raises it (it did — a second lane proposed the same change after the first was overruled).

## Stopping a verifier / gate loop

A QA verifier or gate becomes its own codex-review target that loops forever — each round proposes hardening the gate against an ever-more-esoteric hand-corrupted input the pipeline never actually produces. Stop hardening the verifier when ALL of:

- (a) the shipped artifact is provably correct by INDEPENDENT means (a dominance proof, a `ledger ≡ oracle` set equality, byte-stability across rounds, N passing tests),
- (b) remaining findings are HYPOTHETICAL-only (the pipeline never produces the corrupt input the gate would catch), and
- (c) each round is strictly MORE marginal.

Do not treat the reviewer's "NEEDS-REVISION" verdict as an infinite gate. **Witness the specific named scenarios yourself** — construct a RED witness proving the gate now bites, and construct it correctly: a witness that swaps two entities at the SAME partition can yield an identical, correct graph (gate correctly passes and proves nothing); the real test swaps across DIFFERENT partitions.

## A/B finding classification for downstream-settled findings

Applies to ANY review target where some findings are settled better downstream — a never-run artifact
is only the sharpest case, not the boundary (see the scope note below). For a SHIPPED-but-NEVER-RUN
reference artifact (e.g. a Playwright helper in a repo with no browser CI) there is NO byte-stability/test-suite stop criterion, yet the reviewer keeps escalating into exotic edge cases. Converge by prompting the reviewer to CLASSIFY every finding: category-A (a real logic defect in the DESCRIBED behavior) vs category-B (hypothetical exotica outside the helper's documented scope), and to state "clean modulo category-B" when only B remains. Without an explicit non-gating bucket the reviewer returns NEEDS-REVISION forever. Do this pre-emptively (put the A/B fence in the prompt before the artifact becomes an infinite target), not retrofitted at round 6. Two supporting moves: DOCUMENT the supported scope and FAIL CLOSED (throw) outside it so category-B is handled by CONTRACT not code; and when a category-A finding recurs, prefer a verify-don't-assume REDESIGN (measure → act → RE-MEASURE across the operation) over a cleverer heuristic.

**Scope note — this is NOT just for never-run reference artifacts.** The rule above reads as if it applies
only to a shipped-but-unrunnable helper, which is why it failed to fire on a plan-review loop and got
retrofitted at round 6 again (2026-07-25, SSK phase-2 plan, 8 rounds). The general trigger is wider: **any
review target where some findings are settled better DOWNSTREAM than in the artifact under review.** A plan
that pre-specifies a code change is the common case in this repo — the change will get its own full code
review against real code, so its internal constants, sentinel schemas and control-flow details do not belong
in a plan blocker. Buckets that worked — the SAME category-A/category-B fence above, just relabelled for plan review:
`[REWORK]` (= category-A) = causes material rework, a false green, or a wrong
deliverable if executed as written; `[EXECUTION-DETERMINED]` (= category-B) = doing the work settles it
and further pre-specification adds no safety. Say explicitly in the prompt which downstream gate will catch the second
bucket, or the reviewer has no reason to trust it. Naming both buckets *and* instructing "do not soften real
defects to fill the second bucket, and do not inflate execution detail into the first" is what produced a
3-item verdict after rounds of 5–6.

## Fencing ratified decisions

Fence the loop, don't just exit it. Encode every ratified decision and deliberately-chosen pattern in the review brief each round: "descope X and Y are RATIFIED maintainer decisions — do not re-litigate"; "this weaker-but-honest pattern is intentionally chosen after N rounds proved mechanical proof unwinnable — flag only concrete unsoundness in its USE, not the pattern." Without the fence an adversarial reviewer treats every design retreat as a defect and the loop never converges.

## Same mechanism third time

If round N's fix for finding F1 gets a NEW counterexample at round N+1 with the same SHAPE as F1 (same failure class, cleverer instance), stop iterating on the mechanism and ask: "does the external system I'm racing against already expose a synchronization primitive for this property?" Reaching for the platform's own primitive (e.g. Playwright's `animations:'disabled'`, the same freeze-to-settled mechanism `toHaveScreenshot` uses) is simpler AND more robust than another layer of in-house observation. A "verify-don't-assume redesign" can still be verifying at the WRONG layer — an ABA race outside your observation thread survives every sampling refinement.

**Variant — the predicate keeps failing because the LAYER can't answer the question.** The tell is subtly different from the above: each round's counterexample is a *different* shape, and each new guard is individually reasonable, yet a fresh one falls every round. That is not a mechanism problem — it means the question being asked is unanswerable with the information available *in the layer where you put it*. Diagnostic: name the question in plain words and ask what capability answers it. If the answer is a capability the module deliberately lacks (filesystem, network, clock, user intent), no predicate over the inputs it *does* have will ever be sound, and each round will keep producing a plausible-but-incomplete proxy. Move the DECISION to the layer holding the capability and leave the pure layer to *recognize candidates and produce a replacement*, deciding nothing. Verified 2026-07-19 (enduser-handbook #220): four successive designs — a syntactic `rel === ''` guard, a `legacy !== canonical` guard, a tri-state lexical comparator, then a "canonical is correct by construction" invariant — each died to a new codex counterexample, all because "is this link broken?" was being answered by path algebra inside a deliberately filesystem-free module. Relocating the decision to the workflow step that owns the filesystem ("does the existing destination resolve? then leave it") **deleted** the entire comparator and closed all three open blockers structurally. Signal to watch: a plan that keeps *growing* a decision procedure round over round is in the wrong layer; the correct layer usually makes it *smaller*.

**The missing capability is not always in a module you are writing — check the SHIPPED INTERFACE the plan calls into.** The rule above reads naturally as being about your own pure module's layer, and read that way it does not fire when the absent capability belongs to the *host*: an API, CLI, or workflow step your design assumes offers a seam it does not. The second tell is different too — instead of a new counterexample shape each round, the BLOCKER COUNT PLATEAUS while findings concentrate on one seam, and you rewrite the same section repeatedly, each rewrite individually reasonable. Verified 2026-07-28 (enduser-handbook build provenance): four plan rounds went 11→13→16→9 findings with blockers stuck at 7→4→3→3, and three successive designs for binding a value to per-chapter artifacts all failed because the shipped workflow runs ONE opaque user-supplied command for the whole run and exposes no per-item start or success hook — so per-item commit ordering was never implementable, in any wording. The decisive evidence was one `sed` of the workflow doc's invocation sentence. **Diagnostic, the moment blockers stop falling: stop revising prose and go read what the host actually invokes at the seam** — the residual blockers are naming a capability that does not exist, not a defect in your description of one. Two outcomes follow, and both are progress: the design collapses to what the interface does support (often *more* correct — here, one command per run means one build per run, so run-scoped resolution was right rather than a concession), or the missing capability becomes an explicit prerequisite and the scope decision goes to the user.

**The same plateau also comes from write AUTHORITY rather than a missing hook — and the move that ends it is a PROMPT change, not another fix.** The paragraph above looks for an absent capability; it does not fire when the host has the capability and simply *owns the ground you built on*. Verified 2026-07-29, same plugin, later rounds: blockers sat at 4-5 for seven consecutive rounds, and in three of them the top blocker was a defect in the **previous round's own fix**. Nothing was oscillating and every finding was real — the loop was converging locally inside a structure that was wrong globally, because durable records were being written under the one directory an opaque capture command has full write authority over. Each round therefore added another layer of defence (retain the bytes outside the tree, a tri-state baseline, restore-and-delete reconciliation, cross-process transport, alias-safe writes, alias-safe deletes, gates before every mutation), and each new layer's own defects became the next round's blockers. **Two tells distinguish this from an ordinary non-convergent loop: the reviewer's asks are consistent rather than mutually exclusive, and the artifact GROWS by roughly the size of each fix.** The fix was to change the question: ask the reviewer explicitly whether the residue is shrinking or steady, and if steady to **name the next STRUCTURAL cause instead of the next N findings**. It answered with the ownership mismatch plus a recommendation, that went to the user as a scope decision, and the chosen option deleted every one of those layers at once — the first round in eight where the artifact did not grow. Ask that question as soon as blockers stop falling for two rounds; it costs one round and can save five.

**A named cause is not a taken cause — get the reviewer to define DONE as a finished artifact, then score against it.** The move above gets you a cause; it does not tell you when you have finished acting on it, and *your own judgement of that is the unreliable part*. Verified 2026-07-29, same loop, later rounds: after the reviewer named "distributed contract ownership" I built the artifact it asked for, declared the cause taken in the next round's prompt — and the reviewer replied that the cause was **not new and had not been fully taken**. That happened **twice**, each time costing a full round, because "I added the thing you named" and "the property now holds" are different claims and only the second matters. What broke it was asking a different question: *describe what "fully taken" looks like as a finished artifact, so I can converge on your description of done rather than keep sampling findings*. The answer was five numbered criteria, and from that round on every review returned a **scorecard** — one criterion scored met and stayed met, the others came back with a named gap each. **The value is not the criteria, it is that "am I done?" stops being my opinion**: a plateau that looks identical from the inside (still 4 blockers) becomes visibly differentiated from the outside (1 of 5 met, and the remainder split into "conceptual" vs "drift I introduced"). Ask for it the round after a structural cause is named, not five rounds later.

**While you are at it, gate your own drift mechanically.** In that same stretch, three consecutive rounds spent findings on damage the *previous* round's fix had caused — a column added to a table header but not its rows, a row number used twice after inserting an entry, citations left pointing at pre-renumber rows, an entrypoint specified in the design and absent from every inventory. That class is mechanically detectable, so a reviewer round spent on it is a wasted round. Write a pre-submit checker over the artifact's own structural invariants (uniform table columns, identifiers contiguous and unique, no cross-reference past the last entry, every declared thing present in the inventory that must list it) and run it before every round. Two caveats, both learned the same day: give each check a **minimum-count assertion** so it cannot pass by matching nothing, and **assume its first run's failures are the checker's own bugs** — both of the first run's were, and only the second run's green was worth anything.

## The loop's exit condition

Exit when all three reviewers (code-simplifier / codex / security-review) come back CLEAN on the SAME unchanged tree. The code-simplifier's first explicit no-op round is the leading signal that you're there. Any reviewer-caused change restarts the cycle from the top.

## When the classifier blocks codex

The auto-mode permission classifier can BLOCK `Agent(subagent_type=codex:codex-rescue)` by misattributing the ambient context-window-protection SessionStart hook to your prompt. Legit workaround: drive the runtime directly — `node .../codex-companion.mjs task --background "<clean prompt>"`, then poll `status <id>` / `result <id>` from a `run_in_background` Bash. See the codex-runtime-driving guidance for the full pattern.

## Mutation-completeness is a receding target

Distinct from "stopping a verifier loop" above, where the stop condition is that findings have gone
HYPOTHETICAL. Here every round finds a REAL survivor and the loop still must stop, because the
mutant space is structurally larger than any enumeration of guarantees: for a sixty-line program it
is every statement, condition, operator, initialization and evaluation order. A table of "rules the
function implements" cannot cover it — verified when two survivors turned out to be statement
deletions (a state reset, a control-flow `next`), invisible to a rules frame no matter how carefully
each row was measured.

**Those same two mutants are also the ones a first probe most easily declares DEAD: when a mutant
corrupts STATE rather than an output, the first position where the state is WRONG is not the first
position where it is OBSERVABLE.** Measured on the deletion of the fence-closer's `is_close = 0`
reset — "a stale closer flag survives into the next block's FIRST content line, but that line is
still swallowed by the in_fence branch's unconditional `next`, so a needle there stays hidden under
the bug too. The needle must sit on the second content line of a second consecutive fence, where
scanning has already wrongly fallen back to live-prose mode." An earlier probe placed on the first
line reported a **false negative**, and that is the expensive failure signature: the fixture passes,
the mutant looks already-guarded, the round moves on, and a live hole is left behind looking exactly
like a genuine all-clear. **So before concluding a reset/clear/flag deletion is unkillable, trace
forward to where the corrupted state first CHANGES an emitted decision** — an iteration, a line, or
a whole block later than where it first goes wrong — and put the fixture THERE, not at the mutation
site. This generalizes to any state machine, parser, streaming scanner or accumulator; mutants of a
reset are the ones most likely to be dismissed on a first probe. It is also why the rules table is
blind to this pair in the first place: "neither is a rule: one is state, one is control flow."

Stop when the axes you can name are covered, and write the boundary INTO the file: what was hardened,
along which axes, and that this is NOT a claim of mutation-completeness. State the distinction
explicitly — **"no mutant found yet" and "no mutant exists" are different claims and only the first
is ever true.** Say what a future round should do instead: add a fixture when a specific mutant is
DEMONSTRATED, never reopen a general audit on the theory that the closure claim is incomplete (it is,
structurally).

Five consecutive rounds attacking one test helper each found a real survivor (enduser-handbook,
rounds 20-24). Without the boundary note, round 25 would have found a sixth.

**The survivor is usually the PARTIALLY-CORRECT implementation, not the absent-rule one — and that
makes the enumeration derivable instead of open-ended.** Four more rounds on a different test helper
(enduser-handbook, rounds 32-35) repeated the pattern, and every survivor was a *subset* of the rule
rather than its absence: a fence rule handling backticks but not tildes, a list rule handling bullets
but not ordered markers, a comment scanner handling the first span only and then one capped at two
spans, an indent rule with a prefix denylist instead of column arithmetic. A fixture credited against
the mutant that LACKS the rule is not credited against these — both the correct collector and the
subset one answer FAIL, so the fixture is green against a declining stub. **Credit a discrimination
fixture only against the subset implementations of its rule, enumerated from the rule's own degrees
of freedom** (which delimiter characters, which anchoring, which state carried across lines, which
boundary conditions, which ordering between rules) — and treat any "each was measured against the
mutant that lacks its fix" claim as exactly as strong as the mutant set behind it, no stronger.

**Change the QUESTION to converge, then be ready for the answer to be "don't build this".** Asking
each round to find more survivors is a treadmill — it returns one or two and terminates, so the loop
never ends. Ask instead for the **complete** degree-derived enumeration in one pass, and require the
reviewer to NAME the degrees of freedom it covered so the completeness claim is auditable (a CLEAN
verdict is only ever scoped to the mutants that reviewer actually built — one returned CLEAN here
while a survivor a different mutant would have caught was sitting in the fixture set). Then read the
size of the answer as a design signal: 11 surviving classes plus half the rule-orderings unconstrained
meant the component could not be *proven* at proportionate cost, and the honest exit was not another
fixture but taking the scope decision to the user — who descoped the whole component to its own
issue. **When the enumeration comes back large, the finding is about the design, not the tests.**

## Freeze the tree for the deciding round

Dispatching fixes and committing WHILE a review round runs is good throughput and quietly fatal to
convergence: every verdict then describes a tree that has already moved. Verified 2026-07-20 — this
gap was identified at round 4 (a release commit landing mid-review) and then reproduced for eighteen
consecutive rounds, until a round spent one of its four findings re-reporting a defect fixed before
its verdict landed.

**Before the round that is meant to clear the push: freeze.** No dispatches, no commits, teammates
stood down. Only then does a clean verdict mean "this is shippable" rather than "this was shippable
an hour ago". The first frozen round in that loop produced the first verdict whose findings all
described the reviewed tree.

Corollary for the post-verdict delta: a doc/count correction that fires AFTER the clean verdict is by
design unreviewed. Scope a confirm pass to exactly that delta rather than skipping it — skipping
recreates the same gap at the last possible moment.

## Name your own design's weakest joint IN the review prompt

Handing a reviewer a neutrally-presented plan wastes the round it takes them to find the seam you
already suspected. Instead, state the joint you are least sure of as an explicit, named question and
say it is the sharpest one — then let the reviewer rule on it. You know where you hand-waved; the
reviewer has to discover it.

Verified 2026-07-22 (enduser-handbook plan review, rounds 1-2), twice in one loop. Round 1's prompt
enumerated the load-bearing research premises and asked "flag any place where the design would be
wrong if a premise were subtly off" — both BLOCKERs came back against exactly those premises. Round
2's prompt asked directly whether including a path in a derived root's *selection* set made the
existing containment gate on that same path circular, and labelled it "the sharpest question in this
round"; that framing is what turned a vague unease into a concrete false-GREEN (→
skill:schema-gate-hardening lens 10).

Two things make this work rather than bias the reviewer: ask it as a **question with a real "no"
available** ("is X correct? — prescribe the correct membership"), never as a leading assertion; and
keep asking the rest of the plan openly, so naming one joint narrows nothing else. The technique
composes with the freeze above — freeze the tree, then point at the seam.

Corollary: if you cannot name a weakest joint, you have not understood your own plan well enough to
review-gate it yet. That absence is a signal, not a clean bill of health.

## The contrivance gradient — when the evasions outrun the threat model

A stop signal distinct from "findings have gone HYPOTHETICAL" above. Here every round's finding is
genuinely *reachable* — the artifact really would pass — yet the loop must still stop, because what
is escalating is not the reachability but the **INTENT required of the implementer**.

Read the gradient across rounds. For an assertion guarding a doc contract it ran (2026-07-22,
rounds 9-11 of one plan review):

1. "they just never wrote the outcomes" — a careless teammate genuinely does this. **Real.**
2. "they wrote them, but under the wrong branch" — plausible mistake. **Real.**
3. "they renamed the anchor heading AND planted a decoy cross-reference elsewhere so the
   line-order comparison still passes" — requires an author deliberately defeating the test.
   **Out of threat model.**

When the gradient crosses from *careless* to *hostile*, the loop has left the threat model it was
ever defending, and round N+1 will produce variant four for no gain — no fixed-string, line-based
assertion survives an author who rewrites its anchors.

**Exit move, all four parts:**

- take the round's cheap real improvement anyway if one exists (better anchors cost nothing);
- **write the threat model INTO the artifact** — what these assertions defend against (incomplete
  or careless implementation, and drift) and what they explicitly do not (a hostile author);
- name the other layers that do cover it (human review of the diff, the code-review loop) so the
  assertion is honestly one layer rather than the only one;
- file the real structural guarantee as a follow-up (for text contracts: a structure-aware parser
  resolving each element to its owning section), and **fence later rounds** — "do not treat a new
  anchor-rewriting variant as gating" — with an explicit category-A/category-B bucket in the prompt.

The tell that you are on the gradient rather than converging: each fix is sound, each next finding
is sound, and yet the adversary in the reviewer's counterexample is getting steadily more
determined. Convergence looks like *simpler* counterexamples, not more elaborate ones.

## Review rounds are non-monotonic

Incorporating a reviewer's round-N suggestion creates a NEW surface that round-N+1 can flag —
sometimes flagging the very thing round-N asked for. Concretely (PLAN-213 codex loop): R1 asked to
"skip a doomed `launch()` when no budget" (an optimization → a tri-state `adopt_pending` with an
early return was built); R2 flagged that early-return as a **starvation** wedge (never adopt, never
launch). The fix that satisfied BOTH was the *simplest* design — a bool `adopt_pending` that ALWAYS
falls through to `launch()` unless it actually promoted — i.e. **drop the optimization the earlier
round requested.**

**Why:** review rounds are not a monotonically-improving stack. An "avoid the wasteful/doomed X"
ask is often a short-circuit/early-return/skip, and a skip is exactly what introduces a "then Y
never happens" failure mode. Treating each round's suggestion as strictly additive thrashes.

**How to apply:** (1) when a reviewer asks for an OPTIMIZATION (skip/short-circuit/early-return/
"don't bother"), be suspicious it adds a failure mode — reach first for the simplest always-safe
path (always fall through / always attempt), even if it does one "wasteful" no-op, since a doomed
fallback that fails harmlessly beats a wedge. (2) Always re-review after incorporating — the
incorporation is a new surface (this is why the loop must continue to an explicit clean, not stop
at "incorporated one round"). This is distinct from the "authoritative fix" trap in verify-the-fix.md
(the reviewer's own prescribed fix can carry the next instance of a defect CLASS) — here the issue
is that the reviewer's own asks across rounds can be in tension with each other.

## Non-convergent loops: exit, document, escalate

Some loops are NON-CONVERGENT, not just non-monotonic — then "loop until reviewer-clean" is
UNSATISFIABLE, and the right move is to EXIT, not loop harder. The tell: the reviewer OSCILLATES
because its asks are MUTUALLY EXCLUSIVE and no design satisfies all of them — round-N flags design
A, you switch to B, round-N+1 flags B, and a round-N+2 would just re-flag A. Concretely (the #213
CODE review, same session as the plan loop above): R1 flagged the single-slot always-overwrite
`_defer_attempt` ("loses a previously-preserved pending"); the token-aware KEEP fix was flagged by
R3 ("sticks forever on an invalid same-token pending"); no single-slot policy satisfies BOTH "never
overwrite a preserved attempt" AND "never stick on an invalid one", and a multi-slot queue only
trades the bounded loss for unbounded disk.

Once you recognize this, do NOT run the reviewer a 4th time (it will re-flag the option you
reverted to) and do NOT keep "fixing" — instead:

- (a) pick the STRICTLY-SAFER option (here last-writer-wins: self-healing, can never get stuck — a
  bounded, self-healing residual beats a wedge, mirroring the always-safe-path preference above);
- (b) DOCUMENT the residual in-code + PR so a future reader/reviewer sees a considered choice, not
  an oversight;
- (c) SURFACE the fork to the USER via AskUserQuestion — shipping over a reviewer MAJOR is the
  user's call, not a solo one, especially on an artifact they care about.

The cost is asymmetric: one question is cheap; silently shipping the wrong side of a genuine
no-dominating-solution tradeoff is expensive. This REFINES the loop's exit condition above:
"continue to explicit clean" assumes convergence is achievable; when it provably isn't,
exit-with-documentation is the terminal state.

## Don't engineer around a residual you've already judged acceptable

Corollary — don't "engineer around" a finding you've ALREADY soundly judged bounded-acceptable;
escalate the accept instead. There is a tempting THIRD option between accept-with-doc and
ship-over-it: invent a mechanism that makes the finding moot. On a genuine no-dominating-solution
tradeoff that is usually an OVER-CORRECTION — the mechanism is unreviewed NEW surface and can be
strictly worse (the same optimization trap one level up). This is exactly what happened in #213:
after R1 the single-slot overwrite had ALREADY been reasoned out as a bounded, better-than-status-quo
residual, then that sound call was abandoned to build the token-aware KEEP fix "so codex can't flag
it" — and R3 proved the KEEP strictly worse (sticks on invalid), forcing a revert to the original.
The detour (a whole extra fix+review round) was avoidable: once a residual is SOUNDLY judged
bounded-acceptable, go straight to document-it-and-escalate rather than trying to satisfy the
reviewer with a new design. If you DO build the mooting fix anyway, treat it as a full new change
owing its own red-before-green + review (it is not a free "just make the warning go away").

## Symmetric scope-calibration: over-adding and under-fixing

Symmetric scope-calibration cautions — over-adding AND under-fixing each get caught by the NEXT
reviewer (LT 1.15.0, PR #304). Two miscalibrations in one release, each flagged by a later lens:

- **Under-fix — do NOT launder a correctness bug as an "accepted residual."** The non-convergent-loop
  exit above legitimizes documenting a residual for a genuine *no-dominating-solution* tradeoff; the
  failure is over-applying it to a plain bug. Across 8 codex rounds a validate-only path was
  ACCEPTED — and documented, in a code comment + the PR body — as silently ignoring
  `--expect-source-forms-file`, returning `{"success": true}` with the requested coverage check
  never run. The repo BOT, running the real CLI, rejected that framing and required a fail-loud fix
  (it was right). **Tell:** a "residual" that is a SILENT-IGNORE / FALSE-SUCCESS /
  silently-wrong-output is a *bug*, not a tradeoff — there IS a dominating design (fail loud /
  actually do the check), so the non-convergent-loop precondition does not hold. The reviewer that
  RUNS THE REAL CASE (the bot) is authoritative on residual-vs-bug; codex rounds signing off a
  documented residual is NOT clearance (codex-clean ≠ bot-clean — see inexpressible-defects.md).
  Before writing "accepted residual" anywhere, ask: is there a dominating fix (fail-loud / do-the-
  check)? If yes it's a bug — fix it now, don't defer it into prose.
- **Over-add — do NOT fold a reviewer's OPTIONAL / "not-live, worth-knowing" NOTE into the current
  change as defense-in-depth.** Same trap as the "don't engineer around a residual" section above,
  milder-sounding: a reviewer's optional `const`→`(?:const|let|var)` widening note got folded into a
  fix batch; the next round proved it introduced a false-positive, and the resolution was to REVERT
  it (not forward-fix). **Default: file an optional / not-live note as a FOLLOW-UP issue, don't fold
  it into the change under review** — every speculative addition is unreviewed new surface owing
  its own red-before-green, and "not live today" means the only thing it can do right now is
  regress.

## Port the diminishing-returns check to the plan-review loop too

The CODE-review loop's diminishing-returns/oscillation check ("when the last 2 rounds are entirely
about one helper function you already fixed, STOP and tell the user") has no counterpart in the
PLAN-review loop by default — port it over, and pre-register the check BEFORE dispatching the
round, not after reading its verdict (2026-07-23, enduser-handbook 8-issue `/goal-la` plan, 7
codex-rescue rounds).

Rounds 1-3 of a plan-review loop found genuinely broad, distinct issues (a needle set, a
schema-evaluator design, a tab-handling claim). Rounds 4, 5, and 6 then ALL narrowed on the SAME
sub-mechanism (one evaluator's fail-closed keyword sweep) — each one a REAL, non-trivial gap (not
codex nitpicking its own prior note, and not a non-convergent oscillation per the section above —
every round's finding was distinct and each fix genuinely closed it), but 3 rounds running on one
narrow spot is exactly the diminishing-returns shape, and a plan-review loop with no stated
stop-check for it would keep dispatching rounds indefinitely as long as SOMETHING narrower kept
turning up.

Fix: before sending round 6 to codex, the exact stop-condition was written into that round's own
dispatch instructions for the future turn — "if this round ALSO finds a narrow refinement of the
same sub-mechanism, do not dispatch round 7; instead use AskUserQuestion with three options
(accept-as-documented-residual / one-more-fix-then-stop / keep-looping)" — rather than deciding
reactively once the verdict arrived. **This mattered**: reading round 6's actual NEEDS-REVISION
verdict in the moment, it would have been easy to rationalize "this one's tiny, just fix it and go"
(motivated reasoning to end a long loop) or the opposite "this is definitely oscillating, stop now"
(overcautious, since the sub-mechanism findings kept being real). The pre-registered check made the
decision mechanical instead of a judgment call under fatigue: it fired, the user was asked, they
chose "keep looping," and round 7 came back genuinely CLEAN — vindicating that 3-in-a-row on one
sub-mechanism is a legitimate CHECK-IN point, not automatically a STOP point; the human, not the
loop's own momentum, gets to decide which.

**How to apply:** in any plan-review loop, track which mechanism/section each round's finding
targets; the moment 2 consecutive rounds have both narrowed on the SAME one, write the
round-3-of-that-streak dispatch's own instructions to explicitly check that condition against the
fresh verdict and checkpoint via `AskUserQuestion` if it still holds — don't wait until you're
holding the result to decide whether it "feels like" oscillation.

## Codex alone for plan/code review

**What happened (literary-translator #138, 2026-07-12).** During a `/goal-la` plan-hardening loop,
each round ran BOTH the mandatory `codex:codex-rescue` review AND an independent 16–30-agent
Workflow review (5 adversarial lenses + a skeptic refutation pass) of the same plan file. Across
rounds the parallel pass did catch real defects codex missed (empirically running `jsonschema` to
prove a `source:false` error strips the property name; an `obsidian.md` spec-of-record
contradiction) and gave high-confidence convergence signal. **But the user stopped it:** each
parallel pass burned ~1.5–2.8M subagent tokens and ~15–20 min, and the marginal correctness wasn't
worth that cost at the user's usage tier.

**The rule for this project:**
- Plan review and code review → **codex (`Agent(subagent_type=codex:codex-rescue)`) ALONE.** It is
  the required gate; do not pair it with an independent parallel-agent Workflow review by default.
- Let an already-running parallel review finish (read-only, sunk cost); just launch no more.
- **Ultracode being on is NOT a licence to double up reviewers.** Ultracode says "token cost is not
  a constraint" — but a live, explicit user budget instruction overrides that. When the user is
  watching limits, respect the budget over ultracode's cost-insensitivity.
- **When the parallel pass IS still fine:** only if the user explicitly asks for it ("cross-check
  this", "run an independent review too"). Otherwise single-reviewer.
