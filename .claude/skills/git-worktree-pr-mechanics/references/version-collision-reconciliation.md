# Version-collision reconciliation across parallel worktrees

When several `/goal-la`-style sessions fan out across worktrees, each computes "next version" locally from whatever `plugin.json` says on its own branch — with no visibility into sibling worktrees or their in-flight plans. So parallel sessions all deterministically converge on the same "obvious next" number whenever they forked from the same pre-bump main. Whichever lands on `main` first legitimately claims that number; every other one must notice and re-number before it can merge.

## Contents
- Detect a sibling before you finalize a bump
- Proactive cede vs. reactive rebase-and-renumber
- The version-string auto-merge trap (same plugin, same N)
- Cross-plugin race (peer bumps a *different* plugin)
- The reconciliation mechanic (stash → rebase → apply → stack → bump → verify)
- Layering CHANGELOG/README entries; peer structural refactors
- Shared-main-tree contamination recovery
- A race that lands after your PR is already open
- Completing the merge

## Detect a sibling before you finalize a bump

- Check `find .claude/worktrees -maxdepth 1` (or `git worktree list`) for sibling worktrees — not just `git log` on the current branch. A sibling can be holding the exact same "next" version number in an unmerged, possibly abandoned plan.
- Re-check current `main`'s actual `plugin.json` version and `CHANGELOG.md` immediately BEFORE applying the bump, not just at plan time (the plan-time number is provisional). Whichever effort merges first claims that version; the others re-number at merge time.
- You don't have to rely on a proactive manual check: any reviewer that reads the release-surface files (`plugin.json`/`marketplace.json`/`CHANGELOG`/`README`) as part of its normal diff scope will independently surface a collision (e.g. `main`'s `plugin.json` already at your target with a different CHANGELOG entry). Treat that as a real finding.

## Proactive cede vs. reactive rebase-and-renumber

**Proactive cede beats reactive rebase** when you can see the sibling is ahead. Before your first commit, run `git worktree list` + `git show <sibling-branch>:<path>/plugin.json`. If the sibling is already an open, ahead, MERGEABLE PR at your target N, pre-bump your WHOLE branch to N+1 *before* the first commit — a scoped `perl -i` of `N→N+1` (e.g. `1.3.4→1.3.5`) plus the anchor form (`v134→v135`) across ONLY your changed files. Safe because N+1 doesn't exist on origin yet, so every `N` in your diff is yours. This avoids committing a doomed N and redoing it.

The reactive path (rebase-and-renumber after the peer merges mid-flow) is below; you often still need it even after a proactive cede if the peer merges *during* your review loop.

## The version-string auto-merge trap (same plugin, same N)

When a peer ships version N mid-flow and your branch ALSO bumped to the IDENTICAL N, `git rebase`/`merge` silently 3-way-merges the single-line version files (`plugin.json`, `marketplace.json`, README row+anchor+header) to N with NO conflict — both sides wrote byte-identical "N", so git sees no disagreement. ONLY `CHANGELOG.md` conflicts (the two N-sections differ).

The trap: you resolve just the CHANGELOG, `rebase --continue`, and ship a branch whose 4 version surfaces still read the COLLIDING N while only the CHANGELOG says N+1. After resolving the CHANGELOG to `## N+1` (yours) above `## N` (peer's), you MUST **manually bump the 4 auto-merged surfaces to N+1** and `git commit --amend` the subject N→N+1. Verify:
- `git rev-list --left-right --count origin/main...HEAD` reads `0	1` (your branch is exactly one commit ahead, zero behind).
- A full-suite re-run on the COMBINED tree is green (the rebase pulled in the peer's code + tests too; the pass count is yours + theirs).

## Cross-plugin race (peer bumps a *different* plugin)

When the racing peer bumps a DIFFERENT plugin, `marketplace.json` auto-merges cleanly (the two plugins' entries are dozens of lines apart) and the ONLY conflict is the **adjacent README plugin-table rows** — every plugin sits on a consecutive `| plugin | version |` line, so the peer's row edit and yours are adjacent hunks. Resolve by keeping BOTH updated rows (peer's at its new version + yours at yours); the per-plugin `## <name> — vN` section headers are far apart and auto-merge. Confirm YOUR plugin content is byte-identical across the rebase — `git diff <pre> <post> -- plugins/<name>/` must be empty (the only delta may be the shared-registration merge) — before `--force-with-lease`.

## The reconciliation mechanic

Worked cleanly repeatedly:

1. `git stash push -u -m "<tag>"` the uncommitted WIP (or commit your work FIRST on the feature branch to create a clean merge-base — don't merge origin/main into uncommitted changes).
2. `git rebase origin/main` (a clean fast-forward when no commits exist yet on the branch, just uncommitted changes), or `git merge origin/main`.
3. `git stash apply <captured-sha>` — **`apply`, not `pop`** (so a failed apply leaves the stash intact).
4. Resolve the conflicts — localized to exactly the shared release surfaces (`plugin.json`, `marketplace.json`, `CHANGELOG.md`, `README.md`); code/doc files auto-merge clean when touch-sets are disjoint. For the two single-line JSON version files, `git checkout --theirs` (or `git checkout origin/main -- <file>`) then verify each differs from `origin/main` by ONLY the version line (no sibling content dropped). Resolve CHANGELOG/README with a small `python3` script (see next section) — never a manual 3-way text merge of two feature-note bodies.
5. Bump ALL FOUR version surfaces to the next free number (`plugin.json`, `marketplace.json`, both README locations: row+anchor and section header).
6. Re-run the FULL clean-venv suite on the merged/rebased COMBINED tree (not just your own files) — it should show BOTH clusters' gate counts combined, proving the merge didn't shadow either side's assertions.
7. Verify nothing was silently dropped: `git diff --cached --name-only` shows only your intended files, and a tree-hash comparison (`git rev-parse <mergecommit>^{tree}`) against the pre-push state. Then `git push --force-with-lease`.

## Layering CHANGELOG/README entries; peer structural refactors

- CHANGELOG: stack your new version's entry ABOVE the newer already-merged entry (never below/overwriting it) via a small Python script with an `assert count >= N` guard before every string replacement (a plain `sed`/Edit risks silently matching zero times).
- README historically needed BOTH the cumulative body-PROSE paragraph AND the "what shipped" bullet to carry both versions' sentences stacked (the header/row-vs-body-prose sync gap — see the plugin-version-sync reference for the four-surface checklist).
- **When the peer STRUCTURALLY refactored the conflict file, resolve TOWARD the peer's new structure — do not reconstruct your side's old layout.** If a docs peer reflowed the README section to a new house style (e.g. deleting the cumulative version-log paragraph and relocating history into version-free bullets), take `git show origin/main:README.md` as the base and re-apply only your minimal delta IN THEIR NEW STYLE (bump the row version+anchor, bump the section header, add one bullet). Re-appending your version-log sentence would restore exactly what the peer intentionally removed.

## Shared-main-tree contamination recovery

A concurrent session whose cwd is the SAME main dir can have its edits appear IN your tree mid-run — files no teammate of yours owns. Tells: a modified file no teammate owns with an mtime after your teammates finished; its diff references OTHER issues; the tree is a MOVING TARGET (a file unmodified at one check, modified minutes later), so the same suite gives different failures each run.

- Do NOT `git stash` to "clean up" — it would pocket and clobber the other session's uncommitted work.
- Attribute by CONTENT, not filename: with two sessions in one tree, `git status` filename attribution is unreliable. Grep YOUR change's symbols and check `git show HEAD:<file>` before calling a "foreign-looking" file the peer's pollution — a combined file (e.g. estimator + glossary tests) may hold original content that only looks foreign.

**Recovery = patch-to-clean-worktree isolation:**
1. `git diff origin/main -- <only MY files>` → a patch that by construction EXCLUDES the contamination. List your paths explicitly on ONE command line; do NOT use `git add -A`/stash. (Gotcha: a multi-line shell var with backslash-continuations inside double quotes keeps the literal backslashes → mangled paths → empty patch.) Copy any new untracked files aside — and remember the `cp -i` alias silently declines overwrites, so use `/bin/cp -f` and `grep` your symbols to confirm the copy landed.
2. `git worktree add <clean> -b <new-branch> origin/main` — a fresh, contamination-free base (a NEW branch name sidesteps the two-worktrees-same-branch rule).
3. `git apply --index` the patch + copy the new files in.
4. Do ALL remaining work there (full suite = definitive green, no moving target; review loop; four-surface version-sync; path-scoped commit; push; PR) — never touching the contaminated main dir again.
5. In the shared tree, restore ONLY your files with a targeted `git checkout -- <paths>` — never `-A`/reset (leave co-edited files alone).

Note: `/security-review`'s automatic git-status extraction reads the MAIN dir and comes back EMPTY under worktree isolation — review the clean-worktree diff manually, or point Agent-based reviewers at the worktree path explicitly.

## A race that lands after your PR is already open

A race can land AFTER your PR is open and bot-reviewed (not just pre-commit/pre-PR): amend your fix into the single commit → `git rebase origin/main` → resolve to house style → re-run the COMBINED suite → `git push --force-with-lease`. GitHub flips the PR to `CONFLICTING`/`DIRTY` until the rebase, then back to `MERGEABLE`/`CLEAN`.

## Completing the merge

- `gh pr merge --squash --delete-branch` can print `fatal: 'main' is already used by worktree` from its LOCAL branch-cleanup step while the MERGE ITSELF SUCCEEDS. Verify with `gh pr view <n> --json state,mergedAt`, then `git show origin/main:<path>/plugin.json`; delete the remote branch separately with `git push origin --delete <br>`. Guard a squash against a stale head with `gh pr merge --match-head-commit <SHA>` and confirm `git show origin/main:<file>` shipped your validated head.
- `mergeStateStatus` flipping CLEAN→UNSTABLE from a non-blocking workflow (Dependency Graph / pip) is normal — still `mergeable=MERGEABLE`.
- The `lazy-ants-reviewer` bot reviews on PR-open and re-reviews on a force-push whose head content SUBSTANTIVELY changed; a pure byte-identical rebase force-push does NOT re-trigger it. Merging on a standing approval after a byte-identical rebase is safe iff the new head's plugin content is byte-identical to the approved head AND `reviewDecision` is empty.

(For the general self-authored-public-merge authorization gate and stale-head squash-verify discipline, see the PR/merge-mechanics and pr-squash-merge references.)
