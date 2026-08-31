---
name: literary-translator
description: >
  Reusable engine-loop pipeline for high-fidelity literary book translation
  (codex-translate -> deterministic gate -> codex-review -> Claude-fix, looped
  to convergence), with a frozen name/realia canon, a configurable verse
  policy, and ledger-based resumability. Use when the user says "translate
  this book", "set up a literary translation pipeline", "new book translation
  project", "translate this EPUB/story collection from X to Y", "Gutenberg
  EPUB translation", or "resume book translation". Best suited to collections,
  novellas, and short-story works whose natural chapters are short — NOT a
  general "translate this novel" tool: a novel with genuinely long natural
  chapters exceeds the per-segment word cap and is out of scope for v1 (see
  Overview below and references/gotchas.md).
---

# Literary Translator

## Overview

This skill runs a proven engine-loop pipeline — **codex-translate → deterministic
false-green gate → codex-review → Claude-fix, looped until clean** — to produce
audited, high-fidelity literary translations. The loop runs **per-segment /
per-novella, never per-book**: each chapter, story, or novella is its own
independent unit of work through the pipeline.

Three scope statements, read BEFORE any setup work:

1. **Source-language extraction is proven against Historiettes' own 17th-century
   French text specifically, not French in general.** Any other language, AND
   any other French source, is an unverified starter preset gated by a
   mandatory smoke test (Step 0/W3, `references/language-pair-parameterization.md`).
2. **v1 defaults to converged, audited per-segment drafts — NOT an assembled
   book file** (`output.v1_scope: segment_drafts_and_audit`, still the
   default). Selecting `output.v1_scope: assembled_book` instead assembles a
   single rendered output — an Obsidian wiki this increment; EPUB and a
   custom renderer are later phases (see `references/assembly-and-output.md`).
3. **v1 is scoped to texts whose natural segments/chapters already fit under a
   configurable per-segment word cap (`max_segment_words`).** A novel with
   genuinely long natural chapters is OUT OF SCOPE for v1 — stated here, before
   setup, so effort spent on a profile, style bible, and canon scaffolding
   isn't wasted on a fatal extraction halt at chapter 1.

## Intake & proportionality (do this first)

**Three decisions in this pipeline are not yours to make alone.** You drive
it on behalf of a human who is not reading the run, so deciding IS the job —
but at three points the cost of guessing wrong is a book, and there you
report and ask:

- **A unit reaches `human_escalation` with findings still outstanding.** The
  cap is where the loop stops, not where the judgement is made
  (`--from-cap`, W5).
- **A round's own output re-opens work that had already converged.** W6's
  class sweep is the standing case, and the loop it opens has no terminating
  condition inside this pipeline (end of W6).
- **A choice between re-reviewing and re-translating already-converged
  work.** `--from-converged` authorizes a re-review and never re-translates,
  while `--allow-retranslate-converged` authorizes what `select_segments.py`
  calls, in its own words, "the path that actually destroys converged work".

A hand-back carries three things and then stops: the state as measured, each
route with its real price, and your recommendation. The human answers; you
execute it.

**This is not licence to stop at every fork** — a model that stops at every
fork is worse than one that decides. Nor does it narrow the instructions
elsewhere in this document that already require a human on their own terms:
`ambiguous_sentinels`, where each path needs a human to look at it and a
non-empty bucket fails the run, and the two attestation scripts,
`reject_review.py` and `refuse_finding.py` — copying each `--expect-*` value
back verbatim IS the attestation that a human read that exact verdict or
finding, so neither is ever yours to self-attest. Outside these three and
those, decide, and record the decision.

Before Step 0, before scaffolding a single file: size the job and agree its
output shape with the user out loud. Skipping this is how a plain
translate+gloss job ends up quietly provisioning apparatus it will never use.

1. **State the job's rough size.** Word count (main text, plus the footnote
   apparatus separately if the source has one), segment/chapter count, and
   whether verse or front/back matter is present — the same reconnaissance
   `PLAN.md` section 1 (Source) eventually records; do it now, before any
   scope commitment.
2. **Confirm output shape through existing knobs — ENFORCED at Step 0, not
   walked through as free-form prose.** This plugin has no separate "fast
   mode"/"thorough mode" switch — proportionality is expressed entirely
   through profile knobs that already exist: `glossary.enabled` (does this
   project want a researched name/realia canon at all?),
   `glossary.research_mode` (`live` vs `offline`), `footnotes.apparatus_policy`,
   `output.v1_scope`, `verse_policy.mode` (the six-value enum in
   `references/verse-policy.md`), and `engine.max_fix_rounds`. One further
   knob decides how much *output* apparatus gets provisioned: `output.target`
   (consulted only under `output.v1_scope: assembled_book`).
   `assets/profile.example.yml` ships every one of these except
   `engine.max_fix_rounds` as an invalid `CHOOSE_` sentinel, so this is no
   longer a knob this section walks the user through in the abstract before
   scaffolding proceeds: Step 0 (below) halts on a freshly-copied profile and
   prints, in one pass, a questionnaire naming every sentinel still
   unanswered. `verse_policy.mode` joined that set in #730 — it decides what a
   review may fail a segment on and it is hashed, so it is a decision the user
   makes, never one the orchestrator reads off the source material.
   This section's job is to relay that printed questionnaire: **every printed
   question and every cost it states reaches the user intact — none dropped,
   none paraphrased away** — and then fill in `profile.yml` with their
   answers. Intact is not the same as untouched: where the series decisions
   ledger holds a row for one of these fields (R10), ATTACH that provenance to
   the question rather than replacing it, so the user sees both what the knob
   costs and what they already decided.
   **Volume N>1 of a series does not gather these answers cold.** Read
   `<series directory>/decisions.md` BEFORE relaying the questionnaire and
   carry every recorded decision forward as a default-with-provenance, per
   R10. A decision the user already made and a question they have never been
   asked look identical in a freshly-copied profile, and only the ledger tells
   them apart.
3. **Default fast, offer thorough explicitly, through those same knobs.** The
   default posture for a new project is the lean end of every one of those
   knobs — offline research where live isn't required, the lightest
   apparatus policy the source actually needs. Present the exhaustive
   alternative (live research, a fuller apparatus) as an explicit opt-in the
   user chooses through the same knobs, never as a separate code path.
4. **Agree pipeline role assignment.** Translate and review are
   **hard-locked to codex** (R1, `references/engine-loop.md`) — codex is the
   sole translate/review engine, now LAUNCHED by the shipped, detached
   `codex_job.py` driver (1.4.7) rather than the old `codex:codex-rescue`
   forwarder; every shipped template enforces this and no profile knob swaps
   either role to a different engine. Claude (the orchestrating session)
   **only** applies fixes (or refuses a finding it cannot substantiate — #532),
   orchestrates, and verifies; it never originates a translation or grades its
   own output. **codex-translate → deterministic
   gate → codex-review → Claude-fix, looped to convergence, IS the v1
   default** — not a menu of interchangeable options. Confirm the user has
   Codex CLI access before scaffolding proceeds; v1 has no
   degrade-to-Claude-only fallback. Other constellations — Claude
   translating, a fresh Claude agent reviewing, or any other engine-per-role
   split — are the **durable, reusable pattern** documented in
   `references/operating-constellation.md`: the general shape a future
   engine-per-role knob would unlock, not a v1 choice. This fixed pairing
   needs no profile knob; note it in `PLAN.md` for project-level clarity if
   useful, never in `profile.yml`.
5. **State why the lean default is worth it.** A plain translate+gloss job
   that turns on every knob pays for machinery — live-research round-trips, a
   heavier apparatus, a fuller fix-round budget — it will never read. Naming
   that trade-off up front is cheaper than discovering it mid-project. Defer
   side-quests: a knob not required for THIS project's stated goal stays at
   its lean default, full stop — raise it later, from `PLAN.md` section 5, if
   the project's own scope genuinely grows to need it.

## Step 0 — Read + validate `profile.yml`

Throughout this skill, `{{PLUGIN_ROOT}}` denotes this skill's own directory —
under Claude Code, `${CLAUDE_PLUGIN_ROOT}/skills/literary-translator`, the
directory that holds `assets/`. It is **not** `${CLAUDE_PLUGIN_ROOT}` itself:
an installed plugin has no `assets/` at its root, so every
`{{PLUGIN_ROOT}}/assets/...` path below — and every `--plugin-root` value,
which each consumer that resolves a plugin resource does by appending
`assets/scripts/`, `assets/schemas/` or `assets/templates/` — would name a
directory that does not exist. (`backfill_resume_gate_ack.py` is the one
exception: it accepts `--plugin-root` for flag uniformity and provenance
reporting, and resolves nothing with it.) W9r's own
`LT=<the literary-translator skill directory>` (Step W9r) is the same value.

Implemented by `scripts/profile_validate.py`, invoked as:

```
python3 {{PLUGIN_ROOT}}/assets/scripts/profile_validate.py --profile .claude/literary-translator/profile.yml
```

Run by the **orchestrating session directly**, always from the plugin's own
install path, never a durable-root copy — it runs before Step 0a exists to
create one (same exception as Step 0c reading
`references/source-format-adapters/*.md` directly from the plugin). It is one
of three plugin-path scripts never copied to `durable_root`: `validate_extraction.py`
(the W2 post-extraction gate) and `glossary_preflight.py` (the W3 glossary
staleness gate, 1.4.0) are kept plugin-only for tamper-proofing and
freshness-on-resume rather than because either predates the durable root. See
Step 0a's own copy-pass section below for each exclusion's specific reason —
including why `resolve_codex_companion.py` (the W5 codex-companion path
resolver, 1.4.7) is NOT a fourth exclusion: it is copied like every other
self-anchored script, since the claim that it could not be was found false.

Order of operations:

1. **Existence check first**: if `.claude/literary-translator/profile.yml` is
   absent, copy `assets/profile.example.yml` to that exact path (guarded on
   absence — an existing filled-in profile is never touched again), name the
   path, and then CONTINUE into this same run's placeholder scan at item 5,
   so the one invocation that creates the starter profile is also the one
   that prints its questionnaire. It does not halt on the spot and it does
   not tell anyone to fill the file in first: there is deliberately no window
   in which a sentinel-laden profile exists and its questions have not been
   relayed. **This branch is the ONLY sanctioned creator of that file.** The
   orchestrating session must never create
   `.claude/literary-translator/profile.yml` itself, and must never answer a
   fresh copy's sentinels from that file's own inline comments: a profile
   carrying real values instead of `CHOOSE_` sentinels takes the `exists()`
   branch, item 5's scan finds nothing to ask, and Step 0 prints `OK` without
   one intake decision having been put to the user — printing exactly what a
   run that answered all of them prints. Because the scan parses YAML, item
   2's dependency preflight now runs in this branch too; if a package is
   missing the operator gets that actionable message instead of the
   questionnaire, and the starter profile has still been created.
2. If present, dependency preflight first: `import yaml` and
   `import jsonschema` each wrapped in their own try/except; on
   `ImportError`, print an actionable message naming the specific missing
   package (`"ERROR: this plugin requires the '<package>' Python package.
   Install with: pip install -r <path>"`), where `<path>` is a real
   `requirements.txt` resolved at runtime by walking up from the script's
   own location (never a literal `{{PLUGIN_ROOT}}` string), and exit
   non-zero.
3. Parse YAML with `yaml.safe_load` (never `yaml.load`). Unknown
   `profile_version` halts with a migration hint.
4. Unknown top-level keys are FATAL by default, naming the exact key —
   except keys under a reserved `x_*` namespace, silently allowed
   (forward-compat extension point).
5. **Whole-profile placeholder scan — moved here, strictly BEFORE schema
   validation, exactly so a fresh profile HALTS naming every unanswered
   intake decision instead of one schema-enum error (#727).** Every string
   value anywhere in the parsed document, not a named subset of fields, is
   checked against every literal placeholder `assets/profile.example.yml`
   ships. The two `/ABS/PATH/TO/...` path placeholders and the book-title
   placeholder keep their existing message unchanged. Every remaining
   `CHOOSE_`-prefixed enum sentinel — `glossary.research_mode`,
   `glossary.enabled`, `footnotes.apparatus_policy`, `output.v1_scope`,
   `output.target`, `verse_policy.mode`, and the `plain_text` adapter's
   `verse_detection`/`footnotes` fields — gets an error naming that dotted
   path, plus, for every sentinel this skill documents a question for, that
   question appended verbatim (a sentinel with no documented question keeps
   the base message, never loses the error). Before the first sentinel
   error, print one header line to stderr naming the profile path and
   instructing: relay this list to the user and fill in their answers.
   FATALLY reject (exit non-zero) if any placeholder or sentinel survives —
   this now happens before jsonschema validation ever runs, so a
   freshly-copied profile reports every unanswered intake decision in ONE
   pass instead of the single schema-enum error on whichever field the
   schema happens to gate unconditionally (previously
   `glossary.research_mode` alone — the defect #727 fixes).
6. Validate whole-file shape via
   `jsonschema.Draft202012Validator(profile.schema.json, format_checker=jsonschema.FormatChecker())`,
   loaded from the plugin's own `assets/schemas/profile.schema.json`.
7. Only once schema passes, run procedural checks: `source.path` must exist
   (for every format including `custom`; for `custom`, this is the
   primary/representative sanity-anchor input, while `manifest.json`'s
   `source_inputs[]` remains the authoritative full file list);
   `project.durable_root`'s parent must exist/be writable and must NOT
   resolve under `/tmp`/`scratchpad` (`durable_root` itself need not exist yet
   — Step 0a creates it);
   `output.destination`'s parent is checked only when it resolves outside
   `durable_root`; `source.language.particle_config`'s file existence is
   NOT checked here (deferred to end of Step 0a).
8. `adapter_config.plain_text.segmentation.heading_regex`: when
   `method: heading_regex`, wrap `re.compile(heading_regex)` in try/except,
   FATAL on `re.error`. Cross-field WARNING (non-fatal) if the unselected
   method's own sibling field is non-null.
9. `source.format: custom` selected → print a non-fatal warning naming it
   experimental/unpiloted, pointing at `custom.md`.
10. `source.language.particle_config`: procedurally reject (FATAL, naming
    the field) any value containing `/`, `\`, `..`, or an absolute-path
    prefix, before any path-join.
11. `smoke_test.report_path`: procedurally reject (FATAL) any value
    containing the literal substring `..` anywhere, before any path-join.
12. On a resumed project: check `translate_TASK.md`/`review_TASK.md`/
    `glossary_TASK.md`'s leading `<!-- PROMPT_CONTRACT_VERSION: N -->` marker
    against a hardcoded `CURRENT_PROMPT_CONTRACT_VERSION` constant — FATAL
    on missing marker (treated as version 0), malformed non-integer value,
    duplicated marker, or non-leading marker — each naming the file and the
    specific problem; migration instruction points at the current template
    to manually re-apply (never auto-overwritten).
13. Same for `extract.py` (resumed project): check its leading
    `# EXTRACTOR_CONTRACT_VERSION: N` Python comment (not HTML-comment
    syntax — this file must stay valid Python) against
    `CURRENT_EXTRACTOR_CONTRACT_VERSION`, identical four-state fatal
    treatment.
14. `output.target`: FATAL when it names a **built-in adapter whose module has
    not shipped** — `epub` maps to `render_epub`, and there is no
    `render_epub.py`. Delegated to
    `output_resolve.assert_builtin_adapter_shipped()`, so the mapping and the
    actionable message have one home. Gated on
    `output.v1_scope: assembled_book`: under the default
    `segment_drafts_and_audit` nothing ever reads `output.target`, and refusing
    an inert value would reject profiles that pass today. The field is optional
    in the schema, so an absent `target` does not fire this — that stays a Step
    0d condition. `custom` is untouched here: its `renderer_path: null` HALT is
    the documented co-design starting state and belongs to Step 0d, not to
    Step 0.

Prints one field-named, actionable error line per violation, exits non-zero
on any failure.

## Step 0a — Create durable root; install scripts/languages/schemas; ownership marker

Runs strictly after Step 0. First action: ownership-marker check via
`${durable_root}/.literary-translator-root.json` (`{owner_profile_path, created_at}`).

`MANAGED_ENTRIES` = exactly: `scripts/`, `languages/`, `schemas/`, `segments/`,
`glossary/`, `verses/`, `runs/`, `out/`, plus `.literary-translator-root.json`
itself. Everything else under `durable_root` (`.claude/`, book source files,
`.git/`, README) is ignored for this check — `durable_root` coinciding with a
project's own root is an explicitly supported config.

The moment Step 0a first creates/adopts any `MANAGED_ENTRIES` subdirectory, it
also writes `<managed_dir>/.literary-translator-managed` inside it.

Four outcomes, in this exact order:

1. **None of MANAGED_ENTRIES exist** → fresh adoption: create `durable_root`,
   create every managed subdir + its own per-directory marker, write root
   marker, proceed normally.
2. **Root marker present and matches this profile's path** → resumed project,
   proceed normally, backfill any missing per-directory marker silently.
3. **Ambiguous** (at least one MANAGED_ENTRIES name exists, no root marker,
   and none of those existing directories carry their own per-directory
   marker) → NOT fatal. Halt with an ADOPTION PROMPT: name every pre-existing
   managed-directory name found, and enumerate the exact shipped filenames
   that already exist at their destination paths inside those directories
   (or state explicitly "no shipped-filename collisions found" if none).
   Instruct: set `project.durable_root_adopt_existing: true` and re-run to
   proceed like case 1, or repoint `durable_root` if unsafe.
4. **At least one MANAGED_ENTRIES directory carries its own per-directory
   marker (real prior plugin involvement), but root marker is absent or
   claims a different owner** → the original unconditional FATAL halt, no
   adoption flow — naming the path and either "no ownership marker found"
   or "claimed by a different project (`<owner_profile_path>`)".

Then: creates `project.durable_root` if it doesn't exist; creates the fixed
skeleton: `segments/`, `glossary/`, `glossary/runs/` (1.2.0 — the parent
directory for every glossary-pass run's `{{RUN_ID}}`-scoped fragments and
manifests, see `references/orchestration-and-batching.md` and
`references/canon-and-glossary.md`), `verses/`, `runs/`, `runs/ledger.d/`,
`runs/workflows/`, `scripts/`, `languages/`, `schemas/`, `out/`. Also
explicitly creates the specific resolved parent of `output.destination`
(mkdir -p, idempotent) whenever it resolves inside `durable_root`, and the
same for `smoke_test.report_path` (skipped when null).

Copies (unconditional overwrite, safe since these files are never
hand-edited): every file in `assets/scripts/*.py` (except
`profile_validate.py`, `validate_extraction.py`, `glossary_preflight.py`, and
`fix_scope_audit.py` — four files, EACH excluded for its own distinct reason,
never a shared one (a further exclusion, `scaffold_setup.py`, follows below in
a wholly separate category — it is not a bundle member at all, never mind
never-copied-for-its-own-reason like these four; do not read "four" here as
this paragraph's total exclusion count):
  - `profile_validate.py` runs *before* Step 0a exists to copy anything — Step 0
    reads and validates `profile.yml` first, so there is no durable-root copy of
    this script yet, and there never will be one for that specific invocation.
  - `validate_extraction.py` is kept plugin-only so a hand-adapted `extract.py`
    cannot weaken its own self-checks and still pass: the gate pins the durable
    `extract.py`'s self-check region by hash against the plugin's own shipped
    value, which only works if the checker itself is never a durable, hand-reachable
    copy (see `references/false-green-gate.md`).
  - `fix_scope_audit.py` (1.68.0, #607) is kept plugin-only for the same shape
    of reason as `validate_extraction.py`, one step further out: it is the
    check that asks whether this durable root's copied files still match the
    plugin they came from. A durable copy of it would sit inside the very tree
    it audits, reachable by the same W5 fix turn it is checking, and would
    then be able to report on itself. Because there is no durable copy, W5's
    own preflight REFUSES to start when `{{PLUGIN_ROOT}}` is unsubstituted —
    there is no weaker fallback to degrade to.
  - `glossary_preflight.py` would be actively harmful copied, not merely redundant:
    a durable copy's own `__file__`-relative schema lookup would land on the
    *durable* schemas as its "plugin" side too, comparing durable-vs-durable — a
    vacuous pass that could never detect staleness.

`resolve_codex_companion.py` used to be a fourth exclusion here, on the claimed
reason that a durable copy "could not glob the plugin's own install locations to
find the newest installed `codex-companion.mjs`". That reason was false: the
script **reads** no `__file__` — its own location never enters its search — and
imports nothing plugin-specific; its DEFAULT search is rooted at the RUNNING
Claude config profile (`$CLAUDE_CONFIG_DIR`, else `~/.claude`) and then at
`os.path.expanduser("~")` against
`~/.claude*/plugins/cache/openai-codex/**/codex-companion.mjs` — a different
plugin's own install cache. Both roots are ENVIRONMENT facts, so a durable copy
globs the identical paths and finds the identical companions wherever it runs
from. It is now copied like every other self-anchored script; do not re-exclude
it by re-deriving this same plausible-sounding-but-wrong argument, and do not
re-check it with a literal `__file__` occurrence count — the script's own
docstring has to discuss `__file__` by NAME to explain this exact history, so a
bare grep count is useless as a re-check either way. Read
`tests/resolve_codex_companion.test.py::test_the_resolver_contains_no_executable_reference_to_dunder_file`'s
verdict instead — it parses the file with `ast` and only flags a genuine
executable reference (an `ast.Name` node), never a prose mention.

**Overwriting an adapted REGULAR file at this destination is an ACCEPTED
TRADEOFF, stated so it is not rediscovered as a bug.** A regular file adapted at
`${durable_root}/scripts/resolve_codex_companion.py` while that destination was
documented as never copied is overwritten with no backup and no warning — and on
a RESUMED project (outcome 2 above) with NO collision detection of any kind,
since that detection exists only on outcome 3's ambiguous-adoption path, which
outcome 2's own root marker match bypasses entirely. The retired check was
byte-identity to the shipped source: it reads "MANAGED" only while the shipped
bytes never move, so the first release that edits the resolver turned it into
a halt on every ordinary project — the majority path — that still identified
no adaptation. Separating the populations for real needs a prior-version
digest or a per-file managed marker, permanent machinery this design refuses
to carry for a population measured at zero; the reasoning is transcribed in
`tests/scaffold_idempotency.test.py`'s
`apply_resolve_codex_companion_migration` docstring. A resumed project that DID
adapt this file must re-apply its adaptation after the upgrade, which the
launch fix this copy delivers makes unnecessary in the first place.

So THIS ONE FILE, and only this one, gets a check before its copy, never the
blanket unconditional overwrite the rest of the bundle gets. Classify the
destination with `os.lstat()` first — NEVER `Path.exists()`/`is_file()`,
which FOLLOW a symlink and would silently write through it, leaving whatever
was actually there (a workaround pointed elsewhere, or a stale copy) in
place while reporting success:
  - **Absent** (`os.lstat()` raises `FileNotFoundError`) → copy normally.
    This is every project that never worked around the defect — the
    overwhelming majority, and the same shape a fresh project (outcome 1)
    always has.
  - **A genuine regular file** — whatever its bytes → copy normally. A
    stale managed copy is exactly what the rest of this copy pass
    overwrites unconditionally, and this file is now no different. This
    branch USED to demand byte-identity to the shipped source; that
    condition was cut in #287 and must not come back — byte-identity
    stands in for "is this copy MANAGED?" only until a release edits the
    resolver, after which every ordinary project's own managed copy reads
    as divergent and the halt fires on the majority path.
  - **Anything else** — a symlink (identical-looking target or not — lstat
    does not resolve it, so its target content is never compared), a
    directory, or any other non-absent, non-regular entry `os.lstat()`
    reports — → HALT before copying anything. This limb is NOT migration
    leftover and never expires: it exists because a copy over a symlink writes
    THROUGH it, so the shipped file never lands at the destination at all
    while the copy reports success.
    Name the exact path and state plainly that a pre-existing,
    non-managed entry sits there and this copy pass will not touch it
    silently. Instruct the operator PER ENTRY KIND, never one generic
    "move or rename it" — renaming preserves bytes only for the entry
    kinds where the name and the bytes are the same thing:
      - **Symlink** → renaming the link is NOT preservation — it relocates
        the POINTER, not the bytes it points at, and that target can be
        transient, on a different filesystem, or itself deleted later.
        Instruct the operator to first copy out the RESOLVED target's
        actual content to a new location, THEN remove the symlink.
      - **Directory** → move the whole directory aside; this preserves its
        contents as a unit.
    Then re-run.

**Do not "improve" this into an automatic backup-and-copy, and do not re-add
the byte-identity condition (#287).** Both have been tried and retired; the
reasoning is transcribed in `tests/scaffold_idempotency.test.py`'s
`apply_resolve_codex_companion_migration` docstring, which the tests below it
enforce against this section.

Also, separately, `scaffold_setup.py` — Step 0a's own bundle-hash marker writer
(#194), which likewise runs only from the plugin path: it is invoked below as
Step 0a's final action and imports the plugin's own `cache_key.py` helpers, and
is deliberately NOT a bundle member, so it must never land under
`scripts/`), every shipped
file in `assets/languages/`
(`fr.json`, `de.json`, `es.json`, `he.json`, `it.json`, `README.md`), every file in
`assets/schemas/*.json` → `${durable_root}/scripts/`,
`${durable_root}/languages/`, `${durable_root}/schemas/` respectively.
Touches only the exact shipped filenames — never clobbers a project-local
override coexisting under a different filename (e.g. `fr.local.json`).

Also copies from `assets/templates/` (ONCE, each individually guarded on its
own destination's absence — never re-copied, never regenerated): `PLAN.template.md` →
`${durable_root}/PLAN.md`, `style_bible.template.md` →
`${durable_root}/style_bible.md`, `consistency_issues.template.md` →
`${durable_root}/consistency_issues.md`, `extract.py.template` →
`${durable_root}/extract.py`, `translate_TASK.template.md` →
`${durable_root}/translate_TASK.md`, `review_TASK.template.md` →
`${durable_root}/review_TASK.md`, `glossary_TASK.template.md` →
`${durable_root}/glossary_TASK.md`.

Exception within this same copy pass: `mass-translate-wf.template.js` /
`glossary-pass-wf.template.js` / `skeptic-pass-wf.template.js` get
`scripts/`-style repeatable-overwrite treatment (re-instantiated fresh at
W5/glossary-pass/skeptic-pass time respectively), never the one-time-seed
treatment the other templates get.

Final action of Step 0a — computes and writes the two marker files by
invoking `scaffold_setup.py` (#194) from the plugin path (NOT a durable copy;
it imports the plugin's own `cache_key.py` helpers), AFTER every bundle member
has been copied into `${durable_root}/scripts/` and BEFORE any
`cache_key.py`/`resume_setup.py` call:

```
python3 {{PLUGIN_ROOT}}/assets/scripts/scaffold_setup.py --durable-root ${durable_root}
```

It writes `${durable_root}/runs/.plugin_bundle_hash` (sha1 over the sorted
concatenated bytes of every `PLUGIN_BUNDLE_MEMBERS` entry under `scripts/` — read by
`cache_key.py` rather than re-hashing the bundle per segment; that tuple in
`cache_key.py` is the authority on its own membership, never a count restated
here) and `${durable_root}/runs/.orchestration_bundle_hash` (sha1 over
`scaffold_setup.py`'s own `ORCHESTRATION_BUNDLE_MEMBERS` — non-gating for convergence, never part of the
composite cache key, but gating for resume: folded into the resume-integrity
digest, and also surfaced in W8's reporting). Both are written atomically
(sibling temp file + `os.replace`) with a trailing newline; both readers
`.strip()`.

Last action: the deferred `particle_config` existence check — resolve
`source.language.particle_config` as `${durable_root}/languages/<value>`
(bare filename, exactly one `languages/` segment) and halt (field-named) if
it still doesn't resolve to a real file.

**#409 upgrade note — mandatory, not optional, on a RESUMED project
(outcome 2 above):** this release added `segment_dispatch_driver.py` to
`PLUGIN_BUNDLE_MEMBERS` (the tuple named above), which moves
`plugin_bundle_hash` for every project on upgrade. A moved
`plugin_bundle_hash` makes every already-converged segment's cache key
mismatch, reclassifying it `stale` — dispatch-eligible again.
`select_segments.py`'s Step 1 gate (see the `--allow-retranslate-converged`
flag, W5 below) refuses that using the durable
`${durable_root}/segments/.ever_converged.{seg}` sentinel — but a project
that converged segments on an OLDER version of this plugin, before that
sentinel existed, has NO sentinels at all, so the gate has nothing to
refuse with: the very first W5 dispatch after upgrading would sail through
ungated and silently retranslate the whole book. Before the first W5
dispatch on any project this plugin has touched before it had
`segment_dispatch_driver.py`, run (dry run by default — issues no mutating
operation and changes no project content; the script's own docstring
explains why that is the exact wording and "zero filesystem writes" is not):

```
python3 ${durable_root}/scripts/backfill_ever_converged.py
```

Sequence the backfill before the first W5 dispatch, and for the mode that
writes, that sequencing is enforced rather than asked for.
`select_segments.py`'s Step 1 gate takes its `.ever_converged` census once,
at selection time, and nothing rechecks it when the translate work it
authorized is actually dispatched — so a sentinel raised in between does
not revoke an authorization already granted, and the dispatch retranslates
the very work the sentinel was meant to protect, as a green run.
`backfill_ever_converged.py --apply` therefore acquires the project lease
`${durable_root}/runs/.driver.lock` (a kernel `flock`, `LOCK_EX|LOCK_NB`)
before it does anything and holds it for the whole run — the same lease
`segment_dispatch_driver.py` takes for the entire span of a dispatch, from
before its `select_segments.py` Step 1 call until the dispatch loop
finishes — so an `--apply` run inside that window refuses, exit 1, naming
the lock path, and writes nothing. A dry run (the default, no `--apply`)
takes no lease, deliberately: it writes no sentinel and so cannot cause
this at all, and acquiring one would create `runs/.driver.lock` and break
the dry run's own "issues no mutating operation and changes no project
content" guarantee. A dry run may therefore still be taken alongside a
dispatch; what it reports may simply be stale. Two paths the lease does
not cover, both still governed by convention: running `select_segments.py`
by hand and then dispatching by hand as a separate command — a standalone
`select_segments.py --from-stalled` does acquire this same lease, but that
lease dies with the selector process, leaving the window before a later
hand-run dispatch open exactly as before; and two machines pointed at one
durable root through a sync-replicated folder (Synology Drive, Dropbox,
iCloud), where each takes a valid local flock and neither sees the other —
pre-existing, and identical for the driver's own lease. On a filesystem
that cannot lock (some NFS/SMB mounts) — whether `flock` fails outright or
succeeds without being enforced — the backfill warns on stderr and proceeds
rather than refusing, since refusing there would block the one-time legacy
migration entirely, and a root with no sentinels at all loses every segment
the backfill could have protected.

**Check `$?` first, then** read the printed JSON's
`missing_sentinels`/`counts` fields — a non-empty `missing_sentinels` means
this project needs backfilling before W5 runs; a genuinely fresh project
that has never converged anything reports zero and needs no action. **An
empty `missing_sentinels` means "no action" only on a run that exited 0.**
A census that could not read the directory at all classifies every segment
`ambiguous` and reports `missing_sentinels: []` — the very field this note
tells you to read, empty for the opposite of the reason you would assume.
Other fields do differ (`already_sentineled` is empty too, and the `counts`
follow), but none of them is what this note sends you to look at; `$?`,
`success` and `ambiguous_sentinels` are what separate the two answers, which
is why the dry run now fails on it. The checklist below applies to a
dry run's failure exactly as it does to `--apply`'s. Re-run with `--apply` to
write
the missing sentinels (add `--allow-merge` too if the dry run refused for
lack of an existing `runs/ledger.json`; `--allow-empty` to confirm a
genuinely zero-segment result is expected rather than a broken read).

**Six things decide whether the protection is actually up, and
`missing_sentinels` alone is not one of them. They govern BOTH modes** —
gating this list on `--apply` was itself the bug that let a dry run whose
census established nothing read as a healthy project:

- **`$?` / `success`** — non-zero and `false` mean the run did not finish
  what it set out to do. Two different shapes produce it, so read the
  payload rather than assuming: a per-segment failure carries
  `failed_to_create`, while a fatal abort (an unreadable ledger, a segment
  id that fails the path-safety check) has **no `failed_to_create` key at
  all** — a script that indexes it blindly will crash on exactly the runs
  that matter. A fatal payload always carries `error`, and may carry a
  context key or two beside it (`seg` for an unsafe segment id,
  `ledger_path` for an empty result), so consume `error` and treat the rest
  as optional. Either way, do not dispatch on a failed backfill.
- **`failed_to_create`** — each entry names a segment left unprotected and
  why. Resolve every one before W5.
- **`directory_sync_error`** — the directory could not be `fsync`ed. The
  sentinels are where readers look, but may not survive a crash. Re-running
  settles it: the sync is unconditional, so a retry re-syncs even when it
  creates nothing and finds every sentinel already present.
- **`segments_dir_replaced`** — set by **two different conditions**, so read
  the string, not just the key. Either `segments/` now names a different
  directory than the one the run worked in — everything the run examined and
  linked belongs to the old one, so the **whole report is about a directory
  readers will not consult**, including any segment it called already
  protected, and re-running does not settle that — or the identity could not
  be **determined** at all because `fstat`/`stat` failed, which re-running may
  well settle. Use the key to decide the run is untrustworthy (it is, either
  way); use the string to decide which of the two you are looking at. Checked
  in dry runs too, because a dry run's `missing_sentinels` is what this note
  tells you to act on.

  **Known limitation, narrower than it was but not closed.** The directory
  descriptor settles WHICH DIRECTORY every sentinel lookup read, and nothing
  about the entries inside it: a sentinel rewritten in place by a sync client
  or a restore tool, or simply deleted after the census called it PRESENT,
  reaches a wrong answer without ever touching the pathname. Before trusting a
  clean report on a live, synced or networked project — or when a dispatch
  refuses a segment as `lost_sentinels`, whose non-destructive remedy is
  `backfill_ever_converged.py --apply` — read
  `references/sentinel-backfill.md`, "The known limitation (#442/#443/#621)".
  It states which of the three mechanisms (the two above, plus a
  network-filesystem failover, remount or snapshot switch) the dispatch
  gate's `lost_sentinels` refusal now covers, which residual #443's
  content-bearing marker did NOT close, and why a clean run is evidence about
  the moment it ran and must be re-run immediately before dispatching.
- **`ambiguous_sentinels`** — a path whose protection status could not be
  established. That covers both a path that is demonstrably not a regular
  file (a directory, a symlink, a dangling symlink) and one whose state
  could not be read at all, where it may in truth be absent or perfectly
  fine. Never repaired automatically and never counted as protected; each
  needs a human to look at the path. **A non-empty bucket fails the run**
  (`success: false`, exit 1) — an entry here is a segment whose protection is
  UNPROVEN, which for a dispatch decision is the same standing as
  `failed_to_create`. It is empty whenever the sentinel paths can be read,
  which is the ordinary case; a transient `ESTALE`/`EIO` on a network
  filesystem lands a perfectly good sentinel here too, and re-running settles
  that class on its own. **An entry that persists needs a human, and there is
  deliberately no `--allow-ambiguous` to wave it through** — unlike
  `--allow-empty` and `--allow-merge`, which confirm a state the script
  understands, such a flag would confirm one it explicitly could not
  establish, which is the false clean this whole check exists to remove. Fix
  the path (a directory, FIFO or symlink at a sentinel name means that segment
  is genuinely unprotected), then re-run.
- **`not_evaluated`** — segments this script never considered, because
  their current ledger status is not one it can read as converged. A
  segment that converged and was later re-dispatched has had that
  convergence **erased** from the ledger, so it cannot be recovered here.
  On a project that converged segments before the sentinel existed, these
  must be inventoried by hand — the script makes no claim about them, and
  `success: true` does not cover them.

**`sentinel_attribution` is not on the six-item list above, and deliberately
so.** The report also names, for every marker it found ALREADY present, which
writer the marker SAYS published it. It is a DIAGNOSTIC: it moves no bucket,
no count and not `success`, no gate anywhere reads a marker's body, and it is
self-reported rather than authenticated. **`unattributed` does not mean
unprotected** — every marker written before the field existed is unattributed
and protects exactly as it always did, which is what makes the change safe to
adopt mid-flight. Read `references/sentinel-backfill.md`,
"`sentinel_attribution`", when a segment's ledger row and its marker disagree
and you need to know whether anything ever observed that convergence.

See `backfill_ever_converged.py`'s own module docstring for the full
mechanism and CLI contract.

## Step 0b — Resolve verse-policy adapter

Look up `verse_policy.mode` against the 6-value enum table in
`references/verse-policy.md` → resolves to (a) instruction-block text and
(b) which `validate_draft.py` verse checks apply. **Delivery channel: never
spliced into `translate_TASK.md`/`review_TASK.md` directly** (those stay
verse-policy-neutral, hand-adapted, one-time-copied files) — consumed
exclusively through the generated per-run workflow script's
`{{VERSE_POLICY_INSTRUCTION_BLOCK}}` template substitution, read fresh from
the current `profile.yml` every time a run is scaffolded (W5/glossary-pass) —
this is what keeps it staleness-immune when `verse_policy.mode` changes
later. Fatal validation here: `mode == mixed_by_length` with
`threshold_lines` null/absent halts immediately.

## Step 0c — Resolve source-format adapter

`source.format` → a file under `references/source-format-adapters/`
(`gutenberg-epub.md`, `plain-text.md`, `custom.md`) — read directly from the
plugin install path (same exception class as `profile_validate.py` — no
durable-root copy needed). Filename resolution: lowercase, underscore→hyphen,
`.md` suffix; halt naming available files if missing.

For `custom`, read `references/source-format-adapters/custom.md` now —
Step 0c's two procedural checks (the `extractor_path: null` co-design halt;
the FATAL `..`/leading-`/` rejection before the existence check under
`${durable_root}/scripts/custom_extractors/`), the fixed output contract a
co-designed extractor must meet, and why W2's managed gate SKIPS the
region-hash pin for `custom`, are all there and are not restated here.

## Step 0d — Resolve output-target adapter

Runs only when `output.v1_scope: assembled_book`. Under the default
`output.v1_scope: segment_drafts_and_audit`, Step 0d is a deliberate no-op —
zero resolution work, zero HALT risk — matching the proportionality
guardrail that a plain translate+gloss job never pays for assembly
machinery it will never read. When `assembled_book` IS selected, read
`references/assembly-and-output.md`, "Step 0d — resolving the target,
early", BEFORE resolving anything: it carries `output_resolve.py`'s
enum-to-adapter mapping, the `render_epub` HALT and its three ways out, and
the two procedural checks a schema cannot express for `target: custom` (the
`renderer_path: null` co-design HALT, and the `..`/leading-`/`/pattern FATAL
against the fixed `${durable_root}/scripts/custom_renderers/` subtree). A
Step-0d custom-target HALT blocks only assembly (W9): a project can still
scaffold, translate and converge every segment with the co-design
conversation still outstanding.

**When the target resolves to `obsidian`, ask one further question before
translation starts: do you want an index, and of what?** The default answer
is canon — one note per `canon.json` entry, which is what a book whose names
can be seeded from a list wants. A book whose names are NOT knowable in
advance cannot seed that list, and its translator has to mark entities as it
goes; that is what `output.entity_markup` declares (`tags`, the optional
`ref_attribute`, and `index_from: canon | markup` — full semantics in
`references/output-target-adapters/obsidian.md`). Ask it HERE, not later:
the answer is what this project's `style_contract` then has to tell the
translator to mark, and a convention invented mid-book reaches the reader as
raw markup. An absent block runs none of it, so a project that
wants no marked index answers by saying nothing (the release's two
unconditional renderer changes are named in the CHANGELOG; neither is
reached by a book that carries no such markup).

This question is deliberately NOT one of the `CHOOSE_` sentinels Step 0's
questionnaire prints. Those are asked of every project; this one is only
meaningful under `output.v1_scope: assembled_book` with an `obsidian`
target, and a plain translate+gloss job must not be made to answer an
assembly question it will never read — the same proportionality rule that
makes the whole of Step 0d a no-op under the default scope.

## Pre-read mandate

Before any extraction, prompting, or reviewing work, read (once per
session) the six hard-rule references — `engine-loop.md`,
`false-green-gate.md`, `ledger-and-resumability.md`,
`canon-and-glossary.md`, `verse-policy.md`,
`workflow-schema-validation.md` — plus whichever source/output adapter
this project actually resolves to (Step 0c/0d). Defer the rest — e.g.
`assembly-and-output.md`, `output-target-adapters/obsidian.md` — to the
step that needs them; both sit inert under the default
`output.v1_scope: segment_drafts_and_audit`, and reading them up front pays
for machinery a plain project will never use.

## Hard rules R1–R10

Full content lives in the dedicated reference docs — do not duplicate it
here, follow the linked doc:

- **R1 — Engine-loop role separation.** `references/engine-loop.md`
- **R2 — False-green gate discipline.** `references/false-green-gate.md`
- **R3 — Ledger-based resumability.** `references/ledger-and-resumability.md`
- **R4 — Frozen canon discipline**, including schema-validated
  workflow-level glossary-pass calls only. `references/canon-and-glossary.md`
- **R5 — Verse policy is configurable, never hardcoded.**
  `references/verse-policy.md`
- **R6 — Word-sense/realia accuracy is first-class.** Covered as a review
  dimension in `references/engine-loop.md`. **1.4.0:** this dimension judges
  the DRAFT's word-sense fidelity to the source, never the correctness of an
  already-frozen canon `basis` decision (including `sense_translated`) — a
  suspected-wrong canon entry is RAISED through the glossary/adjudication
  route, never flagged in the per-segment review loop
  (`references/canon-and-glossary.md`,
  `references/orchestration-and-batching.md`'s reviewer carve-out). Raised,
  not reopened: neither route can rewrite a merged entry.
  `glossary_batch_plan.py` excludes every `entries{}` key from the next pass
  and `--retry` overrides only the `review_queue`/dismissed exclusions
  (**#653**), never an `entries{}` exclusion;
  `canon_adjudication_audit.py` never writes a verdict, it blocks. Neither
  route can repair a frozen row itself — that is `canon_validate.py
  --correct`'s job (**#495**), an explicit out-of-band correction that costs
  bounded re-review of the segments referencing that form, never
  re-translation — which is why an accuracy decision, a
  citation included, is reviewed BEFORE the merge
  (`references/canon-and-glossary.md`, "Pre-merge citation review").
- **R7 — Workflow-script schema requirement**, mixed mechanism by path:
  - **W5 translate/review (1.4.7):** codex stays the sole translate/review
    engine (R1) but is LAUNCHED by the shipped, detached `codex_job.py`
    driver, NOT a `codex:codex-rescue` `agent()` call. A plain-Claude
    DISPATCHER prompt (no `agentType`, `effort:'low'`) writes the codex task
    text and launches the driver detached, returning `DISPATCHED <seg> <DISP>`
    immediately; the driver validates the isolated attempt and only then
    atomically promotes it to the canonical
    `segments/<seg>.{draft,review}.json`. A bounded Claude WAIT poll then
    gates on the deterministic on-disk validators — translate: `draft_ready.py`
    AND `validate_draft.py`; review: `review_ready.py` — as the SOLE
    acceptance authority (never the driver's own return or joblog), consuming
    the verdict off disk. **1.16.1 (#348):** that WAIT poll is spent as up to
    8 bounded chunk `agent()` calls plus ONE authoritative non-polling
    re-check of the canonical artifact — the Bash tool clamps any single call
    at 600 s — while the 3450 s wait bound itself is unchanged. No `agent()`
    schema param is involved on this path; the deterministic validators are
    the check.
  - **Glossary/canon-pass batches (unchanged, §6):** each batch is still a
    **schema-less, fire-and-forget DISPATCH** — `agentType:'codex:codex-rescue'`,
    no `schema` param — that writes its verdict to a `{{RUN_ID}}`-scoped disk
    artifact; a bounded Claude WAIT poll, then a schema-validated Claude
    CONSUME/disk-verify call (`canon_validate.py --merge-batches` +
    `CANON_VERIFY_SCHEMA`) reads that artifact back and forces a real
    structured object out of it — never the codex call itself.
    **1.16.2 (#352):** that WAIT poll is now chunked the same way W5's is —
    2 bounded chunks plus one authoritative non-polling re-check, its 900 s
    bound unchanged — so the glossary and skeptic passes no longer differ
    from W5 on this point. The schema shape above is what is unchanged, not
    the poll.
  - **Non-codex mechanical schema-confirmation calls** — `recordLedgerPrompt`,
    `mergeLedgerPrompt`, `verifyReviewArtifactPrompt` — use a `schema` param
    for a different reason (verifying a shell script's own JSON stdout/printed
    line was well-formed, not forcing a codex verdict); none specify
    `agentType:'codex:codex-rescue'`; all run at `effort:'low'` since no
    judgment is involved.
  - Every agent-facing `schema` literal is a flat top-level `object` (`#87` —
    an `agent()` schema is a tool `input_schema`, which cannot be a top-level
    `oneOf`/`allOf`/`anyOf`/`array`).

  `references/workflow-schema-validation.md`
- **R8 — The fix turn is applied in-session, or by at most TWO long-lived
  executors that are never closed between rounds.** R7 governs who calls
  translate/review; nothing governed who EDITS the draft afterwards, and that
  is where an operator's cost silently explodes. Codex cannot do it —
  `--kind review` writes only `<seg>.review.json` and never touches the draft,
  and re-translating converged or hand-corrected text is prohibited — so a
  Claude turn must apply it. Never one spawn per round, per segment, or per
  defect class. **Scope: the HAND-DRIVEN fix turn** — this session, or an
  executor a session hands fix work to, including the driver's `needs_fix`
  stall (below). It is not a statement about `mass-translate-wf.template.js`'s
  `callFix()`, which dispatches one `agent()` per dirty round by design:
  `fixPrompt()` renders that turn's whole editing contract into the prompt and
  the call carries no continuation handle, so there is no warm executor there
  to keep open. **The billable unit is the COLD START, not the round and not
  the concurrency**: twenty sequential spawns cost the same as twenty parallel
  ones, so capping concurrency saves almost nothing, while a warm executor
  re-reads the contract incrementally and a cold one rebuilds it. Measured on
  two books driven through this plugin on the same day: the one that spawned a
  fresh executor per round burned 39.3M cache-creation tokens across 19 spawns
  against its own session's 3.2M, and cost 3.1× the book that applied every fix
  in-session.
  Three corollaries, and they are ONE rule with the above — unsafe apart. **Do
  not respond by enlarging the batches:** small parcels (3–7 loci) are what
  keeps attention on each finding, and executor attention is the only detector
  for a finding whose execution violates another contract rule. Small parcels
  are cheap only BECAUSE the executor stays warm; small parcels with a close
  between them is exactly the configuration that produced the figure above.
  **Do not collapse to a single actor either:** two independent readers exist
  to disagree with the lead's FRAME, not to add hands. Authorization may be
  granted per defect class; the record stays per item, always.
  **And two executors NEVER hold the same segment.** Split the parcels by
  segment and keep that split for as long as a round is open — this is the
  corollary that makes "at most two" safe, and it is the one nothing enforces.
  `runs/.driver.lock` covers a competing driver and
  `segments/.codex_job.<seg>.lock` covers a codex job in its promoting phase;
  **neither covers a fix turn**, which writes `segments/<seg>.draft.json`
  directly — as the `--from-stalled` disclosure below states. That paragraph is
  cited for that fact alone and not for its own race's outcome. What goes wrong is a **lost update**, and it is silent: a fixer
  copies the token it read and rewrites the WHOLE draft, so two fixers off the
  same predecessor end with the later one's text and a finding the earlier one
  already applied is simply gone. **No gate recovers it, and do not expect one
  to.** The reviewed-SHA rebind that `ledger_update.py`, `final_audit.py`,
  `assemble.py` and `validate_assembled.py` each run compares a draft against
  the sha1 the REVIEWER saw; it is a good gate and it does force a fresh review
  once the draft has moved. But a fresh review is a new model pass over the
  surviving text, and nothing carries the earlier round's finding into it — so
  the erased fix may simply not be found again inside `engine.max_fix_rounds`.
  Every gate here proves the reviewer saw the current bytes. None proves that a
  finding already applied to them survived. **This corollary, unlike the
  spawn economics above, DOES bind the `pipeline()` path** — inside ONE
  `pipeline()` invocation it is already handled, since #198's SEGS uniqueness
  guard gives each segment one branch whose fix calls are serial, but nothing
  excludes a SECOND invocation, concurrent or resumed, holding the same
  segment.
- **R9 — A style-contract edit applies FORWARD; a converged segment stays
  converged.** Appending a finding to `style_bible.md` mid-run does not
  invalidate work already reviewed under the previous contract, and must never
  trigger a re-review pass or a back-sweep of earlier segments over the new
  rule. Resetting converged status is an OPERATOR decision, taken only when the
  rules changed radically — never an automatic consequence of one more line.
  The mechanical `converged → stale` flip that follows the edit is a
  bookkeeping consequence of `style_contract_hash` being a cache-key field
  (`references/ledger-and-resumability.md`), not evidence that any prose needs
  rechecking. **Since 1.41.0 the tooling can be told to agree** — set
  `validation.admit_contract_only_stale: true` in `profile.yml` and both the W7
  completeness gate and W9 assembly admit a flipped unit whose `.ever_converged`
  sentinel is not ABSENT (an unreadable or dangling one carves out like a
  present one, as it already does for the machinery-only population), whose
  draft still matches its `reviewed_draft_sha1`, and
  whose only non-machinery moved field is `style_contract_hash`. Nothing is
  rewritten and no hash is stamped: the ledger record still says `stale`, and
  every admitted segment is named on stderr and in each gate's structured
  stdout, so shipping them is a recorded act (`#533`). **The declaration is
  wrong after a REVERSAL, and the tooling cannot tell:** a rule you reversed
  actively demanded the wrong choice in the segments converged under it, one
  global `style_contract_hash` cannot distinguish that from an addition, and
  that is exactly why this is an operator decision rather than a default.
  Undeclared — or declared `false` — every gate behaves as it always did.
  **Since #492 the flip no longer needs a merge to have run since the edit —
  in the direction that ships.**
  It used to: the `converged → stale` reclassification is written by
  `ledger_merge.py`, so an edit landing after the last merge left every record
  saying `converged`, and running W9 without an intervening W7 assembled a book
  whose prose no reviewer had judged under the current contract — silently, as
  a successful run. W9 now re-derives the content-affecting cache-key fields
  from the live root and compares them itself, so the same edit reaches the
  same verdict on either ordering: refused without the declaration, admitted
  and named with it (sentinel condition included). The reverse is untouched: a
  record the ledger already calls `stale` still needs a merge before it can
  ship, whatever the live bytes now say.
  The timing constraint is narrower than it was written here: it is NOT that
  the block only bites "after the last segment converges". **Every** unit that
  converged before the edit is flipped and blocked, whenever the edit lands;
  what timing buys is only that segments converging AFTER it carry the new hash
  and are never flipped at all. So making contract edits early still costs
  least. The move is in the BYTES, not in the number of edits: corrections
  landed together before the loop resumes cost one flip for all of them, while
  the same corrections interleaved with reconvergence cost a flip each. So hold
  pending contract corrections and land them in one edit at a batch boundary —
  the shipped `style_bible.template.md` says this under section E-traps; it
  holds for the whole marked span.
- **R10 — A previous volume is not an input. A new volume takes from exactly
  three places, and the finished book beside it is none of them** — with one
  bounded, marker-only legacy read, defined at the end of this rule. When a
  series gets its next volume, a completed durable root is usually sitting in
  the same tree with working `scripts/`, a filled-in `style_bible.md`, a real
  `profile.yml` and a canon that took weeks. Copying it is the obvious way to
  start and it is the way a book inherits every defect the previous one already
  worked through — silently, because nothing downstream re-reads a decision that
  was COPIED and was right for the last book and wrong for this one. Copying is
  what this rule refuses. Re-ASKING a decision, with its provenance shown, is
  the opposite move, and it is what the ledger below exists for.
  **The three legitimate inputs:** (1) **mechanics** — `scripts/`, `schemas/`,
  workflow and seed templates — come from the PLUGIN: point `durable_root` at a
  NEW empty directory (never one emptied by hand beside a live book) and let
  Step 0a's copy pass fill it from the plugin install path, never a sibling
  root, which is frozen at whatever version that book ran and will not say so;
  (2) **the general contract** comes
  from the shipped `style_bible.template.md` and is then filled in by interview
  — which is what upstreaming a learned rule into the template is FOR; (3)
  **whatever outlives a book** — pending contract corrections, a cross-volume
  name or person registry, and **the decisions the user has made about how to
  work** — comes from the series' own directory, the only place whose contents
  are about the SERIES rather than about one book.

  **The series decisions ledger (#730), the third member of input (3).** A mode,
  an effort tier, a policy the user changed in their own words is not source
  material and it is not canon: R10 has no quarrel with it, and until this rule
  existed R10 discarded it anyway, along with everything else in the finished
  volume. The channel is one file at a MANDATORY relative path,
  `<series directory>/decisions.md`, beside `contract_pending.md`. Not a
  suggested name: a reader told to consult "the series ledger" without a fixed
  path has a hint, not a channel.
  - **Entry shape — five fields, and nothing else is a decision.** One row per
    decision: the dotted `profile.yml` path, the value, the date, the user's own
    words, and the volume it was made in. Anything a reader cannot get from
    those five is a note, not a decision, and belongs elsewhere.
  - **Append-only, and the LAST row for a dotted path is the current decision.**
    A user may revise the same field across volumes — this series already has
    one reading `rhythmic_approximation` in one volume and
    `full_rhymed_plus_literal` in another — so one path accumulates rows, and
    without this sentence volume N is handed two defaults and no rule for which
    holds. Positional, not chronological, precisely because the file is
    append-only: no date arithmetic and no tie-break to get wrong. Earlier rows
    for that path
    are its HISTORY and are never deleted or rewritten — a revision that
    overwrote its predecessor would throw away the reason the decision changed,
    which is the one thing this ledger exists to carry. Surface the current row;
    reach for the earlier ones when the user asks why it changed.
  - **The producer — writing the row is part of making the change.** The moment
    the user changes one of these fields, the row is written; not at the end of
    the volume, not when someone remembers. A ledger nobody writes to is inert,
    and it fails silently: a series that recorded nothing looks exactly like a
    series that decided nothing. The field itself also carries the volume-local
    trace, in this repo's existing convention —
    `# CHANGED by the user, <date>: "<their words>"` plus why it changed. The
    marker is the trace; the ledger is the channel.
  - **Lookup is ledger-first, and EVERY field the ledger holds is re-asked**,
    each from its own current row. Intake reads `decisions.md` BEFORE it relays
    the Step-0 questionnaire. Where the field
    has a printed Step-0 question, the provenance attaches to that question.
    Where it does NOT — `engine.effort` is the plain case, and it is one of the
    fields this ledger most obviously holds — the row is put to the user as a
    standalone provenance-bearing confirmation in the same breath. A row that is
    read and then not surfaced because its field happens to have no sentinel is
    the original defect wearing a ledger: recorded, carried, and silently
    dropped one step later.
  - **The one legacy read, and its exact bounds.** Only when the ledger has no
    row for that field — the ordinary case for a series whose earlier volumes
    predate this rule — may the IMMEDIATELY PRECEDING volume's `profile.yml` be
    read, for its `# CHANGED by the user` markers ALONE. It is read-only, it
    copies nothing into the new profile, and whatever it finds is written into
    the ledger as a row, so it happens once per decision and never again. That
    is the whole exception: not a licence to consult the old book, and not a
    second channel.
  - **What is carried is a question, never a value.** A ledger row must never be
    written into a new `profile.yml` unasked, by a person or by a script. It is
    surfaced by whichever of the two branches above applies to its field —
    beside that field's own printed question where one exists, and otherwise as
    the standalone ask — and it reads the same either way: "tome 1 set this to
    `rhythmic_approximation` on 2026-08-13, at your request: '<their words>' —
    keep it for this volume?" The user answers it again. Nothing is
    inherited without being re-affirmed against THIS text, which is exactly
    R10's own invariant: the reason a decision was right can be about the
    previous book's material and wrong for this one. Showing the reason lets the
    user see that in one line, instead of rediscovering it through review
    findings on a volume already under way.
  **Never copied — the ledger rule above changes nothing here, because its
  legacy read copies nothing and only re-asks — and what each one breaks:** the
  previous `style_bible.md`
  (template plus that book's accretions — rulings whose reasons are gone,
  enforced against a different text); `canon.json` (book-shaped: duplicate
  spellings that resolved to one target *in that book*, a `review_queue` left
  unfrozen for *that book's* cast); `runs/`, the ledger, `segments/`,
  `.ever_converged.*` sentinels, `.codex_job.*` (run state — a stray sentinel
  asserts that a unit converged once, a claim about a book that does not exist
  yet); `profile.yml` verbatim (it carries `v1_scope`, effort and the language
  config of a different source) — unchanged by the ledger rule: no VALUE from a
  previous `profile.yml` is ever copied into a new one, and the marker-only
  legacy read is the sole thing that may open that file at all.
  **And a new volume's `profile.yml` is not hand-authored either** — not
  copied is only half of this entry, and the other half is where the file DOES
  come from: Step 0's existence check is its sole creator, from the plugin's
  own `assets/profile.example.yml` — R10's input (1), mechanics from the
  PLUGIN, not a fourth input — and every INTAKE DECISION in it comes only
  from the user's answers to the questionnaire that same Step 0 run prints.
  (Its other fields — `source.path`, `durable_root`, the source-format and
  adapter knobs — are read off this book's own material, and always were;
  they are not decisions anyone is asked to make.) Writing the file by hand,
  or answering a fresh copy's sentinels from its own inline comments, takes
  every INTAKE DECISION silently and prints exactly what a run that answered
  all of them prints; Step 0's item 1 states that failure in full.
  **The check, because a rule nobody can verify is a wish:** after Step 0a and
  before the first dispatch, `select_segments.py --classify-only` must report
  every unit `not_started`. Anything else means state arrived from somewhere.
  That check covers run state. The scaffold is covered only at the coarse end:
  a wholesale `cp -r` of a finished root brings `.literary-translator-root.json`
  along with it, and Step 0a reads that root marker first, so it halts fatally
  on the different owner (case 4 above). A hand-picked copy into a fresh
  directory brings no marker at all and stops one notch softer, at case 3's
  adoption prompt — which `project.durable_root_adopt_existing: true` waves
  through without anyone inspecting what was copied. That is why the empty root
  above is not a nicety. Past those halts the two directories fail differently,
  and
  neither failure names its cause: `.plugin_bundle_hash` is computed over
  `cache_key.py`'s fixed `PLUGIN_BUNDLE_MEMBERS` allowlist, so a module the
  plugin deleted upstream can sit in a copied `scripts/` forever — still
  importable, invisible to every digest — while `resume_setup.py`'s
  `_schemas_dir_hash()` globs `schemas/*.schema.json`, so a stray schema there
  does move the resume hash and surfaces as a resume mismatch rather than as
  "you copied a neighbour". Neither is worth the minute Step 0a would have
  taken.

## Workflow W1–W9

**W1 Scaffold** — not a copy action itself (Step 0/0a already did all
copying). W1 is the human-facing label for "fill in every placeholder across
the just-scaffolded files" — `style_bible.md`, `PLAN.md` and their siblings.
It does NOT cover `profile.yml`: that file's placeholders are the intake
questionnaire, they were relayed and answered back at Step 0, and Step 0 halts
fatally until they are gone, so W1 can never be reached with one outstanding.
Mechanically enforced,
not just prose: `style_bible.template.md`/`PLAN.template.md` wrap their
must-fill sections in `<!-- LT_REQUIRED_FILL_BEGIN: <id> -->`/
`<!-- LT_REQUIRED_FILL_END -->` marker pairs containing the fixed sentinel
`LT_PLACEHOLDER_UNFILLED`. `scripts/scaffold_validate.py` runs as a hard gate
before W2 begins, with three independent checks: (1) FATALLY halts (naming
file + marker id) if `LT_PLACEHOLDER_UNFILLED` survives inside any marker
span across any scaffolded file — text outside marker spans is not scanned
by *this* check; (2) separately, FATALLY rejects any of the six
Step-0a-copied files (`PLAN.md`/`style_bible.md`/`consistency_issues.md`/
`translate_TASK.md`/`review_TASK.md`/`glossary_TASK.md`) that still contain
an unfilled inline bracket placeholder (`[SOURCE LANGUAGE]`, `[TARGET
LANGUAGE]`, `[PROJECT TITLE / AUTHOR / PERIOD -- fill in]`), matched as a
closed, exact list rather than a generic `[...]` shape so a translator's own
legitimate editorial brackets are never blocked; (3) FATALLY rejects
`translate_TASK.md`/`review_TASK.md` if the shipped illustrative era/domain
trap example survives a copy-paste into a new project — checked two ways,
an exact-substring match on the literal `guéridon=refrain-song` plus a
co-occurrence check (scoped to the callout's own HTML comment) catching a
separator-mangled or partially-deleted survivor the exact match alone would
miss — deliberately not marker-gated (traps are discovered during the run,
nothing to require at W1).

**W2 Extract** — run the resolved source-format adapter's extractor
(spine/footnote/verse detection per Step 0c). Currently that means either
adapt-and-run `extract.py.template` for `gutenberg_epub` — the one working,
source-fidelity-proven adapter — or run the expert-mode `custom` extractor
co-designed per Step 0c; `source.format: plain_text` is specified but not
yet implemented and `extract.py.template` FATALs on it (#62). Either way,
the extractor's own blocking self-checks (bijection, uniqueness,
coverage-no-holes, spine-order, segmentation-nonempty, sentinel-uniqueness,
front-back inventory, verse-structure, `no_segment_exceeds_max_words`, or a
documented equivalent for `custom`) must be green before anything downstream
runs. Plus a `manifest.schema.json` validation pass immediately after
extraction using the real `jsonschema.Draft202012Validator`.

Then a MANDATORY managed post-extraction gate: the producing extractor's
in-file self-checks live in a hand-adapted/hand-written file and could be
silenced to fake green, so they are never the last word. After extraction
produces `manifest.json`, run:

```
python3 {{PLUGIN_ROOT}}/assets/scripts/validate_extraction.py --manifest ${durable_root}/manifest.json --extract ${durable_root}/extract.py --profile .claude/literary-translator/profile.yml
```

from the plugin's own install path — never a durable-root copy (same
exception class as `profile_validate.py`; it is deliberately not a bundle
member and never adapted per-project). It independently RE-DERIVES the
manifest-derivable invariants directly from `manifest.json` (so a hand-edited
extractor that skips or fakes its own enforcement cannot manufacture a green
manifest) and, for `gutenberg_epub`/`plain_text`, pins `extract.py`'s
self-check region by hash. **For `custom`, the region-hash pin is SKIPPED**
(not merely trivial): Step 0a copies `extract.py.template` to `extract.py`
unconditionally even for `custom`, but that copy is never the real extractor
(the co-designed one lives at `scripts/custom_extractors/<value>`), so
pinning it would certify nothing — only the manifest-derivable
re-derivation runs for `custom`, against the manifest the real extractor
produced. See `references/source-format-adapters/custom.md` and
`references/false-green-gate.md` for the full reconciliation. The pipeline
advances to W3 ONLY on its exit `0` (see R2 / `references/false-green-gate.md`).

### Visual-order source — the advisory this gate may print (1.46.0, #489)

The gate can also print, on stderr, a **`WARN visual_order_scan:`** line, and
name the advisory count in its final status. It is REPORT-ONLY: it never
changes the exit code in either direction.

It means a source EPUB is probably in **visual order** rather than logical
order — the usual result of a PDF-to-EPUB conversion. Extraction is
byte-faithful and correct; the mangling is upstream. **Do not send a fix to
the extraction stage.** The damage lands on the LLM turns instead, and on a
live book it reached a converged draft a full review round had already called
clean. `references/false-green-gate.md`'s "The visual-order advisory" section
carries why no deterministic gate can catch this class.

**The scan is a SCREEN, not a verdict.** It detects visual-order *handling* (a
terminal punctuation mark leading an RTL token, which logical order cannot
produce), not the word *reordering* that actually tears tokens. Adjudicate it:

1. Read the sampled units the WARN names, in the manifest, against the source.
   The sample is printed as `\uXXXX` escapes on purpose — **never judge RTL text
   by looking at it**, because a bidi terminal renders a corrupted token
   identically to an intact one. Settle it on the codepoints.
2. **Negative** — the signature fired on something benign: record that in the
   project's notes and carry on. Nothing else to do.
3. **Positive** — paste the clause from `references/gotchas.md` §15 into the
   project's own `style_bible.md`, under `### E-traps`. That is the one place all three turns
   read: the translator reads the style bible in full, the reviewer names it as
   style authority, and the fix turn reads it before editing.

**Before pasting, know what it costs.** Editing `style_bible.md` moves
`style_contract_hash`, which mechanically flips every unit ALREADY converged in
that project to `stale`. R9 means the edit applies FORWARD — nothing needs
re-reviewing — but with `validation.admit_contract_only_stale` false, W7 and W9
will refuse until you set it. On a fresh project this costs nothing; on a
partly-converged one, decide deliberately.

The clause is **not** shipped in `style_bible.template.md`, and this copy is
**not operative** — it is text for the operator to paste after a positive
adjudication, never an instruction to any turn. A book whose source is in
ordinary logical order must never receive it: telling that project's reviewer to
discount torn-token findings would suppress real defects.

The clause itself — a fenced `#### E-traps: visual-order source` block, ready
to paste unaltered — is `references/gotchas.md` §15. Open that file ONLY on a
positive adjudication: a book in ordinary logical order must never receive
the clause, and reading it is not the same as being entitled to paste it.

### An empty content unit is refused at W2 (#397)

`run_derivable_checks` refuses two shapes that used to pass W2 and left their
segment convergeable **only on an invented-text draft** — the faithful draft
rejected forever, and nothing said so until a paid translation job had run:

- **`no_untranslatable_empty_blocks`** — a block cited by a segment's
  `block_ids` whose `plain_text` is empty while its `source_html` is not —
  usually a purely structural node (a scene-separating `<hr>`) emitted as a
  content block. `validate_draft` falls back to `source_html` when `plain_text`
  is falsy, so the faithful empty draft block reads as an empty translation.
  Runs under every `apparatus_policy`. Not refused: a block that is the parent
  of **exactly one** non-embedded verse, legitimately carrying no text of its
  own; and *whitespace-only* `plain_text`, truthy, so the faithful empty draft
  already converges.
- **`no_empty_footnote_definitions`** — a footnote whose definition block
  carries no text, *whitespace-only included*. Runs only under
  `footnotes.apparatus_policy: translate_all | preserve_source`, the two
  policies where footnote text reaches a segpack at all. A footnote's
  `source_text` takes its text from `plain_text` alone — `source_html` is no
  defence — and a blank footnote translation is refused unconditionally.
  #725's carry of the block's own `<i>`/`<em>` from `source_html`, normalised
  to a bare `<i>` (see “Footnote emphasis” below), applies only where
  `plain_text` is non-empty, so it never reaches this gate. **No reachability
  filter, by design:** an empty definition is refused even where no segpack
  would have carried it — the remedy is the same either way, and both attempts
  to model reachability let the defect through.

**What to do when one fires.** The gate names the offending block ids or
footnote numbers. Adapt `${durable_root}/extract.py` so the node is not emitted
as a content block (or the empty definition not emitted), then re-extract. Do
NOT edit the check: the failure is real, and cheaper here than after a
translation round.

### Footnote emphasis reaches the translator in the source's own notation (#725)

A **body** block reaches the translator as raw `source_html`, so the source's
own `<i>`/`<em>` is visible to it. A **footnote definition** carried
markup-stripped `plain_text` only, so under the two apparatus policies that
exist precisely to translate the apparatus, the translator was asked to
preserve italics it had never been shown — while the same span sat in a body
block one field away. Measured on a live volume: 214 of 493 definition blocks
carried emphasis, 370 spans, every one dropped, and on one segment 13 of 31
first-round review findings were "the source italicizes X, the translation
leaves it roman".

`segpack.py` now carries that emphasis into `footnotes[].source_text`, in the
**source's own notation**: `<i>`/`<em>` survive, normalised to a bare `<i>`
(attributes dropped, `<em>` spelled `<i>`), and every other tag is removed.
Two consequences worth knowing:

- **`source_text` is an UNDECIDABLE UNION of two encodings — do not try to
  fold it back.** A definition whose emphasis was carried is an HTML fragment
  with its entities still escaped; one with no emphasis, or one that could not
  be carried, is `plain_text` verbatim and may itself contain a literal `<i>`
  or a bare `&`. Nothing in the string says which you are holding, so a
  consumer that strips `</?i>` and unescapes corrupts the fallback case,
  inventing text that was never there. **A consumer that needs the
  definition's exact, unambiguous text reads `manifest.json`'s own `blocks{}`
  `plain_text`**, and one deciding whether the source marks emphasis reads
  that block's `source_html`.
- **It never mangles the text, and never invents emphasis.** Three checks
  decide and any failure returns `plain_text` unchanged, so emphasis can be
  LOST but never invented or reordered.

Read `references/w2-gate-disclosures.md`, "Footnote emphasis (#725)", before
writing any consumer of `footnotes[].source_text`, before proposing markdown
`*...*` for this again (it records the three ways that design failed), and
before touching `segpack.py`'s tag classifiers.

**Name candidates are unaffected.** The candidate scan still reads the
definition's `plain_text`, never the emphasis-carrying `source_text` — `>` is
the `preceding_char` `tokenize()` records for the token after it and `WRAPPERS`
does not skip it, so scanning the marked text would read a sentence-initial
name as mid-sentence and promote it into `names[]` for the wrong reason.

**Migration.** `segpack.py` is a `derivation_bundle_hash` member, so at the
next Step 0a refresh existing segments are classified
`blocked_needs_regeneration` and the mass-run resume digest moves. Regenerating
the segpacks then moves `note_map_hash` for every segment whose emphasis was
carried, and the `canon.json` restamp that clearing the derivation mismatch
requires also moves the glossary resume digest and any existing skeptic /
suspicion state. Cheapest for a project that has not started W5.

### Oversized source block — the census this gate always prints (#504)

The gate also prints, on stdout, one **`NOTE block_size_census:`** line for
every schema-valid run, clean or not — e.g. `NOTE block_size_census: n=1212
blocks, median 6, p90 851, largest 17896 characters; artifact threshold 10x p90
= 8510 (1 block(s) at or above it). largest: PARA:seg21:0001=17896; ...`. When
at least one block crosses the threshold, it additionally prints a **`WARN
block_size_census:`** line on stderr and adds to the advisory count in the
final status line; the NOTE alone never does. Both are REPORT-ONLY: they touch
neither `derivable_ok` nor `region_ok`, so this census can neither refuse an
ingestion nor rescue a failing one.

It means one of the blocks some segment claims — a member of that segment's
`block_ids` — is disproportionately large next to the rest of this book's own
blocks. The usual cause is a wrap/extraction artifact: a converter joining a
whole narrative, or several paragraphs, into a single block.
`references/false-green-gate.md`'s "The block-size census" section carries the
rest — the p90 reference and the 10x threshold, which blocks the population
excludes, the silence below 30 blocks, the false-negative classes, and why a
genuinely long paragraph is indistinguishable from an artifact here.

Adjudicate a WARN the same way as the visual-order advisory above:

1. Read the named block(s), in the manifest, against the printed source.
2. **A genuinely long paragraph** — record nothing, carry on.
3. **An extraction artifact** — the source block is not authoritative
   structure, so this segment's translation may need to reflect the printed
   book's real paragraphing rather than the extractor's block boundary. Record
   that finding, with the census figures as evidence, in that segment's own
   draft `notes[]` array (see `review_TASK.template.md`), never in the
   manifest. At W2 no draft exists yet, and a `manifest.json` segment object is
   `additionalProperties: false` with no `notes` field to write into — carry
   the finding forward to the translate turn, which is where the draft, and its
   `notes[]`, first exist.

One exception to "always": if the census cannot be BUILT -- a malformed
`blocks{}` the mandatory checks own, say -- no NOTE is printed and the failure
is named as a `WARN block_size_census: scan unavailable` advisory instead, so
the absence is reported rather than silent. A census that could not be
*printed* stays silent: nothing is invented about a run whose stdout failed.

**Nothing is re-paragraphed for you.** Re-cutting a block changes the segpack's
block-key set, and `validate_draft.py` locks that key set 1:1 against the draft
— re-extracting to fix an artifact would force every already-reviewed draft to
be re-cut. This gate names the outlier; it does not act on it, and it does not
ask you to edit `style_bible.md`.

**New in 1.12.0 (#210) — two additional HARD checks land in
`run_derivable_checks`**, both exit `1`, both running unconditionally,
including for `source.format: custom` (only the extractor self-check
region-hash pin above is format-conditional):

- **Heading levels, cross-field validity.** The manifest may declare an
  optional `heading_levels` map (`{block_type: level 1-6}`, sibling to
  `heading_types`, schema-accepted by the
  `jsonschema.Draft202012Validator` pass above) so assembly no longer
  hardcodes every heading to markdown `##` — `render_obsidian.py` emits
  each heading node at its own resolved level, defaulting to level 2 for a
  type absent from the map (or an absent map entirely), byte-identical to
  pre-1.12.0 output. Every key of `heading_levels` must be a member of
  `heading_types ∪ {"HEAD"}` — a key outside that set is a typo that would
  otherwise silently no-op, so it fails loudly here rather than sitting
  unused. `assemble.py` enforces the identical rule again, independently,
  as an `AssembleError`, since it must not trust that W2 ran — it is also
  reachable on a resumed project. See `references/assembly-and-output.md`'s
  BlockNode contract.
- **Undeclared heading-shaped type.** This check fires only when the
  manifest omits the `heading_types` key entirely — a bare absence, never
  an explicit `[]` — AND at least one `manifest.blocks[*].type`
  full-matches, case-insensitively, the heading-shaped allowlist
  `^(?:HEADING|TITLE|CHAPTER|SECTION|PART|SIMAN|PEREK|H[1-6])$` — the same
  literal pattern `validate_assembled.py` already used for its WARN-only
  backstop, now duplicated byte-identically (this plugin's no-shared-util
  convention) and pinned against drift by
  `tests/heading_like_regex_drift.test.py`. An explicit
  `heading_types: []` is a positive declaration and always passes.
  `"HEAD"` never matches the allowlist, so every shipped
  `gutenberg_epub`/`plain_text` project — which tags headings `"HEAD"` and
  never sets `heading_types` — is untouched. The error names every
  offending type plus both remedies: declare it in `heading_types`, or set
  `heading_types: []` to affirm this source has no heading blocks at all.
  This closes the silent half of #210: previously an extractor tagging its
  own heading blocks (e.g. `"CHAPTER"`) and forgetting to declare the tag
  shipped silently, with no error until the WARN-only
  `validate_assembled.py` backstop caught it at W7/W9 — after the whole
  book had already been translated. See
  `references/source-format-adapters/custom.md` for the full walkthrough.

### Heading-level outline — the disclosure this gate prints (#233)

The gate also prints, on stdout, one **`NOTE heading_level_outline:`**
line whenever `U` -- the `manifest.blocks[*].type` values some segment's
`block_ids` names AND `heading_types ∪ {"HEAD"}` contains -- is non-empty.
Every cited tier carries the level `assemble.py` will really resolve for
it (`heading_levels[type]`, else 2 when that entry or the whole map is
absent), marked `declared` or `default`: `NOTE heading_level_outline: 3
heading tier(s) cited: "HEAD"=1 (declared), "PEREK"=2 (default), "SIMAN"=3
(declared)`. When `U` holds two or more tiers AND at least one is
`default`, a **`WARN heading_level_outline:`** line follows on stderr and
counts in the final `(N ADVISORY)` status. A single cited tier never
warns, and neither does a book whose every cited tier is `declared` --
including two tiers deliberately declared at the SAME level. The WARN
fires ONLY on the two-or-more-tiers-with-a-`default` shape, because that
is the one shape where a forgotten declaration and a genuinely flat
two-level outline look identical from here. Both lines are REPORT-ONLY,
exactly like the two advisories above: they touch neither `derivable_ok`
nor `region_ok`, so this disclosure can neither refuse an ingestion nor
rescue a failing one.

`assemble.py` resolves each heading from that same map the identical way
and nothing downstream re-derives it, so a cited tier you never declare
renders at level 2 with no further signal anywhere in the pipeline. **A
SCREEN, not a verdict** -- adjudicate it like the advisories above: read
the tiers the WARN names against the markdown outline you actually intend
for this book, then either declare a level in `heading_levels` for every
tier this book renders (a level of 2 states the default in writing) or
leave it and carry on. Neither changes whether this gate passes.

**The residual.** A flattened or mis-nested outline still exits `0`,
because nothing in a schema-valid manifest separates "the operator
deliberately took the default" from "the operator forgot this tier" and a
hard check here would refuse legal books. #233 stays open; this ships the
disclosure only.

Then, immediately after `validate_extraction.py` passes, run the
**wrapper-conservation gate (#196)** — a normal bundle-copied durable-root
script (unlike `validate_extraction.py` above), so run the durable copy:

```
python3 ${durable_root}/scripts/validate_conservation.py wrapper-conservation
```

This is **opt-in**: a no-op (prints a NOTE, exits `0`) unless `profile.yml`
declares `source.conservation` — only a source hand-wrapped into this project's
format from some other pre-wrap form, with the exact pre-wrap text preserved as
an immutable baseline, has one. When it IS declared the gate is HARD: exit `1`
on any defect, and the pipeline advances to W3 ONLY on exit `0`. What it
compares, at what granularity, and the three defect classes it catches (dropped
baseline content, a truncated/hollowed block, `reading_order_reversal`) are in
`references/w2-gate-disclosures.md`, "Wrapper conservation (#196)", and in
`validate_conservation.py`'s own module docstring. Read either only when
`source.conservation` is declared.

**W3 Bootstrap style bible + language smoke test.** After W2 produces
`manifest.json`, W3's own procedural code (never `profile.schema.json`)
computes three hashes: the resolved `particle_config` file's content hash, a
hash of a representative sample of this project's own extracted source
text, and `language_smoke_report.py`'s own `smoke_report_contract_hash`.
Resolve `report_path` (derive `${durable_root}/runs/language-smoke-report.json`
if null); check for a report recording all three hashes matching currently.
A brand-new project reusing the unmodified `fr.json` against a different
book still requires its own fresh smoke test.

If no matching report exists, run the mandatory smoke test: run
`bootstrap_names.py` against a real text sample, hand-pick a checked-name
list, prepare elision test sentences if `has_elision`, prepare particle-smoke
cases whenever the resolved preset's `particle_list_size > 0` (unless the
particle-free `--no-particles-confirmed` path applies), run
`scripts/language_smoke_report.py` to compute all three hashes, check every
hand-picked name against the extractor's actual output, run
elision/particle test cases, write a `language-smoke-report.schema.json`-
shaped JSON report with `pass:true` only if every checked name found, every
particle-smoke case passed, and every elision test passed. A
stale/mismatched report on any of the three hashes, a `pass:false` report, or
a mismatched `has_elision` value, is treated as no report at all.

On an uncased-script source (Hebrew/Yiddish/Arabic — no `Lu` uppercase
letters), `pass:true` here certifies only what `bootstrap_names.py`'s
`Lu`-gated candidate detector could reach, never native-script name
coverage — see the uncased-script caveat in
`references/language-pair-parameterization.md` before trusting it. Since
1.10.0, `name_inventory` matching (both extractors) is mark- and
connector-insensitive (issues #238/#241): an unpointed, space-joined
inventory entry also matches a pointed and/or maqaf/geresh/gershayim-joined
occurrence in the source text, and vice versa — but the CANDIDATE that gets
recorded and reaches `canon.json`'s own key is always the raw surface form
exactly as the source spells it at that occurrence, never the folded or
inventory-canonical spelling. See
`references/language-pair-parameterization.md`'s "Uncased-script
`.local.json` + `name_inventory` example" for a generic worked walkthrough
of setting up a project-local override.

**`glossary.enabled: false` — the project declared it wants no researched
name/realia canon at all.** The mandatory language smoke test above still
runs: W3a's `segpack.py` re-runs `bootstrap_names.py`'s own candidate
extractor over every segment and W5 acts on the `new_names` it produces, so
name detection stays load-bearing even against an empty canon. What this
branch skips is the research and adjudication that follow the smoke test
below: no `glossary_batch_plan.py`, no `resume_setup.py`, no glossary
Workflow, no canon merge — and the skeptic pass documented in
`references/skeptic-pass.md` does not run on this branch whatever `glossary.skeptic_pass.enabled` says,
because Step 0 fatally refuses that contradictory combination outright
before scaffolding ever reaches here. Bootstrap the canon exactly as the
`no_new_candidates` SKIP branch further below does:

```
python3 ${durable_root}/scripts/canon_validate.py \
  --research-mode <profile's glossary.research_mode> --init \
  --plugin-root {{PLUGIN_ROOT}}
```

then rejoin at the mandatory homonym-split evidence gate below, exactly
like every other branch. `--init` is create-only: a project whose
`canon.json` already exists keeps it byte-untouched and keeps injecting its
entries — `glossary.enabled: false` never discards a canon a prior run
already built. On a rerun after a plugin upgrade moved a
`derivation_bundle_hash` member, use the sanctioned restamp route —
`canon_validate.py --restamp-derivation` FIRST, then re-run `segpack.py`
(that order, never reversed: `segpack.py` copies `canon.json`'s stamp
forward, so running it first only re-copies the stale value) — exactly as
`references/ledger-and-resumability.md` documents for the SKIP branch.

Otherwise (`glossary.enabled` not false, the default), run `bootstrap_names.py` (configured from
`${durable_root}/languages/<particle_config's literal value>` — never
rebuilt from `source.language.code` alone) to get frequency-ranked name
candidates. **1.3.5:** curate and batch those raw candidates with
`scripts/glossary_batch_plan.py` FIRST — it reads `name_candidates.json` plus
the current `canon.json`, drops every candidate already resolved there (an
`entries{}` key, a non-retried `review_queue[].source_form`, OR (**#653**) a
non-retried dismissed `source_form` from `corrections[]` — the #101 filter,
now enforced in code, not merely delegated as prose), curates the
survivors by `likely_name`/`--min-candidate-freq` (the profile's
`glossary.min_candidate_freq` when set, else 2), force-includes any
`elision_ambiguous` pair for adjudication (#91), and prints one JSON line. If
that line is `{"no_new_candidates": true, "batches": []}`, every candidate is
already in canon — or, on an uncased-script source whose preset ships no
`name_inventory`, there were never any candidates to begin with — so SKIP
`resume_setup.py` and the glossary Workflow entirely this run, nothing to
research. **#290:** that SKIP branch is the one GLOSSARY-ENABLED W3 path that
never reaches the glossary merge — and apart from the bootstrap command below,
the merge is the only thing that ever CREATES `canon.json` — so bootstrap it
explicitly here, or W3a below dies with `FATAL: canon.json not found`:

```
python3 ${durable_root}/scripts/canon_validate.py \
  --research-mode <profile's glossary.research_mode> --init \
  --plugin-root {{PLUGIN_ROOT}}
```

That writes an empty-but-stamped `canon.json` (`entries: {}`,
`review_queue: []`, both `generation_hashes` computed by the same
`cache_key.py` a real merge would use, which is exactly what `segpack.py`
copies into every pack). It is create-only: on a project whose `canon.json`
already exists it leaves the file byte-untouched and reports
`"created": false`, exit `0` — so run it unconditionally on this branch. Never
hand-roll the file instead: `segpack.py` rejects a `canon.json` whose
`generation_hashes` fields are absent, and invented values would propagate
verbatim into every pack that `select_segments.py`'s derivation-state gate
later reads.

**#412 — a stamping mode now REFUSES to guess which `cache_key.py` to
trust.** `--init` is one of `canon_validate.py`'s four
`generation_hashes`-STAMPING modes (`--init`, `--restamp-derivation`,
`--merge-batches`, and the legacy bare `--batch` merge), and every one of
them now halts with an argparse error (exit `2`) unless it is handed either
`--plugin-root PATH` or the explicit escape hatch
`--allow-durable-sibling`. Passing both is itself an error: naming a
trusted plugin root and waiving the requirement to name one state two
different intentions. That is why the command above carries `--plugin-root
{{PLUGIN_ROOT}}` — stamping shells out to a sibling `cache_key.py`, and
left to self-anchor that sibling comes out of `${durable_root}/scripts/`,
which the codex processes this pipeline launches hold `--write` over. A
tampered copy sitting there would forge the very hashes that later gate
canon reuse, and the run would report green rather than halt. The refusal
is deliberately NOT a check that every call site is spelled correctly —
the #582 paragraph in W5 records that enumerating call sites does not
converge — but a refusal to proceed without an ANSWER, so a call site that
never learned about the flag halts naming both options instead of stamping
through whatever `cache_key.py` happens to be on disk.
`--allow-durable-sibling` is the sanctioned opt-out for a hand-run
recovery with no orchestrating session to supply a plugin root: it accepts
the durable sibling knowingly, on the operator's own judgement, rather
than silently. `canon_validate.py`'s NON-stamping modes — `--check-batch`,
`--correct`, `--verify-merged`, and validate-only (no mode flag) — resolve no sibling at
all and are unaffected; do not add either flag to them. `--correct` is the one
that WRITES `canon.json` without stamping it, so the four modes named above are
the STAMPING ones, not every mode that writes.

**#495 — correcting a canon entry that is simply WRONG.** `canon.json` is
otherwise write-once: `--merge-batches` refuses a differing resolution for an
already-frozen `source_form` (correctly — that guard is what stops a
re-adjudication pass silently overwriting a frozen decision), `--init` is
create-only, and validate-only writes nothing. The sanctioned route is
`canon_validate.py --research-mode offline --correct correction.json`, where
`correction.json` is one `canon-correction.schema.json` document naming the
`source_form`, stating its CURRENT on-disk value as `old_entry` (the call is
refused, naming both values, if that does not match — so it cannot be used
blind), carrying a required free-text `reason`, and dispositioning either
`correct` (replace) or `remove` (delete — what an interpolated name with zero
source occurrences needs, and the only repair
`canon_adjudication_audit.py`'s BLOCKING `collapsed_split` accepts). The
document is appended verbatim to `canon.json`'s `corrections[]`, so the change
is recorded rather than arriving as an unexplained diff. Never hand-edit
`canon.json` instead: that is a change to the artifact every gate downstream
trusts, made outside every validation this plugin owns. A correction re-stales
exactly the segments whose segpack references that form — bounded re-review via
`--from-converged`, never re-translation — but it does change `canon.json`'s
bytes, which the skeptic pass holds as a frozen input, so run it BETWEEN passes.
Full contract: `references/canon-and-glossary.md`, "`--correct PATH`".

**#653 — dismissing a `review_queue[]` candidate that is simply NOT
canon-worthy.** `correct`/`remove` adjudicate `entries{}`; nothing adjudicated
`review_queue[]` on its own terms — the only way to drain a queued row was to
accept it, which freezes an `entries{}` record, so "a human looked at this and
it isn't canon material" had no spelling short of accepting a bad entry just
to clear the queue. Same command, third `disposition`:
`canon_validate.py --research-mode offline --correct dismissal.json`, where
`dismissal.json` names the `source_form`, states the queued row itself as
`old_item` (a bare string, or `{source_form: ...}` — whichever shape is
actually on disk), carries `reason`, and sets `disposition: "dismiss"`. The
row is removed from `review_queue[]` only — `entries{}` is untouched, even
when the same `source_form` also happens to be an `entries{}` key: a DICT
queue row sharing an `entries{}` key IS an invalid, refused state on its
own (the whole-file overlap check catches it), but `dismiss` REPAIRS it,
because that check runs against the POST-dismissal document — the offending
row is already gone by then. A bare-string row sharing an `entries{}` key
is invisible to that same check either way (it only inspects dict rows) and
instead fails the queue-item schema, a separate malformed-file case.
`--retry` is still the only way back: it
lifts the exclusion a dismissal leaves in `corrections[]`, exactly as it
lifts a queued exclusion, and neither retry forces a name past ordinary
candidate curation. Full contract: `references/canon-and-glossary.md`,
"`--correct PATH`", the `disposition: "dismiss"` bullet.

Otherwise run the codex-glossary-pass,
instantiating `glossary-pass-wf.template.js` fresh from the plugin's current
copy every time — batched over `${durable_root}/glossary_TASK.md`, feeding the
planner's `args` into the Workflow tool and its `batches` into
`resume_setup.py`'s payload. **#396:** this instantiation is one of the
operations covered by the W5 rule below that verifies the bundle markers
against the live plugin tree — see that rule before instantiating here.
**#197:** the same instantiation substitutes
`{{EFFORT}}` (`profile.yml`'s `engine.effort`) alongside every other token —
there is no `{{MODEL}}` token for this template, since a codex model id
does not thread to the glossary pass. **1.16.1 (#347):** that token list gained
`{{CITATION_CONTENT_TYPES}}`, substituted as a COMMA-SEPARATED string inside its
own quotes from `profile.yml`'s `glossary.citation_content_types` — the empty
string when the key is absent or empty, meaning `fetch_citation.py`'s shipped
default. It is REQUIRED, not optional: leaving it unsubstituted throws at
instantiation rather than silently falling back, because a profile setting that
quietly did not take effect is the exact failure this release exists to close.

**#412:** that same instantiation ALSO substitutes `{{PLUGIN_ROOT}}` into
`glossary-pass-wf.template.js` — this skill's own directory, the SAME value
Step 0 already defines (`${CLAUDE_PLUGIN_ROOT}/skills/literary-translator`),
reused here, never redefined — and **not** `${CLAUDE_PLUGIN_ROOT}` itself,
which is the wrong value: an installed plugin has no `assets/` at its root,
so the sibling lookup would name a directory that does not exist. Unlike
this skill's PROSE occurrences of `{{PLUGIN_ROOT}}` — which a reader
substitutes on the fly when typing an example command — this one is a
literal Workflow-template token, exactly like `{{EFFORT}}` and
`{{CITATION_CONTENT_TYPES}}` above: it must be written into the
instantiated `glossary-pass-wf.template.js` file itself. The template
threads it onto the single serialized `canon_validate.py --merge-batches`
call it builds — the one command in this pass that STAMPS
`canon.json`'s `generation_hashes` — so it decides where that call resolves
the sibling `cache_key.py` from: the plugin's own install tree, which the
codex agents this pass dispatches cannot write to, instead of
`${durable_root}/scripts/`, which they can. **Omitting this substitution is
not a neutral default: it leaves the pre-#412 vulnerability open** — a
codex-tampered `cache_key.py` in `${durable_root}/scripts/` would forge the
provenance hashes that later gate canon reuse, and nothing downstream would
catch it. Always substitute it.

**#724:** that token list gained `{{RESUMED_BATCH_INDICES}}` — a BARE JSON array
literal (`[]`, `[0, 3, 7]`), outside any quotes — copied verbatim from the
`resumed_batch_indices` key `resume_setup.py` reports for a glossary run. It is
the batches whose attempt-0 fragment that script re-checked with
`canon_validate.py --check-batch` and found valid, so the pass may skip their
codex dispatch and wait. `[]` is the ordinary value on a fresh run, and copying
it is REQUIRED: the template refuses a non-array, and substituting `[]` where
the script reported indices does not corrupt anything — it just re-dispatches
batches that were already done, which is the expense this token exists to
avoid. This is the one token whose value is not known before `resume_setup.py`
runs, which is why the instantiation happens after it, not before — the same
ordering `{{RUN_ID}}` already forces. It replaces a per-batch `glossary:precheck`
agent call that asked the same question in prose; nothing in the pass answers it
at run time any more.

**A resumed batch is still citation-reviewed.** The skip is over DISPATCH and
WAIT only: both entry points converge on the same PREPARE → JUDGE ladder before
anything merges. Do not read this token as an approval record.

The resolved `effort` value also
belongs in the `subst` object of the payload this session writes for
`resume_setup.py` below — `resume_setup.py`'s own `SUBST_FIELDS` now
requires it, and `compute_input_digest` fails loudly
(`missing required field(s): ['effort']`) if it's omitted. **The same is true of
`citation_content_types` since 1.16.1**, and for a reason worth stating rather
than pattern-matching: it changes the prepare step's actual command line, so it
changes what a cached citation verdict MEANS. Widening the list makes the
boundary admit pages it previously refused, and a resumed run that reused those
verdicts would be reporting decisions taken under the OLD policy as current.
Pass it even when it is the empty string — the empty string is itself the
statement "this run used the shipped default". **1.4.0:** on that same non-empty-candidates
path, after `glossary_batch_plan.py` and strictly before `resume_setup.py`
runs (and before any dispatch), invoke the glossary staleness preflight:

```
python3 {{PLUGIN_ROOT}}/assets/scripts/glossary_preflight.py --durable-root ${durable_root}
```

Exit `0` (stdout `{"preflight":"ok"}`) means this project's durable
`schemas/` and `glossary_TASK.md` already teach `basis:"sense_translated"`
correctly — proceed. **Any non-zero exit HALTS immediately, dispatching
nothing**, and surfaces the script's one-line stderr message verbatim: a
schema-axis failure means re-run Step 0 + Step 0a to refresh
`${durable_root}/schemas/`; a prompt-axis failure means hand-re-apply the
current `glossary_TASK.template.md` into `${durable_root}/glossary_TASK.md`
(never auto-overwritten, per item 12 above) and bump its
`PROMPT_CONTRACT_VERSION` marker. **Since 1.65.0 that axis also fails on
CONTENT at an unchanged marker** (`#510`): a durable copy that does not carry
the shipped prohibition on writing `style_bible.md` halts, because the old
copy's instruction to log a discovery into E-traps survives a marker
comparison. Its remedy is the paragraph, not the marker — apply the current
template's word-sense paragraph and leave the marker alone. Without this gate, a resumed project whose
durable schemas or prompt predate this basis value would either reject a
`sense_translated` batch item outright or never teach the agent the value in
the first place — this preflight is run fresh on every dispatch (never
cached against a resumed run's `input_digest`), so the operator's remedy
takes effect on the very next attempt with nothing stale left to replay.
Run only on the glossary path — the mass/W5 path never validates `basis` and
so cannot hang this way. **1.2.0:** on that non-empty path, before ever
calling `pipeline()`, a deterministic pre-workflow step invokes
`resume_setup.py` (kind `glossary`) — it resolves `effectiveRunId` via the
resume-integrity digest gate, creates `glossary/runs/<RUN_ID>/`, and
atomically writes each batch's `manifest_{index}.json` plus the aggregate
`manifest_all.json`, aborting before any dispatch if any of that fails (see
`references/orchestration-and-batching.md`). **#724:** it then reports
`resumed_batch_indices` — the batches whose attempt-0 fragment it re-checked
with `canon_validate.py --check-batch` and found valid. That happens LAST, after
the stale-attempt wipe and after the manifests exist, because those are what
make the answer a fact; copy it into `{{RESUMED_BATCH_INDICES}}` when you
instantiate the template (see above).

**Dispatch path — `glossary_dispatch_driver.py` is the DEFAULT since 1.75.0
(#800); the `pipeline()` template path below is the retained FALLBACK.** A
Workflow script cannot run bash, so every deterministic step of this pass —
reaching codex, polling the fragment, snapshotting it, fetching its citations,
recording a verdict — costs a full `agent()` call whose only content is "now run
this command". Measured on a live 22-batch volume: **~130 agent calls against
24**. The driver runs those steps locally and hands back only the ONE step that
must stay an agent, the citation judge. It changes no gate: it obtains every
prompt and every command by executing this same template's own builder functions
under Node, so both paths issue byte-identical commands.

```
python3 ${durable_root}/scripts/glossary_dispatch_driver.py \
  --run-id <RUN_ID> --batches-file <the planner's args array, as JSON> \
  --plugin-root {{PLUGIN_ROOT}} \
  --verdict-dir <a session-owned dir OUTSIDE ${durable_root}> \
  --source-lang <X> --target-lang <Y> --research-mode <live|offline> \
  --effort <engine.effort> --citation-content-types <same value as the token> \
  --batch-agent-cap <engine.batch_agent_cap> \
  --resumed-batch-indices '<the same array as {{RESUMED_BATCH_INDICES}}>'
```

There is deliberately no retry knob: the ladder bound is READ from the template's
own `MAX_CITATION_RETRIES`, so the driver and the `pipeline()` fallback cannot
climb different numbers of rungs.

`--plugin-root` and `--verdict-dir` are both REQUIRED and both are refusals, not
conveniences. The driver EXECUTES the template's builders, and
`${durable_root}/` is writable by the very codex jobs it dispatches, so it will
run only the plugin tree's copy — there is deliberately no durable fallback.
`--verdict-dir` holds the judge verdicts, which authorize an approval record and
a merge into an immutable canon; a path inside `${durable_root}` is refused
outright, as is one that is not owned by you and private.

**The loop the session drives.** Read the driver's one JSON line.

1. `needs_judge[]` non-empty → dispatch ONE agent per entry, **in parallel**,
   each with `agentType: "literary-translator:citation-judge"` and that entry's
   `judgePrompt` verbatim. That parallelism is the point; a serial loop throws
   the saving away.
2. Write the replies to a file **inside `--verdict-dir`** as
   `[{"batch": i, "attempt": n, "nonce": "<the entry's own nonce>", "reply": "<the
   agent's full reply>"}, ...]` and re-invoke with `--record-verdicts <that file>`
   plus the same `--verdict-dir`, `--plugin-root` and `--run-id`. A path outside
   that directory is refused: the file carries the nonces that admit an approval,
   so it is authorization input, and one written under `${durable_root}` could be
   rewritten by a still-running codex job before it is read.
3. Repeat while `needs_judge[]` comes back non-empty. A REJECTED batch comes
   back in that list at the next attempt, with a fresh nonce — the recording
   invocation advances the ladder itself, so there is nothing extra to do. The run is done when the
   output carries `"merged": true`; `not_ready[]` names any batch that failed and
   why, with the same `reason` strings the Workflow path uses.
4. `reset[]` names any batch the driver put back to attempt 0 because the
   artifact its status promised is gone. A resume reuses the RUN_ID, and
   `resume_setup.py` deletes that run's approved snapshots, approval records and
   evidence — so a state file written before the interruption can claim a batch is
   awaiting a judge, or ready to merge, over files that no longer exist. Such a
   batch is re-prepared rather than left in a status nothing can transition out
   of. It costs one more judge call; a verdict you already collected for it is
   refused, so send the fresh `judgePrompt` instead of the old reply.

Do NOT edit a verdict's `nonce`, reuse one twice, or answer a batch/attempt the
driver did not ask about — each is refused, and the refusal is what keeps a
verdict bound to the exact snapshot bytes a judge actually read.

**What the driver does not do.** It never decides a name, never widens a gate,
and never reads a retrieved citation body — the judge does that, under
`tools: Read`. Its hand-back channel closes the same class the approval record
closes (a command that never ran, a verdict never produced, a stale verdict
replayed after a resume); it does **not** defend against a hostile codex job,
because the snapshot and evidence live under `RUN_DIR` where every agent in this
pass can write. That is this pass's existing position, not a new one.

**The `pipeline()` fallback, below, remains shipped and supported** — use it if
node is unavailable, if the driver refuses for an environment reason, or to
cross-check a result. Per #436/#516's ordering rule the fallback is not removed
before its replacement has carried a book end to end. On that path each batch runs
the shared fire-and-forget dispatch → bounded poll → disk-truth pattern:
`agent(batchDispatchPrompt(batch, attempt, rejectionReason),
{agentType:'codex:codex-rescue',
effort: EFFORT})` (`EFFORT` = this project's own `engine.effort`, #197 — a
configurable enum, default `high`; schema-less, writes the run-scoped fragment
`glossary/runs/{{RUN_ID}}/out_{index}_attempt_{n}.json` — attempt-scoped, and
`resume_setup.py` wipes stale attempts before the run starts (**1.16.0**), so
a later poll never finds an older fragment sitting at the path it waits on —
self-checks against its own manifest) → `batchWaitChunkPrompt` ×2 +
`batchWaitRecheckPrompt` (bounded poll — since **1.16.2** spent across
bounded chunks plus one authoritative non-polling re-check rather than in the
one `batchWaitPrompt()` call it replaced, `READY`/`PENDING`, so a wait costs
**up to** 3 agent calls — a `READY` in any chunk ends the wait on the spot and
suppresses the re-check, so 1 and 2 are the ordinary cases and 3 is the
worst case; see `references/canon-and-glossary.md`'s **The chunked
wait**) →
**the pre-merge citation review** (see immediately below) → once every
fragment is `READY`, one serialized `canon_validate.py --merge-batches` call
plus one disk-verify call (`schema: CANON_VERIFY_SCHEMA`) close the pass and
freeze `canon.json` — see `references/canon-and-glossary.md` for the full
mechanics and `references/workflow-schema-validation.md` for why the
pre-1.2.0 single schema-validated `agent()` call per batch was replaced
(`#87`/`#88`/`#90`/`#97`). Write `style_contract` sections A–F by hand/
interview with the user; leave section G (glossary) to the glossary-pass
output. The shipped template already wraps sections A–F in
`STYLE_CONTRACT_BEGIN`/`STYLE_CONTRACT_END` markers -- keep them (do not
delete, duplicate, or reorder them): they define the `style_contract_hash`
byte-scope, and `scaffold_validate.py` now enforces exactly one of each, in
order. That byte-scope is also the price list: every later edit inside the
markers moves `style_contract_hash`, and the next stale-check flips every
still-converged segment to `stale`. **R9** gives the policy -- no back-sweep
is owed -- and since **1.41.0** the tooling can be told the same thing. A moved
`style_contract_hash` still sits outside the machinery-only carve-out (that set
means "can never change what the prose should say", which is false for a
contract), so undeclared, W7's completeness gate and W9's assembly refuse each
flipped unit until it converges again. Setting
`validation.admit_contract_only_stale: true` in `profile.yml` opens a second,
separately named acceptance path in both gates for a flipped unit whose
`.ever_converged` sentinel is not ABSENT (an unreadable or dangling one
carves out like a present one), whose draft still matches its
`reviewed_draft_sha1`, and whose only non-machinery moved field is that one:
it is admitted and NAMED, its ledger record untouched. The declaration is the
wrong answer after a rule REVERSAL, and no hash can detect that for you
(`#533`). One thing it does NOT admit: a unit carrying an UNSPENT claim
record — one you published under `--from-converged`, which VOIDED that stored
review, whose re-review then never completed. W9 refuses it at both
contract-only admissions (the merged-`stale` carve-out and the live
cache-key check), because the `reviewed_draft_sha1` it would ship against
belongs to the review you set aside. "Unspent" is read off the ledger, not
the review file, and it is an ORDERING: the claim record stamps `claimed_at`,
the ledger fragment stamps the `timestamp` of its last convergence write, and
a re-review that completed leaves the second later than the first. NOT a
comparison of the stored `cache_key` — that key carries no draft and no
review identity, so a hand-edited draft (this profile's commonest population)
that you re-review and re-converge writes the identical key back, and
comparing it would refuse a unit you did re-review. Finish the re-review;
never delete the claim record, which is the sole durable account of the void
(`#773`).

**The pre-merge citation review** gates whether a batch counts as ready at
all. Under `research_mode: live`, every `basis:"established"` item's `source`
is fetched and reviewed inside `batchStep`, before that batch can reach the
merge. **1.16.1** splits that across TWO Claude calls per attempt,
because fetching and judging in one turn is two defects sharing one call: a
mechanical PREPARE at `effort:"low"` publishes the approved snapshot and then
retrieves every cited page through `scripts/fetch_citation.py` (scheme and
address allowlist, connection pinned to the address it vetted, every redirect
hop re-validated, caps on time/bytes/content-type), reading nothing either
command wrote; an independent JUDGE at `effort:"high"` then reads only local
files and retrieves nothing at all. **Auditing a citation BY HAND goes through
that same `scripts/fetch_citation.py` boundary, never `curl`** — it is what
checks the scheme and address, pins the connection to the address it vetted,
re-validates every redirect hop and caps time, bytes and content type, and a
cited page is attacker-authorable whoever opens it. **#505:** a merge run by
hand under `research_mode: live` is refused outright for any
`basis:"established"` item unless you pass `canon_validate.py`'s
`--citations-reviewed`, attesting such a review approved those exact bytes.
**Since `#734` that attestation must carry its evidence:** pass
`--approval-records R1 R2 …` too, one `glossary-approval/1` verdict record per
merged fragment IN THE SAME ORDER, each naming that fragment's `sha256`
(`--record-approval-to` writes them at the end of the reviewing `--check-batch`
run, as `approval_{i}_attempt_{n}.json` in the glossary run directory). The
attestation alone is now refused, in `--merge-batches` and in the legacy bare
`--batch` alike, and a record whose digest names other bytes refuses the merge
before any fragment is applied. That check can only REFUSE: a record never
permits anything, and never authorizes skipping the citation review.
The Workflow passes both itself on the reviewed path, and a merged row is frozen
against any further merge (revisable only by a deliberate `--correct`,
`#495`), so an unaudited citation that reaches the merge stays until somebody
notices it. The split is what makes that boundary an
enforcement point rather than a rule the attacker can argue with — an agent
that ingests attacker-authored page text can be told by that text to fetch
something else, so the agent that decides what to fetch is the one that never
reads it. Since `#353` the judge is dispatched as
`agentType: "literary-translator:citation-judge"`, a plugin agent granted
`tools: Read` and nothing else, so that boundary is the harness's rather than
the prompt's; the prepare half keeps Bash, which is what it runs the fetcher
with. Neither half is codex — codex wrote the citation, so a different
model is a separate opinion rather than the same reasoning re-run — and
neither can author or repair a canon decision. The judge checks, per
`basis:"established"` item and from the retrieved body alone, that it
resolved, documents the right entity, and actually attests the claimed
`canonical_target_form`; a URL the boundary refused, an HTTP error, or
evidence it cannot read all reject rather than approve.
Nothing before this stage checks a citation's TRUTH (the schema asserts only
a non-empty `format:"uri"` string) and nothing after the merge can repair one
— a merged row is immutable, so this is the last point at which a bad
citation can still be stopped. A single failing item rejects the whole
fragment and regenerates that batch to a fresh attempt-scoped path, never
over the previous attempt, bounded by `MAX_CITATION_RETRIES`; a prepare that
fails drives that same ladder without spending a judge call. Exhausting that
budget ends the run with `merged: false` and
`reason: "citation-review-exhausted"` — a distinct reason from
`fragment-check-failed`, and nothing is merged. Under `offline` the stage is
a no-op, since `established` is forbidden there outright.

**1.16.0: the approval binds the reviewed BYTES, not a path.** Before
anything is fetched or judged, PREPARE re-runs the fragment's own
`--check-batch` validation with `--approve-to`, which copies the exact bytes
it just validated — one read, nothing can change between validating and
copying — to a create-once, attempt-scoped
`approved_{index}_attempt_{n}.json`. The fetcher then takes its URLs from
**that snapshot** and the judge audits **that snapshot**; the mutable `out_*`
attempt path is never read again, and on approval the merge is handed that
same snapshot. The producer is a fire-and-forget
codex job told to rewrite the attempt path until its own self-check passes, so
renames onto the reviewed path after the review are expected behaviour, not
an adversary — and after the snapshot they reach nothing anyone reads. Within
one run, bytes audited, approved and merged are one object by identity. That is
a conclusion from stated preconditions, not a filesystem guarantee, and they
are deliberately NOT enumerated here — read them in
`references/canon-and-glossary.md`,
"What the approved snapshot guarantees, and the preconditions it rests on".
The snapshot stays inside PREPARE's own turn because prepare is the one point
both entry points into the review loop converge on: a resume-skipped batch runs
neither the dispatch nor the wait, and still gets a snapshot and its evidence.
**#724** folded PREPARE's two commands into whichever wait turn already saw
`--check-batch` exit 0, without moving either side of **#347**'s boundary — the
folded turn still opens nothing either command wrote. The call-ceiling
arithmetic that placement costs lives in
`references/pre-merge-citation-review.md`.
`offline` is the one exception: no citation, no reviewer, no snapshot, so the
merge consumes the attempt path there.

The judge's verdict is containment-guarded, as are the wait's, prepare's own
and (**#723**) the approval record's: a reply carrying the failure sentinel
ANYWHERE in it rejects, because matching whole lines alone let a fail sentinel
glued to prose slip past and a trailing clean OK line then approve. The cost is
a false REJECT on a reply that only *discusses* its own fail sentinel, and no
remaining site recovers from that DETERMINISTICALLY inside the run.
**The citation review does not recover RELIABLY**, however much
its retry ladder looks like it should: the ladder regenerates the fragment,
while what tripped the guard is the phrasing of prepare's or the judge's own
reply, so a regenerated attempt clears only if its fresh reply happens not
to re-trip the guard — a re-roll, not a repair — and every attempt can burn
on the same narration, ending the run `citation-review-exhausted` with
nothing merged. Read each batch's `lastRejection` before touching any data —
one naming specific `source_form` values with their `source` URLs and the
check each failed is a data problem; one that reads as an approval,
discusses the `CITATIONS_REJECTED` sentinel rather than any citation, or is
the fixed no-findings placeholder is the guard misfiring, so report it as a
review-prompt defect instead of re-running or hand-editing candidates; and
one quoting a failing `--approve-to` or `fetch_citation.py` command is an
environment or tooling fault rather than anything about the candidates — run
that command by hand and read its error. Full rationale, the two false REDs,
the per-site cost of a false reject, and why a repair after the merge is not
available at all: `references/canon-and-glossary.md`, "Pre-merge citation
review".

**Canon human-adjudication audit, categories 1-4 (opt-in rollout gate, with
one always-enforced carve-out)** — `scripts/canon_adjudication_audit.py`
enumerates every canon name-adjudication a human/codex must sign off —
duplicate source forms (1), existing merges (2), all candidate missed-merge
pairs (3), un-drained `review_queue[]` items (4, `review_queue_unresolved`)
— and cross-checks them against `canon_adjudications.json`. It never
decides identity itself: every call it audits is a human reviewer's or a
schema-validated codex workflow's. Run before Deliver (W7/W8):
`python3 ${durable_root}/scripts/canon_adjudication_audit.py --check` —
exit `0` = every required item has a matching `confirmed_ok` (or a valid
risk-acceptance / the queue is drained), `1` = blocking findings, `2` =
fatal. Add `--advisory` to report without blocking (preserves the plugin's
WARN-first name policy). **Status: categories 2-4 — and category 1's
identical-surface shape — remain an OPT-IN gate** a project enables for
this Deliver-time invocation; the script defaults to hard-blocking (exit 1)
so a project that wires it in gets the full gate. Category 5 (the
homonym-split evidence audit) is a SEPARATE, MANDATORY W-step
— see immediately below — never opt-in, whether or not a project enables
this Deliver-time categories-1-4 gate. **A category-1 SURFACE-VARIANT
finding is likewise never opt-in (#244):** `--advisory` cannot mask it, so
the mandatory pre-W3a invocation below halts on one even on a project that
never enabled this gate. A surface-variant finding is also the only
category-1 shape the pipeline can write — `canon_validate.py` writes
`entries[source_form] = entry`, so pipeline output carries distinct raw
surfaces — and an identical-surface finding, the shape left advisory, means
a hand-authored or legacy canon.
Enable ONLY when a per-person index, per-person bios, or enforced
cross-document consistency is in scope; on a plain translate+gloss job
leave it off — the lightweight `review_queue` remains the correct tool for
genuinely disputed/unresolvable names.
Two routes keep category 4 clear, from opposite directions: a speaking name
with a clear sense-rendering is accepted with `basis:"sense_translated"`
(`references/canon-and-glossary.md`) straight into `entries{}` and never
parks in the queue at all, while a `canon_validate.py --correct` document
with `disposition:"dismiss"` removes an already-queued row, logging it in
`corrections[]` without its ever becoming an `entries{}` record — category 4
counts neither, so the queue holds only the genuinely disputed or
unresolvable, still awaiting either outcome.

**Mandatory homonym-split evidence gate (category 5, always runs)** — unlike
the categories-1-4 gate above, this invocation of the SAME
`canon_adjudication_audit.py --check` is never opt-in and never waits for
Deliver. Run it immediately after **all three** W3-rejoin branches above —
the `glossary.enabled: false` disabled branch, the
`{"no_new_candidates": true, "batches": []}` SKIP path, and the "Otherwise
run the codex-glossary-pass" path alike — and strictly before **W3a Segpack
generation** below, on every project unconditionally:

```
python3 ${durable_root}/scripts/canon_adjudication_audit.py --check \
  --particle-config <particle_config's literal value> --advisory
```

using the profile's `source.language.particle_config` LITERAL value (never
reconstructed from `source.language.code`, same discipline as the
`bootstrap_names.py` invocation above); `--senses-path` is left at its
default, `${durable_root}/canon_senses.json`. **Always pass `--advisory`
here:** per this script's narrowed `--advisory` contract, `--advisory`
downgrades ONLY a categories-2-4 finding, plus a category-1
IDENTICAL-SURFACE one (those stay governed solely by whether a project has
separately opted into the Deliver-time gate above) —
it NEVER masks a category-1 **surface-variant** finding (#244),
`homonym_split`'s missing/stale verdict, `collapsed_split`,
`evidence_unverified`, or `canon_absent_with_senses`. So this W-step still
exits `1` — HALTING here, before W3a, nothing dispatches past it — whenever
`canon_senses.json` is non-empty and carries any unverified, stale, or
collapsed split, even on a project that has never opted into the
categories-1-4 gate; and likewise whenever `canon.json` holds two
proper-name entries whose `source_form` values differ only by case,
whitespace, NFC-vs-NFD or `casefold` (`'Nachman'` vs `'nachman '`) — the one
category-1 shape the pipeline itself can write. Clear that halt by
correcting `canon.json` so the two records become one surface (no
`canon_adjudications.json` is needed for this route — an absent file reads
as empty), or, if the two spellings really are two different people, by
recording a `confirmed_ok` verdict for the item. The escalation makes the
question mandatory; it does not assert the answer. On a project whose
`canon_senses.json` is absent or
schema-valid-empty, this call is a no-op pass-through (`gate_passed: true`)
**for category 5** — run it unconditionally rather than special-casing
whether the sidecar exists. It is NOT a whole-gate no-op: category 1 is
computed from `canon.json` regardless of the sidecar, so a surface-variant
duplicate still exits `1` here on a project that has no adjudicated splits
at all. It says so explicitly rather than reporting a bare zero: the
report's `homonym_split` row reads `NOT ENUMERATED` and the summary carries
`senses_enumerated: false`, so a vacuous zero is never mistaken for an
enumerated-clean one.

**Skeptic pass (RFC #215 Phase 2, opt-in + advisory)** — an adversarial re-read of the
frozen canon by a file-capable agent, gated on `glossary.skeptic_pass.enabled`, which
defaults to false and is off in every live book. Its full procedure — batching, the
frozen-input digest, agent-trust and tamper-detection, the exit-code contract, the
FATAL frozen-input-mutation exception, and the category-5 audit command — lives in
`references/skeptic-pass.md`. Read that file only when the pass is enabled; when it is
false or absent, skip straight to W3a below.

**W3a Segpack generation** (runs right after W3, since `segpack.py`'s canon
injection needs the just-frozen `canon.json`). Run `scripts/segpack.py` for
every candidate segment in `manifest.json`'s `segments[]` — body and
translate-decision `FRONTBACK:{id}` elements alike (both are first-class
`segments[]` members). Validate each output structurally against
`segpack.schema.json`. A missing/schema-invalid segpack for any candidate is
a FATAL preflight error here, naming the offending segment(s) — never
discovered later mid-dispatch.

**W4 Stress-gate** — run the full per-segment pipeline on the highest-risk
segment actually available among this book's own features: choose the
longest body segment, plus whichever of footnotes/verse/front-back-translate
elements are actually enabled/present. If the book genuinely has neither
verse nor footnotes, explicitly record that fact (PLAN.md or ledger note)
and stress-test the longest body segment alone — a legitimate outcome, not a
gap. Sub-chunking is cut from v1 entirely — no defined mechanism for chunk
segpacks, chunk draft naming, chunk-readiness polling, merge, or per-chunk
ledger status. In its place, W2's extraction self-check FATALLY halts
(naming the offending segment(s)) if any segment's `word_count` exceeds
`max_segment_words` — a project hitting this is honestly out of scope for
v1 (needs v2's real sub-chunking design, or a `custom` co-designed extractor
performing a principled pre-split).

**W5 Mass-translate** — W5's DEFAULT launcher is
`segment_dispatch_driver.py` (#516), the detached local driver: it runs the
per-segment translate/review loop as an ordinary local process and invokes
the preflight below itself. See **Default dispatch path** further down for
its launch recipe and for the whole loop it belongs to. Most of what lies
between here and that section is the `mass-translate-wf.template.js` +
`pipeline()` path, which remains shipped and supported as W5's FALLBACK
launcher — but not all of it: the #396 bundle-verification rule and the
#412 `{{PLUGIN_ROOT}}` mandate below both bind a driver launch too, and each
says so where it stands — instantiate that template fresh from the
plugin's current copy every run (never reuse a stale generated copy). Both launchers gate on the
same concrete preflight, `scripts/select_segments.py`: the driver shells it
itself, and a session driving the fallback runs it before `pipeline()` is
called. It:

1. Runs `ledger_merge.py` to materialize current `ledger.json`.
2. Reads the full candidate segment-ID list from `manifest.json`'s
   `segments[]`.
3. For each candidate, calls `cache_key.py --seg <id>` to compute its
   current cache key, computes current on-disk
   `segments/{seg}.draft.json`'s sha1, compares against the fragment's own
   `reviewed_draft_sha1`, and classifies:
   - **`reusable`** — materialized status `converged` AND every cache-key
     field matches AND current draft sha1 still matches
     `reviewed_draft_sha1` — skip.
   - **`stale`** — materialized status `converged` but either a cache-key
     field mismatches OR draft sha1 no longer matches
     `reviewed_draft_sha1` — needs a fresh translate/review/fix pass
     (fragment's old fields fully replaced, never merged forward) — unless
     the mismatch is caused specifically by
     `particle_config_hash`/`source_extraction_hash`/`source_input_hash`/
     `derivation_bundle_hash` and the segpack hasn't been regenerated since
     (see `blocked_needs_regeneration` below). W7 and W9 do ship a `stale`
     unit whose `.ever_converged` sentinel is not ABSENT, whose draft still
     matches `reviewed_draft_sha1` and whose every moved field is
     machinery-only, without that pass (#491, conditions in
     `references/assembly-and-output.md`; the bundle-hash section of
     `references/ledger-and-resumability.md` says why a `converged` count
     falls whenever a release moves the plugin bundle). Records which trigger fired
     as a `stale_reason` sub-field: `cache_key_mismatch` and/or
     `draft_sha1_mismatch`. A `draft_sha1_mismatch`-triggered stale is never
     reclassified as `blocked_needs_regeneration` — the two gates are
     independent. Triage a `stale` by that `stale_reason`, never by the
     materialized ledger's own status or its `stale_mismatched_fields`:
     `ledger_merge.py` computes ITS `stale` from the cache key alone and
     never reads the draft, so a segment stale HERE for draft drift alone
     is still plain `converged` there, with no mismatched fields to show.
     `assemble.py` refuses that drifted draft outright either way.
   - **`blocked_needs_regeneration`** — a `converged` segment whose
     cache-key mismatch is due to a language-config/extraction-config/
     source-file/derivation-script change the segpack itself hasn't caught
     up with yet (checked against `segpack_{seg}.json`'s own
     `generation_hashes`) — excluded from `SEGS`, self-clearing once
     W2/W3/W3a rerun, never a manual-override target.
   - **`recoverable`** — materialized status `in_progress` (interrupted
     prior attempt) — treated identically to `not_started` for dispatch,
     counted separately for visibility.
   - **`not_started`** — no fragment at all.
   - **`human_escalation`** — materialized status `blocked` or
     `non_converged` — excluded from automatic re-dispatch by default.
4. Emits `SEGS = not_started ∪ recoverable ∪ stale` (excluding `reusable`,
   `human_escalation`, `blocked_needs_regeneration`), plus a full
   classification report (counts + IDs per category + each stale segment's
   `stale_reason`). This same list becomes `mergeLedgerPrompt`'s
   `--expected-segs` — no drift between dispatch decision and completeness
   check.

`select_segments.py` CLI flags:

- `--only-segs <comma-list>` (optional) — when supplied, emitted `SEGS` is
  intersected with this list instead of the full eligible set (enables
  operator-paced batches). Also the sole mechanism for retrying a
  `human_escalation` segment: naming a currently-`blocked`/`non_converged`
  ID here is an explicit, auditable override — included in `SEGS` despite
  classification, logged as an override. Omitting `--only-segs` entirely
  reproduces default behavior byte-for-byte.
- FATALS if any `--only-segs` ID is not present in `manifest.json`'s
  `segments[]` at all — names the unrecognized ID(s), never silently drops
  them.
- FATALS if the resulting emitted `SEGS` would be empty, unless
  `--allow-empty` is also passed (escape hatch for a genuine no-op
  confirmation run).
- Every invocation logs requested `--only-segs` IDs alongside
  actually-emitted `SEGS` IDs side by side.
- **#530:** every invocation also reports `eligible_not_dispatched` — the
  eligible units it is NOT dispatching, i.e. the full eligible set minus the
  emitted `SEGS`, in candidate order and de-duplicated. It is in the JSON
  payload (always present, always a list, `[]` on the default path by
  construction) and, when non-empty, on stderr directly under the
  requested/emitted line. `excluded_only_segs` above covers the ids you NAMED
  and this script declined; this covers the opposite direction — an eligible
  unit you never named at all, which previously produced no signal whatsoever
  while the over-specified direction refused loudly. It is a REPORT, never a
  refusal: dispatching a subset is legitimate (operator-paced batches, a
  deliberately narrow retry), and the defect was that an under-specified
  `--only-segs` was indistinguishable from a complete one. Read the IDs, not
  just the count — an omission and a deliberate batch differ only in *which*
  units are outstanding. `segment_dispatch_driver.py` carries the same list in
  its `step1_gate_passed` journal entry — the durable copy — and RELAYS
  `select_segments.py`'s own one-line disclosure onto its own stderr (#551),
  rather than re-printing a second copy of it under its own prefix.
- **#536:** the second report-only field carried into that journal entry is
  `claims_from_cap_over_sentinel` — the `--from-cap` ids admitted over a
  PRESENT `.ever_converged` sentinel (the #537 population; see the `--from-cap`
  bullet below). Always emitted by `select_segments.py`, `[]` when there were
  none; the driver REQUIRES it only on a `--from-cap` invocation, because
  outside one such an admission is impossible by construction and a missing key
  would then mean exactly what `[]` means. It gets no driver-side stderr line
  of its own for the reason above: #551's relay already carries the selector's
  announcement.
- **#409:** `--allow-retranslate-converged` (optional) — without it,
  `select_segments.py` FATALs if the emitted `SEGS` would include any
  segment that has EVER converged before (a durable per-segment sentinel,
  not the ledger status, which is overwritten to `in_progress` before a
  re-dispatch and so would not catch this). A converged segment turns
  dispatch-eligible again as soon as any cache-key field moves — a plugin
  upgrade moves `plugin_bundle_hash` for every segment at once — so
  without this gate a routine upgrade would silently re-translate
  finished, paid-for work. Passing the flag authorizes exactly that
  dispatch; it does not delete the sentinel.
- **#621:** a THIRD refusal, ahead of the per-segment sentinel lookups above
  — `select_segments.py` FATALs if `${durable_root}/segments` itself does
  not resolve (missing, or a symlink whose target is gone) while the
  project's materialized ledger still records segments that converged at
  least once. An unresolvable parent directory would otherwise make every
  sentinel lookup read ENOENT — "never converged" — so this would silently
  authorize retranslating finished work while reporting a clean run.
  `--allow-retranslate-converged` does NOT clear this refusal: that flag
  authorizes redoing segments this script has ESTABLISHED converged, and
  here it has established nothing. The operator restores or remounts the
  directory (a volume that is not mounted, a moved durable root, an
  interrupted restore); if it is genuinely gone and empty and retranslating
  is accepted, say so explicitly at the path rather than with a flag. The
  refusal names the remedy for the shape it actually found, because the two
  differ: an absent path is `mkdir -p -- '${durable_root}/segments'`, while
  a symlink whose target is gone already holds the name — `mkdir -p` fails
  on it with `EEXIST`, so that one is repointed at the real directory, or
  removed and replaced by a directory.
- **#409 Step 3:** a SECOND, independent refusal gate — `select_segments.py`
  also FATALs if any prior `RUN_ID` this project's own evidence shows
  (a draft's `dispatch_token`, or a `runs/workflows/` directory) dispatched
  work without ever getting a `resume_setup.py`-written
  `runs/<RUN_ID>/input.digest`: that dispatch was never checked against the
  inputs it actually consumed, and nothing can safely resume it. There is
  deliberately no flag on `select_segments.py` itself to wave this
  through — the sanctioned remedy is `backfill_resume_gate_ack.py --apply`,
  which records, per run id, that it predates the gate (never fabricating a
  digest). `--classify-only` reads without ever triggering this gate.

**1.2.0: the deterministic pre-workflow step, after `SEGS` and before
`pipeline()`.** With `SEGS` finalized, invoke `resume_setup.py` (kind
`mass`) before the Workflow tool ever launches (on the DEFAULT driver path
the driver performs this call itself — see **Default dispatch path**): it derives the resume-
integrity digest's own segment domain directly from `manifest.json`'s full
candidate set (LT-409 — NEVER from `SEGS`, which shrinks by one entry every
time a segment converges, and would otherwise force a fresh, non-resuming
`RUN_ID` on every single convergence), computing each of THOSE segments'
current `cache_key.py` composite key. It resolves `effectiveRunId` via the
resume-integrity digest gate (`input_digest` MATCH against any candidate in
`resume_from_run_ids`' own `runs/<candidate>/input.digest` → resume with
that candidate; MISMATCH on every candidate, or none offered → fresh
`RUN_ID`), and creates `runs/<RUN_ID>/` — aborting before any dispatch on
failure. The payload's `args` field is PINNED to the literal empty object
`{}` for `kind="mass"` (`resume_setup.py` rejects any other value) — it is
NOT `SEGS`, and NOT `select_segments.py`'s own
`--only-segs`/`--allow-retranslate-converged`/`--allow-empty` scoping
flags, since those govern Step 1's own gating and must not also gate
resume. `segs` is likewise no longer read by
`resume_setup.py` at all (accepted-but-ignored for one release only). See
`resume_setup.py`'s own module docstring for the full payload contract.
**#396:** IMMEDIATELY BEFORE EACH post-Step-0a operation that will READ OR
EXECUTE a member of either verified bundle from the live plugin tree — a
`mass-translate-wf.template.js` or `glossary-pass-wf.template.js`
instantiation, a `segment_dispatch_driver.py` launch, W7's `final_audit.py`,
or any other invocation that redirects a bundle member's resolution to the
live install — run, with the SAME absolute `{{PLUGIN_ROOT}}` this session
passes everywhere else:

```
python3 {{PLUGIN_ROOT}}/assets/scripts/scaffold_setup.py --verify \
    --durable-root ${durable_root} --plugin-root {{PLUGIN_ROOT}}
```

ABORT on any non-zero exit. The markers `cache_key.py` and `resume_setup.py`
read were written at Step 0a and characterize the durable copies, not
whatever the live install now holds — not what this operation is about to
read or execute. Read the `plugin_root=` value back off the success line and
confirm it is the tree you are about to run from. This is Step 0a's writer
in a read-only mode: it repairs nothing and rewrites no marker.

**Each such operation, not once per session, and the difference is the whole
point.** The install is SHARED: another session can update it while this one
is still running, which is one of the producers #396 names. A verdict is
therefore evidence about the tree as it was when the check ran, and it does
not stay true merely because this session has not ended — a result carried
forward across a later instantiation re-opens exactly the window this rule
closes. The cost is one bundle hash per boundary, and there are a handful of
those in a run; the marker exists to avoid re-hashing per SEGMENT, which this
does not do.

A success claims parity for the MEMBERS of the two tuples and nothing wider — an extra,
non-member file under `${durable_root}/scripts/` is invisible to both bundles
yet still importable, so do not read the pass as "this install is the one
this project was scaffolded against".

The predicate is narrower than "resolves anything from the live install" on
purpose: Step 0 itself runs `profile_validate.py` from the live tree before
a durable root exists, and Step 0a's own writer above is itself a live
invocation, so that wider phrasing would demand verifying before there is
anything yet to verify. It is also narrower than "any `--plugin-root`
invocation": `backfill_resume_gate_ack.py` accepts that flag for uniformity
and resolves nothing through it, so that proxy would block an unrelated
migration tool for no reason.

Only then — on the FALLBACK path — is `mass-translate-wf.template.js`
instantiated (fresh from the plugin's current copy every run — never reuse a
stale generated copy) and `pipeline()` launched. That instantiation
substitutes `{{RUN_ID}}`, `{{EFFORT}}`, `{{MODEL}}`,
`{{MAX_CODEX_JOBS_PER_BATCH}}` and `{{CODEX_COMPANION_PATH_JSON}}` — the
last resolved by running `resolve_codex_companion.py` from the plugin's own
install path, which ABORTS W5 on any non-zero exit — alongside every other
token. Read `references/orchestration-and-batching.md`'s W5 and "Prompt
functions" sections BEFORE instantiating, for each token's derivation and
what `resume_setup.py`'s `subst` payload must carry. The DEFAULT path
(`segment_dispatch_driver.py`, below) instantiates no template at all.

**#412:** that same instantiation ALSO substitutes `{{PLUGIN_ROOT}}` — this
skill's own directory, the SAME value Step 0 already defines
(`${CLAUDE_PLUGIN_ROOT}/skills/literary-translator`), reused here, never
redefined — and NOT `${CLAUDE_PLUGIN_ROOT}` itself, which makes `codex_job.py`
exit 2 with "does not resolve to a directory containing assets/scripts/".
Unlike this skill's PROSE occurrences of `{{PLUGIN_ROOT}}`, which a reader
substitutes on the fly when typing an example command,
THIS one is a literal Workflow-template token: it must be written into the
instantiated `mass-translate-wf.template.js` file itself, exactly like
`{{RUN_ID}}`/`{{EFFORT}}`/`{{MODEL}}`/`{{CODEX_COMPANION_PATH_JSON}}`
above. Thread the SAME value to TWO consumers: `resume_setup.py`'s
payload, as a new top-level `plugin_root` field (deliberately NOT inside
`subst` — it is a filesystem path, not a semantic value, so it is never
folded into `input_digest`; see that script's own module docstring), and
`codex_job.py`'s own `--plugin-root` flag, appended to both the translate
and review dispatch commands below alongside `--companion`. Substituting
it redirects where `codex_job.py` resolves
`draft_ready.py`/`validate_draft.py`/`review_ready.py` from — the
plugin's own install tree, which codex cannot write to — instead of
`${durable_root}/scripts/`, which codex CAN write to (every codex launch
below grants `--write` over the whole durable root). **Omitting this
substitution is not a neutral default: it leaves the pre-#412
vulnerability open** — a codex-tampered copy of any of those three gate
scripts sitting in `${durable_root}/scripts/` would validate its own bad
output, and nothing downstream would catch it. Always substitute it.

**#582/#607 — what bounds the fix turn's writes, and where that is recorded.**
`--plugin-root` moves only the CHECKER a script shells out to; nothing here
moves the ENTRY POINT, and that asymmetry is deliberate rather than an
oversight. On the FALLBACK path only, W5 runs `fix_scope_audit.py` from
`{{PLUGIN_ROOT}}` after every dispatched fix call — a copy-fidelity check, not
a write audit — and a mismatch ends the segment (`fix-scope-violation`, or
`fix-scope-unverified` after two failed relays); both classify
`human_escalation`, and neither is recoverable on its own. **Read
`fixScopeHalts` in the batch result before dispatching another batch**: that
array, not the ledger and not the `reason` string, is what survives a failed
durable write. The relay residual is stated rather than closed — the audit
reaches W5 through a model relay, and a relay that fabricates its reply can
fabricate BOTH numbers; nothing here prevents that.

The full record — why relocating entry points was evaluated and not adopted
(recorded so it is not re-raised per review), what `--plugin-root` does and
does not buy, the four classes the copy-fidelity check does not cover, the
`n_checked`/`n_expected` rule that makes `ok: true` honourable, and the
`languages/` hole `tests/fix_scope_audit.test.py` pins — is in
`references/orchestration-and-batching.md`, "The fix turn's write scope
(#582/#607)". Read it when you are on the fallback path, when a fix-scope halt
fires, or before proposing a per-command entry-point rewrite. On the DEFAULT
driver path this whole paragraph is inert: `fix_scope_audit.py` does not fire
there at all — see "What the default path does NOT carry", item (1), below.

Clearing a halt costs that segment a re-translation — name it under
`--only-segs`, and for a previously converged segment add
`--allow-retranslate-converged`. And a mismatch is **not by itself proof of
tampering**: a plugin upgraded mid-project gives the identical signal, and
the one remedy serves both readings — re-run Step 0a's copy pass, then re-run
the segment.

**Default dispatch path — `segment_dispatch_driver.py` (#409, made W5's
default by #516).** This is W5's default launcher. Copied into
`${durable_root}/scripts/` at Step 0a like every other bundle member, it runs
the identical per-segment translate/review loop as a detached local process
instead of inside the Workflow tool, eliminating the WAIT-polling chunking
apparatus entirely. That apparatus is what the flip is about: measured on a
real 40-segment run (`wf_e75b4b96-ac1`), orchestration bookkeeping was
**83.4% of 25.0M tokens** — 694,852 per converged segment — and it is the
share this path does not spend. Unlike the fallback path above, where the
orchestrating session invokes `resume_setup.py` itself as an explicit
preflight step before instantiating the template, the driver resolves the
resume-integrity `RUN_ID` on its own, via `resume_setup.py`, every time it
runs, and shells `select_segments.py` for Step 1 itself — there is no
separate preflight call for a session driving this path to make.

**The fallback is RETAINED, not retired.** Everything above
(`mass-translate-wf.template.js` instantiation, `pipeline()`, the
DISPATCH/WAIT/CONSUME chunking apparatus, the `batch_agent_cap` preflight)
stays shipped and supported, and is the path to take when this driver cannot
be used. Retiring it is a SEPARATE decision and never a rider on this flip:
an escape hatch is not removed before the driver has carried a book end to
end as the default (#432 is the live example of a driver-only path with none).
Never run the two against the same `durable_root` concurrently — nothing in
either path guards against that (the driver's own project-wide lock,
`runs/.driver.lock`, only serializes two driver launches against each other,
never a driver against a Workflow-driven run).

**What the default path does NOT carry, stated where the launcher is
chosen.** Three items, each measured rather than argued.

**(1) `fix_scope_audit.py` (#607) does not fire here at all.** A
driver-mediated fix turn is outside it entirely — the driver hands the
rendered prompt out as `needs_fix` and truncates the template before every
top-level preflight, so no audit call site exists on this route. Say what
that check actually is, because "the fix turn is unaudited" understates it:
it is a COPY-FIDELITY comparison of every file Step 0a copied into the
durable root against the plugin bytes it came from — 48 scripts, the three
workflow templates, 24 schemas and the 6 language files, 81 artifacts — run
after every dispatched fix call on the fallback. What the default path has
in its place is the #396 rule below: `scaffold_setup.py --verify` before
each driver launch, which compares the two BUNDLES — 21 scripts plus
`mass-translate-wf.template.js` and `glossary-pass-wf.template.js`, 23
members. So 58 copied artifacts have no byte comparison on this path,
including every durable schema, every language preset,
`skeptic-pass-wf.template.js`, and the W7/W8 entry points `final_audit.py`
and `assemble.py` — and `final_audit.py` is in NO bundle hash by design (see
W7), so the file the section below names as this path's whole-book authority
is itself one of the uncompared copies. The whole durable root is
codex-writable (`codex_job.py` grants `--write` across it) on both paths.
The fix turn being performed by hand from the rendered prompt is a real
difference from the fallback's automatic `agent()` call — a person reads it
— but it bears on the DRAFT edit, not on any of the above. A digest handed
out at `needs_fix` and required back on the next invocation, tracked
separately, would bracket that draft edit; it closes none of the
copy-fidelity delta.

**(2) A missing `--plugin-root` is not refused.** The fallback template
refuses to start a batch at all when `{{PLUGIN_ROOT}}` arrives empty
(`reason: "fix-scope-plugin-root-missing"`). The driver's equivalent is
softer by design: an EMPTY string is refused, but an OMITTED flag takes the
documented self-anchored mode and resolves `codex_job.py`,
`select_segments.py` and `mass-translate-wf.template.js` itself out of
`${durable_root}/scripts/` — the tree codex holds write access over. That
mode exists for the no-orchestrating-session case and is not being removed;
what changes with the flip is that a session IS driving, so pass
`--plugin-root` (it is in the launch recipe below), and know that forgetting
it fails open rather than loud.

**(3) The batch-final `batchComplete` merge: this path has no per-batch
equivalent, deliberately** — see below for what carries that guarantee
instead.

**#396:** this launch is one of the operations covered by the W5 rule above
that verifies the bundle markers against the live plugin tree. When the
driver is launched with `--plugin-root R`, that same `R` is the value to pass
to that rule's own `--plugin-root`:
`segment_dispatch_driver.py` resolves the template and its sibling scripts
from `R`, and resolves a relative `R` against its OWN runtime cwd, not the
orchestrating session's.

Launch it as an ORDINARY FOREGROUND Bash tool call — NEVER `run_in_background`
(a recorded anti-pattern here: its poll gets harness-stopped mid-wait while
the spawned worker keeps running, producing a false "completed"):

```
nohup python3 {durable_root}/scripts/segment_dispatch_driver.py \
    --plugin-root {plugin_root} \
    [--only-segs SEG1,SEG2,...] [--allow-retranslate-converged] \
    > {durable_root}/runs/driver.<SESSION_ID>.log 2>&1 < /dev/null & disown
```

(`<SESSION_ID>` here is a caller-chosen label for this one log file — e.g. a
timestamp — distinct from the session id the driver generates internally for
its own journal directory.) Full CLI: `--durable-root PATH`/`--plugin-root
PATH` (this driver's own sibling-script resolution — data root vs. install
root, same split as every other #409 script — also threaded through to
`select_segments.py`'s identical flags), `--only-segs SEG1,SEG2,...`/
`--allow-retranslate-converged`/`--allow-empty` (forwarded verbatim to
`select_segments.py`'s own flags of the same name, above), `--from-cap`/
`--from-converged`/`--from-stalled SEG1,SEG2,...` (also forwarded verbatim to
`select_segments.py`'s own claim-ADMISSION flags of the same name — single-
phase and durable-writing, a claim record plus a re-stamped draft
`dispatch_token`, never a blanket authorization: only the ids named),
`--resume-from-run-id RUN_ID` (#458: names WHICH prior run this invocation
should resume, instead of the newest digest-matching candidate
`resume_setup.py` would otherwise be offered — it never bypasses that
script's own digest comparison, only which candidate reaches it. Refuses
exit 1 when the named run's directory or its `input.digest` is missing or
the wrong kind, when its recorded digest does not match this invocation, or
when a selected segment's own draft is stamped for a different run (not reachable for a segment an admission just claimed — `select_segments.py` re-stamps an admitted draft to this run before the gate runs, which is what admission IS; and a token naming no recognizable owner is not "different"); exit 2
for an unsafe id or a filesystem state this script cannot establish. Left
unpinned, resolution itself is unchanged and a fresh mint prints one stderr line
naming the new RUN_ID and how many eligible candidates were offered — in
the driver's own log file for the documented detached launch above, at the
terminal for a foreground run), `--max-concurrent-codex-jobs N` (default
40), `--node BIN`. Exit 0 means the per-segment loop ran to completion —
NOT that every segment converged; read the printed JSON's
`summary.failed`/`summary.needs_fix`. Exit 1 means a gate refused before any
dispatch (lock contention, the Step 1 re-translate gate, the volume cap, a
`--resume-from-run-id` refusal). Exit 2 is a usage/environment error.

**While it runs: `driver_status.py` (#765).** The driver prints its one JSON
line only when the batch is over, so for the hours in between there was no
supported way to ask *is this still working, how far along is it, or did it
already finish?* — and two book projects independently hand-rolled the same
`status.sh` to fill the gap. That surface now ships:

```
python3 {durable_root}/scripts/driver_status.py [--durable-root PATH]
```

It is safe to run at any moment against a live run: it takes NO lock, writes
nothing, and never invokes `select_segments.py` (that path shells
`ledger_merge.py`, which rewrites `runs/ledger.json` and shells `cache_key.py`
per converged fragment — a durable write, however read-only the classification
itself is). It shells out to exactly one thing, `ps`. There is no
`--plugin-root`, because it resolves no sibling script.

**It publishes observations, never a lifecycle verdict**, and that is the
design rather than an omission. Every artifact it can read is advisory:
`append_journal()` is best-effort by its own docstring, `runs/.driver.lock`'s
CONTENT is documented as diagnostic-only and written best-effort, POSIX permits
`ps` to truncate `args`, and both the session id and every journal timestamp
come from a non-monotonic wall clock. So there is no `state: "running"` field
to be wrong; there is `run.recorded_exit` (an object or `null`),
`lock_diagnostic.pid_alive`, `lock_diagnostic.ps_command`, and
`run.last_recorded_event.age_sec`. Compose them:

```
python3 {durable_root}/scripts/driver_status.py | jq -r '
  "run     \(.run.session_id // "none")  selected_by \(.run.selected_by // "-")",
  "exit    \(if .run == null then "unknown" elif .run.recorded_exit then "recorded at \(.run.recorded_exit.ts)" else "none recorded" end)",
  "lock    pid \(.lock_diagnostic.pid // "?")  alive=\(if .lock_diagnostic == null then "?" else .lock_diagnostic.pid_alive end)  is-driver=\(if .lock_diagnostic == null then "?" else .lock_diagnostic.ps_names_driver_script end)",
  "last    \(.run.last_recorded_event.type // "-")  \(.run.last_recorded_event.age_sec // "?")s ago",
  "jobs    \(if .run == null then "?" else (.run.recorded_codex_dispatches.in_flight | length) end) in flight",
  "batch   \(.run.batch_progress.recorded_fragment_status_counts.converged // "?")/\(.run.batch_progress.dispatched // "?") converged (this run)",
  "project \(.progress.recorded_fragment_status_counts.converged // "?")/\(.units.total) converged (whole manifest)"'
```

An exit recorded twenty minutes ago with a dead lock pid READS as a finished
batch; no recorded exit, a live pid whose `ps` line names the driver, and a
last-event age of seconds reads as a working one; a live pid with an hour-old
last event is the wedge worth looking at. "Reads as", not "is" — each line is an
observation, and the paragraph above is why none of them is a proof. Note the
`?`s: an absent lock or an absent run renders as unknown rather than as `false`
or `0`, because those would be answers.

The counts named `recorded_*` are counts of journal ENTRIES — a lost
best-effort write lowers the count without lowering the work, which is why they
are not called anything stronger.

**What `selected_by: lock_diagnostic_pid` does and does not assert.** That
branch requires the lock's pid to be alive AND its `ps` line to name
`segment_dispatch_driver.py`; without both, the ordinary
`greatest_recorded_driver_started_ts` ordering is used and says so. It does NOT
additionally require the `ps` line to name THIS durable root, and that is
deliberate: the documented launch recipe uses an absolute path, but a driver
started as `python3 scripts/segment_dispatch_driver.py` from inside the durable
root is a real and current pattern, and its command line legitimately contains
no absolute root — measured `false` on a live book while this was written.
Requiring it would demote every such run to the fallback. The residual it leaves
— a stale lock pid, reused by a live driver of a DIFFERENT book, matching an
epoch recorded under that same pid here — is visible in the payload rather than
hidden: `ps_names_this_durable_root` and `pid_matches_lock_diagnostic` are both
published.

**Two progress numbers, and they answer different questions.**
`run.batch_progress` is scoped to the segment ids THIS run's Step 1 gate
selected, and is the one to read while a batch is live; `progress` is the whole
manifest. They diverge exactly when they should: a run launched with
`--only-segs` for ten fresh units in a book where seventy already converged is
`0/10` on the first and `70/80` on the second. `batch_progress` is `null` when
the epoch records no `step1_gate_passed`, and ONLY then — a gate that has fired
but whose units have no fragment yet (the ordinary state of a fresh run;
`runs/ledger.d/` does not exist until the first fragment is written) is `0/N`
with every id counted missing, never unknown. The gate's ids are validated with
the same regex `manifest.json`'s are, because they are joined onto
`runs/ledger.d/` to build a path and the journal is written best-effort; ids
that fail it are excluded from the census and COUNTED as
`batch_progress.unsafe_recorded_ids`, so the gap against
`run.recorded_dispatched_segs` — which stays the number the journal recorded —
is visible rather than a quiet shrink.

Both come from the per-segment fragments under `runs/ledger.d/`, intersected
with `manifest.json`, with all five `ledger-fragment.schema.json` statuses
zero-filled and `manifest_ids_without_fragment` /
`fragment_ids_not_in_manifest` published rather than folded away — the next
paragraph is why they cannot come from `runs/ledger.json`. Two caveats travel in
the payload rather than being left implied: `staleness_checked: false` (a
`converged` fragment may have staled since, and only the classifier can say),
and `schema_validated: false` (a fragment is read as "a JSON object with a
string `status`", so a hand-edited artifact `ledger_update.py` would refuse is
still counted). Symlinks and non-regular files are refused rather than followed
at every read, ANCESTOR directories included — a symlinked `runs/` or
`runs/ledger.d` would count another book's population as this one's, and a FIFO
named like a fragment would block the read forever, which is the one thing a
surface you run against a live batch must never do. Every read path is required
to resolve inside the durable root.

It reports no draft-file count on purpose: a draft exists from round 1 onward
and never goes away, so the count saturates, and `segments/*.draft.json` also
matches `codex_job.py`'s private `.att.<seg>.<INV>.draft.json` staging slots.

**The driver does not refresh `runs/ledger.json`.** Its only ledger write is
the per-segment fragment at `runs/ledger.d/<seg>.json`; `ledger.json` itself
was last materialized by this same run's Step 1 `select_segments.py` call, so
once the driver returns it still reports PRE-run state and reads `stale` for
a segment that has just converged. The driver's own printed JSON
(`summary.converged`/`summary.needs_fix`/`summary.failed`) is the authority
for what this run did. To refresh the durable view before reading it, run the
merge with no `--expected-*` flag — that is the only thing "bare" means here,
and it is how `select_segments.py` itself runs it:

```
python3 {durable_root}/scripts/ledger_merge.py \
    --durable-root {durable_root} --plugin-root {plugin_root}
```

`--plugin-root` is not optional in practice, for the same reason it is not on
the `reject_review.py` invocation below: it decides where the trusted
`cache_key.py` sibling is loaded from, and `{durable_root}/scripts/` is a
Step-0a copy the codex process holds write access over (`codex_job.py` grants
`--write` across the whole durable root). Omit it and this merge resolves its
stale-checker from inside the very tree the check exists to audit — a tampered
copy passes itself, and the segment materializes as non-stale after a real
cache-key change. `select_segments.py` forwards both roots for this reason;
`tests/ledger_merge.test.py` pins both directions, the detection with the flag
and the false green without it. **What the flag does NOT buy:** the entry
point you type here is itself the durable copy, so a tampered
`ledger_merge.py` never consults the trusted checker at all. See the #582
paragraph above for why that asymmetry stands.

**Getting the value WRONG is refused, not skipped (#608).** Omitting
`--plugin-root` is still the deliberate self-anchored path and behaves as it always
has. But passing it a path that does not resolve to a directory containing
`assets/scripts/` — a typo, or a `{{PLUGIN_ROOT}}` that never got substituted and
arrived empty — used to fall through to a per-segment *"cache_key.py not found —
skipping stale-check"* warning for **every** segment: the merge then printed its
ordinary success line and materialized a ledger in which nothing had been checked.
That merge now fails before any fragment is read, with `success: false` — and for a
value that did resolve to something, the error names both what you passed and the
path it looked for. (The empty case cannot name a path, and says so instead.) If you see it, fix the
value — do not drop the flag to make it go away, because that swaps a loud refusal
for the self-anchored checker this flag exists to bypass.

Do NOT reach for `--expected-from-manifest`/`--expected-segs` here, and never
add `--run-token` to them — that combination belongs to the batch-final check
below, not to this mid-run refresh. Either expected-segment flag turns on the
missing-fragment completeness check, which REFUSES outright for a manifest id
that has no fragment yet — the normal state of a book mid-way through; adding
`--run-token` further arms the batch-final re-verification that
`mass-translate-wf.template.js`'s `batchComplete` step performs on the
fallback path and that the step below performs on this one. Both
refusals raise BEFORE `ledger.json` is written at all, so what you get is
exactly the staleness you were trying to clear. Second axis, and this one
survives the merge: a fragment records the LAST CONVERGENCE, not the current
draft, so a converged segment hand-edited afterwards goes on reading
`converged`. Neither artifact answers "is the draft on disk the one the
reviewer saw" — that is `reviewed_draft_sha1` against the draft's current
content sha1, which `final_audit.py` and `assemble.py` each recompute for
themselves before anything ships.

**Step of this loop, not an exception to it: the fix turn (#516).** The
driver dispatches translate and review; it does not fix, and nothing
automates the hand-off. The default W5 loop is therefore: launch the driver →
read its printed JSON → perform ONE Claude fix turn per `needs_fix` segment
using the prompt that JSON carries → re-launch the driver, which re-derives
each segment's state from durable disk facts (`derive_next_action()`) and
picks up at the next review round (re-running #396's `scaffold_setup.py
--verify` before EACH launch, not once per session — the flip multiplies
launches, one per fix round rather than one per batch) → repeat until the
summary reports
neither `needs_fix` NOR `failed`. What confirms the BOOK is W7's own audit,
not a per-batch check — see below. "No `needs_fix`" alone is not the
completion condition: a segment whose translate or review FAILED produces no
fix prompt to act on, and the driver still exits successfully carrying it in
`summary.failed`. A batch holding a failure is unfinished, and that id
belongs in no completeness claim. When a segment's review comes back
not-clean, the driver stops at that segment and returns
`outcome: "needs_fix"` — the round label, the findings, and the exact
rendered fix prompt — then moves on/exits without fixing it (applying
findings to a draft is a real LLM content-editing turn a plain Python process
cannot perform). Someone — a human, or an orchestrating session — has to
notice that: the only two channels it is ever announced on are the driver's
own JSON output and its redirected log (`runs/driver.<SESSION_ID>.log`, per
the launch command above), and what follows from reading either is the fix
turn of the loop above. **That JSON arrives only at exit** — stdout
carries exactly ONE line, printed on the driver's terminal path, so the
redirected log shows no per-segment progress at all while the run is in
flight; what lands there live is the driver's stderr — its own warnings, plus
`select_segments.py`'s relayed stderr (#551), which is where the Step 1 gate's
own disclosures arrive. The channel that IS live is its own journal: one entry
per event, each flushed and fsynced as it is written, opening with a
`driver_started` entry carrying the pid — which is also how you tell a driver
still working from one that died. No
script or template anywhere in this plugin currently reads `needs_fix`, the
driver's stdout, or its own journal
(`runs/<internal-session-id>/driver_journal.jsonl`) on the driver's behalf.
Do not launch this driver unattended expecting it to complete a batch
end-to-end — a `needs_fix` segment sits stalled until someone checks.
**R8 governs WHO performs that turn** — this session, or at most two
long-lived executors kept open across rounds; never a fresh spawn per round,
per segment, or per defect class. It does not govern the TIER, and the
prompt does not either: the rendered fix prompt opens with an `Effort:`
line only because the same text is built for the `pipeline()` path, where
the tier is really carried beside it, as `callFix`'s own `agent()` option
(`references/engine-loop.md`, "Effort discipline"). Run by hand, that
opener pins nothing — set the reasoning effort of the turn you dispatch
yourself, to the same `engine.effort` value the line names.

**What replaces the fallback's batch-final check here (#516).** The driver's
only ledger write is the per-segment fragment; it does NOT perform the
batch-final `ledger_merge.py --expected-segs … --run-token …`
re-verification that `mass-translate-wf.template.js`'s `batchComplete` step
performs (the driver's own docstring names that as the caller's). Do not try
to reconstruct that check's roster out of driver output. A driver run is a
repeated SUBSET invocation, not one batch: `--only-segs` is deliberately
outside resume identity, so successive invocations under one `RUN_ID` each
report only their own ids, and a unit that failed in an earlier invocation is
simply absent from a later summary — any roster assembled from summaries is
a claim about what you remembered to run, not about the book.

**W7 carries it instead, on the axis that matters most here.**
`final_audit.py` runs over EVERY currently-converged segment in the project
and recomputes each draft's content sha1 against that segment's own
`reviewed_draft_sha1`; `assemble.py` refuses on the same identity before
anything ships. That is whole-book and roster-free, and it is where "is the
draft on disk the one the reviewer saw" is actually decided — on both
launchers. It is not a superset of the batch-final merge: that merge ALSO
re-asserts the draft's `<run_token>:<seg>` stamp and the review artifact's
matching `:r<round>` token, and W7 reads neither. What W7 does not carry is
the run-token binding, not the draft identity.

You may still run the batch-final merge over a set you are explicitly
claiming complete. If you do, read its OUTPUT rather than its exit status,
because neither weak case is a refusal: an id you did not name is outside the
check entirely, and an id the merge materializes `stale` has its token/sha
re-assertion SKIPPED while the command still reports success, listing that id
in `stale_segments`. A non-empty `stale_segments`, or an id you meant to
claim and did not name, means NOT complete.

```
python3 {durable_root}/scripts/ledger_merge.py \
    --durable-root {durable_root} --plugin-root {plugin_root} \
    --expected-segs SEG1,SEG2,... --run-token RUN_ID
```

`RUN_ID` is the driver's own printed `run_id`. Name only ids converged under
THAT run: the re-assertion reconstructs `<run_token>:<seg>` per id, so an id
stamped by an earlier run fails it. And name only ids you are claiming
CONVERGED — the completeness half is satisfied by whatever fragment happens
to exist, so a `needs_fix` or `failed` id in the list makes an unfinished
batch read as verified.

**When ONE finding of several was refused (#764) — recording the refusal.**
Since #532 the fix turn refuses a finding it cannot substantiate and leaves the
text alone, and it reports that refusal in prose above its `FIXED` line. Record
each such refusal before you move on. Not bookkeeping: the turn's only durable
output is the draft, so a draft where four of five findings were applied and the
fifth refused is byte-for-byte identical to one where the fifth was **missed**.
The next round's reviewer re-raises it, and the next round's fix agent — which
since #541 sees the previous round's verdict but never its disposition — reads
the gap as *dropped* and applies it. Measured on a live book: a conventional
biblical patronymic, correctly refused at r4, split into two `<person>` tags at
r5 against a standing project ruling, minting an index entry for a referent the
book never mentions. Both forms are well-formed, so every gate passed.

Two invocations, the read before the write, exactly as below:

```
# 1. The read mode -- a PURE READ (writes nothing) that prints the stored
#    review's dispatch_token and round label plus every finding's index, loc
#    and issue digest, all from ONE read.
python3 {durable_root}/scripts/refuse_finding.py SEG --print-finding-digests \
    --durable-root {durable_root}

# 2. The record. --finding-index selects (an INDEX, not a loc: one block
#    routinely carries several findings, so a loc would select the wrong one);
#    every --expect-* value is copied VERBATIM from step 1 and is the
#    attestation that a human read that exact finding.
python3 {durable_root}/scripts/refuse_finding.py SEG --finding-index N \
    --reason "the fix turn's own ground for refusing it" --round-label LABEL \
    --expect-token TOK --expect-loc LOC --expect-issue-digest HEX64 \
    --durable-root {durable_root} --plugin-root {plugin_root}
```

The record lands at `segments/<seg>.findings_refused.json` and **releases
nothing**: no gate reads it, `derive_next_action()` never opens it, the round
still costs what it cost, and a re-raised finding stays entirely legitimate.
The one thing it buys is that the NEXT fix turn's prompt can show the refusal
and its reason, so an unapplied finding reads as considered rather than
overlooked. Re-running the same command is a no-op success, not a second
record. It is deliberately NOT given to the next REVIEWER — see `#529`: the
artifact under review is never the authority it is reviewed against, and a
fixer-authored "do not raise this" list would suppress valid findings.

**When the WHOLE VERDICT is wrong (#461) — rejecting it instead of
applying it.** A refusal recorded above is still a report about ONE finding; it
changes no routing, and `reject_review.py` remains the only way to say durably
that a verdict does not bind. A
review can be schema-valid, carry an authentic `loc`, pass the
`fabricated_loc` gate, and still be FALSE about the source — verified on a
live segment whose sole finding claimed a Hebrew string that occurs zero
times in the block (the apparent word was a fragment of a longer one, split
by RTL mangling). There is nothing to apply, so the draft does not change,
and `derive_next_action()` cannot tell "unchanged because the draft was
already right" from "unchanged because nobody tried": it returns
`needs_fix` again, on every subsequent invocation, forever.

**Do NOT resolve that by applying the finding anyway.** It is the path of
least resistance and it is the worst available outcome: it inserts content
the source does not contain, and every gate downstream of the fix turn —
schema, dispatch token, placeholder parity, `draft_sha1` — reads the DRAFT,
never the source, so not one of them can catch it. The reviewer is an LLM
and its findings are not evidence.

The route out is `reject_review.py`, the only component allowed to record
that a stored verdict does not bind. Verify the claim against the source
yourself FIRST — this tool records a human judgement, it does not make one.
Then, two invocations, the read before the write:

```
# 1. The read mode -- a PURE READ (writes nothing) that prints the stored
#    review's own dispatch_token, verdict_digest and round label, all from
#    ONE read, so the three always describe a single verdict.
python3 {durable_root}/scripts/reject_review.py SEG \
    --print-verdict-digest --durable-root {durable_root}
# -> {"success": true, "dispatch_token": ..., "verdict_digest": ...,
#     "round_label": ..., "round_label_problem": null}

# 2. The rejection itself -- every --expect-* value copied VERBATIM from
#    step 1's own output. Nothing auto-fills them: passing them back IS the
#    attestation that a human read that exact verdict.
python3 {durable_root}/scripts/reject_review.py SEG \
    --reason "verified against the source: ..." --round-label LABEL \
    --expect-token TOK --expect-verdict-digest HEX64 \
    --durable-root {durable_root} --plugin-root {plugin_root}
```

`--plugin-root` is not optional in practice: it is what decides where the
trusted `claim_record.py` sibling is loaded from, and `{durable_root}/scripts/`
is a Step-0a copy other passes in this pipeline hold write access over.

The record lands at `segments/<seg>.review_rejected.json`. **It never
yields a re-translation and never writes the draft** — a rejection is never
permission to overwrite. What the next driver invocation does with it
depends on the round:

- **At a numbered round** it dispatches a FRESH review at the next label
  instead of `needs_fix`.
- **At the mandatory `final` round (#527)** it TERMINATES the unit as
  **converged**, on your `--reason`, provided two things the record itself
  cannot assert: the draft has not moved since the verdict you rejected,
  and that verdict's own `coverage_ok` is `true`. If the draft moved, or
  coverage was reported incomplete, you get the fresh `final` review
  instead — that is the fall-through, not a failure.

The `final` behaviour changed because the old one could not work: it bought
exactly one re-review, and a reviewer re-reading the SAME unchanged input
re-derives the same false finding, so the unit capped anyway. Two reviews
of one misleading input are one observation, not two.

**So a `final` rejection is a terminal decision, and it is whole-verdict.**
Reject only a verdict whose findings are ALL unfounded: rejecting a mixed
verdict now converges the segment over its genuine findings instead of
merely costing a review round. If any finding is real, fix the draft.

The record does not survive the review it names: it is bound to that
verdict's token AND its digest, so a genuinely different verdict is judged
on its own merits.

The convergence is visible on disk as such — the ledger fragment carries a
`note` naming this record and quoting your reason, because the `review.json`
beside it still says `clean: false`.

`--reason` is required, non-empty, and durable — it is the entire audit
trail, and the one thing a later reviewer has to go on. Re-running the
identical command is safe: an unspent record is left exactly as it is
(`already_recorded: true`), and one the driver has already consumed is
renewed (`renewed: true`) — the route that still needs at `final`, since
#527, is a verdict the driver sent back for a fresh review anyway. A
DIFFERENT `--reason` for the same verdict refuses rather than overwriting
the first.

**Claiming a segment for re-review (#438) — re-reviewing a hand-edited
draft WITHOUT re-translating it.** A converged or capped segment's draft is
sometimes hand-edited outside the plugin (a name-form fix, a phrasing
tightening) after its stored review already exists. Ordinarily that draft
is untouchable — `previously_converged` and the review-cap classification
exist specifically to refuse re-dispatching finished or capped work — and
nothing else in W5 lets an operator say "re-review THIS draft, exactly as
it stands now, without re-translating it." A **claim** is the narrow,
per-segment, per-run authorization that does exactly that: it never
re-translates, and it clears the refusal gates for exactly the id(s) named
and nothing else.

**The step order for a claim run INVERTS the ordinary one — read this
before running one, because it differs from every W5 run described above.**
For an ordinary run, `select_segments.py` runs first (to produce `SEGS`)
and `resume_setup.py` runs second, against that finalized set ("1.2.0: the
deterministic pre-workflow step", above). **A claim run reverses this:
`resume_setup.py` runs FIRST**, before `select_segments.py` is ever
invoked, and `select_segments.py` takes the resolved RUN_ID explicitly
rather than deriving anything from the ordinary flow:

```
# 1. Resolve RUN_ID first (kind=mass -- the SAME payload contract as an
#    ordinary run's resume_setup.py call, above).
python3 {durable_root}/scripts/resume_setup.py --payload-file {payload.json}
# -> {"success": true, "effectiveRunId": RUN_ID, "resume": true|false,
#     "run_dir": ..., "input_digest": ...}

# 2. THEN select_segments.py -- --run-id and --run-resume copied VERBATIM
#    from step 1's own output (see the --run-resume warning below).
python3 {durable_root}/scripts/select_segments.py \
    --from-cap SEG1[,SEG2,...] --only-segs SEG1[,SEG2,...] \
    --run-id RUN_ID --run-resume true|false
# or:
python3 {durable_root}/scripts/select_segments.py \
    --from-converged SEG1[,SEG2,...] \
    --run-id RUN_ID --run-resume true|false
# or (1.24.0, #455 -- P3, below; --only-segs IS required here, enforced by
# its own D3b check -- a different mechanism from D3's --from-cap gate):
python3 {durable_root}/scripts/select_segments.py \
    --from-stalled SEG1[,SEG2,...] --only-segs SEG1[,SEG2,...] \
    --run-id RUN_ID --run-resume true|false
```

This is not a stylistic reordering — it is what makes the claim mechanism
sound at all. The claim's own durable record lives at
`runs/<RUN_ID>/.claimed.<seg>`, and it re-stamps the draft's own
`dispatch_token` to `<RUN_ID>:<seg>`; BOTH need RUN_ID to already exist at
admission time, which is only true once `resume_setup.py` has already run.
Running the ordinary order (select first, resume second) for a claim would
mean admitting against a RUN_ID that does not exist yet — this is not
merely untested, it is unsound by construction.

**Reversing the order is only safe because of a matching fix to the #409
Step 3 gate — the ordering alone is not the whole story.** A freshly minted
RUN_ID could otherwise manufacture the very evidence Step 3 checks for (a
digest that PROVES the resume-integrity gate ran, when all it actually
proves is that THIS invocation ran, moments ago). `select_segments.py`
closes this by scanning Step 3's evidence exactly ONCE, at the very start
of its own invocation, before either the claim-admission block or the
fresh-evidence refusal ever runs — so an invocation can never see its own
writes as pre-existing evidence, regardless of where a future edit moves
the claim block relative to everything else.

**`--run-resume` is a RELAY of `resume_setup.py`'s own `resume` field —
NEVER an operator judgement call, and never a value to type from memory or
convenience.** Copy the exact `true`/`false` value `resume_setup.py`'s own
stdout reported in step 1 above, nothing else. Getting this wrong does not
fail loudly — **it silently defeats a safety gate, in exactly ONE
direction.** `select_segments.py` refuses a `--run-resume false` claim when
this project's own evidence (a draft already carrying this exact RUN_ID, or
a `runs/workflows/<RUN_ID>/` directory) shows the id is not actually fresh
— but a `--run-resume true` attestation is trusted outright, with nothing
on the code side verifying it against a genuinely resumed digest. A `true`
typed for what is actually a `false` case sails straight through the one
check meant to catch it. There is no way to make this self-verifying; the
operator relaying the value correctly IS the safeguard.

**`resume: true` asserts digest identity and NOT that any work exists under
that run (1.38.0, #538/#544).** It means one thing: the matched candidate's
recorded `input.digest` equals the digest this invocation just computed. It
says nothing about that run having dispatched anything. Two consequences an
operator has to hold alongside it:

- **A claim run whose Step 1 is REFUSED leaves its `runs/<RUN_ID>/` and
  `input.digest` behind.** As of 1.38.0 a refusal ON POLICY —
  `previously_converged`, an unsafe prior RUN_ID, a prior run with no
  resume-integrity digest — performs no durable write of its own: no claim
  record, no re-stamped `dispatch_token`. (A refusal because a claim WRITE
  itself failed is a different case and can still leave a partial one; that
  refusal names the ids it managed to write.) Either way `resume_setup.py`
  had already created that directory before Step 1 ever ran, so the next
  invocation computing the same digest matches it
  and legitimately reports `resume: true` over a run that dispatched
  nothing. That is the gate working as specified; it is not evidence of
  work, and it is not a fault to repair by hand.
- **What a segment's work actually belongs to is its own draft
  `dispatch_token`**, in `segments/<seg>.draft.json`, read directly. It is
  optional at the schema level and records only the most recent dispatch, so
  its absence proves nothing. `select_segments.py`'s `dispatching_run_ids`
  and `run_id_evidence` report which run ids this project holds evidence for
  and of what kind — never a per-segment ownership map, which no single
  field anywhere provides.

**The three admission profiles — never a fourth.** An earlier, ARTIFACT-ONLY
attempt at a third profile (`--from-incomplete`, for stalled/interrupted
work) was designed and then deliberately DELETED from this feature: no
implementable condition over artifacts separates "bookkeeping incomplete"
from "ordinary live work mid-flight" (the default path full-replaces the
ledger to a two-key `in_progress` row before it ever dispatches, which is
indistinguishable from a stall by any artifact on disk). `--from-stalled`
(1.24.0, #455, below) reaches that same population WITHOUT repeating that
mistake — it never claims to tell stalled from live by artifacts alone. It
proves the two liveness facts this project's own kernel state CAN prove and
leaves the remainder an explicit, disclosed operator assertion rather than
dressing an artifact test up as proof. Reintroducing an artifact-only fourth
profile is still forbidden; `--from-stalled` is not that, and neither is
1.25.0's widening of `--from-converged` (#491): admitting a converged unit
whose draft never changed but whose content-affecting cache key did is a
second way to satisfy that profile's own drift condition, not a new
profile — same claim mechanism, same name, same refusal shape. Naming a
segment under the wrong profile is refused BY NAME, not silently
reclassified into another one:

- **`--from-converged SEG1[,SEG2,...]`** — for a segment that converged
  CLEANLY at least once and was then either hand-edited or left untouched
  while a content-affecting cache-key field moved (1.25.0, #491; previously
  only the hand-edited case was admitted). Requires, in addition to the
  shared gates below: a materialized ledger status of `converged` or
  `stale`; a `.ever_converged.<seg>` sentinel present; and the stored
  review's `clean` is `true` — OR, when it is `false`, the review is
  admitted as the CONTINUATION of a re-review loop this project already
  opened (#460; see the paragraph below for what establishes that). The
  drift baseline is one of two shapes:
  - **Draft changed** — current draft content sha1 DIFFERS from
    `reviewed_draft_sha1`. Admits exactly as before 1.25.0; no stored
    `cache_key` is required (`--only-segs`'s force-include can reach a
    hand-edited segment with none).
  - **Draft unchanged** — current sha1 still matches `reviewed_draft_sha1`.
    Requires a usable stored `cache_key` dict, a computable current key, and
    at least one moved field OUTSIDE `MACHINERY_ONLY_CACHE_KEY_FIELDS`.
    `style_contract_hash` is one such field, deliberately and still — so a
    contract-only stale unit stays re-reviewable through this profile even on
    a project that declares `validation.admit_contract_only_stale` (`#533`).
    The declaration says the gates MAY ship it unjudged; it never says the
    operator may not ask for the judgement.
    Anything missing refuses, fail-closed, naming which part was missing. A
    unit whose ONLY moved fields are machinery-only (`plugin_bundle_hash`,
    `schema_hash`, `derivation_bundle_hash`) is refused here too, saying
    assembly no longer requires action for it — `assemble.py`'s
    completeness gate now carves that population out directly, without any
    claim (1.25.0, #491), and `validate_assembled.py`'s reviewed-SHA rebind
    population is widened to match, so a carved-out `stale` segment is
    rebind-checked exactly like a `converged` one instead of failing the
    structural-completeness gate that runs before Deliver. That gate
    restates only the field-list half of the carve-out predicate, never the
    `.ever_converged` sentinel condition — not because assembly will catch
    it (in the default scope assembly may never run), but because this gate
    has never checked the sentinel for any record, so carved-out `stale`
    records are admitted on exactly the terms `converged` ones always were,
    and because `final_audit.py`'s own carve-out count already blocks
    `project_complete` when the sentinel is absent, one gate earlier.
  A successfully-admitted claim clears the `previously_converged` refusal
  for exactly that id, and nothing else — under any of the three profiles,
  since each of them can admit a sentinel-bearing unit (`--from-cap` since
  1.27.0, #537).
- **`--from-cap SEG1[,SEG2,...]`** — for a segment that hit the review cap
  (materialized status `non_converged`, `reason: "cap"`) and was then
  hand-edited. Requires: the stored review's `clean` is `false` WITH
  non-empty `findings`. **The `.ever_converged` sentinel may be absent OR
  present** (1.27.0, #537) — a unit that converged, went stale when the
  contract moved, re-entered the loop and exhausted its rounds there is
  capped *and* sentinel-bearing, and that intersection was previously
  admissible by no profile at all. When the sentinel is present the
  admission is disclosed on `select_segments.py`'s **stderr** — on the
  hand-run recipe above, and, since #551, through `segment_dispatch_driver.py`
  too: that driver captures the selector's stderr and now relays it verbatim
  onto its own, so the line reaches the `runs/driver.<SESSION_ID>.log` the
  launch recipe redirects into (the D9 lost-token disclosure arrives by the
  same relay). Since #536 the ids are also RECORDED rather than only
  announced: they travel in the selector's payload field
  `claims_from_cap_over_sentinel` and are journalled into the driver's
  `step1_gate_passed` entry, the only copy that survives the run. The fact is
  still not written into the claim record — it describes how the invocation
  reached the admission, not the claim. Both are success-path only: an
  invocation that publishes some ids and then refuses on a later one leaves
  those records durable while the refusal payload carries no `claims`, no
  `claims_admitted_via` and no `claims_from_cap_over_sentinel` — read the
  authorizations that did land off `runs/<RUN_ID>/.claimed.<seg>`. #536
  neither widens nor closes that older residual. An *unreadable*
  sentinel is still refused: it is evidence of nothing. Because a capped
  segment is
  `human_escalation`, `--only-segs` naming the same id(s) is ALSO required
  — the same explicit-retry mechanism any other `human_escalation` retry
  already needs, now doubled as a second, independent authorization.
  A capped unit sits at the absorbing `final` round, and `final`'s
  successor is `final` — computed before `engine.max_fix_rounds` is read
  at all, so RAISING that knob returns no capped segment to a numbered fix
  round; it only lengthens the ladder for units that have not capped yet.
  The two routes back are the ones documented above, and which one applies
  turns on whether the verdict or the draft was wrong: `reject_review.py`
  when the stored finding is false (at `final`, over an unmoved draft whose
  verdict reported `coverage_ok`, that CONVERGES the unit — #527; otherwise
  one fresh review at `final`), or a hand-edit of the draft followed by this
  flag plus `--only-segs` when the finding was right.

  Reaching the cap is a state to REPORT, not a residue to adjudicate on your
  own. Show the human what is still outstanding on each capped unit and
  which of those two routes applies to it, and let them choose. The question
  is not "another round?" — a rejection at `final` over an unmoved draft
  converges the unit with no further review at all, and a finding that
  stands has no accept-it-anyway route.
- **`--from-stalled SEG1[,SEG2,...]`** — for a segment stalled with
  genuinely incomplete bookkeeping: previously converged, then left
  `in_progress` with a review that no longer describes the current draft.
  `reviewed_draft_sha1` is unconstrained either way (#796) — see **P3**
  for why. Full condition list, what the profile proves versus what it
  asks the operator to assert, and the hand-driven fallback for a unit
  that fails it: see **P3**, below.

**A dirty review is admitted under `--from-converged` only as the
CONTINUATION of a re-review loop this project already opened — never
merely because the review happens to be dirty, which is just as true of a
segment nobody ever claimed (#460).** The claim mechanism shipped in 1.21.0
worked for exactly one round: when the mandatory fix turn after a
not-clean round 1 produced a dirty round 2, the segment had nowhere to
go — `--from-converged` itself required `clean: true`, and the plain path
refuses via `previously_converged` regardless. Continuation is established
from a claim record at `runs/<run_id>/.claimed.<seg>`, asked for in a
fixed order: the draft's own CURRENT owner first, read off its
`dispatch_token` — "I have not claimed this" and "nobody has" are
different facts — and, ONLY on D9's lost-token recovery path (the draft
carries no token at all, and this run's own prior claim record is what
re-establishes one), this run itself. Every condition is checked against
the record's CONTENTS, never merely its presence: it must agree with the
path it was found at (its own `seg` and `run_id`), carry one of this
project's own claim profiles (#796 — not necessarily `from-converged`
itself: a unit migrates between profile populations as its ledger status
moves, so the profile that opened the loop can no longer be required to
be the one now admitting it), and carry every field `build_claim_record()`
writes — a partial object is what a forgery or a half-finished write looks
like, and it is refused exactly like an absent record. **This widening is
about continuation, not about D9's own lost-token recovery** — that
recovery is a separate, narrower check (`evaluate_lost_token_recovery()`)
and #796 leaves its profile test alone: it still requires this run's own
record to name the EXACT profile now being requested, because its
sanctioned instruction is literally "re-claim under the SAME profile,"
never a different one. On the lost-token
path specifically, admission is ALSO refused if any OTHER run has taken
the segment over since this run's own claim — records are never released,
so two claim records for one segment is not an anomaly, it is what a
sanctioned takeover leaves behind. That refusal is operator-visible and
has an operator-visible consequence: a token-less draft superseded by a
later claim cannot simply be re-claimed by the run that held it first.

**Shared safety gates, across all three profiles, every requested id validated
together in ONE pass (every failure reported at once — three sequential
refusals would cost three round trips to learn three problems):**
`validate_draft.py` passes; `draft_ready.py`'s structural checks pass; the
draft's own `dispatch_token` names a run that actually exists under
`runs/`; the stored review is schema-valid with `coverage_ok: true`; and
the segpack's frozen `canon_map` still agrees with the CURRENT `canon.json`
for every name it lists (a stale segpack is refused, naming the mismatched
names, with a pointer to re-run `segpack.py` before claiming).

**A claim never re-translates.** Admission itself never touches the
draft's own text — only its `dispatch_token` is re-stamped, byte-identical
content otherwise. A claim and `--allow-retranslate-converged` are mutually
exclusive for the same id and rejected OUTRIGHT if both are given for it:
one authorizes re-TRANSLATION, the other re-REVIEW, and "claim wins" would
let one flag silently change what the other one means. Which path CONSUMES
the claim afterwards is not neutral, though. `segment_dispatch_driver.py`
derives each segment's next action from the draft on disk, so a claimed,
healthy one goes straight to review. `pipeline()` has no claim-aware
branch: its translate stage dispatches one `codex_job.py` translate per id
in `SEGS`, claimed ids included — and `codex_job.py` then ADOPTS the
claim-restamped draft without launching codex at all, or refuses the
translate outright (#438 D8) if that draft is missing or fails validation.
The hand-edit survives either way; what the claim costs on that path is
one wasted dispatch per claimed id, never the draft.

**P3 — a stalled, previously-converged, incompletely-bookkept unit.**
`--from-stalled SEG1[,SEG2,...]` (1.24.0, #455) admits a segment stuck with
genuinely incomplete bookkeeping — materialized ledger `status:
in_progress`, a `.ever_converged.<seg>` sentinel PRESENT, a draft on disk,
and a stored review that is stale against that draft, whether or not a
`reviewed_draft_sha1` from an earlier convergence is still on record
(#796 — see below) — rather than cleanly converged-and-edited
(`--from-converged`) or capped-and-edited (`--from-cap`). Neither of those
two profiles reaches it: `--from-cap` refuses because the materialized
status is `in_progress`, not `non_converged`/`reason: "cap"` (since 1.27.0
it is the STATUS that refuses here, never the sentinel — a present sentinel
is admissible under `--from-cap`, see #537); `--from-converged` refuses
because the status is `in_progress`, not one of the converged/stale
statuses that profile requires — `reviewed_draft_sha1` decides nothing
there, since that gate never gets past the status check.

Requires, beyond the shared safety gates above: materialized status
`in_progress`; the `.ever_converged.<seg>` sentinel present; a review
artifact on disk; that review stale against the CURRENT draft, checked
**only on entry** (below); no competing driver
holding `runs/.driver.lock`; no codex job holding this segment's own
`segments/.codex_job.<seg>.lock`; and (D3b) `--only-segs` naming exactly the
claimed id(s). **`--only-segs` IS required here, but not for the reason it is
for `--from-cap` — conflating the two mechanisms is itself a trap.**
`--from-cap`'s population is `human_escalation`, which
`DEFAULT_ELIGIBLE_CATEGORIES` excludes, so an unclaimed capped id can never
reach `segs` at all unless `--only-segs` names it — D3 (claimed ids ⊆
`segs`) is sufficient there on its own. A stalled unit is NOT
`human_escalation`: `classify_segment()` never reads the sentinel, and
`in_progress` classifies `recoverable`, which IS inside
`DEFAULT_ELIGIBLE_CATEGORIES` — so without `--only-segs`, `select_default()`
would sweep every OTHER `not_started`/`recoverable`/`stale` candidate into
`segs` alongside the claim, and D3 alone would not catch it (D3 checks only
that claimed ids are a subset of `segs`, never the reverse). **D3b is this
profile's own check for exactly that gap: when a `--from-stalled` id is
requested, every emitted seg must ALSO be a subset of the claimed ids**
(`segs` ⊆ claimed) — the direction D3 does not cover. Omitting
`--only-segs` does not merely dispatch un-scoped work; it trips D3b's fatal
outright, because `select_default()`'s sweep is exactly what makes `segs` a
strict superset of the claim. Review
`clean` is **not** constrained — a stalled unit's stale review may be
`clean: true` or `clean: false` and both are admitted; unlike
`--from-cap`'s `clean: false`-with-findings requirement, the field
describes a verdict over a draft that no longer exists, so it says nothing
about the CURRENT draft in either direction. A unit whose review is
current and clean but never converged is deliberately excluded — its
remedy is a convergence write, not a re-review. `reviewed_draft_sha1` is
unconstrained the same way (#796): the field records what an EARLIER
convergence saw, and this profile's population is defined by the stored
review no longer describing the CURRENT draft, which that field says
nothing about either way — present and absent are both admitted, at the
same width `clean` is.

**What this profile proves, and what it asks the operator to assert
instead of proving.** Two liveness facts are provable from this durable
root's own kernel state, and admission proves both before it claims
anything:

- **No competing driver holds `runs/.driver.lock`** — a project-wide
  `flock`, acquired and held across the whole admission decision, standalone
  or driver-invoked. Cannot acquire (and, on the driver-invoked path, cannot
  confirm a genuine holder by an independent probe) ⇒ refuse every
  `--from-stalled` id, naming the lease.
- **No codex job is in its promoting phase on this segment** — this
  segment's own `segments/.codex_job.<seg>.lock`, acquired and held across
  the claim write and the token re-stamp for every requested id. Cannot
  acquire ⇒ refuse that id, naming the segment and the job lock.

**What is NOT provable is the operator's own disclosed assertion, recorded
verbatim in `claim_record.py`'s `operator_invocation`: that no Workflow fix
turn, and no OTHER `select_segments.py` claim invocation, is touching these
same ids.** The plugin has no way to check either — a Workflow-dispatched
fix turn holds neither lock above, and a second claim invocation racing
this one is a gap this profile discloses rather than closes. **Naming an id
under `--from-stalled` IS that assertion, stated plainly wherever the
profile appears — its `--help`, its refusal text, and here.** Getting it
wrong has a specific, real cost, and it is understated by saying "work may
be lost": a concurrent fix turn writes the canonical draft directly and
copies whatever token it read, so depending on timing it either loses its
own work or leaves the claimed draft carrying content nobody re-reviewed.
The two locks above cover a driver and a codex job — never a fix turn, and
never a second selector invocation.

A further residual, disclosed rather than closed: an operator running the
selector directly with a forged `--driver-lease-held` while a real driver
runs would pass. That actor is inside this project's trust boundary — the
durable root's owner, who can already rewrite a draft or the ledger
directly — so the gate exists against operator mistake and against a
fabricated model finding, never against that operator.

**Which gate admitted a unit is reported SEPARATELY from the durable
record — read `claims_admitted_via`, not `claims`, to answer it** (1.57.0,
#545/#549). `select_segments.py` reports `claims: {seg: <record>}`, and that
record is the one durably written at this RUN_ID's *first* claim on that
segment; a re-claim inside the same run id deliberately does not rewrite it,
so its `profile` can be older than the admission that just happened. The
sibling map `claims_admitted_via: {seg: profile}` reports the gate *this*
invocation ran, and `segment_dispatch_driver.py` journals both into
`step1_gate_passed`. They differ for a real population: a unit claimed
`--from-converged` early in a run, re-reviewed, capped inside that same run,
then re-claimed `--from-cap` under the same RUN_ID. When they disagree, the
disagreement is the interesting fact — `from-converged` and `from-cap` admit
on different evidence and carry different remedies — and neither map is a
correction of the other. Nothing gates on `claims_admitted_via`; it is
report-only.

**Staleness gates ENTRY only, and continuation of the SAME loop does not
re-trigger it.** Once `--from-stalled` dispatches a fresh review and that
review is promoted, the review is current. If the driver then dies before
the convergence write, or the fresh verdict is rejected via
`reject_review.py` without touching the draft and the driver sends it back
for one more review rather than converging it (#527: at `final` a rejection
over an unmoved, `coverage_ok` verdict converges instead), the unit returns
to `in_progress` + sentinel + no `reviewed_draft_sha1` (still this loop's
ordinary shape, since it never had a baseline to begin with — but no
longer the profile's defining one, since #796 also admits it PRESENT; see
P3 above) with a now-current review — and a standing staleness gate would
wrongly refuse re-entry into the loop this profile just opened.
Continuation is authenticated the same way `--from-converged`'s
dirty-review continuation is, above: against a COMPLETE claim record held
by the draft's current owner, or, on the lost-token path, this run — and,
since #796, that record no longer has to name `--from-stalled` itself,
only one of this project's own claim profiles, because a unit can migrate
between profile populations between the original claim and this
continuation — never merely because the review happens to be current.

**When a unit fails one of the conditions above, the hand-driven procedure
below is the FALLBACK — no longer the only route.** For a unit that genuinely
fails the profile, the same procedure that used to be the only option remains
available, entirely OUTSIDE any plugin script, in this exact order:

1. **A fresh review of the current bytes, at the NEXT round label** (e.g.
   `:r2` if the stale review was `:r1`) — dispatched however W5's ordinary
   review step would dispatch one, against the draft exactly as it stands
   today. Never assume the stale review's findings still describe today's
   text.
2. **If that fresh review comes back `clean: true`** — proceed straight to
   the convergence write below.
3. **If it comes back dirty** — run ONE fix round against ITS findings
   (never the stale review's), re-review, and converge only once THAT
   review is clean. Do not skip the fix round to force a convergence write
   against a dirty review — see the THIRD bullet below (`ledger_update.py`
   never reads `clean`/`coverage_ok`) for why that would succeed silently,
   and be wrong.

**The convergence write (`ledger_update.py`) is NOT auto-filled from the
existing `in_progress` row — the operator supplies every field explicitly,
and three of them are unenforced traps with no code-side safety net:**

- `ledger_update.py` builds its fragment entirely fresh; it never reads the
  prior on-disk fragment for values. The payload's `rounds` (an integer)
  and a COMPLETE, freshly-computed 15-field `cache_key` (via the project's
  own `cache_key.py` — never copied from an old fragment) are both
  **required operator input**; only `n_blocks`/`n_footnotes`/`n_verses`/
  `reviewed_draft_sha1` are filled in for you.
- **`run_token` is OPTIONAL in the payload, and omitting it SILENTLY
  DISABLES both token checks** (the draft-token equality check and the
  review-token match). This procedure is only as safe as this one line: the
  payload MUST carry `run_token: "<the draft's own existing RUN_ID>"` (the
  run that never converged the segment — read it off the draft's CURRENT
  `dispatch_token`, never invented or guessed). Only the content-sha1
  re-check is unconditional; both token checks are not — a payload that
  omits `run_token` is not the reviewed procedure, no matter how correct
  everything else in it looks.
- **`ledger_update.py` never reads `clean` or `coverage_ok` at all —
  verified by search over the whole script.** It consumes only the
  review's `draft_sha1` and `dispatch_token`. On the plugin's own dispatch
  paths, the driver/template enforce the verdict before ever calling this
  writer; this hand-driven route bypasses that enforcement entirely, so
  **the operator IS the enforcement**: record convergence only against a
  review whose own `clean` AND `coverage_ok` are both `true`. Nothing
  downstream will catch a convergence recorded against a dirty review —
  this is the single most dangerous way to get this procedure wrong, and
  it fails completely silently.

**W6 Consistency pass** — cross-segment sweep using `consistency_issues.md`
as a lightweight, hand-maintained tracker after every batch, before the next
starts. Never the output of an automated script, never read back in or
acted on programmatically. A decision recorded here is invisible to the
reviewer, which reads `style_bible.md` and the segpack — its `canon_map` and,
since 1.45.0, its `split_names` — so it binds nothing until it is promoted into
one of those.
**R9 governs a promotion into the style contract**: it binds the segments still
to come, never a re-review of the ones already converged.

**A round whose findings pile up on one rule is evidence about a CLASS, not only
about the loci it names — and sweeping that class is yours, because nothing in
the loop does it (#534).** Convergence is per-segment and one-way: a reviewer
sees one segment, so *"is this wrong anywhere else?"* is a question it cannot
ask, and once a unit is `converged` no automated step reopens it or compares it
against another segment — this pass is the one that does, and nothing has ever
told you to point it at a class. Since 1.62.0 the fix turn reports the round's
shape as `<rule>: N of M findings this round` in its reply — that report is the
only thing that will ever tell you a
class is live, and it grants the fix turn no authority over loci no finding
named. Acting on it is this pass's job, under one rule:

> **Enumerate the class, then adjudicate every site individually. Never close an
> enumeration by applying the rule across it.**

Two traps attend that work:

- **The enumeration is a pattern you wrote, not a given.** The enumeration is
  produced by a pattern you wrote for this round, and that pattern is the step
  most likely to be wrong. It fails silently in both directions and still
  prints a plausible count. Print the DISTINCT matched surface forms and read
  them before treating any hit as a defect, deciding per form and never per
  count. Reading the forms you matched can only show an over-count. A form the
  pattern never matched is missing from that list too, so an omission looks
  exactly like a clean run, and the direction this warns about is the one the
  remedy above cannot see. The check for that direction is a different one:
  widen the pattern deliberately, dropping its most restrictive element, and
  see whether the set of distinct forms grows. That check is one-directional in
  its turn — an already over-broad pattern only grows further under it, which
  reads as confirmation — so pair it with the opposite move: tighten by one
  defensible distinction and see whether the count collapses, and treat a count
  that moves by an order of magnitude under a small, defensible change of
  pattern as the signal to stop and read rather than to sweep. A script that
  writes its vowels as separate combining codepoints — Hebrew and Yiddish among
  the sources this plugin targets — makes that change one codepoint wide and
  puts a shorter word inside a longer one, where the word boundary works against
  you — the mark following the shorter word is not a word character, so `\b`
  MATCHES inside the longer one instead of refusing it — and the printed forms
  show no difference: measured on a Hebrew source, one honorific class swept
  two defensible ways minutes apart returned 688 source sites and 0, the two
  patterns differing by a single negative lookahead for the longer honorific
  whose points the naive pattern had swallowed. Measured on one round of a
  French-to-Russian book, a stem scan for a spelling class returned 89 hits
  across three stems and exactly one of them was a defect — `идет` 66 and
  none, `черт` 12 and none, `произведен` 11 and one — because in an inflected
  language a stem matches forms where the property under test is legitimately
  absent, and in the total those hits are indistinguishable from real ones.
  The same day a source-side scan stringified a whole block dict, counted
  `source_html` and `plain_text` both, and reported every figure at exactly
  twice its true value.
- **A class claim and the sites it names are two claims.** Under the rule above,
  a finding that says a rule is applied inconsistently "throughout" and then
  lists sites has made two claims, and they fail independently. Measure the class
  AND open the named sites, because refuting the class claim discharges no named
  site. In that same round 427 italic spans in the source against 831 in the
  drafts refuted the class outright, while three of the sites that finding named
  were real defects.

The enumeration and the defect set are different objects, and measured on a live
book the ratio ran better than ten to one — 109 occurrences of strings the book
italicises somewhere, about ten of them actually wrong. Four things decide a
site, and skipping any of them is how a sweep does its damage:

- **The site's own source and role.** Membership in the enumeration is not a
  verdict on the site.
- **Whether a DIFFERENT rule in the contract already accounts for it as
  written.** Two rules can each be correct while one rule's enumerated class is
  mostly instances of the other: of 98 sites in one rendered-quotation class,
  86 were already correct and 66 of those were compliant with the
  *quotation-inside-a-quotation* rule instead. Normalising them would have
  destroyed every one.
- **What the book already does with the same string elsewhere.** And never read
  frequency as guilt — to a rule read alone, a book's strongest convention is
  indistinguishable from its most widespread violation. 93 roman occurrences
  against 2 italic was the strongest convention in one corpus and would have
  been the sweep's biggest target.
- **The draft's own `notes[]`.** The apparatus is load-bearing evidence for a
  later sweep, not commentary beside it: one over-correction was stopped only by
  a note written rounds earlier recording that those two occurrences are a man's
  byname and that an earlier finding wanting them italicised was wrong.

**Enumerate the population from the SOURCE, not from your own matcher.** A
draft-side pattern defines its own residue: what it cannot reach is not in the
count, so the class closes, reopens next round at a different honest number, and
closes again. Measured on a live book, one quotation class was declared closed
three times — 32 sites, then 3, then 15 — every count true about its predicate and
silent about the class. Re-anchoring the denominator on the source (every source
run the draft reproduces: 1057 enumerated, 230 reproduced) made the counts
comparable ACROSS rounds, and let the transform refuse to write when it found
fewer pairs than the enumeration held. The measurement that justifies the
re-anchoring: of 20 real defects in that class, reviewers had named 8, and eight of
the 12 unnamed sat in units that had already CONVERGED and would have shipped — a
draft-side pattern cannot find those, because nothing draws attention to a
converged unit. Where the enumeration cannot be complete, say the floor out loud
(a run the alignment cannot locate is invisible to it) and report the residue as a
named number rather than implying zero. Anchoring on the source fixes the
DENOMINATOR, not the pattern: the source-side pattern is one you wrote too, and
the trap above applies to it unchanged.

**Gate a sweep on the invariant its own transform can break — a structural gate by
construction never reads what changed.** Measured on a live book: a parcel
rebuilding aligned source spans as `token + punctuation + space` read a
WORD-INTERNAL mark as punctuation trailing the previous token and inserted a space
the source does not have, in 11 words across 8 segments; its gate was green
throughout because it conserved LETTERS, and a space is not a letter. Name the
invariant per transform — reordering conserves the letter multiset but not the
sequence, splitting conserves letters but not the word count, re-levelling quotes
conserves everything except quote balance. Two cheap rules from the same class:
**a sweep must print its own site count and refuse a zero-site run** (a
replace-across-files loop that matched nothing prints exactly what a successful one
prints), and it must print old/new for every site and have them read, with any
match outside the adjudicated site list reported rather than silently applied.

Reviewer concentration in one segment is evidence of reading order **or** of a
real local defect, and nothing tells them apart except measuring the class.
Report what you find rather than fixing it reflexively: a converged unit goes
stale the moment you touch it, and that is the operator's call to make
deliberately.

**Having made that call, know which of the two routes back you are on, because
only one of them makes the edit's survival independent of something you are not
looking at.** A hand edit moves `draft_sha1`, which drops the unit out of
`reusable` into `stale` — and `stale` is one of the three default-eligible
categories, so the segment is selected again. It is not dispatched again: Step
1's previously-converged gate refuses the whole invocation on the
`.ever_converged` sentinel first, whatever the ledger status says. That refusal
leaves exactly two ways forward, and they are not variants of each other.
`--allow-retranslate-converged` authorizes RE-TRANSLATION, and what it actually
costs turns on something the flag does not control. A hand edit moves no
cache-key field — a unit stale for draft drift alone carries an EMPTY
`mismatched_fields` — so `input_digest` has not moved either, and a run that
resumes under the same `RUN_ID` finds the draft's `dispatch_token` still
matching: `codex_job.py` then ADOPTS that draft instead of launching codex, and
the edit survives into a review. Let any independently hashed input move as
well, and the fresh `RUN_ID` orphans that token — the segment retranslates over
the edit. #742's foreign-draft refusal does NOT save this one: the unit is
previously-converged, so it classifies `stale`, the single category that
refusal exempts — refusing it would refuse the cache-key-drift retranslation
`--allow-retranslate-converged` has just authorized. It covers the REST of the
selection: every NOT-yet-converged draft whose token belongs to another run
halts the dispatch by name instead of being retranslated over a hand fix. A
**claim** removes that dependence: `--from-converged` authorizes re-review only
and never re-translates, and a hand-edited draft is exactly what its drift
branch admits, so naming those ids carries the edit into a confirming review
rather than leaving its survival to whether the digest happened to move. See
*Claiming a segment for re-review (#438)* and *A claim never re-translates*
above, under W5, for the mechanics; the two are mutually exclusive for the same
id and rejected outright if both are given for it.

A site this sweep adjudicated as NOT needing an edit needs neither route. Its
draft is untouched, and with a section-G edit behind it no content-affecting
cache-key field has moved either, so `--from-converged` refuses it — *"there is
no re-review to authorize"*. That refusal is the adjudication's own answer
reaching the tooling, not an obstacle to work around.

**Where a decision goes is a question nothing asks today, and the destinations
are not interchangeable.** Ask it while you are writing the item down. There
are five outcomes:

- **Already owned elsewhere** — `style_bible.template.md`'s own rule is not to
  restate anything `profile.yml`, the segpack or
  `translate_TASK.md`/`review_TASK.md` already owns, and verse policy is its
  standing example. A ruling one of those owns is configured there and recorded
  here as configured, never promoted. This is the only outcome that leaves the
  promotion question entirely.
- **Canon** — the decision changes the target form of a name that ALREADY has
  an `entries{}` row in `canon.json`. That row, not the segpack, is what
  "frozen" means: do not read absence from a `canon_map` as evidence of
  anything, since a legally empty target form is omitted from it and a segpack
  built before the name was canonized still carries the name in `new_names[]`.
  What the segpack does deliver is `canon_map` as source form -> target form and
  nothing else — a `basis` never reaches the reviewer, so no decision can be
  carried by one. Invalidation here is PRECISE, which is not the same as small:
  `used_terms_hash` is a per-segment cache-key field, so exactly the segments
  whose segpack lists that name in `canon_names[]` OR `new_names[]` are
  affected — one segment for a walk-on, every segment for a protagonist — and
  R4's rule is that each of them is RE-TRANSLATED, not merely re-reviewed.
  **R4's `references/canon-and-glossary.md` owns the route and the two
  obligations that come with it**: validate the edited file, and regenerate
  those segments' segpacks before selection runs again. A name that is NOT
  already frozen is not this channel and is never a hand edit either — R4's
  glossary/adjudication route owns it.
- **Homonym split** — the decision is that one source form carries two distinct
  senses. That goes through the `canon_senses.json` adjudication route and into
  regenerated segpacks, where it reaches the reviewer as `split_names`. It is
  NOT a canon row and cannot be made one: `canon_validate.py`'s recollapse guard
  refuses a bare `entries{}` entry for a split form, and the mandatory pre-W3a
  audit halts on one that predates the split. It is not a contract edit either.
  A split carries no frozen target form, so the reviewer may argue the wrong
  sense was chosen but may never prescribe a canonical target form there.
  Invalidation is per-segment exactly as for canon: `split_names` is inside
  `used_terms_hash`, so adding a split or editing a sense's `disambiguator`
  re-stales the segments that carry that form.
- **Section G** — most of what this sweep produces belongs here: a character's
  settled voice, the recurring cast, a motif held to one rendering.
  `style_bible.md`'s `G-cast`/`G-voices`/`G-motifs` sit OUTSIDE the
  style_contract markers, so filling one moves no cache-key field at all, while
  every translate and review call still reads them in full. The edit alone
  neither reclassifies nor re-dispatches an already-converged segment; any
  later dispatch of that segment, for whatever reason, reads the bible in full
  and does see it.
- **Sections A–F** — only a rule none of the above owns, one that must apply
  inside the marked span. That is the case this paragraph exists for: a
  truncated quotation stays truncated, and the verse-rhyme requirement does not
  reach it. One edit there flips every still-converged segment to `stale` at
  once (R9 above), and since 1.41.0 `validation.admit_contract_only_stale` can
  let both ship gates admit that population unjudged.

Pick by what the decision IS, and read each route's price where that route is
documented rather than assuming one is cheaper than another.

The sweep's own input is a READ of this batch's converged drafts, in
`manifest.json`'s `segments[]` order — no other pass in this pipeline
compares translated prose across segments at all: each reviewer call sees
one segment in isolation, and `final_audit.py`'s whole-book checks are
lexical and WARN-only. Read for narrative voice, for how each recurring
character is rendered, and for recurring motifs and epithets.

**Ending this sweep is a decision, and it is the human's.** Sweeping a class
edits sites inside units that have already converged; their drafts drift off
the hash their review was taken against, they land in `stale`, and
re-reviewing them yields fresh class findings whose sweep drifts the next
set. That loop has no terminating condition in this pipeline — no gate
reports it and no status names it — so where to stop is not a state you can
wait for. Report the population the last round re-opened, what each route
costs, and a recommendation, and let the human say whether another sweep
round opens.

What they decide is the sweep BOUNDARY, not the fate of any one draft. Each
touched unit still needs its own outcome, and the choice between carrying the
edit into a confirming re-review (`--from-converged`) and authorizing the
re-translation that may discard it (`--allow-retranslate-converged`) is the
same re-review-versus-re-translate choice intake names as the human's — price
it per unit rather than settling it for them. Restoring a draft to the bytes
its review was taken against is the third move, and it authorizes nothing by
itself. **Do not price any of them from this paragraph. Re-run
`select_segments.py` and read the category it gives each unit**: a restore
clears the draft mismatch alone, and what a unit needs next follows from its
classification rather than from what you did to its draft — including a
`blocked_needs_regeneration` that no override reaches until W2/W3 have rerun.
Those categories are defined with the selector above, which owns them; this
section does not restate them, because a second copy of that table is one that
goes wrong silently. What no outcome does is deliver a draft the reviewer
never saw: `assemble.py` refuses one outright ("a hand-edit the reviewer never
saw must not be assembled") and W7's hard check 2 reports the same mismatch
independently. Record why the sweep stopped in
`consistency_issues.md`, where a record that authorizes nothing belongs;
never as a key on a ledger fragment or record, which `ledger_update.py` will
not write, which both schemas refuse, and which the next ordinary write
erases anyway. One completed book ended this loop by hand-writing ledger
records the supported writer cannot produce — that is what happened on it,
not a route to copy. Note what was decided there: every finding was applied,
and only the bookkeeping was accepted.

**W7 Final audit** — `scripts/final_audit.py`, generalized directly from the
proven `final_audit.py` in the in-house historiettes-t3 provenance project
(5 checks over 75 converged segments, zero hard defects; that project is the
plugin's private origin, not shipped with it).
Runs at W7 over every converged segment:

- **Hard check 1 (`coverage_failures`):** re-invokes `validate_draft.py`
  (reused, never reimplemented) against every converged segment's current
  draft — catches a structurally-broken hand-edit.
- **Hard check 2 (`stale_review_failures`):** compares every converged
  segment's current draft sha1 against its ledger fragment's
  `reviewed_draft_sha1` — catches a hand-edit that stays structurally valid
  but silently substitutes prose the reviewer never saw. Counted separately
  from check 1, both roll into `hard_failures` for backward-compat
  reporting.
- **Six WARN-only, advisory, whole-book checks** — four generalized from the
  real reference's A1/A3/A4/A5 (the real `main()` only ever gates on coverage),
  plus (5) and (6), whose content the project itself supplies:
  (1) glossary-diff — cross-segment name-form drift + `canon.json`
  self-consistency using each draft's `names[]`; (2) link-graph —
  `⟦FNREF_N⟧`/`⟦VERSE_...⟧` sentinel bijection on the translated draft,
  cross-checked against the segpack's vid map; (3) foreign-remainder scan —
  source-language stopword-density + longest-source-alphabet-token-run
  heuristic using the resolved language preset's own `STOPWORDS`
  (generalized from the real reference's hardcoded French list); (4)
  verse-structure — per `verse_policy.mode`'s own required-field table,
  generalized from the real reference's hardcoded `ru_rhymed`/`podstrochnik`
  field names; (5) forbidden-pattern — the project's own deterministic style
  bans, declared as `validation.forbidden_patterns` in `profile.yml` (#520).
  The plugin ships no patterns and hardcodes none: a style contract lives in
  the project's `style_bible.md`, and only the project knows which of its
  rules are codepoint-decidable. Each declaration is an `id`/`pattern`/
  `message` triple; every string leaf of a converged draft's
  `blocks`/`footnotes`/`verses` is tested **as the translator wrote it**
  (sentinels not stripped, emphasis not stripped), and a pattern that fails
  to compile is reported as its own WARN rather than skipped, so an
  unenforced rule can never read as a clean run; (6) term-consistency — the
  project's own pinned COMMON-NOUN terms of art, declared as
  `validation.terms` in `profile.yml` (#199), each a bare
  `source_form`/`target_form` pair. `canon.json` is a proper-name dictionary by
  construction and check (1) above keys on proper-name channels only, so a
  recurring office title or institutional realia renders two ways inside one
  delivered volume with nothing noticing. This reports a CARRIER — one block,
  one footnote definition, one delivered verse FIELD — whose source carries the
  term more often than its OWN translated counterpart carries the pinned form.
  Counting inside a single delivered text is what stops one correct occurrence
  masking a drifted one beside it, and a body that renders the office correctly
  cannot mask a footnote that does not. Matching is by
  substring over NFC-normalized, casefolded text: pin the INVARIANT part of the
  target form. Carriers the active policy passes through untranslated are never
  compared (`preserve_source` footnotes, `skip` verses, a standalone verse's
  placeholder-only block), and the run always prints how many terms it checked,
  so an absent list cannot read as a pass. The plugin ships no terms and
  hardcodes none. Prints every WARN as free text for human eyeballing — never
  auto-"fixed."
- **Whole-project completeness gate** (a third gate, distinct from the two
  hard checks which only ever cover segments already converged): shells out
  to `scripts/select_segments.py` one final time, over the full
  `manifest.json` with no `--only-segs` restriction — folds that
  classification report directly into `final-audit-summary.schema.json`'s
  new `completeness_counts`/`project_complete` fields. `project_complete:
  true` only if every `manifest.json` segment classifies `reusable` — zero
  in every other category.
- **W7 Final audit (#208):** `final_audit.py`'s exit code is now fail-closed on
  both axes: `0` only if hard checks are clean AND the completeness gate reports
  complete; `1` on any hard defect in a converged draft (unchanged, takes
  priority); `3` (new) when hard checks are clean but the project is not yet
  fully converged.

Run it over the whole project, from the durable root's own copy:

```
python3 ${durable_root}/scripts/final_audit.py \
  --plugin-root {{PLUGIN_ROOT}}
```

**#412 — the ENTRY POINT stays durable; only the sibling moves.** That
command runs `${durable_root}/scripts/final_audit.py`, not the plugin copy:
`--plugin-root` moves only the CHECKER a script shells out to, and W5's
#582 paragraph records why relocating entry points was evaluated and not
adopted. What the flag buys here is the completeness gate's
`select_segments.py` sibling, which left to self-anchor comes out of the
same writable `${durable_root}/scripts/` the audit is auditing;
`final_audit.py` forwards the value verbatim alongside a synthesized
`--durable-root`, since that relocated sibling no longer sits under the
root it must classify. Unlike `canon_validate.py`'s stamping modes, which
REFUSE without an answer, this flag stays OPTIONAL by decision, not
oversight: `final_audit.py` had no shipped call site before this one, so
its caller set is closed by construction, and refusing would only break
hand-run audits without closing anything a spelled-out call site leaves
open.

- **Frontback coverage report** (advisory, informational, never
  exit-code-gating on its own): reads `manifest.json`'s `frontback[]`
  inventory directly, emits one line per entry — `translate`-decision
  elements report their own convergence status (cross-reference to
  `segments[]`, not new logic); `regenerate`/`omit`-decision elements
  reported by decision alone. This frontback-through-segment-loop treatment
  is new plugin hardening, carefully-designed but genuinely
  untested-at-scale — do not claim this mechanism is "proven" when building
  or extending it; `references/source-format-adapters/README.md` carries
  its provenance.
- Reads only the canonical `draft_path(seg) = segments/{seg}.draft.json`.
- **Excluded from every bundle hash** — not a member of `plugin_bundle_hash`
  (runs strictly after every segment is already converged, over data already
  on disk) nor of `orchestration_bundle_hash` (whose members are
  `scaffold_setup.py`'s own `ORCHESTRATION_BUNDLE_MEMBERS` tuple, restated in
  `references/ledger-and-resumability.md`; `final_audit.py` is not one of
  them). Editing `final_audit.py` on its own
  never flips a cache key or the resume-integrity digest via either bundle.
- **Verbatim-reproduction census (`scripts/verbatim_census.py`, 1.42.0, #502)
  — OPERATOR-RUN, never dispatched.** Nothing else in this plugin compares
  source text a draft REPRODUCES (a quoted phrase, a name in its original
  script) against the segpack it came from: `validate_draft.py` compares key
  sets and placeholders, `validate_conservation.py` is opt-in and
  word-multiset. Measured on a real Hebrew book, 206 of 4040 reproduced runs
  differed in LETTERS with every gate green. Run it by hand over converged
  segments — `python3 {durable_root}/scripts/verbatim_census.py SEG [SEG ...]`
  — and READ the queue it prints on stdout. **It never corrects and never
  gates**: exit 0 whenever the census ran, however long the queue, and there
  is no output-file flag; on the population that was read word by word there
  were more cases where the DRAFT was right and the SOURCE was corrupt than
  the reverse, so applying a "correction" mechanically damages text more often
  than it repairs it. Hebrew only; it exits 2 rather than printing an empty
  census when a block carries no `plain_text` or the source holds no Hebrew.
  The class shown per row is a LIKELIHOOD rank, not a severity — read the
  whole queue. See `references/false-green-gate.md`.
- **Structural-completeness gate (`scripts/validate_assembled.py`, #202):** runs
  immediately AFTER `final_audit.py` succeeds (default scope, i.e.
  `output.v1_scope: segment_drafts_and_audit`), over the converged drafts +
  `manifest.json`, BEFORE W8 Deliver hands off the audit package. Enforces
  the union structural-completeness invariant over the manifest's declared
  heading set (`heading_types` ∪ the built-in `HEAD`, #210): every declared
  heading block must surface as non-empty translated text somewhere in the
  converged drafts, and every converged draft's on-disk canonical bytes must
  still match its ledger `reviewed_draft_sha1` (rebinding to the reviewed
  SHA, mirroring `assemble.py`'s own guard). Exit `1` HARD on either
  violation; exit `0` with non-gating WARN entries for an undeclared
  heading-like block. See `references/assembly-and-output.md`.
- **Output-coverage v1 floor + within-cohort ratio-outlier lane
  (`scripts/validate_conservation.py output-coverage`, the #202 half
  `validate_assembled.py` declines):** runs immediately after the
  structural-completeness gate above, same scope. **WARN-only — never
  gates, exit `0` always** (barring an env/usage precondition, exit `2`).
  Two lanes share the one subcommand:
  - The **absolute floor** (1.11.0): flags `hollowed_output_block` when a
    `segments[].block_ids[]`-cited block's source text is non-trivial but
    its current converged-draft text is empty/near-empty (an absolute
    word-count floor, not a length band — see that script's own module
    docstring for why a band is deliberately not built here).
  - **New in 1.12.0 — the OPT-IN within-cohort output-coverage ratio-outlier
    surfacer, `Refs #202`**, which runs only when
    `validation.conservation_ratio_band` is declared and which
    structurally cannot close #202 — a within-cohort comparison reads a
    uniformly truncated cohort as clean. Its config keys, its warning codes
    (`low_coverage_outlier`, `zero_output_block`, `insufficient_sample`), the
    `coverage_distribution` payload and the full stated limitation are in
    `references/assembly-and-output.md`, "Output-coverage — the two lanes and
    the blind spot". Read that section before declaring the band, and before
    acting on any warning this lane prints.
  Read the WARN list; it is diagnostic input for W8's report, not a stop
  condition.

**W8 Deliver** — report convergence stats, list any `blocked`/
`non_converged` segments explicitly. Also surface W7's whole-project
completeness gate's own per-category counts alongside `project_complete` —
"this batch: N converged, zero hard defects" and "whole project: M of TOTAL
still incomplete" are two different numbers, never conflated (a batch can
succeed while the project is still incomplete). Treat ANY nonzero
`final_audit.py` exit — `1` or `3` — as a stopped gate; do not proceed to
delivery. `1` means fix the converged draft; `3` means finish
translating/reviewing the remaining segments. Hand off the audit package:
converged per-segment drafts, ledger, each draft's own audit trail,
`final_audit.py`'s summary+WARN list — as `output.v1_scope:
segment_drafts_and_audit`. When `output.v1_scope: assembled_book` instead,
this same completeness gate feeds **W9 Assemble** next: assembling the
drafts into one rendered output is a separate, additional step, never a
silent substitute for the segment-drafts handoff (see
`references/assembly-and-output.md`).

**W9 Assemble** (only when `output.v1_scope: assembled_book`) — assembly
runs as a plain DETERMINISTIC script step, never an agent workflow: it has
no agent-workflow template of its own, and none is planned. Gated on W7's
`final-audit-summary.project_complete: true` — the whole-project
completeness gate, not merely "this batch converged". Before running it,
read `references/assembly-and-output.md`, "W9 Assemble — the run order, gate
by gate": the five scripts in their required order (`assemble.py`,
`validate_assembled.py`, `validate_conservation.py output-coverage`,
`diff_rendered_output.py`, `validate_backlinks.py`), which exits are HARD
and which are advisory, and the carve-out agreement that keeps W7's summary
and `assemble.py`'s own re-derivation naming the same admitted units. Run
the chain from there, in that order; the two paragraphs below describe what
that run reports and assume it has happened.

**What collision de-linking cost this book (1.32.0, #588).** On every
`output.target: obsidian` render — appendix on or off — a
`canonical_target_form` owned by 2+ canon entries is de-linked (unless an
operator-recorded `canon_link_groups.json` group covers every one of its
owners, below), and the
renderer now REPORTS what that cost: `adapter_result.delink_cost` on
`assemble.py`'s stdout line, plus one stderr `WARN` whenever the total is
non-zero. `validate_backlinks.py` republishes the same block verbatim as
`delink_cost` (exit-neutral). `null` there means **not republished by this
gate** — never "measured zero": either no usable measurement in the vault
marker, or the disabled short-circuit, which returns `null` without reading
the vault at all. The renderer's own WARN and `adapter_result.delink_cost`
are the authority in that case. **Read the WARN — do not just log it.** A book whose de-linked
occurrences dwarf its emitted links has had its most-named figures
silenced, and every gate above will still be green. When the WARN names
targets that are spelling variants of ONE referent, the fix is
`canon_link_groups.json` (an identity call recorded upstream, never made by
a script — see `references/canon-and-glossary.md`) and a re-render. A group helps only when
EVERY owner of that target is in it and none is `sense_translated` —
otherwise the target stays de-linked by design, so read the reported
`owners` before editing. Adopting a group re-translates nothing; when it
takes effect it does change the rendered links, so `diff_rendered_output.py`
MISMATCHES (exit `1`) until the baseline is deliberately re-accepted with
`--accept-baseline --force-accept-baseline`.

**A group also decides who owns the `## Mentions` occurrences (#497).** The
same fold key that makes `בְּרֶׁסְּלֶׁב` and `בְּרַסְּלֶׁב` one match also makes
them a COLLISION for the source-anchored index: before #497 every member of
such a group had its occurrences withheld outright, so those notes carried no
`## Mentions` section at all and `validate_backlinks.py` reported
`warnings: 0` — coverage is measured over the eligible universe those forms
had just been removed from, so the loss was invisible to the gate. Measured on
the delivered he→en volume: **27 canon forms, 2 390 occurrence records, zero
Mentions lines**. With a `canon_link_groups.json` ruling covering the group,
those occurrences are credited to the group's **primary**, whose note carries
the whole index; the other members report
`reason: "fold_group_credited_to_link_group_primary"` in the gate's
`unresolved_homonyms` rows — a resolved routing decision, told apart there from
a genuine `fold_match_key_collision` or `is_split`, both of which are still
asking you for an answer. All-or-nothing: it applies only when EVERY form
sharing that fold key — canon entries AND `canon_senses.json` split-only
forms — is an index-eligible canon entry inside one group with one primary and
no member carries a split. `output.target: obsidian` only, since that is the
only target the sidecar projection is attached under. An in-flight W9r registry
run must restart when a group is adopted (`person_registry.py` binds the whole
NodeStream into `registry_input.json`'s digest).

**W9r Person registry — OPT-IN, and opt-in means the operator runs it**
(1.34.0, #550). For a book translated *for genealogy* rather than for the
translation, `scripts/person_registry.py` consolidates what the pipeline
already produced into a person-keyed registry under
`${durable_root}/registry/`. It is in none of the three bundle tuples and
moves no cache key, and there is deliberately no `profile.yml` knob. Run it
**immediately after the W9 chain (`references/assembly-and-output.md`, "W9
Assemble — the run order, gate by gate"), in the same session**. The three
script calls, the two model passes between them, why `--plugin-root` is
mandatory and what the pass does NOT do are in
`references/person-registry.md` — read it in full before the first call, and
do not reconstruct the chain from this paragraph.

## Reference docs

- `references/engine-loop.md` — R1, R6
- `references/false-green-gate.md` — R2
- `references/ledger-and-resumability.md` — R3
- `references/canon-and-glossary.md` — R4
- `references/verse-policy.md` — R5, Step 0b's 6-value enum table
- `references/language-pair-parameterization.md` — smoke-test mechanics, per-language presets
- `references/source-format-adapters/` — `gutenberg-epub.md`, `plain-text.md`, `custom.md`, Step 0c
- `references/workflow-schema-validation.md` — R7
- `references/orchestration-and-batching.md` — W5 dispatch mechanics
- `references/assembly-and-output.md` — output scope, Step 0d, W9, the
  assembler/NodeStream architecture
- `references/output-target-adapters/` — `obsidian.md`, Step 0d's
  per-target rules
- `references/pre-merge-citation-review.md` — the citation truth check,
  read at the glossary pass when a batch is about to merge
- `references/skeptic-pass.md` — the opt-in adversarial canon re-read,
  read only when `glossary.skeptic_pass.enabled` is true
- `references/person-registry.md` — W9r, the opt-in person registry
- `references/gotchas.md` — known pitfalls
