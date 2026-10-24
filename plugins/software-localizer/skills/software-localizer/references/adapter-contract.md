# The adapter contract

An adapter is whatever the project needs — a Node script, a PHP script, a Python script — run as
`adapter.argv` from `localize.json` plus a command. **All adapter code lives in one directory**,
`adapter.code_dir` (default `R/adapter/`; it may also be a directory inside the project, given
as an absolute path): every file named in `adapter.argv` after the first element must be inside
it. The first element is the command — `node`, `php`, a Python interpreter, or the adapter
executable itself; a bare name such as `node` is resolved once on `PATH` to an absolute path,
and that path is both what runs and what is hashed. The acceptance digest covers the whole `code_dir` (which must not contain
symlinks), every file named in or resolved from `adapter.argv` (the interpreter included, so an
upgrade means re-acceptance), `argv` and `options`: editing any adapter file — helpers
included — requires acceptance again. In `adapter.argv`, a relative path resolves
against the workspace `R`; bare command names such as `node` come from `PATH`. The project's own
tooling the adapter calls (its message compiler, `node_modules`, PHP) is not adapter code. The core runs it with the **project root as
the working directory**, stdin closed, a timeout (`adapter_timeout_s`), and expects exactly one
JSON line on stdout and exit code `0` (ok), `1` (refused) or `2` (cannot run).
`adapter_client.py` is the only part of the core that runs it. `collect` and `export` reply
`{"ok": true}` (extra fields are allowed and ignored); `parse` replies with its results (below). A
refusal replies `{"ok": false, "error": "…"}` with exit code 1.

## `collect --options O --source-locale S --target-locales T1,T2 --out F`

`O` is a JSON file holding `adapter.options`. Write the neutral message file to `F`:

```json
{
  "schema": 1,
  "files": ["src/i18n/locales/en.json", "src/i18n/locales/de.json"],
  "messages": [
    {"id": "app:settings.title", "source": "Settings",
     "context": {"file": "src/i18n/locales/en.json", "key": "settings.title",
                 "max_length": null, "comment": null},
     "targets": {"de": "Einstellungen", "ru": null}},
    {"id": "app:inbox.count",
     "source": {"forms": ["No messages", "{count} message", "{count} messages"]},
     "plural": {
       "source_labels": [{"label": "zero", "exact": true}, {"label": "one", "exact": true},
                         {"label": "other", "exact": false}],
       "target_labels": {
         "de": [{"label": "zero", "exact": true}, {"label": "one", "exact": true},
                {"label": "other", "exact": false}],
         "ru": [{"label": "zero", "exact": true}, {"label": "one", "exact": false},
                {"label": "few", "exact": false}, {"label": "many", "exact": false}]},
       "count_arguments": ["count", "n"],
       "general_index": 2},
     "context": {"file": "src/i18n/locales/en.json", "key": "inbox.count",
                 "max_length": null, "comment": null},
     "targets": {"de": {"forms": ["Keine Nachrichten", "{count} Nachricht", "{count} Nachrichten"]},
                 "ru": null}}
  ]
}
```

- `id` — stable and unique. A good id survives an edit to the source text (a key path, not the
  text). Formats that have no key (gettext) use the source text; then an edited source is one
  message gone and one new.
- `files` — every project file the adapter reads or writes, relative to the project root.
  **`collect` and `export` must work in a directory that holds only these files** (at their
  relative paths): acceptance and every export run them in such a staged copy, never in a copy
  of the whole project. Anything else the adapter needs — the project's compiler, `node_modules`,
  a PHP binary — is reached by absolute path, e.g. through `adapter.options`, or by a path the
  adapter computes from its own file's location, which also works in other checkouts. `parse`
  always runs in the live project root (it only reads). Never read or list a file inside the
  workspace `R`: it may sit inside the project and holds copies of the project's catalogs (export
  backups, staged copies). `collect` is refused when `files` names one.
- `targets` — one entry per target locale; `null` when the project has no translation yet.
- Values are carried **exactly**: leading and trailing spaces included.
- `plural` — only for plural messages. `target_labels` must cover every target locale, in the
  order the project stores the forms. `exact: true` means the project's own plural rule selects
  that form for exactly one count (only such a form may drop the count argument). Take labels
  from the project's plural rules; if a message has a form count the rules do not cover, refuse
  (`exit 1`, name the id) rather than guess.
- `general_index` — the source form used for most counts (English `other`).
- `count_arguments` — the argument names that carry the count (vue-i18n accepts both `count` and
  `n`).

## `export --options O --locale L --values V`

`V` is `{"values": {id: "text" | {"forms": [...]}}}`. Change exactly those ids' values for
locale `L` — insert a target the file does not have yet — and nothing else. The core always runs
`export` inside a temporary copy, re-collects there, and refuses the result unless the declared
file list and collected messages differ only in exactly the target values it asked for; only
then does it replace the project's files. Bytes outside the collected representation are not
verified by this comparison. Preserve the file's formatting where the format allows it, and
have the team review the full project diff before committing or shipping (see
[export safety](state.md#export-safety--export_valuespy)).

## `parse --options O --in F`

`F` is `{"items": [{"key": "k1", "id": "app:inbox.count", "text": "one form of a message"}]}`:

- `key` — an opaque correlation key: reply under it, never interpret it.
- `id` — the `id` of the message the text belongs to; the text is one of its source forms, an
  existing translation, or a candidate value. Several items can share one id. One workspace may hold
  catalogs in different message syntaxes — a vue-i18n frontend and a Laravel backend sharing one
  canon: give each catalog's ids a prefix in `collect` and choose the syntax by `id` in `parse`,
  never one parser for both syntaxes.

Reply:

```json
{"results": {"k1": {"ok": true, "tokens": [
   {"kind": "argument", "name": "count", "signature": "{count}"},
   {"kind": "structure", "text": "@:common.save"}]},
 "k2": {"ok": false, "error": "unexpected '@' at 7"}}}
```

- `argument` — a value filled in at runtime. `signature` preserves the argument's name,
  formatter, modifiers and escaping where the project requires them (`{price, number, currency}`,
  `{{- user}}`). Uses that a translation must not interchange have different signatures: for
  example, `{price, number, currency}` and `{price, number, percent}`.
  Spellings the framework fills from the same argument and allows a translation to switch between
  share one `name` and `signature`, even when they change the rendered value's case. In
  [Laravel's replacements](https://github.com/laravel/framework/blob/12.x/src/Illuminate/Translation/Translator.php),
  `:attribute`, `:Attribute` and `:ATTRIBUTE` all use `name: "attribute"` and
  `signature: ":attribute"`, so a German value may start with `:Attribute`. Normalize only the
  variants the framework generates from the actual replacement key: for `actionText`, those are
  `:actionText`, `:ActionText` and `:ACTIONTEXT`, with signature `:actionText`. Do not lowercase
  arbitrary tokens: `:actiontext` keeps its own name and signature and must not match `:actionText`.
- `structure` — anything that must survive translation verbatim: a reference to another
  message, a markup tag, a format directive.
- `ok: false` — the text would fail to compile or render in the project.

`parse` is called for every source form (a source that does not parse fails adapter acceptance)
and for every candidate value before review.

## Acceptance

`adapter_check.py run` collects from the live project, stages only the listed files, and checks
in that staged copy: collecting there gives the same messages as the live project (the adapter
needs nothing it did not list); exporting the
current values unchanged reproduces every file byte for byte; exporting awkward values (double
quotes, apostrophes, backslashes, newlines, tabs, surrounding spaces, non-Latin text) and collecting
again returns them exactly, with everything else unchanged; every source parses. It also prepares the
**coverage turn**, where a model compares the collected ids with the raw files and with an
independent inventory of the whole project, to catch a string or a whole catalog the adapter
never collected; its answer must echo the `run_id` of the check run it answers.
`adapter_check.py accept` records the result with the adapter's file digests and options; any
change to either requires acceptance again.
