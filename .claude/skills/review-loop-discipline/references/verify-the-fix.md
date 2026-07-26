# Verify the fix before saying done

- [Cover the DOMAIN, not the ticket's example](#cover-the-domain-not-the-tickets-example)
- [Widening a matcher needs its own over-correction check](#widening-a-matcher)
- ["Looks redundant" is a reading, not a measurement — full-dataset diff proves it](#looks-redundant-is-a-reading-not-a-measurement)
- [Any reword/rename: verify the REPLACEMENT is accurate](#any-rewordrename)
- [A rebuild can regress what the original got right](#a-rebuild-can-regress)
- [A measured threshold and its gate must share ONE predicate, as code](#a-measured-threshold-and-its-gate-must-share-one-predicate-as-code)
- [State what your proof GUARANTEES — check the axis](#state-what-your-proof-guarantees)
- [Source-completeness before trusting a rebuild](#source-completeness)
- [Cross-check your own artifacts](#cross-check-your-own-artifacts)
- [Reproduce the gate's clean env](#reproduce-the-gates-clean-env)
- [A staged RED gate must be SATISFIABLE by its owner](#a-staged-red-gate-must-be-satisfiable)
- [A coverage claim needs the same evidence as the thing it covers](#a-coverage-claim-needs-the-same-evidence)
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

## State what your proof guarantees

A completeness "proof" can validate the WRONG axis. An anchor gate that proves *every emitted backlink resolves to a real heading* (no DANGLING links) does NOT prove *every mention is found* (no MISSING mentions) — these are orthogonal. Before claiming "complete", state in ONE sentence exactly what your check GUARANTEES, and confirm it is the SAME axis as the requirement.

## Source-completeness

A linking-failure bug can MASK a source-completeness gap. Diagnose at the source, not just the join: a rebuild from an aggregate can fix the reported linking failure while REGRESSING whole partitions that silently produced zero rows upstream. The tell is a per-partition row count of the source (e.g. chunk-02=42, chunk-03=42, chunk-01/05/06=**0**) — not a formatting artifact. Always check source COMPLETENESS (every expected partition non-empty) before trusting a rebuild, even when the reported bug is only about linking; backfill empty partitions from an alternate per-segment source, gated on declared-partition == resolved-partition and every key a verbatim heading.

## Cross-check your own artifacts

The tell for a regression is often an internal contradiction between your OWN artifacts: a registry storing `source_mention_count: 4` for the very entity the index renders as `Mentions (0 ch): —`. Cross-check derived artifacts against each other (registry-count vs index-count) before presenting — a self-contradiction is a free, high-signal defect detector.

## Reproduce the gate's clean env

A green LOCAL test run is not proof of shippability — a CI/review gate under declared-deps-only + a different OS surfaces env-masked failures your machine hides. Two masking causes: an UNDECLARED ambient dep (a package you happen to have installed that makes a check effective locally but a silent no-op under declared deps — e.g. `rfc3987` making jsonschema's `format:"uri"` bite), and an OS-specific tmp path (a guard rejecting a `durable_root` under a `tmp`/`temp`/`scratchpad` component fires under Linux `/tmp` but not macOS `/var/folders/…`). Reproduce the gate's env on macOS BEFORE claiming shippable:

- Fresh venv, declared reqs only: `python3 -m venv V && V/bin/pip install -r requirements.txt` — a venv excludes user-site, stripping ambient extras. Confirm the suspected extra is ABSENT: `V/bin/python -c 'import rfc3987'` → ModuleNotFoundError.
- Force Linux-like tmp: `TMPDIR=/tmp V/bin/python -m pytest` — on macOS `/tmp` → `/private/tmp`, which DOES contain a `tmp` component, so tmp/path-sensitive guards fire exactly as on CI.
- Run the FULL suite that way; green ⇒ shippable. A single knob (`TMPDIR=/tmp`) can reproduce the bulk of the failures instantly; the clean venv reproduces the dep-masked remainder.

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
