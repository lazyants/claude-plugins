# Depositing a converged translation into the genealogy-skills vault

Only when saving a run's output into the genealogy-skills Obsidian book-vault. Once W5 is driven by hand, the plugin's W9 assemble is moot — assemble the vault notes yourself from the verified artifacts.

## Placement

The book vault lives at `vault/reports/rabbinical/<book-slug>/translations/` (with a parallel `genealogy/`). Existing `chunk-NNN.md` files are the whole-book RU translation (`type: translation`, `pdf_pages:[a,b]`, ~5 pp/chunk). A partial EN sample is a SEPARATE artifact — new filename, with `target_lang: en` + `partial: true` + `covers_chunk` — never overwrite a chunk.

## Generate the note, don't hand-transcribe

Build the note programmatically from the verified artifacts (hand-copying RTL Hebrew/Yiddish invites glyph/ordering errors):
- EN body from `<seg>.draft.json` `blocks{block_id: text}`
- Hebrew from `segpack_<seg>.json` `blocks[].plain_text` (same ids)
- verdict from `<seg>.review.json`

Mirror the folder's existing frontmatter house-style verbatim and add provenance (`produced_by`, `engine`, `review_status`, `draft_sha1`).

## Freshness before trust

Run `draft_sha1.py <seg>` and assert it equals the clean review's `draft_sha1`. That canonical hash is NOT a raw `shasum` of the file (different inputs — canonical draft repr vs raw bytes); don't compare across the two and conclude "drift."

## Frontmatter is house-style, not a hard gate

`validate_frontmatter.py` gates only `{report, revision-family, synthesis}` — `type: translation` is SKIPPED (counted, 0 errors). So a translation note's frontmatter is house-style, not enforced.

## `validate_links.py` `./`-strip trap (cost 2 fix rounds)

A link target that is slash-less OR starts with `./` is resolved as a repo-wide BASENAME lookup → `chunk-003.md` / `./chunk-003.md` match ~150 books' `chunk-003.md` → AMBIGUOUS. Cause: `normalise_target` strips a leading `./` (`vault/scripts/validate_links.py:114`) BEFORE the pathful test (`:118`, `"/" in target or startswith("..")`).

Fix: a same-dir sibling link must contain a slash AND not start with `./` → write `../translations/chunk-003.md` (resolves via `source.parent`, unique). PDFs/externals are skipped. The validator is not in CI (deliberate — a backlog of pre-existing broken links), but don't ADD new ones.
