"""tests/extract_bodywalk_verse.test.py

Behavioral tests for two ``extract.py.template`` extraction fixes, exercised
against the REAL, shipped template (loaded exactly as
``manifest_validation.test.py`` does -- copied into a throwaway
``${durable_root}`` and imported fresh):

  #83 -- the body-file walk (``build()``'s ``process_body_node`` helper).
        A wrapper ``<div>`` around the chapter ``<h2>`` was previously missed
        by a direct-child-only ``child.name == "h2"`` match, collapsing the
        whole file to front-matter and silently dropping every paragraph in the
        wrapper. Driven end-to-end through ``build()`` on hand-built minimal
        EPUB fixtures so the real spine/OPF/body-walk path runs, not a
        reimplementation.

  #84 -- ``verse_plain`` / ``verse_payload``. A stanza whose lines are bare
        ``<p>``s (no ``.line`` class) previously produced an empty string,
        dropping the poem's words and any FNREF sentinel baked in by
        ``_prep_clone``. Tested directly on constructed ``.poetry-container``
        soup (no EPUB needed).

The self-check FATALs that lock these fixes in (``body_files_yield_segments``,
``verse_plain_text_nonempty``) live in ``manifest_validation.test.py`` alongside
the other ``run_self_checks`` FATAL tests; this file covers the extraction
behavior itself.

Collection note: like every ``*.test.py`` file in this suite, run with
``python3 -m pytest --import-mode=importlib
tests/extract_bodywalk_verse.test.py`` (configured project-wide via pytest.ini).
"""
import importlib.util
import shutil
import zipfile
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "templates" / "extract.py.template"
)
SCHEMA_PATH = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "schemas" / "manifest.schema.json"
)

assert TEMPLATE_PATH.is_file(), f"extract.py.template not found at {TEMPLATE_PATH}"


def _load_extract_module(tmp_path: Path):
    """Copies extract.py.template into a throwaway ${durable_root} and imports
    the copy fresh (its module-level DURABLE_ROOT self-anchors off wherever it
    is loaded from). build() reads its EPUB from the profile's source.path and
    never touches DURABLE_ROOT, so no manifest.json is written here."""
    durable_root = tmp_path / "durable"
    (durable_root / "schemas").mkdir(parents=True)
    extract_copy = durable_root / "extract.py"
    shutil.copyfile(TEMPLATE_PATH, extract_copy)
    if SCHEMA_PATH.is_file():
        shutil.copyfile(SCHEMA_PATH, durable_root / "schemas" / "manifest.schema.json")

    spec = importlib.util.spec_from_file_location("extract_bodywalk_under_test", extract_copy)
    assert spec is not None and spec.loader is not None, f"could not load spec for {extract_copy}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def extract_mod(tmp_path):
    return _load_extract_module(tmp_path)


# ---------------------------------------------------------------------------
# Minimal EPUB fixture: one body xhtml file, forced to klass "body" via a
# spine_override so classification is deterministic regardless of content.
# ---------------------------------------------------------------------------
_BODY_FILENAME = "body.xhtml"

_CONTAINER_XML = (
    '<?xml version="1.0"?>\n'
    '<container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    '  <rootfiles>\n'
    '    <rootfile full-path="content.opf" '
    'media-type="application/oebps-package+xml"/>\n'
    '  </rootfiles>\n'
    '</container>\n'
)

_OPF = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
    'unique-identifier="bookid">\n'
    '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
    '    <dc:title>Test Book</dc:title>\n'
    '  </metadata>\n'
    '  <manifest>\n'
    f'    <item id="body" href="{_BODY_FILENAME}" '
    'media-type="application/xhtml+xml"/>\n'
    '  </manifest>\n'
    '  <spine>\n'
    '    <itemref idref="body"/>\n'
    '  </spine>\n'
    '</package>\n'
)


def _make_epub(epub_path: Path, body_inner_html: str) -> None:
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        '<head><title>x</title></head>\n'
        f'<body>\n{body_inner_html}\n</body>\n'
        '</html>\n'
    )
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("META-INF/container.xml", _CONTAINER_XML)
        zf.writestr("content.opf", _OPF)
        zf.writestr(_BODY_FILENAME, xhtml)


def _profile_for(epub_path: Path, apparatus_policy: str = "omit_apparatus") -> dict:
    return {
        "source": {
            "format": "gutenberg_epub",
            "path": str(epub_path),
            "adapter_config": {
                "gutenberg_epub": {"spine_overrides": {_BODY_FILENAME: "body"}}
            },
        },
        "project": {"max_segment_words": 100000},
        "footnotes": {"apparatus_policy": apparatus_policy},
    }


def _build(extract_mod, tmp_path: Path, body_inner_html: str):
    epub_path = tmp_path / "book.epub"
    _make_epub(epub_path, body_inner_html)
    manifest, report, _max = extract_mod.build(_profile_for(epub_path))
    return manifest, report


# ---------------------------------------------------------------------------
# Multi-file EPUB fixture -- a body file PLUS a footnote-defs file, needed by
# the #93 Fix A/B tests (an embedded-verse FNREF whose definition lives in a
# separate notes spine file). spine_overrides forces each file's klass
# deterministically, same convention as _profile_for's single-file form.
# ---------------------------------------------------------------------------

def _make_multi_file_epub(epub_path: Path, files) -> None:
    """``files``: an ordered list of ``(filename, body_inner_html)`` pairs,
    each becoming its own spine xhtml item in that order."""
    manifest_items, spine_items, zip_entries = [], [], {}
    for i, (filename, body_inner_html) in enumerate(files):
        item_id = f"item{i}"
        manifest_items.append(
            f'    <item id="{item_id}" href="{filename}" '
            'media-type="application/xhtml+xml"/>\n'
        )
        spine_items.append(f'    <itemref idref="{item_id}"/>\n')
        zip_entries[filename] = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">\n'
            '<head><title>x</title></head>\n'
            f'<body>\n{body_inner_html}\n</body>\n'
            '</html>\n'
        )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '    <dc:title>Test Book</dc:title>\n'
        '  </metadata>\n'
        '  <manifest>\n'
        f'{"".join(manifest_items)}'
        '  </manifest>\n'
        '  <spine>\n'
        f'{"".join(spine_items)}'
        '  </spine>\n'
        '</package>\n'
    )
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("META-INF/container.xml", _CONTAINER_XML)
        zf.writestr("content.opf", opf)
        for filename, xhtml in zip_entries.items():
            zf.writestr(filename, xhtml)


def _profile_multi(epub_path: Path, spine_overrides: dict, apparatus_policy: str) -> dict:
    return {
        "source": {
            "format": "gutenberg_epub",
            "path": str(epub_path),
            "adapter_config": {"gutenberg_epub": {"spine_overrides": spine_overrides}},
        },
        "project": {"max_segment_words": 100000},
        "footnotes": {"apparatus_policy": apparatus_policy},
    }


def _body_segments(manifest):
    return [s for s in manifest["segments"] if s.get("kind") == "body"]


def _find_check(results, name):
    matches = [r for r in results if r["name"] == name]
    assert len(matches) == 1, f"expected exactly one {name!r} check, found {len(matches)}"
    return matches[0]


# ---------------------------------------------------------------------------
# #83 -- div-wrapped headings must NOT collapse the file to front-matter.
# ---------------------------------------------------------------------------

def test_div_wrapped_h2_yields_body_segments(extract_mod, tmp_path):
    """`<div class="header"><h2>...</h2></div>` (the exact collapse trigger)
    followed by a body paragraph: the file must yield a real body segment, not
    misclassify everything as front-matter."""
    body_html = (
        '<div class="header"><h2>Chapter I</h2></div>\n'
        '<p>First paragraph of the chapter.</p>'
    )
    manifest, report = _build(extract_mod, tmp_path, body_html)

    body_segs = _body_segments(manifest)
    assert len(body_segs) == 1, manifest["segments"]
    assert manifest["blocks"]["HEAD:seg01"]["plain_text"] == "Chapter I"
    # the sibling <p> after the wrapper is captured as body prose
    assert body_segs[0]["n_para"] == 1, body_segs[0]
    # nothing leaked into front-matter
    assert report["n_frontback"] == 0, manifest["frontback"]


def test_whole_chapter_div_wrapper_captures_body_paragraph(extract_mod, tmp_path):
    """A whole-chapter `<div>` wrapping BOTH the `<h2>` and its body `<p>`:
    the paragraph INSIDE the wrapper must be captured, never silently dropped
    (the core #83 regression -- flattening must not `continue` past the
    wrapper's other children)."""
    body_html = (
        '<div class="chapter">'
        '<h2>Chapter II</h2>\n'
        '<p>Body inside the wrapper.</p>'
        '</div>'
    )
    manifest, report = _build(extract_mod, tmp_path, body_html)

    body_segs = _body_segments(manifest)
    assert len(body_segs) == 1, manifest["segments"]
    assert body_segs[0]["n_para"] == 1, body_segs[0]
    para_texts = [
        b["plain_text"] for b in manifest["blocks"].values() if b["type"] == "PARA"
    ]
    assert "Body inside the wrapper." in para_texts, para_texts
    assert report["n_frontback"] == 0, manifest["frontback"]


def test_direct_child_h2_structure_unchanged(extract_mod, tmp_path):
    """INVARIANT: for a direct-child `<h2>` (no wrapper) the walk is
    byte-identical to the pre-#83 behavior -- exactly one head + one para
    block, correct text, and body_toplevel_total == body_toplevel_classified."""
    body_html = (
        '<h2>Chapter III</h2>\n'
        '<p>Direct child paragraph.</p>'
    )
    manifest, report = _build(extract_mod, tmp_path, body_html)

    body_segs = _body_segments(manifest)
    assert len(body_segs) == 1, manifest["segments"]
    seg = body_segs[0]
    assert (seg["n_para"], seg["n_verse"], seg["n_quote"]) == (1, 0, 0), seg
    assert manifest["blocks"]["HEAD:seg01"]["plain_text"] == "Chapter III"
    assert manifest["blocks"]["PARA:seg01:0001"]["plain_text"] == "Direct child paragraph."
    assert report["body_toplevel_total"] == report["body_toplevel_classified"] == 2
    assert report["n_frontback"] == 0, manifest["frontback"]


def test_genuine_frontmatter_div_without_heading_still_classified_frontback(extract_mod, tmp_path):
    """A pre-heading `<div>` that contains NO `<h2>` must still fall through to
    the front-matter branch as ONE block (flattening is gated on a descendant
    `<h2>`, so heading-less wrappers keep the old behavior)."""
    body_html = (
        '<div class="titlepage"><p>By The Author</p></div>\n'
        '<h2>Chapter IV</h2>\n'
        '<p>Chapter body.</p>'
    )
    manifest, report = _build(extract_mod, tmp_path, body_html)

    body_segs = _body_segments(manifest)
    assert len(body_segs) == 1, manifest["segments"]
    # the heading-less title div is one front-matter block
    assert report["n_frontback"] == 1, manifest["frontback"]
    assert "By The Author" in manifest["frontback"][0]["text_head"]


def test_frontmatter_toc_wrapper_with_h2_is_flattened_ratified_limitation(extract_mod, tmp_path):
    """RATIFIED LIMITATION (see follow-up issue): the #83 flatten is
    UNCONDITIONAL, so a TOC wrapper carrying an `<h2>` before the first real
    chapter IS promoted to a (spurious) chapter segment. This pins the
    deliberately-SAFE direction -- the TOC's content is PRESERVED and VISIBLE
    (translated as a chapter), NOT dropped or placeholdered. The rejected
    alternative (gating the flatten on a `translate` classification) risked
    silently losing a real illustrated chapter -- see the regression test
    below."""
    body_html = (
        '<div class="toc"><h2>Table of Contents</h2>'
        '<p>Chapter I .......... 1</p></div>\n'
        '<h2>Chapter I</h2>\n'
        '<p>Real chapter text.</p>'
    )
    manifest, report = _build(extract_mod, tmp_path, body_html)

    # the TOC wrapper is flattened -> its own body segment (content preserved &
    # visible), and the real chapter is a second segment.
    body_segs = _body_segments(manifest)
    assert len(body_segs) == 2, manifest["segments"]
    heads = {b["plain_text"] for b in manifest["blocks"].values() if b["type"] == "HEAD"}
    assert heads == {"Table of Contents", "Chapter I"}, heads

    para_texts = [b["plain_text"] for b in manifest["blocks"].values() if b["type"] == "PARA"]
    # the TOC listing is PRESERVED as prose, not dropped/placeholdered
    assert "Chapter I .......... 1" in para_texts, para_texts
    assert "Real chapter text." in para_texts, para_texts
    # nothing was emitted as a regenerate/omit FRONTBACK (the silent-loss path)
    assert report["n_frontback"] == 0, manifest["frontback"]


def test_illustrated_wrapped_chapter_is_flattened_and_prose_captured(extract_mod, tmp_path):
    """Round-4 data-loss regression: a REAL illustrated chapter wrapped in a
    `<div>` whose first block is an image -- so classify_frontback_block would
    call the whole wrapper `regenerate` (it contains an `<img>`) -- must still be
    FLATTENED and its prose captured, NOT emitted as a `regenerate` FRONTBACK
    (which assemble.py would replace with a placeholder = silent chapter loss).
    This is exactly why the round-3 flatten gate was reverted."""
    body_html = (
        '<div class="chapter">'
        '<h2>Chapter I</h2>'
        '<p><img src="plate.png" alt="frontispiece"/></p>'
        '<p>Real chapter prose.</p>'
        '</div>'
    )
    manifest, report = _build(extract_mod, tmp_path, body_html)

    body_segs = _body_segments(manifest)
    assert len(body_segs) == 1, manifest["segments"]
    assert manifest["blocks"]["HEAD:seg01"]["plain_text"] == "Chapter I"
    para_texts = [b["plain_text"] for b in manifest["blocks"].values() if b["type"] == "PARA"]
    assert "Real chapter prose." in para_texts, para_texts
    # the chapter was NOT lost to a regenerate FRONTBACK
    assert report["n_frontback"] == 0, manifest["frontback"]


def test_wrapper_bare_text_between_h2_and_p_fails_coverage(extract_mod, tmp_path):
    """#83 fail-closed: bare text directly inside a heading-bearing wrapper
    (between the `<h2>` and a `<p>`) must NOT be silently dropped by flattening
    -- it is surfaced in `unclassified` so `body_coverage_no_holes` fails."""
    body_html = '<div class="chapter"><h2>Chapter</h2>Loose body text<p>ok</p></div>'
    epub_path = tmp_path / "book.epub"
    _make_epub(epub_path, body_html)
    manifest, report, max_words = extract_mod.build(_profile_for(epub_path))

    assert report["unclassified"], report
    assert any("Loose body text" in item[1] for item in report["unclassified"]), (
        report["unclassified"]
    )

    checks = extract_mod.run_self_checks(manifest, report, max_words)
    cov = _find_check(checks["results"], "body_coverage_no_holes")
    assert cov["ok"] is False, cov["detail"]
    assert checks["all_pass"] is False


def test_wrapper_whitespace_and_comment_do_not_false_fail(extract_mod, tmp_path):
    """#83 companion: whitespace and an HTML comment between the wrapper's tags
    are structural noise -- they must NOT land in `unclassified`, and the body
    `<p>` is still captured cleanly."""
    body_html = (
        '<div class="chapter"><h2>Chapter</h2>   <!-- editorial note --><p>ok</p></div>'
    )
    epub_path = tmp_path / "book.epub"
    _make_epub(epub_path, body_html)
    manifest, report, max_words = extract_mod.build(_profile_for(epub_path))

    assert report["unclassified"] == [], report["unclassified"]
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    cov = _find_check(checks["results"], "body_coverage_no_holes")
    assert cov["ok"] is True, cov["detail"]

    body_segs = _body_segments(manifest)
    assert len(body_segs) == 1 and body_segs[0]["n_para"] == 1, body_segs


def test_top_level_bare_text_between_h2_and_p_fails_coverage(extract_mod, tmp_path):
    """#83 twin: bare text that is a DIRECT child of `<body>` (sibling of the
    top-level `<h2>`/`<p>`) must also fail closed -- the outer body loop routes
    it to `unclassified` instead of `continue`-ing past it."""
    body_html = '<h2>Chapter</h2>Loose text<p>ok</p>'
    epub_path = tmp_path / "book.epub"
    _make_epub(epub_path, body_html)
    manifest, report, max_words = extract_mod.build(_profile_for(epub_path))

    assert report["unclassified"], report
    assert any("Loose text" in item[1] for item in report["unclassified"]), (
        report["unclassified"]
    )

    checks = extract_mod.run_self_checks(manifest, report, max_words)
    cov = _find_check(checks["results"], "body_coverage_no_holes")
    assert cov["ok"] is False, cov["detail"]
    assert checks["all_pass"] is False


def test_top_level_whitespace_and_comment_do_not_false_fail(extract_mod, tmp_path):
    """#83 twin companion: top-level whitespace and an HTML comment between
    `<body>`'s element children are structural noise -- no `unclassified`
    entry, clean coverage, and the `<p>` body is still captured."""
    body_html = '<h2>Chapter</h2>\n<!-- editorial note --><p>ok</p>'
    epub_path = tmp_path / "book.epub"
    _make_epub(epub_path, body_html)
    manifest, report, max_words = extract_mod.build(_profile_for(epub_path))

    assert report["unclassified"] == [], report["unclassified"]
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    cov = _find_check(checks["results"], "body_coverage_no_holes")
    assert cov["ok"] is True, cov["detail"]

    body_segs = _body_segments(manifest)
    assert len(body_segs) == 1 and body_segs[0]["n_para"] == 1, body_segs


# ---------------------------------------------------------------------------
# #84 -- verse_plain / verse_payload recover bare-<p> stanza text + anchors.
# ---------------------------------------------------------------------------

def _container(extract_mod, html: str):
    soup = extract_mod.BeautifulSoup(html, "lxml")
    container = soup.select_one(".poetry-container")
    assert container is not None, "fixture must contain a .poetry-container"
    return container


def test_bare_p_stanza_plain_text_is_nonempty(extract_mod):
    """A `.stanza` whose lines are bare `<p>`s (no `.line`) must yield
    non-empty plain text, not the old empty string."""
    container = _container(
        extract_mod,
        '<div class="poetry-container"><div class="stanza">'
        '<p>Bare line A</p><p>Bare line B</p>'
        '</div></div>',
    )
    plain = extract_mod.verse_plain(container)
    assert plain.strip(), repr(plain)
    assert "Bare line A" in plain and "Bare line B" in plain, repr(plain)


def test_line_stanza_plain_text_unchanged(extract_mod):
    """INVARIANT: a `.stanza` with proper `.line` children is behavior-identical
    to the pre-#84 output."""
    container = _container(
        extract_mod,
        '<div class="poetry-container"><div class="stanza">'
        '<span class="line">Line one</span><span class="line">Line two</span>'
        '</div></div>',
    )
    assert extract_mod.verse_plain(container) == "Line one\nLine two"


def test_empty_boundary_line_is_byte_identical(extract_mod):
    """INVARIANT (#84 regression): a stanza with an EMPTY boundary `.line`
    followed by a second stanza must reproduce the pre-#84 `parts.extend(...)`
    output exactly -- "A\\n\\n\\nB". A bare `.strip()` on the joined stanza
    would collapse the boundary blank to "A\\n\\nB"; the explicit `if st_lines`
    branch preserves it."""
    container = _container(
        extract_mod,
        '<div class="poetry-container">'
        '<div class="stanza"><span class="line">A</span><span class="line"></span></div>'
        '<div class="stanza"><span class="line">B</span></div>'
        '</div>',
    )
    assert extract_mod.verse_plain(container) == "A\n\n\nB"


def test_bare_p_verse_recovers_footnote_anchor(extract_mod):
    """A footnote anchor living inside a bare-`<p>` verse line must be recovered
    as an FNREF via verse_payload -- with the old empty-string verse_plain the
    sentinel (and thus the fnref) was lost."""
    container = _container(
        extract_mod,
        '<div class="poetry-container"><div class="stanza">'
        '<p>A verse line <a id="FNanchor_5">5</a> here</p>'
        '</div></div>',
    )
    html, plain, n_line, n_stanza, fnrefs = extract_mod.verse_payload(container, "translate_all")
    assert fnrefs == [5], (fnrefs, repr(plain))
    assert "⟦FNREF_5⟧" in plain, repr(plain)
    assert plain.strip(), repr(plain)


# ---------------------------------------------------------------------------
# _dom_line_count -- corrected DOM verse-line counter (codex round-4 finding 1,
# refined round 5/6). n_line must count VERSE LINE UNITS (one `.line` element
# OR one bare line-level `<p>`), not get_text("\n") newline fragments -- the
# latter over-counts inline markup and under-counts a MIXED stanza.
# ---------------------------------------------------------------------------

def test_bare_p_stanza_n_line_counts_p_units(extract_mod):
    """(case a) A `.stanza` whose 3 lines are bare `<p>`s (no `.line`) must
    count 3, read from the DOM -- NOT the newline-collapsed plain_text."""
    container = _container(
        extract_mod,
        '<div class="poetry-container"><div class="stanza">'
        '<p>Line one</p><p>Line two</p><p>Line three</p>'
        '</div></div>',
    )
    _html, plain, n_line, _n_stanza, _fnrefs = extract_mod.verse_payload(container, "omit_apparatus")
    assert n_line == 3, n_line
    assert extract_mod._dom_line_count(container) == 3
    # plain_text is collapsed to ONE line by normalize_text -> proves n_line is
    # the DOM count, not a re-derivation from plain_text.
    assert len([ln for ln in plain.splitlines() if ln.strip()]) == 1, repr(plain)


def test_line_stanza_n_line_unchanged(extract_mod):
    """(case b / byte-compat INVARIANT) A `.stanza` with 2 proper `.line`
    children counts 2 -- unchanged from the pre-fix len(select('.line'))."""
    container = _container(
        extract_mod,
        '<div class="poetry-container"><div class="stanza">'
        '<span class="line">Line one</span><span class="line">Line two</span>'
        '</div></div>',
    )
    _html, _plain, n_line, _n_stanza, _fnrefs = extract_mod.verse_payload(container, "omit_apparatus")
    assert n_line == 2, n_line
    assert extract_mod._dom_line_count(container) == 2


def test_mixed_stanza_counts_line_plus_bare_p(extract_mod):
    """(case c DISCRIMINATOR) A stanza mixing 2 `.line` + 1 bare `<p>` must
    count BOTH kinds: 3, not 2 -- the pre-fix/v4-draft helper takes the `.line`
    branch and silently drops the bare `<p>`."""
    container = _container(
        extract_mod,
        '<div class="poetry-container"><div class="stanza">'
        '<span class="line">Line one</span><span class="line">Line two</span>'
        '<p>Bare third line</p>'
        '</div></div>',
    )
    assert extract_mod._dom_line_count(container) == 3


def test_inline_markup_line_counts_once(extract_mod):
    """(case d DISCRIMINATOR -- the key finding) A bare-`<p>` line carrying
    inline markup must count as ONE line, never one per inline text fragment
    (get_text("\\n").splitlines() would give 6 here, not 2)."""
    container = _container(
        extract_mod,
        '<div class="poetry-container"><div class="stanza">'
        '<p>word <i>italic</i> more</p><p>second <b>bold</b> line</p>'
        '</div></div>',
    )
    assert extract_mod._dom_line_count(container) == 2


def test_no_stanza_line_container_unchanged(extract_mod):
    """(case e / byte-compat) No `.stanza`, 2 `.line` -- unchanged."""
    container = _container(
        extract_mod,
        '<div class="poetry-container">'
        '<span class="line">Line one</span><span class="line">Line two</span>'
        '</div>',
    )
    assert extract_mod._dom_line_count(container) == 2


def test_no_stanza_bare_p_container_counts_p(extract_mod):
    """(case e bare-`<p>`) No `.stanza`, no `.line`, 3 bare `<p>` -- must count
    3 (the pre-fix helper returned 0 here)."""
    container = _container(
        extract_mod,
        '<div class="poetry-container">'
        '<p>Line one</p><p>Line two</p><p>Line three</p>'
        '</div>',
    )
    assert extract_mod._dom_line_count(container) == 3


def test_multi_stanza_bare_p_sums(extract_mod):
    """(additive sum) Two bare-`<p>` stanzas (2 + 3 lines) sum to 5."""
    container = _container(
        extract_mod,
        '<div class="poetry-container">'
        '<div class="stanza"><p>A1</p><p>A2</p></div>'
        '<div class="stanza"><p>B1</p><p>B2</p><p>B3</p></div>'
        '</div>',
    )
    assert extract_mod._dom_line_count(container) == 5


def test_line_p_wrapping_not_double_counted(extract_mod):
    """(guard invariant) A `<p>` WRAPPING `.line` children is not itself a
    line -- the `.line`s are the units. GREEN on pristine and fixed."""
    container = _container(
        extract_mod,
        '<div class="poetry-container"><div class="stanza">'
        '<p><span class="line">a</span><span class="line">b</span></p>'
        '</div></div>',
    )
    assert extract_mod._dom_line_count(container) == 2


def test_nested_line_stanza_not_double_counted(extract_mod):
    """(byte-compat DISCRIMINATOR -- codex r5 finding 3) A NESTED-stanza pure-
    `.line` shape must count 2, not 4 -- the outermost-stanza restriction plus a
    single container-level `.line` count prevents double-counting the nested
    stanza's `.line`s (a naive per-stanza sum over container.select('.stanza')
    would count the outer AND the nested stanza's `.line`s)."""
    container = _container(
        extract_mod,
        '<div class="poetry-container"><div class="stanza">'
        '<div class="stanza">'
        '<span class="line">a</span><span class="line">b</span>'
        '</div>'
        '</div></div>',
    )
    assert extract_mod._dom_line_count(container) == 2


def test_no_stanza_mixed_line_and_bare_p_counts_line_only(extract_mod):
    """(codex r6 finding-3 LOCK -- REFUTATION control) A stanza-less container
    mixing 2 `.line` + 1 bare `<p>` must count 2, NOT 3 -- the no-stanza branch
    is EITHER/OR (mirrors verse_plain's own either/or), never additive. Also
    assert the count matches what verse_plain actually surfaces (bare `<p>`
    dropped when `.line` exist) -- proving either/or is the CONSISTENT count,
    not an under-count. GREEN on pristine AND fixed; would RED if the no-stanza
    branch were made additive."""
    container = _container(
        extract_mod,
        '<div class="poetry-container">'
        '<span class="line">Line one</span><span class="line">Line two</span>'
        '<p>Bare stray line</p>'
        '</div>',
    )
    assert extract_mod._dom_line_count(container) == 2
    plain = extract_mod.verse_plain(container)
    assert len([ln for ln in plain.split("\n") if ln.strip()]) == 2, repr(plain)


def test_fallback_parent_ignores_prose_p_beside_orphan_lines(extract_mod):
    """(r6 finding-3 CONTROL -- site-B fallback) A RAW `<div>` (NOT a
    `.poetry-container` -- the bare-stanza fallback's raw-parent shape) holding
    a prose intro `<p>` beside 2 `.line` orphans must count 2 -- additive would
    give 3, miscounting the prose `<p>` as a verse line. Proves the reachable
    mixed no-stanza container is prose-safe under either/or."""
    soup = extract_mod.BeautifulSoup(
        '<div class="chapter">'
        '<p>Prose introduction.</p>'
        '<span class="line">Line one</span><span class="line">Line two</span>'
        '</div>',
        "lxml",
    )
    raw_parent = soup.select_one(".chapter")
    assert raw_parent is not None
    assert extract_mod._dom_line_count(raw_parent) == 2


# ---------------------------------------------------------------------------
# #92 -- body-top-level bare orphan verse inside a heading-bearing wrapper
# (the #83 flatten target) must normalize into real, STANDALONE
# .poetry-container(s), never swallow the <h2> nor leave a stray duplicate
# unmounted embedded registration.
# ---------------------------------------------------------------------------

def test_heading_wrapper_with_orphan_verse_is_normalized_to_standalone_and_mounted(extract_mod, tmp_path):
    """Root-cause regression (#92 finding 1a/1b): a heading-bearing wrapper
    also holding a bare orphan `.stanza`/`.line` (no `.poetry-container`) must
    NOT collapse to a single embedded placeholder swallowing the `<h2>`. The
    heading, prose, and verse must each survive as their own blocks, and the
    verse must be a real, MOUNTED standalone (mount=="block") VERSE node --
    never a duplicate, never left unmounted."""
    body_html = (
        '<div class="chapter">'
        '<h2>Chapter</h2>'
        '<p>Prose before the verse.</p>'
        '<div class="stanza"><span class="line">A line of verse</span></div>'
        '</div>'
    )
    epub_path = tmp_path / "book.epub"
    _make_epub(epub_path, body_html)
    manifest, report, max_words = extract_mod.build(_profile_for(epub_path))

    body_segs = _body_segments(manifest)
    assert len(body_segs) == 1, manifest["segments"]
    assert manifest["blocks"]["HEAD:seg01"]["plain_text"] == "Chapter"
    assert body_segs[0]["n_para"] == 1, body_segs[0]
    assert body_segs[0]["n_verse"] == 1, body_segs[0]

    verse_nodes = manifest["verse"]["store"]
    assert len(verse_nodes) == 1, verse_nodes
    assert verse_nodes[0]["mount"] == "block", verse_nodes[0]
    assert verse_nodes[0]["parent_block"], verse_nodes[0]

    assert report["unclassified"] == [], report["unclassified"]
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    assert checks["all_pass"] is True, checks["results"]


def test_heading_wrapper_interleaved_prose_and_verse_produces_two_verse_blocks(extract_mod, tmp_path):
    """`_wrap_orphan_runs_standalone` wraps each MAXIMAL CONTIGUOUS run of
    orphan verse lines separately -- prose interleaved between two verse runs
    inside the same heading wrapper must yield TWO standalone VERSE blocks
    (and the prose stays its own PARA block), not one run merged across the
    prose gap."""
    body_html = (
        '<div class="chapter">'
        '<h2>Chapter</h2>'
        '<span class="line">First stanza line</span>'
        '<p>Prose in between.</p>'
        '<span class="line">Second stanza line</span>'
        '</div>'
    )
    epub_path = tmp_path / "book.epub"
    _make_epub(epub_path, body_html)
    manifest, report, max_words = extract_mod.build(_profile_for(epub_path))

    body_segs = _body_segments(manifest)
    assert len(body_segs) == 1, manifest["segments"]
    assert body_segs[0]["n_verse"] == 2, body_segs[0]
    assert body_segs[0]["n_para"] == 1, body_segs[0]
    verse_nodes = manifest["verse"]["store"]
    assert len(verse_nodes) == 2, verse_nodes
    assert all(e["mount"] == "block" and e["parent_block"] for e in verse_nodes), verse_nodes

    checks = extract_mod.run_self_checks(manifest, report, max_words)
    assert checks["all_pass"] is True, checks["results"]


# ---------------------------------------------------------------------------
# #93 Fix A/B -- an embedded verse's own FNREF/body-ref sentinel must be
# anchored (post-mount record_anchors pass, Fix A) AND counted by the
# fnref_sentinel_unique / body_ref_markers_well_formed_and_unique self-checks
# (in-region embedded scan, Fix B) -- the carrier block's own plain_text holds
# only the ⟦VERSE_...⟧ placeholder, never the verse's own sentinel.
# ---------------------------------------------------------------------------

def test_embedded_verse_fnref_counted_translate_all(extract_mod, tmp_path):
    """A poem embedded in a QUOTE block carries a footnote anchor whose def
    lives in a separate notes spine file: the verse must be mounted
    (mount=="embedded", parent_block set), the footnote's anchor_block must be
    that carrier (the #93 Fix A post-mount pass), and BOTH fn_bijection and
    fnref_sentinel_unique must pass (the #93 Fix B in-region embedded scan)."""
    body_html = (
        '<h2>Chapter</h2>'
        '<blockquote class="blockquote"><div class="poetry-container">'
        '<div class="stanza"><p>A line with a note<a id="FNanchor_3">3</a></p></div>'
        '</div></blockquote>'
    )
    notes_html = '<div class="footnote"><p><a id="Footnote_3">3</a> A note about the poem.</p></div>'
    epub_path = tmp_path / "book.epub"
    _make_multi_file_epub(epub_path, [("body.xhtml", body_html), ("notes.xhtml", notes_html)])
    profile = _profile_multi(
        epub_path, {"body.xhtml": "body", "notes.xhtml": "footnote-defs"}, "translate_all",
    )
    manifest, report, max_words = extract_mod.build(profile)

    verse_nodes = manifest["verse"]["store"]
    assert len(verse_nodes) == 1, verse_nodes
    assert verse_nodes[0]["mount"] == "embedded", verse_nodes[0]
    assert verse_nodes[0]["parent_block"], verse_nodes[0]

    fn = next((f for f in manifest["footnotes"] if f["n"] == 3), None)
    assert fn is not None, manifest["footnotes"]
    assert fn["anchor_block"] == verse_nodes[0]["parent_block"], fn

    checks = extract_mod.run_self_checks(manifest, report, max_words)
    fn_bij = _find_check(checks["results"], "fn_bijection")
    fnref_uniq = _find_check(checks["results"], "fnref_sentinel_unique")
    assert fn_bij["ok"] is True, fn_bij["detail"]
    assert fnref_uniq["ok"] is True, fnref_uniq["detail"]
    assert checks["all_pass"] is True, checks["results"]


def test_embedded_verse_fnref_duplicate_across_blocks_is_caught(extract_mod, tmp_path):
    """Negative twin: the SAME footnote n also anchored in a paragraph must
    trip fnref_sentinel_unique's cross-block-duplicate detection -- proving
    the embedded occurrence is really counted (not silently ignored)."""
    body_html = (
        '<h2>Chapter</h2>'
        '<p>Prose also citing the note<a id="FNanchor_3">3</a>.</p>'
        '<blockquote class="blockquote"><div class="poetry-container">'
        '<div class="stanza"><p>A line with a note<a id="FNanchor_3">3</a></p></div>'
        '</div></blockquote>'
    )
    notes_html = '<div class="footnote"><p><a id="Footnote_3">3</a> A note about the poem.</p></div>'
    epub_path = tmp_path / "book.epub"
    _make_multi_file_epub(epub_path, [("body.xhtml", body_html), ("notes.xhtml", notes_html)])
    profile = _profile_multi(
        epub_path, {"body.xhtml": "body", "notes.xhtml": "footnote-defs"}, "translate_all",
    )
    manifest, report, max_words = extract_mod.build(profile)
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    fnref_uniq = _find_check(checks["results"], "fnref_sentinel_unique")
    assert fnref_uniq["ok"] is False, fnref_uniq["detail"]


def test_embedded_verse_body_ref_marker_counted_body_refs_only(extract_mod, tmp_path):
    """A literal `[3]` marker (baked from an FNanchor under body_refs_only)
    inside an embedded verse, ALSO present in a paragraph, must trip
    body_ref_markers_well_formed_and_unique's cross-block-duplicate check --
    the pre-fix gate never scanned embedded verse.store text at all."""
    body_html = (
        '<h2>Chapter</h2>'
        '<p>Prose citing the note<a id="FNanchor_3">3</a> too.</p>'
        '<blockquote class="blockquote"><div class="poetry-container">'
        '<div class="stanza"><p>A line citing the note<a id="FNanchor_3">3</a> here</p></div>'
        '</div></blockquote>'
    )
    epub_path = tmp_path / "book.epub"
    _make_epub(epub_path, body_html)
    manifest, report, max_words = extract_mod.build(
        _profile_for(epub_path, apparatus_policy="body_refs_only")
    )
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    dup_check = _find_check(checks["results"], "body_ref_markers_well_formed_and_unique")
    assert dup_check["ok"] is False, dup_check["detail"]


def test_embedded_verse_body_ref_marker_unique_passes(extract_mod, tmp_path):
    """Positive twin: unique markers (embedded verse [3], prose [4]) pass."""
    body_html = (
        '<h2>Chapter</h2>'
        '<p>Prose citing a different note<a id="FNanchor_4">4</a>.</p>'
        '<blockquote class="blockquote"><div class="poetry-container">'
        '<div class="stanza"><p>A line citing the note<a id="FNanchor_3">3</a> here</p></div>'
        '</div></blockquote>'
    )
    epub_path = tmp_path / "book.epub"
    _make_epub(epub_path, body_html)
    manifest, report, max_words = extract_mod.build(
        _profile_for(epub_path, apparatus_policy="body_refs_only")
    )
    checks = extract_mod.run_self_checks(manifest, report, max_words)
    dup_check = _find_check(checks["results"], "body_ref_markers_well_formed_and_unique")
    assert dup_check["ok"] is True, dup_check["detail"]


def test_unmounted_embedded_fnref_surfaces_as_unmounted_not_typeerror(extract_mod):
    """#93 Fix B None-guard (finding-3 methodology -- an INTERMEDIATE-MUTATION
    checkpoint, NOT RED-on-pristine: apply Fix B without the guard and this
    crashes with a TypeError from sorted({str, None}); with the guard it
    returns cleanly). An UNMOUNTED embedded verse entry (parent_block=None)
    carrying an FNREF, alongside a mounted block citing the SAME n, must
    surface as verse_placeholders_unique_and_mounted failing -- run_self_checks
    must RETURN (never raise), and fnref_sentinel_unique (which never sees the
    unmounted entry) stays green."""
    manifest = {
        "blocks": {
            "PARA:seg01:0001": {
                "id": "PARA:seg01:0001", "type": "PARA", "order_index": 1,
                "source_file": "body.xhtml",
                "plain_text": "Some body prose ⟦FNREF_3⟧.",
            },
        },
        "spine": [{"pos": 0, "file": "body.xhtml", "klass": "body"}],
        "segments": [{
            "seg": "seg01", "kind": "body",
            "block_ids": ["PARA:seg01:0001"],
            "n_para": 1, "n_verse": 0, "n_quote": 0,
            "source_files": ["body.xhtml"], "word_count": 4,
        }],
        "footnotes": [{
            "n": 3, "anchor_id": "FNanchor_3", "def_id": "Footnote_3",
            "anchor_block": "PARA:seg01:0001", "anchor_seg": "seg01", "anchor_file": "body.xhtml",
            "def_block": "FN:3", "def_file": "body.xhtml",
        }],
        "frontback": [],
        "verse": {
            "store": [{
                "vid": "V001", "placeholder": "⟦VERSE_V001_deadbeef⟧", "context": "body",
                "mount": "embedded", "parent_block": None, "source_file": "body.xhtml",
                "n_line": 1, "n_stanza": 0, "source_html": "<p>x</p>",
                "plain_text": "A line ⟦FNREF_3⟧", "sha1": "x", "fnrefs": [3],
            }],
            "n_nodes": 1, "n_block": 0, "n_embedded": 1,
            "by_context": {"body": 1, "footnote": 0, "frontback": 0},
            "total_stanza": 0, "total_line": 0,
        },
        "source_inputs": ["book.epub"],
    }
    report = {
        "n_spine": 1, "n_body_files": 1, "n_notes_files": 0, "n_frontback_files": 0,
        "n_segments": 1, "n_blocks": 1, "n_para": 1, "n_quote": 0, "n_verse_blocks": 0,
        "n_fn": 0, "n_frontback": 0, "total_words_body": 4,
        "body_toplevel_total": 1, "body_toplevel_classified": 1,
        "unclassified": [], "orphan_fn": [], "uncovered_verse_lines": [],
        "apparatus_policy": "translate_all",
    }
    checks = extract_mod.run_self_checks(manifest, report, 100000)
    assert checks["all_pass"] is False
    unmounted = _find_check(checks["results"], "verse_placeholders_unique_and_mounted")
    assert unmounted["ok"] is False, unmounted["detail"]
    fnref_check = _find_check(checks["results"], "fnref_sentinel_unique")
    assert fnref_check["ok"] is True, fnref_check["detail"]
