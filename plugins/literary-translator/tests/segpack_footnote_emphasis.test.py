"""tests/segpack_footnote_emphasis.test.py -- issue #725: a footnote
definition's own `<i>`/`<em>` emphasis must reach the translate prompt.

`segpack.py` handed the translator body blocks as raw `source_html` -- `<i>`
and all -- but footnote definitions as markup-stripped `plain_text` only. So
under `footnotes.apparatus_policy: translate_all | preserve_source`, the two
policies that exist precisely to translate the apparatus, the translator was
asked to preserve italics it had never been shown, while the same span was
visible in a body block one field away. Measured on the issue's own corpus
(Tallemant des Réaux, tome 2, fr->ru): 214 of 493 definition blocks carry
emphasis, 370 spans, all dropped; on one segment 13 of 31 first-round review
findings were "the source italicizes X, the translation leaves it roman".

WHY TAGS AND NOT MARKDOWN `*...*`. The issue proposed markdown, and three
successive adversarial review rounds each broke it in the same place -- the
delimiter rules:

  * `<i>a</i><em>b</em>` conserves every character and still emits `*a**b*`;
    so does the `<i>Le </i><i>Cid</i>` a PDF-to-EPUB converter routinely
    produces when it splits an italic run.
  * a source backslash before a span escapes the delimiter: `\\<i>w</i>` ->
    `\\*w*`.
  * CommonMark's punctuation-aware left/right flanking makes `<i>mot,</i>x` ->
    `*mot,*x` render as literal asterisks, and likewise for the equivalent CJK
    and Hebrew shapes.

A tag has no flanking rule, no escape sequence, no doubling, and no collision
with a `*` the source itself contains -- so every one of those failure modes
stops EXISTING rather than being detected one rule at a time. Carrying the
source's own notation also removes the body-vs-footnote asymmetry entirely.

What this suite locks down:
  1. `_footnote_source_text()` -- the conversion table, including every shape
     the markdown design got wrong, and the fallback set.
  2. THE ROUND-TRIP GATE -- the emitted text always reduces back to
     `plain_text` exactly, and any tag that survives balances.
  3. `build_pack()` -- footnotes carry emphasis end to end, while the
     name-candidate channels stay BYTE-IDENTICAL (the scan must keep reading
     `plain_text`; `>` is a token's `preceding_char` and `WRAPPERS` does not
     skip it, so scanning the marked text would flip a sentence-initial name
     into `strong_names`).
  4. `cache_key.compute_note_map_hash` moves for a carried footnote and does
     NOT move for one that fell back.

Loads the real, shipped `segpack.py` via importlib, mirroring tests/
segpack_verse_mount.test.py's own `_load_module` helper -- segpack.py's
`from bootstrap_names import ...` only resolves via sys.path[0] under a real
`python3 segpack.py` invocation, so its own scripts/ directory must be
inserted onto sys.path around the in-process load.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SCHEMAS_DIR = ASSETS_DIR / "schemas"
SEGPACK_SCRIPT = SCRIPTS_DIR / "segpack.py"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"
LANGUAGES_DIR = ASSETS_DIR / "languages"
CANON_SENSES_SCHEMA = SCHEMAS_DIR / "canon-senses.schema.json"

assert SEGPACK_SCRIPT.is_file(), f"segpack.py not found at {SEGPACK_SCRIPT}"
assert CACHE_KEY_SCRIPT.is_file(), f"cache_key.py not found at {CACHE_KEY_SCRIPT}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors segpack_verse_mount.test.py's own loader exactly."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


SEGPACK = _load_module("segpack_footnote_emphasis_under_test", SEGPACK_SCRIPT, SCRIPTS_DIR)
CACHE_KEY = _load_module("cache_key_footnote_emphasis_under_test", CACHE_KEY_SCRIPT, SCRIPTS_DIR)
CANON_SENSES = _load_module(
    "canon_senses_footnote_emphasis_under_test", SCRIPTS_DIR / "canon_senses.py", SCRIPTS_DIR
)

# Real shipped particle config -- build_pack()'s name-scanning pass needs a
# genuinely valid LanguageConfig (never hand-rolled JSON here).
LANG_CONFIG = SEGPACK.load_language_config("fr.json", LANGUAGES_DIR)

CARRY = SEGPACK._footnote_source_text

# A literal U+2028 must never be typed into this file (project convention);
# it is built from an escape so the byte is unambiguous in the source.
U2028 = "\u2028"


def _blk(plain, html):
    return {"plain_text": plain, "source_html": html}


# ---------------------------------------------------------------------------
# 1. _footnote_source_text() -- the conversion table.
#
# `plain_text` in each fixture is what the real extractor's
# normalize_text(block_text(clone)) produces for that `source_html`; the pairs
# were measured against those two functions lifted verbatim out of
# extract.py.template rather than hand-guessed.
# ---------------------------------------------------------------------------

CARRIED_CASES = [
    # (label, plain_text, source_html, expected source_text)
    (
        "one span",
        "Voir Les Historiettes de T.",
        "<p>Voir <i>Les Historiettes</i> de T.</p>",
        "Voir <i>Les Historiettes</i> de T.",
    ),
    (
        "em is normalised to i",
        "a b c",
        "<p><i>a</i> b <em>c</em></p>",
        "<i>a</i> b <i>c</i>",
    ),
    (
        "attributes and case are normalised away",
        "a b",
        '<p><I CLASS="x">a</I> b</p>',
        "<i>a</i> b",
    ),
    # The three shapes the markdown design emitted broken output for.
    (
        "adjacent spans (markdown emitted *a**b*)",
        "ab",
        "<p><i>a</i><em>b</em></p>",
        "<i>a</i><i>b</i>",
    ),
    (
        "converter-split run (markdown emitted *Le **Cid*)",
        "Le Cid de C.",
        "<p><i>Le </i><i>Cid</i> de C.</p>",
        "<i>Le </i><i>Cid</i> de C.",
    ),
    (
        "nested spans (markdown emitted *a *b* c*)",
        "a b c",
        "<p><i>a <em>b</em> c</i></p>",
        "<i>a <i>b</i> c</i>",
    ),
    (
        "whitespace-edged span (markdown emitted voir* le mot *ici)",
        "voir le mot ici",
        "<p>voir<i> le mot </i>ici</p>",
        "voir<i> le mot </i>ici",
    ),
    # Shapes the markdown design had to REFUSE, which tags carry.
    (
        "source's own *** (the issue's measured footnote 287)",
        "Voir ***note*** et ceci.",
        "<p>Voir ***note*** et <i>ceci</i>.</p>",
        "Voir ***note*** et <i>ceci</i>.",
    ),
    (
        "source's own *word*",
        "Use *word* and more.",
        "<p>Use *word* <i>and</i> more.</p>",
        "Use *word* <i>and</i> more.",
    ),
    (
        "backslash before a span (markdown emitted an escaped delimiter)",
        "\\word",
        "<p>\\<i>word</i></p>",
        "\\<i>word</i>",
    ),
    # CommonMark flanking shapes -- markdown rendered all three as literal
    # asterisks; a tag has no flanking rule at all.
    ("intraword punctuation, trailing", "mot,x", "<p><i>mot,</i>x</p>", "<i>mot,</i>x"),
    ("intraword punctuation, leading", "x,mot", "<p>x<i>,mot</i></p>", "x<i>,mot</i>"),
    ("CJK with no inter-word space", "漢。字", "<p><i>漢。</i>字</p>", "<i>漢。</i>字"),
    ("Hebrew", "שלום.א", "<p><i>שלום.</i>א</p>", "<i>שלום.</i>א"),
    # Encoding: entities stay exactly as source_html spells them, so a LITERAL
    # `<i>` in the text is never confused with a real tag.
    (
        "entities stay escaped",
        "Théâtre & Cie",
        "<p><i>Th&#233;&#226;tre</i> &amp; Cie</p>",
        "<i>Th&#233;&#226;tre</i> &amp; Cie",
    ),
    (
        "a literal &lt;i&gt; in the text stays escaped",
        "Use <i> literally",
        "<p><em>Use</em> &lt;i&gt; literally</p>",
        "<i>Use</i> &lt;i&gt; literally",
    ),
    (
        "non-emphasis markup is dropped, emphasis kept",
        "gras et ital",
        "<p><b>gras</b> et <i>ital</i></p>",
        "gras et <i>ital</i>",
    ),
    (
        "a sentinel inside a span is carried byte for byte",
        "Voir ceci⟦FNREF_12⟧ la.",
        "<p>Voir <i>ceci⟦FNREF_12⟧</i> la.</p>",
        "Voir <i>ceci⟦FNREF_12⟧</i> la.",
    ),
    (
        "U+2028 survives -- the whitespace class is the manifest's, not \\s",
        f"a{U2028}b",
        f"<p><i>a</i>{U2028}b</p>",
        f"<i>a</i>{U2028}b",
    ),
]

FALLBACK_CASES = [
    # (label, plain_text, source_html) -- source_text must be plain_text itself.
    ("no emphasis at all", "Rien de special.", "<p>Rien de special.</p>"),
    ("empty definition", "", "<p><i></i></p>"),
    ("whitespace-only definition", "   ", "<p><i>x</i></p>"),
    # Raw, un-normalised source_html: what a CUSTOM adapter may hand us. The
    # built-in gutenberg_epub adapter serialises through BeautifulSoup, which
    # repairs these shapes, so they arrive only from a hand-written extractor.
    ("raw self-closing <i/> with a close (R3-4)", "x", "<i/>x</i>"),
    ("raw stray close before any open", "a b", "a</i> b"),
    ("raw unbalanced open", "a b", "<i>a b"),
    ("raw attribute containing an unescaped >", "x y", '<i title="a>b">x</i> y'),
    ("emphasis inside an HTML comment is not markup", "a <i> b", "<p>a <!-- x --><i> b</p>"),
    # MISMATCHED NAMES. `<i>` stays open across all three characters and
    # `</em>` cannot close it, but the tag COUNT balances and every character
    # round-trips -- so a bare depth counter accepted this and emitted
    # `<i>a</i>b<i>c</i>`, leaving `b` roman where the source has it italic.
    # Text conservation cannot see that; only a name-keyed stack can.
    ("mismatched tag names, numerically balanced", "abc", "<p><i>a</em>b<em>c</i></p>"),
    ("close with no open at all", "ab", "<p>a</em>b</p>"),
    ("two opens, one close", "abc", "<p><i>a<em>b</em>c</p>"),
    # The text simply does not round-trip: a definition whose source spans two
    # block tags concatenates differently from the extractor's block_text().
    ("multi-block definition", "un a deux", "<p>un <i>a</i></p><p>deux</p>"),
]


@pytest.mark.parametrize(
    "label,plain,html,expected",
    CARRIED_CASES,
    ids=[c[0] for c in CARRIED_CASES],
)
def test_emphasis_is_carried(label, plain, html, expected):
    assert label  # every fixture is labelled, so a failure names the shape
    assert CARRY(_blk(plain, html)) == expected


@pytest.mark.parametrize(
    "label,plain,html", FALLBACK_CASES, ids=[c[0] for c in FALLBACK_CASES]
)
def test_unconvertible_definition_falls_back_to_plain_text_byte_for_byte(label, plain, html):
    assert label
    assert CARRY(_blk(plain, html)) == plain


def test_no_emphasis_returns_the_very_same_object():
    """The no-op path must not even rebuild the string -- a definition with no
    emphasis is `plain_text` itself, not a normalized copy of it that could
    differ (and move note_map_hash) for some whitespace the manifest kept."""
    block = _blk(f"a{U2028}b   c", "<p>a b c</p>")
    assert CARRY(block) is block["plain_text"]


def test_empty_definition_never_becomes_a_bare_tag_pair():
    """#397's no_empty_footnote_definitions relies on an empty definition
    yielding no source_text. `<i></i>` must not become `<i></i>` -- that is
    neither empty nor whitespace and would read downstream as a real note."""
    assert CARRY(_blk("", "<p><i></i></p>")) == ""


def test_missing_source_html_is_not_an_error():
    assert CARRY({"plain_text": "texte"}) == "texte"


def test_missing_plain_text_is_not_an_error():
    assert CARRY({"source_html": "<p><i>a</i></p>"}) == ""


@pytest.mark.parametrize(
    "block",
    [
        {"plain_text": None, "source_html": "<p><i>a</i></p>"},
        {"plain_text": 5, "source_html": "<p><i>a</i></p>"},
        {"plain_text": ["a"], "source_html": "<p><i>a</i></p>"},
        {"plain_text": "a", "source_html": 123},
        {"plain_text": "a", "source_html": None},
        {"plain_text": "a", "source_html": ["<i>"]},
    ],
)
def test_a_non_string_manifest_field_reaches_validate_segpack_not_a_traceback(block):
    """manifest.schema.json types both fields as strings, but a `custom`
    adapter's extractor is hand-written and this helper is what meets its
    output first. Reporting a shape problem is validate_segpack()'s job, so
    the value is handed straight back rather than raising out of build_pack()
    -- which is what the pre-#725 `def_block.get("plain_text", "")` did."""
    out = CARRY(block)
    assert out == (block["plain_text"] if isinstance(block["plain_text"], str)
                   else block["plain_text"])


def test_a_non_string_plain_text_still_yields_the_validator_s_own_message():
    pack = _build(FOOTNOTE_HTML)
    pack["footnotes"][0]["source_text"] = None
    errors = SEGPACK.validate_segpack(pack, "seg01")
    assert any("footnotes[0]" in e and "source_text" in e for e in errors), errors


# ---------------------------------------------------------------------------
# 2. The round-trip gate and the balance rule, as WHOLE-SET invariants over
#    every fixture above. A case added later cannot quietly skip them.
# ---------------------------------------------------------------------------

_ALL_FIXTURES = [(c[1], c[2]) for c in CARRIED_CASES] + [
    (c[1], c[2]) for c in FALLBACK_CASES
]


def _norm(text):
    """The manifest's OWN whitespace normalization -- extract.py.template's
    `_WS`, deliberately narrower than `\\s`."""
    import re

    return re.sub(r"[ \t\r\n\xa0]+", " ", text).strip()


def _strip_emphasis(text):
    """Undo exactly what the helper added: remove the bare `<i>`/`</i>` it
    emits, THEN unescape. Never call this on a `plain_text` -- a plain text may
    legitimately contain a literal `<i>` that this would eat, which is the
    whole reason the helper leaves entities escaped."""
    import re
    from html import unescape

    return _norm(unescape(re.sub(r"</?i>", "", text)))


def test_every_fixture_either_round_trips_or_is_plain_text_itself():
    """The one property that makes this safe, over every fixture at once:
    either the definition fell back to `plain_text` unchanged, or removing the
    emphasis tags and unescaping reproduces `plain_text` exactly."""
    carried = fell_back = 0
    for plain, html in _ALL_FIXTURES:
        out = CARRY(_blk(plain, html))
        if out == plain:
            fell_back += 1
            continue
        assert _strip_emphasis(out) == _norm(plain), (
            f"source_text does not reduce back to plain_text for {html!r}: {out!r}"
        )
        carried += 1
    # Both counts asserted: a loop that runs zero times prints exactly what a
    # passing one prints, and so does one that silently carried nothing.
    assert carried + fell_back == len(_ALL_FIXTURES)
    assert carried >= 18, f"only {carried} fixtures carried emphasis"
    assert fell_back >= 8, f"only {fell_back} fixtures fell back"


def test_every_emitted_tag_is_balanced_and_is_the_bare_spelling():
    import re

    for plain, html in _ALL_FIXTURES:
        out = CARRY(_blk(plain, html))
        if out == plain:
            continue
        assert out.count("<i>") == out.count("</i>"), out
        depth = 0
        for m in re.finditer(r"</?i>", out):
            depth += 1 if m.group(0) == "<i>" else -1
            assert depth >= 0, f"close before open in {out!r}"
        assert depth == 0, out
        # No tag other than the bare emphasis pair survives -- `<em>`,
        # attributes and every non-emphasis tag are normalised away. Compared
        # against the STILL-ESCAPED reduction, so a literal `<i>` that the
        # source spelled `&lt;i&gt;` is not counted as a surviving tag.
        import html as _html

        assert re.sub(r"</?i>", "", out).count("<") == _norm(
            _html.escape(plain, quote=False)
        ).count("<"), out


# ---------------------------------------------------------------------------
# 3. build_pack() end to end, and the name-channel inertness.
# ---------------------------------------------------------------------------

# `Effiat` is SINGLE-TOKEN, so segpack promotes it into strong_names only via
# the mid-sentence signal -- and it is placed FIRST IN ITS SENTENCE here
# deliberately. Measured against the real fr.json extractor:
#   extract_candidates("Effiat entra dans la salle.")      -> [("Effiat", False)]
#   extract_candidates("<i>Effiat</i> entra dans la salle.") -> [("Effiat", True)]
# That flip is the whole point of the fixture: if the scan ever reads the
# emphasis-carrying source_text instead of plain_text, `Effiat` enters
# strong_names for the wrong reason. A MULTIWORD name could not detect it --
# it is strong either way -- which is why this is not `Cosette Fantine`.
SENTENCE_INITIAL_NAME = "Effiat"
SPLIT_FORM = "Notre-Dame"

FOOTNOTE_PLAIN = "Effiat entra dans la salle. Il entra dans Notre-Dame le matin."
FOOTNOTE_HTML = (
    "<p><i>Effiat</i> entra dans la salle. Il entra dans <i>Notre-Dame</i> le matin.</p>"
)
FOOTNOTE_HTML_NO_TAGS = (
    "<p>Effiat entra dans la salle. Il entra dans Notre-Dame le matin.</p>"
)


def _base_generation_hashes():
    return {"source_extraction_hash": "a" * 40, "source_input_hash": "b" * 40}


def _canon():
    return {
        "entries": {},
        "generation_hashes": {
            "particle_config_hash": "c" * 40,
            "derivation_bundle_hash": "d" * 40,
        },
    }


def _manifest(def_html, def_plain=FOOTNOTE_PLAIN):
    return {
        "segments": [
            {
                "seg": "seg01",
                "title_text": "Chapter One",
                "kind": "body",
                "word_count": 20,
                "block_ids": ["p1"],
            }
        ],
        "blocks": {
            "p1": {
                "id": "p1",
                "order_index": 0,
                "plain_text": "Le corps du texte ⟦FNREF_1⟧ continue.",
                "fnrefs": [1],
            },
            "fn1": {"id": "fn1", "order_index": 99, "plain_text": def_plain,
                    "source_html": def_html},
        },
        "footnotes": [{"n": 1, "def_block": "fn1"}],
        "verse": {"store": []},
        "generation_hashes": _base_generation_hashes(),
    }


def _senses_doc():
    return {
        "schema_version": 1,
        "entries_by_source_form": {
            SPLIT_FORM: {
                "senses": [
                    {
                        "sense_id": "cathedral",
                        "disambiguator": "the cathedral",
                        "index_scope": "narrative",
                        "evidence": {
                            "block": "PARA:seg01:0001",
                            "seg": "seg01",
                            "char_start": 0,
                            "char_end": len(SPLIT_FORM),
                            "context_start": 0,
                            "context_end": 64,
                            "sha256": "0" * 64,
                        },
                    },
                    {
                        "sense_id": "virgin",
                        "disambiguator": "the Virgin",
                        "index_scope": "allusion",
                        "evidence": {
                            "block": "PARA:seg01:0002",
                            "seg": "seg01",
                            "char_start": 0,
                            "char_end": len(SPLIT_FORM),
                            "context_start": 0,
                            "context_end": 64,
                            "sha256": "0" * 64,
                        },
                    },
                ]
            }
        },
    }


def _senses(tmp_path):
    path = tmp_path / "canon_senses.json"
    path.write_text(json.dumps(_senses_doc(), ensure_ascii=False), encoding="utf-8")
    return CANON_SENSES.load_senses(path, allow_absent=False, schema_path=CANON_SENSES_SCHEMA)


def _build(def_html, senses=None, policy="translate_all", def_plain=FOOTNOTE_PLAIN):
    return SEGPACK.build_pack(
        "seg01", _manifest(def_html, def_plain), _canon(), LANG_CONFIG, policy, senses
    )


def test_build_pack_carries_footnote_emphasis_into_source_text():
    pack = _build(FOOTNOTE_HTML)
    assert pack["footnotes"] == [
        {
            "n": 1,
            "source_text": (
                "<i>Effiat</i> entra dans la salle. "
                "Il entra dans <i>Notre-Dame</i> le matin."
            ),
        }
    ]


@pytest.mark.parametrize("policy", ["translate_all", "preserve_source"])
def test_both_footnote_carrying_policies_carry_emphasis(policy):
    pack = _build(FOOTNOTE_HTML, policy=policy)
    assert "<i>" in pack["footnotes"][0]["source_text"]


def test_fixture_actually_yields_the_sentence_initial_candidate_when_marked():
    """VACUITY GUARD for the inertness test below. If scanning the MARKED text
    would not change the name channels, that test proves nothing -- so prove
    here, against the real extractor, that it would."""
    bn = _load_module(
        "bootstrap_names_vacuity_probe", SCRIPTS_DIR / "bootstrap_names.py", SCRIPTS_DIR
    )
    plain_hits = dict(bn.extract_candidates(FOOTNOTE_PLAIN, LANG_CONFIG))
    marked = CARRY(_blk(FOOTNOTE_PLAIN, FOOTNOTE_HTML))
    marked_hits = dict(bn.extract_candidates(marked, LANG_CONFIG))
    assert plain_hits[SENTENCE_INITIAL_NAME] is False, plain_hits
    assert marked_hits[SENTENCE_INITIAL_NAME] is True, (
        "the marked text no longer flips mid_sentence for "
        f"{SENTENCE_INITIAL_NAME!r} -- the inertness test below would pass "
        "vacuously. hits=" + repr(marked_hits)
    )


def test_name_channels_are_byte_identical_with_and_without_emphasis(tmp_path):
    """The change must be INERT for every name channel: the scan reads
    plain_text, never the emphasis-carrying source_text. Exact structural
    equality over all four channels at once -- a per-channel presence check
    would survive a swap, and `split_names` in particular is derived
    independently of the other three."""
    with_tags = _build(FOOTNOTE_HTML, _senses(tmp_path))
    without_tags = _build(FOOTNOTE_HTML_NO_TAGS, _senses(tmp_path))

    assert with_tags["footnotes"] != without_tags["footnotes"], (
        "the two fixtures produced identical footnotes -- the emphasis was not "
        "carried at all, so this test cannot detect the regression it pins"
    )
    assert with_tags["split_names"], (
        "split_names is empty -- the sidecar fixture stopped working and this "
        "assertion would pass vacuously"
    )
    for channel in ("names", "new_names", "canon_names", "split_names", "canon_map"):
        assert with_tags[channel] == without_tags[channel], channel


def test_sentence_initial_name_stays_out_of_strong_names():
    """The concrete consequence of the mutation, named: `Effiat` is
    single-token and sentence-initial, so it must NOT be a candidate."""
    pack = _build(FOOTNOTE_HTML)
    assert SENTENCE_INITIAL_NAME not in pack["names"], pack["names"]


# ---------------------------------------------------------------------------
# 4. note_map_hash -- the migration cost, and the fallback's lack of one.
# ---------------------------------------------------------------------------


def test_note_map_hash_moves_when_emphasis_is_carried():
    carried = CACHE_KEY.compute_note_map_hash(_build(FOOTNOTE_HTML))
    bare = CACHE_KEY.compute_note_map_hash(_build(FOOTNOTE_HTML_NO_TAGS))
    assert carried != bare, (
        "note_map_hash did not move -- the translator's input changed, so it must"
    )


def test_note_map_hash_does_not_move_for_a_definition_that_falls_back():
    """A fallback costs NO resume identity: a definition whose emphasis cannot
    be carried hashes exactly as it did before this change."""
    fell_back = CACHE_KEY.compute_note_map_hash(_build("<i>a b"))  # raw, unbalanced
    bare = CACHE_KEY.compute_note_map_hash(_build(FOOTNOTE_HTML_NO_TAGS))
    assert fell_back == bare


# ---------------------------------------------------------------------------
# 5. The other consumers of footnotes[].source_text, pinned as inert.
# ---------------------------------------------------------------------------


def _census():
    return _load_module(
        "verbatim_census_footnote_emphasis_under_test",
        SCRIPTS_DIR / "verbatim_census.py",
        SCRIPTS_DIR,
    )


def test_verbatim_census_folds_emphasis_out_before_comparing_runs():
    """verbatim_census reads footnotes[].source_text and extracts runs of
    source-script letters. A tag WRAPPING A WHOLE WORD sits where a space
    already broke the run, so that case would look fine either way -- the case
    that matters is a span INSIDE a word: `<i>אב</i>גד` reads as two runs while
    the correct draft carries one, and the census would queue an intact
    translation as a tier-1 letter_diff.

    Drives `_source_units()`, the real CALL SITE, not `_fold_emphasis()` -- a
    test of the helper alone stays green when the call site stops using it,
    which is exactly the mutation this pins."""
    census = _census()
    plain = "אבגד"
    carried = CARRY(_blk(plain, "<p><i>אב</i>גד</p>"))
    assert carried == "<i>אב</i>גד", carried
    assert census.hebrew_runs(carried) == ["אב", "גד"], (
        "fixture no longer splits the run -- this test would pass vacuously"
    )

    pack = {"blocks": [], "footnotes": [{"n": 1, "source_text": carried}]}
    units, _missing = census._source_units("seg01", pack)
    assert census.hebrew_runs(units["footnotes:1"]) == census.hebrew_runs(plain), (
        "the census's own unit still carries the markup: an intact draft run "
        f"would be queued as a letter_diff. unit={units['footnotes:1']!r}"
    )


def test_verbatim_census_fold_also_undoes_the_preserved_escaping():
    census = _census()
    assert census._fold_emphasis("<i>Th&#233;</i>&amp;") == "Thé&"


def test_final_audit_term_fold_sees_a_term_the_source_italicises_mid_word():
    """W7's term-consistency check counts a pinned term inside each carrier.
    With `Le pr<i>ésident</i>` the source count would drop to ZERO, and zero
    can never exceed the target's count -- so a genuinely drifted footnote
    would go unreported on a completely green run."""
    audit = _load_module(
        "final_audit_footnote_emphasis_under_test",
        SCRIPTS_DIR / "final_audit.py",
        SCRIPTS_DIR,
    )
    term = audit._fold_term_text("président")
    assert term in audit._fold_term_text("Le pr<i>ésident</i> de la cour")
    assert term in audit._fold_term_text("Le pr&#233;sident de la cour")
    assert term in audit._fold_term_text("Le président de la cour")


def test_a_malformed_definition_does_not_scan_quadratically():
    """`<[^>]+>` restarts its end-of-string scan at every `<`, and this is the
    one path that walks raw source_html. A definition that is a long run of
    `<` with no `>` must stay linear."""
    import time

    block = _blk("x", "<" * 200_000 + "<i")
    start = time.monotonic()
    assert CARRY(block) == "x"
    assert time.monotonic() - start < 2.0


def test_validate_segpack_still_accepts_the_carried_shape():
    """source_text stays a plain string: the schema's shape, _FOOTNOTE_KEYS and
    validate_segpack() are all untouched by this change."""
    pack = _build(FOOTNOTE_HTML)
    assert SEGPACK.validate_segpack(pack, "seg01") == []
