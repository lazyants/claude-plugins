import ast
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent / "skills/literary-translator/assets/scripts"
# Not part of SCRIPTS_DIR's glob (different dir, .py.template extension) but
# copied verbatim to ${durable_root}/extract.py and executed as part of the
# pipeline (SKILL.md lines 240-241, 415-437) -- a documented single extra
# path, kept distinct from the glob-derived list below.
EXTRA_SCANNED_FILES = [
    Path(__file__).parent.parent
    / "skills/literary-translator/assets/templates/extract.py.template"
]

def _has_future_annotations(tree):
    return any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names)
        for n in tree.body
    )

def _bitor_linenos(node):
    # A `BinOp`/`BitOr` buried inside a `Call`'s arguments/keywords is a
    # runtime bitwise-OR of the call's argument expressions (e.g. the int
    # flag constants in `re.compile(pattern, re.DOTALL | re.IGNORECASE)`) --
    # NEVER PEP-604 type-alias syntax, because a type alias's right-hand
    # side is never wrapped in a function call. So don't descend into Call
    # arguments; still descend into `Call.func` (a nested attribute/
    # subscript chain there is rare but harmless to inspect) and into every
    # other node type as before, so a union genuinely nested inside a
    # `Subscript` slice (e.g. `Dict[str, A | B]`), which IS evaluated at
    # import time, stays caught. Accepted precision tradeoff (mirrors the
    # bare-`Assign` "Known limitation" below): a type union genuinely nested
    # in a Call argument -- e.g. `cast(list[A | B], v)` -- would go unflagged,
    # but no such case exists in this codebase; if one is added, add an
    # explicit exception rather than reinstating the blanket walk.
    linenos = []

    def visit(n):
        if isinstance(n, ast.Call):
            visit(n.func)
            return
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr):
            linenos.append(n.lineno)
        for child in ast.iter_child_nodes(n):
            visit(child)

    visit(node)
    return linenos

def _signature_annotations(fn):
    args = fn.args
    annos = [
        a.annotation
        for a in (args.posonlyargs + args.args + args.kwonlyargs)
        if a.annotation
    ]
    if args.vararg and args.vararg.annotation:
        annos.append(args.vararg.annotation)
    if args.kwarg and args.kwarg.annotation:
        annos.append(args.kwarg.annotation)
    if fn.returns:
        annos.append(fn.returns)
    return annos

def _signature_defaults(fn):
    # A default-value expression is evaluated when the `def` statement
    # itself executes (import time for a module/class-level def) --
    # entirely independent of `from __future__ import annotations`, which
    # only defers ANNOTATION evaluation to lazy strings. kw_defaults has one
    # entry per kwonlyarg and uses `None` (not omission) to mark "no
    # default given"; those must be filtered out, not treated as an actual
    # bare-None default.
    args = fn.args
    return list(args.defaults) + [d for d in args.kw_defaults if d is not None]

def _find_bare_pep604_unions(tree, skip_annotations):
    offenders = []

    # Function/method signatures at ANY nesting depth: a signature's
    # annotations are evaluated when its `def` statement executes -- for a
    # module- or class-level def that's import time; for a nested def it's
    # whenever the enclosing def is called. Both are real crash surfaces, so
    # both stay covered. Includes posonlyargs/vararg/kwarg, not just
    # args/kwonlyargs (a `def f(x: A | B, /)`, `def f(*x: A | B)`, or
    # `def f(**x: A | B)` must not evade the guard) -- UNLESS
    # `from __future__ import annotations` is active, which defers ALL
    # annotation evaluation (including signatures) to lazy strings; the
    # annotation check below is gated on skip_annotations accordingly.
    #
    # A default-value expression (`def f(x: object = A | B)` or even
    # `def f(x=A | B)` with no annotation at all) is a SEPARATE
    # runtime-evaluated expression the future import has zero effect on --
    # it's evaluated at the same def-statement-execution moment as a
    # module-level Assign, not deferred like an annotation -- so the
    # defaults check runs unconditionally, same reasoning as AnnAssign.value.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not skip_annotations:
                for anno in _signature_annotations(node):
                    offenders.extend(_bitor_linenos(anno))
            for default in _signature_defaults(node):
                offenders.extend(_bitor_linenos(default))

    # Module- and class-level variable assignments only -- both annotated
    # (AnnAssign, e.g. `x: A | B = val`) and plain (Assign, e.g.
    # `x = A | B`) -- including ones nested inside control-flow suites
    # (if/for/while/with/try/except/finally/match) that execute immediately
    # at the enclosing scope's execution time (import time at module level).
    # A plain `x = A | B` evaluates the BinOp the instant that statement
    # runs, same as an annotation's default value -- both are import-time
    # hazards. A function-BODY-local `x: A | B` is either never evaluated at
    # all (bare, no assignment -- a documented CPython no-op) or, if
    # assigned, only evaluated when that specific statement executes at CALL
    # time, not at import time -- neither is the import-time hazard this
    # guard targets, so FunctionDef/AsyncFunctionDef bodies are deliberately
    # NOT descended into here, even when reached through a control-flow
    # suite.
    #
    # An AnnAssign's `.annotation` (the `A | B` in `x: A | B = val`) is
    # deferred to a lazy string by `from __future__ import annotations`,
    # same as a signature annotation, so it's gated on skip_annotations. Its
    # `.value` (the `val`) is a wholly separate runtime-evaluated
    # expression the future import has zero effect on -- `x: object = A |
    # B` still evaluates `A | B` as a real BinOp at import time -- so the
    # value check runs unconditionally, same as a plain Assign.
    _STMT_LIST_FIELDS = {
        ast.If: ("body", "orelse"),
        ast.For: ("body", "orelse"),
        ast.AsyncFor: ("body", "orelse"),
        ast.While: ("body", "orelse"),
        ast.With: ("body",),
        ast.AsyncWith: ("body",),
    }
    # ast.Match/ast.TryStar were added in Python 3.10/3.11 -- referencing
    # ast.Match directly (as the original draft did) would raise
    # AttributeError on older interpreters, self-defeating for a guard whose
    # whole purpose is pre-3.10 safety. Resolve both defensively; () as
    # isinstance's second arg always matches nothing, degrading cleanly to
    # "no match statements possible" on interpreters where match syntax
    # itself couldn't exist anyway.
    _MATCH_TYPE = getattr(ast, "Match", ())
    _TRY_TYPES = (ast.Try,) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())

    def visit_body(stmts):
        for stmt in stmts:
            if isinstance(stmt, ast.AnnAssign):
                if not skip_annotations and stmt.annotation:
                    offenders.extend(_bitor_linenos(stmt.annotation))
                if stmt.value:
                    offenders.extend(_bitor_linenos(stmt.value))
            elif isinstance(stmt, ast.Assign) and stmt.value:
                # Known limitation: at the AST level, ast.BinOp+ast.BitOr
                # can't distinguish a type-alias `Name = SomeType |
                # OtherType` from a legitimate runtime bitwise-OR assignment
                # (e.g. `FLAGS = READ_FLAG | WRITE_FLAG` on plain ints) --
                # both parse identically. No such non-type case exists in
                # this codebase today (verified), so this is an accepted
                # precision tradeoff, not an active bug. There is currently
                # no escape hatch for a legitimate module/class-scope
                # bitwise-OR; if one is ever added and false-positives here,
                # the fix is to add an explicit exception, not to weaken
                # this check.
                offenders.extend(_bitor_linenos(stmt.value))
            elif isinstance(stmt, ast.ClassDef):
                visit_body(stmt.body)
            elif isinstance(stmt, _TRY_TYPES):
                visit_body(stmt.body)
                visit_body(stmt.orelse)
                visit_body(stmt.finalbody)
                for handler in stmt.handlers:
                    visit_body(handler.body)
            elif isinstance(stmt, _MATCH_TYPE):
                # `getattr` (not `stmt.cases` directly): _MATCH_TYPE is a
                # runtime-resolved fallback (`getattr(ast, "Match", ())`) for
                # pre-3.10 interpreters lacking `ast.Match`, so a type checker
                # can't narrow `stmt` to `ast.Match` via this isinstance and
                # flags `.cases` as unknown -- functionally a no-op here since
                # the branch only runs when `stmt` really is an `ast.Match`.
                for case in getattr(stmt, "cases", ()):
                    visit_body(case.body)
            elif type(stmt) in _STMT_LIST_FIELDS:
                for field in _STMT_LIST_FIELDS[type(stmt)]:
                    visit_body(getattr(stmt, field))
            # else: FunctionDef/AsyncFunctionDef (and any other statement
            # kind) is NOT descended into -- its body executes later, not now.

    visit_body(tree.body)
    return offenders

def test_no_unquoted_runtime_pep604_unions_without_future_import():
    scanned_paths = sorted(SCRIPTS_DIR.glob("*.py")) + EXTRA_SCANNED_FILES
    offenders = []
    for path in scanned_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # `from __future__ import annotations` only defers ANNOTATION
        # evaluation to lazy strings -- it has zero effect on runtime
        # VALUE expressions (a plain Assign.value or an AnnAssign.value),
        # so skip_annotations must NOT skip the whole file, only the
        # annotation-specific checks inside _find_bare_pep604_unions.
        skip_annotations = _has_future_annotations(tree)
        for lineno in _find_bare_pep604_unions(tree, skip_annotations):
            offenders.append(f"{path.name}:{lineno}")
    assert offenders == [], (
        f"unquoted runtime-evaluated PEP-604 unions in scripts/the extract "
        f"template: either an annotation lacking `from __future__ import "
        f"annotations` (fails import on Python <=3.9), or a module/class-"
        f"level runtime value (Assign/AnnAssign.value, which the future "
        f"import never protects): {offenders}"
    )

# Hand-constructed fixture exercising the widened ast.Assign recursion
# directly (via ast.parse on a string, not a file on disk). The real
# scripts directory has zero offenders today, so the sweep test above can
# never prove the widened recursion (the Assign branch itself, plus the
# If/For/Try nesting it inherited from the AnnAssign handling) actually
# fires -- only this fixture can. Line numbers below are asserted exactly;
# keep them in sync if the fixture text changes.
FIXTURE_SOURCE = """\
Alias = str | None

X = 1 + 2

if True:
    IfAlias = str | None

for _ in range(1):
    ForAlias = str | None

try:
    TryAlias = str | None
except Exception:
    pass

class Foo:
    ClassAlias = str | None

AnnValueAlias: object = str | None

def func():
    LocalAlias = str | None
"""

def test_find_bare_pep604_unions_fixture_covers_widened_assign_recursion():
    tree = ast.parse(FIXTURE_SOURCE, filename="<fixture>")
    offenders = sorted(_find_bare_pep604_unions(tree, skip_annotations=False))
    assert offenders == [1, 6, 9, 12, 17, 19], (
        "widened ast.Assign recursion must flag every bare `Alias = A | B` "
        "at module scope (line 1), class scope (line 17), and nested "
        "inside if/for/try (lines 6/9/12); AnnAssign's VALUE (line 19, "
        "`AnnValueAlias: object = str | None`) must also be flagged "
        "despite its clean `object` annotation, since the value is a "
        "separate runtime-evaluated BinOp; a non-union assign (`X = 1 + "
        "2`, line 3) and a function-body-local assign (line 22 -- "
        "evaluated at call time, not import time) must stay unflagged; "
        f"got {offenders}"
    )

# Gap-2 fixture: `from __future__ import annotations` defers ANNOTATION
# evaluation to lazy strings, but has zero effect on runtime VALUE
# expressions -- a plain Assign.value or an AnnAssign.value still
# evaluates the BinOp at import time regardless of the future import.
# Line numbers below are asserted exactly; keep them in sync if the
# fixture text changes.
FUTURE_IMPORT_FIXTURE_SOURCE = """\
from __future__ import annotations

def f(x: str | None) -> None:
    pass

y: str | None

Alias = str | None

class Foo:
    ClassAlias: object = str | None
"""

def test_find_bare_pep604_unions_future_import_still_flags_runtime_values():
    tree = ast.parse(FUTURE_IMPORT_FIXTURE_SOURCE, filename="<fixture>")
    skip_annotations = _has_future_annotations(tree)
    assert skip_annotations is True
    offenders = sorted(_find_bare_pep604_unions(tree, skip_annotations))
    assert offenders == [8, 11], (
        "from __future__ import annotations defers ANNOTATION evaluation "
        "only: a function-signature union (line 3, `def f(x: str | "
        "None)`) and a bare-annotation union with no value (line 6, `y: "
        "str | None`) must stay unflagged as negative controls. A plain "
        "runtime Assign (line 8, `Alias = str | None`) and an AnnAssign's "
        "runtime VALUE (line 11, `ClassAlias: object = str | None`, "
        "despite its clean `object` annotation) must still be flagged, "
        "since both evaluate the BinOp at import time regardless of the "
        f"future import; got {offenders}"
    )

# Gap-3 fixture: a function-signature DEFAULT VALUE expression (as opposed
# to an annotation) is evaluated when the `def` statement itself executes
# (import time for a module-level def) -- a wholly separate runtime
# expression from the annotation, and never deferred by `from __future__
# import annotations`. Covers both a positional default and a
# keyword-only default (kw_defaults has a `None` placeholder entry for
# keyword-only args with no default -- must not be misread as a bare-None
# default). Line numbers below are asserted exactly; keep them in sync if
# the fixture text changes.
DEFAULT_VALUE_FIXTURE_SOURCE = """\
def f(x=str | None):
    pass

def g(x=5):
    pass

def h(*, y=str | None):
    pass

def i(*, z):
    pass
"""

def test_find_bare_pep604_unions_fixture_covers_function_default_values():
    tree = ast.parse(DEFAULT_VALUE_FIXTURE_SOURCE, filename="<fixture>")
    offenders = sorted(_find_bare_pep604_unions(tree, skip_annotations=False))
    assert offenders == [1, 7], (
        "function DEFAULT VALUE expressions are evaluated at "
        "def-statement execution time (import time for a module-level "
        "def), independent of annotations -- a bare positional default "
        "union (line 1, `def f(x=str | None)`) and a bare keyword-only "
        "default union (line 7, `def h(*, y=str | None)`) must both be "
        "flagged; a non-union positional default (line 4, `def g(x=5)`) "
        "and a keyword-only arg with no default at all (line 10, `def "
        "i(*, z)` -- a `None` placeholder entry in kw_defaults) must stay "
        f"unflagged; got {offenders}"
    )

DEFAULT_VALUE_FUTURE_IMPORT_FIXTURE_SOURCE = """\
from __future__ import annotations

def f(x=str | None):
    pass

def g(x=5):
    pass

def h(*, y=str | None):
    pass

def i(*, z):
    pass
"""

def test_find_bare_pep604_unions_default_values_unaffected_by_future_import():
    tree = ast.parse(DEFAULT_VALUE_FUTURE_IMPORT_FIXTURE_SOURCE, filename="<fixture>")
    skip_annotations = _has_future_annotations(tree)
    assert skip_annotations is True
    offenders = sorted(_find_bare_pep604_unions(tree, skip_annotations))
    assert offenders == [3, 9], (
        "from __future__ import annotations defers ANNOTATION evaluation "
        "only -- it has zero effect on default VALUE expressions, which "
        "are evaluated at def-statement execution time regardless. A "
        "positional default union (line 3, `def f(x=str | None)`) and a "
        "keyword-only default union (line 9, `def h(*, y=str | None)`) "
        "must still be flagged even with the future import present; a "
        "non-union default (line 6, `def g(x=5)`) and a no-default "
        "keyword-only arg (line 12, `def i(*, z)`) stay unflagged; got "
        f"{offenders}"
    )

# Call-argument fixture: a runtime bitwise-OR of two module int flag
# constants passed as a call argument (e.g. `re.compile(p, re.DOTALL |
# re.IGNORECASE)`) is NEVER PEP-604 type-alias syntax -- a type alias's RHS
# is never wrapped in a function call -- so the BitOr buried in a Call's
# arguments must not be flagged. A union genuinely nested in a runtime
# subscript at module scope (e.g. `Dict[str, "Foo" | None]`, whose BinOp
# IS evaluated at import time) must still be flagged, proving the Call-arg
# exclusion doesn't regress subscript coverage. Line numbers below are
# asserted exactly; keep them in sync if the fixture text changes.
CALL_ARG_FIXTURE_SOURCE = """\
import re
from typing import Dict

PATTERN = re.compile(r"<!--.*?-->", re.DOTALL | re.IGNORECASE)

Aliased: object = Dict[str, "Foo" | None]
"""

def test_find_bare_pep604_unions_ignores_bitor_inside_call_arguments():
    tree = ast.parse(CALL_ARG_FIXTURE_SOURCE, filename="<fixture>")
    offenders = sorted(_find_bare_pep604_unions(tree, skip_annotations=False))
    assert offenders == [6], (
        "a bitwise-OR of int flag constants passed as a CALL ARGUMENT (line "
        "4, `re.compile(..., re.DOTALL | re.IGNORECASE)`) is a runtime "
        "flag OR, never a PEP-604 type union -- a type alias's RHS is never "
        "wrapped in a Call -- so it must NOT be flagged; a union genuinely "
        "nested inside a runtime Subscript (line 6, `Dict[str, \"Foo\" | "
        "None]`, whose BinOp IS evaluated at import time) must STILL be "
        f"flagged so the Call-arg exclusion doesn't regress it; got {offenders}"
    )
