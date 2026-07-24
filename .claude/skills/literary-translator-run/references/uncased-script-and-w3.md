# Uncased-script onboarding + W3 zero-candidate

## Why name-canon is effectively OFF on uncased scripts

`bootstrap_names.py` keys the capitalization gate on Unicode category **`Lu`** (uppercase). Hebrew (and Yiddish/Arabic) letters are category **`Lo`**, so it finds **`n_candidates: 0`** every time. (The output field is `n_candidates`, NOT `candidate_names_total`.)

The plugin anticipates this: the W3 mandatory smoke test has a zero-candidate branch. But be honest about what a green result means — see below.

## Author the language preset (`languages/he.json` / `he.local.json`)

Four REQUIRED keys (empty lists accepted) plus an OPTIONAL fifth `name_inventory` — the exact accepted set is `{PARTICLES, STOPWORDS, has_elision, ELISION_RE, name_inventory}` (`bootstrap_names.py`'s `load_language_config`). The zero-name path below uses just the four:

```json
{"PARTICLES":[],"STOPWORDS":[],"has_elision":false,"ELISION_RE":null}
```

To make the run actually FIND uncased names instead of accepting zero, supply the fifth key — see "Getting uncased names via `name_inventory`" below.

## W3 zero-candidate smoke

With `bootstrap_names.py` reporting 0 candidates:

```
language_smoke_report.py --no-names-confirmed --low-name-density-confirmed --no-particles-confirmed
```

- OMIT `--checked-names`.
- Pass `--particle-config he.json`.
- Run it from `durable_root`, or pass an absolute `--profile` (it defaults to a cwd-relative profile path).

→ `pass:true`.

## Honest framing (do not over-claim)

`pass:true` proves only "detector found zero candidates AND the operator acknowledged it" — NOT "the passage has no names." Uncased-script proper names (e.g. `הבעש״ט` = the Baal Shem Tov) are simply INVISIBLE to the extractor, so the name-canon / glossary pass is skipped (`glossary_batch_plan.py` → `{"no_new_candidates":true}`). Report name-canon as a real limitation of the run, not a clean name dimension.

## Getting uncased names via `name_inventory` (the `.local.json` route)

The zero-name path above ACCEPTS blindness. To actually surface uncased proper names, give the run a candidate source: `bootstrap_names.py`'s `is_upper_initial` gate keys on ASCII `A-Z` or Unicode category `Lu`, and Hebrew/Yiddish/Arabic letters are category `Lo`, so the capitalization pass is structurally dead — the ONLY uncased candidate route is the optional `name_inventory` (a project-local, exact-form allowlist).

- **Ship `name_inventory` in a `he.local.json`, NOT the shipped `he.json` — the `.local.json` suffix is LOAD-BEARING.** Step 0a re-copies every shipped `languages/` filename into `durable_root` with UNCONDITIONAL OVERWRITE (SKILL.md §0a), so any `name_inventory` you add to `he.json` in the durable root is silently reverted on the next scaffold. Only a DIFFERENTLY-named file survives — the plugin's own documented `fr.local.json` override pattern. Point `source.language.particle_config` at the `he.local.json` bare filename; `bootstrap_names.py` resolves `name_inventory` from `${durable_root}/languages/<that literal value>`, so the profile reference is what makes the override take effect.
- **The `no_new_candidates` false-signal trap.** `glossary_batch_plan.py` emits `{"no_new_candidates": true}` for BOTH "every candidate is already in canon" (benign) and "the matcher is blind to this script" (a real miss) — identical signal, opposite meanings — and the SKILL.md glossary-skip branch assumes the benign one. So on an uncased script a skipped glossary pass is NOT evidence there are no names; without a `name_inventory` it means the detector never looked. Don't trust a zero a blind detector produced.
- **Editing Hebrew `name_inventory` forms — bypass the `Edit` tool.** `Edit`'s `old_string` can silently fail to match a Hebrew/RTL block even when copy-pasted verbatim from a preceding `Read` (and retrying with `\uXXXX`-escaped vs. literal forms does not fix it). The moment `Edit` returns "String to replace not found" on an RTL block you just Read, switch to a `python3` script — locate the span via an ASCII marker on either side (`str.index`), build the replacement with explicit `\uXXXX` escapes for every Hebrew char (never a pasted glyph), and `Path.write_text()`. Authoring Hebrew letter-by-letter trades the match risk for a typo risk — a non-final vs. final letter form (regular `מ` where a word-final `ם` is required) that a skim of the surrounding English will NOT catch. So after the script runs, extract every Hebrew span (each string containing a codepoint in U+0590–U+05FF), print the deduped list, and eyeball each for correct final-letter forms (ך/ם/ן/ף/ץ at word-end) before trusting the file.

## W3-canon-init

A zero-candidate glossary writes no `canon.json`, which makes W3a's `segpack.py` halt. Seed the canon first, then build segpacks:

```
canon_validate.py --research-mode offline --merge-batches <file containing literal []>
```

That stamps the `generation_hashes`. Then:

```
segpack.py --all --particle-config he.json --apparatus-policy omit_apparatus
```
