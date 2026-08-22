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
