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
verification for that locale's canon entries, keyed by entry id. `packets.py accept` of a
`research` or `research_verify` run writes only the run's own locale file. `canon.py change-note`
also marks affected records stale across locales, retaining the original evidence. A new
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
If `canon.py change` or `change-scope` encounters such a group, it also leaves the canonical
file and history unchanged and creates no restricted-audit request.
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

## Reviewed DNT scope decisions

For an approved DNT in `canon.json`, `occurrences` is the stored union and
`approved_occurrences` is the active scope copied into `canon.lock.json`. The difference is
pending import proposals. Existing approved entries without the snapshot retain their prior
scope; the next import captures it before merging additions. Initial `approve` snapshots the
whole proposed scope. Imports remain non-destructive and never approve an expansion.

`canon.py scope` reports active/pending ids and a hash of the entire current entry.
`change-scope` requires that hash, the person's name/reason and exact add/remove ids. It appends
the before/after active and stored scopes, deltas, hashes, affected ids, locales and decision to
`canon.history.json`; `scope_approved_by`/`scope_approved_at` identify the latest scope decision
without overwriting the original entry approval. A stale expected hash refuses before writing.
Source spelling, notes, translations, sibling entries and research files are unchanged.

An active change updates the frozen entry hash and writes
`runs/<locale>/canon-audit-<entry>.json` for every configured target locale. These requests cover
the old/new scope union plus source-text matches, because the shared entry hash also makes
their candidate reviews stale. Deleted ids remain in requests so the packet builder reports
them, along with missing target values, as skipped. A request is work to do, not proof that an
audit ran. Build/accept each restricted audit and run fresh checks/review after ledger sync;
export's existing hash guard still refuses a stale candidate. A pending-only rejection changes
history and the editable entry hash but leaves the active lock unchanged and requests no audit.

Keep canon operations serialized in the coordinating session, as for locale wording changes.
The canon, lock and history are separate atomic file replacements, not a multi-file transaction:
after an interrupted operation compare their readbacks with the appended decision before
continuing; refreeze the saved canon and rebuild any missing audit requests from that event's
`affected_occurrences`/`target_locales`, retaining its `scope_change` before/after context.
Never invent or infer a person's approval to repair an interrupted decision.

## Shared-note corrections

`canon.py change-note` corrects an approved entry's common semantic note with an exact `--expect`,
replacement `--note`, nonblank `--reason` and `--by`. All note edits take the semantic path; there
is no editorial exemption. Locale wording/approval records, DNT names/occurrences, other canon
entries and unrelated ledger entries stay unchanged.
An explicitly cleared note stays empty on later imports; untouched empty notes retain the
existing fill-on-import behavior. A later locale-wording `change` preserves the semantic audit's
scope and records its own wording change alongside it, archiving the replaced request in history.
Note and DNT-scope decisions also retain each other's pending audit context and affected IDs.
For a DNT, the correction uses its active approved scope; pending import proposals stay pending.
An unchanged before/after scope snapshot keeps literal matches outside active scope informational.

The history event records exact before/after notes, actor/reason/time, a chained `note_revision`,
affected locales/messages, and snapshots of prior research/verification, affected ledger entries
and any replaced restricted-audit requests. Existing run artifacts stay in place. Research records
keep their evidence/verdicts with a `stale_note` marker; summaries and reports no longer count
those verdicts as verified. Re-research can replace the current record without losing the evidence
archived in history. Fresh verification requires fresh research under the corrected note; neither
is launched automatically. DNT entries are reassessed through audit, not usage research.
Resource-limited work for a stale note is excluded from default continuation. An explicitly
scoped fresh research packet does not resume or union findings for the old meaning.

The revision is included in the frozen entry/hash, even when a later correction restores an old
note's text. Affected candidates and proposals are cleared; their old state remains in history.
For explicit occurrences or approved locale-scoped groups without a literal term match,
`canon.note_context` carries the corrected meaning and group scopes per message through translation,
audit and subsequent review packets. These corrected-note dependencies participate in the candidate's
locale-specific canon hash; ordinary uncorrected informational context retains its previous hash
behavior. Mechanical enforcement still reads only `canon.entries`. A correction reports affected IDs
per locale, so a group in one language does not invalidate another language's unrelated candidates.
Pre-correction research/verification and translation/review/audit packets are refused. Fresh
packets with corrected entries require an exact top-level `note_revisions` echo in their answers,
so rebuilding a packet in the same directory cannot relabel an old answer as a fresh turn.

`runs/<locale>/canon-audit-<entry>.json` identifies the note correction (`field: "note"`), the
explicit occurrences plus messages matched by the lock's relevance rule, and the fresh audit
required in each configured target locale. Build it with `packets.py build --kind audit --locale L
--entry ID`; the prompt distinguishes semantic notes from replacement translations. A request is
an input to reassessment, not evidence that an audit ran or passed. Run canon mutations serially
in the coordinator with locale workers stopped. These files are written separately, with history
and invalidations before the new canon/lock; interruption can leave conservative invalidations
or a pending request and requires inspection/retry, not a claim of an atomic transaction.

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
