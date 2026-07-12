"""tests/sense_translated_behaviour.test.py -- regression/characterization
suite for issue #138 (a lockable canon `basis` for sense-translated speaking
names) covering the two behaviours that do not fit an existing test file
(see PLAN-138.md / CONTRACT-138.md S10):

  TP-9  -- segpack.py's canon injection (build_pack()) is entirely
           basis-blind (grep `basis` in segpack.py: zero hits -- see
           canon_map_delivery.test.py's own docstring for the #130
           precedent this mirrors). A `basis:"sense_translated"` entry with
           a non-empty `canonical_target_form` therefore ALREADY lands in
           `canon_names[]` + `canon_map{}` via the real segpack.py, on the
           UNMODIFIED tree, before #138 touches a single schema/prompt
           surface. CHARACTERIZATION: red-before-green is STRUCTURALLY
           IMPOSSIBLE for this test -- there is no code path in segpack.py
           that could ever reject an entry on account of its `basis` value.
           Uses a SINGLE-WORD name (`segpack.py`'s strong_names filter
           requires `mid_sentence` truthy OR `len(name.split()) > 1` --
           multiword names get an automatic pass via the second disjunct,
           which would mask the basis-blindness claim for the far more
           common single-word speaking name; verified empirically against
           the real fr.json config + tokenizer, see the fixture comment).

  TP-7b -- cap-override staleness participation in
           canon_adjudication_audit.py (the Cat 3 `degenerate_cap_overrides`
           freshness binding, R2-2): promoting a `sense_translated` entry
           onto an EXISTING canon target changes nothing structurally
           dangerous (it becomes a new Cat 2 `existing_merge` item, sharing
           an already-counted distinct target -- the entity SET is
           unchanged); only a genuinely NEW, distinct target adds a new
           entity to that set and therefore STALES a previously-fresh
           `degenerate_cap_overrides["__canon__"]` override (mismatched
           `entity_set_fingerprint`). This mirrors
           test_cat3_cap_early_stop_and_override_lifecycle in
           tests/canon_adjudication_audit.test.py exactly, with
           `basis:"sense_translated"` as the promoted entry's basis --
           proving the cap machinery is basis-agnostic (canon_adjudication_
           audit.py never reads the basis ENUM, only the `is_proper_name`
           boolean and the `basis != "not_a_name"` denylist literal -- see
           that file's own module docstring). CHARACTERIZATION for the same
           structural reason as TP-9/TP-14 in canon_adjudication_audit.
           test.py: nothing here can go red pre-#138 either, since the
           script has zero basis-enum-literal hardcoding beyond
           `"not_a_name"` itself. Both cases (existing-target / new-distinct
           -target) are asserted separately per the plan.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import jsonschema
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS_DIR = SKILL_ROOT / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SCHEMAS_DIR = ASSETS_DIR / "schemas"
LANGUAGES_DIR = ASSETS_DIR / "languages"

SEGPACK_SCRIPT = SCRIPTS_DIR / "segpack.py"
ADJUDICATION_SCRIPT = SCRIPTS_DIR / "canon_adjudication_audit.py"
SUMMARY_SCHEMA_PATH = SCHEMAS_DIR / "canon-adjudication-audit-summary.schema.json"

assert SEGPACK_SCRIPT.is_file(), f"segpack.py not found at {SEGPACK_SCRIPT}"
assert ADJUDICATION_SCRIPT.is_file(), f"canon_adjudication_audit.py not found at {ADJUDICATION_SCRIPT}"
assert SUMMARY_SCHEMA_PATH.is_file(), f"summary schema not found at {SUMMARY_SCHEMA_PATH}"
assert (LANGUAGES_DIR / "fr.json").is_file(), f"fr.json not found under {LANGUAGES_DIR}"

SUMMARY_SCHEMA = json.loads(SUMMARY_SCHEMA_PATH.read_text(encoding="utf-8"))


# ===========================================================================
# TP-9 -- segpack.py canon injection is basis-blind.
# ===========================================================================


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/segpack_verse_mount.test.py's and tests/
    canon_map_delivery.test.py's own loader exactly -- segpack.py's `from
    bootstrap_names import ...` only resolves via sys.path[0] under a real
    `python3 segpack.py` invocation, so its own scripts/ directory must be
    inserted onto sys.path around the in-process load."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


SEGPACK_MODULE = _load_module("sense_translated_behaviour_segpack", SEGPACK_SCRIPT, SCRIPTS_DIR)
LANG_CONFIG = SEGPACK_MODULE.load_language_config("fr.json", LANGUAGES_DIR)


def _base_generation_hashes():
    return {"source_extraction_hash": "a" * 40, "source_input_hash": "b" * 40}


def _canon_generation_hashes():
    return {"particle_config_hash": "c" * 40, "derivation_bundle_hash": "d" * 40}


def _manifest_with_two_single_word_names():
    """Both "Sourire" ("smile") and "Ombre" ("shadow") are SINGLE-WORD
    capitalized candidates, each appearing mid-sentence (not immediately
    after a TERMINATOR) so both satisfy strong_names' `mid_sentence`
    disjunct without help from the `multiword` disjunct -- verified
    empirically against the real tokenizer/fr.json config:
    extract_candidates() yields exactly [("Sourire", True), ("Ombre",
    True)] for this text. A multiword fixture (as canon_map_delivery.test.py
    uses) would pass strong_names' filter via `len(name.split()) > 1` alone
    regardless of basis-blindness, masking the claim under test here."""
    return {
        "segments": [
            {
                "seg": "seg01",
                "title_text": "Chapter One",
                "kind": "body",
                "word_count": 12,
                "block_ids": ["p1"],
            }
        ],
        "blocks": {
            "p1": {
                "id": "p1",
                "order_index": 0,
                "plain_text": (
                    "Elle rencontra Sourire dans la foret. "
                    "Elle vit aussi Ombre dans la penombre."
                ),
            },
        },
        "footnotes": [],
        "verse": {"store": []},
        "generation_hashes": _base_generation_hashes(),
    }


def _canon_with_sense_translated_entry():
    return {
        "entries": {
            "Sourire": {
                "source_form": "Sourire",
                "is_proper_name": True,
                "canonical_target_form": "Smile",
                "basis": "sense_translated",
                "confidence": "high",
                "note": "sense-rendering of a speaking name -- the character known for smiling",
            },
        },
        "generation_hashes": _canon_generation_hashes(),
    }


def test_sense_translated_entry_lands_in_canon_names_and_canon_map():
    """TP-9 (CHARACTERIZATION -- see module docstring: red-before-green is
    structurally impossible, segpack.py has zero `basis` awareness). A
    single-word sense_translated entry with a non-empty
    canonical_target_form is delivered into BOTH canon_names[] and
    canon_map{} by the real, unmodified build_pack() -- exactly like every
    other basis (established/transliterated/title/not_a_name)."""
    manifest = _manifest_with_two_single_word_names()
    canon = _canon_with_sense_translated_entry()

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "omit_apparatus")

    assert pack["canon_names"] == ["Sourire"], pack["canon_names"]
    assert pack["new_names"] == ["Ombre"], pack["new_names"]
    assert pack["canon_map"] == {"Sourire": "Smile"}, pack["canon_map"]


def test_sense_translated_entry_still_omitted_from_canon_map_when_target_form_empty():
    """Companion/control: a sense_translated entry with an empty
    canonical_target_form is still validly omitted from canon_map (D4's
    `pattern:"\\S"` is a SCHEMA constraint -- canon_validate.py's job, never
    segpack.py's, per the iron rule "scripts never judge accuracy" this
    plugin already follows for every other schema-side constraint). Proves
    the omission rule (canon_map_delivery.test.py's #130 regression lock)
    is unaffected by the new basis value."""
    manifest = _manifest_with_two_single_word_names()
    canon = _canon_with_sense_translated_entry()
    canon["entries"]["Sourire"]["canonical_target_form"] = ""

    pack = SEGPACK_MODULE.build_pack("seg01", manifest, canon, LANG_CONFIG, "omit_apparatus")

    assert pack["canon_names"] == ["Sourire"]
    assert pack["canon_map"] == {}


# ===========================================================================
# TP-7b -- cap-override staleness participation (canon_adjudication_audit.py)
# ===========================================================================

DEFAULT_GENERATION_HASHES = {
    "particle_config_hash": "test-particle-hash",
    "derivation_bundle_hash": "test-bundle-hash",
}
DEFAULT_TIMESTAMP = "2026-01-01T00:00:00+00:00"


def N(s):
    """Mirrors tests/canon_adjudication_audit.test.py's own N() exactly --
    see that file's module docstring "Key computation" for the frozen spec
    this normalizes per (only used here for entity_set_fingerprint)."""
    return " ".join(unicodedata.normalize("NFC", s).casefold().split())


def canonical_json(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def entity_set_fingerprint(targets):
    """sha256(canonical_json(sorted(distinct N(canonical_target_form)))) --
    the cap-override freshness binding (plan §2 "Cap-override freshness" /
    R2-2). Verbatim copy of tests/canon_adjudication_audit.test.py's own
    helper (no cross-test-file import -- this codebase's established
    convention, see e.g. that file vs. tests/canon_format_validation.test.py
    both re-implementing their own fixture helpers independently)."""
    distinct_nts = sorted({N(t) for t in targets})
    return hashlib.sha256(canonical_json(distinct_nts).encode("utf-8")).hexdigest()


def entry(source_form, target, is_proper_name=True, basis="transliterated", confidence="high", **extra):
    e = {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "canonical_target_form": target,
        "basis": basis,
        "confidence": confidence,
    }
    e.update(extra)
    return e


def cap_override_record(entity_count, pair_count, cap, entity_set_fingerprint, risk_accepted_by="test-reviewer", reason="fixture-authored risk acceptance", timestamp=DEFAULT_TIMESTAMP):
    return {
        "risk_accepted_by": risk_accepted_by,
        "reason": reason,
        "timestamp": timestamp,
        "entity_count": entity_count,
        "pair_count": pair_count,
        "cap": cap,
        "entity_set_fingerprint": entity_set_fingerprint,
    }


def make_durable_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(ADJUDICATION_SCRIPT, scripts_dir / "canon_adjudication_audit.py")
    return root


def write_canon_raw(root, doc):
    (root / "canon.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def write_canon(root, entries, review_queue=None, generation_hashes=None):
    if isinstance(entries, list):
        keyed = {}
        for i, e in enumerate(entries):
            key = e["source_form"] if e["source_form"] not in keyed else f"{e['source_form']}__{i}"
            keyed[key] = e
        entries = keyed
    doc = {
        "entries": entries,
        "review_queue": review_queue if review_queue is not None else [],
        "generation_hashes": generation_hashes if generation_hashes is not None else dict(DEFAULT_GENERATION_HASHES),
    }
    return write_canon_raw(root, doc)


def write_adjudications(root, adjudications=None, degenerate_cap_overrides=None, review_queue_risk_overrides=None):
    doc = {
        "schema_version": 1,
        "_contract": (
            "TEST FIXTURE -- hand-authored per canon_adjudication_audit.py's "
            "iron-rule authoring boundary (never written by the script itself)."
        ),
        "adjudications": adjudications if adjudications is not None else {},
        "degenerate_cap_overrides": degenerate_cap_overrides if degenerate_cap_overrides is not None else {},
        "review_queue_risk_overrides": review_queue_risk_overrides if review_queue_risk_overrides is not None else {},
    }
    (root / "canon_adjudications.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def run_audit(root, *args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "canon_adjudication_audit.py"), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected exactly one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one stdout JSON line, got {len(lines)}:\n{proc.stdout}"
    return json.loads(lines[0])


def assert_summary_schema_valid(summary):
    jsonschema.validate(instance=summary, schema=SUMMARY_SCHEMA)


def _fresh_three_entity_root(tmp_path):
    """Shared setup for both TP-7b cases: 3 entities, cap=1 (so C(3,2)=3
    pairs triggers exactly one cap-note), a FRESH degenerate_cap_overrides
    override -- verified fresh (exit 0) before either test perturbs it.
    Mirrors tests/canon_adjudication_audit.test.py's own
    test_cat3_cap_early_stop_and_override_lifecycle setup exactly."""
    root = make_durable_root(tmp_path)
    write_canon(root, [
        entry("Alpha", "TargetOne"),
        entry("Beta", "TargetTwo"),
        entry("Gamma", "TargetThree"),
    ])
    fingerprint = entity_set_fingerprint(["TargetOne", "TargetTwo", "TargetThree"])
    write_adjudications(root, degenerate_cap_overrides={
        "__canon__": cap_override_record(entity_count=3, pair_count=3, cap=1, entity_set_fingerprint=fingerprint)
    })
    proc = run_audit(root, "--check", "--pair-review-cap", "1")
    assert proc.returncode == 0, (
        f"sanity check failed -- fixture override must be fresh before the promotion under test:\n"
        f"{proc.stdout}{proc.stderr}"
    )
    return root


def test_sense_translated_promotion_onto_existing_target_does_not_stale_cap_override(tmp_path):
    """TP-7b, part 1 (CHARACTERIZATION -- see module docstring). Adding a
    NEW basis:"sense_translated" record whose canonical_target_form matches
    an EXISTING entity's normalized target ("TargetOne") does not change
    the distinct-target entity SET at all -- it becomes a new Cat 2
    (existing_merge) record within an ALREADY-counted entity, not a new
    entity -- so the pre-existing, fresh Cat 3 cap override must stay VALID
    (cap_overrides_ok), unaffected by the merge."""
    root = _fresh_three_entity_root(tmp_path)

    write_canon(root, [
        entry("Alpha", "TargetOne"),
        entry("Beta", "TargetTwo"),
        entry("Gamma", "TargetThree"),
        entry("Loup", "TargetOne", basis="sense_translated", note="sense-rendering of a speaking name"),
    ])
    proc = run_audit(root, "--check", "--pair-review-cap", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr  # the new Cat 2 merge item is unresolved
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["by_kind"]["existing_merge"] == 1, (
        "the sense_translated record sharing 'TargetOne' with 'Alpha' must form a NEW Cat 2 merge item"
    )
    assert summary["totals"]["cap_overrides_ok"] == 1, (
        "the entity SET (distinct targets) is unchanged by a same-target promotion -- "
        "the pre-existing Cat 3 cap override must remain fresh"
    )
    assert summary["totals"]["cap_overrides_missing"] == 0


def test_sense_translated_promotion_onto_new_distinct_target_stales_cap_override(tmp_path):
    """TP-7b, part 2 (CHARACTERIZATION), companion/negative to part 1 above:
    a NEW basis:"sense_translated" record whose canonical_target_form is a
    genuinely NEW, distinct target ("TargetFour") DOES add a new entity to
    the Cat 3 candidate set -- entity_set_fingerprint changes, and the
    previously-fresh cap override goes STALE (cap_overrides_missing),
    exactly like any other basis promoting a brand-new target (R2-2
    freshness, mirroring tests/canon_adjudication_audit.test.py's
    test_cat3_cap_early_stop_and_override_lifecycle) -- sense_translated
    gets no exemption from cap-override re-signing."""
    root = _fresh_three_entity_root(tmp_path)

    write_canon(root, [
        entry("Alpha", "TargetOne"),
        entry("Beta", "TargetTwo"),
        entry("Gamma", "TargetThree"),
        entry("Loup", "TargetFour", basis="sense_translated", note="sense-rendering of a speaking name"),
    ])
    proc = run_audit(root, "--check", "--pair-review-cap", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["cap_overrides_missing"] == 1, (
        "a NEW distinct sense_translated target must change the entity set and "
        "stale the pre-existing cap override"
    )
    assert summary["totals"]["cap_overrides_ok"] == 0
    assert summary["gate_passed"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
