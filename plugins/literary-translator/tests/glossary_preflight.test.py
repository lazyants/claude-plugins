"""tests/glossary_preflight.test.py -- regression suite for
scripts/glossary_preflight.py (#138, S0/TP-0): the W3 glossary pre-dispatch
staleness gate that halts BEFORE any agent is dispatched whenever a
durable_root's copy of canon-entry.schema.json / canon-batch.schema.json /
glossary_TASK.md has drifted from the plugin's own shipped, fresh
basis:"sense_translated" contract.

Every case below runs the REAL, unmodified shipped glossary_preflight.py as
a genuine subprocess (never a reimplementation) against a constructed
fixture durable_root -- the script self-anchors its "plugin side" (the
schemas + glossary_TASK.template.md it compares AGAINST) to its own
`Path(__file__).resolve().parents[1]`, i.e. THIS repo's real, currently
shipped `assets/schemas/` and `assets/templates/glossary_TASK.template.md`
-- so only the DURABLE side needs to be constructed per fixture.

Red-before-green: glossary_preflight.py is a brand-new file -- every case
here proves the script FIRES on a real staleness shape (a-g) or does NOT
false-fire on a genuinely current root (h, the false-positive guard). Case
(a) additionally asserts an S0-UNIQUE token (the literal "sense_translated"
remediation substring and the offending schema's filename) is present in
the halt message, proving the halt is genuinely THIS gate's own -- not a
vacuous non-zero exit that could come from anywhere.
"""
import copy
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_MARKER_RE = re.compile(r"^\s*<!--\s*PROMPT_CONTRACT_VERSION:\s*(.+?)\s*-->\s*$")


def plugin_prompt_contract_version() -> int:
    """The plugin's OWN currently-shipped glossary_TASK.template.md leading
    marker value -- read directly from the real file (never hardcoded), so
    these tests never drift from whatever version C-prompts' S8 bump lands
    at. Deliberately reimplemented here (not imported from the script under
    test) so this file's own expectations don't just trivially restate the
    script's implementation."""
    text = GLOSSARY_TASK_TEMPLATE_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        match = _MARKER_RE.match(line)
        if match:
            return int(match.group(1).strip())
    raise AssertionError(
        f"{GLOSSARY_TASK_TEMPLATE_PATH} has no leading PROMPT_CONTRACT_VERSION "
        f"marker -- this test file's own harness assumption is stale"
    )

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS_DIR = SKILL_DIR / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SCHEMAS_DIR = ASSETS_DIR / "schemas"
TEMPLATES_DIR = ASSETS_DIR / "templates"

SCRIPT_PATH = SCRIPTS_DIR / "glossary_preflight.py"
CANON_ENTRY_SCHEMA_PATH = SCHEMAS_DIR / "canon-entry.schema.json"
CANON_BATCH_SCHEMA_PATH = SCHEMAS_DIR / "canon-batch.schema.json"
GLOSSARY_TASK_TEMPLATE_PATH = TEMPLATES_DIR / "glossary_TASK.template.md"

for _p in (SCRIPT_PATH, CANON_ENTRY_SCHEMA_PATH, CANON_BATCH_SCHEMA_PATH, GLOSSARY_TASK_TEMPLATE_PATH):
    assert _p.is_file(), f"expected plugin asset not found: {_p}"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def make_current_durable_root(tmp_path) -> Path:
    """A durable_root whose schemas/glossary_TASK.md are exact, unmodified
    copies of the plugin's OWN currently-shipped ones -- the baseline every
    per-case fixture below starts from and (for case (a)-(g)) then
    deliberately staleness-corrupts one axis of. Never depends on the
    plugin's shipped content being any PARTICULAR sense_translated shape --
    just that plugin-copy == durable-copy at construction time, which is
    definitionally current."""
    root = tmp_path / "durable_root"
    schemas_dir = root / "schemas"
    schemas_dir.mkdir(parents=True)
    shutil.copy2(CANON_ENTRY_SCHEMA_PATH, schemas_dir / "canon-entry.schema.json")
    shutil.copy2(CANON_BATCH_SCHEMA_PATH, schemas_dir / "canon-batch.schema.json")
    shutil.copy2(GLOSSARY_TASK_TEMPLATE_PATH, root / "glossary_TASK.md")
    return root


def run_preflight(durable_root: Path):
    # Delegates to run_preflight_with_script (defined below; resolved at call
    # time, always inside a test) so the two share ONE subprocess-invocation
    # contract -- capture_output/text/timeout can never drift between them.
    return run_preflight_with_script(SCRIPT_PATH, durable_root)


def make_fixture_plugin(tmp_path, *, canon_entry_doc=None, canon_batch_doc=None, glossary_task_text=None):
    """Builds an ISOLATED fixture "plugin install": a copy of the REAL
    glossary_preflight.py under `{fixture}/assets/scripts/`, with its OWN
    `assets/schemas/`+`assets/templates/` siblings -- so its self-anchoring
    (`Path(__file__).resolve().parents[1]`) resolves the "plugin side"
    against THIS fixture, never the real installed plugin. Lets a test
    override exactly what the plugin side contains (needed for FIX 1's
    order-insensitivity proof, which needs a plugin schema with 2+
    sense_translated clauses the real shipped schema doesn't have, and FIX
    5's proof, which needs a plugin template with no marker) -- the same
    self-anchoring-isolation pattern tests/draft_path_convention.test.py
    and tests/draft_sha1.test.py use for the scripts they exercise.
    Defaults any doc/text NOT overridden to the real plugin's own current
    shipped copy, so a test overriding only ONE axis doesn't have to
    hand-construct the other two."""
    fixture_root = tmp_path / "fixture_plugin"
    scripts_dir = fixture_root / "assets" / "scripts"
    schemas_dir = fixture_root / "assets" / "schemas"
    templates_dir = fixture_root / "assets" / "templates"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    templates_dir.mkdir(parents=True)

    shutil.copy2(SCRIPT_PATH, scripts_dir / "glossary_preflight.py")
    write_json(
        schemas_dir / "canon-entry.schema.json",
        canon_entry_doc if canon_entry_doc is not None else load_json(CANON_ENTRY_SCHEMA_PATH),
    )
    write_json(
        schemas_dir / "canon-batch.schema.json",
        canon_batch_doc if canon_batch_doc is not None else load_json(CANON_BATCH_SCHEMA_PATH),
    )
    (templates_dir / "glossary_TASK.template.md").write_text(
        glossary_task_text
        if glossary_task_text is not None
        else GLOSSARY_TASK_TEMPLATE_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return scripts_dir / "glossary_preflight.py"


def run_preflight_with_script(script_path: Path, durable_root: Path):
    return subprocess.run(
        [sys.executable, str(script_path), "--durable-root", str(durable_root)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _load_glossary_preflight_module():
    """Loads the REAL shipped glossary_preflight.py directly (by file
    identity, never a reimplementation) for a unit-level call into its
    internals -- mirrors tests/profile_validate.test.py's own
    `_load_profile_validate_module` pattern."""
    spec = importlib.util.spec_from_file_location("glossary_preflight", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# A minimal, valid, marker-carrying glossary task template that does NOT
# mention "sense_translated" -- used by fixture-plugin tests that need to
# keep Step 6's prompt axis out of scope (trivially skipped) so they can
# isolate the schema axis alone.
NEUTRAL_GLOSSARY_TASK_TEXT = (
    "<!-- PROMPT_CONTRACT_VERSION: 3 -->\n<!-- no mention of the new basis here -->\n"
)


def _sense_translated_clause(then_required, note_pattern="\\S", if_required=None):
    return {
        "if": {
            "required": if_required if if_required is not None else ["basis"],
            "properties": {"basis": {"const": "sense_translated"}},
        },
        "then": {
            "required": then_required,
            "properties": {
                "is_proper_name": {"const": True},
                "note": {"type": "string", "pattern": note_pattern},
                "canonical_target_form": {"type": "string", "pattern": "\\S"},
                "source": False,
            },
        },
    }


def _base_canon_entry_doc(allof_clauses):
    return {
        "properties": {"basis": {"enum": ["established", "sense_translated"]}},
        "allOf": allof_clauses,
    }


def assert_halts(result, *, contains=()):
    assert result.returncode == 2, (
        f"expected exit 2, got {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout == "", f"a halt must print nothing on stdout, got:\n{result.stdout}"
    assert result.stderr.strip() != "", "a halt must print an actionable message on stderr"
    assert "Traceback" not in result.stderr, f"halt must never be a raw traceback:\n{result.stderr}"
    # MINOR-1: _halt collapses any embedded whitespace run (including a
    # literal newline from an interpolated value, e.g. --durable-root
    # itself) to a single space -- the "one actionable line" contract must
    # hold even then.
    assert len(result.stderr.strip().splitlines()) == 1, (
        f"a halt must be exactly ONE stderr line:\n{result.stderr!r}"
    )
    for token in contains:
        assert token in result.stderr, f"expected {token!r} in stderr:\n{result.stderr}"


def assert_ok(result):
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert json.loads(result.stdout.strip()) == {"preflight": "ok"}
    # Pin the exact byte-for-byte contract (FIX 4): compact separators, no
    # space after the colon -- matching the CLI contract as documented in
    # CONTRACT-138.md/SKILL.md/this script's own docstring, `{"preflight":
    # "ok"}` (WITH a space, json.dumps' default) is NOT the same literal.
    assert result.stdout == '{"preflight":"ok"}\n', (
        f"expected the exact compact-JSON literal, got: {result.stdout!r}"
    )
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# (h) The false-positive guard -- a genuinely current root must NOT halt.
# Run first / referenced by every other case as the harness's own sanity
# baseline: if this fails, every other case's fixture-construction approach
# (start from a current copy, then corrupt ONE axis) is unsound.
# ---------------------------------------------------------------------------


def test_fully_current_durable_root_passes(tmp_path):
    root = make_current_durable_root(tmp_path)
    result = run_preflight(root)
    assert_ok(result)


# ---------------------------------------------------------------------------
# (a) durable canon-entry.schema.json's basis enum is stale (pre-#138).
# ---------------------------------------------------------------------------


def test_stale_canon_entry_enum_halts_with_s0_unique_token(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-entry.schema.json")
    doc["properties"]["basis"]["enum"] = ["established", "transliterated", "title", "not_a_name"]
    doc["allOf"] = [
        clause for clause in doc["allOf"]
        if clause.get("if", {}).get("properties", {}).get("basis", {}).get("const") != "sense_translated"
    ]
    write_json(root / "schemas" / "canon-entry.schema.json", doc)

    result = run_preflight(root)
    assert_halts(
        result,
        contains=("canon-entry.schema.json", "sense_translated"),
    )


# ---------------------------------------------------------------------------
# (b) durable canon-batch.schema.json's ACCEPTED (oneOf[0]) enum is stale.
# ---------------------------------------------------------------------------


def test_stale_canon_batch_accepted_enum_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-batch.schema.json")
    accepted = doc["items"]["oneOf"][0]
    accepted["properties"]["basis"]["enum"] = ["established", "transliterated", "title", "not_a_name"]
    accepted["allOf"] = [
        clause for clause in accepted["allOf"]
        if clause.get("if", {}).get("properties", {}).get("basis", {}).get("const") != "sense_translated"
    ]
    write_json(root / "schemas" / "canon-batch.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-batch.schema.json",))


# ---------------------------------------------------------------------------
# (c) durable canon-batch.schema.json's QUEUED (oneOf[1]) enum is stale,
# while ACCEPTED is fine -- proves the QUEUED axis is independently checked.
# ---------------------------------------------------------------------------


def test_stale_canon_batch_queued_enum_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-batch.schema.json")
    queued = doc["items"]["oneOf"][1]
    queued["properties"]["basis"]["enum"] = ["established", "transliterated", "title", "not_a_name"]
    write_json(root / "schemas" / "canon-batch.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-batch.schema.json",))


# ---------------------------------------------------------------------------
# (d) PARTIAL migration: the enum was widened to include sense_translated,
# but the conditional's own shape is stale/wrong (a hand-edit that only did
# half the job). The single equality-of-projection invariant must still
# catch this -- widening the enum alone is not enough to pass.
# ---------------------------------------------------------------------------


def test_widened_enum_with_stale_conditional_shape_still_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-entry.schema.json")
    for clause in doc["allOf"]:
        if clause.get("if", {}).get("properties", {}).get("basis", {}).get("const") == "sense_translated":
            # Drop the is_proper_name:{const:true} constraint -- D11's own
            # guard -- while leaving the enum (already widened, since this
            # fixture starts from a current copy) untouched.
            clause["then"]["properties"]["is_proper_name"] = {"type": "boolean"}
    write_json(root / "schemas" / "canon-entry.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))


# ---------------------------------------------------------------------------
# (e) schemas are current but the durable glossary_TASK.md is stale --
# axis=prompt, the inert-feature gap (fresh schema + stale durable prompt
# means the agent is never even taught the new value).
# ---------------------------------------------------------------------------


def test_stale_durable_glossary_task_prompt_halts_with_prompt_axis(tmp_path):
    root = make_current_durable_root(tmp_path)
    # Deliberately does NOT contain the literal "sense_translated" substring
    # anywhere -- a genuinely pre-#138 hand-migrated glossary task.
    (root / "glossary_TASK.md").write_text(
        "# a hand-migrated glossary task predating the newest basis value\n",
        encoding="utf-8",
    )

    result = run_preflight(root)
    assert_halts(result, contains=("axis=prompt", "glossary_TASK.md"))


def test_missing_durable_glossary_task_prompt_halts_with_prompt_axis(tmp_path):
    """Same axis, absent rather than merely stale -- guarded per the CLI
    contract ("guard this read too"), never a crash."""
    root = make_current_durable_root(tmp_path)
    (root / "glossary_TASK.md").unlink()

    result = run_preflight(root)
    assert_halts(result, contains=("axis=prompt",))


# ---------------------------------------------------------------------------
# (f) durable schemas/ absent entirely (Step 0a never ran, or a
# project-root-coincidence misconfiguration) -- actionable, never a crash.
# ---------------------------------------------------------------------------


def test_missing_durable_schemas_directory_halts_actionably(tmp_path):
    root = tmp_path / "durable_root"
    root.mkdir()

    result = run_preflight(root)
    assert_halts(result, contains=("schemas",))


# ---------------------------------------------------------------------------
# (g) a durable schema file exists but is malformed JSON -- actionable,
# never a raw traceback.
# ---------------------------------------------------------------------------


def test_malformed_durable_schema_json_halts_without_traceback(tmp_path):
    root = make_current_durable_root(tmp_path)
    (root / "schemas" / "canon-entry.schema.json").write_text("{not valid json", encoding="utf-8")

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))


def test_non_object_durable_schema_json_halts_without_traceback(tmp_path):
    """Syntactically valid JSON that is not an object at all (e.g. a bare
    array) -- must not crash trying to call .get() on a non-dict."""
    root = make_current_durable_root(tmp_path)
    (root / "schemas" / "canon-batch.schema.json").write_text("[1, 2, 3]", encoding="utf-8")

    result = run_preflight(root)
    assert_halts(result, contains=("canon-batch.schema.json",))


# ---------------------------------------------------------------------------
# Codex round-1 findings, each with its own regression test.
# ---------------------------------------------------------------------------

# FIX 1 (BLOCKER): the projection must collect ALL sense_translated allOf
# clauses, not stop at the first -- a durable schema with a second,
# CONTRADICTORY clause makes an accepted sense_translated item
# unvalidatable (the exact hang this gate exists to prevent), yet a
# first-match-only projection would compare equal to the plugin's single
# clause and wrongly pass.


def test_second_contradictory_sense_translated_clause_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-entry.schema.json")
    sense_translated_clause = next(
        clause for clause in doc["allOf"]
        if clause.get("if", {}).get("properties", {}).get("basis", {}).get("const") == "sense_translated"
    )
    extra_clause = copy.deepcopy(sense_translated_clause)
    # Contradicts the first: REQUIRES source (as a URI) instead of
    # forbidding it -- an item satisfying clause 1 (no source) would fail
    # clause 2's "source" required, and vice versa -- unvalidatable.
    extra_clause["then"]["required"] = ["note", "is_proper_name", "source"]
    extra_clause["then"]["properties"]["source"] = {"type": "string", "format": "uri"}
    doc["allOf"].append(extra_clause)
    write_json(root / "schemas" / "canon-entry.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))


# FIX 2 (IMPORTANT): every nested schema-node access must be guarded so a
# structurally-corrupt-but-syntactically-valid JSON document (a `null`
# where an object was expected) or an invalid-UTF-8 file degrades to a
# clean, actionable exit 2 -- never an AttributeError/UnicodeDecodeError
# traceback.


def test_structurally_corrupt_but_valid_json_schema_halts_without_traceback(tmp_path):
    root = make_current_durable_root(tmp_path)
    # "if": null is syntactically valid JSON, but every naive
    # `clause.get("if", {}).get("properties", ...)` chain crashes on it:
    # `.get("if", {})` returns None (the key IS present, just null), and
    # `None.get(...)` is an AttributeError.
    (root / "schemas" / "canon-entry.schema.json").write_text(
        json.dumps({"properties": {"basis": {"enum": ["established"]}}, "allOf": [{"if": None}]}),
        encoding="utf-8",
    )

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))
    assert "AttributeError" not in result.stderr


def test_invalid_utf8_durable_schema_halts_without_traceback(tmp_path):
    root = make_current_durable_root(tmp_path)
    (root / "schemas" / "canon-entry.schema.json").write_bytes(b"\xff\xfe{ invalid utf8")

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))
    assert "UnicodeDecodeError" not in result.stderr


# FIX 3 (IMPORTANT): the prompt axis must compare the leading
# PROMPT_CONTRACT_VERSION marker, not a bare "sense_translated in text"
# substring -- a stray comment mentioning the string must not vacuously
# pass, and a genuinely reworded-but-current-marker durable copy must not
# be wrongly flagged (the durable file is a hand-migratable seed, never
# byte-compared).


def test_vacuous_substring_mention_with_stale_marker_still_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    stale_marker = plugin_prompt_contract_version() - 1
    # Literally contains "sense_translated" (a bare substring check would
    # have passed this) but the leading marker is behind.
    (root / "glossary_TASK.md").write_text(
        f"<!-- PROMPT_CONTRACT_VERSION: {stale_marker} -->\n"
        f"<!-- TODO: still need to migrate sense_translated by hand -->\n",
        encoding="utf-8",
    )

    result = run_preflight(root)
    assert_halts(result, contains=("axis=prompt", f"version {stale_marker}"))


def test_reformatted_durable_prompt_with_current_marker_passes(tmp_path):
    """The durable copy is NOT byte-identical to the shipped template (a
    hand-migration may reword/reformat it) -- as long as its leading marker
    is current, it must NOT be flagged. Proves this is a version-marker
    comparison, not a strict content-equality one."""
    root = make_current_durable_root(tmp_path)
    current_marker = plugin_prompt_contract_version()
    (root / "glossary_TASK.md").write_text(
        f"<!-- PROMPT_CONTRACT_VERSION: {current_marker} -->\n"
        f"<!-- a hand-migrated, differently-worded but up-to-date copy -->\n",
        encoding="utf-8",
    )

    result = run_preflight(root)
    assert_ok(result)


def test_missing_marker_in_durable_prompt_halts_with_prompt_axis(tmp_path):
    root = make_current_durable_root(tmp_path)
    (root / "glossary_TASK.md").write_text(
        "no leading marker at all, just prose\n", encoding="utf-8"
    )

    result = run_preflight(root)
    assert_halts(result, contains=("axis=prompt",))


# FIX 4 (COSMETIC): stdout must be the exact compact-JSON literal, no space
# after the colon -- see assert_ok's own pinned assertion; this test names
# it explicitly as its own regression lock.


def test_ok_stdout_is_byte_exact_compact_json(tmp_path):
    root = make_current_durable_root(tmp_path)
    result = run_preflight(root)
    assert_ok(result)


# ---------------------------------------------------------------------------
# Codex round-2 findings: one comprehensive robustness INVARIANT --
# for ANY durable input, either (a) pass because it is genuinely equivalent
# to the plugin's shipped contract, or (b) HALT exit-2 actionable -- never
# traceback, never silently under-validate-and-pass; every parse of the
# PLUGIN's own files fails LOUD on corruption (a packaging bug), never
# silently skipped.
#
# NOTE (round-5, superseding the original D-1 framing below): array-order
# INSENSITIVITY -- once treated as part of this invariant ("never be fooled
# by clause ORDER") -- was itself found unsound (codex round-5 BLOCKER-1):
# a context-blind sort of "set-semantic" arrays also sorted INSTANCE DATA
# sitting inside a const/default/enum member under an identical key name,
# corrupting genuine data differences into false-PASSes. This gate is now
# deliberately ORDER-EXACT for every array (see _project's own docstring):
# a healthy durable is a byte-copy of the plugin's schema and never
# reorders arrays, so the only cost is a safe false-HALT on a
# hand-reordered array -- strictly the strictness-biased direction. The
# test below is FLIPPED accordingly (was ..._passes, now ..._now_halts).
# ---------------------------------------------------------------------------

# D-1 (ORIGINALLY order-sensitive list compare; round-5 FLIPS the
# expectation): allOf clause order is semantically irrelevant in JSON
# Schema in principle, but this gate does not rely on that -- a healthy
# durable schema never reorders arrays at all, so treating a reordered
# `allOf` as a genuine difference (HALT) is both correct-enough for #138's
# real job and immune to the round-5 instance-data-collision class. Needs
# a plugin schema carrying 2 sense_translated clauses (the real shipped
# one has only 1), so this uses the fixture-plugin harness rather than
# tampering the durable side alone.


def test_reordered_two_clause_durable_now_halts_order_exact(tmp_path):
    """Round-5: order-EXACT canonicalization means a merely-REORDERED
    (but otherwise identical) durable allOf clause list is now treated as
    a genuine difference and HALTs -- the strictness-biased, safe
    direction (re-applying the plugin's schema is idempotent). This is a
    deliberate behavior FLIP from the round-4 design (which sorted
    "set-semantic" arrays for order-insensitivity); round-4's own version
    of this test asserted `assert_ok` here. Red-before-green pivot: this
    exact fixture (plugin=[A,B], durable=[B,A]) PASSED under round-4 and
    now HALTs under round-5 -- observed directly when round-5's `_project`
    edit landed (the array-sorting removal), confirmed via real subprocess
    run before this test was rewritten."""
    clause_a = _sense_translated_clause(then_required=["is_proper_name", "note"])
    clause_b = _sense_translated_clause(then_required=["is_proper_name", "note", "canonical_target_form"])
    plugin_entry_doc = _base_canon_entry_doc([clause_a, clause_b])
    durable_entry_doc = _base_canon_entry_doc([clause_b, clause_a])  # reordered, not different

    fixture_script = make_fixture_plugin(
        tmp_path, canon_entry_doc=plugin_entry_doc, glossary_task_text=NEUTRAL_GLOSSARY_TASK_TEXT
    )
    durable_root = tmp_path / "durable_root"
    schemas_dir = durable_root / "schemas"
    schemas_dir.mkdir(parents=True)
    write_json(schemas_dir / "canon-entry.schema.json", durable_entry_doc)
    write_json(schemas_dir / "canon-batch.schema.json", load_json(CANON_BATCH_SCHEMA_PATH))
    (durable_root / "glossary_TASK.md").write_text(NEUTRAL_GLOSSARY_TASK_TEXT, encoding="utf-8")

    result = run_preflight_with_script(fixture_script, durable_root)
    assert_halts(result, contains=("canon-entry.schema.json",))


def test_genuinely_different_two_clause_set_still_halts(tmp_path):
    clause_a = _sense_translated_clause(then_required=["is_proper_name", "note"])
    clause_b = _sense_translated_clause(then_required=["is_proper_name", "note", "canonical_target_form"])
    # clause_c differs from clause_b only in its note pattern -- a genuine
    # content difference, not a reordering.
    clause_c = _sense_translated_clause(
        then_required=["is_proper_name", "note", "canonical_target_form"], note_pattern=".+"
    )
    plugin_entry_doc = _base_canon_entry_doc([clause_a, clause_b])
    durable_entry_doc = _base_canon_entry_doc([clause_a, clause_c])

    fixture_script = make_fixture_plugin(
        tmp_path, canon_entry_doc=plugin_entry_doc, glossary_task_text=NEUTRAL_GLOSSARY_TASK_TEXT
    )
    durable_root = tmp_path / "durable_root"
    schemas_dir = durable_root / "schemas"
    schemas_dir.mkdir(parents=True)
    write_json(schemas_dir / "canon-entry.schema.json", durable_entry_doc)
    write_json(schemas_dir / "canon-batch.schema.json", load_json(CANON_BATCH_SCHEMA_PATH))
    (durable_root / "glossary_TASK.md").write_text(NEUTRAL_GLOSSARY_TASK_TEXT, encoding="utf-8")

    result = run_preflight_with_script(fixture_script, durable_root)
    assert_halts(result, contains=("canon-entry.schema.json",))


# D-2 (if.required ignored): if.properties.basis.const alone is NOT the
# whole correctness-bearing `if` -- a tampered if.required:["__never__"]
# structurally DISABLES the clause (it can never activate against a real
# item) while its `then` shape stays identical to a healthy clause.


def test_durable_if_required_tampered_to_disable_clause_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-entry.schema.json")
    for clause in doc["allOf"]:
        if clause.get("if", {}).get("properties", {}).get("basis", {}).get("const") == "sense_translated":
            clause["if"]["required"] = ["__never__"]
    write_json(root / "schemas" / "canon-entry.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))


# D-3 (_project assumes dict before coercion): `_project(doc)` must never
# crash for a non-dict `doc` -- unreachable via the CLI (_read_json_guarded
# already rejects a non-object top level first), but the projection layer's
# own robustness contract requires it. Unit-level (direct call into the
# real module), per the review's own "unit-level is fine" allowance for
# this specific case. Round-4 (D-3 restated): `_project` is now a
# single-argument whole-schema projector -- no per-filename dispatch table
# to iterate anymore.


def test_project_coerces_non_dict_doc_without_crashing():
    gp = _load_glossary_preflight_module()
    for bad_doc in ([], [1, 2, 3], 5, "x", None, True):
        result = gp._project(bad_doc)
        assert isinstance(result, dict), f"{bad_doc!r} -> {result!r}"


# D-4 (marker: only line 1 should count): a marker must be the file's
# FIRST NON-BLANK line -- one appearing only deeper is never "leading" and
# must be treated as absent (not scanned for), and a later, CONFLICTING
# marker after a genuine leading one must never override the leading value.


def test_durable_marker_not_on_leading_line_treated_as_absent_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    current_marker = plugin_prompt_contract_version()
    (root / "glossary_TASK.md").write_text(
        "some prose that is NOT a marker line\n"
        "more prose\n"
        "still more prose\n"
        "yet more prose\n"
        f"<!-- PROMPT_CONTRACT_VERSION: {current_marker} -->\n",  # marker on line 5 -- too late
        encoding="utf-8",
    )

    result = run_preflight(root)
    assert_halts(result, contains=("axis=prompt",))


def test_durable_leading_marker_wins_over_later_conflicting_marker(tmp_path):
    root = make_current_durable_root(tmp_path)
    current_marker = plugin_prompt_contract_version()
    conflicting = current_marker - 1
    (root / "glossary_TASK.md").write_text(
        f"<!-- PROMPT_CONTRACT_VERSION: {current_marker} -->\n"
        f"<!-- PROMPT_CONTRACT_VERSION: {conflicting} -->\n"  # later, conflicting -- must be ignored
        f"<!-- rest of a hand-migrated but up-to-date file -->\n",
        encoding="utf-8",
    )

    result = run_preflight(root)
    assert_ok(result)


# D-5 (CRITICAL -- plugin-marker-None silently disables the axis): if the
# PLUGIN's own shipped glossary_TASK.template.md mentions "sense_translated"
# but has no leading, parseable PROMPT_CONTRACT_VERSION marker, that is a
# plugin-PACKAGING bug and must HALT -- never silently skip the whole
# prompt axis and fall through to success. Needs the fixture-plugin
# harness (can't tamper the real installed plugin's own template).


def test_plugin_template_with_sense_translated_but_unparseable_marker_halts(tmp_path):
    fixture_script = make_fixture_plugin(
        tmp_path,
        glossary_task_text="no leading marker at all\nbut mentions sense_translated somewhere\n",
    )
    # The durable side is a genuinely CURRENT root (real schemas/template) --
    # proves the halt fires on the PLUGIN-side defect alone, before the
    # durable glossary_TASK.md is even read.
    durable_root = make_current_durable_root(tmp_path)

    result = run_preflight_with_script(fixture_script, durable_root)
    assert_halts(result, contains=("PROMPT_CONTRACT_VERSION", "reinstall"))


# ---------------------------------------------------------------------------
# Codex round-3 findings: the projection was a LOSSY hand-enumerated field
# subset compared with Python `==` -- (a) any construct not explicitly
# enumerated leaked through as a false-PASS (the dangerous direction, an
# unbounded hang), and (b) `==` collapses JSON scalar-type distinctions
# (`True == 1`). Fixed by making the projection a LOSSLESS canonical
# capture of the WHOLE if/then subtree, compared via canonical JSON.
# ---------------------------------------------------------------------------

# BLOCKER-1: a durable is_proper_name const of the JSON INTEGER 1 (instead
# of the JSON boolean true) must NOT compare equal to the plugin's -- pre-
# fix, Python's `1 == True` let this false-PASS silently.


def test_durable_is_proper_name_const_int_one_vs_plugin_bool_true_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-entry.schema.json")
    for clause in doc["allOf"]:
        if clause.get("if", {}).get("properties", {}).get("basis", {}).get("const") == "sense_translated":
            clause["then"]["properties"]["is_proper_name"] = {"const": 1}  # JSON int, not bool
    write_json(root / "schemas" / "canon-entry.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))


# BLOCKER-2: an extra, un-enumerated keyword (`"not": {}`) added to `then`
# must be detected -- pre-fix, a hand-picked field list simply never looked
# at it, so it leaked through as a false-PASS.


def test_durable_then_with_extra_unenumerated_keyword_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-entry.schema.json")
    for clause in doc["allOf"]:
        if clause.get("if", {}).get("properties", {}).get("basis", {}).get("const") == "sense_translated":
            clause["then"]["not"] = {}
    write_json(root / "schemas" / "canon-entry.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))


# IMPORTANT-1 -- SUPERSEDED by round-5: originally, canon-batch's two
# oneOf branches merely REORDERED (each still carrying its correct
# `disposition` const) was required to PASS as a healthy schema (round-3's
# position-indexed selection false-HALTed it; round-4's discriminant
# selection + array-sorting fixed that). Round-5 removes ALL array
# sorting (context-blind sorting caused its own false-PASS collisions --
# see the module docstring), so this case now HALTs instead, exactly like
# every other reordered array. Deliberately accepted: a healthy durable
# schema is a byte-copy and never reorders `oneOf` at all.


def test_durable_canon_batch_branches_reversed_now_halts_order_exact(tmp_path):
    """Round-5 flip (was `..._but_healthy_passes`, asserting `assert_ok`).
    Red-before-green pivot: this exact fixture (the two oneOf branches
    swapped, both `disposition` consts intact) PASSED under round-4's
    order-insensitive `oneOf` handling and now HALTs once round-5's array-
    sorting removal landed -- confirmed via real subprocess run before
    this test was rewritten."""
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-batch.schema.json")
    doc["items"]["oneOf"] = list(reversed(doc["items"]["oneOf"]))
    write_json(root / "schemas" / "canon-batch.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-batch.schema.json",))


# IMPORTANT-2: an absurdly long (5000-digit) marker value must degrade to a
# clean halt, never a raised ValueError (Python 3.11+ caps str->int
# conversion length) escaping as a traceback/exit-1.


def test_durable_marker_with_5000_digit_value_halts_cleanly(tmp_path):
    root = make_current_durable_root(tmp_path)
    huge_digits = "1" * 5000
    (root / "glossary_TASK.md").write_text(
        f"<!-- PROMPT_CONTRACT_VERSION: {huge_digits} -->\n", encoding="utf-8"
    )

    result = run_preflight(root)
    assert_halts(result, contains=("axis=prompt",))
    assert "ValueError" not in result.stderr


# IMPORTANT-3: a non-string member in a `required`/`enum` array is
# MALFORMED and must still differ from the plugin's valid array -- pre-fix,
# `sorted(x for x in ... if isinstance(x, str))` silently FILTERED the
# non-string out, making the durable array look identical to the valid one
# (a false-PASS hiding real corruption).


def test_durable_then_required_with_non_string_member_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-entry.schema.json")
    for clause in doc["allOf"]:
        if clause.get("if", {}).get("properties", {}).get("basis", {}).get("const") == "sense_translated":
            clause["then"]["required"] = ["note", "is_proper_name", 7]
    write_json(root / "schemas" / "canon-entry.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))


def test_durable_basis_enum_with_non_string_member_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-entry.schema.json")
    doc["properties"]["basis"]["enum"] = doc["properties"]["basis"]["enum"] + [7]
    write_json(root / "schemas" / "canon-entry.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))


# MINOR-1: a --durable-root value containing an embedded newline must still
# produce a SINGLE stderr line -- assert_halts' own tightened single-line
# check (added this round) is what actually catches a regression here; this
# test supplies the one realistic way an embedded newline reaches a halt
# message (a value the CLI itself never sanitizes).


def test_durable_root_path_with_embedded_newline_still_single_stderr_line(tmp_path):
    weird_root = tmp_path / "durable\nroot" / "does_not_exist"

    result = run_preflight(weird_root)
    assert_halts(result)


# ---------------------------------------------------------------------------
# Codex round-4 findings: the round-3 fix's `{"if": ..., "then": ...}`
# RECONSTRUCTION was itself still leaky -- it drops any SIBLING keyword on
# the clause object itself (only "if"/"then" were ever read out of it), and
# the narrow "enumerate the sense_translated-relevant parts" approach can
# never see a construct outside canon-entry's sense_translated clause or
# canon-batch's accepted/queued branches at all (a duplicated oneOf branch,
# a top-level `not`, ...). Root cause closed by comparing the WHOLE
# canonicalized schema document instead of any hand-picked subset --
# _project(doc) is now `_canonicalize_schema_node(_as_dict(doc))`, no
# per-file dispatch, nothing enumerated.
# ---------------------------------------------------------------------------

# BLOCKER-1: a sibling keyword (`"not": {}`) added ALONGSIDE `if`/`then` on
# the clause object ITSELF (not inside `then`) -- pre-fix, the round-3
# reconstruction `{"if": ..., "then": ...}` only ever read those two keys
# back out of the clause, so a third sibling key on the clause was never
# even looked at and leaked through as a false-PASS.


def test_durable_clause_sibling_keyword_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-entry.schema.json")
    for clause in doc["allOf"]:
        if clause.get("if", {}).get("properties", {}).get("basis", {}).get("const") == "sense_translated":
            clause["not"] = {}  # sibling of "if"/"then" on the clause itself
    write_json(root / "schemas" / "canon-entry.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))


# BLOCKER-2: canon-batch's `items.oneOf` carries a DUPLICATED accepted
# branch (both copies' `disposition` const intact) -- pre-fix, selecting
# by discriminant picked the FIRST matching branch and simply never looked
# at the duplicate, a false-PASS.


def test_durable_canon_batch_duplicate_accepted_branch_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-batch.schema.json")
    accepted = doc["items"]["oneOf"][0]
    doc["items"]["oneOf"].append(copy.deepcopy(accepted))
    write_json(root / "schemas" / "canon-batch.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-batch.schema.json",))


# IMPORTANT-3: an oversized (5000-digit) integer literal embedded ANYWHERE
# in the durable schema JSON (not just in glossary_TASK.md's marker, round-
# 3's IMPORTANT-2) must degrade to a clean halt during PARSING, never a
# raised ValueError escaping as a traceback -- `_read_json_guarded` now
# catches `(ValueError, RecursionError)`, not just `json.JSONDecodeError`.


def test_durable_schema_oversized_int_literal_halts_cleanly(tmp_path):
    root = make_current_durable_root(tmp_path)
    entry_path = root / "schemas" / "canon-entry.schema.json"
    raw = entry_path.read_text(encoding="utf-8")
    huge_digits = "1" * 5000
    # Raw string surgery -- splice a poisoned oversized-int literal in as a
    # new top-level key's value, never constructing the Python int
    # ourselves first (int(huge_digits) would hit the very same conversion
    # limit this test is exercising, in THIS test process).
    assert raw.rstrip().endswith("}")
    poisoned = raw.rstrip()[:-1] + f',"x_poison":{huge_digits}' + "}"
    entry_path.write_text(poisoned, encoding="utf-8")

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))
    assert "ValueError" not in result.stderr


# IMPORTANT-4: a durable schema nested ~995 levels deep under some key must
# degrade to a clean halt -- no RecursionError, no traceback. The shallow,
# shipped plugin schema never reaches `_MAX_CANON_DEPTH`, so it never
# carries the truncation sentinel; a durable schema this deep gets
# truncated at depth and therefore always DIFFERS from the plugin's --
# clean HALT, never a crash and never a false PASS.


def test_durable_schema_deeply_nested_halts_cleanly(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-entry.schema.json")
    node = {"leaf": True}
    for _ in range(995):
        node = {"nested": node}
    doc["x_deep"] = node
    write_json(root / "schemas" / "canon-entry.schema.json", doc)

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))
    assert "RecursionError" not in result.stderr


# ---------------------------------------------------------------------------
# Codex round-5 findings: the round-4 array-SORTING fix was itself unsound
# -- three false-PASS collision classes, all rooted in sorting/truncating
# being CONTEXT-BLIND (it cannot tell schema STRUCTURE from instance DATA,
# or a genuinely-empty slot from a real value). Root cause removed: no
# array is ever sorted, no value is ever a comparison-default sentinel, and
# too-deep RAISES (no truncation value to collide). See _project's and
# _diff_projection's own docstrings for the full design rationale.
# ---------------------------------------------------------------------------

# BLOCKER-1: round-4's array-sorting was CONTEXT-BLIND -- it sorted every
# array under a "set-semantic" key name (required/enum/type/allOf/anyOf/
# oneOf) REGARDLESS of whether that array was actual schema STRUCTURE or
# just happened to be INSTANCE DATA sitting inside a `const`/`default`
# value under the identical key name. A `const` value that is itself
# `{"required": [...]}` -- e.g. an accepted-item template embedded as
# example/default data -- would have its inner array silently sorted,
# making a genuine data difference (reordered instance data) compare
# "equal", a false-PASS. Uses the fixture-plugin harness (needs a custom
# plugin-side schema shape the real shipped ones don't carry).


def test_durable_const_instance_data_reordered_array_halts(tmp_path):
    plugin_entry_doc = {
        "properties": {"basis": {"enum": ["established"]}},
        "allOf": [],
        # "required" is deliberately the SAME key name round-4's
        # _SET_SEMANTIC_KEYS treated as schema-structural -- this proves
        # the collision was specifically the CONTEXT-BLIND instance-data
        # path, not just "some array somewhere got sorted".
        "x_instance_data": {"const": {"required": ["a", "b"]}},
    }
    durable_entry_doc = {
        "properties": {"basis": {"enum": ["established"]}},
        "allOf": [],
        "x_instance_data": {"const": {"required": ["b", "a"]}},  # reordered
    }

    fixture_script = make_fixture_plugin(
        tmp_path, canon_entry_doc=plugin_entry_doc, glossary_task_text=NEUTRAL_GLOSSARY_TASK_TEXT
    )
    durable_root = tmp_path / "durable_root"
    schemas_dir = durable_root / "schemas"
    schemas_dir.mkdir(parents=True)
    write_json(schemas_dir / "canon-entry.schema.json", durable_entry_doc)
    write_json(schemas_dir / "canon-batch.schema.json", load_json(CANON_BATCH_SCHEMA_PATH))
    (durable_root / "glossary_TASK.md").write_text(NEUTRAL_GLOSSARY_TASK_TEXT, encoding="utf-8")

    result = run_preflight_with_script(fixture_script, durable_root)
    assert_halts(result, contains=("canon-entry.schema.json",))


# BLOCKER-2: round-4's `_diff_projection` used `.get(field, "<absent>")` --
# a FIXED sentinel string as the "field is missing" default. If a real
# schema legitimately carried a top-level key whose VALUE happened to be
# the literal string `"<absent>"`, and the durable side simply omitted
# that key entirely, `durable.get(key, "<absent>")` would return the same
# sentinel string as the plugin's own real value -- comparing "equal", a
# false-PASS masking a genuinely missing key.


def test_absent_field_sentinel_collision_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    doc = load_json(root / "schemas" / "canon-entry.schema.json")
    doc["x_sentinel_probe"] = "<absent>"  # the OLD (round-4) sentinel value
    entry_path = root / "schemas" / "canon-entry.schema.json"
    write_json(entry_path, doc)
    # Durable OMITS x_sentinel_probe entirely -- round-4's sentinel-default
    # `.get()` would have made this look identical to the plugin's real
    # `"<absent>"` value.

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))


# BLOCKER-3: round-4's depth-bound TRUNCATED an over-deep subtree to a
# single shared sentinel VALUE. Two INDEPENDENTLY over-deep schemas (one
# plugin-side, one durable-side, with genuinely different content past the
# depth cap) would each truncate to that SAME sentinel and compare
# "equal" -- a false-PASS hiding two different kinds of corruption.
# Raising (never truncating to a value) closes this: there is no value
# left for two malformed schemas to collide on.


def test_two_sided_deep_schemas_halt(tmp_path):
    def deep(leaf, n):
        node = {"leaf": leaf}
        for _ in range(n):
            node = {"nested": node}
        return node

    plugin_entry_doc = {
        "properties": {"basis": {"enum": ["established"]}},
        "allOf": [],
        "x_deep": deep("plugin-leaf", 105),
    }
    durable_entry_doc = {
        "properties": {"basis": {"enum": ["established"]}},
        "allOf": [],
        "x_deep": deep("durable-leaf", 105),  # different leaf, also over the cap
    }

    fixture_script = make_fixture_plugin(
        tmp_path, canon_entry_doc=plugin_entry_doc, glossary_task_text=NEUTRAL_GLOSSARY_TASK_TEXT
    )
    durable_root = tmp_path / "durable_root"
    schemas_dir = durable_root / "schemas"
    schemas_dir.mkdir(parents=True)
    write_json(schemas_dir / "canon-entry.schema.json", durable_entry_doc)
    write_json(schemas_dir / "canon-batch.schema.json", load_json(CANON_BATCH_SCHEMA_PATH))
    (durable_root / "glossary_TASK.md").write_text(NEUTRAL_GLOSSARY_TASK_TEXT, encoding="utf-8")

    # The PLUGIN side is projected first (Step 1), so its own over-depth
    # schema raises before the durable side is ever read -- either way, a
    # clean exit-2 halt, never a traceback, never a false-PASS.
    result = run_preflight_with_script(fixture_script, durable_root)
    assert_halts(result)
    assert "RecursionError" not in result.stderr
    assert "Traceback" not in result.stderr


# BLOCKER (round-6): `_read_json_guarded` parsed via a BARE `json.loads`,
# which silently keeps the LAST member on a DUPLICATE object key
# (last-wins). A durable schema whose `properties` object carries "basis"
# TWICE -- a STALE (pre-#138) enum first, a byte-identical copy of the
# CURRENT clause second -- would parse to the CURRENT value and the
# currency gate would exit 0 (false-PASS), even though the on-disk
# document is corrupt and parser-dependent (a first-wins consumer
# elsewhere would see the STALE contract). The fix (`_reject_duplicate_
# keys`, an `object_pairs_hook`) must HALT on both the durable-side read
# (Steps 3-5) and the plugin-side read (Step 1) -- both flow through the
# same `_read_json_guarded`.


def _raw_text_with_duplicated_basis_key(base_doc: dict) -> str:
    """Returns RAW JSON text for `base_doc` with its `properties.basis`
    entry duplicated: a STALE (pre-#138, no "sense_translated") variant
    first, then a byte-identical copy of `base_doc`'s own CURRENT `basis`
    clause second. Plain `json.loads` (last-wins on a duplicate key)
    resolves this to the CURRENT clause -- i.e. WITHOUT the round-6 fix,
    a preflight run against this text would see what looks like a fully
    current schema and exit 0 (false-PASS), even though the document is
    corrupt. A Python dict can't hold a duplicate key, so this can't be
    built via `write_json`/`json.dumps` of a dict -- it splices a
    hand-written `properties` object (carrying the duplicate) into an
    otherwise ordinary `json.dumps` of the rest of the document."""
    current_basis = base_doc["properties"]["basis"]
    stale_basis = copy.deepcopy(current_basis)
    stale_basis["enum"] = [v for v in stale_basis["enum"] if v != "sense_translated"]
    assert stale_basis != current_basis, (
        "fixture assumption: base_doc's basis enum includes sense_translated"
    )

    other_properties = {k: v for k, v in base_doc["properties"].items() if k != "basis"}
    duplicated_properties_json = (
        json.dumps(other_properties, ensure_ascii=False)[:-1]
        + f', "basis": {json.dumps(stale_basis, ensure_ascii=False)}'
        + f', "basis": {json.dumps(current_basis, ensure_ascii=False)}'
        + "}"
    )

    skeleton_doc = copy.deepcopy(base_doc)
    skeleton_doc["properties"] = "@@PROPERTIES_PLACEHOLDER@@"
    skeleton_json = json.dumps(skeleton_doc, ensure_ascii=False)
    placeholder_literal = json.dumps("@@PROPERTIES_PLACEHOLDER@@")
    assert skeleton_json.count(placeholder_literal) == 1, (
        "fixture assumption: the placeholder string appears exactly once"
    )
    text = skeleton_json.replace(placeholder_literal, duplicated_properties_json)

    # Sanity: an ORDINARY (last-wins) parse resolves to the byte-identical
    # CURRENT basis clause -- proving this fixture genuinely reproduces the
    # false-PASS shape, not just "some other kind of malformed JSON".
    assert json.loads(text)["properties"]["basis"] == current_basis
    return text


def test_durable_duplicate_basis_key_last_wins_would_false_pass_halts(tmp_path):
    root = make_current_durable_root(tmp_path)
    current_doc = load_json(CANON_ENTRY_SCHEMA_PATH)
    text = _raw_text_with_duplicated_basis_key(current_doc)
    (root / "schemas" / "canon-entry.schema.json").write_text(text, encoding="utf-8")

    result = run_preflight(root)
    assert_halts(result, contains=("canon-entry.schema.json",))


def test_plugin_side_duplicate_basis_key_also_halts(tmp_path):
    # The SAME false-PASS vector on the PLUGIN side (Step 1 loads the
    # plugin's own shipped schema first, before the durable comparison
    # ever runs) -- both reads flow through `_read_json_guarded`, so this
    # must HALT identically to the durable-side case above.
    fixture_script = make_fixture_plugin(tmp_path)
    current_doc = load_json(CANON_ENTRY_SCHEMA_PATH)
    text = _raw_text_with_duplicated_basis_key(current_doc)
    fixture_schemas_dir = fixture_script.parent.parent / "schemas"
    (fixture_schemas_dir / "canon-entry.schema.json").write_text(text, encoding="utf-8")

    durable_root = make_current_durable_root(tmp_path)

    result = run_preflight_with_script(fixture_script, durable_root)
    assert_halts(result, contains=("canon-entry.schema.json",))


# ---------------------------------------------------------------------------
# CLI-contract sanity: exit codes are never 1 (reserved for
# canon_validate.py's data-validation failures), and a totally-absent
# durable_root path itself is handled the same as absent schemas/ -- never
# a crash.
# ---------------------------------------------------------------------------


def test_completely_nonexistent_durable_root_halts_actionably(tmp_path):
    result = run_preflight(tmp_path / "does_not_exist_at_all")
    assert_halts(result)


def test_missing_required_flag_exits_nonzero_and_never_zero_or_one():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode not in (0, 1)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
