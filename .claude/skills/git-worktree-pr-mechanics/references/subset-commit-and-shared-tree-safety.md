# Isolating a subset commit; git-state safety in a shared tree

## Commit ONLY your change to a file that already carries unrelated churn

When a file has pending unrelated uncommitted edits (someone else's WIP, a queued removal elsewhere) but you need just your change committed as a clean isolated commit:

0. **GUARD FIRST — abort if any of that path is STAGED:** `git diff --cached --quiet -- <path>`. A non-zero exit means a peer has staged content there; **stop**. Step 2 discards the index *and* the worktree copy, so a staged edit is unrecoverable from a worktree-only patch — and a staged file means someone is mid-commit, which is the worst moment to be committing that path yourself.
1. `git diff HEAD -- <path> > <scratch>/churn.patch` — capture the churn as a patch OUTSIDE the repo. Use `HEAD`, not a bare `git diff`: a bare `git diff` is the worktree-vs-INDEX delta, so it silently omits anything already staged. Confirm the patch is non-empty (`wc -c`) before continuing.
2. `git checkout HEAD -- <path>` — reverts THAT ONE file to HEAD, leaving all other churn (other files, untracked) untouched. Safe only because steps 0–1 proved nothing was staged and saved what was there.
3. Make your change to the now-clean file → `git add <path>` → commit (contains only your change, on top of HEAD).
4. `git apply --3way <scratch>/churn.patch` — re-applies the churn on top; `--3way` gives the same forgiving auto-merge `stash pop` had, so it stays clean when your change and the churn don't overlap (e.g. append at array end vs. a removal elsewhere) and leaves ordinary conflict markers when they do.
5. `git restore --staged -- <path>` — `--3way` implies `--index`, so step 4 leaves the restored churn STAGED; this returns it to unstaged, which is the state the peer actually had.
6. VERIFY: `git show --stat <sha>` touched only intended files; `git status --short <path>` reads ` M` (unstaged), matching the peer's original state, and the churn content is back.

**Use a patch file, NOT `git stash`, and never a bare `git stash pop`.** The stash stack is repo-global and shared: if the `push` in a stash-based version of this recipe fails (a malformed/non-matching pathspec is the common way), the stack is left untouched and the following `pop` silently applies — and **drops** — a *peer's* entry. Verified 2026-07-25 in a scratch repo: with one foreign entry on the stack, `git stash push -- '<non-matching pathspec>'` exits 1 and stashes nothing, then a bare `git stash pop` **exits 0**, merges the peer's WIP into the working tree, and prints `Dropped refs/stash@{0}` — destroying the peer's only copy while reporting success. A patch file has no shared stack to collide with, so the failure is structurally unreachable. If some situation genuinely forces stash, capture the created entry's SHA (`git rev-parse -q --verify stash@{0}` before AND after the push — an unchanged value means the push created nothing, so ABORT) and later `git stash apply <sha>` + `git stash drop <sha>`; never bare `pop`. See the section below for the full hazard write-up.

**The step-0 guard is the load-bearing part, not a formality.** Verified 2026-07-25: with a peer holding a staged edit on line 1 and an unstaged edit on line 30 of the same file, an unguarded `git diff`-based version of this recipe captured only the unstaged delta, `git checkout HEAD --` then discarded both, and re-applying the patch failed outright (`patch does not apply` — its context assumed the staged line-1 edit was present), leaving the file fully reverted to HEAD: **both** peer edits lost, one of them silently. If you genuinely must proceed with staged peer content present, do not use the single-patch flow — capture the two deltas separately (`git diff --cached -- <path>` and `git diff -- <path>`), and restore with `git apply --index <staged>.patch` followed by `git apply <unstaged>.patch` so the index-vs-worktree split survives.

Cleaner than `git add -p` when scripted — interactive git isn't available in this env. Prefer this over `git add -A`; commit only your own files.

## `git commit` commits the whole INDEX — DISJOINT files are not enough, use `git commit -- <pathspec>`

The section above guards a peer staging content on the SAME path. This one needs no path overlap at
all, so that guard never fires. `git add <my files>` followed by a bare `git commit` commits the
entire index, including whatever a peer staged in the window between your two commands. Two agents
editing completely disjoint files still collide, because the index — not just the worktree — is the
shared surface.

**Fix: `git commit -- <pathspec>`.** With a pathspec, git commits from the WORKING TREE for those
paths and ignores the rest of the index, so the race is structurally unreachable. Make it the house
rule for any tree with concurrent agents; `git add` + `git commit` is the unsafe pair.

Verified 2026-07-25 (literary-translator 1.16.0, three agents, one worktree). One agent staged four
reference docs, confirmed `git diff --cached` showed exactly those four, and its `git commit` took
five — a teammate had staged a template in between. Nothing in its own pathspec protected it.

**The recovery leaves a second, quieter hazard.** Splitting the commit back out (`git reset --soft`,
`git restore --staged` the foreign path) is content-safe — verify by sha256 both sides — but it
returns the peer's work to UNSTAGED, a different state from the one they left it in. That peer was
mid-verification, read a sha during the window, and reported its work as committed. It was not: the
work sat uncommitted in the tree while HEAD still carried the stale text it had just corrected, one
`git checkout` from destruction. So: after any index surgery in a shared tree, TELL the owner what
state their work is now in — and treat any git state a peer reports from a shared tree as a snapshot
of a race, re-reading it yourself before acting on it.

## A failed pathspec-scoped `stash push` leaves the stack unchanged — the next `pop` grabs a foreign entry

Verified 2026-07-20 (literary-translator 1.11.0, three parallel sessions sharing one worktree). A teammate ran `git stash push -- <paths> -m "..."` with a malformed pathspec. It errored and stashed **nothing** — the stack was untouched. The teammate then ran `git stash pop` expecting to restore its own work, and instead popped `stash@{0}` — an unrelated entry from a **different branch and a different session** — producing `UU` merge conflicts in three files it did not own.

Two things make this dangerous: (1) `stash push` failing is not loud enough to stop a scripted `pop` that follows it — the pair reads as symmetric, it isn't; nothing ties a `pop` to the entry your `push` created. (2) In a shared worktree the stash stack is global and long-lived — that machine had three stashes from three unrelated branches/sessions going back weeks, so `stash@{0}` meant "whatever some other session left there", not "my last push".

- **Never use stash as an undo in a shared tree.** To restore one file to committed state: `git show HEAD:<path> > <path>` — scoped, no stack involvement, no risk to a peer.
- If you truly must stash, check `git stash list` before AND after the push and confirm your own entry appeared; never `pop` on faith.
- Recovery is clean if caught immediately: `git checkout HEAD -- <the affected paths>` discards the foreign application, and the original stash entry stays intact on the stack (verify with `git stash list` — nothing should have been dropped).

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
