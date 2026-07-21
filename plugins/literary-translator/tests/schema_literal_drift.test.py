"""tests/schema_literal_drift.test.py

Targets cross-cutting consistency between restated cache-key/bundle-
membership definitions and their canonical schema/source-of-truth, per
references/ledger-and-resumability.md's "Keep restatements in sync"
paragraph (§8's "Known restatement sites checklist" in the build plan).

That paragraph is explicit about the discipline this file must follow:

    "Prefer deriving the expected field set programmatically in tests
    (e.g. assert cache_key.py --seg <id>'s own printed JSON keys equal
    ledger-record-base.schema.json's declared cache_key property set)
    rather than hand-typing the same list twice."

So this file does exactly that -- for BOTH restatement families it names:

  1. The composite 15-field `cache_key` structure, restated in:
     - `cache_key.py`'s own `CACHE_KEY_FIELD_ORDER` tuple (and its actual
       `--seg <id>` stdout, exercised for real against a constructed
       fixture durable_root -- not just imported and read as a constant),
     - `ledger-record-base.schema.json`'s `cache_key` sub-schema
       (`properties` keys AND `required` list),
     - `ledger_merge.py`'s own `CACHE_KEY_FIELDS` literal,
     - `select_segments.py`'s own `CACHE_KEY_FIELDS` literal.

  2. The three bundle-membership lists (`plugin_bundle_hash`,
     `orchestration_bundle_hash`, `derivation_bundle_hash`), restated in:
     - `cache_key.py`'s own `PLUGIN_BUNDLE_MEMBERS`/`DERIVATION_BUNDLE_MEMBERS`
       tuples (the only two of the three with a real code-level list --
       `orchestration_bundle_hash` has no computing script of its own, per
       the doc: it's a Step 0a marker file, non-gating for convergence but
       gating for resume via the resume-integrity digest),
     - the prose in references/ledger-and-resumability.md's "The three
       separate bundle hashes -- exact membership" section (parsed
       programmatically, not hand-copied here),
     - the individual scripts' own self-declared membership comments
       (`draft_ready.py`/`select_segments.py` both explicitly self-declare
       "covered by orchestration_bundle_hash, never plugin_bundle_hash").

Nothing here hand-types the 15-field list or either bundle list as a
literal Python list of its own -- every expectation is derived from one
of the restatement sites above and cross-checked against the others.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
SCHEMAS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"
REFERENCES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "references"

CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"
LEDGER_MERGE_SCRIPT = SCRIPTS_DIR / "ledger_merge.py"
SELECT_SEGMENTS_SCRIPT = SCRIPTS_DIR / "select_segments.py"
SKEPTIC_CONSTANTS_SCRIPT = SCRIPTS_DIR / "skeptic_constants.py"
LEDGER_RECORD_BASE_SCHEMA = SCHEMAS_DIR / "ledger-record-base.schema.json"
SKEPTIC_ASSIGNMENT_SCHEMA = SCHEMAS_DIR / "skeptic-assignment.schema.json"
LEDGER_AND_RESUMABILITY_DOC = REFERENCES_DIR / "ledger-and-resumability.md"


# ---------------------------------------------------------------------------
# Small loaders -- these read the shipped, real files directly. No literal
# field list or bundle-membership list is hand-typed anywhere below.
# ---------------------------------------------------------------------------


def _load_module(name: str, path: Path) -> types.ModuleType:
    assert path.is_file(), f"expected a real script at {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cache_key_module() -> types.ModuleType:
    """Imports the real, shipped cache_key.py so its own
    CACHE_KEY_FIELD_ORDER / PLUGIN_BUNDLE_MEMBERS / DERIVATION_BUNDLE_MEMBERS
    constants can be read directly -- these are the canonical single-source
    definitions everything else in this file is checked against."""
    return _load_module("cache_key_under_test", CACHE_KEY_SCRIPT)


@pytest.fixture(scope="module")
def ledger_merge_module() -> types.ModuleType:
    return _load_module("ledger_merge_under_test", LEDGER_MERGE_SCRIPT)


@pytest.fixture(scope="module")
def select_segments_module() -> types.ModuleType:
    return _load_module("select_segments_under_test", SELECT_SEGMENTS_SCRIPT)


@pytest.fixture(scope="module")
def skeptic_constants_module() -> types.ModuleType:
    """Imports the real, shipped skeptic_constants.py so FROZEN_INPUT_SPECS
    -- the single authoritative enumeration of the skeptic pass's H1
    frozen-input tripwire (#243 round 8) -- can be read directly. Both
    skeptic_setup.py's stamper and skeptic_ready.py's verifier now derive
    from this SAME tuple, so this file's own drift check below is the last
    restatement left to guard: skeptic-assignment.schema.json's declared
    *_sha256 properties, which is static JSON data deliberately NOT
    generated from the tuple (codegen would be disproportionate for 3
    literal properties)."""
    return _load_module("skeptic_constants_under_test", SKEPTIC_CONSTANTS_SCRIPT)


def _load_ledger_record_base_schema() -> dict:
    return json.loads(LEDGER_RECORD_BASE_SCHEMA.read_text(encoding="utf-8"))


def _load_skeptic_assignment_schema() -> dict:
    return json.loads(SKEPTIC_ASSIGNMENT_SCHEMA.read_text(encoding="utf-8"))


def _load_ledger_and_resumability_doc() -> str:
    return LEDGER_AND_RESUMABILITY_DOC.read_text(encoding="utf-8")


BUNDLE_SECTION_HEADER = "## The three separate bundle hashes"


def _extract_bundle_hashes_section(doc_text: str) -> str:
    """Scopes to references/ledger-and-resumability.md's own dedicated
    "## The three separate bundle hashes -- exact membership" section.

    This document has TWO distinct restatement sites shaped like
    `- **`plugin_bundle_hash`** ...`: an earlier, deliberately abbreviated
    mention inside the "Composite cache key" field-by-field byte-scope
    listing (which does not name all twelve plugin_bundle_hash members --
    ten scripts + two templates as of 1.4.7/#198 -- it only names
    `ledger_update.py` by way of example, plus the two templates), and this
    section's own bullet, which is the one that names the full, exact
    membership list. Searching the whole document
    for the first matching bullet would silently grab the abbreviated
    one instead -- scope to this header's own section first."""
    start = doc_text.find(BUNDLE_SECTION_HEADER)
    assert start != -1, (
        f"could not locate the {BUNDLE_SECTION_HEADER!r} header in "
        f"{LEDGER_AND_RESUMABILITY_DOC}"
    )
    next_header = doc_text.find("\n## ", start + len(BUNDLE_SECTION_HEADER))
    end = next_header if next_header != -1 else len(doc_text)
    return doc_text[start:end]


def _extract_bundle_files(doc_text: str, hash_name: str) -> frozenset[str]:
    """Parses references/ledger-and-resumability.md's own "The three
    separate bundle hashes" section and returns the set of `*.py`/`*.js`
    filenames named in the bullet for `hash_name` (`plugin_bundle_hash`,
    `orchestration_bundle_hash`, or `derivation_bundle_hash`).

    The bullet's own text span runs from its `- **`<hash_name>`**` opener
    up to (but not including) whichever comes first: the next bullet in
    the same list, or a blank-line paragraph break (this is what correctly
    excludes the "`profile_validate.py` is excluded from all three
    bundles" paragraph, which follows the last bullet as separate prose,
    not a bullet item, from ever being swept into `derivation_bundle_hash`'s
    membership)."""
    section_text = _extract_bundle_hashes_section(doc_text)
    pattern = re.compile(
        r"- \*\*`" + re.escape(hash_name) + r"`\*\*.*?(?=\n- \*\*`|\n\n)",
        re.DOTALL,
    )
    match = pattern.search(section_text)
    assert match, (
        f"could not locate a `- **`{hash_name}`**` bullet inside the "
        f"{BUNDLE_SECTION_HEADER!r} section of {LEDGER_AND_RESUMABILITY_DOC}"
    )
    bullet_text = match.group(0)
    filenames = re.findall(r"`([A-Za-z0-9_.-]+\.(?:py|js))`", bullet_text)
    assert filenames, (
        f"the `{hash_name}` bullet in {LEDGER_AND_RESUMABILITY_DOC} named no "
        f"`*.py`/`*.js` files at all -- bullet text was:\n{bullet_text}"
    )
    return frozenset(filenames)


# ---------------------------------------------------------------------------
# Fixture durable_root construction -- exercises cache_key.py's real CLI
# (subprocess), not just its importable constants, per the doc's own
# recommended test discipline ("derive the expected field set
# programmatically ... e.g. assert cache_key.py --seg <id>'s own printed
# JSON keys").
# ---------------------------------------------------------------------------

SEG_ID = "seg01"


def _build_durable_root(tmp_path: Path) -> Path:
    """Builds a minimal-but-complete durable_root fixture sufficient for
    `cache_key.py --seg seg01` to run its real 15-field computation
    end-to-end with no fatal preflight failures. A real copy of the
    shipped cache_key.py is placed at
    `<durable_root>/scripts/cache_key.py` (self-anchoring: the script
    derives durable_root from its own `__file__`, so this is the only way
    to make the CLI genuinely believe this fixture tree is its project)."""
    durable_root = tmp_path / "durable"
    durable_root.mkdir()

    # -- ownership marker + profile.yml (profile.yml deliberately lives
    # outside durable_root, referenced by an absolute path, since
    # cache_key.py never reads project.durable_root itself -- only the
    # dotted profile fields its own field-computation functions actually
    # use).
    profile_path = tmp_path / "profile.yml"
    profile_path.write_text(
        "\n".join(
            [
                "project:",
                "  pipeline_version: v1",
                "engine:",
                "  effort: high",
                "  max_fix_rounds: 4",
                "source:",
                "  format: plain_text",
                "  path: \"/fake/source.txt\"",
                "  language:",
                "    code: fr",
                "    particle_config: \"fr.json\"",
                "  adapter_config:",
                "    plain_text: {}",
                "target:",
                "  language:",
                "    code: ru",
                "verse_policy:",
                "  mode: literal_only",
                "  threshold_lines: null",
                "footnotes:",
                "  apparatus_policy: translate_all",
                "validation:",
                "  untranslated_sentinel: \"NOT TRANSLATED YET\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (durable_root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )

    # -- style_bible.md, with the exact marker pair style_contract_hash needs.
    (durable_root / "style_bible.md").write_text(
        "# Style Bible\n"
        "<!-- STYLE_CONTRACT_BEGIN -->\n"
        "Sections A-F content goes here.\n"
        "<!-- STYLE_CONTRACT_END -->\n"
        "## G. Glossary\n"
        "(not part of the style contract)\n",
        encoding="utf-8",
    )

    # -- schemas/ (project-local copies; cache_key.py only reads raw bytes,
    # never re-parses these as JSON schema, so trivial content suffices).
    schemas_dir = durable_root / "schemas"
    schemas_dir.mkdir()
    for name in ("draft.schema.json", "review.schema.json", "segpack.schema.json"):
        (schemas_dir / name).write_text(f"{{\"marker\": \"{name}\"}}", encoding="utf-8")

    # -- instantiated prompt files (prompt_hash).
    (durable_root / "translate_TASK.md").write_text("Translate task body.\n", encoding="utf-8")
    (durable_root / "review_TASK.md").write_text("Review task body.\n", encoding="utf-8")

    # -- languages/fr.json (particle_config_hash).
    languages_dir = durable_root / "languages"
    languages_dir.mkdir()
    (languages_dir / "fr.json").write_text('{"has_elision": true}', encoding="utf-8")

    # -- extract.py (source_extraction_hash, plain_text/gutenberg_epub path).
    (durable_root / "extract.py").write_text("# stub extractor\n", encoding="utf-8")

    # -- manifest.json + the one source file it names (source_input_hash).
    (durable_root / "source.txt").write_text("Some source prose.\n", encoding="utf-8")
    (durable_root / "manifest.json").write_text(
        json.dumps({"source_inputs": ["source.txt"]}), encoding="utf-8"
    )

    # -- canon.json (used_terms_hash; empty entries is a legitimate state).
    (durable_root / "canon.json").write_text(json.dumps({"entries": {}}), encoding="utf-8")

    # -- scripts/ : the real cache_key.py copy (self-anchoring) plus the two
    # derivation_bundle_hash members (bootstrap_names.py/segpack.py) and the
    # plugin_bundle_hash marker file cache_key.py reads back verbatim.
    scripts_dir = durable_root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "cache_key.py").write_bytes(CACHE_KEY_SCRIPT.read_bytes())
    (scripts_dir / "bootstrap_names.py").write_text("# stub bootstrap_names\n", encoding="utf-8")
    (scripts_dir / "segpack.py").write_text("# stub segpack\n", encoding="utf-8")

    runs_dir = durable_root / "runs"
    runs_dir.mkdir()
    (runs_dir / ".plugin_bundle_hash").write_text("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", encoding="utf-8")

    # -- segments/segpack_{seg}.json (the four per-segment fields).
    segments_dir = durable_root / "segments"
    segments_dir.mkdir()
    (segments_dir / f"segpack_{SEG_ID}.json").write_text(
        json.dumps(
            {
                "blocks": [],
                "canon_names": [],
                "new_names": [],
                "verses": [],
                "footnotes": [],
            }
        ),
        encoding="utf-8",
    )

    return durable_root


def _run_cache_key(durable_root: Path, *extra_args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Invokes the fixture's own copy of cache_key.py as a real subprocess
    -- from a cwd that is neither durable_root nor its scripts/ directory,
    per ledger-and-resumability.md's "Script self-anchoring" invariant
    test recommendation (the script must never assume cwd)."""
    script_path = durable_root / "scripts" / "cache_key.py"
    return subprocess.run(
        [sys.executable, str(script_path), *extra_args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


@pytest.fixture()
def fixture_durable_root(tmp_path: Path) -> Path:
    return _build_durable_root(tmp_path)


@pytest.fixture()
def neutral_cwd(tmp_path: Path) -> Path:
    """A cwd that is deliberately neither durable_root nor its scripts/
    subdirectory -- proves self-anchoring, not accidental cwd-relative
    correctness."""
    d = tmp_path / "somewhere_else_entirely"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# 1. The 15-field cache_key SET: derived from cache_key.py's real stdout,
#    checked against ledger-record-base.schema.json's declared property set
#    -- exactly the discipline the doc itself recommends.
# ---------------------------------------------------------------------------


def test_cache_key_cli_field_set_matches_schema_declared_properties(
    fixture_durable_root, neutral_cwd
):
    result = _run_cache_key(fixture_durable_root, "--seg", SEG_ID, cwd=neutral_cwd)
    assert result.returncode == 0, (
        f"cache_key.py --seg {SEG_ID} failed against the fixture durable_root; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    printed = json.loads(result.stdout)
    printed_keys = set(printed.keys())

    schema = _load_ledger_record_base_schema()
    cache_key_subschema = schema["properties"]["cache_key"]
    schema_properties = set(cache_key_subschema["properties"].keys())
    schema_required = set(cache_key_subschema["required"])

    assert printed_keys == schema_properties, (
        "cache_key.py --seg's printed JSON keys diverged from "
        "ledger-record-base.schema.json's declared cache_key.properties:\n"
        f"  printed only:          {sorted(printed_keys - schema_properties)}\n"
        f"  schema-declared only:  {sorted(schema_properties - printed_keys)}"
    )
    assert printed_keys == schema_required, (
        "ledger-record-base.schema.json's cache_key.required list has drifted "
        "from its own cache_key.properties (or from cache_key.py's real "
        "output):\n"
        f"  printed only:  {sorted(printed_keys - schema_required)}\n"
        f"  required only: {sorted(schema_required - printed_keys)}"
    )
    # additionalProperties: false is itself part of the authoritative,
    # byte-exact contract -- a schema that dropped it would silently accept
    # a 16th field.
    assert cache_key_subschema.get("additionalProperties") is False, (
        "ledger-record-base.schema.json's cache_key sub-schema must set "
        "additionalProperties: false"
    )


def test_cache_key_cli_field_order_matches_cache_key_py_own_constant(
    fixture_durable_root, neutral_cwd, cache_key_module
):
    """cache_key.py's own module docstring says printed field order must
    match CACHE_KEY_FIELD_ORDER exactly ('do not reorder') -- lock that
    behavior against the real subprocess output, not just the constant."""
    result = _run_cache_key(fixture_durable_root, "--seg", SEG_ID, cwd=neutral_cwd)
    assert result.returncode == 0, result.stderr
    printed_keys_in_order = list(json.loads(result.stdout).keys())

    assert printed_keys_in_order == list(cache_key_module.CACHE_KEY_FIELD_ORDER), (
        "cache_key.py --seg's printed key order no longer matches its own "
        "CACHE_KEY_FIELD_ORDER tuple"
    )


def test_cache_key_field_order_constant_has_no_duplicates_and_is_15_long(cache_key_module):
    order = cache_key_module.CACHE_KEY_FIELD_ORDER
    assert len(order) == 15, f"expected exactly 15 cache_key fields, found {len(order)}: {order}"
    assert len(set(order)) == len(order), f"CACHE_KEY_FIELD_ORDER has duplicate fields: {order}"


# ---------------------------------------------------------------------------
# 2. The same 15-field list, restated a further two times as literal Python
#    lists (ledger_merge.py's and select_segments.py's own CACHE_KEY_FIELDS)
#    -- both must mirror cache_key.py's CACHE_KEY_FIELD_ORDER exactly,
#    per their own comments ("mirrors cache_key.py's own
#    CACHE_KEY_FIELD_ORDER and ledger_merge.py's own CACHE_KEY_FIELDS
#    literal").
# ---------------------------------------------------------------------------


def test_ledger_merge_cache_key_fields_matches_cache_key_py(
    cache_key_module, ledger_merge_module
):
    assert list(ledger_merge_module.CACHE_KEY_FIELDS) == list(
        cache_key_module.CACHE_KEY_FIELD_ORDER
    ), (
        "ledger_merge.py's own CACHE_KEY_FIELDS literal has drifted from "
        "cache_key.py's CACHE_KEY_FIELD_ORDER"
    )


def test_select_segments_cache_key_fields_matches_cache_key_py(
    cache_key_module, select_segments_module
):
    assert list(select_segments_module.CACHE_KEY_FIELDS) == list(
        cache_key_module.CACHE_KEY_FIELD_ORDER
    ), (
        "select_segments.py's own CACHE_KEY_FIELDS literal has drifted from "
        "cache_key.py's CACHE_KEY_FIELD_ORDER"
    )


def test_select_segments_derivation_state_fields_are_a_subset_of_cache_key_fields(
    select_segments_module,
):
    """select_segments.py's own DERIVATION_STATE_FIELDS (the four
    'flag-only, needs regeneration' fields) must all actually be members of
    the 15-field cache_key set -- a typo'd/renamed field here would silently
    stop gating derivation-state staleness for that field."""
    assert select_segments_module.DERIVATION_STATE_FIELDS <= set(
        select_segments_module.CACHE_KEY_FIELDS
    )
    assert select_segments_module.DERIVATION_STATE_FIELDS == {
        "particle_config_hash",
        "source_extraction_hash",
        "source_input_hash",
        "derivation_bundle_hash",
    }


# ---------------------------------------------------------------------------
# 3. Bundle-membership restatement sites: cache_key.py's own
#    PLUGIN_BUNDLE_MEMBERS/DERIVATION_BUNDLE_MEMBERS tuples vs. the prose in
#    references/ledger-and-resumability.md, parsed programmatically.
# ---------------------------------------------------------------------------


def test_plugin_bundle_members_match_reference_doc(cache_key_module):
    doc_text = _load_ledger_and_resumability_doc()
    doc_members = _extract_bundle_files(doc_text, "plugin_bundle_hash")
    code_members = frozenset(cache_key_module.PLUGIN_BUNDLE_MEMBERS)

    assert doc_members == code_members, (
        "plugin_bundle_hash membership has drifted between cache_key.py's "
        "PLUGIN_BUNDLE_MEMBERS and references/ledger-and-resumability.md:\n"
        f"  doc only:  {sorted(doc_members - code_members)}\n"
        f"  code only: {sorted(code_members - doc_members)}"
    )


def test_derivation_bundle_members_match_reference_doc(cache_key_module):
    doc_text = _load_ledger_and_resumability_doc()
    doc_members = _extract_bundle_files(doc_text, "derivation_bundle_hash")
    code_members = frozenset(cache_key_module.DERIVATION_BUNDLE_MEMBERS)

    assert doc_members == code_members, (
        "derivation_bundle_hash membership has drifted between "
        "cache_key.py's DERIVATION_BUNDLE_MEMBERS and "
        "references/ledger-and-resumability.md:\n"
        f"  doc only:  {sorted(doc_members - code_members)}\n"
        f"  code only: {sorted(code_members - doc_members)}"
    )


def test_orchestration_bundle_members_from_doc_are_disjoint_from_the_other_two(
    cache_key_module,
):
    """orchestration_bundle_hash has no computing script of its own (it's a
    Step 0a marker file, non-gating for convergence but gating for resume --
    see the doc's own resume-integrity-digest wording), so its only
    restatement sites are the reference doc's prose and each member script's
    own self-declared docstring comment. Cross-check both against the two
    bundles that DO have a code-level list, and confirm every named file
    actually exists under scripts/ (catches a typo'd/renamed member)."""
    doc_text = _load_ledger_and_resumability_doc()
    orchestration_members = _extract_bundle_files(doc_text, "orchestration_bundle_hash")

    assert orchestration_members == {
        "draft_ready.py",
        "ledger_merge.py",
        "language_smoke_report.py",
        "select_segments.py",
    }, f"unexpected orchestration_bundle_hash membership parsed from the doc: {sorted(orchestration_members)}"

    plugin_members = frozenset(cache_key_module.PLUGIN_BUNDLE_MEMBERS)
    derivation_members = frozenset(cache_key_module.DERIVATION_BUNDLE_MEMBERS)

    assert not (orchestration_members & plugin_members), (
        "a script is claimed by both orchestration_bundle_hash (doc) and "
        f"plugin_bundle_hash (code): {sorted(orchestration_members & plugin_members)}"
    )
    assert not (orchestration_members & derivation_members), (
        "a script is claimed by both orchestration_bundle_hash (doc) and "
        f"derivation_bundle_hash (code): {sorted(orchestration_members & derivation_members)}"
    )

    for filename in orchestration_members:
        assert (SCRIPTS_DIR / filename).is_file(), (
            f"orchestration_bundle_hash names {filename!r} in the reference "
            f"doc, but no such file exists under {SCRIPTS_DIR}"
        )


@pytest.mark.parametrize(
    "filename",
    ["draft_ready.py", "select_segments.py"],
)
def test_self_declaring_orchestration_scripts_agree_with_the_doc(filename):
    """draft_ready.py and select_segments.py each self-declare, in their own
    docstrings, that they are covered by orchestration_bundle_hash and
    explicitly NOT plugin_bundle_hash -- a fourth restatement site for this
    same fact. Cross-check the self-declaration text against the doc-parsed
    membership list, rather than trusting either site alone."""
    doc_text = _load_ledger_and_resumability_doc()
    orchestration_members = _extract_bundle_files(doc_text, "orchestration_bundle_hash")
    assert filename in orchestration_members

    script_text = (SCRIPTS_DIR / filename).read_text(encoding="utf-8")
    assert "orchestration_bundle_hash" in script_text, (
        f"{filename} no longer self-declares its orchestration_bundle_hash "
        "membership in its own docstring/comments"
    )
    assert "plugin_bundle_hash" in script_text, (
        f"{filename} no longer self-declares its EXCLUSION from "
        "plugin_bundle_hash in its own docstring/comments"
    )


def test_profile_validate_excluded_from_every_bundle(cache_key_module):
    doc_text = _load_ledger_and_resumability_doc()

    plugin_members = frozenset(cache_key_module.PLUGIN_BUNDLE_MEMBERS)
    derivation_members = frozenset(cache_key_module.DERIVATION_BUNDLE_MEMBERS)
    orchestration_members = _extract_bundle_files(doc_text, "orchestration_bundle_hash")

    assert "profile_validate.py" not in plugin_members
    assert "profile_validate.py" not in derivation_members
    assert "profile_validate.py" not in orchestration_members

    assert "profile_validate.py` is excluded from **all three** bundles" in doc_text, (
        "references/ledger-and-resumability.md no longer states that "
        "profile_validate.py is excluded from all three bundles"
    )


def test_review_ready_and_resume_setup_are_plugin_bundle_members(cache_key_module):
    """1.2.0 (CONTRACT-1.2.0-reliability.md §4): 'Add review_ready.py +
    resume_setup.py to PLUGIN_BUNDLE_MEMBERS.' Every other assertion in this
    file is fully derived (it would accept either script's addition or
    absence silently, as long as both restatement sites agree) -- this is a
    NAMED regression-catcher locking the two specific new memberships the
    CONTRACT commits to, so a build that forgets one fails loudly here
    rather than only via a downstream resume-integrity/cache-invalidation
    surprise."""
    plugin_members = frozenset(cache_key_module.PLUGIN_BUNDLE_MEMBERS)
    missing = {"review_ready.py", "resume_setup.py"} - plugin_members
    assert not missing, (
        f"PLUGIN_BUNDLE_MEMBERS is missing 1.2.0's new member(s) {sorted(missing)} "
        f"-- CONTRACT-1.2.0-reliability.md §4 requires both review_ready.py and "
        f"resume_setup.py to gate plugin_bundle_hash (and therefore cache reuse)"
    )


def test_bundle_member_scripts_and_templates_exist_on_disk(cache_key_module):
    """Every filename named in either code-level bundle-membership tuple
    must actually resolve to a real, shipped file -- scripts under
    assets/scripts/, the two workflow templates under assets/templates/."""
    for filename in cache_key_module.PLUGIN_BUNDLE_MEMBERS:
        if filename.endswith(".js"):
            assert (TEMPLATES_DIR / filename).is_file(), (
                f"plugin_bundle_hash names template {filename!r}, but no "
                f"such file exists under {TEMPLATES_DIR}"
            )
        else:
            assert (SCRIPTS_DIR / filename).is_file(), (
                f"plugin_bundle_hash names script {filename!r}, but no such "
                f"file exists under {SCRIPTS_DIR}"
            )
    for filename in cache_key_module.DERIVATION_BUNDLE_MEMBERS:
        assert (SCRIPTS_DIR / filename).is_file(), (
            f"derivation_bundle_hash names script {filename!r}, but no such "
            f"file exists under {SCRIPTS_DIR}"
        )


# ---------------------------------------------------------------------------
# 3. FROZEN_INPUT_SPECS (skeptic_constants.py, #243 round 8) <-> the *_sha256
#    stamp properties skeptic-assignment.schema.json declares. Round 8 bound
#    skeptic_setup.py's stamper AND skeptic_ready.py's verifier to this SAME
#    tuple specifically so neither could drift from the other any more --
#    but the schema is static JSON data, deliberately not generated from
#    the tuple (codegen would be disproportionate for 3 literal
#    properties), so it is now the ONE remaining independent restatement of
#    the frozen-input set. An unbound enumeration left to drift is exactly
#    how #243's original bug (manifest.json quietly missing from the
#    verifier table) happened; this is the same discipline as section 1
#    above, applied to the newer table.
# ---------------------------------------------------------------------------


def test_frozen_input_specs_stamp_fields_match_schema_declared_sha256_properties(
    skeptic_constants_module,
):
    """EQUALS, not a subset check in either direction: a schema property
    with no FROZEN_INPUT_SPECS entry would be stamped nowhere but declared
    accepted by the schema (silently unverified); a FROZEN_INPUT_SPECS
    entry with no schema property would make skeptic_setup.py write a
    field skeptic-assignment.schema.json's own additionalProperties:false
    then rejects outright. A subset assertion in either direction would
    stay green through the exact drift this test exists to catch."""
    tuple_stamp_fields = {
        stamp_field for _key, _label, stamp_field in skeptic_constants_module.FROZEN_INPUT_SPECS
    }

    schema = _load_skeptic_assignment_schema()
    schema_properties = set(schema["properties"].keys())
    # Frozen-input stamps are exactly this schema's own *_sha256
    # properties: input_digest/producer_input_digest use an unrelated
    # "_digest" suffix, and the only other "sha256" anywhere in this
    # schema is nested inside assignments[].evidence.sha256 (a citation
    # hash, not a top-level property) -- so filtering the TOP-LEVEL
    # properties dict by this suffix is exact, not a heuristic subset.
    schema_stamp_fields = {name for name in schema_properties if name.endswith("_sha256")}

    assert tuple_stamp_fields == schema_stamp_fields, (
        "FROZEN_INPUT_SPECS (skeptic_constants.py) has diverged from "
        "skeptic-assignment.schema.json's own declared *_sha256 stamp "
        "properties -- exactly the class of drift #243 itself was:\n"
        f"  FROZEN_INPUT_SPECS only:  {sorted(tuple_stamp_fields - schema_stamp_fields)}\n"
        f"  schema-declared only:     {sorted(schema_stamp_fields - tuple_stamp_fields)}"
    )


def test_frozen_input_specs_keys_are_unique(skeptic_constants_module):
    """Round 11 (#243, codex round-11 finding): a SEPARATE invariant from
    the parity test immediately above, and deliberately a SEPARATE test
    rather than folded into it. The parity test compares SETS of STAMP
    FIELD names (the third tuple element) against the schema's declared
    *_sha256 properties -- it is blind to a duplicate in the FIRST tuple
    element (the snapshot KEY compute_producer_input_digest()/
    compute_skeptic_input_digest()/skeptic_setup.py's own
    `_frozen_input_snapshots_by_key` all index by): a fourth
    `FROZEN_INPUT_SPECS` entry that reuses an existing key, e.g.
    `("canon", "fourth.json", "fourth_sha256")`, has a perfectly distinct,
    schema-matchable stamp field (`"fourth_sha256"` != `"canon_sha256"`),
    so it would stay GREEN through the parity test above even though the
    reused `"canon"` key makes every KEY-indexed consumer alias
    `fourth.json`'s stamp onto `canon.json`'s own (state, bytes) snapshot
    -- `canon.json` gets stamped and checked twice, `fourth.json` never
    genuinely represented. Widening the parity test's own EQUALS-not-
    subset assertion to also dedupe-check would blur what it actually
    proves (stamp-field <-> schema-property correspondence) and make its
    own failure message ambiguous about which of two unrelated invariants
    broke; a dedicated test keeps each failure message pointing at the
    one thing that's actually wrong.

    This is deliberately a STATIC, structural check on the tuple itself
    -- independent of, and a layer beneath, the runtime fail-closed guards
    `compute_producer_input_digest()` / `compute_skeptic_input_digest()` /
    `skeptic_setup.py`'s `run()` now carry (round 11's sorted
    non-deduplicated key-LIST comparison, see
    tests/suspicion_scan.test.py's and tests/skeptic_setup.test.py's own
    `duplicate_key_entry` cases): those guards catch a duplicate key
    reactively, the moment some digest/stamp function is next called with
    it. This test catches the SAME mistake proactively, the moment it
    lands in skeptic_constants.py, without needing to drive any of the
    three consumers through a call at all."""
    keys = [spec[0] for spec in skeptic_constants_module.FROZEN_INPUT_SPECS]
    assert len(keys) == len(set(keys)), (
        "FROZEN_INPUT_SPECS (skeptic_constants.py) has a duplicate key -- "
        f"keys: {keys!r}. Every entry must own a UNIQUE first element: "
        "compute_producer_input_digest()/compute_skeptic_input_digest()/"
        "skeptic_setup.py's run() all index a {key: (state, bytes)} "
        "snapshot map by this exact value, so a reused key silently "
        "aliases one frozen input's snapshot onto another entry's stamp "
        "instead of raising -- see skeptic_constants.py's \"what "
        "FROZEN_INPUT_SPECS does NOT cover\" comment."
    )


# ---------------------------------------------------------------------------
# 4. The hand-duplicated FROZEN_INPUT_SPECS key-mismatch guards -- an
#    ALLOWLIST, not a denylist (redesigned; the six-round denylist story
#    this replaces -- #243 rounds 11-16 -- is preserved in git history, not
#    reproduced here).
# ---------------------------------------------------------------------------
#
# `compute_producer_input_digest()` (suspicion_scan.py),
# `compute_skeptic_input_digest()` and `run()` (both skeptic_setup.py), and
# `frozen_input_check()` (skeptic_ready.py) each carry their OWN hand-typed
# copy of the identical `sorted(snapshot_keys) != sorted(spec_keys)`
# comparison -- guarding against a frozen input added to
# skeptic_constants.FROZEN_INPUT_SPECS without a matching hand-added entry
# at that site (or a duplicate key within the tuple itself). Runtime,
# call-driven proof that each of these four guards actually FIRES on a real
# mismatch lives in tests/suspicion_scan.test.py's and
# tests/skeptic_setup.test.py's own `duplicate_key_entry`/
# `same_count_key_swap` cases and tests/skeptic_ready.test.py's
# `test_check_frozen_inputs_fails_closed_on_frozen_input_specs_key_mismatch`;
# this section is a STATIC, sibling-consistency check that all four still
# exist, unweakened, as distinct source-level obligations.
#
# The prior version of this check (#243, rounds 11-16) was a DENYLIST: each
# review round found one more way to weaken a guard while keeping it "close
# enough" to pass, and the check grew one more rejection rule to name that
# specific weakening. Six rounds in, the reviewer's own prescribed fixes
# were "implement reaching-definition analysis" and "handle every binding
# form systematically" -- i.e. build a general dataflow analyzer inside a
# test, for four guards that are all, today, textually near-identical in
# shape. A denylist has no bounded endpoint: it can only ever enumerate
# weakenings someone has already thought to check for.
#
# This version inverts the approach. Instead of asking "is this guard
# weakened in some way we remember to check for", it requires each guard to
# be STRUCTURALLY IDENTICAL to one of a small, closed set of canonical AST
# shapes -- anything that does not match is REJECTED outright, with no
# attempt to characterize why it differs. This is strictly STRONGER than
# the denylist it replaced (which could only ever enumerate weakenings
# someone had already thought to check for) -- but it is NOT "sound by
# construction against every weakening, including ones nobody has thought
# of yet": round 17 found four places where the structural match itself was
# not yet strict enough (a non-canonical destructure target, a
# non-adjacent message assignment, a decoy anchor reachable through a
# non-constant expression, a scope walker blind to decorator/default/
# annotation bindings) and one place it was too strict. The actual TRUST
# BOUNDARY this check rests on is the COMPLETENESS of the canonical-shape
# template each guard is compared against -- a gap in that template (a real
# weakening the template happens not to distinguish from the canonical
# shape) still passes silently. What this buys over the denylist is that
# closing such a gap is a bounded, one-time tightening of the template
# itself, not an open-ended new rejection rule; it needs no dataflow/
# reaching-definition analysis, only structural AST comparison plus a small
# set of SCOPE-BOUNDED facts (a name's binding count and binding kind
# within its own owning function).
#
# The allowlist, in full -- a candidate `if` qualifies as a guard iff ALL
# of:
#   1. Its test is `sorted(X) != sorted(Y)`: a `Compare` with a single
#      `!=` whose both sides are a bare-Name-argument call to the builtin
#      `sorted` (`_sorted_mismatch_operand_names`).
#   2. Exactly ONE of X/Y is bound, as its scope's SOLE binding (of any
#      kind at all -- a name that is ALSO a parameter, or declared
#      global/nonlocal, has more than one real binding even when only one
#      of those is a plain Assign), by a plain `name = <value>` Assign
#      whose value is STRUCTURALLY one of the two canonical
#      FROZEN_INPUT_SPECS key-projection comprehensions the real sites use
#      (`_is_canonical_spec_keys_projection`, `_spec_operand_name`) -- NOT
#      a membership/contains check on whether the name `FROZEN_INPUT_SPECS`
#      merely appears somewhere in the expression (a label projection or a
#      third-name alias both merely CONTAIN that name too).
#   3. The OTHER operand is simply a different bare Name. This check does
#      not validate how it was built -- it is the value under test.
#   4. The builtin `sorted` is bound by NO form -- assignment, `def`,
#      `class`, parameter (incl. defaults/posonly/kwonly/*args/**kwargs),
#      walrus (incl. one scoped inside a comprehension), `for`/`with`/
#      `except`/`import ... as`, `global`/`nonlocal`, or a match-case
#      capture -- anywhere in the guard's own owning scope OR at module
#      level in the same file (`_sorted_is_unshadowed`).
#   5. The raised exception is EXPLICITLY `AssertionError`, found as a
#      DIRECT statement of the `if`'s own body, not nested inside any
#      further compound statement the body contains
#      (`_is_assertion_error_raise`).
#   6. That SAME Raise's exception value carries `ANCHOR_PHRASE` -- either
#      directly, as a string literal anywhere in the Raise's own subtree,
#      or via a message Name whose SOLE binding anywhere in the if-body's
#      own flattened scope is a plain, directly-preceding, same-level
#      if-body Assign to that name
#      (`_if_body_sole_preceding_message_value`). An intervening rebind --
#      wherever in the if-body it is nested, not just a same-level one --
#      disqualifies the message unconditionally.
#   7. The guard's owner resolves to exactly ONE module-level function of
#      the expected name in `EXPECTED_GUARD_SITES` (`_scan_file`,
#      `_site_key`). Two module-level defs sharing that name (e.g. a dead
#      `if False: def run(): ...` sitting beside a real, unguarded `def
#      run()`) is treated as AMBIGUOUS, not silently resolved toward
#      whichever copy happens to carry the guard.
#
# `_is_canonical_spec_keys_projection` accepts TWO forms, not one, because
# the four real, shipped sites use both -- verified against the actual
# source before writing this, not assumed identical across sites:
# `spec_keys = [spec[0] for spec in FROZEN_INPUT_SPECS]` at three sites,
# and skeptic_ready.py's own `_spec_keys = [key for key, _label,
# _stamp_key in FROZEN_INPUT_SPECS]` at the fourth. Both are the identical
# projection -- the first field of every FROZEN_INPUT_SPECS 3-tuple --
# spelled two equally direct ways; treating only one as canonical would
# false-reject a real, already-shipped guard. The loop variable's own
# spelling is deliberately NOT pinned (this check is alpha-equivalent): a
# bound variable's name carries no semantic weight, so pinning it to
# today's literal `spec`/`key` spellings would be a gratuitous false-RED
# risk against a cosmetic rename, not a real weakening closed.
#
# What this does NOT prove, stated plainly (unchanged from every prior
# version of this section):
#   - Reachability/dominance: a canonical guard placed after an
#     unconditional `return` (or behind any other dead-code path) still
#     qualifies. The behavioral tests named above give real, but partial,
#     evidence beyond what this static check provides -- not a general
#     reachability proof.
#   - An unknown FIFTH key-indexed consumer added later with no guard at
#     all: invisible to this check by construction. Tracked separately as
#     a follow-up to centralize all four guards behind one shared helper,
#     which would close this by construction instead of by enumeration.
#   - A rebind reached through `builtins.sorted = ...`, `globals()[...]`,
#     or an alias built through a THIRD name never itself checked against
#     FROZEN_INPUT_SPECS or the other operand -- general aliasing analysis,
#     which this file continues to decline to build (disproportionate for
#     four guards, none of which need it today).
#   - A non-canonical-but-CORRECT guard form -- a chained/destructuring
#     assignment, a message built via `+=` or `.format()`, ... -- is
#     intentionally REJECTED. The allowlist requires the canonical shape,
#     which all four real sites already use; this is a deliberate
#     false-reject by design, not a bug to fix later.


ANCHOR_PHRASE = "FROZEN_INPUT_SPECS contains a duplicate key"

# The four (file, function) semantic obligations a guard must resolve to --
# still a literal, hand-maintained mapping, like section 1's literal `4`:
# there is no honest way to derive "which four functions SHOULD own a
# guard" from the source itself, so a guard silently vanishing, or
# reappearing somewhere else, must fail LOUD via an identity mismatch
# below, not be absorbed by an unbounded count. Unlike the pre-redesign
# version, this does NOT also pin each site's own two operand NAMES: the
# structural requirements above (the spec operand must independently prove
# itself, via requirement 2; the other operand's own spelling is never
# validated, per requirement 3) already make a "right shape, wrong
# operand" copy-paste pointless to separately guard against here.
EXPECTED_GUARD_SITES: "tuple[tuple[str, str], ...]" = (
    ("suspicion_scan.py", "compute_producer_input_digest"),
    ("skeptic_setup.py", "compute_skeptic_input_digest"),
    ("skeptic_setup.py", "run"),
    ("skeptic_ready.py", "frozen_input_check"),
)


# ---------------------------------------------------------------------------
# 4a. Scope-bounded binding facts -- shared by the operand check
#     (requirement 2/3), the `sorted`-shadow check (requirement 4), and the
#     message-immediacy check (requirement 6). "Scope", throughout, means
#     a flattened view of a statement list: every statement reachable
#     through control-flow (If/For/While/Try/With/Match, and their
#     `orelse`/`finalbody`/`handlers`/`cases`) that does not itself
#     introduce a new Python scope, but never descending into a nested
#     FunctionDef/AsyncFunctionDef/ClassDef/Lambda body, each of which
#     owns its own separate scope.
# ---------------------------------------------------------------------------


def _walrus_targets_in_comprehension(node: "ast.AST") -> "list[str]":
    """Every walrus (`:=`) target bound by a comprehension node -- PEP 572
    binds a comprehension's own walrus to its CONTAINING scope, unlike
    everything else the comprehension binds (its own `for` targets, its
    element expression), which stays local to the comprehension itself and
    is correctly invisible to the enclosing scope this function is asked
    about. PEP 572 also makes a comprehension NESTED inside `node` (e.g.
    `[[y := x for x in row] for row in grid]`) transparent for this same
    rule -- its walrus binds to the SAME containing scope as `node`'s own,
    not to the inner comprehension -- so this walk continues through a
    nested comprehension rather than treating it as a boundary. A `Lambda`,
    `FunctionDef`, `AsyncFunctionDef`, or `ClassDef` nested inside `node` is
    a REAL scope boundary, though: a walrus inside one of those belongs to
    THAT scope, never to `node`'s containing scope, so the walk stops
    there -- unlike a raw `ast.walk(node)`, which cannot tell the
    difference and would wrongly attribute e.g. `[x for x in (lambda: (y
    := 1))()]`'s inner walrus to `node`'s own containing scope, exactly
    the false-reject this replaces."""
    targets: "list[str]" = []

    def _walk(n: "ast.AST") -> None:
        if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
            targets.append(n.target.id)
        if isinstance(n, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return  # a real nested scope -- its own walruses are not node's
        for child in ast.iter_child_nodes(n):
            _walk(child)

    _walk(node)
    return targets


def _match_pattern_capture_names(pattern: "ast.AST") -> "list[str]":
    """Every name a `match`/`case` pattern binds via capture -- `case x:`,
    `case [a, *rest]:`, `case {"k": v, **rest}:`, `case Point(x=a, y=b) as
    p:`, `case A() | B() as q:`, .... None of these are `ast.Name` nodes
    (a pattern's bound identifiers are plain `str` attributes --
    `MatchAs.name`, `MatchStar.name`, `MatchMapping.rest`), so a
    Store-context Name walk alone can never see them; required explicitly
    because match-capture is one of the binding forms `sorted` must not be
    reachable through (requirement 4)."""
    names: "list[str]" = []
    if isinstance(pattern, ast.MatchAs):
        if pattern.name:
            names.append(pattern.name)
        if pattern.pattern is not None:
            names.extend(_match_pattern_capture_names(pattern.pattern))
    elif isinstance(pattern, ast.MatchStar):
        if pattern.name:
            names.append(pattern.name)
    elif isinstance(pattern, ast.MatchMapping):
        if pattern.rest:
            names.append(pattern.rest)
        for sub in pattern.patterns:
            names.extend(_match_pattern_capture_names(sub))
    elif isinstance(pattern, ast.MatchSequence):
        for sub in pattern.patterns:
            names.extend(_match_pattern_capture_names(sub))
    elif isinstance(pattern, ast.MatchClass):
        for sub in (*pattern.patterns, *pattern.kwd_patterns):
            names.extend(_match_pattern_capture_names(sub))
    elif isinstance(pattern, ast.MatchOr):
        for sub in pattern.patterns:
            names.extend(_match_pattern_capture_names(sub))
    # MatchValue / MatchSingleton bind nothing.
    return names


def _scope_binding_info(
    scope_body: "list[ast.stmt]",
) -> "tuple[dict[str, int], dict[str, ast.AST | None], set[str]]":
    """Returns `(counts, sole_assign_values, global_or_nonlocal_names)` for
    every name bound within `scope_body`'s own flattened scope (see the
    section header above for exactly what "flattened" means here).

    `counts[name]` is the total number of times `name` is bound by ANY
    form this scope can see: a plain or tuple/starred-unpack Assign
    target, AugAssign/AnnAssign/For/With targets, a walrus (including one
    scoped inside a comprehension), `except ... as name`, `import ... as
    name`/`from ... import x as name`, a `def`/`class` statement's OWN
    name (bound in ITS containing scope, before this walk skips into its
    separate body -- easy to miss, since the natural way to skip a nested
    def/class's body is to return immediately from an overridden visitor
    method, which also skips recording the def/class's own name unless
    that is done as the very first thing in the same method: `def
    sorted(...): ...` at module level is a real rebind of the builtin, not
    a no-op, precisely because Python's `def` statement is itself a
    name-binding statement), and a match-case capture. A nested `def`'s own
    HEADER -- its decorator list, parameter annotations and defaults, and
    return annotation -- executes in THIS scope at def-statement time, not
    inside the function's own separate body, so a walrus there (e.g. a
    module-level `@(sorted := (lambda value: value))` decorator on a
    guard's owning function) is walked too, unlike the body itself.

    `sole_assign_values[name]` is the `.value` of `name`'s own single
    plain `name = <value>` Assign (a bare-Name target, not a tuple/
    starred unpack) -- meaningful, and only ever consulted by a caller,
    when `counts[name] == 1`; any other binding kind, or more than one
    binding, is already a disqualifying fact before this map is read.

    `global_or_nonlocal_names` is every name a `global`/`nonlocal`
    statement anywhere in `scope_body` declares -- such a name's real
    binding lives in an outer scope this walk never visits, so a local
    count for it cannot be trusted either way; callers treat membership
    here as an unconditional disqualifier rather than trusting `counts`.
    """
    counts: "dict[str, int]" = {}
    sole_assign_values: "dict[str, ast.AST | None]" = {}
    global_or_nonlocal: "set[str]" = set()

    def _record(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    class _ScopeBindingVisitor(ast.NodeVisitor):
        def _visit_function_header(
            self, node: "ast.FunctionDef | ast.AsyncFunctionDef"
        ) -> None:
            """Walks the decorator list, every parameter annotation/default,
            and the return annotation -- all of which execute in THIS
            (owning) scope at def-statement time -- using this SAME visitor,
            so a walrus/def/class/etc. nested in one of them is recorded
            exactly as it would be anywhere else in this scope, while a
            nested Lambda within one of them still correctly halts descent
            via `visit_Lambda` below. Never touches `node.body` -- that
            remains a separate scope this walk does not enter."""
            for decorator in node.decorator_list:
                self.visit(decorator)
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                if arg.annotation is not None:
                    self.visit(arg.annotation)
            if args.vararg is not None and args.vararg.annotation is not None:
                self.visit(args.vararg.annotation)
            if args.kwarg is not None and args.kwarg.annotation is not None:
                self.visit(args.kwarg.annotation)
            for default in args.defaults:
                self.visit(default)
            for kw_default in args.kw_defaults:
                if kw_default is not None:
                    self.visit(kw_default)
            if node.returns is not None:
                self.visit(node.returns)

        def visit_FunctionDef(self, node: "ast.FunctionDef") -> None:
            _record(node.name)
            self._visit_function_header(node)
            # own body is a separate scope -- do not descend into it.

        def visit_AsyncFunctionDef(self, node: "ast.AsyncFunctionDef") -> None:
            _record(node.name)
            self._visit_function_header(node)

        def visit_ClassDef(self, node: "ast.ClassDef") -> None:
            _record(node.name)

        def visit_Lambda(self, node: "ast.Lambda") -> None:
            return  # anonymous -- nothing of its own to record; separate scope

        def _record_comprehension_walrus(self, node: "ast.AST") -> None:
            for name in _walrus_targets_in_comprehension(node):
                _record(name)

        def visit_ListComp(self, node: "ast.ListComp") -> None:
            self._record_comprehension_walrus(node)

        def visit_SetComp(self, node: "ast.SetComp") -> None:
            self._record_comprehension_walrus(node)

        def visit_DictComp(self, node: "ast.DictComp") -> None:
            self._record_comprehension_walrus(node)

        def visit_GeneratorExp(self, node: "ast.GeneratorExp") -> None:
            self._record_comprehension_walrus(node)

        def visit_Assign(self, node: "ast.Assign") -> None:
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                # More than one plain Assign to the same bare name is
                # itself already a count>1 fact `counts` will carry;
                # recording None here just avoids handing back an
                # arbitrary ONE of several conflicting values.
                sole_assign_values[name] = (
                    node.value if name not in sole_assign_values else None
                )
            self.generic_visit(node)  # still counts the target(s) + walks the value

        def visit_ExceptHandler(self, node: "ast.ExceptHandler") -> None:
            if node.name:
                _record(node.name)
            self.generic_visit(node)

        def visit_Import(self, node: "ast.Import") -> None:
            for alias in node.names:
                _record(alias.asname or alias.name.split(".")[0])

        def visit_ImportFrom(self, node: "ast.ImportFrom") -> None:
            for alias in node.names:
                _record(alias.asname or alias.name)

        def visit_Global(self, node: "ast.Global") -> None:
            global_or_nonlocal.update(node.names)

        def visit_Nonlocal(self, node: "ast.Nonlocal") -> None:
            global_or_nonlocal.update(node.names)

        def visit_Match(self, node: "ast.Match") -> None:
            for case in node.cases:
                for name in _match_pattern_capture_names(case.pattern):
                    _record(name)
            # Pattern identifiers are plain `str`, never `ast.Name`, so
            # generic_visit re-walking the patterns cannot double-count;
            # this still normally walks each case's own guard + body.
            self.generic_visit(node)

        def visit_Name(self, node: "ast.Name") -> None:
            if isinstance(node.ctx, ast.Store):
                _record(node.id)

    visitor = _ScopeBindingVisitor()
    for stmt in scope_body:
        visitor.visit(stmt)
    return counts, sole_assign_values, global_or_nonlocal


def _param_names(func_node: "ast.FunctionDef | ast.AsyncFunctionDef") -> "set[str]":
    """Every parameter name `func_node`'s own signature introduces into its
    local scope -- positional-only, positional-or-keyword, keyword-only,
    `*args`, `**kwargs`, with or without a default value. `ast.arg` is
    never an `ast.Name` node, so no Store-context Name walk can ever see a
    parameter on its own; `sorted=set` as a default parameter value is
    otherwise structurally invisible."""
    args = func_node.args
    names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg is not None:
        names.add(args.vararg.arg)
    if args.kwarg is not None:
        names.add(args.kwarg.arg)
    return names


# ---------------------------------------------------------------------------
# 4b. The canonical FROZEN_INPUT_SPECS key-projection shape (requirement 2).
# ---------------------------------------------------------------------------


def _subscript_zero_index(slice_node: "ast.AST") -> bool:
    """True iff `slice_node` (a Subscript's own `.slice`) denotes the
    literal integer `0` -- handles both the 3.9+ shape (`.slice` is the
    index expression itself) and the pre-3.9 `ast.Index(...)` wrapper still
    present as an AST class for compatibility. Explicitly excludes `False`:
    `False == 0` in Python, so a bare `node.value == 0` check alone would
    wrongly accept `spec[False]`."""
    node = slice_node
    if isinstance(node, ast.Index):  # pragma: no cover -- pre-3.9 shape only
        node = getattr(node, "value", node)
    return (
        isinstance(node, ast.Constant)
        and not isinstance(node.value, bool)
        and node.value == 0
    )


def _is_canonical_spec_keys_projection(value: "ast.AST") -> bool:
    """True iff `value` is structurally EXACTLY one of the two forms the
    real, shipped guards use to project "the key" out of every
    FROZEN_INPUT_SPECS entry -- a single-generator `ListComp` over the bare
    Name `FROZEN_INPUT_SPECS`, with no `if` filter and no second `for`
    clause, whose element expression is either:

      Form A (suspicion_scan.py, and skeptic_setup.py's two guards -- all
      three spelled identically): `X[0] for X in FROZEN_INPUT_SPECS` -- the
      generator's target is a single bare Name, and the element is that
      SAME name subscripted by the literal `0`.

      Form B (skeptic_ready.py -- verified against the real source before
      writing this check, not assumed identical to the other three): `X
      for X, _, _ in FROZEN_INPUT_SPECS` -- the generator's target is a
      Tuple of EXACTLY THREE bare Names (a full destructuring unpack of
      each 3-tuple entry -- FROZEN_INPUT_SPECS entries are `(key, label,
      stamp_field)` 3-tuples, per skeptic_constants.py's own definition, so
      a 2-name or 4+-name unpack target is not this shape at all, whatever
      it happens to project), and the element is bare the tuple target's
      OWN first Name.

    Both forms project the identical value -- FROZEN_INPUT_SPECS entries
    are `(key, label, stamp_field)` 3-tuples, per skeptic_constants.py's
    own definition -- as two equally direct spellings of "the first field
    of each entry", not two different things one of which is "more
    correct". Treating only Form A as canonical would false-reject
    skeptic_ready.py's own real, already-shipped guard.

    Deliberately alpha-equivalent, not spelling-pinned: the loop
    variable's own name carries no semantic weight (renaming `spec` to `s`
    throughout does not change what the comprehension computes), so this
    accepts ANY name if used consistently between the generator target and
    the element -- not just the literal spellings (`spec`/`key`) the four
    real sites happen to use today.

    This is a whole-expression STRUCTURAL check, not a membership/contains
    check: an expression that merely references the name
    `FROZEN_INPUT_SPECS` somewhere in its subtree (`[spec[1] for spec in
    FROZEN_INPUT_SPECS]` -- the label, not the key; `alias if
    FROZEN_INPUT_SPECS else [...]` -- a third-name alias) does NOT qualify
    just because the name appears. Membership-style matching is exactly
    what let a label-projection and a third-name alias both slip through
    an earlier version of this file."""
    if not isinstance(value, ast.ListComp):
        return False
    if len(value.generators) != 1:
        return False
    generator = value.generators[0]
    if generator.ifs or generator.is_async:
        return False
    if not (
        isinstance(generator.iter, ast.Name) and generator.iter.id == "FROZEN_INPUT_SPECS"
    ):
        return False

    target = generator.target
    elt = value.elt

    if isinstance(target, ast.Name):  # Form A: X[0] for X in FROZEN_INPUT_SPECS
        return (
            isinstance(elt, ast.Subscript)
            and isinstance(elt.value, ast.Name)
            and elt.value.id == target.id
            and _subscript_zero_index(elt.slice)
        )

    if isinstance(target, ast.Tuple):  # Form B: X for X, _, _ in FROZEN_INPUT_SPECS
        elts = target.elts
        return (
            len(elts) == 3
            and all(isinstance(e, ast.Name) for e in elts)
            and isinstance(elt, ast.Name)
            and isinstance(elts[0], ast.Name)
            and elt.id == elts[0].id
        )

    return False


# ---------------------------------------------------------------------------
# 4c. The `if sorted(X) != sorted(Y):` comparison shape (requirement 1).
# ---------------------------------------------------------------------------


def _bare_name_sorted_call(node: "ast.AST") -> "ast.Name | None":
    """Returns the argument Name node iff `node` is exactly `sorted(<bare
    name>)`: a Call to the bare name `sorted`, exactly one positional
    argument, no keyword arguments, and that argument itself a bare Name
    (excludes a nested call like `sorted(set(x))`, a literal like
    `sorted([1])`, and a starred arg like `sorted(*x)`). Returns None for
    anything else."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "sorted"
    ):
        return None
    if len(node.args) != 1 or node.keywords:
        return None
    arg = node.args[0]
    return arg if isinstance(arg, ast.Name) else None


def _sorted_mismatch_operand_names(test: "ast.AST") -> "tuple[str, str] | None":
    """If `test` is exactly `sorted(X) != sorted(Y)` for two bare-Name
    operands (a `Compare` with a single `!=` and both sides a bare
    `sorted(<Name>)` call), returns `(X.id, Y.id)` in the order written.
    Returns None for anything else -- a `set(...)`/`len(...)` comparison, a
    hoisted `if _flag:` (a bare Name test is not this shape at all, and is
    rejected outright rather than resolved -- this file has never chased a
    flag back to its own assignment), a nested-call or literal `sorted()`
    argument, a self-comparison, ...."""
    if not isinstance(test, ast.Compare):
        return None
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.NotEq):
        return None
    if len(test.comparators) != 1:
        return None
    left = _bare_name_sorted_call(test.left)
    right = _bare_name_sorted_call(test.comparators[0])
    if left is None or right is None:
        return None
    return (left.id, right.id)


# ---------------------------------------------------------------------------
# 4d. The raise (requirements 5/6).
# ---------------------------------------------------------------------------


def _is_assertion_error_raise(exc: "ast.AST | None") -> bool:
    """True iff `exc` (a Raise node's own `.exc`) names AssertionError
    directly -- `raise AssertionError(...)` (a Call whose callee is the
    bare Name `AssertionError`) or the bare `raise AssertionError`. Any
    other exception -- including a plausible-looking custom subclass, or an
    unrelated builtin like RuntimeError -- fails: a `raise
    RuntimeError(anchor_phrase)` guard is not the fail-closed contract
    every real site ships, no matter how the message reads."""
    if isinstance(exc, ast.Call):
        return isinstance(exc.func, ast.Name) and exc.func.id == "AssertionError"
    return isinstance(exc, ast.Name) and exc.id == "AssertionError"


def _sole_bare_name_argument(exc: "ast.AST | None") -> "str | None":
    """If `exc` is a bare Name, or a Call with exactly one positional
    bare-Name argument and no keywords, returns that Name's `.id` -- the
    message-holding variable a `raise AssertionError(message)` form
    passes. Returns None for anything else (a literal message built
    inline, a multi-argument call, a keyword argument, ...)."""
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Call) and len(exc.args) == 1 and not exc.keywords:
        arg = exc.args[0]
        if isinstance(arg, ast.Name):
            return arg.id
    return None


def _string_constants_contain_anchor(node: "ast.AST") -> bool:
    return any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) and ANCHOR_PHRASE in n.value
        for n in ast.walk(node)
    )


def _plain_string_constant_value(value: "ast.AST") -> "str | None":
    """Returns the literal string `value` denotes iff `value` is a PLAIN
    string-literal expression: an `ast.Constant` of type `str` (adjacent
    string literals -- `"a" "b"` -- are already merged into a single
    Constant by the parser, so no separate concatenation handling is
    needed here), or an f-string (`ast.JoinedStr`) every one of whose parts
    is itself a constant string -- any interpolated `ast.FormattedValue`
    part disqualifies the whole expression. Returns None for anything
    else: a conditional (`IfExp`), a call, a subscript, a bare Name, a
    binary op, an f-string with any interpolated part, .... This is what
    makes a decoy like `message = ANCHOR_PHRASE if False else "wrong"` get
    REJECTED: walking the assigned expression's whole subtree for a
    constant that merely CONTAINS the anchor phrase somewhere would find
    it inside the dead `if False` branch even though the runtime value of
    that expression is always `"wrong"` -- restricting to a plain string
    form first, before ever looking for the phrase, is what closes that
    gap."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.JoinedStr):
        parts: "list[str]" = []
        for part in value.values:
            if not (isinstance(part, ast.Constant) and isinstance(part.value, str)):
                return None
            parts.append(part.value)
        return "".join(parts)
    return None


def _if_body_sole_preceding_message_value(
    if_node: "ast.If", raise_index: int, name: str
) -> "ast.AST | None":
    """Returns the `.value` of `name`'s message-carrying assignment iff ALL
    of: `name` is bound EXACTLY ONCE anywhere in `if_node.body`'s own
    flattened scope (an intervening rebind ANYWHERE in that scope -- even
    one nested inside a `for`/`if`/`while` the direct body does not
    literally contain as a sibling -- disqualifies the message
    unconditionally, not just a same-level reassignment); AND that sole
    binding is a plain `name = <value>` Assign that is ALSO the DIRECT
    statement of `if_node.body` IMMEDIATELY PRECEDING `raise_index` --
    `if_node.body[raise_index - 1]` exactly, not merely some earlier
    statement in the body. Returns None otherwise.

    Both conditions matter independently, against two distinct decoys:
    without the flattened-scope rebind count, `message = "...anchor...";
    for _ in (0,): message = "wrong"; raise AssertionError(message)` would
    still resolve to the anchor-carrying value even though `message` is NOT
    what the raise actually sees at runtime -- the `for` loop's own
    reassignment lives one level deeper than a same-level scan alone would
    ever look. And without the immediate-adjacency requirement, an
    intervening same-level statement that does not rebind `name` at all --
    e.g. `message = "...anchor..."; _ = 0; raise AssertionError(message)`
    -- would still resolve to the anchor-carrying value even though the
    assignment is not the last thing the if-body does before the raise;
    pinning the check to exactly `raise_index - 1` is what makes
    "immediately preceding" mean what it says, rather than merely
    "somewhere earlier in the same body"."""
    counts, _sole, _global_nonlocal = _scope_binding_info(if_node.body)
    if counts.get(name, 0) != 1:
        return None
    message_index = raise_index - 1
    if message_index < 0:
        return None
    stmt = if_node.body[message_index]
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.targets[0].id == name
    ):
        return stmt.value
    return None


def _guard_raise_is_qualifying(if_node: "ast.If") -> bool:
    """True iff `if_node.body` contains, as a DIRECT statement (not nested
    inside any further loop/conditional/with/try the body contains), a
    `raise AssertionError(...)` whose own exception value carries
    `ANCHOR_PHRASE` -- either as a string literal anywhere in the Raise's
    own subtree, or (per `_if_body_sole_preceding_message_value` and
    `_plain_string_constant_value`) via a message Name resolved back to
    its sole, immediately preceding, same-level if-body assignment WHOSE
    OWN VALUE is itself a plain string-literal expression -- never a
    conditional, call, subscript, or any other non-constant expression. A
    decoy sitting in a dead branch, e.g. `message = ANCHOR_PHRASE if False
    else "wrong"`, does not qualify just because the phrase appears
    somewhere in the assigned expression's subtree."""
    for index, stmt in enumerate(if_node.body):
        if not isinstance(stmt, ast.Raise):
            continue
        if not _is_assertion_error_raise(stmt.exc):
            continue
        if _string_constants_contain_anchor(stmt):
            return True
        message_name = _sole_bare_name_argument(stmt.exc)
        if message_name is None:
            continue
        value = _if_body_sole_preceding_message_value(if_node, index, message_name)
        if value is None:
            continue
        plain_string = _plain_string_constant_value(value)
        if plain_string is not None and ANCHOR_PHRASE in plain_string:
            return True
    return False


# ---------------------------------------------------------------------------
# 4e. Tying the operand shape to the canonical projection (requirements
#     2/3), and the `sorted`-shadow check (requirement 4).
# ---------------------------------------------------------------------------


def _spec_operand_name(
    operand_names: "tuple[str, str]",
    owner_scope_body: "list[ast.stmt]",
    owner_node: "ast.FunctionDef | ast.AsyncFunctionDef | None",
) -> "str | None":
    """Returns the one operand name that qualifies as the
    FROZEN_INPUT_SPECS key-projection operand -- bound as its scope's SOLE
    binding (of ANY kind -- also not a parameter, also not declared
    global/nonlocal: either makes this NOT its sole binding even when
    there is exactly one plain Assign) by a plain `name = <value>` Assign
    whose value is `_is_canonical_spec_keys_projection`. Returns None
    unless EXACTLY ONE of the two operand names qualifies -- if neither
    does, or (a pathological case) both independently do, that is not
    "the" spec operand and this guard does not qualify. Also returns None
    outright if the two operand names are identical (not a real two-sided
    comparison at all)."""
    if operand_names[0] == operand_names[1]:
        return None
    counts, sole_assign_values, global_or_nonlocal = _scope_binding_info(owner_scope_body)
    owner_params = _param_names(owner_node) if owner_node is not None else set()
    qualifying = [
        name
        for name in operand_names
        if name not in global_or_nonlocal
        and name not in owner_params
        and counts.get(name) == 1
        and (assign_value := sole_assign_values.get(name)) is not None
        and _is_canonical_spec_keys_projection(assign_value)
    ]
    return qualifying[0] if len(qualifying) == 1 else None


def _sorted_is_unshadowed(
    owner_node: "ast.FunctionDef | ast.AsyncFunctionDef | None", module_tree: "ast.Module"
) -> bool:
    """True iff the builtin name `sorted` is bound by NO binding form
    (assignment, def, class, parameter, walrus, `for`/`with`/`except`/
    `import ... as`, global/nonlocal, match-capture) anywhere in the
    guard's own owning scope, OR at module level in this same file --
    `sorted = set` (restoring duplicate-key collapse) while the accepted
    `sorted(X) != sorted(Y)` AST shape stays untouched is exactly the
    defect this closes."""
    owner_scope_body = owner_node.body if owner_node is not None else module_tree.body
    owner_counts, _sole, owner_global_nonlocal = _scope_binding_info(owner_scope_body)
    if owner_counts.get("sorted", 0) > 0:
        return False
    if "sorted" in owner_global_nonlocal:
        return False
    if owner_node is not None and "sorted" in _param_names(owner_node):
        return False
    module_counts, _sole2, _global2 = _scope_binding_info(module_tree.body)
    return module_counts.get("sorted", 0) == 0


# ---------------------------------------------------------------------------
# 4f. Owner identity (requirement 7) and the full per-guard verdict.
# ---------------------------------------------------------------------------


def _scan_file(
    tree: "ast.Module",
) -> "tuple[dict[str, list], list[tuple]]":
    """Single walk over `tree`'s whole subtree. Returns `(module_level_defs,
    if_hits)`:

    `module_level_defs`: `dict[name -> list[FunctionDef/AsyncFunctionDef]]`
    of every def whose own lexical position is DIRECTLY the module scope --
    reachable only through control-flow (If/For/While/Try/With/Match) that
    does not itself introduce a scope, never through a ClassDef or another
    FunctionDef/AsyncFunctionDef. `def run():` sitting inside `if False:`
    at module level is still module-level by this definition -- Python's
    `if` does not create a new scope -- which is deliberate: requirement 7
    needs exactly this to report a dead, guarded `if False: def run():
    <guard>` sitting ALONGSIDE a real, unguarded `def run()` as an
    AMBIGUOUS pair of same-named module-level defs, rather than silently
    preferring whichever copy happens to carry the guard.

    `if_hits`: `list[(owner_or_None, ast.If)]` for every `ast.If` anywhere
    in the file, tagged with its nearest lexically-enclosing FunctionDef/
    AsyncFunctionDef (by object identity), or `None` if it sits directly at
    module level or directly inside a class body outside any method. A
    `ClassDef` resets attribution to `None`: a class body's own top-level
    statements execute in the class's own namespace at class-definition
    time, not as part of any lexically enclosing function's runtime scope,
    so a guard relocated into a class nested inside its owning function
    must not be misattributed to that function."""
    module_level_defs: "dict[str, list]" = {}
    if_hits: "list[tuple]" = []

    def _walk(node: "ast.AST", owner, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if owner is None and not in_class:
                    module_level_defs.setdefault(child.name, []).append(child)
                next_owner, next_in_class = child, in_class
            elif isinstance(child, ast.ClassDef):
                next_owner, next_in_class = None, True
            else:
                next_owner, next_in_class = owner, in_class
            if isinstance(child, ast.If):
                if_hits.append((owner, child))
            _walk(child, next_owner, next_in_class)

    _walk(tree, None, False)
    return module_level_defs, if_hits


def _site_key(filename: str, owner_node, module_level_defs: "dict[str, list]") -> "tuple[str, str]":
    """The `(file, label)` key used to compare a found guard's location
    against `EXPECTED_GUARD_SITES`. Only a guard whose owner is
    IDENTITY-verified as the UNIQUE module-level def of its own bare name
    gets the plain `(filename, name)` key that can equal an expected site
    -- a class method or nested function sharing that bare name, an
    AMBIGUOUS name with 2+ module-level defs, or a guard with no owning def
    at all, each get a label that can never equal any `EXPECTED_GUARD_SITES`
    entry, so they always surface as 'unexpected' rather than silently
    masquerading as (or silently satisfying) a real site."""
    if owner_node is None:
        return (filename, "<module level, no owning function>")
    candidates = module_level_defs.get(owner_node.name, [])
    if len(candidates) == 1 and candidates[0] is owner_node:
        return (filename, owner_node.name)
    return (filename, f"<{owner_node.name!r} is not a unique module-level function>")


def _guard_qualification(
    if_node: "ast.If", owner_node, module_tree: "ast.Module"
) -> "str | None":
    """Returns `None` if `if_node` fully qualifies as one of the four
    canonical guards. Otherwise returns a short, human-readable reason it
    was rejected. Only ever called on an `if` whose test already has the
    `sorted(X) != sorted(Y)` comparison shape (see the caller in the test
    function below), so the reasons here are about WHICH further allowlist
    requirement failed, not whether this looked like an attempted guard at
    all."""
    operand_names = _sorted_mismatch_operand_names(if_node.test)
    if operand_names is None:
        return "test is not `sorted(X) != sorted(Y)` with bare-Name operands"
    if not _guard_raise_is_qualifying(if_node):
        return (
            f"body does not directly raise AssertionError carrying {ANCHOR_PHRASE!r}, "
            "either literally or via a message name bound exactly once, "
            "immediately before the raise"
        )
    owner_scope_body = owner_node.body if owner_node is not None else module_tree.body
    if _spec_operand_name(operand_names, owner_scope_body, owner_node) is None:
        return (
            "neither (or both) of the compared operands is bound, as its "
            "scope's sole binding, to the canonical FROZEN_INPUT_SPECS "
            "key-projection comprehension"
        )
    if not _sorted_is_unshadowed(owner_node, module_tree):
        return "the builtin `sorted` is shadowed in the guard's owning or module scope"
    return None


def test_frozen_input_key_mismatch_guards_match_canonical_allowlisted_shape():
    """AST-driven ALLOWLIST check across every FROZEN_INPUT_SPECS
    key-mismatch guard shipped anywhere under `SCRIPTS_DIR`. See the
    section-4 comment block above for the full allowlist this implements,
    why it replaced a six-round denylist, and what it deliberately does
    NOT prove (reachability/dominance; an unguarded fifth consumer; a
    rebind through `builtins`/`globals()`/a third-name alias chain).

    This proves each of the four `EXPECTED_GUARD_SITES` obligations --
    `compute_producer_input_digest()` (suspicion_scan.py),
    `compute_skeptic_input_digest()` and `run()` (both skeptic_setup.py),
    `frozen_input_check()` (skeptic_ready.py) -- independently carries a
    guard STRUCTURALLY IDENTICAL to the canonical shape: `if sorted(X) !=
    sorted(Y):` where one of X/Y is bound, as its owning scope's sole
    binding, to one of the two canonical FROZEN_INPUT_SPECS
    key-projection comprehensions those sites actually ship, `sorted`
    itself is unshadowed anywhere reachable, and the body directly raises
    `AssertionError` carrying the anchor phrase. The file set scanned is
    DERIVED (every `*.py` directly under `SCRIPTS_DIR`, non-recursive --
    no shipped script lives in a nested subdirectory today) rather than
    hand-typed, so a fifth guard landing in a fifth script is found
    automatically; what remains hand-maintained, deliberately, is
    `EXPECTED_GUARD_SITES` itself -- there is no way to derive "which four
    functions SHOULD own a guard" from the source."""
    all_hits: "list[tuple[str, tuple[str, str], ast.If]]" = []
    near_misses: "list[str]" = []
    ambiguous_owners: "list[tuple[str, str, int]]" = []

    for script_path in sorted(SCRIPTS_DIR.glob("*.py")):
        tree = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
        module_level_defs, if_hits = _scan_file(tree)
        filename = script_path.name

        for site_file, site_function in EXPECTED_GUARD_SITES:
            if site_file != filename:
                continue
            candidates = module_level_defs.get(site_function, [])
            if len(candidates) > 1:
                ambiguous_owners.append((filename, site_function, len(candidates)))

        for owner_node, if_node in if_hits:
            if _sorted_mismatch_operand_names(if_node.test) is None:
                continue  # not even attempting the shape -- not a candidate at all
            reason = _guard_qualification(if_node, owner_node, tree)
            if reason is None:
                site = _site_key(filename, owner_node, module_level_defs)
                all_hits.append((filename, site, if_node))
            else:
                owner_label = owner_node.name if owner_node is not None else "<module level>"
                near_misses.append(
                    f"{filename}::{owner_label}: `{ast.unparse(if_node.test)}` -- {reason}"
                )

    assert not ambiguous_owners, (
        "an EXPECTED_GUARD_SITES function name resolves to more than one "
        "module-level def in the same file (e.g. a dead `if False: def "
        "run(): ...` sitting alongside a real `def run()`):\n"
        + "\n".join(
            f"  {file}::{func}: {count} module-level defs"
            for file, func, count in ambiguous_owners
        )
    )

    found_sites = {hit[1] for hit in all_hits}
    expected_sites = frozenset(EXPECTED_GUARD_SITES)
    near_miss_context = (
        "\nnear-miss candidates (right `sorted(X) != sorted(Y)` shape, "
        "rejected on another ground):\n" + "\n".join(f"  {m}" for m in near_misses)
        if near_misses
        else ""
    )

    assert len(all_hits) == 4, (
        "expected exactly 4 FROZEN_INPUT_SPECS key-mismatch guards across "
        f"every *.py file under {SCRIPTS_DIR}, found {len(all_hits)}:\n"
        + "\n".join(f"  {file}::{site}" for file, site, _if_node in all_hits)
        + near_miss_context
    )
    assert found_sites == expected_sites, (
        "FROZEN_INPUT_SPECS key-mismatch guards were not found at exactly "
        "the expected (file, function) locations -- a guard's `if` may have "
        "relocated out of its owning function, or a new one landed "
        "somewhere unexpected:\n"
        f"  missing:    {sorted(expected_sites - found_sites)}\n"
        f"  unexpected: {sorted(found_sites - expected_sites)}"
        + near_miss_context
    )
