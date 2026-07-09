"""Cross-file drift guard for the two independent proper-noun extractors.

``bootstrap_names.py`` (the original historiettes-t3 run-builder) and
``language_smoke_report.py`` (its generalized re-implementation) deliberately
carry SEPARATE copies of the same sentence-boundary algorithm -- no shared
import, because ``language_smoke_report.py`` is copied into an isolated
``${durable_root}/scripts/`` and run as a subprocess, so it cannot depend on a
sibling module being present. Because the copies are independent, their
boundary literals can silently drift -- which is exactly what produced issue
#80 (bootstrap's ``TERMINATORS`` had fallen behind, missing the em-dash / other
delimiters that ``language_smoke_report.py`` had gained). This test pins the
two boundary constants byte-identical so the next edit to one must touch the
other.

Both scripts live outside any Python package, so they are loaded via
``importlib`` from their real paths (same mechanism as
``bootstrap_names.test.py``). Import is side-effect-free: each script's
``main()`` is gated behind ``if __name__ == "__main__"`` and touches no
argv/schema at import time.
"""
import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
BOOTSTRAP_PATH = SCRIPTS_DIR / "bootstrap_names.py"
SMOKE_PATH = SCRIPTS_DIR / "language_smoke_report.py"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bn = _load_module("bootstrap_names_drift_under_test", BOOTSTRAP_PATH)
lsr = _load_module("language_smoke_report_drift_under_test", SMOKE_PATH)


def test_terminators_identical_across_both_extractors():
    # The set of chars that END a sentence must be the same in both copies, or
    # one extractor breaks a run where the other fuses it (issue #80).
    assert bn.TERMINATORS == lsr.TERMINATORS


def test_wrapper_set_identical_across_both_extractors():
    # The set of transparent bracket/quote wrappers the back-scan skips must be
    # the same in both copies (bootstrap names it WRAPPERS; the smoke report
    # names it _WRAPPERS by its own underscore convention).
    assert bn.WRAPPERS == lsr._WRAPPERS


def test_wrappers_disjoint_from_terminators():
    # The whole fix depends on wrappers NOT being terminators: the back-scan
    # skips wrappers to find a terminator behind them, so any overlap would let
    # a wrapper both stop and not-stop a run. Enforce disjointness in both.
    assert not (bn.WRAPPERS & bn.TERMINATORS)
    assert not (lsr._WRAPPERS & lsr.TERMINATORS)
