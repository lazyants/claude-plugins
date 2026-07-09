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
   **hard-locked to `codex:codex-rescue`** (R1, `references/engine-loop.md`)
   — every shipped template enforces this and no profile knob swaps either
   role to a different engine. Claude (the orchestrating session) **only**
   applies fixes, orchestrates, and verifies — it never originates a
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

Run by the **orchestrating session directly**. This is the ONE script always
invoked from the plugin's own install path, never a durable-root copy — it
runs before Step 0a exists to create one (same exception as Step 0c reading
`references/source-format-adapters/*.md` directly from the plugin).

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
skeleton: `segments/`, `glossary/`, `verses/`, `runs/`, `runs/ledger.d/`,
`runs/workflows/`, `scripts/`, `languages/`, `schemas/`, `out/`. Also
explicitly creates the specific resolved parent of `output.destination`
(mkdir -p, idempotent) whenever it resolves inside `durable_root`, and the
same for `smoke_test.report_path` (skipped when null).

Copies (unconditional overwrite, safe since these files are never
hand-edited): every file in `assets/scripts/*.py` (except
`profile_validate.py`, never copied), every shipped file in `assets/languages/`
(`fr.json`, `de.json`, `es.json`, `it.json`, `README.md`), every file in
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
`glossary-pass-wf.template.js` get `scripts/`-style repeatable-overwrite
treatment (re-instantiated fresh at W5/glossary-pass time), never the
one-time-seed treatment the other templates get.

Computes/refreshes two marker files: `${durable_root}/runs/.plugin_bundle_hash`
(read by `cache_key.py` rather than re-hashing the bundle per segment) and
`${durable_root}/runs/.orchestration_bundle_hash` (sibling, non-gating,
provenance-only for W8 reporting).

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
  `gutenberg-epub.md`/`plain-text.md` as starting patterns), but its output
  contract is fixed — must produce a `manifest.json` matching the exact same
  shape every other adapter produces (block-ID types, `order_index`,
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
  dimension in `references/engine-loop.md`.
- **R7 — Workflow-script schema requirement**, two explicit categories:
  1. Codex structured-accuracy/canon calls (review + canon/glossary-pass
     batches) — MUST be `agentType:'codex:codex-rescue'` + a `schema` param.
     The translate call is the one deliberate exception even within this
     category: intentionally schema-less, gated by file output +
     `draft_ready.py`/`validate_draft.py` instead, matching the proven
     reference script exactly.
  2. Non-codex mechanical schema-confirmation calls — `recordLedgerPrompt` —
     use a `schema` param for a different reason (verifying a shell script's
     JSON stdout was well-formed, not forcing a codex verdict). Not
     `agentType:'codex:codex-rescue'`. `effort:'low'` since no judgment is
     involved.

  `references/workflow-schema-validation.md`

## Workflow W1–W9

**W1 Scaffold** — not a copy action itself (Step 0/0a already did all
copying). W1 is the human-facing label for "fill in every placeholder across
`profile.yml` and every other just-scaffolded file." Mechanically enforced,
not just prose: `style_bible.template.md`/`PLAN.template.md` wrap their
must-fill sections in `<!-- LT_REQUIRED_FILL_BEGIN: <id> -->`/
`<!-- LT_REQUIRED_FILL_END -->` marker pairs containing the fixed sentinel
`LT_PLACEHOLDER_UNFILLED`. `scripts/scaffold_validate.py` runs as a hard gate
before W2 begins: FATALLY halts (naming file + marker id) if
`LT_PLACEHOLDER_UNFILLED` survives inside any marker span across any
scaffolded file — text outside marker spans is never scanned. Plus a
separate, marker-free check: FATALLY rejects `translate_TASK.md`/
`review_TASK.md` if the literal string `guéridon=refrain-song` (the shipped
illustrative era/domain trap example) survives a copy-paste into a new
project — deliberately not marker-gated (traps are discovered during the
run, nothing to require at W1).

**W2 Extract** — adapt `extract.py.template` for the source (spine/footnote/
verse detection per the resolved source-format adapter); run it; its own
blocking self-checks (bijection, uniqueness, coverage-no-holes, spine-order,
segmentation-nonempty, sentinel-uniqueness, front-back inventory,
verse-structure, `no_segment_exceeds_max_words`) must be green before
anything downstream runs. Plus a `manifest.schema.json` validation pass
immediately after extraction using the real `jsonschema.Draft202012Validator`.

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

Then run `bootstrap_names.py` (configured from
`${durable_root}/languages/<particle_config's literal value>` — never
rebuilt from `source.language.code` alone) to get frequency-ranked name
candidates. Run the codex-glossary-pass as a schema-validated Workflow-level
`agent()` call — instantiate `glossary-pass-wf.template.js` fresh from the
plugin's current copy every time — batched over
`${durable_root}/glossary_TASK.md`, each batch call is
`agent(glossaryPrompt(batch), {agentType:'codex:codex-rescue', effort:'high', schema: CANON_BATCH_SCHEMA})`
— to freeze `canon.json`. Write `style_contract` sections A–F by hand/
interview with the user; leave section G (glossary) to the glossary-pass
output.

**Canon human-adjudication audit (opt-in rollout gate)** —
`scripts/canon_adjudication_audit.py` enumerates every canon
name-adjudication a human/codex must sign off (duplicate source forms,
existing merges, all candidate missed-merge pairs, and un-drained
`review_queue[]` items) and cross-checks them against
`canon_adjudications.json`. Run before Deliver (W7/W8):
`python3 ${durable_root}/scripts/canon_adjudication_audit.py --check` —
exit `0` = every required item has a matching `confirmed_ok` (or a valid
risk-acceptance / the queue is drained), `1` = blocking findings, `2` =
fatal. Add `--advisory` to report without blocking (preserves the plugin's
WARN-first name policy). **Status: NEW machinery, not pilot-proven** — it
is an OPT-IN gate a project enables, not yet wired as a mandatory W-step;
the script defaults to hard-blocking (exit 1) so a project that wires it in
gets the full gate. The accuracy calls it audits are authored by a human
reviewer or a schema-validated codex workflow — the script never decides
identity itself. Enable ONLY when a per-person index, per-person bios, or
enforced cross-document consistency is in scope; on a plain translate+gloss
job leave it off — the lightweight `review_queue` is the correct tool.

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
- Excluded from `plugin_bundle_hash` (runs strictly after every segment is
  already converged, over data already on disk) — covered by the separate,
  non-gating `orchestration_bundle_hash` instead.

**W8 Deliver** — report convergence stats, list any `blocked`/
`non_converged` segments explicitly. Also surface W7's whole-project
completeness gate's own per-category counts alongside `project_complete` —
"this batch: N converged, zero hard defects" and "whole project: M of TOTAL
still incomplete" are two different numbers, never conflated (a batch can
succeed while the project is still incomplete). Hand off the audit package:
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
the NodeStream/anchor-map artifacts). Then run
`scripts/diff_rendered_output.py` as the acceptance gate: it re-renders and
diffs against the last accepted baseline — exit `0` on an exact match, `1`
on a mismatch or guard refusal, `2` when no baseline exists yet
(`--accept-baseline` freezes the current render as the new baseline). The
render+diff comparison IS the acceptance gate — there is no separate
item-count check alongside it.

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
