# literary-translator — data model + house style (the anchor)

Table of contents:
- Canon data model (the load-bearing facts)
- Advisory quick-facts (how the plugin actually works)
- Sentinel-write, hashing-duplication and ledger-write facts
- The proper-noun extractor exists in TWO copies
- THE IRON RULE
- Script house style
- Test conventions (pytest)
- Docs / registration surfaces when adding a script

## Canon data model — the load-bearing facts

- **`canon.json` is a 1:1 name dictionary, NOT an entity/coreference layer.** `entries{}` is a JSON
  object **keyed by `source_form`**; each value = `canon-entry.schema.json`:
  `{source_form, is_proper_name, canonical_target_form, basis∈{established,transliterated,title,not_a_name},
  source, confidence∈{high,medium,low}, note}`. Required: `source_form`, `is_proper_name`,
  `canonical_target_form`, `basis`, `confidence`; if `basis == "established"` then `source` is a
  required URI. Plus `review_queue[]` (plain array of QUEUED items; the QUEUED branch requires ONLY
  `note` + `disposition`, so a queued item may LACK `source_form` / `canonical_target_form`). Plus
  `generation_hashes`.
- **There is NO `entity_id`, NO `aliases[]`, NO variant/cluster/coref, NO edge/candidate/
  reconciliation/rejected store, NO two-pass agent-agreement, NO `duplicate_fr_rows` dataset.** Two
  spellings of one person = two unrelated entries; the only implicit link is both pointing
  `canonical_target_form` at the same string (nothing indexes target forms). "merge" here means either
  canon batch-merge (`canon_validate.py --batch`) or ledger fragment-merge — **never** an entity merge.
  `ledger.json` (`{segments:{seg:record}}`, 15-field `cache_key`, `status`) is
  translation-resumability, unrelated to names.
- **`review_queue` is a passive parking lot that NO gate drains today** — a book can pass W7
  `final_audit.py` and ship at W8 with a non-empty `review_queue` nobody was forced to resolve.

## Advisory quick-facts (how the plugin actually works)

- **Engine is HARDCODED, not a profile knob:** codex (`codex:codex-rescue`) does BOTH translate AND
  review; Claude does only the fix pass. Wanting Claude-translate is a plugin change, not config. The
  `engine:` block only tunes `effort` / `max_fix_rounds` / `batch_agent_cap`.
- **Source-occurrence scans: ONE pass per text, grouping spans — never re-tokenize per form.**
  `occ_index.index_manifest` is the reference implementation and the pattern to copy for any new
  source-occurrence scan. The naive shape (`for form in forms: for text in texts: scan`) is
  `O(texts×forms)` and was escalated to a blocking P1 by the repo's review bot on 1.8.0; tokenize each
  text once and group the resulting spans by form instead.
- **Segmentation cuts a NEW segment on every `<h2>`** ("files ≠ chapters" — the adapter never treats a
  spine file as one segment). A "long novella" is usually many `<h2>` segments; measure real
  per-`<h2>` word counts before worrying about size.
- **`max_segment_words` is a FATAL preflight CEILING** — extraction HALTS naming offenders; v1 has NO
  auto-split / sub-chunking. Raising it just PERMITS a big segment, it does not split.
- **Output `v1_scope`:** `segment_drafts_and_audit` (default → per-segment drafts + audit; the user
  assembles via their own `build_epub.py`) vs `assembled_book` (W9 assembler + renderer whose ONLY
  target is **Obsidian** — there is **NO EPUB output target**). An EPUB deliverable ⇒ MUST use
  `segment_drafts` + own builder.
- **`glossary.research_mode: live|offline`** = the cost/rigor knob (default offline; live is an
  explicit opt-in). Since canon has no entity model, cross-volume name consistency = SEED from the
  prior tome's `canon.json` (shape `{version, n, n_proper, n_established, review_queue, entries,
  canon_hash}`), NOT "fresh".

## Sentinel-write, hashing-duplication and ledger-write facts

- **`mark_ever_converged()`'s final shape** — each element of it replaced a defect found across seven
  codex review rounds, so a future "cleanup" that drops any one re-opens that defect: stage the
  write → `fsync` it → publish via `os.link()`, **never** `rename`, and never unlink the public
  sentinel name; open **ONE** `O_DIRECTORY` descriptor **before** the census and create staging
  relative to it; the mode `0o644 & ~umask` is read **once per run**, not per file.
- **`draft_content_sha1()` has SEVEN production copies** — six inline, plus `validate_assembled.py`,
  which alone delegates to `_canonical_draft_projection_sha1()` instead of inlining it — and the six
  inline copies drift **as a group**: touching one without the other five is a partial fix, the same
  shape as the `mentions_cfg` triple-duplication above.
- **`.resume_gate_ack` is still checked with `.exists()`**, which fails toward MORE refusal (blocks a
  resume it shouldn't) — the comfortable failure direction, which is exactly why nobody has noticed
  it yet.
- **A live project can hold decoy ledger-shaped trees that a root-discovery glob will find.** The
  `ssk-he-en/vol2` project holds four (`smoke/*`, `archive/*`) plus one empty scaffolded `slice`, so
  any glob discovering `runs/ledger.json`-shaped paths under that root returns **five** candidates,
  not one real ledger.
- **Segment ids can be colon-bearing and still reach real filenames.** Both live books carry them:
  tome1 has 15, vol2 has 2. A `validate_seg` change or a new path-building helper must be exercised
  against a colon-bearing id, not assumed safe because the schema doesn't forbid it.
- **`ledger_update.py`'s `enrich_converged_fields()` calls `emit_failure()` at its
  `if not mark_ever_converged(...)` guard and REFUSES the `converged` fragment when the
  sentinel write fails.** This is what makes a one-off sentinel backfill (the kind shipped from
  1.18.0) genuinely one-off rather than something the next run silently re-needs. The recurring wrong
  re-derivation is reading "non-fatal by design" as describing the *caller's* behavior — it describes
  only `mark_ever_converged()`'s own return value; the ledger-update caller still hard-refuses on it.

## The proper-noun extractor exists in TWO independent copies

`language_smoke_report.py` (`extract_candidate_names`) and `bootstrap_names.py`
(`tokenize` + `extract_candidates`, the one that actually feeds the canon glossary pass). They
DELIBERATELY don't share a module — `language_smoke_report.py` is copied to an isolated
`${durable_root}/scripts/` and run as a subprocess, so it can't import a sibling — which means their
boundary literals silently drift. `tests/extractor_terminators_drift.test.py` pins `TERMINATORS` AND
the `_WRAPPERS` / `WRAPPERS` set byte-identical across both: **touch one extractor, touch both.**

Design gotcha worth keeping: the boundary back-scan skips a transparent-wrapper set (`()[]{}'’‘“«`) to
find a terminator masked behind a closing/opening quote/bracket, and that set MUST stay **DISJOINT**
from `TERMINATORS` — skipping a terminator-quote (`»` / `"` / `”`) would regress the
no-terminator-behind case (`«Fiona» George` must stay two names). When extending the skip set, extend
to the domain's real quote chars (the guillemet `«` opener is the dominant FR/RU/ES dialogue case),
not the English examples a ticket happens to cite.

**Extending `TOKEN_RE`/`NAME_CONNECTORS` with a script-conditional connector (issues #282/#283):**
two traps, both caught by codex-rescue, not by manual review:
- **A single-char lookaround checking "is the adjacent char a letter-or-mark of script X" is NOT
  proof the run is script X** — a combining MARK has no script identity of its own (`_MARK_CLASS`
  already accepts marks from any of the four supported scripts after ANY letter), so a non-X base
  letter immediately followed by an X-range mark passes a naive check. Python's `re` requires each
  lookbehind to be individually fixed-width, but does NOT require several *separately* fixed-width
  lookbehinds OR'd together to share one width — so proving "a genuine script-X LETTER sits N marks
  back" (N = 0..some small disclosed bound) needs a bounded UNION of exact-width alternatives
  (`(?:(?<=[LETTERS])|(?<=[LETTERS][MARK])|(?<=[LETTERS][MARK]{2})|...)`), each alternative requiring
  the actual letter class, never the letter-or-mark union. Adversarial test case that catches a wrong
  design: a foreign-script base letter directly followed by one of the target script's own combining
  marks, then the connector, then a target-script letter.
- **Building such a pattern across multiple `r"..." r"..."` lines mixed with explicit `+` risks
  Python's implicit adjacent-string-literal concatenation silently combining pieces you meant to
  keep separate** — the resulting `.pattern` can compile fine and just be the WRONG regex, with no
  error. Build multi-part patterns via unambiguous explicit `+` concatenation only (no bare adjacent
  string literals), and always print/diff the final compiled `.pattern` against a few known cases
  before trusting the source formatting.

**Elision handling — the tokenizer SPLITS an elided article so the name behind it survives** (verified
against v1.15.2; the plugin's own `references/gotchas.md` carries the `french-elision-tokenizer-miss`
lesson, cited in `bootstrap_names.py`'s `tokenize` docstring). A capitalized-run extractor over
French/Romance text otherwise SILENTLY drops any name behind a lowercase elided article: a continuation
class that includes the apostrophe fuses `d'Effiat` into ONE token whose lowercase initial `d` makes
`is_upper_initial()` reject it, so `Effiat` NEVER surfaces as a candidate — a TOTAL, corpus-wide, silent
miss for that whole name (every elided occurrence fails identically), not a probabilistic under-count.
The plugin guards it: `tokenize(text, elision_re)` matches each raw token against the language's own
`ELISION_RE` — a per-language, exactly-2-capture-group regex loaded from the
`languages/<particle_config>` file (group 1 = article remnant, group 2 = name-initial remainder; NO
hardcoded `[dDlL]`, so `fr.json`/`it.json` share the mechanism, gated by a `has_elision` bool) — and
emits the two pieces as SEPARATE tokens each with its own recomputed span, so the remainder passes
`is_upper_initial()` and starts its own run.

- **Two DIFFERENT elision mechanisms — don't conflate them.** (1) The tokenizer's hard SPLIT above fires
  only for a LOWERCASE article (`d'Effiat`), unambiguously an elided article — `ELISION_RE`'s article
  group is lowercase-only, so a CAPITAL-article surface (`D'Artagnan`, `L'Enclos`) stays FUSED and passes
  the upper gate on its own. (2) For those capital-article forms `bootstrap_names.py` layers a
  DETECTION-ONLY flag (`elision_ambiguous: true` + `elision_stripped_form`, issue #91) when the stripped
  remainder is itself another candidate row — genuinely ambiguous (fixed compound `D'Artagnan` vs elided
  article + a known name), so per THE IRON RULE it only surfaces the ambiguity for the glossary
  adjudicator and NEVER auto-splits. When touching either extractor copy, preserve BOTH; regression-test
  with synthetic `d'X`/`l'X`/`qu'X` sentences (the split must fire ONLY on true elision, not on
  contractions `J'ai`/`C'est`, which stay capitalized and are caught separately). Generalizes to ANY
  Romance-language (`l'`, `qu'`) elision extractor.

## THE IRON RULE (enforced plugin-wide)

Scripts **surface candidates and enforce schemas; they NEVER make an accuracy/identity call.** "Are
these two forms the same entity / is this the right rendering?" is a **codex-only** judgment
(`agent(..., agentType: 'codex:codex-rescue', schema: CANON_BATCH_SCHEMA)`), never a script, never
Claude. A script MAY compute *candidate* signals deterministically (frequency, string similarity,
co-occurrence) and validate/persist codex's verdicts — that is the line. Documented at
`canon_validate.py` docstring ~L43-45 + `references/canon-and-glossary.md` L33-45.

## Script house style (match exactly)

Modeled on `final_audit.py` / `canon_validate.py` / `review_artifact_check.py` / `ledger_merge.py`.

- `#!/usr/bin/env python3`; rich module docstring documenting the FULL contract + citing the relevant
  `references/*.md`. **No `from __future__ import annotations`** — they use string annotations like
  `"Registry"`, `"list | None"`.
- **Self-anchored paths, never cwd, never a `--durable-root` flag:**
  `DURABLE_ROOT = Path(__file__).resolve().parents[1]`, `SCRIPTS_DIR = ...parent`,
  `SCHEMAS_DIR = DURABLE_ROOT/"schemas"`, `CANON_PATH = DURABLE_ROOT/"canon.json"`. (Prod re-copies
  `assets/scripts/*.py` into every project's `${durable_root}/scripts/`, so a deployed copy can go
  stale relative to the plugin path.) Module-level `UPPER_CASE` constants.
- **NO shared util module — each script is fully self-contained/dependency-free.** So a CROSS-CUTTING
  helper (e.g. the seg-id `validate_seg`) is DUPLICATED BYTE-IDENTICALLY across every consuming script,
  not imported. When such a change fans out to a team: the LEAD pins the canonical helper text in the
  dispatch, each teammate copies it verbatim into its own file(s), each teammate creates a NEW
  dedicated `tests/seg_safety_<scope>.test.py` (never edits the SHARED multi-script tests like
  `draft_path_convention.test.py` / `schema_literal_drift.test.py` — breakage of those is reported to
  the lead for CENTRAL reconciliation), and a drift-guard test asserts the copies agree. `manifest.json`
  is NOT `jsonschema`-validated at runtime (advisory schema) — the runtime script checks are the
  load-bearing gate.
- **Output: exactly ONE JSON line to stdout** (schema-shaped), ALL human detail to **stderr**. **Exit
  0 clean / 1 gate-fail / 2 fatal.** A fatal error prints a named line to stderr ONLY and prints NO
  stdout JSON (nothing can be mistaken for a schema-conforming result — the `review_artifact_check.py`
  discipline). Callers read stdout, not the exit code alone.
- Atomic write: `tmp → os.replace`, `json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)+"\n"`.
  UTC via `datetime.now(timezone.utc).isoformat()`. A custom exception carries an `offending` /
  `missing_segments` payload folded into the failure JSON. Schemas registered by `$id` == bare filename
  (jsonschema Draft202012 + referencing `Registry`) when jsonschema is used.
- **`final_audit.py` is the closest analog for a GATE:** hard checks (gate exit code) + WARN-only
  advisory checks; reads canon `entries{}` for its WARN glossary-diff (`source_form` →
  `canonical_target_form` drift, self-inconsistency). It is **stdlib-only for its core** (no runtime
  jsonschema); its `final-audit-summary.schema.json` is used by the TEST + as docs.
  `review_artifact_check.py` is proudly "dependency-free: stdlib json only" and does seg-id safety via
  an ALLOWLIST `validate_seg` — `(?:FRONTBACK:)?[A-Za-z0-9_]+` via `re.fullmatch` (upgraded from an old
  reject-`/`, `\`, `..`, absolute DENYLIST that false-accepted shell metachars like `seg;rm`). **Gate
  norm for NAME questions is WARN-first** (final_audit makes name drift a WARN; `review_queue` blocks
  nothing) — but `final_audit` IS itself a hard gate for structural / coverage / review-freshness.
- **A `"default": <value>` annotation in `profile.schema.json` is documentation-only — nothing fills it
  in.** No default-filling validator runs anywhere in the plugin; `profile_validate.py` schema-validates
  the config exactly as WRITTEN, so an absent key stays absent and every consumer must independently
  resolve "absent means X" itself. Verified on `output.adapter_config.obsidian.mentions_section.enabled`
  (`profile.schema.json:698-701`, `default: true` — its own description literally says: "this 'default'
  annotation is documentation only, see the three runtime predicates in
  render_obsidian.py/assemble.py/validate_backlinks.py for the actual mechanism"). The REAL
  enable-predicate is `mentions_cfg.get("enabled") is not False`, hand-duplicated byte-for-byte across
  **three** call sites: `validate_backlinks.py:372` (`_effective_enabled`), `assemble.py:1353`
  (`_effective_mentions_enabled`), `render_obsidian.py:207` (`_effective_mentions_enabled`) — each
  docstring explicitly cross-references the other two copies. **Flipping only some of the duplicated
  sites silently yields a false-green disabled-report on an advisory gate** — e.g. patching
  `render_obsidian.py`'s copy alone would still ship the `## Mentions` section while
  `validate_backlinks.py`'s independent copy quietly reports `"status": "disabled"`. Lesson: any
  schema-`default` flip on this plugin must grep for and update EVERY duplicated predicate site
  together — never trust the schema annotation as the single source of truth.

## Test conventions (pytest)

- `pytest.ini` at plugin root: `python_files = *.test.py` (NOT `test_*.py`) + `addopts =
  --import-mode=importlib` (required — the default prepend mode chokes on the `<name>.test` stem).
  Files are `tests/<script>.test.py`. No conftest, no CI, no Makefile. Deps in `requirements.txt`
  (`jsonschema>=4.26.0`, `PyYAML>=6.0.3`, `beautifulsoup4`, `lxml`). Run: `cd <plugin root> &&
  python3 -m pytest` (or `python3 -m pytest tests/x.test.py -v`).
- **Dominant pattern = subprocess:** copy the REAL shipped script (and any siblings/schemas it
  self-anchors to) into an isolated `tmp_path/durable_root/scripts/` so
  `Path(__file__).resolve().parents[1]` resolves against the fixture (exactly like prod), then run with
  `sys.executable` (never bare `"python3"`), an explicit `timeout=`, `capture_output=True, text=True`.
  Assert on ALL of: exit code + exactly-one-JSON-line stdout (a shared `parse_summary` helper) + stderr
  substrings + jsonschema-validate the summary against the real schema. Factory helpers return dicts a
  test perturbs in one place. End with
  `if __name__=="__main__": sys.exit(pytest.main([__file__,"-v"]))`. Confirmed-but-unfixed bugs are
  locked as FAILING asserts of the correct behavior, never skipped.
- **`language_smoke_report.test.py` low-density count-gate trap:** the script's low-name-density branch
  requires `len(checked_names) == candidate_names_total`, so a short regression sample must extract
  EXACTLY the candidates you list in `checked_names` and NO stray capital. E.g. the ticket-style
  `"'I saw Fiona.' George…"` is false-RED here because capital `I` is a third candidate → both the
  `report["candidate_names_total"] == 2` assert AND the exact-count gate fail. Fix: use all-lowercase
  lead-ins (`"'we saw Fiona.' George nodded."`) so only the intended proper nouns survive. The
  in-process `bootstrap_names.test.py` unit tests have NO such gate (subset asserts), so they can use
  ticket-literal strings — a real asymmetry between the two extractors' test styles.

## Docs / registration surfaces when adding a script

- **No script-inventory table anywhere.** A new script is auto-copied by Step 0a's glob
  `assets/scripts/*.py` (no per-file copy registration). Document it INLINE in SKILL.md under whichever
  Step/Workflow it runs (like `final_audit.py`'s W7 block or `select_segments.py`'s W5), OR in an
  existing `references/*.md` section (like `canon_validate.py` in `canon-and-glossary.md`) — never a new
  per-script reference doc, never both.
- CHANGELOG.md = loose Keep-a-Changelog: add a `-` bullet (em-dash + backtick prose) under the current
  version section; don't open a new version section unless asked.
- **`cache_key.py` bundle tuples** (`PLUGIN_BUNDLE_MEMBERS` / `DERIVATION_BUNDLE_MEMBERS`) — add a
  script ONLY if it shapes content/derivation. **GATES are EXCLUDED** (`final_audit.py` +
  `select_segments.py` are absent from the tuples). Adding to a tuple ALSO requires updating a
  **count-word** ("six scripts") duplicated in the `cache_key.py` comment +
  `references/ledger-and-resumability.md` + `references/orchestration-and-batching.md` — a drift trap
  `schema_literal_drift.test.py` will catch. `plugin.json` has no script list.
