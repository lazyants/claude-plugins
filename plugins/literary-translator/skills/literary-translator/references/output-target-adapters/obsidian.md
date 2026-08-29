# `obsidian` — the shipped, primary output target

**Status: shipped this increment.** This is the only working
`output.target` value so far — `epub` has no renderer behind it and therefore
does not resolve at all (Step 0 and Step 0d both HALT on it), and `custom` is
always co-designed per project. See [`README.md`](./README.md) for the full
three-target table and why v1 ships no generic framework above them.

Selected via `output.target: obsidian` in `profile.yml`, only ever consulted
when `output.v1_scope: assembled_book` (`SKILL.md`'s Step 0d). Renders the
assembled NodeStream (`references/assembly-and-output.md`) into an Obsidian
vault: one set of narrative pages carrying the translated book itself, plus
one entity note per frozen `canon.json` entry, cross-linked by wikilinks —
and, when the project declares `output.entity_markup` with
`index_from: markup`, one further note per entity its translator marked
inline (see "Markup-driven entity notes" below; absent that declaration
nothing on this page changes except the two unconditional items named under
"Editorial brackets"). Its own knobs live under
`output.adapter_config.obsidian` — currently just `folders` (the
category→folder catalog, see below); `assets/profile.example.yml` ships the
shape. `output.entity_markup` is NOT one of them: it is read by
`assemble.py` as well, so it sits directly under `output`.

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
  construction ("Hope", "Wolf"), and this matcher would otherwise wikilink
  every incidental occurrence of that word in the prose, not just the
  entity's own mentions — the boundary rule below does not help, and cannot:
  it refuses a match that is only part of a longer word, while a
  sense-rendering matches as a *whole* word. Such an entry still gets
  a full entity note (frontmatter, `basis` included) — only the body
  auto-linking is suppressed, erring toward the recoverable failure (a
  missing auto-link) over a false-link flood. (The pre-existing `not_a_name`
  realia case above is unaffected by this rule and stays body-matched as
  before.)
- Match within a single narrative block's text (a plain string match against
  the resolved text, never entity/NLP matching); wrap only the **first
  occurrence per block** — a name repeated three times in one paragraph
  gets exactly one wikilink, not three.
- **Refuse a match that is only part of a longer written run (#587).** If the
  character immediately before or immediately after the matched span is
  alphanumeric (`str.isalnum()`), the match is discarded: the target is a
  fragment of a longer word, not a mention. Longest-first ordering does not
  cover this — it stops a shorter *target* shadowing a longer one, and here
  the longer string is ordinary prose. Without the rule the Yiddish demonym
  `Tepliker` ("the man from Teplik") rendered as `[[…|Teplik]]er`, the word
  cut in half in the delivered book; any language that forms a demonym or
  adjective by suffixing a name reaches this (`Breslov`/`Breslover`,
  `Paris`/`Parisian`, `Tudor`/`Tudors`), as does any target that is a common
  short word.
  - The test is **alphanumeric, never non-space**: `[[…|Reb Noson]]’s` is
    correct and common, and so are a following comma, period or closing
    quote. Only a letter or digit means the target is a fragment.
  - It is applied **per match, against the adjacent characters** — not as a
    `\b` in the pattern. `\b` is asserted relative to each alternative's own
    edge character, so a `canonical_target_form` beginning or ending in
    punctuation flips what it means: `R.` plus `\b` *matches* `R.Smith`,
    which this rule refuses — and, in the other direction, does *not* match
    `R. Noson`, which this rule links. It also needs no per-script branch,
    since LETTERS are `isalnum()` in Hebrew and Cyrillic alike; combining
    marks are not, which is the gap below.
  - A refused span is still **consumed** (the scan is non-overlapping), so a
    different, shorter target starting inside it gets no turn: targets
    `Ann Marie` and `Marie` over the prose `JoAnn Marie` link nothing at
    all, rather than linking `Marie` to a different entity inside a full
    name. That is the deliberate direction — a missing link is recoverable
    through the source-anchored `## Mentions` appendix, a wrong one is not.
  - A refused match is **not** counted as seen, so it never spends the
    block's single first-occurrence slot, nor the book-wide first occurrence
    that `parenthetical_originals: first_occurrence` tracks.
  - Still uncovered, deliberately: characters that attach to a word without
    being alphanumeric — combining marks, ZWJ/ZWNJ, soft hyphen, the bidi
    marks (#590). `target + ZWNJ + suffix` is still cut.
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
`output.index` is retired, not a later phase (see
`references/assembly-and-output.md` for what a project builds instead in its
place); this adapter's own occurrence tracking never depended on it. A
depth-1 MOC (map-of-content) stub listing every category folder is a
reasonable, proportional addition; a deeper generated index is explicitly
out of scope here.

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
the aggregated `output.index` person-index page it once might have routed to
is retired, not a later phase. `index_scope` is a different case and stays:
it is carried end-to-end rather than merely declared — validated on
`canon-senses.schema.json`, re-declared and enum-checked on
`segpack.schema.json` by `segpack.py`, and projected into the registry row
`person_registry.py` writes. It was never added to `canon-entry.schema.json`,
and is not being added.

**Checking a POST-PROCESSED vault (`--entity-note-map FILE`).** Both metrics
locate each entity's note by re-deriving its path through the renderer's own
naming rule, so a vault whose entity notes a downstream layer renamed — or
merged, one note per entity rather than one per spelling — reports every
expected occurrence missing while being perfectly correct. `--vault DIR` does
not help: it moves the root, never the derivation. `--entity-note-map` supplies
the derivation instead, as a JSON object `{source_form: "<vault-relative>.md"}`
that replaces it wholesale for *both* metrics. Several source_forms may share
one path (an inline link to the shared note then credits every owner of it — an
exit-neutral aggregation, disclosed because attribution between merged
spellings is genuinely ambiguous); a canon entry the map omits is treated as
having no note in this vault, so its occurrences count as missing rather than
raising. A map never blanket-passes: a mapped note whose `## Mentions` region
really is missing an expected segment link still yields that pair and exit `1`.
An unreadable/non-object file, a non-string or non-relative-`*.md` value
(a stemless `.md` basename and an embedded NUL are both refused here — each is
lexically `*.md` yet cannot name a real note), or a
key that is not a `canon.json` entry — or the same key twice, since a silently
de-duplicated map would aim both metrics at whichever line came second — is
exit `2` — but only once the gate is
enabled: the `disabled` short-circuit still returns exit `0` first and reads no
map, because a gate that will not run should not fail on an input it will not
use. On the enabled path a supplied map makes the report carry
`"note_map_source": "supplied"`; the default report is unchanged, its absence
meaning the paths were derived.

**Collision de-linking is decoupled from the Mentions flag, but still
gated on `output.target == "obsidian"`.** When two or more canon entries
share one NFC-exact `canonical_target_form` (grouping is case-sensitive —
`"Peter"` and `"peter"` are distinct targets, each single-owner, each
keeps its inline link), NONE of them gets an inline link on any obsidian
render — appendix on or off — so the inline linker never misattributes a
shared display text to one owner's `note_identity` (#207). Since 1.32.0
there is exactly one exception, and it is not an inference the renderer
makes: when **every** owner of that target is a member of one
`canon_link_groups.json` group and none is `sense_translated`, the operator
has already stated the owners are one referent, so the target links to that
group's primary — see *Re-linking one referent* below.
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

### What de-linking cost this render — `delink_cost` (1.32.0)

De-linking is silent by construction: the vault renders, every gate passes,
and nothing says how much of the book's naming went unlinked. In one
delivered vault that was **1373 unlinked occurrences against 537 emitted
links** — the book's most-named figures, silenced, with a clean bill of
health (#588). So `render()` now returns, and stamps into the vault
marker, a `delink_cost` block:

```json
{"delinked_targets": [{"canonical_target_form": "Moyshe-Leyb",
                        "owners": ["משה לייב", "משה־לייב"],
                        "unlinked_occurrences": 1373}],
 "unlinked_occurrences_total": 1373,
 "inline_links_emitted": 537}
```

- It rides out on `assemble.py`'s stdout as `adapter_result.delink_cost`,
  and `validate_backlinks.py` **republishes it verbatim** from the marker
  (exit-neutral — `warnings` stays `len(missing)`). The gate never
  re-derives it: it short-circuits entirely when the appendix is disabled,
  which is exactly the configuration the measured vault ran under.
- A **non-zero total always prints one stderr `WARN`**, on every obsidian
  render. No ratio threshold — a book whose most-named figures are silenced
  should not need to clear a bar to be told so.
- **A de-linked target CONSUMES its span, and nothing links inside it.**
  Linking and counting are one scan over the union of linkable and
  de-linked targets, for this reason: a scan that knew only the surviving
  targets would match a shorter one *inside* a de-linked longer one — canon
  holding a colliding `John Smith` and a single-owner `John` rendered
  `[[…|John]] Smith`, a link landing on the wrong man inside the very span
  de-linking had just suppressed, while the cost report called that same
  occurrence unlinked. The #587 word boundary cannot catch it (the
  character after `John` is a space). A consumed span never spends the
  block's one-link-per-target budget, so the short target can still take its
  one link at a later eligible occurrence in the same block.
- **The span is consumed even when the #587 boundary guard REFUSES the
  de-linked match**, and that costs the short target its link at exactly
  that spot. With `John Smith` de-linked and `John` surviving, the prose
  `John Smithson arrived.` matches `John Smith` at 0–10, `_boundary_ok`
  refuses it (the next character is `s`), and `re.finditer` has already
  consumed the span — so `John` gets no turn there, even though it stands
  alone by both word boundaries. It is also not counted, which is correct
  under this metric's own definition: a link group could not recover that
  occurrence either, because the boundary guard would still refuse it.
  Releases before 1.32.0 emitted `[[…|John]] Smithson` here. The direction
  is deliberate — a missing link is recoverable, a link on the wrong man is
  not (#207). The obvious remedy — re-scanning from `m.start() + 1` after a
  refusal — is deliberately not taken, and **the reasons live next to the
  code, not here**: the comment beside the refusal in `_Linker.link`, and the
  docstring of the test that pins it, which goes RED under exactly that
  mutant. That test is
  `tests/render_obsidian.test.py::test_a_refused_span_is_consumed_so_no_shorter_target_links_inside_it`
  — given as a pytest node id because
  `tests/render_obsidian_link_groups.test.py` holds a near-homonym,
  `test_a_delinked_span_is_consumed_and_no_shorter_target_links_inside_it`,
  which pins the DE-LINK consumption rather than the boundary refusal. Both
  sit where a change to the behaviour must pass. This page deliberately does
  not restate them: a third copy of a scan-order argument is a third thing to
  get wrong, and 1.32.x got it wrong twice that way.
- The counts come from inside `_Linker`, over the exact text the wikilink
  rule is applied to — **never a re-scan of the finished markdown**, which
  would be both over- and under-inclusive (a verse gloss is linked BEFORE
  it is wrapped as `> *Literal: …*`, the segment title is duplicated into
  YAML frontmatter, and the inline-verse label is protected by position).
  Every occurrence counts, not one per block: the question is how many
  unlinked mentions a reader actually meets. A de-linked short name nested
  inside a longer linked one is charged to the longer name.
- `unlinked_occurrences` and `inline_links_emitted` are **different
  cardinalities on purpose** (occurrences vs. links; the wikilink rule
  emits at most one link per target per block). Nothing is claimed about
  their ratio.
- A target with zero occurrences is still listed — the de-linked SET names
  every canon form implicated, and its cost being zero is the useful part.
- The marker is re-stamped WITHOUT a measurement the moment the old vault
  is cleaned, so an interrupted render can never leave a previous render's
  number standing over notes it no longer describes.
- `delink_cost: null` in the GATE report means "not republished here" —
  never "measured zero". Two different causes: on the enabled path, no
  usable measurement in the marker (absent, unreadable, another adapter's,
  or from a render that did not finish); on the disabled path, the gate
  short-circuits before reading the vault at all, so `null` says nothing
  about whether the render measured anything. The renderer's stderr WARN and
  `adapter_result.delink_cost` are the authority in that second case.

### Re-linking one referent — `canon_link_groups.json` (1.32.0)

De-linking cannot tell two spellings of one man from two different men, and
in a pointed-script corpus the first case is the normal one. A
**link group** is how an identity call made upstream is recorded so the
renderer can act on it. Optional sidecar at
`{durable_root}/canon_link_groups.json`, schema
`schemas/canon-link-groups.schema.json`:

```json
{"schema_version": 1,
 "groups": [{"primary": "משה לייב",
             "members": ["משה לייב", "משה־לייב"],
             "note": "same man, with and without maqaf — adjudicated W7"}]}
```

When **every** owner of a colliding target reduces to the same group
primary, and no owner is `sense_translated`, the shared target links to
that primary's note instead of being de-linked. Four deliberate limits:

1. **Only targets that would otherwise be de-linked move.** A single-owner
   target is untouched, group or no group.
2. **The matcher never widens.** The alternation is built from the same
   `canonical_target_form` strings either way — no string becomes newly
   matchable, no prose is newly rewritten.
3. **A group plus an outsider still de-links**, and so does a group
   containing a `sense_translated` owner: the anti-flood invariant (#138)
   and the misattribution rule (#207) both outrank a routing preference.
4. **It is not an entity layer.** `canon.json` stays a 1:1 name dictionary,
   and every member keeps its own entity note and frontmatter.

### What a group does to the `## Mentions` appendix (1.58.0+, #497)

The three limits above are about inline links. A group has a second effect,
and until #497 this section claimed it had none: **when a group's members
collide on a `#238/#241` fold key, the group's occurrences are credited to
its primary**, so the primary's `## Mentions` appendix is the collapse-free
index for that referent and the other members' notes carry no appendix.

Read that against what it replaced, not against an ideal: before #497 a fold
collision withheld the occurrences of EVERY member, so none of those notes had
an appendix at all. On the live he→en volume that was 27 canon forms and 2 390
occurrence records, with `validate_backlinks.py` reporting `warnings: 0` —
the loss was invisible to the gate, because coverage is measured over the
eligible universe those forms had just been removed from.

The crediting is deliberately all-or-nothing on the whole fold key. It applies
only when **every** form sharing that fold key — across canon entries AND
`canon_senses.json` split-only forms — is an index-eligible canon entry inside
one group with one primary, and no member carries a homonym split. A
split-only form on the key, an `is_proper_name: false` entry on the key, a
partial group, two primaries, or a primary outside the group all leave the
whole group withheld with `reason: "fold_match_key_collision"`, exactly as
before.

`validate_backlinks.py`'s `unresolved_homonyms` rows carry `reason`, which is
how a credited non-primary member (`fold_group_credited_to_link_group_primary`
— a resolved routing decision) is told apart from a genuine collision or a
homonym split, both of which are still asking you for an answer.

Two limits worth knowing before you rely on this:

- It is `output.target: obsidian` only. The sidecar projection is attached to
  the NodeStream under that target alone, so an `epub`/`custom` project's fold
  groups stay withheld — including in `person_registry.py`'s W9r counts.
- Where a group's members carry DISTINCT single-owner `canonical_target_form`s
  they each keep their own inline link (limit 1: a single-owner target is
  untouched), so a non-primary note remains reachable while holding no
  appendix. For the case the sidecar exists for — one shared, de-linked
  target — the note the links reach IS the one that holds the appendix.
- Adopting a group on a delivered book re-renders it: accept the new baseline
  with `diff_rendered_output.py --accept-baseline --force-accept-baseline`,
  and note that an in-flight W9r registry run must restart, since
  `person_registry.py` binds the whole NodeStream into `registry_input.json`'s
  digest.

**A script never decides membership.** `note` is required and non-blank for
exactly that reason: the file records a call, it does not make one (the
iron rule). `assemble.py` loads it fail-closed — a malformed sidecar, a
member that is not a byte-exact `canon['entries']` key, a `primary` outside
its own `members`, or a form claimed by two groups all halt assembly rather
than render a vault whose links contradict the operator's own decision. A
**dangling symlink is not "absent"** — a broken sidecar is one the operator
meant to have. Membership is byte-exact: never folded, never NFC-normalized.

**Migration.** The sidecar sits outside all 15 cache-key fields, so adopting
a group re-translates **nothing**. `render_obsidian.py`'s own bytes changed,
so `render_version` moved. With no sidecar the rendered markdown is
unchanged, so `diff_rendered_output.py` still MATCHES: it prints the
advisory `stale_baseline` WARN and exits `0`, and re-accepting is optional.
Adopting a group changes the rendered links only when it actually takes
effect — a group whose target has zero occurrences in the prose, or one the
outsider/`sense_translated` rules leave de-linked anyway, produces the same
Markdown and the diff still matches. When it does take effect the diff
MISMATCHES (exit `1`) and a deliberate re-accept is required. Any re-accept,
in either case, is `--accept-baseline --force-accept-baseline` —
`--accept-baseline` alone refuses to overwrite a baseline that already
exists.

## Markup-driven entity notes — `index_from: markup` (1.73.0, #795)

Everything above describes the canon-driven index, which is still the
default and is unchanged. A project whose names cannot be seeded from a list
in advance declares `output.entity_markup` instead (grammar, modes and the
five assemble-time refusals: `references/assembly-and-output.md`, "Inline
entity markup"). Under `index_from: markup`, `assemble.py` hands this adapter
`nodestream["entity_markup"].spans` and the adapter builds notes from them.
Under any other mode — including an absent block — this adapter ignores that
key entirely, exactly as it ignores `nodestream["mentions"]` when the
Mentions appendix is not effective-enabled.

**Identity is the PAIR `(tag, ref or payload)`.** The tag is not merely the
folder: `<person>Jordan</person>` and `<place>Jordan</place>` are two notes,
`People/Jordan.md` and `Places/Jordan.md`. Collapsing them would be an entity
merge — a judgement this plugin does not make anywhere, and one the issue
that added this feature explicitly excluded.

**Composition with canon, not competition.** When the label (`ref` if
present, else the payload) is a linkable canon `canonical_target_form`, the
span links THAT canon note and mints nothing. Otherwise a markup note is
minted. Composition is refused when the canon entry's own `category`
CONTRADICTS the declared tag — otherwise `<person>Jordan</person>` would link
a canon note for a place named Jordan, which is both the entity-merge
judgement this plugin never makes and a silent shortfall (no person note, the
coverage counts still balanced, exit 0). A canon entry with no `category`
composes with any tag, deliberately: the shipped glossary pass never asks for
that field, so on a typical project it is empty everywhere, and demanding a
positive match would stop composition entirely and mint a duplicate beside
every canon note. Canon notes are resolved first and their relpaths are byte-identical
to a render with no markup at all, so `validate_backlinks.py`'s independent
re-derivation still matches; markup notes are deduped against the same
`used_paths` set afterwards and can never take or overwrite a canon note's
path.

**Every marked span becomes a wikilink — every occurrence, every node kind,
headings included.** This is not a stylistic choice, it is what keeps the two
indexes from interfering. An emitted `[[…]]` is a protected span, so the
canon scan cannot see inside it and the two mechanisms share no state.
Leaving even one class of marked span as bare text would expose it to a scan
that is longest-first (so `<person>John</person> Smith` links the wrong man
when canon holds both `John` and `John Smith`), that suppresses repeats
within a block (so two marked spans yield one link), and that refuses a match
adjacent to an alphanumeric (so `<person>Ann</person>ette` yields none) —
three ways to deliver less, or worse, than what was marked, all of them
silent.

**A markup note carries only what is true**: `aliases` (every distinct
printed payload seen for that identity, sorted), `name`, `category` (the
tag), `ref` when the label came from one, and `direction`. It carries no
`basis`, `confidence` or `source` — those are canon's, and inventing them
here would be fabrication. It carries no `## Mentions` section either: that
appendix is source-anchored and canon-keyed, and `validate_backlinks.py`
derives the notes it parses from canon alone, so markup notes are invisible
to it and it needs no change.

**Headings.** A heading's spans link like any other, and the frontmatter
`title:` and the filename slug are derived from the FLATTENED text — the
wikilink reduced to its display form and `\[`/`\]` unescaped — so no markup,
no link syntax and no escape residue reaches a filename. That flattening runs
only in `index` mode, so a literal `[[…]]` an operator wrote into a source
heading on any other project still reaches `title:` exactly as it does today.

One thing there is NOT mode-gated, and it is one of the two behaviours a
project declaring nothing can still notice: `_heading_plain_text` scrubs
`⟦ENT_n⟧`/`⟦/ENT_n⟧` unconditionally, keeping what sits between a pair —
exactly the posture the `⟦FNREF_N⟧` anchor scrub beside it already has, and
for the same reason (a fixed machine shape, never prose). Every matching
token individually, not only a well-formed pair: a lone opener or closer
ships to a reader just as visibly as a whole one. It cannot be
gated: `validate_backlinks.py` rebuilds each segment note's filename from the
PERSISTED nodestream — written by `assemble.py` before this adapter resolved
anything — and it is handed no mode to gate on, so without the scrub it would
derive a filename with raw sentinels in it for a segment written without
them, and report every Mentions link into that segment missing. The cost is
that a heading carrying that literal machine shape loses it from `title:` and
from the slug on any project — and, because such a heading no longer takes
the byte-identical fast path, has its internal whitespace collapsed there
too.

**What the coverage guarantee is, and is not.** The adapter refuses
(`entity_markup_coverage_mismatch`) unless every recorded span resolved
exactly once. That is a claim about RESOLUTION. It is not a claim that every
link reaches a written note: a segment note carries only the footnote
definitions its own nodes reference, so a span inside an UNREFERENCED
footnote definition resolves and is never delivered. The one such gap that is
a plausible operator error — a span in a dedicated verse node's ignored
`text` — is refused at assemble time instead.

**The vault is checked before it is destroyed.** `_clean_vault_content`
removes the managed vault before the first note is written, so BOTH inputs a
note's text is built from are walked for `⟦ENT_n⟧` / `⟦/ENT_n⟧` tokens BEFORE
that point (`entity_markup_unresolvable`), leaving the existing vault
untouched on a refusal: the whole NodeStream value, where every token must
sit in a slot the pre-pass rewrites and pair up one-to-one with a recorded
span; and the whole of `canon.json`, where any such token is refused
outright, because an entity note's frontmatter and heading come straight from
the entry and never pass through the pre-pass. A per-note residual check
before each write stands behind that; with the preflight covering both inputs
it can only fire on a resolver bug, and it is the last thing between a
machine token and a reader.

**Three accepted imprecisions, all about `parenthetical_originals:
first_occurrence` and none about a link target.** The pre-pass consults and
updates the same book-global set the canon linker uses, so the original-script
gloss still appears exactly once. But an UNMARKED occurrence earlier in the
book than the first marked one loses the gloss to the marked one (closing that
would mean running the canon scan first, which is the two-competing-scans
design this whole section avoids); and within a single node an inline embedded
verse is spliced at its placeholder position at render time while the pre-pass
visits a node's text and its verse content as separate strings, so the gloss
can land on the later of two marked occurrences inside one node; and the
pre-pass visits every node before any footnote definition while rendering
delivers segment 1's footnotes before segment 2's prose, so the gloss can
land in a footnote that the reader meets after an unglossed occurrence in
later prose. All three are the price of resolving spans in ONE whole-
NodeStream pass instead of at each rendering site, which is what makes the
coverage identity checkable at all.

**Editorial brackets.** A bracket the translator places around a name used to
collide with the wikilink put inside it: `[[[People/Reb Noson|Reb Noson]]]`
makes Obsidian read the target as `[People/Reb Noson` and leave a stray `]`.
The outer pair is now escaped at both emission sites, which preserves what the
reader sees and lets the link parse. This applies to canon links too, so it is
one of the two behaviours here that can change rendered output for a project
that declares no markup at all — the unconditional heading scrub above is the
other. A bracket is treated as escaped on the PARITY of the backslash run
before it, so `\\[Name]` (an escaped backslash, then a literal bracket) is
still repaired.

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

**The shipped glossary pass never asks for `category`** —
`glossary_TASK.template.md` neither requests the field nor illustrates it. So a
catalog declared here routes only the entries that actually carry one, which
under the shipped prompt may be none of them. An entry can still acquire the
field by other routes: `canon-entry.schema.json`'s own `category` description
owns that list, and says what a failed category check does and does not
establish. Declaring the catalog populates nothing by itself, so do not write a
canon completeness gate that assumes the field is populated.

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

That set has three legs, and only the first is a plain character list:

- **`str.isalnum()`** — any Unicode alphanumeric, in any script.
- **the combining-mark CATEGORIES `Mn`/`Mc`/`Me`** — niqqud, cantillation
  and every other script's marks. This leg is a Unicode-category test
  rather than an enumeration on purpose: a combining mark is combining by
  definition, so it can be neither a path separator nor an extension, and
  admitting the category as a whole cannot weaken the guarantees below.
  Before this leg existed, a fully pointed Hebrew name became one `_` per
  mark — a stem no reader can type (#586).
- **a curated punctuation set** — `space _ - ( ) '` plus `. ,` (printed
  names such as `Mrs. Adil`, `Miriam, daughter of our Rebbe`), U+2019 RIGHT
  SINGLE QUOTATION MARK (parity with the ASCII apostrophe already
  admitted — a name such as "Be'er Mayim Chaim" may carry either one,
  depending on source, and both must sanitize the same way), and U+05BE
  HEBREW PUNCTUATION MAQAF, U+05F3 GERESH, U+05F4 GERSHAYIM — letter-level
  orthography in Hebrew and Yiddish names, not decoration.

Admitting `.` means two properties that used to come free from excluding it
outright are now enforced explicitly, in code — plus one new guard the mark
leg above makes necessary on its own:

- a run of `.` collapses to a single `.`, and `.` is stripped at both ends
  alongside `_`/space — so a sanitized stem can never *be* or *contain* a
  `..` traversal segment, can never start with `.` (which would also hide
  the note from `diff_rendered_output.py`'s recursive vault walker, which
  skips a dot-prefixed path component at every level — a dot-named note
  would be invisible to the render+diff acceptance gate that is supposed
  to be watching this adapter's own output), and can never end with `.`;
- while the candidate still ends in `.md`, case-insensitively, that `.`
  becomes `_` (`x.md` → `x_md`, `x.MD` → `x_MD`) — `.md` is the extension
  this adapter itself appends, so a stem already carrying one would make
  the wikilink identity (the relpath minus the appended `.md`) name a file
  that does not exist;
- if every surviving character is a combining mark, the deterministic
  fallback name is used instead of the mark-only result — a stem made
  entirely of invisible marks is a filename no reader can even see, let
  alone type.

`tests/render_obsidian.test.py` pins all three properties above and the
exact stems the three legs produce.

Two further caps exist for a different reason — not what the name *says*,
but whether the filesystem will accept it at all. `_write_note` runs after
`_clean_vault_content` has already emptied the managed vault, so a name the
kernel refuses does not lose one note: it aborts the render over a
half-rebuilt vault. Both are applied before the normalization tail, and both
were the review's finding rather than #586's:

- **240 bytes** per stem, truncated on a character boundary. `NAME_MAX` is
  255 — bytes on ext4, characters on APFS — so a byte budget is
  conservative for both, and 240 leaves room for the appended `.md` and for
  `_dedupe_path`'s `-<n>` suffix. The truncation collisions this can create
  are exactly what `_dedupe_path` already resolves. Half of this is
  pre-existing: 300 alphanumeric characters already produced a
  300-character stem before the mark leg existed.
- **30 consecutive combining marks** per base, the rest replaced with `_`.
  Measured: macOS creates a filename with 31 marks on one base and refuses
  32 with `EILSEQ` regardless of length, so the byte cap does not cover it;
  30 leaves a margin under that measured threshold, and defends that
  predicate only. It over-catches in one direction on purpose — macOS
  accepts `A` + 30 marks + U+034F CGJ + 30 marks, and this cap truncates it
  anyway, because CGJ is itself a mark and counts toward the run. Under-
  catching aborts a render; over-catching costs a name no orthography
  produces, since a fully pointed Hebrew letter carries three or four marks,
  not thirty-one.

Accepted, documented residuals:

- **Format characters** (`Cf` — ZWJ/ZWNJ/RLM and friends) are deliberately
  NOT admitted: they are invisible, so admitting them would let two names
  that look identical to a reader resolve to two different files. The same
  reasoning cuts the other way for a mark this sanitizer DOES admit: two
  source forms differing only by an invisible mark outside `Cf` (e.g.
  U+034F COMBINING GRAPHEME JOINER) now sanitize to two visually identical
  filenames — accepted, since excluding all of `Mn`/`Mc`/`Me` to close that
  gap would also exclude legitimate niqqud and cantillation.
- A stem that happens to end in some OTHER recognized extension (`.png`)
  is not neutralized — only a trailing `.md` is, since a general
  trailing-extension rule would damage legitimate names like `J.R.R`.
- Win32 reserved device basenames — Microsoft's own list of 28: `CON`,
  `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9` and the six ISO/IEC
  8859-1 superscript aliases `COM¹` `COM²` `COM³` `LPT¹` `LPT²` `LPT³`,
  which Windows treats as digits in a device name and which `str.isalnum()`
  admits — get
  a `_` appended to the basename — `AUX.txt` → `AUX_.txt`, `CON` → `CON_` —
  because a device name stays reserved when an extension follows it, so
  both `AUX.txt` and the emitted `AUX.txt.md` are device paths. The bare
  form was already unwritable before #586 and admitting `.` widened the
  class, which is what makes it the same defect as the two caps rather than
  a separate wish. Enforced on every platform, not only Windows: a vault is
  copied and synced between machines, so a name unwritable *there* is a
  defect wherever it was rendered. Names that merely start with or contain a
  device word (`Constantine`, `Aux Chien`, `nulla`) are untouched.

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
