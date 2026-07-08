"""tests/dependency_preflight.test.py

Targets the ``requirements.txt`` import-preflight discipline shared across
``profile_validate.py`` / ``canon_validate.py`` (``jsonschema``, ``PyYAML``)
and ``extract.py.template`` (``beautifulsoup4``/``lxml``) -- see the build
spec's "requirements.txt" section and each script's own module docstring.

Every script that needs one of these packages MUST wrap its import in a
try/except ``ImportError`` that prints an actionable ``pip install -r
requirements.txt`` message NAMING the specific missing package, and exits
non-zero -- never an unhandled ``ImportError``/raw traceback. This file
locks that down per-package, per-script:

  - ``profile_validate.py``'s ``dependency_preflight()`` function: one
    try/except each for ``yaml``/``jsonschema`` (exit code 2).
  - ``canon_validate.py``'s MODULE-LEVEL try/except (executes at import
    time, not behind a callable) covering ``import jsonschema`` AND
    ``from referencing import Registry, Resource`` in the SAME try block --
    either import failing must still surface the same jsonschema-named,
    actionable message (exit code 1).
  - ``extract.py.template``'s FOUR independent try/except blocks (PyYAML,
    jsonschema, beautifulsoup4, lxml -- exit code 1 each), PLUS its
    second-level preflight that actually PARSES two tiny fixture strings
    (``BeautifulSoup("<a>x</a>", "lxml")`` and the ``"xml"`` variant), each
    in its own try/except, to catch a missing parser BACKEND
    (``bs4.FeatureNotFound``) separately from a missing ``bs4`` import --
    and separately from each other (the loop tries ``"lxml"`` first, and
    only reaches ``"xml"`` if the ``"lxml"`` backend actually worked).

A one-shot "control" test per script confirms the preflight is a no-op (no
``SystemExit``, real modules bound) when every dependency is genuinely
installed -- otherwise a test that only ever exercises the failure branch
could pass against a preflight that ALWAYS exits, missing dependencies or
not.

None of the three target scripts is a Python package, so each is loaded
fresh, directly from its real shipped path, via ``importlib`` -- never
registered into ``sys.modules`` (avoids any collision between the
per-test-case fresh copies this file creates, and avoids polluting any
other test file's own fresh load of the same script). Missing dependencies
are simulated the standard way: setting ``sys.modules[name] = None`` makes
the interpreter's own import machinery raise ``ImportError`` for that name,
without needing the package to be genuinely absent from the environment.
``monkeypatch.setitem``/``setattr`` are used throughout so every mutation is
undone automatically at test teardown, regardless of pass/fail.
"""
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

# Real, top-level, unpoisoned imports -- done ONCE here, before any test
# monkeypatches sys.modules to simulate a missing dependency. `bs4.builder`
# registers its lxml/xml tree-builder classes as a ONE-TIME side effect at
# bs4.builder's own FIRST import (`from . import _lxml` inside a swallowed
# try/except) -- if that first import happened to occur while
# `sys.modules["lxml"]` was poisoned to None by an earlier test in this same
# process, the registry would starve permanently for the rest of the run,
# breaking even a later, fully-unpoisoned "control case" test with a bogus
# FeatureNotFound. Importing for real up front guarantees the registration
# already happened while lxml was genuinely importable, regardless of test
# order below.
import bs4  # noqa: F401
import jsonschema  # noqa: F401
import lxml  # noqa: F401
import referencing  # noqa: F401
import yaml  # noqa: F401

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"

PROFILE_VALIDATE_PATH = SCRIPTS_DIR / "profile_validate.py"
CANON_VALIDATE_PATH = SCRIPTS_DIR / "canon_validate.py"
EXTRACT_TEMPLATE_PATH = TEMPLATES_DIR / "extract.py.template"


def _load_module_fresh(path: Path, name: str):
    """Loads ``path`` as a brand-new, isolated module object -- NOT
    registered in ``sys.modules`` -- using an explicit ``SourceFileLoader``
    so this also works for ``extract.py.template``, whose ``.template``
    suffix ``importlib.util.spec_from_file_location`` cannot infer a loader
    for on its own (verified: it returns ``None`` for that path without an
    explicit loader)."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _load_profile_validate():
    return _load_module_fresh(PROFILE_VALIDATE_PATH, "profile_validate_preflight_test")


def _exec_canon_validate_fresh():
    """Returns a callable that, when invoked, executes ``canon_validate.py``
    fresh (its dependency preflight runs at MODULE level, not behind a
    function, so exercising it means exec'ing the module itself, wrapped by
    the caller in ``pytest.raises(SystemExit)``)."""
    def _do_exec():
        return _load_module_fresh(CANON_VALIDATE_PATH, "canon_validate_preflight_test")
    return _do_exec


def _exec_extract_template_fresh():
    def _do_exec():
        return _load_module_fresh(EXTRACT_TEMPLATE_PATH, "extract_template_preflight_test")
    return _do_exec


# ---------------------------------------------------------------------------
# profile_validate.py -- dependency_preflight() (exit code 2)
# ---------------------------------------------------------------------------

def test_profile_validate_missing_pyyaml_is_actionable(monkeypatch, capsys):
    module = _load_profile_validate()
    monkeypatch.setitem(sys.modules, "yaml", None)

    with pytest.raises(SystemExit) as exc_info:
        module.dependency_preflight()

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "PyYAML" in err
    assert "jsonschema" not in err
    assert "pip install -r" in err
    assert "requirements.txt" in err
    assert err.strip().startswith("ERROR: this plugin requires")


def test_profile_validate_missing_jsonschema_is_actionable(monkeypatch, capsys):
    module = _load_profile_validate()
    monkeypatch.setitem(sys.modules, "jsonschema", None)

    with pytest.raises(SystemExit) as exc_info:
        module.dependency_preflight()

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "jsonschema" in err
    assert "PyYAML" not in err
    assert "pip install -r" in err
    assert "requirements.txt" in err
    assert err.strip().startswith("ERROR: this plugin requires")


def test_profile_validate_missing_yaml_short_circuits_before_jsonschema(monkeypatch, capsys):
    """yaml is checked FIRST -- if it is also missing, the jsonschema check
    is never reached, and the message names only PyYAML."""
    module = _load_profile_validate()
    monkeypatch.setitem(sys.modules, "yaml", None)
    monkeypatch.setitem(sys.modules, "jsonschema", None)

    with pytest.raises(SystemExit) as exc_info:
        module.dependency_preflight()

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "PyYAML" in err
    assert "jsonschema" not in err


def test_profile_validate_dependency_preflight_control_case(monkeypatch, capsys):
    """Control case: with both real packages genuinely importable, the
    preflight is a no-op -- no SystemExit, no stderr output, and the
    module's own `yaml`/`jsonschema` globals get bound to real modules.
    Without this, a preflight that unconditionally exits would still pass
    the two failure-path tests above."""
    module = _load_profile_validate()

    module.dependency_preflight()

    err = capsys.readouterr().err
    assert err == ""
    assert module.yaml is not None
    assert module.jsonschema is not None
    assert module.yaml.__name__ == "yaml"
    assert module.jsonschema.__name__ == "jsonschema"


# ---------------------------------------------------------------------------
# canon_validate.py -- module-level try/except (exit code 1)
# ---------------------------------------------------------------------------

def test_canon_validate_missing_jsonschema_is_actionable(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    do_exec = _exec_canon_validate_fresh()

    with pytest.raises(SystemExit) as exc_info:
        do_exec()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "jsonschema" in err
    assert "canon_validate.py requires" in err
    assert "pip install -r requirements.txt" in err
    assert "import error" in err


def test_canon_validate_missing_referencing_still_names_jsonschema(monkeypatch, capsys):
    """`import jsonschema` and `from referencing import Registry, Resource`
    share ONE try/except -- referencing missing (jsonschema itself present)
    must raise the same jsonschema-named actionable message, not a bare
    'referencing' traceback."""
    monkeypatch.setitem(sys.modules, "referencing", None)
    do_exec = _exec_canon_validate_fresh()

    with pytest.raises(SystemExit) as exc_info:
        do_exec()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "jsonschema" in err
    assert "canon_validate.py requires" in err
    assert "pip install -r requirements.txt" in err


def test_canon_validate_control_case_loads_cleanly(capsys):
    """Control case: with jsonschema/referencing genuinely importable,
    canon_validate.py loads without exiting and its jsonschema-dependent
    module-level constants exist."""
    do_exec = _exec_canon_validate_fresh()

    module = do_exec()

    err = capsys.readouterr().err
    assert err == ""
    assert module.jsonschema is not None
    assert module.RESEARCH_MODES == ("live", "offline")


# ---------------------------------------------------------------------------
# extract.py.template -- four independent module-level try/except blocks
# (exit code 1 each), PLUS the two-level bs4/lxml parser-backend preflight.
# ---------------------------------------------------------------------------

def test_extract_template_missing_pyyaml_is_actionable(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "yaml", None)
    do_exec = _exec_extract_template_fresh()

    with pytest.raises(SystemExit) as exc_info:
        do_exec()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "PyYAML" in err
    assert "pip install PyYAML" in err
    assert "pip install -r requirements.txt" in err
    assert err.strip().startswith("ERROR: this plugin requires")


def test_extract_template_missing_jsonschema_is_actionable(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    do_exec = _exec_extract_template_fresh()

    with pytest.raises(SystemExit) as exc_info:
        do_exec()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "jsonschema" in err
    assert "pip install jsonschema" in err
    assert "pip install -r requirements.txt" in err


def test_extract_template_missing_beautifulsoup4_is_actionable(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "bs4", None)
    do_exec = _exec_extract_template_fresh()

    with pytest.raises(SystemExit) as exc_info:
        do_exec()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "beautifulsoup4" in err
    assert "pip install beautifulsoup4" in err
    assert "pip install -r requirements.txt" in err


def test_extract_template_missing_lxml_package_is_actionable(monkeypatch, capsys):
    """`import lxml` itself failing (the package is genuinely absent) --
    distinct from the FeatureNotFound parser-backend cases below, and
    checked AFTER the bs4 import succeeds."""
    monkeypatch.setitem(sys.modules, "lxml", None)
    do_exec = _exec_extract_template_fresh()

    with pytest.raises(SystemExit) as exc_info:
        do_exec()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "lxml" in err
    assert "pip install lxml" in err
    assert "pip install -r requirements.txt" in err
    assert "parser backend" in err


def test_extract_template_missing_lxml_parser_backend_is_actionable(monkeypatch, capsys):
    """bs4 imports fine and the `lxml` PACKAGE imports fine, but bs4's
    "lxml" parser BACKEND is unavailable (FeatureNotFound) -- must be caught
    at preflight time (this two-tiny-fixture-string parse), not mid-
    extraction, with a backend-named message distinct from the plain
    'lxml package missing' case above."""
    import bs4

    calls = []

    class FakeBeautifulSoup:
        def __new__(cls, markup, backend=None, *args, **kwargs):
            calls.append(backend)
            if backend == "lxml":
                raise bs4.FeatureNotFound(
                    "Couldn't find a tree builder with the features you "
                    "requested: lxml"
                )
            return object.__new__(cls)

    monkeypatch.setattr(bs4, "BeautifulSoup", FakeBeautifulSoup)
    do_exec = _exec_extract_template_fresh()

    with pytest.raises(SystemExit) as exc_info:
        do_exec()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "lxml" in err
    assert "'lxml' parser backend is unavailable" in err
    assert "pip install lxml" in err
    # The loop tries "lxml" first and exits on FeatureNotFound before ever
    # reaching "xml" -- lock in the ordering, not just the end message.
    assert calls == ["lxml"]


def test_extract_template_missing_xml_parser_backend_only_is_actionable(monkeypatch, capsys):
    """The "lxml" backend fixture parse SUCCEEDS, but the "xml" one raises
    FeatureNotFound -- proves the two backend checks are genuinely
    independent (not just the first one always firing), and that the
    actionable message names the SPECIFIC failing backend ("xml"), not the
    first one tried."""
    import bs4

    calls = []

    class FakeBeautifulSoup:
        def __new__(cls, markup, backend=None, *args, **kwargs):
            calls.append(backend)
            if backend == "xml":
                raise bs4.FeatureNotFound(
                    "Couldn't find a tree builder with the features you "
                    "requested: xml"
                )
            return object.__new__(cls)

    monkeypatch.setattr(bs4, "BeautifulSoup", FakeBeautifulSoup)
    do_exec = _exec_extract_template_fresh()

    with pytest.raises(SystemExit) as exc_info:
        do_exec()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "'xml' parser backend is unavailable" in err
    assert "'lxml' parser backend is unavailable" not in err
    assert "pip install lxml" in err
    # Both backends were actually attempted, "lxml" first (and it silently
    # succeeded), "xml" second (and it's the one that failed).
    assert calls == ["lxml", "xml"]


def test_extract_template_control_case_loads_cleanly(capsys):
    """Control case: with beautifulsoup4/lxml/jsonschema/PyYAML all
    genuinely installed AND the real lxml/xml parser backends both
    functional, extract.py.template loads without exiting."""
    do_exec = _exec_extract_template_fresh()

    module = do_exec()

    err = capsys.readouterr().err
    assert err == ""
    assert module.yaml is not None
    assert module.jsonschema is not None
    assert module.BeautifulSoup is not None
    assert module.lxml is not None
