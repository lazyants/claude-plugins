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
research result for an entry replaces its record and clears the verification; a verification
attaches only to the exact research it was built from (`research_sha256`).

Nothing hashes it: it is not part of `canon.lock.json`, a candidate's snapshot or any check, so
adding or redoing research never makes a translation stale. It is durable evidence that belongs
with `canon.json` and `canon.history.json` — the record of why a term was chosen — and
`canon.py approve` copies a summary of it into the history event of each approval.

## Approved audit context

Existing `canon.json` files need no changes. To carry an already-reviewed concept group into
audits, maintain an optional top-level `groups` list in that file through the project's normal
review. Do not infer membership from similar words or launch research to populate it. For example,
after approving both entries' German translations:

```json
{
  "id": "evidence",
  "locale": "de",
  "entry_ids": ["t-evidence", "t-evidence-based-genealogy"],
  "note": "Use Beweis for the noun; retain evidenzbasiert for the positioning adjective.",
  "scope": "Noun and positioning adjective; other marketing phrases need a scoped decision.",
  "approved_by": "<operator>",
  "approved_at": "2026-10-25T12:00:00Z"
}
```

This is one element of `groups`, alongside the existing top-level `schema` and `entries`.
Only the operator's explicit approval supplies `approved_by` and `approved_at`; model candidate
imports do not create or update groups. Review changes to membership, note or scope and update
the approval metadata before refreezing. `canon.py freeze` retains approved groups separately
from entries. Every group needs a unique ID, a locale, distinct member IDs, a note, a scope and
approval metadata; every member must be a frozen DNT entry or have an approved translation in
that locale. An incomplete approved group refuses the freeze without replacing the old lock.
If `canon.py change` encounters such a group, it also leaves the canonical file and history
unchanged and creates no restricted-audit request.
Groups without both approval fields are omitted. Entry-level decision scopes remain in `note`.

Ordinary and restricted audit packets carry `canon_context` keyed by message ID. For each message,
the seeds are the existing literal, case-sensitive source matches and DNT occurrences, plus
approved entries naming that exact message ID in `occurrences`. A group is included only when a
seed is one of its members and its locale matches the audit. Its members and scope notes travel
together; membership does not expand transitively through other groups. Unrelated groups and
unapproved translations stay out. A term's explicit occurrence supplies informational context even
when `ancestor couple` does not literally match the source `Ancestor couple`.
Occurrence entries already present in `canon` are not repeated unless they belong to a selected
group, whose members travel together with their shared note and scope.

`canon` still contains only the existing mechanical relevance selection. `canon_context` is a
separate frozen snapshot for the reviewer: it never enters checks or candidate canon hashes,
waives a failure, requires a compound phrase outside its scope, or widens a restricted audit.
Changing group notes alone therefore does not stale candidates. When the approved scope does not
settle a usage, the reviewer reports that question rather than inventing a missing decision or
duplicating an already-approved term. Packet fixtures verify this transfer; they do not certify a
model's linguistic verdict or measure translation quality.

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
