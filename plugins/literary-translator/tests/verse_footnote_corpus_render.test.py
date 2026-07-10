"""tests/verse_footnote_corpus_render.test.py -- Track D (#106): the RENDER
layer, the layer that proves the WHOLE #106 end-to-end chain.

``render_obsidian.py``'s own module docstring says, verbatim (near the
bottom of the file, right above its standalone-CLI section):

    "Standalone CLI -- a thin wrapper for manual smoke-testing. Not part of
    the assembler's real call path (assemble.py imports and calls render()
    in-process); D's tests are expected to import render()/its helpers
    directly against a hand-authored fixture NodeStream rather than shell
    out to this CLI, per the shared build contract."

"D" is this file (Track D, #106). This file does exactly that -- calls
``render()`` in-process, never shells out to the CLI -- except the
NodeStream it feeds in is NOT hand-authored: it is produced by the REAL
``assemble.py`` ``build_nodestream()`` (via the shared
``tests/verse_footnote_corpus.py`` helper's ``build_nodestream_for()``),
which itself consumed the REAL extractor's manifest and the REAL
``segpack.py`` output. That makes this file a strictly STRONGER test than a
hand-built NodeStream fixture: it exercises the full extract -> segpack ->
draft -> assemble -> render pipeline for every verse-mount x
footnote-citation-site combination in ``corpus.CASES`` (cases "a".."g"),
and asserts on the FINAL markdown a production run would actually write
into the user's Obsidian vault.

The seven cases are documented in full in ``verse_footnote_corpus.py``'s own
module docstring; the shape asserted here for each was captured by actually
running this exact chain against the real, POST-#92/#93/#96 tree (never
guessed from reading source alone) -- see that module's "Baselined against
the real, POST-#92/#93/#96 tree" section.

## The one invariant checked for every single case

No raw ``⟦FNREF_N⟧`` / ``⟦VERSE_...⟧`` sentinel may ever leak into the
rendered markdown -- every footnote citation must have resolved to a
``[^N]`` reference + a matching ``[^N]:`` definition line, and every verse
placeholder must have been substituted with its rendered content (or, for
cases (c)/(g), deliberately stripped per the Phase 0 footnote-embedded-verse
policy -- see those two tests' own docstrings). ``_render_case()`` below
enforces this centrally so no per-case test can forget it.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

CORPUS_PATH = Path(__file__).resolve().parent / "verse_footnote_corpus.py"


def _load_corpus():
    spec = importlib.util.spec_from_file_location(
        "verse_footnote_corpus_for_render_test", CORPUS_PATH
    )
    assert spec is not None and spec.loader is not None, f"could not load spec for {CORPUS_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus = _load_corpus()
# Loaded ONCE at module scope, not per-test -- render_obsidian.py is
# stateless (no DURABLE_ROOT self-anchor, takes out_dir as a parameter), per
# corpus.load_render_module()'s own docstring and render_obsidian.test.py's
# identical convention.
render_mod = corpus.load_render_module()


@pytest.fixture
def extract_mod(tmp_path):
    """A fresh extractor copy per test -- extract.py.template's own
    module-level DURABLE_ROOT self-anchors off wherever it is loaded from,
    so it must not be shared/reused across tests the way render_mod is."""
    return corpus.load_extract_module(tmp_path)


def _render_case(label: str, extract_mod, tmp_path: Path) -> str:
    """Drives corpus case `label` through the REAL extract -> segpack ->
    draft_for -> build_nodestream -> render chain, in-process, and returns
    the full rendered markdown body of the single written note (every
    corpus case is a single segment, "seg01", so exactly one note is
    written). Asserts the core #106 sentinel-leak invariant centrally so
    every per-case test inherits it for free."""
    case = corpus.CASES[label]
    manifest, report, max_words = corpus.manifest_for(case, extract_mod, tmp_path)
    pack = corpus.segpack_for("seg01", manifest, case.apparatus_policy, corpus.LANG_CONFIG)
    draft = corpus.draft_for(manifest, pack)
    nodestream, anchor_map, assemble_mod = corpus.build_nodestream_for(
        case, manifest, pack, draft, tmp_path
    )

    out_dir = tmp_path / f"out_{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_out = render_mod.render(nodestream, corpus.render_canon(), corpus.render_profile(), out_dir)

    assert manifest_out["kind"] == "vault"
    assert len(manifest_out["written"]) == 1, (
        f"case {label}: every corpus case is a SINGLE segment (\"seg01\") -> "
        f"exactly ONE written note file, got: {manifest_out['written']}"
    )
    text = (out_dir / manifest_out["written"][0]).read_text(encoding="utf-8")

    assert "⟦FNREF" not in text, (
        f"case {label}: raw ⟦FNREF_N⟧ sentinel leaked into rendered output:\n{text}"
    )
    assert "⟦VERSE" not in text, (
        f"case {label}: raw ⟦VERSE_...⟧ sentinel leaked into rendered output:\n{text}"
    )
    return text


# ===========================================================================
# (a) prose block + body footnote ref, no verse.
# ===========================================================================


def test_case_a_prose_body_footnote_no_verse(extract_mod, tmp_path):
    text = _render_case("a", extract_mod, tmp_path)
    assert "[TR] Some prose with a note[^1] attached." in text
    assert "[^1]: [TR] 1 A note about the prose." in text


# ===========================================================================
# (b) prose/quote with an EMBEDDED verse (mount=embedded); the footnote ref
#     lives in the SURROUNDING PROSE, not inside the verse. The embedded
#     verse itself renders as a compact inline italic substitution
#     (_render_verse_inline) -- no blockquote "> " prefix.
# ===========================================================================


def test_case_b_embedded_verse_footnote_in_surrounding_prose(extract_mod, tmp_path):
    text = _render_case("b", extract_mod, tmp_path)
    assert "[TR] Some prose with a note[^1] introducing a poem." in text, (
        "the footnote ref must land in the surrounding prose, not the verse"
    )
    assert "*Rendered V001 line one / Rendered V001 line two*" in text, (
        "mount=embedded must render as a compact inline italic substitution, "
        "not a blockquote"
    )
    assert "[^1]: [TR] 1 A note about the prose." in text


# ===========================================================================
# (c) footnote DEFINITION text embeds a verse (mount=embedded,
#     context=footnote) -- the embedded verse is referenced (so assembly
#     succeeds, no orphan) but its own rendered content is stripped, never
#     independently rendered anywhere (Phase 0 policy).
# ===========================================================================


def test_case_c_footnote_def_embedded_verse_is_stripped_not_rendered(extract_mod, tmp_path):
    text = _render_case("c", extract_mod, tmp_path)
    assert "[^1]: [TR] 1 A note quoting a poem: " in text, (
        "the embedded verse's placeholder must be STRIPPED from the def "
        "text (Phase 0 policy), leaving a trailing space where it was"
    )
    assert "Rendered V001" not in text, (
        "the footnote-def-embedded verse's own rendered content must never "
        "appear anywhere in the output -- referenced but not independently "
        "rendered"
    )


# ===========================================================================
# (d) standalone verse (mount=block), no footnote -- a real blockquote
#     (_render_verse_block), and since there is no footnote at all in this
#     case, no "[^" markup of any kind anywhere.
# ===========================================================================


def test_case_d_standalone_verse_block_no_footnote(extract_mod, tmp_path):
    text = _render_case("d", extract_mod, tmp_path)
    assert "> Rendered V001 line one" in text
    assert "> Rendered V001 line two" in text
    assert "*Literal: A literal gloss for V001 line one and line two, worded differently*" in text
    assert "[^" not in text, "no footnote exists in this case -- no ref/def markup at all"


# ===========================================================================
# (e) standalone verse (mount=block) whose OWN text carries a footnote ref
#     (r4 #93 end-to-end fix -- the core #105 "render CLOSED" case). The
#     footnote REF lands INSIDE the blockquote (converted from the verse's
#     own ⟦FNREF_1⟧ via _convert_verse_fnrefs); the DEF line sits outside it.
# ===========================================================================


def test_case_e_standalone_verse_own_footnote_ref(extract_mod, tmp_path):
    text = _render_case("e", extract_mod, tmp_path)
    assert "> Rendered V001 line one[^1]" in text, (
        "the verse's own footnote ref must be converted and land INSIDE "
        "the blockquote"
    )
    assert "[^1]: [TR] 1 A note about the poem." in text


# ===========================================================================
# (f) footnote cited INSIDE an embedded verse (mount=embedded) -- the
#     surrounding prose carries no footnote ref of its own; the ref lands
#     inside the compact inline italic verse substitution (assemble+render
#     half of the segpack point-1 fix).
# ===========================================================================


def test_case_f_footnote_cited_inside_embedded_verse(extract_mod, tmp_path):
    text = _render_case("f", extract_mod, tmp_path)
    assert "[TR] Some prose introducing a poem." in text, (
        "the surrounding prose must carry NO footnote ref of its own"
    )
    assert "*Rendered V001 line one[^1] / Rendered V001 line two*" in text, (
        "the footnote ref must land INSIDE the compact inline italic verse "
        "substitution"
    )
    assert "[^1]: [TR] 1 A note about the poem." in text


# ===========================================================================
# (g) outer verse cites a footnote whose DEF embeds a SECOND verse (r6
#     finding 2) -- the corpus's hardest case, and the most important test
#     in the whole corpus. The outer verse renders normally as a blockquote
#     WITH its footnote ref converted; the inner verse's own
#     rendered/literal_gloss content must NEVER appear anywhere in the final
#     output (referenced, so assembly succeeds and there is no orphan, but
#     never independently rendered) -- checked over the WHOLE document, not
#     just the footnote def line, since TWO verses' sentinels were in play.
# ===========================================================================


def test_case_g_outer_verse_footnote_def_embeds_second_verse(extract_mod, tmp_path):
    text = _render_case("g", extract_mod, tmp_path)
    assert "> Rendered V001 line one[^1]" in text, (
        "the OUTER verse must render normally, as a blockquote, with its "
        "footnote ref converted"
    )
    assert "[^1]: [TR] 1 A note quoting another poem: " in text, (
        "the INNER verse's placeholder must be stripped from the def text "
        "(Phase 0 policy), leaving a trailing space where it was"
    )
    assert "Rendered V002" not in text, (
        "the KEY assertion: the inner verse's own rendered/literal_gloss "
        "content must never appear ANYWHERE in the final output -- "
        "referenced (so assembly succeeds, no orphan) but never "
        "independently rendered"
    )
    # The standard sentinel-leak invariant is already checked inside
    # _render_case(), but it is especially load-bearing here since two
    # distinct verses' sentinels were in play simultaneously.
