# Authoring field traps

This file collects field-tested traps from real chapter work that the other references do not catch; it complements `anti-fabrication.md`, `completeness-gate.md`, `glossary-discipline.md`, `manifest-discipline.md` and the publish-target adapters. Read it before you declare a chapter done: each entry gives the rule, the reason and, where one exists, the observable tell that you are inside the trap.

## Verifying behavioural claims

- **Re-read the finished chapter sentence by sentence and check every behavioural claim against the code that runs, not against the screenshot or your own summary.**
  The coverage gate checks which surfaces are documented; nothing re-reads a finished sentence and asks whether the application really does that. A review of a "complete" chapter found a dozen defects, nearly all invented behaviour in connecting prose. Review prose like code, and call a chapter done only when the pipeline is green and every reviewer thread is resolved.
  Tell: a green pipeline, and a reviewer still finds documentation defects.
  Extends [anti-fabrication.md](anti-fabrication.md#the-trace-to-evidence-rule): that rule traces steps and labels, not behavioural sentences.

- **Take reversibility and defaults from the code that decides them, never from a label, a template line or a screenshot.**
  An import grouped with delete as "irreversible" only rendered a preview and persisted nothing. A template line that read "unchecked for a new record" ignored a controller that preset every value, and a default read off a screenshot was one the capture's own reveal step had set. A default is a fact about controller, model and template together; a brief that repeats such a claim is still a claim.
  Tell: the capture spec's own comment contradicts the sentence next to its screenshot.

- **Read the meaning of a status, a filter or a flag from the query behind it, not from its wording.**
  A "waiting" status held nothing back because the publish query looked only at the end date; an empty end date was listed as "inactive" while the query treated it as always live; a "finalized" flag was true for items with no result; a date filter changed only what was counted, never which rows appeared.
  Tell: the expression that computes a status and the query that publishes the items disagree.

- **A worked example in the chapter must obey the rule printed beside it.**
  A chapter warned that a comma-separated value is discarded as one invalid address, while the seeded example in its screenshot was comma-separated. When a chapter states a format rule, grep the seed for that field and pin the rendered value so the example cannot drift back.
  Tell: a screenshot value that the neighbouring sentence says is invalid.

- **Trace a field to both ends before you document it: what stores its value and what reads it back.**
  A select outside any form and absent from the validated keys saved nothing; a tooltip promised template variables the builder never supplied; fields "locked after the first save" could be unlocked by a client script that markup and request rules did not show. A save that also writes keys the form never renders (blanking them) belongs at the top of the chapter. Confirm each defect from two independent sources before the chapter discloses it.
  Tell: a control that looks like a working setting whose value no code reads.

- **Trace each displayed figure to its own query, and never turn a fixture's state into a product rule.**
  Three amount columns followed two different period rules, and a document created by an action was not computed like the open amount shown beside it. A fixture with month-long periods became four false sentences. A value can be a stored snapshot while its colour is computed live from today's date and a setting; do not restate a gate's arithmetic when a cell can legitimately print a value that seems to contradict it.
  Tell: a sentence that holds for the seeded period and fails for another.

- **When two pages show similar balances, say which one a page shows and which action consumes it.**
  One page carried planned values with their own open amount and document-creation action; a sibling billed imported actual values with a different open amount. A sentence sending the reader from one balance to create the document under the other was false, and only an automated reviewer caught it.

- **When one record pool feeds several consumers, document each consumer's behaviour, or scope out the ones the page cannot know.**
  One template pool fed three senders: one took the subject from the template name, one from the subject field with variables substituted, and one depended on a dispatcher that passed no context, so nothing was substituted. Assuming uniform behaviour caused most review findings.

- **For every delete control, read the foreign-key actions behind it; the confirmation text will not mention them.**
  A generic "are you sure" dialog deleted every message in a category, and deleting a template removed a whole settings row through a cascading column. A trash icon is not evidence the record is unused.
  Tell: identical confirmation text for a single record and for a cascade.

- **Trace a cell that has no visible label to its data source before the prose names it; how a value looks is not what it means.**
  A label-less cell on a card showed a word that looked like a customer segment; it was a partner's name. Two drafts named it wrongly in turn (an invented field, then a different entity). Read the component and the API resource that feed it.
  Tell: successive drafts or reviewers name the same cell differently.
  Extends [glossary-discipline.md](glossary-discipline.md#mandatory-extension), which says to halt and ask when you cannot define a term.

- **Verify small interaction facts before stating them: what an icon signals, which way an arrow points, and whether a key closes a widget.**
  An eye icon was crossed out while its box was open, so prose said "open eye" the wrong way round; a sort arrow pointed up for descending; eight columns shared one glyph that differed only in colour. Name the state ("box expanded"), not the glyph, and do not claim that Escape closes a menu unless you checked its focus behaviour.
  Tell: the wrong sentence survives several review rounds and a late round catches it.

- **Never quote or translate a label the browser draws; describe the control instead.**
  A file input shows its caption and its "no file chosen" text in the browser's locale, not the app's (English in a capture image that lacks the locale), so a chapter in another language cannot quote it and must not invent a translation.
  Tell: English captions in a screenshot on a page whose chapter is written in another language.
  Extends [anti-fabrication.md](anti-fabrication.md#the-label-fidelity-rule), which covers the app's own labels.

- **Do not enumerate failure causes the application does not distinguish.**
  A handler that catches every exception and redirects with one generic message lets the chapter name that message and nothing more.
  Tell: one message for every kind of failure.

- **When one component serves two pages, verify per route which behaviour applies, and write nothing for a branch the audience cannot reach.**
  The same bundle served an administrator page and a user page and branched on the path; a role-specific rule in it was unreachable because the middleware refuses that role.

- **Re-measure a suspected defect with realistic input before it enters prose or a tracker.**
  The measured case and the input sequence that settled it are in [capture-field-traps.md](capture-field-traps.md#waits-animation-and-readiness).
  Tell: a defect that only the automation driver triggers.

- **Make sure the check behind a behavioural claim can fail on the broken page.**
  A sentence such as "clicking the header sorts the list" needs a check that rejects the unsorted rendering; a probe that ran after a manual click on the very tab it claimed to select proved nothing. The method (compute what the broken page renders, add a discriminating step, prove it with a negative control) is in [capture-field-traps.md](capture-field-traps.md#selectors-and-text-pins); apply it to every claim you verify, not only to capture specs.
  Tell: an expected value you could write down without running anything, over a fixture order you also chose.

- **Count numbers with a command and re-check version claims at the moment you write them; never quote a remembered total or copy a claim forward.**
  Chapter and suite counts grew from 40 to 53 in a week; remembered totals reached a plan and a commit message and had to be corrected, and a stale tool-version claim was copied forward. The same holds for index sentences that count what is not yet documented.
  Tell: a number or version in a brief that you did not just derive.

## Coverage and scope

- **For every step that tells the reader to change something, test that doing it has the effect the text implies; reachability is the weaker test.**
  A walkthrough of a settings page was reachable, honestly photographed and inert: the price computation ignored that setting whenever an external source supplied the value, and the only record with rows in the audited data used one (real imported data, not a seeded record). The matrix asked "can this be reached and shown"; three plan-review rounds reasoned about capture safety and image truth, never about effect.
  Extends [completeness-gate.md](completeness-gate.md#the-audit-walk-through).

- **Check assets in both directions: every image on disk must belong to a manifest step, not only every step to a file.**
  Every gate ran from step to file, so four leftover images passed the whole suite; add a file-to-step check.
  Tell: an image on disk that no manifest step produces.
  Extends [completeness-gate.md](completeness-gate.md#block-vs-allow-rules-for-publish), whose block rules run from step to file.

- **Scope an area by walking every row of its hub page, including rows that point at pages no menu reaches.**
  A settings hub injected four rows that led to pages other sections own; no menu listed them, so the hub was their only entry point and they belonged to the same area.

- **A page the capture role is refused on cannot be documented from capture; say so, and budget a second account.**
  Pages restricted to non-administrators answer the administrator capture role with a forbidden response, and one related list page was unreachable the same way. Claims about such pages are source-verified only; use a role the page serves, or mark the claims as read from code.
  Tell: a forbidden response during the audit pass.

## Permissions and security wording

- **Never write that a page stays reachable by its address when a right hides its menu entry; describe the menu effect only.**
  Such a sentence documents an authorization bypass for every reader. Report the gap to the product owners through the normal channel. Three plan and code review rounds missed it; the closing security pass caught it.
  Extends [running-ui-source.md](running-ui-source.md#implication-3-permission-leaks-are-bugs-not-features): leaks are bugs, and the prose must not publish them.

- **State a page's prerequisites from the route's own authorization check, not from menu visibility, and name a system-wide switch as a switch.**
  A user-independent feature flag reads like a per-user right; calling it "the right to X" puts a false prerequisite and a wrong troubleshooting step in the chapter. Menu, start page and route often test different conditions, including the enclosing section's condition. A right's label can differ from what it gates, and it may gate more than the page.
  Tell: a troubleshooting step that tells the reader to request a right that no per-user setting grants.

- **Word a permission as required, not sufficient, unless you have proven sufficiency.**
  "Right A is required to grant administrator access" is safe; "right A grants it" needs proof, because licence switches and menu-only rights may also apply.

- **Do not describe how a third party could abuse a behaviour.**
  A sentence explaining how an outsider could misuse a behaviour against another user was flagged as a blocker; keep only what the user experiences. Do not publish side effects of hidden or deactivated features, what a link grants, or what a page checks for signed-in users.

- **Say nothing definite about access rules or deadlines that are still open product questions, and name no tracker items in the prose.**
  A chapter that ties an action to "being signed in", or relates two dates, answers a parked question by implication. A closing security pass caught it.

- **Describe what happens, never the function, class or route that does it.**
  "Cancel is a history-back link, not a reset" states the behaviour; naming the handler adds developer detail and, for internal paths, publishes structure. The glossary code field has the same rule (see "Glossary and navigation wiring").

## Parallel chapters and shared files

- **Parallel chapters collide on shared navigation, index, manifest and glossary files even when their content is disjoint, and the exposure window is the whole review.**
  Worktrees isolate a chapter file and its assets and nothing else. A chapter that sat through several review rounds found its base far behind, with a whole group of chapters merged meanwhile. Before you push, list the commits on the target branch since your base and diff the shared files against its tip; agree order positions with parallel branches up front, land them one at a time and wire the second after rebasing.
  Tell: the merge base is many commits behind when the chapter is ready.
  Extends [manifest-discipline.md](manifest-discipline.md#shared-edit-hotspots).

- **Rebuild a conflicted structured file (a manifest, an order list, a code array) from the target branch's tip, then re-apply your own additions and review the result; merge only purely additive markdown lines by hand.**
  Entries in such files share prefix and suffix lines, so markers miss entry boundaries: a hunk can straddle two blocks, a union leaves the first unclosed, and a blind marker drop fuses two entries. Starting from the target's tip, not from whichever side has more entries, keeps its corrections to existing entries (a corrected title), which no slug check sees. For a long branch regenerate each file as `git show origin/main:<f>` plus `git diff <old-base> <our-head> -- <f> | patch -p1 -F3` and fix only the rejects. Verify every slug, diff the result against the target's tip so that only your additions differ, and run the type check. Index and glossary bullet lists that both sides only appended to merge by keeping every bullet.
  Tell: duplicate keys or a missing brace after a "keep both" resolution.
  Extends [manifest-discipline.md](manifest-discipline.md#shared-edit-hotspots), which resolves additively and re-runs the type check.

- **The silent half of a collision is prose that counts or enumerates what is not yet documented.**
  Two branches each wrote "the remaining two items" with different names; merged, exactly one item remained and every gate was green. The same class spans files: one branch corrected a fact in a chapter while another branch's new glossary entry restated the old fact, with no conflict. After merging, recount such sentences and diff the target's changes to every chapter your new entries describe.
  Tell: a green pipeline and a sentence that is true on each branch alone.

- **After sibling chapters land, repair the pointers in both directions and run one review for contradictions between siblings.**
  Newly merged chapters said "not yet documented" about your topic, and yours named a dialog without a link; a post-merge review round found two real contradictions. Check that nothing was silently reverted: `git diff --numstat origin/main HEAD | awk '$2>0'` must list deletions only where you edited.

- **Do your git work and run your tools from your own worktree, never from a shared checkout that another session may have on a different branch.**
  Switching branches in a shared checkout drags another session's uncommitted tracked changes along and strands them on your branch; even read-only commands there make "which branch am I on" unanswerable. Leave foreign changes alone: never stage, restore or commit someone else's work. An isolation option that worktrees the session's own repository does not isolate a nested one.
  Tell: modified files you never touched after a branch switch.

- **Review tools that run git from the session root read the wrong repository when the work sits in a nested worktree.**
  A built-in security review once reported "nothing to commit" and once assembled a full, plausible diff of a different change set because the session repository was dirty on an unrelated branch. Check the branch in the report's git-status block against yours; feed the tool the real diff, or state the worktree path and commit range and say that the working directory is not the repository under review.
  Tell: the report names a branch or files you did not change.

- **In a shared checkout or scratch folder, act on exact names: restore from a copy, move files one by one.**
  A glob move took other sessions' screenshots from a shared tool-output folder, and undoing a negative control with `git checkout -- <file>` wiped a teammate's uncommitted edits in that file; the copy-first method is in [capture-field-traps.md](capture-field-traps.md#selectors-and-text-pins).
  Tell: a teammate's change disappears after your cleanup.

- **When you extract a handbook into its own repository, sever cross-repo links first and verify the new repository before you delete from the old one.**
  Give the new repository its own lockfile and a copy of shared dev dependencies; untrack files before ignoring them (ignoring does not untrack); cut the extraction from the branch that actually holds the files; do not import the app's end-to-end login setup across the boundary. A gitignored sibling clone keeps the developer repository free of handbook files while capture still reaches the app's running stack.
  Tell: a wikilink that crosses the repository boundary.

## Glossary and navigation wiring

- **A glossary term is wired in several places and no check compares them; script the cross-check after every wiring change.**
  Definition block, summary row, chapter frontmatter terms and capture-manifest terms can disagree while the navigation check, the linter and the unit tests stay green; a definition without a summary row passes too.
  Tell: older entries that have a definition but no summary row.
  Extends [completeness-gate.md](completeness-gate.md#surface-enumeration-mechanical-first-pass), which compares frontmatter against the manifest only.

- **Do not publish how an identifier is built, class paths or internal route paths in a glossary entry's code field.**
  On a public or shared site that is structure an attacker can use and a reader does not need; one review removed the character set of an id. Keep the identifier a short English name.
  Extends [glossary-discipline.md](glossary-discipline.md#english-code-identifier-when-required), whose examples name class and route names: choose an identifier a reader may see.

- **Verify a glossary definition like chapter prose, and state its conditions.**
  A definition said two values were "the same" without saying that this holds only when no configurable rule applies; the claim was fixed after review.

- **Check what a term really defines before you link it from a chapter.**
  A term named like an everyday quantity defined an unrelated regulatory value; linking it from a chapter about that everyday quantity misled the reader.

- **When two entities have near-identical names, add an explicit "not to be confused with" line to the chapter and to both glossary entries.**
  Two record types whose names differ by one word collide badly.

- **Place and name a page by what the running UI shows; a route prefix or a route name can mislead.**
  A menu entry sat under an unexpected section despite its URL prefix, and a route called "templates" was the page the menu calls "rules". Take the group from the menu the user sees. When menu label and page heading differ, record one as canonical and the other as a synonym, as [glossary-discipline.md](glossary-discipline.md#the-one-canonical-term-rule) describes.

- **Handbook navigation should mirror the app's menu order; when the app shows two different orders, that is a decision for the owner, not a lookup.**
  Two surfaces rendered the same grid in opposite reading orders (column-first and row-first). Keep one order source guarded by a check that also compares the index pages' lists with it; a new group's position comes from that source, not from where it is convenient to append, and a sub-page chapter sits right after the page it is reached from.
  Tell: navigation falls back to alphabetical order.

- **A navigation check covers only the sites it knows; a literal list inside a test and a forward reference slip past it.**
  A unit test hard-coded one group's chapter order, so the navigation check passed while the unit test failed, and a rebase that took the other side's test file reverted it silently. A chapter wired in before it exists fails as a stale entry, so no batch pre-wires a chapter it does not create. List your handbook's hand-maintained wiring sites and touch each one.
  Tell: one check green and another red for the same missing entry.

- **When an end-user area and an administrator area share a group slug and title on purpose, key the groups check on the slug alone and qualify only the order entries by area.**
  One handbook gave both areas a group with the same slug and title for the same topic; with that split, the end-user group sat where the end-user menu puts it.

## Static-site publishing

- **Under a path prefix, the generator's base URL must carry the path, or every navigation link 404s.**
  Served below a path, Quartz's explorer emits root-absolute links; a bare-host `baseUrl` drops the prefix. Make the build check prefix-aware so it fails on a prefix-less absolute link. A project and a content folder with the same name produce a redundant URL segment; it works, and flattening it means rewriting link prefixes.
  Tell: pages exist at the prefixed URL while navigation links point at the root.

- **Verify the rendered navigation only under the real base path.**
  The explorer sorts client-side from a content index fetched through the prefix; served at the root those fetches 404 and no sidebar renders, so a root-served preview proves nothing. Build with the CI steps, copy the output into `<dir>/<prefix>`, serve `<dir>`, and read the order from the DOM. Judge expansion from the rendered DOM or a screenshot, not the static attribute.
  Tell: no sidebar at all in a local preview.

- **Pin the generator to a validated commit, not a release tag, when the tag lacks a command your build uses; re-validate the full build before every bump.**
  A Quartz 5 release tag lacked the install-from-config argument the build called ("Unknown arguments").
  Tell: an unknown-argument error from the build.

- **Custom CSS must be copied into the generator's build clone, and already-compiled Sass layout variables cannot be overridden from it.**
  When the build clones Quartz and overlays only the config and lock file, a repository-root `custom.scss` is ignored unless CI copies it into the clone's styles folder after the config copy and before the build. A Sass variable resolves at compile time, so reassigning one (for example the side-panel width) from custom CSS does nothing: override the rendered rule at the generator's own breakpoints (mobile up to 800, tablet 801 to 1200, desktop from 1201, contiguous). Runtime CSS custom properties such as `--light`, `--bodyFont` and `--darkgray` are not compiled away and can be overridden.

- **Under Quartz's `shortest` link resolution, `../` embeds break at depth two or more; the adapter's remedy is to switch the vault to `relative` resolution.**
  A chapter two folders deep with `../assets/` emitted `../.././assets/`, which resolves to the site root, and the build check reported a missing image. `absolute` mode breaks relative image paths and anchors for path-prefixed wikilinks, and a root-absolute path is wrong under a prefix. As a field observation only: assets kept beside the chapter with same-directory embeds also rendered correctly under `shortest`, though the adapter weighed that co-location and declined it.
  Tell: a "missing image" for an embed that exists on disk.
  Extends [obsidian-vault.md](publish-targets/obsidian-vault.md#grouped-chapters-and-quartz), which documents the flip to `relative`; it can also change how bare wikilinks resolve elsewhere in the vault.

- **Give every navigation folder an index page with a clean title, and order the explorer with a YAML string, not frontmatter.**
  A folder's sidebar label is its index page's title; the default order is alphabetical. A YAML block scalar holding an arrow function works as the explorer's sort function but not as the folder-page plugin's sort option, which runs at build time; frontmatter order is impossible because the content index carries no custom keys. Disabling the folder-page plugin removes every folder index page, so hide its auto-generated list with CSS.
  Tell: two contradicting lists on a section page.

- **Keep the frontmatter-parsing plugin on and hard line breaks off, inject titles from the first heading, and collapse wikilinks split across lines.**
  Disabling the parser renders frontmatter as a heading; hard line breaks on hard-wrapped prose force a `<br>` per line; the generator titles pages from file names unless a build step injects the heading; Obsidian tolerates a wikilink split across lines but Quartz leaks it literally.
  Tell: frontmatter rendered as a heading, or a wikilink printed literally.

- **Never write angle-bracket placeholders such as `<Word>` in chapter prose; the site renders them as HTML and the word vanishes.**
  Use an ellipsis or plain words. Verified in the build artifact.
  Tell: a sentence with a missing word on the published page but not in the source.

- **In Obsidian, escape the alias pipe of a wikilink inside a table cell as `[[target\|alias]]`, only inside the double brackets and only on the flagged lines.**
  Unescaped, the alias pipe is a column delimiter: markdownlint reports MD056 and the table renders wrong. A lint job allowed to fail lets such rows merge green, so run the linter yourself. Apply it line-scoped, because the same alias in prose is valid and must stay unescaped: `perl -i -pe 's/\[\[([^\]|]*)\|([^\]]*)\]\]/[[$1\\|$2]]/g if $. == N || $. == M' <file>`, then compare lint output before and after.
  Tell: MD056 findings after adding a table row with a wikilink.

- **Under a subpath, robots.txt is not read; add a per-page noindex meta and verify the live served HTML before you open access.**
  Crawlers check only the origin root, which the project does not control. GitLab 17.10 and later accept a read-scoped access token as a Bearer header on an access-controlled site, so the served HTML can be verified while the site is still private. Pass the token from a file or stdin (for example curl `-H @file` or `--config -`), never on the command line or into logs, and revoke it after the check. Switching the site to public access is the owner's decision: ask for it only after the screenshot clearance, then check once more unauthenticated at the canonical URL, with no cache-busting parameter and a rollback trigger.

- **A noindex tag keeps a site out of search results; it is not access control.**
  Anyone with the link reads a public site, so clear every screenshot for personal and business data first, including aggregate views that name nobody. Page access is a separate project setting from repository visibility, so a private repository can serve public pages; read the platform's docs before concluding it cannot. An automated PII audit over-flags (about two dozen already-masked shots), so look at what it flags yourself.
  Tell: a count of "critical" shots from an automated audit that nobody opened.

- **A check that matches an HTML attribute must require whitespace before the attribute name.**
  `content=` also matches `data-content=`, so a gate for the noindex meta accepted a look-alike tag. Extract the pattern into a small named function with its own tests.

- **Build on every pipeline, deploy from the default branch only, and re-run the deploy once serving is configured.**
  The build validates before merge; a Pages deployment registers only when the deploy job runs after the serving mode is configured, so a pre-configuration deploy leaves none. The build clones the generator and fetches its plugins, so the runner needs outbound access to the generator's host; vendor the plugins if it is blocked.
  Tell: serving is configured but the site's deployment list is empty.

- **Before you restructure output that a tool generates or consumes, validate that tool's own path contract; group-aware does not mean co-location-aware.**
  Adapting the tool's profile to a new layout hid that the tool could not drive it: the group axis shipped separately from per-group asset co-location, and the harness had forked from the plugin's helpers. Measure by calling the tool's path helpers with your profile values rather than reading adapter prose, and re-read the tool's current reference before hand-patching what it generates (version claims in old notes rot).
  Extends [obsidian-vault.md](publish-targets/obsidian-vault.md#grouped-chapters-and-quartz).
