# Step 0a scaffold + profile (hand-built)

Step 0a has NO executable scaffold script — SKILL.md §0a is prose only. A real operator cannot scaffold the run from the shipped artifacts alone; build the `durable_root` by hand, then write and validate the profile.

## 1. Hand-build the durable_root

Create the ~11 managed dirs:

```
segments/  glossary/  glossary/runs/  verses/  runs/  runs/ledger.d/  runs/workflows/  scripts/  languages/  schemas/  out/
```

Then:
- Copy every `assets/scripts/*.py` **EXCEPT the 3 plugin-only gates** (`profile_validate.py`, `validate_extraction.py`, `glossary_preflight.py`) into `scripts/`.
- Copy the 2 `*-wf.template.js` into `scripts/`.
- Copy the shipped `languages/` and `schemas/` in.
- Seed the 7 templates: `PLAN`, `style_bible`, `consistency_issues`, `extract.py`, `translate_TASK`, `review_TASK`, `glossary_TASK`.
- Write the root marker `.literary-translator-root.json` and a per-dir `.literary-translator-managed` in each managed dir.

## 2. Write the two bundle-hash markers (nothing ships to do this)

`cache_key.py` is read-only ("never writes"); `compute_plugin_bundle_hash` only READS `runs/.plugin_bundle_hash`. Step 0a is *supposed* to write it but no shipped script does. Compute it by REUSING cache_key's own helpers:

```
sha1_hex(concat_sorted_bytes([scripts/<m> for m in PLUGIN_BUNDLE_MEMBERS]))  →  runs/.plugin_bundle_hash
```

Do the same over the **4 orchestration members** — `draft_ready.py, ledger_merge.py, language_smoke_report.py, select_segments.py` — → `runs/.orchestration_bundle_hash`. Those 4 members are enumerated ONLY in `references/ledger-and-resumability.md` prose (~L491), not in code.

- The plugin hash IS cache-key member 15 (gating).
- The orchestration hash is non-gating for convergence — it only affects resume.

## 3. durable_root must be a real path

`profile_validate` rejects `durable_root` under `/tmp` or a scratchpad dir. Use a real path, e.g. `~/lazy-ants/development/<slug>-run`. (It must ALSO sit inside the codex agent's writable workspace — see `manual-translation-drive.md`; the plugin's house style is `durable_root == the session's project root`.)

## 4. Author the language preset

No `he.json` (or any Hebrew) preset ships — only `fr`/`de`/`es`/`it`. Author `languages/he.json` as part of scaffolding so Step 0a does not halt on a missing preset later. The contract and rationale are in `uncased-script-and-w3.md`.

## 5. Write and validate the profile

The profile lives at `${durable_root}/.claude/literary-translator/profile.yml` — Step 0's `--profile` path is THAT, **not** `${durable_root}/profile.yml`.

he→en values:
- `source.format: gutenberg_epub`
- `adapter_config.gutenberg_epub.spine_overrides: {"content.xhtml":"body"}`
- replace the inert `plain_text` `CHOOSE_` sentinels
- `verse_policy.mode: literal_only`
- `apparatus_policy: omit_apparatus`
- `glossary.research_mode: offline`
- `engine.effort: high` — schema-const (leave as-is); the config model at high was the model×effort bake-off winner, so no override needed here. (This is the plugin's engine field; it is a DIFFERENT knob from the codex-dispatch prompt's `Effort:` line — see `manual-translation-drive.md`.)
- `output.v1_scope: assembled_book` + `output.target: obsidian`
- `max_segment_words: 6000`

Run Step 0:

```
python3 <PLUGIN_ROOT>/assets/scripts/profile_validate.py --profile <abs profile>
```
