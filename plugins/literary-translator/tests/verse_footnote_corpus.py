"""tests/verse_footnote_corpus.py -- shared verse x footnote corpus (#106).

NOT a `*.test.py` file -- pytest's `--import-mode=importlib` collection glob
only picks up `*.test.py`, so this ordinary `.py` module is never collected
as a test file itself. Each per-layer test file
(`verse_footnote_corpus_extract.test.py`, `_segpack.test.py`,
`_validate.test.py`, `_assemble.test.py`, `_render.test.py`) self-loads it via
``importlib.util.spec_from_file_location`` -- the same self-anchoring
convention every other test module in this suite uses for the real,
shipped scripts under ``skills/literary-translator/assets/scripts/``.

## Why this corpus exists

#106 needs ONE canonical set of minimal Gutenberg-EPUB fixtures that drives
the REAL extract -> segpack -> validate_draft -> assemble -> render pipeline
for every combination of verse mount (block/embedded) x footnote citation
site (body prose / inside a verse / inside a footnote definition) -- see the
corpus-cases table in the build plan. Centralizing the fixtures + the
extract->segpack->draft bridge here means every per-layer test asserts
against the SAME real intermediate data, not five independently-guessed
copies that could silently drift from each other.

## The seven cases (verse x footnote cross-product)

  (a) prose block + body footnote ref, no verse
  (b) prose/quote block with an EMBEDDED verse (mount=embedded), footnote
      ref in the SURROUNDING PROSE (not inside the verse)
  (c) footnote DEFINITION text that embeds a verse (mount=embedded,
      context="footnote") -- exercises segpack's
      ``mount=="embedded" and parent in footnote_def_block_ids`` pickup
  (d) standalone verse block (mount=block), no footnote
  (e) standalone verse block (mount=block) whose OWN verse text carries a
      footnote ref (r4 #93 end-to-end fix)
  (f) footnote ref cited INSIDE an embedded verse (mount=embedded) -- the
      verse's own translated content carries the ⟦FNREF_N⟧, not the
      surrounding prose (segpack point-1 discovery)
  (g) outer verse cites a footnote whose DEFINITION embeds a SECOND verse
      (r6 finding 2) -- the inner verse must be marked referenced (not
      orphaned) yet is stripped-not-rendered from the footnote text

## The build() -> segpack bridge (finding 8) -- synthetic hashes, not two_phase_write()

``build()`` returns a manifest with no ``generation_hashes`` at all;
``segpack.build_pack()`` hard-requires
``manifest.generation_hashes.{source_extraction_hash,source_input_hash}``
and ``canon.generation_hashes.{particle_config_hash,derivation_bundle_hash}``.
The real bridge for that is ``two_phase_write()``, which pins the REAL hash
derivation and shells out to ``cache_key.py`` under a materialized
``${durable_root}/scripts/`` -- none of which this corpus needs or wants to
couple itself to (a hash-derivation regression is a wholly separate concern
from a verse/footnote-carry-through regression). Instead this module injects
fixed, opaque dummy hashes directly onto the manifest/canon dicts in memory
(``segpack.py`` copies them verbatim; ``validate_segpack()`` only requires
non-empty strings) -- see ``CORPUS_MANIFEST_HASHES`` / ``CORPUS_CANON`` and
``manifest_for()`` / ``segpack_for()`` below.

## The segpack -> draft bridge -- built programmatically, not hardcoded

A verse's ``placeholder`` string bakes in an 8-hex ``sha1(plain_text)[:8]``
suffix computed BY THE REAL EXTRACTOR -- a literal, hand-typed draft fixture
would have to hardcode that hash, silently drifting the instant a case's
fixture HTML (or extract.py.template's own normalize_text/sha1 plumbing)
changes elsewhere. ``draft_for()`` instead builds the translated draft
PROGRAMMATICALLY from the REAL ``segpack`` (placeholders, footnote numbers,
verse vids) and the REAL ``manifest`` (which verse.store entries carry their
own ⟦FNREF_n⟧, needed for cases e/f/g) -- so it is correct for every case
above with no per-case special-casing, and never goes stale against a hash
it does not itself control. Each verse's own rendered/literal_gloss text
embeds its OWN vid (``f"Rendered {vid} line one..."``), so a render-layer
test can assert one verse's content is present while a DIFFERENT verse's
content (e.g. case (g)'s stripped-not-rendered inner verse) is absent,
without the two cases' generic boilerplate text colliding.

## Baselined against the real, POST-#92/#93/#96 tree

Every shape asserted by the five per-layer test files was verified by
actually running the real extract -> segpack -> validate_draft -> assemble
-> render chain against these exact fixtures (never guessed/derived from
reading the source alone) -- per the #106 sequencing gate, which requires
#92/#93/#96 to have landed first (they have, in this worktree).
"""
from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path
from typing import NamedTuple

# NOTE: this module is NEVER `import`-ed normally -- every caller loads it via
# `importlib.util.spec_from_file_location` (see the module docstring), which
# does not register it under `sys.modules[__name__]`. A `@dataclasses.dataclass`
# definition needs exactly that entry to resolve its own annotations (Python
# 3.14 `dataclasses._is_type` does `sys.modules.get(cls.__module__).__dict__`)
# and fatals with `AttributeError: 'NoneType' object has no attribute
# '__dict__'` otherwise -- `typing.NamedTuple` has no such requirement, so
# `CorpusCase` below is a NamedTuple, not a dataclass.

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
TEMPLATE_PATH = ASSETS_DIR / "templates" / "extract.py.template"
SCHEMA_PATH = ASSETS_DIR / "schemas" / "manifest.schema.json"
LANGUAGES_DIR = ASSETS_DIR / "languages"

assert TEMPLATE_PATH.is_file(), f"extract.py.template not found at {TEMPLATE_PATH}"
assert SCRIPTS_DIR.is_dir(), f"scripts dir not found at {SCRIPTS_DIR}"
assert (LANGUAGES_DIR / "fr.json").is_file(), f"fr.json not found under {LANGUAGES_DIR}"


# ---------------------------------------------------------------------------
# Generic module loader (mirrors every other test file's own importlib
# self-anchoring convention, e.g. extract_bodywalk_verse.test.py /
# segpack_verse_mount.test.py / render_obsidian.test.py).
# ---------------------------------------------------------------------------


def _load_module(name: str, path: Path, extra_sys_path: Path | None = None):
    if extra_sys_path is not None:
        sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if extra_sys_path is not None:
            sys.path.remove(str(extra_sys_path))


# ---------------------------------------------------------------------------
# EPUB fixture construction -- mirrors extract_bodywalk_verse.test.py's own
# `_make_multi_file_epub`/`_profile_multi` helpers exactly (spine_overrides
# forces each file's klass deterministically). Copied, not imported --
# extract_bodywalk_verse.test.py is a `*.test.py` file pytest owns, never a
# module another file should import from.
# ---------------------------------------------------------------------------

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


class CorpusCase(NamedTuple):
    """One #106 corpus case. ``files``/``spine_overrides``/``apparatus_policy``
    feed `build_epub()`/`profile_for()`; ``description`` documents which
    verse-mount x footnote-site combination the case exercises (the
    "expected-shape descriptor" is the case's real, baselined pipeline
    output itself -- each per-layer test asserts against that directly,
    rather than a second, redundant hand-maintained shape dict that could
    itself drift from the real one)."""

    label: str
    description: str
    files: tuple
    spine_overrides: dict
    apparatus_policy: str


CASES: dict[str, CorpusCase] = {
    "a": CorpusCase(
        label="a",
        description="prose block + body footnote ref, no verse",
        files=(
            ("body.xhtml", '<h2>Chapter</h2><p>Some prose with a note<a id="FNanchor_1">1</a> attached.</p>'),
            ("notes.xhtml", '<div class="footnote"><p><a id="Footnote_1">1</a> A note about the prose.</p></div>'),
        ),
        spine_overrides={"body.xhtml": "body", "notes.xhtml": "footnote-defs"},
        apparatus_policy="translate_all",
    ),
    "b": CorpusCase(
        label="b",
        description=(
            "prose/quote block with an EMBEDDED verse (mount=embedded), "
            "footnote ref in the SURROUNDING PROSE (not inside the verse)"
        ),
        files=(
            (
                "body.xhtml",
                '<h2>Chapter</h2><p>Some prose with a note<a id="FNanchor_1">1</a> introducing a poem.</p>'
                '<blockquote class="blockquote"><div class="poetry-container"><div class="stanza">'
                '<span class="line">Embedded line one</span><span class="line">Embedded line two</span>'
                '</div></div></blockquote>',
            ),
            ("notes.xhtml", '<div class="footnote"><p><a id="Footnote_1">1</a> A note about the prose.</p></div>'),
        ),
        spine_overrides={"body.xhtml": "body", "notes.xhtml": "footnote-defs"},
        apparatus_policy="translate_all",
    ),
    "c": CorpusCase(
        label="c",
        description=(
            "footnote DEFINITION text that embeds a verse (mount=embedded, "
            "context=footnote) -- segpack's footnote_def_block_ids pickup"
        ),
        files=(
            ("body.xhtml", '<h2>Chapter</h2><p>Some prose with a note<a id="FNanchor_1">1</a> attached.</p>'),
            (
                "notes.xhtml",
                '<div class="footnote"><p><a id="Footnote_1">1</a> A note quoting a poem:</p>'
                '<div class="poetry-container"><div class="stanza">'
                '<span class="line">Quoted line one</span><span class="line">Quoted line two</span>'
                '</div></div></div>',
            ),
        ),
        spine_overrides={"body.xhtml": "body", "notes.xhtml": "footnote-defs"},
        apparatus_policy="translate_all",
    ),
    "d": CorpusCase(
        label="d",
        description="standalone verse block (mount=block), no footnote",
        files=(
            (
                "body.xhtml",
                '<h2>Chapter</h2><div class="poetry-container"><div class="stanza">'
                '<span class="line">Standalone line one</span><span class="line">Standalone line two</span>'
                '</div></div>',
            ),
        ),
        spine_overrides={"body.xhtml": "body"},
        apparatus_policy="translate_all",
    ),
    "e": CorpusCase(
        label="e",
        description=(
            "standalone verse block (mount=block) whose OWN verse text "
            "carries a footnote ref (r4 #93 end-to-end fix)"
        ),
        files=(
            (
                "body.xhtml",
                '<h2>Chapter</h2><div class="poetry-container"><div class="stanza">'
                '<span class="line">A line with a note<a id="FNanchor_1">1</a></span>'
                '<span class="line">Second line</span>'
                '</div></div>',
            ),
            ("notes.xhtml", '<div class="footnote"><p><a id="Footnote_1">1</a> A note about the poem.</p></div>'),
        ),
        spine_overrides={"body.xhtml": "body", "notes.xhtml": "footnote-defs"},
        apparatus_policy="translate_all",
    ),
    "f": CorpusCase(
        label="f",
        description=(
            "footnote ref cited INSIDE an embedded verse (mount=embedded) -- "
            "the verse's own content carries the FNREF, not the surrounding "
            "prose (segpack point-1 discovery)"
        ),
        files=(
            (
                "body.xhtml",
                '<h2>Chapter</h2><p>Some prose introducing a poem.</p>'
                '<blockquote class="blockquote"><div class="poetry-container"><div class="stanza">'
                '<span class="line">Embedded line with a note<a id="FNanchor_1">1</a></span>'
                '<span class="line">Second embedded line</span>'
                '</div></div></blockquote>',
            ),
            ("notes.xhtml", '<div class="footnote"><p><a id="Footnote_1">1</a> A note about the poem.</p></div>'),
        ),
        spine_overrides={"body.xhtml": "body", "notes.xhtml": "footnote-defs"},
        apparatus_policy="translate_all",
    ),
    "g": CorpusCase(
        label="g",
        description=(
            "outer verse cites a footnote whose DEFINITION embeds a SECOND "
            "verse (r6 finding 2) -- the inner verse must be referenced "
            "(not orphaned) yet is stripped-not-rendered"
        ),
        files=(
            (
                "body.xhtml",
                '<h2>Chapter</h2><div class="poetry-container"><div class="stanza">'
                '<span class="line">Outer line with a note<a id="FNanchor_1">1</a></span>'
                '<span class="line">Outer second line</span>'
                '</div></div>',
            ),
            (
                "notes.xhtml",
                '<div class="footnote"><p><a id="Footnote_1">1</a> A note quoting another poem:</p>'
                '<div class="poetry-container"><div class="stanza">'
                '<span class="line">Inner quoted line one</span><span class="line">Inner quoted line two</span>'
                '</div></div></div>',
            ),
        ),
        spine_overrides={"body.xhtml": "body", "notes.xhtml": "footnote-defs"},
        apparatus_policy="translate_all",
    ),
}


def build_epub(case: CorpusCase, tmp_path: Path) -> Path:
    epub_path = tmp_path / f"corpus_{case.label}.epub"
    _make_multi_file_epub(epub_path, case.files)
    return epub_path


def profile_for(case: CorpusCase, epub_path: Path) -> dict:
    return {
        "source": {
            "format": "gutenberg_epub",
            "path": str(epub_path),
            "adapter_config": {"gutenberg_epub": {"spine_overrides": dict(case.spine_overrides)}},
        },
        "project": {"max_segment_words": 100000},
        "footnotes": {"apparatus_policy": case.apparatus_policy},
    }


# ---------------------------------------------------------------------------
# Layer 1 -- extract. Mirrors extract_bodywalk_verse.test.py's own
# `_load_extract_module`: copies the template into a throwaway
# ${durable_root} and imports the copy fresh (its module-level DURABLE_ROOT
# self-anchors off wherever it is loaded from).
# ---------------------------------------------------------------------------


def load_extract_module(tmp_path: Path):
    durable_root = tmp_path / "durable"
    (durable_root / "schemas").mkdir(parents=True)
    extract_copy = durable_root / "extract.py"
    import shutil

    shutil.copyfile(TEMPLATE_PATH, extract_copy)
    if SCHEMA_PATH.is_file():
        shutil.copyfile(SCHEMA_PATH, durable_root / "schemas" / "manifest.schema.json")
    return _load_module("verse_footnote_corpus_extract_under_test", extract_copy)


# The two synthetic-hash constants (finding 8 fix) -- see the module
# docstring's "build() -> segpack bridge" section. segpack.py copies these
# verbatim; validate_segpack() only requires non-empty strings, so opaque
# dummies are contract-satisfying without pinning real hash derivation.
CORPUS_MANIFEST_HASHES = {
    "source_extraction_hash": "corpus-fixed-extraction-hash",
    "source_input_hash": "corpus-fixed-input-hash",
}
CORPUS_CANON = {
    "entries": {},
    "generation_hashes": {
        "particle_config_hash": "corpus-fixed-particle-hash",
        "derivation_bundle_hash": "corpus-fixed-bundle-hash",
    },
}


def manifest_for(case: CorpusCase, extract_mod, tmp_path: Path):
    """Runs the REAL extractor on `case`'s minimal EPUB, then bridges the
    finding-8 hash gap in memory. Returns (manifest, report, max_segment_words)."""
    epub_path = build_epub(case, tmp_path)
    profile = profile_for(case, epub_path)
    manifest, report, max_words = extract_mod.build(profile)
    manifest["generation_hashes"] = dict(CORPUS_MANIFEST_HASHES)
    return manifest, report, max_words


# ---------------------------------------------------------------------------
# Layer 2 -- segpack. Loaded ONCE at corpus-helper import time (segpack.py
# has no per-test DURABLE_ROOT coupling -- its only import-time side effect
# is the `bootstrap_names` sibling sys.path dance, harmless to share across
# every case/test in one process), mirroring segpack_verse_mount.test.py's
# own module-level `SEGPACK_MODULE`/`LANG_CONFIG` constants.
# ---------------------------------------------------------------------------

SEGPACK_MODULE = _load_module(
    "verse_footnote_corpus_segpack", SCRIPTS_DIR / "segpack.py", SCRIPTS_DIR
)
LANG_CONFIG = SEGPACK_MODULE.load_language_config("fr.json", LANGUAGES_DIR)


def segpack_for(seg_id: str, manifest: dict, apparatus_policy: str, lang_config):
    return SEGPACK_MODULE.build_pack(seg_id, manifest, CORPUS_CANON, lang_config, apparatus_policy)


# ---------------------------------------------------------------------------
# Layer 2.5 -- the segpack -> draft bridge. See the module docstring's
# "segpack -> draft bridge" section for why this is programmatic, not a
# hand-typed literal dict.
# ---------------------------------------------------------------------------


def draft_for(manifest: dict, segpack: dict) -> dict:
    """Builds a translated draft for `segpack` (whose `seg` matches some
    segment of `manifest`) that satisfies validate_draft.py's checks 1-6
    for EVERY corpus case uniformly:

      - a mount=="block" verse's OWN carrier block is translated as EXACTLY
        its placeholder (check 3's per-block bijection);
      - every other block/footnote keeps a "[TR] " prefix over the real
        source text, trivially preserving the source's sentinel multiset
        (check 2/4);
      - every verse gets a distinct 2-line rendered + literal_gloss pair
        under verse_policy.mode=full_rhymed_plus_literal (check 5), each
        embedding its OWN vid so a render-layer assertion can tell one
        verse's content apart from another's;
      - a verse whose manifest.verse.store entry carries fnrefs (cases
        e/f/g) gets that ⟦FNREF_n⟧ baked into its OWN rendered/literal_gloss
        content, exercising assemble's `_scan_verse_content_fnrefs`.
    """
    verse_store_by_vid = {v["vid"]: v for v in manifest["verse"]["store"]}
    verses_by_parent_block = {
        v["parent_block"]: v for v in segpack["verses"] if v["mount"] == "block"
    }

    draft_blocks = {}
    for b in segpack["blocks"]:
        if b["id"] in verses_by_parent_block:
            draft_blocks[b["id"]] = verses_by_parent_block[b["id"]]["placeholder"]
        else:
            draft_blocks[b["id"]] = f"[TR] {b.get('plain_text') or ''}"

    draft_footnotes = {str(fo["n"]): f"[TR] {fo['source_text']}" for fo in segpack["footnotes"]}

    draft_verses = {}
    for v in segpack["verses"]:
        vid = v["vid"]
        src = verse_store_by_vid.get(vid, {})
        fnref_suffix = "".join(f"⟦FNREF_{n}⟧" for n in (src.get("fnrefs") or []))
        draft_verses[vid] = {
            "rendered": f"Rendered {vid} line one{fnref_suffix}\nRendered {vid} line two",
            "literal_gloss": f"A literal gloss for {vid} line one{fnref_suffix} and line two, worded differently",
        }

    return {
        "seg": segpack["seg"],
        "blocks": draft_blocks,
        "footnotes": draft_footnotes,
        "verses": draft_verses,
        "names": [],
        "notes": [],
    }


# ---------------------------------------------------------------------------
# Layer 3 -- validate_draft.py. Mirrors validate_draft.test.py's own
# `make_durable_root`/`write_segment`/`run_validate` subprocess harness
# exactly (validate_draft.py is invoked exactly as production does:
# `python3 {durable_root}/scripts/validate_draft.py SEG`).
# ---------------------------------------------------------------------------

DEFAULT_VALIDATE_PROFILE = {
    "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}


def make_validate_root(tmp_path: Path, label: str, profile: dict | None = None) -> Path:
    import json
    import shutil

    import yaml

    root = tmp_path / f"vroot_{label}"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / "validate_draft.py", scripts_dir / "validate_draft.py")
    (root / "segments").mkdir()

    profile_path = root / "profile.yml"
    profile_path.write_text(
        yaml.safe_dump(profile if profile is not None else DEFAULT_VALIDATE_PROFILE, sort_keys=False),
        encoding="utf-8",
    )
    marker = {"owner_profile_path": str(profile_path)}
    (root / ".literary-translator-root.json").write_text(json.dumps(marker), encoding="utf-8")
    return root


def write_validate_segment(root: Path, seg_id: str, segpack: dict, draft: dict) -> None:
    import json

    segments_dir = root / "segments"
    (segments_dir / f"segpack_{seg_id}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )
    (segments_dir / f"{seg_id}.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )


def run_validate_draft(root: Path, seg_id: str):
    import subprocess

    return subprocess.run(
        [sys.executable, str(root / "scripts" / "validate_draft.py"), seg_id],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Layer 4 -- assemble.py. `build_nodestream(profile, manifest, converged)` is
# PURE with respect to the filesystem beyond reading `segments/segpack_{seg}
# .json` / `segments/{seg}.draft.json` via its own self-anchored
# draft_path()/segpack_path() -- so a fresh copy of assemble.py (+ its two
# sibling imports) under a throwaway durable_root, with just those two files
# written, is enough to call it in-process. No ledger.json/canon.json/
# dispatch_adapter needed here (render is exercised independently, in-process,
# by the render layer below, per render_obsidian.py's own module docstring:
# "D's tests are expected to import render()/its helpers directly ... rather
# than shell out").
# ---------------------------------------------------------------------------


def make_assemble_root(tmp_path: Path, label: str) -> Path:
    import shutil

    root = tmp_path / f"aroot_{label}"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name in ("assemble.py", "output_resolve.py", "render_obsidian.py", "validate_draft.py"):
        shutil.copy2(SCRIPTS_DIR / name, scripts_dir / name)
    (root / "segments").mkdir()
    return root


def write_assemble_segment(root: Path, seg_id: str, segpack: dict, draft: dict) -> None:
    import json

    (root / "segments" / f"segpack_{seg_id}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )
    (root / "segments" / f"{seg_id}.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )


def load_assemble_module(root: Path, label: str):
    """Fresh load per case (unique module name so distinct cases in the same
    test run never alias one another's module object) -- assemble.py's own
    module-level `sys.path.insert(0, SCRIPTS_DIR)` + `import validate_draft
    as vd` / `import output_resolve` resolve harmlessly across repeated
    loads (byte-identical sibling copies; `build_nodestream` itself never
    calls into either import, so a stale cached `sys.modules` entry from an
    earlier case's copy cannot affect this one)."""
    return _load_module(f"verse_footnote_corpus_assemble_{label}", root / "scripts" / "assemble.py")


def assemble_profile(target_lang: str = "ru") -> dict:
    return {
        "target": {"language": {"code": target_lang}},
        "verse_policy": {"mode": "full_rhymed_plus_literal", "threshold_lines": None},
        "footnotes": {"apparatus_policy": "translate_all"},
        "project": {"title": "Test Book"},
    }


def build_nodestream_for(case: CorpusCase, manifest: dict, segpack: dict, draft: dict, tmp_path: Path):
    """End-to-end assemble step for one case: writes segpack/draft under a
    fresh assemble root and calls the REAL `build_nodestream()` in-process.
    Returns (nodestream, anchor_map)."""
    root = make_assemble_root(tmp_path, case.label)
    write_assemble_segment(root, "seg01", segpack, draft)
    assemble_mod = load_assemble_module(root, case.label)
    converged = {"seg01": {}}
    nodestream, anchor_map = assemble_mod.build_nodestream(assemble_profile(), manifest, converged)
    return nodestream, anchor_map, assemble_mod


# ---------------------------------------------------------------------------
# Layer 5 -- render_obsidian.py. Stateless (no DURABLE_ROOT self-anchor,
# takes out_dir as a parameter) -- safe to load ONCE and reuse, per
# render_obsidian.test.py's own `_load_render_obsidian_module()` convention.
# ---------------------------------------------------------------------------


def load_render_module():
    return _load_module("verse_footnote_corpus_render", SCRIPTS_DIR / "render_obsidian.py")


def render_profile(target_lang: str = "ru") -> dict:
    return {
        "target": {"language": {"code": target_lang}},
        "output": {
            "name_display": {"parenthetical_originals": "never"},
            "adapter_config": {"obsidian": {"folders": {}}},
        },
    }


def render_canon() -> dict:
    return {"entries": {}, "review_queue": [], "generation_hashes": {}}
