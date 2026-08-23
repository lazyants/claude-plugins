"""tests/canon_category_disclosure.test.py -- #406: the four operator-facing
sites that advertise a canon entry's optional `category` field must say, in the
place a reader actually consults, that the SHIPPED glossary pass never asks for
it -- and the shipped glossary templates must keep not asking.

WHAT THIS FILE PROVES, EXACTLY:

  1. the disclosure sentence is present in `properties.category.description` of
     canon-entry.schema.json, in the `folders` description of
     profile.schema.json, inside obsidian.md's "Category->folder catalog"
     section, and inside profile.example.yml's comment block under
     `folders: {}`;
  2. neither shipped glossary template -- glossary_TASK.template.md nor
     glossary-pass-wf.template.js -- mentions `categor` at all.

WHAT IT DOES **NOT** PROVE, and must never be read as proving: that no shipped
writer can emit the field at runtime. Several legitimately can. canon-batch.
schema.json admits an optional `category` at intake, so a glossary agent may
volunteer it unprompted and an operator may hand-supply a category-bearing
batch; `canon_validate.py --correct` explicitly legalizes a category-only
correction; and `${durable_root}/glossary_TASK.md` is a one-time, hand-adaptable
project seed whose adapted copy may ask for the field. (2) is a claim about the
SHIPPED TEMPLATES ONLY. That is also exactly why the disclosure's own wording is
a negative about the prompt plus a prohibition on assuming, never a positive
claim about what a canon contains.

The two halves make this two-sided. Deleting or rewording a disclosure fails
half 1. Teaching either shipped template to ask for `category` fails half 2 --
which is the moment all four disclosures become false and must be revised
together, and is the only reason half 2 is in this file at all.

Each half-1 assertion is LOCATION-SCOPED, never a whole-file membership test: a
whole-file needle stays green after the sentence is moved out of the property /
section / comment block a reader consults on the way to authoring a gate, which
is precisely the defect #406 records. The two Markdown/YAML sites are matched
against WHITESPACE-COLLAPSED text, because both files hard-wrap and a needle
spanning a wrap point would otherwise miss while the content is fully intact.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PLUGIN_ROOT / "skills" / "literary-translator"
ASSETS = SKILL_DIR / "assets"

CANON_ENTRY_SCHEMA = ASSETS / "schemas" / "canon-entry.schema.json"
PROFILE_SCHEMA = ASSETS / "schemas" / "profile.schema.json"
PROFILE_EXAMPLE = ASSETS / "profile.example.yml"
OBSIDIAN_DOC = SKILL_DIR / "references" / "output-target-adapters" / "obsidian.md"
GLOSSARY_TASK_TEMPLATE = ASSETS / "templates" / "glossary_TASK.template.md"
GLOSSARY_WF_TEMPLATE = ASSETS / "templates" / "glossary-pass-wf.template.js"

for path in (
    CANON_ENTRY_SCHEMA,
    PROFILE_SCHEMA,
    PROFILE_EXAMPLE,
    OBSIDIAN_DOC,
    GLOSSARY_TASK_TEMPLATE,
    GLOSSARY_WF_TEMPLATE,
):
    assert path.is_file(), f"expected {path} to exist"

CATEGORY_TOKEN_RE = re.compile(r"categor", re.IGNORECASE)


def _collapse(text: str) -> str:
    """Every run of whitespace to one space -- so a needle is independent of
    where the file happens to hard-wrap today."""
    return re.sub(r"\s+", " ", text).strip()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- half 1: the disclosure, each pinned to its own location --------------------


def test_canon_entry_schema_discloses_on_the_category_property_itself():
    """Scoped to `properties.category.description`. Moving the sentence to the
    schema's top-level description, or to a `$comment`, must fail: the reader
    this defect was measured on was reading the PROPERTY."""
    description = _load(CANON_ENTRY_SCHEMA)["properties"]["category"]["description"]
    collapsed = _collapse(description)
    assert (
        "NOT REQUESTED BY THE SHIPPED GLOSSARY PASS: glossary_TASK.template.md "
        "neither asks for this field nor illustrates it" in collapsed
    ), "canon-entry.schema.json's category description lost its #406 disclosure"
    assert "MUST NOT assume it is populated" in collapsed, (
        "canon-entry.schema.json's category description no longer forbids "
        "assuming the field is populated"
    )


def test_profile_schema_discloses_on_the_folders_catalog_itself():
    """Scoped to the `folders` property's own description -- the one surface an
    operator maintaining an ALREADY-CREATED profile reaches (Step 0 copies
    profile.example.yml only when the profile is absent, while every validation
    loads the plugin's own profile.schema.json)."""
    folders = (
        _load(PROFILE_SCHEMA)["properties"]["output"]["properties"]["adapter_config"]
        ["properties"]["obsidian"]["properties"]["folders"]
    )
    collapsed = _collapse(folders["description"])
    assert (
        "The shipped glossary pass never asks for 'category', so a catalog "
        "declared here routes nothing until entries actually carry one" in collapsed
    ), "profile.schema.json's obsidian.folders description lost its #406 disclosure"


def test_obsidian_doc_discloses_inside_the_category_catalog_section():
    """Scoped to the window between the catalog heading and the next `## `
    heading -- the section holding the copy-me `folders:` yaml block."""
    text = OBSIDIAN_DOC.read_text(encoding="utf-8")
    heading = "## Category→folder catalog"
    assert text.count(heading) == 1, f"catalog heading not unique: {heading!r}"
    start = text.index(heading)
    next_heading = text.find("\n## ", start + len(heading))
    assert next_heading != -1, "no heading follows the category catalog section"
    section = _collapse(text[start:next_heading])
    assert "The shipped glossary pass never asks for `category`" in section, (
        "obsidian.md's Category->folder catalog section lost its #406 disclosure"
    )
    assert (
        "do not write a canon completeness gate that assumes the field is "
        "populated" in section
    ), "obsidian.md's catalog section no longer warns against the #406 gate"


def test_profile_example_discloses_in_the_folders_comment_block():
    """Scoped to the contiguous `#` comment run immediately following the
    `folders: {}` line -- the block an operator reads while filling the copied
    profile in. A needle anywhere else in this 700-line example would not."""
    lines = PROFILE_EXAMPLE.read_text(encoding="utf-8").splitlines()
    folders_idx = [i for i, line in enumerate(lines) if line.strip() == "folders: {}"]
    assert len(folders_idx) == 1, (
        f"expected exactly one 'folders: {{}}' line in {PROFILE_EXAMPLE.name}, "
        f"found {len(folders_idx)} -- this test's anchor is stale"
    )
    block: list[str] = []
    for line in lines[folders_idx[0] + 1 :]:
        if not line.strip().startswith("#"):
            break
        block.append(line.strip().lstrip("#"))
    assert block, "the folders: {} line is no longer followed by a comment block"
    collapsed = _collapse(" ".join(block))
    assert (
        "The shipped glossary pass never asks for `category`, so a catalog "
        "declared here routes only entries that already carry one" in collapsed
    ), "profile.example.yml's folders comment block lost its #406 disclosure"


# --- half 2: the fact the disclosure asserts ------------------------------------


def test_shipped_glossary_templates_do_not_mention_category():
    """The disclosure above is only true while this holds. Case-insensitive and
    stem-level (`categor`), so `Category`, `categories` and `categorise` all
    count: any of them appearing means the shipped prompt has started to talk
    about the field, and every disclosure site must then be revised."""
    for template in (GLOSSARY_TASK_TEMPLATE, GLOSSARY_WF_TEMPLATE):
        hits = [
            f"{template.name}:{n}: {line.strip()}"
            for n, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1)
            if CATEGORY_TOKEN_RE.search(line)
        ]
        assert not hits, (
            "a shipped glossary template now mentions `category`, so the #406 "
            "disclosures in canon-entry.schema.json, profile.schema.json, "
            "profile.example.yml and obsidian.md are no longer true and must be "
            "revised:\n" + "\n".join(hits)
        )
