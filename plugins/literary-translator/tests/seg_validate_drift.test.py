"""tests/seg_validate_drift.test.py -- source-identity drift guard for the
duplicated seg-id safety helper (issue #63).

This plugin's "no shared lib between self-contained scripts" convention
(see tests/seg_safety_source_and_workflow.test.py, tests/
seg_safety_segpack.test.py) means the path/shell-safety contract for a
segment id -- `_SEG_ID_RE` + `validate_seg()` -- is hand-copied into TEN
scripts. The seg_safety_*.test.py suite proves each copy accepts/rejects
the right *values*, but nothing asserts the copies' *source text* stays in
sync -- a silent hand-edit to one copy (e.g. narrowing/widening the
allowlist regex in cache_key.py without mirroring the change everywhere
else) would pass every existing behavioral test for the nine UNTOUCHED
scripts while quietly diverging. This file closes that gap.

The 10 scripts (all under skills/literary-translator/assets/scripts/):
cache_key.py, draft_ready.py, draft_sha1.py, ledger_update.py,
validate_draft.py, select_segments.py, segpack.py, review_artifact_check.py,
review_ready.py, codex_job.py. review_ready.py (1.2.0) was a PRE-EXISTING
omission -- it carries the copy but `ALL_SCRIPTS` used to miss it; codex_job.py
(1.4.7, #198) is the new W5 dispatch driver.

Reality is NOT "all 10 byte-identical" -- three groups (verified by reading
every copy):

  1. `_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")` -- the
     load-bearing, path/shell-safety-critical allowlist literal -- IS
     byte-identical across all 10. This is the highest-value invariant to
     guard: it is the actual security boundary (see
     tests/seg_safety_source_and_workflow.test.py's module docstring for
     the vulnerability this regex closes), and it should never need a
     legitimate per-script variant.
  2. `validate_seg()`'s function body is byte-identical across EIGHT scripts
     (cache_key.py, draft_ready.py, draft_sha1.py, ledger_update.py,
     validate_draft.py, segpack.py, review_ready.py, codex_job.py).
  3. `select_segments.py`'s `validate_seg()` differs from that group of eight
     by a DOCSTRING LINE-WRAP ONLY -- the same words, rewrapped across
     lines differently. Its executable logic is identical. Normalizing
     docstring whitespace (collapsing all runs of whitespace to a single
     space) before comparing folds it into the canonical group, alongside
     an AST-level comparison of the non-docstring statements (so the
     regression this guards against is "the code changed", not "someone
     reflowed a comment").
  4. `review_artifact_check.py`'s `validate_seg()` is INTENTIONALLY
     divergent -- it upgrades from the historical denylist wording to extra
     denylist-style branches ahead of the allowlist fallback, kept
     deliberately for backward-compatible error messages. This divergence
     is documented and locked by its own dedicated regression test,
     tests/seg_safety_source_and_workflow.test.py::
     test_review_artifact_check_denylist_to_allowlist_upgrade -- so it is
     explicitly EXEMPTED from the function-body drift check here (checking
     it here would just fight that other, more precise test).

Extraction uses the `ast` module to locate the `_SEG_ID_RE` assignment and
the `validate_seg` function definition by name in each script, rather than
hardcoding line numbers (which drift as scripts are edited elsewhere).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

# All 10 scripts carrying a copy of the seg-id safety contract.
ALL_SCRIPTS = [
    "cache_key.py",
    "draft_ready.py",
    "draft_sha1.py",
    "ledger_update.py",
    "validate_draft.py",
    "select_segments.py",
    "segpack.py",
    "review_artifact_check.py",
    "review_ready.py",
    "codex_job.py",
]

# The canonical copy the function-body check compares everything else
# against. Arbitrary choice among the byte-identical group of six.
CANONICAL_SCRIPT = "cache_key.py"

# review_artifact_check.py's validate_seg() is a documented, deliberate
# divergence (denylist-wording upgrade), locked by its own test -- see the
# module docstring above. Exempt it from the function-body drift check.
FUNCTION_BODY_EXEMPT = {"review_artifact_check.py"}

for _name in ALL_SCRIPTS:
    assert (SCRIPTS_DIR / _name).is_file(), f"expected a real script at {SCRIPTS_DIR / _name}"


# ---------------------------------------------------------------------------
# AST-based extraction helpers -- locate by name, not by line number.
# ---------------------------------------------------------------------------


def _parse(script_name: str) -> tuple[str, ast.Module]:
    source = (SCRIPTS_DIR / script_name).read_text(encoding="utf-8")
    return source, ast.parse(source, filename=script_name)


def _seg_id_re_assign_source(script_name: str) -> str:
    """Returns the exact source text of the `_SEG_ID_RE = re.compile(...)`
    assignment statement in `script_name`, located by target name (not line
    number)."""
    source, tree = _parse(script_name)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_SEG_ID_RE"
        ):
            segment = ast.get_source_segment(source, node)
            assert segment is not None, f"could not slice _SEG_ID_RE source from {script_name}"
            return segment
    raise AssertionError(f"no `_SEG_ID_RE = ...` assignment found in {script_name}")


def _validate_seg_function_node(script_name: str) -> ast.FunctionDef:
    _, tree = _parse(script_name)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "validate_seg":
            return node
    raise AssertionError(f"no `def validate_seg(...)` found in {script_name}")


def _normalized_docstring(func_node: ast.FunctionDef) -> str:
    """The docstring's textual content with all whitespace runs (including
    the newlines a line-rewrap introduces) collapsed to single spaces --
    so a cosmetic rewrap compares equal, while an actual wording change
    does not."""
    doc = ast.get_docstring(func_node, clean=True)
    assert doc is not None, "validate_seg() must have a docstring"
    return " ".join(doc.split())


def _executable_body_dump(func_node: ast.FunctionDef) -> str:
    """An AST dump of validate_seg()'s statements, EXCLUDING the leading
    docstring Expr statement -- i.e. the actual executable logic, immune to
    docstring rewrapping (that's covered separately by
    `_normalized_docstring`) and to comment/whitespace changes (AST doesn't
    retain those at all)."""
    body = func_node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    assert body, "validate_seg() has no executable statements beyond its docstring"
    return "\n".join(ast.dump(stmt) for stmt in body)


# ---------------------------------------------------------------------------
# 1. Universal invariant, NO exemption: the allowlist regex literal itself.
#    This is the security-critical bit -- every one of the 8 scripts must
#    carry the exact same `_SEG_ID_RE` pattern.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script_name", ALL_SCRIPTS)
def test_seg_id_re_assignment_matches_canonical(script_name):
    canonical = _seg_id_re_assign_source(CANONICAL_SCRIPT)
    actual = _seg_id_re_assign_source(script_name)
    assert actual == canonical, (
        f"{script_name}'s `_SEG_ID_RE` assignment has drifted from "
        f"{CANONICAL_SCRIPT}'s (the path/shell-safety-critical allowlist "
        "literal must be byte-identical across every consuming script):\n"
        f"  {CANONICAL_SCRIPT}: {canonical!r}\n"
        f"  {script_name}: {actual!r}"
    )


def test_seg_id_re_pattern_is_the_expected_allowlist():
    """Pins the canonical literal itself, so a drift that moved in lockstep
    across all 8 scripts (passing the test above) still gets caught."""
    canonical = _seg_id_re_assign_source(CANONICAL_SCRIPT)
    assert canonical == '_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")', (
        f"canonical `_SEG_ID_RE` pattern has changed: {canonical!r}"
    )


# ---------------------------------------------------------------------------
# 2. Function-body invariant, WITH the documented review_artifact_check.py
#    exemption: validate_seg()'s executable logic + (whitespace-normalized)
#    docstring content.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script_name",
    [name for name in ALL_SCRIPTS if name not in FUNCTION_BODY_EXEMPT],
)
def test_validate_seg_body_matches_canonical(script_name):
    canonical_node = _validate_seg_function_node(CANONICAL_SCRIPT)
    actual_node = _validate_seg_function_node(script_name)

    canonical_body = _executable_body_dump(canonical_node)
    actual_body = _executable_body_dump(actual_node)
    assert actual_body == canonical_body, (
        f"{script_name}'s `validate_seg()` executable logic has drifted from "
        f"{CANONICAL_SCRIPT}'s (this is NOT the documented "
        "review_artifact_check.py divergence -- that script is exempted "
        "separately)."
    )

    canonical_doc = _normalized_docstring(canonical_node)
    actual_doc = _normalized_docstring(actual_node)
    assert actual_doc == canonical_doc, (
        f"{script_name}'s `validate_seg()` docstring content has drifted "
        f"from {CANONICAL_SCRIPT}'s beyond mere line-rewrap (whitespace-"
        "normalized comparison still differs):\n"
        f"  {CANONICAL_SCRIPT}: {canonical_doc!r}\n"
        f"  {script_name}: {actual_doc!r}"
    )


def test_review_artifact_check_is_the_only_function_body_exemption():
    """Guards the exemption set itself: if a future edit adds a script to
    FUNCTION_BODY_EXEMPT without updating this test (or removes
    review_artifact_check.py's documented divergence), that's worth
    noticing explicitly rather than silently narrowing coverage."""
    assert FUNCTION_BODY_EXEMPT == {"review_artifact_check.py"}
    # The divergence must still actually exist -- if a future edit makes
    # review_artifact_check.py's validate_seg() converge with the canonical
    # group, the exemption is stale and should be dropped (not left as
    # unnecessary permissiveness).
    canonical_node = _validate_seg_function_node(CANONICAL_SCRIPT)
    review_node = _validate_seg_function_node("review_artifact_check.py")
    canonical_body = _executable_body_dump(canonical_node)
    review_body = _executable_body_dump(review_node)
    assert review_body != canonical_body, (
        "review_artifact_check.py's validate_seg() now matches the canonical "
        "body exactly -- the FUNCTION_BODY_EXEMPT exemption is stale and "
        "should be removed so this script is covered by the strict check too."
    )


# ---------------------------------------------------------------------------
# 3. codex_job.py must be a REAL cache_key.py PLUGIN_BUNDLE_MEMBERS entry
#    (PLAN #198 §3), not merely listed in this test's ALL_SCRIPTS. The two are
#    DISTINCT lists: ALL_SCRIPTS enumerates the scripts carrying the seg-id
#    SAFETY copy; PLUGIN_BUNDLE_MEMBERS is the gating-hash membership tuple in
#    cache_key.py (which includes non-copy-carriers like canon_validate.py /
#    resume_setup.py + the two workflow templates, and EXCLUDES copy-carriers
#    like select_segments.py). A named assertion here -- parsed from
#    cache_key.py SOURCE, never a doc/test-list echo that could drift in
#    lockstep -- catches the failure mode where codex_job.py is added to
#    ALL_SCRIPTS but never registered as a bundle member, so an edit to the W5
#    dispatch driver would silently NOT flip plugin_bundle_hash.
# ---------------------------------------------------------------------------


def _plugin_bundle_members() -> list[str]:
    """The string entries of cache_key.py's `PLUGIN_BUNDLE_MEMBERS` tuple,
    extracted from source via `ast` (never imported -- located by target name,
    not line number)."""
    source, tree = _parse("cache_key.py")
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "PLUGIN_BUNDLE_MEMBERS"
        ):
            value = node.value
            assert isinstance(value, (ast.Tuple, ast.List)), (
                "cache_key.py PLUGIN_BUNDLE_MEMBERS must be a tuple/list literal"
            )
            members = []
            for elt in value.elts:
                assert isinstance(elt, ast.Constant) and isinstance(elt.value, str), (
                    "every PLUGIN_BUNDLE_MEMBERS entry must be a string literal"
                )
                members.append(elt.value)
            return members
    raise AssertionError("no `PLUGIN_BUNDLE_MEMBERS = ...` assignment found in cache_key.py")


def test_codex_job_is_a_plugin_bundle_member():
    """PLAN #198 §3: codex_job.py (the W5 dispatch driver) must be registered
    in cache_key.py's PLUGIN_BUNDLE_MEMBERS so an edit to it flips
    plugin_bundle_hash -- NOT merely present in this test's ALL_SCRIPTS. This
    asserts the REAL bundle membership (parsed from cache_key.py source), so
    ALL_SCRIPTS and the actual bundle cannot silently drift apart."""
    members = _plugin_bundle_members()
    assert "codex_job.py" in members, (
        "codex_job.py is in this test's ALL_SCRIPTS (it carries the seg-id "
        "safety copy) but is NOT a cache_key.py PLUGIN_BUNDLE_MEMBERS entry -- "
        "register it there so an edit to the W5 dispatch driver flips "
        f"plugin_bundle_hash. Current members: {members}"
    )


def test_all_scripts_and_plugin_bundle_members_are_distinct_lists():
    """The two lists encode DIFFERENT contracts and must stay distinct:
    ALL_SCRIPTS = seg-id-safety-copy carriers; PLUGIN_BUNDLE_MEMBERS =
    gating-hash members. They overlap but are not the same set. Concrete,
    stable witnesses of the asymmetry pin that they are not conflated."""
    all_scripts = set(ALL_SCRIPTS)
    members = set(_plugin_bundle_members())
    assert all_scripts != members, (
        "ALL_SCRIPTS and PLUGIN_BUNDLE_MEMBERS unexpectedly became identical "
        "-- they encode different contracts and must stay distinct lists."
    )
    assert "select_segments.py" in all_scripts and "select_segments.py" not in members, (
        "select_segments.py carries the seg-safety copy (ALL_SCRIPTS) but is an "
        "orchestration-bundle member, not a plugin-bundle member -- witness stale."
    )
    assert "canon_validate.py" in members and "canon_validate.py" not in all_scripts, (
        "canon_validate.py is a plugin-bundle member but does not carry the "
        "seg-safety copy (not in ALL_SCRIPTS) -- witness stale."
    )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
