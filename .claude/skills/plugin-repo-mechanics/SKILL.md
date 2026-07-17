---
name: plugin-repo-mechanics
description: Publishing and maintenance mechanics for the lazy-ants claude-plugins marketplace repo — use when bumping a plugin's version (the four synced surfaces — plugin.json, marketplace.json, CHANGELOG, README row/header/anchor/prose), adding a new plugin or a new ai-cli-optout vendor, verifying a squash-merge or stacked PR actually landed on origin/main, predicting or resolving parallel-PR conflicts on the shared README table + marketplace.json, handling the lazy-ants-reviewer bot before merge, running `claude plugin` CLI ops (the CLAUDE_CONFIG_DIR=.claude-bm / installLocation symlink trap), refreshing the installed copy after merge, or scrubbing author-local paths before publishing a generalized plugin.
---

Publishing and maintenance mechanics for THIS claude-plugins marketplace repo. Two cross-cutting rules apply everywhere below:

- **Verify from `origin/main`, never from a tool's success output.** A merge command's changed-file list, a commit message's "N surfaces synced" claim, and a green local suite are all NOT proof. Re-grep the actual value on `origin/main`.
- **Worktree teardown / branch-revival / parallel-session-race mechanics live in the `git-worktree-pr-mechanics` skill** — this skill points there rather than duplicating them.

Read the reference file that matches the task:

- **`references/version-and-surface-sync.md`** — read when bumping a plugin's version, adding a new plugin, or adding/renaming an `ai-cli-optout` vendor. Covers the four synced surfaces (plugin.json, marketplace.json, CHANGELOG, README row/header/anchor/prose), the README anchor-slug gotcha, the repo-level `metadata.version` +0.1.0 rule for a NEW plugin, and the vendor-add README sweep.
- **`references/merge-and-review-bot.md`** — read when merging a PR, verifying a squash landed the right content, predicting or resolving a parallel-PR conflict on the shared registration surfaces, dealing with a stacked PR or a stale-base branch, auto-closing multiple issues, auditing what's genuinely unmerged, or handling the `lazy-ants-reviewer` bot.
- **`references/publishing-and-cli.md`** — read when running any `claude plugin …` CLI op, fixing a "corrupted installLocation" rejection, refreshing the installed copy after merge, or scrubbing author-local paths / non-shipped-file directives before publishing a plugin generalized from a private project.
