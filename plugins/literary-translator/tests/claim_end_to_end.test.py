"""tests/claim_end_to_end.test.py -- #438's own "#1 required test" (PLAN.md
"Tests -- required in the first draft"): a claim through the ACTUAL selector
JSON, into the driver, into the ACTUAL rendered reviewer prompt, through
codex_job.py/review_ready.py, to ledger_update.py and ledger_merge.py --
asserting the draft's own bytes are UNCHANGED apart from the token.

This closes the producer/consumer seam the plan calls out by name: a
producer and a consumer each passing against their own fixture is the known
failure mode ("the selector emitted `claims` as a dict, the driver initially
required a list"). Every hop below drives the REAL, currently-shipped
script/function it names -- never a paraphrase, never a hand-rolled stand-in
-- so a shape mismatch anywhere in the chain shows up here even when each
component's own dedicated suite (select_segments.test.py, claim_selector.
test.py, claim_driver.test.py, claim_prompt_contract.test.py,
resume_integrity.test.py) already passes in isolation against ITS OWN
fixture.

Explicitly NOT retested here (each has its own dedicated file, per this
project's "no shared lib between self-contained scripts" convention and its
mirror, "no shared test coverage between dedicated test files"): the full
D1-D6 admission-condition matrix (claim_selector.test.py), parse_claims_
field()'s own exhaustive malformation matrix and D8's driver-side refusal
(claim_driver.test.py), D9's fix-round token-preservation prompt line
(claim_prompt_contract.test.py), or resume_setup.py's D1a ordering property
(claim_run_ordering.test.py). This file's own job is narrower and specific:
prove the WIRES between those pieces carry the REAL shape end to end.

## History: a gap this file found, then watched close

This file's first revision found and documented a real gap: at the time it
was written, select_segments.py's own claim-admission write loop
deliberately did NOT call rewrite_draft_dispatch_token() -- three
independent confirmations (driver, team-lead, and this file's own author,
via this exact E2E trace) landed on the same defect from different
directions. Without the rewrite, `safe_adopt()`'s own `draft_ready.py
--expect-token` check fails first, so `_refuse_claimed_translate()` (D8)
fires on a segment whose draft is perfectly healthy -- the guard refusing
precisely the case its own behaviour table says must ADOPT.

D1a settled on a SINGLE-PHASE claim (record first, dispatch_token rewrite
second, both inside one invocation -- the two-write ordering is
safety-critical: a crash between them must leave the draft on its OLD
token plus a durable record, never a re-stamped draft with no record) once
the actual defect was traced to the #409 Step 3 evidence gate scanning
LIVE rather than against a snapshot taken before the claim block's own
writes. `selector` wired the rewrite in at select_segments.py's claim
write loop (`rewrite_draft_dispatch_token()`, called immediately after the
claim record write) once that fix landed. Part B below now asserts the
CLOSED state directly: Part A's real `select_segments.py` call already
re-stamps the draft, with no separate step needed -- this test's own
`git log` is the record that it once asserted the opposite, on purpose,
until the day it didn't.

## Fixture strategy

One P2 (--from-cap) population, built with the same shape claim_selector.
test.py's own build_from_cap_segment() uses (duplicated, not imported, per
house convention) -- a real, valid, non-clean-with-findings review, a real
draft/segpack pair that actually passes S1 (validate_draft.py) and S2
(draft_ready.py structural check) for real, and a manifest of one segment.
--from-cap is chosen over --from-converged because it needs no
.ever_converged sentinel bookkeeping, keeping the fixture's own moving
parts to the minimum this file's OWN subject (the wiring, not the admission
matrix) needs.

Every script this file drives is the REAL, currently-shipped one, copied
(select_segments.py, ledger_merge.py, ledger_update.py, draft_ready.py,
validate_draft.py, review_ready.py, draft_sha1.py, claim_record.py) or
loaded in-process from its real on-disk path (segment_dispatch_driver.py's
parse_claims_field(), select_segments.py's own rewrite_draft_dispatch_
token(), codex_job.py's CodexJob.safe_adopt() -- the latter two loaded from
their REAL, un-copied location so codex_job.py's own _trusted_scripts_dir()
default resolves to the REAL, currently-shipped draft_ready.py/
validate_draft.py, exercising production code on both ends of that gate,
not a copy of it). Only cache_key.py is stubbed (same fixture stand-in
select_segments.test.py/claim_selector.test.py/ledger_merge.test.py already
use), since its own 15-field hashing algorithm has its own dedicated test
file.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC = ASSETS_DIR / "schemas"
TEMPLATES_SRC_DIR = ASSETS_DIR / "templates"

SELECT_SCRIPT_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
LEDGER_UPDATE_SRC = SCRIPTS_SRC_DIR / "ledger_update.py"
DRAFT_READY_SRC = SCRIPTS_SRC_DIR / "draft_ready.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
REVIEW_READY_SRC = SCRIPTS_SRC_DIR / "review_ready.py"
DRAFT_SHA1_SRC = SCRIPTS_SRC_DIR / "draft_sha1.py"
CLAIM_RECORD_SRC = SCRIPTS_SRC_DIR / "claim_record.py"
CODEX_JOB_SRC = SCRIPTS_SRC_DIR / "codex_job.py"
SEGMENT_DISPATCH_DRIVER_SRC = SCRIPTS_SRC_DIR / "segment_dispatch_driver.py"
MASS_TRANSLATE_TEMPLATE_SRC = TEMPLATES_SRC_DIR / "mass-translate-wf.template.js"

for _src in (
    SELECT_SCRIPT_SRC, LEDGER_MERGE_SRC, LEDGER_UPDATE_SRC, DRAFT_READY_SRC,
    VALIDATE_DRAFT_SRC, REVIEW_READY_SRC, DRAFT_SHA1_SRC, CLAIM_RECORD_SRC,
    CODEX_JOB_SRC, SEGMENT_DISPATCH_DRIVER_SRC, MASS_TRANSLATE_TEMPLATE_SRC,
):
    assert _src.is_file(), f"required sibling script not found at {_src}"
assert SCHEMAS_SRC.is_dir(), f"schemas dir not found at {SCHEMAS_SRC}"

NODE = shutil.which("node")
_needs_node = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; the reviewer-prompt rendering assertion "
    "executes mass-translate-wf.template.js's own reviewDispatchPrompt() "
    "under Node (no hard Node.js dependency for this plugin otherwise)",
)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Loaded once from the REAL, un-copied source -- parse_claims_field() is a
# pure function (no fixture root needed to load it), and loading codex_job.py
# from its real path lets CodexJob._trusted_scripts_dir()'s own default
# (self.plugin_root is None) resolve to the REAL, currently-shipped
# assets/scripts/ -- so safe_adopt() below exercises production draft_ready.py/
# validate_draft.py, not a copy of them.
DRIVER = _load_module(SEGMENT_DISPATCH_DRIVER_SRC, "segment_dispatch_driver_e2e")
CODEX_JOB_MOD = _load_module(CODEX_JOB_SRC, "codex_job_e2e")
# rewrite_draft_dispatch_token() takes durable_root as an explicit argument
# and touches nothing self-anchored, so the real, un-copied source is loaded
# directly here too, exactly as whichever future call site wires it in will
# call the shipped function.
SELECT_MOD = _load_module(SELECT_SCRIPT_SRC, "select_segments_e2e")

DriverError = DRIVER.DriverError

CACHE_KEY_FIELDS = [
    "input_sha1", "style_contract_hash", "used_terms_hash", "pipeline_version",
    "schema_hash", "prompt_hash", "agent_config_hash", "profile_semantics_hash",
    "particle_config_hash", "source_extraction_hash", "source_input_hash",
    "derivation_bundle_hash", "verse_map_hash", "note_map_hash", "plugin_bundle_hash",
]

# Same fixture stand-in for cache_key.py that select_segments.test.py/
# claim_selector.test.py/ledger_merge.test.py already use.
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    parser.add_argument("--field")
    parser.add_argument("--durable-root", default=None)
    args = parser.parse_args()
    if args.durable_root:
        durable_root = Path(args.durable_root).resolve()
    else:
        durable_root = Path(__file__).resolve().parent.parent
    keys_path = durable_root / "test_fixture_cache_keys.json"
    if not args.seg:
        sys.stderr.write("fake cache_key.py: test stub requires --seg\\n")
        return 1
    data = json.loads(keys_path.read_text(encoding="utf-8"))
    if args.seg not in data:
        sys.stderr.write(f"fake cache_key.py: no fixture key for {args.seg}\\n")
        return 1
    print(json.dumps(data[args.seg]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

# validate_draft.py's own load_profile()/ProfileConfig requires all three
# sections -- verbatim copy of tests/validate_draft.test.py's DEFAULT_PROFILE
# (a proven-good fixture), duplicated per house convention.
DEFAULT_PROFILE = {
    "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}

FN_PH = "\u27e6FNREF_1\u27e7"
V_PH_A = "\u27e6VERSE_vA\u27e7"
V_PH_B = "\u27e6VERSE_vB\u27e7"

RUN_ID = "20260812T000000Z"
SOURCE_RUN_ID = "20260801T090000Z"


# ---------------------------------------------------------------------------
# Fixture harness (duplicated from claim_selector.test.py's own
# make_durable_root/build_from_cap_segment, extended with the extra siblings
# this file's own longer chain needs: ledger_update.py, review_ready.py,
# draft_sha1.py).
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name, src in (
        ("select_segments.py", SELECT_SCRIPT_SRC),
        ("ledger_merge.py", LEDGER_MERGE_SRC),
        ("ledger_update.py", LEDGER_UPDATE_SRC),
        ("draft_ready.py", DRAFT_READY_SRC),
        ("validate_draft.py", VALIDATE_DRAFT_SRC),
        ("review_ready.py", REVIEW_READY_SRC),
        ("draft_sha1.py", DRAFT_SHA1_SRC),
        ("claim_record.py", CLAIM_RECORD_SRC),
    ):
        shutil.copy2(src, scripts_dir / name)
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    shutil.copytree(SCHEMAS_SRC, root / "schemas")

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    profile_path = root / "profile.yml"
    profile_path.write_text(yaml.safe_dump(DEFAULT_PROFILE, sort_keys=False), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )
    write_canon(root, {})
    return root


def write_manifest(root, seg_ids):
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": s} for s in seg_ids]}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_fragment(root, seg, record):
    frag_path = root / "runs" / "ledger.d" / f"{seg}.json"
    frag_path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def write_fixture_cache_keys(root, mapping):
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
    )


def write_canon(root, entries):
    (root / "canon.json").write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")


def make_cache_key(seed):
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def draft_content_sha1_of(doc: dict) -> str:
    """Independent ground-truth reimplementation of draft_content_sha1()
    (projects out dispatch_token, sorted-key compact-separator canonical
    JSON) -- an oracle, deliberately not pinned against production, matching
    claim_selector.test.py's own copy of this same helper."""
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    raw = json.dumps(projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def clean_segpack(seg):
    return {
        "seg": seg,
        "blocks": [
            {"id": "p1", "order_index": 0, "source_html": f"<p>Some prose with a note {FN_PH} attached.</p>"},
            {"id": "vblockA", "order_index": 1, "source_html": "<p>Premiere ligne<br/>Deuxieme ligne</p>"},
            {"id": "vblockB", "order_index": 2, "source_html": "<p>Autre premiere<br/>Autre deuxieme</p>"},
        ],
        "footnotes": [{"n": 1, "source_text": "Une note en francais."}],
        "verses": [
            {"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"},
            {"vid": "vB", "placeholder": V_PH_B, "parent_block": "vblockB"},
        ],
        "names": [],
        "canon_names": [],
        "new_names": [],
        "canon_map": {},
        "generation_hashes": {
            "source_extraction_hash": "sxh-0",
            "source_input_hash": "sih-0",
            "particle_config_hash": "pch-0",
            "derivation_bundle_hash": "dbh-0",
        },
    }


def clean_draft(seg):
    return {
        "seg": seg,
        "blocks": {
            "p1": f"Some translated prose with a note {FN_PH} attached.",
            "vblockA": V_PH_A,
            "vblockB": V_PH_B,
        },
        "footnotes": {"1": "A translated note in English."},
        "verses": {
            "vA": {
                "rendered": "First line rendered so\nSecond line rendered so",
                "literal_gloss": "The first line means one thing, the second means another",
            },
            "vB": {
                "rendered": "Another line rendered here\nAnother second line here",
                "literal_gloss": "This gloss says something different from the rendering above",
            },
        },
        "names": [],
        "notes": [],
    }


def write_segpack(root, seg, segpack):
    (root / "segments" / f"segpack_{seg}.json").write_text(json.dumps(segpack, ensure_ascii=False), encoding="utf-8")


def write_draft_doc(root, seg, draft):
    (root / "segments" / f"{seg}.draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")


def read_draft_doc(root, seg):
    return json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))


def write_review(root, seg, review):
    (root / "segments" / f"{seg}.review.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")


def make_run_dir(root, run_id):
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    digest_path = run_dir / "input.digest"
    if not digest_path.exists():
        digest_path.write_text(json.dumps({"digest": f"stub-{run_id}"}), encoding="utf-8")


def build_from_cap_segment(root, seg, fixture_keys: dict, *, source_run_id=SOURCE_RUN_ID):
    """P2 shape (POPULATIONS.md), verbatim copy of claim_selector.test.py's
    own build_from_cap_segment(): non_converged/reason=cap, NO sentinel, a
    stored review clean:false with findings, human_escalation -- reachable
    only via --only-segs."""
    segpack = clean_segpack(seg)
    write_segpack(root, seg, segpack)
    write_canon(root, {})

    draft = clean_draft(seg)
    draft["blocks"] = dict(draft["blocks"])
    draft["blocks"]["p1"] = draft["blocks"]["p1"] + " Hand-fixed after the cap."
    draft["dispatch_token"] = f"{source_run_id}:{seg}"
    write_draft_doc(root, seg, draft)

    make_run_dir(root, source_run_id)

    ck = make_cache_key(seg)
    fixture_keys[seg] = ck

    review = {
        "clean": False,
        "coverage_ok": True,
        "findings": [{"loc": "p1", "severity": "medium", "issue": "awkward phrasing", "suggest": "rephrase"}],
        "draft_sha1": "0" * 40,
    }
    write_review(root, seg, review)

    frag = {"timestamp": "2026-01-01T00:00:00Z", "status": "non_converged", "reason": "cap", "rounds": 4}
    write_fragment(root, seg, frag)


def run_select(root, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "select_segments.py"), *extra_args],
        capture_output=True, text=True, timeout=timeout, cwd=str(root),
    )


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def compute_real_draft_sha1(root, seg, timeout=30):
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "draft_sha1.py"), seg],
        capture_output=True, text=True, cwd=str(root), timeout=timeout,
    )
    assert proc.returncode == 0, (
        f"draft_sha1.py failed while building a test fixture for seg {seg!r}: "
        f"rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return proc.stdout.strip()


def run_review_ready(root, seg, expect_token, timeout=30):
    cmd = [sys.executable, str(root / "scripts" / "review_ready.py"), seg, "--expect-token", expect_token]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), timeout=timeout)


def write_payload_file(root, name, payload):
    path = root / "runs" / f".payload_{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def run_ledger_update(root, seg, payload_path, timeout=30):
    cmd = [sys.executable, str(root / "scripts" / "ledger_update.py"), seg, "--payload-file", str(payload_path)]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), timeout=timeout)


def run_ledger_merge(root, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "ledger_merge.py"), *extra_args],
        capture_output=True, text=True, cwd=str(root), timeout=timeout,
    )


def parse_one_json_line(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return json.loads(lines[0])


# ---------------------------------------------------------------------------
# JS harness for the ACTUAL rendered reviewDispatchPrompt() -- a minimal,
# purpose-built wrap (distinct from claim_prompt_contract.test.py's HARNESS,
# which drives the whole mocked pipeline): this only needs ONE builder
# function's return value, so the wrapped source's tail is a direct call and
# return, not a mocked agent()/pipeline() run.
# ---------------------------------------------------------------------------

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_SOURCE_LANG = "fr"
FIXTURE_TARGET_LANG = "en"
FIXTURE_VERSE_POLICY = "Render every verse literally, line by line."
FIXTURE_COMPANION_PATH = "/opt/codex/1.0.10/codex-companion.mjs"
FIXTURE_EFFORT = "high"


def instantiate_template(run_id: str) -> str:
    text = MASS_TRANSLATE_TEMPLATE_SRC.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT)
    text = text.replace("{{RUN_ID}}", run_id)
    text = text.replace("{{SOURCE_LANG}}", FIXTURE_SOURCE_LANG)
    text = text.replace("{{TARGET_LANG}}", FIXTURE_TARGET_LANG)
    text = text.replace("{{MAX_FIX_ROUNDS}}", "3")
    text = text.replace("{{BATCH_AGENT_CAP}}", "100000")
    text = text.replace("{{MAX_CODEX_JOBS_PER_BATCH}}", "100000")
    text = text.replace("{{VERSE_POLICY_INSTRUCTION_BLOCK}}", json.dumps(FIXTURE_VERSE_POLICY)[1:-1])
    text = text.replace("{{CODEX_COMPANION_PATH_JSON}}", json.dumps(FIXTURE_COMPANION_PATH))
    text = text.replace("{{EFFORT}}", FIXTURE_EFFORT)
    text = text.replace("{{MODEL}}", "")
    text = text.replace("{{PLUGIN_ROOT}}", json.dumps(""))
    assert "{{" not in text, "fixture instantiation left an unresolved token"
    return text


def _wrap(js_source: str) -> str:
    assert js_source.count("export const meta") == 1
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


# Drives the REAL pipeline() loop for real (unlike a direct call to
# reviewDispatchPrompt(), which is unreachable from outside __workflowMain__'s
# own scope and, more importantly, is not what actually reaches agent(): the
# text passed to agent() for the "review-dispatch:SEG:r1" label is
# reviewDrivePrompt(seg, roundLabel)'s own output, which calls
# reviewDispatchPrompt() internally and embeds its FULL text verbatim inside
# a bash heredoc -- so capturing that label's promptText, exactly as
# claim_prompt_contract.test.py's Part 3 harness already does, is the only
# way to observe the ACTUAL rendered reviewDispatchPrompt() text, not a
# reimplementation of it). The mocked review-read response for round 1 is
# CLEAN, so the run converges after exactly one round -- this harness only
# needs the round-1 DISPATCH prompt, never a fix round.
E2E_HARNESS = r"""
'use strict';
__WRAPPED_SOURCE__

const SEG = __SEG_JSON__;
const SEGS_ARGS = [SEG];
const CLEAN_REVIEW = { clean: true, coverage_ok: true, findings: [], draft_sha1: "a".repeat(40) };
const callsLog = [];
const promptByLabel = {};

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  promptByLabel[label] = promptText;
  callsLog.push({ label: label });

  if (label.indexOf("ledger:") === 0) {
    let status = "blocked";
    if (label.indexOf(":in_progress:") !== -1) status = "in_progress";
    else if (label.indexOf(":converged:") !== -1) status = "converged";
    else if (label.indexOf(":cap:") !== -1) status = "non_converged";
    return { success: true, status: status, fragment_path: "/x/" + SEG + ".json", fragment_sha1: "d" };
  }
  if (label === "merge-ledger") {
    return { success: true, ledger_path: "/x/l.json", n_segments: 1, missing_segments: [], stale_segments: [] };
  }
  if (label === "translate:" + SEG) return "DISPATCHED " + SEG + " a1b2c3d4";
  if (label === "review-dispatch:" + SEG + ":r1") return "DISPATCHED " + SEG + " beef1234";
  if (label === "wait:" + SEG) return "READY " + SEG;
  if (label === "review-wait:" + SEG + ":r1") return "READY " + SEG;
  if (label === "review-read:" + SEG + ":r1") return CLEAN_REVIEW;
  if (label === "artifact-check:" + SEG + ":r1") return { match: true };
  if (label === "draft-probe:" + SEG) return { present: true };
  throw new Error("mock agent(): unrecognized label " + JSON.stringify(label));
}

async function pipeline(items, stage1, stage2) {
  const out = [];
  for (const item of items) {
    const r1 = await stage1(item);
    out.push(await stage2(r1, item));
  }
  return out;
}
function log() {}

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, SEGS_ARGS);
    process.stdout.write(JSON.stringify({ result: result, calls: callsLog, promptByLabel: promptByLabel }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.message || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def render_review_dispatch_prompt(tmp_path, seg: str, run_id: str, timeout=30) -> str:
    """Runs the REAL template's pipeline() for real (round 1 converges
    clean) and returns the ACTUAL text passed to agent() for the
    "review-dispatch:{seg}:r1" label -- reviewDrivePrompt()'s own output,
    which embeds reviewDispatchPrompt(seg, "1")'s full text verbatim as a
    bash heredoc body. Asserting against THIS is asserting against the real
    rendered reviewer prompt, not a paraphrase of it."""
    src = instantiate_template(run_id)
    harness = (
        E2E_HARNESS.replace("__WRAPPED_SOURCE__", _wrap(src))
        .replace("__SEG_JSON__", json.dumps(seg))
    )
    p = tmp_path / "claim_e2e_review_prompt_harness.js"
    p.write_text(harness, encoding="utf-8")
    assert NODE is not None
    proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 0, (
        f"harness run failed for seg={seg!r} (rc={proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    out = json.loads(proc.stdout)
    label = "review-dispatch:" + seg + ":r1"
    assert label in out["promptByLabel"], (
        f"expected the harness to reach the round-1 review-dispatch call; "
        f"labels actually seen: {sorted(out['promptByLabel'].keys())}"
    )
    return out["promptByLabel"][label]


# ---------------------------------------------------------------------------
# The end-to-end test.
# ---------------------------------------------------------------------------

@_needs_node
def test_claim_end_to_end_real_selector_through_real_ledger_merge_preserves_draft_bytes(tmp_path):
    """The plan's #1 required test, driven hop by hop against the REAL
    script/function at every step -- see this file's own module docstring
    for the full rationale and what is deliberately out of scope."""
    root = make_durable_root(tmp_path)
    seg = "seg01"
    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)

    # Baseline: the draft's own CONTENT sha1 (dispatch_token projected out),
    # computed BEFORE any claim touches it -- the value every assertion
    # below is measured against, right through to the very end of the
    # chain.
    baseline_content_sha1 = draft_content_sha1_of(read_draft_doc(root, seg))

    # Regression-catcher for the oracle itself (matches draft_sha1.py's own
    # test convention: prove the stability assertions below aren't
    # vacuously true because the hash ignores content). A one-field content
    # mutation must move the hash.
    _mutated = dict(read_draft_doc(root, seg))
    _mutated["blocks"] = dict(_mutated["blocks"], p1=_mutated["blocks"]["p1"] + " MUTATED")
    assert draft_content_sha1_of(_mutated) != baseline_content_sha1, (
        "draft_content_sha1_of() must react to a real content change -- "
        "every 'unchanged apart from the token' assertion below depends on "
        "this oracle actually discriminating"
    )

    # =======================================================================
    # Part A -- the REAL selector JSON's `claims` field -> the REAL driver's
    # parse_claims_field(). D3's own producer/consumer seam.
    # =======================================================================
    # --run-resume "false": RUN_ID here is a freshly-minted id (this test
    # never calls resume_setup.py), never a resumed one -- select_segments.py
    # now requires the pair together (D1a's #409 Step 3 fix, landed mid-flight
    # under this file; see its own --run-resume help text).
    proc = run_select(root, "--only-segs", seg, "--from-cap", seg, "--run-id", RUN_ID, "--run-resume", "false")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    out = parse_stdout(proc)
    assert out.get("success", True) is not False, out
    assert isinstance(out.get("claims"), dict), (
        f"the wire shape is settled as a DICT keyed by seg -- got {type(out.get('claims'))}: {out.get('claims')!r}"
    )
    assert seg in out["claims"], out

    extracted = DRIVER.parse_claims_field(out, out["segs"])
    assert extracted == {seg: "from-cap"}, (
        f"the REAL selector output must extract cleanly through the REAL driver "
        f"function into {{seg: profile}}. Got: {extracted}"
    )

    # Negative control: the EXACT historical bug class the plan calls out by
    # name ("the selector emitted a dict, the driver initially required a
    # list") -- reproduced by mutating the REAL selector output's own
    # `claims` field into a list and re-running it through the REAL parser.
    list_shaped_result = dict(out, claims=[dict(out["claims"][seg], seg=seg)])
    with pytest.raises(DriverError) as exc_info:
        DRIVER.parse_claims_field(list_shaped_result, out["segs"])
    assert "not a JSON object" in str(exc_info.value), (
        f"a list-shaped claims field must be refused fail-closed, naming the "
        f"shape problem. Got: {exc_info.value}"
    )

    # =======================================================================
    # Part B -- the feature closed: admission itself (Part A's real
    # select_segments.py call, above) now performs BOTH the durable
    # claim-record write AND the draft's dispatch_token rewrite, in that
    # order, inside the SAME invocation (select_segments.py:2423-2439 --
    # landed by `selector` after this test's first revision pinned the
    # opposite as a characterization; D1a's normative order is record
    # first, rewrite second, both before this function ever returns).
    # =======================================================================
    new_token = f"{RUN_ID}:{seg}"
    after_admission_doc = read_draft_doc(root, seg)
    assert after_admission_doc["dispatch_token"] == new_token, (
        "claim admission must re-stamp the draft's own dispatch_token to "
        "this run's value as part of the SAME invocation that wrote the "
        "claim record -- got "
        f"{after_admission_doc['dispatch_token']!r}, expected {new_token!r}"
    )
    assert draft_content_sha1_of(after_admission_doc) == baseline_content_sha1, (
        "the rewrite must touch ONLY dispatch_token -- every other byte of "
        "content must be exactly what it was before the claim touched it"
    )

    # Negative control proving the assertion above is not vacuous: a
    # CodexJob built for the OLD source-run token no longer matches what is
    # actually on disk -- the draft genuinely moved, this is not a
    # bookkeeping-only claim record with the draft untouched.
    job_stale_token = CODEX_JOB_MOD.CodexJob(
        kind="translate", seg=seg, tok=f"{SOURCE_RUN_ID}:{seg}", disp="d0", root=str(root),
        companion="/fake/codex-companion.mjs", prompt_text="", prompt_file=str(root / "unused_prompt.txt"),
        deadline_sec=60, poll_sec=1, effort="high", node="node", run_id=RUN_ID,
    )
    assert job_stale_token.safe_adopt() is False, (
        "the OLD source-run token must no longer match the draft on disk "
        "post-claim -- if it still does, the rewrite above did not really run"
    )

    # Positive control: a CodexJob for the CURRENT run's own token adopts
    # the already-claimed draft cleanly, with NO extra step -- against the
    # REAL, currently-shipped draft_ready.py/validate_draft.py.
    job_current_token = CODEX_JOB_MOD.CodexJob(
        kind="translate", seg=seg, tok=new_token, disp="d1", root=str(root),
        companion="/fake/codex-companion.mjs", prompt_file=str(root / "unused_prompt.txt"), prompt_text="",
        deadline_sec=60, poll_sec=1, effort="high", node="node", run_id=RUN_ID,
    )
    assert job_current_token.safe_adopt() is True, (
        "a healthy, admitted claimed draft must safely adopt under the "
        "run's own expected token immediately after admission, with no "
        "separate rewrite step required"
    )

    # =======================================================================
    # Part C -- idempotency (D9): re-applying rewrite_draft_dispatch_token()
    # directly (the SAME function select_segments.py's admission loop just
    # called in-process, loaded from its real source) is a no-op, not a
    # second authorization or an error -- a re-claim in the same run must
    # not be mistaken for one.
    # =======================================================================
    ok2, detail2 = SELECT_MOD.rewrite_draft_dispatch_token(seg, root, new_token)
    assert ok2, detail2
    idempotent_doc = read_draft_doc(root, seg)
    assert idempotent_doc["dispatch_token"] == new_token
    assert draft_content_sha1_of(idempotent_doc) == baseline_content_sha1

    # =======================================================================
    # Part D -- the ACTUAL rendered reviewer prompt agrees with what is
    # actually on disk: reviewDispatchPrompt() hardcodes the draft token it
    # demands (RUN_ID:seg) independently of anything this test wired up --
    # it must equal the token the claim chain above actually produced.
    # =======================================================================
    prompt_text = render_review_dispatch_prompt(tmp_path, seg, RUN_ID)
    assert json.dumps(new_token) in prompt_text, (
        f"the ACTUAL rendered reviewDispatchPrompt() must demand exactly the "
        f"token the claim chain re-stamped onto the draft ({new_token!r}) -- "
        f"a divergence here means the template's own RUN_ID and the claim's "
        f"run_id have silently come apart. Prompt:\n{prompt_text}"
    )
    expected_review_token = f"{new_token}:r1"
    assert json.dumps(expected_review_token) in prompt_text, (
        f"the prompt must also demand the round-1 review token "
        f"{expected_review_token!r} for its own dispatch_token field"
    )
    # Discrimination check: a DIFFERENT run's token must NOT appear -- proves
    # the two containment assertions above are not trivially true against a
    # prompt that happens to mention every plausible token.
    wrong_token = f"{SOURCE_RUN_ID}:{seg}"
    assert json.dumps(wrong_token) not in prompt_text, (
        f"the prompt must not carry the OLD source run's token "
        f"({wrong_token!r}) -- if it does, the containment assertions above "
        f"are not discriminating"
    )

    # =======================================================================
    # Part E -- review_ready.py -> ledger_update.py -> ledger_merge.py, a
    # genuine convergence write, ending on the SAME draft-content-sha1
    # assertion this test opened with.
    # =======================================================================
    real_draft_sha1 = compute_real_draft_sha1(root, seg)
    write_review(root, seg, {
        "clean": True,
        "coverage_ok": True,
        "findings": [],
        "draft_sha1": real_draft_sha1,
        "dispatch_token": expected_review_token,
    })
    ready_proc = run_review_ready(root, seg, expect_token=expected_review_token)
    assert ready_proc.returncode == 0, (
        f"review_ready.py must accept the round-1 review carrying the token "
        f"the actual rendered prompt itself demanded. "
        f"stdout={ready_proc.stdout}\nstderr={ready_proc.stderr}"
    )

    ledger_payload = {
        "status": "converged",
        "rounds": 1,
        "cache_key": fixture_keys[seg],
        "run_token": RUN_ID,
    }
    payload_path = write_payload_file(root, "e2e", ledger_payload)
    update_proc = run_ledger_update(root, seg, payload_path)
    update_out = parse_one_json_line(update_proc)
    assert update_out.get("success") is True, (
        f"ledger_update.py must record convergence for the re-claimed, "
        f"re-reviewed segment. Got: {update_out}"
    )

    merge_proc = run_ledger_merge(root, "--expected-segs", seg, "--run-token", RUN_ID, "--skip-stale-check")
    merge_out = parse_one_json_line(merge_proc)
    assert merge_out.get("success") is True, (
        f"ledger_merge.py's own batch-final token/sha re-check must also "
        f"accept the re-claimed segment. Got: {merge_out}"
    )

    # The load-bearing assertion this whole file exists to make: after the
    # FULL real chain -- admission, re-stamp, adoption, the actual rendered
    # reviewer prompt, review_ready.py, ledger_update.py, ledger_merge.py --
    # the draft's own CONTENT is byte-for-byte what it was before any of
    # this touched it. Only dispatch_token ever changed.
    final_doc = read_draft_doc(root, seg)
    assert final_doc["dispatch_token"] == new_token
    assert draft_content_sha1_of(final_doc) == baseline_content_sha1, (
        "the draft's own bytes must be unchanged apart from the token, end "
        "to end through the entire real claim chain"
    )
