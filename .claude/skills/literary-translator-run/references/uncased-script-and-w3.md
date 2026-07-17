# Uncased-script onboarding + W3 zero-candidate

## Why name-canon is effectively OFF on uncased scripts

`bootstrap_names.py` keys the capitalization gate on Unicode category **`Lu`** (uppercase). Hebrew (and Yiddish/Arabic) letters are category **`Lo`**, so it finds **`n_candidates: 0`** every time. (The output field is `n_candidates`, NOT `candidate_names_total`.)

The plugin anticipates this: the W3 mandatory smoke test has a zero-candidate branch. But be honest about what a green result means — see below.

## Author `languages/he.json`

Four-key contract (empty lists are accepted):

```json
{"PARTICLES":[],"STOPWORDS":[],"has_elision":false,"ELISION_RE":null}
```

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

## W3-canon-init

A zero-candidate glossary writes no `canon.json`, which makes W3a's `segpack.py` halt. Seed the canon first, then build segpacks:

```
canon_validate.py --research-mode offline --merge-batches <file containing literal []>
```

That stamps the `generation_hashes`. Then:

```
segpack.py --all --particle-config he.json --apparatus-policy omit_apparatus
```
