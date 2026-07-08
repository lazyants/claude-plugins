"""tests/diff_rendered_output.test.py -- scripts/diff_rendered_output.py,
the render+diff acceptance gate (see references/assembly-and-output.md's
"Render + diff" section, Phase 1; generalizes historiettes-t3's own
diff_rendered_output.py to a stdlib-only MARKDOWN line reducer).

## Invocation style

``python3 {durable_root}/scripts/diff_rendered_output.py --candidate-dir
<dir> [--accept-baseline [--force-accept-baseline]]``. ``--force-accept-
baseline`` was a genuine naming ambiguity between the shared contract's
own §11 (spells it out by that exact name) and §13 (shorthands it as just
"``--force``") -- CONFIRMED against the real, shipped script: it is
``--force-accept-baseline``, matching §11's literal spelling.

Diffs whatever sits under ``--candidate-dir`` (self-resolved from
profile.yml's ``output.destination`` when omitted -- but that default path
needs a real ownership marker, so every fixture here passes
``--candidate-dir`` explicitly instead, exactly as the real script's own
module docstring recommends for standalone/test invocations) against the
frozen baseline at the FIXED ``${durable_root}/out/.baseline/...`` (this
path is independent of ``--candidate-dir`` -- it never moves).

Every fixture is a bare ``durable_root`` with just ``scripts/
diff_rendered_output.py`` (the real, copied file) and a hand-populated
``out/`` tree standing in for whatever render_obsidian.py would have
produced -- this file's own scope is the diff/reduce/baseline machinery,
independent of any adapter actually having run.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
)
DIFF_SRC = SCRIPTS_SRC_DIR / "diff_rendered_output.py"

assert DIFF_SRC.is_file(), (
    f"diff_rendered_output.py not found at {DIFF_SRC} -- Phase 1 "
    "(contract §11-diff) has not landed yet"
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def make_root(tmp_path) -> Path:
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DIFF_SRC, scripts_dir / "diff_rendered_output.py")
    (root / "out").mkdir()
    return root


def reset_vault(root: Path, files: dict) -> None:
    """Replaces the CURRENT render (everything under out/ except the
    tool's own .baseline/ bookkeeping dir) with exactly `files`
    (relpath -> text content)."""
    out_dir = root / "out"
    for child in out_dir.iterdir():
        if child.name == ".baseline":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for relpath, content in files.items():
        path = out_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def run_diff(root: Path, *args, candidate_dir=None, timeout=30) -> subprocess.CompletedProcess:
    """--candidate-dir is passed EXPLICITLY on every invocation (defaulting
    to root/out) -- the script's own default candidate-dir resolution
    requires a real profile.yml + ownership marker (via
    cache_key.load_profile), which these bare-bones fixtures deliberately
    don't have; its own module docstring recommends exactly this override
    for standalone/test invocations. The baseline itself is NOT affected by
    --candidate-dir -- it always lives at the fixed
    {durable_root}/out/.baseline/, independent of whichever directory is
    being diffed as the candidate."""
    if candidate_dir is None:
        candidate_dir = root / "out"
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "diff_rendered_output.py"),
         "--candidate-dir", str(candidate_dir), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def parse_one_json_line(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


BASE_VAULT = {
    "people/ivan.md": "---\nsource_form: Ivan_src\n---\n\n# Ivan\n\nSome body text about Ivan.\n",
    "places/rome.md": "---\nsource_form: Rome_src\n---\n\n# Rome\n\nSome body text about Rome.\n",
}

CODE_BLOCK_VAULT = {
    "people/ivan.md": (
        "# Ivan\n\nA snippet:\n\n```\n    def foo():\n        return 1\n```\n"
    ),
    "places/rome.md": "# Rome\n\nOrdinary body text.\n",
}


# ===========================================================================
# 1. Bootstrap state: no baseline yet.
# ===========================================================================


def test_no_baseline_yet_reports_no_baseline_exit_2(tmp_path):
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)

    result = run_diff(root)
    assert result.returncode == 2, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload["reason"] == "no_baseline"


# ===========================================================================
# 2. accept-baseline freeze + deterministic re-diff (contract §13:
#    "deterministic rebuild").
# ===========================================================================


def test_accept_baseline_then_rediff_unchanged_matches_exit_0(tmp_path):
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)

    accept = run_diff(root, "--accept-baseline")
    assert accept.returncode == 0, f"stdout:\n{accept.stdout}\nstderr:\n{accept.stderr}"

    rediff = run_diff(root)
    assert rediff.returncode == 0, f"stdout:\n{rediff.stdout}\nstderr:\n{rediff.stderr}"
    payload = parse_one_json_line(rediff)
    assert payload["reason"] == "ok"


# ===========================================================================
# 3. Overwrite guard: refuse without --force-accept-baseline, succeed with it.
# ===========================================================================


def test_accept_baseline_refuses_overwrite_without_force(tmp_path):
    """The overwrite-refusal reason is its own distinct value,
    "baseline_exists" -- NOT "mismatch" (that reason is reserved for a
    genuine content mismatch on an ordinary diff run). The full closed
    reason set is {ok, mismatch, candidate_not_built, no_baseline,
    baseline_exists}."""
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)
    assert run_diff(root, "--accept-baseline").returncode == 0

    refused = run_diff(root, "--accept-baseline")
    assert refused.returncode == 1, f"stdout:\n{refused.stdout}\nstderr:\n{refused.stderr}"
    assert parse_one_json_line(refused)["reason"] == "baseline_exists"

    # The refusal must not have touched the existing baseline -- re-diffing
    # the SAME, still-original content must still cleanly match.
    still_matches = run_diff(root)
    assert still_matches.returncode == 0
    assert parse_one_json_line(still_matches)["reason"] == "ok"


def test_accept_baseline_force_overwrites_existing_baseline(tmp_path):
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)
    assert run_diff(root, "--accept-baseline").returncode == 0

    mutated = dict(BASE_VAULT)
    mutated["people/new_arrival.md"] = "# New Arrival\n\nBrand new content.\n"
    reset_vault(root, mutated)

    forced = run_diff(root, "--accept-baseline", "--force-accept-baseline")
    assert forced.returncode == 0, f"stdout:\n{forced.stdout}\nstderr:\n{forced.stderr}"

    # The baseline now reflects the MUTATED content -- re-diffing it
    # unchanged matches; diffing the ORIGINAL content against it mismatches.
    rediff_mutated = run_diff(root)
    assert rediff_mutated.returncode == 0
    assert parse_one_json_line(rediff_mutated)["reason"] == "ok"

    reset_vault(root, BASE_VAULT)
    rediff_original = run_diff(root)
    assert rediff_original.returncode == 1
    assert parse_one_json_line(rediff_original)["reason"] == "mismatch"


def test_accept_baseline_refuses_when_only_reduced_txt_exists_no_meta_json(tmp_path):
    """FIXSPEC_lt_review1.md C5: the overwrite guard must fire on ANY
    existing baseline content (reduced.txt OR meta.json OR a non-empty
    baseline dir), not just meta.json's presence -- the gap this locks:
    hand-construct a PARTIAL/legacy baseline with reduced.txt but
    deliberately NO meta.json (simulating an interrupted/older
    --accept-baseline write, or a hand-authored fixture), and confirm
    --accept-baseline still refuses (exit 1, reason baseline_exists)
    rather than silently overwriting it."""
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)

    baseline_dir = root / "out" / ".baseline"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "reduced.txt").write_text("--- people/ivan.md ---\n# Ivan\n", encoding="utf-8")
    assert not (baseline_dir / "meta.json").exists(), "sanity: no meta.json in this fixture"

    refused = run_diff(root, "--accept-baseline")
    assert refused.returncode == 1, f"stdout:\n{refused.stdout}\nstderr:\n{refused.stderr}"
    assert parse_one_json_line(refused)["reason"] == "baseline_exists"

    # The refusal must not have touched the hand-placed reduced.txt.
    assert (baseline_dir / "reduced.txt").read_text(encoding="utf-8") == (
        "--- people/ivan.md ---\n# Ivan\n"
    )


# ===========================================================================
# 4. Leading-indentation preserved (contract §11: markdown is
#    whitespace-significant -- must NOT be collapsed the way HTML norm_ws
#    would).
# ===========================================================================


def test_leading_indentation_only_change_is_a_mismatch(tmp_path):
    root = make_root(tmp_path)
    reset_vault(root, CODE_BLOCK_VAULT)
    assert run_diff(root, "--accept-baseline").returncode == 0

    reindented = dict(CODE_BLOCK_VAULT)
    reindented["people/ivan.md"] = (
        "# Ivan\n\nA snippet:\n\n```\n  def foo():\n    return 1\n```\n"
    )
    reset_vault(root, reindented)

    result = run_diff(root)
    assert result.returncode == 1, (
        f"a leading-indentation-only change (4-space -> 2-space) must be "
        f"caught as a mismatch, not silently normalized away:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert parse_one_json_line(result)["reason"] == "mismatch"

    # Isolate the assertion: restoring the EXACT original indentation must
    # make it match again, proving the mismatch really was that one edit.
    reset_vault(root, CODE_BLOCK_VAULT)
    restored = run_diff(root)
    assert restored.returncode == 0
    assert parse_one_json_line(restored)["reason"] == "ok"


def test_trailing_whitespace_only_change_still_matches(tmp_path):
    """Companion case: rstrip() of trailing whitespace per line IS part of
    the documented normalization (contract §11) -- unlike leading
    indentation, a pure trailing-whitespace difference must NOT cause a
    false mismatch."""
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)
    assert run_diff(root, "--accept-baseline").returncode == 0

    trailing_ws = {k: v.replace("Ivan.\n", "Ivan.   \n") for k, v in BASE_VAULT.items()}
    reset_vault(root, trailing_ws)

    result = run_diff(root)
    assert result.returncode == 0, (
        f"trailing whitespace must be normalized away, not treated as a "
        f"real change:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert parse_one_json_line(result)["reason"] == "ok"


# ===========================================================================
# 5. Structural changes fail loudly (contract §11: "opaque OTHER" catch-all
#    -- an unrecognized/unexpected structural change must never silently pass).
# ===========================================================================


def test_an_unexpected_extra_file_causes_a_mismatch(tmp_path):
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)
    assert run_diff(root, "--accept-baseline").returncode == 0

    with_extra = dict(BASE_VAULT)
    with_extra["places/unexpected.md"] = "# Unexpected\n\nThis file was never baselined.\n"
    reset_vault(root, with_extra)

    result = run_diff(root)
    assert result.returncode == 1
    assert parse_one_json_line(result)["reason"] == "mismatch"


def test_a_removed_file_causes_a_mismatch(tmp_path):
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)
    assert run_diff(root, "--accept-baseline").returncode == 0

    fewer_files = {"people/ivan.md": BASE_VAULT["people/ivan.md"]}  # rome.md dropped
    reset_vault(root, fewer_files)

    result = run_diff(root)
    assert result.returncode == 1
    assert parse_one_json_line(result)["reason"] == "mismatch"


# ===========================================================================
# 6. Multi-file vault reduction: every file genuinely participates.
# ===========================================================================


@pytest.mark.parametrize("which_file", ["people/ivan.md", "places/rome.md"])
def test_multi_file_vault_every_file_participates_in_the_comparison(tmp_path, which_file):
    root = make_root(tmp_path)
    three_files = dict(BASE_VAULT)
    three_files["places/extra.md"] = "# Extra\n\nA third file, so this is a genuine multi-file vault.\n"
    reset_vault(root, three_files)
    assert run_diff(root, "--accept-baseline").returncode == 0

    mutated = dict(three_files)
    mutated[which_file] = mutated[which_file] + "\nAn appended sentence that changes this ONE file only.\n"
    reset_vault(root, mutated)

    result = run_diff(root)
    assert result.returncode == 1, (
        f"a change isolated to {which_file} must be caught even with other "
        f"files present in the vault -- every file must genuinely "
        f"participate in the reduction, not just the first/last one found"
    )
    assert parse_one_json_line(result)["reason"] == "mismatch"


# ===========================================================================
# 7. candidate_not_built: a baseline exists, but the current render is gone.
# ===========================================================================


def test_candidate_not_built_when_the_current_render_is_missing(tmp_path):
    """The candidate dir must be genuinely ABSENT (not merely empty -- an
    empty-but-existing directory reduces to zero lines and is instead an
    ordinary "mismatch" against a non-empty baseline). The baseline itself
    is unaffected by --candidate-dir (it always lives at the fixed
    {durable_root}/out/.baseline/), so this simulates "assemble.py never
    (re-)ran" by pointing --candidate-dir at a path that was never created,
    while the previously-accepted baseline survives untouched."""
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)
    assert run_diff(root, "--accept-baseline").returncode == 0

    never_built = root / "out_never_rendered"
    assert not never_built.exists()

    result = run_diff(root, candidate_dir=never_built)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert parse_one_json_line(result)["reason"] == "candidate_not_built"


# ===========================================================================
# 8. Round-2 regression test (FIXSPEC_lt_review2.md C3): an unexpected
#    exception must still emit one JSON line, exit 1, never a bare traceback.
# ===========================================================================


def test_cli_unexpected_exception_emits_one_json_line_not_a_bare_traceback(tmp_path):
    """Hermetic repro: a POISONED baseline (reduced.txt containing invalid
    UTF-8 bytes) -- _read_baseline_lines's own .read_text(encoding="utf-8")
    raises an uncaught UnicodeDecodeError, a stand-in for "any unexpected
    exception" (a corrupt baseline, a permissions error, etc.) reaching the
    CLI's own catch-all, which must still surface as one JSON line."""
    root = make_root(tmp_path)
    candidate_dir = root / "out"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "people").mkdir()
    (candidate_dir / "people" / "ivan.md").write_text("# Ivan\n", encoding="utf-8")

    baseline_dir = candidate_dir / ".baseline"
    baseline_dir.mkdir(parents=True)
    baseline_dir.joinpath("reduced.txt").write_bytes(b"\xff\xfe\x00bad-utf8")

    result = run_diff(root, candidate_dir=candidate_dir)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line, no bare traceback, got:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(lines[0])
    assert payload.get("success") is False
    assert "Traceback" not in result.stdout


# ===========================================================================
# 9. Round-3 regression tests (FIXSPEC_lt_review3.md C2): a profile/
#    dependency SystemExit reached via resolve_default_candidate_dir()
#    (only exercised when --candidate-dir is OMITTED, unlike every test
#    above) must surface as one JSON line, exit 2 -- never stderr-only.
#    NOTE: unlike render_obsidian.py (which distinguishes dependency_
#    precondition at import-time from profile_precondition at
#    load_profile()-time), diff_rendered_output.py wraps the WHOLE
#    resolve_default_candidate_dir() call in one try/except SystemExit, so
#    BOTH an import-time failure and a load_profile()-time failure land
#    under the SAME "profile_precondition" reason here -- confirmed by the
#    two tests below, deliberately using two different trigger mechanisms.
# ===========================================================================


def _make_root_for_default_candidate_dir(tmp_path, *, poison_cache_key=False) -> Path:
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DIFF_SRC, scripts_dir / "diff_rendered_output.py")
    shutil.copy2(SCRIPTS_SRC_DIR / "output_resolve.py", scripts_dir / "output_resolve.py")
    if poison_cache_key:
        (scripts_dir / "cache_key.py").write_text(
            "import sys\n"
            "print('ERROR: poisoned cache_key.py dependency preflight', file=sys.stderr)\n"
            "sys.exit(2)\n",
            encoding="utf-8",
        )
    else:
        shutil.copy2(SCRIPTS_SRC_DIR / "cache_key.py", scripts_dir / "cache_key.py")
        # Deliberately NO profile.yml, NO .literary-translator-root.json
        # marker -- cache_key.load_profile() sys.exits on the missing marker.
    return root


def test_cli_profile_precondition_when_cache_key_exits_at_import(tmp_path):
    """Trigger #1: cache_key.py itself sys.exits DURING the `import
    cache_key` statement inside resolve_default_candidate_dir() -- an
    import-time (dependency-shaped) failure, still surfaced here as
    "profile_precondition" (see this section's own module-docstring note)."""
    root = _make_root_for_default_candidate_dir(tmp_path, poison_cache_key=True)

    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "diff_rendered_output.py")],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line, not stderr-only, got:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = json.loads(lines[0])
    assert payload.get("success") is False
    assert payload.get("reason") == "profile_precondition"


def test_cli_profile_precondition_when_load_profile_fails_on_missing_marker(tmp_path):
    """Trigger #2: a REAL cache_key.py imports fine, but
    cache_key.load_profile() itself sys.exits because no ownership marker
    exists -- the exact scenario FIXSPEC_lt_review3.md C2 names
    ("cache_key.load_profile() at :254")."""
    root = _make_root_for_default_candidate_dir(tmp_path, poison_cache_key=False)

    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "diff_rendered_output.py")],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line, not stderr-only, got:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = json.loads(lines[0])
    assert payload.get("success") is False
    assert payload.get("reason") == "profile_precondition"


# ===========================================================================
# 10. Round-5 regression tests (FIXSPEC_lt_review5.md finding 1): `.baseline/`
#     is a PRESERVED dotfile (render_obsidian.py's clean-render never
#     touches it), so a planted symlink there could survive indefinitely
#     and either satisfy write_baseline()'s mkdir(exist_ok=True) or let a
#     plain write follow it out of the vault. write_baseline() now guards
#     the dir itself (symlink -> refuse) and writes reduced.txt/meta.json
#     via mkstemp+os.replace (no-follow). Every fixture here invokes the
#     real script via subprocess (never in-process import) -- this file's
#     own established convention -- which is also what keeps
#     BASELINE_DIR/BASELINE_LINES_PATH/BASELINE_META_PATH (module-level
#     constants self-anchored from the COPIED script's own location)
#     correctly scoped to each isolated tmp fixture, never the real
#     assets/scripts/ tree.
# ===========================================================================


def test_baseline_dir_symlink_is_refused_and_external_dir_untouched(tmp_path):
    """--force-accept-baseline is used here to bypass baseline_exists()'s
    OWN, earlier, broader check -- which itself FOLLOWS the .baseline
    symlink (Path.is_dir()/.iterdir() both follow a symlink) and would
    otherwise report "baseline_exists" before write_baseline() ever gets a
    chance to run its own, more specific symlink guard. --force sidesteps
    that unrelated interaction so this test actually reaches (and locks)
    the write_baseline()-level "baseline_dir_is_symlink" refusal."""
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)

    external_dir = tmp_path / "external_baseline_dir"
    external_dir.mkdir()
    external_file = external_dir / "precious.txt"
    external_file.write_text("precious external content", encoding="utf-8")

    baseline_symlink = root / "out" / ".baseline"
    baseline_symlink.symlink_to(external_dir, target_is_directory=True)

    result = run_diff(root, "--accept-baseline", "--force-accept-baseline")
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "baseline_dir_is_symlink"

    assert external_file.is_file() and external_file.read_text(encoding="utf-8") == "precious external content", (
        "a refused baseline write must leave the symlink's external target completely untouched"
    )
    assert list(external_dir.iterdir()) == [external_file], (
        "nothing must be written into the external dir the symlink points at"
    )


def test_baseline_reduced_txt_symlink_is_replaced_not_followed(tmp_path):
    """A REAL (non-symlink) .baseline/ dir, but with reduced.txt itself
    planted as a symlink to an external file -- the atomic mkstemp+
    os.replace write must replace the symlink entry, never follow it
    through to the external target. --force-accept-baseline bypasses
    baseline_exists()'s own broader "is there ANY content already" check
    (which would otherwise see the planted reduced.txt entry and refuse
    with "baseline_exists" before ever reaching the write path this test
    targets) -- unrelated to the no-follow behavior being locked here."""
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)

    baseline_dir = root / "out" / ".baseline"
    baseline_dir.mkdir(parents=True)
    external_target = tmp_path / "external_reduced.txt"
    external_target.write_text("precious external reduced content", encoding="utf-8")
    reduced_symlink = baseline_dir / "reduced.txt"
    reduced_symlink.symlink_to(external_target)
    assert reduced_symlink.is_symlink()

    result = run_diff(root, "--accept-baseline", "--force-accept-baseline")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert external_target.is_file() and external_target.read_text(encoding="utf-8") == "precious external reduced content", (
        "the atomic write must never follow the symlink through to the external target"
    )
    reduced_path = baseline_dir / "reduced.txt"
    assert not reduced_path.is_symlink(), "reduced.txt must now be a REAL regular file, not a symlink"
    assert reduced_path.is_file()

    leftover_tmp = list(baseline_dir.glob("lt-baseline-tmp-*"))
    assert leftover_tmp == [], f"expected zero stray tmp entries, found: {leftover_tmp}"


def test_normal_accept_baseline_leaves_no_stray_tmp_file(tmp_path):
    root = make_root(tmp_path)
    reset_vault(root, BASE_VAULT)

    result = run_diff(root, "--accept-baseline")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    baseline_dir = root / "out" / ".baseline"
    leftover_tmp = list(baseline_dir.glob("lt-baseline-tmp-*"))
    assert leftover_tmp == [], f"expected zero stray tmp entries, found: {leftover_tmp}"
    assert (baseline_dir / "reduced.txt").is_file() and not (baseline_dir / "reduced.txt").is_symlink()
    assert (baseline_dir / "meta.json").is_file() and not (baseline_dir / "meta.json").is_symlink()


# ===========================================================================
# 11. Round-6 regression test: the PARENT of `.baseline/` -- `out/` itself,
#     a direct child of the trusted DURABLE_ROOT -- being a symlink must be
#     refused BEFORE the `.baseline`-itself check even runs (the round-5
#     fix only guarded `.baseline/` itself, one level too low). This test
#     builds its own bare durable_root directly (not via make_root(), whose
#     out/ is always a plain mkdir'd directory) so `out/` itself can be
#     planted as the symlink under test.
# ===========================================================================


def test_parent_out_dir_symlink_is_refused_before_baseline_dir_is_created(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DIFF_SRC, scripts_dir / "diff_rendered_output.py")

    external_dir = tmp_path / "external_out_dir"
    external_dir.mkdir()
    external_file = external_dir / "precious.txt"
    external_file.write_text("precious external content", encoding="utf-8")

    out_symlink = root / "out"
    out_symlink.symlink_to(external_dir, target_is_directory=True)

    result = run_diff(root, "--accept-baseline", candidate_dir=out_symlink)
    assert result.returncode == 1, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    payload = parse_one_json_line(result)
    assert payload.get("reason") == "out_dir_is_symlink"

    assert external_file.is_file() and external_file.read_text(encoding="utf-8") == "precious external content", (
        "a refused baseline write must leave the symlink's external target completely untouched"
    )
    assert not (external_dir / ".baseline").exists(), (
        "no .baseline/ may ever be created inside the external target the "
        "planted out/ symlink points at"
    )
    assert sorted(p.name for p in external_dir.iterdir()) == ["precious.txt"], (
        "nothing new may be written into the external dir"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
