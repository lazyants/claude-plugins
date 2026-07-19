# Merge, squash-verification, and the review bot

Core rule, restated: **squash-merge can ship stale content — verify from `origin/main`, never trust the merge command's output.** A merge command printing a changed-file list is NOT proof the intended content landed.

## Contents

- Squash ships stale content — verify origin
- Predict a parallel-PR conflict read-only with `git merge-tree`
- Resolve a parallel-PR conflict on the shared surfaces
- Stacked PRs: deleting the parent's branch CLOSES the child
- Stale-base branch → rebase onto a MOVED main
- `gh pr merge --delete-branch` local error ≠ merge failure
- "Already merged" right after a merge-confirm = the user merged via GitHub UI
- `Closes #a, #b` auto-closes only the FIRST issue
- Audit "what's genuinely unmerged?" in a SQUASH-merge repo
- Handling the `lazy-ants-reviewer` (ped-ant) bot

## Squash ships stale content — verify origin

If you push a commit, then **amend** and push again over an open PR, the squash can land ONLY the first commit's content — the PR head on GitHub may still point at the old commit at merge time, so the squash uses it.

- After pushing an amend/rewrite to a PR branch, **confirm origin actually has it before merging:** `git show origin/<branch>:<file> | grep <expected>`. Prefer `git push --force-with-lease`, or just **add a NEW commit instead of amending** one already tied to an open PR.
- After ANY merge, verify from the remote: `git fetch && git show origin/main:<file>`. For a version/asset change, re-grep the actual values on `origin/main`.
- **Recover** (merge shipped stale content): the amended/rewritten commit is usually still in `git reflog` (look for `commit (amend)`). Cherry-pick it onto a fresh branch off updated `main`, re-PR, re-merge, verify `origin/main` again. `gh pr merge --squash --subject "…"` pins the squash message so it doesn't silently reuse a stale first-commit message.

## Predict a parallel-PR conflict read-only with `git merge-tree`

Two independent PRs off the same `main` that each bump their own plugin both touch the SHARED registration surfaces (`README.md` plugins table + `.claude-plugin/marketplace.json`); whichever merges SECOND conflicts. Don't guess the blast radius — COMPUTE it without touching the tree: `git merge-tree --write-tree --name-only <branchA> <branchB>` (git ≥2.38) reports exactly which files conflict. Typically `marketplace.json` **auto-merges** (the two version bumps sit in different plugin BLOCKS ~40 lines apart) and only `README.md` conflicts (the two plugins' table rows are ADJACENT, so the 3-line hunk contexts overlap). The conflict is trivial/additive — keep both rows. So merge the finished+approved PR FIRST and let the in-flight one absorb the one-hunk rebase. GitHub's per-PR "MERGEABLE" only flips to CONFLICTING once the first PR lands — `merge-tree` tells you the safe merge order in advance.

## Resolve a parallel-PR conflict on the shared surfaces

When a sibling plugin merged between your branch-point and your merge (`mergeable: CONFLICTING / DIRTY`): `git fetch origin main` → `git merge origin/main` into your branch → resolve by keeping BOTH plugins' rows/entries at their correct versions/anchors (theirs for the sibling, yours for your plugin) → re-run the suite → push. Then:

- Right after the resolving push, `gh pr view` often still shows `CONFLICTING` (GitHub recomputes mergeability async). Confirm locally with `git merge-base --is-ancestor origin/main HEAD` (true = no real conflict) and poll `gh pr view --json mergeable` until it flips to `MERGEABLE`. An `UNSTABLE` state is normal (the review-bot check is advisory). Merge only then.

## Stacked PRs: deleting the parent's branch CLOSES the child

Merging a PR with `--delete-branch` while a second PR is **stacked** on it (child's base = the branch being deleted) does NOT retarget the child to `main` — GitHub **closes** the child, and a closed PR whose base branch is gone **cannot be reopened or retargeted** (`gh pr edit --base main` and `gh pr reopen` both hard-error "Cannot change the base branch of a closed pull request"). The child also goes CONFLICTING (the squash put the parent's content on `main` under a new SHA). Recovery: `git merge origin/main` into the child, resolve the shared file (`git checkout --ours <file>` keeps BOTH bullets if the child branch already had the superset), push, open a NEW PR to `main` (the closed one stays dead). Avoid entirely: merge the child BEFORE deleting the parent's branch, or land the parent then rebase the child onto `main` first.

Also: `gh pr merge --squash` prints the **PR-vs-merge-base** diffstat (can show far more files than really changed when the stack's merge-base predates the parent merge), NOT the squash commit's real diff. Verify what actually landed: `git show --stat origin/main` (expect only the child's own files) + a duplication grep — each changed string must appear EXACTLY once on `main` (no double-applied parent content).

## Stale-base branch → rebase onto a MOVED main

A feature branch built off an OLD base while `main` moved ahead must be **rebased onto `origin/main`, never merged as-is** (the stale base would revert the newer PRs' work). `git rebase --onto origin/main <oldbase>~1` replays the branch's own commits onto current main; only files the branch's commits TOUCHED can conflict (untouched files correctly take main's version). The general revive-vs-rebuild decision and minimal-conflict reconciliation are in the `git-worktree-pr-mechanics` skill; the marketplace-repo-specific guards:

- **A test-count JUMP is the tell the base was stale** (e.g. suite went 676→1074 after the rebase because main had gained hundreds of tests during the base PR's review rounds). Eyeball the jump and explain it; don't just trust the green.
- **Prove NO stale-shadowing via the DELETION count:** `git diff origin/main HEAD --stat` — a tiny total deletion count proves the branch only adds/replaces intended lines; a LARGE deletion count means the ancient-based commits silently reverted main's evolved files. Confirm every file in the diff is an intended add/change; run `git diff origin/main HEAD -- <sharedfile> | grep '^-'` on each auto-merged shared file to see it only removes what you meant to.
- **Reconcile with a newer authoring PR by taking BOTH:** keep the newer PR's fix AS THE BASE and layer the feature delta on top (e.g. a `plugin.json` description shortened to be byte-identical to the marketplace entry + author-path scrub → keep those, then add the new-version clause + bump). Auto-merged shared files still need a spot check that the newer PR's edit survived the merge.

## `gh pr merge --delete-branch` local error ≠ merge failure

Running `gh pr merge <n> --squash --delete-branch …` while a linked worktree (or a teammate's worktree) holds the PR branch fails locally with `failed to run git: fatal: 'main' is already used by worktree at '<path>'` or `failed to delete local branch <b>: cannot delete branch used by worktree`. This is the LOCAL post-merge step (gh trying to switch/delete after the merge) — **the remote squash-merge already SUCCEEDED.** Do NOT re-run the merge (it would 404 on an already-merged PR). Verify remotely instead:

- `gh pr view <n> --json state,mergedAt,mergeCommit` → `state:"MERGED"` (there is NO `merged` json field — use `state`/`mergedAt`/`mergeCommit`), and `git fetch origin main` → top commit is the squash; re-grep `origin/main` for the intended content.
- The `--delete-branch` may also not have run: `git ls-remote --heads origin <branch>` still shows it → delete manually with `git push origin --delete <branch>` (a non-`main` branch delete is NOT blocked by the auto-mode classifier, unlike a push TO main).
- For the local worktree/branch teardown the failed step skipped, see the `git-worktree-pr-mechanics` skill.

## "Already merged" right after a merge-confirm = the user merged via GitHub UI

On a public-marketplace drive the auto-mode classifier blocks a self-authored auto-merge, so the flow is: AskUserQuestion → user selects "merge now" (their SELECTION is the genuine confirm) → you RETRY the merge. If the retry prints `! Pull request #N was already merged` and `git fetch origin main` already shows origin/main AT the squash commit, the **user merged it themselves via the GitHub UI** — treat "already merged" as the EXPECTED outcome, not an anomaly to chase. Still verify:

- **Byte-identity of the shipped content** — `git diff --stat <your-local-commit> origin/main` is EMPTY ⇒ main == exactly your committed tree (no stale-squash, no peer contamination). This one check settles "who merged it / is it my content" fastest.
- Merge commit subject == your PR title + `(#N)`; `gh pr view N --json state,mergedAt,mergeCommit` → `MERGED`; re-grep the version on `origin/main`; the auto-closed issues' `closedAt` matches the merge time.

## `Closes #a, #b` auto-closes only the FIRST issue

A closing keyword binds to the ONE issue reference immediately after it; it does NOT distribute across a comma list. `Closes #78, #77, #63, #61` auto-closes only **#78**. GitHub requires a keyword before EACH number: `Closes #78, closes #77, closes #63, closes #61` (or one `Closes #n` per line). Guard: after any squash-merge whose PR body lists multiple `Closes`, re-check every linked issue with `gh issue view <n> --json state,stateReason` and close the stragglers manually with `gh issue close <n> --comment "…"` — carry each issue's premise-correction note into its closing comment rather than a bare close. Best fixed at authoring time by repeating the keyword per issue.

## Audit "what's genuinely unmerged?" in a SQUASH-merge repo

`git branch --no-merged origin/main` (and `git cherry`, and any tip-is-ancestor test) **over-reports** here: a squash-merge lands the branch's CONTENT under a NEW commit SHA, so the source branch's tip is never an ancestor of `main` and shows as "not merged" even though it fully shipped. Don't trust ancestry — check two things: (1) the upstream **`[gone]`** marker in `git branch -vv` (the remote branch was deleted on merge = strong shipped signal), and (2) the branch's **content is on `main`** — grep a signature line on `origin/main` (a CHANGELOG entry, a distinctive symbol) rather than diffing (an old branch diffs huge against an advanced main without being unmerged). Both true ⇒ shipped residue, safe to prune; a branch with a LIVE remote whose content is absent from main ⇒ genuinely unmerged.

Traps: `--no-merged` needs a fresh `git fetch --prune` AT report time — with active parallel sessions a merge-status answer has a seconds-long shelf-life. A worktree being un-`locked` does NOT mean inactive — never prune another domain's branch/worktree on the "not locked" signal alone (see the `git-worktree-pr-mechanics` skill). And **keep each outward-facing git op in its OWN Bash command:** the auto-mode permission classifier denies the ENTIRE batch if any one line is gated, so bundling `git push origin --delete` with local cleanup collateral-denies the safe LOCAL work — run local removals as one command and each remote `--delete` separately.

## Handling the `lazy-ants-reviewer` (ped-ant) bot

Every PR here gets a `lazy-ants-reviewer` bot review (a check-run + a GitHub review). It catches real bugs codex misses, so ADDRESS its findings; the mechanics cost retries:

- **A `NEUTRAL` check conclusion is ADVISORY, not merge-blocking.** The bot posts `CHANGES_REQUESTED` with inline findings, yet `reviewDecision` stays `null` and `mergeStateStatus` sits at `UNSTABLE` (normal here — no CI); the PR is still `MERGEABLE`. Don't read UNSTABLE/CHANGES_REQUESTED as "blocked" — but resolve it (below) for a clean merge. It re-reviews automatically on push: after a fix commit the check flips `IN_PROGRESS`→done (~3–4 min); zero new inline comments on the new head = satisfied.
- **Reply to an inline review comment** via the dedicated replies endpoint: `gh api repos/O/R/pulls/<N>/comments/<comment_id>/replies -f body=…`. The `-f in_reply_to=<id>` FORM field on the `/pulls/<N>/comments` collection endpoint is REJECTED (`422 "in_reply_to is not a permitted key"`).
- **Resolve a review thread** (clears `UNSTABLE`→`CLEAN`) via GraphQL, not REST: read the id from `reviewThreads(first:N){nodes{id isResolved isOutdated}}`, then `mutation{ resolveReviewThread(input:{threadId:"<id>"}){thread{isResolved}} }`. An outdated-but-unresolved thread keeps the state UNSTABLE; resolving it flips it to CLEAN.
- **The poll FALSE-MATCH — your own reply looks like a bot review.** Filtering `pulls/<N>/reviews` by `commit_id == <head>` ALONE matches YOU: GitHub wraps a reply to a review thread as a `COMMENTED` "review" authored by your own user on the SAME head SHA, so the poll fires seconds after you reply and reports the bot re-reviewed when it hasn't (its check is still `IN_PROGRESS`). Filter on BOTH: `user.login=="lazy-ants-reviewer[bot]"` AND `commit_id==<head>`. Then guard the jq — `[.[] | select(…)] | last` on an EMPTY array yields `{"state":null}`, a non-empty string that is not the literal `null`, so it slips a naive `[ -n "$x" ] && [ "$x" != "null" ]` test and fires the loop anyway. Test `[…] | length` and require `>= 1`.
- **Polling the bot's check-run:** a **backgrounded** `while … sleep` poll (Bash `run_in_background: true`) survives turn boundaries and re-invokes you on exit. If bg polls get killed at a turn boundary, fall back to a BOUNDED FOREGROUND loop with an extended Bash-tool `timeout` (~300000ms), or the Monitor tool — foreground `sleep` alone is blocked. Shell trap: **`status` is a read-only special variable in zsh** — `status=$(…)` dies `read-only variable: status`; name it `st`/`cc`. Re-trigger the bot after an infra crash with an empty-commit `synchronize` push; close/reopen does NOT re-fire it.
