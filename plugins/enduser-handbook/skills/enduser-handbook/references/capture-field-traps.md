# Capture field traps

Field-tested traps from real capture runs, complementing [capture-safety.md](capture-safety.md),
[capture-spec-helpers.md](capture-spec-helpers.md) and [container-isolation.md](container-isolation.md):
each entry is a bold rule, the reason or the fix and, where one exists, the observable tell; an entry
that builds on an existing reference links to it. Read it before you write or debug a capture spec,
when a run is green but a screenshot looks wrong, and before you reuse a helper on a page it was not
written for.

## Masking and PII

- **A totals or "all" view names nobody and still publishes real figures: mask the numbers.**
  A fixture cannot help, because the aggregate sums it with every real row, and an invented figure
  is a fabricated shot. Assert that at least one cell was masked and that no remaining cell matches a
  digit, then re-capture and diff so that only the masked steps change.
  Extends [capture-safety.md](capture-safety.md#mask-reproducibly-then-prove-the-mask-held). Tell: a
  shot that names no one but shows amounts or volumes.

- **Mask the value element by name, never "the first non-blank text node".**
  A cell can hold a hidden label (a responsive label) before the value, so the placeholder lands
  there and the visible value is emptied. Scan the whole cell for a residual digit afterwards.
  Tell: a cell photographs blank or wrong while every count is green.

- **Assigning `textContent` deletes child controls, and a table cell can hold a whole hidden dialog.**
  An icon font draws its glyph from its text, so blanking that text erases the control. Mask text
  nodes per nested dialog, skip control glyphs and dismiss buttons, and never skip a hidden dialog:
  the scan reads off-screen text.
  Tell: a mask reports success and an icon is gone from the frame.

- **A mask that lists known names is a denylist: invert it to an allowlist of approved strings, and
  seed a fixture so no uncontrolled value is left to check.**
  `expect(rewrites > 0)` proves a rewrite happened, not that every name was covered. Use one
  selector per assert call (a union stays green on whichever still matches), read `.value` for
  inputs, and remember `toContainText` proves presence, not exclusivity. An allowlist only detects.
  Extends [capture-safety.md](capture-safety.md#mask-reproducibly-then-prove-the-mask-held). Tell: a
  new name reaches a shot and no assertion moves.

- **A full-list shot publishes every row: mask with a keep-list and prove coverage per row.**
  Blank the free-text cells of every row except the fixture rows. A cell total is satisfied by one
  row over-matching and one matching nothing, and `masked == total - keep` holds by construction:
  count rows on which every cell selector matched. On a global list the mask may mask zero cells.
  With no fixture row to keep, mask every free-text column on every row and assert the matched-column
  count; drive a current-period report to fixture-only periods.
  Extends [page-identity.md](page-identity.md#screenshot-guidance). Tell: a green leak assert over a
  list of free-text names.

- **Coverage is not targeting: open the PNG the first time a mask is wired to a list.**
  Selectors that name the wrong columns pass every count and publish the columns meant to be hidden.

- **Mask again inside any step whose clip can reach a data row.**
  A tooltip band can reach into the table body and was safe only because an earlier step masked.
  Keep masks idempotent.

- **Mounted-but-hidden content carries data: mask it right after load, before the first shot.**
  Row tooltips and inline modals sit in the DOM when not shown and a mask keyed on visible cells
  misses them. Rewrite the value nodes and keep the labels (a bulleted tooltip ships an empty box). A
  band shot taken before the per-tile masks publishes raw ids and junk labels.
  Extends [capture-safety.md](capture-safety.md#mask-reproducibly-then-prove-the-mask-held). Tell:
  found by eyeballing PNGs after many green runs.

- **Mask chrome names at page load, before an overlay that mounts in the same subtree opens.**
  A mask run with a dialog open also matches the dialog's row tooltips, and a mask that expects
  exactly one match throws. Assert the placeholder is present before later shots.

- **A DOM mask applied behind the framework's back can outlive a re-render of a keyed row.**
  The framework diffs old virtual text against new, never the DOM: the placeholder stays while the
  virtual text is unchanged, and Vue updates the existing text node when it differs. Pin cell
  identity once per page load, before masking, then assert only that the mask survived, and force a
  fresh document before a shot that needs the real value.
  Tell: a later step fails reading the harness's own placeholder, which looks like selector drift.

- **A guard's failure message is a disclosure surface.**
  Printing surviving values or the matched string copies the leak into logs. Compare inside the
  page and report counts, or a keyed hash with a per-run secret: an unsalted hash of low-entropy
  personal data is reversible.

- **A pattern leak assert cannot see free text: pin the exact value of every free-text field.**
  Assert `toHaveValue(LITERAL)` before the shot; an empty value counts, `toBeVisible()` does not. A
  pin detects drift and does not sanction the value, so pin only one already judged publishable, and
  a negative control proves the pin executes, not which element it hit.
  Extends [capture-safety.md](capture-safety.md#mask-reproducibly-then-prove-the-mask-held).
  Tell: one family of fields is pinned with a comment, a similar family is left to the pattern.

- **Scope each leak assert to the whole clip, not to the anchors it was built from.**
  A band is anchored on a first and a last locator while the clip spans everything between, and may
  start outside the container the page-load assert covered. Check containment per step.
  Extends [capture-safety.md](capture-safety.md#mask-reproducibly-then-prove-the-mask-held) and
  [capture-spec-helpers.md](capture-spec-helpers.md#what-each-helper-must-guarantee).

- **Every text instrument is blind to an uploaded image.**
  Mask the preview with an exact coverage count and describe it in prose; show it only after a human
  has looked, then pin a content hash.
  Extends [capture-spec-helpers.md](capture-spec-helpers.md#what-each-helper-must-guarantee).

- **An unsaved create form is not PII-free.**
  It renders database-backed option labels and free-text tenant fields; the exemption from masking
  belongs to the captured clip, never to the route shape.

- **A production-derived dump is not anonymised, and a committed PNG leaks through history for good.**
  Anonymise inside the capture step, not in the shared database, with a leak set wider than the
  rewriter and fail-closed counts returned from the page. Hide noise (a seeded list of person names)
  as well as the headline name, and have one reviewer per asset directory catalogue identifiers.
  Extends [capture-safety.md](capture-safety.md#the-pii-rule--capturepii_categories).

- **Re-capturing a synthetic shot on real data is a PII regression.**
  Scope a dump-backed recapture to the chapters that need the dump (per-record overlays) and keep
  the synthetic list shots.

- **Fix leak-scan false positives at the source, narrowly, and in order.**
  Shape fixture values so they cannot trip a pattern (a spaced reference number against an IBAN
  pattern), remove hidden sort-key spans (assert the count), or strip one pinned value, never a
  pattern family. Run identity patterns before stripping tokens whose removal only others need.

## Request guards and navigation

- **A navigation is not only `page.goto`: clicks, form submits, sort links and a `selectOption`
  that reloads by GET load fresh documents too.**
  Each re-renders the chrome with the user's name, which no pattern catches. A guard keyed to one API
  shape silently defines the surface as that shape: enumerate the shapes in the corpus. A lexical
  rule on a navigation-shaped follow-up catches clicks, but a lexical check is no proof; the fix is a
  runtime check at screenshot time. Exempt a false positive with a stated reason.
  Extends [capture-safety.md](capture-safety.md#mask-reproducibly-then-prove-the-mask-held). Tell:
  every `goto` is masked, yet click-driven navigations ship the unmasked header.

- **A browser-side guard covers the browser only; real read-only enforcement needs a boundary beyond
  it.**
  It misses a global setup's login request (its own closed context) and server-side calls a page
  makes while loading. The boundary (server-side refusal, or a disposable target) must refuse
  destructive GET paths too, because the redirect hole is a GET.
  Extends [capture-spec-helpers.md](capture-spec-helpers.md#what-each-helper-must-guarantee).

- **A spec's own `page.route` can bypass the context-level guard.**
  Page-level routes run first, so a handler that fulfils without `route.fallback()` never reaches
  the guard. The default `page` fixture fits badly (no service-worker block, page created before the
  guard) and a guard on a global setup's login context is inert: use one guarded fixture.
  Extends [capture-spec-helpers.md](capture-spec-helpers.md#the-spec-skeleton).

- **A repeat fetch served from Chromium's in-memory resource cache can fire the request event
  without reaching `route()`.**
  Seen on one Chromium build (version not recorded): a strict "observed is a subset of judged" check
  failed on one run and passed on an identical one. It is not the HTTP cache, which Playwright
  disables under routing. Tolerate an unjudged entry only if it is not a write and the same method
  and URL was already passed earlier in the run; never loosen the check beyond that.
  Extends [capture-engines.md](capture-engines.md#playwright-shipped-reference-case), which notes the
  cache bypass for the other engines only.

- **"Never submit a form" is not the whole rule, and a page of write controls gets a guard with no
  write path.**
  A writing GET behind a link and an AJAX POST fire with no form submit. Deny the GET by pattern,
  leave the AJAX write undeclared so the fail-closed guard reddens if it ever fires, and capture open
  states only: the run must be unable to write, not merely observed not to.
  Extends [capture-spec-helpers.md](capture-spec-helpers.md#what-each-helper-must-guarantee).

- **A path-pattern write guard that blocks an API path also blocks a script file named with the same
  word.**
  The page silently never mounts. Exempt only the bundler's output prefix, anchored (for example
  `^/build/`) and for GET and HEAD only; never exempt by extension or substring, because a business
  route can contain the same word. The audit-script entry in
  [Runner, container and suite](#runner-container-and-suite) applies the same rule.

- **A path allowlist matches the whole pathname: a parameterised path needs an anchored pattern.**
  A string copied from a sibling page's allowlist blocks the new page's legitimate read.

## Waits, animation and readiness

- **`animations: 'disabled'` fast-forwards finite CSS animations, CSS transitions and Web Animations,
  but not a script's timer: script-driven fades need an explicit wait.**
  A jQuery UI tooltip fades by a timer writing `style.opacity`: on open wait with
  `toHaveCSS('opacity', '1')`, on close with `toHaveCount(0)` (removed, not merely hidden), or the
  next step's shot carries a dissolving overlay.
  Extends [page-identity.md](page-identity.md#screenshot-guidance). Tell: washed-out text, then a
  ghost in the next shot, both runs green.

- **Identify the real widget class before waiting on it: a wait on a selector that never exists passes
  instantly.**
  A control that looked like a Bootstrap tooltip rendered jQuery UI's `div.ui-tooltip`, so a wait
  on `.tooltip` matched nothing. Probe the real node, wait for it opaque, then settle before asserting
  count zero, because parking the pointer is itself a hover.

- **A popover shot mid-fade composites the whole popover at the fading ancestor's opacity.**
  The card inside is opaque throughout, so a per-element guard passes while the page shows through.
  Settle the wrapper (`not.toHaveClass(/fade-\w+-active/)` plus `toHaveCSS('opacity', '1')`) and
  diagnose by dumping computed opacity along the ancestor chain.

- **`animations: 'disabled'` awaits nothing asynchronous: classify a modal by when its content
  arrives.**
  Classify a modal by the app's handler order: content injected before the dialog is shown means
  visibility already gates content; shown first, wait for the data element inside the modal
  (`waitForResponse` armed before the click), not its shell. Check an "empty modal" race claim first.

- **"Modal hidden" is not "modal gone": wait for four things.**
  Bootstrap drops `show` to start the fade and removes `modal-open`, the backdrop and the body
  `overflow` lock only on `transitionend`. Wait for no `modal-open`, zero `.modal-backdrop`, body
  `overflow` not `hidden`, and equal `scrollHeight` across two animation frames.
  Extends [page-identity.md](page-identity.md#screenshot-guidance). Tell: a sticky footer about 100
  px off its resting place in the shot after a dismissal.

- **Pixel-diff two frames before believing any theory about why a shot drifts.**
  The diff isolated one 100-by-100 block: a floating back-to-top control that scrolling raised and a
  timer fades; any scrolled viewport shot can contain it. A wait on the wrong element can go green by
  spending time, so compare hashes over several runs.
  Tell: the first shot after an interaction is the bad one, whatever options it carries.

- **`locator.isVisible({ timeout })` ignores the timeout: it is an immediate check.**
  As a tolerant wait for asynchronous content it races; use `waitFor({ state: 'visible', timeout })`
  turned into a boolean.
  Tell: a step skipped in some runs only.

- **A popper component can drop the first programmatic click: retry once, only while it is closed.**
  A second click re-opens a closed popover and does not toggle an open one shut.

- **A defect seen only under synthetic driver input must be re-measured with realistic input before it
  is filed.**
  A typed date "lost" on apply after `locator.click()` did not reproduce with a real mouse
  move, down and up (4 of 4): a test-driver artifact, not a product defect.

- **A label-shape wait is vacuous when row labels derive from the current filter.**
  After a view switch the old rows already carry the new label: compare the rendered values with the
  received payload, and wait for the loader to hide, before masking.
  Extends [page-identity.md](page-identity.md#server-rendered-pages-and-the-xhr-wait).

- **A `fill()` with the value a field already holds may send no request: read the handler first.**
  Playwright dispatches the input event either way, but some applications ignore an update that
  changes nothing, so a `waitForResponse` armed for it times out. It bites when consecutive steps
  pin the same value.

- **URL and load-state waits cannot see a navigation that lands on the same state.**
  A redirect back to the same URL makes `toHaveURL` pass instantly and `waitForLoadState('load')`
  resolves at once on a loaded page: arm `waitForResponse()` through `Promise.all` before the click.

- **A submit blocked by HTML5 validation sends no request.**
  `waitForResponse` times out with nothing in the log; list the blockers with
  `$$eval('form :invalid', ...)`.

## Selectors and text pins

- **A bare `form` selector matches the layout's hidden form first.**
  A shared logout form breaks strict mode, or `.first()` asserts the wrong element. A form around
  floated columns has a zero-height box: assert a leaf for identity and add `toBeAttached()` only
  beside it, never instead (an unmounted app root is attached too: assert the mounted root).
  Extends [page-identity.md](page-identity.md#the-principle).

- **Server-template whitespace leaks into attribute values.**
  A template conditional inside an attribute leaves double and trailing spaces, so an exact
  `[title="..."]` cannot match: use `[title^="..."]`.

- **Playwright normalises whitespace for string text matching, not for a RegExp.**
  `hasText: /^Label$/` fails against text the template put on the next line (an icon `<i>` often
  carries newlines). Use a trimmed string or `:text-is("...")`, exact and whitespace-normalised.
  Tell: an anchored regex matches zero elements whose text looks identical.

- **`textContent` and `innerText` include icon-font ligature words.**
  A ligature such as `clear` or `error_outline` prefixes a caption: strip it when comparing a
  caption, but include it in a whole-text pin.

- **`innerText` applies CSS `text-transform`: run regex and leak checks on `textContent`.**
  An uppercase transform made a secret-shaped pattern match a headline.

- **A fading list stays in `innerText` until it is `display: none`: wait for it hidden before the pin
  and the shot.**
  Assert `toBeHidden()`, never pin the options, and prove the fix with two consecutive green runs; a
  flaky pin invites loosening the pattern, which defeats it.
  Tell: a pin fails only on a frame right after a select interaction.

- **Do not identify an open dialog by class: attribute fall-through puts it on the backdrop, and
  several dialogs share one.**
  `.backdrop .dialog.cls` never matches; use `.backdrop .dialog` and pin the title or a body heading,
  and for a confirm dialog nested in its host modal the question text via `filter({ hasText }).last()`.
  A lingering `.modal.fade.show` intercepts the next click: dismiss leftovers at the start of a step.

- **A widget list populated on first open makes "masked 0" mean "nothing to mask": assert it empty.**
  One Chosen build renders `ul.chosen-results` empty until the first open, so masking its `li`
  masks nothing and reports success. Assert the lists are empty before every shot, the fail-closed
  direction.
  Extends [capture-safety.md](capture-safety.md#mask-reproducibly-then-prove-the-mask-held).

- **Appending a suffix to a comma-joined selector string reparses by CSS comma precedence.**
  `'a, b' + ' option'` means `a`, or `b option`. Splitting the string on commas is no fix: it breaks
  `:is(select, datalist)` and commas inside quoted attributes. Keep the selectors as an array (or
  split only top-level commas), append the suffix to each complete selector, then join.
  Tell: a count of 12 where the truth is 24, and nothing fails.

- **Check every selector-derived count against a number obtained another way.**
  A helper that matched zero reads like one with nothing to do.

- **A shared helper's exact-count precondition is a claim about the page it was written for.**
  Read its opening assertions against the new page's markup. On different chrome, capture the
  page's own fixed element as its own region and suppress the global widget locally, repeating the
  proof (exact count before, effect after, cleanup in `finally`); do not loosen the helper.
  Tell: the spec aborts before any PNG with a chrome-count message that does not say "wrong page".

- **`.filter({ has })` matches an ancestor and its descendant, and `.first()` takes the outer one.**
  Use a direct-child chain plus `toHaveCount(1)`. A `has:` sub-locator is relative to each
  candidate: do not repeat the page-root prefix inside it.

- **A visually hidden checkbox or radio input may or may not count as visible: assert the label.**
  Playwright treats `display: none`, `visibility: hidden` and an empty box as not visible, but
  `opacity: 0` and a screen-reader-only clip (a non-empty box) as visible. Assert the label the
  reader sees, not the input.

- **`.first()` on a page whose identity is a count stays green when all but one element vanish:
  assert the exact count.**

- **A target resolved dynamically at capture time can silently become a different record than the
  prose names.**
  A first row from an unordered query is the usual case. Pin the expected identity and let it fail
  loudly; when a relation loads unordered, seed exactly one related row.

- **Pin a known-wrong rendering as a mismatch, so the product fix reddens the run.**
  Where a chapter documents a defect it must not fix (a doubled heading), pin the broken string with a
  comment saying so. When the product is fixed the spec fails: the signal to update the chapter.

- **An assertion that cannot fail is decoration: compute what the broken page renders and make the
  check reject it.**
  A sort assertion passes on the unsorted page when the fixture's default order equals the asserted
  one. Add a second click asserting the reverse, prove it with a negative control (swap the
  expectation, watch the run go red), and start a probe from a state the behaviour must change.
  Tell: an expected value you could write down without running anything.

- **Restore a negative control from a copy made beforehand, never with `git checkout -- <file>`.**
  The checkout also wipes uncommitted edits other people have in that file.

- **A capture that reddens at a step you did not touch is usually product UI drift.**
  Find the rename with `git log --oneline -S '<selector>'` in the app repository, re-sync the
  selectors, drop prose notes the platform fix made stale and regenerate the shots. Only running the
  capture finds it: type checks and unit CI never drive a browser.
  Extends [running-ui-source.md](running-ui-source.md#implication-1-re-capture-when-the-ui-changes-even-if-steps-look-the-same).

- **Never alter the UI to photograph an invisible element.**
  An alert missing the class that makes it visible renders at opacity 0 and swallows the next
  click; that is a product defect to describe. Pin its state instead of adding the class.
  Extends [anti-fabrication.md](anti-fabrication.md#if-you-cannot-capture-it-say-so).

## Screenshot geometry and image checks

- **An application can have a run-to-run baseline of drift of its own: measure it before believing a
  drift report.**
  A three-run series on unmodified main drifted 8 of 30 PNGs, a different set per series even for
  byte-identical specs; a full run there rewrote 123. A single-series drift list samples that noise
  floor: run the same series on main, and call a shot fixed only if it drifts there and is stable
  after. Do not commit churn you cannot attribute.
  Referenced from [container-isolation.md](container-isolation.md#what-the-projects-command-must-guarantee).
  Tell: a drift list that changes between identical runs.

- **`page.screenshot({ clip })` without `fullPage: true` silently crops to the viewport and still
  writes a plausible PNG.**
  Read the written file's pixel size from its PNG header and compare it with the clip times
  `devicePixelRatio` within about two device pixels; a crop is off by hundreds. With `fullPage: true`
  the clip is in document coordinates: add `scrollX` and `scrollY` to `boundingBox()`.
  Extends [capture-spec-helpers.md](capture-spec-helpers.md#what-each-helper-must-guarantee). Tell: a
  caption describing four tables over a PNG that shows one.

- **A size gate proves size, not containment.**
  Rect containment passes for an invisible element (`visibility: hidden` keeps a box): await
  `toBeVisible()`. DOM ancestry is not geometry (`position` or `transform` push a child out of its
  ancestor) and the clip can change before capture: re-measure after and scan the enclosing rect.

- **An element shot of a container narrower than its content silently drops the overflow.**
  A fixed-width wrapper around a wider table loses the last columns with every assertion green. Clip
  on the content's own boxes with a containment check, at a viewport wide enough that the full-page
  canvas cannot clamp.
  Extends [capture-spec-helpers.md](capture-spec-helpers.md#what-each-helper-must-guarantee).

- **Bootstrap `.row` hangs half a gutter past its column, so a section clip shears.**
  Take x and width from the enclosing column's padding box and top and bottom from the section's own
  first and last element; accept a list of locators for width and pad per edge (a top pad of 0 is
  usually right).
  Tell: the frame sits half a gutter off, the size gate is satisfied, and a button can be cropped.

- **`locator.screenshot()` captures one element's box.**
  For a single table column, compute a clip from the table's and the first header cell's
  `boundingBox()` and use `page.screenshot({ clip })`.

- **Anchor a clip on the outermost visible edge of what it must contain.**
  A badge that overflows its icon box needs the band anchored on the badge, and a viewport fitted to
  an overlay's height must grow to a lower button row.

- **An element shot of a full-viewport modal container captures only the viewport box.**
  The body scrolls inside a fixed backdrop: scroll the target into view, re-assert the dialog title,
  then take a second shot (top and bottom).
  Extends [page-identity.md](page-identity.md#screenshot-guidance).
  Tell: the apply button is absent from a shot of a tall dialog.

- **An open native `<select>` popup cannot be screenshotted: plan the step out.**
  The operating system draws it outside the page. Document the options as a prose table and shoot
  the effect of each choice; forcing it (`size=7`, an injected fake list) fabricates a state. A
  widget wrapper's list is in-page, so check for one first.
  Extends [anti-fabrication.md](anti-fabrication.md#if-you-cannot-capture-it-say-so).

- **A viewport narrower than a layout breakpoint hides content.**
  A sidebar can vanish below one width and a table turn into a card list below another that is not
  the usual 768. Set the capture and smoke viewport above the breakpoints the page uses.
  Tell: a check passes at one viewport and fails at the harness default.

- **A visual anomaly in a downscaled tall screenshot is a hypothesis, not a finding.**
  A PNG thousands of pixels tall arrives downscaled and can seem to repeat blocks that are not in the
  file. Count dark pixels in the supposed duplicate (zero means blank) or crop the tail and read only
  the crop, and corroborate with the DOM count.
  Tell: you start building a tall-capture helper to fix something seen only in a thumbnail.

- **Eyeball every regenerated PNG against its committed predecessor; a hash or size match proves
  little.**
  A shot once showed the form row instead of the footer bar at identical dimensions. Look at the
  overlay shot and at the one after it: a drifting picture is a smaller defect than a wrong one.

- **Prove a shot is fresh by deleting it before the run and requiring it to exist afterwards.**
  A restored file that was never rewritten hashes as unchanged and reads as a pass.

- **When a re-run only moves seeded clock times, run-dependent ids or creation times, keep the
  committed PNGs.**
  Pixel-diff the bounding boxes: a whole-image box can still be only times plus anti-aliasing noise,
  so count the strongly differing pixels and look.

## Fixtures, seeds and teardown

- **A row count is not an identity, and a framework default's literal values are not one either.**
  `count() > 1` passes in exactly the state that loses data: the fixture row detached, one stranger
  attached. Diff actual ids against expected; stamp a marker in the seed (the same in teardown); keep
  identity and cardinality as two refusals; include soft-deleted rows; fail toward refusing, in a comment.
  Extends [capture-safety.md](capture-safety.md#synthetic-seed-data-must-be-hermetic).

- **Attribute fixture rows by a run marker in the data when the table has no owner column.**
  An uploaded file name carrying a run token lets teardown find what this run created. Delete a
  fixture account only when two identity keys both match, with no override.

- **A seed is create-only: it refuses when matching rows exist instead of deleting them.**
  A name prefix can be real data and no predicate proves a script created a row (the environment name
  labels the process; connection settings pick the database). Split seed from teardown. A refusal is
  a stop: report it, because running the teardown over rows this run did not create is the user's
  ownership decision. Guard both on the resolved connection (host and database name against an
  allowlist of local, disposable databases), not on the environment label.
  Extends [capture-safety.md](capture-safety.md#synthetic-seed-data-must-be-hermetic). Tell: the seed
  stops with a refusal saying that accounts with the fixture usernames already exist.

- **Seeded rows must also be inert to scheduled jobs, not only to create-time observers.**
  A daily scheduler can send real mail for them: check that each job's query excludes the row (an
  empty expiry date or an optional flag off).
  Extends [capture-safety.md](capture-safety.md#synthetic-seed-data-must-be-hermetic).

- **Scheduled jobs can delete pinned fixtures: compute their death date.**
  A nightly job that soft-deletes by age gives every pinned record an expiry. Repair rows with the base
  query builder (a model restore fires observers), only rows your seed stamped, in the local capture
  database. The soft delete bumped `updated_at`, so a repaired row tops a default-sorted list: never
  pick a card by position, and pin steps by id, not by hash.
  Extends [capture-safety.md](capture-safety.md#synthetic-seed-data-must-be-hermetic).

- **A pinned step must fail, never skip and keep the old PNG.**
  A guard that counts an annotated skip as explained keeps the old file, so a drifted pin
  republishes a stale frame green. Pin a step only when its frame carries the record's identity.

- **A control that renders only for records without dependants needs its own seeded record.**
  A delete button shown only at a child count of zero renders on no imported record. A join table
  without a foreign key survives cascading deletes, so teardown clears it by id first; seed, capture,
  teardown must round-trip to the original row counts.

- **A page whose state depends on one row's presence shows two states at one URL: one run yields two
  byte-identical shots.**
  Split it into two filtered tests with explicit state asserts and change the data between them.
  Extends [state-variants.md](state-variants.md#populated-is-the-default-variants-are-opt-in).

- **Override every randomised factory default that a shot renders.**
  Defaults surface as jarring values (a person name in a product column) or random flags: set clean
  demo names and seed observe-only fields. Once a spec is robust do not re-seed between runs, because
  faker regenerates everything and makes leak asserts and skips flaky.

- **A seed piped to a REPL through stdin can throw parse errors on punctuation.**
  Seen with `artisan tinker`: an unexpected `"` after typographic quotes or an em dash inside
  double-quoted strings, and after joining a class constant with `.`. The cause was not isolated, so
  treat it as an input trap of that transport, not a PHP rule. What worked: ASCII text and `{$var}`.

- **Use fixed dates, not run-relative ones.**
  Fixed dates keep status labels byte-identical across re-captures; run-relative dates move every
  shot and every caption quoting one. Do not self-check a fixed-date fixture against a rolling
  window, which it falls out of after a year.

- **A page that is empty on the bare seed can hide an error that fires once rows exist.**
  Seed first. If a freshly populated list fails, suspect a relation the query did not load while the
  view reads it (a strict lazy-loading violation outside production): the fix is in the app.

- **A spec that really saves must run only through a wrapper that snapshots, captures and restores
  from an exit trap.**
  The spec refuses to start without the snapshot flag, such specs never run concurrently, and the
  restore is verified by diffing the touched tables against the snapshot. Run it only against a
  local, disposable seed database (never production, staging or a shared environment), with every
  non-DB side effect stubbed (log mailer, sync queue, empty API keys) and a coordination window held;
  see [Runner, container and suite](#runner-container-and-suite). The rules of capture-safety.md
  still govern.
  Extends [capture-safety.md](capture-safety.md#screenshot-the-warning-dialog-not-the-after-state).

- **Flush application caches after any data swap or fixture flip, and restore them exactly.**
  An import under a running app leaves settings and permission caches stale, so gated overlays
  resolve against old data; a setting cached forever needs its cache tag flushed on flip and on
  teardown. Reload long-running workers if a cleared cache is only rebuilt at boot.
  Tell: a cache clear blanks the page's scripts until the server reloads.

- **A recovery path written in a comment is a claim until it has run.**
  A seed that records before it commits only recovers if teardown recognises a rolled-back run.

- **A restored dump carries uploaded file names but not the binaries.**
  Logos and attachments may 404 locally whichever record you pick.

- **Never hardcode the capture account's real login e-mail in a seed script.**
  It ends up in the repository history.

## Runner, container and suite

- **Run the project's whole capture lint and unit suite before reporting done, also when several agents
  write specs.**
  Repo-wide rules, such as every navigation being followed by a mask, live in other test files, so
  a targeted run cannot see them. The suite is filesystem-only and cheap: require it plus the type
  check in every capture-spec brief.
  Tell: a dozen raw navigations surface at the lead's full run after the merge.

- **After a shared capture-helper change, run the whole capture suite and diff the failing-spec set
  against unmodified main on the same data.**
  Raw failure counts depend on seeded data, so compare sets: anything only on the branch is the
  regression, and PNG rewrites on an unchanged tree are baseline noise. A single spec's result is
  order-dependent: re-run it alone on both sides. Where no CI job captures, this run is the only gate.

- **After merging main, skip the re-capture only for a documentation-only merge.**
  The committed PNGs stay valid only when the merge touched chapters or indexes alone. A template,
  CSS or label change in the application, or a change to shared capture code, means re-capture.
  Extends [running-ui-source.md](running-ui-source.md#implication-1-re-capture-when-the-ui-changes-even-if-steps-look-the-same).

- **Fail the run when a manifest step wrote no fresh PNG, unless it carries an explicit skip
  annotation.**
  Unexplained is a hard failure; explained is printed, since some reporters drop annotations. A
  pinned step whose pin drifted is not a legitimate skip. A caller that legitimately writes nothing
  (a smoke suite) opts out with an empty expected list. When a shared helper gains default-on
  behaviour, grep its callers by function name across the whole tree.
  Extends [completeness-gate.md](completeness-gate.md#block-vs-allow-rules-for-publish).

- **Add one data-independent smoke test per chapter, on a bare CI seed.**
  It navigates, asserts it was not sent to login, and checks the heading, structure and exact-count
  fingerprints with selectors from one module shared with the capture specs: a new control moves its
  selector there, never copies it. Count chapters from the manifest, never from memory.

- **Every smoke structure selector must be visible on the bare seed.**
  Fields in a block hidden unless a setting selects it, and markup in a loop over rows the seed lacks,
  fail only there: assert an always-rendered control. Walk the whole entry, since the test stops at
  the first failure; a local run on a data-rich import proves nothing about the bare seed.

- **A smoke asserts only rights-independent chrome, and a page without app chrome needs a variant
  with no header.**
  Every tile and menu entry depends on rights and licence switches, and an empty title renders a
  hidden heading: identify the page by an always-rendered control.

- **Select one spec by its exact file name, not by a slug substring.**
  A bare `orders` also matches `orders-archive` and regenerates its screenshots, which you then
  stage by accident; with a catch-all `testMatch` a bare run regenerates every chapter.
  Scope: only for naming one spec in a wrapper that accepts one; never a licence to narrow a
  whole-suite `capture.command` the profile says to run verbatim, see
  [revalidation.md](revalidation.md#the-flow).

- **A non-default spec suffix needs `testMatch`.**
  The default matches `*.spec.*` and `*.test.*` only, so without an override `playwright test --list`
  shows none of the capture specs. Set `testMatch` in the config and confirm with `--list`.

- **Playwright empties its `outputDir` at run start: never keep a wrapper's lock, log or state there.**
  A lock kept in the default `test-results` vanishes, the exit trap's teardown redirect fails, the
  teardown never runs and the next seed refuses. A worktree run hides it (it cleans its own directory)
  as does an explicit log directory: reproduce in a disposable clone with default paths, then run the
  teardown by hand, never from the shared main checkout. Write comparison artifacts outside that
  directory too.

- **The official Playwright image ships only the `C`, `C.utf8` and `POSIX` locales, so a process
  locale pin selects nothing.**
  That includes the `-e LANG=<locale>` pin suggested in
  [container-isolation.md](container-isolation.md#common-command-patterns-engine-agnostic):
  `setlocale` fails and falls back. Check with `docker run --rm <image> bash -lc 'locale -a'`. What
  changes a screenshot is the browser-context locale, and changing a shared one re-renders every
  chapter: plan a full re-capture and PNG diff.
  Extends [container-isolation.md](container-isolation.md#what-the-projects-command-must-guarantee).

- **`docker run -v <missing host path>:<container path>` creates the host path as an empty directory.**
  A later `ln -s target name` nests the link inside it and succeeds silently; the symptom is
  `error TS2688: Cannot find type definition file for 'node'`. Check `ls -ld name` (`d` versus `l`)
  and remove the unintended empty directory with `rmdir` before you create the symlink.
  Extends [container-isolation.md](container-isolation.md#capturing-from-a-git-worktree).

- **A dangling dependency symlink inside a container looks like a stale install.**
  `tsc: not found` appears when a worktree's `node_modules` points outside the mount, for type check,
  lint and unit gates as much as capture. Overlay the real modules, or verify with an anonymous
  volume at `/app/node_modules` plus `npm ci`; never reinstall into the shared target.
  Extends [container-isolation.md](container-isolation.md#capturing-from-a-git-worktree).

- **When the shared host browser is contended (never reap another session's browser), run a
  throwaway audit script in the capture container.**
  Keep it read-only (never submit or confirm, Escape only) and print structure only (counts,
  booleans, selector names), never page text. Log in from an env file that is gitignored and outside
  the repo, and exempt only the bundler's output prefix from its GET-blocking regex, as in the
  path-pattern guard entry in [Request guards and navigation](#request-guards-and-navigation). Wrap
  each probe in its own `try/catch` and print once at the end: one mid-script timeout otherwise
  discards every observation. Delete it afterwards.
  If a permission layer refuses a step, stop and ask the user (or hand them the command); never
  re-route a refused step through a container or a script.
  Extends [container-isolation.md](container-isolation.md#common-command-patterns-engine-agnostic).

- **Two `docker run` mistakes cost whole runs.**
  Dropping the image name makes docker read the next word as the image (`Unable to find image
  'npx:latest'`, exit 125). Piping a long run into `grep` or `head` keeps the call alive to the
  tool's time limit: redirect to a file and search the file.

- **Stub an external API that a page calls on load with a private app container, never by editing the
  shared environment.**
  A hard-coded host, or an SDK with no base-URL setting, ignores the base-URL variable: map the live
  host to loopback (`--add-host`) or overlay the configuration. Give the container its own cache prefix,
  sync queue, log mailer, empty API keys and a higher rate limit (about 25 reads per load hit 429).

- **Parallel wrappers need unique container names, and cleanup removes containers by recorded id.**
  Two stubs under one alias get DNS round-robin. Record the id with `docker run --cidfile` and
  remove by it, never by name: a concurrent run shares constant names and your exit trap would
  delete its container. Docker writes no cidfile on a name conflict.
  Extends [container-isolation.md](container-isolation.md#capturing-from-a-git-worktree).

- **A capture run that writes a shared database needs a coordination window.**
  Tell parallel sessions before and after, hold DB-writing runs while another holds the window, and
  take a lock so a second run of the same mode exits before it seeds.
  Extends [container-isolation.md](container-isolation.md#capturing-from-a-git-worktree).

- **Reproduce CI-only unit failures in the CI image, not the local capture image.**
  A unit job on a minimal Node image failed a test that spawns `bash` with an empty reason while it
  passed locally; the job needed `bash` installed explicitly.

- **Retry a browser-launch crash once before debugging.**
  A headless browser died with SIGSEGV at launch, one spec failed before running any code and an
  immediate re-run was green.
  Tell: a failure before the first line of the test body.
