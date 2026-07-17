---
name: literary-translator-ops
description: Engineering conventions for working ON the literary-translator plugin (in the claude-plugins repo at plugins/literary-translator) — use when modifying its Python scripts or JSON schemas, editing canon.json or reasoning about its 1:1 name-dictionary data model, changing any hashed file and needing the re-translation / resume / render-baseline migration cost, enriching canon without triggering mass re-translation, matching the script/test/docs house style, or porting or adjusting the canon_adjudication_audit gate.
---

# literary-translator plugin — engineering conventions

The plugin lives in THIS repo (`claude-plugins`) at `plugins/literary-translator/`. Scripts under
`skills/literary-translator/assets/scripts/`, JSON schemas under `.../assets/schemas/`, references
under `.../references/`, tests under `tests/` (plugin root). Registered in the repo `marketplace.json`;
all changes ride the claude-plugins PR / review-bot flow. The plugin was generalized from the real,
proven `historiettes-t3` project — many scripts carry a `generalized from the real, proven
historiettes-t3/<x>.py` docstring line, and that source repo is ground truth.

Two rules dominate every edit here:
- **The iron rule** — scripts surface candidates and enforce schemas; they NEVER make an
  accuracy/identity call. That is codex's job, never a script's, never Claude's (see plugin-facts.md).
- **Every hashed file has a migration cost** — before writing "zero migration" anywhere, price the
  edit against the five hash surfaces (see hash-migration-impact.md).

## References

- **references/plugin-facts.md** — read before writing or editing any script, schema, or test: the
  `canon.json` data model, the iron rule, the script house style (self-anchored paths, one-JSON-line
  stdout, exit 0/1/2), the pytest test conventions and subprocess pattern, and the docs/registration
  surfaces to touch when adding a script.
- **references/hash-migration-impact.md** — read before editing ANY schema or script, or before
  editing `canon.json` content: the five hash surfaces (cache_key composite / resume digest /
  render_version / migration-inert / canon-DATA `used_terms_hash`), their very different blast radii,
  the sidecar rule for enriching canon without re-translating, and the derivation-regen dead-end that
  BRICKS mature projects.
- **references/canon-adjudication-audit.md** — read when porting or adjusting the
  `canon_adjudication_audit.py` gate: the 4 human-adjudication categories mapped onto canon.json's
  entity-less model, the key / fatal / blocking design, and the reusable spec-port methodology.
