# Fixing a stale `file.ext:NNN` code citation — two traps in the obvious fix

This plugin's docs cite real code lines by `file.ext:NNN` (e.g. `chapter-paths.mjs:174-178`,
`capture-guard-policy.mjs:271`, `SKILL.md:100` — 6 such citations across `SKILL.md` and
`references/**/*.md` as of 2026-08). Editing any cited file pushes every line below the insertion
down, so a citation elsewhere silently goes stale. Two traps sit in the obvious fix.

**1. The verification proves the wrong property.** After renumbering a batch of citations, checking
that "the line this citation now names holds the same text it named before" passes for every
citation — and it is exactly the property that carries an ALREADY-WRONG citation through unchanged.
Measured on one batch: three citations were wrong *before* the renumbering pass (two ~500 lines off,
one ~2 400), and each passed the re-verification *because* both the old and the new line happened to
hold the same wrong text. A citation "corrected" this way is worse than one left alone: it now reads
as re-verified.

**2. Re-running a drift scan on the already-fixed tree double-applies its own offsets.** An
offset-map approach (`base line → current line`) assumes every citation in the file is still
pre-edit. Once a batch has been renumbered, re-running the same scan maps the NEW number forward
again and reports false drift. For a second round of edits, the baseline must be the state the
citations were last resolved against (the INDEX), never the original base commit.

**No mechanical check closes correctness — only a human/model reading each citation against its own
sentence does.** Four measured reasons a purely mechanical check (grep the target line, diff the
cited text, confirm the symbol exists nearby) still misses real drift: a context window pairs the
citation with an anchor from a neighbouring clause; a bare identifier reads as a prose noun as often
as it reads as code; prose names a function `foo()` while the file defines `def foo(args)`; and a
citation may legitimately name a line *inside* a function whose name the surrounding sentence uses.
The well-posed form — anchor immediately adjacent, statement-shaped, unique in the target file —
decides only a small minority of citations mechanically. The complete check is reading each citation
against its own sentence, which is a judgment call, not a script.

**`tools/citation_audit.py` now enumerates and gates this repo's whole `file.ext:NNN` corpus** (its
ordered-anchor design is the one that survived `changelog_citations.test.py`'s two defeated weaker
designs, referenced above). It re-checks a declared anchor set on every run, so a citation that was
correct when adjudicated cannot silently rot again — it does NOT establish that a citation is correct
in the first place; that adjudication is still the judgment call described above.

**Historical CHANGELOG entries are a deliberate exception.** `reference-assets.test.sh` treats
`CHANGELOG.md` entries as frozen prose describing what was true at release time (see the
`CLASS_SENTENCE_1_11_0` pin and the surrounding comments around `CHLOG="$PLUGIN_DIR/../../CHANGELOG.md"`)
— do not "fix" a changelog entry's citation to match the current tree; it is not out of date, it is
dated. When stating how many citations are correct after a renumbering pass, state the count as
measured on the final tree, not the count planned going in — the true count can move during the pass
itself as fixes land.

Related: `SKILL.md` §11 (`citation-audit-lib.mjs`'s own citation matcher — a different citation
shape: "above"/"below" *direction* claims against quoted section headings, not `file:NNN` code
citations; the two matchers solve adjacent but distinct problems in this same doc set).
