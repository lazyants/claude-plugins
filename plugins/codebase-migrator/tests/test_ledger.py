"""Tests for ledger.py (plan section 7, owner A).

Fixtures are hand-built directly against the pinned JSON schemas (inventory.json,
registry.lock.json, net.lock.json) rather than produced by B's/C's real scripts:
this file tests ledger.py in isolation. The one place the real scripts are wired
together end to end is test_e2e.py.
"""

import json
import types

import pytest

import cm_common
import ledger


def _setup_root(work_root, coverage_floor=80):
    root = work_root
    legacy_root = root / "legacy"
    (legacy_root / "shop").mkdir(parents=True)
    (legacy_root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    money_path = legacy_root / "shop" / "money.py"
    money_path.write_text("def round_money(x):\n    return round(x, 2)\n", encoding="utf-8")
    pricing_path = legacy_root / "shop" / "pricing.py"
    pricing_path.write_text(
        "from shop.money import round_money\n\n\n"
        "def apply_discount(price, pct):\n"
        "    return round_money(price * (100 - pct) / 100)\n",
        encoding="utf-8",
    )

    cfg = {
        "schema": 1,
        "source_stack": "python",
        "target_stack": "python",
        "legacy_root": str(legacy_root),
        "legacy_package": "shop",
        "target_root": str(root / "target"),
        "target_package": "shop2",
        "fidelity_policy": "bug_for_bug",
        "seam": "in_process",
        "unit_granularity": "file",
        "naming_policy": "preserve",
        "net_source": "generated_golden_master",
        "dead_code_policy": "port",
        "coverage_floor_pct": coverage_floor,
        "max_fix_rounds": 3,
        "codex_bin": "codex",
    }
    cm_common.atomic_write_json(root / "migration.json", cfg)

    inventory = {
        "schema": 1,
        "legacy_package": "shop",
        "units": {
            "shop.money": {
                "file": "shop/money.py",
                "source_sha256": cm_common.sha256_file(money_path),
                "public_symbols": ["shop.money:round_money"],
                "symbol_spans": {"shop.money:round_money": [1, 2]},
                "imports_units": [],
                "imported_symbols": [],
                "flags": {},
                "eligible": True,
                "ineligible_reasons": [],
                "executable_lines": [2],
            },
            "shop.pricing": {
                "file": "shop/pricing.py",
                "source_sha256": cm_common.sha256_file(pricing_path),
                "public_symbols": ["shop.pricing:apply_discount"],
                "symbol_spans": {"shop.pricing:apply_discount": [4, 5]},
                "imports_units": ["shop.money"],
                "imported_symbols": ["shop.money:round_money"],
                "flags": {},
                "eligible": True,
                "ineligible_reasons": [],
                "executable_lines": [5],
            },
        },
        "edges": [["shop.pricing", "shop.money"]],
        "unreferenced_public": [],
    }
    cm_common.atomic_write_json(root / "inventory.json", inventory)

    registry_lock = {
        "schema": 1,
        "rows": {
            "shop.money:round_money": {
                "row": {
                    "source": "shop.money:round_money",
                    "cardinality": "one_to_one",
                    "entry": "shop2.money:round_money",
                    "targets": ["shop2.money:round_money"],
                    "reason": None,
                },
                "digest": "x",
            },
            "shop.pricing:apply_discount": {
                "row": {
                    "source": "shop.pricing:apply_discount",
                    "cardinality": "one_to_one",
                    "entry": "shop2.pricing:apply_discount",
                    "targets": ["shop2.pricing:apply_discount"],
                    "reason": None,
                },
                "digest": "y",
            },
        },
    }
    cm_common.atomic_write_json(root / "registry.lock.json", registry_lock)
    cm_common.atomic_write_json(root / "ledger.json", {"schema": 1, "units": {}})
    cm_common.atomic_write_text(root / "conventions.md", "# Conventions\n\nUse snake_case.\n")

    for d in ("cases", "nets", "runs"):
        (root / d).mkdir(exist_ok=True)

    return root, cfg


def _write_cases(root, unit, cases_list):
    cm_common.atomic_write_json(root / "cases" / f"{unit}.json", {"schema": 1, "cases": cases_list})


def _capture_net(root, cfg, unit, closure_units, coverage_pct=100, deterministic=True, stateful=False):
    legacy_root = cm_common.resolved_paths(root, cfg)["legacy_root"]
    legacy_digests = cm_common.closure_digests(legacy_root, cfg["legacy_package"], list(closure_units))
    legacy_closure_sha256 = cm_common.sha256_json(legacy_digests)

    nets_path = root / "nets" / f"{unit}.json"
    cm_common.atomic_write_json(
        nets_path,
        {
            "schema": 1,
            "unit": unit,
            "legacy_closure": legacy_digests,
            "legacy_closure_sha256": legacy_closure_sha256,
            "observations": [],
        },
    )

    cases_path = root / "cases" / f"{unit}.json"
    net_lock_path = root / "net.lock.json"
    net_lock = (
        cm_common.read_json(net_lock_path, "net.lock.json")
        if net_lock_path.is_file()
        else {"schema": 1, "units": {}}
    )
    net_lock["units"][unit] = {
        "net_sha256": cm_common.sha256_file(nets_path),
        "cases_sha256": cm_common.sha256_file(cases_path),
        "legacy_closure_sha256": legacy_closure_sha256,
        "coverage_pct": coverage_pct,
        "kept": 1,
        "dropped": {},
        "skipped_dropped_symbol": [],
        "deterministic": deterministic,
        "stateful": stateful,
    }
    cm_common.atomic_write_json(net_lock_path, net_lock)


def _make_fully_eligible(work_root):
    root, cfg = _setup_root(work_root)
    _write_cases(root, "shop.pricing", [{"id": "c1", "call": "apply_discount", "args": [100, 10], "kwargs": {}}])
    _capture_net(root, cfg, "shop.pricing", ["shop.pricing", "shop.money"])
    return root, cfg


def _flip_net_lock_field(root, unit, field, value):
    path = root / "net.lock.json"
    lock = json.loads(path.read_text(encoding="utf-8"))
    lock["units"][unit][field] = value
    cm_common.atomic_write_json(path, lock)


def _write_target(root, cfg, unit, content=None):
    content = content or (
        # a real import of the dependency's target module, so
        # inventory.import_closure (and cache_key's target_closure_sha256)
        # actually discovers shop2.money -- matching the real `good` port
        # fixture's own shape (plan section 5.4).
        "from shop2.money import round_money\n\n\n"
        "def apply_discount(price, pct):\n    return round_money(price * (100 - pct) / 100)\n"
    )
    target_path = cm_common.target_file(root, cfg, unit)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    return target_path


def _write_r2_r3(root, cfg, unit):
    target_path = cm_common.target_file(root, cfg, unit)
    target_sha256 = cm_common.sha256_file(target_path)
    key_sha256 = cm_common.sha256_json(ledger.cache_key(root, cfg, unit))
    run_dir = cm_common.unit_run_dir(root, unit)
    for name in ("r2", "r3"):
        cm_common.atomic_write_json(
            run_dir / f"{name}.json",
            {"ok": True, "unit": unit, "target_sha256": target_sha256, "key_sha256": key_sha256},
        )
    return target_sha256, key_sha256


def _write_review(root, unit, round_n=1, findings=None, malformed=None, target_sha256=None):
    run_dir = cm_common.unit_run_dir(root, unit)
    cm_common.atomic_write_json(
        run_dir / f"review.r{round_n}.json",
        {
            "schema": 1,
            "round": round_n,
            "target_sha256": target_sha256,
            "findings": findings or [],
            "malformed": malformed or [],
        },
    )


def _finding(rule="rule", severity="minor"):
    base = {"rule": rule, "severity": severity, "location": "l", "issue": "i", "suggestion": "s"}
    digest = cm_common.sha256_json(base)
    return dict(base, digest=digest), digest


# ---------------------------------------------------------------------------
# eligible() -- each reason fires alone
# ---------------------------------------------------------------------------


def test_eligible_true_on_fully_set_up_unit(work_root):
    root, cfg = _make_fully_eligible(work_root)
    assert ledger.eligible(root, cfg, "shop.pricing") == []


def test_eligible_reason_not_statically_eligible(work_root):
    root, cfg = _make_fully_eligible(work_root)
    inv_path = root / "inventory.json"
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    inv["units"]["shop.pricing"]["eligible"] = False
    inv["units"]["shop.pricing"]["ineligible_reasons"] = ["io"]
    cm_common.atomic_write_json(inv_path, inv)
    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert len(reasons) == 1
    assert "not statically eligible" in reasons[0]


def test_eligible_reason_inventory_stale(work_root):
    root, cfg = _make_fully_eligible(work_root)
    legacy_root = cm_common.resolved_paths(root, cfg)["legacy_root"]
    pricing_path = legacy_root / "shop" / "pricing.py"
    pricing_path.write_text(pricing_path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert len(reasons) == 1
    assert "inventory is stale" in reasons[0]
    assert "shop.pricing" in reasons[0]


def test_eligible_reason_missing_frozen_rows(work_root):
    root, cfg = _make_fully_eligible(work_root)
    lock_path = root / "registry.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    del lock["rows"]["shop.money:round_money"]
    cm_common.atomic_write_json(lock_path, lock)
    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert len(reasons) == 1
    assert "missing frozen row" in reasons[0]
    assert "shop.money:round_money" in reasons[0]


def test_eligible_reason_no_net_recorded(work_root):
    root, cfg = _setup_root(work_root)
    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert len(reasons) == 1
    assert "no net recorded" in reasons[0]


def test_eligible_reason_net_not_deterministic(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _flip_net_lock_field(root, "shop.pricing", "deterministic", False)
    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert len(reasons) == 1
    assert "not deterministic" in reasons[0]


def test_eligible_reason_net_stateful(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _flip_net_lock_field(root, "shop.pricing", "stateful", True)
    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert len(reasons) == 1
    assert "stateful" in reasons[0]


def test_eligible_reason_coverage_below_floor(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _flip_net_lock_field(root, "shop.pricing", "coverage_pct", 10)
    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert len(reasons) == 1
    assert "below the floor" in reasons[0]


def test_eligible_reason_nets_digest_mismatch(work_root):
    root, cfg = _make_fully_eligible(work_root)
    nets_path = root / "nets" / "shop.pricing.json"
    net = json.loads(nets_path.read_text(encoding="utf-8"))
    net["observations"].append({"tampered": True})
    nets_path.write_text(json.dumps(net), encoding="utf-8")
    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert len(reasons) == 1
    assert "does not match net.lock.json" in reasons[0]


def test_eligible_reason_cases_changed_since_capture(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_cases(
        root,
        "shop.pricing",
        [
            {"id": "c1", "call": "apply_discount", "args": [100, 10], "kwargs": {}},
            {"id": "c2", "call": "apply_discount", "args": [50, 5], "kwargs": {}},
        ],
    )
    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert len(reasons) == 1
    assert "changed since capture" in reasons[0]


def test_eligible_reason_closure_drifted_in_dependency(work_root):
    root, cfg = _make_fully_eligible(work_root)
    legacy_root = cm_common.resolved_paths(root, cfg)["legacy_root"]
    money_path = legacy_root / "shop" / "money.py"
    money_path.write_text(money_path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    inv_path = root / "inventory.json"
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    inv["units"]["shop.money"]["source_sha256"] = cm_common.sha256_file(money_path)
    cm_common.atomic_write_json(inv_path, inv)

    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert len(reasons) == 1
    assert "drifted" in reasons[0]
    # only the dependency that actually changed is named, not shop.pricing too
    changed_segment = reasons[0].split("drifted for ", 1)[1].split(" since ", 1)[0]
    assert changed_segment == "shop.money"

    pricing_path = legacy_root / "shop" / "pricing.py"
    assert cm_common.sha256_file(pricing_path) == inv["units"]["shop.pricing"]["source_sha256"]


def test_eligible_reason_conventions_sentinel(work_root):
    root, cfg = _make_fully_eligible(work_root)
    cm_common.atomic_write_text(root / "conventions.md", "# Conventions\n\nCHOOSE_CONVENTIONS\n")
    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert len(reasons) == 1
    assert "CHOOSE_CONVENTIONS" in reasons[0]


# ---------------------------------------------------------------------------
# converge
# ---------------------------------------------------------------------------


def test_converge_succeeds_with_all_preconditions(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_target(root, cfg, "shop.pricing")
    target_sha256, key_sha256 = _write_r2_r3(root, cfg, "shop.pricing")
    _write_review(root, "shop.pricing", 1, findings=[], target_sha256=target_sha256)

    code = ledger.cmd_converge(root, cfg, types.SimpleNamespace(unit="shop.pricing"))
    assert code == cm_common.EXIT_OK
    led = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
    assert led["units"]["shop.pricing"]["state"] == "converged"
    assert led["units"]["shop.pricing"]["key_sha256"] == key_sha256


def test_converge_refuses_on_malformed_review(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_target(root, cfg, "shop.pricing")
    target_sha256, _ = _write_r2_r3(root, cfg, "shop.pricing")
    _write_review(root, "shop.pricing", 1, findings=[], malformed=["not json"], target_sha256=target_sha256)

    with pytest.raises(SystemExit) as exc:
        ledger.cmd_converge(root, cfg, types.SimpleNamespace(unit="shop.pricing"))
    assert exc.value.code == cm_common.EXIT_FAIL


def test_converge_refuses_on_stale_r2_r3(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_target(root, cfg, "shop.pricing")
    target_sha256, _ = _write_r2_r3(root, cfg, "shop.pricing")
    _write_review(root, "shop.pricing", 1, findings=[], target_sha256=target_sha256)
    _write_target(root, cfg, "shop.pricing", content="def apply_discount(price, pct):\n    return 0\n")

    with pytest.raises(SystemExit) as exc:
        ledger.cmd_converge(root, cfg, types.SimpleNamespace(unit="shop.pricing"))
    assert exc.value.code == cm_common.EXIT_FAIL


def test_converge_refuses_after_dependency_shim_replaced_by_port(work_root):
    # U's own target file and the legacy-side key stay identical throughout;
    # only its dependency's target file changes from a shim to a real port.
    root, cfg = _make_fully_eligible(work_root)
    _write_target(root, cfg, "shop.pricing")

    money_target = cm_common.target_file(root, cfg, "shop.money")
    money_target.parent.mkdir(parents=True, exist_ok=True)
    money_target.write_text(
        "# codebase-migrator: shim for shop.money\nfrom shop.money import round_money\n",
        encoding="utf-8",
    )

    target_sha256, _ = _write_r2_r3(root, cfg, "shop.pricing")
    _write_review(root, "shop.pricing", 1, findings=[], target_sha256=target_sha256)

    # converge succeeds while the dependency is still a shim.
    code = ledger.cmd_converge(root, cfg, types.SimpleNamespace(unit="shop.pricing"))
    assert code == cm_common.EXIT_OK

    # replace the shim with a real port -- different bytes, same module name.
    money_target.write_text("def round_money(x):\n    return round(x, 2)\n", encoding="utf-8")

    # the stored r2/r3 (key_sha256 computed while money was a shim) must now
    # be refused: a fresh cache_key's target_closure_sha256 has moved, even
    # though shop.pricing's own target file and legacy_closure_sha256 have not.
    with pytest.raises(SystemExit) as exc:
        ledger.cmd_converge(root, cfg, types.SimpleNamespace(unit="shop.pricing"))
    assert exc.value.code == cm_common.EXIT_FAIL

    # classify flips the already-converged unit to stale, naming the field.
    ledger.cmd_classify(root, cfg, types.SimpleNamespace())
    led = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
    assert led["units"]["shop.pricing"]["state"] == "stale"
    assert "target_closure_sha256" in led["units"]["shop.pricing"]["reason"]


def test_converge_refuses_on_unrefused_finding(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_target(root, cfg, "shop.pricing")
    target_sha256, _ = _write_r2_r3(root, cfg, "shop.pricing")
    finding, _digest = _finding()
    _write_review(root, "shop.pricing", 1, findings=[finding], target_sha256=target_sha256)

    with pytest.raises(SystemExit) as exc:
        ledger.cmd_converge(root, cfg, types.SimpleNamespace(unit="shop.pricing"))
    assert exc.value.code == cm_common.EXIT_FAIL


def test_converge_succeeds_after_refusing_finding(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_target(root, cfg, "shop.pricing")
    target_sha256, _ = _write_r2_r3(root, cfg, "shop.pricing")
    finding, digest = _finding()
    _write_review(root, "shop.pricing", 1, findings=[finding], target_sha256=target_sha256)

    ledger.cmd_refuse(
        root, cfg, types.SimpleNamespace(unit="shop.pricing", finding_digest=digest, reason="not applicable")
    )
    code = ledger.cmd_converge(root, cfg, types.SimpleNamespace(unit="shop.pricing"))
    assert code == cm_common.EXIT_OK


def test_converge_refuses_on_missing_review(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_target(root, cfg, "shop.pricing")
    _write_r2_r3(root, cfg, "shop.pricing")

    with pytest.raises(SystemExit) as exc:
        ledger.cmd_converge(root, cfg, types.SimpleNamespace(unit="shop.pricing"))
    assert exc.value.code == cm_common.EXIT_FAIL


def test_converge_refuses_after_drift_even_with_fresh_r2_r3(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_target(root, cfg, "shop.pricing")

    legacy_root = cm_common.resolved_paths(root, cfg)["legacy_root"]
    money_path = legacy_root / "shop" / "money.py"
    money_path.write_text(money_path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    inv_path = root / "inventory.json"
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    inv["units"]["shop.money"]["source_sha256"] = cm_common.sha256_file(money_path)
    cm_common.atomic_write_json(inv_path, inv)

    ledger.cmd_accept_drift(
        root, cfg, types.SimpleNamespace(unit="shop.pricing", operator="alice", reason="benign refactor")
    )

    target_sha256, _ = _write_r2_r3(root, cfg, "shop.pricing")
    _write_review(root, "shop.pricing", 1, findings=[], target_sha256=target_sha256)

    with pytest.raises(SystemExit) as exc:
        ledger.cmd_converge(root, cfg, types.SimpleNamespace(unit="shop.pricing"))
    assert exc.value.code == cm_common.EXIT_FAIL


# ---------------------------------------------------------------------------
# accept-drift
# ---------------------------------------------------------------------------


def test_accept_drift_refuses_while_inventory_stale(work_root):
    root, cfg = _make_fully_eligible(work_root)
    legacy_root = cm_common.resolved_paths(root, cfg)["legacy_root"]
    money_path = legacy_root / "shop" / "money.py"
    money_path.write_text(money_path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        ledger.cmd_accept_drift(
            root, cfg, types.SimpleNamespace(unit="shop.pricing", operator="alice", reason="x")
        )
    assert exc.value.code == cm_common.EXIT_FAIL
    assert not (root / "drift_log.json").exists()


def test_accept_drift_records_and_removes_net_lock(work_root):
    root, cfg = _make_fully_eligible(work_root)
    legacy_root = cm_common.resolved_paths(root, cfg)["legacy_root"]
    money_path = legacy_root / "shop" / "money.py"
    money_path.write_text(money_path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    inv_path = root / "inventory.json"
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    inv["units"]["shop.money"]["source_sha256"] = cm_common.sha256_file(money_path)
    cm_common.atomic_write_json(inv_path, inv)

    code = ledger.cmd_accept_drift(
        root, cfg, types.SimpleNamespace(unit="shop.pricing", operator="alice", reason="benign refactor")
    )
    assert code == cm_common.EXIT_OK

    drift_log = json.loads((root / "drift_log.json").read_text(encoding="utf-8"))
    assert len(drift_log["entries"]) == 1
    assert drift_log["entries"][0]["unit"] == "shop.pricing"
    assert drift_log["entries"][0]["operator"] == "alice"

    net_lock = json.loads((root / "net.lock.json").read_text(encoding="utf-8"))
    assert "shop.pricing" not in net_lock["units"]

    reasons = ledger.eligible(root, cfg, "shop.pricing")
    assert any("no net recorded" in r for r in reasons)

    led = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
    assert led["units"]["shop.pricing"]["state"] == "pending"


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_classify_stale_by_convention(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_target(root, cfg, "shop.pricing")
    target_sha256, _ = _write_r2_r3(root, cfg, "shop.pricing")
    _write_review(root, "shop.pricing", 1, findings=[], target_sha256=target_sha256)
    ledger.cmd_converge(root, cfg, types.SimpleNamespace(unit="shop.pricing"))

    cm_common.atomic_write_text(root / "conventions.md", "# Conventions\n\nUse camelCase.\n")
    ledger.cmd_classify(root, cfg, types.SimpleNamespace())
    led = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
    assert led["units"]["shop.pricing"]["state"] == "stale_by_convention"


def test_classify_stale_for_other_change(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_target(root, cfg, "shop.pricing")
    target_sha256, _ = _write_r2_r3(root, cfg, "shop.pricing")
    _write_review(root, "shop.pricing", 1, findings=[], target_sha256=target_sha256)
    ledger.cmd_converge(root, cfg, types.SimpleNamespace(unit="shop.pricing"))

    lock_path = root / "registry.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["rows"]["shop.money:round_money"]["row"]["reason"] = "changed"
    lock["rows"]["shop.money:round_money"]["digest"] = "different"
    cm_common.atomic_write_json(lock_path, lock)

    ledger.cmd_classify(root, cfg, types.SimpleNamespace())
    led = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
    assert led["units"]["shop.pricing"]["state"] == "stale"
    assert "rows_sha256" in led["units"]["shop.pricing"]["reason"]


# ---------------------------------------------------------------------------
# refuse / admit
# ---------------------------------------------------------------------------


def test_refuse_rejects_unknown_digest(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_review(root, "shop.pricing", 1, findings=[])

    with pytest.raises(SystemExit) as exc:
        ledger.cmd_refuse(
            root, cfg, types.SimpleNamespace(unit="shop.pricing", finding_digest="deadbeef", reason="x")
        )
    assert exc.value.code == cm_common.EXIT_FAIL


def test_admit_rejects_unknown_digest(work_root):
    root, cfg = _make_fully_eligible(work_root)
    _write_review(root, "shop.pricing", 1, findings=[])

    with pytest.raises(SystemExit) as exc:
        ledger.cmd_admit(
            root, cfg, types.SimpleNamespace(unit="shop.pricing", round=1, finding_digest="deadbeef")
        )
    assert exc.value.code == cm_common.EXIT_FAIL


def test_refuse_then_admit_same_digest_rejected(work_root):
    root, cfg = _make_fully_eligible(work_root)
    finding, digest = _finding()
    _write_review(root, "shop.pricing", 1, findings=[finding])

    ledger.cmd_refuse(root, cfg, types.SimpleNamespace(unit="shop.pricing", finding_digest=digest, reason="no"))

    with pytest.raises(SystemExit) as exc:
        ledger.cmd_admit(root, cfg, types.SimpleNamespace(unit="shop.pricing", round=1, finding_digest=digest))
    assert exc.value.code == cm_common.EXIT_FAIL


def test_admit_then_refuse_same_digest_rejected(work_root):
    root, cfg = _make_fully_eligible(work_root)
    finding, digest = _finding(severity="major")
    _write_review(root, "shop.pricing", 1, findings=[finding])

    ledger.cmd_admit(root, cfg, types.SimpleNamespace(unit="shop.pricing", round=1, finding_digest=digest))

    with pytest.raises(SystemExit) as exc:
        ledger.cmd_refuse(root, cfg, types.SimpleNamespace(unit="shop.pricing", finding_digest=digest, reason="no"))
    assert exc.value.code == cm_common.EXIT_FAIL


# ---------------------------------------------------------------------------
# key / cache_key
# ---------------------------------------------------------------------------


def test_key_command_matches_cache_key_function(work_root, capsys):
    root, cfg = _make_fully_eligible(work_root)
    key = ledger.cache_key(root, cfg, "shop.pricing")
    code = ledger.cmd_key(root, cfg, types.SimpleNamespace(unit="shop.pricing"))
    assert code == cm_common.EXIT_OK
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["cache_key"] == key
    assert out["key_sha256"] == cm_common.sha256_json(key)


def test_plugin_sha256_hashes_only_py_files(work_root, monkeypatch):
    # A comparison against cache_key() itself (as in the test above) cannot
    # catch a regression that widens plugin_sha256 to non-.py files: both
    # sides would drift together. Point cm_common.plugin_root() at a
    # throwaway fake plugin tree instead, so a non-.py file can be added
    # without touching the real scripts/ directory.
    fake_plugin = work_root / "fake_plugin"
    scripts_dir = fake_plugin / "skills" / "codebase-migrator" / "scripts"
    templates_dir = fake_plugin / "skills" / "codebase-migrator" / "assets" / "templates"
    scripts_dir.mkdir(parents=True)
    templates_dir.mkdir(parents=True)
    (scripts_dir / "a.py").write_text("A = 1\n", encoding="utf-8")
    (scripts_dir / "b.py").write_text("B = 2\n", encoding="utf-8")

    monkeypatch.setattr(cm_common, "plugin_root", lambda: fake_plugin)

    root, cfg = _make_fully_eligible(work_root)
    key_before = ledger.cache_key(root, cfg, "shop.pricing")

    (scripts_dir / "NOTES.txt").write_text("not python\n", encoding="utf-8")
    key_after_non_py = ledger.cache_key(root, cfg, "shop.pricing")
    assert key_after_non_py["plugin_sha256"] == key_before["plugin_sha256"]

    # sanity: an actual .py addition DOES change it, so the assertion above
    # is not vacuously true because nothing gets hashed at all.
    (scripts_dir / "c.py").write_text("C = 3\n", encoding="utf-8")
    key_after_py = ledger.cache_key(root, cfg, "shop.pricing")
    assert key_after_py["plugin_sha256"] != key_before["plugin_sha256"]
