---
name: literary-translator-run
description: Driving a real book through the literary-translator plugin as an operator, especially Hebrew, Yiddish, Arabic or another uncased/non-Latin script — onboarding a source language preset (he.local.json), hand-scaffolding Step 0a (durable_root, bundle-hash markers, profile.yml), EPUB-wrapping a plain-text/OCR/PDF source, marker-snapped segmentation, structure attestation, clearing a W3 zero-candidate name canon, driving W5 translate and W3a glossary dispatch by hand, depositing into the Obsidian book-vault, and auditing an LLM-built canon or translation via a blind-adjudicated convergence loop.
---

# Driving a real book through the literary-translator plugin

Operating the plugin on a real book by hand (not the automated `mass-translate-wf` Workflow) hits shipped-artifact gaps that force manual work at several phases — worst for uncased/non-Latin scripts (Hebrew, Yiddish, Arabic), where the name-canon path is effectively off. Steps 0–W3a of the plugin are still fully used; only Step 0a scaffolding, the source adapter, and the W5 translate dispatch need a manual replacement.

## End-to-end phase order

1. **Scaffold Step 0a + write the profile** — no scaffold script ships; hand-build the `durable_root` and its bundle-hash markers, point the profile at the right language preset (authoring one ONLY for a language with none shipped — `he.json` ships), then validate the profile. → `references/step0a-scaffold-and-profile.md`
2. **Prepare the source** — the `plain_text`/`custom` adapters are unimplemented, so extract clean text and wrap it as a minimal `gutenberg_epub`, then place chapter markers for segmentation. → `references/source-prep.md`
3. **Onboard the uncased language + pass W3** — name detection finds zero candidates on non-cased scripts; use the shipped `he.json` (override via `he.local.json`, never by editing it) and clear the zero-candidate smoke + canon-init. → `references/uncased-script-and-w3.md`
4. **Drive translation dispatch by hand** — prove the dispatch actually works with a throwaway smoke test first, then dispatch each segment with a blocking `codex-companion.mjs task --write` (`codex:codex-rescue` backgrounds and never yields a draft), transcribing the Workflow's own prompt builders verbatim. → `references/manual-translation-drive.md`
5. **(optional) Deposit the converged output** into the genealogy-skills Obsidian book-vault. → `references/vault-deposit.md`
6. **(optional) Verify the converged canon/translation** — a source-grounded, cross-model-tail, blind-adjudicated convergence loop, with the exact stop condition that keeps it from running forever. → `references/canon-verification-loop.md`

## Load-bearing invariants
- The shipped extractor is `gutenberg_epub`-ONLY — you cannot translate a `.txt`/OCR/scraped source directly through the code; wrap it as an EPUB.
- On uncased scripts a green W3 proves only "detector found zero names AND operator acknowledged it," never "the passage has no names." Report name-canon as a real limitation, not a clean dimension.
- No source-fidelity gate exists: a splitter that drops or duplicates text still passes W2 green. Gate every model-produced heading against SOURCE ATTESTATION (the marker must lead a real source block), never against in-range plausibility.
- When reproducing any of the plugin's automated orchestrators by hand, transcribe its own prompt builders verbatim and substitute only the variables — every guardrail you re-author from scratch is one you will drop.
- Prove any hand-driven dispatch (W5 translate, W3a glossary batches) with a throwaway smoke test before committing to a full run — a 1-segment smoke test lets the review step eat the only segment, so the dispatch step you meant to test never executes and the smoke goes green either way.
