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
