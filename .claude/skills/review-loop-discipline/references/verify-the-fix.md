# Verify the fix before saying done

- [Cover the DOMAIN, not the ticket's example](#cover-the-domain-not-the-tickets-example)
- [Widening a matcher needs its own over-correction check](#widening-a-matcher)
- [Any reword/rename: verify the REPLACEMENT is accurate](#any-rewordrename)
- [A rebuild can regress what the original got right](#a-rebuild-can-regress)
- [State what your proof GUARANTEES — check the axis](#state-what-your-proof-guarantees)
- [Source-completeness before trusting a rebuild](#source-completeness)
- [Cross-check your own artifacts](#cross-check-your-own-artifacts)
- [Reproduce the gate's clean env](#reproduce-the-gates-clean-env)
- [A staged RED gate must be SATISFIABLE by its owner](#a-staged-red-gate-must-be-satisfiable)

## Cover the DOMAIN, not the ticket's example

A ticket's repro is a MINIMAL example, usually the simplest instance of the class. A fix plus a regression test that both key off that one example give false confidence — the suite is green because it only tests the example, not the domain, and the gap survives precisely because nothing exercises the domain's real inputs. Concrete: a name-fusion fix that broke on the script's `TERMINATORS` set passed the ticket's `"Fiona. George"` repro and a 1126-test suite, but `TERMINATORS` omitted the em-dash `—` — the DOMINANT dialogue delimiter in French/Russian/Spanish literary prose, i.e. the tool's core domain — so `"Fiona. — George"` still fused.

When fixing a bug in a domain-specific processor (parser, tokenizer, extractor, formatter), before calling it done: enumerate the DOMINANT real-domain inputs of the bug's class and test the fix against EACH, not just the ticket's example. An adversarial review briefed to check the fix AGAINST THE DOMAIN (not just "does it pass") is what surfaces the gap even when the suite is green.

## Widening a matcher

Not every domain char/case is safe to add — a widened fix needs its OWN over-correction check (a second adversarial pass hunting regression). And that check needs REAL domain data: widening `ELISION_RE` to split capitalized `L'`/`D'` passes clean PLAN review yet breaks real fixed compounds (`D'Artagnan`, `L'Aquila`, `D'Annunzio`, `L'Oréal`) — only code-level review running the actual widened regex against real proper nouns catches it. A clean plan-review pass is NOT evidence against over-correction; if the domain has known fixed-form exceptions, have an adversarial code reviewer test the fix against them explicitly.

## Any reword/rename

Removing a wrong term X can INTRODUCE a new inaccuracy. A blanket replacement can be token-correct yet semantically wrong (universalizing a built-in's internals onto every adapter). Verify the REPLACEMENT is accurate, not merely that X is gone — a regression test that only checks X's ABSENCE false-greens the bad replacement, so add field-specific POSITIVE assertions (the field must NAME the right owning component). Then sweep every restatement (CHANGELOG, the new test's docstring) and fix the whole class.

## A rebuild can regress

When you REPLACE a rejected artifact with a "more rigorous" rebuilt pipeline, it can silently REGRESS a dimension the original got right. A fresh independent re-derivation left 126 of 240 entities unresolved (33% coverage) where the rejected original's reused-data path had 97.5%. **When you replace a rejected deliverable, DIFF the new output against the old on EVERY dimension** — a rebuild that fixes complaint A can silently regress dimension B the old one handled. "More rigorous" is not "better" until measured. Prefer REUSING an existing artifact's already-correct data over a from-scratch re-derivation unless you have MEASURED that the re-derivation covers at least as much.

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
