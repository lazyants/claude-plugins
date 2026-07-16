"""tests/senses_fixture_guard.test.py -- the drift guard for RFC #215's
canon_senses.py fixture-staging discipline (plan §"Isolated-fixture harness
update", R5-F4/R6-F1/R7-F1/R8-F2; contract §9).

The invariant: a suite that isolates a canon_senses CONSUMER script
(canon_validate.py / glossary_batch_plan.py / canon_adjudication_audit.py --
every real script that does ``from canon_senses import ...``) via a raw
``shutil.copy2``/``copy``/``copytree`` (file-staging into a ``tmp_path``
fixture) or ``SourceFileLoader``/``spec_from_file_location`` (an in-process
load) MUST also make ``canon_senses`` importable at that call, or
``ModuleNotFoundError`` fires before any of the suite's own assertions run.
Fixing this suite-by-suite invites exactly the drift this guard exists to
catch, so every NEW such isolation should route through
``tests/_senses_fixture.py``'s ``stage_consumer()`` (file-staging) or its
documented ``sys.path.insert(SCRIPTS_SRC_DIR)`` idiom (in-process, loading
from the consumer's own real, non-copied location).

Detection is deliberately FILE-LOCAL (no cross-file dataflow, per the plan's
own "closes the cross-file/wrapper hole WITHOUT over-promising dataflow
analysis" framing): for each real test file, in isolation --

  1. Does the file's text mention any consumer script's filename anywhere
     (``_touches_consumer``)? If not, this file has nothing to do with
     canon_senses staging at all, regardless of what idioms it happens to
     use for entirely unrelated scripts (e.g. bootstrap_names.py,
     evidence_verify.py) -- skip it. This is what keeps the guard from
     false-flagging every OTHER suite in the plugin that stages some
     unrelated script the same way.
  2. If it DOES touch a consumer, does it use the copy idiom? If so, it
     must ALSO be "handled": either it calls ``stage_consumer(`` directly,
     or it separately stages ``canon_senses.py`` itself (a manually-correct
     dual-copy is still safe, just not the preferred style -- flagged
     nowhere further, since the actual regression this guard exists to
     prevent -- ``ModuleNotFoundError`` -- genuinely cannot occur if
     canon_senses.py is right there in the same isolated tree).
  3. Does it use the loader idiom (in-process)? It must ALSO be "handled":
     either ``sys.path.insert(`` appears anywhere in the file (the
     established idiom for a sibling import to resolve against a script's
     own real directory -- this is what makes ``dependency_preflight.
     test.py``'s pre-fix ``_load_module_fresh`` (NO sys.path handling at
     all) the one real, catchable bug shape), or it calls
     ``stage_consumer(``.

Because check 1 gates on a WHOLE-FILE literal scan rather than "does the
literal sit inside the SAME call/statement as the idiom", this also catches
the exact `dependency_preflight.test.py` shape the R6-F1 finding named: a
generic helper (``_load_module_fresh(path, name)``, no literal in its own
call to ``SourceFileLoader`` at all) whose only consumer reference lives at
a DIFFERENT call site in the same file (``_load_module_fresh(
CANON_VALIDATE_PATH, ...)``) -- a naive same-statement literal check misses
this; the whole-file scan does not, because it never needed the literal to
be co-located with the idiom in the first place. A genuinely physically
cross-FILE split (the idiom in one file, the consumer literal only ever
supplied from a wholly separate file) is the one shape file-local detection
cannot see by construction -- an accepted, documented limitation, not a
silent gap.

Two mutation tests (below) prove the guard has teeth: (a) a raw
``spec_from_file_location`` naming a consumer directly, with no staging,
must be flagged; (b) a from-scratch reconstruction of the real
`dependency_preflight.test.py` shape (generic helper + a separate call site
supplying the consumer reference, no sys.path handling anywhere) must also
be flagged -- proving detection does not depend on the literal and the
idiom sharing one call/statement.

KNOWN GUARD-PRECISION GAP (codex-round finding, accepted -- see the third,
``xfail``-marked mutation test below): the loader "handled" check accepts
ANY ``sys.path.insert(`` call ANYWHERE in the file, without verifying the
inserted path is actually the CONSUMER's own scripts directory. A file that
inserts only ``TESTS_DIR`` (e.g. to import ``_senses_fixture`` itself) and
then spec-loads a consumer from its real scripts/ location WITHOUT ALSO
inserting that directory would be wrongly judged "handled". Refining the
needle to require the inserted path expression to look scripts-dir-shaped
is not safely doable file-locally: every legitimate in-process caller in
this plugin (``sense_translated_behaviour.test.py``, ``canon_map_delivery.
test.py``, ``occ_index.test.py``, ``evidence_verify.test.py``,
``canon_validate_recollapse.test.py``'s Section 1) names that parameter/
variable something generic like ``extra_sys_path`` at the ``sys.path.
insert`` call site itself -- the real scripts-dir constant (``SCRIPTS_DIR``
or similar) is bound one hop away, at the CALLER's own argument, which is
exactly the kind of cross-statement resolution this guard's file-local,
no-dataflow design deliberately does not attempt (requiring "scripts" to
appear at the call site would false-flag every one of those already-correct
files). Accepted as a guard-PRECISION gap, not a correctness gap: the miss
fails LOUDLY -- the affected test itself errors with ``ModuleNotFoundError``
the instant it runs, so a real regression of this shape can never land
silently; only this guard's OWN ability to preemptively name the file is
reduced.
"""
import re
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

import sys  # noqa: E402

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _senses_fixture import AUTHORITATIVE_FIXTURE_INVENTORY  # noqa: E402

# Every real script that itself does `from canon_senses import ...` /
# `import canon_senses` -- i.e. a script whose isolation (copy or in-process
# load) genuinely needs canon_senses staged/resolvable alongside it.
# evidence_verify.py is deliberately NOT here: it receives already-loaded
# senses from its caller and never imports canon_senses itself (contract §5).
CONSUMER_SCRIPTS = (
    "canon_validate.py",
    "glossary_batch_plan.py",
    "canon_adjudication_audit.py",
)

# This guard's own infrastructure files legitimately contain the very
# idiom/consumer-name text the checks below look for -- _senses_fixture.py
# IS the sanctioned helper (its body must call shutil.copy2 on a consumer),
# and this file's own mutation-test fixtures below are synthetic SOURCE TEXT
# containing that same text as string literals, not real isolation code.
GUARD_INFRASTRUCTURE_FILES = frozenset({"_senses_fixture.py", Path(__file__).name})

COPY_IDIOM_RE = re.compile(r"shutil\.copy(?:2|tree)?\s*\(")
LOADER_IDIOM_RE = re.compile(r"\b(?:SourceFileLoader|spec_from_file_location)\s*\(")
SYS_PATH_INSERT_RE = re.compile(r"sys\.path\.insert\s*\(")
STAGE_CONSUMER_CALL_RE = re.compile(r"\bstage_consumer\s*\(")

# Triple-quoted string literals (module/function docstrings -- and this
# guard's OWN embedded synthetic-fixture/fake-script strings) and `#`-line
# comments are stripped before scanning, so a docstring/comment merely
# DISCUSSING a consumer script by name (e.g. "canon_validate.py's recollapse
# guard imports load_senses from here") is never mistaken for actual
# isolation code -- regex-based, not a real tokenizer; file-local
# good-enough, the same tolerance as the rest of this guard.
TRIPLE_QUOTED_RE = re.compile(r'("""|\'\'\')(?:(?!\1).)*?\1', re.DOTALL)
LINE_COMMENT_RE = re.compile(r"#[^\n]*")


def _code_only(text: str) -> str:
    text = TRIPLE_QUOTED_RE.sub("", text)
    text = LINE_COMMENT_RE.sub("", text)
    return text


def _touches_consumer(code: str) -> bool:
    return any(name in code for name in CONSUMER_SCRIPTS)


def find_violations(text: str) -> list[str]:
    """Returns the list of unhandled idiom categories ("copy", "loader")
    found in `text` -- empty if `text` doesn't touch a consumer at all, or
    touches one but every idiom it uses is handled."""
    code = _code_only(text)
    if not _touches_consumer(code):
        return []

    violations = []
    if COPY_IDIOM_RE.search(code):
        handled = STAGE_CONSUMER_CALL_RE.search(code) or "canon_senses.py" in code
        if not handled:
            violations.append("copy")
    if LOADER_IDIOM_RE.search(code):
        handled = SYS_PATH_INSERT_RE.search(code) or STAGE_CONSUMER_CALL_RE.search(code)
        if not handled:
            violations.append("loader")
    return violations


def scan_directory(directory: Path, *, inventoried_files: set) -> dict:
    """Scans every `*.py` file directly under `directory` (non-recursive --
    matches this plugin's flat tests/ layout) and returns
    {filename: [violation categories]} for every file with an unhandled
    consumer-isolation idiom, skipping GUARD_INFRASTRUCTURE_FILES and any
    name in `inventoried_files`."""
    violations = {}
    for path in sorted(directory.glob("*.py")):
        if path.name in GUARD_INFRASTRUCTURE_FILES or path.name in inventoried_files:
            continue
        found = find_violations(path.read_text(encoding="utf-8"))
        if found:
            violations[path.name] = found
    return violations


def _inventoried_files() -> set:
    return {entry["file"] for entry in AUTHORITATIVE_FIXTURE_INVENTORY}


# ===========================================================================
# The guard itself
# ===========================================================================


def test_no_unsanctioned_consumer_isolation_outside_helper_or_inventory():
    violations = scan_directory(TESTS_DIR, inventoried_files=_inventoried_files())
    assert not violations, (
        "the following test files isolate a canon_senses consumer script "
        "(canon_validate.py / glossary_batch_plan.py / "
        "canon_adjudication_audit.py) via a raw shutil.copy*/SourceFileLoader/"
        "spec_from_file_location without staging canon_senses alongside it -- "
        "route through tests/_senses_fixture.py's stage_consumer() (file-"
        "staging) or its documented sys.path.insert(SCRIPTS_SRC_DIR) idiom "
        "(in-process load from the consumer's own real location), or add a "
        "reviewed tests/_senses_fixture.py::AUTHORITATIVE_FIXTURE_INVENTORY "
        f"entry:\n{violations}"
    )


# ===========================================================================
# Mutation tests -- prove the guard has teeth (contract §9 (a)/(b))
# ===========================================================================


def test_mutation_raw_loader_naming_consumer_directly_is_flagged(tmp_path):
    """(a) A raw `spec_from_file_location(...)` call whose OWN argument
    literally names a consumer script, with no sys.path handling and no
    stage_consumer() call anywhere in the file, must be flagged."""
    sneaky = tmp_path / "sneaky_isolation.py"
    sneaky.write_text(
        'import importlib.util\n'
        'spec = importlib.util.spec_from_file_location('
        '"x", "/some/path/canon_validate.py")\n',
        encoding="utf-8",
    )

    violations = scan_directory(tmp_path, inventoried_files=set())

    assert violations == {"sneaky_isolation.py": ["loader"]}


def test_mutation_generic_helper_with_separate_consumer_reference_is_flagged(tmp_path):
    """(b) Reconstructs the REAL bug shape this guard exists to catch --
    tests/dependency_preflight.test.py's pre-fix `_load_module_fresh(path,
    name)`: a generic loader whose OWN call to SourceFileLoader carries no
    literal at all (both arguments are parameters), with the actual
    consumer reference supplied at a SEPARATE call site
    (`_load_module_fresh(CANON_VALIDATE_PATH, ...)`) elsewhere in the file,
    and no sys.path handling anywhere. A check that only looked for the
    idiom and the literal inside the SAME call/statement would miss this --
    the whole-file scan here does not, because `_touches_consumer` never
    required the literal to be co-located with the idiom."""
    wrapper = tmp_path / "generic_wrapper_isolation.py"
    wrapper.write_text(
        'import importlib.machinery\n'
        'import importlib.util\n'
        '\n'
        'CANON_VALIDATE_PATH = "/some/path/canon_validate.py"\n'
        '\n'
        'def _load_module_fresh(path, name):\n'
        '    loader = importlib.machinery.SourceFileLoader(name, str(path))\n'
        '    spec = importlib.util.spec_from_loader(loader.name, loader)\n'
        '    module = importlib.util.module_from_spec(spec)\n'
        '    loader.exec_module(module)\n'
        '    return module\n'
        '\n'
        'def do_it():\n'
        '    return _load_module_fresh(CANON_VALIDATE_PATH, "fresh")\n',
        encoding="utf-8",
    )

    violations = scan_directory(tmp_path, inventoried_files=set())

    assert violations == {"generic_wrapper_isolation.py": ["loader"]}


def test_mutation_handled_variants_are_not_flagged(tmp_path):
    """Control case: the same two shapes as above, but correctly handled --
    a copy that also stages canon_senses.py, and a loader that also
    sys.path.inserts -- must NOT be flagged. Without this, a guard that
    always flagged any consumer mention would still pass the two mutation
    tests above."""
    handled_copy = tmp_path / "handled_copy.py"
    handled_copy.write_text(
        'import shutil\n'
        'shutil.copy2(SRC, scripts_dir / "canon_validate.py")\n'
        'shutil.copy2(SENSES_SRC, scripts_dir / "canon_senses.py")\n',
        encoding="utf-8",
    )
    handled_loader = tmp_path / "handled_loader.py"
    handled_loader.write_text(
        'import sys\n'
        'sys.path.insert(0, str(SCRIPTS_DIR))\n'
        'spec = importlib.util.spec_from_file_location(name, path)\n'
        '# CANON_VALIDATE_PATH = ".../canon_validate.py" referenced elsewhere\n',
        encoding="utf-8",
    )

    violations = scan_directory(tmp_path, inventoried_files=set())

    assert violations == {}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known guard-precision gap (codex round, accepted -- see module "
        "docstring): the loader 'handled' check accepts ANY sys.path.insert("
        "...) anywhere in the file, without confirming the inserted path is "
        "the CONSUMER's own scripts directory. If this assertion starts "
        "PASSING, the guard has gotten stricter than documented -- update "
        "the module docstring's limitation note and remove this marker."
    ),
)
def test_mutation_tests_dir_only_insert_is_not_enough_to_handle_a_real_scripts_load(tmp_path):
    """Demonstrates the documented gap: `sys.path.insert(0, str(TESTS_DIR))`
    (present only to import `_senses_fixture` itself) is NOT the same thing
    as inserting the consumer's own scripts directory, but the current
    needle can't tell the difference and reports this file as handled
    anyway. The desired (not-yet-true) behavior asserted here is that this
    SHOULD be flagged."""
    sneaky = tmp_path / "tests_dir_only_insert.py"
    sneaky.write_text(
        'import sys\n'
        'from pathlib import Path\n'
        'TESTS_DIR = Path(__file__).resolve().parent\n'
        'sys.path.insert(0, str(TESTS_DIR))\n'
        'from _senses_fixture import stage_consumer\n'
        'import importlib.util\n'
        'spec = importlib.util.spec_from_file_location('
        '"x", "/real/scripts/canon_validate.py")\n',
        encoding="utf-8",
    )

    violations = scan_directory(tmp_path, inventoried_files=set())

    assert violations == {"tests_dir_only_insert.py": ["loader"]}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
