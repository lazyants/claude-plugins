"""tests/mentions_default_drift.test.py -- NEW (1.10.0, lt-appendix-4-sessions
Session B, README §8.3): the drift guard for the `mentions_section`
default-ON flip across THREE deliberately-non-shared predicate copies --
`render_obsidian._effective_mentions_enabled`, `assemble._effective_mentions_
enabled`, and `validate_backlinks._effective_enabled`. Precedent for this
file's shape: `tests/extractor_terminators_drift.test.py`.

## Why this file exists (README §3.5/§8.1, MEASURED)

The three predicates are non-shared BY DESIGN (each docstring says so; see
`references/assembly-and-output.md`'s "no shared fold home" precedent for the
same discipline elsewhere in this plugin). A change that edits only
`assets/schemas/profile.schema.json`'s `"default"` annotation changes nothing
at runtime -- there is no defaults-filling machinery anywhere in this repo.
The MEASURED fact this guard exists to close: flipping only TWO of the three
predicates produces `validate_backlinks._disabled_report()` on a profile the
renderer/assembler treat as ENABLED -- a silent false GREEN on an advisory
gate that reports zero of everything. The whole rest of the test suite (2601
tests at the time of the flip) is PROVABLY BLIND to this: every one of the
gate's own tests writes the flag explicitly, so none of them observes the
DEFAULT at all. This file is the only place in the suite that does.

The LEAD runs this guard before tagging, not either session -- a session's
green on its own guard is not the evidence that matters here (README §11).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
RENDER_OBSIDIAN_SRC = SCRIPTS_DIR / "render_obsidian.py"
ASSEMBLE_SRC = SCRIPTS_DIR / "assemble.py"
VALIDATE_BACKLINKS_SRC = SCRIPTS_DIR / "validate_backlinks.py"

for _p in (RENDER_OBSIDIAN_SRC, ASSEMBLE_SRC, VALIDATE_BACKLINKS_SRC):
    assert _p.is_file(), f"required source not found at {_p}"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Loaded from their REAL locations (never an isolated durable_root fixture)
# so each module's own sibling imports (validate_draft, output_resolve,
# bootstrap_names, canon_senses, occurrence_targets) resolve against the
# real, fully-provisioned assets/scripts/ directory -- this file only calls
# the three predicate functions directly, in-process, never a full
# render()/assemble()/gate run.
render_obsidian = _load_module("mentions_drift_render_obsidian", RENDER_OBSIDIAN_SRC)
assemble = _load_module("mentions_drift_assemble", ASSEMBLE_SRC)
validate_backlinks = _load_module("mentions_drift_validate_backlinks", VALIDATE_BACKLINKS_SRC)

PREDICATES = {
    "render_obsidian": render_obsidian._effective_mentions_enabled,
    "assemble": assemble._effective_mentions_enabled,
    "validate_backlinks": validate_backlinks._effective_enabled,
}


# ===========================================================================
# B16 -- the ONLY test in the entire suite that observes the default at all
# (MEASURED: the other 2601 pass identically whether a predicate is flipped
# or not).
# ===========================================================================


def test_absent_enabled_key_is_on_in_all_three_predicates():
    """target: obsidian, NO mentions_section block at all. RED today (pre-
    flip): all three predicates return False."""
    profile = {"output": {"target": "obsidian"}}
    for name, predicate in PREDICATES.items():
        assert predicate(profile) is True, f"{name}._effective_*enabled must default ON"


def test_explicit_false_is_off_in_all_three_predicates():
    """GREEN today and after: a regression PIN -- the flip (`is True` ->
    `is not False`) must not have turned the predicate into an
    unconditional `True`."""
    profile = {
        "output": {
            "target": "obsidian",
            "adapter_config": {"obsidian": {"mentions_section": {"enabled": False}}},
        }
    }
    for name, predicate in PREDICATES.items():
        assert predicate(profile) is False, f"{name}: explicit enabled:false must still be OFF"


def test_non_obsidian_target_stays_off_in_all_three_predicates():
    """GREEN today and after: a regression PIN protecting the dormant-
    sub-block short-circuit -- the exact property the three-way
    duplication exists to guarantee: an obsidian sub-block enabled:true
    under a DIFFERENT output.target must never activate anything."""
    profile = {
        "output": {
            "target": "custom",
            "adapter_config": {"obsidian": {"mentions_section": {"enabled": True}}},
        }
    }
    for name, predicate in PREDICATES.items():
        assert predicate(profile) is False, f"{name}: target != obsidian must stay OFF regardless of enabled"


# ===========================================================================
# B19 -- table-driven over all seven rows of the README §3.5 truth table.
# Asserts EQUALITY BETWEEN THE THREE predicates on every row (never each
# one's value in isolation) -- this is the shape that catches a SEMANTIC
# divergence (one predicate growing a normalization step the others lack)
# as well as a boolean one, PROVIDED all three are called with the exact
# same expression -- see this file's own module docstring on why a
# structural-only guard is a known residual limitation.
# ===========================================================================

TRUTH_TABLE = [
    # (label, profile, expected)
    ("no adapter_config at all", {"output": {"target": "obsidian"}}, True),
    (
        "adapter_config.obsidian: null",
        {"output": {"target": "obsidian", "adapter_config": {"obsidian": None}}},
        True,
    ),
    (
        "mentions_section: null",
        {
            "output": {
                "target": "obsidian",
                "adapter_config": {"obsidian": {"mentions_section": None}},
            }
        },
        True,
    ),
    (
        "enabled: false",
        {
            "output": {
                "target": "obsidian",
                "adapter_config": {"obsidian": {"mentions_section": {"enabled": False}}},
            }
        },
        False,
    ),
    (
        "enabled: true",
        {
            "output": {
                "target": "obsidian",
                "adapter_config": {"obsidian": {"mentions_section": {"enabled": True}}},
            }
        },
        True,
    ),
    (
        "target: custom, enabled: true",
        {
            "output": {
                "target": "custom",
                "adapter_config": {"obsidian": {"mentions_section": {"enabled": True}}},
            }
        },
        False,
    ),
    (
        # B-O1 (lead-decision): enabled: null resolves ON at the predicate
        # (`None is not False`) -- schema-invalid under `type: boolean`, so
        # profile_validate rejects it upstream for well-formed profiles,
        # but the predicate itself never sees a validator.
        "enabled: null",
        {
            "output": {
                "target": "obsidian",
                "adapter_config": {"obsidian": {"mentions_section": {"enabled": None}}},
            }
        },
        True,
    ),
]


@pytest.mark.parametrize("label,profile,expected", TRUTH_TABLE, ids=[row[0] for row in TRUTH_TABLE])
def test_all_three_predicates_agree_on_every_shape(label, profile, expected):
    results = {name: predicate(profile) for name, predicate in PREDICATES.items()}
    assert len(set(results.values())) == 1, (
        f"[{label}] the three predicates disagree: {results} -- Contract 6 "
        f"requires truth-table identity across all three"
    )
    assert results["render_obsidian"] is expected, f"[{label}] expected {expected}, got {results}"


def test_adapter_config_obsidian_null_resolves_on_b_o1():
    """B-O1 (lead-decision, pinned explicitly beyond the table row above):
    `output.target: obsidian` with NO explicit `obsidian` sub-block object
    (`adapter_config.obsidian: null`) resolves to ON -- the exact shape
    `tests/output_adapter_schema_shape.test.py` builds. Consistent with
    absent-means-on: a target: obsidian project with no explicit obsidian
    block still gets the default appendix."""
    profile = {"output": {"target": "obsidian", "adapter_config": {"obsidian": None}}}
    for name, predicate in PREDICATES.items():
        assert predicate(profile) is True, f"{name}: adapter_config.obsidian: null must resolve ON (B-O1)"


# ===========================================================================
# B20 -- render-side end-to-end default-on (sees two of three predicates;
# only this drift guard's own B16 covers validate_backlinks.py, and only
# assemble.test.py's dedicated tests cover assemble.py's own preconditions
# under §O2a -- this test does NOT replace either).
# ===========================================================================


def _make_node(node_id, seg, text):
    return {
        "id": node_id, "seg": seg, "kind": "prose", "raw_type": "PARA",
        "order_index": 0, "medium": "plain", "text": text, "fnrefs": [], "verses": [],
    }


def test_render_side_end_to_end_default_on(tmp_path):
    """A profile with output.target: obsidian and NO mentions_section key
    must render a '## Mentions' section. RED TODAY (pre-flip). Mutation
    after the fix: revert either the renderer's or assemble's predicate
    back to `is True` -> RED (assemble's own precondition-chain contract is
    exercised separately, in assemble.test.py; this test drives
    render_obsidian.render() directly with a hand-authored
    nodestream["mentions"], mirroring render_obsidian_occindex.test.py's
    own fixture style)."""
    canon = {
        "entries": {"Ivan_src": {
            "source_form": "Ivan_src", "is_proper_name": True,
            "canonical_target_form": "Иван", "basis": "transliterated",
            "confidence": "high", "category": "person",
        }},
        "review_queue": [], "generation_hashes": {},
    }
    nodes = [_make_node("n1", "seg01", "Иван пришёл домой.")]
    nodestream = {
        "book": {"seg_order": ["seg01"], "title": "Test Book"},
        "nodes": nodes,
        "footnotes": [],
        "meta": {"target": "ru", "verse_mode": "literal_only", "apparatus_policy": "translate_all"},
        "mentions": {"Ivan_src": [{"source_form": "Ivan_src", "seg": "seg01", "origin": "block", "source_block": "n1"}]},
    }
    # NO mentions_section key anywhere -- the shape this whole guard exists
    # to prove resolves to enabled.
    profile = {
        "target": {"language": {"code": "ru"}},
        "output": {
            "target": "obsidian",
            "name_display": {"parenthetical_originals": "never"},
            "adapter_config": {"obsidian": {"folders": {}}},
        },
    }
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    manifest = render_obsidian.render(nodestream, canon, profile, out_dir)

    entity_note = next(
        out_dir / rel for rel in manifest["written"]
        if (out_dir / rel).read_text(encoding="utf-8").find("source_form: Ivan_src") != -1
    )
    assert "## Mentions" in entity_note.read_text(encoding="utf-8"), (
        "output.target: obsidian with no mentions_section key must render "
        "a Mentions section by default (1.10.0+)"
    )


# ===========================================================================
# FAIL-CLOSED-ALWAYS regression pin (supersedes the earlier §O2a
# implied-vs-explicit axis). Codex review (MAJOR-2, user-ratified) found
# that the implied-vs-explicit advisory-skip posture didn't hold end to
# end: validate_backlinks.py -- the LAST step of the same W9 chain, on the
# SAME default-on predicate -- has no such distinction and unconditionally
# fails closed on a broken Mentions dependency, so an implied flag that
# `assemble.py` let through with a warning still bricked the pipeline one
# step later. The fix matches assemble.py's posture to
# validate_backlinks.py's already-fail-closed one and REMOVES the
# implied/explicit distinction entirely -- `_mentions_explicitly_enabled`
# and `_degrade_or_raise` no longer exist. This pin documents the removal
# deliberately, so their reintroduction is a visible, reviewed choice, not
# a silent drift; the actual end-to-end fail-closed proof (both the
# implied and the explicit-true case, against the real under-provisioned
# durable_root fixture) lives in assemble.test.py's
# `test_implied_flag_also_fails_closed` / `test_explicit_flag_also_fails_closed`.
# ===========================================================================


def test_assemble_has_no_implied_vs_explicit_helpers():
    assert not hasattr(assemble, "_mentions_explicitly_enabled"), (
        "the implied-vs-explicit distinction was removed (fail-closed-"
        "always, MAJOR-2) -- if this helper has come back, its "
        "reintroduction must be a deliberate, reviewed choice"
    )
    assert not hasattr(assemble, "_degrade_or_raise"), (
        "the graceful-degrade branch point was removed along with the "
        "implied-vs-explicit distinction (fail-closed-always, MAJOR-2)"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
