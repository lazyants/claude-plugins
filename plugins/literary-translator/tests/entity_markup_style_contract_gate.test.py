"""tests/entity_markup_style_contract_gate.test.py -- gate for issue #913:

A project that declares ``output.entity_markup`` with a resolved
``index_from`` of ``markup`` must not be able to reach translation spend
while its ``style_bible.md`` style contract never tells the translator to
mark with the declared tag vocabulary. ``validate_extraction.py`` (the
MANDATORY W2 post-extraction gate) gained one new FATAL check,
``entity_markup_style_contract``, that closes this hole before the first LLM
dispatch of any kind.

This file is deliberately its own file, never folded into
``validate_extraction.test.py``: a fixture-guard test elsewhere in this repo
matches at FILE level, and mixing an unrelated inventory into one file can
turn it red for the wrong reason.

Every test here DRIVES THE REAL SCRIPT as a subprocess against real fixture
files on disk (``manifest.json``, ``extract.py``, ``profile.yml``, and --
where relevant -- ``style_bible.md``), never a hand-built stub and never a
re-implementation of the check's own regex. Every fixture sets
``source.format: custom`` in ``profile.yml`` -- this is the one documented
combination under which ``validate_extraction.py`` SKIPS the unrelated
extractor self-check-region pin (see its own module docstring and the
``NOTE selfcheck_region_pin: SKIPPED for source.format: custom`` branch in
``main()``) -- so the manifest/extract fixtures below can stay minimal
without needing to reproduce ``CURRENT_EXTRACTOR_SELFCHECK_HASH``. The
manifest fixture itself is otherwise schema-valid and derivable-checks-clean
under ``apparatus_policy: omit_apparatus`` (the same baseline shape
``validate_extraction.test.py``'s own ``_baseline_manifest()`` uses), so
every run below reaches the new check rather than failing earlier for an
unrelated reason.

Case list (see plan-913 / issue #913's Change B):
  1. a declared tag missing from the span -> exit 1, that tag named
  2. every declared tag present -> exit 0
  3. prefix collision: tags [person, person-title], contract documents only
     <person-title> -> exit 1 naming person
  3b. parameterized: the ONLY apparent evidence for `person` is
     <personTitle>, <person:name>, or <personé> -> exit 1 for each -- this is
     what distinguishes the shipped positive-lookahead matcher from the
     REJECTED `(?![a-z0-9_-])` negative-lookahead one, which passes case 3
     and still admits all three of these as false evidence. Without this
     case a regression to the rejected matcher stays green.
  4. a tag ending in `-` or `_` is matched correctly (the reason `\\b` is
     wrong: Python places no word boundary between a trailing `-`/`_` and
     the `>` that follows it)
  5. the tag appears in style_bible.md but OUTSIDE the markers -> exit 1
  6. index_from absent, and index_from: canon -> exit 0, inert, with no tag
     anywhere in the project (not even a style_bible.md on disk)
  7. no entity_markup block at all -> exit 0, inert, no style_bible.md needed
  8. parameterized tag-shape refusals: tags: person (bare string -- NOT a
     per-character refusal), tags: [], tags: [""], tags: [1] -> exit 1 each,
     naming output.entity_markup.tags, with no success line. tags: [] is the
     one that matters on its own: a list-only shape check would accept it,
     run zero comparisons, and print what a passing run prints.
  9. two missing tags -> BOTH named in one run, not just the first
  10. style_bible.md absent, and every malformed marker-pair state
     (duplicate begin, duplicate end, missing begin, missing end, out of
     order) -> exit 1, named
  11. a drift test: this module's two STYLE_CONTRACT marker constants must
     equal cache_key.py's own (compute_style_contract_hash), independently
     parsed from cache_key.py's source rather than imported -- cache_key.py
     is a PLUGIN_BUNDLE_MEMBERS entry, and importing from it would move
     plugin_bundle_hash and re-stale every converged segment in every
     project.
  12. a control: the verified-tag count printed in the success line must
     equal the number of declared tags, so a rig that runs zero comparisons
     cannot print what a passing one prints.
"""

import ast
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "validate_extraction.py"
CACHE_KEY_PATH = SCRIPTS_DIR / "cache_key.py"

assert SCRIPT_PATH.is_file(), f"expected {SCRIPT_PATH} to exist"
assert CACHE_KEY_PATH.is_file(), f"expected {CACHE_KEY_PATH} to exist"

CHECK_NAME = "entity_markup_style_contract"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Loaded purely for its two STYLE_CONTRACT marker constants -- used below to
# build realistic style_bible.md fixtures and, in the drift test, compared
# against an independent parse of cache_key.py's own literals. Every FUNCTIONAL
# assertion in this file still goes through the real script as a subprocess;
# this import never substitutes for that.
ve = _load_module("validate_extraction_under_test_entity_markup_gate", SCRIPT_PATH)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _baseline_manifest() -> dict:
    """A schema-valid, all-derivable-checks-pass manifest under
    apparatus_policy omit_apparatus -- the same baseline shape
    validate_extraction.test.py's own _baseline_manifest() builds. Kept as an
    independent copy rather than an import: this is its own test file by
    design (house rule -- a fixture-guard test elsewhere matches at file
    level), and importing a sibling *.test.py module is not this repo's
    fixture dialect."""
    def sha1(s: str) -> str:
        return hashlib.sha1(s.encode("utf-8")).hexdigest()

    return {
        "blocks": {
            "HEAD:seg01": {
                "id": "HEAD:seg01", "type": "HEAD", "order_index": 0,
                "source_file": "body.xhtml", "plain_text": "Chapter One",
                "sha1": sha1("Chapter One"),
            },
            "PARA:seg01:0001": {
                "id": "PARA:seg01:0001", "type": "PARA", "order_index": 1,
                "source_file": "body.xhtml", "plain_text": "Some body prose.",
                "sha1": sha1("Some body prose."),
            },
            "FRONTBACK:fm01": {
                "id": "FRONTBACK:fm01", "type": "FRONTBACK", "order_index": 2,
                "source_file": "front.xhtml", "plain_text": "Title page text",
                "sha1": sha1("Title page text"),
                "decision": "translate", "reason": "title-page text worth keeping",
            },
            "FRONTBACK:fm02": {
                "id": "FRONTBACK:fm02", "type": "FRONTBACK", "order_index": 3,
                "source_file": "front.xhtml", "plain_text": "Project Gutenberg boilerplate",
                "sha1": sha1("Project Gutenberg boilerplate"),
                "decision": "omit", "reason": "Project Gutenberg boilerplate header",
            },
        },
        "spine": [
            {"pos": 0, "file": "body.xhtml", "klass": "body"},
            {"pos": 1, "file": "front.xhtml", "klass": "front-back"},
        ],
        "segments": [
            {
                "seg": "seg01", "kind": "body",
                "block_ids": ["HEAD:seg01", "PARA:seg01:0001"],
                "word_count": 4, "n_para": 1, "n_verse": 0, "n_quote": 0,
                "source_files": ["body.xhtml"],
            },
            {
                "seg": "FRONTBACK:fm01", "kind": "frontback",
                "block_ids": ["FRONTBACK:fm01"], "word_count": 3,
                "source_files": ["front.xhtml"],
            },
        ],
        "footnotes": [],
        "frontback": [
            {"id": "FRONTBACK:fm01", "decision": "translate", "reason": "title-page text worth keeping"},
            {"id": "FRONTBACK:fm02", "decision": "omit", "reason": "Project Gutenberg boilerplate header"},
        ],
        "verse": {
            "store": [], "n_nodes": 0, "n_block": 0, "n_embedded": 0,
            "by_context": {"body": 0, "footnote": 0, "frontback": 0},
        },
        "source_inputs": ["book.epub"],
        "generation_hashes": {
            "source_extraction_hash": sha1("source_extraction_hash fixture"),
            "source_input_hash": sha1("source_input_hash fixture"),
        },
    }


def _write_manifest(tmp_path: Path) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(_baseline_manifest()), encoding="utf-8")
    return p


def _write_extract(tmp_path: Path) -> Path:
    """Never read for source.format: custom (main()'s region-pin branch is
    skipped entirely), so its content is irrelevant -- a real durable_root
    still has a file at this path, so the fixture does too."""
    p = tmp_path / "extract.py"
    p.write_text("#!/usr/bin/env python3\n# fixture: never read (source.format: custom)\n", encoding="utf-8")
    return p


def _write_profile(tmp_path: Path, *, entity_markup=None, output_present: bool = True) -> Path:
    profile = {
        "project": {"max_segment_words": 700},
        "footnotes": {"apparatus_policy": "omit_apparatus"},
        "source": {"format": "custom"},
    }
    if output_present:
        output = {}
        if entity_markup is not None:
            output["entity_markup"] = entity_markup
        profile["output"] = output
    p = tmp_path / "profile.yml"
    p.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    return p


def _style_bible_text(span_body: str, *, begin_markers: int = 1, end_markers: int = 1,
                       end_before_begin: bool = False,
                       prologue: str = "Some editorial preface prose.\n",
                       epilogue: str = "\nMore prose after the style contract.\n") -> str:
    begin_block = "\n".join([ve.STYLE_CONTRACT_BEGIN_MARKER] * begin_markers)
    end_block = "\n".join([ve.STYLE_CONTRACT_END_MARKER] * end_markers)
    if end_before_begin:
        return f"{prologue}\n{end_block}\n{span_body}\n{begin_block}\n{epilogue}"
    return f"{prologue}\n{begin_block}\n{span_body}\n{end_block}\n{epilogue}"


def _write_style_bible(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "style_bible.md"
    p.write_text(text, encoding="utf-8")
    return p


def _run_gate(tmp_path: Path, *, entity_markup=None, output_present: bool = True,
              style_bible_text=None, style_bible_bytes=None,
              timeout: int = 30) -> subprocess.CompletedProcess:
    """Writes manifest/extract/profile (and, if given, style_bible.md) to
    tmp_path and invokes the REAL validate_extraction.py as a subprocess --
    the exact three-flag command form documented in SKILL.md's Workflow
    W1-W9 section, under the W2 Extract step.

    ``style_bible_bytes`` writes the file verbatim instead of as text, for the
    case that needs a byte sequence no text write can produce (an invalid
    UTF-8 byte). Both spellings target the SAME path, so passing both is
    refused rather than resolved by a silent precedence rule."""
    assert style_bible_text is None or style_bible_bytes is None, (
        "pass style_bible_text or style_bible_bytes, never both -- they write "
        "the same style_bible.md"
    )
    manifest_path = _write_manifest(tmp_path)
    extract_path = _write_extract(tmp_path)
    profile_path = _write_profile(tmp_path, entity_markup=entity_markup, output_present=output_present)
    if style_bible_text is not None:
        _write_style_bible(tmp_path, style_bible_text)
    if style_bible_bytes is not None:
        (tmp_path / "style_bible.md").write_bytes(style_bible_bytes)
    return subprocess.run(
        [
            sys.executable, str(SCRIPT_PATH),
            "--manifest", str(manifest_path),
            "--extract", str(extract_path),
            "--profile", str(profile_path),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Case 1 -- a declared tag missing from the span
# ---------------------------------------------------------------------------

def test_missing_tag_fails_naming_it(tmp_path):
    style_bible = _style_bible_text("General instructions with no entity markup mentioned at all.")
    proc = _run_gate(
        tmp_path,
        entity_markup={"index_from": "markup", "tags": ["place"]},
        style_bible_text=style_bible,
    )
    assert proc.returncode == 1, proc.stderr
    fail_lines = [ln for ln in proc.stderr.splitlines() if ln.startswith(f"FAIL {CHECK_NAME}: ")]
    assert len(fail_lines) == 1, proc.stderr
    assert "place" in fail_lines[0]
    assert f"PASS {CHECK_NAME}" not in proc.stdout


# ---------------------------------------------------------------------------
# Case 2 (and the SKILL.md W2 Extract step's documented command-form smoke
# test) --
# every declared tag present
# ---------------------------------------------------------------------------

def test_all_declared_tags_present_exits_zero(tmp_path):
    """Also the invocation documented in SKILL.md's W2 Extract step: --manifest, then
    --extract, then --profile, in that exact order, against a real durable
    project layout on disk."""
    style_bible = _style_bible_text(
        "Mark named individuals with <person>...</person> tags and places "
        "with <place>...</place> tags."
    )
    manifest_path = _write_manifest(tmp_path)
    extract_path = _write_extract(tmp_path)
    profile_path = _write_profile(tmp_path, entity_markup={"index_from": "markup", "tags": ["person", "place"]})
    _write_style_bible(tmp_path, style_bible)

    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT_PATH),
            "--manifest", str(manifest_path),
            "--extract", str(extract_path),
            "--profile", str(profile_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    pass_lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(f"PASS {CHECK_NAME}")]
    assert len(pass_lines) == 1, proc.stdout
    assert "declared tag(s) verified" in pass_lines[0]


# ---------------------------------------------------------------------------
# Case 3 -- prefix collision regression (round-1 BLOCKER)
# ---------------------------------------------------------------------------

def _only_missing_tag_mentions(fail_line: str) -> str:
    """Isolates the rendered <tag> list out of the FAIL line's fixed prose,
    so a substring check for 'person' cannot accidentally match inside
    'person-title' when person-title must NOT be reported missing."""
    marker = "does not name declared tag(s) "
    idx = fail_line.index(marker) + len(marker)
    end = fail_line.index(" -- add each one", idx)
    return fail_line[idx:end]


def test_prefix_collision_person_vs_person_title(tmp_path):
    """tags: [person, person-title]; the contract documents ONLY
    <person-title>. A raw "<" + tag substring match (the round-1 BLOCKER)
    would let person-title's occurrence satisfy person too; the shipped
    terminator must not -- person alone is reported missing, and
    person-title (whose own occurrence matched) is not."""
    style_bible = _style_bible_text(
        "Mark honorific titles with <person-title>...</person-title>."
    )
    proc = _run_gate(
        tmp_path,
        entity_markup={"index_from": "markup", "tags": ["person", "person-title"]},
        style_bible_text=style_bible,
    )
    assert proc.returncode == 1, proc.stderr
    fail_lines = [ln for ln in proc.stderr.splitlines() if ln.startswith(f"FAIL {CHECK_NAME}: ")]
    assert len(fail_lines) == 1, proc.stderr
    rendered = _only_missing_tag_mentions(fail_lines[0])
    assert rendered == "<person>", fail_lines[0]


# ---------------------------------------------------------------------------
# Case 3b -- the round-2 bypasses that a negative-lookahead terminator admits
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("false_evidence", ["<personTitle>", "<person:name>", "<personé>"])
def test_negative_lookahead_bypasses_are_rejected(tmp_path, false_evidence):
    """This is what distinguishes the shipped positive-lookahead terminator
    (?=$|[\\s/>]) from the REJECTED (?![a-z0-9_-]) negative-lookahead one:
    the rejected matcher passes case 3 above AND still admits all three of
    these as false evidence that `person` was named, because none of
    'T', ':', or 'é' is in [a-z0-9_-]. Without this parameterized case a
    regression to the rejected matcher would stay green."""
    style_bible = _style_bible_text(f"An aside mentioning {false_evidence} in passing.")
    proc = _run_gate(
        tmp_path,
        entity_markup={"index_from": "markup", "tags": ["person"]},
        style_bible_text=style_bible,
    )
    assert proc.returncode == 1, proc.stderr
    fail_lines = [ln for ln in proc.stderr.splitlines() if ln.startswith(f"FAIL {CHECK_NAME}: ")]
    assert len(fail_lines) == 1, proc.stderr
    assert "person" in fail_lines[0]


# ---------------------------------------------------------------------------
# Case 4 -- a tag ending in `-` or `_` is matched correctly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tag", ["honor-", "note_"])
def test_tag_ending_in_hyphen_or_underscore_matches(tmp_path, tag):
    """A tag may legally end in '-' or '_' (profile.schema.json's pattern
    allows both), and both must still match their own opening/closing form.

    Only the `honor-` parameter is a \\b MUTATION detector. Python places no
    word boundary between a trailing '-' and the '>' that follows it, so a
    \\b-based terminator never matches `<honor->` and that parameter goes red.
    `_` IS a Python word character, so `</?note_\\b` DOES match `<note_>` and
    the `note_` parameter stays green under the same broken matcher -- it is a
    valid-shape positive case here, never evidence about \\b."""
    style_bible = _style_bible_text(f"Use <{tag}>...</{tag}> for this element.")
    proc = _run_gate(
        tmp_path,
        entity_markup={"index_from": "markup", "tags": [tag]},
        style_bible_text=style_bible,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"PASS {CHECK_NAME}" in proc.stdout


# ---------------------------------------------------------------------------
# Case 5 -- tag present in style_bible.md but OUTSIDE the markers
# ---------------------------------------------------------------------------

def test_tag_outside_markers_fails(tmp_path):
    style_bible = _style_bible_text(
        "",  # nothing inside the span
        epilogue="\nAn appendix, after the contract, mentions <place>...</place>.\n",
    )
    proc = _run_gate(
        tmp_path,
        entity_markup={"index_from": "markup", "tags": ["place"]},
        style_bible_text=style_bible,
    )
    assert proc.returncode == 1, proc.stderr
    fail_lines = [ln for ln in proc.stderr.splitlines() if ln.startswith(f"FAIL {CHECK_NAME}: ")]
    assert len(fail_lines) == 1, proc.stderr
    assert "place" in fail_lines[0]


# ---------------------------------------------------------------------------
# Case 6 -- index_from absent / index_from: canon -> inert, no style_bible.md
# needed at all (the check must return before ever resolving that path)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entity_markup", [
    {"tags": ["ghost"]},  # index_from absent -> resolves to canon
    {"tags": ["ghost"], "index_from": "canon"},
])
def test_non_markup_index_from_is_inert(tmp_path, entity_markup):
    # No style_bible.md is written at all -- proves the check returns inert
    # before ever resolving that path when index_from is not "markup".
    proc = _run_gate(tmp_path, entity_markup=entity_markup, style_bible_text=None)
    assert proc.returncode == 0, proc.stderr
    pass_lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(f"PASS {CHECK_NAME}")]
    assert len(pass_lines) == 1, proc.stdout
    assert "inert" in pass_lines[0]


# ---------------------------------------------------------------------------
# Case 7 -- no entity_markup block at all -> inert
# ---------------------------------------------------------------------------

def test_no_entity_markup_block_is_inert(tmp_path):
    proc = _run_gate(tmp_path, entity_markup=None, output_present=True, style_bible_text=None)
    assert proc.returncode == 0, proc.stderr
    pass_lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(f"PASS {CHECK_NAME}")]
    assert len(pass_lines) == 1, proc.stdout
    assert "inert" in pass_lines[0]


def test_no_output_block_at_all_is_inert(tmp_path):
    proc = _run_gate(tmp_path, entity_markup=None, output_present=False, style_bible_text=None)
    assert proc.returncode == 0, proc.stderr
    pass_lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(f"PASS {CHECK_NAME}")]
    assert len(pass_lines) == 1, proc.stdout
    assert "inert" in pass_lines[0]


# ---------------------------------------------------------------------------
# Case 8 -- tag-shape refusals (no style_bible.md needed: the shape check
# runs before that path is ever resolved)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_tags", ["person", [], [""], [1]], ids=["bare-string", "empty-list", "empty-string-member", "non-string-member"])
def test_malformed_tags_shape_fails_naming_the_field(tmp_path, bad_tags):
    """tags: person is the documented load-bearing trap: a bare string is
    ITERABLE, so an unvalidated reader would zip over its CHARACTERS and
    report success on a per-character vocabulary -- assert this is NOT a
    per-character refusal (the field name is named once, not per letter).
    tags: [] matters on its own: a list-ONLY shape check (isinstance(tags,
    list) alone) would accept it, run zero tag comparisons, and print
    exactly what a passing run prints."""
    proc = _run_gate(
        tmp_path,
        entity_markup={"index_from": "markup", "tags": bad_tags},
        style_bible_text=None,
    )
    assert proc.returncode == 1, proc.stderr
    fail_lines = [ln for ln in proc.stderr.splitlines() if ln.startswith(f"FAIL {CHECK_NAME}: ")]
    assert len(fail_lines) == 1, (
        f"expected exactly one FAIL {CHECK_NAME} line (not one per character), got {fail_lines}"
    )
    assert "output.entity_markup.tags" in fail_lines[0]
    assert f"PASS {CHECK_NAME}" not in proc.stdout


# ---------------------------------------------------------------------------
# Case 9 -- two missing tags named in the same run
# ---------------------------------------------------------------------------

def test_two_missing_tags_both_named(tmp_path):
    style_bible = _style_bible_text("No entity markup mentioned in this contract.")
    proc = _run_gate(
        tmp_path,
        entity_markup={"index_from": "markup", "tags": ["alpha", "beta"]},
        style_bible_text=style_bible,
    )
    assert proc.returncode == 1, proc.stderr
    fail_lines = [ln for ln in proc.stderr.splitlines() if ln.startswith(f"FAIL {CHECK_NAME}: ")]
    assert len(fail_lines) == 1, proc.stderr
    assert "alpha" in fail_lines[0]
    assert "beta" in fail_lines[0]


# ---------------------------------------------------------------------------
# Case 10 -- style_bible.md absent, and every malformed marker-pair state
# ---------------------------------------------------------------------------

def test_style_bible_absent_fails(tmp_path):
    proc = _run_gate(
        tmp_path,
        entity_markup={"index_from": "markup", "tags": ["place"]},
        style_bible_text=None,  # never written at all
    )
    assert proc.returncode == 1, proc.stderr
    fail_lines = [ln for ln in proc.stderr.splitlines() if ln.startswith(f"FAIL {CHECK_NAME}: ")]
    assert len(fail_lines) == 1, proc.stderr
    assert "style_bible.md" in fail_lines[0]


def test_style_bible_with_invalid_utf8_fails_with_a_named_finding(tmp_path):
    """An UNREADABLE style_bible.md must reach the operator as a NAMED finding,
    not as a traceback. `Path.read_text()` raises UnicodeDecodeError on an
    invalid byte, and UnicodeDecodeError is not an OSError -- an OSError-only
    clause let it escape, and the gate printed a stack trace where it documents
    a finding. The exit code alone does not distinguish the two, so this asserts
    the FAIL line and the absence of a traceback, never just returncode == 1."""
    proc = _run_gate(
        tmp_path,
        entity_markup={"index_from": "markup", "tags": ["place"]},
        style_bible_bytes=(
            b"<!-- STYLE_CONTRACT_BEGIN -->\nUse <place> here: \xff\n"
            b"<!-- STYLE_CONTRACT_END -->\n"
        ),
    )
    assert proc.returncode == 1, proc.stderr
    fail_lines = [ln for ln in proc.stderr.splitlines() if ln.startswith(f"FAIL {CHECK_NAME}: ")]
    assert len(fail_lines) == 1, proc.stderr
    assert "style_bible.md" in fail_lines[0]
    assert "Traceback" not in proc.stderr, proc.stderr
    assert f"PASS {CHECK_NAME}" not in proc.stdout


@pytest.mark.parametrize("kwargs,expect_substr", [
    ({"begin_markers": 0, "end_markers": 1}, "STYLE_CONTRACT_BEGIN"),
    ({"begin_markers": 1, "end_markers": 0}, "STYLE_CONTRACT_END"),
    ({"begin_markers": 2, "end_markers": 1}, "STYLE_CONTRACT_BEGIN"),
    ({"begin_markers": 1, "end_markers": 2}, "STYLE_CONTRACT_END"),
    ({"begin_markers": 1, "end_markers": 1, "end_before_begin": True}, "order"),
], ids=["missing-begin", "missing-end", "duplicate-begin", "duplicate-end", "out-of-order"])
def test_malformed_marker_pair_fails_naming_the_state(tmp_path, kwargs, expect_substr):
    style_bible = _style_bible_text("<place>...</place>", **kwargs)
    proc = _run_gate(
        tmp_path,
        entity_markup={"index_from": "markup", "tags": ["place"]},
        style_bible_text=style_bible,
    )
    assert proc.returncode == 1, proc.stderr
    fail_lines = [ln for ln in proc.stderr.splitlines() if ln.startswith(f"FAIL {CHECK_NAME}: ")]
    assert len(fail_lines) == 1, proc.stderr
    assert "style_bible.md" in fail_lines[0]
    assert expect_substr in fail_lines[0]


# ---------------------------------------------------------------------------
# Case 11 -- marker-constant drift test against cache_key.py
# ---------------------------------------------------------------------------

def _cache_key_style_contract_markers() -> dict:
    """Independently parses cache_key.py's compute_style_contract_hash()
    for its begin_marker/end_marker byte-literal
    assignments, decoded to str -- never imported. validate_extraction.py's
    own module comment says why: cache_key.py is a PLUGIN_BUNDLE_MEMBERS
    entry, so importing from it here would pull this gate into the
    generation-hash computation and re-stale every converged segment in
    every project."""
    tree = ast.parse(CACHE_KEY_PATH.read_text(encoding="utf-8"))
    literals = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "compute_style_contract_hash":
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Assign)
                    and len(sub.targets) == 1
                    and isinstance(sub.targets[0], ast.Name)
                    and sub.targets[0].id in ("begin_marker", "end_marker")
                    and isinstance(sub.value, ast.Constant)
                    and isinstance(sub.value.value, bytes)
                ):
                    literals[sub.targets[0].id] = sub.value.value.decode("ascii")
    return literals


def test_style_contract_markers_match_cache_key_source():
    literals = _cache_key_style_contract_markers()
    assert set(literals) == {"begin_marker", "end_marker"}, (
        f"could not parse both marker literals out of cache_key.py's "
        f"compute_style_contract_hash; found {sorted(literals)}"
    )
    assert ve.STYLE_CONTRACT_BEGIN_MARKER == literals["begin_marker"], (
        ve.STYLE_CONTRACT_BEGIN_MARKER, literals["begin_marker"],
    )
    assert ve.STYLE_CONTRACT_END_MARKER == literals["end_marker"], (
        ve.STYLE_CONTRACT_END_MARKER, literals["end_marker"],
    )


# ---------------------------------------------------------------------------
# Case 12 -- control: the gate actually ran every comparison
# ---------------------------------------------------------------------------

def test_verified_count_matches_number_of_declared_tags(tmp_path):
    """A loop that runs zero comparisons must not be able to print what a
    passing one prints -- the success detail names an integer count, and
    this test re-derives that count independently (len(tags)) rather than
    trusting the printed number on its own."""
    tags = ["person", "place", "organization"]
    style_bible = _style_bible_text(
        "Mark with <person>...</person>, <place>...</place>, and "
        "<organization>...</organization>."
    )
    proc = _run_gate(
        tmp_path,
        entity_markup={"index_from": "markup", "tags": tags},
        style_bible_text=style_bible,
    )
    assert proc.returncode == 0, proc.stderr
    pass_line = next(ln for ln in proc.stdout.splitlines() if ln.startswith(f"PASS {CHECK_NAME}"))
    m = re.search(r"(\d+) declared tag\(s\) verified", pass_line)
    assert m is not None, pass_line
    assert int(m.group(1)) == len(tags), pass_line
