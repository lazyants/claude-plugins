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
  index:
    enabled: false
    person_grouping: false
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
`warnings` counts the four WARN-only advisory checks: glossary-diff,
link-graph, foreign-remainder scan, and verse-structure. WARN findings are for
human review; they are never auto-fixed by guessing.

`completeness_counts` uses exactly `not_started`, `recoverable`, `stale`,
`blocked_needs_regeneration`, and `human_escalation`. `human_escalation` is the
category for materialized `blocked` or `non_converged` statuses.
`project_complete == (every one of completeness_counts' five values == 0)`,
which means every `manifest.json` segment, including translate-decision
`FRONTBACK:{id}` units, classifies `reusable`.

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
working target, `obsidian`; `epub` and `custom` resolve (Step 0d validates the
enum/path shape) but do not yet render (see "Why `build_epub.py` hasn't been
generalized" below for `epub`; `custom` is always co-designed per project, see
`references/output-target-adapters/README.md`).

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
2. **Gate on the ledger, per segment:** only a segment whose materialized
   `runs/ledger.json` status is `converged`, AND whose on-disk draft sha1
   still matches that fragment's `reviewed_draft_sha1`, is assembled — the
   same guard `final_audit.py`'s hard check 2 already uses, so a hand-edit
   the reviewer never saw can't silently ship inside an assembled book
   either. The whole run is additionally gated on W7's
   `final-audit-summary.project_complete: true` (see Path 1 above) before
   assembly starts at all.
3. For each block: translated text comes from `segments/{seg}.draft.json`;
   its manifest type and `source_html` presence decide `medium`
   (`html`|`plain`). A HEAD-type block is classified `heading` (its text is
   normally superseded by the segment's own `title_text`); a block that is
   some verse's `parent_block` with `mount: block` is classified `verse`;
   everything else is `prose`. `FN:{N}` definition blocks are never rendered
   inline — they live in `manifest.blocks{}` with their own `order_index` but
   are never members of a body segment's `block_ids[]`; they surface only via
   the footnotes table.
4. Front/back matter follows its `manifest.frontback[]` disposition:
   `translate` assembles normally from its draft; `regenerate` has no draft —
   the assembler emits a documented placeholder node plus a warning
   (full regeneration is a later-phase refinement, not Phase 0/1);
   `omit` is dropped.

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
  "meta":      { "target": str, "verse_mode": str, "apparatus_policy": str }
}
BlockNode = {
  "id": str, "seg": str,
  "kind": "heading" | "prose" | "verse",   # semantic, derived per the algorithm above
  "raw_type": str,                        # manifest type, opaque passthrough
  "order_index": int,
  "medium": "html" | "plain",
  "text": str,                            # translated text, sentinels still inline
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

#### The adapter entry point

Every built-in output-target adapter module exposes the same signature:

```python
def render(nodestream: dict, canon: dict, profile: dict, out_dir: Path) -> dict:
    """Writes the artifact(s) under out_dir. Returns a small manifest
    { "written": [relative_path, ...], "kind": "vault"|"file" } for the diff tool."""
```

`assemble.py` resolves `output.target` to either a flat sibling module name
(`render_obsidian`, `render_epub`) it imports directly from
`assets/scripts/`, or — for `target: custom` — a `Path` loaded via
`importlib` from the fixed `${durable_root}/scripts/custom_renderers/`
subtree (see `references/output-target-adapters/README.md` for the full
resolution/path-safety rules). `out_dir` defaults under
`${durable_root}/out/`, respecting `output.destination` when it is set —
Step 0a already `mkdir -p`s its resolved parent.

#### Render + diff — the acceptance gate

`scripts/diff_rendered_output.py` re-renders and diffs against the last
accepted baseline. It is a stdlib-only markdown-aware reduction (no `bs4`):
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
mismatch or a guard refusal (`"mismatch"` / `"candidate_not_built"`); `2` =
no baseline exists yet (`"no_baseline"`). `--accept-baseline` freezes the
current reduced render as the new baseline, and is itself
overwrite-guarded — it refuses (exit 1) if a baseline already exists unless
`--force-accept-baseline` is also passed. The baseline is stamped with a
render-version/hash so a stale-renderer baseline is detectable. There is no
separate item-count acceptance check anywhere in this pipeline — the
render+diff comparison **is** the gate.

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
stays resolvable (Step 0d validates the enum) but unimplemented — a later
phase, not this one.

The same discipline applies to any future `epub` output-target effort:
`build_epub.py`'s real, current behavior — not this reference's description
of it, nor the plan that preceded this plugin — is the ground truth, but
verifying it firsthand is only possible for whoever has access to the
non-shipped historiettes-t3 provenance project referenced above.

## Also out of scope for this increment

- **No bilingual-output layout logic.** A bilingual EPUB or other bilingual
  layout is a plausible later addition, but only once the `epub` target
  itself exists.
- **No standalone occurrence index.** `output.index.enabled` stays OPT-IN and
  gated; the `obsidian` target gets an occurrence index for free via native
  backlinks (see `references/output-target-adapters/obsidian.md`) — a
  separate, generated index page is a later phase.
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
  backlinks-as-index.
- `references/ledger-and-resumability.md` — the `reviewed_draft_sha1` gate
  W9 reuses from `final_audit.py`'s hard check 2.
- `references/verse-policy.md` — the placeholder-bijection invariant W9's
  sentinel resolution enforces at assembly time too.
