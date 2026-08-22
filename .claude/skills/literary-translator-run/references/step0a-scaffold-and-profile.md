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

## 4. The language preset — author one ONLY if the language has none shipped

**`he.json` DOES ship** (since 1.9.0, `6fb80ba`/#195) alongside `fr`/`de`/`es`/`it` — do NOT author or hand-edit it. Its `STOPWORDS` is a curated 40-word list and Step 0a **unconditionally overwrites** every shipped `languages/` filename in `durable_root`, so an edit in place is silently reverted on the next scaffold (and authoring an empty stub would destroy the curated preset for that run). To add a `name_inventory` or otherwise override, ship a **`he.local.json`** — the `.local` suffix is load-bearing; see "Getting uncased names via `name_inventory`" in `uncased-script-and-w3.md`. Only a language with NO shipped preset needs authoring from scratch; the contract for every key is in `{{PLUGIN_ROOT}}/assets/languages/README.md`.

## 5. Write and validate the profile

The profile lives at `${durable_root}/.claude/literary-translator/profile.yml` — Step 0's `--profile` path is THAT, **not** `${durable_root}/profile.yml`.

he→en values:
- `source.format: gutenberg_epub`
- `adapter_config.gutenberg_epub.spine_overrides: {"content.xhtml":"body"}`
- replace the inert `plain_text` `CHOOSE_` sentinels
- `verse_policy.mode: literal_only`
- `apparatus_policy: omit_apparatus`
- `glossary.research_mode: offline`
- `engine.effort: high` — schema-const (leave as-is), so there is nothing to override here. (This is the plugin's engine field; it is a DIFFERENT knob from the codex-dispatch prompt's `Effort:` line — see `manual-translation-drive.md`. Do not read this const as a validated tier — no effort tier has been validated as a winner; see `skill:codex-runtime-driving` → `references/model-effort-bakeoff.md`.)
- `output.v1_scope: assembled_book` + `output.target: obsidian`
- `max_segment_words: 6000`

Run Step 0:

```
python3 {{PLUGIN_ROOT}}/assets/scripts/profile_validate.py --profile <abs profile>
```

## 6. If you hand-build a managed-dir verifier: policy differs by dir, not byte-exactness

A hand-built verifier that checks the scaffolded managed dirs (`scripts/`, `schemas/`, `languages/`)
against the shipped originals should NOT enforce a blanket byte-exact / no-extra-files rule for
every dir. The right axis is whether SKILL.md contemplates operator-authored content in that dir:
`languages/` legitimately holds an operator's `*.local.json` override (e.g. `he.local.json`), so an
extra file there should be `info`-only; `scripts/` and `schemas/` are never operator-authored, so an
extra or stale file there is real drift and must `fail`. Note that an extra/stale file sitting in
`scripts/` provably CANNOT move `plugin_bundle_hash` — `PLUGIN_BUNDLE_MEMBERS` is a literal
fixed-name allowlist — so an upstream-deleted module can keep sitting in `scripts/`, stay
importable, and never show up as a hash mismatch; only an explicit extra-file check catches it.

## The durable copy is the executable, not the plugin cache

A literary-translator project executes `${durable_root}/scripts/*.py` — Step 0a (above) is what
copies the plugin's `assets/scripts/` into that directory. **Between a plugin upgrade and the next
Step 0a, a root runs the OLD code**, no matter what the marketplace, the profile cache, or
`plugin list` says.

Measured 2026-08-09 on `ssk-he-en/vol2/run` right after 1.21.0 shipped and all four profiles were
refreshed and content-verified:

    durable scripts/select_segments.py  sha1 7064cf59…  --from-converged: 0   (== the 1.19.0 cache copy)
    cache 1.21.0 select_segments.py     sha1 58499622…  --from-converged: 26
    durable scripts/claim_record.py     ABSENT          (a member BOTH bundles now require)

**When answering "can this project do X" for anything plugin-shipped, resolve and hash the file
the project would actually execute (`${durable_root}/scripts/<name>.py`) and grep IT for the
capability.** Never the plugin cache, and never the version string — a root can sit several
releases behind the installed plugin indefinitely, which is a supported state, not a fault.

"1.21.0 is installed" and "the new profile is available to project P" are different claims — the
first is about profiles, the second is per-root and requires re-running Step 0a.
