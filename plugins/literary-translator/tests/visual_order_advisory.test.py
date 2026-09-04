"""Tests for the visual-order advisory scan in ``scripts/validate_extraction.py``
(issue #489).

A source EPUB converted from a PDF can carry RTL text in VISUAL order. The
extraction is byte-faithful and every deterministic gate passes it, because none
of them read what a fragment MEANS -- so the damage lands on the LLM turns, and
on this book it produced both false reviewer findings and one mistranslation that
reached a converged draft. The scan ends that silence.

What these tests are built to catch, in order of how easily each would otherwise
ship green:

  * the scan not being WIRED at all -- deleting the ``run_advisory_scans()`` call
    from ``main()`` must turn ``test_wiring_*`` red;
  * the advisory changing the EXIT DECISION in either direction: rescuing a
    failing manifest, or refusing a passing one when the scan itself raises;
  * evidence printed RAW. A bidi-reordering terminal renders a corrupted RTL
    token identically to an intact one, so evidence meant to be ADJUDICATED must
    be codepoints. Asserting "contains a backslash-u" is not enough -- an
    implementation can escape the token and still print a raw histogram key --
    so the payload is asserted ASCII-only as a whole;
  * an embedded verse being missed. Its text is lifted OUT of its carrier block
    and replaced by a placeholder, so a blocks-only scan cannot see it;
  * a standalone verse being counted TWICE (it is already a blocks[] entry).

**Every RTL string here is built from ``\\uXXXX`` escapes on purpose.** Pasting
the literal characters is how an invisible control or a reordered anchor silently
gets into a fixture -- and in a file about bidi mangling, a fixture that does not
say what it contains is worthless.
"""

import importlib.util
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
    / "validate_extraction.py"
)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The sibling suite owns the schema-valid baseline manifest and the main()
# harness; re-deriving either here would let the two drift apart silently.
_sib = _load("validate_extraction_test", Path(__file__).parent / "validate_extraction.test.py")
_baseline_manifest = _sib._baseline_manifest
_run_gate = _sib._run_gate

# Take the module under test FROM the sibling, never a second load of the same
# file. Two importlib loads produce two distinct module objects, so a
# monkeypatch applied to one is invisible to the main() living in the other --
# the forced-raise test below is what caught that, by passing its exit-code
# assertion while the advisory it was supposed to force never appeared.
ve = _sib.ve
assert ve.__file__ == str(SCRIPT_PATH), "the sibling must be testing this same script"


# --- Hebrew/Arabic fixtures, spelled out -----------------------------------

TOV = "\u05D8\u05D5\u05D1"                    # tet-vav-bet
SHALOM = "\u05E9\u05DC\u05D5\u05DD"          # shin-lamed-vav-mem
# nun + SHEVA, i.e. a niqqud mark sitting between the punctuation and the first
# letter once the token is led by a stop -- the case that defeats a naive
# ``(?=[HEBREW LETTER])`` lookahead.
NIQQUD_WORD = "\u05E0\u05B0\u05D9\u05E9"
SOF_PASUQ = "\u05C3"                            # HEBREW PUNCTUATION SOF PASUQ
RLM = "\u200F"                                  # RIGHT-TO-LEFT MARK (category Cf)
WALAKIN = "\u0648\u0644\u0643\u0646"          # Arabic "walakin"
ARABIC_FULL_STOP = "\u06D4"
ELLIPSIS = "\u2026"
ADLAM_ALIF = "\U0001E900"                      # ADLAM CAPITAL LETTER ALIF (astral, bidi R)


# ---------------------------------------------------------------------------
# The signature: positive direction
# ---------------------------------------------------------------------------

def test_leading_full_stop_on_hebrew_token_hits():
    # The shape of PARA:seg22:0055 on the live book: a stop at the LEFT edge of
    # a run, which logical order cannot produce.
    assert ve._leading_terminal_punct_hits(f".{SHALOM}") == [f".{SHALOM}"]


def test_leading_comma_on_hebrew_token_hits():
    assert ve._leading_terminal_punct_hits(f",{TOV}") == [f",{TOV}"]


def test_niqqud_between_punctuation_and_letter_still_hits():
    # Stripping niqqud on one side only once turned 777 real occurrences into
    # 677 -- a mark between the stop and the letter must be skipped, not fatal.
    token = f".{NIQQUD_WORD}"
    assert ve._leading_terminal_punct_hits(token) == [token]


def test_bidi_format_character_between_punctuation_and_letter_still_hits():
    # RLM/LRM is exactly what a bidi-aware converter emits at a run edge.
    token = f".{RLM}{SHALOM}"
    assert ve._leading_terminal_punct_hits(token) == [token]


def test_sof_pasuq_led_token_hits():
    token = f"{SOF_PASUQ}{TOV}"
    assert ve._leading_terminal_punct_hits(token) == [token]


def test_arabic_full_stop_led_token_hits():
    token = f"{ARABIC_FULL_STOP}{WALAKIN}"
    assert ve._leading_terminal_punct_hits(token) == [token]


def test_astral_rtl_letter_is_seen():
    # An earlier revision asked a hand-kept table of BMP ranges instead of
    # Unicode, so Adlam, Hanifi Rohingya and the Syriac supplement carried the
    # full signature and still finished on an unqualified green.
    token = f".{ADLAM_ALIF}"
    assert ve._leading_terminal_punct_hits(token) == [token]


# ---------------------------------------------------------------------------
# The signature: false-positive direction
#
# Measured on 46 762 RTL tokens of known logical-order Hebrew (pointed and
# unpointed), Arabic, Persian and Urdu: zero hits. These pin the constructions
# that would break that if the terminal set were widened carelessly.
# ---------------------------------------------------------------------------

def test_logical_order_hebrew_does_not_hit():
    assert ve._leading_terminal_punct_hits(f"{SHALOM} {TOV}. {NIQQUD_WORD},") == []


def test_sof_pasuq_terminated_verse_does_not_hit():
    # The mirror of test_sof_pasuq_led_token_hits, and the reason adding that
    # mark to the terminal set is safe: 267 real occurrences in the Hebrew
    # negative corpus admitted nothing, because in logical order a sof pasuq
    # ENDS a token.
    assert ve._leading_terminal_punct_hits(f"{SHALOM} {TOV}{SOF_PASUQ}") == []


def test_sentence_initial_ellipsis_does_not_hit():
    # Legitimate logical-order elision. The ellipsis is excluded from the
    # terminal set for exactly this reason -- and it cost nothing: it
    # contributed zero of the positive control book's 921 hits.
    assert ve._leading_terminal_punct_hits(f"{ELLIPSIS}{WALAKIN}") == []


def test_opening_punctuation_does_not_hit():
    assert ve._leading_terminal_punct_hits(f"({SHALOM} \u00AB{TOV} [{TOV}") == []


def test_latin_text_does_not_hit():
    assert ve._leading_terminal_punct_hits(".hello, world) again") == []


# ---------------------------------------------------------------------------
# Scan population: blocks PLUS embedded verse, and no double-count
# ---------------------------------------------------------------------------

def _manifest_with(blocks=None, verse_store=None):
    return {
        "blocks": blocks or {},
        "verse": {"store": verse_store or []},
    }


def test_embedded_verse_alone_triggers_the_advisory():
    # An embedded verse's text is lifted OUT of its carrier block and replaced
    # by a placeholder, so a blocks-only scan is blind to it. The carrier here
    # is deliberately clean.
    m = _manifest_with(
        blocks={"PARA:seg01:0001": {"plain_text": f"{SHALOM} ⟦VERSE_V001_abc⟧"}},
        verse_store=[{"vid": "V001", "mount": "embedded", "plain_text": f".{TOV}"}],
    )
    n_hits, n_units_with_hits, _, _, samples = ve.scan_visual_order(m)
    assert n_hits == 1
    assert n_units_with_hits == 1
    assert samples[0][0] == "verse:V001", "a verse sample must be labelled by vid"


def test_standalone_verse_is_counted_exactly_once():
    # mount == "block" verses ARE ordinary blocks[] entries. Asserting merely
    # "an advisory fired" would not notice the same hit counted twice.
    m = _manifest_with(
        blocks={"VERSE:seg01:0012": {"plain_text": f".{TOV}"}},
        verse_store=[{"vid": "V001", "mount": "block", "plain_text": f".{TOV}"}],
    )
    n_hits, n_units_with_hits, _, histogram, _ = ve.scan_visual_order(m)
    assert n_hits == 1, "a standalone verse must not be scanned twice"
    assert n_units_with_hits == 1
    assert histogram == {"U+002E": 1}


def test_clean_manifest_produces_no_advisory():
    m = _manifest_with(blocks={"PARA:seg01:0001": {"plain_text": f"{SHALOM} {TOV}."}})
    assert ve.run_advisory_scans(m) == []


def test_non_rtl_manifest_produces_no_advisory():
    m = _manifest_with(blocks={"PARA:seg01:0001": {"plain_text": "Ordinary Latin prose."}})
    assert ve.run_advisory_scans(m) == []


# ---------------------------------------------------------------------------
# Evidence must be codepoints, never glyphs
# ---------------------------------------------------------------------------

def test_evidence_payload_is_ascii_only():
    # Asserted on the helper's OWN payload, not the gate's combined stream --
    # that stream legitimately carries non-ASCII paths and other output.
    m = _manifest_with(
        blocks={"PARA:seg01:0001": {"plain_text": f"{SOF_PASUQ}{RLM}{TOV}"}}
    )
    (name, detail), = ve.run_advisory_scans(m)
    assert name == ve.VISUAL_ORDER_SCAN_NAME
    assert detail.isascii(), (
        "raw RTL or bidi-control bytes in the advisory payload: a bidi terminal "
        "renders a corrupted token exactly like an intact one, so evidence that "
        "is meant to be adjudicated must be escaped"
    )


def test_exact_escaped_sample():
    # The leading mark is deliberately non-ASCII (sof pasuq) and the token
    # carries a bidi control, so an implementation that escapes the letters but
    # emits a raw mark or a raw histogram key cannot satisfy this.
    token = f"{SOF_PASUQ}{RLM}{TOV}"
    assert ve._escape_evidence(token) == "\\u05C3\\u200F\\u05D8\\u05D5\\u05D1"


def test_astral_codepoints_escape_unambiguously():
    # A four-digit form would render this as "\\u1E900", which reads as U+1E90
    # followed by "0" -- evidence whose spelling is ambiguous cannot settle the
    # question it was printed to settle.
    assert ve._escape_evidence(ADLAM_ALIF) == "\\U0001E900"


def test_histogram_keys_are_codepoints_not_glyphs():
    m = _manifest_with(blocks={"PARA:seg01:0001": {"plain_text": f"{SOF_PASUQ}{TOV}"}})
    _, _, _, histogram, _ = ve.scan_visual_order(m)
    assert histogram == {"U+05C3": 1}


def test_a_non_ascii_block_id_cannot_reach_the_payload_raw():
    # The label is extractor-authored and a custom extractor may emit anything.
    # A raw RTL id would reorder the very diagnostic being adjudicated -- and it
    # would do so while every other assertion here still passed.
    m = _manifest_with(blocks={f"PARA:{SHALOM}:0001": {"plain_text": f".{TOV}"}})
    (_, detail), = ve.run_advisory_scans(m)
    assert detail.isascii(), "a non-ASCII block id reached the advisory payload raw"
    assert "\\u05E9" in detail, "the label should appear, escaped rather than dropped"


# ---------------------------------------------------------------------------
# Detached combining marks (#845): a SECOND class the punctuation screen
# above cannot reach -- a vowel or dagesh with no letter behind it.
# ---------------------------------------------------------------------------

import time

SHEVA = "\u05B0"    # HEBREW POINT SHEVA (Mn) -- a mark with nothing else needed to detach it
PATAH = "\u05B7"    # HEBREW POINT PATAH (Mn)
DAGESH = "\u05BC"   # HEBREW POINT DAGESH OR MAPIQ (Mn)
SEGOL = "\u05B6"    # HEBREW POINT SEGOL (Mn)
QAMATS = "\u05B8"   # HEBREW POINT QAMATS (Mn)
HE = "\u05D4"       # HEBREW LETTER HE
AYIN = "\u05E2"     # HEBREW LETTER AYIN
DALET = "\u05D3"    # HEBREW LETTER DALET
SHIN_LETTER = "\u05E9"  # HEBREW LETTER SHIN, bare (SHALOM above already opens with shin+lamed+vav+mem)
YOD = "\u05D9"      # HEBREW LETTER YOD
ALEF = "\u05D0"     # HEBREW LETTER ALEF
RESH = "\u05E8"     # HEBREW LETTER RESH


def test_mark_after_space_hits():
    # A mark that opens the SECOND token of the text: nothing but whitespace
    # is behind it, which is detached under any reading.
    token = f"{SHEVA}{TOV}"
    assert ve._detached_mark_hits(f"{SHALOM} {token}") == [(token, 1)]


def test_mark_at_token_index_zero_hits():
    # The same case with no preceding token at all -- token[0] is the very
    # first character of the text.
    token = f"{SHEVA}{TOV}"
    assert ve._detached_mark_hits(token) == [(token, 1)]


def test_mark_after_terminal_punctuation_hits():
    token = f".{SHEVA}{TOV}"
    assert ve._detached_mark_hits(token) == [(token, 1)]


def test_pointed_cluster_second_and_third_marks_both_attach_to_base():
    # he + patah + dagesh (transliterated "haC" with a dot, cited in
    # _leading_terminal_punct_hits's own comment above) is a LEGITIMATE
    # pointed letter, not two detached marks.
    # Asserted incrementally: the second character (patah) attaching to the
    # base is the easy case a naive one-char-back lookback also gets right;
    # the third character (dagesh) is the case that predicate gets WRONG,
    # because ITS immediate predecessor is the patah, not the letter -- only
    # walking back across the whole mark run finds the base behind it too.
    assert ve._detached_mark_hits(HE + PATAH) == []
    assert ve._detached_mark_hits(HE + PATAH + DAGESH) == []


def test_a_non_letter_RESETS_the_base_after_a_letter_earlier_in_the_token():
    # The flag the pass carries is "the last significant character was a
    # LETTER", not "a letter appeared somewhere in this token". An
    # implementation that never resets it -- the plausible wrong one -- reads
    # the alef at the front and then attaches every later mark to it, however
    # much punctuation stands in between, and silently under-counts exactly
    # the visual-order shape this class exists to find: a mark stranded on the
    # far side of a displaced stop.
    token = ALEF + "." + SHEVA + TOV
    assert ve._detached_mark_hits(token) == [(token, 1)]


def test_the_documented_keycap_false_positive_is_pinned_at_two():
    # NOT a wish: _detached_mark_hits's docstring, false-green-gate.md's
    # stated-limitations list and this release's CHANGELOG all tell an
    # operator that a keycap sequence adds 2 to the figure. Documented
    # behaviour with no test is documentation that can go false in silence --
    # and the fix here would be to change the docs, never to special-case
    # UTS #51 in a predicate whose target corpus contains no emoji at all.
    keycap = "1\uFE0F\u20E3"  # DIGIT ONE + VS16 (Mn) + COMBINING KEYCAP (Me)
    assert ve._detached_mark_hits(keycap) == [(keycap, 2)]


def test_letter_rlm_mark_yields_zero():
    # RLM (Cf) between a letter and its mark is exactly what a bidi-aware
    # converter emits there -- transparent, not a base-letter reset.
    assert ve._detached_mark_hits(HE + RLM + PATAH) == []


def test_unpointed_rtl_unit_yields_zero_marks():
    m = _manifest_with(blocks={"PARA:seg01:0001": {"plain_text": f"{SHALOM} {TOV}"}})
    assert ve.scan_detached_marks(m) == (0, 0)


def test_latin_unit_is_never_scanned_for_marks():
    # A combining mark with no base is detached by the predicate's OWN logic
    # (asserted directly, first) -- but scan_detached_marks only ever sees
    # RTL-bearing units, via the shared _rtl_text_units filter, so a Latin
    # unit carrying the identical construction is never handed to it at all.
    detached_latin_token = "\u0301hello"  # combining acute at token start
    assert ve._detached_mark_hits(detached_latin_token) == [(detached_latin_token, 1)]
    m = _manifest_with(blocks={"PARA:seg01:0001": {"plain_text": detached_latin_token}})
    assert ve.scan_detached_marks(m) == (0, 0)


def test_two_marks_in_one_unit_counts_two_marks_one_unit():
    m = _manifest_with(
        blocks={"PARA:seg01:0001": {"plain_text": f"{SHEVA}{TOV} {SHEVA}{SHALOM}"}}
    )
    assert ve.scan_detached_marks(m) == (2, 1)


def test_embedded_and_standalone_verses_are_in_the_detached_population():
    # scan_detached_marks must walk the SAME population as scan_visual_order,
    # not blocks[] alone. An embedded verse's text is lifted OUT of its
    # carrier block and replaced by a placeholder, so a blocks-only numerator
    # would drop it while the printed denominator -- n_rtl_units, which comes
    # from scan_visual_order -- still counts it: a figure wrong in the
    # direction that under-reports the class this release exists to surface.
    # A standalone verse (mount == "block") is already a blocks[] entry and
    # must not be counted twice.
    m = _manifest_with(
        blocks={
            "PARA:seg01:0001": {"plain_text": f"{SHEVA}{TOV}"},
            "PARA:seg01:0002": {"plain_text": f"{SHEVA}{SHALOM}"},
        },
        verse_store=[
            {"vid": "v1", "mount": "embedded", "plain_text": f"{SHEVA}{TOV}"},
            {"vid": "v2", "mount": "block", "plain_text": f"{SHEVA}{SHALOM}"},
        ],
    )
    # 3 marks over 3 units: two blocks plus the embedded verse. The standalone
    # verse contributes nothing of its own -- it IS PARA:seg01:0002 already.
    assert ve.scan_detached_marks(m) == (3, 3)
    _, _, n_rtl_units, _, _ = ve.scan_visual_order(m)
    assert n_rtl_units == 3, "numerator and denominator must share one population"


def test_detached_marks_alone_do_not_fire_the_advisory():
    # Pins the accepted non-goal (frozen in the plan): the firing condition
    # stays `if n_hits:` only. A book with detached marks and zero
    # punctuation hits stays silent. An implementation that fires on
    # `n_hits or n_marks` passes every other test in this file while
    # breaking this one.
    m = _manifest_with(
        blocks={"PARA:seg01:0001": {"plain_text": f"{SHEVA}{TOV} {SHALOM}"}}
    )
    n_hits, _, _, _, _ = ve.scan_visual_order(m)
    n_marks, _ = ve.scan_detached_marks(m)
    assert n_hits == 0
    assert n_marks > 0, "fixture must actually carry a detached mark"
    assert ve.run_advisory_scans(m) == []


def test_fired_advisory_prints_detached_figure_including_zero():
    m = _manifest_with(blocks={"PARA:seg01:0001": {"plain_text": f".{SHALOM} ,{TOV}"}})
    assert ve.scan_detached_marks(m) == (0, 0), "fixture must carry zero detached marks"
    (_, detail), = ve.run_advisory_scans(m)
    assert "0 combining mark(s) in 0 of" in detail, (
        "the detached figure must print even when it is zero -- an omitted "
        "line reads as 'no such class'"
    )


def test_fired_advisory_prints_nonzero_detached_figure():
    m = _manifest_with(
        blocks={"PARA:seg01:0001": {"plain_text": f".{SHALOM} {SHEVA}{TOV}"}}
    )
    n_marks, n_units_with_marks = ve.scan_detached_marks(m)
    assert n_marks == 1
    (_, detail), = ve.run_advisory_scans(m)
    assert f"{n_marks} combining mark(s) in {n_units_with_marks} of" in detail


def test_advisory_payload_stays_ascii_with_detached_text():
    m = _manifest_with(
        blocks={"PARA:seg01:0001": {"plain_text": f"{SOF_PASUQ}{RLM}{TOV} {SHEVA}{SHALOM}"}}
    )
    (_, detail), = ve.run_advisory_scans(m)
    assert detail.isascii()
    assert "NOTHING HERE SAMPLES THE SECOND CLASS" in detail
    assert "adjudicating the sample settles the punctuation class only" in detail


def test_long_mark_run_scans_in_linear_time():
    # Guards the O(n) single pass against a regression to the O(k^2) backward
    # walk it replaced -- measured 11.5s at 20 000 marks for the walk against
    # 0.0009s for this pass. A wall-clock bound is the only assertion that
    # can tell the two implementations apart; a correctness assertion alone
    # passes either.
    token = SHEVA * 20000
    started = time.perf_counter()
    hits = ve._detached_mark_hits(token)
    elapsed = time.perf_counter() - started
    assert hits == [(token, 20000)]
    assert elapsed < 1.0, f"took {elapsed:.3f}s -- looks quadratic, not linear"


def test_issue_worked_case_segol_floated_to_block_start_is_a_hit():
    # The issue's own worked example: the segol that belongs to the shin
    # (forming the conjunction "she-") has been floated by the converter to
    # the START of the block, ahead of the first word entirely -- nothing
    # precedes it but the top of the block, which is detached under any
    # reading.
    ad = AYIN + PATAH + DALET                       # transliterated "ad" (until)
    yair = YOD + QAMATS + ALEF + YOD + RESH          # transliterated "yair", a legitimately pointed cluster
    block_text = f"{SEGOL}{ad} {SHIN_LETTER} {yair}"
    assert ve._detached_mark_hits(block_text) == [(f"{SEGOL}{ad}", 1)]


# ---------------------------------------------------------------------------
# End-to-end through the real main(): the advisory is REPORT-ONLY
# ---------------------------------------------------------------------------

def _manifest_with_hits():
    """The schema-valid baseline, with one block's text made visual-order."""
    m = _baseline_manifest()
    first = sorted(m["blocks"])[0]
    m["blocks"][first]["plain_text"] = f".{SHALOM} ,{TOV}"
    return m


def test_wiring_hit_bearing_manifest_warns_and_still_exits_zero(tmp_path, monkeypatch, capsys):
    code = _run_gate(tmp_path, monkeypatch, _manifest_with_hits())
    captured = capsys.readouterr()
    assert code == 0, "an advisory must never refuse an ingestion"
    assert f"WARN {ve.VISUAL_ORDER_SCAN_NAME}:" in captured.err
    assert "ADVISORY" in captured.out, (
        "a fired advisory must be named in the final status line, never left "
        "sitting under an unqualified OK"
    )


def test_wiring_clean_manifest_emits_no_warn_and_an_unqualified_ok(tmp_path, monkeypatch, capsys):
    code = _run_gate(tmp_path, monkeypatch, _baseline_manifest())
    captured = capsys.readouterr()
    assert code == 0
    assert "WARN" not in captured.err
    assert "ADVISORY" not in captured.out
    assert "OK -- post-extraction gate passed" in captured.out


def test_advisory_does_not_rescue_a_failing_manifest(tmp_path, monkeypatch, capsys):
    # Asserting only the exit code and the absent OK would be satisfied by an
    # early exit that never ran the scan -- the WARN assertion is what makes
    # this test about the advisory rather than about the mandatory check.
    m = _manifest_with_hits()
    m["spine"] = list(reversed(m["spine"]))
    code = _run_gate(tmp_path, monkeypatch, m)
    captured = capsys.readouterr()
    assert code == 1
    assert f"WARN {ve.VISUAL_ORDER_SCAN_NAME}:" in captured.err
    assert "OK -- post-extraction gate passed" not in captured.out


def test_schema_valid_but_underivable_manifest_still_warns(tmp_path, monkeypatch, capsys):
    # The placement decision exists for this case: the count fields
    # run_derivable_checks() indexes are NOT schema-required, so a schema-valid
    # manifest can reach its exit-1 structural boundary. An advisory placed
    # after that boundary would be skipped on exactly the malformed inputs most
    # likely to be mangled.
    m = _manifest_with_hits()
    del m["segments"][0]["n_para"]
    code = _run_gate(tmp_path, monkeypatch, m)
    captured = capsys.readouterr()
    assert code == 1
    assert f"WARN {ve.VISUAL_ORDER_SCAN_NAME}:" in captured.err
    assert "FAIL manifest_wellformed" in captured.err
    assert "Traceback" not in captured.err


def _boom(_manifest):
    raise RuntimeError("synthetic scan failure")


def test_a_raising_scan_still_cannot_rescue_a_failing_manifest(tmp_path, monkeypatch, capsys):
    # The no-rescue half of the report-only contract, in the failure mode. The
    # clean-manifest version below would be satisfied by a handler that prints
    # "scan unavailable" and then exits 0 -- which would rescue EVERY failing
    # manifest.
    monkeypatch.setattr(ve, "run_advisory_scans", _boom)
    m = _baseline_manifest()
    m["spine"] = list(reversed(m["spine"]))
    code = _run_gate(tmp_path, monkeypatch, m)
    captured = capsys.readouterr()
    assert code == 1
    assert "scan unavailable" in captured.err
    assert "OK -- post-extraction gate passed" not in captured.out


def test_a_raising_scan_degrades_to_an_advisory_and_never_gates(tmp_path, monkeypatch, capsys):
    def _boom(_manifest):  # noqa: ARG001
        raise RuntimeError("synthetic scan failure")

    monkeypatch.setattr(_sib.ve, "run_advisory_scans", _boom)
    code = _run_gate(tmp_path, monkeypatch, _baseline_manifest())
    captured = capsys.readouterr()
    assert code == 0, "a broken advisory must never refuse an otherwise clean gate"
    assert "scan unavailable" in captured.err
    assert "RuntimeError" in captured.err
