"""tests/canon_senses.test.py -- tests for scripts/canon_senses.py, the
homonym-split sidecar's shared `normalize_form` leaf + the single
runtime-validating `load_senses` loader every consumer (canon_validate.py's
recollapse guard, canon_adjudication_audit.py's split category,
glossary_batch_plan.py's split-form exclusion) imports from here.

Every assertion below drives `load_senses` end to end (never a bare
`jsonschema.Draft202012Validator` in isolation) against synthetic inline
fixtures written to `tmp_path` -- never the absent SSK data. This locks
down:

  - the strict `canon-senses.schema.json` shape: `senses` `minItems:2` (a
    1-sense record is a hard load error, not "not split"); every object is
    `additionalProperties:false` so a malformed/short evidence record or a
    stored `quote`/`context_window` (derived-at-read-time fields that must
    never be persisted) is rejected;
  - the three procedural rejects JSON Schema itself cannot express: a
    duplicate `sense_id` within one entry (`uniqueItems` sees two distinct
    objects as distinct and would let this through), a non-NFC
    `entries_by_source_form` key, and two keys colliding under the shared
    `normalize_form` comparator (a casefold collision and a whitespace
    collision, both individually well-formed NFC strings);
  - the path-state policy: `allow_absent=True` tolerates ONLY a genuinely
    absent path; an explicit missing path, a directory, and a dangling
    symlink all BLOCK regardless of `allow_absent`; a raw `{}` (missing
    required top-level fields) is a BLOCKING schema error, never silently
    "empty" -- only a schema-VALID document with `entries_by_source_form
    == {}` is the distinguished empty state;
  - `normalize_form` itself (NFC + casefold + whitespace-collapse) and the
    `is_split` convenience predicate, both compared via the normalized
    form rather than a raw key lookup;
  - the read/parse layer below the schema: a non-UTF-8 sidecar
    (`UnicodeDecodeError`, a `ValueError` subclass, not `OSError`) is
    translated into `CanonSensesLoadError`, never left to escape as a raw
    traceback past every consumer's `CanonSensesLoadError`-only catch; a
    pathologically deep-nested document is rejected DETERMINISTICALLY by
    `_measure_nesting_depth`'s iterative (non-recursive) preflight against
    `MAX_NESTING_DEPTH`, not merely by a `RecursionError` backstop whose
    actual trigger point (inside `json.loads` vs. inside jsonschema's
    error-formatting `repr()`) shifts with the interpreter's incidental
    stack depth -- a boundary test pins the preflight's `>` (not `>=`)
    comparison at exactly `MAX_NESTING_DEPTH`, and a separate EXTREME
    fixture (100000 levels) asserts only that SOME layer raises
    `CanonSensesLoadError`, since which layer catches it is itself
    interpreter-dependent; and a JSON-escaped LONE SURROGATE, in either an
    `entries_by_source_form` key or a value, is rejected by
    `_reject_unencodable_strings`'s iterative UTF-8-encodability walk
    (`json.loads` accepts it and hands back a `str` that would otherwise
    crash the first consumer to `.encode("utf-8")` it).

Module under test lives outside any Python package (a standalone script
copied to ``${durable_root}/scripts/`` at runtime), so it is loaded here via
``importlib`` from its real path -- same convention as
``bootstrap_names.test.py`` -- and the real, shipped
``canon-senses.schema.json`` is used as `load_senses`'s default schema
(never a copy), so these tests also double as a live check that the
shipped schema and the loader agree.
"""
import importlib.util
import json
import sys
import unicodedata
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "scripts"
    / "canon_senses.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("canon_senses_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cs = _load_module()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _valid_evidence(**overrides):
    evidence = {
        "block": "PARA:seg01:0001",
        "seg": "seg01",
        "char_start": 10,
        "char_end": 16,
        "context_start": 0,
        "context_end": 40,
        "sha256": "a" * 64,
    }
    evidence.update(overrides)
    return evidence


def _valid_sense(sense_id, disambiguator="the king", index_scope="narrative", evidence=None):
    return {
        "sense_id": sense_id,
        "disambiguator": disambiguator,
        "index_scope": index_scope,
        "evidence": evidence if evidence is not None else _valid_evidence(),
    }


def _valid_doc(source_form="Jean", senses=None):
    return {
        "schema_version": 1,
        "entries_by_source_form": {
            source_form: {
                "senses": senses if senses is not None else [_valid_sense("s1"), _valid_sense("s2")]
            }
        },
    }


def _write(tmp_path, doc, name="canon_senses.json"):
    path = tmp_path / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# normalize_form
# ---------------------------------------------------------------------------

def test_normalize_form_nfc_casefold_whitespace():
    assert cs.normalize_form("  Jean  ") == "jean"
    # "É" ("É" = capital E acute) casefolds to the same string as
    # "é" (lowercase e acute) -- both individually already NFC.
    assert cs.normalize_form("É") == cs.normalize_form("é")
    # A decomposed (NFD) accented char normalizes to the same form as its
    # precomposed (NFC) equivalent.
    nfd = unicodedata.normalize("NFD", "é")
    assert nfd != "é"
    assert cs.normalize_form(nfd) == cs.normalize_form("é")


# ---------------------------------------------------------------------------
# load_senses -- happy path / emptiness
# ---------------------------------------------------------------------------

def test_valid_two_sense_doc_loads_populated(tmp_path):
    path = _write(tmp_path, _valid_doc())
    result = cs.load_senses(path, allow_absent=False)
    assert result.is_empty is False
    assert list(result.entries_by_source_form.keys()) == ["Jean"]
    assert len(result.entries_by_source_form["Jean"]["senses"]) == 2


def test_absent_path_with_allow_absent_is_empty(tmp_path):
    path = tmp_path / "does_not_exist.json"
    result = cs.load_senses(path, allow_absent=True)
    assert result.is_empty is True
    assert result.entries_by_source_form == {}


def test_schema_valid_empty_entries_doc_is_empty(tmp_path):
    path = _write(tmp_path, {"schema_version": 1, "entries_by_source_form": {}})
    result = cs.load_senses(path, allow_absent=False)
    assert result.is_empty is True
    assert result.entries_by_source_form == {}


def test_raw_empty_object_is_blocking_not_empty(tmp_path):
    path = _write(tmp_path, {})
    # allow_absent=True proves this is NOT rescued as "empty" -- the file
    # IS present, just schema-invalid (missing required top-level fields),
    # which must BLOCK regardless of allow_absent.
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(path, allow_absent=True)


# ---------------------------------------------------------------------------
# load_senses -- path-state policy
# ---------------------------------------------------------------------------

def test_explicit_missing_path_blocks(tmp_path):
    path = tmp_path / "missing_explicit.json"
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(path, allow_absent=False)


def test_directory_path_blocks_regardless_of_allow_absent(tmp_path):
    dir_path = tmp_path / "a_directory"
    dir_path.mkdir()
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(dir_path, allow_absent=True)


def test_dangling_symlink_blocks_regardless_of_allow_absent(tmp_path):
    target = tmp_path / "nonexistent_target.json"
    link = tmp_path / "dangling_link.json"
    link.symlink_to(target)
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(link, allow_absent=True)


# ---------------------------------------------------------------------------
# load_senses -- read/parse failures below the schema layer
# ---------------------------------------------------------------------------

def test_invalid_utf8_sidecar_raises_load_error(tmp_path):
    """A non-UTF-8 sidecar makes `read_text` raise UnicodeDecodeError -- a
    ValueError subclass, not OSError -- which must be translated into the
    documented CanonSensesLoadError rather than escaping as a raw
    traceback (every consumer's `_load_senses_or_raise`-style wrapper
    catches only CanonSensesLoadError)."""
    path = tmp_path / "canon_senses.json"
    path.write_bytes(b"\xff\xfe\x00invalid")
    with pytest.raises(cs.CanonSensesLoadError) as exc_info:
        cs.load_senses(path, allow_absent=False)
    assert str(path) in str(exc_info.value)


def test_deeply_nested_json_raises_load_error(tmp_path):
    """A document nested `MAX_NESTING_DEPTH + 1` levels deep must be
    rejected via the DETERMINISTIC depth preflight (`_measure_nesting_depth`
    in `_read_json_file`), asserted by checking the message names the
    measured depth. This fixture is deliberately just ONE level past the
    ceiling (not the earlier extreme 100000-level fixture, see
    `test_extreme_nesting_is_rejected_by_some_layer` below): depth 101
    parses via `json.loads` on every supported interpreter (3.11-3.14
    all tested), so the preflight is guaranteed to be the layer that
    fires and the message assertion is safe everywhere. The earlier
    100000-level fixture is NOT reused here for a message assertion --
    empirically, whether `json.loads` itself raises `RecursionError`
    (3.11, 3.12) or succeeds and lets our preflight or jsonschema's own
    error-formatting `repr()` be the one to trip (3.14, and even then
    only sometimes, since the trigger point shifts with the
    interpreter's incidental stack depth) is NOT deterministic across
    environments -- asserting a specific message for THAT fixture would
    itself be a version-dependent flake. Driven through the public
    `load_senses(path, allow_absent=False)` entry point, like every
    other test in this file -- never a private helper called in
    isolation."""
    path = tmp_path / "canon_senses.json"
    depth = cs.MAX_NESTING_DEPTH + 1
    path.write_text("[" * depth + "]" * depth, encoding="utf-8")
    with pytest.raises(cs.CanonSensesLoadError) as exc_info:
        cs.load_senses(path, allow_absent=False)
    message = str(exc_info.value)
    assert str(path) in message
    assert f"{depth} levels deep" in message
    assert f"maximum of {cs.MAX_NESTING_DEPTH}" in message


def test_extreme_nesting_is_rejected_by_some_layer(tmp_path):
    """An EXTREME 100000-level-deep document must always end up as
    `CanonSensesLoadError` -- never a raw `RecursionError` or any other
    uncaught exception -- but WHICH layer catches it is deliberately not
    asserted: the deterministic depth preflight is the intended primary
    defense, but a `RecursionError` backstop inside `json.loads` itself
    (observed on CPython 3.11/3.12, where `json.loads` raises before the
    preflight ever runs) or inside `_schema_validate`'s error-formatting
    `repr()` (observed on 3.14 in some runs) are both still correct
    outcomes -- that's the whole point of layering multiple guards
    instead of relying on exactly one. This is the ONLY test in this
    file whose fixture and assertion are that loose; every other
    CanonSensesLoadError-raising test either uses a smaller, unambiguous
    fixture or asserts specific message content."""
    path = tmp_path / "canon_senses.json"
    path.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(path, allow_absent=False)


def test_nesting_depth_at_the_limit_is_not_a_depth_error(tmp_path):
    """A document nested EXACTLY `MAX_NESTING_DEPTH` levels deep must not
    trip the depth preflight -- the ceiling is deliberately generous
    (real canon_senses.json documents are ~6 levels deep), so this is a
    boundary check on the preflight's `>` (not `>=`) comparison. The
    fixture here is a bare nested-array literal, so it still fails
    downstream schema validation (root must be an object) -- that
    failure is fine and expected; what must NOT happen is the depth
    error specifically."""
    path = tmp_path / "canon_senses.json"
    path.write_text("[" * cs.MAX_NESTING_DEPTH + "]" * cs.MAX_NESTING_DEPTH, encoding="utf-8")
    with pytest.raises(cs.CanonSensesLoadError) as exc_info:
        cs.load_senses(path, allow_absent=False)
    assert "levels deep" not in str(exc_info.value)


def _write_with_lone_surrogate(tmp_path, doc, name="canon_senses.json"):
    """Like `_write`, but for a `doc` containing a Python `str` with an
    embedded LONE SURROGATE codepoint (e.g. `"s1\\ud800"`) -- `_write`'s
    own `json.dumps(doc, ensure_ascii=False)` would try to write that raw
    surrogate directly and fail with UnicodeEncodeError at WRITE time
    (before the loader is ever exercised). `ensure_ascii=True` escapes it
    back to its literal `\\ud800` JSON text form instead, which is pure
    ASCII and therefore always safe to write -- `json.loads` still
    reconstructs the real surrogate codepoint in memory when the fixture
    is read back, exactly as a hostile/corrupted sidecar would."""
    raw = json.dumps(doc, ensure_ascii=True)
    path = tmp_path / name
    path.write_text(raw, encoding="utf-8")
    return path


def test_lone_surrogate_in_value_rejected(tmp_path):
    """A JSON-escaped lone surrogate (e.g. in `sense_id`) parses fine --
    `json.loads` accepts it and hands back a valid Python `str` -- and
    would pass schema validation and the procedural checks untouched, but
    can never be encoded as UTF-8 (every consumer eventually does; e.g.
    canon_adjudication_audit.py's identity-key construction). Must be
    caught by `_reject_unencodable_strings` at load time."""
    doc = _valid_doc(senses=[_valid_sense("s1\ud800"), _valid_sense("s2")])
    path = _write_with_lone_surrogate(tmp_path, doc)
    with pytest.raises(cs.CanonSensesLoadError) as exc_info:
        cs.load_senses(path, allow_absent=False)
    assert "not valid UTF-8" in str(exc_info.value)


def test_lone_surrogate_in_key_rejected(tmp_path):
    """Same as above, but the lone surrogate is in an
    `entries_by_source_form` KEY rather than a value -- the walker checks
    both."""
    doc = _valid_doc(source_form="Jean\ud800")
    path = _write_with_lone_surrogate(tmp_path, doc)
    with pytest.raises(cs.CanonSensesLoadError) as exc_info:
        cs.load_senses(path, allow_absent=False)
    assert "not valid UTF-8" in str(exc_info.value)


# ---------------------------------------------------------------------------
# load_senses -- schema-expressible rejects (strict schema)
# ---------------------------------------------------------------------------

def test_one_sense_record_is_hard_load_error(tmp_path):
    doc = _valid_doc(senses=[_valid_sense("s1")])
    path = _write(tmp_path, doc)
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(path, allow_absent=False)


def test_malformed_evidence_missing_field_rejected(tmp_path):
    evidence = _valid_evidence()
    del evidence["sha256"]
    doc = _valid_doc(senses=[_valid_sense("s1", evidence=evidence), _valid_sense("s2")])
    path = _write(tmp_path, doc)
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(path, allow_absent=False)


def test_stored_quote_field_rejected(tmp_path):
    """`quote` is derived from the offsets at read time and must never be
    persisted -- the schema's additionalProperties:false on `evidence`
    rejects it outright."""
    evidence = _valid_evidence(quote="Jean")
    doc = _valid_doc(senses=[_valid_sense("s1", evidence=evidence), _valid_sense("s2")])
    path = _write(tmp_path, doc)
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(path, allow_absent=False)


def test_stored_context_window_field_rejected(tmp_path):
    evidence = _valid_evidence(context_window="...Jean said...")
    doc = _valid_doc(senses=[_valid_sense("s1", evidence=evidence), _valid_sense("s2")])
    path = _write(tmp_path, doc)
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(path, allow_absent=False)


def test_extra_key_on_sense_object_rejected(tmp_path):
    sense = _valid_sense("s1")
    sense["extra_field"] = "not part of the contract"
    doc = _valid_doc(senses=[sense, _valid_sense("s2")])
    path = _write(tmp_path, doc)
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(path, allow_absent=False)


# ---------------------------------------------------------------------------
# load_senses -- procedural rejects (schema cannot express these)
# ---------------------------------------------------------------------------

def test_duplicate_sense_id_within_entry_rejected(tmp_path):
    """Two senses with the SAME sense_id but distinct disambiguators --
    uniqueItems sees two distinct objects and would not catch this; only
    the procedural check does."""
    s1 = _valid_sense("dup", disambiguator="the king")
    s2 = _valid_sense("dup", disambiguator="the saint")
    doc = _valid_doc(senses=[s1, s2])
    path = _write(tmp_path, doc)
    with pytest.raises(cs.CanonSensesLoadError) as exc_info:
        cs.load_senses(path, allow_absent=False)
    assert "dup" in str(exc_info.value)


def test_non_nfc_key_rejected(tmp_path):
    nfd_key = unicodedata.normalize("NFD", "René")  # decomposed "René"
    assert nfd_key != unicodedata.normalize("NFC", nfd_key)
    doc = _valid_doc(source_form=nfd_key)
    path = _write(tmp_path, doc)
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(path, allow_absent=False)


def test_casefold_collision_rejected(tmp_path):
    """"É" and "é" are each individually valid NFC strings, but
    collide under normalize_form()'s casefold step."""
    doc = {
        "schema_version": 1,
        "entries_by_source_form": {
            "É": {"senses": [_valid_sense("s1"), _valid_sense("s2")]},
            "é": {"senses": [_valid_sense("s3"), _valid_sense("s4")]},
        },
    }
    path = _write(tmp_path, doc)
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(path, allow_absent=False)


def test_whitespace_collision_rejected(tmp_path):
    """"Jean" and " Jean " are each individually valid NFC strings, but
    collide under normalize_form()'s whitespace-collapse step."""
    doc = {
        "schema_version": 1,
        "entries_by_source_form": {
            "Jean": {"senses": [_valid_sense("s1"), _valid_sense("s2")]},
            " Jean ": {"senses": [_valid_sense("s3"), _valid_sense("s4")]},
        },
    }
    path = _write(tmp_path, doc)
    with pytest.raises(cs.CanonSensesLoadError):
        cs.load_senses(path, allow_absent=False)


# ---------------------------------------------------------------------------
# is_split
# ---------------------------------------------------------------------------

def test_is_split_true_for_split_entry_compared_normalized(tmp_path):
    path = _write(tmp_path, _valid_doc(source_form="Jean"))
    result = cs.load_senses(path, allow_absent=False)
    assert cs.is_split(result, "Jean") is True
    assert cs.is_split(result, " jean ") is True
    assert cs.is_split(result, "SomeOtherName") is False


def test_is_split_false_on_empty_result():
    empty = cs.SensesResult(is_empty=True, entries_by_source_form={})
    assert cs.is_split(empty, "Jean") is False


def test_is_split_lookup_cost_does_not_scale_with_entry_count(tmp_path, monkeypatch):
    """RED-before-GREEN witness for the O(n)-per-lookup bug: `is_split`
    used to renormalize EVERY key in `entries_by_source_form` on EVERY
    call (a linear rescan), so a single lookup's own `normalize_form`
    call count scaled with the sidecar's entry count. Once `load_senses`
    has built its `normalized_index`, a lookup must cost exactly ONE
    `normalize_form` call (the query itself) regardless of how many
    entries the sidecar holds."""
    n = 200
    entries = {
        f"name{i:04d}": {"senses": [_valid_sense(f"s{i}a"), _valid_sense(f"s{i}b")]}
        for i in range(n)
    }
    doc = {"schema_version": 1, "entries_by_source_form": entries}
    path = _write(tmp_path, doc)
    result = cs.load_senses(path, allow_absent=False)

    calls = []
    real_normalize_form = cs.normalize_form

    def _counting_normalize_form(s):
        calls.append(s)
        return real_normalize_form(s)

    monkeypatch.setattr(cs, "normalize_form", _counting_normalize_form)

    calls.clear()
    assert cs.is_split(result, "name0100") is True
    hit_calls = len(calls)

    calls.clear()
    assert cs.is_split(result, "does-not-exist") is False
    miss_calls = len(calls)

    assert hit_calls == 1, (
        f"expected exactly 1 normalize_form call per lookup (indexed), got "
        f"{hit_calls} for n={n} entries -- is_split is re-scanning every key"
    )
    assert miss_calls == 1, (
        f"expected exactly 1 normalize_form call per lookup (indexed), got "
        f"{miss_calls} for n={n} entries -- is_split is re-scanning every key"
    )


def test_is_split_collision_index_matches_linear_scan_first_key_wins():
    """Two keys that normalize to the SAME form can never survive
    `load_senses`'s own procedural check (see
    test_casefold_collision_rejected / test_whitespace_collision_rejected
    above), so this drives `entries_by_source_form` directly, bypassing
    `load_senses` entirely, to pin `is_split`'s collision semantics: the
    indexed O(1) path must return the exact same answer the linear scan
    would -- the FIRST key in iteration order wins, never a later
    colliding key silently overwriting it. The two colliding entries are
    deliberately given DIFFERENT split status (1 sense vs. 2) so a wrong
    winner would flip the boolean, not just point at a different but
    equally-split entry."""
    entries = {
        "Jean": {"senses": [_valid_sense("s1")]},  # first key: 1 sense -> NOT split
        " jean ": {"senses": [_valid_sense("s2"), _valid_sense("s3")]},  # collides, 2 senses
    }
    linear = cs.SensesResult(is_empty=False, entries_by_source_form=entries)
    indexed = cs.SensesResult(
        is_empty=False,
        entries_by_source_form=entries,
        normalized_index=cs._build_normalized_index(entries),
    )
    assert cs.is_split(linear, "Jean") is False
    assert cs.is_split(indexed, "Jean") is False
