# Parallel-session races: detection, ref recovery, selective re-verification

Root cause across all of these: concurrent sessions/worktrees on the same repo have NO visibility into each other. "I'm working on other issues in parallel" can mean the *same* issue cluster via a different channel (another session, a Workflow with `isolation: 'worktree'`), not necessarily disjoint work — don't assume "other" means "non-overlapping."

## A ref vanishes mid-operation

git branch refs are repo-global, not worktree-scoped. A concurrent process (even in a different physical worktree) deleting or force-updating the same ref will yank it out from under an in-progress `git rebase` in another worktree — a session can independently finish the same cluster, merge it, and tear down the worktree as its own housekeeping step, racing your rebase.

- If a ref vanishes (worktree gone, branch unresolvable) while a concurrent session is active, don't assume data loss — the commit object usually survives ungarbage-collected. Recover immediately: `git branch rescue/<name> <sha>`, using the SHA you captured from `git log`/reflog output BEFORE the ref vanished. (This is why you capture SHAs proactively during any multi-step git surgery.)
- After confirming the "duplicate" work is safely on `main` via the merge commit, delete your own rescue branches and `git worktree prune` — don't leave scratch refs behind.

## Before sinking effort into reconciling a stale-but-matching branch

Re-check the target issues' live GitHub state and `gh pr list --state merged` for the repo — a parallel session may have already shipped it. Don't assume an old on-disk worktree is stale junk to delete on sight either: verify its issues aren't already closed (`gh issue view` / merged-PR search) first, since some may have been closed by a different parallel session's PR.

## An adjacent cluster merges while your investigation is in flight

A different session's PR can merge to `main` mid-investigation, closing a sub-part of YOUR issue scope and/or rewriting files your in-flight investigation agents are reading. Fast-forwarding your worktree onto the new `origin/main` is safe when you have zero local commits yet — but it raises which findings are still trustworthy. Don't blanket-redo everything (wasteful) or blanket-trust everything (risky). Partition by the merged PR's **touched-file list** (`gh pr view <n> --json files`):

- **Files the PR did NOT touch:** sanity-check the investigation's cited line/pattern still greps true on the new tree (cheap, seconds), then trust the finding as-is.
- **Files the PR DID touch:** treat the in-flight agent's finding as unverified regardless of whether it completed before or after the fast-forward (a completed-looking result can have read torn/inconsistent content mid-write). Re-dispatch a fresh verification agent against the settled post-merge tree, explicitly telling it what the merge changed and what's now out of scope.
- Also re-read the issue's LATEST comment (not just its original body) — the merged PR may have closed part of your scope; the latest comment, not the original filing, is ground truth.

## A stash command unexpectedly reports "No local changes to save"

Two concurrent sessions can independently reach the same "stale, superseded" diagnosis and `git stash` the identical files moments apart — your command then finds nothing left to stash because the peer won the race. Don't trust the stash message text to identify who/what created a stash; only the diff content is reliable evidence.

- When "No local changes to save" follows a stash you expected to succeed, immediately re-run `git stash list` and diff the top entry: `git stash show -p stash@{0}` against what you expected to stash.
- If it matches, a concurrent session got there first — the outcome is still correct (working tree clean, WIP preserved), just not via your own command.
