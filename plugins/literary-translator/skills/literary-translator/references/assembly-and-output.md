# Assembly and output

## Two paths, one `output.v1_scope` switch

`profile.yml`'s `output:` block decides which of two very different
deliverables a project produces. `v1_scope` is the switch:

```yaml
output:
  v1_scope: segment_drafts_and_audit   # or: assembled_book
  destination: "/ABS/PATH/TO/YOUR_PROJECT/out/"
  target: obsidian                    # obsidian | epub | custom
  name_display:
    parenthetical_originals: never    # never | first_occurrence
  adapter_config:
    obsidian: {}
    epub: null
    custom: null
```

`destination` is where the deliverable is written, regardless of which path
runs — the audit/handoff package under `segment_drafts_and_audit`, or the
rendered book output under `assembled_book`. `target`, `name_display`,
`index`, and `adapter_config` are only ever consulted when
`v1_scope: assembled_book` — under the default `segment_drafts_and_audit`
they sit fully inert, read by nothing (Step 0d is a deliberate no-op; see
below), which is exactly why they cost a plain translate+gloss job nothing.

### Path 1 (default): `segment_drafts_and_audit`

v1's original, still-default deliverable is **not a book** — every
segment's converged draft plus its full audit trail. What it delivers, per
project:

- every segment's converged draft (`segments/{seg}.draft.json`)
- the materialized ledger (`ledger.json`), a per-segment progress/status view
  built from `runs/ledger.d/*.json` fragments
- each draft's own `validate_draft.py` audit trail
- `final_audit.py`'s whole-project summary/WARN report (`final-audit-summary.schema.json`)

The default destination resolves inside `project.durable_root` as
`${durable_root}/out/`. Step 0 checks `output.destination`'s parent only when
the destination resolves outside `durable_root`; inside-root destinations are
created at Step 0a by `mkdir -p` of the specific resolved parent, including
non-default nested paths such as `${durable_root}/exports/final/report.md`.

The final audit summary is the machine-readable completion signal. It reports
`coverage_failures`, `stale_review_failures`, `hard_failures`, `warnings`,
`project_complete`, `completeness_counts`, `frontback_coverage`, and
`generated_at`, where `hard_failures == coverage_failures +
stale_review_failures`.

W7 runs `final_audit.py` over every converged segment. `coverage_failures`
are hard failures from re-running `validate_draft.py` against each current
converged draft. `stale_review_failures` are hard failures where the current
draft sha1 no longer matches that segment's ledger `reviewed_draft_sha1`.
`warnings` counts the six WARN-only advisory checks: glossary-diff,
link-graph, foreign-remainder scan, verse-structure, forbidden-pattern
(the project's own `validation.forbidden_patterns` declarations, #520 — the
plugin ships none), and term-consistency (the project's own
`validation.terms` pins for recurring COMMON-NOUN terms of art, #199 — the
plugin ships none either; canon.json is proper-name-only, so glossary-diff
above cannot see such a term, and the count is per CARRIER so one correct
occurrence cannot mask a drifted one beside it). WARN findings are for
human review; they are never auto-fixed by guessing.

`completeness_counts` uses exactly `not_started`, `recoverable`, `stale`,
`blocked_needs_regeneration`, and `human_escalation`. `human_escalation` is the
category for materialized `blocked` or `non_converged` statuses.
`project_complete == (every one of completeness_counts' four non-'stale'
values == 0 AND completeness_counts.stale - stale_previously_converged -
len(stale_contract_admitted) == 0)`. In the plain case that means every
`manifest.json` segment, including translate-decision `FRONTBACK:{id}` units,
classifies `reusable`; the two subtractions are the named carve-outs for
segments that DID converge and only look stale afterwards. The first
(`stale_previously_converged`, #409/#491, always on) covers a machinery-only
cache-key move. The second (`stale_contract_admitted`, #533) covers a
`style_contract_hash` move and exists only when `profile.yml` declares
`validation.admit_contract_only_stale`; it is a list of segment ids, omitted
from the summary entirely when the declaration is absent or nothing qualified.
The two populations are disjoint by construction — the first requires EVERY
moved field to be machinery-only, the second requires `style_contract_hash`
to be among them, and that field is not machinery-only.
`completeness_counts.stale` itself stays the RAW count either way.

**#208 — completeness fail-closed gate.** `final_audit.py`'s exit code is no
longer purely a function of `hard_failures`. It now exits
`completeness_exit_code(hard_failures, project_complete)`: `0` only when both
hard checks (coverage, stale-review) are clean AND the whole-project
completeness gate reports `project_complete: true`; `1` if either hard check
fails (unchanged priority over incompleteness); `3` if hard checks are clean
but the project has not fully converged (any of `not_started`/`recoverable`/
`stale`/`blocked_needs_regeneration`/`human_escalation` segments remain).
This closes the previous gap where a project with unconverged segments
silently exited `0` on the default `segment_drafts_and_audit` delivery path,
giving it no deterministic delivery-refusal gate to match the engine-loop
HARD rule already enforced on the `assembled_book` path (`assemble.py:3481`'s
`assert_project_complete`). `warnings` and the frontback coverage report
remain purely informational.

There is one `frontback_coverage` entry per `manifest.json` `frontback[]` item.
Each entry has `id`, `decision: "translate"|"regenerate"|"omit"`, and
`status: string|null`. For `decision:"translate"`, `status` is the matching
segment's own classification. For `decision:"regenerate"` or `decision:"omit"`,
`status` is `null`. The field is always present, with an empty array when there
is no front/back matter.

That bundle — converged drafts plus the full audit trail — is Path 1's
deliverable. This scope boundary does not remove W6 or W7: the hand-maintained
`consistency_issues.md` consistency pass and the automated `final_audit.py`
final audit both still run regardless of which `v1_scope` path is selected.
W6 runs after every batch, before the next batch starts;
`consistency_issues.md` is never the output of an automated script and is
never read back in or acted on programmatically.

At W8, the handoff report must list any `blocked`/`non_converged` segments and
surface W7's per-category counts alongside `project_complete`. It must keep
"this batch: N converged, zero hard defects" separate from "whole project: M of
TOTAL still incomplete"; a batch can succeed while the whole project is still
incomplete. Delivery must not mark the audit package complete while any item
remains `blocked` or `non_converged` — under either `v1_scope` path, since
`assembled_book` (below) is itself gated on `project_complete: true`.

### Path 2: `assembled_book`

Selecting `v1_scope: assembled_book` turns on **Step 0d** (resolve the
output-target adapter) and **W9 Assemble** (run it), producing one rendered
book output instead of — really, in addition to, since W7/W8's audit trail is
unconditional — the segment-drafts handoff. This increment ships exactly one
working target, `obsidian`. `epub` does NOT resolve: `render_epub.py` has never
been written, so Step 0 and Step 0d both HALT on it, naming the missing module
and the alternatives (see "Why `build_epub.py` hasn't been generalized" below).
`custom` is unchanged: a null `adapter_config.custom.renderer_path` halts for
co-design, while a non-null, path-safe value resolves to that project's own
renderer and renders (see `references/output-target-adapters/README.md`).

#### Step 0d — resolving the target, early

Step 0d runs right after Step 0c, and only when `v1_scope: assembled_book` —
under the default `segment_drafts_and_audit` it is a deliberate no-op, zero
resolution work. When it does run, it resolves the already-schema-validated
`output.target` (`obsidian` | `epub` | `custom`) to a concrete adapter, plus
reads `name_display`/`index`/the one `adapter_config.<target>` sub-block that
matches. The reason this happens at setup time — right alongside Step 0c's
source-format resolution, long before W9 — rather than only when assembly
actually runs, is the same reason Step 0c resolves `source.format` early: a
`target: custom` project with a null `adapter_config.custom.renderer_path`
needs the co-design conversation to start immediately, not be discovered
after every segment has already converged. See `SKILL.md`'s Step 0d for the
exact HALT/FATAL conditions and `references/output-target-adapters/README.md`
for the adapter contract Step 0d resolves into.
Runs only when `output.v1_scope: assembled_book`. Under the default
`output.v1_scope: segment_drafts_and_audit`, Step 0d is a deliberate no-op —
zero resolution work, zero HALT risk — matching the proportionality
guardrail that a plain translate+gloss job never pays for assembly
machinery it will never read (`references/assembly-and-output.md`).

When `assembled_book` is selected, resolve the already-schema-validated
`output.target` (`obsidian` | `epub` | `custom`) via `output_resolve.py`'s
resolution logic, plus read `output.name_display` and the one
`output.adapter_config.<target>` sub-block matching the resolved
target — the others sit inert. This step depends ONLY on the
already-validated `profile.output` block (no manifest, no ledger, no draft
required yet) — the same "resolve early, from validated shape alone"
posture Step 0b/0c already apply to `verse_policy.mode`/`source.format`, so
a blocking co-design need surfaces at setup time, never mid-project.

- `target: obsidian` resolves to the built-in `render_obsidian` adapter
  (shipped this increment). `target: epub` maps to the built-in name
  `render_epub`, a later-phase adapter **not yet written** — so it does not
  resolve: `output_resolve.py` HALTS, naming the missing `render_epub.py` and
  the three ways out (stay on `v1_scope: segment_drafts_and_audit` and build
  the EPUB with a project-local script; `target: custom` with a co-designed
  renderer; or `target: obsidian`). The enum-to-name mapping is exhaustive
  over the enum; it was never a claim that every module it names exists, and
  until this check that gap only surfaced at W9, with the whole book already
  translated and converged (#726).
- `target: custom` specifically: the schema validates shape only — the
  `adapter_config.custom.renderer_path` key is required whenever
  `target: custom`, value must be `string | null`. Step 0d owns the two
  procedural checks a schema can't express, the same split Step 0c already
  applies to `source.adapter_config.custom.extractor_path`:
  - `null` — valid, the expected starting state — HALT and co-design a
    hand-crafted Python renderer with the user (informed by
    `render_obsidian.py` as a starting pattern), against the fixed
    `render(nodestream, canon, profile, out_dir) -> dict` entry-point every
    built-in adapter implements
    (`references/output-target-adapters/README.md`).
  - Non-null — FATAL rejection (before any existence check) of any value
    containing `..`, starting with `/`, or not matching the schema's
    `^[A-Za-z0-9._/-]+$` pattern. Resolution is against a fixed subtree,
    `${durable_root}/scripts/custom_renderers/<value>`, never an arbitrary
    filesystem location. Only then does Step 0d check the resolved path
    actually exists — FATAL, naming the unresolvable path, if not.

Unlike a Step-0c custom-source HALT, which blocks the whole project before
extraction can even begin, a Step-0d custom-target HALT blocks only
assembly (W9) — a project can still scaffold, translate, and converge every
segment with the co-design conversation still outstanding, and only hits
this HALT once `output.v1_scope: assembled_book` is actually chosen.


#### W9 Assemble — the reconstruction algorithm

`scripts/assemble.py` is a deterministic stdlib script — never an agent
workflow, no review/fix loop, no `assemble-wf.template.js`. It performs a
**three-source join**: a converged draft alone is unassemblable, since it is
pure keyed content with no order or structure. Order and structure live only
in `manifest.json`.

1. Load `profile.yml`, `manifest.json`, `ledger.json`, `canon.json`. Iterate
   `manifest.segments[]` in array order (each segment's `block_ids[]` is
   already `order_index`-sorted internally). `manifest.blocks{}.order_index`
   is the single whole-book reading-order axis — `spine[]`'s raw native file
   order and any per-segment-local `segpack.blocks[].order_index` are both
   red herrings, never the stitch key.
2. **Gate on the ledger, per segment:** a segment is assembled when its
   materialized `runs/ledger.json` status is `converged` — or `stale` under
   one of the two named carve-outs below — AND its on-disk draft sha1 still
   matches that fragment's `reviewed_draft_sha1`. That sha1 comparison is the
   same guard `final_audit.py`'s hard check 2 uses, it is FATAL rather than a
   silent skip, and neither carve-out relaxes it: a hand-edit the reviewer
   never saw can't silently ship inside an assembled book either. The two
   carve-outs mirror W7's own, so the two gates never disagree about a
   record — (a) **machinery-only** (#491, always on): every field in
   `stale_mismatched_fields` is in `{plugin_bundle_hash, schema_hash,
   derivation_bundle_hash}` and the `.ever_converged.<seg>` sentinel is not
   absent; (b) **contract-only** (#533, only when `profile.yml` declares
   `validation.admit_contract_only_stale: true`): the same sentinel condition,
   plus `style_contract_hash` among the moved fields and every other moved
   field machinery-only. Units admitted by (b) are listed in `assemble.py`'s
   own `contract_stale_admitted` stdout key and named on stderr — they ship
   without having been judged against the current style contract, which is a
   decision the operator made, not one the tool inferred. Carve-out (b) has
   one refusal of its own (#773): a unit with an UNSPENT claim record — a
   `--from-converged` claim VOIDED its stored review and the re-review never
   completed — is refused rather than admitted, because the
   `reviewed_draft_sha1` it would ship against is that voided review's. The
   same refusal guards step 2a's live check below, which admits a
   contract-only drift independently of the merged status. Spentness is an
   ORDERING over the ledger: the claim record's `claimed_at` against the
   fragment's own convergence `timestamp`. Neither of the two obvious
   comparisons works — the review document's `dispatch_token` is reused across
   resumes and same-round redispatches, and the stored `cache_key` carries no
   draft or review identity, so a hand-edited draft that IS re-reviewed and
   re-converged writes the identical key back. A moved `cache_key` is still
   honoured as a secondary proof of convergence, for the case where the
   fragment's timestamp cannot be ordered. The whole run is
   additionally gated on W7's `final-audit-summary.project_complete: true`
   (see Path 1 above) before assembly starts at all.
2a. **Confirm the ledger snapshot against the LIVE inputs (#492).** Everything
   in step 2 reads `runs/ledger.json` — the snapshot the last `ledger_merge.py`
   run produced — so on its own it cannot see a content input edited since that
   merge. Immediately after the completeness gate, `assemble.py` re-derives the
   twelve content-affecting cache-key fields from the durable root by calling
   `cache_key.py`'s own field computers, and compares them to every SHIPPED
   record's stored `cache_key`. A moved field refuses with
   `reason: stale_live_inputs`, naming each segment and each field, and tells
   the operator to re-run the merge. The invariant is ONE-DIRECTIONAL, and
   deliberately so: a record the snapshot still calls `converged` can no longer
   ship on a merge that predates the edit. The pipeline does normally run W7
   before W9, but nothing enforced the ordering, and getting it wrong produced
   a finished-looking book rather than a halt. The reverse direction is NOT
   rehabilitated and is not meant to be: a record the snapshot already calls
   `stale` is refused by the completeness gate in step 2 before this check ever
   runs, even where the live inputs have since reverted to the reviewed key.
   That refusal is fail-closed and its remedy is one command (re-run W7);
   re-deciding a reverted key here would make assembly a second admission
   authority over a verdict `ledger_merge.py` owns, which is exactly the design
   #492's own body records as tried and rejected.
   Three things bound it. The machinery-only trio above is EXCLUDED — the check
   is exactly the complement of that carve-out, so a plugin upgrade still
   cannot strand a finished book. Only
   segments the CURRENT manifest requires are checked, so a retained
   out-of-manifest entry cannot newly block an assemblable book. And carve-out
   (b) applies here identically, sentinel condition included: under the
   declaration, a live drift whose only moved field is `style_contract_hash`
   and whose `.ever_converged.<seg>` sentinel is not absent is admitted and
   joins the same `contract_stale_admitted` list — which, being observed at
   assembly time, may therefore name units that W7's own ledger-derived list
   (and `validate_assembled.py`'s, and `validate_conservation.py`'s) does not
   yet contain. It adds no write, no persisted artifact and no subprocess.
3. For each block: translated text comes from `segments/{seg}.draft.json`;
   its manifest type and `source_html` presence decide `medium`
   (`html`|`plain`). A block whose `type` is `HEAD`, **or is listed in the
   manifest's `heading_types`**, is classified `heading` (#210).
   `heading_types` is an optional, additive, manifest-declared array of
   block-type tags — absent means only `HEAD` is a heading, byte-identical
   to pre-#210 behavior. The heading's rendered text comes directly from the
   block's own translated draft text, put into the heading node by assembly
   itself — it is never superseded by the segment's own `title_text`, which
   only feeds the segpack `title` field and is never an assembly fallback for
   an empty or missing heading. A block that is some verse's `parent_block`
   with `mount: block` is classified `verse` — but the heading test takes
   precedence over the block-mount-verse test, so a declared-heading block
   that is also a block-mount verse parent classifies `heading`, exactly like
   `HEAD` already does today. Everything else is `prose`. `FN:{N}` definition
   blocks are never rendered
   inline — they live in `manifest.blocks{}` with their own `order_index` but
   are never members of a body segment's `block_ids[]`; they surface only via
   the footnotes table.
4. Front/back matter follows its `manifest.frontback[]` disposition:
   `translate` assembles normally from its draft; `regenerate` has no draft —
   the assembler emits a documented placeholder node plus a warning
   (full regeneration is a later-phase refinement, not Phase 0/1);
   `omit` is dropped.

#### Heading levels (`heading_levels`, #210)

Every heading node built before this feature existed rendered at a
hardcoded markdown level 2 (`##`). `manifest.json` may now declare an
optional `heading_levels` map — `{block_type: level}`, level an integer
1-6 — sibling to `heading_types`. Assembly looks up a heading block's own
`raw_type` in that map and stores the result as the node's `level`; a type
absent from the map, or an absent map entirely, resolves to **2** —
byte-identical to pre-1.12.0 output for any project that does not opt in.
A non-heading node's `level` is always `None`.

Every key of `heading_levels` must be a member of `heading_types ∪
{"HEAD"}` — a key outside that set is a typo that would otherwise silently
no-op, since it would never be looked up against any real block.
`assemble.py` enforces this itself, raising `AssembleError` on violation,
rather than trusting that W2 already ran (`assemble.py` is also reachable
on a resumed project); `validate_extraction.py` enforces the identical
rule independently at W2 (see `SKILL.md`'s W2 Extract). There is no
schema file for the NodeStream IR this document describes — this contract
doc is the authoritative shape.

`render_obsidian.py` clamps rather than trusts: a malformed `level` on a
`kind:"heading"` node (absent, `None`, a non-`int`, a `bool`, or outside
1..6) renders at level 2, a renderer fail-safe rather than a second
validation gate — a raw `#` run must never reach 0 (which would silently
demote a heading to bare prose with a stray leading space) or exceed 6
(not a valid ATX heading).

Assembly's own default-to-2 behavior is unchanged by #233: what's new
is disclosure, not resolution. `validate_extraction.py` now prints the
resolved outline at W2 — a `NOTE heading_level_outline:` naming every
cited tier's resolved level and whether it came from `declared` or
`default`, plus a `WARN` when a book citing two or more tiers took any
of them at the default — and that scan is report-only, touching neither
`derivable_ok` nor `region_ok`. See `SKILL.md`'s W2 Extract.

#### Sentinel resolution — fail closed

Two sentinel families appear byte-for-byte inside `draft.blocks[id]` strings:
`⟦FNREF_N⟧` (matched against `draft.footnotes[str(N)]` and
`manifest.footnotes[].n`) and each verse's exact `⟦VERSE_{vid}_{8hex}⟧`
placeholder (mapped to `vid` via the segpack's `verses[]`, then resolved
through `draft.verses[vid]`). The assembler substitutes the stored
placeholder string verbatim — it never reconstructs the token from `vid`,
since the 8-hex suffix is opaque.

The bijection is enforced, and a violation is fatal (exit 1), never silently
emitted: every `⟦FNREF_N⟧` present in any block text has exactly one
`draft.footnotes[str(N)]`; every verse placeholder present has exactly one
`draft.verses[vid]`; footnote `n` is unique book-wide; no dangling reference,
no duplicate. Under `verse_policy.mode: skip`, a verse's `content == {}` is
expected — there is intentionally no verse body to insert. As a corollary of
that voided content, a `skip`-mode footnote whose *sole* citation site is a
mode-voided verse's own content is legitimately unresolvable-by-design (no
sentinel scan can reach it, yet the draft still supplies its text) and is
allowed through, not treated as an orphan footnote — it is stripped, never
rendered, so nothing dangles; any verse embedded in that footnote's own
definition is likewise marked referenced (never orphaned) and stripped. A
footnote definition's own nested sentinels are stripped, not recursively
expanded, in Phase 0/1 — proportional to what the reference project's own
markdown path does.

#### The NodeStream and anchor-map artifacts

`assemble.py` builds an in-memory NodeStream and also writes it to
`${durable_root}/out/.assembled/nodestream.json`:

```
NodeStream = {
  "book":      { "seg_order": [str,...], "title": str|null },
  "nodes":     [ BlockNode, ... ],                    # whole-book reading order
  "footnotes": [ { "n": int, "text": str }, ... ],    # book-wide, unique n, ASCENDING
  "meta":      { "target": str, "verse_mode": str, "apparatus_policy": str },

  # OPTIONAL, present ONLY under output.entity_markup with index_from: markup
  # (see "Inline entity markup" below). Absent otherwise -- an absent key is
  # not the same as an empty one, and the renderer ignores this key entirely
  # unless its own mode predicate says index.
  "entity_markup": { "spans": { "<n>": {"tag": str, "payload": str,
                                        "ref": str} } }
}
BlockNode = {
  "id": str, "seg": str,
  "kind": "heading" | "prose" | "verse",   # semantic, derived per the algorithm above
  "raw_type": str,                        # manifest type, opaque passthrough
  "order_index": int,
  "medium": "html" | "plain",
  "text": str,                            # translated text, sentinels still inline
  "level": int | None,                    # heading level 1-6 (#210), else None
  "fnrefs": [int, ...],                   # footnote numbers referenced in this block
  "verses": [ { "vid": str, "placeholder": str, "content": <object|{}> }, ... ]
}
```

The NodeStream carries sentinels-in-text plus resolution data — it does
**not** pre-render them. Substituting the verse placeholder for rendered
verse, `⟦FNREF_N⟧` for a target-language footnote-ref, and appending
footnote definitions, is each output-target adapter's own job at render
time. This is what keeps the two adapters (this increment: `obsidian`; a
later phase: `epub`) diverging only at render time, never in how the book is
reconstructed.

A companion `${durable_root}/out/.assembled/anchor_map.json` mirrors the
node order for structural resync:

```
{ "blocks":    [ {"block_id","seg","kind","order_index"}, ... ],
  "footnotes": [n, ...],
  "verses":    [vid, ...] }
```

used by `diff_rendered_output.py` for structural-completeness checking and
keyed resync, so one inserted node doesn't cascade a mismatch across the
whole diff.

#### Inline entity markup — `output.entity_markup` (1.73.0, #795)

A book whose names are not knowable before translation cannot seed a name
list, so its translator marks entities inline as it goes. Before 1.73.0
nothing here recognised such markup, so it reached the reader verbatim and
the index covered only what canon happened to carry. `output.entity_markup`
is how a project declares the convention:

```yaml
output:
  entity_markup:                 # ABSENT -> no scan, no key (see the mode table)
    tags: [person, place, work]  # required; the element names the translator may emit
    ref_attribute: ref           # optional, default "ref"
    index_from: markup           # optional, canon | markup; default canon
```

Three effective modes, resolved independently in `assemble.py` and
`render_obsidian.py` from the same profile fields (the `mentions_section`
predicates are the precedent for that discipline):

| profile | mode | behaviour |
| --- | --- | --- |
| block absent | `off` | no scan, no `entity_markup` key; assembled output byte-identical (the renderer has two unconditional changes of its own — obsidian.md, "Editorial brackets") |
| present, `index_from` absent or `canon` | `strip` | the declared elements are removed and their payload kept; no sentinels, no new notes |
| present, `index_from: markup`, `output.target: obsidian` | `index` | elements become `⟦ENT_n⟧payload⟦/ENT_n⟧` and the spans are recorded; the adapter mints and links notes |
| present, `index_from: markup`, any other target | FATAL | `entity_markup_index_unsupported_target` — no other shipped adapter consumes the spans, and degrading to `strip` would hand the operator an index they asked for and did not get |

The grammar is deliberately narrow — `<TAG>` / `<TAG REF="…">` … `</TAG>`,
non-nested — so an unknown angle-bracket run in the prose is source text and
survives untouched. What it will NOT do is pass a malformed use of a name the
project itself declared: every position where a declared tag name follows
`<` or `</` must begin a token the grammar matches in full, so `<person/>`,
`<person ref=x>` and a bare unterminated `<person` are refused rather than
delivered. `assemble.py` scans block text, verse `rendered` and
`literal_gloss`, and footnote definitions, and refuses (exit 1, one JSON
line, `reason` named) on the following. The first two are about the MARKUP
and fire in both modes; the last three defend the RENDERER's emission
grammar and are checked in `index` mode only — `strip` puts the payload back
into the prose byte for byte and emits no wikilink, no note name and no
heading, so a bracket, a pipe, a line break or a sentinel inside a marked run
is ordinary text there and refusing it would be a false RED on input that
mode handles correctly.

- `entity_markup_config_invalid` — the block's own shape. Re-checked HERE
  because `assemble.py` loads the profile through `validate_draft.py`'s
  loader, which does not run jsonschema; `profile_validate.py`'s Step-0 gate
  is not on this path, and a profile can be hand-edited after Step 0. A bare
  string `tags: person` is the case that makes this load-bearing: it is
  iterable, so an unvalidated reader builds a per-CHARACTER alternation and
  reports success.
- `entity_markup_malformed` — unpaired, nested, mismatched, or a malformed
  declared-tag token as above.
- `entity_markup_span_contains_sentinel` (index mode) — a machine sentinel
  or a verse placeholder inside the payload or inside the `ref`. `<person>X⟦FNREF_1⟧</person>`
  would render as `[[People/X|X[^1]]]`, whose footnote closer collides with
  the wikilink closer; a sentinel inside a `ref` passes the sentinel
  validator when it names a real footnote and would then be lifted out of the
  narrative and written into a note's own heading. The guard reaches node and
  verse text only — footnote-definition sentinels are already stripped in
  Phase 0/1, before this pass runs, so nothing leaks there and the guard
  simply has nothing to say.
- `entity_markup_span_unsafe_text` (index mode) — `[`, `]`, `|`, CR or LF in
  the payload or the `ref`. Each of those breaks the wikilink alias or the note name the
  renderer interpolates them into, and none of them requires a hostile
  author.
- `entity_markup_span_unrendered` (index mode) — a span in the `text` of a
  `kind: "verse"` node. The renderer builds such a node from `verses[]` alone and ignores its
  `text`, so the span would be recorded, counted and never delivered.

The summary JSON reports `entity_markup: {mode, strings_scanned, spans,
tags}` whenever the block is declared. A book may genuinely carry no markup,
so a zero is not a refusal — but it is VISIBLE, rather than
indistinguishable from a scan that never ran.

#### The adapter entry point

Every built-in output-target adapter module exposes the same signature:

```python
def render(nodestream: dict, canon: dict, profile: dict, out_dir: Path) -> dict:
    """Writes the artifact(s) under out_dir. Returns a small manifest
    { "written": [relative_path, ...], "kind": "vault"|"file" } for the diff tool."""
```

`assemble.py` resolves `output.target` to either a flat sibling module name it
imports directly from `assets/scripts/` (`render_obsidian` — `render_epub` is
mapped but unwritten, and halts at resolution rather than being returned), or —
for `target: custom` — a `Path` loaded via `importlib` from the fixed
`${durable_root}/scripts/custom_renderers/`
subtree (see `references/output-target-adapters/README.md` for the full
resolution/path-safety rules). `out_dir` defaults under
`${durable_root}/out/`, respecting `output.destination` when it is set —
Step 0a already `mkdir -p`s its resolved parent.

#### Render + diff — the acceptance gate

`scripts/diff_rendered_output.py` reduces the ALREADY-rendered output and
diffs it against the last accepted baseline -- it renders nothing itself, so a
stale out/ is compared as it stands. It is a stdlib-only markdown-aware reduction (no `bs4`):
normalize line endings, `rstrip()` trailing whitespace per line while
preserving leading indentation (markdown is whitespace-significant), strip a
trailing blank-line tail. For a vault-shaped render (many files), the
reduction concatenates files in sorted-relative-path order, each preceded by
a `--- <relpath> ---` header line, before line-reducing the whole. The
verdict is exact-equality of the reduced sequence, compared positionally
with `itertools.zip_longest` so every failure accumulates rather than
short-circuiting on the first one; `difflib` produces a readable report
alongside the exact-equality verdict.

Exit codes and a one-line JSON stdout `reason`: `0` = match (`"ok"`); `1` =
mismatch or a guard refusal (`"mismatch"` / `"candidate_not_built"` /
`"baseline_dir_not_found"` / the out_dir refusals, whose reason strings
name symlinks but also cover a `..` traversal destination); `2` = no baseline exists
yet (`"no_baseline"`) or a profile precondition failed
(`"profile_precondition"`). The script's own module docstring carries the full
`reason` set, including the one JSON line that carries no `reason` at all
(`main()`'s defensive catch-all). `--accept-baseline` freezes the
current reduced render as the new baseline, and is itself
overwrite-guarded — it refuses (exit 1) if a baseline already exists unless
`--force-accept-baseline` is also passed. The baseline is stamped with a
render-version/hash so a stale-renderer baseline is detectable. There is no
separate item-count acceptance check anywhere in this pipeline — the
render+diff comparison **is** the gate for rendered-content equality.

**Two-tree mode (`--baseline-dir DIR`).** There is exactly one frozen baseline
per durable root, and `--accept-baseline` overwrites it with whatever
`--candidate-dir` names — so a project that POST-PROCESSES the rendered vault
could only compare its result by destroying the reduction the pipeline's own
acceptance gate depends on. `--baseline-dir A
--candidate-dir B` reduces *both* directories with the same reducer and
compares them positionally — the same verdict rule, two supplied inputs. It is
read-only (no baseline is read, written, or required, so a project with no
`out/.baseline/` at all can use it), it is mutually exclusive with
`--accept-baseline`, it never reports `stale_baseline` (there is no stored
render-version behind either tree), and its `ok`/`mismatch` payloads carry
`"mode": "two_tree"` so a consumer cannot mistake one verdict for the other. A
missing `--baseline-dir` is exit `1`, `reason: "baseline_dir_not_found"`.

#### Structural-completeness gate (`scripts/validate_assembled.py`, #202)

A distinct gate from render+diff above — this one checks that a declared
heading *surfaced at all*, not whether the rendered bytes exactly match a
baseline. A NEW, standalone, self-anchored script (same convention as
`final_audit.py`/`validate_draft.py`) enforcing the UNION
structural-completeness invariant: every block whose `type` is in the
manifest's declared heading set (`heading_types` ∪ the built-in `HEAD`,
#210) must surface, book-wide, as non-empty translated text. Source markers
are a `Counter` keyed by `(seg, block_id)` over the FULL manifest (not only
converged segments) — a `Counter`, not a set, because the schema allows the
same `(seg, block_id)` key to legitimately recur (a repeated id within one
segment's `block_ids[]`, or two `segments[]` entries sharing a `seg`), and
only a per-key count catches a dropped occurrence hiding behind its
surviving twin.

Runs in BOTH output scopes: at W7/W8 (default `segment_drafts_and_audit`)
checking converged draft text and rebinding to each draft's ledger
`reviewed_draft_sha1` (mirroring `assemble.py`'s own hand-edit-after-review
guard, `manifest.json`-gated per §2 above); at W9 (`assembled_book`, after
`assemble.py` writes `nodestream.json`, before render+diff) checking the
assembled NodeStream's own `kind:"heading"` nodes instead. A broad
heading-like type allowlist (`HEADING|TITLE|CHAPTER|SECTION|PART|SIMAN|
PEREK|H[1-6]`) fires a non-gating WARN for an undeclared block, but never
gates the HARD exit code — the declared set is the sole non-heuristic
source of truth. Exit `0` clean / `1` HARD defect / `2` env-usage; one JSON
line `{"defects":[...], "warnings":[...]}` to stdout. See `SKILL.md` W7/W8/W9
for the exact invocation points.

#### Output-coverage — the OPT-IN ratio-outlier lane and the blind spot

  - **New in 1.12.0 — a within-cohort output-coverage ratio-outlier
    surfacer, `Refs #202` (this does NOT close #202 — see the limitation
    below).** OPT-IN: config `validation.conservation_ratio_band`
    (`min_source_words_band`/`min_cohort`/`k`/`abs_guard`); absent or
    `null` means this lane does not run at all and `output-coverage`
    behaves exactly as it did in 1.11.0. Per cohort
    (blocks sharing a manifest `type`), it flags `low_coverage_outlier`
    when a block's output/source word ratio falls below a robust
    median-and-MAD fence computed from its OWN cohort, AND is well below
    that cohort's own typical ratio (a second, independent `abs_guard`
    condition that defends against a degenerate near-zero-MAD cohort).
    `zero_output_block` and `insufficient_sample` (naming a `reason`)
    cover the edge cases; a `coverage_distribution` entry
    (`median_ratio`/`mad`/`fence_ratio` per cohort, `null` when nothing
    was eligible to compute them from) rides alongside the warnings on the
    same stdout JSON line.
  - **Stated limitation — this lane structurally cannot close #202.** It
    is a within-cohort comparison, never an absolute truthfulness check:
    if every block in a cohort is truncated by roughly the same
    proportion, that cohort's own median absorbs the truncation and
    nothing reads as an outlier — detecting uniform collapse would need a
    reference outside the audited population, and none exists here. It is
    also NOT language-pair-agnostic: `normalize_words()` is NFC +
    whitespace splitting only, no morphological/markup/sentinel
    normalization, so agglutinative/compounding target languages and
    markup-heavy blocks produce ratios that are not linguistically
    comparable across language pairs. What it DOES catch: a few collapsed
    blocks amid an otherwise healthy cohort — proportional truncation
    across a range of block sizes, which the absolute floor above cannot
    see at any single fixed `(min_source_words, max_output_words)` pair.

#### W9 Assemble — the run order, gate by gate

**W9 Assemble** (only when `output.v1_scope: assembled_book`) — assembly
runs as a plain DETERMINISTIC script step (`assemble.py` then
`diff_rendered_output.py`), never an agent workflow: it has no
agent-workflow template of its own, and none is planned. Assembly has no
review/fix loop and no ledger prompts to schema-validate, so it does not
mirror `mass-translate-wf.template.js`'s agent machinery. Gated on W7's
`final-audit-summary.project_complete: true` — the whole-project
completeness gate, not merely "this batch converged" — assembling a book
from a project that is not yet fully converged is refused, never silently
attempted over a partial set. Because that ONE verdict gates the whole step,
both gates must agree about every unit: `assemble.py` re-derives the same two
carve-outs (the #491 machinery-only one and, when
`validation.admit_contract_only_stale` is declared, #533's contract-only one)
from the same merged ledger rather than trusting the summary. When either
admits a unit, both name it — `stale_contract_admitted` in W7's summary,
`contract_stale_admitted` in `assemble.py`'s, and a stderr block in each.
One asymmetry since #492, and it is deliberate: W9 ALSO admits a contract-only
drift it detects itself, by comparing the live inputs against each shipped
record (same declaration, same sentinel condition). Such a unit is still
`converged` in the merged ledger, so W7's summary — which reads that snapshot —
has nothing to name yet. W9's list is therefore the authority on what THIS run
shipped unjudged against the current contract, and may legitimately exceed
W7's; re-running W7 after the merge brings the two back into step. The same
holds for `validate_assembled.py` and `validate_conservation.py`, which derive
their own lists from that snapshot too.
Keep the declaration stable across the whole W7→W9 chain; toggling it between
steps is the only way a normal run can make the two gates disagree about the
same book. (They read the moved-field list from different authorities -- W7
from `select_segments.py`'s recomputation against the CURRENT cache key, W9
from the materialized `stale_mismatched_fields` -- but `ledger_merge.py:873-887`
drops any inherited value and re-derives that list from the same diff, so the
two agree by construction on anything a run produces. Hand-editing the
materialized `ledger.json` between the two steps can split them; so can
editing one segment's `status` to `converged`, which skips both carve-outs
entirely.)

Run `scripts/assemble.py`, which reconstructs the whole-book reading order
from `manifest.json` + every converged segment's draft + `ledger.json`'s
convergence gate into the shared NodeStream artifact, then invokes the
Step-0d-resolved output-target adapter (`render_obsidian` in this
increment) to render the book under `${durable_root}/out/` (see
`references/assembly-and-output.md` for the reconstruction algorithm and
the NodeStream/anchor-map artifacts).

Then run `scripts/validate_assembled.py` — AFTER `assemble.py` writes
`out/.assembled/nodestream.json`, BEFORE `scripts/diff_rendered_output.py` —
the same #202 structural-completeness gate, this time checking that every
declared heading source marker surfaced as a non-empty `kind:"heading"` node
in the assembled NodeStream. Exit `1` HARD on a dropped/misclassified
heading; exit `0` with non-gating WARN entries otherwise.

Then run `scripts/validate_conservation.py output-coverage` — the same
WARN-only #202 floor + within-cohort ratio-outlier lane as W7 (see above),
this time reading `out/.assembled/nodestream.json`
(`output.v1_scope: assembled_book`) instead of converged drafts. Never
gates; exit `0` always barring an env/usage precondition.

Then run `scripts/diff_rendered_output.py` as the acceptance gate: it
reduces the ALREADY-rendered output (it renders nothing itself) and diffs it
against the last accepted baseline — exit `0` on an exact match, `1`
on a mismatch or guard refusal, `2` when no baseline exists yet
(`--accept-baseline` freezes the current render as the new baseline). For
rendered-content equality, the render+diff comparison IS the acceptance
gate — there is no separate item-count check alongside it (structural
completeness is `validate_assembled.py`'s distinct concern above, checked
before this step ever runs). To compare two ALREADY-rendered trees instead —
the check a project that post-processes the vault needs, and the only one the
frozen baseline cannot express — pass `--baseline-dir A --candidate-dir B`:
read-only, no baseline involved, `"mode": "two_tree"` on the verdict.

Then — for `output.target: obsidian`, ON BY DEFAULT unless explicitly
disabled (`output.adapter_config.obsidian.mentions_section.enabled: false`) —
run `scripts/validate_backlinks.py` as an **advisory** appendix-integrity gate,
AFTER `diff_rendered_output.py`. It re-derives the source-anchored occurrence
universe and checks that every index-eligible entity's `## Mentions` section
covers its occurrences (metric 1, the sole warning source), plus a
native-inline-backlink diagnostic and collision/unresolved-homonym reports
(metric 2, exit-neutral). Unlike the hard gates above, its **exit `1` is
ADVISORY — log the warnings and CONTINUE W9** (it never blocks assembly);
only exit `2` (unreadable/malformed input, e.g. a missing
`out/.assembled/nodestream.json`) halts. When the target is not obsidian, or
the flag is explicitly disabled, it short-circuits to
`mentions_coverage.status: disabled`, exit `0`. Against a vault whose entity
notes a post-processing layer renamed or merged, pass `--entity-note-map FILE`
(a JSON `{source_form: vault-relative *.md path}`) — without it the gate
re-derives every note path from the renderer's own rule and reports the whole
vault missing. The `## Mentions` section is
a source-anchored occurrence index (mirroring the SSK `build_index.py`
model) that supersedes the older "native backlinks are the occurrence index"
stance for `output.target: obsidian` projects; see
`references/output-target-adapters/obsidian.md`.

## Why `build_epub.py` hasn't been generalized (why `epub` isn't shipped yet)

The real reference project, `historiettes-t3`, has its own
`build_epub.py` (704 lines), confirmed to exist. It is **not**, however,
independently audited or generalized the way `final_audit.py` was before
being brought into this plugin as `scripts/final_audit.py` — and it is not
what backs `output.target: epub` in this increment, because it hasn't been
verified to fit the fixed `render(nodestream, canon, profile, out_dir)`
adapter contract above.

Concretely: `build_epub.py` exists in the source project, but this
increment's plan has not yet read it end to end, verified its actual
behavior against its own code (the same discipline already applied to
`final_audit.py` — trust the code, not the docstring or a prior plan's
description of it), or decided how much of it generalizes cleanly to
arbitrary language pairs / source formats versus how much is specific to
Historiettes' own layout. Until that reading happens, `output.target: epub`
stays a *declared* enum value that does not resolve: Step 0
(`profile_validate.py`) and Step 0d (`output_resolve.py`) both HALT on it,
naming the missing `render_epub.py` and the three alternatives. It is a later
phase, not this one — and the halt is what says so at setup time rather than at
W9, after a whole book has been translated and converged (#726).

The same discipline applies to any future `epub` output-target effort:
`build_epub.py`'s real, current behavior — not this reference's description
of it, nor the plan that preceded this plugin — is the ground truth, but
verifying it firsthand is only possible for whoever has access to the
non-shipped historiettes-t3 provenance project referenced above.

## Also out of scope for this increment

- **No bilingual-output layout logic.** A bilingual EPUB or other bilingual
  layout is a plausible later addition, but only once the `epub` target
  itself exists.
- **`output.index` retired, not deferred.** The knob is gone from
  `profile.schema.json`; a profile still carrying an `output.index:` block is
  now refused by `profile_validate.py` with an `additionalProperties` error
  naming `index` — the remedy is to delete the block. Nothing ever read it,
  so nothing in this increment loses a capability by its removal.
  **New in 1.8.0, ON BY DEFAULT since 1.10.0:** the `obsidian` target
  additionally supports a *per-entity* source-anchored `## Mentions`
  occurrence index
  (`output.adapter_config.obsidian.mentions_section.enabled` — an absent
  `mentions_section` block, or an absent `enabled` key within a present
  block, resolves to enabled; `enabled` must be a boolean when present —
  a literal `enabled: null` is schema-invalid and rejected by
  `profile_validate.py`, so it is never a reachable way to spell the
  default-on behavior; `enabled: false` opts out), which is the
  authoritative fix for the completeness gap in native backlinks (#206) —
  and, since collision de-linking now applies to every obsidian render
  regardless of this flag (#207), makes a de-linked homonym's occurrences
  discoverable rather than silently missing. When effective-enabled (and
  `output.target: obsidian`),
  `assemble.py` computes the occurrence data (it holds the manifest) and
  attaches it as an **optional `mentions` field on the NodeStream** —
  `{source_form: [{seg, origin, …}]}` — which the obsidian adapter renders;
  the 4-argument `render(nodestream, canon, profile, out_dir)` contract is
  unchanged (the data rides inside `nodestream`). An explicit
  `enabled: false` is byte-identical to pre-1.10.0 output **except** for
  homonym collisions, which are de-linked (not misattributed) on that path
  too as of this release — see "Collision de-linking" in
  `references/output-target-adapters/obsidian.md`.
- **A standalone index page is the project's own job, not this plugin's.**
  Recorded here because `output.index` promised one from 1.8.0 until its
  retirement and never built it. The shape that worked on a real book was
  **four pages** — a chapters table-of-contents plus one page per `category`
  present — not the single aggregated person page that knob named. Three
  properties that book measured, for whoever builds one: source the rows from
  the **rendered vault's own frontmatter**, never from canon, so the index
  cannot disagree with what it indexes and cannot emit rows for notes a
  downstream layer has since renamed or merged; list on each row every form
  the note covers (its `aliases` minus the display name), or a reader
  searching a variant spelling finds nothing; and emit no per-row count
  unless it is re-derived from the artifact itself, an index page being where
  a fabricated number is least likely to be checked. Step 0a copies
  `PLAN.template.md` once and never refreshes it (`SKILL.md:459`), so a
  project scaffolded before the retirement may still name
  `output.index.enabled` in its hand-edited `PLAN.md` intake answer; drop
  that phrase by hand — there is no automatic migration.
- **No generic renderer-plugin framework above the three fixed presets**
  (`obsidian`/`epub`/`custom`) — see
  `references/output-target-adapters/README.md`'s "why only three" section
  for why that ceiling is deliberate, not an oversight.

## Screencast-as-proof: a personal convention, not a plugin rule

One specific operator of this plugin treats
screencasting the final delivered book being opened in a reader as their
own personal proof-of-completion habit. That is a personal workflow
convention, not a rule this plugin imposes on other adopters. It is not
part of `SKILL.md`'s hard rules, and future users of this plugin are not
expected to follow it.

## See also

- `SKILL.md`, Step 0d and W9 Assemble — the orchestrating-session procedure
  that resolves `output.target` and runs the assembler.
- `references/output-target-adapters/README.md` — the adapter table, the
  shared output contract, custom-renderer path-safety, and why v1 ships
  exactly three targets with no generic framework above them.
- `references/output-target-adapters/obsidian.md` — the shipped `obsidian`
  adapter: vault layout, entity-note frontmatter, the wikilink rule,
  collision de-linking, and the source-anchored `## Mentions` occurrence
  index.
- `references/ledger-and-resumability.md` — the `reviewed_draft_sha1` gate
  W9 reuses from `final_audit.py`'s hard check 2.
- `references/verse-policy.md` — the placeholder-bijection invariant W9's
  sentinel resolution enforces at assembly time too.
