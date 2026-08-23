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

## The two hops, and why calling the functions was not enough

The first hop (test_claim_end_to_end_real_selector_through_real_ledger_merge_
preserves_draft_bytes) drives select_segments.py for real and then calls
parse_claims_field() and CodexJob.safe_adopt() as FUNCTIONS. That proves the
shapes fit. It cannot prove anything about the ORCHESTRATION meant to hand
them to each other -- and did not: while this file was green, `--from-cap`
was completely non-functional through segment_dispatch_driver.py, whose run()
forwarded the claim flags with run_id=None into a selector that fatals on
exactly that combination. A test that calls the pieces can never fail on the
wire between them, which is the same producer/consumer trap this file's own
opening paragraph claims to close.

So the second hop, at the bottom of this file, drives the driver's own CLI
end to end -- and it is the only place in the suite that does so over the
REAL draft_ready.py/validate_draft.py (tests/segment_dispatch_driver.test.py
has its own claim end-to-end test, but its subject is the driver's logic, so
its leaf gates are fakes that only check a file exists). It also asserts the
record-first ORDER on disk rather than merely observing that both writes
landed, which says nothing about which landed first.

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

The orchestration hop adds two more fakes and no others: resolve_codex_
companion.py (which would otherwise go looking for a real codex install on
this machine) and codex_job.py itself -- the ONE genuinely unfakeable leaf,
since a real invocation spends a real paid codex turn against a real model.
That fake accepts the REAL argv shape, logs the raw argv it was handed, and
writes the artifact a successful turn would have produced. Everything it
stands in for is covered against the real CodexJob in tests/codex_job_
driver.test.py and tests/claim_chokepoint.test.py.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from _workflow_instantiation import instantiate_mass_translate  # noqa: E402

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

def make_durable_root(tmp_path, profile=None):
    """`profile` defaults to DEFAULT_PROFILE (the three sections the REAL
    validate_draft.py's own load_profile() requires). The driver hop at the
    bottom of this file passes a superset -- the same three sections plus
    the engine/source/target keys segment_dispatch_driver.py reads -- rather
    than a second root builder, so both hops are provably staged from the
    same tree."""
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
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(src.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    shutil.copytree(SCHEMAS_SRC, root / "schemas")

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    profile_path = root / "profile.yml"
    profile_path.write_text(
        yaml.safe_dump(profile if profile is not None else DEFAULT_PROFILE, sort_keys=False),
        encoding="utf-8",
    )
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

FIXTURE_TARGET_LANG = "en"
FIXTURE_COMPANION_PATH = "/opt/codex/1.0.10/codex-companion.mjs"
FIXTURE_EFFORT = "high"


def instantiate_template(run_id: str) -> str:
    """The token map itself now lives in _workflow_instantiation.py (#413).
    TARGET_LANG/CODEX_COMPANION_PATH_JSON/EFFORT stay overridden to this
    file's own fixture values; DURABLE_ROOT, SOURCE_LANG, MAX_FIX_ROUNDS,
    BATCH_AGENT_CAP, MAX_CODEX_JOBS_PER_BATCH, VERSE_POLICY_INSTRUCTION_BLOCK,
    MODEL and PLUGIN_ROOT are exactly the shared module's own defaults, so no
    override is needed for them here."""
    return instantiate_mass_translate(
        run_id=run_id,
        target_lang=FIXTURE_TARGET_LANG,
        codex_companion_path_json=FIXTURE_COMPANION_PATH,
        effort=FIXTURE_EFFORT,
    )


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
    # order, inside the SAME invocation (select_segments.py's claim write
    # loop calls rewrite_draft_dispatch_token() immediately after
    # write_claim_record() -- landed by `selector` after this test's first
    # revision pinned the opposite as a characterization; D1a's normative
    # order is record first, rewrite second, both before that loop's
    # iteration ends). The ORDER itself is asserted separately, and on
    # disk, by test_a_failed_token_rewrite_leaves_the_record_not_the_token
    # further down -- both outcomes landing says nothing about which
    # landed first.
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
    #
    # safe_adopt() is called deliberately in ISOLATION for both controls
    # below: what is being read out of it is one bit -- "does the token on
    # disk satisfy this run's gates" -- and driving the whole of CodexJob.run()
    # to learn it would additionally exercise the lease, the sandbox and a
    # launch against a companion path that does not exist. It is NOT standing
    # in for the D8 chokepoint or for the dispatch path: those are driven
    # through the real CodexJob.run() in tests/claim_chokepoint.test.py and
    # through the real driver CLI at the bottom of this file.
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
    #
    # `expected_content_sha1` is keyword-only and REQUIRED (it closes the
    # TOCTOU between admission and the stamp), and `baseline_content_sha1`
    # is exactly the right value to pass: it is this draft's content sha1
    # with dispatch_token projected out, computed before anything touched
    # the draft, and asserted unchanged three lines below. A no-op re-claim
    # still has to prove it is stamping the draft it gated.
    # =======================================================================
    ok2, detail2 = SELECT_MOD.rewrite_draft_dispatch_token(
        seg, root, new_token, expected_content_sha1=baseline_content_sha1
    )
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


# ---------------------------------------------------------------------------
# The ORCHESTRATION hop -- the same claim, driven through the REAL
# segment_dispatch_driver.py CLI instead of by calling its functions.
#
# WHY THIS SECTION EXISTS, stated bluntly because the gap it closes is this
# file's own: everything above drives select_segments.py directly and then
# calls parse_claims_field() and CodexJob.safe_adopt() as FUNCTIONS. That
# proves the shapes fit; it cannot prove anything about the orchestration
# that is supposed to pass them to each other -- and it did not. While this
# file was green, `--from-cap` was completely non-functional through the
# driver: run() forwarded the claim flags to select_segments.py with
# run_id=None, and the real selector fatals on exactly that combination, so
# every claim invocation exited 1 and dispatched nothing. A test that calls
# the pieces can never fail on the wire between them.
#
# tests/segment_dispatch_driver.test.py has its own claim end-to-end test and
# it is deliberately NOT duplicated here. That one stages FAKE draft_ready.py
# and validate_draft.py (its subject is the driver's own logic, so its leaf
# gates only check that a file exists, and S1/S2 pass trivially). This one
# stages the REAL ones, which is this file's whole reason for being: the
# claim seam and the real leaf gates exercised in the same invocation, which
# neither file covered before. Only the paid codex turn itself is faked -- a
# leaf, and the docstring of the fake says so.
# ---------------------------------------------------------------------------

RESUME_SETUP_SRC = SCRIPTS_SRC_DIR / "resume_setup.py"

for _src in (SEGMENT_DISPATCH_DRIVER_SRC, RESUME_SETUP_SRC):
    assert _src.is_file(), f"required sibling script not found at {_src}"

# DEFAULT_PROFILE (what the REAL validate_draft.py needs) plus exactly the
# keys segment_dispatch_driver.py's own load_engine_config()/
# load_translate_config() read. Built by extension, never by rewriting the
# three sections above, so the two hops cannot drift into gating different
# drafts.
DRIVER_PROFILE = dict(
    DEFAULT_PROFILE,
    engine={
        "max_fix_rounds": 2,
        "max_codex_jobs_per_batch": 400,
        "batch_agent_cap": 10000,
        "effort": "high",
    },
    source={"language": {"code": "fr"}},
    target={"language": {"code": "en"}},
)

FAKE_RESOLVE_CODEX_COMPANION_PY = """#!/usr/bin/env python3
import argparse
import json


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--durable-root", required=True)
    p.add_argument("--node", default="node")
    p.add_argument("--search-glob", action="append", default=None)
    p.add_argument("--timeout-sec", type=int, default=30)
    p.parse_args()
    print(json.dumps({"companion_path": "/fake/codex-companion.mjs"}))


if __name__ == "__main__":
    main()
"""

# The ONE faked leaf, and the only thing in this whole chain that is faked:
# a real codex_job.py invocation spends a real, paid codex turn against a
# real model. Everything it is a stand-in for is covered by
# tests/codex_job_driver.test.py and tests/claim_chokepoint.test.py against
# the real CodexJob.
#
# It accepts the REAL argv shape (so a flag this driver stops forwarding is a
# parse error here, loudly, rather than a silently ignored difference), logs
# the raw argv it was handed -- never a reconstruction, so the test observes
# what was actually sent -- and writes the artifact a successful turn would
# have produced. The review's draft_sha1 comes from the REAL, staged
# draft_sha1.py rather than a second hand-rolled hash, for the same reason
# the driver itself refuses to keep an eighth copy of that algorithm.
FAKE_CODEX_JOB_PY = """#!/usr/bin/env python3
import argparse
import importlib.util
import json
import sys
from pathlib import Path


def _real_draft_sha1_module():
    path = Path(__file__).resolve().parent / "draft_sha1.py"
    spec = importlib.util.spec_from_file_location("draft_sha1_fixture", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--kind", required=True)
    p.add_argument("--run-id", default=None)
    p.add_argument("--companion", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--seg", required=True)
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--expect-token", required=True)
    p.add_argument("--disp", required=True)
    p.add_argument("--deadline-sec", required=True)
    p.add_argument("--effort", default="high")
    p.add_argument("--model", default=None)
    p.add_argument("--plugin-root", default=None)
    p.add_argument("--node", default="node")
    args = p.parse_args()

    cwd = Path(args.cwd)
    with open(cwd / "test_fixture_argv_log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": args.kind, "seg": args.seg, "argv": sys.argv[1:]}) + "\\n")

    segments_dir = cwd / "segments"
    if args.kind == "translate":
        # Deliberately DESTRUCTIVE, and nothing here should ever reach it: a
        # translate dispatch for a claimed segment overwrites the operator's
        # hand-edited draft, which is the exact outcome #438 exists to
        # prevent. Writing a recognizable replacement makes that outcome
        # visible as changed BYTES rather than only as a log line.
        (segments_dir / (args.seg + ".draft.json")).write_text(
            json.dumps({"seg": args.seg, "blocks": {"p1": "A FRESH MACHINE TRANSLATION"},
                        "footnotes": {}, "verses": {}, "names": [], "notes": [],
                        "dispatch_token": args.expect_token}),
            encoding="utf-8")
    else:
        draft_path = segments_dir / (args.seg + ".draft.json")
        review = {
            "clean": True, "coverage_ok": True, "findings": [],
            "draft_sha1": _real_draft_sha1_module().draft_content_sha1(draft_path),
            "dispatch_token": args.expect_token,
        }
        (segments_dir / (args.seg + ".review.json")).write_text(
            json.dumps(review), encoding="utf-8")

    print(json.dumps({"ok": True, "kind": args.kind, "seg": args.seg, "jobId": "fake-job",
                      "job_status": "completed", "timed_out": False, "adopted": False,
                      "reason": "promoted", "error_detail": None}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def make_driver_root(tmp_path, seg):
    """make_durable_root()'s tree plus everything the driver's own CLI needs:
    the REAL segment_dispatch_driver.py and resume_setup.py, the REAL
    mass-translate-wf.template.js, the two bundle-hash markers resume_setup.py
    FATALs without, and the two fakes named above. The P2 (--from-cap)
    segment is built by the SAME build_from_cap_segment() the selector hop
    uses."""
    root = make_durable_root(tmp_path, profile=DRIVER_PROFILE)
    scripts_dir = root / "scripts"
    shutil.copy2(SEGMENT_DISPATCH_DRIVER_SRC, scripts_dir / "segment_dispatch_driver.py")
    shutil.copy2(RESUME_SETUP_SRC, scripts_dir / "resume_setup.py")
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(RESUME_SETUP_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    (scripts_dir / "resolve_codex_companion.py").write_text(
        FAKE_RESOLVE_CODEX_COMPANION_PY, encoding="utf-8")
    (scripts_dir / "codex_job.py").write_text(FAKE_CODEX_JOB_PY, encoding="utf-8")
    templates_dir = root / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MASS_TRANSLATE_TEMPLATE_SRC, templates_dir / "mass-translate-wf.template.js")
    (root / "runs" / ".plugin_bundle_hash").write_text("fixture-plugin-bundle-hash\n", encoding="utf-8")
    (root / "runs" / ".orchestration_bundle_hash").write_text(
        "fixture-orchestration-bundle-hash\n", encoding="utf-8")

    fixture_keys = {}
    build_from_cap_segment(root, seg, fixture_keys)
    write_manifest(root, [seg])
    write_fixture_cache_keys(root, fixture_keys)
    return root


def run_driver(root, *extra_args, timeout=90):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "segment_dispatch_driver.py"), *extra_args],
        capture_output=True, text=True, timeout=timeout, cwd=str(root),
    )


def read_argv_log(root):
    log_path = root / "test_fixture_argv_log.jsonl"
    if not log_path.is_file():
        return []
    return [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def minted_run_dirs(root):
    """Every runs/<ID>/ this invocation created -- i.e. every one carrying an
    input.digest except the source run the fixture pre-seeded. The run id is
    minted by resume_setup.py from the wall clock, so it is READ BACK from
    disk here rather than predicted."""
    return sorted(
        p.name for p in (root / "runs").iterdir()
        if p.is_dir() and (p / "input.digest").is_file() and p.name != SOURCE_RUN_ID
    )


@_needs_node
def test_from_cap_claim_through_the_real_driver_cli_over_the_real_leaf_gates(tmp_path):
    """#438's headline capability through the REAL segment_dispatch_driver.py
    CLI, against the REAL select_segments.py AND the REAL draft_ready.py /
    validate_draft.py -- the combination no other file exercises. Only the
    paid codex turn is faked.

    What each assertion is load-bearing for:

      * `claims == {seg: "from-cap"}` and a claim record at exactly the path
        claim_record.claimed_path() computes: the selector's output survived
        the driver's own parse (the historical dict-vs-list seam) and the
        record landed where every reader looks for it -- computed by the real
        module, never spelled out as a path literal here;
      * the re-stamped token: the draft belongs to the run that is dispatching
        it. If the id the claim stamped ever diverged from the id the dispatch
        loop runs under, draft_ready.py --expect-token refuses, the driver
        falls through to "translate", and the hand-edited draft is destroyed;
      * `kinds == ["review"]` AND the draft's own bytes: a claimed segment is
        RE-REVIEWED, never re-translated. The bytes assertion is the one that
        matters -- the fake codex_job.py writes a recognizably different draft
        on a translate dispatch, so a regression here shows up as the
        operator's own text being gone, not merely as a wrong label.

    THE MUTATION THAT MAKES THIS FAIL: revert run()'s pre-selection block in
    segment_dispatch_driver.py to pass `run_id=None` into
    run_select_segments() (the abandoned two-phase contract). The real
    selector then fatals with "a claim ... was requested but --run-id was not
    given", the driver exits 1, and nothing is claimed or dispatched. That
    was the shipped state while every claim test in this file passed."""
    seg = "seg01"
    root = make_driver_root(tmp_path, seg)
    baseline_content_sha1 = draft_content_sha1_of(read_draft_doc(root, seg))
    hand_fixed_text = read_draft_doc(root, seg)["blocks"]["p1"]
    assert "Hand-fixed after the cap." in hand_fixed_text, (
        "the fixture's own premise: this draft carries an edit a re-translation "
        "would destroy"
    )

    proc = run_driver(root, "--only-segs", seg, "--from-cap", seg)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True, payload
    assert payload["claims"] == {seg: "from-cap"}, payload

    run_id = payload["run_id"]
    assert isinstance(run_id, str) and run_id, payload
    assert minted_run_dirs(root) == [run_id], (
        "the id reported back must be the one whose run directory this "
        "invocation actually created"
    )

    claim_mod = _load_module(CLAIM_RECORD_SRC, "claim_record_for_driver_e2e")
    record_path = claim_mod.claimed_path(run_id, seg, root / "runs")
    assert record_path.is_file(), f"no claim record at {record_path}"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["seg"] == seg and record["run_id"] == run_id, record
    assert record["profile"] == "from-cap", record
    assert record["previous_dispatch_token"] == f"{SOURCE_RUN_ID}:{seg}", record
    assert record["pre_claim_content_sha1"] == baseline_content_sha1, (
        "the record's pre-claim baseline must be the hash of the draft as it was "
        "BEFORE the claim -- that value is the whole point of writing the record"
    )

    after = read_draft_doc(root, seg)
    assert after["dispatch_token"] == f"{run_id}:{seg}", after
    assert after["blocks"]["p1"] == hand_fixed_text, (
        "the operator's hand-edited bytes must survive the whole run -- a translate "
        "dispatch would have replaced them with the fake's marker text"
    )
    assert draft_content_sha1_of(after) == baseline_content_sha1

    kinds = [entry["kind"] for entry in read_argv_log(root)]
    assert kinds == ["review"], (
        f"a claimed, hand-edited draft must be RE-REVIEWED, never re-translated -- "
        f"got dispatches: {kinds}"
    )


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permissions, so the token rewrite cannot be "
           "made to fail and the ordering stays unobservable",
)
def test_a_failed_token_rewrite_leaves_the_record_not_the_token(tmp_path):
    """RECORD-FIRST IS NORMATIVE, and this is the test that asserts the ORDER
    rather than the two outcomes. Both landing proves nothing about which
    landed first, and the ordering is the entire crash-safety argument: a
    crash between the two writes must leave the draft on its OLD token PLUS a
    durable record (recoverable -- every existing gate still refuses the old
    token, and a re-claim is idempotent), never a re-stamped draft with NO
    record, which D8's guard cannot refuse because it sees no record and
    reads "unclaimed".

    The order is made observable by taking the token rewrite away from a run
    that has already written its record: segments/ is made unwritable, so
    rewrite_draft_dispatch_token()'s exclusive temp-file create fails with
    EACCES while the claim record's own write (into runs/<RUN_ID>/) is
    unaffected. That is a real failure of the real function on the real
    ordering -- not a monkeypatched stand-in -- and it reproduces exactly the
    on-disk state a crash between the two writes would leave.

    THE MUTATION THAT MAKES THIS FAIL: swap the two calls in
    select_segments.py's claim write loop so the draft is re-stamped before
    write_claim_record() runs. The rewrite then fails FIRST, the loop's
    `continue` is reached before the record is ever written, and the claim
    record assertion below finds nothing on disk -- while the "old token
    intact" assertion still passes, which is why both are needed.

    Note what is deliberately NOT asserted: that the invocation reports
    failure is checked, but the ORPHANED runs/<RUN_ID>/ directory a refused
    claim leaves behind is a disclosed cost of single-phase admission, pinned
    in tests/segment_dispatch_driver.test.py rather than re-litigated here.

    No @_needs_node marker, unlike the test above: this invocation is refused
    by the Step 1 gate, so it never reaches call_template_functions() and
    never spawns node."""
    seg = "seg01"
    root = make_driver_root(tmp_path, seg)
    old_token = f"{SOURCE_RUN_ID}:{seg}"
    baseline_content_sha1 = draft_content_sha1_of(read_draft_doc(root, seg))
    segments_dir = root / "segments"

    # r-x: every gate can still READ the draft and the segpack (S1/S2 must
    # genuinely pass, or this test would be asserting the ordering of a claim
    # that was never admitted), but nothing can create the temp file the
    # re-stamp stages into.
    os.chmod(segments_dir, 0o555)
    try:
        proc = run_driver(root, "--only-segs", seg, "--from-cap", seg)
    finally:
        os.chmod(segments_dir, 0o755)

    assert proc.returncode != 0, (
        f"a token rewrite that cannot land must fail the invocation, not be "
        f"reported as a claim: stdout={proc.stdout!r}"
    )
    assert "dispatch_token rewrite failed" in proc.stdout, proc.stdout

    minted = minted_run_dirs(root)
    assert len(minted) == 1, f"expected exactly one minted run directory, got {minted}"
    claim_mod = _load_module(CLAIM_RECORD_SRC, "claim_record_for_ordering_e2e")
    record_path = claim_mod.claimed_path(minted[0], seg, root / "runs")

    assert record_path.is_file(), (
        f"RECORD-FIRST VIOLATED: the token rewrite failed, so if the record is not "
        f"on disk it was never written before the rewrite was attempted. Expected a "
        f"claim record at {record_path}"
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["previous_dispatch_token"] == old_token, record

    after = read_draft_doc(root, seg)
    assert after["dispatch_token"] == old_token, (
        "the draft must still hold its OLD token -- the state every existing gate "
        "already refuses safely, which is what makes a record-without-token "
        "recoverable"
    )
    assert draft_content_sha1_of(after) == baseline_content_sha1, (
        "a refused rewrite must install nothing at all"
    )
    assert read_argv_log(root) == [], "nothing may be dispatched after a failed claim"
