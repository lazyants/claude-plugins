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
# `run()`'s own copy (skeptic_setup.py) NOW HAS a behavioral test, landed
# alongside round 15's fixes below:
# `skeptic_setup.test.py`'s own
# `test_run_fails_closed_on_frozen_input_specs_key_mismatch_after_upstream_digests`.
# It drives `run()` end-to-end, injecting a duplicate-key
# `FROZEN_INPUT_SPECS` mutation only AFTER `compute_skeptic_input_digest()`
# has already run against the real, unmutated tuple (so control genuinely
# reaches line 831's guard rather than tripping an earlier one), and its
# own RED-proof confirms the guard fires both on that duplicate-key
# mismatch and on relocation to dead code after `run()`'s `return`. Stated
# exactly as that test's own docstring records it, not oversold: RED-proof
# point (2) there also shows a bare LENGTH-comparison weakening of this
# SAME guard stays GREEN against that duplicate-key mutation (the mutated
# `_spec_keys` list is longer than the unmutated `_frozen_input_snapshots_by_key`
# dict, so a length check trips on THIS mismatch shape incidentally, by
# size, not because it validates cardinality-with-kind) -- so that
# behavioral test complements this file's static AST check below, it does
# not supersede it; a length-only regression at this specific guard would
# still slip past the behavioral test alone.
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
# not, and cannot, prove reachability, for any of them, equally. All four
# sites now have a behavioral test (named above, `run()`'s landed with
# round 15) with empirical proof that their guard fires under the
# SPECIFIC mismatches those tests construct -- not a general reachability
# proof, but real evidence beyond what this file provides. `run()`'s own
# behavioral test does cover a return-relocation mutation specifically
# (see its own RED-proof point (3) above) -- but a reachability gap of a
# DIFFERENT shape (any other control-flow path that skips the guard
# without literally following `return`, e.g. an early `continue`/`break`
# inside an enclosing loop this file has no such loop today, or a second
# caller that never reaches this code path at all) is still open for all
# four sites, this static check included.
#
# Round 14 (#243, codex round-14 review): three further false-green holes,
# all demonstrated against the actual checker as it stood after round 13.
#
# Finding 1 -- operand and callee bindings were unchecked. The shape check
# above (`_is_sorted_list_mismatch_shape`) confirms the guard's `if` test
# IS `sorted(X) != sorted(Y)` over the right two operand SPELLINGS -- but a
# spelling is not a binding. Two mutations stayed green through it:
#   (a) rebinding one operand name to alias the other's value BEFORE the
#       guard, e.g. `_spec_keys = list(_frozen_input_snapshots_by_key)` in
#       `run()` -- the comparison becomes tautological (can never fire)
#       while both expected names are still textually present at the
#       guard itself;
#   (b) shadowing the builtin `sorted` with a local (or module-level)
#       `sorted = set` -- duplicate-key collapse, the exact defect round
#       11 fixed, is restored while the accepted AST shape is untouched.
# General dataflow/aliasing analysis (proving WHAT a rebind's right-hand
# side evaluates to, or whether it happens to alias the other operand) is
# the same class of disproportionate machinery this file has already
# declined to build twice (the round-13 hoist-resolver story above, and
# the reachability/dominance gap still left open above) -- but every guard
# this codebase actually ships binds each of its own two operand names,
# and the name `sorted`, in a fixed, narrow shape: each operand name
# exactly ONCE, `sorted` not at all, within the guard's own owning scope
# (verified directly against all four shipped sites via a throwaway AST
# count before writing the check below: 1/1/0, at every site, in both
# files). So `_store_name_counts_in_scope`/`_rebind_violations` below are a
# purely SYNTACTIC count of bare-Name Store bindings in that scope, not
# aliasing analysis -- a count outside that fixed shape is already,
# unambiguously, a real defect here, cheap and sound rather than a
# heuristic that could plausibly misfire on legitimate code shaped like
# what's shipped today. What this does NOT catch, stated plainly:
# rebinding via anything other than a bare Name Store in the guard's own
# scope -- `import builtins; builtins.sorted = set`, `globals()["sorted"]
# = set`, a rebind reached only through a closure two scopes up, or any
# genuine alias-through-a-third-name dataflow chain. Those remain exactly
# the kind of general analysis this file continues to decline to build.
#
# Finding 2 -- ownership was tracked by bare function NAME only
# (`owner_func.name`), not by qualified lexical position, even though the
# round-13 `ClassDef` reset already existed to null out ownership across a
# class boundary. A guard relocated from module-level `run()` into
#     class _RelocatedGuard:
#         def run(self):
#             ...
# reset ownership to `None` entering the class, same as before -- but then
# re-established it as bare `"run"` re-entering the method `def run`,
# indistinguishable from the real module-level function of the same name.
# Fixed by qualifying a method's owner as `"<ClassName>.<method>"` (using
# the immediately-enclosing class, tracked via a second `immediate_parent`
# scope-kind marker threaded alongside `current_owner`) whenever a
# `FunctionDef`/`AsyncFunctionDef`'s own immediate lexical parent is a
# `ClassDef`, and leaving a genuine module-level (or nested-in-a-plain-
# function) def as its bare name, exactly as before. `EXPECTED_GUARD_SITES`
# is keyed on these qualified names below, so `"run"` now means,
# specifically, a def named `run` whose own immediate parent scope is NOT a
# class -- `_RelocatedGuard.run` is a different key entirely, and fails the
# identity-match assertion below as a missing expected site plus an
# unexpected one, exactly the drift this exists to catch.
#
# Finding 3 -- the anchor phrase and the `Raise` were found independently,
# each via `ast.walk` over the guard's ENTIRE body subtree, with no
# requirement that the matched `Raise` be the thing that actually executes
# unconditionally, or that the anchor phrase live inside THAT `Raise`'s own
# exception value. Two shapes stayed green:
#   - `if sorted(X) != sorted(Y): \n    while False:\n        raise
#     AssertionError("...anchor phrase...")` -- a `Raise` that can never
#     execute, nested inside an unrelated compound statement;
#   - a bare no-op string carrying the anchor phrase as a dead expression
#     statement, followed by an unrelated `raise SomethingElse(...)`.
# Fixed WITHOUT dominance/reachability analysis, per codex's own framing of
# this as narrower than the acknowledged "guard after return" gap left
# open above: `_if_carries_anchor_raise` now requires a `Raise` DIRECTLY in
# `if_node.body` (not nested inside any further compound statement the
# direct body contains), and requires the anchor phrase to be found
# specifically within THAT `Raise` node's own subtree -- not anywhere else
# in the guard body. A `Raise` reachable only through a nested
# loop/conditional is no longer treated as the guard's raise at all; an
# anchor phrase sitting beside, rather than inside, the real raise no
# longer counts either.
#
# Round 15 (#243, codex round-15 review): three more false-green holes,
# each arriving through the exact remedy the round-14 review itself
# prescribed -- the recurring shape this file's own history keeps
# demonstrating: a check that keys on a data structure discarding the very
# thing the next attack varies.
#
# Finding 1 -- round 14's `_store_name_counts_in_scope`/`_rebind_violations`
# validate CARDINALITY (an operand name bound exactly once, `sorted` bound
# zero times) but neither the BINDING KIND nor the accepted INITIALIZER.
# Three mutations stayed green through it, each keeping the count itself
# exactly right: (a) replacing `_spec_keys`'s SOLE initializer with
# `list(_frozen_input_snapshots_by_key)` -- still exactly one Assign to
# `_spec_keys`, but now a bare alias of the OTHER operand's value, making
# the comparison tautological; (b) rebinding `_spec_keys` via a walrus
# inside a comprehension -- PEP 572 binds a comprehension's own `:=` to
# the CONTAINING scope, but the round-14 visitor's `visit_ListComp`/etc.
# return unconditionally, skipping the comprehension body entirely, so the
# second binding was never counted; (c) adding `sorted=set` as a DEFAULT
# PARAMETER of the guard's owning function -- a parameter is an `ast.arg`,
# never a Store-context `ast.Name`, so it was structurally invisible to a
# checker that only ever visits `Name` nodes. `global`/`nonlocal` are a
# related, still-open gap of the same shape: a name so declared is not
# actually bound in the scope this walk inspects at all (its real target
# is an outer scope this file has no reason to walk generally), so a local
# count for it can't be trusted either way -- treated below as an
# unconditional violation rather than chased into the outer scope, the
# same "reject the unsupported form outright" discipline round 13 already
# established for a hoisted `if _flag:`.
#
# Fixed by replacing the pure count with `_scope_binding_info()`, which
# additionally tracks, per bound name: every binding kind a bare
# `ast.Name`-Store walk cannot see on its own (`except ... as`/
# `ast.ExceptHandler.name`, `import ... as`/`alias.asname`, and a walrus
# specifically scoped inside a comprehension via a targeted subtree search
# rather than a blanket descent -- everything else a comprehension binds,
# its own `for` targets and element expression, stays correctly out of
# scope); every `global`/`nonlocal` declaration; and, for a name bound by
# exactly one plain `name = <value>` Assign to a bare Name, that Assign's
# own `.value` node, so its shape can be inspected rather than merely
# counted. `_rebind_violations` now requires each operand name's sole
# binding to actually BE that plain-Assign form (a binding of any other
# kind -- `AnnAssign`, parameter, `with ... as`, ... -- is rejected even at
# count 1, since this check can no longer verify what such a binding's
# value is), requires that Assign's value to not reference the OTHER
# operand's name anywhere in its subtree (closes bypass (a), and its
# mirror image, symmetrically, without needing to know in advance which of
# the pair is "the real" initializer), and requires the FROZEN_INPUT_SPECS-
# derived operand specifically (identified structurally, by its name
# ending in `spec_keys` -- true of `spec_keys`/`_spec_keys` at all four
# real sites, verified against every shipped guard before writing this
# check) to reference `FROZEN_INPUT_SPECS` itself somewhere in that value.
# `sorted` additionally may not appear as a parameter name (closes (c)) or
# in a `global`/`nonlocal` declaration, on top of round 14's existing
# zero-Store-bindings check (which, extended to see into a comprehension's
# walrus target, now also closes (b)). Deliberately still NOT dataflow/
# aliasing analysis in the general sense: this does not evaluate what an
# arbitrary expression aliases, only (i) whether a name is bound more than
# once by any binding form this walk can see, (ii) whether the one
# accepted binding is the right KIND, and (iii) whether that binding's own
# value textually references a specific, named upstream identifier -- the
# same proportionality argument round 14 already made for its own
# syntactic count, extended to cover the shapes that count alone left
# open. What remains open, stated plainly: a rebind reached through
# `builtins.sorted = ...`, `globals()[...]`, an alias built through a
# third name this file never checks against `FROZEN_INPUT_SPECS` or the
# other operand, or a `global`/`nonlocal`-declared name whose true owning
# (outer) scope this file does not itself validate.
#
# Finding 2 -- `_find_guard_ifs` qualified a method's owner using only its
# SINGLE immediate lexical parent (`f"{immediate_parent[1]}.{child.name}"`
# when that parent is a class, else the bare name) -- not the FULL lexical
# path. Two shapes collided under it, both measured directly against the
# round-14 checker before this fix: a `def run()` nested inside another
# function reported as bare `"run"`, identical to (and colliding with) the
# real module-level `EXPECTED_GUARD_SITES` key `("skeptic_setup.py",
# "run")`; and two DIFFERENT outer classes each containing their own
# `class Inner: def run(self): ...` both reported as `"Inner.run"`, since
# `immediate_parent` only ever remembered the nearest one class/function
# name, overwritten at every new nesting level rather than accumulated.
# Fixed by threading a `path_stack` -- every enclosing function AND class
# name, outermost first, appended to (never overwritten) at each level --
# and qualifying a new def's owner as the full dotted join of that stack
# plus its own name. A bare module-level function's path stack is empty,
# so its qualified name is still just its own bare name, unchanged from
# round 14 -- none of the four real shipped sites are nested inside
# anything, so `EXPECTED_GUARD_SITES` itself needs no changes for this
# fix; only synthetic, deliberately-nested mutations exercise the new
# behavior. `current_owner` still means exactly what round 13 established
# (the nearest enclosing FUNCTION only; a `ClassDef`'s own body outside
# any method still resets it to `None`) -- only the STRING used to name
# that owner changed.
#
# Finding 3 -- `_if_carries_anchor_raise`'s own docstring (round 14)
# claimed "a message built via an intermediate variable ... is still
# found", but the implementation only ever called `ast.walk` on the
# `Raise` statement's OWN subtree -- a `message = "...anchor..."` Assign
# in a PRECEDING sibling statement is not part of that subtree at all, so
# the documented claim was false: `if sorted(paths) != sorted(_spec_keys):
# message = "...anchor phrase..."; raise AssertionError(message)` --
# itself a perfectly correct, fail-closed guard -- was rejected by this
# checker. Fixed by resolving ONE level: when the `Raise`'s exception
# value is a bare Name, or a single-argument call whose one positional
# argument is a bare Name, search backwards from the `Raise` for the
# NEAREST preceding statement in `if_node.body` that assigns that same
# name, and check that assignment's own value for the anchor phrase
# instead. This is deliberately NOT the round-13 hoist-resolver's mistake:
# that walked an unordered, breadth-first scope-body candidate list with
# no notion of program order; here the candidate list is `if_node.body`
# itself, a single flat list already in source order, so "nearest
# preceding" is sound to compute directly, no reaching-definition analysis
# needed. Resolution stops at one level (it does not chase a message
# variable that itself references a further variable) and is scoped only
# to `if_node.body`'s own statements (a decoy anchor-carrying assignment
# to an unrelated name, or one that lives outside the `if` entirely, still
# does not resolve) -- both deliberate, narrow boundaries matching every
# other proportionality call this file has already made. The two existing
# mandatory-RED shapes this fix must not regress -- `while False: raise
# ...` (the `Raise` is nested inside a `While`, never a direct `if_node.body`
# statement, so it is never reached at all) and a decoy anchor string
# sitting beside an unrelated raise (the resolver only ever looks at the
# SPECIFIC name the `Raise` itself references) -- stay rejected under this
# fix for the same structural reasons round 14 already established, not
# because of anything new added here.


ANCHOR_PHRASE = "FROZEN_INPUT_SPECS contains a duplicate key"

# The four (file, function) semantic obligations codex asked this test to
# bind to directly, by AST location, rather than trusting a scalar count.
# Round 14 (Finding 2): each function key is now a QUALIFIED name -- see
# the round-14 Finding 2 comment above -- so a guard relocated into a
# same-named class method (`_RelocatedGuard.run`) no longer collides with
# its real module-level namesake (`run`).
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


def _sole_bare_name_argument(exc: "ast.AST | None") -> "str | None":
    """If `exc` (a `Raise` node's own `.exc`) is either a bare `Name`
    (`raise some_exception_instance`) or a `Call` with exactly one
    positional argument, itself a bare `Name`, and no keyword arguments
    (`raise AssertionError(message)`), returns that Name's `.id`. Returns
    `None` for anything else -- a literal message built inline, a
    multi-argument call, a keyword argument, ... -- so the round-15
    intermediate-variable resolution below only ever fires for the exact
    narrow shape it is meant to close, not a general "find any Name
    anywhere in the raise" heuristic."""
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Call) and len(exc.args) == 1 and not exc.keywords:
        arg = exc.args[0]
        if isinstance(arg, ast.Name):
            return arg.id
    return None


def _preceding_sibling_assigns_anchor_string(
    body: "list[ast.stmt]", raise_index: int, name: str
) -> bool:
    """Searches `body[:raise_index]` BACKWARDS (nearest first) for a plain
    `name = <value>` `Assign` to the bare Name `name`, and returns whether
    THAT assignment's own `.value` subtree carries `ANCHOR_PHRASE` in a
    string literal. Stops at the first (nearest) such assignment found --
    resolution is deliberately exactly one level, matching the narrow scope
    `_if_carries_anchor_raise` itself declines to widen beyond.

    `body` is `if_node.body` -- a single flat, already-program-ordered
    statement list -- so "nearest preceding" is sound to compute by a plain
    backwards scan. This is NOT the round-13 hoist-resolver's mistake: that
    resolver's candidate list came from a breadth-first, unordered `stack`-
    based scope-body traversal with no notion of source order at all;
    there is no such ordering hazard over a single list already in source
    order."""
    for stmt in reversed(body[:raise_index]):
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == name
        ):
            return any(
                isinstance(n, ast.Constant) and isinstance(n.value, str) and ANCHOR_PHRASE in n.value
                for n in ast.walk(stmt.value)
            )
    return False


def _if_carries_anchor_raise(if_node: ast.If) -> bool:
    """True if `if_node`'s own body (not its `elif`/`else`) contains a
    `Raise` DIRECTLY (a top-level statement of the `if`'s body, not nested
    inside a further loop/conditional/with/try the body contains) whose
    OWN exception-value subtree carries the round-11 anchor phrase
    somewhere in a string literal -- not "the Raise's first argument is
    this exact Constant", so a message split across an f-string's own
    literal pieces (skeptic_ready.py's copy runs the phrase inline after
    "...or " within a longer string, not on its own line) is still found.
    `ast.walk` (scoped to just that one `Raise` node, not the whole `if`
    body) already descends into an f-string's `JoinedStr.values`, so no
    separate f-string-specific handling is needed here -- every literal
    piece surfaces as its own `Constant`.

    Round 14 (Finding 3, codex round-14 review): the prior version found
    a `Raise` and the anchor phrase INDEPENDENTLY, each via `ast.walk`
    over the entire body subtree, with no requirement that they be the
    SAME raise or that the raise be reachable at all. Two shapes passed
    silently: a `Raise` nested inside `while False:` (dead code, never
    executes, but still found by an unscoped `ast.walk`), and a bare
    no-op string literal carrying the anchor phrase as its own dead
    expression statement, sitting beside an unrelated `raise
    SomethingElse(...)`. Requiring the `Raise` to be a direct body
    statement (not further nested) and the anchor to live inside THAT
    `Raise`'s own subtree closes both without needing dominance/
    reachability analysis -- narrower, and cheaper, than the acknowledged
    "guard after an unconditional `return`" gap this file still leaves
    open (see the closing comment above `ANCHOR_PHRASE`).

    Round 15 (Finding 3, codex round-15 review): round 14's own docstring
    (previous paragraph, before this fix) claimed a message built via an
    intermediate variable was "still found" -- false: the direct
    `ast.walk(stmt)` below only ever sees the `Raise` node's OWN subtree,
    and `message = "...anchor..."; raise AssertionError(message)` puts the
    literal in a PRECEDING sibling statement's `Assign.value`, never inside
    the `Raise` at all, so that perfectly correct, fail-closed guard shape
    was rejected. Fixed by resolving one level: if the direct check finds
    nothing and the `Raise`'s exception value is a bare Name, or a
    single-arg call whose one positional argument is a bare Name (see
    `_sole_bare_name_argument`), search backwards through this SAME `if`
    body for the nearest preceding assignment to that name and check IT
    instead (see `_preceding_sibling_assigns_anchor_string`). The two
    existing mandatory-RED shapes are unaffected: `while False: raise ...`
    still never reaches the `isinstance(stmt, ast.Raise)` check at all (the
    direct body statement is the `While`, not the `Raise` nested inside
    it), and a decoy anchor-carrying assignment to an unrelated name is
    never consulted, because resolution only ever looks up the SPECIFIC
    name the `Raise` itself references."""
    for index, stmt in enumerate(if_node.body):
        if not isinstance(stmt, ast.Raise):
            continue
        if any(
            isinstance(n, ast.Constant) and isinstance(n.value, str) and ANCHOR_PHRASE in n.value
            for n in ast.walk(stmt)
        ):
            return True
        message_name = _sole_bare_name_argument(stmt.exc)
        if message_name is not None and _preceding_sibling_assigns_anchor_string(
            if_node.body, index, message_name
        ):
            return True
    return False


def _find_guard_ifs(node: ast.AST, current_owner, path_stack: list, hits: list) -> None:
    """Recursively walks `node`'s children, tracking which named
    `FunctionDef`/`AsyncFunctionDef` most closely encloses each `If` --
    deliberately NOT a blind `ast.walk`, which has no notion of "current
    enclosing function" and would misattribute a nested def's own guard to
    its outer function (or a guard that moved OUT of its owning function
    into a sibling function, the exact drift shape this test must still
    catch). Appends `(current_owner, if_node)` for every `If` whose body
    `_if_carries_anchor_raise`, where `current_owner` is either `None`
    (module level, or a `ClassDef` body outside any method) or a
    `(qualified_name, function_node)` pair.

    `path_stack` is threaded alongside `current_owner` and holds every
    enclosing function AND class name -- outermost first -- for whichever
    scope directly contains `node`; used solely to build the FULL dotted
    qualified name of the next def encountered (see round 15's Finding 2
    below), never itself the attribution `current_owner` records.

    A `ClassDef` resets `current_owner` to `None`, the same as module
    level: codex round 13's finding 2 named this as a second, separate
    ownership defect from the hoist -- without this reset, a guard
    relocated directly into a class body nested inside its owning function
    (executed at class-definition time, not as part of the function's own
    control flow) stayed attributed to that outer function, so the
    relocation was invisible to `found_sites` below. Verified on disk
    before that fix: a guard moved into `class _Inner:` nested inside
    `run()` was reported as owned by `run` itself.

    Round 14 (Finding 2, codex round-14 review): resetting ownership on
    `ClassDef` entry was not enough on its own -- a `FunctionDef` nested
    directly inside that class RE-established ownership as its own bare
    `.name` on the next recursion, indistinguishable from a genuine
    module-level function of the same name. A guard moved from
    module-level `run()` into `class _RelocatedGuard: def run(self): ...`
    was reported as owned by plain `"run"`, exactly like the real
    function it replaced. The round-14 fix qualified a def's owner as
    `"<ClassName>.<def_name>"` whenever its SINGLE immediate lexical parent
    was a `ClassDef`, and left it bare otherwise.

    Round 15 (Finding 2, codex round-15 review): a single immediate parent
    is not a full lexical path, and two more collisions stayed green
    through the round-14 version, both measured directly against it before
    this fix: a `def run()` nested inside another plain FUNCTION (not a
    class) still qualified as bare `"run"` (its immediate parent is a
    function, not a class, so round 14's own qualification branch never
    triggered at all) -- colliding with the real module-level
    `("skeptic_setup.py", "run")` site; and two DIFFERENT outer classes
    each containing their own `class Inner: def run(self): ...` both
    qualified as `"Inner.run"`, since `immediate_parent` only ever
    remembered the SINGLE nearest class/function name, overwritten (not
    accumulated) at every new nesting level -- `OuterA.Inner.run` and
    `OuterB.Inner.run` were indistinguishable. Fixed by replacing the
    single `immediate_parent` with `path_stack`, an accumulating list of
    every enclosing function AND class name in lexical order, and
    qualifying a new def as the full dotted join of that stack plus its
    own name (`".".join(name for _kind, name in path_stack + [own name])`)
    -- a bare module-level function's stack is empty, so its qualified name
    is still just its own bare name, unchanged from round 14. None of the
    four real shipped guard sites are nested inside anything, so
    `EXPECTED_GUARD_SITES` needs no changes for this fix; only a
    deliberately-nested synthetic mutation exercises the new path-stack
    behavior. `current_owner`'s own meaning is unchanged from round 13 --
    the nearest enclosing FUNCTION only, reset to `None` entering a
    `ClassDef` -- only the STRING used to qualify that owner changed."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified_name = ".".join([name for _kind, name in path_stack] + [child.name])
            next_owner = (qualified_name, child)
            next_stack = path_stack + [("function", child.name)]
        elif isinstance(child, ast.ClassDef):
            next_owner = None
            next_stack = path_stack + [("class", child.name)]
        else:
            next_owner = current_owner
            next_stack = path_stack
        if isinstance(child, ast.If) and _if_carries_anchor_raise(child):
            hits.append((current_owner, child))
        _find_guard_ifs(child, next_owner, next_stack, hits)


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


def _resolved_generic_sorted_operands(expr) -> "tuple[str, str] | None":
    """If `expr` is exactly `sorted(X) != sorted(Y)` for ANY two bare-Name
    operands -- a `Compare` node with a single `!=` operator and both
    sides a bare `sorted(<Name>)` call (see `_bare_name_sorted_call`) --
    returns `(X.id, Y.id)` in the order written. Returns `None` for
    anything else (an unsupported hoisted form, a `set(...)`/`len(...)`
    comparison, a nested-call or literal argument to `sorted()`, ...),
    without regard to which SPECIFIC two names are used -- that
    site-specific binding is `_is_sorted_list_mismatch_shape`'s job below,
    which is written in terms of this helper so the two never drift apart.

    Round 14 (Finding 1): also the entry point `_rebind_violations` uses
    to recover a guard's own actual operand names, independent of whether
    they turn out to match the expected pair -- the rebind checks below
    need to know WHICH two names to count Store-bindings for even before
    the site-specific identity check runs."""
    if expr is None or not isinstance(expr, ast.Compare):
        return None
    if len(expr.ops) != 1 or not isinstance(expr.ops[0], ast.NotEq):
        return None
    if len(expr.comparators) != 1:
        return None
    left = _bare_name_sorted_call(expr.left)
    right = _bare_name_sorted_call(expr.comparators[0])
    if left is None or right is None:
        return None
    return (left.id, right.id)


def _is_sorted_list_mismatch_shape(expr, expected_names: "tuple[str, str]") -> bool:
    """True iff `expr` is exactly `sorted(X) != sorted(Y)` where `{X, Y}`
    (order-insensitive, since `!=` is symmetric) equals `set(expected_names)`
    -- built on `_resolved_generic_sorted_operands` above, narrowed to this
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
    operands = _resolved_generic_sorted_operands(expr)
    if operands is None:
        return False
    return set(operands) == set(expected_names)


def _scope_binding_info(
    scope_body: "list[ast.stmt]",
) -> "tuple[dict[str, int], dict[str, ast.AST | None], set[str]]":
    """Returns `(counts, sole_assign_values, global_or_nonlocal_names)` for
    every bare-name binding reachable DIRECTLY within `scope_body`'s own
    statements -- not descending into a nested function/class/lambda body
    (each is its own scope), and only descending into a comprehension body
    far enough to find a `NamedExpr` (walrus) target, since PEP 572 binds a
    walrus inside a comprehension to the comprehension's CONTAINING scope,
    while everything else a comprehension binds (its own `for` targets, its
    element expression) stays local to the comprehension itself.

    `counts` mirrors round 14's `_store_name_counts_in_scope` (every
    bare-Name Store-context binding: `Assign`/`AugAssign`/`AnnAssign`/`For`/
    `With` targets, walrus, ...) plus two binding shapes round 14 could not
    see at all because they are not `ast.Name` nodes: `except ... as name`
    (an `ExceptHandler.name` string) and `import ... as name`/
    `from ... import x as name` (an `alias.asname`/`alias.name` string).

    `sole_assign_values` maps a name to the `.value` expression of its OWN
    single plain `Assign` (a bare-Name target, not a tuple/starred unpack)
    -- used by `_rebind_violations` to check the accepted INITIALIZER
    shape, not just that a binding occurred. Only ever consulted by the
    caller when `counts[name] == 1`; a name bound more than once, or bound
    by any non-`Assign` kind, is already a violation before this map is
    read at all.

    `global_or_nonlocal_names` is every name declared by a `global`/
    `nonlocal` statement anywhere in `scope_body` -- a name so declared is
    not actually owned by this scope (its real assignment target is an
    outer scope this walk never visits), so a caller must treat it as an
    unconditional violation rather than trust a local count for it.

    Round 15 (Finding 1): the round-14 pure count validated CARDINALITY
    only -- it could not see a parameter (an `ast.arg`, never a Store-
    context `ast.Name`), could not see a walrus bound inside a
    comprehension (the round-14 version returned unconditionally on every
    comprehension node, before ever looking for one), and had no notion of
    an initializer's own shape. See the round-15 Finding 1 comment above
    `ANCHOR_PHRASE` for the three concrete bypasses this closes and the
    proportionality argument for why this stays a SYNTACTIC walk, not
    dataflow/aliasing analysis -- it still does not ask what an arbitrary
    expression evaluates to, only whether a name is bound more than the
    fixed number of times (and by the fixed kind) every guard this
    codebase actually ships binds it."""
    counts: "dict[str, int]" = {}
    sole_assign_values: "dict[str, ast.AST | None]" = {}
    global_or_nonlocal: "set[str]" = set()

    def _record(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    class _ScopeBindingVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.AST) -> None:
            return  # separate scope -- do not descend

        def visit_AsyncFunctionDef(self, node: ast.AST) -> None:
            return

        def visit_ClassDef(self, node: ast.AST) -> None:
            return

        def visit_Lambda(self, node: ast.AST) -> None:
            return

        def _walrus_targets_only(self, node: ast.AST) -> None:
            # A comprehension is its own scope in Python 3 for everything
            # EXCEPT a walrus, which PEP 572 binds to the comprehension's
            # containing scope -- so this looks for `NamedExpr` targets
            # only, and deliberately does not otherwise descend (the
            # comprehension's own `for` targets and element expression stay
            # local to it, invisible to the enclosing scope this function
            # is asked about).
            for n in ast.walk(node):
                if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                    _record(n.target.id)

        def visit_ListComp(self, node: ast.AST) -> None:
            self._walrus_targets_only(node)

        def visit_SetComp(self, node: ast.AST) -> None:
            self._walrus_targets_only(node)

        def visit_DictComp(self, node: ast.AST) -> None:
            self._walrus_targets_only(node)

        def visit_GeneratorExp(self, node: ast.AST) -> None:
            self._walrus_targets_only(node)

        def visit_Assign(self, node: ast.Assign) -> None:
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                # More than one plain Assign to the same bare name is
                # itself already a count>1 violation the caller will catch
                # via `counts`; recording `None` here just avoids handing
                # back an arbitrary ONE of several conflicting values.
                sole_assign_values[name] = (
                    node.value if name not in sole_assign_values else None
                )
            self.generic_visit(node)  # still counts the target + walks the value

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.name:
                _record(node.name)
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                _record(alias.asname or alias.name.split(".")[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                _record(alias.asname or alias.name)

        def visit_Global(self, node: ast.Global) -> None:
            global_or_nonlocal.update(node.names)

        def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
            global_or_nonlocal.update(node.names)

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Store):
                _record(node.id)

    visitor = _ScopeBindingVisitor()
    for stmt in scope_body:
        visitor.visit(stmt)
    return counts, sole_assign_values, global_or_nonlocal


def _param_names(func_node: "ast.FunctionDef | ast.AsyncFunctionDef") -> "set[str]":
    """Every parameter name `func_node`'s own signature introduces directly
    into its local scope -- positional-only, positional-or-keyword,
    keyword-only, `*args`, `**kwargs` -- regardless of whether any of them
    carries a default value. A parameter binds its name exactly like an
    assignment would, but `ast.arg` is never an `ast.Name` node, so no
    Store-context Name walk (round 14's or round 15's `_scope_binding_info`
    alike) can ever see it; this is why `sorted=set` as a default parameter
    value (round 15, Finding 1) was invisible to the round-14 check."""
    args = func_node.args
    names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg is not None:
        names.add(args.vararg.arg)
    if args.kwarg is not None:
        names.add(args.kwarg.arg)
    return names


def _references_name(node: "ast.AST | None", name: str) -> bool:
    """True if `name` appears anywhere in `node`'s own subtree as a bare
    `ast.Name` (any context) -- used to check that an operand's initializer
    actually references a specific upstream name (`FROZEN_INPUT_SPECS`, or
    the OTHER operand) rather than merely having a plausible-looking shape.
    `node` may be `None` (no initializer resolved), which is never a
    reference to anything."""
    if node is None:
        return False
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


_SPEC_KEYS_OPERAND_SUFFIX = "spec_keys"


def _rebind_violations(
    operand_names: "tuple[str, str]",
    owner_func: "ast.FunctionDef | ast.AsyncFunctionDef | None",
    module_tree: ast.Module,
) -> "list[str]":
    """Round 14 (Finding 1): cheap, sound, narrowly-scoped defense against
    a rebind of either operand name, or of the builtin name `sorted`
    itself, that defeats the guard while leaving its AST shape and operand
    spelling untouched. Returns a list of human-readable violation strings
    (empty if none found).

    Round 15 (Finding 1, codex round-15 review): the round-14 version above
    validated CARDINALITY only (a name bound exactly once, `sorted` bound
    zero times) -- not the BINDING KIND, nor the accepted INITIALIZER. This
    version additionally requires: each operand name's sole binding to
    actually be a plain `name = <value>` `Assign` to a bare Name (any other
    binding KIND -- `AnnAssign`, a parameter, `with ... as`, ... -- is
    rejected even at count 1, since this check can no longer verify what
    such a binding's value even is); that Assign's own value to NOT
    reference the OTHER operand's name anywhere in its subtree (closes
    `_spec_keys = list(_frozen_input_snapshots_by_key)` -- and its mirror
    image, symmetrically, without needing to know in advance which operand
    is "the real" one); the FROZEN_INPUT_SPECS-derived operand specifically
    (identified structurally, by its name ending in `spec_keys` -- true of
    `spec_keys`/`_spec_keys` at all four real sites, verified against every
    shipped guard before writing this check) to reference
    `FROZEN_INPUT_SPECS` itself somewhere in its value; and `sorted`
    additionally to never appear as a parameter name or in a `global`/
    `nonlocal` declaration, on top of the existing zero-Store-bindings
    check (now also seeing a walrus bound inside a comprehension, via
    `_scope_binding_info`'s own round-15 extension).

    Still deliberately NOT general dataflow/aliasing analysis: this does
    not evaluate what an arbitrary expression evaluates to, only (a)
    whether a name is bound more than once by any binding form this walk
    can see, (b) whether the one accepted binding is the right KIND, and
    (c) whether that binding's own value textually references a specific,
    named identifier. Full aliasing analysis was already rejected twice as
    disproportionate for this file (see the round-13 hoist-resolver story
    above); this remains proportionate for the same reason round 14 gave --
    every real guard's own two operand names are each bound EXACTLY ONCE,
    by a plain Assign built from the expected upstream name, and the name
    `sorted` ZERO times, in scope (verified against all four shipped sites
    before writing this check) -- any deviation from that fixed shape is
    already, unambiguously, a real defect here, not a judgment call. What
    remains open, stated plainly: a rebind reached through
    `builtins.sorted = ...`, `globals()[...]`, an alias built through a
    third name never checked against `FROZEN_INPUT_SPECS` or the other
    operand, or a `global`/`nonlocal`-declared name whose true owning
    (outer) scope this file does not itself validate -- flagged below as an
    unconditional violation rather than silently trusted, but not chased
    into that outer scope."""
    owner_scope_body = owner_func.body if owner_func is not None else module_tree.body
    owner_counts, owner_assign_values, owner_global_nonlocal = _scope_binding_info(
        owner_scope_body
    )
    owner_param_names = _param_names(owner_func) if owner_func is not None else set()

    violations = []
    for index, name in enumerate(operand_names):
        other_name = operand_names[1 - index]
        if name in owner_global_nonlocal:
            violations.append(
                f"operand name {name!r} is declared global/nonlocal in the "
                "guard's own owning scope -- its real binding lives in an "
                "outer scope this check does not follow, so a local rebind "
                "count cannot be trusted for it at all"
            )
            continue
        if name in owner_param_names:
            violations.append(
                f"operand name {name!r} is also a parameter name of the "
                "guard's own owning function -- this check requires it to "
                "be bound by exactly one plain assignment, not a parameter"
            )
            continue
        count = owner_counts.get(name, 0)
        if count != 1:
            violations.append(
                f"operand name {name!r} is bound {count} time(s) in its guard's "
                "own owning scope (expected exactly 1) -- a second rebind (e.g. "
                "aliasing it to the OTHER operand's value) makes the comparison "
                "tautological while leaving both expected names present"
            )
            continue
        value = owner_assign_values.get(name)
        if value is None:
            violations.append(
                f"operand name {name!r} is bound exactly once in its guard's "
                "own owning scope, but not by a plain `name = <value>` "
                "assignment to a bare name -- this check cannot verify the "
                "accepted initializer shape for any other binding form"
            )
            continue
        if _references_name(value, other_name):
            violations.append(
                f"operand name {name!r} is initialized from an expression "
                f"that itself references the OTHER operand name "
                f"{other_name!r} -- this makes the guard's comparison "
                "tautological (it can never fire) while leaving both "
                "expected names textually present at the guard"
            )
        if name.endswith(_SPEC_KEYS_OPERAND_SUFFIX) and not _references_name(
            value, "FROZEN_INPUT_SPECS"
        ):
            violations.append(
                f"operand name {name!r} is bound exactly once by a plain "
                "assignment, but its initializer does not reference "
                "FROZEN_INPUT_SPECS anywhere -- the FROZEN_INPUT_SPECS-"
                "derived operand must actually be built from that tuple, "
                "not from an arbitrary expression that happens to have the "
                "right name"
            )

    if "sorted" in owner_global_nonlocal:
        violations.append(
            "the builtin name `sorted` is declared global/nonlocal in the "
            "guard's own owning scope"
        )
    if "sorted" in owner_param_names:
        violations.append(
            "the builtin name `sorted` is a parameter name of the guard's "
            "own owning function (e.g. a default parameter `sorted=set`) -- "
            "this shadows the builtin for the whole function even though it "
            "is never assigned to as a statement"
        )
    if owner_counts.get("sorted", 0) > 0:
        violations.append(
            "the builtin name `sorted` is locally rebound within the guard's "
            "own owning scope -- e.g. `sorted = set` restores duplicate-key "
            "collapse while leaving the accepted `sorted(X) != sorted(Y)` AST "
            "shape untouched"
        )
    if owner_func is not None:
        module_counts, _module_assign_values, _module_global_nonlocal = _scope_binding_info(
            module_tree.body
        )
        if module_counts.get("sorted", 0) > 0:
            violations.append(
                "the builtin name `sorted` is rebound at module level in this "
                "file -- this shadows `sorted` for every function in the "
                "module, including the guard's own owning scope"
            )
    return violations


def _guard_hits_in_file(path: Path) -> list:
    """Returns one dict per FROZEN_INPUT_SPECS key-mismatch guard found
    anywhere in `path`, each shaped `{"file": <name relative to
    SCRIPTS_DIR>, "function": <qualified owner name, or None>,
    "resolved_expr": <the guard's own `if` test, or None if it is a bare
    name (a hoisted flag)>, "display": <ast.unparse of the test, or a
    fallback string explaining why the hoisted form is unsupported>,
    "rebind_violations": <list of round-14 Finding 1 violation strings,
    always empty when `resolved_expr` is None>}` -- the `display` string is
    what a failing assertion below quotes, so a test failure names the
    exact shape actually observed, not just that something didn't match.
    `function` is now a QUALIFIED name (see `_find_guard_ifs`'s own
    docstring, round-14 Finding 2) -- a method's owner is
    `"<ClassName>.<def_name>"`, not just `<def_name>`.

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
    _find_guard_ifs(tree, None, [], raw_hits)

    results = []
    for owner, if_node in raw_hits:
        owner_name = owner[0] if owner is not None else None
        owner_node = owner[1] if owner is not None else None
        test_expr = if_node.test
        if isinstance(test_expr, ast.Name):
            resolved = None
            display = (
                f"<hoisted form `if {test_expr.id}:` is not supported; "
                "write the sorted(X) != sorted(Y) comparison directly at "
                "the guard, not behind an intermediate flag variable>"
            )
            rebind_violations: list = []
        else:
            resolved = test_expr
            display = ast.unparse(resolved)
            operands = _resolved_generic_sorted_operands(resolved)
            rebind_violations = (
                _rebind_violations(operands, owner_node, tree) if operands is not None else []
            )
        results.append(
            {
                "file": str(path.relative_to(SCRIPTS_DIR)),
                "function": owner_name,
                "resolved_expr": resolved,
                "display": display,
                "rebind_violations": rebind_violations,
            }
        )
    return results


def test_frozen_input_key_mismatch_guards_bind_to_owning_function_via_ast():
    """AST-driven sibling-consistency check across every FROZEN_INPUT_SPECS
    key-mismatch guard shipped anywhere under `SCRIPTS_DIR`. See this
    section's own comment block above for the full round-13 and round-14
    story: a prior, purely REGEX-based version of this test located a
    guard by its exception-message anchor phrase alone, with no binding to
    the actual `if`/function it lived in; a later AST version's one-level
    hoist RESOLVER turned out to be unsound (a breadth-first candidate
    order masquerading as "nearest preceding"); and round 14 closed three
    further false-green holes in the AST version that replaced it (operand/
    `sorted` rebinding, same-named-method ownership collision, and an
    anchor/raise found independently of each other and of reachability --
    see the round-14 comment block above `ANCHOR_PHRASE`). This version
    requires the guard's `if` test to BE, directly, the required
    comparison; rejects a hoisted `if _flag:` outright as an unsupported
    form rather than guessing at what `_flag` might resolve to; requires
    the guard's operand names and the builtin `sorted` to each be bound in
    its owning scope exactly as many times as every real guard binds them;
    qualifies a method's ownership by its enclosing class so it cannot be
    confused with a same-named module-level function; and requires the
    anchor-carrying `Raise` to be a direct, unconditionally-reached
    statement of the guard's own body.

    This proves something stronger than a scalar count: each of the four
    (file, function) semantic obligations named in `EXPECTED_GUARD_SITES`
    -- `compute_producer_input_digest()` (suspicion_scan.py),
    `compute_skeptic_input_digest()` and `run()` (both skeptic_setup.py),
    `frozen_input_check()` (skeptic_ready.py) -- independently carries a
    guard whose `if` test is exactly `sorted(X) != sorted(Y)` over THAT
    site's own two operand names (bare-Name arguments only, order-
    insensitive), found directly at the guard, with neither operand name
    nor `sorted` itself rebound anywhere in the guard's own owning scope.

    The file SET scanned is still DERIVED (every `*.py` directly under
    `SCRIPTS_DIR`, non-recursive) rather than hand-typed -- see the
    round-12 comment above for why: it's what let this test's own first
    version miss skeptic_ready.py's guard, landing in the same round,
    entirely. What IS still hand-maintained, deliberately, is
    `EXPECTED_GUARD_SITES` itself: a guard's `if` relocating into some
    OTHER function in the same file (or a fifth guard landing anywhere)
    changes `found_sites` below and is caught by identity, not just by a
    count staying accidentally correct -- including a relocation into a
    nested class body (attributed to `None`) or into a same-named method
    of a class (attributed to a distinct qualified name), per
    `_find_guard_ifs`'s own docstring.

    LIMITS, stated plainly rather than left implicit:
      - This does NOT prove reachability -- a guard of the exact required
        shape sitting after an unconditional `return` (or any other
        dead-code path) still passes every assertion below. See this
        section's closing comment block above for why that is a
        deliberate, documented gap rather than an oversight.
      - The round-14 rebind checks are a SYNTACTIC Store-name count, not
        dataflow/aliasing analysis -- a rebind reached via
        `builtins.sorted = ...`, `globals()[...]`, or any alias-through-a-
        third-name chain is invisible to it. See the round-14 Finding 1
        comment above for why that's the deliberate boundary."""
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
        assert not hit["rebind_violations"], (
            f"{hit['file']}::{hit['function']}: guard passes the shape/"
            "operand-name check but fails a round-14 Finding 1 rebind "
            "check:\n" + "\n".join(f"  - {v}" for v in hit["rebind_violations"])
        )
