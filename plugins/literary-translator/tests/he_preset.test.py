"""Tests for the shipped ``assets/languages/he.json`` Hebrew preset (#195).

``he.json`` is an uncased-script (Hebrew, Unicode category ``Lo``) source
extraction config. Its shape is the ordinary four-required-key contract, but
its values are deliberately degenerate for the capitalization-run algorithm:

  - ``PARTICLES: []`` -- the Pass-1 capitalization run never fires on
    category-``Lo`` Hebrew (``is_upper_initial`` gates on ASCII/``Lu``), so a
    particle list would be inert; empty is the correct, honest value.
  - ``STOPWORDS`` -- STANDALONE, whitespace-delimited Hebrew function words
    ONLY (pronouns, conjunctions, standalone prepositions/adverbs). The one
    Hebrew consumer, ``final_audit.warn_foreign_remainder``, whitespace-splits
    the target draft to flag untranslated Hebrew remnants, so single-letter
    proclitics (ה/ב/כ/ל/מ/ש/ו -- which orthographically fuse onto the next
    word and never stand alone) would be dead weight and are excluded.
  - ``has_elision: false`` / ``ELISION_RE: null`` -- Hebrew has no
    French/Italian-style article-apostrophe elision.

This file must load cleanly and identically under BOTH extraction
implementations' loaders -- ``bootstrap_names.load_language_config`` and
``language_smoke_report.load_particle_config`` -- exactly like every Latin
preset, so a Hebrew project can never diverge between the two.

Both modules under test are standalone scripts (copied to
``${durable_root}/scripts/`` at runtime), loaded here via ``importlib`` from
their real paths rather than imported normally.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
REAL_LANGUAGES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "languages"
HE_JSON_PATH = REAL_LANGUAGES_DIR / "he.json"


def _load_module(name: str, filename: str):
    path = SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bn = _load_module("bootstrap_names_he_preset_under_test", "bootstrap_names.py")
smoke = _load_module("language_smoke_report_he_preset_under_test", "language_smoke_report.py")


# A representative, unambiguous subset of the shipped standalone function
# words -- each is a genuine whitespace-delimited Hebrew word, NOT a
# single-letter proclitic:
#   של  = "of" (possessive)      את  = definite direct-object marker
#   כי  = "because/that"          לא  = "not"          אבל = "but"
#   הוא = "he"                    זה  = "this (m.)"
_EXPECTED_STANDALONE_STOPWORDS = ["של", "את", "כי", "לא", "אבל", "הוא", "זה"]
# A single-letter proclitic that must NEVER be shipped as a stopword: it
# fuses onto the following word and never stands alone, so it can never be a
# whitespace-delimited token in ``final_audit``'s foreign-remainder scan.
_FORBIDDEN_PROCLITIC = "ש"


def _write_mutation(tmp_path: Path, **overrides) -> Path:
    """Copy the shipped he.json, apply ``overrides`` to its parsed dict, and
    write it under a fresh ``languages/`` dir so both loaders can resolve it
    by bare filename (``bn``) or by explicit path (``smoke``)."""
    languages_dir = tmp_path / "languages"
    languages_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(HE_JSON_PATH.read_bytes())
    data.update(overrides)
    out = languages_dir / "he.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def test_he_preset_loads():
    """The shipped preset loads via ``bootstrap_names`` with the exact
    degenerate-but-valid uncased-script value set."""
    lang = bn.load_language_config("he.json", languages_dir=REAL_LANGUAGES_DIR)

    assert lang.particles == frozenset(), (
        "he.json PARTICLES must resolve to an empty frozenset -- the Pass-1 "
        "capitalization run never fires on category-Lo Hebrew, so any particle "
        f"would be inert; got {sorted(lang.particles)!r}"
    )
    assert lang.has_elision is False
    assert lang.elision_re is None
    assert lang.name_inventory == frozenset(), (
        "a shipped preset must not embed a project-local name_inventory"
    )

    for word in _EXPECTED_STANDALONE_STOPWORDS:
        assert word in lang.stopwords, (
            f"expected standalone Hebrew function word {word!r} in he.json STOPWORDS"
        )
    assert _FORBIDDEN_PROCLITIC not in lang.stopwords, (
        f"single-letter proclitic {_FORBIDDEN_PROCLITIC!r} must never be a "
        "stopword -- it fuses onto the next word and never stands alone"
    )


def test_he_preset_smoke_loader_agrees():
    """``language_smoke_report``'s independent loader accepts he.json
    identically -- same particles/stopwords/elision, so W3's has_elision
    cross-check can never disagree with W2's extractor."""
    bn_lang = bn.load_language_config("he.json", languages_dir=REAL_LANGUAGES_DIR)
    smoke_lang = smoke.load_particle_config(HE_JSON_PATH)

    assert smoke_lang["has_elision"] is False
    assert smoke_lang["elision_re"] is None
    assert smoke_lang["name_inventory"] == frozenset()
    # PARTICLES: bn lowercases+strips into a frozenset; smoke keeps the raw
    # list. Both must be empty for he.json.
    assert list(smoke_lang["particles"]) == []
    assert bn_lang.particles == frozenset()
    # STOPWORDS: bn stores frozenset(raw); smoke stores set(raw). Identical
    # membership either way.
    assert set(smoke_lang["stopwords"]) == set(bn_lang.stopwords)
    for word in _EXPECTED_STANDALONE_STOPWORDS:
        assert word in smoke_lang["stopwords"]


def test_he_preset_stopwords_are_standalone_not_proclitics():
    """No shipped stopword is a single-letter proclitic, and the list is a
    sound size (a stub, not a token gesture)."""
    lang = bn.load_language_config("he.json", languages_dir=REAL_LANGUAGES_DIR)
    # Every single-letter attaching prefix (ha/be/ke/le/mi/she/ve).
    proclitics = {"ה", "ב", "כ", "ל", "מ", "ש", "ו"}
    for word in lang.stopwords:
        assert len(word) >= 2, (
            f"stopword {word!r} is a single character -- a Hebrew single-letter "
            "form is a proclitic, never a standalone function word"
        )
        assert word not in proclitics
    assert 30 <= len(lang.stopwords) <= 45, (
        f"expected a sound ~30-45 word standalone-function-word list, got "
        f"{len(lang.stopwords)}"
    )


def test_he_preset_particles_empty_is_load_bearing(tmp_path):
    """The empty-PARTICLES invariant is a real, load-bearing property of the
    shipped file -- a mutation that adds a particle still LOADS (it is a valid
    shape) but breaks the ``particles == frozenset()`` assertion, proving that
    assertion is not vacuous."""
    mutated = _write_mutation(tmp_path, PARTICLES=["ben"])
    bn_lang = bn.load_language_config("he.json", languages_dir=mutated.parent)
    assert bn_lang.particles == frozenset({"ben"})
    assert bn_lang.particles != frozenset(), (
        "a PARTICLES mutation must change the resolved particle set -- confirms "
        "test_he_preset_loads's empty-set assertion is meaningful"
    )
    # The smoke loader accepts the same valid shape identically.
    smoke_lang = smoke.load_particle_config(mutated)
    assert smoke_lang["particles"] == ["ben"]


def test_he_preset_elision_true_null_regex_rejected(tmp_path):
    """A same-schema mutation flipping ``has_elision: true`` while leaving
    ``ELISION_RE: null`` is fatal in BOTH loaders (elision requires a
    non-empty 2-group regex) -- so a malformed Hebrew preset can never load
    half-valid."""
    mutated = _write_mutation(tmp_path, has_elision=True, ELISION_RE=None)
    with pytest.raises(bn.BootstrapNamesError):
        bn.load_language_config("he.json", languages_dir=mutated.parent)
    # smoke's fatal() calls sys.exit(2) -> SystemExit.
    with pytest.raises(SystemExit):
        smoke.load_particle_config(mutated)
