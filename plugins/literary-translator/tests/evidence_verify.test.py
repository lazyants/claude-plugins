"""Tests for scripts/evidence_verify.py -- binds every stored
canon_senses.json evidence record (block/seg/char_start/char_end/
context_start/context_end/sha256) to hard, re-derivable facts about the
manifest it claims to come from (Phase 1, RFC #215, plan §1b).

Module under test lives outside any Python package (a standalone script
copied to ``${durable_root}/scripts/`` at runtime, sibling of
``occ_index.py``/``bootstrap_names.py``, whose
``production_occurrences()``/tokenizer it imports for real matcher-
authentication), so it is loaded here via importlib from its real path,
with ``SCRIPTS_DIR`` on ``sys.path`` for its own ``from occ_index import
...`` (which in turn needs ``from bootstrap_names import ...``) to resolve
-- mirrors ``tests/occ_index.test.py``'s own loader exactly.

Every fixture is synthetic and inline (never the absent SSK data), and a
manifest is always the plain dict shape `evidence_verify.py` expects: an
already-parsed ``manifest.json`` document (``{"blocks": {block_id:
{..., "seg":..., "plain_text":...}}}``) -- this module never resolves a
manifest path itself.
"""
import hashlib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Optional

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
EVIDENCE_VERIFY_SCRIPT = SCRIPTS_DIR / "evidence_verify.py"
BOOTSTRAP_NAMES_SCRIPT = SCRIPTS_DIR / "bootstrap_names.py"

assert EVIDENCE_VERIFY_SCRIPT.is_file(), f"evidence_verify.py not found at {EVIDENCE_VERIFY_SCRIPT}"
assert BOOTSTRAP_NAMES_SCRIPT.is_file(), f"bootstrap_names.py not found at {BOOTSTRAP_NAMES_SCRIPT}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/occ_index.test.py's own loader: SCRIPTS_DIR must be on
    sys.path around the in-process load so a standalone script's own
    top-level ``from occ_index import ...`` / ``from bootstrap_names import
    ...`` resolve exactly like they would under a real
    ``python3 evidence_verify.py`` invocation."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


bn = _load_module("bootstrap_names_for_evidence_verify_test", BOOTSTRAP_NAMES_SCRIPT, SCRIPTS_DIR)
ev = _load_module("evidence_verify_under_test", EVIDENCE_VERIFY_SCRIPT, SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_lang(particles=(), stopwords=(), elision_pattern=None, has_elision=None,
              name_inventory=()):
    """Mirrors tests/occ_index.test.py's own make_lang helper exactly (now
    including its `name_inventory` param, needed for the #243 Hebrew
    fold-collision fixtures below), so fixtures stay comparable across both
    suites."""
    elision_re = re.compile(elision_pattern) if elision_pattern else None
    if has_elision is None:
        has_elision = elision_re is not None
    return bn.LanguageConfig(
        path=Path("<test-fixture>"),
        particles=frozenset(p.lower() for p in particles),
        stopwords=frozenset(stopwords),
        elision_re=elision_re,
        has_elision=has_elision,
        raw_bytes=b"{}",
        name_inventory=frozenset(name_inventory),
    )


FR_ELISION_PATTERN = r"^([dl])['’]([A-ZÀÂÄÆÇÉÈÊËÎÏÔŒÖÙÛÜŸ].*)$"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _block(text, seg: Optional[str] = "seg01", block_id="PARA:seg01:0001"):
    return block_id, {
        "id": block_id, "type": "PARA", "seg": seg, "order_index": 0,
        "source_file": "x.txt", "plain_text": text, "sha1": "deadbeef",
    }


def _manifest(*blocks):
    return {"blocks": dict(blocks)}


def _whole_block_evidence(block_id, seg, text, char_start, char_end, **overrides):
    """A correctly-shaped evidence dict whose context is the WHOLE block
    (mirrors occ_index.py's own _context_window convention) with a
    genuinely correct sha256 -- the baseline every negative test mutates
    exactly one field of."""
    evidence = {
        "block": block_id, "seg": seg,
        "char_start": char_start, "char_end": char_end,
        "context_start": 0, "context_end": len(text),
        "sha256": _sha256_hex(text.encode("utf-8")),
    }
    evidence.update(overrides)
    return evidence


def _sense(sense_id, evidence):
    return {
        "sense_id": sense_id, "disambiguator": "test", "index_scope": "narrative",
        "evidence": evidence,
    }


# ---------------------------------------------------------------------------
# Positive cases
# ---------------------------------------------------------------------------

def test_valid_evidence_verifies_clean():
    lang = make_lang()
    text = "Jean marchait. Jean revint."
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    (char_start, char_end), *_ = ev.production_occurrences("Jean", text, lang)
    evidence = _whole_block_evidence(block_id, "seg01", text, char_start, char_end)
    assert ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang) is None


def test_valid_seg_null_block_is_positive():
    lang = make_lang()
    text = "Jean arriva."
    block_id, block = _block(text, seg=None, block_id="FRONTBACK:fm01")
    manifest = _manifest((block_id, block))
    spans = ev.production_occurrences("Jean", text, lang)
    char_start, char_end = spans[0]
    evidence = _whole_block_evidence(block_id, None, text, char_start, char_end)
    assert ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang) is None


def test_duplicate_quote_in_one_block_gets_distinct_spans_both_verify():
    # Two senses of the SAME source_form referencing the repeated surface's
    # two DISTINCT occurrences in one block -- each must verify
    # independently against its own span; no false collision between them.
    lang = make_lang()
    text = "Jean vit Jean."
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    spans = ev.production_occurrences("Jean", text, lang)
    assert len(spans) == 2
    (s1_start, s1_end), (s2_start, s2_end) = spans
    assert (s1_start, s1_end) != (s2_start, s2_end)

    class _Entries:
        entries_by_source_form = {
            "Jean": {"senses": [
                _sense("s1", _whole_block_evidence(block_id, "seg01", text, s1_start, s1_end)),
                _sense("s2", _whole_block_evidence(block_id, "seg01", text, s2_start, s2_end)),
            ]}
        }

    failures = ev.verify_senses(_Entries(), manifest, lang)
    assert failures == []


# ---------------------------------------------------------------------------
# Evidence matrix -- one failure axis per test
# ---------------------------------------------------------------------------

def test_wrong_offset_not_a_production_span_fails():
    # An off-by-one span that is a genuine in-bounds substring of the block
    # but that the matcher never emits as a completed "Jean" run.
    lang = make_lang()
    text = "Jean vit Paul."
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    evidence = _whole_block_evidence(block_id, "seg01", text, 1, 5)  # "ean "
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang)
    assert failure is not None
    assert "matcher-authentication" in failure.reason


def test_offset_shifted_to_a_different_name_in_same_block_fails():
    # (i)-(iii) all pass (block exists, seg matches, context is the whole
    # block with a CORRECT sha256, bounds are fine) -- only matcher-
    # authentication (iv) must catch that [char_start,char_end) is "Paul",
    # not a production span of "Jean".
    lang = make_lang()
    text = "Jean met Paul"
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    paul_start, paul_end = text.index("Paul"), text.index("Paul") + len("Paul")
    evidence = _whole_block_evidence(block_id, "seg01", text, paul_start, paul_end)
    # sanity: (i)-(iii) genuinely pass on this fixture in isolation.
    assert text[evidence["context_start"]:evidence["context_end"]] == text
    assert evidence["sha256"] == _sha256_hex(text.encode("utf-8"))
    assert 0 <= evidence["context_start"] <= paul_start < paul_end <= evidence["context_end"]

    failure = ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang)
    assert failure is not None
    assert "matcher-authentication" in failure.reason
    assert failure.source_form == "Jean"
    assert failure.block == block_id
    assert (failure.char_start, failure.char_end) == (paul_start, paul_end)


def test_duplicate_quote_wrong_block_fails():
    lang = make_lang()
    text = "Jean arriva."
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    evidence = _whole_block_evidence("NONEXISTENT:0001", "seg01", text, 0, 4)
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang)
    assert failure is not None
    assert "not found in manifest" in failure.reason


def test_seg_mismatch_fails():
    lang = make_lang()
    text = "Jean arriva."
    block_id, block = _block(text, seg="seg01")
    manifest = _manifest((block_id, block))
    evidence = _whole_block_evidence(block_id, "seg99", text, 0, 4)  # wrong seg
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang)
    assert failure is not None
    assert "seg" in failure.reason


def test_raw_byte_sha256_mismatch_fails():
    lang = make_lang()
    text = "Jean arriva."
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    evidence = _whole_block_evidence(block_id, "seg01", text, 0, 4, sha256="0" * 64)
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang)
    assert failure is not None
    assert "sha256 mismatch" in failure.reason


def test_out_of_bounds_char_end_fails():
    lang = make_lang()
    text = "Jean"
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    evidence = _whole_block_evidence(block_id, "seg01", text, 0, 40)  # way past len(text)
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang)
    assert failure is not None
    assert "out of bounds" in failure.reason


def test_char_start_not_less_than_char_end_fails():
    lang = make_lang()
    text = "Jean arriva."
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    evidence = _whole_block_evidence(block_id, "seg01", text, 4, 4)  # empty span
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang)
    assert failure is not None
    assert "out of bounds" in failure.reason or "char_start" in failure.reason


def test_context_does_not_enclose_occurrence_fails():
    lang = make_lang()
    text = "Jean marchait vite."
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    spans = ev.production_occurrences("marchait", text, lang) or [(text.index("marchait"), text.index("marchait") + len("marchait"))]
    char_start, char_end = spans[0]
    # context ends BEFORE the occurrence ends -- context does not enclose it.
    evidence = {
        "block": block_id, "seg": "seg01",
        "char_start": char_start, "char_end": char_end,
        "context_start": 0, "context_end": char_end - 1,
        "sha256": _sha256_hex(text[0:char_end - 1].encode("utf-8")),
    }
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang)
    assert failure is not None
    # whichever check trips first (bounds/enclosure) it must fail, not verify.
    assert failure.reason


def test_missing_manifest_none_fails():
    lang = make_lang()
    evidence = {
        "block": "PARA:seg01:0001", "seg": "seg01",
        "char_start": 0, "char_end": 4, "context_start": 0, "context_end": 4,
        "sha256": "a" * 64,
    }
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), None, lang)
    assert failure is not None
    assert "not found in manifest" in failure.reason


def test_malformed_manifest_missing_blocks_key_fails():
    lang = make_lang()
    evidence = {
        "block": "PARA:seg01:0001", "seg": "seg01",
        "char_start": 0, "char_end": 4, "context_start": 0, "context_end": 4,
        "sha256": "a" * 64,
    }
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), {"not_blocks": {}}, lang)
    assert failure is not None
    assert "not found in manifest" in failure.reason


# ---------------------------------------------------------------------------
# Hostile/malformed manifest VALUES must degrade to a per-sense
# EvidenceFailure, never crash (codex-caught blocker: plain_text:null passed
# the old "'plain_text' not in block_record" presence-only check, then
# len(None) raised TypeError inside verify_evidence).
# ---------------------------------------------------------------------------

def test_plain_text_null_returns_failure_not_crash():
    lang = make_lang()
    block_id = "PARA:seg01:0001"
    block = {
        "id": block_id, "type": "PARA", "seg": "seg01", "order_index": 0,
        "source_file": "x.txt", "plain_text": None, "sha1": "deadbeef",
    }
    manifest = _manifest((block_id, block))
    evidence = {
        "block": block_id, "seg": "seg01",
        "char_start": 0, "char_end": 4, "context_start": 0, "context_end": 4,
        "sha256": "a" * 64,
    }
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang)
    assert failure is not None
    assert isinstance(failure, ev.EvidenceFailure)
    assert "not a string" in failure.reason
    assert "NoneType" in failure.reason


def test_plain_text_wrong_type_int_returns_failure_not_crash():
    lang = make_lang()
    block_id = "PARA:seg01:0001"
    block = {
        "id": block_id, "type": "PARA", "seg": "seg01", "order_index": 0,
        "source_file": "x.txt", "plain_text": 42, "sha1": "deadbeef",
    }
    manifest = _manifest((block_id, block))
    evidence = {
        "block": block_id, "seg": "seg01",
        "char_start": 0, "char_end": 4, "context_start": 0, "context_end": 4,
        "sha256": "a" * 64,
    }
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang)
    assert failure is not None
    assert "not a string" in failure.reason
    assert "int" in failure.reason


def test_plain_text_missing_vs_null_are_distinct_reasons():
    # A block record with no "plain_text" key at all (missing) is caught by
    # the block-existence check; a block record WITH "plain_text": null
    # present is caught by the type check -- both must fail, but for
    # DIFFERENT, individually diagnosable reasons.
    lang = make_lang()
    evidence_base = {
        "seg": "seg01", "char_start": 0, "char_end": 4,
        "context_start": 0, "context_end": 4, "sha256": "a" * 64,
    }

    missing_id = "PARA:seg01:0001"
    missing_block = {
        "id": missing_id, "type": "PARA", "seg": "seg01", "order_index": 0,
        "source_file": "x.txt", "sha1": "deadbeef",  # no plain_text key at all
    }
    null_id = "PARA:seg01:0002"
    null_block = {
        "id": null_id, "type": "PARA", "seg": "seg01", "order_index": 1,
        "source_file": "x.txt", "plain_text": None, "sha1": "deadbeef2",
    }
    manifest = _manifest((missing_id, missing_block), (null_id, null_block))

    missing_failure = ev.verify_evidence(
        "Jean", _sense("s1", {**evidence_base, "block": missing_id}), manifest, lang
    )
    null_failure = ev.verify_evidence(
        "Jean", _sense("s2", {**evidence_base, "block": null_id}), manifest, lang
    )

    assert missing_failure is not None and null_failure is not None
    assert "not found in manifest" in missing_failure.reason
    assert "not a string" in null_failure.reason
    assert missing_failure.reason != null_failure.reason


def test_verify_senses_hostile_block_does_not_abort_remaining_senses():
    # A lone (unpaired) surrogate in plain_text is a valid `str` -- it slips
    # past the explicit isinstance(block_text, str) check -- but
    # .encode("utf-8") on a slice containing it raises UnicodeEncodeError.
    # This is exactly the "anything else unanticipated" case the
    # verify_senses-level try/except (not the field-level type check) exists
    # to contain: one hostile sense must become its OWN EvidenceFailure
    # without dropping verification of the other senses.
    #
    # A trailing THIRD sense (ordinary wrong-block failure, no exception
    # involved) is deliberately placed AFTER the hostile one: "healthy"
    # alone can't prove non-abortion (codex round 2) since it produces zero
    # failures either way, so an implementation that returns immediately
    # after appending the hostile except-path failure would coincidentally
    # produce the SAME single-failure list this test used to assert. Only
    # by requiring "trailing_bad"'s ordinary failure to ALSO be present does
    # the assertion actually distinguish "continues past the exception"
    # from "aborts right after it".
    lang = make_lang()
    hostile_text = "Jean\ud800"
    hostile_id = "PARA:seg01:0001"
    hostile_block = {
        "id": hostile_id, "type": "PARA", "seg": "seg01", "order_index": 0,
        "source_file": "x.txt", "plain_text": hostile_text, "sha1": "a",
    }
    healthy_text = "Jean revint."
    healthy_id = "PARA:seg01:0002"
    healthy_block = {
        "id": healthy_id, "type": "PARA", "seg": "seg01", "order_index": 1,
        "source_file": "x.txt", "plain_text": healthy_text, "sha1": "b",
    }
    manifest = _manifest((hostile_id, hostile_block), (healthy_id, healthy_block))

    hostile_evidence = {
        "block": hostile_id, "seg": "seg01",
        "char_start": 0, "char_end": 4, "context_start": 0, "context_end": len(hostile_text),
        "sha256": "0" * 64,  # never reached -- the encode() call raises first
    }
    healthy_span = ev.production_occurrences("Jean", healthy_text, lang)[0]
    healthy_evidence = _whole_block_evidence(healthy_id, "seg01", healthy_text, *healthy_span)
    trailing_bad_evidence = {
        "block": "NONEXISTENT:0001", "seg": "seg01",
        "char_start": 0, "char_end": 4, "context_start": 0, "context_end": 4,
        "sha256": "a" * 64,
    }

    class _Entries:
        entries_by_source_form = {
            "Jean": {"senses": [
                _sense("hostile", hostile_evidence),
                _sense("healthy", healthy_evidence),
                _sense("trailing_bad", trailing_bad_evidence),
            ]}
        }

    failures = ev.verify_senses(_Entries(), manifest, lang)  # must not raise
    assert len(failures) == 2
    failure_by_sense = {f.sense_id: f for f in failures}
    assert set(failure_by_sense) == {"hostile", "trailing_bad"}
    assert "UnicodeEncodeError" in failure_by_sense["hostile"].reason
    assert "not found in manifest" in failure_by_sense["trailing_bad"].reason


def test_verify_evidence_direct_hostile_surrogate_returns_failure_not_crash():
    # Covers the DIRECT verify_evidence() call path -- verify_senses()'s own
    # broad except (test above) never runs here, so this exercises whether
    # verify_evidence() itself degrades a lone-surrogate plain_text to an
    # EvidenceFailure. The module docstring documents direct verify_evidence()
    # calls as a supported use ("exposed directly for callers (and tests)
    # that already have one sense in hand"), so this path must be just as
    # crash-proof as the verify_senses() batch entry point.
    lang = make_lang()
    hostile_text = "Jean\ud800"
    block_id = "PARA:seg01:0001"
    block = {
        "id": block_id, "type": "PARA", "seg": "seg01", "order_index": 0,
        "source_file": "x.txt", "plain_text": hostile_text, "sha1": "a",
    }
    manifest = _manifest((block_id, block))
    evidence = {
        "block": block_id, "seg": "seg01",
        "char_start": 0, "char_end": 4, "context_start": 0, "context_end": len(hostile_text),
        "sha256": "0" * 64,  # never reached -- the encode() call raises first, pre-fix
    }
    failure = ev.verify_evidence("Jean", _sense("s1", evidence), manifest, lang)
    assert failure is not None
    assert isinstance(failure, ev.EvidenceFailure)
    assert "UnicodeEncodeError" in failure.reason


# ---------------------------------------------------------------------------
# Elision/no-elision matcher parity (R5 F1) -- paired, identical bytes.
# Proves verify_evidence is genuinely config-parameterized through
# production_occurrences, never a bare in-bounds-substring check.
# ---------------------------------------------------------------------------

def test_elision_no_elision_matcher_parity_identical_bytes():
    text = "d'Effiat arriva."
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    # The SAME evidence bytes -- span [2,8) == "Effiat", correct whole-block
    # context + sha256 either way.
    evidence = _whole_block_evidence(block_id, "seg01", text, 2, 8)
    assert text[2:8] == "Effiat"

    with_elision = make_lang(elision_pattern=FR_ELISION_PATTERN)
    without_elision = make_lang(elision_pattern=None, has_elision=False)

    accepted = ev.verify_evidence("Effiat", _sense("s1", evidence), manifest, with_elision)
    assert accepted is None, "under elision, [2,8) IS a production span of 'Effiat' -- must ACCEPT"

    rejected = ev.verify_evidence("Effiat", _sense("s1", evidence), manifest, without_elision)
    assert rejected is not None, "without elision, [2,8) is NOT a production span -- must REJECT"
    assert "matcher-authentication" in rejected.reason


def test_verify_senses_aggregates_across_entries_and_senses():
    lang = make_lang(elision_pattern=FR_ELISION_PATTERN)
    text_jean = "Jean marchait. Jean revint."
    text_effiat = "d'Effiat arriva."
    jean_id, jean_block = _block(text_jean, block_id="PARA:seg01:0001")
    effiat_id, effiat_block = _block(text_effiat, block_id="PARA:seg01:0002")
    manifest = _manifest((jean_id, jean_block), (effiat_id, effiat_block))

    jean_spans = ev.production_occurrences("Jean", text_jean, lang)

    class _Entries:
        entries_by_source_form = {
            "Jean": {"senses": [
                _sense("j1", _whole_block_evidence(jean_id, "seg01", text_jean, *jean_spans[0])),
                _sense("j2", _whole_block_evidence(jean_id, "seg01", text_jean, *jean_spans[1])),
            ]},
            "Effiat": {"senses": [
                _sense("e1", _whole_block_evidence(effiat_id, "seg01", text_effiat, 2, 8)),
                # e2 is deliberately broken: offset shifted past the real span.
                _sense("e2", _whole_block_evidence(effiat_id, "seg01", text_effiat, 0, 1)),
            ]},
        }

    failures = ev.verify_senses(_Entries(), manifest, lang)
    assert len(failures) == 1
    assert failures[0].sense_id == "e2"
    assert failures[0].source_form == "Effiat"


# ---------------------------------------------------------------------------
# #243 -- fold-key collisions, fail-closed. Real Baal Shem Tov pointed/
# unpointed maqaf/space variants (whoswho.final.json:87-98), mirroring
# tests/occ_index.test.py's own Hebrew collision fixture so both sites'
# behavior stays directly comparable.
# ---------------------------------------------------------------------------

BST_MAQAF_UNPOINTED = "הבעל־שם־טוב"
BST_SPACE_UNPOINTED = "הבעל שם טוב"
BST_MAQAF_POINTED_OCCURRENCE = "הַבַּעַל־שֵׁם־טוֹב"


def test_243_verify_senses_fold_collision_fail_closed_both_senses_fail():
    """Two DISTINCT canon source_forms (maqaf-joined and space-joined,
    both unpointed) fold to the SAME #238/#241 match key. Each has ONE
    sense whose stored evidence points at the SAME single physical
    occurrence (pointed+maqaf in the source text). Matcher-authentication
    must fail for BOTH -- fail-closed -- never accept one arbitrarily
    (which of the two "won" would depend on dict/iteration order, exactly
    the silent-overwrite hazard this guard exists to prevent)."""
    lang = make_lang(name_inventory=[BST_MAQAF_UNPOINTED, BST_SPACE_UNPOINTED])
    text = f"ראה {BST_MAQAF_POINTED_OCCURRENCE} אתמול."
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    char_start = text.index(BST_MAQAF_POINTED_OCCURRENCE)
    char_end = char_start + len(BST_MAQAF_POINTED_OCCURRENCE)

    class _Entries:
        entries_by_source_form = {
            BST_MAQAF_UNPOINTED: {"senses": [
                _sense("maqaf1", _whole_block_evidence(block_id, "seg01", text, char_start, char_end)),
            ]},
            BST_SPACE_UNPOINTED: {"senses": [
                _sense("space1", _whole_block_evidence(block_id, "seg01", text, char_start, char_end)),
            ]},
        }

    failures = ev.verify_senses(_Entries(), manifest, lang)
    assert len(failures) == 2
    failure_by_sense = {f.sense_id: f for f in failures}
    assert set(failure_by_sense) == {"maqaf1", "space1"}
    for failure in failures:
        assert "matcher-authentication" in failure.reason


def test_243_verify_senses_no_collision_still_verifies_clean():
    # Sanity/non-regression: a SINGLE Hebrew source_form with no colliding
    # sibling in the competitor universe must still verify normally under
    # the new fold-aware grouping.
    lang = make_lang(name_inventory=[BST_MAQAF_UNPOINTED])
    text = f"ראה {BST_MAQAF_POINTED_OCCURRENCE} אתמול."
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    char_start = text.index(BST_MAQAF_POINTED_OCCURRENCE)
    char_end = char_start + len(BST_MAQAF_POINTED_OCCURRENCE)

    class _Entries:
        entries_by_source_form = {
            BST_MAQAF_UNPOINTED: {"senses": [
                _sense("s1", _whole_block_evidence(block_id, "seg01", text, char_start, char_end)),
            ]},
        }

    assert ev.verify_senses(_Entries(), manifest, lang) == []


def test_243_verify_senses_canon_param_widens_the_competitor_universe():
    """`canon` is optional (defaults to None, the canon-absent audit branch)
    but when given, its `entries` join the competitor universe. Here the
    ONLY sense being verified is BST_MAQAF_UNPOINTED -- alone, senses_result
    has no local collision -- but `canon` carries BST_SPACE_UNPOINTED as a
    sibling entry (e.g. a plain non-split canon entry that never needed its
    own canon_senses.json record). Passing `canon=` must still catch the
    collision; omitting it must not (proving `canon` is genuinely load-
    bearing here, not a no-op)."""
    lang = make_lang(name_inventory=[BST_MAQAF_UNPOINTED, BST_SPACE_UNPOINTED])
    text = f"ראה {BST_MAQAF_POINTED_OCCURRENCE} אתמול."
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))
    char_start = text.index(BST_MAQAF_POINTED_OCCURRENCE)
    char_end = char_start + len(BST_MAQAF_POINTED_OCCURRENCE)

    class _Entries:
        entries_by_source_form = {
            BST_MAQAF_UNPOINTED: {"senses": [
                _sense("s1", _whole_block_evidence(block_id, "seg01", text, char_start, char_end)),
            ]},
        }

    without_canon = ev.verify_senses(_Entries(), manifest, lang)
    assert without_canon == [], "no local collision within senses_result alone -- must verify clean"

    canon = {"entries": {BST_SPACE_UNPOINTED: {"is_proper_name": True}}}
    with_canon = ev.verify_senses(_Entries(), manifest, lang, canon=canon)
    assert len(with_canon) == 1
    assert with_canon[0].source_form == BST_MAQAF_UNPOINTED
    assert "matcher-authentication" in with_canon[0].reason


# ---------------------------------------------------------------------------
# #243 parity -- _group_production_spans_by_name()'s fold-aware grouping
# must agree with production_occurrences()'s own fold-aware comparison
# (occ_index.py Site 1) for every NON-colliding source_form: this is the
# exact invariant _group_production_spans_by_name()'s own docstring promises
# ("mirrors index_manifest()'s ... so the two never drift"), checked here
# against Site 1 directly rather than re-deriving it.
# ---------------------------------------------------------------------------

def test_243_group_production_spans_by_name_parity_with_production_occurrences():
    # evidence_verify.py itself already imports fold_collision_map from
    # canon_senses at module level -- reuse that binding (ev.fold_collision_map)
    # rather than a bare top-level `import canon_senses`, which a static
    # checker cannot resolve from this test file's own location (mirrors
    # tests/occ_index.test.py's own fix for the identical pattern).
    cases = [
        (make_lang(), "Jean marchait. Jean revint.", "Jean"),
        (make_lang(particles=["de"]), "Il visita le Chateau de Versailles hier.",
         "Chateau de Versailles"),
        (make_lang(name_inventory=[BST_MAQAF_UNPOINTED]),
         f"ראה {BST_MAQAF_POINTED_OCCURRENCE} אתמול.", BST_MAQAF_UNPOINTED),
    ]
    for lang, text, source_form in cases:
        competitors = ev.fold_collision_map([source_form])
        grouped = ev._group_production_spans_by_name(text, lang, competitors)
        assert list(grouped.get(source_form, [])) == ev.production_occurrences(
            source_form, text, lang
        ), f"parity broke for source_form={source_form!r}"


# ---------------------------------------------------------------------------
# Performance -- per-block production-span caching (codex round-4 MAJOR
# finding). verify_senses() previously called verify_evidence() -> occ_index.
# production_occurrences() -> the full extract_candidate_spans() tokenizer
# pass once per SENSE; a split entry with N senses on one block re-extracted
# that same block N times. verify_senses() must now extract each distinct
# block exactly once, however many senses reference it.
# ---------------------------------------------------------------------------

def test_verify_senses_extracts_each_block_once_regardless_of_sense_count():
    # Patches BOTH occ_index.py's own module-global _run_spans (what its
    # production_occurrences() calls internally -- the pre-fix path) and
    # this module's separately-bound `ev._run_spans` (what
    # _group_production_spans_by_name() calls -- the post-fix caching path)
    # onto ONE shared counter, since a `from occ_index import ...` binds an
    # independent name in ev's own namespace: patching only one of the two
    # would silently miss whichever path the code under test actually took.
    oi_module = sys.modules.get("occ_index")
    assert oi_module is not None, "occ_index must already be imported (evidence_verify.py imports it)"

    lang = make_lang()
    n_senses = 20
    text = "Jean. " * n_senses
    block_id, block = _block(text)
    manifest = _manifest((block_id, block))

    real_run_spans = oi_module._run_spans
    call_count = {"n": 0}

    def counting_run_spans(block_text, language_config):
        call_count["n"] += 1
        return real_run_spans(block_text, language_config)

    # Sanity check FIRST, via the real (unpatched) function, so it never
    # pollutes the counter the assertion below relies on.
    jean_spans = [
        (char_start, char_end)
        for name, _mid_sentence, char_start, char_end in real_run_spans(text, lang)
        if name == "Jean"
    ]
    assert len(jean_spans) == n_senses, "fixture must yield one distinct span per sense, no merging"

    class _Entries:
        entries_by_source_form = {
            "Jean": {"senses": [
                _sense(f"s{i}", _whole_block_evidence(block_id, "seg01", text, cs, ce))
                for i, (cs, ce) in enumerate(jean_spans)
            ]}
        }

    # setattr(), not `oi_module._run_spans = ...` -- both `oi_module` (a bare
    # sys.modules lookup) and `ev` (loaded via importlib, below) are typed as
    # ModuleType by a static checker, which does not know `_run_spans` is a
    # real attribute of either at runtime; setattr() patches/restores the
    # exact same live attribute without a static existence check.
    setattr(oi_module, "_run_spans", counting_run_spans)
    setattr(ev, "_run_spans", counting_run_spans)
    try:
        failures = ev.verify_senses(_Entries(), manifest, lang)
    finally:
        setattr(oi_module, "_run_spans", real_run_spans)
        setattr(ev, "_run_spans", real_run_spans)

    assert failures == []
    assert call_count["n"] == 1, (
        f"expected exactly 1 extraction pass for {n_senses} senses on ONE block, "
        f"got {call_count['n']} (block is being re-extracted per sense)"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
