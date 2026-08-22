# The worked batch

The record behind the rules: one plugin's tracker, 30 open issues, audited twice. Kept for calibration and for the cases that go the other way — not as policy.

## The two stages, and the gap between them

Stage one asked **does it still reproduce**. 23 of 30 came back `still-real`, 3 `premise-stale`, 2 `partly-fixed`, 1 `already-fixed`, 1 a design question. By that measure the backlog was almost entirely valid.

Stage two asked **who authors the input, and which function is wrong**. Six issues were work worth scheduling.

That gap is the whole reason this skill exists. Reproduction was never the axis: the reviewers had measured the branch before filing, so of course it reproduced.

## Final tally

| Disposition | Count | Numbers |
| --- | --- | --- |
| Real work worth scheduling | 6 | #472, #474, #476, #473, #477, #560 |
| Closed as manufactured | 8 | #475, #553, #554, #246, #345, #354, #381, #421 |
| Closed as already fixed | 1 | #21 |
| Prose correction, no ticket needed | 7 | #21, #147, #261, #344, #470, #478, #557 |
| Parked, no named consumer | 9 | #65, #222, #224, #328, #349, #357, #380, #420, #479 |

#21 appears twice: the correction it asked for had already landed, so it closed as fixed *and* it belonged to the prose class. Applying the two-names gate at filing time would have produced about 6 numbers plus 7 prose edits — roughly 13 items of substance where 30 tickets stood.

Provenance distribution: 26 review-discovered, 3 doc-contradiction, 1 speculative. **All six real issues were review-discovered.** Nothing here licenses closing on provenance.

## Cases that decided each way

### One branch, four numbers — the duplicate test

`findContainer`'s `zero` outcome cannot distinguish "this group has no container" from "a container exists and the string compare failed", and it feeds an unconditional create. That single branch was filed four times, once per input spelling: #476 (emoji, bold, anchor, case), #475 (markdown link and wikilink), #553 (invisible character), #554 (newline).

The damage was not noise. #475 proposed **widening** the match; #476 argued for **failing closed**. Two open numbers, opposite remedies, either one implementable first. #476 survived because its measured table already carried the others' triggers as rows — including the linked-heading row #475 was filed for.

The docs did the same thing in prose: #470, #471 and #557 are three consecutive review rounds each finding a different paragraph of one file.

### The one that survived a hostile read — #472

`maskAndAssert` never enters a same-origin iframe. A customer e-mail typed into a rich-text Notes field renders inside that iframe, is composited into the region PNG, has no text node the walker can reach, matches no leak pattern, and the run goes green.

Every test passed it. Ordinary author, ordinary app, data crossing outward past the boundary the operator owns, silent failure. The clincher was the asymmetry: listing a selector inside the frame fails loudly, forgetting one is silent. Re-pricing shrank the fix from a cross-frame scan to counting frames in the scanned subtree and throwing unless the caller opts out.

### Promoted from prose to real — #474

Two shipped sentences stated a guarantee the code does not deliver: that a locale key drives the language the app under test renders in. No such option reached any browser-context call. The doc closed by telling the reader to *depend* on it — "you do not accept that it usually picks the right one, pin it".

Filed as a doc contradiction, it triaged as `real`: an operator copies the shipped example profile, wires the environment exactly as prescribed, and gets 40 English screenshots under German prose with every gate green. That is Side B of the AI-consumer axis — the reader follows a false sentence literally and reports success.

### Ordinary input, ordinary app — #560, and a body that was wrong

The shipped example capture spec — the one artifact every adopter copies — carries four `denyPatterns`. `matchesDeny` is raw substring over URL and post body, while the built-in verb check is token-exact and already covers all four verbs. So the four entries add zero coverage and are strictly more trigger-happy: an ordinary trash view at `/items/deleted`, an `/approvers` roster, a `sendgrid`-hosted pixel all flip to blocked and hard-fail the run mid-capture.

The issue's own premise — that the patterns are inert — was false, and would have sent an implementer the wrong way. `premise-stale` fired as a rider: correct the body, then schedule.

### Closed on the trust boundary — #354, #421

The operator owns the app, the machine, the output directory, and is the provenance record's only consumer.

#354 wanted an unbounded label sanitised before interpolation into a halt message. The evidence table was 400 repeated characters, an injection sentence and a delimiter collision — strings only a test produces — and the "interpolation" is a model writing a sentence about a file it had already read in full. Outside the boundary twice over.

#421 needed a filesystem to recycle an inode inside one run, or a backup to restore a previous build's screenshots mid-run. Neither has an actor. Its proposed closure would have broken the capture contract every profile depends on, and the residual it chases already ships documented verbatim.

### Closed on re-pricing — #246, #222

#246 carried five acceptance criteria demanding a filesystem-owning production module with URI semantics, percent-decoding and a non-throwing binder — to perform a scan-and-repair the model consumer does natively. Its own body recorded five plan revisions reviewed and killed. The replacement is one paragraph: if this handbook predates version N, read every embed once and rewrite the ones that do not resolve.

#222's descoped design is a staged-commit filesystem transaction with a rollback state machine. Parked with its unpark condition named, because a shipped document cites it by number.

### Round-N residual dumping — #357, #420, #421

#357 says in its own words that it audits a contract for round 15 of a 15-round review, against a file in no diff. Its consumer is a maintainer writing a third adapter; the target enum is closed at two values and a test actively forbids the third name. #420 and #421 were filed in one late burst closing a release.

A review loop obliged to keep going sends a reviewer out of scope, and out of scope is where every "nobody realistic" issue in this batch came from — for seven of the thirty, that phrase is the audit's whole answer to who hits it.

### Filed against the wrong file — #147

The issue demanded a backfill of eleven missing releases into the root changelog. The plugin's own changelog held all of them the same day. Real gap, wrong file, and the cheapest correct fix is one pointer line — not the backfill the issue asks for.

### Widened and still parked — #349

The issue's trigger looked crafted. Widening it produced ordinary titles — a bracketed chapter title, renamed twice — that defeat the same parse and measurably leave dead index rows. The producer test passed. It parked anyway, because the residual already ships disclosed in three places, including an extension contract that binds future adapters not to promise convergence. Passing the producer test does not by itself schedule an issue.

## Verdicts that were overruled during the audit

- **#553 park → close.** Same `zero` branch as #476, narrowed to the invisible-character spelling, and split out by the branch that narrowed the parent's claims rather than by anyone authoring a handbook. As a standalone number it splits one fix across two tickets.
- **#474 prose → real.** Silently shipping 40 English screenshots under German prose is deliverable corruption, and the false clause is stated as a guarantee the reader must act on.

Both overrules moved in the direction the hostile prior does not favour on one axis and does on the other. That is the shape to expect: a triage pass with no overrules in either direction was probably not adjudicating.
