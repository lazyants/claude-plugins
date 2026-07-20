# Isolating a subset commit; git-state safety in a shared tree

## Commit ONLY your change to a file that already carries unrelated churn

When a file has pending unrelated uncommitted edits (someone else's WIP, a queued removal elsewhere) but you need just your change committed as a clean isolated commit:

1. `git stash push -- <path>` — reverts THAT ONE file to HEAD, leaving all other churn (other files, untracked) untouched.
2. Make your change to the now-clean file → `git add <path>` → commit (contains only your change, on top of HEAD).
3. `git stash pop` — re-applies the stashed churn on top via a 3-way auto-merge; clean when your change and the churn don't overlap (e.g. append at array end vs. a removal elsewhere).
4. VERIFY: `git show --stat <sha>` touched only intended files; working tree still holds exactly the original churn (`git status --short`).

Cleaner than `git add -p` when scripted — interactive git isn't available in this env. Prefer this over `git add -A`; commit only your own files.

## Never run git-state ops concurrently with active subagents in the SHARED cwd

Teammates and Workflow agents share the working tree — worktree isolation does NOT isolate the main tree. Running `git status` / `git add --dry-run` while agents do their own `git` inspection returns transient GARBAGE (observed: `--dry-run` reported 2 of 92 files; a committed file showed as untracked `??`). It settles to correct once agents go idle.

- WAIT for subagents to go idle before staging/committing/inspecting.
- Re-check with an explicit `git -C <repo> status` after they finish — the snapshot mid-run is a race, not the truth.

## Teammates can trigger the same hazard against EACH OTHER

Branches/stashes/the working tree are repo-global, not per-teammate. A teammate's "clean revert of my own edit" (`git checkout --`, `git stash`, `git reset` on a file it believes is "just mine") can revert a DIFFERENT teammate's uncommitted work living in the same file. Brief teammates explicitly:

- Never `git checkout --` / `git stash` / `git reset` a file in a shared worktree without checking `git status` immediately BEFORE and AFTER — even for "my own" file.
- Prefer a **scratchpad copy of the diff** (or `git stash push -- <path>` then `apply`, never a bare `pop`) over any full-file revert as the first move when experimenting.
- Recovery when it happens: a pre-saved patch + `git apply`, or `git stash apply <sha>` + drop (never bare `pop`) — but this only works because the teammate self-checked `git status`/`git diff` immediately after.

## The `cp -i` alias makes a "restore the backup" command silently no-op

This shell aliases `cp` to `cp -i`. `cp backup.py real.py` (intending a plain overwrite) silently prints `overwrite … ? (y/n [n])` and does NOT overwrite. Passing `-f` (`cp -f backup.py real.py`) does **not** override the alias-injected `-i` either. The command returns exit 0, so "no error = it worked" leaves the file in the WRONG state with no signal.

- Never trust a `cp`/`mv`/`rm` restore from exit code alone in an unfamiliar shell — verify the target file's content directly afterward (`grep` your expected symbols / diff).
- For a guaranteed-non-interactive restore, bypass the alias entirely: `/bin/cp -f`, or `python3 -c "open(a,'wb').write(open(b,'rb').read())"` (no shell alias can intercept a stdlib call).

## `git -C <dir>` does NOT scope a redirect — the path it prints may be relative

`git -C <worktree> rev-parse --git-path info/exclude` prints an ABSOLUTE path in a linked
worktree and a RELATIVE one (`.git/info/exclude`) in an ordinary checkout. `-C` changes git's
working directory, not the shell's, so a `>>` redirect on that output resolves in the CALLER's
cwd — the write escapes the worktree you carefully scoped the command to.

Fix: `git -C <dir> rev-parse --path-format=absolute --git-path info/exclude`. Same for any
`rev-parse` output you feed to a shell redirect, `cd`, or another tool.

**The dangerous part is the asymmetry, not the flag.** The relative form appears only in an
ordinary checkout, so a test run from a linked worktree passes and hides it — which is exactly
how a reviewer-requested fix for this shipped still-broken (2026-07-20, #263: `-C` was added to
both calls, verified in a worktree, and the ordinary-checkout case was never exercised until the
bot ran both). Generalizes past git: **when two environments differ in the property under test,
the one you happen to be standing in is the one that proves nothing.** Pick the discriminating
case deliberately, or run both.

Verify exclusion with `git -C <dir> check-ignore -q <path>` rather than `git status --short` —
status cannot be empty on an already-dirty tree, so it proves nothing where it is most likely
to be used.
