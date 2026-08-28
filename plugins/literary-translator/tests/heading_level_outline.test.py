"""Tests for the heading-level outline disclosure in
``scripts/validate_extraction.py`` (issue #233).

``assemble.py`` renders every heading block at ``heading_levels.get(raw_type,
2)`` -- a type absent from ``heading_levels`` silently collapses to a level-2
markdown heading, and nothing downstream re-derives it: ``validate_assembled.py``
proves heading KIND completeness only (#210), never LEVEL correctness. A book
whose outline was meant to nest HEAD > SIMAN > PEREK, but whose operator forgot
to declare a level for PEREK, ships an outline that is flat or mis-nested with
every gate green. This is a disclosure, not a gate (#233 stays OPEN): nothing in
a schema-valid manifest tells "the operator deliberately took the default" apart
from "the operator forgot this tier", so nothing here may fail a build.

What these tests are built to catch, in order of how easily each would
otherwise ship green:

  * the scan not being WIRED into ``run_advisory_scans()`` or the NOTE not
    being wired into ``main()`` -- ``test_wiring_*`` must go red;
  * the NOTE depending on the verdict -- it must print on BOTH a passing run
    and a run the mandatory checks FAIL, since it is a disclosure, not a
    gate result;
  * a heading type ``heading_types`` DECLARES but no segment CITES leaking
    into the NOTE/WARN -- only ``U`` (cited types kept to ``H``) may appear;
  * the WARN threshold (``len(U) >= 2 and at least one default``) firing or
    staying silent on the wrong side of either boundary, including the case
    two tiers are DELIBERATELY declared at the identical level (must stay
    silent -- that is not a forgotten tier);
  * a heading_levels key that is declared-but-uncited masquerading as
    evidence that a CITED tier was declared (it must not suppress the WARN
    for the tiers that actually are cited and defaulted);
  * ONE raising scan swallowing a sibling's finding, in EITHER direction --
    the same regression #504's per-scan isolation exists for;
  * a broken shared helper being reported TWICE (once as the WARN, once as
    the NOTE) -- the same de-duplication ``block_size_already_reported``
    does for the census, mirrored here;
  * the advisory or the NOTE changing the EXIT DECISION in either direction,
    on both a passing and a failing manifest;
  * a regression against the two shipped W2 heading fixtures in
    ``validate_extraction.test.py`` (#210) -- this feature must not touch
    them.

Every "must stay silent" assertion below sits in a file that also contains
"must fire" assertions built the same way, so deleting the feature (or its
wiring) cannot leave this file green: the firing tests would go red first.

Interpretation note (see the #233 CONTRACT's own "Exact output" section): the
contract's illustrative NOTE line shows ``"PEREK"=3 (default)``, but its
Definitions section is explicit that a defaulted tier is ALWAYS level 2
(mirroring ``assemble.py:2193``'s ``heading_levels.get(raw_type, 2)``). That
example line is read here as illustrative of the FORMAT only; the fixtures
below use levels for which "declared" and the ever-present default (2) do
not collide, and expected strings are derived from that fixed default, never
from the example's own numbers.
"""

import importlib.util
from pathlib import Path

import pytest


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
# harness. Taking the module under test FROM it (never a second importlib
# load of validate_extraction.py) is what keeps a monkeypatch visible to the
# main() that runs -- two loads produce two module objects and a patch on one
# is invisible to the other.
_sib = _load("validate_extraction_test", Path(__file__).parent / "validate_extraction.test.py")
_baseline_manifest = _sib._baseline_manifest
_run_gate = _sib._run_gate
ve = _sib.ve
assert ve.__file__ == str(SCRIPT_PATH), "the sibling must be testing this same script"


def _sha1(text):
    return _sib._sha1(text)


def _manifest_with_an_oversized_block():
    """A manifest that makes ``scan_block_size_outliers`` fire -- population
    over the screening minimum, one block far past the outlier threshold.
    Built locally rather than importing ``block_size_census.test.py``'s own
    ``_manifest_of_sizes``: that helper's file does its own raw ``_load`` of
    ``validate_extraction.test.py``, which would exec a SECOND copy of this
    suite's ``_sib`` (and so a second ``ve``) rather than reusing this one --
    exactly the trap the shared-``_sib`` pattern above exists to avoid."""
    m = _baseline_manifest()
    block_ids = list(m["segments"][0]["block_ids"])
    for index in range(1, 61):
        text = "x" * (17896 if index == 60 else 400)
        block_id = f"PARA:seg01:big:{index:04d}"
        m["blocks"][block_id] = {
            "id": block_id, "type": "PARA", "order_index": 100 + index,
            "source_file": "body.xhtml", "plain_text": text, "sha1": _sha1(text),
        }
        block_ids.append(block_id)
    m["segments"][0]["block_ids"] = block_ids
    return m


def _manifest_citing(cited_types, *, heading_types=None, heading_levels=None):
    """The schema-valid baseline (which already cites one HEAD block via
    ``seg01``) plus one extra schema-valid block per entry in ``cited_types``,
    appended to seg01's own ``block_ids`` so each one participates in ``U``
    exactly like a real heading block would.

    ``heading_types`` is written to the manifest verbatim -- pass a superset
    of ``cited_types`` to exercise a type that is DECLARED but never cited
    (Fixtures C and F below); leaving it ``None`` omits the key entirely.
    ``heading_levels`` is written verbatim too; leaving it ``None`` omits
    that key. Built by MUTATING the shipped baseline, never hand-rolled, so
    every fixture here is the same shape the gate actually accepts.

    No derivable check re-derives ``word_count``/``n_para`` against block
    content (only that a body segment's ``n_para + n_verse + n_quote`` is
    non-zero, and that ``word_count`` stays under ``max_segment_words`` --
    see ``run_derivable_checks``), so the baseline's own values are left
    untouched rather than recomputed here."""
    m = _baseline_manifest()
    block_ids = list(m["segments"][0]["block_ids"])
    for index, raw_type in enumerate(cited_types, start=1):
        text = f"{raw_type} heading text"
        block_id = f"{raw_type}:seg01:{index:04d}"
        m["blocks"][block_id] = {
            "id": block_id, "type": raw_type, "order_index": 10 + index,
            "source_file": "body.xhtml", "plain_text": text, "sha1": _sha1(text),
        }
        block_ids.append(block_id)
    m["segments"][0]["block_ids"] = block_ids
    if heading_types is not None:
        m["heading_types"] = heading_types
    if heading_levels is not None:
        m["heading_levels"] = heading_levels
    return m


def _manifest_citing_no_heading_block():
    """seg01 cites ONLY its own non-heading PARA block -- ``U`` is empty.
    The HEAD block stays defined in ``blocks{}`` (nothing requires every
    block to be cited; ``block_graph_integrity`` only rejects a dangling
    reference in the OTHER direction) but is no longer named by any
    segment, so it drops out of ``U`` by the Definitions section's own
    rule."""
    m = _baseline_manifest()
    m["segments"][0]["block_ids"] = ["PARA:seg01:0001"]
    return m


# ---------------------------------------------------------------------------
# heading_level_tiers(): the predicate -- sorting, the declared/default
# marker, and the empty case
# ---------------------------------------------------------------------------

def test_tiers_sorted_by_level_then_type_with_correct_declared_markers():
    # HEAD=1 (declared), PEREK=2 (default -- absent from heading_levels),
    # SIMAN=3 (declared). Chosen so the ever-present default (2) does NOT
    # collide with any declared level, and so sort order (level, type)
    # differs from insertion/heading_types order -- a wrong sort silently
    # reorders the NOTE and would not be caught by a same-order fixture.
    m = _manifest_citing(
        ["SIMAN", "PEREK"],
        heading_types=["SIMAN", "PEREK"],
        heading_levels={"HEAD": 1, "SIMAN": 3},
    )
    assert ve.heading_level_tiers(m) == [
        ("HEAD", 1, True),
        ("PEREK", 2, False),
        ("SIMAN", 3, True),
    ]


def test_tiers_on_a_manifest_citing_no_heading_block_is_empty():
    assert ve.heading_level_tiers(_manifest_citing_no_heading_block()) == []


@pytest.mark.parametrize("bad_level", [True, 0, 7, "3", 3.0, None])
def test_a_malformed_declared_level_reads_as_the_default_on_a_direct_call(bad_level):
    # UNREACHABLE through main(): manifest.schema.json pins heading_levels
    # values to integer 1..6 and the gate exits 1 on a schema error before any
    # advisory runs (the sibling suite's
    # test_heading_levels_value_rejected_by_schema owns that boundary). Pinned
    # anyway because _resolved_heading_level's type/range check exists for a
    # caller importing this module DIRECTLY -- this suite is one -- and an
    # unpinned guard is a guard the next reader can delete green. ``True`` is
    # the case that needs the explicit bool exclusion: ``isinstance(True, int)``
    # is ``True`` in Python, so a plain int-and-range check would render a
    # boolean as a DECLARED level 1.
    m = _manifest_citing(
        ["SIMAN"], heading_types=["SIMAN"], heading_levels={"SIMAN": bad_level},
    )
    assert ve.heading_level_tiers(m) == [("HEAD", 2, False), ("SIMAN", 2, False)]


def test_a_non_object_heading_levels_reads_as_every_tier_defaulted():
    # Same reachability story as above -- the schema pins heading_levels to an
    # object -- and the same reason to pin it: heading_level_tiers()'s
    # isinstance-dict coercion is otherwise deletable green.
    #
    # The fixture is a LIST CONTAINING a cited tier name, not an arbitrary
    # non-dict: ``"SIMAN" in [("SIMAN", 3)]`` is False, so that shape defaults
    # through _resolved_heading_level's membership test and would pass with the
    # coercion deleted. ``["SIMAN"]`` is the shape that actually needs it --
    # the membership test says True and the subscript then raises TypeError.
    m = _manifest_citing(["SIMAN"], heading_types=["SIMAN"], heading_levels=["SIMAN"])
    assert ve.heading_level_tiers(m) == [("HEAD", 2, False), ("SIMAN", 2, False)]


def test_format_heading_level_outline_returns_none_when_u_is_empty():
    assert ve.format_heading_level_outline(_manifest_citing_no_heading_block()) is None


# ---------------------------------------------------------------------------
# format_heading_level_outline(): the NOTE payload, printed on every
# schema-valid run -- and disclosure means it must not depend on the verdict
# ---------------------------------------------------------------------------

def test_note_on_a_clean_run_citing_one_heading_tier():
    m = _baseline_manifest()  # untouched: cites exactly HEAD, unmapped -> default
    assert ve.format_heading_level_outline(m) == (
        'heading_level_outline: 1 heading tier(s) cited: "HEAD"=2 (default)'
    )


def test_note_also_prints_on_a_run_the_mandatory_checks_fail(tmp_path, monkeypatch, capsys):
    # Disclosure, not a gate: the same #504 technique (reverse the spine so a
    # DERIVABLE check fails) proves the NOTE is unconditional on the verdict.
    m = _baseline_manifest()
    m["spine"] = list(reversed(m["spine"]))
    code = _run_gate(tmp_path, monkeypatch, m)
    captured = capsys.readouterr()
    assert code == 1
    assert f'NOTE {ve.HEADING_LEVEL_SCAN_NAME}: 1 heading tier(s) cited: "HEAD"=2 (default)' in captured.out


def test_note_lists_every_cited_tier_with_level_and_marker():
    m = _manifest_citing(
        ["SIMAN", "PEREK"],
        heading_types=["SIMAN", "PEREK"],
        heading_levels={"HEAD": 1, "SIMAN": 3},
    )
    assert ve.format_heading_level_outline(m) == (
        'heading_level_outline: 3 heading tier(s) cited: '
        '"HEAD"=1 (declared), "PEREK"=2 (default), "SIMAN"=3 (declared)'
    )


def test_note_omits_a_declared_but_uncited_heading_type():
    # PEREK is DECLARED (so heading_levels could legally name it) but no
    # block of that type is ever cited by any segment -- it must not reach
    # the NOTE the way a real cited-and-defaulted tier would.
    m = _manifest_citing(["SIMAN"], heading_types=["SIMAN", "PEREK"])
    note = ve.format_heading_level_outline(m)
    assert '"PEREK"' not in note
    assert '"HEAD"' in note and '"SIMAN"' in note
    assert note.startswith("heading_level_outline: 2 heading tier(s) cited: ")


# ---------------------------------------------------------------------------
# _heading_level_advisory(): the WARN -- exact firing/silent boundary
# ---------------------------------------------------------------------------

def test_warn_message_names_defaulted_then_declared_tiers():
    m = _manifest_citing(
        ["SIMAN", "PEREK"],
        heading_types=["SIMAN", "PEREK"],
        heading_levels={"HEAD": 1, "SIMAN": 3},
    )
    (name, detail), = ve._heading_level_advisory(m)
    assert name == ve.HEADING_LEVEL_SCAN_NAME
    assert (
        'this book cites 3 heading tier(s) and 1 of them has no level in '
        'manifest.heading_levels, so it renders at level 2 by default: "PEREK". '
        'Declared: "HEAD"=1, "SIMAN"=3.'
    ) in detail
    # The sibling advisories ship both of these sentences verbatim; the
    # contract requires this one keep them verbatim too.
    assert "This is a SCREEN, not a verdict: an outline you meant to be flat looks identical here." in detail
    assert "Nothing here changes whether this gate passes." in detail


def test_warn_fires_two_cited_tiers_no_heading_levels_at_all():
    m = _manifest_citing(["SIMAN"], heading_types=["SIMAN"])  # no heading_levels key
    (name, detail), = ve._heading_level_advisory(m)
    assert name == ve.HEADING_LEVEL_SCAN_NAME
    # 2 (plural) of them, so the shipped wording pluralises -- "have"/"they",
    # not the singular "has"/"it" the 1-defaulted fixtures above use.
    assert "cites 2 heading tier(s) and 2 of them have no level" in detail
    assert '"HEAD"' in detail and '"SIMAN"' in detail


def test_warn_fires_two_cited_tiers_map_covers_one_of_them():
    m = _manifest_citing(["SIMAN"], heading_types=["SIMAN"], heading_levels={"SIMAN": 4})
    (name, detail), = ve._heading_level_advisory(m)
    assert name == ve.HEADING_LEVEL_SCAN_NAME
    assert "cites 2 heading tier(s) and 1 of them has no level" in detail
    assert 'default: "HEAD"' in detail
    assert 'Declared: "SIMAN"=4' in detail


def test_warn_fires_when_maps_only_key_is_declared_but_uncited():
    # The shipped key-subset guard (heading_levels_keys_are_declared_heading_
    # types) accepts a heading_levels key iff it is a member of heading_types
    # -- so GHOST, declared but never cited, is a legal key. It must not read
    # as evidence that HEAD or SIMAN (the two tiers actually cited, and
    # actually defaulted) were declared.
    m = _manifest_citing(
        ["SIMAN"], heading_types=["SIMAN", "GHOST"], heading_levels={"GHOST": 4},
    )
    (name, detail), = ve._heading_level_advisory(m)
    assert name == ve.HEADING_LEVEL_SCAN_NAME
    assert "GHOST" not in detail, "an uncited declared type must not reach the WARN either"
    assert "cites 2 heading tier(s) and 2 of them have no level" in detail
    assert '"HEAD"' in detail and '"SIMAN"' in detail


def test_warn_silent_every_cited_tier_declared_including_a_tie_at_one_level():
    # SIMAN and PEREK are DELIBERATELY declared at the SAME level -- that is
    # an operator decision, not a forgotten tier, and must not warn.
    m = _manifest_citing(
        ["SIMAN", "PEREK"],
        heading_types=["SIMAN", "PEREK"],
        heading_levels={"HEAD": 1, "SIMAN": 2, "PEREK": 2},
    )
    assert ve._heading_level_advisory(m) == []


@pytest.mark.parametrize("heading_levels", [None, {"HEAD": 5}])
def test_warn_silent_one_cited_tier_only_mapped_or_not(heading_levels):
    m = _manifest_citing([], heading_types=None, heading_levels=heading_levels)
    assert ve._heading_level_advisory(m) == []


# ---------------------------------------------------------------------------
# Isolation: one raising scan must not swallow a sibling's finding, in
# either direction (the #504 regression this per-scan boundary exists for)
# ---------------------------------------------------------------------------

def _boom(_manifest):
    raise RuntimeError("synthetic scan failure")


def test_a_raising_heading_scan_leaves_the_block_size_advisory_reported(monkeypatch):
    m = _manifest_with_an_oversized_block()
    monkeypatch.setattr(ve, "_heading_level_advisory", _boom)
    names = dict(ve.run_advisory_scans(m))
    assert ve.BLOCK_SIZE_SCAN_NAME in names, (
        "a sibling's failure must not swallow a real finding -- a swallowed "
        "advisory is indistinguishable from a clean book"
    )
    assert "scan unavailable" in names[ve.HEADING_LEVEL_SCAN_NAME]


def test_a_raising_block_size_scan_leaves_the_heading_advisory_reported(monkeypatch):
    m = _manifest_citing(["SIMAN"], heading_types=["SIMAN"])  # WARN fires
    monkeypatch.setattr(ve, "scan_block_size_outliers", _boom)
    names = dict(ve.run_advisory_scans(m))
    assert ve.HEADING_LEVEL_SCAN_NAME in names
    assert "cites 2 heading tier(s)" in names[ve.HEADING_LEVEL_SCAN_NAME]
    assert "scan unavailable" in names[ve.BLOCK_SIZE_SCAN_NAME]


def test_heading_level_advisory_registered_after_block_size_in_run_advisory_scans():
    # No introspection point exposes registration order directly; drive it
    # the only observable way -- both scans firing on one manifest, and read
    # the order results were appended in.
    m = _manifest_with_an_oversized_block()
    m["heading_types"] = ["SIMAN"]
    m["blocks"]["SIMAN:seg01:0001"] = {
        "id": "SIMAN:seg01:0001", "type": "SIMAN", "order_index": 900,
        "source_file": "body.xhtml", "plain_text": "x", "sha1": _sha1("x"),
    }
    m["segments"][0]["block_ids"] = m["segments"][0]["block_ids"] + ["SIMAN:seg01:0001"]
    names = [name for name, _ in ve.run_advisory_scans(m)]
    assert names.index(ve.BLOCK_SIZE_SCAN_NAME) < names.index(ve.HEADING_LEVEL_SCAN_NAME)


def test_one_broken_shared_tiers_helper_produces_exactly_one_advisory(tmp_path, monkeypatch, capsys):
    # heading_level_tiers() backs BOTH the NOTE (format_heading_level_outline)
    # and the WARN (_heading_level_advisory, run BEFORE the NOTE is built).
    # Breaking it must be reported ONCE, not twice -- the same de-duplication
    # block_size_already_reported performs for the census, mirrored here.
    m = _manifest_citing(["SIMAN"], heading_types=["SIMAN"])
    monkeypatch.setattr(_sib.ve, "heading_level_tiers", _boom)
    code = _run_gate(tmp_path, monkeypatch, m)
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err.count(f"WARN {ve.HEADING_LEVEL_SCAN_NAME}:") == 1
    assert f"NOTE {ve.HEADING_LEVEL_SCAN_NAME}:" not in captured.out, (
        "a NOTE that could not be built must not print one claiming it was"
    )


def test_a_failing_heading_note_print_does_not_suppress_the_census_or_the_gate(tmp_path, monkeypatch, capsys):
    # The heading NOTE is printed inside its OWN try/except boundary, after
    # the census's -- mirrors the #504 "failed emission may not gate, and
    # may not take a sibling's report down with it" regression.
    real_print = print

    def _print(*args, **kwargs):
        if args and isinstance(args[0], str) and args[0].startswith(f"NOTE {ve.HEADING_LEVEL_SCAN_NAME}:"):
            raise OSError("synthetic stdout failure")
        return real_print(*args, **kwargs)

    monkeypatch.setattr("builtins.print", _print)
    code = _run_gate(tmp_path, monkeypatch, _baseline_manifest())
    monkeypatch.undo()
    captured = capsys.readouterr()
    assert code == 0, "a failed NOTE emission may not change the exit decision"
    assert f"NOTE {ve.BLOCK_SIZE_SCAN_NAME}:" in captured.out, (
        "the census printed BEFORE the heading NOTE must survive a failure in it"
    )


# ---------------------------------------------------------------------------
# Wiring: through the real main(), and through run_advisory_scans() directly
# ---------------------------------------------------------------------------

def test_wiring_run_advisory_scans_returns_the_named_entry_when_it_fires():
    m = _manifest_citing(["SIMAN"], heading_types=["SIMAN"])
    names = dict(ve.run_advisory_scans(m))
    assert ve.HEADING_LEVEL_SCAN_NAME in names
    assert "cites 2 heading tier(s)" in names[ve.HEADING_LEVEL_SCAN_NAME]


def test_wiring_heading_warn_and_note_appear_through_main(tmp_path, monkeypatch, capsys):
    m = _manifest_citing(["SIMAN"], heading_types=["SIMAN"])
    code = _run_gate(tmp_path, monkeypatch, m)
    captured = capsys.readouterr()
    assert code == 0, "an advisory must never refuse an ingestion"
    assert f"WARN {ve.HEADING_LEVEL_SCAN_NAME}:" in captured.err
    assert "ADVISORY" in captured.out
    assert f"NOTE {ve.HEADING_LEVEL_SCAN_NAME}:" in captured.out


def test_wiring_clean_single_tier_manifest_emits_the_note_but_no_warn(tmp_path, monkeypatch, capsys):
    code = _run_gate(tmp_path, monkeypatch, _baseline_manifest())
    captured = capsys.readouterr()
    assert code == 0
    assert f"WARN {ve.HEADING_LEVEL_SCAN_NAME}:" not in captured.err
    assert "ADVISORY" not in captured.out, "the NOTE must not move the advisory count"
    assert f'NOTE {ve.HEADING_LEVEL_SCAN_NAME}: 1 heading tier(s) cited: "HEAD"=2 (default)' in captured.out
    assert "OK -- post-extraction gate passed" in captured.out


# ---------------------------------------------------------------------------
# Exit-code independence: the (N ADVISORY) suffix moves with the WARN
# firing, the exit code moves only with the mandatory-check verdict
# ---------------------------------------------------------------------------

def test_exit_code_is_independent_of_whether_the_heading_warn_fires(tmp_path, monkeypatch, capsys):
    clean_manifest = _baseline_manifest()  # 1 tier -> WARN silent
    warning_manifest = _manifest_citing(["SIMAN"], heading_types=["SIMAN"])  # 2 tiers -> WARN fires
    failing_clean = _baseline_manifest()
    failing_clean["spine"] = list(reversed(failing_clean["spine"]))
    failing_warning = _manifest_citing(["SIMAN"], heading_types=["SIMAN"])
    failing_warning["spine"] = list(reversed(failing_warning["spine"]))

    code = _run_gate(tmp_path, monkeypatch, clean_manifest)
    out = capsys.readouterr()
    assert code == 0 and "ADVISORY" not in out.out

    code = _run_gate(tmp_path, monkeypatch, warning_manifest)
    out = capsys.readouterr()
    assert code == 0 and "(1 ADVISORY)" in out.out, (
        "a passing manifest's exit code must not depend on whether the "
        "heading WARN fires -- only the suffix naming it may move"
    )

    code = _run_gate(tmp_path, monkeypatch, failing_clean)
    out = capsys.readouterr()
    assert code == 1 and f"WARN {ve.HEADING_LEVEL_SCAN_NAME}:" not in out.err

    code = _run_gate(tmp_path, monkeypatch, failing_warning)
    out = capsys.readouterr()
    assert code == 1, (
        "a failing manifest's exit code must not depend on whether the "
        "heading WARN fires either -- it stays 1 with the WARN now present"
    )
    assert f"WARN {ve.HEADING_LEVEL_SCAN_NAME}:" in out.err
    assert "OK -- post-extraction gate passed" not in out.out


# ---------------------------------------------------------------------------
# Regression: the two shipped W2 heading fixtures (#210) must still exit 0
# ---------------------------------------------------------------------------

def test_regression_heading_levels_valid_map_still_exits_zero(tmp_path, monkeypatch):
    m = _sib._manifest_with_heading_levels({"HEAD": 3})
    assert _run_gate(tmp_path, monkeypatch, m) == 0


def test_regression_heading_levels_key_in_declared_heading_types_still_exits_zero(tmp_path, monkeypatch):
    m = _sib._manifest_with_heading_levels({"CHAPTER": 3})
    m["heading_types"] = ["CHAPTER"]
    assert _run_gate(tmp_path, monkeypatch, m) == 0
