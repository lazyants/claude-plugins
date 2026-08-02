---
name: literary-translator-ops
description: Engineering conventions for working ON the literary-translator plugin (in the claude-plugins repo at plugins/literary-translator) — use when modifying its Python scripts or JSON schemas, adding or widening a field on a candidate row / skeptic assignment / any structure a workflow template consumes, editing canon.json or reasoning about its 1:1 name-dictionary data model, changing any hashed file and needing the re-translation / resume / render-baseline migration cost, enriching canon without triggering mass re-translation, matching the script/test/docs house style, or porting or adjusting the canon_adjudication_audit gate.
---

# literary-translator plugin — engineering conventions

The plugin lives in THIS repo (`claude-plugins`) at `plugins/literary-translator/`. Scripts under
`skills/literary-translator/assets/scripts/`, JSON schemas under `.../assets/schemas/`, references
under `.../references/`, tests under `tests/` (plugin root). Registered in the repo `marketplace.json`;
all changes ride the claude-plugins PR / review-bot flow. The plugin was generalized from the real,
proven `historiettes-t3` project — many scripts carry a `generalized from the real, proven
historiettes-t3/<x>.py` docstring line, and that source repo is ground truth.

Three rules dominate every edit here:
- **The iron rule** — scripts surface candidates and enforce schemas; they NEVER make an
  accuracy/identity call. That is codex's job, never a script's, never Claude's (see plugin-facts.md).
- **Every hashed file has a migration cost** — before writing "zero migration" anywhere, price the
  edit against the five hash surfaces (see hash-migration-impact.md).
- **Adding a field to a candidate row or a skeptic assignment is a PROMPT decision, not a data
  decision.** Two workflow templates serialize the whole Python-produced structure into the prompt
  verbatim — `glossary-pass-wf.template.js:748` (`JSON.stringify(batch.candidates, null, 1)`) and
  `skeptic-pass-wf.template.js:408` (`...(batch.assignments, null, 1)`). Nothing on the Python side
  hints at this: the row is built in `bootstrap_names.collect_candidates()`, written to
  `name_candidates.json`, and the exposure happens in another language, in another directory, outside
  any diff that adds the field. So a new field must be bounded and injection-checked where it is
  ADDED. This is why `_capped_candidate_name()` bounds `name` in the producer at all, against this
  repo's own convention of bounding at the render site — see `bootstrap_names.py`'s "WHERE THE BOUND
  BELONGS" comment. Cost of not knowing it: a 1.16.3-era plan proposed a per-row `match_key` while
  asserting the bounded-by-default property was preserved; the field was measured at 5 200 characters
  inside that prompt, and the plan died on it.

**Wikilink resolution ground truth lives in the `enduser-handbook-ops` skill** →
`references/wikilink-resolution-ground-truth.md` — read it before changing what `render_obsidian.py`
or `validate_backlinks.py` emits or accepts as a `[[…]]` target: measured Obsidian and Quartz
resolution tiers. Vault-root-relative equals content-root-absolute (and so survives both) ONLY when
Quartz's content root IS the vault root — if content lives at a subdirectory, that form carries a
stale prefix and resolves nowhere; and under `markdownLinkResolution: relative` NO spelling resolves,
that mode needs genuinely relative links.

## References

- **references/plugin-facts.md** — read before writing or editing any script, schema, or test: the
  `canon.json` data model, the iron rule, the script house style (self-anchored paths, one-JSON-line
  stdout, exit 0/1/2), the pytest test conventions and subprocess pattern, and the docs/registration
  surfaces to touch when adding a script.
- **references/hash-migration-impact.md** — read before editing ANY schema or script, or before
  editing `canon.json` content: the five hash surfaces (cache_key composite / resume digest /
  render_version / migration-inert / canon-DATA `used_terms_hash`), their very different blast radii,
  the sidecar rule for enriching canon without re-translating, and the derivation-regen recovery path
  for a mature/zero-candidate project (`--restamp-derivation`, sanctioned since 1.15.0 — no longer a
  permanent brick).
- **references/canon-adjudication-audit.md** — read when porting or adjusting the
  `canon_adjudication_audit.py` gate: the 4 human-adjudication categories mapped onto canon.json's
  entity-less model, the key / fatal / blocking design, and the reusable spec-port methodology.
