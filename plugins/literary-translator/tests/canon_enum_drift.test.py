"""tests/canon_enum_drift.test.py -- #138 (`basis:"sense_translated"`) drift
guards for the literary-translator canon subsystem. See PLAN-138.md /
CONTRACT-138.md for the full spec; this file covers:

  1. TP-2 -- enum-triple equality: canon-entry.schema.json's `basis` enum,
     canon-batch.schema.json's ACCEPTED-branch `basis` enum, and its QUEUED-
     branch `basis` enum must all be the SAME set, and that set must
     contain `sense_translated`.

  2. TP-2c -- the two-copy SYNC guard: the `sense_translated` conditional
     (note+is_proper_name required, is_proper_name pinned to `true`,
     note/canonical_target_form pattern `\\S`, source forbidden) is
     authored TWICE by design -- once in canon-entry.schema.json, once
     inlined into canon-batch.schema.json's ACCEPTED branch (so that file
     validates standalone, see its own module description) -- and the two
     copies must agree byte-for-byte on their normalized shape.

  3. TP-3 -- prompt/schema agreement (anti-inert-fix): every prose/template
     surface that hand-enumerates the `basis` values is parsed as an EXACT
     SET and compared against the shipped canon-entry.schema.json enum --
     never a substring check, which the new value's own prose would make
     vacuously pass. This exists because glossary-pass-wf.template.js is
     REGENERATED into every scaffolded project on every run; a schema-only
     fix with a stale prompt is a silent no-op end to end (the dispatched
     agent never learns the new value exists).

Every parser below is a targeted regex/JSON-path extraction of the EXACT
allowed-values list each artifact declares, not a keyword search -- an
artifact listing the wrong four-of-five (or a stray sixth) value still
reds.
"""
import json
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS_DIR = SKILL_ROOT / "assets"
SCHEMAS_DIR = ASSETS_DIR / "schemas"
TEMPLATES_DIR = ASSETS_DIR / "templates"
REFERENCES_DIR = SKILL_ROOT / "references"

CANON_ENTRY_SCHEMA = SCHEMAS_DIR / "canon-entry.schema.json"
CANON_BATCH_SCHEMA = SCHEMAS_DIR / "canon-batch.schema.json"
GLOSSARY_WF_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"
GLOSSARY_TASK_TEMPLATE = TEMPLATES_DIR / "glossary_TASK.template.md"
TRANSLATE_TASK_TEMPLATE = TEMPLATES_DIR / "translate_TASK.template.md"
STYLE_BIBLE_TEMPLATE = TEMPLATES_DIR / "style_bible.template.md"
OBSIDIAN_SPEC = REFERENCES_DIR / "output-target-adapters" / "obsidian.md"

for _path in (
    CANON_ENTRY_SCHEMA,
    CANON_BATCH_SCHEMA,
    GLOSSARY_WF_TEMPLATE,
    GLOSSARY_TASK_TEMPLATE,
    TRANSLATE_TASK_TEMPLATE,
    STYLE_BIBLE_TEMPLATE,
    OBSIDIAN_SPEC,
):
    assert _path.is_file(), f"expected file not found: {_path}"

SENSE_TRANSLATED = "sense_translated"


def _load_schema(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ===========================================================================
# TP-2 -- enum-triple equality across the three basis-enum sites.
# ===========================================================================


def _canon_entry_basis_enum():
    schema = _load_schema(CANON_ENTRY_SCHEMA)
    return set(schema["properties"]["basis"]["enum"])


def _canon_batch_basis_enum(branch_index):
    schema = _load_schema(CANON_BATCH_SCHEMA)
    branch = schema["items"]["oneOf"][branch_index]
    return set(branch["properties"]["basis"]["enum"])


def test_basis_enum_triple_is_in_sync_and_contains_sense_translated():
    entry_enum = _canon_entry_basis_enum()
    accepted_enum = _canon_batch_basis_enum(0)
    queued_enum = _canon_batch_basis_enum(1)

    assert entry_enum == accepted_enum == queued_enum, (
        "the three basis enums have drifted apart:\n"
        f"canon-entry: {sorted(entry_enum)}\n"
        f"canon-batch ACCEPTED: {sorted(accepted_enum)}\n"
        f"canon-batch QUEUED: {sorted(queued_enum)}"
    )
    assert SENSE_TRANSLATED in entry_enum


# ===========================================================================
# TP-2c -- the sense_translated conditional's two-copy sync guard.
# ===========================================================================


def _find_sense_translated_conditional(schema_node):
    """Locate the allOf branch whose `if.properties.basis.const ==
    "sense_translated"` and return its `then` clause. #138's schema
    restructure (S1) turns the pre-existing bare top-level if/then into
    `allOf:[{if,then},{if,then}]` -- a sibling bare if/then would be a
    DUPLICATE JSON key that json.load silently drops the first of, so this
    walks allOf, never a bare top-level if/then, to prove the restructure
    actually happened. Returns None if no such branch exists.
    """
    for branch in schema_node.get("allOf", []):
        basis_const = branch.get("if", {}).get("properties", {}).get("basis", {}).get("const")
        if basis_const == SENSE_TRANSLATED:
            return branch.get("then", {})
    return None


def _normalize_sense_translated_shape(then_clause):
    props = then_clause.get("properties", {})
    return {
        "required": frozenset(then_clause.get("required", [])),
        "is_proper_name_const": props.get("is_proper_name", {}).get("const"),
        "note_pattern": props.get("note", {}).get("pattern"),
        "canonical_target_form_pattern": props.get("canonical_target_form", {}).get("pattern"),
        "source": props.get("source"),
    }


def test_sense_translated_conditional_sync_between_canon_entry_and_batch_accepted():
    entry_schema = _load_schema(CANON_ENTRY_SCHEMA)
    batch_schema = _load_schema(CANON_BATCH_SCHEMA)

    entry_then = _find_sense_translated_conditional(entry_schema)
    assert entry_then is not None, (
        "canon-entry.schema.json: no allOf branch keyed on basis==\"sense_translated\""
    )

    accepted_branch = batch_schema["items"]["oneOf"][0]
    assert accepted_branch.get("title") == "ACCEPTED"
    batch_then = _find_sense_translated_conditional(accepted_branch)
    assert batch_then is not None, (
        "canon-batch.schema.json ACCEPTED branch: no allOf branch keyed on "
        "basis==\"sense_translated\""
    )

    entry_shape = _normalize_sense_translated_shape(entry_then)
    batch_shape = _normalize_sense_translated_shape(batch_then)
    assert entry_shape == batch_shape, (
        "the two sense_translated conditionals have drifted apart:\n"
        f"canon-entry: {entry_shape}\ncanon-batch ACCEPTED: {batch_shape}"
    )

    # Pin the CONTRACT-specified shape exactly, not just mutual agreement --
    # two copies could drift together to something equally wrong.
    assert entry_shape["required"] == frozenset({"note", "is_proper_name"})
    assert entry_shape["is_proper_name_const"] is True
    assert entry_shape["note_pattern"] == r"\S"
    assert entry_shape["canonical_target_form_pattern"] == r"\S"
    assert entry_shape["source"] is False


# ===========================================================================
# TP-3 -- every prompt/doc surface that hand-enumerates the basis values,
# parsed as an exact SET, never a substring check.
# ===========================================================================


def _parse_glossary_wf_alternation():
    """glossary-pass-wf.template.js's `batchDispatchPrompt` -- the
    "basis (...)" alternation inside a JS template-string literal (so
    quotes are backslash-escaped in the SOURCE bytes; normalize before
    extracting)."""
    text = GLOSSARY_WF_TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r"basis \(([^)]+)\)", text)
    assert m, 'no "basis (...)" alternation found in glossary-pass-wf.template.js'
    span = m.group(1).replace('\\"', '"')
    values = re.findall(r'"([a-z_]+)"', span)
    assert values, f"no quoted basis values extracted from: {span!r}"
    return set(values)


def _parse_glossary_task_prose_list():
    """glossary_TASK.template.md's `- **`basis`** (accepted items only) --
    exactly one of:` bullet list -- each value is its own indented
    sub-bullet `  - **`value`** -- ...`."""
    lines = GLOSSARY_TASK_TEMPLATE.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, ln in enumerate(lines) if "**`basis`**" in ln and "exactly one of" in ln),
        None,
    )
    assert start is not None, "no '**`basis`** ... exactly one of:' anchor line found"
    values = []
    for ln in lines[start + 1:]:
        if re.match(r"- ", ln):
            # A new TOP-LEVEL bullet (zero indent) ends the basis sub-list --
            # e.g. "- **Nicknames, epithets, and aliases...". Sub-bullets
            # themselves are indented, and their own wrapped continuation
            # lines are indented further still, so neither ever hits this.
            break
        m = re.match(r"\s*- \*\*`([a-z_]+)`\*\*", ln)
        if m is not None:
            values.append(m.group(1))
    assert values, "no indented basis sub-bullets found under the anchor line"
    return set(values)


def _parse_glossary_task_json_skeleton():
    """The same file's example-return JSON skeleton: `"basis":
    "established|transliterated|title|not_a_name",` -- a pipe-list, not
    real JSON (it's a human-readable placeholder inside a fenced example),
    so this is a targeted regex, not json.load."""
    text = GLOSSARY_TASK_TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r'"basis":\s*"([a-z_|]+)"', text)
    assert m, '"basis": "...|..." skeleton line not found in glossary_TASK.template.md'
    return set(m.group(1).split("|"))


def _parse_translate_task_names_list():
    """translate_TASK.template.md's `names[]` shape declaration --
    `"basis": "established|transliterated"` -- same pipe-list shape as the
    glossary JSON skeleton above."""
    text = TRANSLATE_TASK_TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r'"basis":\s*"([a-z_|]+)"', text)
    assert m, '"basis": "...|..." names[] shape not found in translate_TASK.template.md'
    return set(m.group(1).split("|"))


def _parse_style_bible_g_summary():
    """style_bible.template.md section G's frozen-as-of summary clause --
    `entry count by basis (`established` / `transliterated` / `title` /
    `sense_translated` / `not_a_name`), plus how many entries still sit in
    `review_queue`` -- the parenthesized span is exactly the basis-enum
    list; `review_queue` (a DISPOSITION, not a basis) sits outside the
    parens and must never leak into the parsed set."""
    text = STYLE_BIBLE_TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r"by basis \(([^)]+)\)", text)
    assert m, "no 'by basis (...)' clause found in style_bible.template.md section G"
    values = re.findall(r"`([a-z_]+)`", m.group(1))
    assert values, f"no backtick-quoted basis values extracted from: {m.group(1)!r}"
    assert "review_queue" not in values, (
        "parser bug: captured span leaked past the 'by basis (...)' boundary"
    )
    return set(values)


def _parse_obsidian_frontmatter_enum():
    """obsidian.md:53 -- the entity-note frontmatter's `basis:` pipe-enum
    line inside the fenced YAML example block."""
    text = OBSIDIAN_SPEC.read_text(encoding="utf-8")
    m = re.search(r"^basis:\s*([a-z_ |]+)$", text, re.MULTILINE)
    assert m, "no 'basis: a | b | c' frontmatter line found in obsidian.md"
    values = {v.strip() for v in m.group(1).split("|")}
    assert values, f"no basis values extracted from: {m.group(1)!r}"
    return values


_TP3_SURFACES = [
    ("glossary-pass-wf.template.js alternation", _parse_glossary_wf_alternation),
    ("glossary_TASK.template.md prose bullet list", _parse_glossary_task_prose_list),
    ("glossary_TASK.template.md JSON skeleton", _parse_glossary_task_json_skeleton),
    ("translate_TASK.template.md names[] shape", _parse_translate_task_names_list),
    ("style_bible.template.md section-G summary clause", _parse_style_bible_g_summary),
    ("obsidian.md frontmatter enum", _parse_obsidian_frontmatter_enum),
]


@pytest.mark.parametrize(
    "label, parser", _TP3_SURFACES, ids=[s[0] for s in _TP3_SURFACES]
)
def test_prompt_surface_matches_schema_basis_enum(label, parser):
    schema_enum = _canon_entry_basis_enum()
    parsed = parser()
    assert parsed == schema_enum, (
        f"{label} declares {sorted(parsed)}, but canon-entry.schema.json's "
        f"basis enum is {sorted(schema_enum)}"
    )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
