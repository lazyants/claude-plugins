# W9r — the opt-in person registry (#550)

Some books are translated *for* something other than the translation. When the
deliverable a project's owner actually wants is genealogy, the question they
need answered is "how many distinct people are in this book, which name forms
are the same person, what does the book print them as, and how are they
related." Nothing else in this plugin answers it: `canon.json` is a 1:1
name-form → target-form dictionary with **no entity model**, by design.

W9r consolidates what the pipeline already produced into a person-keyed
registry. It writes **new artifacts only** — it reads `canon.json`, never
writes it, and enters no cache key.

## Opt-in means the operator runs it

There is deliberately **no profile knob**. W9r is a post-delivery operator
tool, not a link in an automatic chain: a project that wants a registry runs
`scripts/person_registry.py`, and a project that does not never invokes it.

A `profile.yml` flag would have meant editing
`assets/schemas/profile.schema.json`, whose copy under
`${durable_root}/schemas/` is hashed by `resume_setup.py::_schemas_dir_hash`
into `input_digest` — costing **every** project its resume identity to gate a
step nothing auto-runs. What a non-adopting project pays for this feature is
therefore exactly one inert file in `${durable_root}/scripts/`, and no cache
key moves: `person_registry.py` is in none of the three bundle-member tuples,
and nothing hashes the scripts directory as a whole. The three schemas ship in
`assets/schemas/registry/` — Step 0a's copy pass globs `assets/schemas/*.json`
and `_schemas_dir_hash` globs `${durable_root}/schemas/*.schema.json`, both
NON-recursively, so a schema in that subdirectory is neither copied nor hashed.
Running the script from a durable root therefore needs `--plugin-root PATH` to
find them (the `#412` pattern `canon_validate.py` already uses).

## The deterministic / judgement split

Deciding whether two name forms denote the same person is **interpretation**
and must be an LLM judgement — never a string matcher, surname heuristic, or
edit-distance rule. In a naming system that fixes identity by patronymic or by
place, a shared surface is ambiguous **by construction**: on the corpus this
pass was generalized from, one spelling of a given name denotes six distinct
men, and 40 forms denote more than one person. Any "similar names are one
person" rule merges them, silently, in the direction that looks like success.

So the script decides no identity. It reads artifacts, calls the production
occurrence engine, counts strings, validates schemas, verifies that quotes
exist where they claim to, joins adjudications to claims, and emits files.

**And a second axis, which is the reason there are two model calls rather than
one.** A verbatim-quote check proves a sentence exists; it can never prove the
sentence says what the claim says. A model can cite a real sentence — "David
visited Isaac in Warsaw" — and attach `son_of: Isaac`, and every structural
gate passes. Only a reader catches that. So **Pass B**, a *freshly dispatched*
call that sees each judgement in isolation, adjudicates every person, every
typed claim, every printed-name attribution, every non-person classification
and every identity status with its reason. Pass
B must not inherit Pass A's conversation: a reader who already holds the
narrative that produced a judgement is not an independent check on it.

## Running it

W9r runs **immediately after W9's chain, in the same session** — see "assembly
currency" below for why that matters.

```
LT=<the literary-translator skill directory>   # holds assets/schemas/registry/
python3 scripts/person_registry.py --prep   --plugin-root "$LT"
#           -> registry/registry_input.json
#   Pass A: one call over the whole cast, per registry_TASK.md
#           -> registry/registry_verdicts.json
python3 scripts/person_registry.py --claims --plugin-root "$LT"
#           -> registry/registry_claims.json
#   Pass B: a FRESH dispatch, per registry_TASK.md's Pass B section, whose only
#           semantic inputs are that section and registry_claims.json
#           -> registry/registry_adjudications.json
python3 scripts/person_registry.py --build  --plugin-root "$LT"
#           -> registry/person_registry.json + registry/PEOPLE.md
```

`--plugin-root` is not optional here — `--claims` and `--build` exit `2` with
`schema_not_found` without it, for the reason above.

Copy `assets/templates/registry_TASK.template.md` to
`${durable_root}/registry_TASK.md` and fill its bracketed placeholders before
Pass A. Everything lands under `${durable_root}/registry/` — deliberately not
under `out/`, which is the render destination and is destroyed by
`render_obsidian.py`'s clean-then-rebuild (only dot-prefixed entries survive
there).

## The prep universe: canon alone is not the cast

`--prep` builds its units from the union of three populations, keyed on
`(source_form, sense_id)`:

| population | unit | occurrences |
| --- | --- | --- |
| `canon.json` `entries{}` | `(form, null)` | from `occurrence_targets.build` |
| `canon_senses.json` `entries_by_source_form` | one per **sense** | `null` |
| `canon.json` `review_queue[]`, coalesced by form | `(form, null)` | `null` |

The senses population is load-bearing, not an edge case: **an adjudicated
homonym split is deliberately absent from `canon.json`'s `entries{}`** — that
is the whole point of the sidecar, as `glossary_batch_plan.py`'s split-form
exclusion says outright. A canon-only universe would omit exactly the people a
genealogy registry exists for. When a form has senses, only its per-sense units
are emitted; a coexisting `review_queue` row survives as a refusal-only unit
carrying all its notes, because the project's own record that a third referent
is unresolved must not vanish behind a two-sense resolution.

Each unit's contexts are **one per physical occurrence, paired with the
delivered text of the same container**. Two occurrences in one block arrive as
two records with identical locators, and each is windowed on its OWN matcher
span — centring both on the container's first span would show one sentence
twice while `contexts_total` said two, hiding a distinguishing second mention
behind a number claiming it was shown. A NodeStream node carries the same id as
the manifest block it was translated from, so every context also carries
`target_text`: what the book actually PRINTS there. Without it, a model asked
for "the strings the book prints" is guessing from the canonical form, and the
adjudicator checking it is guessing too. A `printed_surface` claim additionally
carries the passages where that exact string occurs in the
delivered corpus — with the true total, and a flag saying whether it is seeing
all of them or an even spread over them.

Occurrences come from `occurrence_targets.build` — the same call
`assemble.py::_attach_mentions` makes — so a mention location does not depend
on how that occurrence's translated surface happens to be spelled. Forms the
engine refuses to attribute (a fold-key collision, a split) carry no count and
appear under `unattributed_units`. A unit with no occurrence path at all
carries `null` **with a reason**, never `0`: a zero would read as "not in the
book", which is a different fact.

## Evidence locators are origin-aware

The three occurrence origins live in three different containers, and a
block-only locator is unverifiable for two of them:

| origin | locator | container |
| --- | --- | --- |
| `block` | `block` | `manifest.blocks[block].plain_text` |
| `embedded_verse` | `vid` | the `manifest.verse.store[]` entry with that vid |
| `footnote` | `footnote_n` | `manifest.blocks[def_block].plain_text` |

An embedded verse's parent block carries only the `⟦VERSE_…⟧` placeholder, not
the verse's prose — so a block-only rule would reject a legitimate verse quote
*and* accept one lifted from unrelated parent-block text. A bare `vid` is the
right key and needs no composite: `manifest.verse.store[]`'s vid space is
globally unique book-wide and `assemble.py` raises on a duplicate (a stronger
guarantee than segpack's own per-segment vid).

`--prep` also runs `evidence_verify.verify_senses` against the manifest it just
read. `canon_senses.load_senses` validates **structure only**, so a sidecar
whose cited block has since moved still loads cleanly; without the verifier,
the one authenticated place in the book a senses-only person has would be an
unchecked assertion.

## The gates

`--claims` runs P1–P7 before it emits; `--build` re-runs them (it never assumes
`--claims` saw the same bytes) and then adds B1–B6.

| gate | refuses |
| --- | --- |
| P1 | a verdict that fails `registry-verdicts.schema.json` |
| P2 | a verdict whose `input_sha256` is not the prep on disk |
| P2a | a NodeStream whose bytes are not the ones `--prep` read |
| P3 | a unit claimed by nobody, or by two people; a refusal-only unit anywhere but `refusals[]` |
| P4 | a unit the prep input does not contain |
| P5 | a quote absent from the container its locator names; a surface containing an assembly sentinel |
| B1 | an adjudication set bound to a different prep, verdict or claims document |
| B2 | a missing, duplicated or invented `claim_id` |
| B3 | applies the adjudications (below) |
| B4 | recomputes every number |
| B5 | after B3, against the cast that SURVIVED: a relation whose target person was itself refused |
| P6 | a duplicate `person_id`; a `to_person_id` outside Pass A's cast |
| P7 | a line break in a field that becomes one line of `PEOPLE.md` |
| B6 | a registry that fails `person-registry.schema.json`, before it is written |

**And to the BOOK.** The delivered NodeStream is the one input this chain reads
but nothing else pins: `--claims` takes a printed surface's evidence out of it
and `--build` counts against it, while the prep, verdict and claims digests all
cover documents rather than the corpus. So `--prep` hashes it into its own body
— every later digest inherits the binding for free — and `--claims` and
`--build` re-verify it. The failure this closes is the quiet one: a surface that
VANISHES from a re-assembled book reports `not_found_in_target_text`, which a
reader sees, but a surface that merely MOVED still counts, now against an
affirmation given for passages Pass B was shown in a different text.

**B1 binds to the VERDICT, not only to the prep.** `registry_claims.json`
carries the digest of the Pass A verdict it was projected from, inside its own
hashed body, and `--build` recomputes it. Without that, a verdict edited after
`--claims` still cites the same `input_sha256`: the build would re-project a
different claim set and apply Pass B's `claim_id`-keyed affirmations to
material Pass B never saw.

**Every judgement is adjudicated, including the negative ones and the prose.**
A `non_person_forms[]` row is a claim like any other and gets its own entry in
`registry_claims.json`; an unaffirmed one does not stand — the unit moves to
`refusals[]`, where an operator sees it. Removing a real person from the cast
is exactly as silent as inventing one. `identity_note` travels inside the
person claim for the same reason: it reaches the reader verbatim in PEOPLE.md,
so a relation asserted there is unchecked by every typed claim. `display_name`
is named in the same question. `identity_status` is adjudicated for BOTH values
— `contested` is the safe status, but its stated reason is the sentence a
genealogy reader leans on hardest, and the failure mode the issue names is a
reason that talks about SCARCITY when the field is about IDENTITY. And a
relation claim carries an identity card for BOTH parties — a bare
`to_person_id` asks the adjudicator to confirm a claim "about these exact
parties" while hiding one of them.

This is a class, not a list, and it is guarded as one — in two halves, because
either alone proves little. A marker sweep stamps every free-text field of a
verdict, runs the pass, and fails if any marker reaching `person_registry.json`
is absent from `registry_claims.json`; a second test walks
`registry-verdicts.schema.json` itself and requires every string-valued path to
be either stamped by that sweep or carrying a written reason why not. A field
added to the schema later shows up there as an unaccounted path. That walk
follows `properties`, `items`, `$defs` refs and `allOf`/`anyOf`/`oneOf` — the
constructs this schema uses; it is not a general JSON-Schema walker. One carve-out,
named: `refusals[].reason` is Pass A's own account of why it declined,
published as such — adjudicating it would be asking a second model to talk the
first out of caution.

**And no unaffirmed prose reaches a live field.** When an identity-status claim
is not affirmed the status becomes `contested` with a DETERMINISTIC reason, not
with the adjudicator's own wording: Pass B's sentence is a refutation that
nothing affirmed, and it could carry the scarcity-for-identity conflation
straight back into the field the claim exists to protect. It is kept verbatim
in `refuted_claims[]`. Adjudicator prose belongs in the refusal and refutation
sinks, never in a factual field.

**B3, the part worth reading twice.** An unaffirmed person claim **refuses**:
every unit moves to `refusals[]` with the adjudicator's reason and no person
record is emitted. It is deliberately *not* split into single-unit survivors —
a survivor was never itself adjudicated, so emitting one would put back exactly
the unadjudicated person record the claim existed to prevent, and it would need
invented rules for what name and status the survivor inherits. Every claim
owned by a refused person is cascade-refuted with `owner_identity_not_affirmed`
rather than copied to each survivor (which fabricates an edge) or assigned to
one arbitrarily (which fabricates a different one). Nothing is silently
deleted: every unaffirmed claim is in `refuted_claims[]` with its reason.

**B4, the numbers.** `mention_count` is the source-anchored occurrence count
across a person's attributable units. `printed_forms[]` counts each affirmed
surface in the corpus **the renderer actually delivers** — every node's `text`
with its verse placeholders removed, each of its verses' `rendered` and
`literal_gloss`, and only those footnotes some node's `fnrefs` reaches. That
last restriction is not pedantry: `assemble.py` deliberately puts footnotes
discovered through a definition-embedded verse into the NodeStream while
keeping them out of every node's `fnrefs`, because that verse is stripped
rather than rendered, and `render_obsidian.py` emits only what `fnrefs`
reaches. Counting the raw list would report a printed name on a page no reader
is shown.

A placeholder becomes a **hard seam** in the counting corpus — a newline, never
a space and never the verse's own text spliced in. The renderer resolves it to
the verse wrapped in markup, so no printed name spans that join; a space would
let `John⟦…⟧Smith` count a `John Smith` the book never prints, and splicing the
verse in bare would fabricate a different one. Every part of this corpus is
joined by the same seam and no surface is matched across one. The **display**
side does the opposite on purpose: a context's `target_text` substitutes each
placeholder in place, because a standalone (`mount: "block"`) verse's node text
is nothing but the placeholder while `occurrence_targets` reports its
occurrences as block-origin — handing a model `⟦VERSE_…⟧` where the rendering
belongs is the same silent under-coverage from the other side. NFC-normalized on both sides, longest-surface-first with matched spans
consumed. The consumption inventory includes the canonical target form of
*every* unit — refused and non-person ones included — **and of every canon
entry the project declared not identity-bearing**. A short surface must not
absorb a longer form that simply has no owner here.

That makes the inventory slightly WIDER than the renderer's own alternation, in
exactly one shape. A target owned only by `sense_translated` entries is dropped
from the renderer's index entirely — not de-linked, absent — so it consumes
nothing and the renderer links a shorter name inside it. Correct for a link,
where the alternative is no link at all. Counting asks a different question, and
the answer a genealogy registry needs is the other one: the book prints `John
Book`, so a `John` inside it is not a mention of a person named John, and a
wrong number is what nothing downstream catches.

The **collision** case used to diverge the same way and no longer does: since
1.32.0 (#588) the renderer's single scan consumes a de-linked target's span too,
which is what this counter already did for its own reason. Both facts are
measured against the shipped `build_entity_index` and `_Linker` — constructed the
way `render()` constructs them, with `delinked_targets` and the diagnostic
pattern, since a linker built with the defaults matches differently and a parity
test against the wrong linker reads as evidence while proving nothing. A surface claimed by more than
one person carries **no count and no owner** and is listed under
`shared_printed_forms`: attributing a shared surface to whichever candidate is
more frequent is exactly the fabricated-person error this registry avoids.

The word boundary is `boundary_ok`, behaviourally identical to the shipped
wikilinker's own `_boundary_ok` (#587) — `str.isalnum()` on the adjacent
character, never `\b`. Keeping one rule matters: a registry that counted a
printed name under a different rule than the vault links it under would
disagree with the vault about the same book, and the disagreement would read as
a data problem rather than as two implementations of one decision. For a target
language that does not space its words the rule cannot work — a following
particle is `isalnum()` too — so a surface printed in the book but with no countable span
is reported `boundary_ambiguous` with the substring count and no count. Two
things produce that: the boundary rule refused every match, or a longer
inventory form consumed them. Consumption is deliberate and matches the
renderer exactly, because it IS the renderer's construction: one alternation
over the whole inventory, sorted longest-first, scanned once, left to right.
Scanning surface by surface and masking between passes is not equivalent — it
resolves an overlap in favour of the LONGER surface whenever the longer one
starts LATER, where one leftmost-first scan prefers the EARLIER one. Over
"R. Nachman of Tulchin" the renderer links `R. Nachman`; a longest-first sweep
would consume `Nachman of Tulchin` and report `R. Nachman` as printed nowhere.
Measured, and narrower than it first looks: when the longer surface starts
earlier or at the same offset the two agree, and the same-offset case is what
longest-first exists for.
Longest-first still decides ties at ONE offset, which is the guarantee that
rule actually makes. A boundary-refused span is still consumed, so `Marie` is
not counted inside `JoAnn Marie` after `Ann Marie` was refused for its
preceding letter. Parity is measured, not asserted: a test drives the shipped
`build_entity_index` + `_Linker` over each corpus and compares which surfaces
it wraps against which surfaces this counter counts. The substring count comes from the delivered
corpus, never from the residue left after consumption: `not_found_in_target_text`
is a claim about the book, and a reader draws "the book never prints this name"
from it. None of this aborts: `Ann` inside `Anna` produces the identical
signature, and nothing in the text distinguishes the two. `--surface-boundary
none` counts substrings for a no-space target, at the documented cost that an
embedded longer form is then counted for the shorter surface.

**P7, and why a Markdown detail is a gate.** `PEOPLE.md` is built by
interpolating model-written strings into headings and bullets. A `display_name`
carrying `\n\n## Refused` writes a section no adjudication produced; an
`identity_note` carrying `\n- **son_of** X` writes a kinship edge a reader
cannot tell from an affirmed one — the fabricated relation this whole design
exists to prevent, arriving through the formatting rather than through a claim.
No adversary is needed: a model emitting a two-line note does it by accident. So
the identity fields are refused outright, naming the field, and every value the
renderer interpolates ALSO passes through a collapse-to-one-line helper — two
layers, because a single one is one edit away from being the only thing between
a stray newline and a claim the registry never made. Evidence quotes are exempt
from the refusal and covered only by the collapse: a verse spans lines, and the
quote must stay verbatim in the JSON to remain checkable against its container.

## What it does not do, stated rather than implied

- **Assembly currency is checked, not bound.** `--prep` refuses a partial
  assembly (a body segment with no node), a scope change, and a draft
  hand-edited after assembly (the ledger's `reviewed_draft_sha1` no longer
  matches `draft_sha1.draft_content_sha1`). The draft requirement covers the
  segments `manifest.segments[]` DECLARES: a `decision: regenerate` front/back
  unit becomes a node with its own `FRONTBACK:{id}` seg and is deliberately
  never a manifest segment, so demanding a draft per NodeStream seg would
  reject a valid book. It does **not** catch a segment
  revised, re-reviewed and re-converged *after* W9 ran: the new draft matches
  the new ledger and the old NodeStream still lists the segment. Binding that
  means persisting per-segment reviewed-draft hashes inside the NodeStream —
  an `assemble.py` change, and `assemble.py` is a `PLUGIN_BUNDLE_MEMBER` whose
  bytes re-stale every converged segment of every project. Refused as
  disproportionate; the artifact says `assembly_currency: "not_bound"`, and the
  remedy is to run W9r right after W9.
- **A merge over truncated context is disclosed, not refused.** With eight
  contexts per unit, any unit with more than eight occurrences truncates —
  which is every principal figure in a book. Refusing on the flag would refuse
  exactly the merges the pass exists to make. Mitigations: the kept contexts
  are an even spread across the whole book rather than the first N, the flag
  reaches both passes and the artifact (`evidence_truncated`), and
  `--max-contexts-per-form` is the operator's dial.
- **No derived inverse edges.** Only relations the book states, as adjudicated.
  An importer can invert a stated edge itself; a plugin that derived them would
  be manufacturing relations, which is one of the two failures this design
  exists to prevent.
- **Per-sense occurrence attribution is not derivable.** `canon_senses.json`
  records one verified occurrence per sense, not an assignment of every
  occurrence.
- **Evidence quotes are source-side.** The registry's *names* are target-side;
  its *provenance* is source-side.
- **One project, one registry.** No cross-volume or series consolidation.
- **The input cap is blunt.** `--max-input-chars` refuses a silently huge prep
  document; it is not a model-capacity check, because the plugin does not know
  the dispatched model's context window.
