# Publishing: `claude plugin` CLI, config-dir trap, and the author-path scrub

## Always run `claude plugin …` with `CLAUDE_CONFIG_DIR=/Users/moi/.claude-bm`

This machine has FOUR Claude Code profiles: `~/.claude` is the real base (normal dir; `plugins/`, `memory/`, `projects/`, `settings.json` all REAL), while `~/.claude2`, `~/.claude3`, `~/.claude-bm` are profile dirs whose every entry is a **symlink back to `~/.claude/<entry>`** — the ONLY real per-profile file is `settings.json`. So the plugins store and `known_marketplaces.json` are ONE shared physical set (same inode) across all four; only the recorded path *strings* differ by which profile wrote them. The desktop **app** runs `.claude-bm`. Do NOT try to "consolidate" the config dirs — the split is deliberate.

**The break:** the app's marketplace refresh does a strict PREFIX check — `installLocation` must start with `/Users/moi/.claude-bm/plugins/marketplaces`, and it does NOT resolve symlinks. Running `claude plugin …` from a non-`.claude-bm` profile stamps that profile's prefix on whatever it touches, so the app then rejects the entry: `Marketplace 'X' has a corrupted installLocation … expected a path inside /Users/moi/.claude-bm/plugins/marketplaces`.

**FIX (surgical — do NOT remove+re-add):** `claude plugin marketplace remove <name>` would ORPHAN every plugin installed from it. Instead edit the `installLocation` STRING in `~/.claude/plugins/known_marketplaces.json` from `.claude2`/`.claude3`/`.claude` → `.claude-bm` (same physical dir, zero data movement). Back up first, then `python3 -m json.tool <file>` to validate. Verify by reproducing the app env: `CLAUDE_CONFIG_DIR=/Users/moi/.claude-bm claude plugin marketplace update <name>` must succeed. (The `installPath` strings in `installed_plugins.json` are NOT strictly validated, so only the marketplace `installLocation` needs fixing.)

**It RECURS — the fix is not durable.** Because `known_marketplaces.json` is ONE shared inode but each profile prefix-checks it against its OWN `CLAUDE_CONFIG_DIR`, only ONE prefix can be stored at a time; ANY `claude plugin` op (or auto-refresh) from a non-`.claude-bm` profile re-stamps that prefix and re-breaks the app view. The recurring fix stays the one-line string edit above. Durable-fix candidates need USER OK first (structural change to a deliberate shared-data setup — do NOT do unilaterally): de-symlink `known_marketplaces.json` alone so each profile holds its own real copy with its own prefix (each still resolves through its own `plugins/` symlink to the same shared marketplace clones, so all four validate simultaneously; cost: adding a marketplace then means updating all four copies); or a launchd/session-start normalizer that rewrites the prefix to `.claude-bm`.

## Refresh the installed copy after merge (publish ≠ ship)

The four synced surfaces + merge-to-main is the *publish*, not the *ship*. The INSTALLED plugin stays stale until you pull the marketplace cache and update the plugin — always do this last step, run with `CLAUDE_CONFIG_DIR=/Users/moi/.claude-bm`: `claude plugin marketplace update lazyants`, then update the plugin from the `lazyants` marketplace. (Note: `lazyants` is the GitHub marketplace name — distinct from the `lazy-ants` directory marketplace.)

## Scrub author-local paths before publishing a generalized plugin

This repo repeatedly GENERALIZES an in-house/private project into a public marketplace plugin (`enduser-handbook` ← vpp/plattform; `literary-translator` ← historiettes-t3). The shipped skill + reference docs then tend to carry TWO artifacts that neither the `lazy-ants-reviewer` bot nor the code reviewers catch (they review CODE, not doc prose):

1. **Absolute author-local paths** hardcoded in docs (e.g. `/Users/moi/lazy-ants/development/historiettes-t3/...`). This leaks the author's OS username into a PUBLIC plugin AND contradicts the plugin's own self-anchored-path house style (`Path(__file__).resolve().parents[1]`, never absolute).
2. **"Read the real file directly for ground truth"** instructions pointing at that non-shipped origin — a directive an installed user cannot follow (the path doesn't exist on their machine).

**Preflight before publishing / reviewing any generalized plugin:**

- `grep -rn "/Users/\|/home/" <plugin>/` → scrub EVERY absolute local path (also catches the username leak).
- `grep -rn "<origin-project-name>" <plugin>/` → relabel the origin as the "in-house / private provenance project, not shipped with the plugin", and drop/soften any "read/open the real file" directive (gate it behind "if you have access to the source project"). KEEP the provenance INTENT (it's the battle-tested source) — just without the unreachable path/instruction.

These are AUTHORING-quality defects, not code — a skill-authoring audit (plugin-validator + skill-reviewer + a completeness lens, run as a multi-lens adversarial pass) catches them where the code/security bots pass clean.

**Do NOT scrub the acceptable cases the grep will also hit:** naming the provenance project as attribution ("Generalized from historiettes-t3's final_audit.py") is fine, and a "read directly there" pointer at a SHIPPED sibling script (e.g. `validate_draft.py` for its single-source constant) is fine. Only absolute paths + read-a-NON-shipped-file directives are defects.

**Re-grep the INCREMENT at MERGE time — a scrub on `main` doesn't protect a stale branch.** A feature branch built off a base that predates the scrub would RE-INTRODUCE the leaks on merge, and the increment's own new files add FRESH ones, so run the preflight grep on the increment again at merge time, not just once at first publish. Fresh flavors to catch: an absolute path inherited from a pre-scrub base surviving into a re-touched doc (the rebase conflict resolution must take main's scrubbed version, not the stale one); a NEW script docstring with the read-a-non-shipped-file directive; and internal DESIGN-DOC cites (e.g. `CONTRACT_lt_assemble.md §X`) in shipped TEST docstrings — a non-shipped scratchpad doc a user can't open; replace with the SHIPPED spec (SKILL.md section + `references/…`), don't cite the design doc. Keep the merge SCOPED: fix the increment's own files; residual pre-existing instances go to a follow-up, not this feature PR.
