# Reviving a stale unmerged branch

**When:** you find a fully-committed, possibly-pushed branch/worktree from an earlier session that looks like it already did the work you're about to start — but it forked from main a while ago (commonly from a mid-flight WIP snapshot that was later squash-merged under a different hash, possibly different scope).

## Diagnose before touching anything

1. `git merge-base --is-ancestor <branch's-base-commit> main` — if NO, the branch didn't fork from a real point on main's current history. It likely forked from a mid-flight WIP commit later squash-merged differently into what's on main now.
2. Diff the branch's OWN payload commit against its DIRECT PARENT: `git diff <parent> <tip> --stat` — NOT against current main. If that file list is clean/scoped to the intended work (no unrelated files), the branch's real contribution is narrow and salvageable, even though a raw `git diff main <tip>` looks huge and scary (that conflates the branch's real work with everything main gained independently since the fork point).
3. `git merge-tree <merge-base> main <branch>` (dry run, no working-tree changes) — gives the real conflicted-file list before you commit to a reconciliation. Cheap, fast, tells you the true blast radius.

## Reconcile with minimal surface

If the branch's payload is one (or a few) commits sitting on top of a superseded WIP snapshot, don't replay the whole lineage:

`git rebase --onto main <superseded-parent> <branch>`

replays only the commits AFTER that parent, skipping the WIP snapshot's now-irrelevant diff entirely — avoids re-fighting conflicts against work that's already obsolete on both sides.

## Verify the result — don't trust a clean exit code alone

- Diff the reconciled tip against main and confirm the file list / line-count stats match the branch's original payload-vs-parent diff (from step 2) almost exactly. A match proves the rebase reproduced the same net change rather than silently dropping or duplicating something.
- Grep for leftover `<<<<<<<` / `>>>>>>>` markers regardless of exit status.

## Before finishing

Re-check the target issue tracker / merged-PR list — a parallel session may have already shipped the same work while you were mid-diagnosis (see `references/parallel-session-races.md`).
