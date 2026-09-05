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
import re
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
    run_canon_init,
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

    init = run_canon_init(root)
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
    assert run_canon_init(root).returncode == 0

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

    again = run_canon_init(root)
    assert again.returncode == 0, f"second --init failed:\n{again.stdout}\n{again.stderr}"
    payload = json.loads(again.stdout)
    assert payload["created"] is False
    assert canon_path.read_bytes() == before, "--init re-wrote an existing canon.json"

    # The REPORTED counterpart of the byte-identical check above, asserted in
    # the same place so the observable behaviour and the signal a caller reads
    # cannot drift apart. This is the one path where a successful invocation
    # must still answer "no" to "did provenance move?" -- an inverted or
    # stale flag here would be a false provenance signal, which is precisely
    # the class this release exists to close. (The four WRITING modes are
    # covered by canon_stamp_conservation.test.py's own uniformity test; this
    # create-only case is not a writing mode and lives here.)
    assert payload["generation_hashes_restamped"] is False, (
        "--init on an existing canon wrote nothing but reported that "
        "provenance moved"
    )


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


# ---------------------------------------------------------------------------
# 5. SKILL.md wiring, #858 -- the ORDINARY path must name it too
# ---------------------------------------------------------------------------

# The region is bounded by two anchors that BOTH predate #858, and it excludes
# every earlier `--init` in W3 on purpose. Two already sit above it -- the
# `glossary.enabled: false` branch's own bootstrap and the `no_new_candidates`
# SKIP branch's -- so a region that merely started at W3 would find `--init`
# on the UNFIXED file and pass while the ordinary path stayed silent. This one
# starts where the SKIP/`--correct` discussion ends and stops at the
# `resume_setup.py` pre-workflow step, which is the deadline the bootstrap has
# to beat: verified to hold NO `--init` at all on the pre-fix SKILL.md.
ORDINARY_PATH_REGION_START = 'the `disposition: "dismiss"` bullet.'
ORDINARY_PATH_REGION_END = "deterministic pre-workflow step invokes"
# The COMMAND, not the name: the bootstrap paragraph names the preflight in
# its own prose ("before `glossary_preflight.py`"), which sits earlier than
# the fenced init command and would make an ordering check compare a
# sentence against a command.
PREFLIGHT_COMMAND_FRAGMENT = "assets/scripts/glossary_preflight.py"


_FENCED_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)\n```", re.DOTALL)


def _fenced_blocks(region: str) -> list:
    """Every ``` fenced block in the region, body only -- the same shape
    `skill_prose_present.test.py` reads commands with. A command assertion has to
    land on the FENCE: a plan-review round demonstrated that
    `"--init" in region` still passed after the flag was deleted from the fenced
    command, because the surrounding prose names `--init` too."""
    return _FENCED_BLOCK_RE.findall(region)


def _bootstrap_fence_offset(region: str) -> int:
    """Where the fenced bootstrap COMMAND starts, or -1. An ordering check must
    use this rather than `region.find("--init")`: the paragraph introducing the
    step names `--init` in prose several lines ABOVE its own fence, so a
    find()-based offset would still land before the preflight even if the
    command itself were moved below it -- the review-bot finding on this file."""
    for match in _FENCED_BLOCK_RE.finditer(region):
        body = match.group(1)
        if INIT_COMMAND_FRAGMENT in body and "--init" in body:
            return match.start()
    return -1


def _paragraphs(region: str) -> list:
    """Blank-line-separated paragraphs. Split on a blank line that may carry
    whitespace: a stray space would otherwise merge two paragraphs into one, and
    a merged paragraph can satisfy the read-back check with tokens borrowed from
    both halves -- which is the exact binding this helper exists to enforce. The
    read-back assertions have to land on ONE paragraph: a code-review round
    showed that checking for HALT and the two key names anywhere in the region
    passed after the whole read-back paragraph was replaced, because the
    staleness preflight says HALTS a few paragraphs down."""
    return re.split(r"\n\s*\n", region)


def _ordinary_glossary_path_region() -> str:
    text = SKILL_MD.read_text(encoding="utf-8")
    start = text.find(ORDINARY_PATH_REGION_START)
    assert start != -1, (
        "SKILL.md no longer ends its W3 --correct discussion where this test "
        "bounds the ordinary glossary path"
    )
    end = text.find(ORDINARY_PATH_REGION_END, start)
    assert end != -1, (
        "SKILL.md no longer describes the resume_setup.py pre-workflow step "
        "after the ordinary glossary path"
    )
    return text[start:end]


def test_skill_md_ordinary_path_bootstraps_the_canon_before_dispatch():
    """#858: a project's FIRST glossary run reaches the dispatch with no
    canon.json -- the merge that creates it runs after every batch has been
    dispatched -- and the dispatch prompt tells each codex job to read that
    file in full. Measured on a live volume, 2 of 12 such dispatches answered
    with a question about the missing file and wrote no fragment. The SKIP
    branch's bootstrap does not cover this path, so the ordinary path has to
    name the command itself."""
    region = _ordinary_glossary_path_region()

    bootstrap_fences = [
        fence for fence in _fenced_blocks(region)
        if INIT_COMMAND_FRAGMENT in fence and "--init" in fence
    ]
    assert bootstrap_fences, (
        "SKILL.md's ordinary (non-empty-candidates) W3 path carries no fenced "
        "`canon_validate.py --init` command, so a project's first glossary run "
        "still dispatches every batch against a canon.json that does not exist "
        "(#858). Prose mentioning --init is not the instruction; the command is"
    )
    assert any("--plugin-root" in fence for fence in bootstrap_fences), (
        "the ordinary path's bootstrap command omits --plugin-root, which "
        "every generation_hashes-stamping mode refuses to run without (#412)"
    )


def test_skill_md_ordinary_path_bootstrap_precedes_every_later_gate():
    """The bootstrap is only correct BEFORE resume_setup.py: that step's
    glossary input_digest folds in the literal `no-canon` for an absent file
    (resume_setup.py's _canon_hash), so a first run bootstrapped after it would
    hash `no-canon` while every resume attempt hashed the empty canon -- the
    digests would never match and the run could never resume. The region's own
    END anchor IS the resume_setup.py step, so containment proves that half;
    this also pins it ahead of the preflight, which halts the pass. Both offsets
    are COMMAND offsets -- the fenced bootstrap against the fenced preflight
    invocation -- never a prose mention of either."""
    region = _ordinary_glossary_path_region()

    init_offset = _bootstrap_fence_offset(region)
    assert init_offset != -1, (
        "the ordinary path no longer carries a fenced bootstrap command"
    )
    preflight_offset = region.find(PREFLIGHT_COMMAND_FRAGMENT)
    assert preflight_offset != -1, (
        "the ordinary path no longer names the glossary staleness preflight"
    )
    assert init_offset < preflight_offset, (
        "SKILL.md orders the canon bootstrap AFTER glossary_preflight.py on "
        "the ordinary path -- the bootstrap has to precede every step that can "
        "halt or hash, resume_setup.py above all (#858)"
    )


def test_skill_md_ordinary_path_reads_the_bootstrap_back():
    """`${durable_root}/scripts/` is a Step-0a copy the dispatched codex jobs
    hold --write over, so the durable canon_validate.py's own success line is a
    claim about its postcondition, not proof of it. The documented path has to
    confirm the file itself and halt otherwise -- a plan-review MAJOR, admitted."""
    region = _ordinary_glossary_path_region()

    # All four tokens must sit in ONE paragraph. Region-wide, each of them
    # occurs somewhere anyway -- the staleness preflight says HALTS, and
    # entries/review_queue are ordinary canon vocabulary -- so a region-wide
    # check passes with the read-back paragraph deleted outright, which a
    # plan-review round demonstrated by mutation.
    required = ("canon.json", "HALT", "entries", "review_queue")
    read_back = [
        para for para in _paragraphs(region)
        if all(token in para for token in required)
    ]
    assert read_back, (
        "no single paragraph of SKILL.md's ordinary glossary path tells the "
        "session to confirm canon.json itself -- naming `entries` and "
        "`review_queue` -- and to HALT otherwise. ${durable_root}/scripts/ is "
        "writable by the dispatched codex jobs, so the durable "
        "canon_validate.py's own success line is a claim about its "
        "postcondition, not proof of it (#858)"
    )
