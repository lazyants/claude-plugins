"""Tests for observe.py: canonical encoding, the state snapshot, the audit
hook, and the harness (plan section 4.6, tests owned by C)."""

from __future__ import annotations

import importlib
import sys

import pytest

import observe


# ---------------------------------------------------------------------------
# Encoding / decoding round trips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [None, True, False, 0, -5, 123456789012345678901234567890, "hello", "", "unicode: \u2028\u00e9"],
)
def test_encode_decode_roundtrip_native(value):
    enc = observe.encode(value)
    assert enc == value
    assert observe.decode(enc) == value


def test_encode_decode_roundtrip_float():
    for value in (1.5, -0.0, float("inf"), float("-inf")):
        enc = observe.encode(value)
        assert enc == {"$float": repr(value)}
        assert repr(observe.decode(enc)) == repr(value)
    nan_enc = observe.encode(float("nan"))
    nan_dec = observe.decode(nan_enc)
    assert nan_dec != nan_dec  # still NaN


def test_encode_decode_roundtrip_bytes():
    value = b"\x00\x01\xff hello"
    enc = observe.encode(value)
    assert enc == {"$bytes": value.hex()}
    assert observe.decode(enc) == value


def test_encode_decode_roundtrip_tuple():
    value = (1, "a", (2, 3))
    enc = observe.encode(value)
    assert enc == {"$tuple": [1, "a", {"$tuple": [2, 3]}]}
    assert observe.decode(enc) == value


def test_encode_decode_roundtrip_bytearray():
    value = bytearray(b"abc")
    enc = observe.encode(value)
    assert "$id" in enc
    assert observe.decode(enc) == value


def test_encode_decode_roundtrip_set_and_frozenset():
    value = {3, 1, 2}
    enc = observe.encode(value)
    assert "$id" in enc
    assert observe.decode(enc) == value

    fvalue = frozenset({"b", "a"})
    fenc = observe.encode(fvalue)
    assert "$id" not in fenc
    assert observe.decode(fenc) == fvalue


def test_encode_decode_roundtrip_list_and_dict():
    value = [1, {"a": 2}, [3, 4]]
    enc = observe.encode(value)
    assert observe.decode(enc) == value

    dvalue = {"x": 1, "y": [2, 3]}
    denc = observe.encode(dvalue)
    assert observe.decode(denc) == dvalue


def test_decode_rejects_ref_obj_unsupported():
    for bad in ({"$ref": 0}, {"$obj": "m:c", "$state": {}, "$id": 0}, {"$unsupported": "x"}):
        with pytest.raises(ValueError):
            observe.decode(bad)


def test_aliasing_dedupe_style_return_is_ref_but_a_copy_is_not():
    ctx = observe.new_encode_context()
    shared = [1, 2, 3]
    arg_enc = observe.encode(shared, ctx)
    return_same = observe.encode(shared, ctx)  # same object, e.g. dedupe(items) returning items
    assert return_same == {"$ref": arg_enc["$id"]}

    ctx2 = observe.new_encode_context()
    shared2 = [1, 2, 3]
    arg_enc2 = observe.encode(shared2, ctx2)
    return_copy = observe.encode(list(shared2), ctx2)  # a port that returns an equal COPY
    assert "$ref" not in return_copy
    assert return_copy["$list"] == arg_enc2["$list"]
    assert return_copy["$id"] != arg_enc2["$id"]


# ---------------------------------------------------------------------------
# State snapshot: one throwaway module per mutation shape
# ---------------------------------------------------------------------------


@pytest.fixture
def make_module(tmp_path):
    created = []

    def _make(subdir: str, name: str, source: str):
        base = tmp_path / subdir
        base.mkdir(parents=True, exist_ok=True)
        (base / f"{name}.py").write_text(source)
        sys.path.insert(0, str(base))
        try:
            sys.modules.pop(name, None)
            mod = importlib.import_module(name)
        finally:
            sys.path.remove(str(base))
        created.append(name)
        return mod

    yield _make
    for name in created:
        sys.modules.pop(name, None)


def _changes_after(tmp_path, action):
    pre = observe.state_snapshot(tmp_path)
    action()
    post = observe.state_snapshot(tmp_path)
    return observe.snapshot_changes(pre, post)


def test_state_snapshot_detects_mutated_module_dict(tmp_path, make_module):
    mod = make_module("legacy", "sm_dict", "D = {'a': 1}\n\n\ndef mutate():\n    D['a'] = 2\n")
    changes = _changes_after(tmp_path, mod.mutate)
    assert "sm_dict:D" in changes


def test_state_snapshot_detects_rebound_module_global(tmp_path, make_module):
    mod = make_module("legacy", "sm_rebind", "X = 1\n\n\ndef rebind():\n    global X\n    X = 2\n")
    changes = _changes_after(tmp_path, mod.rebind)
    assert "sm_rebind:X" in changes


def test_state_snapshot_detects_mutated_default_argument(tmp_path, make_module):
    mod = make_module(
        "legacy", "sm_default", "def f(seen=[]):\n    seen.append(1)\n    return seen\n"
    )
    changes = _changes_after(tmp_path, mod.f)
    assert "sm_default:f" in changes


def test_state_snapshot_detects_mutated_closure_cell(tmp_path, make_module):
    mod = make_module(
        "legacy",
        "sm_closure",
        "def _make():\n"
        "    n = 0\n\n"
        "    def step():\n"
        "        nonlocal n\n"
        "        n += 1\n"
        "        return n\n\n"
        "    return step\n\n\n"
        "step = _make()\n",
    )
    changes = _changes_after(tmp_path, mod.step)
    assert "sm_closure:step" in changes


def test_state_snapshot_detects_mutated_class_attribute(tmp_path, make_module):
    mod = make_module(
        "legacy", "sm_classattr", "class C:\n    count = 0\n\n\ndef bump():\n    C.count += 1\n"
    )
    changes = _changes_after(tmp_path, mod.bump)
    assert "sm_classattr:C" in changes


def test_state_snapshot_detects_mutated_module_instance_attribute(tmp_path, make_module):
    mod = make_module(
        "legacy",
        "sm_instance",
        "class Box:\n"
        "    def __init__(self):\n"
        "        self.value = 0\n\n\n"
        "BOX = Box()\n\n\n"
        "def bump():\n    BOX.value += 1\n",
    )
    changes = _changes_after(tmp_path, mod.bump)
    assert "sm_instance:BOX" in changes


def test_state_snapshot_detects_mutation_through_local_alias(tmp_path, make_module):
    mod = make_module(
        "legacy",
        "sm_alias",
        "_REG = {}\n\n\ndef put(k, v):\n    reg = _REG\n    reg[k] = v\n",
    )
    changes = _changes_after(tmp_path, lambda: mod.put("k", 1))
    assert "sm_alias:_REG" in changes


def test_state_snapshot_detects_mutated_function_attribute(tmp_path, make_module):
    mod = make_module(
        "legacy",
        "sm_fattr",
        "def f():\n    f.calls += 1\n    return f.calls\n\n\nf.calls = 0\n",
    )
    changes = _changes_after(tmp_path, mod.f)
    assert "sm_fattr:f" in changes


def test_state_snapshot_detects_mutated_default_of_user_call(tmp_path, make_module):
    mod = make_module(
        "legacy",
        "sm_call_default",
        "class Adder:\n"
        "    def __call__(self, x, seen=[]):\n"
        "        seen.append(x)\n"
        "        return seen\n\n\n"
        "ADDER = Adder()\n",
    )
    changes = _changes_after(tmp_path, lambda: mod.ADDER(1))
    assert "sm_call_default:Adder" in changes


def test_state_snapshot_reports_no_change_for_argument_or_receiver_only_mutation(tmp_path, make_module):
    mod = make_module(
        "legacy",
        "sm_pure",
        "def only_mutates_arg(lst):\n"
        "    lst.append(1)\n"
        "    return lst\n\n\n"
        "class R:\n"
        "    def __init__(self):\n"
        "        self.items = []\n\n"
        "    def add(self, x):\n"
        "        self.items.append(x)\n",
    )

    def action():
        mod.only_mutates_arg([1, 2])
        mod.R().add(1)

    assert _changes_after(tmp_path, action) == []


def test_state_snapshot_terminates_on_recursive_closure(tmp_path, make_module):
    mod = make_module(
        "legacy",
        "sm_recursive",
        "def make_rec():\n"
        "    def rec(n):\n"
        "        if n <= 0:\n"
        "            return 0\n"
        "        return 1 + rec(n - 1)\n\n"
        "    return rec\n\n\n"
        "REC = make_rec()\n",
    )
    changes = _changes_after(tmp_path, lambda: mod.REC(5))
    assert changes == []


def test_per_key_id_context_independent_of_module_load_order(tmp_path, make_module):
    untouched = make_module("legacy", "zzz_untouched", "X = [1, 2, [3, 4]]\n")
    del untouched
    pre = observe.state_snapshot(tmp_path)
    before = pre["zzz_untouched:X"]

    make_module("legacy", "aaa_new_module", "Y = 1\n")  # sorts before "zzz_untouched"

    post = observe.state_snapshot(tmp_path)
    assert post["zzz_untouched:X"] == before


def test_state_snapshot_detects_mutation_through_module_level_bound_method(tmp_path, make_module):
    """A receiver mutated only through a module-level bound method
    (`_bump = Counter().bump`, the receiver itself never separately a
    module attribute) used to be invisible: `types.MethodType` sits in
    `_OPAQUE_TYPES`, so it encoded as `$unsupported` on every snapshot,
    identically before and after."""
    mod = make_module(
        "legacy",
        "sm_bound_method",
        "class Counter:\n"
        "    def __init__(self):\n"
        "        self.n = 0\n\n"
        "    def bump(self):\n"
        "        self.n += 1\n"
        "        return self.n\n\n\n"
        "_bump = Counter().bump\n\n\n"
        "def call_bump():\n"
        "    return _bump()\n",
    )
    changes = _changes_after(tmp_path, mod.call_bump)
    assert "sm_bound_method:_bump" in changes


def test_state_snapshot_detects_mutation_through_builtin_bound_method(tmp_path, make_module):
    """`_add = [].append`: the list is reachable only via the builtin
    method's own referents, never as a separate module attribute."""
    mod = make_module(
        "legacy",
        "sm_builtin_method",
        "_add = [].append\n\n\ndef call_add(x):\n    return _add(x)\n",
    )
    changes = _changes_after(tmp_path, lambda: mod.call_add(1))
    assert "sm_builtin_method:_add" in changes


def test_state_snapshot_detects_mutation_through_functools_partial(tmp_path, make_module):
    """`functools.partial`'s own `__dict__` is always empty (verified: a
    fresh partial's `vars()` is `{}`) even though `hasattr(..., "__dict__")`
    is True — its real state (func/args/keywords) is never in that dict, so
    the old `hasattr(__dict__)` fallback would have silently encoded a
    constant empty state regardless of what the bound mutable arg does."""
    mod = make_module(
        "legacy",
        "sm_partial",
        "import functools\n\n\n"
        "def _accumulate(acc, x):\n"
        "    acc.append(x)\n"
        "    return acc\n\n\n"
        "_bound_acc = functools.partial(_accumulate, [])\n\n\n"
        "def call_partial(x):\n"
        "    return _bound_acc(x)\n",
    )
    changes = _changes_after(tmp_path, lambda: mod.call_partial(1))
    assert "sm_partial:_bound_acc" in changes


def test_state_snapshot_reports_no_change_for_a_pure_call_through_generic_referents(tmp_path, make_module):
    """The generic `gc.get_referents` fallback must not spuriously flag a
    call that touches none of these values."""
    mod = make_module(
        "legacy",
        "sm_ref_graph_pure",
        "_add = [].append\n\n\ndef noop():\n    return 1\n",
    )
    assert _changes_after(tmp_path, mod.noop) == []


# ---------------------------------------------------------------------------
# Subprocess-level harness behaviour
# ---------------------------------------------------------------------------


def test_module_loaded_during_call_vs_preloaded(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "helper_lc.py").write_text("VALUE = 1\n")
    (legacy / "owner_lc.py").write_text(
        "def load_helper():\n    import helper_lc\n    return helper_lc.VALUE\n"
    )
    job = {
        "mode": "capture",
        "env": "A",
        "preload": [],
        "stage_root": str(tmp_path),
        "sys_path": [str(legacy)],
        "module": "owner_lc",
        "calls": {},
        "cases": [{"id": "c1", "call": "load_helper", "args": []}],
        "trace_file": str(legacy / "owner_lc.py"),
    }
    not_preloaded = observe.run_harness(job, stage=tmp_path, timeout_s=30)
    obs1 = not_preloaded["observations"][0]
    assert "helper_lc:<loaded during call>" in obs1["state_changes"]

    preloaded_job = dict(job, preload=["helper_lc"])
    preloaded = observe.run_harness(preloaded_job, stage=tmp_path, timeout_s=30)
    obs2 = preloaded["observations"][0]
    assert "helper_lc:<loaded during call>" not in obs2["state_changes"]


def test_preloaded_registry_append_flags_every_case(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "reg_owner.py").write_text("REGISTRY = []\n\n\ndef noop():\n    return 1\n")
    (legacy / "reg_helper.py").write_text(
        "import reg_owner\n\nreg_owner.REGISTRY.append('helper-loaded')\n"
    )
    job = {
        "mode": "capture",
        "env": "A",
        "preload": ["reg_helper"],
        "stage_root": str(tmp_path),
        "sys_path": [str(legacy)],
        "module": "reg_owner",
        "calls": {},
        "cases": [{"id": "c1", "call": "noop", "args": []}],
        "trace_file": str(legacy / "reg_owner.py"),
    }
    result = observe.run_harness(job, stage=tmp_path, timeout_s=30)
    obs = result["observations"][0]
    assert "reg_owner:REGISTRY" in obs["state_changes"]


def test_env_a_vs_b_signals_differ(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "clockmod.py").write_text(
        "import time\n"
        "import random\n\n"
        "clock = time.time\n\n\n"
        "def now():\n    return time.time()\n\n\n"
        "def now_via_alias():\n    return clock()\n\n\n"
        "def rand():\n    return random.random()\n\n\n"
        "def set_order():\n    return list({'zeta', 'alpha', 'mu', 'beta', 'gamma'})\n\n\n"
        "def hour():\n    return time.localtime().tm_hour\n"
    )
    cases = [
        {"id": "now", "call": "now", "args": []},
        {"id": "alias", "call": "now_via_alias", "args": []},
        {"id": "rand", "call": "rand", "args": []},
        {"id": "order", "call": "set_order", "args": []},
        {"id": "hour", "call": "hour", "args": []},
    ]
    runs = {}
    for env in ("A", "B"):
        job = {
            "mode": "capture",
            "env": env,
            "preload": [],
            "stage_root": str(tmp_path),
            "sys_path": [str(legacy)],
            "module": "clockmod",
            "calls": {},
            "cases": cases,
            "trace_file": str(legacy / "clockmod.py"),
        }
        runs[env] = {o["case_id"]: o for o in observe.run_harness(job, stage=tmp_path, timeout_s=30)["observations"]}
    for cid in ("now", "alias", "rand", "order", "hour"):
        assert runs["A"][cid]["return"] != runs["B"][cid]["return"], cid


def test_audit_hook_denies_writes_and_dangerous_calls_but_allows_reads(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    readable = tmp_path / "readable.txt"
    readable.write_text("hello")
    (legacy / "auditmod.py").write_text(
        "import os\n"
        "import subprocess\n"
        "import threading\n\n"
        f"READABLE = {str(readable)!r}\n\n\n"
        "def write_open():\n"
        "    with open('newfile.txt', 'w') as f:\n"
        "        f.write('x')\n"
        "    return 1\n\n\n"
        "def os_open_wronly():\n"
        "    return os.open('newfile2.txt', os.O_WRONLY | os.O_CREAT)\n\n\n"
        "def remove_file():\n"
        "    os.remove(READABLE)\n"
        "    return 1\n\n\n"
        "def run_subprocess():\n"
        "    subprocess.Popen(['true'])\n"
        "    return 1\n\n\n"
        "def import_ctypes():\n"
        "    import ctypes\n"
        "    return 1\n\n\n"
        "def start_thread():\n"
        "    t = threading.Thread(target=lambda: None)\n"
        "    t.start()\n"
        "    return 1\n\n\n"
        "def read_file():\n"
        "    with open(READABLE, 'r') as f:\n"
        "        return f.read()\n"
    )
    cases = [
        {"id": "wr", "call": "write_open", "args": []},
        {"id": "osopen", "call": "os_open_wronly", "args": []},
        {"id": "rm", "call": "remove_file", "args": []},
        {"id": "sp", "call": "run_subprocess", "args": []},
        {"id": "ct", "call": "import_ctypes", "args": []},
        {"id": "th", "call": "start_thread", "args": []},
        {"id": "rd", "call": "read_file", "args": []},
    ]
    job = {
        "mode": "capture",
        "env": "A",
        "preload": [],
        "stage_root": str(tmp_path),
        "sys_path": [str(legacy)],
        "module": "auditmod",
        "calls": {},
        "cases": cases,
        "trace_file": str(legacy / "auditmod.py"),
    }
    result = observe.run_harness(job, stage=tmp_path, timeout_s=30)
    obs = {o["case_id"]: o for o in result["observations"]}
    for cid in ("wr", "osopen", "rm", "sp", "ct", "th"):
        assert obs[cid]["denied"], cid
        assert obs[cid]["error"] is not None, cid
        assert obs[cid]["error"]["type"] == "builtins:PermissionError", cid
    assert obs["rd"]["denied"] == []
    assert obs["rd"]["return"] == "hello"


def test_tamper_check_flags_disable_and_disable_then_restore(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "tampermod.py").write_text(
        "import sys\n\n\n"
        "def disable_only():\n    sys.setprofile(None)\n    return 1\n\n\n"
        "def disable_and_restore():\n"
        "    old = sys.getprofile()\n"
        "    sys.setprofile(None)\n"
        "    sys.setprofile(old)\n"
        "    return 2\n\n\n"
        "def clean():\n    return 3\n"
    )
    job = {
        "mode": "replay",
        "env": "A",
        "preload": [],
        "stage_root": str(tmp_path),
        "sys_path": [str(legacy)],
        "module": "tampermod",
        "calls": {},
        "cases": [
            {"id": "c1", "call": "disable_only", "args": []},
            {"id": "c2", "call": "disable_and_restore", "args": []},
            {"id": "c3", "call": "clean", "args": []},
        ],
        "trace_file": None,
    }
    result = observe.run_harness(job, stage=tmp_path, timeout_s=30)
    obs = {o["case_id"]: o for o in result["observations"]}
    assert "tamper:sys.setprofile" in obs["c1"]["denied"]
    assert "tamper:sys.setprofile" in obs["c2"]["denied"]
    assert obs["c3"]["denied"] == []


def test_harness_timeout_yields_harness_error_per_case(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "slowmod.py").write_text("import time\n\ntime.sleep(5)\n\n\ndef f():\n    return 1\n")
    job = {
        "mode": "capture",
        "env": "A",
        "preload": [],
        "stage_root": str(tmp_path),
        "sys_path": [str(legacy)],
        "module": "slowmod",
        "calls": {},
        "cases": [{"id": "c1", "call": "f", "args": []}],
        "trace_file": str(legacy / "slowmod.py"),
    }
    result = observe.run_harness(job, stage=tmp_path, timeout_s=1)
    assert result["import_error"] is not None
    assert "timed out" in result["import_error"]
    assert result["observations"][0]["status"] == "harness_error"
