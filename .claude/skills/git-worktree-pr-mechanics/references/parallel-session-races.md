# Parallel-session races: detection, ref recovery, selective re-verification

Root cause across all of these: concurrent sessions/worktrees on the same repo have NO visibility into each other. "I'm working on other issues in parallel" can mean the *same* issue cluster via a different channel (another session, a Workflow with `isolation: 'worktree'`), not necessarily disjoint work — don't assume "other" means "non-overlapping."

## Before destroying anything, PROVE ownership — codex job records carry a `sessionId`

That premise is true of git and false of the codex plugin, which is the one channel that names its owner. Nothing in `git worktree list`, `git status`, a branch ref or an mtime says WHO. But every job record under `${CLAUDE_CONFIG_DIR}/plugins/data/codex-openai-codex/state/<project>-<hash>/jobs/task-*.json` carries `sessionId`, `workspaceRoot`, `status` and `pid` — so for any session doing codex work, ownership is an OBSERVATION, not an inference.

**Run this before merging a PR you did not open, pruning a worktree you did not create, or deleting a branch you do not recognise.** Glob across every profile and every project state dir (`~/.claude*/plugins/data/codex-openai-codex/state/*/jobs/*.json` — the state trees are per-profile), keep the last hour by mtime, group by `sessionId`, and compare against your own. `workspaceRoot` then names the exact worktree that session was driving.

**What the record proves is ATTRIBUTION, and attribution is not liveness — do not read `status` as "someone is there right now".** The plugin rewrites that field only on normal completion or a `SessionEnd` hook, so a killed worker, a crashed machine or a missed hook leaves `running` on disk forever, and an mtime window happily selects it. Measured on this machine 2026-07-31 while writing this section: **668 records carried `status: "running"` and 667 of their pids were dead** — a 1-in-668 precision, i.e. the field is worthless as a liveness signal on its own. A stopped cadence of `completed` jobs is the same trap one step removed: it proves the peer was there, not that it still is.

**So corroborate before calling an owner live, with a signal that decays on its own:** `ps -p <pid>` for the recorded pid, and compare the process's start time against the record's `startedAt` so a reused pid is not accepted as the original worker. Cheapest and strongest where it applies — sample the state dir twice a few minutes apart and look for a NEW record, or a head/branch that moved; something that must be actively produced cannot be left behind by a dead session. Absent all of that, the honest verdict is "recently attributed to session X, liveness unknown" — which is still enough to refuse a destructive action, and that is the decision this test exists to gate.

This is the positive test the standing prohibition lacks: "a worktree being un-`locked` does not mean inactive" tells you what not to trust, and the inferential tells elsewhere in this file (mtime, moving-target files, diff content) establish that the tree changed but never who changed it. Verified 2026-07-31: twenty-plus `frozen*` review snapshots plus an open PR read as abandoned residue from an earlier session. Attribution by `sessionId` showed a DIFFERENT session had been driving them for five hours; the same sweep also surfaced a third session working an unrelated repo. Merging that PR — latest codex verdict `DO NOT SHIP` — and pruning those worktrees would have destroyed a live fourteen-round review loop. Liveness there rested on the two decaying signals, a fresh `completed` record every ~15 minutes and a PR head advancing every ~10, not on the `running` flag; the id is what turned "some peer, probably" into "session X, this worktree".

Two limits, both structural: a session that does no codex work leaves no record and stays invisible, so a clean sweep is not proof of absence — fall back to the inferential tells below. And a single-profile glob silently misses a peer running under a different `CLAUDE_CONFIG_DIR`, which is the same shape as every other absence claim made with too narrow a search.

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
