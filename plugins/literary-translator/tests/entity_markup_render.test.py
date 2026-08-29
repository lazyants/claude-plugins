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
  2. **One integration case** (section 13) builds a real `durable_root`, runs
     the ACTUAL `assemble.py` as a subprocess, and feeds the
     `nodestream.json` it wrote into `render()`. A hand-authored fixture on
     BOTH sides of a wire contract is exactly how a broken contract passes two
     green suites, so at least one case has to cross the seam for real.

## Delivery is asserted against the WRITTEN FILES

Section 9 reads the emitted `.md` files off disk rather than trusting
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
#    (assemble.py refuses them -- pinned across the seam in section 13), and
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
    plain_dir, plain_manifest = render_into(
        tmp_path, make_nodestream(nodes, spans=None), canon,
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
def test_a_non_index_profile_ignores_a_planted_entity_markup_key(tmp_path, profile_kwargs):
    nodes = [make_node("p1", "seg01", f"{ent(1, 'John')} spoke.")]
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "John")}),
        make_canon({}), make_profile(**profile_kwargs),
    )
    assert "entity_markup" not in manifest
    assert entity_note_relpaths(manifest) == [], "no markup note may be minted"
    body = segment_note_texts(out_dir)[0]
    assert ent(1, "John") in body, (
        "the key is ignored ENTIRELY -- the renderer neither resolves nor "
        f"refuses it. Got:\n{body}"
    )


# ===========================================================================
# 16. The editorial-bracket collision (§7), at BOTH emission sites.
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


def test_bracketed_marked_name_escapes_the_outer_pair(tmp_path):
    """The new pre-pass insertion point, same rule."""
    nodes = [make_node("p1", "seg01", f"And [{ent(1, 'Reb Noson')}] spoke.")]
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Reb Noson")}),
        make_canon({}), make_profile(),
    )
    body = segment_note_texts(out_dir)[0]
    assert "And \\[[[People/Reb Noson|Reb Noson]]\\] spoke." in body, f"got:\n{body}"
    assert manifest["entity_markup"]["brackets_escaped"] == 1


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


def test_already_escaped_brackets_are_left_alone(tmp_path):
    nodes = [make_node("p1", "seg01", f"And \\[{ent(1, 'Reb Noson')}\\] spoke.")]
    out_dir, manifest = render_into(
        tmp_path, make_nodestream(nodes, spans={"1": span("person", "Reb Noson")}),
        make_canon({}), make_profile(),
    )
    body = segment_note_texts(out_dir)[0]
    assert "And \\[[[People/Reb Noson|Reb Noson]]\\] spoke." in body, f"got:\n{body}"
    assert "\\\\[" not in body, "an operator's own escape must not be re-escaped"
    assert manifest["entity_markup"]["brackets_escaped"] == 0


# ===========================================================================
# 17. validate_backlinks.py against a vault that contains markup notes.
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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
