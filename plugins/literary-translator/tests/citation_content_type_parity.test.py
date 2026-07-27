"""tests/citation_content_type_parity.test.py -- 1.16.1 (#347).

`glossary.citation_content_types` is validated in THREE places, by three
different engines:

  1. `assets/schemas/profile.schema.json` -- preflight, Python `re` via
     jsonschema;
  2. `assets/templates/glossary-pass-wf.template.js` -- workflow instantiation,
     ECMA-262 `RegExp`;
  3. `assets/scripts/fetch_citation.py` -- the runtime boundary, Python `re`.

Three copies of one rule is the shape that rots. The failure it rots INTO is
specific and quiet: a value that passes preflight and then dies at the runtime
gate produces a mid-run refusal for a profile the operator was told was valid,
and a value that passes preflight and the template but is admitted more widely
by the fetcher silently widens a security boundary. Neither shows up in a test
of any single copy -- each file's own tests pass against its own regex.

So this file tests the three copies AGAINST EACH OTHER over a shared table,
rather than testing any one of them against a hand-written expectation.

The trailing-newline row is not hypothetical padding. All three copies were
first written `^...$`, and Python's `$` matches before a trailing newline while
ECMA-262's does not -- so that single row is the one input on which the engines
genuinely disagreed, and it is why the schema carries `(?![\\s\\S])` and the
Python copy carries `\\Z`.
"""
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[1] / "skills/literary-translator/assets"
SCHEMA_PATH = ASSETS / "schemas/profile.schema.json"
TEMPLATE_PATH = ASSETS / "templates/glossary-pass-wf.template.js"
SCRIPT_PATH = ASSETS / "scripts/fetch_citation.py"


def _load_fetch_citation():
    spec = importlib.util.spec_from_file_location("fetch_citation_parity", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fc = _load_fetch_citation()


def schema_pattern() -> str:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    node = schema["properties"]["glossary"]["properties"]["citation_content_types"]
    return node["items"]["pattern"]


def template_pattern() -> str:
    """The literal regex the template guards with, lifted from its source.

    Read out of the file rather than duplicated here: a copy in this test would
    be a FOURTH place for the rule to live, which is the problem, not the fix.
    """
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    matches = re.findall(r"if \(!(/.+?/)\.test\(t\)\) \{", text)
    assert len(matches) == 1, f"expected exactly one content-type guard, found {len(matches)}"
    return matches[0]


# (value, is_admitted) -- the shared table all three engines are judged against.
CASES = [
    ("text/", True),
    ("text/plain", True),
    ("application/pdf", True),
    ("application/xhtml", True),
    ("application/vnd.openxmlformats+xml", True),
    ("x-custom/thing", True),
    ("", False),
    ("*/*", False),
    ("text/*", False),
    ("TEXT/HTML", False),
    ("text/html; charset=utf-8", False),
    ("text/ html", False),
    ("not-a-type", False),
    ("/leading-slash", False),
    ("text/html\nX-Injected: 1", False),
    ("text/html\n", False),      # the engine-disagreement row -- see the docstring
    (" text/html", False),
]


@pytest.mark.parametrize("value, admitted", CASES)
def test_the_runtime_boundary_agrees_with_the_table(value, admitted):
    assert bool(fc.CONTENT_TYPE_PREFIX_RE.match(value)) is admitted


@pytest.mark.parametrize("value, admitted", CASES)
def test_the_preflight_schema_agrees_with_the_table(value, admitted):
    assert bool(re.match(schema_pattern(), value)) is admitted


@pytest.mark.parametrize("value, admitted", CASES)
def test_the_workflow_template_guard_agrees_with_the_table(value, admitted):
    """Evaluated by a real ECMA-262 engine, not by translating the pattern into
    Python -- the whole point is that the two engines differ, so re-implementing
    the JS regex in `re` would share the blind spot it exists to catch."""
    node = subprocess.run(
        ["node", "-e",
         "const re = " + template_pattern() + ";"
         "let v = ''; process.stdin.on('data', d => v += d)"
         ".on('end', () => process.stdout.write(re.test(v) ? '1' : '0'))"],
        input=value, capture_output=True, text=True, check=True)
    assert (node.stdout == "1") is admitted


def test_the_three_copies_admit_exactly_the_same_set():
    """The property stated directly, so a future row added to CASES cannot be
    satisfied by three separately-wrong engines."""
    node_available = subprocess.run(["node", "--version"], capture_output=True).returncode == 0
    assert node_available, "node is required: the template guard must be judged by a real JS engine"
    for value, _ in CASES:
        py = bool(fc.CONTENT_TYPE_PREFIX_RE.match(value))
        sc = bool(re.match(schema_pattern(), value))
        assert py == sc, f"schema and runtime disagree on {value!r}: schema={sc} runtime={py}"


def test_the_shipped_default_passes_every_copy():
    """A default the gates would reject is the one regression that would make
    every existing project refuse at preflight."""
    for value in fc.ALLOWED_CONTENT_PREFIXES:
        assert fc.CONTENT_TYPE_PREFIX_RE.match(value), value
        assert re.match(schema_pattern(), value), value


def test_the_schema_maxitems_matches_the_scripts_cap():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    node = schema["properties"]["glossary"]["properties"]["citation_content_types"]
    assert node["maxItems"] == fc.MAX_CONTENT_TYPE_PREFIXES


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
