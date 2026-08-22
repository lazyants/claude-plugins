# The wrong exception type escapes a catch/contract, silently

Two measured cases, same shape: a `try`/`except` or a shared-body pinning test is written for one
exception type, and a sibling code path raises a *different* type instead — the intended handling
never fires, and nothing about the failure looks like the bug it actually is.

## A byte-identical pinned body behaves differently per module's own import list

A parity/pinning test that asserts SOURCE-TEXT identity across modules (`inspect.getsource`) does not
assert BEHAVIORAL identity — the same body text executes differently depending on what each module
itself imports.

Measured (`literary-translator` #438): a shared sentinel predicate is duplicated across four modules
and pinned byte-identical by `select_segments.test.py` via `inspect.getsource`, so the test proves
all four copies are the same *text*. Two of the four (`select_segments.py`, `final_audit.py`) had no
`import os`. The pinned body names `os.lstat`; in those two modules the same line raises `NameError`
instead of running the lstat call at all — and `NameError` is not an `OSError`, so it escapes the
predicate's own contract (a caller written to catch `OSError` around the predicate). The pinning test
stayed green throughout, because green text-identity is exactly what it checks; it was never able to
see that one module's identical-looking body was actually calling an undefined name.

**Apply:** a `getsource`-style pinning test proves the four copies read the same, never that they RUN
the same. Either widen the pin to actually import and execute the shared body in each module's own
namespace (not just diff the text), or audit each copy's import list for every name the shared body
references — text identity does not imply import-list identity.

## A swallow written for one exception type misses the type the real caller raises

Measured (`literary-translator` #491, PR #494): hoisting a manifest-extraction step made
`malformed_manifest` (exit 1) preempt `no_converged_segments` (exit 2, a documented non-fatal
BOOTSTRAP state) — a reordering defect on its own. The repair for that reordering still missed a
non-DICT manifest: the swallow around the extraction caught only `AssembleError`, while calling
`.get()` on a manifest that turned out not to be a dict raises `AttributeError` instead — the wrong
exception type again escapes the catch, same shape as the `NameError`/`OSError` case above.

**The pinning test missed it too, and for a diagnosable reason: its `parametrize` varied the
`segments` VALUE and never the top-level TYPE.** Every case fed the function a dict-shaped manifest
with different `segments` contents; none fed it a manifest that was not a dict at all, so the
`AttributeError` path was never constructed, let alone asserted on.

**Apply:** a `try`/`except SomeError` is only as sound as the enumeration of exception types every
real caller and every real input shape can actually raise — grep what the guarded call can raise, not
what you expect it to raise. And when a parametrized test varies one field's VALUE across cases,
check separately whether it ever varies that field's or a sibling field's TYPE — a table that is
exhaustive over values can still be silent on an entire class of malformed input.
