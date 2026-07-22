"""tests/seg_safety_source_and_workflow.test.py -- regression-lock for the
seg-id path/shell-injection fix.

THE VULNERABILITY this file locks down: a manifest segment id (`seg`) flows
from `select_segments.py` (which reads manifest.json's `segments[]`) into
`mass-translate-wf.template.js`, where it is spliced RAW/unquoted into shell
command strings the workflow builds (including a `bash` for-loop inside
waitPrompt). An unsafe `seg` -- a path-traversal id (`../evil`), an absolute
path (`/etc/x`), or a shell metacharacter id (`seg;rm`) -- could therefore
escape the durable root or inject arbitrary shell commands. Before this fix,
`review_artifact_check.py`'s own `validate_seg()` was a DENYLIST (rejecting
only empty/absolute/`/`/`\\`/`..`) that let `seg;rm` straight through.

THE FIX, three layers:
  1. `select_segments.py` -- the SOURCE guard. Validates every seg id it
     reads (manifest.json's segments[], and --only-segs) against the
     canonical allowlist BEFORE building any path or emitting SEGS, so a
     poisoned manifest is rejected at the earliest possible point.
  2. `review_artifact_check.py` -- its existing `validate_seg()` upgraded
     from the denylist above to the same allowlist (kept backward-compatible
     with the specific "absolute path" / "path separator" / "'..' path
     component" / "must not be empty" messages `tests/
     review_artifact_check.test.py` already asserts on -- the allowlist
     regex is still the single source of truth for accept/reject; only the
     diagnostic wording for the four classic path-escape cases is kept, so
     no existing test breaks).
  3. `mass-translate-wf.template.js` -- a defense-in-depth JS guard, right
     after `SEGS` is built from `args`, that throws on any element not
     matching the same allowlist, before any prompt-builder function (and
     therefore any shell command string) is ever built.

The canonical allowlist, identical across all three: a seg id is either an
ordinary body id (`[A-Za-z0-9_]+`, e.g. "seg01") or a translate-decision
`FRONTBACK:{id}` unit (e.g. "FRONTBACK:fm01") -- nothing else, no path
separators, no `..`, no shell metacharacters, no whitespace.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SELECT_SCRIPT_SRC = ASSETS_DIR / "scripts" / "select_segments.py"
LEDGER_MERGE_SRC = ASSETS_DIR / "scripts" / "ledger_merge.py"
REVIEW_CHECK_SCRIPT_SRC = ASSETS_DIR / "scripts" / "review_artifact_check.py"
SCHEMAS_SRC = ASSETS_DIR / "schemas"
MASS_TRANSLATE_TEMPLATE = ASSETS_DIR / "templates" / "mass-translate-wf.template.js"

assert SELECT_SCRIPT_SRC.is_file(), f"select_segments.py not found at {SELECT_SCRIPT_SRC}"
assert LEDGER_MERGE_SRC.is_file(), f"ledger_merge.py not found at {LEDGER_MERGE_SRC}"
assert REVIEW_CHECK_SCRIPT_SRC.is_file(), f"review_artifact_check.py not found at {REVIEW_CHECK_SCRIPT_SRC}"
assert SCHEMAS_SRC.is_dir(), f"schemas dir not found at {SCHEMAS_SRC}"
assert MASS_TRANSLATE_TEMPLATE.is_file(), f"mass-translate-wf.template.js not found at {MASS_TRANSLATE_TEMPLATE}"

# A representative subset of the shared validation vocabulary (the full list
# lives in both scripts' own docstrings/comments): safe ordinary + FRONTBACK
# ids, and unsafe ids covering path traversal, absolute paths, shell
# metacharacters, whitespace, and a trailing newline (the re.fullmatch vs.
# re.match+"$" trap).
SAFE_SEG_IDS = [
    "seg01",
    "seg0",
    "segAnchor",
    "seg_alpha",
    "seg05_blocked_regen",
    "FRONTBACK:fm01",
    "FRONTBACK:back_omit",
]
UNSAFE_SEG_IDS = [
    "../evil",
    "seg;rm",
    "/etc/x",
    "seg\n",
    "seg|pipe",
    "seg`cmd`",
    "seg$(cmd)",
    "seg two",
    "..",
]


# ---------------------------------------------------------------------------
# Part 1: select_segments.py -- the source guard.
# ---------------------------------------------------------------------------

# A minimal, self-contained fixture harness -- duplicated from
# tests/select_segments.test.py's own `make_durable_root`/`write_manifest`/
# `run_select` (per this directory's "one test file per mechanism, no shared
# lib between self-contained test files" convention). Only what this file's
# own tests need: no converged/stale fragments are ever written here (every
# candidate this file uses is either rejected before ledger_merge.py ever
# runs, or lands as plain not_started), so no fake cache_key.py stub is
# needed -- ledger_merge.py treats a missing cache_key.py as a non-fatal
# warning, and only for fragments with status "converged" (see
# ledger_merge.py's own `_compute_stale_segments`).


def make_durable_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SELECT_SCRIPT_SRC, scripts_dir / "select_segments.py")
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")

    schemas_dir = root / "schemas"
    shutil.copytree(SCHEMAS_SRC, schemas_dir)

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    return root


def write_manifest(root, seg_ids):
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": s} for s in seg_ids]}, ensure_ascii=False),
        encoding="utf-8",
    )


def run_select(root, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "select_segments.py"), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )


def parse_stdout(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return json.loads(lines[0])


@pytest.mark.parametrize("unsafe_seg", UNSAFE_SEG_IDS)
def test_select_segments_fatals_on_unsafe_manifest_seg_id(tmp_path, unsafe_seg):
    root = make_durable_root(tmp_path)
    # A safe id alongside the unsafe one, so a regression that only checks
    # the FIRST or LAST candidate would still be caught.
    write_manifest(root, ["seg01_ok", unsafe_seg])

    proc = run_select(root)

    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "unsafe segment id" in payload["error"]
    # Never even reaches ledger_merge.py once an unsafe id is found.
    assert not (root / "runs" / "ledger.json").exists()


def test_select_segments_accepts_clean_manifest(tmp_path):
    root = make_durable_root(tmp_path)
    write_manifest(root, SAFE_SEG_IDS)

    proc = run_select(root)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True
    # No ledger fragments at all -> every candidate is not_started, all
    # eligible -> SEGS == every id, in manifest order.
    assert payload["segs"] == SAFE_SEG_IDS


@pytest.mark.parametrize("unsafe_seg", ["seg;rm", "../evil", "seg|pipe", "/etc/x"])
def test_select_segments_only_segs_fatals_on_unsafe_id(tmp_path, unsafe_seg):
    # NOTE: a trailing-newline id is deliberately NOT parametrized here --
    # parse_only_segs()'s own documented whitespace-trimming (see
    # tests/select_segments.test.py's test_only_segs_dedups_and_trims_
    # whitespace) legitimately strips it before validate_seg() ever sees it,
    # so it is not a meaningful unsafe case for THIS call site specifically
    # (it is covered for the manifest.json path, which never strips, via
    # test_select_segments_fatals_on_unsafe_manifest_seg_id above).
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01_ok"])

    proc = run_select(root, "--only-segs", f"seg01_ok,{unsafe_seg}")

    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "--only-segs" in payload["error"]
    assert "unsafe segment id" in payload["error"]
    # Rejected before the "not present in manifest.json" check would even
    # apply, and before ledger_merge.py ever runs.
    assert not (root / "runs" / "ledger.json").exists()


# ---------------------------------------------------------------------------
# Part 2: review_artifact_check.py -- the denylist -> allowlist upgrade.
# Imported directly (not subprocess) so validate_seg() can be exercised as a
# plain function, per this task's own instruction.
# ---------------------------------------------------------------------------


def _load_review_artifact_check_module():
    spec = importlib.util.spec_from_file_location(
        "review_artifact_check_under_test", REVIEW_CHECK_SCRIPT_SRC
    )
    assert spec is not None and spec.loader is not None, f"could not load spec for {REVIEW_CHECK_SCRIPT_SRC}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review_artifact_check = _load_review_artifact_check_module()


@pytest.mark.parametrize("unsafe_seg", UNSAFE_SEG_IDS)
def test_review_artifact_check_validate_seg_rejects_unsafe_ids(unsafe_seg):
    assert review_artifact_check.validate_seg(unsafe_seg) is not None


@pytest.mark.parametrize("safe_seg", SAFE_SEG_IDS)
def test_review_artifact_check_validate_seg_accepts_safe_ids(safe_seg):
    assert review_artifact_check.validate_seg(safe_seg) is None


def test_review_artifact_check_denylist_to_allowlist_upgrade():
    """The exact regression this fix closes: under the OLD denylist (empty /
    absolute / '/' or '\\\\' / '..'), "seg;rm" had none of those properties
    and was therefore silently ACCEPTED -- letting a shell-metacharacter id
    reach review_path(seg) construction. The upgraded allowlist must now
    REJECT it."""
    assert review_artifact_check.validate_seg("seg;rm") is not None
    # Sanity: the classic path-escape cases these tests must never break
    # (tests/review_artifact_check.test.py asserts these exact substrings)
    # are still caught too, with their historical wording intact.
    assert "absolute path" in review_artifact_check.validate_seg("/etc/passwd")
    assert "path separator" in review_artifact_check.validate_seg("foo/bar")
    assert "'..' path component" in review_artifact_check.validate_seg("..")
    assert "must not be empty" in review_artifact_check.validate_seg("")


# ---------------------------------------------------------------------------
# Part 3: mass-translate-wf.template.js -- the defense-in-depth JS guard.
# Executed for real via Node.js (skipped if node is not on PATH, matching
# tests/batch_size_estimator.test.py's own stance: no hard Node.js
# dependency for this plugin otherwise).
# ---------------------------------------------------------------------------

NODE = shutil.which("node")

# Cheap, always-on sanity check (no node needed): the guard's identifying
# symbols must still be present in the raw template text, so a future
# refactor that accidentally deletes the guard fails loudly even in a
# node-less environment.
def test_template_source_declares_the_seg_id_guard():
    raw = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    assert "SEG_ID_RE" in raw
    assert "Unsafe segment id" in raw
    # Must appear after SEGS is built, and before the first prompt-builder
    # function (translatePrompt) -- i.e. before any shell command string is
    # ever constructed.
    segs_idx = raw.index("const SEGS = Array.isArray(args)")
    guard_idx = raw.index("SEG_ID_RE")
    translate_prompt_idx = raw.index("function translatePrompt(")
    assert segs_idx < guard_idx < translate_prompt_idx


def _wrap_for_execution(js_source: str) -> str:
    """Wraps the real, substituted template body the same way the Workflow
    tool that actually executes this file must -- an async function whose
    parameters ARE the `agent`/`pipeline`/`log`/`args` globals (see
    tests/batch_size_estimator.test.py's own identical helper; duplicated
    here rather than imported, per this directory's self-contained-test-file
    convention)."""
    assert js_source.count("export const meta") == 1
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


HARNESS_TEMPLATE = r"""
'use strict';

__WRAPPED_SOURCE__

const SEGS_ARGS = __SEGS_JSON__;
const callsLog = [];
const logLines = [];
let pipelineCalled = false;

async function agent(promptText, opts) {
  callsLog.push((opts && opts.label) || "");
  throw new Error("agent() should not have been called by this test");
}

async function pipeline(items, stage1, stage2) {
  pipelineCalled = true;
  throw new Error("pipeline() should not have been called by this test");
}

function log(msg) { logLines.push(String(msg)); }

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, SEGS_ARGS);
    process.stdout.write(JSON.stringify({
      result: result,
      calls: callsLog,
      log: logLines,
      pipelineCalled: pipelineCalled,
    }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.message || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def instantiate_mass_translate(*, batch_agent_cap: int) -> str:
    """Same one-time substitution contract every sibling test file
    re-implements. `batch_agent_cap` is the only value these tests care
    about -- deliberately set to 0 by the "safe id" tests below so the
    batch-too-large gate trips immediately AFTER the seg-id guard runs,
    without ever needing a real agent()/pipeline() mock. {{RUN_ID}} (CONTRACT
    -1.2.0-reliability.md sec2, a NEW documented substitution token the
    1.2.0 reliability build added to this template) is substituted here with
    a stable, colon-free, allowlist-legal fixture value purely so the
    "no leftover {{...}}" assertion below stays meaningful -- this file's
    own SEG_ID-guard assertions never read or depend on RUN_ID's value."""
    text = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", "/fixture/project/durable_root")
    text = text.replace("{{RUN_ID}}", "20260710T000000Z")
    text = text.replace("{{SOURCE_LANG}}", "fr")
    text = text.replace("{{TARGET_LANG}}", "ru")
    text = text.replace("{{MAX_FIX_ROUNDS}}", "2")
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    text = text.replace("{{VERSE_POLICY_INSTRUCTION_BLOCK}}", "Render literally.")
    # 1.4.7 (#198): the driver's codex-companion path, spliced into
    # `const COMPANION = {{...}};` as a JSON string literal. This file's
    # SEG_ID-guard assertions never read its value; it only needs to be a valid
    # JS literal so the "no leftover {{...}}" assertion below stays meaningful.
    text = text.replace("{{CODEX_COMPANION_PATH_JSON}}", '"/fake/codex-companion.mjs"')
    # #197 -- engine.effort/engine.model. Neither is read by this file's
    # SEG_ID-guard assertions; they only need to resolve.
    text = text.replace("{{EFFORT}}", "high")
    text = text.replace("{{MODEL}}", "")
    assert "{{" not in text
    return text


def run_guard_harness(tmp_path: Path, segs: list, batch_agent_cap: int = 0, timeout: int = 30):
    assert NODE is not None, "node executable not found on PATH -- required to run this test file"
    js_source = instantiate_mass_translate(batch_agent_cap=batch_agent_cap)
    wrapped = _wrap_for_execution(js_source)
    text = HARNESS_TEMPLATE.replace("__WRAPPED_SOURCE__", wrapped)
    text = text.replace("__SEGS_JSON__", json.dumps(segs))
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(text, encoding="utf-8")
    return subprocess.run([NODE, str(harness_path)], capture_output=True, text=True, timeout=timeout)


pytestmark_node = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; no hard Node.js dependency for this plugin otherwise "
    "(matches tests/batch_size_estimator.test.py's own stance)",
)


@pytestmark_node
@pytest.mark.parametrize("unsafe_seg", UNSAFE_SEG_IDS)
def test_workflow_js_guard_throws_on_unsafe_seg(tmp_path, unsafe_seg):
    proc = run_guard_harness(tmp_path, [unsafe_seg])

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "Unsafe segment id" in proc.stderr
    # Never reaches agent()/pipeline() -- the throw happens before any
    # shell command string is ever built.
    assert proc.stdout.strip() == ""


@pytestmark_node
@pytest.mark.parametrize("safe_seg", SAFE_SEG_IDS)
def test_workflow_js_guard_passes_safe_seg_and_continues(tmp_path, safe_seg):
    # batch_agent_cap=0 forces the very next gate (batch-too-large) to trip
    # immediately after the seg-id guard, without ever calling agent()/
    # pipeline() -- proving the guard did NOT throw for a safe id, while
    # staying cheap (no PLAN/mock machinery needed).
    proc = run_guard_harness(tmp_path, [safe_seg], batch_agent_cap=0)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = json.loads(proc.stdout)
    assert out["calls"] == []
    assert out["pipelineCalled"] is False
    assert out["result"]["reason"] == "batch-too-large"


@pytestmark_node
def test_workflow_js_guard_rejects_non_string_element():
    """SEGS is normally produced by select_segments.py's own JSON emission,
    always strings -- but the guard's `typeof s !== "string"` branch is the
    only thing standing between a malformed args array (e.g. a stray number
    or null) and a shell command built from `undefined`/`null`."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        proc = run_guard_harness(Path(tmp), [123])
        assert proc.returncode != 0
        assert "Unsafe segment id" in proc.stderr


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
