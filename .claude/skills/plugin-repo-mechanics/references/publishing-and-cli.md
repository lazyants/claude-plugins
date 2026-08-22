# Publishing: `claude plugin` CLI, config-dir trap, and the author-path scrub

## `claude plugin …` CLI + config-dir architecture → see the `multi-profile-plugins` skill

The **generalized** config-dir architecture — the per-profile plugin-store model, the "corrupted installLocation" churn and its structural fix, and the deep reverse-engineered validation/GC mechanism — now lives in the public **`multi-profile-plugins`** plugin skill (which also ships a `scripts/inspect_profiles.py` health check); read it for which `CONFIG_DIR` to target, a "corrupted installLocation" error, or why a plugin GC-deletes in one profile. **This machine's specifics** — the four profiles (`~/.claude` base + `~/.claude2`/`~/.claude3`/`~/.claude-bm`), per-profile-**independent** plugin stores since 2026-07-21 (so `claude plugin` ops are now safe & isolated from ANY profile, and the old "always `CLAUDE_CONFIG_DIR=.claude-bm`" rule and `installLocation` string-edit fix are **OBSOLETE**) — live in the user's global `CLAUDE.md`. The **publishing-specific** follow-through stays below.

## Refresh the installed copy after merge (publish ≠ ship)

The four synced surfaces + merge-to-main is the *publish*, not the *ship*. The INSTALLED plugin stays stale until you pull the marketplace cache and update the plugin — always do this last step. **Post-2026-07-21 this is PER-PROFILE** (stores are independent now): run it for each profile you want the update in, e.g. `CLAUDE_CONFIG_DIR=/Users/moi/.claude-bm claude plugin marketplace update lazyants` (for the app), then update the plugin from the `lazyants` marketplace; repeat with `CLAUDE_CONFIG_DIR=/Users/moi/.claude` (and `.claude2`/`.claude3`) for the CLI profiles you actually use. (Note: `lazyants` is the GitHub marketplace name — distinct from the `lazy-ants` directory marketplace.) The exact two-command form, the marketplace-qualified update id, and the restart caveat are in the next section.

### `plugin update` defaults to `--scope user` and reports THAT scope's result — a false green over a stale local install

`claude plugin update <id>` takes `-s/--scope {user,project,local,managed}` and **defaults to `user`**. A second install of the same plugin at `local` scope is invisible to the default invocation, and — this is the trap — the command still prints a cheerful success line describing the USER-scope entry. Run from the local install's own project directory it answers `✔ literary-translator is already at the latest version (1.26.0).` while that project's own copy sits at 1.23.0. **The failure prints exactly what success prints**, so a per-profile refresh loop can report four green updates and leave two stale installs behind.

Two consequences:

- **Enumerate scopes before believing a refresh is complete.** `claude plugin list` shows each entry with its own `Scope:` line, and a plugin installed twice appears twice — that duplicate is the only visible tell in the CLI output.
- **Verify from the registry, not the command.** `<profile>/plugins/installed_plugins.json` → `plugins["<name>@<marketplace>"]` is a LIST, one object per scope, each with its own `scope`, `version`, `installPath` and (for `local`) `projectPath`. Read the version from there after updating. This is the same rule as the section below — never from a `plugins/cache/` path glob — one level up: never from the update command's own success line either.

Fix form, once a local-scope entry is found: `cd <projectPath> && CLAUDE_CONFIG_DIR=<profile> claude plugin update <name>@<marketplace> --scope local`. The `cd` alone is NOT enough; without `--scope local` it silently re-reports the user scope.

## Read an installed version with `claude plugin list` — NEVER from a `plugins/cache/` path

`<profile>/plugins/cache/<marketplace>/<plugin>/` holds **one subdirectory per version ever
installed**, not just the active one (10–13 versioned dirs per profile on this machine). So

```
find "$d/plugins/cache/lazyants" -name plugin.json -path '*literary-translator*' | head -1
```

returns whichever versioned dir the filesystem happened to list first — a stale one, with no error
and no clue that it is stale. There is no ordering guarantee at all: nothing sorts, so the answer is
not the highest version, not the lowest, and not stable between runs.

Measured 2026-08-02 while verifying a release: that glob reported
`.claude 1.15.0 / .claude2 1.8.0 / .claude3 1.16.2 / .claude-bm 1.15.0`, and those numbers went to
the user as the basis for a decision. `claude plugin list` reported the truth: **1.16.2 in all four**.
The "1.8.0" profile did not exist; the whole "nine-minor jump, could disturb in-flight work" framing
was an artifact of `head -1`. Re-running the same glob later that day gave four *different* wrong
answers (`1.14.1 / 1.15.2 / 1.10.0 / 1.16.2`) against a true `1.17.0` everywhere — and note that
across those eight readings exactly one happened to be right, which is the trap: a glob that is
sometimes accidentally correct reads as a working accessor.

**Ask the owning accessor instead:**

```
CLAUDE_CONFIG_DIR=$d claude plugin list      # authoritative installed version
```

The id it prints is **marketplace-qualified** (`literary-translator@lazyants`), and `claude plugin
update` needs that form — an unqualified name fails with `Plugin "literary-translator" not found`.
Release refresh is therefore two steps per profile:

```
claude plugin marketplace update <marketplace>
claude plugin update <plugin>@<marketplace>
```

and the update prints `Restart to apply changes` — the running session keeps the old copy, so a
post-update `plugin list` in the same session is not evidence the new code is loaded. See
[[feedback-publish-refresh-installed]].

This is the plugin-cache instance of the standing global rule: locating an artifact by a path you
CONSTRUCTED, instead of asking for it by id, renders staleness and absence identically to fact. The
damage was not a wrong number in a log — it was a wrong state read that became a decision input the
user acted on ([[feedback-askuserquestion-cost-model]]).

## Switching a marketplace SOURCE (directory ↔ github) — EDIT the registry, never `marketplace remove`

All four profiles' `lazyants` marketplace is `source: github` (`lazyants/claude-plugins`) as of 2026-07-23. `.claude` USED to be `source: directory` pointed at the local primary checkout, which made it recurringly LAG: a remote squash-merge leaves local `main` behind `origin/main`, so a directory-source `plugin update` ships the OLD working-tree version until you fast-forward the primary first (tell: "one profile a different version than its siblings"). Switching it to github fixed the lag; the trade-off is `.claude` no longer serves uncommitted local plugin edits (repoint it back to `directory` if local-dev serving is ever wanted).

**Do the switch by hand-editing `known_marketplaces.json`, NOT via `marketplace remove` + `add`:**
- A `directory` source's `installLocation` **IS the source path** — for `.claude`'s `lazyants` that was the live repo itself — and `marketplace remove` DELETES a marketplace's `installLocation`, so on a directory-source pointing at a repo it could **delete the repo**. There is no `--force`/`--overwrite` on `add` and no `--keep-files` on `remove`.
- `remove` also uninstalls EVERY plugin sourced from that marketplace (verified: sibling `@lazyants` plugins vanished from `installed_plugins.json` + the enabled list; `settings.json` `true` entries do NOT survive, so you'd have to `claude plugin install <name>@lazyants` each one afterward).
- **Safe switch:** back up `known_marketplaces.json`; change the entry's `source` to `{"source":"github","repo":"lazyants/claude-plugins"}` and `installLocation` to `<profile>/plugins/marketplaces/lazyants` (a prior github-era clone of the repo is usually still sitting there to reuse — check `git -C <installLocation> remote -v`); then `claude plugin marketplace update lazyants` (git-pulls the clone) + `claude plugin update <plugin>@lazyants`. Hand-editing `installLocation` to an in-profile clone is safe — it matches the profile's own CONFIG_DIR prefix, so no "corrupted installLocation". This is a LEGIT source re-point, distinct from the `installLocation` string-edit-to-dodge-validation workaround called OBSOLETE above.

**If a profile drifts BACK to `directory` after being switched to github:** the tell is a version-REGRESSION message from `claude plugin update` (e.g. "updated from 1.4.0 to 1.3.0"). Restore it with the same safe edit-`known_marketplaces.json` method above (`source`→github + `installLocation`→the in-profile clone + `marketplace update`). AVOID the `remove`+`add` route: `remove` uninstalls EVERY plugin sourced from that marketplace (verified: two plugins — `enduser-handbook@lazyants` and `obsidian-project-vault@lazyants` — vanished from a profile's `installed_plugins.json` + enabled list) and, if the drifted `installLocation` happens to point at a live repo, could delete it.

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
