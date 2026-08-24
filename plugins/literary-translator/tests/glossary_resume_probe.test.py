"""resume_setup.py's `probe_resumed_batches()` -- #724 A.

WHAT MOVED, AND WHY IT IS NOT JUST A COST FIX. The glossary Workflow used to
answer "does this batch already have a valid attempt-0 fragment?" with a full
subagent per batch: one bash command, `effort:"low"`, a `PRESENT`/`ABSENT` reply.
Measured on a live run, ~40k tokens per call and 21 calls per relaunch, for a
question whose entire input is disk state that is settled before the Workflow
starts.

The second cost was correctness, and it is the one that justifies moving the
probe rather than merely making it cheaper: the ANSWER travelled as agent prose.
That is the whole reason #228, #308 and #371 exist -- a reply that merely
MENTIONED `ABSENT 3`, or carried `PRESENT 3` glued to a word by any of sixteen
measured characters, decided the batch. A set computed by this function cannot be
glued, decorated, or contradicted by a later sentence.

WHERE IT HAD TO GO. #724 proposes `glossary_batch_plan.py` as the host. That is
not implementable: `glossary_batch_plan.py` runs strictly BEFORE
`resume_setup.py`, so at that point `_wipe_stale_glossary_fragments()` has not
decided what survives and `write_glossary_manifests()` has not written the file a
fragment's coverage is checked AGAINST. The probe therefore lives in
`resume_setup.py`, after both, and this file's `test_probe_reads_post_wipe_state`
is what makes that ordering an assertion rather than a comment.

FAIL-SAFE DIRECTION. Every failure mode -- absent fragment, malformed JSON, wrong
coverage, an unusable interpreter, a timeout -- leaves the batch OUT of the
returned set, which sends it down the ordinary dispatch path. Nothing here can
wrongly TRUST a fragment; the worst it can do is pay for a re-dispatch. That
asymmetry is asserted below rather than argued, because it is the only reason
this probe is allowed to be cheap.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _canon_project_fixture import (  # noqa: E402
    accepted_item,
    make_project,
    run_canon_init,
)
from _workflow_instantiation import instantiate_glossary_pass  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_RS_PATH = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts" / "resume_setup.py"
)


@pytest.fixture(scope="module")
def rs():
    spec = importlib.util.spec_from_file_location("resume_setup_probe_under_test", _RS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path):
    """A durable root holding the REAL canon_validate.py, initialised. The probe
    shells out to `${durable_root}/scripts/canon_validate.py`, so a fixture that
    stubbed it would be measuring the stub."""
    root = make_project(tmp_path)
    init = run_canon_init(root)
    assert init.returncode == 0, f"{init.stdout}\n{init.stderr}"
    return root


def _run_dir(root: Path) -> Path:
    d = root / "glossary" / "runs" / "R"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_batch(run_dir: Path, index: int, names, *, fragment=True, valid=True):
    """Seeds manifest_{i}.json and, optionally, out_{i}_attempt_0.json."""
    (run_dir / f"manifest_{index}.json").write_text(
        json.dumps(sorted(set(names))), encoding="utf-8"
    )
    if not fragment:
        return
    if valid:
        body = json.dumps([accepted_item(n, n + "-ru") for n in names], ensure_ascii=False)
    else:
        # Shape-valid JSON that fails Pass 1: an accepted item with no
        # canonical_target_form.
        body = json.dumps(
            [{"source_form": n, "is_proper_name": True, "disposition": "accepted"} for n in names],
            ensure_ascii=False,
        )
    (run_dir / f"out_{index}_attempt_0.json").write_text(body, encoding="utf-8")


def _batches(*specs):
    return [{"index": i, "names": names} for i, names in specs]


def _probe(rs, root, batches, research_mode="offline"):
    return rs.probe_resumed_batches(_run_dir(root), root, batches, research_mode)


# ---------------------------------------------------------------------------
# The answer itself.
# ---------------------------------------------------------------------------

def test_a_valid_attempt_zero_fragment_is_reported_resumed(rs, project):
    run_dir = _run_dir(project)
    _write_batch(run_dir, 0, ["Sappho"])
    assert _probe(rs, project, _batches((0, ["Sappho"]))) == [0]


def test_a_missing_fragment_is_not_resumed(rs, project):
    run_dir = _run_dir(project)
    _write_batch(run_dir, 0, ["Sappho"], fragment=False)
    assert _probe(rs, project, _batches((0, ["Sappho"]))) == []


def test_an_invalid_fragment_is_not_resumed(rs, project):
    run_dir = _run_dir(project)
    _write_batch(run_dir, 0, ["Sappho"], valid=False)
    assert _probe(rs, project, _batches((0, ["Sappho"]))) == [], (
        "a fragment that fails --check-batch must fall through to a real "
        "dispatch -- the probe may never trust one"
    )


def test_a_malformed_fragment_is_not_resumed(rs, project):
    run_dir = _run_dir(project)
    _write_batch(run_dir, 0, ["Sappho"], fragment=False)
    (run_dir / "out_0_attempt_0.json").write_text("{ not json", encoding="utf-8")
    assert _probe(rs, project, _batches((0, ["Sappho"]))) == []


def test_a_coverage_mismatch_is_not_resumed(rs, project):
    """The fragment is perfectly valid on its own and covers the WRONG names --
    the case a shape-only probe would wave through. It matters here more than it
    does for a fresh dispatch: this run's manifest is what the batch is FOR."""
    run_dir = _run_dir(project)
    _write_batch(run_dir, 0, ["Sappho"])
    (run_dir / "manifest_0.json").write_text(
        json.dumps(["Sappho", "Ninon"]), encoding="utf-8"
    )
    assert _probe(rs, project, _batches((0, ["Sappho", "Ninon"]))) == []


def test_the_decision_is_per_batch(rs, project):
    run_dir = _run_dir(project)
    _write_batch(run_dir, 0, ["Sappho"])
    _write_batch(run_dir, 1, ["Ninon"], fragment=False)
    _write_batch(run_dir, 2, ["Moliere"])
    assert _probe(rs, project, _batches((0, ["Sappho"]), (1, ["Ninon"]), (2, ["Moliere"]))) == [0, 2]


def test_a_fresh_run_with_nothing_on_disk_returns_the_empty_set(rs, project):
    run_dir = _run_dir(project)
    for i, names in ((0, ["Sappho"]), (1, ["Ninon"])):
        _write_batch(run_dir, i, names, fragment=False)
    assert _probe(rs, project, _batches((0, ["Sappho"]), (1, ["Ninon"]))) == []


# ---------------------------------------------------------------------------
# Ordering: the probe must see POST-wipe, POST-manifest state.
# ---------------------------------------------------------------------------

def test_probe_reads_post_wipe_state(rs, project):
    """The ordering claim, made executable. A fresh run wipes attempt 0; a probe
    that ran before the wipe -- which is exactly where #724 proposed to put it,
    in glossary_batch_plan.py -- would report that batch resumed and the
    Workflow would skip a dispatch for a fragment that no longer exists.
    """
    run_dir = _run_dir(project)
    _write_batch(run_dir, 0, ["Sappho"])
    batches = _batches((0, ["Sappho"]))

    # Before the wipe the fragment is there and valid: this is what an
    # early-running probe would have seen.
    assert _probe(rs, project, batches) == [0]

    rs._wipe_stale_glossary_fragments(run_dir, resume=False)
    assert not (run_dir / "out_0_attempt_0.json").exists()
    assert _probe(rs, project, batches) == [], (
        "the probe must run AFTER the wipe; a batch whose fragment the wipe just "
        "removed is not resumed"
    )


def test_a_resume_keeps_attempt_zero_so_the_probe_still_finds_it(rs, project):
    """The other half of the same ordering: a RESUME deliberately keeps attempt
    0, and that is precisely the case the whole resume-skip exists for."""
    run_dir = _run_dir(project)
    _write_batch(run_dir, 0, ["Sappho"])
    rs._wipe_stale_glossary_fragments(run_dir, resume=True)
    assert _probe(rs, project, _batches((0, ["Sappho"]))) == [0]


# ---------------------------------------------------------------------------
# Fail-safe: no environment failure may produce a false "resumed".
# ---------------------------------------------------------------------------

def test_an_unusable_validator_reports_nothing_resumed(rs, project):
    """A missing or broken canon_validate.py must send every batch down the
    dispatch path, never mark them resumed. Asserted by pointing the probe at a
    durable root with no scripts/ at all, which is the shape of a half-scaffolded
    project."""
    run_dir = _run_dir(project)
    _write_batch(run_dir, 0, ["Sappho"])
    empty_root = project.parent / "no_scripts_root"
    empty_root.mkdir()
    assert rs.probe_resumed_batches(run_dir, empty_root, _batches((0, ["Sappho"])), "offline") == []


def test_a_subprocess_failure_is_swallowed_into_not_resumed(rs, project, monkeypatch):
    """Any OSError/SubprocessError -- a timeout, an exec failure -- resolves to
    "not resumed" rather than escaping. The probe runs inside a pre-workflow gate
    whose whole job is to abort loudly on real setup failures, so an exception
    here would turn a cheap optimisation into a run-stopper."""
    import subprocess as _sp

    run_dir = _run_dir(project)
    _write_batch(run_dir, 0, ["Sappho"])

    def boom(*a, **k):
        raise _sp.TimeoutExpired(cmd="canon_validate.py", timeout=120)

    monkeypatch.setattr(rs.subprocess, "run", boom)
    assert _probe(rs, project, _batches((0, ["Sappho"]))) == []


# ---------------------------------------------------------------------------
# The reported key, end to end through the CLI.
# ---------------------------------------------------------------------------

def test_the_probe_reads_the_path_the_template_dispatch_writes(tmp_path):
    """THE SEAM, and the reason #724 turned an intra-file coupling into a
    cross-language one.

    This function checks exactly one path per batch, `out_{index}_attempt_0.json`.
    That literal 0 is not arbitrary -- it is a claim about
    glossary-pass-wf.template.js, whose retry loop enters at attempt 0 and whose
    dispatch therefore writes that filename. Probe any other attempt and this
    function asks about a file no run ever wrote: it would report nothing
    resumed, on every run, and the only symptom would be a re-dispatch of work
    already done. Nothing goes red, because the fragment is simply regenerated.

    Until #724 both ends lived in the template (a precheck prompt built from
    checkBatchCmd(index, 0), and the loop), and one test compared two rendered
    prompts. They are now in different languages, so the comparison has to be
    too: this reads the fragment BASENAME out of the template's own rendered
    dispatch prompt and seeds a file under exactly that name, then asserts the
    real probe finds it. A template that moved its entry attempt, or a probe
    that changed which attempt it checks, breaks this from either side.

    The template's half -- that the loop really does enter at attempt 0 -- is
    asserted in tests/glossary_citation_review.test.py::
    test_the_retry_loop_enters_at_attempt_zero_which_is_what_the_probe_assumes,
    which reads it from a real run's call sequence. Neither half is sufficient
    alone."""
    import json as _json
    import re as _re
    import subprocess as _sp

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; this seam renders the real template")

    src = instantiate_glossary_pass(
        durable_root="/fixture/durable_root",
        run_id="SEAMRUN",
        research_mode="offline",
    )
    # Rendered, not grepped from the raw template: the path is built by string
    # concatenation from RUN_DIR, so only the instantiated text has it whole.
    body = src.replace("export const meta", "const meta", 1)
    harness = (
        "async function __wf__(agent, pipeline, log, args) {\n" + body + "\n}\n"
        "const seen = [];\n"
        "async function agent(p, o) {\n"
        "  seen.push({label: (o && o.label) || '', prompt: String(p)});\n"
        "  const l = (o && o.label) || '';\n"
        "  if (l.indexOf('glossary:wait') === 0) return 'READY 0';\n"
        "  if (l === 'glossary:verify') return {verified: true};\n"
        "  return 'ok';\n"
        "}\n"
        "async function pipeline(items, s1) { const o = []; "
        "for (const i of items) o.push(await s1(i)); return o; }\n"
        "(async () => { await __wf__(agent, pipeline, function () {}, "
        "[{index: 0, candidates: [{name: 'Sappho', freq: 3, likely_name: true}]}]); "
        "process.stdout.write(JSON.stringify(seen)); })();\n"
    )
    harness_path = tmp_path / "seam.js"
    harness_path.write_text(harness, encoding="utf-8")
    proc = _sp.run([node, str(harness_path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    dispatch = [c["prompt"] for c in _json.loads(proc.stdout)
                if c["label"] == "glossary:dispatch:0"]
    assert len(dispatch) == 1, f"expected exactly one dispatch prompt, got {len(dispatch)}"
    names = set(_re.findall(r"out_0_attempt_\d+\.json", dispatch[0]))
    assert len(names) == 1, (
        f"the dispatch prompt must name exactly one fragment file so this seam has "
        f"an unambiguous basename to seed; it named {sorted(names)}"
    )
    template_basename = names.pop()

    # Now seed a VALID fragment under exactly that name and ask the real probe.
    root = make_project(tmp_path / "proj")
    init = run_canon_init(root)
    assert init.returncode == 0, f"{init.stdout}\n{init.stderr}"
    run_dir = root / "glossary" / "runs" / "R"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest_0.json").write_text(_json.dumps(["Sappho"]), encoding="utf-8")
    (run_dir / template_basename).write_text(
        _json.dumps([accepted_item("Sappho", "Sappho-ru")], ensure_ascii=False),
        encoding="utf-8",
    )

    spec = importlib.util.spec_from_file_location("resume_setup_seam", _RS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.probe_resumed_batches(
        run_dir, root, [{"index": 0, "names": ["Sappho"]}], "offline"
    ) == [0], (
        f"the probe did not find a valid fragment stored under "
        f"{template_basename!r} -- the name the template's own dispatch prompt "
        f"says attempt 0 is written to. The two ends of the resume seam name "
        f"different files, so every resumed run would re-dispatch every batch"
    )


def test_glossary_run_dir_derivation_is_shared(rs):
    """main() probes a directory write_run_dir() created. Two copies of that
    join is how the probe comes to read a directory the wipe never touched, so
    both go through one helper."""
    dirs = {"durable_root": Path("/x/root"), "runs_dir": Path("/x/root/runs")}
    assert rs.glossary_run_dir_for(dirs, "R") == Path("/x/root/glossary/runs/R")
