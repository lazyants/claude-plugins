# Step 0a scaffold (hand-built) + profile (created by Step 0, never by hand)

Step 0a has NO executable scaffold script — SKILL.md §0a is prose only. A real operator cannot scaffold the run from the shipped artifacts alone; build the `durable_root` by hand, then let Step 0 create the profile and answer the questionnaire it prints. The `durable_root` is hand-built because nothing ships to build it; `profile.yml` is **not**, because something does — see §5.

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

### Ownership check reference: `tests/durable_root_reachability.test.py`

No shipped script implements Step 0a's ownership/adoption check — the SKILL.md prose above is the
only spec. `tests/durable_root_reachability.test.py` (plugin root) is the repo's **reference
implementation** of that check, because nothing else in the plugin does it: read it before writing or
debugging any hand-built ownership/adoption logic.

- **A wholesale `cp -r` of an existing `durable_root` brings `.literary-translator-root.json` along**,
  which trips the root-marker fatal FIRST (the copy looks like it already owns a different run).
- **A hand-picked copy that leaves the marker behind brings no marker at all** and lands on the test's
  case 3, the adoption prompt — which `project.durable_root_adopt_existing: true` waves through
  **without inspecting the directory's contents**. Setting that flag to get past a false-positive
  ownership block also disables the real check for a genuine collision.

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

## 5. Let Step 0 CREATE the profile, then answer its questionnaire

**Do not write `profile.yml` by hand, and do not answer a fresh copy's sentinels from its own inline comments.** Everything else in this reference is hand-built because no scaffold script ships; the profile is the one artifact that has a shipped creator, and using it is load-bearing rather than a convenience. Run Step 0 against the ABSENT path first: `profile_validate.py` copies `assets/profile.example.yml` there and, in that same run, prints every `CHOOSE_` sentinel as the intake questionnaire (#751). Those sentinels ARE the questions — a hand-authored profile has none, so the scan finds nothing to ask and Step 0 prints `OK` while every intake decision was made by the orchestrator instead of the user. That is how 12 of 12 profiles across both live books came to omit `glossary.enabled`, whose default is `true`: nobody was ever shown the question.

So: relay the printed questionnaire to the user intact, get their answers, and write those in. R10 applies here too — for volume N>1, read `<series directory>/decisions.md` first and attach each recorded row to its question as provenance, never as a value written in unasked.

The profile lives at `${durable_root}/.claude/literary-translator/profile.yml` — Step 0's `--profile` path is THAT, **not** `${durable_root}/profile.yml`. Point it at the absent path and it is created there.

**Fields you fill from the material, not from the user** — these are not intake decisions, they describe the source's shape:
- `source.format: gutenberg_epub`
- `adapter_config.gutenberg_epub.spine_overrides: {"content.xhtml":"body"}`
- replace the inert `plain_text` `CHOOSE_` sentinels
- `max_segment_words: 6000`
- `engine.effort: high` — schema-const (leave as-is), so there is nothing to override here. (This is the plugin's engine field; it is a DIFFERENT knob from the codex-dispatch prompt's `Effort:` line — see `manual-translation-drive.md`. Do not read this const as a validated tier — no effort tier has been validated as a winner; see `skill:codex-runtime-driving` → `references/model-effort-bakeoff.md`.)

**Fields that come only from the relayed questionnaire.** The values below are what the **ssk he→en** book answered — they are that book's answers, recorded so you can recognise the questions, and R10 forbids carrying any of them into a new volume's profile unasked:
- `verse_policy.mode: literal_only`
- `footnotes.apparatus_policy: omit_apparatus`
- `glossary.research_mode: offline`
- `glossary.enabled` — **ask it explicitly.** It is the one intake decision that is schema-OPTIONAL and carries `default: true`, so a profile that simply omits it turns the whole W3 glossary/canon pass on with nobody having chosen that. Every profile on this machine omits it.
- `output.v1_scope: assembled_book` + `output.target: obsidian`

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

## Checking sentinel coverage on a live book — traps and a no-write rule

**The sentinel filename is `.ever_converged.{seg}` — DOT-PREFIXED.** A glob written as
`*.ever_converged` (missing the leading dot, or matching the wrong end of the name) returns zero
hits on a fully-covered root and prints **exactly** what a genuinely empty, unprotected root prints —
a false-negative that is indistinguishable from the true-negative case without reading the code. Get
the exact name from `ever_converged_path()` itself, never from memory or from a prior session's
recollection. Segment directories also differ per book, so a coverage check must resolve this
per-root, not assume one layout: tome1 is `<root>/segments`, vol2 is `<root>/run/segments`.

**Checking a stale durable copy makes the false-negative worse, and it is a LIVE hazard, not a closed
one — `ssk-he-en/vol2`'s durable `scripts/` is still the 1.19.0 copy**, which classifies the sentinel
with a bare `Path.exists()` (verified at that copy's own line 519). Its three failure modes point in
**different** directions, and only one of the three looks like a healthy run — do not compress them
into one "`exists()` is unreliable" line, the value here is that they disagree:

- **(a) A directory sitting at the marker path** → `exists()` returns True → the segment is counted
  `already_sentineled`, `missing_sentinels` comes out EMPTY, and there is no real protection. **This
  is the one that reads as a clean run** — the most dangerous of the three because nothing about the
  output looks wrong.
- **(b) An unreadable marker path** (`EACCES`/`ESTALE`/`EIO`) → since Python 3.13, `Path.exists()`
  swallows every `OSError` and returns False → the segment lands in `missing_sentinels`, which comes
  out NON-empty — the opposite symptom from (a), for a filesystem condition that has nothing to do
  with whether the segment is actually protected.
- **(c) A dangling symlink at the marker path** → `exists()` returns False (matching case b's
  read), while the writer's own `os.open(O_CREAT|O_EXCL)` gets `EEXIST` and reports the segment
  already marked → the ledger records the segment as converged and protected, the coverage check
  reports it unprotected, and a re-run silently retranslates it.

**Do not run `select_segments.py --classify-only` or `final_audit.py` against a live book to check its
state.** Both re-materialize `runs/ledger.json`, which counts as a WRITE to the project you are only
trying to inspect. The memory that recorded this rule cited it to an index file that does not actually
contain it — this section is the rule's real home. Relatedly,
a `not_evaluated` result from any of these checks is **not** a pass; the script's own output says so.
The six `not_evaluated` cases found on the live books were closed only by hand inventory, not by
re-running a script to get a cleaner-looking verdict.
