"""tests/canon_batch_inline_shape_drift.test.py

Targets a 5th restatement site the earlier `category`-wiring regression
lock (see ``tests/canon_format_validation.test.py``) missed:
``glossary-pass-wf.template.js``'s own ``CANON_BATCH_ACCEPTED_SHAPE``
inline JS object literal (passed straight to the codex glossary-pass agent
call as its ``schema`` argument, ~line 153) restates the SAME
"accepted"-branch shape that ``canon-batch.schema.json``'s own
``items.oneOf[0]`` declares -- both are ``additionalProperties:false``, so
a field added to one but not the other is silently REJECTED at whichever
surface didn't get it. `category` was wired through canon-entry.schema.json,
canon-batch.schema.json's accepted branch, and canon_validate.py's
CANON_ENTRY_FIELDS allow-list, but NOT this inline template literal --
meaning a real glossary-pass run assigning `category` to an accepted item
would have been rejected by the agent-call's own schema before
canon_validate.py ever saw it.

Per this plugin's own testing discipline (see schema_literal_drift.test.py's
module docstring: "prefer deriving the expected field set programmatically
... rather than hand-typing the same list twice"), this file does NOT
hand-type the expected property set anywhere -- it parses BOTH real, shipped
sources and diffs them:

  - the JS literal, via a small brace/bracket-depth-aware top-level-key
    scanner (handles nested `{...}`/`[...]` values and skips string-literal
    contents, so a property whose own description happens to mention a
    brace never gets mistaken for a sibling key);
  - the JSON schema, via a plain ``json.load`` of the real file.

Only the ACCEPTED branch is in scope here (mirroring the earlier
canon_format_validation.test.py fix, which likewise only extended the
ACCEPTED branch -- the QUEUED branch never claimed to carry `category`).
"""
import json
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
TEMPLATE_PATH = ASSETS_DIR / "templates" / "glossary-pass-wf.template.js"
CANON_BATCH_SCHEMA_PATH = ASSETS_DIR / "schemas" / "canon-batch.schema.json"

assert TEMPLATE_PATH.is_file(), f"expected glossary-pass-wf.template.js at {TEMPLATE_PATH}"
assert CANON_BATCH_SCHEMA_PATH.is_file(), f"expected canon-batch.schema.json at {CANON_BATCH_SCHEMA_PATH}"


# ---------------------------------------------------------------------------
# JS-literal extraction -- generic, not hand-tuned to today's field list.
# ---------------------------------------------------------------------------


def _skip_string_literal(text: str, quote_index: int) -> int:
    """``text[quote_index]`` must be the opening ``'"'``. Returns the index
    just past the closing ``'"'``, honoring backslash escapes (so an escaped
    quote inside the string never ends it early)."""
    n = len(text)
    i = quote_index + 1
    while i < n and text[i] != '"':
        if text[i] == "\\":
            i += 1
        i += 1
    return i + 1


def _find_matching_brace(text: str, open_index: int) -> int:
    """``text[open_index]`` must be ``'{'``. Returns the index of its
    matching ``'}'``, skipping over nested braces/brackets AND string
    literals (so a stray ``{``/``}`` inside a description string never
    desyncs the depth count)."""
    assert text[open_index] == "{", f"expected '{{' at index {open_index}"
    depth = 0
    i = open_index
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string_literal(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError(f"unbalanced braces starting at index {open_index}")


_KEY_RE = re.compile(r'([A-Za-z_$][A-Za-z0-9_$]*)\s*:')


def _top_level_object_keys(obj_content: str) -> set[str]:
    """Given the CONTENT of a JS object literal (the text strictly between
    its outer ``{`` and matching ``}``), returns the set of top-level key
    identifiers -- i.e. keys whose colon sits at brace/bracket depth 0
    relative to this content. Nested ``{...}``/``[...]`` groups (a
    property's own object/array value) and string-literal contents are
    both skipped, so neither can be mistaken for a sibling top-level key."""
    keys: set[str] = set()
    depth = 0
    i = 0
    n = len(obj_content)
    while i < n:
        c = obj_content[i]
        if c == '"':
            i = _skip_string_literal(obj_content, i)
            continue
        if c in "{[":
            depth += 1
            i += 1
            continue
        if c in "}]":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            m = _KEY_RE.match(obj_content, i)
            if m:
                keys.add(m.group(1))
                i = m.end()
                continue
        i += 1
    return keys


def _extract_named_const_object(source: str, const_name: str) -> str:
    """Returns the CONTENT (excluding outer braces) of
    ``const {const_name} = { ... }`` as it literally appears in ``source``."""
    marker = f"const {const_name} = {{"
    start = source.find(marker)
    assert start != -1, f"could not find {marker!r} in the template"
    open_brace_index = start + len(marker) - 1
    close_brace_index = _find_matching_brace(source, open_brace_index)
    return source[open_brace_index + 1 : close_brace_index]


def _accepted_shape_properties_keys() -> set[str]:
    """The top-level key set of CANON_BATCH_ACCEPTED_SHAPE's own
    ``properties: { ... }`` sub-object (not the shape's own top-level keys
    like ``type``/``required``/``if``/``then``)."""
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    shape_content = _extract_named_const_object(source, "CANON_BATCH_ACCEPTED_SHAPE")
    props_marker = "properties: {"
    props_start = shape_content.find(props_marker)
    assert props_start != -1, (
        "CANON_BATCH_ACCEPTED_SHAPE no longer declares its own 'properties: {' "
        "sub-object -- the template's shape changed structurally"
    )
    open_brace_index = props_start + len(props_marker) - 1
    close_brace_index = _find_matching_brace(shape_content, open_brace_index)
    props_content = shape_content[open_brace_index + 1 : close_brace_index]
    return _top_level_object_keys(props_content)


def _accepted_shape_required_list() -> list[str]:
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    shape_content = _extract_named_const_object(source, "CANON_BATCH_ACCEPTED_SHAPE")
    match = re.search(r'required:\s*\[([^\]]*)\]', shape_content)
    assert match, "CANON_BATCH_ACCEPTED_SHAPE no longer declares a 'required: [...]' list"
    return re.findall(r'"([^"]+)"', match.group(1))


# ---------------------------------------------------------------------------
# canon-batch.schema.json's own ACCEPTED branch (items.oneOf[0]).
# ---------------------------------------------------------------------------


def _load_canon_batch_accepted_branch() -> dict:
    schema = json.loads(CANON_BATCH_SCHEMA_PATH.read_text(encoding="utf-8"))
    branch = schema["items"]["oneOf"][0]
    assert branch.get("title") == "ACCEPTED", (
        f"expected items.oneOf[0] to be the ACCEPTED branch, got title={branch.get('title')!r}"
    )
    return branch


# ---------------------------------------------------------------------------
# The drift assertions.
# ---------------------------------------------------------------------------


def test_inline_accepted_shape_properties_match_canon_batch_schema_accepted_branch():
    js_keys = _accepted_shape_properties_keys()
    schema_branch = _load_canon_batch_accepted_branch()
    schema_keys = set(schema_branch["properties"].keys())

    assert js_keys == schema_keys, (
        "glossary-pass-wf.template.js's CANON_BATCH_ACCEPTED_SHAPE.properties "
        "has drifted from canon-batch.schema.json's ACCEPTED branch "
        "(items.oneOf[0].properties) -- both are additionalProperties:false, "
        "so a field present on only one side is silently rejected at "
        "whichever surface lacks it:\n"
        f"  template only: {sorted(js_keys - schema_keys)}\n"
        f"  schema only:   {sorted(schema_keys - js_keys)}"
    )


def test_inline_accepted_shape_required_matches_canon_batch_schema_accepted_branch():
    js_required = _accepted_shape_required_list()
    schema_branch = _load_canon_batch_accepted_branch()
    schema_required = schema_branch["required"]

    assert set(js_required) == set(schema_required), (
        "CANON_BATCH_ACCEPTED_SHAPE.required has drifted from "
        "canon-batch.schema.json's ACCEPTED branch 'required' list:\n"
        f"  template only: {sorted(set(js_required) - set(schema_required))}\n"
        f"  schema only:   {sorted(set(schema_required) - set(js_required))}"
    )


def test_category_specifically_allowed_on_both_surfaces():
    """The concrete regression this file exists to lock: `category` (added
    to canon-entry.schema.json / canon-batch.schema.json / CANON_ENTRY_FIELDS
    by the earlier fix) must ALSO be declared on the inline template
    literal -- not just implied by the general set-equality checks above."""
    js_keys = _accepted_shape_properties_keys()
    schema_keys = set(_load_canon_batch_accepted_branch()["properties"].keys())

    assert "category" in js_keys, (
        "CANON_BATCH_ACCEPTED_SHAPE.properties no longer declares 'category' "
        "-- a real glossary-pass run assigning it would be rejected by this "
        "inline schema before canon_validate.py ever sees the batch"
    )
    assert "category" in schema_keys, "canon-batch.schema.json's ACCEPTED branch no longer declares 'category'"
    # category must stay OPTIONAL on the template side too -- never
    # required, since it's an open, per-project vocabulary.
    assert "category" not in _accepted_shape_required_list()


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
