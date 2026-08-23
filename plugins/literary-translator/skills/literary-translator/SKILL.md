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

Before Step 0, before scaffolding a single file: size the job and agree its
output shape with the user out loud. Skipping this is how a plain
translate+gloss job ends up quietly provisioning apparatus it will never use.

1. **State the job's rough size.** Word count (main text, plus the footnote
   apparatus separately if the source has one), segment/chapter count, and
   whether verse or front/back matter is present — the same reconnaissance
   `PLAN.md` section 1 (Source) eventually records; do it now, before any
   scope commitment.
2. **Confirm output shape through existing knobs, never a new mode.** This
   plugin has no separate "fast mode"/"thorough mode" switch — proportionality
   is expressed entirely through profile knobs that already exist:
   `glossary.research_mode` (`live` vs `offline`), `footnotes.apparatus_policy`,
   `verse_policy.mode` (the six-value enum in
   `references/verse-policy.md`), and `engine.max_fix_rounds`. Two further
   knobs decide how much *output* apparatus gets provisioned:
   `output.target` (defaults `obsidian`) and `output.index.enabled` (defaults
   `false`). Walk the user through what each knob currently resolves to for
   this project before scaffolding proceeds.
3. **Default fast, offer thorough explicitly, through those same knobs.** The
   default posture for a new project is the lean end of every one of those
   knobs — offline research where live isn't required, the lightest
   apparatus policy the source actually needs, index off. Present the
   exhaustive alternative (live research, a fuller apparatus, index on) as an
   explicit opt-in the user chooses through the same knobs, never as a
   separate code path.
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
   heavier apparatus, an occurrence index — it will never read. Naming that
   trade-off up front is cheaper than discovering it mid-project. Defer
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

1. **Existence check first**, before any dependency preflight or validation:
   if `.claude/literary-translator/profile.yml` is absent, copy
   `assets/profile.example.yml` to that exact path (guarded on absence — an
   existing filled-in profile is never touched again) and HALT, naming the
   path and instructing the user to fill in every placeholder. Do not run
   dependency preflight or schema validation in this branch.
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
5. Validate whole-file shape via
   `jsonschema.Draft202012Validator(profile.schema.json, format_checker=jsonschema.FormatChecker())`,
   loaded from the plugin's own `assets/schemas/profile.schema.json`.
6. Only once schema passes, run procedural checks: `source.path` must exist
   (for every format including `custom`; for `custom`, this is the
   primary/representative sanity-anchor input, while `manifest.json`'s
   `source_inputs[]` remains the authoritative full file list);
   `project.durable_root`'s parent must exist/be writable and must NOT
   resolve under `/tmp`/`scratchpad` (`durable_root` itself need not exist yet
   — Step 0a creates it);
   `output.destination`'s parent is checked only when it resolves outside
   `durable_root`; `source.language.particle_config`'s file existence is
   NOT checked here (deferred to end of Step 0a).
7. Whole-profile placeholder-substring scan (every field, not a named
   subset): FATALLY reject if any value anywhere still contains
   `/ABS/PATH/TO/YOUR_PROJECT`, `/ABS/PATH/TO/YOUR_SOURCE`, or
   `YOUR BOOK TITLE HERE`.
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
find the newest installed `codex-companion.mjs`". That reason was false, and the
disproof is short: the script **reads** no `__file__` — its own location never
enters its search — and imports nothing plugin-specific; its entire search is
rooted at `os.path.expanduser("~")` against
`~/.claude*/plugins/cache/openai-codex/**/codex-companion.mjs` — a different
plugin's own install cache, found the same way regardless of where
`resolve_codex_companion.py` itself happens to be running from. A durable copy
globs the identical `~` paths and finds the identical companions. It is now
copied like every other self-anchored script; do not re-exclude it by
re-deriving this same plausible-sounding-but-wrong argument — a literal
occurrence count of `__file__` in the file is not this claim (the script's own
docstring has to discuss `__file__` by NAME to explain this exact history, which
makes a bare grep count useless as a re-check either way). Read
`tests/resolve_codex_companion.test.py::test_the_resolver_contains_no_executable_reference_to_dunder_file`'s
verdict instead — it parses the file with `ast` and only flags a genuine
executable reference (an `ast.Name` node), never a prose mention.

**Migration note, mandatory before `resolve_codex_companion.py` is copied on
any project scaffolded before this correction:** its destination,
`${durable_root}/scripts/resolve_codex_companion.py`, was explicitly EXCLUDED
from this copy pass until now, and the "unconditional overwrite, safe since
these files are never hand-edited" premise the rest of this copy pass rests on
was NEVER true for this one path — a project that hit the exit-2 default-
launch defect this correction fixes could have reasonably worked around it by
placing its own adapted copy exactly there, on the explicit strength of that
destination being documented as untouched. Copying over it unconditionally now
would silently destroy that adaptation with no backup and no warning, and on a
RESUMED project (outcome 2 above) this would happen with NO collision
detection of any kind — collision detection exists only on outcome 3's
ambiguous-adoption path, which a resumed project's own root marker match
bypasses entirely.

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
  - **A genuine regular file, byte-identical** to the shipped source → copy
    normally (a no-op overwrite of itself).
  - **Anything else** — a genuine regular file with DIFFERENT bytes, a
    symlink (identical-looking target or not — lstat does not resolve it,
    so its target content is never compared), a directory, or any other
    non-absent entry `os.lstat()` reports — → HALT before copying anything.
    Name the exact path and state plainly that a pre-existing,
    non-managed entry sits there and this copy pass will not touch it
    silently. Instruct the operator PER ENTRY KIND, never one generic
    "move or rename it" — renaming preserves bytes only for the entry
    kinds where the name and the bytes are the same thing:
      - **Divergent regular file** → move or rename the file itself aside.
        This genuinely preserves its bytes; the name is the only thing
        that changes.
      - **Symlink** → renaming the link is NOT preservation — it relocates
        the POINTER, not the bytes it points at, and that target can be
        transient, on a different filesystem, or itself deleted later.
        Instruct the operator to first copy out the RESOLVED target's
        actual content to a new location, THEN remove the symlink.
      - **Directory** → move the whole directory aside; this preserves its
        contents as a unit.
    Then re-run.

**This is deliberately a REFUSAL, not a clever preserving copy.** An earlier
version of this note specified renaming a divergent file aside to a
`.pre-upgrade-backup` sibling before copying over it, so the copy could
proceed unattended. That shape has three real failure modes none of this
document's own instructions can close, because Step 0a's copy pass is
orchestrating-session prose executed by hand, not atomic code with the
guarantees the shape would need: (1) a byte-identical-looking SYMLINK is
still a symlink after a naive copy-over-the-path — the copy follows it and
writes through to wherever it points, so the shipped file never actually
lands at the expected destination, silently defeating the very launch fix
this correction exists to deliver; (2) a DIVERGENT symlink's "backup" is
just the symlink renamed, preserving a pointer rather than the adapted
bytes it points at, which can go stale or vanish independently; (3) two
concurrent scaffolds of the same durable_root can both pick the same free
backup name and the second overwrites the first's backup. **A HALT closes
exactly these three** — it performs no automatic write to a divergent
destination at all, so there is no automatic-copy-through-a-symlink, no
pointer-only backup, and no concurrent-backup-name race, because none of
those three OPERATIONS ever run. It does NOT close every race this check
could conceivably have: `os.lstat()`'s classification and the copy that
follows it, in the absent and byte-identical branches above, are still two
separate operations, not one atomic one — an entry classified absent or
identical can change before the copy actually runs. That window is real,
it is not closed by refusing (refusing only ever applies to the divergent
branch, which performs no copy at all), and it is not being claimed closed
here; it is simply much less consequential in this shape (prose a session
executes by hand, once, rather than a machine loop an attacker can race
repeatedly) than the automatic-backup shape's three failure modes were,
which is why THOSE three, and not this one, are what this refusal exists
to close. Do not "improve" this back into an automatic backup-and-copy
without also closing all three of ITS failure modes for real, not just
for the ordinary sequential-regular-file case that happens to look safe
in testing.

This check has no "runs once, ever" property to claim, and does not need
one: unlike a backup-and-copy design (which only needed to fire on the
FIRST migration), a halt-based check is memoryless and correctly re-fires
on ANY future divergence at this exact path — whether from an old,
never-cleaned-up workaround or a genuinely new one an operator creates
later — treating both identically and safely, with no marker or prior-
version digest required to tell them apart. Every OTHER bundle member never
needs this treatment: none of them were ever excluded from the copy pass
before, so none of them have a population of pre-existing, possibly-
hand-adapted destinations to protect.

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

  **Known limitation, narrower than it was but not closed. Read this before
  trusting a clean report on a live or networked project.** Every sentinel
  lookup now goes through the directory descriptor the run holds — the census
  and the writer's `EEXIST` re-read alike — so no read can land in a
  different directory than the one the run opened. **That settles WHICH
  DIRECTORY and nothing about the entries inside it**, and two mechanisms
  reach a wrong answer without ever touching the pathname:

  - a sync client or restore tool rewriting sentinel entries **in place**,
    which leaves the directory inode unchanged, so the identity check sees
    nothing wrong;
  - a sentinel simply deleted after the census classified it PRESENT.

  A third is only partly closed: network-filesystem failover, remount or
  snapshot switching now surfaces as AMBIGUOUS and fails the run **if it
  invalidates the descriptor**, but a silent switch that keeps it valid does
  not.

  **What the dispatch gate now does about it, and what it still cannot do
  (#442).** The consequence used to be silent retranslation outright:
  `select_segments.py` gated only the segments it found PRESENT, so a marker
  that had since gone absent left that segment eligible and the refusal that
  would have protected converged work never fired. It now fires for the case
  the marker's own writer makes impossible: `ledger_update.py` cannot publish
  a `converged` ledger record without first writing the marker, so a selected
  segment whose materialized record says converged/stale while its marker
  reads ABSENT is refused, reported as `lost_sentinels`, and pointed at
  `backfill_ever_converged.py --apply` as the non-destructive remedy. The
  ledger status is a second witness in a different directory, written by a
  different writer, and one deleted marker no longer removes both.

  **The residual, stated as a state rather than a promise.** That second
  witness is mutable. A unit whose status has ALSO moved off converged/stale
  has neither witness left, classifies as `recoverable`, and is still
  dispatched silently. Two routes reach that state, and one of them needs no
  earlier re-dispatch at all: convergence raises the marker *before* it
  commits the ledger fragment, so a run killed between those two steps leaves
  a finished, reviewed unit at `in_progress` with its marker up — delete the
  marker after that and nothing remembers. (The other route is an
  authorized re-dispatch interrupted after the driver's own `in_progress`
  write.) Closing that needs provenance the one-bit marker does not carry —
  tracked as #443, with the dispatch-time race tracked separately as #621.
  Until then: treat a clean run as evidence about the moment it ran, and
  re-run it immediately before dispatching rather than relying on an earlier
  result.

  **Two earlier drafts of this note were wrong in opposite directions, which
  is why it is worth reading rather than skimming.** The first said the
  failure needs something renaming `segments/` — an understatement, since a
  rename is one mechanism and not a precondition. The second said closing it
  "needs a locking protocol honoured by everything that can touch
  `segments/`" — an overstatement that survived several review rounds because
  a limitation that sounds cautious never gets attacked. The descriptor was
  already open; the census simply was not using it, and PR review reproduced
  a clean report about a directory the project was not using.
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

For `custom` specifically: the schema (`profile.schema.json`) validates
shape only — the `extractor_path` key is required whenever `format: custom`,
value must be `string | null`. Step 0c owns the two procedural checks a
schema can't express:

- If `null`: valid, expected starting state — halt and co-design a
  hand-crafted Python extractor with the user (informed by
  `gutenberg-epub.md` as a starting pattern — the one working adapter;
  `plain-text.md` documents the same target shape as a forward spec for #62,
  not yet implemented), but its output contract is fixed — must produce a
  `manifest.json` matching the exact same shape `gutenberg_epub`'s adapter
  produces (block-ID types, `order_index`,
  `spine`/`segments`/`footnotes`/`verse.store`, `source_inputs[]`, and final
  `generation_hashes.source_extraction_hash`/`.source_input_hash` via the same
  two-phase write), and pass the same round-trip self-check suite plus
  `manifest.schema.json` validation that `extract.py.template` runs (or a
  documented equivalent). Once written, the project sets `extractor_path` to
  point at it.
- If non-null: FATAL rejection (before existence check) of any value
  containing `..` or starting with `/` — resolution is against a fixed
  subtree, `${durable_root}/scripts/custom_extractors/<value>`, never
  arbitrary. Then check it resolves to an existing file — FATAL, naming the
  unresolvable path, if not.

Honesty note for W2's managed gate (see below): for `custom`, that gate runs
schema validation + independent manifest-derivable re-derivation against the
custom-produced `manifest.json`, but SKIPS the region-hash pin entirely —
`extract.py` on disk (Step 0a's unconditional template copy) is never the
real custom extractor, so pinning it would certify nothing. See
`references/source-format-adapters/custom.md` /
`references/false-green-gate.md`.

## Step 0d — Resolve output-target adapter

Runs only when `output.v1_scope: assembled_book`. Under the default
`output.v1_scope: segment_drafts_and_audit`, Step 0d is a deliberate no-op —
zero resolution work, zero HALT risk — matching the proportionality
guardrail that a plain translate+gloss job never pays for assembly
machinery it will never read (`references/assembly-and-output.md`).

When `assembled_book` is selected, resolve the already-schema-validated
`output.target` (`obsidian` | `epub` | `custom`) via `output_resolve.py`'s
resolution logic, plus read `output.name_display`, `output.index`, and the
one `output.adapter_config.<target>` sub-block matching the resolved
target — the others sit inert. This step depends ONLY on the
already-validated `profile.output` block (no manifest, no ledger, no draft
required yet) — the same "resolve early, from validated shape alone"
posture Step 0b/0c already apply to `verse_policy.mode`/`source.format`, so
a blocking co-design need surfaces at setup time, never mid-project.

- `target: obsidian` resolves to the built-in `render_obsidian` adapter
  (shipped this increment). `target: epub` resolves to the built-in name
  `render_epub`, a later-phase adapter not yet shipped — resolving the name
  now is exhaustive enum coverage, not a claim the renderer exists.
- `target: custom` specifically: the schema validates shape only — the
  `adapter_config.custom.renderer_path` key is required whenever
  `target: custom`, value must be `string | null`. Step 0d owns the two
  procedural checks a schema can't express, the same split Step 0c already
  applies to `source.adapter_config.custom.extractor_path`:
  - `null` — valid, the expected starting state — HALT and co-design a
    hand-crafted Python renderer with the user (informed by
    `render_obsidian.py` as a starting pattern), against the fixed
    `render(nodestream, canon, profile, out_dir) -> dict` entry-point every
    built-in adapter implements
    (`references/output-target-adapters/README.md`).
  - Non-null — FATAL rejection (before any existence check) of any value
    containing `..`, starting with `/`, or not matching the schema's
    `^[A-Za-z0-9._/-]+$` pattern. Resolution is against a fixed subtree,
    `${durable_root}/scripts/custom_renderers/<value>`, never an arbitrary
    filesystem location. Only then does Step 0d check the resolved path
    actually exists — FATAL, naming the unresolvable path, if not.

Unlike a Step-0c custom-source HALT, which blocks the whole project before
extraction can even begin, a Step-0d custom-target HALT blocks only
assembly (W9) — a project can still scaffold, translate, and converge every
segment with the co-design conversation still outstanding, and only hits
this HALT once `output.v1_scope: assembled_book` is actually chosen.

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
  and `--retry` overrides only the `review_queue` exclusion;
  `canon_adjudication_audit.py` never writes a verdict, it blocks. A frozen
  row is repaired only by a hand edit of `canon.json` that re-translates
  every segment using that term — which is why an accuracy decision, a
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
  defect class. **The billable unit is the COLD START, not the round and not
  the concurrency**: twenty sequential spawns cost the same as twenty parallel
  ones, so capping concurrency saves almost nothing, while a warm executor
  re-reads the contract incrementally and a cold one rebuilds it. Measured on
  two books driven through this plugin on the same day: the one that spawned a
  fresh executor per round burned 39.3M cache-creation tokens across 19 spawns
  against its own session's 3.2M, and cost 3.1× the book that applied every fix
  in-session.
  Two corollaries, and they are ONE rule with the above — unsafe apart. **Do
  not respond by enlarging the batches:** small parcels (3–7 loci) are what
  keeps attention on each finding, and executor attention is the only detector
  for a finding whose execution violates another contract rule. Small parcels
  are cheap only BECAUSE the executor stays warm; small parcels with a close
  between them is exactly the configuration that produced the figure above.
  **Do not collapse to a single actor either:** two independent readers exist
  to disagree with the lead's FRAME, not to add hands. Authorization may be
  granted per defect class; the record stays per item, always.
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
  three places, and the finished book beside it is none of them.** When a series
  gets its next volume, a completed durable root is usually sitting in the same
  tree with working `scripts/`, a filled-in `style_bible.md`, a real
  `profile.yml` and a canon that took weeks. Copying it is the obvious way to
  start and it is the way a book inherits every defect the previous one already
  worked through — silently, because nothing downstream re-reads a decision that
  was right for the last book and wrong for this one.
  **The three legitimate inputs:** (1) **mechanics** — `scripts/`, `schemas/`,
  workflow and seed templates — come from the PLUGIN: point `durable_root` at a
  NEW empty directory (never one emptied by hand beside a live book) and let
  Step 0a's copy pass fill it from the plugin install path, never a sibling
  root, which is frozen at whatever version that book ran and will not say so;
  (2) **the general contract** comes
  from the shipped `style_bible.template.md` and is then filled in by interview
  — which is what upstreaming a learned rule into the template is FOR; (3)
  **whatever outlives a book** — pending contract corrections, a cross-volume
  name or person registry — comes from the series' own directory, the only place
  whose contents are about the SERIES rather than about one book.
  **Never copied, and what each one breaks:** the previous `style_bible.md`
  (template plus that book's accretions — rulings whose reasons are gone,
  enforced against a different text); `canon.json` (book-shaped: duplicate
  spellings that resolved to one target *in that book*, a `review_queue` left
  unfrozen for *that book's* cast); `runs/`, the ledger, `segments/`,
  `.ever_converged.*` sentinels, `.codex_job.*` (run state — a stray sentinel
  asserts that a unit converged once, a claim about a book that does not exist
  yet); `profile.yml` verbatim (it carries `v1_scope`, effort and the language
  config of a different source).
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
`profile.yml` and every other just-scaffolded file." Mechanically enforced,
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
changes the exit code in either direction, so an advisory neither blocks a book
you legitimately want to translate nor rescues a failing extraction.

It means a source EPUB is probably in **visual order** rather than logical
order — the usual result of a PDF-to-EPUB conversion. Extraction is byte-faithful
and correct; the mangling is upstream. **Do not send a fix to the extraction
stage.** The damage lands on the LLM turns instead: a visual-order run tears
words apart, so a reviewer reads a stranded fragment as a real word and files a
finding against a CORRECT draft, and a translator can invert who did what to
whom. Both have happened on a live book, the second reaching a converged draft
that a full review round had already called clean. No deterministic gate can
catch this class — token counts, digests, schema validation and `validate_draft`
never read what a fragment MEANS.

**The scan is a SCREEN, not a verdict.** It detects visual-order *handling* (a
terminal punctuation mark leading an RTL token, which logical order cannot
produce), not the word *reordering* that actually tears tokens. Adjudicate it:

1. Read the sampled units the WARN names, in the manifest, against the source.
   The sample is printed as `\uXXXX` escapes on purpose — **never judge RTL text
   by looking at it**, because a bidi terminal renders a corrupted token
   identically to an intact one. Settle it on the codepoints.
2. **Negative** — the signature fired on something benign: record that in the
   project's notes and carry on. Nothing else to do.
3. **Positive** — paste the clause below into the project's own
   `style_bible.md`, under `### E-traps`. That is the one place all three turns
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

```markdown
#### E-traps: visual-order source

The extracted source text of THIS book is in VISUAL order, not logical order: a
PDF-to-EPUB converter emitted runs whose words appear in display sequence, and
extraction preserved those bytes faithfully. The segpack, `manifest.json` and
the source EPUB all carry the SAME mangled bytes — there is no unmangled copy to
consult, so reconstruction is the only recovery. Binding on every turn:

1. **The token order you read is not the author's word order.** Where a span is
   visually ordered, reconstruct the logical clause before you translate, judge
   or edit it. Reconstruction is bounded: reverse the order of WORDS, never the
   characters inside a word (words are themselves stored logically); reverse
   only a span you have established is WHOLLY reordered, never a partly-affected
   line; and PRESERVE the internal order of any embedded left-to-right run — a
   Latin name, a citation sigil, a digit sequence. Treat the result as a
   hypothesis: keep it only if it yields a grammatical clause consistent with
   the surrounding context. Where the span is ambiguous or mixed-direction,
   reconstruct from context and say so — never apply reversal mechanically.
2. **A short token stranded at a span boundary is usually half of a torn word,
   not a word.** Do not file a word-order or torn-token finding, and do not act
   on one, until you have reconstructed the span. Then report only what SURVIVES
   reconstruction — this is not an instruction to suppress findings.
3. **Never judge an RTL token by how it looks.** A bidi terminal renders a
   corrupted token identically to an intact one. Settle any such question on the
   codepoints.
```

### An empty content unit is refused at W2 (#397)

`run_derivable_checks` refuses two shapes that used to pass W2 and then left
their segment convergeable **only on an invented-text draft** — the faithful
draft is rejected forever — surfacing only much later, after a paid translation
job had already run:

- **`no_untranslatable_empty_blocks`** — a block cited by a segment's
  `block_ids` whose `plain_text` is empty while its `source_html` is not. A
  purely structural node (a scene-separating `<hr>`) emitted as a content block
  is the usual cause. `validate_draft` falls back to `source_html` when
  `plain_text` is falsy, so it reports the (correctly) empty draft block as an
  empty translation. The segment then converges only if the translator invents
  text for a node that has none — which is worse than the refusal. Runs under
  every `apparatus_policy`. A block that is the parent of exactly one
  non-embedded verse is exempt — that block legitimately carries no text of its
  own. *Whitespace-only* `plain_text` is NOT refused: it is truthy, so the
  faithful empty draft already converges.
- **`no_empty_footnote_definitions`** — a footnote whose definition block
  carries no text (whitespace-only included). Runs only under
  `footnotes.apparatus_policy: translate_all | preserve_source`, the two
  policies where footnote text is carried into a segpack at all. A footnote's
  `source_text` is taken from `plain_text` alone, with no `source_html`
  fallback, and a blank footnote translation is refused unconditionally.
  This check has **no reachability filter by design**: an empty definition is
  refused even if no segpack would have carried it. That over-catch is
  deliberate — the remedy is the same either way, and both attempts to model
  reachability got it wrong in a way that let the defect through.

**What to do when one fires.** The gate names the offending block ids or
footnote numbers. Adapt `${durable_root}/extract.py` so the node is not emitted
as a content block (or so the empty definition is not emitted), then re-extract.
Do NOT edit the check: the failure is real, and it is cheaper here than after a
translation round.

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
whole narrative, or several paragraphs, into a single block. Nothing else in
this pipeline is size-aware at the block level to catch it — the only existing
size check is the per-segment word count, and a 17 896-character block passes
that exactly as a 400-character one does.

**The census is a SCREEN, not a verdict.** The reference is this book's own p90
block size; the threshold is 10x that reference, computed only over the
distinct blocks some segment's `block_ids` names whose `plain_text` is
non-empty (this excludes `FN:` footnote-definition blocks, unattached
front/back matter such as the Project Gutenberg licence block, and an embedded
verse's text, which is lifted out of its carrier block). Below 30 such blocks
the check is silent about outliers — the reference would sit inside the
outlier's own neighbourhood — but the NOTE still prints the count, so the
silence is visible rather than absent. A genuinely long paragraph looks
identical here to an artifact; the census cannot tell them apart, and does not
try to.

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

Then, immediately after `validate_extraction.py` passes, run the
**wrapper-conservation gate (#196)** — a normal bundle-copied durable-root
script (unlike `validate_extraction.py` above), so run the durable copy:

```
python3 ${durable_root}/scripts/validate_conservation.py wrapper-conservation
```

This is **opt-in**: it is a no-op (prints a NOTE, exits `0`) unless
`profile.yml` declares `source.conservation` (`baseline_path` +
`provenance_path`, optionally `allowed_omissions_path`) — only relevant when
this project's source was hand-wrapped into its current format from some
other pre-wrap form (e.g. hand-split `pdftotext -layout` output turned into
an EPUB) and the exact pre-wrap text was preserved as an immutable baseline.
When declared, it is HARD: it compares the preserved baseline against
`manifest.json` via the wrap-time provenance map, at word-multiset
granularity (never byte-exact — legitimate reflow, e.g. the same
layout-whitespace collapse `source_html` → `plain_text` already performs,
must never false-RED), catching a hand-wrap that silently dropped baseline
content (#196), a block that reached the wrap but was truncated/hollowed
when written (the #202 case `validate_assembled.py` declines at assembly
time), and a block that was physically shuffled relative to its neighbors
even though its own content survived intact (`reading_order_reversal`,
checked against manifest `order_index`). Exit `1` HARD on any defect — the pipeline
advances to W3 ONLY on exit `0`. See `validate_conservation.py`'s own module
docstring for the full check spec and the three-artifact contract.

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

Then run `bootstrap_names.py` (configured from
`${durable_root}/languages/<particle_config's literal value>` — never
rebuilt from `source.language.code` alone) to get frequency-ranked name
candidates. **1.3.5:** curate and batch those raw candidates with
`scripts/glossary_batch_plan.py` FIRST — it reads `name_candidates.json` plus
the current `canon.json`, drops every candidate already resolved there (an
`entries{}` key OR a non-retried `review_queue[].source_form` — the #101
filter, now enforced in code, not merely delegated as prose), curates the
survivors by `likely_name`/`--min-candidate-freq` (the profile's
`glossary.min_candidate_freq` when set, else 2), force-includes any
`elision_ambiguous` pair for adjudication (#91), and prints one JSON line. If
that line is `{"no_new_candidates": true, "batches": []}`, every candidate is
already in canon — or, on an uncased-script source whose preset ships no
`name_inventory`, there were never any candidates to begin with — so SKIP
`resume_setup.py` and the glossary Workflow entirely this run, nothing to
research. **#290:** that SKIP branch is the one W3 path that never reaches the
glossary merge — and apart from the bootstrap command below, the merge is the
only thing that ever CREATES `canon.json` — so bootstrap it explicitly here,
or W3a below dies with `FATAL: canon.json not found`:

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
`references/orchestration-and-batching.md`). Only then does each batch run
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
(`#533`).

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
files and retrieves nothing at all. The split is what makes that boundary an
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
The snapshot stays inside PREPARE's own turn rather than becoming a step of
its own, but no longer to save a call: the **1.16.1** split already spends
one, taking the live ceiling from `1 + 3*(MAX_CITATION_RETRIES+1)` to
`1 + 4*(MAX_CITATION_RETRIES+1)`; **1.16.2** then took it to
`1 + (3 + WAIT_CALLS)*(MAX_CITATION_RETRIES+1)` = **19**, the wait having
stopped being a single agent call. What survives is the structural reason —
prepare is the one point both entry points into the review loop converge on,
so a resume-skipped batch, which runs neither the dispatch nor the wait,
still gets a snapshot and its evidence.
`offline` is the one exception: no citation, no reviewer, no snapshot, so the
merge consumes the attempt path there.

The judge's verdict is containment-guarded, as are the precheck's, the
wait's, and prepare's own: a reply carrying the failure sentinel ANYWHERE in
it rejects, because matching whole lines alone let a fail sentinel glued to
prose slip past and a trailing clean OK line then approve. The cost is a
false REJECT on a reply that only *discusses* its own fail sentinel, and
only ONE of the guarded sites recovers from that DETERMINISTICALLY inside
the run — the precheck, which falls through to the dispatch it would have
run anyway. **The citation review does not recover RELIABLY**, however much
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

**Canon human-adjudication audit, categories 1-4 (opt-in rollout gate)** —
`scripts/canon_adjudication_audit.py` enumerates every canon
name-adjudication a human/codex must sign off (duplicate source forms,
existing merges, all candidate missed-merge pairs, and un-drained
`review_queue[]` items) and cross-checks them against
`canon_adjudications.json`. **1.4.0:** the `basis:"sense_translated"` value
(`references/canon-and-glossary.md`) gives the glossary-pass agent a
truthful basis for a sense-translated speaking name that previously had none
— such a candidate now resolves straight into `entries{}` instead of parking
permanently in `review_queue[]`, so this gate's category-4
(`review_queue_unresolved`) blocks less often in practice; `review_queue[]`
now holds only genuinely disputed/unresolvable names. Run before Deliver (W7/W8):
`python3 ${durable_root}/scripts/canon_adjudication_audit.py --check` —
exit `0` = every required item has a matching `confirmed_ok` (or a valid
risk-acceptance / the queue is drained), `1` = blocking findings, `2` =
fatal. Add `--advisory` to report without blocking (preserves the plugin's
WARN-first name policy). **Status: categories 1-4 remain an OPT-IN gate** a
project enables for this Deliver-time invocation; the script defaults to
hard-blocking (exit 1) so a project that wires it in gets the full gate.
Category 5 (the homonym-split evidence audit) is a SEPARATE, MANDATORY
W-step — see immediately below — never opt-in, regardless of whether a
project enables this Deliver-time categories-1-4 gate. The accuracy calls
it audits are authored by a human
reviewer or a schema-validated codex workflow — the script never decides
identity itself. Enable ONLY when a per-person index, per-person bios, or
enforced cross-document consistency is in scope; on a plain translate+gloss
job leave it off — the lightweight `review_queue` remains the correct tool
for genuinely disputed/unresolvable names (a speaking name with a clear
sense-rendering resolves via `basis:"sense_translated"` instead, so the
queue's role is narrower than it once was, not eliminated).

**Mandatory homonym-split evidence gate (category 5, always runs)** — unlike
the categories-1-4 gate above, this invocation of the SAME
`canon_adjudication_audit.py --check` is never opt-in and never waits for
Deliver. Run it immediately after **both** W3-rejoin branches above — the
`{"no_new_candidates": true, "batches": []}` SKIP path and the "Otherwise
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
downgrades ONLY a categories-1-4 finding (those stay governed solely by
whether a project has separately opted into the Deliver-time gate above) —
it NEVER masks `homonym_split`'s missing/stale verdict, `collapsed_split`,
`evidence_unverified`, or `canon_absent_with_senses`. So this W-step still
exits `1` — HALTING here, before W3a, nothing dispatches past it — whenever
`canon_senses.json` is non-empty and carries any unverified, stale, or
collapsed split, even on a project that has never opted into the
categories-1-4 gate. On a project whose `canon_senses.json` is absent or
schema-valid-empty, this call is a no-op pass-through (`gate_passed: true`)
— run it unconditionally rather than special-casing whether the sidecar
exists. It says so explicitly rather than reporting a bare zero: the
report's `homonym_split` row reads `NOT ENUMERATED` and the summary carries
`senses_enumerated: false`, so a vacuous zero is never mistaken for an
enumerated-clean one.

**Skeptic pass (RFC #215 Phase 2, opt-in + advisory)** — if
`glossary.skeptic_pass.enabled` is true in `profile.yml`, run the
structural-risk triage + adverse-only skeptic pass immediately after the
mandatory homonym-split gate above and before W3a. Every enabled pass
re-derives its own worklist fresh (never trusts a stale one):
`suspicion_scan.py --canon ${durable_root}/canon.json --manifest
${durable_root}/manifest.json --particle-config <literal value>
--research-mode <profile's glossary.research_mode> --source-format
<profile's source.format>` plus the profile's `glossary.skeptic_pass`
overrides (`--dispersion-threshold` / `--sample-cap` /
`--windows-per-entity` / `--near-threshold` / `--near-cap` /
`--near-pair-budget` / `--citation-block-types`, else
`skeptic_constants.py` defaults), writing
`${durable_root}/suspicion_worklist.json`. Then `skeptic_setup.py`
(`kind="skeptic"`, a resume domain fully separate from `resume_setup.py`
— never edits it, never adds a `kind` to it), invoked with **`--source-lang
<the SAME source-language label you interpolate into the template's
`{{SOURCE_LANG}}` placeholder at Step 0a>`** (REQUIRED — folded into the
skeptic input_digest, so changing the prompt's source-language label forces
a fresh RUN_ID; NOT reconstructed from `source.language.code`, since the
glossary/skeptic templates render `{{SOURCE_LANG}}` as a human-readable name,
not the locale code) plus its resolution flags (`--particle-config`,
`--research-mode`, `--source-format`, `--batch-agent-cap`, and any
`glossary.skeptic_pass` tuning overrides — mirror the `suspicion_scan.py`
values above; run `skeptic_setup.py --help` for the full set), validates
that worklist's freshness (schema + `producer_input_digest`), resolves the
skeptic RUN_ID, and atomically writes
`${durable_root}/skeptic/runs/{RUN_ID}/assignments.json` (aggregate) plus
one `assignments_{index}.json` per batch — BEFORE any dispatch. Only then
instantiate `skeptic-pass-wf.template.js` fresh from the plugin's current
copy (see Step 0a) and run it, passing `args` = the batches grouped from
`assignments.json`, each entity's `windows[]` enriched with the resolved
whole-block `text` (`manifest.blocks[window.block].plain_text`) alongside
the assignment's own fields. The Workflow's own dispatch → bounded-wait →
`skeptic_ready.py --validate-fragment` per batch, then one serialized
`skeptic_ready.py --merge-fragments` plus a disk-independent
`skeptic_ready.py --verify-merged`, produce
`${durable_root}/skeptic_triage.json`. Finally run `skeptic_report.py` to
render the findings for a human.

**Agent-trust & tamper-detection (H1):** like the glossary pass, this opt-in
pass feeds source-text windows to a file-capable `codex:codex-rescue` agent —
it carries the same pipeline-wide agent-trust, adding NO new filesystem
privilege and NO new accepted-state write path (the triage schema is
adverse-only and no freeze/merge reader opens `skeptic_triage.json`). As a
best-effort integrity tripwire, `skeptic_setup.py` stamps a THREE-way hash
triplet — `canon_sha256`/`manifest_sha256`/`senses_sha256` (#243 made
`canon_senses.json` a third authoritative frozen input this release, so it
is stamped and checked alongside the other two) — into the aggregate
manifest. Both the stamper and every verifier ultimately reduce to the
same `compute_frozen_input_hash_from_state` (`suspicion_scan.py`) — no
second, independently-drifting copy of the hash formula exists to fall out
of sync — but WHEN and HOW each side reads the bytes it hashes differs, and
the difference is load-bearing. The stamper always hashes a `(state,
content)` pair it already captured ONCE at derivation-read time, before the
freshness/worklist check that same snapshot fed — a fresh re-read at
stamp-write time would instead record whatever is on disk at THAT later
moment, silently adopting any mutation that landed in the window between
derivation and stamping as if it had been there from the start. Verifiers
are not uniform either: `canon.json` and `canon_senses.json` are hashed
from a captured snapshot too — the SAME one a downstream parse of the
competitors universe (#243) goes on to reuse — so the tamper comparison and
that parse can never independently disagree about which on-disk version
each one describes.

All three frozen inputs — `canon.json`, `manifest.json`, `canon_senses.json`
— now go through that one gated capture step alike, with no exception:
`frozen_input_check()` drives all three off a single table, and the loop
over that table is the only place `frozen_input_check()` itself reads a
frozen input's bytes for the H1 tamper comparison. It is not the only place
in `skeptic_ready.py` that reads canon/senses bytes at all —
`_resolve_competitors()` deliberately falls back to a plain fresh read of
`canon.json`/`canon_senses.json` when the caller has no H1-approved
snapshot to reuse for that particular input (`run_validate_fragment`, which
never calls `frozen_input_check()` at all; and `run_verify_merged` for
whichever of the two inputs happened to have no stamp to compare against).
That fallback is intentional, not a gap this round closed — see
`_resolve_competitors()`'s own docstring. `manifest.json` used to be wired
in separately, as a
hand-written call that captured its own snapshot outside that gate — which
is exactly how a stamped `manifest.json` read failure could escape the
standalone check raw, despite that mode's own documented "never crashes"
contract. Folding it into the same table doesn't just fix that one gap, it
removes the capacity for a future fourth frozen input to reopen it the same
way *inside `skeptic_ready.py` itself*: the only way to wire a read into
this module's own H1 tamper-comparison path is to add a table entry, and
there is no longer a code shape in `skeptic_ready.py`'s
`frozen_input_check()` that reaches a frozen input's bytes for that
comparison any other route. That is narrower than "any read of a frozen
input's bytes at all" — `_resolve_competitors()`'s fallback reads
(described two paragraphs above) stay exactly as un-gated as before; they
were never the gap this table closes, since they answer a different
question (what is the competitor universe) than the one the table's H1
comparison answers (did this frozen input change). `manifest.json`'s
snapshot is captured through that same gate but, having no downstream
parser in this module the way canon/senses do, is discarded once its own
tamper comparison is done.

That table (round 7) closed the read-side gap inside the verifier, but it
was still, by itself, only ONE of three independent enumerations of the
frozen-input set: `skeptic_setup.py` (the stamper) separately hand-wrote
the three `"..._sha256": ...` fields into `assignments.json`, and this
schema separately declared them — a fourth frozen input could be added to
the stamper and the schema and simply never typed into
`frozen_input_check()`'s table, and nothing would fail; it just wouldn't be
checked. Round 8 (#243 codex follow-up) closes that: `FROZEN_INPUT_SPECS`,
a single `(key, filename label, stamp field name)` tuple in
`skeptic_constants.py`, is now the shared source both sides iterate — the
stamper builds every stamp field in `assignments.json` from it (no
hand-written `"..._sha256"` line remains in `skeptic_setup.py`), and the
verifier builds its own check table from the exact same tuple. A frozen
input can no longer be wired into the stamper without also being wired into
the verifier, because there is no longer a place in EITHER script's own
code to add one without touching that shared tuple first. The schema's
`canon_sha256`/`manifest_sha256`/`senses_sha256` properties are still
separately-declared static data (JSON Schema cannot derive from a Python
tuple) — a parity test asserts the schema's declared **top-level property
names ending in `_sha256`** equal the stamp-field set `FROZEN_INPUT_SPECS`
derives, so a `_sha256`-suffixed schema property added without a matching
tuple entry (or the reverse) fails that test rather than going unnoticed.
That suffix filter applies to the SCHEMA side of the comparison only —
`FROZEN_INPUT_SPECS`'s own stamp-field set is never filtered by it — so a
schema property that stamps a frozen input WITHOUT a `_sha256` name (or
one nested below the top level, like `assignments[].evidence.sha256`)
stays invisible to this parity check only when it exists on the schema
side ALONE, with no matching `FROZEN_INPUT_SPECS` entry. If the same
non-suffix field is ALSO present in `FROZEN_INPUT_SPECS` (a tuple entry
whose `stamp_field` happens not to end in `_sha256`), the equality DOES
fail: that field appears in the tuple's unfiltered stamp-field set with
nothing on the filtered schema side to match it.

`FROZEN_INPUT_SPECS` binds the stamper and the verifier's tamper check —
that is its whole guarantee, and even that much is conditional on every
tuple entry having its OWN key (round 11, #243: a new entry that instead
REUSES an existing key aliases that key's existing snapshot at the
stamper/verifier's own hand-maintained lookup dicts instead of raising.
All four such maps now carry the same fail-closed
`sorted(<keys>) != sorted(<spec keys>)` guard — `suspicion_scan.py`'s and
`skeptic_setup.py`'s two digest functions and `skeptic_setup.py`'s own
`run()` block (round 11), plus `skeptic_ready.py`'s `paths` map in
`frozen_input_check()` (round 12) — and an AST-driven ALLOWLIST test parses
every top-level `*.py` file directly under `SCRIPTS_DIR` and requires each
of the four sites to carry a guard structurally identical to a canonical
shape. This replaced a six-round DENYLIST (#243, rounds 11-16, preserved
in git history, not reproduced here): each round found one more way to
weaken a guard while still passing, and the check grew one more rejection
rule to name that specific weakening. Six rounds in, the reviewer's own
prescribed fix was to build a general reaching-definition/dataflow
analyzer inside a test, for four guards that are, in practice, textually
near-identical — a denylist has no bounded endpoint, since it can only
enumerate weakenings someone has already thought to check for.

The allowlist inverts the approach: a candidate `if` qualifies only if it
is structurally identical to the canonical shape, and anything that does
not match is rejected outright, with no attempt to resolve or characterize
why it differs. Concretely: the test must be exactly `sorted(X) !=
sorted(Y)` for two bare-Name operands, with the builtin `sorted` itself
unshadowed anywhere reachable from the guard's owning or module scope;
exactly one of X/Y must be bound, as its owning scope's SOLE binding, to
one of the two canonical `FROZEN_INPUT_SPECS` key-projection
comprehensions the real sites actually ship — the subscript form
`[spec[0] for spec in FROZEN_INPUT_SPECS]` at three sites, or the
destructure form `[key for key, _label, _stamp_key in FROZEN_INPUT_SPECS]`
at `skeptic_ready.py`'s `frozen_input_check()`; and the body must directly
raise `AssertionError` carrying the anchor phrase. A hoisted `if _flag:`
(the comparison assigned to a name first) simply isn't this shape — a
bare-Name test — so it needs no special-case rejection rule of its own,
unlike the denylist version it replaced. This is strictly stronger
than that: the denylist enumerated specific weakenings to reject, and
each round found one it had missed, while the allowlist rejects any
candidate that does not match the canonical shape — no dataflow
analysis needed, only structural AST comparison plus a few
scope-bounded binding facts. The remaining trust boundary is not an
unanticipated class of weakening but a gap in the structural template
itself — some node facet the match doesn't pin. Structural completeness
is what has to be gotten right, and unlike "did we think of every
weakening," it is a property that can be checked directly.

`EXPECTED_GUARD_SITES` is still a hand-maintained set of the four (file,
function) obligations a guard must resolve to — there is no way to derive
"which four functions should own a guard" from the source itself — but it
no longer also pins each site's own two operand names the way an earlier
version of this check did: the structural requirements above already make
a "right shape, wrong operand" copy-paste pointless to separately guard
against, since the spec operand must independently prove itself against
the canonical projection shape and the other operand's own spelling is
never validated at all (it is simply the value under test). A guard's
owner is the nearest lexically-enclosing function, tracked by a scope walk
that resets attribution to `None` on crossing a `ClassDef` boundary — a
class body's own top-level statements run in the class's namespace, not
the enclosing function's runtime scope, so a guard relocated into a class
nested inside its owning function is reported as unowned rather than
misattributed. Separately, an owner only counts as matching an
`EXPECTED_GUARD_SITES` entry when it resolves to the UNIQUE module-level
def of that name; two module-level defs sharing a name — a dead `if
False: def run(): <guard>` sitting beside the real, unguarded `def run()`
— are reported AMBIGUOUS rather than silently resolved toward whichever
copy happens to carry the guard. Either way — wrong owner, no owner, or
an ambiguous one — a report happens only because a human already
enumerated the sites being compared against, not because the test derives
on its own which functions should own one. A guard is still located only
by this exact structural shape and file-set scan — a copy that lands in a
nested subdirectory the scan doesn't recurse into (no shipped script does
today) is still invisible to it.

Also invisible by construction: an unknown FIFTH key-indexed consumer
added later with no guard of its own at all — there is nothing to compare
it against. The durable fix, tracked as a follow-up, is to centralize all
four guards behind one shared helper each consumer calls, closing this by
construction instead of by enumeration.

What none of this proves, and does not claim to: that a guard's `if`
DOMINATES the protected access at runtime. A guard of the exact required
shape, in the exact expected function, sitting after an unconditional
`return` (or any other control-flow path that skips it) passes every
static check here while never executing. This is not just argued but
verified experimentally: relocating a guard to dead code placed after its
own function's `return` (function otherwise intact) leaves the static AST
check blind at every one of the four sites, as expected — but the
BEHAVIORAL suite's coverage of that same relocation is uneven, not
absent, and not universal either. At
`suspicion_scan.py::compute_producer_input_digest`, the relocation was
still caught: its behavioral test file failed (4 failing tests) even
though the static check stayed blind. At `skeptic_setup.py::run`, nothing
caught it — the static check stayed blind AND the plugin's entire test
suite stayed green (2799 passed, 1 skipped, 2 xfailed). That measurement
established that `run()`'s own copy of this guard was, at the time, the
one site among the four with no mismatch-driven behavioral test of its
own. Round 14 (#243) closed it: `skeptic_setup.test.py`'s
`test_run_fails_closed_on_frozen_input_specs_key_mismatch_after_upstream_digests`
wraps the real `resolve_skeptic_run()` so a duplicate-key
`FROZEN_INPUT_SPECS` mutation is injected only after both upstream digest
guards have already run against unmutated state, then asserts `run()`'s
own guard raises and names both sorted key lists — verified red the same
way, against the guard relocated to the same dead code. That test's own
docstring records a residual gap: weakening the guard to a bare LENGTH
comparison still passes green against this duplicate-key mutation (only a
same-count divergent-key swap would slip past it), a gap
`compute_skeptic_input_digest()`'s own round-11 parametrized test already
covers directly. The other three sites' behavioral tests
(`suspicion_scan.test.py`'s and `skeptic_setup.test.py`'s own
`duplicate_key_entry`/`same_count_key_swap` cases for the two digest
functions, `skeptic_ready.test.py`'s
`--verify-merged`/`--check-frozen-inputs` case for `frozen_input_check()`)
close this reachability gap for their own sites, not because they were
built to prove reachability, but because driving the guard through a real
key mismatch necessarily also proves it executes.
`FROZEN_INPUT_SPECS` does NOT bind the earlier
`read_frozen_input_snapshot()` capture in `skeptic_setup.py`, and its
SIGNATURE does not bind `compute_producer_input_digest()`/
`compute_skeptic_input_digest()` either — both still take a fixed
positional/keyword canon+manifest+senses signature, unrelated to this
tuple; a fourth frozen input still needs its own hand-added parameter (and
a matching update at every call site) before either digest can hash it at
all. Round 9 shipped with that gap silent, and it remains true only
CONDITIONALLY, historically stated: a fourth frozen input added to
`FROZEN_INPUT_SPECS` (plus the schema) is captured, stamped, and
H1-tamper-checked ONLY once it ALSO gets its own hand-added entry at every
one of those other manual sites — never from the tuple entry alone, and
never by reusing an existing key at any one of them (rounds 11 and 12
closed that silent-alias failure mode at all four key-indexed maps, and
`test_frozen_input_specs_keys_are_unique` closes it a layer beneath, on
the tuple itself; the schema's own `_sha256`-suffix parity test stays
deliberately blind to it, comparing STAMP FIELDS rather than keys, which
is why the uniqueness invariant is a separate test with its own
unambiguous failure message). Assuming those manual sites ARE correctly
updated, the gap that remained through round 9 was narrower but still
real: the fourth input would be captured, stamped, and H1-tamper-checked
correctly, yet invisible to both freshness digests — a mutation to it
BEFORE `skeptic_setup.py` ran would leave a stale worklist's
`producer_input_digest` unchanged, so the stale worklist would still read
as fresh and get (re)certified against the new state, the same
stale-certified-as-fresh failure mode this release closes for
`canon_senses.json`, just re-opened at a boundary this tuple didn't reach.

Round 10 (#243) closed that silent half WITHOUT touching either
signature or any call site: each function body now builds its own
`{key: (state, bytes)}` map from the parameters it already receives and
asserts that map's key set equals `FROZEN_INPUT_SPECS`'s key set BEFORE
hashing anything. A parameter added to the signature with no matching
`FROZEN_INPUT_SPECS` entry (or a `FROZEN_INPUT_SPECS` entry with no
matching parameter/map entry) now raises `AssertionError` the first time
the function runs, instead of the digest silently omitting the new input
forever. Both digest functions were re-derived this way against
`FROZEN_INPUT_SPECS`'s current 3-entry order and verified byte-identical
to the pre-round-10 formula on a fixed fixture — this is a hardening of
what already-shipped projects hash, not a digest-compatibility break.

Round 11 (#243): that key-SET comparison itself had a gap -- a `set()` on
both sides collapses duplicates, so a `FROZEN_INPUT_SPECS` entry that
REUSES an existing key (rather than getting its own) reduced to the same
key set as the hand-maintained map and passed the guard silently, then
aliased the reused key's snapshot into the loop instead of hashing the
new input at all. Both digest functions now compare the full,
non-deduplicated, sorted KEY LIST instead of a set -- strictly stronger,
since a duplicate changes the list even when it doesn't change the set —
and were re-verified byte-identical to the pre-round-11 formula on the
same fixed fixture.
`skeptic_constants.py`'s own comment next to `FROZEN_INPUT_SPECS` lists
every site a new frozen input still needs by hand, and which of them now
fail loud versus which (the raw capture calls only) still don't.
Generalizing the two digest functions' SIGNATURE so this tuple could drive
parameter names too was evaluated (#243 round 9) and deferred again in
round 10: both are direct-called by fixed parameter name/position from
dozens of sites in `tests/skeptic_setup.test.py` and
`tests/suspicion_scan.test.py`, several of which pin the exact NUL-byte
framing between two specific adjacent parameters — a cross-file
test-authoring change, not a same-file mechanical one. Round 10 fixed the
silent-omission risk by hardening the function BODIES instead.

Detection now fires at **two** decision points, not one. The first is
`skeptic_ready.py --verify-merged`'s own internal check, which runs after a
successful merge, as before. The second is new this release and is the
substantive part of the fix, not a footnote: `skeptic_ready.py
--check-frozen-inputs` — a standalone CLI mode built from the exact same
shared `frozen_input_check()` function `--verify-merged` calls internally —
is now called UNCONDITIONALLY from the Workflow's `notReadyBatches` branch,
before it concludes that a batch never becoming ready is merely an ordinary
advisory outcome. Previously, that branch gave up with a bare
`fragment-check-failed` and never called `--verify-merged` at all, so a
frozen input tampered sometime after `skeptic_setup.py` stamped this run
but before any batch's fragment ever validated would go completely
unreported as the FATAL tamper it is — the not-ready path is exactly where
a run ENDS when something has already gone wrong, so it is also exactly
where a tampered input was most likely to go unnoticed: the old behavior
reported the most alarming possible state (a frozen input changed
mid-pass) as the blandest possible outcome (an ordinary "some batches
didn't finish" advisory).

Sharing that one function does NOT mean the two modes always agree, and the
divergence is deliberate, not a bug: they answer differently on a READ
failure for a frozen input, as opposed to a hash MISMATCH, which both
always treat as fatal. `--verify-merged` fails CLOSED on a read error — it
raises raw — because degrading a frozen input it still needs to parse (the
#243 competitors universe projects from `canon.json`/`canon_senses.json`)
would silently empty that universe and let every ambiguous form sail
through unflagged, exactly the fail-OPEN failure mode this release closes
elsewhere. `--check-frozen-inputs` tolerates the same read error and
degrades instead, because it never parses anything downstream — it only
ever answers "did a frozen input change," and raising there would trade its
own documented "never crashes" contract for a check that buys nothing in
return. Read the two modes as applying different, equally deliberate rules
for an unreadable input, not as two implementations of one rule that happen
to disagree.

This catches ACCIDENTAL / non-adversarial mutation of the frozen inputs (a
crash, a stray process, a buggy well-behaved agent) — it is NOT a hard
guarantee: a prompt-injected agent with pipeline-wide FS-write can rewrite
or delete the co-located stamp to match its tampered canon. A sound version
(anchoring the setup-time hash in a trusted CLI channel) is deferred to
Phase 3 alongside the warn→block flip; full agent containment is the
out-of-scope pipeline-wide FS-sandbox concern.

**Exit-code contract:** this block is advisory FOR SKEPTIC FINDINGS — a
non-zero exit from `suspicion_scan.py` / `skeptic_setup.py`, or a Workflow
result of `merged:false` for an ordinary skeptic reason (batch-too-large /
fragment-check-failed / coverage-gap / `verify-failed`), HALTS only the
skeptic pass for this run; log it and proceed straight to W3a regardless.
**EXCEPTION — a frozen-input mutation is FATAL to the WHOLE pipeline, NOT
advisory:** if the Workflow result carries `frozenInputMismatch: true`
(reason `"frozen-input-mismatch"` — either `skeptic_ready.py
--verify-merged` after a successful merge, OR `skeptic_ready.py
--check-frozen-inputs` from the `notReadyBatches` branch when a batch never
became ready, re-hashed `canon.json`/`manifest.json`/`canon_senses.json`
and found one changed on disk since `skeptic_setup.py` stamped this run —
see the H1 paragraph above for why both decision points exist), do NOT
proceed to W3a. The frozen inputs W3a consumes (segpack canon injection,
translation) were mutated mid-pass, so continuing would bake that mutation
into accepted state. HALT here (FATAL), surface the mismatch, and require
restoring + re-freezing/re-validating the trusted `canon.json`/
`manifest.json`/`canon_senses.json` before any re-run. (This is the one
non-advisory outcome of the opt-in pass; every skeptic *finding* stays
advisory.)
**The cat-5 audit command (`canon_adjudication_audit.py --check`,
immediately above) is UNCHANGED by any of this** — it never reads
`skeptic_triage.json` / `suspicion_worklist.json`, and its own summary +
exit code are byte-identical whether or not this opt-in pass ever ran;
`skeptic_report.py` is a wholly separate, advisory command a human runs to
see the skeptic pass's own findings, never itself a gate. When
`glossary.skeptic_pass.enabled` is false/absent (the default), skip this
entire block.

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

**W5 Mass-translate** — instantiate `mass-translate-wf.template.js` fresh
from the plugin's current copy every run (never reuse a stale generated copy).
A concrete preflight, `scripts/select_segments.py`, runs before `pipeline()`
is called. It:

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
  its `step1_gate_passed` journal entry and prints the same one-line
  disclosure on its own stderr.
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
`mass`) before the Workflow tool ever launches: it derives the resume-
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

Only then is `mass-translate-wf.template.js` instantiated (fresh from the
plugin's current copy every run — never reuse a stale generated copy),
substituting the resolved `{{RUN_ID}}` alongside every other token, and
`pipeline()` launched. **#197:** the same instantiation substitutes
`{{EFFORT}}` (`engine.effort`) and `{{MODEL}}` (`engine.model`, or an
empty string when unset) too; the `resume_setup.py` payload's `subst`
object must carry the resolved `effort` value as well (see the W3
glossary-pass note above — `compute_input_digest` fails loudly if it's
missing). **#409:** that instantiation also substitutes `{{MAX_CODEX_JOBS_PER_BATCH}}`,
a DERIVED token called out here rather than left to the generic "every other
token" rule above, because it cannot be read straight out of `profile.yml`. It
is a BARE integer: `engine.max_codex_jobs_per_batch` when the profile sets it,
otherwise `profile.schema.json`'s own documented `default` for that field. The
profile key is deliberately OPTIONAL (making it `required` would reject every
profile written before it existed) but the token is NOT, and JSON Schema
`default` is an annotation no validator injects, so the orchestrator must apply
the fallback itself. It joins `SUBST_FIELDS`, so the `subst` object must carry
the resolved `max_codex_jobs_per_batch` exactly as it carries `batch_agent_cap`.
The refusal message deliberately does NOT report whether the limit was
configured or defaulted, and no token carries that provenance: the template
holds no such information, so the message cannot misstate it. That is a
correctness property, not an omission — do not "improve" it by threading a
provenance flag through. Leaving the token unsubstituted is a hard JavaScript
syntax error at instantiation (verified: `node --check` exits 1 on an
unsubstituted bare token), not a silent fallback — the same fail-loud property
`{{CITATION_CONTENT_TYPES}}` relies on. **1.4.7:** as part of that same instantiation the
orchestrator first runs `resolve_codex_companion.py --durable-root
${durable_root}` from the plugin's own install path — the orchestrating
session already has `{{PLUGIN_ROOT}}` in hand at this step, so there is no
reason to prefer the durable copy Step 0a now also places at
`${durable_root}/scripts/resolve_codex_companion.py` for the fully
self-anchored, no-orchestrating-session case (`segment_dispatch_driver.py`'s
own dispatch path uses that copy instead; see Step 0a's copy-pass section for
why this script needs no plugin-path-specific behavior either way) — ABORTS
W5 on any non-zero exit (codex is the required engine per R1 — fail-fast, not
today's silent no-draft hang), reads
the raw `companion_path` it prints, `json.dumps`-encodes that string ONCE, and
substitutes it as the `{{CODEX_COMPANION_PATH_JSON}}` token alongside every
other. Each per-segment translate/review dispatch then launches codex through
the detached `codex_job.py` driver (R1/R7), not a `codex:codex-rescue`
`agent()` call. See `references/orchestration-and-batching.md` for
the full `{{RUN_ID}}` derivation contract and digest definition, and
`references/ledger-and-resumability.md` for the `dispatch_token`
commit-gate chain this sets up for translate/review to enforce per segment.

**#412:** that same instantiation ALSO substitutes `{{PLUGIN_ROOT}}` — this
skill's own directory, the SAME value this skill's own
`{{PLUGIN_ROOT}}` placeholder already resolves to throughout this document
(Step 0: `${CLAUDE_PLUGIN_ROOT}/skills/literary-translator`), reused here,
never redefined — NOT `${CLAUDE_PLUGIN_ROOT}` itself, which makes
`codex_job.py` exit 2 with "does not resolve to a directory containing
assets/scripts/". Unlike
this skill's OTHER `{{PLUGIN_ROOT}}` occurrences — plain prose the reader
substitutes on the fly when typing an example command (Step 0, W2, W3) —
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

**#582 — why the ENTRY POINT stays `${durable_root}/scripts/`, and what
`--plugin-root` does and does not buy.** The W5-and-later commands below run
their scripts from the durable copy, which the same passes can write to (the
Step 0 / W2 / W3 commands already run from `{{PLUGIN_ROOT}}`). That asymmetry
is deliberate and this paragraph is the reason, recorded so it is not
re-raised per review: **`--plugin-root` moves only the CHECKER a script shells
out to; nothing here moves the ENTRY POINT.** Relocating individual entry
points to `{{PLUGIN_ROOT}}` is real defence in depth — it prevents direct
execution of a tampered durable copy of that one command — but it was
evaluated and not adopted as the answer here, because it does not close the
class. The class survives it for two measured reasons:

1. **The fix turn is a write-capable deputy.** On a non-clean, non-final
   review inside the fix-round budget, `runRound()` calls `callFix()`, which
   dispatches a PLAIN Claude agent (`agent()` with no `agentType` — not
   `codex_job.py`, so not sandboxed) and hands it a review whose
   `issue`/`suggest` text codex itself authored. Since #532 that turn applies an
   entry only where it can substantiate the claim against the source and refuses
   the rest, so it is a reader of that free text rather than an executor of it —
   but it is still a write-capable agent taking untrusted prose as input.
   `REVIEW_SCHEMA` constrains a finding's SHAPE (`loc`/`severity`/`issue`/
   `suggest`, strings, no extra keys); beyond that the only content check is
   that `loc` contains a colon (`AUTHENTIC_LOC_RE`) — `issue` and `suggest`
   are unconstrained prose. That turn is told to rewrite the draft, but it is
   neither forbidden nor technically prevented from writing elsewhere. So the
   surface that decides where a fix lands is an untrusted party's free text
   reaching a write-capable agent — a surface that sandboxing codex does not
   reach and that relocating an entry point does not narrow.
2. **A per-command rewrite does not converge.** The invocations are not a
   closed set: they live in this file, in `references/`, in the three
   per-pass TASK prompt templates and in the three workflow templates' own
   command builders, and a new one is added by any future dispatch. Trust
   that depends on every call site being spelled correctly is trust that
   silently lapses the first time one is not.

Note what `--plugin-root` actually does, since it is easy to overstate: it
BYPASSES a possibly-tampered durable sibling by executing the trusted plugin
copy instead. It does not detect, report, or repair the tampering — a poisoned
durable checker simply sits there unexecuted. `plugin_bundle_hash` does not
detect one either (`cache_key.py` reads the Step-0a marker; it never re-hashes
the copies).

The durable root holding one project's data, and a human reading the
deliverable, limit the expected blast radius of the DELIVERABLE — they do not
bound the fix turn's filesystem write reach.

**What 1.68.0 (#607) did about it, and what it deliberately did not.** Two
halves shipped. `fixPrompt` now tells that turn it has exactly ONE write
target, `segments/<seg>.draft.json`, and that a finding whose remedy would
require editing any other file is refused on that ground alone — a gate
script edited so that it accepts a draft is never a fix. And after every
dispatched fix call, before its reply is even inspected, W5 runs
`fix_scope_audit.py` from `{{PLUGIN_ROOT}}`: a **copy-fidelity check** that
every file Step 0a copied into this durable root (`scripts/`, `schemas/`,
`languages/`, the three workflow templates) still equals the plugin bytes it
came from, plus the two `runs/` bundle markers, whose expected values are
derivable from the same plugin tree. That closes the specific sentence two
paragraphs above — `plugin_bundle_hash` never re-hashed the copies; this
does.

It is a copy-fidelity check and NOT a write audit, and the difference is the
whole residual:

- Files with **no plugin twin** are not covered — `canon.json`,
  `canon_senses.json`, `manifest.json`, the segpacks, the ledger, and the
  one-time template seeds an operator then hand-edits (`style_bible.md`,
  `extract.py`, the `*_TASK.md` files). Most of those are translation
  CONTENT. One that is not is `runs/<id>/.resume_gate_ack`, a per-run
  authorization with no derivable authority: it is gate state, it is
  uncovered, and it is named here rather than left implicit.
- It is a **point-in-time** comparison. A write that lands after the audit
  call returns — a detached child the fix turn spawned — is not in it, and is
  reproducible. The next audit on that root still finds the divergence, since
  the check has no baseline and re-reads the plugin bytes every time; the
  uncovered case is the LAST fix round of a segment that then converges, where
  no next audit runs.
- It cannot **confine** the turn. That turn is a plain Claude agent holding
  the operator's own permissions, and the Workflow `agent()` API offers no
  filesystem confinement. A party able to alter the PLUGIN tree — including
  the auditor itself — can make the check report clean while durable
  divergence remains; a later plugin refresh repairs the auditor but does not
  retroactively reveal that divergence. What the check changes is that a
  tamper must now cover a second tree.
- A **driver-mediated** fix turn is not audited at all.
  `segment_dispatch_driver.py` returns the rendered fix prompt as `needs_fix`
  for an external Claude turn and truncates this template before every
  top-level preflight, so no audit call site fires on that route. Bracketing
  it needs a digest handed out at `needs_fix` and required back on the next
  invocation; that is tracked separately and is not in 1.68.0.

**Halt contract.** A mismatch ends the segment with reason
`fix-scope-violation`; two consecutive failures of the audit relay end it as
`fix-scope-unverified`. Both attempt a terminal `blocked` fragment and both
classify `human_escalation`. Neither is recoverable on its own,
deliberately: leaving the segment `in_progress` would let the next batch run
over exactly the state the gate could not verify.

That durable fragment, though, is written by `ledger_update.py` FROM the tree
the audit has just reported as diverging, so the write can fail for the very
reason the halt fired — and the `in_progress` fragment already on disk
classifies `recoverable`. So the halt is ALSO recorded where the audited tree
cannot reach it: a `FIX-SCOPE HALT` log line, and a batch result carrying
`batchComplete: false` and one `fixScopeHalts` entry per halted segment, each
with a `ledgerRecorded` flag. The `fixScopeHalts` array is the invariant, not
the `reason` string: `reason` reads `"fix-scope-halt"` on the ordinary path,
but a batch whose FINAL ledger merge also failed returns
`"ledger-merge-failed"` — still `batchComplete: false`, still carrying the
halts. **Read `fixScopeHalts` before dispatching another batch** — that
array, not the ledger and not the reason, is what survives a failed write. It does not make the durable record bulletproof; it
makes a batch that halted unable to end looking clean.

**A clean verdict is honoured only if it counted something.** The script
reports both `n_checked` and the `n_expected` it derives from the same walk;
W5 honours `ok: true` only when they are equal and non-zero, and otherwise
takes the `fix-scope-unverified` path. `ok` alone was a false GREEN — a walk
that runs zero times prints exactly like one that covered everything.

**What the counts prove is self-consistency, not coverage**, and the check
does not pretend otherwise: both sides come from the same walk over the
PLUGIN tree, so a plugin tree that has lost members shrinks them together and
they still agree. Two verdicts read the population from the DURABLE root
instead, which the plugin tree cannot shrink — `orphaned`, a
`${durable_root}/schemas/*.json` with no plugin twin (that directory has no
sanctioned addition, and a file dropped there is loaded into
`canon_validate.py`'s registry and hashed into the run identity), and
`degenerate`, a plugin-side class that yields nothing while the durable root
still holds files of it.

**`languages/` is the one class where that leaves a hole, and it is a real
one.** An orphan sweep cannot run there — the documented `fr.local.json`
override would read as an orphan and cost a segment a re-translation — so
`languages/` is covered by `degenerate` alone, which fires only on WHOLESALE
loss. If a plugin tree loses ONE preset (`assets/languages/fr.json`) while
the others remain, the durable `fr.json` drops out of `compared_pairs()`,
both counts shrink together, `degenerate` skips the class because plugin
languages still exist, and a widened edit to that durable file audits CLEAN.
That is not the disclosed "someone rewrote the auditor" case; an incomplete
refresh reaches it. The durable language file carries the pair's particle
configuration, so the consequence is real. It is left open rather than
patched: every closure needs either a stored baseline (the design this
release rejected three times) or a naming rule for legitimate overrides that
SKILL.md does not fix. `tests/fix_scope_audit.test.py` pins the clean result
so the limitation cannot be lost.

The relay residual is stated rather than closed: the audit reaches W5 through
a model relay, and a relay that fabricates its reply can fabricate BOTH
numbers. Nothing here prevents that.

Clearing a halt costs that segment a re-translation — name it under
`--only-segs`, and for a previously converged segment add
`--allow-retranslate-converged`. And a mismatch is **not by itself proof of
tampering**: a plugin upgraded mid-project gives the identical signal, and
the one remedy serves both readings — re-run Step 0a's copy pass, then re-run
the segment.

**Optional dispatch path — `segment_dispatch_driver.py` (#409).** Everything
above (`mass-translate-wf.template.js` instantiation, `pipeline()`, the
DISPATCH/WAIT/CONSUME chunking apparatus) remains W5's DEFAULT dispatch
mechanism. `segment_dispatch_driver.py` — copied into
`${durable_root}/scripts/` at Step 0a like every other bundle member — is an
ALTERNATIVE, not a replacement: it runs the identical per-segment
translate/review loop as a detached local process instead of inside the
Workflow tool, eliminating the WAIT-polling chunking apparatus entirely.
Unlike the `pipeline()` path above, where the orchestrating session invokes
`resume_setup.py` itself as an explicit preflight step before instantiating
the template, the driver resolves the resume-integrity `RUN_ID` on its own,
via `resume_setup.py`, every time it runs — there is no separate preflight
call for a session driving this path to make. Switching W5 over to it by
default is deferred to a later step (the fix step
below still needs a Claude turn today, and nothing currently automates the
hand-off — see below); until then, use it only if you deliberately choose
to, and never against the same `durable_root` as a concurrent `pipeline()`
run — nothing in either path guards against that (the driver's own
project-wide lock, `runs/.driver.lock`, only serializes two driver launches
against each other, never a driver against a Workflow-driven run).

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
unpinned, the only change is that a fresh mint now prints one stderr line
naming the new RUN_ID and how many eligible candidates were offered — in
the driver's own log file for the documented detached launch above, at the
terminal for a foreground run), `--max-concurrent-codex-jobs N` (default
40), `--node BIN`. Exit 0 means the per-segment loop ran to completion —
NOT that every segment converged; read the printed JSON's
`summary.failed`/`summary.needs_fix`. Exit 1 means a gate refused before any
dispatch (lock contention, the Step 1 re-translate gate, the volume cap, a
`--resume-from-run-id` refusal). Exit 2 is a usage/environment error.

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
add `--run-token` to them. Either expected-segment flag turns on the
missing-fragment completeness check, which REFUSES outright for a manifest id
that has no fragment yet — the normal state of a book mid-way through; adding
`--run-token` further arms the batch-final re-verification that
`mass-translate-wf.template.js`'s `batchComplete` step exists for. Both
refusals raise BEFORE `ledger.json` is written at all, so what you get is
exactly the staleness you were trying to clear. Second axis, and this one
survives the merge: a fragment records the LAST CONVERGENCE, not the current
draft, so a converged segment hand-edited afterwards goes on reading
`converged`. Neither artifact answers "is the draft on disk the one the
reviewer saw" — that is `reviewed_draft_sha1` against the draft's current
content sha1, which `final_audit.py` and `assemble.py` each recompute for
themselves before anything ships.

**The driver cannot perform the fix step, and nothing today automates the
hand-off.** When a segment's review comes back not-clean, the driver stops
at that segment and returns `outcome: "needs_fix"` — the round label, the
findings, and the exact rendered fix prompt — then moves on/exits without
fixing it (applying findings to a draft is a real LLM content-editing turn a
plain Python process cannot perform). Someone — a human, or an orchestrating
session — must notice this in the driver's own JSON output or its redirected
log (`runs/driver.<SESSION_ID>.log`, per the launch command above), perform
ONE Claude turn using that exact fix prompt to rewrite the draft, and
re-invoke the driver to resume. **That JSON arrives only at exit** — stdout
carries exactly ONE line, printed on the driver's terminal path, so the
redirected log shows no per-segment progress at all while the run is in
flight; only the driver's stderr warnings land there live. The channel that
IS live is its own journal: one entry per event, each flushed and fsynced as
it is written, opening with a `driver_started` entry carrying the pid —
which is also how you tell a driver still working from one that died. No
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

**When the finding is WRONG (#461) — rejecting a verdict instead of
applying it.** Since #532 the fix turn refuses a finding it cannot substantiate
and leaves the text alone — but a refusal is a REPORT, not a record: it changes
no file, and this script remains the only way to say durably that a verdict does
not bind. A
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
  admission is disclosed on `select_segments.py`'s **stderr** — so you see
  it on the hand-run recipe above, but **not** through
  `segment_dispatch_driver.py`, which captures the selector's stderr and
  discards it on success (the same fate as the D9 lost-token disclosure);
  the fact is not written into the claim record either. An *unreadable*
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
- **`--from-stalled SEG1[,SEG2,...]`** — for a segment stalled with
  genuinely incomplete bookkeeping: previously converged, then left
  `in_progress` with no `reviewed_draft_sha1` and a review that no longer
  describes the current draft. Full condition list, what the profile
  proves versus what it asks the operator to assert, and the hand-driven
  fallback for a unit that fails it: see **P3**, below.

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
path it was found at (its own `seg` and `run_id`), carry the
`from-converged` profile, and carry every field `build_claim_record()`
writes — a partial object is what a forgery or a half-finished write looks
like, and it is refused exactly like an absent record. On the lost-token
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
in_progress`, a `.ever_converged.<seg>` sentinel PRESENT, no
`reviewed_draft_sha1`, a draft on disk, and a stored review that is stale
against that draft — rather than cleanly converged-and-edited
(`--from-converged`) or capped-and-edited (`--from-cap`). Neither of those
two profiles reaches it: `--from-cap` refuses because the materialized
status is `in_progress`, not `non_converged`/`reason: "cap"` (since 1.27.0
it is the STATUS that refuses here, never the sentinel — a present sentinel
is admissible under `--from-cap`, see #537); `--from-converged` refuses
because there is no `reviewed_draft_sha1`, the drift baseline that profile
requires.

Requires, beyond the shared safety gates above: materialized status
`in_progress`; the `.ever_converged.<seg>` sentinel present; no
`reviewed_draft_sha1`; a review artifact on disk; that review stale against
the CURRENT draft, checked **only on entry** (below); no competing driver
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
`clean` is **not** constrained — a stalled
unit's stale review may be `clean: true` or `clean: false` and both are
admitted; unlike `--from-cap`'s `clean: false`-with-findings requirement,
the field describes a verdict over a draft that no longer exists, so it
says nothing about the CURRENT draft in either direction. A unit whose
review is current and clean but never converged is deliberately excluded —
its remedy is a convergence write, not a re-review.

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
to `in_progress` + sentinel + no `reviewed_draft_sha1` with a now-current
review — and a standing staleness gate would wrongly refuse re-entry into
the loop this profile just opened. Continuation is authenticated the same
way `--from-converged`'s dirty-review continuation is, above: against a
COMPLETE claim record held by the draft's current owner, or, on the
lost-token path, this run — never merely because the review happens to be
current.

**When a unit fails one of the conditions above, the hand-driven procedure
below is the FALLBACK — no longer the only route.** The currently known
instances (`seg21`, `FRONTBACK:errata_02`) are both admissible under
`--from-stalled` on their real on-disk state as of this writing; drive them
through it rather than by hand. For a unit that genuinely fails the
profile, the same procedure that used to be the only option remains
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

Reviewer concentration in one segment is evidence of reading order **or** of a
real local defect, and nothing tells them apart except measuring the class.
Report what you find rather than fixing it reflexively: a converged unit goes
stale the moment you touch it, and that is the operator's call to make
deliberately.

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
- **Five WARN-only, advisory, whole-book checks** — four generalized from the
  real reference's A1/A3/A4/A5 (the real `main()` only ever gates on coverage),
  plus (5) whose content the project itself supplies:
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
  unenforced rule can never read as a clean run. Prints every WARN as free text for human eyeballing — never
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

**#412 — the ENTRY POINT stays durable; only the sibling moves.** The
command above deliberately runs `${durable_root}/scripts/final_audit.py`,
not the plugin copy: `--plugin-root` moves only the CHECKER a script shells
out to, and W5's own "#582 — why the ENTRY POINT stays
`${durable_root}/scripts/`" paragraph records why relocating entry points
was evaluated and not adopted. What the flag buys here is the
whole-project completeness gate's sibling — that gate shells out to
`select_segments.py`, and left to self-anchor that sibling comes out of the
same writable `${durable_root}/scripts/` the audit is auditing.
`final_audit.py` forwards the value verbatim, alongside a synthesized
`--durable-root`, since the relocated `select_segments.py` no longer sits
under the root it must classify. Unlike `canon_validate.py`, whose stamping
modes REFUSE to run without an answer, `final_audit.py`'s `--plugin-root`
stays OPTIONAL — and that asymmetry is a decision, not an oversight: it had
no shipped call site at all before this command, so its caller set is
closed by construction the moment this one exists, and a refusal would only
break hand-run audits without closing anything a spelled-out call site
leaves open.

- **Frontback coverage report** (advisory, informational, never
  exit-code-gating on its own): reads `manifest.json`'s `frontback[]`
  inventory directly, emits one line per entry — `translate`-decision
  elements report their own convergence status (cross-reference to
  `segments[]`, not new logic); `regenerate`/`omit`-decision elements
  reported by decision alone. This frontback-through-segment-loop treatment
  is new plugin hardening, generalizing an intent the real historiettes-t3
  project's own PLAN document stated but never actually implemented — do
  not claim this mechanism is "proven" when building or extending it; it is
  carefully-designed but genuinely untested-at-scale.
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
  - **New in 1.12.0 — a within-cohort output-coverage ratio-outlier
    surfacer, `Refs #202` (this does NOT close #202 — see the limitation
    below).** OPT-IN: config `validation.conservation_ratio_band`
    (`min_source_words_band`/`min_cohort`/`k`/`abs_guard`); absent or
    `null` means this lane does not run at all and `output-coverage`
    behaves exactly as it did in 1.11.0. Per cohort
    (blocks sharing a manifest `type`), it flags `low_coverage_outlier`
    when a block's output/source word ratio falls below a robust
    median-and-MAD fence computed from its OWN cohort, AND is well below
    that cohort's own typical ratio (a second, independent `abs_guard`
    condition that defends against a degenerate near-zero-MAD cohort).
    `zero_output_block` and `insufficient_sample` (naming a `reason`)
    cover the edge cases; a `coverage_distribution` entry
    (`median_ratio`/`mad`/`fence_ratio` per cohort, `null` when nothing
    was eligible to compute them from) rides alongside the warnings on the
    same stdout JSON line.
  - **Stated limitation — this lane structurally cannot close #202.** It
    is a within-cohort comparison, never an absolute truthfulness check:
    if every block in a cohort is truncated by roughly the same
    proportion, that cohort's own median absorbs the truncation and
    nothing reads as an outlier — detecting uniform collapse would need a
    reference outside the audited population, and none exists here. It is
    also NOT language-pair-agnostic: `normalize_words()` is NFC +
    whitespace splitting only, no morphological/markup/sentinel
    normalization, so agglutinative/compounding target languages and
    markup-heavy blocks produce ratios that are not linguistically
    comparable across language pairs. What it DOES catch: a few collapsed
    blocks amid an otherwise healthy cohort — proportional truncation
    across a range of block sizes, which the absolute floor above cannot
    see at any single fixed `(min_source_words, max_output_words)` pair.
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
runs as a plain DETERMINISTIC script step (`assemble.py` then
`diff_rendered_output.py`), never an agent workflow: it has no
agent-workflow template of its own, and none is planned. Assembly has no
review/fix loop and no ledger prompts to schema-validate, so it does not
mirror `mass-translate-wf.template.js`'s agent machinery. Gated on W7's
`final-audit-summary.project_complete: true` — the whole-project
completeness gate, not merely "this batch converged" — assembling a book
from a project that is not yet fully converged is refused, never silently
attempted over a partial set. Because that ONE verdict gates the whole step,
both gates must agree about every unit: `assemble.py` re-derives the same two
carve-outs (the #491 machinery-only one and, when
`validation.admit_contract_only_stale` is declared, #533's contract-only one)
from the same merged ledger rather than trusting the summary. When either
admits a unit, both name it — `stale_contract_admitted` in W7's summary,
`contract_stale_admitted` in `assemble.py`'s, and a stderr block in each.
One asymmetry since #492, and it is deliberate: W9 ALSO admits a contract-only
drift it detects itself, by comparing the live inputs against each shipped
record (same declaration, same sentinel condition). Such a unit is still
`converged` in the merged ledger, so W7's summary — which reads that snapshot —
has nothing to name yet. W9's list is therefore the authority on what THIS run
shipped unjudged against the current contract, and may legitimately exceed
W7's; re-running W7 after the merge brings the two back into step. The same
holds for `validate_assembled.py` and `validate_conservation.py`, which derive
their own lists from that snapshot too.
Keep the declaration stable across the whole W7→W9 chain; toggling it between
steps is the only way a normal run can make the two gates disagree about the
same book. (They read the moved-field list from different authorities -- W7
from `select_segments.py`'s recomputation against the CURRENT cache key, W9
from the materialized `stale_mismatched_fields` -- but `ledger_merge.py:648`
drops any inherited value and re-derives that list from the same diff, so the
two agree by construction on anything a run produces. Hand-editing the
materialized `ledger.json` between the two steps can split them; so can
editing one segment's `status` to `converged`, which skips both carve-outs
entirely.)

Run `scripts/assemble.py`, which reconstructs the whole-book reading order
from `manifest.json` + every converged segment's draft + `ledger.json`'s
convergence gate into the shared NodeStream artifact, then invokes the
Step-0d-resolved output-target adapter (`render_obsidian` in this
increment) to render the book under `${durable_root}/out/` (see
`references/assembly-and-output.md` for the reconstruction algorithm and
the NodeStream/anchor-map artifacts).

Then run `scripts/validate_assembled.py` — AFTER `assemble.py` writes
`out/.assembled/nodestream.json`, BEFORE `scripts/diff_rendered_output.py` —
the same #202 structural-completeness gate, this time checking that every
declared heading source marker surfaced as a non-empty `kind:"heading"` node
in the assembled NodeStream. Exit `1` HARD on a dropped/misclassified
heading; exit `0` with non-gating WARN entries otherwise.

Then run `scripts/validate_conservation.py output-coverage` — the same
WARN-only #202 floor + within-cohort ratio-outlier lane as W7 (see above),
this time reading `out/.assembled/nodestream.json`
(`output.v1_scope: assembled_book`) instead of converged drafts. Never
gates; exit `0` always barring an env/usage precondition.

Then run `scripts/diff_rendered_output.py` as the acceptance gate: it
reduces the ALREADY-rendered output (it renders nothing itself) and diffs it
against the last accepted baseline — exit `0` on an exact match, `1`
on a mismatch or guard refusal, `2` when no baseline exists yet
(`--accept-baseline` freezes the current render as the new baseline). For
rendered-content equality, the render+diff comparison IS the acceptance
gate — there is no separate item-count check alongside it (structural
completeness is `validate_assembled.py`'s distinct concern above, checked
before this step ever runs). To compare two ALREADY-rendered trees instead —
the check a project that post-processes the vault needs, and the only one the
frozen baseline cannot express — pass `--baseline-dir A --candidate-dir B`:
read-only, no baseline involved, `"mode": "two_tree"` on the verdict.

Then — for `output.target: obsidian`, ON BY DEFAULT unless explicitly
disabled (`output.adapter_config.obsidian.mentions_section.enabled: false`) —
run `scripts/validate_backlinks.py` as an **advisory** appendix-integrity gate,
AFTER `diff_rendered_output.py`. It re-derives the source-anchored occurrence
universe and checks that every index-eligible entity's `## Mentions` section
covers its occurrences (metric 1, the sole warning source), plus a
native-inline-backlink diagnostic and collision/unresolved-homonym reports
(metric 2, exit-neutral). Unlike the hard gates above, its **exit `1` is
ADVISORY — log the warnings and CONTINUE W9** (it never blocks assembly);
only exit `2` (unreadable/malformed input, e.g. a missing
`out/.assembled/nodestream.json`) halts. When the target is not obsidian, or
the flag is explicitly disabled, it short-circuits to
`mentions_coverage.status: disabled`, exit `0`. Against a vault whose entity
notes a post-processing layer renamed or merged, pass `--entity-note-map FILE`
(a JSON `{source_form: vault-relative *.md path}`) — without it the gate
re-derives every note path from the renderer's own rule and reports the whole
vault missing. The `## Mentions` section is
a source-anchored occurrence index (mirroring the SSK `build_index.py`
model) that supersedes the older "native backlinks are the occurrence index"
stance for `output.target: obsidian` projects; see
`references/output-target-adapters/obsidian.md`.

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
already produced into a person-keyed registry: one record per human being,
every source form and alias, the target renderings **as actually printed** with
counts taken from the assembled text, typed kinship each carrying the sentence
it was derived from, places and dates where stated, mention locations, and an
identity-contested flag kept separate from the mention count. It writes NEW
artifacts under `${durable_root}/registry/` only — it reads `canon.json` and
never writes it, it is in **none** of the three bundle tuples, and it changes
no cache key. **There is deliberately no `profile.yml` knob**: a project that
wants a registry runs this step, one that does not never invokes it, and a
profile key would have moved `${durable_root}/schemas/`'s hash — hence
`input_digest` — for every project on earth to gate a step nothing auto-runs.

Run it **immediately after the W9 chain above, in the same session** — its
`--prep` gate detects a partial assembly, a scope change and a post-assembly
draft edit, but cannot detect a segment re-converged after W9 ran, which the
emitted artifact states as `assembly_currency: "not_bound"`.

The chain is bound to both inputs it reads: `--prep` hashes `manifest.json`
and the assembled NodeStream into its own body, and `--claims`/`--build`
refuse (`manifest_changed` / `nodestream_changed`) when either moved
underneath. The remedy is always to re-run `--prep`, Pass A, `--claims` and
Pass B against the current text — never to re-run only the step that failed,
whose inputs are the stale ones.

Copy `assets/templates/registry_TASK.template.md` → `${durable_root}/
registry_TASK.md` (a W9r-time copy, NOT a Step-0a one) and fill its bracketed
placeholders. Then three script calls with two model calls between them:

```
LT=<the literary-translator skill directory>   # holds assets/schemas/registry/
python3 scripts/person_registry.py --prep   --plugin-root "$LT"
#             ->  registry/registry_input.json
#   Pass A -- ONE call over the whole cast, per registry_TASK.md's Pass A
#             section  ->  registry/registry_verdicts.json
python3 scripts/person_registry.py --claims --plugin-root "$LT"
#             ->  registry/registry_claims.json
#   Pass B -- a FRESH dispatch whose only semantic inputs are registry_TASK.md's
#             Pass B section and registry_claims.json; it must NOT inherit Pass
#             A's conversation and must not read registry_verdicts.json
#             ->  registry/registry_adjudications.json
python3 scripts/person_registry.py --build  --plugin-root "$LT"
#             ->  registry/person_registry.json + registry/PEOPLE.md
```

`--plugin-root` is not optional from `${durable_root}/scripts/`: the three
registry schemas ship under `assets/schemas/registry/` and are deliberately
never copied into a durable root (Step 0a's copy glob and `resume_setup.py`'s
schema hash are both non-recursive, which is exactly why they live there), so
`--claims` and `--build` exit `2` with `schema_not_found` without it. Exit `0`
/ `1` (a rejected verdict) / `2` (a usage or precondition failure), one JSON
line on stdout.

**Why two model calls and not one.** Deciding that two name forms denote the
same person is interpretation, so it is a model's judgement — but a
verbatim-quote check proves only that a sentence EXISTS, never that it says
what the claim says. A model can cite a real sentence and attach an unrelated
kinship claim to it, and every structural gate passes. Pass B is the only thing
in the design that can catch that, and a merge that is plausible and wrong; its
independence is load-bearing, which is why it is a fresh dispatch. An
unaffirmed person claim REFUSES (every unit to `refusals[]`, no record emitted)
rather than splitting into single-unit survivors nobody adjudicated. Both
passes read the DELIVERED text, not only the source: every context pairs its
source occurrence with what the book prints in that same container, and a
printed-surface claim carries the passages where that exact string
occurs in the assembled corpus, with the true total and a truncation flag. Full contract, the prep universe's three populations, the
gate table and the stated non-goals: `references/person-registry.md`.

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
- `references/person-registry.md` — W9r, the opt-in person registry
- `references/gotchas.md` — known pitfalls
