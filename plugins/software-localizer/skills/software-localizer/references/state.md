# Ledger states, export safety and recovery

## States — `R/ledger/<locale>.json`, one file per target locale, per message id

| state | meaning | what changes it |
| --- | --- | --- |
| `pending` | no translation yet | a translate round |
| `existing` | the project already had a translation when the plugin first saw it | `ledger.py adopt`, or an accepted audit proposal |
| `translated` | the plugin exported this value and the project still has it | a source/style change → `stale`; a change by someone else → `human_locked` |
| `stale` | the source or the locale's style changed since the plugin's translation | a translate round |
| `human_locked` | someone edited a value the plugin had exported | `ledger.py adopt`, or an accepted audit proposal |
| `escalated` | still failing after `max_rounds` | a person; it stays untranslated meanwhile |

Each locale has its own ledger file, so per-locale loops can run in parallel without touching each
other's state; `ledger.py sync` (all locales) and canon changes run in the main session.

`ledger.py sync` (after every `collect.py`) applies these transitions. For `existing` and
`human_locked` messages a changed source, context or style is only **reported**; the value is
kept. A message still waiting for a translation that suddenly has one in the project (a person
translated it meanwhile) becomes `existing`.

Every candidate records the source, context and style it was made and reviewed against; when
any of them changes, sync clears the candidate.

## Canon research evidence

`R/canon.research/<locale>.json`, one file per target locale, holds the usage research and its
verification for that locale's canon entries, keyed by entry id. Only `packets.py accept` of a
`research` or `research_verify` run writes it, and only the run's own locale file. A new
research result for an entry clears the verification; a verification attaches only to the
exact research it was built from (`research_sha256`). New packets also bind acceptance to the
entry's full stored checkpoint, so a stale packet cannot overwrite later progress.

Each answer may carry `progress: {"status": "complete" | "resource_limited", "remaining": [...]}`.
A complete bounded search can have no findings and low confidence. Resource-limited work must
list its unperformed queries or checks; default selection includes it for continuation rather
than treating it as a finished source gap. Verification waits for completed research, retains
checked indexes across partial replies and counts as verified only when complete. Saved legacy
answers without `progress` retain their former complete interpretation; arbitrary notes are
never parsed to guess a disposition.

The entry's `attempts` retains each accepted raw answer and its packet path (a legacy record is
snapshotted before its first replacement). Research continuation unions findings without
changing earlier quotes; verification continuation retains earlier checks by index. New
research packets use unused directories, preserving original packets, prompts and saved outputs.
`researched.packet` and `verification.packet` locate the latest checkpoints. The report lists
resource-limited work separately, and approval history includes explicit progress when present.
See [ordinary-turn continuation](research-continuation.md) before running the next packets.

Both research build kinds return a `diagnostics` list in their JSON response. A selected
entry with stored occurrence IDs absent from `messages.json` gets one
`unresolved_canon_occurrences` diagnostic with `entry_id`, `unresolved_ids`, `declared_count`
and `resolvable_count`. Counts cover its entire declared list, independently of the five-example
sample; a present message without a target in this locale still counts as resolvable.
Entries with no unresolved IDs produce no diagnostic. The build keeps using current examples
and leaves `ok`, `selected` and `skipped` unchanged. These are source-context warnings, separate
from current-message audit coverage: they neither invalidate accepted results nor authorize
pruning references, changing approvals or broad re-research. Packets and canon state are unchanged.

Nothing hashes it: it is not part of `canon.lock.json`, a candidate's snapshot or any check, so
adding or redoing research never makes a translation stale. It is durable evidence that belongs
with `canon.json` and `canon.history.json` — the record of why a term was chosen — and
`canon.py approve` copies a summary of it into the history event of each approval.

## Export safety — `export_values.py`

0. Exports hold an exclusive lock on `R/exports/.lock` from start to finish, so two exports
   never interleave and recovery never touches an export that is still running.
1. An unfinished export journal from an interrupted run is rolled back first.
2. The live project is collected again. Export is refused when a target changed since the last
   sync, or when the source, context or style differs from what the candidate was reviewed
   against.
3. The adapter writes into a temporary copy; the copy is collected again. The declared file list
   and collected messages must match the live project except for exactly the exported target
   values. This compares what `collect` represents, not every byte of the declared files.
4. A journal and backups (the changed project files and that locale's ledger file) are written,
   recording for each file the bytes it had and the bytes the export will write. Each project
   file is re-read and must still have the bytes it had when it was staged — an edit made
   meanwhile refuses the export; files are replaced one by one with atomic renames; the ledger
   is updated; only then is the journal marked done.
5. Rolling back — after a failure, or on the next run after an interruption — decides per file
   by its **current** bytes: still the exported bytes → the backup is restored; already the
   original bytes → nothing to do; anything else → someone edited it since, so it is left alone
   and reported as a conflict. A rollback never overwrites a person's edit.
6. A candidate also records the canon entries relevant to its message; if an approved canon entry
   changes after review, the candidate is not exported until it is checked and reviewed again.

`--dry-run` stops after step 3 and lists the changed files; it does not show their diff.

The byte-exact unchanged round trip at [adapter acceptance](adapter-contract.md#acceptance)
checks that the adapter preserves files when no values change. It does not verify every later
export. For each real export, have the team review the full project diff before committing or
shipping: this is the guard for bytes outside the collected representation, such as formatting,
comments, key order and changes to files declared only because the adapter reads them.

## Workspace files

`R` may sit inside the project, so its files fall into three groups. `scaffold.py` writes an
`R/.gitignore` that keeps only the first group in git (it never replaces an existing
`.gitignore`); add a `!/<name>` line to it to commit something else.

| group | files | in the project's repository |
| --- | --- | --- |
| decisions | `localize.json`, `adapter/`, `canon.json`, `canon.lock.json`, `canon.history.json` | committed and reviewed like any project change |
| one checkout's state | `adapter.lock.json`, `runs/_adapter_check.json`, `ledger/` | not committed |
| run state | `messages.json`, `runs/`, `exports/`, `reports/`, `project-checks/`, temporary directories, `.lz-tmp-*` files | not committed |

- `adapter.lock.json` records adapter files by absolute path, so it matches only the checkout that
  accepted it. A new checkout or worktree runs `adapter_check.py run` and `accept` again.
- A ledger describes one state of the project's catalogs, so it stays with its checkout. Losing it
  is safe: `ledger.py sync` starts every message the project already translates as `existing`, and
  nothing overwrites an `existing` value without `ledger.py adopt` or an accepted audit proposal.
  A new checkout starts without the old candidates and review progress.
- Give `project_root` as a relative path (`..` for `<project>/localization`): an absolute path
  works in one checkout only. Tool paths in `adapter.options` have the same problem; let the adapter
  compute them from its own file's location instead. Its working directory is not a reliable base:
  `collect` and `export` also run in a staged copy that holds only the declared files.
- `R` holds copies of the project's catalogs: export backups and staged copies. `collect` is
  refused when the adapter lists a file inside `R`, and the coverage inventory leaves `R` out.
  Leave `R` out of scratch copies of the project as well ([project checks](project-checks.md)).
