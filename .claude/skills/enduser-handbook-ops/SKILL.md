---
name: enduser-handbook-ops
description: Working ON the enduser-handbook plugin — modifying its skill, publish-target/adapter-filename resolution, link-emission canons (wikilinks/index rows), grep-based test assertions over its hard-wrapped docs, reference-assets.test.sh portability, citation-audit-lib.mjs or profile-schema-evaluator.mjs, or its capture-safety PII audit.
---

The `enduser-handbook` plugin is a **contract-dense reference-doc skill**: the same rule is stated in several `references/*.md` files plus `SKILL.md`, the docs are hard-wrapped, and the runtime steps (`Step 0b`, `W5`) must agree with the prose. Recurring traps, the review discipline that catches them, and a technique for designing convergence checks on manual-work recipes.

Read the reference file that matches the task:

- **`references/wikilink-resolution-ground-truth.md`** — read when writing, validating or specifying ANY `[[…]]` target: measured Obsidian/Quartz resolution tiers, the vault-root-relative rule, and how to re-derive both.
- **`references/reference-assets-suite-output.md`** — read when capturing or reading a `reference-assets.test.sh` run: `bad()` prints each failing check's NAME to stderr, while stdout carries the ~700 passing `ok` lines AND the `TOTAL:` summary — which is why a bare `tail` (even with `2>&1`) shows the count but loses the failing check's name.
- **`references/skill-parameterization.md`** — read when adding, renaming or removing a profile key: the every-key-needs-a-consumer audit and the two dead-key examples it came from (the general per-project-parameterization mechanism lives in `plugin-repo-mechanics`).
- **`references/manual-work-convergence-facts.md`** — read when designing or reviewing a completion check for a halt-driven manual-work recipe (see §12).

## 1. Publish-target adapter resolution drifts across ~5 surfaces

The skill resolves a publish-target adapter **filename** from `publish.target` by **lowercasing and replacing `_` with `-`**:
- `static_md` → `references/publish-targets/static-md.md`
- `obsidian_vault` → `references/publish-targets/obsidian-vault.md`

That single rule is referenced in spots that **drift independently** — fix one and the others silently keep the old form:
- `SKILL.md` **Step 0b** (resolve + halt) AND **W5** (the *runtime* publish step — this is the one that actually fires at publish time, so a bug here HALTS a real publish, not just planning);
- `references/publish-targets/README.md` (intro paragraph + the numbered selection mechanism);
- `references/glossary-discipline.md` (link-target mention);
- `references/publish-targets/obsidian-vault.md` — an "only adapter that ships" count/claim that also goes stale when a second adapter lands (a `static-md` adapter now ships alongside it).

**The trap:** adding the underscore→hyphen rule to `Step 0b` only, while `W5` + `README` + `glossary-discipline.md` still show the raw `references/publish-targets/<publish.target>.md` form, sends a `static_md` profile to a non-existent `static_md.md` and HALTS at publish time.

**When you touch adapter resolution (or add a new adapter such as Confluence/Docusaurus):**
1. `grep -rn '<publish.target>\.md' plugins/enduser-handbook/skills` and update EVERY hit — the raw templated form must not survive anywhere (it currently returns zero hits; keep it that way).
2. Re-check `obsidian-vault.md`'s "only adapter" claim and the profile-example target-enum comment for a stale count.
3. The regression net is a `hasnt '<publish.target>.md'` assertion on `SKILL.md` and `publish-targets/README.md` (in `plugins/enduser-handbook/tests/reference-assets.test.sh`) — keep it green, and extend it to any new surface.

## 2. The skill docs hard-wrap at ~95 cols — grep needles that span a wrap MISS silently

`SKILL.md` and `references/*.md` prose is **hard-wrapped at ~95 columns**, so a phrase you think of as one string is frequently split across two physical lines. `grep`/`grep -F` is line-based, so any needle that spans a wrap point matches **nothing — silently**. This bites two kinds of work:

**Test assertions.** The `reference-assets.test.sh` helpers (`has`/`hasnt`/`has_ci`) run `grep -qF <needle> <file>`. A multi-word needle copied from the rendered doc gives a false negative the moment the doc wraps it. **Fix:** pick the longest fragment that stays on ONE physical line (e.g. `` 'resolve under `publish.chapters_dir` so the rendered' `` or `'MUST resolve under chapters_dir'`), never a phrase that visually wraps. A gate sentinel that is green *before* the fix (because the needle was wrong) is a no-op — watch it fail red first (see the red-before-green discipline).

**Enumeration sweeps** (grepping for an overstatement/claim across the skill to list every site). ONE needle is never enough on these docs:
- The phrase wraps in **multiple different places**, so one needle finds only the un-wrapped hits.
- A variant may be **capitalized** and sit in a file that also carries a real hit — a case-sensitive sweep misses it, and a careless in-file reword can clobber the benign mention.
- Asset-file banner comments wrap the same claim differently (`.mjs`/`.d.mts`/`.ts` headers) — invisible to a prose needle.

**The wrap trap bites your own VERIFICATION too, not just test needles.** When checking whether a
teammate/reviewer actually wrote a required phrase, a plain `grep` for that phrase silently returns
nothing if it straddles a wrap — and the natural reading is "they didn't write it", i.e. you accuse
correct work of being missing. Verified 2026-07-19 four separate times in one session (twice by a
teammate mid-edit, once by the lead auditing a teammate's output, once as a false-RED in a staged
gate). For a VERIFICATION grep, join lines AND collapse whitespace:
`tr '\n' ' ' < FILE | tr -s '[:space:]' ' ' | grep -o '<phrase>'`. The collapse is not optional —
continuation lines in Markdown lists are indented, so `tr '\n' ' '` alone turns `chapters or\n  glossary`
into THREE spaces and a single-spaced needle still misses. Joining without collapsing reproduces the
very false-negative this is meant to prevent (caught by review 2026-07-20, in this advice itself).
Alternatively match a short fragment guaranteed to sit on one physical line. Reserve single-line needles for gates you
control the wording of; use wrap-tolerant matching whenever you are reading someone else's prose.

**Sweep discipline that holds:** run **several** short, wrap-surviving needles case-insensitively over the WHOLE repo, e.g.
```
grep -rIn -i "other engines"
grep -rIn "Fork for other"
grep -rIn -i "fork the asset"
```
then get `grep -rIl` (distinct files) + a count, **classify each hit** (real claim vs benign mention) before touching it, and **eyeball the file list and count against the source** — do NOT grep the whole human phrase, and do NOT `head`-truncate a sweep you are going to count. Replacement wording can differ **by file group** (driver assets vs engine-neutral libs), so an "identical wording" acceptance criterion is per-group, not global.

## 3. Hardening the capture-safety surface-audit against PII leaks

`surface-audit.playwright.ts` enumerates the live DOM **broadly on purpose** ("never filter; the human classify pass decides"), then console-logs and commits a control inventory. That over-capture means any logged field of a non-control element can carry PII.

**The trap — negative per-field suppression RECEDES and never terminates.** "Suppress `textContent` for value-bearing controls" → next round finds `textContent` of textarea/select/contenteditable → then `aria-label`/`title` of data regions → then aggregate text of genuine links wrapping data cells → … Each fix exposes the adjacent field/shape; an adversarial reviewer ALWAYS finds the next one, because the over-capture design intentionally admits PII.

**The convergence move — flip to a POSITIVE whitelist.** Stop enumerating what to suppress; define the one shape whose text IS a clean label and emit raw text only for it. The rule lives in `assets/lib/control-inventory.mjs` (`extractRecord`): log raw text ONLY for a **genuine LEAF control** =
- `isGenuineControl(self)` — button/input/select/textarea/summary, `a[href]`, `[role=button|menuitem|tab|switch|link]`, contenteditable; judged by **tag/role/href, NOT identity attrs**; AND
- **no genuine-control descendant** — a `querySelector` probe with a NARROW `GENUINE_CONTROL_SELECTOR` that EXCLUDES `[aria-label]`/`[data-testid]`/`.badge`/`[role=status]`, so a non-control instrumented label-span doesn't count (a leaf `<button><span data-testid>Save</span></button>` keeps its label); AND
- not value-bearing.

Everything else (data regions, rows, badges, genuine-by-role containers wrapping a child control) → text suppressed; identity from structural attrs only.

**The irreducible residual — make it a user-ratified DOCUMENTED boundary, do NOT chase it.** Some fields are BOTH needed identity AND a PII carrier and can't be separated structurally: an icon-only `<span aria-label="Delete">` is indistinguishable from a labelled data region `<div aria-label="Jane jane@…">`, so `aria-label`/`title` can't be stripped without dropping real icon-only controls; a genuine control's own visible label can be a clickable customer name. These are documented in `references/completeness-gate.md` ("PII boundary of the mechanical pass": run on **seeded/non-PII data** + **human scrub before commit**) and the `surface-audit.playwright.ts` **`PII BOUNDARY`** banner — deliberately **NOT redacted**, because a pattern mask gives **false assurance** (a bare name defeats it). The user explicitly chose this documented boundary over a "structural-identity-only" mode (which would drop icon-only labels); the declined option is filed as deferred work (`docs/Backlog.md` → a `surface-audit-structural-mode` issue). **Do not re-litigate the boundary as an unfixed bug in a future round.**

**Reusable process:** when adversarial review keeps finding the SAME class on an over-capturing tool, that is the signal to (a) flip from negative per-field suppression to a positive whitelist, and (b) surface the irreducible residual to the user as a scope/boundary decision rather than looping.

## 4. "Dependency-free" is a narrower claim than it sounds — check for a GATED external-tool call before inventing a lighter approximation

A plan to add a new validation/gate to `tests/reference-assets.test.sh` (or `.test.mjs`) can wrongly
conclude "no real X available, so approximate it with a grep-based/structural stand-in" from checking
only `package.json`/`node_modules` (absent — the plugin genuinely has none). That check misses a
**gated external-interpreter call already present in the test suite for a similar purpose**:
`tests/profile-version.differential.test.mjs` shells out to Ruby's stdlib `Psych` YAML parser behind a
`ruby -ryaml` availability guard, to differentially test the plugin's own hand-rolled YAML line-scanner
against a real parser. A later plan that needed to validate `handbook.profile.example.yml` against
`assets/profile.schema.json` almost shipped a flat/approximate "does this key name appear somewhere"
check instead of reusing that same gated-Ruby pattern — codex plan review caught it as both false-green
(missed an invalid `enum`/`const` value, a moved key, a removed required field) and false-red (rejected a
valid instance correctly omitting the optional `style_guide.inline` object). **Before designing a new
lighter-weight check to avoid "adding a dependency," grep the WHOLE test suite for existing gated
external-tool invocations (`ruby -r`, `command -v`, `npx --no-install`, etc.), not just for a
`package.json`** — a real, already-accepted mechanism for the same class of problem may already exist one
file over.

## 5. A whole-file `has` check proves EXISTENCE, not LOCATION — survives a relocate-into-fence mutation

`reference-assets.test.sh` has two needle-assertion helpers: `has` (whole-file — the phrase exists SOMEWHERE) and `has_in_section` (fence-aware, bound to a specific `##`/`###` heading). When a new test hardens a claim that's supposed to live at ONE normative site in a multi-section doc (the exact shape of #251/#252-style needle-pinning work), `has` is not sufficient even when the needle string is verified unique — a mutation that deletes the claim from its real site and pastes the identical text into a fenced code block under an UNRELATED heading still satisfies a plain `has`, because `has` never checks which section (or whether fenced-vs-live) the match sits in.

**This survived TWO codex-rescue rounds before the `lazy-ants-reviewer` bot caught it** (2026-07-23, PR #316): both rounds were explicitly asked to verify needle uniqueness and load-bearingness against wording mutations, both confirmed uniqueness correctly, and neither one independently thought to test a *relocation* mutation (move the exact text to the wrong section) — uniqueness and section-binding are orthogonal properties, and a review checklist that only asks about one silently assumes the other. **When writing or reviewing a new needle-pinning assertion for a claim that has ONE correct normative location, default to `has_in_section`** — a doc having no other *legitimate* section for the phrase does NOT make plain `has` sufficient, since an illegitimate fenced copy pasted anywhere still satisfies a whole-file grep; `has_in_section`'s heading+fence binding is what actually rules that out, so plain `has` is essentially never the right choice for a single-normative-site claim. When reviewing (self or via codex), add "relocate the needle into a fenced block under a different heading, confirm the check now FAILS (goes red)" as its own mandatory probe alongside the wording-mutation probes — it is not implied by them, and the expected outcome is the opposite of the wording-mutation probes' baseline (there, an unrelated section passing is fine; here, a relocated needle passing IS the bug).

**The direction flips for `hasnt` — scoping a NEGATIVE assertion WEAKENS it.** "More specific =
stronger" is the default intuition for assertions and it is exactly backwards for absence claims:
scoping a POSITIVE claim TIGHTENS it (the phrase must now hold under the right heading, not merely
somewhere in the file), while scoping a NEGATIVE claim LOOSENS it (the forbidden text is now
permitted everywhere except that one section). The same helper upgrade therefore hardens one family
and loosens the other. When the section-bounded engine landed, the 24 existing whole-file `hasnt`
assertions were deliberately left untouched: "section-scoped absence is a strictly weaker claim than
whole-file absence, so converting them would have loosened those gates while appearing to harden
them." The failure mode is the silent kind — the diff reads as a uniform hardening pass, the
assertion COUNT is unchanged, the suite stays green, and 24 gates quietly stop covering the rest of
the file. Adopt `hasnt_in_section` one genuine caller at a time; never sweep the existing whole-file
`hasnt` calls into it.

**Corollary: a helper whose call sites are ALL positive has an unguarded boundary.** Measured when
`has_in_section`'s same-or-shallower heading rule was first guarded: every real call site was a
positive assertion and all four of its self-tests placed the needle INSIDE the section under test,
so DELETING the boundary check only WIDENED the scan and every assertion stayed green — confirmed
by removing it, after which a W6-only phrase was accepted under the W1 heading. That is
load-bearing because the W1 and W6 pins deliberately share one needle (`` MUST run
`validateGroups(entries)` ``) and their independence comes ENTIRELY from section termination, so a
regressed boundary lets W6's copy satisfy the W1 pin with no other gate noticing. The
positive-reformulation trick that covers the other self-tests cannot rescue this one: "removing the
boundary check ONLY adds false positives, never removes a true one, so there is no positive fact
whose presence depends on the boundary holding." That is `hasnt_in_section`'s one legitimate caller
and the shape to look for before adding a second — the reasoning is spelled out at its definition
and its self-test block in `reference-assets.test.sh`.

## 6. A link-emission canon change has multiple write sites — including the manual-recipe prose, not just the machine canon

1.8.0, #294, ped-ant P1. The vault-rel wikilink fix touched the adapter + `revalidation.md`'s "Write-time canon", but `revalidation.md`'s manual group-migration recipe (step 4) still told the operator to WRITE the old bare `[[<slug>]]` form under `wikilinks:true` — a 2nd emission site recreating #294. BOTH codex plan review AND codex working-tree review missed it (a single-tree pass can't see a prose recipe as an emission site); the ped-ant bot caught it.

Two durable moves: (1) **the resolution-only convergence gate is BLIND to a stale canonical spelling** — a bare slug still RESOLVES when the basename is unique, so a wrong-but-resolvable recipe passes CI silently; "does it resolve" cannot catch "is it the canonical spelling". (2) **Class-sweep = grep every `[[` in the doc and classify each hit as a WRITE-instruction vs a recognition/gone-check** — only a WRITE using the stale form is a defect; recognition mentions ("may still carry the old bare form") are correct and expected.

When changing any emission canon here, enumerate ALL write sites (adapter doc + `revalidation.md` write-canon + migration recipe + inbound-link fixers) before declaring done. Sibling of the `_`→`-` adapter-drift trap in §1.

## 7. A gate/backstop "catches X" claim must be traced against its actual reject predicate

1.9.2, #311, ped-ant P2, caught TWICE. The #311 docs claimed a divergent hand-authored path-mode index row is "caught by the link-integrity resolution gate"; codex-pr2 passed it CLEAN, but the ped-ant traced the real workflow: **append-and-retain** — `locateChapterLine` returns `present:false` → static-md step-0 APPENDS the canonical `.md` row → item 5 passes on that appended row while the divergent row is RETAINED; item 2 checks the chapter's OWN relative links, not an index-wide broken-link sweep. "A gate exists nearby" ≠ "this gate rejects THIS state" — read the gate's pass/reject predicate against the concrete failure sequence.

AND after fixing the code comment + JSDoc + `static-md.md` note, the SAME false claim survived in the release-facing **1.9.2 CHANGELOG entry** (and the PR body) — the multi-write-site trap (§6 above) extends to RELEASE COPY; class-sweep the whole diff (`grep 'caught by' / 'backstop'`) when correcting a semantic claim, not just the code/doc sites. Both catches are fresh evidence for the "one clean codex pass ≠ ped-ant-clean on this plugin" lesson below.

## 8. Chapter-paths delimiter tests must prove the RULE, not the EOF-swallow

1.7.1, #254. `findFenceClose`/`findCodeSpanClose` (in `chapter-paths.mjs`) return the text LENGTH (NOT `-1`) when no closer exists — an unterminated fence/span swallows to EOF (documented in their own comments). So a test meant to pin the inline-code exact-length rule (`runLen === openLen`) with a fixture like `` `x`` and [[link]] here `` reaches `false` via the EOF-swallow, NOT the exact-length rule — it enshrines a pre-existing quirk, and a codex code-review (correctly) flags it as pinning a false-negative.

Fix: give the span a GENUINE later exact-length closer — `` `x`` [[link]]` end `` — so `[[link]]` is inert because a real 1-backtick run closes the span, and `false` rests on the `===` rule itself (verify empirically against the real module + confirm it still flips under a `>=` mutant).

General principle for this file's threshold tests (there's an ongoing hardening cadence — #294–#303 and beyond): the fixture must fail for the RIGHT reason — isolate the target property, not an incidental adjacent behavior that yields the same value.

## 9. The ped-ant bot catches cross-env portability that codex + local runs miss

1.7.0, PR #293. After codex reached CLEAN and the local suite passed 466/466 in both shells, the bot flagged a P1: `reference-assets.test.sh`'s new `category_files` used **`sort -z`, a GNU extension older BSD/macOS `sort` vintages lack**.

Two lessons: (1) a local "both shells" pass was misleading — `bash`/`/bin/bash` resolve `sort`/`find` via **PATH to Homebrew GNU tools**; to test portability, force stock tools: `env -i PATH="/usr/bin:/bin" /bin/bash <suite>`. (2) The bot's SPECIFIC claim was wrong for CURRENT macOS (Darwin 25.5 `/usr/bin/sort` = Apple `2.3-Apple(199)` DOES support `-z`; BSD `find` has `-maxdepth`/`-mindepth`) — but it correctly surfaced a real DISTRIBUTION fragility, so verify the bot's claim on the target env AND fix the fragility regardless.

Fix here was a simplification: `sort -z` was never load-bearing (count/presence gate, order-irrelevant), so drop the sort stage entirely. Post-codex-clean is NOT ship-ready on this repo until the ped-ant round passes — budget it (see "Review discipline" below).

## 10. A "must-now-fail" self-test needs a raw exit-code check, not a bare assertion call

1.8.4, #302. `reference-assets.test.sh`'s whole suite exits on `[ "$FAIL" -eq 0 ]`; `ok()`/`bad()` both return `printf`'s exit status, not their own identity. If the fix under test is "an already-broken case must now report `bad`", a plain `hasnt_in_section "..." ...` call would correctly increment the real `FAIL` counter and break the build the moment the fix actually works — you cannot regression-lock a "should fail" case as a normal assertion in this style.

Two working shapes: (1) call the underlying engine directly and read its raw exit code (`if _section_contains ...; then rc=0; else rc=$?; fi`, no `!`/pipeline so `$?` reads cleanly under `pipefail`); (2) isolate the wrapper call inside a **subshell with its own reset `FAIL=0`** (`( FAIL=0; hasnt_in_section ... >/dev/null 2>&1; [ "$FAIL" -eq 1 ] )`) — `bad()`'s `FAIL=$((FAIL+1))` mutation doesn't survive the subshell boundary, so it never touches the real tally, and the subshell's own exit code tells you whether `bad` fired.

Reach for this pattern whenever hardening a "must now correctly fail" case in this file, not just a "still correctly passes" one.

## 11. Fixing ReDoS backtracking does not make a matcher linear — re-benchmark the outer shape at scale

1.9.3, #258, ped-ant caught it in two rounds. `citation-audit-lib.mjs`'s original span regex had two independent bugs stacked on the same line: an adjacent-optional-`\s*` separator was genuinely exponential (fixed first, confirmed by a security-review pass AND codex's own code review independently), but even after that fix, the OUTER shape — one monolithic "one-or-more-quotes-then-direction" regex retried via `matchAll` at every quote-start position — was still quadratic on an undirected run (each of the N quote-starts rescans up to the remaining N-i quotes before failing). The ped-ant bot caught this as a SEPARATE finding on the very next review round, after the exponential fix had already shipped and looked done.

The actual fix required abandoning the single-regex-retried-everywhere approach for a genuine single forward pass: find every quoted title once (cheap, no repeated-group shape), walk that list once growing maximal separator-only chains, check for a trailing direction word only ONCE per chain's endpoint (provably lossless here — an interior gap containing "above"/"below" text would have stopped chain growth there, so no valid boundary can hide inside an already-grown chain).

General lesson: after fixing a catastrophic-backtracking finding, **re-benchmark at scale** (not just re-run the original repro at the size that made it obviously hang) — "no longer exponential" and "linear" are different claims, and a reviewer that already found one perf bug in a matcher is likely to look harder at the fix, not less.

## 12. Designing the completion/convergence check for a halt-driven manual-work recipe

When automation for a step is descoped in favor of "halt + manual recipe + re-run", the **completion VERIFICATION of that manual work becomes the new complexity sink** — it concentrates almost every subsequent review finding. This needs its own fact-soundness taxonomy, halt-as-record discipline, and loop-exit rule; see `references/manual-work-convergence-facts.md` before designing or reviewing any such check (originated in the 1.5.0 group-axis release, #19, codex rounds 6–15).

## 13. `key in obj` walks the prototype chain — and its two failure directions hide each other

`in` is membership *including inherited members*, so a required or declared key named after an `Object.prototype` member (`toString`, `constructor`, `valueOf`, `hasOwnProperty`) is satisfied by that inherited member **even on a genuinely empty instance**. In `profile-schema-evaluator.mjs`'s `validate()` → `walk()` this bit at two independent sites, and they fail in OPPOSITE directions: the **required-key check** silently PASSES (false green — the key is "present" because `Object.prototype` has it), while the **properties-descend check** spuriously REJECTS (it descends into the inherited member and validates `Object.prototype.toString`, a FUNCTION, against a schema for a property the instance never set). Use `Object.hasOwn(value, key)` for membership on any data-shaped object. Fixed at three sites — both evaluator sites plus the `#296-part-1` required/properties cross-check in `reference-assets.test.sh` (`2cba9bc`, issue #296).

**The scope is any plain object whose keys come from OUTSIDE the code — not just the evaluator.** Wherever keys are filenames, slugs, user config keys or any other external namespace, a plain `{}` is wrong in *two* directions, and the second is not a membership bug at all — it is a WRITE that never lands. Assigning to the key `__proto__` never creates an own property, so `JSON.stringify` emits `{}` either way, but the two sub-cases differ and both matter (measured, Node 22): a **primitive** value (`'v'`, `42`) is silently discarded and the prototype is untouched; an **object or `null`** value silently REPLACES the object's prototype, which is worse than losing the value. Nothing throws in either case, and no membership fix can see it. A screenshot may legally be named `toString.png` or `__proto__`; both were live against 1.12.0's provenance hash maps.

**Pick a container, then use ITS membership API — the two are one decision, not two.** A `Map` is queried with `map.has(key)`; `Object.hasOwn(map, key)` inspects properties of the Map *object* and returns `false` for every entry it holds, so pairing "use a Map" with "use `Object.hasOwn`" makes every lookup miss. A null-prototype object (`Object.create(null)`) removes both directions at once — there are no inherited members, so even `key in dict` is own-only, and `__proto__` becomes an ordinary own property with no setter to intercept it — but `Object.hasOwn` is still the clearer spelling there because it survives someone later swapping the container back to `{}`. What is never right is a plain `{}` with `in`.

**The reusable trap is in the TEST, not the fix: a probe that exercises both sites at once passes for the wrong reason.** A schema carrying BOTH `required: ['toString']` AND a matching `properties.toString` entry still produced a nonzero error count under the pre-fix code — but that error came from the properties-descend site's own leak, which **masked** the required-check site being separately and silently satisfied by inheritance. A single combined probe therefore goes green after fixing only one of the two sites. So the regression probes deliberately isolate each site (no matching `properties` entry in the required-check schema), and each was verified by hand against a scratch revert of the fix before being wired in. Generalizes: **when one root cause has N sites, a probe spanning several of them can be satisfied by any one — isolate one site per probe, and prove each RED separately** (same shape as the "revert N symmetric sites ONE AT A TIME" rule).

Found by the `lazy-ants-reviewer` bot on PR #318 *after* codex rounds came back clean — the second of the two P1s that bot caught in a single session (the other is §5), and a further data point for the review-discipline note below.

## Review discipline for this contract-dense plugin

- **The `lazy-ants-reviewer` (ped-ant) GitHub bot is a real cross-file/runtime-contract net, not a rubber stamp.** It has caught runtime-path contract bugs (e.g. the `W5` publish miss) that BOTH a multi-round codex plan review AND a codex working-tree review missed, because a single-tree review can't see cross-file/runtime inconsistency. After codex says CLEAN, still expect the bot to find them. Workflow: push, then reply to and resolve its thread via GraphQL (`addPullRequestReviewThreadReply` then `resolveReviewThread`), and let it re-review — it posts **"Result: no findings"** when clean; its status check stays **UNSTABLE regardless**, which is normal for this repo.
- **One "no findings" — even from two independent reviewers — is NOT convergence on a contract-dense reference doc.** After both the ped-ant bot said "no findings" AND an earlier codex pass was clean, a FRESH exhaustive codex pass on the *same committed tree* has surfaced several more real contract bugs (e.g. a glossary relative-link that double-prefixed `../` onto an already-relative `<glossary-rel>`, over-climbing one segment and contradicting the file's own worked example). Keep running fresh adversarial passes against the CURRENT tree until one comes back clean **before merging**. The same held for a same-file gap, not just cross-file: see #5 above (`has` vs `has_in_section`), caught by the bot after two clean codex rounds.
- **`reference-assets.test.sh`'s absolute PASS/FAIL total is environment-dependent — state deltas in CHANGELOG entries, not absolute counts.** The suite's optional `esbuild`-gated TypeScript check (the `command -v esbuild` / `npx --no-install esbuild --version` block — grep for it, the line number drifts) adds 0 or 1 assertion depending on whether a LOCAL `esbuild` binary or a cached `npx --no-install esbuild` resolves — this differs between a normal dev shell and the bot's/codex's sandboxed environment, producing a consistent ±1 total at every measurement point while the delta between before/after stays identical. Verified 2026-07-23: a CHANGELOG entry stating "486 → 490 assertions" was accurate locally but the bot's sandbox measured "485 → 489" and flagged the mismatch as a documentation bug. Write release notes as `+N assertions` (the portable fact), not `X → Y` absolute totals (environment-fragile) — same principle the plan's own "baseline assertion count is not a stable invariant" note already established for planning, now confirmed to bite release-note prose too.
