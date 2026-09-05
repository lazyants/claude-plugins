"""tests/entity_markup_example_slot.test.py

Targets the ``output.entity_markup`` slot in ``assets/profile.example.yml`` (#873).

The field is asked for by SKILL.md's Step 0d ("do you want an index, and of
what?") and is real in ``profile.schema.json``, but until this release it
appeared NOWHERE in the example profile, in any form. Step 0 copies that file
verbatim into a project that has no ``profile.yml`` yet, so the file an
operator actually edits held no slot for the answer -- and every volume of one
live series that reached Step 0d hand-authored the block, at the same place,
carrying the same series ruling forward by retyping it.

Deliberately its OWN file rather than a fourth case in
``profile_example_validation.test.py``: that file's docstring declares it is
split into exactly THREE cases, and folding a fourth in would make its own
contract stale.

Each test states the property it pins and why. Two facts sit above them all,
because neither belongs to a single test:

  * Shipping the block COMMENTED is what lets this release touch no script at
    all. A ``CHOOSE_`` sentinel would need a matching ``KNOB_QUESTIONS`` entry
    in ``profile_validate.py``, held equal by that script's own two-way drift
    guard. ``glossary.name_discovery`` already ships this shape in the same
    file, and ``name_discovery.test.py`` pins it the same way.

  * The block sits where ``profile.schema.json`` orders the key -- between
    ``name_display`` and ``adapter_config``. Every other key under ``output:``
    in the example already follows schema order, and this one now does too.
    The hand-authored copies all put it ABOVE ``name_display:`` instead; that
    is evidence of the burden this release removes, not evidence about where
    the shipped block belongs, and matching the schema costs nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS = SKILL_DIR / "assets"
PROFILE_EXAMPLE = ASSETS / "profile.example.yml"
PROFILE_SCHEMA = ASSETS / "schemas" / "profile.schema.json"

# Every token that only appears in this file because of the entity_markup
# block: the block name plus its three fields. `tags` carries its colon
# because the bare word also occurs in the block's own prose.
BLOCK_TOKENS = ("entity_markup", "index_from", "ref_attribute", "tags:")


@pytest.fixture(scope="module")
def example_text() -> str:
    return PROFILE_EXAMPLE.read_text(encoding="utf-8")


def test_the_example_ships_the_block_commented_out_with_no_sentinel(example_text: str) -> None:
    """A live block would turn markup stripping on for every scaffolded book;
    a CHOOSE_ sentinel would demand an answer from projects Step 0d exempts."""
    assert "entity_markup" in example_text, (
        "assets/profile.example.yml must document output.entity_markup -- the "
        "whole of #873 is that the file an operator edits had no slot for "
        "Step 0d's index answer"
    )
    checked = 0
    for lineno, line in enumerate(example_text.splitlines(), 1):
        if not any(tok in line for tok in BLOCK_TOKENS):
            continue
        checked += 1
        assert line.lstrip().startswith("#"), (
            f"the entity_markup block must ship commented out, but "
            f"profile.example.yml:{lineno} is live: {line!r}"
        )
        assert "CHOOSE_" not in line, (
            f"no CHOOSE_ sentinel may be added for entity_markup -- Step 0d "
            f"excludes it on purpose, and a sentinel would additionally require "
            f"a KNOB_QUESTIONS entry in profile_validate.py. "
            f"profile.example.yml:{lineno}: {line!r}"
        )
    # A loop that iterates zero times prints exactly what a passing one prints.
    assert checked >= 4, (
        f"expected at least the block name plus its three fields to be present "
        f"as commented lines, found {checked}"
    )
    print(f"entity_markup commented lines checked = {checked}")


def test_the_block_is_absent_from_the_parsed_document(example_text: str) -> None:
    """Absence is the valid 'no' SKILL.md documents, and the default every
    existing project inherits. The parser is what settles it: a live block and
    a commented one both CONTAIN the string, so the answer cannot come from the
    text, and this check holds even if BLOCK_TOKENS ever stops matching."""
    doc = yaml.safe_load(example_text)
    assert "entity_markup" not in doc["output"], (
        "output.entity_markup must NOT be a live key -- an absent block runs "
        "none of it, and shipping it live would enable markup stripping for "
        "every freshly scaffolded project"
    )
    print(f"output keys = {sorted(doc['output'])}")


def test_the_block_sits_under_output_in_schema_order(example_text: str) -> None:
    """Placement is the half neither the parse nor the substring check sees --
    a commented block anywhere in the file satisfies both. It must sit under
    output:, between name_display: and adapter_config:, which is the order
    profile.schema.json declares and the order every other key here follows.

    The bound is read off the SCHEMA rather than restated, so if the schema
    ever reorders these properties this test says so instead of pinning a
    slot the shipped contract has moved away from."""
    schema = json.loads(PROFILE_SCHEMA.read_text(encoding="utf-8"))
    order = list(schema["properties"]["output"]["properties"])
    assert order.index("name_display") < order.index("entity_markup") < order.index(
        "adapter_config"
    ), f"schema reordered output's properties under this test: {order}"

    output_hdr = example_text.index("\noutput:\n")
    name_display = example_text.index("\n  name_display:\n", output_hdr)
    block = example_text.index("# entity_markup --", output_hdr)
    adapter_config = example_text.index("\n  adapter_config:\n", output_hdr)
    assert output_hdr < name_display < block < adapter_config, (
        "the commented entity_markup block belongs under output:, between "
        f"name_display: and adapter_config: (output:={output_hdr} "
        f"name_display={name_display} block={block} "
        f"adapter_config={adapter_config})"
    )
    print(
        f"output:={output_hdr} block={block} name_display={name_display} "
        f"adapter_config={adapter_config}"
    )


def test_the_comment_names_the_fields_the_schema_actually_declares(example_text: str) -> None:
    """The block is documentation, so it is worth exactly as much as its
    agreement with the schema. Read the property names off the schema rather
    than restating them here -- a restated list drifts silently."""
    schema = json.loads(PROFILE_SCHEMA.read_text(encoding="utf-8"))
    declared = schema["properties"]["output"]["properties"]["entity_markup"]["properties"]
    assert set(declared) == {"tags", "ref_attribute", "index_from"}, (
        f"schema changed shape under this test: {sorted(declared)}"
    )
    start = example_text.index("# entity_markup --")
    end = example_text.index("\n  adapter_config:\n", start)
    block_text = example_text[start:end]
    for field in sorted(declared):
        assert field in block_text, (
            f"the commented block must name every field the schema declares; "
            f"{field!r} is missing"
        )
    # The one cross-field refusal an operator can actually trip.
    assert "entity_markup_index_unsupported_target" in block_text, (
        "the block must state that index_from: markup requires "
        "output.target: obsidian -- assemble.py refuses outright rather than "
        "degrading to strip"
    )
    print(f"schema fields named in the comment = {sorted(declared)}")
