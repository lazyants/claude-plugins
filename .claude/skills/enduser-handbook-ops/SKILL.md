---
name: enduser-handbook-ops
description: Working ON the enduser-handbook plugin — use when modifying its skill, changing publish-target/adapter-filename resolution, writing or debugging grep-based test assertions or enumeration sweeps over its hard-wrapped SKILL.md/references docs, or hardening/auditing its capture-safety surface-audit against PII leaks; covers the multi-surface adapter-resolution drift, the ~95-col hard-wrap grep trap, the positive-whitelist PII-convergence move, and the ped-ant/codex review discipline for this contract-dense reference doc.
---

The `enduser-handbook` plugin is a **contract-dense reference-doc skill**: the same rule is stated in several `references/*.md` files plus `SKILL.md`, the docs are hard-wrapped, and the runtime steps (`Step 0b`, `W5`) must agree with the prose. Three recurring traps and the review discipline that catches them.

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

## Review discipline for this contract-dense plugin

- **The `lazy-ants-reviewer` (ped-ant) GitHub bot is a real cross-file/runtime-contract net, not a rubber stamp.** It has caught runtime-path contract bugs (e.g. the `W5` publish miss) that BOTH a multi-round codex plan review AND a codex working-tree review missed, because a single-tree review can't see cross-file/runtime inconsistency. After codex says CLEAN, still expect the bot to find them. Workflow: push, then reply to and resolve its thread via GraphQL (`addPullRequestReviewThreadReply` then `resolveReviewThread`), and let it re-review — it posts **"Result: no findings"** when clean; its status check stays **UNSTABLE regardless**, which is normal for this repo.
- **One "no findings" — even from two independent reviewers — is NOT convergence on a contract-dense reference doc.** After both the ped-ant bot said "no findings" AND an earlier codex pass was clean, a FRESH exhaustive codex pass on the *same committed tree* has surfaced several more real contract bugs (e.g. a glossary relative-link that double-prefixed `../` onto an already-relative `<glossary-rel>`, over-climbing one segment and contradicting the file's own worked example). Keep running fresh adversarial passes against the CURRENT tree until one comes back clean **before merging**. The same held for a same-file gap, not just cross-file: see #5 above (`has` vs `has_in_section`), caught by the bot after two clean codex rounds.
- **`reference-assets.test.sh`'s absolute PASS/FAIL total is environment-dependent — state deltas in CHANGELOG entries, not absolute counts.** The suite's optional `esbuild`-gated TypeScript check (the `command -v esbuild` / `npx --no-install esbuild --version` block — grep for it, the line number drifts) adds 0 or 1 assertion depending on whether a LOCAL `esbuild` binary or a cached `npx --no-install esbuild` resolves — this differs between a normal dev shell and the bot's/codex's sandboxed environment, producing a consistent ±1 total at every measurement point while the delta between before/after stays identical. Verified 2026-07-23: a CHANGELOG entry stating "486 → 490 assertions" was accurate locally but the bot's sandbox measured "485 → 489" and flagged the mismatch as a documentation bug. Write release notes as `+N assertions` (the portable fact), not `X → Y` absolute totals (environment-fragile) — same principle the plan's own "baseline assertion count is not a stable invariant" note already established for planning, now confirmed to bite release-note prose too.
