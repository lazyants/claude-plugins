"""tests/extract_noreturn_annotation.test.py

Issue #136 (LOW): ``extract.py.template``'s two fatal-abort helpers,
``_missing_dep`` and ``die``, unconditionally ``sys.exit(1)`` but were not
annotated ``typing.NoReturn`` -- so Pyright reports every optional-dependency
import that follows a ``_missing_dep(...)`` call (yaml/jsonschema/bs4/lxml)
as "possibly unbound", since without the annotation the type checker can't
prove the helper never returns. Every other shipped script already
annotates its fatal helper ``-> NoReturn`` (e.g.
``validate_extraction.py``'s ``_die_missing_dependency``) -- extract.py.template
was the sole deviating file.

This locks down, via a static AST check (no need to import the template or
its optional bs4/lxml/yaml/jsonschema dependencies), that the template:
  - imports ``NoReturn`` from ``typing`` at module level, and
  - annotates both ``_missing_dep`` and ``die`` with a ``NoReturn`` return
    type.
"""
import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "templates"
    / "extract.py.template"
)


def _parse_template():
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(TEMPLATE_PATH))


def _is_noreturn(annotation) -> bool:
    """True if `annotation` names `NoReturn`, whether imported bare
    (`ast.Name`) or referenced as `typing.NoReturn` (`ast.Attribute`)."""
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name):
        return annotation.id == "NoReturn"
    if isinstance(annotation, ast.Attribute):
        return annotation.attr == "NoReturn"
    return False


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_extract_template_parses_cleanly():
    """Sanity check: the template is valid Python (guards against a typo in
    the fix itself breaking the file)."""
    _parse_template()


def test_extract_template_imports_noreturn_from_typing():
    tree = _parse_template()
    imported_noreturn = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "typing"
        and any(alias.name == "NoReturn" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imported_noreturn, "expected a top-level `from typing import NoReturn`"


def test_missing_dep_annotated_noreturn():
    tree = _parse_template()
    func = _find_function(tree, "_missing_dep")
    assert func is not None, "_missing_dep function not found"
    assert _is_noreturn(func.returns), (
        f"_missing_dep must be annotated `-> NoReturn`, got {ast.dump(func.returns) if func.returns else None}"
    )


def test_die_annotated_noreturn():
    tree = _parse_template()
    func = _find_function(tree, "die")
    assert func is not None, "die function not found"
    assert _is_noreturn(func.returns), (
        f"die must be annotated `-> NoReturn`, got {ast.dump(func.returns) if func.returns else None}"
    )
