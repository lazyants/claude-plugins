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
what it names), or `2` (cannot run). The workspace `R` is a directory **outside** the project.
References: `references/adapter-contract.md` (what an adapter must do),
`references/checks.md` (what scripts check and what the review judges),
`references/state.md` (ledger states, export safety, recovery).

## Hard rules

1. **Nothing runs on defaults.** `localize.json` ships `CHOOSE_` sentinels; `config_validate.py`
   must exit 0 first.
2. **The model never writes a project file.** Codex runs read-only and returns JSON; a script
   checks it and the adapter writes it.
3. **No value reaches the project without passing the script checks and a review verdict bound
   to that exact value.** A value changed after review is a new value.
4. **A person's translation is never overwritten.** Existing translations start `existing`;
   an edit the plugin did not make turns a message `human_locked`. Only `ledger.py adopt` or an
   accepted audit proposal changes that.
5. **Refuse, don't degrade.** A message still failing after `max_rounds` is `escalated` and left
   untranslated, listed in the report.
6. **The canon is decided once.** Changing an approved entry is `canon.py change`, which
   records who and why and triggers an audit of that entry.

## 1. Intake

```bash
python3 S/scaffold.py --root R
# fill R/localize.json with the operator: project_root, source_locale, target_locales,
# style per target locale (formality: "Sie" / "вы" / "vous"… and short notes),
# adapter.argv (set in step 2), allow_identical (ids that may stay untranslated)
python3 S/config_validate.py --root R
```

Ask the operator which strings are in scope (UI only? emails? docs?) and where they live.

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
plural rules, never from a guess.

Then accept it:

```bash
python3 S/adapter_check.py run --root R
# -> unchanged and awkward-value round trips in a temp copy, parse sanity, and
#    R/runs/_coverage/prompt.md for the coverage turn
```

Run the **coverage turn**: a Claude subagent given `R/runs/_coverage/prompt.md`, asked for the
JSON only; save its answer as `R/runs/_coverage/output.json`. Every missing string it names must
be collected (fix the adapter, rerun) or declared out of scope with the operator:

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

Show the operator the proposals — **a short core list**: product terms, UI labels that other
messages refer to, do-not-translate names. In audit mode the proposals also show every current
rendering side by side, which is where inconsistent terms surface. Approve what the operator
confirms, then freeze:

```bash
python3 S/canon.py approve --root R --entry ID --locale L [--value V] --by "<operator>"
python3 S/canon.py approve --root R --entry ID --by "<operator>"   # a do-not-translate entry
python3 S/canon.py freeze --root R
```

Review turns later propose new candidates (each review batch writes
`<batch dir>/new_canon_candidates.json`); import them the same way and approve as they come.
Nothing a model proposes takes effect until a person approves it — an unapproved entry,
do-not-translate entries included, stays out of `canon.lock.json`.

Changing an approved entry later is always `canon.py change` (it states the current value and
a reason), followed by a restricted audit of that entry in the locale:

```bash
python3 S/canon.py change --root R --entry ID --locale L --expect CURRENT --value NEW \
  --reason "…" --by "<operator>"
python3 S/packets.py build --root R --kind audit --locale L --entry ID
```

## 5. Audit an existing translation

```bash
python3 S/packets.py build --root R --kind audit --locale L
```

For each batch run a Claude subagent on its `prompt.md` (JSON only), save the answer, and
`packets.py accept --root R --run <batch dir> --output <answer.json>`. Findings with a proposed
replacement are stored, not applied. Write the report and go through it with the operator:

```bash
python3 S/report.py --root R --locale L
python3 S/packets.py accept-audit --root R --locale L --id ID [--id ID …] --by "<operator>"
```

An accepted proposal still gets its own review verdict (step 6's review loop) before export.

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
codex exec -s read-only --skip-git-repo-check -C <batch dir> -o <batch dir>/translate.out.json - \
  < <batch dir>/prompt.md
python3 S/packets.py accept --root R --run <batch dir> --output <batch dir>/translate.out.json
```

Then review what passed the checks:

```bash
python3 S/packets.py build --root R --kind review --locale L
```

Claude subagent per batch (`prompt.md`, JSON only) → save → `packets.py accept`. Repeat
translate (fix rounds carry the problems) and review until `packets.py build` finds nothing, or
the rest is `escalated`.

## 7. Export and report

```bash
python3 S/export_values.py --root R --locale L --dry-run
python3 S/export_values.py --root R --locale L
python3 S/report.py --root R --locale L
python3 S/status.py --root R
```

Export re-collects the live project first and refuses when anything changed since the review;
it writes through a journal with backups, so an interrupted export is rolled back on the next
run (`references/state.md`). The project's own checks and tests run after export, in the
project, as usual; commit there with the team's normal review.
