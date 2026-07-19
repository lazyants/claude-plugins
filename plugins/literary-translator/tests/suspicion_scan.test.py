"""Tests for scripts/suspicion_scan.py -- the deterministic,
confidence-INDEPENDENT structural-risk scan over a frozen canon.json (RFC
#215 Phase 2, "skeptic pass", plan Part A).

Module under test lives outside any Python package (a standalone script
copied to ``${durable_root}/scripts/`` at runtime, sibling of
``occ_index.py``/``bootstrap_names.py``/``canon_senses.py``/
``skeptic_constants.py``, all of which it imports bare), so it is loaded
here via importlib from its real path, with ``SCRIPTS_DIR`` temporarily on
``sys.path`` so those sibling imports resolve -- mirrors
``tests/occ_index.test.py``'s own loader exactly. ``skeptic_constants``'s
names (``RISK_SINGLETON``, tags, sentinels, ...) are reached as attributes
of the loaded ``ss`` module (``ss.RISK_SINGLETON``) rather than loaded a
second time -- ``suspicion_scan.py`` already imported them bare into its
own namespace.

Every test names, in its own comment, the exact code mutation that would
make it fail (red-before-green discipline) -- no vacuous asserts.
"""
import difflib
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SCHEMAS_DIR = ASSETS_DIR / "schemas"
SUSPICION_SCAN_SCRIPT = SCRIPTS_DIR / "suspicion_scan.py"
BOOTSTRAP_NAMES_SCRIPT = SCRIPTS_DIR / "bootstrap_names.py"

assert SUSPICION_SCAN_SCRIPT.is_file(), f"suspicion_scan.py not found at {SUSPICION_SCAN_SCRIPT}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/occ_index.test.py's own loader: SCRIPTS_DIR must be on
    sys.path around the in-process load so a standalone script's own
    top-level ``from X import ...`` (occ_index, bootstrap_names,
    canon_senses, skeptic_constants) resolves exactly like it would under a
    real ``python3 suspicion_scan.py`` invocation."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


ss = _load_module("suspicion_scan_under_test", SUSPICION_SCAN_SCRIPT, SCRIPTS_DIR)
bn = _load_module("bootstrap_names_for_suspicion_scan_test", BOOTSTRAP_NAMES_SCRIPT, SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def make_lang(particles=(), stopwords=(), elision_pattern=None, has_elision=None,
              name_inventory=()):
    """Mirrors tests/caseless_offset.test.py's own bn_lang helper."""
    import re
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


def make_block(block_id, text, *, seg="seg01", btype="PARA", order_index=0):
    return {
        "id": block_id, "type": btype, "seg": seg, "order_index": order_index,
        "source_file": "book.txt", "plain_text": text, "sha1": "deadbeef",
    }


def make_manifest(blocks: dict, verse_store=()):
    return {"blocks": blocks, "verse": {"store": list(verse_store)}}


def write_manifest(tmp_path: Path, blocks: dict, verse_store=(), name="manifest.json"):
    manifest = make_manifest(blocks, verse_store)
    path = tmp_path / name
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path, manifest


def make_entry(canonical_target_form, *, source_form=None, basis="transliterated",
               confidence="high", is_proper_name=True, category=None, source=None):
    entry = {
        "canonical_target_form": canonical_target_form,
        "is_proper_name": is_proper_name,
        "basis": basis,
        "confidence": confidence,
    }
    if source_form is not None:
        entry["source_form"] = source_form
    if category is not None:
        entry["category"] = category
    if basis == "established":
        entry["source"] = source or "https://example.org/ref"
    return entry


def write_particle_config(languages_dir: Path, filename: str, *, particles=(), stopwords=(),
                           has_elision=False, elision_pattern=None, name_inventory=None):
    languages_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "PARTICLES": list(particles),
        "STOPWORDS": list(stopwords),
        "has_elision": has_elision,
        "ELISION_RE": elision_pattern,
    }
    if name_inventory is not None:
        doc["name_inventory"] = list(name_inventory)
    path = languages_dir / filename
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def scan(canon_entries, manifest, manifest_path, lang, **overrides):
    """build_worklist() with sensible defaults, overridable per test."""
    kwargs = dict(
        research_mode="live",
        citation_types=("FN", "QUOTE"),
        dispersion_threshold=12,
        sample_cap=50,
        near_threshold=0.15,
        near_cap=40,
        near_pair_budget=5000,
    )
    kwargs.update(overrides)
    return ss.build_worklist(canon_entries, manifest, manifest_path, lang, **kwargs)


def by_form(entries_out):
    return {e["source_form"]: e for e in entries_out}


# ---------------------------------------------------------------------------
# Scope filter (_in_scope)
# ---------------------------------------------------------------------------

def test_in_scope_true_for_ordinary_proper_name_entry():
    # Mutation: `_in_scope` returning False unconditionally would fail this.
    assert ss._in_scope(make_entry("Foo")) is True


def test_in_scope_false_for_is_proper_name_false():
    # Mutation: dropping the `is_proper_name` check from `_in_scope` (leaving
    # only the basis check) would make this wrongly return True.
    assert ss._in_scope(make_entry("Foo", is_proper_name=False)) is False


def test_in_scope_false_for_basis_not_a_name():
    # Mutation: dropping the `basis != NON_IDENTITY_BASIS` check would make
    # this wrongly return True.
    assert ss._in_scope(make_entry("Foo", basis="not_a_name")) is False


# ---------------------------------------------------------------------------
# Class 1: merge_participant
# ---------------------------------------------------------------------------

def test_merge_participant_fires_for_shared_normalized_target(tmp_path):
    canon = {
        "Henry": make_entry("Henry the Fifth", confidence="high"),
        "Hank": make_entry("HENRY THE FIFTH", confidence="high"),
    }
    blocks = {
        "PARA:1": make_block("PARA:1", "Henry arrived.", seg="seg01"),
        "PARA:2": make_block("PARA:2", "Hank left.", seg="seg02"),
    }
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _warnings = scan(canon, manifest, manifest_path, make_lang())
    out = by_form(entries)
    # Mutation: comparing raw canonical_target_form strings instead of
    # normalize_form(...) (NFC+casefold+whitespace-collapse) would miss this
    # case-differing duplicate ("Henry the Fifth" vs "HENRY THE FIFTH").
    assert ss.RISK_MERGE_PARTICIPANT in out["Henry"]["risk_classes"]
    assert ss.RISK_MERGE_PARTICIPANT in out["Hank"]["risk_classes"]
    assert out["Henry"]["group_key"] == out["Hank"]["group_key"] == ss.normalize_form("Henry the Fifth")


def test_merge_participant_silent_on_distinct_targets(tmp_path):
    canon = {
        "Henry": make_entry("Henry the Fifth", confidence="high"),
        "Gerald": make_entry("Gerald the Third", confidence="high"),
    }
    blocks = {
        "PARA:1": make_block("PARA:1", "Henry arrived.", seg="seg01"),
        "PARA:2": make_block("PARA:2", "Gerald left.", seg="seg02"),
    }
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _warnings = scan(canon, manifest, manifest_path, make_lang())
    out = by_form(entries)
    # Mutation: a group-size check of `>= 1` instead of `>= 2` would flag
    # every entry as merge_participant, including these two unrelated ones.
    assert ss.RISK_MERGE_PARTICIPANT not in out.get("Henry", {}).get("risk_classes", [])
    assert ss.RISK_MERGE_PARTICIPANT not in out.get("Gerald", {}).get("risk_classes", [])


def test_merge_participant_respects_scope_filter(tmp_path):
    # Two entries share a normalized target, but one is scope-excluded
    # (is_proper_name: false) -- the filter must run BEFORE grouping, so
    # the remaining single scope_in member never fires merge_participant.
    canon = {
        "Henry": make_entry("Henry the Fifth", confidence="high"),
        "Hank": make_entry("Henry the Fifth", confidence="high", is_proper_name=False),
    }
    blocks = {"PARA:1": make_block("PARA:1", "Henry arrived.", seg="seg01")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _warnings = scan(canon, manifest, manifest_path, make_lang())
    out = by_form(entries)
    # Mutation: grouping over ALL entries (never applying _in_scope first)
    # would still form a 2-member group here and wrongly flag "Henry".
    assert ss.RISK_MERGE_PARTICIPANT not in out.get("Henry", {}).get("risk_classes", [])


# ---------------------------------------------------------------------------
# Class 2: established_offline
# ---------------------------------------------------------------------------

def test_established_offline_fires_only_under_offline_research_mode(tmp_path):
    canon = {"Ivan": make_entry("Ivan", basis="established", confidence="high")}
    blocks = {"PARA:1": make_block("PARA:1", "Ivan arrived.", seg="seg01")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)

    entries_live, _ = scan(canon, manifest, manifest_path, make_lang(), research_mode="live")
    # Mutation: checking `basis == "established"` alone (ignoring
    # research_mode) would flag this under "live" too.
    assert ss.RISK_ESTABLISHED_OFFLINE not in by_form(entries_live).get("Ivan", {}).get("risk_classes", [])

    entries_offline, _ = scan(canon, manifest, manifest_path, make_lang(), research_mode="offline")
    assert ss.RISK_ESTABLISHED_OFFLINE in by_form(entries_offline)["Ivan"]["risk_classes"]


def test_established_offline_silent_for_non_established_basis(tmp_path):
    canon = {"Jasper": make_entry("Jasper", basis="transliterated", confidence="high")}
    blocks = {"PARA:1": make_block("PARA:1", "Jasper arrived.", seg="seg01")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _ = scan(canon, manifest, manifest_path, make_lang(), research_mode="offline")
    # Mutation: flagging established_offline regardless of `basis` would
    # wrongly fire here.
    assert ss.RISK_ESTABLISHED_OFFLINE not in by_form(entries).get("Jasper", {}).get("risk_classes", [])


def test_established_offline_not_scope_filtered(tmp_path):
    # established_offline is a provenance signal, not an identity class --
    # it must fire even for a non-proper-name / not_a_name entry.
    canon = {"Kappa": make_entry("Kappa", basis="established", confidence="high",
                                  is_proper_name=False)}
    blocks = {"PARA:1": make_block("PARA:1", "Kappa appears.", seg="seg01")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _ = scan(canon, manifest, manifest_path, make_lang(), research_mode="offline")
    # Mutation: computing established_offline only over `scope_in` (instead
    # of `canon_entries`) would silently drop this entry.
    assert ss.RISK_ESTABLISHED_OFFLINE in by_form(entries)["Kappa"]["risk_classes"]


# ---------------------------------------------------------------------------
# Class 3: singleton
# ---------------------------------------------------------------------------

def test_singleton_fires_on_exactly_one_occurrence(tmp_path):
    canon = {"Louis": make_entry("Louis", confidence="high")}
    blocks = {"PARA:1": make_block("PARA:1", "Louis arrived alone.", seg="seg01")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _ = scan(canon, manifest, manifest_path, make_lang())
    # Mutation: checking `freq >= 1` instead of `freq == 1` would also flag
    # a 2+-occurrence entry as singleton.
    assert ss.RISK_SINGLETON in by_form(entries)["Louis"]["risk_classes"]


def test_singleton_silent_on_two_occurrences(tmp_path):
    canon = {"Miriam": make_entry("Miriam", confidence="high")}
    blocks = {"PARA:1": make_block("PARA:1", "Miriam left. Miriam returned.", seg="seg01")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _ = scan(canon, manifest, manifest_path, make_lang())
    # Mutation: an off-by-one (`freq <= 1`) would also mark this singleton.
    assert ss.RISK_SINGLETON not in by_form(entries).get("Miriam", {}).get("risk_classes", [])


# ---------------------------------------------------------------------------
# Class 4: high_dispersion
# ---------------------------------------------------------------------------

def test_high_dispersion_fires_when_distinct_segs_meet_threshold(tmp_path):
    canon = {"Nadia": make_entry("Nadia", confidence="high")}
    blocks = {
        "PARA:1": make_block("PARA:1", "Nadia here.", seg="seg01"),
        "PARA:2": make_block("PARA:2", "Nadia there.", seg="seg02"),
    }
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _ = scan(canon, manifest, manifest_path, make_lang(), dispersion_threshold=2)
    # Mutation: counting distinct BLOCKS instead of distinct SEGS would also
    # pass here (blocks and segs happen to coincide), but a `> threshold`
    # (strict) instead of `>=` would wrongly exclude this exact-threshold case.
    assert ss.RISK_HIGH_DISPERSION in by_form(entries)["Nadia"]["risk_classes"]


def test_high_dispersion_silent_below_threshold(tmp_path):
    canon = {"Oskar": make_entry("Oskar", confidence="high")}
    blocks = {
        "PARA:1": make_block("PARA:1", "Oskar here. Oskar again.", seg="seg01"),
    }
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _ = scan(canon, manifest, manifest_path, make_lang(), dispersion_threshold=2)
    # Mutation: computing dispersion from occurrence COUNT instead of
    # distinct-SEG count would wrongly flag this (2 occurrences, 1 seg).
    assert ss.RISK_HIGH_DISPERSION not in by_form(entries).get("Oskar", {}).get("risk_classes", [])


# ---------------------------------------------------------------------------
# Class 5: all_citation
# ---------------------------------------------------------------------------

def test_all_citation_fires_when_every_occurrence_is_citation_typed(tmp_path):
    canon = {"Petra": make_entry("Petra", confidence="high")}
    blocks = {"QUOTE:1": make_block("QUOTE:1", "Petra said this.", seg="seg01", btype="QUOTE")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _ = scan(canon, manifest, manifest_path, make_lang(),
                       citation_types=("FN", "QUOTE"))
    # Mutation: checking `any(...)` instead of `all(...)` across occurrences
    # would also fire when only SOME occurrences are citation-typed.
    assert ss.RISK_ALL_CITATION in by_form(entries)["Petra"]["risk_classes"]


def test_all_citation_silent_when_occurrence_is_narrative(tmp_path):
    canon = {"Quentin": make_entry("Quentin", confidence="high")}
    blocks = {"PARA:1": make_block("PARA:1", "Quentin walked.", seg="seg01", btype="PARA")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _ = scan(canon, manifest, manifest_path, make_lang(),
                       citation_types=("FN", "QUOTE"))
    # Mutation: defaulting an unrecognized/PARA block type into the
    # citation set would wrongly fire this.
    assert ss.RISK_ALL_CITATION not in by_form(entries).get("Quentin", {}).get("risk_classes", [])


def test_all_citation_disabled_failsafe_for_custom_format(tmp_path):
    # source.format=custom has no configured default citation-tag set --
    # resolve_citation_block_types returns None, disabling the class
    # fail-safe entirely, even though every occurrence sits in a QUOTE block.
    canon = {"Rosalind": make_entry("Rosalind", confidence="high")}
    blocks = {"QUOTE:1": make_block("QUOTE:1", "Rosalind said this.", seg="seg01", btype="QUOTE")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    citation_types = ss.resolve_citation_block_types("custom", None)
    assert citation_types is None
    entries, _ = scan(canon, manifest, manifest_path, make_lang(), citation_types=citation_types)
    out = by_form(entries)["Rosalind"]
    # Mutation: guessing a citation set from the block's own tag spelling
    # (e.g. treating any block type containing "QUOTE" as citation-like)
    # instead of disabling the class outright would wrongly fire this.
    assert ss.RISK_ALL_CITATION not in out["risk_classes"]
    assert ss.CITATION_UNAVAILABLE_TAG in out.get("notes", [])


def test_all_citation_false_for_zero_occurrence_entry_unit():
    # Direct unit test on _classify_occurrences: an entry with NO
    # occurrences at all must never be labeled all_citation -- that is a
    # distinct "no occurrences" signal (ZERO_OCCURRENCE_TAG), not a
    # citation label.
    risk, notes, _dispersion = ss._classify_occurrences([], citation_types=("FN", "QUOTE"))
    # Mutation: treating an empty occurrence list as vacuously "all
    # citation" (Python's `all([])` is True) without the `freq == 0` guard
    # would wrongly set RISK_ALL_CITATION here.
    assert ss.RISK_ALL_CITATION not in risk
    assert ss.ZERO_OCCURRENCE_TAG in notes


def test_zero_occurrence_entry_not_labeled_all_citation_integration(tmp_path):
    # A zero-occurrence entry that IS otherwise flagged (here via
    # merge_participant, so it actually appears in the worklist) must carry
    # ZERO_OCCURRENCE_TAG and never all_citation.
    canon = {
        "Sanjay": make_entry("Shared Target", confidence="high"),
        "Sunil": make_entry("Shared Target", confidence="high"),
    }
    blocks = {"PARA:1": make_block("PARA:1", "Sunil arrived.", seg="seg01")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _ = scan(canon, manifest, manifest_path, make_lang(),
                       citation_types=("FN", "QUOTE"))
    out = by_form(entries)["Sanjay"]  # "Sanjay" never appears in any block -> freq 0
    assert ss.RISK_ALL_CITATION not in out["risk_classes"]
    assert ss.ZERO_OCCURRENCE_TAG in out.get("notes", [])


# ---------------------------------------------------------------------------
# Class 6: near_merge
# ---------------------------------------------------------------------------

def test_near_merge_catches_mordecai_nordecai_across_first_char_blocks():
    # The round-2 regression: first-char/length blocking would put
    # "Mordecai"/"Nordecai" in different blocks (they differ in char 0) and
    # never generate this candidate pair at all.
    expected_distance = 1 - difflib.SequenceMatcher(None, "mordecai", "nordecai").ratio()
    assert expected_distance <= 0.15
    flagged, _notes, truncated = ss._near_merge(
        ["Mordecai", "Nordecai"], near_threshold=0.15, near_cap=40, near_pair_budget=5000
    )
    # Mutation: blocking candidates by `s[0]` (first character) instead of
    # shared bigrams would never compare this pair at all -- flagged would
    # stay empty.
    assert flagged == {"Mordecai", "Nordecai"}
    assert not truncated


def test_near_merge_integration_via_build_worklist(tmp_path):
    canon = {
        "Mordecai": make_entry("Mordecai", confidence="high"),
        "Nordecai": make_entry("Nordecai", confidence="high"),
    }
    blocks = {
        "PARA:1": make_block("PARA:1", "Mordecai spoke.", seg="seg01"),
        "PARA:2": make_block("PARA:2", "Nordecai spoke.", seg="seg02"),
    }
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _ = scan(canon, manifest, manifest_path, make_lang(), near_threshold=0.15)
    out = by_form(entries)
    # Mutation: forgetting to wire _near_merge's flagged set into
    # per_entry_risk in build_worklist would leave near_merge absent here
    # even though the unit-level check passes.
    assert ss.RISK_NEAR_MERGE in out["Mordecai"]["risk_classes"]
    assert ss.RISK_NEAR_MERGE in out["Nordecai"]["risk_classes"]


def test_near_merge_no_shared_bigram_blind_spot_is_documented_and_real():
    # "Ab"/"Ac" share NO bigram (each 2-char string's sole bigram IS the
    # whole string) but their distance (0.5) would qualify under a
    # generous threshold if they were ever compared -- the documented
    # blind spot: they never are.
    na, nb = ss.normalize_form("Ab"), ss.normalize_form("Ac")
    distance_if_compared = 1 - difflib.SequenceMatcher(None, na, nb).ratio()
    assert distance_if_compared <= 0.6  # would qualify under this threshold
    flagged, _notes, truncated = ss._near_merge(
        ["Ab", "Ac"], near_threshold=0.6, near_cap=40, near_pair_budget=5000
    )
    # Mutation: comparing every pair unconditionally (dropping the
    # bigram-sharing precondition from _near_merge_candidate_pairs) would
    # flag this pair; the blind spot exists BECAUSE blocking, not distance,
    # decides candidacy.
    assert flagged == set()
    assert not truncated


def test_near_merge_budget_truncation_is_deterministic_and_logged():
    # Four forms (already lowercase, so raw == normalized -- keeps the
    # expected pair tuples below directly readable) all sharing bigrams
    # "aa" and "xa" -> 6 candidate pairs; near_pair_budget=3 forces
    # truncation. Deterministic iteration order (sorted bigram keys, sorted
    # members, itertools.combinations order) means the SAME 3 pairs are
    # kept on every run. _near_merge_candidate_pairs returns pairs of the
    # RAW forms passed in (not normalized forms) -- see its own docstring.
    forms = ["xaaa", "xaab", "xaac", "xaad"]
    candidate_pairs, truncated = ss._near_merge_candidate_pairs(
        forms, {f: ss.normalize_form(f) for f in forms}, near_pair_budget=3
    )
    # Mutation: not checking `len(candidate_pairs) >= near_pair_budget`
    # before adding a pair (e.g. checking AFTER, or not at all) would let
    # candidate_pairs grow past the budget silently.
    assert len(candidate_pairs) == 3
    assert truncated is True
    assert candidate_pairs == {("xaaa", "xaab"), ("xaaa", "xaac"), ("xaaa", "xaad")}


def test_near_merge_budget_truncation_logged_in_worklist_warnings(tmp_path):
    canon = {f"Xaa{c}": make_entry(f"Xaa{c}", confidence="high") for c in "abcd"}
    blocks = {
        f"PARA:{i}": make_block(f"PARA:{i}", f"{name} spoke.", seg=f"seg0{i}")
        for i, name in enumerate(canon)
    }
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, warnings = scan(canon, manifest, manifest_path, make_lang(),
                              near_pair_budget=3, near_threshold=1.0)
    # Mutation: truncating candidate generation WITHOUT appending a warning
    # (silent truncation) would leave `warnings` empty here.
    assert any("near_pair_budget" in w or "budget" in w for w in warnings)
    out = by_form(entries)
    flagged_near = [sf for sf, e in out.items() if ss.RISK_NEAR_MERGE in e["risk_classes"]]
    assert flagged_near, "expected at least one near_merge flag under threshold=1.0"
    for sf in flagged_near:
        assert ss.NEAR_BUDGET_TRUNCATED_TAG in out[sf].get("notes", [])


def test_near_merge_tie_break_prefers_lexicographically_smaller_norm_pair():
    # "qqqx"/"qqqy" and "zzzx"/"zzzy" are two DISJOINT-bigram-space pairs
    # (no q/z cross terms) with the IDENTICAL hand-verified distance 0.25.
    # near_cap=1 must keep the pair whose (norm_lo, norm_hi) sorts first.
    forms = ["qqqx", "qqqy", "zzzx", "zzzy"]
    flagged, _notes, truncated = ss._near_merge(
        forms, near_threshold=0.5, near_cap=1, near_pair_budget=5000
    )
    assert not truncated
    # Mutation: sorting only by distance (dropping the (norm_lo, norm_hi)
    # tie-break key) would make the outcome depend on set/dict iteration
    # order instead of always picking the lexicographically smaller pair.
    assert flagged == {"qqqx", "qqqy"}


def _near_merge_flagged_under_hashseed(hashseed: str) -> str:
    """Runs the M6 all-ties-collide scenario in a FRESH subprocess with
    PYTHONHASHSEED forced to `hashseed`, and returns the flagged set as a
    sorted, printed fingerprint -- keeping the hash-seed effect confined to
    the subprocess's own set/dict ordering, exactly the mechanism under
    test (`candidate_pairs` is a `set`, so its iteration order is
    hash-seed-dependent)."""
    script = (
        "import importlib.util, sys\n"
        f"sys.path.insert(0, {str(SCRIPTS_DIR)!r})\n"
        f"spec = importlib.util.spec_from_file_location('ss_hashseed_check', {str(SUSPICION_SCAN_SCRIPT)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "flagged, _notes, _truncated = mod._near_merge(\n"
        "    ['A B', 'a b', 'A  B'], near_threshold=0, near_cap=1, near_pair_budget=5000)\n"
        "print(sorted(flagged))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONHASHSEED": hashseed},
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def test_near_merge_cap_tiebreak_deterministic_across_hash_seeds():
    # Three raw forms ("A B", "a b", "A  B") all normalize (NFC + casefold +
    # whitespace-collapse) to the identical string "a b" -- every pairing
    # among them has distance=0 AND an identical (norm_lo, norm_hi) key, so
    # near_cap=1's surviving pair depends ENTIRELY on whatever tie-break
    # comes after that.
    seed0 = _near_merge_flagged_under_hashseed("0")
    seed1 = _near_merge_flagged_under_hashseed("1")
    # Mutation: a qualifying.sort() key of only (distance, norm_lo, norm_hi)
    # (dropping the raw-form pair from the tie-break) leaves these fully-tied
    # entries in `candidate_pairs`' set-iteration order, which is
    # PYTHONHASHSEED-dependent -- this would make seed0 != seed1 flakily.
    assert seed0 == seed1


def test_near_merge_respects_scope_filter(tmp_path):
    canon = {
        "Mordecai": make_entry("Mordecai", confidence="high"),
        "Nordecai": make_entry("Nordecai", confidence="high", is_proper_name=False),
    }
    blocks = {
        "PARA:1": make_block("PARA:1", "Mordecai spoke.", seg="seg01"),
        "PARA:2": make_block("PARA:2", "Nordecai spoke.", seg="seg02"),
    }
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, _ = scan(canon, manifest, manifest_path, make_lang(), near_threshold=0.15)
    out = by_form(entries)
    # Mutation: running near_merge over ALL canon entries (never filtering
    # via _in_scope) would still flag "Mordecai" against the excluded form.
    assert ss.RISK_NEAR_MERGE not in out.get("Mordecai", {}).get("risk_classes", [])


# ---------------------------------------------------------------------------
# Class 7: sampled
# ---------------------------------------------------------------------------

def test_sampled_global_cap_tiebreak_and_sentinel_determinism():
    scope_in = {}
    for sf in ("Alice1", "Alice2"):
        scope_in[sf] = make_entry(sf, confidence="high", category="alpha")
    for sf in ("Beta1", "Beta2", "Beta3"):
        scope_in[sf] = make_entry(sf, confidence="high", category="beta")
    scope_in["Solo1"] = make_entry("Solo1", confidence="medium")  # no category -> sentinel

    selected = ss._sampled(scope_in, per_entry_risk={}, sample_cap=3)

    # Hand-verified apportionment (see suspicion_scan.test.py's own module
    # docstring math): alpha ideal=1.0 (quota 1, remainder 0), beta
    # ideal=1.5 (quota 1, remainder 0.5), no-category ideal=0.5 (quota 0,
    # remainder 0.5) -- a tie between beta and no-category's remainders,
    # broken by stratum key order: NO_CATEGORY_SENTINEL ("\x00...") sorts
    # before "beta", so the no-category stratum wins the single remaining
    # slot (remainder=1).
    # Mutation: comparing stratum keys as an f-joined string instead of a
    # plain tuple (or omitting NO_CATEGORY_SENTINEL entirely, leaving a
    # bare None/"" key that sorts inconsistently against real categories)
    # could flip this tie the other way.
    assert len(selected) == 3
    assert "Solo1" in selected
    assert len(selected & {"Alice1", "Alice2"}) == 1
    beta_members = ["Beta1", "Beta2", "Beta3"]
    expected_beta = min(beta_members, key=lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest())
    # Mutation: selecting by insertion order (or any non-hash key) instead
    # of ascending sha256(source_form) hex would not reliably pick this
    # specific member.
    assert selected & set(beta_members) == {expected_beta}


def test_sampled_deterministic_across_two_calls():
    scope_in = {f"Form{i}": make_entry(f"Form{i}", confidence="high", category="cat")
                for i in range(9)}
    first = ss._sampled(scope_in, {}, sample_cap=4)
    second = ss._sampled(scope_in, {}, sample_cap=4)
    # Mutation: iterating a plain (unordered-by-hash) set/dict for the
    # first-K selection instead of sorting by sha256 hex would make this
    # nondeterministic across interpreter runs (PYTHONHASHSEED).
    assert first == second
    assert len(first) == 4


def test_sampled_excludes_entries_already_flagged_by_earlier_classes():
    scope_in = {
        "Tara": make_entry("Tara", confidence="high"),
        "Ulric": make_entry("Ulric", confidence="high"),
    }
    per_entry_risk = {"Tara": {ss.RISK_SINGLETON}}
    selected = ss._sampled(scope_in, per_entry_risk, sample_cap=50)
    # Mutation: not checking `per_entry_risk.get(sf)` (sampling from the
    # full scope_in regardless of prior flags) would include "Tara" too.
    assert selected == {"Ulric"}


def test_sampled_excludes_low_confidence():
    scope_in = {"Vera": make_entry("Vera", confidence="low")}
    selected = ss._sampled(scope_in, {}, sample_cap=50)
    # Mutation: checking confidence "not in ('low',)" (inverted) instead of
    # "in ('high','medium')" would wrongly include a low-confidence entry.
    assert selected == set()


# ---------------------------------------------------------------------------
# Verse precision (mount-aware, round-3 blocker 2) -- 5 sub-cases
# ---------------------------------------------------------------------------

def test_verse_i_standalone_verse_counted_exactly_once_not_double(tmp_path):
    blocks = {
        "VERSE:seg01:1": make_block("VERSE:seg01:1", "Aldous wandered.", seg="seg01", btype="VERSE"),
    }
    verse_store = [{
        "vid": "V001", "placeholder": "⟦VERSE_V001_ab⟧", "context": "body",
        "mount": "block", "parent_block": "VERSE:seg01:1",
        "plain_text": "Aldous wandered.", "sha1": "s1",
    }]
    canon = {"Aldous": make_entry("Aldous", confidence="high")}
    manifest_path, manifest = write_manifest(tmp_path, blocks, verse_store)
    entries, _ = scan(canon, manifest, manifest_path, make_lang())
    out = by_form(entries)["Aldous"]
    # Mutation: re-scanning mount:"block" entries from verse.store (removing
    # the `!= VERSE_MOUNT_EMBEDDED` skip in verse_occurrences()) would make
    # freq become 2 (block scan + verse scan), falsely losing singleton.
    assert ss.RISK_SINGLETON in out["risk_classes"]
    assert len(out["occurrence_refs"]) == 1


def test_verse_ii_embedded_only_mention_is_surfaced(tmp_path):
    blocks = {
        "PARA:1": make_block("PARA:1", "He read the verse: ⟦VERSE_V002_ab⟧ and left.",
                              seg="seg01"),
    }
    verse_store = [{
        "vid": "V002", "placeholder": "⟦VERSE_V002_ab⟧", "context": "body",
        "mount": "embedded", "parent_block": "PARA:1",
        "plain_text": "Beatrice wept alone.", "sha1": "s2",
    }]
    canon = {"Beatrice": make_entry("Beatrice", confidence="high")}
    manifest_path, manifest = write_manifest(tmp_path, blocks, verse_store)
    entries, _ = scan(canon, manifest, manifest_path, make_lang())
    out = by_form(entries)["Beatrice"]
    # Mutation: dropping the verse_occurrences() call from
    # _combined_occurrences (block-scan-only) would make "Beatrice" vanish
    # entirely -- freq stays 0 (she never appears in the carrier's own
    # plain_text, only inside the placeholder-replaced verse text).
    assert ss.RISK_SINGLETON in out["risk_classes"]


def test_verse_iii_carrier_type_classification_not_coarse_context(tmp_path):
    blocks = {
        "QUOTE:1": make_block("QUOTE:1", "⟦VERSE_V003_ab⟧", seg="seg01", btype="QUOTE"),
    }
    verse_store = [{
        # context says "body" (non-citation) -- classification must use the
        # CARRIER's own type ("QUOTE"), never this coarse field.
        "vid": "V003", "placeholder": "⟦VERSE_V003_ab⟧", "context": "body",
        "mount": "embedded", "parent_block": "QUOTE:1",
        "plain_text": "Celeste recited this line.", "sha1": "s3",
    }]
    canon = {"Celeste": make_entry("Celeste", confidence="high")}
    manifest_path, manifest = write_manifest(tmp_path, blocks, verse_store)
    entries, _ = scan(canon, manifest, manifest_path, make_lang(),
                       citation_types=("FN", "QUOTE"))
    out = by_form(entries)["Celeste"]
    # Mutation: classifying via the verse entry's own `context` field
    # ("body") instead of manifest.blocks[parent_block].type ("QUOTE")
    # would falsely suppress all_citation here.
    assert ss.RISK_ALL_CITATION in out["risk_classes"]


def test_verse_iv_malformed_parent_block_unresolved_not_guessed(tmp_path):
    blocks = {}  # parent_block deliberately does not exist
    verse_store = [{
        "vid": "V004", "placeholder": "⟦VERSE_V004_ab⟧", "context": "body",
        "mount": "embedded", "parent_block": "",  # dangling/empty
        "plain_text": "Desmond spoke here.", "sha1": "s4",
    }]
    canon = {"Desmond": make_entry("Desmond", confidence="high")}
    manifest_path, manifest = write_manifest(tmp_path, blocks, verse_store)
    entries, _ = scan(canon, manifest, manifest_path, make_lang(),
                       citation_types=("FN", "QUOTE"))
    out = by_form(entries)["Desmond"]
    # Mutation: guessing a citation classification for a malformed/dangling
    # parent_block (e.g. defaulting block_type to a citation tag, or
    # falling back to the verse's own context) instead of leaving it `None`
    # would risk a false all_citation; a `None` can never be `in`
    # citation_types, so all_citation correctly never fires.
    assert ss.RISK_ALL_CITATION not in out["risk_classes"]
    assert ss.VERSE_PARENT_UNRESOLVED_TAG in out.get("notes", [])
    # Still counted toward freq (singleton fires -- one real occurrence).
    assert ss.RISK_SINGLETON in out["risk_classes"]


def test_verse_v_mount_absent_treated_as_block_backed_not_rescanned():
    manifest = {
        "blocks": {},
        "verse": {"store": [{
            "vid": "V005", "placeholder": "x", "context": "body",
            "parent_block": "VERSE:seg01:9", "plain_text": "Edwin here.", "sha1": "s5",
            # no "mount" key at all
        }]},
    }
    lang = make_lang()
    result = ss.verse_occurrences("Edwin", manifest, lang)
    # Mutation: defaulting an absent `mount` to "embedded" (e.g.
    # `entry.get("mount", VERSE_MOUNT_EMBEDDED)`) instead of leaving it
    # anything-but-"embedded" (which skips) would scan this entry and, if
    # "Edwin" also appears in its own real VERSE: block elsewhere (the
    # standalone representation), double-count it.
    assert result == []


def test_verse_vi_two_distinct_embedded_verse_nodes_not_collapsed_by_dedup(tmp_path):
    # Two DISTINCT embedded verse nodes (different vid) share the SAME
    # parent_block -- and, because embedded-verse char offsets are LOCAL to
    # each verse node's own plain_text, they also share the SAME
    # (char_start, char_end) for the SAME source_form. These are two
    # genuinely distinct occurrences (different verse nodes) and must both
    # count: freq 2, no singleton.
    blocks = {
        "PARA:1": make_block("PARA:1", "⟦VERSE_V001_ab⟧ ⟦VERSE_V002_ab⟧", seg="seg01"),
    }
    verse_store = [
        {"vid": "V001", "placeholder": "⟦VERSE_V001_ab⟧", "context": "body",
         "mount": "embedded", "parent_block": "PARA:1",
         "plain_text": "Gideon spoke.", "sha1": "s1"},
        {"vid": "V002", "placeholder": "⟦VERSE_V002_ab⟧", "context": "body",
         "mount": "embedded", "parent_block": "PARA:1",
         "plain_text": "Gideon left.", "sha1": "s2"},
    ]
    canon = {"Gideon": make_entry("Gideon", confidence="high")}
    manifest_path, manifest = write_manifest(tmp_path, blocks, verse_store)
    entries, _ = scan(canon, manifest, manifest_path, make_lang())
    out = by_form(entries)["Gideon"]
    # Mutation: a dedup key in _combined_occurrences that omits occ["vid"]
    # (only (OCC_ORIGIN_VERSE_EMBEDDED, parent_block, char_start, char_end))
    # would wrongly collapse these two distinct verse-node occurrences into
    # one, since both match "Gideon" at the same LOCAL char_start/char_end
    # -- freq would drop to 1 and RISK_SINGLETON would wrongly fire.
    assert ss.RISK_SINGLETON not in out["risk_classes"]
    assert len(out["occurrence_refs"]) == 2


# ---------------------------------------------------------------------------
# unique-block window dedup
# ---------------------------------------------------------------------------

def test_combined_occurrences_dedupes_exact_duplicate_block_records(tmp_path):
    blocks = {"PARA:1": make_block("PARA:1", "Felix met Felix again.", seg="seg01")}
    manifest = make_manifest(blocks)
    block_records = [
        {"source_form": "Felix", "block": "PARA:1", "seg": "seg01", "char_start": 0, "char_end": 5},
        {"source_form": "Felix", "block": "PARA:1", "seg": "seg01", "char_start": 0, "char_end": 5},
        {"source_form": "Felix", "block": "PARA:1", "seg": "seg01", "char_start": 9, "char_end": 14},
    ]
    combined = ss._combined_occurrences("Felix", block_records, manifest, make_lang())
    # Mutation: appending every block_record without the `seen`/dedup-key
    # guard would count the exact duplicate TWICE (3 entries instead of 2),
    # inflating freq and losing precision even though only 2 genuinely
    # distinct spans exist.
    assert len(combined) == 2
    spans = {(c["char_start"], c["char_end"]) for c in combined}
    assert spans == {(0, 5), (9, 14)}


# ---------------------------------------------------------------------------
# empty/absent canon; uncased corpus with no name_inventory
# ---------------------------------------------------------------------------

def test_empty_canon_entries_produces_empty_worklist(tmp_path):
    blocks = {"PARA:1": make_block("PARA:1", "Nothing here.", seg="seg01")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    entries, warnings = scan({}, manifest, manifest_path, make_lang())
    # Mutation: crashing or raising on an empty canon_entries dict (instead
    # of the schema-valid "nothing structurally suspicious" empty list)
    # would fail this.
    assert entries == []
    assert warnings == []


def test_main_tolerates_absent_canon_file(tmp_path):
    languages_dir = tmp_path / "languages"
    write_particle_config(languages_dir, "en.json")
    blocks = {"PARA:1": make_block("PARA:1", "Nothing here.", seg="seg01")}
    manifest_path, _manifest = write_manifest(tmp_path, blocks)
    out_path = tmp_path / "suspicion_worklist.json"
    canon_path = tmp_path / "canon.json"
    assert not canon_path.is_file()

    rc = ss.main([
        "--canon", str(canon_path), "--manifest", str(manifest_path),
        "--particle-config", "en.json", "--languages-dir", str(languages_dir),
        "--research-mode", "live", "--source-format", "plain_text",
        "--out", str(out_path),
    ])
    # Mutation: treating an absent canon.json as a hard error (instead of
    # an empty canon) would make main() return nonzero here.
    assert rc == 0
    worklist = json.loads(out_path.read_text(encoding="utf-8"))
    assert worklist["entries"] == []


def test_main_tolerates_empty_object_canon_file(tmp_path):
    languages_dir = tmp_path / "languages"
    write_particle_config(languages_dir, "en.json")
    blocks = {"PARA:1": make_block("PARA:1", "Nothing here.", seg="seg01")}
    manifest_path, _manifest = write_manifest(tmp_path, blocks)
    out_path = tmp_path / "suspicion_worklist.json"
    canon_path = tmp_path / "canon.json"
    canon_path.write_text("{}", encoding="utf-8")

    rc = ss.main([
        "--canon", str(canon_path), "--manifest", str(manifest_path),
        "--particle-config", "en.json", "--languages-dir", str(languages_dir),
        "--research-mode", "live", "--source-format", "plain_text",
        "--out", str(out_path),
    ])
    assert rc == 0
    worklist = json.loads(out_path.read_text(encoding="utf-8"))
    # Mutation: assuming `canon_doc["entries"]` is always present (KeyError
    # instead of a defaulted {}) would crash on this bare `{}` canon.json.
    assert worklist["entries"] == []


def test_uncased_corpus_no_name_inventory_yields_zero_candidates(tmp_path):
    # A Hebrew form with NO name_inventory configured: is_upper_initial()
    # can never see a candidate in a script with no case distinction (Lo
    # category), and the inventory bypass is disabled (empty inventory) --
    # see caseless_offset.test.py:248-260 for the underlying matcher
    # invariant this test exercises through the full scan pipeline.
    hebrew_form = "משה לייב"
    canon = {hebrew_form: make_entry(hebrew_form, confidence="high")}
    blocks = {"PARA:1": make_block("PARA:1", "ראה משה לייב אתמול.", seg="seg01")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    lang = make_lang(name_inventory=())  # explicitly empty
    entries, _ = scan(canon, manifest, manifest_path, lang)
    out = by_form(entries).get(hebrew_form)
    # Mutation: bypassing bootstrap_names' own is_upper_initial()/inventory
    # gate (e.g. suspicion_scan.py reimplementing its own case-insensitive
    # matching) would surface a spurious occurrence here.
    if out is not None:
        assert ss.ZERO_OCCURRENCE_TAG in out.get("notes", [])
        assert ss.RISK_SINGLETON not in out["risk_classes"]


# ---------------------------------------------------------------------------
# Class 8: fold_collision (#243)
# ---------------------------------------------------------------------------

# The real occ_index.test.py collision pair (space-joined/unvocalized vs
# maqaf-joined/vocalized Baal Shem Tov-style forms) -- both fold to the same
# bootstrap_names.fold_match_key.
FORM_A = "משה לייב"
FORM_B = "מֹשֶׁה־לַיִיב"


def test_fold_collision_two_in_scope_colliding_forms_both_flagged_two_rows(tmp_path):
    # Routing fires BEFORE any occurrence search even runs (the whole point
    # of skipping the corrupted combined-occurrence computation) -- an empty
    # manifest is enough to prove the two rows exist and carry the class.
    canon = {
        FORM_A: make_entry("Target", confidence="high"),
        FORM_B: make_entry("Target", confidence="high"),
    }
    manifest_path, manifest = write_manifest(tmp_path, {})
    competitors = ss.fold_collision_map([FORM_A, FORM_B])
    entries, _warnings = scan(canon, manifest, manifest_path, make_lang(), competitors=competitors)
    out = by_form(entries)
    # Mutation: granularity of "one flag per collision group" (instead of
    # one row per source_form) would collapse these into a single row.
    assert set(out.keys()) == {FORM_A, FORM_B}
    for sf in (FORM_A, FORM_B):
        assert ss.RISK_FOLD_COLLISION in out[sf]["risk_classes"]
        # Mutation: combining fold_collision with the ordinary
        # occurrence-count classes (instead of skipping that computation
        # entirely) would leave occurrence_refs non-empty here even though
        # this form's own occurrences were never even searched.
        assert out[sf]["occurrence_refs"] == []
        assert ss.FOLD_COLLISION_OCCURRENCES_SUPPRESSED_TAG in out[sf].get("notes", [])


def test_fold_collision_sibling_outside_scope_in_keeps_ordinary_counters(tmp_path):
    # FORM_B shares FORM_A's competitor group but is NEVER a scope_in member
    # here (simulates BOTH real exclusion reasons identically -- an
    # is_proper_name:false canon entry, or a split-only canon_senses.json
    # form that never became a canon entry at all -- build_worklist only
    # ever asks "is the sibling in scope_in", never why it might not be).
    canon = {FORM_A: make_entry("Target", confidence="high")}
    blocks = {"PARA:1": make_block("PARA:1", "Lone occurrence here.", seg="seg01")}
    manifest_path, manifest = write_manifest(tmp_path, blocks)
    competitors = ss.fold_collision_map([FORM_A, FORM_B])  # FORM_B never in canon/scope_in
    entries, _ = scan(canon, manifest, manifest_path, make_lang(), competitors=competitors)
    out = by_form(entries)
    # Mutation: flagging fold_collision off the RAW competitors.is_colliding
    # check (global, ignoring scope_in) instead of re-projecting each group
    # down to scope_in would wrongly flag FORM_A here even though its only
    # sibling never gets an output row at all.
    assert ss.RISK_FOLD_COLLISION not in out.get(FORM_A, {}).get("risk_classes", [])


def test_fold_collision_none_competitors_disables_class_entirely(tmp_path):
    # competitors=None (the default -- no caller resolved a senses sidecar)
    # must be inert, even for a genuinely fold-colliding pair -- pre-#243
    # backward compatibility for every existing caller.
    canon = {
        FORM_A: make_entry("Target", confidence="high"),
        FORM_B: make_entry("Target", confidence="high"),
    }
    manifest_path, manifest = write_manifest(tmp_path, {})
    entries, _ = scan(canon, manifest, manifest_path, make_lang())  # no competitors kwarg
    out = by_form(entries)
    for sf in (FORM_A, FORM_B):
        assert ss.RISK_FOLD_COLLISION not in out.get(sf, {}).get("risk_classes", [])


def test_fold_colliding_forms_helper_unit():
    competitors = ss.fold_collision_map([FORM_A, FORM_B, "Solo"])
    # Both scope_in members of the colliding group.
    assert ss._fold_colliding_forms([FORM_A, FORM_B, "Solo"], competitors) == {FORM_A, FORM_B}
    # FORM_B not in THIS call's scope_in_forms -- FORM_A must not collide.
    assert ss._fold_colliding_forms([FORM_A, "Solo"], competitors) == set()
    # competitors=None -- nothing collides.
    assert ss._fold_colliding_forms([FORM_A, FORM_B], None) == set()


# ---------------------------------------------------------------------------
# resolve_citation_block_types
# ---------------------------------------------------------------------------

def test_resolve_citation_block_types_override_wins_for_any_format():
    result = ss.resolve_citation_block_types("custom", ("TAG",))
    # Mutation: ignoring `override` when the format is unknown/custom
    # (falling through to the format map regardless) would return None here.
    assert result == ("TAG",)


def test_resolve_citation_block_types_known_format_default():
    result = ss.resolve_citation_block_types("gutenberg_epub", None)
    assert result == ss.CITATION_BLOCK_TYPES_BY_FORMAT["gutenberg_epub"]


def test_resolve_citation_block_types_unknown_format_disabled():
    result = ss.resolve_citation_block_types("custom", None)
    # Mutation: falling back to a hardcoded default tuple instead of `None`
    # for an unconfigured format would silently re-enable the class 5
    # fail-safe for custom/unknown adapters.
    assert result is None


# ---------------------------------------------------------------------------
# producer_input_digest
# ---------------------------------------------------------------------------

def _write_closure_files(dirpath: Path, contents: dict) -> None:
    for name, content in contents.items():
        (dirpath / name).write_bytes(content)


def test_producer_input_digest_deterministic_across_two_calls(tmp_path):
    contents = {name: f"content-{name}".encode() for name in ss.PRODUCER_CODE_CLOSURE}
    _write_closure_files(tmp_path, contents)
    params = {"dispersion_threshold": 12}
    d1 = ss.compute_producer_input_digest(b"canon", b"manifest", b"senses", params, b"lang", tmp_path)
    d2 = ss.compute_producer_input_digest(b"canon", b"manifest", b"senses", params, b"lang", tmp_path)
    # Mutation: hashing a non-canonical (e.g. insertion-order-dependent)
    # serialization of `resolved_params` would make this flaky across dict
    # construction orders even for byte-identical inputs.
    assert d1 == d2
    assert len(d1) == 64  # sha256 hex


@pytest.mark.parametrize("closure_index", range(5))
def test_producer_input_digest_changes_with_each_closure_member(tmp_path, closure_index):
    names = ss.PRODUCER_CODE_CLOSURE
    contents = {name: f"content-{name}".encode() for name in names}
    _write_closure_files(tmp_path, contents)
    params = {"dispersion_threshold": 12}
    baseline = ss.compute_producer_input_digest(b"canon", b"manifest", b"senses", params, b"lang", tmp_path)

    mutated = dict(contents)
    target = names[closure_index]
    mutated[target] = contents[target] + b"-mutated"
    _write_closure_files(tmp_path, mutated)
    changed = ss.compute_producer_input_digest(b"canon", b"manifest", b"senses", params, b"lang", tmp_path)
    # Mutation: omitting `target` from PRODUCER_CODE_CLOSURE (or reading a
    # cached/stale copy of its bytes) would make a change to that ONE file
    # invisible to the digest, letting a stale worklist silently pass
    # skeptic_setup.py's freshness check after an authoritative edit.
    assert changed != baseline, f"digest did not change when {target} bytes changed"


def test_producer_input_digest_changes_with_canon_manifest_params_and_lang_bytes(tmp_path):
    contents = {name: f"content-{name}".encode() for name in ss.PRODUCER_CODE_CLOSURE}
    _write_closure_files(tmp_path, contents)
    base_params = {"dispersion_threshold": 12}
    baseline = ss.compute_producer_input_digest(b"canon-A", b"manifest-A", b"senses-A", base_params,
                                                 b"lang-A", tmp_path)

    assert ss.compute_producer_input_digest(b"canon-B", b"manifest-A", b"senses-A", base_params,
                                             b"lang-A", tmp_path) != baseline
    assert ss.compute_producer_input_digest(b"canon-A", b"manifest-B", b"senses-A", base_params,
                                             b"lang-A", tmp_path) != baseline
    assert ss.compute_producer_input_digest(b"canon-A", b"manifest-A", b"senses-A",
                                             {"dispersion_threshold": 13},
                                             b"lang-A", tmp_path) != baseline
    # Mutation: forgetting to fold language_config_raw_bytes into the
    # digest (e.g. dropping it from the `parts` list) would make this last
    # comparison equal to baseline even though the particle-config file
    # content differs.
    assert ss.compute_producer_input_digest(b"canon-A", b"manifest-A", b"senses-A", base_params,
                                             b"lang-B", tmp_path) != baseline
    # #243: senses_bytes itself must be folded into the digest -- a curator
    # editing canon_senses.json (e.g. adding/removing a split-only form)
    # with canon/manifest/params/lang all held constant must still change
    # the stamped digest, or a stale worklist computed against the OLD
    # competitors universe would pass skeptic_setup.py's freshness check.
    assert ss.compute_producer_input_digest(b"canon-A", b"manifest-A", b"senses-B", base_params,
                                             b"lang-A", tmp_path) != baseline


def test_producer_input_digest_absent_senses_differs_from_logically_empty_senses(tmp_path):
    # #243: an absent canon_senses.json sidecar (senses_bytes == b"", the
    # tolerant-read convention -- see compute_producer_input_digest's own
    # docstring) must hash DIFFERENTLY from a schema-valid but logically
    # empty document's real bytes -- otherwise deleting the sidecar between
    # scans would be invisible to the digest.
    contents = {name: f"content-{name}".encode() for name in ss.PRODUCER_CODE_CLOSURE}
    _write_closure_files(tmp_path, contents)
    params = {"dispersion_threshold": 12}
    absent = ss.compute_producer_input_digest(b"canon", b"manifest", b"", params, b"lang", tmp_path)
    logically_empty_bytes = b'{"schema_version":1,"entries_by_source_form":{}}'
    logically_empty = ss.compute_producer_input_digest(
        b"canon", b"manifest", logically_empty_bytes, params, b"lang", tmp_path
    )
    # Mutation: normalizing/short-circuiting an absent sidecar's bytes to
    # match a "logically empty" canonical form (instead of hashing the raw
    # b"" a caller actually read) would collapse this distinction.
    assert absent != logically_empty


def test_producer_input_digest_separator_prevents_boundary_collision(tmp_path):
    contents = {name: b"x" for name in ss.PRODUCER_CODE_CLOSURE}
    _write_closure_files(tmp_path, contents)
    params = {}
    d_ab_c = ss.compute_producer_input_digest(b"AB", b"C", b"", params, b"", tmp_path)
    d_a_bc = ss.compute_producer_input_digest(b"A", b"BC", b"", params, b"", tmp_path)
    # Mutation: removing the `hasher.update(b"\\x00")` separator between
    # concatenated parts would make these two genuinely different inputs
    # ("AB"+"C" vs "A"+"BC") hash identically.
    assert d_ab_c != d_a_bc


def test_resolved_scan_params_uses_actual_resolved_citation_types():
    params = ss.resolved_scan_params(
        dispersion_threshold=12, sample_cap=50, windows_per_entity=8,
        near_threshold=0.15, near_cap=40, near_pair_budget=5000,
        research_mode="offline", source_format="custom",
        resolved_citation_types=None,
    )
    # Mutation: recording the raw CLI override (possibly None even when a
    # format default WOULD apply) instead of the fully-RESOLVED citation
    # set would make this field ambiguous between "no override given" and
    # "fail-safe genuinely disabled".
    assert params["citation_block_types"] is None
    assert params["research_mode"] == "offline"

    params2 = ss.resolved_scan_params(
        dispersion_threshold=12, sample_cap=50, windows_per_entity=8,
        near_threshold=0.15, near_cap=40, near_pair_budget=5000,
        research_mode="live", source_format="gutenberg_epub",
        resolved_citation_types=("QUOTE", "FN"),
    )
    # sorted() for canonical hashing regardless of input list order.
    assert params2["citation_block_types"] == ["FN", "QUOTE"]


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------

def test_citation_block_types_explicit_empty_disables_all_citation_via_cli(tmp_path):
    # `--citation-block-types` with ZERO args is a valid, DISTINCT config
    # from omitting the flag entirely: it explicitly means "no block type
    # is ever citation for this project" (all_citation permanently
    # disabled), not "fall back to the source.format default".
    languages_dir = tmp_path / "languages"
    write_particle_config(languages_dir, "en.json")
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({
        "entries": {"Winifred": make_entry("Winifred", confidence="high")},
    }, ensure_ascii=False), encoding="utf-8")
    blocks = {"QUOTE:1": make_block("QUOTE:1", "Winifred said this.", seg="seg01", btype="QUOTE")}
    manifest_path, _manifest = write_manifest(tmp_path, blocks)

    # Baseline: omitting the flag uses gutenberg_epub's own default
    # (FN, QUOTE) -- Winifred's sole occurrence sits in a QUOTE block, so
    # all_citation DOES fire.
    out_default = tmp_path / "default.json"
    rc = ss.main([
        "--canon", str(canon_path), "--manifest", str(manifest_path),
        "--particle-config", "en.json", "--languages-dir", str(languages_dir),
        "--research-mode", "live", "--source-format", "gutenberg_epub",
        "--out", str(out_default),
    ])
    assert rc == 0
    default_entries = json.loads(out_default.read_text(encoding="utf-8"))["entries"]
    assert ss.RISK_ALL_CITATION in by_form(default_entries)["Winifred"]["risk_classes"]

    # Explicit empty override: --citation-block-types with ZERO args.
    out_empty = tmp_path / "empty.json"
    rc = ss.main([
        "--canon", str(canon_path), "--manifest", str(manifest_path),
        "--particle-config", "en.json", "--languages-dir", str(languages_dir),
        "--research-mode", "live", "--source-format", "gutenberg_epub",
        "--citation-block-types", "--out", str(out_empty),
    ])
    # Mutation: `nargs="+"` (instead of "*") would make argparse REJECT this
    # exact invocation (SystemExit) since it requires >=1 value, making an
    # explicit empty citation set unrepresentable on the command line.
    assert rc == 0
    empty_entries = json.loads(out_empty.read_text(encoding="utf-8"))["entries"]
    out = by_form(empty_entries).get("Winifred", {})
    # Mutation: `if args.citation_block_types else None` (truthiness, not
    # `is not None`) would coerce the empty list back to None, silently
    # falling back to the format default and wrongly firing all_citation
    # here exactly as in the baseline run above.
    assert ss.RISK_ALL_CITATION not in out.get("risk_classes", [])


def test_main_end_to_end_produces_schema_valid_worklist_with_matching_digest(tmp_path):
    import jsonschema

    languages_dir = tmp_path / "languages"
    write_particle_config(languages_dir, "en.json")

    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({
        "entries": {
            "Winston": make_entry("Winston", confidence="high"),
        },
        "review_queue": [],
        "generation_hashes": {"particle_config_hash": "x", "derivation_bundle_hash": "y"},
    }, ensure_ascii=False), encoding="utf-8")

    blocks = {"PARA:1": make_block("PARA:1", "Winston walked alone.", seg="seg01")}
    manifest_path, _manifest = write_manifest(tmp_path, blocks)
    out_path = tmp_path / "suspicion_worklist.json"

    rc = ss.main([
        "--canon", str(canon_path), "--manifest", str(manifest_path),
        "--particle-config", "en.json", "--languages-dir", str(languages_dir),
        "--research-mode", "live", "--source-format", "plain_text",
        "--out", str(out_path),
    ])
    assert rc == 0

    worklist = json.loads(out_path.read_text(encoding="utf-8"))
    schema = json.loads((SCHEMAS_DIR / "suspicion-worklist.schema.json").read_text(encoding="utf-8"))
    # Mutation: writing a worklist with, say, an extra undocumented field
    # (additionalProperties: false in the schema) or a missing
    # producer_input_digest would fail this validation.
    jsonschema.Draft202012Validator(schema).validate(worklist)

    out = by_form(worklist["entries"])["Winston"]
    assert ss.RISK_SINGLETON in out["risk_classes"]

    # Independently recompute the digest exactly as main() should have.
    lang = bn.load_language_config("en.json", languages_dir)
    resolved_params = ss.resolved_scan_params(
        dispersion_threshold=ss.DISPERSION_THRESHOLD_DEFAULT,
        sample_cap=ss.SAMPLE_CAP_DEFAULT,
        windows_per_entity=ss.WINDOWS_PER_ENTITY_DEFAULT,
        near_threshold=ss.NEAR_THRESHOLD_DEFAULT,
        near_cap=ss.NEAR_CAP_DEFAULT,
        near_pair_budget=ss.NEAR_PAIR_BUDGET_DEFAULT,
        research_mode="live",
        source_format="plain_text",
        resolved_citation_types=ss.resolve_citation_block_types("plain_text", None),
    )
    expected_digest = ss.compute_producer_input_digest(
        canon_path.read_bytes(), manifest_path.read_bytes(), b"",  # no canon_senses.json in this fixture
        resolved_params, lang.raw_bytes, ss.SCRIPT_DIR,
    )
    # Mutation: hashing the WRONG resolved parameters (e.g. CLI defaults
    # that don't match what main() actually resolved) would desync
    # producer and an independent verifier's recomputed digest.
    assert worklist["producer_input_digest"] == expected_digest


def test_main_cli_senses_path_wires_competitors_split_only_form_never_flags(tmp_path):
    """#243 end-to-end: --senses-path is parsed to build the ambiguity-
    competitors universe (union of canon.json entries + canon_senses.json
    forms, split-only included). FORM_B here is split-only -- present ONLY
    in the sidecar, never in canon.json -- so it can poison FORM_A's
    ambiguity detection (competitor) but never itself becomes eligible for
    output (never in scope_in). FORM_A alone in canon.json, with no OTHER
    scope_in sibling, must therefore stay UNFLAGGED by class 8."""
    languages_dir = tmp_path / "languages"
    write_particle_config(languages_dir, "en.json")

    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({
        "entries": {FORM_A: make_entry("Target", confidence="high")},
    }, ensure_ascii=False), encoding="utf-8")

    valid_evidence = {
        "block": "b1", "seg": "seg01", "char_start": 0, "char_end": 4,
        "context_start": 0, "context_end": 20, "sha256": "a" * 64,
    }
    valid_sense = lambda sid: {  # noqa: E731 -- local test-only shorthand
        "sense_id": sid, "disambiguator": sid, "index_scope": "narrative", "evidence": valid_evidence,
    }
    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(json.dumps({
        "schema_version": 1,
        "entries_by_source_form": {FORM_B: {"senses": [valid_sense("s1"), valid_sense("s2")]}},
    }, ensure_ascii=False), encoding="utf-8")

    blocks = {"PARA:1": make_block("PARA:1", "Lone occurrence here.", seg="seg01")}
    manifest_path, _manifest = write_manifest(tmp_path, blocks)
    out_path = tmp_path / "suspicion_worklist.json"

    rc = ss.main([
        "--canon", str(canon_path), "--manifest", str(manifest_path),
        "--senses-path", str(senses_path),
        "--particle-config", "en.json", "--languages-dir", str(languages_dir),
        "--research-mode", "live", "--source-format", "plain_text",
        "--out", str(out_path),
    ])
    assert rc == 0, "main() must accept --senses-path"
    worklist = json.loads(out_path.read_text(encoding="utf-8"))
    out = by_form(worklist["entries"]).get(FORM_A, {})
    # Mutation: building competitors from canon.json entries alone (never
    # unioning in the senses sidecar's own forms) would trivially pass this
    # assertion for the WRONG reason (FORM_B never even entering the
    # universe) -- this test only proves the negative half; the positive
    # half (two canon entries DO flag) is covered by
    # test_fold_collision_two_in_scope_colliding_forms_both_flagged_two_rows.
    assert ss.RISK_FOLD_COLLISION not in out.get("risk_classes", [])


def test_main_tolerates_absent_senses_path_default(tmp_path):
    languages_dir = tmp_path / "languages"
    write_particle_config(languages_dir, "en.json")
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({
        "entries": {"Winifred": make_entry("Winifred", confidence="high")},
    }, ensure_ascii=False), encoding="utf-8")
    blocks = {"PARA:1": make_block("PARA:1", "Winifred spoke.", seg="seg01")}
    manifest_path, _manifest = write_manifest(tmp_path, blocks)
    out_path = tmp_path / "suspicion_worklist.json"
    senses_path = tmp_path / "canon_senses.json"
    assert not senses_path.is_file()

    rc = ss.main([
        "--canon", str(canon_path), "--manifest", str(manifest_path),
        "--particle-config", "en.json", "--languages-dir", str(languages_dir),
        "--research-mode", "live", "--source-format", "plain_text",
        "--out", str(out_path),
    ])
    # Mutation: treating an absent implicit-default canon_senses.json as a
    # hard error (instead of allow_absent=True, the same tolerance --canon
    # already gets) would make main() return nonzero here.
    assert rc == 0
    worklist = json.loads(out_path.read_text(encoding="utf-8"))
    assert by_form(worklist["entries"])["Winifred"]["risk_classes"] == [ss.RISK_SINGLETON]


def test_main_explicit_senses_path_missing_is_hard_error(tmp_path):
    languages_dir = tmp_path / "languages"
    write_particle_config(languages_dir, "en.json")
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}, ensure_ascii=False), encoding="utf-8")
    blocks = {"PARA:1": make_block("PARA:1", "Nothing here.", seg="seg01")}
    manifest_path, _manifest = write_manifest(tmp_path, blocks)
    missing_senses_path = tmp_path / "typo_canon_senses.json"
    assert not missing_senses_path.is_file()

    rc = ss.main([
        "--canon", str(canon_path), "--manifest", str(manifest_path),
        "--senses-path", str(missing_senses_path),
        "--particle-config", "en.json", "--languages-dir", str(languages_dir),
        "--research-mode", "live", "--source-format", "plain_text",
        "--out", str(tmp_path / "out.json"),
    ])
    # Mutation: `allow_absent=True` unconditionally (never gated on whether
    # --senses-path was explicitly given) would silently treat this typo'd
    # path as "no splits" instead of a hard error.
    assert rc == 1
