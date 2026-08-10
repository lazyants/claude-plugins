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

Section E (below the D8-cross-run block) is the one exception: it covers
`_definitive_stat()` and `_resumable_run_id_candidates()`, the round-5
(post-1.21.0 codex review) hardening against Path.is_dir()/is_file()'s own
error-swallowing inside run-id resumability scanning. That function's
PRE-EXISTING coverage (ordering, the glossary-sibling exclusion, the
round-4 uncapped-candidates fix, the double-dot exclusion) stays in
tests/segment_dispatch_driver.test.py, which owns the rest of that
function's behavior; Section E adds only the unstattable-entry refusal
this round shipped without any test for.

## Fixture strategy

Deliberately NEVER stages the REAL select_segments.py -- nor the REAL
resume_setup.py -- for any CLI-level (`run_driver()`) test in this file.
Both are replaced by small, fully-controlled fakes
(FAKE_SELECT_SEGMENTS_PY, FAKE_RESUME_SETUP_PY) that record the argv they
were handed and answer with a fixed payload. Every CLI-level test here is
about THIS driver's own argv-forwarding/response-parsing code -- which
values reach which subprocess, in which order -- never about which ids
select_segments.py itself would admit, nor about which RUN_ID
resume_setup.py would mint.

That split is deliberate and it has a matching obligation: a fake cannot
refuse the way the real script does, so a fake-only suite can go green
over a seam that is completely broken in production. It did exactly that
once already -- the driver forwarded `--from-cap` with no `--run-id`, the
real select_segments.py fatals on that combination, and this file's fake
happily answered `success: true` for every one of those runs. The
end-to-end proof that the driver drives the REAL select_segments.py
through a REAL claim therefore lives in
tests/segment_dispatch_driver.test.py
(`test_from_cap_claim_admitted_end_to_end_through_the_real_selector`),
which stages the real script and asserts the claim actually lands. Do not
let this file become the only coverage of that seam again.

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
# each value the full claim_record.py payload -- build_claim_record()'s own
# CLAIM_RECORD_FIELDS, and nothing else. The D6/D10 reporting fields
# (cache_key_moved_fields/cache_key_movement_machinery_only/cache_key_note/
# pre_claim_review) used to be spread on top of the record at that call site;
# they are FIELDS OF THE RECORD now, so the wire value and the on-disk value
# are the same object rather than two shapes one line apart.
# `_claim_entry()` builds a minimal-but-valid value for that shape so each
# test below only has to vary the ONE field it is actually probing.
def _claim_entry(seg, profile="from-cap", **overrides):
    entry = {
        "seg": seg, "profile": profile, "run_id": "RUN-A", "source_run_id": "SOME-OLDER-RUN",
        "previous_dispatch_token": f"SOME-OLDER-RUN:{seg}", "pre_claim_content_sha1": "sha1",
        "pre_claim_review": {
            "dispatch_token": f"SOME-OLDER-RUN:{seg}:r1", "clean": False,
            "coverage_ok": True, "findings_count": 1,
        },
        "pre_claim_cache_key": None,
        "cache_key_at_claim": {"input_sha1": "x"},
        "cache_key_moved_fields": [],
        "cache_key_movement_machinery_only": None,
        "cache_key_note": "no recorded cache_key on this fragment",
        "operator_invocation": "test invocation",
        "claimed_at": "2026-08-08T00:00:00Z",
    }
    entry.update(overrides)
    return entry


def test_claim_entry_fixture_matches_the_shipped_record_field_set():
    """The fixture above claims to mirror claim_record.py's own record shape.
    Pinned, not asserted in prose: a field added to (or renamed in)
    CLAIM_RECORD_FIELDS without this fixture following makes every
    parse_claims_field() test below exercise a shape the real selector never
    emits -- passing for a payload production cannot produce."""
    assert tuple(_claim_entry("seg01")) == CLAIM_RECORD.CLAIM_RECORD_FIELDS


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
    itself reads: scripts_dir (must hold a real claim_record.py AND
    draft_sha1.py, both loaded via the same verify-then-reopen-by-path
    discipline the driver uses for every other in-process-executed
    sibling), runs_dir (where the per-run claim record lives, #438 D4) and
    durable_root (whose segments/ holds the draft whose dispatch_token
    names the CURRENT owner).

    durable_root and draft_sha1.py were added when D8 gained its cross-run
    half. Before that, this guard only ever looked in its own run's claim
    namespace, so a fixture with neither was sufficient -- which is
    precisely why the cross-run hole was invisible from here: the fixture
    could not represent a draft owned by somebody else, so no test could
    have expressed the case."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(CLAIM_RECORD_SRC, scripts_dir / "claim_record.py")
    shutil.copy2(SCRIPTS_SRC_DIR / "draft_sha1.py", scripts_dir / "draft_sha1.py")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    durable_root = tmp_path / "durable"
    (durable_root / "segments").mkdir(parents=True)
    return {"scripts_dir": scripts_dir, "runs_dir": runs_dir,
            "durable_root": durable_root}


def _write_draft(ctx, seg, *, token=None, text="ORIGINAL DRAFT TEXT", raw=None):
    """Puts a draft at the canonical path D8 reads to learn the current
    owner. `raw` writes bytes verbatim, for the not-JSON / not-an-object
    cases; otherwise a minimal valid draft, with `dispatch_token` present
    only when `token` is given (a tokenless draft is what every pre-1.2.0
    project has)."""
    path = ctx.dirs["durable_root"] / "segments" / f"{seg}.draft.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return path
    doc = {"seg": seg, "text": text}
    if token is not None:
        doc["dispatch_token"] = token
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _minimal_ctx(tmp_path, run_id="testrun"):
    dirs = _minimal_claim_dirs(tmp_path)
    return DRIVER.DispatchContext(
        dirs=dirs, run_id=run_id, translate_cfg={"max_fix_rounds": 2}, companion_path="/fake/companion.mjs",
        durable_root_str=None, plugin_root_str=None, node_bin="node", session_id="test-session",
    )


def test_claim_refusal_for_translate_returns_none_when_absent(tmp_path):
    ctx = _minimal_ctx(tmp_path)
    assert DRIVER.claim_refusal_for_translate(ctx, "seg01") is None


def _fixture_claim_payload(claim_mod, seg, run_id, profile="from-cap", cache_key=None):
    """One place building the 14-field claim_record.py payload every fixture
    in this file needs. build_claim_record() is keyword-only with every field
    REQUIRED (no defaults), so a field added to CLAIM_RECORD_FIELDS is a
    TypeError here rather than a fixture that silently drifts from the record
    production writes -- which is the whole point of that signature, and the
    reason this helper spells all fourteen out instead of passing **kwargs."""
    return claim_mod.build_claim_record(
        seg=seg,
        profile=profile,
        run_id=run_id,
        source_run_id="SOME-OLDER-RUN",
        previous_dispatch_token=f"SOME-OLDER-RUN:{seg}",
        pre_claim_content_sha1="pre-claim-sha1",
        pre_claim_review={
            "dispatch_token": f"SOME-OLDER-RUN:{seg}:r1", "clean": False,
            "coverage_ok": True, "findings_count": 1,
        },
        pre_claim_cache_key=None,
        cache_key_at_claim=cache_key if cache_key is not None else {"input_sha1": "x"},
        cache_key_moved_fields=[],
        cache_key_movement_machinery_only=None,
        cache_key_note="no recorded cache_key on this fragment",
        operator_invocation="test invocation",
        claimed_at="2026-08-08T00:00:00Z",
    )


def test_claim_refusal_for_translate_refuses_when_present(tmp_path):
    ctx = _minimal_ctx(tmp_path)
    path = CLAIM_RECORD.claimed_path(ctx.run_id, "seg01", ctx.dirs["runs_dir"])
    ok, detail = CLAIM_RECORD.write_claim_record(
        path, _fixture_claim_payload(CLAIM_RECORD, "seg01", ctx.run_id),
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


def test_claim_refusal_for_translate_refuses_an_unsafe_run_id_instead_of_crashing(tmp_path):
    """#438: claimed_path() RAISES ValueError on a run id it will not build
    a path from, rather than silently returning one that escapes the durable
    root. This call site must map that to a REFUSAL, in the same direction as
    its AMBIGUOUS case -- a run id whose claim records cannot even be looked
    for is strictly worse than a record that cannot be read, so D8 must not
    clear the segment.

    Letting the exception escape instead would reach main()'s defensive
    catch-all as "unexpected error", exit 2 -- one bad run id killing a whole
    batch rather than one segment. run() validates the resolved run id before
    it ever becomes ctx.run_id, so a real dispatch cannot reach this; the
    guard is here so the property holds by inspection of the function rather
    than by tracing whoever built the context."""
    ctx = _minimal_ctx(tmp_path, run_id="../evil")

    refusal = DRIVER.claim_refusal_for_translate(ctx, "seg01")

    assert refusal is not None, "an unsafe run id must never read as 'not claimed'"
    assert "seg01" in refusal
    assert "../evil" in refusal
    assert "#438 D8" in refusal


def test_claim_refusal_for_translate_handles_a_colon_bearing_seg_id(tmp_path):
    ctx = _minimal_ctx(tmp_path)
    seg = "FRONTBACK:errata_02"
    assert DRIVER.claim_refusal_for_translate(ctx, seg) is None

    path = CLAIM_RECORD.claimed_path(ctx.run_id, seg, ctx.dirs["runs_dir"])
    ok, detail = CLAIM_RECORD.write_claim_record(
        path, _fixture_claim_payload(CLAIM_RECORD, seg, ctx.run_id),
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
        _fixture_claim_payload(claim_mod, seg, run_id, profile=profile, cache_key=make_cache_key(seg)),
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

    # STAND-IN for the state a real admission leaves behind, reproduced by
    # hand so this unit can vary D11's inputs freely. The production write
    # is select_segments.py's own, inside the SINGLE-PHASE admission call
    # (claim record first, dispatch_token second -- its admission loop
    # calls rewrite_draft_dispatch_token() immediately after
    # write_claim_record() succeeds); there is no separate commit phase and
    # there never will be. This line reproduces its EFFECT only (the draft's
    # dispatch_token already equal to THIS run's translate token, #438 D4).
    # The proof that the REAL path produces this same state is not here:
    # tests/claim_end_to_end.test.py asserts it against the real selector,
    # and tests/segment_dispatch_driver.test.py::test_from_cap_claim_
    # admitted_end_to_end_through_the_real_selector asserts the driver
    # reaches it through its own CLI.
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
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only-segs", default=None)
    p.add_argument("--allow-retranslate-converged", action="store_true")
    p.add_argument("--allow-empty", action="store_true")
    p.add_argument("--from-cap", default=None)
    p.add_argument("--from-converged", default=None)
    p.add_argument("--run-id", default=None)
    p.add_argument("--run-resume", default=None, choices=("true", "false"))
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
    #
    # --run-resume carries the real script's own `choices` so a driver that
    # relayed a Python bool ("True"/"False") or an unset value is refused
    # here rather than silently recorded. It does NOT reproduce the real
    # script's pairing refusal or its claim-requires-a-run-id fatal: this
    # fake cannot stand in for those, which is precisely why the real-
    # selector test named in this file's module docstring exists.
    Path("test_fixture_select_segments_received.json").write_text(
        json.dumps({
            "from_cap": args.from_cap, "from_converged": args.from_converged,
            "run_id": args.run_id, "run_resume": args.run_resume, "argv": sys.argv[1:],
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

# The RUN_ID the fake resume_setup.py below always answers with. A shape no
# wall clock would ever produce, so an assertion that this exact string
# reached select_segments.py cannot pass by coincidence against a real
# timestamp id.
FAKE_RESOLVED_RUN_ID = "FAKERESOLVED0000Z"

FAKE_RESUME_SETUP_PY = """#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--payload-file", required=True)
    p.add_argument("--durable-root", default=None)
    p.add_argument("--plugin-root", default=None)
    args = p.parse_args()

    # Proves this script RAN (and how many times), which is what the
    # "an ordinary dispatch resolves no run id before selection" test
    # asserts the absence of.
    marker = Path("test_fixture_resume_setup_calls.jsonl")
    with open(marker, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"payload": json.loads(Path(args.payload_file).read_text(encoding="utf-8"))}) + "\\n")

    print(json.dumps({
        "success": True,
        "effectiveRunId": "__RUN_ID__",
        "resume": False,
        "run_dir": "runs/__RUN_ID__",
        "input_digest": "fake-digest",
    }))


if __name__ == "__main__":
    main()
""".replace("__RUN_ID__", FAKE_RESOLVED_RUN_ID)


def make_cli_root(tmp_path, name="durable_root"):
    """A durable_root for CLI-level (subprocess) driver invocations, with a
    FAKE select_segments.py AND a FAKE resume_setup.py standing in for the
    real ones make_durable_root() stages (never the real ones -- see this
    file's own module docstring for why).

    resume_setup.py has to be faked here as of #438: run() now resolves the
    RUN_ID BEFORE the Step 1 gate call whenever --from-cap/--from-converged
    is given, because admission is single-phase and select_segments.py
    refuses a claim with no --run-id. The real resume_setup.py would need a
    manifest, bundle-hash markers and per-segment cache keys this section
    deliberately does not build; the fake answers with one fixed, recognizable
    id so a test can assert THAT EXACT VALUE arrived at select_segments.py --
    a stronger check than "some id was forwarded", since it also proves the
    driver forwards the id it RESOLVED rather than one it invented."""
    root = make_durable_root(tmp_path, name=name)
    (root / "scripts" / "select_segments.py").write_text(FAKE_SELECT_SEGMENTS_PY, encoding="utf-8")
    (root / "scripts" / "resume_setup.py").write_text(FAKE_RESUME_SETUP_PY, encoding="utf-8")
    return root


def read_select_segments_received(root):
    path = root / "test_fixture_select_segments_received.json"
    assert path.is_file(), "select_segments.py was never invoked"
    return json.loads(path.read_text(encoding="utf-8"))


def resume_setup_call_count(root):
    path = root / "test_fixture_resume_setup_calls.jsonl"
    if not path.is_file():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


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


def test_from_cap_and_from_converged_forwarded_with_the_resolved_run_id_pair(tmp_path):
    """#438: the claim is SINGLE-PHASE, so the ONE Step 1 gate call must
    carry the RUN_ID the claim will re-stamp drafts to -- select_segments.py
    fatals on a claim with no --run-id, and refuses --run-id without its
    paired --run-resume. The value forwarded must be the id run() actually
    RESOLVED (the fake resume_setup.py's own recognizable
    FAKE_RESOLVED_RUN_ID), never one this driver minted for itself.

    This test replaces one that asserted the exact opposite -- `received[
    "run_id"] is None`, "the validate-only call must never carry a run_id"
    -- which pinned the abandoned two-phase design and, because the fake
    selector cannot refuse the way the real one does, kept the suite green
    while --from-cap was refused at the gate on every real invocation."""
    root = make_cli_root(tmp_path)
    proc = run_driver(root, "--from-cap", "seg01,seg02", "--from-converged", "seg03", "--allow-empty")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"

    received = read_select_segments_received(root)
    assert received["from_cap"] == "seg01,seg02"
    assert received["from_converged"] == "seg03"
    assert received["run_id"] == FAKE_RESOLVED_RUN_ID, received
    assert received["run_resume"] == "false", received
    # The PAIR, adjacent and in this order, not merely both present
    # somewhere in the argv -- select_segments.py reads them as one relay.
    argv = received["argv"]
    assert "--run-id" in argv, argv
    idx = argv.index("--run-id")
    assert argv[idx:idx + 4] == ["--run-id", FAKE_RESOLVED_RUN_ID, "--run-resume", "false"], argv

    payload = parse_stdout(proc)
    assert payload["claims"] == {}
    # The resolved id is reported even on the empty-SEGS early return: it
    # names the runs/<ID>/ directory this invocation created, and nothing
    # else in the payload would.
    assert payload["run_id"] == FAKE_RESOLVED_RUN_ID, payload
    assert payload["resume"] is False, payload
    assert resume_setup_call_count(root) == 1, (
        "the run id must be resolved EXACTLY once per invocation -- a second "
        "resolution could hand the dispatch loop a different id than the claim "
        "stamped onto the drafts"
    )


def test_an_ordinary_dispatch_resolves_no_run_id_before_selection(tmp_path):
    """The property the pre-#438 ordering existed to protect, kept: with no
    claim flag, nothing is resolved before the Step 1 gate, so a gate refusal
    still costs no side effect at all. Proven by resume_setup.py never having
    run by the time select_segments.py answers with an empty selection (the
    empty-SEGS early return happens before the ordinary path's own
    resolution)."""
    root = make_cli_root(tmp_path)
    proc = run_driver(root, "--allow-empty")
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"

    received = read_select_segments_received(root)
    assert received["run_id"] is None, received
    assert received["run_resume"] is None, received
    assert resume_setup_call_count(root) == 0, (
        "an invocation with no claim flag must not resolve (and therefore must "
        "not create) a run directory before the gate has passed"
    )
    payload = parse_stdout(proc)
    assert payload["run_id"] is None, payload


def test_run_select_segments_forwards_an_explicit_run_id_and_resume_when_given(tmp_path):
    """run_select_segments() called directly, bypassing run(), so the argv
    shape is pinned independently of which caller assembles it."""
    root = make_cli_root(tmp_path)
    driver_mod = _load_fixture_driver(root)
    dirs = driver_mod.resolve_dirs(None)

    result = driver_mod.run_select_segments(dirs, run_id="EXPLICIT-RUN-ID", run_resume="true")

    assert result["success"] is True
    received = read_select_segments_received(root)
    assert received["run_id"] == "EXPLICIT-RUN-ID"
    assert received["run_resume"] == "true"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"run_id": "EXPLICIT-RUN-ID"},
        {"run_resume": "false"},
    ],
    ids=["run_id_without_run_resume", "run_resume_without_run_id"],
)
def test_run_select_segments_refuses_a_split_run_id_run_resume_pair(tmp_path, kwargs):
    """select_segments.py refuses the pair split (its own :1972 fatal), and
    so does this function -- refusing HERE names the caller's bug instead of
    reporting a child's complaint about an argv this driver built. Refused
    before the subprocess ever starts, proven by the fake selector's own
    receipt file never appearing."""
    root = make_cli_root(tmp_path)
    driver_mod = _load_fixture_driver(root)
    dirs = driver_mod.resolve_dirs(None)

    with pytest.raises(driver_mod.DriverError, match="TOGETHER or not at all"):
        driver_mod.run_select_segments(dirs, **kwargs)

    assert not (root / "test_fixture_select_segments_received.json").exists(), (
        "select_segments.py must never have been invoked with a half-built pair"
    )


def test_run_select_segments_refuses_a_non_literal_run_resume(tmp_path):
    """A Python bool formatted into the flag ("True"/"False") is the exact
    accident this guard exists for: argparse's own `choices` in the real
    script would reject it, but only after the subprocess has been launched
    and only as an opaque usage error attributed to the child."""
    root = make_cli_root(tmp_path)
    driver_mod = _load_fixture_driver(root)
    dirs = driver_mod.resolve_dirs(None)

    with pytest.raises(driver_mod.DriverError, match="literal string"):
        driver_mod.run_select_segments(dirs, run_id="EXPLICIT-RUN-ID", run_resume=str(False))

    assert not (root / "test_fixture_select_segments_received.json").exists()


def test_run_resume_literal_refuses_a_missing_or_non_boolean_resume_field():
    """--run-resume relays resume_setup.py's own `resume` field straight into
    select_segments.py's #409 Step 3 fresh-evidence check. A missing field
    coerced to False would arm the "fresh id" branch of that gate on a verdict
    resume_setup.py never gave."""
    assert DRIVER.run_resume_literal({"resume": True}) == "true"
    assert DRIVER.run_resume_literal({"resume": False}) == "false"
    for bad in ({}, {"resume": None}, {"resume": "false"}, {"resume": 0}):
        with pytest.raises(DRIVER.DriverError, match="not a JSON boolean"):
            DRIVER.run_resume_literal(bad)


def test_accepted_run_id_refuses_an_unsafe_effective_run_id():
    """The id resume_setup.py returns becomes a filesystem path segment
    (runs/<RUN_ID>/.claimed.<seg>), a codex_job.py --run-id, and every
    dispatch_token this run writes. Validated here rather than trusted
    because it came from a sibling script."""
    assert DRIVER.accepted_run_id({"effectiveRunId": "20260812T000000Z"}) == "20260812T000000Z"
    for bad in ({}, {"effectiveRunId": None}, {"effectiveRunId": ""},
                {"effectiveRunId": "../elsewhere"}, {"effectiveRunId": "z..poison"},
                {"effectiveRunId": "run id with spaces"}):
        with pytest.raises(DRIVER.DriverError, match="unusable effectiveRunId"):
            DRIVER.accepted_run_id(bad)


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


# ---------------------------------------------------------------------------
# D8's CROSS-RUN half. The guard used to build its lookup path out of
# ctx.run_id alone, so it could only ever see its OWN namespace: an ordinary
# later run failed the token gate, found nothing under its own id, read that
# as "unclaimed", and dispatched translate over a draft another run was
# actively holding. Every test below distinguishes "this run has not claimed
# seg" from "seg is unclaimed" -- the conflation that was the defect.
# ---------------------------------------------------------------------------

RUN_A = "20260101T000000Z"
RUN_B = "20260202T000000Z"


def _ctx_with_foreign_claim(tmp_path, *, hold=True, token=RUN_A + ":seg01", raw=None):
    ctx = _minimal_ctx(tmp_path, run_id=RUN_B)
    if hold:
        path = CLAIM_RECORD.claimed_path(RUN_A, "seg01", ctx.dirs["runs_dir"])
        ok, detail = CLAIM_RECORD.write_claim_record(
            path, _fixture_claim_payload(CLAIM_RECORD, "seg01", RUN_A))
        assert ok, detail
    _write_draft(ctx, "seg01", token=token, text="HAND EDIT OWNED BY RUN A", raw=raw)
    return ctx


def test_D8_refuses_translate_over_a_draft_a_DIFFERENT_run_holds(tmp_path):
    """THE REPORTED DEFECT. Run A holds a live claim on seg01 and the draft
    carries A's token. Run B -- an ordinary invocation that claims nothing --
    must be refused, because dispatching translate here overwrites the
    hand-edited draft this whole feature exists to protect.

    Reproduced end-to-end through the real process_segment() before the fix:
    B failed the token gate, found no record under RUN_B, dispatched translate
    AND review, replaced the text, and stamped its own token while A's claim
    record sat there untouched.

    THE MUTATION THAT MAKES THIS FAIL: restore `return None` in place of the
    _foreign_claim_refusal_for_translate() call on the CLAIM_ABSENT branch of
    claim_refusal_for_translate(). Measured: this test's `is not None`
    assertion fails, and the three no-regression tests below stay green --
    which is the boundary that matters, since a guard that refuses everything
    would satisfy this test alone."""
    ctx = _ctx_with_foreign_claim(tmp_path)

    refusal = DRIVER.claim_refusal_for_translate(ctx, "seg01")

    assert refusal is not None, (
        "run B must not translate over a draft run A holds a live claim on"
    )
    assert RUN_A in refusal, f"the refusal must NAME the owning run: {refusal!r}"
    assert "#438 D8" in refusal, refusal
    assert "OWNED BY RUN" in refusal, refusal


def test_D8_still_allows_the_ordinary_first_translation_with_no_draft(tmp_path):
    """The single most important no-regression case: with no draft on disk
    there is nothing to overwrite, and this is what EVERY normal dispatch
    looks like. A guard that refused "cannot determine the owner" as one
    undifferentiated case would block the entire pipeline."""
    ctx = _minimal_ctx(tmp_path, run_id=RUN_B)
    assert DRIVER.claim_refusal_for_translate(ctx, "seg01") is None


def test_D8_still_allows_a_tokenless_draft_when_NOBODY_holds_a_claim(tmp_path):
    """A draft with no dispatch_token and no claim record anywhere asserts no
    ownership -- and is what every pre-1.21.0 project has on disk, since claims
    did not exist before this release. Refusing these would be a silent
    regression with nothing to do with claims.

    Paired deliberately with the D9 test below: on the draft ALONE these two
    fixtures are identical, and the claim records are the only thing that
    tells them apart. An earlier version of this guard asserted only this half
    and therefore codified "tokenless means unowned", which was a BLOCKER."""
    ctx = _minimal_ctx(tmp_path, run_id=RUN_B)
    _write_draft(ctx, "seg01", token=None)
    assert DRIVER.claim_refusal_for_translate(ctx, "seg01") is None


def test_D8_refuses_a_TOKENLESS_draft_that_another_run_still_holds_a_claim_on(tmp_path):
    """D9's lost-token state, and the case the first version of this guard got
    WRONG by allowing.

    Run A claims seg01 and hand-edits the draft; a later fix round drops the
    dispatch_token from the draft -- a sequence this repository explicitly
    recognizes and provides a recovery for, because it is what a fix agent that
    fails to copy the token byte for byte produces. A's claim record is then
    the ONLY surviving evidence of who owns that hand edit. Ordinary run B
    fails draft_ready, derives `translate`, and -- before this -- sailed
    through D8, because the guard returned None the moment it saw no token
    without ever consulting a foreign namespace.

    This is the one case where the guard DOES enumerate claim records rather
    than reading ownership off the token, and the asymmetry is deliberate:
    there is no token to read an owner from, and the denial-of-service argument
    against enumeration (records are immortal, so a once-claimed segment would
    become permanently un-translatable) does not apply to a project that has no
    claim records at all -- which is every project predating this release.

    THE MUTATION THAT MAKES THIS FAIL: return None instead of consulting
    any_foreign_claim() on the `owner is None` branch of
    claim_record.foreign_owner_refusal(). The sibling test above stays green
    under that mutation, which is the whole point of asserting both."""
    ctx = _minimal_ctx(tmp_path, run_id=RUN_B)
    path = CLAIM_RECORD.claimed_path(RUN_A, "seg01", ctx.dirs["runs_dir"])
    ok, detail = CLAIM_RECORD.write_claim_record(
        path, _fixture_claim_payload(CLAIM_RECORD, "seg01", RUN_A))
    assert ok, detail
    _write_draft(ctx, "seg01", token=None, text="HAND EDIT OWNED BY RUN A")

    refusal = DRIVER.claim_refusal_for_translate(ctx, "seg01")

    assert refusal is not None, (
        "a tokenless draft that run A still holds a claim on is D9's lost-token "
        "state, not an unowned draft -- translating destroys the hand edit"
    )
    assert RUN_A in refusal, f"the refusal must name the holder: {refusal!r}"
    assert "#438 D8" in refusal, refusal


@pytest.mark.parametrize("token", ["RUN-A", "RUN-A:"])
def test_D8_owner_parse_agrees_with_both_peer_parsers(tmp_path, token):
    """A token with no colon, or with an empty seg half, names NO run --
    select_segments.py's draft_run_id() and draft_ready.py's _claim_run_id()
    both reject exactly these, and a guard that accepted them would hold a
    different notion of ownership than the two components that decide it
    everywhere else.

    Asserted through the OBSERVABLE consequence rather than by calling the
    parser: with no claim record anywhere, a token naming no run falls to the
    tokenless branch and proceeds. A `split(':')[0]` parser would instead
    derive owner 'RUN-A' and consult its namespace."""
    assert CLAIM_RECORD.draft_owner_run_id(token) is None
    ctx = _minimal_ctx(tmp_path, run_id=RUN_B)
    _write_draft(ctx, "seg01", token=token)
    assert DRIVER.claim_refusal_for_translate(ctx, "seg01") is None


def test_D8_still_allows_a_foreign_token_that_nobody_actually_holds(tmp_path):
    """The token names run A but A holds NO claim record. Nothing releases a
    claim, so ownership has to be read off the token plus a LIVE record, never
    off the token alone -- and draft_ready.py reports this same state as
    closer to a LOST claim than a live one. Blocking it would strand the
    recovery.

    This is also why the fix does not enumerate runs/*/.claimed.<seg>: records
    are immortal, so any once-claimed segment would become permanently
    un-translatable by anybody."""
    ctx = _ctx_with_foreign_claim(tmp_path, hold=False)
    assert DRIVER.claim_refusal_for_translate(ctx, "seg01") is None


def test_D8_still_allows_a_draft_stamped_by_THIS_run(tmp_path):
    """Own token, no foreign owner -- the ordinary resume case."""
    ctx = _minimal_ctx(tmp_path, run_id=RUN_B)
    _write_draft(ctx, "seg01", token=f"{RUN_B}:seg01")
    assert DRIVER.claim_refusal_for_translate(ctx, "seg01") is None


@pytest.mark.parametrize("raw,label", [
    ("{not json", "not valid JSON"),
    ('["a list"]', "not a JSON object"),
])
def test_D8_refuses_a_draft_whose_owner_cannot_be_read(tmp_path, raw, label):
    """Content exists and this run cannot establish who owns it. Same
    direction the pre-existing AMBIGUOUS branch takes, and for the same
    reason: failing to block is the destructive direction, while over-blocking
    only leaves the segment recoverable for the next invocation."""
    ctx = _minimal_ctx(tmp_path, run_id=RUN_B)
    _write_draft(ctx, "seg01", raw=raw)
    refusal = DRIVER.claim_refusal_for_translate(ctx, "seg01")
    assert refusal is not None, f"a draft that is {label} must not be overwritten"
    assert "#438 D8" in refusal, refusal


def test_D8_refuses_when_the_token_names_an_unusable_run_id(tmp_path):
    """A run id claim_record.py will not build a path from. The driver cannot
    look the owner up at all, which is strictly worse than a record it cannot
    read -- so it refuses rather than treating "cannot look" as "nobody
    there"."""
    ctx = _ctx_with_foreign_claim(tmp_path, hold=False, token="../../etc:seg01")
    refusal = DRIVER.claim_refusal_for_translate(ctx, "seg01")
    assert refusal is not None
    assert "#438 D8" in refusal, refusal


# ---------------------------------------------------------------------------
# E -- _definitive_stat() / _resumable_run_id_candidates(), round-5
# (post-1.21.0 codex review) hardening. See the Scope section of this file's
# own module docstring for why this lives here rather than in
# tests/segment_dispatch_driver.test.py, which owns the rest of
# _resumable_run_id_candidates()'s coverage.
#
# Path.is_dir()/Path.is_file() both swallow the underlying stat error and
# answer False on ANY OSError (measured on the Python this ships against,
# 3.14.6, for both EACCES and ELOOP) -- so an `except OSError` wrapped
# around either call never fires, and "I could not look" (EACCES/EIO/ELOOP)
# reads identically to "it is not there" (ENOENT). _definitive_stat() closes
# that: os.stat() directly, None ONLY for FileNotFoundError/
# NotADirectoryError, DriverError (exit_code=2, naming the path and what
# could not be established) for every other OSError. Every test below is
# staged with a SYMLINK LOOP, not a chmod fixture: ELOOP is not a permission
# bit, so it needs no root guard (chmod 0o000 is silently a no-op under root
# -- see claim_chokepoint.test.py's own `_needs_unprivileged_uid`), and it
# breaks exactly the one entry it is applied to, leaving everything else in
# the fixture intact.
# ---------------------------------------------------------------------------


def test_resumable_run_id_candidates_refuses_on_an_unstattable_entry(tmp_path):
    """An entry under runs_dir/ that _definitive_stat() cannot classify must
    refuse via DriverError naming the entry, not silently vanish from the
    candidate list the way the pre-round-5 Path.is_dir() call did (see
    _resumable_run_id_candidates()'s own docstring, "codex round-5"
    paragraph, for the exact #438 hole a silently-dropped candidate opens:
    a fresh run id minted in its place, orphaning an in-progress hand-edit
    that still names the old run and reads as unowned to every ownership
    guard downstream)."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    durable_root = tmp_path / "durable"
    durable_root.mkdir()

    good = runs_dir / "20260101T000000Z"
    good.mkdir()
    (good / "input.digest").write_text("digest-a", encoding="utf-8")

    loop = runs_dir / "20260202T000000Z"
    loop.symlink_to(loop)

    # Fixture preconditions, proven with the real syscalls this test is
    # actually about rather than taken on faith: the listing succeeds and
    # still includes the loop entry, os.stat() on it raises, and is_dir()
    # answers False on that same entry -- so a future Python that stops
    # suppressing the error fails this assertion loudly instead of the test
    # quietly staging something else.
    assert loop.name in {p.name for p in runs_dir.iterdir()}
    with pytest.raises(OSError):
        os.stat(loop)
    assert loop.is_dir() is False

    with pytest.raises(DRIVER.DriverError) as excinfo:
        DRIVER._resumable_run_id_candidates(runs_dir, durable_root)
    assert loop.name in str(excinfo.value), excinfo.value
    assert excinfo.value.exit_code == 2


def test_resumable_run_id_candidates_refuses_when_runs_dir_itself_is_unstattable(tmp_path):
    """Same rule one level up: `runs_dir` itself definitively-not-existing is
    an ordinary "no resumable prior run" ([] returned, no run yet exists) --
    but `runs_dir` being UNREADABLE is a different answer this driver cannot
    tell apart from ENOENT without _definitive_stat()."""
    durable_root = tmp_path / "durable"
    durable_root.mkdir()
    runs_dir = tmp_path / "runs"
    runs_dir.symlink_to(runs_dir)

    # Same swallow, one level up: is_dir() (and exists()) both answer False
    # on this ELOOP, indistinguishable from a runs_dir that was never
    # created -- the exact ambiguity _definitive_stat() exists to resolve.
    assert runs_dir.is_dir() is False
    with pytest.raises(OSError):
        os.stat(runs_dir)

    with pytest.raises(DRIVER.DriverError) as excinfo:
        DRIVER._resumable_run_id_candidates(runs_dir, durable_root)
    assert str(runs_dir) in str(excinfo.value), excinfo.value
    assert excinfo.value.exit_code == 2


def test_resumable_run_id_candidates_still_resolves_real_candidates_in_order(tmp_path):
    """ALLOW-DIRECTION CONTROL for the two refusal tests above: an ordinary,
    fully readable runs_dir must still return every real candidate, in
    _resumable_run_id_candidates()'s documented most-recent-first order.
    Without this, the two refusal tests above would be indistinguishable
    from a version that refuses UNCONDITIONALLY -- a change that merely
    looks like a targeted fix."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    durable_root = tmp_path / "durable"
    durable_root.mkdir()

    for run_id in ("20260101T000000Z", "20260202T000000Z", "20260303T000000Z"):
        d = runs_dir / run_id
        d.mkdir()
        (d / "input.digest").write_text("digest", encoding="utf-8")
    # A non-run-id entry runs_dir also legitimately holds (this function's
    # own docstring: ledger.json, workflows/, ...) -- excluded by the
    # name-shape check before any stat, must not perturb the real
    # candidates' order.
    (runs_dir / "ledger.json").write_text("{}", encoding="utf-8")

    candidates = DRIVER._resumable_run_id_candidates(runs_dir, durable_root)
    assert candidates == [
        "20260303T000000Z", "20260202T000000Z", "20260101T000000Z",
    ]


def test_resumable_run_id_candidates_skips_a_definitively_absent_entry(tmp_path):
    """The other side of the split _definitive_stat() draws: an entry whose
    name matches the run-id shape but resolves to nothing at all (a dangling
    symlink -> ENOENT) is DEFINITIVELY not a run directory, and must be
    quietly skipped -- exactly like an entry validate_run_id() itself
    disqualifies -- not turned into a DriverError. Refusing here would make
    every ordinary broken symlink under runs_dir fatal, which is not what
    round-5 asked for; the boundary is "could not tell" vs. "definitely not
    there", not "anything not a clean directory"."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    durable_root = tmp_path / "durable"
    durable_root.mkdir()

    good = runs_dir / "20260101T000000Z"
    good.mkdir()
    (good / "input.digest").write_text("digest", encoding="utf-8")

    dangling = runs_dir / "20260202T000000Z"
    dangling.symlink_to(runs_dir / "no-such-target")

    # Fixture preconditions via the real syscalls: listed, os.stat() raises
    # the DEFINITIVE (not swallowed-generic) FileNotFoundError, is_dir()
    # answers False.
    assert dangling.name in {p.name for p in runs_dir.iterdir()}
    with pytest.raises(FileNotFoundError):
        os.stat(dangling)
    assert dangling.is_dir() is False

    candidates = DRIVER._resumable_run_id_candidates(runs_dir, durable_root)
    assert candidates == ["20260101T000000Z"]
