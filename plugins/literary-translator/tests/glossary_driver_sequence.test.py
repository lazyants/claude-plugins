#!/usr/bin/env python3
"""The driver's full two-invocation SEQUENCE, driven end to end.

WHY THIS FILE EXISTS, stated plainly because it is the lesson rather than the
feature. The first five suites for this driver tested its pieces — the harness,
the selector, the splice, the channel's refusals — and every one of them stayed
GREEN while the driver's default path could not dispatch a single batch: the
companion resolver was called without the `--durable-root` its shipped CLI
requires. A unit suite over helpers cannot see that, because nothing in it ever
walks the path an operator walks.

So this file asserts OUTCOMES over the real sequence: drive → hand back → record
a verdict → drive again → merge. Codex, the network and the gate scripts are
replaced by recorded fakes, and each fake is CONTRACT-SHAPED — it fails the test
if the driver calls it with arguments the real thing would refuse. That is what
makes a fake worth having: `resolve_codex_companion.py`'s stub here parses the
real script's own required arguments, so the blocker above would have gone red at
the first assertion.

What is NOT faked: the template. Every prompt and command still comes from
executing the shipped `glossary-pass-wf.template.js` under node, because that is
the contract the driver rests on.
"""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
SCRIPTS = SKILL_ROOT / "assets" / "scripts"
DRIVER = SCRIPTS / "glossary_dispatch_driver.py"
JSON_STDOUT = SCRIPTS / "json_stdout.py"
RESOLVER = SCRIPTS / "resolve_codex_companion.py"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node runs the template builders")

CANDIDATES = [{"name": "Alpha", "freq": 5}, {"name": "Beta", "freq": 3}]
BATCHES = [{"index": 0, "candidates": CANDIDATES}]


def _default_rows(bases=("established", "established")):
    """The whole-batch decision an ordinary dispatch produces. One definition, used
    by the companion stub AND by plant_fragment, so a fragment that appeared by
    dispatch is indistinguishable from one a test planted -- which is the point:
    a test may then assert on a row that DIFFERS from it and know that difference
    came from the repair, not from the fixture."""
    return [{"source_form": c["name"], "basis": b, "disposition": "accepted",
             "canonical_target_form": c["name"], "confidence": "high",
             "is_proper_name": True, "source": f"https://x.test/{i}"}
            for i, (c, b) in enumerate(zip(CANDIDATES, bases))]


def _write(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def bed(tmp_path):
    """A durable_root the driver can self-anchor into, plus a session dir.

    `scripts/` holds the real driver and CONTRACT-SHAPED stubs for the three
    programs it shells: the companion resolver, canon_validate.py and
    fetch_citation.py. Each stub records its argv so a test can assert what the
    driver actually asked for."""
    durable = tmp_path / "durable"
    scripts = durable / "scripts"
    run_dir = durable / "glossary" / "runs" / "run1"
    run_dir.mkdir(parents=True)
    scripts.mkdir(parents=True)
    shutil.copy2(DRIVER, scripts / "glossary_dispatch_driver.py")
    # json_stdout.py is the driver's one hard sibling dependency: it is loaded
    # by exact path at import time and the driver exits without it, exactly as a
    # deployed copy does. Staging it keeps this fixture a real scripts/ dir.
    shutil.copy2(JSON_STDOUT, scripts / "json_stdout.py")

    calls = tmp_path / "calls.log"

    # The resolver stub parses the REAL script's required arguments. This is the
    # assertion that the shipped blocker would have failed: a driver calling it
    # bare exits non-zero here exactly as the real resolver does.
    _write(scripts / "resolve_codex_companion.py", f'''
        import argparse, json, sys
        open({str(calls)!r}, "a").write("resolver " + " ".join(sys.argv[1:]) + "\\n")
        p = argparse.ArgumentParser()
        p.add_argument("--durable-root", required=True)
        p.add_argument("--node", default="node")
        p.add_argument("--search-glob", action="append")
        p.add_argument("--timeout-sec", type=int)
        p.parse_args()
        print(json.dumps({{"companion_path": {str(tmp_path / "companion.mjs")!r}}}))
    ''')
    # The companion stub stands in for the codex turn: it reads the prompt it was
    # given, finds the run-scoped .json path that prompt tells the agent to write,
    # and writes there. Faithful in the two ways that matter:
    #
    #  - the artifact appears DURING the driver's poll, not before it, so the
    #    driver's own stale-artifact unlink is exercised rather than defeated; and
    #  - an ORDINARY DISPATCH writes a whole-batch decision UNCONDITIONALLY, which
    #    is exactly what the real prompt orders ("decide every candidate", one
    #    atomic write to that path). An earlier version of this stub wrote only
    #    what a sidecar plan listed, so a dispatch nobody wanted quietly wrote
    #    NOTHING -- and a driver that dispatched over a freshly repaired rung
    #    looked identical to one that did not.
    #
    # The sidecar plan overrides a specific artifact by filename; a repair has no
    # default, because a repair turn that never writes is a real case.
    planted = tmp_path / "planted.json"
    planted.write_text(json.dumps({}))
    _write(tmp_path / "companion.mjs", f'''
        import fs from "node:fs";
        const args = process.argv.slice(2);
        const pf = args[args.indexOf("--prompt-file") + 1];
        const prompt = fs.readFileSync(pf, "utf8");
        const plan = JSON.parse(fs.readFileSync({str(planted)!r}, "utf8"));
        const fresh = {json.dumps(_default_rows())};
        const paths = prompt.match(/\\S+\\/(?:out|repair)_\\d+_attempt_\\d+\\.json/g) || [];
        for (const target of new Set(paths)) {{
          const key = target.split("/").pop();
          fs.appendFileSync({str(calls)!r}, "companion " + key + "\\n");
          const rows = plan[key] || (key.startsWith("out_") ? fresh : null);
          if (rows) fs.writeFileSync(target, JSON.stringify(rows));
        }}
        console.log(JSON.stringify({{ok: true}}));
    ''')

    # canon_validate stub: --check-batch succeeds once the fragment exists;
    # --approve-to copies it; --record-approval-to writes a record; merge/verify
    # succeed. Argument ORDER is asserted, not merely accepted.
    _write(scripts / "canon_validate.py", f'''
        import json, shutil, sys, pathlib
        argv = sys.argv[1:]
        open({str(calls)!r}, "a").write("canon " + " ".join(argv) + "\\n")
        if "--check-batch" in argv:
            frag = pathlib.Path(argv[argv.index("--check-batch") + 1])
            assert argv.index("--research-mode") < argv.index("--expect-source-forms-file"), \\
                "flag order changed"
            if not frag.exists():
                print(json.dumps({{"success": False}})); sys.exit(1)
            if "--approve-to" in argv:
                shutil.copy2(frag, argv[argv.index("--approve-to") + 1])
            if "--record-approval-to" in argv:
                pathlib.Path(argv[argv.index("--record-approval-to") + 1]).write_text(
                    json.dumps({{"approved": True}}))
            print(json.dumps({{"success": True}})); sys.exit(0)
        if "--merge-batches" in argv:
            assert "--plugin-root" in argv, "the merge must carry --plugin-root (#412)"
            print(json.dumps({{"merged": True}})); sys.exit(0)
        if "--verify-merged" in argv:
            assert "--plugin-root" not in argv, "verify must NOT carry --plugin-root"
            print(json.dumps({{"verified": True}})); sys.exit(0)
        sys.exit(2)
    ''')

    # fetch_citation stub: writes an index.json whose outcomes a test controls
    # through a sidecar file, so one bed drives the fetched / 404 / budget cases.
    outcomes = tmp_path / "outcomes.json"
    outcomes.write_text(json.dumps(["fetched", "fetched"]))
    _write(scripts / "fetch_citation.py", f'''
        import json, sys, pathlib
        argv = sys.argv[1:]
        open({str(calls)!r}, "a").write("fetch " + " ".join(argv) + "\\n")
        out = pathlib.Path(argv[argv.index("--out-dir") + 1]); out.mkdir(parents=True, exist_ok=True)
        planned = json.loads(pathlib.Path({str(outcomes)!r}).read_text())
        entries = [{{"item_index": i, "outcome": o, "source": "https://x.test/" + str(i),
                     "source_form": "F" + str(i), "final_origin": "https://x.test",
                     "chain": [], "content_type": "text/html", "bytes": 10,
                     "evidence_file": "ev_%03d.txt" % i}} for i, o in enumerate(planned)]
        (out / "index.json").write_text(json.dumps({{"entries": entries, "counts": {{}}}}))
        print(json.dumps({{"success": True, "n_sources": len(entries)}}))
    ''')

    return {"tmp": tmp_path, "durable": durable, "scripts": scripts,
            "run_dir": run_dir, "session": tmp_path / "session",
            "calls": calls, "outcomes": outcomes, "planted": planted}


def load(bed):
    spec = importlib.util.spec_from_file_location(
        f"gdd_seq{abs(hash(str(bed['scripts'])))}",
        bed["scripts"] / "glossary_dispatch_driver.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_driver(bed, *extra, expect=0):
    """Invokes the driver as a subprocess, exactly as an operator does, and
    returns its one stdout JSON line."""
    batches_file = bed["tmp"] / "batches.json"
    batches_file.write_text(json.dumps(BATCHES))
    argv = [sys.executable, str(bed["scripts"] / "glossary_dispatch_driver.py"),
            "--run-id", "run1", "--batches-file", str(batches_file),
            "--verdict-dir", str(bed["session"]),
            "--plugin-root", str(SKILL_ROOT),
            "--source-lang", "he", "--target-lang", "en",
            "--research-mode", "live", "--effort", "high",
            "--citation-content-types", "text/html",
            "--poll-sec", "0.05", "--deadline-sec", "6",
            "--node", NODE, *extra]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    assert proc.returncode == expect, (
        f"exit {proc.returncode}, expected {expect}\nSTDOUT:\n{proc.stdout[-2000:]}\nSTDERR:\n{proc.stderr[-2000:]}")
    line = [l for l in proc.stdout.strip().splitlines() if l.startswith("{")]
    assert len(line) == 1, f"expected exactly one JSON line, got {proc.stdout!r}"
    return json.loads(line[0]), proc


def plant_fragment(bed, attempt=0, bases=("established", "established")):
    """Writes a fragment WITHOUT a dispatch, for the cases that need one to
    pre-exist (a resumed attempt 0, a wiped-artifact resume). Ordinary dispatch
    paths must NOT call this: the companion stub writes the same rows, and
    planting them first hides whether the dispatch happened at all."""
    rows = _default_rows(bases)
    (bed["run_dir"] / f"out_0_attempt_{attempt}.json").write_text(json.dumps(rows))
    return rows


def companion_targets(bed):
    """Every artifact the fake codex turn was asked to write, in order. This is
    the record of which DISPATCHES actually happened."""
    return [l.split(" ", 1)[1] for l in bed["calls"].read_text().splitlines()
            if l.startswith("companion ")]


# ---------------------------------------------------------------------------
# The blocker this file exists for
# ---------------------------------------------------------------------------

def test_the_companion_resolver_is_called_with_its_required_arguments(bed):
    """THE REGRESSION TEST FOR THE SHIPPED BLOCKER. The driver called the resolver
    bare; the real script has `--durable-root` as `required=True`, so every
    dispatch died on an argparse error before codex was ever reached. Nothing in
    the unit suites touched this path."""
    run_driver(bed)
    resolver_calls = [l for l in bed["calls"].read_text().splitlines()
                      if l.startswith("resolver ")]
    assert resolver_calls, "the resolver was never invoked"
    assert "--durable-root" in resolver_calls[0], (
        "the driver must pass --durable-root; the shipped resolver requires it "
        "and exits 2 without it, killing every dispatch")
    assert "--node" in resolver_calls[0]


def test_the_real_resolver_would_refuse_a_bare_invocation(bed):
    """Pins the premise the test above rests on, against the SHIPPED resolver
    rather than the stub -- so this stays honest if the real CLI ever changes."""
    proc = subprocess.run([sys.executable, str(RESOLVER)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "--durable-root" in proc.stderr


# ---------------------------------------------------------------------------
# The full sequence
# ---------------------------------------------------------------------------

def test_drive_hands_back_one_judge_then_a_verdict_merges(bed):
    out, _ = run_driver(bed)
    assert out["needs_judge"], "a live batch must hand back for a judge"
    entry = out["needs_judge"][0]
    assert entry["agentType"] == "literary-translator:citation-judge"
    assert entry["batch"] == 0 and entry["attempt"] == 0
    assert out["merged"] is False, "nothing may merge before a verdict"

    verdicts = bed["session"] / "v.json"
    verdicts.write_text(json.dumps([{
        "batch": 0, "attempt": 0, "nonce": entry["nonce"],
        "reply": "audited every citation.\nCITATIONS_OK 0 ATTEMPT 0"}]))
    out2, _ = run_driver(bed, "--record-verdicts", str(verdicts))
    assert out2["recorded"][0]["approved"] is True
    assert out2["merged"] is True, f"expected a merge, got {out2}"
    assert (bed["run_dir"] / "approval_0_attempt_0.json").exists()


def test_a_rejection_advances_the_ladder_instead_of_stranding_the_batch(bed):
    """The shipped three-attempt ladder must actually run. Recording a rejection
    as a note and stopping left the batch neither ready nor awaiting a judge, so
    attempt 1 never happened."""
    out, _ = run_driver(bed)
    entry = out["needs_judge"][0]

    verdicts = bed["session"] / "v.json"
    verdicts.write_text(json.dumps([{
        "batch": 0, "attempt": 0, "nonce": entry["nonce"],
        "reply": "source 1 does not attest the form.\nCITATIONS_REJECTED 0 ATTEMPT 0"}]))
    out2, _ = run_driver(bed, "--record-verdicts", str(verdicts))

    assert out2["recorded"][0]["approved"] is False
    assert out2["needs_judge"], (
        "a rejection must advance to the next rung and hand back a NEW judge "
        "prompt; recording it and stopping strands the batch")
    assert out2["needs_judge"][0]["attempt"] == 1
    assert out2["needs_judge"][0]["nonce"] != entry["nonce"], (
        "the next attempt must mint a fresh PREPARE nonce")
    assert out2["merged"] is False


def test_a_verdict_cannot_be_replayed_after_it_is_consumed(bed):
    out, _ = run_driver(bed)
    entry = out["needs_judge"][0]
    verdicts = bed["session"] / "v.json"
    verdicts.write_text(json.dumps([{
        "batch": 0, "attempt": 0, "nonce": entry["nonce"],
        "reply": "ok\nCITATIONS_OK 0 ATTEMPT 0"}]))
    run_driver(bed, "--record-verdicts", str(verdicts))
    out3, _ = run_driver(bed, "--record-verdicts", str(verdicts), expect=1)
    assert out3["refused"], "a consumed verdict must be refused on replay"


def test_a_verdict_for_bytes_that_changed_is_refused(bed):
    """The snapshot digest is re-hashed immediately before the approval record is
    written, so a verdict cannot be carried onto bytes it never named."""
    out, _ = run_driver(bed)
    entry = out["needs_judge"][0]
    snap = bed["run_dir"] / "approved_0_attempt_0.json"
    snap.write_text(json.dumps([{"source_form": "Alpha", "basis": "transliterated",
                                 "disposition": "accepted"}]))
    verdicts = bed["session"] / "v.json"
    verdicts.write_text(json.dumps([{
        "batch": 0, "attempt": 0, "nonce": entry["nonce"],
        "reply": "ok\nCITATIONS_OK 0 ATTEMPT 0"}]))
    out2, _ = run_driver(bed, "--record-verdicts", str(verdicts), expect=1)
    assert out2["refused"]
    assert not (bed["run_dir"] / "approval_0_attempt_0.json").exists(), (
        "no approval record may be written for a snapshot whose bytes moved")
    assert out2["merged"] is False


def test_state_from_another_run_is_discarded_not_merged(bed):
    """A reused verdict directory must behave like a fresh one. Carrying a
    previous run's readiness forward is how an approval from run A satisfies run
    B's merge admission."""
    out, _ = run_driver(bed)
    entry = out["needs_judge"][0]
    verdicts = bed["session"] / "v.json"
    verdicts.write_text(json.dumps([{
        "batch": 0, "attempt": 0, "nonce": entry["nonce"],
        "reply": "ok\nCITATIONS_OK 0 ATTEMPT 0"}]))
    run_driver(bed, "--record-verdicts", str(verdicts))

    m = load(bed)
    state = m.read_pending(bed["session"])
    assert state["batches"]["0"]["status"] == "ready"
    # Same directory, a DIFFERENT run id.
    reloaded = m.load_state(bed["session"], bed["durable"], "run2")
    assert reloaded["batches"] == {}, (
        "state from another run must be discarded, never carried into this one")
    assert reloaded["run_id"] == "run2"


# ---------------------------------------------------------------------------
# The repair path, end to end
# ---------------------------------------------------------------------------

def test_a_shared_budget_outcome_spends_no_judge(bed):
    """fetch_citation.py exits 0 on a soft budget failure, so without an explicit
    branch the batch would fall through and spend a judge on an environment
    fault — whose rejection would then be misread as a content rejection."""
    bed["outcomes"].write_text(json.dumps(["refused:batch-byte-budget", "fetched"]))
    out, _ = run_driver(bed, expect=1)
    assert out["needs_judge"] == [], "no judge may be spent on a budget failure"
    assert out["not_ready"], "the batch must be reported, not silently dropped"


def test_a_retrieval_failure_repairs_before_any_judge_runs(bed):
    """The repair is a PRE-judge step: once a judge is dispatched at all, every
    established citation in the batch has retrieved."""
    bed["outcomes"].write_text(json.dumps(["http_error:404", "fetched"]))

    # What the repair TURN produces, handed to the companion stub so the artifact
    # appears while the driver polls -- after its own stale-artifact unlink, the
    # way a real codex turn produces it.
    bed["planted"].write_text(json.dumps({"repair_0_attempt_0.json": [{
        "source_form": "Alpha", "basis": "transliterated", "disposition": "accepted",
        "canonical_target_form": "Alpha", "confidence": "high",
        "is_proper_name": True}]}))
    out, _ = run_driver(bed)
    fetches = [l for l in bed["calls"].read_text().splitlines() if l.startswith("fetch ")]
    assert len(fetches) >= 2, "a repaired fragment must be re-fetched before judging"
    assert out["needs_judge"], "after a successful repair the batch reaches a judge"
    assert out["needs_judge"][0]["attempt"] == 1, "the repair reserves exactly one rung"
    assert companion_targets(bed) == ["out_0_attempt_0.json",
                                      "repair_0_attempt_0.json"], (
        "a valid repair POPULATES the reserved rung. Handing that rung back to "
        "ordinary dispatch launches a whole-batch job whose prompt orders the "
        "agent to decide every candidate and atomically write this same path -- "
        "the untouched rows silently re-decided, and which write the next APPROVE "
        "snapshots decided by scheduling")
    spliced = json.loads((bed["run_dir"] / "out_0_attempt_1.json").read_text())
    assert [r["source_form"] for r in spliced] == ["Alpha", "Beta"], (
        "the splice must preserve the snapshot's row order and full coverage")
    assert spliced[0]["basis"] == "transliterated", "the repaired row landed"
    assert spliced[1]["source"] == "https://x.test/1", "an untouched row is untouched"


def test_the_ladder_bound_comes_from_the_template(bed):
    m = load(bed)
    text = (SKILL_ROOT / "assets" / "templates" / "glossary-pass-wf.template.js").read_text()
    assert m.template_max_citation_retries(text) == 2
    with pytest.raises(SystemExit):
        m.template_max_citation_retries("const MAX_CITATION_RETRIES = nope\n")


def test_a_verdict_file_outside_the_verdict_dir_is_refused(bed):
    out, _ = run_driver(bed)
    entry = out["needs_judge"][0]
    stray = bed["durable"] / "glossary" / "runs" / "run1" / "v.json"
    stray.write_text(json.dumps([{"batch": 0, "attempt": 0,
                                  "nonce": entry["nonce"],
                                  "reply": "ok\nCITATIONS_OK 0 ATTEMPT 0"}]))
    proc = subprocess.run(
        [sys.executable, str(bed["scripts"] / "glossary_dispatch_driver.py"),
         "--run-id", "run1", "--batches-file", str(bed["tmp"] / "batches.json"),
         "--verdict-dir", str(bed["session"]), "--plugin-root", str(SKILL_ROOT),
         "--node", NODE, "--record-verdicts", str(stray)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2
    assert not (bed["run_dir"] / "approval_0_attempt_0.json").exists()



# ---------------------------------------------------------------------------
# A resume reuses the RUN_ID -- so the state document outlives the artifacts
# ---------------------------------------------------------------------------

def _wipe_as_resume_setup_does(bed):
    """What resume_setup.py does to this run's glossary directory on a MATCHING
    resume: every approved snapshot, every approval record and every evidence
    directory goes; out_{i}_attempt_0.json stays. The state document lives in the
    SESSION directory, which that script never touches -- which is the whole
    problem this pair of tests covers."""
    for entry in bed["run_dir"].iterdir():
        if entry.name.startswith(("approved_", "approval_", "evidence_")):
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()


def test_an_awaiting_judge_status_outliving_its_snapshot_is_reset_not_wedged(bed):
    """A resume reuses the same RUN_ID, so the state document is KEPT while
    resume_setup.py deletes the snapshot the judge was handed. Nothing then
    transitions that batch: its old verdict is refused forever because the
    snapshot is unreadable, and drive_all skips it because it reads as awaiting
    one. The batch is wedged with no operator move that frees it."""
    out, _ = run_driver(bed)
    entry = out["needs_judge"][0]
    assert (bed["run_dir"] / "approved_0_attempt_0.json").exists()

    _wipe_as_resume_setup_does(bed)

    verdicts = bed["session"] / "v.json"
    verdicts.write_text(json.dumps([{
        "batch": 0, "attempt": 0, "nonce": entry["nonce"],
        "reply": "ok\nCITATIONS_OK 0 ATTEMPT 0"}]))
    out2, _ = run_driver(bed, "--record-verdicts", str(verdicts), expect=1)

    assert out2["reset"], "the stale awaiting status must be reported, not silently kept"
    assert out2["reset"][0]["was"] == "awaiting_judge"
    assert out2["refused"], "a verdict for a snapshot that is gone cannot be honoured"
    assert out2["needs_judge"], (
        "the batch must be re-prepared in this same invocation; leaving it "
        "awaiting a verdict nobody can produce wedges it permanently")
    assert out2["needs_judge"][0]["nonce"] != entry["nonce"], (
        "the fresh PREPARE must mint a fresh nonce")
    assert not (bed["run_dir"] / "approval_0_attempt_0.json").exists(), (
        "no approval may be recorded against the wiped snapshot")


def _plant_ready_state(bed, *, keep_merge_path: bool, keep_record: bool):
    """Writes the state document an interrupted run leaves behind: batch 0 READY,
    naming a merge fragment and an approval record. Which of the two survives is
    the parameter, because each is a SEPARATE reconciliation condition and the
    first one checked would otherwise mask the second."""
    m = load(bed)
    d = m.resolve_verdict_dir(str(bed["session"]), bed["durable"])
    merge_path = bed["run_dir"] / "approved_0_attempt_0.json"
    record_path = bed["run_dir"] / "approval_0_attempt_0.json"
    if keep_merge_path:
        merge_path.write_text(json.dumps(_default_rows()))
    if keep_record:
        record_path.write_text(json.dumps({"approved": True}))
    state = m.fresh_state(bed["durable"], "run1")
    state["batches"]["0"] = {
        "attempt": 0, "status": "ready", "citationReview": "approved",
        "mergePath": str(merge_path), "approvalRecordPath": str(record_path),
        "approvalRecorded": True}
    m.save_state(d, state)


def test_a_ready_status_outliving_its_approval_record_is_reset_not_merged(bed):
    """The same wedge on the other skipped status, and the worse half: a `ready`
    entry carries the mergePath and the approval record the merge will name, so
    keeping it after those files are gone points the one irreversible write in the
    pass at paths that no longer exist.

    The merge fragment is PRESENT here and only the record is gone -- otherwise
    the missing-fragment condition, which is checked first, would satisfy this
    test on its own and the record check could be deleted without a red."""
    _plant_ready_state(bed, keep_merge_path=True, keep_record=False)
    out, _ = run_driver(bed)
    assert out["reset"] and out["reset"][0]["was"] == "ready"
    assert "approval record" in out["reset"][0]["reason"], (
        f"the record, not the fragment, is what is missing: {out['reset'][0]}")
    assert out["merged"] is False, (
        "#723's record is what admits a batch to the merge; without it nobody can "
        "reconstruct what was approved")
    assert out["needs_judge"], "the reset batch is re-driven from attempt 0"


def test_a_ready_status_outliving_its_merge_fragment_is_reset_not_merged(bed):
    """The other half of the same condition: the record survived, the bytes it
    approves did not."""
    _plant_ready_state(bed, keep_merge_path=False, keep_record=True)
    out, _ = run_driver(bed)
    assert out["reset"] and out["reset"][0]["was"] == "ready"
    assert "merge" in out["reset"][0]["reason"]
    assert out["merged"] is False
    assert out["needs_judge"]


# ---------------------------------------------------------------------------
# The CLI boundary
# ---------------------------------------------------------------------------

def test_a_resumed_index_list_that_is_not_a_list_of_ints_is_refused(bed):
    """`set()` takes any iterable, so the JSON string "0" becomes {"0"} -- which
    never equals the integer index 0. Batch 0 would then be treated as NOT
    resumed and redispatched, overwriting the attempt-0 fragment resume_setup.py
    deliberately kept and already re-checked. The value still reaches the template
    as a well-formed array, so its own guard never sees it."""
    plant_fragment(bed)
    before = (bed["run_dir"] / "out_0_attempt_0.json").read_bytes()
    for bad in ('"0"', '0', '{"0": true}', 'null', '[true]', '["0"]'):
        batches_file = bed["tmp"] / "batches.json"
        batches_file.write_text(json.dumps(BATCHES))
        proc = subprocess.run(
            [sys.executable, str(bed["scripts"] / "glossary_dispatch_driver.py"),
             "--run-id", "run1", "--batches-file", str(batches_file),
             "--verdict-dir", str(bed["session"]), "--plugin-root", str(SKILL_ROOT),
             "--node", NODE, "--resumed-batch-indices", bad],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 2, f"{bad!r} was accepted"
    assert (bed["run_dir"] / "out_0_attempt_0.json").read_bytes() == before, (
        "a refused invocation must not have dispatched anything")


# ---------------------------------------------------------------------------
# The ladder's far end
# ---------------------------------------------------------------------------

def test_a_rejection_at_the_final_rung_exhausts_at_that_rung(bed):
    """The ladder is 0..MAX_CITATION_RETRIES. Incrementing past it on the last
    rejection persists and REPORTS an attempt that never ran as the one that
    exhausted -- the durable state then contradicts the contract it enforces."""
    out, _ = run_driver(bed)
    verdicts = bed["session"] / "v.json"
    for attempt in (0, 1, 2):
        entry = out["needs_judge"][0]
        assert entry["attempt"] == attempt
        verdicts.write_text(json.dumps([{
            "batch": 0, "attempt": attempt, "nonce": entry["nonce"],
            "reply": f"source 1 is not attested.\nCITATIONS_REJECTED 0 ATTEMPT {attempt}"}]))
        out, _ = run_driver(bed, "--record-verdicts", str(verdicts),
                            expect=1 if attempt == 2 else 0)

    assert out["needs_judge"] == [], "the ladder is exhausted; no rung 3 exists"
    assert out["maxCitationRetries"] == 2
    failed = out["not_ready"][0]
    assert failed["attempt"] == 2, (
        f"the exhausting rung is the last one that ran, not one past it; got "
        f"{failed['attempt']}")
    assert failed["attemptsUsed"] == 3
    assert failed["reason"] == "citation-review-exhausted"
    assert out["merged"] is False
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
