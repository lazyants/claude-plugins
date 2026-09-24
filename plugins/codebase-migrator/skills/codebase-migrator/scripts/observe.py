#!/usr/bin/env python3
"""Observation harness: canonical encoding, the persistent-state snapshot, the
audit-hook write boundary, and the traced import/case runner (plan section
4.6). `net_capture.py` and `diff_gate.py` import this module as a library
(`stage_trees`, `run_harness`, `encode`, `decode`); the module is also
executed directly as a subprocess: `python3 observe.py harness` reads one job
JSON from stdin and writes one result JSON to stdout.

No work happens at import time beyond defining names, so importing this
module (as a library) is always side-effect free.
"""

from __future__ import annotations

import gc
import io
import json
import os
import random
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cm_common  # noqa: E402

# The behavioural channels a golden-master observation is compared on
# (net_capture.py's cross-environment determinism check, diff_gate.py's
# legacy-vs-target replay comparison). Never `covered_lines`, `route_files`
# or any path -- those legitimately differ between runs.
BEHAVIOURAL_CHANNELS = (
    "status",
    "return",
    "error",
    "receiver_after",
    "args_after",
    "kwargs_after",
    "stdout",
    "stderr",
)

# ---------------------------------------------------------------------------
# Canonical encoding (plan 4.6)
# ---------------------------------------------------------------------------

_MAX_DEPTH = 64
_MISSING = object()


def new_encode_context(stage_modules=()) -> dict:
    """A fresh id/ref-tracking context for one encoding pass. `stage_modules`
    is the set of module names considered "defined in the stage", used only
    in state mode to decide whether a function/class is descended or left as
    a bare pointer."""
    return {"seen": {}, "next_id": 0, "stage_modules": frozenset(stage_modules)}


def _assign_id(ctx: dict, oid: int) -> int:
    n = ctx["next_id"]
    ctx["seen"][oid] = n
    ctx["next_id"] = n + 1
    return n


def _type_qualname(value) -> str:
    t = type(value)
    mod = getattr(t, "__module__", None)
    name = getattr(t, "__qualname__", t.__name__)
    if mod and mod != "builtins":
        return f"{mod}.{name}"
    return name


def _has_declared_slots(cls) -> bool:
    for klass in cls.__mro__:
        if "__slots__" in klass.__dict__:
            return True
    return False


def _instance_state(obj) -> dict:
    """`__dict__` plus declared `__slots__` values, slots in base-to-derived
    declaration order, `__dict__` entries appended after."""
    state: dict = {}
    cls = type(obj)
    for klass in reversed(cls.__mro__):
        slots = klass.__dict__.get("__slots__")
        if slots is None:
            continue
        if isinstance(slots, str):
            slots = (slots,)
        for name in slots:
            if name in ("__dict__", "__weakref__"):
                continue
            if hasattr(obj, name):
                state[name] = getattr(obj, name)
    if hasattr(obj, "__dict__"):
        for name, value in vars(obj).items():
            state[name] = value
    return state


def _defined_in_stage(obj, ctx: dict) -> bool:
    return getattr(obj, "__module__", None) in ctx["stage_modules"]


_OPAQUE_TYPES = (
    types.FunctionType,
    types.BuiltinFunctionType,
    types.ModuleType,
    type,
    types.GeneratorType,
    types.CoroutineType,
    types.MethodType,
)


def encode(value, ctx: dict | None = None, state: bool = False, depth: int = 0):
    """Canonical, JSON-able encoding of `value`. `ctx` carries the shared
    id/ref table for one encoding pass (aliasing between the receiver, the
    arguments and the return value is only visible when the same `ctx` is
    reused across all of them); omitted, a fresh one-shot context is used.
    `state=True` selects the state-snapshot variant (module/function/class
    get their own encodings instead of `$unsupported`)."""
    if ctx is None:
        ctx = new_encode_context()
    if depth > _MAX_DEPTH:
        return {"$unsupported": "depth"}
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return {"$float": repr(value)}
    if isinstance(value, bytes):
        return {"$bytes": value.hex()}
    if isinstance(value, tuple):
        return {"$tuple": [encode(v, ctx, state, depth + 1) for v in value]}
    if isinstance(value, frozenset):
        items = [encode(v, ctx, state, depth + 1) for v in value]
        items.sort(key=lambda e: json.dumps(e, sort_keys=True))
        return {"$frozenset": items}

    if state:
        if isinstance(value, types.ModuleType):
            return {"$module": value.__name__}
        if isinstance(value, type):
            return _encode_class_state(value, ctx, depth)
        if isinstance(value, types.FunctionType):
            return _encode_function_state(value, ctx, depth)

    oid = id(value)
    if oid in ctx["seen"]:
        return {"$ref": ctx["seen"][oid]}

    if isinstance(value, list):
        n = _assign_id(ctx, oid)
        return {"$list": [encode(v, ctx, state, depth + 1) for v in value], "$id": n}
    if isinstance(value, set):
        n = _assign_id(ctx, oid)
        items = [encode(v, ctx, state, depth + 1) for v in value]
        items.sort(key=lambda e: json.dumps(e, sort_keys=True))
        return {"$set": items, "$id": n}
    if isinstance(value, bytearray):
        n = _assign_id(ctx, oid)
        return {"$bytearray": value.hex(), "$id": n}
    if isinstance(value, dict):
        n = _assign_id(ctx, oid)
        pairs = [
            [encode(k, ctx, state, depth + 1), encode(v, ctx, state, depth + 1)]
            for k, v in value.items()
        ]
        return {"$dict": pairs, "$id": n}

    if not state:
        if isinstance(value, _OPAQUE_TYPES):
            return {"$unsupported": _type_qualname(value)}
        if hasattr(value, "__dict__") or _has_declared_slots(type(value)):
            n = _assign_id(ctx, oid)
            qualname = f"{type(value).__module__}:{type(value).__qualname__}"
            attrs = _instance_state(value)
            encoded_state = {k: encode(v, ctx, state, depth + 1) for k, v in attrs.items()}
            return {"$obj": qualname, "$state": encoded_state, "$id": n}
        return {"$unsupported": _type_qualname(value)}

    # State mode, no specific rule matched above (module/class/function
    # defined in the stage are already handled). Rather than keep
    # enumerating object shapes by name — this is the third review round to
    # find one this used to miss (function attributes, then class dunders,
    # now a bound method) — descend into whatever CPython's own garbage
    # collector says this object references. A bound method's referents are
    # its `__func__` and `__self__`; a builtin method like `[].append`'s is
    # the list itself; `functools.partial`'s are its func/args/keywords —
    # each covered generically, with no per-type special case. Plain custom
    # instances (with or without a real `__dict__`) are covered the same
    # way: their referents include the instance dict or slot values, which
    # already carries the mutation. A referent that is itself a module still
    # goes through this same `encode()` and hits the `$module` rule above,
    # rather than being expanded into its own (potentially huge) referent
    # graph.
    return _encode_ref_graph_state(value, ctx, depth)


def _encode_function_state(fn, ctx: dict, depth: int):
    qualname = f"{fn.__module__}:{fn.__qualname__}"
    if not _defined_in_stage(fn, ctx):
        return {"$fn": qualname}
    oid = id(fn)
    if oid in ctx["seen"]:
        return {"$ref": ctx["seen"][oid]}
    n = _assign_id(ctx, oid)
    closure = []
    if fn.__closure__:
        for cell in fn.__closure__:
            try:
                val = cell.cell_contents
            except ValueError:
                closure.append({"$empty_cell": True})
            else:
                closure.append(encode(val, ctx, True, depth + 1))
    defaults = encode(fn.__defaults__, ctx, True, depth + 1) if fn.__defaults__ is not None else None
    kwdefaults = (
        encode(fn.__kwdefaults__, ctx, True, depth + 1) if fn.__kwdefaults__ is not None else None
    )
    attrs = encode(dict(fn.__dict__), ctx, True, depth + 1)
    return {
        "$fn": qualname,
        "$id": n,
        "defaults": defaults,
        "kwdefaults": kwdefaults,
        "closure": closure,
        "attrs": attrs,
    }


def _encode_class_state(cls, ctx: dict, depth: int):
    qualname = f"{cls.__module__}:{cls.__qualname__}"
    if not _defined_in_stage(cls, ctx):
        return {"$cls": qualname}
    oid = id(cls)
    if oid in ctx["seen"]:
        return {"$ref": ctx["seen"][oid]}
    n = _assign_id(ctx, oid)
    dict_items = {}
    for name, value in vars(cls).items():
        if name in ("__dict__", "__weakref__"):
            continue
        if isinstance(value, (staticmethod, classmethod)):
            dict_items[name] = encode(value.__func__, ctx, True, depth + 1)
        elif isinstance(value, property):
            dict_items[name] = {
                "fget": encode(value.fget, ctx, True, depth + 1) if value.fget else None,
                "fset": encode(value.fset, ctx, True, depth + 1) if value.fset else None,
                "fdel": encode(value.fdel, ctx, True, depth + 1) if value.fdel else None,
            }
        else:
            dict_items[name] = encode(value, ctx, True, depth + 1)
    return {"$cls": qualname, "$id": n, "dict": dict_items}


_REFERENT_SKIP_TYPES = (type, types.CodeType, types.FrameType)


def _safe_repr_sha256(value) -> str:
    """A C-level immutable value type (`decimal.Decimal`, for one) can hold
    its value with no referents at all — `gc.get_referents(Decimal("1"))`
    is just `[<class 'decimal.Decimal'>]`, which the skip list drops, so a
    rebind from `Decimal("1")` to `Decimal("2")` would otherwise encode
    identically. `repr()` is the one thing that reliably reflects such a
    value; a `repr()` that itself raises reports `"repr_error"` rather than
    propagating."""
    try:
        text = repr(value)
    except Exception:
        return "repr_error"
    return cm_common.sha256_bytes(text.encode("utf-8", errors="backslashreplace"))


def _encode_ref_graph_state(value, ctx: dict, depth: int):
    """State-mode fallback for any object with no more specific rule:
    encode what CPython's own garbage collector says this object
    references, plus a hash of its `repr()` (see `_safe_repr_sha256`).
    Type, code and frame objects are skipped outright (pure interpreter
    bookkeeping, never behaviour); a module referent is not skipped, but
    recurses through `encode()` itself, which hits the `$module` rule and
    does not expand its own referent graph."""
    oid = id(value)
    if oid in ctx["seen"]:
        return {"$ref": ctx["seen"][oid]}
    n = _assign_id(ctx, oid)
    items = []
    for ref in gc.get_referents(value):
        if isinstance(ref, _REFERENT_SKIP_TYPES):
            continue
        items.append(encode(ref, ctx, True, depth + 1))
    return {
        "$ref_graph": _type_qualname(value),
        "$id": n,
        "items": items,
        "repr_sha256": _safe_repr_sha256(value),
    }


def decode(obj):
    """Inverse of the plain-JSON and tagged-literal subset of `encode`.
    `$ref`, `$obj` and `$unsupported` cannot be decoded: a case's arguments
    are literal inputs, never a captured observation."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, list):
        return [decode(v) for v in obj]
    if isinstance(obj, dict):
        if "$float" in obj:
            return float(obj["$float"])
        if "$bytes" in obj:
            return bytes.fromhex(obj["$bytes"])
        if "$tuple" in obj:
            return tuple(decode(v) for v in obj["$tuple"])
        if "$list" in obj:
            return [decode(v) for v in obj["$list"]]
        if "$dict" in obj:
            return {decode(k): decode(v) for k, v in obj["$dict"]}
        if "$set" in obj:
            return {decode(v) for v in obj["$set"]}
        if "$frozenset" in obj:
            return frozenset(decode(v) for v in obj["$frozenset"])
        if "$bytearray" in obj:
            return bytearray.fromhex(obj["$bytearray"])
        if "$ref" in obj or "$obj" in obj or "$unsupported" in obj:
            raise ValueError(f"decode does not support a tagged observation: {sorted(obj)}")
        return {k: decode(v) for k, v in obj.items()}
    raise ValueError(f"cannot decode a value of type {type(obj).__name__}")


# ---------------------------------------------------------------------------
# State snapshot (plan 4.6)
# ---------------------------------------------------------------------------


def _is_under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _resolve_quiet(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def state_snapshot(stage_root) -> dict:
    """Encode every attribute of every loaded module whose `__file__`
    resolves under `stage_root/legacy` or `stage_root/target`, keyed
    `"<module>:<name>"`. Each key gets its own fresh id context."""
    stage_root = Path(stage_root).resolve()
    legacy_base = _resolve_quiet(stage_root / "legacy")
    target_base = _resolve_quiet(stage_root / "target")

    stage_mods: dict[str, types.ModuleType] = {}
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        fp = _resolve_quiet(Path(f))
        if _is_under(fp, legacy_base) or _is_under(fp, target_base):
            stage_mods[name] = mod

    stage_mod_names = frozenset(stage_mods.keys())
    result: dict[str, object] = {}
    for name, mod in stage_mods.items():
        for attr, value in vars(mod).items():
            if attr in cm_common.INTERPRETER_MODULE_ATTRS:
                continue
            ctx = new_encode_context(stage_mod_names)
            result[f"{name}:{attr}"] = encode(value, ctx, state=True)
    return result


def snapshot_changes(pre: dict, post: dict) -> list:
    """Sorted keys whose encoding differs between `pre` and `post`, plus
    `"<module>:<loaded during call>"` for every stage module present in
    `post` but absent from `pre` (a module with no baseline cannot be shown
    unchanged, so it is reported instead)."""

    def _mod(key: str) -> str:
        return key.split(":", 1)[0]

    pre_modules = {_mod(k) for k in pre}
    post_modules = {_mod(k) for k in post}
    new_modules = post_modules - pre_modules
    changed = {f"{m}:<loaded during call>" for m in new_modules}
    for key in set(pre) | set(post):
        if _mod(key) in new_modules:
            continue
        if pre.get(key, _MISSING) != post.get(key, _MISSING):
            changed.add(key)
    return sorted(changed)


# ---------------------------------------------------------------------------
# Audit hook (write boundary) — plan 4.6
# ---------------------------------------------------------------------------

_AUDIT_DENY_EXACT = frozenset(
    {
        "os.remove",
        "os.unlink",
        "os.rename",
        "os.replace",
        "os.mkdir",
        "os.rmdir",
        "os.chmod",
        "os.chown",
        "os.link",
        "os.symlink",
        "os.truncate",
        "os.utime",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "os.fork",
        "os.forkpty",
        "socket.connect",
        "socket.bind",
        "socket.sendto",
        "ctypes.dlopen",
        "ctypes.dlsym",
        "ctypes.cdata",
    }
)

_WRITE_MODE_CHARS = ("w", "a", "x", "+")


def _write_flag_mask() -> int:
    mask = 0
    for name in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"):
        mask |= getattr(os, name, 0)
    return mask


def _open_is_write(args) -> bool:
    values = list(args) + [None, None, None]
    mode, flags = values[1], values[2]
    if isinstance(mode, str) and any(c in mode for c in _WRITE_MODE_CHARS):
        return True
    if isinstance(flags, int) and (flags & _write_flag_mask()):
        return True
    return False


def _summarize_args(args) -> str:
    try:
        text = " ".join(str(a) for a in args)
    except Exception:
        text = repr(args)
    return text[:200]


def _deny_reason(event: str, args) -> str | None:
    if event == "open":
        if _open_is_write(args):
            return f"open {_summarize_args(args)}"
        return None
    if event in _AUDIT_DENY_EXACT:
        return f"{event} {_summarize_args(args)}"
    if event.startswith("shutil.") or event.startswith("_thread.start_"):
        return f"{event} {_summarize_args(args)}"
    if event == "import":
        module = args[0] if args else None
        if isinstance(module, str) and (
            module in ("ctypes", "_ctypes")
            or module.startswith("ctypes.")
            or module.startswith("_ctypes.")
        ):
            return f"import {module}"
        return None
    return None


_audit_state = {
    "installed": False,
    "denied": None,
    "settrace_count": 0,
    "setprofile_count": 0,
}


def install_audit_hook() -> None:
    """Install the process-wide audit hook once. It denies (raises
    `PermissionError`, recorded in the current window's denied list) the
    events named in plan 4.6, and counts `sys.settrace`/`sys.setprofile`
    calls for the tamper check without denying them."""
    if _audit_state["installed"]:
        return

    def _hook(event: str, args) -> None:
        if event == "sys.settrace":
            _audit_state["settrace_count"] += 1
            return
        if event == "sys.setprofile":
            _audit_state["setprofile_count"] += 1
            return
        reason = _deny_reason(event, args)
        if reason is not None:
            if _audit_state["denied"] is not None:
                _audit_state["denied"].append(reason)
            raise PermissionError(reason)

    sys.addaudithook(_hook)
    if sys.version_info < (3, 12):
        _raise_thread_start_event()
    _audit_state["installed"] = True


def _raise_thread_start_event() -> None:
    """Python 3.11 starts a thread without raising any audit event (3.12
    added `_thread.start_new_thread`), so the hook above could not deny it.
    Wrap the one primitive every thread start goes through so it raises that
    same event first; the hook then denies it exactly as on 3.12+."""
    import _thread
    import threading

    original = _thread.start_new_thread

    def start_new_thread(function, args, kwargs=None):
        sys.audit("_thread.start_new_thread", function, args, kwargs)
        if kwargs is None:
            return original(function, args)
        return original(function, args, kwargs)

    _thread.start_new_thread = start_new_thread
    _thread.start_new = start_new_thread
    threading._start_new_thread = start_new_thread


def _begin_window() -> None:
    _audit_state["denied"] = []


def _end_window() -> list:
    denied = _audit_state["denied"] or []
    _audit_state["denied"] = None
    return denied


# ---------------------------------------------------------------------------
# Tracing: coverage (capture) and route recording (replay) — plan 4.6
# ---------------------------------------------------------------------------


def _stage_relative(filename: str, stage_root: Path) -> str | None:
    fp = _resolve_quiet(Path(filename))
    for sub in ("legacy", "target"):
        base = _resolve_quiet(stage_root / sub)
        if _is_under(fp, base):
            return f"{sub}/{fp.relative_to(base).as_posix()}"
    return None


def _make_capture_tracer(trace_file: str | None, covered: set):
    trace_file_norm = os.path.normpath(trace_file) if trace_file else None

    def _line_tracer(frame, event, arg):
        if event == "line":
            covered.add(frame.f_lineno)
        return _line_tracer

    def _global_tracer(frame, event, arg):
        if event != "call":
            return None
        if trace_file_norm is not None and os.path.normpath(frame.f_code.co_filename) == trace_file_norm:
            return _line_tracer
        return None

    return _global_tracer


def _make_replay_profiler(stage_root: Path, route: set):
    def _profiler(frame, event, arg):
        if event != "call":
            return
        rel = _stage_relative(frame.f_code.co_filename, stage_root)
        if rel is not None:
            route.add(rel)

    return _profiler


def _run_capture_window(trace_file: str | None, fn):
    """Bracket exactly one call to `fn` with exactly one `sys.settrace`
    start and stop. There is deliberately no way to pause tracing mid-call:
    an earlier design used a module-level flag the observed code could
    reach through `sys.modules["__main__"]` (the harness IS `__main__`) and
    set around a legacy import or call, silencing the tracer while the
    audit hook's settrace/setprofile CALL counters — the only thing the
    tamper check reads — stayed at exactly 2. A snapshot needed between two
    windows must be taken by the caller outside both, never by pausing one."""
    covered: set = set()
    tracer = _make_capture_tracer(trace_file, covered)
    pre_t, pre_p = _audit_state["settrace_count"], _audit_state["setprofile_count"]
    sys.settrace(tracer)
    try:
        fn()
    finally:
        sys.settrace(None)
    tamper = []
    if _audit_state["settrace_count"] - pre_t != 2:
        tamper.append("tamper:sys.settrace")
    if _audit_state["setprofile_count"] - pre_p != 0:
        tamper.append("tamper:sys.setprofile")
    return sorted(covered), tamper


def _run_replay_window(stage_root: Path, fn):
    """Bracket exactly one call to `fn` with exactly one `sys.setprofile`
    start and stop — see `_run_capture_window` for why there is no
    pause/resume escape hatch."""
    route: set = set()
    profiler = _make_replay_profiler(stage_root, route)
    pre_t, pre_p = _audit_state["settrace_count"], _audit_state["setprofile_count"]
    sys.setprofile(profiler)
    try:
        fn()
    finally:
        sys.setprofile(None)
    tamper = []
    if _audit_state["setprofile_count"] - pre_p != 2:
        tamper.append("tamper:sys.setprofile")
    if _audit_state["settrace_count"] - pre_t != 0:
        tamper.append("tamper:sys.settrace")
    return sorted(route), tamper


# ---------------------------------------------------------------------------
# Clock / hash-seed / random-seed environment (plan 4.6)
# ---------------------------------------------------------------------------

_ENV_PARAMS = {
    "A": {"PYTHONHASHSEED": "0", "TZ": "UTC", "random_seed": 0, "clock": 1700000000.0},
    "B": {"PYTHONHASHSEED": "1", "TZ": "Asia/Kolkata", "random_seed": 1, "clock": 1703200000.25},
}


def _patch_clock(value: float) -> None:
    if hasattr(time, "tzset"):
        time.tzset()

    def _time():
        return value

    def _time_ns():
        return int(value * 1_000_000_000)

    def _monotonic():
        return value

    def _monotonic_ns():
        return int(value * 1_000_000_000)

    def _perf_counter():
        return value

    def _perf_counter_ns():
        return int(value * 1_000_000_000)

    def _process_time():
        return value

    def _process_time_ns():
        return int(value * 1_000_000_000)

    time.time = _time
    time.time_ns = _time_ns
    time.monotonic = _monotonic
    time.monotonic_ns = _monotonic_ns
    time.perf_counter = _perf_counter
    time.perf_counter_ns = _perf_counter_ns
    time.process_time = _process_time
    time.process_time_ns = _process_time_ns

    orig_localtime = time.localtime
    orig_gmtime = time.gmtime
    orig_ctime = time.ctime
    orig_asctime = time.asctime
    orig_strftime = time.strftime

    def _localtime(secs=None):
        return orig_localtime(value if secs is None else secs)

    def _gmtime(secs=None):
        return orig_gmtime(value if secs is None else secs)

    def _ctime(secs=None):
        return orig_ctime(value if secs is None else secs)

    def _asctime(t=None):
        return orig_asctime(_localtime() if t is None else t)

    def _strftime(fmt, t=None):
        return orig_strftime(fmt, _localtime() if t is None else t)

    time.localtime = _localtime
    time.gmtime = _gmtime
    time.ctime = _ctime
    time.asctime = _asctime
    time.strftime = _strftime


def _seed_and_patch_clock(env: str) -> None:
    params = _ENV_PARAMS[env]
    random.seed(params["random_seed"])
    _patch_clock(params["clock"])


# ---------------------------------------------------------------------------
# Case execution
# ---------------------------------------------------------------------------


def _redirect_streams():
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    return old_out, old_err


def _restore_streams(old_out, old_err):
    out_text = sys.stdout.getvalue() if isinstance(sys.stdout, io.StringIO) else ""
    err_text = sys.stderr.getvalue() if isinstance(sys.stderr, io.StringIO) else ""
    sys.stdout, sys.stderr = old_out, old_err
    return out_text, err_text


def _build_error(exc: BaseException) -> dict:
    return {"type": f"{type(exc).__module__}:{type(exc).__qualname__}", "message": str(exc)}


def _empty_observation(case_id, harness_error: str) -> dict:
    return {
        "case_id": case_id,
        "status": "harness_error",
        "return": None,
        "error": None,
        "receiver_after": None,
        "args_after": [],
        "kwargs_after": {},
        "stdout": "",
        "stderr": "",
        "state_changes": [],
        "route_files": None,
        "covered_lines": None,
        "denied": [],
        "harness_error": harness_error,
    }


def _run_one_case_inner(module_obj, case: dict, calls: dict, mode: str, stage_root: Path, trace_file):
    case_id = case["id"]
    call_spec = case["call"]
    target_name = calls.get(call_spec, call_spec)
    is_method = "." in target_name
    args = [decode(a) for a in case.get("args", [])]
    kwargs = {k: decode(v) for k, v in case.get("kwargs", {}).items()}

    box = {"receiver": None, "return": None, "error": None}

    def _invoke():
        try:
            if is_method:
                cls_name, method_name = target_name.split(".", 1)
                cls = getattr(module_obj, cls_name)
                init_args = [decode(a) for a in case.get("init_args", [])]
                init_kwargs = {k: decode(v) for k, v in case.get("init_kwargs", {}).items()}
                box["receiver"] = cls(*init_args, **init_kwargs)
                method = getattr(box["receiver"], method_name)
                box["return"] = method(*args, **kwargs)
            else:
                fn = getattr(module_obj, target_name)
                box["return"] = fn(*args, **kwargs)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # the observed error, part of the behavioural channel
            box["error"] = exc

    old_out, old_err = _redirect_streams()
    _begin_window()
    pre = state_snapshot(stage_root)
    try:
        if mode == "capture":
            covered_or_route, tamper = _run_capture_window(trace_file, _invoke)
        else:
            covered_or_route, tamper = _run_replay_window(stage_root, _invoke)
    finally:
        post = state_snapshot(stage_root)
        out_text, err_text = _restore_streams(old_out, old_err)
        denied = _end_window()

    state_changes = snapshot_changes(pre, post)
    denied = sorted(set(denied) | set(tamper))

    ctx = new_encode_context()
    receiver_enc = encode(box["receiver"], ctx)
    args_enc = [encode(a, ctx) for a in args]
    kwargs_enc = {k: encode(kwargs[k], ctx) for k in sorted(kwargs)}
    return_enc = encode(box["return"], ctx)
    error_enc = _build_error(box["error"]) if box["error"] is not None else None

    return {
        "case_id": case_id,
        "status": "ok",
        "return": return_enc,
        "error": error_enc,
        "receiver_after": receiver_enc,
        "args_after": args_enc,
        "kwargs_after": kwargs_enc,
        "stdout": out_text,
        "stderr": err_text,
        "state_changes": state_changes,
        "route_files": covered_or_route if mode == "replay" else None,
        "covered_lines": covered_or_route if mode == "capture" else None,
        "denied": denied,
        "harness_error": None,
    }


def _run_one_case(module_obj, case: dict, calls: dict, mode: str, stage_root: Path, trace_file) -> dict:
    case_id = case.get("id", "?")
    try:
        return _run_one_case_inner(module_obj, case, calls, mode, stage_root, trace_file)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # a bug in the harness itself, not in the observed code
        return _empty_observation(case_id, str(exc))


# ---------------------------------------------------------------------------
# Harness entrypoint: one job in, one result out
# ---------------------------------------------------------------------------


def run_job(job: dict) -> dict:
    mode = job["mode"]
    env = job["env"]
    preload = job.get("preload") or []
    stage_root = Path(job["stage_root"]).resolve()
    sys_path = job.get("sys_path") or []
    module_name = job["module"]
    calls = job.get("calls") or {}
    cases = job.get("cases") or []
    trace_file = job.get("trace_file")

    sys.dont_write_bytecode = True
    _seed_and_patch_clock(env)
    for p in reversed(sys_path):
        if p not in sys.path:
            sys.path.insert(0, p)
    install_audit_hook()

    holder = {"module_obj": None}

    def _import_module_only():
        import importlib

        holder["module_obj"] = importlib.import_module(module_name)

    def _import_preload_only():
        import importlib

        for name in preload:
            importlib.import_module(name)

    def _run_window(fn):
        """One bracketed window: exactly one start and one stop. Returns
        `(covered_or_route, tamper, error)`; `error` is the stringified
        exception on failure, `covered_or_route` then `None`."""
        try:
            if mode == "capture":
                covered_or_route, tamper = _run_capture_window(trace_file, fn)
            else:
                covered_or_route, tamper = _run_replay_window(stage_root, fn)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            return None, [], str(exc)
        return covered_or_route, tamper, None

    old_out, old_err = _redirect_streams()
    import_error = None

    # Window 1: import `module` alone. A snapshot is never taken while a
    # window is open (see `_run_capture_window`'s docstring) — it is taken
    # here, between window 1 and window 2, outside both.
    _begin_window()
    module_covered_or_route, module_tamper, import_error = _run_window(_import_module_only)
    module_denied = _end_window()

    pre_preload: dict = {}
    post_preload: dict = {}
    preload_covered_or_route: list = []
    preload_tamper: list = []
    preload_denied: list = []
    if import_error is None:
        pre_preload = state_snapshot(stage_root)

        # Window 2: import the preload list alone.
        _begin_window()
        preload_covered_or_route, preload_tamper, import_error = _run_window(_import_preload_only)
        preload_denied = _end_window()

        post_preload = state_snapshot(stage_root) if import_error is None else pre_preload

    _restore_streams(old_out, old_err)

    if import_error is not None:
        observations = [_empty_observation(case.get("id", "?"), import_error) for case in cases]
        return {
            "observations": observations,
            "import_covered_lines": None,
            "import_route_files": None,
            "import_error": import_error,
        }

    pre_modules = {k.split(":", 1)[0] for k in pre_preload}
    raw_preload_changes = snapshot_changes(pre_preload, post_preload)
    preload_changes = [c for c in raw_preload_changes if c.split(":", 1)[0] in pre_modules]

    import_covered_or_route = sorted(set(module_covered_or_route or []) | set(preload_covered_or_route or []))
    import_extra_denied = sorted(
        set(module_denied) | set(preload_denied) | set(module_tamper) | set(preload_tamper)
    )

    observations = []
    for case in cases:
        obs = _run_one_case(holder["module_obj"], case, calls, mode, stage_root, trace_file)
        if preload_changes:
            obs["state_changes"] = sorted(set(obs["state_changes"]) | set(preload_changes))
        if import_extra_denied:
            obs["denied"] = sorted(set(obs["denied"]) | set(import_extra_denied))
        observations.append(obs)

    return {
        "observations": observations,
        "import_covered_lines": import_covered_or_route if mode == "capture" else None,
        "import_route_files": import_covered_or_route if mode == "replay" else None,
        "import_error": None,
    }


# ---------------------------------------------------------------------------
# Staging and the out-of-process runner
# ---------------------------------------------------------------------------


class HarnessFailure(Exception):
    """The harness subprocess produced no parseable result."""


def stage_trees(root: Path, cfg: dict, stage: Path) -> dict:
    """Copy `legacy_root` and `target_root` into `stage/legacy` and
    `stage/target` (skipping `.git`/`__pycache__`). Jobs reference only these
    staged copies, never the durable root's own trees."""
    root = Path(root)
    stage = Path(stage)
    paths = cm_common.resolved_paths(root, cfg)
    legacy_dst = stage / "legacy"
    target_dst = stage / "target"
    _copy_tree(paths["legacy_root"], legacy_dst)
    _copy_tree(paths["target_root"], target_dst)
    return {"legacy": legacy_dst, "target": target_dst}


def _copy_tree(src: Path, dst: Path) -> None:
    src = Path(src)
    dst = Path(dst)
    if not src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        return
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(".git", "__pycache__"),
        dirs_exist_ok=True,
        symlinks=False,
    )


def run_harness(job: dict, stage: Path, timeout_s: int = 120) -> dict:
    """Run `python3 observe.py harness` in a fresh subprocess with a minimal
    environment, feed it `job` on stdin, and parse its one-line result. A
    timeout is reported as `harness_error` for every case; a non-JSON or
    empty stdout raises `HarnessFailure` instead."""
    env_name = job["env"]
    params = _ENV_PARAMS[env_name]
    stage = Path(stage)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(stage),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": params["PYTHONHASHSEED"],
        "TZ": params["TZ"],
    }
    script = str(Path(__file__).resolve())
    job_text = json.dumps(job)

    try:
        proc = subprocess.run(
            [sys.executable, script, "harness"],
            input=job_text,
            capture_output=True,
            text=True,
            cwd=str(stage),
            env=env,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        message = f"harness timed out after {timeout_s}s"
        return {
            "observations": [_empty_observation(c.get("id", "?"), message) for c in job.get("cases", [])],
            "import_covered_lines": None,
            "import_route_files": None,
            "import_error": message,
        }

    stdout_text = (proc.stdout or "").strip()
    if not stdout_text:
        raise HarnessFailure((proc.stderr or "")[-2000:])
    try:
        return json.loads(stdout_text)
    except json.JSONDecodeError:
        raise HarnessFailure((proc.stderr or "")[-2000:])


# ---------------------------------------------------------------------------
# CLI: `python3 observe.py harness`
# ---------------------------------------------------------------------------


def main() -> int:
    # Saved before any stream redirection: the harness's own result must
    # always reach the real stdout, even if a case leaves the streams
    # unrestored somewhere inside a bug.
    true_stdout = sys.stdout

    if len(sys.argv) < 2 or sys.argv[1] != "harness":
        message = "usage: observe.py harness"
        print(message, file=sys.stderr)
        true_stdout.write(json.dumps({"ok": False, "error": message}))
        true_stdout.write("\n")
        true_stdout.flush()
        return cm_common.EXIT_CANNOT

    try:
        job = json.loads(sys.stdin.read())
    except Exception as exc:
        result = {
            "observations": [],
            "import_covered_lines": None,
            "import_route_files": None,
            "import_error": f"bad job input: {exc}",
        }
        true_stdout.write(json.dumps(result))
        true_stdout.write("\n")
        true_stdout.flush()
        return cm_common.EXIT_CANNOT

    try:
        result = run_job(job)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        result = {
            "observations": [],
            "import_covered_lines": None,
            "import_route_files": None,
            "import_error": f"harness crashed: {exc}",
        }
        true_stdout.write(json.dumps(result))
        true_stdout.write("\n")
        true_stdout.flush()
        return cm_common.EXIT_CANNOT

    true_stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True))
    true_stdout.write("\n")
    true_stdout.flush()
    return cm_common.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
