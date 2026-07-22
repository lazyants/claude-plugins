<!--
  PLAN.template.md -- one-time-seed project-planning scaffold.

  Step 0a copies this file ONCE to `${durable_root}/PLAN.md` and never
  re-copies over it once it exists (see SKILL.md's Step 0a). Hand-adapt
  every `LT_REQUIRED_FILL_BEGIN`/`LT_REQUIRED_FILL_END` HTML-comment
  marker span below for THIS project before W2 (Extract) starts --
  `scripts/scaffold_validate.py` FATALLY rejects any span that still
  contains the literal sentinel `LT_PLACEHOLDER_UNFILLED`, naming the file
  and the specific marker id (see that script's own docstring). NOTE: this
  very sentence deliberately never writes the marker's own opening/closing
  HTML-comment delimiters back to back -- doing so here, inside this
  explanatory header, would itself parse as a real (accidental) marker
  span to that same regex-based scanner, which does not understand
  comment nesting.

  This file is prose for humans (and Claude, as curator), not
  machine-consumed config -- nothing in this codebase parses PLAN.md's
  content except that one marker-span sentinel scan. The actual enforced
  configuration for this project (source format, footnote/verse policy,
  research mode, engine effort, output scope, ...) lives in `profile.yml`;
  this file records the WHY behind those choices, THIS project's own
  source-specific notes, and a running execution log -- it never
  redefines the plugin's own mechanism (block-ID coverage, the
  translate -> review -> Claude-fix -> re-review convergence loop, the
  ledger, the canon/glossary bootstrap, W1-W8). That mechanism is
  documented once, in `SKILL.md` and `references/`; do not duplicate it
  here, follow the linked docs.
-->

# Project plan -- [PROJECT TITLE / AUTHOR / PERIOD -- fill in]

> A living project-planning document. Sections 0-4 below are filled in once, before W2 (Extract) starts;
> section 5 is an append-only running log kept up to date as the project actually proceeds through
> SKILL.md's W1-W8 workflow.

---

## 0. Goal

<!-- LT_REQUIRED_FILL_BEGIN: goal -->
LT_PLACEHOLDER_UNFILLED -- one paragraph: what "done" means for this project. Name the target-language
voice/quality bar this project is aiming for, and which of this book's own features are in play (verse,
footnote apparatus, front/back matter). State the deliverable's scope explicitly: this plugin's v1 output
scope is converged per-segment drafts plus a full audit trail (`references/assembly-and-output.md`) --
NOT a single assembled book file (EPUB, PDF, ...). If this project also wants a fully assembled book, name
that here as an explicit, separate, out-of-scope-for-v1 follow-on effort -- never assume it is included.
<!-- LT_REQUIRED_FILL_END -->

## 1. Source

<!-- LT_REQUIRED_FILL_BEGIN: source -->
LT_PLACEHOLDER_UNFILLED -- name the source: title, author, period/genre, edition/identifier (e.g. a
Gutenberg ebook ID), approximate word count (main text, and the footnote apparatus separately if this
source has one), and `source.format` (`gutenberg_epub` | `plain_text` | `custom` -- matches `profile.yml`).
Record what you already know about this source's own layout from having looked at the actual file: how
segments/chapters are marked, whether verse is present and how it's marked up, the footnote-anchor
convention, and any front/back matter (title page, table of contents, publisher boilerplate) this
extraction will need to classify as `translate` / `regenerate` / `omit`.
<!-- LT_REQUIRED_FILL_END -->

## 2. Fixed parameters and their rationale

<!-- LT_REQUIRED_FILL_BEGIN: fixed-parameters -->
LT_PLACEHOLDER_UNFILLED -- record the WHY behind this project's own `profile.yml` choices, wherever that
reasoning is not already obvious from the schema alone. Typical items: `footnotes.apparatus_policy` and
why; `verse_policy.mode` and why (see `references/verse-policy.md` for the six-value enum);
`glossary.research_mode` (`live` | `offline`) and why -- this is a statement about THIS run's actual
environment, not a preference; any project-specific reason to deviate from the chooseable `engine.effort`
(default `high`) / `max_fix_rounds` / `batch_agent_cap` defaults.
<!-- LT_REQUIRED_FILL_END -->

### Intake & proportionality agreement (SKILL.md's "Intake & proportionality" step)

<!-- LT_REQUIRED_FILL_BEGIN: intake-proportionality-agreement -->
LT_PLACEHOLDER_UNFILLED -- record the outcome of SKILL.md's "Intake & proportionality" step, agreed with
the user before scaffolding: this project's rough size (word count, segment/chapter count, verse/front-back
presence); which tier was chosen -- fast (lean defaults) or thorough (opt-in fuller apparatus) -- and
exactly which knobs express that choice (`glossary.research_mode`, `footnotes.apparatus_policy`,
`verse_policy.mode`, `engine.max_fix_rounds`, `output.target`, `output.index.enabled`); and the worth-it
rationale for that choice -- why the chosen tier's cost (or lack of it) is justified for this project's
actual goal, not assumed by default.
<!-- LT_REQUIRED_FILL_END -->

## 3. Extraction notes specific to this source

<!-- LT_REQUIRED_FILL_BEGIN: extraction-notes -->
LT_PLACEHOLDER_UNFILLED -- anything about THIS source's own layout the extraction adapter needs to handle
beyond `profile.yml`'s `adapter_config` fields: known spine/file-ordering quirks, the full front/back-matter
inventory with a proposed `translate` / `regenerate` / `omit` decision for each item and why, verse-markup
edge cases, footnote-anchor peculiarities. This is reconnaissance done BEFORE running the extractor, from
having actually looked at the source file -- not a promise about what the extractor will find.
<!-- LT_REQUIRED_FILL_END -->

## 4. Risks and open points

<!-- LT_REQUIRED_FILL_BEGIN: risks -->
LT_PLACEHOLDER_UNFILLED -- anything specific to this project that could derail it: scale/cost estimate
(word count times the per-segment convergence loop, at this project's own `engine.effort` -- default
`high`), session-limit exposure,
known-hard passages (embedded third-language text, disputed readings, wordplay), anything else worth
flagging before the mass-translate batches start. New risks discovered mid-project get appended below in
section 5, not retrofitted into this section.
<!-- LT_REQUIRED_FILL_END -->

## 5. Execution log (append-only; not gated)

Not a required-fill span -- kept up to date as the project actually proceeds, batch by batch. Mirrors
SKILL.md's own W1-W8 step order; this section tracks progress against it, it does not redefine it.

- **Stress-gate segment (W4).** Fill in once `manifest.json` exists (W2 has run): the actual segment
  chosen for the stress gate (longest body segment, plus whichever of footnotes/verse/front-back-translate
  this book actually has) and why. If this book genuinely has neither verse nor footnotes, record that
  explicitly -- a legitimate outcome, not a gap (see SKILL.md's W4).
- **Batches.** One entry per mass-translate batch (W5): date, segment IDs, outcome (converged / blocked /
  non_converged / escalated), and anything the W6 consistency pass surfaced -- cross-reference
  `consistency_issues.md` rather than duplicating its content here.
- **Final audit (W7) / handoff (W8).** `final_audit.py`'s summary counts once the project reaches
  completion, and a pointer to the delivered audit package at `output.destination`.
