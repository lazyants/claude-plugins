"""tests/canon_map_delivery.test.py -- regression-lock suite for #130
(delivering the frozen canon target forms to the translate/review prompts).

Prior to this fix, segpack.py's build_pack() split a segment's strong_names
into canon_names (already-canonized source forms) vs new_names, but never
delivered the frozen canonical_target_form itself into the segpack -- so
neither translatePrompt() nor reviewDispatchPrompt() ever saw the actual
target-language rendering, only the bare canon_names source-form list (plus
a stale "use these forms verbatim, no exceptions" instruction that would
have made a correctly-DECLINED rendering look like a review defect anyway,
since a name's canonical citation form is rarely the grammatically-inflected
form a sentence actually requires).

This suite locks down:
  1. build_pack() emits canon_map (source_form -> canonical_target_form) for
     every canon_names entry whose canon.json record carries a non-empty
     canonical_target_form -- a canon entry with an empty/missing target
     form is validly omitted from canon_map (canon_names remains the source
     of truth for "is this name canonized", canon_map's keys are a SUBSET,
     not necessarily equal).
  2. validate_segpack() enforces canon_map's shape: an object, every key a
     non-empty string that is ALSO a member of canon_names, every value a
     non-empty string; canon_map is now a required top-level field.
  3. translate_TASK.template.md / review_TASK.template.md no longer carry
     the false "verbatim, no exceptions" instruction.

Loads the real, shipped segpack.py via importlib (mirrors tests/
segpack_verse_mount.test.py's own `_load_module` helper -- segpack.py's
`from bootstrap_names import ...` only resolves via sys.path[0] under a
real `python3 segpack.py` invocation, so its own scripts/ directory must be
inserted onto sys.path around the in-process load).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS_DIR = SKILL_ROOT / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SEGPACK_SCRIPT = SCRIPTS_DIR / "segpack.py"
LANGUAGES_DIR = ASSETS_DIR / "languages"
TRANSLATE_TASK_TEMPLATE = ASSETS_DIR / "templates" / "translate_TASK.template.md"
REVIEW_TASK_TEMPLATE = ASSETS_DIR / "templates" / "review_TASK.template.md"

assert SEGPACK_SCRIPT.is_file(), f"segpack.py not found at {SEGPACK_SCRIPT}"
assert (LANGUAGES_DIR / "fr.json").is_file(), f"fr.json not found under {LANGUAGES_DIR}"
assert TRANSLATE_TASK_TEMPLATE.is_file(), f"translate_TASK.template.md not found at {TRANSLATE_TASK_TEMPLATE}"
assert REVIEW_TASK_TEMPLATE.is_file(), f"review_TASK.template.md not found at {REVIEW_TASK_TEMPLATE}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors segpack_verse_mount.test.py's own loader exactly (see that
    file's docstring for why the sys.path dance is needed)."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


SEGPACK_MODULE = _load_module("canon_map_delivery_under_test", SEGPACK_SCRIPT, SCRIPTS_DIR)

# Real shipped particle config -- build_pack()'s name-scanning pass needs a
# genuinely valid LanguageConfig (never hand-rolled JSON here).
LANG_CONFIG = SEGPACK_MODULE.load_language_config("fr.json", LANGUAGES_DIR)


def _base_generation_hashes():
    return {"source_extraction_hash": "a" * 40, "source_input_hash": "b" * 40}


def _canon_generation_hashes():
    return {"particle_config_hash": "c" * 40, "derivation_bundle_hash": "d" * 40}


# ---------------------------------------------------------------------------
# 1. build_pack() -- canon_map delivery, via the REAL tokenizer/extractor
#    (no hand-guessed strong_names list): "Jean Valjean" is canonized (with a
#    DISTINCT canonical_target_form, proving canon_map is not just an echo of
#    canon_names) and "Cosette Fantine" is not -- verified against the real
#    fr.json config to land in canon_names/new_names exactly as expected.
# ---------------------------------------------------------------------------


def _manifest_with_two_multiword_names():
    return {
        "segments": [
            {
                "seg": "seg01",
                "title_text": "Chapter One",
                "kind": "body",
                "word_count": 10,
                "block_ids": ["p1"],
            }
        ],
        "blocks": {
            "p1": {
                "id": "p1",
                "order_index": 0,
                "plain_text": (
                    "Jean Valjean marchait dans la rue. "
                    "Cosette Fantine jouait non loin."
                ),
            },
        },
        "footnotes": [],
        "verse": {"store": []},
        "generation_hashes": _base_generation_hashes(),
    }


def _canon_with_one_target_form():
    return {
        "entries": {
            "Jean Valjean": {
                "source_form": "Jean Valjean",
                "is_proper_name": True,
                "canonical_target_form": "Jean Valjean-EN",
                "basis": "transliterated",
                "confidence": "high",
            },
        },
        "generation_hashes": _canon_generation_hashes(),
    }


def test_build_pack_emits_canon_map_for_canonized_name_with_target_form():
    manifest = _manifest_with_two_multiword_names()
    canon = _canon_with_one_target_form()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "omit_apparatus")

    assert pack["canon_names"] == ["Jean Valjean"], pack["canon_names"]
    assert pack["new_names"] == ["Cosette Fantine"], pack["new_names"]
    assert pack["canon_map"] == {"Jean Valjean": "Jean Valjean-EN"}, pack["canon_map"]


def test_build_pack_canon_map_keys_are_a_subset_of_canon_names():
    manifest = _manifest_with_two_multiword_names()
    canon = _canon_with_one_target_form()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "omit_apparatus")

    assert set(pack["canon_map"].keys()) <= set(pack["canon_names"])


def test_build_pack_omits_canon_map_entry_for_empty_target_form():
    """A canon entry with an empty-string canonical_target_form is validly
    omitted from canon_map -- canon_names is still the source of truth for
    "is this name canonized", independent of whether a usable target form
    was ever recorded."""
    manifest = _manifest_with_two_multiword_names()
    canon = _canon_with_one_target_form()
    canon["entries"]["Jean Valjean"]["canonical_target_form"] = ""

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "omit_apparatus")

    assert pack["canon_names"] == ["Jean Valjean"]
    assert pack["canon_map"] == {}


def test_build_pack_omits_canon_map_entry_for_missing_target_form():
    manifest = _manifest_with_two_multiword_names()
    canon = _canon_with_one_target_form()
    del canon["entries"]["Jean Valjean"]["canonical_target_form"]

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "omit_apparatus")

    assert pack["canon_names"] == ["Jean Valjean"]
    assert pack["canon_map"] == {}


def test_build_pack_no_canon_entries_yields_empty_canon_map():
    manifest = _manifest_with_two_multiword_names()
    canon = {"entries": {}, "generation_hashes": _canon_generation_hashes()}

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "omit_apparatus")

    assert pack["canon_names"] == []
    assert pack["canon_map"] == {}


# ---------------------------------------------------------------------------
# 2. validate_segpack() -- canon_map shape enforcement.
# ---------------------------------------------------------------------------


def _pack_with_canon_map(canon_names=("Jean Valjean",), canon_map=None):
    if canon_map is None:
        canon_map = {"Jean Valjean": "Jean Valjean-EN"}
    return {
        "seg": "seg01",
        "title": "Chapter One",
        "kind": "body",
        "word_count": 4,
        "blocks": [],
        "footnotes": [],
        "verses": [],
        "names": list(canon_names),
        "canon_names": list(canon_names),
        "new_names": [],
        "canon_map": canon_map,
        "generation_hashes": {
            "source_extraction_hash": "a" * 40,
            "source_input_hash": "b" * 40,
            "particle_config_hash": "c" * 40,
            "derivation_bundle_hash": "d" * 40,
        },
    }


def test_validate_segpack_accepts_well_formed_canon_map():
    errors = SEGPACK_MODULE.validate_segpack(_pack_with_canon_map())
    assert errors == [], errors


def test_validate_segpack_accepts_empty_canon_map():
    errors = SEGPACK_MODULE.validate_segpack(_pack_with_canon_map(canon_names=(), canon_map={}))
    assert errors == [], errors


def test_validate_segpack_rejects_missing_canon_map_field():
    pack = _pack_with_canon_map()
    del pack["canon_map"]
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("missing required top-level field" in e and "canon_map" in e for e in errors), errors


def test_validate_segpack_rejects_non_dict_canon_map():
    errors = SEGPACK_MODULE.validate_segpack(_pack_with_canon_map(canon_map=["Jean Valjean-EN"]))
    assert any("'canon_map' must be an object" in e for e in errors), errors


@pytest.mark.parametrize("bad_value", [123, None, "", ["x"], {"nested": "obj"}])
def test_validate_segpack_rejects_non_string_or_empty_canon_map_value(bad_value):
    errors = SEGPACK_MODULE.validate_segpack(
        _pack_with_canon_map(canon_map={"Jean Valjean": bad_value})
    )
    assert any("canon_map['Jean Valjean']" in e or "canon_map[" in e for e in errors), errors


def test_validate_segpack_rejects_canon_map_key_not_in_canon_names():
    errors = SEGPACK_MODULE.validate_segpack(
        _pack_with_canon_map(
            canon_names=("Jean Valjean",),
            canon_map={"Jean Valjean": "Jean Valjean-EN", "Ghost": "Ghost-EN"},
        )
    )
    assert any("'canon_map' key 'Ghost' is not in 'canon_names'" in e for e in errors), errors


def test_validate_segpack_rejects_canon_map_when_canon_names_missing_too():
    """A pack missing BOTH canon_names and canon_map is caught by the
    pre-existing missing-top-level-field short-circuit, not silently
    accepted."""
    pack = _pack_with_canon_map()
    del pack["canon_names"]
    del pack["canon_map"]
    errors = SEGPACK_MODULE.validate_segpack(pack)
    assert any("missing required top-level field" in e for e in errors), errors


# ---------------------------------------------------------------------------
# 3. Prose-contract regression: the false "verbatim, no exceptions" framing
#    must be gone from both task templates (#130) -- a correctly-declined
#    rendering of the canonical stem must never read as a violation.
# ---------------------------------------------------------------------------


def test_translate_task_template_no_longer_demands_verbatim_no_exceptions():
    text = TRANSLATE_TASK_TEMPLATE.read_text(encoding="utf-8")
    assert "verbatim, no exceptions" not in text, (
        "translate_TASK.template.md must not tell the translator to render "
        "canon_names forms verbatim, no exceptions -- a correctly declined/"
        "inflected form of the canon_map stem is correct (#130)"
    )
    assert "canon_map" in text


def test_review_task_template_no_longer_demands_verbatim():
    text = REVIEW_TASK_TEMPLATE.read_text(encoding="utf-8")
    assert "forms were used verbatim" not in text, (
        "review_TASK.template.md must not ask the reviewer to check that "
        "canon_names forms were used verbatim -- see #130's declined-stem rule"
    )
    assert "canon_map" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
