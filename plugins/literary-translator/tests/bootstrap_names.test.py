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
import unicodedata
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
    toks_only = [t[0] for t in tokens]
    assert "d" in toks_only
    assert "Effiat" in toks_only
    # the fused raw token must never survive as its own token
    assert "d'Effiat" not in toks_only


def test_tokenize_elided_remainder_preceding_char_is_apostrophe():
    # (token, preceding_char, start, end) -- start/end are half-open
    # codepoint offsets into "d'Effiat" ("d"=[0,1), "Effiat"=[2,8), the
    # apostrophe at index 1 belongs to neither child span).
    lang_elision_re = re.compile(FR_ELISION_PATTERN)
    tokens = bn.tokenize("d'Effiat", lang_elision_re)
    assert tokens[0] == ("d", ".", 0, 1)  # start-of-text sentence boundary
    assert tokens[1] == ("Effiat", "'", 2, 8)
    assert "d'Effiat"[2:8] == "Effiat"


def test_tokenize_without_elision_re_leaves_fused_token_intact():
    # No elision config (e.g. German/English/Russian): the fused token is
    # NOT split -- its own first char is what is_upper_initial() will see.
    tokens = bn.tokenize("d'Effiat", None)
    toks_only = [t[0] for t in tokens]
    assert toks_only == ["d'Effiat"]


def test_tokenize_sentence_initial_preceding_char():
    lang_elision_re = None
    tokens = bn.tokenize("Paris. Londres attend.", lang_elision_re)
    # first token of the text is preceded by "." (start-of-text sentinel);
    # "Paris" spans codepoints [0, 5).
    assert tokens[0] == ("Paris", ".", 0, 5)
    # "Londres" follows the period -> also preceded by "."
    names = [t[0] for t in tokens]
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


def test_token_re_leaves_trailing_apostrophe_unconsumed():
    # Issue #82: the tokenizer used to ABSORB a trailing apostrophe into the
    # token (e.g. "Fiona’" as one token), so a stray apostrophe after a name
    # fused into the candidate. Every token must now both START and END in a
    # letter -- a connector is matched only BETWEEN two letters -- so a trailing
    # "'"/"’" is left unconsumed. Cover both the straight and curly variants.
    for text in ("Fiona’ George nodded.", "Fiona' George nodded."):
        tokens = bn.TOKEN_RE.findall(text)
        assert tokens == ["Fiona", "George", "nodded"], text
        assert not any("’" in t or "'" in t for t in tokens), text


def test_extract_candidates_strips_trailing_apostrophe_not_split():
    # STRIP-ONLY resolution of #82: the trailing apostrophe is dropped from the
    # token stream (not turned into its own token or a split point). "'"/"’" is
    # a WRAPPER, not a TERMINATOR, so with no real sentence boundary present the
    # run still fuses exactly as before -- but the candidate no longer carries
    # the apostrophe. Pin the EXACT result to lock strip-not-split.
    lang = make_lang(particles=["de", "du", "von"], elision_pattern=FR_ELISION_PATTERN)
    for text in ("Fiona’ George nodded.", "Fiona' George nodded."):
        out = bn.extract_candidates(text, lang)
        assert out == [("Fiona George", False)], text
        assert all("’" not in n and "'" not in n for n, _ in out), text


def test_over_correction_guard_internal_connectors_and_elision_intact():
    # The #82 fix must NOT over-correct: an internal connector between two
    # letters is still consumed, so elision still splits d'/l' off a
    # capitalized name (d'Effiat, l'Autriche captured), a hyphenated name stays
    # one token, and a lowercase elided contraction (aujourd'hui) is not
    # fragmented. Both straight and curly apostrophe variants must behave alike.
    lang = make_lang(particles=["de", "du", "von"], elision_pattern=FR_ELISION_PATTERN)
    for text in ("Le marquis d'Effiat vit l'Autriche.", "Le marquis d’Effiat vit l’Autriche."):
        names = [n for n, _ in bn.extract_candidates(text, lang)]
        assert "Effiat" in names and "Autriche" in names, text
    # Saint-Simon stays a single hyphenated token / candidate.
    assert bn.TOKEN_RE.findall("Saint-Simon") == ["Saint-Simon"]
    saint = [n for n, _ in bn.extract_candidates("Le duc de Saint-Simon parla.", lang)]
    assert "Saint-Simon" in saint
    # aujourd'hui: internal apostrophe kept, token not fragmented (both variants).
    assert bn.TOKEN_RE.findall("aujourd'hui") == ["aujourd'hui"]
    assert bn.TOKEN_RE.findall("aujourd’hui") == ["aujourd’hui"]


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
        # a sentence-initial capitalized elision (e.g. "D'Effiat") is
        # intentionally NOT split: ELISION_RE's group 1 is deliberately
        # lowercase-only ([dl]) so a fixed proper-noun spelling such as
        # "D'Artagnan"/"L'Aquila" survives whole instead of being torn into
        # a bogus "Artagnan"/"Aquila" (see assets/languages/README.md's
        # it.json status row). #91 proposed widening ELISION_RE to also
        # split capitalized elisions; that was investigated and reverted
        # for conflicting with this design -- see
        # test_fixed_compound_dartagnan_stays_fused_fr below.
        ("seg2", "Le marquis d'Effiat revint bientot d'Autriche."),
    ]
    result = bn.collect_candidates(sources, lang)
    names = {r["name"] for r in result["candidates"]}
    assert "Effiat" in names
    assert "Autriche" in names
    effiat_row = next(r for r in result["candidates"] if r["name"] == "Effiat")
    assert effiat_row["freq"] >= 2
    assert effiat_row["n_segments"] == 2


def test_fixed_compound_dartagnan_stays_fused_fr():
    # #91 proposed making ELISION_RE's article-capture class
    # case-insensitive so a sentence-initial capitalized elision (e.g.
    # "L'Enclos") would split like its mid-sentence lowercase counterpart.
    # Investigated and reverted: assets/languages/README.md documents this
    # as INTENTIONAL -- a sentence-initial capitalized elided form is a
    # fixed proper-noun spelling (D'Artagnan, L'Aquila, D'Annunzio,
    # L'Oreal) that already starts with a capital and survives the
    # extractor's capitalization gate whole; widening the regex would
    # wrongly tear these into "Artagnan"/"Aquila"/etc. This regression
    # guards the documented tradeoff against a future accidental re-fix.
    lang = load_real_fr_config()
    text = "D'Artagnan arriva a la cour."
    names = [n for n, _ in bn.extract_candidates(text, lang)]
    assert "D'Artagnan" in names, f"fixed compound was wrongly split; got {names}"
    assert "Artagnan" not in names, f"fixed compound was wrongly split; got {names}"


def test_fixed_compound_laquila_stays_fused_it():
    # Italian analogue -- see test_fixed_compound_dartagnan_stays_fused_fr
    # and assets/languages/README.md's it.json status row.
    lang = bn.load_language_config("it.json", languages_dir=REAL_LANGUAGES_DIR)
    text = "L'Aquila e una citta antica."
    names = [n for n, _ in bn.extract_candidates(text, lang)]
    assert "L'Aquila" in names, f"fixed compound was wrongly split; got {names}"
    assert "Aquila" not in names, f"fixed compound was wrongly split; got {names}"


# ---------------------------------------------------------------------------
# #91 -- capitalized-elision ambiguity DETECTION (detection only; the flag
# feeds the glossary adjudicator, this script never auto-splits/auto-merges).
# collect_candidates() tags a single-token capitalized row as
# ``elision_ambiguous`` iff its lowercased-first-char form matches the
# language's own ELISION_RE AND the stripped name-initial remainder is itself
# another candidate row (global match). ELISION_RE itself is UNCHANGED, so the
# tokenizer still keeps D'Artagnan / L'Aquila fused (the two
# test_fixed_compound_*_stays_fused_* regressions above stay green).
# ---------------------------------------------------------------------------

def test_elision_ambiguous_flags_capitalized_elision_pair_fr():
    # Genuine ambiguity: "L'Enclos" (a capitalized elision, NOT split by the
    # lowercase-only ELISION_RE -> stays one token) occurs once, and a bare
    # "Enclos" also occurs. The surface "L'Enclos" could be a fixed compound
    # OR elided article "l'" + the already-seen name "Enclos" -> flag it.
    lang = load_real_fr_config()
    sources = [("seg1", "L'Enclos entra dans la salle. Il salua Enclos ensuite.")]
    result = bn.collect_candidates(sources, lang)
    rows = {r["name"]: r for r in result["candidates"]}
    assert "L'Enclos" in rows
    assert "Enclos" in rows
    lenclos = rows["L'Enclos"]
    # Realistic DOMINANT case (load-bearing for Teammate B's force-include):
    # single-word, sentence-initial (mid_sentence=0), freq=1 -> likely_name is
    # False. A likely_name=True fixture would pass vacuously under the broken
    # reading, so pin these explicitly.
    assert lenclos["multiword"] is False
    assert lenclos["mid_sentence"] == 0
    assert lenclos["freq"] == 1
    assert lenclos["likely_name"] is False
    # The detector fires and names the re-capitalized stripped form.
    assert lenclos["elision_ambiguous"] is True
    assert lenclos["elision_stripped_form"] == "Enclos"
    # The bare "Enclos" row carries no elided article -> never flagged.
    assert rows["Enclos"].get("elision_ambiguous") in (None, False)
    assert "elision_stripped_form" not in rows["Enclos"]


def test_elision_ambiguous_positive_control_dartagnan_with_bare_artagnan_fr():
    # POSITIVE CONTROL proving the detector is LIVE code (so the true-negative
    # below isn't vacuously green because the field never exists): a corpus
    # with BOTH the fixed-compound surface "D'Artagnan" AND a bare "Artagnan"
    # occurrence. The stripped form ("Artagnan") matches the bare row, so the
    # "D'Artagnan" row MUST be flagged. Only CAPITALIZED "D'Artagnan" is used
    # -- a lowercase "d'Artagnan" would be split by the tokenizer and would
    # itself manufacture the bare "Artagnan" row, contaminating the fixture.
    lang = load_real_fr_config()
    sources = [("seg1", "D'Artagnan degaina son epee. Plus tard, il revit Artagnan seul.")]
    result = bn.collect_candidates(sources, lang)
    rows = {r["name"]: r for r in result["candidates"]}
    assert "D'Artagnan" in rows
    assert "Artagnan" in rows
    assert rows["D'Artagnan"]["elision_ambiguous"] is True
    assert rows["D'Artagnan"]["elision_stripped_form"] == "Artagnan"


def test_elision_ambiguous_true_negative_dartagnan_no_bare_artagnan_fr():
    # TRUE NEGATIVE: a Musketeers-style corpus with "D'Artagnan" and NO bare
    # "Artagnan" occurrence -- nothing to pair with, so the flag must NOT fire.
    # Capitalized forms only. (Vacuously green on the pre-fix tree; meaningful
    # only alongside the positive control above, which proves the field CAN be
    # set.)
    lang = load_real_fr_config()
    sources = [("seg1", "D'Artagnan degaina son epee. D'Artagnan salua le roi.")]
    result = bn.collect_candidates(sources, lang)
    rows = {r["name"]: r for r in result["candidates"]}
    assert "D'Artagnan" in rows
    assert "Artagnan" not in rows
    assert rows["D'Artagnan"].get("elision_ambiguous") in (None, False)
    assert "elision_stripped_form" not in rows["D'Artagnan"]


def test_elision_ambiguous_generalizes_to_it_config_laquila_pair():
    # Proves the detector reuses each language's OWN ELISION_RE verbatim (no
    # hardcoded [dDlL]): the identical mechanism fires for Italian "L'Aquila"
    # when a bare "Aquila" also occurs.
    lang = bn.load_language_config("it.json", languages_dir=REAL_LANGUAGES_DIR)
    sources = [("seg1", "L'Aquila e una citta. Io vidi Aquila ieri.")]
    result = bn.collect_candidates(sources, lang)
    rows = {r["name"]: r for r in result["candidates"]}
    assert "L'Aquila" in rows
    assert "Aquila" in rows
    assert rows["L'Aquila"]["elision_ambiguous"] is True
    assert rows["L'Aquila"]["elision_stripped_form"] == "Aquila"


# ---------------------------------------------------------------------------
# #225 -- offset-safe, mark-inclusive tokenizer. TOKEN_RE now absorbs a
# letter's combining marks (Hebrew niqqud/cantillation, Arabic harakat, Latin
# NFD accents) INSIDE the token instead of ending a run at the first mark,
# while preserving the raw Unicode-codepoint offsets occ_index.py's evidence
# spans bind to (RFC #215 Phase 0c). The mark class is built from a curated,
# category-filtered sub-range list (_MARK_SUBRANGES); the completeness tests
# below are the empirical backstop for that list.
# ---------------------------------------------------------------------------

POINTED_HEBREW = "שָׁלוֹם"                            # niqqud between every consonant
VOCALIZED_ARABIC = "سَلَام"                            # Arabic harakat (fatha) marks
NFD_RESUME = unicodedata.normalize("NFD", "résumé")  # base letter + U+0301, twice


def test_tokenizer_keeps_pointed_hebrew_single_token():
    # Pre-#225 the niqqud ended each run early -> one token per consonant (3);
    # now the whole pointed word is ONE token.
    assert len(list(bn.TOKEN_RE.finditer(POINTED_HEBREW))) == 1


def test_tokenizer_keeps_vocalized_arabic_single_token():
    # Vocalized Arabic harakat behave identically (pre-#225: 3 tokens).
    assert len(list(bn.TOKEN_RE.finditer(VOCALIZED_ARABIC))) == 1


def test_tokenizer_keeps_arabic_extended_mark_single_token():
    # #225 follow-up (codex Medium): the curated Arabic sub-ranges stopped at
    # U+06ED, omitting the Arabic Extended-A/B combining marks (~U+0870-08FF)
    # used by vocalized Extended-Arabic names/manuscripts -- e.g. U+08F0
    # ARABIC OPEN FATHATAN. Pre-fix this split into 2 tokens (mark ended the
    # run early), silently altering the name and breaking production_
    # occurrences()'s offset-span authentication (RFC #215 Phase 0c).
    text = "ا" + "ࣰ" + "ب"
    spans = [(tok, s, e) for tok, _prec, s, e in bn.tokenize(text, None)]
    assert len(spans) == 1
    tok, s, e = spans[0]
    assert tok == text
    assert text[s:e] == text


def test_tokenizer_keeps_nfd_combining_accent_inside_token():
    # An NFD Latin word (base + combining acute, twice) is ONE token
    # (pre-#225: 2 tokens, split at each combining accent); the NFC form was
    # already one token and must stay one (no regression).
    assert len(list(bn.TOKEN_RE.finditer(NFD_RESUME))) == 1
    assert len(list(bn.TOKEN_RE.finditer(unicodedata.normalize("NFC", "résumé")))) == 1


def test_tokenizer_nfc_latin_and_connectors_unchanged():
    # The mark class only ever ABSORBS marks; connector/letter handling for the
    # plugin's existing NFC Latin corpus is unchanged (guards against a silent
    # behavior shift, incl. the issue #82 trailing-connector rule).
    assert bn.TOKEN_RE.findall("Saint-Simon") == ["Saint-Simon"]
    assert bn.TOKEN_RE.findall("aujourd'hui") == ["aujourd'hui"]
    assert bn.TOKEN_RE.findall("aujourd’hui") == ["aujourd’hui"]
    assert bn.TOKEN_RE.findall("Fiona’ George") == ["Fiona", "George"]


def test_tokenizer_offset_span_reconstructs_pointed_substring():
    # THE occ_index invariant (#206 / RFC #215 Phase 0c): the emitted
    # (start, end) span reconstructs the pointed word -- marks included --
    # verbatim from the raw text. Pre-#225 no single token spanned the whole
    # pointed word, so next() below raised StopIteration.
    text = f"ראה {POINTED_HEBREW} אתמול."
    spans = [(tok, s, e) for tok, _prec, s, e in bn.tokenize(text, None)]
    s, e = next((s, e) for tok, s, e in spans if tok == POINTED_HEBREW)
    assert text[s:e] == POINTED_HEBREW


# --- MARK class completeness / purity backstop -----------------------------
# The spans the curated sub-ranges claim to cover COMPLETELY. Bounded at the
# curated upper edge (U+1ACE) rather than the whole 1AB0-1AFF block so a FUTURE
# Unicode assignment past 1ACE cannot spuriously RED this on a newer
# interpreter; the Hebrew (0591-05C7) and Arabic (0610-06ED) spans deliberately
# CONTAIN non-mark punctuation the sub-ranges skip, making the omission check a
# real test rather than a tautology. The Arabic Extended-A/B span (0870-08FF)
# is curated as the whole block (see _MARK_SUBRANGES), so it CONTAINS assigned
# non-mark letters the category filter -- not the sub-range -- drops; still a
# real regression guard against a future narrowing of that sub-range.
MARK_SUPER_RANGES = (
    (0x0300, 0x036F), (0x1AB0, 0x1ACE), (0x1DC0, 0x1DFF), (0xFE20, 0xFE2F),
    (0x0483, 0x0489), (0x0591, 0x05C7), (0x0610, 0x06ED), (0x0870, 0x08FF),
)


def _mark_class_pattern():
    return re.compile("[" + bn._MARK_CLASS + "]")


def test_mark_class_accepts_only_combining_marks():
    # PURITY: every codepoint TOKEN_RE's mark class matches is category M*.
    cls = _mark_class_pattern()
    non_marks = sorted(
        (hex(cp), unicodedata.category(chr(cp)))
        for lo, hi in bn._MARK_SUBRANGES
        for cp in range(lo, hi + 1)
        if cls.match(chr(cp)) and not unicodedata.category(chr(cp)).startswith("M")
    )
    assert non_marks == [], f"mark class accepts non-M codepoints: {non_marks}"


# The Arabic Extended-A/B sub-range (0870-08FF) is curated as the WHOLE
# block rather than a tight mark-only enumeration like every other Arabic
# sub-range -- the block interleaves marks with genuine Arabic letters, and
# the category filter (not hand-curation) is relied on to drop them. Exempt
# from the typo guard below so it keeps checking every OTHER, still
# tightly-curated sub-range for a stray non-mark codepoint.
_WHOLE_BLOCK_SUBRANGES = {(0x0870, 0x08FF)}


def test_curated_subranges_name_no_assigned_non_mark():
    # A curated sub-range codepoint must be category M* OR unassigned (Cn) --
    # never an assigned letter/digit/punct. Catches a typo (e.g. sweeping in
    # 05BE maqaf/Pd) while tolerating a Unicode-version Cn gap the category
    # filter harmlessly drops.
    strays = sorted(
        (hex(cp), unicodedata.category(chr(cp)))
        for lo, hi in bn._MARK_SUBRANGES
        if (lo, hi) not in _WHOLE_BLOCK_SUBRANGES
        for cp in range(lo, hi + 1)
        if unicodedata.category(chr(cp)) != "Cn"
        and not unicodedata.category(chr(cp)).startswith("M")
    )
    assert strays == [], f"curated sub-range names an assigned non-mark: {strays}"


def test_mark_class_omits_no_mark_within_super_ranges():
    # COMPLETENESS: within each span the curated list claims to cover fully,
    # every category-M codepoint is accepted by the mark class. Catches a
    # sub-range that silently drops a real mark (e.g. 05C4 alone instead of
    # 05C4-05C5, or a forgotten Hebrew cantillation accent).
    cls = _mark_class_pattern()
    omitted = sorted(
        (hex(cp), unicodedata.name(chr(cp), "?"))
        for lo, hi in MARK_SUPER_RANGES
        for cp in range(lo, hi + 1)
        if unicodedata.category(chr(cp)).startswith("M") and not cls.match(chr(cp))
    )
    assert omitted == [], f"category-M codepoint(s) in a claimed span not covered: {omitted}"


def test_letter_class_disjoint_from_mark_class():
    # DISJOINTNESS keeps the LETTER/MARK/CONNECTOR parse linear + deterministic:
    # LETTER ([^\W\d_]) must match none of the curated marks.
    letter = re.compile(r"[^\W\d_]")
    cls = _mark_class_pattern()
    overlap = sorted(
        hex(cp)
        for lo, hi in bn._MARK_SUBRANGES
        for cp in range(lo, hi + 1)
        if cls.match(chr(cp)) and letter.match(chr(cp))
    )
    assert overlap == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
