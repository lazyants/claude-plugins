"""tests/final_audit_adjudication_note.test.py -- regression lock for F5:
the GLOSSARY-DIFF self-inconsistency WARN's adjudication-reconcile note.

``final_audit.py``'s ``warn_glossary_diff()`` flags a ``canon.json`` where
the SAME ``source_form`` resolves to more than one ``canonical_target_form``
across its own ``entries{}``. A genuine self-inconsistency can also be an
INTENTIONAL split a human already ratified in ``canon_adjudications.json``
(see ``canon_adjudication_audit.py``) -- e.g. one source name that
legitimately renders two different ways in two different contexts. F5 adds a
one-line reconcile note to that specific WARN so an operator checks
``canon_adjudications.json`` before treating it as a defect, WITHOUT
changing the message's existing ``GLOSSARY-DIFF ...`` prefix, which
``tests/final_audit.test.py::test_warn_glossary_diff_canon_self_inconsistency``
already asserts as a substring.

This file exercises ``warn_glossary_diff()`` directly (not the full
``final_audit.py`` subprocess integration final_audit.test.py already
covers) -- the self-inconsistency branch only ever reads ``CANON_PATH``, so
no converged-segment/draft fixtures are needed; passing an empty
``converged`` set is sufficient and keeps this test scoped to exactly the
one code path F5 touches.
"""
import importlib.util
import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts" / "final_audit.py"
)

assert SCRIPT_PATH.is_file(), f"final_audit.py not found at {SCRIPT_PATH}"


def _load_final_audit():
    """Loads the real, shipped final_audit.py directly from its install
    path via importlib -- mirroring profile_validate.test.py's own
    convention for scripts that are exercised at the function level rather
    than via the full-subprocess fixture harness. Loading from the real
    path (not a tmp_path copy) means final_audit.py's own
    ``sys.path.insert(0, str(SCRIPTS_DIR))`` + ``import validate_draft``
    resolves against the real, adjacent scripts -- exactly as installed."""
    spec = importlib.util.spec_from_file_location(
        "final_audit_under_test", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None, f"could not load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_self_inconsistent_canon(path: Path):
    """A canon.json where 'Jean' resolves to two different
    canonical_target_form values across its own entries{} -- the genuine
    self-inconsistency warn_glossary_diff's first check flags."""
    path.write_text(
        json.dumps(
            {
                "entries": {
                    "Jean_A": {
                        "source_form": "Jean",
                        "canonical_target_form": "John",
                        "is_proper_name": True,
                        "basis": "transliterated",
                        "confidence": "high",
                    },
                    "Jean_B": {
                        "source_form": "Jean",
                        "canonical_target_form": "Zhan",
                        "is_proper_name": True,
                        "basis": "transliterated",
                        "confidence": "high",
                    },
                }
            }
        ),
        encoding="utf-8",
    )


def test_self_inconsistency_warn_includes_adjudication_reconcile_note(tmp_path):
    fa = _load_final_audit()
    canon_path = tmp_path / "canon.json"
    _write_self_inconsistent_canon(canon_path)
    fa.CANON_PATH = canon_path

    warns = fa.warn_glossary_diff(set())

    matching = [w for w in warns if "self-inconsistent" in w]
    assert len(matching) == 1, warns
    warn = matching[0]

    # The reconcile note is appended, never replaces, the existing prefix --
    # final_audit.test.py's own regression asserts this exact substring.
    assert warn.startswith(
        "GLOSSARY-DIFF canon.json self-inconsistent: source_form 'Jean' -> "
        "['John', 'Zhan']"
    ), warn
    assert "canon_adjudications.json" in warn, warn
    assert "reconcile" in warn.lower(), warn


def test_consistent_canon_produces_no_self_inconsistency_warn(tmp_path):
    """Companion negative case: a canon.json with no source_form collision
    must not spuriously grow the reconcile note out of thin air."""
    fa = _load_final_audit()
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(
        json.dumps(
            {
                "entries": {
                    "Jean": {
                        "source_form": "Jean",
                        "canonical_target_form": "John",
                        "is_proper_name": True,
                        "basis": "transliterated",
                        "confidence": "high",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    fa.CANON_PATH = canon_path

    warns = fa.warn_glossary_diff(set())

    assert not any("self-inconsistent" in w for w in warns), warns


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
