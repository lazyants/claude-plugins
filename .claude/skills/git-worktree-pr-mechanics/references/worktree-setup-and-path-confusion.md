# Isolated worktree setup; primary-vs-worktree path confusion

## Create the worktree off clean origin/main

For any `/goal-la` or parallel-implementation task, the FIRST git action is to create an isolated worktree off clean `origin/main`, and do ALL work there (lead + teammates):

```
git -C <primary> fetch origin -q
git worktree add <primary>/.claude/worktrees/<slug> -b <branch> origin/main
```

`.claude/worktrees/` is the established location. Never `git checkout -b` in the primary checkout.

## Teammates share the LEAD's cwd, not the worktree

Teammates share the LEAD's process cwd (the primary checkout), NOT the worktree — the Agent `isolation:"worktree"` param gives each agent its OWN separate worktree, which fragments disjoint-file work you need to consolidate on ONE branch. Instead: brief every teammate with ABSOLUTE paths UNDER the shared worktree for its owned files, and have them read/Edit/pytest via those absolute paths (all cwd-independent). Teammates do NO git ops; the LEAD does every git op FROM the worktree (`git -C <worktree> …`).

## If the primary checkout is already contaminated with a peer's WIP

`git checkout -b <new> origin/main` in the primary checkout PRESERVES any uncommitted edits already sitting there — it's a working-tree op, not scoped to `origin/main`'s committed state. Verified 2026-07-13: during a `/goal-la` on issues #174/#180/#181's cluster, session-start `git status` was clean, but by team-build time the primary checkout had 5 modified files never touched by this session (`render_obsidian.py`, `validate_draft.py`, `false-green-gate.md` + 2 tests, ~600 lines) — a CONCURRENT session's uncommitted WIP on branch `fix/lt-171-172-173` (issues #171/#172/#173). `false-green-gate.md` was itself one of this session's own target files (issues #174/#180/#181) — a direct collision. `git checkout -b fix/lt-1.4.2-… origin/main` carried the peer's uncommitted changes onto the new branch.

Recovery: since a fresh branch off the same commit shares identical tree content, `git checkout <their-branch>` is a pure HEAD-pointer move that LEAVES the other session's uncommitted files exactly as-is (verified) — restore their branch, `git branch -D` your contaminating branch, THEN create the worktree. Never `reset --hard` / `stash` their WIP.

## Repo-root files are the ones you'll accidentally edit at the WRONG checkout

Verified 2026-07-16, LT 1.5.0 assembly. Even inside a correct worktree: a version bump touches repo-root surfaces (`README.md`, `.claude-plugin/marketplace.json`) whose absolute paths LACK the `plugins/<name>/` segment that otherwise cues you to prefix the worktree root — so a Read/Edit at `<primary>/README.md` instead of `<primary>/.claude/worktrees/<slug>/README.md` silently edits the PRIMARY checkout (dirtying its checked-out `main`), and the worktree commit ends up MISSING the bumps. Tell: `git diff --stat origin/main` from the worktree omits the shared root files AND the worktree copy still reads the old version. Recovery: `git -C <primary> checkout -- <files>` to clean `main`, then re-Read+Edit the SAME files at worktree-rooted paths (the plugin-scoped files under `plugins/<name>/…` were assumed not to have this trap because their path already forces the reminder — see below for why that assumption is too strong). Prevention: consciously prefix the worktree root on EVERY repo-root file edit during a worktree build.

## Read-worktree / Edit-primary path mismatch fails LOUDLY only by luck

Verified 2026-07-19, LT 1.10.0. Reading `<worktree>/plugins/literary-translator/CHANGELOG.md`, then passing the PRIMARY-rooted path to Edit errored `String to replace not found` — but ONLY because the primary was pre-merge and lacked the 1.10.0 text. For any file whose target string exists in BOTH checkouts (the normal case for a stable file), the same slip **silently edits the wrong tree**: Edit reports success, the worktree copy is unchanged, and the commit quietly omits the change. So the previous section's assumption that "plugin-scoped files don't have this trap because their path already forces the reminder" is too strong — the real trap is the Read-path/Edit-path MISMATCH, not the missing `plugins/<name>/` segment. Guard: when you Read a worktree file in order to Edit it, copy the path from the Read call VERBATIM into the Edit call; never retype or reconstruct it from memory.

## A Workflow/agent dispatched at the worktree can silently read the STALE PRIMARY

Verified 2026-07-17. Even handed ABSOLUTE worktree paths in its prompt, a subagent's shell cwd resets to the primary checkout, so any RELATIVE read it runs (`pytest tests/…`, `cat plugins/<name>/.claude-plugin/plugin.json`) hits the stale primary — here 5 investigators reported the primary's `1.4.7` / `1994`-test numbers while the worktree was `1.5.0` / `2213`. Unchanged-file `file:line` anchors stay valid (identical bytes), but re-verify every version string / suite count / anything version-sensitive against the worktree yourself before baking it into a plan. Tell: the reported version/count matches `origin/main`'s PARENT commit, not the branch tip.

## Teammate variant: an absolute worktree path in the brief is NOT enough, and the failure is SILENT

Verified 2026-07-19, LT 1.11.0 five-agent build. A teammate briefed with the absolute worktree prefix still ran several Bash/Read/Edit calls against the PRIMARY checkout — landing its whole deliverable as uncommitted changes on `main`. Nothing failed loudly: the edits applied cleanly, its own tests passed, and it reported success. It surfaced only because a SECOND teammate's import of the first one's new symbol did not resolve and it thought to check `git status` in both trees.

Fix: (a) require each teammate to confirm its first written path back to you, not just receive it; (b) when one teammate reports a cross-owner symbol missing, check WHICH TREE before assuming it is unwritten; (c) recovery is `git diff > patch` + `git apply` in the worktree + `git checkout --` in primary — back it up FIRST (patch + raw copies) and verify the transplant by diff-line count against `origin/main` before discarding the primary copy.
