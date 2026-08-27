# Changelog

All notable changes to `lazyants/claude-plugins` are documented here, with one exception: **`literary-translator` keeps its own changelog at [`plugins/literary-translator/CHANGELOG.md`](plugins/literary-translator/CHANGELOG.md)** — its releases after 1.1.0, and its Known limitations, live there, and the `[literary-translator 1.1.0]` entry below is frozen rather than continued. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is per-plugin, not repo-wide.

## [multi-profile-plugins 1.3.0] — 2026-08-27

### Changed

- **`code-limits` reports one row per ALLOWANCE, not one per window.** An account's five-hour and
  weekly figures are two readings of one quota; printing them as two rows repeated the profile and
  the pool on both and made an account compete with itself for the top of the page. They now sit
  side by side under a `5H` and a `WEEKLY` column on one line. The window columns are discovered
  from the data, so any other window the backend reports opens a column of its own rather than
  being folded into somebody else's. Two windows of equal duration under one pool -- which the
  Codex schema permits -- take two rows of the same column, because a cell holds one number and
  dropping the second would be silent.
- **The lifecycle sections are gone; the CELL carries its own caveat.** A window whose reset has
  passed reads `14h ago` and is dimmed beside whatever current cells share its row, so a previous
  window's percentage still never reads as a current one -- without a heading and a repeated
  caveat per block. The `[stale-after-reset]` token moves to one legend line, printed only when
  such a cell is on the page.
- **The table is ordered by candidate, alphabetically, not by consumption.** Ranking scattered
  one Codex home's pools down the page and printed the same directory name in four places, and a
  rank over a mix of current and expired windows was ordering numbers that are not comparable.
  A candidate's rows are now adjacent and its name is printed once for the group; position means
  nothing, and consumption is read off the figure and its hue where it always was.
- **The gauge is gone and a percentage no longer pads itself with a decimal it never measured.**
  Three window columns of bar-plus-number wrapped the table on a terminal that would otherwise
  hold it, and the bar never said anything the number beside it did not. `58.0%` now reads `58%`;
  a vendor that does report a fraction still prints it.

- **Only the Codex pool the CLI spends from is shown.** `rateLimitsByLimitId` enumerates pools
  a person at a terminal is not asking about -- `codex_bengalfox` (`GPT-5.3-Codex-Spark`) and
  `base_model_inference` (`gpt-reserve`) -- and the top-level `rateLimits` object already names
  the one that answers "how much can I still use here". Read from that pointer rather than a
  hardcoded id. When the map does not hold the pool that pointer names, the top-level object IS
  that pool and answers for it; every pool is kept only when there is no window anywhere else to
  show.
- **A cached window that has already reset is re-read live.** The file cannot answer once its
  window is over, and signing in does not refresh it: the CLI rewrites `.claude.json` at login
  but refreshes `cachedUsageUtilization` only after a request that carries usage back, so a
  freshly authenticated profile could sit at a three-day-old figure with nothing on the page to
  suggest signing in again was not the fix. Only that profile is re-read, only when its window
  is over; a retry that fails keeps the cached rows and states its reason as a note rather than
  a warning. `SOURCE` reads `api` for a refreshed row and a cache age for one that was not. A
  retry that PARTLY succeeds is merged per window: a live record wins unless it gapped and the
  cache holds that window, so one malformed entry in the response cannot throw away a window that
  refreshed cleanly and leave the stale figure this feature exists to replace.
- **Claude Code's model-scoped weekly pool is no longer shown.** It read `0%` on every account
  measured, and a `WEEKLY/<model>` column empty on every row but one cost more table width than
  the pool was worth. A profile that carries nothing else still shows it, rather than being
  reported as malformed over a payload that is perfectly well formed.
- **A Codex pool is named the way the backend names it.** The payload carries a `limitName`
  beside every pool and the report was discarding it, printing internal ids: `codex_bengalfox`
  and `base_model_inference` are the pools the vendor itself calls `GPT-5.3-Codex-Spark` and
  `gpt-reserve`. An unnamed pool keeps its id, and so do two pools that would end up sharing one
  label, because two rows reading the same name cannot be told apart.

### Fixed

- **The default Claude Code profile is read from `~/.claude.json`, where it actually lives.** The
  config path is `<CLAUDE_CONFIG_DIR or $HOME>/.claude.json`, and for the default profile the
  config dir falls back to `$HOME` -- so the file sits BESIDE `~/.claude`, which is that profile's
  data directory. Reading `~/.claude/.claude.json` found a stale copy left by an older release and
  reported `no-usage-cache` for an account whose weekly pool was at 87%, under a diagnostic that
  exits 0. That external config now also counts as the profile's DISCOVERY marker: an
  installation authenticating through the Keychain has neither in-directory marker, and the
  account was dropped before the ledger with no diagnostic at all.
- **A row is grouped by the candidate itself, not by its printed name.** Two candidates can share
  a basename -- `--claude-profile` is repeatable and takes paths under different parents -- and a
  name carrying a non-printable character is escaped to print, mapping two directories onto one
  string. Grouped by the printed name, two accounts merged into one row: one supplying the 5h
  figure, the other the weekly, under one account's freshness, exiting 0.
- **A Claude window that gaps stays on its own pool's row.** Its placement is now read off the
  raw entry, so a malformed `session` reports under `all` / `5H` instead of opening both a row
  and a column named after its index in the payload. The warning still names that index, which
  is what identifies the entry that could not be read.
- **`hasAvailableSubscription: false` no longer suppresses a profile that has usage to report.**
  The flag is now read only as the REASON a cache is absent. Accounts ship it beside a full,
  freshly fetched `limits` array -- one of them at 100% of its weekly pool -- so treating it as
  "not subscribed" hid exactly the numbers worth reading, and hid them cleanly.
- **`is_active: false` no longer withdraws a current Claude Code window.** Exactly one pool per
  account carries that flag and it marks whichever one is currently BINDING -- a five-hour window
  9% used and resetting in 17 minutes carried `is_active: false` because the weekly pool bound
  first. Rendered as "no current window" it was dimmed as not comparable to the cell beside it,
  taking its reset time with it. A reset time in the future now means the window is current, and
  nothing else does.
- **A window that gaps stays under the pool it was read from.** Its diagnostic used to name itself
  as its own allowance, which opened a row of its own and lost which pool had gone unread.

## [multi-profile-plugins 1.2.0] — 2026-08-27

### Added

- **A third skill, `code-limits`** — one report over every usage-limit pool this machine draws on:
  every discovered Claude Code profile under `~/.claude*` and every discovered Codex home under
  `~/.codex*`. Claude Code is read from its on-disk cache by default and `--live` fetches instead;
  Codex is read live over `codex app-server`'s read-only `account/rateLimits/read`. A window whose
  reset has already passed describes the PREVIOUS window, so it renders as `stale-after-reset` and
  is never presented as current usage.
- **`code-limit`, the report as one command on `PATH`** — installed by
  `skills/code-limits/scripts/install_code_limit.py` as three lines of POSIX `sh` whose only
  statement execs the shipped script, so there is no second copy to drift. The installer refuses a
  version-scoped plugin cache as its source (those directories name one version and are garbage
  collected), and treats any file at a managed name that is not byte-exactly its own shim —
  symlinks included — as somebody else's, left untouched unless `--force`. Legacy `claude-limit`
  and `claude-limits` are managed only where they already exist.
- **Codex reset vouchers are reported with the vendor's own title and expiry**, not just a count.
  A voucher lapses whether or not anyone looks at it, and the count alone never says when.

### Changed

- **The report is a table rather than a log.** One flat table of pools, most consumed first, with
  the profile as a column instead of a heading; rows that are not current usage sit below in their
  own sections, each headed by the reason, on columns shared with the table above. Caveats are
  stated once per section instead of once per row. Ordering is total, so two runs over the same
  data cannot disagree, and it is deliberately not a projected-exhaustion estimate — that would
  need a burn rate nothing here measures.
- **Colour**, via `--color=auto|always|never`. `auto` requires a terminal with `NO_COLOR` unset, so
  piping the report still yields plain text; stripping the escapes from a coloured run reproduces
  the uncoloured one byte for byte.

### Fixed

- **A vendor string can no longer forge a line in the report.** Strings from the Codex backend are
  now refused by Unicode category rather than by a control-character range: a voucher title
  carrying U+2028 LINE SEPARATOR added lines that looked like the report's own, on a run that
  stayed clean. The voucher title is escaped and quoted like every other printed vendor value.

## [multi-profile-plugins 1.1.0] — 2026-08-25

### Added

- **A second skill, `multi-profile-codex`, for Codex `CODEX_HOME` profiles (#756).** The same shape
  the plugin already documented for Claude Code recurs one CLI over: `CODEX_HOME` decides where
  files are read FROM, not what they SAY, so a profile seeded by copying the base `config.toml`
  inherits every absolute path in it and is sent back into the home it was copied from — including
  `CODEX_HOME` itself pinned inside an `[mcp_servers.*.env]` block, which re-enters the base home
  from the MCP subprocess while the CLI that spawned it stays correctly isolated. It ships as a
  sibling skill rather than as prose in the existing one because a skill's `description` is its load
  gate, and one description covering both CLIs fires wrongly in both directions.
- **`skills/multi-profile-codex/scripts/inspect_codex_profiles.py`** — a read-only, stdlib-only
  health check reporting two homes that share one `auth.json` **or merely the same account**
  (distinct files, one usage pool — the quiet failure), which dotted TOML key points into another
  profile's home, and which content stores resolve to a shared target. Its path matching is
  boundary-aware: a substring test false-positives every sibling (`~/.codex` inside `~/.codex2`) and
  a `startswith` test then misses the real pins, which sit mid-string inside `:`-joined paths and
  JSON blobs.

### Changed

- The plugin's `description` and keywords, in both `plugin.json` and `marketplace.json`, now cover
  config-profile isolation for both CLIs rather than Claude Code alone.

### Notes

- The version is cut here rather than in #756, which deliberately merged without a bump under this
  repo's batch-fold convention. Merge-to-`main` is the publish, so an unbumped `plugins/` edit makes
  one version label mean two payloads — and this one is not a doc tweak: a machine that installed
  1.0.0 does not have the second skill or its health check, while a fresh install from current
  `main` does.

## [enduser-handbook 1.18.3] — 2026-08-25

### Changed

- **Citation line numbers only, from the repo-wide `file.ext:NNN` gate (#579).** Every changed byte
  in this plugin sits inside a comment, a docstring, a `.d.mts` doc block, a reference page's prose
  or a test's explanatory comment: a citation whose target had drifted now names the range its
  sentence actually claims. `chapter-paths.mjs`, `build-identity.d.mts`, `chapter-paths.d.mts`,
  `capture-engines.md`, `state-variants.md`, the two publish-target pages and three test files are
  touched; no executable statement changed in any of them, so nothing a user of this plugin can
  observe differs from 1.18.2. The version is cut anyway, for the reason 1.18.2 was: merge-to-`main`
  is the publish here, and leaving `plugins/` content unbumped makes one version label mean two
  different payloads — the cached copy on a machine that already installed 1.18.2, and a fresh
  install resolving from current `main`.

## [ai-cli-optout 1.1.3] — 2026-08-24

### Changed

- **The Anthropic tradeoff notes say what the build actually gates, and name the build they were
  measured against.** The previous notes attributed the breakage to a shared "GrowthBook
  kill-switch with feature entitlements" and cited a specific model version as a casualty.
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` and `DISABLE_TELEMETRY` each disable the remote
  feature-flag FETCH, so every capability delivered behind a flag stops being offered and the
  client's built-in defaults are what remain. Measured against Claude Code 2.1.241 on 2026-08-24:
  that build gates `/remote-control`, `/feedback`, `/design-sync`, Projects and
  `--enable-live-preview` on either variable. A note that cites a model version goes stale on the
  next model release while the gate it describes does not, so the notes now name capabilities
  instead.
- **Either variable is sufficient on its own**, isolated by bisection 2026-04-24 — settling a
  question an earlier revision of the note left open as "NOT TESTED".
- **`DO_NOT_TRACK` and `DISABLE_GROWTHBOOK` are named as being in the same set** and must not be
  shipped as opt-outs for the same reason.
- Upstream `anthropics/claude-code#34178` is recorded as closed 2026-04-12 without the behaviour
  changing; it still reproduces. `CONTRIBUTING.md` carries the authoring rule this produced.

## [enduser-handbook 1.18.2] — 2026-08-24

### Changed

- **`tests/reference-assets.test.sh` only.** No shipped skill, adapter or documentation byte
  changed, so nothing a user of this plugin can observe is different from 1.18.1. The version is
  cut anyway because merge-to-`main` is the publish here: leaving `plugins/` content unbumped
  makes one version label mean two different payloads — the cached copy on a machine that already
  installed 1.18.1, and a fresh install resolving from current `main`.

## [enduser-handbook 1.18.1] — 2026-08-18

Four sentences cited issues that had just been closed as decisions, describing them as work still
tracked. Each now states the decision instead.

### Fixed

- **Shipped prose pointed at closed issues as open work (#380, #341, #577).** The 2026-08-18 tracker
  triage closed nine items, three of them as decisions rather than as work delivered.
  `references/publish-targets/README.md`'s list of what the extension contract cannot yet require
  cited the hardcoded provenance path as a gap awaiting a key (#380); it now says the record is a
  private audit trail for revalidation rather than a published fact about the document, so no
  configurable provenance directory ships and nothing consumes one.
  `tests/reference-assets.test.sh` claimed semantic inversion by surrounding context was bounded "by
  review and by #341's structure-aware reader" — a bound that does not exist, the reader having been
  weighed and declined as disproportionate for a doc-pin harness — and called the residual class
  "filed as its own work"; it is now bounded by review alone, and the class is stated as open by
  decision, with #341 named as where the evidence and the two alternatives are kept.
  `tests/export-parity-lib.mjs` said the census-only case was "tracked in #577 with the linking
  work"; the parity suite's own *every function export's declared arity was actually read, none
  skipped* check is what goes red when one reaches the loop unread, and that check is now named as
  the tracker. **The failure mode is silent in both directions:** a reader who follows a stale
  citation learns only that the sentence is out of date, and a reader who does not follow it acts on
  a plan nobody intends to carry out. One pin was added, binding the provenance sentence to its
  decision under `### What this contract cannot yet require`, because that list is exactly where the
  key would be filed again. Verified red by mutation before it was accepted. No behaviour, no export
  and no adapter rule changed; the reference-assets suite gains exactly one check and stays fully
  green. The delta is the portable figure, not the total: the profile-schema validation assertion at
  `tests/reference-assets.test.sh:3106` is emitted only where Ruby can load `yaml` and `json`, so an
  environment without it counts one lower at both ends — measured, by shadowing `ruby`, not assumed.
- **Dated release copy is deliberately not swept.** Earlier entries here, and the version-tagged
  notes in the root `README.md`, carry their own citations of issues that have since closed
  (`#246`, `#472`, `#110` and others) — each was true when it was published, and this changelog
  corrects forward rather than rewriting an entry in place, the one exception being the explicit
  *Superseded* annotation the 1.14.0 entry already carries. What is corrected above is the prose a
  reader executes or maintains today: the shipped skill reference and the test suites' own
  comments. A later sweep will find the historical citations; they are left as written on purpose.

## [enduser-handbook 1.18.0] — 2026-08-18

A flat index row is read back before it is written, so a title that cannot survive its own row's
link parse halts the run instead of growing the index without limit.

Closes #574.

### Fixed

- **The flat "line absent ⇒ append" branch had no read-back of its own (#574).** A manifest `title`
  whose text keeps its own row's link destination from parsing makes step 0 (`locateChapterLine`)
  report the chapter absent on *every* run, so the branch appends an identical row each publish
  while the manifest never changes: measured against `935d9e5`, four publishes leave four rows, in
  both link modes. 1.17.0's `findStaleChapterRows` cannot reach them — every appended row carries
  the link this run would write, which is exactly the test that tells this run's own row from a
  leftover, so widening the scan would break the unchanged-manifest case it exists to serve. Both
  adapters now compose the row, read that one line back through the very `locateChapterLine` call
  their own step 0 makes (with the same target and the same options), and refuse to write a row it
  cannot find, halting with the chapter's title named as the thing to change. **This is a prose
  fix: no executable behaviour and no export under `assets/lib/` changed — the only edit there
  is a comment on `findStaleChapterRows`.**

### Changed

- **The publish-target extension contract binds the obligation, not the spelling.**
  `references/publish-targets/README.md` previously told a third adapter's author that the shipped
  flat branch has no membership check; it now states that refusing an unreadable row before writing
  it is the append branch's own obligation, discharged through *that* target's step-0 reader —
  because the property to hold is "the reader this file's next run uses can find the row this run
  wrote", and only that reader can answer it.
- **The alternative was named and declined, in the contract and in the adapters.** A membership
  check that recognises the unreadable row — the flat analogue of `wireNestedListChapter`'s
  `present` outcome, which #574 proposed — bounds the count at one but leaves a permanently
  unreadable row in a managed file that every later run, migration and scan has to special-case.
  Refusing is what ships.

### Known limitations

- **Which titles are refused is not summarizable by a character rule, and neither mode is
  uniformly the stricter one.** Measured: `Items]v1` and `A [b] c` are refused in both modes;
  `Items]` and `Items [beta]` are refused in path mode and accepted under wikilinks; `Items \] esc`
  is accepted in path mode and refused under wikilinks. The adapters therefore tell the reader to
  run the helper rather than predict it, and a verdict measured in one mode says nothing about the
  other.
- **Rows already in an operator's index from before this release are not retro-reported.**
  `findStaleChapterRows` still skips them while their title stays fixed, by the same construction
  as above. The refusal's halt is what leads the operator to change the title, after which every
  one of those rows is named on the next run — measured in both link modes.

## [enduser-handbook 1.17.0] — 2026-08-16

Backfilled 2026-08-18: 1.17.0 shipped (`f6a31ff`, merged `935d9e5`) with its release notes written
into `README.md` only, so this file's newest entry was 1.16.0 while the plugin was at 1.17.0.
Relocated 2026-08-24: the root `README.md` no longer carries per-release notes for this plugin, so
the full notes live here, moved rather than re-summarised: the bodies are byte-for-byte what the README carried, and only the bullet headings were re-annotated from a version tag to the commit and issue each shipped under.

- **The publish-target extension contract says what a third adapter owes revalidation, addresses and completion (`7a66a1d`, #357)** — `references/publish-targets/README.md` is the file a maintainer follows to add a target beyond the two that ship, and it asked which link *syntax* a target uses while never mentioning W6 at all, so a correct establishment-time adapter could leave the migration path undefined and still report a publish successful without verifying anything. It now carries three sections, derived by reading both shipped adapters and `chapter-paths.mjs` rather than a release diff. **Address derivation:** one formula per link class, each naming its origin, its operand normalization, its output and whether it is depth-sensitive — the shipped pair's own divergence is the evidence, since index→chapter is measured from the index file's directory by `static-md` and from the vault root by `obsidian-vault` under wikilinks, while chapter→asset is the same full-target join in both and should be mirrored rather than re-derived. **The completion gate:** shape fixed (ordered, halt on first failure, run after the write *and* after the wiring), item list not, with two items stated as target-specific because copying either shipped adapter is actively wrong — the two take opposite containment positions and their navigability floors differ for reasons that come from the renderer. Its membership item re-reads the index, because the wiring step returning success is not evidence the chapter is listed. **Revalidation and W6:** what a group change means in the target's own object model per delta kind, which changes an adapter may perform and which it must halt on, the halt's three-part structure, and the post-migration link scan. The contract also states plainly what no per-adapter extension point can express today — `manualMigrationChecklist` emits a fixed set of twelve fact kinds with no target parameter and no registry, `renderManualMigrationHalt` has no formatter hook, `migrationRecordPath` hardcodes the `.provenance` path (#380) — so a third adapter's revalidation contract is prose-only, and the document says so rather than leaving the next author to find out (#357).
- **The index rows a title edit leaves behind are named, not accumulated silently (`8a4c650`)** — the membership guard added in v1.11.0 compares the *complete* current link string, so it bounds re-runs of an unchanged manifest entry and nothing more. Edit a chapter title in a way that breaks its own row's link-target parse — an unescaped `]` is the measured case — and the guard is handed a string it has never seen, correctly finds no match, and inserts: one unrecognizable row per distinct title, without limit, in both link modes (the issue scoped it to path mode; `[[handbook/admin/items|Items]v1]]` accumulates identically). New export `findStaleChapterRows` is a sibling of `locateChapterLine`, not a new writer outcome — the locator answers which rows *resolve* to the chapter, this answers which rows carry the same destination as a whole link token and still fail the parse, and the two sets are disjoint by construction, so `verifyNonHeadingPlacement`'s rule-4 accept-list needs no new member. Both shipped adapters call it at step 0 and halt naming every row with its line number and its raw text; neither deletes anything, because the index format carries no row-to-chapter ownership record and nothing distinguishes a row this tool wrote from one an operator hand-authored around an unparseable link. **Every one of the five admitted review findings on this change was in an *exemption*** — the logic deciding "this row is fine, do not name it" — and they are recorded because the pattern outlived each fix: own-row by containment (unsound against a title ending in the next title's opening bracket, measured at three accumulated rows and none reported); own-row by exact comparison, wrong on a CRLF index, where the writer reads `\r`-free logical lines; the same comparison wrong on any row shape other than a bullet, which halted on a numbered or table row the adapter itself had just appended and would append again — a false positive with *no edit that clears it*; a link-reference-definition exemption that matched a definition-looking *prefix*, so a title of `Old]: x y` hid its own leftover; and the destination match searching the raw target instead of the module's own `foldTargetForMatch`, which left one of the two accepted wikilink spellings unnamed. The resolution is an asymmetry rather than a better predicate: exact comparison on the `-`/`*`/`+` bullet the writer itself emits, containment everywhere else, because the two populations have opposite safe failure modes and an over-report is only cheap while it stays clearable. Two boundaries are stated rather than implied: a flat entry whose target-breaking title is never edited still appends one row per publish and this scan is blind to it by construction (#574), and the halt is what makes every exemption load-bearing in the first place (#585) (#349).

## [enduser-handbook 1.16.0] — 2026-08-16

What a `<canvas>` paints is photographed and unscannable, so the region that holds one is now
refused rather than quietly captured.

Closes #565.

This is also the release that carries the `assets/lib` declaration/runtime parity gate (#420, #339),
which merged without a version bump for the release sequencer to fold in. Its lines are below under
*Added*, *Changed* and *Known limitations*, so this entry describes its own artifact rather than only
the lane that cut it.

### Fixed

- **`maskAndAssert`'s leak scan could not see a `<canvas>`, and no carve-out list named it (#565).**
  The scan corpus is DOM text nodes, form-control values and input/textarea placeholders. What a
  `<canvas>` paints contributes to none of them — it is a bitmap — while `page.screenshot`
  composites its pixels like everything else. (Its *fallback* children are ordinary text nodes and
  are scanned; they are simply not what the canvas shows.) Canvas-rendered PII was therefore photographed,
  unmaskable and unscannable, and silent in every direction at once: no element inside it for
  `selectors` to match, nothing of what was painted for `patterns` to fire on, and a coverage assert
  satisfied by whatever *was* listed. The author could not even mask it by listing the `<canvas>`,
  because that sets `textContent` and a canvas's children are fallback content that paints nothing.
  The concrete shapes are a document preview rendered to a canvas (PDF.js renders every page that
  way), a canvas-mode data grid, and a signature pad — where a whole document body, not a bounded
  label, rides into the shot.

  This is #472's silent-half shape one element class over, so it takes #472's answer rather than a
  new mechanism: the count is taken in the same browser `evaluate` that already counts nested
  browsing contexts, and a region containing a `<canvas>` **throws** unless the caller passes
  `allowUnscannedCanvas: true`. The count reuses `queryDeep`, so a canvas inside an open shadow root
  is covered identically to the mask and the scan, and keeps the `root.matches()` term so a
  canvas-scoped locator is counted too — `querySelectorAll` returns descendants only. A canvas the
  caller *did* list in `selectors` still counts: the mask tag removes it from the scan without
  changing pixels no mask could overwrite. The refusal is checked **before** the coverage assert,
  for the reason the frame refusal is — past it, a drifted selector count misreports the cause as
  selector drift.

  The remedies differ from the framed case and the docs now say so: there is no "scan it yourself
  per canvas", because a canvas hosts no document — no second corpus to run the mask and the scan
  over. The refusal names `<canvas>` only; `<img>`/`<video>` pixels are photographed and unscanned
  as well and stay the human eyeball-the-frame step's job, stated rather than left implied.

- **Three write sites, and a claim that stopped being true when the second gate landed (#565).** The
  carve-out is stated in `references/capture-spec-helpers.md`, in `maskAndAssert`'s own
  `SCAN CARVE-OUT` docblock, and in `references/capture-safety.md`'s masking rules; all three now
  name the canvas, and the contract doc's count goes from **Five** things the scan does not cover to
  **Six**. "That is why this ONE carve-out is enforced" was true with one enforced carve-out and
  false with two, and is retired in both copies that carried it.

- **"A canvas has no text" was a false universal, written three times (#565).** A `<canvas>`'s
  **fallback** children are ordinary text nodes and the TreeWalker *does* collect them; what no pass
  can reach is what the canvas **paints**. Measured in a real browser rather than reasoned about:
  run over a canvas that both paints one string and carries another as fallback, the collected
  corpus contains the fallback string and never the painted one. Three review rounds each found one instance of this
  universal and each fix left another standing, because the wordings differ per site and one file
  ended up stating the correction *and* the universal at once — which no positive assertion can
  catch. Every canvas sentence is now scoped to the painted pixels.

### Added

- **`maskAndAssert` has executable coverage for the first time (#565).** Every guarantee it makes
  was previously pinned by grep only, and a grep proves text is present, never that it works — which
  cannot express #565's actual claim, a two-sided mutation (red on a region containing a canvas,
  green on a legitimate capture). `tests/mask-and-assert.test.mjs` drives the **real** shipped
  helper against a DOM stub — a stub of the *engine*, in the shape
  `capture-guard-redirect-wiring.fixture.mjs` already established — over 14 scenarios. Five
  mutations of the shipped refusal were each watched going red for the right reason, and two
  scenarios exist so that a stub silently producing empty results could not yield a false green. The
  stub throws on any selector form it does not implement rather than answering "no match", and what
  it does not prove is enumerated in its own header.

- **A class gate over every write site, including the release copy (#565).** Each retired wording of
  the false universal is pinned absent in *every* one of the five files, so a recurrence in a site
  that did not previously have it still goes red; the `.ts` site uses the code-aware absence helper,
  since the markdown-only one would have been green by construction against a JSDoc wrap. The first
  version of the gate covered only the three plugin documents that had already gone wrong — and these
  very release notes then reintroduced the universal in the CHANGELOG entry and the README section,
  where nothing was watching, which is the failure mode the gate exists for. Both root documents are
  in scope, and both scoped sentences are also pinned *present* so deleting them cannot satisfy the
  absence pins by silence; the CHANGELOG pin is bound to this heading, so relocating the sentence to
  another entry fails too. All 30 class-gate pins were watched going red by reintroducing their
  wording — 15 in the round that introduced the gate, 15 in the round that extended it to the release
  copy — as were the two positive pins and the heading binding. One reintroduction was split across a
  hard wrap, which is what proves the absence helper's wrap tolerance; splitting every one of them
  would re-prove the helper rather than the pin. It gates the wordings that have actually appeared —
  it moves a known recurrence from review-caught to CI-caught, and does not make the class
  unwriteable. One site it structurally cannot cover is
  `reference-assets.test.sh` itself, where the retired wordings live as needles; that is stated at
  the block rather than left as a silent hole.

- `tests/export-parity-lib.mjs` + `tests/declaration-parity.test.mjs` — a compile-free
  declaration/runtime parity gate over `assets/lib` (#420, #339). Every module's `.d.mts` is compared
  against the REAL import namespace of its `.mjs`: names in both directions, declared arity as a range
  against `Function.prototype.length`, and orphan modules on either side including a stale
  declaration-only module that a `*.mjs` walk cannot see. The declaration side is read by a
  statement-aware extractor rather than a regex — #339 records five consecutive rounds of measured
  false-greens from regex designs — and any construct it cannot read fails the gate instead of being
  skipped. Verified by exhaustive mutation over the shipped tree: 89/89 renamed declarations and 72/72
  emptied signatures caught.

### Changed

- `tests/reference-assets.test.sh`: the six per-name `chapter-paths.d.mts` needles from #330 and the
  per-release "one needle per added declaration" recount are retired in favour of the general gate,
  which enumerates every declaration in every module rather than the ones a release remembered to pin.
  A new census block prints the module/export/arity counts, because the `node --test` block discards
  stdout and a parity run that enumerated nothing is otherwise indistinguishable from a clean one.

### Known limitations

- Declaration TYPE correctness remains unchecked: a declared type that is wrong while the name and the
  parameter count are both right is invisible to the parity gate. Closing that needs a TypeScript
  toolchain this repository does not have; tracked in #573.
- Four declaration kinds have a declared arity the reader does not reach — a class (`constructor`
  member), a specifier, a star re-export and a default expression. Each is named in the gate's own
  census rather than skipped, so the count of unread arities is pinned and cannot grow in silence;
  reading them is tracked in #577. No shipped `.d.mts` uses any of these forms today.
- The `<canvas>` refusal is a refusal, not a scan: `allowUnscannedCanvas: true` returns the caller to
  the eyeball-the-frame step, and `<img>`/`<video>` pixels were never covered and still are not.

`tests/reference-assets.test.sh` gains **+52 assertions, none removed**, plus one more wherever `node`
is on PATH — 16 carve-out disclosure pins, 2 correction pins, 2 release-copy pins, 30 class-gate
pins, 2 seam pins, and the node-suite runner that is the +1. Reconciled by diffing the two
check-name sets rather than by arithmetic on totals. Absolute totals are deliberately not quoted:
several blocks here are gated on an optional local tool (`node`, `ruby`, `esbuild`), so an endpoint
is a fact about the machine that measured it. The delta is the more stable figure but not an
invariant either — measured +52/0 under stock tools with none of the three present, and +53/0 with
node and ruby available, the difference being the node-gated runner named above and nothing else. The
plugin ships 20 `node:test` suites, 1395 tests, wherever node is available to run them.

## [enduser-handbook 1.15.0] — 2026-08-16

The two publish-target adapter contracts: a zero label match is not licence to create a container,
and the outcome that declines to conclude now says who is left to look.

Closes #476 and #563 — items 5 and 6 land here, which completes that eight-item batch — and carries
out the requirement #553 left behind when it was closed as a duplicate of #476.

### Fixed

- **A zero container match licensed an unconditional create (#476).** Both publish adapters turned
  `findContainer`'s `zero` outcome into an unconditional `## <group_title>` write, so a heading that
  merely *rendered* as the group title got a second, render-identical section appended beside it in
  an index the tool does not own. The run reported success and never self-corrected, because the
  next run matched the heading it had written itself. Both adapters now re-read the container
  headings first and halt when any of them plausibly renders as `group_title`, naming the group
  title and every candidate. The halt prescribes no repair: whether two headings are one section
  spelled two ways or two sections that render alike is a question about what the sections mean,
  which a spelling comparison cannot reach, so it reports and hands the file back the way the
  multiple-candidate halt beside it always has. The fold covers ornament only — a leading or
  trailing run carrying no letters and no digits — and deliberately not a content-bearing suffix:
  `Reports (2024)` and `Reports (2025)` are two sections an operator maintains side by side, and
  folding the parenthetical would halt a correct handbook with no edit that clears it.
- **An invisible character in a container heading forked the index silently (#476, delivering
  #553).** A second compare now runs with the unsafe invisible characters removed from both sides —
  exactly the curated set `isPlainLabel` already refuses, which spares U+200C and U+200D by
  construction because those are required inside ordinary Persian and Hindi words — and names the
  offending code point and its offset, since nothing else makes it visible to the operator. Both
  compares are detect-only: neither selects a container to write into, so neither can mis-target.
- **The Related block's index member had a category and no target form (#261, via #563 item 6).**
  `obsidian-vault.md` now names the handbook index as a third legitimate member — the
  `{{publish.index_file}}` that `assets/chapter-template.md`'s `{{handbook_index_link}}` placeholder
  resolves to — and gives its target form in both `publish.wikilinks` modes. Legitimate, not
  required: the ≥2 floor counts links rather than member types, so two sibling chapters still clear
  it.

### Changed

- **The `unverifiable` placement outcome now says who looks (#345, via #563 item 5).** #345 asked
  for an explicit confirmation step and was answered no on its own evidence. Both adapters instead
  tell the consuming agent to read the index region and confirm by eye, which it can do because it
  already holds the index lines, and the neighbouring claim that nothing further verifies placement
  is qualified to *automatically*. That is the agent's own read, not a prompt to the operator, so
  the outcome does not become a confirmation step — and W6's migration checklist keeps its own.

### Note on the 1.14.0 label

The commit delivering the above merged to `main` 2m14s after 1.14.0 and, by its own design, bumped
nothing — that release lands centrally, here. **From that merge until this bump**, the artifact
labelled 1.14.0 on `main` contained items 5 and 6 while its own entry below said it did not. That
entry is left standing as the record of what its release shipped; this paragraph is the correction.

Two populations of installed copy differ, and only one of them has anything to repair. A copy
installed **in the 2m14s before that merge** reports 1.14.0 and holds the older adapter docs;
`claude plugin update` repairs it, because a bump is what makes the update copy bytes at all — an
unbumped edit returns `up_to_date` and copies zero bytes, which is why the wrong label would
otherwise have stuck indefinitely. A copy installed **after that merge** already holds the newer
docs under the 1.14.0 label, so only the label was ever wrong. Measured on the author's machine:
`obsidian-vault.md` is 1107 lines in the tree 1.14.0 was cut from and 1248 lines after that merge,
and four independently-installed caches *named* 1.14.0 hold 1248 — an installed copy's version
label names which release it was fetched under, never which bytes it contains.

## [enduser-handbook 1.14.0] — 2026-08-16

Two parallel tracks over one theme: an assertion that never checked what it claimed to, and a batch
of sentences that were false about the shipped artifact.

Closes #568. Also ships #472, #473, #474, #477 and #560, each closed on merge, and delivers #563
items 1–4, 7 and 8; items 5 and 6 (the publish adapters' `unverifiable` branch and the Related-block
category) are untouched here and #563 stays open for them.

> **Superseded on the last sentence.** Items 5 and 6 merged to `main` 2m14s after this release,
> under this version label and with no bump of its own; 1.15.0 released them and closed #563. See
> "Note on the 1.14.0 label" in that entry. The sentence above is accurate about what *this release*
> shipped and is kept for that reason, not about what the 1.14.0 label named after those 2m14s.

### Fixed

- **Page identity never pinned the labels a chapter quotes (#477).** Step 4 required only that the
  narrated element be asserted **visible**. Visibility is a predicate about a box, not about text, so
  a spec anchored on a testid or a CSS selector satisfied it while pinning nothing the reader will
  read. Rename a column header: route, heading, steps and triggers unchanged, the delta classifies as
  an accepted diff, the re-capture writes a PNG showing the new string, and the published step quotes
  a label absent from its own screenshot — every gate green. Step 4 now requires the assertion be
  keyed to the exact quoted text **and** scoped narrowly enough to identify the one instance the step
  narrates: a page-wide exact match is satisfied by the same string anywhere on the page, and because
  the shipped helpers take the FIRST match, scoping to the captured region is not yet identity when
  the same string repeats inside it. The remedy is the smallest distinguishing container; a count of
  exact matches is a fallback sound only over a fixed row set. The label class that earns no
  coverage-matrix row is keyed on **inertness**, not element type — a sortable column header, or a
  `<label>` that focuses its control, is an interactive trigger and earns a row like any other, and
  the assertion is owed either way, since a matrix row records a label rather than asserting one.
  `anti-fabrication.md`'s self-audit carries the reciprocal obligation, with disclosure prose as the
  stated exception; that exception runs through the **label**, not the step, so a disclosure that
  embeds a captured open state still pins every label that screenshot documents. No option was added
  to `IdentityOptions` and no runtime behaviour changed — the exact-matched primitive already shipped.
- **The shipped capture skeleton contradicted the contract it teaches (#568).** `capture.example.spec.ts`'s
  overview step asserted `getByRole('main')` visibility and pinned no text — the file every adopter
  copies as their starting point. It now demonstrates the pin (an exact-text, role-scoped assertion on
  the narrated column header) and says why the container assertion above it is not enough.
- **`maskAndAssert` refuses a region that frames another document (#472).** Neither the mask nor the
  leak scan crosses a document boundary while `page.screenshot` composites the child document's
  pixels, so unlisted PII inside an `<iframe>` had nothing to mask, no text node to collect and no
  pattern to match — a green run with the value in the published PNG. The carve-out is now enforced
  rather than only disclosed, behind a new `MaskOptions.allowUnscannedFrames` opt-out.
- **Both capture skeletons stopped letting `finally` replace the primary failure (#473).** A bare
  `finally { await guard.assertNoDangerousHits(); await context.close(); }` discards the body's error
  and skips the close when the guard ledger is non-empty — permanent for an adopter whose REST POST
  reads fail closed into that ledger.
- **The `<iframe>` carve-out justified its selector with a mechanism that does not exist (#567).**
  Three defects `#472`'s own round-3 rewrite introduced into `capture-spec-helpers.md`: a duplicated
  word straddling the hard wrap (invisible to a line-based `hasnt`, so the pin added for it is
  wrap-tolerant), a sentence contradicted three sentences later, and — the one that matters in a file
  an LLM executes — a false technical claim. The unqualified `<object>` selector was defended by
  saying an `object[data]` form would miss a `data` assigned by script after parse. It would not: a
  CSS attribute selector is evaluated against the live DOM at `querySelectorAll` time and
  `HTMLObjectElement.data` reflects the content attribute. The design choice is unchanged and still
  right — over-refusal is the correct direction for a PII gate — but it is right for the reason now
  stated, that the count is taken when the helper runs and the pixels are taken later.
- **Seven documentation claims measured wrong (#560, #563 items 1–2, #344 via item 7).** The
  example's `denyPatterns` were a raw substring match where the built-in verb block is token-exact,
  so they blocked ordinary reads (a verb in the host, the fragment or a query value) while still
  missing encoded and query-string spellings the built-in catches — the two sets overlap, neither is
  a superset, and the shipped comment beside them says so precisely. Three admission claims in the
  guard contract were wrong against the shipped `decideRoute`. Two cross-file pointers in the test
  suite cited line ranges that had both rotted — fixed by dropping the range and keeping the
  greppable identifier, since renumbering only reproduces the defect on the next insertion above it.
  And `profile-validation.md` claimed a false-reject-free design for mechanism B was "tracked as a
  follow-up"; nothing tracks it, so the text states the resolution instead — a documented deferred
  residual.

### Changed

- **`capture.locale` pins the process locale, never the app's UI language (#474).** This is now a
  normative requirement rather than a clarification, and it carries an adopter action:
  `container-isolation.md` requires the capture spec to pass the browser-context locale as well —
  pin **both** — and both shipped skeletons now set `locale` on their `newContext` call. An existing
  spec that relies on the process locale alone has an edit to make.
- **Whole-suite `capture.command` beats the delta rule (#478, #563 item 3).** Revalidation step 5 told
  the consumer to re-capture only the deltas while `container-isolation.md` requires the profile's
  command to run exactly as written, and every shipped representative command is a whole-suite
  invocation — no third option existed. The step now scopes only re-**authoring** to the delta set and
  hands the re-shot scope to the verbatim command; provenance is re-recorded for every chapter the run
  actually re-shot. Three claims are deliberately narrower than the original ticket's wording, which
  is falsifiable against the shipped code: `record_stale` applies to a record that already exists (a
  chapter with none reports `record_absent`), running the provenance substep is not a promise the
  record gets rewritten, and `capture_failed` is one run-level warning rather than a halt.
- **A one-time pre-1.6.0 embed sweep (#246, #563 item 4).** Read each chapter's embeds once, resolve
  each target on disk, rewrite only the ones that do not resolve, and leave a resolving embed byte for
  byte alone. This replaces the filesystem-owning repair module the original ticket asked for.
- **The root changelog states its one exception (#147, #563 item 8).** `literary-translator` keeps its
  own changelog under `plugins/literary-translator/`; this file is frozen for it at the 1.1.0 entry.
  The absence of that pointer is what manufactured the original backfill request, and the README
  plugin table now carries the same note.

## [enduser-handbook 1.13.0] — 2026-08-15

Two independent tracks, both about a run that completes while telling the operator something untrue.
Three defects in the index-wiring path, each able to put a wrong row into a real handbook index
without saying so (#337, #350, #351); and the capture guard, which silently never saw a redirect hop
and whose contract document promised guarantees the policy does not enforce (#471). Closes #337,
#350, #351, #471.

### Fixed
- **The locator and the writer now apply ONE frontmatter rule** (`indexView`, via a helper shared
  with the writer's own `prepareIndexLines`) — #337. Only the writer blanked a closed leading
  frontmatter block before sanitizing, so a single backtick inside a YAML scalar opened an
  inline-code span that erased the rest of the document *for the locator alone*: a headings-form
  index read as absent AND non-heading, the run routed it into the nested-list writer, and that
  writer appended a bullet-shaped container plus a duplicate row on every publish. `findContainer`
  is repaired by the same change — both callers share `classifyIndexForm`. Scope is a CLOSED block
  only; an unclosed leading `---` remains a YAML document-start marker and exempts nothing.
- **Container labels are compared under Unicode NFC** — #351. A decomposed and a precomposed
  spelling of one label are one container, at every comparison site: the nested-list container
  match, `findContainer`'s heading filter, `containerTitleMatches`, `validateGroups` gates 5 and 6,
  `verifyNonHeadingPlacement` rule 5, and `classifyEntryDelta`'s title comparison.
- **A `misplaced` verdict is no longer discarded as `unverifiable`** — #350. The writer's
  `unwritable`/`group_title` refusal says the file is a readable list and the label is plain; only
  the container line it would emit is unsafe, which says nothing about where the existing row sits.
  That outcome now falls through to the container comparison, and can conclude only `misplaced` —
  never a false `ok`, since the refusal is reachable solely on the create branch.
- **The capture guard now audits redirect hops** — #471. `context.route` is never called for a
  request the browser issues itself to follow a 3xx `Location`, measured against real chromium on
  playwright-core 1.61.1 and 1.62.1: a `GET /reports/monthly` → 302 → `/orders/42/finalize` chain
  reached the server in full while the interception handler saw only the first request, so the hop
  was never classified, never blocked, never recorded, and `assertNoDangerousHits()` stayed green. A
  second, audit-only channel on `context.on('request')` now re-classifies every hop through the same
  `decideRoute` on the *hop's own* method, URL and body (307/308 preserve both, so a body-shaped
  `denyPattern` reaches a hop exactly as it reaches a fresh request; 301/302/303 may downgrade a POST
  to a GET), exposes the whole chain via `redirectHops()`, and pushes a would-be-blocked hop into the
  dangerous ledger — except one the project's own `classifyRequest` calls `'benign'`, which is
  reported in the chain only. **This is DETECTION, not prevention**: the browser has already sent the
  hop, so a failure naming a `redirect-hop:` reason means a live request fired, not that one was
  stopped. Preventing it would need `route.fetch()` with manual redirect following, which this
  release does not do.

### Added
- **An invisible character in a label is refused rather than normalized** (#351) — no normalization
  merges two labels differing by a zero-width space, and choosing one would be a guess.
  `isPlainLabel` rejects a 20-member set (the complete bidi-control family plus ZWSP, soft hyphen,
  word joiner, invisible operators and BOM), and `validateGroups` refuses such a `group_title` at
  authoring time, naming the code point and its offset — "delete the invisible character" being the
  one instruction an operator cannot execute for a character they cannot see.

### Changed
- **The citation audit keys on identity, not file offset** (#342, merged separately as a test-only
  change with no release entry of its own — 1.13.0 is the version that ships it). `EXPECTED_UNRESOLVED`
  entries are now `file + section + sectionNth + quoted text + direction + ordinal`, so prose moving
  around a citation no longer reddens the suite, and a documented regenerate-and-paste command plus a
  test asserting the shipped block equals what that command emits replace hand-transcription. This
  release exercises it: the doc edits above removed five citations (`EXPECTED_TOTAL_CITATIONS` 94 →
  89), three of them unresolved — and the regenerated allowlist differs by exactly those three and
  nothing else. The other two were resolved cross-references carried by the "tracked separately as
  #337" clause this release retired, so they never sat in the allowlist to begin with.
- The **verified class** for present-line placement verification widened, and its sentence — quoted
  verbatim in both publish adapters and `revalidation.md` — now reads: files for which the
  fixed-probe writer call returns `kind === 'inserted'`, `kind === 'present'`, or `kind ===
  'unwritable'` with `field === 'group_title'`, and which hold exactly one selected-target match (a
  row inside a closed leading frontmatter block is not a match at all). The 1.11.0 entry below
  deliberately keeps the narrower sentence that was true then; the suite pins each separately.
- **The capture-spec contract stops promising what the guard cannot do** — #470, #471, #472.
  `references/capture-spec-helpers.md` is a mandatory pre-read, so each overstatement misled every
  capture author downstream. Corrected against the implementation: "intercepts every request" →
  classifies every request *the engine surfaces to its interception handler*; "everything else fails
  closed" → fail-closed covers *non-GET/HEAD only*, and the GET/HEAD allow never examines the origin;
  "a destructive GET still fails closed" → true only when the verb is one of a fixed 16 in
  `DANGEROUS_VERB_SET` (13 English, 3 German — a GET to `/orders/42/confirm`, `/reports/publish` or
  `/users/7/impersonate` is admitted, each verified against the real `decideRoute`); and the general
  GET/HEAD allow is reached only past the deny, benign, SSE and beacon blocks, so an event-source GET
  the predicate did not admit is blocked rather than passed. The scan carve-out now names the
  same-origin `<iframe>` as a fifth uncovered item.

### Migration
- An index that already accumulated BOTH spellings of one container label now halts as `multiple`
  instead of quietly feeding two containers. Merge them by hand; the two lines are pixel-identical,
  so locate them with `python3 -c "import sys,unicodedata; [print(i+1, repr(l)) for i,l in
  enumerate(open(sys.argv[1])) if l != unicodedata.normalize('NFC', l)]" INDEX.md`.
- A `group_title` carrying an invisible character now fails manifest validation.
- A chapter row that exists only inside a closed leading frontmatter block is no longer reported
  present, so the run wires a real row in the body; a file that used to halt on "two lines match the
  target" because one match sat in frontmatter now proceeds on the real row alone.

### Known limitations
- The headings branch does not refuse an invisible-character container heading (#553), and a
  `group_title` carrying a line break passes every gate yet never resolves (#554). Both are
  pre-existing, both are now stated in the two adapters rather than left to be rediscovered.
- ZWNJ/ZWJ stay legal: they are required inside ordinary words in Persian, Hindi and other scripts,
  so refusing them would lock a correctly-spelled title out of automation.
- **#470 and #472 remain open.** This release documents both gaps precisely; it fixes neither. The
  GET/HEAD allow is still origin-blind and braked only by the fixed 16-verb list, and the automated
  scan still does not reach the content of a same-origin `<iframe>`.
- **Redirect-hop coverage is after-the-fact.** A dangerous hop fails the run but has already reached
  the server.
- **Dedicated-Worker hop coverage is unestablished.** No evidence of a gap; establishing it needs a
  runtime fixture rather than static reading.
- The contract doc **re-derives the guard's branch order in several paragraphs**, which is why three
  review rounds each found a different paraphrase overstating it. Structural fix filed as #557.

## [ai-cli-optout 1.1.2] — 2026-08-03

The shipped `SKILL.md` frontmatter `description` was 1674 characters against the Agent Skills
maximum of 1024 — startup discovery metadata, so an over-limit value risks failing validation or
loading depending on the consumer, silently either way. Closes #374.

### Fixed
- **`description` trimmed to 956 characters**, 68 under the limit, from 1674 (both measured by
  parsing the frontmatter with a real YAML parser, not by counting source bytes). The trailing list of
  41 literal trigger phrases was the cheapest ~700 characters to reclaim.
  - **What actually preserves discovery is naming things, not listing phrasings.** The body loads only
    after the skill has been selected, so moving phrases there cannot keep them matchable — a first
    draft did exactly that and claimed "no phrase was dropped", which was true of the file and false
    of the behavior. The description instead now names **every vendor** and **every action term** —
    telemetry, tracking, analytics, error reporting, feedback, opt out, privacy mode, kill switch — so
    requests like "disable windows telemetry" or "opt out of vercel" still match without appearing
    verbatim. `gh` and JetBrains join the vendor enumeration, where neither had ever appeared; the old
    description mentioned them only inside trigger phrases. Measured: of the 43 distinct words across
    all 41 former phrases, exactly two are absent from the new description — `optout`, a spelling of
    "opt out", and `off`, which is a synonym rather than a spelling, so "vscode telemetry off" is the
    one phrasing that now rests on semantic inference rather than on a word the description contains.
  - Ten representative phrasings remain in the description; all 41 are recorded in a new **Trigger
    phrases** section in the body, explicitly labelled documentation rather than discovery.

### Not included
- **An automated frontmatter-length gate is deliberately deferred to #425, not forgotten.** #374
  proposed one alongside the fix, and a draft was written and reviewed twice. Both rounds rejected it
  for the same
  structural reason: the cap applies to the YAML *value*, not to the source text, so enforcing it
  without a YAML parser means writing one. Each review round surfaced a fresh class of valid YAML that
  the hand-rolled reader silently UNDER-measured — aliases, `+` chomping with trailing blank lines,
  U+00A0 inside a plain scalar, a duplicate key (YAML takes the last, the reader took the first), a
  column-zero key nested in a flow mapping — and every one of those is a false PASS on a file that
  really does violate the cap, which is the exact failure such a gate exists to prevent. A gate that
  under-measures is worse than no gate, because it converts an unknown into a false assurance. The
  requirement stands: **#425** carries the full list of measured under-counts with the parser's own
  figures, the requirements an acceptable design must meet, and the verification trap that made the
  rejected draft look verified when it was not. The design needs a root of trust — a real parser, or a
  provably conservative over-estimate — rather than another special case.

## [enduser-handbook 1.12.0] — 2026-07-31

Build-provenance records: a published handbook can say which build of the documented software it
describes. Closes #362.

### Added
- **Build-identity resolution** (`assets/lib/build-identity.mjs`, new, pure — it imports nothing at
  all) — an optional `capture.build_identity{command, ui_read}` resolves a build identity through a
  three-step chain (configured command → an LLM read of the running UI → unavailable, with a fixed
  reason naming which step ended it). The chain runs **twice per capture run**, at open and at
  close, and the two results combine: the same known value both times is recorded as-is; differing
  known values record `null` with `build_changed_during_capture`; a closing resolution that itself
  fails records `build_unconfirmed`, because a closing failure is not evidence of a deploy but is
  not evidence against one either. W2 warns and never halts on any of these — a missing, failing or
  drifted identity source never blocks capture. A third resolution happens at W6, to classify the
  delta between the recorded identity and the current one.
- **Provenance records** (`assets/lib/capture-record.mjs`, new, the only new module that touches
  disk) — one run record per capture run and one record per chapter, written through a single
  injectable filesystem seam so the atomicity claims are testable by interposition rather than by
  inspecting results after the fact. Ownership is decided by topology, not by a flag: gate 5
  requires `capture.output_dir` and the derived provenance root to be physically disjoint at a
  component boundary, and halts **only when `capture.build_identity` is configured** — an adopter
  who never asked for provenance gets one warning and a run that proceeds. W6 then reports
  `provenance_unavailable` rather than `record_absent`, because "cannot carry provenance here" and
  "the records were lost" are different problems and only one is worth investigating.
- **The open and the close are bracketed over `capture.output_dir`, and the bracket's limits are
  stated rather than implied** — the run state carries what the open observed of that directory,
  authenticated by the same digest as the rest of the opening payload, and the close refuses
  (`provenance_hazard`, `capture.output_dir moved while this run was open`) when it no longer holds.
  The refusal names which observation disagreed. Two things are deliberately **not** drift: a root
  the capture command creates during the run (the ordinary first capture), and a rename or alias
  rotation that leaves the same physical directory in place under a new resolved path — for a root
  that existed at open the directory's identity decides, and for one that did not, containment
  inside the ancestor its absence was established against. What IS refused is `capture.command`
  replacing its own output directory. Two limits an adopter is entitled to know rather than
  discover: the directory is compared by `<dev>:<ino>`, which is unique among objects live at the
  same time and not across time, so detection is reliable against a rename-over and **best-effort
  against a delete-then-recreate** whose inode the filesystem happens to reuse; and every regular
  file the CLOSING snapshot can see under an accepted entry's asset directory is attributed to this
  run — including one absent at open, which is the ordinary case for a file the capture just
  produced and precisely why a file that merely appeared mid-run is indistinguishable from one the
  command wrote — because nothing on a filesystem says which process wrote a file. Keep backup, sync and editor
  tooling off `capture.output_dir` while a run is open. Neither residue closes with a longer
  path-based comparison — both need a capability this module does not have (a held handle, or a
  run-owned staging directory the capture writes into), so they are documented preconditions rather
  than checks that pretend.
- **Crash recovery as a total function over what is on disk** — the classifier observes
  `(token, record, temps)` *after* gate 6 and returns one of nine states; a path failing gate 6 is a
  halt (`provenance_hazard`), not a state, so totality is over a written-down input domain rather
  than over a vague "whatever can be on disk". Each repair carries a progress chain derived from its
  own mutation order — abort runs `prepared → open → absent` — so re-running after a crash *inside*
  a repair resumes the remaining suffix instead of refusing; only the final state is a no-op. The
  repair a state prescribes is the only API permitted to act on it, and `expected` is an
  optimistic-concurrency witness rather than an authorization capability, which is stated outright
  rather than implied.
- **Image-destination API** (`assets/lib/chapter-paths.mjs`) — the link-group scanner, the
  inert-context stripper and the destination decoder are now exported, alongside
  `buildEmbedCandidates`, `isCanonicalAssetKey` and `expectedAssets`. Completeness is
  accounted on **raw** chapter text against a stripped-view recognition pass, so any `![` marker
  that cannot be proved accounted for halts naming the construct. That one rule closes the whole
  erasure class — an unmatched inline-code opener, an unclosed or malformed comment, a backtick in a
  fence info string — instead of growing a special case per review round.

### Changed
- **Breaking, stated plainly: `manualMigrationChecklist` now requires a fifth argument.**
  `manualMigrationChecklist(profileLike, oldEntry, newEntry, vaultRelChaptersDir, provenanceActive)`
  throws a `TypeError` unless `provenanceActive` is an explicit boolean; it previously defaulted to
  `false`. A four-argument call that used to return migration facts now throws before producing the
  halt or the checklist. The default was removed rather than kept because it was the defect: the
  twelfth fact kind was reachable by no caller outside the tests, so an active group migration
  silently omitted the provenance-record move from both the checklist and the rendered halt, with
  nothing red to catch it — the same shape as the extraction-seam defect this release also had to
  fix. Passing `false` is a legitimate explicit answer for a run where provenance is not active and
  reproduces every pre-1.12.0 checklist byte-for-byte; omitting the argument is not the same thing
  and is refused. The version is 1.12.0 rather than 2.0.0 because this function is a skill asset
  consumed by the workflow in `SKILL.md` — updated in the same release — and not a published
  package API; an adopter who wrote their own script against `assets/lib/chapter-paths.mjs` is the
  one case that breaks, and it breaks loudly rather than silently.

### Notes
- The opening digest is RFC 8785 (JCS), implemented in-tree because this repository carries no
  `package.json` and no lockfile. Duplicate keys are compared as **decoded** names rather than raw
  lexemes, lone surrogates are rejected by a manual code-unit scan rather than
  `String.prototype.isWellFormed` (no Node floor is declared anywhere here), and the digest is
  pinned by an independently computed UTF-8 vector — a canonicalizer that hashes the correct string
  as UTF-16LE passes every self-consistent fixture and fails only that one.
- Deleting a record is a pathname operation and inherits the check-not-lock limit gate 3 already
  carries: the descriptor validated is not the entry unlinked, so a parent component replaced
  between inspection and mutation redirects it. Node exposes no `unlinkat`. Hostile state already
  present when inspection begins is covered; concurrent replacement is a stated residual risk.
- No `publish.provenance_dir` and no frontmatter emission — both were specified, priced and
  declined for this release rather than shipped as configuration nothing honours.

## [enduser-handbook 1.11.0] — 2026-07-26

Makes the #329 non-heading manual halt convergent and adds present-line placement verification for a bounded non-heading-index subset (#330). Closes #329. Closes #330.

### Added
- **Present-line placement verification for non-heading indexes** (#330) — a new `verifyNonHeadingPlacement` (`assets/lib/chapter-paths.mjs`, exported alongside `indexView` and `leadingFrontmatterSpan` in `chapter-paths.d.mts`) checks a grouped chapter's already-present index line against the entry's current `group_title`, but only for files for which the fixed-probe writer call returns `kind === 'inserted'` or `kind === 'present'` and which hold exactly one selected-target match, that match lying outside the writer-recognized leading-frontmatter span. `ok` proceeds; `misplaced` halts reusing the exact headings-form wording (`foundContainer: null` renders as `(none)` when uncontained). A fourth outcome, `inconsistent`, is a fail-closed contradiction check rather than an outcome an operator should expect: it fires only when the verifier's own match count differs from the one the caller reported, and since the verifier re-runs the same pure lookup with the same arguments — and both adapters halt on two-or-more matches at step 0 and route zero matches to the line-absent branch — no documented caller can produce it. Every other non-heading file keeps today's behavior: native/YAML MkDocs configuration, and any Markdown nav file the class excludes (a wildcard, an ordered list, an explicit `<!--nav-->` marker, two same-named containers, or a match sitting inside frontmatter), returns `unverifiable` — the check ran and declined to conclude, nothing further verifies placement, no confirmation is requested, and the run continues unverified exactly as it did before 1.11.0 on that path. MkDocs `nav:` container automation itself remains its own follow-up, #328.
- **Convergent #329 halt** — the shipped `not-a-list` manual halt in both adapters now also tells the operator exactly what shape the next run recognizes (a Markdown list row indented two spaces under the named container, with the exact link or wikilink target) for a `group_title`, target and title an isolated gate accepts, so one manual edit converges on the very next run for that case, provided the row carries a plain title AND the real index carries no inert region — the gate judges a two-line candidate in isolation and never reads the operator's actual file, so an unclosed fence, HTML comment or inline code anywhere in it defeats the accepted edit and the halt repeats (measured). The halt asks for exactly that and stops there, deliberately: it gives the operator an instruction rather than a rule about which outcome a non-plain title produces. Four successive attempts to state that rule in prose were each measured false — the recognizing predicate is `isPlainLabel` applied to whatever `extractLabel` returns for that row, and `extractLabel`'s decoding differs by link syntax (a Markdown-link wrapper decodes backslash escapes, a wikilink alias does not, and HTML entities are decoded by neither), so it is neither "the raw characters" nor "what the title renders as". The exact recognized class, with the measured outcome per link mode, now lives in "Nested-list automation limits" and is pinned by a unit matrix rather than by a sentence. A triple the gate rejects (an embedded newline, a padded `group_title`, a disallowed character in the target, ...) gets the unchanged 1.10.0 halt instead, with no improvement and no convergence claim. #329 itself — automating a bare path-table container — is closed as not soundly automatable rather than delivered; this halt improvement is what the issue gets instead.

### Fixed
- **`wireNestedListChapter` unbounded index-row growth** — a runtime behavior fix to the shipped 1.10.0 nested-list writer, not a documentation correction. The writer had no membership check of its own: it relied entirely on the caller's step-0 presence scan (`locateChapterLine`), which recognizes a row by parsing a link target out of it. When a manifest `title` kept its own row's link destination from parsing and the emitted child marker was `-`, step 0 reported it absent on every run and the insert-only 1.10.0 writer appended the same row on every publish. With an emitted `*`/`+` child, 1.10.0 instead wrote one row whose raw grouped target made the next scan refuse the file. `wireNestedListChapter` now runs its own literal, content-verbatim membership check and returns `{kind: 'present', index}` — one of the two outcomes 1.11.0 adds to the shipped 1.10.0 trio (`inserted | multiple | not-a-list`), making the returned union five: `inserted | present | unwritable | multiple | not-a-list` — instead of inserting a duplicate when the accepted target container already carries this exact chapter link. This bounds the `-` child case; the `*`/`+` child case is prevented before writing by the separate re-read rule below. The writer reuses the last existing child's marker, falling back to the container marker only when there is no child, so this scope is not a property of the whole file or necessarily of the container.
- **The 1.10.0 writer could poison the index file it wrote into** — the second runtime behavior fix, and the one with the wider blast radius. For the re-read-rejected values below, 1.10.0 emitted bytes that its own scanner refused on every later run, disabling nested-list automation for every chapter and group in that index. 1.11.0 instead feeds the proposed bytes through the real `prepareIndexLines` / `hasYamlMappingStructure` / `containerOwnerScan` pipeline and returns `{kind: 'unwritable', field}` without an index to persist. `field` is derived by substituting a known-readable stand-in for each emitted line, so it names `'title'`, `'group_title'`, or `'unknown'` when neither single substitution clears the refusal. Both adapters surface one byte-identical halt.

  The refusal matrix is over field × link mode × the bullet marker **of the line being written**. For an existing container, the new child reuses the last existing child's marker, falling back to the container marker only when there is no child; on container creation, both new lines use the first indent-0 bullet's marker. In a `title`, every emitted marker refuses a backtick run (an unterminated inline code span, never a fence), an HTML comment, or U+2028/U+2029. When the child marker is `*`/`+`, path mode also refuses a row whose whole-link unwrap falls to raw content after an unescaped `]` or a trailing odd backslash run; wikilinks mode refuses any `]` in the alias because its whole-content label unwrap falls to raw content even when step 0 can still parse a terminal bracket run. In both modes that raw content exposes the grouped target's `/` to `isBarePathBullet`. In a newly-created `group_title` line, U+2028/U+2029 is fatal on every marker; `/` or terminal `.md` is fatal with `*`/`+`; and a colon after the first token or an all-hyphen value is fatal with `-`. Inline code or an HTML comment in a `group_title` instead short-circuits earlier to `not-a-list`.

  Measured on both releases for every refusal cell named above: 1.10.0 wrote the value and the result then answered `not-a-list` even for an unrelated group; 1.11.0 writes none of them. A title fault leaves the untouched index available to other chapters, including another chapter under the same container. A `group_title` fault repeats for that group until the group-scoped manifest value is fixed; every entry in the group must change together because `validateGroups` rejects conflicting titles. The corrected value then wires on the next run, provided the index carries exactly ONE container of that name — measured, an index already holding two same-named containers answers `multiple` for every value, corrected or not, because container ambiguity is resolved before any value is judged.

- **The `present` halt's recovery instruction could re-halt** — the shipped wording told the operator to "give the chapter a plain title in the manifest — no Markdown markup, backslash escapes, or HTML entities in it". A U+2028 or U+2029 line separator is none of those three, so it satisfies the instruction literally, and the 1.11.0 re-read postcondition above then refuses it: measured `{kind: 'unwritable', field: 'title'}` in all 12 cells of the matrix that applies here — the two separators × both link modes × all three emitted markers, with the field fixed to the chapter title, since that is the value this halt asks the operator to change. The halt now states the same positive class as the `unwritable` halt — a non-empty title of Unicode letters and numbers with words separated by single ASCII spaces — verified across both link modes and all three emitted markers, and byte-identical in both adapters. The three #329 manual halts deliberately keep the broader wording: measured separately, their rows all reach the next run's proceeding branch, separators included as `unverifiable` rather than a repeat halt, so narrowing them would refuse values that demonstrably work. Both adapters now open "Nested-list automation limits" with that comparison, since an operator meeting both halts otherwise sees two recovery rules and no reason for the difference.

### Changed
- Both publish-target adapter docs (`static-md.md`, `obsidian-vault.md`) — the present-line non-heading branch now calls the verifier, and "Nested-list automation limits" names the verified class and its exclusions, including the shipped 1.10.0 leading-frontmatter view disagreement (reproduced, not fixed here — #337) and the literate-nav multi-list `SUMMARY.md` caveat. `revalidation.md`'s non-heading `group_title`-changed fact is narrowed to the same verified class, with explicit user confirmation retained for everything outside it.

### Testing
- `tests/citation-audit.test.mjs` — the #343 ReDoS scaling assertion is repaired rather than worked around, because this release's added tests pushed it from occasional to reliable: measured 3 full-suite failures in 3 at 804 tests against 3 clean runs in 3 at the 616-test pre-release baseline, with `tests/citation-audit-lib.mjs`'s regex untouched. It compares two wall-clock durations, and both ends were unsound — `Date.now()` gave the small run 0-1ms and a `Math.max(small, 1)` floor turned the "ratio" into the large run's absolute duration, while mid-measurement descheduling inflated the large run to 57-94ms against a ~16ms baseline. Now timed on a nanosecond clock, and the scaling verdict is a median at both levels: the typical of five samples per size, then the median of five paired ratios. Minimising each side independently was tried and could not work, because the noise runs both ways — measured in-suite, single paired ratios ranged 3.2x-108.7x, and the low end means the SMALL run was the one that got hit. A minimum also discards a majority, so four slow calls and one fast one read as five fast ones. That statistic ranged 12.1x-29.3x over 16 full-suite runs, and the ceiling is 60x, set from both ends by measurement: 2.0x above the worst healthy sample, and 2.0x below the 117.4x the retired quadratic matcher actually produces at these inputs — not the ~256x a textbook n^2 would suggest. That same matcher takes 38.8 seconds where the healthy pass takes tens of milliseconds, so the 2-second bound is the decisive gate at a 19x margin and the ratio is the early signal at 2x. The 2-second blow-up bound reads the worst single large sample. Two alternatives were measured and rejected first: enlarging both inputs to 20,000/320,000 made the in-suite median worse (56.7x against 15.8x, both pairs timed in the same runs) because the working set leaves cache, and merely raising the ceiling to 120x still failed 1 run in 20. This remains a wall-clock bound and can still go falsely red on a loaded machine: a failure means re-measure on a quiet box, not a proven regression.
- `tests/reference-assets.test.sh`: needles for the new `.d.mts` declarations, the canonical verified-class sentence at all four pinned prose sites (both adapters' limits sections, `revalidation.md`, this changelog entry), and the convergent #329 halts plus the `inconsistent` halt as exact strings. That placement clause was measured false twice during review, in opposite directions — first promising `misplaced` unconditionally, then denying it could ever apply to a non-plain title — and the pinned wording is the third, measured version. Pinning an exact string catches wording drift but cannot catch a wording that is wrong, so the pins are regenerated from the adapter bytes by a rule that must first reproduce the previous release's pins byte-for-byte before it is allowed to write, and the claims themselves are checked by running the shipped module rather than by re-reading the prose.

## [enduser-handbook 1.10.0] — 2026-07-24

Automates GitBook `SUMMARY.md`-style nested-list index container wiring, so a grouped chapter on a bulleted (non-heading) index no longer always halts for manual container creation. Closes #223.

### Added
- **Nested-list index container automation** (#223) — a new `wireNestedListChapter` (`assets/lib/chapter-paths.mjs`, alongside exported `extractLabel`/`isPlainLabel`) automates grouped-chapter wiring on a GitBook-style nested-list index: a single forward-pass validator over a raw-faithful BODY finds or creates a plain-label container bullet and inserts the chapter line under it, with EOL-faithful emission (CRLF and terminal-newline preserved byte-for-byte). Both publish adapters (`static-md`, `obsidian-vault`) call it on the line-absent non-heading branch; two or more matching container bullets halt naming them (`Found multiple '<group_title>' container bullets …`). The automatable subset is a deliberately conservative, user-ratified boundary: plain-text container labels and group titles only — inline markup, a `*`/`+` bare-path row, inline code / comments / fences, YAML `nav:`, path tables, mixed line endings, and any other ambiguous shape defer to the existing manual halt. Its residual was described as one visible cosmetic duplicate, but 1.11.0 measured two marker-scoped failures: a target-breaking title on an emitted `-` child appended one row on every publish without limit, while the same raw grouped-link shape on an emitted `*`/`+` child wrote once and made every later scan refuse the file. Nothing was deleted, but neither outcome was the claimed one-off residual. A richer rendering-aware matcher is a possible follow-up, not a bug.

### Changed
- Both publish-target adapter docs (`static-md.md`, `obsidian-vault.md`) and `revalidation.md` updated to the actual scope — headings-form plus the bounded nested-list subset — with a new "Nested-list automation limits" section documenting the residual; stale "only headings-form is automated" and "no parser exists" prose narrowed.

### Testing
- `tests/chapter-paths.test.mjs`: +43 tests — SINGLE/ZERO/MULTIPLE outcomes, rule-isolating `not-a-list` mutant fixtures (each watched red-before-green, one guard removed at a time), mask-pair rejections, positive-accept fixtures, direct `extractLabel`/`isPlainLabel` units, and input purity.
- `tests/reference-assets.test.sh`: +17 assertions — `has_in_section` needles for the new adapter scope/branch/halt/limits prose in both adapters plus an `md-structure` structural ownership pin.
- `tests/citation-audit.test.mjs`: the #258 citation-direction lint re-pinned after the doc edits added five citations and shifted offsets — every new unresolved entry reviewed as a legitimate near-miss (parenthetical `INDEX wiring`/`Grouped index wiring` headings, the non-heading `Non-headings index` reference).

## [enduser-handbook 1.9.3] — 2026-07-24

Adds a structural citation-direction lint and fixes two live wrong-direction citations it was built to catch. Closes #258.

### Added
- **Citation-direction lint** (#258) — `tests/citation-audit-lib.mjs` scans every reference doc + `SKILL.md` for a quoted-title citation immediately followed by an "above"/"below" direction word (deliberately verb-free — no "see" requirement — after three rounds of plan review each found a real citation form a verb-anchored pattern missed), reusing `assets/lib/md-structure.mjs`'s exported `maskFencedRegions` so a fenced-code false-positive is excluded by the one shared fence engine. Every occurrence carries its absolute character offset — a true per-occurrence identity — via a newline-offset table. `tests/citation-audit.test.mjs` asserts: a non-vacuity guard (nonzero citation count), a mechanically-enforced unresolved-citation allowlist keyed `{file, offset, quotedText, direction}` (exact match — a new unresolved citation, a stale entry that's since become resolvable, or a direction flip on an unresolved citation all fail loudly), a uniqueness guard (an ambiguous title matching 2+ headings never silently resolves), and a direction assertion over every resolved citation.

### Fixed
- **Two live wrong-direction citations** (#258) — `references/publish-targets/obsidian-vault.md`: `"Layout you produce"` said "below" (heading is above); the containment-check note's `"Glossary backlink discipline"` said "below" (heading is above). Both targeted, length-preserving single-occurrence edits — the same phrases are correctly directed elsewhere in the file.

### Security
- **ReDoS in the citation-matching regex, found and fixed pre-merge** — the original separator shape (`\s*(?:[,;:]|and\b)?\s*`, two adjacent unbounded quantifiers sandwiching an optional group) had exponential backtracking on an undirected run of quoted titles (verified: ~26 repeats took 8+ seconds). Not triggered by the shipped corpus, but a latent hang risk for a future doc edit. Fixed by collapsing the separator into one quantified alternation (`(?:[\s,;:]|\band\b)*`), verified polynomial-bounded (not exponential) up to 80,000 repeats; regression test pins both the non-match and a tight time bound.

### Testing
- New `tests/citation-audit.test.mjs`: 11 cases (non-vacuity, allowlist exactness, uniqueness, direction assertion over 31 resolved citations, 6 synthetic fixtures including a same-line/same-title/opposite-direction pair and the ReDoS regression lock). Full suite: +2 assertions (module discovery + the node:test run).

## [enduser-handbook 1.9.2] — 2026-07-24

Documentation-only follow-ups: makes the path-mode `.md` byte-identity in `locateChapterLine` an explicitly intentional design (#311) and the omission of the link formula from `revalidation.md`'s boundary-trigger note deliberate (#260). No behavior change. Closes #311. Closes #260.

### Changed
- **Path-mode index-matching byte-identity documented as intentional** (#311) — the opt-in `{wikilink}` `.md`-fold in `foldTargetForMatch`/`locateChapterLine` is deliberately NOT generalized to path mode: a path-mode index target is a real href where `.md` is load-bearing (`items` and `items.md` are distinct resources, with no Obsidian `[[note.md]] == [[note]]` equivalence off a static site), so folding would risk a false-positive match against a genuinely-different resource. A divergent hand-authored extensionless row is therefore left unmatched: step 0 appends the canonical `.md` row and retains the divergent row alongside it (append-and-retain) — a benign redundant index entry, not a silent false-match — and the link-integrity gate does not reject the retained row (an index-wide broken-link/alias sweep would be needed, noted as a possible future improvement). Strengthened the `foldTargetForMatch` comment + `locateChapterLine` JSDoc + a `static-md.md` Step-0 note; zero logic change (the fold still only fires under `wikilink: true`).
- **Link-formula omission made deliberate in `revalidation.md`** (#260) — the "Boundary triggers" note names the group-aware path and embed formulas; a scoping clause now explicitly excludes the link formula (scoped differently per adapter and mode) so a future editor can't naively add it and reintroduce the embed/link conflation two earlier rounds fixed.

### Testing
- `chapter-paths.test.mjs`: +1 test locking the by-design path-mode extensionless non-match. `reference-assets.test.sh`: +2 `has_in_section` pins for the #311 static-md note and the #260 boundary-trigger clause.

## [enduser-handbook 1.9.1] — 2026-07-24

Adds a real, dependency-free Markdown structural parser backing the reference-doc test harness's placement proofs. Closes #303.

### Added
- **`assets/lib/md-structure.mjs` Markdown heading-tree parser** (#303) — a pure, dependency-free structural resolver (`parseHeadings`, `findOwner`, `sectionStatus`, and the shared `maskFencedRegions` fence primitive) porting the fence/CRLF/tab rules from the existing `_section_contains` awk engine in `reference-assets.test.sh`, so the two engines that hard-gate the same reference docs stay provably consistent. Backs the shell suite's line-order `assert_line_before` heuristic (kept, additive, not replaced) with an authoritative structural proof: 6 new node:test branch-ownership pins against the real `static-md.md`, each guarded by sentinel- and heading-uniqueness checks plus a decoy/moved-occurrence mutant fixture proving a moved real occurrence can't hide behind a correct-position decoy.

### Testing
- New `tests/md-structure.test.mjs`: 28 cases (every ported fence rule in both directions, exact-heading/first-occurrence/prefix-decoy binding, the three `sectionStatus` states, the corrected half-open interval boundary, `findOwner` nesting, prototype-chain heading titles, and the 6 real branch-ownership pins with their mutant fixture). Full suite: +11 assertions (module-pairing/normative-banner gates for the new files + the node:test run).

## [enduser-handbook 1.9.0] — 2026-07-24

Adds an optional `publish.vault_root` override for the Obsidian vault-root binding (#298) and an opt-in `publish.per_group_slug_uniqueness` key that relaxes the duplicate-slug halt to per-group under group-qualified links (#310). Closes #298. Closes #310.

### Added
- **Optional `publish.vault_root` override** (#298) — a new optional profile key names the Obsidian vault root directly: the escape hatch when `.obsidian/` is gitignored or absent, and the in-release resolution for the previously-unsupported multiple-`.obsidian/`-marker ambiguity halt. When set, it must resolve to an existing readable directory (external/absolute paths allowed; unlike a first-run `chapters_dir` it gets no ENOENT trailing-suffix allowance) and bypasses the `.obsidian/` discovery walk and both the zero- and two-or-more-marker halts; the pre-existing "everything must resolve under `<vault-root>`" validation still catches a mis-pointed value. Schema + example + adapter prose only — `chapter-paths.mjs` already consumes the precomputed `vaultRelChaptersDir`, so `<vault-root>` merely gains a second provenance.
- **Opt-in `publish.per_group_slug_uniqueness`** (#310) — a new optional boolean (default false = the pre-1.9.0 global duplicate-slug halt, byte-for-byte). When true, `validateGroups`/`duplicateSlugHalts` scope slug uniqueness *per namespace*: a well-formed grouped entry keys on `group<NUL>slug`, so two chapters in different groups — or a flat (group-less) chapter and a grouped one — may share a slug, while a same-group duplicate and two flat chapters still halt. The opt-in re-admits cross-namespace basename ambiguity for user-authored bare `[[slug]]` wikilinks and Quartz-shortest bare-name resolution, documented across the adapter and manifest docs as the accepted tradeoff. `validateGroups` gains an optional `{ perGroupSlugs }` second argument (1-arg callers unchanged); `SKILL.md` W1/W6 thread it from the profile.

### Testing
- `chapter-paths.test.mjs`: +9 tests — the four per-namespace scenarios (different-group OK / same-group halt / two-flat halt / flat-vs-grouped OK), a NUL-separator alias-freedom discriminator (adjacent-boundary values, red against a no-separator join), and a malformed-group guard (red against a loose `!== undefined` predicate). `profile-schema-evaluator.test.mjs`: +4 tests — `vault_root`/`per_group_slug_uniqueness` GREEN validation + RED type probes. `reference-assets.test.sh`: net +10 pins for the #298 override prose and the #310 per-namespace rationale (deltas — absolute totals are environment-dependent, see the 1.8.1 entry).

## [enduser-handbook 1.8.4] — 2026-07-24

Fixes a silent-pass gap in the reference-doc test harness's negative-assertion helper. Closes #302.

### Fixed
- **`hasnt_in_section` can't distinguish "needle absent" from "heading absent"** (#302) — `_section_contains` now returns a distinct exit code (2) when the queried heading itself is absent, separate from exit 1 (heading present, needle genuinely not found under it). `hasnt_in_section` dispatches on the three-way code, so a negative pin correctly hard-fails once its heading is renamed or deleted, instead of silently and permanently passing because both cases used to fold into one nonzero exit.

### Testing
- `reference-assets.test.sh`: +2 assertions (the absent-heading exit-code lock and the wrapper hard-fail lock), both watched red against the prior two-way engine before the fix. All 9 pre-existing `hasnt_in_section` self-tests plus the one real caller (`## Vault root`) reconfirmed unaffected.

## [enduser-handbook 1.8.3] — 2026-07-23

Adds drift-prevention gates against `profile.schema.json`, the `assets/lib` module pairing convention, and the capture-guard sentinel set, closing three follow-up issues surfaced by earlier self-review. Closes #296. Closes #297. Closes #299.

### Added
- **Schema-derived required/properties cross-check + 3 new enum assertions + obsidian-vault 8-key binding block** (#296) — the previous hardcoded 9-item required-keys loop was replaced with a real structural cross-check parsed straight off `profile.schema.json`'s own `required`/`properties` arrays, rather than grepping the schema against itself. Added the 3 missing enum assertions (`stack.frontend.type`, `stack.surface`, `diataxis.quadrants_in_use` items) and a symmetric obsidian-vault "Exact-key bindings" block mirroring static-md's existing 8-line block.
- **Real example-profile-validates-against-schema gate** (#296) — a new recursive JSON-Schema evaluator (`tests/profile-schema-evaluator.mjs`) implementing the exact keyword subset `profile.schema.json` uses (`type` incl. union arrays, `additionalProperties` in all three forms, `required`, `properties`, `const`, `enum`, `pattern`, `items`, `minItems`), with a fail-closed structural sweep that walks every subschema reachable via `properties`/`items`/a schema-valued `additionalProperties` and rejects any unrecognized keyword — regardless of whether the shipped example instance happens to exercise that branch. Wired to `handbook.profile.example.yml` via a gated `ruby -ryaml -rjson` → Psych → JSON → Node evaluator pipeline, skipping loudly if Ruby/Psych is unavailable.
- **`assets/lib` module-pairing gate** (#297) — for every `assets/lib/*.mjs` module, asserts its `.d.mts` declaration and `tests/*.test.mjs` file both exist, using the existing `category_files` helper. Forward-looking regression gate; no existing gap.
- **Capture-guard sentinel set-equality gate** (#299) — extracts the actual `// [guard:*]` sentinel set from `capture-guard-policy.mjs` (excluding the header comment's own literal wildcard mention) and asserts it's set-equal to the hardcoded 7-item allowlist in `reference-assets.test.sh`, catching a sentinel added/renamed/removed in source without the test's list being updated to match. The set-equality gate's own `sort -u` pipelines check exit status before comparing (`LC_ALL=C` pinned on both) — an earlier draft compared unchecked output, so a failed `sort` would have produced two empty, "matching" strings and silently passed.

### Fixed
- **Prototype-chain membership leak in the #296 schema-validation gates** — found by the `lazy-ants-reviewer` bot on PR #318. The evaluator's required-key check, its properties-descend check, and the bash-side required/properties cross-check all used `key in obj`, which walks the prototype chain: a required/declared key named after an inherited `Object.prototype` member (`toString`, `constructor`, ...) was silently satisfied by that inherited member even on a genuinely empty instance — a false PASS on the required-key check, and a spurious false REJECT on the properties-descend check. All three sites switched to `Object.hasOwn()`; added 3 regression tests isolating each site individually (combining `required` and `properties` on the same key in one test schema was found to mask one site's leak behind the other's error output).

### Testing
- `reference-assets.test.sh`: +8 assertions across the four #296/#297/#299 sub-gates (deltas — absolute totals are environment-dependent, see the 1.8.1 entry). `profile-schema-evaluator.test.mjs`: new file, 29 tests (26 original + 3 prototype-chain regression probes).

## [enduser-handbook 1.8.2] — 2026-07-23

Hardens `reference-assets.test.sh`'s `revalidation.md`/`SKILL.md` halt-clause coverage with site-bound needle pins in place of loose whole-file checks, closes a missing-coverage gap on the chapter-wiring non-heading-form completion branch, and adds two `chapter-paths.mjs` boundary-condition tests. Closes #251. Closes #252. Closes #256.

### Changed
- **`revalidation.md`/`SKILL.md` halt and delta-manifest assertions replaced with site-bound pins** (#251) — the previous whole-file `has`/`has_ci` checks for `'newly discovered'`, `'delta manifest'`, and case-insensitive `'halt'` were too loose to distinguish a mutation of the accepted-diff class's "no halt" conclusion from the material class's "newly discovered" trigger clause. Replaced with six needle pins bound to exact, verified-unique lines in `revalidation.md`, plus one scoped pin on `SKILL.md`'s own W6 halt clause.

### Added
- **Missing non-heading-form completion assertion** (#252) — `obsidian-vault.md`'s chapter-wiring section has two branches that independently claim completion in different wording; only the headings-form branch had test coverage. Added the missing assertion for the non-heading-form branch's own wording — no doc change, purely closing a test-coverage gap.
- **Two `chapter-paths.mjs` boundary tests** (#256) — `specReferencesDir` fixtures exercising the needle-at-index-0 and needle-at-EOF branches, and a `chapterHasWikilinkTo`/`isComponentSuffixMatch` fixture exercising the equal-component-length boundary. No production code changed; both functions were already correct by design.

### Testing
- `reference-assets.test.sh`: 6 old whole-file checks removed (3 each, `revalidation.md` + `SKILL.md`), 8 new site-bound pins added in their place plus 1 new #252 pin — net +3 assertions. `chapter-paths.test.mjs`: +3 assertions (190 → 193). Absolute suite totals are environment-dependent (see the 1.8.1 entry above) — deltas are the portable figures.

## [enduser-handbook 1.8.1] — 2026-07-23

Documents (with a regression lock, not a behavior change) why tab-prefixed fence markers in `_section_contains` can never produce an incorrect halt decision, and adds the missing "Write-time canon" citation to two previously-uncited `obsidian-vault.md` mentions. Closes #257. Closes #259.

### Added
- **Tab-handling proof + regression lock for `_section_contains`** (#257) — the original issue asked whether a leading tab in a fence marker's indentation could cause `leading_spaces()`'s character-offset and CommonMark's column-count semantics to disagree. A structural proof (documented in a comment above `leading_spaces()`) shows they never can: any leading tab forces the derived `rest` to begin at that unconsumed tab (which can never match a fence marker), and independently forces the true CommonMark column past the fence-gate threshold — so the two never disagree, for any input. No code change; two new self-test fixtures lock in the current (correct) behavior as a forward-looking regression guard.
- **Missing "Write-time canon" citations in `obsidian-vault.md`** (#259) — two mentions of write-time canon had no citation back to `revalidation.md`'s "Write-time canon" section, unlike the equivalent `static-md.md` passages. Both sites now cite it, matching `static-md.md`'s exact phrasing, with new test assertions pinning the citation text at each site.

### Testing
- `reference-assets.test.sh`: +4 assertions (2 new #257 self-test fixtures, 2 new #259 `has_in_section` citation pins). Absolute totals are environment-dependent (the suite's optional esbuild-gated TypeScript check adds 0 or 1 assertion depending on local tooling) so only the delta is stated here — see the suite's own conditional-discovery note.

## [enduser-handbook 1.8.0] — 2026-07-23

Obsidian-vault chapter wikilinks and INDEX targets become vault-root-relative, and the formula that computes them is now exported and directly tested. Closes #294. Closes #295.

### Changed
- **Chapter wikilinks/index targets are vault-root-relative in wikilinks mode** (#294) — the `obsidian-vault` adapter previously emitted the bare chapter `slug` (Obsidian basename resolution), which only disambiguates when that slug is unique across the *whole* vault; the plugin only enforces uniqueness across the handbook, so a same-basename foreign vault note could shadow a nested chapter's link and force Obsidian onto its fragile suffix-match resolution tier. The formula now emits the vault-root-relative path instead (Obsidian's exact-match tier), mirroring the #247 glossary-link fix. A root-topology handbook (`chapters_dir` == vault root) already resolved safely and is unaffected.
- **Legacy bare-link transition, applied on publish** — a pre-1.8.0 chapter row or in-chapter link still spelled as the bare `[[slug]]` is recognized (via a union scan over both the old and new spellings) and retargeted to the vault-relative form in place, instead of being duplicated or silently left stale. Existing placement halts (wrong/missing container) are unchanged and still apply on top of this.

### Added
- **`currentIndexExpectedTarget` is exported** (#295) — previously a private helper inside `chapter-paths.mjs`, reachable only through the Step-0 index-wiring flow and asserted by no direct test. It is now a named export, declared in `chapter-paths.d.mts`, and covered by direct unit tests in `chapter-paths.test.mjs`.

## [enduser-handbook 1.7.1] — 2026-07-23

Fixes the conflicting-`group_title` manifest halt to name EVERY conflicting title, not just the first two. `validateGroups` enumerated only `distinctTitles[0]`/`[1]`, so a group with three or more distinct titles fired the halt correctly but dropped the 3rd+ from the message — an operator aligning the first two would re-hit the same halt on a title it never named. The halt now comma-joins all distinct titles. Closes #250. The rest is `chapter-paths` test-suite hardening against three mutant classes the shipped fixtures couldn't distinguish: `manualMigrationChecklist`'s `output_dir`/`chapters_dir`/`index_file` roots are now exercised fully decoupled (#253); the fence (`runLen >= openLen`) and inline-code (`runLen === openLen`) delimiter-length rules are now tested with UNEQUAL opener/closer runs (#254); and `renderManualMigrationHalt`'s scan-failure header + detail rendering is pinned with a multi-tuple fixture (#255). Closes #253. Closes #254. Closes #255.

## [enduser-handbook 1.7.0] — 2026-07-23

De-hardcodes the reference-assets doc test suite's enumeration, fixes both wrong glossary wikilink spellings down to one vault-root-relative form, and specifies the flat/group-free INDEX target at the same precision as the grouped one. Closes #262. Closes #247. Closes #248.

### Fixed
- **The Obsidian glossary wikilink was wrong two different ways** (#247) — the raw `{{publish.glossary_dir}}/index#term` path (the same shape #220 already fixed for the wikilinks-off case) and, separately, a self-contradiction under `publish.wikilinks: true`. Both are replaced by one vault-root-relative `relative(<vault-root>, {{publish.glossary_dir}})` form, stated at a single site instead of being repeated — and able to drift — per branch.
- **`tests/reference-assets.test.sh` hardcoded its own coverage** (#262) — a nine-name list of unit-test files to re-run, plus three separate doc-scan lists kept in sync with the reference tree by hand. `profile-version.differential.test.mjs` was never on the nine-name list, so a whole test file was silently never executed; new asset or reference files added since could go unscanned the same way. Both mechanisms are now derived from the directory via a fail-closed glob helper, so a new file is covered the moment it exists rather than the moment someone remembers to list it.
- **A false "pinned by unit test" claim in `static-md.md`** — the grouped container-line half of the claim was never actually exercised by the cited test.

### Changed
- **The flat / group-free INDEX target reaches grouped-level precision in both adapters** (#248), closing two different gaps. The Obsidian adapter's flat branch stated no link-target formula at all, in either `wikilinks` mode — now added. The static-Markdown adapter already stated the formula, but its group-free flow was incompletely specified: the flat membership/duplicate outcomes (including the duplicate-slug halt) lived entirely under a `### Grouped index wiring (anyGroup manifests only)` heading, so a group-free reader was told the whole section was inapplicable and never saw them; there was also no index→chapter worked example for either degenerate layout, the row's display text was never bound to the manifest `title`, and the activation rule falsely claimed a unit-test pin that does not exist (see the false "pinned by unit test" claim under Fixed). All of these are now specified.
- **The glossary rule collapses to one site** (#247) — see Fixed above.
- **A newly-bound Obsidian vault root**, with three new halts: zero-marker, ambiguous-marker, and unreadable-ancestor. The ambiguous two-marker case is an **unsupported topology**, not a correctable error — it halts rather than guessing which marker is authoritative.
- **`publish.glossary_seed` becomes conditional in the Obsidian adapter** — the one actual behaviour change in this release, and its scope is precise: a profile with a set, readable `glossary_seed` keeps exactly today's reconciliation behaviour; only the unset/unreadable branch changes, from silently required to skipped. No deprecation cycle — this corrects the Obsidian adapter *to* the base skill's long-standing "when set and readable" contract, not a new one.

### Testing
- `reference-assets.test.sh`: 455 unconditional core assertions, +10 when `node` is on `PATH` (the executable-unit-test block, now a glob over all 10 `*.test.mjs` files — one assertion per file — instead of the old hardcoded nine-name list, so it now also covers `profile-version.differential.test.mjs`), +1 when `esbuild` is reachable (the optional TypeScript syntax check, unchanged). The two optional blocks are independent of each other.
- `node --test plugins/enduser-handbook/tests/*.test.mjs`: 462 tests, unchanged — this release adds no unit test.

## [multi-profile-plugins 1.0.0] — 2026-07-21

Initial release. Knowledge + read-only diagnostics for Claude Code plugin behavior across multiple `CLAUDE_CONFIG_DIR` profiles.

### Added
- **Skill** — explains why profiles that share a `plugins/` store hit recurring "corrupted installLocation" errors and cross-profile plugin deletion, and the structural fix (independent per-profile stores). Diagnosis-and-reasoning only; no automated migration is performed.
- **`references/cli-mechanism.md`** — the reverse-engineered CLI `installLocation` prefix validation and catalog-scoped garbage-collection mechanism, and why a durable fix needs fully independent per-profile plugin content rather than just a de-shared registry file.
- **`scripts/inspect_profiles.py`** — a read-only, stdlib-only health check that auto-detects `~/.claude*` profiles (or accepts explicit ones), flags a shared `known_marketplaces.json` (the churn risk) and cross-profile pointer leaks (exact prefix match, not a substring grep), and exits non-zero on any warning.

## [enduser-handbook 1.6.0] — 2026-07-19

Group-free manifests get the same link canon as grouped ones, and a duplicate flat slug now halts instead of silently overwriting a chapter. Closes #220. Closes #221.

### Fixed
- **Obsidian group-free glossary links 404'd** (`references/publish-targets/obsidian-vault.md`) — with `publish.wikilinks: false` and a group-free manifest, the adapter wrote the raw profile-key path `{{publish.glossary_dir}}/index.md#term`, which the renderer resolves *relative to the chapter*. For the shipped example layout a chapter at `vault/handbook/items.md` produced `vault/handbook/vault/knowledge/glossary/index.md` — every glossary backlink dead. The group-free and `anyGroup` bullets are merged into one rule using the full-target `relative()` formula.
- **The link-integrity gate that should have caught it was itself activation-scoped** — its standard-Markdown-link resolution check ran only when `publish.wikilinks: false` **and** the manifest was `anyGroup`, so a group-free vault never resolution-checked its Markdown links at all. Now covers every `wikilinks: false` chapter. The gate is **chapter-scoped**: it fires on publish, or on a revalidation that *touches* the chapter (accepted-diff or material re-author). A no-op revalidation does not run it, so an untouched chapter with a stale link stays broken.
- **A duplicate flat slug silently overwrote a chapter file and its asset dir** (#221) — `validateGroups` short-circuited to `[]` for group-free manifests, so its duplicate-slug gate never ran. The gate is extracted into a module-internal `duplicateSlugHalts` and now runs unconditionally. `validateGroups` also had **no production caller** and was documented as "an optional convenience"; it is now a mandatory, blocking pre-write step at all three write paths — W1 before assets are captured, W6 before accepted-diff re-capture and re-authoring, and as a numbered step in the manifest discipline's fixed order, ahead of the halt-for-review step.

### Changed
- **`staticEmbedPath` write canon is unconditional** (#220) — the `anyGroup` branch is gone; flat entries and group-free manifests alike use `relative(dirname(chapter_file), join(chapterAssetDir(entry), <file>))`. The superseded 1.4.1 partial concatenation degenerated to a forbidden leading-slash path when `dirname(chapter_file) == capture.output_dir`, and to a non-minimal parent path otherwise. `legacyStaticEmbedPath` is retained and exported for API compatibility but is no longer called.
- **Write-time only — no automatic retroactive repair.** Already-written chapters are not rewritten, and no chapter is rewritten *solely* because of an upgrade or an `anyGroup` flip. A pre-existing broken link is surfaced by the link-integrity gate when its chapter is next published or touched, not repaired in place. The repair engine is deferred to #246.
- **The group-free exception inventory is scoped to the helper module.** `assets/lib/chapter-paths.mjs` has exactly two 1.6.0 exceptions (`staticEmbedPath`, `validateGroups`); an adapter may carry its own group-free behaviour changes on top — the Obsidian adapter's glossary formula and link gate both changed here — so `anyGroup` gating must not be assumed to cover everything.
- `references/revalidation.md` gains the `## Write-time canon` heading that two `static-md.md` citations were already pointing at.

### Testing
- `tests/reference-assets.test.sh` gains `has_in_section`/`hasnt_in_section`, a shared `_section_contains` engine that bounds a fixed-string assertion — or its negation — to one Markdown section (heading to the next same-or-shallower heading), instead of a whole-file grep that could not distinguish a group-free branch from its already-correct grouped sibling (the existing embed-formula needle passed while the group-free glossary branch was still wrong). The engine has since been hardened across 18 enumerated rules plus two statement-level cases, each pinned by a named mutant and the fixture that catches it — the file's own boundary note is explicit this is not a claim of mutation-completeness, only the residuals five adversarial review rounds actually found.
- The grouped halt-order pin now fires all six gates and asserts the full array by `deepEqual`; the previous fixture triggered only three, so relocating the duplicate gate would have gone undetected.
<!-- Re-verify this line's counts against a fresh `node --test .../*.test.mjs` + `bash .../reference-assets.test.sh` run after the final clean review verdict, before commit — review rounds routinely add assertions after this was first written. WARNING: reference-assets.test.sh's total is ENVIRONMENT-DEPENDENT, not a single number — it has two conditional blocks (a `node`-on-`PATH` re-run of a HARDCODED list of nine named `*.test.mjs` files, not a glob — a newly-added tenth test file is not automatically covered — and an optional `esbuild`/`npx --no-install esbuild`-reachable TypeScript syntax check) that each add assertions only when available. Do not substitute your own machine's one observed total; re-derive the unconditional-core / +node-block / +TS-check split the way the line below states it, and measure all three yourself rather than trusting a prior round's numbers (they have been wrong before). -->
- 462 unit tests across the plugin's suite (`plugins/enduser-handbook/tests/*.test.mjs`, requires `node`), of which the #220/#221 changes in `chapter-paths.test.mjs` account for 168. `reference-assets.test.sh`'s documentation-assertion count is environment-dependent: 377 unconditional assertions (21 of which self-test the `has_in_section`/`hasnt_in_section` engine itself), +9 more when `node` is on `PATH` (the executable-unit-test block re-runs a hardcoded list of nine named `*.test.mjs` files and asserts each passes — NOT a glob, so a tenth test file added to `tests/` is not automatically covered), +1 more when `esbuild` — directly or via `npx --no-install` — is reachable (the optional TypeScript syntax check). The two gates are INDEPENDENT of each other, so the total `bash reference-assets.test.sh` reports on a given machine is simply the core plus whichever of the two increments apply there.

### Known limitations
- `posixRelative` misresolves profile paths whose operands carry **unequal unresolved leading `../` climbs** (it compares leading `..` segments as if they were the same directory). Pre-existing and unchanged by this release: the superseded concatenation and the full-target canon return byte-identical wrong output there, verified across 600 generated layouts. Tracked in #246.
- The wikilinks-**ON** glossary spelling contradiction (#247) and the unspecified flat/group-free INDEX target formula (#248) are pre-existing and deliberately out of scope here.

## [enduser-handbook 1.5.0] — 2026-07-17

An optional second navigation level for the handbook: manifest entries may declare a `group` (kebab-case directory axis) plus a localized `group_title`, and both publish adapters then write chapters to `chapters_dir/<group>/<slug>.md` with grouped asset dirs and two-level nav wiring. Strictly additive — a group-free manifest keeps the shipped flat layout byte-identically. Closes #19.

### Added
- Manifest fields `group` + `group_title` (`references/manifest-discipline.md`): English kebab-case one-level `group`; `group_title` required for every grouped entry, identical within a group, unique across groups. Validation gates (globally-unique slugs, reserved `assets` group/slug, group/flat-slug collision) halt with exact shared strings. Activation rule: all grouped behavior keys off `anyGroup(manifest)` — group-free manifests never meet a new code path.
- `skills/enduser-handbook/assets/lib/chapter-paths.mjs` (+ `.d.mts`) — dependency-free path/derivation/validation library (`chapterRelPath`, `chapterAssetDir`, `embedPath`, `staticEmbedPath` mode selector, `validateGroups`, `locateChapterLine`, `findContainer`, `groupChanges`, `manualMigrationChecklist`, `renderManualMigrationHalt`, `specReferencesDir`, `chapterHasWikilinkTo`), unit-tested in `tests/chapter-paths.test.mjs` with red-before-green mutation evidence (28 mutations, a–bb).
- Manual group-migration boundary (`references/revalidation.md`): retained-entry group/title changes and grouped removals HALT with a structured record (halt-is-the-record — a context-free re-run reconstructs every terminal check from the halt text) plus a manual recipe; per-delta-kind terminal-state convergence facts; capture-spec completion is red-flag + explicit user confirmation (never auto-passed); post-migration handbook-wide link scan; stale-artifact advisory. Automated migration is deliberately descoped to a follow-up issue.
- Two-level nav wiring in both adapters (`obsidian-vault.md`, `static-md.md`): establishment-only index/container wiring with a per-chapter already-wired short-circuit, wrong-container and ambiguous-container halts, headings-form automation (non-heading index forms get a manual-wiring halt).

### Changed
- `skills/enduser-handbook/assets/capture.example.spec.ts` — the per-chapter output dir is now derived via `chapterAssetDir` from the manifest entry instead of a hardcoded literal; guarded by a structural consumer-binding pin (binding anchor + sink counts + raw-screenshot-idiom ban) in the unit tests.
- `references/publish-targets/static-md.md` — under `anyGroup`, embeds use the full-target `relative()` formula (the legacy partial concatenation stays byte-identical for group-free manifests; its degenerate empty-`<rel>` leading-slash quirk is pinned by a mode-divergence test).
- Quartz `shortest`-resolution limitation for grouped vaults documented in `obsidian-vault.md` (chapter slugs are globally unique, so wikilinks stay unambiguous; `assets_per_group` co-location is a follow-up).

## [enduser-handbook 1.4.1] — 2026-07-16

Hardening plus a portability bugfix for the reference capture assets: a bleed-free capture helper for oversize overlays, an Obsidian embed path that finally derives from `capture.output_dir`, and the last safety gate before a destructive click lifted into a unit-tested library.

### Added
- `skills/enduser-handbook/assets/capture-helpers.playwright.ts` — `captureRegionClipped`, a bleed-free capture for an overlay taller than the viewport: one viewport-clipped screenshot instead of the scroll-stitched element shot that let a `position:fixed` page-behind re-composite across seams. Uses Playwright's `animations:'disabled'` for a deterministic settled frame, publishes atomically (temp-write + `rename`), and fails closed (unlinks the target on any error) so a PNG at the output path is always trustworthy proof. Pure clip math extracted to `assets/lib/viewport-clip.mjs` (`clampClipToViewport`, unit-tested; throws on any horizontal clip). Closes #18.

### Fixed
- `skills/enduser-handbook/references/publish-targets/obsidian-vault.md` — chapter image embeds are now DERIVED from `capture.output_dir` (`relative(dirname(chapter_file), join(capture.output_dir, <slug>, <file>))`) instead of a hardcoded `assets/<chapter-slug>/` prefix, so a profile whose `output_dir` is not `<chapters_dir>/assets` no longer produces 404 embeds. Adds an inside-the-vault boundary check plus POSIX-slash / never-absolute portability rules. Closes #153.

### Changed
- `skills/enduser-handbook/assets/capture-helpers.playwright.ts` — the `dismissModal` safe-negative-label gate is extracted to a unit-tested `assets/lib/dismiss-safe-label-policy.mjs` (`isSafeNegativeLabel` + frozen `DEFAULT_SAFE_LABELS` / `UNSAFE_LEADING_VERBS`), so a regression in the last check before a destructive-button click is caught by tests rather than shipped silently. Behavior unchanged. Closes #154.

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
