"""tests/segpack_description_source_honesty.test.py

Targets #185: a `description` string must not attribute an extraction fact
to `extract.py.template` as if it were the ONLY possible producing
extractor, and must credit the component that actually produces each fact.
For `source.format: custom`, the co-designed custom extractor
(`scripts/custom_extractors/<value>`) produces `manifest.json`, and the
shared `segpack.py` builds each segpack from it -- so a description that
names `extract.py.template` (or credits the extractor with what segpack.py
actually does) misleads a reader working a custom-source project.

Two things are exercised:

1. **segpack.schema.json** -- every `description` string in the schema
   (walked structurally, not hand-picked by line number) is checked against
   a source-honesty predicate: any description mentioning
   `extract.py.template` must ALSO name a built-in scope (`gutenberg_epub`
   or `plain_text`) AND the exact concept `custom extractor`. A negative
   control proves the predicate is not vacuously true -- an unscoped
   attribution with unrelated "custom" wording must still fail it.

2. **The two other #185 sites this release also touches** (`cache_key.py`'s
   `--field` docstring and `validate_extraction.py`'s FATAL
   `manifest_wellformed` diagnostic) -- regression-locked by asserting their
   exact pre-fix phrases are gone, so the fix's scope can't quietly narrow
   back down to segpack.schema.json alone.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

SEGPACK_SCHEMA = SCHEMAS_DIR / "segpack.schema.json"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"
VALIDATE_EXTRACTION_SCRIPT = SCRIPTS_DIR / "validate_extraction.py"


def _load_segpack_schema() -> dict:
    return json.loads(SEGPACK_SCHEMA.read_text(encoding="utf-8"))


def _iter_descriptions(node):
    """Walks an arbitrary JSON-Schema fragment (dicts/lists of any depth),
    yielding every string value found under a `description` key --
    `properties`, `items`, and nested sub-schemas included. Structural, so a
    future field added anywhere in the schema is covered automatically."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield value
            else:
                yield from _iter_descriptions(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_descriptions(item)


def _is_source_honest(description: str) -> bool:
    """#185's predicate: a description that mentions `extract.py.template`
    must ALSO name a built-in scope (`gutenberg_epub` or `plain_text`) AND
    the exact concept `custom extractor` -- otherwise it reads as though
    extract.py.template were the only possible producing extractor, which is
    false for `source.format: custom` (a co-designed custom extractor).
    A description that never mentions `extract.py.template` at all trivially
    passes -- it makes no universal-attribution claim to begin with."""
    if "extract.py.template" not in description:
        return True
    names_builtin_scope = "gutenberg_epub" in description or "plain_text" in description
    names_custom_extractor = "custom extractor" in description
    return names_builtin_scope and names_custom_extractor


class TestSegpackSchemaDescriptionSourceHonesty:
    def test_every_description_mentioning_extract_py_template_is_source_scoped(self):
        schema = _load_segpack_schema()
        offenders = [d for d in _iter_descriptions(schema) if not _is_source_honest(d)]
        assert not offenders, (
            "segpack.schema.json has description(s) attributing a segpack "
            "fact to extract.py.template without naming the custom-extractor "
            "split:\n" + "\n".join(f"  - {d!r}" for d in offenders)
        )

    def test_negative_control_catches_an_unscoped_attribution(self):
        """Proves the predicate above is not vacuous: a description with
        unrelated 'custom' wording (no 'custom extractor' concept, no
        built-in scope) alongside an unscoped extract.py.template
        attribution must FAIL the predicate."""
        bad = (
            "Uses a custom naming scheme for this field, as recorded by "
            "extract.py.template's some_check."
        )
        assert not _is_source_honest(bad)

    def test_segpack_schema_is_valid_json_schema(self):
        schema = _load_segpack_schema()
        jsonschema.Draft202012Validator.check_schema(schema)

    def test_no_description_universalizes_a_segpack_py_internal_onto_the_extractor(self):
        """codex round-2: the blanket 'the producing extractor' swap was itself
        inaccurate for two fields -- segpack.py (shared, shipped) does the
        order_index sort and the apparatus_policy-driven footnote selection,
        not the extractor, so no extractor (built-in or custom) can be
        credited with either. Token-absence, not just the mention-scoping
        predicate above, since a reworded phrase could still smuggle back an
        inaccurate universal attribution under different wording."""
        schema = _load_segpack_schema()
        descriptions = list(_iter_descriptions(schema))
        for phrase in ("re-ranking pass", "footnote-grouping distinction"):
            offenders = [d for d in descriptions if phrase in d]
            assert not offenders, (
                f"segpack.schema.json still has a description crediting an "
                f"extractor with segpack.py's own {phrase!r} internal:\n"
                + "\n".join(f"  - {d!r}" for d in offenders)
            )

    def test_order_index_and_footnotes_descriptions_name_the_real_actor(self):
        """Positive companion to the absence check above: order_index's
        description must credit segpack.py by name (it copies the value from
        the manifest block and sorts by it), and footnotes' description must
        name footnotes.apparatus_policy (the field segpack.py actually
        branches inclusion on) -- so a reword that merely deletes the old
        phrase without stating the real mechanism still fails here."""
        schema = _load_segpack_schema()
        order_index_description = schema["properties"]["blocks"]["items"]["properties"][
            "order_index"
        ]["description"]
        assert "segpack.py" in order_index_description, (
            "segpack.schema.json's order_index description no longer credits "
            "segpack.py with the copy/sort it actually performs"
        )

        footnotes_description = schema["properties"]["footnotes"]["description"]
        assert "apparatus_policy" in footnotes_description, (
            "segpack.schema.json's footnotes description no longer names "
            "footnotes.apparatus_policy as what actually drives inclusion"
        )


class TestExpandedSourceHonestySitesAbsence:
    """Regression-locks the two #185 sites beyond segpack.schema.json that
    also carried an unscoped `extract.py.template` attribution this release
    reworded. cache_key.py is owned by a different teammate this round --
    that assertion is expected to stay red until their edit lands."""

    def test_cache_key_py_no_longer_attributes_two_phase_write_to_extract_py_template(self):
        text = CACHE_KEY_SCRIPT.read_text(encoding="utf-8")
        assert "Used by extract.py.template's two-phase" not in text

    def test_validate_extraction_py_diagnostic_is_source_neutral(self):
        text = VALIDATE_EXTRACTION_SCRIPT.read_text(encoding="utf-8")
        assert "the shape extract.py.template" not in text


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
