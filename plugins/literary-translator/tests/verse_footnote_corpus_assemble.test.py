"""tests/verse_footnote_corpus_assemble.test.py -- #106 verse x footnote
corpus, ASSEMBLE layer only.

Drives all 7 corpus cases (``verse_footnote_corpus.CASES``, keyed "a".."g")
through the REAL extractor -> REAL segpack -> the shared programmatic draft
builder (``corpus.draft_for()``) -> the REAL ``assemble.py``'s
``build_nodestream()``. Three sibling files already cover the extract and
segpack layers (``verse_footnote_corpus_extract.test.py``,
``verse_footnote_corpus_segpack.test.py``) and the validate layer
(``verse_footnote_corpus_validate.test.py``); this file is the assemble-layer
member of that same family and touches none of them.

## Why ``build_nodestream`` is driven IN-PROCESS, not via subprocess+ledger

The #106 build plan gives this layer an explicit choice: "Follow
assemble.test.py's on-disk-durable-root convention, or drive
build_nodestream in-process." ``assemble.test.py`` shells out to a real
``durable_root`` (manifest.json, ledger.json, canon.json, profile.yml +
ownership marker) because IT is asserting assemble.py's whole-process
contract -- exit codes, the fail-closed JSON error line, and the two
committed-to-disk artifacts (``nodestream.json`` / ``anchor_map.json``).
None of that is this corpus's concern: every one of these 7 cases is
expected to SUCCEED, and what's being pinned is the verse x footnote
node/anchor shape ``build_nodestream()`` returns, not the surrounding CLI
plumbing. ``corpus.build_nodestream_for()`` therefore only materializes a
throwaway ``scripts/`` copy + the two on-disk segment files
(``segpack_seg01.json`` / ``seg01.draft.json``) that ``build_nodestream()``
itself reads, and calls it directly as an in-process Python function --
mirroring ``render_obsidian.py``'s own module docstring, which states the
identical rationale one layer up for its sibling render layer: "D's tests
are expected to import render()/its helpers directly ... rather than shell
out." No ledger.json/canon.json/cache-key harness is built here because
this layer doesn't exercise them -- a hash-derivation or ledger-gating
regression is a wholly separate concern from a verse/footnote-carry-through
regression (see the corpus helper's own module docstring, "build() ->
segpack bridge" section, for the same separation-of-concerns argument one
layer down).

## What's asserted

Per case: the assembled ``nodestream["nodes"]`` as ``(id, fnrefs)`` pairs (in
book order), ``nodestream["footnotes"]``, and ``anchor_map["verses"]``. Every
case must assemble WITHOUT raising -- for cases (c) and (g) in particular,
that absence of an exception is itself the load-bearing assertion: it proves
a footnote-def-embedded verse is treated as referenced (not a fatal
``orphan_verse``), even though it never appears in any node's own
``verses[]`` list. Case (g) additionally pins that the OUTER verse (V001)
resolves normally while the INNER verse (V002), referenced only through the
footnote-definition text, is never independently rendered into any node.
"""

import importlib.util
from pathlib import Path

import pytest

CORPUS_PATH = Path(__file__).resolve().parent / "verse_footnote_corpus.py"


def _load_corpus():
    spec = importlib.util.spec_from_file_location("verse_footnote_corpus_for_assemble_test", CORPUS_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec for {CORPUS_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus = _load_corpus()


@pytest.fixture
def extract_mod(tmp_path):
    return corpus.load_extract_module(tmp_path)


def _node_id_fnrefs(nodestream: dict) -> list:
    return [(n["id"], n["fnrefs"]) for n in nodestream["nodes"]]


def _assemble_case(label: str, tmp_path: Path, extract_mod):
    """Runs one corpus case end-to-end (extract -> segpack -> draft ->
    build_nodestream) and returns (nodestream, anchor_map)."""
    case = corpus.CASES[label]
    manifest, report, max_words = corpus.manifest_for(case, extract_mod, tmp_path)
    pack = corpus.segpack_for("seg01", manifest, case.apparatus_policy, corpus.LANG_CONFIG)
    draft = corpus.draft_for(manifest, pack)
    nodestream, anchor_map, assemble_mod = corpus.build_nodestream_for(case, manifest, pack, draft, tmp_path)
    return nodestream, anchor_map


# ===========================================================================
# (a) prose block + body footnote ref, no verse.
# ===========================================================================


def test_case_a_prose_block_with_body_footnote_no_verse(tmp_path, extract_mod):
    nodestream, anchor_map = _assemble_case("a", tmp_path, extract_mod)

    assert _node_id_fnrefs(nodestream) == [
        ("HEAD:seg01", []),
        ("PARA:seg01:0001", [1]),
    ]
    assert nodestream["footnotes"] == [{"n": 1, "text": "[TR] 1 A note about the prose."}]
    assert anchor_map["verses"] == [], "no verse anywhere in this case -- anchor_map.verses must stay empty"


# ===========================================================================
# (b) prose/quote block with an EMBEDDED verse; footnote ref in the
#     SURROUNDING PROSE, not inside the verse.
# ===========================================================================


def test_case_b_embedded_verse_with_footnote_in_surrounding_prose(tmp_path, extract_mod):
    nodestream, anchor_map = _assemble_case("b", tmp_path, extract_mod)

    assert _node_id_fnrefs(nodestream) == [
        ("HEAD:seg01", []),
        ("PARA:seg01:0001", [1]),
        ("QUOTE:seg01:0002", []),
    ], "the embedded verse's carrier node must have NO fnrefs -- the footnote lives only in the surrounding prose"
    assert nodestream["footnotes"] == [{"n": 1, "text": "[TR] 1 A note about the prose."}]
    assert anchor_map["verses"] == ["V001"], (
        "the embedded verse's carrier IS a segment block, so it belongs in anchor_map.verses"
    )


# ===========================================================================
# (c) footnote DEFINITION text that embeds a verse (segpack's
#     footnote_def_block_ids pickup) -- the embedded verse never becomes a
#     node of its own, yet must not be a fatal orphan.
# ===========================================================================


def test_case_c_verse_embedded_in_footnote_definition_is_referenced_not_orphan(tmp_path, extract_mod):
    nodestream, anchor_map = _assemble_case("c", tmp_path, extract_mod)

    assert _node_id_fnrefs(nodestream) == [
        ("HEAD:seg01", []),
        ("PARA:seg01:0001", [1]),
    ], "no verse-bearing node exists in this segment's own nodes at all"
    assert nodestream["footnotes"] == [
        {"n": 1, "text": "[TR] 1 A note quoting a poem: "}
    ], "the embedded verse placeholder inside the footnote def text is STRIPPED (Phase 0 policy), leaving a trailing space"
    assert anchor_map["verses"] == [], (
        "the footnote-def-embedded verse is referenced (build succeeds, no orphan_verse) but "
        "referenced-only -- it must never appear in anchor_map.verses"
    )


# ===========================================================================
# (d) standalone verse block (mount=block), no footnote.
# ===========================================================================


def test_case_d_standalone_verse_block_no_footnote(tmp_path, extract_mod):
    nodestream, anchor_map = _assemble_case("d", tmp_path, extract_mod)

    assert _node_id_fnrefs(nodestream) == [
        ("HEAD:seg01", []),
        ("VERSE:seg01:0001", []),
    ]
    assert nodestream["footnotes"] == []
    assert anchor_map["verses"] == ["V001"]


# ===========================================================================
# (e) standalone verse block whose OWN verse text carries a footnote ref
#     (r4 #93 end-to-end fix: _scan_verse_content_fnrefs reads
#     draft.verses[vid].rendered/.literal_gloss, not the block text).
# ===========================================================================


def test_case_e_standalone_verse_own_content_carries_footnote_ref(tmp_path, extract_mod):
    nodestream, anchor_map = _assemble_case("e", tmp_path, extract_mod)

    assert _node_id_fnrefs(nodestream) == [
        ("HEAD:seg01", []),
        ("VERSE:seg01:0001", [1]),
    ], "the verse's own rendered/literal_gloss content carries ⟦FNREF_1⟧, so its node's fnrefs must include it"
    assert nodestream["footnotes"] == [{"n": 1, "text": "[TR] 1 A note about the poem."}]
    assert anchor_map["verses"] == ["V001"]


# ===========================================================================
# (f) footnote ref cited INSIDE an embedded verse (mount=embedded) -- the
#     CARRIER node's fnrefs comes purely from _scan_verse_content_fnrefs on
#     the embedded verse's own content (segpack point-1 discovery), even
#     though the carrier's own block text never contains a raw FNREF.
# ===========================================================================


def test_case_f_embedded_verse_own_content_carries_footnote_ref(tmp_path, extract_mod):
    nodestream, anchor_map = _assemble_case("f", tmp_path, extract_mod)

    assert _node_id_fnrefs(nodestream) == [
        ("HEAD:seg01", []),
        ("PARA:seg01:0001", []),
        ("QUOTE:seg01:0002", [1]),
    ], "the QUOTE carrier's fnrefs must be [1] even though its own text has no raw FNREF -- it comes from the embedded verse's own content"
    assert nodestream["footnotes"] == [{"n": 1, "text": "[TR] 1 A note about the poem."}]
    assert anchor_map["verses"] == ["V001"]


# ===========================================================================
# (g) outer verse cites a footnote whose DEFINITION embeds a SECOND verse
#     (r6 finding 2) -- the inner verse must be referenced (not orphaned)
#     yet is stripped-not-rendered from the footnote text. This is the
#     closest analog to assemble.test.py's own
#     test_footnote_cited_in_verse_content_whose_def_embeds_a_verse_is_not_orphan,
#     one level up (mount=block outer verse here, vs. mount=embedded there).
#
# CRITICAL (per the plan): assert POST-fix SUCCESS + inner verse NOT
# orphaned + N in fnrefs. Do NOT assert the specific pre-fix failure REASON
# -- pre-fix this case raised AssembleError(reason="orphan_verse"), but only
# the fact that it now succeeds is pinned, never that string.
# ===========================================================================


def test_case_g_outer_verse_cites_footnote_whose_def_embeds_inner_verse(tmp_path, extract_mod):
    # (1) The assembly must not raise at all -- this alone proves the inner
    # verse (V002) was not fatally orphaned. No try/except: an unhandled
    # AssembleError here fails the test, which is exactly the assertion.
    nodestream, anchor_map = _assemble_case("g", tmp_path, extract_mod)

    assert _node_id_fnrefs(nodestream) == [
        ("HEAD:seg01", []),
        ("VERSE:seg01:0001", [1]),
    ], "only ONE verse-bearing node exists -- the inner verse (V002) never becomes its own node"
    assert nodestream["footnotes"] == [
        {"n": 1, "text": "[TR] 1 A note quoting another poem: "}
    ], "the inner verse's placeholder inside the footnote def text is stripped, leaving a trailing space"

    # (2) The outer verse renders normally.
    assert "V001" in anchor_map["verses"]
    # (3) The inner verse is referenced-only -- never independently in any
    # node's own verses[], proving "stripped-not-rendered" (r7 finding 1).
    assert "V002" not in anchor_map["verses"]

    # (4) The VERSE:seg01:0001 node's own `verses` field carries exactly one
    # entry, vid == "V001" -- V002 never appears in ANY node's verses list,
    # only referenced via the footnote text scan.
    verse_node = next(n for n in nodestream["nodes"] if n["id"] == "VERSE:seg01:0001")
    assert [v["vid"] for v in verse_node["verses"]] == ["V001"]
