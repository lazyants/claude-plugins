"""tests/entity_markup_display.test.py -- #925: a markup-minted entity note's
printed heading/`display:` form, ruled by an operator/Claude-authored sidecar
`${durable_root}/markup_display.json` and read by `render_obsidian.py` at
render time.

## What this file owns

`tests/entity_markup_render.test.py` owns the base #795 markup-note contract
(identity, aliases, filename/link resolution, frontmatter shape with no
ruling). This file owns the FIFTH render input #925 adds on top of it: the
sidecar that lets an operator turn a slug/ref label such as
`r-nachman-noson` into the printed form the translation actually uses --
`display: "R. Nachman Noson"` on the note's frontmatter and heading -- without
ever changing the note's filename, `ref`, or any wikilink target.

## Why every test stages its own copy and imports from it

`MARKUP_DISPLAY_PATH` is `DURABLE_ROOT / "markup_display.json"`, resolved from
the SCRIPT's own location exactly like `CANON_PATH` and `canon_link_groups.py`'s
own sidecar -- not a `render()` parameter. A test that imported the plugin's
one shared `render_obsidian` module (as `tests/entity_markup_render.test.py`
does) would have `DURABLE_ROOT` pinned to the real plugin source tree, where
`assets/markup_display.json` does not exist and is never tracked -- every
sidecar-present case would be untestable, and monkeypatching the module
CONSTANT would prove nothing about the self-anchored path production code
actually resolves. So every test that needs a sidecar (most of them) calls
`stage_durable_root(tmp_path)`, which copies the shipped scripts into
`tmp_path/durable_root/scripts/` and imports `render_obsidian` fresh from
THAT copy via `importlib` (a unique module name per call, so parallel-xdist
workers loading many copies never collide in `sys.modules`) -- `DURABLE_ROOT`
inside the loaded module is then the fixture root, and the sidecar is read
from its real production location, `tmp_path/durable_root/markup_display.json`.

## Invocation styles

Unit cases call the staged module's own `render()` directly with a
hand-authored NodeStream (mirrors `tests/entity_markup_render.test.py`'s own
split). Two integration cases run the REAL CLIs as subprocesses: section 7
drives `render_obsidian.py`'s own `main()`, and section 8 drives the actual
`assemble.py` end to end (the real W9 operator path), reusing
`tests/entity_markup_assemble.test.py`'s fixture-staging discipline.

## Delivery is asserted against the WRITTEN FILES

Every success case reads the emitted `.md` file(s) off disk rather than
trusting only the returned manifest -- a manifest field proves render()
computed something, not that a reader would ever see it in the vault.
"""
from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from unittest import mock

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"

RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"
ASSEMBLE_SRC = SCRIPTS_SRC_DIR / "assemble.py"
OUTPUT_RESOLVE_SRC = SCRIPTS_SRC_DIR / "output_resolve.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
CACHE_KEY_SRC = SCRIPTS_SRC_DIR / "cache_key.py"
JSON_STDOUT_SRC = SCRIPTS_SRC_DIR / "json_stdout.py"
VALIDATE_BACKLINKS_SRC = SCRIPTS_SRC_DIR / "validate_backlinks.py"
BOOTSTRAP_NAMES_SRC = SCRIPTS_SRC_DIR / "bootstrap_names.py"
CANON_SENSES_SRC = SCRIPTS_SRC_DIR / "canon_senses.py"
CANON_SENSES_SCHEMA_SRC = SCHEMAS_SRC_DIR / "canon-senses.schema.json"

for _src in (RENDER_OBSIDIAN_SRC, ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, VALIDATE_DRAFT_SRC,
             CACHE_KEY_SRC, JSON_STDOUT_SRC, VALIDATE_BACKLINKS_SRC, BOOTSTRAP_NAMES_SRC,
             CANON_SENSES_SRC, CANON_SENSES_SCHEMA_SRC):
    assert _src.is_file(), f"required fixture source not found at {_src}"

FOLDERS = {"person": "People", "place": "Places"}

_MODULE_SEQ = itertools.count()


# ---------------------------------------------------------------------------
# Fixture builders -- restated from tests/entity_markup_render.test.py rather
# than imported (house convention: one self-contained file per test module).
# ---------------------------------------------------------------------------

def stage_durable_root(tmp_path):
    """Stage a fresh `${root}/scripts/` with the real shipped scripts and
    import `render_obsidian` from THAT copy, so `DURABLE_ROOT` inside it is
    `root` and `MARKUP_DISPLAY_PATH` resolves to `root/markup_display.json`
    -- the real production location, never monkeypatched. Returns
    `(root, module)`."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (RENDER_OBSIDIAN_SRC, ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, VALIDATE_DRAFT_SRC,
                CACHE_KEY_SRC, JSON_STDOUT_SRC, VALIDATE_BACKLINKS_SRC, BOOTSTRAP_NAMES_SRC,
                CANON_SENSES_SRC):
        shutil.copy2(src, scripts_dir / src.name)
    schemas_dir = root / "schemas"
    schemas_dir.mkdir()
    shutil.copy2(CANON_SENSES_SCHEMA_SRC, schemas_dir / CANON_SENSES_SCHEMA_SRC.name)

    module_name = f"render_obsidian_under_display_test_{next(_MODULE_SEQ)}"
    spec =importlib.util.spec_from_file_location(module_name, scripts_dir / "render_obsidian.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return root, module


def ent(n, payload):
    """The exact three-part sequence assemble.py emits in `index` mode."""
    return f"⟦ENT_{n}⟧{payload}⟦/ENT_{n}⟧"


def span(tag, payload, ref=None):
    record = {"tag": tag, "payload": payload}
    if ref is not None:
        record["ref"] = ref
    return record


def make_node(node_id, seg, text, kind="prose", medium="plain", fnrefs=None,
              verses=None, order_index=0, raw_type="PARA"):
    return {
        "id": node_id, "seg": seg, "kind": kind, "raw_type": raw_type,
        "order_index": order_index, "medium": medium, "text": text,
        "fnrefs": fnrefs or [], "verses": verses or [],
    }


def make_nodestream(nodes, footnotes=None, spans=None, target="ru", extra=None):
    nodestream = {
        "book": {"seg_order": sorted({n["seg"] for n in nodes}), "title": "Test Book"},
        "nodes": nodes,
        "footnotes": footnotes or [],
        "meta": {"target": target, "verse_mode": "literal_only",
                 "apparatus_policy": "translate_all"},
    }
    if spans is not None:
        nodestream["entity_markup"] = {"spans": spans}
    if extra:
        nodestream.update(extra)
    return nodestream


def canon_entry(source_form, canonical_target_form, category="person",
                is_proper_name=True, basis="transliterated", confidence="high"):
    return {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "canonical_target_form": canonical_target_form,
        "basis": basis,
        "confidence": confidence,
        "category": category,
    }


def make_canon(entries):
    return {"entries": entries, "review_queue": [], "generation_hashes": {}}


def make_profile(index_from="markup", tags=("person", "place"),
                  parenthetical_originals="never", folders=None, target="obsidian",
                  entity_markup=True, mentions_enabled=False):
    output = {
        "v1_scope": "assembled_book",
        "target": target,
        "name_display": {"parenthetical_originals": parenthetical_originals},
        "adapter_config": {"obsidian": {
            "folders": FOLDERS if folders is None else folders,
            "mentions_section": {"enabled": mentions_enabled},
        }},
    }
    if entity_markup:
        block = {"tags": list(tags)}
        if index_from is not None:
            block["index_from"] = index_from
        output["entity_markup"] = block
    return {"target": {"language": {"code": "ru"}}, "output": output}


def render_into(module, tmp_path, nodestream, canon, profile, out_dir=None):
    out_dir = out_dir or (tmp_path / "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = module.render(nodestream, canon, profile, out_dir)
    return out_dir, manifest


def make_managed_vault(module, tmp_path):
    """An out_dir that is ALREADY a vault this adapter owns, carrying one
    ordinary (non-dot) file -- `_clean_vault_content` would happily delete
    it, so its survival proves a refusal fired BEFORE the clean."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / module.VAULT_MARKER_FILENAME).write_text(
        json.dumps({"managed_by": "literary-translator", "target": "obsidian"}) + "\n",
        encoding="utf-8",
    )
    (out_dir / "SURVIVOR.md").write_text("pre-existing vault content\n", encoding="utf-8")
    return out_dir


def read(out_dir, relpath):
    return (out_dir / relpath).read_text(encoding="utf-8")


def segment_note_texts(out_dir):
    return [p.read_text(encoding="utf-8") for p in sorted(out_dir.iterdir())
            if p.is_file() and p.suffix == ".md"]


def parse_frontmatter(text):
    assert text.startswith("---"), f"expected YAML frontmatter, got:\n{text[:200]!r}"
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"malformed frontmatter block:\n{text[:200]!r}"
    return yaml.safe_load(parts[1]) or {}


def entity_note_relpaths(manifest):
    return sorted(rel for rel in manifest["written"] if "/" in rel)


def write_sidecar(root, doc):
    """`doc` is any JSON-serializable Python value -- `json.dumps` handles
    "root is a list" and "root is a bare string" just as naturally as "root
    is the well-shaped object", which is exactly the range several
    malformed-shape cases below need to express."""
    path = root / "markup_display.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def write_sidecar_raw(root, text):
    """For a sidecar body that cannot be expressed as a Python JSON value at
    all: hand-typed duplicate member names, an escaped lone surrogate, or
    plain broken JSON syntax."""
    path = root / "markup_display.json"
    path.write_text(text, encoding="utf-8")
    return path


def base_fixture():
    """One minted identity, `(person, "noson")`, with two printed forms --
    the shared starting point for every case below that does not need a
    bespoke book of its own."""
    nodes = [make_node("p1", "seg01",
                        f"{ent(1, 'Reb Noson')} spoke, and later {ent(2, 'R. Noson')} left.")]
    spans = {"1": span("person", "Reb Noson", ref="noson"),
             "2": span("person", "R. Noson", ref="noson")}
    return make_nodestream(nodes, spans=spans), make_canon({}), make_profile()


def assert_refused_before_clean(module, tmp_path, nodestream, canon, profile, reason):
    """Render into an ALREADY-managed vault and prove the refusal fires
    before `_clean_vault_content` runs: `SURVIVOR.md` must still be there
    afterwards, byte-for-byte."""
    out_dir = make_managed_vault(module, tmp_path)
    with pytest.raises(module.RenderError) as exc_info:
        module.render(nodestream, canon, profile, out_dir)
    assert exc_info.value.reason == reason, (
        f"expected reason {reason!r}, got {exc_info.value.reason!r}: {exc_info.value}"
    )
    assert (out_dir / "SURVIVOR.md").is_file(), (
        "a refusal must leave the pre-existing vault untouched -- it fired "
        "AFTER the clean instead of before it"
    )
    assert (out_dir / "SURVIVOR.md").read_text(encoding="utf-8") == "pre-existing vault content\n"
    return exc_info.value


# ===========================================================================
# 1. Sidecar absent -> byte-identical to today, with a VISIBLE displays==0.
# ===========================================================================

def test_absent_sidecar_leaves_note_text_byte_identical_to_today(tmp_path):
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    assert not (root / "markup_display.json").exists()

    out_dir, manifest = render_into(module, tmp_path, nodestream, canon, profile)

    text = read(out_dir, "People/noson.md")
    fm = parse_frontmatter(text)
    assert "display" not in fm, fm
    assert list(fm.keys()) == ["aliases", "name", "category", "ref", "direction"], fm
    assert text.rstrip().endswith("# noson"), text

    assert manifest["entity_markup"]["displays"] == 0
    assert manifest["entity_markup"]["identities"] == [{
        "tag": "person", "label": "noson", "ref": "noson", "note": "People/noson",
        "display": None,
        "aliases": [{"form": "R. Noson", "count": 1}, {"form": "Reb Noson", "count": 1}],
    }]


# ===========================================================================
# 2. Ruling present -> heading and frontmatter reflect it; filename, `ref`
#    and every wikilink target stay unchanged.
# ===========================================================================

def test_ruling_present_heading_and_frontmatter_reflect_the_display(tmp_path):
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "noson", "display": "Reb Noson"},
    ]})

    out_dir, manifest = render_into(module, tmp_path, nodestream, canon, profile)

    text = read(out_dir, "People/noson.md")
    assert text.rstrip().endswith("# Reb Noson"), text
    fm = parse_frontmatter(text)
    assert list(fm.keys()) == ["aliases", "name", "category", "ref", "display", "direction"], fm
    assert fm["name"] == "noson"
    assert fm["ref"] == "noson"
    assert fm["display"] == "Reb Noson"

    assert manifest["entity_markup"]["displays"] == 1
    (identity,) = manifest["entity_markup"]["identities"]
    assert identity["tag"] == "person" and identity["label"] == "noson"
    assert identity["display"] == "Reb Noson"
    assert identity["note"] == "People/noson"


def test_a_ruling_changes_only_the_label_never_filename_or_links(tmp_path):
    """Two independently staged renders of the SAME book, one ruled and one
    not: filename and every wikilink target the segment prose emits must be
    byte-identical, and the wikilink DISPLAY TEXT stays the printed payload
    at each occurrence -- never the ruled display."""
    nodestream, canon, profile = base_fixture()

    root_a, module_a = stage_durable_root(tmp_path / "a")
    out_a, manifest_a = render_into(module_a, tmp_path / "a", nodestream, canon, profile)

    root_b, module_b = stage_durable_root(tmp_path / "b")
    write_sidecar(root_b, {"displays": [
        {"tag": "person", "label": "noson", "display": "Reb Noson"},
    ]})
    out_b, manifest_b = render_into(module_b, tmp_path / "b", nodestream, canon, profile)

    assert entity_note_relpaths(manifest_a) == entity_note_relpaths(manifest_b) == ["People/noson.md"]

    body_a = segment_note_texts(out_a)[0]
    body_b = segment_note_texts(out_b)[0]
    assert "[[People/noson|Reb Noson]]" in body_a and "[[People/noson|R. Noson]]" in body_a
    assert "[[People/noson|Reb Noson]]" in body_b and "[[People/noson|R. Noson]]" in body_b, (
        f"a ruling must not touch the wikilink alias text, got:\n{body_b}"
    )


# ===========================================================================
# 3. Alias counts are per-span-occurrence, sorted count desc then form.
# ===========================================================================

def test_alias_counts_are_per_span_occurrence_sorted_by_count_desc(tmp_path):
    nodes = [make_node("p1", "seg01",
                        f"{ent(1, 'the Rav')} said. {ent(2, 'the Rav')} said again. "
                        f"{ent(3, 'the Rav')} once more, and {ent(4, 'R. Aharon')} agreed.")]
    spans = {"1": span("person", "the Rav", ref="aharon"),
             "2": span("person", "the Rav", ref="aharon"),
             "3": span("person", "the Rav", ref="aharon"),
             "4": span("person", "R. Aharon", ref="aharon")}
    root, module = stage_durable_root(tmp_path)
    nodestream = make_nodestream(nodes, spans=spans)
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(module, tmp_path, nodestream, canon, profile)

    fm = parse_frontmatter(read(out_dir, "People/aharon.md"))
    assert fm["aliases"] == ["R. Aharon", "the Rav"], (
        "the note's own aliases stay sorted+deduped, unaffected by the counts"
    )
    assert manifest["entity_markup"]["identities"] == [{
        "tag": "person", "label": "aharon", "ref": "aharon", "note": "People/aharon",
        "display": None,
        "aliases": [{"form": "the Rav", "count": 3}, {"form": "R. Aharon", "count": 1}],
    }]


def test_a_ruled_identitys_manifest_entry_echoes_the_display(tmp_path):
    nodes = [make_node("p1", "seg01",
                        f"{ent(1, 'the Rav')} said. {ent(2, 'R. Aharon')} agreed.")]
    spans = {"1": span("person", "the Rav", ref="aharon"),
             "2": span("person", "R. Aharon", ref="aharon")}
    root, module = stage_durable_root(tmp_path)
    nodestream = make_nodestream(nodes, spans=spans)
    canon = make_canon({})
    profile = make_profile()
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "aharon", "display": "R. Aharon"},
    ]})

    out_dir, manifest = render_into(module, tmp_path, nodestream, canon, profile)

    assert manifest["entity_markup"]["displays"] == 1
    assert manifest["entity_markup"]["identities"] == [{
        "tag": "person", "label": "aharon", "ref": "aharon", "note": "People/aharon",
        "display": "R. Aharon",
        # Tied counts (1 and 1) break by FORM ascending: "R." (0x52 'R') sorts
        # before "the" (0x74 't').
        "aliases": [{"form": "R. Aharon", "count": 1}, {"form": "the Rav", "count": 1}],
    }]
    assert read(out_dir, "People/aharon.md").rstrip().endswith("# R. Aharon")


# ===========================================================================
# 4. Refusals, fired BEFORE the clean.
# ===========================================================================

_INVALID_SHAPE_DOCS = [
    pytest.param([], id="root-not-an-object-list"),
    pytest.param("not an object", id="root-not-an-object-string"),
    pytest.param({"displays": "not-a-list"}, id="displays-not-a-list"),
    pytest.param({}, id="displays-missing"),
    pytest.param({"displays": ["not-an-object"]}, id="entry-not-an-object"),
    pytest.param({"displays": [{"tag": "person", "label": "noson"}]},
                 id="entry-missing-display-field"),
    pytest.param(
        {"displays": [{"tag": "person", "label": "noson", "display": "X", "extra": 1}]},
        id="entry-extra-field",
    ),
    pytest.param({"displays": [{"tag": "person", "label": "", "display": "X"}]},
                 id="field-empty-string"),
    pytest.param({"displays": [{"tag": 7, "label": "noson", "display": "X"}]},
                 id="field-not-a-string"),
    pytest.param({"displays": [{"tag": "person", "label": "noson", "display": "   "}]},
                 id="display-empty-after-strip"),
]


@pytest.mark.parametrize("doc", _INVALID_SHAPE_DOCS)
def test_malformed_sidecar_shapes_are_refused_before_clean(tmp_path, doc):
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    write_sidecar(root, doc)
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_invalid")


def test_display_containing_lf_is_refused(tmp_path):
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "noson", "display": "Reb\nNoson"},
    ]})
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_invalid")


def test_display_containing_nel_is_refused(tmp_path):
    """NEL (U+0085) is one of `_MENTIONS_LINE_BREAK_CHARS` alongside LF --
    built with `chr()`, never pasted literally, per the plugin's own rule
    against invisible/boundary characters in source."""
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    nel = chr(0x85)
    assert nel in module._MENTIONS_LINE_BREAK_CHARS
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "noson", "display": f"Reb{nel}Noson"},
    ]})
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_invalid")


def test_display_containing_the_reserved_mentions_token_is_refused(tmp_path):
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    token = module._MENTIONS_RESERVED_TOKEN
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "noson", "display": f"Reb {token}Noson"},
    ]})
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_invalid")


def test_display_containing_an_entity_sentinel_is_refused(tmp_path):
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    sentinel = chr(0x27E6) + "ENT_1" + chr(0x27E7)
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "noson", "display": f"Reb {sentinel} Noson"},
    ]})
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_invalid")


def test_display_as_an_escaped_lone_surrogate_is_refused(tmp_path):
    """The six literal characters `\\ud800` in the file -- JSON accepts an
    escaped lone surrogate, and `display.encode("utf-8")` must refuse it
    here rather than let `_write_note`'s strict write raise only after the
    vault has already been cleaned."""
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    write_sidecar_raw(
        root, '{"displays":[{"tag":"person","label":"noson","display":"\\ud800"}]}'
    )
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_invalid")


def test_duplicate_identity_two_entries_same_tag_and_label_is_refused(tmp_path):
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "noson", "display": "R. Noson"},
        {"tag": "person", "label": "noson", "display": "Reb Noson"},
    ]})
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_invalid")


def test_same_label_as_nfc_and_nfd_rows_is_a_duplicate_in_identity_space(tmp_path):
    nfc_label = "R" + chr(0xE9)  # "Ré", precomposed
    nfd_label = unicodedata.normalize("NFD", nfc_label)  # "Re" + combining acute
    assert nfc_label != nfd_label

    root, module = stage_durable_root(tmp_path)
    nodes = [make_node("p1", "seg01", f"{ent(1, nfc_label)} spoke.")]
    nodestream = make_nodestream(nodes, spans={"1": span("person", nfc_label)})
    canon = make_canon({})
    profile = make_profile()
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": nfc_label, "display": nfc_label},
        {"tag": "person", "label": nfd_label, "display": nfc_label},
    ]})
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_invalid")


def test_duplicate_json_member_names_in_one_entry_is_refused(tmp_path):
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    write_sidecar_raw(
        root,
        '{"displays":[{"tag":"person","label":"noson","display":"A","display":"B"}]}',
    )
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_invalid")


def test_a_parser_recursion_error_is_translated_to_markup_display_invalid(tmp_path):
    """`json.loads` raises RecursionError, not ValueError, on a document
    nested past the interpreter's C-stack limit, and the depth bound only
    runs on a PARSED document -- so without its own handler that shape
    escaped as a reasonless generic failure. The limit is 300 000 nested
    arrays on this interpreter and differs per platform, so no fixture depth
    is both reliably above it and small; a depth below it is refused by the
    depth bound with the SAME reason, which proves nothing about this
    branch. So the parser is stood in for directly: the refusal must be
    reason-coded and still pre-clean (the parse precedes the clean)."""
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    write_sidecar_raw(root, '{"displays": []}')

    def parser_overflows(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    with mock.patch.object(module.json, "loads", parser_overflows):
        assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                     "markup_display_invalid")


def test_dangling_symlink_at_sidecar_path_is_refused_not_read_as_absent(tmp_path):
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    (root / "markup_display.json").symlink_to(root / "gone.json")
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_invalid")


def test_a_directory_at_the_sidecar_path_is_refused_not_read_as_absent(tmp_path):
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    (root / "markup_display.json").mkdir()
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_invalid")


def test_ruling_for_a_label_no_span_carries_is_unknown_identity(tmp_path):
    root, module = stage_durable_root(tmp_path)
    nodestream, canon, profile = base_fixture()
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "nobody-marked-this", "display": "Nobody"},
    ]})
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_unknown_identity")


def test_ruling_for_an_identity_canon_composes_is_unknown_identity(tmp_path):
    """Mirrors tests/entity_markup_render.test.py's
    test_canon_backed_marked_span_mints_no_note_and_links_the_canon_note:
    canon owns "Ivan" as a `canonical_target_form`, so the marked span
    composes with the canon note and mints no markup note of its own --
    `(person, "Ivan")` is simply absent from `markup_records`, exactly like
    an unmarked label."""
    root, module = stage_durable_root(tmp_path)
    canon = make_canon({"Иван": canon_entry("Иван", "Ivan")})
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Ivan')} arrived.")]
    nodestream = make_nodestream(nodes, spans={"1": span("person", "Ivan")})
    profile = make_profile()
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "Ivan", "display": "Ivan"},
    ]})
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_unknown_identity")


def test_display_not_among_the_identitys_printed_forms_is_refused(tmp_path):
    """The stale-after-re-translation case: a ruling naming a form the
    identity's spans do not currently print (only "R. Noson" and "Noson" are
    printed here) is refused exactly like any other invalid ruling."""
    nodes = [make_node("p1", "seg01",
                        f"{ent(1, 'R. Noson')} spoke, and later {ent(2, 'Noson')} left.")]
    spans = {"1": span("person", "R. Noson", ref="noson"),
             "2": span("person", "Noson", ref="noson")}
    root, module = stage_durable_root(tmp_path)
    nodestream = make_nodestream(nodes, spans=spans)
    canon = make_canon({})
    profile = make_profile()
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "noson", "display": "Reb Noson"},
    ]})
    assert_refused_before_clean(module, tmp_path, nodestream, canon, profile,
                                 "markup_display_not_printed")


# ===========================================================================
# 5. Same label under two tags: a ruling for one leaves the other alone.
# ===========================================================================

def test_ruling_for_one_tag_leaves_the_other_tags_note_alone(tmp_path):
    """A ruling names `(tag, label)`, not `label` alone: the `person` note
    gets its display, and the unrelated `place` note sharing the same label
    (identity #3 in tests/entity_markup_render.test.py) stays untouched --
    the display must be one of the PERSON identity's own printed forms, so
    a second span gives it a form to rule."""
    nodes = [make_node("p1", "seg01",
                        f"{ent(1, 'Jordan')} crossed near {ent(2, 'the River Jordan')}, "
                        f"east of the {ent(3, 'Jordan')}.")]
    spans = {"1": span("person", "Jordan", ref="jordan"),
             "2": span("person", "the River Jordan", ref="jordan"),
             "3": span("place", "Jordan")}
    root, module = stage_durable_root(tmp_path)
    nodestream = make_nodestream(nodes, spans=spans)
    canon = make_canon({})
    profile = make_profile()
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "jordan", "display": "the River Jordan"},
    ]})

    out_dir, _manifest = render_into(module, tmp_path, nodestream, canon, profile)

    assert read(out_dir, "People/jordan.md").rstrip().endswith("# the River Jordan")
    assert read(out_dir, "Places/Jordan.md").rstrip().endswith("# Jordan")


# ===========================================================================
# 6. NFD label in the sidecar resolves the NFC identity; the display value
#    is written EXACTLY as ruled, never re-normalized.
# ===========================================================================

def test_nfd_label_matches_the_nfc_identity_and_display_is_written_unnormalized(tmp_path):
    nfc_form = "R" + chr(0xE9)  # "Ré", precomposed (NFC)
    nfd_form = unicodedata.normalize("NFD", nfc_form)  # "Re" + combining acute
    assert nfc_form != nfd_form
    assert unicodedata.normalize("NFC", nfd_form) == nfc_form

    root, module = stage_durable_root(tmp_path)
    nodes = [make_node("p1", "seg01", f"{ent(1, nfc_form)} spoke.")]
    nodestream = make_nodestream(nodes, spans={"1": span("person", nfc_form)})
    canon = make_canon({})
    profile = make_profile()
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": nfd_form, "display": nfd_form},
    ]})

    out_dir, manifest = render_into(module, tmp_path, nodestream, canon, profile)

    (relpath,) = entity_note_relpaths(manifest)
    raw = read(out_dir, relpath)
    assert nfd_form in raw, (
        f"the display must be written EXACTLY as ruled, in NFD, never "
        f"re-normalized -- got:\n{raw!r}"
    )
    assert f"# {nfd_form}" in raw.splitlines()
    fm = parse_frontmatter(raw)
    assert fm["name"] == nfc_form, "the identity's own label stays the NFC form the spans carry"
    assert fm["display"] == nfd_form
    assert manifest["entity_markup"]["displays"] == 1


# ===========================================================================
# 7. render_obsidian.py's own CLI (main()), as a real subprocess.
# ===========================================================================

def _write_cli_profile(root, profile):
    (root / "profile.yml").write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )


def _run_render_cli(root, tmp_path, out_dir):
    """Write `base_fixture()`'s nodestream and canon to disk and run the
    staged `render_obsidian.py` on them as a real subprocess. Returns
    `(proc, payload)`, `payload` being the single JSON line on stdout."""
    nodestream, canon, _profile = base_fixture()
    nodestream_path = tmp_path / "nodestream.json"
    canon_path = tmp_path / "canon.json"
    nodestream_path.write_text(json.dumps(nodestream), encoding="utf-8")
    canon_path.write_text(json.dumps(canon), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "render_obsidian.py"),
         "--nodestream", str(nodestream_path), "--canon", str(canon_path),
         "--out-dir", str(out_dir)],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return proc, json.loads(lines[0])


def test_cli_subprocess_with_a_valid_sidecar_reports_one_identity(tmp_path):
    root, _module = stage_durable_root(tmp_path)
    _write_cli_profile(root, make_profile())
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "noson", "display": "Reb Noson"},
    ]})
    out_dir = tmp_path / "cli-out"

    proc, payload = _run_render_cli(root, tmp_path, out_dir)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert payload["entity_markup"]["displays"] == 1
    identities = payload["entity_markup"]["identities"]
    assert identities and any(i["display"] == "Reb Noson" for i in identities), identities
    # Delivery, not only the manifest: the shipped CLI must have WRITTEN the
    # ruled heading, or a stub returning the expected manifest would pass here.
    note = (out_dir / "People" / "noson.md").read_text(encoding="utf-8")
    assert note.rstrip().endswith("\n# Reb Noson"), note
    assert parse_frontmatter(note)["display"] == "Reb Noson"


def test_cli_subprocess_with_a_malformed_sidecar_exits_1_and_preserves_the_vault(tmp_path):
    root, module = stage_durable_root(tmp_path)
    _write_cli_profile(root, make_profile())
    write_sidecar(root, {"displays": "not-a-list"})
    out_dir = make_managed_vault(module, tmp_path)

    proc, payload = _run_render_cli(root, tmp_path, out_dir)
    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert payload.get("success") is False
    assert payload.get("reason") == "markup_display_invalid", payload
    assert (out_dir / "SURVIVOR.md").is_file(), (
        "the pre-existing vault must survive a refused render"
    )


# ===========================================================================
# 8. assemble.py, the real W9 path, as a real subprocess (fixture staging
#    restated from tests/entity_markup_assemble.test.py).
# ===========================================================================

ASM_FN_PH_1 = "⟦FNREF_1⟧"
ASM_V_PH_A = "⟦VERSE_vA_abc12345⟧"
ASM_V_PH_B = "⟦VERSE_vB_def67890⟧"
ASM_PERSON_INDEX = {"tags": ["person", "place"], "index_from": "markup"}
ASM_VAULT_MARKER_FILENAME = ".literary-translator-vault.json"


class _AsmOmit:
    def __repr__(self):
        return "<omit>"


_ASM_OMIT = _AsmOmit()


def asm_default_profile(output_target="obsidian", entity_markup=_ASM_OMIT):
    output_cfg = {
        "v1_scope": "assembled_book",
        "destination": "/placeholder/out/",
        "target": output_target,
        "name_display": {"parenthetical_originals": "never"},
        "adapter_config": {
            # Non-empty, unlike tests/entity_markup_assemble.test.py's own
            # default ({}) -- an out-of-catalog category routes to the
            # `other/` fallback folder (`DEFAULT_FOLDER`), and this file's
            # assemble tests read the rendered note by an explicit path.
            "obsidian": {"folders": FOLDERS, "mentions_section": {"enabled": False}},
            "epub": None, "custom": None,
        },
    }
    if entity_markup is not _ASM_OMIT:
        output_cfg["entity_markup"] = entity_markup
    return {
        "profile_version": 1,
        "project": {"title": "Test Book", "durable_root": "/placeholder",
                    "pipeline_version": "v1", "max_segment_words": 15000},
        "source": {
            "format": "plain_text", "path": "/logical/source.txt", "gutenberg_id": None,
            "language": {"code": "fr", "particle_config": "fr_test.json",
                         "smoke_test": {"report_path": None}},
            "adapter_config": {
                "gutenberg_epub": None,
                "plain_text": {
                    "segmentation": {"method": "blank_line_run", "blank_line_threshold": 2,
                                      "heading_regex": None},
                    "verse_detection": "none_confirmed", "verse_regex": None,
                    "footnotes": "none_confirmed", "footnote_anchor_regex": None,
                    "footnote_def_regex": None,
                },
                "custom": {"extractor_path": None},
            },
        },
        "target": {"language": {"code": "ru", "register_notes": "informal"}},
        "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
        "engine": {"effort": "high", "max_fix_rounds": 4, "batch_agent_cap": 1000},
        "footnotes": {"apparatus_policy": "translate_all"},
        "glossary": {"research_mode": "live"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
        "output": output_cfg,
    }


def asm_write_cache_key_inputs(root, scripts_dir):
    for name, body in (("bootstrap_names.py", b"# bootstrap_names.py fixture\n"),
                        ("segpack.py", b"# segpack.py fixture\n")):
        if not (scripts_dir / name).exists():
            (scripts_dir / name).write_bytes(body)
    (root / "style_bible.md").write_bytes(
        b"# Style Bible\n\n<!-- STYLE_CONTRACT_BEGIN -->\n"
        b"Formal register, Oxford comma.\n<!-- STYLE_CONTRACT_END -->\n"
    )
    (root / "translate_TASK.md").write_bytes(b"TRANSLATE TASK PROMPT v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW TASK PROMPT v1\n")
    (root / "extract.py").write_bytes(b"# extract.py fixture v1\n")
    (root / "source.txt").write_bytes(b"Ceci est un texte source de test.\n")
    languages_dir = root / "languages"
    languages_dir.mkdir(exist_ok=True)
    (languages_dir / "fr_test.json").write_text(
        json.dumps({"PARTICLES": ["de", "du", "des"], "STOPWORDS": ["le", "la", "les"],
                    "has_elision": False, "ELISION_RE": None}),
        encoding="utf-8",
    )
    (root / "schemas").mkdir(exist_ok=True)
    for name in ("draft.schema.json", "review.schema.json", "segpack.schema.json"):
        (root / "schemas" / name).write_bytes(b"{}\n")
    runs_dir = root / "runs"
    runs_dir.mkdir(exist_ok=True)
    (runs_dir / ".plugin_bundle_hash").write_text("test-plugin-bundle-marker-v1\n", encoding="utf-8")


def asm_real_cache_key(root, seg):
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "cache_key.py"), "--seg", seg],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"fixture setup: cache_key.py --seg {seg} failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def asm_draft_content_sha1_of(doc):
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(projected, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def asm_make_root(tmp_path, output_target="obsidian", entity_markup=_ASM_OMIT):
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC,
                CACHE_KEY_SRC, JSON_STDOUT_SRC):
        shutil.copy2(src, scripts_dir / src.name)

    profile = asm_default_profile(output_target=output_target, entity_markup=entity_markup)
    profile["project"]["durable_root"] = str(root)
    profile["output"]["destination"] = str(root / "out")
    (root / "profile.yml").write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )
    (root / "canon.json").write_text(
        json.dumps({"entries": {}, "review_queue": [],
                    "generation_hashes": {"particle_config_hash": "x",
                                           "derivation_bundle_hash": "y"}}),
        encoding="utf-8",
    )
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    asm_write_cache_key_inputs(root, scripts_dir)

    blocks = {
        "h1": {"id": "h1", "type": "HEAD", "seg": "seg01", "order_index": 0,
               "plain_text": "Chapitre un", "sha1": hashlib.sha1(b"h1").hexdigest(),
               "source_file": "source.txt"},
        "p1": {"id": "p1", "type": "PARA", "seg": "seg01", "order_index": 1,
               "plain_text": "Prose une.", "fnrefs": [1],
               "sha1": hashlib.sha1(b"p1").hexdigest(), "source_file": "source.txt"},
    }
    manifest = {
        "blocks": blocks,
        "spine": [{"pos": 0, "file": "source.txt", "klass": "body"}],
        "segments": [{"seg": "seg01", "kind": "body", "title_text": "Chapitre un",
                      "block_ids": ["h1", "p1"], "word_count": 100}],
        "footnotes": [{"n": 1, "anchor_block": "p1", "anchor_seg": "seg01", "def_block": "FN1"}],
        "frontback": [], "verse": {"store": []},
        "source_inputs": ["source.txt"],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    blocks["FN1"] = {"id": "FN1", "type": "FN", "seg": None, "order_index": 2,
                      "plain_text": "Texte de la note.",
                      "sha1": hashlib.sha1(b"FN1").hexdigest(), "source_file": "source.txt"}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    segpack = {
        "seg": "seg01", "title": "seg01", "kind": "body", "word_count": 10,
        "blocks": [{"id": "h1", "order_index": 0, "plain_text": "Chapitre un"},
                   {"id": "p1", "order_index": 1, "plain_text": "Prose une."}],
        "footnotes": [{"n": 1, "source_text": "Texte de la note."}],
        "verses": [], "names": [], "canon_names": [], "new_names": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y",
                               "particle_config_hash": "x", "derivation_bundle_hash": "y"},
    }
    (root / "segments" / "segpack_seg01.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8")

    draft = {
        "seg": "seg01",
        "blocks": {
            "h1": "Chapitre un",
            "p1": (f'Prose <person ref="aharon">the Rav</person> and '
                   f'<person ref="aharon">R. Aharon</person> {ASM_FN_PH_1} done.'),
        },
        "footnotes": {"1": "Footnote definition text."},
        "verses": {}, "names": [], "notes": [],
    }
    (root / "segments" / "seg01.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8")

    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": {"seg01": {
            "timestamp": "2026-01-01T00:00:00+00:00", "status": "converged", "rounds": 1,
            "cache_key": asm_real_cache_key(root, "seg01"),
            "n_blocks": 2, "n_footnotes": 1, "n_verses": 0,
            "reviewed_draft_sha1": asm_draft_content_sha1_of(draft),
        }}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def asm_run_assemble(root, timeout=120):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "assemble.py")],
        capture_output=True, text=True, timeout=timeout,
    )


def asm_parse_one_json_line(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def test_assemble_subprocess_with_a_valid_ruling_renders_the_display(tmp_path):
    root = asm_make_root(tmp_path, entity_markup=ASM_PERSON_INDEX)
    write_sidecar(root, {"displays": [
        {"tag": "person", "label": "aharon", "display": "R. Aharon"},
    ]})

    proc = asm_run_assemble(root)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = asm_parse_one_json_line(proc)
    assert payload["success"] is True, payload
    entity_markup = payload["adapter_result"]["entity_markup"]
    assert entity_markup["displays"] == 1
    assert entity_markup["identities"], entity_markup

    heading = (root / "out" / "People" / "aharon.md").read_text(encoding="utf-8")
    assert heading.rstrip().endswith("# R. Aharon"), heading


def test_assemble_subprocess_with_a_malformed_sidecar_exits_1_and_preserves_the_vault(tmp_path):
    root = asm_make_root(tmp_path, entity_markup=ASM_PERSON_INDEX)
    out_dir = root / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ASM_VAULT_MARKER_FILENAME).write_text(
        json.dumps({"managed_by": "literary-translator", "target": "obsidian"}) + "\n",
        encoding="utf-8",
    )
    (out_dir / "SURVIVOR.md").write_text("pre-existing vault content\n", encoding="utf-8")

    write_sidecar(root, {"displays": "not-a-list"})

    proc = asm_run_assemble(root)
    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = asm_parse_one_json_line(proc)
    assert payload["success"] is False
    # assemble.py's dispatch_adapter wraps every RenderError as an
    # AssembleError WITHOUT a `reason` field (pre-existing for every render
    # refusal, not changed by #925) -- `payload` therefore carries no
    # `reason` key at all. The message text names the sidecar file so an
    # operator can find it, but does NOT embed the machine-readable reason
    # slug (measured: "adapter render() failed: <path>'s \"displays\" key
    # must be a list, got str ...", no "markup_display_invalid" substring).
    assert "reason" not in payload, payload
    assert "markup_display.json" in payload.get("error", ""), payload
    assert (out_dir / "SURVIVOR.md").is_file(), (
        "a refused render must leave the pre-existing vault untouched"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
