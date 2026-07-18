# Language-pair parameterization

Two independent axes are profile-driven, and neither is hardcoded in any
script:

1. **Source-language extraction config** — what `bootstrap_names.py` needs to
   find proper-noun candidates in the source text (particle list, stopword
   list, elision handling).
2. **Target-language transcription/register rules** — how the target
   language renders those names and how it handles formality. This is
   project data, never plugin code.

This page covers axis 1 in full, including the mandatory smoke test that
gates trusting it, and touches axis 2 only briefly since it lives in
`style_bible.md`, not here.

## Axis 1: source-language extraction config

`assets/languages/<code>.json` ships inside the plugin and is copied to
`${durable_root}/languages/<code>.json` by Step 0a — never read from inside
the plugin install directory at runtime. A project points at one via
`source.language.particle_config` in `profile.yml`, and that field is
**always a bare filename**, e.g. `"fr.json"`, resolved as
`${durable_root}/languages/<value>` — never a `"languages/"`-prefixed value
(joining the two would double the `languages/` segment and the path would
never resolve). The value must be a simple
alphanumeric/dot/underscore/hyphen filename ending in `.json`. Its schema
pattern is `^[A-Za-z0-9._-]+\.json$`; `profile_validate.py` still fatally
rejects, naming the field, any `particle_config` value containing `/`, `\`,
`..`, or an absolute-path prefix.

Step 0 does **not** existence-check `source.language.particle_config`; that is
deferred until the end of Step 0a, after shipped presets have been copied into
`${durable_root}/languages/`. Step 0a's last action resolves the bare filename
as `${durable_root}/languages/<value>` and halts with a field-named error if it
still is not a real file, covering both a typoed shipped preset and a missing
project-local override. `bootstrap_names.py` dereferences the same literal
`particle_config` value — never `source.language.code` — so an override such as
`fr.local.json` is not silently ignored.

A language config file contains four required keys, plus an optional fifth:

- `PARTICLES` — the source language's particle list (e.g. French
  `de/du/des/la/le/l'/von/van/saint/sainte`; German `von/zu`; Italian
  `di/da/del/della`; Spanish `de/del/de la`).
- `STOPWORDS` — source-language function words that are capitalized but are
  not names (sentence-initial articles, pronouns, conjunctions).
- `ELISION_RE` — the elision pattern, for languages that fuse an elided
  article onto the next word (French/Italian `d'Effiat`, `l'Autriche`;
  German/English/Russian don't).
- `has_elision` — boolean. **This field lives only in the resolved
  `particle_config` file, never in `profile.yml`.** An earlier design put it
  in the profile; it was removed from `profile.schema.json` entirely because
  `bootstrap_names.py` already reads `has_elision` from the same language
  file it reads `PARTICLES`/`STOPWORDS`/`ELISION_RE` from, and a second copy
  in the profile could drift out of sync with the file that's actually
  authoritative. To change whether a language elides, edit the resolved
  language file directly (or fork a `.local.json` override — see
  "Remediation" below).
- `name_inventory` — optional, `string[] | null`, absent/`null` by default
  and unset in every shipped preset (`fr.json`/`de.json`/`es.json`/`it.json`
  are unaffected). A **project-local** list of full native-script name forms
  — multi-token forms allowed (e.g. a two-word Hebrew name) — that both
  extraction implementations (`bootstrap_names.py` and
  `language_smoke_report.py`) match as **complete forms**, not assembled from
  a token run the way the particle algorithm builds Latin-script candidates.
  It exists for caseless/multiword surfacing where the ordinary
  capitalization-run path finds nothing: `is_upper_initial`
  (`bootstrap_names.py:148`) gates on ASCII or Unicode category `Lu`, and
  Hebrew letters are category `Lo` — invisible to that gate — so without a
  `name_inventory` a Hebrew name surfaces zero candidates. Matching a
  `name_inventory` form never crosses an existing sentence-terminator
  boundary (the same boundary the particle-run algorithm already respects),
  so a form split across `. ` is not joined into one candidate. Add it to a
  language file (a shipped preset, or a project's own `.local.json`
  override) only when that language's script needs this bypass; it has no
  effect on any project using the four required keys alone.

**Shipped presets:**

| File | Status |
|---|---|
| `fr.json` | Fully populated, extracted directly from the proven `bootstrap_names.py` — the one real, battle-tested config. Proven against Historiettes' own 17th-century French text specifically (~143k real words of that book), **not** against French prose in general. |
| `de.json`, `es.json`, `it.json` | Thinner starter stubs — particle lists from general knowledge, stopword lists intentionally minimal. `assets/languages/README.md` states explicitly: unverified starting points, expected to grow, exactly like `fr.json`'s own history. |
| `he.json` | The concrete **uncased-script** preset. Hebrew letters are category `Lo`, so the `Lu`-gated capitalization run never fires: `PARTICLES` is `[]` by design and the preset alone surfaces zero native-script candidates (a project must add a `name_inventory` override — see the fifth key above and the uncased-script trap in "Sample selection"). `STOPWORDS` lists standalone, whitespace-delimited Hebrew function words only — never single-letter proclitics (`ה`/`ב`/`כ`/`ל`/`מ`/`ש`/`ו`), which fuse onto the next word and so never appear as a whitespace token in `final_audit.warn_foreign_remainder`'s remnant scan. `has_elision: false`. Unverified starting point, not smoke-tested against a real book. |

Character-class detection (is this token's first letter uppercase) uses
Unicode-category (`unicodedata.category(ch) == 'Lu'`) as the primary check,
with the ASCII+French-accented literal range kept only as a fast-path. This
makes a Cyrillic- or Greek-alphabet source book *plausible*, not proven —
the mandatory smoke test below is what actually establishes that on real
text, for any language, including one that superficially "looks like it
should just work."

An earlier round of this project generalized a real bug from this exact
extractor — preserved in `references/gotchas.md`:

> Any regex-based proper-noun extractor over a source language with
> grammatical elision will silently and totally — not just imprecisely —
> drop names behind an elided article, because the fused token's first
> character defeats the capitalization gate. Test any new
> `languages/<code>.json` with synthetic `d'X`/`l'X`/`qu'X`-equivalent (or
> that language's own elision pattern) sentences before trusting it on real
> text.

The mandatory smoke test below is the generalized gate this lesson became,
not a one-off note-to-self.

## Axis 2: target-language transcription/register rules

Explicitly project data, never plugin code. The plugin does not ship any
`fr→ru` or other transliteration ruleset. The shipped
`style_bible.template.md` (copied to `${durable_root}/style_bible.md` at
Step 0a) has a section C-translit that is a skeleton with instructions —
"state your book's fixed source→target practical transcription rule here" —
filled in per book by the codex glossary pass plus the project's own
research. Section B (a formal/informal register matrix — ты/вы, Sie/du,
vous/tu) is optional: delete it if the target language has no T–V
distinction.

## Why even unmodified `fr.json` needs a fresh smoke test

This is design decision 2 (inventory §18 / plan §19): **"any language pair"
is downgraded to a smoke-tested claim, not a general one.** French source
extraction is proven only against Historiettes' own text. Reusing the
unmodified, proven `fr.json` for a *different* book still requires its own
fresh smoke test — the claim "French extraction works" was never a claim
about French, it was a claim about this one script running correctly over
this one book's ~143k words. A different book's vocabulary, orthography,
and name density are untested territory even when the language code and the
config file byte-for-byte match a config that has already passed.

The cache key (`particle_config_hash`, see "Failure and remediation" below)
protects an *already-verified* config from silently drifting later. It is
never a substitute for the smoke test itself — a config whose bytes have
never changed can still be running against a source it has never seen.

## The mandatory smoke test

The gate is keyed to a **TRIPLE identity**, not "did the config change":

1. **This exact `particle_config` file's content** (`particle_config_sha1`).
2. **This exact project's own extracted-source-text sample**
   (`source_sample_sha1`).
3. **`language_smoke_report.py`'s own version**
   (`smoke_report_contract_hash` — sha1 of the script's own bytes, so a
   stored `pass:true` report generated by a since-fixed older version of the
   script is never silently trusted forever, for either a brand-new or an
   already-started project).

**Sequencing:** W2 (extraction) must run before W3 (the smoke test) — the
smoke test needs the `manifest.json` W2 produces to build its source
sample, so it cannot run before extraction exists. **W3 in `SKILL.md`**
(never Step 0, and never inside `profile.schema.json`, which cannot hash a
file) computes all three hashes procedurally on every run and treats a
missing report, a mismatch on any of the three hashes, a `has_elision`
disagreement with the currently-resolved language file, or a stored
`pass:false`, as a **fatal gate blocking W4/W5**.

`source.language.smoke_test.report_path` is a string-or-null profile field.
Step 0 validates only its shape. When it is `null`, W3 derives
`${project.durable_root}/runs/language-smoke-report.json` fresh every run
(nothing is written back to the profile). A project may set an explicit
non-default string instead; when set, it must be a bare relative path
starting with `runs/` and matching `^runs/[A-Za-z0-9._/-]+$`; the actual
`..`-segment rejection is procedural, so `profile_validate.py` fatally
rejects any value containing `..`.

Step 0a also resolves any explicit non-null `smoke_test.report_path` and
creates that value's specific parent directory (`mkdir -p`, idempotent)
under `${durable_root}` before W3 can write the report. `null` is skipped
because the derived default already lives directly under the fixed `runs/`
skeleton. Every non-null value gets this treatment: the schema requires the
literal `runs/` prefix, so there is no outside-`durable_root` branch to
mirror from `output.destination`. For example, `runs/custom/report.json` is
valid, Step 0a creates `runs/custom/`, and W3's later write must succeed on
the first attempt rather than failing on a missing parent directory.

### Procedure

1. Run `bootstrap_names.py` against a real sample of *this book's* actual
   source text (not synthetic sentences only).
2. Hand-pick a list of names/titles a human reader can already see in that
   sample, and manually verify every one of them surfaces as a candidate
   (it doesn't need to rank "strong" — just present).
3. Test the elision/particle rules with synthetic sentences built from
   *this source language's actual* elision/particle patterns — not just
   French `d'X`/`l'X` examples, which don't apply to a non-elision language.
4. Run `scripts/language_smoke_report.py`, which owns the whole report:
   deterministic, schema-validated, never free-text.

### Sample selection (stratified, not front-loaded)

Sampling only the first 1–3 body segments can pass on easy, front-loaded
prose while the particle/elision/name forms that actually break extraction
show up later — a smoke test that only ever reads the introduction proves
much less than it appears to. Instead, let the ordered list of body-kind
segments (by `order_index`) have length N. Select **four anchor segments**,
deduplicated if N is small:

1. **first** — the first body segment.
2. **middle** — the body segment at index `N // 2`.
3. **late** — the last body segment.
4. **high-density** — scan every body segment's `plain_text` for a cheap
   density signal (capitalized-run tokens per 100 words, plus, when
   `has_elision`, elided-article patterns per 100 words) and pick whichever
   not-already-selected segment scores highest.

**Plus a fifth anchor, `frontback`**, whenever `manifest.json`'s
`frontback[]` has *any* `decision:"translate"` entry: concatenate the
`plain_text` of *every* `translate`-decision `kind:"frontback"` segment —
all of them join, not just one representative, since front matter is
typically short and discrete, not a narrative arc to sample a position
from. Absent entirely when no translate-decision frontback exists (e.g. a
`plain_text` source, which has no frontmatter concept at all).

Concatenate the four body segments (or fewer once duplicates collapse) plus
the fifth anchor's content when present — each capped at 750 words — in
`order_index` order for the body anchors, frontback content appended after.
`source_sample_selection` records the exact chosen segment IDs, which
anchor each one filled, and each one's own `kind` (`"body"` |
`"frontback"`) — specifically
`{method:"stratified_v1", segments_used:[{seg_id, anchor, kind}], word_count}`
— fully auditable, not just a word count.

### Normalization before hashing

Collapse all whitespace runs to a single space (the same normalization
`validate_draft.py`'s distinctness check uses) before computing
`source_sample_sha1`, so a harmless re-wrap of the same underlying text
doesn't spuriously invalidate a report.

### No vacuous passes

On the **default** path (`low_name_density_confirmed:false`, i.e.
`candidate_names_total >= 10` -- see below), `--checked-names` must supply
at least **10 names**. This is script-enforced (`language_smoke_report.py`
refuses to run -- fatal error, no report written -- if fewer are supplied)
and, on THIS branch only, also schema-enforced: `language-smoke-report.schema.json`'s
`checked_names` property itself has `minItems: 0` unconditionally, but an
`if`/`then` keyed on `low_name_density_confirmed:false` raises the
effective floor to `checked_names.minItems: 10` for exactly this branch.
There is no silent `pass:true` for an empty or near-empty check list. The
low-density and zero-candidate branches below relax this floor (each with
its own script- and/or schema-enforced conditional requirement, not a bare
`minItems: 10`).

### Low-name-density path

The 10-name floor stays the default (it correctly blocks a vacuous pass on
a name-dense book like Historiettes), but it would also block a genuinely
valid project — a short essay, a poetry collection, philosophical prose —
where a human cannot honestly hand-pick 10 real proper names because the
sample doesn't contain that many. `language_smoke_report.py` computes
`candidate_names_total` (the count of distinct proper-noun candidates
`bootstrap_names.py` itself finds in the selected sample) and branches:

- `candidate_names_total >= 10`: unchanged. `checked_names` must supply at
  least 10 **distinct** names — a **sample**, not every candidate;
  `low_name_density_confirmed` is written `false`. `parse_checked_names()`
  deduplicates its input first (first-occurrence order), so this is a
  genuine distinct-name count, not a raw entry count — `--checked-names
  Alice,Alice,...` (ten copies of one name) collapses to one distinct name
  and is refused.
- `candidate_names_total < 10`: without `--low-name-density-confirmed` on
  the command line, the script still refuses to run — same fatal, no report
  — forcing a conscious human acknowledgment that the source really is this
  name-sparse. **With** `--low-name-density-confirmed`: `checked_names`
  (after dedup) must **set-cover every distinct candidate**
  (`set(checked_names) == candidate_name_set`, checked against the deduped
  set, not a raw entry count). `low_name_density_confirmed` is written
  `true`. The check is enforced procedurally by the script itself, not by
  the schema (a JSON Schema validates shape only, and cross-checking one
  field's values against another set isn't a native schema keyword).
  `--checked-names Alice,Alice` against candidates `{Alice, Bob}` first
  dedupes to `["Alice"]`, which does not cover `Bob` — the script FATALs,
  naming every still-uncovered candidate, rather than silently passing.

**Zero-candidate case gets its own, separate branch** — a flat `minItems:1`
on the low-density path would directly conflict with `len==0` when the
count is genuinely zero. When `candidate_names_total == 0`, the script
requires a *separate, stronger* flag, `--no-names-confirmed` (in addition
to `--low-name-density-confirmed` — the source genuinely contains not one
candidate proper name, a rarer and more extreme claim than merely "fewer
than 10"), and refuses to run without it. With it: `checked_names` is
legitimately allowed to be empty, and the report writes
`no_names_confirmed: true` alongside `low_name_density_confirmed: true`
(zero is a special case of low-density).

**Uncased-script sources are a distinct trap layered on top of this
branch.** `bootstrap_names.py`'s candidate predicate requires a Unicode
`Lu` (uppercase) initial — script-agnostic, but Hebrew, Yiddish, and Arabic
letters are all `Lo` (no case distinction), so no native-script token can
ever qualify as a candidate. `candidate_names_total` on such a source is
`0` unless some incidental cased-script token (stray Latin/Cyrillic text)
survives extraction — and even then that stray token isn't a native-script
name either. Either way, native-script proper names are **never surfaced**
by the candidate gate, so a `pass:true` reached via `--no-names-confirmed`
(the genuine-zero path above) or via checked cased-script candidates
certifies only that the `Lu`-gated detector found nothing more to check —
**not** "this text has no names." Inventory native-script names manually
before trusting either path on an uncased-script source.

### Particle smoke cases are decoupled from name density

`particle_smoke_cases`'s own requirement keys *only* to `particle_list_size
> 0` (the resolved language preset's own `PARTICLES` list length) —
regardless of `candidate_names_total`, `low_name_density_confirmed`, or
`no_names_confirmed`. A name-sparse project gets particle-tested for the
same reason a name-dense one does: `bootstrap_names.py`'s `PARTICLES` set
is consulted unconditionally for every candidate name it processes, with no
name-count gate anywhere in the run-building algorithm. Required whenever
`particle_list_size > 0`; optional only when `particle_list_size == 0` (a
genuinely particle-free language) **and** `--no-particles-confirmed` is
also passed.

### Elision test cases are self-contained in the report

`language_smoke_report.py` copies `has_elision` from the *resolved*
`particle_config` file (never from `profile.yml`, which does not have this
field at all) into the report itself, as a field. This is deliberate:
`language-smoke-report.schema.json` is a standalone schema and has no way to
see external profile or language-file state — it can only condition on
data inside the document being validated. Its `if/then` rule reads: `if
has_elision == true, then elision_test_cases required, minItems: 1`. When
`has_elision: false`, `elision_test_cases` is simply absent (not a loophole
— elision-miss risk doesn't apply there at all).

### CLI inputs

- `--checked-names name1,name2,...` — the hand-picked list (≥10, or per the
  density branches above).
- `--elision-test-file <path>` — required when `has_elision`. A small file
  of synthetic sentences + expected extracted names built from this
  language's actual elision pattern. Minimum 1 case.
- `--particle-smoke-file <path>` — required per the decoupled rule above. A
  JSON array of `{token: string, is_particle: boolean}` entries, e.g.:

  ```json
  [{"token": "de", "is_particle": true},
   {"token": "château", "is_particle": false},
   {"token": "l'", "is_particle": true}]
  ```

  Minimum 1 entry.

### Output (`language-smoke-report.schema.json` shape)

- `particle_config_sha1`
- `source_sample_sha1`
- `smoke_report_contract_hash` (sha1 of the script's own bytes)
- `source_sample_selection`
  (`{method:"stratified_v1", segments_used:[{seg_id, anchor, kind}], word_count}`)
- `candidate_names_total`
- `checked_names[]` — each with `found: boolean`
- `elision_test_cases[]` — each with `passed: boolean`
- `particle_smoke_cases[]` — each entry echoed back with `passed: boolean`
  (`true` only if the script's own `PARTICLES`/`STOPWORDS` classification
  for this token agrees with the human-supplied `is_particle`)
- `low_name_density_confirmed` / `no_names_confirmed` (booleans)
- `particle_list_size` / `no_particles_confirmed`
- `has_elision` (boolean)
- `pass` — a single field, `true` only if every checked name was found,
  every elision test passed, **and** every particle-smoke case (when
  present) passed. Never a human-typed "looks good" note standing in for
  an actual check.

## Failure and remediation

When `language_smoke_report.py` reports `pass:false` (a checked name wasn't
found, an elision test failed, or a particle-smoke case failed), the
terminal message shows:

- the failing preset's resolved filename (e.g. `fr.json`, or the current
  `particle_config` override),
- all three hashes — `particle_config_sha1` / `source_sample_sha1` /
  `smoke_report_contract_hash`,
- the `profile.yml` path being validated,
- the exact field(s) to edit, named by line-anchor where the script can
  determine one.

**Copy, don't edit the shipped preset in place.** Copy
`${durable_root}/languages/<code>.json` (never the plugin's own
`assets/languages/<code>.json` directly) to a project-local override, e.g.
`${durable_root}/languages/<code>.local.json` — inside the *same*
`${durable_root}/languages/` directory, under a filename Step 0a never
re-copies over. Set `source.language.particle_config` to the **bare
filename** of that copy, e.g. `"fr.local.json"` — never
`"languages/fr.local.json"`, which would double the `languages/` path
segment. The shipped preset stays clean and unmodified; this project's fork
is where the fix lands.

Fix the specific failure class:

- a missed checked name usually means an incomplete `PARTICLES` or
  `STOPWORDS` entry for this source language's actual usage — extend the
  list;
- a failed particle-smoke case means the `PARTICLES`/`STOPWORDS`
  classification for that token disagrees with the human-supplied
  `is_particle` expectation — fix the relevant list;
- a failed elision test usually means `ELISION_RE`'s pattern doesn't match
  this language's actual elision forms — fix the regex, re-deriving it from
  real examples in the source text, not from memory.

Re-run `language_smoke_report.py` against the **same** sample, checked
names, and elision/particle cases — never silently swap in an easier sample
to make the gate pass. If `pass:true` now, W3 can proceed — but on an
uncased-script source a `pass:true` certifies only what the `Lu`-gated
detector could reach, not native-script name coverage; inventory those
names by hand before trusting it.

**`particle_config_hash` (the ledger cache-key field) is what guarantees an
already-converged segment can never silently keep reflecting the pre-fix
config** — the moment the fix lands, it flips every affected segment to
`stale`. It does **not**, by itself, regenerate `canon.json` or any
already-built segpack: a name `bootstrap_names.py` previously missed
entirely still won't appear in a `stale` segment's existing segpack's
`canon_names[]`/`new_names[]` until the operator manually re-runs the
glossary pass and `segpack.py` for the affected segments. Do this
regeneration before re-running W5 on those segments — the cache-key flip
stops a stale segment from being silently reused, it does not
auto-regenerate its own inputs.

## See also

- [`ledger-and-resumability.md`](./ledger-and-resumability.md) —
  `particle_config_hash` and the other cache-key fields, and the
  `blocked_needs_regeneration` classification for a stale segment whose
  upstream artifact hasn't actually been regenerated yet.
- [`canon-and-glossary.md`](./canon-and-glossary.md) — how the codex
  glossary pass consumes `bootstrap_names.py`'s candidates and stamps
  `generation_hashes.particle_config_hash` into `canon.json`.
- `assets/languages/README.md` — the unverified-starting-point notice for
  every non-`fr` preset.
