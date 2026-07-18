"""Tests for scripts/occ_index.py -- the offset-preserving source occurrence
index (Phase 0, RFC #215, plan §0c) and its ``production_occurrences()``
matcher-authentication helper, which Phase 1's ``evidence_verify.py`` binds
every stored ``canon_senses.json`` evidence span against.

Module under test lives outside any Python package (a standalone script
copied to ``${durable_root}/scripts/`` at runtime, sibling of
``bootstrap_names.py``, whose offset-preserving production tokenizer/matcher
it reuses -- never reimplements), so it is loaded here via importlib from its
real path, with ``SCRIPTS_DIR`` temporarily on ``sys.path`` so its own
``from bootstrap_names import ...`` resolves -- mirrors
``tests/segpack_verse_mount.test.py``'s own loader.
"""
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
OCC_INDEX_SCRIPT = SCRIPTS_DIR / "occ_index.py"
BOOTSTRAP_NAMES_SCRIPT = SCRIPTS_DIR / "bootstrap_names.py"

assert OCC_INDEX_SCRIPT.is_file(), f"occ_index.py not found at {OCC_INDEX_SCRIPT}"
assert BOOTSTRAP_NAMES_SCRIPT.is_file(), f"bootstrap_names.py not found at {BOOTSTRAP_NAMES_SCRIPT}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/segpack_verse_mount.test.py's own loader: SCRIPTS_DIR
    must be on sys.path around the in-process load so a standalone script's
    own top-level ``from bootstrap_names import ...`` resolves exactly like
    it would under a real ``python3 occ_index.py`` invocation.
    """
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


bn = _load_module("bootstrap_names_for_occ_index_test", BOOTSTRAP_NAMES_SCRIPT, SCRIPTS_DIR)
occ = _load_module("occ_index_under_test", OCC_INDEX_SCRIPT, SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_lang(particles=(), stopwords=(), elision_pattern=None, has_elision=None,
              name_inventory=()):
    """Builds a ``LanguageConfig`` directly -- mirrors
    tests/bootstrap_names.test.py's own ``make_lang`` helper exactly, so
    fixtures stay comparable across both suites.
    """
    elision_re = re.compile(elision_pattern) if elision_pattern else None
    if has_elision is None:
        has_elision = elision_re is not None
    return bn.LanguageConfig(
        path=Path("<test-fixture>"),
        particles=frozenset(p.lower() for p in particles),
        stopwords=frozenset(stopwords),
        elision_re=elision_re,
        has_elision=has_elision,
        raw_bytes=b"{}",
        name_inventory=frozenset(name_inventory),
    )


FR_ELISION_PATTERN = r"^([dl])['’]([A-ZÀÂÄÆÇÉÈÊËÎÏÔŒÖÙÛÜŸ].*)$"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# production_occurrences() -- offset round-trip (repeated surfaces,
# variable-length sentinels, elision, multi-token, non-BMP, block-boundary)
# ---------------------------------------------------------------------------

def test_production_occurrences_round_trip_exact_substring():
    lang = make_lang()
    text = "Jean marchait. Jean revint."
    spans = occ.production_occurrences("Jean", text, lang)
    assert len(spans) == 2
    for start, end in spans:
        assert text[start:end] == "Jean"


def test_production_occurrences_repeated_surfaces_get_distinct_spans():
    lang = make_lang()
    text = "Jean vit Jean."
    spans = occ.production_occurrences("Jean", text, lang)
    assert len(spans) == 2
    assert spans[0] != spans[1]
    assert len({spans[0], spans[1]}) == 2


def test_production_occurrences_multitoken_name_span_covers_whole_run():
    lang = make_lang()
    text = "Jean Valjean marchait dans la rue."
    spans = occ.production_occurrences("Jean Valjean", text, lang)
    assert len(spans) == 1
    start, end = spans[0]
    assert text[start:end] == "Jean Valjean"


def test_production_occurrences_particle_continuation_multiword_span():
    lang = make_lang(particles=["de"])
    text = "Il visita le Chateau de Versailles hier."
    spans = occ.production_occurrences("Chateau de Versailles", text, lang)
    assert len(spans) == 1
    start, end = spans[0]
    assert text[start:end] == "Chateau de Versailles"


def test_production_occurrences_variable_length_sentinel_offsets_stay_aligned():
    # Sentinels of very different bracketed lengths sit between/around the
    # names -- an equal-length mask (not a collapsing substitution) must keep
    # every later offset aligned to THIS raw, un-masked text.
    lang = make_lang()
    text = "Jean⟦FNREF_1⟧ vit Marie⟦VERSE_seg02_ab12cd34ef⟧ hier."
    jean_spans = occ.production_occurrences("Jean", text, lang)
    assert len(jean_spans) == 1
    j_start, j_end = jean_spans[0]
    assert text[j_start:j_end] == "Jean"

    marie_spans = occ.production_occurrences("Marie", text, lang)
    assert len(marie_spans) == 1
    m_start, m_end = marie_spans[0]
    assert text[m_start:m_end] == "Marie"


def test_production_occurrences_non_bmp_unicode_offsets():
    # A non-BMP astral codepoint (U+1F600, 1 Python str index under PEP 393)
    # precedes the occurrence -- proves offsets are codepoint-based, never
    # accidentally computed in UTF-16 code units.
    lang = make_lang()
    text = "\U0001F600 Jean arriva."
    spans = occ.production_occurrences("Jean", text, lang)
    assert len(spans) == 1
    start, end = spans[0]
    assert (start, end) == (2, 6)
    assert text[start:end] == "Jean"


def test_production_occurrences_block_boundary_start_and_end():
    lang = make_lang()
    text = "Jean"
    assert occ.production_occurrences("Jean", text, lang) == [(0, 4)]


def test_production_occurrences_elision_child_span():
    lang = make_lang(elision_pattern=FR_ELISION_PATTERN)
    text = "Le marquis d'Effiat arriva."
    spans = occ.production_occurrences("Effiat", text, lang)
    assert len(spans) == 1
    start, end = spans[0]
    assert text[start:end] == "Effiat"
    assert (start, end) == (13, 19)


def test_production_occurrences_in_bounds_substring_not_a_run_is_absent():
    # "ean" is a genuine in-bounds substring of "Jean" but the matcher never
    # emits it as a completed run -- matcher-authentication must not accept a
    # mere in-bounds substring.
    lang = make_lang()
    text = "Jean vit Paul."
    assert occ.production_occurrences("ean", text, lang) == []


def test_production_occurrences_absent_source_form_returns_empty():
    lang = make_lang()
    text = "Jean vit Paul."
    assert occ.production_occurrences("Georges", text, lang) == []


# ---------------------------------------------------------------------------
# Elision/no-elision matcher parity (R5 F1) -- config-parameterized, paired,
# identical bytes. Proves production_occurrences is NOT substring-based.
# ---------------------------------------------------------------------------

def test_elision_no_elision_matcher_parity_identical_bytes():
    text = "d'Effiat arriva."
    with_elision = make_lang(elision_pattern=FR_ELISION_PATTERN)
    without_elision = make_lang(elision_pattern=None, has_elision=False)

    with_spans = occ.production_occurrences("Effiat", text, with_elision)
    assert with_spans == [(2, 8)]
    assert text[2:8] == "Effiat"

    without_spans = occ.production_occurrences("Effiat", text, without_elision)
    assert without_spans == [], (
        "without elision configured, 'Effiat' is NOT a production span"
    )

    # Without elision, the fused token "d'Effiat" is ALSO never a production
    # span here -- it is lowercase-initial, so is_upper_initial() rejects it
    # as a run-start and it is silently and totally dropped (the documented
    # french-elision-tokenizer-miss behavior bootstrap_names.test.py itself
    # locks in as test_regression_elided_names_would_be_silently_dropped_
    # without_split). This is NOT a substring-fallback bug in
    # production_occurrences -- it is the real production matcher's own
    # documented behavior; a bare-substring fallback would have wrongly
    # accepted "d'Effiat" (or "Effiat") here regardless.
    assert occ.production_occurrences("d'Effiat", text, without_elision) == []


def test_elision_no_elision_matcher_parity_never_a_two_arg_call():
    # A 2-arg no-config helper would necessarily fail one half of the pair
    # above (it can't know which config to honor). Assert the real function
    # requires the third positional/keyword argument.
    import inspect
    sig = inspect.signature(occ.production_occurrences)
    assert len(sig.parameters) == 3


# ---------------------------------------------------------------------------
# Occurrence record shape -- {source_form, block, seg, char_start, char_end,
# quote, context_start, context_end, context_sha256}
# ---------------------------------------------------------------------------

def test_build_occurrence_records_shape_and_hash():
    lang = make_lang()
    text = "Jean marchait. Jean revint."
    records = occ.build_occurrence_records("Jean", "PARA:seg01:0001", "seg01", text, lang)
    assert len(records) == 2
    for rec in records:
        assert rec["source_form"] == "Jean"
        assert rec["block"] == "PARA:seg01:0001"
        assert rec["seg"] == "seg01"
        assert rec["quote"] == text[rec["char_start"]:rec["char_end"]] == "Jean"
        assert rec["context_start"] <= rec["char_start"] < rec["char_end"] <= rec["context_end"]
        context_bytes = text[rec["context_start"]:rec["context_end"]].encode("utf-8")
        assert rec["context_sha256"] == _sha256_hex(context_bytes)
    # a distinct span per repeat -- no two occurrence records collapse onto
    # the same span.
    assert records[0]["char_start"] != records[1]["char_start"]


def test_build_occurrence_records_context_is_whole_block_not_nfc():
    # NFC-vs-raw-bytes: an NFD-decomposed accented block must hash its own
    # exact raw bytes, never an NFC-normalized re-encoding.
    lang = make_lang()
    text = "Jean vit René hier."  # "Rene" + combining acute (NFD)
    records = occ.build_occurrence_records("Jean", "PARA:seg01:0002", "seg01", text, lang)
    assert len(records) == 1
    rec = records[0]
    assert (rec["context_start"], rec["context_end"]) == (0, len(text))
    assert rec["context_sha256"] == _sha256_hex(text.encode("utf-8"))
    # sanity: the NFD form's raw bytes differ from its NFC re-encoding, so a
    # hash-of-NFC implementation would NOT match this assertion.
    import unicodedata
    assert text.encode("utf-8") != unicodedata.normalize("NFC", text).encode("utf-8")


def test_build_occurrence_records_seg_null_indexed_by_block():
    lang = make_lang()
    text = "Jean arriva."
    records = occ.build_occurrence_records("Jean", "FRONTBACK:fm01", None, text, lang)
    assert len(records) == 1
    assert records[0]["seg"] is None
    assert records[0]["block"] == "FRONTBACK:fm01"


def test_build_occurrence_records_no_occurrence_yields_empty_list():
    lang = make_lang()
    text = "Marie arriva."
    assert occ.build_occurrence_records("Jean", "PARA:seg01:0001", "seg01", text, lang) == []


# ---------------------------------------------------------------------------
# index_manifest() -- full manifest walk (block ids from blocks{} keys,
# seg:null blocks preserved, offsets stay per-block)
# ---------------------------------------------------------------------------

def _write_manifest(tmp_path, blocks):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"blocks": blocks}), encoding="utf-8")
    return path


def test_index_manifest_walks_every_block_and_preserves_seg_null(tmp_path):
    lang = make_lang()
    blocks = {
        "PARA:seg01:0001": {
            "id": "PARA:seg01:0001", "type": "PARA", "seg": "seg01",
            "order_index": 0, "source_file": "x.txt",
            "plain_text": "Jean arriva.", "sha1": "deadbeef",
        },
        "FRONTBACK:fm01": {
            "id": "FRONTBACK:fm01", "type": "FRONTBACK", "seg": None,
            "order_index": 1, "source_file": "x.txt",
            "plain_text": "Jean partit.", "sha1": "deadbeef2",
        },
    }
    manifest_path = _write_manifest(tmp_path, blocks)
    records = occ.index_manifest(manifest_path, ["Jean"], lang)
    by_block = {r["block"]: r for r in records}
    assert len(records) == 2
    assert by_block["PARA:seg01:0001"]["seg"] == "seg01"
    assert by_block["FRONTBACK:fm01"]["seg"] is None
    # offsets are per-block, not accumulated across the manifest walk.
    for rec in records:
        assert rec["char_start"] == 0


def test_index_manifest_skips_empty_and_whitespace_only_blocks(tmp_path):
    lang = make_lang()
    blocks = {
        "PARA:seg01:0001": {
            "id": "PARA:seg01:0001", "type": "PARA", "seg": "seg01",
            "order_index": 0, "source_file": "x.txt",
            "plain_text": "Jean arriva.", "sha1": "a",
        },
        "PARA:seg01:0002": {
            "id": "PARA:seg01:0002", "type": "PARA", "seg": "seg01",
            "order_index": 1, "source_file": "x.txt",
            "plain_text": "   ", "sha1": "b",
        },
    }
    manifest_path = _write_manifest(tmp_path, blocks)
    records = occ.index_manifest(manifest_path, ["Jean"], lang)
    assert {r["block"] for r in records} == {"PARA:seg01:0001"}


def test_index_manifest_multiple_forms_across_blocks(tmp_path):
    lang = make_lang()
    blocks = {
        "PARA:seg01:0001": {
            "id": "PARA:seg01:0001", "type": "PARA", "seg": "seg01",
            "order_index": 0, "source_file": "x.txt",
            "plain_text": "Jean vit Marie.", "sha1": "a",
        },
        "PARA:seg01:0002": {
            "id": "PARA:seg01:0002", "type": "PARA", "seg": "seg01",
            "order_index": 1, "source_file": "x.txt",
            "plain_text": "Marie partit seule.", "sha1": "b",
        },
    }
    manifest_path = _write_manifest(tmp_path, blocks)
    records = occ.index_manifest(manifest_path, ["Jean", "Marie"], lang)
    names_by_block = {}
    for rec in records:
        names_by_block.setdefault(rec["block"], set()).add(rec["source_form"])
    assert names_by_block["PARA:seg01:0001"] == {"Jean", "Marie"}
    assert names_by_block["PARA:seg01:0002"] == {"Marie"}


class _CountingFormsList(list):
    """A ``list`` subclass that counts how many times it is iterated --
    used to prove ``index_manifest()`` no longer walks the WHOLE
    ``source_forms`` list once per block (finding 8, RFC #215 Phase 0
    review round 4). Duck-types as ``source_forms``'s normal ``list``
    argument in every other respect.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.iter_count = 0

    def __iter__(self):
        self.iter_count += 1
        return super().__iter__()


# ---------------------------------------------------------------------------
# Performance regression: index_manifest() must run the extraction primitive
# (_run_spans() -> bootstrap_names.extract_candidate_spans()) exactly ONCE
# per block, never once per (block, source_form) pair -- a codex-rescue
# adversarial review found the un-fixed code re-ran the full tokenizer/
# run-building pass per source_form, ~100x waste measured on 100 forms over
# one block (0.569s vs 0.0013s for a single extraction of that block).
#
# Finding 8 (round 4): even with the extraction primitive fixed, the loop
# still probed EVERY name in ``source_forms`` on EVERY block regardless of
# whether it ever occurred there -- O(blocks x forms) PER-FORM probes, even
# though most forms don't occur in most blocks. The strengthened assertion
# below counts how many times ``source_forms`` itself is iterated: the
# un-fixed code iterated it once INSIDE the per-block loop (so
# ``iter_count == n_blocks``); the fix must iterate it exactly once
# OVERALL (building a rank map before the per-block loop), so
# ``iter_count == 1`` regardless of ``n_blocks``.
# ---------------------------------------------------------------------------

def test_index_manifest_calls_extraction_primitive_once_per_block_not_per_form(tmp_path, monkeypatch):
    lang = make_lang()
    n_blocks = 3
    blocks = {
        f"PARA:seg01:{i:04d}": {
            "id": f"PARA:seg01:{i:04d}", "type": "PARA", "seg": "seg01",
            "order_index": i, "source_file": "x.txt",
            "plain_text": "Jean vit Marie et Paul.", "sha1": f"h{i}",
        }
        for i in range(n_blocks)
    }
    manifest_path = _write_manifest(tmp_path, blocks)
    source_forms = _CountingFormsList(f"Form{i}" for i in range(50))

    call_count = {"n": 0}
    real_extract = bn.extract_candidate_spans

    def counting_extract(text, language_config):
        call_count["n"] += 1
        return real_extract(text, language_config)

    monkeypatch.setattr(occ, "extract_candidate_spans", counting_extract)
    occ.index_manifest(manifest_path, source_forms, lang)
    assert call_count["n"] == n_blocks, (
        f"expected exactly one extraction call per block ({n_blocks}), got "
        f"{call_count['n']} -- extraction is being re-run per source_form"
    )
    assert source_forms.iter_count == 1, (
        f"source_forms was iterated {source_forms.iter_count} times -- "
        f"expected exactly 1 (not once per block, {n_blocks} blocks): "
        "the per-form probe must scale with matched-name count, not with "
        "len(source_forms) x n_blocks (finding 8)"
    )


def test_index_manifest_matches_per_form_build_occurrence_records_output(tmp_path):
    # Zero-output-regression cross-check: index_manifest()'s grouped-by-block
    # fast path must produce IDENTICAL records to calling
    # build_occurrence_records() once per (block, source_form) pair -- same
    # records, same order.
    lang = make_lang(particles=["de"])
    blocks = {
        "PARA:seg01:0001": {
            "id": "PARA:seg01:0001", "type": "PARA", "seg": "seg01",
            "order_index": 0, "source_file": "x.txt",
            "plain_text": "Jean vit Marie. Jean Valjean marchait. Marie revint.",
            "sha1": "a",
        },
        "PARA:seg01:0002": {
            "id": "PARA:seg01:0002", "type": "PARA", "seg": "seg01",
            "order_index": 1, "source_file": "x.txt",
            "plain_text": "Le Chateau de Versailles etait vide.",
            "sha1": "b",
        },
    }
    manifest_path = _write_manifest(tmp_path, blocks)
    source_forms = ["Jean", "Marie", "Jean Valjean", "Chateau de Versailles", "Absent"]

    fast = occ.index_manifest(manifest_path, source_forms, lang)

    expected = []
    for block_id, seg, text in occ.iter_manifest_blocks(manifest_path):
        for source_form in source_forms:
            expected.extend(
                occ.build_occurrence_records(source_form, block_id, seg, text, lang)
            )

    assert fast == expected


# ---------------------------------------------------------------------------
# A-C6 (this train, #238/#241) -- production_occurrences() stays mark/
# connector-SENSITIVE, deliberately. Pins the honest, documented residual
# rather than a wished-for behavior: occurrence_targets.py now correctly
# indexes a pointed/maqaf-joined occurrence in the ## Mentions appendix (see
# tests/occurrence_targets.test.py's own #238 SCOPE test), but THIS module
# does not -- see occ_index.py's own module docstring for the rationale
# (A-C6 = NO, lead-decisions.md) and the deferred-follow-up issue.
# ---------------------------------------------------------------------------

def test_A_C6_production_occurrences_finds_the_raw_emitted_occurrence():
    lang = make_lang(name_inventory=["משה לייב"])
    text = "ראה מֹשֶׁה־לַיִיב אתמול."
    # the production matcher DOES find the occurrence -- under its own raw,
    # unfolded emitted name (Contract 5).
    spans = occ.production_occurrences("מֹשֶׁה־לַיִיב", text, lang)
    assert len(spans) == 1
    s, e = spans[0]
    assert text[s:e] == "מֹשֶׁה־לַיִיב"


def test_A_C6_production_occurrences_misses_the_unfolded_canon_source_form():
    """The documented residual: an exact lookup by the canon's own unfolded
    source_form finds NOTHING for a pointed/maqaf-joined occurrence -- this
    is what A-C6 = NO leaves unresolved this train, deliberately.

    HONESTY NOTE (not independently red-before-green): this assertion is
    ALSO true on pre-#238/#241 code, but for an unrelated reason (the
    matcher found nothing at all pre-fix, vs. found-under-a-different-name
    post-fix). It only documents the intended residual when read alongside
    test_A_C6_production_occurrences_finds_the_raw_emitted_occurrence
    (which IS red-before-green) -- that companion test is what proves the
    matcher actually recalls the occurrence now; THIS test proves the
    unfolded lookup still can't find it under its own canon key."""
    lang = make_lang(name_inventory=["משה לייב"])
    text = "ראה מֹשֶׁה־לַיִיב אתמול."
    assert occ.production_occurrences("משה לייב", text, lang) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
