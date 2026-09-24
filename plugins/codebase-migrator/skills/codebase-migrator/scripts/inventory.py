#!/usr/bin/env python3
"""W2 static AST inventory of the legacy package (plan section 4.3).

Decides only what a runtime check cannot see: effects and inputs that leave
or enter the Python object graph by way of syntax (a clock read, I/O, a
string-based lookup). Whether a unit keeps persistent state is decided at
runtime by net_capture.py's state snapshot, never here.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cm_common  # noqa: E402

# --- flag tables -----------------------------------------------------------

_UNCONTROLLED_PREFIXES = ("time.", "random.", "uuid.", "secrets.")
_UNCONTROLLED_EXACT = {
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.datetime.today",
    "datetime.date.today",
    "os.getenv",
    "input",
}
_UNCONTROLLED_ENVIRON_PREFIX = "os.environ"

_IO_EXACT = {"open"}
_IO_PREFIXES = ("shutil.", "subprocess.", "socket.", "threading.", "urllib.", "http.", "logging.")
_IO_OS_EXEMPT = {"os.path.join", "os.path.basename", "os.path.dirname", "os.path.splitext"}
_IO_PATH_EXACT_METHODS = {"open", "touch", "rename", "replace", "rmdir", "symlink_to"}

# `_is_io`'s "pathlib.Path.<method>" rule above only fires once the receiver
# resolves to "pathlib.Path" (a direct or constructor-chained reference).
# `p = Path(x); p.write_text(...)` never resolves that way (`p` is an
# ordinary local name), so this is a coarser, name-only pre-filter: once a
# module imports pathlib in ANY form, a call to one of these EXACT method
# names on any receiver is io. `replace`, `rename` and `open` are
# deliberately excluded here (already covered above when the receiver DOES
# resolve) because they collide with common non-Path methods (`str.replace`
# above all) and would false-positive by name alone.
_IO_PATH_METHODS_ANY_RECEIVER = {
    "write_text",
    "write_bytes",
    "unlink",
    "mkdir",
    "touch",
    "rmdir",
    "symlink_to",
    "hardlink_to",
    "chmod",
    "lchmod",
}

_DYNAMIC_EXACT_CALL = {
    "eval",
    "exec",
    "__import__",
    "importlib.import_module",
    "globals",
    "locals",
    "vars",
    "sys.modules.get",
}
_DYNAMIC_ATTR_USE = {"sys.settrace", "sys.setprofile", "sys._getframe"}

# Any reference to these — not only a call — is uncontrolled input: reading
# the environment by attribute, subscript or iteration is exactly as
# uncontrolled as calling it, and `os.environ.get(...)`/`os.getenv(...)`
# still get their own "call:" flag from `_is_uncontrolled` on top of this.
_UNCONTROLLED_REF_EXACT = {"os.environ", "os.getenv"}


def _is_uncontrolled(resolved: str) -> bool:
    if resolved in _UNCONTROLLED_EXACT:
        return True
    if resolved.startswith(_UNCONTROLLED_PREFIXES):
        return True
    if resolved.startswith(_UNCONTROLLED_ENVIRON_PREFIX):
        return True
    return False


def _is_io(resolved: str) -> bool:
    if resolved in _IO_EXACT:
        return True
    if resolved.startswith("os.") and resolved not in _IO_OS_EXEMPT:
        return True
    if resolved.startswith(_IO_PREFIXES):
        return True
    if resolved.startswith("pathlib.Path."):
        tail = resolved.rsplit(".", 1)[-1]
        if "write" in tail or "unlink" in tail or "mkdir" in tail or tail in _IO_PATH_EXACT_METHODS:
            return True
    return False


def _dotted(node: ast.AST | None) -> str | None:
    """Resolve a Name/Attribute chain to a dotted string, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    return None


def _apply_alias(dotted: str, import_alias: dict, value_alias: dict) -> str:
    parts = dotted.split(".")
    head, rest = parts[0], parts[1:]
    if head in value_alias:
        base = value_alias[head]
    elif head in import_alias:
        base = import_alias[head]
    else:
        base = head
    return base if not rest else base + "." + ".".join(rest)


def _resolve_relative(module: str, package: str, level: int, node_module: str | None) -> str:
    """Resolve a relative import's absolute dotted target.

    `module` is treated as a regular (non-package) module: level 1 reaches
    its own parent package. A package unit (an `__init__.py`, whose own
    `__package__` is itself, not its parent) gets this right by having its
    caller pass `module + ".__init__"` instead of `module` — the synthetic
    trailing segment is exactly what a regular module's own filename would
    supply, so the existing "strip one segment" arithmetic below produces
    the package's own name at level 1, its parent at level 2, and so on,
    with no separate code path. `analyze_module`'s `is_package` flag drives
    this; callers that already have a real file (`_build_unit`,
    `import_closure`) detect it from the filename.
    """
    if "." in module:
        base_parts = module.split(".")[:-1]
    else:
        base_parts = [package] if package else []
    strip = level - 1
    if strip:
        base_parts = base_parts[: len(base_parts) - strip] if strip < len(base_parts) else []
    base = ".".join(base_parts)
    if node_module:
        return f"{base}.{node_module}" if base else node_module
    return base


def _prescan_aliases(tree: ast.Module, module: str, package: str) -> tuple[dict, dict]:
    """One alias table per module (plan 4.3): import aliases plus a name bound
    to a flagged reference, at module or function scope, including a default
    argument bound to one."""
    import_alias: dict[str, str] = {}
    value_alias: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    import_alias[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module
            else:
                base = _resolve_relative(module, package, node.level, node.module)
            for alias in node.names:
                local = alias.asname or alias.name
                full = f"{base}.{alias.name}" if base else alias.name
                import_alias[local] = full
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            dotted = _dotted(node.value)
            if dotted:
                resolved = _apply_alias(dotted, import_alias, value_alias)
                if _is_uncontrolled(resolved):
                    value_alias[node.targets[0].id] = resolved
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            positional = list(args.posonlyargs) + list(args.args)
            offset = len(positional) - len(args.defaults)
            for i, default in enumerate(args.defaults):
                dotted = _dotted(default)
                if dotted:
                    resolved = _apply_alias(dotted, import_alias, value_alias)
                    if _is_uncontrolled(resolved):
                        value_alias[positional[offset + i].arg] = resolved
            for kwarg, default in zip(args.kwonlyargs, args.kw_defaults):
                if default is None:
                    continue
                dotted = _dotted(default)
                if dotted:
                    resolved = _apply_alias(dotted, import_alias, value_alias)
                    if _is_uncontrolled(resolved):
                        value_alias[kwarg.arg] = resolved
    return import_alias, value_alias


def _imports_pathlib(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "pathlib" or alias.name.startswith("pathlib.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "pathlib" or (node.module is not None and node.module.startswith("pathlib.")):
                return True
    return False


class _Walker(ast.NodeVisitor):
    def __init__(self, module: str, package: str, import_alias: dict, value_alias: dict, imports_pathlib: bool = False):
        self.module = module
        self.package = package
        self.import_alias = import_alias
        self.value_alias = value_alias
        self.imports_pathlib = imports_pathlib
        self.stack: list[str] = []
        self.uncontrolled_input: list[str] = []
        self.io: list[str] = []
        self.dynamic_call: list[str] = []
        self.imports: set[str] = set()

    def _qual(self) -> str:
        return ".".join(self.stack) if self.stack else "<module>"

    def _resolve(self, node: ast.AST | None) -> str | None:
        dotted = _dotted(node)
        if dotted is None:
            return None
        return _apply_alias(dotted, self.import_alias, self.value_alias)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.name
            if name == self.package or name.startswith(self.package + "."):
                self.imports.add(name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0:
            base = node.module
        else:
            base = _resolve_relative(self.module, self.package, node.level, node.module)
        if base and (base == self.package or base.startswith(self.package + ".")):
            self.imports.add(base)
            for alias in node.names:
                self.imports.add(f"{base}.{alias.name}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        resolved = self._resolve(node.func)
        func = self._qual()
        if resolved is not None:
            if _is_uncontrolled(resolved):
                self.uncontrolled_input.append(f"call:{resolved}@{func}")
            if _is_io(resolved):
                self.io.append(f"call:{resolved}@{func}")
            if resolved == "getattr":
                if len(node.args) >= 2 and not isinstance(node.args[1], ast.Constant):
                    self.dynamic_call.append(f"getattr@{func}")
            elif resolved == "setattr":
                self.dynamic_call.append(f"setattr@{func}")
            elif resolved in _DYNAMIC_EXACT_CALL:
                self.dynamic_call.append(f"{resolved}@{func}")
        else:
            # `_dotted` cannot see through a CALL receiver (e.g.
            # `pathlib.Path(p).write_text(...)`, or aliased `Path(p)...`):
            # resolve the constructor call's own func specifically, so the
            # method name still gets checked as `pathlib.Path.<method>`.
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
                ctor_resolved = self._resolve(node.func.value.func)
                if ctor_resolved == "pathlib.Path":
                    path_resolved = f"pathlib.Path.{node.func.attr}"
                    if _is_io(path_resolved):
                        self.io.append(f"call:{path_resolved}@{func}")
        # Coarser, name-only pre-filter: `p = Path(x); p.write_text(...)`
        # never resolves `p` to "pathlib.Path" above (no type inference), so
        # once the module imports pathlib in any form, a call to one of the
        # write-ish method names on ANY receiver is flagged by name alone.
        if (
            self.imports_pathlib
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _IO_PATH_METHODS_ANY_RECEIVER
        ):
            self.io.append(f"call:{node.func.attr}@{func}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        resolved = self._resolve(node)
        if resolved in _DYNAMIC_ATTR_USE:
            self.dynamic_call.append(f"{resolved}@{self._qual()}")
        if resolved in _UNCONTROLLED_REF_EXACT:
            self.uncontrolled_input.append(f"ref:{resolved}@{self._qual()}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # Catches a bare aliased reference (`from os import environ`, then
        # `environ` or `environ["X"]` used directly with no `os.` prefix to
        # see as an Attribute node) and a reference to `os.getenv` assigned
        # to a name without being called.
        resolved = self._resolve(node)
        if resolved in _UNCONTROLLED_REF_EXACT:
            self.uncontrolled_input.append(f"ref:{resolved}@{self._qual()}")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        resolved = self._resolve(node.value)
        if resolved == "sys.modules":
            self.dynamic_call.append(f"sys.modules@{self._qual()}")
        self.generic_visit(node)


def _literal_str_list(node: ast.AST) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        out = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.append(elt.value)
            else:
                return None
        return out
    return None


def _public_names_and_spans(tree: ast.Module) -> tuple[list[str], dict[str, list[int]]]:
    dunder_all: list[str] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "__all__":
                dunder_all = _literal_str_list(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "__all__":
            if node.value is not None:
                dunder_all = _literal_str_list(node.value)

    spans: dict[str, list[int]] = {}
    top_level: dict[str, ast.AST] = {}
    order: list[str] = []
    for node in tree.body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            start = node.lineno
            if node.decorator_list:
                start = min(start, node.decorator_list[0].lineno)
            spans[name] = [start, node.end_lineno]
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name is not None and name not in top_level:
            top_level[name] = node
            order.append(name)

    if dunder_all is not None:
        names = [n for n in dunder_all if n in top_level or True]
    else:
        names = [n for n in order if not n.startswith("_")]
    spans = {n: s for n, s in spans.items() if n in names}
    return names, spans


def analyze_module(source: str, module: str, package: str, *, is_package: bool = False) -> dict:
    """`is_package` is keyword-only with a safe default (False): every
    existing caller passing exactly the pinned 3 positional args is
    unaffected. Pass it when `module`'s file is an `__init__.py`, so a
    relative import inside it resolves against itself (plan 4.3's
    `_resolve_relative`), not against its parent package."""
    tree = ast.parse(source, filename=module)
    resolve_module = f"{module}.__init__" if is_package else module
    import_alias, value_alias = _prescan_aliases(tree, resolve_module, package)
    walker = _Walker(resolve_module, package, import_alias, value_alias, imports_pathlib=_imports_pathlib(tree))
    walker.visit(tree)
    names, spans = _public_names_and_spans(tree)
    return {
        "uncontrolled_input": sorted(set(walker.uncontrolled_input)),
        "io": sorted(set(walker.io)),
        "dynamic_call": sorted(set(walker.dynamic_call)),
        "public_symbols": [f"{module}:{n}" for n in names],
        "symbol_spans": {f"{module}:{n}": v for n, v in spans.items()},
        "imports": sorted(walker.imports),
    }


def import_closure(base: Path, package: str, module: str) -> list[str]:
    """Transitive closure of `module`'s candidate imports, kept only when the
    candidate resolves to a real file under `base/<package>`."""
    seen: set[str] = set()
    pending = [module]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        candidate_file = base / (current.replace(".", "/") + ".py")
        candidate_init = base / current.replace(".", "/") / "__init__.py"
        if not (candidate_file.is_file() or candidate_init.is_file()):
            continue
        seen.add(current)
        is_package = not candidate_file.is_file()
        source_file = candidate_init if is_package else candidate_file
        try:
            source = source_file.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            info = analyze_module(source, current, package, is_package=is_package)
        except SyntaxError:
            continue
        for cand in info["imports"]:
            if cand not in seen:
                pending.append(cand)
    return sorted(seen)


def _module_file(base: Path, module: str) -> Path | None:
    """The existing `.py` file for `module` under `base`, package or plain,
    else `None`."""
    candidate_file = base / (module.replace(".", "/") + ".py")
    if candidate_file.is_file():
        return candidate_file
    candidate_init = base / module.replace(".", "/") / "__init__.py"
    if candidate_init.is_file():
        return candidate_init
    return None


def closure_files(base: Path, package: str, module: str) -> list[str]:
    """Every file Python's own import machinery executes when importing
    `module`: the file of each member of `import_closure`, plus every
    EXISTING `__init__.py` along each member's ancestor-package chain
    (within `package`, the package root's own `__init__.py` included) —
    whether or not that ancestor is itself a discovered "unit". Importing
    `a.b.c` always runs `a/__init__.py` then `a/b/__init__.py` first, a
    docstring-only or otherwise trivial one included, so a closure built
    only from discovered units silently misses files that genuinely execute
    at import time. Sorted, posix, relative to `base`."""
    files: set[str] = set()
    for mod in import_closure(base, package, module):
        mod_file = _module_file(base, mod)
        if mod_file is not None:
            files.add(mod_file.relative_to(base).as_posix())
        parts = mod.split(".")
        for i in range(1, len(parts)):
            ancestor = ".".join(parts[:i])
            init_path = base / ancestor.replace(".", "/") / "__init__.py"
            if init_path.is_file():
                files.add(init_path.relative_to(base).as_posix())
    return sorted(files)


# --- inventory build ---------------------------------------------------------


def _is_init_unit(path: Path) -> bool:
    # SyntaxError is NOT caught here: a syntax-invalid __init__.py must fail
    # the same named parse-error way as any other file (build_inventory's
    # caller of _discover_units catches it), never be silently treated as
    # "not a unit". Only a genuinely unreadable file returns False.
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return False
    tree = ast.parse(source, filename=str(path))
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return len(body) > 0


def _discover_units(base: Path, package: str) -> dict[str, Path]:
    pkg_dir = base / package
    units: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(pkg_dir):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        rel_dir = Path(dirpath).relative_to(base)
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            file_path = Path(dirpath) / fname
            if fname == "__init__.py":
                mod_name = ".".join(rel_dir.parts)
                if not mod_name:
                    continue
                if not _is_init_unit(file_path):
                    continue
                units[mod_name] = file_path
            else:
                mod_name = ".".join((*rel_dir.parts, fname[:-3]))
                units[mod_name] = file_path
    return dict(sorted(units.items()))


def _executable_lines(source: str) -> list[int]:
    """§4.3: for every nested code object, its co_lines() integer lines,
    minus the def/class/decorator prologue line when a later line is also
    present."""
    code = compile(source, "<inventory>", "exec")
    lines: set[int] = set()

    def _walk(c) -> None:
        nested = [const for const in c.co_consts if hasattr(const, "co_code")]
        if c.co_name != "<module>":
            own = {ln for _, _, ln in c.co_lines() if ln is not None}
            if own:
                if len(own) > 1 and c.co_firstlineno in own:
                    own.discard(c.co_firstlineno)
                lines.update(own)
        for const in nested:
            _walk(const)

    for const in code.co_consts:
        if hasattr(const, "co_code"):
            _walk(const)
    return sorted(lines)


def _build_unit(unit: str, path: Path, package: str, units: dict[str, Path]) -> dict:
    source = path.read_text(encoding="utf-8")
    is_package = path.name == "__init__.py"
    info = analyze_module(source, unit, package, is_package=is_package)
    imports_units = sorted({c for c in info["imports"] if c in units and c != unit})
    imported_symbols: set[str] = set()
    # Local names bound to a whole module-unit (not a specific symbol):
    # `from pkg import M` or `import pkg.M as M` where pkg.M is itself a
    # discovered unit. `from shop import money` then `money.round_money(x)`
    # (no direct `from shop.money import round_money`) is exactly this
    # shape: the import statement alone names no symbol, so every
    # `M.name` attribute access anywhere in the file has to be walked too.
    module_alias: dict[str, str] = {}
    resolve_unit = f"{unit}.__init__" if is_package else unit
    tree = ast.parse(source, filename=unit)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module
            else:
                base = _resolve_relative(resolve_unit, package, node.level, node.module)
            # Match against the package tree by NAME (as `_Walker.visit_ImportFrom`
            # does for the "imports" field), never by `base in units`: the
            # top-level package's own `__init__.py` is almost always trivial
            # (docstring-only, not a discovered unit), and `from pkg import
            # money` is exactly that shape. Requiring `base` itself to be a
            # unit would silently skip every such import.
            if base and (base == package or base.startswith(package + ".")):
                for alias in node.names:
                    target_unit = f"{base}.{alias.name}"
                    if target_unit in units:
                        module_alias[alias.asname or alias.name] = target_unit
                    else:
                        imported_symbols.add(f"{base}:{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname and alias.name in units:
                    module_alias[alias.asname] = alias.name
    if module_alias:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name)
                and node.value.id in module_alias
            ):
                target_unit = module_alias[node.value.id]
                if f"{target_unit}.{node.attr}" not in units:
                    imported_symbols.add(f"{target_unit}:{node.attr}")
    flags = {
        "uncontrolled_input": info["uncontrolled_input"],
        "dynamic_call": info["dynamic_call"],
        "io": info["io"],
    }
    ineligible_reasons = list(flags["uncontrolled_input"]) + list(flags["io"]) + list(flags["dynamic_call"])
    return {
        "file": _rel_file(unit, path),
        "source_sha256": cm_common.sha256_bytes(source.encode("utf-8")),
        "public_symbols": info["public_symbols"],
        "symbol_spans": info["symbol_spans"],
        "imports_units": imports_units,
        "imported_symbols": sorted(imported_symbols),
        "flags": flags,
        "eligible": len(ineligible_reasons) == 0,
        "ineligible_reasons": ineligible_reasons,
        "executable_lines": _executable_lines(source),
    }


def _rel_file(unit: str, path: Path) -> str:
    return unit.replace(".", "/") + ("/__init__.py" if path.name == "__init__.py" else ".py")


def build_inventory(root: Path, cfg: dict) -> dict:
    paths = cm_common.resolved_paths(root, cfg)
    legacy_root = paths["legacy_root"]
    package = cfg["legacy_package"]
    try:
        units = _discover_units(legacy_root, package)
    except SyntaxError as exc:
        cm_common.fail(f"failed to parse {exc.filename}: {exc}", cm_common.EXIT_FAIL, file=str(exc.filename))
    if not units:
        cm_common.fail(f"no units found under {legacy_root / package}", cm_common.EXIT_FAIL)

    unit_rows: dict[str, dict] = {}
    for unit, path in units.items():
        try:
            unit_rows[unit] = _build_unit(unit, path, package, units)
        except SyntaxError as exc:
            cm_common.fail(f"failed to parse {path}: {exc}", cm_common.EXIT_FAIL, file=str(path))

    # Static eligibility over `cm_common.unit_closure`: unit plus its
    # transitive imports_units AND every ancestor package of any of those
    # that is itself an executable unit. Importing a module always runs its
    # ancestor packages' __init__.py first, so an ancestor unit with a flag
    # is exactly as disqualifying as an explicit import of a flagged
    # dependency. `unit_closure` is already fully transitive, and each
    # member's OWN flags (immutable, set once above) are what we check —
    # never a row's `ineligible_reasons`, which this very loop is about to
    # rewrite, so checking it would make the result depend on dict iteration
    # order.
    def _own_flags(row: dict) -> list[str]:
        return list(row["flags"]["uncontrolled_input"]) + list(row["flags"]["io"]) + list(row["flags"]["dynamic_call"])

    inventory_so_far = {"units": unit_rows}
    for unit, row in unit_rows.items():
        own_reasons = _own_flags(row)
        for other in cm_common.unit_closure(inventory_so_far, unit):
            if other == unit:
                continue
            if _own_flags(unit_rows[other]):
                own_reasons.append(f"ineligible dependency: {other}")
        row["eligible"] = len(own_reasons) == 0
        row["ineligible_reasons"] = own_reasons

    edges = sorted(
        [unit, dep] for unit, row in unit_rows.items() for dep in row["imports_units"]
    )

    imported_anywhere: set[str] = set()
    for row in unit_rows.values():
        imported_anywhere.update(row["imported_symbols"])
    unreferenced_public = sorted(
        sym for row in unit_rows.values() for sym in row["public_symbols"] if sym not in imported_anywhere
    )

    return {
        "schema": 1,
        "legacy_package": package,
        "units": unit_rows,
        "edges": edges,
        "unreferenced_public": unreferenced_public,
    }


def main() -> int:
    parser = cm_common.make_parser(prog="inventory.py")
    parser.add_argument("--root", required=True)
    args = parser.parse_args()

    root = cm_common.resolve_root(args.root)
    cfg = cm_common.load_config(root)
    inventory = build_inventory(root, cfg)
    cm_common.atomic_write_json(root / "inventory.json", inventory)

    eligible = sum(1 for row in inventory["units"].values() if row["eligible"])
    ineligible = {
        unit: row["ineligible_reasons"]
        for unit, row in inventory["units"].items()
        if not row["eligible"]
    }
    cm_common.emit({
        "ok": True,
        "units": len(inventory["units"]),
        "eligible": eligible,
        "ineligible": ineligible,
    })
    return cm_common.EXIT_OK


if __name__ == "__main__":
    cm_common.run_main(main)
