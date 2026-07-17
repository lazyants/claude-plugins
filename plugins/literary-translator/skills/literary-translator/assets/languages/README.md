# Language presets (`assets/languages/`)

Each `<code>.json` here is a **source-language extraction config** for
`scripts/bootstrap_names.py` — see
[`references/language-pair-parameterization.md`](../../references/language-pair-parameterization.md)
for the full contract. Step 0a copies every file in this directory verbatim
to `${durable_root}/languages/`; a project's `profile.yml` points at one via
`source.language.particle_config` (always a bare filename, e.g. `"fr.json"`).

Every file contains **four required keys**, plus an optional fifth:

| Key | Type | Meaning |
|---|---|---|
| `PARTICLES` | `string[]` | Lowercase name particles that can bridge two capitalized tokens into one proper-noun run (French `de`/`du`/`des`/`la`/`le`/.../`von`/`van`/`saint`/`sainte`, etc.). |
| `STOPWORDS` | `string[]` | Exact surface forms of capitalized-but-not-a-name function words (sentence-initial articles, pronouns, conjunctions, honorifics) — checked case-sensitively against the token as it appears in text. |
| `has_elision` | `boolean` | Whether this language fuses an elided article onto the next word (French/Italian `d'Effiat`, `l'Autriche` — German/English/Russian don't). |
| `ELISION_RE` | `string \| null` | Required (non-null) iff `has_elision: true`. A regex with **exactly 2 capture groups**: group 1 is the elided article's own remnant, group 2 is the name-initial remainder. `null` when `has_elision: false`. |
| `name_inventory` | `string[] \| null` (optional) | Project-local list of full native-script name forms (multi-token allowed, e.g. a two-word Hebrew name), matched as complete forms — not assembled from token runs — to caseless/multiword-surface candidates whose script has no ASCII/`Lu` initial-cap signal (Hebrew letters are Unicode category `Lo`, invisible to the capitalization gate). Absent or `null` in every preset below; none of the shipped Latin-script files set it. |

Beyond these five, no field is added on a whim (e.g. the real historiettes-t3
script's own French-only `CONTRACTION_RE` heuristic is deliberately *not*
part of this contract — a config that needs to reject elided-contraction
openers like `C'est`/`J'ai` as false-positive candidates lists their exact
surface forms in `STOPWORDS` instead, keeping every language file's shape
uniform).

The #91 **capitalized-elision ambiguity detection** (1.3.5) also needs no new
key: `bootstrap_names.py` reuses each config's own `ELISION_RE` verbatim to
*flag* — never split — a capitalized single-token candidate that might be an
elided article plus an already-known name (`elision_ambiguous: true` +
`elision_stripped_form`), leaving the accept-vs-`review_queue` call to the
glossary adjudicator. Because it reuses the existing key rather than adding one,
it generalizes to `fr.json` and `it.json` alike. See
[`references/gotchas.md`](../../references/gotchas.md)'s elision lesson and the
1.3.5 `CHANGELOG.md` entry.

## Status of each shipped preset

| File | Status | Basis |
|---|---|---|
| **`fr.json`** | **Proven.** | Extracted **verbatim** from `historiettes-t3/bootstrap_names.py`'s own `PARTICLES`/`STOPWORDS`/`ELISION_RE` literals — the real, project-specific script that ran correctly over ~143,000 real words of *Les Historiettes de Tallemant des Réaux*, tome 3 (17th-century French). Nothing here was reinvented or approximated; every entry traces back to that one script's actual, battle-tested data. |
| `de.json` | **Unverified starting point.** | Particle list (`von`, `zu`) from general knowledge. Stopword list intentionally minimal (~30 common capitalized pronouns/articles/conjunctions). `has_elision: false` — German has no article-apostrophe elision comparable to French/Italian. **Not smoke-tested against any real book.** German additionally capitalizes *every* common noun (not just proper nouns), which this stub's stopword list does not attempt to solve — expect to hit far more false-positive candidates here than with `fr.json` until a real project extends it. |
| `es.json` | **Unverified starting point.** | Particle list (`de`, `del`, `la`, `los`, `las`) from general knowledge. Stopword list intentionally minimal (~40 entries). `has_elision: false` — Spanish `del`/`al` are obligatory `de+el`/`a+el` contractions, not apostrophe elision, so they don't defeat the tokenizer's capitalization gate the way French/Italian elision does. **Not smoke-tested against any real book.** |
| `it.json` | **Unverified starting point.** | Particle list (`di`, `da`, `del`, `della`, `dei`, `degli`, `delle`, `dal`, `dallo`, `dagli`, `dalla`, plus bare `d`/`l` for post-elision-split joins) from general knowledge. Stopword list intentionally minimal (~40 entries). `has_elision: true` — Italian *does* elide (`d'Annunzio`, `dell'Adige`, `l'Aquila`) — with a starter `ELISION_RE` matching a lowercase remnant + apostrophe + capitalized remainder. **Not smoke-tested against any real book**, and the starter regex only matches a *lowercase*-initial elided remnant — a sentence-initial capitalized form (`L'Aquila` as a fixed proper-noun spelling) is not split by design, since in that case the whole fused token already starts with a capital and survives the extractor's capitalization gate intact. 1.3.5 handles the *ambiguous* case of such a fused form — where the stripped remainder is itself another candidate — without touching this by-design behavior: `bootstrap_names.py` flags it `elision_ambiguous` for the glossary adjudicator rather than splitting it (see the key-contract note above and #91). |

**These three are unverified starting points, expected to grow — the first
real project in each language should treat its `STOPWORDS`/`PARTICLES` list
as a living document, adding entries as real text surfaces gaps, exactly
like `fr.json`'s own history** (it started as a project-specific script and
only became "proven" after running correctly over an entire real book).

## Before trusting any of these on a real project

**Every** language config — including the unmodified, proven `fr.json`
reused on a *different* book — requires its own fresh smoke test before
`bootstrap_names.py`'s output is trusted for a mass run. The gate is keyed
to a triple identity (this exact config's content, this exact project's own
extracted-source-sample, and `language_smoke_report.py`'s own version), not
"did the file change." See
[`references/language-pair-parameterization.md`](../../references/language-pair-parameterization.md)
for the full procedure, and
[`references/gotchas.md`](../../references/gotchas.md) for the
`french-elision-tokenizer-miss` lesson this whole mechanism generalizes from:
a regex-based extractor over a source language with grammatical elision can
silently and *totally* — not just imprecisely — drop names behind an elided
article, because the fused token's first character defeats the
capitalization gate.

## Adding a project-local override

Never hand-edit a shipped preset in place — Step 0a overwrites it
unconditionally on every run. Instead, copy
`${durable_root}/languages/<code>.json` to a sibling file under a filename
Step 0a never touches (e.g. `fr.local.json`), edit the copy, and repoint
`source.language.particle_config` at its bare filename (e.g. `"fr.local.json"`,
never `"languages/fr.local.json"`).
