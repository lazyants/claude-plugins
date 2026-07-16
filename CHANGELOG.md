# Changelog

All notable changes to `lazyants/claude-plugins` are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is per-plugin, not repo-wide.

## [enduser-handbook 1.4.0] — 2026-07-16

A maintenance release removing pre-existing false-rejects and one wrong-version edge in the dependency-free `profile_version` pre-flight scan, and adding one Step-0 cross-field warning — all differential-tested against Ruby/Psych so the scan still never reports a version a real YAML parser reads differently, and never halts a single document a real parser loads. No change to the capture guard.

### Fixed
- The document-wide tab-in-indentation guard rejected a tab that is legal *content* inside a block / quoted / flow scalar (e.g. a tab-indented line in the shipped-shaped `capture.command: |` block). The guard now runs after the structural scan and consults its opacity tracking, halting only on a tab used as real block indentation. Plain-scalar tab-continuations and a spaces-then-tab blank line remain documented residuals (they halt, never mis-report a version). (#126)
- A leading `---` document-start marker, a trailing `...` document-end marker, and a plain `? snake_case` explicit mapping key were rejected as "not a top-level key" even though a real parser loads such a single document and reads `profile_version` fine. The Step-4 shape allowlist now accepts a single leading `---`, a trailing `...` marker, and a plain `? snake_case` explicit key, while still halting a genuine multi-document stream (a real parser's single-document load returns the *first* document, so reading a later document's version would be wrong). A *trailing* bare `---` — which opens an empty final document a real parser would ignore — stays a safe documented halt (the scan cannot cheaply tell it from a real second document, so it conservatively halts; never a wrong version). An explicit `? profile_version` in every spelling is rejected — it is a confirmed hidden duplicate that changes the parsed version. Non-snake_case / quoted / tagged explicit keys and `%YAML`/`%TAG` directives remain documented residuals (they halt). (#127)
- The numeric value reader mis-reported a version on two spellings a real parser reads differently: `profile_version: 010` was read as decimal 10 (Ruby/Psych reads it as octal 8), and an integer above 2⁵³-1 was rounded (e.g. `9007199254740993` → `…992`). Both now halt as `malformed` — a leading-zero integer is ambiguous (a parser may read it as octal) and an integer beyond the exact-representable range cannot be read without a full parser. The canonical form stays a non-zero-leading decimal integer. (surfaced reviewing #125/#126/#127)
- Mechanism B (an invalid dedent) stays a deliberately documented residual, now backed by expanded differential-fuzz coverage proving block scalars are treated as pure opacity (never flagged): a false-reject-free B detector requires modeling block-scalar content together with general indentation, reintroducing the mini-YAML-parser mis-parse risk that would false-reject the valid shipped `capture.command: |` block. (#125)

### Added
- Step 0 now emits a warn-level cross-field check: every key of `capture.role_flags` must be a member of `capture.auth_role_enum`. A typo'd role key (e.g. `admn` under `auth_role_enum: [admin]`) previously validated clean while its intended capability gate silently never applied; the check names any orphan key and continues, consistent with the existing unknown-key warning policy. (#155)

## [ai-cli-optout 1.1.1] — 2026-07-12

Retires the root `KNOWN_ISSUES.md`; its tracked caveats and planned-coverage list now live as GitHub issues. Mostly a documentation move, but the shipped `vendors/anthropic.json` carries two `tradeoff_note` strings that pointed at `KNOWN_ISSUES.md §C2`, so retargeting them ships a patch release.

### Changed
- `vendors/anthropic.json` — the two `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` `tradeoff_note` strings now cite <https://github.com/lazyants/claude-plugins/issues/142> instead of `KNOWN_ISSUES.md §C2`. Wording otherwise unchanged.
- `README.md`, `CONTRIBUTING.md` — every in-repo `KNOWN_ISSUES.md` pointer retargeted to the corresponding issue: the §C2 GrowthBook trade-off → #142, the Vercel Claude Code plugin planned-coverage note → #144, the Linux privacy surfaces out-of-scope note → #145. The contributor sweep/checklist prose is rewritten to the GitHub-issue workflow (file or update an issue labeled `plugin:ai-cli-optout`; close the tracking issue in the same PR).

### Removed
- `KNOWN_ISSUES.md` (root) — content migrated to GitHub issues: documented caveats C1 / C2 / C3 → #141 / #142 / #143; planned coverage → Vercel Claude Code plugin #144, Windsurf/Codeium + Zed + Ollama #58, Linux privacy surfaces #145.

## [literary-translator 1.1.0] — 2026-07-08

Adds optional **book assembly + output rendering**, lifting the 1.0.0 non-goal "v1 delivers converged per-segment drafts, not an assembled book". Converged per-segment drafts can now be assembled and rendered into an output target — an Obsidian glossary-wiki keyed on the frozen canon — behind a deterministic render/diff acceptance gate. New; not yet pilot-proven at scale.

### Added
- `skills/literary-translator/assets/scripts/assemble.py` — fail-closed 3-source assembler joining `manifest.json` + per-segment `*.draft.json` + `segpack_*.json`, gated on the ledger (every in-scope segment `converged` + sha1-matched); emits a target-agnostic NodeStream + anchor map to `out/.assembled/`, then dispatches the resolved output adapter.
- `skills/literary-translator/assets/scripts/render_obsidian.py` — the `obsidian` output adapter: chapter notes with folder-qualified `[[People/…|display]]` wikilinks, footnotes, verse blocks with literal glosses, and one entity note per `canon.json` entry (canon IS the entity registry). Fail-closed against symlink data-loss (ownership-marker gate + no-follow atomic writes; `out_dir`, its parent, the leaf, and the marker all guarded).
- `skills/literary-translator/assets/scripts/output_resolve.py` — target-agnostic adapter + `out_dir` resolution from `profile.yml`'s `output.*`, shared by assemble and diff.
- `skills/literary-translator/assets/scripts/diff_rendered_output.py` — deterministic render/diff acceptance gate: `--accept-baseline` freezes the render; a re-render must match line-for-line (exit 0). Same symlink-safe discipline for its `.baseline/` snapshot.
- `skills/literary-translator/references/output-target-adapters/` + schema shapes — normative adapter docs (`assembly-and-output`, `obsidian`) and the NodeStream / adapter-result schema literals.
- Suite grows to 676+ tests (from 500+), including adversarial symlink-safety regressions across all three new scripts.

### Changed
- `SKILL.md`, `assets/profile.example.yml`, `assets/schemas/profile.schema.json` — `output.v1_scope: assembled_book` wiring and the `output.*` config surface (adapter target, destination, wikilinks + category-folder options).

## [literary-translator 1.0.0] — 2026-07-08

Initial release. New plugin — high-fidelity literary book translation over a Gutenberg-style EPUB or plain-text source: a codex-translate → deterministic false-green gate → codex-review → Claude-fix loop run to convergence per segment, with a frozen name/realia canon, a configurable verse policy, and ledger-based resumability. Generalized from the in-house historiettes-t3 project.

### Added
- `plugins/literary-translator/skills/literary-translator/SKILL.md` — the engine-loop skill: codex-translate → false-green gate → codex-review → Claude-fix, frozen name/realia canon, configurable verse policy, ledger-based resumability, and `gutenberg_epub` / `plain_text` / `custom` source adapters.
- `skills/literary-translator/assets/scripts/` — stdlib-first, self-anchored gate/validation scripts (canon validation, the `validate_draft.py` false-green gate, ledger update/merge, final audit, segment selection, and the `canon_adjudication_audit.py` human-adjudication gate); each emits exactly one JSON line to stdout with human detail to stderr, under a shared exit-code convention (0 clean / 1 gate-fail / 2 fatal).
- `skills/literary-translator/assets/schemas/` + `references/` — JSON Schemas for every machine-checked artifact plus the reference docs (engine loop, canon/glossary, ledger/resumability, verse policy, source-format adapters, false-green gate).
- `tests/` — pytest suite (`*.test.py`, `--import-mode=importlib`) over synthetic fixtures: 500+ tests across every script, schema-literal drift, and an end-to-end ledger acceptance run. Run with `cd plugins/literary-translator && python3 -m pytest`.
- Honesty caveats carried from the source project: extraction is proven against Historiettes' 17th-century French specifically (every other language/source is an unverified starter preset gated by a mandatory smoke test), and one of the two source adapters plus the expert custom extractor remain experimental until pilot-proven end-to-end.

## [enduser-handbook 1.3.0] — 2026-07-11

Cross-line structural coverage for the dependency-free `profile_version` scan. No change to the capture guard. (This entry backfills a changelog gap — 1.3.0 shipped via #128 but was never logged.)

### Added
- The `profile_version` scan now catches two additional structural error classes, both provably false-reject-free (differential-tested against Ruby's Psych, never halting a document a real YAML parser would load): mechanism A (an unterminated flow collection or quoted scalar anywhere in the document) and mechanism C (an alias to an undefined anchor, in a document with no `&anchor` defined at all). Mechanism B (invalid dedent, including through the block-scalar `capture.command: |` shape) is deliberately deferred and tracked as a follow-up. (#110)

## [enduser-handbook 1.2.0] — 2026-07-10

A feature release adding five authoring-ergonomics and coverage enhancements. No change to the fail-closed capture guard — its seven-sentinel route order is untouched.

### Added
- A dependency-free profile validator: a normative `assets/profile.schema.json` pins the profile shape, `references/profile-validation.md` holds the supported-version list and the ordered Step-0 checks, and `assets/lib/profile-version.mjs` reads `profile_version` via a pure, parse-safe column-0 line-scan (no YAML parser — Node has none and the plugin ships zero dependencies). It allowlists the whole top level and fails closed on every other YAML spelling, proved by differential testing against a real parser; a small `node` CLI tail (exit 0/1/2) is optional and Step 0 never requires `node`. (#64)
- A `/scaffold-profile` command (the plugin's first `commands/` entry) that generates `.claude/handbook/profile.yml` interactively — auto-detecting `stack.*` from `composer.json` / `package.json` / framework markers and confirming with the user, then writing a filled copy of the canonical example plus a `style-guide.md` stub. It never overwrites an existing profile (writes a `.new` sidecar) and never invokes the capture workflow (`Skill` and `Bash` excluded from `allowed-tools`). (#66)
- State-variant capture: `assertIdentity` gains an optional `state: { present, absent }` marker so a chapter can capture **real** empty / error / denied states (never a synthesized response) — `state.present` is a first-class readiness anchor for screens with no normal heading, and `state.absent` fails the run closed if a staged precondition reverted. `references/state-variants.md` and the completeness gate's per-page state-coverage checklist document it. (#67)
- A per-role surface re-audit: `assets/lib/surface-diff.mjs` (`structuralKey` + `diffSurfaces`) diffs the interactive surface between roles on the PII-free structural tuple `[tag, role, name, testId]` — never per-role label/class fields — with counts 0-filled across the full declared role set so both membership and count asymmetry are caught. It reuses `matrixLabel` from `control-inventory.mjs` for display only. (#73)
- `references/capture-engines.md` — one reference documenting the four `capture.engine` values (Playwright / Cypress / Puppeteer / manual), each engine's guard `resourceType` obligation, and where a recipe must fail at install rather than pretend coverage (Cypress's `req.resourceType` is deprecated as of 14.0.0). Marked "illustrative recipes, not tested contracts". (#70)

## [enduser-handbook 1.1.2] — 2026-07-10

A maintenance release closing seven issues — one guard-hardening fix, three correctness fixes, one test-harness fix, and two documentation-accuracy trims.

### Security
- `hasDangerousVerb` percent-decoded URL segments only once, letting a doubly-encoded dangerous verb (e.g. `%2564elete`) bypass the capture guard's deny step on a destructive GET. Decoding now iterates to a fixed point (capped at 5 passes). The always-on protection was never affected: every non-GET already failed closed. (#71)

### Fixed
- `dismissModal` fell through to the cancel-button branch on a fading-out modal because `dialog.isVisible()` resolves instantly rather than waiting for the close animation; it now uses a bounded `waitFor({ state: 'hidden', timeout: 1000 })`. (#49)
- The `capture-helpers.playwright.ts` header advertised only the WebSocket-routing Playwright floor (>= 1.48) and omitted the higher floor actually required by `assertIdentity`'s `filter({ visible: true })` spinner check (Playwright 1.51); both floors are now documented, each attached to the API that needs it. (#50)
- The JSDoc above `installCaptureGuard` described the route-classification order as six branches, omitting `classify-benign`; it now lists the full seven-branch order matching `decideRoute`. (#51)
- `surface-audit.playwright.ts`'s coverage-matrix label chain omitted a control's `value`, so a native `<input type=submit value=Delete>` printed as `(unlabelled control)` even though the JSON inventory captured `value` correctly. The chain moved to a unit-tested `matrixLabel()` in `assets/lib/control-inventory.mjs`, with `value` ranked directly below `ariaLabel` — HTML-AAM accessible-name order for `<input type=submit>`. (#52)
- `count_fixed` in `tests/reference-assets.test.sh` emitted `0\n0` for an absent needle (`grep -c` already prints 0 before exiting 1), so the per-sentinel check errored and fell into its OK branch — reporting a MISSING guard sentinel as "present exactly once". (#53)

### Changed
- Corrected an overstated "fork it for other engines" claim across 19 sites (`SKILL.md`, six reference docs, `README.md`, and eleven asset-banner comments): the methodology is normative and engine-agnostic, the Playwright driver assets get reimplemented per engine, and the engine-neutral `assets/lib/*.mjs` helpers are reused as-is by any engine's driver glue. (#69)
- Documented that the shipped `classifyGraphqlRead` read-classifier admits only inline single-operation GraphQL queries; a project whose reads are REST/RPC POST calls (Django/DRF, JSON-RPC) must supply its own fail-closed `classifyRequest`. No code change — the hook already accepts one. (#69)

## [enduser-handbook 1.1.1] — 2026-06-22

Two fixes shipped across two stacked PRs: a capture-safety correctness correction and a set of capture-determinism guardrails for page-identity assertions.

### Fixed
- `references/capture-spec-helpers.md`, `assets/capture-helpers.playwright.ts` — a broken/failed `<img>`'s `alt` text *is* painted into the frame via browser replacement-rendering, but it is not a DOM text node, so the text/value/placeholder leak-scan misses it exactly like it misses `::before`/`::after` content. `alt` moves out of the "non-rendered attributes" group into the painted-but-unscannable eyeball-backstop bucket; only `title`/`aria-label` remain genuinely non-rendered. (#15)
- `references/capture-safety.md` — documents the bidirectional mask/leak-scan scope rule: scope must equal the captured frame, never narrower. A full-viewport/full-page shot scans the document root (so framed app chrome, e.g. a logged-in user name, is never left unscanned); a `captureRegion` shot scans its own element-scoped locator — `maskAndAssert` is locator-driven, so a non-modal capture otherwise gets no automated scan at all. (#15)

### Added
- `references/page-identity.md` — four author-time capture-determinism guardrails, each guarding a shot that ships wrong or broken while the run still looks green: a zero-height layout wrapper false-negatives `toBeVisible()` (assert a content-bearing child instead); a mid-animation frame gets captured before a transition settles (disable animations or wait for it); a full-element shot of lazy-loaded/virtualized content ships blank below-the-fold rows (scroll to load first); and a deliberately staged data-state precondition silently reverts unnoticed (pair it with a fail-closed assertion that it held). (#17)

## [enduser-handbook 1.1.0] — 2026-06-21

First additive publish-target adapter since 1.0.0, fulfilling the 1.0.0 promise of additional publish targets. No change to the existing authoring rules; the only base-skill edits are correctness fixes the new adapter exposed.

### Added
- `references/publish-targets/static-md.md` — a normative publish adapter for a plain-Markdown docs tree (GitHub wiki, MkDocs, GitBook, plain repo): flat-index TOC wiring, relative Markdown links computed from the chapter file, and a hard requirement of `publish.wikilinks: false` (halts if true). Universal plain-Markdown fallback alongside the existing `obsidian-vault` adapter.
- `tests/reference-assets.test.sh` — a new `== publish-target adapters ==` block: exact-key binding assertions for `static-md.md`, the relative-link mandate, the no-Obsidian-leakage guards, the Step 0b filename-normalization rule, and the dynamic halt-list phrasing.

### Changed
- `SKILL.md` Step 0b — explicit adapter-filename normalization (lowercase, replace `_` with `-`; `obsidian_vault` → `obsidian-vault.md`, `static_md` → `static-md.md`), fixing a latent ambiguity that also affected `obsidian_vault`; the "Available:" halt list is now derived dynamically from the files in `references/publish-targets/` minus `README.md` instead of hardcoding the adapter set.
- `SKILL.md` W4 + Consistency and `references/glossary-discipline.md` — `publish.glossary_seed` reads are now conditional ("when `publish.glossary_seed` is set/readable"), since a static docs tree may ship no seed index. The `obsidian-vault` adapter keeps requiring the seed as a target-level requirement.
- `references/publish-targets/README.md` — "what ships" now lists both `obsidian-vault` and `static-md`; `confluence`/`gitbook`/`docusaurus` remain future targets.
- `assets/handbook.profile.example.yml` — target-enum comment honesty trim (`obsidian_vault`, `static_md` ship; `confluence`/`gitbook`/`docusaurus` are future).
- `marketplace.json` — description now mentions both publish adapters; added `markdown` and `docs` keywords.

### Tests
- `tests/reference-assets.test.sh` — new `== publish-target adapters ==` gate covering the `static-md.md` bindings, relative-link mandate, no-`[[`/no-dataview leakage, `wikilinks: false` requirement, Step 0b filename-normalization phrase, and the dynamic halt-list phrasing (stale `Available: obsidian-vault.` literal gone; `files in this directory minus README.md` present).

## [enduser-handbook 1.0.6] — 2026-06-20

Residual-hardening release for the v1.0.5 capture tooling, closing four gaps surfaced while authoring chapters. No change to the existing authoring rules; the engine-agnostic stance and the v1.0.5 PII-leak whitelist are preserved (the only new verbatim field, `className`, is brought under the documented seeded-data + human-scrub boundary).

### Added
- `references/completeness-gate.md` — a concrete **disclose TRIGGER LIST** (errors/500 on an absent prerequisite, live external send, un-maskable PII, irreversible action, role-gated control) + copy-paste **"Disclosure prose templates"**, replacing the previous principle-only guidance.
- `assets/capture-helpers.playwright.ts` — `captureRegion` gains an opt-in `{ maxHeight }` cap that clamps a runaway-height region (temporary CSS `max-height`/`overflow` + `scrollTop` reset, shot at `scale: 'css'`, restored after); a separate `blockedBenign` ledger + `blockedBenign()` accessor.

### Changed
- `assets/lib/control-inventory.mjs` — `INTERACTIVE_SELECTOR` now also matches framework button/toggle controls (`.btn`, `[data-bs-toggle]`, `[data-toggle]`) so glyph/icon controls (`<span class="btn glyphicon-trash">`) are no longer missed; these are ENUMERATED but not "genuine" (their text stays suppressed, preserving the PII whitelist). `extractRecord` now captures `class` verbatim (`className`) and `classifyByShape` scans it for destructive icon classes (`glyphicon-trash`/`fa-trash`/`bi-trash`/`mdi-delete`).
- `assets/lib/capture-guard-policy.mjs` — `classifyRequest` gains a `'benign'` verdict (`'read' | 'benign' | undefined`) and a new `[guard:classify-benign]` branch between `deny` and `eventsource`: known-harmless dev telemetry (laravel-boost `/_boost/`, Sentry) is BLOCKED (it never fires) but routed to the non-dangerous ledger so `assertNoDangerousHits()` no longer false-trips on console-logging pages. The predicate is now total (consulted for beacons/SSE too, so it must return `undefined` for unrecognized requests and never throw).
- `assets/surface-audit.playwright.ts` — `className` added to the coverage-matrix label fallback (a class-only glyph control shows its class instead of `(unlabelled control)`).
- `references/capture-safety.md`, `references/capture-spec-helpers.md`, `references/page-identity.md` — documented the disclose triggers, the `'benign'` verdict (`'read'` admits, `'benign'` blocks-but-not-counted), the `className` verbatim field under the PII boundary, and the `captureRegion` `maxHeight` cap.
- `assets/capture.example.spec.ts` — example `classifyRequest` returning `'benign'` for `/_boost/` + Sentry shapes; `captureRegion(..., { maxHeight })` usage.
- `plugins/enduser-handbook/tests/` — new `className`/glyph cases (control-inventory), `'benign'`-verdict cases (capture-guard-policy), a predicate-totality case (graphql-read-classifier); `reference-assets.test.sh` extended (SEVEN-sentinel order incl. `classify-benign`, plus new selector / `className` / `blockedBenign` / `maxHeight` / disclose assertions).

## [enduser-handbook 1.0.5] — 2026-06-20

Tooling + revalidation release. The methodology already mandated a live-UI surface enumerator and the capture-safety/page-identity machinery, but shipped neither as runnable code — every chapter re-implemented them by hand and they drifted. This release ships them as non-normative Playwright **reference implementations** (the contract stays engine-agnostic — fork for other engines, exactly like `regression-checks.sh`), adds a first-class **revalidation/audit mode**, and fixes wording/consistency drifts surfaced across several authoring sessions. No change to the existing authoring rules.

### Added
- `assets/lib/control-inventory.mjs` (+ `control-inventory.d.mts`) — browser-agnostic, Node-testable extraction/normalization: `extractRecord` (always returns a record, never drops a control), `normalizeControls` (no filtering by text presence), `classifyByShape` (destructive-control hint).
- `assets/surface-audit.playwright.ts` — live-DOM surface enumerator (uses `page.$$`, not `$$eval`, so extraction is unit-testable) that dumps every control's verbatim text/title/aria-label/href/role — icon-only controls included — plus a coverage-matrix skeleton.
- `assets/capture-helpers.playwright.ts` — context-level `installCaptureGuard` (service-worker block, ordered fail-closed request classifier covering writes/SSE/beacons, WebSocket blocking via `routeWebSocket`, single `classifyRequest` read escape), plus `assertIdentity`, `captureRegion`/`captureViewport`, `openModalDialog`/`dismissModal` (Escape-first), and `maskAndAssert` (newline-joined leak-assert + mask-coverage assert).
- `assets/capture.example.spec.ts` — skeleton chapter spec wiring the canonical guarded-capture flow.
- `references/capture-spec-helpers.md` — engine-agnostic contract for the helper module + spec skeleton.
- `references/revalidation.md` — audit/revalidation mode for already-merged chapters.
- `plugins/enduser-handbook/tests/control-inventory.test.mjs` (`node --test`, zero deps) + `tests/reference-assets.test.sh` (structural gate) — regression-catchers for the shipped reference assets (icon-only/destructive controls survive; guard ordering and invariants hold; cross-file wording contracts).

### Changed
- `references/completeness-gate.md` — added a "Surface enumeration (mechanical first pass)" recipe pointing at the reference enumerator; cross-check that the manifest's `glossary_terms` and the chapter frontmatter's `glossary_terms` stay in sync.
- `references/manifest-discipline.md` — added "Shared-edit hotspots" (the manifest/glossary/chapter-index are append-hotspots; resolve additively and re-run type/lint; split the manifest into per-chapter modules); renamed the per-chapter field `glossaryTerms` → `glossary_terms` to match the assets.
- `references/container-isolation.md` — added "Capturing from a git worktree": overlay the dangling symlinked `node_modules` with a second read-only mount, stage with explicit `git add` (not `-A`), serialize parallel-worktree captures.
- `references/page-identity.md` — server-rendered pages (no post-mount XHR) are now a first-class identity case (assert heading/DOM directly); added screenshot guidance (reset session-persisted filters before overview shots; capture the viewport for long unpaginated lists).
- `references/anti-fabrication.md` — added a concrete "do not 'correct' the UI's grammar/punctuation" anti-example (hyphenation/spacing/casing).
- `references/capture-safety.md` — Escape is the version-agnostic safe dialog dismiss (don't pin to a framework-specific cancel handle); pointer to the new helper contract.
- `references/running-ui-source.md` — points the "enumerate the running UI" mandate at the reference enumerator.
- `SKILL.md` — added W6 revalidation/audit mode; server-rendered page-identity wording; pointers to the new enumeration + helper assets.
- `assets/handbook.profile.example.yml`, `assets/capture-manifest.example.yml` — `page_identity_signal` / `waitForApi` now cover the server-rendered (no-XHR) case.
- `assets/regression-checks.sh` — clarified that `golden` must be the SAME chapter's prior version, not a sibling exemplar.

## [enduser-handbook 1.0.4] — 2026-06-19

Documentation-only release. Genericizes the shipped example profile so the public asset no longer carries project-identifying domain strings; no behavioral or schema change to the plugin.

### Changed
- `assets/handbook.profile.example.yml` — replaced project-specific persona, audience, and live-action examples (energy-market "Marktpartner", Apigee, Brand7/ELE, DocuSign, …) with neutral, illustrative placeholders. The example still exercises every field; it just no longer fingerprints a specific project.
- `README.md` — added a "Tips for best results" section to the `enduser-handbook` entry: plan the chapter first then fan out at high effort with multi-agent orchestration (`ultracode`), author one page at a time, review from multiple agent perspectives, and rerun the skill as a completeness pass to confirm every feature is described.

## [enduser-handbook 1.0.3] — 2026-06-19

Documentation-only release. Adds three concrete capture-safety hazards surfaced while authoring the `/admin/contracts` chapters; no behavioral or schema change to the plugin.

### Changed
- `references/capture-safety.md` — four additions:
  - **Leak-assert must read per-text-node, not a concatenated `textContent`.** Joining a subtree into one string fuses adjacent cells, so unrelated neighbours match a pattern neither contains alone (an order number butting against the next cell reads as an IBAN) — a false leak that wastes a run and can hide a real one. Build the scanned string from individual text nodes + form-control values joined by a newline.
  - **Dismiss confirm dialogs via the safe control, pinned by selector.** The close click is itself a hazard: select the non-destructive / non-primary (or cancel-labelled) button, never "the primary button" or "the first button", which can resolve to the destructive control depending on button order. Assert the dialog's identifying text before clicking.
  - **Auto-save-on-input fields are observe-only.** A field that persists on every keystroke (notes box, inline-edit, immediate toggle) is a mutating action with no Save button — typing one character *is* the write and corrupts the synthetic record mid-run. Seed it and capture as-is; never type. Classify side-effects as persists-on-input, not only persists-on-submit.
  - **Synthetic seed data must be hermetic.** Creating a record via factories can fire model observers / lifecycle hooks that send e-mail, queue jobs, broadcast, or call an external API — so a "local-only" seed can still hit a live integration. Guard the seed to local AND confirm no hook on the seeded models performs an external send (or fake the outbound layer for the seed run).

## [enduser-handbook 1.0.2] — 2026-06-19

Documentation-only release. Extends the PII-masking guidance to cover identifiers that have no detectable pattern; no behavioral or schema change to the plugin.

### Changed
- `references/capture-safety.md` — added a fourth masking rule for **non-pattern-matchable PII** (personal names, customer/account ids, opaque record hashes). The fail-closed leak-assert can only catch PII it can *match* (an e-mail regex, a known domain), so it is blind to free-form identifiers — a silently-missed mask (renamed column header, drifted selector) ships the real value. For that class the *mask itself* must be fail-closed: have it report how many targets it matched and assert that count equals the intended number, so a missed target throws instead of leaking. Pattern-matchable PII stays caught by the leak-assert; unmatchable PII is caught by the coverage assert.

## [enduser-handbook 1.0.1] — 2026-06-19

Documentation-only release. Hardens the screenshot-capture guidance in the skill; no behavioral or schema change to the plugin.

### Changed
- `references/capture-safety.md` — the PII-masking guidance now mandates *reproducible* masking: mask in-step (including control/header values), assert no leak with a fail-closed check, and scope both the mask and the leak-assert to the screenshot frame rather than a DOM subtree (a transparent backdrop can bleed un-masked content from the page behind a modal). Always keep an eyeball-confirmation shot.
- `references/container-isolation.md` — added an engine-agnostic "Common command patterns" section (pin the locale, run as the host user, keep engine caches out of the bind-mounted repo, join the existing network instead of recreating services, pin the engine image in lockstep with the test dependency). Concrete per-project commands still live in the project's `capture.command` / `.claude/handbook/capture-recipe.md`.
- Clarified that `capture.locale` is a **full POSIX locale** (e.g. `de_DE.UTF-8`) fed verbatim to `LANG`/`LC_ALL`, distinct from the content-language code in `language.code` — a bare ISO code can't pin date/number/sort formatting. Reconciled across `SKILL.md`, `container-isolation.md` guarantee 1, and the example profile (`capture.locale: de_DE.UTF-8`), so the shipped example now literally satisfies the guarantee.

## [cc-usage-coach 1.0.0] — 2026-06-18

Initial release. New plugin — personalized, behavior-aware analysis of where your Claude Code (Max/Pro) usage-limit tokens go, with ranked, low-effort ways to use fewer, computed entirely from your local session logs. Python measures; Claude concludes.

### Added
- `plugins/cc-usage-coach/skills/cc-usage-coach/SKILL.md` — the skill that drives the scripts and writes the personalized report from the signal pack.
- `scripts/extract.py` — scans local Claude Code session logs into a local `dataset/`.
- `scripts/signals.py` — emits `signal_pack.json` (path-free AND project-name-free — project labels are opaque IDs — safe to share) plus two local-only maps: `source_index.json` (opaque `source_ref` → real file) and `project_index.json` (opaque project ID → real project name).
- `scripts/arc.py <source_ref>` — inspects a single session's prompt arc (local-only).
- Local-first by construction: no network calls; `source_index.json`, `project_index.json`, `dataset/`, and the `arc.py` digest are local-only (real paths, project names + prompt text, `0600` where applicable, never uploaded). Honors `CLAUDE_CONFIG_DIR`; extra scan roots via `CC_COACH_CONFIG_DIRS`; output location via `CC_COACH_OUT` (else next to the scripts if writable, else `${XDG_CACHE_HOME:-~/.cache}/cc-usage-coach/`).
- `tests/` — pytest suite over synthetic fixtures (no real logs, no network) covering the extractor, the signal-pack shape and its path-free + project-name-free guarantee, the per-session arc, and fixture safety. Run with `bash tests/run-all.sh`.

## [enduser-handbook 1.0.0] — 2026-06-18

Initial release. New plugin for generating end-user handbooks across projects (German/„Sie", English, any register; Laravel/Vue, Django/React, etc.) from a per-project `.claude/handbook/profile.yml`.

### Added
- Methodology lifted from VPP-handbook (Diátaxis, anti-fabrication, capture safety, glossary discipline, completeness gate); project-specific bits (language, stack, capture command, publish target) are profile-driven.
- v1 ships the `obsidian_vault` publish-target adapter; Confluence/GitBook/Docusaurus targets are an additive future change.

## [db-guardrails 1.0.0] — 2026-05-22

Initial release. New plugin — protects databases from accidental destructive commands run by AI coding agents. Generalised from a four-layer guardrail stack built in-house after an agent twice wiped a development database via a misrouted `artisan migrate`.

### Added
- `plugins/db-guardrails/hooks/block-destructive-db.sh` + `hooks/hooks.json` — always-on `PreToolUse:Bash` hook (layer 4). Framework-agnostic: blocks raw SQL (`DROP`, `TRUNCATE`, `DELETE` without `WHERE`), Laravel, Rails, Django, Prisma, TypeORM, Sequelize, Knex, Drizzle, Doctrine/Symfony, EF Core, Alembic, Flyway, Liquibase, MongoDB, Redis, plus `docker compose down -v` and `rm -rf` of DB data directories. Out-of-band bypass via `ALLOW_DESTRUCTIVE_DB_HOOK=true`; no inline self-bypass. Written for bash 3.2+; `jq`/`python3` payload parsing with a fail-open-with-warning fallback.
- `plugins/db-guardrails/skills/db-guardrails/SKILL.md` — `/db-guardrails` installer skill. Detects database engine + framework, scaffolds layers 1–3.
- `assets/` — layer 1 privilege separation for MySQL/MariaDB (`mariadb`/`mysql` client auto-detected) and PostgreSQL; layer 2/3 drop-in guard files for Laravel, Django, Rails and Symfony.
- `references/framework-guards.md` — per-framework boot-guard placement notes, plus the Node-ORM connection-string-split config pattern and the MongoDB scoped-role recipe.
- `tests/block-destructive-db.test.sh` — 28 assertions covering blocked commands, legitimate look-alikes (`truncate -s 0`, `php artisan migrate`, `DELETE ... WHERE`, `rm -rf node_modules`), and the bypass env var.

## [obsidian-project-vault 1.0.0] — 2026-04-28

Initial release. Promotes the in-house `obsidian-project-vault` skill (previously a personal-scope skill at `~/.claude/skills/`) into a marketplace plugin so it can be installed and updated via `claude plugin install obsidian-project-vault@lazyants`.

### Added
- `plugins/obsidian-project-vault/skills/obsidian-project-vault/SKILL.md` — LLM Wiki pattern, three-layer architecture (raw sources / wiki / schema), four setup modes (create, migrate, audit, ingest), Report template + frontmatter, INDEX.md navigation, CLAUDE.md workflow integration, query-and-file-back loop, vault-lint operation.
- `plugins/obsidian-project-vault/skills/obsidian-project-vault/references/obsidian-tips.md` — human-side Obsidian workflow notes (Web Clipper, Dataview queries, graph view).

## [ai-cli-optout 1.1.0] — 2026-04-24

Adds Vercel CLI and generalizes the CLI-command opt-out schema so adjacent developer CLIs can slot in without bespoke fields.

### Added
- `vendors/vercel.json` — Vercel CLI. Two documented opt-outs, both shipped: `vercel telemetry disable` subcommand (persistent — writes `collectMetrics=false` to the XDG config file cross-platform) and `VERCEL_TELEMETRY_DISABLED=1` env var (per-run override only — does NOT change the persisted status, per vendor docs). `persistent_files[]` surfaces config + auth paths for macOS, Linux, and Windows (`%APPDATA%\Roaming\xdg.data\com.vercel.cli\`) for review — never deleted.
- `cli_commands[]` schema field and test-suite invariant (`cmd` + `disables` non-empty).

### Changed
- `vendors/copilot.json` — `gh_config_commands[]` → `cli_commands[]`. Semantics unchanged; the old name was specific to `gh config set`, the new name covers the generic "vendor-blessed CLI opt-out command" pattern (Vercel's `vercel telemetry disable`, future equivalents).
- `SKILL.md` Step 3 (c2) rewritten to describe generic `cli_commands[]` with examples for both GitHub and Vercel.
- Vendor matrix in `SKILL.md` extended with a Vercel row; frontmatter triggers add `"disable vercel telemetry"`, `"opt out of vercel"`, `"vercel privacy"`.

### Notes
- Next.js and Turborepo are explicitly **not** covered. Both are Vercel-owned but ship separate telemetry streams with documented opt-outs (`NEXT_TELEMETRY_DISABLED=1` / `next telemetry disable`; `TURBO_TELEMETRY_DISABLED=1` / `DO_NOT_TRACK=1` / `turbo telemetry disable`). Adding them requires separate vendor files — deferred until requested.
- Test count after this release: 357 assertions across 2 files (was 330 in 1.0.3); delta is the new `cli_commands` shape assertion running against every vendor plus all existing assertions running against the new `vercel.json`.

## [ai-cli-optout 1.0.3] — 2026-04-24

First public-ready release. Pre-publish blockers from 0.1.0 closed.

### Fixed
- **B1** — `vendors/phpstorm.json` `detect_paths` narrowed to PhpStorm-specific locations (`/Applications/PhpStorm.app` and `~/Applications/JetBrains Toolbox/Apps/PhpStorm`). The shared `~/Library/Application Support/JetBrains` ancestor — matched by every JetBrains IDE — is gone. A regression guard in `tests/vendor-schema.test.sh` blocks re-introduction of any shared / ancestor path in any vendor JSON.

### Added
- `plugins/ai-cli-optout/tests/` — 182 assertions across 2 files. `vendor-schema.test.sh` covers JSON validity, required-field shape, dotted-path edit keys, `manual_only` invariants (zero reachable auto-edit entries by construction), `shell_commands[]` platform-gating, and the B1 regression guard. `scripts.test.sh` smoke-tests both shipped bash scripts with isolated fake-HOME and `file://` fixtures — no network required.

## [ai-cli-optout 0.1.0] — 2026-04-24

Initial scaffold. Not publicly released.

### Added
- 11 vendor configs: Anthropic Claude Code, OpenAI Codex CLI, Google Gemini CLI, GitHub Copilot CLI + `gh`, Cursor (manual-only), Cursor CLI, Google Antigravity, VS Code, PhpStorm (manual-only), macOS system privacy, Windows system privacy.
- Platform-gated execution: dormant vendors render as copy-paste on the wrong OS, never auto-execute.
- Research script (`scripts/check_new_optouts.sh`) — diffs live vendor docs against baseline to surface newly documented env vars / settings keys.
- Persistent-files report (`scripts/report_persistent_files.sh`) — lists local state (session logs, caches, OAuth tokens) without deleting.
- Provider switches documented (Bedrock / Vertex / Foundry) — surfaced only on explicit user request; never auto-applied.
