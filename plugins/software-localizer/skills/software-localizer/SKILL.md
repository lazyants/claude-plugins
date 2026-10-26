---
name: software-localizer
description: >
  Translate a software project's user-interface strings into another language, or audit the
  translation it already has, with literary-translator's discipline: a canon and glossary
  decided once so every term and UI label is named the same way everywhere, translation in
  small batches (codex) through script checks and a Claude review, a resumable ledger that never
  overwrites a person's edit, and a report for a native speaker. How strings are collected from
  the project and exported back is a per-project adapter written when the skill is used,
  accepted only after mechanical round-trip and coverage checks. Use when the user says
  "localize this project", "translate the app into German", "check our Russian translation",
  "audit the translations", "add a language to this project", or "resume the localization".
  Not for extracting hard-coded strings out of code, and not for translating books (use
  literary-translator).
---

# software-localizer

Translate a project's UI strings into another language, or audit an existing translation. The
plugin ships a **fixed core** — canon, checks, model turns, ledger, export, report — that works
on one neutral message format. Everything format-specific lives in a **per-project adapter** you
write with the operator at the start (step 2): it collects the strings, exports translations
back, and parses message syntax, ideally by calling the project's own tooling.

Scripts live in `${CLAUDE_PLUGIN_ROOT}/skills/software-localizer/scripts/` (call it `S` below).
Every script prints one JSON line and exits `0` (ok), `1` (refused — read the message and fix
what it names), or `2` (cannot run). The workspace `R` is a directory outside the project or
inside it — for example `<project>/localization` with `"project_root": ".."` — but never the
project root itself or a directory that contains the project. Inside the project, commit what
`R/.gitignore` keeps (the config, the adapter and the canon) through the project's normal review;
the rest of `R` is one checkout's run state ([workspace files](references/state.md#workspace-files)).
References: `references/adapter-contract.md` (what an adapter must do),
`references/checks.md` (what scripts check and what the review judges),
`references/project-checks.md` (project tests before approval and export),
`references/state.md` (ledger states, canon research evidence, export safety, recovery,
workspace files).

`packets.py build` returns each batch's `dir`: use that path for the model turn, its answer and
`packets.py accept --run`. Batches live in `R/runs/<locale>/_packets/<kind>/<batch>` (canon uses
`_canon` as its locale), so building a review preserves the audit's packet, prompt and outputs.
Repeated builds of the same kind may reuse their directory for fix rounds. Older run
directories stay in place and can still be passed to `accept` with their own answers.
The reserved `_packets` directory keeps new runs separate from legacy batch names.

**Native Claude turns require verified transport.** For canon, research,
research verification, audit and review, submit the generated ASCII `request.json`
as the complete native `Agent` arguments, or pipe `prompt.md` directly to a fresh
CLI child with the packaged web-only agent. Prefer CLI stdin when host hooks
rewrite Agent prompts. Capture a receipt from the actual session and returned
result before `packets.py accept`. The parent does all file operations;
research children have only web tools.
Follow [packet handoff](references/packet-handoff.md) for the executable commands,
supported log format and failed-attempt recovery. A packet file or value hash
alone does not prove what the worker saw.

## Hard rules

1. **Nothing runs on defaults.** `localize.json` ships `CHOOSE_` sentinels; `config_validate.py`
   must exit 0 first.
2. **The model never writes a project file.** Codex runs read-only and returns JSON; a script
   checks it and the adapter writes it.
3. **No value reaches the live project without passing the script checks and a review verdict bound
   to that exact value.** A value changed after review is a new value.
4. **A person's translation is never overwritten.** Existing translations start `existing`;
   an edit the plugin did not make turns a message `human_locked`. Only `ledger.py adopt` or an
   accepted audit proposal changes that.
5. **Refuse, don't degrade.** A message still failing after `max_rounds` is `escalated` and left
   untranslated, listed in the report.
6. **The canon is decided once.** Use `canon.py change` for approved locale wording and
   `canon.py change-note` for a shared semantic note. Both require a person's decision,
   record who and why, and request an audit of that entry.
7. **Project checks precede approval and export.** Test proposed values in a scratch project
   before asking a person to accept audit fixes, and test the final reviewed values again before
   live export. A model verdict or an export dry run does not run the project's tests.
8. **A valid fragment can still need a project change.** When a locale needs different boundary
   whitespace, keep the core rejection and follow the
   [fragment-boundary decision route](references/fragment-boundaries.md). Adapter acceptance,
   context notes and passing project tests do not authorize an exception.

## 1. Intake

```bash
python3 S/scaffold.py --root R
# fill R/localize.json with the operator: project_root (".." for R = <project>/localization),
# source_locale, target_locales,
# style per target locale (formality: "Sie" / "вы" / "vous"… and short notes),
# adapter.argv (set in step 2), allow_identical (ids that may stay untranslated)
```

Ask the operator which strings are in scope (UI only? emails? docs?) and where they live.
Ask which target languages they — or a native speaker on the team — can judge. For the others
the canon gets usage research before approval (step 4): record the project's field and any
reference sites the operator trusts per locale in `localize.json`'s optional `research` block,
`{"field": "genealogy and DNA research web app", "sites": {"de": ["wiki.genealogy.net"]}}`.
Identify the project's own i18n checks and test commands from its instructions and CI; record
their prerequisites and run an unchanged baseline in a scratch copy. Keep the commands and
results in `R/project-checks/` ([procedure](references/project-checks.md)).

## 2. Build the adapter

Write it with the operator, in `R/adapter/` — or in a directory inside the project, if the team
wants to keep it, set as `adapter.code_dir`. Every adapter file must be in that one directory:
acceptance covers the whole directory.
Contract: `references/adapter-contract.md`. Three commands, each printing one JSON line:

- `collect` — every in-scope string into the neutral `messages.json`, with ids, sources,
  existing targets, plural forms and their labels, and context;
- `export` — write given values for one locale, changing nothing else;
- `parse` — tokenize message text into arguments and structure tokens, or report it invalid.

**Prefer the project's own tooling for `parse`** — a vue-i18n project's
`@intlify/message-compiler`, PHP for Laravel lang files, the project's ICU library. A
re-implemented message syntax gets details wrong (significant spaces, escapes, reference syntax)
that the project's compiler already gets right. Plural labels come from the project's own
plural rules, never from a guess. A project with several message formats (a vue-i18n frontend
and a Laravel backend) stays one workspace with one canon: `parse` picks each text's syntax from
the message `id` it is sent with.

Once the adapter files exist, set `adapter.argv` in `R/localize.json` and validate the
configuration. `config_validate.py` must exit 0 before running the adapter acceptance checks:

```bash
python3 S/config_validate.py --root R
python3 S/adapter_check.py run --root R
# -> unchanged and awkward-value round trips in a temp copy, parse sanity, and
#    R/runs/_coverage/prompt.md for the coverage turn
```

Run the **coverage turn**: a Claude subagent with file-read and shell tools, given
`R/runs/_coverage/prompt.md`, asked for the JSON only. It examines the complete independent
inventory in `R/runs/_coverage/inventory.txt` (path, count and SHA-256 in the packet). For large
repositories, use the [exhaustive classification procedure](references/coverage-inventory.md):
retain every path in a mapping, review the group index, and inspect all unresolved candidates
without loading the raw list into model context. It needs no optional MCP. Scope decisions
need evidence; unresolved candidates remain coverage gaps. The turn may read any project file
and write only its classification evidence beside the inventory.
Save its answer as `R/runs/_coverage/output.json`. Every missing
string it names must be collected (fix the adapter, rerun) or declared out of scope with the operator:

```bash
python3 S/adapter_check.py accept --root R --coverage R/runs/_coverage/output.json \
  --by "<operator>" [--out-of-scope KEY ...]
```

Any later change to the adapter files or options needs `run` + `accept` again — every script
that collects or exports refuses a stale acceptance.

## 3. Collect and sync

```bash
python3 S/collect.py --root R
python3 S/ledger.py sync --root R
```

Run both again whenever the project changed (new strings, edited sources, someone else's
translation work). Existing translations become `existing`; nothing is overwritten. To let the
plugin maintain translations that were already there, the operator adopts them explicitly:
`python3 S/ledger.py adopt --root R --locale L (--id ID [--id ID …] | --all-existing) --by "<operator>"`.

## 4. Canon

```bash
python3 S/packets.py build --root R --kind canon
```

Run the canon turn (Claude subagent, `prompt.md` of that run, JSON only), save the answer, then:

```bash
python3 S/packets.py accept --root R --run <run dir> --output <answer.json>
python3 S/canon.py import --root R --file <run dir>/candidates.json
```

**Usage research.** A proposal comes only from the project's own strings. Before the operator
approves a term in a language they cannot judge, find out how established products and
communities in the project's field name it there. Per locale:

```bash
python3 S/packets.py build --root R --kind research --locale L [--entry ID …]
# run the web-only usage-research CLI child from prompt.md; capture its CLI receipt
python3 S/packets.py accept --root R --run <batch dir> --output <answer.json>
python3 S/packets.py build --root R --kind research_verify --locale L
# a DIFFERENT fresh usage-research child per batch; capture its receipt
python3 S/packets.py accept --root R --run <batch dir> --output <answer.json>
```

Read each build response's `diagnostics` for unresolved stored occurrence IDs
([fields and meaning](references/state.md#canon-research-evidence)); show these source-context
warnings to the operator without changing canon scope, approvals or audit coverage.

By default `research` takes every term and UI label not yet approved in `L` that has no
research, plus resource-limited entries. Agree the scope with the operator first and narrow it
to the core list with `--entry` (repeatable; naming a completed entry explicitly re-researches
it, which discards its old verification). A low-confidence completed search with no findings
stays complete. `research_verify` takes completed research with no verification or a
resource-limited verification. Both turns return typed `progress` with exact remaining work.
Batches hold 8 terms. The research turn cites a site, URL, rendering and verbatim quote per
source and gives a recommendation, a confidence and a note per term; the verification turn
re-opens every source, marks each confirmed or not, and tries to refute the recommendation
(`holds`, `weak` or `refuted`, with a better rendering if it has one). An unconfirmed source
is not evidence. The evidence is kept in `R/canon.research/<L>.json`, next to the canon
([state](references/state.md#canon-research-evidence)); accept a locale's batches one at a
time. Expect gaps: some sites block automated fetches (their quotes cannot be confirmed), the
web-search budget can run out mid-batch, and a concept that exists only in this product has no
outside evidence — it gets low confidence and says so. Both turns read untrusted web pages, so
give their subagents web search and page fetch only, no file or shell tools; the session saves
each answer itself. The generated requests select the packaged `usage-research`
agent to enforce that tool restriction.

**Pace the coordinator across ordinary user turns.** A tool allowance is shared by the
coordinator and its nested agents in that user turn, so adding subagents or locales does not
multiply it. Choose a bounded set of packets, keep separate ownership per locale, and stop
searching when the tool says the turn is exhausted. Save and accept actual answers, report
completed versus resource-limited entries, then finish the reply. Wait for the operator's
normal next-turn authorization before continuing unfinished work; another `claude -p` launched
inside the exhausted turn is not that authorization. Never raise a setting or switch profiles
to evade the cap. Follow [checkpoint and continuation](references/research-continuation.md)
for exact packet/entry handoff, preserved evidence and verification recovery.

Show the operator the proposals — **a short core list**: product terms, UI labels that other
messages refer to, do-not-translate names. In audit mode the proposals also show every current
rendering side by side, which is where inconsistent terms surface. Where research exists, show
each proposal with its recommendation, confidence, verdict and the confirmed sources (site, URL,
quote); `report.py --locale L` lists the terms a native speaker should still check (low
confidence, unverified, `weak`/`refuted`, or no confirmed source). The research only informs:
the operator may approve the proposal, the recommendation or another value, and `approve`
records the research summary beside the decision in `canon.history.json`. Approve what the
operator confirms, then freeze:

```bash
python3 S/canon.py approve --root R --entry ID --locale L [--value V] --by "<operator>"
python3 S/canon.py approve --root R --entry ID --by "<operator>"   # a do-not-translate entry
python3 S/canon.py freeze --root R
```

Freeze checks approved whole UI labels against approved do-not-translate rules at
their collected message occurrences. When labels and active DNT occurrences coexist, keep `messages.json`
current with `collect` first. A conflict blocks freeze and loading that lock for a new
packet, ledger sync or export; the diagnostic names the locale, both entries, occurrence,
approved value and failed rule. Use `canon.py change` for owner-approved wording repairs.
Valid repairs can be recorded one at a time: while other conflicts remain, `change`
returns `frozen: false` with their diagnostics and leaves the lock unchanged. The final
coherent repair refreshes the lock. Use `scope` and `change-scope` below for an explicit
owner-reviewed scope correction; commands do not narrow scope or waive DNT automatically.
The check does not infer a conflict from a label fragment inside a longer message.

Review turns later propose new candidates (each review batch writes
`<batch dir>/new_canon_candidates.json`); import them the same way and approve as they come.
Nothing a model proposes takes effect until a person approves it — an unapproved entry,
do-not-translate entries included, stays out of `canon.lock.json`.

Already-reviewed concept groups and scope notes can also be carried into audit packets using
the optional `canon.json` `groups` list ([approved audit context](references/state.md#approved-audit-context)).
Reuse explicit membership and approved occurrences; do not infer new relations. This is
informational context for the reviewer and does not expand mechanical enforcement.

Changing approved locale wording later uses `canon.py change` (it states the current value and
a reason), followed by a restricted audit of that entry in the locale:

```bash
python3 S/canon.py change --root R --entry ID --locale L --expect CURRENT --value NEW \
  --reason "…" --by "<operator>"
python3 S/packets.py build --root R --kind audit --locale L --entry ID
```

**DNT occurrence scope.** Import still unions occurrences; additions to an already-approved
DNT stay pending until a person reviews them. Subset imports never delete scope. Inspect it:

```bash
python3 S/canon.py scope --root R --entry ID
```

Show the operator its `active` and `pending` ids, the messages and current/proposed translations
in every configured target locale, and the intended exact additions/removals. A brand absent
from source copy does not establish that its scope is wrong. The operator may retain it; current
rejections remain in force. Apply only their concrete decision, using this output's
`entry_sha256` as `HASH` (the entire current entry, including pending proposals):

```bash
python3 S/canon.py change-scope --root R --entry ID --expect-sha256 HASH \
  --add MESSAGE_ID --remove OTHER_ID --reason "Person's scope decision" --by "<operator>"
python3 S/ledger.py sync --root R
# For EACH configured target locale L:
python3 S/packets.py build --root R --kind audit --locale L --entry ID
```

`--add`/`--remove` repeat; supply either or both. Adding a pending id approves only that id;
removing a pending id rejects its proposal. Other pending ids remain pending. Fresh additions
must exist in `messages.json` (collect first). Stale hashes and invalid/no-op deltas refuse
without writing. Repeating `approve` on an approved DNT refuses; use `change-scope`.
The command records exact before/after scopes, hashes, who and why in history, refreezes, and
requests audits of affected ids in every target locale. Removed ids and source-text matches
are included; audit packets explain the new scope and never authorize reinserting the brand
for a removed occurrence. Inspect skipped missing ids/targets rather than counting them as
audited. Active scope changes invalidate dependent candidate reviews through existing hashes:
sync, fresh checks/review and the restricted audits are required before export. Rejecting only
pending proposals leaves the active lock unchanged and requests no new audit. Spelling, approved
locale wording, sibling entries and prior research/history are retained. See
[scope state and recovery](references/state.md#reviewed-dnt-scope-decisions).

A scope delta that introduces a whole-label/DNT conflict refuses before any decision is
written. Valid scope repairs can be recorded one at a time: remaining pre-existing conflicts
return `frozen: false` and keep the previous lock until the final coherent repair.
Final scope audits retain affected IDs from that prior frozen scope across sequential repairs.

To correct the **shared note** of an approved term, UI label or do-not-translate entry, first
show the person the exact old/new notes, reason and affected locales/messages. Execute only
the correction they confirm; model output is not approval. Stop locale workers while changing
the canon.

Shared-note corrections require a coherent canon first. Resolve any whole-label/DNT
diagnostics through owner-reviewed wording or scope decisions before retrying; refusal
leaves notes, history and prior evidence unchanged.

With a validated config and current `messages.json`:

```bash
python3 S/canon.py change-note --root R --entry ID --expect "exact current note" \
  --note "corrected meaning" --reason "person's reason" --by "<operator>"
```

The exact-note guard refuses stale input. This route treats **every note correction as semantic**,
including editorial edits; there is no lighter editorial mode. It preserves all locale approvals,
wording, DNT spelling/occurrences and sibling entries. It records exact notes and prior affected
evidence in history, refreezes the lock with a new revision, clears affected candidates/proposals,
marks existing research/verification stale, and lists audit requests for every configured target
locale. Inspect `affected` in its JSON result, then build a restricted audit as above per locale.
These requests schedule no model work by themselves. Reassessment does not authorize another
research run: agree the unanswered question and bounded scope with the operator before explicitly
building `research --entry ID` and then a fresh `research_verify` turn. DNT entries have no usage-
research turn; their corrected meaning goes through the restricted audit. Old evidence remains
available in history and old run files, and old turns cannot become current on the new meaning.
For corrected entries, answers must echo the packet's top-level `note_revisions` unchanged,
as its prompt directs. See [state and evidence](references/state.md#shared-note-corrections).

## 5. Audit an existing translation

```bash
python3 S/packets.py build --root R --kind audit --locale L
```

Builds resume messages without a current full-audit verdict. To audit every eligible message
again, add `--fresh` (unrestricted audit only). A canon-entry audit never substitutes for a full
message audit. Accepted passes and failures are saved with their snapshot and run provenance;
missing or invalid verdicts remain unreviewed. Changed source, target, context, style or relevant
canon makes the saved verdict stale. Run collect and sync after project edits before resuming.
Approved informational canon context is part of that snapshot, including linked group and
scope notes; it does not change mechanical candidate/export hashes.
The existing [native handoff receipt](references/packet-handoff.md) binds each answer to the
complete original packet and instructions, so a late answer cannot claim changed inputs after
a rebuild. Audit replies keep the existing `value_sha256` verdict format.

For each batch run a Claude subagent on its `prompt.md` (JSON only), save the answer, and
`packets.py accept --root R --run <batch dir> --output <answer.json>`. Every accepted failure
is stored with its issues and snapshot hashes. Findings without a proposed replacement appear
in the report as unresolved; they cannot be accepted or exported. Proposed replacements are
stored, not applied. **Before asking the operator to approve them**, overlay
the current proposals in a scratch project and run its i18n checks
([procedure](references/project-checks.md)). Attach failures to the ids they name, with the
exact proposed values and test output, alongside the audit report; a rejected value is a
problem to fix, not a proposal ready for acceptance. Unattributed failures block the checked
set until resolved. Write the report, present it together with these results, then accept only
the proposals that passed:

```bash
python3 S/report.py --root R --locale L
python3 S/packets.py accept-audit --root R --locale L --id ID [--id ID …] --by "<operator>"
```

`status.py` and the report show current **reviewed / total**, pass/fail, unresolved, stale and
unreviewed counts, with remaining IDs. Total includes every collected message, including those
without a target; reviewed means a current accepted full-audit pass or fail, while unresolved
includes failures and messages still needing a current verdict. Findings without a replacement
remain visible. A fresh same-scope verdict supersedes its older unaccepted proposal. These are
audit records, not approval to modify the project ([details](references/state.md#audit-evidence-and-coverage)).
Audit acceptance rejects items whose collected snapshot has changed since the packet was
built, preserving newer verdicts and proposals. Rebuild those missing items before continuing.

Each message has one active replacement proposal. A failure from another audit scope preserves
that pending checked replacement; any new replacement remains visible as saved evidence and is listed
in `audit_proposal_conflicts`. Resolve the pending proposal, then rerun the other audit scope
(`--fresh` for a full audit) before asking to approve its replacement.
Older proposals without scope metadata remain **unknown**, not assumed to be full audits.
Checked replacements stay active and can still be approved through `accept-audit`.
Findings without an approvable replacement are retained as legacy evidence when a new verdict
arrives, leaving the active slot available for a newly checked correction. Their original
issues, hashes and run remain visible without inferring a scope.

An accepted proposal still gets its own review verdict (step 6's review loop) before export.
Changed proposals, including review counter-proposals, need a fresh project check before any
new operator acceptance; results for an earlier value do not cover its replacement.

If a proposal needs to repeat a named argument more often than its non-plural source, keep
the failed check and follow the [reviewed source/caller escalation](references/checks.md#when-a-locale-needs-to-repeat-a-named-argument).
Runtime success and audit acceptance cannot waive the argument check.

**Many target languages.** Every step from here on is per locale (`--locale L`), and locales
never share state: the ledger, candidates, style, plural labels, canon translations, export and
report are all kept per locale. With several target languages, run the per-locale loops in
parallel — one subagent per locale, each driving its own `packets.py build/accept` cycle — and
keep `collect.py` + `ledger.py sync` and every `canon.py` change in the main session, since
they touch all locales at once. `export_values.py` takes an exclusive lock on the workspace, so
exports of different locales run one after another even when started in parallel. A full audit is large (a project with 2 000 strings and 7
target languages is ~14 000 items, ~350 batches at the default `batch_size`): agree with the
operator whether to audit everything or start with the most-used namespaces.

## 6. Translate

```bash
python3 S/packets.py build --root R --kind translate --locale L
```

For each batch, codex translates, read-only:

```bash
codex exec -s read-only --skip-git-repo-check -c project_doc_max_bytes=0 -C <batch dir> \
  -o <batch dir>/translate.out.json - < <batch dir>/prompt.md
python3 S/packets.py accept --root R --run <batch dir> --output <batch dir>/translate.out.json
```

`project_doc_max_bytes=0` keeps the project's own agent instructions (`AGENTS.md`) out of the
translation turn. Codex reads them from every directory between the repository root and the `-C`
directory, and that path runs through the project when `R` sits inside it.

Then review what passed the checks:

```bash
python3 S/packets.py build --root R --kind review --locale L
```

Claude subagent per batch (`prompt.md`, JSON only) → save → `packets.py accept`. Repeat
translate (fix rounds carry the problems) and review until `packets.py build` finds nothing, or
the rest is `escalated`.

Before retrying a `whitespace` failure, inspect the caller when the value is a sentence fragment.
If the locale needs a different boundary, pause that message and save its failed value and check
output before another packet can overwrite them; follow the
[caller/catalog escalation route](references/fragment-boundaries.md) instead of asking the model
to trim it into compliance. This decision is made by the orchestrator; the scripts do not detect
locale-specific composition or pause retries automatically.

## 7. Export and report

First make a fresh scratch project from the current project, and a separate scratch workspace
holding the final candidates. Point only the scratch workspace at the scratch project, export
there with `export_values.py` **without `--dry-run`**, and run the same project i18n checks.
Follow [the scratch-workspace procedure](references/project-checks.md#before-live-export),
including its journal and adapter-acceptance precautions. Stop on a new failure or a check that
cannot run; fix and re-review affected values, then repeat. Only a passing check of this exact
candidate set and project snapshot permits the live commands below:

```bash
python3 S/export_values.py --root R --locale L --dry-run
python3 S/export_values.py --root R --locale L
python3 S/report.py --root R --locale L
python3 S/status.py --root R
```

Export re-collects the live project first and refuses when anything changed since the review;
it writes through a journal with backups, so an interrupted export is rolled back on the next
run (`references/state.md`). Run the project's normal checks after live export as well; review
the full project diff and commit there with the team's normal review. Recheck in scratch if
the project, candidates or test setup changed since the pre-export check.
