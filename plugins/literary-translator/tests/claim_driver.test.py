"""tests/claim_driver.test.py -- #438 D3 (fail-closed transport of the
claim authorization from select_segments.py's own JSON output into this
driver's dispatch context), D8's driver-side guard ("a claimed segment
may NEVER be dispatched for translation" -- the driver's own, EARLIER
layer; codex_job.py's own chokepoint is a separate, second layer owned
elsewhere), and D11 (round label/budget restart-at-1 after a claim, on
BOTH admission profiles).

## Scope

This file tests exactly the three functions #438 added to
segment_dispatch_driver.py: `parse_claims_field()` (D3), `claim_refusal_
for_translate()` (D8), and the interaction between a live on-disk claim
record and `derive_next_action()`'s own pre-existing "review from a
different run/round shape matches nothing -> round_label '1'" behavior
(D11 -- NOT a new mechanism; see that function's own comment). It does
NOT re-test select_segments.py's own admission logic (D1/D2/D5/D6 --
which ids get admitted under which profile) or codex_job.py's own
chokepoint (the second D8 layer) -- both are owned by other files this
plugin's own "no shared lib between self-contained scripts" convention
keeps this file from reaching into.

## Fixture strategy

Deliberately NEVER stages the REAL select_segments.py for any CLI-level
(`run_driver()`) test in this file: at the time this file was written,
select_segments.py was mid-edit by a parallel teammate and its own
`import claim_record` broke the PRE-EXISTING
tests/segment_dispatch_driver.test.py fixture (that file's own
`make_durable_root()` never copies claim_record.py) -- a live, reported
regression, unrelated to this file, that this file sidesteps entirely by
using its own small, fully-controlled FAKE_SELECT_SEGMENTS_PY wherever a
CLI-level test needs *a* select_segments.py subprocess to exist. Every
CLI-level test here is about THIS driver's own argv-forwarding/response-
parsing code, never about which ids select_segments.py itself would
admit.

D8/D11 tests call `process_segment()`/`claim_refusal_for_translate()`
directly against a hand-built DispatchContext (mirroring
segment_dispatch_driver.test.py's own `_fixture_ctx()` pattern) rather
than driving a full CLI dispatch -- this needs the REAL
resume_setup.py/ledger_update.py/draft_sha1.py/mass-translate-wf.template.js
(self-contained enough to run unmodified against a tmp fixture) plus
FAKEs matching the real script's OBSERVABLE CONTRACT for
resolve_codex_companion.py/draft_ready.py/validate_draft.py/codex_job.py/
cache_key.py -- identical composition to phase2_project() in the
pre-existing driver test file, duplicated here rather than imported (no
test file in this project imports another).
"""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
TEMPLATES_SRC_DIR = ASSETS_DIR / "templates"
DRIVER_SRC = SCRIPTS_SRC_DIR / "segment_dispatch_driver.py"
CLAIM_RECORD_SRC = SCRIPTS_SRC_DIR / "claim_record.py"
DRAFT_SHA1_SRC = SCRIPTS_SRC_DIR / "draft_sha1.py"
RESUME_SETUP_SRC = SCRIPTS_SRC_DIR / "resume_setup.py"
LEDGER_UPDATE_SRC = SCRIPTS_SRC_DIR / "ledger_update.py"
MASS_TRANSLATE_TEMPLATE_SRC = TEMPLATES_SRC_DIR / "mass-translate-wf.template.js"

for _src in (
    DRIVER_SRC, CLAIM_RECORD_SRC, DRAFT_SHA1_SRC, RESUME_SETUP_SRC, LEDGER_UPDATE_SRC,
    MASS_TRANSLATE_TEMPLATE_SRC,
):
    assert _src.is_file(), f"expected script not found: {_src}"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Loaded once from the REAL, un-copied source -- used for the pure-function
# parse_claims_field() tests, which need no fixture root at all (they take
# plain dicts/lists, never touch the filesystem).
DRIVER = _load_module(DRIVER_SRC, "segment_dispatch_driver_under_test_claim")
# Loaded once for building fixture claim records directly (build_claim_record/
# write_claim_record/claimed_path) -- never re-derived by hand, per this
# module's own "shared by import, not reimplemented per reader" argument.
CLAIM_RECORD = _load_module(CLAIM_RECORD_SRC, "claim_record_for_fixtures")


# ---------------------------------------------------------------------------
# A -- parse_claims_field() (#438 D3), pure-function tests. No fixture root.
# ---------------------------------------------------------------------------


# select_segments.py reports `claims` as a JSON object KEYED BY SEGMENT ID,
# each value the full claim_record.py payload (build_claim_record()'s own
# CLAIM_RECORD_FIELDS) plus that script's own D6/D10 reporting-only fields.
# `_claim_entry()` builds a minimal-but-valid value for that shape so each
# test below only has to vary the ONE field it is actually probing.
def _claim_entry(seg, profile="from-cap", **overrides):
    entry = {
        "seg": seg, "profile": profile, "run_id": "RUN-A", "source_run_id": "SOME-OLDER-RUN",
        "previous_dispatch_token": f"SOME-OLDER-RUN:{seg}", "pre_claim_content_sha1": "sha1",
        "operator_invocation": "test invocation", "cache_key": {"input_sha1": "x"},
        "claimed_at": "2026-08-08T00:00:00Z",
    }
    entry.update(overrides)
    return entry


def test_parse_claims_field_missing_field_is_fatal():
    with pytest.raises(DRIVER.DriverError, match="no 'claims' field"):
        DRIVER.parse_claims_field({"success": True, "segs": ["seg01"]}, ["seg01"])


def test_parse_claims_field_not_an_object_is_fatal():
    # A LIST -- specifically `[{"seg": ..., "profile": ...}, ...]` -- is
    # the shape this driver's own parser almost shipped against before the
    # lead ruled the dict-keyed-by-seg shape (already implemented and
    # shipped by select_segments.py) is the actual contract. Kept as its
    # own explicit case, not folded into test_parse_claims_field_value_not_
    # an_object_is_fatal below, so a regression back to accepting a list
    # is caught by name.
    with pytest.raises(DRIVER.DriverError, match="not a JSON object"):
        DRIVER.parse_claims_field({"claims": ["seg01"]}, ["seg01"])


def test_parse_claims_field_value_not_an_object_is_fatal():
    with pytest.raises(DRIVER.DriverError, match=r"claims\['seg01'\] is not a JSON object"):
        DRIVER.parse_claims_field({"claims": {"seg01": "from-cap"}}, ["seg01"])


def test_parse_claims_field_key_seg_mismatch_is_fatal():
    # The dict key and the entry's own 'seg' field are two copies of the
    # same fact -- select_segments.py's own dict-comprehension shape makes
    # them agree by construction, but this driver must not TRUST that
    # construction; it must CHECK it.
    with pytest.raises(DRIVER.DriverError, match="disagrees with its own dict key"):
        DRIVER.parse_claims_field({"claims": {"seg01": _claim_entry("seg02")}}, ["seg01", "seg02"])


def test_parse_claims_field_unsafe_seg_key_is_fatal():
    # validate_seg()'s own regex refuses this -- same check --only-segs
    # already gets, reused here so the two authorization channels this
    # file recognizes are held to one safety bar, not two.
    with pytest.raises(DRIVER.DriverError, match="unsafe segment id"):
        DRIVER.parse_claims_field(
            {"claims": {"../etc/passwd": _claim_entry("../etc/passwd")}},
            ["seg01"],
        )


def test_parse_claims_field_unknown_profile_is_fatal():
    with pytest.raises(DRIVER.DriverError, match="must be one of"):
        DRIVER.parse_claims_field(
            {"claims": {"seg01": _claim_entry("seg01", profile="from-thin-air")}},
            ["seg01"],
        )


def test_parse_claims_field_seg_not_a_subset_of_segs_is_fatal():
    # The failure D3 exists to close: a claim naming an id this invocation
    # never even selected must never be silently accepted as authorization
    # to act on that id.
    with pytest.raises(DRIVER.DriverError, match="not a member of this invocation's own 'segs' set"):
        DRIVER.parse_claims_field(
            {"claims": {"seg99": _claim_entry("seg99")}},
            ["seg01"],
        )


def test_parse_claims_field_accepts_a_colon_bearing_seg_id():
    # Segment ids contain colons in real books (`FRONTBACK:errata_02`) and
    # reach real filenames -- the FIRST-colon-splitting token parsers
    # elsewhere in this plugin already special-case. This claim path must
    # round-trip the id unmangled, never split or reject it on the colon
    # alone (validate_seg()'s own regex already allows exactly one
    # `FRONTBACK:` prefix).
    result = DRIVER.parse_claims_field(
        {"claims": {"FRONTBACK:errata_02": _claim_entry("FRONTBACK:errata_02")}},
        ["FRONTBACK:errata_02", "seg01"],
    )
    assert result == {"FRONTBACK:errata_02": "from-cap"}


def test_parse_claims_field_empty_claims_is_valid():
    assert DRIVER.parse_claims_field({"claims": {}}, ["seg01", "seg02"]) == {}


def test_parse_claims_field_valid_multi_entry_both_profiles():
    result = DRIVER.parse_claims_field(
        {"claims": {
            "seg01": _claim_entry("seg01", profile="from-cap"),
            "seg02": _claim_entry("seg02", profile="from-converged"),
        }},
        ["seg01", "seg02", "seg03"],
    )
    assert result == {"seg01": "from-cap", "seg02": "from-converged"}


# ---------------------------------------------------------------------------
# B -- claim_refusal_for_translate() (#438 D8), unit tests against a
# minimal dirs{}/DispatchContext -- no full phase2 dispatch fixture needed,
# only claim_record.py present under scripts_dir and a runs_dir to write
# under.
# ---------------------------------------------------------------------------


def _minimal_claim_dirs(tmp_path):
    """A `dirs`-shaped dict carrying only what claim_refusal_for_translate()
    itself reads: scripts_dir (must hold a real claim_record.py, loaded via
    the same verify-then-reopen-by-path discipline the driver uses for
    every other in-process-executed sibling) and runs_dir (where the
    per-run claim record lives, #438 D4)."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(CLAIM_RECORD_SRC, scripts_dir / "claim_record.py")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return {"scripts_dir": scripts_dir, "runs_dir": runs_dir}


def _minimal_ctx(tmp_path, run_id="testrun"):
    dirs = _minimal_claim_dirs(tmp_path)
    return DRIVER.DispatchContext(
        dirs=dirs, run_id=run_id, translate_cfg={"max_fix_rounds": 2}, companion_path="/fake/companion.mjs",
        durable_root_str=None, plugin_root_str=None, node_bin="node", session_id="test-session",
    )


def test_claim_refusal_for_translate_returns_none_when_absent(tmp_path):
    ctx = _minimal_ctx(tmp_path)
    assert DRIVER.claim_refusal_for_translate(ctx, "seg01") is None


def test_claim_refusal_for_translate_refuses_when_present(tmp_path):
    ctx = _minimal_ctx(tmp_path)
    path = CLAIM_RECORD.claimed_path(ctx.run_id, "seg01", ctx.dirs["runs_dir"])
    ok, detail = CLAIM_RECORD.write_claim_record(
        path,
        CLAIM_RECORD.build_claim_record(
            "seg01", "from-cap", ctx.run_id, "SOME-OLDER-RUN",
            "SOME-OLDER-RUN:seg01", "pre-claim-sha1", "test invocation",
            {"input_sha1": "x"}, "2026-08-08T00:00:00Z",
        ),
    )
    assert ok, detail

    refusal = DRIVER.claim_refusal_for_translate(ctx, "seg01")
    assert refusal is not None
    assert "seg01" in refusal
    assert "#438 D8" in refusal


def test_claim_refusal_for_translate_refuses_on_non_regular_entry(tmp_path):
    # A directory occupying the claim path -- classify_claim_record()'s own
    # AMBIGUOUS branch. This call site must map AMBIGUOUS to REFUSE (the
    # opposite of claim_record.py's own admission-time "do not claim"
    # guidance) -- see claim_refusal_for_translate()'s own docstring for
    # why the two call sites need opposite safe directions.
    ctx = _minimal_ctx(tmp_path)
    path = CLAIM_RECORD.claimed_path(ctx.run_id, "seg01", ctx.dirs["runs_dir"])
    path.mkdir(parents=True)

    refusal = DRIVER.claim_refusal_for_translate(ctx, "seg01")
    assert refusal is not None
    assert "not read" in refusal or "could not be read" in refusal


def test_claim_refusal_for_translate_refuses_on_permission_denied(tmp_path):
    ctx = _minimal_ctx(tmp_path)
    path = CLAIM_RECORD.claimed_path(ctx.run_id, "seg01", ctx.dirs["runs_dir"])
    path.parent.mkdir(parents=True, exist_ok=True)
    locked_dir = path.parent
    os.chmod(locked_dir, 0o000)
    try:
        refusal = DRIVER.claim_refusal_for_translate(ctx, "seg01")
    finally:
        # Restore before tmp_path teardown tries to remove this directory.
        os.chmod(locked_dir, 0o755)
    assert refusal is not None


def test_claim_refusal_for_translate_handles_a_colon_bearing_seg_id(tmp_path):
    ctx = _minimal_ctx(tmp_path)
    seg = "FRONTBACK:errata_02"
    assert DRIVER.claim_refusal_for_translate(ctx, seg) is None

    path = CLAIM_RECORD.claimed_path(ctx.run_id, seg, ctx.dirs["runs_dir"])
    ok, detail = CLAIM_RECORD.write_claim_record(
        path,
        CLAIM_RECORD.build_claim_record(
            seg, "from-cap", ctx.run_id, "SOME-OLDER-RUN",
            f"SOME-OLDER-RUN:{seg}", "pre-claim-sha1", "test invocation",
            {"input_sha1": "x"}, "2026-08-08T00:00:00Z",
        ),
    )
    assert ok, detail
    refusal = DRIVER.claim_refusal_for_translate(ctx, seg)
    assert refusal is not None
    assert seg in refusal


# ===========================================================================
# C -- process_segment()-level end-to-end tests (#438 D8/D11) against a full
# phase2 fixture. Duplicated from segment_dispatch_driver.test.py's own
# fixture composition (this project's tests never import one another).
# ===========================================================================

FULL_PROFILE_YAML = (
    "engine:\n"
    "  max_fix_rounds: 2\n"
    "  max_codex_jobs_per_batch: 400\n"
    "  batch_agent_cap: 10000\n"
    "  effort: high\n"
    "source:\n"
    "  language:\n"
    "    code: fr\n"
    "target:\n"
    "  language:\n"
    "    code: ru\n"
    "verse_policy:\n"
    "  mode: skip\n"
    "  threshold_lines: null\n"
)

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
    durable_root = Path(args.durable_root).resolve() if args.durable_root else Path(__file__).resolve().parent.parent
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

FAKE_DRAFT_READY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("seg")
    p.add_argument("--expect-token", default=None)
    p.add_argument("--candidate-file", default=None)
    p.add_argument("--durable-root", default=None)
    args = p.parse_args()
    durable_root = Path(args.durable_root).resolve() if args.durable_root else Path(__file__).resolve().parent.parent
    path = Path(args.candidate_file) if args.candidate_file else durable_root / "segments" / (args.seg + ".draft.json")
    if not path.is_file():
        print(json.dumps({"ready": False, "reason": "missing"}))
        return 1
    obj = json.loads(path.read_text(encoding="utf-8"))
    if args.expect_token is not None and obj.get("dispatch_token") != args.expect_token:
        print(json.dumps({"ready": False, "reason": "token-mismatch"}))
        return 1
    print(json.dumps({"ready": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

FAKE_VALIDATE_DRAFT_PY = """#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("seg")
    p.add_argument("--candidate-file", default=None)
    p.add_argument("--durable-root", default=None)
    args = p.parse_args()
    durable_root = Path(args.durable_root).resolve() if args.durable_root else Path(__file__).resolve().parent.parent
    path = Path(args.candidate_file) if args.candidate_file else durable_root / "segments" / (args.seg + ".draft.json")
    if not path.is_file():
        print("FAIL: draft missing")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

# Controllable fake codex_job.py, identical observable contract to
# segment_dispatch_driver.test.py's own FAKE_CODEX_JOB_PHASE2_PY: writes an
# argv log every call (so a test can assert NOTHING was dispatched), and on
# success writes a real draft (translate) or a clean/coverage_ok review
# (review) using the REAL, staged draft_sha1.py for the sha1 -- never a
# hand-duplicated hash.
FAKE_CODEX_JOB_PHASE2_PY = """#!/usr/bin/env python3
import argparse
import importlib.util
import json
import sys
from pathlib import Path


def _load_real_draft_sha1():
    path = Path(__file__).resolve().parent / "draft_sha1.py"
    spec = importlib.util.spec_from_file_location("draft_sha1_fixture", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--kind", required=True)
    p.add_argument("--companion", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--seg", required=True)
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--expect-token", required=True)
    p.add_argument("--run-id", default=None)
    p.add_argument("--disp", required=True)
    p.add_argument("--deadline-sec", required=True)
    p.add_argument("--effort", default="high")
    p.add_argument("--model", default=None)
    p.add_argument("--plugin-root", default=None)
    p.add_argument("--node", default="node")
    args = p.parse_args()

    cwd = Path(args.cwd)
    argv_log_path = cwd / "test_fixture_argv_log.jsonl"
    with open(argv_log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": args.kind, "seg": args.seg, "argv": sys.argv[1:]}) + "\\n")
    segments_dir = cwd / "segments"

    if args.kind == "translate":
        draft = {"seg": args.seg, "blocks": {"p1": "hola"}, "dispatch_token": args.expect_token}
        (segments_dir / (args.seg + ".draft.json")).write_text(json.dumps(draft), encoding="utf-8")
    else:
        draft_path = segments_dir / (args.seg + ".draft.json")
        sha1_mod = _load_real_draft_sha1()
        review = {
            "clean": True, "coverage_ok": True, "findings": [],
            "draft_sha1": sha1_mod.draft_content_sha1(draft_path), "dispatch_token": args.expect_token,
        }
        (segments_dir / (args.seg + ".review.json")).write_text(json.dumps(review), encoding="utf-8")

    line = {
        "ok": True, "kind": args.kind, "seg": args.seg, "jobId": "fake-job",
        "job_status": "completed", "timed_out": False, "adopted": False,
        "reason": "promoted", "error_detail": None,
    }
    print(json.dumps(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

_PHASE2_SIBLING_NAMES = (
    "resume_setup.py", "resolve_codex_companion.py", "ledger_update.py", "cache_key.py",
    "draft_ready.py", "validate_draft.py",
)


def make_durable_root(tmp_path, name="durable_root", profile_yaml=FULL_PROFILE_YAML):
    """Isolated durable_root carrying: the real segment_dispatch_driver.py
    + claim_record.py under scripts/ (both required for
    claim_refusal_for_translate()'s own sibling import), a minimal
    profile.yml, and the runs/segments scaffolding. Does NOT stage
    select_segments.py at all -- every test in section C drives
    process_segment() directly and never shells out to it."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DRIVER_SRC, scripts_dir / "segment_dispatch_driver.py")
    shutil.copy2(CLAIM_RECORD_SRC, scripts_dir / "claim_record.py")
    shutil.copy2(RESUME_SETUP_SRC, scripts_dir / "resume_setup.py")
    shutil.copy2(LEDGER_UPDATE_SRC, scripts_dir / "ledger_update.py")
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    (scripts_dir / "resolve_codex_companion.py").write_text(FAKE_RESOLVE_CODEX_COMPANION_PY, encoding="utf-8")
    (scripts_dir / "draft_ready.py").write_text(FAKE_DRAFT_READY_PY, encoding="utf-8")
    (scripts_dir / "validate_draft.py").write_text(FAKE_VALIDATE_DRAFT_PY, encoding="utf-8")
    (scripts_dir / "codex_job.py").write_text(FAKE_CODEX_JOB_PHASE2_PY, encoding="utf-8")

    shutil.copytree(ASSETS_DIR / "schemas", root / "schemas")

    templates_dir = root / "templates"
    templates_dir.mkdir()
    shutil.copy2(MASS_TRANSLATE_TEMPLATE_SRC, templates_dir / "mass-translate-wf.template.js")

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()

    profile_path = root / "profile.yml"
    profile_path.write_text(profile_yaml, encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )
    return root


def write_fixture_cache_keys(root, mapping):
    (root / "test_fixture_cache_keys.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")


CACHE_KEY_FIELDS = [
    "input_sha1", "style_contract_hash", "used_terms_hash", "pipeline_version",
    "schema_hash", "prompt_hash", "agent_config_hash", "profile_semantics_hash",
    "particle_config_hash", "source_extraction_hash", "source_input_hash",
    "derivation_bundle_hash", "verse_map_hash", "note_map_hash", "plugin_bundle_hash",
]


def make_cache_key(seed):
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def write_fixture_segpack(root, seg):
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps({"seg": seg, "blocks": [], "footnotes": [], "verses": []}, ensure_ascii=False),
        encoding="utf-8",
    )


_FIXTURE_TRANSLATE_CFG = {
    "max_fix_rounds": 2, "batch_agent_cap": 10000, "max_codex_jobs_per_batch": 400,
    "effort": "high", "model": "", "source_lang": "fr", "target_lang": "ru",
    "verse_policy": {"mode": "skip", "threshold_lines": None},
    "research_mode": "", "citation_content_types": [],
}


def _load_fixture_driver(root):
    """Loads segment_dispatch_driver.py from ITS OWN staged copy under
    `root/scripts/` -- self-anchoring only resolves to this fixture's own
    siblings (claim_record.py included) when the module is loaded from
    THERE, mirroring segment_dispatch_driver.test.py's own
    `_load_fixture_driver()`."""
    return _load_module(root / "scripts" / "segment_dispatch_driver.py", "segment_dispatch_driver_fixture_claim")


def _fixture_ctx(root, run_id, translate_cfg=None):
    driver_mod = _load_fixture_driver(root)
    dirs = driver_mod.resolve_dirs(None)
    ctx = driver_mod.DispatchContext(
        dirs=dirs, run_id=run_id, translate_cfg=translate_cfg or dict(_FIXTURE_TRANSLATE_CFG),
        companion_path="/fake/codex-companion.mjs", durable_root_str=None, plugin_root_str=None,
        node_bin="node", session_id="test-session",
    )
    return driver_mod, ctx


def _write_claim(driver_mod, root, run_id, seg, profile):
    claim_mod = driver_mod._load_claim_record_module(root / "scripts")
    path = claim_mod.claimed_path(run_id, seg, root / "runs")
    ok, detail = claim_mod.write_claim_record(
        path,
        claim_mod.build_claim_record(
            seg, profile, run_id, "SOME-OLDER-RUN", f"SOME-OLDER-RUN:{seg}",
            "pre-claim-sha1", "test invocation", make_cache_key(seg), "2026-08-08T00:00:00Z",
        ),
    )
    assert ok, detail
    return path


def _argv_log(root):
    log_path = root / "test_fixture_argv_log.jsonl"
    if not log_path.is_file():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_unclaimed_segment_dispatches_and_converges_normally(tmp_path):
    """Regression guard for this file's own change: an ORDINARY not_started
    segment with no claim record anywhere must be completely unaffected by
    claim_refusal_for_translate() -- it must dispatch a real translate and
    converge exactly as it did before #438."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {"seg01": make_cache_key("seg01")})
    write_fixture_segpack(root, "seg01")
    driver_mod, ctx = _fixture_ctx(root, run_id="RUN-A")

    result = driver_mod.process_segment("seg01", ctx)

    assert result["outcome"] == "converged", result
    log = _argv_log(root)
    kinds = [entry["kind"] for entry in log]
    assert kinds == ["translate", "review"], kinds
    for entry in log:
        assert "--run-id" in entry["argv"], entry["argv"]
        idx = entry["argv"].index("--run-id")
        assert entry["argv"][idx + 1] == "RUN-A", entry["argv"]


def test_a_claimed_segment_with_a_missing_draft_is_refused_not_translated(tmp_path):
    """#438 D8's own named residual scenario: a claim record exists for
    this run+seg, but the draft itself is absent (simulating "went invalid
    or missing between admission and dispatch") -- derive_next_action()
    falls through to {"action": "translate"}, and claim_refusal_for_
    translate() must catch it BEFORE any ledger write or codex_job.py
    dispatch. Uses a colon-bearing seg id (a real shape, `FRONTBACK:
    errata_02`) to prove the refusal path -- not only claim_record.py's own
    already-tested path handling -- tolerates it end to end."""
    seg = "FRONTBACK:errata_02"
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {seg: make_cache_key(seg)})
    write_fixture_segpack(root, seg)
    driver_mod, ctx = _fixture_ctx(root, run_id="RUN-A")
    _write_claim(driver_mod, root, "RUN-A", seg, "from-converged")

    result = driver_mod.process_segment(seg, ctx)

    assert result["outcome"] == "failed", result
    assert result["reason"] == "claimed-segment-translate-refused", result
    assert seg in result["detail"]
    assert _argv_log(root) == [], "codex_job.py must never have been invoked"
    assert not (root / "runs" / "ledger.d" / f"{seg}.json").exists(), (
        "no ledger fragment may be written on refusal"
    )


def test_a_claimed_segment_with_a_non_regular_claim_record_is_refused_not_translated(tmp_path):
    """AMBIGUOUS (a directory sitting at the claim path, never Path.exists())
    must refuse exactly like PRESENT at this call site -- proven here through
    process_segment(), not only at claim_refusal_for_translate()'s own unit
    level."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {"seg01": make_cache_key("seg01")})
    write_fixture_segpack(root, "seg01")
    driver_mod, ctx = _fixture_ctx(root, run_id="RUN-A")
    claim_mod = driver_mod._load_claim_record_module(root / "scripts")
    claim_path = claim_mod.claimed_path("RUN-A", "seg01", root / "runs")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.mkdir()  # a directory, not a regular file -- AMBIGUOUS

    result = driver_mod.process_segment("seg01", ctx)

    assert result["outcome"] == "failed", result
    assert result["reason"] == "claimed-segment-translate-refused", result
    assert _argv_log(root) == []


def _run_d11_restart_scenario(tmp_path, profile):
    """A HEALTHY claimed draft (token already rewritten to THIS run's
    translate-shaped token, #438 D4) plus an ORPHANED review.json left over
    from the run this draft was claimed FROM (a foreign dispatch_token,
    matching D11's own "a review carrying an OLD run's token matches
    nothing" scenario). derive_next_action() must never return
    {"action": "translate"} here at all -- draft_ok is already True, so it
    routes straight to the review branch, `_matched_review_round_label()`
    returns None for the foreign token, and round_label restarts at '1'.
    Returns the final process_segment() result and the argv log."""
    seg = "seg01"
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {seg: make_cache_key(seg)})
    write_fixture_segpack(root, seg)
    driver_mod, ctx = _fixture_ctx(root, run_id="RUN-CURRENT")
    _write_claim(driver_mod, root, "RUN-CURRENT", seg, profile)

    # STAND-IN FOR THE SELECTOR'S COMMIT PHASE, NOT A PROVEN PRODUCTION
    # PATH: the actual draft-token rewrite is select_segments.py's own
    # write, landing together with the claim record (record first, token
    # second, per the lead's ruling) -- as of this test, that rewrite does
    # not exist anywhere in the tree yet (confirmed: neither select_
    # segments.py nor codex_job.py performs it; see the #438 thread).
    # This line manually reproduces its EFFECT (the draft's dispatch_token
    # already equal to THIS run's translate token, #438 D4) so D8/D11 can
    # be tested against the state a real claim is SUPPOSED to leave behind.
    # Do not read this as evidence the production path does it -- once the
    # rewrite lands, the end-to-end test proving the real path produces
    # this same state is owned by whoever builds the commit phase, not by
    # this fixture.
    draft = {"seg": seg, "blocks": {"p1": "hola"}, "dispatch_token": driver_mod.translate_dispatch_token("RUN-CURRENT", seg)}
    draft_path = root / "segments" / f"{seg}.draft.json"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")

    # The orphaned review this draft carried from the run it was claimed
    # FROM -- same shape a --from-converged claim's pre-admission review
    # would leave behind.
    sha1_mod = driver_mod._load_draft_sha1_module(ctx.dirs["scripts_dir"])
    orphan_review = {
        "clean": True, "coverage_ok": True, "findings": [],
        "draft_sha1": sha1_mod.draft_content_sha1(draft_path),
        "dispatch_token": "SOME-OLDER-RUN:seg01:r1",
    }
    (root / "segments" / f"{seg}.review.json").write_text(json.dumps(orphan_review), encoding="utf-8")

    result = driver_mod.process_segment(seg, ctx)
    log = _argv_log(root)
    if log:
        # #438: codex_job.py now REQUIRES --run-id on every dispatch (never
        # derived from --expect-token) -- proves this driver's own
        # build_codex_job_argv()/run_one_codex_job() wiring actually
        # forwards ctx.run_id, not merely that the fake tolerates its
        # absence.
        assert "--run-id" in log[0]["argv"], log[0]["argv"]
        idx = log[0]["argv"].index("--run-id")
        assert log[0]["argv"][idx + 1] == "RUN-CURRENT", log[0]["argv"]
    return root, result


def test_d11_restart_at_1_after_a_claim_from_cap_profile(tmp_path):
    root, result = _run_d11_restart_scenario(tmp_path, "from-cap")

    kinds = [entry["kind"] for entry in _argv_log(root)]
    assert kinds == ["review"], (
        f"a healthy claimed segment must skip translate entirely, got dispatches: {kinds}"
    )
    assert result["outcome"] == "converged", result
    fragment = json.loads((root / "runs" / "ledger.d" / "seg01.json").read_text(encoding="utf-8"))
    assert fragment["rounds"] == 1, fragment


def test_d11_restart_at_1_after_a_claim_from_converged_profile(tmp_path):
    root, result = _run_d11_restart_scenario(tmp_path, "from-converged")

    kinds = [entry["kind"] for entry in _argv_log(root)]
    assert kinds == ["review"], (
        f"a healthy claimed segment must skip translate entirely, got dispatches: {kinds}"
    )
    assert result["outcome"] == "converged", result
    fragment = json.loads((root / "runs" / "ledger.d" / "seg01.json").read_text(encoding="utf-8"))
    assert fragment["rounds"] == 1, fragment


# ===========================================================================
# D/E -- CLI-level (#438 D3) tests: local flag validation and argv
# forwarding, against a self-contained FAKE select_segments.py -- never the
# real one (see this file's own module docstring for why).
# ===========================================================================

FAKE_SELECT_SEGMENTS_PY = """#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only-segs", default=None)
    p.add_argument("--allow-retranslate-converged", action="store_true")
    p.add_argument("--allow-empty", action="store_true")
    p.add_argument("--from-cap", default=None)
    p.add_argument("--from-converged", default=None)
    p.add_argument("--run-id", default=None)
    p.add_argument("--durable-root", default=None)
    p.add_argument("--plugin-root", default=None)
    args = p.parse_args()

    # Records the raw argv values this process actually received, for the
    # test to inspect afterward -- mirrors segment_dispatch_driver.test.py's
    # own FAKE_CODEX_JOB_PHASE2_PY argv_log_path convention. Deliberately
    # NEVER folds from_cap/from_converged into 'segs' below -- this fake
    # exists to prove argv FORWARDING only, and always reporting an empty
    # segs/claims keeps every CLI-level test in this section on the cheap
    # --allow-empty early-return path, with no need for the driver's full
    # dispatch machinery.
    Path("test_fixture_select_segments_received.json").write_text(
        json.dumps({
            "from_cap": args.from_cap, "from_converged": args.from_converged, "run_id": args.run_id,
        }),
        encoding="utf-8",
    )

    print(json.dumps({
        "success": True, "segs": [], "claims": {},
        "counts": {}, "classification": {},
    }))


if __name__ == "__main__":
    main()
"""


def make_cli_root(tmp_path, name="durable_root"):
    """A durable_root for CLI-level (subprocess) driver invocations, with a
    FAKE select_segments.py (never the real one -- see this file's own
    module docstring for why) standing in for the real one make_durable_root()
    stages. run() forwards --from-cap/--from-converged to this (validate-
    only) call but never a run_id (#438 D1a, reverted after a codex
    BLOCKER -- see run()'s own comment at its call site), so nothing here
    needs resume_setup.py's own real machinery; every test in this section
    either refuses locally before select_segments.py ever runs, or takes
    the empty-SEGS early return."""
    root = make_durable_root(tmp_path, name=name)
    (root / "scripts" / "select_segments.py").write_text(FAKE_SELECT_SEGMENTS_PY, encoding="utf-8")
    return root


def run_driver(root, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "segment_dispatch_driver.py"), *extra_args],
        capture_output=True, text=True, timeout=timeout, cwd=str(root),
    )


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def test_from_cap_bad_id_refused_locally_before_select_segments_ever_runs(tmp_path):
    root = make_cli_root(tmp_path)
    proc = run_driver(root, "--from-cap", "../etc/passwd")
    assert proc.returncode == 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert "--from-cap" in payload["error"]
    assert not (root / "test_fixture_select_segments_received.json").exists(), (
        "select_segments.py must never have been invoked"
    )


def test_from_converged_bad_id_refused_locally_before_select_segments_ever_runs(tmp_path):
    root = make_cli_root(tmp_path)
    proc = run_driver(root, "--from-converged", "not a valid id")
    assert proc.returncode == 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert "--from-converged" in payload["error"]
    assert not (root / "test_fixture_select_segments_received.json").exists()


def test_from_cap_and_from_converged_forwarded_verbatim_to_select_segments(tmp_path):
    # #438 D1a: this call is validate-only -- --from-cap/--from-converged
    # are forwarded, but run_id must NOT be (see run()'s own comment at
    # its run_select_segments() call site for why an earlier revision that
    # did was reverted: it let resume_setup.py mint runs/<ID>/input.digest
    # before the selector could refuse, manufacturing evidence the #409
    # Step 3 gate reads as proof a historical gate ran).
    root = make_cli_root(tmp_path)
    proc = run_driver(root, "--from-cap", "seg01,seg02", "--from-converged", "seg03", "--allow-empty")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"

    received = json.loads((root / "test_fixture_select_segments_received.json").read_text(encoding="utf-8"))
    assert received["from_cap"] == "seg01,seg02"
    assert received["from_converged"] == "seg03"
    assert received["run_id"] is None, "the validate-only call must never carry a run_id"

    payload = parse_stdout(proc)
    assert payload["claims"] == {}


def test_run_select_segments_forwards_an_explicit_run_id_when_given(tmp_path):
    """run()'s own call site never passes a run_id (see the test above),
    but run_select_segments() itself must still forward one when a FUTURE
    caller (#438 D1a's still-unbuilt commit-phase driver) supplies one --
    called directly here, bypassing run() entirely, so this stays covered
    even though nothing in this driver currently exercises it end to end."""
    root = make_cli_root(tmp_path)
    driver_mod = _load_fixture_driver(root)
    dirs = driver_mod.resolve_dirs(None)

    result = driver_mod.run_select_segments(dirs, run_id="EXPLICIT-RUN-ID")

    assert result["success"] is True
    received = json.loads((root / "test_fixture_select_segments_received.json").read_text(encoding="utf-8"))
    assert received["run_id"] == "EXPLICIT-RUN-ID"


def test_claims_field_is_required_and_propagates_through_the_empty_segs_result(tmp_path):
    """No --from-cap/--from-converged passed -- select_segments.py's own
    'claims' field is still validated (required on every invocation, #438
    D3) and, being empty here, folds into an empty {} that is reported
    in the final result exactly like every other gate decision."""
    root = make_cli_root(tmp_path)
    proc = run_driver(root, "--allow-empty")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["segs"] == []
    assert payload["claims"] == {}
