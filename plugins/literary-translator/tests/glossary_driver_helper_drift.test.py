#!/usr/bin/env python3
"""The two no-follow filesystem helpers exist in TWO copies. Keep them equal.

This plugin ships no shared util module: every script self-anchors and stays
dependency-free, so a cross-cutting helper is DUPLICATED byte-for-byte rather than
imported (see references/plugin-facts.md's script house style, and the
`draft_content_sha1()` / `mentions_cfg` precedents where exactly this duplication
drifted).

`_open_regular_no_follow_walk` and `_refuse_unless_executable_leaf` are the
security boundary for every path either driver EXECUTES or READS as code. Their
comments record that each narrower mechanism written for this boundary had its own
gap in a different layer, and that the walk is the only one attacked from every
layer and held. So a fix to one copy that does not reach the other leaves a driver
running the weaker version -- and nothing else in either suite would notice, because
each file's own tests would still pass.

The comparison is on the FULL function text including comments and docstring: the
reasoning is the artifact here as much as the code, and a copy that keeps the body
while losing why it is shaped that way is the next edit's hazard.
"""

import re
import sys
from pathlib import Path

import pytest

SCRIPTS = (Path(__file__).resolve().parents[1] / "skills" / "literary-translator"
           / "assets" / "scripts")
SEGMENT_DRIVER = SCRIPTS / "segment_dispatch_driver.py"
GLOSSARY_DRIVER = SCRIPTS / "glossary_dispatch_driver.py"

DUPLICATED = ("_open_regular_no_follow_walk", "_refuse_unless_executable_leaf")


def extract_function(path: Path, name: str) -> str:
    """Slices from `def <name>(` to the next top-level `def`/`class`, which is
    exact here because both files declare these at module level and neither uses a
    nested def inside them."""
    src = path.read_text(encoding="utf-8")
    start = re.search(rf"^def {re.escape(name)}\(", src, re.M)
    assert start, f"{name} not found in {path.name}"
    nxt = re.search(r"^(?:def |class )", src[start.end():], re.M)
    end = start.end() + nxt.start() if nxt else len(src)
    return src[start.start():end].rstrip() + "\n"


@pytest.mark.parametrize("name", DUPLICATED)
def test_the_duplicated_helper_is_byte_identical_across_both_drivers(name):
    theirs = extract_function(SEGMENT_DRIVER, name)
    ours = extract_function(GLOSSARY_DRIVER, name)
    assert ours == theirs, (
        f"{name} has drifted between segment_dispatch_driver.py and "
        f"glossary_dispatch_driver.py. This plugin duplicates rather than imports, "
        f"so BOTH copies must be edited together -- a one-sided fix leaves one "
        f"driver running the weaker version of a security boundary."
    )


@pytest.mark.parametrize("name", DUPLICATED)
def test_the_helper_is_substantial_in_both_copies(name):
    """Guards the comparison itself against vacuity: two empty or truncated slices
    are also 'identical'. Both copies carry a long explanatory comment block, so a
    slice that collapsed to a few lines means the extractor, not the code, broke."""
    for path in (SEGMENT_DRIVER, GLOSSARY_DRIVER):
        text = extract_function(path, name)
        assert len(text.splitlines()) > 30, (
            f"{name} in {path.name} sliced to {len(text.splitlines())} lines -- "
            f"the extractor is not finding the whole function")


def test_the_glossary_driver_does_not_import_the_helper_from_its_sibling():
    """The duplication is deliberate and the no-import rule is what makes each
    script independently copyable into a project's own scripts/ dir. An import
    would work on this machine and break in every deployed durable_root.

    PARSED, not grepped. A substring scan for "from segment_dispatch_driver"
    matches this file's own prose about the duplication -- the same trap
    resolve_codex_companion.py's docstring records for `__file__` ("read that
    test's verdict, not a raw grep: the mentions in this docstring are prose ABOUT
    the claim and a text search cannot tell them apart from a real one"). Only the
    AST distinguishes a sentence from an import statement."""
    import ast
    tree = ast.parse(GLOSSARY_DRIVER.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "segment_dispatch_driver" not in imported, (
        "the glossary driver imports its sibling; this plugin duplicates rather "
        "than imports, and an import breaks in every deployed durable_root where "
        "only some scripts are copied")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
