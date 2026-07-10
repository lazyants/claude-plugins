"""tests/verse_footnote_corpus_validate.test.py -- #106 verse x footnote
corpus, VALIDATE_DRAFT layer only.

Drives all 7 corpus cases (`verse_footnote_corpus.CASES`, keyed "a".."g" --
see that module's docstring for the full verse-mount x footnote-citation-site
cross-product) through the REAL pipeline: extract.py.template's `build()` ->
segpack.py's `build_pack()` -> the shared, already-verified
`draft_for()` draft builder -> the REAL `validate_draft.py`, invoked as a
subprocess exactly as production does (mirroring `validate_draft.test.py`'s
own `make_durable_root`/`write_segment`/`run_validate` harness, here provided
by the corpus helper as `make_validate_root`/`write_validate_segment`/
`run_validate_draft`).

Every case must clear validate_draft.py's gate with exit 0 and an
`[seg01] OK  blocks=N fn=N verses=N -- coverage+placeholders+content clean`
summary line. The exact `blocks=`/`fn=`/`verses=` counts pin check 3's
per-block verse bijection, check 4's footnote coverage, and check 5's
verse-content-completeness for every case in the cross-product -- not just
"it passed", but the precise coverage shape the #106 build plan requires.

Sibling layers (`verse_footnote_corpus_extract.test.py`,
`verse_footnote_corpus_segpack.test.py`) exercise the extract/segpack layers
directly; this file only exercises the validate_draft.py gate.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

CORPUS_PATH = Path(__file__).resolve().parent / "verse_footnote_corpus.py"


def _load_corpus():
    spec = importlib.util.spec_from_file_location("verse_footnote_corpus_for_validate_test", CORPUS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus = _load_corpus()


@pytest.fixture
def extract_mod(tmp_path):
    return corpus.load_extract_module(tmp_path)


def _run_case_through_validate(label: str, extract_mod, tmp_path: Path):
    """Drives one corpus case through extract -> segpack -> draft_for ->
    validate_draft.py, exactly per the module docstring. Returns the
    subprocess.CompletedProcess from validate_draft.py."""
    case = corpus.CASES[label]
    manifest, _report, _max_words = corpus.manifest_for(case, extract_mod, tmp_path)
    pack = corpus.segpack_for("seg01", manifest, case.apparatus_policy, corpus.LANG_CONFIG)
    draft = corpus.draft_for(manifest, pack)

    root = corpus.make_validate_root(tmp_path, case.label)
    corpus.write_validate_segment(root, "seg01", pack, draft)
    return corpus.run_validate_draft(root, "seg01")


def _assert_clean_pass(result, expected_coverage: str) -> None:
    assert result.returncode == 0, (
        f"expected validate_draft.py to pass, got rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[seg01] OK" in result.stdout
    assert expected_coverage in result.stdout, (
        f"expected coverage shape {expected_coverage!r} in stdout, got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# One test per corpus case -- exact blocks=/fn=/verses= counts, verified
# against the real post-#92/#93/#96 pipeline immediately before this file
# was written.
# ---------------------------------------------------------------------------


def test_case_a_prose_block_body_footnote_no_verse(extract_mod, tmp_path):
    """(a) prose block + body footnote ref, no verse."""
    result = _run_case_through_validate("a", extract_mod, tmp_path)
    _assert_clean_pass(result, "blocks=2 fn=1 verses=0")


def test_case_b_embedded_verse_footnote_in_surrounding_prose(extract_mod, tmp_path):
    """(b) embedded verse (mount=embedded), footnote ref in the surrounding
    prose (not inside the verse)."""
    result = _run_case_through_validate("b", extract_mod, tmp_path)
    _assert_clean_pass(result, "blocks=3 fn=1 verses=1")


def test_case_c_footnote_def_embeds_verse(extract_mod, tmp_path):
    """(c) footnote definition text that embeds a verse (mount=embedded,
    context=footnote) -- segpack's footnote_def_block_ids pickup."""
    result = _run_case_through_validate("c", extract_mod, tmp_path)
    _assert_clean_pass(result, "blocks=2 fn=1 verses=1")


def test_case_d_standalone_verse_no_footnote(extract_mod, tmp_path):
    """(d) standalone verse block (mount=block), no footnote."""
    result = _run_case_through_validate("d", extract_mod, tmp_path)
    _assert_clean_pass(result, "blocks=2 fn=0 verses=1")


def test_case_e_standalone_verse_own_text_carries_footnote(extract_mod, tmp_path):
    """(e) standalone verse block (mount=block) whose own verse text carries
    a footnote ref (r4 #93 end-to-end fix)."""
    result = _run_case_through_validate("e", extract_mod, tmp_path)
    _assert_clean_pass(result, "blocks=2 fn=1 verses=1")


def test_case_f_footnote_cited_inside_embedded_verse(extract_mod, tmp_path):
    """(f) footnote ref cited inside an embedded verse (mount=embedded) --
    the verse's own translated content carries the FNREF, not the
    surrounding prose (segpack point-1 discovery)."""
    result = _run_case_through_validate("f", extract_mod, tmp_path)
    _assert_clean_pass(result, "blocks=3 fn=1 verses=1")


def test_case_g_outer_verse_cites_footnote_def_embedding_second_verse(extract_mod, tmp_path):
    """(g) outer verse cites a footnote whose definition embeds a second
    verse (r6 finding 2) -- the inner verse must be marked referenced (not
    orphaned) yet is stripped-not-rendered from the footnote text."""
    result = _run_case_through_validate("g", extract_mod, tmp_path)
    _assert_clean_pass(result, "blocks=2 fn=1 verses=2")


# ---------------------------------------------------------------------------
# Sanity check (not per-case): a clean pass must actually name its segment
# and must not be masking a stray WARN/ERROR on stderr behind the summary
# line's "clean" verdict.
# ---------------------------------------------------------------------------


def test_case_g_clean_pass_names_segment_with_no_stray_stderr_noise(extract_mod, tmp_path):
    result = _run_case_through_validate("g", extract_mod, tmp_path)

    assert result.returncode == 0
    assert "[seg01]" in result.stdout
    assert result.stderr == "", f"expected empty stderr, got:\n{result.stderr}"
    assert "WARN" not in result.stderr
    assert "ERROR" not in result.stderr


if __name__ == "__main__":
    import sys

    import pytest as _pytest

    sys.exit(_pytest.main([__file__, "-v"]))
