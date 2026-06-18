# Glossary discipline

The handbook and the underlying product must use **one canonical term per concept**. The glossary
is the single source of truth that prevents term drift across the product's lifetime and across
chapters written months apart. Without this discipline, the same feature ends up with three names,
the user's mental model fractures, and search inside the handbook stops working.

You apply this discipline on every chapter, every time. No exceptions for "small" chapters.

## The one-canonical-term rule

For every domain concept the chapter touches, you commit to a single canonical form and use only
that form in prose.

- "Canonical" means **the term as written in the language declared by `glossary.canonical_term_language`**.
  If the profile sets it to the same value as `language.code`, the canonical form is the reader-facing
  word; if it diverges (e.g. a multilingual product whose glossary stays in the source-of-truth
  language), follow the profile.
- A "concept" is identified by what it *does* in the product, not by what it is called in any one
  screen. If two UI labels point at the same underlying thing, that is one concept with two surface
  forms, not two concepts.
- The canonical form is what appears in prose. Other forms (alternate spellings, legacy names,
  vendor aliases) live only inside the glossary entry as synonyms.

If the running UI uses more than one word for the same concept, you pick ONE as canonical, record
the rest under the synonym list, and use only the canonical form in prose. Note the divergence in
the entry so a future author understands why the chosen form was chosen.

## Mandatory extension

Before you publish a chapter, every domain term used in that chapter must have an entry under
`publish.glossary_dir`. This is non-negotiable.

- While authoring: for every domain term you write, you confirm it has an entry.
- If a term is missing: you **extend the glossary** before publishing. Add the entry, then continue.
  You do not leave a term undefined and you do not defer it.
- If you cannot define a term confidently (you do not understand it well enough from the running UI
  and the code), you halt and ask. You do not ship a placeholder definition. A wrong definition in
  the glossary poisons every chapter that links to it.
- "Domain term" means any noun phrase a reader would not understand from general knowledge of the
  product's category. Common words ("save", "cancel") are not domain terms. Product-specific nouns,
  role names, workflow stages, named artifacts, and vendor-of-record labels are.

The glossary grows monotonically with the handbook. Each chapter either reuses existing entries or
adds new ones; entries are not removed unless the concept itself is removed from the product.

## Synonym discipline

You list synonyms under the `glossary.synonym_field_name` field of each entry. This is where every
non-canonical surface form goes:

- **Alternate spellings**: the same word written with or without a hyphen, with or without a
  diacritic, in title case versus lowercase.
- **Legacy names**: what the feature was called before a rename. Readers searching for the old name
  must find the entry.
- **Vendor aliases**: when a third-party integration uses a different word for the same concept,
  record the vendor's word as a synonym and note which vendor uses it.
- **Internal-jargon variants**: words the engineering team uses that leak into UI strings or admin
  screens. Capture them so they resolve to the canonical form.

Use an em dash, the literal string "none", or the empty list (whichever convention the existing
entries use) when an entry genuinely has no synonyms. Do not omit the field — its presence signals
that synonyms were considered.

The rule for prose is strict: **the canonical form appears in prose; synonyms appear only inside
the glossary entry**. If a synonym shows up in a chapter body, that is a defect — either the prose
is wrong or the synonym should be promoted to canonical and the entry rewritten.

## English-code identifier (when required)

When `glossary.english_code_required: true`, every entry carries a stable English-language
identifier alongside the canonical form. This identifier ties the reader-facing word to whatever
exists in the codebase: the class name, the route name, the API field, the enum case.

- The identifier is English even when `glossary.canonical_term_language` is not. Code identifiers
  in this codebase are English by global policy; the glossary makes that link explicit.
- The identifier is stable: if the reader-facing word changes but the underlying code does not, the
  identifier stays the same and the canonical form moves. If the code is renamed, the identifier
  updates and the old name becomes a synonym.
- One entry, one identifier. If two reader-facing concepts share a code identifier, you have either
  found a synonym (collapse into one entry) or a code-side ambiguity worth flagging to engineering.
- When `glossary.english_code_required: false`, you omit this field. Do not invent identifiers for
  a product whose glossary is not tied to a specific codebase.

## First-occurrence linking

In each chapter, you link the canonical term on its first occurrence to its glossary entry.
Subsequent uses in the same chapter are plain text.

- The link target is the entry inside `publish.glossary_dir`. The link syntax depends on the
  publish target — the adapter at `references/publish-targets/<publish.target>.md` controls the
  exact form. You produce the link; the adapter dictates whether it is a wikilink, a relative
  markdown link, an anchor, or something else.
- First occurrence is per chapter, not per handbook. A reader who lands on chapter seven via search
  needs the link as much as the reader who started at chapter one.
- If the same term appears in a heading and in the body, the body's first occurrence is what you
  link. Headings stay plain to keep the rendered TOC clean.
- Repeating the link on every occurrence is noise; omitting it on first occurrence is a defect.
  One link per chapter per term.

## Seeding from the existing index

Before you author the first chapter — and again whenever you start work in a fresh area of the
product — you scan `publish.glossary_seed` for terms that already exist. This file is the
project's pre-existing list of domain terms; it tells you what canonical forms have already been
agreed.

- You read `publish.glossary_seed` in full. You note which terms are already entries under
  `publish.glossary_dir` and which are still seeds awaiting a real entry.
- You reuse existing canonical forms verbatim. You do not invent a new spelling when one already
  exists in the seed.
- When you create a real entry for a seeded term, you update the seed file's marker (whatever
  signal that file uses to distinguish "seed" from "active") so future authors see the entry now
  exists.
- If `publish.glossary_seed` is unset or the file is missing, you proceed without it — but you note
  in your working memory that no seed was available, which makes term-drift risk higher and the
  scan of `publish.glossary_dir` more important.

## Pre-publish checklist

Before you publish a chapter, you confirm:

- Every domain term in the chapter has an entry under `publish.glossary_dir`.
- Each entry uses the canonical form per `glossary.canonical_term_language`.
- Each entry carries an English code identifier when `glossary.english_code_required: true`.
- Synonyms are listed under `glossary.synonym_field_name`; none appear in chapter prose.
- The first occurrence of each domain term in the chapter is linked to its entry; later occurrences
  are plain.
- New entries reference the seed if the term came from `publish.glossary_seed`.

If any of these fails, you fix the glossary or the chapter before publishing. You do not publish a
chapter that puts the glossary out of sync.
