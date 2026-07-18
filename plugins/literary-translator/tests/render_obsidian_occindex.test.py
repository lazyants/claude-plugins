"""tests/render_obsidian_occindex.test.py -- D1/D3/D4's renderer-side half
of the opt-in, source-anchored occurrence index (RFC lt-appendix-backlink-
integrity, plan section "D1 -- Opt-in `## Mentions` section" /
"D3 -- Collision de-link"): the `## Mentions` section render_obsidian.py
emits on entity notes from `nodestream["mentions"]`, the flag-gated
collision de-linking `build_entity_index` now performs, and the canon-field
safety rejections that close the marker-forgery vector -- all gated on the
SAME `_effective_mentions_enabled(profile)` predicate render() computes
from its own `profile` argument.

Self-contained (mirrors tests/render_obsidian.test.py's own convention: no
cross-test-file imports) -- loads the real module via
`importlib.util.spec_from_file_location` and drives `render()` directly
with a hand-built NodeStream/canon/profile, exactly like every other
render_obsidian test file. `nodestream["mentions"]` is hand-authored here
too, per the pinned contract shape `{source_form: [Record, ...]}` with
`Record = {source_form, seg, origin, source_block, vid?, footnote_n?}` --
this file only ever reads `seg` off a Record (the renderer is origin-
agnostic), so the fixture helper only bothers filling `seg`/`source_form`.

## Documented interpretation calls

  1. **Mentions link ordering within an entity note.** Contract only says
     "reading order, deduped per note" -- this file renders each note as a
     Markdown bullet list (`- [[identity]]`), one per DISTINCT seg the
     entity occurs in, sorted by that seg's OWN position in the book's
     `full_order` (never the Record list's own, unspecified, order).
  2. **"Byte-identical to 1.7.0" without a committed 1.7.0 fixture.** This
     file proves it differentially (`test_flag_off_render_ignores_stale_
     mentions_data_byte_identical`): the SAME canon/profile with the
     feature not effective-enabled, rendered once with populated
     `nodestream["mentions"]` and once with the key entirely absent,
     must produce byte-identical output -- the only way "flag off ignores
     stale mentions data entirely" is testable without a golden vault.
"""
from __future__ import annotations

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
        "render_obsidian_occindex_under_test", RENDER_OBSIDIAN_SRC
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_obsidian = _load_render_obsidian_module()
assert hasattr(render_obsidian, "render")


# ---------------------------------------------------------------------------
# Fixture builders (mirrors tests/render_obsidian.test.py's own helpers)
# ---------------------------------------------------------------------------


def make_node(node_id, seg, text, kind="prose", medium="plain", fnrefs=None, verses=None, order_index=0):
    return {
        "id": node_id, "seg": seg, "kind": kind, "raw_type": "PARA",
        "order_index": order_index, "medium": medium, "text": text,
        "fnrefs": fnrefs or [], "verses": verses or [],
    }


def make_nodestream(nodes, footnotes=None, target="ru", verse_mode="literal_only", mentions=None):
    ns = {
        "book": {"seg_order": sorted({n["seg"] for n in nodes}), "title": "Test Book"},
        "nodes": nodes,
        "footnotes": footnotes or [],
        "meta": {"target": target, "verse_mode": verse_mode, "apparatus_policy": "translate_all"},
    }
    if mentions is not None:
        ns["mentions"] = mentions
    return ns


def mention_record(source_form, seg, origin="block", source_block="b1", **extra):
    record = {"source_form": source_form, "seg": seg, "origin": origin, "source_block": source_block}
    record.update(extra)
    return record


def canon_entry(source_form, canonical_target_form, category="person", is_proper_name=True,
                basis="transliterated", confidence="high", note=None):
    entry = {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "canonical_target_form": canonical_target_form,
        "basis": basis,
        "confidence": confidence,
        "category": category,
    }
    if note is not None:
        entry["note"] = note
    return entry


def make_canon(entries: dict):
    return {"entries": entries, "review_queue": [], "generation_hashes": {}}


def make_profile(folders=None, parenthetical_originals="never", target_lang="ru",
                  output_target="obsidian", mentions_enabled=True):
    return {
        "target": {"language": {"code": target_lang}},
        "output": {
            "target": output_target,
            "name_display": {"parenthetical_originals": parenthetical_originals},
            "adapter_config": {
                "obsidian": {
                    "folders": folders or {},
                    "mentions_section": {"enabled": mentions_enabled},
                },
            },
        },
    }


def render_into(tmp_path, nodestream, canon, profile):
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = render_obsidian.render(nodestream, canon, profile, out_dir)
    return out_dir, manifest


def all_written_paths(out_dir, manifest):
    return [out_dir / rel for rel in manifest["written"]]


def find_file_with_content(paths, predicate):
    return [p for p in paths if p.is_file() and predicate(p.read_text(encoding="utf-8"))]


def parse_frontmatter(text: str) -> dict:
    assert text.startswith("---"), f"expected YAML frontmatter, got:\n{text[:200]!r}"
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"malformed frontmatter block:\n{text[:200]!r}"
    return yaml.safe_load(parts[1]) or {}


def entity_note_identity(out_dir, manifest, source_form):
    for rel in manifest["written"]:
        p = out_dir / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        if parse_frontmatter(text).get("source_form") == source_form:
            return rel[: -len(".md")] if rel.endswith(".md") else rel
    raise AssertionError(f"no emitted entity note found with source_form={source_form!r}")


def entity_note_path(out_dir, manifest, source_form):
    for rel in manifest["written"]:
        p = out_dir / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        if text.startswith("---") and parse_frontmatter(text).get("source_form") == source_form:
            return p
    raise AssertionError(f"no emitted entity note found with source_form={source_form!r}")


def written_note_identity_for_seg(out_dir, manifest, seg):
    """The exact relpath (minus '.md') of the SEGMENT note actually
    emitted for `seg` -- found the same black-box way
    render_obsidian.test.py's own `entity_note_identity` finds an entity
    note: by matching each written file's own frontmatter, never guessed
    or re-derived from render_obsidian.py's internal slug/index logic."""
    for rel in manifest["written"]:
        p = out_dir / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        if parse_frontmatter(text).get("seg") == seg:
            return rel[: -len(".md")] if rel.endswith(".md") else rel
    raise AssertionError(f"no segment note found for seg={seg!r} among {manifest['written']}")


# ===========================================================================
# 1. `## Mentions`: links present, reading-order, deduped per note.
# ===========================================================================


def test_mentions_section_lists_reading_order_deduped_links(tmp_path):
    canon = make_canon({"Ivan_src": canon_entry("Ivan_src", "Иван")})
    nodes = [
        make_node("n1", "seg01", "Первый абзац."),
        make_node("n2", "seg02", "Второй абзац."),
        make_node("n3", "seg03", "Третий абзац."),
    ]
    mentions = {
        "Ivan_src": [
            mention_record("Ivan_src", "seg03"),
            mention_record("Ivan_src", "seg01"),
            mention_record("Ivan_src", "seg01"),  # duplicate seg -- must dedupe to one link
            mention_record("Ivan_src", "seg02"),
        ]
    }
    ns = make_nodestream(nodes, mentions=mentions)
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity_seg01 = written_note_identity_for_seg(out_dir, manifest, "seg01")
    identity_seg02 = written_note_identity_for_seg(out_dir, manifest, "seg02")
    identity_seg03 = written_note_identity_for_seg(out_dir, manifest, "seg03")

    body = entity_note_path(out_dir, manifest, "Ivan_src").read_text(encoding="utf-8")

    assert "<!-- lt:mentions:begin -->" in body
    assert "<!-- lt:mentions:end -->" in body
    assert "## Mentions" in body

    pos1 = body.index(f"[[{identity_seg01}]]")
    pos2 = body.index(f"[[{identity_seg02}]]")
    pos3 = body.index(f"[[{identity_seg03}]]")
    assert pos1 < pos2 < pos3, f"expected reading order seg01 < seg02 < seg03, got:\n{body}"
    assert body.count(f"[[{identity_seg01}]]") == 1, f"duplicate seg mentions must dedupe -- got:\n{body}"


def test_no_eligible_mentions_means_no_section(tmp_path):
    """An entity with an empty mentions list, and one absent from
    nodestream['mentions'] entirely, must BOTH get no section at all --
    never an empty '## Mentions' heading."""
    canon = make_canon({
        "HasEmpty_src": canon_entry("HasEmpty_src", "Пусто"),
        "HasNone_src": canon_entry("HasNone_src", "Ничего"),
    })
    ns = make_nodestream(
        [make_node("n1", "seg01", "Some narrative text with no relevant names.")],
        mentions={"HasEmpty_src": []},  # HasNone_src key absent entirely
    )
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    for source_form in ("HasEmpty_src", "HasNone_src"):
        body = entity_note_path(out_dir, manifest, source_form).read_text(encoding="utf-8")
        assert "## Mentions" not in body, f"{source_form} must get NO Mentions section -- got:\n{body}"
        assert "lt:mentions:" not in body


# ===========================================================================
# 2. Back-compat: not effective-enabled ignores nodestream['mentions']
#    entirely, byte-identical output regardless of whether stale data is
#    present (proved differentially -- no committed 1.7.0 fixture exists
#    in this file).
# ===========================================================================


def test_flag_off_render_ignores_stale_mentions_data_byte_identical(tmp_path):
    canon = make_canon({
        "Ioann_src": canon_entry("Ioann_src", "Джон"),
        "Yan_src": canon_entry("Yan_src", "Джон"),
    })
    nodes = [make_node("n1", "seg01", "Джон пришёл домой.")]
    profile = make_profile(folders={"person": "people"}, mentions_enabled=False)

    stale_mentions = {"Ioann_src": [mention_record("Ioann_src", "seg01")]}
    ns_with_stale = make_nodestream(nodes, mentions=stale_mentions)
    ns_without = make_nodestream(nodes)

    out_dir_a, manifest_a = render_into(tmp_path / "a", ns_with_stale, canon, profile)
    out_dir_b, manifest_b = render_into(tmp_path / "b", ns_without, canon, profile)

    assert manifest_a["written"] == manifest_b["written"]
    for rel in manifest_a["written"]:
        text_a = (out_dir_a / rel).read_text(encoding="utf-8")
        text_b = (out_dir_b / rel).read_text(encoding="utf-8")
        assert text_a == text_b, f"flag-off render must ignore nodestream['mentions'] entirely -- {rel} differs"
        assert "lt:mentions:" not in text_a


# ===========================================================================
# 3. D3 (#207-a) collision de-link: paired flag-on/flag-off assertions.
# ===========================================================================


def test_collision_delink_true_removes_both_owners_but_both_notes_still_render(tmp_path):
    canon = make_canon({
        "Ioann_src": canon_entry("Ioann_src", "Джон"),
        "Yan_src": canon_entry("Yan_src", "Джон"),
    })
    ns = make_nodestream([make_node("n1", "seg01", "Джон пришёл домой.")])
    profile = make_profile(folders={"person": "people"}, mentions_enabled=True)

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)  # must not KeyError

    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "пришёл домой" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")
    assert "[[" not in body_text, (
        f"a collision (>=2 owners of one canonical_target_form) must be "
        f"de-linked entirely under collision_delink=True -- got:\n{body_text}"
    )
    assert "Джон пришёл домой" in body_text

    for source_form in ("Ioann_src", "Yan_src"):
        entity_note_path(out_dir, manifest, source_form)  # raises if not exactly one note found


def test_collision_delink_false_keeps_tiebreak_winner_link(tmp_path):
    """Paired with the test above (codex R2 b3): the SAME collision, with
    the feature off, must still produce the old tiebreak-winner inline
    link -- not de-linked, not both owners linked."""
    canon = make_canon({
        "Ioann_src": canon_entry("Ioann_src", "Джон"),
        "Yan_src": canon_entry("Yan_src", "Джон"),
    })
    ns = make_nodestream([make_node("n1", "seg01", "Джон пришёл домой.")])
    profile = make_profile(folders={"person": "people"}, mentions_enabled=False)

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "пришёл домой" in t
    )
    body_text = body_matches[0].read_text(encoding="utf-8")
    identity = entity_note_identity(out_dir, manifest, "Yan_src")
    assert f"[[{identity}|Джон]]" in body_text, (
        f"the shorter source_form (Yan_src) must still win the tiebreak with the flag off -- got:\n{body_text}"
    )


# ===========================================================================
# 4. Marker-forgery rejections (codex R5/R6): reserved token in any field
#    that reaches raw Markdown, and a line-break char in the two fields
#    that can become a heading.
# ===========================================================================


def test_render_rejects_reserved_token_in_canonical_target_form(tmp_path):
    canon = make_canon({"Bad_src": canon_entry("Bad_src", "forged lt:mentions:begin text")})
    ns = make_nodestream([make_node("n1", "seg01", "text")])
    profile = make_profile(folders={"person": "people"}, mentions_enabled=True)

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_into(tmp_path, ns, canon, profile)
    assert exc_info.value.reason == "mentions_reserved_token_in_canon_field"


def test_render_rejects_reserved_token_in_source_form(tmp_path):
    hostile_source_form = "lt:mentions:hostile_src"
    canon = make_canon({hostile_source_form: canon_entry(hostile_source_form, "Нечто")})
    ns = make_nodestream([make_node("n1", "seg01", "text")])
    profile = make_profile(folders={"person": "people"}, mentions_enabled=True)

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_into(tmp_path, ns, canon, profile)
    assert exc_info.value.reason == "mentions_reserved_token_in_canon_field"


def test_render_rejects_reserved_token_in_note(tmp_path):
    canon = make_canon({
        "Bad_src": canon_entry("Bad_src", "Нечто", note="this note contains lt:mentions:end forged text"),
    })
    ns = make_nodestream([make_node("n1", "seg01", "text")])
    profile = make_profile(folders={"person": "people"}, mentions_enabled=True)

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_into(tmp_path, ns, canon, profile)
    assert exc_info.value.reason == "mentions_reserved_token_in_canon_field"


_LINE_BREAK_CODEPOINTS = [0x0A, 0x0D, 0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029]


@pytest.mark.parametrize("codepoint", _LINE_BREAK_CODEPOINTS)
def test_render_rejects_line_break_in_canonical_target_form(tmp_path, codepoint):
    hostile_target = "Джон" + chr(codepoint) + "Иванов"
    canon = make_canon({"Bad_src": canon_entry("Bad_src", hostile_target)})
    ns = make_nodestream([make_node("n1", "seg01", "text")])
    profile = make_profile(folders={"person": "people"}, mentions_enabled=True)

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_into(tmp_path, ns, canon, profile)
    assert exc_info.value.reason == "mentions_field_line_break"


@pytest.mark.parametrize("codepoint", _LINE_BREAK_CODEPOINTS)
def test_render_rejects_line_break_in_source_form(tmp_path, codepoint):
    hostile_source_form = "Bad" + chr(codepoint) + "_src"
    canon = make_canon({hostile_source_form: canon_entry(hostile_source_form, "Нечто")})
    ns = make_nodestream([make_node("n1", "seg01", "text")])
    profile = make_profile(folders={"person": "people"}, mentions_enabled=True)

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_into(tmp_path, ns, canon, profile)
    assert exc_info.value.reason == "mentions_field_line_break"


# ===========================================================================
# 5. #138 sense_translated (D1): eligible for source-anchored Mentions, but
#    the inline auto-linker still excludes it (unchanged from 1.7.0/#138).
# ===========================================================================


def test_sense_translated_gets_mentions_section_but_no_inline_link(tmp_path):
    canon = make_canon({
        "Nadezhda_src": canon_entry(
            "Nadezhda_src", "Hope", category="person", is_proper_name=True,
            basis="sense_translated", note="a sense-translated speaking name",
        ),
    })
    text = "Hope walked in. Later she lost all hope entirely."
    ns = make_nodestream(
        [make_node("n1", "seg01", text)],
        mentions={"Nadezhda_src": [mention_record("Nadezhda_src", "seg01")]},
    )
    profile = make_profile(folders={"person": "people"}, mentions_enabled=True)

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)

    body_matches = find_file_with_content(all_written_paths(out_dir, manifest), lambda t: "walked in" in t)
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")
    assert "[[" not in body_text, (
        f"a sense_translated target must never be body-wikilinked, even with Mentions enabled -- got:\n{body_text}"
    )

    entity_body = entity_note_path(out_dir, manifest, "Nadezhda_src").read_text(encoding="utf-8")
    assert "## Mentions" in entity_body
    seg01_identity = written_note_identity_for_seg(out_dir, manifest, "seg01")
    assert f"[[{seg01_identity}]]" in entity_body, (
        f"a sense_translated proper name must still get its source-anchored Mentions section -- got:\n{entity_body}"
    )


# ===========================================================================
# 6. Standalone-CLI regression (codex R9 b1): a dormant obsidian sub-block
#    under a NON-obsidian output.target must never activate ANY of D1/D3 --
#    all trigger conditions present simultaneously, none must fire.
# ===========================================================================


def test_target_custom_with_dormant_flag_activates_nothing(tmp_path):
    canon = make_canon({
        "Ioann_src": canon_entry("Ioann_src", "Джон"),                          # collision partner 1
        "Yan_src": canon_entry("Yan_src", "Джон"),                               # collision partner 2
        "Hostile_src": canon_entry("Hostile_src", "forged lt:mentions:begin"),   # would-be rejection trigger
    })
    ns = make_nodestream(
        [make_node("n1", "seg01", "Джон пришёл домой.")],
        mentions={"Ioann_src": [mention_record("Ioann_src", "seg01")]},
    )
    profile = make_profile(folders={"person": "people"}, output_target="custom", mentions_enabled=True)

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)  # must not raise

    for rel in manifest["written"]:
        text = (out_dir / rel).read_text(encoding="utf-8")
        assert "## Mentions" not in text, f"{rel}: Mentions must not activate under target:custom"
        # NOT a blanket "lt:mentions: not in text": Hostile_src's own
        # canonical_target_form LEGITIMATELY contains that substring as
        # ordinary content when the feature is inactive (no active gate to
        # spoof) -- byte-identical to 1.7.0 means that text passes through
        # UNCHANGED, not stripped. The real safety property under test is
        # that the renderer never emits its OWN generated markers here.
        assert render_obsidian.MENTIONS_SECTION_MARKER_BEGIN not in text, (
            f"{rel}: the Mentions boundary marker must not appear under target:custom"
        )
        assert render_obsidian.MENTIONS_SECTION_MARKER_END not in text, (
            f"{rel}: the Mentions boundary marker must not appear under target:custom"
        )

    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "пришёл домой" in t
    )
    body_text = body_matches[0].read_text(encoding="utf-8")
    identity = entity_note_identity(out_dir, manifest, "Yan_src")
    assert f"[[{identity}|Джон]]" in body_text, (
        "a dormant flag under the wrong target must keep the OLD tiebreak-winner link, "
        f"not de-link the collision -- got:\n{body_text}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
