# Make the defect inexpressible, not merely absent

- [The escalation ladder, and why the first three rungs fail](#the-escalation-ladder-and-why-the-first-three-rungs-fail)
- [The step people skip: delete the alternative path](#the-step-people-skip-delete-the-alternative-path)
- [How to apply](#how-to-apply)
- [Brief the design question, and invite a reasoned refusal](#brief-the-design-question-and-invite-a-reasoned-refusal)
- [When the recurring defect is in the CHECK itself: denylist → allowlist](#when-the-recurring-defect-is-in-the-check-itself-denylist--allowlist)
- [The symmetric-twin tell](#the-symmetric-twin-tell)
- [Treadmill vs convergent — and surface the call](#treadmill-vs-convergent--and-surface-the-call)
- [When a check RECONSTRUCTS a materialized property: read the output](#when-a-check-reconstructs-a-materialized-property-read-the-output)
- [Three sharper moves for rung 4](#three-sharper-moves-for-rung-4)
- [When rung-2 patching is a REGEX, not a table: drop the accidental anchor](#when-rung-2-patching-is-a-regex-not-a-table-drop-the-accidental-anchor)

Verified across eight codex review rounds (2026-07-19/20, literary-translator 1.11.0). One class —
*a mechanism is present, but not engaged on the path production actually takes* — appeared **eight
times**. Every fix was individually correct and verified. Each moved the boundary outward instead
of closing it. Round 6's own fix produced instance seven **against itself, within one round**.

## The escalation ladder, and why the first three rungs fail

1. **Fix the site.** Correct, and the class reappears at the next site.
2. **Fix all N known sites.** Better, but the reviewer's site list has been a SUBSET every single
   round of this release — the enumeration is the weak step. **This applies to the TEST that
   guards against drift, too, and that is the sharpest trap:** a static consistency check written
   to catch one of N duplicated guards diverging enumerated 3 of 4 sites and asserted
   `len(guards) == 3` — accurate when authored, stale within the same round because a peer added
   the fourth concurrently. The newest guard could drift freely and the check stayed green. A
   hard-coded list of files to scan has exactly the failure mode it exists to prevent; DERIVE the
   set (glob the directory for the guard shape) so a new site cannot be silently excluded by not
   being looked at.
3. **Add a shared helper and call it from each site.** This is the seductive one: it *looks*
   structural. It isn't — each call site must still remember to call it, so a site that routes
   around the helper is still writable. Instance eight was exactly this: canon and senses went
   through the new gate; manifest kept a hand-written call to the old ungated comparator.
   **And the parity test written to protect it inherits the same blind spot.** A test asserting the
   helper is byte-identical across its copies is green precisely while a call site never calls it —
   the bytes agree, the behaviour does not. Verified: a containment guard was byte-identical across
   two templates the whole time, which told nobody that a third template's wait site never invoked
   it, and the divergence sat on a false-GREEN verdict boundary. Compare the copies' bytes and you
   have measured the helper; drive a shared table of inputs through each site's REAL entry point and
   compare the outputs, and you have measured what the reader assumed the first test proved.
4. **Make the defect inexpressible.** A table/tuple of the things to handle, iterated by ONE loop
   that is the only code performing the guarded operation. A new member can only be added as a
   table entry; there is no code shape that reaches the unguarded path.

### "Drive it through the REAL entry point" has its own ladder, and each rung is decoy-able one level up

Rung 3 says to stop measuring the helper and measure the site. True, but underspecified in the way
that costs rounds: **a gate is only as strong as the thing it actually OBSERVES, and every way of
"locating the real decision" can be satisfied by a decoy planted beside it.** Measured over four
consecutive review rounds on one release, each round's gate defeated by a mutation the previous
round's gate could not see:

| the gate observes | the decoy that satisfies it | measured result |
|---|---|---|
| the guard call exists, and its offset precedes the verdict's | an unused sibling guard placed **earlier in the file**, real guard deleted | gate green, zero real guards |
| the guard is `!`-negated and `&&`-adjacent to the verdict — same statement | alias the verdict fn, park the correctly guarded expression in an **unused `const`**, point the live branch at the alias | gate green (35 passed), live branch resume-skips |
| an end-to-end run of the real workflow, asserting the expected **call labels** appeared | two semantically empty `agent()` calls carrying those labels, real guard deleted | all assertions green, no real dispatch, no artifact written |
| an end-to-end run asserting the expected **artifact exists** at the expected path | write an expected-looking fixture straight to that path, real operation deleted | green while the guarded operation never ran (raised by a reviewer against this very page; not measured — the row is here because the ladder does not stop at the row above it) |

Each fix was locally reasonable and each was beaten one level up, because each still observed a
LOCATOR or a NAME rather than an EFFECT. Patching the locator to also reject the latest decoy
(reject aliases, reject unused consts) buys exactly one round — the next reviewer plants the decoy
at the level above. The last row is the one to read twice: it arrived as a review finding on the
first draft of this section, which had called the artifact rung the end of the ladder. There is no
rung that is safe by construction.

**The rule: assert an effect only the real operation could have produced — its CONTENT and its
provenance, not the fact that something happened.** "A fragment exists at the expected path" is
still a proxy: a bypass can write an expected-looking file as cheaply as it can log an expected
label. What a decoy cannot fake without doing the work is content carrying identity from the real
inputs — the batch's own `assignment_id` embedded in the prompt that was actually sent, a value
re-derived from the real artifact by the real downstream code, a record whose rejection reason names
the input that caused it. Never a name, label, offset, call count, extracted copy, or bare existence
check. When you catch yourself asserting `"some:label" in calls` — or `path.exists()` — ask what
observable state it is standing in for, and assert that instead.

**And prove the binding with a negative control, in the same round you write it.** Mutate the real
operation away — delete the guard, replace the dispatch with a decoy that produces the same surface
signal — and watch the new assertion fail. An assertion that stays green under that mutation is
still on a proxy, whatever it is named after; the fix is not finished until you have watched it go
red for the right reason. This is the cheapest step in the whole ladder and the one that would have
caught every row of the table one round earlier.

Corollary for the prose: **do not write the absolute.** "This gate cannot be fooled at any level"
was shipped in release notes one round after two absolutes about the same gate had already been
refuted, and the next round refuted it too. State what the gate observes and what that does and does
not establish; the honest sentence survives the next round, the absolute never has.

## The step people skip: delete the alternative path

**Delete the alternative path.** Rung 4 with the old helper left in place is rung 3 wearing a
costume — the exemption is merely unexercised, and the next author (or a refactor) can reach it
again. In one release the path-based comparator was deleted outright and the snapshot-based one
took over its name, so afterward there was literally no function that performed an ungated read.
Confirm with a grep that the deleted symbol has **zero** production callers, not merely few.

## How to apply

- The trigger is a **count**, not a severity: the third occurrence of one class is the signal to
  stop patching sites and change the shape. Do not wait for the eighth.
- Hand the design question to whoever holds the code and invite a reasoned refusal (see "Brief the
  design question" below) — a table is not always proportionate, and "worse than N explicit sites,
  because X" is a legitimate answer worth hearing.
- State the resulting property in docs as the STRUCTURAL claim ("a fourth input cannot be wired in
  without going through the gate"), not the weaker current fact ("all three are gated today"). The
  weaker phrasing is what lets the next author reintroduce the exemption without noticing.
- Then attack your own claim: ask whether the defect is genuinely inexpressible or merely
  inconvenient. Watch for a caller that can construct the operation outside the table entirely, a
  now-callerless helper left lying around as a loaded gun, and any asymmetry in how table members
  are treated — every instance of this class in that release lived in an asymmetry.

## Brief the design question, and invite a reasoned refusal

When a review loop lands on the same defect **class** twice, the reflex is to prescribe the
next fix. Prescribing gets a correct patch and a third round at the next site. What works
better: hand the teammate the CLASS, the proposed shape, and explicit permission to reject it.

**The wording that earned its keep (2026-07-19, literary-translator 1.11.0):**
> "Answer the design question first, explicitly, before writing code: can this be made
> unbypassable by construction rather than by placement? … If you conclude it does NOT hold
> up, say so with the reason and propose what you think is right instead. I would rather hear
> a well-argued 'keep the per-site guards, because X' than get a structural change you do not
> believe in."

The teammate came back having **rejected the naive version of my own suggestion**, with a
reason I could not have supplied: pushing the check down into the parsing function would have
required teaching the agent-facing sentinel protocol a third magic string — the highest
blast-radius surface in that plugin, and the exact surface we had just spent a task repairing.
It proposed an orchestrator-level check instead, mirroring a pattern the reviewer had already
confirmed correct. That answer was better than the original brief, and it only existed because
refusing was framed as an acceptable outcome rather than as non-compliance.

**How to apply:**
- Name the CLASS in the brief ("any failure path that runs before X converts a tamper into an
  advisory result"), not just the instance. Cite the rounds it has already appeared in — the
  recurrence is the argument for redesigning rather than patching.
- State that each prior fix was CORRECT and confirmed. That is what makes the class insidious:
  nothing registers as failure, so the patch-again reflex never trips.
- Propose a shape, then explicitly invite refusal with a reason. Ask for the reasoning, not the
  verdict.
- Expect to approve scope EXPANSION that follows from the redesign (here: applying a new hash
  format to all three stamps, not just the one flagged) — a structural fix applied to one of N
  sibling sites is a half-measure that reads as done.

**Also verified this session:** when a teammate pushes back on a finding you raised, verify
their claim before conceding OR insisting. A `None`-comparison was called a latent crash; the
teammate said it was only a static-narrowing limitation; reading the guards showed they
returned before any comparison — the teammate was right. Saying so plainly is cheap and makes
the next pushback more likely, which is the outcome you want.

## When the recurring defect is in the CHECK itself: denylist → allowlist

The same class recurred in the STATIC TEST written to catch the drift, and there rung 4 has a
specific name. The check began as a **denylist**: enumerate each way a guard could be weakened and
reject it (a `set()` that collapses duplicates, a `len()` blind to identity, a `list(...)` rebind,
a hoist, `sorted` shadowed by a name/def/class, an `IfExp` decoy in a raise message...). Six
rounds, six new weakenings — a denylist can only reject what someone already thought to check, so
it never converges; by round 16 the reviewer's own prescribed fix was "build a reaching-definition
analyzer inside a test." The fix that finally held was to **invert it to an allowlist**: require the
guard to be structurally identical to a small set of canonical AST templates and reject everything
else. That makes "a weakened guard that still passes" *inexpressible* — it is rung 4 for a checker,
the exact analogue of "one loop over a table, delete the alternative path." A denylist is the
checker form of patching sites.

**The cheapest instance of this is a one-string anti-rot pin, and it is easy to miss because it
looks like a guard rather than a denylist.** After a review retires a wrong wording, the natural
guard is a negative pin asserting the retired phrase never comes back. That pin forbids a
*spelling*, not the *error*. Measured 2026-07-27 (`enduser-handbook`): a round retired "code
fences" from an operator halt because a backtick run in a title is an unterminated inline code
SPAN, never a fence; a later round — same author, same file — reintroduced exactly that error as
"a fenced-code run", and the pin stayed green because its needle was the old exact phrase. The
suite reported 606/606 over a defect it had been written to prevent, and the error shipped in a
commit. Same shape as the AST denylist, one rung cheaper to fix:

- **Pin the CLASS, not the retired string** — forbid the concept ("fence", "fenced", any spelling)
  where the mechanism makes it wrong, or
- better, **pair every negative pin with a POSITIVE assertion of the correct mechanism** ("the halt
  says *span*, and says it names any run length"). A positive pin fails on a synonym; a negative
  pin cannot.
- The tell that you need this: you are writing a pin whose justification is "round N decided this
  wording was wrong." That justification is about a concept; a string needle cannot carry it.

The same reasoning applies to the PROSE the pin guards. When an enumeration of a behaviour matrix
has been measured wrong more than twice, stop enumerating and state a positive constraint that is
safe over the whole matrix — deliberately stricter than the code if necessary, and *labelled* as
stricter. Then verify the constraint by searching for a counterexample rather than by re-reading
the list: enumerations fail by omission, and omission is exactly what re-reading cannot see.

### The rung you land on is fine; the SENTENCE describing it is what keeps failing

The escalation above was then run to its end in the same file, and the durable finding is not the
final gate — it is that **every rung's mechanism was a genuine improvement while every rung's
COMMENT claimed the class was closed.** Measured across four consecutive rounds, 2026-07-27,
`enduser-handbook` `reference-assets.test.sh`, each defeat demonstrated by executing the mutation
rather than argued:

| rung | what it asserts | how it was defeated |
|---|---|---|
| negative needle | the retired phrase is absent | a new spelling of the same error |
| positive pin | the correct mechanism sentence is present | a contradiction **added** beside it — a presence check cannot see an addition |
| token count | the section holds exactly N `fenc` tokens | a **paired** substitution: remove one truthful occurrence, add one false one, net zero |
| count + full coverage | every counted occurrence also sits inside a phrase pin | the needle kept VERBATIM inside a sentence asserting the opposite |

That last one is the sharpest and has the cleanest cause. The defeated needle was
`HTML comment or a fenced block anywhere`, and the mutation was
`...anywhere is accepted by this automation` — same needle, same token count, meaning inverted.
**A needle that stops at a noun phrase pins a SUBJECT, and a subject is equally compatible with the
opposite predicate.** The fix is not a new mechanism: extend each needle through the word that
carries the claim's polarity (`... fall outside the subset as well`; `can never sit at the ...`).
**Do not go looking for an acceptance test a needle can pass. There isn't one, and two attempts to
write one were falsified by construction in consecutive review rounds.**

| shipped rule | defeated by |
|---|---|
| "write the sentence that contradicts the claim while keeping your needle verbatim; if you can, the needle is too short" | append after the span: `... fall outside the subset as well ONLY IN THE RETIRED 1.10.0 BEHAVIOR; 1.11.0 accepts every one of these forms.` |
| "could someone REWRITE this claim in place without touching my needle? if yes, extend it" | prefix before the span: `IT IS FALSE THAT Inline code, an HTML comment or a fenced block anywhere ... fall outside the subset as well.` |

Both mutants keep every needle byte-identical and the pinned token count unchanged, and leave every
assertion green over inverted prose. Extending rightward cannot stop a prefix, extending leftward
cannot stop a suffix, and neither stops the sentence being relocated intact under a "retired claims"
heading. **Any finite span has a context that reverses it.**

That makes both rules *unsatisfiable*, which is worse than lax — an unsatisfiable instruction reads
as rigor while producing longer, more brittle pins with the identical hole, and it survived a bot
review and a merge on exactly that appearance. Watch for this shape generally: **a criterion no
artifact can meet looks indistinguishable from a demanding one, and the tell is that you cannot name
a single example that passes it.**

What a fixed-string pin actually answers, and the only thing it answers:

> Are these exact bytes still present, in this section, as the scanner sees it?

That covers DELETION and IN-PLACE MODIFICATION of the pinned bytes — both of which really occurred
here, twice, which is why the pins are worth keeping and worth reaching through the polarity-carrying
word. It covers nothing about surrounding context and therefore nothing about whether the document is
TRUE. Choose a needle by asking which bytes you want to be told about if they vanish or change; do
not ask whether it can be contradicted, because the answer is always yes. Semantic inversion by
context is bounded by review and by a structure-aware reader, never by a string.

Three things generalize.

**The design rule: bound the TOKEN, not the spelling — then cover every counted occurrence with a
phrase pin.** A count alone is a denylist's mirror image: it constrains the total while leaving each
individual occurrence free to be swapped. Coverage is what closes the pair, and it is checkable
mechanically — sum the token occurrences inside the pinned needles and require it to equal the
section's total. Check it per file: two sibling documents describing the same mechanism had
*different* sentence sets (one had a single unpinned occurrence, the other two), so a pin list
copied from one to the other silently leaves a hole.

**The review rule, which is worth more: at each rung the round shipped a comment asserting more
coverage than the mechanism had, and each next round found the gap by attacking the sentence rather
than the code.** The habit that finally converged was to write the residual INTO the comment in a
form falsifiable by execution — "the count detects only a NET change; a paired addition and removal
cancel; a contradiction phrased without the token still evades" — and to treat any sentence you
cannot construct a counterexample to as evidence you have not looked hard enough, not as evidence it
is true. Add to a review brief for any prose-guarding gate: *"try to falsify the comment, not just
the code."* No mechanical gate over prose decides whether the prose is TRUE; a gate can only narrow
the ways a false claim arrives, so the honest artifact is a gate whose description says exactly which
ways remain. Every rung above was confirmed by watching the mutant pass BEFORE the fix and fail after, one
file at a time — a new gate is worth nothing until it has been seen failing on the real defect.

**The third thing, and the one that ends the loop: decide where you STOP, and write the stopping
point down.** A fixed-string pin over prose has no final rung — each round buys one more evasion
class and the next round finds another. Four rounds in, the honest artifact was not a fifth
mechanism but a comment naming the gate's remaining evasions explicitly and pointing the class at
the filed issue for a structure-aware reader. Say "drift detector, never a truth detector" in the
artifact itself. Otherwise every future reader re-derives the ladder from scratch, and — the actual
cost here — every round's own comment quietly promises the rung above the one it built.

## The symmetric-twin tell

Even after the allowlist, two more rounds each found the SYMMETRIC OTHER HALF of the previous
round's fix (rounds 17-18): round 17 tightened the FUNCTION-header `sorted`-shadow walk → round 18
found CLASS headers uncovered; round 17 fixed the MESSAGE-VARIABLE anchor path → round 18 found the
DIRECT-RAISE path. When a reviewer keeps returning the mirror image of your last fix, you are on
rung 3 (fixing sites) inside the checker: **unify the split paths into ONE routine that both forms
dispatch through** (one `_expr_guarantees_anchor` for both raise shapes; one header-walk for
function+async+class+lambda), and the whole symmetric family closes at once — plus a variant
nobody asked about (lambda parameter defaults leak into the enclosing scope). Enumerate the axis
(all def-types; all message-forms) and prove it exhausted, rather than waiting for the next twin.

## Treadmill vs convergent — and surface the call

Distinguish the two failure shapes out loud: a NEW-CLASS finding each round (denylist, unbounded —
a treadmill) vs a BOUNDED-FACET / symmetric-twin finding (the structural match missing one node
facet — convergent, severity decaying). When a review loop runs long, characterize which you're in
and hand the proportionality decision to the user rather than looping silently — especially for a
defense-in-depth check whose four real guards already have behavioral tests, where the marginal
value of round N is shrinking. "I predicted a fixed point and was wrong once" is worth saying;
mispredicting convergence twice is the signal to stop or centralize (one guarded helper would have
mooted the whole check).

## When a check RECONSTRUCTS a materialized property: read the output

2026-07-22, LT 1.13.0 `orphaned_owners`. A distinct checker-form of rung 3, and the treadmill wore a
new costume. A diagnostic answered "does this entity have a backlink anywhere?" by RECONSTRUCTING
the renderer's link decision from upstream models — and every reconstruction diverged from the
renderer's ACTUAL behavior at an edge where two subsystem definitions disagree (the gate groups by
casefold `normalize_form`; the renderer keys NFC-exact + drops `sense_translated` + never links a
target absent from the rendered prose). The predicate walked three reconstruction rungs, each a
real false-pos/neg a reviewer caught: `no-appendix` (an inline-linked-but-uncovered owner
false-FLAGGED) → `+require-de-linked` (a `sense_translated` owner, never de-linked yet never linked,
MISSED) → `+not-in-build_entity_index-map` i.e. linker ELIGIBILITY (a target eligible but never
occurring in rendered prose → no link emitted, MISSED). The fix that held: stop reconstructing the
decision; READ THE MATERIALIZED OUTPUT — scan the actual emitted `[[…]]` links from the rendered
notes (reuse the scan the inline-advisory already runs), symmetric with how the appendix side
already reads the real Mentions region. **When the system already EMITS the property you're
checking for, ground the check in that emitted artifact; a reconstruction from N upstream models
keeps failing at every edge those models reconcile differently.** The count-trigger still applies —
by the 2nd reconstruction edge, jump to the output instead of patching a 3rd model. Variant-specific
tell: a static/plausibility reviewer (codex) FALSE-CLEARED the eligibility reconstruction TWICE; the
reviewer that RUNS THE ACTUAL CASE (the repo bot) is authoritative for emission-vs-eligibility gaps
— the gap is invisible to shape-tracing (codex-clean≠bot-clean). The reconstruction shares the wrong
model with the thing it checks — the same blind-spot shape as any check built out of the same
assumptions as the code it verifies.

## Three sharper moves for rung 4

2026-07-23, LT 1.15.0 — one class closed five times. The class "a hand-maintained parallel list
drifts from the thing it mirrors" recurred five times in one release (two hint strings, a subset
tuple, a magic per-mode guard, a forgotten mode). Three refinements to how rung 4 is actually
reached:

- **Bring the escaping OUTLIER into the table as DATA, not a parallel guard.** The instance that
  keeps escaping every table-driven guard is usually the one that isn't a table row at all — a
  fallback/legacy/default path handled by a special case. The legacy bare-`--batch` merge slipped
  past every guard because it selected no row; the fix was a `dest=None` row plus two-phase
  selection (flag-rows first, fallback only if none matched), and it closed the class with **zero
  new guards** — the row inherits every current AND future column. When you catch yourself adding
  the Nth special-case `if` beside a table, the real fix is almost always "make the outlier a row."
- **Derive the set by the ACTUAL property, not a PROXY for it.** "Derive the set, don't hard-code
  it" (rung 2 above) is necessary but not sufficient: a three-layer lock derived its consume-site
  set from *"functions that call `hasOnlyKeys`"* — the ESTABLISHED PATTERN — so all three layers
  were blind to a guard that reimplemented the defect WITHOUT calling it (`raw.exit_code !==
  undefined`, no `in`, no `hasOnlyKeys`). Deriving by a convention only ever protects code that
  already follows the convention. Derive by the property that actually MAKES it a member (here:
  "receives an untrusted agent return", determinable at the call site) — or, if that isn't
  statically decidable, say so in a truthful comment naming the residual rather than shipping the
  proxy as if it were the real predicate.
- **A redesign must PRESERVE the property; "simpler" that drops the guarantee is abandonment, not
  rung 4.** Invoking the count-trigger, replacing a 200-line JS lexer with behavioural tests was
  proposed — and a reviewer refuted it in one line: *a behavioural test cannot cover a site that
  does not exist yet, because calling it requires knowing its shape.* The redesign silently dropped
  the exact property the lexer existed to hold. The real answer was a THIRD layer (roster +
  execution ON TOP of the static locks), because static and behavioural cover disjoint blind spots —
  synthesis, not substitution. Before banking a simpler shape, construct the case the old shape
  caught and prove the new one still catches it. And for a safety LOCK specifically, bias toward
  false-RED (loud, someone fixes it) over false-GREEN (silent) — a lexer tuned to reduce false
  alarms by guessing "regex vs division" bought that with a silent bypass, which is the wrong trade
  for a gate. The proportionality ceiling still holds: measure it (49 raw `in` occurrences vs 1 in
  code — a non-lexing check is 48:1 noise) before arguing a lock is "too big".

## When rung-2 patching is a REGEX, not a table: drop the accidental anchor

2026-07-24, enduser-handbook #258 citation-audit design. A plan-review loop found "one more
citation form the matcher misses" **three rounds running** (13 → 27 → 32 → 47 counted instances
across rounds 1-4) while designing a lint for `(see "X" above/below)`-style doc citations. Each
round's fix was rung-2 patching wearing a regex costume: round 1 handled only the parenthesized
form; round 2 added comma-separated and compound (one direction word, multiple quoted targets)
forms; round 3 dropped the `see` verb requirement entirely after the reviewer found citations with
no verb at all. Only round 4 came back clean — and the reason wasn't "we finally enumerated every
sentence template," it's that round 3's fix **deleted the anchor that was never actually part of
the invariant**. The true structural signal was never "the word `see` precedes a quoted title" —
it was "a quoted title sits near a direction word," full stop; `see` was an incidental feature of
most examples, not a defining one, and every round before round 3 was unconsciously treating an
accident of the first few samples as load-bearing. Once the accidental anchor was dropped (matching
ANY quoted string immediately followed by `above`/`below`, verb or no verb, comma or no comma), the
pattern-space this checker needed to cover collapsed from "an open-ended list of sentence shapes"
to "one proximity relationship" — an instance of rung 4 (inexpressible, not enumerated) applied to
pattern/regex design specifically, not to a code table. **Tell that you're rung-2-patching a
regex**: each successive review round names a **new sentence template** your pattern doesn't match,
not a new edge case within a template you already handle. When that happens twice, stop adding
alternation branches and ask what the actual invariant is, independent of any verb, noun, or
punctuation your current samples happen to share. (A useful side effect of the resulting
over-match: because only quoted strings that exactly match a real heading title get asserted, an
unrelated quoted-string-near-"above" false match just lands in a tracked "unresolved" allowlist
rather than corrupting a result — over-matching into a verified bucket is safer here than
under-matching via one more special case.)

A reviewer's own prescribed fix can carry the very next instance of the class it was meant to
close — see verify-the-fix.md, "An authoritative fix still needs review".
