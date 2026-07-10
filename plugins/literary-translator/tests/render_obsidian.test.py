"""tests/render_obsidian.test.py -- scripts/render_obsidian.py, the
NodeStream -> Obsidian-vault adapter (see references/assembly-and-output.md
and references/output-target-adapters/README.md, Phase 1).

## Invocation style

``render_obsidian.py`` exposes a pure function,
``render(nodestream, canon, profile, out_dir) -> {"written": [...], "kind": ...}``
(contract §10) -- this file imports the real module via
``importlib.util.spec_from_file_location`` and calls ``render()`` directly,
IN-PROCESS, with a HAND-CONSTRUCTED NodeStream dict matching the exact §5
shape. This deliberately does NOT depend on assemble.py existing/working --
render_obsidian.py's own contract is independently testable from a NodeStream
literal, which is the whole point of the NodeStream IR boundary (contract §5:
"keeps the two adapters diverging only at render time").

Every test locates its expected output file(s) by scanning the returned
``written`` manifest + its content (never by guessing an unspecified exact
filename/path convention for the rendered "book" markdown itself, which the
contract does not pin down -- only the ENTITY-NOTE side of the vault has a
contractually exact frontmatter field set, contract §8).

## Documented interpretation calls (genuine contract ambiguities)

  1. **canonical_target_form tiebreak when two entries share the exact same
     value.** Contract §8 says only "pick one and test it" -- this file
     picks: shortest ``source_form`` wins, then lexicographic. If the real
     implementation picks a different (but equally documented) tiebreak,
     that is a flagged ambiguity for the lead, not a bug in either side.
  2. **An unmapped-but-syntactically-safe category** (not blank, not in
     ``adapter_config.obsidian.folders``, but passes the allow-list) is left
     UNTESTED here -- only the two contract-unambiguous cases (mapped, and
     blank/absent -> "other") plus the SECURITY case (hostile value must
     never escape and must not literally become an unsafe path segment) are
     asserted.
  3. **RTL frontmatter's exact mechanism** (field name, e.g. ``direction:
     rtl`` vs a ``cssclasses`` entry) is not specified anywhere yet
     (references/output-target-adapters/obsidian.md, owned by a parallel
     teammate, does not exist at the time this file was written) -- the RTL
     test here only asserts SOME case-insensitive "rtl" marker appears for
     an RTL target language and does NOT appear for a non-RTL one, without
     pinning the exact field.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
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
    f"render_obsidian.py not found at {RENDER_OBSIDIAN_SRC} -- Phase 1 "
    "(contract §8/§10) has not landed yet"
)


def _load_render_obsidian_module():
    spec = importlib.util.spec_from_file_location(
        "render_obsidian_under_test", RENDER_OBSIDIAN_SRC
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_obsidian = _load_render_obsidian_module()
assert hasattr(render_obsidian, "render"), (
    "render_obsidian.py must expose render(nodestream, canon, profile, out_dir) "
    "per contract §10"
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def make_node(node_id, seg, text, kind="prose", medium="plain", fnrefs=None, verses=None, order_index=0):
    return {
        "id": node_id, "seg": seg, "kind": kind, "raw_type": "PARA",
        "order_index": order_index, "medium": medium, "text": text,
        "fnrefs": fnrefs or [], "verses": verses or [],
    }


def make_nodestream(nodes, footnotes=None, target="ru", verse_mode="literal_only"):
    return {
        "book": {"seg_order": sorted({n["seg"] for n in nodes}), "title": "Test Book"},
        "nodes": nodes,
        "footnotes": footnotes or [],
        "meta": {"target": target, "verse_mode": verse_mode, "apparatus_policy": "translate_all"},
    }


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


def make_profile(folders=None, parenthetical_originals="never", target_lang="ru"):
    return {
        "target": {"language": {"code": target_lang}},
        "output": {
            "name_display": {"parenthetical_originals": parenthetical_originals},
            "adapter_config": {"obsidian": {"folders": folders or {}}},
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
    matches = [p for p in paths if p.is_file() and predicate(p.read_text(encoding="utf-8"))]
    return matches


def parse_frontmatter(text: str) -> dict:
    """Splits a `---\\n...\\n---` YAML frontmatter block off the top of an
    Obsidian note and parses it. Raises AssertionError with the file's own
    head if no frontmatter block is found."""
    assert text.startswith("---"), f"expected YAML frontmatter, got:\n{text[:200]!r}"
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"malformed frontmatter block:\n{text[:200]!r}"
    return yaml.safe_load(parts[1]) or {}


def entity_note_identity(out_dir, manifest, source_form):
    """The exact relpath (minus '.md') of the entity note ACTUALLY EMITTED
    for `source_form` -- found by matching each written file's own
    frontmatter `source_form` field, never guessed or re-derived from an
    internal helper. Since review round 2 (FIXSPEC_lt_review2.md C2), the
    wikilink target is this FOLDER-QUALIFIED relpath (e.g. "people/Ivan"),
    not a bare stem -- two entities in different folders can otherwise
    share one stem and collide on the same `[[stem|...]]` target. Deriving
    the expected target from the REAL written output (rather than calling
    render_obsidian.py's own internal resolver a second time) means this
    stays a true black-box check of the "link target == emitted note
    identity" invariant, not a test that would share a latent bug with the
    very helper it's supposed to be checking."""
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


# ===========================================================================
# 1. Entity notes: one per canon entry, correct frontmatter fields.
# ===========================================================================


def test_entity_note_frontmatter_carries_the_documented_fields(tmp_path):
    canon = make_canon({
        "Ivan_src": canon_entry("Ivan_src", "Иван", category="person",
                                 confidence="high", note="a test note"),
    })
    ns = make_nodestream([make_node("p1", "seg01", "Ordinary prose, no name mention.")])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    assert manifest["kind"] == "vault"

    paths = all_written_paths(out_dir, manifest)
    matches = find_file_with_content(paths, lambda t: "Ivan_src" in t)
    assert len(matches) == 1, f"expected exactly one entity note for Ivan_src, found {len(matches)}"
    fm = parse_frontmatter(matches[0].read_text(encoding="utf-8"))

    assert fm["source_form"] == "Ivan_src"
    assert fm["canonical_target_form"] == "Иван"
    assert fm["category"] == "person"
    assert fm["is_proper_name"] is True
    assert fm["basis"] == "transliterated"
    assert fm["confidence"] == "high"
    assert fm["note"] == "a test note"
    assert "notes" not in fm, "the frontmatter field must be 'note' SINGULAR, not 'notes'"


def test_realia_entry_not_a_name_still_gets_a_note(tmp_path):
    """basis:'not_a_name' / is_proper_name:false entries are realia -- still
    get an entity note, matched into the wikilink pass the same way as a
    proper name (contract §8)."""
    canon = make_canon({
        "Baguette_src": canon_entry("Baguette_src", "багет", category="object",
                                     is_proper_name=False, basis="not_a_name"),
    })
    ns = make_nodestream([make_node("p1", "seg01", "Он купил багет утром.")])
    profile = make_profile(folders={"object": "objects"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    paths = all_written_paths(out_dir, manifest)

    # "Baguette_src" legitimately appears in TWO places -- the entity
    # note's own frontmatter AND the wikilink target in the narrative page
    # -- so distinguish by parsing each candidate's frontmatter rather than
    # a bare substring count.
    note_matches = [
        p for p in find_file_with_content(paths, lambda t: "Baguette_src" in t)
        if parse_frontmatter(p.read_text(encoding="utf-8")).get("source_form") == "Baguette_src"
    ]
    assert len(note_matches) == 1
    fm = parse_frontmatter(note_matches[0].read_text(encoding="utf-8"))
    assert fm["is_proper_name"] is False
    assert fm["basis"] == "not_a_name"

    identity = entity_note_identity(out_dir, manifest, "Baguette_src")
    book_matches = find_file_with_content(
        paths, lambda t: f"[[{identity}|багет]]" in t
    )
    assert len(book_matches) >= 1, "a not_a_name/realia entry must still be wikilinked in body text"


# ===========================================================================
# 2. category -> folder routing, incl. unknown -> other, incl. the
#    path-safety allow-list (contract §8, SECURITY hard requirement).
# ===========================================================================


def test_category_routes_into_its_mapped_folder(tmp_path):
    canon = make_canon({"Ivan_src": canon_entry("Ivan_src", "Иван", category="person")})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    matches = [p for p in manifest["written"] if "Ivan_src" in (out_dir / p).read_text(encoding="utf-8")]
    assert any(rel.split("/")[0] == "people" or Path(rel).parts[0] == "people" for rel in matches), (
        f"expected the note under the mapped 'people' folder, got: {matches}"
    )


def test_absent_category_routes_to_other(tmp_path):
    canon = make_canon({
        "Unclassified_src": {
            "source_form": "Unclassified_src", "is_proper_name": True,
            "canonical_target_form": "Некто", "basis": "transliterated", "confidence": "low",
            # category deliberately absent
        }
    })
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    matches = [p for p in manifest["written"] if "Unclassified_src" in (out_dir / p).read_text(encoding="utf-8")]
    assert any(Path(rel).parts[0] == "other" for rel in matches), (
        f"an absent category must default to the 'other' folder, got: {matches}"
    )


def test_hostile_category_value_never_escapes_the_vault(tmp_path):
    canon = make_canon({
        "Hostile_src": canon_entry("Hostile_src", "Опасно", category="../../../../etc"),
    })
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)

    try:
        manifest = render_obsidian.render(ns, canon, profile, out_dir)
    except SystemExit as exc:
        assert exc.code != 0
        manifest = None
    except Exception:
        # Any other raised exception is also an acceptable "refused" outcome
        # for this security test -- the only load-bearing assertion is
        # containment, checked unconditionally below regardless of whether
        # render() raised or degraded gracefully to a safe fallback folder.
        manifest = None

    # No file anywhere on disk may exist outside out_dir's own subtree.
    escape_targets = [
        tmp_path / "etc", out_dir.parent.parent / "etc", Path("/etc/passwd"),
    ]
    for target in escape_targets:
        assert not (target.exists() and target.is_file() and "Hostile_src" in target.read_text(errors="ignore")), (
            f"a hostile category value must never let content escape to {target}"
        )

    if manifest is not None:
        for rel in manifest["written"]:
            resolved = (out_dir / rel).resolve()
            assert resolved.is_relative_to(out_dir.resolve()), (
                f"written path {resolved} escaped out_dir {out_dir.resolve()}"
            )


# ===========================================================================
# 3. Wikilink injection: LONGEST-FIRST match, first-occurrence-per-block,
#    canonical_target_form-not-unique tiebreak.
# ===========================================================================


def test_longest_canonical_target_form_matches_before_its_own_substring(tmp_path):
    canon = make_canon({
        "IvanGrozny_src": canon_entry("IvanGrozny_src", "Иван Грозный"),
        "IvanShort_src": canon_entry("IvanShort_src", "Иван"),
    })
    text = "Иван Грозный сказал слово. Позже Иван снова заговорил."
    ns = make_nodestream([make_node("p1", "seg01", text)])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "сказал слово" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    long_identity = entity_note_identity(out_dir, manifest, "IvanGrozny_src")
    short_identity = entity_note_identity(out_dir, manifest, "IvanShort_src")
    assert f"[[{long_identity}|Иван Грозный]]" in body_text, body_text
    assert f"[[{short_identity}|Иван]]" in body_text, body_text
    # The longer match must consume the shared substring -- no double-wrap
    # like "[[...[[<short_identity>|Иван]] Грозный...]]".
    assert f"[[{short_identity}|Иван]] Грозный" not in body_text


def test_first_occurrence_per_block_only_the_first_mention_is_wrapped(tmp_path):
    canon = make_canon({"Maria_src": canon_entry("Maria_src", "Мария")})
    text = "Мария сказала это. Мария снова сказала это."
    ns = make_nodestream([make_node("p1", "seg01", text)])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "сказала это" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    identity = entity_note_identity(out_dir, manifest, "Maria_src")
    assert body_text.count(f"[[{identity}|Мария]]") == 1, (
        f"only the FIRST occurrence in a block must be wrapped, got:\n{body_text}"
    )
    assert "Мария снова" in body_text, "the second, unwrapped occurrence must remain plain text"


def test_shared_canonical_target_form_tiebreak_prefers_shorter_source_form(tmp_path):
    """Documented interpretation call #1 (see module docstring): shortest
    source_form wins when two entries share one canonical_target_form."""
    canon = make_canon({
        "Ioann_src": canon_entry("Ioann_src", "Джон"),  # 9 chars
        "Yan_src": canon_entry("Yan_src", "Джон"),       # 7 chars -- shorter
    })
    ns = make_nodestream([make_node("p1", "seg01", "Джон пришёл домой.")])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "пришёл домой" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")
    identity = entity_note_identity(out_dir, manifest, "Yan_src")
    assert f"[[{identity}|Джон]]" in body_text, (
        f"expected the shorter source_form (Yan_src) to win the tiebreak, got:\n{body_text}"
    )


# ===========================================================================
# 4. name_display: never vs first_occurrence (contract §13's "name_display
#    never-vs-first_occurrence").
# ===========================================================================


def test_parenthetical_originals_never_shows_no_gloss(tmp_path):
    canon = make_canon({"Ivan_src": canon_entry("Ivan_src", "Иван")})
    # Two DIFFERENT segments (-> two separate narrative-page files), not two
    # blocks within one segment -- see the book-wide test below for why this
    # distinction matters.
    ns = make_nodestream([
        make_node("p1", "seg01", "Иван вошёл в комнату."),
        make_node("p2", "seg02", "Иван снова заговорил."),
    ])
    profile = make_profile(folders={"person": "people"}, parenthetical_originals="never")

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    paths = all_written_paths(out_dir, manifest)
    combined = "\n".join(p.read_text(encoding="utf-8") for p in paths if p.is_file())
    assert "(Ivan_src)" not in combined


def test_parenthetical_originals_first_occurrence_is_book_wide_not_per_block_or_per_segment(tmp_path):
    """RATIFIED semantics (RECONCILE_lt_seams.md): `name_display.
    parenthetical_originals: first_occurrence` tracks the first occurrence
    ACROSS THE WHOLE BOOK -- a distinct scope from the wikilink rule's own
    per-BLOCK first-occurrence reset (see
    test_first_occurrence_per_block_only_the_first_mention_is_wrapped
    above). Using two nodes in the SAME segment would only prove "not
    per-block" (still ambiguous with "per-segment" tracking) -- putting the
    second mention in a genuinely DIFFERENT segment (seg01 vs seg02, two
    separate narrative-page files) is what actually isolates "book-wide"
    from "per-segment"."""
    canon = make_canon({"Ivan_src": canon_entry("Ivan_src", "Иван")})
    ns = make_nodestream([
        make_node("p1", "seg01", "Иван вошёл в комнату."),
        make_node("p2", "seg02", "Иван снова заговорил."),
    ])
    profile = make_profile(folders={"person": "people"}, parenthetical_originals="first_occurrence")

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_paths = [
        p for p in all_written_paths(out_dir, manifest)
        if p.is_file() and ("вошёл" in p.read_text(encoding="utf-8") or "снова заговорил" in p.read_text(encoding="utf-8"))
    ]
    assert len(body_paths) == 2, "sanity: seg01 and seg02 must render as two SEPARATE narrative pages"
    combined = "\n".join(p.read_text(encoding="utf-8") for p in body_paths)
    assert combined.count("(Ivan_src)") == 1, (
        f"the original-script gloss must appear exactly once ACROSS THE "
        f"WHOLE BOOK (book-wide first occurrence, spanning a segment "
        f"boundary), not once per segment, got:\n{combined}"
    )
    seg01_page = next(p for p in body_paths if "вошёл" in p.read_text(encoding="utf-8"))
    seg02_page = next(p for p in body_paths if "снова заговорил" in p.read_text(encoding="utf-8"))
    assert "(Ivan_src)" in seg01_page.read_text(encoding="utf-8"), (
        "the gloss belongs on the FIRST (book-order) occurrence, seg01"
    )
    assert "(Ivan_src)" not in seg02_page.read_text(encoding="utf-8"), (
        "seg02's own (later, book-order) occurrence must NOT repeat the gloss"
    )


# ===========================================================================
# 5. RTL frontmatter (loose/flexible per documented interpretation call #3).
# ===========================================================================


def test_rtl_target_language_note_carries_some_rtl_marker(tmp_path):
    canon = make_canon({"Ivan_src": canon_entry("Ivan_src", "איוואן")})
    ns = make_nodestream([make_node("p1", "seg01", "טקסט לדוגמה")], target="he")
    profile = make_profile(folders={"person": "people"}, target_lang="he")

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    matches = find_file_with_content(all_written_paths(out_dir, manifest), lambda t: "Ivan_src" in t)
    assert len(matches) == 1
    assert "rtl" in matches[0].read_text(encoding="utf-8").lower(), (
        "an RTL target language's entity note must carry SOME rtl marker "
        "(exact field name is an open contract ambiguity -- see module docstring)"
    )


def test_non_rtl_target_language_note_carries_no_rtl_marker(tmp_path):
    canon = make_canon({"Ivan_src": canon_entry("Ivan_src", "Иван")})
    ns = make_nodestream([make_node("p1", "seg01", "Обычный текст")], target="ru")
    profile = make_profile(folders={"person": "people"}, target_lang="ru")

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    matches = find_file_with_content(all_written_paths(out_dir, manifest), lambda t: "Ivan_src" in t)
    assert len(matches) == 1
    assert "rtl" not in matches[0].read_text(encoding="utf-8").lower()


# ===========================================================================
# 6. Regression tests for codex review-round-1 fixes (FIXSPEC_lt_review1.md,
#    "C / lt-obsidian" section) -- written to the spec's exact repros so
#    they align with the fix in parallel; some may be red until the fix
#    lands (see the run report to the lead).
# ===========================================================================


def test_degenerate_whitespace_canonical_target_form_is_not_matched(tmp_path):
    """A canon entry whose canonical_target_form is empty or whitespace-only
    must never become a matcher -- otherwise it would wrap the first space
    (or nothing at all) in every block (FIXSPEC C1)."""
    canon = make_canon({
        "Degenerate_src": canon_entry("Degenerate_src", " "),  # a single space
        "Ivan_src": canon_entry("Ivan_src", "Иван"),
    })
    text = "Иван вошёл в комнату и сел."
    ns = make_nodestream([make_node("p1", "seg01", text)])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "вошёл" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    assert "| ]]" not in body_text and "|]]" not in body_text, (
        f"a whitespace-only canonical_target_form must never become a "
        f"matcher -- got:\n{body_text}"
    )
    # The real entity ("Иван") must still be wikilinked normally -- the
    # degenerate entry must be skipped, not poison the whole matcher.
    identity = entity_note_identity(out_dir, manifest, "Ivan_src")
    assert f"[[{identity}|Иван]]" in body_text, body_text
    # The sentence's own spacing must be untouched (no space anywhere got
    # wrapped into a spurious wikilink).
    assert " и сел." in body_text


def test_syntax_aware_linker_does_not_nest_inside_an_already_emitted_wikilink(tmp_path):
    """Repro from FIXSPEC C2: an inline-embedded verse's OWN content is
    linked first (inside _render_verse_inline), producing a real
    "[[Alice_src|Alice]]" wikilink spliced into the block's text; the
    OUTER, whole-block linker.link() call that runs afterward must not
    re-scan and re-wrap the "Alice" that is now sitting inside that
    already-emitted wikilink's own display portion."""
    v_ph = "⟦VERSE_vInline_00000001⟧"
    canon = make_canon({"Alice_src": canon_entry("Alice_src", "Alice")})
    node = make_node(
        "p1", "seg01",
        f"Before verse {v_ph}, after verse.",
        verses=[{"vid": "vInline", "placeholder": v_ph, "content": {"rendered": "Alice said hello"}}],
    )
    ns = make_nodestream([node])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "said hello" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    identity = entity_note_identity(out_dir, manifest, "Alice_src")
    assert body_text.count(f"[[{identity}|Alice]]") == 1, (
        f"expected exactly ONE wikilink for Alice, not a re-wrapped/nested "
        f"one -- got:\n{body_text}"
    )
    assert f"[[{identity}|[[" not in body_text, (
        f"the already-emitted wikilink's own display portion must never be "
        f"re-wrapped (nested link corruption) -- got:\n{body_text}"
    )


def test_syntax_aware_linker_does_not_corrupt_a_footnote_reference(tmp_path):
    """Repro from FIXSPEC C2: a canon entry whose canonical_target_form is
    literally "1" must not get wrapped INSIDE the "[^1]" footnote-reference
    syntax the fnref substitution just produced (which would corrupt it
    into "[^[[One_src|1]]]")."""
    canon = make_canon({"One_src": canon_entry("One_src", "1")})
    node = make_node(
        "p1", "seg01", "See note ⟦FNREF_1⟧ for details.", fnrefs=[1],
    )
    ns = make_nodestream([node], footnotes=[{"n": 1, "text": "A definition."}])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "for details" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    identity = entity_note_identity(out_dir, manifest, "One_src")
    assert "[^1]" in body_text, f"the footnote reference must survive intact -- got:\n{body_text}"
    assert f"[[{identity}|1]]" not in body_text, (
        f"the digit inside [^1] must never be wikilinked -- protected-span "
        f"violation, got:\n{body_text}"
    )


def test_syntax_aware_linker_does_not_wrap_inside_a_raw_sentinel_token(tmp_path):
    """A canon target that happens to be a substring of a raw, still-
    unresolved ⟦...⟧ sentinel token (an edge case outside the normal
    contract, used here purely to exercise the protected-span mechanism)
    must not get wrapped -- the sentinel token stays untouched, verbatim."""
    stray_sentinel = "⟦VERSE_stray_00000000⟧"
    canon = make_canon({"VerseWord_src": canon_entry("VerseWord_src", "VERSE")})
    node = make_node("p1", "seg01", f"Look at this: {stray_sentinel} for reference.", verses=[])
    ns = make_nodestream([node])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "for reference" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    identity = entity_note_identity(out_dir, manifest, "VerseWord_src")
    assert stray_sentinel in body_text, (
        f"the raw sentinel token must survive completely untouched -- got:\n{body_text}"
    )
    assert f"[[{identity}|VERSE]]" not in body_text, (
        f"a canon target inside a raw sentinel token must not be wikilinked -- got:\n{body_text}"
    )


# ===========================================================================
# 6b. #105 render close -- a footnote cited from INSIDE a verse's own
#     translated content (not the surrounding prose) must render as an
#     Obsidian [^N] ref + the page's own [^N]: def, never a raw ⟦FNREF_N⟧
#     leak. assemble.py's _scan_verse_content_fnrefs merges such a footnote
#     into the verse node's own fnrefs -- these tests exercise the render
#     side of that contract with a hand-built NodeStream, for both mounts.
# ===========================================================================


def test_verse_block_embedded_footnote_ref_renders_as_ref_and_def(tmp_path):
    """mount=block: a dedicated verse block (kind="verse") whose own
    content.rendered carries ⟦FNREF_1⟧. The block-verse branch of
    _render_block returns early via _render_verse_block, which never reaches
    the node's own prose fnref-substitution loop -- so the conversion must
    happen inside the verse renderer itself (_verse_texts ->
    _convert_verse_fnrefs), not rely on that loop."""
    v_ph = "⟦VERSE_vA_00000001⟧"
    node = make_node(
        "vblockA", "seg01", v_ph, kind="verse", fnrefs=[1],
        verses=[{
            "vid": "vA", "placeholder": v_ph,
            "content": {"rendered": "First line here.\nSecond line ⟦FNREF_1⟧ done.",
                        "literal_gloss": "Gloss line one and two."},
        }],
    )
    ns = make_nodestream([node], footnotes=[{"n": 1, "text": "A footnote definition."}])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "First line here" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    assert "[^1]" in body_text, f"the verse-embedded footnote ref must render as [^1] -- got:\n{body_text}"
    assert "[^1]: A footnote definition." in body_text, (
        f"the footnote's own [^1]: definition line must be emitted -- got:\n{body_text}"
    )
    assert "⟦FNREF_1⟧" not in body_text, (
        f"the raw sentinel must never leak into rendered output -- got:\n{body_text}"
    )


def test_verse_inline_embedded_footnote_ref_renders_as_ref_and_def(tmp_path):
    """mount=embedded: a verse spliced inline into a prose block's own text
    via _render_verse_inline, whose own content.rendered carries
    ⟦FNREF_1⟧. Unlike the block-verse case (which returns early and never
    reaches _render_block's own prose fnref-substitution loop), the embedded
    case's carrier text DOES reach that loop after splicing -- and since it
    is a blind string-replace over the WHOLE post-splice text (regardless of
    whether _convert_verse_fnrefs already converted the sentinel), this
    control test is intentionally GREEN on BOTH pristine and fixed code
    (verified via git-stash): the mount=embedded path already worked, given
    assemble.py supplies the verse-content footnote in node.fnrefs. It pins
    that _convert_verse_fnrefs' earlier conversion is harmless/idempotent
    here, not a regression discriminator -- the block-mount test above is."""
    v_ph = "⟦VERSE_vInline_00000002⟧"
    node = make_node(
        "p1", "seg01", f"Before the couplet: {v_ph} after the couplet.",
        fnrefs=[1],
        verses=[{
            "vid": "vInline", "placeholder": v_ph,
            "content": {"rendered": "A rendered line ⟦FNREF_1⟧ here."},
        }],
    )
    ns = make_nodestream([node], footnotes=[{"n": 1, "text": "An inline footnote definition."}])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "Before the couplet" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    assert "[^1]" in body_text, f"the verse-embedded footnote ref must render as [^1] -- got:\n{body_text}"
    assert "[^1]: An inline footnote definition." in body_text, (
        f"the footnote's own [^1]: definition line must be emitted -- got:\n{body_text}"
    )
    assert "⟦FNREF_1⟧" not in body_text, (
        f"the raw sentinel must never leak into rendered output -- got:\n{body_text}"
    )
    assert v_ph not in body_text, (
        f"the embedded verse's own placeholder must be substituted, not leaked -- got:\n{body_text}"
    )


# ===========================================================================
# 7. Wikilink TARGET must be the emitted note identity (FIXSPEC round 1 C3 +
#    round 2 C2) -- never the raw source_form (which may itself be
#    path-like), and, since round 2, FOLDER-QUALIFIED (a bare stem is not
#    guaranteed unique across different category folders).
# ===========================================================================


def test_wikilink_target_is_the_sanitized_note_name_not_the_raw_source_form(tmp_path):
    hostile_source_form = "../escape_src"
    canon = make_canon({
        hostile_source_form: canon_entry(hostile_source_form, "Опасно", category="person"),
    })
    ns = make_nodestream([make_node("p1", "seg01", "Он сказал: Опасно, берегись.")])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity = entity_note_identity(out_dir, manifest, hostile_source_form)

    assert "/" in identity, f"sanity: the identity should be folder-qualified, got {identity!r}"
    stem = identity.rsplit("/", 1)[-1]
    assert ".." not in stem and "/" not in stem, (
        f"sanity: the sanitized STEM itself must be path-safe, got {stem!r}"
    )
    entity_rel = [p for p in manifest["written"] if p == f"{identity}.md"]
    assert len(entity_rel) == 1, (
        f"expected {identity}.md among written paths: {manifest['written']}"
    )

    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "берегись" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    assert f"[[{identity}|Опасно]]" in body_text, (
        f"the wikilink TARGET must be the SAME (folder-qualified) note "
        f"identity used for the filename -- got:\n{body_text}"
    )
    assert hostile_source_form not in body_text, (
        f"the raw, path-like source_form must never appear as a link target -- got:\n{body_text}"
    )
    assert "[[../" not in body_text and "[[.." not in body_text, (
        "the wikilink target must never itself be path-like"
    )


# ===========================================================================
# 8. Clean render: re-rendering into an existing out_dir must remove stale
#    content that no longer exists in the current NodeStream/canon.json
#    (FIXSPEC C4) -- deterministic rebuild is the whole point of the diff gate.
# ===========================================================================


def test_clean_render_removes_a_stale_entity_note_after_the_canon_entry_is_dropped(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)

    canon_v1 = make_canon({"Ivan_src": canon_entry("Ivan_src", "Иван", category="person")})
    ns = make_nodestream([make_node("p1", "seg01", "Обычный текст без упоминаний.")])
    profile = make_profile(folders={"person": "people"})

    manifest_v1 = render_obsidian.render(ns, canon_v1, profile, out_dir)
    stale_rel = next(
        rel for rel in manifest_v1["written"]
        if "Ivan_src" in (out_dir / rel).read_text(encoding="utf-8")
    )
    assert (out_dir / stale_rel).is_file()
    vault_marker = out_dir / render_obsidian.VAULT_MARKER_FILENAME
    assert vault_marker.is_file(), (
        "the first successful render must stamp the ownership marker -- "
        "the SECOND render (below) relies on it to clean normally"
    )

    # A pre-existing .baseline/ marker must survive a re-render untouched --
    # it lives outside render_obsidian.py's own managed vault content.
    baseline_marker = out_dir / ".baseline" / "meta.json"
    baseline_marker.parent.mkdir(parents=True, exist_ok=True)
    baseline_marker.write_text("{}", encoding="utf-8")

    canon_v2 = make_canon({})  # the entry is GONE in this run
    manifest_v2 = render_obsidian.render(ns, canon_v2, profile, out_dir)

    assert not (out_dir / stale_rel).exists(), (
        "a dropped canon entry's stale note must not survive a re-render "
        "into the same out_dir"
    )
    assert stale_rel not in manifest_v2["written"]
    assert baseline_marker.is_file(), (
        "a clean-render pass must preserve .baseline/ (and anything outside "
        "the managed vault content), never sweep it up as stale"
    )
    assert vault_marker.is_file(), (
        "the ownership marker itself (FIXSPEC_lt_review2.md C1) must "
        "survive/be restamped across a re-render, never swept up as stale"
    )
    assert not vault_marker.is_symlink(), "the restamped marker must be a real regular file"
    assert render_obsidian._is_valid_vault_marker(vault_marker), (
        "FIXSPEC_lt_review3.md C1(d) regression: a legit second render into "
        "a properly-marked vault must still leave a VALID (real, "
        "regular-file, content-verified) marker behind, not just SOME file "
        "at that path"
    )


def test_clean_render_removes_a_stale_narrative_page_after_a_segment_is_dropped(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    canon = make_canon({})
    profile = make_profile(folders={})

    ns_v1 = make_nodestream([
        make_node("p1", "seg01", "Первая глава."),
        make_node("p2", "seg02", "Вторая глава."),
    ])
    manifest_v1 = render_obsidian.render(ns_v1, canon, profile, out_dir)
    seg02_rel = next(
        rel for rel in manifest_v1["written"]
        if "Вторая глава" in (out_dir / rel).read_text(encoding="utf-8")
    )
    assert (out_dir / seg02_rel).is_file()

    ns_v2 = make_nodestream([make_node("p1", "seg01", "Первая глава.")])  # seg02 is GONE
    manifest_v2 = render_obsidian.render(ns_v2, canon, profile, out_dir)

    assert not (out_dir / seg02_rel).exists(), (
        "a segment removed from the NodeStream must not leave a stale "
        "narrative page behind after a re-render"
    )
    assert seg02_rel not in manifest_v2["written"]


# ===========================================================================
# 9. render_obsidian.py's standalone CLI: a missing/unreadable input file
#    must emit one JSON line, not bare stderr text (FIXSPEC C6).
# ===========================================================================


def test_render_cli_missing_nodestream_input_emits_one_json_line(tmp_path):
    missing_nodestream = tmp_path / "does_not_exist_nodestream.json"
    missing_canon = tmp_path / "does_not_exist_canon.json"
    out_dir = tmp_path / "out"

    proc = subprocess.run(
        [sys.executable, str(RENDER_OBSIDIAN_SRC),
         "--nodestream", str(missing_nodestream),
         "--canon", str(missing_canon),
         "--out-dir", str(out_dir)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line on stdout for a missing-input CLI "
        f"error (not bare stderr text), got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = json.loads(lines[0])
    assert payload.get("success") is False


# ===========================================================================
# 10. Round-2 regression tests (FIXSPEC_lt_review2.md, "C / lt-obsidian").
# ===========================================================================


def test_two_entities_same_bare_stem_different_folders_get_distinct_wikilink_targets(tmp_path):
    """FIXSPEC round 2 C2: "Ivan!" and "Ivan?" both sanitize to the SAME
    bare stem ("Ivan_") but live in DIFFERENT category folders -- two
    genuinely separate emitted files that never collide on disk
    (folder/stem.md is already unique per folder). The bug the fix closes
    is the WIKILINK TARGET, not the filename: a bare (non-folder-qualified)
    target would have produced the identical [[Ivan_|...]] link for both
    entities, ambiguous in Obsidian even though the two notes are distinct
    files. Confirm each body wikilink resolves to exactly ONE emitted note,
    and the two targets differ."""
    canon = make_canon({
        "Ivan!": canon_entry("Ivan!", "Иван-А", category="person"),
        "Ivan?": canon_entry("Ivan?", "Иван-Б", category="place"),
    })
    ns = make_nodestream([make_node("p1", "seg01", "Здесь Иван-А и там Иван-Б.")])
    profile = make_profile(folders={"person": "people", "place": "places"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity_a = entity_note_identity(out_dir, manifest, "Ivan!")
    identity_b = entity_note_identity(out_dir, manifest, "Ivan?")

    assert identity_a != identity_b, (
        f"two entities sharing a bare stem in different folders must still "
        f"resolve to DISTINCT wikilink targets, got identical: {identity_a!r}"
    )
    # Sanity: they really DO share the same bare stem (the exact scenario
    # this test locks) -- the folder qualification is what disambiguates.
    assert identity_a.rsplit("/", 1)[-1] == identity_b.rsplit("/", 1)[-1], (
        f"sanity: expected a shared bare stem, got {identity_a!r} vs {identity_b!r}"
    )

    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "Здесь" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    assert f"[[{identity_a}|Иван-А]]" in body_text, body_text
    assert f"[[{identity_b}|Иван-Б]]" in body_text, body_text

    # Each target must resolve to exactly one real, emitted file.
    assert (out_dir / f"{identity_a}.md").is_file()
    assert (out_dir / f"{identity_b}.md").is_file()
    assert f"{identity_a}.md" in manifest["written"]
    assert f"{identity_b}.md" in manifest["written"]


def test_symlinked_out_dir_is_refused_and_nothing_is_deleted(tmp_path):
    """FIXSPEC round 2 C1(i): out_dir itself being a symlink must be
    refused BEFORE any clean/write -- writing/cleaning through it could
    affect the link TARGET's own contents, which may not be a vault this
    adapter owns at all."""
    real_target = tmp_path / "real_target"
    real_target.mkdir()
    preexisting = real_target / "user_file.txt"
    preexisting.write_text("do not touch me", encoding="utf-8")

    out_dir_symlink = tmp_path / "out_symlink"
    out_dir_symlink.symlink_to(real_target, target_is_directory=True)

    canon = make_canon({})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_obsidian.render(ns, canon, profile, out_dir_symlink)
    assert exc_info.value.reason == "out_dir_is_symlink"

    assert preexisting.is_file() and preexisting.read_text(encoding="utf-8") == "do not touch me", (
        "nothing under the symlink's TARGET may be touched by a refused render"
    )
    assert list(real_target.iterdir()) == [preexisting], (
        "the refusal must be a pure refusal -- no partial write/clean through the symlink"
    )


def test_unmanaged_non_empty_out_dir_is_refused_and_user_file_survives(tmp_path):
    """FIXSPEC round 2 C1(ii): a non-empty out_dir with NO ownership marker
    is not a vault this adapter has ever rendered into -- refuse rather
    than blindly delete a caller's own files."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    user_file = out_dir / "my_own_notes.txt"
    user_file.write_text("unrelated content", encoding="utf-8")

    canon = make_canon({})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_obsidian.render(ns, canon, profile, out_dir)
    assert exc_info.value.reason == "out_dir_not_managed"

    assert user_file.is_file() and user_file.read_text(encoding="utf-8") == "unrelated content", (
        "a refused render must leave the caller's own pre-existing file untouched"
    )


def test_a_symlink_entry_inside_the_vault_is_unlinked_target_siblings_untouched(tmp_path):
    """FIXSPEC round 2 C1(iv): a symlink ENTRY found directly inside the
    vault (not out_dir itself -- an entry within it) must be unlink()-ed
    on the next clean-render pass, never rmtree'd/recursed into -- the
    link's TARGET, and especially the target's own SIBLINGS, must be
    completely untouched."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    canon = make_canon({})
    profile = make_profile(folders={})
    ns = make_nodestream([make_node("p1", "seg01", "text")])

    # First render establishes the ownership marker so the SECOND render
    # below takes the "clean normally" path, not the unmanaged refusal.
    render_obsidian.render(ns, canon, profile, out_dir)

    external_dir = tmp_path / "external"
    external_dir.mkdir()
    target_file = external_dir / "target.txt"
    target_file.write_text("external content", encoding="utf-8")
    sibling_file = external_dir / "sibling.txt"
    sibling_file.write_text("sibling content", encoding="utf-8")

    symlink_entry = out_dir / "sneaky_link.txt"
    symlink_entry.symlink_to(target_file)
    assert symlink_entry.is_symlink()

    render_obsidian.render(ns, canon, profile, out_dir)

    assert not symlink_entry.is_symlink() and not symlink_entry.exists(), (
        "a symlink ENTRY inside the vault must be unlink()-ed on the next "
        "clean-render pass, not left behind"
    )
    assert target_file.is_file() and target_file.read_text(encoding="utf-8") == "external content", (
        "unlinking the symlink ENTRY must never delete/recurse into its TARGET"
    )
    assert sibling_file.is_file() and sibling_file.read_text(encoding="utf-8") == "sibling content", (
        "the target's own SIBLINGS must be completely untouched"
    )


def test_render_cli_unexpected_exception_emits_one_json_line_not_a_bare_traceback(tmp_path):
    """FIXSPEC round 2 C3: an unexpected (non-RenderError) exception mid-
    render -- an unwritable path, a poisoned profile shape, etc. -- must
    still surface as one JSON line + exit 1, never a bare traceback.
    Hermetic repro: a NodeStream node missing its required "seg" key --
    render()'s own `node["seg"]` direct-key access raises an uncaught
    KeyError, a stand-in for "any unexpected exception mid-render" without
    needing a real filesystem-permission trick."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in ("render_obsidian.py", "cache_key.py", "output_resolve.py"):
        shutil.copy2(SCRIPTS_SRC_DIR / name, scripts_dir / name)
    (root / "profile.yml").write_text(yaml.safe_dump({"minimal": True}), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )

    broken_nodestream = {
        "book": {"seg_order": [], "title": None},
        "nodes": [{"id": "p1", "kind": "prose", "raw_type": "PARA", "order_index": 0,
                   "medium": "plain", "text": "hi", "fnrefs": [], "verses": []}],  # no "seg" key
        "footnotes": [],
        "meta": {"target": "ru", "verse_mode": "literal_only", "apparatus_policy": "translate_all"},
    }
    nodestream_path = tmp_path / "nodestream.json"
    canon_path = tmp_path / "canon.json"
    nodestream_path.write_text(json.dumps(broken_nodestream), encoding="utf-8")
    canon_path.write_text(
        json.dumps({"entries": {}, "review_queue": [], "generation_hashes": {}}), encoding="utf-8"
    )
    out_dir = tmp_path / "out"

    proc = subprocess.run(
        [sys.executable, str(scripts_dir / "render_obsidian.py"),
         "--nodestream", str(nodestream_path), "--canon", str(canon_path), "--out-dir", str(out_dir)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line, no bare traceback, got:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = json.loads(lines[0])
    assert payload.get("success") is False
    assert "Traceback" not in proc.stdout


# ===========================================================================
# 11. Round-3 regression tests (FIXSPEC_lt_review3.md, "C / lt-obsidian").
#     [BLOCKER] Ownership-marker symlink bypass -- the round-2 gate used a
#     plain `.is_file()` check, which FOLLOWS a symlink; since the marker
#     is a preserved dotfile, a PLANTED
#     `.literary-translator-vault.json -> /some/real/file` symlink could
#     survive clean-render and either (a) satisfy the gate so real user
#     data gets deleted, or (b) get clobbered-through by the marker WRITE
#     itself. _is_valid_vault_marker/_stamp_vault_marker close both holes.
# ===========================================================================


def test_symlink_marker_with_user_data_is_refused_and_nothing_touched(tmp_path):
    """Case (a): out_dir has non-dot user data AND a SYMLINK named exactly
    like the marker, pointing at an external regular file. The gate must
    NOT be fooled by `is_file()` following the symlink -- refuse
    out_dir_not_managed, and touch NEITHER the user file NOR the symlink's
    external target."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    user_file = out_dir / "my_notes.txt"
    user_file.write_text("user data", encoding="utf-8")

    external_target = tmp_path / "external_target.json"
    external_target.write_text('{"not": "a marker"}', encoding="utf-8")
    marker_symlink = out_dir / render_obsidian.VAULT_MARKER_FILENAME
    marker_symlink.symlink_to(external_target)

    canon = make_canon({})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_obsidian.render(ns, canon, profile, out_dir)
    assert exc_info.value.reason == "out_dir_not_managed"

    assert user_file.is_file() and user_file.read_text(encoding="utf-8") == "user data", (
        "the refused render must leave the user's own file completely untouched"
    )
    assert external_target.is_file() and external_target.read_text(encoding="utf-8") == '{"not": "a marker"}', (
        "the refused render must never write through the marker symlink into its external target"
    )


def test_empty_out_dir_with_marker_symlink_does_not_clobber_external_target(tmp_path):
    """Case (b): an otherwise-EMPTY out_dir contains only the marker
    symlink (pointing at an external file) -- clean-render sees zero
    non-dot entries, so it proceeds (nothing to refuse), but the marker
    WRITE itself must never follow that symlink through to its external
    target. render() must succeed, replacing the symlink with a real
    marker file (os.replace never follows a symlink), leaving the external
    target's own content completely unchanged."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)

    external_target = tmp_path / "external_target.txt"
    external_target.write_text("precious external content", encoding="utf-8")
    marker_symlink = out_dir / render_obsidian.VAULT_MARKER_FILENAME
    marker_symlink.symlink_to(external_target)
    assert marker_symlink.is_symlink()

    canon = make_canon({})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})

    render_obsidian.render(ns, canon, profile, out_dir)  # must NOT raise

    assert external_target.is_file() and external_target.read_text(encoding="utf-8") == "precious external content", (
        "the marker write must never follow/clobber the symlink's external target"
    )
    marker_path = out_dir / render_obsidian.VAULT_MARKER_FILENAME
    assert not marker_path.is_symlink(), "the marker must now be a REAL regular file, not a symlink"
    assert render_obsidian._is_valid_vault_marker(marker_path)


@pytest.mark.parametrize("marker_content", [
    "not json at all {{{",
    json.dumps({"managed_by": "someone_else", "target": "obsidian"}),
    json.dumps({"unrelated": "content"}),
])
def test_foreign_regular_file_named_marker_with_wrong_content_is_refused(tmp_path, marker_content):
    """Case (c): a foreign REGULAR file (never a symlink) that merely
    happens to share the marker's exact filename, with content that is
    either non-JSON garbage or valid JSON but the WRONG managed_by --
    content validation (not just "is it a real file") must refuse this too."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    user_file = out_dir / "my_notes.txt"
    user_file.write_text("user data", encoding="utf-8")
    (out_dir / render_obsidian.VAULT_MARKER_FILENAME).write_text(marker_content, encoding="utf-8")

    canon = make_canon({})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_obsidian.render(ns, canon, profile, out_dir)
    assert exc_info.value.reason == "out_dir_not_managed"
    assert user_file.is_file() and user_file.read_text(encoding="utf-8") == "user data"


# ===========================================================================
# 12. Round-3 CLI envelope tests (FIXSPEC_lt_review3.md C2): a profile/
#     dependency SystemExit reached from render_obsidian.py's own CLI
#     main() must surface as one JSON line, exit 2 -- never stderr-only.
# ===========================================================================


def _write_minimal_nodestream_and_canon(tmp_path):
    nodestream_path = tmp_path / "nodestream.json"
    canon_path = tmp_path / "canon.json"
    nodestream_path.write_text(json.dumps({
        "book": {"seg_order": [], "title": None},
        "nodes": [], "footnotes": [],
        "meta": {"target": "ru", "verse_mode": "literal_only", "apparatus_policy": "translate_all"},
    }), encoding="utf-8")
    canon_path.write_text(
        json.dumps({"entries": {}, "review_queue": [], "generation_hashes": {}}), encoding="utf-8"
    )
    return nodestream_path, canon_path


def test_render_cli_dependency_precondition_when_cache_key_exits_at_import(tmp_path):
    """cache_key.py sys.exits DURING its own `import cache_key` statement
    inside main() -- an import-time (dependency-shaped) failure, distinct
    from load_profile()'s own later failure (see the companion test below)."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(RENDER_OBSIDIAN_SRC, scripts_dir / "render_obsidian.py")
    shutil.copy2(SCRIPTS_SRC_DIR / "output_resolve.py", scripts_dir / "output_resolve.py")
    (scripts_dir / "cache_key.py").write_text(
        "import sys\n"
        "print('ERROR: poisoned cache_key.py dependency preflight', file=sys.stderr)\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )
    nodestream_path, canon_path = _write_minimal_nodestream_and_canon(tmp_path)
    out_dir = tmp_path / "out"

    proc = subprocess.run(
        [sys.executable, str(scripts_dir / "render_obsidian.py"),
         "--nodestream", str(nodestream_path), "--canon", str(canon_path), "--out-dir", str(out_dir)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line, not stderr-only, got:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = json.loads(lines[0])
    assert payload.get("success") is False
    assert payload.get("reason") == "dependency_precondition"


def test_render_cli_profile_precondition_when_load_profile_fails_on_missing_marker(tmp_path):
    """A REAL cache_key.py imports fine, but cache_key.load_profile()
    itself sys.exits because no ownership marker exists."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in ("render_obsidian.py", "cache_key.py", "output_resolve.py"):
        shutil.copy2(SCRIPTS_SRC_DIR / name, scripts_dir / name)
    # Deliberately NO profile.yml, NO .literary-translator-root.json marker.
    nodestream_path, canon_path = _write_minimal_nodestream_and_canon(tmp_path)
    out_dir = tmp_path / "out"

    proc = subprocess.run(
        [sys.executable, str(scripts_dir / "render_obsidian.py"),
         "--nodestream", str(nodestream_path), "--canon", str(canon_path), "--out-dir", str(out_dir)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line, not stderr-only, got:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = json.loads(lines[0])
    assert payload.get("success") is False
    assert payload.get("reason") == "profile_precondition"


def test_render_cli_argparse_usage_error_stays_standard_not_json():
    """An argparse usage error (an unrecognized flag) is the ONE
    intentional non-JSON exit (FIXSPEC_lt_review3.md C2) -- standard
    stderr usage text, exit 2, empty stdout. Must NOT be converted into a
    JSON envelope."""
    proc = subprocess.run(
        [sys.executable, str(RENDER_OBSIDIAN_SRC), "--not-a-real-flag"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.strip() == "", (
        f"argparse usage errors must stay stderr-only, not get a JSON envelope -- "
        f"got stdout:\n{proc.stdout!r}"
    )
    assert proc.stderr.strip(), "expected standard argparse usage text on stderr"


# ===========================================================================
# 13. Round-4 regression tests: non-UTF-8 marker content, and the
#     mkstemp+os.replace atomic-write contract (_stamp_vault_marker).
# ===========================================================================


def test_non_utf8_marker_content_is_refused_not_a_crash(tmp_path):
    """_is_valid_vault_marker's except clause is (OSError, ValueError) --
    NOT just json.JSONDecodeError -- specifically because a REAL marker
    file containing raw non-UTF-8 bytes raises UnicodeDecodeError straight
    through read_text(encoding="utf-8") (ValueError is the common parent
    of JSONDecodeError AND UnicodeDecodeError). This is a DIFFERENT code
    path from test_foreign_regular_file_named_marker_with_wrong_content_is_refused
    above, which only covers valid-UTF-8-but-wrong-JSON content -- a
    genuinely non-UTF-8 marker file must be refused as out_dir_not_managed,
    never let a bare UnicodeDecodeError escape."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    user_file = out_dir / "my_notes.txt"
    user_file.write_text("user data", encoding="utf-8")
    (out_dir / render_obsidian.VAULT_MARKER_FILENAME).write_bytes(b"\xff\xfe not valid utf-8")

    canon = make_canon({})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_obsidian.render(ns, canon, profile, out_dir)
    assert exc_info.value.reason == "out_dir_not_managed"
    assert user_file.is_file() and user_file.read_text(encoding="utf-8") == "user data", (
        "a refused render must leave the user's own file completely untouched"
    )


def test_marker_write_is_atomic_no_stray_tmp_file_left_behind(tmp_path):
    """_stamp_vault_marker writes via tempfile.mkstemp(dir=out_dir,
    prefix="lt-vault-tmp-") + os.fdopen + os.replace -- after a successful
    render into a fresh/empty out_dir, the marker must be a real, valid,
    non-symlink regular file, and NO "lt-vault-tmp-*" temp entry may
    remain (the whole point of os.replace as the LAST step)."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    canon = make_canon({})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})

    render_obsidian.render(ns, canon, profile, out_dir)

    marker_path = out_dir / render_obsidian.VAULT_MARKER_FILENAME
    assert marker_path.is_file() and not marker_path.is_symlink()
    assert render_obsidian._is_valid_vault_marker(marker_path)
    leftover_tmp = list(out_dir.glob("lt-vault-tmp-*"))
    assert leftover_tmp == [], f"expected zero stray tmp entries, found: {leftover_tmp}"


def test_stray_leftover_tmp_file_from_a_simulated_crash_is_swept_by_clean_render(tmp_path):
    """Self-healing case: a "lt-vault-tmp-*" leftover simulating a crash
    between mkstemp's write and the final os.replace on a PRIOR run --
    deliberately given a NON-dot prefix (review round 4's own stated
    rationale) so it is swept by the next render's ORDINARY clean-render
    pass (the same non-dot-entry deletion loop every other stray file
    goes through), rather than surviving forever like a dotfile would.
    Render must still succeed normally afterward."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    canon = make_canon({})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})

    # First render establishes the ownership marker so the SECOND render
    # below takes the "clean normally" path, not the unmanaged refusal.
    render_obsidian.render(ns, canon, profile, out_dir)

    stray = out_dir / "lt-vault-tmp-simulated-crash-leftover"
    stray.write_text("leftover from a simulated crash", encoding="utf-8")
    assert stray.is_file()

    render_obsidian.render(ns, canon, profile, out_dir)  # must NOT raise

    assert not stray.exists(), (
        "a stray non-dot lt-vault-tmp-* leftover must be swept by the "
        "ordinary clean-render pass, not survive across a re-render"
    )
    marker_path = out_dir / render_obsidian.VAULT_MARKER_FILENAME
    assert render_obsidian._is_valid_vault_marker(marker_path)


# ===========================================================================
# 14. Round-5 regression tests: marker validator must check the FULL
#     identity (managed_by AND target == "obsidian"), not managed_by alone
#     -- a partial or cross-adapter marker must not satisfy this adapter's
#     own ownership gate (FIXSPEC_lt_review5.md finding 2).
# ===========================================================================


def test_partial_marker_missing_target_is_refused(tmp_path):
    """A real regular-file marker with managed_by but NO target key at all
    must not satisfy the gate -- it is not the full identity this adapter
    actually stamps."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    user_file = out_dir / "my_notes.txt"
    user_file.write_text("user data", encoding="utf-8")
    (out_dir / render_obsidian.VAULT_MARKER_FILENAME).write_text(
        json.dumps({"managed_by": "literary-translator"}), encoding="utf-8"
    )

    canon = make_canon({})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_obsidian.render(ns, canon, profile, out_dir)
    assert exc_info.value.reason == "out_dir_not_managed"
    assert user_file.is_file() and user_file.read_text(encoding="utf-8") == "user data"


def test_cross_adapter_marker_with_wrong_target_is_refused(tmp_path):
    """A real regular-file marker with the correct managed_by but a
    DIFFERENT target (e.g. stamped by some other output-target adapter)
    must not satisfy the obsidian gate -- otherwise obsidian's own
    clean-render would delete a vault it does not actually own."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    user_file = out_dir / "my_notes.txt"
    user_file.write_text("user data", encoding="utf-8")
    (out_dir / render_obsidian.VAULT_MARKER_FILENAME).write_text(
        json.dumps({"managed_by": "literary-translator", "target": "docusaurus"}), encoding="utf-8"
    )

    canon = make_canon({})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})

    with pytest.raises(render_obsidian.RenderError) as exc_info:
        render_obsidian.render(ns, canon, profile, out_dir)
    assert exc_info.value.reason == "out_dir_not_managed"
    assert user_file.is_file() and user_file.read_text(encoding="utf-8") == "user data"


def test_correct_full_marker_payload_still_cleans_normally(tmp_path):
    """Regression: the CORRECT, full marker payload (managed_by AND
    target=="obsidian" -- exactly what _marker_payload()/_stamp_vault_marker
    actually stamp) must still satisfy the gate and clean+rewrite normally
    on a second render, same as any legitimately-marked vault."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    (out_dir / render_obsidian.VAULT_MARKER_FILENAME).write_text(
        json.dumps({"managed_by": "literary-translator", "target": "obsidian"}), encoding="utf-8"
    )
    stale_file = out_dir / "some_stale_note.md"
    stale_file.write_text("stale content from a prior run", encoding="utf-8")

    canon = make_canon({})
    ns = make_nodestream([make_node("p1", "seg01", "text")])
    profile = make_profile(folders={})

    manifest = render_obsidian.render(ns, canon, profile, out_dir)  # must NOT raise
    assert manifest["kind"] == "vault"

    assert not stale_file.exists(), "a legitimately-marked vault must still be cleaned normally"
    marker_path = out_dir / render_obsidian.VAULT_MARKER_FILENAME
    assert render_obsidian._is_valid_vault_marker(marker_path)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
