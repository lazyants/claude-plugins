"""Tests for scripts/bootstrap_names.py.

Targets: the tokenizer / run-building / frequency-scoring algorithm, the
Unicode-category-based capitalization check (``unicodedata.category(ch) ==
'Lu'`` primary, ASCII fast path), and the requirement that
PARTICLES/STOPWORDS/ELISION_RE/has_elision are read from the RESOLVED
``languages/<particle_config's LITERAL value>`` file -- never reconstructed
from ``source.language.code`` -- so a project-local override such as
``fr.local.json`` is genuinely respected.

Also locks in the documented ``french-elision-tokenizer-miss`` regression:
a regex-based proper-noun extractor over a source language with grammatical
elision (French ``d'Effiat``, ``l'Autriche``) must not silently and totally
drop the name behind the elided article.

Module under test lives outside any Python package (a standalone script
copied to ``${durable_root}/scripts/`` at runtime), so it is loaded here via
``importlib`` from its real path rather than imported normally.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "scripts"
    / "bootstrap_names.py"
)
REAL_LANGUAGES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "languages"


def _load_module():
    spec = importlib.util.spec_from_file_location("bootstrap_names_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bn = _load_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_lang(particles=(), stopwords=(), elision_pattern=None, has_elision=None):
    """Build a ``LanguageConfig`` directly (bypassing ``load_language_config``)
    for pure algorithm-level tests, so assertions aren't entangled with the
    real, much larger ``fr.json`` STOPWORDS list.
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
    )


FR_ELISION_PATTERN = r"^([dl])['’]([A-ZÀÂÄÆÇÉÈÊËÎÏÔŒÖÙÛÜŸ].*)$"


def load_real_fr_config():
    """Load the actual shipped ``fr.json`` from the real ``assets/languages``
    directory -- the one the plan calls "fully populated, extracted directly
    from the proven bootstrap_names.py".
    """
    return bn.load_language_config("fr.json", languages_dir=REAL_LANGUAGES_DIR)


# ---------------------------------------------------------------------------
# is_upper_initial() -- Unicode-category capitalization check
# ---------------------------------------------------------------------------

def test_is_upper_initial_ascii_fast_path():
    assert bn.is_upper_initial("Paris") is True
    assert bn.is_upper_initial("paris") is False


def test_is_upper_initial_empty_string_is_false():
    assert bn.is_upper_initial("") is False


def test_is_upper_initial_lowercase_accented_is_false():
    # 'é' is category Ll (lowercase letter) -- must not be misclassified.
    assert bn.is_upper_initial("émile") is False


def test_is_upper_initial_french_accented_capital_fast_path():
    # Accented capitals used by the French ELISION_RE's own remainder class
    # (Àâ Ä Æ Ç É È Ê Ë Î Ï Ô Œ Ö Ù Û Ü Ÿ) must be recognized as upper-initial.
    for name in ("Émile", "Étienne", "Ça"):
        assert bn.is_upper_initial(name) is True, name


def test_is_upper_initial_non_latin_script_via_unicode_category():
    # Proves the check is genuinely Unicode-category-based (unicodedata.category
    # == 'Lu'), not a hardcoded ASCII/French-accented range -- a Cyrillic
    # capital must also pass, even though it is nowhere in the ASCII fast path
    # or the French-accented literal range.
    assert bn.is_upper_initial("Аркадий") is True  # "Аркадий"
    assert bn.is_upper_initial("аркадий") is False  # "аркадий"


# ---------------------------------------------------------------------------
# is_particle() -- case/trailing-apostrophe-folded membership test
# ---------------------------------------------------------------------------

def test_is_particle_case_folds():
    lang = make_lang(particles=["de", "von"])
    assert bn.is_particle("De", lang) is True
    assert bn.is_particle("VON", lang) is True


def test_is_particle_strips_trailing_apostrophe():
    lang = make_lang(particles=["l", "d"])
    assert bn.is_particle("L'", lang) is True
    assert bn.is_particle("D’", lang) is True  # curly apostrophe variant


def test_is_particle_false_for_non_particle():
    lang = make_lang(particles=["de", "von"])
    assert bn.is_particle("Chateau", lang) is False


# ---------------------------------------------------------------------------
# tokenize() -- elision splitting
# ---------------------------------------------------------------------------

def test_tokenize_splits_elided_token_into_article_and_name():
    lang_elision_re = re.compile(FR_ELISION_PATTERN)
    tokens = bn.tokenize("le marquis d'Effiat arriva", lang_elision_re)
    toks_only = [t for t, _ in tokens]
    assert "d" in toks_only
    assert "Effiat" in toks_only
    # the fused raw token must never survive as its own token
    assert "d'Effiat" not in toks_only


def test_tokenize_elided_remainder_preceding_char_is_apostrophe():
    lang_elision_re = re.compile(FR_ELISION_PATTERN)
    tokens = bn.tokenize("d'Effiat", lang_elision_re)
    assert tokens[0] == ("d", ".")  # start-of-text sentence boundary
    assert tokens[1] == ("Effiat", "'")


def test_tokenize_without_elision_re_leaves_fused_token_intact():
    # No elision config (e.g. German/English/Russian): the fused token is
    # NOT split -- its own first char is what is_upper_initial() will see.
    tokens = bn.tokenize("d'Effiat", None)
    toks_only = [t for t, _ in tokens]
    assert toks_only == ["d'Effiat"]


def test_tokenize_sentence_initial_preceding_char():
    lang_elision_re = None
    tokens = bn.tokenize("Paris. Londres attend.", lang_elision_re)
    # first token of the text is preceded by "." (start-of-text sentinel)
    assert tokens[0] == ("Paris", ".")
    # "Londres" follows the period -> also preceded by "."
    names = [t for t, _ in tokens]
    idx = names.index("Londres")
    assert tokens[idx][1] == "."


# ---------------------------------------------------------------------------
# extract_candidates() -- run-building algorithm
# ---------------------------------------------------------------------------

def test_extract_candidates_joins_adjacent_capitalized_run():
    lang = make_lang()
    out = bn.extract_candidates("Jean Valjean marchait dans la rue.", lang)
    names = [n for n, _ in out]
    assert "Jean Valjean" in names


def test_extract_candidates_particle_continuation():
    lang = make_lang(particles=["de"])
    out = bn.extract_candidates("Il visita le Chateau de Versailles hier.", lang)
    names = [n for n, _ in out]
    assert "Chateau de Versailles" in names


def test_extract_candidates_particle_without_following_capital_does_not_join():
    # "de" is a particle, but if what follows is NOT capitalized, the run
    # must stop -- a particle can only continue a run when it bridges to
    # another capitalized token.
    lang = make_lang(particles=["de"], stopwords=["Le"])
    out = bn.extract_candidates("Le Chateau de solide pierre resista.", lang)
    names = [n for n, _ in out]
    assert "Chateau de solide" not in names
    assert "Chateau" in names


def test_extract_candidates_stopword_blocks_run_start():
    lang = make_lang(stopwords=["Le"])
    out = bn.extract_candidates("Le Comte partit.", lang)
    names = [n for n, _ in out]
    # "Le" must never itself start (or appear inside) a run
    assert all(not n.startswith("Le ") and n != "Le" for n in names)
    assert "Comte" in names


def test_extract_candidates_never_bridges_sentence_boundary():
    # A capitalized-token run must not fuse two unrelated proper nouns
    # separated by a sentence terminator into one bogus multiword candidate,
    # even though "Ensuite" is itself capitalized and would otherwise
    # qualify to extend the "Effiat" run per the plain continuation rule.
    lang = make_lang()
    out = bn.extract_candidates("Effiat. Ensuite revint.", lang)
    names = [n for n, _ in out]
    assert "Effiat Ensuite" not in names
    assert "Effiat" in names
    assert "Ensuite" in names


def test_extract_candidates_quote_masked_boundary():
    # A real terminator masked behind a closing quote (the '.' sits before
    # the "'") must still be found -- "Fiona" and "George" are two unrelated
    # proper nouns in adjacent sentences, not one fused run.
    lang = make_lang()
    out = bn.extract_candidates("'I saw Fiona.' George nodded.", lang)
    names = [n for n, _ in out]
    assert "Fiona George" not in names
    assert "Fiona" in names
    assert "George" in names


def test_extract_candidates_bracket_masked_boundary():
    lang = make_lang()
    out = bn.extract_candidates("(Fiona.) George arrived.", lang)
    names = [n for n, _ in out]
    assert "Fiona George" not in names
    assert "Fiona" in names
    assert "George" in names


def test_extract_candidates_guillemet_masked_boundary():
    lang = make_lang()
    out = bn.extract_candidates("Fiona. « George arriva. »", lang)
    names = [n for n, _ in out]
    assert "Fiona George" not in names
    assert "Fiona" in names
    assert "George" in names


def test_extract_candidates_nested_wrapper_masked_boundary():
    # Two stacked wrapper chars ")" + "]" mask the terminator before George;
    # the back-scan must skip BOTH to reach the "." behind them (exercises the
    # skip loop iterating more than once).
    lang = make_lang()
    out = bn.extract_candidates("([Fiona.]) George arrived.", lang)
    names = [n for n, _ in out]
    assert "Fiona George" not in names
    assert "Fiona" in names
    assert "George" in names


def test_extract_candidates_em_dash_boundary():
    # Proves TERMINATORS now includes the em-dash "—" (a dialogue-line
    # delimiter): before this sync, "—" was not a terminator here, so
    # "Fiona" and "George" would fuse across it.
    lang = make_lang()
    out = bn.extract_candidates("Fiona. — George arriva.", lang)
    names = [n for n, _ in out]
    assert "Fiona George" not in names
    assert "Fiona" in names
    assert "George" in names


def test_extract_candidates_particle_branch_respects_boundary():
    # The particle-continuation branch must not bridge a sentence terminator
    # before its trailing name -- "du" is a particle, but "George" starts a
    # new sentence, so "Fiona du George" must never form.
    lang = make_lang(particles=["du"])
    out = bn.extract_candidates("parla Fiona du. George arriva.", lang)
    names = [n for n, _ in out]
    assert "Fiona du George" not in names


def test_tokenize_backscan_skips_wrapper_to_terminator():
    tokens = bn.tokenize("Fiona.' George", None)
    george = next(t for t in tokens if t[0] == "George")
    assert george[1] == "."


def test_tokenize_no_terminator_behind_wrapper_stays_non_initial():
    # Documents the intended non-regression: no period sits behind the ")",
    # so "George" is not treated as sentence-initial -- the run still fuses
    # exactly as today.
    tokens = bn.tokenize("(Fiona) George", None)
    george = next(t for t in tokens if t[0] == "George")
    assert george[1] not in bn.TERMINATORS


def test_extract_candidates_mid_sentence_flag():
    lang = make_lang()
    out = bn.extract_candidates("Marie vit Paul hier soir.", lang)
    d = dict(out)
    # "Marie" opens the sentence -> not mid-sentence
    assert d["Marie"] is False
    # "Paul" is preceded by "vit" (not a terminator) -> mid-sentence
    assert d["Paul"] is True


# ---------------------------------------------------------------------------
# collect_candidates() -- frequency/mid-sentence/multiword scoring
# ---------------------------------------------------------------------------

def test_collect_candidates_frequency_and_segments():
    lang = make_lang()
    sources = [
        ("seg1", "Paris est belle. Paul aime Paris."),
        ("seg2", "Paris est loin."),
    ]
    result = bn.collect_candidates(sources, lang)
    rows = {r["name"]: r for r in result["candidates"]}
    assert rows["Paris"]["freq"] == 3
    assert rows["Paris"]["n_segments"] == 2
    assert rows["Paul"]["freq"] == 1
    assert rows["Paul"]["n_segments"] == 1


def test_collect_candidates_likely_name_rules():
    lang = make_lang()
    # "Q" is a bare single-letter token (an editorial initial, not a name);
    # "Marie"/"Paul" are ordinary multi-letter names that each occur
    # mid-sentence at least once but never reach freq>=4 and never form a
    # multiword run -- isolating the mid_sentence>0 branch of the OR.
    sources = [("s", "Q dort. Marie vit Paul. Paul vit Marie.")]
    result = bn.collect_candidates(sources, lang)
    rows = {r["name"]: r for r in result["candidates"]}
    # a bare single capital letter is an abbreviation, never a likely name,
    # regardless of frequency/mid-sentence count.
    assert rows["Q"]["abbrev"] is True
    assert rows["Q"]["likely_name"] is False
    # "Marie"/"Paul" each occur mid-sentence at least once -> likely names.
    assert rows["Marie"]["mid_sentence"] > 0
    assert rows["Marie"]["likely_name"] is True
    assert rows["Paul"]["mid_sentence"] > 0
    assert rows["Paul"]["likely_name"] is True


def test_collect_candidates_sort_order_freq_then_mid_sentence_then_name():
    lang = make_lang()
    # Bob(freq 2) must outrank Anna/Carl (freq 1 each); among the freq-1 tie,
    # alphabetical order (Anna before Carl) breaks the tie.
    sources = [("s", "Anna dort. Bob dort. Bob dort. Carl dort.")]
    result = bn.collect_candidates(sources, lang)
    names_in_order = [r["name"] for r in result["candidates"]]
    assert names_in_order[0] == "Bob"
    assert names_in_order.index("Anna") < names_in_order.index("Carl")
    # General sortedness proof against the documented key, independent of
    # the concrete example above.
    expected = sorted(
        result["candidates"],
        key=lambda r: (-r["freq"], -r["mid_sentence"], r["name"]),
    )
    assert result["candidates"] == expected


# ---------------------------------------------------------------------------
# load_language_config() -- resolves the LITERAL particle_config filename,
# never reconstructed from source.language.code; project-local overrides
# (e.g. fr.local.json) must be genuinely respected.
# ---------------------------------------------------------------------------

def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_language_config_resolves_literal_filename_value(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(
        languages_dir / "fr.json",
        {
            "PARTICLES": ["de"],
            "STOPWORDS": ["Le"],
            "has_elision": False,
            "ELISION_RE": None,
        },
    )
    lang = bn.load_language_config("fr.json", languages_dir=languages_dir)
    assert lang.particles == frozenset({"de"})
    assert lang.stopwords == frozenset({"Le"})
    assert lang.has_elision is False


def test_load_language_config_respects_project_local_override_not_language_code(tmp_path):
    # The profile's source.language.code is "fr", but its particle_config
    # LITERAL value points at a project-local override with genuinely
    # different content. The loader must use exactly that file's content --
    # never silently fall back to reconstructing "fr.json" from the code.
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(
        languages_dir / "fr.json",
        {
            "PARTICLES": ["de"],
            "STOPWORDS": [],
            "has_elision": False,
            "ELISION_RE": None,
        },
    )
    _write_json(
        languages_dir / "fr.local.json",
        {
            "PARTICLES": ["de", "zzz_project_specific_particle"],
            "STOPWORDS": ["Custom"],
            "has_elision": True,
            "ELISION_RE": r"^([dl])['](.*)$",
        },
    )

    overridden = bn.load_language_config("fr.local.json", languages_dir=languages_dir)
    baseline = bn.load_language_config("fr.json", languages_dir=languages_dir)

    assert "zzz_project_specific_particle" in overridden.particles
    assert overridden.has_elision is True
    assert overridden.path.name == "fr.local.json"

    # The baseline file must remain unaffected -- proves the two resolve
    # independently by literal filename, not through any code-derived path.
    assert "zzz_project_specific_particle" not in baseline.particles
    assert baseline.has_elision is False
    assert baseline.path.name == "fr.json"


def test_load_language_config_rejects_path_traversal_filename(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    with pytest.raises(bn.BootstrapNamesError):
        bn.load_language_config("../fr.json", languages_dir=languages_dir)


def test_load_language_config_rejects_non_bare_filename_with_slash(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    with pytest.raises(bn.BootstrapNamesError):
        bn.load_language_config("sub/fr.json", languages_dir=languages_dir)


def test_load_language_config_missing_file_raises(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    with pytest.raises(bn.BootstrapNamesError):
        bn.load_language_config("nope.json", languages_dir=languages_dir)


def test_load_language_config_rejects_missing_particles_field(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(
        languages_dir / "bad.json",
        {"STOPWORDS": [], "has_elision": False, "ELISION_RE": None},
    )
    with pytest.raises(bn.BootstrapNamesError, match="PARTICLES"):
        bn.load_language_config("bad.json", languages_dir=languages_dir)


def test_load_language_config_rejects_non_bool_has_elision(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(
        languages_dir / "bad.json",
        {"PARTICLES": [], "STOPWORDS": [], "has_elision": "yes", "ELISION_RE": None},
    )
    with pytest.raises(bn.BootstrapNamesError, match="has_elision"):
        bn.load_language_config("bad.json", languages_dir=languages_dir)


def test_load_language_config_rejects_missing_elision_re_when_has_elision_true(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(
        languages_dir / "bad.json",
        {"PARTICLES": [], "STOPWORDS": [], "has_elision": True, "ELISION_RE": None},
    )
    with pytest.raises(bn.BootstrapNamesError):
        bn.load_language_config("bad.json", languages_dir=languages_dir)


def test_load_language_config_rejects_wrong_capture_group_count(tmp_path):
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir()
    _write_json(
        languages_dir / "bad.json",
        {
            "PARTICLES": [],
            "STOPWORDS": [],
            "has_elision": True,
            "ELISION_RE": r"^([dl])'(.*)(x)?$",  # 3 groups, not 2
        },
    )
    with pytest.raises(bn.BootstrapNamesError, match="2 capture groups"):
        bn.load_language_config("bad.json", languages_dir=languages_dir)


def test_load_language_config_loads_real_shipped_fr_json():
    # Sanity check against the actual shipped preset -- "the ONE real,
    # battle-tested config" per the plan doc.
    lang = load_real_fr_config()
    assert lang.has_elision is True
    assert "de" in lang.particles
    assert "von" in lang.particles
    assert lang.elision_re is not None
    assert lang.elision_re.groups == 2


# ---------------------------------------------------------------------------
# The french-elision-tokenizer-miss regression: d'Effiat / l'Autriche must
# be CAPTURED, not silently and totally dropped, when has_elision/ELISION_RE
# are honored -- and demonstrably WOULD be dropped without the split.
# ---------------------------------------------------------------------------

def test_regression_elided_names_captured_with_real_fr_elision_config():
    lang = load_real_fr_config()
    text = (
        "Le marquis d'Effiat arriva a la cour. "
        "Il revenait tout juste de l'Autriche."
    )
    out = bn.extract_candidates(text, lang)
    names = [n for n, _ in out]
    assert "Effiat" in names, f"'Effiat' behind d'Effiat was dropped; got {names}"
    assert "Autriche" in names, f"'Autriche' behind l'Autriche was dropped; got {names}"


def test_regression_elided_names_would_be_silently_dropped_without_split():
    # Same text, same STOPWORDS/PARTICLES, but with elision disabled --
    # this is the "before the fix" behavior the gotcha describes: the fused
    # token's lowercase first character defeats is_upper_initial() and the
    # whole name is dropped, not merely mis-scored.
    real_lang = load_real_fr_config()
    lang_no_elision = bn.LanguageConfig(
        path=real_lang.path,
        particles=real_lang.particles,
        stopwords=real_lang.stopwords,
        elision_re=None,
        has_elision=False,
        raw_bytes=real_lang.raw_bytes,
    )
    text = (
        "Le marquis d'Effiat arriva a la cour. "
        "Il revenait tout juste de l'Autriche."
    )
    out = bn.extract_candidates(text, lang_no_elision)
    names = [n for n, _ in out]
    assert "Effiat" not in names
    assert "Autriche" not in names
    # the fused, lowercase-initial tokens are dropped entirely -- not merely
    # captured with the wrong casing/shape.
    assert "d'Effiat" not in names
    assert "l'Autriche" not in names


def test_regression_collect_candidates_end_to_end_with_real_fr_config():
    # Integration-level check across the whole pipeline used by the real
    # smoke test / mass run: collect_candidates() over multiple sources.
    lang = load_real_fr_config()
    sources = [
        ("seg1", "Le marquis d'Effiat partit pour l'Autriche."),
        # kept lowercase "d'"/"l'" throughout (mid-sentence elision only) --
        # ELISION_RE's group 1 is the literal char class [dl], so a
        # sentence-initial capitalized "D'Effiat" would NOT match and is
        # deliberately out of scope for this fixture.
        ("seg2", "Le marquis d'Effiat revint bientot d'Autriche."),
    ]
    result = bn.collect_candidates(sources, lang)
    names = {r["name"] for r in result["candidates"]}
    assert "Effiat" in names
    assert "Autriche" in names
    effiat_row = next(r for r in result["candidates"] if r["name"] == "Effiat")
    assert effiat_row["freq"] >= 2
    assert effiat_row["n_segments"] == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
