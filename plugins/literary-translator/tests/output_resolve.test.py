"""tests/output_resolve.test.py -- scripts/output_resolve.py's enum
dispatch + custom-renderer path-safety trio (see
references/output-target-adapters/README.md, mirroring cache_key.py's own
``resolve_extractor_path`` on the source side).

## Invocation style

``output_resolve.py`` is consumed IN-PROCESS by assemble.py
(``adapter = resolve_output_adapter(profile, durable_root)``, contract
§10). Unlike ``cache_key.py``'s own ``fail()`` helper (print to stderr,
``sys.exit(1)``), the real, shipped ``resolve_output_adapter`` raises its
own ``OutputResolveError`` exception on every failure path instead of
calling ``sys.exit`` directly -- a deliberate, better-than-my-original-
assumption design for a function meant to be called as a library from
inside another script's own process (its optional standalone CLI's
``main()`` is what turns ``OutputResolveError`` into a fatal, non-zero
exit + one JSON stdout line). This file loads the real module via
``importlib.util.spec_from_file_location`` (the same idiom
``durable_root_reachability.test.py`` uses for ``cache_key.py``'s
``load_owner_marker``) and calls ``resolve_output_adapter`` directly,
catching ``output_resolve.OutputResolveError``.

## A documented interpretation call

The custom path-safety trio's step 3 ("join under the fixed subtree ...,
then let read/existence fail() if absent") is read here as: existence is
NOT checked by ``resolve_output_adapter`` itself (mirroring
``cache_key.py``'s own ``resolve_extractor_path``, which likewise returns
a Path without probing the filesystem -- the caller's own later read is
what actually fails on a missing file). This file therefore only asserts
that a SYNTACTICALLY safe custom renderer_path resolves to the correct
Path under the fixed subtree, for a path that DOES exist on disk --
whether resolve_output_adapter also independently fails fast on a missing
file is left untested here as a flagged ambiguity (see the accompanying
report to the lead).
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
)
OUTPUT_RESOLVE_SRC = SCRIPTS_SRC_DIR / "output_resolve.py"
# output_resolve.py's own standalone CLI imports validate_draft.py as a
# sibling (profile loading) -- needed for the profile_precondition CLI test.
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"

assert OUTPUT_RESOLVE_SRC.is_file(), (
    f"output_resolve.py not found at {OUTPUT_RESOLVE_SRC} -- Phase 0 "
    "(contract §9) has not landed yet"
)
assert VALIDATE_DRAFT_SRC.is_file(), f"validate_draft.py not found at {VALIDATE_DRAFT_SRC}"


def _load_output_resolve_module():
    spec = importlib.util.spec_from_file_location(
        "output_resolve_under_test", OUTPUT_RESOLVE_SRC
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


output_resolve = _load_output_resolve_module()

assert hasattr(output_resolve, "resolve_output_adapter"), (
    "output_resolve.py must expose resolve_output_adapter(profile, durable_root) "
    "per contract §9"
)
assert hasattr(output_resolve, "resolve_out_dir"), (
    "output_resolve.py must expose resolve_out_dir(profile, durable_root) -- the "
    "one shared output-directory rule assemble.py, each adapter, and "
    "diff_rendered_output.py all consult (reconciliation item #1: this lives on "
    "the resolver side, not render_obsidian.py, so the diff tool -- which must "
    "stay target-agnostic -- never has to import an adapter for a path helper)"
)


def base_profile(target, adapter_config=None):
    return {
        "output": {
            "target": target,
            "adapter_config": adapter_config or {"obsidian": None, "epub": None, "custom": None},
        }
    }


# ===========================================================================
# Enum dispatch (contract §9's literal pseudocode).
# ===========================================================================


def test_obsidian_target_resolves_to_the_documented_module_name(tmp_path):
    result = output_resolve.resolve_output_adapter(base_profile("obsidian"), tmp_path)
    assert result == "render_obsidian"


def test_epub_target_resolves_to_the_documented_module_name(tmp_path):
    result = output_resolve.resolve_output_adapter(base_profile("epub"), tmp_path)
    assert result == "render_epub"


def test_custom_target_resolves_to_a_path_safe_path(tmp_path):
    custom_dir = tmp_path / "scripts" / "custom_renderers"
    custom_dir.mkdir(parents=True)
    renderer = custom_dir / "my_renderer.py"
    renderer.write_text("# fixture custom renderer\n", encoding="utf-8")

    profile = base_profile(
        "custom",
        {"obsidian": None, "epub": None, "custom": {"renderer_path": "my_renderer.py"}},
    )
    result = output_resolve.resolve_output_adapter(profile, tmp_path)

    assert isinstance(result, Path)
    assert result.resolve() == renderer.resolve()


def test_unknown_target_is_fatal_with_no_default_fallthrough(tmp_path):
    profile = base_profile("pdf")
    with pytest.raises(output_resolve.OutputResolveError, match="pdf"):
        output_resolve.resolve_output_adapter(profile, tmp_path)


# ===========================================================================
# Custom path-safety trio (contract §9, points 1-3).
# ===========================================================================


def _custom_profile(renderer_path):
    return base_profile(
        "custom", {"obsidian": None, "epub": None, "custom": {"renderer_path": renderer_path}}
    )


def test_null_renderer_path_halts_rather_than_silently_defaulting(tmp_path):
    with pytest.raises(output_resolve.OutputResolveError, match="renderer_path"):
        output_resolve.resolve_output_adapter(_custom_profile(None), tmp_path)


def test_empty_string_renderer_path_halts_same_as_null(tmp_path):
    with pytest.raises(output_resolve.OutputResolveError, match="renderer_path"):
        output_resolve.resolve_output_adapter(_custom_profile(""), tmp_path)


def test_non_string_renderer_path_is_rejected(tmp_path):
    with pytest.raises(output_resolve.OutputResolveError):
        output_resolve.resolve_output_adapter(_custom_profile(123), tmp_path)


@pytest.mark.parametrize(
    "hostile_value",
    [
        "../escape.py",
        "sub/../../escape.py",
        "/absolute/escape.py",
        "evil; rm -rf /.py",
        "evil`rm -rf /`.py",
        "evil$(rm -rf /).py",
        "evil|rm.py",
        "evil\nrm.py",
    ],
)
def test_hostile_renderer_path_values_are_rejected_before_any_join(tmp_path, hostile_value):
    with pytest.raises(output_resolve.OutputResolveError):
        output_resolve.resolve_output_adapter(_custom_profile(hostile_value), tmp_path)
    # A refused path must never even be joined onto disk -- nothing should
    # exist outside the durable_root's own custom_renderers subtree.
    escaped_marker = tmp_path.parent / "escape.py"
    assert not escaped_marker.exists()


def test_nested_but_safe_renderer_path_stays_contained_under_the_fixed_subtree(tmp_path):
    custom_dir = tmp_path / "scripts" / "custom_renderers"
    nested_dir = custom_dir / "sub" / "dir"
    nested_dir.mkdir(parents=True)
    renderer = nested_dir / "good.py"
    renderer.write_text("# nested fixture renderer\n", encoding="utf-8")

    result = output_resolve.resolve_output_adapter(_custom_profile("sub/dir/good.py"), tmp_path)

    assert isinstance(result, Path)
    resolved = result.resolve()
    assert str(resolved).startswith(str(custom_dir.resolve())), (
        f"resolved custom renderer path {resolved} escaped the fixed "
        f"subtree {custom_dir.resolve()}"
    )
    assert resolved == renderer.resolve()



# ===========================================================================
# resolve_out_dir(profile, durable_root) -- the one shared output-directory
# rule (reconciliation item #1/#2 from RECONCILE_lt_seams.md): null/absent
# output.destination -> {durable_root}/out; an explicit string ->
# that path, resolved against durable_root when relative.
# ===========================================================================


def test_resolve_out_dir_defaults_to_durable_root_out_when_destination_absent(tmp_path):
    profile = {"output": {}}
    result = output_resolve.resolve_out_dir(profile, tmp_path)
    # No-follow abspath (never .resolve()) so a symlinked component can't be
    # collapsed away; on macOS .resolve() would also follow /var -> /private/var.
    assert result == Path(os.path.abspath(tmp_path / "out"))


def test_resolve_out_dir_defaults_to_durable_root_out_when_destination_null(tmp_path):
    profile = {"output": {"destination": None}}
    result = output_resolve.resolve_out_dir(profile, tmp_path)
    assert result == Path(os.path.abspath(tmp_path / "out"))


def test_resolve_out_dir_defaults_to_durable_root_out_when_destination_empty_string(tmp_path):
    profile = {"output": {"destination": ""}}
    result = output_resolve.resolve_out_dir(profile, tmp_path)
    assert result == Path(os.path.abspath(tmp_path / "out"))


def test_resolve_out_dir_uses_an_explicit_absolute_destination_as_is(tmp_path):
    explicit = tmp_path / "elsewhere" / "book_output"
    profile = {"output": {"destination": str(explicit)}}
    result = output_resolve.resolve_out_dir(profile, tmp_path)
    assert result == Path(os.path.abspath(explicit))


def test_resolve_out_dir_resolves_a_relative_destination_against_durable_root(tmp_path):
    profile = {"output": {"destination": "custom_out/subdir"}}
    result = output_resolve.resolve_out_dir(profile, tmp_path)
    assert result == Path(os.path.abspath(tmp_path / "custom_out" / "subdir"))


def test_resolve_out_dir_never_creates_the_directory(tmp_path):
    profile = {"output": {"destination": "not_created_yet"}}
    result = output_resolve.resolve_out_dir(profile, tmp_path)
    assert not result.exists(), "resolve_out_dir must only compute the path, never mkdir it"


# ===========================================================================
# resolve_out_dir symlink-escape guard (PR #76 bot blocker #4): .resolve()
# followed ALL symlink components, so output.destination via a symlinked
# parent collapsed to the real external target and slipped past every
# downstream out_dir symlink guard. resolve_out_dir now normalizes no-follow
# (os.path.abspath) and REFUSES a destination reached through a symlinked
# below-root component -- while NOT rejecting a legitimately symlinked install
# (durable_root/an ancestor a symlink, real output subdir underneath).
# ===========================================================================


def test_resolve_out_dir_rejects_a_symlinked_below_root_component(tmp_path):
    """(a) A destination reached through an in-root symlinked parent must be
    REFUSED, not silently followed to the external target the symlink points
    at -- the exact escape the bot reproduced."""
    external = tmp_path / "external_real"
    external.mkdir()
    linkdir = tmp_path / "linkdir"
    linkdir.symlink_to(external, target_is_directory=True)
    profile = {"output": {"destination": "linkdir/vault"}}
    with pytest.raises(output_resolve.OutputResolveError, match="symlink"):
        output_resolve.resolve_out_dir(profile, tmp_path)


def test_resolve_out_dir_rejects_a_dotdot_destination_segment(tmp_path):
    """(b) A `..` segment in output.destination is refused outright (before
    any normalization could quietly collapse it back inside the root)."""
    profile = {"output": {"destination": "sub/../escape"}}
    with pytest.raises(output_resolve.OutputResolveError, match=r"\.\."):
        output_resolve.resolve_out_dir(profile, tmp_path)


def test_resolve_out_dir_allows_a_symlinked_install_with_real_output_subdir(tmp_path):
    """(c) BOUNDARY CONSTRAINT: durable_root itself (or an ancestor) may be a
    symlink while the output subdir under it is a REAL directory (a
    legitimately symlinked skill install). resolve_out_dir must NOT reject
    that -- only components the destination contributes BELOW the root are
    checked, never the root/ancestors themselves."""
    real_root = tmp_path / "real_root"
    real_root.mkdir()
    (real_root / "out").mkdir()  # REAL output subdir under the (soon) symlinked root
    linked_root = tmp_path / "linked_root"
    linked_root.symlink_to(real_root, target_is_directory=True)

    profile = {"output": {"destination": None}}  # default -> {durable_root}/out
    result = output_resolve.resolve_out_dir(profile, linked_root)
    assert result == Path(os.path.abspath(linked_root / "out"))


def test_resolve_out_dir_happy_path_real_below_root_dirs_resolve_to_abspath(tmp_path):
    """(d) Happy path: a relative destination pointing at REAL (non-symlink)
    below-root directories resolves cleanly to the no-follow abspath."""
    (tmp_path / "book_out" / "vault").mkdir(parents=True)
    profile = {"output": {"destination": "book_out/vault"}}
    result = output_resolve.resolve_out_dir(profile, tmp_path)
    assert result == Path(os.path.abspath(tmp_path / "book_out" / "vault"))


# ===========================================================================
# profile_precondition -- FIXSPEC_lt_review1.md B5: output_resolve.py's own
# standalone CLI currently does `except SystemExit: raise` around
# vd.load_profile(), re-raising validate_draft.py's stderr-only sys.exit(2)
# with NO JSON line at all. The fix wraps this into ONE JSON line,
# {"success": false, "reason": "profile_precondition", ...}, still at exit
# code 2 (a defined precondition, not a fatal defect).
# ===========================================================================


def test_cli_profile_precondition_emits_one_json_line_on_missing_ownership_marker(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(OUTPUT_RESOLVE_SRC, scripts_dir / "output_resolve.py")
    shutil.copy2(VALIDATE_DRAFT_SRC, scripts_dir / "validate_draft.py")
    # Deliberately NO profile.yml, NO .literary-translator-root.json marker.

    proc = subprocess.run(
        [sys.executable, str(scripts_dir / "output_resolve.py")],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line on stdout, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = json.loads(lines[0])
    assert payload.get("success") is False
    assert payload.get("reason") == "profile_precondition"


# ===========================================================================
# dependency_precondition -- FIXSPEC_lt_review2.md B2: DISTINCT from
# profile_precondition above -- validate_draft.py's own module-level
# dependency preflight (its PyYAML import guard) can sys.exit(2) DURING the
# `import validate_draft` statement itself, before vd.load_profile() is
# ever called. Hermetic repro: a POISONED validate_draft.py stub that
# sys.exits(2) at import time -- never touches the real environment's
# PyYAML install.
# ===========================================================================


def test_cli_dependency_precondition_emits_one_json_line_when_validate_draft_exits_at_import(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(OUTPUT_RESOLVE_SRC, scripts_dir / "output_resolve.py")
    (scripts_dir / "validate_draft.py").write_text(
        "import sys\n"
        "print('ERROR: poisoned validate_draft.py dependency preflight', file=sys.stderr)\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )
    # Deliberately no profile.yml/marker -- the import fails before either
    # would ever be consulted.

    proc = subprocess.run(
        [sys.executable, str(scripts_dir / "output_resolve.py")],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line on stdout, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = json.loads(lines[0])
    assert payload.get("success") is False
    assert payload.get("reason") == "dependency_precondition"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
