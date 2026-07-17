---
name: literary-translator-run
description: How to drive a real book end-to-end through the literary-translator plugin as an operator — use when running/translating an actual book (especially Hebrew, Yiddish, Arabic, or any uncased/non-Latin script) through the plugin, onboarding a new source language, hand-scaffolding Step 0a (managed dirs + bundle-hash markers + language preset), EPUB-wrapping a plain-text/OCR/PDF source because the plain_text and custom adapters are unimplemented, choosing marker-snapped segmentation for a book without clean chapter structure, or working around W3 zero-candidate name-canon and W5 mass-translate dispatch convergence problems.
---

# Driving a real book through the literary-translator plugin

Operating the plugin on a real book by hand (not the automated `mass-translate-wf` Workflow) hits shipped-artifact gaps that force manual work at several phases — worst for uncased/non-Latin scripts (Hebrew, Yiddish, Arabic), where the name-canon path is effectively off. Steps 0–W3a of the plugin are still fully used; only Step 0a scaffolding, the source adapter, and the W5 translate dispatch need a manual replacement.

## End-to-end phase order

1. **Scaffold Step 0a + write the profile** — no scaffold script ships; hand-build the `durable_root`, its bundle-hash markers, and the language preset, then validate the profile. → `references/step0a-scaffold-and-profile.md`
2. **Prepare the source** — the `plain_text`/`custom` adapters are unimplemented, so extract clean text and wrap it as a minimal `gutenberg_epub`, then place chapter markers for segmentation. → `references/source-prep.md`
3. **Onboard the uncased language + pass W3** — name detection finds zero candidates on non-cased scripts; author the language preset and clear the zero-candidate smoke + canon-init. → `references/uncased-script-and-w3.md`
4. **Drive translation dispatch by hand** — `codex:codex-rescue` backgrounds and never yields a draft; dispatch each segment with a blocking `codex-companion.mjs task --write`, transcribing the Workflow's own prompt builders verbatim. → `references/manual-translation-drive.md`
5. **(optional) Deposit the converged output** into the genealogy-skills Obsidian book-vault. → `references/vault-deposit.md`

## Load-bearing invariants
- The shipped extractor is `gutenberg_epub`-ONLY — you cannot translate a `.txt`/OCR/scraped source directly through the code; wrap it as an EPUB.
- On uncased scripts a green W3 proves only "detector found zero names AND operator acknowledged it," never "the passage has no names." Report name-canon as a real limitation, not a clean dimension.
- No source-fidelity gate exists: a splitter that drops or duplicates text still passes W2 green. Gate every model-produced heading against SOURCE ATTESTATION (the marker must lead a real source block), never against in-range plausibility.
- When reproducing any of the plugin's automated orchestrators by hand, transcribe its own prompt builders verbatim and substitute only the variables — every guardrail you re-author from scratch is one you will drop.
