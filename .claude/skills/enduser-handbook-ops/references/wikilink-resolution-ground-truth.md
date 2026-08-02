# Obsidian & Quartz wikilink resolution — measured, not documented

**Open this before writing, validating, or specifying any `[[…]]` target** in any plugin that emits
into an Obsidian vault (`enduser-handbook`'s obsidian-vault adapter, `literary-translator`'s
`render_obsidian.py` / `validate_backlinks.py`, `obsidian-project-vault`). Established 2026-07-22
from PRIMARY SOURCES because the public docs are incomplete and the forum folklore is wrong.

## The one rule

**Emit a VAULT-ROOT-RELATIVE path.** It is the only spelling that is first-class in Obsidian and
also survives Quartz `shortest`. A raw repo/project-relative path and a bare-basename path both
LOOK right and both fail.

| spelling (glossary really at vault-relative `knowledge/glossary/index.md`) | Obsidian | Quartz `shortest` |
|---|---|---|
| `[[vault/knowledge/glossary/index]]` — project-root path, vault root is `vault/` | **✗** | ✗ |
| `[[glossary/index]]` — basename of the dir + `/index` | ✓ but only via the last-resort suffix tier | ✗ |
| `[[knowledge/glossary/index]]` — **vault-root-relative** | ✓ **exact-match tier** | ✓ |

## Obsidian (measured on 1.12.7)

`MetadataCache.getLinkpathDest`, resolution tiers in order — everything lowercased, so **all
resolution is case-insensitive**:

0. Candidate set = files whose **basename** matches (retry with `.md` appended). No basename match ⇒
   resolution stops dead. **No alias/title fallback** in the desktop app.
1. Bare name + exactly one candidate ⇒ that file.
2. Explicit `./` or `../` ⇒ joined onto `dirname(source)`, **exact** full-path match required.
3. **Exact vault-root path** match (leading `/` stripped first). ← the tier you want
4. `linkpath.startsWith("/")` ⇒ returns `[]`, **no fallback at all**. Never emit a `/`-anchored link.
5. Suffix fallback: `path.endsWith(linkpath)`, bucketed by source dirname, each sorted by path
   length ascending.

Consequences that bite:

- A path **longer than / not a suffix of** the real vault path resolves to nothing — and **clicking
  it silently CREATES a file at that literal path inside the vault.** This is how a project-root
  path (carrying the extra `vault/` segment) turns a broken link into vault pollution.
- **A single-segment (bare) linkpath STILL reaches tier 3 when a same-basename collision exists —
  so a file genuinely at the vault ROOT is NOT shadowed.** `[[items]]` for a real `items.md` at the
  vault root, with a foreign `archive/items.md` also present: tier 1 fires ONLY on **exactly one**
  candidate, so the 2-candidate collision skips it → tier 3's exact full-path test
  (`candidate.path.toLowerCase() === linkpath`, `.md` appended on retry) uniquely matches the root
  `items.md` (`archive/items.md` ≠ `items.md`). The bare-basename bug is therefore **nested-only**:
  `[[items]]` emitted for `handbook/items.md` misses tier 3 (`handbook/items` ≠ `items`) and falls
  to the fragile tier-5 suffix. Corollary: for a root-level file the single-segment vault-root-
  relative path IS its tier-3 key — emitting a bare slug there is correct, not a downgrade.
  (Re-verified 2026-07-23 by extracting `getLinkpathDest` from the installed Obsidian `app.js`, not
  from docs — the candidate set is `uniqueFileLookup` keyed by basename; tiers 1→3→5 exactly as above.)
- Tier 5's `endsWith` is a **raw string suffix, not segment-aware**: `[[y/index]]` resolves to
  `knowledge/glossary/index.md`. Anything relying on tier 5 is loose and source-dependent.
- Heading anchors are case-insensitive and whitespace-collapsed; `[!"#$%&()*+,.:;<=>?@^\`{|}~/\[\]\\]`
  are all replaced by a space on both sides — but `-`, `_`, `'` must match literally. German `ß` is
  NOT folded to `ss`. Unicode normalization (NFC vs NFD) is NOT applied to heading text — a live
  risk for decomposed umlauts or Hebrew with niqqud.

## Quartz (v4 AND v5 — the matcher differs)

- **v4** compares the whole slugified target against each slug's **last segment only**.
- **v5** adds multi-segment **suffix** matching and lowercases (a documented BREAKING change).
- Both, on 0 or ≥2 matches, fall back to treating the target as **content-root-absolute** — which is
  why the vault-root-relative form works: it IS that path (when content root == vault root).
- `simplifySlug` strips a trailing `index` **before** matching, so `glossary/index` collapses to
  single-segment `glossary`, matches nothing, and falls back to a non-existent `/glossary/`.
- A bare `index` target NEVER reaches the matcher — it always means the site root, so **a nested
  `index.md` is unaddressable by bare name.**
- `shortest` is the default in a freshly created site.

**The precondition everyone forgets:** vault-root-relative == content-root-absolute only when
**Quartz's content root IS the vault root**. If content lives at `vault/content/`, the form carries a
stale prefix and resolves nowhere. State that precondition; do not claim the form "just works".
Under `markdownLinkResolution: relative`, *no* spelling resolves — that mode needs genuinely relative
links.

## How to re-derive (do not trust this file forever)

- Obsidian is closed-source but **not opaque**: its JS is plain text inside
  `/Applications/Obsidian.app/Contents/Resources/obsidian.asar`. Grep for `getLinkpathDest`,
  transcribe it, and **execute it** against synthetic vault fixtures — that is how the table above
  was produced, not by reading docs.
- Quartz: read `quartz/util/path.ts` (v4) and `quartz-community/utils/src/path.ts` (v5) plus their
  own `path.test.ts` pins. Check the DEFAULT BRANCH first — it moved v4→v5.

## Related

- [[index_enduser_handbook]] — the obsidian-vault adapter (#247 is exactly this bug, twice).
- [[index_literary_translator]] — `render_obsidian.py` emits `[[…]]` too; #245 is a collision-
  definition disagreement in the same area.
- SKILL.md §6 (a link-emission canon change has multiple write sites) and §1 (adapter-resolution
  drift) are the enduser-handbook-side consequences of getting this spelling wrong.
