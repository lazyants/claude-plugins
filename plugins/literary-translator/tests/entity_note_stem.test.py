"""tests/entity_note_stem.test.py -- #930's profile knob,
`output.adapter_config.obsidian.entity_note_stem`, that lets a project name
canon entity notes after the entry's `canonical_target_form` (the
target-language rendering) instead of the resolved default,
`source_form` (the entries{} key, i.e. the original-script identity).

## What #930 changed and what it did not

Only the entity note's FILENAME STEM moves. The note's frontmatter
(`source_form`, `aliases: [source_form]`, `canonical_target_form`), its H1
heading, and the wikilink DISPLAY text are unchanged under both values --
this file's cases 7 and 10 assert that alongside the stem itself, not just
the stem in isolation. Collisions (two entries sharing one target form, or
differing only in case) are handled by the pre-existing `_dedupe_path`
(NFC+casefold, deterministic `-<n>` suffix in `sorted(entries)` order,
#99) -- cases 4 and 5 pin that under the new stem field, never a fuse or
an overwrite. Markup-driven notes (`output.entity_markup`,
`index_from: markup`) keep picking their stem from the span label, but
share ONE collision set with canon notes, and canon notes are allocated
FIRST -- case 10 pins the resulting cross-kind collision under both knob
values.

The knob is resolved in exactly one place, `render_obsidian.py`'s
`_entity_note_stem_field()`, which `validate_backlinks.py` also calls
(case 8 is the one seam test feeding the renderer's actual note paths into
that consumer) so the two can never disagree.

## Invocation style

Cases 1-7 and 10 import `render_obsidian` directly via
`importlib.util.spec_from_file_location`, exactly as
`tests/entity_markup_render.test.py` does -- the fixture builders below
(`ent`, `span`, `make_node`, `make_nodestream`, `canon_entry`, `make_canon`,
`make_profile`, `render_into`) copy that file's shapes. Case 8 loads
`tests/validate_backlinks.test.py` itself by path (the same
`importlib.util.spec_from_file_location` idiom
`tests/backlink_integrity_e2e.test.py` uses to load its own case_spec
module) and reuses its `durable_root` fixture builders, rather than
reimplementing that subprocess bed here. Case 9 loads `profile_validate.py`
directly and drives its real Draft202012Validator pass, mirroring
`tests/profile_validate.test.py`'s own `schema_errors()`/`make_base_profile()`
pattern.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"
PROFILE_VALIDATE_SRC = SCRIPTS_SRC_DIR / "profile_validate.py"
PROFILE_SCHEMA_SRC = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas" / "profile.schema.json"
)
VALIDATE_BACKLINKS_TEST_SRC = PLUGIN_ROOT / "tests" / "validate_backlinks.test.py"
PROFILE_EXAMPLE_VALIDATION_TEST_SRC = PLUGIN_ROOT / "tests" / "profile_example_validation.test.py"

for _src in (RENDER_OBSIDIAN_SRC, PROFILE_VALIDATE_SRC, PROFILE_SCHEMA_SRC, VALIDATE_BACKLINKS_TEST_SRC,
             PROFILE_EXAMPLE_VALIDATION_TEST_SRC):
    assert _src.is_file(), f"required fixture source not found at {_src}"


def _load_module_by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_obsidian = _load_module_by_path("render_obsidian_under_entity_note_stem_test", RENDER_OBSIDIAN_SRC)
RenderError = render_obsidian.RenderError

# The validate_backlinks.py seam (case 8): loaded as a whole TEST MODULE, not
# copied, so its durable_root fixture builders (`make_root`, `write_canon`,
# `canon_entry`, `write_nodestream`, `set_aggregate`, `write_note`,
# `raw_entity_note`, `mentions_block`, `raw_segment_note`, `run_gate`,
# `report_of`) stay the single source of truth for that bed's shape. This
# import executes that file's module-level code (path constants, one
# existence-assert loop, a stub-source string literal) but registers no
# pytest items of its own -- pytest collects tests by scanning the
# filesystem, not by walking already-imported modules.
vb = _load_module_by_path("validate_backlinks_test_module_for_entity_note_stem_test", VALIDATE_BACKLINKS_TEST_SRC)

FOLDERS = {"person": "People", "place": "Places"}


# ---------------------------------------------------------------------------
# Fixture builders -- copies tests/entity_markup_render.test.py's shapes.
# ---------------------------------------------------------------------------

def ent(n, payload):
    """The exact three-part sequence assemble.py emits in `index` mode."""
    return f"⟦ENT_{n}⟧{payload}⟦/ENT_{n}⟧"


def span(tag, payload, ref=None):
    record = {"tag": tag, "payload": payload}
    if ref is not None:
        record["ref"] = ref
    return record


def make_node(node_id, seg, text, kind="prose", raw_type="PARA", order_index=0):
    return {
        "id": node_id, "seg": seg, "kind": kind, "raw_type": raw_type,
        "order_index": order_index, "medium": "plain", "text": text,
        "fnrefs": [], "verses": [],
    }


def make_nodestream(nodes, spans=None, target="ru"):
    """`spans=None` leaves the `entity_markup` key OFF entirely (plain-text
    canon scanning, used by cases 2-7). `spans={...}` declares marked
    entities in `index` mode (case 10)."""
    nodestream = {
        "book": {"seg_order": sorted({n["seg"] for n in nodes}), "title": "Test Book"},
        "nodes": nodes,
        "footnotes": [],
        "meta": {"target": target, "verse_mode": "literal_only", "apparatus_policy": "translate_all"},
    }
    if spans is not None:
        nodestream["entity_markup"] = {"spans": spans}
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


def make_profile(folders=None, entity_note_stem=None, entity_markup=False,
                  tags=("person", "place"), index_from="markup"):
    """`entity_note_stem` (#930), when given, is written to
    `output.adapter_config.obsidian.entity_note_stem` directly -- absent
    (the default, `None`) leaves the key off the profile entirely, which is
    the state the knob must resolve to `source_form` from."""
    obsidian_cfg = {"folders": FOLDERS if folders is None else folders,
                    "mentions_section": {"enabled": False}}
    if entity_note_stem is not None:
        obsidian_cfg["entity_note_stem"] = entity_note_stem
    output = {
        "v1_scope": "assembled_book",
        "target": "obsidian",
        "name_display": {"parenthetical_originals": "never"},
        "adapter_config": {"obsidian": obsidian_cfg},
    }
    if entity_markup:
        block = {"tags": list(tags)}
        if index_from is not None:
            block["index_from"] = index_from
        output["entity_markup"] = block
    return {"target": {"language": {"code": "ru"}}, "output": output}


def render_into(tmp_path, nodestream, canon, profile, out_dir=None):
    out_dir = out_dir or (tmp_path / "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = render_obsidian.render(nodestream, canon, profile, out_dir)
    return out_dir, manifest


def read(out_dir, relpath):
    return (out_dir / relpath).read_text(encoding="utf-8")


def segment_note_texts(out_dir):
    """Every rendered NARRATIVE page's text -- segment notes are the only
    *.md files at the vault ROOT (entity and markup notes are always
    foldered)."""
    return [p.read_text(encoding="utf-8") for p in sorted(out_dir.iterdir())
            if p.is_file() and p.suffix == ".md"]


def parse_frontmatter(text):
    assert text.startswith("---"), f"expected YAML frontmatter, got:\n{text[:200]!r}"
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"malformed frontmatter block:\n{text[:200]!r}"
    return yaml.safe_load(parts[1]) or {}


def entity_note_relpaths(manifest):
    """Every written note that is NOT a segment note (i.e. lives in a
    folder)."""
    return sorted(rel for rel in manifest["written"] if "/" in rel)


def entity_note_identity(out_dir, manifest, source_form):
    """The exact relpath (minus '.md') of the entity note ACTUALLY EMITTED
    for `source_form` -- found by matching each written file's own
    frontmatter `source_form` field, never guessed or re-derived from the
    internal resolver under test. Copies
    tests/render_obsidian.test.py's own helper of the same name."""
    for rel in manifest["written"]:
        p = out_dir / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        if parse_frontmatter(text).get("source_form") == source_form:
            return rel[: -len(".md")] if rel.endswith(".md") else rel
    raise AssertionError(
        f"no emitted entity note found with source_form={source_form!r} "
        f"among written paths: {manifest['written']}"
    )


def _profile_with_stem_key(value, present=True):
    obsidian_cfg = {}
    if present:
        obsidian_cfg["entity_note_stem"] = value
    return {"output": {"adapter_config": {"obsidian": obsidian_cfg}}}


# ===========================================================================
# 1. `_entity_note_stem_field` -- the single resolution point (module
#    constants first).
# ===========================================================================

def test_module_constants_name_the_two_legal_fields_and_the_default():
    assert render_obsidian.ENTITY_NOTE_STEM_FIELDS == ("source_form", "canonical_target_form")
    assert render_obsidian.DEFAULT_ENTITY_NOTE_STEM == "source_form"


@pytest.mark.parametrize("profile", [{}, {"output": {}}, None])
def test_entity_note_stem_field_absent_key_resolves_to_source_form(profile):
    assert render_obsidian._entity_note_stem_field(profile) == "source_form"


def test_entity_note_stem_field_explicit_source_form_passes_through():
    profile = _profile_with_stem_key("source_form")
    assert render_obsidian._entity_note_stem_field(profile) == "source_form"


def test_entity_note_stem_field_explicit_canonical_target_form_passes_through():
    profile = _profile_with_stem_key("canonical_target_form")
    assert render_obsidian._entity_note_stem_field(profile) == "canonical_target_form"


@pytest.mark.parametrize("value", ["filename", None], ids=["unknown_string", "explicit_null"])
def test_entity_note_stem_field_rejects_a_present_key_outside_the_enum(value):
    """A PRESENT key whose value is outside the enum is refused -- including
    an explicit `null`, which must not silently fall back to the default:
    membership in ENTITY_NOTE_STEM_FIELDS, never `.get(...) is None`, is
    what makes this fire; a schema-invalid null must never resolve to a
    stem the operator did not ask for."""
    profile = _profile_with_stem_key(value, present=True)
    with pytest.raises(RenderError) as excinfo:
        render_obsidian._entity_note_stem_field(profile)
    assert excinfo.value.reason == "entity_note_stem_invalid"
    message = str(excinfo.value)
    assert "output.adapter_config.obsidian.entity_note_stem" in message, message
    assert "source_form" in message and "canonical_target_form" in message, message


# ===========================================================================
# 2-6. `_resolve_entity_notes` / `_entity_note_relpath` unit cases.
# ===========================================================================

def test_resolve_entity_notes_default_stem_is_source_form_byte_identical():
    """Byte-identical to the pre-#930 relpath for a Hebrew-keyed canon --
    the default resolves to `source_form` whether or not `stem_field` is
    passed at all."""
    entries = {"אברהם": canon_entry("אברהם", "Abraham")}
    assert render_obsidian._resolve_entity_notes(entries, FOLDERS) == {
        "אברהם": "People/אברהם.md"
    }


def test_resolve_entity_notes_canonical_target_form_stem_is_latin_folder_unchanged():
    entries = {"אברהם": canon_entry("אברהם", "Abraham")}
    result = render_obsidian._resolve_entity_notes(entries, FOLDERS, stem_field="canonical_target_form")
    assert result == {"אברהם": "People/Abraham.md"}


def test_resolve_entity_notes_shared_target_form_dedupes_by_source_form_sort_order():
    """Two entries sharing one `canonical_target_form` -> `X.md` and
    `X-2.md`, the bare name going to the lexicographically-FIRST
    `source_form` -- both present in the returned map under their TRUE
    `source_form` keys."""
    entries = {"Zzz": canon_entry("Zzz", "X"), "Aaa": canon_entry("Aaa", "X")}
    result = render_obsidian._resolve_entity_notes(entries, FOLDERS, stem_field="canonical_target_form")
    assert result == {"Aaa": "People/X.md", "Zzz": "People/X-2.md"}
    assert set(result) == {"Aaa", "Zzz"}, "both TRUE source_form keys must be present"


def test_resolve_entity_notes_case_only_target_collision_gets_suffix():
    """Case-only difference -- proves the NFC+casefold folded key fires,
    not just plain string inequality."""
    entries = {
        "our Rebbe": canon_entry("our Rebbe", "our Rebbe"),
        "Our Rebbe": canon_entry("Our Rebbe", "Our Rebbe"),
    }
    result = render_obsidian._resolve_entity_notes(entries, FOLDERS, stem_field="canonical_target_form")
    assert result == {
        "Our Rebbe": "People/Our Rebbe.md",
        "our Rebbe": "People/our Rebbe-2.md",
    }, "expected the -2 SUFFIX on the collision, not just two distinct paths"


@pytest.mark.parametrize("entry_kwargs", [
    {"canonical_target_form": ""},
    {"canonical_target_form": "   "},
    {},
])
def test_resolve_entity_notes_empty_blank_or_absent_target_form_falls_back_to_source_form(entry_kwargs):
    """Empty, whitespace-only ("blank"), or absent `canonical_target_form`
    all fall back to `source_form` for the stem -- every entry still gets a
    note under a name that identifies it."""
    entry = canon_entry("שלום", "")
    entry.update(entry_kwargs)
    entries = {"שלום": entry}
    result = render_obsidian._resolve_entity_notes(entries, FOLDERS, stem_field="canonical_target_form")
    assert result == {"שלום": "People/שלום.md"}


# ===========================================================================
# 7. Full render() under the knob: every entity note has a Latin stem,
#    frontmatter/H1/link-display stay unchanged, link target agrees with
#    the emitted filename -- then the same canon under the default profile.
# ===========================================================================

def test_render_full_vault_uses_latin_stems_under_the_knob_frontmatter_and_link_agree(tmp_path):
    source_form = "שלום"
    canon = make_canon({source_form: canon_entry(source_form, "Shalom", category="person")})
    ns = make_nodestream([make_node("p1", "seg01", "He said Shalom to them.")])
    profile = make_profile(entity_markup=False, entity_note_stem="canonical_target_form")

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity = entity_note_identity(out_dir, manifest, source_form)
    assert identity == "People/Shalom", f"expected a Latin stem, got {identity!r}"

    fm = parse_frontmatter(read(out_dir, f"{identity}.md"))
    assert fm["source_form"] == source_form, "frontmatter source_form must stay the original-script identity"
    assert fm["aliases"] == [source_form], "aliases must still carry the raw source_form"
    assert fm["canonical_target_form"] == "Shalom"

    body_texts = segment_note_texts(out_dir)
    assert len(body_texts) == 1
    assert f"[[{identity}|Shalom]]" in body_texts[0], f"got:\n{body_texts[0]}"
    note_text = read(out_dir, f"{identity}.md")
    assert "# Shalom" in note_text.splitlines(), (
        f"the H1 heading must stay canonical_target_form under the knob too:\n{note_text}"
    )

    default_out_dir, default_manifest = render_into(
        tmp_path, ns, canon, make_profile(entity_markup=False), out_dir=tmp_path / "out_default"
    )
    default_identity = entity_note_identity(default_out_dir, default_manifest, source_form)
    assert default_identity == f"People/{source_form}", (
        f"the default (knob absent) must keep the Hebrew stem, got {default_identity!r}"
    )
    default_fm = parse_frontmatter(read(default_out_dir, f"{default_identity}.md"))
    assert default_fm["source_form"] == source_form
    assert default_fm["canonical_target_form"] == "Shalom"
    default_note_text = read(default_out_dir, f"{default_identity}.md")
    assert "# Shalom" in default_note_text.splitlines(), (
        f"the H1 is canonical_target_form regardless of the stem knob:\n{default_note_text}"
    )


# ===========================================================================
# 8. Seam test: validate_backlinks.py resolves through the SAME knob.
# ===========================================================================

def _write_profile_entity_note_stem(root, value):
    """Rewrites the fixture's already-written profile.yml (from
    vb.make_root) to add `output.adapter_config.obsidian.entity_note_stem`,
    leaving every other key make_root() wrote untouched."""
    profile_path = root / "profile.yml"
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    profile["output"]["adapter_config"]["obsidian"]["entity_note_stem"] = value
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")


def _target_stem_vault(tmp_path):
    """One canon entry Ivan -> Ivanko, mentioned in seg01, whose entity note
    is written at the TARGET-form stem `People/Ivanko.md`. Whether the gate
    finds that note is decided by the profile's `entity_note_stem` alone --
    the two seam cases below share this vault and differ only in that key."""
    root = vb.make_root(tmp_path, folders={"person": "People"})
    vb.write_canon(root, {"Ivan": vb.canon_entry("Ivan", "Ivanko", category="person")})
    vb.write_nodestream(root, ["seg01"])
    vb.set_aggregate(root, {
        "eligible_by_source_form": {"Ivan": [{"source_form": "Ivan", "seg": "seg01", "origin": "block"}]},
        "unresolved_homonyms": {},
    })
    vb.write_note(root, "People/Ivanko.md", vb.raw_entity_note(
        source_form="Ivan", canonical_target_form="Ivanko",
        body_lines=vb.mentions_block(["001 seg01"]),
    ))
    vb.write_note(root, "001 seg01.md", vb.raw_segment_note())
    return root


def test_validate_backlinks_resolves_the_target_stem_note_and_reports_full_coverage(tmp_path):
    root = _target_stem_vault(tmp_path)
    _write_profile_entity_note_stem(root, "canonical_target_form")

    proc = vb.run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = vb.report_of(proc)
    assert report["mentions_coverage"] == {"status": "enabled", "checked_entities": 1, "missing": []}
    assert report["warnings"] == 0


def test_validate_backlinks_without_the_knob_reports_the_target_stem_note_missing(tmp_path):
    """The SAME vault (entity note written at the TARGET-form stem) but a
    profile.yml carrying no `entity_note_stem` key -- the gate resolves
    `source_form` and never finds `People/Ivanko.md`. Proves the gate
    actually reads the key rather than passing vacuously."""
    root = _target_stem_vault(tmp_path)

    proc = vb.run_gate(root)
    assert proc.returncode == 1
    report = vb.report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}]
    assert report["warnings"] == 1


# ===========================================================================
# 9. profile_validate.py Step 6 (schema) accepts/rejects the enum.
# ===========================================================================

pv = _load_module_by_path("profile_validate_under_entity_note_stem_test", PROFILE_VALIDATE_SRC)
pv.dependency_preflight()
SCHEMA = pv.load_profile_schema()


def _base_profile_with_obsidian_output(entity_note_stem=None):
    """A fully schema-valid profile (mirrors
    tests/profile_validate.test.py's own make_base_profile(), extended with
    an obsidian output block so `adapter_config.obsidian.entity_note_stem`
    is reachable)."""
    profile = {
        "profile_version": 1,
        "project": {
            "title": "A Real Book Title",
            "durable_root": "/some/real/project",
            "pipeline_version": "v1",
            "max_segment_words": 15000,
        },
        "source": {
            "format": "gutenberg_epub",
            "path": "/some/real/project/source.epub",
            "gutenberg_id": None,
            "language": {
                "code": "fr",
                "particle_config": "fr.json",
                "smoke_test": {"report_path": None},
            },
            "adapter_config": {
                "gutenberg_epub": {"spine_overrides": {}, "frontback_overrides": {}},
                "plain_text": {
                    "segmentation": {
                        "method": "blank_line_run",
                        "blank_line_threshold": 2,
                        "heading_regex": None,
                    },
                    "verse_detection": "none_confirmed",
                    "verse_regex": None,
                    "footnotes": "none_confirmed",
                    "footnote_anchor_regex": None,
                    "footnote_def_regex": None,
                },
                "custom": {"extractor_path": None},
            },
        },
        "target": {"language": {"code": "ru", "register_notes": "informal"}},
        "verse_policy": {"mode": "literal_only", "threshold_lines": None},
        "engine": {"effort": "high", "max_fix_rounds": 4, "batch_agent_cap": 1000},
        "footnotes": {"apparatus_policy": "translate_all"},
        "glossary": {"research_mode": "live"},
        "validation": {"untranslated_sentinel": "no translation"},
        "output": {
            "v1_scope": "assembled_book",
            "destination": "/some/real/project/out/",
            "target": "obsidian",
            "adapter_config": {"obsidian": {"folders": {}}},
        },
    }
    if entity_note_stem is not None:
        profile["output"]["adapter_config"]["obsidian"]["entity_note_stem"] = entity_note_stem
    return profile


def test_profile_validate_baseline_obsidian_profile_is_schema_valid():
    assert pv.validate_against_schema(_base_profile_with_obsidian_output(), SCHEMA) == []


def test_profile_validate_accepts_canonical_target_form():
    errors = pv.validate_against_schema(
        _base_profile_with_obsidian_output("canonical_target_form"), SCHEMA
    )
    assert errors == []


def test_profile_validate_accepts_source_form():
    errors = pv.validate_against_schema(
        _base_profile_with_obsidian_output("source_form"), SCHEMA
    )
    assert errors == []


def test_profile_validate_rejects_an_out_of_enum_value_naming_the_path():
    errors = pv.validate_against_schema(
        _base_profile_with_obsidian_output("filename"), SCHEMA
    )
    assert errors != []
    assert any("output.adapter_config.obsidian.entity_note_stem" in e for e in errors), errors


def test_profile_validate_cli_subprocess_rejects_entity_note_stem_out_of_enum(tmp_path):
    """The real profile_validate.py CLI entry point (subprocess, house style
    -- see tests/profile_example_validation.test.py's own module docstring
    on why the CLI is exercised directly, not just validate_against_schema()
    in isolation), driven against a FILLED-IN copy of profile.example.yml
    (that file's own `_build_filled_profile()`, loaded by path rather than
    duplicated here -- the verbatim shipped example would fail Step 5's
    placeholder scan before ever reaching Step 6's schema check, so its
    output would never mention entity_note_stem at all) carrying an invalid
    `entity_note_stem`."""
    pev = _load_module_by_path(
        "profile_example_validation_test_module_for_entity_note_stem_test",
        PROFILE_EXAMPLE_VALIDATION_TEST_SRC,
    )
    durable_root = tmp_path / "project"
    durable_root.mkdir()
    source_path = tmp_path / "source.epub"
    profile = pev._build_filled_profile(durable_root, source_path)
    profile.setdefault("output", {}).setdefault("adapter_config", {})["obsidian"] = {
        "entity_note_stem": "filename"
    }
    profile_path = tmp_path / "profile.yml"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(PROFILE_VALIDATE_SRC), "--profile", str(profile_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "entity_note_stem" in proc.stderr, proc.stderr


# ===========================================================================
# 10. Cross-kind collision: a canon note and an independently-minted markup
#     note land on the SAME target-form stem. Canon is allocated FIRST, so
#     the markup note takes the -<n> suffix, under either knob value.
# ===========================================================================

def test_markup_note_colliding_with_a_canonical_target_form_stem_takes_the_suffix(tmp_path):
    """Canon entry תקווה -> Hope (basis sense_translated, so it is not
    composed into the canon note and the markup span below resolves to an
    INDEPENDENTLY MINTED markup note -- see
    tests/entity_markup_render.test.py's own
    test_markup_note_colliding_with_a_canon_note_is_deduped_and_canon_keeps_its_path
    for the identical John/Jonathan shape this copies). Under the default,
    the canon note's Hebrew stem does not collide with the markup label
    'Hope', so both keep bare names. Under `canonical_target_form`, the
    canon note's stem also becomes 'Hope' -- now colliding with the markup
    note -- and because render() allocates canon paths BEFORE markup paths
    through the same collision set, canon keeps the bare name and the
    markup note gets the -2 suffix."""
    source_form = "תקווה"
    canon = make_canon({
        source_form: canon_entry(source_form, "Hope", category="person", basis="sense_translated")
    })
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Hope')} arrived.")]
    spans = {"1": span("person", "Hope")}

    default_dir, default_manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=spans), canon,
        make_profile(entity_markup=True, index_from="markup"),
        out_dir=tmp_path / "out_default",
    )
    assert entity_note_relpaths(default_manifest) == ["People/Hope.md", f"People/{source_form}.md"]
    default_body = segment_note_texts(default_dir)[0]
    assert "[[People/Hope|Hope]] arrived." in default_body, f"got:\n{default_body}"

    stem_dir, stem_manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=spans), canon,
        make_profile(entity_markup=True, index_from="markup", entity_note_stem="canonical_target_form"),
        out_dir=tmp_path / "out_stem",
    )
    assert entity_note_relpaths(stem_manifest) == ["People/Hope-2.md", "People/Hope.md"]
    canon_fm = parse_frontmatter(read(stem_dir, "People/Hope.md"))
    assert canon_fm["source_form"] == source_form, "the CANON note keeps the unsuffixed path"
    markup_fm = parse_frontmatter(read(stem_dir, "People/Hope-2.md"))
    assert markup_fm["name"] == "Hope"
    stem_body = segment_note_texts(stem_dir)[0]
    assert "[[People/Hope-2|Hope]] arrived." in stem_body, f"got:\n{stem_body}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
