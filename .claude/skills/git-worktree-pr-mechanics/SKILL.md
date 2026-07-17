---
name: git-worktree-pr-mechanics
description: Git worktree/branch/PR failure-recovery mechanics — use when isolating a single change into a clean commit amid unrelated churn, running git-state ops safely while subagents or teammates share the working tree, reviving and reconciling a stale unmerged branch onto current main, detecting and recovering from a parallel session that deleted your worktree/branch or contaminated the shared tree, or resolving a version-number collision when concurrent worktrees target the same next release.
---

Recovery playbooks for git worktree, branch, and PR hazards — most arising when multiple Claude Code sessions or teammates operate on the same repo with no visibility into each other. Branches, stashes, and the working tree are repo-global, NOT worktree- or teammate-scoped; worktree isolation does NOT isolate the shared main tree. A concurrent process can delete a ref, mutate a file, or claim a version number out from under you.

**One cross-cutting habit:** during any multi-step git surgery, capture commit SHAs proactively from `git log`/reflog BEFORE running anything that could move or delete a ref — the SHA is your recovery handle if a ref vanishes. And verify outcomes by content (diff, grep, tree-hash), never by exit code alone.

Read the reference file that matches the task:

- **`references/subset-commit-and-shared-tree-safety.md`** — read when committing only your change to a file that also carries unrelated churn, or when running any git-state op (`status`, `add`, `stash`, `checkout --`, `reset`) while subagents/teammates share the cwd. Covers the stash-isolate recipe, the concurrent-git-readout race, and the `cp -i` silent-no-op restore trap.
- **`references/reviving-stale-branch.md`** — read when you find a fully-committed, forked-a-while-ago branch/worktree that looks like it already did your work, and you must decide revive-vs-rebuild and reconcile it onto current main with minimal conflict surface.
- **`references/parallel-session-races.md`** — read when a ref vanishes mid-operation, an adjacent cluster merges while your investigation is in flight, or a stash command unexpectedly reports "No local changes to save". Covers race detection, ref recovery via SHA, and selective re-verification of in-flight findings.
- **`references/version-collision-reconciliation.md`** — read when a concurrent worktree/session targets the same next release version, when the shared main tree gets contaminated by a peer's edits, or when a race lands mid-review or after your PR is already open. Covers proactive cede, the version-string auto-merge trap, contaminated-tree recovery, and post-PR-open rebase resolution.
