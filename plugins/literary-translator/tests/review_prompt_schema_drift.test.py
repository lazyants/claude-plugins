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

1.2.0 CHANGE (CONTRACT-1.2.0-reliability.md sections 1-2, #87 fix): three of
the four literals are no longer 1:1 with their canonical `.schema.json`
file at all. `REVIEW_ARTIFACT_SCHEMA`/`LEDGER_WRITE_SCHEMA`/
`LEDGER_MERGE_SCHEMA` are now FLAT (`type:"object"`, no top-level `oneOf`
-- a discriminated `oneOf` cannot be a tool `input_schema`, the #87 bug this
build fixes), while their canonical `.schema.json` files stay STRONG
`oneOf`s (Owner C deliberately keeps these unchanged -- they still validate
each script's own stdout at runtime). Strict structural PARITY between a
flat literal and a `oneOf` schema is therefore no longer a meaningful
comparison for these three -- see `tests/agent_schema_top_level_object
.test.py` (locks every flat literal's top-level-object/no-combinator shape)
and `tests/ledger_confirmation_schema.test.py` (locks that the flat ledger
literals ACCEPT what the real scripts actually emit, plus the on-disk
strong schemas' own reject-side behavior) for where that coverage now
lives; THIS file no longer re-asserts it for the two ledger schemas.
`REVIEW_ARTIFACT_SCHEMA`'s own "accepts real review_artifact_check.py
output" containment check stays in THIS file (no dedicated sibling file
owns it) -- see its own test below.

`REVIEW_SCHEMA` is UNCHANGED (still the flat 4-field verdict shape:
clean/coverage_ok/findings/draft_sha1) but its canonical file,
`review.schema.json`, gained a 5th, OPTIONAL `dispatch_token` field (1.2.0
resume-integrity metadata -- see that field's own schema description).
`REVIEW_SCHEMA` is therefore now an intentional 4-field PROJECTION of the
5-field canonical schema, not full equality -- this file's parity
assertion for `REVIEW_SCHEMA` checks exactly that (property/required-set
containment with a NAMED single permitted extra field, plus full
structural parity on every field the two DO share), not byte-equal
property sets.

Named assertions from the build inventory's "Exact schema literals"
subsection are locked explicitly, on both representations:
  - REVIEW_SCHEMA has NO `verse_status` field (verse issues report as
    ordinary `findings[]` entries with `loc: "VERSE:{vid}"`; verse COVERAGE
    is validate_draft.py's job, never review judgment).
  - REVIEW_SCHEMA REQUIRES `draft_sha1` (a deliberate plugin addition over
    the real reference schema).
  - Every one of the four schemas sets `additionalProperties: false`
    (REVIEW_ARTIFACT/LEDGER_WRITE/LEDGER_MERGE at their own flat top level;
    the two on-disk `oneOf`-shaped confirmation schemas retain their own
    per-branch `additionalProperties:false` + SUCCESS/FAILURE asymmetry --
    see `ledger_confirmation_schema.test.py`'s reject-side tests, which now
    own that specific lock).

A mutation ("regression-catcher") test proves the comparison helper itself
is not vacuously true -- it deliberately breaks a couple of the invariants
above on a copy of the canonical schema and asserts the parity/projection
helper raises, before trusting a clean pass on the real, unmutated files
above.

Collection note: like every ``*.test.py`` file in this suite, pytest's
default "prepend" import mode cannot resolve the dotted module name (e.g.
``No module named 'review_prompt_schema_drift'``) -- this repo's
``pytest.ini`` already sets ``--import-mode=importlib`` globally, so a plain
``python3 -m pytest tests/review_prompt_schema_drift.test.py`` from the
plugin root works without any extra flags.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Union, cast

import jsonschema
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
    """Asserts two already-canonicalizable schema shapes describe the
    identical structural (validation-affecting) contract -- see module
    docstring for exactly which keys are excluded from this comparison, and
    why. Not called for cross-representation parity within THIS file
    anymore (post-1.2.0, only REVIEW_SCHEMA vs review.schema.json is still
    a meaningful parity-shaped comparison, and that one is now a documented
    PROJECTION -- see assert_schema_is_projection below); kept as a public
    helper because tests/agent_schema_top_level_object.test.py imports it
    (via importlib, house style: reuse this file's parser/compare helpers
    rather than vendoring a second copy) to lock each flattened literal's
    exact pinned shape against a hand-written expected-shape literal, which
    IS a legitimate byte-equal comparison (two Python dicts, not a JS
    literal vs. a oneOf schema)."""
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


def assert_schema_is_projection(js_literal, canonical_schema, *, extra_fields: set, label: str) -> None:
    """1.2.0: REVIEW_SCHEMA is a deliberate PROJECTION of the (now larger)
    canonical review.schema.json, not a byte-equal parity pair -- see module
    docstring. Asserts: both are top-level `object`/`additionalProperties:
    false`; the canonical schema's property set is EXACTLY the JS literal's
    own property set PLUS `extra_fields` (named precisely, not just "a
    superset") -- catching both an unwanted extra field AND a silently
    dropped/renamed `extra_fields` member; `required` sets are identical
    (the extra field(s) must stay optional-only, per CONTRACT-1.2.0-
    reliability.md's own field description); and every property the two DO
    share is fully structurally identical (recursing through
    `_canonicalize`, so a drift in a SHARED field's own shape -- e.g. a
    relaxed `additionalProperties` inside `findings[]` -- is still caught,
    exactly as strict parity would have caught it)."""
    js_canon = _canonicalize(js_literal)
    canon_canon = _canonicalize(canonical_schema)

    assert js_canon["type"] == canon_canon["type"] == "object", (
        f"{label}: both must be top-level 'object'"
    )
    assert js_canon["additionalProperties"] is False and canon_canon["additionalProperties"] is False, (
        f"{label}: both must set additionalProperties:false"
    )

    js_props = set(js_canon["properties"].keys())
    canon_props = set(canon_canon["properties"].keys())
    actual_extra = canon_props - js_props
    assert actual_extra == extra_fields, (
        f"{label}: canonical schema's property set diverges from the JS "
        f"literal's own by more/fewer than the documented projection gap "
        f"-- expected exactly {sorted(extra_fields)} extra field(s) on the "
        f"canonical side, got {sorted(actual_extra)} (canonical-only: "
        f"{sorted(canon_props - js_props)}, JS-only: {sorted(js_props - canon_props)})"
    )

    js_required = set(js_canon.get("required", []))
    canon_required = set(canon_canon.get("required", []))
    assert js_required == canon_required, (
        f"{label}: 'required' sets must be identical (the projection's "
        f"extra field(s) {sorted(extra_fields)} must stay OPTIONAL-only) "
        f"-- JS required={sorted(js_required)}, canonical required={sorted(canon_required)}"
    )

    for shared_prop in sorted(js_props & canon_props):
        js_sub = js_canon["properties"][shared_prop]
        canon_sub = canon_canon["properties"][shared_prop]
        assert js_sub == canon_sub, (
            f"{label}: shared property {shared_prop!r} has structurally "
            f"drifted between the JS literal and the canonical schema:\n"
            f"  JS: {json.dumps(js_sub, indent=2, sort_keys=True)}\n"
            f"  canonical: {json.dumps(canon_sub, indent=2, sort_keys=True)}"
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
# The core drift-detection assertion. 1.2.0: only REVIEW_SCHEMA is still
# compared against its canonical file here (as a documented PROJECTION, not
# byte-equal parity -- see module docstring). REVIEW_ARTIFACT_SCHEMA/
# LEDGER_WRITE_SCHEMA/LEDGER_MERGE_SCHEMA are flat vs. their canonical
# files' own strong `oneOf` -- no longer a meaningful parity comparison;
# their own coverage lives in agent_schema_top_level_object.test.py (shape)
# and ledger_confirmation_schema.test.py (ledger ones' real-output
# acceptance) -- REVIEW_ARTIFACT_SCHEMA's own acceptance test is the one
# still homed in THIS file, immediately below.
# ---------------------------------------------------------------------------

def test_review_schema_is_a_projection_of_canonical_review_schema(js_schemas, canonical_schemas):
    assert_schema_is_projection(
        js_schemas["REVIEW_SCHEMA"],
        canonical_schemas["REVIEW_SCHEMA"],
        extra_fields={"dispatch_token"},
        label="REVIEW_SCHEMA vs review.schema.json",
    )


# ---------------------------------------------------------------------------
# REVIEW_ARTIFACT_SCHEMA's own "decoupled acceptance" check (CONTRACT
# section 1's "DECOUPLE the flat ledger literals from strict on-disk
# parity" instruction, applied here to REVIEW_ARTIFACT_SCHEMA -- the one
# flattened schema with no dedicated sibling test file of its own): drives
# the REAL review_artifact_check.py script via subprocess for both its
# MATCH and MISMATCH outcomes, and asserts the flat JS literal ACCEPTS
# both real emitted shapes (never that the two representations are
# byte-identical -- they can't be; one is flat, the other a strong oneOf).
# ---------------------------------------------------------------------------

REVIEW_ARTIFACT_CHECK_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills" / "literary-translator" / "assets" / "scripts" / "review_artifact_check.py"
)


def _make_review_artifact_check_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    (root / "segments").mkdir()
    assert REVIEW_ARTIFACT_CHECK_SCRIPT.is_file(), f"expected {REVIEW_ARTIFACT_CHECK_SCRIPT}"
    shutil.copy2(REVIEW_ARTIFACT_CHECK_SCRIPT, scripts_dir / "review_artifact_check.py")
    return root


def _run_review_artifact_check(root, seg, expected_file):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "review_artifact_check.py"), seg, "--expected-file", str(expected_file)],
        capture_output=True, text=True, timeout=30,
    )


def test_review_artifact_schema_flat_literal_accepts_real_review_artifact_check_output(
    tmp_path, js_schemas, canonical_schemas,
):
    root = _make_review_artifact_check_root(tmp_path)
    flat_schema = js_schemas["REVIEW_ARTIFACT_SCHEMA"]
    strong_schema = canonical_schemas["REVIEW_ARTIFACT_SCHEMA"]

    # -- MATCH: on-disk review artifact == expected-file content exactly.
    seg_match = "segMatch"
    rev_obj = {"clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "a" * 40}
    (root / "segments" / f"{seg_match}.review.json").write_text(json.dumps(rev_obj), encoding="utf-8")
    expected_file = tmp_path / "expected_match.json"
    expected_file.write_text(json.dumps(rev_obj), encoding="utf-8")
    result = _run_review_artifact_check(root, seg_match, expected_file)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    match_payload = json.loads(result.stdout.strip())
    assert match_payload == {"match": True}
    jsonschema.validate(instance=match_payload, schema=strong_schema)
    jsonschema.validate(instance=match_payload, schema=flat_schema)

    # -- MISMATCH: on-disk review artifact diverges from expected-file.
    seg_mismatch = "segMismatch"
    (root / "segments" / f"{seg_mismatch}.review.json").write_text(
        json.dumps({"clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "b" * 40}),
        encoding="utf-8",
    )
    expected_file2 = tmp_path / "expected_mismatch.json"
    expected_file2.write_text(
        json.dumps({"clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "c" * 40}),
        encoding="utf-8",
    )
    result2 = _run_review_artifact_check(root, seg_mismatch, expected_file2)
    assert result2.returncode == 0, f"{result2.stdout}\n{result2.stderr}"
    mismatch_payload = json.loads(result2.stdout.strip())
    assert mismatch_payload["match"] is False and "mismatch_detail" in mismatch_payload
    jsonschema.validate(instance=mismatch_payload, schema=strong_schema)
    jsonschema.validate(instance=mismatch_payload, schema=flat_schema)


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


# 1.3.6 (#135): the findings[] description previously claimed "Empty when
# clean is true" -- false under the softened low-only clean bar (clean is
# judged solely on whether any finding requires a fix round; residual
# low/cosmetic findings may remain even when clean is true, per
# review.schema.json's own `clean` description / the template's matching
# REVIEW_SCHEMA `clean` description above). Description is an annotation-only
# key (excluded from the structural parity/projection comparisons above), so
# it needs its own explicit lock here on BOTH representations -- a
# description-only edit is otherwise invisible to every other assertion in
# this file.
@pytest.mark.parametrize("source", ["js_schemas", "canonical_schemas"])
def test_findings_description_no_longer_claims_empty_when_clean(request, source):
    schemas = request.getfixturevalue(source)
    description = schemas["REVIEW_SCHEMA"]["properties"]["findings"]["description"]
    assert "Empty when clean is true" not in description, (
        f"REVIEW_SCHEMA findings description ({source}) still claims the stale "
        f"'Empty when clean is true' invariant (#135)"
    )
    assert "non-empty" in description and "clean" in description, (
        f"REVIEW_SCHEMA findings description ({source}) should state findings may "
        f"remain non-empty even when clean is true"
    )


# 1.2.0 (CONTRACT section 1, #87 fix): the four named oneOf-branch-indexing
# tests that used to live here (REVIEW_ARTIFACT_SCHEMA's both-branches
# additionalProperties check; LEDGER_WRITE_SCHEMA's/LEDGER_MERGE_SCHEMA's
# SUCCESS/FAILURE asymmetry checks) are DELETED, not merely reparametrized
# -- js_schemas["REVIEW_ARTIFACT_SCHEMA"]/["LEDGER_WRITE_SCHEMA"]/
# ["LEDGER_MERGE_SCHEMA"] no longer HAVE a top-level "oneOf" key at all
# post-flatten (that IS the #87 fix), so indexing schemas[...]["oneOf"] on
# the js_schemas side would KeyError. The on-disk canonical_schemas side of
# these same four checks is still real, valid coverage of the STRONG
# schemas' own SUCCESS/FAILURE branch asymmetry -- but it's no longer this
# JS-literal-drift file's concern; ledger_confirmation_schema.test.py's own
# REJECT-SIDE section (crossover-success-with-error /
# success-missing-fragment-fields / success-with-nonempty-missing-segments
# / success-plus-unknown-field / failure-missing-error, for BOTH
# ledger-write-confirmation.schema.json and
# ledger-merge-confirmation.schema.json) now owns that exact lock.
# REVIEW_ARTIFACT_SCHEMA's own on-disk oneOf branch shape is exercised
# indirectly by test_review_artifact_schema_flat_literal_accepts_real_
# review_artifact_check_output above (both a MATCH and a MISMATCH real
# payload validate against review-artifact-check.schema.json's strong
# oneOf).


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

def test_projection_helper_catches_added_verse_status_field(js_schemas, canonical_schemas):
    """A THIRD, unnamed extra field (verse_status) beyond the one documented
    projection gap (dispatch_token) must be caught -- the extra-fields set
    check is exact, not merely "canonical is a superset"."""
    mutated = json.loads(json.dumps(canonical_schemas["REVIEW_SCHEMA"]))
    mutated["properties"]["verse_status"] = {"type": "string"}
    with pytest.raises(AssertionError):
        assert_schema_is_projection(
            js_schemas["REVIEW_SCHEMA"], mutated, extra_fields={"dispatch_token"},
            label="mutated REVIEW_SCHEMA (added verse_status)",
        )


def test_projection_helper_catches_dropped_draft_sha1_requirement(js_schemas, canonical_schemas):
    mutated = json.loads(json.dumps(canonical_schemas["REVIEW_SCHEMA"]))
    mutated["required"].remove("draft_sha1")
    with pytest.raises(AssertionError):
        assert_schema_is_projection(
            js_schemas["REVIEW_SCHEMA"], mutated, extra_fields={"dispatch_token"},
            label="mutated REVIEW_SCHEMA (dropped draft_sha1 requirement)",
        )


def test_projection_helper_catches_dispatch_token_promoted_to_required(js_schemas, canonical_schemas):
    """The projection's one permitted extra field (dispatch_token) must stay
    OPTIONAL-only -- promoting it to required on the canonical side (without
    REVIEW_SCHEMA following suit, which it structurally can't -- it doesn't
    even have the field) must be caught, not silently accepted as "still a
    valid projection"."""
    mutated = json.loads(json.dumps(canonical_schemas["REVIEW_SCHEMA"]))
    mutated["required"].append("dispatch_token")
    with pytest.raises(AssertionError):
        assert_schema_is_projection(
            js_schemas["REVIEW_SCHEMA"], mutated, extra_fields={"dispatch_token"},
            label="mutated REVIEW_SCHEMA (dispatch_token promoted to required)",
        )


def test_projection_helper_catches_relaxed_additional_properties_on_a_shared_field(js_schemas, canonical_schemas):
    """A structural drift on a SHARED field (findings[].additionalProperties,
    relaxed from false to true) must still be caught by the projection
    helper, exactly as strict parity would have caught it -- the projection
    relaxation applies ONLY to the documented extra top-level field(s),
    never to fields both representations actually share."""
    mutated = json.loads(json.dumps(canonical_schemas["REVIEW_SCHEMA"]))
    mutated["properties"]["findings"]["items"]["additionalProperties"] = True
    with pytest.raises(AssertionError):
        assert_schema_is_projection(
            js_schemas["REVIEW_SCHEMA"], mutated, extra_fields={"dispatch_token"},
            label="mutated REVIEW_SCHEMA (relaxed findings[].additionalProperties)",
        )


def test_projection_helper_ignores_description_only_divergence(js_schemas, canonical_schemas):
    """Sanity companion to the catches above: a PURE prose edit (no
    structural change) must NOT be flagged -- otherwise the helper would be
    too strict to ever pass against these two independently-worded, but
    structurally-compatible, real files."""
    mutated = json.loads(json.dumps(canonical_schemas["REVIEW_SCHEMA"]))
    mutated["properties"]["clean"]["description"] = "Completely different prose, on purpose."
    mutated["description"] = "Also completely different top-level prose."
    assert_schema_is_projection(
        js_schemas["REVIEW_SCHEMA"], mutated, extra_fields={"dispatch_token"},
        label="mutated REVIEW_SCHEMA (description-only edit)",
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
