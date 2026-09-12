"""tests/entity_markup_untaggable.test.py -- regression-lock suite for
scripts/entity_markup_untaggable.py (#932): the W3a/W5 report of which
canon.json targets `assemble.py` will refuse as a marked entity span's
label at W7 (`_entity_markup_canon_collision_preflight`,
`RenderError("entity_markup_canon_collision")`, #837).

## Fixture strategy

Every test builds a REAL, self-contained `durable_root` on disk and invokes
the ACTUAL `entity_markup_untaggable.py` as a subprocess (`sys.executable`,
never bare `"python3"`), exactly the way Step 0a places it
(`{durable_root}/scripts/entity_markup_untaggable.py`), so its
`Path(__file__)`-based self-anchoring resolves against the fixture. The
fixture stages the REAL shipped siblings the script imports --
`render_obsidian.py`, `canon_link_groups.py`, `assemble.py`,
`validate_draft.py`, `output_resolve.py`, `cache_key.py`, `json_stdout.py`
-- copied byte-for-byte, never stubs, matching `tests/final_audit.test.py`'s
own discipline (what this file proves is that
`entity_markup_untaggable.py` correctly INTEGRATES with the real, currently
shipped `render_obsidian`/`assemble`/`canon_link_groups` predicates, not a
hand-maintained stand-in that could quietly drift from them). Deliberately
NOT copied: `scaffold_setup.py` (a real durable_root never has it beside the
other scripts -- see `entity_markup_untaggable.py`'s own docstring on why
its atomic-publish routine is a copy, not an import).
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
)
SCHEMAS_SRC_DIR = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"
)

SCRIPT_SRC = SCRIPTS_SRC_DIR / "entity_markup_untaggable.py"
assert SCRIPT_SRC.is_file(), f"entity_markup_untaggable.py not found at {SCRIPT_SRC}"

# Every real script entity_markup_untaggable.py depends on, directly (module
# import) or as a real sibling its own imports expect to find beside it.
SCRIPTS_TO_COPY = (
    "entity_markup_untaggable.py",
    "render_obsidian.py",
    "canon_link_groups.py",
    "assemble.py",
    "validate_draft.py",
    "output_resolve.py",
    "cache_key.py",
    "json_stdout.py",
)
for _name in SCRIPTS_TO_COPY:
    assert (SCRIPTS_SRC_DIR / _name).is_file(), f"{_name} not found at {SCRIPTS_SRC_DIR}"

OUTPUT_NAME = "entity_markup_untaggable.json"

# Every chmod(0o000) case below: 0o000 is ignored by root, and os.geteuid()
# does not exist on Windows.
_needs_permission_bits = pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0,
    reason="permission bits are not meaningful as root/on Windows",
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def default_profile(entity_markup=None, output_target="obsidian"):
    """Shaped like `tests/final_audit.test.py::default_profile` -- the
    fields `validate_draft.load_profile()`'s callers generally expect --
    plus the `output` block this script's own predicate actually reads.
    `entity_markup=None` omits `output.entity_markup` entirely (mode
    `off`); a dict declares the block verbatim (the caller decides
    `index_from`/`tags`/`ref_attribute`)."""
    profile = {
        "project": {"pipeline_version": "v1"},
        "engine": {"effort": "high", "max_fix_rounds": 4, "batch_agent_cap": 1000},
        "source": {
            "format": "plain_text",
            "path": "/logical/source.txt",
            "language": {"code": "fr", "particle_config": "fr_test.json"},
        },
        "target": {"language": {"code": "ru"}},
        "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
        "footnotes": {"apparatus_policy": "translate_all"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
        "output": {"target": output_target, "v1_scope": "assembled_book"},
    }
    if entity_markup is not None:
        profile["output"]["entity_markup"] = entity_markup
    return profile


def make_durable_root(tmp_path, profile=None, canon=None, link_groups=None, name="durable_root"):
    """A complete `durable_root`: real copies of every script
    `entity_markup_untaggable.py` touches, the real `schemas/` tree (the
    canon-link-groups schema `canon_link_groups.py` self-anchors to), an
    ownership marker + `profile.yml`, and `canon.json`. `link_groups`, when
    given, writes `canon_link_groups.json`; when omitted, no sidecar file
    exists at all (mode `off`/`strip` never touch it either way)."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for script_name in SCRIPTS_TO_COPY:
        shutil.copy2(SCRIPTS_SRC_DIR / script_name, scripts_dir / script_name)

    shutil.copytree(SCHEMAS_SRC_DIR, root / "schemas")

    (root / "profile.yml").write_text(
        yaml.safe_dump(profile if profile is not None else default_profile(), sort_keys=False),
        encoding="utf-8",
    )
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )
    (root / "canon.json").write_text(
        json.dumps(canon if canon is not None else {"entries": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    if link_groups is not None:
        (root / "canon_link_groups.json").write_text(
            json.dumps(link_groups, ensure_ascii=False), encoding="utf-8"
        )
    return root


def canon_entry(target, basis="established", category=None, source="https://example.invalid/x"):
    entry = {
        "is_proper_name": True,
        "canonical_target_form": target,
        "basis": basis,
        "confidence": "high",
    }
    if basis == "established":
        entry["source"] = source
    if category is not None:
        entry["category"] = category
    return entry


def run_script(root):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "entity_markup_untaggable.py")],
        capture_output=True,
        text=True,
        timeout=90,
    )


def parse_one_line(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line on stdout, got {len(lines)}: "
        f"{proc.stdout!r}\nstderr: {proc.stderr}"
    )
    return json.loads(lines[0])


# ---------------------------------------------------------------------------
# Case 1-2: off / strip -- no canon read, no file, exit 0.
# ---------------------------------------------------------------------------


def test_mode_off_no_block_writes_nothing(tmp_path):
    root = make_durable_root(tmp_path, profile=default_profile(entity_markup=None))
    proc = run_script(root)
    assert proc.returncode == 0, proc.stderr
    doc = parse_one_line(proc)
    assert doc == {
        "success": True,
        "applicable": False,
        "entity_markup_mode": "off",
        "path": str(root / OUTPUT_NAME),
        "file_present": False,
        "untaggable_count": 0,
    }, proc.stderr
    assert not (root / OUTPUT_NAME).exists(), proc.stderr


def test_mode_strip_writes_nothing(tmp_path):
    root = make_durable_root(
        tmp_path,
        profile=default_profile(entity_markup={"tags": ["person"]}),  # index_from absent -> strip
    )
    proc = run_script(root)
    assert proc.returncode == 0, proc.stderr
    doc = parse_one_line(proc)
    assert doc["applicable"] is False, proc.stderr
    assert doc["entity_markup_mode"] == "strip", proc.stderr
    assert not (root / OUTPUT_NAME).exists(), proc.stderr


def test_mode_off_leaves_stale_file_in_place_with_note(tmp_path):
    """A stale file from a prior `index` run, left after the profile moved
    to `off`/`strip`, is untouched -- SKILL.md tells the operator to delete
    it if the mode change is deliberate."""
    root = make_durable_root(tmp_path, profile=default_profile(entity_markup=None))
    stale = {"generated_by": "entity_markup_untaggable.py", "tags": [], "untaggable": []}
    (root / OUTPUT_NAME).write_text(json.dumps(stale), encoding="utf-8")
    proc = run_script(root)
    assert proc.returncode == 0, proc.stderr
    doc = parse_one_line(proc)
    assert doc["file_present"] is True, proc.stderr
    assert "left in place" in proc.stderr, proc.stderr
    assert (root / OUTPUT_NAME).read_text(encoding="utf-8") == json.dumps(stale), (
        "the stale file's bytes must be untouched"
    )


# ---------------------------------------------------------------------------
# Case 3: index mode -- the six-target matrix.
# ---------------------------------------------------------------------------


def _case3_canon():
    return {
        "entries": {
            "a1": canon_entry("Target A"),
            "b1": canon_entry("Target B"),
            "b2": canon_entry("Target B"),
            "c1": canon_entry("Target C", basis="established"),
            "c2": canon_entry("Target C", basis="sense_translated"),
            "d1": canon_entry("Target D", category="place"),
            "d2": canon_entry("Target D", category="person"),
            "e1": canon_entry("Target E"),
            "e2": canon_entry("Target E"),
            "f1": canon_entry("Target F", basis="established"),
            "f2": canon_entry("Target F", basis="sense_translated"),
        }
    }


def _case3_link_groups():
    return {
        "schema_version": 1,
        "groups": [
            {"primary": "e1", "members": ["e1", "e2"], "note": "one referent, established (#932 test)"},
            {"primary": "f1", "members": ["f1", "f2"], "note": "one referent incl. a sense-translated form (#932 test)"},
        ],
    }


def test_index_mode_untaggable_matrix(tmp_path):
    root = make_durable_root(
        tmp_path,
        profile=default_profile(
            entity_markup={"tags": ["person", "place"], "index_from": "markup"}
        ),
        canon=_case3_canon(),
        link_groups=_case3_link_groups(),
    )
    proc = run_script(root)
    assert proc.returncode == 0, proc.stderr
    doc = parse_one_line(proc)
    assert doc["applicable"] is True, proc.stderr
    assert doc["entity_markup_mode"] == "index", proc.stderr
    assert doc["tags"] == ["person", "place"], proc.stderr
    assert "untaggable" not in doc, "rows must never be echoed on stdout"

    out_path = root / OUTPUT_NAME
    assert out_path.is_file(), proc.stderr
    published_mode = stat.S_IMODE(out_path.stat().st_mode)
    assert published_mode == 0o600, (
        f"the published file must carry the temp file's own 0600 mode "
        f"(scaffold_setup.atomic_write_text's own discipline, os.replace() "
        f"never widens it), got {oct(published_mode)}"
    )
    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk["generated_by"] == "entity_markup_untaggable.py"
    assert on_disk["tags"] == ["person", "place"]

    rows = on_disk["untaggable"]
    assert doc["untaggable_count"] == len(rows), proc.stderr
    targets = [row["target"] for row in rows]
    # Row order: by target.
    assert targets == sorted(targets), f"rows must be sorted by target: {targets}"

    by_target = {row["target"]: row for row in rows}
    assert set(by_target) == {"Target B", "Target C", "Target F"}, (
        f"Target A (single owner), Target D (no tag has >=2 compatible "
        f"owners) and Target E (fully link-grouped, no sense_translated "
        f"member) must NOT be listed; got {sorted(by_target)}"
    )

    row_b = by_target["Target B"]
    assert row_b["tags"] == ["person", "place"], row_b
    assert row_b["owners"] == ["b1", "b2"], row_b
    assert row_b["sense_translated_owners"] == [], row_b

    row_c = by_target["Target C"]
    assert row_c["tags"] == ["person", "place"], row_c
    assert row_c["owners"] == ["c1", "c2"], row_c
    assert row_c["sense_translated_owners"] == ["c2"], row_c

    row_f = by_target["Target F"]
    assert row_f["tags"] == ["person", "place"], row_f
    assert row_f["owners"] == ["f1", "f2"], row_f
    assert row_f["sense_translated_owners"] == ["f2"], row_f


def test_index_mode_target_forms_are_nfc_normalized(tmp_path):
    """A `canonical_target_form` stored in decomposed (NFD) form must
    collapse onto the same NFC target key as its composed spelling --
    matching `render_obsidian._owners_by_target`'s own NFC normalization."""
    composed = "Khétam"  # single-codepoint 'é'
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    canon = {
        "entries": {
            "x1": canon_entry(composed),
            "x2": canon_entry(decomposed),
        }
    }
    root = make_durable_root(
        tmp_path,
        profile=default_profile(entity_markup={"tags": ["person"], "index_from": "markup"}),
        canon=canon,
    )
    proc = run_script(root)
    assert proc.returncode == 0, proc.stderr
    on_disk = json.loads((root / OUTPUT_NAME).read_text(encoding="utf-8"))
    assert len(on_disk["untaggable"]) == 1, on_disk
    assert on_disk["untaggable"][0]["target"] == composed, on_disk


def test_index_mode_no_collisions_writes_empty_list(tmp_path):
    canon = {"entries": {"a1": canon_entry("Solo Target")}}
    root = make_durable_root(
        tmp_path,
        profile=default_profile(entity_markup={"tags": ["person"], "index_from": "markup"}),
        canon=canon,
    )
    proc = run_script(root)
    assert proc.returncode == 0, proc.stderr
    doc = parse_one_line(proc)
    assert doc["untaggable_count"] == 0, proc.stderr
    on_disk = json.loads((root / OUTPUT_NAME).read_text(encoding="utf-8"))
    assert on_disk["untaggable"] == [], on_disk


# ---------------------------------------------------------------------------
# Case 4: invalid output.entity_markup block -> exit 2, no file.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        {"tags": "person", "index_from": "canon"},  # bare string tags
        {"tags": ["person", "person"], "index_from": "canon"},  # duplicate
        {"tags": ["person"], "ref_attribute": None, "index_from": "canon"},  # null ref_attribute
    ],
    ids=["bare_string_tags", "duplicate_tags", "null_ref_attribute"],
)
def test_invalid_block_refused_even_under_strip_mode(tmp_path, block):
    """The validator runs BEFORE the mode is resolved, so a malformed
    block under `index_from: canon` (strip mode) is refused, not silently
    reported 'not applicable'."""
    root = make_durable_root(
        tmp_path, profile=default_profile(entity_markup=block)
    )
    proc = run_script(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == "", "no stdout JSON on a fatal error"
    assert "entity_markup_config_invalid" in proc.stderr, proc.stderr
    assert not (root / OUTPUT_NAME).exists()


def test_index_from_markup_under_unsupported_target_refused(tmp_path):
    root = make_durable_root(
        tmp_path,
        profile=default_profile(
            entity_markup={"tags": ["person"], "index_from": "markup"},
            output_target="epub",
        ),
    )
    proc = run_script(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert "entity_markup_index_unsupported_target" in proc.stderr, proc.stderr
    assert not (root / OUTPUT_NAME).exists()


# ---------------------------------------------------------------------------
# Case 4b: a malformed canon.json -> exit 2, reason canon_invalid, no
# traceback, no file.
# ---------------------------------------------------------------------------


def _index_profile():
    return default_profile(entity_markup={"tags": ["person"], "index_from": "markup"})


def test_non_string_canonical_target_form_is_canon_invalid(tmp_path):
    canon = {"entries": {"x1": {"is_proper_name": True, "canonical_target_form": 7, "basis": "established", "confidence": "high", "source": "https://example.invalid"}}}
    root = make_durable_root(tmp_path, profile=_index_profile(), canon=canon)
    proc = run_script(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert "canon_invalid" in proc.stderr, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert not (root / OUTPUT_NAME).exists()


def test_escaped_lone_surrogate_target_is_canon_invalid(tmp_path):
    """A JSON-escaped lone surrogate is valid JSON and survives parsing and
    every renderer helper untouched -- it fails only when this script's own
    boundary `.encode('utf-8')`s the payload as a validation step (the same
    hole `claim_record.py`'s own `write_claim_record()` documents and fixes
    on its write path: `UnicodeEncodeError` is a `ValueError`, not an
    `OSError`)."""
    root = make_durable_root(tmp_path, profile=_index_profile(), canon={"entries": {}})
    canon_raw = '{"entries": {"x1": {"is_proper_name": true, "canonical_target_form": "\\ud800", "basis": "established", "confidence": "high", "source": "https://example.invalid"}, "x2": {"is_proper_name": true, "canonical_target_form": "\\ud800", "basis": "established", "confidence": "high", "source": "https://example.invalid"}}}'
    (root / "canon.json").write_text(canon_raw, encoding="utf-8")
    proc = run_script(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert "canon_invalid" in proc.stderr, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert not (root / OUTPUT_NAME).exists()


@_needs_permission_bits
def test_unreadable_canon_json_is_canon_invalid(tmp_path):
    root = make_durable_root(tmp_path, profile=_index_profile(), canon={"entries": {}})
    canon_path = root / "canon.json"
    canon_path.chmod(0o000)
    try:
        proc = run_script(root)
    finally:
        canon_path.chmod(0o644)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert "canon_invalid" in proc.stderr, proc.stderr
    assert not (root / OUTPUT_NAME).exists()


# ---------------------------------------------------------------------------
# Case 4c: a symlink pre-planted at the output path -> refused, symlink's
# own target untouched.
# ---------------------------------------------------------------------------


def test_preplanted_symlink_at_output_path_is_refused(tmp_path):
    root = make_durable_root(
        tmp_path, profile=_index_profile(), canon={"entries": {"a1": canon_entry("T"), "a2": canon_entry("T")}}
    )
    decoy = tmp_path / "decoy.json"
    decoy.write_text('{"untouched": true}', encoding="utf-8")
    (root / OUTPUT_NAME).symlink_to(decoy)

    proc = run_script(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert "symlink" in proc.stderr.lower(), proc.stderr
    assert decoy.read_text(encoding="utf-8") == '{"untouched": true}', (
        "the symlink's own target must be left byte-for-byte untouched"
    )
    assert (root / OUTPUT_NAME).is_symlink(), "the symlink itself must not be replaced"


# ---------------------------------------------------------------------------
# Case 5: malformed / missing canon_link_groups.json.
# ---------------------------------------------------------------------------


def test_malformed_link_groups_sidecar_is_refused(tmp_path):
    root = make_durable_root(
        tmp_path,
        profile=_index_profile(),
        canon={"entries": {"a1": canon_entry("T"), "a2": canon_entry("T")}},
    )
    (root / "canon_link_groups.json").write_text("not json at all", encoding="utf-8")
    proc = run_script(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert "canon_link_groups_invalid" in proc.stderr, proc.stderr
    assert not (root / OUTPUT_NAME).exists()


def test_link_groups_sidecar_present_but_module_missing_is_dependency_precondition(tmp_path):
    root = make_durable_root(
        tmp_path,
        profile=_index_profile(),
        canon={"entries": {"a1": canon_entry("T"), "a2": canon_entry("T")}},
        link_groups={"schema_version": 1, "groups": []},
    )
    (root / "scripts" / "canon_link_groups.py").unlink()
    proc = run_script(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert "dependency_precondition" in proc.stderr, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert not (root / OUTPUT_NAME).exists()


@_needs_permission_bits
def test_unreadable_assemble_py_is_dependency_precondition(tmp_path):
    """`assemble.py` EXISTING but unreadable (permission bits) raises
    `PermissionError` -- an `OSError` -- from the import machinery itself,
    a different failure than "not found" but the same
    `dependency_precondition` report to the caller (`_import_assemble()`
    catches `OSError` alongside `ImportError`/`SystemExit` for exactly
    this)."""
    root = make_durable_root(
        tmp_path,
        profile=_index_profile(),
        canon={"entries": {"a1": canon_entry("T"), "a2": canon_entry("T")}},
    )
    assemble_path = root / "scripts" / "assemble.py"
    assemble_path.chmod(0o000)
    try:
        proc = run_script(root)
    finally:
        assemble_path.chmod(0o644)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert "dependency_precondition" in proc.stderr, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert not (root / OUTPUT_NAME).exists()


@_needs_permission_bits
def test_unreadable_validate_draft_py_is_dependency_precondition(tmp_path):
    """`validate_draft.py` EXISTING but unreadable: main()'s own guarded
    import routes the `OSError` through fatal() with the same
    `dependency_precondition` reason the lazy imports report, so the
    machine-readable reason contract holds for every sibling this script
    loads, not only the ones inside resolve_untaggable()."""
    root = make_durable_root(
        tmp_path,
        profile=_index_profile(),
        canon={"entries": {"a1": canon_entry("T"), "a2": canon_entry("T")}},
    )
    target = root / "scripts" / "validate_draft.py"
    target.chmod(0o000)
    try:
        proc = run_script(root)
    finally:
        target.chmod(0o644)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert "dependency_precondition" in proc.stderr, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert not (root / OUTPUT_NAME).exists()


@_needs_permission_bits
def test_unreadable_canon_link_groups_py_is_dependency_precondition(tmp_path):
    """The sidecar IS present, but `canon_link_groups.py` itself is
    unreadable -- the `OSError` branch of the lazy `import
    canon_link_groups` inside `resolve_untaggable()`, distinct from the
    module simply being absent (already covered above)."""
    root = make_durable_root(
        tmp_path,
        profile=_index_profile(),
        canon={"entries": {"a1": canon_entry("T"), "a2": canon_entry("T")}},
        link_groups={"schema_version": 1, "groups": []},
    )
    clg_path = root / "scripts" / "canon_link_groups.py"
    clg_path.chmod(0o000)
    try:
        proc = run_script(root)
    finally:
        clg_path.chmod(0o644)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert "dependency_precondition" in proc.stderr, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert not (root / OUTPUT_NAME).exists()


@_needs_permission_bits
def test_unreadable_render_obsidian_py_is_dependency_precondition(tmp_path):
    """`render_obsidian.py` is a hard, module-level dependency (unlike
    `assemble.py`/`canon_link_groups.py`, imported lazily) -- an existing
    but unreadable copy must still be reported as `dependency_precondition`
    through `fatal()`, never a bare `PermissionError` traceback."""
    root = make_durable_root(
        tmp_path,
        profile=_index_profile(),
        canon={"entries": {"a1": canon_entry("T"), "a2": canon_entry("T")}},
    )
    ro_path = root / "scripts" / "render_obsidian.py"
    ro_path.chmod(0o000)
    try:
        proc = run_script(root)
    finally:
        ro_path.chmod(0o644)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert "dependency_precondition" in proc.stderr, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert not (root / OUTPUT_NAME).exists()


# ---------------------------------------------------------------------------
# Case 6: idempotent re-run; a stale file is replaced.
# ---------------------------------------------------------------------------


def test_rerun_is_byte_identical_and_replaces_stale_file(tmp_path):
    root = make_durable_root(
        tmp_path,
        profile=_index_profile(),
        canon={"entries": {"a1": canon_entry("T"), "a2": canon_entry("T")}},
    )
    (root / OUTPUT_NAME).write_text('{"generated_by": "stale", "tags": [], "untaggable": []}', encoding="utf-8")

    proc1 = run_script(root)
    assert proc1.returncode == 0, proc1.stderr
    first_bytes = (root / OUTPUT_NAME).read_bytes()
    assert b"stale" not in first_bytes

    proc2 = run_script(root)
    assert proc2.returncode == 0, proc2.stderr
    second_bytes = (root / OUTPUT_NAME).read_bytes()
    assert first_bytes == second_bytes, "a pure function of unchanged inputs must re-publish byte-identical output"


# ---------------------------------------------------------------------------
# Case 7: parity with the shipped render_obsidian predicate -- for every
# multi-owner target, the script's verdict (listed / not listed) must match
# an independent call to the real render_obsidian helpers.
# ---------------------------------------------------------------------------


def test_parity_with_render_obsidian_predicate_directly(tmp_path):
    """Measured parity, not a reconstruction: build ONE synthetic marked
    span per (declared tag, every canon target) -- the exact shape
    `render_obsidian._entity_markup_identity` reads (`tag` +
    `payload` as the label, no `ref`) -- and call the renderer's OWN
    render-time twin, `_canon_collision_conflicts`, directly. The set of
    `(label, tag)` pairs it flags, and each row's `owners`, must equal what
    this script wrote to its own output file."""
    import importlib.util

    canon = _case3_canon()
    link_groups = _case3_link_groups()
    tags = ["person", "place"]
    root = make_durable_root(
        tmp_path,
        profile=default_profile(entity_markup={"tags": tags, "index_from": "markup"}),
        canon=canon,
        link_groups=link_groups,
    )
    proc = run_script(root)
    assert proc.returncode == 0, proc.stderr
    on_disk = json.loads((root / OUTPUT_NAME).read_text(encoding="utf-8"))
    rows = on_disk["untaggable"]

    # Load the FIXTURE's own render_obsidian.py / canon_link_groups.py
    # copies directly (not the plugin-source ones) so this parity check is
    # against the exact bytes the script under test actually called.
    ro_path = root / "scripts" / "render_obsidian.py"
    spec = importlib.util.spec_from_file_location("ro_parity_check", ro_path)
    ro = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ro)

    clg_path = root / "scripts" / "canon_link_groups.py"
    spec2 = importlib.util.spec_from_file_location("clg_parity_check", clg_path)
    clg = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(clg)

    entries = canon["entries"]
    primary = clg.load_link_groups(root / "canon_link_groups.json", entries)

    # ONE synthetic span per (declared tag, every canon target), covering
    # single-owner targets too so the renderer's own `< 2 owners` and
    # `< 2 compatible owners` short-circuits are exercised identically to
    # this script's own `untaggable_rows()`.
    owners_by_target = ro._owners_by_target(entries)
    spans = {
        f"span_{i}": {"tag": tag, "payload": target}
        for i, (target, tag) in enumerate(
            (target, tag) for target in owners_by_target for tag in tags
        )
    }
    conflicts = ro._canon_collision_conflicts(spans, entries, True, primary)

    expected_pairs = {(row["target"], tag) for row in rows for tag in row["tags"]}
    actual_pairs = {(c["label"], c["tag"]) for c in conflicts}
    assert actual_pairs == expected_pairs, (
        f"script rows vs render_obsidian._canon_collision_conflicts diverge: "
        f"script-only={expected_pairs - actual_pairs} "
        f"renderer-only={actual_pairs - expected_pairs}"
    )

    conflict_owners = {(c["label"], c["tag"]): c["owners"] for c in conflicts}
    for row in rows:
        for tag in row["tags"]:
            assert conflict_owners[(row["target"], tag)] == row["owners"], (
                f"{row['target']}/{tag}: script owners {row['owners']} != "
                f"renderer owners {conflict_owners[(row['target'], tag)]}"
            )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
