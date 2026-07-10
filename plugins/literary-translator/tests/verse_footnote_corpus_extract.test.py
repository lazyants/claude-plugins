"""tests/verse_footnote_corpus_extract.test.py

EXTRACT-layer coverage for the #106 "verse x footnote corpus" (the 7 minimal
Gutenberg-EPUB fixtures in ``tests/verse_footnote_corpus.py``, cases "a".."g").
Each test drives ONE case through the REAL, shipped ``extract.py.template``'s
``build()`` + ``run_self_checks()`` (never a reimplementation or a
hand-guessed manifest) and pins the verse/footnote-relevant manifest shape --
this is the layer that catches #92/#93 output drift and #96 mount-threading
regressions before they reach segpack/validate/assemble/render.

The corpus module is NOT a ``*.test.py`` file (pytest's ``python_files =
*.test.py`` glob in ``pytest.ini`` would never collect it as a test anyway),
so it is self-loaded here via ``importlib.util.spec_from_file_location`` --
the same convention every other test file in this suite uses for the real
shipped scripts under ``skills/literary-translator/assets/scripts/``. See
``verse_footnote_corpus.py``'s own module docstring for the full rationale
behind each of the 7 cases and the build()->segpack bridge.

Collection note: like every ``*.test.py`` file in this suite, run with
``python3 -m pytest --import-mode=importlib
tests/verse_footnote_corpus_extract.test.py`` (configured project-wide via
pytest.ini).
"""
import importlib.util
from pathlib import Path

import pytest

CORPUS_PATH = Path(__file__).resolve().parent / "verse_footnote_corpus.py"


def _load_corpus():
    spec = importlib.util.spec_from_file_location("verse_footnote_corpus_for_extract_test", CORPUS_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec for {CORPUS_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus = _load_corpus()


@pytest.fixture()
def extract_mod(tmp_path):
    return corpus.load_extract_module(tmp_path)


def _find_check(results, name):
    matches = [r for r in results if r["name"] == name]
    assert len(matches) == 1, f"expected exactly one {name!r} check, found {len(matches)}"
    return matches[0]


def _assert_common_shape(manifest):
    """Every corpus case yields exactly one segment ("seg01") with a single
    HEAD block reading "Chapter" -- pinned once here so each per-case test
    below only asserts what's DISTINCT about that case's verse/footnote
    shape."""
    assert [s["seg"] for s in manifest["segments"]] == ["seg01"], manifest["segments"]
    assert manifest["blocks"]["HEAD:seg01"]["plain_text"] == "Chapter"


# ---------------------------------------------------------------------------
# (a) prose block + body footnote ref, no verse.
# ---------------------------------------------------------------------------

def test_case_a_prose_with_body_footnote_no_verse(extract_mod, tmp_path):
    """No verse anywhere: verse.store stays empty and the footnote's anchor
    resolves onto the plain prose block."""
    case = corpus.CASES["a"]
    manifest, report, max_words = corpus.manifest_for(case, extract_mod, tmp_path)
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    assert checks["all_pass"] is True, checks["results"]
    _assert_common_shape(manifest)

    assert manifest["verse"]["store"] == []

    assert len(manifest["footnotes"]) == 1, manifest["footnotes"]
    fn = manifest["footnotes"][0]
    assert fn["n"] == 1
    assert fn["anchor_id"] == "FNanchor_1"
    assert fn["def_id"] == "Footnote_1"
    assert fn["anchor_block"] == "PARA:seg01:0001"
    assert fn["anchor_seg"] == "seg01"
    assert fn["def_block"] == "FN:1"

    para = manifest["blocks"]["PARA:seg01:0001"]
    assert para["plain_text"] == "Some prose with a note⟦FNREF_1⟧ attached."
    assert para["fnrefs"] == [1]


# ---------------------------------------------------------------------------
# (b) embedded verse (mount=embedded), footnote ref in the SURROUNDING
# prose -- not inside the verse itself.
# ---------------------------------------------------------------------------

def test_case_b_embedded_verse_footnote_in_surrounding_prose(extract_mod, tmp_path):
    """The verse is embedded in a QUOTE block; the footnote anchors on the
    PROSE paragraph that introduces it, never on the verse. The verse's own
    carrier block holds JUST its placeholder and carries no fnrefs of its
    own."""
    case = corpus.CASES["b"]
    manifest, report, max_words = corpus.manifest_for(case, extract_mod, tmp_path)
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    assert checks["all_pass"] is True, checks["results"]
    _assert_common_shape(manifest)

    verse_store = manifest["verse"]["store"]
    assert len(verse_store) == 1, verse_store
    v = verse_store[0]
    assert v["vid"] == "V001"
    assert v["context"] == "body"
    assert v["mount"] == "embedded"
    assert v["parent_block"] == "QUOTE:seg01:0002"
    assert v["n_line"] == 2
    assert v["n_stanza"] == 1
    assert v["fnrefs"] == []

    assert len(manifest["footnotes"]) == 1, manifest["footnotes"]
    fn = manifest["footnotes"][0]
    assert fn["n"] == 1
    assert fn["anchor_block"] == "PARA:seg01:0001"

    quote_block = manifest["blocks"]["QUOTE:seg01:0002"]
    assert quote_block["plain_text"].startswith("⟦VERSE_V001_"), quote_block["plain_text"]
    assert quote_block["fnrefs"] == []


# ---------------------------------------------------------------------------
# (c) footnote DEFINITION text embeds a verse (mount=embedded,
# context="footnote") -- segpack's footnote_def_block_ids pickup target.
# ---------------------------------------------------------------------------

def test_case_c_footnote_definition_embeds_verse(extract_mod, tmp_path):
    """The verse lives inside the footnote's OWN definition text
    (context="footnote"), anchored the ordinary way from the prose
    paragraph."""
    case = corpus.CASES["c"]
    manifest, report, max_words = corpus.manifest_for(case, extract_mod, tmp_path)
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    assert checks["all_pass"] is True, checks["results"]
    _assert_common_shape(manifest)

    verse_store = manifest["verse"]["store"]
    assert len(verse_store) == 1, verse_store
    v = verse_store[0]
    assert v["vid"] == "V001"
    assert v["context"] == "footnote"
    assert v["mount"] == "embedded"
    assert v["parent_block"] == "FN:1"
    assert v["fnrefs"] == []

    assert len(manifest["footnotes"]) == 1, manifest["footnotes"]
    fn = manifest["footnotes"][0]
    assert fn["n"] == 1
    assert fn["anchor_block"] == "PARA:seg01:0001"
    assert fn["def_block"] == "FN:1"

    fn_text = manifest["blocks"]["FN:1"]["plain_text"]
    assert fn_text.startswith("1 A note quoting a poem: ⟦VERSE_V001_"), fn_text


# ---------------------------------------------------------------------------
# (d) standalone verse block (mount=block), no footnote.
# ---------------------------------------------------------------------------

def test_case_d_standalone_verse_block_no_footnote(extract_mod, tmp_path):
    """A mount=block VERSE carrier block stores the RAW verse text, not a
    placeholder -- the placeholder concept lives only in verse.store."""
    case = corpus.CASES["d"]
    manifest, report, max_words = corpus.manifest_for(case, extract_mod, tmp_path)
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    assert checks["all_pass"] is True, checks["results"]
    _assert_common_shape(manifest)

    verse_store = manifest["verse"]["store"]
    assert len(verse_store) == 1, verse_store
    v = verse_store[0]
    assert v["vid"] == "V001"
    assert v["context"] == "body"
    assert v["mount"] == "block"
    assert v["parent_block"] == "VERSE:seg01:0001"
    assert v["n_line"] == 2
    assert v["n_stanza"] == 1
    assert v["fnrefs"] == []

    assert manifest["footnotes"] == []

    assert manifest["blocks"]["VERSE:seg01:0001"]["plain_text"] == (
        "Standalone line one\nStandalone line two"
    )


# ---------------------------------------------------------------------------
# (e) standalone verse block (mount=block) whose OWN verse text carries a
# footnote ref (r4 #93 end-to-end fix).
# ---------------------------------------------------------------------------

def test_case_e_standalone_verse_own_footnote(extract_mod, tmp_path):
    """A mount=block verse IS a body block, so the ordinary body-walk anchor
    recording anchors the footnote directly on the verse's OWN carrier
    block -- no special-casing needed."""
    case = corpus.CASES["e"]
    manifest, report, max_words = corpus.manifest_for(case, extract_mod, tmp_path)
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    assert checks["all_pass"] is True, checks["results"]
    _assert_common_shape(manifest)

    verse_store = manifest["verse"]["store"]
    assert len(verse_store) == 1, verse_store
    v = verse_store[0]
    assert v["vid"] == "V001"
    assert v["context"] == "body"
    assert v["mount"] == "block"
    assert v["parent_block"] == "VERSE:seg01:0001"
    assert v["fnrefs"] == [1]

    assert len(manifest["footnotes"]) == 1, manifest["footnotes"]
    fn = manifest["footnotes"][0]
    assert fn["n"] == 1
    assert fn["anchor_block"] == "VERSE:seg01:0001"

    verse_block = manifest["blocks"]["VERSE:seg01:0001"]
    assert verse_block["plain_text"] == "A line with a note⟦FNREF_1⟧\nSecond line"
    assert verse_block["fnrefs"] == [1]

    results = checks["results"]
    assert _find_check(results, "fn_bijection")["ok"] is True
    assert _find_check(results, "fnref_sentinel_unique")["ok"] is True


# ---------------------------------------------------------------------------
# (f) footnote ref cited INSIDE an embedded verse -- segpack point-1
# discovery target.
# ---------------------------------------------------------------------------

def test_case_f_footnote_cited_inside_embedded_verse(extract_mod, tmp_path):
    """The verse's OWN translated content carries the ⟦FNREF_1⟧, not the
    surrounding prose. Since an embedded verse is not itself a body block,
    the #93 Fix A post-mount pass anchors the footnote on the CARRIER block
    instead -- whose own fnrefs stay empty (the citation lives only on the
    verse.store entry), which is exactly why segpack needs its own point-1
    discovery pass (tested at the segpack layer, not here)."""
    case = corpus.CASES["f"]
    manifest, report, max_words = corpus.manifest_for(case, extract_mod, tmp_path)
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    assert checks["all_pass"] is True, checks["results"]
    _assert_common_shape(manifest)

    verse_store = manifest["verse"]["store"]
    assert len(verse_store) == 1, verse_store
    v = verse_store[0]
    assert v["vid"] == "V001"
    assert v["context"] == "body"
    assert v["mount"] == "embedded"
    assert v["parent_block"] == "QUOTE:seg01:0002"
    assert v["fnrefs"] == [1]

    assert len(manifest["footnotes"]) == 1, manifest["footnotes"]
    fn = manifest["footnotes"][0]
    assert fn["n"] == 1
    assert fn["anchor_block"] == "QUOTE:seg01:0002"

    assert manifest["blocks"]["QUOTE:seg01:0002"]["fnrefs"] == []

    results = checks["results"]
    assert _find_check(results, "fn_bijection")["ok"] is True
    assert _find_check(results, "fnref_sentinel_unique")["ok"] is True


# ---------------------------------------------------------------------------
# (g) outer verse cites a footnote whose DEFINITION embeds a SECOND verse
# (r6 finding 2).
# ---------------------------------------------------------------------------

def test_case_g_outer_verse_footnote_definition_embeds_second_verse(extract_mod, tmp_path):
    """Two verse.store entries: the OUTER mount=block verse (which carries
    the fnref itself, same shape as case e) and an INNER mount=embedded
    verse living inside that footnote's own definition text (same shape as
    case c) -- the inner verse must be present/mounted, never dropped just
    because its footnote is cited from another verse rather than prose."""
    case = corpus.CASES["g"]
    manifest, report, max_words = corpus.manifest_for(case, extract_mod, tmp_path)
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    assert checks["all_pass"] is True, checks["results"]
    _assert_common_shape(manifest)

    verse_store = manifest["verse"]["store"]
    assert len(verse_store) == 2, verse_store
    by_vid = {v["vid"]: v for v in verse_store}
    assert set(by_vid) == {"V001", "V002"}, by_vid

    outer = by_vid["V001"]
    assert outer["context"] == "body"
    assert outer["mount"] == "block"
    assert outer["parent_block"] == "VERSE:seg01:0001"
    assert outer["fnrefs"] == [1]

    inner = by_vid["V002"]
    assert inner["context"] == "footnote"
    assert inner["mount"] == "embedded"
    assert inner["parent_block"] == "FN:1"
    assert inner["fnrefs"] == []

    assert len(manifest["footnotes"]) == 1, manifest["footnotes"]
    fn = manifest["footnotes"][0]
    assert fn["n"] == 1
    assert fn["anchor_block"] == "VERSE:seg01:0001"
    assert fn["def_block"] == "FN:1"

    fn_text = manifest["blocks"]["FN:1"]["plain_text"]
    assert fn_text.startswith("1 A note quoting another poem: ⟦VERSE_V002_"), fn_text
