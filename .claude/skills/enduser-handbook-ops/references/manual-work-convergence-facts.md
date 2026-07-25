# Convergence facts for manual-work recipes

Origin: enduser-handbook #19 (the 1.5.0 group-axis release), codex rounds 6–15.

When automation is descoped and replaced by "halt + manual recipe + re-run", the **completion
VERIFICATION of the manual work becomes the new complexity sink** — rounds 9–14 of that release
concentrated EVERY finding on it. Budget for that in the plan, and design the checks with this
taxonomy from the start.

## Fact-soundness taxonomy (what is mechanically provable)

- **Disk facts** (file exists at derived path / old path gone) — sound; always machine-checked.
- **Path-link index facts** (a line matches `relative(dirname(index_file), chapter_file)`) —
  sound, because old and new targets differ TEXTUALLY. The coordinate system must be the INDEX's
  directory, never the content root (R7-F2).
- **Same-string facts** (bare `[[slug]]` wikilinks — old and new lines are identical text) — need
  positional/container proofs; degenerate when old/new container titles coincide → the fact
  becomes "exactly ONE match under the shared container" (R14-F3). Removed entries additionally
  need a resolution-INDEPENDENT forbidden-target scan (`[[removed-slug]]` anywhere) — a resolving
  wikilink can silently RETARGET to a same-basename foreign note and false-pass any resolution
  gate (R14-F2).
- **Arbitrary-user-artifact facts** (a project's capture spec — free-form TS) — NOT mechanically
  provable. Six consecutive rounds (R9-F6→R14-F1) produced counterexamples to every classifier
  (literal sinks → dynamic reconstruction → decoy literals → stale helper arguments → stale
  manifest copies). Terminal form: **red-flag + confirmation** — a cheap check that soundly proves
  NON-completion (old literal present ⇒ unmet), plus explicit user confirmation for everything
  else; NO auto-MET branch ever. Attestation is proportionate: the operation is manual by ratified
  decision anyway.

## Structural rules that survived 7 rounds of attack

- **Halt-is-the-record**: with no durable state, the halt text must embed the full structured
  per-entry facts (old→new paths, removal lines) so a context-free later run reconstructs every
  check (R10-F5). A downstream-verifier failure halt must RE-EMBED the whole record — a fact can
  regress while the user fixes something else; retry order = facts → handbook-wide verification →
  touched gates (R13-F3).
- **Convergence per delta-kind**: path changes get path facts; title-only changes get container
  facts (or confirmation on unparseable forms — raw string presence is gameable via substring
  containment, R10-F3); mode flips get NO retroactive facts at all (below). Never one fact list for
  all kinds — that's where "old path gone" broke title-only changes (self-caught during the
  release).
- **Entry domains**: retained (old∩new) / new-only (normal authoring, NEVER migration) / old-only
  (removal facts, `newEntry=null`). Without them, a flip via pure addition demands facts for an
  unauthored chapter = deadlock (R9-F2).
- **Delta lifetime**: deltas exist where detected (the manifest-review step, pre-edit manifest in
  context); consumed only when facts AND downstream verification pass the same run; fresh-run
  limitation stated + a non-blocking stale-artifact advisory (set-diff vs current manifest), never
  a halt on foreign files.
- **Write-time canon vs resolution gates** (R9-F3): formulas govern what the skill WRITES; gates
  verify semantic RESOLUTION, never spelling equality — so mode flips impose no retroactive
  rewrites (untouched links keep resolving) and stop being halts entirely. Pick the one spelling
  that is VALID IN EVERY MODE (the full-target form) as the manual-rewrite canon — a
  destination-mode-dependent canon deadlocks degenerate layouts (R10-F1). Chapter-scoped gates
  never revisit UNTOUCHED chapters → moves need one handbook-wide resolution scan as post-migration
  verification (R11-F2; codex-vetted as verification, not re-opened automation).

## Loop management

- N consecutive rounds of counterexamples against the SAME fact = the paradigm tell. The
  resolution is not always descope — for a VERIFICATION mechanism it's the **attestation
  downgrade** (red-flag + confirm). That's a refinement WITHIN the ratified descope: decide it
  directly and flag for veto; don't spend an AskUserQuestion (contrast the scope-tier escalations
  that belong in the review-loop-discipline skill).
- **Plan-surgery hygiene**: replacing a section wholesale via Edit left the OLD sibling block in
  place, and a dangling-reference keyword grep false-passed (wording didn't match the grep terms) —
  codex caught the duplicate contract (R9-F7). After structural surgery, RE-READ the region and
  count section headings (`grep -c '^\*\*Halt texts'`); a keyword grep for removed machinery is not
  a completeness proof (the same grep-gate-verification rule applies to plan docs, not just code).

## Related

- The `review-loop-discipline` skill covers the loop exit/fence rule this refinement lives inside.
- The `codex-runtime-driving` skill covers driving the many codex rounds this took.
- This technique shipped as part of the enduser-handbook 1.5.0 group-axis release (#19).
