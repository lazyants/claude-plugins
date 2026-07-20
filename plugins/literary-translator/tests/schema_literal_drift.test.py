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
# 4. The hand-duplicated FROZEN_INPUT_SPECS key-mismatch guards (round 11,
#    #243) -- a STATIC, structural sibling-consistency check, not a
#    behavioral one.
# ---------------------------------------------------------------------------
#
# `compute_producer_input_digest()` (suspicion_scan.py),
# `compute_skeptic_input_digest()` (skeptic_setup.py), `run()`'s own
# `_frozen_input_snapshots_by_key` stamping block (skeptic_setup.py, added
# as defense-in-depth), and `frozen_input_check()`'s own `paths` dict
# (skeptic_ready.py, the verifier side) each carry their OWN hand-typed
# copy of the identical `sorted(snapshot_keys) != sorted(spec_keys)`
# comparison -- round 11's fix for the SET-collapses-duplicates gap codex
# found (see tests/suspicion_scan.test.py's and
# tests/skeptic_setup.test.py's own `duplicate_key_entry`/
# `same_count_key_swap` cases for the runtime, call-driven proof that this
# shape is non-vacuous).
#
# Round 12 (#243): this section originally scanned only
# suspicion_scan.py/skeptic_setup.py by NAME and asserted a hard-coded
# count of 3 -- accurate when written, but skeptic_ready.py's own copy of
# this exact guard (its `paths`/`frozen_input_check()` block) landed in
# the SAME round, concurrently, in a file this section never looked at.
# The count-of-3 assertion stayed green with the fourth guard entirely
# outside its view -- a hard-coded FILE LIST has the identical failure
# shape a hard-coded field/key LIST has everywhere else in this file: it
# is correct until something new lands somewhere it never looked. Fixed
# by deriving the file SET to scan (every `*.py` directly under
# `SCRIPTS_DIR` -- the real shipped script directory, not a restated
# subset of it) rather than naming files one at a time; a fifth guard
# landing in a fifth script is now found automatically.
#
# Round 13 (#243): the round-12 version located a guard purely by REGEX --
# find the literal anchor phrase "FROZEN_INPUT_SPECS contains a duplicate
# key" in the exception prose, then associate it with the NEAREST
# PRECEDING `if sorted|set|len(...):` line ANYWHERE EARLIER IN THE FILE,
# with no binding to the same `if`, block, or function. skeptic_setup.py's
# `run()` guard was hoisted to
#     _keys_mismatch = set(_frozen_input_snapshots_by_key) != set(_spec_keys)
#     if _keys_mismatch:
# -- `if _keys_mismatch:` no longer matched the condition-shape regex at
# all, so the anchor silently rebound to `compute_skeptic_input_digest()`'s
# OWN unrelated guard earlier in the same file. The count stayed 4, every
# reported shape stayed `sorted(...) != sorted(...)`, and the test stayed
# GREEN with a weakened `set(...)`-based guard live in `run()`. The
# literal `4` was counting anchor-phrase MESSAGES, not distinct protected
# `if` sites -- a regex has no notion of "which `if` does this belong to",
# so it can't be blamed for silently reattaching one string to a totally
# different piece of code once the two drift apart lexically.
#
# Fixed (round 13, first pass) by switching from regex to a real AST walk:
# this module parses each scanned file, tracks which named function most
# closely encloses each `if`, and binds `EXPECTED_GUARD_SITES` to exact
# (file, function) identities rather than a scalar count. That first pass
# also tried to RESOLVE a hoisted `_flag = <expr>; if _flag:` back to
# `<expr>` (one level, via "nearest preceding in-scope assignment"), on
# the reasoning that hoisting itself isn't unsafe -- only a weakened
# comparison hidden behind it would be.
#
# Round 13 (codex round-13 review, second pass): that resolver was itself
# unsound. Its candidate list came from `_statements_in_scope()`'s
# traversal order (breadth-first, via a stack), not source-line order, so
# "nearest preceding" picked the last-TRAVERSED assignment, not the last
# assignment before the `if` in program order -- an earlier assignment
# inside a conditional branch could out-rank a later, unconditional one,
# and the resolver had no notion of reachability or scope-exit: a guard
# placed after a `return`, or relocated into a nested class body, resolved
# and passed exactly as if it still guarded its intended access. A SOUND
# version needs real reaching-definition analysis, not a line-number
# heuristic over an unordered candidate list -- disproportionate for what
# this test protects (four guards, all of which are already written as
# direct comparisons).
#
# So this version REJECTS hoisting outright instead: `if_node.test` must
# itself be the `sorted(X) != sorted(Y)` comparison, found directly at the
# guard. A bare-name test (`if _flag:`) is treated as an unsupported form
# -- not resolved, not silently passed -- and its failure message says so
# plainly ("hoisted form ... is not supported ... write the comparison
# directly at the guard") rather than implying the guard is malformed.
# Nothing real is lost: all four shipped guards are, and have always been,
# direct comparisons -- the round-13 hoist was never a legitimate shape
# this codebase needed, only the attack that broke the prior regex-based
# locator.
#
# Finding 1 (codex round 13, same review): the shape check itself only
# confirmed both `sorted()` callees were named `sorted` -- it never
# inspected their arguments, so `sorted(set(X)) != sorted(set(Y))`
# (duplicate-collapse is back), `sorted(X) != sorted(X)` (never fires),
# and `sorted([]) != sorted([1])` (always fires) all passed. Fixed by
# requiring each `sorted()` call take exactly one bare-Name positional
# argument (no nested call, no literal, no starred/keyword arg) AND by
# binding `EXPECTED_GUARD_SITES` to each site's own specific two operand
# names (order-insensitive, since `!=` is symmetric), not just "any two
# sorted() calls" -- a guard with the right shape but the WRONG pair of
# names (e.g. copy-pasted from a sibling site without updating one
# operand) is exactly as real a defect as a weakened comparison operator.
#
# The file SET being scanned is still DERIVED (every `*.py` directly under
# `SCRIPTS_DIR`, non-recursive) for the same round-12 reason: a hand-typed
# file list is the restatement shape this section exists to avoid.
# Non-recursive is a real, currently-harmless boundary: no shipped script
# lives in a nested subdirectory of `SCRIPTS_DIR` today, so `glob("*.py")`
# sees every real guard; if a future script were ever nested there, this
# scan (like the round-12 one before it) would need to switch to
# `rglob("*.py")` to keep seeing it -- left non-recursive here rather than
# pre-emptively widened, since a recursive glob over a directory that may
# one day gain non-shipped scratch subdirectories has its own
# false-positive risk this codebase doesn't need yet.
#
# Behavioral (runtime, call-driven) coverage of these four guards is
# UNEVEN, and this file states exactly where the line falls rather than
# implying an even coat: `suspicion_scan.test.py`'s own
# `test_producer_input_digest_fails_closed_on_frozen_input_specs_key_mismatch`
# and `skeptic_setup.test.py`'s own
# `test_skeptic_input_digest_fails_closed_on_frozen_input_specs_key_mismatch`
# each drive `compute_producer_input_digest()`/`compute_skeptic_input_digest()`
# directly, parametrized over `duplicate_key_entry`/`same_count_key_swap`,
# and prove those two guards fire on a real mismatch.
# `skeptic_ready.test.py`'s
# `test_check_frozen_inputs_fails_closed_on_frozen_input_specs_key_mismatch`
# does the same for `frozen_input_check()`, driving it through
# `skeptic_ready.py`'s real `--verify-merged`/`--check-frozen-inputs` CLI
# path -- this file does not re-describe that test, only relies on its
# existence rather than claiming (as an earlier round wrongly did) that no
# such runtime test is possible.
#
# `run()`'s own copy (skeptic_setup.py) has NO behavioral test anywhere in
# this suite -- confirmed by grep, not assumed: no test references `run(`
# or `_frozen_input_snapshots_by_key` at all. `run()`'s guard follows
# `compute_producer_input_digest()`/`compute_skeptic_input_digest()`,
# called earlier in the SAME `run()` invocation against the SAME
# `FROZEN_INPUT_SPECS` and the SAME captured canon/manifest/senses state,
# so BY CONSTRUCTION the identical boolean already evaluated False twice
# before control reaches it -- but that is an argument about the shape of
# the code, not a runtime test, and it says nothing about whether `run()`'s
# own guard is reachable or fires correctly if that upstream argument ever
# stops holding (a future reordering, a second caller, ...). For `run()`
# specifically, this static AST check IS the only guard-related coverage
# that exists.
#
# What IS additionally provided here, and actually targets these guards'
# real risk (a hand-duplicated fix silently drifting -- or being silently
# MISATTRIBUTED, per round 13 -- in exactly one of its four copies): a
# STATIC, AST-driven check that all four (file, function) obligations
# still carry a guard whose `if` test IS, directly, the identical
# non-deduplicated sorted-list shape over that site's own two operands,
# attributed to the correct owning function -- or, for a guard relocated
# into a nested class body, correctly reported as no longer owned by its
# former function at all. `_find_guard_ifs` resets ownership to `None` at
# every `ClassDef`, closing the second half of codex's finding 2: without
# that reset, a guard moved into a class nested inside its owning
# function (verified on disk before this fix) stayed misattributed to
# that function, so the relocation was invisible here.
#
# What this file does NOT prove, and does not claim to: that a guard's
# `if` DOMINATES the protected access at runtime. A guard of the exact
# required shape, in the exact expected function, sitting immediately
# after an unconditional `return` (or any other control-flow path that
# skips it) passes every check here while never executing -- codex's
# finding 2 named this half explicitly too, and it is still open.
# A sound fix needs real reaching-definition/dominance analysis, the same
# class of disproportionate machinery this file already declined to build
# for the hoist resolver above, for the same reason: none of the four
# shipped guards needs it today. A partial positional heuristic ("the
# `if` appears before the first use of the protected mapping") was
# considered and rejected -- it would not catch the return-before-guard
# case at all, while reading as if it did, which is worse than not
# checking. Left undone deliberately: this static check proves shape,
# operands, and ownership identity, for all four sites equally -- it does
# not, and cannot, prove reachability, for any of them, equally. The three
# sites with a behavioral test (named above) additionally have empirical
# proof that their guard fires under the SPECIFIC mismatches those tests
# construct -- not a general reachability proof, but real evidence beyond
# what this file provides. `run()` has neither: no behavioral test AND no
# reachability proof, so a guard placed after a `return` (or any other
# dead-code relocation) in `run()` specifically would be caught by
# NOTHING in this suite.


ANCHOR_PHRASE = "FROZEN_INPUT_SPECS contains a duplicate key"

# The four (file, function) semantic obligations codex asked this test to
# bind to directly, by AST location, rather than trusting a scalar count.
# This is deliberately still a literal, hand-maintained mapping -- exactly
# like section 1's literal `4` -- because there is no honest way to derive
# "which four functions SHOULD own a guard" from the source itself; a
# guard silently vanishing, or reappearing somewhere else, must fail LOUD
# via an identity mismatch below, not be absorbed by an unbounded count.
#
# Each site also maps to its own two hand-typed operand names
# (order-insensitive -- `!=` is symmetric, and nothing about this check
# depends on which side a given site happens to write first): the
# snapshot-keys/paths-dict name on one side, the FROZEN_INPUT_SPECS-derived
# key-list name on the other. Finding 1 (codex round 13): a guard that
# resolves to `sorted(X) != sorted(Y)` syntactically but with the WRONG
# pair of names -- e.g. copy-pasted from a sibling site without updating
# one operand -- is exactly as real a defect as a `set(...)`-based guard,
# so the shape check below is bound to this site-specific pair, not just
# to "some two sorted() calls".
EXPECTED_GUARD_SITES = {
    ("suspicion_scan.py", "compute_producer_input_digest"): (
        "frozen_input_snapshots",
        "spec_keys",
    ),
    ("skeptic_setup.py", "compute_skeptic_input_digest"): (
        "frozen_input_snapshots",
        "spec_keys",
    ),
    ("skeptic_setup.py", "run"): (
        "_frozen_input_snapshots_by_key",
        "_spec_keys",
    ),
    ("skeptic_ready.py", "frozen_input_check"): (
        "paths",
        "_spec_keys",
    ),
}


def _site_sort_key(site: tuple) -> tuple:
    """Sort key for a `(file, function)` site tuple whose `function` may be
    `None` (a guard found outside any named function) -- plain `sorted()`
    would raise `TypeError` comparing `None` to `str` the moment such a
    site shows up in a failure message, which is exactly the moment a
    readable message matters most."""
    file_name, function_name = site
    return (file_name, function_name if function_name is not None else "")


def _if_carries_anchor_raise(if_node: ast.If) -> bool:
    """True if `if_node`'s own body (not its `elif`/`else`) both raises
    AND carries the round-11 anchor phrase somewhere in a string literal
    within that raise -- checked as two independent facts over the whole
    body subtree, not "the Raise's first argument is this exact Constant",
    so a message built via an intermediate variable, or split across an
    f-string's own literal pieces (skeptic_ready.py's copy runs the phrase
    inline after "...or " within a longer string, not on its own line),
    is still found. `ast.walk` already descends into an f-string's
    `JoinedStr.values`, so no separate f-string-specific handling is
    needed here -- every literal piece surfaces as its own `Constant`."""
    body_nodes = [n for stmt in if_node.body for n in ast.walk(stmt)]
    has_raise = any(isinstance(n, ast.Raise) for n in body_nodes)
    has_anchor = any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) and ANCHOR_PHRASE in n.value
        for n in body_nodes
    )
    return has_raise and has_anchor


def _find_guard_ifs(node: ast.AST, current_func, hits: list) -> None:
    """Recursively walks `node`'s children, tracking which named
    `FunctionDef`/`AsyncFunctionDef` (module-level `None` if none) most
    closely encloses each `If` -- deliberately NOT a blind `ast.walk`,
    which has no notion of "current enclosing function" and would
    misattribute a nested def's own guard to its outer function (or a
    guard that moved OUT of its owning function into a sibling function,
    the exact drift shape this test must still catch). Appends
    `(current_func, if_node)` for every `If` whose body
    `_if_carries_anchor_raise`.

    A `ClassDef` also resets `current_func` to `None`, the same as
    module level: codex round 13's finding 2 named this as a second,
    separate ownership defect from the hoist -- without this reset, a
    guard relocated directly into a class body nested inside its owning
    function (executed at class-definition time, not as part of the
    function's own control flow) stayed attributed to that outer
    function, so the relocation was invisible to `found_sites` below.
    Verified on disk before this fix: a guard moved into
    `class _Inner:` nested inside `run()` was reported as owned by
    `run` itself. A `FunctionDef`/`AsyncFunctionDef` nested inside that
    class still correctly re-establishes its OWN name on the next
    recursion, same as any other nested def -- only a guard sitting
    directly in the class body, in no method at all, is affected."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            next_func = child
        elif isinstance(child, ast.ClassDef):
            next_func = None
        else:
            next_func = current_func
        if isinstance(child, ast.If) and _if_carries_anchor_raise(child):
            hits.append((current_func, child))
        _find_guard_ifs(child, next_func, hits)


def _bare_name_sorted_call(node) -> "ast.Name | None":
    """Returns the argument `Name` node iff `node` is exactly
    `sorted(<bare name>)` -- a `Call` to a bare `sorted` with exactly one
    positional argument, no keyword arguments (rules out `sorted(x,
    key=...)`/`sorted(x, reverse=...)`), and that one argument itself a
    bare `Name` (rules out a nested call like `sorted(set(x))`, a literal
    like `sorted([1])`, and a starred arg like `sorted(*x)` -- `ast.Starred`
    is not an `ast.Name`, so it is excluded by the same isinstance check
    with no special-casing needed). Returns `None` for anything else.

    Finding 1 (codex round 13): the prior version of this check only
    confirmed the callee was named `sorted` and never looked at the
    argument at all, so `sorted(set(X)) != sorted(set(Y))` -- the exact
    duplicate-collapse defect round 11 fixed -- passed it silently. This
    is what closes that gap."""
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


def _is_sorted_list_mismatch_shape(expr, expected_names: "tuple[str, str]") -> bool:
    """True iff `expr` is exactly `sorted(X) != sorted(Y)` where `{X, Y}`
    (order-insensitive, since `!=` is symmetric) equals `set(expected_names)`
    -- a `Compare` node with a single `!=` operator and both sides a bare
    `sorted(<Name>)` call (see `_bare_name_sorted_call`) over this
    particular site's own two operands. Anything else (`None` from an
    unsupported hoisted form, a `set(...)`/`len(...)` comparison, a
    self-comparison `sorted(X) != sorted(X)`, a comparison against the
    wrong-but-real name, a boolean flag with no comparison at all, ...)
    returns `False` rather than raising -- a guard that no longer even
    LOOKS like the required, site-specific comparison is exactly the drift
    this exists to catch, not a reason to crash the test with an unrelated
    `AttributeError`.

    Binding to `expected_names` (not just "any two sorted() calls") is
    Finding 1's second half: a guard with the right shape but the WRONG
    pair of operand names -- e.g. copy-pasted from a sibling site without
    updating one side -- is exactly as real a defect as a `set(...)`-based
    guard, and a bare shape check blind to WHICH names are compared would
    stay green through it."""
    if expr is None or not isinstance(expr, ast.Compare):
        return False
    if len(expr.ops) != 1 or not isinstance(expr.ops[0], ast.NotEq):
        return False
    if len(expr.comparators) != 1:
        return False
    left = _bare_name_sorted_call(expr.left)
    right = _bare_name_sorted_call(expr.comparators[0])
    if left is None or right is None:
        return False
    return {left.id, right.id} == set(expected_names)


def _guard_hits_in_file(path: Path) -> list:
    """Returns one dict per FROZEN_INPUT_SPECS key-mismatch guard found
    anywhere in `path`, each shaped `{"file": <name relative to
    SCRIPTS_DIR>, "function": <enclosing def name, or None>,
    "resolved_expr": <the guard's own `if` test, or None if it is a bare
    name (a hoisted flag)>, "display": <ast.unparse of the test, or a
    fallback string explaining why the hoisted form is unsupported>}` --
    the `display` string is what a failing assertion below quotes, so a
    test failure names the exact shape actually observed, not just that
    something didn't match.

    Deliberately does NOT attempt to resolve a hoisted `_flag = <expr>; if
    _flag:` back to `<expr>` -- see this section's own round-13 comment
    block above for why a one-level resolver turned out to be unsound (its
    "nearest preceding assignment" heuristic ran over an unordered,
    breadth-first traversal, not program order). `if_node.test` is used
    exactly as written; a bare-name test is reported as unsupported rather
    than guessed at."""
    source_text = path.read_text(encoding="utf-8")
    tree = ast.parse(source_text, filename=str(path))
    raw_hits: list = []
    _find_guard_ifs(tree, None, raw_hits)

    results = []
    for owner_func, if_node in raw_hits:
        test_expr = if_node.test
        if isinstance(test_expr, ast.Name):
            resolved = None
            display = (
                f"<hoisted form `if {test_expr.id}:` is not supported; "
                "write the sorted(X) != sorted(Y) comparison directly at "
                "the guard, not behind an intermediate flag variable>"
            )
        else:
            resolved = test_expr
            display = ast.unparse(resolved)
        results.append(
            {
                "file": str(path.relative_to(SCRIPTS_DIR)),
                "function": owner_func.name if owner_func is not None else None,
                "resolved_expr": resolved,
                "display": display,
            }
        )
    return results


def test_frozen_input_key_mismatch_guards_bind_to_owning_function_via_ast():
    """AST-driven sibling-consistency check across every FROZEN_INPUT_SPECS
    key-mismatch guard shipped anywhere under `SCRIPTS_DIR`. See this
    section's own comment block above for the full round-13 story: a
    prior, purely REGEX-based version of this test located a guard by its
    exception-message anchor phrase alone, with no binding to the actual
    `if`/function it lived in, and a later AST version's one-level hoist
    RESOLVER turned out to be unsound (a breadth-first candidate order
    masquerading as "nearest preceding"). This version does neither: it
    requires the guard's `if` test to BE, directly, the required
    comparison, and rejects a hoisted `if _flag:` outright as an
    unsupported form rather than guessing at what `_flag` might resolve
    to.

    This proves something stronger than a scalar count: each of the four
    (file, function) semantic obligations named in `EXPECTED_GUARD_SITES`
    -- `compute_producer_input_digest()` (suspicion_scan.py),
    `compute_skeptic_input_digest()` and `run()` (both skeptic_setup.py),
    `frozen_input_check()` (skeptic_ready.py) -- independently carries a
    guard whose `if` test is exactly `sorted(X) != sorted(Y)` over THAT
    site's own two operand names (bare-Name arguments only, order-
    insensitive), found directly at the guard.

    The file SET scanned is still DERIVED (every `*.py` directly under
    `SCRIPTS_DIR`, non-recursive) rather than hand-typed -- see the
    round-12 comment above for why: it's what let this test's own first
    version miss skeptic_ready.py's guard, landing in the same round,
    entirely. What IS still hand-maintained, deliberately, is
    `EXPECTED_GUARD_SITES` itself: a guard's `if` relocating into some
    OTHER function in the same file (or a fifth guard landing anywhere)
    changes `found_sites` below and is caught by identity, not just by a
    count staying accidentally correct -- including a relocation into a
    nested class body, which `_find_guard_ifs` now attributes to `None`
    rather than to the class's enclosing function (see its own docstring).

    LIMIT, stated plainly rather than left implicit: this proves shape,
    operands, and ownership identity for all four guards. It does NOT
    prove reachability -- a guard of the exact required shape sitting
    after an unconditional `return` (or any other dead-code path) still
    passes every assertion below. See this section's closing comment
    block above for why that is a deliberate, documented gap rather than
    an oversight."""
    all_hits = []
    for script_path in sorted(SCRIPTS_DIR.glob("*.py")):
        all_hits.extend(_guard_hits_in_file(script_path))

    found_sites = {(hit["file"], hit["function"]) for hit in all_hits}
    expected_sites = frozenset(EXPECTED_GUARD_SITES)

    assert len(all_hits) == 4, (
        "expected exactly 4 FROZEN_INPUT_SPECS key-mismatch guards across "
        f"every *.py file under {SCRIPTS_DIR}, found {len(all_hits)}:\n"
        + "\n".join(f"  {hit['file']}::{hit['function']}: {hit['display']}" for hit in all_hits)
    )
    assert found_sites == expected_sites, (
        "FROZEN_INPUT_SPECS key-mismatch guards were not found at exactly "
        "the expected (file, function) locations -- a guard's `if` may "
        "have relocated out of its owning function, or a new one landed "
        "somewhere unexpected:\n"
        f"  missing:    {sorted(expected_sites - found_sites, key=_site_sort_key)}\n"
        f"  unexpected: {sorted(found_sites - expected_sites, key=_site_sort_key)}"
    )

    for hit in all_hits:
        site = (hit["file"], hit["function"])
        assert hit["resolved_expr"] is not None, f"{hit['file']}::{hit['function']}: {hit['display']}"
        expected_names = EXPECTED_GUARD_SITES[site]
        assert _is_sorted_list_mismatch_shape(hit["resolved_expr"], expected_names), (
            f"{hit['file']}::{hit['function']}: a FROZEN_INPUT_SPECS "
            f"key-mismatch guard's `if` resolves to {hit['display']!r}, not "
            f"the required `sorted({expected_names[0]}) != "
            f"sorted({expected_names[1]})` shape (order-insensitive) -- a "
            "`set(...)` comparison collapses duplicate keys, a bare "
            "`len(...)` comparison misses a same-count divergent-key-name "
            "swap, a literal or nested-call argument to sorted() can hide "
            "an unrelated comparison, and a wrong-but-real operand name "
            "silently compares the wrong data; only `sorted(X) != "
            "sorted(Y)` with bare-Name arguments matching this site's own "
            "two operands catches every case."
        )
