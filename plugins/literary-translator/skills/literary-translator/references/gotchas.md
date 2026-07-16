# Gotchas and pitfalls

The single place to read before touching this plugin's trickiest mechanisms.
Everything here is either a load-bearing invariant that looks negotiable but
isn't, or a subsystem that is new plugin hardening rather than something the
real `historiettes-t3` project ever ran at scale. If you're about to "fix"
something that looks inconsistent, check this file first — it is probably
deliberate and the inconsistency is documented below.

## 1. Ground truth for anything "proven" is the real project, not this doc

`historiettes-t3` is this plugin's in-house, private provenance project —
the battle-tested origin that `extract.py`, `bootstrap_names.py`,
`segpack.py`, `validate_draft.py`, `draft_ready.py`, `final_audit.py`, and
`reference/historiettes-mass-translate-wf.reference.js` were generalized
from. It is not shipped with this plugin and isn't a path an installed user
can open — this file's "source-proven" claims describe what that project
demonstrated, not something you can go re-check firsthand. If you have
access to the source project, the real file is one `Read` away and is
better ground truth than a paraphrase in this plugin's own reference files.
The real project ran at ~75-segment scale; that is the actual evidence
base, and it is narrower than it sounds (one book, one language pair).

## 2. New hardening is not source-proven — pilot it

None of the following ever ran against a real book in the real project, or
they generalize beyond the one adapter path that did. They are careful,
deliberate designs, but "careful" is not the same claim as "proven." Each
needs the source-specified full test coverage, pilot run, labeling, or some
combination before being trusted unconditionally. The first four are also
flagged (in code comments / CHANGELOG) as needing the mandatory pilot run.
This is not a reason to simplify or cut any of them — it's an honest
calibration, not a warning to work around.

1. **The FRONTBACK-through-segment-loop design.** The real project handled
   front/back matter via a separate, hand-maintained `frontmatter_ru.json`,
   *not* the ledger pipeline. Routing FRONTBACK items through the same
   segment loop and ledger is new.
2. **The ledger fragment / cache-key / derivation-state subsystem** — see
   [`ledger-and-resumability.md`](./ledger-and-resumability.md) in full. The
   real project's only ledger-shaped artifact was a single
   human-readable `ledger.json` status report; the per-segment fragment
   files, the atomic writer, the merge/stale materializer, the 15-field
   composite cache key, and the derivation-state gate are all new.
3. **`engine.batch_agent_cap`'s preflight call-count estimator.** The real
   reference script has no such estimator anywhere — it simply pipelines
   whatever `SEGS` it's given. (1.3.5: W3's glossary-pass template now shares
   this same cap with its own `3*BATCHES.length + 2` worst-case formula.)
4. **The glossary-pass workflow template.** The real project ran its
   glossary pass as ad hoc `glossary/TASK.md` + codex batches producing
   `glossary/out_*.json` files — not a schema-validated Workflow script. The
   template applies the proven review-loop *mechanics* — 1.2.0: fire-and-
   forget dispatch, bounded disk-poll, disk-is-truth (the same
   DISPATCH → WAIT → CONSUME pattern review now uses, see
   `references/workflow-schema-validation.md`) — to a new context by
   analogy. Pilot this on one small batch and manually verify the
   `canon.json` merge output before treating it as fully load-bearing.
5. **The non-Historiettes adapter surfaces (`plain_text` and `custom`).**
   `plain_text` is **specified but not yet implemented**: the shipped
   `extract.py.template` FATALs on any non-`gutenberg_epub` `source.format`
   at its format gate, so there are no fixtures/round-trip self-checks to run yet —
   tracked by #62. `custom` is a co-designed escape hatch with a fixed
   manifest contract, never a third scaffolded preset implying out-of-the-box
   coverage; supported but experimental, unproven until its own tests/pilot
   runs pass.

`tests/ledger_e2e_acceptance.test.py` is the mandatory first fixture for
item 2 — build it *before* trusting the subsystem, not after. One
continuous, mocked-agent run must exercise, in order: (1) batch 1 dispatches
segs A/B/C — B converges, C hits `max_fix_rounds` → `non_converged`; (2) a
simulated interruption leaves A's genuine `recoverable` fragment after its
`in_progress` write but before its terminal write; (3) B's `style_bible.md`
fixture is edited between batches → the 2nd classification pass must
reclassify B `stale`; (4) batch 2's `select_segments.py` asserts A
`recoverable` (dispatched like `not_started`), B `stale` (re-dispatched,
full-replace fragment, no stale fields surviving);
(5) `--only-segs <C>` retries the `human_escalation` segment C, re-enters
`SEGS`, stale terminal fragment replaced; (6) `ledger_merge.py
--expected-segs` completeness check passes even with fragments from *both*
batches; (7) a final assertion on the merged `ledger.json`'s end-to-end
correctness. A pilot/soak run on a first real project is necessary but not
sufficient on its own — this fixture has to exist first.

There is also a separate hard release gate from the plan's §19 item 5 /
§20 step 12: before the plugin is considered ship-ready, BOTH
`tests/ledger_e2e_acceptance.test.py` and a genuine pilot run against a
SECOND real book (not `historiettes-t3` again; ideally a different
language preset, deliberately exercising the smoke-test gate and a real
`blocked_needs_regeneration` transition) must actually run and pass
against real data. The pilot exercises `gutenberg_epub` — the only working,
shipped source adapter (`plain_text` is specified but not yet implemented,
#62); `custom` already carries its own
`experimental/unstable, not yet pilot-proven with the NEW machinery` label
independent of which book the pilot uses.

## 3. Canonical path invariants have NO language suffix — do not "fix" this

- `draft_path(seg) = segments/{seg}.draft.json`
- `review_path(seg) = segments/{seg}.review.json`

Both are deliberately unsuffixed, unlike the real source's
`.ru.draft.json`. v1 has exactly one target language per project already
recorded in `profile.yml`, so a suffix would add no information. This is
load-bearing: every script or template that touches a draft file
(`validate_draft.py`, `draft_ready.py`, `ledger_update.py`, `final_audit.py`,
`draft_sha1.py`, `assemble.py`, `ledger_merge.py`, `select_segments.py`,
`codex_job.py`, `review_TASK.template.md`, `translate_TASK.template.md`,
`mass-translate-wf.template.js`) must use the exact unsuffixed path — a
ported script hardcoding `.ru.draft.json` is a bug, not a style choice.
`review_path(seg)` additionally requires the `segments/` prefix (matches the
real reference exactly — never a top-level `${durable_root}/{seg}.review.json`).

Writers/readers of `review_path(seg)`: `reviewDispatchPrompt` (the JS review
task-body writer, was `reviewPrompt`), `review_TASK.template.md` (the codex
review-task writer output line), and `codex_job.py` (`--kind review` — derives
and validate-before-promotes it) WRITE it; `readReviewPrompt`,
`verifyReviewArtifactPrompt`, and — since 1.3.6/#132 option b — `fixPrompt` (it
READS the on-disk `findings[]`, see §5), plus `review_artifact_check.py`,
`review_ready.py`, `ledger_merge.py`, and `ledger_update.py` (the last for the
`reviewed_draft_sha1`/`dispatch_token` binding check) READ it.

`tests/draft_path_convention.test.py` instantiates every one of the **twelve**
draft-path call sites and the **ten** review-path call sites against a fixture
and asserts the exact path, failing loudly and naming the offender if any one
of them drifts. (The regression-lock test counts writer+reader SITES, so its
review-path site count is larger than the conceptual "readers" list above — that
is expected, not a contradiction: the test locks every writer site too.)

## 4. Every copied script self-anchors — never assumes cwd, never takes a flag

Every script under `scripts/` derives its own working root via
`Path(__file__).resolve().parents[1]` (it always lives at
`${durable_root}/scripts/<name>.py`). Never assume `cwd == durable_root`.
Never add a `--durable-root` flag — that is not the mechanism.

There are two different halves of the reachability guarantee and they are
easy to conflate: the `{{DURABLE_ROOT}}` template token is how an *agent*
finds and invokes the script; `Path(__file__).resolve().parents[1]`
self-anchoring is how the *script itself* finds everything else once it's
running. Both are needed; neither substitutes for the other.

Test discipline: invoke a representative script from a cwd that is neither
`durable_root` nor the script's own directory, and assert it still correctly
reads/writes under the real `durable_root`.

Also load-bearing: `durable_root` must never resolve under `/tmp` or a
scratchpad-like path — Step 0a rejects that outright.

## 5. The fix call is 3-argument, on purpose — do not revert to 2 arguments

`agent(fixPrompt(seg, round, revObj), {effort:'high'})` — a deliberate,
documented departure from the real reference script's byte-exact
2-argument `fixPrompt(seg, round)` shape. Do not "fix" this back to two
arguments thinking it's a drift error.

Why: since 1.3.6 (#132 option b) `fixPrompt` READS `review_path(seg)` from disk
itself and applies every entry in its on-disk `findings[]` array. `revObj` (the
3rd argument) is kept for other consumers — the convergence decision and the
review-artifact gate's own `--expected-file` — but its findings are no longer
spliced into the fix prompt's text at all, so a transcription slip in the CONSUME
agent's in-memory copy can no longer reach the fixer. This closes the
review-artifact gate's residual risk for the fix step via the fixer's own
independent disk read — a reversal of the earlier round-60 in-memory design (see
`references/engine-loop.md` R1). See §9 below for what the gate protects instead.

## 6. `profile.example.yml`'s shipped comments must stay clean

Every inline YAML comment in `profile.example.yml` must be stripped to
*current rule + short rationale + remediation only* — no "round N, MAJOR #X
fix" archaeology. This file ships verbatim into every new user's own
project; a comment history that only makes sense against this plan's own
review log is noise to every actual user.

## 7. When in doubt: this plugin's own reference docs first, then the plan

If an exact field name, enum value, or hash definition is unclear, the
relevant reference file in `references/` (cross-referenced throughout this
plugin's docs) is authoritative. If genuinely ambiguous even there, the
original `PLAN_literary_translator_plugin.md` is the ultimate fallback —
not training-data guessing, not "it seemed reasonable."

## 8. The three ledger schema files — a real JSON Schema composition gotcha

Full field lists live in
[`ledger-and-resumability.md`](./ledger-and-resumability.md); the gotcha
itself is worth stating on its own because it is easy to get backwards.

- **`ledger-record-base.schema.json`** declares no `status` property at all
  — deliberately, to avoid enum conflicts when composed by the other two
  schemas. Its conditional requirements use `allOf`/`if`/`then`, and **each
  `if` clause must include `"required": ["status"]` inside itself** (not
  just inside `properties.status`) — otherwise a status-absent instance
  vacuously satisfies the `if` and the `then` requirements silently never
  apply.
- **`ledger-fragment.schema.json`** composes `{"allOf": [{"$ref":
  "ledger-record-base.schema.json"}]}` plus its own `status` enum: `[pending,
  in_progress, converged, non_converged, blocked]` — **no `stale`, ever**.
  `ledger_update.py` never writes `stale` to an on-disk fragment. It uses
  `unevaluatedProperties: false`, not `additionalProperties: false` — the
  latter cannot see properties satisfied by a sibling `allOf` branch and
  would incorrectly reject them; `unevaluatedProperties` is
  `allOf`/`$ref`-aware and gets this right.
- **`ledger.schema.json`** is the *separate*, materialized multi-record
  shape (`{"segments": {"additionalProperties": {"allOf": [{"$ref":
  "ledger-record-base.schema.json"}], "properties": {"status": {"enum":
  [pending, in_progress, converged, non_converged, blocked, stale]}}, ...}}}`).
  It composes against the *same* status-free base as the fragment schema —
  **never against the fragment schema itself** — so the wider enum
  (including `stale`) never conflicts with the fragment schema's narrower
  one. `stale` is a status `ledger_merge.py` *computes* when materializing
  this map; it is never found in any actual on-disk fragment.

Get the composition backwards (e.g. have `ledger.schema.json` extend
`ledger-fragment.schema.json` instead of the base) and the `stale` enum
value becomes invalid wherever the fragment schema's narrower enum is also
in scope.

## 9. The review-artifact gate's residual risk — stale-vs-fresh and both-stale-agreeing

`verifyReviewArtifactPrompt` + `review_artifact_check.py` exist because an
earlier design had the *agent itself* judging whether `review_path(seg)`
matched the review call's returned object — a genuine LLM judgment call,
unlike every other "mechanical confirmation" in this plugin where
deterministic code does the comparing. The fix: the agent writes the
review's own returned object verbatim to a scratch `--expected-file`, then
invokes the dependency-free stdlib-only script, which reads canonical
`review_path(seg)` from disk, canonicalizes both (sorted-key JSON), and does
a byte-for-byte comparison — printing `{match:true}` or `{match:false,
mismatch_detail:"..."}`. The agent's job shrinks to relaying that line
verbatim, never judging the match itself.

On `match:false`: retry the *original* review call once, fresh, and re-run
the same check against the retry's write. Still `match:false` → `blocked`
with reason `review-artifact-mismatch`.

**What this gate protects changed once `fixPrompt` stopped reading
`review_path(seg)` (see §5).** It no longer protects the fix step's input —
it protects `ledger_update.py`'s later `reviewed_draft_sha1` binding check
and any later audit-trail inspection, both of which still read
`review_path(seg)` from disk at a later point.

**Known, accepted residual risks — do not try to "complete" these, they are
scoped as accepted:**

1. **Stale-vs-fresh:** `--expected-file` is still *written by an LLM agent*,
   not by the JS (which has no filesystem access). If that agent ever writes
   something other than the fresh `revObj` text it was actually given — for
   example, accidentally re-writing an *earlier* round's stale on-disk
   `review_path(seg)` snapshot instead of the current one — the
   deterministic script still "correctly" compares against a wrong file and
   reports whatever the byte comparison actually finds. `tests/
   review_artifact_check.test.py` locks in a fixture for exactly this shape:
   `--expected-file` populated with a stale, previously-written
   `review_path(seg)` snapshot while the *current* `review_path(seg)` holds
   a genuinely different, later review — this must assert `{match:false}`,
   proving the comparison mechanism itself has no blind spot that lets a
   wrongly-populated expected-file slip through as a false match. That test
   locks in what *is* proven (the comparison correctly flags this specific
   mismatch shape) without overclaiming what isn't (that the expected-file
   is always populated correctly in the first place).
2. **Both-stale-agreeing (rarer, compound, explicitly not covered):** the
   case where *both* `review_path(seg)` and `--expected-file` are stale and
   happen to byte-for-byte agree with each other (but not with the actual
   current review) is an accepted residual risk. No test claims to close it.
   This residual is now confined to the ledger-binding/audit-trail question
   only — it is never a "wrong findings reach the fix step" question, since
   §5's `revObj`-direct-injection change closes that specific failure mode
   structurally regardless of what's on disk.

## 10. TDZ gotcha — schema literals must be declared ABOVE the `pipeline()` call

In both workflow-script templates, every inline schema literal must be
declared *above* its first use in the pipeline call.
`mass-translate-wf.template.js` declares `REVIEW_SCHEMA`,
`REVIEW_ARTIFACT_SCHEMA`, `LEDGER_WRITE_SCHEMA`, `LEDGER_MERGE_SCHEMA`;
`glossary-pass-wf.template.js` declares its own `CANON_VERIFY_SCHEMA` (new
in 1.2.0 — `CANON_BATCH_SCHEMA` is gone entirely, not merely relocated: the
glossary batch dispatch carries no agent-facing schema at all now, see §14
below). Each template repeats its own declare-above-use discipline
independently — they are two separate Workflow scripts, not a shared
module with one schema owner. A schema declared after its first use
silently no-ops due to temporal-dead-zone semantics in this execution
model — this is a known gotcha in the Workflow tool's execution model, not
a JS style preference. There is no runtime error to catch it; it just
silently does the wrong thing, so get the declaration order right the first
time and don't reorder these declarations later without checking every call
site.

## 11. `profile.example.yml` is deliberately non-runnable — do not "fix" that either

Every placeholder in the shipped file (`YOUR BOOK TITLE HERE`,
`/ABS/PATH/TO/YOUR_PROJECT`, `/ABS/PATH/TO/YOUR_SOURCE.epub`,
`CHOOSE_live_or_offline`, `CHOOSE_none_confirmed_or_regex`,
`CHOOSE_none_confirmed_or_markdown_ref_or_custom_regex`) is an
intentionally invalid sentinel that `profile_validate.py` fatally rejects
by design. A profile can only pass Step 0 once every placeholder is
genuinely replaced with a real value.

**Do not write a test that expects this file to pass Step 0 verbatim — it
must not.** A prior mistake in this plan's own review process made exactly
this error once. The correct test discipline needs three separate
fixtures, not one:

1. Missing profile → auto-copy the example into place + halt.
2. The verbatim shipped example → fatal rejection, naming every placeholder.
3. A fully-filled-in-but-otherwise-structurally-identical copy → clean pass.

(`tests/profile_example_validation.test.py` is this exact 3-case split.)

Validation itself is split three ways and the three should never be
conflated: (1) `profile.schema.json` — structural contract only (required
keys, enums, types, shape-only conditionals); (2) Step 0 procedural code —
everything a static schema can't check that *is* available before
extraction (real filesystem existence/writability checks, placeholder-
substring rejection); (3) W3 procedural code — the smoke-test hash
comparisons, which need `manifest.json` to exist first and so cannot run at
Step 0.

## 12. bs4/lxml dependency preflight must actually parse, not just import

`beautifulsoup4` + `lxml` are needed only by `extract.py.template`'s
`gutenberg_epub` adapter — `plain_text`, once implemented (#62), will need
neither and must never import `bs4` on that code path.

The preflight for this dependency pair must not stop at import-level. `bs4`
can import fine while the `lxml`/`xml` *parser backend* is separately
missing (`FeatureNotFound`), so the preflight must additionally actually
parse two tiny fixture strings — `BeautifulSoup("<a>x</a>", "lxml")` and
`BeautifulSoup("<a>x</a>", "xml")` — each in its own try/except, and report
a backend-named actionable message at preflight time. Catching this only
mid-extraction produces a much less legible traceback, and an import-only
check would pass while the real parse still fails later.

Every script needing `jsonschema`/`PyYAML`/`beautifulsoup4`/`lxml` must wrap
its import in a try/except with an actionable `pip install -r
requirements.txt` message naming the specific missing package — never an
unhandled `ImportError`/raw traceback.

## 13. Other easy-to-miss invariants worth a second look

- **`jsonschema.validate()`'s convenience wrapper does not enable format
  assertions by default.** `profile_validate.py`/`canon_validate.py` must
  pass `format_checker=jsonschema.FormatChecker()` explicitly.
- **`yaml.safe_load`, never `yaml.load`**, in `profile_validate.py`.
- **`draft.schema.json` is mode-neutral; `validate_draft.py` owns
  verse-policy semantics.** Do not add `verse_policy.mode`-keyed `if`/`then`
  branches or a standalone `verse.schema.json`: the schema is only the
  structural superset, while `validate_draft.py` enforces which
  `rendered`/`literal_gloss` fields are required, forbidden, or conditional
  for the active mode.
- **`agent_config_hash` deliberately excludes `batch_agent_cap`** — it's a
  pure orchestration/scheduling knob with zero effect on translator/reviewer
  output semantics. Including it would invalidate every converged segment on
  a mere batch-size tweak.
- **`used_terms_hash` includes `new_names[]`, not just `canon_names[]`** — a
  name uncanonized at segpack-build time contributes nothing to the hash
  either way; the moment it's canonized elsewhere, its bytes enter the hash
  for the first time, correctly flipping the segment stale. No persisted
  reverse index needed — this is a live re-check on every computation.
- **Regex-based proper-noun extraction plus grammatical elision can silently
  drop names entirely.** A fused elided article can hide the capitalization
  signal (`d'X`/`l'X` patterns), so test any new `languages/<code>.json` with
  synthetic elision-pattern sentences before trusting it on real text. The
  *capitalized* sentence-initial case (`L'Enclos` — the name `Enclos` behind an
  elided `l'`, or a fixed compound like `L'Aquila`?) is deliberately NOT solved
  by widening `ELISION_RE` (a reverted attempt split real compounds like
  `D'Artagnan`); 1.3.5 resolves it at the adjudication stage instead —
  `bootstrap_names.py` flags such a row `elision_ambiguous` (detection only)
  and `glossary_batch_plan.py` force-includes the pair so the glossary
  adjudicator routes it to `review_queue` unless it is confirmed a distinct
  entity (#91).
- **`derivation_bundle_hash` is split out of `plugin_bundle_hash`
  deliberately** — closes the gap where a fix to `bootstrap_names.py` or
  `segpack.py` would otherwise flip `plugin_bundle_hash` (ordinary `stale`,
  forcing retranslate) without forcing the segpack itself to regenerate
  first, i.e. silently retranslating against stale segpack/canon data.
- **The `blocked_needs_regeneration` classification is a label only** —
  computed by `select_segments.py`, never written to a ledger fragment's own
  `status` (the underlying fragment stays `converged` throughout). It
  self-clears once the operator actually reruns the missing regeneration
  step, with no `--only-segs` override needed.
- **`ledger_merge.py`'s completeness check is a subset check, never exact
  key-set equality** — `ledger.json` accumulates fragments across every
  batch ever run, so extra keys from prior batches are explicitly allowed;
  only a `SEGS` name with no matching key at all is a failure.
- **Every `ledger_update.py` write is a full replace, never a
  read-modify-write merge** — an `in_progress` write produces a fragment
  with no `reason`/`rounds`/`cache_key`/segment-stats at all, even if the
  prior on-disk fragment had a full `converged` shape. Don't "helpfully"
  carry forward prior fields.
- **No skip-translate optimization on resume, and this is a reaffirmed
  decision, not an oversight.** An `in_progress` fragment found at resume is
  classified `recoverable` and dispatched exactly like `not_started` — full
  re-translate, never routed straight to review even if a complete draft
  already exists. An `in_progress` fragment never stores a `cache_key`, so
  there's no baseline to detect a style-bible/canon edit made between crash
  and resume; the "wasteful" redundant translate call is precisely what
  naturally re-applies any such edit, since `translatePrompt` reads
  `style_bible.md`/`canon.json` fresh on every dispatch. v1's honest scope
  is "resumable via the ledger, with a redundant but safe re-translation for
  any interrupted-but-already-drafted segment" — not zero-waste resumption.
- **`REVIEW_SCHEMA` is exact-shape (`additionalProperties: false`), the
  four verdict fields, no `dispatch_token`; `REVIEW_ARTIFACT_SCHEMA`,
  `LEDGER_WRITE_SCHEMA`, `LEDGER_MERGE_SCHEMA`, and `CANON_VERIFY_SCHEMA`
  are all FLAT `agent()`-facing schemas as of 1.2.0 (§14 below) —
  `additionalProperties: false`, a relaxed union of every field the old
  `oneOf` branches used to discriminate.** `CANON_BATCH_SCHEMA` is gone
  entirely, not merely flattened. The on-disk `ledger-write-confirmation
  .schema.json`/`ledger-merge-confirmation.schema.json`/`review-artifact
  -check.schema.json` stay strong `oneOf` — a failure branch must never
  also require a success-only field (e.g. a `fragment_path`/`fragment_sha1`
  that was never written), and vice versa — and the exact-key-set JS guard
  at each flat-schema consume site is what re-establishes that
  discrimination on the agent-relayed object.
- **The composite cache-key field list and bundle memberships get restated
  in multiple places** (`ledger-record-base.schema.json`'s property set,
  `select_segments.py`/`ledger_update.py`/`cache_key.py`, the design-decision
  prose, the implementation-order steps). `used_terms_hash` specifically has
  three restatement sites. Prefer deriving the expected value
  programmatically in tests (e.g. derive the expected cache-key field set
  from `cache_key.py --seg <id>`'s own printed JSON keys and assert it
  equals `ledger-record-base.schema.json`'s declared property set) rather
  than hand-typing the same list twice and letting the copies drift.
- **`tests/ledger_e2e_acceptance.test.py`'s line-571 flake is RESOLVED
  (1.1.1).** The batch-2 full-replace assertion used to also check
  `beta_fragment_batch2["timestamp"] != beta_fragment_batch1["timestamp"]`,
  which raced when both re-converge writes landed in the same
  second-resolution clock tick — verified non-deterministic (pass / fail /
  pass across isolated runs, 2026-07-08). The wall-clock check was removed;
  the full-replace property is still proven from fragment *content*
  (`rounds`, `cache_key`, `style_contract_hash`, `reviewed_draft_sha1`, plus
  a fresh `cache_key` recompute), which loses zero coverage. Unrelated to
  `canon_adjudication_audit.py` (that gate's own 87 tests are
  deterministic).
- **`basis:"sense_translated"` (1.4.0) structurally forbids `source`** — it is
  not a convention an agent is merely asked to follow; both
  `canon-entry.schema.json` and `canon-batch.schema.json`'s ACCEPTED branch
  set `"source": false` under this basis's conditional, so a batch item that
  tries to attach a citation to a sense-rendering fails schema validation
  outright. **Precedence (D12):** `established` wins whenever a citable
  conventional target form genuinely exists — `sense_translated` is reserved
  for a rendering that makes no established-form claim at all; do not "fix" a
  `sense_translated` entry by adding a `source` — reclassify it as
  `established` instead, through a fresh glossary-pass adjudication, never by
  hand-editing `canon.json`.
- **`sense_translated` targets are deliberately NOT body-wikilinked in the
  Obsidian renderer.** A sense-rendering is an ordinary word by construction
  ("Hope", "Wolf") — `render_obsidian.py`'s unanchored, case-sensitive
  alternation would otherwise link every occurrence of that word in prose.
  `build_entity_index` skips `basis:"sense_translated"` entries when building
  the body-link alternation; the entity **note is still emitted** and its
  frontmatter `basis` still round-trips — only the automatic in-prose
  linking is suppressed, erring toward a missing link over a false-link
  flood. See `references/output-target-adapters/obsidian.md`.
- **The elision-ambiguous / speaking-name overlap resolves to `review_queue`,
  never `sense_translated`, when both apply (#91).** A candidate flagged
  `elision_ambiguous` by `bootstrap_names.py` is force-included in its
  glossary batch for adjudication regardless of what basis it might otherwise
  earn; if that same candidate also reads as a clear speaking name, the
  elision-ambiguity routing still wins — it goes to `review_queue` for a
  human/codex to confirm it is a distinct entity before any basis (including
  `sense_translated`) is ever assigned. Don't "fix" this ordering by letting a
  confident speaking-name read skip the elision adjudication step.

## 14. An `agent()` schema is a tool `input_schema` — plain object only, no combinator (`#87`)

The Workflow tool's `agent(..., {schema})` param becomes a tool-use API
`input_schema`. The API requires a top-level `type:"object"` — it does
**not** accept a top-level `oneOf`/`allOf`/`anyOf`, and would not enforce an
`if`/`then` discriminator even if it did. A file-validation schema shape
(a discriminated union, or a bare `array`) is simply **not a valid agent
schema**, full stop — it is not "stricter than needed," it is a different
kind of object that produces an HTTP 400 on first dispatch.

Before 1.2.0, three literals violated this directly: `CANON_BATCH_SCHEMA`
was a top-level `array` (blocking every glossary batch dispatch outright),
and `REVIEW_ARTIFACT_SCHEMA`/`LEDGER_WRITE_SCHEMA`/`LEDGER_MERGE_SCHEMA`
were each a top-level `oneOf` (blocking mass-translate outright). The fix
generalizes: whenever a schema is genuinely a discriminated union on disk
(review/ledger confirmation, canon-batch), the **on-disk** schema file stays
the strong `oneOf` — it validates a *script's* real stdout/file content at
runtime — while any literal actually passed to `agent()` is a **flattened**
`type:"object"`, relaxed-union version, or is deleted outright if nothing
downstream needs the agent to relay a schema-shaped return at all (as
`CANON_BATCH_SCHEMA` was — the glossary batch dispatch is schema-less
fire-and-forget now). Branch discrimination does not disappear when a
schema flattens; it moves to two other places: the on-disk schema
(Python-validated) and, for the agent-relayed object specifically, an
**exact-key-set JS guard** at the consume site — see
`references/workflow-schema-validation.md` and
`references/ledger-and-resumability.md` for the guard field sets. Never
trust a flattened literal alone: it would accept `{success:true,
error:"x"}` as a success just as readily as a genuine one.

`tests/agent_schema_top_level_object.test.py` is the direct regression lock:
every remaining `schema:` const, top-level `object`, no top-level
combinator.

## See also

- [`ledger-and-resumability.md`](./ledger-and-resumability.md) — full spec
  of the subsystem in §2/§3/§8/§9/§13 above.
- [`canon-and-glossary.md`](./canon-and-glossary.md) — glossary-pass
  mechanics referenced in §2 and §13.
- [`false-green-gate.md`](./false-green-gate.md) — `validate_draft.py`,
  the deterministic gate the review-artifact gate (§9) is *not* the same
  mechanism as.
