"""tests/review_prompt_schema_drift.test.py

Targets the four inline JS schema object literals declared in
``mass-translate-wf.template.js`` -- ``REVIEW_SCHEMA``,
``REVIEW_ARTIFACT_SCHEMA``, ``LEDGER_WRITE_SCHEMA``, ``LEDGER_MERGE_SCHEMA``
-- against their canonical JSON Schema files (``review.schema.json``,
``review-artifact-check.schema.json``, ``ledger-write-confirmation.schema.json``,
``ledger-merge-confirmation.schema.json``). See the plan's own "Exact schema
literals" subsection (build inventory §13): the JS file's own comments claim
each literal "Matches X.schema.json exactly" -- this file locks that claim so
an edit to one representation (JS literal or canonical `.schema.json`) can't
silently diverge from the other. The four literals are declared ABOVE the
`pipeline()` call in the template file specifically because of the documented
temporal-dead-zone gotcha (references/gotchas.md item 10 /
gotcha_workflow_const_tdz_silent_fail.md): a schema literal declared AFTER
its first use silently no-ops there is no runtime error to catch it, so
that ordering is asserted here too, as a regression lock.

There is no JS runtime dependency (e.g. Node) anywhere else in this plugin's
test suite (it is pure Python + jsonschema/PyYAML/bs4/lxml -- see
requirements.txt), so this file does not introduce one either: it parses the
JS object-literal syntax itself, with a small purpose-built recursive-descent
parser scoped exactly to the restricted grammar these four literals actually
use (nested objects/arrays, double-quoted strings, unquoted identifier keys,
true/false/null, integers, trailing commas, `//` line comments). This is not
a general JS parser and does not need to be one -- it only has to parse
literal object/array/string/boolean/number syntax, which is exactly what
these four `const NAME = { ... };` declarations are.

Structural ("field-for-field") parity is asserted on the JSON-Schema-
normative keys only: `type`, `properties` (key set + per-property `type`/
`const`/`items`), `required` (as a set -- order is not semantically
meaningful), `additionalProperties`, and `oneOf` branch shapes. `$schema`,
`$id`, `title`, and `description` are annotation-only (they do not affect
schema validation behavior) and are DELIBERATELY excluded from the parity
comparison -- confirmed by inspection that the two representations' prose
differs in places (e.g. REVIEW_SCHEMA's `coverage_ok` description text is
not byte-identical between the JS literal and review.schema.json), which is
not itself a drift bug under this file's definition of "matches exactly":
the structural contract (what shape of object is valid) is what the four
schema-validated `agent()` calls in the pipeline actually enforce, not the
prose.

Named assertions from the build inventory's "Exact schema literals"
subsection are locked explicitly, on both representations:
  - REVIEW_SCHEMA has NO `verse_status` field (verse issues report as
    ordinary `findings[]` entries with `loc: "VERSE:{vid}"`; verse COVERAGE
    is validate_draft.py's job, never review judgment).
  - REVIEW_SCHEMA REQUIRES `draft_sha1` (a deliberate plugin addition over
    the real reference schema).
  - Every one of the four schemas (and every oneOf branch, where
    applicable) sets `additionalProperties: false`.
  - The two `oneOf`-shaped confirmation schemas' SUCCESS/FAILURE branches
    are asymmetric by design: a FAILURE branch never claims a field
    (`fragment_path`/`fragment_sha1`/`ledger_path`/`n_segments`/
    `stale_segments`) that was never written.

A mutation ("regression-catcher") test proves the comparison helper itself
is not vacuously true -- it deliberately breaks a couple of the invariants
above on a copy of the canonical schema and asserts the parity helper
raises, before trusting a clean pass on the real, unmutated files above.

Collection note: like every ``*.test.py`` file in this suite, pytest's
default "prepend" import mode cannot resolve the dotted module name (e.g.
``No module named 'review_prompt_schema_drift'``) -- this repo's
``pytest.ini`` already sets ``--import-mode=importlib`` globally, so a plain
``python3 -m pytest tests/review_prompt_schema_drift.test.py`` from the
plugin root works without any extra flags.
"""
import json
import re
from pathlib import Path
from typing import Union, cast

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "templates" / "mass-translate-wf.template.js"
)
SCHEMAS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"

CONST_TO_SCHEMA_FILE = {
    "REVIEW_SCHEMA": "review.schema.json",
    "REVIEW_ARTIFACT_SCHEMA": "review-artifact-check.schema.json",
    "LEDGER_WRITE_SCHEMA": "ledger-write-confirmation.schema.json",
    "LEDGER_MERGE_SCHEMA": "ledger-merge-confirmation.schema.json",
}

# Keys that are annotation-only (never consulted by JSON Schema validation
# itself) -- excluded from structural parity comparisons. See module
# docstring for why this exclusion is deliberate, not an oversight.
_ANNOTATION_ONLY_KEYS = {"$schema", "$id", "title", "description"}


# ---------------------------------------------------------------------------
# A small, purpose-built JS object-literal parser. Scoped exactly to the
# restricted grammar the four schema literals use -- see module docstring.
# ---------------------------------------------------------------------------

class JSLiteralParseError(Exception):
    pass


_TOKEN_RE = re.compile(
    r"""
    (?P<ws>[ \t\r\n]+)
  | (?P<comment>//[^\n]*)
  | (?P<string>"(?:[^"\\]|\\.)*")
  | (?P<number>-?\d+(?:\.\d+)?)
  | (?P<ident>[A-Za-z_$][A-Za-z0-9_$]*)
  | (?P<punct>[{}\[\]:,])
""",
    re.VERBOSE,
)


def _tokenize(text: str) -> list[tuple[str, str]]:
    pos = 0
    n = len(text)
    tokens: list[tuple[str, str]] = []
    while pos < n:
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise JSLiteralParseError(f"unexpected character {text[pos]!r} at offset {pos}")
        pos = m.end()
        kind = m.lastgroup
        # _TOKEN_RE is a flat alternation (no nested groups) in which every
        # single branch is itself a named group (ws/comment/string/number/
        # ident/punct) and the pattern contains no unnamed groups at all. A
        # successful match therefore always has exactly one named group that
        # participated, so `lastgroup` cannot legitimately be None here --
        # assert it so a future edit to the regex that broke this invariant
        # (e.g. adding an unnamed/non-capturing top-level alternative) would
        # fail loudly instead of silently mis-tokenizing.
        assert kind is not None, f"_TOKEN_RE matched with no named group at offset {pos}: {m!r}"
        if kind in ("ws", "comment"):
            continue
        value = m.group(kind)
        # Same invariant as above: every alternative in _TOKEN_RE requires at
        # least one character (there is no `*`-quantified or empty branch),
        # so the group that actually matched always captured non-empty text
        # -- `m.group(kind)` for the *matched* group is never None.
        assert value is not None, f"named group {kind!r} matched but produced no text at offset {pos}"
        tokens.append((kind, value))
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.i = 0

    def _peek(self) -> tuple[str | None, str | None]:
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def _advance(self) -> tuple[str, str]:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def _expect(self, punct: str) -> None:
        _kind, val = self._advance()
        if val != punct:
            raise JSLiteralParseError(f"expected {punct!r}, got {val!r}")

    def parse_value(self):
        kind, val = self._peek()
        if val == "{":
            return self._parse_object()
        if val == "[":
            return self._parse_array()
        if kind == "string":
            # Re-consume via `_advance()` (typed `tuple[str, str]`) rather than
            # trusting the already-peeked `val` (typed `str | None` on `_peek`'s
            # signature, since `_peek` must also represent "no more tokens").
            # `kind == "string"` guarantees the token that peeked is exactly
            # the one `_advance()` now returns, so this is a real narrowing,
            # not a workaround.
            _, string_val = self._advance()
            return json.loads(string_val)
        if kind == "number":
            _, number_val = self._advance()
            return float(number_val) if "." in number_val else int(number_val)
        if kind == "ident":
            if val == "true":
                self._advance()
                return True
            if val == "false":
                self._advance()
                return False
            if val == "null":
                self._advance()
                return None
            raise JSLiteralParseError(f"unexpected bare identifier as a value: {val!r}")
        raise JSLiteralParseError(f"unexpected token (kind={kind!r}, value={val!r})")

    def _parse_object(self) -> dict:
        self._expect("{")
        obj: dict = {}
        kind, val = self._peek()
        if val == "}":
            self._advance()
            return obj
        while True:
            kkind, kval = self._advance()
            if kkind == "string":
                key = json.loads(kval)
            elif kkind == "ident":
                key = kval
            else:
                raise JSLiteralParseError(f"expected an object key, got {kval!r}")
            self._expect(":")
            obj[key] = self.parse_value()
            kind, val = self._peek()
            if val == ",":
                self._advance()
                kind, val = self._peek()
                if val == "}":
                    self._advance()
                    break
                continue
            if val == "}":
                self._advance()
                break
            raise JSLiteralParseError(f"expected ',' or '}}' after object entry, got {val!r}")
        return obj

    def _parse_array(self) -> list:
        self._expect("[")
        arr: list = []
        kind, val = self._peek()
        if val == "]":
            self._advance()
            return arr
        while True:
            arr.append(self.parse_value())
            kind, val = self._peek()
            if val == ",":
                self._advance()
                kind, val = self._peek()
                if val == "]":
                    self._advance()
                    break
                continue
            if val == "]":
                self._advance()
                break
            raise JSLiteralParseError(f"expected ',' or ']' after array entry, got {val!r}")
        return arr


def parse_js_object_literal(text: str):
    """Parses a single JS object/array literal expression (no trailing
    semicolon/statement) into the equivalent Python dict/list/str/bool/
    int/None structure."""
    tokens = _tokenize(text)
    parser = _Parser(tokens)
    value = parser.parse_value()
    if parser.i != len(tokens):
        remainder = tokens[parser.i]
        raise JSLiteralParseError(f"trailing tokens after the literal's closing brace: {remainder!r}")
    return value


def _find_balanced_brace_span(source: str, start: int) -> int:
    """``start`` must index the literal's opening ``{``. Returns the index
    just past its matching closing ``}``, respecting string literals and
    ``//`` line comments so a stray brace inside either doesn't miscount
    (none of the four schema literals actually contain one, but the
    `loc` finding's own description text --  "VERSE:{vid}" -- does contain a
    brace inside a string, which is exactly the case this guards)."""
    assert source[start] == "{", f"expected '{{' at offset {start}, found {source[start]!r}"
    depth = 0
    i = start
    n = len(source)
    while i < n:
        c = source[i]
        if c == '"':
            i += 1
            while i < n and source[i] != '"':
                if source[i] == "\\":
                    i += 1
                i += 1
            i += 1  # skip the closing quote
            continue
        if c == "/" and i + 1 < n and source[i + 1] == "/":
            newline = source.find("\n", i)
            i = newline if newline != -1 else n
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise JSLiteralParseError(f"unbalanced braces starting at offset {start}")


def extract_const_object_literal(source: str, const_name: str) -> str:
    """Locates ``const <const_name> = { ... }`` in ``source`` and returns the
    literal object-expression text (the ``{ ... }`` span only, not the
    ``const NAME =`` prefix or trailing ``;``)."""
    m = re.search(r"const\s+" + re.escape(const_name) + r"\s*=\s*", source)
    if not m:
        raise AssertionError(
            f"expected 'const {const_name} = {{ ... }};' declaration in "
            f"{TEMPLATE_PATH}, found none"
        )
    start = m.end()
    if start >= len(source) or source[start] != "{":
        raise AssertionError(
            f"const {const_name} in {TEMPLATE_PATH} is not assigned an "
            f"object literal (expected '{{' immediately after '=')"
        )
    end = _find_balanced_brace_span(source, start)
    return source[start:end]


# The true possible shapes flowing through `_canonicalize`: it is fed either
# a JS-literal-parsed value (dict/list/str/int/float/bool/None -- see
# `_Parser`/`parse_js_object_literal` above) or a `json.loads`-parsed value
# (the same set of shapes, by definition of JSON). Declaring this recursively
# lets pyright track the function's true return shape instead of widening it
# to Unknown across the recursive isinstance branches below.
_CanonicalValue = Union[
    dict[str, "_CanonicalValue"], list["_CanonicalValue"], str, int, float, bool, None
]


def _canonicalize(value: _CanonicalValue) -> _CanonicalValue:
    """Strips annotation-only keys recursively and normalizes `required`
    arrays to a sorted list (required-ness is a set, not an ordered
    sequence) so two independently-authored, structurally-equivalent
    schemas compare equal regardless of key/require ordering."""
    if isinstance(value, dict):
        result: dict[str, _CanonicalValue] = {}
        for key, val in value.items():
            if key in _ANNOTATION_ONLY_KEYS:
                continue
            canon_val = _canonicalize(val)
            if key == "required" and isinstance(canon_val, list):
                # JSON Schema's `required` array is defined by the JSON
                # Schema spec to contain only property-name strings -- never
                # nested objects/arrays/numbers/bools. Assert that real
                # invariant (rather than silently casting) so a schema that
                # ever violated it would fail loudly here instead of
                # `sorted()` either mis-ordering or raising a confusing
                # TypeError deep inside comparison logic.
                for item in canon_val:
                    assert isinstance(item, str), (
                        f"'required' array must contain only strings, got {item!r}"
                    )
                sorted_required = sorted(cast("list[str]", canon_val))
                # `list` is invariant, so `list[str]` (just proven above) is
                # not directly assignable back to the `list[_CanonicalValue]`
                # -typed `canon_val` even though every `str` is a valid
                # `_CanonicalValue` member -- this second cast just restates
                # that already-proven fact for the assignment below.
                canon_val = cast("list[_CanonicalValue]", sorted_required)
            result[key] = canon_val
        return result
    if isinstance(value, list):
        return [_canonicalize(v) for v in value]
    return value


def assert_schema_structural_parity(js_literal, canonical_schema, *, label: str) -> None:
    """Asserts the JS literal and the canonical `.schema.json` file describe
    the identical structural (validation-affecting) contract -- see module
    docstring for exactly which keys are excluded from this comparison, and
    why."""
    js_canon = _canonicalize(js_literal)
    json_canon = _canonicalize(canonical_schema)
    assert js_canon == json_canon, (
        f"{label}: structural drift between the JS inline schema literal "
        f"and the canonical JSON Schema file (descriptions/titles/$schema/"
        f"$id are excluded from this comparison -- only type/required/"
        f"additionalProperties/properties/oneOf/const/items structure is "
        f"compared):\n"
        f"  JS literal   (canonicalized): {json.dumps(js_canon, indent=2, sort_keys=True)}\n"
        f"  canonical schema (canonicalized): {json.dumps(json_canon, indent=2, sort_keys=True)}"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def js_source() -> str:
    assert TEMPLATE_PATH.is_file(), f"expected {TEMPLATE_PATH}"
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js_schemas(js_source: str) -> dict:
    return {
        const_name: parse_js_object_literal(extract_const_object_literal(js_source, const_name))
        for const_name in CONST_TO_SCHEMA_FILE
    }


@pytest.fixture(scope="module")
def canonical_schemas() -> dict:
    result = {}
    for const_name, filename in CONST_TO_SCHEMA_FILE.items():
        path = SCHEMAS_DIR / filename
        assert path.is_file(), f"expected canonical schema at {path}"
        result[const_name] = json.loads(path.read_text(encoding="utf-8"))
    return result


# ---------------------------------------------------------------------------
# Baseline sanity: the fixture construction itself (extraction + parsing)
# actually produced all four schemas, each a real dict -- proves the harness
# is innocent before any parity assertion is trusted below.
# ---------------------------------------------------------------------------

def test_all_four_schema_literals_extracted_and_parsed(js_schemas):
    assert set(js_schemas.keys()) == set(CONST_TO_SCHEMA_FILE.keys())
    for const_name, parsed in js_schemas.items():
        assert isinstance(parsed, dict), f"{const_name} did not parse to an object literal"
        assert parsed, f"{const_name} parsed to an empty object -- extraction likely mis-spanned"


def test_all_four_canonical_schema_files_exist_and_parse(canonical_schemas):
    assert set(canonical_schemas.keys()) == set(CONST_TO_SCHEMA_FILE.keys())
    for const_name, schema in canonical_schemas.items():
        assert isinstance(schema, dict) and schema, (
            f"canonical schema for {const_name} ({CONST_TO_SCHEMA_FILE[const_name]}) "
            f"did not load to a non-empty object"
        )


# ---------------------------------------------------------------------------
# The core drift-detection assertion: structural parity, one pair at a time.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("const_name", sorted(CONST_TO_SCHEMA_FILE))
def test_js_schema_literal_matches_canonical_schema_file(js_schemas, canonical_schemas, const_name):
    assert_schema_structural_parity(
        js_schemas[const_name],
        canonical_schemas[const_name],
        label=f"{const_name} vs {CONST_TO_SCHEMA_FILE[const_name]}",
    )


# ---------------------------------------------------------------------------
# Named assertions from the build inventory's "Exact schema literals"
# subsection -- locked on BOTH representations, so drift in either direction
# is caught.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", ["js_schemas", "canonical_schemas"])
def test_review_schema_has_no_verse_status_field(request, source):
    schemas = request.getfixturevalue(source)
    props = schemas["REVIEW_SCHEMA"]["properties"]
    assert "verse_status" not in props, (
        f"REVIEW_SCHEMA ({source}) must never grow a verse_status field -- "
        f"verse-specific issues report as ordinary findings[] entries "
        f"(loc: 'VERSE:{{vid}}'); verse COVERAGE is exclusively "
        f"validate_draft.py's job, never review judgment."
    )


@pytest.mark.parametrize("source", ["js_schemas", "canonical_schemas"])
def test_review_schema_requires_draft_sha1(request, source):
    schemas = request.getfixturevalue(source)
    schema = schemas["REVIEW_SCHEMA"]
    assert "draft_sha1" in schema["required"], (
        f"REVIEW_SCHEMA ({source}) must REQUIRE draft_sha1 -- a deliberate "
        f"plugin addition over the real reference schema, closing a "
        f"staleness-detection gap at convergence time."
    )
    assert schema["properties"]["draft_sha1"]["type"] == "string"


@pytest.mark.parametrize("source", ["js_schemas", "canonical_schemas"])
def test_review_schema_forbids_additional_properties(request, source):
    schemas = request.getfixturevalue(source)
    schema = schemas["REVIEW_SCHEMA"]
    assert schema["additionalProperties"] is False
    findings_items = schema["properties"]["findings"]["items"]
    assert findings_items["additionalProperties"] is False
    assert sorted(findings_items["required"]) == sorted(["loc", "severity", "issue", "suggest"])


@pytest.mark.parametrize("source", ["js_schemas", "canonical_schemas"])
def test_review_artifact_schema_both_branches_forbid_additional_properties(request, source):
    schemas = request.getfixturevalue(source)
    branches = schemas["REVIEW_ARTIFACT_SCHEMA"]["oneOf"]
    assert len(branches) == 2
    for branch in branches:
        assert branch["additionalProperties"] is False


@pytest.mark.parametrize("source", ["js_schemas", "canonical_schemas"])
def test_ledger_write_schema_failure_branch_omits_fragment_fields(request, source):
    """The two branches are deliberately asymmetric: a failure never claims
    a fragment_path/fragment_sha1 that was never written."""
    schemas = request.getfixturevalue(source)
    branches = schemas["LEDGER_WRITE_SCHEMA"]["oneOf"]
    failure = next(b for b in branches if b["properties"]["success"]["const"] is False)
    success = next(b for b in branches if b["properties"]["success"]["const"] is True)

    assert set(failure["required"]) == {"success", "error"}
    assert "fragment_path" not in failure["properties"]
    assert "fragment_sha1" not in failure["properties"]
    assert failure["additionalProperties"] is False

    assert set(success["required"]) == {"success", "status", "fragment_path", "fragment_sha1"}
    assert success["additionalProperties"] is False


@pytest.mark.parametrize("source", ["js_schemas", "canonical_schemas"])
def test_ledger_merge_schema_success_branch_requires_empty_missing_segments(request, source):
    """SUCCESS's missing_segments must be a strictly EMPTY array -- a
    completeness/subset check that already succeeded has nothing missing
    by definition; FAILURE's own missing_segments (below) is unrestricted."""
    schemas = request.getfixturevalue(source)
    branches = schemas["LEDGER_MERGE_SCHEMA"]["oneOf"]
    success = next(b for b in branches if b["properties"]["success"]["const"] is True)
    assert success["properties"]["missing_segments"]["maxItems"] == 0
    assert set(success["required"]) == {
        "success", "ledger_path", "n_segments", "missing_segments", "stale_segments",
    }
    assert success["additionalProperties"] is False


@pytest.mark.parametrize("source", ["js_schemas", "canonical_schemas"])
def test_ledger_merge_schema_failure_branch_omits_ledger_fields(request, source):
    schemas = request.getfixturevalue(source)
    branches = schemas["LEDGER_MERGE_SCHEMA"]["oneOf"]
    failure = next(b for b in branches if b["properties"]["success"]["const"] is False)
    assert set(failure["required"]) == {"success", "error"}
    assert "ledger_path" not in failure["properties"]
    assert "n_segments" not in failure["properties"]
    assert "stale_segments" not in failure["properties"]
    # failure's own optional missing_segments (unlike success's) carries no
    # maxItems:0 restriction -- a failed merge may legitimately report which
    # segments are missing.
    if "missing_segments" in failure["properties"]:
        assert "maxItems" not in failure["properties"]["missing_segments"]
    assert failure["additionalProperties"] is False


# ---------------------------------------------------------------------------
# TDZ gotcha regression lock: every schema const must be declared textually
# BEFORE the real pipeline() invocation -- NOT merely before the first
# mention of the substring "pipeline(" anywhere in the file, since the
# file's own header comments mention "pipeline()" (in prose) well before any
# of the four consts are declared.
# ---------------------------------------------------------------------------

def test_schema_literals_declared_above_the_real_pipeline_call(js_source):
    call_match = re.search(r"\bawait\s+pipeline\s*\(", js_source)
    assert call_match, f"expected an 'await pipeline(...)' call in {TEMPLATE_PATH}"
    pipeline_call_idx = call_match.start()

    for const_name in CONST_TO_SCHEMA_FILE:
        const_match = re.search(r"\bconst\s+" + re.escape(const_name) + r"\s*=", js_source)
        assert const_match, f"expected 'const {const_name} =' in {TEMPLATE_PATH}"
        assert const_match.start() < pipeline_call_idx, (
            f"const {const_name} is declared AFTER the real 'await pipeline(...)' "
            f"call -- this silently no-ops under this execution model's "
            f"temporal-dead-zone semantics (references/gotchas.md item 10); it "
            f"must be declared above every use, including this call"
        )


# ---------------------------------------------------------------------------
# Regression-catcher: the parity helper itself must not be vacuously true.
# Each case below injects one specific, named divergence into a COPY of a
# real canonical schema and asserts the helper actually raises against the
# real (unmutated) JS literal -- proving the comparison in
# test_js_schema_literal_matches_canonical_schema_file above would genuinely
# catch that same divergence if it ever crept into either source file.
# ---------------------------------------------------------------------------

def test_parity_helper_catches_added_verse_status_field(js_schemas, canonical_schemas):
    mutated = json.loads(json.dumps(canonical_schemas["REVIEW_SCHEMA"]))
    mutated["properties"]["verse_status"] = {"type": "string"}
    mutated["required"].append("verse_status")
    with pytest.raises(AssertionError):
        assert_schema_structural_parity(
            js_schemas["REVIEW_SCHEMA"], mutated, label="mutated REVIEW_SCHEMA (added verse_status)"
        )


def test_parity_helper_catches_dropped_draft_sha1_requirement(js_schemas, canonical_schemas):
    mutated = json.loads(json.dumps(canonical_schemas["REVIEW_SCHEMA"]))
    mutated["required"].remove("draft_sha1")
    with pytest.raises(AssertionError):
        assert_schema_structural_parity(
            js_schemas["REVIEW_SCHEMA"], mutated, label="mutated REVIEW_SCHEMA (dropped draft_sha1 requirement)"
        )


def test_parity_helper_catches_relaxed_additional_properties(js_schemas, canonical_schemas):
    mutated = json.loads(json.dumps(canonical_schemas["LEDGER_WRITE_SCHEMA"]))
    mutated["oneOf"][0]["additionalProperties"] = True
    with pytest.raises(AssertionError):
        assert_schema_structural_parity(
            js_schemas["LEDGER_WRITE_SCHEMA"], mutated,
            label="mutated LEDGER_WRITE_SCHEMA (relaxed additionalProperties)",
        )


def test_parity_helper_ignores_description_only_divergence(js_schemas, canonical_schemas):
    """Sanity companion to the three catches above: a PURE prose edit (no
    structural change) must NOT be flagged -- otherwise the helper would be
    too strict to ever pass against these two independently-worded, but
    structurally-identical, real files."""
    mutated = json.loads(json.dumps(canonical_schemas["REVIEW_SCHEMA"]))
    mutated["properties"]["clean"]["description"] = "Completely different prose, on purpose."
    mutated["description"] = "Also completely different top-level prose."
    assert_schema_structural_parity(
        js_schemas["REVIEW_SCHEMA"], mutated, label="mutated REVIEW_SCHEMA (description-only edit)"
    )


# ---------------------------------------------------------------------------
# Parser/extractor edge cases -- the harness itself must fail legibly, not
# silently mis-extract, if a const is ever renamed or its literal removed.
# ---------------------------------------------------------------------------

def test_extract_const_object_literal_raises_on_missing_const(js_source):
    with pytest.raises(AssertionError, match="NOT_A_REAL_SCHEMA_CONST"):
        extract_const_object_literal(js_source, "NOT_A_REAL_SCHEMA_CONST")


def test_parse_js_object_literal_handles_brace_inside_string_value():
    """Regression lock for _find_balanced_brace_span's string-awareness --
    REVIEW_SCHEMA's own `loc` finding description contains a literal '{' in
    'VERSE:{vid}' inside a string value; a brace-counter that isn't
    string-aware would stop early and truncate the extracted literal."""
    text = '{ hint: "example locator: VERSE:{vid} inside a string" }'
    parsed = parse_js_object_literal(text)
    assert parsed == {"hint": "example locator: VERSE:{vid} inside a string"}


def test_parse_js_object_literal_handles_trailing_commas():
    text = '{ a: 1, b: [1, 2, 3,], }'
    assert parse_js_object_literal(text) == {"a": 1, "b": [1, 2, 3]}
