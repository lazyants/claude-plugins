# Revalidation — auditing an already-merged chapter

The author workflow (W1–W5) builds a chapter from scratch and halts for manifest
review before any capture. That path is **author-only**. It does not cover the
recurring task of checking whether a chapter that already shipped still matches
the running app after the feature moved underneath it. Revalidation is that
missing audit path.

You revalidate when a merged chapter may have gone stale — a dependency bump, a
copy revision, a layout tweak, a periodic pre-publish sweep — and you need to
know what, if anything, to refresh, without re-running the full author halt for
scope the user already accepted.

## The flow

1. **Re-derive the feature surface from the running UI.** Same as W1: drive the
   live surface and enumerate it today, per `running-ui-source.md`. The running
   UI is ground truth; the existing chapter and its screenshots are dated
   artifacts, not authority.
2. **Diff against the existing chapter and its manifest entry.** Compare the
   re-derived surface — routes, roles, steps, glossary terms, side-effect
   classes, every interactive trigger — against what the merged chapter and its
   capture-manifest entry currently say.
3. **Classify each delta into exactly one of three classes:**
   - **no-op** — the re-derived surface is identical to the accepted chapter +
     manifest. Nothing to do.
   - **accepted-diff** — an observed UI / prose / screenshot **refresh within
     the already-accepted manifest scope**: NO changed manifest field, NO
     added or removed step, NO route / role / glossary / side-effect change, and
     NO newly discovered interactive trigger. The control set and meaning are
     unchanged; only the rendered artifact moved (a restyled button, a reworded
     label that maps to the same step, a refreshed screenshot). Re-capture and
     re-author the refreshed artifacts; no halt.
   - **material** — anything else: a changed, added, or removed control or step,
     a route / role / glossary-term change, a side-effect reclassification, or a
     newly discovered interactive trigger.
4. **Halt on any material delta.**
   Revalidation skips only the initial accepted-manifest review for no-op or accepted-diff unchanged scope. Any material delta — to route, role, steps, glossary terms, side-effect class, or a changed/added/removed control or newly discovered interactive trigger — emits a delta manifest and halts for user acceptance per `manifest-discipline.md`.
   You do not re-capture a material delta before that acceptance closes.
5. **Re-capture and re-author only the deltas.** Refresh artifacts for the
   accepted-diff deltas and for the material deltas the user accepted in step 4.
   Untouched scope keeps its existing artifacts; you do not re-shoot a no-op.
6. **Run the completeness gate.** Build the coverage matrix and block on any
   unresolved row, exactly as on first authoring — see `completeness-gate.md`.
   Revalidation never publishes on a stale or incomplete matrix.

## How this differs from W1–W5

W1–W5 always halt for manifest review because, on first authoring, **all** scope
is unaccepted — there is no prior bless to lean on. Revalidation starts from a
manifest the user already accepted, so it can refresh artifacts inside that
accepted scope without re-asking. The carve-out is **bounded strictly to
unchanged scope**: the no-op and accepted-diff classes are defined so that
nothing which would alter what the user reviewed can slip through them. The
moment a delta touches a manifest-auditable field — route, role, step, glossary
term, side-effect class — or surfaces a control the manifest never enumerated,
it is **material**, and material always routes back through the
`manifest-discipline.md` "When the manifest changes" halt.

This is why the carve-out is not a loophole: it skips the review only where the
re-derived surface is provably identical to what was already blessed. Any
deviation that a reviewer would want to see is, by definition, a material delta
that emits a delta manifest and halts.

## Write-time canon

Every new chapter, and every chapter W6 re-authors for its own reasons, is
written with the full-target **embed** formula — the same formula in both
publish-target adapters (`static-md.md`, `obsidian-vault.md`), whether the
manifest is grouped or group-free.

The full-target **link** formula is not as uniform. `static-md.md` always
uses it. `obsidian-vault.md` uses it only under `publish.wikilinks: false`
— with wikilinks on, an Obsidian chapter links **vault-root-relative**
instead: `[[<vault-rel>/<group>/<slug>|Display title]]` (`<group>` present
only for a grouped entry), where `<vault-rel>` is computed against
`<vault-root>`, never the linking chapter's own directory
(`obsidian-vault.md`'s "Wikilinks vs Markdown links", §1a) — a *different*
relative-path computation, not the full-target formula's chapter-relative
one. That is a different link syntax entirely, not an exception to this
canon: a wikilinks-on chapter was never a candidate for the full-target
link formula, so there is nothing here for the canon to override. An
installed handbook may still carry the pre-1.8.0 bare `[[<slug>]]`
spelling for a chapter no run has yet touched; W6's union scan (below,
"Terminal-state convergence checklist") recognizes it and retargets it in
place — a recognized legacy spelling, not a second canon.

The gates this canon feeds are **resolution** checks, not spelling checks:
they verify that a link or embed target *resolves* on disk, not that it is
spelled the one canonical way. A retained chapter that W6 does not touch
keeps whatever spelling already resolves, byte for byte — the gate is
satisfied either way, so there is nothing to rewrite.

**The narrow claim, stated precisely:** 1.6.0 performs **no automatic
retroactive #220 repair**, and never rewrites a chapter *solely* because of
an upgrade or an `anyGroup` flip. An `anyGroup` flip alone is always
informational only (see the note under "Boundary triggers" below) — it
never by itself triggers a rewrite of an untouched chapter. A chapter *is*
legitimately rewritten when something else causes it: an ordinary W6
accepted-diff or material re-author ("The flow" above), or the manual
group-migration recipe below. Neither of those is new in 1.6.0, and neither
is what an `anyGroup` flip alone would do.

## Manual-migration boundary (the group axis)

1.5.0 adds an optional `group`/`group_title` pair to manifest entries (see [manifest-discipline.md](manifest-discipline.md)). Moving an entry between groups, renaming a group's title, or removing a grouped entry all require moving files on disk — the chapter file, its asset directory, and its index-file line and container — and that relocation is **not automated in 1.5.0**. Instead of moving anything itself, the skill halts and hands you a recipe: no automated relocation, no in-place rewrite of chapters the delta did not touch, no journal or rollback machinery, no container rename/delete, no inbound-link rewriter, no capture-spec updater. You are the transaction engine; the halt text below tells you exactly what to move and edit.

### Boundary triggers

A W6 manifest delta splits every entry into one of three domains: **retained** (present in both the old and the new manifest), **new-only** (added — this is ordinary W1–W5 authoring and never a migration matter, even when the addition is the manifest's first grouped entry), and **old-only** (removed). The boundary halts with the manual group-migration recipe below on exactly three trigger kinds, and only when the old manifest, the new manifest, or both contain at least one grouped entry:

- a `group` field added, removed, or changed on a **retained** entry;
- a `group_title` change on a **retained** group;
- a **grouped** old-only entry — a flat removal needs no migration, because nothing moved outside the flat slots of `publish.chapters_dir` and `capture.output_dir`.

An anyGroup flip with no retained-entry group change is NOT a halt — it surfaces only an informational write-canon note in the W6 report, because an untouched chapter's links keep resolving (nothing they point at moved), so the skill never imposes a retroactive rewrite just because the manifest went from group-free to grouped or back elsewhere. The note tells you only that new and rewritten chapters are now written with the group-aware path and embed formulas — see the write-time canon rule the adapters follow (`static-md.md`, `obsidian-vault.md`).

### The manual group-migration recipe

Shared by both publish-target adapters — follow it exactly regardless of which one the profile resolves to. For each changed entry the halt names:

1. Move the chapter file from its old path to its current derived path — `publish.chapters_dir/<slug>.md` flat, `publish.chapters_dir/<group>/<slug>.md` grouped.
2. Move the asset directory from its old path to its current derived path — `capture.output_dir/<slug>/` flat, `capture.output_dir/<group>/<slug>/` grouped. Do NOT re-run capture for this move: a group-only relocation is not a feature change, so the existing screenshots are still correct — see the recapture carve-out in `manifest-discipline.md`.
3. Update the index file: retarget or remove the old line, then wire the current line under the correct container, creating the container heading first if it does not exist yet — the same container-resolution wiring the adapter uses when establishing a brand-new grouped chapter.
4. Rewrite the moved chapter's own **embeds** using the full-target formula — ALWAYS, regardless of whether the entry's destination is flat or grouped, and regardless of adapter or `publish.wikilinks` mode. The full-target spelling resolves in every mode; a flat-only spelling can permanently fail to resolve for a chapter whose destination sits directly under `capture.output_dir`. Rewrite its **chapter-target links** — sibling-chapter links, wherever they appear (Related block or elsewhere) — using each adapter's own chapter-link canon: the full-target relative formula for `static-md.md` always and for `obsidian-vault.md` under `publish.wikilinks: false`, or `obsidian-vault.md`'s vault-root-relative `[[<vault-rel>/<group>/<slug>|Display title]]` wikilink (§1a — NOT the pre-1.8.0 bare `[[<slug>|Display title]]` spelling) under `publish.wikilinks: true`. Rewrite its **glossary-target links** — Related-block glossary links and first-occurrence glossary links alike — using each adapter's separate **glossary** canon instead, per its own "Glossary backlink discipline" section: never the chapter-link formula, even within the same mode — the two target types use different formulas. `static-md.md` also has a mandatory **index-target link** back to `{{publish.index_file}}` (its navigability check, "Link-integrity gate before you publish") — rewrite it the same depth-sensitive way, per "Relative links — the general rule"'s index case, never the chapter-link or glossary-link formula. `obsidian-vault.md` has no equivalent mandatory index-target link in its Related block, so this case does not apply there.
5. Fix inbound links from other chapters that referenced the old path.
6. Update the project's capture spec output dir(s) so future captures write to the current derived asset dir.
7. For a removed entry: delete its chapter file, its asset directory, and its index line. Removing the now-possibly-empty container is optional — your call, not the skill's.
8. Re-run. The skill re-verifies every terminal-state fact below and, only once they all hold, runs the post-migration handbook-wide link scan before it considers the migration complete.

### Terminal-state convergence checklist

The halt is not delta-consuming on its own — the completion bar is terminal state on disk, re-checked fresh on every run. `manualMigrationChecklist` in `assets/lib/chapter-paths.mjs` emits the facts below per changed entry; every fact must hold before the migration is treated as done.

**Retained entry, `group` changed** — path facts:

- the chapter file exists at the current derived path;
- the asset directory exists at the current derived dir;
- the index line targets the current path under the correct container — the same step-0 idempotency machinery the adapters already use to establish a new chapter; a flat destination is membership-only, no container;
- the capture-spec fact: call `specReferencesDir(specText, dir)` once with the old asset dir's `capture.output_dir`-qualified form and once with its `output_dir`-relative tail (`<group>/<slug>` or `<slug>`) — either call reporting a hit is decisive negative evidence and the fact is UNMET, while neither hitting proves nothing on its own (a capture spec is arbitrary user TypeScript, so completion there is not mechanically provable) and the fact is met only by EXPLICIT USER CONFIRMATION that the spec now writes exclusively to the current derived dir(s);
- when `oldEntry` is available: the old chapter path is gone and the old asset directory is gone;
- no index line targets the OLD path, proved per the index form. Path-link indexes: no line matches the old relative expected target — the same `relative(dirname(index_file), chapter_file)` coordinate system the current-target check uses, computed against the old chapter file. Wikilinks mode (`wikilinks: true`) now searches a concrete **qualified** old target too — `currentIndexExpectedTarget`'s vault-root-relative formula (`obsidian-vault.md`'s §1a) computed against the old entry, which a group-slug rename always changes (`handbook/admin/items` -> `handbook/management/items`), so the old and new lines are never textually identical under this formula: inspect every line whose folded target matches the old qualified target via `locateChapterLine`'s `matches` array (`{wikilink: true}`, so a `.md`-suffixed spelling still counts) and confirm none survives. A pre-1.8.0 handbook may ALSO still carry the old entry's **legacy bare** `[[<old slug>]]` row — that must be gone too, but the check is scoped to lines sitting under the container titled the OLD `group_title` (the halt record below is where that title comes from on a context-free re-run), never a vault-wide bare-slug scan: a root grouped-to-flat migration can make the new flat target equal the old bare slug, and a global "bare slug also gone" rule would forbid the very row the migration must create — scoping to the old container makes both requirements satisfiable at once. If the old container is gone entirely, both the qualified-gone and the scoped legacy-bare-gone halves of the fact hold trivially. Non-heading index forms have no parser to check placement with, so the fact is explicit user confirmation, same pattern as the capture-spec fact above.

A retained entry moving from grouped to flat keeps every fact above except the title fact, which never applies to a flat destination — there is no current `group_title` to check the index line against. Its current chapter file, asset dir, and index line are checked against the shipped flat wiring — membership only, no container — while the capture-spec fact and the old-gone / no-old-index-target facts still apply exactly as above, verifying the move away from the old grouped location actually completed.

**Retained entry, `group_title` changed** — the title fact, which fires whenever `old.group_title !== new.group_title` **and the destination is grouped** (a grouped-to-flat change has no title fact — see the paragraph above), and which unions with any path facts above when the same entry also changed `group`:

- headings-form index: the index line sits under a container titled the CURRENT `group_title` — parsed, so this is sound;
- non-heading index: no parser exists, and raw string presence is gameable (a title that shrank to a substring of the old label, or that string appearing in unrelated nav text, would be vacuously "present"), so the fact is EXPLICIT USER CONFIRMATION that the container labeling this chapter's entry now reads the current `group_title`.

**Grouped entry removed** (`newEntry` is null):

- the old chapter path is gone and the old asset directory is gone;
- no index line targets the old path — for path-link indexes, no line matches the old relative expected target, exactly as in the group-change proof above; for wikilinks mode, no line matches the old entry's **qualified** target either (same `currentIndexExpectedTarget` formula, computed against the old entry) AND no line carries the old entry's **legacy bare** `[[<old slug>]]` spelling under the container titled the OLD `group_title` (from the halt record below) — unlike the group-change case there is no current line to protect, so both halves of the fact simply hold once the old container carries neither spelling, or hold trivially when the old container is gone entirely; non-heading indexes fall back to explicit confirmation, same as elsewhere;
- no live capture sink for the removed entry: call `specReferencesDir(specText, dir)` against the removed entry's old dir, both spellings — a hit is UNMET, otherwise the fact is explicit user confirmation that the removed entry's spec or section has been deleted or disabled, so a leftover spec cannot silently recreate the deleted assets on the next capture run;
- no chapter contains a wikilink that can reference the removed entry: call `chapterHasWikilinkTo(chapterText, slug, oldChapterRelPath)` against every remaining chapter's text, which strips the `|`/`#`/`^` suffixes and one terminal `.md` before classifying the target — an unqualified target is forbidden when its basename case-insensitively equals the removed slug, a qualified target (containing `/`) is forbidden when its path components are a component-aligned, case-insensitive suffix of the removed chapter's old path, and permitted when it is a differently-qualified explicit path to something else — a deliberate correction to a foreign note, not a stale reference to the one that got removed; any forbidden hit is UNMET.

### Post-migration handbook-wide link scan

Once every fact above holds for every changed entry, W6 runs a post-migration handbook-wide link scan: every markdown link, embed, and wikilink in every current chapter must resolve — one pass, verification only, not scoped to the touched chapters. This is what catches a stale inbound link sitting in a chapter the migration never touched: the adapters' link-integrity gates are chapter-scoped and would never revisit it, and revalidation's preserve-untouched rule would otherwise skip it entirely. The existing chapter-scoped gates still run for the touched chapters on top of this scan.

The delta counts as consumed only when the facts above AND this scan both pass in the same run. A terminal fact can regress while you are fixing the reported links, so a scan failure does not let the retry skip straight back to the scan alone — it re-halts with the scan-failure text below, which re-embeds the full migration record, and the retry order is fixed: re-verify every terminal-state fact above, then repeat the handbook-wide link scan, then re-run the touched-chapter gates, before the migration counts as complete. Ordinary publishes — no migration delta pending, no unresolved scan-failure halt — keep the shipped chapter-scoped gates unchanged; this scan never runs for them.

### Delta lifetime and the stale-artifact advisory

The old manifest exists exactly where the delta is detected: W6 *is* the manifest-edit review step, so the pre-edit manifest is already in context when `groupChanges` runs. The halt text embeds every old path it derives, so the recipe needs no separate storage, and the delta is consumed the moment the terminal facts hold — the facts themselves are the record; there is no durable snapshot and no journal.

This has a real limitation, stated plainly: if a manifest is edited outside W6 and the skill next sees it fresh — with no old manifest anywhere in context — the boundary cannot fire, because there is nothing to diff against. Establishment machinery then wires the current manifest as if it were new, which can leave stale old-grouping chapter files and asset directories behind without any halt to catch it.

The mitigation is visible, not silent: on any anyGroup manifest run, print a non-blocking stale-artifact advisory listing chapter files under `publish.chapters_dir` and asset directories under `capture.output_dir` that are not derivable from the current manifest — a plain set-diff over every entry's `chapterRelPath`/`chapterAssetDir` against what is actually on disk. This is a WARNING with a pointer back to this recipe, never a halt: a foreign file some other process legitimately owns is not this skill's business to delete, so the advisory errs toward visible-but-not-blocking rather than a false-positive halt.

### Halt texts

Both halts below are produced verbatim by `renderManualMigrationHalt` in `assets/lib/chapter-paths.mjs` — the wording here is the same production text, not a paraphrase, so a context-free later invocation can reconstruct every terminal-state check straight from what got printed.

**Manual migration halt** — one entry-line per changed entry, in delta order; which of the three forms below applies depends on the entry's change kind:

```
This manifest change requires manual group migration (not automated in 1.5.0):
  <slug>: <old_chapter_path> -> <new_chapter_path>; assets <old_asset_dir> -> <new_asset_dir>
  <slug>: container title '<old_title>' -> '<new_title>'
  <slug>: removed — delete <old_chapter_path>, <old_asset_dir>, and its index line (was under container '<old_title>')
Follow the manual migration recipe in references/revalidation.md, then re-run.
```

The first form (a `group` change) gets suffixed with `; was under container '<old_title>'` whenever the source side was grouped — grouped-to-grouped and grouped-to-flat moves both need the old container title for the container-scoped legacy-bare-gone proof above, and a grouped-to-flat move's current entry has no `group_title` of its own to supply it, so only the record can (a flat-to-grouped move has no old container and takes no suffix). Every grouped entry carries a `group_title`, so whenever the suffix is owed, the field to fill it with always exists.

**Scan-failure halt** — printed only after the terminal-state facts all held but the post-migration scan above still found a break. It re-embeds the full original migration record — the per-entry lines above, verbatim — between the two lines below, because a fact can regress while you are fixing the reported links, and the retry needs the whole record again, not just the new breakage:

```
Post-migration link scan failed (<n> broken): <chapter>:<line> -> <target> ….
Fix the listed links, then re-run — the re-run MUST re-verify the terminal facts above, repeat the handbook-wide link scan, and re-run the touched-chapter gates, in that order, before this migration counts as complete.
```

For a role-axis diff of the interactive surface — how it differs between roles rather than over time — see [surface-diff.md](surface-diff.md).
