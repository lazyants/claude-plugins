"""tests/claim_forces_review_only.test.py -- #450: a segment THIS
INVOCATION admitted a claim for (ctx.claims, #438 D3) must never be
translated, independently of what the ON-DISK claim record says by the
time dispatch actually reaches it.

## Scope

This file tests exactly the one thing #450 added:
`claim_capability_refusal_for_translate()` and its wiring into
process_segment()'s own `if action["action"] == "translate":` branch --
never `claim_refusal_for_translate()` itself (#438 D8, tested at length in
tests/claim_driver.test.py) or codex_job.py's own separate chokepoint.

The defect this closes: `select_segments.py` grants a claim, this driver
validates it once (`parse_claims_field()`, #438 D3) and folds it into
`ctx.claims` -- and then never reads that fact again. The two existing D8
chokepoints both re-derive ownership from the ON-DISK claim record at
dispatch time, and if THAT record is gone by then (a partial restore, a
`runs/` prune, any concurrent writer -- while ctx.claims, private process
memory, still names the segment) both of them read "nothing found" as
"nothing was ever granted" and let a translate through over a
hand-edited, re-review-only draft. This file proves ctx.claims is now
enforced on its own, unconditionally, regardless of on-disk agreement.

## Fixture strategy

Duplicated from tests/claim_driver.test.py's own Section C fixture
(itself duplicated from tests/segment_dispatch_driver.test.py's
`phase2_project()` composition) -- this project's tests never import one
another. The REAL `segment_dispatch_driver.py`, `claim_record.py`,
`resume_setup.py`, `ledger_update.py`, `draft_sha1.py` are staged
unmodified; `cache_key.py`, `resolve_codex_companion.py`, `draft_ready.py`,
`validate_draft.py`, `codex_job.py` are small controllable fakes matching
the real scripts' observable contract. Every test here calls
`process_segment()`/`claim_capability_refusal_for_translate()` directly
against a hand-built `DispatchContext` -- never a full CLI dispatch --
because this file's whole point is what THIS INVOCATION's own `ctx.claims`
does, which only a directly-built context can vary independently of
whatever `select_segments.py` would itself decide to admit.

Deliberately never writes an on-disk claim record (`claim_record.py`'s own
`write_claim_record()`) in the REFUSE-side test below -- that absence IS
the scenario #450 exists for, not an oversight. tests/claim_driver.test.py
already covers the case where the on-disk record is present.
"""
import importlib.util
import json
import shutil
from pathlib import Path

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
# claim_capability_refusal_for_translate() unit tests below, which build
# their own minimal DispatchContext rather than a whole phase2 fixture.
DRIVER = _load_module(DRIVER_SRC, "segment_dispatch_driver_under_test_claim_cap")


# ---------------------------------------------------------------------------
# A -- claim_capability_refusal_for_translate() (#450), pure unit tests
# against a bare DispatchContext. No fixture root, no disk I/O at all: the
# whole point of this function is that it answers from ctx.claims alone.
# ---------------------------------------------------------------------------


def _bare_ctx(claims=None):
    return DRIVER.DispatchContext(
        dirs={}, run_id="RUN-A", translate_cfg={"max_fix_rounds": 2},
        companion_path="/fake/companion.mjs", durable_root_str=None, plugin_root_str=None,
        node_bin="node", session_id="test-session", claims=claims,
    )


def test_capability_refusal_returns_none_when_seg_absent_from_claims():
    ctx = _bare_ctx(claims={"seg99": "from-cap"})
    assert DRIVER.claim_capability_refusal_for_translate(ctx, "seg01") is None


def test_capability_refusal_returns_none_when_claims_is_empty():
    ctx = _bare_ctx(claims={})
    assert DRIVER.claim_capability_refusal_for_translate(ctx, "seg01") is None


def test_capability_refusal_returns_none_when_claims_is_the_default():
    # DispatchContext's own claims=None default becomes {} -- confirms the
    # capability check tolerates a context nobody passed claims= to at all
    # (every pre-#450 call site), not only one that passed an empty dict
    # explicitly.
    ctx = _bare_ctx(claims=None)
    assert DRIVER.claim_capability_refusal_for_translate(ctx, "seg01") is None


def test_capability_refusal_names_the_segment_and_profile_for_from_cap():
    ctx = _bare_ctx(claims={"seg01": "from-cap"})
    refusal = DRIVER.claim_capability_refusal_for_translate(ctx, "seg01")
    assert refusal is not None
    assert "seg01" in refusal
    assert "from-cap" in refusal
    assert "#450" in refusal


def test_capability_refusal_names_the_segment_and_profile_for_from_converged():
    ctx = _bare_ctx(claims={"seg01": "from-converged"})
    refusal = DRIVER.claim_capability_refusal_for_translate(ctx, "seg01")
    assert refusal is not None
    assert "seg01" in refusal
    assert "from-converged" in refusal


def test_capability_refusal_is_per_segment_not_per_invocation():
    # A refusal for one claimed segment must not leak onto an UNCLAIMED
    # sibling in the same claims dict -- the capability is scoped to the
    # one id it was granted for, exactly like the on-disk record it stands
    # in for is scoped to one seg.
    ctx = _bare_ctx(claims={"seg01": "from-cap"})
    assert DRIVER.claim_capability_refusal_for_translate(ctx, "seg02") is None
    refusal = DRIVER.claim_capability_refusal_for_translate(ctx, "seg01")
    assert refusal is not None


# ===========================================================================
# B -- process_segment()-level end-to-end tests (#450) against a full
# phase2 fixture. Duplicated from tests/claim_driver.test.py's own Section
# C fixture composition (this project's tests never import one another).
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

# Controllable fake codex_job.py -- writes an argv log every call (so a test
# can assert NOTHING was dispatched) and, on success, writes a real draft
# (translate) or a clean/coverage_ok review (review) using the REAL, staged
# draft_sha1.py for the sha1 -- never a hand-duplicated hash. Identical
# observable contract to tests/claim_driver.test.py's own
# FAKE_CODEX_JOB_PHASE2_PY.
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


def make_durable_root(tmp_path, name="durable_root", profile_yaml=FULL_PROFILE_YAML):
    """Isolated durable_root carrying: the real segment_dispatch_driver.py
    + claim_record.py under scripts/ (both required for
    claim_refusal_for_translate()'s own sibling import, which
    process_segment() still calls right after the #450 capability check),
    a minimal profile.yml, and the runs/segments scaffolding. Does NOT
    stage select_segments.py -- every test here drives process_segment()
    directly and never shells out to it."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DRIVER_SRC, scripts_dir / "segment_dispatch_driver.py")
    shutil.copy2(CLAIM_RECORD_SRC, scripts_dir / "claim_record.py")
    shutil.copy2(RESUME_SETUP_SRC, scripts_dir / "resume_setup.py")
    shutil.copy2(LEDGER_UPDATE_SRC, scripts_dir / "ledger_update.py")
    shutil.copy2(DRAFT_SHA1_SRC, scripts_dir / "draft_sha1.py")
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(DRAFT_SHA1_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
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
    THERE, mirroring tests/claim_driver.test.py's own
    `_load_fixture_driver()`."""
    return _load_module(root / "scripts" / "segment_dispatch_driver.py", "segment_dispatch_driver_fixture_claim_cap")


def _fixture_ctx(root, run_id, claims=None, translate_cfg=None):
    driver_mod = _load_fixture_driver(root)
    dirs = driver_mod.resolve_dirs(None)
    ctx = driver_mod.DispatchContext(
        dirs=dirs, run_id=run_id, translate_cfg=translate_cfg or dict(_FIXTURE_TRANSLATE_CFG),
        companion_path="/fake/codex-companion.mjs", durable_root_str=None, plugin_root_str=None,
        node_bin="node", session_id="test-session", claims=claims,
    )
    return driver_mod, ctx


def _argv_log(root):
    log_path = root / "test_fixture_argv_log.jsonl"
    if not log_path.is_file():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_claimed_segment_whose_action_is_translate_is_refused_even_with_no_on_disk_claim_record(tmp_path):
    """The #450 defect itself. No draft on disk at all (a not_started
    segment, exactly like the missing-draft scenario #438 D8's own
    residual case describes) -- derive_next_action() falls straight to
    {"action": "translate"}. Crucially, NO on-disk claim record is ever
    written for this run+seg -- simulating the record having been pruned,
    lost in a partial restore, or simply never persisted, the exact state
    that leaves claim_refusal_for_translate() unable to see anything
    (CLAIM_ABSENT -> foreign_owner_refusal() -> proceed, since there is no
    draft yet to read an owner off either). Only ctx.claims -- this
    invocation's own memory of what select_segments.py granted -- still
    knows seg01 was claimed. The capability check must catch it anyway,
    and must do so BEFORE anything is dispatched or written."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {"seg01": make_cache_key("seg01")})
    write_fixture_segpack(root, "seg01")
    driver_mod, ctx = _fixture_ctx(root, run_id="RUN-A", claims={"seg01": "from-converged"})

    # Setup check: the OLDER, on-disk-only chokepoint genuinely sees nothing
    # here -- proving the scenario actually needs the #450 fix, rather than
    # accidentally being caught by the pre-existing #438 D8 layer instead.
    assert driver_mod.claim_refusal_for_translate(ctx, "seg01") is None, (
        "setup check: this scenario must be invisible to the on-disk-only "
        "chokepoint, or it is not exercising #450's own gap"
    )

    result = driver_mod.process_segment("seg01", ctx)

    assert result["outcome"] == "failed", result
    assert result["reason"] == "invocation-claim-translate-refused", result
    assert "seg01" in result["detail"]
    assert "from-converged" in result["detail"]
    assert _argv_log(root) == [], "codex_job.py must never have been invoked"
    assert not (root / "runs" / "ledger.d" / "seg01.json").exists(), (
        "no ledger fragment may be written on refusal"
    )
    assert not (root / "segments" / "seg01.draft.json").exists(), (
        "no draft may be written on refusal"
    )


def test_a_from_stalled_claimed_segment_whose_action_is_translate_is_refused_even_with_no_on_disk_claim_record(tmp_path):
    """#455's profile, same #450 gap as the test immediately above: the
    capability check reads `seg in ctx.claims` unconditionally, never which
    profile string the claim carries -- so a mutant that special-cases
    'from-stalled' out of the refusal (return None only for that one
    profile, leaving --from-cap/--from-converged intact) would pass every
    test above this one, since none of them ever construct a context whose
    claims dict names 'from-stalled'. This is the ONLY thing that changes
    from the --from-converged version above; the fixture, the missing-draft
    setup, and all three assertions are identical on purpose."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {"seg01": make_cache_key("seg01")})
    write_fixture_segpack(root, "seg01")
    driver_mod, ctx = _fixture_ctx(root, run_id="RUN-A", claims={"seg01": "from-stalled"})

    # Setup check: the OLDER, on-disk-only chokepoint genuinely sees nothing
    # here -- proving the scenario actually needs the #450 fix, rather than
    # accidentally being caught by the pre-existing #438 D8 layer instead.
    assert driver_mod.claim_refusal_for_translate(ctx, "seg01") is None, (
        "setup check: this scenario must be invisible to the on-disk-only "
        "chokepoint, or it is not exercising #450's own gap"
    )

    result = driver_mod.process_segment("seg01", ctx)

    assert result["outcome"] == "failed", result
    assert result["reason"] == "invocation-claim-translate-refused", result
    assert "seg01" in result["detail"]
    assert "from-stalled" in result["detail"]
    assert _argv_log(root) == [], "codex_job.py must never have been invoked"
    assert not (root / "runs" / "ledger.d" / "seg01.json").exists(), (
        "no ledger fragment may be written on refusal"
    )
    assert not (root / "segments" / "seg01.draft.json").exists(), (
        "no draft may be written on refusal"
    )


def test_the_same_segment_with_no_claim_in_this_invocation_dispatches_normally(tmp_path):
    """The ALLOW side, without which a version of the #450 fix that simply
    refuses every translate unconditionally would pass the refuse-side
    test above while silently halting the whole pipeline -- the WORSE
    defect a per-segment capability check must not trade for. Identical
    setup to the refuse-side test, MINUS the claim: ctx.claims carries
    nothing for seg01, so it must translate and converge exactly as it did
    before #450."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {"seg01": make_cache_key("seg01")})
    write_fixture_segpack(root, "seg01")
    driver_mod, ctx = _fixture_ctx(root, run_id="RUN-A", claims={})

    result = driver_mod.process_segment("seg01", ctx)

    assert result["outcome"] == "converged", result
    kinds = [entry["kind"] for entry in _argv_log(root)]
    assert kinds == ["translate", "review"], kinds


def test_an_unrelated_claimed_segment_does_not_block_this_ones_translate(tmp_path):
    """Companion to the ALLOW-side test above, at the batch level rather
    than the single-flag level: ctx.claims naming a DIFFERENT segment must
    not leak into this segment's own dispatch -- the capability is
    per-segment, not per-invocation. (seg02 is never processed here; its
    presence in ctx.claims alone is the thing under test.)"""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {"seg01": make_cache_key("seg01")})
    write_fixture_segpack(root, "seg01")
    driver_mod, ctx = _fixture_ctx(root, run_id="RUN-A", claims={"seg02": "from-cap"})

    result = driver_mod.process_segment("seg01", ctx)

    assert result["outcome"] == "converged", result
    kinds = [entry["kind"] for entry in _argv_log(root)]
    assert kinds == ["translate", "review"], kinds


def test_a_claimed_segment_whose_action_is_review_is_unaffected(tmp_path):
    """derive_next_action() never even reaches {"action": "translate"} here
    -- a valid, correctly-tokened draft already on disk with no review.json
    yet routes straight to {"action": "review", "round_label": "1"} on its
    own. The #450 capability check must be a no-op on this path: it is
    scoped to the translate branch alone, and process_segment() must
    dispatch the review exactly as if seg01 carried no claim at all."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {"seg01": make_cache_key("seg01")})
    write_fixture_segpack(root, "seg01")
    driver_mod, ctx = _fixture_ctx(root, run_id="RUN-A", claims={"seg01": "from-converged"})
    draft = {
        "seg": "seg01", "blocks": {"p1": "hola"},
        "dispatch_token": driver_mod.translate_dispatch_token("RUN-A", "seg01"),
    }
    (root / "segments" / "seg01.draft.json").write_text(json.dumps(draft), encoding="utf-8")

    result = driver_mod.process_segment("seg01", ctx)

    assert result["outcome"] == "converged", result
    kinds = [entry["kind"] for entry in _argv_log(root)]
    assert kinds == ["review"], (
        f"a claimed segment routed to 'review' by derive_next_action() must dispatch "
        f"the review untouched by the #450 capability check, got: {kinds}"
    )


def test_a_claimed_segment_whose_action_is_needs_fix_is_unaffected(tmp_path):
    """A non-clean, non-final review whose recorded draft_sha1 still
    matches the current draft -- derive_next_action() routes to
    {"action": "needs_fix", ...}, never anywhere near the translate branch
    the #450 check guards. process_segment() STOPS here by design
    (applying a fix is a real LLM content-editing turn this driver cannot
    perform -- see its own module docstring): outcome must be "needs_fix"
    and codex_job.py must never have been invoked for this iteration."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {"seg01": make_cache_key("seg01")})
    write_fixture_segpack(root, "seg01")
    driver_mod, ctx = _fixture_ctx(root, run_id="RUN-A", claims={"seg01": "from-cap"})
    token = driver_mod.translate_dispatch_token("RUN-A", "seg01")
    draft_path = root / "segments" / "seg01.draft.json"
    draft_path.write_text(json.dumps({"seg": "seg01", "blocks": {"p1": "hola"}, "dispatch_token": token}), encoding="utf-8")
    sha1_mod = driver_mod._load_draft_sha1_module(ctx.dirs["scripts_dir"])
    findings = [{"loc": "p1:1", "severity": "major", "issue": "x", "suggest": "y"}]
    review = {
        "clean": False, "coverage_ok": True, "findings": findings,
        "draft_sha1": sha1_mod.draft_content_sha1(draft_path),
        "dispatch_token": driver_mod.review_dispatch_token("RUN-A", "seg01", "1"),
    }
    (root / "segments" / "seg01.review.json").write_text(json.dumps(review), encoding="utf-8")

    result = driver_mod.process_segment("seg01", ctx)

    assert result["outcome"] == "needs_fix", result
    assert result["round_label"] == "1", result
    assert _argv_log(root) == [], "needs_fix must dispatch nothing of its own"


# ===========================================================================
# C -- the WIRING itself: does segment_dispatch_driver.py's own run()
# (segment_dispatch_driver.py:5750's `claims=claims`) actually carry a REAL
# select_segments.py admission into ctx.claims, or does
# claim_capability_refusal_for_translate() only ever get exercised against a
# context THIS SUITE injected `claims=` into by hand? Every test in Sections
# A/B builds DispatchContext directly and passes `claims=` itself -- deleting
# `claims=claims` from the real construction leaves all eleven of them green,
# because none of them ever go through run() at all. This section does: it
# drives run() end to end, with a REAL select_segments.py subprocess call
# admitting the claim, and asserts the #450 refusal fires from THAT.
#
# Fixture strategy: entirely NEW names, never Section A/B's own
# make_durable_root()/write_review()/etc -- this project's tests never
# import one another, and a same-named redefinition later in this same
# module would silently replace Section B's own fixture for every test that
# runs after this point in the file (Python module globals are shared
# across all functions defined at module scope). Duplicated instead from
# tests/claim_end_to_end.test.py's own make_driver_root()/
# build_from_cap_segment() -- the only other fixture in this suite that
# drives select_segments.py's real admission AND segment_dispatch_driver.py's
# own run() together over the REAL draft_ready.py/validate_draft.py leaf
# gates (Section B's own make_durable_root(), directly above, stages FAKE
# draft_ready.py/validate_draft.py and never shells out to select_segments.py
# at all -- it cannot admit a real claim). FAKE_CACHE_KEY_PY,
# FAKE_RESOLVE_CODEX_COMPANION_PY, FAKE_CODEX_JOB_PHASE2_PY, CACHE_KEY_FIELDS,
# make_cache_key() and _argv_log() from Section B above are reused as-is (pure
# content/read-only helpers, safe to share).
# ===========================================================================

WIRING_SELECT_SCRIPT_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
WIRING_LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
WIRING_DRAFT_READY_SRC = SCRIPTS_SRC_DIR / "draft_ready.py"
WIRING_VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
WIRING_REVIEW_READY_SRC = SCRIPTS_SRC_DIR / "review_ready.py"

for _src in (
    WIRING_SELECT_SCRIPT_SRC, WIRING_LEDGER_MERGE_SRC, WIRING_DRAFT_READY_SRC,
    WIRING_VALIDATE_DRAFT_SRC, WIRING_REVIEW_READY_SRC,
):
    assert _src.is_file(), f"expected script not found: {_src}"

# The three sections real validate_draft.py's own load_profile()/
# ProfileConfig requires, PLUS the engine/source/target keys
# segment_dispatch_driver.py's own load_engine_config()/load_translate_config()
# read -- both sets at once, since this section's own fixture drives both
# scripts for real in the SAME invocation (segment_dispatch_driver.test.py's
# own DRIVER_PROFILE/DEFAULT_PROFILE composition, spelled here as a raw YAML
# string to match this file's own house style rather than adding a `yaml`
# import Section A/B never needed).
WIRING_PROFILE_YAML = (
    "verse_policy:\n"
    "  mode: full_rhymed_plus_literal\n"
    "  threshold_lines: null\n"
    "footnotes:\n"
    "  apparatus_policy: translate_all\n"
    "validation:\n"
    "  untranslated_sentinel: \"[TODO-UNTRANSLATED]\"\n"
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
    "    code: en\n"
)

WIRING_FN_PH = "⟦FNREF_1⟧"
WIRING_V_PH_A = "⟦VERSE_vA⟧"
WIRING_V_PH_B = "⟦VERSE_vB⟧"
WIRING_SOURCE_RUN_ID = "20260801T090000Z"


def wiring_clean_segpack(seg):
    """Duplicated from tests/claim_end_to_end.test.py's own clean_segpack()
    -- a segpack that genuinely passes the REAL validate_draft.py/
    draft_ready.py structural gates, not a minimal stand-in like Section B's
    own write_fixture_segpack() (which only ever faces the FAKE leaf
    scripts)."""
    return {
        "seg": seg,
        "blocks": [
            {"id": "p1", "order_index": 0, "source_html": f"<p>Some prose with a note {WIRING_FN_PH} attached.</p>"},
            {"id": "vblockA", "order_index": 1, "source_html": "<p>Premiere ligne<br/>Deuxieme ligne</p>"},
            {"id": "vblockB", "order_index": 2, "source_html": "<p>Autre premiere<br/>Autre deuxieme</p>"},
        ],
        "footnotes": [{"n": 1, "source_text": "Une note en francais."}],
        "verses": [
            {"vid": "vA", "placeholder": WIRING_V_PH_A, "parent_block": "vblockA"},
            {"vid": "vB", "placeholder": WIRING_V_PH_B, "parent_block": "vblockB"},
        ],
        "names": [], "canon_names": [], "new_names": [], "canon_map": {},
        "generation_hashes": {
            "source_extraction_hash": "sxh-0", "source_input_hash": "sih-0",
            "particle_config_hash": "pch-0", "derivation_bundle_hash": "dbh-0",
        },
    }


def wiring_clean_draft(seg):
    """Duplicated from tests/claim_end_to_end.test.py's own clean_draft()."""
    return {
        "seg": seg,
        "blocks": {
            "p1": f"Some translated prose with a note {WIRING_FN_PH} attached.",
            "vblockA": WIRING_V_PH_A, "vblockB": WIRING_V_PH_B,
        },
        "footnotes": {"1": "A translated note in English."},
        "verses": {
            "vA": {"rendered": "First line rendered so\nSecond line rendered so",
                   "literal_gloss": "The first line means one thing, the second means another"},
            "vB": {"rendered": "Another line rendered here\nAnother second line here",
                   "literal_gloss": "This gloss says something different from the rendering above"},
        },
        "names": [], "notes": [],
    }


def wiring_write_segpack(root, seg, segpack):
    (root / "segments" / f"segpack_{seg}.json").write_text(json.dumps(segpack, ensure_ascii=False), encoding="utf-8")


def wiring_write_draft_doc(root, seg, draft):
    (root / "segments" / f"{seg}.draft.json").write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")


def wiring_write_review(root, seg, review):
    (root / "segments" / f"{seg}.review.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")


def wiring_write_canon(root, entries):
    (root / "canon.json").write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")


def wiring_write_fragment(root, seg, record):
    (root / "runs" / "ledger.d" / f"{seg}.json").write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def wiring_make_run_dir(root, run_id):
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    digest_path = run_dir / "input.digest"
    if not digest_path.exists():
        digest_path.write_text(json.dumps({"digest": f"stub-{run_id}"}), encoding="utf-8")


def wiring_build_from_cap_segment(root, seg, fixture_keys):
    """P2 (--from-cap) population, duplicated from tests/claim_end_to_end.
    test.py's own build_from_cap_segment(): non_converged/reason=cap, NO
    .ever_converged sentinel, a stored non-clean review with findings -- the
    shape the REAL select_segments.py actually admits under --from-cap."""
    segpack = wiring_clean_segpack(seg)
    wiring_write_segpack(root, seg, segpack)
    wiring_write_canon(root, {})

    draft = wiring_clean_draft(seg)
    draft["blocks"] = dict(draft["blocks"])
    draft["blocks"]["p1"] = draft["blocks"]["p1"] + " Hand-fixed after the cap."
    draft["dispatch_token"] = f"{WIRING_SOURCE_RUN_ID}:{seg}"
    wiring_write_draft_doc(root, seg, draft)

    wiring_make_run_dir(root, WIRING_SOURCE_RUN_ID)

    fixture_keys[seg] = make_cache_key(seg)

    review = {
        "clean": False,
        "coverage_ok": True,
        "findings": [{"loc": "p1", "severity": "medium", "issue": "awkward phrasing", "suggest": "rephrase"}],
        "draft_sha1": "0" * 40,
    }
    wiring_write_review(root, seg, review)

    frag = {"timestamp": "2026-01-01T00:00:00Z", "status": "non_converged", "reason": "cap", "rounds": 4}
    wiring_write_fragment(root, seg, frag)


def wiring_build_from_stalled_segment(root, seg, fixture_keys):
    """P3 (--from-stalled, #455) population, duplicated from
    tests/claim_stalled_admission.test.py's own build_from_stalled_segment():
    materialized status in_progress, a `.ever_converged.<seg>` sentinel
    PRESENT, no `reviewed_draft_sha1`, a stored review that is STALE against
    the current draft -- the shape the REAL select_segments.py actually
    admits under --from-stalled. Mirrors wiring_build_from_cap_segment()
    immediately above; see this file's own module docstring for why this
    file builds its own copy rather than importing that one.

    The stale review's own `draft_sha1` is computed by the REAL, already-
    staged draft_sha1.py against the PRE-EDIT draft actually written to
    disk (never a hand-rolled oracle) -- staleness is an ENTRY condition
    this profile's own admission gate checks, so a wrong value here would
    make the fixture refuse for the wrong reason rather than admit."""
    segpack = wiring_clean_segpack(seg)
    wiring_write_segpack(root, seg, segpack)
    wiring_write_canon(root, {})

    draft_sha1_mod = _load_module(root / "scripts" / "draft_sha1.py", "draft_sha1_for_wiring_stalled")
    draft_path = root / "segments" / f"{seg}.draft.json"

    pre_edit_draft = wiring_clean_draft(seg)
    wiring_write_draft_doc(root, seg, pre_edit_draft)
    reviewed_sha1 = draft_sha1_mod.draft_content_sha1(draft_path)

    # The hand-corrected bytes the stored review no longer describes --
    # what makes the stored verdict stale, matching the live seg21/errata_02
    # units this profile was designed for (SKILL.md's own P3 section).
    draft = dict(pre_edit_draft)
    draft["blocks"] = dict(draft["blocks"])
    draft["blocks"]["p1"] = draft["blocks"]["p1"] + " Hand-corrected after the driver died."
    draft["dispatch_token"] = f"{WIRING_SOURCE_RUN_ID}:{seg}"
    wiring_write_draft_doc(root, seg, draft)

    wiring_make_run_dir(root, WIRING_SOURCE_RUN_ID)

    fixture_keys[seg] = make_cache_key(seg)

    review = {
        "clean": True,
        "coverage_ok": True,
        "findings": [],
        "draft_sha1": reviewed_sha1,
        "dispatch_token": f"{WIRING_SOURCE_RUN_ID}:{seg}:r1",
    }
    wiring_write_review(root, seg, review)

    (root / "segments" / f".ever_converged.{seg}").write_text("converged\n", encoding="utf-8")

    # No `reason` key: an in_progress fragment carries none (the live units'
    # own measured shape), and writing a literal null would fail
    # ledger-record-base.schema.json's own string typing before any claim
    # gate is even reached.
    frag = {"timestamp": "2026-01-01T00:00:00Z", "status": "in_progress", "rounds": 1}
    wiring_write_fragment(root, seg, frag)


def wiring_make_driver_root(tmp_path, seg):
    """Everything segment_dispatch_driver.py's own run() needs to drive a
    REAL --from-cap claim through a REAL select_segments.py subprocess and
    then dispatch over the REAL draft_ready.py/validate_draft.py: the real
    select_segments.py, ledger_merge.py, draft_ready.py, validate_draft.py,
    review_ready.py, draft_sha1.py, claim_record.py, segment_dispatch_driver.py,
    resume_setup.py, plus FAKE cache_key.py/resolve_codex_companion.py/
    codex_job.py (reused from Section B above -- the paid codex turn is the
    one genuinely unfakeable leaf, exactly as every other file in this suite
    treats it) and the two bundle-hash markers resume_setup.py FATALs
    without. Composition duplicated from tests/claim_end_to_end.test.py's own
    make_driver_root()/make_durable_root()."""
    root = tmp_path / "wiring_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name, src in (
        ("select_segments.py", WIRING_SELECT_SCRIPT_SRC),
        ("ledger_merge.py", WIRING_LEDGER_MERGE_SRC),
        ("ledger_update.py", LEDGER_UPDATE_SRC),
        ("draft_ready.py", WIRING_DRAFT_READY_SRC),
        ("validate_draft.py", WIRING_VALIDATE_DRAFT_SRC),
        ("review_ready.py", WIRING_REVIEW_READY_SRC),
        ("draft_sha1.py", DRAFT_SHA1_SRC),
        ("claim_record.py", CLAIM_RECORD_SRC),
        ("segment_dispatch_driver.py", DRIVER_SRC),
        ("resume_setup.py", RESUME_SETUP_SRC),
    ):
        shutil.copy2(src, scripts_dir / name)
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(src.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    (scripts_dir / "resolve_codex_companion.py").write_text(FAKE_RESOLVE_CODEX_COMPANION_PY, encoding="utf-8")
    (scripts_dir / "codex_job.py").write_text(FAKE_CODEX_JOB_PHASE2_PY, encoding="utf-8")

    shutil.copytree(ASSETS_DIR / "schemas", root / "schemas")

    templates_dir = root / "templates"
    templates_dir.mkdir()
    shutil.copy2(MASS_TRANSLATE_TEMPLATE_SRC, templates_dir / "mass-translate-wf.template.js")

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    (root / "runs" / ".plugin_bundle_hash").write_text("fixture-plugin-bundle-hash\n", encoding="utf-8")
    (root / "runs" / ".orchestration_bundle_hash").write_text(
        "fixture-orchestration-bundle-hash\n", encoding="utf-8")

    profile_path = root / "profile.yml"
    profile_path.write_text(WIRING_PROFILE_YAML, encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )

    fixture_keys = {}
    wiring_build_from_cap_segment(root, seg, fixture_keys)
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": seg}]}, ensure_ascii=False), encoding="utf-8"
    )
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps(fixture_keys, ensure_ascii=False), encoding="utf-8"
    )
    return root


def wiring_make_driver_root_from_stalled(tmp_path, seg):
    """The #455 sibling of wiring_make_driver_root() immediately above --
    same staging (real scripts/schemas/templates/bundle-hash markers/
    profile.yml), the P3 (--from-stalled) population instead of P2. Kept as
    its own full copy rather than a `profile=` parameter on the existing
    function: this file's own convention is duplication over cross-
    reference (see the module docstring), and --from-stalled additionally
    needs the driver's own runs/.driver.lock to be genuinely free/
    acquirable, which nothing about the --from-cap fixture has ever had to
    consider -- a shared builder risks the two populations quietly
    entangling."""
    root = tmp_path / "wiring_root_stalled"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name, src in (
        ("select_segments.py", WIRING_SELECT_SCRIPT_SRC),
        ("ledger_merge.py", WIRING_LEDGER_MERGE_SRC),
        ("ledger_update.py", LEDGER_UPDATE_SRC),
        ("draft_ready.py", WIRING_DRAFT_READY_SRC),
        ("validate_draft.py", WIRING_VALIDATE_DRAFT_SRC),
        ("review_ready.py", WIRING_REVIEW_READY_SRC),
        ("draft_sha1.py", DRAFT_SHA1_SRC),
        ("claim_record.py", CLAIM_RECORD_SRC),
        ("segment_dispatch_driver.py", DRIVER_SRC),
        ("resume_setup.py", RESUME_SETUP_SRC),
    ):
        shutil.copy2(src, scripts_dir / name)
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(src.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    (scripts_dir / "resolve_codex_companion.py").write_text(FAKE_RESOLVE_CODEX_COMPANION_PY, encoding="utf-8")
    (scripts_dir / "codex_job.py").write_text(FAKE_CODEX_JOB_PHASE2_PY, encoding="utf-8")

    shutil.copytree(ASSETS_DIR / "schemas", root / "schemas")

    templates_dir = root / "templates"
    templates_dir.mkdir()
    shutil.copy2(MASS_TRANSLATE_TEMPLATE_SRC, templates_dir / "mass-translate-wf.template.js")

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    (root / "runs" / ".plugin_bundle_hash").write_text("fixture-plugin-bundle-hash\n", encoding="utf-8")
    (root / "runs" / ".orchestration_bundle_hash").write_text(
        "fixture-orchestration-bundle-hash\n", encoding="utf-8")

    profile_path = root / "profile.yml"
    profile_path.write_text(WIRING_PROFILE_YAML, encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )

    fixture_keys = {}
    wiring_build_from_stalled_segment(root, seg, fixture_keys)
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": seg}]}, ensure_ascii=False), encoding="utf-8"
    )
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps(fixture_keys, ensure_ascii=False), encoding="utf-8"
    )
    return root


def test_a_real_selector_claim_reaches_process_segment_through_the_real_run_wiring(tmp_path):
    """The wiring itself, proven through the real path rather than a
    hand-built DispatchContext (unlike every test in Sections A/B above): a
    codex review of this file found that all eleven of those tests
    construct DispatchContext directly and inject `claims=` themselves, so
    they prove `claim_capability_refusal_for_translate()` works but prove
    NOTHING about whether run() actually populates `ctx.claims` from the
    selector's own output -- deleting `claims=claims` from the real
    construction at segment_dispatch_driver.py:5750 leaves all eleven of
    them green, because none of them ever go through run() at all.

    This test drives run() end to end. A real `--from-cap seg01` invocation
    shells out to the REAL select_segments.py, which validates S1
    (validate_draft.py)/S2 (draft_ready.py) for real, writes a real on-disk
    claim record, re-stamps the draft's dispatch_token for real, and returns
    `claims` in its own JSON payload -- exactly the wire parse_claims_field()
    reads and DispatchContext(claims=claims) is supposed to carry forward.

    If the test asserted nothing else at this point, the claimed segment
    would simply be RE-REVIEWED (derive_next_action() would see a healthy,
    correctly re-tokened draft and a stored review, exactly as
    tests/claim_end_to_end.test.py's own driver-CLI test already proves) --
    and #450's own branch, gated on `action["action"] == "translate"`, would
    never even run. So the ONE thing this test corrupts is the ON-DISK STATE
    the real admission just wrote, at the one moment between
    select_segments.py returning and run_segment_loop() reaching this
    segment: the draft is deleted (derive_next_action() then falls through
    to {"action": "translate"} -- #438 D8's OWN named residual scenario, "a
    claimed draft that went invalid or missing between admission and
    dispatch") and the ON-DISK CLAIM RECORD is deleted too (so the OLDER,
    on-disk-only chokepoint, claim_refusal_for_translate(), also sees
    nothing here -- with the draft gone, claim_record.py's own
    foreign_owner_refusal() takes its documented "NO DRAFT on disk ->
    proceed" branch -- and cannot mask a wiring defect by catching the case
    through its own, unrelated channel). Only ctx.claims -- this
    invocation's own private memory of what the real selector actually
    granted -- can still know seg01 was claimed, exactly mirroring Section
    B's own scenario, this time reached honestly end to end.

    THE ONE NARROW PATCH, named here per its own request: run_select_
    segments() -- the driver's OWN thin subprocess wrapper around the real
    select_segments.py call -- is wrapped, never replaced: the original
    still runs unmodified and its real returned JSON is passed back
    verbatim. The wrapper only injects the corruption above, and only AFTER
    the real subprocess call has already returned and BEFORE run() reaches
    run_segment_loop(). Nothing about DispatchContext, claim_capability_
    refusal_for_translate(), or derive_next_action() is patched -- every one
    of those runs unmodified, for real, against the corrupted disk state
    this wrapper leaves behind, modelling the #450 module docstring's own
    "a partial restore, a runs/ prune, any concurrent writer" scenario."""
    seg = "seg01"
    root = wiring_make_driver_root(tmp_path, seg)
    driver_mod = _load_module(
        root / "scripts" / "segment_dispatch_driver.py", "segment_dispatch_driver_wiring_e2e"
    )

    real_run_select_segments = driver_mod.run_select_segments

    def corrupting_run_select_segments(dirs, **kwargs):
        result = real_run_select_segments(dirs, **kwargs)
        claims = result.get("claims")
        if result.get("success") and isinstance(claims, dict) and seg in claims:
            run_id = kwargs.get("run_id")
            durable_root = dirs["durable_root"]
            draft_path = durable_root / "segments" / f"{seg}.draft.json"
            assert draft_path.is_file(), (
                "setup check: the REAL select_segments.py admission must have left "
                "a re-stamped draft on disk before this wrapper deletes it, or the "
                "corruption below proves nothing about a genuine admission"
            )
            draft_path.unlink()
            claim_mod = _load_module(root / "scripts" / "claim_record.py", "claim_record_for_wiring_e2e")
            record_path = claim_mod.claimed_path(run_id, seg, dirs["runs_dir"])
            assert record_path.is_file(), (
                "setup check: the REAL select_segments.py admission must have "
                "written a durable claim record before this wrapper deletes it"
            )
            record_path.unlink()
        return result

    driver_mod.run_select_segments = corrupting_run_select_segments

    args = driver_mod.build_arg_parser().parse_args(["--only-segs", seg, "--from-cap", seg])
    dirs = driver_mod.resolve_dirs(None)

    result = driver_mod.run(args, dirs)

    assert result["success"] is True, result
    assert result["claims"] == {seg: "from-cap"}, (
        f"setup check: the REAL selector must have actually admitted the claim "
        f"for this to be a meaningful test of the wiring -- got: {result}"
    )

    seg_results = [r for r in result["results"] if r.get("seg") == seg]
    assert len(seg_results) == 1, result["results"]
    seg_result = seg_results[0]

    assert seg_result["outcome"] == "failed", seg_result
    assert seg_result["reason"] == "invocation-claim-translate-refused", seg_result
    assert seg in seg_result.get("detail", ""), seg_result
    assert "from-cap" in seg_result.get("detail", ""), seg_result

    assert _argv_log(root) == [], "codex_job.py must never have been invoked"
    assert not (root / "segments" / f"{seg}.draft.json").exists(), (
        "no draft may be (re-)written on refusal"
    )
    fragment = json.loads((root / "runs" / "ledger.d" / f"{seg}.json").read_text(encoding="utf-8"))
    assert fragment["status"] == "non_converged", (
        f"the pre-existing ledger fragment must be untouched by a refused translate "
        f"-- no in_progress write may happen on this path. Got: {fragment}"
    )


def test_a_real_selector_claimed_batch_at_exactly_the_claimed_cap_is_not_refused(tmp_path):
    """#514, through the REAL run() wiring rather than the pure helper.

    The defect this asserts against was measured in the field, not
    imagined: `check_volume_cap()` was called with `len(segs)` alone, so it
    charged every admitted id one translate job -- including the ids in
    `claims`, for which `claim_capability_refusal_for_translate()` refuses
    a translate unconditionally. On a live book 80 ids admitted under
    --from-converged at max_fix_rounds: 4 were computed as 480 against the
    shipped cap of 400 when the reachable count was 400 (exactly the cap),
    and the batch was refused with a message telling the operator to raise
    a limit that did not need raising.

    Scaled to this fixture's own profile, which uses max_fix_rounds: 2:
    one claimed id costs 3 (the two numbered reviews plus the mandatory
    final one), and the pre-#514 arithmetic charged 4. The cap is set to
    exactly 3 below, so the OLD code refuses this batch and the corrected
    code admits it -- and the boundary is `>` on both, so 3 == 3 must pass.

    Deliberately asserted through run() and its own journal rather than by
    calling check_volume_cap() directly: the keyword this release adds
    defaults to 0, i.e. to the old pessimistic arithmetic, so a helper-only
    test would stay green against a run() that never passes it. The wiring
    is the thing that can silently not happen -- exactly the reasoning the
    two wiring tests below this one were added under."""
    seg = "seg01"
    root = wiring_make_driver_root(tmp_path, seg)
    # One claimed id at this fixture's max_fix_rounds: 2 -- 3 reachable
    # codex jobs, 4 under the pre-#514 charge.
    (root / "profile.yml").write_text(
        WIRING_PROFILE_YAML.replace(
            "  max_codex_jobs_per_batch: 400\n", "  max_codex_jobs_per_batch: 3\n"
        ),
        encoding="utf-8",
    )
    driver_mod = _load_module(
        root / "scripts" / "segment_dispatch_driver.py", "segment_dispatch_driver_volume_cap_e2e"
    )

    args = driver_mod.build_arg_parser().parse_args(["--only-segs", seg, "--from-cap", seg])
    dirs = driver_mod.resolve_dirs(None)

    result = driver_mod.run(args, dirs)

    assert result.get("reason") != "batch-too-large-codex-jobs", (
        f"a claimed batch whose reachable cost is EXACTLY the cap must be admitted: {result}"
    )
    assert result["claims"] == {seg: "from-cap"}, (
        f"setup check: the REAL selector must have actually admitted the claim, or this "
        f"proves nothing about the claimed population's arithmetic -- got: {result}"
    )

    events = [
        json.loads(line)
        for journal in (root / "runs").glob("*/driver_journal.jsonl")
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    volume_events = [e for e in events if e.get("type", "").startswith("volume_check_")]
    assert len(volume_events) == 1, (
        f"exactly one volume-gate event must be journalled per invocation: {volume_events}"
    )
    passed = volume_events[0]
    assert passed["type"] == "volume_check_passed", passed
    assert passed["estimatedCodexJobs"] == 3, (
        f"one claimed id at max_fix_rounds=2 costs the two numbered reviews plus the "
        f"mandatory final one, and no translate job: {passed}"
    )
    assert passed["claimedSegs"] == 1, passed
    assert passed["codexJobsCap"] == 3, passed


def test_a_real_selector_from_stalled_claim_reaches_process_segment_through_the_real_run_wiring(tmp_path):
    """#455's sibling of the --from-cap wiring test immediately above --
    same reasoning, same corruption wrapper, same three-assertion shape.
    Exists because that test alone leaves the from-stalled capability
    proven only by Section A/B's own hand-built DispatchContext tests,
    which -- exactly as the docstring above explains for --from-cap --
    would stay green even if `claims=claims` were deleted from run()'s
    real DispatchContext construction, or if
    claim_capability_refusal_for_translate() special-cased 'from-stalled'
    out of the refusal specifically. Only a REAL select_segments.py
    admission, reached through this driver's own run(), proves the wiring
    itself for this profile too.

    --from-stalled additionally drives run()'s own runs/.driver.lock
    acquisition and its --driver-lease-held forwarding to the child
    selector (segment_dispatch_driver.py:1520-1558) -- both real here, not
    stubbed, since this test calls run() directly rather than through a
    subprocess and nothing about that machinery is mocked."""
    seg = "seg21"
    root = wiring_make_driver_root_from_stalled(tmp_path, seg)
    driver_mod = _load_module(
        root / "scripts" / "segment_dispatch_driver.py", "segment_dispatch_driver_wiring_e2e_stalled"
    )

    real_run_select_segments = driver_mod.run_select_segments

    def corrupting_run_select_segments(dirs, **kwargs):
        result = real_run_select_segments(dirs, **kwargs)
        claims = result.get("claims")
        if result.get("success") and isinstance(claims, dict) and seg in claims:
            run_id = kwargs.get("run_id")
            durable_root = dirs["durable_root"]
            draft_path = durable_root / "segments" / f"{seg}.draft.json"
            assert draft_path.is_file(), (
                "setup check: the REAL select_segments.py admission must have left "
                "a re-stamped draft on disk before this wrapper deletes it, or the "
                "corruption below proves nothing about a genuine admission"
            )
            draft_path.unlink()
            claim_mod = _load_module(root / "scripts" / "claim_record.py", "claim_record_for_wiring_e2e_stalled")
            record_path = claim_mod.claimed_path(run_id, seg, dirs["runs_dir"])
            assert record_path.is_file(), (
                "setup check: the REAL select_segments.py admission must have "
                "written a durable claim record before this wrapper deletes it"
            )
            record_path.unlink()
        return result

    driver_mod.run_select_segments = corrupting_run_select_segments

    args = driver_mod.build_arg_parser().parse_args(["--only-segs", seg, "--from-stalled", seg])
    dirs = driver_mod.resolve_dirs(None)

    result = driver_mod.run(args, dirs)

    assert result["success"] is True, result
    assert result["claims"] == {seg: "from-stalled"}, (
        f"setup check: the REAL selector must have actually admitted the claim "
        f"for this to be a meaningful test of the wiring -- got: {result}"
    )

    seg_results = [r for r in result["results"] if r.get("seg") == seg]
    assert len(seg_results) == 1, result["results"]
    seg_result = seg_results[0]

    assert seg_result["outcome"] == "failed", seg_result
    assert seg_result["reason"] == "invocation-claim-translate-refused", seg_result
    assert seg in seg_result.get("detail", ""), seg_result
    assert "from-stalled" in seg_result.get("detail", ""), seg_result

    assert _argv_log(root) == [], "codex_job.py must never have been invoked"
    assert not (root / "segments" / f"{seg}.draft.json").exists(), (
        "no draft may be (re-)written on refusal"
    )
    fragment = json.loads((root / "runs" / "ledger.d" / f"{seg}.json").read_text(encoding="utf-8"))
    assert fragment["status"] == "in_progress", (
        f"the pre-existing ledger fragment must be untouched by a refused translate "
        f"-- no new write may happen on this path. Got: {fragment}"
    )
