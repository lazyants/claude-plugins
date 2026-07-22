"""tests/heading_render_levels.test.py -- render_obsidian.py's heading LEVEL
support (#210 R1, D1/A2): the node-`level`-aware `#`/`##`/`###` emission
`_render_block`'s heading branch now performs, and `_heading_level`'s
renderer fail-safe clamp that backs it.

## Ownership and relationship to tests/render_obsidian.test.py

This file owns ONLY the level-specific proof for #210 R1 -- rendering at a
declared level, the defensive clamp for a malformed or absent `level`, the
back-compat byte-identity invariant, and the #171 title/slug non-regression.
`tests/render_obsidian.test.py` stays the existing surface pinning EVERY
OTHER `_render_block`/`render()` behavior (verse, footnote, wikilink,
entity-note, Mentions...) -- this file adds to that coverage, it does not
replace or re-test any of it.

## Invocation style

Mirrors tests/render_obsidian.test.py exactly, per render_obsidian.py's own
module docstring ("D's tests are expected to import render()/its helpers
directly against a hand-authored fixture NodeStream rather than shell out to
this CLI"): `importlib.util.spec_from_file_location` loads the real shipped
script IN-PROCESS, so both the pure `render()` entry point and the private
`_heading_level`/`_render_block`/`_segment_title` helpers are directly
reachable against hand-built node/nodestream fixtures -- including a `level`
of `True`/`"3"`/`None`/absent-entirely, none of which a subprocess CLI
invocation (JSON file in, JSON line out) could ever construct or observe.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
)
RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"

assert RENDER_OBSIDIAN_SRC.is_file(), (
    f"render_obsidian.py not found at {RENDER_OBSIDIAN_SRC}"
)


def _load_render_obsidian_module():
    spec = importlib.util.spec_from_file_location(
        "render_obsidian_under_test_heading_levels", RENDER_OBSIDIAN_SRC
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_obsidian = _load_render_obsidian_module()
assert hasattr(render_obsidian, "_heading_level"), (
    "render_obsidian.py must expose _heading_level(node) -- #210 R1's "
    "renderer fail-safe clamp"
)


# ---------------------------------------------------------------------------
# Fixture builders -- deliberately local to this file (test fixture builders,
# not the cross-cutting SHIPPED-script logic the plugin's "no shared util
# module" rule targets), but shaped identically to their
# tests/render_obsidian.test.py namesakes for readability.
# ---------------------------------------------------------------------------

# Sentinel distinguishing "the 'level' key is entirely absent from the node
# dict" from "the 'level' key is present and explicitly None" -- both are
# real, distinct clamp cases the plan requires (an absent key is what every
# pre-#210-R1 node, and any node from a manifest with no heading_levels,
# actually carries; an explicit None is a defensive extra shape no known
# producer emits today but the clamp must still cover it).
_ABSENT = object()


def make_node(node_id, seg, text, kind="prose", level=_ABSENT, fnrefs=None, verses=None, order_index=0):
    node = {
        "id": node_id, "seg": seg, "kind": kind, "raw_type": "PARA",
        "order_index": order_index, "medium": "plain", "text": text,
        "fnrefs": fnrefs or [], "verses": verses or [],
    }
    if level is not _ABSENT:
        node["level"] = level
    return node


def make_nodestream(nodes, footnotes=None, target="ru", verse_mode="literal_only"):
    return {
        "book": {"seg_order": sorted({n["seg"] for n in nodes}), "title": "Test Book"},
        "nodes": nodes,
        "footnotes": footnotes or [],
        "meta": {"target": target, "verse_mode": verse_mode, "apparatus_policy": "translate_all"},
    }


def make_canon(entries):
    return {"entries": entries, "review_queue": [], "generation_hashes": {}}


def make_profile(folders=None, target_lang="ru"):
    return {
        "target": {"language": {"code": target_lang}},
        "output": {
            "name_display": {"parenthetical_originals": "never"},
            "adapter_config": {"obsidian": {"folders": folders or {}}},
        },
    }


def render_into(out_dir, nodestream, canon, profile):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = render_obsidian.render(nodestream, canon, profile, out_dir)
    return out_dir, manifest


def parse_frontmatter(text):
    """Splits a `---\\n...\\n---` YAML frontmatter block off the top of an
    Obsidian note and parses it -- same convention as
    tests/render_obsidian.test.py's own helper of the same name."""
    assert text.startswith("---"), f"expected YAML frontmatter, got:\n{text[:200]!r}"
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"malformed frontmatter block:\n{text[:200]!r}"
    return yaml.safe_load(parts[1]) or {}


def body_after_frontmatter(text):
    """Everything after the closing `---` of the frontmatter block, with the
    leading/trailing blank-line padding `_render_segment_note`'s own
    `"\\n\\n".join(...)` joins introduce stripped off -- the body content
    itself, unmodified otherwise."""
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"malformed frontmatter block:\n{text[:200]!r}"
    return parts[2].strip("\n")


def no_op_linker():
    """A `_Linker` with no compiled entity pattern -- `link()`'s own
    `if ... self.pattern is None: return text` short-circuit makes it a pure
    pass-through, exactly what a heading-level unit test needs and nothing
    more (no canon, no wikilinking, no first-occurrence bookkeeping)."""
    return render_obsidian._Linker(None, {}, "never")


# ===========================================================================
# 1. Valid levels 1-6: the declared level's own `#` count, nothing else.
# ===========================================================================


@pytest.mark.parametrize(
    ("level_value", "expected_prefix"),
    [(1, "#"), (3, "###"), (6, "######")],
    ids=["level_1", "level_3", "level_6"],
)
def test_render_block_emits_the_declared_hash_count(level_value, expected_prefix):
    node = make_node("h1", "seg01", "Chapter One", kind="heading", level=level_value)
    assert render_obsidian._render_block(node, no_op_linker()) == f"{expected_prefix} Chapter One"


# ===========================================================================
# 2. The defensive clamp: every malformed or absent `level` renders `## `,
#    never invalid markdown (`#######`) and never silently degrading a
#    heading to bare prose (a 0-count `#` run).
# ===========================================================================


@pytest.mark.parametrize(
    "level_value",
    [0, 7, "3", True, None, _ABSENT],
    ids=["zero", "seven", "string_three", "bool_true", "none", "absent"],
)
def test_render_block_clamps_every_malformed_or_absent_level_to_two(level_value):
    """A hand-built nodestream node is exactly what an upstream validation
    bug, a resumed pre-#210-R1 project, or a hand-authored fixture could
    hand this renderer -- none of these six shapes may ever reach an invalid
    (`#######`) or silently-degrading (an empty `#` run collapsing the
    heading into plain prose) render. See `_heading_level`'s own docstring
    for why this is a renderer CLAMP, not a validation gate."""
    node = make_node("h1", "seg01", "Chapter One", kind="heading", level=level_value)
    got = render_obsidian._render_block(node, no_op_linker())
    assert got == "## Chapter One", f"level={level_value!r} must clamp to level 2 -- got {got!r}"


# ===========================================================================
# 3. Back-compat byte-identity (load-bearing): a node with no `level` key at
#    all -- every node built before this feature existed, and every project
#    whose manifest never declares heading_levels -- must render EXACTLY the
#    pre-1.12.0 hardcoded output. Expected line pinned as a LITERAL, never
#    re-derived from the renderer under test.
# ===========================================================================


def test_back_compat_absent_level_renders_byte_identical_to_pre_1_12_0(tmp_path):
    node = make_node("h1", "seg01", "Chapter One", kind="heading")
    assert "level" not in node, "sanity: this fixture must carry no 'level' key at all"

    ns = make_nodestream([node])
    out_dir, manifest = render_into(tmp_path / "out", ns, make_canon({}), make_profile())
    assert len(manifest["written"]) == 1

    body_text = (out_dir / manifest["written"][0]).read_text(encoding="utf-8")
    first_line = body_after_frontmatter(body_text).splitlines()[0]
    assert first_line == "## Chapter One", (
        f"a heading node with no 'level' key must render the pre-1.12.0 "
        f"literal '## Chapter One' -- got {first_line!r}"
    )


# ===========================================================================
# 4. #171 non-regression: _segment_title / the filename slug derive from the
#    heading TEXT only, and must stay unaffected by `level`.
# ===========================================================================


def test_segment_title_is_unaffected_by_level():
    for level_value in (1, 2, 3, 4, 5, 6, 0, 7, "3", True, None, _ABSENT):
        node = make_node("h1", "seg01", "Chapter One", kind="heading", level=level_value)
        title = render_obsidian._segment_title([node], "seg01")
        assert title == "Chapter One", f"level={level_value!r} changed the title -- got {title!r}"


def test_filename_slug_and_frontmatter_title_are_identical_across_levels(tmp_path):
    """Two renders differing ONLY in the heading's `level` must produce the
    identical filename and the identical frontmatter `title` -- the level
    may only ever change the rendered `#` count, never the #171 title/slug
    path."""
    node_lvl1 = make_node("h1", "seg01", "Chapter One", kind="heading", level=1)
    node_lvl5 = make_node("h1", "seg01", "Chapter One", kind="heading", level=5)

    out_dir1, manifest1 = render_into(
        tmp_path / "lvl1", make_nodestream([node_lvl1]), make_canon({}), make_profile()
    )
    out_dir2, manifest2 = render_into(
        tmp_path / "lvl5", make_nodestream([node_lvl5]), make_canon({}), make_profile()
    )

    assert manifest1["written"] == manifest2["written"], (
        "the filename must be identical regardless of heading level"
    )
    rel_path = manifest1["written"][0]
    fm1 = parse_frontmatter((out_dir1 / rel_path).read_text(encoding="utf-8"))
    fm2 = parse_frontmatter((out_dir2 / rel_path).read_text(encoding="utf-8"))
    assert fm1["title"] == fm2["title"] == "Chapter One"

    body1 = body_after_frontmatter((out_dir1 / rel_path).read_text(encoding="utf-8"))
    body2 = body_after_frontmatter((out_dir2 / rel_path).read_text(encoding="utf-8"))
    assert body1.splitlines()[0] == "# Chapter One"
    assert body2.splitlines()[0] == "##### Chapter One"


# ===========================================================================
# 5. Mutation-proof: the level-3 case above is not vacuously true. Neuter
#    `_heading_level` to a constant 2 and show the identical level-3 fixture
#    FLIPS to rendering at level 2 -- if `_render_block`'s heading branch did
#    not actually consult `_heading_level`, this neutering would have no
#    observable effect.
# ===========================================================================


def test_mutation_proof_neutering_heading_level_flips_the_level_three_fixture(monkeypatch):
    node = make_node("h1", "seg01", "Chapter Three", kind="heading", level=3)

    assert render_obsidian._render_block(node, no_op_linker()) == "### Chapter Three", (
        "sanity: the real (non-monkeypatched) clamp must render level 3 as "
        "### before mutating it"
    )

    monkeypatch.setattr(render_obsidian, "_heading_level", lambda n: 2)
    got = render_obsidian._render_block(node, no_op_linker())
    assert got == "## Chapter Three", (
        f"neutering _heading_level to a constant 2 must flip this level-3 "
        f"fixture's render from ### to ## -- if it doesn't, _render_block's "
        f"heading branch is not actually calling _heading_level (got {got!r})"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
