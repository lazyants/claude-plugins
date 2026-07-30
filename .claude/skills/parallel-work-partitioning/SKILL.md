---
name: parallel-work-partitioning
description: Planning technique for splitting an issue/change set across two or more parallel sessions, worktrees, or teammates BEFORE dispatch. Use when deciding who owns what ahead of a fan-out, sanity-checking a scout-proposed split, or partitioning a batch of issues/tickets across parallel workers. Covers why the partition is forced by shared FILES rather than topic, why a grep of current code under-detects the right split, cache-bundle membership as a second partitioning axis, shared registration files as merge-time (not dev-time) coordination, and why a scout's fix_approach is not implementability-verified until codex or a reviewer confirms the supporting contract exists.
---

# Partitioning a set of changes across parallel sessions / worktrees

Forged 2026-07-17 splitting the SSK-audit literary-translator issues into two parallel-session groups (user drives one session, another drives the other). This is a planning technique — apply it BEFORE fanning out, not after.

## The partition is forced by shared FILES, not by topic

Two parallel sessions/worktrees must not edit the **same file** (branches collide at merge; teammates in one session share cwd). So the real constraint is a **file-coupling graph**: issues sharing a file form a connected component that MUST live in one owner — even when they look like distinct features. Example: `render_obsidian.py` coupled **#206 (matcher), #207 (homonym collapse), Phase-3 index_scope, #200 (cross-volume linker), #203 (embedded-language display)** into ONE un-splittable owner, purely because every fix lands in that one renderer. Whole components assign freely to either group; only balance is negotiable, disjointness is not.

## A grep of CURRENT code UNDER-detects the coupling

A first-pass grep for where each issue is *currently mentioned* is not enough — a fix often LANDS in a file the current code doesn't yet reference. In the SSK-audit case, grepping for current mentions put #200/#203 in the extraction group. **Wrong** — a deeper "**where would this fix GO**" read (a scout per cluster) correctly moved #200 (cross-volume backlink resolution) and #203 (embedded-language display) into the renderer group. **Partition by "where the fix lands," not "where the symbol appears."** Grep is a floor, not the answer.

## Cache-bundle membership = a second partitioning axis

Beyond file-disjointness, segregate by **cache blast radius**: put ALL cache-invalidating edits (files in `PLUGIN_BUNDLE_MEMBERS` / `DERIVATION_BUNDLE_MEMBERS` / a `schema_hash` schema) into ONE group so a single release absorbs the single mass re-translation; keep the other group **cache-safe** so it ships freely with zero re-translation. In the SSK-audit split, the renderer group was fully cache-safe (`render_obsidian.py` is in no bundle) while the extraction group carried the derivation-member landmines — a clean split on both axes at once. (Hash-surface facts → skill:literary-translator-ops.)

## Disjoint FILES are not disjoint CLAIMS — a comment about a sibling file is a shared surface

A file-level partition does not protect a claim whose subject is another lane's file. Two lanes edit
non-overlapping files; lane A's comment states a fact about lane B's file; lane B's edit falsifies it
in the same round. Nothing collides, every lane is correct on its own file, and the false claim
ships.

Verified twice in one round, the second time in the *replacement* for the first: a comment asserted
the sibling template "contains no `rejectedAnywhere` at all" while the same working tree had two —
written while another lane was porting exactly that. Both versions read true when authored and were
false within the hour.

**Why single-file review structurally cannot catch it:** a claim about file B has no edit in file A
that can invalidate it. Reviewing A never surfaces it, and B's reviewer never opens A. That is the
mechanism, and it is why the failure repeats rather than being a slip.

- At dispatch: name it. Ask whoever edits a file to grep the others for assertions *about* it — the
  cheap guard, and the one no file partition provides.
- In the fix: do not update the claim, **delete it**. Replace an observation about another file with
  a citation of the check that ENFORCES the agreement. A pointer to an enforcing test stays true as
  long as the test exists and fails loudly when it does not; a remembered fact rots silently.

## Shared REGISTRATION files are merge-time, not dev-time, collisions

The version quartet (plugin.json / marketplace.json / README / CHANGELOG), central schemas, and shared docs (SKILL.md, a cross-referenced adapter doc) get touched by both groups but are **integrator-wired at merge** (sequence the version bumps, e.g. group A→1.8.0, group B→1.9.0). They don't block the split — flag them as coordination, not as a coupling edge.

## A scout's `fix_approach` is NOT implementability-verified — the codex plan-loop still finds the missing contract

An investigation/scout Workflow reliably reports **root cause + fix LOCATION**, but reliably **UNDER-checks whether the fix's supporting CONTRACT exists**. In the SSK-audit case, scouts recommended driving homonym-correct backlinks from `canon_senses` sense-assignment; the codex plan-review then found `canon_senses` carries **no `sense → note_identity` key and no source→target text alignment** — the whole approach was impossible, and the plan shrank hard (index_scope/#206-matcher/#203 all deferred because the contracts they need — a sense→note map, a "which category = person" declaration, a per-node language field — simply don't exist yet). **Do not treat a scout's fix_approach as buildable.** The codex plan-loop is exactly what catches the "this fix needs a mapping/field/alignment that doesn't exist" class — run it even after a thorough investigation.

## The memory INDEX is shared even when code files aren't — defer index rewrites during a live parallel session

The auto-memory dir (`~/.claude*/projects/<slug>/memory/`) is a **whole-dir symlink shared across all profiles**, so two parallel CC sessions write the **same `MEMORY.md` and `index_*.md`** — a coupling the code-file partition above does NOT cover (their repo files can be perfectly disjoint yet both append to the shared index; last-writer-wins clobbers the other's line). So during an active parallel session: (a) **do NOT rewrite/compact a shared index line the other session is editing** — e.g. a size-hook asking to compact `MEMORY.md` while the parallel session just wrote a big pointer line: DEFER the compaction until it's done; (b) capture new learnings in **unique-named memory FILES** (zero clobber) and point them from an EXISTING already-pointed file rather than adding a fresh `MEMORY.md` pointer into the contested index. Same spirit as the code-file rule: find the disjoint surface and stay on it.

## A read of a teammate's work is valid only for the instant it was taken

While teammates are actively editing, disk state and their reports are both **timestamps**, not
facts. Reading at the start of a turn and acting on it at the end compares two different moments —
and the gap is where the wrong conclusion forms. Measured 2026-07-29 (enduser-handbook 1.12.0, five
parallel teammates): this produced four false findings in one session — a teammate told their fix
"had not landed" when it had, a "banner missing" report contradicted by my own test run **in the
same turn**, and, worst, a teammate told to stand down and hand over their files while they were
mid-flight and had already landed the exports being waited on. Each time the teammate's report was
accurate and my read was stale; the reverse never once occurred.

Two rules follow. **Re-read immediately before sending any corrective or directive message**, not at
the start of the turn — the cost is one command and the alternative is instructing someone to undo
correct work. And **never infer state from silence**: the one teammate who reported least was
working fastest, and their quiet was mistaken for a stall three separate times.

The same asymmetry applies to needles. A grep for an exact phrase reported a teammate had skipped a
documentation note they had in fact written in different words — an over-precise needle producing a
false accusation about someone else's work. Search for the substance, and when a check contradicts a
teammate's specific claim, assume the check is wrong first.

## Related

- skill:git-worktree-pr-mechanics — one worktree per parallel session; concurrent-worktree version-collision recovery.
- skill:subagent-trust-verification — pinning the shared contract before fan-out; integrator wires registration files.
