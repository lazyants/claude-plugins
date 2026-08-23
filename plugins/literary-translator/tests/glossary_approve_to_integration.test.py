"""tests/glossary_approve_to_integration.test.py -- the ONE cross-language
producer/consumer test for LT 1.16.x's approve-to snapshot seam.

Neither sibling suite closes this seam alone. tests/canon_approve_to.test.py
runs the REAL canon_validate.py, but against a HAND-BUILT --check-batch
--approve-to command, so it cannot notice the glossary template emitting a
different flag or path. tests/glossary_snapshot_ordering.test.py checks the
template EMITS `<checkBatchCmd> --approve-to <approvedPath>` into the
citation-PREPARE prompt (the citation-review prompt until 1.16.1 moved retrieval,
and the approve command with it, out of the judging agent -- see #347), but never
RUNS that string against the script, so it cannot notice canon_validate.py
renaming the flag or moving the write. The gap between them is JS<->Python
flag/path drift: either side could change and both files stay green.

This test drives the REAL template under Node, lifts the approve command it
ACTUALLY emitted (verbatim -- never reconstructed for the run), and executes
THAT against the REAL canon_validate.py over a schema-valid fragment; it then
lifts the template's own --merge-batches command and runs it too, so the full
producer/consumer round-trip -- approve -> snapshot -> merge -> canon.json -- is
exercised end to end with the banked mergePath proven to be the exact snapshot
Python wrote. A rename on either side of the seam fails here.

The CRLF line terminator in the fragment bytes is load-bearing, for the same
reason it is in tests/canon_approve_to.test.py: an LF-only fixture cannot tell
read_bytes() from read_text(), because both yield identical bytes for LF
content. Only a fragment whose on-disk bytes carry CR proves the snapshot the
merge later consumes is byte-identical to what --check-batch validated, across
the real seam rather than within one script's own suite.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
GLOSSARY_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"

assert GLOSSARY_TEMPLATE.is_file(), f"expected plugin template not found: {GLOSSARY_TEMPLATE}"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; this test executes the real glossary-pass "
    "template under Node to lift the approve command it emits (no hard Node.js "
    "dependency for this plugin otherwise)",
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _canon_project_fixture import (  # noqa: E402
    accepted_item,
    make_project,
    run_canon_init,
)

FIXTURE_RUN_ID = "20260726T000000Z"
FIXTURE_SOURCE_LANG = "French"
FIXTURE_TARGET_LANG = "Russian"
SOURCE_FORM = "Sappho"
TARGET_FORM = "Sapho"


def instantiate(durable_root: str, *, plugin_root: str,
                research_mode: str = "live",
                batch_agent_cap: int = 10_000) -> str:
    """The template's documented one-time substitution -- but with DURABLE_ROOT
    bound to a REAL writable directory (unlike the sibling harnesses' fixed
    /fixture/... root), so the paths it emits can be created and the command it
    emits can actually run against the real script staged inside that root."""
    text = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", durable_root)
    text = text.replace("{{SOURCE_LANG}}", FIXTURE_SOURCE_LANG)
    text = text.replace("{{TARGET_LANG}}", FIXTURE_TARGET_LANG)
    text = text.replace("{{RESEARCH_MODE}}", research_mode)
    text = text.replace("{{RUN_ID}}", FIXTURE_RUN_ID)
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    text = text.replace("{{EFFORT}}", "high")
    # 1.16.1 (#347): empty = fetch_citation.py's shipped default list.
    text = text.replace("{{CITATION_CONTENT_TYPES}}", "")
    # #412 -- json.dumps JS string literal, token OUTSIDE quotes. REQUIRED,
    # with no default: unlike mass-translate-wf.template.js's own
    # {{PLUGIN_ROOT}}, an empty string is not a "did not opt into the
    # redirect" sentinel for THIS template -- it throws at instantiation, so
    # a defaulted-empty call would surface as run()'s opaque "template threw
    # under Node" rather than as a missing argument. Callers must name a
    # root, and this harness's one caller
    # (test_the_emitted_approve_command_snapshots_byte_identically_against_the_real_script)
    # names the REAL skill root, because its --merge-batches command is run
    # for real against the REAL canon_validate.py, which since #412 refuses
    # every stamping mode without either --plugin-root or
    # --allow-durable-sibling.
    text = text.replace("{{PLUGIN_ROOT}}", json.dumps(plugin_root))
    assert "{{" not in text, "fixture instantiation left an unresolved token"
    return text


def _wrap(js_source: str) -> str:
    assert js_source.count("export const meta") == 1
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


def make_batch(index: int, names: list) -> dict:
    return {
        "index": index,
        "candidates": [
            {
                "name": n, "freq": 3, "mid_sentence": False, "multiword": False,
                "abbrev": False, "n_segments": 2, "likely_name": True,
            }
            for n in names
        ],
    }


# Same extract-substitute-wrap-run-under-Node harness as the sibling glossary
# suites: records every call's label and rendered prompt IN ORDER so the approve
# command the template actually built can be lifted back out.
HARNESS = r"""
'use strict';
__WRAPPED_SOURCE__

const BATCHES_ARGS = __BATCHES_JSON__;
const PLAN = __PLAN_JSON__;
const promptsByLabel = {};
const callsLog = [];
const logLines = [];
const seenCount = {};

function record(label, promptText) {
  if (!promptsByLabel[label]) promptsByLabel[label] = [];
  promptsByLabel[label].push(typeof promptText === "string" ? promptText : String(promptText));
  seenCount[label] = (seenCount[label] || 0) + 1;
  return seenCount[label] - 1;
}

function nth(list, i, fallback) {
  if (!Array.isArray(list)) return fallback;
  return (i < list.length) ? list[i] : fallback;
}

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  const ordinal = record(label, promptText);
  callsLog.push({ label: label, ordinal: ordinal });

  if (label === "glossary:merge") return "MERGED (mock)";
  if (label === "glossary:verify") return { verified: true };

  const parts = label.split(":");
  const kind = parts[1];
  const idx = parts[parts.length - 1];
  const p = PLAN[idx] || {};

  if (kind === "precheck") {
    return Object.prototype.hasOwnProperty.call(p, "precheck") ? p.precheck : ("ABSENT " + idx);
  }
  if (kind === "dispatch") return "FRAGMENT " + idx;
  if (kind === "wait") return "READY " + idx;
  // 1.16.1 (#347) -- the citation review is two calls now, and the JUDGE runs
  // only after PREPARE reports EVIDENCE_READY. Leaving this label unanswered
  // does not fail loudly: the batch simply climbs the retry ladder to
  // exhaustion, no review prompt is ever recorded, and the approve command this
  // test exists to LIFT is never emitted at all.
  if (kind === "citation-prepare") {
    return nth(p.prepares, ordinal, "EVIDENCE_READY " + idx + " ATTEMPT " + ordinal);
  }
  if (kind === "citation-review") {
    // Attempt derived from the prepare count rather than from this label's own
    // ordinal: a failed prepare spends no judge call, so the two diverge. See
    // tests/glossary_snapshot_ordering.test.py's copy of this harness.
    const prepared = seenCount["glossary:citation-prepare:" + idx] || 1;
    return nth(p.reviews, ordinal, "CITATIONS_OK " + idx + " ATTEMPT " + (prepared - 1));
  }
  return "UNEXPECTED_LABEL " + label;
}

async function pipeline(items, stage1) {
  const out = [];
  for (const item of items) out.push(await stage1(item));
  return out;
}

function log(msg) { logLines.push(String(msg)); }

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, BATCHES_ARGS);
    process.stdout.write(JSON.stringify({
      result: result, calls: callsLog, promptsByLabel: promptsByLabel, log: logLines,
    }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.stack || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def run(*, tmp_path: Path, durable_root: str, batches: list, plugin_root: str,
        research_mode: str = "live", plan: dict | None = None,
        timeout: int = 30) -> dict:
    plan = plan or {}
    harness = (
        HARNESS.replace("__WRAPPED_SOURCE__", _wrap(instantiate(
            durable_root, research_mode=research_mode, plugin_root=plugin_root)))
        .replace("__BATCHES_JSON__", json.dumps(batches))
        .replace("__PLAN_JSON__", json.dumps(plan))
    )
    p = tmp_path / "glossary_approve_to_harness.js"
    p.write_text(harness, encoding="utf-8")
    # NODE is only None when `node` is absent from PATH, in which case
    # pytestmark's skipif already skipped this test before the call is reached.
    assert NODE is not None
    proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 0, f"template threw under Node: {proc.stderr}"
    return json.loads(proc.stdout)


def prompts_for(out: dict, label: str) -> list:
    return out["promptsByLabel"].get(label, [])


def run_dir(root: Path) -> Path:
    return root / "glossary" / "runs" / FIXTURE_RUN_ID


def attempt_path(root: Path, index: int, attempt: int) -> Path:
    return run_dir(root) / f"out_{index}_attempt_{attempt}.json"


def approved_path(root: Path, index: int, attempt: int) -> Path:
    return run_dir(root) / f"approved_{index}_attempt_{attempt}.json"


def manifest_path(root: Path, index: int) -> Path:
    return run_dir(root) / f"manifest_{index}.json"


# The chunk poll's ACCEPT gate as rendered since 1.16.2 (#352). The
# `>/dev/null 2>&1` suppression is part of the emitted poll, NOT part of the
# --check-batch contract, so it is matched here rather than swallowed into the
# extracted command -- the approve command is that contract plus an APPENDED
# flag, and a stray suffix would make every comparison below compare the wrong
# string while still looking like it passed.
_CHUNK_ACCEPT_RE = re.compile(r"while true; do (.*?) >/dev/null 2>&1 && exit 0;")


def check_cmd_from_wait(out: dict, root: Path, index: int, attempt: int = 0) -> str:
    """The --check-batch command the WAIT poll for this attempt actually issued,
    lifted back out of its rendered polling loop (mirrors
    glossary_snapshot_ordering.test.py's helper, but resolved against a REAL
    durable root).

    1.16.2 (#352): a wait renders up to WAIT_CHUNKS chunk prompts under one
    label, so this selects by the attempt's own fragment PATH rather than by
    position in the prompt list."""
    wanted = str(attempt_path(root, index, attempt))
    hits = []
    for prompt in prompts_for(out, f"glossary:wait:{index}"):
        for line in prompt.split("\n"):
            m = _CHUNK_ACCEPT_RE.search(line)
            if m and "--check-batch" in m.group(1) and wanted in m.group(1):
                hits.append(m.group(1))
    assert hits, (
        f"no wait chunk for batch {index} attempt {attempt} issued a --check-batch "
        f"gate naming {wanted}"
    )
    assert len(set(hits)) == 1, (
        f"the wait chunks for batch {index} attempt {attempt} issued DIFFERENT "
        f"--check-batch commands: {sorted(set(hits))}"
    )
    return hits[0]


def approve_cmd_for(check_cmd: str, root: Path, index: int, attempt: int) -> str:
    """--approve-to APPENDED to the pinned --check-batch contract, never
    interleaved -- the shape glossary_snapshot_ordering.test.py pins on the JS
    side. Used here only as a readable cross-check of the lifted command."""
    return check_cmd + " --approve-to " + str(approved_path(root, index, attempt))


def emitted_approve_cmd(prepare: str) -> str:
    """The approve command the template ACTUALLY emitted into the citation
    PREPARE call's STEP 1 line -- lifted verbatim, not rebuilt, so the string
    this test runs is the string the template produces.

    Read off the prepare prompt since 1.16.1 (#347), where retrieval moved out of
    the judging agent and the approve command moved with it into the new prepare
    call; it used to be lifted from the citation-review prompt. The STEP 1 check
    is not decoration: the whole seam only means anything if the snapshot is
    still the first thing that happens, so lifting a command that had drifted to
    some later step would run green while the ordering guarantee was gone.
    """
    step1 = [ln for ln in prepare.split("\n") if "--approve-to" in ln]
    assert len(step1) == 1, (
        f"expected exactly one --approve-to line in the citation-prepare prompt, "
        f"found {len(step1)}"
    )
    line = step1[0]
    assert line.startswith("STEP 1."), (
        f"the approve command is no longer prepare's first act: {line}"
    )
    assert "python3 " in line, f"the approve line does not begin a python3 command: {line}"
    return line[line.index("python3 "):]


def emitted_merge_cmd(merge_prompt: str) -> str:
    """The --merge-batches command the template ACTUALLY emitted into the merge
    prompt -- lifted verbatim, so the merge half of the round-trip runs the
    template's own command, not a rebuilt one."""
    lines = [ln for ln in merge_prompt.split("\n") if "--merge-batches" in ln]
    assert len(lines) == 1, (
        f"expected exactly one --merge-batches line in the merge prompt, found {len(lines)}"
    )
    line = lines[0]
    assert "python3 " in line, f"the merge line does not begin a python3 command: {line}"
    return line[line.index("python3 "):]


def _crlf_fragment_bytes() -> bytes:
    """One accepted item, pretty-printed, terminated with CRLF. JSON permits
    CR/CRLF as inter-token whitespace, so this stays valid while carrying bytes
    read_text() would rewrite -- the whole point of the byte-fidelity check."""
    text = json.dumps([accepted_item(SOURCE_FORM, TARGET_FORM)], indent=2, ensure_ascii=False)
    return text.replace("\n", "\r\n").encode("utf-8")


def test_the_emitted_approve_command_snapshots_byte_identically_against_the_real_script(tmp_path):
    """The producer (glossary template, under Node) hands its emitted approve
    command to the consumer (canon_validate.py, the real script) and the snapshot
    that comes back is byte-identical to the CRLF fragment that was validated.

    Every flag and path is the template's own; only PY (argv[0]) is retargeted to
    this interpreter. So a flag rename, a path-shape change, or a lost
    read_bytes()->read_text() on either side of the JS/Python seam fails here --
    the failure no per-file suite can produce on its own."""
    root = make_project(tmp_path)
    init = run_canon_init(root)
    assert init.returncode == 0, f"canon init failed:\n{init.stdout}\n{init.stderr}"

    # #412 -- the REAL plugin install root (this repo's own
    # skills/literary-translator, which really does hold
    # assets/scripts/cache_key.py), so the --merge-batches command the
    # template emits below carries a --plugin-root that resolves against the
    # genuine sibling script rather than the durable_root's own copy under
    # {root}/scripts/ -- proving the #412 redirect works end to end, not
    # just that its command STRING looks right.
    real_plugin_root = str(PLUGIN_ROOT / "skills" / "literary-translator")

    # Drive the REAL template under Node with DURABLE_ROOT bound to this project,
    # so the command it emits names the real staged canon_validate.py.
    out = run(
        tmp_path=tmp_path, durable_root=str(root),
        batches=[make_batch(0, [SOURCE_FORM])], plugin_root=real_plugin_root,
    )
    prepare = prompts_for(out, "glossary:citation-prepare:0")[0]

    # Lift the approve command the template ACTUALLY emitted. Cross-check it
    # against the wait poll's own --check-batch string plus the appended flag, so
    # a drift in either the check prefix or the --approve-to append is named here
    # rather than surfacing as an opaque subprocess failure below.
    emitted = emitted_approve_cmd(prepare)
    reconstructed = approve_cmd_for(check_cmd_from_wait(out, root, 0), root, 0, 0)
    assert emitted == reconstructed, (
        "the emitted approve command is not checkBatchCmd()+--approve-to as the "
        f"contract requires:\n  emitted:       {emitted}\n  reconstructed: {reconstructed}"
    )

    attempt = attempt_path(root, 0, 0)
    approved = approved_path(root, 0, 0)
    manifest = manifest_path(root, 0)
    assert str(attempt) in emitted and str(approved) in emitted, (
        f"the emitted command does not name this batch/attempt's own paths:\n{emitted}"
    )

    # Materialise exactly what the emitted command expects to read: the attempt
    # fragment (as CRLF bytes on disk) and the exact-coverage source-forms
    # manifest --check-batch validates against.
    run_dir(root).mkdir(parents=True, exist_ok=True)
    raw = _crlf_fragment_bytes()
    attempt.write_bytes(raw)
    manifest.write_text(json.dumps([SOURCE_FORM]), encoding="utf-8")

    # Run the template's own emitted command against the REAL canon_validate.py.
    argv = shlex.split(emitted)
    assert argv[0] == "python3", f"unexpected interpreter token in emitted command: {argv[0]!r}"
    argv[0] = sys.executable
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)

    assert proc.returncode == 0, (
        "the template's emitted approve command failed against the real "
        f"canon_validate.py:\n{proc.stdout}\n{proc.stderr}"
    )
    assert approved.is_file(), (
        f"the emitted command exited 0 but wrote no snapshot at {approved}"
    )
    assert approved.read_bytes() == raw, (
        "the snapshot is not byte-identical to the validated CRLF fragment -- a "
        "read_text() anywhere across the JS->Python seam would have normalised "
        "the line endings the merge later consumes"
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload.get("approved_path") == str(approved), (
        f"the script's stdout must name the snapshot it wrote so a caller can "
        f"bank it; got {payload!r}"
    )

    # The other half of the seam: the mergePath the template BANKED is the exact
    # path Python just wrote, and the --merge-batches command the template emits
    # over that snapshot folds the entry into canon.json. This closes the full
    # producer/consumer round-trip -- approve -> snapshot -> merge -> canon --
    # with every command lifted from the template, never hand-built.
    banked_merge_path = out["result"]["batches"][0]["mergePath"]
    assert banked_merge_path == str(approved), (
        "the template banked a mergePath that is not the snapshot Python wrote:\n"
        f"  banked:       {banked_merge_path}\n  python wrote: {approved}"
    )

    merge_cmd = emitted_merge_cmd(prompts_for(out, "glossary:merge")[0])
    assert str(approved) in merge_cmd, (
        f"the emitted merge command does not name the approved snapshot:\n{merge_cmd}"
    )
    margv = shlex.split(merge_cmd)
    assert margv[0] == "python3", f"unexpected interpreter token in merge command: {margv[0]!r}"
    margv[0] = sys.executable
    mproc = subprocess.run(margv, capture_output=True, text=True, timeout=120)
    assert mproc.returncode == 0, (
        "the template's emitted --merge-batches command failed against the real "
        f"canon_validate.py:\n{mproc.stdout}\n{mproc.stderr}"
    )
    canon = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert list(canon.get("entries", {})) == [SOURCE_FORM], (
        "the snapshot the template approved must merge into canon.json under its "
        f"own source_form; canon entries were {list(canon.get('entries', {}))}"
    )
