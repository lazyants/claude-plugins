# `obsidian` — the shipped, primary output target

**Status: shipped this increment.** This is the only working
`output.target` value so far — `epub` resolves (Step 0d validates the enum)
but has no renderer behind it yet, and `custom` is always co-designed per
project. See [`README.md`](./README.md) for the full three-target table and
why v1 ships no generic framework above them.

Selected via `output.target: obsidian` in `profile.yml`, only ever consulted
when `output.v1_scope: assembled_book` (`SKILL.md`'s Step 0d). Renders the
assembled NodeStream (`references/assembly-and-output.md`) into an Obsidian
vault: one set of narrative pages carrying the translated book itself, plus
one entity note per frozen `canon.json` entry, cross-linked by wikilinks.
Its own knobs live under `output.adapter_config.obsidian` — currently just
`folders` (the category→folder catalog, see below); `assets/profile.example.yml`
ships the shape.

## Vault layout

Everything is written under `out_dir` (`${durable_root}/out/` by default,
or wherever `output.destination` resolves) as a `vault/` root:

- **Narrative pages** — one page per `manifest.segments[]` entry, in the
  NodeStream's `book.seg_order` reading order, each rendering that
  segment's `BlockNode`s (heading/prose/verse) with sentinels resolved:
  `⟦FNREF_N⟧` becomes an Obsidian-style footnote reference, a verse
  placeholder becomes the rendered verse text (or nothing, under
  `verse_policy.mode: skip`, per the shared assembler contract), and
  footnote definitions are appended per page. Canon terms occurring in the
  page's text are wikilinked (see below).
- **Entity notes** — one markdown file per `canon.json` `entries{}` entry
  (keyed by `source_form`, the unique original-script identity), routed into
  `vault/<folder>/` per the category→folder catalog below.

`render()` returns `{"written": [...], "kind": "vault"}` — the `"vault"`
`kind` is what tells `scripts/diff_rendered_output.py` to reduce the render
by concatenating every written file in sorted-relative-path order (each
preceded by a `--- <relpath> ---` header) before line-diffing, rather than
treating it as a single file.

## Entity-note frontmatter

Every entity note carries YAML frontmatter mirroring its `canon.json`
entry, plus two adapter-computed fields:

```yaml
---
aliases: [<original-script identity -- same value as source_form, below>]
source_form: <original-script identity, canon.json's entries{} key>
canonical_target_form: <the target-language rendering that appears in body text>
category: <open vocabulary, e.g. person/place/work/group/divine-name -- blank/absent renders as "other">
is_proper_name: <bool>
basis: established | transliterated | title | not_a_name | sense_translated
confidence: high | medium | low
source: <URI, required when basis: established>
note: <free-text human note -- singular field name, matching canon-entry.schema.json>
direction: ltr | rtl
---
```

`aliases` is always `[source_form]` — it is what lets a reader or
Obsidian's own search still find this note by its original-script identity
even though the wikilink *target* pointing at the note is the sanitized
`note_identity`, never the raw `source_form` itself (see "The wikilink
rule" below). `note` is deliberately singular — it mirrors
`canon-entry.schema.json`'s own
`note` field name exactly, not a pluralized `notes` list. `direction`
records the vault-wide writing direction implied by the project's target
language (`target.language.code`) so Obsidian renders right-to-left scripts
correctly; it is not part of `canon.json` itself, it is computed by this
adapter at render time. Entries with `basis: not_a_name` /
`is_proper_name: false` — realia, not names — still get a full entity note,
documented the same as any other entry, and are matched into body text the
same way (below); the frontmatter contract does not branch on
`is_proper_name`.

## The wikilink rule

**The asymmetry to hold onto:** the substring that actually appears in
*translated* body text is `canonical_target_form`, never `source_form` — the
wikilink's *display* text is what a reader sees, and its *target/identity*
is `note_identity`, the entity note's own sanitized, folder-qualified
relpath. `note_identity` is derived from the winning `source_form` but is a
distinct string from it, and only `note_identity` is ever safe to put
inside `[[...]]`.

- Build the matcher over the set of every entry's `canonical_target_form`
  value, **sorted longest-first**, so a longer name is never shadowed by a
  shorter one that happens to be its substring — **except** entries with
  `basis: sense_translated` (#138), which are deliberately **excluded from
  the matcher entirely**. A sense-rendering is an ordinary word by
  construction ("Hope", "Wolf"), and this unanchored, no-word-boundary
  matcher would otherwise wikilink every incidental occurrence of that word
  in the prose, not just the entity's own mentions. Such an entry still gets
  a full entity note (frontmatter, `basis` included) — only the body
  auto-linking is suppressed, erring toward the recoverable failure (a
  missing auto-link) over a false-link flood. (The pre-existing `not_a_name`
  realia case above is unaffected by this rule and stays body-matched as
  before.)
- Match within a single narrative block's text (a plain string match against
  the resolved text, never entity/NLP matching); wrap only the **first
  occurrence per block** — a name repeated three times in one paragraph
  gets exactly one wikilink, not three.
- The wikilink itself: `[[<note_identity>|<canonical_target_form>]]` — link
  target/identity is `note_identity`: the same sanitized, collision-deduped,
  **folder-qualified** relpath (e.g. `People/Ivan`, minus the `.md`
  extension) that the entity-note-writing loop resolves for that entry's
  actual filename, both resolved from the one lookup up front so a link can
  never point at a note the writer doesn't actually emit under that exact
  name. This is deliberately **not** `source_form`: a raw `source_form`
  containing path-like text (`../`, a leading separator, control bytes)
  would otherwise leak straight into a wikilink target, and even a
  "safe-looking" bare stem is not guaranteed unique once two entries in
  *different* folders sanitize to the same name — folder-qualification is
  what keeps those apart. `source_form` still travels with the note, just
  never as the link target: it lives in the note's own `source_form`
  frontmatter field and its `aliases` entry (see "Entity-note frontmatter"
  above), so a reader or Obsidian's own search can still find the note by
  its original-script identity. Display text is `canonical_target_form` (so
  the reader sees the actual translated name in context, not the original
  script or the sanitized filename).
- `canonical_target_form` is **not** guaranteed unique across entries (two
  different `source_form`s can transliterate to the same target-language
  string). Sharing is checked **NFC-normalized but still case-sensitive**:
  `"Peter"` and `"peter"` (or an NFD variant of the same string) are
  DISTINCT targets to the renderer, so each stays single-owner and keeps
  its inline link — only an NFC-exact match with ≥2 owners collides. **When
  it does, collision de-linking applies to every obsidian render, on or
  off the Mentions appendix (#207):** none of the colliding entries gets an
  inline link — no shared display text is ever linked to a single entry's
  `note_identity`, so a reader can never be misdirected to the wrong
  entity's note. (This is gated on `output.target == "obsidian"`, like the
  rest of this adapter — the non-obsidian `custom` CLI path activates none
  of this.) `build_entity_index()` still documents a
  shortest-`source_form`-then-lexicographic tiebreak, but only as that
  function's `collision_delink=False` default behavior for direct callers
  and tests; `render()` always calls it with `collision_delink=True`, so
  the production renderer never reaches the tiebreak branch. See
  "Collision de-linking" below.

## Native backlinks are a best-effort affordance, not the occurrence index

Obsidian's native backlinks panel on every entity note lists every
narrative page whose rendered prose links to it via the inline matcher (see
the wikilink rule above). That's a convenient, zero-cost reading affordance
— but it is **not** the authoritative occurrence index (#206): per the
plugin's iron rule, the inline matcher never makes an identity call, so it
only ever fires on a verbatim, case-sensitive `canonical_target_form` match
against translated prose. A variant rendering, an abbreviated mention, or a
de-linked homonym collision (see "Collision de-linking" below) simply gets
no backlink. `build_name_manifest.py` (the reference project's own
hand-rolled occurrence-gathering script) is deliberately not ported as a
*separate* index file — instead, the source-anchored `## Mentions` section
below is the authoritative, variant-immune, homonym-split occurrence index,
and `validate_backlinks.py` is what verifies its coverage.
`output.index.enabled` (see `references/assembly-and-output.md`) governs a
*different*, still-later-phase concept — a generated standalone index page
— and stays irrelevant to this adapter's own occurrence tracking. A depth-1
MOC (map-of-content) stub listing every category folder is a reasonable,
proportional addition; a deeper generated index is explicitly out of scope
here.

### 1.8.0+ — source-anchored `## Mentions` section, ON BY DEFAULT since 1.10.0

Native backlinks are only as complete as the **inline linker**, which matches
one `canonical_target_form` string against translated prose — so a variant
target rendering simply gets no backlink (#206), and when two source forms
share an NFC-exact target (grouping is case-sensitive; a case or whitespace
variant is its own distinct, single-owner target and keeps its link),
collision de-linking (applies to every obsidian render, independent of
this section's own enabled flag — see "Collision de-linking" below) means
NEITHER owner gets an inline link (#207), not just the losing one.
`output.adapter_config.obsidian.mentions_section.enabled`
adds an authoritative **source-anchored** occurrence index: a `## Mentions`
section in each entity note, wrapped in reserved `<!-- lt:mentions:begin/end -->`
markers, listing the segment notes where the entity's *source* forms occur (per
`occ_index`), independent of how the target surface varies. This is the
`build_name_manifest.py` model ported at last, and it supersedes "native
backlinks are the occurrence index." `sense_translated` proper names — which
the inline linker deliberately never auto-links — DO get Mentions here (source
anchoring links them safely), and (1.10.0, #240) a `sense_translated` entry
sharing a `canonical_target_form` with a narrative entry now correctly
contributes to that target's collision count even though it can never win the
inline-link tiebreak itself — see "Collision de-linking" below.

**ON BY DEFAULT (1.10.0+):** an absent `mentions_section` block, or an
absent `enabled` key within a present block, resolves to enabled for
`output.target: obsidian`; set `enabled: false` explicitly to opt out.
Output is byte-identical to pre-1.10.0 **except** for homonym collisions:
as of this release collision de-linking (see "Collision de-linking" below)
applies regardless of this flag, so an NFC-exact `canonical_target_form`
shared by ≥2 canon entries (case-sensitive — a case/whitespace variant is
its own distinct, single-owner target) gets no inline link on the disabled
path either, instead of pre-1.10.0's misattribution to the
shortest-`source_form` owner. One limitation of the
disabled path: `validate_backlinks.py` short-circuits to a disabled report
and computes nothing there, so a homonym orphaned by this de-linking (no
inline link, and — since the section is off — no `## Mentions` backlink
either) is not surfaced by the gate. `enabled` must be a **boolean**
when present — a literal `enabled: null` (or `mentions_section: null`) is
schema-invalid (`profile.schema.json` declares both as non-nullable) and
is **rejected by `profile_validate.py`** before it ever reaches the
runtime predicate. Omit the key (or the whole block) to get the
default-on behavior through the normal, schema-valid path — `null` is not
a supported way to spell it. (The three runtime predicates' own `is not
False` check tolerates `None` defensively, purely as a fallback for a
profile dict constructed outside the normal Step 0 validation path; it is
not evidence that a schema-valid profile can carry `enabled: null`.)
Through 1.9.x this was opt-in (default false) — see the CHANGELOG for the
migration note (a rendered vault holding an accepted
`diff_rendered_output.py` baseline needs one operator `--accept-baseline`
re-accept once this lands, since `render_obsidian.py`'s own bytes changed;
converged segments are never re-translated by this flip).
The advisory `validate_backlinks.py` W9 gate (non-blocking) reports coverage;
the aggregated `output.index` person-index page + `index_scope` routing
remain a later phase.

**Collision de-linking is decoupled from the Mentions flag, but still
gated on `output.target == "obsidian"`.** When two or more canon entries
share one NFC-exact `canonical_target_form` (grouping is case-sensitive —
`"Peter"` and `"peter"` are distinct targets, each single-owner, each
keeps its inline link), NONE of them gets an inline link on any obsidian
render — appendix on or off — so the inline linker never misattributes a
shared display text to one owner's `note_identity` (#207).
Previously this was gated on the same effective-Mentions predicate as the
`## Mentions` section itself, so an `enabled: false` opt-out reintroduced
the misattribution; now only the `## Mentions` section (and the
reserved-field/gate checks around it) is governed by
`_effective_mentions_enabled` — de-linking is not. The non-obsidian
`custom` CLI path is unchanged: it activates neither the `## Mentions`
section, collision de-linking, nor the `validate_backlinks.py` gate. With
the section active,
its per-entity `## Mentions` listing is what makes the de-linked entries'
occurrences discoverable at all; with the section disabled, a de-linked
homonym has no inline link AND no `## Mentions` backlink — see the
disabled-path limitation noted above.

## Category→folder catalog — presets are EXAMPLES, not an enum

`category` is genuinely **open vocabulary** — `canon-entry.schema.json`
documents it as free-form per-project text, not a fixed schema enum, because
the right catalog differs per work (a mythology-heavy text needs
`divine-name`; a political history needs `institution`; many projects need
neither). This adapter routes each entity note into `vault/<folder>/` using
the profile's own `output.adapter_config.obsidian.folders` map as a
**lookup table only** (`category → folder`); a category absent from that
map, blank/absent on the entry itself, or simply unmapped, routes to
`vault/other/` **unconditionally** — never as the category string itself
(see "Security" below).

The categories below are **illustrative starting presets**, not a hardcoded
enum this adapter switches on — copy, rename, or drop any of them per
project:

| Example `category` | Example folder |
|---|---|
| `person` | `People` |
| `place` | `Places` |
| `work` | `Works` |
| `group` | `Groups` |
| `divine-name` | `Divine Names` |

```yaml
output:
  adapter_config:
    obsidian:
      folders:
        person: People
        place: Places
        work: Works
        group: Groups
        divine-name: "Divine Names"
        # any other project-specific category the co-designed canon uses;
        # absent-or-blank category always routes to "other"
```

## Security: only mapped folder VALUES ever reach a filesystem path

`category` itself is used **exclusively as a dict-lookup key** into
`output.adapter_config.obsidian.folders` — it is never joined into a path,
full stop, no matter how ASCII-safe or path-segment-looking a given
category string happens to be. Absent, blank, or unmapped all resolve to
`vault/other/` **unconditionally** — there is no fallback where a
"safe-looking" unmapped category gets used raw as its own folder name.

The only strings that ever reach a path join are the **folder VALUES this
project's own profile declares** in `folders` (plus the fixed literal
`other`). Before any declared folder value is used as a path segment, this
adapter enforces a **positive allow-list**, `^[A-Za-z0-9 _-]+$`, and rejects
`.`/`..`/empty/a leading path separator. A denylist is not sufficient here
(a denylist rejecting `/` or `..` still lets through other shell/path
metacharacters it didn't anticipate — see the repo's own identifier→path
allow-list precedent). This means the untrusted-input boundary this
allow-list actually defends is the profile's own `folders` map — not
`category`, which never reaches the join at all.

Note *filenames*, derived from each entry's `source_form`, get the same
fail-closed, allow-list-first posture applied to whatever filesystem-unsafe
characters a raw name could contain (path separators, `..`, control/NUL
bytes, a leading separator) — rejected/stripped before the file is written,
never patched up after the fact with a denylist of specific bad substrings.
Unlike `category`/`folders` (an English-ish open vocabulary the profile
declares), `source_form` is often non-ASCII source-script text (Cyrillic,
etc.) by design — see `SKILL.md`'s English-only-identifiers rule, which
governs code identifiers, not this kind of data-derived filename — so the
filename sanitizer's allowed character set is necessarily wider than
`category`'s, while holding the same "positive allow-list, reject
traversal/separators before any join" discipline.

## See also

- [`README.md`](./README.md) — the three-target table, the shared
  `render(...)` entry point, `output_resolve.py`'s dispatch and `custom`
  path-safety, and why there is no generic renderer framework.
- [`../assembly-and-output.md`](../assembly-and-output.md) — Step 0d, W9
  Assemble, the NodeStream/anchor-map artifacts this adapter consumes, and
  the render+diff acceptance gate that checks this adapter's own output.
- [`../canon-and-glossary.md`](../canon-and-glossary.md) — how `canon.json`
  gets frozen in the first place; this adapter only ever reads it, never
  writes or adjudicates it.
- `assets/schemas/canon-entry.schema.json` — the authoritative shape for
  every field this adapter's frontmatter mirrors, including `category`'s own
  documented open-vocabulary/`other`-default behavior.
