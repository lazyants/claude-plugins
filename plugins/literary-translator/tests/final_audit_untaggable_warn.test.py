"""tests/final_audit_untaggable_warn.test.py -- WARN (7) `untaggable-target`
in scripts/final_audit.py (#932): the same `entity_markup_canon_collision`
predicate `assemble.py` enforces at the W7 render is recomputed LIVE here,
so a translate/review turn learns about a colliding canon target before the
render refuses it, not only once it does.

Self-contained per this plugin's test convention (see
`.claude/skills/literary-translator-ops/references/plugin-facts.md`'s "Test
conventions" section) -- `tests/final_audit.test.py` is NOT imported from,
its own fixture-building shape is copied here instead, trimmed to what this
file's cases actually need: no converged segment is ever built (WARN (7) is
a declaration-level check, like the two lanes immediately above it in
`main()`), so `add_converged_segment()`/`cache_key.py` subprocess plumbing is
left out entirely. `manifest.json` still carries exactly one segment id (a
bare-list `select_segments.py` FATALs on -- "empty 'segments' array" is a
manifest-shape refusal, distinct from the empty-emitted-SEGS case
`--allow-empty` covers) that is never given a segpack/draft/ledger fragment,
so it classifies `not_started`; every fixture here therefore reports
`project_complete: false` and exits **3**, and every case below asserts that
SAME exit code regardless of how many WARN (7) lines fire -- proving the new
lane never perturbs the pre-existing exit contract.

## Cases (plan #932 section 5, second test file)

  (a) index mode, one colliding target, NO on-disk file -> per-target WARN
      + the "absent" WARN, warnings count +2.
  (b) the REAL `entity_markup_untaggable.py` is run as a subprocess to
      produce the file (the producer/consumer seam) -> per-target WARN only.
  (c) canon.json is then mutated to add a second owner under a NEW target,
      WITHOUT re-running the script -> the on-disk file is now STALE.
  (d) `output.entity_markup` absent, and the fixture deliberately has no
      `entity_markup_untaggable.py` at all -> no `UNTAGGABLE-TARGET` line
      (proves the no-import gate never even tries the sibling).
  (e) block declared, but `entity_markup_untaggable.py` deleted from the
      fixture -> exactly one "check skipped" WARN.
  (f) block declared, `canon_link_groups.json` sidecar present, but
      `canon_link_groups.py` deleted from the fixture -> one "could not
      resolve" WARN (reason dependency_precondition).
  (g) on-disk file holds a JSON array (`[]`), not an object -> one
      "unreadable" WARN.
  (h) a canon entry's `canonical_target_form` is a bare number -> one "could
      not resolve" WARN (reason canon_invalid), summary JSON still printed.
  (i) `entity_markup_untaggable.json` is a symlink to a regular file holding
      the CORRECT rows -> one "unreadable" WARN (lstat classification,
      never followed), never silently accepted and never "STALE".

Every case asserts the audit still prints exactly one JSON summary line and
that its exit code is the fixture's ordinary one (3) -- the new lane must
never crash the whole audit or move the exit code on its own.
"""
import json
import os
import shutil
import subprocess
import sys
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

FINAL_AUDIT_SRC = SCRIPTS_SRC_DIR / "final_audit.py"
assert FINAL_AUDIT_SRC.is_file(), f"final_audit.py not found at {FINAL_AUDIT_SRC}"

# Every real script final_audit.py itself needs, directly or transitively --
# same set tests/final_audit.test.py copies, minus the pieces this file's
# cases never exercise (no converged segment is ever built here).
BASE_SCRIPTS = (
    "final_audit.py",
    "validate_draft.py",
    "bootstrap_names.py",
    "select_segments.py",
    "ledger_merge.py",
    "cache_key.py",
)
for _name in BASE_SCRIPTS:
    assert (SCRIPTS_SRC_DIR / _name).is_file(), f"{_name} not found at {SCRIPTS_SRC_DIR}"

# The siblings `entity_markup_untaggable.py` itself needs at runtime (per
# #932's plan, section 1): `assemble.py` (module-level `import validate_draft`/
# `import output_resolve`, both already in BASE_SCRIPTS/copied separately;
# `import cache_key` likewise already in BASE_SCRIPTS), `render_obsidian.py`
# (`_owners_by_target`/`_link_decision`/`_category_compatible`), and
# `canon_link_groups.py` (lazy import, only reached when the sidecar file
# exists on disk).
ENTITY_MARKUP_SCRIPTS = ("render_obsidian.py", "assemble.py", "output_resolve.py")
CANON_LINK_GROUPS_SCRIPT = "canon_link_groups.py"
UNTAGGABLE_SCRIPT = "entity_markup_untaggable.py"
OUTPUT_NAME = "entity_markup_untaggable.json"
for _name in (*ENTITY_MARKUP_SCRIPTS, CANON_LINK_GROUPS_SCRIPT, UNTAGGABLE_SCRIPT):
    assert (SCRIPTS_SRC_DIR / _name).is_file(), f"{_name} not found at {SCRIPTS_SRC_DIR}"


def default_profile(entity_markup=None):
    """Mirrors tests/final_audit.test.py::default_profile's shape (the same
    base fields every hard/WARN check needs), plus an optional
    `output.entity_markup` block. `entity_markup`, when given, is written
    under `output.target: obsidian` / `output.v1_scope: assembled_book` --
    the only shape `index_from: markup` is not refused under (#837)."""
    profile = {
        "project": {"pipeline_version": "v1"},
        "engine": {"effort": "high", "max_fix_rounds": 4, "batch_agent_cap": 1000},
        "source": {
            "format": "plain_text",
            "path": "/logical/source.txt",
            "language": {"code": "fr", "particle_config": "fr_test.json"},
            "adapter_config": {
                "plain_text": {
                    "segmentation": {"method": "blank_line_run", "blank_line_threshold": 2}
                },
                "gutenberg_epub": {},
                "custom": {},
            },
        },
        "target": {"language": {"code": "ru"}},
        "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
        "footnotes": {"apparatus_policy": "translate_all"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
    }
    if entity_markup is not None:
        profile["output"] = {
            "v1_scope": "assembled_book",
            "destination": "vault",
            "target": "obsidian",
            "entity_markup": entity_markup,
        }
    return profile


def make_durable_root(
    tmp_path,
    entity_markup=None,
    canon=None,
    canon_link_groups_doc=None,
    include_untaggable_script=False,
    include_link_groups_script=True,
) -> Path:
    """A minimal-but-complete durable_root: real copies of every script
    final_audit.py touches (`BASE_SCRIPTS`), plus -- only when
    `entity_markup` is given, matching how WARN (7) itself gates on the
    declared block before importing anything -- the siblings
    `entity_markup_untaggable.py` needs. `manifest.json` carries exactly one
    segment id that is never given a segpack/draft/ledger fragment (WARN (7)
    is declaration-level, so no converged segment is needed to exercise it);
    it classifies `not_started`, so every fixture here reports
    `project_complete: false` and exits exactly 3, giving every case the
    same baseline exit code to assert against.
    """
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    pad_seg = "zz_not_started_pad"
    for name in BASE_SCRIPTS:
        shutil.copy2(SCRIPTS_SRC_DIR / name, scripts_dir / name)
    shutil.copy2(SCRIPTS_SRC_DIR / "json_stdout.py", scripts_dir / "json_stdout.py")
    # cache_key.py's derivation_bundle_hash hashes this file's raw bytes --
    # content is irrelevant, only needs to exist (same placeholder
    # tests/final_audit.test.py's own make_durable_root writes).
    (scripts_dir / "segpack.py").write_bytes(b"# segpack.py fixture placeholder\n")

    if entity_markup is not None:
        for name in ENTITY_MARKUP_SCRIPTS:
            shutil.copy2(SCRIPTS_SRC_DIR / name, scripts_dir / name)
        if include_link_groups_script:
            shutil.copy2(
                SCRIPTS_SRC_DIR / CANON_LINK_GROUPS_SCRIPT,
                scripts_dir / CANON_LINK_GROUPS_SCRIPT,
            )
        if include_untaggable_script:
            shutil.copy2(SCRIPTS_SRC_DIR / UNTAGGABLE_SCRIPT, scripts_dir / UNTAGGABLE_SCRIPT)

    (root / "profile.yml").write_text(
        yaml.safe_dump(default_profile(entity_markup=entity_markup), sort_keys=False),
        encoding="utf-8",
    )
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )

    (root / "style_bible.md").write_bytes(
        b"# Style Bible\n\n<!-- STYLE_CONTRACT_BEGIN -->\n"
        b"Formal register, Oxford comma.\n<!-- STYLE_CONTRACT_END -->\n"
    )
    (root / "translate_TASK.md").write_bytes(b"TRANSLATE TASK PROMPT v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW TASK PROMPT v1\n")
    (root / "extract.py").write_bytes(b"# extract.py fixture v1\n")

    shutil.copytree(SCHEMAS_SRC_DIR, root / "schemas")

    languages_dir = root / "languages"
    languages_dir.mkdir()
    (languages_dir / "fr_test.json").write_text(
        json.dumps(
            {
                "PARTICLES": ["de", "du", "des"],
                "STOPWORDS": ["de", "la", "le", "et", "un", "une", "des", "du", "les", "dans"],
                "has_elision": False,
                "ELISION_RE": None,
            }
        ),
        encoding="utf-8",
    )

    source_file = root / "source_original.txt"
    source_file.write_bytes(b"Ceci est un texte source de test.\n")

    manifest = {
        "source_inputs": [str(source_file.resolve())],
        "segments": [{"seg": pad_seg}],
        "frontback": [],
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    (root / "canon.json").write_text(
        json.dumps(canon if canon is not None else {"entries": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    if canon_link_groups_doc is not None:
        (root / "canon_link_groups.json").write_text(
            json.dumps(canon_link_groups_doc, ensure_ascii=False), encoding="utf-8"
        )

    runs_dir = root / "runs"
    runs_dir.mkdir()
    (runs_dir / ".plugin_bundle_hash").write_text("test-plugin-bundle-marker-v1\n", encoding="utf-8")

    (root / "segments").mkdir()

    return root


def run_final_audit(root: Path, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "final_audit.py")],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_untaggable_script(root: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / UNTAGGABLE_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_summary(proc: subprocess.CompletedProcess) -> dict:
    assert proc.stdout.strip(), (
        f"expected final_audit.py to print exactly one JSON line to stdout, "
        f"got nothing. stderr:\n{proc.stderr}"
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one stdout JSON line, got {len(lines)}:\n{proc.stdout}"
    return json.loads(lines[0])


def untaggable_lines(stderr: str):
    return [ln for ln in stderr.splitlines() if "UNTAGGABLE-TARGET" in ln]


COLLIDING_CANON = {
    "entries": {
        "Source A": {"canonical_target_form": "Shared Target", "basis": "established"},
        "Source B": {"canonical_target_form": "Shared Target", "basis": "established"},
    }
}

ENTITY_MARKUP_BLOCK = {"tags": ["person", "place"], "index_from": "markup"}


# ---------------------------------------------------------------------------
# (a) index mode, one collision, no on-disk file -> two WARN lines.
# ---------------------------------------------------------------------------


def test_collision_with_no_file_warns_twice(tmp_path):
    root = make_durable_root(
        tmp_path,
        entity_markup=ENTITY_MARKUP_BLOCK,
        canon=COLLIDING_CANON,
        include_untaggable_script=True,
    )
    assert not (root / OUTPUT_NAME).exists()

    result = run_final_audit(root)
    summary = parse_summary(result)

    lines = untaggable_lines(result.stderr)
    assert len(lines) == 2, f"expected 2 UNTAGGABLE-TARGET lines, got {len(lines)}:\n{result.stderr}"
    assert any("Shared Target" in ln and "owned by" in ln for ln in lines), result.stderr
    absent_line = next((ln for ln in lines if "is absent" in ln and OUTPUT_NAME in ln), None)
    assert absent_line is not None, result.stderr
    # #932 review round 1: the re-run command is ABSOLUTE (self-anchored to
    # this durable_root), never a bare relative path -- final_audit.py may
    # be invoked from any cwd.
    assert f"python3 {root}/scripts/{UNTAGGABLE_SCRIPT}" in absent_line, absent_line
    assert summary["warnings"] >= 2
    assert result.returncode == 3, result.stderr


# ---------------------------------------------------------------------------
# (a2) #932 review round 1: the "absent" WARN fires UNCONDITIONALLY in index
# mode, even when today's canon has no collision at all -- an absent file
# means no translate/review turn was told anything, independent of whether
# the live list happens to be empty right now.
# ---------------------------------------------------------------------------


def test_absent_file_warns_unconditionally_even_with_no_collision(tmp_path):
    root = make_durable_root(
        tmp_path,
        entity_markup=ENTITY_MARKUP_BLOCK,
        canon={"entries": {}},
        include_untaggable_script=True,
    )
    assert not (root / OUTPUT_NAME).exists()

    result = run_final_audit(root)
    summary = parse_summary(result)

    lines = untaggable_lines(result.stderr)
    assert len(lines) == 1, f"expected exactly 1 UNTAGGABLE-TARGET line (absent only), got {len(lines)}:\n{result.stderr}"
    assert "is absent" in lines[0] and OUTPUT_NAME in lines[0], lines
    assert f"python3 {root}/scripts/{UNTAGGABLE_SCRIPT}" in lines[0], lines
    assert summary["warnings"] == 1
    assert result.returncode == 3, result.stderr


def test_malformed_block_reaches_the_resolver_as_config_invalid(tmp_path):
    """The no-import gate keys on the `entity_markup` KEY being absent, never
    on its value being a mapping (ped-ant on PR #953): `entity_markup: person`
    -- a bare string, the hand edit `assemble.py` refuses as
    `entity_markup_config_invalid` -- must reach `resolve_untaggable()` and
    surface as its one "could not resolve" WARN, not read as "nothing
    declared" and vanish from a lane whose whole point is saying so early."""
    root = make_durable_root(
        tmp_path,
        entity_markup="person",
        canon={"entries": {}},
        include_untaggable_script=True,
    )

    result = run_final_audit(root)
    summary = parse_summary(result)

    lines = untaggable_lines(result.stderr)
    assert len(lines) == 1, f"expected exactly 1 UNTAGGABLE-TARGET line, got {len(lines)}:\n{result.stderr}"
    assert "could not resolve" in lines[0], lines
    assert "entity_markup_config_invalid" in lines[0], lines
    assert summary["warnings"] == 1
    assert result.returncode == 3, result.stderr


# ---------------------------------------------------------------------------
# (b)/(c) the producer/consumer seam: run the REAL script, then go stale.
# ---------------------------------------------------------------------------


def test_real_script_writes_file_then_goes_stale_after_canon_edit(tmp_path):
    root = make_durable_root(
        tmp_path,
        entity_markup=ENTITY_MARKUP_BLOCK,
        canon=COLLIDING_CANON,
        include_untaggable_script=True,
    )

    # (b) The producer/consumer seam: the REAL entity_markup_untaggable.py,
    # run as a subprocess, is what final_audit.py's WARN (7) then reads back.
    producer = run_untaggable_script(root)
    assert producer.returncode == 0, (
        f"entity_markup_untaggable.py failed:\nstdout:\n{producer.stdout}\n"
        f"stderr:\n{producer.stderr}"
    )
    assert (root / OUTPUT_NAME).is_file()

    result = run_final_audit(root)
    lines = untaggable_lines(result.stderr)
    assert len(lines) == 1, f"expected exactly 1 UNTAGGABLE-TARGET line (per-target only), got {len(lines)}:\n{result.stderr}"
    assert "Shared Target" in lines[0] and "owned by" in lines[0]
    assert not any("STALE" in ln or "is absent" in ln for ln in lines)
    assert result.returncode == 3, result.stderr

    # (c) canon.json is mutated to add a second owner under a NEW target,
    # WITHOUT re-running the script -- the on-disk file is now stale against
    # the live list.
    canon = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    canon["entries"]["Source C"] = {"canonical_target_form": "Second Target", "basis": "established"}
    canon["entries"]["Source D"] = {"canonical_target_form": "Second Target", "basis": "established"}
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")

    result2 = run_final_audit(root)
    lines2 = untaggable_lines(result2.stderr)
    stale_line = next((ln for ln in lines2 if "STALE" in ln and OUTPUT_NAME in ln), None)
    assert stale_line is not None, result2.stderr
    assert f"python3 {root}/scripts/{UNTAGGABLE_SCRIPT}" in stale_line, stale_line
    # Both targets now collide live; the STALE line is IN ADDITION to the
    # per-target lines, never a replacement for them.
    assert any("Shared Target" in ln for ln in lines2), result2.stderr
    assert any("Second Target" in ln for ln in lines2), result2.stderr
    assert result2.returncode == 3, result2.stderr


# ---------------------------------------------------------------------------
# (d) block absent, fixture deliberately has no entity_markup_untaggable.py.
# ---------------------------------------------------------------------------


def test_block_absent_no_warn_no_import(tmp_path):
    root = make_durable_root(tmp_path, entity_markup=None)
    assert not (root / "scripts" / UNTAGGABLE_SCRIPT).exists()

    result = run_final_audit(root)
    summary = parse_summary(result)

    assert untaggable_lines(result.stderr) == []
    assert result.returncode == 3, result.stderr
    assert summary["warnings"] == 0


# ---------------------------------------------------------------------------
# (e) block declared, entity_markup_untaggable.py deleted from the fixture.
# ---------------------------------------------------------------------------


def test_script_missing_reports_check_skipped(tmp_path):
    root = make_durable_root(
        tmp_path,
        entity_markup=ENTITY_MARKUP_BLOCK,
        canon=COLLIDING_CANON,
        include_untaggable_script=False,
    )
    assert not (root / "scripts" / UNTAGGABLE_SCRIPT).exists()

    result = run_final_audit(root)
    summary = parse_summary(result)

    lines = untaggable_lines(result.stderr)
    assert len(lines) == 1, lines
    assert "check skipped" in lines[0] and "could not import" in lines[0], lines
    assert result.returncode == 3, result.stderr
    assert summary["warnings"] >= 1


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="0o000 is ignored by root, and os.geteuid() does not exist on Windows",
)
def test_unreadable_script_reports_check_skipped(tmp_path):
    """#932 review round 1: an entity_markup_untaggable.py that EXISTS but
    cannot be read raises PermissionError/OSError at import time, not
    ImportError -- the same "check skipped" WARN must fire, never an
    unhandled crash of this whole audit. Skipped as root (0o000 is ignored)
    and on Windows (no os.geteuid()) via a proper skipif marker, matching
    the same guard the script test uses -- an unguarded `if ...: return`
    would report a false-green PASS under either condition instead of a
    visible skip."""
    root = make_durable_root(
        tmp_path,
        entity_markup=ENTITY_MARKUP_BLOCK,
        canon=COLLIDING_CANON,
        include_untaggable_script=True,
    )
    (root / "scripts" / UNTAGGABLE_SCRIPT).chmod(0o000)
    try:
        result = run_final_audit(root)
    finally:
        (root / "scripts" / UNTAGGABLE_SCRIPT).chmod(0o644)

    summary = parse_summary(result)
    lines = untaggable_lines(result.stderr)
    assert len(lines) == 1, lines
    assert "check skipped" in lines[0] and "could not import" in lines[0], lines
    assert result.returncode == 3, result.stderr
    assert summary["warnings"] >= 1


# ---------------------------------------------------------------------------
# (f) sidecar present, canon_link_groups.py deleted from the fixture.
# ---------------------------------------------------------------------------


def test_link_groups_script_missing_reports_could_not_resolve(tmp_path):
    root = make_durable_root(
        tmp_path,
        entity_markup=ENTITY_MARKUP_BLOCK,
        canon=COLLIDING_CANON,
        canon_link_groups_doc={"groups": []},
        include_untaggable_script=True,
        include_link_groups_script=False,
    )
    assert (root / "canon_link_groups.json").is_file()
    assert not (root / "scripts" / CANON_LINK_GROUPS_SCRIPT).exists()

    result = run_final_audit(root)
    summary = parse_summary(result)

    lines = untaggable_lines(result.stderr)
    assert len(lines) == 1, lines
    assert "could not resolve" in lines[0], lines
    assert "reason=dependency_precondition" in lines[0], lines
    assert result.returncode == 3, result.stderr
    assert summary["warnings"] >= 1


# ---------------------------------------------------------------------------
# (g) on-disk file holds a JSON array, not an object.
# ---------------------------------------------------------------------------


def test_file_holding_a_list_is_unreadable(tmp_path):
    root = make_durable_root(
        tmp_path,
        entity_markup=ENTITY_MARKUP_BLOCK,
        canon={"entries": {}},
        include_untaggable_script=True,
    )
    (root / OUTPUT_NAME).write_text("[]", encoding="utf-8")

    result = run_final_audit(root)
    summary = parse_summary(result)

    lines = untaggable_lines(result.stderr)
    assert len(lines) == 1, lines
    assert "is unreadable" in lines[0] and OUTPUT_NAME in lines[0], lines
    assert f"python3 {root}/scripts/{UNTAGGABLE_SCRIPT}" in lines[0], lines[0]
    assert not any("STALE" in ln for ln in lines)
    assert result.returncode == 3, result.stderr
    assert summary["warnings"] >= 1


# ---------------------------------------------------------------------------
# (h) a canon entry's canonical_target_form is a bare number.
# ---------------------------------------------------------------------------


def test_numeric_canonical_target_form_reports_could_not_resolve(tmp_path):
    root = make_durable_root(
        tmp_path,
        entity_markup=ENTITY_MARKUP_BLOCK,
        canon={"entries": {"Source A": {"canonical_target_form": 7, "basis": "established"}}},
        include_untaggable_script=True,
    )

    result = run_final_audit(root)
    summary = parse_summary(result)

    lines = untaggable_lines(result.stderr)
    assert len(lines) == 1, lines
    assert "could not resolve" in lines[0], lines
    assert "reason=canon_invalid" in lines[0], lines
    assert result.returncode == 3, result.stderr
    assert summary["warnings"] >= 1
    # The summary JSON contract is unaffected by this WARN-only failure.
    assert "hard_failures" in summary and "project_complete" in summary


# ---------------------------------------------------------------------------
# (i) entity_markup_untaggable.json is a symlink to a regular, CORRECT file.
# ---------------------------------------------------------------------------


def test_symlinked_output_file_is_unreadable_never_stale(tmp_path):
    root = make_durable_root(
        tmp_path,
        entity_markup=ENTITY_MARKUP_BLOCK,
        canon={"entries": {}},
        include_untaggable_script=True,
    )
    real_file = tmp_path / "real_untaggable.json"
    real_file.write_text(
        json.dumps(
            {"generated_by": UNTAGGABLE_SCRIPT, "tags": ["person", "place"], "untaggable": []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.symlink(real_file, root / OUTPUT_NAME)
    assert (root / OUTPUT_NAME).is_symlink()

    result = run_final_audit(root)
    summary = parse_summary(result)

    lines = untaggable_lines(result.stderr)
    assert len(lines) == 1, lines
    assert "is unreadable" in lines[0] and OUTPUT_NAME in lines[0], lines
    assert f"python3 {root}/scripts/{UNTAGGABLE_SCRIPT}" in lines[0], lines[0]
    assert "STALE" not in lines[0]
    assert result.returncode == 3, result.stderr
    assert summary["warnings"] >= 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
