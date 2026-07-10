"""tests/verse_footnote_corpus_segpack.test.py

SEGPACK-layer coverage for the #106 "verse x footnote corpus" (the 7 minimal
Gutenberg-EPUB fixtures in ``tests/verse_footnote_corpus.py``, cases "a".."g").
Each test drives ONE case through the REAL extractor (``extract.py.template``'s
``build()``) THEN the REAL ``segpack.py``'s ``build_pack()`` (never a
reimplementation or a hand-guessed segpack dict), and asserts:

  - ``validate_segpack(pack)`` returns an empty error list (structurally
    sound against ``segpack.schema.json``'s hand-rolled self-check);
  - ``pack["generation_hashes"]`` is the corpus's synthetic-hash bridge,
    threaded through verbatim (see ``verse_footnote_corpus.py``'s "build() ->
    segpack bridge" docstring section -- ``build_pack()`` copies
    ``manifest.generation_hashes``/``canon.generation_hashes`` byte-for-byte,
    it never recomputes them);
  - ``verses``/``footnotes`` passthrough, with ``mount``/``n_line`` threaded
    per cluster #96.

The corpus module is NOT a ``*.test.py`` file (pytest's ``python_files =
*.test.py`` glob in ``pytest.ini`` would never collect it as a test anyway),
so it is self-loaded here via ``importlib.util.spec_from_file_location`` --
the same convention every other test module in this suite uses for the real,
shipped scripts under ``skills/literary-translator/assets/scripts/`` (see
``segpack_verse_mount.test.py``'s own ``_load_module`` for the established
segpack.py-loading idiom). The corpus helper already loads segpack.py
module-level as ``corpus.SEGPACK_MODULE`` (+ a real ``fr.json`` particle
config as ``corpus.LANG_CONFIG``), so this file never loads segpack.py
itself -- it only calls ``corpus.segpack_for(...)`` /
``corpus.SEGPACK_MODULE.validate_segpack(...)``.

Case (f) is the POINT-1 regression this file specifically pins: a footnote
cited ONLY inside an EMBEDDED verse's own text is invisible on the carrier
block (segpack's ``_BLOCK_KEYS`` output shape does not even carry an
``fnrefs`` field) yet must still surface in ``pack["footnotes"]`` via
segpack's dedicated verse-scan pass. Case (c)/(g) pin the sibling
``footnote_def_block_ids`` pickup -- a verse embedded in a footnote's own
DEFINITION text is included in ``verses[]`` even though its ``parent_block``
is never one of this segpack's own ``blocks[]`` ids.

Collection note: like every ``*.test.py`` file in this suite, run with
``python3 -m pytest --import-mode=importlib
tests/verse_footnote_corpus_segpack.test.py`` (configured project-wide via
pytest.ini).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

CORPUS_PATH = Path(__file__).resolve().parent / "verse_footnote_corpus.py"


def _load_corpus():
    spec = importlib.util.spec_from_file_location("verse_footnote_corpus_for_segpack_test", CORPUS_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec for {CORPUS_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus = _load_corpus()

# The corpus's synthetic-hash bridge (see verse_footnote_corpus.py's
# CORPUS_MANIFEST_HASHES / CORPUS_CANON) -- build_pack() copies these
# verbatim onto every case's segpack, regardless of which fixture produced it.
EXPECTED_GENERATION_HASHES = {
    "source_extraction_hash": "corpus-fixed-extraction-hash",
    "source_input_hash": "corpus-fixed-input-hash",
    "particle_config_hash": "corpus-fixed-particle-hash",
    "derivation_bundle_hash": "corpus-fixed-bundle-hash",
}


@pytest.fixture()
def extract_mod(tmp_path):
    return corpus.load_extract_module(tmp_path)


def _pack_for(label, extract_mod, tmp_path):
    """Drives corpus case `label` through the REAL extractor then the REAL
    segpack.build_pack(), returning the resulting segpack dict."""
    case = corpus.CASES[label]
    manifest, report, max_words = corpus.manifest_for(case, extract_mod, tmp_path)
    return corpus.segpack_for("seg01", manifest, case.apparatus_policy, corpus.LANG_CONFIG)


def _assert_valid_and_hashes(pack):
    """Common assertion every case below shares: validate_segpack() is clean
    and the synthetic-hash bridge threaded through verbatim -- so each
    per-case test only asserts what's DISTINCT about that case's
    blocks/footnotes/verses shape."""
    errors = corpus.SEGPACK_MODULE.validate_segpack(pack)
    assert errors == [], errors
    assert pack["generation_hashes"] == EXPECTED_GENERATION_HASHES


# ---------------------------------------------------------------------------
# (a) prose block + body footnote ref, no verse.
# ---------------------------------------------------------------------------


def test_case_a_prose_body_footnote_no_verse(extract_mod, tmp_path):
    """No verse anywhere: HEAD + PARA blocks only, one body footnote,
    verses[] stays empty."""
    pack = _pack_for("a", extract_mod, tmp_path)
    _assert_valid_and_hashes(pack)

    assert [b["id"] for b in pack["blocks"]] == ["HEAD:seg01", "PARA:seg01:0001"]
    assert pack["footnotes"] == [{"n": 1, "source_text": "1 A note about the prose."}]
    assert pack["verses"] == []


# ---------------------------------------------------------------------------
# (b) embedded verse (mount=embedded), footnote in the SURROUNDING prose.
# ---------------------------------------------------------------------------


def test_case_b_embedded_verse_footnote_in_surrounding_prose(extract_mod, tmp_path):
    """Embedded verse (mount=embedded) inside a QUOTE block; the footnote is
    cited from the surrounding prose paragraph, never from the verse
    itself -- both discovered via the ORDINARY block-fnrefs path."""
    pack = _pack_for("b", extract_mod, tmp_path)
    _assert_valid_and_hashes(pack)

    assert [b["id"] for b in pack["blocks"]] == [
        "HEAD:seg01", "PARA:seg01:0001", "QUOTE:seg01:0002",
    ]
    quote_block = next(b for b in pack["blocks"] if b["id"] == "QUOTE:seg01:0002")
    assert quote_block["plain_text"].startswith("⟦VERSE_V001_"), quote_block["plain_text"]

    assert pack["footnotes"] == [{"n": 1, "source_text": "1 A note about the prose."}]

    assert len(pack["verses"]) == 1, pack["verses"]
    v = pack["verses"][0]
    assert v["vid"] == "V001"
    assert v["parent_block"] == "QUOTE:seg01:0002"
    assert v["mount"] == "embedded"
    assert v["n_line"] == 2


# ---------------------------------------------------------------------------
# (c) footnote DEFINITION text embeds a verse -- footnote_def_block_ids pickup.
# ---------------------------------------------------------------------------


def test_case_c_footnote_definition_embeds_verse(extract_mod, tmp_path):
    """The verse lives inside the footnote's own DEFINITION text (FN:1),
    which is never a member of this segpack's own blocks[] -- yet the verse
    is still included in verses[] because its parent is one of this
    segpack's footnote_def_block_ids (segpack.py:326's
    ``parent not in seg_block_ids and parent not in footnote_def_block_ids``
    guard)."""
    pack = _pack_for("c", extract_mod, tmp_path)
    _assert_valid_and_hashes(pack)

    assert [b["id"] for b in pack["blocks"]] == ["HEAD:seg01", "PARA:seg01:0001"]
    assert "FN:1" not in [b["id"] for b in pack["blocks"]]

    assert len(pack["footnotes"]) == 1, pack["footnotes"]
    fn = pack["footnotes"][0]
    assert fn["n"] == 1
    assert fn["source_text"].startswith("1 A note quoting a poem: ⟦VERSE_V001_"), fn["source_text"]

    assert len(pack["verses"]) == 1, pack["verses"]
    v = pack["verses"][0]
    assert v["vid"] == "V001"
    assert v["parent_block"] == "FN:1"
    assert v["mount"] == "embedded"
    assert v["n_line"] == 2


# ---------------------------------------------------------------------------
# (d) standalone verse block (mount=block), no footnote.
# ---------------------------------------------------------------------------


def test_case_d_standalone_verse_no_footnote(extract_mod, tmp_path):
    """A mount=block standalone verse, no footnote at all."""
    pack = _pack_for("d", extract_mod, tmp_path)
    _assert_valid_and_hashes(pack)

    assert [b["id"] for b in pack["blocks"]] == ["HEAD:seg01", "VERSE:seg01:0001"]
    assert pack["footnotes"] == []

    assert len(pack["verses"]) == 1, pack["verses"]
    v = pack["verses"][0]
    assert v["vid"] == "V001"
    assert v["placeholder"].startswith("⟦VERSE_V001_"), v["placeholder"]
    assert v["parent_block"] == "VERSE:seg01:0001"
    assert v["mount"] == "block"
    assert v["n_line"] == 2


# ---------------------------------------------------------------------------
# (e) standalone verse (mount=block) whose OWN text carries a footnote.
# ---------------------------------------------------------------------------


def test_case_e_standalone_verse_own_footnote(extract_mod, tmp_path):
    """A mount=block verse IS itself a body block, so its own fnrefs are
    already recorded on the manifest verse-carrier block -- the footnote is
    discovered via the ORDINARY block-fnrefs scan (r4 #93 end-to-end fix),
    with no need for segpack's dedicated embedded-verse pass."""
    pack = _pack_for("e", extract_mod, tmp_path)
    _assert_valid_and_hashes(pack)

    assert [b["id"] for b in pack["blocks"]] == ["HEAD:seg01", "VERSE:seg01:0001"]
    assert pack["footnotes"] == [{"n": 1, "source_text": "1 A note about the poem."}]

    assert len(pack["verses"]) == 1, pack["verses"]
    v = pack["verses"][0]
    assert v["vid"] == "V001"
    assert v["parent_block"] == "VERSE:seg01:0001"
    assert v["mount"] == "block"
    assert v["n_line"] == 2


# ---------------------------------------------------------------------------
# (f) footnote cited INSIDE an embedded verse -- segpack POINT-1 discovery.
# ---------------------------------------------------------------------------


def test_case_f_footnote_cited_inside_embedded_verse(extract_mod, tmp_path):
    """POINT-1 regression (this is the case that RED-lines on pre-fix
    segpack): the footnote is cited ONLY inside the embedded verse's OWN
    text, never in the QUOTE carrier block's own fnrefs/plain_text -- and
    segpack's block output shape (``_BLOCK_KEYS``) doesn't even carry an
    ``fnrefs`` field, so the carrier block itself exposes NO fnrefs-driven
    signal at all. The footnote is discoverable ONLY via segpack's dedicated
    verse-scan pass reading ``manifest.verse.store``'s own
    fnrefs/plain_text -- proven here by asserting it IS present in
    pack["footnotes"] despite the carrier block showing nothing."""
    pack = _pack_for("f", extract_mod, tmp_path)
    _assert_valid_and_hashes(pack)

    assert [b["id"] for b in pack["blocks"]] == [
        "HEAD:seg01", "PARA:seg01:0001", "QUOTE:seg01:0002",
    ]
    quote_block = next(b for b in pack["blocks"] if b["id"] == "QUOTE:seg01:0002")
    # segpack's block output shape never carries fnrefs at all (build_pack's
    # blocks_out entries only ever get id/order_index/source_html/plain_text/
    # body_ref_markers) -- so the carrier block itself is blind to the
    # footnote below, yet it still shows up in pack["footnotes"].
    assert "fnrefs" not in quote_block

    assert pack["footnotes"] == [{"n": 1, "source_text": "1 A note about the poem."}]

    assert len(pack["verses"]) == 1, pack["verses"]
    v = pack["verses"][0]
    assert v["vid"] == "V001"
    assert v["parent_block"] == "QUOTE:seg01:0002"
    assert v["mount"] == "embedded"
    assert v["n_line"] == 2


# ---------------------------------------------------------------------------
# (g) outer verse cites a footnote whose DEFINITION embeds a second verse.
# ---------------------------------------------------------------------------


def test_case_g_outer_verse_footnote_definition_embeds_second_verse(extract_mod, tmp_path):
    """Two verse.store entries compose: the OUTER mount=block verse carries
    the fnref itself (discovered via the same ordinary block-fnrefs path as
    case e), and that footnote's own DEFINITION embeds a SECOND,
    mount=embedded verse (picked up via the same footnote_def_block_ids
    mechanism as case c) -- proving point-1 discovery and the
    footnote-def-embedded-verse pickup compose correctly together
    (r6 finding 2)."""
    pack = _pack_for("g", extract_mod, tmp_path)
    _assert_valid_and_hashes(pack)

    assert [b["id"] for b in pack["blocks"]] == ["HEAD:seg01", "VERSE:seg01:0001"]

    assert len(pack["footnotes"]) == 1, pack["footnotes"]
    fn = pack["footnotes"][0]
    assert fn["n"] == 1
    assert fn["source_text"].startswith(
        "1 A note quoting another poem: ⟦VERSE_V002_"
    ), fn["source_text"]

    assert len(pack["verses"]) == 2, pack["verses"]
    by_vid = {v["vid"]: v for v in pack["verses"]}
    assert set(by_vid) == {"V001", "V002"}, by_vid

    outer = by_vid["V001"]
    assert outer["parent_block"] == "VERSE:seg01:0001"
    assert outer["mount"] == "block"
    assert outer["n_line"] == 2

    inner = by_vid["V002"]
    assert inner["parent_block"] == "FN:1"
    assert inner["mount"] == "embedded"
    assert inner["n_line"] == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
