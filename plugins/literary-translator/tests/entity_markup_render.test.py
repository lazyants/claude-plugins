"""tests/entity_markup_render.test.py -- #795's RENDER half: the
`output.entity_markup` knob as `render_obsidian.py` consumes it.

## What this file owns

`assemble.py` owns the SCAN (it parses the operator's declared
`<person ref="…">…</person>` grammar, refuses what it cannot render safely,
and records what it found); this file owns everything downstream of the
sentinel form it emits. Its own gates are pinned in
`tests/entity_markup_assemble.test.py`.

The seam, restated so a failure here is diagnosable without opening the
producer: in `index` mode each declared element has become
`⟦ENT_{n}⟧{payload}⟦/ENT_{n}⟧` in the nodestream text, and the nodestream
carries exactly one new key,
`entity_markup = {"spans": {"<n>": {"tag", "payload", "ref"?}}}` -- `n` a
book-global monotonic integer, keyed by its DECIMAL STRING form, `ref`
present only when the attribute was. In `strip` and `off` mode there is no
`entity_markup` key at all.

## Invocation style

Two styles on purpose, and the split is load-bearing.

  1. **Unit cases** import `render_obsidian.render()` directly and drive it
     with a hand-authored NodeStream, exactly as `tests/render_obsidian.test.py`
     does -- the adapter's contract is independently testable from a NodeStream
     literal, which is the point of the IR boundary.
  2. **One integration case** (section 18) builds a real `durable_root`, runs
     the ACTUAL `assemble.py` as a subprocess, and feeds the
     `nodestream.json` it wrote into `render()`. A hand-authored fixture on
     BOTH sides of a wire contract is exactly how a broken contract passes two
     green suites, so at least one case has to cross the seam for real.

## Delivery is asserted against the WRITTEN FILES

Section 11 reads the emitted `.md` files off disk rather than trusting
`render()`'s returned manifest. A manifest count proves the pre-pass replaced
something; it does not prove anything reached a reader. `_render_block`
discards a `kind: "verse"` node's own `text` entirely, so
counted-and-undelivered is a real state, not a hypothetical.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"
ASSEMBLE_SRC = SCRIPTS_SRC_DIR / "assemble.py"
OUTPUT_RESOLVE_SRC = SCRIPTS_SRC_DIR / "output_resolve.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
CACHE_KEY_SRC = SCRIPTS_SRC_DIR / "cache_key.py"
JSON_STDOUT_SRC = SCRIPTS_SRC_DIR / "json_stdout.py"
VALIDATE_BACKLINKS_SRC = SCRIPTS_SRC_DIR / "validate_backlinks.py"
BOOTSTRAP_NAMES_SRC = SCRIPTS_SRC_DIR / "bootstrap_names.py"
CANON_SENSES_SRC = SCRIPTS_SRC_DIR / "canon_senses.py"
CANON_SENSES_SCHEMA_SRC = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"
    / "canon-senses.schema.json"
)

for _src in (RENDER_OBSIDIAN_SRC, ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, VALIDATE_DRAFT_SRC,
             CACHE_KEY_SRC, JSON_STDOUT_SRC, VALIDATE_BACKLINKS_SRC, BOOTSTRAP_NAMES_SRC,
             CANON_SENSES_SRC, CANON_SENSES_SCHEMA_SRC):
    assert _src.is_file(), f"required fixture source not found at {_src}"


def _load_render_obsidian_module():
    spec = importlib.util.spec_from_file_location(
        "render_obsidian_under_entity_markup_test", RENDER_OBSIDIAN_SRC
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_obsidian = _load_render_obsidian_module()
RenderError = render_obsidian.RenderError

FOLDERS = {"person": "People", "place": "Places"}


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def ent(n, payload):
    """The exact three-part sequence assemble.py emits in `index` mode."""
    return f"⟦ENT_{n}⟧{payload}⟦/ENT_{n}⟧"


def span(tag, payload, ref=None):
    """One `entity_markup.spans` record. `ref` is OMITTED (not null) when the
    attribute was absent -- the producer's contract, and the thing
    `_entity_markup_identity`'s `ref or payload` rule turns on."""
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


def make_verse(placeholder, rendered="", literal_gloss="", vid="v1", mount="block"):
    return {
        "vid": vid, "placeholder": placeholder, "mount": mount,
        "content": {"rendered": rendered, "literal_gloss": literal_gloss},
    }


def make_nodestream(nodes, footnotes=None, spans=None, target="ru", extra=None):
    """`spans=None` leaves the `entity_markup` key OFF entirely, which is what
    `strip`/`off` mode actually produces. `spans={}` writes the key with an
    empty span table -- a book that declared the knob and carries no marked
    entity, which must render fine and report a VISIBLE zero."""
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
    """`output.target` is "obsidian" by default here, unlike
    tests/render_obsidian.test.py's own builder -- `index` mode is defined to
    require it (`_entity_markup_mode`), and so is collision de-linking, so a
    fixture that omitted it would silently exercise a different adapter
    posture than the one #795 ships."""
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


def render_into(tmp_path, nodestream, canon, profile, out_dir=None):
    out_dir = out_dir or (tmp_path / "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = render_obsidian.render(nodestream, canon, profile, out_dir)
    return out_dir, manifest


def make_managed_vault(tmp_path):
    """An out_dir that is ALREADY a vault this adapter owns, carrying one
    ordinary (non-dot) file. `_clean_vault_content` would happily delete that
    file, so its survival is what proves a refusal fired BEFORE the clean --
    the whole point of §6.4's ordering."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / render_obsidian.VAULT_MARKER_FILENAME).write_text(
        json.dumps({"managed_by": "literary-translator", "target": "obsidian"}) + "\n",
        encoding="utf-8",
    )
    (out_dir / "SURVIVOR.md").write_text("pre-existing vault content\n", encoding="utf-8")
    return out_dir


def read(out_dir, relpath):
    return (out_dir / relpath).read_text(encoding="utf-8")


def segment_note_texts(out_dir):
    """Every rendered NARRATIVE page's text. Segment notes are the only *.md
    files at the vault ROOT (entity and markup notes are always foldered), so
    this needs no filename-convention guess beyond that."""
    return [p.read_text(encoding="utf-8") for p in sorted(out_dir.iterdir())
            if p.is_file() and p.suffix == ".md"]


def vault_texts(out_dir, manifest):
    return [read(out_dir, rel) for rel in manifest["written"]]


def parse_frontmatter(text):
    assert text.startswith("---"), f"expected YAML frontmatter, got:\n{text[:200]!r}"
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"malformed frontmatter block:\n{text[:200]!r}"
    return yaml.safe_load(parts[1]) or {}


def entity_note_relpaths(manifest):
    """Every written note that is NOT a segment note (i.e. lives in a
    folder)."""
    return sorted(rel for rel in manifest["written"] if "/" in rel)


# ===========================================================================
# 1. A marked entity canon knows nothing about gets its own note, foldered by
#    its tag, and EVERY marked occurrence links.
# ===========================================================================

def test_unknown_marked_entity_gets_its_own_note_and_every_occurrence_links(tmp_path):
    nodes = [make_node("p1", "seg01",
                       f"{ent(1, 'Reb Noson')} spoke, and later {ent(2, 'Reb Noson')} left.")]
    spans = {"1": span("person", "Reb Noson"), "2": span("person", "Reb Noson")}
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=spans), make_canon({}), make_profile()
    )

    assert entity_note_relpaths(manifest) == ["People/Reb Noson.md"], (
        "one markup note, foldered by its TAG through the same "
        "category->folder catalog canon notes use"
    )
    body = segment_note_texts(out_dir)[0]
    assert body.count("[[People/Reb Noson|Reb Noson]]") == 2, (
        "every marked occurrence links -- no marked span is ever left as bare "
        f"text for the canon scan to (mis)handle. Got:\n{body}"
    )
    assert manifest["entity_markup"] == {
        "spans": 2, "notes": 1, "links": 2, "brackets_escaped": 0
    }


def test_declared_block_with_zero_spans_renders_and_reports_a_visible_zero(tmp_path):
    """A book may genuinely carry no marked entity. That must render, and the
    zero must be VISIBLE rather than indistinguishable from a scan that never
    ran."""
    nodes = [make_node("p1", "seg01", "Nothing is marked here.")]
    _out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={}), make_canon({}), make_profile()
    )
    assert manifest["entity_markup"] == {
        "spans": 0, "notes": 0, "links": 0, "brackets_escaped": 0
    }


# ===========================================================================
# 2. Two spans, same printed payload, different `ref` -- the case
#    string-matching cannot express at all.
# ===========================================================================

def test_same_payload_different_ref_makes_two_notes_each_linked_from_its_own_span(tmp_path):
    nodes = [make_node("p1", "seg01",
                       f"{ent(1, 'John')} met {ent(2, 'John')} at the gate.")]
    spans = {"1": span("person", "John", ref="john-of-nemirov"),
             "2": span("person", "John", ref="john-the-scribe")}
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=spans), make_canon({}), make_profile()
    )

    assert entity_note_relpaths(manifest) == [
        "People/john-of-nemirov.md", "People/john-the-scribe.md"
    ]
    body = segment_note_texts(out_dir)[0]
    assert "[[People/john-of-nemirov|John]] met [[People/john-the-scribe|John]]" in body, (
        "identity is the REF when one is present -- two men who print the same "
        f"must not collapse onto one note. Got:\n{body}"
    )


# ===========================================================================
# 3. Identity is (tag, label), never the label alone.
# ===========================================================================

def test_same_label_under_two_tags_stays_two_notes_in_two_folders(tmp_path):
    nodes = [make_node("p1", "seg01",
                       f"{ent(1, 'Jordan')} crossed the {ent(2, 'Jordan')}.")]
    spans = {"1": span("person", "Jordan"), "2": span("place", "Jordan")}
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=spans), make_canon({}), make_profile()
    )

    assert entity_note_relpaths(manifest) == ["People/Jordan.md", "Places/Jordan.md"], (
        "the tag is part of the IDENTITY, not merely the folder -- collapsing "
        "these two would be the entity-merge judgement #795 excludes, and one "
        "note cannot truthfully carry two categories"
    )
    body = segment_note_texts(out_dir)[0]
    assert "[[People/Jordan|Jordan]] crossed the [[Places/Jordan|Jordan]]" in body


def test_one_ref_reused_under_two_tags_also_stays_two_notes(tmp_path):
    nodes = [make_node("p1", "seg01", f"{ent(1, 'the man')} of {ent(2, 'the town')}.")]
    spans = {"1": span("person", "the man", ref="Nemirov"),
             "2": span("place", "the town", ref="Nemirov")}
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=spans), make_canon({}), make_profile()
    )

    assert entity_note_relpaths(manifest) == ["People/Nemirov.md", "Places/Nemirov.md"]
    body = segment_note_texts(out_dir)[0]
    assert "[[People/Nemirov|the man]] of [[Places/Nemirov|the town]]" in body


# ===========================================================================
# 4. The three defects a bare-text branch would reintroduce, each with a canon
#    deliberately set up to expose it. Each passes trivially against an
#    implementation that leaves NO marked span bare, and fails against one
#    that delegates any span to the canon scan.
# ===========================================================================

def test_longest_first_canon_match_can_never_steal_a_marked_span(tmp_path):
    """The canon scan is longest-first and matches over the RECOMPOSED text,
    not entity boundaries. With canon carrying both `John` and `John Smith`,
    a marked `John` followed by the word `Smith` would be swallowed into a
    wikilink for the OTHER man."""
    canon = make_canon({
        "Ivan": canon_entry("Ivan", "John"),
        "Ivan Kuznetsov": canon_entry("Ivan Kuznetsov", "John Smith"),
    })
    nodes = [make_node("p1", "seg01", f"{ent(1, 'John')} Smith walked on.")]
    out_dir, _manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "John")}),
        canon, make_profile(),
    )

    body = segment_note_texts(out_dir)[0]
    assert "[[People/Ivan|John]] Smith walked on." in body, f"got:\n{body}"
    assert "John Smith]]" not in body and "Ivan Kuznetsov|" not in body, (
        f"the marked span must never resolve to the LONGER canon target:\n{body}"
    )


def test_two_marked_spans_in_one_block_both_link_despite_seen_in_block(tmp_path):
    """`seen_in_block` suppresses the second and later canon occurrences in a
    block. Canon carries the target here on purpose: a bare-text branch would
    hand both spans to the canon scan and get ONE link out of two marks."""
    canon = make_canon({"Ivan": canon_entry("Ivan", "John")})
    nodes = [make_node("p1", "seg01", f"{ent(1, 'John')} and {ent(2, 'John')} again.")]
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(
            nodes, spans={"1": span("person", "John"), "2": span("person", "John")}),
        canon, make_profile(),
    )

    body = segment_note_texts(out_dir)[0]
    assert body.count("[[People/Ivan|John]]") == 2, f"got:\n{body}"
    assert manifest["entity_markup"]["links"] == 2


def test_marked_span_abutting_a_letter_still_links_despite_the_boundary_guard(tmp_path):
    """`_boundary_ok` refuses a canon target adjacent to an alphanumeric
    (#587's "Teplik" inside "Tepliker"). A marked span carries its own
    boundaries, so `<person>Ann</person>ette` must still link."""
    canon = make_canon({"Anna": canon_entry("Anna", "Ann")})
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Ann')}ette arrived.")]
    out_dir, _manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Ann")}),
        canon, make_profile(),
    )

    body = segment_note_texts(out_dir)[0]
    assert "[[People/Anna|Ann]]ette arrived." in body, f"got:\n{body}"


# ===========================================================================
# 5. A marked span whose label IS a linkable canon target: link canon, mint
#    nothing, and honour parenthetical_originals rather than bypassing it.
# ===========================================================================

def test_canon_backed_marked_span_mints_no_note_and_links_the_canon_note(tmp_path):
    canon = make_canon({"Иван": canon_entry("Иван", "Ivan")})
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Ivan')} arrived.")]
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Ivan")}),
        canon, make_profile(),
    )

    assert entity_note_relpaths(manifest) == ["People/Иван.md"], (
        "canon is the authority wherever it has an entry -- a marked span "
        "whose label is a linkable target must not mint a rival note"
    )
    assert manifest["entity_markup"]["notes"] == 0
    assert "[[People/Иван|Ivan]] arrived." in segment_note_texts(out_dir)[0]


def test_first_occurrence_gloss_appears_exactly_once_book_wide(tmp_path):
    """The pre-pass consults and updates the SAME `_Linker.global_seen` set the
    canon linker uses, so `parenthetical_originals: first_occurrence` shows the
    original-script gloss once across BOTH mechanisms -- not once per
    mechanism, and not once per segment."""
    canon = make_canon({"Иван": canon_entry("Иван", "Ivan")})
    nodes = [
        make_node("p1", "seg01", f"{ent(1, 'Ivan')} arrived.", order_index=0),
        make_node("p2", "seg01", f"Later {ent(2, 'Ivan')} left.", order_index=1),
        make_node("p3", "seg02", "And Ivan was gone.", order_index=2),
    ]
    spans = {"1": span("person", "Ivan"), "2": span("person", "Ivan")}
    out_dir, _manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=spans), canon,
        make_profile(parenthetical_originals="first_occurrence"),
    )

    bodies = segment_note_texts(out_dir)
    joined = "\n".join(bodies)
    assert joined.count("(Иван)") == 1, (
        f"the gloss must appear exactly once book-wide, got:\n{joined}"
    )
    assert "[[People/Иван|Ivan]] (Иван) arrived." in bodies[0], (
        f"and on the FIRST emitted occurrence, got:\n{bodies[0]}"
    )


# ===========================================================================
# 6. Markdown-active characters in a payload are unreachable BY CONSTRUCTION
#    (assemble.py refuses them -- pinned across the seam in section 18), and
#    the characters that ARE legal render a well-formed wikilink.
# ===========================================================================

def test_legal_punctuation_in_a_payload_renders_a_well_formed_wikilink(tmp_path):
    """`.`/`,`/`'` are ordinary in a printed name and are NOT refused upstream,
    so the emission grammar has to handle them. `[`, `]`, `|`, CR and LF are
    the ones a wikilink alias cannot survive, and they never arrive here --
    see test_assemble_refuses_a_pipe_in_a_payload."""
    payload = "Mrs. Adil, O'Brien"
    nodes = [make_node("p1", "seg01", f"{ent(1, payload)} nodded.")]
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", payload)}),
        make_canon({}), make_profile(),
    )

    body = segment_note_texts(out_dir)[0]
    assert f"[[People/{payload}|{payload}]] nodded." in body, f"got:\n{body}"
    link = body[body.index("[["):body.index("]]") + 2]
    assert link.count("|") == 1 and link.count("[[") == 1 and link.count("]]") == 1
    assert f"People/{payload}.md" in manifest["written"]


# ===========================================================================
# 7. Markup-note frontmatter carries only what is true.
# ===========================================================================

def test_aliases_collect_every_printed_payload_sorted_and_deduped(tmp_path):
    nodes = [make_node(
        "p1", "seg01",
        f"{ent(1, 'Reb Noson')}, {ent(2, 'R. Noson')}, and {ent(3, 'Reb Noson')} again.")]
    spans = {
        "1": span("person", "Reb Noson", ref="noson"),
        "2": span("person", "R. Noson", ref="noson"),
        "3": span("person", "Reb Noson", ref="noson"),
    }
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=spans), make_canon({}), make_profile()
    )

    assert entity_note_relpaths(manifest) == ["People/noson.md"]
    fm = parse_frontmatter(read(out_dir, "People/noson.md"))
    assert fm["aliases"] == ["R. Noson", "Reb Noson"], (
        "every DISTINCT printed payload, sorted and deduped -- one man, two "
        "printed forms, three occurrences"
    )
    assert fm["name"] == "noson"
    assert fm["category"] == "person"
    assert fm["ref"] == "noson"
    assert fm["direction"] == "ltr"
    for canon_only_field in ("basis", "confidence", "source", "is_proper_name"):
        assert canon_only_field not in fm, (
            f"{canon_only_field!r} is a canon field -- a markup note has no "
            "canon entry behind it, so carrying one would be a fabrication"
        )
    assert read(out_dir, "People/noson.md").rstrip().endswith("# noson")


def test_a_payload_derived_note_carries_no_ref_key(tmp_path):
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Reb Noson')} spoke.")]
    out_dir, _manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Reb Noson")}),
        make_canon({}), make_profile(),
    )
    fm = parse_frontmatter(read(out_dir, "People/Reb Noson.md"))
    assert "ref" not in fm, "the label came from the payload -- there is no ref to report"
    assert fm["aliases"] == ["Reb Noson"]
    assert fm["name"] == "Reb Noson"


# ===========================================================================
# 8. Markup notes are resolved AFTER canon, through the same collision set.
# ===========================================================================

def test_markup_note_colliding_with_a_canon_note_is_deduped_and_canon_keeps_its_path(tmp_path):
    """Canon resolves FIRST, so every canon relpath stays byte-identical to a
    render with no markup at all -- which is what keeps
    `validate_backlinks.py`'s own independent re-derivation correct. The
    markup note gets the `-2` suffix, never the other way round."""
    canon = make_canon({"John": canon_entry("John", "Jonathan")})
    nodes = [make_node("p1", "seg01", f"{ent(1, 'John')} arrived.")]
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "John")}),
        canon, make_profile(),
    )

    assert entity_note_relpaths(manifest) == ["People/John-2.md", "People/John.md"]
    assert parse_frontmatter(read(out_dir, "People/John.md"))["source_form"] == "John", (
        "the CANON note keeps the unsuffixed path"
    )
    assert parse_frontmatter(read(out_dir, "People/John-2.md"))["name"] == "John"
    assert "[[People/John-2|John]] arrived." in segment_note_texts(out_dir)[0]

    # ...and the same canon rendered with the knob absent resolves identically.
    # PLAIN text on this half, not the marked-up nodes: a non-index render now
    # REFUSES sentinel-bearing text rather than delivering it, so reusing them
    # would exercise that refusal instead of the canon relpath this asserts.
    plain_nodes = [make_node("p1", "seg01", "John arrived.")]
    plain_dir, plain_manifest = render_into(
        tmp_path, make_nodestream(plain_nodes, spans=None), canon,
        make_profile(entity_markup=False), out_dir=tmp_path / "plain",
    )
    assert entity_note_relpaths(plain_manifest) == ["People/John.md"]
    assert plain_dir.joinpath("People/John.md").is_file()


def test_resolve_entity_notes_two_argument_call_is_unchanged(tmp_path):
    """`validate_backlinks.py:860` calls this with TWO arguments and
    immediately `.items()` the result. The added `used_paths` parameter must
    stay optional and the return shape must stay a dict, or that gate breaks
    after a successful render."""
    entries = {"John": canon_entry("John", "Jonathan"),
               "Jean": canon_entry("Jean", "Jean")}
    result = render_obsidian._resolve_entity_notes(entries, FOLDERS)
    assert isinstance(result, dict)
    assert dict(result.items()) == {"Jean": "People/Jean.md", "John": "People/John.md"}


# ===========================================================================
# 9. Headings: link like any other span in the BODY, plain text in the title.
# ===========================================================================

def test_marked_heading_links_the_ref_never_the_canon_homonym_and_the_title_is_plain(tmp_path):
    """Canon holds a linkable `John` belonging to entity A; the heading marks a
    DIFFERENT John by ref. Resolving heading spans to bare payload would hand
    the heading to the canon scan and link the wrong man."""
    canon = make_canon({"A": canon_entry("A", "John")})
    nodes = [make_node("h1", "seg01", ent(1, "John"), kind="heading", raw_type="H2")]
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "John", ref="B")}),
        canon, make_profile(),
    )

    body = segment_note_texts(out_dir)[0]
    assert "[[People/B|John]]" in body, f"got:\n{body}"
    assert "People/A|" not in body, f"the heading must NOT link the canon homonym:\n{body}"

    title = parse_frontmatter(body)["title"]
    assert title == "John", f"got title {title!r}"
    for residue in ("<", "_", "[[", "\\[", "⟦"):
        assert residue not in title, f"{residue!r} leaked into title {title!r}"
    slugs = [rel for rel in manifest["written"] if "/" not in rel]
    assert slugs == ["001 John.md"], f"got {slugs}"


def test_flattening_is_mode_confined_so_an_authored_wikilink_heading_is_untouched(tmp_path):
    """With `index_from` NOT markup, a literal `[[x|y]]` an operator wrote into
    a source heading must still reach `title:` exactly as it does today on
    every other project."""
    nodes = [make_node("h1", "seg01", "[[x|y]]", kind="heading", raw_type="H2")]
    out_dir, _manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=None), make_canon({}),
        make_profile(index_from="canon"),
    )
    assert parse_frontmatter(segment_note_texts(out_dir)[0])["title"] == "[[x|y]]"


def test_segment_title_off_the_persisted_nodestream_matches_what_render_wrote(tmp_path):
    """`validate_backlinks.py:780` reconstructs each segment note's filename by
    calling `_segment_title(seg_nodes, seg)` -- two arguments, no flattening --
    against the PERSISTED nodestream.json, which assemble.py wrote BEFORE this
    adapter's resolution pre-pass ran and whose heading text therefore still
    carries raw ⟦ENT_n⟧ sentinels. `_heading_plain_text` scrubs that token pair
    (keeping the payload) for exactly this reason: without it the gate derives
    "001 _ENT_1_John_ENT_1_" for a segment written as "001 John", and every
    Mentions link into that segment is reported missing."""
    heading = make_node("h1", "seg01", f"Chapter of {ent(1, 'John')}",
                        kind="heading", raw_type="H2")
    persisted = make_nodestream([heading], spans={"1": span("person", "John")})

    out_dir, manifest = render_into(tmp_path, persisted, make_canon({}), make_profile())
    # Computed AFTER the render, off the SAME object the caller handed in --
    # which also proves render() rewrote its own deepcopy and not assemble.py's
    # already-persisted nodestream.
    reconstructed = render_obsidian._segment_title([heading], "seg01")
    assert heading["text"] == f"Chapter of {ent(1, 'John')}", (
        "render() must not mutate the caller's nodestream"
    )
    written_slugs = [rel for rel in manifest["written"] if "/" not in rel]
    assert written_slugs == ["001 Chapter of John.md"], written_slugs
    assert reconstructed == "Chapter of John", reconstructed
    assert f"001 {render_obsidian.sanitize_filename_component(reconstructed, 'x')}.md" \
        in written_slugs
    assert parse_frontmatter(segment_note_texts(out_dir)[0])["title"] == "Chapter of John"


# ===========================================================================
# 10. Every carrier the renderer can emit from -- three DISTINCT verse/footnote
#     paths, not one generic "verse" case.
# ===========================================================================

def test_markup_inside_a_dedicated_verse_block_resolves(tmp_path):
    verse = make_verse("⟦VERSE_vA⟧", rendered=f"{ent(1, 'John')} sang")
    nodes = [make_node("v1", "seg01", "⟦VERSE_vA⟧", kind="verse", verses=[verse])]
    out_dir, _manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "John")}),
        make_canon({}), make_profile(),
    )
    assert "> [[People/John|John]] sang" in segment_note_texts(out_dir)[0]


def test_markup_inside_an_inline_embedded_verse_resolves(tmp_path):
    """`_render_verse_inline` splices its output into the composed block AFTER
    any per-function resolution point would have run -- which is exactly how a
    per-site design leaks, and why resolution is one pre-pass."""
    verse = make_verse("⟦VERSE_vA⟧", rendered=f"{ent(1, 'John')} sang", mount="inline")
    nodes = [make_node("p1", "seg01", "He wrote: ⟦VERSE_vA⟧ and stopped.", verses=[verse])]
    out_dir, _manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "John")}),
        make_canon({}), make_profile(),
    )
    body = segment_note_texts(out_dir)[0]
    assert "He wrote: *[[People/John|John]] sang* and stopped." in body, f"got:\n{body}"


def test_markup_inside_a_referenced_footnote_definition_resolves(tmp_path):
    nodes = [make_node("p1", "seg01", "Prose ⟦FNREF_1⟧ here.", fnrefs=[1])]
    footnotes = [{"n": 1, "text": f"See {ent(1, 'John')} on this."}]
    out_dir, _manifest = render_into(
        tmp_path, make_nodestream(nodes, footnotes=footnotes,
                                  spans={"1": span("person", "John")}),
        make_canon({}), make_profile(),
    )
    assert "[^1]: See [[People/John|John]] on this." in segment_note_texts(out_dir)[0]


# ===========================================================================
# 11. DELIVERY, asserted against the WRITTEN VAULT rather than the manifest.
# ===========================================================================

def _five_carrier_fixture():
    """One book with a span in each of the five carriers a reader can actually
    reach: prose, a heading, a dedicated verse block, an inline embedded verse,
    and a REFERENCED footnote definition."""
    inline_verse = make_verse("⟦VERSE_vI⟧", rendered=f"{ent(4, 'Dov')} sang",
                              vid="vI", mount="inline")
    block_verse = make_verse("⟦VERSE_vB⟧", rendered=f"{ent(3, 'Chaim')} wept", vid="vB")
    nodes = [
        make_node("h1", "seg01", f"Chapter of {ent(1, 'Aaron')}", kind="heading",
                  raw_type="H2", order_index=0),
        make_node("p1", "seg01", f"{ent(2, 'Boruch')} spoke ⟦FNREF_1⟧.",
                  fnrefs=[1], order_index=1),
        make_node("vb", "seg01", "⟦VERSE_vB⟧", kind="verse", verses=[block_verse],
                  order_index=2),
        make_node("p2", "seg01", "He wrote: ⟦VERSE_vI⟧ and stopped.",
                  verses=[inline_verse], order_index=3),
    ]
    footnotes = [{"n": 1, "text": f"On {ent(5, 'Ezra')}."}]
    spans = {
        "1": span("person", "Aaron"), "2": span("person", "Boruch"),
        "3": span("person", "Chaim"), "4": span("person", "Dov"),
        "5": span("person", "Ezra"),
    }
    return make_nodestream(nodes, footnotes=footnotes, spans=spans), spans


def test_every_span_is_delivered_into_a_written_note(tmp_path):
    nodestream, spans = _five_carrier_fixture()
    out_dir, manifest = render_into(tmp_path, nodestream, make_canon({}), make_profile())

    delivered = "\n".join(vault_texts(out_dir, manifest))
    for key, record in sorted(spans.items()):
        payload = record["payload"]
        assert f"[[People/{payload}|{payload}]]" in delivered, (
            f"span {key} ({payload!r}) was counted but never reached a written "
            f"note -- a manifest count proves the pre-pass replaced something, "
            f"not that anything reached a reader. Vault:\n{delivered}"
        )
    assert "⟦" not in delivered and "ENT_" not in delivered


def test_resolution_identity_is_reported_on_the_same_fixture(tmp_path):
    nodestream, spans = _five_carrier_fixture()
    _out_dir, manifest = render_into(tmp_path, nodestream, make_canon({}), make_profile())
    assert manifest["entity_markup"]["spans"] == len(spans) == 5
    assert manifest["entity_markup"]["links"] == 5
    assert manifest["entity_markup"]["notes"] == 5


def test_a_pre_pass_that_resolves_all_but_one_span_is_refused(tmp_path, monkeypatch):
    """A resolver that quietly drops spans is otherwise indistinguishable from
    one that resolves them all: the counts look plausible and every spot-check
    passes."""
    real_apply = render_obsidian._apply_entity_markup

    def _skip_one(nodestream, spans, *rest):
        victim = sorted(spans)[0]
        for container, key in render_obsidian._entity_markup_string_slots(nodestream):
            container[key] = (container[key]
                              .replace(f"⟦ENT_{victim}⟧", "")
                              .replace(f"⟦/ENT_{victim}⟧", ""))
        return real_apply(nodestream, spans, *rest)

    monkeypatch.setattr(render_obsidian, "_apply_entity_markup", _skip_one)
    nodestream, _spans = _five_carrier_fixture()
    with pytest.raises(RenderError) as excinfo:
        render_into(tmp_path, nodestream, make_canon({}), make_profile())
    assert excinfo.value.reason == "entity_markup_coverage_mismatch"


# ===========================================================================
# 12. links_emitted stays honest.
# ===========================================================================

def test_inline_links_emitted_counts_the_markup_links_too(tmp_path):
    """`delink_cost.inline_links_emitted` is documented as EVERY inline link
    this render inserted, and `validate_backlinks.py` republishes it -- it must
    not quietly start meaning "the canon ones"."""
    nodestream, spans = _five_carrier_fixture()
    _out_dir, manifest = render_into(tmp_path, nodestream, make_canon({}), make_profile())
    assert manifest["delink_cost"]["inline_links_emitted"] == len(spans) == 5


# ===========================================================================
# 13. Preflight (§6.4) -- refuse BEFORE the existing vault is deleted.
# ===========================================================================

def _assert_preflight_refuses(tmp_path, nodestream):
    out_dir = make_managed_vault(tmp_path)
    with pytest.raises(RenderError) as excinfo:
        render_obsidian.render(nodestream, make_canon({}), make_profile(), out_dir)
    assert excinfo.value.reason == "entity_markup_unresolvable"
    assert (out_dir / "SURVIVOR.md").is_file(), (
        "the refusal must fire BEFORE _clean_vault_content -- otherwise the "
        "operator is left with neither the old vault nor a complete new one"
    )


def test_preflight_refuses_a_token_in_a_field_the_rewrite_never_scans(tmp_path):
    """The walk is over the WHOLE JSON value, not only the strings the pre-pass
    rewrites. Without this case a scan of only the rewritten strings passes the
    same test, and the sentinel ships in the persisted artifact."""
    node = make_node("p1", "seg01", "Plain prose.", raw_type=ent(1, "PARA"))
    _assert_preflight_refuses(
        tmp_path, make_nodestream([node], spans={"1": span("person", "PARA")})
    )


def test_preflight_refuses_a_token_in_a_verse_placeholder(tmp_path):
    verse = make_verse(ent(1, "VERSE"), rendered="a line")
    node = make_node("v1", "seg01", "text", kind="verse", verses=[verse])
    _assert_preflight_refuses(
        tmp_path, make_nodestream([node], spans={"1": span("person", "VERSE")})
    )


def test_preflight_refuses_a_lone_opener(tmp_path):
    node = make_node("p1", "seg01", "A ⟦ENT_9⟧ dangling opener.")
    _assert_preflight_refuses(
        tmp_path, make_nodestream([node], spans={"9": span("person", "x")})
    )


def test_preflight_refuses_a_lone_closer(tmp_path):
    """Both forms: a lone closer reaches a reader just as much as a lone
    opener."""
    node = make_node("p1", "seg01", "A ⟦/ENT_9⟧ dangling closer.")
    _assert_preflight_refuses(
        tmp_path, make_nodestream([node], spans={"9": span("person", "x")})
    )


def test_preflight_refuses_a_well_formed_pair_with_no_span_record(tmp_path):
    node = make_node("p1", "seg01", f"Here is {ent(7, 'John')}.")
    _assert_preflight_refuses(
        tmp_path, make_nodestream([node], spans={"1": span("person", "John")})
    )


# ===========================================================================
# 14. The write-time post-condition (§6.5), reached INDEPENDENTLY of the
#     preflight -- every fixture above is rejected earlier, so without this the
#     last reader-safety check could be deleted and the suite would stay green.
# ===========================================================================

def test_a_resolver_bug_that_leaves_a_sentinel_is_caught_before_the_note_is_written(
        tmp_path, monkeypatch):
    real_apply = render_obsidian._apply_entity_markup

    def _leak_one(nodestream, spans, *rest):
        counts = real_apply(nodestream, spans, *rest)
        # AFTER the real pre-pass and after the preflight already passed:
        # the only way to reach the write-time check on purpose.
        nodestream["nodes"][0]["text"] += " ⟦ENT_1⟧"
        return counts

    monkeypatch.setattr(render_obsidian, "_apply_entity_markup", _leak_one)
    nodes = [make_node("p1", "seg01", f"{ent(1, 'John')} spoke.")]
    out_dir = tmp_path / "out"
    with pytest.raises(RenderError) as excinfo:
        render_into(tmp_path, make_nodestream(nodes, spans={"1": span("person", "John")}),
                    make_canon({}), make_profile(), out_dir=out_dir)
    assert excinfo.value.reason == "entity_markup_residual_sentinel"
    assert not list(out_dir.glob("*.md")), "the offending note must not be written"


# ===========================================================================
# 15. Every non-index mode ignores `nodestream["entity_markup"]` ENTIRELY --
#     the same discipline `nodestream["mentions"]` already gets.
# ===========================================================================

@pytest.mark.parametrize("profile_kwargs", [
    {"index_from": "canon"},
    {"index_from": None},
    {"entity_markup": False},
    {"index_from": "markup", "target": "custom"},
])
def test_a_non_index_profile_refuses_a_stale_index_mode_nodestream(tmp_path, profile_kwargs):
    """Being inert about the KEY is right; being inert about a book whose
    TEXT carries `⟦ENT_n⟧` is how machine markup reaches a reader -- the
    failure #795 exists to close.

    A NON-EMPTY span table can only come from an `index`-mode assemble
    (`strip` and `off` write no key, and `index_from: markup` under another
    target is refused there), so this pairing means the NodeStream and the
    profile are from different runs: someone dropped `index_from: markup` and
    re-rendered without re-assembling. Nothing in these modes can resolve the
    sentinels, so the render refuses rather than delivering them verbatim.
    Every non-index mode is covered, including the `target: custom` row where
    the renderer stays inert on the MODE."""
    out_dir = make_managed_vault(tmp_path)
    nodes = [make_node("p1", "seg01", f"{ent(1, 'John')} spoke.")]
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(
            tmp_path, make_nodestream(nodes, spans={"1": span("person", "John")}),
            make_canon({}), make_profile(**profile_kwargs), out_dir=out_dir,
        )
    assert excinfo.value.reason == "entity_markup_stale_nodestream", excinfo.value.reason
    assert (out_dir / "SURVIVOR.md").is_file(), (
        "the refusal must fire BEFORE _clean_vault_content"
    )


@pytest.mark.parametrize(
    "spans",
    [
        pytest.param({}, id="empty-table"),
        pytest.param(None, id="key-absent"),
        pytest.param({"1": 5}, id="malformed-record"),
        pytest.param({"9": span("person", "Elsewhere")}, id="wrong-id"),
    ],
)
def test_sentinel_bearing_text_is_refused_whatever_the_span_table_says(tmp_path, spans):
    """The span table is only a PROXY for sentinel-bearing text, and a broken
    one: a hand-edited or truncated NodeStream carries the tokens with an
    empty, absent or malformed table. Keying the refusal on the TABLE let
    exactly that shape -- empty table, `off` mode -- clean the managed vault
    and write raw `⟦ENT_1⟧` tokens into a segment note, which is the original
    #795 failure with one more step in front of it. The refusal reads the
    TEXT."""
    out_dir = make_managed_vault(tmp_path)
    nodes = [make_node("p1", "seg01", f"{ent(1, 'John')} spoke.")]
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(
            tmp_path, make_nodestream(nodes, spans=spans), make_canon({}),
            make_profile(entity_markup=False), out_dir=out_dir,
        )
    assert excinfo.value.reason == "entity_markup_stale_nodestream", excinfo.value.reason
    assert (out_dir / "SURVIVOR.md").is_file(), (
        "the refusal must fire BEFORE _clean_vault_content"
    )


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("A lone ⟦ENT_1⟧ opener.", id="lone-opener"),
        pytest.param("A lone ⟦/ENT_1⟧ closer.", id="lone-closer"),
    ],
)
def test_a_lone_sentinel_token_is_refused_in_a_non_index_mode(tmp_path, text):
    """A half-pair ships to a reader just as visibly as a whole one, so the
    scan is per TOKEN -- the same shape the heading scrub takes."""
    out_dir = make_managed_vault(tmp_path)
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(
            tmp_path, make_nodestream([make_node("p1", "seg01", text)], spans=None),
            make_canon({}), make_profile(entity_markup=False), out_dir=out_dir,
        )
    assert excinfo.value.reason == "entity_markup_stale_nodestream", excinfo.value.reason
    assert (out_dir / "SURVIVOR.md").is_file()


@pytest.mark.parametrize("profile_kwargs", [
    {"index_from": "canon"},
    {"index_from": None},
    {"entity_markup": False},
    {"index_from": "markup", "target": "custom"},
])
def test_a_non_index_profile_still_ignores_an_EMPTY_planted_key(tmp_path, profile_kwargs):
    """The other half, and the reason the refusal keys on the span table
    rather than on the key's presence: a book that declared the knob, carried
    no marked entity, and then had the knob removed leaves an
    `entity_markup: {"spans": {}}` behind. There are no sentinels in its text,
    so there is nothing to refuse -- the key is ignored exactly as
    `nodestream["mentions"]` is under a non-effective-enabled profile."""
    nodes = [make_node("p1", "seg01", "John spoke.")]
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={}),
        make_canon({}), make_profile(**profile_kwargs),
    )
    assert "entity_markup" not in manifest
    assert entity_note_relpaths(manifest) == [], "no markup note may be minted"
    assert "John spoke." in segment_note_texts(out_dir)[0]


# ===========================================================================
# 16. The editorial-bracket collision (§7), at BOTH emission sites. The
#     side-by-side parity table -- every literal/escaped combination on each
#     side, at the marked site, with the `brackets_escaped` count -- is
#     section 26; what lives here is the canon site and the heading, whose
#     unescaping in the title that table does not reach.
# ===========================================================================

def test_bracketed_canon_name_escapes_the_outer_pair(tmp_path):
    """The existing `_Linker` insertion point. `[` + `[[People/X|X]]` + `]`
    reads to Obsidian as the target `[People/X` plus a stray `]`."""
    canon = make_canon({"Reb Noson": canon_entry("Reb Noson", "Reb Noson")})
    nodes = [make_node("p1", "seg01", "And [Reb Noson] spoke.")]
    out_dir, _manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=None), canon,
        make_profile(entity_markup=False),
    )
    body = segment_note_texts(out_dir)[0]
    assert "And \\[[[People/Reb Noson|Reb Noson]]\\] spoke." in body, f"got:\n{body}"


def test_bracketed_marked_name_in_a_heading_escapes_the_body_and_leaves_the_title_plain(tmp_path):
    """§6.3's reason for unescaping inside `_heading_plain_text`: flattening
    only the inner wikilink would leave `title: \\[John\\]` in the frontmatter
    and the backslashes in the filename slug."""
    nodes = [make_node("h1", "seg01", f"[{ent(1, 'John')}]", kind="heading", raw_type="H2")]
    out_dir, _manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "John")}),
        make_canon({}), make_profile(),
    )
    body = segment_note_texts(out_dir)[0]
    assert "## \\[[[People/John|John]]\\]" in body, f"got:\n{body}"
    title = parse_frontmatter(body)["title"]
    assert title == "[John]", f"got title {title!r}"
    assert "\\" not in title and "[[" not in title


# ===========================================================================
# 17b. validate_backlinks.py against a vault that contains markup notes.
# ===========================================================================

_STUB_OCCURRENCE_TARGETS_SRC = '''\
"""Test double for occurrence_targets.py, installed only inside this file's
own isolated fixture root. The real occurrence engine's eligibility rules are
tests/validate_backlinks.test.py's subject, not this file's -- here the
aggregate is FIXED so the assertion is about the gate still running end to end
over a vault carrying markup notes, with a canon entity whose expected
occurrence count is deliberately non-zero."""
import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def build(manifest, canon, senses_result, language_config, nodestream):
    return json.loads((_HERE / "_test_aggregate.json").read_text(encoding="utf-8"))
'''


def _run_backlinks_gate_over(tmp_path, nodes):
    """Render `nodes` into a real vault, persist the PRE-pre-pass nodestream
    beside it exactly as assemble.py does, and run the shipped
    `validate_backlinks.py` over the result.

    The canon entity `Иван` has a NON-ZERO expected occurrence count in
    `seg01` on purpose -- an empty canon would make "the gate passed" mean
    only "it did not crash". The book also carries a marked entity canon knows
    nothing about, so the vault genuinely contains a markup note."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (VALIDATE_BACKLINKS_SRC, VALIDATE_DRAFT_SRC, OUTPUT_RESOLVE_SRC,
                RENDER_OBSIDIAN_SRC, BOOTSTRAP_NAMES_SRC, CANON_SENSES_SRC,
                JSON_STDOUT_SRC):
        shutil.copy2(src, scripts_dir / src.name)
    (scripts_dir / "occurrence_targets.py").write_text(
        _STUB_OCCURRENCE_TARGETS_SRC, encoding="utf-8")
    (scripts_dir / "_test_aggregate.json").write_text(
        json.dumps({"eligible_by_source_form": {
                        "Иван": [{"source_form": "Иван", "seg": "seg01",
                                  "origin": "target_form"}]},
                    "unresolved_homonyms": {}}),
        encoding="utf-8")
    (root / "schemas").mkdir()
    shutil.copy2(CANON_SENSES_SCHEMA_SRC, root / "schemas" / CANON_SENSES_SCHEMA_SRC.name)
    (root / "languages").mkdir()
    (root / "languages" / "test.json").write_text(
        json.dumps({"PARTICLES": [], "STOPWORDS": [], "has_elision": False,
                    "ELISION_RE": None}),
        encoding="utf-8")

    profile = make_profile(mentions_enabled=True)
    profile["output"]["destination"] = str(root / "out")
    profile["source"] = {"language": {"code": "en", "particle_config": "test.json",
                                      "smoke_test": {"report_path": None}}}
    (root / "profile.yml").write_text(yaml.safe_dump(profile, sort_keys=False),
                                      encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"blocks": {}, "segments": []}), encoding="utf-8")

    canon = make_canon({"Иван": canon_entry("Иван", "Ivan")})
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False),
                                     encoding="utf-8")

    joined = "".join(node["text"] for node in nodes)
    spans = {n: span("person", "Reb Noson")
             for n in ("1", "2") if f"⟦ENT_{n}⟧" in joined}
    assert spans, "fixture: at least one marked span is the point of this gate"
    nodestream = make_nodestream(nodes, spans=spans)
    nodestream["mentions"] = {"Иван": [{"source_form": "Иван", "seg": "seg01",
                                        "origin": "target_form"}]}

    out_dir = root / "out"
    out_dir.mkdir(parents=True)
    # The PERSISTED artifact is the PRE-pre-pass one -- assemble.py writes
    # nodestream.json before dispatching the adapter, so the copy the gate
    # reads still carries raw ⟦ENT_n⟧ sentinels. Written FIRST, from the same
    # object, so render()'s deepcopy discipline is what keeps the two apart.
    assembled = out_dir / ".assembled"
    assembled.mkdir(parents=True, exist_ok=True)
    (assembled / "nodestream.json").write_text(
        json.dumps(nodestream, ensure_ascii=False), encoding="utf-8")

    manifest = render_obsidian.render(nodestream, canon, profile, out_dir)
    assert "People/Reb Noson.md" in manifest["written"], "sanity: a markup note exists"

    proc = subprocess.run(
        [sys.executable, str(scripts_dir / "validate_backlinks.py")],
        capture_output=True, text=True, timeout=60,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return json.loads(lines[0]), manifest


def _assert_canon_entity_fully_covered(report):
    coverage = report["mentions_coverage"]
    assert coverage["status"] == "enabled", report
    assert coverage["checked_entities"] == 1, (
        f"the canon entity must actually be CHECKED, not skipped: {report}"
    )
    assert coverage["missing"] == [], (
        f"its Mentions coverage must be reported as complete: {report}"
    )


def test_validate_backlinks_still_checks_canon_coverage_over_a_markup_vault(tmp_path):
    """Markup notes are invisible to this gate by design: it derives the notes
    it parses from canon alone, so an extra `People/Reb Noson.md` in the vault
    must change nothing about what it checks."""
    report, _manifest = _run_backlinks_gate_over(
        tmp_path, [make_node("p1", "seg01", f"Ivan met {ent(1, 'Reb Noson')}.")]
    )
    _assert_canon_entity_fully_covered(report)


def test_validate_backlinks_reconstructs_the_filename_of_a_MARKED_HEADING_segment(tmp_path):
    """The regression lock for `_heading_plain_text`'s ⟦ENT_n⟧ scrub, driven
    end to end rather than through the helper directly.

    This gate rebuilds each segment note's filename with
    `render_obsidian._segment_title` + `sanitize_filename_component` over the
    PERSISTED nodestream, whose heading text still carries raw sentinels.
    Without the scrub it derives "001 Chapter of _ENT_1_Reb Noson_ENT_1_" for a
    file render() actually wrote as "001 Chapter of Reb Noson", the Mentions
    link in the canon note points at the real name, and the entity is reported
    as missing coverage it in fact has. Delete the scrub and this goes red;
    the previous test does not."""
    nodes = [
        make_node("h1", "seg01", f"Chapter of {ent(1, 'Reb Noson')}",
                  kind="heading", raw_type="H2", order_index=0),
        make_node("p1", "seg01", f"Ivan met {ent(2, 'Reb Noson')} there.",
                  order_index=1),
    ]
    report, manifest = _run_backlinks_gate_over(tmp_path, nodes)
    assert [rel for rel in manifest["written"] if "/" not in rel] == [
        "001 Chapter of Reb Noson.md"
    ], manifest["written"]
    _assert_canon_entity_fully_covered(report)


# ===========================================================================
# 18. THE INTEGRATION CASE -- the real assemble.py's own nodestream.json fed
#     into render(). A hand-authored fixture on BOTH sides of a wire contract
#     is exactly how a broken contract passes two green suites.
# ===========================================================================

def _integration_profile(root, block_text_tags=("person", "place"), index_from="markup"):
    profile = {
        "profile_version": 1,
        "project": {"title": "Test Book", "durable_root": str(root),
                    "pipeline_version": "v1", "max_segment_words": 15000},
        "source": {
            "format": "plain_text", "path": "/logical/source.txt", "gutenberg_id": None,
            "language": {"code": "fr", "particle_config": "fr_test.json",
                         "smoke_test": {"report_path": None}},
            "adapter_config": {
                "gutenberg_epub": None,
                "plain_text": {
                    "segmentation": {"method": "blank_line_run",
                                     "blank_line_threshold": 2, "heading_regex": None},
                    "verse_detection": "none_confirmed", "verse_regex": None,
                    "footnotes": "none_confirmed", "footnote_anchor_regex": None,
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
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
        "output": {
            "v1_scope": "assembled_book", "destination": str(root / "out"),
            "target": "obsidian",
            "name_display": {"parenthetical_originals": "never"},
            "entity_markup": {"tags": list(block_text_tags), "index_from": index_from},
            "adapter_config": {
                "obsidian": {"folders": FOLDERS,
                             "mentions_section": {"enabled": False}},
                "epub": None, "custom": None,
            },
        },
    }
    return profile


def _draft_content_sha1(doc):
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(projected, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def _real_cache_key(root, seg):
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "cache_key.py"), "--seg", seg],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        f"fixture setup: cache_key.py --seg {seg} failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def make_integration_root(tmp_path, block_text, index_from="markup"):
    """A minimal one-segment converged book whose translated draft carries the
    operator's own declared markup. Helpers are re-derived here rather than
    imported from tests/assemble.test.py -- house convention is one
    self-contained file per test module."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC,
                VALIDATE_DRAFT_SRC, CACHE_KEY_SRC, JSON_STDOUT_SRC):
        shutil.copy2(src, scripts_dir / src.name)
    for name, body in (("bootstrap_names.py", b"# bootstrap_names.py fixture\n"),
                       ("segpack.py", b"# segpack.py fixture\n")):
        (scripts_dir / name).write_bytes(body)

    (root / "profile.yml").write_text(
        yaml.safe_dump(_integration_profile(root, index_from=index_from),
                       sort_keys=False),
        encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8")
    (root / "canon.json").write_text(
        json.dumps({"entries": {}, "review_queue": [],
                    "generation_hashes": {"particle_config_hash": "x",
                                          "derivation_bundle_hash": "y"}}),
        encoding="utf-8")

    (root / "style_bible.md").write_bytes(
        b"# Style Bible\n\n<!-- STYLE_CONTRACT_BEGIN -->\n"
        b"Formal register, Oxford comma.\n<!-- STYLE_CONTRACT_END -->\n")
    (root / "translate_TASK.md").write_bytes(b"TRANSLATE TASK PROMPT v1\n")
    (root / "review_TASK.md").write_bytes(b"REVIEW TASK PROMPT v1\n")
    (root / "extract.py").write_bytes(b"# extract.py fixture v1\n")
    (root / "source.txt").write_bytes(b"Ceci est un texte source de test.\n")
    (root / "languages").mkdir()
    (root / "languages" / "fr_test.json").write_text(
        json.dumps({"PARTICLES": ["de"], "STOPWORDS": ["le"], "has_elision": False,
                    "ELISION_RE": None}),
        encoding="utf-8")
    (root / "schemas").mkdir()
    for name in ("draft.schema.json", "review.schema.json", "segpack.schema.json"):
        (root / "schemas" / name).write_bytes(b"{}\n")
    (root / "runs").mkdir()
    (root / "runs" / ".plugin_bundle_hash").write_text(
        "test-plugin-bundle-marker-v1\n", encoding="utf-8")
    (root / "segments").mkdir()

    manifest = {
        "blocks": {"p1": {"id": "p1", "type": "PARA", "seg": "seg01", "order_index": 0,
                          "plain_text": "source text",
                          "sha1": hashlib.sha1(b"p1").hexdigest(),
                          "source_file": "source.txt"}},
        "spine": [{"pos": 0, "file": "source.txt", "klass": "body"}],
        "segments": [{"seg": "seg01", "kind": "body", "title_text": "Chapter One",
                      "block_ids": ["p1"], "word_count": 10}],
        "footnotes": [], "frontback": [], "verse": {"store": []},
        "source_inputs": ["source.txt"],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False),
                                        encoding="utf-8")
    segpack = {
        "seg": "seg01", "title": "seg01", "kind": "body", "word_count": 10,
        "blocks": [{"id": "p1", "order_index": 0, "plain_text": "source text"}],
        "footnotes": [], "verses": [], "names": [], "canon_names": [], "new_names": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y",
                              "particle_config_hash": "x", "derivation_bundle_hash": "y"},
    }
    (root / "segments" / "segpack_seg01.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8")
    draft = {"seg": "seg01", "blocks": {"p1": block_text}, "footnotes": {},
             "verses": {}, "names": [], "notes": []}
    (root / "segments" / "seg01.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": {"seg01": {
            "timestamp": "2026-01-01T00:00:00+00:00", "status": "converged", "rounds": 1,
            "cache_key": _real_cache_key(root, "seg01"), "n_blocks": 1,
            "n_footnotes": 0, "n_verses": 0,
            "reviewed_draft_sha1": _draft_content_sha1(draft),
        }}}, ensure_ascii=False),
        encoding="utf-8")
    return root


def run_assemble(root, timeout=120):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "assemble.py")],
        capture_output=True, text=True, timeout=timeout,
    )


def test_real_assemble_output_feeds_render_across_the_wire(tmp_path):
    """THE seam test. Everything else in this file hand-authors the NodeStream
    on the consumer side; this one takes the producer's actual bytes."""
    root = make_integration_root(
        tmp_path,
        'Then <person ref="noson">Reb Noson</person> reached '
        "<place>Nemirov</place>.",
    )
    proc = run_assemble(root)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

    nodestream = json.loads(
        (root / "out" / ".assembled" / "nodestream.json").read_text(encoding="utf-8"))

    # 1. The seam's own shape, read off the producer's real artifact.
    spans = nodestream["entity_markup"]["spans"]
    assert sorted(spans) == ["1", "2"], spans
    assert spans["1"] == {"tag": "person", "payload": "Reb Noson", "ref": "noson"}
    assert spans["2"] == {"tag": "place", "payload": "Nemirov"}, (
        "`ref` must be OMITTED, not null, when the attribute was absent"
    )
    text = nodestream["nodes"][0]["text"]
    assert text == (f"Then {ent(1, 'Reb Noson')} reached {ent(2, 'Nemirov')}."), text

    # 2. Those exact bytes, through render().
    canon = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    profile = yaml.safe_load((root / "profile.yml").read_text(encoding="utf-8"))
    out_dir, manifest = render_into(tmp_path, nodestream, canon, profile,
                                    out_dir=tmp_path / "seam_vault")
    assert entity_note_relpaths(manifest) == ["People/noson.md", "Places/Nemirov.md"]
    body = segment_note_texts(out_dir)[0]
    assert "Then [[People/noson|Reb Noson]] reached [[Places/Nemirov|Nemirov]]." in body, (
        f"got:\n{body}"
    )
    assert manifest["entity_markup"]["spans"] == 2


def test_assemble_refuses_a_pipe_in_a_payload(tmp_path):
    """The consumer's own precondition, pinned where it is actually enforced.
    §6.2 interpolates the payload as a wikilink ALIAS, and `[[X|a|b]]`
    re-splits at the pipe -- a grammar that admitted the character and an
    emission grammar that cannot escape it are simply incompatible, so the
    producer refuses instead."""
    root = make_integration_root(tmp_path, "Then <person>a|b</person> spoke.")
    proc = run_assemble(root)
    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "entity_markup_span_unsafe_text" in proc.stdout, proc.stdout


def test_strip_mode_leaves_no_entity_markup_key_for_the_renderer_to_find(tmp_path):
    """`index_from: canon` is the `strip` mode: the elements are removed, the
    payload stays, and the NodeStream carries NO `entity_markup` key at all --
    which is the state section 15's ignore-entirely tests stand in for."""
    root = make_integration_root(
        tmp_path, "Then <person>Reb Noson</person> spoke.", index_from="canon")
    proc = run_assemble(root)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

    nodestream = json.loads(
        (root / "out" / ".assembled" / "nodestream.json").read_text(encoding="utf-8"))
    assert "entity_markup" not in nodestream, nodestream.keys()
    assert nodestream["nodes"][0]["text"] == "Then Reb Noson spoke."


# ===========================================================================
# 19. Canon composition must not absorb a differently-CATEGORIZED entity.
#     Composing on the label alone made `<person>Jordan</person>` link a canon
#     note for a PLACE named Jordan: the entity-merge judgement this plugin
#     never makes, and a silent shortfall -- no person note, coverage counts
#     still balanced, exit 0.
# ===========================================================================

def test_a_contradicting_canon_category_refuses_composition_and_mints_its_own_note(tmp_path):
    canon = make_canon({"Ярдэн": canon_entry("Ярдэн", "Jordan", category="place")})
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Jordan')} and {ent(2, 'Jordan')}.")]
    spans = {"1": span("place", "Jordan"), "2": span("person", "Jordan")}
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=spans), canon, make_profile(),
    )

    assert entity_note_relpaths(manifest) == ["People/Jordan.md", "Places/Ярдэн.md"], (
        "the place span composes with canon; the PERSON span must mint its own "
        "note rather than be absorbed into a canon entry for a place"
    )
    assert manifest["entity_markup"]["notes"] == 1
    body = segment_note_texts(out_dir)[0]
    assert "[[Places/Ярдэн|Jordan]] and [[People/Jordan|Jordan]]." in body, body


def test_a_matching_canon_category_still_composes(tmp_path):
    canon = make_canon({"Ярдэн": canon_entry("Ярдэн", "Jordan", category="place")})
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Jordan')} lies east.")]
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("place", "Jordan")}),
        canon, make_profile(),
    )
    assert entity_note_relpaths(manifest) == ["Places/Ярдэн.md"]
    assert manifest["entity_markup"]["notes"] == 0


@pytest.mark.parametrize("category", ["", "   ", None])
def test_a_canon_entry_with_no_category_composes_with_any_tag(tmp_path, category):
    """Deliberate, not lax: the shipped glossary pass never asks for
    `category`, so on a typical project the field is empty everywhere.
    Demanding a positive match would stop composition entirely and mint a
    duplicate beside every canon note -- the two-competing-indexes outcome
    this feature exists to avoid."""
    entry = canon_entry("Ярдэн", "Jordan", category="place")
    if category is None:
        del entry["category"]
    else:
        entry["category"] = category
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Jordan')} arrived.")]
    _out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Jordan")}),
        make_canon({"Ярдэн": entry}), make_profile(),
    )
    # `other/` because a categoryless canon entry routes to DEFAULT_FOLDER --
    # unchanged behaviour, and exactly why demanding a positive category match
    # would strand every such entry with a duplicate markup note beside it.
    assert entity_note_relpaths(manifest) == ["other/Ярдэн.md"]
    assert manifest["entity_markup"]["notes"] == 0


# ===========================================================================
# 20. The preflight owns BOTH directions. A record with no pair, or one id
#     used twice, used to pass it and be caught only by the post-render count
#     comparison -- which runs after _clean_vault_content has already emptied
#     the operator's vault.
# ===========================================================================

def test_preflight_refuses_a_span_record_that_no_pair_uses(tmp_path):
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Ivan')} arrived.")]
    _assert_preflight_refuses(
        tmp_path,
        make_nodestream(nodes, spans={"1": span("person", "Ivan"),
                                      "2": span("person", "Pyotr")}),
    )


def test_preflight_refuses_one_span_id_used_by_two_pairs(tmp_path):
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Ivan')} met {ent(1, 'Ivan')}.")]
    _assert_preflight_refuses(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Ivan")}),
    )


# ===========================================================================
# 21. The ⟦ENT_n⟧ heading scrub is UNCONDITIONAL, and that is the one thing a
#     project declaring nothing can notice. Pinned deliberately rather than
#     left to be discovered: it cannot be mode-gated, because
#     validate_backlinks.py rebuilds a segment filename off the PERSISTED
#     nodestream and is handed no mode to gate on.
# ===========================================================================

def test_the_ent_heading_scrub_is_unconditional_at_the_caller_that_needs_it():
    """The scrub runs in EVERY mode, and the caller that makes that
    load-bearing is not `render()` -- it is `validate_backlinks.py:780`, which
    rebuilds each segment note's filename by calling `_segment_title` against
    the PERSISTED nodestream.json. assemble.py wrote that file BEFORE the
    resolution pre-pass ran, so its heading text still carries raw sentinels,
    and it is handed no mode to gate on. Without the scrub that gate derives
    `001 _ENT_1_John_ENT_1_` for a segment render() wrote as `001 John`, and
    reports every Mentions link into it missing.

    Asserted at that TWO-ARGUMENT call rather than through a whole render of
    an undeclared project: such a render now refuses sentinel-bearing text
    outright (`entity_markup_stale_nodestream`), which is the right answer for
    a delivered book and the wrong vehicle for this scrub."""
    heading = make_node("h1", "seg01", f"Chapter {ent(1, 'One')}",
                        kind="heading", raw_type="H2")
    assert render_obsidian._segment_title(
        [heading], {"seg": "seg01", "kind": "body"}
    ) == "Chapter One"


# ===========================================================================
# 22. The three things review round 2 found untested, each pinned where it
#     actually decides something.
# ===========================================================================

def test_canon_carrying_the_reserved_token_is_refused_before_the_vault_is_cleaned(tmp_path):
    """An entity note's frontmatter and heading are built STRAIGHT from the
    canon entry and never pass through the resolution pre-pass, so a reserved
    token sitting in canon.json reaches `_reject_residual_entity_tokens` only
    at WRITE time -- after `_clean_vault_content` has emptied the operator's
    vault. Same class as the two span-record conditions beside it, and closed
    in the same place: the preflight walks canon too."""
    out_dir = make_managed_vault(tmp_path)
    canon = make_canon({"Иван": canon_entry("Иван", f"Ivan {ent(1, 'x')}")})
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Ivan')} arrived.")]

    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(
            tmp_path, make_nodestream(nodes, spans={"1": span("person", "Ivan")}),
            canon, make_profile(), out_dir=out_dir,
        )
    assert excinfo.value.reason == "entity_markup_unresolvable", excinfo.value.reason
    assert "canon.json" in str(excinfo.value)
    assert (out_dir / "SURVIVOR.md").is_file(), (
        "the refusal must fire BEFORE _clean_vault_content -- the operator "
        "keeps the vault they already had"
    )


def test_the_footnote_ordering_imprecision_is_what_the_docs_say_it_is(tmp_path):
    """An ACCEPTED imprecision, pinned so it stays accepted rather than
    drifting silently. The pre-pass visits every NODE before any footnote
    definition, while rendering delivers segment 1's footnotes before segment
    2's prose. So with the only two occurrences in seg01's FOOTNOTE and in
    seg02's prose, `parenthetical_originals: first_occurrence` puts the gloss
    on the seg02 prose -- and the reader meets the unglossed footnote
    occurrence a page earlier.

    What IS guaranteed, and is asserted here beside the ordering, is that the
    gloss appears exactly once book-wide. Closing the ordering gap would mean
    running the pre-pass in render order, i.e. resolving spans at each
    rendering site instead of in one whole-NodeStream pass -- the design this
    feature deliberately does not have, because it is what makes the coverage
    identity checkable at all."""
    nodes = [
        make_node("p1", "seg01", "A quiet opening.", fnrefs=[1]),
        make_node("p2", "seg02", f"Later {ent(2, 'Ivan')} left.", order_index=1),
    ]
    footnotes = [{"n": 1, "text": f"See {ent(1, 'Ivan')} here.", "seg": "seg01"}]
    canon = make_canon({"Иван": canon_entry("Иван", "Ivan")})
    out_dir, _manifest = render_into(
        tmp_path,
        make_nodestream(nodes, footnotes=footnotes,
                        spans={"1": span("person", "Ivan"),
                               "2": span("person", "Ivan")}),
        canon, make_profile(parenthetical_originals="first_occurrence"),
    )
    first, second = segment_note_texts(out_dir)
    assert "[[People/Иван|Ivan]] here." in first and "(Иван)" not in first, (
        f"expected the EARLIER page's footnote occurrence to be unglossed:\n{first}"
    )
    assert "[[People/Иван|Ivan]] (Иван) left." in second, (
        f"expected the gloss on the LATER page's prose occurrence:\n{second}"
    )
    assert (first + second).count("(Иван)") == 1, (
        "exactly-once book-wide is the guarantee, and it still holds"
    )


# ===========================================================================
# 23. Composition identity is NFC-normalized and tolerant of a canon
#     `category` that is not a string at all.
# ===========================================================================

def test_composition_matches_across_unicode_normalization_forms(tmp_path):
    """`_entity_markup_identity` NFC-normalizes the label and the canon index
    is built the same way, so a payload spelled in DECOMPOSED form still
    composes with a canon entry stored precomposed. Without that, one
    invisible difference in spelling mints a duplicate note beside the canon
    one and the operator sees two entries for one person."""
    precomposed = "Jos\u00e9"                    # e-acute as one code point
    decomposed = "Jose\u0301"                    # e + combining acute
    assert precomposed != decomposed
    canon = make_canon({"Хосе": canon_entry("Хосе", precomposed)})
    nodes = [make_node("p1", "seg01", f"{ent(1, decomposed)} arrived.")]
    _out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", decomposed)}),
        canon, make_profile(),
    )
    assert entity_note_relpaths(manifest) == ["People/Хосе.md"], (
        "a decomposed payload must compose with the precomposed canon entry, "
        "not mint a second note for the same person"
    )
    assert manifest["entity_markup"]["notes"] == 0


@pytest.mark.parametrize("category", [5, ["place"], {"name": "place"}, True])
def test_a_canon_category_that_is_not_a_string_composes_like_an_absent_one(
    tmp_path, category
):
    """`_canon_composition` tests `isinstance(category, str)` deliberately: a
    non-string `category` is not a CONTRADICTION, it is an unreadable field,
    and the no-category rule already says canon speaks only where it has
    actually spoken. Pinned because the alternative -- comparing whatever the
    field holds against the tag -- would refuse composition on every such
    entry and mint the duplicate note the whole feature exists to avoid."""
    entry = canon_entry("Ярдэн", "Jordan", category="place")
    entry["category"] = category
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Jordan')} arrived.")]
    _out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Jordan")}),
        make_canon({"Ярдэн": entry}), make_profile(),
    )
    assert entity_note_relpaths(manifest) == ["other/Ярдэн.md"]
    assert manifest["entity_markup"]["notes"] == 0


# ===========================================================================
# 24. The heading scrub takes EVERY matching token, not only a well-formed
#     pair -- and a heading it touches loses the byte-identical fast path.
# ===========================================================================

@pytest.mark.parametrize(
    "heading_text, expected_title",
    [
        pytest.param("Chapter ⟦ENT_1⟧One", "Chapter One", id="lone-opener"),
        pytest.param("Chapter ⟦/ENT_1⟧One", "Chapter One", id="lone-closer"),
        pytest.param("Chapter  ⟦ENT_1⟧ One ", "Chapter One", id="whitespace-collapse"),
    ],
)
def test_a_lone_heading_token_is_scrubbed_and_the_title_whitespace_collapses(
    heading_text, expected_title
):
    """A half-pair ships to a reader just as visibly as a whole one, so the
    scrub is per TOKEN. The collapse is its documented cost: a heading the
    scrub touches no longer takes `_heading_plain_text`'s byte-identical fast
    path, so its internal whitespace is normalized. Both matter to the
    filename `validate_backlinks.py` re-derives, which is the call asserted
    here -- see the test above for why not through a render."""
    heading = make_node("h1", "seg01", heading_text, kind="heading", raw_type="H2")
    assert render_obsidian._heading_plain_text(heading) == expected_title
    assert render_obsidian._segment_title(
        [heading], {"seg": "seg01", "kind": "body"}
    ) == expected_title


# ===========================================================================
# 25. The preflight owns the span table's SHAPE and its AGREEMENT with the
#     text, not only its cardinality. Three review rounds each found one more
#     malformed shape reaching `_clean_vault_content`; these two conditions
#     are the answer to the class. Every case below asserts the SURVIVOR file
#     is still there -- a refusal that fires after the clean has already cost
#     the operator the vault they had.
# ===========================================================================

@pytest.mark.parametrize(
    "record, field",
    [
        pytest.param({"tag": "person", "payload": 5}, "payload", id="payload-int"),
        pytest.param({"tag": "person", "payload": None}, "payload", id="payload-null"),
        pytest.param({"tag": "person", "payload": ""}, "payload", id="payload-empty"),
        pytest.param({"tag": "person"}, "payload", id="payload-missing"),
        pytest.param({"tag": 7, "payload": "Ivan"}, "tag", id="tag-int"),
        pytest.param({"tag": "", "payload": "Ivan"}, "tag", id="tag-empty"),
        pytest.param({"payload": "Ivan"}, "tag", id="tag-missing"),
        pytest.param({"tag": "person", "payload": "Ivan", "ref": 3}, "ref", id="ref-int"),
        pytest.param({"tag": "person", "payload": "Ivan", "ref": []}, "ref", id="ref-list"),
    ],
)
def test_a_malformed_span_record_field_refuses_with_the_vault_intact(
    tmp_path, record, field
):
    """A non-string `payload` used to reach `unicodedata.normalize` as a
    TypeError -- AFTER `_clean_vault_content`, so the operator lost the vault
    they had and got a traceback instead of a named refusal. Nothing
    downstream re-checks these fields: they are interpolated into a note
    name, a wikilink alias and an `# H1`."""
    out_dir = make_managed_vault(tmp_path)
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Ivan')} arrived.")]
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(
            tmp_path, make_nodestream(nodes, spans={"1": record}),
            make_canon({}), make_profile(), out_dir=out_dir,
        )
    assert excinfo.value.reason == "entity_markup_unresolvable", excinfo.value.reason
    assert field in str(excinfo.value), str(excinfo.value)
    assert (out_dir / "SURVIVOR.md").is_file(), (
        "the refusal must fire BEFORE _clean_vault_content"
    )


def test_a_span_record_whose_payload_disagrees_with_the_text_is_refused(tmp_path):
    """The rewriter takes the DISPLAYED alias from the text between the
    tokens and the NOTE from the record. A table from a different run than
    the text therefore writes `[[People/Pyotr|Ivan]]` -- one person's printed
    name linked to another person's note -- and every count still balances,
    so the render exits 0 and nothing ever says so. Only comparing the two
    catches it, and it has to happen before the clean."""
    out_dir = make_managed_vault(tmp_path)
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Ivan')} arrived.")]
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(
            tmp_path,
            make_nodestream(nodes, spans={"1": span("person", "Pyotr")}),
            make_canon({}), make_profile(), out_dir=out_dir,
        )
    assert excinfo.value.reason == "entity_markup_unresolvable", excinfo.value.reason
    assert "'Ivan'" in str(excinfo.value) and "'Pyotr'" in str(excinfo.value)
    assert (out_dir / "SURVIVOR.md").is_file()


# ===========================================================================
# 26. The editorial-bracket pair is decided SIDE BY SIDE. Requiring both to
#     be literal left `[Name\]` -- an operator who escaped only the closer --
#     with a bare opener and a broken link target.
# ===========================================================================

@pytest.mark.parametrize(
    "source, expected, escaped",
    [
        pytest.param("[{link}]", "\\[{piece}\\]", 1, id="both-literal"),
        pytest.param("[{link}\\]", "\\[{piece}\\]", 1, id="closer-already-escaped"),
        pytest.param("\\[{link}]", "\\[{piece}\\]", 1, id="opener-already-escaped"),
        pytest.param("\\[{link}\\]", "\\[{piece}\\]", 0, id="both-already-escaped"),
        pytest.param("\\\\[{link}]", "\\\\\\[{piece}\\]", 1, id="even-run-is-literal"),
        # Closing-side parity, the mirror of the opening side. A run of FOUR
        # backslashes before the `]` is two escaped backslashes, so the `]`
        # after them is LITERAL and must be escaped -- with the run kept
        # verbatim in front of it. A run of THREE is an escaped backslash plus
        # an escaped `]`: nothing to do on that side. Both were classified as
        # "no closing bracket at all" while the prose already claimed parity
        # on both sides, so the literal opener shipped bare.
        pytest.param("[{link}" + "\\" * 4 + "]", "\\[{piece}" + "\\" * 5 + "]", 1,
                     id="closing-even-run-is-literal"),
        pytest.param("[{link}" + "\\" * 3 + "]", "\\[{piece}" + "\\" * 3 + "]", 1,
                     id="closing-odd-run-is-escaped"),
        pytest.param("{link}", "{piece}", 0, id="no-brackets"),
        pytest.param("[{link}", "[{piece}", 0, id="unmatched-opener-untouched"),
        pytest.param("{link}]", "{piece}]", 0, id="unmatched-closer-untouched"),
    ],
)
def test_each_bracket_side_is_escaped_only_when_it_is_literal(
    tmp_path, source, expected, escaped
):
    """Whatever the reader was shown before must still be what they see --
    `[Reb Noson]` either way -- while the emitted wikilink always parses. An
    escape the operator already wrote is never doubled, and an UNMATCHED
    bracket is left alone: without a pair it is not an editorial bracket, it
    is the literal source text the unresolved-bracket contract promises."""
    piece = "[[People/Reb Noson|Reb Noson]]"
    nodes = [make_node("p1", "seg01",
                       "And " + source.format(link=ent(1, "Reb Noson")) + " spoke.")]
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Reb Noson")}),
        make_canon({}), make_profile(),
    )
    body = segment_note_texts(out_dir)[0]
    want = "And " + expected.format(piece=piece) + " spoke."
    assert want in body, f"want:\n{want}\ngot:\n{body}"
    assert manifest["entity_markup"]["brackets_escaped"] == escaped, body


def test_the_canon_linker_decides_bracket_sides_the_same_way(tmp_path):
    """The other emission site, and the one an UNDECLARED project reaches.
    Both call `_editorial_bracket_emit`; a fix applied to one only would be
    invisible until a canon-only book hit the mixed case."""
    canon = make_canon({"Иван": canon_entry("Иван", "Ivan")})
    nodes = [make_node("p1", "seg01", "And [Ivan\\] spoke.")]
    out_dir, _manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=None), canon,
        make_profile(entity_markup=False),
    )
    body = segment_note_texts(out_dir)[0]
    assert "And \\[[[People/Иван|Ivan]]\\] spoke." in body, f"got:\n{body}"


# ===========================================================================
# 27. Condition 0 reads the RAW span table and re-applies the PRODUCER's own
#     constraints. An unused garbage record used to be filtered away before
#     the preflight could see it, and a hand-edited payload carrying a pipe
#     or a sentinel rendered a malformed wikilink.
# ===========================================================================

def test_an_unused_garbage_record_is_refused_not_silently_dropped(tmp_path):
    """`_entity_markup_spans` used to drop every non-mapping record and claim
    the preflight would catch it. It could not: the inverse check only ever
    saw what that filter returned, so a record no pair cites was dropped in
    silence and the render carried on -- reporting a span count that did not
    match the table the operator was looking at."""
    out_dir = make_managed_vault(tmp_path)
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Ivan')} arrived.")]
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(
            tmp_path,
            make_nodestream(nodes, spans={"1": span("person", "Ivan"), "2": 5}),
            make_canon({}), make_profile(), out_dir=out_dir,
        )
    assert excinfo.value.reason == "entity_markup_unresolvable", excinfo.value.reason
    assert "'2'" in str(excinfo.value), str(excinfo.value)
    assert (out_dir / "SURVIVOR.md").is_file()


@pytest.mark.parametrize(
    "field, value",
    [
        pytest.param("payload", "Iv|an", id="payload-pipe"),
        pytest.param("payload", "Iv[an", id="payload-open-bracket"),
        pytest.param("payload", "Iv]an", id="payload-close-bracket"),
        pytest.param("payload", "Iv\nan", id="payload-newline"),
        pytest.param("payload", "Ivan\u27e6FNREF_1\u27e7", id="payload-footnote-sentinel"),
        pytest.param("ref", "iv|an", id="ref-pipe"),
        pytest.param("ref", "iv\u27e6VERSE_v1_abc\u27e7", id="ref-verse-placeholder"),
        pytest.param("tag", "per|son", id="tag-pipe"),
    ],
)
def test_a_record_carrying_a_producer_forbidden_character_is_refused(
    tmp_path, field, value
):
    """assemble.py refuses these the moment the span is recorded, precisely
    because this adapter interpolates the value into a wikilink alias and a
    note name. The persisted artifact is a SEPARATE input -- hand-edited, or
    written by a different version -- so re-applying the constraint here is
    what makes "nothing downstream re-checks these fields" a safe statement
    rather than a hopeful one. A pipe rendered `[[People/Iv_an|Iv|an]]`."""
    out_dir = make_managed_vault(tmp_path)
    record = {"tag": "person", "payload": "Ivan"}
    record[field] = value
    nodes = [make_node("p1", "seg01", f"{ent(1, record['payload'])} arrived.")]
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(
            tmp_path, make_nodestream(nodes, spans={"1": record}),
            make_canon({}), make_profile(), out_dir=out_dir,
        )
    assert excinfo.value.reason == "entity_markup_unresolvable", excinfo.value.reason
    assert field in str(excinfo.value), str(excinfo.value)
    assert (out_dir / "SURVIVOR.md").is_file()


def test_a_present_null_ref_is_refused_rather_than_read_as_absent(tmp_path):
    """Assembly OMITS `ref` when the attribute was absent; it never writes
    null. A present null is therefore a shape nothing in this pipeline
    produces, and reading it as "absent" would accept a record whose meaning
    is guessed rather than known."""
    out_dir = make_managed_vault(tmp_path)
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Ivan')} arrived.")]
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(
            tmp_path,
            make_nodestream(
                nodes, spans={"1": {"tag": "person", "payload": "Ivan", "ref": None}}
            ),
            make_canon({}), make_profile(), out_dir=out_dir,
        )
    assert excinfo.value.reason == "entity_markup_unresolvable", excinfo.value.reason
    assert (out_dir / "SURVIVOR.md").is_file()


def test_an_absent_ref_key_is_still_fine(tmp_path):
    """The other half of the pin above -- without it, "a null ref refuses"
    would pass just as well if EVERY ref-less record refused, which is the
    common case."""
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Ivan')} arrived.")]
    _out_dir, manifest = render_into(
        tmp_path,
        make_nodestream(nodes, spans={"1": {"tag": "person", "payload": "Ivan"}}),
        make_canon({}), make_profile(),
    )
    assert entity_note_relpaths(manifest) == ["People/Ivan.md"]


# ===========================================================================
# 28. A canon target with >=2 owners is REMOVED from the link map (#207/#588)
#     so no reader is sent to the wrong entity's note. Markup used to walk
#     around that: composition read the already-reduced map, found nothing,
#     and minted ONE note for the shared label -- asserting the very identity
#     the de-link refused to assert (#837). Nothing exercised the two paths
#     together, which is why it shipped.
# ===========================================================================

TWO_OWNERS = {
    "מֹוהַרְנַ\"ת": canon_entry("מֹוהַרְנַ\"ת", "Reb Noson", category=""),
    "מֹוהַרְנַ\"תְ": canon_entry("מֹוהַרְנַ\"תְ", "Reb Noson", category=""),
}


def test_a_marked_span_over_a_delinked_collision_refuses_instead_of_merging(tmp_path):
    """The defect itself. Two canon forms share the printed name, so the
    target is de-linked; every marked occurrence used to land on one minted
    note standing for both entries, exit 0, counts balanced."""
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Reb Noson')} wrote, and {ent(2, 'Reb Noson')} left.")]
    spans = {"1": span("person", "Reb Noson"), "2": span("person", "Reb Noson")}
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(tmp_path, make_nodestream(nodes, spans=spans),
                    make_canon(TWO_OWNERS), make_profile())
    assert excinfo.value.reason == "entity_markup_canon_collision", excinfo.value.reason
    message = str(excinfo.value)
    assert "'Reb Noson'" in message, message
    assert "2 span(s)" in message, message
    for owner in TWO_OWNERS:
        # `repr()` INCLUDING its quote delimiters: the shorter key is a prefix of
        # the longer one, so a bare substring test is satisfied by the longer form
        # alone and a regression that named only one owner would stay green.
        assert repr(owner) in message, f"{owner!r} unnamed in: {message}"


def test_the_refusal_names_the_QUALIFIED_remedy_not_just_the_file(tmp_path):
    """A group re-links a target only when EVERY owner is a member and none
    is `sense_translated`. An unqualified "add a link group" sends an
    operator to do something that cannot work for a mixed-sense collision;
    the `ref` escape is the one that always can."""
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Reb Noson')} wrote.")]
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(tmp_path, make_nodestream(nodes, spans={"1": span("person", "Reb Noson")}),
                    make_canon(TWO_OWNERS), make_profile())
    message = str(excinfo.value)
    assert "canon_link_groups.json" in message, message
    assert "EVERY owner is a member" in message, message
    assert "sense_translated" in message, message
    assert "ref attribute" in message, message


def test_the_refusal_happens_BEFORE_the_vault_is_cleaned(tmp_path):
    """The whole reason this lives in the pre-clean window. Left where
    composition runs, the same refusal would fire after
    `_clean_vault_content` had already emptied the managed vault, leaving
    the operator with neither the old book nor a new one."""
    out_dir = make_managed_vault(tmp_path)
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Reb Noson')} wrote.")]
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(tmp_path, make_nodestream(nodes, spans={"1": span("person", "Reb Noson")}),
                    make_canon(TWO_OWNERS), make_profile(), out_dir=out_dir)
    assert excinfo.value.reason == "entity_markup_canon_collision", excinfo.value.reason
    assert (out_dir / "SURVIVOR.md").is_file(), "the refusal must not cost the existing vault"


def test_a_fully_grouped_collision_composes_onto_the_primary_and_mints_nothing(tmp_path):
    """The shape of a project that has DONE the adjudication: every owner in
    one `canon_link_groups.json` group, so `_link_decision` re-links the
    target to the primary and there is nothing for markup to mint. This is
    the control -- without it, "the check fires" and "the check fires on
    everything" look identical."""
    primary = "מֹוהַרְנַ\"ת"
    groups = {source_form: primary for source_form in TWO_OWNERS}
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Reb Noson')} wrote.")]
    _out_dir, manifest = render_into(
        tmp_path,
        make_nodestream(nodes, spans={"1": span("person", "Reb Noson")},
                        extra={"link_groups": groups}),
        make_canon(TWO_OWNERS), make_profile(),
    )
    assert manifest["entity_markup"]["notes"] == 0, "canon owns this name -- mint nothing"
    assert manifest["entity_markup"]["links"] == 1


def test_an_all_sense_translated_collision_still_refuses(tmp_path):
    """`_link_decision` returns `(None, False)` when every owner is
    `sense_translated`: the cost flag says nothing was lost, because the
    target was never auto-linkable. But `build_entity_index` drops it just
    the same, so markup still minted one note over two canon entries. Keying
    this check on the COST flag rather than on the winner would have left
    exactly this merge shipping."""
    entries = {
        "אֹור": canon_entry("אֹור", "the Light", category="", basis="sense_translated"),
        "אֹורָה": canon_entry("אֹורָה", "the Light", category="", basis="sense_translated"),
    }
    nodes = [make_node("p1", "seg01", f"{ent(1, 'the Light')} shone.")]
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(tmp_path, make_nodestream(nodes, spans={"1": span("person", "the Light")}),
                    make_canon(entries), make_profile())
    assert excinfo.value.reason == "entity_markup_canon_collision", excinfo.value.reason


def test_a_collision_among_owners_of_ANOTHER_category_does_not_halt(tmp_path):
    """The tag is part of the identity. Two canon PLACE owners of "Jordan"
    de-link the place target, but `<person>Jordan</person>` is a different
    entity and its own note is correct -- section 19 already pins that.
    Halting it would break a working book and push the operator toward a
    link group asserting two unrelated entities are one referent."""
    entries = {
        "יַרְדֵן": canon_entry("יַרְדֵן", "Jordan", category="place"),
        "הַיַרְדֵן": canon_entry("הַיַרְדֵן", "Jordan", category="place"),
    }
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Jordan')} spoke.")]
    _out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Jordan")}),
        make_canon(entries), make_profile(),
    )
    assert "People/Jordan.md" in entity_note_relpaths(manifest), (
        "a person marked beside two canon PLACES is not a merge"
    )
    assert manifest["entity_markup"]["notes"] == 1, "its own person note, minted"


def test_one_compatible_owner_beside_an_incompatible_one_does_not_halt(tmp_path):
    """The boundary of the rule above. With only ONE owner able to answer
    for this tag there is nothing two-sided to merge, and minted records
    stay keyed by `(tag, label)` regardless."""
    entries = {
        "יַרְדֵן": canon_entry("יַרְדֵן", "Jordan", category="place"),
        "יַרְדֵנִי": canon_entry("יַרְדֵנִי", "Jordan", category="person"),
    }
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Jordan')} spoke.")]
    _out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Jordan")}),
        make_canon(entries), make_profile(),
    )
    assert "People/Jordan.md" in entity_note_relpaths(manifest)
    assert manifest["entity_markup"]["notes"] == 1


def test_a_distinct_ref_takes_the_span_out_of_the_collision(tmp_path):
    """The escape that always works, including for a mixed-sense collision a
    link group can never re-link: `ref` IS the identity, so the span stops
    naming the contested target at all."""
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Reb Noson')} wrote.")]
    spans = {"1": span("person", "Reb Noson", ref="Reb Noson Sternhartz")}
    _out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=spans),
        make_canon(TWO_OWNERS), make_profile(),
    )
    assert "People/Reb Noson Sternhartz.md" in entity_note_relpaths(manifest)
    assert manifest["entity_markup"]["notes"] == 1


def test_a_single_owner_target_is_untouched(tmp_path):
    """Vacuity guard on the >=2 condition: one owner composes exactly as it
    did before, so a check that halted on every canon target would be caught
    here rather than in a real book."""
    entries = {"מֹוהַרְנַ\"ת": canon_entry("מֹוהַרְנַ\"ת", "Reb Noson", category="")}
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Reb Noson')} wrote.")]
    _out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Reb Noson")}),
        make_canon(entries), make_profile(),
    )
    assert manifest["entity_markup"]["notes"] == 0, "composes onto the canon note"


def test_an_unmarked_collision_is_still_merely_delinked(tmp_path):
    """The check is about MARKED spans only. A book whose colliding name is
    never marked keeps 1.74.0's behaviour exactly -- de-linked prose, a
    WARN, and a successful render."""
    nodes = [make_node("p1", "seg01", "Reb Noson wrote, and Reb Noson left.")]
    _out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={}),
        make_canon(TWO_OWNERS), make_profile(),
    )
    assert manifest["entity_markup"] == {"spans": 0, "notes": 0, "links": 0,
                                         "brackets_escaped": 0}
    assert manifest["delink_cost"]["unlinked_occurrences_total"] == 2


def test_the_conflict_rows_lead_with_the_costliest_label(tmp_path):
    """The message shows at most five rows, so the order is what an operator
    acts on first."""
    entries = dict(TWO_OWNERS)
    entries.update({
        "א": canon_entry("א", "Chaykel", category=""),
        "ב": canon_entry("ב", "Chaykel", category=""),
    })
    rows = render_obsidian._canon_collision_conflicts(
        {"1": span("person", "Chaykel"),
         "2": span("person", "Reb Noson"),
         "3": span("person", "Reb Noson"),
         "4": span("person", "Reb Noson")},
        entries, True, None,
    )
    assert [(row["label"], row["spans"]) for row in rows] == [("Reb Noson", 3), ("Chaykel", 1)]


def test_the_message_names_EVERY_owner_not_only_the_compatible_ones(tmp_path):
    """The refusal tells the operator a link group must contain EVERY owner.
    Naming only the two that matched the tag would describe a group that
    cannot work: `_link_decision` reduces over all three, and the ungrouped
    outsider de-links the target again."""
    entries = {
        "א": canon_entry("א", "Chaykel", category=""),
        "ב": canon_entry("ב", "Chaykel", category="person"),
        "ג": canon_entry("ג", "Chaykel", category="place"),
    }
    nodes = [make_node("p1", "seg01", f"{ent(1, 'Chaykel')} arrived.")]
    with pytest.raises(render_obsidian.RenderError) as excinfo:
        render_into(tmp_path, make_nodestream(nodes, spans={"1": span("person", "Chaykel")}),
                    make_canon(entries), make_profile())
    message = str(excinfo.value)
    for owner in entries:
        assert repr(owner) in message, f"{owner!r} unnamed in: {message}"


def test_the_trigger_still_counts_only_compatible_owners(tmp_path):
    """The other half of the pin above: the FULL list is what gets displayed,
    but it must not be what decides. Two place owners plus one person owner
    leave a single owner able to answer a `person` span, so nothing refuses."""
    entries = {
        "א": canon_entry("א", "Chaykel", category="place"),
        "ב": canon_entry("ב", "Chaykel", category="place"),
        "ג": canon_entry("ג", "Chaykel", category="person"),
    }
    rows = render_obsidian._canon_collision_conflicts(
        {"1": span("person", "Chaykel")}, entries, True, None
    )
    assert rows == []


def test_the_conflict_scan_is_inert_when_collision_delinking_is_off():
    """`collision_delink=False` cannot be reached through `render()` with
    markup active -- `index` mode requires `output.target: obsidian` and so
    does de-linking -- so the flag is pinned on the helper directly rather
    than through a render that cannot exist."""
    assert render_obsidian._canon_collision_conflicts(
        {"1": span("person", "Reb Noson")}, TWO_OWNERS, False, None
    ) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
