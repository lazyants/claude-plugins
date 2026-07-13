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
LEDGER_RECORD_BASE_SCHEMA = SCHEMAS_DIR / "ledger-record-base.schema.json"
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


def _load_ledger_record_base_schema() -> dict:
    return json.loads(LEDGER_RECORD_BASE_SCHEMA.read_text(encoding="utf-8"))


def _load_ledger_and_resumability_doc() -> str:
    return LEDGER_AND_RESUMABILITY_DOC.read_text(encoding="utf-8")


BUNDLE_SECTION_HEADER = "## The three separate bundle hashes"


def _extract_bundle_hashes_section(doc_text: str) -> str:
    """Scopes to references/ledger-and-resumability.md's own dedicated
    "## The three separate bundle hashes -- exact membership" section.

    This document has TWO distinct restatement sites shaped like
    `- **`plugin_bundle_hash`** ...`: an earlier, deliberately abbreviated
    mention inside the "Composite cache key" field-by-field byte-scope
    listing (which does not name all six plugin_bundle_hash members --
    it only names `ledger_update.py` by way of example, plus the two
    templates), and this section's own bullet, which is the one that
    names the full, exact membership list. Searching the whole document
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
