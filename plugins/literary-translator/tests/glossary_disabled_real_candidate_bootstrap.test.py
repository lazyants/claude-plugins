"""tests/glossary_disabled_real_candidate_bootstrap.test.py -- #727 C5 item 6.

``canon_init_zero_candidate_bootstrap.test.py``'s own fixture (uncased Hebrew
against ``he.json``, which ships no ``name_inventory``) is DELIBERATELY built
to produce ZERO candidates -- that is exactly what makes it prove #290's
zero-candidate SKIP-branch bootstrap. Extending it as-is would prove nothing
about whether the NEW ``glossary.enabled: false`` branch (SKILL.md, #727)
actually surfaces a REAL candidate as ``new_names`` when the source genuinely
contains one: name detection is documented to stay load-bearing on this
branch precisely because ``segpack.py`` re-runs ``bootstrap_names.py``'s own
extractor over every segment regardless of whether the glossary research
pass itself ran -- a claim the zero-candidate fixture cannot exercise, since
it has no candidate to surface in the first place.

This sibling suite (same ``tests/_canon_project_fixture.py`` harness, see its
``make_project()``/``run_segpack()`` optional ``particle_config``/``manifest``
overrides added for this purpose) drives the branch's documented command
sequence -- ``canon_validate.py --init`` then ``segpack.py``, identical to the
``no_new_candidates`` SKIP branch's own bootstrap -- against a French project
whose source names "Jean" twice, the second time mid-sentence. That satisfies
BOTH strength heuristics this pipeline actually applies: ``bootstrap_names.py``'s
own ``likely_name`` (``mid_count > 0 or multiword or freq >= 4``) and
``segpack.py``'s own, separate ``strong_names`` filter (``mid > 0 or
multiword``, no frequency floor at all) -- so the candidate is guaranteed
"strong" by either script's own criterion, not merely present.

Codex review follow-up: the ORIGINAL version of this suite proved 'Jean'
reaches ``new_names`` via ``segpack.py``'s own ``strong_names`` filter alone,
but that filter is DELIBERATELY more permissive than the real glossary-pass
admission path (no frequency floor at all) -- so it does not by itself prove
the glossary-research pass would actually have researched this name. A
second test below (``test_real_candidate_is_admitted_by_bootstrap_and_
default_frequency_planner``) drives ``bootstrap_names.collect_candidates()``
and ``glossary_batch_plan.select_included()`` directly -- the REAL functions
the documented, glossary-ENABLED path uses to decide what reaches the
codex-glossary-pass -- over the SAME fixture, at the shipped default
``--min-candidate-freq``, and asserts 'Jean' is admitted there too. Without
this, a future change to either heuristic could silently make 'Jean' a
non-candidate on the ENABLED path while the segpack-only test stayed green.

Deliberate scope cut, stated per the shared #727 contract: this suite does
NOT drive ``select_segments.py`` -- that would need extra staging
(``ledger_merge.py`` plus a real ledger) for a property the stamp-equality
assertion below already covers.
"""
import importlib.util
import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _canon_project_fixture import (  # noqa: E402
    LANGUAGES_SRC,
    SCRIPTS_SRC,
    live_generation_hashes,
    make_project,
    run_canon_init,
    run_script,
    run_segpack,
)


def _load_module(name: str, path: Path):
    """Loads a script module directly from its real shipped path, with
    SCRIPTS_SRC temporarily on sys.path -- glossary_batch_plan.py does a
    sibling `from canon_senses import ...` (same reason
    _canon_project_fixture.py's own load_canon_validate_module() needs the
    same treatment for canon_validate.py)."""
    sys.path.insert(0, str(SCRIPTS_SRC))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS_SRC))


bootstrap_names = _load_module("bootstrap_names_under_test", SCRIPTS_SRC / "bootstrap_names.py")
glossary_batch_plan = _load_module(
    "glossary_batch_plan_under_test", SCRIPTS_SRC / "glossary_batch_plan.py"
)

FRENCH_PARTICLE_CONFIG = "fr.json"
# "Jean" appears TWICE: sentence-initial in the first block (not mid-sentence
# there), and mid-sentence in the second (preceded by "bien") -- so
# bootstrap_names.py's own mid_count > 0 fires regardless of the overall
# frequency, and so does segpack.py's own, independently-computed
# strong_names filter (mid > 0 or multiword, no frequency floor at all).
FRENCH_BLOCK_ONE = "Jean partit tôt le matin pour la ville la plus proche."
FRENCH_BLOCK_TWO = "Le vieux prêtre connaissait bien Jean depuis son enfance."


def french_manifest_doc() -> dict:
    return {
        "segments": [
            {
                "seg": "seg01",
                "title_text": "Chapitre un",
                "kind": "body",
                "word_count": 20,
                "block_ids": ["p1", "p2"],
            }
        ],
        "blocks": {
            "p1": {"id": "p1", "seg": "seg01", "order_index": 0, "plain_text": FRENCH_BLOCK_ONE},
            "p2": {"id": "p2", "seg": "seg01", "order_index": 1, "plain_text": FRENCH_BLOCK_TWO},
        },
        "footnotes": [],
        "verse": {"store": []},
        "generation_hashes": {
            "source_extraction_hash": "c" * 40,
            "source_input_hash": "d" * 40,
        },
    }


# ---------------------------------------------------------------------------
# 0. Fixture premise -- the preset really does detect "Jean" via the
#    Capitalized-run heuristic alone, never a name_inventory override.
# ---------------------------------------------------------------------------


def test_fr_preset_still_ships_no_name_inventory():
    """Mirrors canon_init_zero_candidate_bootstrap.test.py's own premise
    check: if fr.json ever grows a name_inventory, "Jean" could start being
    detected via that override instead of the Capitalized-run heuristic this
    fixture means to exercise -- re-derive rather than trust it silently."""
    preset = json.loads((LANGUAGES_SRC / FRENCH_PARTICLE_CONFIG).read_text(encoding="utf-8"))
    assert "name_inventory" not in preset, (
        f"{FRENCH_PARTICLE_CONFIG} now ships a name_inventory -- this "
        "fixture's candidate detection must be re-derived to keep proving "
        "the Capitalized-run heuristic alone"
    )


# ---------------------------------------------------------------------------
# 1. The acceptance criterion -- the branch's documented sequence surfaces
#    the real candidate as new_names, with an empty canon_names and a
#    genuinely-stamped generation_hashes.
# ---------------------------------------------------------------------------


def test_glossary_disabled_bootstraps_empty_canon_and_surfaces_real_candidate_as_new(tmp_path):
    root = make_project(
        tmp_path, particle_config=FRENCH_PARTICLE_CONFIG, manifest=french_manifest_doc()
    )

    init = run_canon_init(root)
    assert init.returncode == 0, f"canon_validate.py --init failed:\n{init.stdout}\n{init.stderr}"
    payload = json.loads(init.stdout)
    assert payload["success"] is True
    assert payload["mode"] == "init"
    assert payload["created"] is True

    canon = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert canon["entries"] == {}
    assert canon["review_queue"] == []
    # Not merely present: identical to what a live cache_key.py run yields
    # for this project -- what a real glossary merge would have stamped.
    assert canon["generation_hashes"] == live_generation_hashes(root)

    seg = run_segpack(root, particle_config=FRENCH_PARTICLE_CONFIG)
    assert seg.returncode == 0, f"segpack.py failed after --init:\n{seg.stdout}\n{seg.stderr}"

    pack = json.loads((root / "segments" / "segpack_seg01.json").read_text(encoding="utf-8"))
    assert pack["canon_names"] == [], (
        "the bootstrapped canon is entirely empty -- nothing should be "
        f"classified as already-canonized:\n{pack['canon_names']!r}"
    )
    assert "Jean" in pack["new_names"], (
        "expected the real, detectable candidate 'Jean' to surface as an "
        "UNRESOLVED new name -- name detection must stay load-bearing even "
        f"on the glossary.enabled: false branch; got new_names="
        f"{pack['new_names']!r}"
    )
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        assert pack["generation_hashes"][field] == canon["generation_hashes"][field], (
            f"segpack copied a different {field} than the bootstrapped canon carries"
        )


# ---------------------------------------------------------------------------
# 2. Codex follow-up -- 'Jean' is a candidate the ENABLED glossary-research
#    path would actually admit, not merely a string segpack.py's own (more
#    permissive) strong_names filter happens to surface. Drives the REAL
#    bootstrap_names.collect_candidates() and glossary_batch_plan.
#    select_included() over the SAME fixture, at the shipped default
#    --min-candidate-freq.
# ---------------------------------------------------------------------------


def test_real_candidate_is_admitted_by_bootstrap_and_default_frequency_planner(tmp_path):
    """segpack.py's own strong_names filter (mid > 0 or multiword, NO
    frequency floor at all) is deliberately more permissive than the real
    glossary-pass admission path -- so test 1 above proving 'Jean' reaches
    new_names does not by itself prove the glossary-ENABLED path would have
    researched it. This test drives the two REAL functions that actually
    decide that, over the identical fixture manifest, and would catch a
    future change to either heuristic (bootstrap_names.py's own likely_name,
    or glossary_batch_plan.py's own frequency-floor admission) that made
    'Jean' a non-candidate on the enabled path while test 1 stayed green."""
    root = make_project(
        tmp_path, particle_config=FRENCH_PARTICLE_CONFIG, manifest=french_manifest_doc()
    )

    lang = bootstrap_names.load_language_config(
        FRENCH_PARTICLE_CONFIG, languages_dir=root / "languages"
    )
    sources = list(bootstrap_names.iter_manifest_texts(root / "manifest.json"))
    assert sources, "the fixture manifest yielded no scannable text at all"
    result = bootstrap_names.collect_candidates(sources, lang)

    jean_rows = [row for row in result["candidates"] if row["name"] == "Jean"]
    assert len(jean_rows) == 1, (
        f"expected exactly one 'Jean' candidate row from the real "
        f"collect_candidates(); got:\n{result['candidates']}"
    )
    jean = jean_rows[0]
    assert jean["freq"] == 2, (
        f"expected 'Jean' to occur twice across the two fixture blocks; got:\n{jean}"
    )
    assert jean["mid_sentence"] >= 1, (
        "expected at least one mid-sentence occurrence -- the second fixture "
        f"block names 'Jean' right after 'bien'; got:\n{jean}"
    )
    assert jean["likely_name"] is True, (
        "bootstrap_names.py's own likely_name heuristic (mid_count > 0 or "
        f"multiword or freq >= 4) did not admit 'Jean'; got:\n{jean}"
    )

    # No canon.json/review_queue/corrections/senses sidecar exist yet in
    # this fixture -- exactly the state glossary_batch_plan.py's own
    # load_canon()/load_senses_sidecar() treat as "nothing to exclude",
    # mirroring the documented first-glossary-run state on the ENABLED path.
    entry_keys, queued, dismissed = glossary_batch_plan.load_canon(root / "canon.json", False)
    senses = glossary_batch_plan.load_senses_sidecar(root / "canon_senses.json", False)
    included = glossary_batch_plan.select_included(
        result["candidates"],
        entry_keys,
        queued,
        dismissed,
        retry=set(),
        min_freq=glossary_batch_plan.DEFAULT_MIN_CANDIDATE_FREQ,
        senses=senses,
    )
    included_names = {row["name"] for row in included}
    assert "Jean" in included_names, (
        "expected the real glossary_batch_plan.select_included() to admit "
        "'Jean' at the shipped DEFAULT --min-candidate-freq "
        f"({glossary_batch_plan.DEFAULT_MIN_CANDIDATE_FREQ}) -- if this ever "
        "goes red, the glossary-ENABLED path would also have stopped "
        f"researching this name; got included={included_names!r}"
    )
