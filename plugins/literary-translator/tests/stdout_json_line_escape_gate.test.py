"""tests/stdout_json_line_escape_gate.test.py -- #369: no script under
assets/scripts/ may put a raw `json.dumps(..., ensure_ascii=False)` on stdout.

## What the defect is

`json.dumps(..., ensure_ascii=False)` backslash-escapes every control character
below 0x20 unconditionally, but leaves a LITERAL U+0085 NEL, U+2028 LINE
SEPARATOR and U+2029 PARAGRAPH SEPARATOR raw -- JSON permits all three inside a
string. Such a payload is ONE line to `str.split("\\n")` and TWO to
`str.splitlines()`. Several of these CLIs answer on a stdout an LLM agent reads
with a LINE-oriented grammar (`templates/glossary-pass-wf.template.js` tells the
dispatched agent to run a check command "and read its one line of JSON output",
then answer with a line-shaped sentinel), so a `source_form`, a rationale, a
block id or an exception message carrying one of those characters forges a
second physical line. 1.16.2 fixed that inside `skeptic_ready.py` alone; #369
closed the remaining 48 sites and this gate is what keeps them closed.

## Why a gate rather than only the fixes

The fixes are the one-time half. The class GREW between the issue being filed
and being worked (38 sites in 17 scripts -> 45 in 20, as three scripts joined),
so the trigger that produced it still fires: a new CLI, or a new error path in an
old one, reaches for the same idiom. The gate's consumer is CI.

## ZERO exemptions, deliberately

An earlier scope proposed exempting the "operator-read" stdouts. Cut: the escape
is decoded-value-preserving (a `\\uXXXX` escape round-trips through every JSON
parser identically to the raw character), so routing a site nobody parses costs
nothing, whereas an exemption list is a permanent adjudication that must be
re-argued whenever a stdout gains a reader. Uniform routing means this file has
no allowlist to keep honest.

## What this gate does NOT claim

It is syntactic. It covers the five shapes by which a raw serialisation actually
reaches stdout in this tree, plus simple intra-function dataflow -- it does NOT
prove total stdout coverage, and a payload built by a route none of the five
describe (say, assembled through two intermediate objects, or via a `__str__`)
would pass it. That limit is stated here rather than implied, and the
`test_gate_flags_*` mutation tests below are the evidence that each shape it
DOES claim is genuinely detected: a gate never watched failing on the shape it
advertises is not a gate.
"""

import ast
import os
import textwrap
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
assert SCRIPTS_DIR.is_dir(), f"scripts directory not found at {SCRIPTS_DIR}"


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------

def _raw_dumps_calls(node):
    """Every `json.dumps/dump(..., ensure_ascii=False)` call inside `node`."""
    out = []
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in ("dumps", "dump")
                and getattr(sub.func.value, "id", None) == "json"):
            for kw in sub.keywords:
                if (kw.arg == "ensure_ascii" and isinstance(kw.value, ast.Constant)
                        and kw.value.value is False):
                    out.append(sub)
    return out


def _goes_to_stderr(call):
    return any(kw.arg == "file" and "stderr" in ast.dump(kw.value) for kw in call.keywords)


def _print_calls(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "print":
            if not _goes_to_stderr(sub):
                yield sub


def _names_mentioned(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _functions(tree):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def raw_stdout_sites(source: str, label: str = "<source>") -> list:
    """Return `(line, shape)` for every raw serialisation reaching stdout.

    Five shapes, each one measured in the real tree before being encoded here:

    A. directly inside a non-stderr ``print(...)`` argument, or inside
       ``json.dump(..., sys.stdout, ...)``  -- 45 sites at the time of #369;
    B. assigned to a local name that the same function then prints
       (``verbatim_census.py``'s ``rendered = json.dumps(...)`` / ``print(rendered)``);
    C. returned from a helper whose result is printed
       (``backfill_ever_converged.py``'s ``_json_line()``; the raw dump sits
       INSIDE the helper, so a rule anchored on ``raise``/``print`` ancestry
       misses it entirely);
    D. carried by an exception value that a stdout-printing handler re-emits
       (``backfill_resume_gate_ack.py``'s ``fatal()`` raising ``FatalError(
       json.dumps(...))``, printed as ``str(exc)``);
    E. written with ``sys.stdout.write(...)`` rather than ``print`` -- directly
       or through one local. ``print`` is not the only spelling and this tree
       already uses the other one (``bootstrap_names.py`` emits its payload that
       way), so without this rule the most plausible way to reintroduce #369
       would pass.

    Shape D requires the module to actually contain a non-stderr ``print`` inside
    an ``except`` handler -- the raise's exception CLASS NAME is not evidence of
    anything on its own, and judging by it would reject an inner dump that is
    caught and re-serialised safely (``canon_validate.py`` does exactly that).
    """
    tree = ast.parse(source, filename=label)
    hits = set()

    def flag(call, shape):
        hits.add((call.lineno, shape))

    # --- A: direct -----------------------------------------------------------
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            if _goes_to_stderr(node):
                continue
            for call in _raw_dumps_calls(node):
                flag(call, "A/print")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "dump"
                and getattr(node.func.value, "id", None) == "json"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Attribute)
                and node.args[1].attr == "stdout"):
            for call in _raw_dumps_calls(node):
                flag(call, "A/json.dump->stdout")

    # --- E: sys.stdout.write(...) -------------------------------------------
    # `print` is not the only spelling, and this tree already uses the other one:
    # bootstrap_names.py writes its stdout payload with sys.stdout.write. Without
    # this rule the most plausible way to reintroduce #369 passes the gate.
    def _is_stdout_write(call):
        f = call.func
        return (isinstance(f, ast.Attribute) and f.attr == "write"
                and isinstance(f.value, ast.Attribute) and f.value.attr == "stdout"
                # ...and the receiver must be `sys` itself. Without this, ANY
                # object of this program's own that happens to carry a writable
                # `.stdout` is flagged as a #369 site, though nothing an agent
                # reads by line is on the other end of it.
                and isinstance(f.value.value, ast.Name) and f.value.value.id == "sys")

    stdout_write_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_stdout_write(node):
            for call in _raw_dumps_calls(node):
                flag(call, "E/sys.stdout.write")
            for arg in node.args:
                stdout_write_names |= _names_mentioned(arg)
    if stdout_write_names:
        for fn in _functions(tree):
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    bound = {tg.id for tg in node.targets if isinstance(tg, ast.Name)}
                    if bound & stdout_write_names:
                        for call in _raw_dumps_calls(node.value):
                            flag(call, "E/sys.stdout.write")

    # --- B: assigned to a name this function prints ---------------------------
    for fn in _functions(tree):
        printed = set()
        for p in _print_calls(fn):
            printed |= _names_mentioned(p)
        if not printed:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                bound = {t.id for t in node.targets if isinstance(t, ast.Name)}
                if bound & printed:
                    for call in _raw_dumps_calls(node.value):
                        flag(call, "B/assign-then-print")

    # --- C: returned from a helper whose result is printed --------------------
    printed_callees = set()
    for p in _print_calls(tree):
        for sub in ast.walk(p):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                printed_callees.add(sub.func.id)
    for fn in _functions(tree):
        if fn.name not in printed_callees:
            continue
        returned_names = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and node.value is not None:
                for call in _raw_dumps_calls(node.value):
                    flag(call, "C/return-then-print")
                returned_names |= _names_mentioned(node.value)
        # ...and one hop through a local. `backfill_ever_converged.py`'s
        # `_json_line()` does `line = json.dumps(...)` and then `return line`,
        # so a rule that only reads the RETURN expression sees nothing: the
        # measured shape needs the assignment followed into the return.
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                bound = {t.id for t in node.targets if isinstance(t, ast.Name)}
                if bound & returned_names:
                    for call in _raw_dumps_calls(node.value):
                        flag(call, "C/return-then-print")

    # --- D: raised, then printed by a handler ---------------------------------
    prints_in_handler = any(
        any(True for _ in _print_calls(handler))
        for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    )
    if prints_in_handler:
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                for call in _raw_dumps_calls(node.exc):
                    flag(call, "D/raise-then-print")

    return sorted(hits)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def _scanned_files():
    return sorted(p for p in SCRIPTS_DIR.glob("*.py") if p.is_file())


def test_the_scan_covers_every_script_in_the_directory():
    """Anti-vacuity by EXACT SET, never by a floor.

    A floor ("at least 40 files") is satisfied by a scan that silently dropped
    ten of fifty, and a loop that runs over the wrong population prints exactly
    what a correct one prints. So the scanned set is compared against an
    independent enumeration -- `os.listdir`, not the same `Path.glob` the gate
    uses -- and both sides must be non-empty.
    """
    scanned = {p.name for p in _scanned_files()}
    independent = {
        name for name in os.listdir(SCRIPTS_DIR)
        if name.endswith(".py") and (SCRIPTS_DIR / name).is_file()
    }
    assert scanned, "the gate scanned zero files -- the glob or the path is wrong"
    assert independent, "independent enumeration found zero .py files -- the path is wrong"
    assert scanned == independent, (
        "the gate's file set has diverged from what is actually in the scripts "
        f"directory. Only in the scan: {sorted(scanned - independent)}. "
        f"Only on disk: {sorted(independent - scanned)}"
    )


def test_no_script_puts_a_raw_json_dumps_on_stdout():
    """THE GATE. Zero exemptions -- see this module's docstring."""
    offenders = []
    for path in _scanned_files():
        for line, shape in raw_stdout_sites(path.read_text(encoding="utf-8"), path.name):
            offenders.append(f"{path.name}:{line} ({shape})")
    assert not offenders, (
        "raw json.dumps(..., ensure_ascii=False) reaches stdout at:\n  "
        + "\n  ".join(offenders)
        + "\n\nRoute it through json_stdout.dumps_line() instead: U+0085, U+2028 "
        "and U+2029 survive ensure_ascii=False raw, and a payload carrying one "
        "renders to a reading agent as TWO physical lines. See #369."
    )


def test_every_script_that_emits_json_on_stdout_loads_the_shared_helper():
    """The routing half's companion: a script that calls `dumps_line` must load
    it by EXACT PATH. A bare `import json_stdout` resolves through the global
    `sys.modules` cache regardless of which staged copy the caller intended, so
    one process staging several durable roots would bind the first root's copy
    for all of them -- and a staged root MISSING the helper would then run green
    against a cached one. Pinning the loader shape here is what keeps
    `json_stdout_contract.test.py`'s hostile staging test meaningful."""
    wrong = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8")
        if path.name == "json_stdout.py" or "dumps_line(" not in text:
            continue
        if "_JSON_STDOUT_PATH" not in text or "spec_from_file_location" not in text:
            wrong.append(f"{path.name}: uses dumps_line() without the exact-path loader")
        if "\nimport json_stdout" in text or "\nfrom json_stdout import" in text:
            wrong.append(f"{path.name}: bare `import json_stdout` -- must load by exact path")
    assert not wrong, "\n".join(wrong)


# ---------------------------------------------------------------------------
# Mutation tests -- the gate watched failing on each shape it advertises
# ---------------------------------------------------------------------------

_SHAPES = {
    "A/print": """
        import json
        def main():
            print(json.dumps({"a": 1}, ensure_ascii=False))
    """,
    "A/json.dump->stdout": """
        import json, sys
        def main():
            json.dump({"a": 1}, sys.stdout, ensure_ascii=False)
    """,
    "B/assign-then-print": """
        import json
        def main():
            rendered = json.dumps({"a": 1}, ensure_ascii=False)
            print(rendered)
    """,
    "C/return-then-print": """
        import json
        def _line(payload):
            return json.dumps(payload, ensure_ascii=False)
        def main():
            print(_line({"a": 1}))
    """,
    # The measured spelling: the raw dump is bound to a local FIRST, so a rule
    # that reads only the return expression sees a bare Name and finds nothing.
    "C/return-then-print via a local": """
        import json
        def _line(payload):
            line = json.dumps(payload, ensure_ascii=False)
            if not line:
                return "{}"
            return line
        def main():
            print(_line({"a": 1}))
    """,
    "E/sys.stdout.write": """
        import json, sys
        def main():
            sys.stdout.write(json.dumps({"a": 1}, ensure_ascii=False) + chr(10))
    """,
    # The measured spelling in this tree: bootstrap_names.py builds the payload
    # first and writes it, so a rule that only reads the write() argument
    # expression sees a bare Name.
    "E/sys.stdout.write via a local": """
        import json, sys
        def main():
            rendered = json.dumps({"a": 1}, ensure_ascii=False)
            sys.stdout.write(rendered + chr(10))
    """,
    "D/raise-then-print": """
        import json
        class Fatal(Exception):
            pass
        def fatal(msg):
            raise Fatal(json.dumps({"error": msg}, ensure_ascii=False))
        def main():
            try:
                fatal("x")
            except Fatal as exc:
                print(str(exc))
    """,
}


def test_gate_flags_every_shape_it_advertises():
    """Red-before-green, kept permanently. Each synthetic module below is the
    minimal spelling of one shape; the gate must flag it AND label it with that
    shape, so a rule silently narrowed to nothing cannot pass by finding the
    site under some other rule."""
    for shape, body in _SHAPES.items():
        found = raw_stdout_sites(textwrap.dedent(body), f"<{shape}>")
        assert found, f"the gate did not flag shape {shape} -- it detects nothing there"
        expected = shape.split(" via ")[0]
        assert any(s == expected for _, s in found), (
            f"shape {expected} was flagged as {[s for _, s in found]} instead -- the "
            "rule that is supposed to catch it is not the one that fired"
        )


_MUST_NOT_FLAG = {
    "stderr-only": """
        import json, sys
        def main():
            print(json.dumps({"a": 1}, ensure_ascii=False), file=sys.stderr)
    """,
    "canonical-form-for-hashing": """
        import hashlib, json
        def digest(obj):
            return hashlib.sha256(
                json.dumps(obj, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
            ).hexdigest()
    """,
    "durable-file-write": """
        import json
        def write(path, doc):
            path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    """,
    "caught-and-reserialised": """
        import json
        class Err(Exception):
            pass
        def inner():
            raise Err("plain message")
        def main():
            try:
                inner()
            except Err as exc:
                print(json_stdout_dumps_line({"error": str(exc)}))
    """,
    "ascii-escaped-is-immune": """
        import json
        def main():
            print(json.dumps({"a": 1}))
    """,
    # A `.stdout` that is NOT sys's -- some object of this program's own that
    # happens to carry a writable stream under that name. Nothing an agent
    # reads by line is on the other end of it, and shape E used to flag it
    # because it matched on the attribute name alone.
    "another-objects-stdout-write": """
        import json, io
        class Report:
            def __init__(self):
                self.stdout = io.StringIO()
        def main():
            report = Report()
            report.stdout.write(json.dumps({"a": 1}, ensure_ascii=False))
    """,
}


def test_gate_does_not_flag_what_is_deliberately_out_of_scope():
    """The other half of a gate's honesty. Escaping a canonical form would change
    every hash it feeds and mass-invalidate converged work, so these shapes are
    non-goals rather than oversights -- and a gate that grew to flag them would
    be pressure to "fix" exactly the thing that must not be touched. The
    `ascii-escaped` case pins the true statement that `ensure_ascii=True`
    (`select_segments.py`'s FatalError) is already immune."""
    for label, body in _MUST_NOT_FLAG.items():
        found = raw_stdout_sites(textwrap.dedent(body), f"<{label}>")
        assert not found, f"the gate over-detects: it flagged the {label} case at {found}"
