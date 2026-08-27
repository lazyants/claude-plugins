# Pre-merge citation review

**Read this at the glossary pass, when a batch's citations are about to be merged into
`canon.json` — not before.** Everything in `canon-and-glossary.md` up to the point that
sends you here constrains the SHAPE of a citation; this document is about its TRUTH, and
it binds only at the merge step.

It moved out of `canon-and-glossary.md` unchanged. That file is compulsory under the
Pre-read mandate (`SKILL.md`); this one is not, which is the point of the split — a
project reaches it when it reaches the pass, not on every run.

### Pre-merge citation review

Everything above constrains the SHAPE of a citation, never its truth.
`canon-entry.schema.json` requires `source` when `basis == "established"`
and asserts `format: "uri"` plus `minLength: 1` on it; `--check-batch` runs
that same per-item shape check plus the offline backstop. No path opens the
URL, and none asks whether the cited reference actually attests the
`canonical_target_form` it was offered for. A fabricated but well-formed URI
cleared every check the pipeline had before this stage.

So the glossary pass reviews each `basis: "established"` citation itself,
**inside `batchStep`, before that batch counts as ready** — and therefore
before any fragment reaches `--merge-batches`.

**Since 1.16.1 (#347) the stage is TWO calls per attempt, not one.** Until
then a single agent both fetched every `source` URL and judged what came
back, which is two defects sharing one call. The SSRF half is closed by
`scripts/fetch_citation.py` — an http/https scheme allowlist, no embedded
credentials, every resolved address checked, the connection pinned to the
address it vetted, every redirect hop re-validated, and caps on time, bytes
and content type. The PROMPT-INJECTION half cannot be closed the same way,
and the first attempt to — telling that same agent to fetch only through the
helper — was rejected in review, correctly: the reviewer holds Bash and
ingests attacker-authorable page text, so a hostile citation page can simply
instruct it to curl something else. A rule the attacker can talk the enforcer
out of is not an enforcement point. So retrieval moved OUT of the judging
agent rather than being fenced inside it. PREPARE runs exactly two commands —
the `--approve-to` snapshot below, then `fetch_citation.py --batch` over that
snapshot — and reads only the one line of locally generated JSON each of them
prints; it never opens the snapshot or an evidence file, so nothing it
ingests was authored outside this project, and an agent that reads no
attacker text cannot be talked out of anything. If the snapshot command fails
it stops there rather than fetching, and no judge call is spent. The JUDGE
reads local files only — the snapshot, the evidence `index.json`, and exactly
the bodies that index names as an `evidence_file` — and needs no network at
all; it is handed no fragment path, not even inside prose forbidding a read
of it, because a prompt-injected judge should have to guess that string
rather than be given it.

**The claim the split supports, and no wider one:** in the citation audit path
retrieval happens only through `fetch_citation.py`, launched by an agent that
never reads the retrieved bytes, and the agent that judges neither performs
retrieval nor holds a tool that could. It does NOT make the pass SSRF-free:
the batch dispatch still does open web research by design under
`research_mode: live`. That one is accepted by design and documented rather
than quietly covered (#353); overclaiming here would be worse than the
original bug, because the next reader would stop looking.

**The judge's capability, not just its instructions (#353).** Until then the
split had removed the judge's REASON to fetch and its INPUT for fetching, and
said so at exactly that width, because it had not removed the CAPABILITY: the
judge could still run a command while reading attacker-authored page bodies.
It is now dispatched as `agentType: "literary-translator:citation-judge"`, a
plugin agent whose frontmatter grants `tools: Read` and nothing else, so the
boundary is the harness's rather than the prompt's. An agentType that cannot be resolved is fail-closed — no
fallback to a full-tool agent, and a batch whose verdict never arrives is not
approved.

**Neither half is codex, and neither carries a schema** — the judge's
`agentType` names the tool-restricted Claude agent above, never a codex
dispatch, and both calls are sentinel-verdict shaped exactly like the
wait step (a schema-bearing call can wedge the Workflow if the
forwarder detaches, #97). Codex is what PRODUCED the citation, so a reviewer
running under a different model is a genuinely separate opinion rather than
the same reasoning re-run; `tests/bounded_poll_present.test.py` pins this
template's codex work-call set to exactly `{batchDispatchPrompt}`, which
keeps it that way. This does not loosen R1/R4: the stage AUTHORS nothing and
repairs nothing — its only two powers are approve and reject, every canon
resolution still comes from codex, and a rejection's only effect is to make
codex redo the batch. The two efforts differ deliberately: PREPARE takes the
the wait's `"low"`, being mechanical — run two commands, relay
which succeeded — while the JUDGE keeps `"high"` as the one judgment call in
the template. Neither is wired to `{{EFFORT}}`, which stays the codex
dual-injection knob and nothing else.

Scope is narrow and explicit: only items whose `basis` is exactly
`established` are examined — every other basis makes no external source
claim at all — and for each one the judge decides from that item's retrieved
body alone, never from the URL's shape, its domain's reputation, or its own
memory of what lives at that address. Three checks: it RESOLVES (the index
records that item's outcome as `fetched` and the body is the reference page
itself — not a 404, a parked domain, a content-hiding login wall, or plainly
a different page than the URL promised; an outcome of `refused:<reason>` or
`http_error:<code>` FAILS this check, because nothing was retrieved and so
nothing supports the claim); it is ABOUT THE RIGHT ENTITY (not merely a
same-named bearer); and it SUPPORTS THE CLAIMED FORM — the page actually
attests the `canonical_target_form` as an established target-language
rendering. That third one is the common failure: a page proving only that
the entity exists, or giving the name only in the source language, does not
support an `established` claim. A missing, empty, non-URL, or
search-results/query `source` rejects too, and so does evidence the judge
cannot read — an unverifiable citation is never approved on the grounds that
verification was unavailable, and going to fetch the page itself to settle
it is not an option that task has. `index.json` deliberately covers EVERY
item carrying a `source`, not only the `established` ones, so entries
outside the judge's scope are expected rather than a defect. The verdict is
**per batch, not per item**: a single failing item rejects the whole
fragment, so there is no partial verdict to express. A fragment with no
`established` items at all passes trivially — a live-mode batch that
happened to resolve everything by transliteration or sense-translation costs
one cheap prepare-and-approve pair, never a research round.

**Every attempt gets its own fragment path** — `out_{index}_attempt_{n}.json`
from attempt 0 onward, where this used to be one fixed `out_{index}.json`.
That is not tidiness: the single path made a citation rejection
unenforceable IN PRINCIPLE. A citation-rejected fragment is still perfectly
valid STRUCTURALLY — its URL is present and URI-shaped, which is exactly why
`--check-batch` passed it — so the wait step for the regenerated fragment
would return `READY` against the REJECTED bytes the instant it looked,
whether or not the agent had rewritten anything yet, and those bytes would
sail into the merge. Per-attempt paths make that impossible by construction
rather than by timing — but only together with the pre-run wipe, and only for
the WITHIN-run case. Attempt n+1's wait polls a path that does not exist
until the fresh dispatch atomically renames it into place: inside one run
because the path is attempt-scoped, and across runs because
`resume_setup.py` wipes stale fragments before the run starts (**1.16.0**).
It reuses the same `RUN_ID` on a digest-match resume and nothing deleted
fragments, so before that wipe a prior run's `out_{index}_attempt_{n}.json`
sat at exactly the path the new run would poll, and `--check-batch` — which
has no mtime, no token and no freshness notion at all — passed on those bytes
at once, so the reviewer audited the previous run's fragment. The wipe is
conditioned on the resume flag `resume_setup.py` already computes: a
**fresh** run wipes ALL `out_*` and `approved_*` attempts including attempt 0
(fresh-ID uniqueness only checks `runs/<RUN_ID>`, so an orphaned
`glossary/runs/<RUN_ID>` directory can outlive its identity directory and
collide on the one-second timestamp), while a **resume** wipes `n >= 1` and
every snapshot but keeps attempt 0, which the resume-skip optimisation
depends on wholly and which is citation-reviewed either way. Every
`evidence_*_attempt_*` DIRECTORY goes unconditionally under both flags,
attempt 0 included (**1.16.1**): evidence is an OUTPUT of the citation
review, re-produced by the prepare step before anything judges it, so a
surviving copy is never useful and is potentially wrong — it follows the
`approved_*` rule, not the `out_*` one.

For the same reason
the verdict sentinels carry the ATTEMPT number, not just the batch index — a
verdict is a statement about one attempt path, so a stale verdict simply
fails to match. A mismatched, malformed, or absent verdict falls to the
REJECT side, which is still the right direction but not a cheap one here: a
wrong reject costs one regeneration if the ladder then clears, and the WHOLE
RUN if it does not — the attempts exhaust to `citation-review-exhausted` and,
the merge being all-or-nothing, ZERO batches merge. A wrong accept costs a
permanently frozen fabricated citation, which is worse and unrepairable,
so the direction stands; the cost of being wrong is what is bounded loosely.

**What the merge is handed is the approved SNAPSHOT, not the attempt path**
(**1.16.0**) — because approval binding a path rather than bytes was not
enough even inside a single run, and needed no adversary to fail. The
dispatch is a fire-and-forget `codex:codex-rescue` job whose own prompt tells
it to rewrite the attempt fragment until that fragment's self-check passes,
and the codex job outlives the awaited call — which is *why* the wait poll
exists at all — so several atomic renames onto the reviewed path are ordinary
expected behaviour. `pipeline()` then waits for every batch before the one
`--merge-batches`, so an approved fragment sits un-rechecked while its
siblings climb their retry ladders; `--merge-batches` fresh-reads from disk
and knows nothing of the citation review's VERDICT — since **#505** it knows
only whether the caller attested one (`--citations-reviewed`, whose refusal rule is
in [`canon-and-glossary.md`](./canon-and-glossary.md)), which is
a different and much smaller thing — and `--verify-merged` re-reads too but
checks shape and coverage, never citations.

So the fragment's own `--check-batch` validation is re-run with
`--approve-to` as PREPARE's first command, before anything is fetched and
long before anything is judged: that invocation copies the exact bytes it
just validated — one `read_bytes()` from the read that validated them, no
second read, no window — to a create-once
`approved_{index}_attempt_{n}.json`, `fetch_citation.py` then takes its URLs
from THAT, and the judge audits THAT. The ordering is the whole fix and
cannot be reversed: snapshotting *after* the audit leaves a producer free to
replace validated-bytes-A with structurally-valid-bytes-B between the
reviewer's read and the copy, and fetching from the mutable attempt path has
the same defect one layer out — the URLs retrieved would be ones no reviewer
ever approved. On `CITATIONS_OK` the merge consumes the snapshot, so within
one run the bytes audited, the bytes approved and the bytes merged are one
object by identity, and a post-snapshot rewrite of `out_*` reaches nothing
anyone reads — the defect is unrepresentable rather than detected, with no
hash to compare and no window to keep short. That "within one run" is
load-bearing and rests on preconditions; the next section states them once,
and every other mention of this guarantee points there instead of restating
them.

The snapshot stays inside PREPARE's own turn rather than becoming a step of
its own, but since **1.16.1** the reason is no longer cost: the split already
spends the extra call, taking the live ceiling from
`1 + 3*(MAX_CITATION_RETRIES+1)` to `1 + 4*(MAX_CITATION_RETRIES+1)` — and
**1.16.2** took it further still, to
`2 + (3 + WAIT_CALLS)*(MAX_CITATION_RETRIES+1)` (**20** at the shipped
`WAIT_CALLS = 3`), when the wait itself stopped being reliably one agent call
— `WAIT_CALLS` is its worst case, not its price (see
**The chunked wait** in [`canon-and-glossary.md`](./canon-and-glossary.md)). **#724** then took it to
`1 + (2 + WAIT_CALLS)*(MAX_CITATION_RETRIES+1)` (**16**): the resume precheck
was deleted, and PREPARE's two commands now run inside whichever wait turn
already saw `--check-batch` exit 0, so on the fresh path prepare is no longer
a call of its own. What
survives is the structural reason, which was always the stronger one — this
is the ONE point both entry points into the review loop converge on, and the
fold does not move it: the folded turn issues the SAME two commands, in the
same order, that the standalone task states, and a resumed batch that runs no
wait still spends the standalone call. Putting
the snapshot in the wait step and NOWHERE ELSE would silently skip it on every
resume-skipped batch, because that path runs neither the dispatch nor the
wait, and a resumed, never-reviewed fragment is precisely the case this whole
stage exists for. Prepare sits at that convergence point, so both entry
points get a snapshot and evidence alike.

#### What the approved snapshot guarantees, and the preconditions it rests on

This is the canonical statement of the property, and the only place its
qualifiers belong. Everywhere else that mentions it — `SKILL.md`, the other
`references/`, the workflow templates, `canon_validate.py` — states it in
short form and cites this heading by name instead of re-deriving its own
qualifiers, because a guarantee restated in six places is a guarantee that
drifts in six places. Correct it here.

**What holds.** Within one run the snapshot is published CREATE-ONCE: the
validated bytes go to a unique temp path, which `os.link()` then links into
place. What that buys is exclusive CREATION and nothing more — a second
`--approve-to` cannot publish over the entry: IDENTICAL bytes are an
idempotent no-op, and DIFFERENT bytes — a repeated `--check-batch
--approve-to`, or two overlapping reviewer dispatches for the same batch and
attempt — fail closed and name the path, leaving the already-audited copy
byte-untouched.

It does NOT make the published file immutable. Once created, the snapshot is
an ordinary writable file; `os.link()` never runs against it again, and any
process holding the path can truncate or rewrite it in place. "The bytes the
citation reviewer audits are the bytes the merge consumes" is therefore a
conclusion drawn FROM the three preconditions below, not something the
filesystem enforces by itself.

**Precondition A: one live run per glossary run DIRECTORY** — filesystem
identity, not string identity, and the two are not the same thing.
`RUN_ID_RE` is `[A-Za-z0-9][A-Za-z0-9._-]*` and `resolve_run()` returns the
caller's own spelling unnormalised, so `abc` and `ABC` are both valid and
distinct as RUN_ID strings; on a case-insensitive filesystem (the macOS
default) they name ONE `glossary/runs/<RUN_ID>/` directory. A precondition
worded as "one live run per RUN_ID string" would therefore not be enough: what
matters is what the filesystem resolves the path to.

**Precondition B: `durable_root` on a hardlink-capable filesystem.**
`os.link()` is what makes creation exclusive, so a filesystem that cannot
provide it (some SMB/FAT mounts) makes the publish FAIL, loudly and by name.
It deliberately does not fall back to an overwriting write, which would
silently restore the duplicate-approval race on precisely the setups nobody
tests on.

**Precondition C: nothing writes the snapshot path out of band.**
`--approve-to` is the only writer — by INSTRUCTION, not by enforcement, which
is why it belongs here rather than under what holds. Two agents hold the
path since 1.16.1, and neither sentence below is enforced by anything.
PREPARE runs the command that publishes it and is told "You must not create,
modify, or delete any file yourself. The only changes this task may produce
are the ones those two commands make on their own"; it reads no retrieved
bytes, so nothing it ingests can argue it past that. The JUDGE is the one
handed the path while reading untrusted fetched pages, and it is told "You
must not create, modify, or delete any file, in this directory or anywhere
else" — which the split does not enforce either: it removed the judge's
REASON to run a command, not its Bash tool. A
process that rewrites the path AFTER the audit and then returns
`CITATIONS_OK` defeats the property with preconditions A and B both intact,
because the merge fresh-reads whatever the path holds at merge time.

**What is NOT claimed.** No lock is taken, and the snapshot carries no
run-identity binding — nothing in it records which run produced it. The
property is OPERATIONAL, the same species as `canon.json`'s single-writer note
in `canon_validate.py`: it holds because the orchestrator runs one glossary
pass per run directory at a time, not because anything here locks a file. What
ENDS it is the run-start wipe — `resume_setup.py`'s
`_wipe_stale_glossary_fragments` unlinks every `approved_*` (its keep rule
spares `out_*_attempt_0`, and only on a resume) — so a second run starting on
a live run's directory deletes the audited snapshot and reopens the slot, and
the first run's already-issued `CITATIONS_OK` would then merge bytes nobody
audited. The wipe is deliberate and stays: it exists so a fresh run cannot
adopt an orphaned directory's stale attempt. That makes this a bounded
precondition, not an unnoticed defect.

**Evidence status.** The guarantee rests on `os.link()`'s create-once
semantics, not on any test — and the tests around it each touch less than
their names suggest, so read them for what they actually exercise. The
concurrent-writer test in `tests/canon_approve_to.test.py` starts eight
processes that call `_write_approved_snapshot` DIRECTLY, not through
`--approve-to`, and asserts that exactly one of them wins; it races the helper
rather than the CLI on purpose, since the window is microseconds wide and full
CLI runs would sample it only by luck. So it CAN catch this helper regressing
to a check-then-act publish, and has caught one across separate runs — but
sampling is what makes such a regression likely to be caught, not certain to
be. It says nothing about the CLI: that path is covered separately, by the
sequential `--check-batch … --approve-to` tests for its behaviour, and by a
wiring test pinning the CLI to publishing THROUGH this helper. Take each for
what it is.

Fail-closed follows from the snapshot being attempt-scoped as well: if the
winning attempt was never approved, the `approved_{index}_attempt_{n}.json`
the merge names does not exist and the merge dies on a missing file before
any `canon.json` write, while a rejected earlier attempt's snapshot sits at a
path the merge never names and so cannot satisfy it either. Under `offline`
no `established` item is legal, so no reviewer runs and no snapshot is
produced — the merge consumes the ATTEMPT path there. That is an explicit
branch, not a global rename: "the merge always consumes approved paths" would
make every offline merge fail on a missing file.

**The containment guard, and why line equality alone was not enough.**
`sentinelVerdict()` decides on whole-LINE equality: it sees a fail sentinel
only when that sentinel's line, after `String.prototype.trim()`, equals the
sentinel exactly — nothing else may share the line except what `trim()`
strips. In the realistic failure shape, a reviewer writing its finding and
then the sentinel on the SAME line, that prose is on the line regardless, so
ANY glue character hides the sentinel, a plain space included: **15 of 16 over
`GLUE_CHARS` in `tests/glossary_citation_review.test.py`, prose sharing the
sentinel's line**. Only a line feed puts the sentinel on a line of its own
(CRLF is safe for the same reason, and is deliberately not in that table).

With the sentinel ALONE on its line the same table splits, which is worth
knowing before "simplifying" anything here: **7 of 16 over `GLUE_CHARS` in
`tests/glossary_citation_review.test.py`, sentinel alone on its line**. `trim()`
reaches a line's two ends and so strips a space, tab, VT, FF, CR, NBSP, U+2028
and U+2029; those still match and still reject correctly. The 7 survivors — the
C0 separators U+001C–U+001F, NEL U+0085, a zero-width space, and any ordinary
character — hide the sentinel with or without prose. Do not reason about that
set by eye: U+0085 is not `trim()`-strippable in JS while U+2028 and U+2029 are.

**Always publish a gluing count with both its SHAPE and its SET**, naming the
set by constant and file as above. The same guard measured over a different
table, or over a different reply shape, yields a different and equally correct
number, and a bare count reads as a contradiction between surfaces that do
not actually disagree. This release publishes four, one per (set, shape)
pair — the two above, plus **14 of 15 over `ALL_GLUES` in
`tests/mass_translate_sentinel_containment.test.py`, prose sharing the
sentinel's line**, and **6 of 15 over that same set, sentinel alone on its
line**.

None of the four restates another, because the two sets are genuinely
different: they share 13 characters, `GLUE_CHARS` adds the C0 separators
U+001D–U+001F, and `ALL_GLUES` adds an ASCII hyphen and quote. `trim()`
rescues the same nine characters in each, so the alone-shape counts fall out
of what each set adds beyond the shared 13 — three unrescued characters
against two, hence 7 of 16 over `GLUE_CHARS` against 6 of 15 over
`ALL_GLUES`, both with the sentinel alone on its line.

Enumerate the four rather than asserting how many there are: a count OF the
release's own published counts is self-referential, goes stale the moment
another is added, and looks no different from a correct one at a glance —
which is exactly how "three" survived here past the fourth.

The end state is identical either way: the fail scan skips the sentinel, a
trailing clean OK line then approves the batch, and a reply carrying BOTH
verdicts silently resolves to the approving one.

Each of this template's four sites — wait, prepare, judge and
(**#723**) the approval record — therefore now short-circuits to REJECT when `rejectedAnywhere(reply,
failSentinel)` finds the fail sentinel anywhere in the reply as a plain
substring, evaluated BEFORE `sentinelVerdict()` is consulted
at all. Substring containment is strictly easier to satisfy than line
equality, so the guard can only ADD rejections, never remove one — it moves
the failure into the fail-safe direction by construction, not by care.

The same guard is applied to `mass-translate-wf.template.js`'s translate and
review waits. Its `DRAFT_MISSING` fix check is guarded too, but in the OPPOSITE
direction and through a differently-named wrapper: there `DRAFT_MISSING` is the
OK sentinel, so gluing hides a GENUINE missing-draft report rather than faking a
pass, and `runRound` keys on `mentionedAnywhere()` — same containment test as
`rejectedAnywhere()`, which it delegates to, but a hit biases toward ACTING on
the sentinel instead of rejecting. Six guarded sites over the two templates.
(It was seven until **#724** deleted the glossary precheck; the count and its
composition have both moved, so read the list, not the number.)
`skeptic-pass-wf.template.js` mirrors this control flow and is deliberately NOT
guarded — it sits in no `cache_key.py` bundle and carries its own
`compute_skeptic_input_digest()`, so editing it would force a fresh skeptic
RUN_ID that this release does not otherwise pay. See the 1.16.0 CHANGELOG entry.

The guard buys its safety with two false REDs, both worth recognizing in a
log. Neither is *bounded* in the sense that word invites: what a bound applies
to below is the number of attempts, never the cause of the reject.

- A reply that merely MENTIONS the fail sentinel while approving — "this is
  not a `CITATIONS_REJECTED 0 ATTEMPT 0` case" — now rejects.
- A sentinel can be a substring of a longer-indexed sibling: `PENDING 1` occurs
  inside `PENDING 10`. So a wait reply for batch 1 that quotes batch 10's
  sentinel takes the reject branch. (First written down against the precheck's
  `ABSENT 1`/`ABSENT 10`; **#724** deleted that site, and the collision is a
  fact about substring containment rather than about any one sentinel.) The citation verdict is NOT
  exposed to this at shipped settings, because its sentinels end in
  ` ATTEMPT <n>`, which terminates the batch index —
  `CITATIONS_REJECTED 1 ATTEMPT 0` is not a substring of
  `CITATIONS_REJECTED 10 ATTEMPT 0`. The attempt number can collide the same
  way (`ATTEMPT 1` inside `ATTEMPT 10`), which the shipped
  `MAX_CITATION_RETRIES = 2` keeps unreachable; raising it to 10 or more would
  make it reachable.

**A false REJECT does not cost the same at every site**, and the difference is
what to read a failed run against. At every remaining site the trigger is the
reply's PHRASING rather than the data, so whatever retry the site gets — the
citation ladder's next attempt in-run, a later run for the others — is another
roll of the same die and not a repair.

**The one site that DID recover deterministically was the precheck, and #724
removed it** — not by weakening it, but by removing the reply it read: the
resume decision is now `resume_setup.py`'s own `--check-batch` run, substituted
into the template as an array, so there is no phrasing to get wrong. That is
worth stating rather than quietly dropping, because it is the only entry in
this list ever to have been repaired in-run, and its disappearance from the
list is a deletion of the failure mode rather than of the remedy.
- **Evidence prepare (1.16.1)** — joins the citation ladder below rather than
  falling through: a false hit on `EVIDENCE_FAILED` skips the judge call
  entirely, carries prepare's own reply forward as the next attempt's
  regeneration constraint, and still counts against `MAX_CITATION_RETRIES`,
  so that attempt costs `1 + WAIT_CALLS` calls rather than the ladder's
  `2 + WAIT_CALLS` — 4 rather than 5 at the shipped `WAIT_CALLS = 3`. Both terms
  dropped by one in **#724**, which folded the prepare into the wait turn on the
  fresh path; the saving is the JUDGE call in both cases, and that is what has
  always made this attempt cheaper than a judged one. Not a repair
  either, and for the same reason as the review below — the ladder varies the
  FRAGMENT, while what tripped the guard was prepare's WORDING.
- **Citation review — NOT RELIABLY self-recovering, however much its retry
  ladder looks like it.** The batch does regenerate to a fresh attempt and get
  reviewed again, bounded by `MAX_CITATION_RETRIES`. But the ladder varies the
  FRAGMENT while the guard was tripped by the reviewer's WORDING, and every
  prompt that owns a fail sentinel prints that sentinel verbatim in its own
  instructions — so a reviewer reasoning about its verdict in prose is an
  ordinary output, and the next attempt's reviewer reads the same invitation
  to do it again. It may decline it, and that attempt then merges in the same
  run — but that is a re-roll landing well, not a repair, since nothing the
  ladder varies addresses what tripped the guard. Burning all
  `MAX_CITATION_RETRIES + 1` attempts returns `citation-review-exhausted`, and
  the merge being all-or-nothing, **zero** batches merge: the run produces
  nothing while the data may have been fine throughout. What the bound buys is
  termination, not recovery: nothing about the trigger is per-run state, so
  re-invoking the pass is another re-roll rather than a reliable repair.
  **Telling the causes apart is what an operator actually needs**, and it
  is readable off the reply, which is why the exhaustion message states all
  three instead of one. The judge's prompt requires a genuine rejection to
  list, above its verdict line, one line per offending item naming that item's
  `source_form`, its `source` URL, and which of the three checks it failed and
  how; `batchStep` hands that reply to the next attempt as its regeneration
  constraint and returns it as `lastRejection`, so the text is there to read.
  A `lastRejection` naming specific `source_form` values with their URLs is a
  data problem — route those candidates to `disposition: "review_queue"` or
  supply real sources, then re-run. A `lastRejection` that instead reads as an
  approval, discusses the `CITATIONS_REJECTED` sentinel rather than any
  citation, or is the fixed no-findings placeholder is the guard misfiring:
  nothing in the data needs editing, the attempt fragments and their approved
  snapshots are on disk to inspect, and the right response is to treat it as a
  review-prompt defect and report it — not to re-run and not to hand-edit
  candidates. Since **1.16.1** a third cause reaches this same return: a
  `lastRejection` quoting a failing command rather than discussing any
  citation — `canon_validate.py --check-batch --approve-to`, or
  `scripts/fetch_citation.py` — is an environment or tooling fault, not a
  fact about the candidates. Run that exact command by hand and read its
  error; a fetcher that cannot reach the network at all fails every batch
  identically, which is the quickest way to tell this case from the other two.
- **Wait** — NOT automatic, and this is the one that matters. The site returns
  `{ready: false, reason: "glossary-pass-null"}` immediately, straight out of
  `batchStep`; the enclosing attempt loop does not catch it, because this is a
  `return` and not a `continue`. That batch is over for the run, and since the
  merge is all-or-nothing it takes the whole pass with it — `merged: false`,
  `reason: "fragment-check-failed"`, nothing merged at all. Recovery here is
  an operator re-invoking the pass, not the template retrying — and that
  re-invocation must NOT pass `resumeFromRunId`, or it replays this batch's
  cached replies unchanged; see `references/orchestration-and-batching.md`'s
  **Exception — a MATCH whose cached result is a non-answer (#404).**
- **Mass-translate's three sites**, for completeness, since they carry the same
  containment test: its review wait blocks that segment for the run
  (`reason: "review-timeout"`); its translate wait returns the deliberately
  non-terminal `reason: "translate-timeout"`, which `select_segments.py` treats
  as recoverable and auto-redispatches next run; and its `DRAFT_MISSING` fix
  site, on a false hit, probes via `draftPresentAndValid()`, finds the draft
  present, and returns `reason: "fix-call-failed"` with no terminal ledger
  write — also auto-redispatched. Those last two are the cheapest false REDs of
  the six.

Regeneration is bounded by `MAX_CITATION_RETRIES`, and the next attempt's
dispatch prompt is handed the rejecting reviewer's own findings (minus the
verdict sentinel lines) as its regeneration constraint. Dropping those lines
is PROMPT HYGIENE, and claiming anything stronger would be false: a leaked
sentinel reaches no parser at all. The dispatch call's own reply is
DISCARDED — its `await agent(...)` is not assigned to anything — and the only
reply sentinel-parsed anywhere near it is the separate wait step's, over a
disjoint `READY`/`PENDING` set that no `CITATIONS_*` string can collide with.
So a leak cannot corrupt the state machine or route a rejected fragment into
the merge. It is still worth stripping: that prompt is meant to hand the next
attempt the reviewer's findings and nothing else. Exhausting the
budget returns `merged: false` with `reason: "citation-review-exhausted"` —
deliberately a DISTINCT reason from `fragment-check-failed`, because "a
fragment never became structurally valid" and "the fragments were valid but
their citations did not survive review" are different operator problems with
different remedies. Either way nothing is merged.

Under `research_mode: offline` the stage is a no-op: `established` is
forbidden outright there (see the research-preflight and offline-fallback policy in
[`canon-and-glossary.md`](./canon-and-glossary.md)), so there is no citation to review.

**Why it must be PRE-merge: a merged row cannot be repaired.** This is the
load-bearing rationale, not a preference for failing early. Once a
`source_form` is a key in `canon.json`'s `entries{}`, every shipped path
that could plausibly change it is closed:

- `canon_validate.py` is the only script in the plugin that writes
  `canon.json` at all, and the merge is the only one of its modes that can
  write an `entries{}` row through the ordinary glossary pass. Since **#495**
  there is exactly one other way, and it is deliberately out-of-band:
  `--correct` (see **`--correct PATH`** in [`canon-and-glossary.md`](./canon-and-glossary.md)) changes or deletes ONE frozen entry,
  requires the caller to state the old value, and records the change with its
  reason in `corrections[]`. It is not an override of anything below — the
  collision refusal in the next bullet is untouched by it, and a correction
  cannot be reached from a batch at all. The other writing modes reach the same
  single `_atomic_write_json` call site but cannot touch a resolved entry:
  `--init` is create-only (an existing canon.json is left byte-untouched and is
  not even read), and `--restamp-derivation` moves only the two
  `generation_hashes` fields.
- **A conflicting re-merge is fatal, not a fix.** `_merge_batch` raises on a
  genuine cross-run collision — two different resolutions claimed for the
  same `source_form` — naming both the old and the new value, and the whole
  merge is refused. An IDENTICAL re-submission is a silent no-op. So
  re-running the glossary pass with a corrected citation does not overwrite
  the wrong one; it fails the merge.
- **The glossary pass cannot even re-ask.** `glossary_batch_plan.py` drops
  every candidate already present as an `entries{}` key before the codex
  pass ever sees it (**Citation cache** in [`canon-and-glossary.md`](./canon-and-glossary.md)), and `--retry`
  overrides ONLY the `review_queue`/dismissed exclusions (**#653** added the
  second) — it cannot reinstate an already-resolved entry, and says so in
  its own diagnostic.
- **`--verify-merged` reports, it does not repair.** It fresh-reads
  `canon.json` and every named fragment and returns `{verified, missing[]}`.
  It is disk-independent and writes nothing at all — it can only tell you
  that the merged canon disagrees with the fragments, never reconcile them.
- **`canon_adjudication_audit.py` blocks, it does not repair.** Its own IRON
  RULE is explicit: it mechanically enumerates every item a human or a
  schema-validated codex workflow must sign off and cross-checks the
  recorded verdicts against canon.json's current state — and it never writes
  a verdict or a risk-acceptance itself.
- **The skeptic pass is post-merge, opt-in, and advisory-only.** Its
  `established_offline` risk class exists precisely because
  `canon_validate.py`'s offline backstop only checks INCOMING batches and
  never re-scans an already-frozen canon — and that class contributes nothing
  under `live` (`suspicion_scan.py`'s `_established_offline_forms()` returns
  an empty set unless `research_mode == "offline"`). A `live` `established`
  entry can still be flagged by the basis-blind classes — `singleton`,
  `all_citation`, `near_merge`, `merge_participant`, `high_dispersion`,
  `fold_collision`, `sampled` — whose only scope filter drops
  `is_proper_name: false` / `basis: "not_a_name"`. None of that repairs
  anything, which is the point here: no freeze/merge reader ever opens
  `skeptic_triage.json`, and its verdict schema cannot express a
  confirmation, let alone a repair.

What remains is `--correct` (**#495**) — an explicit, recorded, out-of-band
correction, one entry at a time, stating the old value. It is a real option for
a human and it is exactly the one this stage exists to avoid needing: see
**Retroactive canon edits invalidate precisely** in [`canon-and-glossary.md`](./canon-and-glossary.md) for what it costs. Every
segment whose `used_terms_hash` covers that term goes stale — bounded re-review
via `--from-converged`, since **1.25.0**, rather than the re-translation it
would have cost before that. Before #495 the only move here was a hand edit
outside every shipped tool, which cost the same invalidation and recorded
nothing.

**Why in-batch, rather than "after all batches, before the merge".** There
is no such window. `glossary-pass-wf.template.js` runs
`pipeline(BATCHES, batchStep)` and then, in the SAME Workflow call, the
`--merge-batches` and `--verify-merged` steps — nothing pauses between the
last fragment becoming ready and `canon.json` being written. Pre-merge
therefore has to mean pre-READY, inside `batchStep`.

