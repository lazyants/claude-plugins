"""Tests for RFC #215 Phase 0a/0b/0c: caseless multiword surfacing (the
inventory-driven bypass), the optional `name_inventory` 5th language-config
key, and the offset-preserving occurrence spans `bootstrap_names.py`'s
`extract_candidate_spans()`/`tokenize()` now emit.

Covers BOTH extraction implementations (`bootstrap_names.py` -- the
production extractor `occ_index.py`'s `production_occurrences()` reuses --
and `language_smoke_report.py`'s deliberately separate re-implementation),
since 0a/0b require parity between the two (see
`extractor_terminators_drift.test.py` for the sibling shared-constant drift
guard this file does not duplicate).

Module under test lives outside any Python package (a standalone script
copied to ``${durable_root}/scripts/`` at runtime), so both are loaded here
via ``importlib`` from their real paths -- same mechanism as
``bootstrap_names.test.py``/``extractor_terminators_drift.test.py``.
"""
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
BOOTSTRAP_PATH = SCRIPTS_DIR / "bootstrap_names.py"
SMOKE_PATH = SCRIPTS_DIR / "language_smoke_report.py"
REAL_LANGUAGES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "languages"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bn = _load_module("bootstrap_names_caseless_offset_under_test", BOOTSTRAP_PATH)
lsr = _load_module("language_smoke_report_caseless_offset_under_test", SMOKE_PATH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def bn_lang(particles=(), stopwords=(), elision_pattern=None, has_elision=None,
            name_inventory=()):
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


def lsr_lang(particles=(), stopwords=(), elision_pattern=None, has_elision=None,
             name_inventory=()):
    elision_re = re.compile(elision_pattern) if elision_pattern else None
    if has_elision is None:
        has_elision = elision_re is not None
    return {
        "raw_bytes": b"{}",
        "particles": list(particles),
        "particles_lower": {p.lower() for p in particles},
        "stopwords": set(stopwords),
        "has_elision": has_elision,
        "elision_re": elision_re,
        "name_inventory": frozenset(name_inventory),
    }


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


FR_ELISION_PATTERN = r"^([dl])['’](.+)$"

# A synthetic two-word Hebrew name (no real grammatical claim intended --
# purely a script with NO case distinction at all, category 'Lo', to exercise
# the documented #204 bug: is_upper_initial() gates on ASCII/'Lu' and can
# never see a candidate in a script that has no uppercase letters).
HEBREW_NAME = "משה לייב"
HEBREW_TEXT_MATCH = "ראה משה לייב אתמול."
HEBREW_TEXT_BOUNDARY = "משה. לייב הלך."


# ---------------------------------------------------------------------------
# 0b -- name_inventory optional 5th config key, both loaders
# ---------------------------------------------------------------------------

def test_bootstrap_load_language_config_parses_name_inventory(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(languages_dir / "he.json", {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
        "name_inventory": [HEBREW_NAME],
    })
    lang = bn.load_language_config("he.json", languages_dir=languages_dir)
    assert lang.name_inventory == frozenset({HEBREW_NAME})


def test_bootstrap_load_language_config_name_inventory_absent_defaults_empty(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(languages_dir / "he.json", {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
    })
    lang = bn.load_language_config("he.json", languages_dir=languages_dir)
    assert lang.name_inventory == frozenset()


def test_bootstrap_load_language_config_name_inventory_null_defaults_empty(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(languages_dir / "he.json", {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
        "name_inventory": None,
    })
    lang = bn.load_language_config("he.json", languages_dir=languages_dir)
    assert lang.name_inventory == frozenset()


def test_bootstrap_load_language_config_rejects_non_list_name_inventory(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(languages_dir / "bad.json", {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
        "name_inventory": HEBREW_NAME,  # a bare string, not an array
    })
    with pytest.raises(bn.BootstrapNamesError, match="name_inventory"):
        bn.load_language_config("bad.json", languages_dir=languages_dir)


def test_bootstrap_load_language_config_rejects_blank_name_inventory_entry(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(languages_dir / "bad.json", {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
        "name_inventory": [HEBREW_NAME, "   "],
    })
    with pytest.raises(bn.BootstrapNamesError, match="name_inventory"):
        bn.load_language_config("bad.json", languages_dir=languages_dir)


def test_bootstrap_shipped_latin_presets_unaffected_by_name_inventory():
    # 0b: shipped Latin files are unchanged -- name_inventory stays empty.
    for filename in ("fr.json", "de.json", "es.json", "it.json"):
        lang = bn.load_language_config(filename, languages_dir=REAL_LANGUAGES_DIR)
        assert lang.name_inventory == frozenset(), filename


def test_lsr_load_particle_config_parses_name_inventory(tmp_path):
    path = tmp_path / "he.json"
    _write_json(path, {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
        "name_inventory": [HEBREW_NAME],
    })
    lang = lsr.load_particle_config(path)
    assert lang["name_inventory"] == frozenset({HEBREW_NAME})


def test_lsr_load_particle_config_name_inventory_absent_defaults_empty(tmp_path):
    path = tmp_path / "he.json"
    _write_json(path, {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
    })
    lang = lsr.load_particle_config(path)
    assert lang["name_inventory"] == frozenset()


def test_lsr_load_particle_config_rejects_non_list_name_inventory(tmp_path):
    path = tmp_path / "bad.json"
    _write_json(path, {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
        "name_inventory": 42,
    })
    with pytest.raises(SystemExit):
        lsr.load_particle_config(path)


# ---------------------------------------------------------------------------
# Finding 7 (RFC #215 Phase 0 review round 4 -- robustness): both loaders
# validate the known keys but did not reject UNKNOWN ones -- so a typo like
# "name_inventroy" loaded with an EMPTY name_inventory, silently disabling
# Phase 0's caseless bypass with no error at all.
# ---------------------------------------------------------------------------

def test_bootstrap_load_language_config_rejects_name_inventory_typo(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(languages_dir / "typo.json", {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
        "name_inventroy": [HEBREW_NAME],  # typo: should be name_inventory
    })
    with pytest.raises(bn.BootstrapNamesError, match="name_inventroy"):
        bn.load_language_config("typo.json", languages_dir=languages_dir)


def test_bootstrap_load_language_config_rejects_unrelated_unknown_key(tmp_path):
    # Not just a name_inventory typo -- ANY unrecognized key is rejected,
    # e.g. the historiettes-t3-only CONTRACTION_RE LanguageConfig's own
    # docstring explicitly documents as OUT of this contract.
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(languages_dir / "bad.json", {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
        "CONTRACTION_RE": "^(J|C)'.*$",
    })
    with pytest.raises(bn.BootstrapNamesError, match="CONTRACTION_RE"):
        bn.load_language_config("bad.json", languages_dir=languages_dir)


def test_lsr_load_particle_config_rejects_name_inventory_typo(tmp_path):
    path = tmp_path / "typo.json"
    _write_json(path, {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
        "name_inventroy": [HEBREW_NAME],
    })
    with pytest.raises(SystemExit):
        lsr.load_particle_config(path)


def test_lsr_load_particle_config_rejects_unrelated_unknown_key(tmp_path):
    path = tmp_path / "bad.json"
    _write_json(path, {
        "PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None,
        "CONTRACTION_RE": "^(J|C)'.*$",
    })
    with pytest.raises(SystemExit):
        lsr.load_particle_config(path)


# ---------------------------------------------------------------------------
# 0a -- caseless multiword surfacing via the inventory route, BOTH extractors
# ---------------------------------------------------------------------------

def test_bootstrap_extract_candidate_spans_caseless_hebrew_via_inventory():
    lang = bn_lang(name_inventory=[HEBREW_NAME])
    out = bn.extract_candidate_spans(HEBREW_TEXT_MATCH, lang)
    names = [n for n, _mid, _s, _e in out]
    assert HEBREW_NAME in names


def test_bootstrap_extract_candidate_spans_hebrew_zero_candidates_without_inventory():
    # Pins the documented #204 bug: is_upper_initial() gates on ASCII/'Lu';
    # Hebrew letters are category 'Lo' -- with no name_inventory configured,
    # this script surfaces NOTHING for a Hebrew source at all.
    lang = bn_lang(name_inventory=[])
    out = bn.extract_candidate_spans(HEBREW_TEXT_MATCH, lang)
    assert out == []


def test_bootstrap_extract_candidate_spans_inventory_does_not_bridge_sentence_boundary():
    lang = bn_lang(name_inventory=[HEBREW_NAME])
    out = bn.extract_candidate_spans(HEBREW_TEXT_BOUNDARY, lang)
    names = [n for n, _mid, _s, _e in out]
    assert HEBREW_NAME not in names


def test_lsr_extract_candidate_names_caseless_hebrew_via_inventory():
    lang = lsr_lang(name_inventory=[HEBREW_NAME])
    out = lsr.extract_candidate_names(HEBREW_TEXT_MATCH, lang)
    names = [n for n, _mid in out]
    assert HEBREW_NAME in names


def test_lsr_extract_candidate_names_hebrew_zero_candidates_without_inventory():
    lang = lsr_lang(name_inventory=[])
    out = lsr.extract_candidate_names(HEBREW_TEXT_MATCH, lang)
    assert out == []


def test_lsr_extract_candidate_names_inventory_does_not_bridge_sentence_boundary():
    lang = lsr_lang(name_inventory=[HEBREW_NAME])
    out = lsr.extract_candidate_names(HEBREW_TEXT_BOUNDARY, lang)
    names = [n for n, _mid in out]
    assert HEBREW_NAME not in names


def test_caseless_multiword_parity_between_both_extractors_match_case():
    # "assert the config is parsed, returned, AND changes extraction output
    # in BOTH bootstrap_names and language_smoke_report" (plan Tests section).
    bn_names = {n for n, _m, _s, _e in bn.extract_candidate_spans(HEBREW_TEXT_MATCH, bn_lang(name_inventory=[HEBREW_NAME]))}
    lsr_names = {n for n, _m in lsr.extract_candidate_names(HEBREW_TEXT_MATCH, lsr_lang(name_inventory=[HEBREW_NAME]))}
    assert HEBREW_NAME in bn_names
    assert bn_names == lsr_names


def test_caseless_multiword_parity_between_both_extractors_boundary_case():
    # "assert the form is NOT matched across sentence punctuation (parity
    # in both)" (plan Tests section).
    bn_names = {n for n, _m, _s, _e in bn.extract_candidate_spans(HEBREW_TEXT_BOUNDARY, bn_lang(name_inventory=[HEBREW_NAME]))}
    lsr_names = {n for n, _m in lsr.extract_candidate_names(HEBREW_TEXT_BOUNDARY, lsr_lang(name_inventory=[HEBREW_NAME]))}
    assert HEBREW_NAME not in bn_names
    assert bn_names == lsr_names


def test_bootstrap_inventory_route_does_not_double_count_capitalized_match():
    # A name_inventory entry that ALSO satisfies the ordinary capitalized-run
    # algorithm must be surfaced exactly ONCE (pass 2 skips tokens pass 1
    # already claimed) -- not once per route.
    lang = bn_lang(name_inventory=["Jean Valjean"])
    out = bn.extract_candidate_spans("Jean Valjean marchait dans la rue.", lang)
    matches = [r for r in out if r[0] == "Jean Valjean"]
    assert len(matches) == 1


def test_bootstrap_inventory_route_prefers_longest_form_at_a_position():
    lang = bn_lang(name_inventory=[HEBREW_NAME, "משה"])
    out = bn.extract_candidate_spans(HEBREW_TEXT_MATCH, lang)
    names = [n for n, _m, _s, _e in out]
    assert HEBREW_NAME in names
    assert "משה" not in names  # the shorter form must not ALSO fire inside the longer match


# A mixed-script inventory form where pass 1's ordinary capitalized-run
# algorithm already claims ONE of the form's tokens on its own ("Cohen" is
# ASCII-uppercase-initial and satisfies is_upper_initial() by itself; "משה"
# has no case distinction at all and pass 1 can never claim it). Pass 2 must
# not treat "one token already claimed" as "reject the whole form" -- a
# PARTIAL claim must still let the full inventory form surface; only a claim
# covering EVERY one of the form's tokens (an exact duplicate of what pass 1
# already emitted) is a reason to skip.
MIXED_SCRIPT_NAME = "משה Cohen"
MIXED_SCRIPT_TEXT = "ראה משה Cohen אתמול."


def test_bootstrap_inventory_route_surfaces_full_form_despite_partial_pass1_claim():
    lang = bn_lang(name_inventory=[MIXED_SCRIPT_NAME])
    out = bn.extract_candidate_spans(MIXED_SCRIPT_TEXT, lang)
    by_name = {n: (s, e) for n, _m, s, e in out}
    assert MIXED_SCRIPT_NAME in by_name
    s, e = by_name[MIXED_SCRIPT_NAME]
    assert MIXED_SCRIPT_TEXT[s:e] == MIXED_SCRIPT_NAME
    # "Cohen" alone -- pass 1's own claim -- is allowed to coexist; it is a
    # DIFFERENT candidate name, and downstream consumers (occ_index.py's
    # production_occurrences()) match spans by exact source_form string, so
    # the two candidates are never confused with one another.
    assert "Cohen" in by_name


def test_lsr_inventory_route_surfaces_full_form_despite_partial_pass1_claim():
    lang = lsr_lang(name_inventory=[MIXED_SCRIPT_NAME])
    out = lsr.extract_candidate_names(MIXED_SCRIPT_TEXT, lang)
    names = [n for n, _mid in out]
    assert MIXED_SCRIPT_NAME in names
    assert "Cohen" in names


def test_caseless_multiword_parity_mixed_script_partial_overlap():
    bn_names = {
        n for n, _m, _s, _e in
        bn.extract_candidate_spans(MIXED_SCRIPT_TEXT, bn_lang(name_inventory=[MIXED_SCRIPT_NAME]))
    }
    lsr_names = {
        n for n, _m in
        lsr.extract_candidate_names(MIXED_SCRIPT_TEXT, lsr_lang(name_inventory=[MIXED_SCRIPT_NAME]))
    }
    assert MIXED_SCRIPT_NAME in bn_names
    assert bn_names == lsr_names


# ---------------------------------------------------------------------------
# 0a -- pass-2 suppression INVARIANT matrix (codex round 2): "pass 2 emits
# every inventory-form occurrence, suppressing ONLY an exact (name, span)
# duplicate" -- never a per-token claimed bitmap. Each row below is an
# adversarial case a bitmap-based approach was caught breaking across three
# review rounds; each is run through BOTH extractors (parity is a hard
# requirement). ``expected_present`` are the candidate NAMES that MUST
# appear in the output; the fixtures are deliberately built so pass 1 and
# pass 2 overlap, nest, or chain -- overlapping candidates for DIFFERENT
# names are correct output, not a bug (see extract_candidate_spans()'s
# INVARIANT docstring).
# ---------------------------------------------------------------------------
SUPPRESSION_MATRIX = [
    pytest.param(
        ["Cohen"], "Jean Cohen arrived.", {"Jean Cohen", "Cohen"},
        id="singleton_inside_larger_pass1_run",
    ),
    pytest.param(
        ["משה לייב", "לייב כהן"], "משה לייב כהן", {"משה לייב", "לייב כהן"},
        id="overlapping_starts_shared_boundary_token",
    ),
    pytest.param(
        ["א ב", "ב ג", "ג ד"], "א ב ג ד", {"א ב", "ב ג", "ג ד"},
        id="three_way_chained_overlap",
    ),
    pytest.param(
        ["א ב ג", "ב"], "א ב ג", {"א ב ג", "ב"},
        id="nested_form_strict_subrun",
    ),
    pytest.param(
        ["Cohen משה"], "Bonjour. Cohen משה הלך.", {"Cohen", "Cohen משה"},
        id="reversed_order_mixed_script_sentence_initial",
    ),
    pytest.param(
        ["Jean Cohen", "Jean"], "Jean Cohen arrived.", {"Jean Cohen", "Jean"},
        id="longest_duplicate_falls_back_to_shorter_fresh_form",
    ),
]


@pytest.mark.parametrize("name_inventory, text, expected_present", SUPPRESSION_MATRIX)
def test_bootstrap_suppression_invariant_matrix(name_inventory, text, expected_present):
    lang = bn_lang(name_inventory=name_inventory)
    out = bn.extract_candidate_spans(text, lang)
    by_name = {}
    for n, _mid, s, e in out:
        by_name.setdefault(n, []).append((s, e))
    for name in expected_present:
        assert name in by_name, f"{name!r} missing from {sorted(by_name)}"
    # every reported span must reconstruct the exact name text verbatim.
    for name, spans in by_name.items():
        for s, e in spans:
            assert text[s:e] == name


@pytest.mark.parametrize("name_inventory, text, expected_present", SUPPRESSION_MATRIX)
def test_lsr_suppression_invariant_matrix(name_inventory, text, expected_present):
    lang = lsr_lang(name_inventory=name_inventory)
    out = lsr.extract_candidate_names(text, lang)
    names = {n for n, _mid in out}
    for name in expected_present:
        assert name in names, f"{name!r} missing from {sorted(names)}"


@pytest.mark.parametrize("name_inventory, text, expected_present", SUPPRESSION_MATRIX)
def test_suppression_invariant_matrix_parity_between_both_extractors(name_inventory, text, expected_present):
    bn_names = {n for n, _m, _s, _e in bn.extract_candidate_spans(text, bn_lang(name_inventory=name_inventory))}
    lsr_names = {n for n, _m in lsr.extract_candidate_names(text, lsr_lang(name_inventory=name_inventory))}
    assert bn_names == lsr_names


def test_bootstrap_singleton_inside_larger_run_mid_sentence_flags_are_correct():
    # The overlap itself must not corrupt the mid_sentence bookkeeping: the
    # OUTER pass-1 run is sentence-initial (text-initial), the INNER pass-2
    # singleton starts mid-run so it is mid-sentence.
    lang = bn_lang(name_inventory=["Cohen"])
    out = bn.extract_candidate_spans("Jean Cohen arrived.", lang)
    by_name = {n: mid for n, mid, _s, _e in out}
    assert by_name["Jean Cohen"] is False  # sentence-initial -> NOT mid-sentence
    assert by_name["Cohen"] is True        # follows "Jean" -> mid-sentence


def test_bootstrap_reversed_order_case_is_sentence_initial_when_text_says_so():
    lang = bn_lang(name_inventory=["Cohen משה"])
    out = bn.extract_candidate_spans("Bonjour. Cohen משה הלך.", lang)
    by_name = {n: mid for n, mid, _s, _e in out}
    # both the pass-1 singleton "Cohen" and the pass-2 full form start right
    # after the "Bonjour." sentence boundary -- both sentence-initial.
    assert by_name["Cohen"] is False
    assert by_name["Cohen משה"] is False


def test_lsr_inventory_route_does_not_double_count_capitalized_match():
    # lsr-side parity companion to
    # test_bootstrap_inventory_route_does_not_double_count_capitalized_match:
    # the ORIGINAL dedup case (an inventory form IDENTICAL to a pass-1 run)
    # must still be deduped under the new seen_spans-based invariant.
    lang = lsr_lang(name_inventory=["Jean Valjean"])
    out = lsr.extract_candidate_names("Jean Valjean marchait dans la rue.", lang)
    matches = [n for n, _mid in out if n == "Jean Valjean"]
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# Finding 2 (RFC #215 Phase 0 review round 4 -- CORRECTNESS REGRESSION the
# trie introduced): the trie walk must fall back to a SHORTER fresh terminal
# at the same position when the LONGEST terminal there is an exact
# duplicate of an already-emitted span, exactly like the pre-trie linear
# scan did ("longest-first, then continue to the next-shorter form until a
# fresh one wins"). A deepest-only walk (remembering only the LAST terminal
# seen) cannot express this fallback at all: pass 1 already emits "Jean
# Cohen" for "Jean Cohen arrived."; pass 2's longest inventory match at
# position 0 ("Jean Cohen") is then an exact duplicate, and the walk must
# fall back to the SHORTER, still-fresh "Jean" instead of emitting nothing.
# ---------------------------------------------------------------------------

def test_bootstrap_pass2_falls_back_to_shorter_fresh_form_when_longest_is_duplicate():
    lang = bn_lang(name_inventory=["Jean Cohen", "Jean"])
    out = bn.extract_candidate_spans("Jean Cohen arrived.", lang)
    names = [n for n, _mid, _s, _e in out]
    assert "Jean Cohen" in names
    assert "Jean" in names  # must NOT be silently dropped
    # control: with only "Jean" configured (no duplicate longest match to
    # fall back FROM), "Jean" is still correctly surfaced on its own.
    control_lang = bn_lang(name_inventory=["Jean"])
    control_out = bn.extract_candidate_spans("Jean Cohen arrived.", control_lang)
    assert "Jean" in [n for n, _mid, _s, _e in control_out]


def test_lsr_pass2_falls_back_to_shorter_fresh_form_when_longest_is_duplicate():
    lang = lsr_lang(name_inventory=["Jean Cohen", "Jean"])
    out = lsr.extract_candidate_names("Jean Cohen arrived.", lang)
    names = [n for n, _mid in out]
    assert "Jean Cohen" in names
    assert "Jean" in names


def test_pass2_fallback_parity_between_both_extractors():
    bn_names = {
        n for n, _m, _s, _e in
        bn.extract_candidate_spans("Jean Cohen arrived.", bn_lang(name_inventory=["Jean Cohen", "Jean"]))
    }
    lsr_names = {
        n for n, _m in
        lsr.extract_candidate_names("Jean Cohen arrived.", lsr_lang(name_inventory=["Jean Cohen", "Jean"]))
    }
    assert bn_names == lsr_names
    assert {"Jean Cohen", "Jean"} <= bn_names


# ---------------------------------------------------------------------------
# Finding 3 (RFC #215 Phase 0 review round 4 -- perf; the trie was rebuilt
# per block): collect_candidates() calls the extractor once per manifest
# block, so an uncached build re-tokenized every inventory form and rebuilt
# the whole trie on EVERY block. The trie for a given (name_inventory,
# elision_re) pair must be built exactly ONCE and reused across every call
# that shares it.
# ---------------------------------------------------------------------------

def test_bootstrap_inventory_trie_built_once_across_repeated_calls(monkeypatch):
    lang = bn_lang(name_inventory=["Jean Valjean", "Cohen"])
    bn._compiled_inventory_trie.cache_clear()
    call_count = {"n": 0}
    real_build = bn._build_inventory_trie

    def counting_build(inventory_forms):
        call_count["n"] += 1
        return real_build(inventory_forms)

    monkeypatch.setattr(bn, "_build_inventory_trie", counting_build)
    for _ in range(5):
        bn.extract_candidate_spans("Jean Valjean marchait. Cohen arriva.", lang)
    assert call_count["n"] == 1, (
        f"_build_inventory_trie was called {call_count['n']} times across 5 "
        "extract_candidate_spans() calls sharing the SAME LanguageConfig -- "
        "expected exactly 1 (cached), not rebuilt on every call"
    )


def test_lsr_inventory_trie_built_once_across_repeated_calls(monkeypatch):
    lang = lsr_lang(name_inventory=["Jean Valjean", "Cohen"])
    lsr._compiled_inventory_trie.cache_clear()
    call_count = {"n": 0}
    real_build = lsr._build_inventory_trie

    def counting_build(inventory_forms):
        call_count["n"] += 1
        return real_build(inventory_forms)

    monkeypatch.setattr(lsr, "_build_inventory_trie", counting_build)
    for _ in range(5):
        lsr.extract_candidate_names("Jean Valjean marchait. Cohen arriva.", lang)
    assert call_count["n"] == 1, (
        f"_build_inventory_trie was called {call_count['n']} times across 5 "
        "extract_candidate_names() calls sharing the SAME config dict -- "
        "expected exactly 1 (cached), not rebuilt on every call"
    )


# ---------------------------------------------------------------------------
# 0c -- offset-preserving occurrence spans (bootstrap_names.py only --
# language_smoke_report.py never emits spans, see its own module docstring)
# ---------------------------------------------------------------------------

def test_tokenize_elision_span_matches_contract_worked_example():
    # RFC #215 Phase 0c's own worked example: tokenize("d'Effiat") WITH
    # elision emits span [2,8) for "Effiat".
    elision_re = re.compile(FR_ELISION_PATTERN)
    tokens = bn.tokenize("d'Effiat", elision_re)
    name, preceding, start, end = tokens[1]
    assert (name, start, end) == ("Effiat", 2, 8)
    assert "d'Effiat"[start:end] == "Effiat"


def test_tokenize_non_elided_token_spans_reconstruct_raw_text():
    text = "Paris. Londres attend."
    tokens = bn.tokenize(text, None)
    by_name = {t[0]: t for t in tokens}
    s, e = by_name["Paris"][2], by_name["Paris"][3]
    assert text[s:e] == "Paris"
    s, e = by_name["Londres"][2], by_name["Londres"][3]
    assert text[s:e] == "Londres"


def test_extract_candidate_spans_repeated_surfaces_get_distinct_spans():
    lang = bn_lang()
    text = "Jean vit Jean encore."
    out = bn.extract_candidate_spans(text, lang)
    jean_spans = [(s, e) for n, _mid, s, e in out if n == "Jean"]
    assert len(jean_spans) == 2
    assert jean_spans[0] != jean_spans[1]
    for s, e in jean_spans:
        assert text[s:e] == "Jean"


def test_extract_candidate_spans_multitoken_span_reconstructs_raw_text():
    lang = bn_lang(particles=["de"])
    text = "Il visita le Chateau de Versailles hier."
    out = bn.extract_candidate_spans(text, lang)
    s, e = next((s, e) for n, _m, s, e in out if n == "Chateau de Versailles")
    assert text[s:e] == "Chateau de Versailles"


def test_extract_candidate_spans_block_boundary_start_and_end():
    lang = bn_lang()
    text = "Jean"
    out = bn.extract_candidate_spans(text, lang)
    assert out == [("Jean", False, 0, 4)]
    assert text[0:4] == "Jean"


def test_extract_candidate_spans_non_bmp_unicode_span():
    # Deseret script (U+10400 range) is bicameral -- category Lu/Ll -- and
    # non-BMP, so a standalone Deseret name passes is_upper_initial() via
    # genuine Unicode-category lookup, and CPython's codepoint-based str
    # indexing (PEP 393) must produce the exact same span math as any
    # BMP-only name; a UTF-16-code-unit assumption would be wrong here.
    name = "\U00010400\U00010401\U00010402"  # DESERET CAPITAL LETTER LONG A/AH/AW
    text = f"Marie vit {name} hier."
    lang = bn_lang()
    out = bn.extract_candidate_spans(text, lang)
    s, e = next((s, e) for n, _m, s, e in out if n == name)
    assert text[s:e] == name
    assert e - s == len(name) == 3


def test_extract_candidate_spans_variable_length_sentinel_offset_preserving():
    raw = "Paul ⟦FNREF_5⟧ vit ⟦VERSE_12_abcdef01⟧ Jean."
    masked = bn.mask_sentinels(raw)
    assert len(masked) == len(raw)
    lang = bn_lang()
    out = bn.extract_candidate_spans(raw, lang)
    spans = {n: (s, e) for n, _m, s, e in out}
    assert raw[spans["Paul"][0]:spans["Paul"][1]] == "Paul"
    assert raw[spans["Jean"][0]:spans["Jean"][1]] == "Jean"


def test_mask_sentinels_preserves_length_and_offsets_of_surrounding_text():
    raw = "A ⟦X⟧ B ⟦LONGER_SENTINEL_TOKEN⟧ C"
    masked = bn.mask_sentinels(raw)
    assert len(masked) == len(raw)
    # every non-sentinel character keeps its exact original index
    assert masked.index("A") == raw.index("A")
    assert masked.index("B") == raw.index("B")
    assert masked.index("C") == raw.index("C")


def test_extract_candidate_spans_real_fr_elision_config_span_reconstructs_name():
    lang = bn.load_language_config("fr.json", languages_dir=REAL_LANGUAGES_DIR)
    text = "Le marquis d'Effiat arriva a la cour."
    out = bn.extract_candidate_spans(text, lang)
    s, e = next((s, e) for n, _m, s, e in out if n == "Effiat")
    assert text[s:e] == "Effiat"


# ---------------------------------------------------------------------------
# Performance regression: pass 2's inventory scan must not be quadratic in
# (n_tokens x n_forms). A codex-rescue adversarial review found the un-fixed
# linear-scan-per-position code took ~9.8s (bootstrap_names.py) / ~9.5s
# (language_smoke_report.py) for 2000 two-token inventory forms (deliberately
# sharing their first token -- a first-token-bucketing fix would NOT help
# this adversarial case) against a 20000-token no-match document. The bound
# below is deliberately generous (the fixed trie-based scan actually runs in
# well under 0.1s) -- the point is proving no quadratic blowup survived, not
# pinning an exact number, so this stays robust under CI load.
# ---------------------------------------------------------------------------
PERF_BOUND_SECONDS = 2.0


def _adversarial_inventory_and_text():
    # 2000 two-token forms sharing their own first token ("Shared") -- the
    # worst case for a linear per-position scan, and one a naive first-token
    # bucketing optimization would NOT fix either (every form still lands in
    # the same bucket).
    name_inventory = [f"Shared Token{i}" for i in range(2000)]
    # ~20000 tokens, none of which match any inventory form.
    text = "Bonjour tout le monde aujourd'hui il fait beau. " * 2000
    return name_inventory, text


def test_bootstrap_inventory_scan_is_not_quadratic():
    name_inventory, text = _adversarial_inventory_and_text()
    lang = bn_lang(name_inventory=name_inventory)
    t0 = time.time()
    out = bn.extract_candidate_spans(text, lang)
    elapsed = time.time() - t0
    assert elapsed < PERF_BOUND_SECONDS, (
        f"extract_candidate_spans took {elapsed:.2f}s against a 2000-form/"
        f"20000-token adversarial no-match case (bound {PERF_BOUND_SECONDS}s) "
        "-- pass 2's inventory scan appears to have regressed to O(tokens x "
        "forms)"
    )
    # None of the (unrelated) inventory forms actually occur in this document
    # -- pass 1's own capitalized-run algorithm still fires on "Bonjour" (not
    # under test here), so only the inventory-form names are checked absent.
    names = {n for n, _mid, _s, _e in out}
    assert not names & set(name_inventory)


def test_lsr_inventory_scan_is_not_quadratic():
    name_inventory, text = _adversarial_inventory_and_text()
    lang = lsr_lang(name_inventory=name_inventory)
    t0 = time.time()
    out = lsr.extract_candidate_names(text, lang)
    elapsed = time.time() - t0
    assert elapsed < PERF_BOUND_SECONDS, (
        f"extract_candidate_names took {elapsed:.2f}s against a 2000-form/"
        f"20000-token adversarial no-match case (bound {PERF_BOUND_SECONDS}s) "
        "-- pass 2's inventory scan appears to have regressed to O(tokens x "
        "forms)"
    )
    names = {n for n, _mid in out}
    assert not names & set(name_inventory)


# ---------------------------------------------------------------------------
# Finding 9 (RFC #215 Phase 0 review round 4 -- docstring overclaim + test
# gap): the trie walk RESTARTS a descent at EVERY token position, so its
# real bound is O(n_tokens x L) (L = the longest inventory form's token
# count), not O(n_tokens) as an earlier docstring claimed. Measured directly
# (process_time, both n_tokens and L varied independently): time scales
# ~linearly in EACH factor, so it scales ~4x when both double together --
# genuine quadratic-like growth when the inventory form and the document
# both grow, exactly the case _adversarial_inventory_and_text() above cannot
# exercise: its 2000 forms never share their FIRST token with the document,
# so every walk there stops at depth 1 regardless of how long the forms are.
# This fixture shares the SAME token at every position in both the
# inventory form and the document, forcing every walk to descend the full L
# levels before failing on the form's own final (never-present) token.
# ---------------------------------------------------------------------------

def _shared_prefix_inventory_and_text(l_tokens=50, n_tokens=4000):
    form = " ".join(["Word"] * l_tokens + ["FinalMatch"])
    text = " ".join(["Word"] * n_tokens)
    return form, text


def test_bootstrap_inventory_scan_shared_prefix_stays_within_generous_bound():
    form, text = _shared_prefix_inventory_and_text()
    lang = bn_lang(name_inventory=[form])
    t0 = time.time()
    out = bn.extract_candidate_spans(text, lang)
    elapsed = time.time() - t0
    assert elapsed < PERF_BOUND_SECONDS, (
        f"extract_candidate_spans took {elapsed:.2f}s against a shared-long-"
        f"prefix inventory form (bound {PERF_BOUND_SECONDS}s) -- this is the "
        "genuinely O(n_tokens x L) shared-prefix walk cost the no-shared-"
        "prefix adversarial fixture above cannot exercise at all"
    )
    names = {n for n, _mid, _s, _e in out}
    assert form not in names  # the form's final token never actually occurs


def test_lsr_inventory_scan_shared_prefix_stays_within_generous_bound():
    form, text = _shared_prefix_inventory_and_text()
    lang = lsr_lang(name_inventory=[form])
    t0 = time.time()
    out = lsr.extract_candidate_names(text, lang)
    elapsed = time.time() - t0
    assert elapsed < PERF_BOUND_SECONDS, (
        f"extract_candidate_names took {elapsed:.2f}s against a shared-long-"
        f"prefix inventory form (bound {PERF_BOUND_SECONDS}s)"
    )
    names = {n for n, _mid in out}
    assert form not in names


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
