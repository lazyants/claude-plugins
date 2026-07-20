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
   **only** applies fixes, orchestrates, and verifies — it never originates a
   translation or grades its own output. **codex-translate → deterministic
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

Throughout this skill, `{{PLUGIN_ROOT}}` denotes the plugin's install
directory — under Claude Code, the `${CLAUDE_PLUGIN_ROOT}` environment
variable.

Implemented by `scripts/profile_validate.py`, invoked as:

```
python3 {{PLUGIN_ROOT}}/assets/scripts/profile_validate.py --profile .claude/literary-translator/profile.yml
```

Run by the **orchestrating session directly**, always from the plugin's own
install path, never a durable-root copy — it runs before Step 0a exists to
create one (same exception as Step 0c reading
`references/source-format-adapters/*.md` directly from the plugin). It is one
of four plugin-path scripts never copied to `durable_root`: `validate_extraction.py`
(the W2 post-extraction gate) and `glossary_preflight.py` (the W3 glossary
staleness gate, 1.4.0) are kept plugin-only for tamper-proofing and
freshness-on-resume rather than because either predates the durable root, and
`resolve_codex_companion.py` (the W5 codex-companion path resolver, 1.4.7) is
never copied because it must glob the plugin's own install locations to find
the newest installed `codex-companion.mjs` — a durable-root copy could not.

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
`resolve_codex_companion.py` — all four run only from the plugin path and are
never copied; a copied `glossary_preflight.py` would resolve its own
`__file__`-relative schema lookup against the *durable* schemas and compare
durable-vs-durable, a vacuous pass that could never detect staleness, and a
copied `resolve_codex_companion.py` could not glob the plugin's own install
locations to find the newest installed `codex-companion.mjs`; and, separately,
`scaffold_setup.py` — Step 0a's own bundle-hash marker writer (#194), which
likewise runs only from the plugin path: it is invoked below as Step 0a's final
action and imports the plugin's own `cache_key.py` helpers, and is deliberately
NOT a bundle member, so it must never land under `scripts/`), every shipped
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
concatenated bytes of the 13 `PLUGIN_BUNDLE_MEMBERS` under `scripts/` — read by
`cache_key.py` rather than re-hashing the bundle per segment) and
`${durable_root}/runs/.orchestration_bundle_hash` (sha1 over the four
orchestration-only scripts — non-gating for convergence, never part of the
composite cache key, but gating for resume: folded into the resume-integrity
digest, and also surfaced in W8's reporting). Both are written atomically
(sibling temp file + `os.replace`) with a trailing newline; both readers
`.strip()`.

Last action: the deferred `particle_config` existence check — resolve
`source.language.particle_config` as `${durable_root}/languages/<value>`
(bare filename, exactly one `languages/` segment) and halt (field-named) if
it still doesn't resolve to a real file.

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

## Hard rules R1–R7

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
  suspected-wrong canon entry is reopened via the glossary/adjudication route,
  never flagged in the per-segment review loop (`references/canon-and-glossary.md`,
  `references/orchestration-and-batching.md`'s reviewer carve-out).
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
    the verdict off disk. No `agent()` schema param is involved on this path;
    the deterministic validators are the check.
  - **Glossary/canon-pass batches (unchanged, §6):** each batch is still a
    **schema-less, fire-and-forget DISPATCH** — `agentType:'codex:codex-rescue'`,
    no `schema` param — that writes its verdict to a `{{RUN_ID}}`-scoped disk
    artifact; a bounded Claude WAIT poll, then a schema-validated Claude
    CONSUME/disk-verify call (`canon_validate.py --merge-batches` +
    `CANON_VERIFY_SCHEMA`) reads that artifact back and forces a real
    structured object out of it — never the codex call itself.
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
already in canon — SKIP `resume_setup.py` and the glossary Workflow entirely
this run, nothing to research. Otherwise run the codex-glossary-pass,
instantiating `glossary-pass-wf.template.js` fresh from the plugin's current
copy every time — batched over `${durable_root}/glossary_TASK.md`, feeding the
planner's `args` into the Workflow tool and its `batches` into
`resume_setup.py`'s payload. **1.4.0:** on that same non-empty-candidates
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
`PROMPT_CONTRACT_VERSION` marker. Without this gate, a resumed project whose
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
`agent(batchDispatchPrompt(batch), {agentType:'codex:codex-rescue',
effort:'high'})` (schema-less, writes the run-scoped fragment
`glossary/runs/{{RUN_ID}}/out_{index}.json`, self-checks against its own
manifest) → `batchWaitPrompt` (bounded poll) → once every fragment is
`READY`, one serialized `canon_validate.py --merge-batches` call plus one
disk-verify call (`schema: CANON_VERIFY_SCHEMA`) close the pass and freeze
`canon.json` — see `references/canon-and-glossary.md` for the full
mechanics and `references/workflow-schema-validation.md` for why the
pre-1.2.0 single schema-validated `agent()` call per batch was replaced
(`#87`/`#88`/`#90`/`#97`). Write `style_contract` sections A–F by hand/
interview with the user; leave section G (glossary) to the glossary-pass
output. The shipped template already wraps sections A–F in
`STYLE_CONTRACT_BEGIN`/`STYLE_CONTRACT_END` markers -- keep them (do not
delete, duplicate, or reorder them): they define the `style_contract_hash`
byte-scope, and `scaffold_validate.py` now enforces exactly one of each, in
order.

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
exists.

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
this module's own verifier is to add a table entry, and there is no longer
a code shape in `skeptic_ready.py` that reaches a frozen input's bytes any
other route. `manifest.json`'s snapshot is captured through that same gate
but, having no downstream parser in this module the way canon/senses do, is
discarded once its own tamper comparison is done.

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
that is its whole, and only, guarantee. It does NOT bind the earlier
`read_frozen_input_snapshot()` capture in `skeptic_setup.py`, and its
SIGNATURE does not bind `compute_producer_input_digest()`/
`compute_skeptic_input_digest()` either — both still take a fixed
positional/keyword canon+manifest+senses signature, unrelated to this
tuple; a fourth frozen input still needs its own hand-added parameter (and
a matching update at every call site) before either digest can hash it at
all. Round 9 shipped with that gap silent: a fourth frozen input added
ONLY to `FROZEN_INPUT_SPECS` (plus the schema, plus a matching signature
parameter with no corresponding hand-added tuple-key entry, or vice versa)
would be captured, stamped, and H1-tamper-checked correctly, yet invisible
to both freshness digests — a mutation to it BEFORE `skeptic_setup.py` ran
would leave a stale worklist's `producer_input_digest` unchanged, so the
stale worklist would still read as fresh and get (re)certified against the
new state, the same stale-certified-as-fresh failure mode this release
closes for `canon_senses.json`, just re-opened at a boundary this tuple
didn't reach.

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
     (see `blocked_needs_regeneration` below). Records which trigger fired
     as a `stale_reason` sub-field: `cache_key_mismatch` and/or
     `draft_sha1_mismatch`. A `draft_sha1_mismatch`-triggered stale is never
     reclassified as `blocked_needs_regeneration` — the two gates are
     independent.
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

**1.2.0: the deterministic pre-workflow step, after `SEGS` and before
`pipeline()`.** With `SEGS` finalized, invoke `resume_setup.py` (kind
`mass`) before the Workflow tool ever launches: it computes each segment in
`SEGS`'s current `cache_key.py` composite key, resolves `effectiveRunId` via
the resume-integrity digest gate (`input_digest` MATCH against a prior
`runs/<RUN_ID>/input.digest` → resume with `resumeFromRunId`; MISMATCH or
absent → fresh `RUN_ID`, no `resumeFromRunId`), and creates
`runs/workflows/<RUN_ID>/` — aborting before any dispatch on failure. Only
then is `mass-translate-wf.template.js` instantiated (fresh from the
plugin's current copy every run — never reuse a stale generated copy),
substituting the resolved `{{RUN_ID}}` alongside every other token, and
`pipeline()` launched. **1.4.7:** as part of that same instantiation the
orchestrator first runs `resolve_codex_companion.py --durable-root
${durable_root}` from the plugin's own install path (never a durable-root
copy — it must glob the plugin's install locations to find the newest
installed `codex-companion.mjs`), ABORTS W5 on any non-zero exit (codex is the
required engine per R1 — fail-fast, not today's silent no-draft hang), reads
the raw `companion_path` it prints, `json.dumps`-encodes that string ONCE, and
substitutes it as the `{{CODEX_COMPANION_PATH_JSON}}` token alongside every
other. Each per-segment translate/review dispatch then launches codex through
the detached `codex_job.py` driver (R1/R7), not a `codex:codex-rescue`
`agent()` call. See `references/orchestration-and-batching.md` for
the full `{{RUN_ID}}` derivation contract and digest definition, and
`references/ledger-and-resumability.md` for the `dispatch_token`
commit-gate chain this sets up for translate/review to enforce per segment.

**W6 Consistency pass** — cross-segment sweep using `consistency_issues.md`
as a lightweight, hand-maintained tracker after every batch, before the next
starts. Never the output of an automated script, never read back in or
acted on programmatically.

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
- **Four WARN-only, advisory, whole-book checks** (generalized from the real
  reference's A1/A3/A4/A5 — the real `main()` only ever gates on coverage):
  (1) glossary-diff — cross-segment name-form drift + `canon.json`
  self-consistency using each draft's `names[]`; (2) link-graph —
  `⟦FNREF_N⟧`/`⟦VERSE_...⟧` sentinel bijection on the translated draft,
  cross-checked against the segpack's vid map; (3) foreign-remainder scan —
  source-language stopword-density + longest-source-alphabet-token-run
  heuristic using the resolved language preset's own `STOPWORDS`
  (generalized from the real reference's hardcoded French list); (4)
  verse-structure — per `verse_policy.mode`'s own required-field table,
  generalized from the real reference's hardcoded `ru_rhymed`/`podstrochnik`
  field names. Prints every WARN as free text for human eyeballing — never
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
  on disk) nor of `orchestration_bundle_hash` (whose four members are
  `draft_ready.py`, `ledger_merge.py`, `language_smoke_report.py`, and
  `select_segments.py` — see `references/ledger-and-resumability.md`;
  `final_audit.py` is not one of them). Editing `final_audit.py` on its own
  never flips a cache key or the resume-integrity digest via either bundle.
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
- **Output-coverage v1 floor (`scripts/validate_conservation.py
  output-coverage`, the #202 half `validate_assembled.py` declines):** runs
  immediately after the structural-completeness gate above, same scope. **WARN-only
  — never gates, exit `0` always** (barring an env/usage precondition, exit
  `2`): flags `hollowed_output_block` when a `segments[].block_ids[]`-cited
  block's source text is non-trivial but its current converged-draft text is
  empty/near-empty (an absolute word-count floor, not a length band — see
  that script's own module docstring for why a band is deliberately not
  built here). Read the WARN list; it is diagnostic input for W8's report,
  not a stop condition.

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
attempted over a partial set.

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

Then run `scripts/validate_conservation.py output-coverage` — same
WARN-only #202 floor as W7, this time reading `out/.assembled/
nodestream.json` (`output.v1_scope: assembled_book`) instead of converged
drafts. Never gates; exit `0` always barring an env/usage precondition.

Then run `scripts/diff_rendered_output.py` as the acceptance gate: it
re-renders and diffs against the last accepted baseline — exit `0` on an exact match, `1`
on a mismatch or guard refusal, `2` when no baseline exists yet
(`--accept-baseline` freezes the current render as the new baseline). For
rendered-content equality, the render+diff comparison IS the acceptance
gate — there is no separate item-count check alongside it (structural
completeness is `validate_assembled.py`'s distinct concern above, checked
before this step ever runs).

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
`mentions_coverage.status: disabled`, exit `0`. The `## Mentions` section is
a source-anchored occurrence index (mirroring the SSK `build_index.py`
model) that supersedes the older "native backlinks are the occurrence index"
stance for `output.target: obsidian` projects; see
`references/output-target-adapters/obsidian.md`.

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
- `references/gotchas.md` — known pitfalls
