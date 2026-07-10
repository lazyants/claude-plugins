"""tests/agent_schema_top_level_object.test.py -- locks the #87 fix: every
JS object literal passed as the `schema:` option to a real `agent(...)`
call in `mass-translate-wf.template.js` or `glossary-pass-wf.template.js`
must be top-level `type:"object"` with NO top-level `oneOf`/`allOf`/`anyOf`.

Why (CONTRACT-1.2.0-reliability.md section 1; PLAN's "#87 schema
resolution" table): an agent's `schema` option is a tool-use API
`input_schema`. A discriminated `oneOf` (or `array`, in the old
`CANON_BATCH_SCHEMA` case) at the TOP level cannot be a tool `input_schema`
-- the API rejects it outright, HTTP 400 on the very first dispatch. That
was #87: glossary's old `CANON_BATCH_SCHEMA` (top-level `array`) blocked
W3; three top-level-`oneOf` schemas in `mass-translate-wf.template.js`
blocked W5. The fix FLATTENS every agent-facing schema to a plain
`type:"object"` with no top-level combinator; branch discrimination those
combinators used to express is instead enforced by exact-key-set JS guards
at each consume site (CONTRACT section 5) and by the UNCHANGED strong
`oneOf` on-disk `.schema.json` files, which validate the underlying
*scripts'* stdout, never the agent's relayed object.

This file locks TWO independent things:

  1. Discovery, not a hardcoded list: it scans each template's real source
     for every `agent(...)` call site that passes a `schema:` option,
     extracts the referenced identifier, locates that identifier's own
     `const NAME = { ... };` declaration, and asserts the PARSED literal is
     top-level `object` with no top-level `oneOf`/`allOf`/`anyOf`. A future
     schema added to either template's `agent()` call sites is picked up
     automatically -- this file does not need editing when the call-site
     list changes, only when the *discovery* itself needs review (see
     `test_scan_finds_expected_call_sites_*` below, which pins today's
     known call-site set so a change is visible, not silent).
  2. Structural-parity against the CONTRACT's own pinned literal text (its
     section 1, quoted verbatim in the constants below) for the four
     schemas the 1.2.0 build flattens/adds: `REVIEW_ARTIFACT_SCHEMA`,
     `LEDGER_WRITE_SCHEMA`, `LEDGER_MERGE_SCHEMA` (all three owned by
     mass-translate-wf.template.js), and `CANON_VERIFY_SCHEMA` (NEW, owned
     by glossary-pass-wf.template.js). Plus `REVIEW_SCHEMA` (unchanged
     4-field shape, still owned by mass-translate-wf.template.js) is
     checked for its documented "does NOT include dispatch_token"
     invariant.
  3. `CANON_BATCH_SCHEMA` is asserted POSITIVELY GONE from
     glossary-pass-wf.template.js -- no `const CANON_BATCH_SCHEMA =`
     declaration and no `schema: CANON_BATCH_SCHEMA` usage anywhere. Per
     the PLAN, glossary batch dispatch becomes schema-less fire-and-forget
     entirely (never flattened, unlike the ledger/artifact/verify
     schemas) -- there is no agent-facing literal for it at all any more.
     The template's own header comment legitimately mentions the bare
     string "CANON_BATCH_SCHEMA" in prose explaining why it's gone; this
     file's absence check is scoped to the two structural shapes (a const
     declaration, a schema: usage), not a blind prose-tripping substring
     ban.

No JS runtime dependency is introduced here (matching this suite's
existing convention -- see `review_prompt_schema_drift.test.py`'s own
docstring): this file reuses that module's hand-rolled JS object-literal
parser (`parse_js_object_literal`/`extract_const_object_literal`/
`_find_balanced_brace_span`/`_tokenize`) and its canonicalize-and-compare
helper (`_canonicalize`/`assert_schema_structural_parity`) via
`importlib.util.spec_from_file_location`, rather than vendoring a second
copy of either.

A regression-catcher pass at the end proves both comparison helpers this
file relies on (the top-level-object-no-combinator assertion, and the
imported structural-parity assertion) are not vacuously true -- each is
exercised against a deliberately mutated copy and must raise.
"""
import importlib.util
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
MASS_TRANSLATE_PATH = TEMPLATES_DIR / "mass-translate-wf.template.js"
GLOSSARY_PATH = TEMPLATES_DIR / "glossary-pass-wf.template.js"

for _p in (MASS_TRANSLATE_PATH, GLOSSARY_PATH):
    assert _p.is_file(), f"expected plugin template not found: {_p}"


# ---------------------------------------------------------------------------
# Reuse review_prompt_schema_drift.test.py's hand-rolled JS object-literal
# parser and canonicalize-and-compare helper, rather than vendoring a second
# copy of either (house style -- see this module's own docstring).
# ---------------------------------------------------------------------------

_DRIFT_TEST_PATH = Path(__file__).resolve().parent / "review_prompt_schema_drift.test.py"
assert _DRIFT_TEST_PATH.is_file(), f"expected sibling test file not found: {_DRIFT_TEST_PATH}"

_drift_spec = importlib.util.spec_from_file_location(
    "review_prompt_schema_drift_shared_for_agent_schema_top_level_object", _DRIFT_TEST_PATH
)
_drift = importlib.util.module_from_spec(_drift_spec)
_drift_spec.loader.exec_module(_drift)

parse_js_object_literal = _drift.parse_js_object_literal
extract_const_object_literal = _drift.extract_const_object_literal
_find_balanced_brace_span = _drift._find_balanced_brace_span
assert_schema_structural_parity = _drift.assert_schema_structural_parity


# ---------------------------------------------------------------------------
# Discovery: every `agent(...)` call site that passes a `schema:` option.
# ---------------------------------------------------------------------------

_AGENT_CALL_RE = re.compile(r"\bagent\s*\(")
_SCHEMA_KEY_RE = re.compile(r"\bschema:\s*([A-Za-z_$][A-Za-z0-9_$]*)")
_CLOSES_CALL_RE = re.compile(r"\s*,?\s*\)")


def find_schema_option_identifiers(source: str) -> dict[str, list[int]]:
    """Scans `source` for every real `agent(...)` call and, for each call
    whose options object carries a `schema:` key, returns
    {identifier_name: [call-start-offsets, ...]}.

    For every schema-carrying call site in both shipped templates, the
    prompt-text argument is a bare function-call/identifier expression with
    no embedded `{` of its own (confirmed by inspection) -- so "the first
    `{` after `agent(`" always lands on the real options object, never a
    decoy inside the prompt text. As a defense-in-depth sanity check, this
    also confirms that brace-object is genuinely the LAST argument of THIS
    `agent(...)` call (only whitespace/an optional trailing comma before
    the call's own closing `)`) before trusting it -- so a future prompt
    builder that DOES embed a `{...}` literal in its own argument would
    fail this sanity check and be skipped here (silently missing that call
    site) rather than mis-extracting a decoy; the `test_scan_finds_expected
    _call_sites_*` tests below pin today's known site count precisely so
    such a silent miss would show up as a set-mismatch failure, not go
    unnoticed.
    """
    identifiers: dict[str, list[int]] = {}
    for m in _AGENT_CALL_RE.finditer(source):
        brace_idx = source.find("{", m.end())
        if brace_idx == -1:
            continue
        end = _find_balanced_brace_span(source, brace_idx)
        if not _CLOSES_CALL_RE.match(source, end):
            continue  # this brace-object isn't the call's last argument
        call_options_text = source[brace_idx:end]
        schema_match = _SCHEMA_KEY_RE.search(call_options_text)
        if schema_match:
            identifiers.setdefault(schema_match.group(1), []).append(m.start())
    return identifiers


# ---------------------------------------------------------------------------
# The core assertion: top-level object, no top-level combinator.
# ---------------------------------------------------------------------------

_COMBINATOR_KEYS = ("oneOf", "allOf", "anyOf")


def assert_top_level_object_no_combinator(schema, *, label: str) -> None:
    """A tool-use `agent()` `schema` option is a tool `input_schema` -- the
    API rejects (HTTP 400 on first dispatch) anything whose TOP level isn't
    a plain `object`, including a top-level `oneOf`/`allOf`/`anyOf` (exactly
    #87) or a non-object top-level `type` (the old glossary
    `CANON_BATCH_SCHEMA`, top-level `array`)."""
    assert isinstance(schema, dict), f"{label}: parsed schema is not an object literal, got {type(schema).__name__}"
    assert schema.get("type") == "object", (
        f"{label}: top-level 'type' must be exactly \"object\" (an agent's "
        f"`schema` option is a tool input_schema; the tool-use API rejects "
        f"any other top-level type outright -- HTTP 400 on first dispatch, "
        f"the #87 bug this build fixes), got {schema.get('type')!r}"
    )
    present_combinators = [k for k in _COMBINATOR_KEYS if k in schema]
    assert not present_combinators, (
        f"{label}: top-level schema must carry none of {_COMBINATOR_KEYS} "
        f"(a discriminated oneOf/allOf/anyOf cannot be a tool input_schema "
        f"-- exactly the #87 HTTP-400-on-dispatch bug), found: {present_combinators}"
    )


# ---------------------------------------------------------------------------
# CONTRACT section 1's own pinned literal text, quoted verbatim (this file's
# job is to prove the SHIPPED templates match this exactly, not to guess at
# it) -- REVIEW_SCHEMA is handled separately below (its own dedicated
# checks), since it is a projection/parity case, not a flat-literal-from-
# scratch case like these four.
# ---------------------------------------------------------------------------

PINNED_CONTRACT_SHAPES = {
    "REVIEW_ARTIFACT_SCHEMA": {
        "type": "object",
        "additionalProperties": False,
        "required": ["match"],
        "properties": {
            "match": {"type": "boolean"},
            "mismatch_detail": {"type": "string"},
        },
    },
    "LEDGER_WRITE_SCHEMA": {
        "type": "object",
        "additionalProperties": False,
        "required": ["success"],
        "properties": {
            "success": {"type": "boolean"},
            "status": {"type": "string"},
            "fragment_path": {"type": "string"},
            "fragment_sha1": {"type": "string"},
            "error": {"type": "string"},
            "exit_code": {"type": "integer"},
            "stderr": {"type": "string"},
        },
    },
    "LEDGER_MERGE_SCHEMA": {
        "type": "object",
        "additionalProperties": False,
        "required": ["success"],
        "properties": {
            "success": {"type": "boolean"},
            "ledger_path": {"type": "string"},
            "n_segments": {"type": "integer"},
            "missing_segments": {"type": "array", "items": {"type": "string"}},
            "stale_segments": {"type": "array", "items": {"type": "string"}},
            "error": {"type": "string"},
            "exit_code": {"type": "integer"},
            "stderr": {"type": "string"},
        },
    },
    "CANON_VERIFY_SCHEMA": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verified"],
        "properties": {
            "verified": {"type": "boolean"},
            "missing": {"type": "array", "items": {"type": "string"}},
        },
    },
}

# Which template owns which const (CONTRACT's "File ownership" section: B
# owns the three mass-translate flat literals plus the unchanged
# REVIEW_SCHEMA; A owns the one glossary literal).
CONST_TEMPLATE_OWNER = {
    "REVIEW_SCHEMA": "mass_translate",
    "REVIEW_ARTIFACT_SCHEMA": "mass_translate",
    "LEDGER_WRITE_SCHEMA": "mass_translate",
    "LEDGER_MERGE_SCHEMA": "mass_translate",
    "CANON_VERIFY_SCHEMA": "glossary",
}

EXPECTED_MASS_TRANSLATE_SCHEMA_IDENTIFIERS = {
    "REVIEW_SCHEMA", "REVIEW_ARTIFACT_SCHEMA", "LEDGER_WRITE_SCHEMA", "LEDGER_MERGE_SCHEMA",
}
EXPECTED_GLOSSARY_SCHEMA_IDENTIFIERS = {"CANON_VERIFY_SCHEMA"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mass_translate_source() -> str:
    return MASS_TRANSLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def glossary_source() -> str:
    return GLOSSARY_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def template_source_by_owner(mass_translate_source, glossary_source) -> dict:
    return {"mass_translate": mass_translate_source, "glossary": glossary_source}


@pytest.fixture(scope="module")
def mass_translate_schema_identifiers(mass_translate_source) -> dict:
    return find_schema_option_identifiers(mass_translate_source)


@pytest.fixture(scope="module")
def glossary_schema_identifiers(glossary_source) -> dict:
    return find_schema_option_identifiers(glossary_source)


@pytest.fixture(scope="module")
def parsed_schemas(template_source_by_owner) -> dict:
    """Every schema const named in CONST_TEMPLATE_OWNER, parsed from its
    owning template's real source -- the single source of truth every test
    below reads from."""
    result = {}
    for const_name, owner in CONST_TEMPLATE_OWNER.items():
        source = template_source_by_owner[owner]
        literal_text = extract_const_object_literal(source, const_name)
        result[const_name] = parse_js_object_literal(literal_text)
    return result


# ---------------------------------------------------------------------------
# 1. Discovery pins today's known call-site set -- a silent miss in
#    find_schema_option_identifiers (see its own docstring) or a schema
#    quietly dropped from/added to an agent() call would show up here as a
#    set mismatch, not go unnoticed.
# ---------------------------------------------------------------------------

def test_scan_finds_expected_call_sites_in_mass_translate_template(mass_translate_schema_identifiers):
    assert set(mass_translate_schema_identifiers.keys()) == EXPECTED_MASS_TRANSLATE_SCHEMA_IDENTIFIERS, (
        f"expected exactly {sorted(EXPECTED_MASS_TRANSLATE_SCHEMA_IDENTIFIERS)} as the schema-bearing "
        f"agent() call sites in {MASS_TRANSLATE_PATH.name}, found "
        f"{sorted(mass_translate_schema_identifiers.keys())}"
    )
    # Every identifier found is used at exactly one call site in this file
    # today -- a sanity fact this test also locks, so a future accidental
    # duplicate dispatch of e.g. LEDGER_WRITE_SCHEMA is visible.
    for const_name, offsets in mass_translate_schema_identifiers.items():
        assert len(offsets) == 1, (
            f"{const_name} is used as a schema: option at {len(offsets)} call "
            f"sites in {MASS_TRANSLATE_PATH.name}, expected exactly 1"
        )


def test_scan_finds_expected_call_sites_in_glossary_template(glossary_schema_identifiers):
    assert set(glossary_schema_identifiers.keys()) == EXPECTED_GLOSSARY_SCHEMA_IDENTIFIERS, (
        f"expected exactly {sorted(EXPECTED_GLOSSARY_SCHEMA_IDENTIFIERS)} as the schema-bearing "
        f"agent() call sites in {GLOSSARY_PATH.name}, found "
        f"{sorted(glossary_schema_identifiers.keys())}"
    )


# ---------------------------------------------------------------------------
# 2. CANON_BATCH_SCHEMA is positively GONE from the glossary template: no
#    const declaration, no schema: usage. The header comment's own
#    explanatory prose legitimately contains the bare string
#    "CANON_BATCH_SCHEMA" -- this check is scoped to the two structural
#    shapes a regression could actually reintroduce, not a blind substring
#    ban that would trip on that prose.
# ---------------------------------------------------------------------------

def test_canon_batch_schema_const_declaration_absent_from_glossary_template(glossary_source):
    m = re.search(r"\bconst\s+CANON_BATCH_SCHEMA\s*=", glossary_source)
    assert m is None, (
        f"found a 'const CANON_BATCH_SCHEMA =' declaration in "
        f"{GLOSSARY_PATH.name} -- per the PLAN, glossary batch dispatch is "
        f"schema-less fire-and-forget (#87); CANON_BATCH_SCHEMA must be "
        f"DELETED outright, not merely flattened like the other three "
        f"literals"
    )


def test_canon_batch_schema_never_used_as_a_schema_option_in_glossary_template(glossary_source):
    m = re.search(r"schema:\s*CANON_BATCH_SCHEMA\b", glossary_source)
    assert m is None, (
        f"found 'schema: CANON_BATCH_SCHEMA' at an agent() call site in "
        f"{GLOSSARY_PATH.name} -- the batch dispatch call must carry no "
        f"agent-facing schema literal at all any more (#87)"
    )


def test_canon_batch_schema_never_appears_in_any_agent_call_options(glossary_schema_identifiers):
    """Complements the two static-text checks above with the SAME
    call-site-discovery machinery every other assertion in this file
    trusts: CANON_BATCH_SCHEMA must never surface as a discovered
    schema-option identifier either."""
    assert "CANON_BATCH_SCHEMA" not in glossary_schema_identifiers


# ---------------------------------------------------------------------------
# 3. The core #87 assertion: every discovered schema-option identifier, in
#    EITHER template, is top-level object with no top-level combinator.
#    Data-driven off CONST_TEMPLATE_OWNER (not a copy-pasted list) -- see
#    module docstring point 1.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("const_name", sorted(CONST_TEMPLATE_OWNER))
def test_agent_schema_is_top_level_object_no_combinator(parsed_schemas, const_name):
    assert_top_level_object_no_combinator(
        parsed_schemas[const_name],
        label=f"{const_name} ({CONST_TEMPLATE_OWNER[const_name]} template)",
    )


# ---------------------------------------------------------------------------
# 4. Structural parity against the CONTRACT's own pinned literal text, for
#    the four flattened/new schemas.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("const_name", sorted(PINNED_CONTRACT_SHAPES))
def test_agent_schema_matches_contract_pinned_shape(parsed_schemas, const_name):
    assert_schema_structural_parity(
        parsed_schemas[const_name],
        PINNED_CONTRACT_SHAPES[const_name],
        label=f"{const_name} vs CONTRACT-1.2.0-reliability.md section 1's pinned literal",
    )


# ---------------------------------------------------------------------------
# 5. REVIEW_SCHEMA-specific: unchanged 4-field projection, does NOT include
#    dispatch_token (CONTRACT section 1's own explicit callout -- the
#    on-disk review.schema.json gains a 5th required dispatch_token field,
#    but REVIEW_SCHEMA stays the original 4-field shape on purpose).
# ---------------------------------------------------------------------------

def test_review_schema_is_the_unchanged_four_field_projection(parsed_schemas):
    schema = parsed_schemas["REVIEW_SCHEMA"]
    assert set(schema["required"]) == {"clean", "coverage_ok", "findings", "draft_sha1"}
    assert schema["additionalProperties"] is False
    assert "dispatch_token" not in schema["properties"], (
        "REVIEW_SCHEMA must NOT include dispatch_token -- it is an "
        "intentional 4-field PROJECTION of the 5-field on-disk "
        "review.schema.json (which carries dispatch_token as run-scoping "
        "freshness metadata, never part of the agent-facing verdict)"
    )


# ---------------------------------------------------------------------------
# Regression-catchers: neither comparison helper this file relies on is
# vacuously true.
# ---------------------------------------------------------------------------

def test_top_level_object_helper_catches_injected_top_level_oneof(parsed_schemas):
    mutated = dict(parsed_schemas["REVIEW_ARTIFACT_SCHEMA"])
    mutated["oneOf"] = [
        {"type": "object", "properties": {"match": {"const": True}}},
        {"type": "object", "properties": {"match": {"const": False}}},
    ]
    with pytest.raises(AssertionError):
        assert_top_level_object_no_combinator(mutated, label="mutated REVIEW_ARTIFACT_SCHEMA (injected oneOf)")


def test_top_level_object_helper_catches_non_object_top_level_type(parsed_schemas):
    mutated = dict(parsed_schemas["LEDGER_WRITE_SCHEMA"])
    mutated["type"] = "array"  # the old CANON_BATCH_SCHEMA's own top-level shape
    with pytest.raises(AssertionError):
        assert_top_level_object_no_combinator(mutated, label="mutated LEDGER_WRITE_SCHEMA (type: array)")


def test_top_level_object_helper_accepts_the_real_unmutated_schemas(parsed_schemas):
    """Sanity companion to the two catches above: proves the helper isn't
    just always-raising -- every real, unmutated schema in this file's own
    coverage set must pass cleanly."""
    for const_name in CONST_TEMPLATE_OWNER:
        assert_top_level_object_no_combinator(parsed_schemas[const_name], label=f"real {const_name}")


def test_contract_pinned_shape_helper_catches_a_dropped_required_field(parsed_schemas):
    import json

    mutated = json.loads(json.dumps(PINNED_CONTRACT_SHAPES["LEDGER_MERGE_SCHEMA"]))
    mutated["required"] = []  # CONTRACT pins required: ["success"]
    with pytest.raises(AssertionError):
        assert_schema_structural_parity(
            parsed_schemas["LEDGER_MERGE_SCHEMA"], mutated,
            label="mutated CONTRACT pinned LEDGER_MERGE_SCHEMA (dropped required)",
        )


def test_contract_pinned_shape_helper_catches_a_relaxed_missing_segments_maxitems(parsed_schemas):
    """CONTRACT section 1 is explicit that flat LEDGER_MERGE_SCHEMA's
    missing_segments uses the RELAXED union shape ({type:"array",
    items:{type:"string"}}, no maxItems) -- proves a maxItems:0 creeping
    back in (the OLD success-branch-only shape) would be caught."""
    import json

    mutated = json.loads(json.dumps(PINNED_CONTRACT_SHAPES["LEDGER_MERGE_SCHEMA"]))
    mutated["properties"]["missing_segments"]["maxItems"] = 0
    with pytest.raises(AssertionError):
        assert_schema_structural_parity(
            parsed_schemas["LEDGER_MERGE_SCHEMA"], mutated,
            label="mutated CONTRACT pinned LEDGER_MERGE_SCHEMA (reintroduced maxItems:0)",
        )
