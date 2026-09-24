"""Shared library for codebase-migrator scripts.

Every other script in this plugin imports exactly the names defined here.
This module must stay importable with no side effects beyond reading the
process environment (``tempfile.gettempdir()``): no file writes, no network,
no work at import time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable, NoReturn

EXIT_OK, EXIT_FAIL, EXIT_CANNOT = 0, 1, 2

PLUGIN_VERSION = "0.1.0"

# A durable root must not sit under any of these: `codex exec -s workspace-write`
# may write inside a temp root, so a root there is not protected by the sandbox
# probe (see sandbox.py). Compared by realpath, so a symlinked temp root (macOS
# /var -> /private/var) cannot hide it.
TEMP_ROOTS: tuple[str, ...] = (
    "/tmp",
    "/private/tmp",
    "/var/folders",
    "/private/var/folders",
    tempfile.gettempdir(),
)

_UNIT_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Interpreter-managed module attributes excluded from the state snapshot
# (owner C, observe.py); kept here because it is a small fixed fact, not
# behaviour, and both C's harness and A's cm_common may need it later.
INTERPRETER_MODULE_ATTRS = frozenset(
    {
        "__builtins__",
        "__loader__",
        "__spec__",
        "__file__",
        "__cached__",
        "__name__",
        "__package__",
        "__path__",
        "__doc__",
        "__warningregistry__",
    }
)


def emit(obj: dict) -> None:
    """Write exactly one JSON line to stdout and flush it."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, sort_keys=True))
    sys.stdout.write("\n")
    sys.stdout.flush()


def fail(msg: str, code: int, **fields) -> NoReturn:
    """Report a failure on both channels and exit.

    stderr gets the human message; stdout gets exactly one JSON line
    (``{"ok": false, "error": msg, **fields}``); the process exits with
    `code`. Never raises past this point.
    """
    print(msg, file=sys.stderr)
    payload = {"ok": False, "error": msg}
    payload.update(fields)
    emit(payload)
    sys.exit(code)


def run_main(main: Callable[[], int]) -> NoReturn:
    """Top-level wrapper every script's ``if __name__ == "__main__"`` calls.

    Runs `main`, which must return an exit code. Any exception other than
    `SystemExit`/`KeyboardInterrupt` is caught here and converted to
    ``fail(str(exc), EXIT_CANNOT)`` so a script never lets a raw traceback
    reach the user.
    """
    try:
        code = main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:  # top-level safety net, by design
        fail(str(exc), EXIT_CANNOT)
    else:
        sys.exit(code)


class _FailingArgumentParser(argparse.ArgumentParser):
    """An `ArgumentParser` whose `error()` follows the plugin's failure
    contract (one stdout JSON line, human detail on stderr, exit
    `EXIT_CANNOT`) instead of argparse's default (usage text on stderr,
    a bare `sys.exit(2)` with no JSON line at all)."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        fail(message, EXIT_CANNOT)


def make_parser(prog: str, description: str = None) -> argparse.ArgumentParser:
    """Build the top-level parser for a script, so a bad CLI argument still
    emits exactly one JSON line before exiting.

    `add_subparsers()` defaults its `parser_class` to `type(self)`, so every
    subcommand parser created from the result of this function inherits the
    same failure behaviour with no further wiring needed."""
    return _FailingArgumentParser(prog=prog, description=description)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj) -> str:
    text = json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return sha256_bytes(text.encode("utf-8"))


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: tmp file in the same directory,
    flush, fsync, then `os.replace`. Leaves no temp file behind, on success
    or on failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".cm-tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, obj) -> None:
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    atomic_write_text(path, text)


def read_json(path: Path, what: str):
    """Read and parse one JSON file, or `fail(EXIT_CANNOT)` naming `what`."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"{what} is missing: {path}", EXIT_CANNOT)
    except OSError as exc:
        fail(f"{what} is unreadable: {path} ({exc})", EXIT_CANNOT)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        fail(f"{what} is invalid JSON: {path} ({exc})", EXIT_CANNOT)


def resolve_root(root_arg: str) -> Path:
    """Resolve `root_arg` to its realpath. `fail(EXIT_CANNOT)` if it is not
    an existing directory. `scaffold.py` does its own resolution instead of
    this, since a fresh root need not exist yet."""
    p = Path(root_arg).resolve()
    if not p.is_dir():
        fail(f"root is not a directory: {root_arg}", EXIT_CANNOT)
    return p


def under_temp_root(path: Path) -> bool:
    """True if the realpath of `path` equals or is inside the realpath of
    any entry of `TEMP_ROOTS`."""
    rp = Path(path).resolve()
    for root in TEMP_ROOTS:
        try:
            root_rp = Path(root).resolve()
        except OSError:
            continue
        if rp == root_rp:
            return True
        try:
            rp.relative_to(root_rp)
            return True
        except ValueError:
            continue
    return False


def load_config(root: Path) -> dict:
    """Read `root/migration.json` and validate it. A missing file, invalid
    JSON, or any validation problem (including an unanswered key) is
    `fail(EXIT_CANNOT)`: from a downstream script's point of view an unready
    config is a missing dependency, not its own judgment to make."""
    root = Path(root)
    cfg = read_json(root / "migration.json", "migration.json")
    import migration_validate  # lazy import: migration_validate imports cm_common

    problems = migration_validate.validate(cfg, root)
    if problems:
        detail = "; ".join(f"{p['key']} ({p['kind']}): {p['message']}" for p in problems)
        fail(f"migration.json has problems: {detail}", EXIT_CANNOT, problems=problems)
    return cfg


def _module_file(base: Path, dotted: str) -> Path:
    """The `.py` file for a dotted module name under `base`: a package
    directory (one already present under `base`) resolves to its
    `__init__.py`; anything else resolves to `<name>.py`."""
    rel = dotted.replace(".", "/")
    pkg_dir = base / rel
    if pkg_dir.is_dir():
        return pkg_dir / "__init__.py"
    return base / (rel + ".py")


def resolved_paths(root: Path, cfg: dict) -> dict:
    """`{"legacy_root", "target_root"}` as absolute `Path`s, resolved
    relative to `root` when the config gives a relative path."""
    root = Path(root)

    def resolve_rel(value: str) -> Path:
        p = Path(value)
        if not p.is_absolute():
            p = root / p
        return p.resolve()

    return {
        "legacy_root": resolve_rel(cfg["legacy_root"]),
        "target_root": resolve_rel(cfg["target_root"]),
    }


def legacy_file(root: Path, cfg: dict, unit: str) -> Path:
    legacy_root = resolved_paths(root, cfg)["legacy_root"]
    return _module_file(legacy_root, unit)


def target_module(cfg: dict, unit: str) -> str:
    legacy_package = cfg["legacy_package"]
    target_package = cfg["target_package"]
    return target_package + unit[len(legacy_package):]


def target_file(root: Path, cfg: dict, unit: str) -> Path:
    target_root = resolved_paths(root, cfg)["target_root"]
    return _module_file(target_root, target_module(cfg, unit))


def require_unit(root: Path, unit: str) -> str:
    """`unit` must be a syntactically valid dotted identifier and a key of
    `inventory.json`'s `"units"`, else `fail(EXIT_CANNOT)`. Every script that
    takes `--unit` calls this before building any path from it, so a
    path-traversal or absolute-path value is refused before it can reach the
    filesystem."""
    segments = unit.split(".") if unit else []
    if not segments or not all(_UNIT_SEGMENT_RE.match(seg) for seg in segments):
        fail(f"not a valid unit id: {unit}", EXIT_CANNOT)
    inventory = read_json(Path(root) / "inventory.json", "inventory.json")
    units = inventory.get("units", {})
    if unit not in units:
        fail(f"unknown unit: {unit}", EXIT_CANNOT)
    return unit


def unit_run_dir(root: Path, unit: str) -> Path:
    d = Path(root) / "runs" / unit
    d.mkdir(parents=True, exist_ok=True)
    return d


def tree_digests(base: Path, exclude_dirs=(".git", "__pycache__")) -> dict:
    """`relpath (posix) -> sha256` for every file under `base`, sorted walk.
    A symlink (file or directory) is recorded as `"symlink:<target>"` and,
    for a directory, is not descended into."""
    base = Path(base)
    result: dict[str, str] = {}
    if not base.exists():
        return result
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        keep_dirs = []
        for d in sorted(dirnames):
            if d in exclude_dirs:
                continue
            full_d = Path(dirpath) / d
            if full_d.is_symlink():
                rel = full_d.relative_to(base).as_posix()
                result[rel] = f"symlink:{os.readlink(full_d)}"
                continue
            keep_dirs.append(d)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = full.relative_to(base).as_posix()
            if full.is_symlink():
                result[rel] = f"symlink:{os.readlink(full)}"
            else:
                result[rel] = sha256_file(full)
    return result


def closure_digests(legacy_base: Path, legacy_package: str, units: list) -> dict:
    """`unit -> sha256` of each unit's file under `legacy_base`. One
    function serves both the staged tree (capture) and the live tree (R0,
    R3, cache key), so equal bytes give equal digests regardless of which
    tree produced them. A unit whose file is absent hashes as the digest of
    empty bytes, so a removed dependency still shows up as a change rather
    than silently vanishing from the map."""
    base = Path(legacy_base)
    result: dict[str, str] = {}
    for unit in units:
        f = _module_file(base, unit)
        result[unit] = sha256_file(f) if f.is_file() else sha256_bytes(b"")
    return result


def unit_closure(inventory: dict, unit: str) -> list:
    """`unit`, its transitive `imports_units`, and every ancestor package of
    any of those that is itself a unit (a package whose `__init__.py` has a
    non-empty body after its docstring -- see `require_unit`/`inventory.py`).
    Importing a module always executes its ancestor packages' `__init__.py`
    first, so an ancestor that is a real unit is an implicit dependency; an
    ancestor that is docstring-only is not a unit at all and is not added
    here (a route rule elsewhere admits it as an unavoidable import, but it
    is never part of this closure). Sorted, de-duplicated."""
    units = inventory.get("units", {})

    def ancestor_units(u):
        parts = u.split(".")
        for i in range(1, len(parts)):
            prefix = ".".join(parts[:i])
            if prefix in units:
                yield prefix

    seen: set = set()
    stack = [unit]
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        for dep in units.get(u, {}).get("imports_units", []):
            if dep not in seen:
                stack.append(dep)
        for anc in ancestor_units(u):
            if anc not in seen:
                stack.append(anc)
    return sorted(seen)


def protected_digests(root: Path, cfg: dict) -> dict:
    """`tree_digests` of the durable root, `legacy_root` and `target_root`,
    with keys prefixed `"root/"`, `"legacy/"`, `"target/"`. Used to bracket
    every sandboxed execution (harness runs, codex dispatch): anything that
    changes here between `before` and `after` happened outside the sandbox
    boundary and is tampering, never a legitimate write of the bracketing
    script's own."""
    paths = resolved_paths(root, cfg)
    result: dict[str, str] = {}
    for prefix, base in (
        ("root", Path(root)),
        ("legacy", paths["legacy_root"]),
        ("target", paths["target_root"]),
    ):
        for rel, digest in tree_digests(base).items():
            result[f"{prefix}/{rel}"] = digest
    return result


def diff_digests(before: dict, after: dict) -> list:
    """Sorted keys added, removed or changed between two digest maps."""
    keys = set(before) | set(after)
    return sorted(k for k in keys if before.get(k) != after.get(k))


def plugin_root() -> Path:
    """`plugins/codebase-migrator`, derived from this file's own location
    (`skills/codebase-migrator/scripts/cm_common.py`)."""
    return Path(__file__).resolve().parents[3]
