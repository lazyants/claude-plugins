"""A machine-truncated `source_form` may never be merged as `accepted` (#383).

`glossary_TASK.md` tells the adjudicator to queue a marker-bearing candidate,
and `glossary_preflight.py` step 6c refuses to dispatch a durable prompt that
lacks that instruction -- so the model is guaranteed to be TOLD. This file
covers the other half: what happens when it is told and does it anyway.

`canon_validate._enforce_no_truncated_accepted()` runs in Pass 1's company on
BOTH batch paths -- `run_check_batch` (the per-fragment precheck, which is also
what `--approve-to` snapshots after) and `run_merge_batches` (the final write).
Covering only the first would leave the merge path open; covering only the
second would let a doomed fragment be approved and audited before anything
refused it.

Three things are pinned here, and the third is the one that rots silently:

1. REFUSAL. A marker-bearing `accepted` item is refused on both paths.
2. THE PERMITTED FORM. The same `source_form` as `review_queue` still passes.
   That asymmetry is the whole remedy -- refusing both would leave the
   candidate nowhere to go, and `glossary_batch_plan.py` excludes a queued
   `source_form` from every later batch, so queueing genuinely ends it.
3. DRIFT. `canon_validate.CAPPED_NAME_MARKER_RE` is a deliberate restatement of
   `bootstrap_names`' own marker shape (that file documents why: this gate runs
   on the offline path and must not grow an import edge for a check that reads
   three constants). A restatement nothing compares is a restatement that
   drifts, so the pin below asserts the EXPECTED shape on the producer side
   first and only then their agreement -- two copies agreeing is one
   observation, and both can be wrong the same way.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"


def _load_module(name: str, filename: str):
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
        assert spec is not None and spec.loader is not None, filename
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS_DIR))


cv = _load_module("canon_validate_for_truncated_source_form_test", "canon_validate.py")
bn = _load_module("bootstrap_names_for_truncated_source_form_test", "bootstrap_names.py")


def _capped_form() -> str:
    """A `source_form` the PRODUCER actually produced -- never hand-assembled,
    so these tests exercise the real emitted string."""
    over_cap = "A" * (bn._MAX_CANDIDATE_NAME_CHARS + 50)
    capped = bn._capped_candidate_name(over_cap)
    assert capped != over_cap, "exemplar did not trip the cap -- test would be vacuous"
    return capped


def _accepted_item(source_form: str) -> dict:
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "disposition": "accepted",
        "canonical_target_form": "Target",
        "basis": "transliterated",
        "confidence": "high",
    }


def _queued_item(source_form: str) -> dict:
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "disposition": "review_queue",
        "note": "machine-truncated candidate name; see the source run",
    }


# --- 1. refusal ------------------------------------------------------------


def test_marker_bearing_accepted_item_is_refused():
    with pytest.raises(cv.CanonValidationError) as excinfo:
        cv._enforce_no_truncated_accepted([_accepted_item(_capped_form())])
    message = str(excinfo.value)
    assert "inert" in message, message
    assert "review_queue" in message, message


def test_the_refusal_names_the_offending_index():
    batch = [
        _queued_item("Ordinary"),
        _accepted_item(_capped_form()),
    ]
    with pytest.raises(cv.CanonValidationError) as excinfo:
        cv._enforce_no_truncated_accepted(batch)
    # Index 1, not 0 -- a refusal that always blamed the first item would be
    # indistinguishable from a correct one on a single-item batch.
    assert "1" in str(excinfo.value), str(excinfo.value)


# --- 2. the permitted form -------------------------------------------------


def test_the_same_form_queued_is_permitted():
    """The asymmetry that makes the rule actionable. If this ever starts
    raising, the remedy has become a dead end: the adjudicator would be told
    to queue a candidate that the gate then refuses to record."""
    cv._enforce_no_truncated_accepted([_queued_item(_capped_form())])


def test_an_ordinary_accepted_item_is_untouched():
    cv._enforce_no_truncated_accepted([_accepted_item("Marie Claire")])


def test_a_form_merely_mentioning_the_marker_text_mid_string_is_untouched():
    """The marker is matched ANCHORED at the end, exactly as the producer
    appends it. A name that merely contains marker-shaped text is not a
    truncated name, and refusing it would be a false halt on data the
    producer never emitted."""
    marker = _capped_form()[bn._MAX_CANDIDATE_NAME_CHARS:]
    cv._enforce_no_truncated_accepted([_accepted_item(f"Prefix{marker} and more")])


def test_a_non_dict_or_non_string_item_is_left_to_pass_one():
    """Shape is Pass 1's job. Masking a structural error with this gate's
    message would send the operator after the wrong defect."""
    cv._enforce_no_truncated_accepted(["not a dict", {"source_form": 17,
                                                      "disposition": "accepted"}])


# --- 3. drift against the producer -----------------------------------------


def test_the_restated_marker_regex_matches_the_producers_own_shape():
    """Assert the EXPECTED shape on the producer side FIRST, then agreement.
    Comparing the two copies to each other alone proves only that they are
    consistent -- which holds just as well when both are wrong."""
    marker = _capped_form()[bn._MAX_CANDIDATE_NAME_CHARS:]

    # Producer side, stated independently of either regex.
    assert marker.startswith(" [...truncated:"), marker
    assert marker.endswith("]"), marker
    digest = marker[len(" [...truncated:"):-1]
    assert re.fullmatch(r"[0-9a-f]{16}", digest), digest

    # Agreement: the restatement must accept exactly what the producer emits.
    assert cv.CAPPED_NAME_MARKER_RE.search(_capped_form()) is not None
    assert cv.CAPPED_NAME_MARKER_RE.search("Marie Claire") is None
    # And it must be anchored, like the producer's own _CAPPED_NAME_MARKER_RE.
    assert cv.CAPPED_NAME_MARKER_RE.search(f"{marker} trailing") is None


def test_the_restatement_tracks_a_producer_digest_width_change():
    """The restatement hard-codes 16. If the producer's digest width moves,
    this test is the thing that says so -- otherwise the gate would silently
    stop matching real truncated names and fail OPEN, which is the direction
    that ships the defect."""
    assert bn._CAPPED_NAME_DIGEST_CHARS == 16, (
        "bootstrap_names moved its digest width; update "
        "canon_validate.CAPPED_NAME_MARKER_RE (and glossary_TASK.template.md's "
        "two marker descriptions) in the same commit"
    )


# --- both call paths -------------------------------------------------------


def _write(tmp_path: Path, name: str, doc) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_check_batch_refuses_before_approving_a_snapshot(tmp_path):
    """The precheck path. It matters that this fires BEFORE --approve-to
    writes: an approved snapshot is what a reviewer audits, so a doomed
    fragment must never leave one behind."""
    batch_path = _write(tmp_path, "out_0.json", [_accepted_item(_capped_form())])
    approve_to = tmp_path / "approved_0.json"
    registry = cv._build_schema_registry()

    with pytest.raises(cv.CanonValidationError):
        cv.run_check_batch(
            tmp_path / "canon.json",
            str(batch_path),
            "offline",
            None,
            registry,
            tmp_path / "canon_senses.json",
            True,
            approve_to=str(approve_to),
        )
    assert not approve_to.exists(), (
        "a refused fragment left an approved snapshot behind -- a reviewer "
        "could audit bytes that will never merge"
    )


def test_merge_batches_refuses_the_same_fragment(tmp_path):
    """The write path, pinned separately. Covering only the precheck would
    leave this one open, and it is the one that reaches canon.json."""
    batch_path = _write(tmp_path, "out_0.json", [_accepted_item(_capped_form())])
    canon_path = tmp_path / "canon.json"
    registry = cv._build_schema_registry()

    with pytest.raises(cv.CanonValidationError) as excinfo:
        cv.run_merge_batches(
            canon_path,
            [str(batch_path)],
            "offline",
            registry,
            tmp_path / "canon_senses.json",
            True,
        )
    # Pin the REASON, not merely the exception type. run_merge_batches raises
    # CanonValidationError for unrelated causes too (an unscaffolded root has
    # no ownership marker for cache_key.py), so a bare `raises` here passed
    # even with the gate removed -- measured, not imagined.
    assert "inert canon entry" in str(excinfo.value), str(excinfo.value)
    assert not canon_path.exists(), "a refused batch must write no canon.json"
