"""tests/render_obsidian_link_groups.test.py -- #588's two halves in
render_obsidian.py: the one-entity LINK GROUPS that let an
upstream-established group of canon forms re-link a shared
`canonical_target_form` instead of being de-linked, and the `delink_cost`
measurement that finally puts a NUMBER on what de-linking costs a book.

Self-contained (mirrors tests/render_obsidian.test.py's convention: no
cross-test-file imports) -- loads the real module via
`importlib.util.spec_from_file_location` and drives `render()` directly with
a hand-built NodeStream/canon/profile.

The specific things pinned here, each one a defect that a codex plan-review
round constructed as a concrete failure before it was written:

  - a grouped collision re-links to the group's PRIMARY note; a group plus
    an outsider, or a group containing a `sense_translated` owner, still
    de-links (the anti-flood invariant outranks a routing preference);
  - a single-owner target NEVER moves, group or no group, and an absent map
    renders byte-identically -- the blast radius is exactly "targets that
    would otherwise be de-linked";
  - `delinked_owners_by_target` agrees with `validate_backlinks.py`'s own
    "in the no-delink map, absent from the delink map" two-call diff, INCLUDING
    the all-`sense_translated` case where de-linking costs nothing;
  - the count is EVERY occurrence, not one per block, and it survives the
    case where NOTHING is linkable at all (`build_entity_index` returns
    `(None, {})` there, and the linker's early return used to skip the whole
    diagnostic pass -- a book whose every name collides reported zero);
  - the count is taken on the linker's own input, so a gloss linked BEFORE
    the renderer wraps it in `> *Literal: …*` is counted, and the segment
    title duplicated into YAML frontmatter is not counted twice;
  - a target containing regex metacharacters is escaped, not compiled;
  - `inline_links_emitted` counts links this render inserted, never a
    `[[…]]` that was already in the translated source text;
  - a consumed map naming a non-canon primary is refused BEFORE the existing
    vault is cleaned -- a rejected input must not cost the operator the
    vault already on disk.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"

assert RENDER_OBSIDIAN_SRC.is_file(), f"render_obsidian.py not found at {RENDER_OBSIDIAN_SRC}"


def _load_render_obsidian_module():
    spec = importlib.util.spec_from_file_location(
        "render_obsidian_link_groups_under_test", RENDER_OBSIDIAN_SRC
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_obsidian = _load_render_obsidian_module()


# ---------------------------------------------------------------------------
# Fixture builders (mirrors tests/render_obsidian_occindex.test.py's helpers)
# ---------------------------------------------------------------------------

def make_node(node_id, seg, text, kind="prose", order_index=0, verses=None, fnrefs=None):
    return {
        "id": node_id, "seg": seg, "kind": kind, "raw_type": "PARA",
        "order_index": order_index, "medium": "plain", "text": text,
        "fnrefs": fnrefs or [], "verses": verses or [],
    }


def make_nodestream(nodes, footnotes=None, link_groups=None, target="en"):
    ns = {
        "book": {"seg_order": sorted({n["seg"] for n in nodes}), "title": "Test Book"},
        "nodes": nodes,
        "footnotes": footnotes or [],
        "meta": {"target": target, "verse_mode": "literal_only", "apparatus_policy": "translate_all"},
    }
    if link_groups is not None:
        ns["link_groups"] = link_groups
    return ns


def canon_entry(source_form, canonical_target_form, basis="transliterated"):
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "canonical_target_form": canonical_target_form,
        "basis": basis,
        "confidence": "high",
        "category": "person",
    }


def make_canon(entries: dict):
    return {"entries": entries, "review_queue": [], "generation_hashes": {}}


def make_profile(output_target="obsidian", mentions_enabled=False, parenthetical_originals="never"):
    return {
        "target": {"language": {"code": "en"}},
        "output": {
            "target": output_target,
            "name_display": {"parenthetical_originals": parenthetical_originals},
            "adapter_config": {
                "obsidian": {
                    "folders": {},
                    "mentions_section": {"enabled": mentions_enabled},
                },
            },
        },
    }


def render_into(tmp_path, nodestream, canon, profile, name="out"):
    out_dir = tmp_path / name
    out_dir.mkdir(parents=True, exist_ok=True)
    result = render_obsidian.render(nodestream, canon, profile, out_dir)
    return out_dir, result


def vault_text(out_dir, result):
    """Every written note's text, joined -- for coarse presence assertions."""
    return "\n".join(
        (out_dir / rel).read_text(encoding="utf-8") for rel in result["written"]
    )


def segment_note_text(out_dir, result):
    for rel in result["written"]:
        if "/" not in rel:  # segment notes live at the vault root
            return (out_dir / rel).read_text(encoding="utf-8")
    raise AssertionError("no segment note written")


def marker_payload(out_dir):
    path = out_dir / render_obsidian.VAULT_MARKER_FILENAME
    return json.loads(path.read_text(encoding="utf-8"))


TWO_OWNERS = {
    "משה לייב": canon_entry("משה לייב", "Moyshe-Leyb"),
    "משה־לייב": canon_entry("משה־לייב", "Moyshe-Leyb"),
}
ONE_GROUP = {"משה לייב": "משה לייב", "משה־לייב": "משה לייב"}


# ---------------------------------------------------------------------------
# Group semantics
# ---------------------------------------------------------------------------

def test_grouped_collision_relinks_to_the_group_primary(tmp_path):
    """The #588 fix: two spellings of one man stop silencing his name."""
    ns = make_nodestream(
        [make_node("n1", "seg01", "Moyshe-Leyb walked in.")], link_groups=ONE_GROUP
    )
    out_dir, result = render_into(tmp_path, ns, make_canon(TWO_OWNERS), make_profile())
    text = segment_note_text(out_dir, result)
    # ...and to the PRIMARY's own note, not the other member's -- the identity
    # resolved by the renderer's own `_resolve_entity_notes`, never guessed.
    relpaths = render_obsidian._resolve_entity_notes(TWO_OWNERS, {})
    primary_note = relpaths["\u05de\u05e9\u05d4 \u05dc\u05d9\u05d9\u05d1"][: -len(".md")]
    other_note = relpaths["\u05de\u05e9\u05d4\u05be\u05dc\u05d9\u05d9\u05d1"][: -len(".md")]
    assert primary_note != other_note
    assert f"[[{primary_note}|Moyshe-Leyb]]" in text, text
    assert other_note not in text


def test_ungrouped_collision_still_delinks(tmp_path):
    ns = make_nodestream([make_node("n1", "seg01", "Moyshe-Leyb walked in.")])
    out_dir, result = render_into(tmp_path, ns, make_canon(TWO_OWNERS), make_profile())
    assert "[[" not in segment_note_text(out_dir, result)


def test_group_plus_an_outsider_still_delinks(tmp_path):
    """Two members of one group plus a THIRD, ungrouped owner of the same
    target is still >=2 distinct entities -- exactly the misattribution
    de-linking exists to prevent."""
    entries = dict(TWO_OWNERS)
    entries["Other"] = canon_entry("Other", "Moyshe-Leyb")
    ns = make_nodestream(
        [make_node("n1", "seg01", "Moyshe-Leyb walked in.")], link_groups=ONE_GROUP
    )
    out_dir, result = render_into(tmp_path, ns, make_canon(entries), make_profile())
    assert "[[" not in segment_note_text(out_dir, result)


def test_sense_translated_member_still_suppresses_the_link(tmp_path):
    """#138's anti-flood invariant outranks a group's routing preference: a
    sense-rendered target is an ordinary word by construction."""
    entries = {
        "תקווה": canon_entry("תקווה", "Hope", basis="sense_translated"),
        "הופ": canon_entry("הופ", "Hope"),
    }
    ns = make_nodestream(
        [make_node("n1", "seg01", "Hope is a thing with feathers.")],
        link_groups={"תקווה": "הופ", "הופ": "הופ"},
    )
    out_dir, result = render_into(tmp_path, ns, make_canon(entries), make_profile())
    assert "[[" not in segment_note_text(out_dir, result)


def test_single_owner_target_never_moves_under_a_group(tmp_path):
    """A group may only touch a target that would otherwise be DE-LINKED."""
    entries = {
        "אברהם": canon_entry("אברהם", "Avraham"),
        "אברם": canon_entry("אברם", "Avram"),
    }
    groups = {"אברהם": "אברהם", "אברם": "אברהם"}
    nodes = [make_node("n1", "seg01", "Avram and Avraham spoke.")]
    out_a, res_a = render_into(tmp_path, make_nodestream(nodes), make_canon(entries),
                               make_profile(), name="plain")
    out_b, res_b = render_into(tmp_path, make_nodestream(nodes, link_groups=groups),
                               make_canon(entries), make_profile(), name="grouped")
    assert segment_note_text(out_a, res_a) == segment_note_text(out_b, res_b)


def test_absent_map_renders_identically_to_no_key_at_all(tmp_path):
    nodes = [make_node("n1", "seg01", "Moyshe-Leyb walked in.")]
    out_a, res_a = render_into(tmp_path, make_nodestream(nodes), make_canon(TWO_OWNERS),
                               make_profile(), name="absent")
    out_b, res_b = render_into(tmp_path, make_nodestream(nodes, link_groups={}),
                               make_canon(TWO_OWNERS), make_profile(), name="empty")
    assert vault_text(out_a, res_a) == vault_text(out_b, res_b)


def test_groups_are_inert_on_the_dormant_custom_target_path(tmp_path):
    """`target: "custom"` with a dormant obsidian block activates neither
    collision de-linking nor its inverse: the old tiebreak still picks a
    winner, and the group changes nothing."""
    nodes = [make_node("n1", "seg01", "Moyshe-Leyb walked in.")]
    profile = make_profile(output_target="custom")
    out_a, res_a = render_into(tmp_path, make_nodestream(nodes), make_canon(TWO_OWNERS),
                               profile, name="nogroup")
    out_b, res_b = render_into(tmp_path, make_nodestream(nodes, link_groups=ONE_GROUP),
                               make_canon(TWO_OWNERS), profile, name="group")
    assert vault_text(out_a, res_a) == vault_text(out_b, res_b)
    assert res_b["delink_cost"]["delinked_targets"] == []


# ---------------------------------------------------------------------------
# delinked_owners_by_target == validate_backlinks.py's two-call diff
# ---------------------------------------------------------------------------

def _two_call_diff(entries, note_ids, primary_by_source_form=None):
    """validate_backlinks._renderer_delinked_targets' own definition."""
    _, no_delink = render_obsidian.build_entity_index(
        entries, note_ids, collision_delink=False,
        primary_by_source_form=primary_by_source_form)
    _, delinked = render_obsidian.build_entity_index(
        entries, note_ids, collision_delink=True,
        primary_by_source_form=primary_by_source_form)
    return set(no_delink) - set(delinked)


@pytest.mark.parametrize("entries,groups", [
    (TWO_OWNERS, None),
    (TWO_OWNERS, ONE_GROUP),
    ({"a": canon_entry("a", "X"), "b": canon_entry("b", "Y")}, None),
    # every owner sense_translated -- never linked either way, so de-linking
    # costs NOTHING and must not be reported as a cost.
    ({"a": canon_entry("a", "Hope", basis="sense_translated"),
      "b": canon_entry("b", "Hope", basis="sense_translated")}, None),
    # one sense_translated owner + one narrative owner (#240's own case)
    ({"a": canon_entry("a", "Hope", basis="sense_translated"),
      "b": canon_entry("b", "Hope")}, None),
    ({}, None),
])
def test_delinked_owners_by_target_matches_the_two_call_diff(entries, groups):
    note_ids = {sf: f"People/{i}" for i, sf in enumerate(entries)}
    assert set(render_obsidian.delinked_owners_by_target(entries, groups)) == _two_call_diff(
        entries, note_ids, groups
    )


# ---------------------------------------------------------------------------
# The cost measurement
# ---------------------------------------------------------------------------

def test_every_occurrence_is_counted_not_one_per_block(tmp_path):
    ns = make_nodestream([
        make_node("n1", "seg01", "Moyshe-Leyb met Moyshe-Leyb, and Moyshe-Leyb laughed."),
        make_node("n2", "seg01", "Moyshe-Leyb again.", order_index=1),
    ])
    _, result = render_into(tmp_path, ns, make_canon(TWO_OWNERS), make_profile())
    cost = result["delink_cost"]
    assert cost["unlinked_occurrences_total"] == 4
    assert cost["delinked_targets"][0] == {
        "canonical_target_form": "Moyshe-Leyb",
        "owners": ["משה לייב", "משה־לייב"],
        "unlinked_occurrences": 4,
    }


def test_all_names_colliding_still_reports_the_cost(tmp_path):
    """The regression that made this metric worthless on the very book it
    was written for: with nothing linkable at all `build_entity_index`
    returns `(None, {})`, and the linker's early return skipped the whole
    diagnostic pass -- reporting zero for a book where EVERY name is
    silenced."""
    ns = make_nodestream([make_node("n1", "seg01", "Moyshe-Leyb. Moyshe-Leyb.")])
    _, result = render_into(tmp_path, ns, make_canon(TWO_OWNERS), make_profile())
    assert result["delink_cost"]["unlinked_occurrences_total"] == 2
    assert result["delink_cost"]["inline_links_emitted"] == 0


def test_a_group_drops_the_cost_to_zero(tmp_path):
    ns = make_nodestream(
        [make_node("n1", "seg01", "Moyshe-Leyb. Moyshe-Leyb.")], link_groups=ONE_GROUP
    )
    _, result = render_into(tmp_path, ns, make_canon(TWO_OWNERS), make_profile())
    assert result["delink_cost"] == {
        "delinked_targets": [],
        "unlinked_occurrences_total": 0,
        "inline_links_emitted": 1,  # first occurrence per block
    }


def test_a_delinked_name_inside_a_longer_linked_name_is_not_double_charged(tmp_path):
    """The wikilink rule links only the FIRST occurrence per block, so a
    linked name's later occurrences are plain text. A de-linked short name
    nested inside one of them belongs to the LONGER name -- charging it to
    the short one would inflate the cost with occurrences no reader
    experiences as a missing link."""
    entries = dict(TWO_OWNERS)
    entries["long"] = canon_entry("long", "Moyshe-Leyb of Berditchev")
    ns = make_nodestream([make_node(
        "n1", "seg01",
        "Moyshe-Leyb of Berditchev spoke. Moyshe-Leyb of Berditchev left.",
    )])
    _, result = render_into(tmp_path, ns, make_canon(entries), make_profile())
    # Two occurrences of the LONG name: the first is linked, the second is
    # plain -- and neither is a de-linked "Moyshe-Leyb" occurrence.
    assert result["delink_cost"]["unlinked_occurrences_total"] == 0
    assert result["delink_cost"]["inline_links_emitted"] == 1


def test_a_gloss_linked_before_its_literal_wrapper_is_counted(tmp_path):
    """The count is taken on the linker's INPUT, not on the finished
    markdown: `_render_verse_block` links the gloss and only then wraps it
    as `> *Literal: …*`, so a final-text scan could attribute the gloss
    occurrence to a longer target that only exists after wrapping."""
    verse = {
        "vid": "v1", "placeholder": "⟦VERSE_v1⟧", "mount": "block",
        "content": {"rendered": "A song for Rivka", "literal_gloss": "Moyshe-Leyb sings"},
    }
    node = make_node("n1", "seg01", "⟦VERSE_v1⟧", kind="verse", verses=[verse])
    entries = dict(TWO_OWNERS)
    entries["solo"] = canon_entry("solo", "Rivka")   # keeps the linkable pattern non-None
    _, result = render_into(tmp_path, make_nodestream([node]),
                            make_canon(entries), make_profile())
    assert result["delink_cost"]["unlinked_occurrences_total"] == 1
    assert result["delink_cost"]["inline_links_emitted"] == 1


def test_the_frontmatter_title_copy_is_not_counted(tmp_path):
    """A segment's heading text is duplicated into YAML `title`; the linker
    never sees the frontmatter, so the heading occurrence is counted once."""
    entries = dict(TWO_OWNERS)
    entries["solo"] = canon_entry("solo", "Rivka")   # keeps the linkable pattern non-None
    ns = make_nodestream([
        make_node("n1", "seg01", "Moyshe-Leyb", kind="heading"),
        make_node("n2", "seg01", "Rivka waited.", order_index=1),
    ])
    out_dir, result = render_into(tmp_path, ns, make_canon(entries), make_profile())
    text = segment_note_text(out_dir, result)
    assert "title: Moyshe-Leyb" in text          # the heading text, copied into YAML
    assert "# Moyshe-Leyb" in text               # ...and the heading itself
    # A post-hoc scan of THIS note would find the name twice. The linker sees
    # it once, which is the number of occurrences a reader actually meets.
    assert result["delink_cost"]["unlinked_occurrences_total"] == 1


def test_regex_metacharacters_in_a_target_are_escaped(tmp_path):
    entries = {
        "a": canon_entry("a", "C++ (the language)"),
        "b": canon_entry("b", "C++ (the language)"),
    }
    ns = make_nodestream([make_node("n1", "seg01", "He wrote C++ (the language) daily.")])
    _, result = render_into(tmp_path, ns, make_canon(entries), make_profile())
    assert result["delink_cost"]["unlinked_occurrences_total"] == 1


def test_a_preexisting_wikilink_is_not_counted_as_emitted(tmp_path):
    """`_Linker` PRESERVES a `[[…]]` already present in the translated
    source text (it is a protected span). Counting emitted links by
    re-scanning the finished note would credit this render with a link it
    never inserted."""
    entries = {"solo": canon_entry("solo", "Rivka")}
    ns = make_nodestream([make_node("n1", "seg01", "See [[People/Elsewhere|Elsewhere]] first.")])
    _, result = render_into(tmp_path, ns, make_canon(entries), make_profile())
    assert result["delink_cost"]["inline_links_emitted"] == 0


def test_warn_names_the_number_on_stderr(tmp_path, capsys):
    ns = make_nodestream([make_node("n1", "seg01", "Moyshe-Leyb. Moyshe-Leyb.")])
    render_into(tmp_path, ns, make_canon(TWO_OWNERS), make_profile())
    err = capsys.readouterr().err
    assert "WARN: collision de-linking left 2 occurrence(s)" in err
    assert "canon_link_groups.json" in err


def test_no_warn_when_nothing_was_delinked(tmp_path, capsys):
    entries = {"solo": canon_entry("solo", "Rivka")}
    ns = make_nodestream([make_node("n1", "seg01", "Rivka waited.")])
    render_into(tmp_path, ns, make_canon(entries), make_profile())
    assert "collision de-linking" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The vault marker round trip
# ---------------------------------------------------------------------------

def test_the_measured_block_is_stamped_into_the_vault_marker(tmp_path):
    ns = make_nodestream([make_node("n1", "seg01", "Moyshe-Leyb. Moyshe-Leyb.")])
    out_dir, result = render_into(tmp_path, ns, make_canon(TWO_OWNERS), make_profile())
    payload = marker_payload(out_dir)
    assert payload["managed_by"] == "literary-translator"
    assert payload["target"] == "obsidian"
    assert payload["delink_cost"] == result["delink_cost"]


def test_extra_marker_keys_do_not_break_the_ownership_gate(tmp_path):
    """A second render into the same vault must still recognize it as one
    this adapter owns -- the marker is the ownership token, and #588 gave it
    a payload."""
    ns = make_nodestream([make_node("n1", "seg01", "Moyshe-Leyb.")])
    out_dir, _ = render_into(tmp_path, ns, make_canon(TWO_OWNERS), make_profile())
    assert render_obsidian._is_valid_vault_marker(
        out_dir / render_obsidian.VAULT_MARKER_FILENAME
    )
    render_obsidian.render(ns, make_canon(TWO_OWNERS), make_profile(), out_dir)


def test_a_failed_render_leaves_no_stale_measurement_behind(tmp_path, monkeypatch):
    """An interrupted re-render must not leave the PREVIOUS render's number
    standing over notes it no longer describes: the marker is re-stamped
    WITHOUT a measurement as soon as the old vault is cleaned, and the
    measured one only on success."""
    ns = make_nodestream([make_node("n1", "seg01", "Moyshe-Leyb. Moyshe-Leyb.")])
    out_dir, _ = render_into(tmp_path, ns, make_canon(TWO_OWNERS), make_profile())
    assert marker_payload(out_dir)["delink_cost"]["unlinked_occurrences_total"] == 2

    def explode(*_args, **_kwargs):
        raise RuntimeError("killed mid-render")

    monkeypatch.setattr(render_obsidian, "_write_note", explode)
    with pytest.raises(RuntimeError):
        render_obsidian.render(ns, make_canon(TWO_OWNERS), make_profile(), out_dir)
    monkeypatch.undo()

    payload = marker_payload(out_dir)
    assert payload["managed_by"] == "literary-translator"  # still owned
    assert "delink_cost" not in payload  # ...but no longer claiming a measurement


# ---------------------------------------------------------------------------
# Consumed-map validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_map,needle", [
    ({"משה לייב": "Ghost", "משה־לייב": "Ghost"}, "not a canon entry"),
    ({"Ghost": "משה לייב"}, "not a canon entry"),
    ({"משה לייב": "משה־לייב"}, "not a member of its own group"),
    ("not-a-map", "must be an object"),
    ({"משה לייב": 7}, "string-to-string"),
])
def test_an_invalid_consumed_map_is_refused(tmp_path, bad_map, needle):
    ns = make_nodestream([make_node("n1", "seg01", "Moyshe-Leyb.")], link_groups=bad_map)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(render_obsidian.RenderError) as exc:
        render_obsidian.render(ns, make_canon(TWO_OWNERS), make_profile(), out_dir)
    assert exc.value.reason == "link_groups_invalid"
    assert needle in str(exc.value)


def test_a_refused_map_does_not_destroy_the_existing_vault(tmp_path):
    """Validation happens BEFORE the clean: the standalone CLI can be handed
    a hand-edited NodeStream, and a rejected input must not cost the
    operator the last good vault."""
    good = make_nodestream([make_node("n1", "seg01", "Moyshe-Leyb.")])
    out_dir, result = render_into(tmp_path, good, make_canon(TWO_OWNERS), make_profile())
    before = {rel: (out_dir / rel).read_bytes() for rel in result["written"]}
    marker_before = (out_dir / render_obsidian.VAULT_MARKER_FILENAME).read_bytes()

    bad = make_nodestream([make_node("n1", "seg01", "Moyshe-Leyb.")],
                          link_groups={"משה לייב": "Ghost", "משה־לייב": "Ghost"})
    with pytest.raises(render_obsidian.RenderError):
        render_obsidian.render(bad, make_canon(TWO_OWNERS), make_profile(), out_dir)

    after = {rel: (out_dir / rel).read_bytes() for rel in result["written"]
             if (out_dir / rel).is_file()}
    assert after == before
    assert (out_dir / render_obsidian.VAULT_MARKER_FILENAME).read_bytes() == marker_before


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
