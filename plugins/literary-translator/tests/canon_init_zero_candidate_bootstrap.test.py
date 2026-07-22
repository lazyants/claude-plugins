"""tests/canon_init_zero_candidate_bootstrap.test.py -- regression coverage
for issue #290: W3's zero-candidate SKIP branch dead-ended at W3a because
nothing on it ever created canon.json.

The route is the plugin's own designed uncased-script path (#177, enabled by
the he.json preset from #195), and it is reached BY CONSTRUCTION, not as an
edge case: he.json ships no `name_inventory`, so `bootstrap_names.py`'s
`Lu`-gated candidate detector finds nothing in a Hebrew source,
`glossary_batch_plan.py` prints `{"no_new_candidates": true, "batches": []}`,
and SKILL.md's W3 tells the operator to SKIP `resume_setup.py` and the
glossary Workflow entirely. But the glossary merge is the ONLY writer of
canon.json, so following that instruction exactly left W3a's `segpack.py`
exiting 1 with `FATAL: canon.json not found at ...`.

The fix is `canon_validate.py --init` (the bootstrap lives in the module that
already owns canon writing and generation_hashes stamping), wired into
SKILL.md's W3 SKIP sentence. This suite drives the REAL scripts as
subprocesses against an isolated durable_root (see
`tests/_canon_project_fixture.py`) and covers:

  1. The un-bootstrapped negative control -- the #290 fatal itself, verbatim.
     Proves the fixture genuinely reproduces the defect rather than passing
     for a fixture-shaped reason.
  2. The documented SKIP path end to end: bootstrap_names.py ->
     glossary_batch_plan.py (asserting the real `no_new_candidates` marker) ->
     the documented `--init` command -> segpack.py exits 0. The bootstrapped
     canon.json's `generation_hashes` are asserted EQUAL to what a live
     `cache_key.py --field ...` computes for this same project, and to what
     segpack.py copied into the pack it wrote -- not merely present, since a
     hand-rolled stub would satisfy a presence check and still be wrong.
  3. `--init`'s create-only contract: an existing canon.json is left
     byte-identical, never re-stamped (a re-stamp would clear
     select_segments.py's derivation-state gate without regenerating
     anything), and the command stays exit-0 so the documented SKIP-branch
     invocation is safe on every re-run.
  4. SKILL.md's W3 SKIP branch actually naming the command -- a fix that
     leaves the documented path silently dead-ending is not a fix.
"""
import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _canon_project_fixture import (  # noqa: E402
    LANGUAGES_SRC,
    PARTICLE_CONFIG,
    SKILL_MD,
    live_generation_hashes,
    make_project,
    perturb_derivation_bundle,
    run_canon_validate,
    run_init,
    run_script,
    run_segpack,
)


def run_bootstrap_names(root: Path):
    return run_script(root, "bootstrap_names.py", "--particle-config", PARTICLE_CONFIG)


def run_batch_plan(root: Path):
    return run_script(root, "glossary_batch_plan.py")


def walk_zero_candidate_path(root: Path):
    """Drives W3 exactly as SKILL.md documents it, up to (not including) the
    bootstrap, asserting at each step that this project really is on the
    zero-candidate SKIP branch."""
    boot = run_bootstrap_names(root)
    assert boot.returncode == 0, f"bootstrap_names.py failed:\n{boot.stdout}\n{boot.stderr}"
    candidates = json.loads((root / "name_candidates.json").read_text(encoding="utf-8"))
    assert candidates["n_candidates"] == 0, (
        "fixture no longer reproduces #290's route -- bootstrap_names.py found "
        f"{candidates['n_candidates']} candidate(s) in the uncased Hebrew "
        "source, so this project is not on the zero-candidate SKIP branch"
    )

    plan = run_batch_plan(root)
    assert plan.returncode == 0, f"glossary_batch_plan.py failed:\n{plan.stdout}\n{plan.stderr}"
    assert json.loads(plan.stdout) == {"no_new_candidates": True, "batches": []}


# ---------------------------------------------------------------------------
# 0. Fixture premise -- the preset really does ship no name_inventory
# ---------------------------------------------------------------------------


def test_he_preset_still_ships_no_name_inventory():
    preset = json.loads((LANGUAGES_SRC / PARTICLE_CONFIG).read_text(encoding="utf-8"))
    assert "name_inventory" not in preset, (
        f"{PARTICLE_CONFIG} now ships a name_inventory -- the zero-candidate "
        "route this suite covers is no longer reached by construction from "
        "this preset alone; re-derive the fixture before trusting these tests"
    )


# ---------------------------------------------------------------------------
# 1. Negative control -- the #290 fatal itself
# ---------------------------------------------------------------------------


def test_skip_path_without_bootstrap_still_fatals_at_segpack(tmp_path):
    """Nothing on the SKIP branch creates canon.json, so W3a's segpack.py
    exits 1 with the exact fatal issue #290 reported. Locks the defect in
    place as a characterization: --init is what an operator runs to leave this
    state, never something segpack.py silently papers over."""
    root = make_project(tmp_path)
    walk_zero_candidate_path(root)

    assert not (root / "canon.json").exists(), (
        "canon.json exists before any bootstrap -- the SKIP path is no longer "
        "the un-bootstrapped state this control assumes"
    )

    seg = run_segpack(root)
    assert seg.returncode == 1, f"expected segpack.py to fatal; got {seg.returncode}\n{seg.stdout}"
    assert f"FATAL: canon.json not found at {root / 'canon.json'}" in seg.stderr, (
        f"segpack.py did not fatal on the missing canon.json:\n{seg.stderr}"
    )


# ---------------------------------------------------------------------------
# 2. The acceptance criterion -- documented SKIP path reaches a green W3a
# ---------------------------------------------------------------------------


def test_documented_skip_path_bootstraps_canon_and_segpack_succeeds(tmp_path):
    root = make_project(tmp_path)
    walk_zero_candidate_path(root)

    init = run_init(root)
    assert init.returncode == 0, f"canon_validate.py --init failed:\n{init.stdout}\n{init.stderr}"
    payload = json.loads(init.stdout)
    assert payload["success"] is True
    assert payload["mode"] == "init"
    assert payload["created"] is True

    canon = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert canon["entries"] == {}
    assert canon["review_queue"] == []

    # Not merely present: identical to what a live cache_key.py run yields for
    # this project, which is what a real glossary merge would have stamped.
    assert canon["generation_hashes"] == live_generation_hashes(root)

    seg = run_segpack(root)
    assert seg.returncode == 0, f"segpack.py failed after --init:\n{seg.stdout}\n{seg.stderr}"

    pack = json.loads((root / "segments" / "segpack_seg01.json").read_text(encoding="utf-8"))
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        assert pack["generation_hashes"][field] == canon["generation_hashes"][field], (
            f"segpack copied a different {field} than the bootstrapped canon carries"
        )
    # An empty canon yields no locked forms -- every name in the segment (here,
    # none, since the source is uncased) surfaces via new_names instead.
    assert pack["canon_names"] == []


# ---------------------------------------------------------------------------
# 3. --init's create-only contract
# ---------------------------------------------------------------------------


def test_init_leaves_an_existing_canon_byte_identical(tmp_path):
    """Idempotent AND non-re-stamping. select_segments.py's derivation-state
    gate reads exactly the two generation_hashes --init writes, so a re-stamp
    would let an operator clear that gate without regenerating anything.
    Exit 0 either way, so the documented SKIP-branch command stays safe on
    every re-run of an already-bootstrapped project."""
    root = make_project(tmp_path)
    assert run_init(root).returncode == 0

    canon_path = root / "canon.json"
    before = canon_path.read_bytes()

    # Edit a derivation-bundle member so a re-stamp would provably differ.
    perturb_derivation_bundle(root)
    assert live_generation_hashes(root)["derivation_bundle_hash"] != json.loads(
        before.decode("utf-8")
    )["generation_hashes"]["derivation_bundle_hash"], (
        "fixture edit did not change derivation_bundle_hash -- this test could "
        "not detect a re-stamp"
    )

    again = run_init(root)
    assert again.returncode == 0, f"second --init failed:\n{again.stdout}\n{again.stderr}"
    assert json.loads(again.stdout)["created"] is False
    assert canon_path.read_bytes() == before, "--init re-wrote an existing canon.json"


@pytest.mark.parametrize(
    "extra",
    [
        ["--batch", "frag.json"],
        ["--expect-source-forms-file", "manifest_all.json"],
        ["--merge-batches", "frag.json"],
        ["--verify-merged", "--batch", "frag.json"],
    ],
    ids=["batch", "expect-source-forms-file", "merge-batches", "verify-merged"],
)
def test_init_refuses_fragment_flags(tmp_path, extra):
    """--init reads no fragment; accepting one silently would leave a call site
    believing a batch had been processed when nothing was."""
    root = make_project(tmp_path)
    proc = run_canon_validate(root, "--init", *extra)
    assert proc.returncode == 2, f"expected an argparse usage error, got:\n{proc.stdout}\n{proc.stderr}"
    assert not (root / "canon.json").exists(), "a rejected --init still wrote canon.json"


# ---------------------------------------------------------------------------
# 4. SKILL.md wiring -- the documented path must name the command
# ---------------------------------------------------------------------------

NO_NEW_CANDIDATES_MARKER = '{"no_new_candidates": true, "batches": []}'
INIT_COMMAND_FRAGMENT = "canon_validate.py"
MANDATORY_GATE_HEADING = "**Mandatory homonym-split evidence gate"


def test_skill_md_skip_branch_names_the_bootstrap_command():
    text = SKILL_MD.read_text(encoding="utf-8")

    skip_offset = text.find(NO_NEW_CANDIDATES_MARKER)
    assert skip_offset != -1, "SKILL.md no longer describes the no_new_candidates SKIP branch"
    gate_offset = text.find(MANDATORY_GATE_HEADING)
    assert gate_offset != -1, "SKILL.md no longer carries the mandatory homonym-split gate section"

    branch = text[skip_offset:gate_offset]
    assert "--init" in branch, (
        "SKILL.md's W3 no_new_candidates SKIP branch does not name "
        "`canon_validate.py --init` -- the documented path still dead-ends at "
        "W3a's 'FATAL: canon.json not found' (#290)"
    )
    init_offset = branch.find("--init")
    assert INIT_COMMAND_FRAGMENT in branch[:init_offset], (
        "SKILL.md mentions --init on the SKIP branch without naming the script "
        "it belongs to"
    )
