"""tests/canon_stamp_conservation.test.py -- regression coverage for #291
(a merge that changes nothing must not move `generation_hashes`) and #292
(`--batch` silently ignored alongside another mode).

#291 -- why this matters. `canon.json`'s two `generation_hashes` fields are a
PROVENANCE CLAIM: "this canon's content was produced under this
particle_config and this derivation bundle". `segpack.py` copies them
verbatim into every pack, and `select_segments.py`'s derivation-state gate
compares the pack's copy against a freshly computed `cache_key.py` value to
decide whether a segment is `blocked_needs_regeneration`. `_stamp_write_verify`
used to re-stamp unconditionally, so ANY merge advanced that claim --
including one that merged nothing at all. That let an operator clear the
regeneration gate without regenerating anything, which is the dangerous
direction: segments read as caught-up and stale output ships.

Crucially the hole is NOT confined to an empty fragment, which is why
"reject a zero-item fragment set" was rejected as the fix. `_merge_batch`'s
own contract is "an identical re-submission is a silent no-op" (an accepted
item only collides when it DIFFERS from the existing entry), so a fully
populated fragment of already-merged items also changes nothing and used to
re-stamp -- while reporting `merged_accepted: N`, so it did not even look
like a no-op to the caller. The fix therefore keys on whether the merged
DOCUMENT changed, not on how many items the fragment carried.

The boundary decisions this suite pins deliberately:

  * items already present, byte-identical outcome -> NOT a change, conserve.
  * `review_queue[]`-only change -> IS a change, re-stamp. `review_queue` is
    schema-required content, written by the merge, and read back by
    `glossary_batch_plan.py` (queued names are excluded from re-research), so
    the file's behaviour genuinely changed under the current derivation state.
  * ordering: equality is plain `==` on the document minus the stamp, so a
    list reorder counts as a change. `entries{}` is written with
    `sort_keys=True` so its order is not observable, and `review_queue[]` is
    only ever filtered/appended, so a pure reorder is not reachable through
    `_merge_batch` today -- the decision is pinned anyway.
  * a prior stamp that is missing/incomplete/empty is NOT preserved -- it is
    re-stamped, so a corrupt stamp stays self-healing exactly as before.

#291 also REMOVES the undocumented escape hatch that issue #193 depends on
(#193 names `--merge-batches <empty>` explicitly as its only unsanctioned
restamp path). `--restamp-derivation` is the sanctioned replacement, and is
covered here too -- without it this fix would turn #193's latent brick into
an unconditional one.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _canon_project_fixture import (  # noqa: E402
    SCRIPTS_SRC,
    accepted_item,
    live_generation_hashes,
    make_project,
    perturb_derivation_bundle,
    queued_item,
    read_canon,
    run_canon_validate,
    run_canon_init,
    stamp_of,
    write_fragment,
)


def bootstrapped_project(tmp_path):
    """A project with a real, stamped canon.json, then a derivation-bundle
    edit so a re-stamp would provably change `derivation_bundle_hash`. Every
    #291 test starts here: without the perturbation, "the stamp did not move"
    would be trivially true and the suite would prove nothing."""
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0
    before = stamp_of(root)

    perturb_derivation_bundle(root)
    live = live_generation_hashes(root)
    assert live["derivation_bundle_hash"] != before["derivation_bundle_hash"], (
        "fixture perturbation did not move derivation_bundle_hash -- these "
        "tests could not distinguish a conserved stamp from an unchanged one"
    )
    return root, before, live


def assert_stamp_conserved(root, before, what):
    after = stamp_of(root)
    assert after == before, (
        f"#291: {what} moved canon.json's generation_hashes.\n"
        f"  before: {before}\n"
        f"  after:  {after}\n"
        "A merge that changes nothing must not advance the provenance claim "
        "select_segments.py's derivation-state gate reads."
    )


# ---------------------------------------------------------------------------
# #291 -- a merge that changes nothing must not move the stamp
# ---------------------------------------------------------------------------


def test_zero_item_merge_does_not_move_the_stamp(tmp_path):
    """The exact reproduction from #291: an empty `[]` fragment."""
    root, before, _ = bootstrapped_project(tmp_path)
    frag = write_fragment(root, [])

    proc = run_canon_validate(root, "--merge-batches", str(frag))
    assert proc.returncode == 0, f"merge failed:\n{proc.stdout}\n{proc.stderr}"
    assert_stamp_conserved(root, before, "a zero-item --merge-batches")


def test_noop_merge_of_already_present_entries_does_not_move_the_stamp(tmp_path):
    """The case that rules out "reject empty fragment sets" as a fix: a
    NON-empty fragment whose items are all identical re-submissions merges to
    a byte-identical document, yet reports merged_accepted > 0."""
    root, _, _ = bootstrapped_project(tmp_path)
    frag = write_fragment(root, [accepted_item("אברהם", "Abraham")])

    first = run_canon_validate(root, "--merge-batches", str(frag))
    assert first.returncode == 0, f"first merge failed:\n{first.stdout}\n{first.stderr}"
    after_real_merge = read_canon(root)

    # Perturb AGAIN, after the real merge: without this the re-merge would
    # re-stamp to the value it already holds and the test would pass even
    # against the unfixed script -- a false green.
    perturb_derivation_bundle(root)
    assert live_generation_hashes(root)["derivation_bundle_hash"] != (
        after_real_merge["generation_hashes"]["derivation_bundle_hash"]
    ), "second perturbation did not move the hash -- the re-merge check would be vacuous"

    second = run_canon_validate(root, "--merge-batches", str(frag))
    assert second.returncode == 0, f"re-merge failed:\n{second.stdout}\n{second.stderr}"
    payload = json.loads(second.stdout)
    assert payload["merged_accepted"] == 1, (
        "fixture premise changed: the re-merge no longer reports a merged item, "
        "so it no longer exercises the 'looks like a real merge' shape"
    )

    now = read_canon(root)
    assert now["entries"] == after_real_merge["entries"]
    assert now["review_queue"] == after_real_merge["review_queue"]
    assert_stamp_conserved(
        root, after_real_merge["generation_hashes"], "a no-op merge of already-present entries"
    )


def test_legacy_single_fragment_merge_path_also_conserves_the_stamp(tmp_path):
    """`run_merge` (legacy `--batch`) and `run_merge_batches` share
    `_stamp_write_verify`, so the fix must cover both -- #193 pins that helper
    as the sole stamp writer."""
    root, before, _ = bootstrapped_project(tmp_path)
    frag = write_fragment(root, [])

    proc = run_canon_validate(root, "--batch", str(frag))
    assert proc.returncode == 0, f"legacy merge failed:\n{proc.stdout}\n{proc.stderr}"
    assert_stamp_conserved(root, before, "a zero-item legacy --batch merge")


def test_a_real_content_change_still_moves_the_stamp(tmp_path):
    """Positive control against over-suppression. Suppressing too eagerly is
    the OTHER failure direction: it would strand every project on a stale
    provenance claim and brick segment selection permanently."""
    root, _, live = bootstrapped_project(tmp_path)
    frag = write_fragment(root, [accepted_item("שרה", "Sarah")])

    proc = run_canon_validate(root, "--merge-batches", str(frag))
    assert proc.returncode == 0, f"merge failed:\n{proc.stdout}\n{proc.stderr}"

    canon = read_canon(root)
    assert "שרה" in canon["entries"], "the merge did not actually add the entry"
    assert canon["generation_hashes"] == live, (
        "a merge that genuinely added an entry must advance the stamp to the "
        "current derivation state"
    )


def test_review_queue_only_change_moves_the_stamp(tmp_path):
    """Pinned boundary decision: a merge that only touches review_queue[] IS a
    content change and DOES re-stamp. review_queue is schema-required content
    that glossary_batch_plan.py reads back to exclude queued names from
    re-research, so the file's behaviour genuinely changed."""
    root, _, live = bootstrapped_project(tmp_path)
    frag = write_fragment(root, [queued_item("יצחק")])

    proc = run_canon_validate(root, "--merge-batches", str(frag))
    assert proc.returncode == 0, f"merge failed:\n{proc.stdout}\n{proc.stderr}"

    canon = read_canon(root)
    assert canon["entries"] == {}, "fixture premise: this merge must not touch entries{}"
    assert [q["source_form"] for q in canon["review_queue"]] == ["יצחק"]
    assert canon["generation_hashes"] == live, (
        "a review_queue-only merge is a content change and must re-stamp"
    )


def test_corrupt_prior_stamp_is_healed_not_preserved(tmp_path):
    """A prior stamp that is present-but-empty must never be carried forward.
    canon-file.schema.json types these fields as plain strings and cannot
    reject "", and _stamp_generation_hash refuses to WRITE an empty value --
    so preserving one would smuggle in exactly what that guard exists to
    prevent. Such a stamp is re-stamped instead, keeping it self-healing."""
    root, _, live = bootstrapped_project(tmp_path)

    canon = read_canon(root)
    canon["generation_hashes"]["particle_config_hash"] = ""
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")

    frag = write_fragment(root, [])
    proc = run_canon_validate(root, "--merge-batches", str(frag))
    assert proc.returncode == 0, f"merge failed:\n{proc.stdout}\n{proc.stderr}"

    assert stamp_of(root) == live, (
        "a corrupt (empty-valued) prior stamp must be re-stamped, not conserved"
    )


def test_merge_reports_whether_it_restamped(tmp_path):
    """The conservation must be visible, not silent -- an operator whose gate
    did not clear needs to see why."""
    root, _, _ = bootstrapped_project(tmp_path)

    noop = run_canon_validate(root, "--merge-batches", str(write_fragment(root, [])))
    assert json.loads(noop.stdout)["generation_hashes_restamped"] is False

    real = run_canon_validate(
        root, "--merge-batches", str(write_fragment(root, [accepted_item("רבקה", "Rebecca")], "f2.json"))
    )
    assert json.loads(real.stdout)["generation_hashes_restamped"] is True


# ---------------------------------------------------------------------------
# #291 -- the sanctioned replacement for the escape hatch this fix removes
# ---------------------------------------------------------------------------


def test_restamp_derivation_advances_an_unchanged_canon(tmp_path):
    """#193's ask: a mature, zero-candidate project whose derivation bundle
    moved has no merge to run, so it needs an explicit, named way to re-record
    provenance. This is the sanctioned path that replaces the
    `--merge-batches <empty>` trick #193 documents as unsanctioned."""
    root, before, live = bootstrapped_project(tmp_path)

    proc = run_canon_validate(root, "--restamp-derivation")
    assert proc.returncode == 0, f"--restamp-derivation failed:\n{proc.stdout}\n{proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["mode"] == "restamp_derivation"
    assert payload["generation_hashes_changed"] == ["derivation_bundle_hash"]

    canon = read_canon(root)
    assert canon["generation_hashes"] == live
    assert canon["generation_hashes"] != before
    # Content is untouched -- this advances provenance only.
    assert canon["entries"] == {}
    assert canon["review_queue"] == []


def test_restamp_derivation_requires_an_existing_canon(tmp_path):
    root = make_project(tmp_path)
    proc = run_canon_validate(root, "--restamp-derivation")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert "not found" in payload["error"]
    assert "--init" in payload["error"], (
        "the failure should point at the bootstrap command rather than leaving "
        "the operator to guess"
    )


# ---------------------------------------------------------------------------
# #292 -- --batch alongside another mode must not be silently ignored
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "other_mode",
    [
        ["--check-batch", "other.json"],
        ["--merge-batches", "other.json"],
        ["--restamp-derivation"],
    ],
    ids=["check-batch", "merge-batches", "restamp-derivation"],
)
def test_batch_alongside_another_mode_is_rejected(tmp_path, other_mode):
    """Before #292 this exited 0 with a `"success": true` payload while
    silently discarding --batch -- the dispatch chain's elif ordering means it
    could never be reached. A caller could read that as "my fragment merged"."""
    root, _, _ = bootstrapped_project(tmp_path)
    frag = write_fragment(root, [accepted_item("יעקב", "Jacob")])
    other = write_fragment(root, [], "other.json")
    assert other.is_file()

    proc = run_canon_validate(root, *other_mode, "--batch", str(frag))
    assert proc.returncode == 2, (
        f"expected an argparse usage error, got {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    )
    assert "--batch" in proc.stderr
    assert "יעקב" not in json.dumps(read_canon(root), ensure_ascii=False), (
        "the rejected --batch fragment must not have been merged"
    )


# ---------------------------------------------------------------------------
# One mode table -- adding a mode must require touching exactly one place
# ---------------------------------------------------------------------------


def load_canon_validate_module():
    """In-process load of the REAL canon_validate.py, to read its own
    MODE_SPECS table. Never used to execute the CLI -- every behavioural test
    here drives it as a subprocess. The script's directory goes on sys.path
    for the load so its sibling `from canon_senses import ...` resolves."""
    scripts_dir = SCRIPTS_SRC
    sys.path.insert(0, str(scripts_dir))
    try:
        spec = importlib.util.spec_from_file_location(
            "canon_validate_modes_under_test", scripts_dir / "canon_validate.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(scripts_dir))


def test_every_parser_mode_flag_is_declared_in_the_one_mode_table():
    """The P3 drift guard. Previously main() carried THREE hand-maintained
    lists -- the mode table, a subset tuple for the fragmentless guard, and a
    `!= "--verify-merged"` magic string for the batch guard -- so a mode added
    to one was silently missed by the others. Verified by experiment before
    the fix: a new fragmentless mode added to the first table only sailed
    past the --expect-source-forms-file guard that rejects --init in the
    identical position.

    Checks BOTH directions, because either gap reintroduces the defect: a
    parser flag missing from the table gets no guards, and a table row with
    no parser flag is a typo that silently never matches."""
    module = load_canon_validate_module()
    parser = module.build_arg_parser()

    parser_dests = {action.dest for action in parser._actions} - set(module.NON_MODE_DESTS)
    table_dests = {spec.dest for spec in module.MODE_SPECS}
    assert parser_dests == table_dests, (
        "canon_validate.py's mode flags and MODE_SPECS have drifted apart.\n"
        f"  on the parser but NOT in MODE_SPECS: {sorted(parser_dests - table_dests)}\n"
        f"  in MODE_SPECS but NOT on the parser: {sorted(table_dests - parser_dests)}\n"
        "Every guard in main() is a comprehension over MODE_SPECS, so a mode "
        "missing from it silently gets no mutual-exclusion, no --batch "
        "conflict check and no fragmentless check."
    )

    option_strings = {opt for action in parser._actions for opt in action.option_strings}
    for spec in module.MODE_SPECS:
        assert spec.flag in option_strings, (
            f"MODE_SPECS names {spec.flag!r}, which is not an option string on "
            "the parser -- error messages would name a flag that does not exist"
        )


@pytest.mark.parametrize(
    "flag", ["--init", "--restamp-derivation"], ids=["init", "restamp-derivation"]
)
def test_no_fragmentless_mode_accepts_a_source_forms_manifest(tmp_path, flag):
    """Behavioural counterpart to the table check: every mode declared
    reads_fragment=False must actually reject the manifest flag, not just be
    listed as such."""
    root, _, _ = bootstrapped_project(tmp_path)
    proc = run_canon_validate(root, flag, "--expect-source-forms-file", "manifest_all.json")
    assert proc.returncode == 2, (
        f"{flag} accepted --expect-source-forms-file:\n{proc.stdout}\n{proc.stderr}"
    )


def test_every_writing_mode_reports_generation_hashes_restamped(tmp_path):
    """P4: one question, one key, across all four writing modes. These fields
    are new in unreleased 1.15.0 with no consumers yet, so this is the last
    moment to make them consistent cheaply."""
    root = make_project(tmp_path)

    init = run_canon_init(root)
    assert json.loads(init.stdout)["generation_hashes_restamped"] is True, (
        "a bootstrap writes a fresh stamp and must report it as such"
    )

    perturb_derivation_bundle(root)
    noop = run_canon_validate(root, "--merge-batches", str(write_fragment(root, [])))
    assert json.loads(noop.stdout)["generation_hashes_restamped"] is False

    legacy = run_canon_validate(root, "--batch", str(write_fragment(root, [], "f_legacy.json")))
    assert json.loads(legacy.stdout)["generation_hashes_restamped"] is False

    restamp = run_canon_validate(root, "--restamp-derivation")
    payload = json.loads(restamp.stdout)
    assert payload["generation_hashes_restamped"] is True
    # The extra detail this mode alone carries, alongside the shared key.
    assert payload["generation_hashes_changed"] == ["derivation_bundle_hash"]

    # A second restamp legitimately moves nothing -- restamped stays true
    # (it did write), while the field list is empty (nothing changed).
    again = json.loads(run_canon_validate(root, "--restamp-derivation").stdout)
    assert again["generation_hashes_restamped"] is True
    assert again["generation_hashes_changed"] == []


def test_the_two_legitimate_batch_shapes_still_work(tmp_path):
    """Negative control for #292: the hard error must not break `--batch`
    alone (legacy single-fragment merge) or `--batch` under --verify-merged
    (the documented repeatable form)."""
    root, _, _ = bootstrapped_project(tmp_path)
    frag = write_fragment(root, [accepted_item("לאה", "Leah")])

    merge = run_canon_validate(root, "--batch", str(frag))
    assert merge.returncode == 0, f"--batch alone broke:\n{merge.stdout}\n{merge.stderr}"
    assert "לאה" in read_canon(root)["entries"]

    verify = run_canon_validate(root, "--verify-merged", "--batch", str(frag))
    assert verify.returncode == 0, f"--verify-merged --batch broke:\n{verify.stdout}\n{verify.stderr}"
    assert json.loads(verify.stdout)["verified"] is True
