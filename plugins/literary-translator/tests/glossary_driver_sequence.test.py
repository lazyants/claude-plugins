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
        const cwd = args[args.indexOf("--cwd") + 1];
        fs.appendFileSync({str(calls)!r}, "cwd " + cwd + "\\n");
        const prompt = fs.readFileSync(pf, "utf8");
        const plan = JSON.parse(fs.readFileSync({str(planted)!r}, "utf8"));
        const fresh = {json.dumps(_default_rows())};
        // #806: the artifact goes inside the sandbox this job was launched with,
        // so the target is --cwd plus the basename the prompt names. Matching the
        // WHOLE path out of the prompt would be wrong twice over: the self-check
        // command names it single-quoted, and a sandbox path holding a space
        // (a TMPDIR the operator chose) has no unambiguous regex at all.
        const names = prompt.match(/(?:out|repair)_\\d+_attempt_\\d+\\.json/g) || [];
        for (const key of new Set(names)) {{
          const target = cwd + "/" + key;
          // CONTRACT-SHAPED, like every other stub here: a real codex turn writes
          // where the PROMPT tells it to, and it can only write inside its own
          // sandbox. If those two are not the same directory the driver has
          // dispatched a job that cannot produce its artifact, and this stub must
          // say so rather than quietly writing somewhere the driver never polls.
          if (!prompt.includes(target)) {{
            throw new Error("the prompt does not name " + target +
              " -- the sandbox --cwd and the prompt's out-path disagree");
          }}
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


def run_driver_raw(bed, *extra, env=None):
    """The driver as a subprocess, WITHOUT run_driver's exit-code and
    one-JSON-line assertions. An environment fault exits 2 having emitted no
    hand-back at all, which is the property under test -- run_driver would fail
    on the missing line first and say nothing about why.

    ONE argv, built here and nowhere else: a second copy of this command line
    would let the ordinary path and the environment-fault path drift into
    invoking two different drivers, and the fault path's whole claim is that it
    is the SAME invocation an operator makes."""
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
    return subprocess.run(argv, capture_output=True, text=True, timeout=180,
                          env=({**os.environ, **env} if env else None))


def run_driver(bed, *extra, expect=0, env=None):
    """Invokes the driver as a subprocess, exactly as an operator does, and
    returns its one stdout JSON line."""
    proc = run_driver_raw(bed, *extra, env=env)
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


def _logged(bed, kind):
    """The values of one KIND of line out of the shared stub log, in call order.
    Every fake appends `<kind> <value>`, so a reader of one kind is a prefix and
    nothing else -- and reading it in one place keeps two readers of the same log
    from drifting into two different notions of where a value starts."""
    prefix = kind + " "
    return [line[len(prefix):] for line in bed["calls"].read_text().splitlines()
            if line.startswith(prefix)]


def companion_cwds(bed):
    """Every --cwd the fake codex turn was launched with, in order. This is the
    record of where each dispatched job was actually confined -- read from argv,
    never from the driver's source."""
    return _logged(bed, "cwd")


def companion_targets(bed):
    """Every artifact the fake codex turn was asked to write, in order. This is
    the record of which DISPATCHES actually happened."""
    return _logged(bed, "companion")


def _has_enclosing_repo(path) -> bool:
    """codex-companion's OWN workspace-root algorithm, run here rather than
    restated: `git rev-parse --show-toplevel` walking up from `path`."""
    proc = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, timeout=30)
    return proc.returncode == 0


# Gate stand-ins for the publish unit tests: publish_fragment runs whatever command
# string it is handed through the driver's own shlex-based runner, so a real
# canon_validate.py is not needed to pin WHERE the gate sits in the sequence.
_PASSING_GATE = f"{sys.executable} -c pass"
_FAILING_GATE = f"{sys.executable} -c raise(SystemExit(1))"


@pytest.fixture
def publish_bed(bed, tmp_path):
    """The fixed cast every publish_fragment unit test needs, so each test spells
    only the bytes it is actually about.

    The sandbox is where the job's artifact lives and the only place the job may
    write; `target` is the canonical RUN_DIR path a resume reads; `staging` is the
    driver's own private name beside that target, spelled with a fixed token here
    because these tests call publish_fragment directly and must know the name to
    assert on it."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    return {"mod": load(bed), "sandbox": sandbox,
            "target": bed["run_dir"] / "out_0_attempt_0.json",
            "staging": bed["run_dir"] / ".publish_0_attempt_0_deadbeef.json"}


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
    # appears while the driver polls, the way a real codex turn produces it --
    # inside that dispatch's own sandbox, which #806 made the only place it lives.
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
def test_a_refused_final_gate_exits_non_zero(bed):
    """The CLI contract says 1 = a gate refused. The merge and the disk-independent
    verify ARE gates, and they are the last two -- so the run that most needs a
    non-zero status is exactly this one: every batch approved, every verdict
    consumed, and the one irreversible write refused. Reporting that as exit 0
    lets shell-level orchestration read a failed final gate as success."""
    out, _ = run_driver(bed)
    entry = out["needs_judge"][0]
    verdicts = bed["session"] / "v.json"
    verdicts.write_text(json.dumps([{
        "batch": 0, "attempt": 0, "nonce": entry["nonce"],
        "reply": "ok\nCITATIONS_OK 0 ATTEMPT 0"}]))
    # Make --merge-batches refuse, leaving every other stub behaviour as it was.
    # The command the driver issues is unchanged; only the gate's answer is.
    stub = bed["scripts"] / "canon_validate.py"
    stub.write_text(stub.read_text().replace(
        'print(json.dumps({"merged": True})); sys.exit(0)',
        'print(json.dumps({"merged": False, "error": "refused"})); sys.exit(1)'),
        encoding="utf-8")
    out2, _ = run_driver(bed, "--record-verdicts", str(verdicts), expect=1)
    assert out2["merged"] is False
    assert out2["reason"] == "merge-failed"
    assert out2["not_ready"] == [], (
        "no BATCH failed -- this exit status must come from the gate itself, "
        "otherwise the test would pass for the wrong reason")
    assert out2["refused"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# #806 -- WHAT A DISPATCHED JOB MAY WRITE
#
# Every assertion below reads the cwd the companion was actually LAUNCHED with,
# and re-runs codex-companion's own workspace-root algorithm against it. Reading
# the driver's source instead would prove only that a string was passed; it is
# the companion's resolution, not ours, that decides the boundary.
# ---------------------------------------------------------------------------

def test_a_dispatched_job_is_confined_outside_the_durable_root(bed):
    out, _ = run_driver(bed)
    assert out["needs_judge"], "the batch must have reached a judge"
    cwds = companion_cwds(bed)
    assert cwds, "no codex job was launched at all -- nothing was proved"
    for cwd in cwds:
        assert not Path(cwd).is_relative_to(bed["durable"]), (
            f"the job was launched inside the durable root at {cwd}; every file "
            f"under it, including scripts/ and this driver's deployed copy, "
            f"would be model-writable")
        assert not _has_enclosing_repo(cwd), (
            f"{cwd} has an enclosing git repository, so codex-companion would "
            f"resolve its workspace-write root to that repo instead -- the "
            f"sandbox would be a name, not a boundary")
    # ...and the artifact still reaches its canonical path, published by the
    # driver rather than written there by the job.
    assert (bed["run_dir"] / "out_0_attempt_0.json").exists(), (
        "the gated sandbox bytes must be published into RUN_DIR")


def test_the_repair_dispatch_is_confined_too(bed):
    """The repair has its OWN launch_codex call site. A one-sided assertion on
    the ordinary dispatch would leave it unpinned."""
    bed["outcomes"].write_text(json.dumps(["http_error:404", "fetched"]))
    bed["planted"].write_text(json.dumps({"repair_0_attempt_0.json": [{
        "source_form": "Alpha", "basis": "transliterated", "disposition": "accepted",
        "canonical_target_form": "Alpha", "confidence": "high",
        "is_proper_name": True}]}))
    run_driver(bed)
    assert companion_targets(bed) == ["out_0_attempt_0.json",
                                      "repair_0_attempt_0.json"], (
        "this test is only meaningful if a repair actually dispatched")
    cwds = companion_cwds(bed)
    assert len(cwds) == 2, f"expected a dispatch and a repair launch, got {cwds}"
    assert cwds[0] != cwds[1], "each launch gets its own single-use sandbox"
    for cwd in cwds:
        assert not Path(cwd).is_relative_to(bed["durable"])
        assert not _has_enclosing_repo(cwd)


def test_confinement_holds_when_the_durable_root_is_inside_a_git_repository(bed):
    """The case that rules out the cheaper fix.

    Pointing --cwd at RUN_DIR would confine nothing here: RUN_DIR is nested under
    durable_root, and codex-companion resolves its write root by walking UP to
    the enclosing git top level, so it would land on this repository and hand the
    job every file in it. durable_root coinciding with a project's own root is an
    EXPLICITLY SUPPORTED layout (SKILL.md, Step 0a), so the boundary has to hold
    here or it does not exist."""
    subprocess.run(["git", "init", "-q", str(bed["durable"])], check=True, timeout=60)
    assert _has_enclosing_repo(bed["run_dir"]), (
        "fixture precondition: RUN_DIR must now resolve to an enclosing repo, "
        "which is exactly what makes the RUN_DIR-as-cwd design a no-op")

    out, _ = run_driver(bed)
    assert out["needs_judge"], "the supported layout must still drive normally"
    cwds = companion_cwds(bed)
    assert cwds, "no codex job was launched at all -- nothing was proved"
    for cwd in cwds:
        assert not _has_enclosing_repo(cwd), (
            f"{cwd} resolves to an enclosing repository even though the sandbox "
            f"is meant to sit outside every working tree")


def test_an_unconfined_sandbox_refuses_to_dispatch_rather_than_warning(bed, tmp_path):
    """Strictness bias, and it is free HERE specifically: a TMPDIR inside a git
    working tree is pathological, so refusing costs no sanctioned layout -- while
    dispatching into it would hand the job write access to that whole repository
    and report that it had been confined."""
    bad_tmp = tmp_path / "tmp_in_repo"
    bad_tmp.mkdir()
    subprocess.run(["git", "init", "-q", str(bad_tmp)], check=True, timeout=60)

    proc = run_driver_raw(bed, env={"TMPDIR": str(bad_tmp)})
    assert proc.returncode == 2, (
        f"an unconfined sandbox is an ENVIRONMENT fault, not a verdict about this "
        f"batch -- exit 2, not {proc.returncode}\n{proc.stderr[-1500:]}")
    assert companion_cwds(bed) == [], "nothing may be dispatched into an unconfined sandbox"
    assert companion_targets(bed) == [], "no codex turn may have run"
    assert "write-confined" in proc.stderr, f"the refusal must say why: {proc.stderr[-800:]}"


def test_a_corrected_tmpdir_resumes_instead_of_finding_the_batch_wedged(bed, tmp_path):
    """The reason the refusal exits 2 rather than failing the batch.

    A DriverError here would reach drive_all(), which records status="failed" --
    one of the two statuses the NEXT invocation skips. The operator would fix
    TMPDIR, re-run exactly as documented, and find the batch skipped forever, a
    recoverable environment fault turned into a run only deleting authorization
    state can clear. So the first invocation must write no state at all."""
    bad_tmp = tmp_path / "tmp_in_repo2"
    bad_tmp.mkdir()
    subprocess.run(["git", "init", "-q", str(bad_tmp)], check=True, timeout=60)

    first = run_driver_raw(bed, env={"TMPDIR": str(bad_tmp)})
    # Deliberately NOT `== 2` -- that contract belongs to the test above, and
    # asserting it here would stop this test at the first invocation under exactly
    # the regression it exists to catch (a refusal that fails the BATCH exits 1),
    # leaving the wedge below untested. Only "the first invocation did not
    # succeed" is a precondition for what follows.
    assert first.returncode != 0, f"the bad-TMPDIR run should not have succeeded\n{first.stderr[-800:]}"

    # Same run id, same verdict directory, corrected TMPDIR -- the documented
    # re-run after fixing the environment.
    out, _ = run_driver(bed)
    assert out["needs_judge"], (
        f"the corrected re-run must dispatch the batch, not skip it as failed: {out}")
    assert companion_targets(bed) == ["out_0_attempt_0.json"], (
        "and it must be an ORDINARY first dispatch, not a resumed rung")


def test_a_tmpdir_holding_a_space_still_dispatches(bed, tmp_path):
    """TMPDIR belongs to the operator, not to this pipeline. The sandbox path is
    spliced into the --check-batch command the job is told to run and the driver
    polls with, so an unquoted path holding a space would be split into two argv
    entries by BOTH consumers -- and the batch would time out as
    glossary-pass-null with nothing to say why."""
    spaced = tmp_path / "tmp with space"
    spaced.mkdir()
    out, _ = run_driver(bed, env={"TMPDIR": str(spaced)})
    assert out["needs_judge"], f"a whitespace-bearing TMPDIR must still drive: {out}"
    cwds = companion_cwds(bed)
    assert cwds and " " in cwds[0], (
        f"fixture precondition: the sandbox must actually sit under the "
        f"whitespace-bearing TMPDIR, got {cwds}")
    assert (bed["run_dir"] / "out_0_attempt_0.json").exists()


def test_a_symlinked_sandbox_artifact_is_never_published(publish_bed, tmp_path):
    """A confined job can still WRITE A SYMLINK inside its own sandbox: write
    confinement restricts where writes LAND, never what a link's target names."""
    mod, target, staging = (publish_bed["mod"], publish_bed["target"],
                            publish_bed["staging"])
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps([{"source_form": "smuggled"}]))
    link = publish_bed["sandbox"] / "out_0_attempt_0.json"
    link.symlink_to(elsewhere)

    with pytest.raises(mod.DriverError):
        mod.publish_fragment(link, target, staging, _PASSING_GATE, "batch 0 attempt 0")
    assert not target.exists(), "nothing may be published from a symlinked artifact"
    assert not staging.exists(), "the staging copy must not survive a refusal"


def test_a_published_fragment_is_the_bytes_that_passed_the_gate(publish_bed):
    mod, target, staging = (publish_bed["mod"], publish_bed["target"],
                            publish_bed["staging"])
    src = publish_bed["sandbox"] / "out_0_attempt_0.json"
    # Deliberately NOT this file's own json.dumps formatting: a publish that
    # re-serialized would silently normalise these bytes, and the gate would then
    # have validated an object that is not the one on disk.
    raw = b'[{"source_form":  "Alpha",\n  "basis":"transliterated"} ]'
    src.write_bytes(raw)
    mod.publish_fragment(src, target, staging, _PASSING_GATE, "batch 0 attempt 0")
    assert target.read_bytes() == raw, "the published bytes must be verbatim"
    assert not staging.exists(), "the staging copy must be renamed away, never left behind"


def test_bytes_that_fail_the_gate_never_reach_the_canonical_path(publish_bed):
    """The poll gates the SANDBOX artifact, which the job still owns and can rewrite
    after passing. So what is captured is gated again, against the driver's own
    staged copy, BEFORE the rename -- gating after it would leave a refused fragment
    sitting at exactly the path a resume reads."""
    mod, target, staging = (publish_bed["mod"], publish_bed["target"],
                            publish_bed["staging"])
    src = publish_bed["sandbox"] / "out_0_attempt_0.json"
    src.write_bytes(b'[{"source_form": "rewritten after the gate passed"}]')
    with pytest.raises(mod.DriverError):
        mod.publish_fragment(src, target, staging, _FAILING_GATE, "batch 0 attempt 0")
    assert not target.exists(), (
        "a fragment that failed the gate must never appear at the canonical path")
    assert not staging.exists(), "and the staged copy must be cleaned up"


def test_a_temp_root_location_is_reported_rather_than_left_implied(bed):
    """codex's workspace-write grants /tmp and $TMPDIR on top of the workspace root
    (measured against `codex sandbox` directly), so a durable root or verdict dir
    there is still writable by a dispatched job and no --cwd changes it. pytest's
    own tmp_path is under $TMPDIR, which is precisely why this is warned and not
    refused -- and why the warning has to be asserted rather than assumed."""
    mod = load(bed)
    warned = mod.warn_if_under_a_temp_root(bed["durable"], bed["session"])
    assert set(warned) == {"durable root", "verdict directory"}, (
        f"a bed under pytest's tmp_path is under $TMPDIR; both should warn, got {warned}")
    outside = mod.warn_if_under_a_temp_root(Path("/"), Path("/"))
    assert outside == [], f"a path outside every temp root must not warn, got {outside}"


def test_a_publication_never_adopts_a_file_it_did_not_create(publish_bed):
    """The staging name carries a fresh random token, so a path that already
    exists is not a stale copy of ours -- it is someone else's file at our name,
    and adopting it is how two concurrent drivers come to share one inode."""
    mod, target, staging = (publish_bed["mod"], publish_bed["target"],
                            publish_bed["staging"])
    src = publish_bed["sandbox"] / "out_0_attempt_0.json"
    src.write_bytes(b'[{"source_form": "Alpha"}]')
    staging.write_bytes(b"someone else's bytes")
    with pytest.raises(OSError):
        mod.publish_fragment(src, target, staging, _PASSING_GATE, "batch 0 attempt 0")
    assert not target.exists()
    assert staging.read_bytes() == b"someone else's bytes", (
        "a collision must not answer by DELETING the other publication's file -- "
        "until the O_EXCL open succeeds this publication owns nothing at that path")


def test_a_sandbox_that_cannot_be_removed_is_named_rather_than_leaked(bed, monkeypatch, tmp_path):
    """rmtree stays best-effort so cleanup cannot fail a finished batch -- but a
    directory the job made unremovable must still be named, or it is a leak nobody
    can find."""
    mod = load(bed)
    monkeypatch.setattr(mod.shutil, "rmtree", lambda *a, **k: None)
    printed = []
    monkeypatch.setattr(mod, "log", lambda m: printed.append(m))
    with mod.DispatchSandbox("dispatch-9-9") as sandbox:
        survivor = sandbox.path
    assert survivor.exists(), "fixture precondition: rmtree was stubbed out"
    assert any(str(survivor) in m and "could not be removed" in m for m in printed), (
        f"the surviving sandbox path must be named in the log, got {printed}")
    shutil.rmtree(survivor, ignore_errors=True)


def test_a_real_run_reports_a_temp_root_location(bed):
    """The operational call, not just the helper. A pytest bed is itself under
    $TMPDIR, so an ordinary drive must print the warning -- deleting the call in
    main() has to go red somewhere."""
    _out, proc = run_driver(bed)
    assert "WARNING" in proc.stderr and "codex makes writable" in proc.stderr, (
        f"a run whose durable root is under $TMPDIR must say so: {proc.stderr[-800:]}")
