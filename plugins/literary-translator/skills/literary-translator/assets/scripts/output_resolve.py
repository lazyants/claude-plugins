#!/usr/bin/env python3
"""output_resolve.py -- Step 0d: resolves profile.yml's `output.target` to a
concrete adapter dispatch target. Generalizes `cache_key.py`'s own
`resolve_extractor_path` (the source-side format -> extractor resolver,
see that function) to the OUTPUT side -- same shape of problem (a closed,
profile-declared enum picking between built-in implementations and a
path-safety-checked escape hatch), mirrored deliberately.

`resolve_output_adapter(profile, durable_root)` returns one of:

  - `"render_obsidian"` / `"render_epub"` -- a flat sibling module NAME
    (never a Path) for the two built-in adapters. `assemble.py` imports
    whichever one by name and calls its `render(...)` entry point (see
    references/output-target-adapters/README.md's shared adapter contract).
  - a `Path` -- for `output.target == "custom"`, the resolved,
    path-safety-checked custom renderer module under
    `{durable_root}/scripts/custom_renderers/`.

`obsidian` and `epub` are two DISTINCT built-in renderers (unlike the
source side, where `gutenberg_epub` and `plain_text` would share one
`extract.py` once `plain_text` is implemented (#62)) -- there is no generic
renderer-plugin framework above these three fixed presets, by design (see
the plan's proportionality guardrails).

## The custom escape hatch -- null-path HALT + path-safety trio

A null/absent `output.adapter_config.custom.renderer_path` is a co-design
SENTINEL, not a swallowed error -- exactly like the source side's
`source.adapter_config.custom.extractor_path` (see `custom.md` and
`cache_key.py::resolve_extractor_path`): it means "the custom output
renderer hasn't been written yet," and this function HALTS (raises
`OutputResolveError`, which the caller/CLI turns into a fatal, non-zero
exit) naming the exact profile field to set.

A non-null `renderer_path` must clear a POSITIVE-allow-list path-safety
trio (never a denylist -- an untrusted string reaching a filesystem path
must always be allow-listed; a denylist that merely rejects `..`/absolute
prefixes still lets shell metacharacters or other surprises through):

  1. must be a non-empty string, not starting with `/` (a leading slash
     would let an otherwise in-character-class value escape the joined
     subtree entirely);
  2. must fully match `^[A-Za-z0-9._/-]+$` AND must not contain a literal
     `..` path segment;
  3. once joined under the FIXED subtree
     `{durable_root}/scripts/custom_renderers/`, the RESOLVED path must
     still be contained within that subtree (`Path.relative_to`, which
     raises if it is not) -- this is the actual containment PROOF; checks
     1-2 narrow the character set and reject the obvious `..` case, but 3
     is what a symlink or an equivalent edge case would still have to
     clear.

This module is a pure dispatch resolver: it never reads/writes
`canon.json`, `manifest.json`, or any draft -- `assemble.py` owns all of
that. It is also self-contained (no cross-script imports) -- the tiny
`fail`/`profile_get`-style helpers below are deliberately duplicated
rather than imported from a sibling script, matching this plugin's own
"flat scripts only" convention (see e.g. `final_audit.py` duplicating its
own `draft_path`/`segpack_path` rather than importing `validate_draft.py`'s
identical ones).

Usage (optional standalone CLI, mirrors the "one JSON line on stdout"
house convention -- `assemble.py` itself calls `resolve_output_adapter`
directly as a library function; this CLI exists for a standalone Step 0d
preflight check):

    python3 output_resolve.py

Exit 0 on a successful resolution (one JSON line describing it), 1 on any
resolution failure (unknown target, null/unsafe custom renderer_path),
2 if a required dependency (PyYAML, via validate_draft.py's profile
loader) is missing.
"""
import json
import os
import re
import sys
from pathlib import Path
from typing import NoReturn, Union

# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent

CUSTOM_RENDERERS_SUBDIR = "custom_renderers"
RENDERER_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

BUILTIN_ADAPTER_MODULES = {
    "obsidian": "render_obsidian",
    "epub": "render_epub",
}


class OutputResolveError(Exception):
    """Raised for any failure resolving output.target to a dispatch
    target. Caught centrally by main() and reported as a fatal, non-zero
    exit -- never a bare traceback for an expected/actionable condition."""


def _check_renderer_path_shape(renderer_path) -> None:
    if not isinstance(renderer_path, str) or not renderer_path:
        raise OutputResolveError(
            "output.adapter_config.custom.renderer_path must be a non-empty "
            f"string; got {renderer_path!r}"
        )
    if renderer_path.startswith("/"):
        raise OutputResolveError(
            "output.adapter_config.custom.renderer_path must be a relative "
            f"path (no leading '/'); got {renderer_path!r}"
        )
    if not RENDERER_PATH_RE.fullmatch(renderer_path):
        raise OutputResolveError(
            "output.adapter_config.custom.renderer_path contains characters "
            "outside the allowed set [A-Za-z0-9._/-]; got "
            f"{renderer_path!r}"
        )
    if ".." in renderer_path.split("/"):
        raise OutputResolveError(
            "output.adapter_config.custom.renderer_path must not contain a "
            f"'..' path segment; got {renderer_path!r}"
        )


def resolve_output_adapter(profile: dict, durable_root: Path) -> Union[str, Path]:
    """Returns `"render_obsidian"` | `"render_epub"` (built-in adapter
    module names) or a `Path` (the resolved, path-safety-checked custom
    renderer module) -- exhaustive over `output.target`'s closed enum, no
    default fallthrough. Raises `OutputResolveError` naming the exact
    problem on any failure."""
    output = profile.get("output") if isinstance(profile, dict) else None
    if not isinstance(output, dict):
        raise OutputResolveError("profile.yml is missing required field 'output'")
    target = output.get("target")

    if target is None:
        raise OutputResolveError(
            "profile.yml has no output.target set -- add one of "
            "obsidian|epub|custom under output.target (see "
            "references/output-target-adapters/README.md)"
        )
    if target in BUILTIN_ADAPTER_MODULES:
        return BUILTIN_ADAPTER_MODULES[target]
    if target == "custom":
        adapter_config = output.get("adapter_config") or {}
        custom_config = adapter_config.get("custom") or {}
        renderer_path = custom_config.get("renderer_path")
        if not renderer_path:
            raise OutputResolveError(
                "output.adapter_config.custom.renderer_path is not set yet "
                "-- cannot resolve the custom output adapter until it has "
                "been co-designed and pointed at (see "
                "references/output-target-adapters/README.md)"
            )
        _check_renderer_path_shape(renderer_path)
        base = (durable_root / "scripts" / CUSTOM_RENDERERS_SUBDIR).resolve()
        candidate = (base / renderer_path).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            raise OutputResolveError(
                "output.adapter_config.custom.renderer_path resolves "
                f"outside the fixed {CUSTOM_RENDERERS_SUBDIR}/ subtree; got "
                f"{renderer_path!r}"
            )
        return candidate
    raise OutputResolveError(
        f"unknown output.target {target!r} -- must be one of obsidian|epub|custom"
    )


def resolve_out_dir(profile: dict, durable_root: Path) -> Path:
    """Resolves the ONE shared output directory every adapter and the
    render+diff tool write into/read from -- centralizing the rule here
    so `assemble.py`, each adapter, and `diff_rendered_output.py` can
    never each derive a slightly different path. Never creates the
    directory -- callers `mkdir` it themselves.

    - `output.destination` set to a non-empty string -> that path (`~`
      expanded; if relative, resolved against `durable_root`).
    - `output.destination` null/absent -> `{durable_root}/out`.

    `output.destination` must not contain a `..` segment. The resulting path
    is normalized WITHOUT following symlinks (never `.resolve()`) and REFUSED
    if it is reached through a symlinked path COMPONENT -- a symlink there
    would redirect the rendered book outside its declared destination and
    silently defeat every downstream out_dir symlink guard (which only ever
    see the already-collapsed final path a `.resolve()` would hand them). A
    legitimately symlinked INSTALL (`durable_root` itself, or an ancestor, a
    symlink with a REAL output subdir underneath) is NOT rejected: only the
    components the destination contributes below `durable_root` are checked
    (leaf + immediate parent for an explicit out-of-root destination).
    """
    output = profile.get("output") if isinstance(profile, dict) else None
    destination = output.get("destination") if isinstance(output, dict) else None
    if isinstance(destination, str) and destination:
        if ".." in Path(destination).parts:
            raise OutputResolveError(
                "output.destination must not contain a '..' path segment; "
                f"got {destination!r}"
            )
        dest_path = Path(destination).expanduser()
        if not dest_path.is_absolute():
            dest_path = durable_root / dest_path
    else:
        dest_path = durable_root / "out"
    # Normalize WITHOUT following symlinks. NEVER .resolve() here: it would
    # collapse a symlinked component and defeat both the check below and every
    # downstream out_dir symlink guard (which trust this returned path).
    out_dir = Path(os.path.abspath(dest_path))
    _assert_no_symlink_out_dir_components(out_dir, Path(os.path.abspath(durable_root)))
    return out_dir


def _assert_no_symlink_out_dir_components(out_dir: Path, root: Path) -> None:
    """Refuse if `out_dir` is reached THROUGH a symlink. Component-wise,
    no-follow `is_symlink()` (never realpath-containment) so a legitimately
    symlinked install (`durable_root`/an ancestor a symlink, real output subdir)
    is NOT rejected -- only components strictly BELOW `root` are checked for an
    in-root destination; only the leaf and its immediate parent for an explicit
    out-of-root (absolute external) destination (its higher ancestors are the
    user's own filesystem, not something this profile introduced). A
    non-existent component is not a symlink, so a fresh (not-yet-created)
    destination passes cleanly."""
    try:
        rel_parts = out_dir.relative_to(root).parts
    except ValueError:
        # Out-of-root (explicit external) destination: only the leaf and its
        # immediate parent are ours to vet -- higher ancestors are the user's
        # own filesystem, not something this profile introduced.
        checkpoints = [out_dir.parent, out_dir]
    else:
        # In-root destination: vet every component the destination contributes
        # strictly BELOW root (root itself is never checked -> an empty
        # rel_parts, i.e. out_dir == root, checks nothing).
        checkpoints = []
        cur = root
        for part in rel_parts:
            cur = cur / part
            checkpoints.append(cur)
    for cp in checkpoints:
        if cp.is_symlink():
            raise OutputResolveError(
                f"output destination is reached through a symlinked path "
                f"component ({cp}) -- refusing to assemble into it: a symlink "
                f"here would redirect the rendered book outside its declared "
                f"destination. Point output.destination at a real directory."
            )


# ---------------------------------------------------------------------------
# Optional standalone CLI.
# ---------------------------------------------------------------------------


def _environment_fatal(msg: str) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def main(argv=None) -> int:
    del argv  # no CLI arguments -- self-anchored, matches every sibling script

    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import validate_draft as vd
    except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
        _environment_fatal(
            f"could not import validate_draft.py from {SCRIPTS_DIR}: {exc}"
        )
    except SystemExit:
        # validate_draft.py's own module-level dependency preflight (its
        # PyYAML import guard) can sys.exit(2) DURING this very import
        # statement -- before this CLI's own JSON-envelope machinery below
        # ever gets a chance to run. Scoped to just this import (never a
        # broader try block), so this can't swallow an unrelated SystemExit
        # from elsewhere.
        print(
            json.dumps(
                {
                    "success": False,
                    "reason": "dependency_precondition",
                    "error": (
                        f"could not import validate_draft.py from "
                        f"{SCRIPTS_DIR} -- it halted during its own "
                        "module-level dependency preflight (see stderr for "
                        "the specific reason)"
                    ),
                }
            )
        )
        return 2

    try:
        profile = vd.load_profile()
    except SystemExit:
        # validate_draft.py's own load_profile() halts via sys.exit(2) on a
        # profile/environment precondition, printing only to stderr -- never
        # leave this CLI without its own one-JSON-line, reason-carrying
        # contract just because the failure happened one function down.
        print(
            json.dumps(
                {
                    "success": False,
                    "reason": "profile_precondition",
                    "error": (
                        "profile.yml failed to load/validate via "
                        "validate_draft.py's own profile loader (see this "
                        "run's stderr for the specific reason it halted)"
                    ),
                }
            )
        )
        return 2
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(
            json.dumps({"success": False, "error": f"unexpected error: {exc}"}),
        )
        return 1

    try:
        resolved = resolve_output_adapter(profile, DURABLE_ROOT)
    except OutputResolveError as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        return 1

    target = profile.get("output", {}).get("target")
    result = {
        "success": True,
        "target": target,
        "resolved": resolved if isinstance(resolved, str) else str(resolved),
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
