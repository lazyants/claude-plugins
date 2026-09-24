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

## Export safety — `export_values.py`

0. Exports hold an exclusive lock on `R/exports/.lock` from start to finish, so two exports
   never interleave and recovery never touches an export that is still running.
1. An unfinished export journal from an interrupted run is rolled back first.
2. The live project is collected again. Export is refused when a target changed since the last
   sync, or when the source, context or style differs from what the candidate was reviewed
   against.
3. The adapter writes into a temporary copy; the copy is collected again and must differ from
   the live project in exactly the exported values — nothing else.
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

`--dry-run` stops after step 3 and shows what would change.
