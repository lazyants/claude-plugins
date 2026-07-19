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

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
BOOTSTRAP_PATH = SCRIPTS_DIR / "bootstrap_names.py"
SMOKE_PATH = SCRIPTS_DIR / "language_smoke_report.py"
FINAL_AUDIT_PATH = SCRIPTS_DIR / "final_audit.py"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bn = _load_module("bootstrap_names_drift_under_test", BOOTSTRAP_PATH)
lsr = _load_module("language_smoke_report_drift_under_test", SMOKE_PATH)
# final_audit.py is FROZEN this train (read/import only, A-C4) -- loaded here
# purely so this drift guard can assert bootstrap_names.py's/
# language_smoke_report.py's own #238 fold agrees with the pre-existing
# _fold_source_marks it mirrors, never to modify it.
fa = _load_module("final_audit_drift_under_test", FINAL_AUDIT_PATH)


def test_terminators_identical_across_both_extractors():
    # The set of chars that END a sentence must be the same in both copies, or
    # one extractor breaks a run where the other fuses it (issue #80).
    assert bn.TERMINATORS == lsr.TERMINATORS


def test_token_re_identical_across_both_extractors():
    # The tokenizer pattern must be byte-identical in both copies, or one
    # extractor would carve tokens differently from the other (e.g. absorb a
    # trailing apostrophe the other strips -- issue #82). Pinning the compiled
    # pattern makes the next edit to one tokenizer touch the other.
    assert bn.TOKEN_RE.pattern == lsr.TOKEN_RE.pattern


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


# ---------------------------------------------------------------------------
# #238/#241 (A-C4) -- THREE independent copies of the same Hebrew mark/
# connector match-key fold exist this train: bootstrap_names.py,
# language_smoke_report.py, and the pre-existing final_audit.py
# (_fold_source_marks, final_audit.py:548). No shared import between any of
# them (final_audit.py is frozen; language_smoke_report.py runs as an
# isolated subprocess) -- this is the minimum drift guard asserting all
# three still agree.
# ---------------------------------------------------------------------------

def test_name_connectors_identical_across_both_extractors():
    assert bn.NAME_CONNECTORS == lsr.NAME_CONNECTORS


def test_name_connectors_are_a_strict_subset_of_token_re_connector_class():
    # #241: maqaf/geresh/gershayim ONLY -- never the apostrophes/hyphens
    # TOKEN_RE's own connector class also allows (widening this set breaks
    # Latin non-regression -- tests/caseless_offset.test.py). The literal
    # class body below is copied from TOKEN_RE.pattern itself (see
    # test_token_re_identical_across_both_extractors above for why it is
    # safe to hardcode: any future change there would first break THAT
    # test).
    token_re_connector_class = "'’‑׳״־-"
    assert set(bn.NAME_CONNECTORS) <= set(token_re_connector_class)
    assert not (set(bn.NAME_CONNECTORS) & set("'’‑-"))  # never an apostrophe/hyphen


@pytest.mark.parametrize("s", [
    "משה",
    "משה־לייב",
    "מֹשֶׁה",
    "מֹשֶׁה־לַיִיב",
    "Jean-Baptiste",
    "O'Brien",
    "Ångstrom",
])
def test_match_units_identical_across_both_extractors(s):
    assert bn.match_units(s) == lsr.match_units(s)


@pytest.mark.parametrize("s", [
    "משה",
    "מֹשֶׁה",
    "מֹשֶׁה־לַיִיב",
])
def test_match_units_mark_fold_agrees_with_final_audit_fold_source_marks(s):
    # bootstrap_names.match_units()/language_smoke_report.match_units() both
    # connector-split AFTER folding; final_audit._fold_source_marks() only
    # folds (no connector concept there at all -- it operates on whole
    # whitespace-split draft tokens). Compare the FOLD step alone -- joining
    # match_units() back with no separator reproduces the same folded string
    # for an input with no NAME_CONNECTORS character (all three parametrized
    # cases here are single #241-connector-free words or the ONE compound,
    # verified separately for its own two units).
    has_connector = any(c in s for c in bn.NAME_CONNECTORS)
    if has_connector:
        units = bn.match_units(s)
        # each unit, folded, must equal what final_audit's own fold produces
        # for that SAME substring in isolation.
        for u in units:
            assert u == fa._fold_source_marks(u)
    else:
        assert "".join(bn.match_units(s)) == fa._fold_source_marks(s)


def test_bootstrap_fold_match_marks_agrees_with_final_audit_fold_source_marks():
    for s in ("משה", "מֹשֶׁה", "מֹשֶׁה־לַיִיב", "Sí", "Ångstrom"):
        assert bn._fold_match_marks(s) == fa._fold_source_marks(s)
