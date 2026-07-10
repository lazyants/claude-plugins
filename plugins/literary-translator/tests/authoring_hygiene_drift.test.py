"""tests/authoring_hygiene_drift.test.py

Targets a specific authoring-hygiene defect class (issue #77): a shipped
script's docstring telling an installed reader to consult a NON-shipped
file for ground truth. This plugin's scripts legitimately attribute
provenance to the private `historiettes-t3` origin project ("Generalized
from historiettes-t3's ..."), and that attribution is fine to ship -- but
a handful of docstrings went one step further and told the reader to
"read it directly before changing this one", pointing at a
`historiettes-t3/...` path that does not exist in an installed copy of
this plugin. An installed user has no way to follow that instruction.

`canon_adjudication_audit.py` and `final_audit.py` both carried this
banned directive (fixed for #77, redirected to in-repo authorities:
`references/canon-and-glossary.md` and SKILL.md's "W7 Final audit" section
/ `assets/schemas/final-audit-summary.schema.json`, respectively).

This guard scans EVERY shipped Python script under `assets/scripts/` and
asserts none of them reintroduces the banned "read it directly before
changing this one" directive -- an "unreachable ground truth" pointer at
a non-shipped origin. It does NOT ban `historiettes-t3`
mentions outright (plain provenance attribution, like
`profile_validate.py`'s own pointer at the SHIPPED
`assets/profile.example.yml`, is legitimate and must keep working).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

SHIPPED_SCRIPTS = sorted(SCRIPTS_DIR.glob("*.py"))

# The banned phrase itself: an instruction to read something "directly"
# before changing "this one" (case-insensitive -- the exact shipped wording
# was "read it directly before changing this one", but a rewording that
# still tells the reader to go read "this file" or "that file" directly is
# just as unreachable for an installed user). The inter-word separator is
# `[\s#]+`, not just `\s+`, so the match survives a hard-wrapped `#` comment
# where the phrase continues on a following comment line (the `\n# ` between
# words is otherwise non-whitespace at the `#` and would break a plain
# `\s+` join).
READ_DIRECTLY_PHRASE = re.compile(
    r"read[\s#]+it[\s#]+directly[\s#]+before[\s#]+changing[\s#]+this[\s#]+one", re.IGNORECASE
)


@pytest.mark.parametrize("script_path", SHIPPED_SCRIPTS, ids=lambda p: p.name)
def test_script_does_not_direct_readers_to_non_shipped_origin_for_ground_truth(script_path):
    source = script_path.read_text(encoding="utf-8")

    assert not READ_DIRECTLY_PHRASE.search(source), (
        f"{script_path.name} still tells readers to \"read it directly before "
        "changing this one\" -- that instruction is unreachable for an "
        "installed user if it points at a non-shipped historiettes-t3 path. "
        "Redirect to an in-repo authority instead (a references/*.md doc, "
        "SKILL.md section, or a shipped schema)."
    )


def test_shipped_scripts_directory_is_non_empty():
    """Guards against a silently-empty glob (e.g. a path typo) making every
    parametrized test above vacuously pass."""
    assert len(SHIPPED_SCRIPTS) > 10, (
        f"expected many shipped scripts under {SCRIPTS_DIR}, found "
        f"{len(SHIPPED_SCRIPTS)} -- SCRIPTS_DIR may be wrong"
    )


def test_banned_phrase_regex_actually_matches_the_original_pre_fix_wording():
    """Proves READ_DIRECTLY_PHRASE isn't accidentally too narrow to have ever
    caught the real pre-fix docstring text (both #77 originals used this
    exact phrasing)."""
    assert READ_DIRECTLY_PHRASE.search(
        "ground truth for the methodology below; read it directly before "
        "changing this one"
    )
    assert READ_DIRECTLY_PHRASE.search(
        "that file is ground truth for the checks below; read it directly "
        "before changing this one"
    )


def test_banned_phrase_regex_catches_a_hard_wrapped_comment_reintroduction():
    """Proves the `[\\s#]+` join isn't just cosmetic: a two-line `#` comment
    that wraps mid-phrase (the `before`/`changing` boundary lands right at
    the line break) must still be caught. Before this fix, `\\s+` could not
    cross the `\\n# ` comment-continuation marker (`#` is non-whitespace),
    so a reintroduction split across comment lines would pass this guard
    silently."""
    assert READ_DIRECTLY_PHRASE.search(
        "# See historiettes-t3/foo.py -- read it directly before\n"
        "# changing this one."
    )


def test_historiettes_t3_provenance_attribution_remains_legitimate_and_unbanned():
    """The fix must not have overcorrected into banning `historiettes-t3`
    mentions outright -- plain provenance attribution ("Generalized from
    historiettes-t3's ...") is legitimate and several shipped scripts
    still carry it. Confirm at least one such attribution survives."""
    mentions = [
        path for path in SHIPPED_SCRIPTS if "historiettes-t3" in path.read_text(encoding="utf-8")
    ]
    assert mentions, (
        "expected at least one shipped script to still attribute provenance "
        "to historiettes-t3 -- if none do, the fix may have stripped "
        "legitimate attribution along with the banned directive"
    )


# --------------------------------------------------------------------------
# Doc-prose corpus (issue #103): the SAME "unreachable ground truth" defect
# class as #77 above, but recurring in reference-doc PROSE rather than script
# docstrings. Two `references/*.md` paragraphs told the reader to "read
# `historiettes-t3/...` directly" -- a non-shipped origin path an installed
# user cannot reach. The #77 guard above only scans `.py` scripts and matches
# one exact verbatim phrase, so it never covered the docs (whose wording
# differs every time). This is a SECOND, independent guard over the docs
# corpus, keyed not on a fixed phrase but on the co-occurrence -- within a
# single paragraph -- of a `historiettes-t3` mention and a near-adjacent
# read/directly imperative. Plain provenance attribution
# (`engine-loop.md:11`'s bare parenthetical) stays legitimate.
# --------------------------------------------------------------------------

REFERENCES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "references"
REFERENCE_DOCS = sorted(REFERENCES_DIR.rglob("*.md"))

# "read"/"reading" and "directly" within <=4 words of each other, in either
# order. The proximity window is deliberately tight: it must catch the two
# banned paragraphs ("read that file directly", "reading `build_epub.py`
# directly") while NOT flagging a paragraph where "directly" and "read" happen
# to co-occur but refer to different things several words apart -- e.g.
# `source-format-adapters/gutenberg-epub.md`'s "generalized directly from
# `historiettes-t3`'s real, executed `extract.py` -- read verbatim in this
# file" (~5 words between "directly" and "read", pointing at the SHIPPED doc
# itself, not the origin path). The window naturally spans hard-wrapped lines
# because `\W` (in the `\W+\w+` word-gap unit) matches `\n`.
READ_DIRECTLY_PROXIMITY_RE = re.compile(
    r"\b(?:read|reading)\b(?:\W+\w+){0,4}\W+directly\b"
    r"|\bdirectly\b(?:\W+\w+){0,4}\W+(?:read|reading)\b",
    re.IGNORECASE,
)

# An inline-code span (`` `...` ``) collapsed to a single placeholder token
# before the proximity regex runs. Without this, a path like
# `historiettes-t3/reference/foo.reference.js` counts as MANY `\w+` slots
# (one per slash/dot/dash-separated segment) under the word-distance model
# above, so a plausible rephrasing like "Read `<long path>` directly" -- the
# object sitting entirely between the verb and the adverb -- would slip past
# the <=4-word window even though a human reader sees "read ... directly" as
# immediately adjacent. Collapsing to one token restores that intuition.
_CODE_SPAN_RE = re.compile(r"`[^`]*`")


def _collapse_code_spans(text):
    return _CODE_SPAN_RE.sub("CODEREF", text)


def _paragraphs(text):
    """Split on blank lines -- the proximity check is scoped per paragraph so a
    `historiettes-t3` mention in one paragraph and a stray 'read ... directly'
    in a distant, unrelated paragraph don't combine into a false positive."""
    return re.split(r"\n\s*\n", text)


def _paragraphs_directing_to_read_historiettes_t3_directly(text):
    return [
        paragraph
        for paragraph in _paragraphs(text)
        if "historiettes-t3" in paragraph.lower()
        and READ_DIRECTLY_PROXIMITY_RE.search(_collapse_code_spans(paragraph))
    ]


def test_reference_docs_directory_is_non_empty():
    """Guards against a silently-empty glob (e.g. a renamed/moved references
    dir) making every parametrized doc-prose test below vacuously pass."""
    assert len(REFERENCE_DOCS) > 5, (
        f"expected many reference docs under {REFERENCES_DIR}, found "
        f"{len(REFERENCE_DOCS)} -- REFERENCES_DIR may be wrong"
    )


@pytest.mark.parametrize(
    "doc_path", REFERENCE_DOCS, ids=lambda p: str(p.relative_to(REFERENCES_DIR))
)
def test_no_reference_doc_directs_readers_to_read_a_non_shipped_historiettes_t3_path_directly(
    doc_path,
):
    text = doc_path.read_text(encoding="utf-8")

    offending = _paragraphs_directing_to_read_historiettes_t3_directly(text)
    assert not offending, (
        f"{doc_path.relative_to(REFERENCES_DIR)} contains a paragraph that both "
        "mentions historiettes-t3 and tells the reader to read/consult it "
        '"directly" -- that instruction is unreachable for an installed user '
        "if it points at a non-shipped historiettes-t3 path. State the "
        "provenance as attribution only (see engine-loop.md), or redirect to "
        "an in-repo authority. Offending paragraph(s):\n\n"
        + "\n\n---\n\n".join(offending)
    )


def test_proximity_detector_matches_the_original_pre_fix_doc_paragraphs():
    """Red-before-green proof: reproduces (verbatim, hard-wrapped exactly as
    they shipped) the two ORIGINAL pre-fix paragraphs #103 removed, and asserts
    the detector DOES flag them. Without this, a detector too narrow to have
    ever caught the real bug could sit green forever and give false comfort.
    The originals are hardcoded here rather than read from git history so the
    proof survives independent of the repo's future state."""
    orchestration_original = (
        "`mass-translate-wf.template.js` is generalized from the real, proven\n"
        "`historiettes-t3/reference/historiettes-mass-translate-wf.reference.js` —\n"
        "read that file directly for ground truth on structure. These properties are\n"
        "preserved exactly because they are precisely what made the original reliable:"
    )
    assembly_original = (
        "A future `epub` output-target effort should start by reading `build_epub.py`\n"
        "directly — `historiettes-t3/build_epub.py` in the (non-shipped) in-house\n"
        "provenance project — and verifying its real behavior firsthand, if you have\n"
        "access to that project — not by guessing at its shape from this reference or\n"
        "from the plan that preceded this plugin."
    )

    assert _paragraphs_directing_to_read_historiettes_t3_directly(orchestration_original), (
        "detector failed to flag the original orchestration-and-batching.md "
        "pre-fix paragraph -- it is too narrow to have caught the real #103 bug"
    )
    assert _paragraphs_directing_to_read_historiettes_t3_directly(assembly_original), (
        "detector failed to flag the original assembly-and-output.md pre-fix "
        "paragraph -- it is too narrow to have caught the real #103 bug"
    )


def test_proximity_detector_catches_verb_object_adverb_phrasing_with_a_long_path():
    """A plausible future rephrasing of the same #103 defect: the verb
    immediately precedes a long, slash/dot-separated inline-code path, which
    itself immediately precedes 'directly' -- e.g. "Read `<path>` directly".
    Without collapsing the code span to one token, each path segment eats
    its own word-distance slot and the match falls outside the <=4-word
    window even though a human reads this as "read ... directly" adjacent."""
    text = (
        "Read `historiettes-t3/reference/historiettes-mass-translate-wf.reference.js` "
        "directly for ground truth on structure."
    )
    assert _paragraphs_directing_to_read_historiettes_t3_directly(text)


def test_proximity_detector_catches_a_recased_historiettes_t3_mention():
    """The `historiettes-t3` containment check must not be case-sensitive --
    a doc could plausibly capitalize it sentence-initially."""
    text = "Historiettes-t3 -- read that file directly for ground truth on structure."
    assert _paragraphs_directing_to_read_historiettes_t3_directly(text)


def test_proximity_detector_does_not_flag_the_gutenberg_epub_provenance_paragraph():
    """False-positive guard: the real, current `gutenberg-epub.md` provenance
    block (which `_paragraphs` sees as ONE paragraph -- its bullets have no
    blank lines between them) mentions "historiettes-t3" several times and
    also contains "directly", "read", and "read verbatim" -- but every
    read/directly pair is >4 words apart and refers to reading the SHIPPED doc
    itself, not a non-shipped origin path. If the proximity window were too
    loose (e.g. co-occur-anywhere-in-paragraph), this legitimate paragraph
    would be wrongly banned. Hardcoded byte-verbatim from the live file (the
    full multi-bullet paragraph, exactly as the splitter segments it) so a
    future widening of the regex trips this test."""
    gutenberg_paragraph = (
        "- **Proven**: the spine-classification heuristic, the custom block-boundary\n"
        "  text extractor, the footnote anchor↔definition bijection, the\n"
        "  cross-notes-file footnote grouping, and the verse-container detection\n"
        "  below are generalized directly from `historiettes-t3`'s real, executed\n"
        "  `extract.py` — read verbatim in this file where it matters — and that\n"
        "  script ran successfully, with its checks passing, against one real book\n"
        "  (`Les Historiettes de Tallemant des Réaux`, tome 3, Project Gutenberg\n"
        "  ebook 39314, 76 segments, 423 footnotes).\n"
        "- **Proven against**: *that one EPUB's* specific markup conventions — its\n"
        "  particular `x-ebookmaker-pageno` class, its particular\n"
        "  `FNanchor_N`/`Footnote_N` id convention, its particular\n"
        "  `.poetry-container`/`.stanza`/`.line` verse markup, its particular\n"
        "  Project-Gutenberg-generated front/back boilerplate. It is **not** proven\n"
        "  against Gutenberg EPUBs in general — a different Gutenberg-sourced book\n"
        "  can and often will use different id conventions, different front-matter\n"
        "  markup, or no verse markup at all. Any new project using this adapter\n"
        "  against a *different* source still needs the same design discipline this\n"
        "  file documents (classify by content, never by filename; verify the\n"
        "  bijection; never use `get_text(\" \")`), but treat every literal\n"
        "  regex/id-convention/class-name below as a **starting template to adapt**,\n"
        "  not a guarantee that it will match a different book's markup unchanged.\n"
        "- **Not proven at all — regardless of the extraction claim above**: the\n"
        "  ledger/cache-key/derivation-state machinery this plugin wraps around\n"
        "  extraction (`ledger-and-resumability.md`), and specifically the\n"
        "  `FRONTBACK:{id}` → `segments[]` → ledger → review pipeline described\n"
        "  below. Both are new for this plugin. `historiettes-t3`'s real, shipped\n"
        "  code never ran front/back matter through anything like a ledger at all —\n"
        "  see \"`FRONTBACK:{id}` handling\" below for the direct-inspection evidence.\n"
        "  Treat that specific mechanism with the same \"carefully designed, not yet\n"
        "  run at scale\" confidence as the rest of the ledger subsystem, independent\n"
        "  of this adapter's older and narrower extraction-fidelity claim."
    )

    assert not _paragraphs_directing_to_read_historiettes_t3_directly(gutenberg_paragraph), (
        "detector wrongly flagged the gutenberg-epub.md provenance paragraph -- "
        "the read/directly proximity window is too loose"
    )


def test_historiettes_t3_reference_doc_mentions_remain_legitimate_and_unbanned():
    """Mirror of the scripts-corpus test above for the docs corpus: the fix
    must not have overcorrected into stripping every `historiettes-t3` mention
    from the reference docs. Plain provenance attribution (e.g.
    engine-loop.md's bare parenthetical) is legitimate and must survive."""
    mentions = [
        path for path in REFERENCE_DOCS if "historiettes-t3" in path.read_text(encoding="utf-8")
    ]
    assert mentions, (
        "expected at least one reference doc to still attribute provenance to "
        "historiettes-t3 -- if none do, the #103 fix may have stripped "
        "legitimate attribution along with the banned directives"
    )
