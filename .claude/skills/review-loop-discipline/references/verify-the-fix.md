# Verify the fix before saying done

- [Cover the DOMAIN, not the ticket's example](#cover-the-domain-not-the-tickets-example)
- [Widening a matcher needs its own over-correction check](#widening-a-matcher)
- ["Looks redundant" is a reading, not a measurement — full-dataset diff proves it](#looks-redundant-is-a-reading-not-a-measurement)
- [Any reword/rename: verify the REPLACEMENT is accurate](#any-rewordrename)
- [A rebuild can regress what the original got right](#a-rebuild-can-regress)
- [A measured threshold and its gate must share ONE predicate, as code](#a-measured-threshold-and-its-gate-must-share-one-predicate-as-code)
- [State what your proof GUARANTEES — check the axis](#state-what-your-proof-guarantees)
- [A measured count is a property of the SAMPLE — publish the condition](#a-measured-count-is-a-property-of-the-sample--publish-the-condition-not-the-number)
- [Source-completeness before trusting a rebuild](#source-completeness)
- [Cross-check your own artifacts](#cross-check-your-own-artifacts)
- [Reproduce the gate's clean env](#reproduce-the-gates-clean-env)
- [RED evidence read from a MOVING ref stops being evidence at the commit](#red-evidence-that-reads-its-before-from-a-moving-ref-stops-being-evidence-at-the-commit)
- [A staged RED gate must be SATISFIABLE by its owner](#a-staged-red-gate-must-be-satisfiable)
- [A coverage claim needs the same evidence as the thing it covers](#a-coverage-claim-needs-the-same-evidence)
- [Comparing a probe's verdict before and after the fix settles nothing on its own — in EITHER direction](#a-probe-that-says-the-same-thing-before-and-after-the-fix)
- [Revert the whole file — the pins that SURVIVE say it was never guarded](#revert-the-whole-file-and-read-which-pins-survive)
- [A never-varied argument is an untested argument — instrument, don't re-review](#a-never-varied-argument-is-an-untested-argument)
- [A pin can CEMENT a wrong claim — pinning is not review](#a-pin-can-cement-a-wrong-claim)
- [Ask which correct behaviours have NO gate at all](#ask-which-correct-behaviours-have-no-gate)
- [An authoritative fix still needs review](#an-authoritative-fix-still-needs-review)
- [A bare `assert` guard vanishes under `python -O`](#a-bare-assert-guard-vanishes-under-python--o)

## Cover the DOMAIN, not the ticket's example

A ticket's repro is a MINIMAL example, usually the simplest instance of the class. A fix plus a regression test that both key off that one example give false confidence — the suite is green because it only tests the example, not the domain, and the gap survives precisely because nothing exercises the domain's real inputs. Concrete: a name-fusion fix that broke on the script's `TERMINATORS` set passed the ticket's `"Fiona. George"` repro and a 1126-test suite, but `TERMINATORS` omitted the em-dash `—` — the DOMINANT dialogue delimiter in French/Russian/Spanish literary prose, i.e. the tool's core domain — so `"Fiona. — George"` still fused.

When fixing a bug in a domain-specific processor (parser, tokenizer, extractor, formatter), before calling it done: enumerate the DOMINANT real-domain inputs of the bug's class and test the fix against EACH, not just the ticket's example. An adversarial review briefed to check the fix AGAINST THE DOMAIN (not just "does it pass") is what surfaces the gap even when the suite is green.

## Widening a matcher

Not every domain char/case is safe to add — a widened fix needs its OWN over-correction check (a second adversarial pass hunting regression). And that check needs REAL domain data: widening `ELISION_RE` to split capitalized `L'`/`D'` passes clean PLAN review yet breaks real fixed compounds (`D'Artagnan`, `L'Aquila`, `D'Annunzio`, `L'Oréal`) — only code-level review running the actual widened regex against real proper nouns catches it. A clean plan-review pass is NOT evidence against over-correction; if the domain has known fixed-form exceptions, have an adversarial code reviewer test the fix against them explicitly.

## "Looks redundant" is a reading, not a measurement

When a piece of code looks redundant with logic elsewhere in the SAME file, do NOT trust the
redundancy argument from READING alone — the two paths can cover overlapping-but-not-identical
populations, so one is a strict superset of the other on exactly the inputs your reasoning skipped.
Only an empirical before/after diff across the FULL dataset — not just the cases motivating the
change — reveals the gap. Concrete: `re.IGNORECASE` in `audit_name_annotations.py`'s
`_compile_form_pattern` looked dead, because `generate_case_paradigm` already emits both the
capitalized and the lowercased form of every name as separate literal candidates, so case-sensitive
matching should lose nothing. Removing it to fix two collisions broke 8+ previously-clean entities:
the redundancy held only for paradigm-GENERATED forms, never for the hand-declared literal-case
`ru_match_forms` strings, each frozen in whatever case it happened to have where the annotator first
saw it — and a first-word-of-sentence capital legitimately alternates with a mid-sentence lowercase
across different occurrences of the same word. Eyeballing the two segments the change targeted
showed nothing; only the full-corpus diff surfaced the regression. Snapshot a full baseline
immediately BEFORE any shared-code change, so a concurrent agent's unrelated edit landing between
your two runs cannot be mistaken for — or mask — your own regression.

## Any reword/rename

Removing a wrong term X can INTRODUCE a new inaccuracy. A blanket replacement can be token-correct yet semantically wrong (universalizing a built-in's internals onto every adapter). Verify the REPLACEMENT is accurate, not merely that X is gone — a regression test that only checks X's ABSENCE false-greens the bad replacement, so add field-specific POSITIVE assertions (the field must NAME the right owning component). Then sweep every restatement (CHANGELOG, the new test's docstring) and fix the whole class.

- **When the reworded document is measurement-bearing, assert the no-new-figure invariant
  MECHANICALLY — do not diff-read it.** A clarifying reword is precisely the edit that silently
  introduces or perturbs a figure, and a human reading the diff will not notice a number that
  merely moved sentences. Two cheap checks, run together: diff the SET of numeric tokens in the
  file against the previous commit and require it unchanged ("verified mechanically that the set of
  numeric tokens in the file is unchanged against the previous commit"), and hash the fenced code
  block the prose quotes (one docs-correction branch held its block at sha256 prefix
  `f64c801742e6bbc0`, byte-identical across all six of its commits). The pair turns "this round
  only clarifies wording" from a claim into a checked property. The block hash alone is the weaker
  half: it says nothing about the prose quoting the block's OUTPUT, which is where the perturbed
  figure lands. The technique arrived on round 6, after five rounds of number defects — it is not
  something anyone invents under time pressure, so reach for it at the start of a docs-correction
  loop rather than at its end.

- **Sweeping every restatement is not enough: the restatements are NOT equal, and the one that
  loads FIRST is the one that steers behaviour.** When a finding is refuted or corrected in a
  reference doc, correct every surface that SUMMARIZES it in the same change, and treat the
  index/router bullet as the load-bearing one — in a progressive-disclosure skill the router is
  read on EVERY load while the reference is opened only conditionally, so a reader who trusts the
  summary never reaches the correction and the stale summary silently wins. Each file stays
  internally coherent, which is why nothing looks wrong. Verified 2026-07-25: a pre-registered
  replication — 3 replicates per cell, blind cross-model judge, counterbalanced presentation
  order — refuted an n=1 bake-off's "high won / xhigh over-reaches" conclusion, and
  `.claude/skills/codex-runtime-driving/references/model-effort-bakeoff.md` was rewritten
  accordingly; the one-line router bullet in `.claude/skills/codex-runtime-driving/SKILL.md` was
  missed and kept presenting the refuted result as "the durable finding". ONE missed site, caught
  only when the router was read directly rather than the reference — and not cosmetic: the stale
  summary steered toward `high` for faithful/fidelity-risk work, against the standing rule that
  fidelity-risk translation pins `xhigh`, so the entry point actively inverted a policy-relevant
  recommendation while the reference it pointed at said the opposite. Where possible make the
  summary POINT at the reference instead of restating its conclusion; where it must restate, the
  correction is not finished until the router has been re-read.

- **The trigger is ANY change to a reference, and ADDING a case is the reading that gets missed.**
  Everything above is written around a summary that went STALE — it asserts something the reference
  now refutes, so there is a false sentence to find. Widening a reference fails differently and more
  quietly: the router entry stays perfectly TRUE, merely narrower than what now sits behind it, so
  the new case has no entry point and is never selected. Nothing is stale, nothing is false, every
  file is internally coherent, and no check fires — frontmatter parses, the description is under
  cap, the markdown is valid, `git diff --check` is clean. The only symptom is that the added rule
  never loads for the situation it was written for, which is indistinguishable from never having
  written it. Verified 2026-08-02, and the sharpest available demonstration that the narrow trigger
  is the problem: three references were widened in one change while all three router entries kept
  their old scope, and the bullet you are reading now was OPEN IN THE EDITOR at the time — it did
  not fire, because its own trigger says "refuted or corrected" and that change was an addition. A
  reviewer caught it, and the commit documenting "a rule existed and did not fire" had reproduced
  that failure inside itself. **Whenever a reference gains a case, widen its router entry in the
  SAME commit, then re-read the entry and ask the routing question directly: would a task about
  this new case have any reason to open this file?** If the entry does not name the new trigger,
  the case is unreachable. Progressive disclosure means the router is not documentation of the
  reference — it is the only thing that can select it.

## A rebuild can regress

**This fires for replacing a MECHANISM or a documented RECIPE, not just a data-producing artifact.** The trigger is any wholesale swap made to close a review finding — the replacement silently inherits the obligation to handle every input state the original handled, and the states the old mechanism covered for free are the ones you will forget — a state the old one absorbed implicitly will never appear in the finding you are fixing, which is exactly why the every-dimension diff below has to be the thing that catches it. Verified 2026-07-25: a `git stash push`/`pop` recipe was replaced with a `git diff` + `git apply` patch flow to close a foreign-stash hazard. `stash` covers staged *and* unstaged content; `git diff` is the worktree-vs-index delta, so it silently omits the index — a peer's STAGED edit was destroyed by the following `git checkout HEAD --`, and the patch was non-empty, so the recipe sailed past its own emptiness check. The fix traded a loud shared-stack hazard for a silent data-loss one and needed a whole extra review round.

When you REPLACE a rejected artifact with a "more rigorous" rebuilt pipeline, it can silently REGRESS a dimension the original got right. A fresh independent re-derivation left 126 of 240 entities unresolved (33% coverage) where the rejected original's reused-data path had 97.5%. **When you replace a rejected deliverable, DIFF the new output against the old on EVERY dimension** — a rebuild that fixes complaint A can silently regress dimension B the old one handled. "More rigorous" is not "better" until measured. Prefer REUSING an existing artifact's already-correct data over a from-scratch re-derivation unless you have MEASURED that the re-derivation covers at least as much.

## A measured threshold and its gate must share ONE predicate, as code

When a gate's threshold is calibrated from a measurement ("clean is ~2%, degraded is ~54%, so fail above
5%"), the gate must reuse the **same predicate code that produced those numbers** — never a prose
restatement of it. Re-deriving the predicate in words silently drifts from the script that measured the
band, and the drift is invisible: the sentence reads correct, the threshold looks calibrated, and the gate
is wrong. Verified 2026-07-25 (SSK phase 2): one integrity gate was specified wrongly **four rounds
running** — too tight; then inverted (as a rate, `1/27 < 1/20`, so it failed clean text and passed degraded);
then correct in direction but with `token_count` undefined and a live division-by-zero; then defined with
predicates that did not match the measuring script at all. Only copying the measuring script's actual
predicates ended it — and doing so also revealed the corpus was **~4× more degraded** than every prior
round had reasoned from, because the prose counter used `len(token)==1` where the real one counted Hebrew
letters *inside* the token. (Absolute bands and the corpus detail belong to the worked example, not here.)

**How to apply.** State the gate as integer arithmetic over named counters (`20*single <= total`, not "no
more than 1 per 20" — a rate reading inverts easily); assert the denominator is non-zero before dividing;
and pin test vectors for the exact cases where the two predicates could diverge, since a behaviour-equivalent
copy is only kept in step by those vectors. Corollary worth its own reflex: **if a measurement lands between
your documented bands, suspect your predicate before inventing an intermediate band** — a weak predicate
manufactures a middle that does not exist. Worked example, with the corrected bands and predicates:
`skill:literary-translator-run` → `references/source-prep.md`.

**Audit the published block itself: a fenced block that IS the gate spec can back only SOME of the
section's numbers while reading as fully code-backed.** That appearance is exactly why such a gate
survives round after round — every reviewer treats a section with a Python fence as measured. Two
mechanical tells, both found in one block: (a) **a helper DEFINED AND NEVER CALLED** — `is_heb_mark`
was defined and never called, and the `detached` gate it belonged to "had no code at all, only
prose"; (b) **a pinned vector that contradicts the shown code** — the vector line pinned `ב-`
(Bet plus a hyphen) as excluded, while the shown code put it in `heb` AND counted it as a
single-letter fragment. So audit by checking that every counter the prose cites is COMPUTED by a
line that actually runs, and by EXECUTING the block rather than reading it: extract the fence out
of the markdown, run it against the real inputs, and republish every number from that execution.
Pinned vectors only keep two predicates in step once something runs them.

**A published band must be rounded OUTWARD, or it excludes its own data.** Measured instance, in
that same section: garble was stated as 54-61%, bracketing outward (54.30 floors to 54, 60.87 ceils
to 61), while clean was stated as 1.9-2.7% with its floor rounded to NEAREST — so the clean band
excluded its own lowest measured variant, 1.89%. Two rounding conventions in one section, and the
wrong one is invisible by construction: 1.9 looks like a correct rounding of 1.89. The rule is one
sentence — "a range presented as bracketing its data should contain it". The half a reviewer will
get wrong is the SEVERITY: the confirm-pass reviewer classified this as "a rounding display
artifact rather than a defect", which is defensible in general and wrong here, because the
section's entire subject is published numbers agreeing with the code that produced them. **The
document's own thesis sets the severity of a rounding nit, not the size of the discrepancy.**

## State what your proof guarantees

A completeness "proof" can validate the WRONG axis. An anchor gate that proves *every emitted backlink resolves to a real heading* (no DANGLING links) does NOT prove *every mention is found* (no MISSING mentions) — these are orthogonal. Before claiming "complete", state in ONE sentence exactly what your check GUARANTEES, and confirm it is the SAME axis as the requirement.

## A measured count is a property of the SAMPLE — publish the condition, not the number

An honest benchmark becomes a false claim the moment it is restated as a rule. The count is
correct for the inputs it ran on; the rule generalises it to inputs it never saw. The tell is that
nothing looks wrong — the measurement is real, the `n` is stated, and the prose beside it still
overclaims.

Verified 2026-07-25 (literary-translator, sentinel gluing). Measured that 15 of 16 characters
gluing prose to a verdict sentinel defeated a whole-line equality check, and wrote the rule as
"**any** character glues it". False: the mechanism is `trim()` on each LF-delimited line, so the
count depends on the fixture SHAPE, not the character. Same function, same 16 characters:

| reply shape | hides the sentinel |
|---|---|
| `prose + GLUE + SENTINEL` | 15/16 — prose is on the line regardless, so `trim()` never gets a chance |
| `GLUE + SENTINEL`, no prose | 7/16 — `trim()` strips 9 of them and those are seen correctly |

The canonical, domain-side write-up of that measurement lives in
`plugins/literary-translator/skills/literary-translator/references/canon-and-glossary.md` (search
`15 of 16 over \`GLUE_CHARS\``); this section exists to state the REVIEW rule the measurement
produced, not to be a second copy of it.

Costs that a condition-first statement would have avoided: the overclaim propagated into a PR body,
a shipped code comment and two teammate briefs; one brief instructed a fixture design that could not
have discriminated anything; and two artifacts that were each correct — a comment listing NBSP among
the defeaters, a doc saying NBSP is trimmed and still matches — read as a contradiction until both
shapes were executed side by side.

So, before a measured number goes into prose: **state the condition the mechanism actually turns on,
and name the sample the count came from.** Four different fixture sets in this one round gave 11/12,
14/16, 15/16 and 16/16 — every one correct, and any two of them quoted without their sets look like
a defect. If two artifacts must both carry a count, each names its own sample; if only one may, it
is the pinned test, and prose points at it rather than restating the number.

Corollary for the "obvious" member of a set: **verify membership, do not infer it from the name.**
JS `trim()` strips U+2028 and U+2029 but NOT U+0085 NEL, which reads exactly like a line break. A
negative control built on the plausible-looking member silently proves nothing.

Corollary one step earlier, about DERIVING the count rather than generalising it: **never publish a
count of tests or assertions obtained by grepping the helper's name once** — the same string also
appears in echo banners, section headers, labels and comments, so a grep count is an upper bound,
not the count. Derive the number a second, differently-shaped way — a runtime tally of what actually
executed, reconciled against the source grep — and identify the specific non-assertion lines BY NAME
before the figure reaches a changelog, PR body or report. Twice on one branch: at round 25 the
source held 21 self-test assertions while a runtime scan showed 22 matching lines, the extra being a
plain echo banner; at round 6, earlier and the same trap, a naive grep reported 3 self-tests where
only 2 were assertions — "the apparent third self-test is an echoed section banner" — and that
round-6 miscount reached a committed CHANGELOG line before it was corrected.

## Source-completeness

A linking-failure bug can MASK a source-completeness gap. Diagnose at the source, not just the join: a rebuild from an aggregate can fix the reported linking failure while REGRESSING whole partitions that silently produced zero rows upstream. The tell is a per-partition row count of the source (e.g. chunk-02=42, chunk-03=42, chunk-01/05/06=**0**) — not a formatting artifact. Always check source COMPLETENESS (every expected partition non-empty) before trusting a rebuild, even when the reported bug is only about linking; backfill empty partitions from an alternate per-segment source, gated on declared-partition == resolved-partition and every key a verbatim heading.

## Cross-check your own artifacts

The tell for a regression is often an internal contradiction between your OWN artifacts: a registry storing `source_mention_count: 4` for the very entity the index renders as `Mentions (0 ch): —`. Cross-check derived artifacts against each other (registry-count vs index-count) before presenting — a self-contradiction is a free, high-signal defect detector.

## Reproduce the gate's clean env

A green LOCAL test run is not proof of shippability — a CI/review gate under declared-deps-only + a different OS surfaces env-masked failures your machine hides. Two masking causes: an UNDECLARED ambient dep (a package you happen to have installed that makes a check effective locally but a silent no-op under declared deps — e.g. `rfc3987` making jsonschema's `format:"uri"` bite), and an OS-specific tmp path (a guard rejecting a `durable_root` under a `tmp`/`temp`/`scratchpad` component fires under Linux `/tmp` but not macOS `/var/folders/…`). Reproduce the gate's env on macOS BEFORE claiming shippable:

- Fresh venv, declared reqs only: `python3 -m venv V && V/bin/pip install -r requirements.txt` — a venv excludes user-site, stripping ambient extras. Confirm the suspected extra is ABSENT: `V/bin/python -c 'import rfc3987'` → ModuleNotFoundError.
- Force Linux-like tmp: `TMPDIR=/tmp V/bin/python -m pytest` — on macOS `/tmp` → `/private/tmp`, which DOES contain a `tmp` component, so tmp/path-sensitive guards fire exactly as on CI.
- Run the FULL suite that way; green ⇒ shippable. A single knob (`TMPDIR=/tmp`) can reproduce the bulk of the failures instantly; the clean venv reproduces the dep-masked remainder.

## RED evidence that reads its "before" from a MOVING ref stops being evidence at the commit

A gate that proves the defect by re-reading the source at a baseline must FREEZE that baseline as a
40-hex SHA. `git show HEAD:<path>` is the trap: it returns the pre-fix tree for exactly as long as
the work is uncommitted, and returns the POST-fix tree from the release commit onward.

What makes it expensive is the direction it fails in. If the gate asserts "the baseline still fails",
it goes permanently red for a structural reason and the next person deletes it. If it *skips* when
the baseline already carries the fix — the reasonable-looking guard — the gate degrades into silence:
green, running, reporting nothing, and indistinguishable from a gate that ran. Verified: four
red-evidence gates had already become silent skips on the release commit before anyone noticed, and
the published suite figure was arithmetically impossible for the tree it was attached to, which is
what surfaced them.

- Freeze the SHA, and assert it is still an **ancestor** of HEAD — not merely that it resolves. A
  rebase leaves the old object resolvable for a while while the branch no longer descends from it,
  at which point every row's provenance is void and `rev-parse` still passes.
- Under a frozen ref, **retire the skip**. "The baseline already has the fix" was a real state worth
  standing down for while the ref moved; frozen, it can only mean the SHA is wrong, and skipping on
  that hides precisely the thing worth knowing.
- Claims introduced *by* the release under review need their own baseline — they occur zero times at
  the pre-release SHA, so a single-baseline set cannot express them and presence-before fails on
  correct rows.
- **That second baseline lives only on the feature branch, so it silently makes the MERGE METHOD
  load-bearing.** Both squash and rebase invalidate the pinned SHA — it stops being an ancestor of
  the target and the ancestor assertion goes red — but their recovery costs are not the same, and
  conflating them overstates the rebase case. Measured on a throwaway repo rather than reasoned:

  - **Rebase is recoverable.** The intermediate commit is replayed onto the new base under a fresh
    SHA carrying the same content, so a commit holding the pre-fix tree still exists on the target.
    Re-point the constant at it — locate it by message and patch identity, and diff its tree against
    the old one rather than trusting the message, since a rebase that resolved a conflict can alter
    the very content the rows measure. One constant changes; no row is re-derived.
  - **Squash is not.** The squash writes one new commit whose parent is the target's tip, holding the
    branch's FINAL tree and never referencing its intermediate commits at all. Verified by walking
    every commit reachable from the target: none carries the intermediate tree, so no re-point
    satisfies the gate and no constant edit fixes it.

    Keep two problems apart here, because conflating them sends you hunting for the wrong thing.
    *Finding the object* is usually easy — the intermediate commit survives on the feature branch,
    and a deleted branch of a merged PR can be restored from the PR page; reflog and
    `git fsck --unreachable` are the last resort, not the first. *Satisfying the gate* is the part
    that has no fix: the assertion demands an ancestor of the TARGET, and after a squash no such
    commit carries the pre-fix tree, however easily you can still read that tree elsewhere. So the
    cost is re-deriving every affected row against whatever commit does hold the pre-fix state — or
    changing what the gate asserts, which is a larger decision than this bullet covers.

  Check it before merging, not after: `git merge-base --is-ancestor <pinned-sha> origin/<target>`
  answering false means the SHA is branch-only, and `gh api repos/<owner>/<repo> --jq
  '{squash:.allow_squash_merge, merge:.allow_merge_commit, rebase:.allow_rebase_merge}'` says which
  routes are even reachable — squash being the unrecoverable one. Repo practice is not protection —
  a repo that has always merged with merge commits will still offer squash in the UI.

  Then remove the silence, because a constraint nobody can see is the actual defect: state it on the
  pinned constant itself, and branch the assertion's failure text on *which* baseline failed so the
  branch-only one names the squash/rebase signature and its own remedy — re-point at the replayed
  commit after a rebase, re-derive the rows after a squash — instead of a generic "history was
  rewritten". Verify that text actually renders — an `assert`'s message is evaluated only on failure,
  so a passing run never executes it; force the failure against a real non-ancestor commit in a
  throwaway worktree, not a garbage SHA.

## A staged RED gate must be satisfiable

When you stage a failing assertion BEFORE the fix (red-before-green across a team, where one owner
writes the gate and a different owner writes the prose/code that turns it green), the gate must be
satisfiable **by edits that owner is actually permitted to make**. Check this at authoring time, not
when the owner reports failure.

The failure shape: a `hasnt`-style casualty needle whose text ALSO matches a line the plan explicitly
tells that owner to PRESERVE. The gate can then only go green by violating the plan, so a teammate
doing exactly the right thing still fails. Verified 2026-07-19: a casualty
`group-free manifest (shipped 1.4.1 form, unchanged)` was intended to bind one bullet but also
matched a second, preserved bullet elsewhere in the same file; fixed by prefixing the discriminating
words that made it unique. A needle punishing correct work is worse than no needle — it teaches the
owner to distrust the gate.

Cheap authoring checks, all `grep`, before handing the gate over:
- **casualty uniqueness** — `grep -nF <casualty> <file>` must return EXACTLY the line(s) that owner
  is supposed to change; if it also hits a preserved line, discriminate it further;
- **post-edit needle is genuinely RED now** — zero matches inside the bounded section AND whole-file;
- **the owner's allowed file set contains every line the gate implicates.**

Related: distinguish **replacement** rows (a casualty exists; the paired `hasnt` proves the old
wording is gone) from **addition-only** rows (new prose where none existed; `hasnt` is legitimately
absent). Asserting a universal pairing you do not have invites someone to invent a casualty for an
addition-only row, which manufactures exactly the unsatisfiable gate above.

## A coverage claim needs the same evidence

When you build a table of "which rules are guarded", a row justified by REASONING is not
verified — only a row where you ran a specific mutant and watched a specific fixture go red is.
The two failure phrasings to distrust in your own table: **"transitively covered by the other
fixtures"** and **"every fixture depends on it"**. Both sound conclusive and neither is a
measurement.

Verified 2026-07-20 (enduser-handbook, 18-rule scanner enumeration): six rows verified by
running a mutant all held; the two rows justified by inference were both wrong. "Transitively
covered" failed because the cited fixtures exercised only the *closing* direction, so nothing
proved a deeper heading stays inside. "Every fixture depends on it" was true and irrelevant —
prefix matching still matches exact strings, so every fixture passed while an extended heading
wrongly bound.

Closure criterion: a rule counts as guarded ONLY if a mutant was run and a fixture went red. No
transitive arguments; no "no realistic mutation exists" unless one was attempted and its
impossibility can be stated. The claim that something is covered is itself a claim.

## A probe that says the same thing before and after the fix

A scratch probe built to demonstrate a defect, or a fixture built to pin it, can fail to REACH the
condition it names while reporting a perfectly plausible verdict. Its own "did the hook fire"
assertion does not catch this, because the hook DID fire — somewhere else.

**Run the probe against the pre-fix commit AND the post-fix commit before believing either answer.**
A detached worktree at each, or the module copied out with `git show <sha>:<path>`, is minutes of
work. It is necessary, and on its own it settles nothing in either direction.

**An identical verdict at both is INCONCLUSIVE, and stopping there is the trap.** A probe that never
reached the code under test and a fix that genuinely does not work produce the same two readings;
the comparison cannot tell them apart, and the two conclusions send you in opposite directions.
Guessing "faulty probe" is how a real failed fix gets dismissed — the more comfortable guess, and
the one whose cost lands after the release.

**A CHANGED verdict is weaker evidence than it looks, too.** It says the probe separates those two
TREES — not that it reached the target window, and not that the fix is why. Two commits from history
differ by more than one fix, so an unrelated guard that also moved can flip the outcome while the
staged sequence never happens; that certifies a mis-keyed probe just as readily as the identical
case dismisses a real one.

Both directions need the same thing, which is why it is worth getting first: evidence that the
staged condition was REACHED, or a delta that isolates the fix.

- **Reachability:** a trace or counter of the target call showing the seam fired inside the window
  that matters — not merely that it fired at all.
- **An isolated delta:** revert the fix IN PLACE on the post-fix tree rather than reaching for an
  earlier commit, so the two runs differ by those lines and nothing else. A known-good positive
  control — a tree where the defect is present and this probe is known to catch it — is the same
  move.

Anything else is attribution by adjacency.

**The same trap runs in reverse, and that is the direction this section kept failing to cover.** A
probe is just as often built to REFUTE a reviewer's reported defect — to show the topology they
described does not actually reach a bad outcome — and a probe that fails to reach the condition
answers "refuted" just as plausibly as it answers "unfixed". Read the failure to reproduce as what it
is: evidence about the topology you BUILT, never about the finding. Before downgrading anyone's
severity on it, produce the same reachability evidence the fix direction demands — show the decision
you claim is safe was the one that actually ran, rather than an earlier guard refusing first and
masking it. Measured: a reported false-accept was probed, an earlier bracket refused, and the finding
was downgraded to "masked" in a shipped commit message; the real topology needed ONE more directory
created before that bracket passed and the reported decision decided, at which point a previous
build's file was committed as the current run's output exactly as reported. One `mkdir` was the whole
distance between "does not reproduce" and a confirmed BLOCKER.

**A reviewer's own `NOT EXECUTED` marking raises the bar on you rather than lowering it.** The
instinct is to treat a source-proved-only finding as weaker evidence. It is the opposite: they have
told you precisely which step they could not perform — a sandbox that denies `mkdtemp`, no network,
no credentials — so that is the step whose absence their reasoning could not check, and the one your
reproduction has to get right. Their inability to run it is a map of where to look, not a discount on
the claim. A reviewer who marks its own limits is more trustworthy, not less; treat an unmarked
finding with more suspicion than a marked one.

The generator is an interposition seam placed by ORDINAL — "the Nth call of `realpathSync` on this
path", "the first `openSync` of the token" — when an earlier CALLER owns that position. Verified:
a probe removing a symlink at the first resolution of a configured path had its removal land in an
ownership gate that runs before the function under test, so the sequence it staged never occurred;
it returned the same ok-with-foreign-bytes result at the two pre-fix commits AND at the commit that
had just closed the defect. Read alone, that says a just-shipped fix does not work — a false result
about one's own work, from a probe whose hook fired every time. The reachability evidence that broke
the tie was COUNTING the resolutions of that path in one run: there were two, and the ownership gate
owned the first. Re-keyed to the second, the probe separated the three commits immediately — and
only that re-keyed run licensed the conclusion, not the three matching verdicts before it. Note what
the three commits could NOT have supplied on their own: they came from history and differed by more
than the fix, so even the changed verdict would have been attribution by adjacency. The count is
what carried it.

Its permanent-fixture twin: a seam keyed on the CONFIGURED path string while the module reads the
RESOLVED one interposes on nothing at all. That fixture passed against the unfixed module because an
unrelated guard happened to refuse the input — and what exposed it was asserting the halt's MESSAGE;
a fixture asserting only the halt class would have been committed as coverage of a defect it never
touched.

So: key a seam on something that identifies the WINDOW — the exact string the module passes, a flag
an earlier stage sets, the state the seam itself observes — rather than on a position in a call
sequence. Where a count is unavoidable, measure the sequence once and record in the fixture's own
comment which caller owns each position, because the next change to any caller silently renumbers it.

## Revert the whole file and read which pins SURVIVE

**After fixing a defect in text or code that already HAS coverage, revert the WHOLE edited file,
run the FULL suite, and read which PRE-EXISTING assertions stay green.** If only your new
assertions go red, the claim was never guarded at all — that is a coverage GAP, not a regression in
guarded text, and the two call for different follow-up. Running the counterfactual against only the
affected file's own assertions cannot tell the two apart, because the pins that would have caught
the defect are precisely the ones you did not think to look at.

Two independent runs on one branch, both decided by the survivors:

- A wholesale revert of `obsidian-vault.md` left all FOUR pre-existing link-integrity item-2 pins
  green — they targeted applicability, chapter-scope, gate-removal and the mode-neutral label, and
  none touched what the gate classifies as a link. The gate's overbreadth was completely unguarded
  in both directions.
- Reverting either of two edited files left exactly the 2 new pins failing and all 355 others
  green — including the 5 pre-existing manifest-discipline pins and the 6 pre-existing
  revalidation pins, none of which reached the claims being fixed.

This is the INVERSE direction of the rule banked in `close-the-class.md` →
[Mutating an unguarded rule: read the SPREAD of the
reds](close-the-class.md#mutating-an-unguarded-rule-read-the-spread-of-the-reds). That one mutates
the helper and counts the blast radius of the reds; this one reverts the fix and counts the
SURVIVORS. Same suite run, opposite question — neither substitutes for the other.

## A never-varied argument is an untested argument

A parameter that every call site passes the SAME value for is not tested, it is hardcodable:
inline that value inside the function and the suite stays green, because no fixture ever asked for
anything else. Measured (enduser-handbook 1.6.0): `chapterHasWikilinkTo`'s `slug` was passed the
literal `'orders'` at all 24 call sites, so hardcoding it inside the function passed all 30
assertions — and that predicate gates whether a manual-migration REMOVAL may proceed, so it would
have been silently broken for every slug except the fixture's.

The same shape hits THRESHOLDS, where the thing that never varies is the distance from the
boundary: three `> 1` comparisons in that file were only ever exercised at exactly 2. Mutating
`duplicateSlugHalts`' `count > 1` to `count === 2` left all 158 tests green — under that mutant
three identical slugs return `[]` for BOTH manifest kinds, restoring the silent overwrite the
release exists to prevent. The cause was not three careless sites: "Every duplicate fixture in the
suite used exactly two occurrences, including the round-10 and round-11 companions added earlier;
a suite-wide blind spot, not three sites." Worst instance was `findContainer`, where three
ambiguous candidates fall past both the multiple and the single check into `zero`, telling the
caller to CREATE a section when three already exist.

**Close this class by instrumentation, not by another review round.** The calibration to keep:
three consecutive adversarial rounds each found ONE instance of this family and under-reported the
class by seven sites; one mechanical sweep found them in a single pass. The sweep is
project-agnostic — wrap every export, capture the real argument tuples from an UNMODIFIED suite
run (a run altered to collect them measures the instrumentation, not the suite), flag every
parameter whose value never varies, then cross-check each flagged row against literal source
before reporting. Yield, with its method attached: "all 14 exports wrapped, 219 real call tuples
captured from an unmodified suite run, every non-VARIED row cross-checked against literal source
before reporting" — four gaps the three review rounds had not reached, plus three threshold
boundaries exercised at only one value.

The trigger to stop reviewing and start instrumenting is the same defect FAMILY landing in two
rounds running. A third round returns a third instance, not the class.

## A pin can cement a wrong claim

A pin locks in whatever it points at. If the underlying claim is wrong, the pin does not catch
the defect — it entrenches it, and makes the eventual fix noisier by turning a prose correction
into a test failure. **Do not treat an assertion's existence as evidence its claim is correct**;
check the claim against whatever owns the behaviour.

Happened twice in one loop (enduser-handbook 1.6.0), both times pinning text a brief had
specified: a recipe step prescribing one link form for a category with two target types, and a
Related-block rule inlining one example as representative of both. In each case the reviewer's
next round had to break the pin before the prose could be fixed.

Corollary for scope: do NOT pin a claim a legitimate future fix would need to change (e.g. a
verified negative like "adapter X has no equivalent requirement" when a filed follow-up may add
one). A pin that opposes a correct future edit is the same failure as one that cements a wrong
claim — it imposes test-scaffolding cost on work that isn't a regression.

**Stopping rule for the sweep that adds the pins: name the mutation the assertion catches and that
mutation's real consequence, or do not add it.** A pinning sweep has no natural stopping condition
— every occurrence looks pinnable, the sweep's own momentum pushes toward pinning all of them, and
each pin is permanent maintenance plus a future false-red fighting a correct edit. Recorded as the
reason for a deliberate exclusion inside a sweep that classified 86 raw matches and pinned every
other load-bearing call in the file: "`README.md`'s `anyGroup`/`relative()` mentions were
considered and deliberately excluded: that prose is advisory guidance for a future adapter author,
not an imperative governing this skill's behavior, so no mutation with a real consequence could be
named for it. A gate whose mutation cannot be named is decoration." In a repo where docs ARE the
production path, this is also the test that separates normative prose from advisory prose. With
the two rules above it, the three decide what NOT to assert.

## Ask which correct behaviours have no gate

Every audit lens above asks whether a GATE proves its claim. None asks the inverse: **which
correct behaviours have no gate pointing at them at all?** That class is invisible to mutation
testing (nothing is missing or wrong), to argument-variation analysis (the parameter is fine),
and to reading (the code is right).

The generator is: a behaviour proved correct by a scratch probe during development, never
converted into a permanent fixture. The proof happened and then evaporated.

Verified 2026-07-20: a fence-length rule was probed correct in one round, and the permanent
self-tests added the next round covered a different axis — sixteen rounds then inherited a
correct rule whose guard did not exist. The following round found the same shape one level up:
the section boundary that made two other pins *independent* was itself unguarded, so removing
the call one pin protected would have left both green.

A manual probe shows the code is correct now; only a permanent fixture shows it stays correct.

**The standing instance of this class in THIS repo is the command examples inside the skills.**
Every skill under `.claude/skills/` ships copy-pasteable recipes, and the repo's gates — structural
validation plus the doc-assertion suite — assert on TEXT only: "Neither was covered by the
structural validation or the doc-assertion suite, which do not execute command examples." A
documented command can therefore be reproducibly broken and ship through a fully green suite, which
is why the blind spot reads as coverage: you have to notice an absence, not read an error. Measured
(n=1 snippet, one branch, so treat the rate as illustrative and the surface as real): ONE ~5-line
shell snippet — the scratchpad-staging / git-exclude recipe in `skill:codex-runtime-driving` —
accumulated THREE separate reproducible bugs over three consecutive review rounds ("Third bot
finding on this snippet, and correct"), final score review-bot 3, test suites 0. The three:
`tr '\n' ' '` with no following `tr -s '[:space:]' ' '`, so the wrap-tolerant grep recipe
reproduced the exact false-negative it existed to prevent; an unscoped `git rev-parse` /
`git status` while the copy targeted `<worktree>`; and `-C` alone not scoping a `>>` redirect.
Each was reproduced before being fixed — the commit closing the first pair records it plainly,
"Both reproduced before fixing" — which is the only reason the count is worth anything, and that
reproduction is also the missing fixture. Until something executes them, the review bot IS the gate
for every command example in the repo.

## An authoritative fix still needs review

Verified 2026-07-20 (literary-translator 1.11.0, codex rounds 10→11). Codex prescribed a bounded
fix in precise terms: build a key→snapshot map inside each digest and compare it against the
constant tuple "with an exact-key-SET check". That was relayed verbatim; the teammate implemented
it faithfully; it was verified on disk, measured for digest byte-identity, and committed.

A **set collapses duplicates**. A fourth descriptor reusing an existing key compared equal, passed
both guards, and caused one input to be stamped and checked twice while the new one was never
represented at all. That was instance ELEVEN of the same class the fix was written to close (see
inexpressible-defects.md).

Every link in the chain did its job. Reviewer authored, implementer applied, lead verified — and
the defect rode through all three, because each link was checking *fidelity to the prescription*
rather than *whether the prescription was right*.

**Why this is not just "review harder":** the prescription arrived with earned authority — the same
reviewer had, one round earlier, correctly overruled a refusal and produced a bounded fix that
dissolved a blast radius that had been wrongly priced as unavoidable. Being right that recently is
precisely what makes the next instruction land unexamined. The trust was justified; the exemption
was not.

**How to apply:**
- **Attack the prescription's data structure, not just its intent.** `set` vs sorted list, `>=` vs
  `==`, "contains" vs "equals" — the reviewer names a shape in a sentence; the sentence cannot
  carry its own edge cases. Ask what the chosen structure DISCARDS: a set discards multiplicity, a
  dict discards order, a count discards identity.
- **Relaying is not reviewing.** When passing a reviewer's fix to an implementer, the brief should
  contain your own reading of what could still go wrong, not just the quote. If the relay is
  verbatim, nobody in the chain has independently thought about it.
- **A finding can be RIGHT while the mechanism it cites for WHY is wrong — verify the cited fact
  before repeating it in your fix or docs.** Verified 2026-07-21 (1.11.0, post-merge bot round):
  codex's P1 on the conservation gate was valid (a `min`-anchor reduction misses interleaving), but
  its supporting claim — "duplicate `order_index` is separately invalid per the schema" — was
  false: `manifest.schema.json` only types it `integer>=0` and `validate_extraction.py` never
  mentions it; the real enforcer is `assemble.py`, which fatally *raises* `duplicate_order_index`.
  Writing a docstring qualifier citing the schema (as codex named it) would have replaced one false
  claim with another. Accept/reject the finding on its merits, but grep the codebase for the
  enforcer it names — a reviewer citing the wrong file is common, and its citation is not evidence.
- **The verification that was run was real and still insufficient** — byte-identity, zero
  deletions, `-O` behaviour, suite green. All true, none of it aimed at the guard's discriminating
  power. Verifying that a fix is *faithfully applied* is a different question from whether it
  *closes the class*; run both, and say which one was run.
- Send the fix back through the loop even when its author is the reviewer. A round that only
  confirms "yes, you did what I said" is cheap; this one returned instance eleven.

## A bare `assert` guard vanishes under `python -O`

Python's `-O` flag strips every `assert` STATEMENT from the bytecode. So a guard written as

```python
assert set(snapshots) == spec_keys, "..."       # GONE under -O
```

does not merely stop reporting — it stops existing. The function proceeds as if the invariant
held. A `raise` is never stripped:

```python
if sorted(snapshots) != sorted(spec_keys):      # survives -O
    raise AssertionError("...")
```

`AssertionError` is still the right exception TYPE for an internal-invariant violation; it is the
bare `assert` *statement* that is unsafe, not the exception class. Keeping the class means callers
and tests that expect `AssertionError` are unaffected by the rewrite.

**Why this is worse than an ordinary disabled check:** the failure is silent, mode-dependent, and
invisible in review — the source reads as guarded, the test suite (which runs without `-O`)
passes, and only the optimized production path is unprotected. It is the same shape as the
recurring class in inexpressible-defects.md — a mechanism present but not engaged on the path
actually taken — with the extra sting that the disengaged path is the one you ship.

**How to apply:**
- **Any guard whose job is to fail CLOSED gets `if ...: raise`, never a bare `assert`.** Reserve
  bare `assert` for redundant narrowing that something upstream already guarantees (e.g. narrowing
  a value argparse's `required=True` mutex group has already made non-None) — there, stripping it
  costs nothing.
- **Verify, don't infer.** Load the real module under `python3 -O`, drive the invariant false, and
  watch it raise. Reading the source cannot distinguish the two forms' runtime behaviour.
- **Sweep the whole diff, not the site you just wrote.** An AST/grep sweep for `^\s*assert ` across
  changed production files separates real fail-closed boundaries from harmless narrowing; classify
  each rather than reporting a count.
- Pre-existing bare asserts outside your diff are usually narrowing-after-a-dependency-check and
  not worth expanding scope for — but say so explicitly, so a clean-looking sweep isn't read as "I
  checked and there was nothing".

Verified 2026-07-20 (literary-translator 1.11.0): a teammate chose `if ...: raise AssertionError`
deliberately for this reason; confirmed by loading the real module under `-O` with a mutated
constant and watching it still raise. Had it been a bare `assert`, the release's whole
frozen-input fail-closed mechanism would have been absent in optimized execution.

**Diagnostic technique: is a wall of `-O` failures pre-existing, or a regression?** When
`python3 -O -m pytest` on the full suite shows failures unrelated to your own diff (different
files, different subsystem), don't eyeball-guess "probably pre-existing" from the file list alone —
prove it: `git worktree add --detach <tmp-path> origin/main`, run the identical `-O` command there,
and diff the exact sorted `FAILED` test-name sets (not just the counts — a matching count can mask
one newly-broken + one newly-fixed). Verified 2026-07-23 (literary-translator #282/#283, v1.15.2): a
58-test `-O` failure wall across `ledger_confirmation_schema.test.py`,
`mandatory_split_audit_wiring.test.py`, `resume_integrity.test.py`,
`review_prompt_schema_drift.test.py`, `scaffold_setup.test.py`, `senses_fixture_guard.test.py`
turned out to be a BYTE-IDENTICAL failure set on `origin/main` before the branch's changes — a
real, pre-existing bare-assert casualty at scale in this same codebase, not a regression, and safe
to ship past without blocking the PR on it. Filed as a follow-up rather than fixed inline (out of
scope for that PR's diff).
