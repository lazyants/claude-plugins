"""Tests for sandbox.py -- the write-boundary dispatcher (plan sections 4.10, 7, owner D)."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import cm_common
import inventory
import sandbox

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
FAKE_CODEX = TESTS_DIR / "fakes" / "fake_codex.py"
SANDBOX_SCRIPT = Path(sandbox.__file__).resolve()


def _shop_cfg(legacy_root: Path, target_root: Path) -> dict:
    return {
        "schema": 1,
        "source_stack": "python",
        "target_stack": "python",
        "legacy_root": str(legacy_root),
        "legacy_package": "shop",
        "target_root": str(target_root),
        "target_package": "shop2",
        "fidelity_policy": "bug_for_bug",
        "seam": "in_process",
        "unit_granularity": "file",
        "naming_policy": "preserve",
        "net_source": "generated_golden_master",
        "dead_code_policy": "port",
        "coverage_floor_pct": 0,
        "max_fix_rounds": 3,
        "codex_bin": "codex",
    }


def _build_shop_root(work_root: Path) -> tuple[Path, dict]:
    """A durable root kept as a SIBLING of legacy_root and target_root under
    the same work_root -- never a parent of either -- exactly like a real
    migration, where target_root and legacy_root live in the project tree
    and the durable root is a separate scratch directory. This matters for
    sandbox.py's promotion-destination containment check (a promotion's
    destination must resolve inside target_root and never inside legacy_root
    or the durable root): nesting target_root under the durable root, as a
    test-only convenience, would make every legitimate promotion trip that
    check. Returns `(root, cfg)`, with `cfg` as `cm_common.load_config()`
    itself loads and validates it."""
    root = work_root / "proj"
    root.mkdir(parents=True, exist_ok=True)
    legacy_root = work_root / "legacy"
    target_root = work_root / "target"
    shutil.copytree(FIXTURES_DIR / "legacy" / "shop", legacy_root / "shop")

    cfg = _shop_cfg(legacy_root, target_root)
    (root / "migration.json").write_text(json.dumps(cfg), encoding="utf-8")
    (root / "conventions.md").write_text("Plain functions, preserve names.\n", encoding="utf-8")
    for d in ("cases", "nets", "runs"):
        (root / d).mkdir(parents=True, exist_ok=True)

    inv = inventory.build_inventory(root, cfg)
    cm_common.atomic_write_json(root / "inventory.json", inv)

    registry = json.loads((FIXTURES_DIR / "registry.shop.json").read_text(encoding="utf-8"))
    lock_rows = {
        row["source"]: {"row": row, "digest": cm_common.sha256_json(row)}
        for row in registry["rows"]
    }
    cm_common.atomic_write_json(root / "registry.lock.json", {"schema": 1, "rows": lock_rows})
    return root, cm_common.load_config(root)


@pytest.fixture
def shop_root(work_root):
    return _build_shop_root(work_root)


def _install_good_port(cfg: dict) -> None:
    target_root = Path(cfg["target_root"])
    shutil.copytree(FIXTURES_DIR / "ports" / "good" / "shop2", target_root / "shop2")


def _freeze_net(root: Path, cfg: dict, unit: str) -> None:
    """Enough of a netted state for `ledger.eligible(unit)` to return no
    reasons: a frozen net.lock.json entry whose digests match real files on
    disk and whose legacy_closure_sha256 matches the current legacy tree.

    Keyed by `inventory.closure_files` relative file paths (via
    `cm_common.file_digests`), matching exactly what `ledger.eligible()` and
    `net_capture.py` both compute -- every import-closure module file plus
    every ancestor package `__init__.py`, unit or not."""
    legacy_root = Path(cfg["legacy_root"])
    closure_files = inventory.closure_files(legacy_root, cfg["legacy_package"], unit)
    live_digests = cm_common.file_digests(legacy_root, closure_files)
    legacy_closure_sha256 = cm_common.sha256_json(live_digests)

    cases_path = root / "cases" / f"{unit}.json"
    shutil.copyfile(FIXTURES_DIR / "cases" / f"{unit}.json", cases_path)
    kept = len(json.loads(cases_path.read_text(encoding="utf-8"))["cases"])

    nets_path = root / "nets" / f"{unit}.json"
    cm_common.atomic_write_json(
        nets_path, {"schema": 1, "unit": unit, "legacy_closure": live_digests, "observations": []}
    )

    net_lock_path = root / "net.lock.json"
    net_lock = (
        json.loads(net_lock_path.read_text(encoding="utf-8"))
        if net_lock_path.is_file()
        else {"schema": 1, "units": {}}
    )
    net_lock["units"][unit] = {
        "net_sha256": cm_common.sha256_file(nets_path),
        "cases_sha256": cm_common.sha256_file(cases_path),
        "legacy_closure_sha256": legacy_closure_sha256,
        "coverage_pct": 100,
        "kept": kept,
        "dropped": {},
        "skipped_dropped_symbol": [],
        "deterministic": True,
        "stateful": False,
    }
    cm_common.atomic_write_json(net_lock_path, net_lock)


def _record_probe(root: Path, monkeypatch, result: str = "denied") -> None:
    monkeypatch.setenv("CM_CODEX_BIN", str(FAKE_CODEX))
    version = sandbox._codex_version(str(FAKE_CODEX))
    cm_common.atomic_write_json(
        root / "runs" / "sandbox_probe.json",
        {"schema": 1, "codex_version": version, "result": result, "at": "2026-01-01T00:00:00+00:00", "attempts": {}},
    )


def _use_fake(monkeypatch, scenario: str, **extra) -> None:
    monkeypatch.setenv("CM_CODEX_BIN", str(FAKE_CODEX))
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", scenario)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


# --- probe ---------------------------------------------------------------------


def test_probe_denied_from_ground_truth_files(shop_root, monkeypatch):
    root, cfg = shop_root
    _use_fake(monkeypatch, "probe_denied")

    code = sandbox.cmd_probe(root, cfg)

    assert code == cm_common.EXIT_OK
    record = json.loads((root / "runs" / "sandbox_probe.json").read_text(encoding="utf-8"))
    assert record["result"] == "denied"
    assert record["codex_version"] == "fake-codex 0.0.0-test"
    # the canaries were removed afterward regardless of verdict
    assert not (root / "runs" / ".cm_canary").exists()
    assert not (Path(cfg["legacy_root"]) / ".cm_canary").exists()
    assert not (Path(cfg["target_root"]) / ".cm_canary").exists()


def test_probe_not_exercised_when_the_script_never_ran(shop_root, monkeypatch):
    root, cfg = shop_root
    _use_fake(monkeypatch, "probe_not_exercised")

    code = sandbox.cmd_probe(root, cfg)

    assert code == cm_common.EXIT_FAIL
    record = json.loads((root / "runs" / "sandbox_probe.json").read_text(encoding="utf-8"))
    assert record["result"] == "NOT_EXERCISED"


def test_probe_not_denied_when_a_canary_is_overwritten(shop_root, monkeypatch):
    root, cfg = shop_root
    _use_fake(monkeypatch, "probe_not_denied")

    code = sandbox.cmd_probe(root, cfg)

    assert code == cm_common.EXIT_FAIL
    record = json.loads((root / "runs" / "sandbox_probe.json").read_text(encoding="utf-8"))
    assert record["result"] == "NOT_DENIED"


def test_probe_argv_includes_ephemeral(shop_root, monkeypatch, tmp_path):
    root, cfg = shop_root
    log_path = tmp_path / "argv.json"
    _use_fake(monkeypatch, "probe_denied", FAKE_CODEX_ARGV_LOG=str(log_path))

    sandbox.cmd_probe(root, cfg)

    argv = json.loads(log_path.read_text(encoding="utf-8"))
    assert argv[0] == "exec"
    assert "--ephemeral" in argv
    assert argv[-1] == "-"


# --- dispatch preconditions ------------------------------------------------------


def test_dispatch_refuses_without_a_probe_record(shop_root, monkeypatch):
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _use_fake(monkeypatch, "port_ok")

    with pytest.raises(SystemExit) as exc:
        sandbox.cmd_dispatch(root, cfg, "shop.pricing", "port", 1)
    assert exc.value.code == cm_common.EXIT_FAIL


def test_dispatch_refuses_with_a_probe_from_another_codex_version(shop_root, monkeypatch):
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    monkeypatch.setenv("CM_CODEX_BIN", str(FAKE_CODEX))
    cm_common.atomic_write_json(
        root / "runs" / "sandbox_probe.json",
        {"schema": 1, "codex_version": "some-other-codex-1.0", "result": "denied", "at": "x", "attempts": {}},
    )
    _use_fake(monkeypatch, "port_ok")

    with pytest.raises(SystemExit) as exc:
        sandbox.cmd_dispatch(root, cfg, "shop.pricing", "port", 1)
    assert exc.value.code == cm_common.EXIT_FAIL


@pytest.mark.parametrize("bad_result", ["NOT_DENIED", "NOT_EXERCISED"])
def test_dispatch_refuses_with_a_non_denied_probe(shop_root, monkeypatch, bad_result):
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch, result=bad_result)
    _use_fake(monkeypatch, "port_ok")

    with pytest.raises(SystemExit) as exc:
        sandbox.cmd_dispatch(root, cfg, "shop.pricing", "port", 1)
    assert exc.value.code == cm_common.EXIT_FAIL


def test_dispatch_refuses_when_the_stage_lands_inside_a_git_worktree(shop_root, monkeypatch):
    """`codex exec` resolves its workspace root upward to the nearest git
    toplevel, not just to `-C <stage>` -- the exact failure literary-translator
    hit. Forces the stage to land inside this checkout's own git worktree
    instead of the system temp dir, to exercise the same refusal `probe`
    already had."""
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    _use_fake(monkeypatch, "port_ok")

    fake_stage = TESTS_DIR / ".work" / "fake-stage-inside-git"
    fake_stage.mkdir(parents=True, exist_ok=True)
    try:
        monkeypatch.setattr(sandbox.tempfile, "mkdtemp", lambda **kwargs: str(fake_stage))

        with pytest.raises(SystemExit) as exc:
            sandbox.cmd_dispatch(root, cfg, "shop.pricing", "port", 1)
        assert exc.value.code == cm_common.EXIT_CANNOT
    finally:
        shutil.rmtree(fake_stage, ignore_errors=True)


# --- dispatch: port / fix promotion ------------------------------------------------


def test_port_dispatch_promotes_target_only_and_lists_ignored_outputs(shop_root, monkeypatch, capsys):
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    port_text = (FIXTURES_DIR / "ports" / "good" / "shop2" / "pricing.py").read_text(encoding="utf-8")
    _use_fake(monkeypatch, "port_extra_ignored", FAKE_CODEX_TARGET_PY=port_text)

    code = sandbox.cmd_dispatch(root, cfg, "shop.pricing", "port", 1)

    assert code == cm_common.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["promoted"] == ["out/target.py"]
    assert out["ignored_outputs"] == ["out/notes.txt"]
    assert out["tampered"] == []

    target_path = cm_common.target_file(root, cfg, "shop.pricing")
    assert target_path.read_text(encoding="utf-8") == port_text


def test_dispatch_detects_a_write_into_the_durable_root_and_promotes_nothing(shop_root, monkeypatch, capsys):
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    tamper_target = Path(cfg["legacy_root"]) / "shop" / "money.py"
    original = tamper_target.read_text(encoding="utf-8")
    _use_fake(monkeypatch, "tamper_outside", FAKE_CODEX_TAMPER_PATH=str(tamper_target))

    try:
        with pytest.raises(SystemExit) as exc:
            sandbox.cmd_dispatch(root, cfg, "shop.pricing", "port", 1)
        assert exc.value.code == cm_common.EXIT_FAIL

        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False
        assert "legacy/shop/money.py" in out["tampered"]

        target_path = cm_common.target_file(root, cfg, "shop.pricing")
        assert not target_path.exists()
    finally:
        tamper_target.write_text(original, encoding="utf-8")


def test_dispatch_still_reports_tamper_and_journals_when_codex_times_out(shop_root, monkeypatch):
    """A codex turn that writes into the legacy tree and then hangs must
    still be caught: the after-run tamper check and the journal entry must
    not be skipped just because the subprocess call itself raised
    TimeoutExpired."""
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    tamper_target = Path(cfg["legacy_root"]) / "shop" / "money.py"
    original = tamper_target.read_text(encoding="utf-8")
    monkeypatch.setenv("CM_DISPATCH_TIMEOUT_S", "1")
    _use_fake(
        monkeypatch, "tamper_then_hang",
        FAKE_CODEX_TAMPER_PATH=str(tamper_target), FAKE_CODEX_HANG_SECONDS="5",
    )

    try:
        with pytest.raises(SystemExit) as exc:
            sandbox.cmd_dispatch(root, cfg, "shop.pricing", "port", 1)
        assert exc.value.code == cm_common.EXIT_FAIL  # tamper wins over "codex unavailable"

        run_dir = cm_common.unit_run_dir(root, "shop.pricing")
        journal_lines = (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()
        assert journal_lines  # the timeout no longer skips the journal entry
        last_entry = json.loads(journal_lines[-1])
        assert any("legacy/shop/money.py" in t for t in last_entry["tampered"])

        target_path = cm_common.target_file(root, cfg, "shop.pricing")
        assert not target_path.exists()
    finally:
        tamper_target.write_text(original, encoding="utf-8")


def test_dispatch_tamper_wins_even_when_the_codex_call_is_interrupted(shop_root, monkeypatch):
    """`try`/`finally` (not an except-list) means the tamper check and its
    journal entry run for ANY exit from the codex call, including a bare
    `KeyboardInterrupt` that the narrow except-list never matches -- and
    since a `cm_common.fail` call inside `finally` supersedes whatever was
    propagating, the interrupt itself never escapes: tampering is reported
    as the ordinary "dispatch tampered" refusal."""
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    tamper_target = Path(cfg["legacy_root"]) / "shop" / "money.py"
    original = tamper_target.read_text(encoding="utf-8")

    real_run = subprocess.run

    def _fake_run(argv, **kwargs):
        # Only intercept the actual codex-exec call; let _codex_version's
        # "--version" probe and _assert_stage_outside_git's "git" call
        # through untouched, or the preconditions above would trip first.
        if isinstance(argv, list) and len(argv) > 1 and argv[1] == "exec":
            tamper_target.write_text("tampered", encoding="utf-8")
            raise KeyboardInterrupt
        return real_run(argv, **kwargs)

    _use_fake(monkeypatch, "port_ok")
    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run)

    try:
        with pytest.raises(SystemExit) as exc:
            sandbox.cmd_dispatch(root, cfg, "shop.pricing", "port", 1)
        assert exc.value.code == cm_common.EXIT_FAIL

        run_dir = cm_common.unit_run_dir(root, "shop.pricing")
        journal_lines = (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()
        assert journal_lines
        last_entry = json.loads(journal_lines[-1])
        assert any("legacy/shop/money.py" in t for t in last_entry["tampered"])

        target_path = cm_common.target_file(root, cfg, "shop.pricing")
        assert not target_path.exists()
    finally:
        tamper_target.write_text(original, encoding="utf-8")


def test_dispatch_rejects_a_nonzero_codex_exit_even_with_a_wellformed_review(shop_root, monkeypatch):
    """A failed process that still left a syntactically clean
    `{"findings": []}` on disk must not become a promoted, clean review."""
    root, cfg = shop_root
    _install_good_port(cfg)
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    _use_fake(monkeypatch, "review_then_fail")

    with pytest.raises(SystemExit) as exc:
        sandbox.cmd_dispatch(root, cfg, "shop.pricing", "review", 1)
    assert exc.value.code == cm_common.EXIT_FAIL

    run_dir = cm_common.unit_run_dir(root, "shop.pricing")
    assert not (run_dir / "review.r1.json").exists()

    journal_lines = (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert journal_lines
    last_entry = json.loads(journal_lines[-1])
    assert last_entry["exit"] == 1


def test_port_dispatch_refuses_a_symlinked_output(shop_root, monkeypatch):
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    _use_fake(monkeypatch, "port_symlink")

    with pytest.raises(SystemExit) as exc:
        sandbox.cmd_dispatch(root, cfg, "shop.pricing", "port", 1)
    assert exc.value.code == cm_common.EXIT_FAIL

    target_path = cm_common.target_file(root, cfg, "shop.pricing")
    assert not target_path.exists()


def test_port_dispatch_refuses_when_the_target_package_symlinks_into_legacy(shop_root, monkeypatch):
    """A pre-existing symlink `target_root/shop2 -> legacy_root/shop` passes
    the before/after tamper check untouched (nothing under the unresolved
    target_root path changed while codex ran) and then would redirect the
    promotion's own atomic write into the legacy tree -- caught only by the
    destination-containment check right before that write."""
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)

    target_root = Path(cfg["target_root"])
    legacy_root = Path(cfg["legacy_root"])
    target_root.mkdir(parents=True, exist_ok=True)
    os.symlink(legacy_root / "shop", target_root / "shop2")

    legacy_pricing_before = (legacy_root / "shop" / "pricing.py").read_text(encoding="utf-8")
    port_text = (FIXTURES_DIR / "ports" / "good" / "shop2" / "pricing.py").read_text(encoding="utf-8")
    _use_fake(monkeypatch, "port_ok", FAKE_CODEX_TARGET_PY=port_text)

    with pytest.raises(SystemExit) as exc:
        sandbox.cmd_dispatch(root, cfg, "shop.pricing", "port", 1)
    assert exc.value.code == cm_common.EXIT_FAIL

    # Nothing must have been written through the symlink into the real
    # legacy file.
    assert (legacy_root / "shop" / "pricing.py").read_text(encoding="utf-8") == legacy_pricing_before


def test_fix_dispatch_refuses_with_an_unadjudicated_finding(shop_root, monkeypatch):
    root, cfg = shop_root
    _install_good_port(cfg)
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)

    run_dir = cm_common.unit_run_dir(root, "shop.pricing")
    finding = {
        "rule": "idiom", "severity": "minor", "location": "shop2.pricing:describe",
        "issue": "uses print directly", "suggestion": "use the target logging convention",
    }
    finding["digest"] = cm_common.sha256_json(finding)
    target_sha256 = cm_common.sha256_file(cm_common.target_file(root, cfg, "shop.pricing"))
    review_doc = {"schema": 1, "round": 1, "target_sha256": target_sha256, "findings": [finding], "malformed": []}
    cm_common.atomic_write_json(run_dir / "review.r1.json", review_doc)
    # deliberately neither admitted nor refused

    _use_fake(monkeypatch, "fix_ok")
    with pytest.raises(SystemExit) as exc:
        sandbox.cmd_dispatch(root, cfg, "shop.pricing", "fix", 1)
    assert exc.value.code == cm_common.EXIT_FAIL


# --- dispatch: review promotion ---------------------------------------------------


def test_review_dispatch_keeps_valid_findings_and_records_malformed_verbatim(shop_root, monkeypatch, capsys):
    root, cfg = shop_root
    _install_good_port(cfg)
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    payload = json.dumps(
        {
            "findings": [
                {
                    "rule": "idiom", "severity": "minor", "location": "shop2.pricing:apply_discount",
                    "issue": "docstring missing", "suggestion": "add one",
                },
                {
                    "rule": "error-path", "severity": "major", "location": "shop2.pricing:dedupe",
                    "issue": "mutates its argument silently", "suggestion": "document it",
                },
                # missing "issue" and "suggestion" -- malformed
                {"rule": "resource", "severity": "minor", "location": "shop2.pricing:describe"},
            ]
        }
    )
    _use_fake(monkeypatch, "review_json", FAKE_CODEX_REVIEW_JSON=payload)

    code = sandbox.cmd_dispatch(root, cfg, "shop.pricing", "review", 1)

    assert code == cm_common.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["malformed_findings"] == [
        {"rule": "resource", "severity": "minor", "location": "shop2.pricing:describe"}
    ]

    review_doc = json.loads((root / "runs" / "shop.pricing" / "review.r1.json").read_text(encoding="utf-8"))
    assert len(review_doc["findings"]) == 2
    assert all("digest" in f for f in review_doc["findings"])
    assert review_doc["malformed"] == [
        {"rule": "resource", "severity": "minor", "location": "shop2.pricing:describe"}
    ]


def test_review_prose_around_json_is_recorded_as_malformed(shop_root, monkeypatch, capsys):
    root, cfg = shop_root
    _install_good_port(cfg)
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    payload = json.dumps({"findings": []})
    _use_fake(monkeypatch, "review_prose", FAKE_CODEX_REVIEW_JSON=payload)

    code = sandbox.cmd_dispatch(root, cfg, "shop.pricing", "review", 1)

    assert code == cm_common.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["malformed_findings"] == ["<message not a single JSON object>"]

    review_doc = json.loads((root / "runs" / "shop.pricing" / "review.r1.json").read_text(encoding="utf-8"))
    assert review_doc["findings"] == []
    assert review_doc["malformed"] == ["<message not a single JSON object>"]


def test_review_empty_findings_is_a_valid_answer(shop_root, monkeypatch, capsys):
    root, cfg = shop_root
    _install_good_port(cfg)
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    _use_fake(monkeypatch, "review_empty")

    code = sandbox.cmd_dispatch(root, cfg, "shop.pricing", "review", 1)

    assert code == cm_common.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["malformed_findings"] == []
    review_doc = json.loads((root / "runs" / "shop.pricing" / "review.r1.json").read_text(encoding="utf-8"))
    assert review_doc["findings"] == []
    assert review_doc["malformed"] == []


# --- dispatch: cases promotion ----------------------------------------------------


def test_cases_dispatch_merges_new_ids_only(shop_root, monkeypatch, capsys):
    root, cfg = shop_root
    _record_probe(root, monkeypatch)
    existing = {"schema": 1, "cases": [{"id": "p1", "call": "apply_discount", "args": [100, 10]}]}
    cm_common.atomic_write_json(root / "cases" / "shop.pricing.json", existing)

    payload = json.dumps(
        {
            "cases": [
                {"id": "p1", "call": "apply_discount", "args": [999, 999]},  # existing id: never replaced
                {"id": "p9", "call": "apply_discount", "args": [50, 5]},  # new
                {"id": "", "call": "apply_discount", "args": [1, 1]},  # invalid: empty id
            ]
        }
    )
    _use_fake(monkeypatch, "cases_json", FAKE_CODEX_CASES_JSON=payload)

    code = sandbox.cmd_dispatch(root, cfg, "shop.pricing", "cases", 1)

    assert code == cm_common.EXIT_OK
    merged = json.loads((root / "cases" / "shop.pricing.json").read_text(encoding="utf-8"))
    assert merged["schema"] == 1
    ids = [c["id"] for c in merged["cases"]]
    assert ids == ["p1", "p9"]
    assert merged["cases"][0]["args"] == [100, 10]

    out = json.loads(capsys.readouterr().out)
    assert any("invalid case" in item for item in out["ignored_outputs"])


def test_cases_dispatch_refuses_a_duplicate_id_within_the_proposed_batch(shop_root, monkeypatch):
    root, cfg = shop_root
    _record_probe(root, monkeypatch)
    existing = {"schema": 1, "cases": [{"id": "p1", "call": "apply_discount", "args": [100, 10]}]}
    cm_common.atomic_write_json(root / "cases" / "shop.pricing.json", existing)

    # Two DIFFERENT proposed cases sharing one new id -- must refuse the
    # whole promotion rather than silently letting the second overwrite the
    # first once appended, or non-deterministically keeping only one.
    payload = json.dumps(
        {
            "cases": [
                {"id": "p9", "call": "apply_discount", "args": [1, 1]},
                {"id": "p9", "call": "apply_discount", "args": [2, 2]},
                {"id": "p10", "call": "apply_discount", "args": [3, 3]},
            ]
        }
    )
    _use_fake(monkeypatch, "cases_json", FAKE_CODEX_CASES_JSON=payload)

    with pytest.raises(SystemExit) as exc:
        sandbox.cmd_dispatch(root, cfg, "shop.pricing", "cases", 1)
    assert exc.value.code == cm_common.EXIT_FAIL

    # Nothing promoted at all -- the existing file is untouched.
    merged = json.loads((root / "cases" / "shop.pricing.json").read_text(encoding="utf-8"))
    assert merged == existing


# --- argv shape and rendered prompts -----------------------------------------------


def test_dispatch_argv_matches_the_pinned_shape(shop_root, monkeypatch, tmp_path):
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    log_path = tmp_path / "argv.json"
    _use_fake(monkeypatch, "port_ok", FAKE_CODEX_ARGV_LOG=str(log_path))

    sandbox.cmd_dispatch(root, cfg, "shop.pricing", "port", 1)

    argv = json.loads(log_path.read_text(encoding="utf-8"))
    assert argv[0] == "exec"
    assert argv[1] == "-s"
    assert argv[2] == "workspace-write"
    assert argv[3] == "-C"
    assert argv[5] == "--skip-git-repo-check"
    assert argv[-3] == "-o"
    assert argv[-1] == "-"
    assert "--ephemeral" not in argv


def test_review_dispatch_uses_read_only_mode(shop_root, monkeypatch, tmp_path):
    root, cfg = shop_root
    _install_good_port(cfg)
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    log_path = tmp_path / "argv.json"
    _use_fake(monkeypatch, "review_empty", FAKE_CODEX_ARGV_LOG=str(log_path))

    sandbox.cmd_dispatch(root, cfg, "shop.pricing", "review", 1)

    argv = json.loads(log_path.read_text(encoding="utf-8"))
    assert argv[2] == "read-only"


def test_render_template_names_the_single_write_target_and_marks_inputs_read_only():
    templates_dir = cm_common.plugin_root() / "skills" / "codebase-migrator" / "assets" / "templates"

    port_prompt = sandbox.render_template(templates_dir / "port_TASK.md", "shop.pricing", "shop2.pricing", 1)
    assert "out/target.py" in port_prompt
    assert "shop2.pricing" in port_prompt
    assert "non-authoritative" in port_prompt.lower()
    assert "{{" not in port_prompt

    fix_prompt = sandbox.render_template(templates_dir / "fix_TASK.md", "shop.pricing", "shop2.pricing", 2)
    assert "out/target.py" in fix_prompt
    assert "round 2" in fix_prompt.lower()
    assert "{{" not in fix_prompt

    review_prompt = sandbox.render_template(templates_dir / "review_TASK.md", "shop.pricing", "shop2.pricing", 3)
    assert "your one write target" in review_prompt.lower()
    assert "none" in review_prompt.lower()
    assert "round 3" in review_prompt.lower()
    assert "{{" not in review_prompt

    cases_prompt = sandbox.render_template(templates_dir / "cases_TASK.md", "shop.pricing", "shop2.pricing", 1)
    assert "out/cases.json" in cases_prompt
    assert "{{" not in cases_prompt


def test_render_template_raises_on_an_unreplaced_placeholder(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("Hello {{UNIT}} and {{SOMETHING_ELSE}}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        sandbox.render_template(bad, "shop.pricing", "shop2.pricing", 1)


def test_dispatch_prompt_received_by_codex_names_the_write_target_and_injection_guard(
    shop_root, monkeypatch, tmp_path
):
    """Not the template source, and not `render_template`'s return value in
    isolation -- the actual bytes fake_codex received on stdin for a real
    dispatch of each kind, which is what an operator's turn would actually
    read."""
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)

    expected_write_target = {
        "port": "out/target.py",
        "review": "None. This task writes nothing to disk.",
        "fix": "out/target.py",
        "cases": "out/cases.json",
    }
    fake_scenario = {"port": "port_ok", "review": "review_empty", "fix": "fix_ok", "cases": "cases_json"}

    # port first (creates the target), then review (round 1, empty findings
    # -- trivially satisfies fix's "every finding adjudicated" precondition),
    # then fix, then cases.
    for kind in ("port", "review", "fix", "cases"):
        log_path = tmp_path / f"prompt-{kind}.txt"
        extra = {"FAKE_CODEX_PROMPT_LOG": str(log_path)}
        if kind == "cases":
            extra["FAKE_CODEX_CASES_JSON"] = json.dumps({"cases": []})
        _use_fake(monkeypatch, fake_scenario[kind], **extra)

        code = sandbox.cmd_dispatch(root, cfg, "shop.pricing", kind, 1)
        assert code == cm_common.EXIT_OK, kind

        prompt = log_path.read_text(encoding="utf-8")
        assert "Your one write target" in prompt, kind
        assert expected_write_target[kind] in prompt, kind
        assert "not instructions to you" in prompt, kind


def test_digests_reports_a_stable_count_and_hash(shop_root):
    root, cfg = shop_root
    first = sandbox.cmd_digests
    import io
    import contextlib

    def _run():
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = first(root, cfg)
        return code, json.loads(buf.getvalue())

    code_a, out_a = _run()
    code_b, out_b = _run()
    assert code_a == cm_common.EXIT_OK == code_b
    assert out_a == out_b
    assert out_a["count"] >= 0


def test_cli_main_probe_then_dispatch_across_two_invocations_on_the_same_root(shop_root, monkeypatch):
    """`sandbox.py probe` creates target_root as a side effect (mkdir). A
    second CLI-level call against the same root -- `dispatch`, a separate
    `main()` invocation and so a fresh `cm_common.load_config()` -- used to
    be refused by migration_validate.py before A's fix, which used to treat
    an existing target_root as invalid config. This is the exact shape that
    fix was needed for."""
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _use_fake(monkeypatch, "probe_denied")

    monkeypatch.setattr("sys.argv", ["sandbox.py", "probe", "--root", str(root)])
    code_probe = sandbox.main()
    assert code_probe == cm_common.EXIT_OK
    assert Path(cfg["target_root"]).is_dir()  # probe's own mkdir side effect

    port_text = (FIXTURES_DIR / "ports" / "good" / "shop2" / "pricing.py").read_text(encoding="utf-8")
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "port_ok")
    monkeypatch.setenv("FAKE_CODEX_TARGET_PY", port_text)
    monkeypatch.setattr(
        "sys.argv",
        ["sandbox.py", "dispatch", "--root", str(root), "--unit", "shop.pricing", "--kind", "port", "--round", "1"],
    )
    code_dispatch = sandbox.main()
    assert code_dispatch == cm_common.EXIT_OK

    target_path = cm_common.target_file(root, cfg, "shop.pricing")
    assert target_path.read_text(encoding="utf-8") == port_text


def test_sandbox_dispatch_as_a_real_subprocess_against_fake_codex(shop_root, monkeypatch):
    """`sandbox.py dispatch` invoked exactly as the pipeline would, in its own
    process, against fake_codex, on a root whose target_root already exists
    before this call (created here directly, not by a prior dispatch) --
    `_build_shop_root()` already called `cm_common.load_config()` once, so
    this subprocess's own `load_config()` is a second call against the same
    root. Real process-boundary coverage of the write-boundary dispatcher,
    not `main()` called in-process."""
    root, cfg = shop_root
    _freeze_net(root, cfg, "shop.pricing")
    _record_probe(root, monkeypatch)
    Path(cfg["target_root"]).mkdir(parents=True, exist_ok=True)
    port_text = (FIXTURES_DIR / "ports" / "good" / "shop2" / "pricing.py").read_text(encoding="utf-8")

    env = dict(os.environ)
    env["CM_CODEX_BIN"] = str(FAKE_CODEX)
    env["FAKE_CODEX_SCENARIO"] = "port_ok"
    env["FAKE_CODEX_TARGET_PY"] = port_text

    proc = subprocess.run(
        [
            sys.executable, str(SANDBOX_SCRIPT), "dispatch",
            "--root", str(root), "--unit", "shop.pricing", "--kind", "port", "--round", "1",
        ],
        capture_output=True, text=True, timeout=60, env=env,
    )

    assert proc.returncode == cm_common.EXIT_OK, proc.stderr
    stdout_lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(stdout_lines) == 1, proc.stdout
    payload = json.loads(stdout_lines[0])
    assert payload["ok"] is True
    assert payload["promoted"] == ["out/target.py"]
    assert payload["tampered"] == []

    target_path = cm_common.target_file(root, cfg, "shop.pricing")
    assert target_path.read_text(encoding="utf-8") == port_text


def test_dispatch_cli_unknown_flag_emits_one_json_line_and_exits_2(shop_root):
    """`cm_common.make_parser` (A): an argparse error still follows the
    plugin's one-JSON-line-on-stdout contract instead of argparse's own bare
    usage-text-and-exit-2 default."""
    root, _cfg = shop_root

    proc = subprocess.run(
        [
            sys.executable, str(SANDBOX_SCRIPT), "dispatch",
            "--root", str(root), "--unit", "shop.pricing", "--kind", "port", "--bogus-flag",
        ],
        capture_output=True, text=True, timeout=30,
    )

    assert proc.returncode == cm_common.EXIT_CANNOT
    stdout_lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(stdout_lines) == 1, proc.stdout
    payload = json.loads(stdout_lines[0])
    assert payload["ok"] is False
    assert "error" in payload
