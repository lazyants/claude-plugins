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
import unicodedata
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


def test_syntax_aware_linker_does_not_corrupt_a_preexisting_literal_wikilink_span(tmp_path):
    """Retargeted (2026-07): the scenario this test used to guard -- an
    inline-embedded verse's spliced text getting matched a SECOND time by a
    later `link()` call, nesting a wikilink inside a wikilink -- no longer
    exists. `_render_verse_inline` was made a pure formatter (#105c) and all
    entity linking now happens in exactly ONE pass over the fully composed
    block text, so a name spliced in from a verse is never matched twice to
    begin with; deleting `_PROTECTED_SPAN_RE`'s `\\[\\[.*?\\]\\]` alternative
    does not turn the old scenario red any more.

    What that alternative still guards under the current single-pass
    architecture: RAW prose/draft text that already contains a literal
    "[[...]]"-shaped span BEFORE `linker.link()` ever runs on it -- e.g.
    hand-authored markup carried over from source material, not anything
    this plugin itself emits. Without the guard, the single regex pass would
    match an entity name that happens to fall inside that pre-existing span
    too, nesting a corrupted wikilink inside it."""
    canon = make_canon({"Alice_src": canon_entry("Alice_src", "Alice")})
    text = "See [[Alice]] wave, then Alice waved again."
    ns = make_nodestream([make_node("p1", "seg01", text)])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "waved again" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    identity = entity_note_identity(out_dir, manifest, "Alice_src")
    assert "[[Alice]]" in body_text, (
        f"a pre-existing literal wikilink-shaped span in the raw text must "
        f"survive completely untouched, never re-wrapped -- got:\n{body_text}"
    )
    assert "[[[[" not in body_text, (
        f"the pre-existing span must never be nested inside a second "
        f"wikilink -- got:\n{body_text}"
    )
    assert body_text.count(f"[[{identity}|Alice]]") == 1, (
        f"the LATER, genuinely plain 'Alice' mention (outside the "
        f"pre-existing span) must still get linked normally -- got:\n{body_text}"
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


def test_syntax_aware_linker_recognizes_entity_across_nfc_and_nfd_normalization_forms(tmp_path):
    """An entity name can occur in the document in either Unicode
    normalization form -- NFC (precomposed, e.g. a single "e-acute"
    codepoint) or NFD (canonically-equivalent decomposed, e.g. plain "e"
    followed by a combining acute-accent codepoint) -- since draft/segment
    text can be spliced in from different upstream sources that don't agree
    on a form. Both must be recognized as the SAME canon target: the
    compiled matcher pattern is built from ONE literal `canonical_target_form`
    string, so an unnormalized scan would silently miss whichever occurrence
    happens to be byte-different from it, breaking both wikilink coverage
    (a missed link) and the "first true occurrence" invariant (a later,
    byte-matching occurrence would wrongly become "first")."""
    nfc_name = unicodedata.normalize("NFC", "René")
    nfd_name = unicodedata.normalize("NFD", "René")
    assert nfc_name != nfd_name, "sanity: the two forms must be byte-distinct"

    canon = make_canon({"Rene_src": canon_entry("Rene_src", nfc_name)})
    # The NFD-form occurrence comes FIRST in true document order; the
    # NFC-form (byte-identical to canonical_target_form) occurrence second.
    text = f"{nfd_name} entered the room. Later, {nfc_name} spoke again."
    ns = make_nodestream([make_node("p1", "seg01", text)])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "entered the room" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    identity = entity_note_identity(out_dir, manifest, "Rene_src")
    link_nfc = f"[[{identity}|{nfc_name}]]"
    assert body_text.count(link_nfc) == 1, (
        f"both the NFD-form and NFC-form occurrences must be recognized as "
        f"the SAME target and wikilinked exactly once (not zero, not twice) "
        f"-- got:\n{body_text}"
    )
    assert body_text.index(link_nfc) < body_text.index("Later,"), (
        f"the wikilink must land on the true FIRST (NFD-form) occurrence in "
        f"document order, not the later NFC-form one -- got:\n{body_text}"
    )
    assert nfd_name not in body_text, (
        f"the raw NFD-form bytes must not survive unlinked in the output -- "
        f"got:\n{body_text}"
    )


def test_syntax_aware_linker_normalizes_only_matchable_text_not_protected_spans(tmp_path):
    """A blanket NFC-normalize over the WHOLE incoming text (before
    protected-span detection) would silently rewrite the bytes of anything
    inside an already-protected span too -- e.g. a pre-existing, hand-
    authored `[[...]]` wikilink whose bracketed target text happens to be in
    NFD form. That corrupts it: the real file on disk was written via
    `_dedupe_path`, which deliberately preserves ORIGINAL (non-normalized)
    bytes, so a silently-NFC'd link target in the text would no longer match
    the real path on a normalization-sensitive filesystem. Protected spans
    must survive completely untouched; only the genuinely MATCHABLE
    (non-protected) text should be normalized, so an NFD-form occurrence
    there is still correctly recognized and linked."""
    nfc_name = unicodedata.normalize("NFC", "가나")
    nfd_name = unicodedata.normalize("NFD", "가나")
    assert nfc_name != nfd_name, "sanity: the two forms must be byte-distinct"

    canon = make_canon({"Gana_src": canon_entry("Gana_src", nfc_name)})
    # A pre-existing wikilink (protected span) whose bracketed target text is
    # itself the NFD-decomposed form. A separate, un-bracketed occurrence of
    # the same underlying text (also NFD-form) appears later, in matchable
    # prose -- that one must still be normalized and linked.
    text = f"See [[other/{nfd_name}]] here. Later, {nfd_name} appears again in prose."
    ns = make_nodestream([make_node("p1", "seg01", text)])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "appears again" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    identity = entity_note_identity(out_dir, manifest, "Gana_src")
    assert f"[[other/{nfd_name}]]" in body_text, (
        f"the pre-existing wikilink's bracketed target text must survive "
        f"byte-for-byte in its ORIGINAL (NFD) form -- a blanket normalize "
        f"would silently rewrite it to NFC and desync it from the real file "
        f"path on disk -- got:\n{body_text!r}"
    )
    assert f"[[other/{nfc_name}]]" not in body_text, (
        f"the pre-existing wikilink must never be silently NFC-normalized "
        f"-- got:\n{body_text!r}"
    )
    assert body_text.count(f"[[{identity}|{nfc_name}]]") == 1, (
        f"the later, genuinely-matchable NFD-form occurrence (outside the "
        f"protected span) must still be recognized as the same canon target "
        f"and wikilinked, wrapped with the canonical NFC-form target -- "
        f"got:\n{body_text!r}"
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
    case's carrier text DOES reach that loop after splicing -- and since
    _render_block resolves every placeholder AND fnref sentinel in one atomic
    re.sub pass over the composed text (node.fnrefs supplies ⟦FNREF_1⟧ -> [^1]
    regardless of whether _convert_verse_fnrefs already converted the sentinel),
    this control test is intentionally GREEN on BOTH pristine and fixed code
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
# 6c. #105c: double wikilink -- a name already linked INSIDE a verse must not
#     link again in the surrounding prose (mount=embedded), and a name
#     appearing in BOTH a verse's own rendered text and its literal gloss
#     (mount=block) must link only once. Root cause: `_Linker.link()` used
#     to create its own fresh `seen_in_block` set on every call, so the
#     verse renderers (each calling `link()` independently) and the outer
#     `_render_block` prose call never shared first-occurrence state.
# ===========================================================================


def test_name_in_inline_verse_and_surrounding_prose_links_once(tmp_path):
    """The canonical target form appears once inside an inline (mount !=
    block) verse's own rendered text, and again in the block's surrounding
    prose. Today: linked twice (the verse's own `link()` call and the outer
    prose `link()` call each get their own fresh `seen_in_block`). Fixed:
    one shared `seen_in_block` per rendered block means only the first
    (verse) occurrence is wrapped; the later prose occurrence stays plain
    text, per the existing first-occurrence-per-block rule."""
    v_ph = "⟦VERSE_vInline_00000003⟧"
    node = make_node(
        "p1", "seg01", f"Before the verse: {v_ph} Иван stood after.",
        verses=[{
            "vid": "vInline", "placeholder": v_ph,
            "content": {"rendered": "Иван sang a song."},
        }],
    )
    ns = make_nodestream([node])
    canon = make_canon({"Ivan_src": canon_entry("Ivan_src", "Иван", category="person")})
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity = entity_note_identity(out_dir, manifest, "Ivan_src")
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "Before the verse" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    link_str = f"[[{identity}|Иван]]"
    assert body_text.count(link_str) == 1, (
        f"expected exactly one wikilink for a name already linked inside "
        f"the verse -- got {body_text.count(link_str)} in:\n{body_text}"
    )
    assert "Иван stood after" in body_text, (
        f"the second (prose) occurrence must survive as plain text, not "
        f"disappear -- got:\n{body_text}"
    )


def test_name_in_verse_rendered_and_gloss_links_once(tmp_path):
    """The canonical target form appears in BOTH a dedicated verse block's
    own `rendered` text and its `literal_gloss` (mount=block). Today:
    `_render_verse_block` calls `linker.link()` separately for `rendered`
    and `gloss`, each with its own fresh `seen_in_block`, so both occurrences
    get wrapped. Fixed: one shared `seen_in_block` across both calls means
    only the `rendered` occurrence (linked first) is wrapped; the gloss's
    occurrence stays plain text."""
    v_ph = "⟦VERSE_vA_00000004⟧"
    node = make_node(
        "vblockA", "seg01", v_ph, kind="verse",
        verses=[{
            "vid": "vA", "placeholder": v_ph,
            "content": {"rendered": "Иван sang a song.",
                        "literal_gloss": "Иван sang literally too."},
        }],
    )
    ns = make_nodestream([node])
    canon = make_canon({"Ivan_src": canon_entry("Ivan_src", "Иван", category="person")})
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity = entity_note_identity(out_dir, manifest, "Ivan_src")
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "sang a song" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    link_str = f"[[{identity}|Иван]]"
    assert body_text.count(link_str) == 1, (
        f"expected exactly one wikilink for a name appearing in both a "
        f"verse's rendered text and its own gloss -- got "
        f"{body_text.count(link_str)} in:\n{body_text}"
    )
    assert "sang literally too" in body_text, (
        f"the gloss's own occurrence must survive as plain text, not "
        f"disappear -- got:\n{body_text}"
    )


def test_first_occurrence_follows_display_order_not_processing_order(tmp_path):
    """#105c follow-up: prose text containing the entity name BEFORE an
    inline verse placeholder, whose own verse content ALSO contains that
    name. The verse is *processed* before the surrounding prose (its
    placeholder gets spliced in first), but it *displays* AFTER the prose
    text that precedes the placeholder. The "first occurrence per block"
    wikilink rule (obsidian.md: "wrap only the first occurrence per block")
    must follow DISPLAY order, not processing order -- so the wikilink
    belongs on the prose occurrence, not the verse occurrence, even though
    the verse is linked first if verse content is linked independently
    before being spliced into the prose."""
    v_ph = "⟦VERSE_vInline_00000005⟧"
    node = make_node(
        "p1", "seg01", f"Иван stood before. {v_ph}",
        verses=[{
            "vid": "vInline", "placeholder": v_ph,
            "content": {"rendered": "Иван sang later."},
        }],
    )
    ns = make_nodestream([node])
    canon = make_canon({"Ivan_src": canon_entry("Ivan_src", "Иван", category="person")})
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity = entity_note_identity(out_dir, manifest, "Ivan_src")
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "stood before" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    link_str = f"[[{identity}|Иван]]"
    assert body_text.count(link_str) == 1, (
        f"expected exactly one wikilink -- got {body_text.count(link_str)} "
        f"in:\n{body_text}"
    )
    assert f"{link_str} stood before" in body_text, (
        f"expected the wikilink on the PROSE occurrence (first in DISPLAY "
        f"order), not the verse occurrence (first in processing order) -- "
        f"got:\n{body_text}"
    )
    assert "Иван sang later" in body_text, (
        f"the verse's own occurrence (already seen in the block, later in "
        f"display order) must survive as plain, unwrapped text -- "
        f"got:\n{body_text}"
    )


def test_inline_verse_lit_label_is_not_wikilinked_and_gloss_content_is(tmp_path):
    """Regression (#105c follow-up): the renderer-authored literal label
    " (lit.: " that _render_verse_inline emits before an inline verse's gloss
    must never itself be swept into a wikilink -- even when a canon entry's
    canonical_target_form is the bare word "lit". Because #105c links the WHOLE
    composed block text in one pass (true document order) and _Linker.pattern
    is an unanchored literal alternation (no word boundary), the label's own
    incidental "lit" used to match FIRST and consume the block's single first-
    occurrence slot: the LABEL text got wikilinked (e.g. "([[people/lit|lit]].: ")
    while the REAL gloss-content "lit" went unlinked (already 'seen'). The fix
    must protect the label from the linker's pass BY POSITION (no sentinel, no
    content restore), WITHOUT reverting to linking verse content independently
    (which would reintroduce the #105c double-link)."""
    v_ph = "⟦VERSE_vLit_00000006⟧"
    node = make_node(
        "p1", "seg01", f"Before verse: {v_ph} after.",
        verses=[{
            "vid": "vLit", "placeholder": v_ph,
            "content": {"rendered": "A poem line here.",
                        "literal_gloss": "lit means bed"},
        }],
    )
    ns = make_nodestream([node])
    canon = make_canon({"lit_src": canon_entry("lit_src", "lit", category="person")})
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity = entity_note_identity(out_dir, manifest, "lit_src")
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "Before verse" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    link_str = f"[[{identity}|lit]]"
    # The renderer-authored label must read back verbatim, never wrapped.
    assert " (lit.: " in body_text, (
        f"the renderer-authored '(lit.: ' label must survive verbatim, not be "
        f"swept into a wikilink -- got:\n{body_text}"
    )
    # The one wikilink belongs to the real gloss-content occurrence of "lit".
    assert f"{link_str} means bed" in body_text, (
        f"the wikilink must land on the real gloss-content 'lit', not on the "
        f"label text -- got:\n{body_text}"
    )
    assert body_text.count(link_str) == 1, (
        f"expected exactly one wikilink (on the gloss content) -- got "
        f"{body_text.count(link_str)} in:\n{body_text}"
    )
    # No protection sentinel may leak into the rendered output.
    assert "⟦" not in body_text and "⟧" not in body_text, (
        f"no protection sentinel may leak into rendered output -- got:\n{body_text}"
    )


def test_two_gloss_bearing_inline_verses_each_get_own_position_protected_label(tmp_path):
    """Regression (round 5 redesign): protection is now BY POSITION, not by a
    sentinel string, so the per-match position arithmetic in _render_block must
    handle MULTIPLE inline-verse labels in one block. Two gloss-bearing verses
    each emit their own literal " (lit.: " label at a DIFFERENT absolute offset
    in the composed text; _render_block must protect BOTH spans (neither matched
    into by the single-pass linker) while still linking the real gloss-content
    occurrences. An off-by-N in the running `cursor` would protect the wrong span
    -- wikilinking a label's incidental "lit", or leaving a real one unprotected.

    (Replaces the old placeholder-equals-sentinel collision test: with no
    sentinel string there is nothing for a free-form placeholder to coincide
    with, so that collision class structurally cannot exist anymore. This
    exercises the mechanism that replaced it -- multi-label position tracking.)"""
    v1_ph = "⟦VERSE_v1_00000001⟧"
    v2_ph = "⟦VERSE_v2_00000002⟧"
    node = make_node(
        "p1", "seg01",
        f"One: {v1_ph} and two: {v2_ph} end.",
        verses=[
            {"vid": "v1", "placeholder": v1_ph,
             "content": {"rendered": "First poem line.",
                         "literal_gloss": "lit alpha"}},
            {"vid": "v2", "placeholder": v2_ph,
             "content": {"rendered": "Second poem line.",
                         "literal_gloss": "lit beta"}},
        ],
    )
    ns = make_nodestream([node])
    canon = make_canon({"lit_src": canon_entry("lit_src", "lit", category="person")})
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity = entity_note_identity(out_dir, manifest, "lit_src")
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "One:" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    link_str = f"[[{identity}|lit]]"
    # BOTH renderer-authored labels survive verbatim at their own positions --
    # neither label's incidental "lit" swept into a wikilink.
    assert body_text.count(" (lit.: ") == 2, (
        f"both inline-verse labels must survive verbatim, position-protected -- "
        f"got {body_text.count(' (lit.: ')} in:\n{body_text}"
    )
    # Both rendered lines and both gloss contents render at their own slots.
    assert "*First poem line.*" in body_text and "*Second poem line.*" in body_text, (
        f"both verses must render at their own placeholder positions -- got:\n{body_text}"
    )
    assert "alpha)" in body_text and "beta)" in body_text, (
        f"both gloss contents must render under their own labels -- got:\n{body_text}"
    )
    # The one first-occurrence wikilink lands on real gloss content ("lit alpha"),
    # never on either label's incidental "lit"; the later "lit beta" is already
    # 'seen' in-block, so exactly one link total.
    assert f"{link_str} alpha" in body_text, (
        f"the wikilink must land on the real gloss 'lit', not a label -- got:\n{body_text}"
    )
    assert body_text.count(link_str) == 1, (
        f"exactly one first-occurrence wikilink expected -- got "
        f"{body_text.count(link_str)} in:\n{body_text}"
    )
    # No protection machinery may leak into rendered output.
    assert "⟦" not in body_text and "⟧" not in body_text, (
        f"no sentinel/placeholder may leak into rendered output -- got:\n{body_text}"
    )


def test_inline_verse_label_nested_inside_preexisting_wikilink_not_duplicated(tmp_path):
    """Regression (codex-rescue): an inline-verse " (lit.: " label span
    (tracked by _render_block as an `extra_protected` position) can end up
    NESTED INSIDE a _PROTECTED_SPAN_RE-matched span when the verse's
    placeholder sat between the brackets of a pre-existing `[[...]]` wikilink
    in the block's raw text. The two spans then OVERLAP -- the label is fully
    contained in the wikilink span. link()'s NFC-reconstruction loop assumes
    ascending, DISJOINT spans: it copied the whole wikilink span verbatim,
    then re-appended the already-copied label substring a SECOND time and
    regressed `last`, corrupting the output (codex saw the label + trailing
    gloss duplicated, with the leaked inner "lit" then re-wikilinked). The fix
    coalesces overlapping/touching spans into their union before the loop, so
    the nested label collapses into the single enclosing wikilink span and the
    whole pre-existing wikilink is preserved byte-for-byte, exactly once."""
    v_ph = "⟦VERSE_vLit_00000006⟧"
    # The verse placeholder sits BETWEEN the brackets of a pre-existing
    # wikilink -- after substitution the emitted " (lit.: " label lands nested
    # inside the `[[...]]` protected span.
    node = make_node(
        "p1", "seg01", f"[[people/Existing|{v_ph}]]",
        verses=[{
            "vid": "vLit", "placeholder": v_ph,
            "content": {"rendered": "A poem line here.",
                        "literal_gloss": "lit means bed"},
        }],
    )
    ns = make_nodestream([node])
    # A canon target "lit" makes the bug's leaked-out tail visibly re-wikilink
    # -- a sharp discriminator: in correct output nothing inside the protected
    # wikilink is ever linked.
    canon = make_canon({"lit_src": canon_entry("lit_src", "lit", category="person")})
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "people/Existing" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    expected_link = "[[people/Existing|*A poem line here.* (lit.: lit means bed)]]"
    assert body_text.count(expected_link) == 1, (
        f"the pre-existing wikilink (with the nested verse label inside it) must "
        f"survive byte-for-byte, exactly once -- got:\n{body_text}"
    )
    # The nested label must not be duplicated: exactly one " (lit.: " and one
    # copy of the trailing gloss content.
    assert body_text.count(" (lit.: ") == 1, (
        f"the nested ' (lit.: ' label must appear exactly once, not be re-copied by "
        f"the overlapping-span reconstruction bug -- got:\n{body_text}"
    )
    assert body_text.count("means bed") == 1, (
        f"the label's trailing gloss must not be duplicated -- got:\n{body_text}"
    )
    # Everything inside the protected wikilink stays verbatim: the incidental
    # "lit" must NOT be wikilinked (it only would be if the label's gloss leaked
    # out of the protected span, the exact bug -- codex saw "[[people/lit_src|lit]]").
    assert "|lit]]" not in body_text, (
        f"no content inside the protected wikilink may leak out and be re-linked -- "
        f"got:\n{body_text}"
    )
    assert "⟦" not in body_text and "⟧" not in body_text, (
        f"no protection sentinel may leak into rendered output -- got:\n{body_text}"
    )


def test_source_form_containing_literal_lit_label_text_is_unaffected(tmp_path):
    """Regression (round 4/5 redesign): position-based protection only ever
    covers a REAL inline-verse label span, so a canon `source_form` that happens
    to contain the literal label text " (lit.: " itself is completely unaffected.
    It is injected into the first-occurrence parenthetical via a link()-built
    `piece` (`piece += f" ({source_form})"`), never appears in this block's
    `label_ranges`, and is echoed verbatim. Under the old sentinel design this
    class of author-controlled string was the round-4 corruption vector (a blind
    content restore rewrote canon data it had no business touching); the round-5
    redesign removes all content matching, so there is nothing that could rewrite
    it. This proves position-scoping never spuriously acts on an identical-looking
    string from a different channel."""
    hostile_source_form = "src (lit.: name"
    # An ordinary target word ("Ivan") that appears in the prose so it gets
    # matched/linked, with first_occurrence so its source_form is appended as a
    # parenthetical gloss -- the exact channel that injects the look-alike text.
    canon = make_canon(
        {hostile_source_form: canon_entry(hostile_source_form, "Ivan", category="person")}
    )
    ns = make_nodestream([make_node("p1", "seg01", "Ivan walked into the room.")])
    profile = make_profile(folders={"person": "people"}, parenthetical_originals="first_occurrence")

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "walked into the room" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    # The source_form's parenthetical must render VERBATIM, its literal
    # " (lit.: " substring intact and unremarkable -- position protection covers
    # only genuine inline-verse labels, never a linker-injected gloss.
    assert f"({hostile_source_form})" in body_text, (
        f"the canon source_form's parenthetical gloss must survive verbatim, its "
        f"literal ' (lit.: ' substring included -- got:\n{body_text}"
    )


def test_block_prose_containing_old_sentinel_string_is_not_rewritten(tmp_path):
    """Regression (round 5): rounds 2-4 restored a fixed sentinel string by
    CONTENT MATCHING over every verbatim slice link() copied out of its input. A
    block's OWN raw prose could coincidentally contain that exact string --
    nothing to do with verses, canon, or placeholders -- and be silently
    rewritten into "(lit.: ", corrupting a translator's actual words. Codex
    reproduced it with plain prose == the sentinel and no verse involved at all.
    The round-5 redesign removes ALL content matching: protection is by POSITION
    only, so any pre-existing text is rendered verbatim (the old sentinel string
    is now just an ordinary ⟦...⟧ span, echoed untouched). Verified RED on the
    round-4 restore code via git-stash ("⟦LIT_LABEL⟧" prose rewritten to
    "(lit.: ")."""
    # The literal string that WAS `_VERSE_LIT_LABEL_SENTINEL` in rounds 2-4. It
    # is deleted from the module now; here it is just ordinary document prose.
    old_sentinel = "⟦LIT_LABEL⟧"
    node = make_node("p1", "seg01", f"The bracket macro {old_sentinel} appears here.")
    ns = make_nodestream([node])
    # Non-empty canon with a target IN the prose, so the FULL link path runs
    # (not the pattern-is-None early return) -- the round-4 restore corrupted the
    # verbatim tail slice on exactly this path.
    canon = make_canon(
        {"bracket_src": canon_entry("bracket_src", "bracket", category="person")}
    )
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity = entity_note_identity(out_dir, manifest, "bracket_src")
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "appears here" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    # Full link path actually ran (a real canon target in the prose got linked).
    assert f"[[{identity}|bracket]]" in body_text, (
        f"the canon target 'bracket' must link, proving the full (non-early-return) "
        f"link path ran -- got:\n{body_text}"
    )
    # The old sentinel string is ordinary prose: echoed verbatim, NEVER rewritten
    # into a "(lit.: " label by any content-matching restore (the round-4 bug).
    assert old_sentinel in body_text, (
        f"the prose's literal '{old_sentinel}' must survive verbatim -- got:\n{body_text}"
    )
    assert "(lit.: " not in body_text, (
        f"no '(lit.: ' label may appear -- this block has no inline-verse gloss, so "
        f"any '(lit.: ' is the old restore corrupting prose -- got:\n{body_text}"
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


def test_case_differing_source_forms_in_the_same_folder_get_casefold_distinct_targets(tmp_path):
    """#99: "IVAN" and "Ivan" sanitize to two EXACT-STRING-distinct stems
    ("People/IVAN.md" vs "People/Ivan.md") in the SAME folder, so
    `_dedupe_path`'s plain `in used_paths` check never applies its `-2`
    disambiguation suffix -- both targets are already unequal as exact
    strings. But a case-insensitive filesystem (APFS default, Windows)
    resolves both writes to ONE inode, so the second `write_text` silently
    clobbers the first. Asserting plain `!=` here would be true both before
    and after the fix and prove nothing; the real invariant is that the two
    targets must also be distinct under casefold, which only the fixed
    `_dedupe_path` (folding the membership key) guarantees."""
    canon = make_canon({
        "IVAN": canon_entry("IVAN", "Иван-А", category="person"),
        "Ivan": canon_entry("Ivan", "Иван-Б", category="person"),
    })
    ns = make_nodestream([make_node("p1", "seg01", "Здесь Иван-А и там Иван-Б.")])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity_a = entity_note_identity(out_dir, manifest, "IVAN")
    identity_b = entity_note_identity(out_dir, manifest, "Ivan")

    assert identity_a.casefold() != identity_b.casefold(), (
        f"case-differing source forms in the same folder must resolve to "
        f"casefold-distinct wikilink targets (else they collide on a "
        f"case-insensitive filesystem), got: {identity_a!r} vs {identity_b!r}"
    )


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


# ===========================================================================
# 15. #119 (Fix A): a kind:"verse" node's `verses[]` may carry 2+ entries --
#     _render_block must render EVERY entry, not silently truncate to
#     verses[0]. Framed as DEFENSE-IN-DEPTH: the real validated pipeline
#     does not feed render a 2+-entry `verses` list today (validate_draft.py
#     rejects the carrier shape earlier), but render_obsidian.py is built and
#     tested independently of assemble.py -- a hand-built NodeStream, a future
#     assemble.py change, or a different adapter could still feed it one, and
#     it must not drop content. All three tests below are RED against the
#     pre-fix verses[0]-only code and GREEN after the multi-entry loop lands.
# ===========================================================================


def test_verse_block_renders_all_entries_not_only_the_first(tmp_path):
    """A kind:"verse" node carrying TWO verse entries must render BOTH, as
    two separate blockquotes (blank line between them, matching how
    _render_segment_note joins sibling blocks) -- neither dropped. RED
    pre-fix: verses[1] is silently lost (only verses[0] rendered)."""
    v1_ph = "⟦VERSE_va1_00000110⟧"
    v2_ph = "⟦VERSE_va2_00000111⟧"
    node = make_node(
        "vblockMulti", "seg01", f"{v1_ph}\n{v2_ph}", kind="verse",
        verses=[
            {"vid": "va1", "placeholder": v1_ph,
             "content": {"rendered": "First verse line one.\nFirst verse line two."}},
            {"vid": "va2", "placeholder": v2_ph,
             "content": {"rendered": "Second verse line one.\nSecond verse line two."}},
        ],
    )
    ns = make_nodestream([node])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "First verse line one" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    # Both verse entries render, neither truncated.
    assert "> First verse line one." in body_text, body_text
    assert "> Second verse line one." in body_text, (
        f"the SECOND verse entry must not be silently dropped (#119) -- "
        f"got:\n{body_text}"
    )
    # They render as two DISTINCT blockquotes (blank line between), not one
    # merged blockquote -- the blank-line join is what makes Obsidian treat
    # them as separate quote blocks.
    assert "> First verse line two.\n\n> Second verse line one." in body_text, (
        f"the two verse entries must render as SEPARATE blockquotes "
        f"(blank line between), not merged into one -- got:\n{body_text}"
    )


def test_verse_block_multi_entry_shares_one_seen_in_block(tmp_path):
    """A name appearing in BOTH verse entries of one kind:"verse" node must
    wikilink only ONCE -- the multi-entry loop shares ONE `seen_in_block`
    across all entries (#105c: one wikilink per rendered block). RED pre-fix
    on the "second entry rendered at all" axis (verses[1] dropped, so its
    occurrence is absent); a naive fix using a fresh seen_in_block per entry
    would make count==2, which this pins to 1."""
    v1_ph = "⟦VERSE_vb1_00000112⟧"
    v2_ph = "⟦VERSE_vb2_00000113⟧"
    node = make_node(
        "vblockShared", "seg01", f"{v1_ph}\n{v2_ph}", kind="verse",
        verses=[
            {"vid": "vb1", "placeholder": v1_ph,
             "content": {"rendered": "Иван walks first."}},
            {"vid": "vb2", "placeholder": v2_ph,
             "content": {"rendered": "Иван walks again."}},
        ],
    )
    ns = make_nodestream([node])
    canon = make_canon({"Ivan_src": canon_entry("Ivan_src", "Иван", category="person")})
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    identity = entity_note_identity(out_dir, manifest, "Ivan_src")
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "walks first" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    # The second entry renders (RED pre-fix: dropped entirely).
    assert "Иван walks again" in body_text, (
        f"the second verse entry must render (#119) -- got:\n{body_text}"
    )
    link_str = f"[[{identity}|Иван]]"
    assert body_text.count(link_str) == 1, (
        f"a name in BOTH verse entries must link exactly once (shared "
        f"seen_in_block across entries) -- got {body_text.count(link_str)} "
        f"in:\n{body_text}"
    )


def test_verse_block_footnote_cited_only_in_second_entry_is_not_dropped(tmp_path):
    """A footnote cited ONLY inside the second verse entry's content must
    render its [^N] ref in the body -- with no dangling [^N]: definition. The
    node's fnrefs (built from ALL entries' footnotes) emits the [^N]: def
    line regardless; pre-fix, verses[1] is dropped so the [^N] REF never
    appears in the body -> a dangling def. RED pre-fix on the body-ref
    assertion."""
    v1_ph = "⟦VERSE_vc1_00000114⟧"
    v2_ph = "⟦VERSE_vc2_00000115⟧"
    node = make_node(
        "vblockFn", "seg01", f"{v1_ph}\n{v2_ph}", kind="verse", fnrefs=[7],
        verses=[
            {"vid": "vc1", "placeholder": v1_ph,
             "content": {"rendered": "First verse, no footnote here."}},
            {"vid": "vc2", "placeholder": v2_ph,
             "content": {"rendered": "Second verse cites ⟦FNREF_7⟧ inline."}},
        ],
    )
    ns = make_nodestream([node], footnotes=[{"n": 7, "text": "The seventh footnote."}])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "First verse, no footnote" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    # The [^7] REF must appear in the body, inside the second verse's own
    # blockquote line (RED pre-fix: that whole entry is dropped, leaving the
    # [^7]: def dangling with no in-body reference).
    assert "> Second verse cites [^7] inline." in body_text, (
        f"the footnote cited only in the SECOND verse entry must render its "
        f"[^7] ref in the body (#119 compounding symptom) -- got:\n{body_text}"
    )
    assert "[^7]: The seventh footnote." in body_text, (
        f"the footnote's own [^7]: definition line must be present -- "
        f"got:\n{body_text}"
    )
    assert "⟦FNREF_7⟧" not in body_text, (
        f"the raw sentinel must never leak into rendered output -- got:\n{body_text}"
    )


# ===========================================================================
# 16. #118 item 3 (Fix D): a verse embedded as the ENTIRE content of a prose
#     block renders as a full blockquote (matching a mount:"block" verse's
#     presentation), not the compact inline italic. Scoped narrowly: prose
#     only (NEVER heading), exactly one verse claim, and the ORIGINAL block
#     text must be nothing but that verse's placeholder. A verse genuinely
#     embedded mid-sentence, or in a heading, keeps today's compact-italic
#     rendering UNCHANGED (regression guards below).
# ===========================================================================


def test_embedded_verse_sole_content_of_prose_block_renders_as_blockquote(tmp_path):
    """A prose block whose ENTIRE text is a single verse placeholder (the
    dominant real case) renders that verse as a blockquote, with its own
    cited footnote [^n] inside it. RED pre-fix: rendered as compact inline
    italic (*...*), not a blockquote."""
    v_ph = "⟦VERSE_vSole_00000120⟧"
    node = make_node(
        "p1", "seg01", v_ph, kind="prose", fnrefs=[3],
        verses=[{
            "vid": "vSole", "placeholder": v_ph,
            "content": {"rendered": "A whole-block verse line ⟦FNREF_3⟧ here."},
        }],
    )
    ns = make_nodestream([node], footnotes=[{"n": 3, "text": "Sole-content footnote."}])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "whole-block verse line" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    # Blockquote rendering, with the footnote ref inside the quote.
    assert "> A whole-block verse line [^3] here." in body_text, (
        f"a verse that is the SOLE content of a prose block must render as a "
        f"blockquote (#118 item 3) -- got:\n{body_text}"
    )
    # NOT the old compact inline italic.
    assert "*A whole-block verse line [^3] here.*" not in body_text, (
        f"the sole-content embedded verse must NOT keep the compact inline "
        f"italic rendering -- got:\n{body_text}"
    )
    assert "[^3]: Sole-content footnote." in body_text, body_text
    assert "⟦FNREF_3⟧" not in body_text, body_text


def test_embedded_verse_with_surrounding_prose_stays_compact_italic(tmp_path):
    """Regression guard for Fix D's narrow scope: the SAME embedded verse,
    but with real prose text before AND after it in the same block, is
    genuinely mid-paragraph -- a blockquote cannot sit there -- so it MUST
    keep today's compact inline-italic rendering, UNCHANGED. Green both
    pre-fix and post-fix (proves the fix does not over-reach)."""
    v_ph = "⟦VERSE_vMid_00000121⟧"
    node = make_node(
        "p1", "seg01", f"Before it: {v_ph} and after it.", kind="prose", fnrefs=[3],
        verses=[{
            "vid": "vMid", "placeholder": v_ph,
            "content": {"rendered": "A mid-sentence verse line ⟦FNREF_3⟧ here."},
        }],
    )
    ns = make_nodestream([node], footnotes=[{"n": 3, "text": "Mid footnote."}])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "Before it:" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    assert "Before it: *A mid-sentence verse line [^3] here.* and after it." in body_text, (
        f"a verse embedded mid-sentence must keep the compact inline-italic "
        f"rendering (a blockquote cannot sit mid-paragraph) -- got:\n{body_text}"
    )
    assert "> A mid-sentence verse line" not in body_text, (
        f"a mid-sentence embedded verse must NOT be promoted to a blockquote "
        f"-- got:\n{body_text}"
    )
    assert "[^3]: Mid footnote." in body_text, body_text
    assert "⟦FNREF_3⟧" not in body_text, body_text


def test_embedded_verse_sole_content_of_heading_stays_inline_not_blockquote(tmp_path):
    """Regression guard for Fix D's `kind == "prose"`-only scope: a HEADING
    node whose entire text is a verse placeholder must keep its "## " heading
    semantics with a compact inline rendering -- NEVER become a bare
    blockquote (a "## > ..." would be nonsense). Green both pre-fix and
    post-fix (the fix explicitly excludes kind == "heading")."""
    v_ph = "⟦VERSE_vHead_00000122⟧"
    node = make_node(
        "h1", "seg01", v_ph, kind="heading",
        verses=[{
            "vid": "vHead", "placeholder": v_ph,
            "content": {"rendered": "A heading-embedded verse line."},
        }],
    )
    ns = make_nodestream([node])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "heading-embedded verse line" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    assert "## *A heading-embedded verse line.*" in body_text, (
        f"a heading whose whole text is a verse placeholder must keep its "
        f"## heading + compact-italic rendering -- got:\n{body_text}"
    )
    assert "> A heading-embedded verse line" not in body_text, (
        f"a heading-embedded verse must NEVER be promoted to a blockquote "
        f"-- got:\n{body_text}"
    )


# ===========================================================================
# 17. #171: `_segment_title`/`_heading_plain_text` -- a heading node's KNOWN
#     sentinels (its own footnote anchors, a declared verse placeholder) must
#     be resolved to plain text before feeding the frontmatter `title` and
#     the filename slug, never leaked verbatim; any OTHER bracketed span
#     (literal source prose) must survive completely untouched.
# ===========================================================================


def test_heading_fnref_anchor_is_resolved_out_of_title_and_slug(tmp_path):
    """A heading's own footnote-anchor sentinel must never leak into the
    frontmatter title or the filename slug. RED pre-fix: the raw
    ⟦FNREF_1⟧ sentinel (and its letters, "FNREF", which survive the
    filename allow-list since they're plain alnum) leaked into both."""
    node = make_node(
        "h1", "seg01", "Chapter One ⟦FNREF_1⟧", kind="heading", fnrefs=[1],
    )
    assert render_obsidian._segment_title([node], "seg01") == "Chapter One"

    ns = make_nodestream([node], footnotes=[{"n": 1, "text": "A footnote."}])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "Chapter One" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")
    fm = parse_frontmatter(body_text)
    assert fm["title"] == "Chapter One", f"got frontmatter title {fm['title']!r}"
    assert "⟦" not in fm["title"] and "FNREF" not in fm["title"] and "[^" not in fm["title"]

    rel_path = next(rel for rel in manifest["written"] if (out_dir / rel) == body_matches[0])
    assert "⟦" not in rel_path and "FNREF" not in rel_path and "[^" not in rel_path, (
        f"the filename slug must not carry the sentinel/its letters -- got: {rel_path!r}"
    )


def test_heading_verse_placeholder_title_resolves_to_flattened_verse_text(tmp_path):
    """A heading whose ENTIRE text is a declared verse placeholder (free-form,
    no "VERSE_" naming convention required -- segpack.schema.json does not
    constrain it) resolves to that verse's own flattened rendered text, with
    any footnote ref inside it ([^N], itself converted from a raw
    ⟦FNREF_N⟧ by `_verse_texts`) stripped -- a footnote marker does not
    belong in a title. RED pre-fix: the raw placeholder sentinel itself was
    the title verbatim."""
    v_ph = "⟦POEM_1⟧"
    node = make_node(
        "h1", "seg01", v_ph, kind="heading",
        verses=[{
            "vid": "poem1", "placeholder": v_ph,
            "content": {"rendered": "Line one ⟦FNREF_2⟧ line two."},
        }],
    )
    title = render_obsidian._segment_title([node], "seg01")
    assert title == "Line one line two.", f"got {title!r}"
    assert "[^2]" not in title and "⟦" not in title


def test_heading_degenerate_verse_rendering_to_own_sentinel_does_not_leak(tmp_path):
    """Codex-rescue regression: a degenerate/malformed verse whose own
    `content.rendered` renders back to the LITERAL placeholder sentinel that
    names it (a shape upstream assemble.py rejects, but render()'s own
    public entry accepts directly, per its own module docstring -- built and
    tested independently of assemble.py) makes the substitution a NO-OP net
    change. Gating the "plain heading" fast path on `text == original` would
    then return the ORIGINAL raw sentinel unstripped -- a #171-invariant
    violation. The fix gates on "was there a known sentinel to resolve"
    instead, and blanks any residual known placeholder a malformed
    replacement re-introduced -- so the title/slug must carry NO `⟦`/`⟧` at
    all here (falling back to the segment id, since the heading resolves to
    empty)."""
    v_ph = "⟦POEM_1⟧"
    node = make_node(
        "h1", "seg01", v_ph, kind="heading",
        verses=[{
            "vid": "poem1", "placeholder": v_ph,
            "content": {"rendered": v_ph},   # degenerate: renders to its own sentinel
        }],
    )
    ns = make_nodestream([node])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    assert len(manifest["written"]) == 1
    rel_path = manifest["written"][0]
    body_text = (out_dir / rel_path).read_text(encoding="utf-8")
    fm = parse_frontmatter(body_text)

    assert "⟦" not in fm["title"] and "⟧" not in fm["title"], (
        f"a degenerate verse rendering to its own sentinel must never leak a "
        f"raw ⟦…⟧ into the title -- got {fm['title']!r}"
    )
    assert "⟦" not in rel_path and "⟧" not in rel_path, (
        f"nor into the filename slug -- got {rel_path!r}"
    )


def test_heading_unmatched_bracket_span_is_preserved_verbatim(tmp_path):
    """NEGATIVE (over-reach guard): a bracketed span in a heading that is
    NEITHER a declared verse placeholder NOR a footnote anchor is ordinary
    literal source prose -- it must survive completely untouched. Expected
    to be green both pre- and post-fix (the old `_segment_title` also only
    `.strip()`ed the raw text) -- verified via git-stash, not a red/green
    discriminator; it guards the fix against stripping too much."""
    node = make_node("h1", "seg01", "A ⟦variant⟧ B", kind="heading")
    title = render_obsidian._segment_title([node], "seg01")
    assert title == "A ⟦variant⟧ B", (
        f"a bracketed span with no matching sentinel must be preserved "
        f"verbatim, not stripped -- got {title!r}"
    )


def test_plain_heading_with_internal_double_space_is_unchanged(tmp_path):
    """REGRESSION (over-reach guard): a plain heading with no sentinels at
    all must come back byte-identical modulo `.strip()` -- no internal
    whitespace collapse. Expected green both pre- and post-fix (verified via
    git-stash); it guards the fix's "nothing resolved -> exact prior
    behavior" fast path."""
    node = make_node("h1", "seg01", "Chapter  Two", kind="heading")
    title = render_obsidian._segment_title([node], "seg01")
    assert title == "Chapter  Two", (
        f"a plain heading's internal whitespace must never be collapsed -- got {title!r}"
    )


# ===========================================================================
# 18. #172: multi-line footnote-definition continuations and verse-gloss
#     text must not eject their tail out of the enclosing markdown construct
#     ([^n]: def / the blockquote's own "> *Literal: …*" line) -- every
#     continuation line indented (footnote defs, CommonMark convention) or
#     flattened to one line (the gloss, which can't itself be multi-line
#     inside a single "> " blockquote line). Both non-LF line endings (CRLF,
#     lone CR) must normalize to LF first, with no bare "\r" ever surviving.
# ===========================================================================


def test_multiline_footnote_definition_gets_indented_continuation(tmp_path):
    """#172(a): a footnote definition's own text may itself be multi-line --
    each continuation line must be indented 4 spaces (CommonMark footnote
    continuation), never ejected out of the `[^n]:` def at column 0. Covers
    both a plain two-line def and a blank-line-containing one. RED pre-fix:
    `fn_lines` built each def as a single un-indented line, so a
    continuation line landed at column 0, outside the footnote def."""
    node = make_node(
        "p1", "seg01", "See ⟦FNREF_1⟧ and ⟦FNREF_2⟧ here.", fnrefs=[1, 2],
    )
    ns = make_nodestream([node], footnotes=[
        {"n": 1, "text": "line1\nline2"},
        {"n": 2, "text": "a\n\nb"},
    ])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "See [^1]" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    assert "[^1]: line1\n    line2" in body_text, (
        f"a multi-line footnote def's continuation must be indented, not "
        f"ejected at column 0 -- got:\n{body_text}"
    )
    assert "\nline2" not in body_text, (
        f"no unindented continuation line may survive at column 0 -- got:\n{body_text}"
    )
    assert "[^2]: a\n    \n    b" in body_text, (
        f"a blank-line-containing footnote def must indent EVERY "
        f"continuation line, including the blank one -- got:\n{body_text}"
    )


def test_footnote_definition_crlf_and_lone_cr_are_normalized_and_indented(tmp_path):
    """#172(a) CRLF/lone-CR: a footnote's own text may carry non-LF line
    endings (CRLF from a Windows-authored source, or a lone CR) -- both must
    normalize to LF before the continuation-indent transform, and no bare
    "\\r" may survive in the rendered output. Read back via
    `read_bytes().decode()`, NOT `read_text()`'s default universal-newline
    translation, which would silently turn any stray "\\r" the code left
    behind into "\\n" on READ, making the "\\r" assertion below vacuous
    (`read_text(newline=...)` is Python 3.13+ only; this plugin's floor is
    3.10, per tests/python_floor_pep604_drift.test.py)."""
    node = make_node(
        "p1", "seg01", "See ⟦FNREF_1⟧ and ⟦FNREF_2⟧ here.", fnrefs=[1, 2],
    )
    ns = make_nodestream([node], footnotes=[
        {"n": 1, "text": "a\r\nb"},
        {"n": 2, "text": "a\rb"},
    ])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "See [^1]" in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_bytes().decode("utf-8")

    assert "\r" not in body_text, f"no bare CR may survive -- got:\n{body_text!r}"
    assert "[^1]: a\n    b" in body_text, (
        f"a CRLF footnote def must normalize+indent its continuation -- got:\n{body_text}"
    )
    assert "[^2]: a\n    b" in body_text, (
        f"a lone-CR footnote def must normalize+indent its continuation -- got:\n{body_text}"
    )


def test_verse_block_multiline_gloss_flattened_to_single_line(tmp_path):
    """#172(b): a dedicated verse block's own `literal_gloss` may itself be
    multi-line -- unlike the rendered verse body (each line its own `> `-
    prefixed blockquote line, by design), the `> *Literal: …*` line must stay
    a SINGLE line: a multi-line gloss must not eject its tail out of the
    blockquote. RED pre-fix: the gloss was appended as one unsplit f-string,
    so an embedded "\\n" broke the line out of the "> " prefix entirely."""
    v_ph = "⟦VERSE_vGloss_00000200⟧"
    node = make_node(
        "vblockGloss", "seg01", v_ph, kind="verse",
        verses=[{
            "vid": "vGloss", "placeholder": v_ph,
            "content": {"rendered": "Line one.\nLine two.",
                        "literal_gloss": "Gloss line one.\nGloss line two."},
        }],
    )
    ns = make_nodestream([node])
    canon = make_canon({})
    profile = make_profile()

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    body_matches = find_file_with_content(
        all_written_paths(out_dir, manifest), lambda t: "Line one." in t
    )
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    assert "> Line one." in body_text and "> Line two." in body_text, body_text
    assert "> *Literal: Gloss line one. Gloss line two.*" in body_text, (
        f"a multi-line literal_gloss must be flattened to a SINGLE "
        f"'> *Literal: …*' line -- got:\n{body_text}"
    )
    for line in body_text.splitlines():
        if "*" in line:
            assert line.startswith(">"), (
                f"no dangling '*' may appear outside a '> '-prefixed line -- "
                f"got line {line!r} in:\n{body_text}"
            )


def test_verse_block_gloss_crlf_and_lone_cr_flattened_no_bare_cr(tmp_path):
    """#172(b) CRLF/lone-CR: the same flattening must normalize non-LF line
    endings in the gloss before joining with a space, and no bare "\\r" may
    survive. Read back via `read_bytes().decode()`, NOT `read_text()`'s
    default universal-newline translation, which would silently turn any
    stray "\\r" the code left behind into "\\n" on READ, making the "\\r"
    assertion below vacuous (`read_text(newline=...)` is Python 3.13+ only;
    this plugin's floor is 3.10, per tests/python_floor_pep604_drift.test.py)."""
    for content_gloss, label in [("x\r\ny", "crlf"), ("x\ry", "lone-cr")]:
        v_ph = f"⟦VERSE_vGlossCR_{label}⟧"
        node = make_node(
            f"vblockGlossCR_{label}", "seg01", v_ph, kind="verse",
            verses=[{
                "vid": f"vGlossCR_{label}", "placeholder": v_ph,
                "content": {"rendered": "A line.", "literal_gloss": content_gloss},
            }],
        )
        ns = make_nodestream([node])
        canon = make_canon({})
        profile = make_profile()

        out_dir, manifest = render_into(tmp_path / label, ns, canon, profile)
        body_matches = find_file_with_content(
            all_written_paths(out_dir, manifest), lambda t: "A line." in t
        )
        assert len(body_matches) == 1
        body_text = body_matches[0].read_bytes().decode("utf-8")

        assert "\r" not in body_text, f"no bare CR may survive ({label}) -- got:\n{body_text!r}"
        assert "> *Literal: x y*" in body_text, (
            f"the gloss must flatten to a single space-joined line ({label}) -- got:\n{body_text}"
        )


# ===========================================================================
# 9. #138 sense_translated: the entity note is emitted and `basis` round-
#    trips like any other basis, but the body-link matcher deliberately
#    excludes it (D14) -- a sense-rendering is an ordinary word by
#    construction ("Hope", "Wolf"), so the unanchored, no-word-boundary
#    matcher would otherwise wikilink every incidental occurrence of that
#    word in the prose, not just the entity's own mentions.
# ===========================================================================


def test_sense_translated_entity_note_is_emitted_and_basis_round_trips(tmp_path):
    """TP-12 (CHARACTERIZATION -- cannot go red: #138 only narrows the
    body-link matcher in build_entity_index; _render_entity_note never
    branches on basis, so entity-note emission for basis:'sense_translated'
    is unchanged behavior, asserted here for completeness)."""
    canon = make_canon({
        "Nadezhda_src": canon_entry(
            "Nadezhda_src", "Hope", category="person", is_proper_name=True,
            basis="sense_translated", note="a sense-translated speaking name",
        ),
    })
    ns = make_nodestream([make_node("p1", "seg01", "Some unrelated prose.")])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    paths = all_written_paths(out_dir, manifest)
    matches = [
        p for p in find_file_with_content(paths, lambda t: "Nadezhda_src" in t)
        if parse_frontmatter(p.read_text(encoding="utf-8")).get("source_form") == "Nadezhda_src"
    ]
    assert len(matches) == 1, f"expected exactly one entity note, found {len(matches)}"
    fm = parse_frontmatter(matches[0].read_text(encoding="utf-8"))
    assert fm["basis"] == "sense_translated"
    assert fm["canonical_target_form"] == "Hope"
    assert fm["note"] == "a sense-translated speaking name"


def test_sense_translated_target_is_not_body_linked_capitalized_or_lowercase(tmp_path):
    """TP-13 (RED pre-fix -- D14). Over-correction check done FIRST (per the
    build contract): test_realia_entry_not_a_name_still_gets_a_note above
    already proves a basis:'not_a_name'/is_proper_name:false entry STAYS
    body-matched -- so this skip must be scoped to
    basis == 'sense_translated' only, never to is_proper_name or any other
    basis. Confirmed: build_entity_index's new guard checks
    `entry.get("basis") == "sense_translated"` specifically.

    RED pre-fix -> GREEN post-fix pivot: before the `continue` guard landed
    in build_entity_index, 'Hope' -- an ordinary word by construction --
    was an unqualified matcher target like any other canon entry, so its one
    case-sensitive-matching (capitalized, sentence-initial) occurrence WAS
    wikilinked. After the guard, the target is never added to the matcher at
    all, so neither the capitalized nor the lowercase occurrence links."""
    canon = make_canon({
        "Nadezhda_src": canon_entry(
            "Nadezhda_src", "Hope", category="person", is_proper_name=True,
            basis="sense_translated", note="a sense-translated speaking name",
        ),
    })
    text = "Hope walked in. Later she lost all hope entirely."
    ns = make_nodestream([make_node("p1", "seg01", text)])
    profile = make_profile(folders={"person": "people"})

    out_dir, manifest = render_into(tmp_path, ns, canon, profile)
    paths = all_written_paths(out_dir, manifest)
    body_matches = find_file_with_content(paths, lambda t: "walked in" in t)
    assert len(body_matches) == 1
    body_text = body_matches[0].read_text(encoding="utf-8")

    assert "[[" not in body_text, (
        f"a sense_translated target must never be body-wikilinked, "
        f"capitalized or lowercase -- got:\n{body_text}"
    )
    assert "Hope walked in" in body_text, body_text
    assert "lost all hope entirely" in body_text, body_text

    # The entity note itself is unaffected by the body-link suppression --
    # still emitted, its basis intact (same invariant as TP-12, re-asserted
    # here on the exact fixture this test renders).
    note_matches = [
        p for p in find_file_with_content(paths, lambda t: "Nadezhda_src" in t)
        if parse_frontmatter(p.read_text(encoding="utf-8")).get("source_form") == "Nadezhda_src"
    ]
    assert len(note_matches) == 1
    assert parse_frontmatter(note_matches[0].read_text(encoding="utf-8"))["basis"] == "sense_translated"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
