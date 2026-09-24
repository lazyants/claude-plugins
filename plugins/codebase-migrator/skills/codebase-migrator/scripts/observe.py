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
        if isinstance(value, staticmethod):
            dict_items[name] = encode(value.__func__, ctx, True, depth + 1)
        elif isinstance(value, classmethod):
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


def _encode_ref_graph_state(value, ctx: dict, depth: int):
    """State-mode fallback for any object with no more specific rule:
    encode what CPython's own garbage collector says this object
    references. Type, code and frame objects are skipped outright (pure
    interpreter bookkeeping, never behaviour); a module referent is not
    skipped, but recurses through `encode()` itself, which hits the
    `$module` rule and does not expand its own referent graph."""
    oid = id(value)
    if oid in ctx["seen"]:
        return {"$ref": ctx["seen"][oid]}
    n = _assign_id(ctx, oid)
    items = []
    for ref in gc.get_referents(value):
        if isinstance(ref, _REFERENT_SKIP_TYPES):
            continue
        items.append(encode(ref, ctx, True, depth + 1))
    return {"$ref_graph": _type_qualname(value), "$id": n, "items": items}


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
    _audit_state["installed"] = True


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


# A traced/profiled window can itself need to call harness code while the
# tracer/profiler is still installed (the import window takes a state
# snapshot mid-window, per plan 4.6 step 2). That snapshot walks every loaded
# module and encodes its attributes — dozens of Python-level calls — and a
# process-wide `sys.setprofile` would otherwise see every one of them,
# multiplying the work by the number of loaded modules and making a plain
# import take minutes. `_trace_state["suspended"]` lets the harness's own
# code run with the tracer/profiler function still installed (so the
# tamper-check counters, which only count `sys.settrace`/`sys.setprofile`
# CALLS, are unaffected) but immediately returning without doing any work.
_trace_state = {"suspended": False}


def pause_tracing():
    """Context manager: run a block with the currently-installed capture
    tracer or replay profiler turned into a no-op, without an extra
    `sys.settrace`/`sys.setprofile` call (which would break the tamper
    check's exact-delta-of-2 invariant)."""

    class _Pause:
        def __enter__(self):
            self._prev = _trace_state["suspended"]
            _trace_state["suspended"] = True

        def __exit__(self, *exc):
            _trace_state["suspended"] = self._prev

    return _Pause()


def _make_capture_tracer(trace_file: str | None, covered: set):
    trace_file_norm = os.path.normpath(trace_file) if trace_file else None

    def _line_tracer(frame, event, arg):
        if _trace_state["suspended"]:
            return None
        if event == "line":
            covered.add(frame.f_lineno)
        return _line_tracer

    def _global_tracer(frame, event, arg):
        if _trace_state["suspended"]:
            return None
        if event != "call":
            return None
        if trace_file_norm is not None and os.path.normpath(frame.f_code.co_filename) == trace_file_norm:
            return _line_tracer
        return None

    return _global_tracer


def _make_replay_profiler(stage_root: Path, route: set):
    def _profiler(frame, event, arg):
        if _trace_state["suspended"]:
            return
        if event != "call":
            return
        rel = _stage_relative(frame.f_code.co_filename, stage_root)
        if rel is not None:
            route.add(rel)

    return _profiler


def _run_capture_window(trace_file: str | None, fn):
    covered: set = set()
    tracer = _make_capture_tracer(trace_file, covered)
    pre_t, pre_p = _audit_state["settrace_count"], _audit_state["setprofile_count"]
    _trace_state["suspended"] = False
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
    route: set = set()
    profiler = _make_replay_profiler(stage_root, route)
    pre_t, pre_p = _audit_state["settrace_count"], _audit_state["setprofile_count"]
    _trace_state["suspended"] = False
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

    state = {"module_obj": None, "pre_preload": None, "post_preload": None}

    def _do_import():
        import importlib

        state["module_obj"] = importlib.import_module(module_name)
        with pause_tracing():
            state["pre_preload"] = state_snapshot(stage_root)
        for name in preload:
            importlib.import_module(name)
        with pause_tracing():
            state["post_preload"] = state_snapshot(stage_root)

    _begin_window()
    old_out, old_err = _redirect_streams()
    import_error = None
    import_covered_or_route: list | None = None
    import_tamper: list = []
    try:
        if mode == "capture":
            import_covered_or_route, import_tamper = _run_capture_window(trace_file, _do_import)
        else:
            import_covered_or_route, import_tamper = _run_replay_window(stage_root, _do_import)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        import_error = str(exc)
        import_covered_or_route = None
    finally:
        _restore_streams(old_out, old_err)
    import_denied = _end_window()

    if import_error is not None:
        observations = [_empty_observation(case.get("id", "?"), import_error) for case in cases]
        return {
            "observations": observations,
            "import_covered_lines": None,
            "import_route_files": None,
            "import_error": import_error,
        }

    pre_preload = state["pre_preload"] or {}
    post_preload = state["post_preload"] or pre_preload
    pre_modules = {k.split(":", 1)[0] for k in pre_preload}
    raw_preload_changes = snapshot_changes(pre_preload, post_preload)
    preload_changes = [c for c in raw_preload_changes if c.split(":", 1)[0] in pre_modules]

    import_extra_denied = sorted(set(import_denied) | set(import_tamper))

    observations = []
    for case in cases:
        obs = _run_one_case(state["module_obj"], case, calls, mode, stage_root, trace_file)
        if preload_changes:
            obs["state_changes"] = sorted(set(obs["state_changes"]) | set(preload_changes))
        if import_extra_denied:
            obs["denied"] = sorted(set(obs["denied"]) | set(import_extra_denied))
        observations.append(obs)

    return {
        "observations": observations,
        "import_covered_lines": sorted(import_covered_or_route) if mode == "capture" else None,
        "import_route_files": sorted(import_covered_or_route) if mode == "replay" else None,
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
    timeout, or a non-JSON/empty stdout, is reported as `harness_error` for
    every case (the latter also raises `HarnessFailure`)."""
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
    if len(sys.argv) < 2 or sys.argv[1] != "harness":
        print("usage: observe.py harness", file=sys.stderr)
        return cm_common.EXIT_CANNOT

    # Saved before any stream redirection: the harness's own result must
    # always reach the real stdout, even if a case leaves the streams
    # unrestored somewhere inside a bug.
    true_stdout = sys.stdout

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
