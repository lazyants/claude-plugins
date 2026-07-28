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


def template_validator_js() -> str:
    """The template's ENTIRE validator as runnable JS: the split/trim/filter
    pipeline AND the regex, lifted from the source together.

    Testing the regex alone was the round-3 gap -- the pipeline's `.trim()` is
    part of the rule, so a value the pattern rejects can still be admitted after
    normalization. Both halves are read out of the template so neither can drift
    from what actually ships.
    """
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    # The exact normalization the template applies before its guard.
    pipeline = re.search(
        r"const CITATION_TYPE_LIST = CITATION_CONTENT_TYPES\.split\(\",\"\)\s*"
        r"(\.map\(function \(t\) \{ return t\.trim\(\) \}\)\s*"
        r"\.filter\(function \(t\) \{ return t\.length > 0 \}\))", text)
    assert pipeline, "the template's normalization pipeline has changed shape; update this test"
    return (
        # argv[1], not argv[2]: `node -e SCRIPT -- VALUE` CONSUMES the `--`, so the
        # value is the first user arg. The `--` is kept so a value starting with
        # "-" is not parsed as a node flag.
        "const raw = process.argv[1];"
        "const CITATION_TYPE_LIST = raw.split(\",\")" + pipeline.group(1) + ";"
        "let ok = CITATION_TYPE_LIST.length > 0;"
        "for (const t of CITATION_TYPE_LIST) { if (!" + template_pattern() + ".test(t)) ok = false; }"
        "process.stdout.write(ok ? 'ADMIT' : 'REJECT');"
    )


# (value, is_admitted) -- the shared table all three engines are judged against.
CASES = [
    ("text/", True),
    ("text/plain", True),
    ("application/pdf", True),
    ("application/xhtml", True),
    ("application/vnd.openxmlformats+xml", True),
    ("x-custom/thing", True),
    # SHELL METACHARACTERS. The first charset for these three patterns was
    # derived from RFC 9110's `tchar`, which legitimately includes ! # $ & ^ --
    # and the template interpolates the value into a bash command line. The
    # security review reproduced it: "text/html&id" passed ALL THREE validators
    # and bash then ran `id`. The value is now single-quoted at the interpolation
    # (that is the boundary) AND the charset is narrowed to what a real media
    # type needs (this is the defence in depth). Both, because either alone
    # leaves the next charset change one edit from a live injection.
    ("text/html&id", False),
    ("text/html$USER", False),
    ("text/html#x", False),
    ("text/html^x", False),
    ("text/html_x", False),
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


# The ONE place the three engines legitimately differ, stated explicitly rather
# than smoothed over -- an undocumented divergence is the defect; a documented
# one is a design decision.
#
# The template validates a COMMA-SEPARATED STRING (the substitution token), while
# the schema and the fetcher validate the individual ARRAY ITEM. So the template
# must tolerate the separator spacing a human writes by hand -- "text/,
# application/pdf" -- and therefore `.trim()`s each field before matching. That
# makes it strictly more permissive about SURROUNDING WHITESPACE, and only that.
#
# This is safe in the direction that matters: the schema runs first, at preflight,
# on the profile value itself, and rejects a stray-whitespace entry there. The
# template's tolerance can only accept something the authoritative gate already
# approved. Whitespace also cannot survive into the shell argument -- the value is
# single-quoted at the interpolation and re-validated by the fetcher.
#
# Anything OTHER than leading/trailing whitespace must still agree everywhere, so
# this dict is deliberately tiny and every entry needs the argument above.
TEMPLATE_WHITESPACE_EXCEPTIONS = {
    " text/html": True,      # leading space -> trimmed, then matches
    "text/html\n": True,     # trailing newline -> trimmed, then matches
}


@pytest.mark.parametrize("value, admitted", CASES)
def test_the_workflow_template_guard_agrees_with_the_table(value, admitted):
    """Evaluated by a real ECMA-262 engine, not by translating the pattern into
    Python -- the whole point is that the two engines differ, so re-implementing
    the JS regex in `re` would share the blind spot it exists to catch.

    And it runs the template's WHOLE validator -- the split/trim/filter pipeline
    plus the regex -- not the regex literal alone. Codex found that gap in the
    1.16.1 round-3 review: the three bare patterns were equivalent while the
    enclosing validators were not, because the template `.trim()`s first and the
    other two engines do not, so `"text/html "` was normalized-and-accepted by
    one and rejected by two. A parity test that stops at the pattern cannot see
    a difference that lives in the code around it.
    """
    node = subprocess.run(
        ["node", "-e", template_validator_js(),
         "--", value],
        capture_output=True, text=True, check=True)
    assert node.stdout.strip() in ("ADMIT", "REJECT"), node.stdout + node.stderr
    expected = TEMPLATE_WHITESPACE_EXCEPTIONS.get(value, admitted)
    assert (node.stdout.strip() == "ADMIT") is expected, \
        f"template validator said {node.stdout.strip()} for {value!r}, expected {expected}"


def test_the_only_divergence_between_the_engines_is_surrounding_whitespace():
    """Pins the exception list SHUT. Without this, a future edit could add any
    row to TEMPLATE_WHITESPACE_EXCEPTIONS and quietly re-open a real divergence
    behind a name that says 'whitespace'."""
    for value in TEMPLATE_WHITESPACE_EXCEPTIONS:
        assert value != value.strip(), \
            f"{value!r} is in the whitespace-exception list but differs by more than whitespace"
        assert bool(fc.CONTENT_TYPE_PREFIX_RE.match(value.strip())), \
            f"{value!r} must match everywhere once trimmed; the exception is about whitespace only"


def test_node_is_available():
    """A guard on the guard. `test_the_workflow_template_guard_agrees_with_the_table`
    runs node with check=True, so a missing node surfaces as a raw
    FileNotFoundError rather than a readable message -- and a reader could then
    conclude the JS engine was covered when it was never invoked.

    This replaces an earlier `test_the_three_copies_admit_exactly_the_same_set`,
    which was deleted in review round 4 for committing the very defect this
    release exists to close: its docstring promised it prevented "three
    separately-wrong engines", and its body compared two (Python vs schema),
    probing node for --version and then never using it to judge anything. The
    three parametrized tests above already pin EACH engine against CASES
    individually, which is strictly stronger than pairwise agreement, so the
    deleted test was redundant as well as overclaiming.
    """
    assert subprocess.run(["node", "--version"], capture_output=True).returncode == 0, \
        "node is required: the template guard must be judged by a real JS engine"


def test_the_shipped_default_passes_both_PYTHON_copies():
    """A default the gates would reject is the one regression that would make
    every existing project refuse at preflight.

    Two copies, not three, and that is correct rather than a gap: with the
    profile key absent, `{{CITATION_CONTENT_TYPES}}` substitutes to "",
    `CITATION_TYPE_LIST` is empty, and the template's guard loop never iterates --
    so the JS copy never sees the shipped default at all.
    """
    for value in fc.ALLOWED_CONTENT_PREFIXES:
        assert fc.CONTENT_TYPE_PREFIX_RE.match(value), value
        assert re.match(schema_pattern(), value), value


def test_the_schema_maxitems_matches_the_scripts_cap():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    node = schema["properties"]["glossary"]["properties"]["citation_content_types"]
    assert node["maxItems"] == fc.MAX_CONTENT_TYPE_PREFIXES


def test_the_workflow_template_pins_the_same_count_cap_as_the_other_two_engines():
    """The COUNT cap is a three-way constant with, until round 6, a two-way test.

    fetch_citation.py's MAX_CONTENT_TYPE_PREFIXES and profile.schema.json's
    maxItems were pinned to each other; the template's own guard was a bare
    literal that nothing checked, while its error message asserts agreement with
    both. Bump the Python constant and the schema follows it here -- the template
    would have gone on refusing at the old number, which is a preflight gate
    disagreeing with the runtime gate, the exact thing the schema's own
    description calls "worse than no preflight gate".
    """
    src = TEMPLATE_PATH.read_text(encoding="utf-8")
    caps = [int(n) for n in re.findall(r"CITATION_TYPE_LIST\.length > (\d+)", src)]
    assert caps, "no count cap found in the workflow template"
    for cap in caps:
        assert cap == fc.MAX_CONTENT_TYPE_PREFIXES, (
            f"template caps the list at {cap}, fetch_citation.py at "
            f"{fc.MAX_CONTENT_TYPE_PREFIXES}")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
