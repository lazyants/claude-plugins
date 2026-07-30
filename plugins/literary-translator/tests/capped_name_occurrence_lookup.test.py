"""An over-cap candidate must stay findable by its OWN source form.

`bootstrap_names._capped_candidate_name()` bounds the `name` that
`extract_candidate_spans()` RETURNS, but the span it returns still covers the
full raw run. Three consumers group or filter those spans by
`fold_match_key(name)` -- the CAPPED string -- while their callers query by the
canon entry's original `source_form`. For any run longer than
`_MAX_CANDIDATE_NAME_CHARS` the two keys differ, so the occurrence is
unreachable from the only spelling a canon entry actually stores.

Why this is a MIGRATION defect and not a theoretical one: before the cap
existed there was no bound at all, so an over-long run WAS extractable and
adjudicable into `canon.json`. This release changes both
`cache_key.DERIVATION_BUNDLE_MEMBERS` (`bootstrap_names.py` and `segpack.py`),
which forces every existing project through regeneration -- so such an entry
loses its occurrences at exactly the moment everyone regenerates, silently.

The fix keys the match on the span's own text rather than on the
reconstructed-and-possibly-capped `name` -- specifically on the SENTINEL-MASKED
slice, built in one place, `bootstrap_names.span_match_keys()`. Both halves of
that are load-bearing, and each one is what the other's naive version breaks:

  * UNCAPPED, or an over-cap run is unreachable from the only spelling
    canon.json stores -- the defect this file was opened for.
  * SENTINEL-FREE, or a run that legitimately spans an inline sentinel becomes
    unreachable instead. Runs are built over `mask_sentinels(text)` while their
    offsets stay in the raw text, so `Marie ⟦FNREF_5⟧ Claire` is emitted as
    `Marie Claire` over the full span; folding the RAW slice yields
    `Marie FNREF Claire`. The first version of this fix used the raw slice and
    traded one unreachability for another, at all four sites at once.

That is also why the whitespace controls matter: `name` is a space-JOINED
reconstruction of the run's tokens, not a literal slice, so the two genuinely
differ for a run containing a double space or a newline. Measured over 36 spans
across 18 shapes, `fold_match_key` folds every such apart-at-the-byte-level
form to the SAME key as `name`, and the masked slice disagrees with `name` on
exactly one shape: the over-cap one, which is the disagreement being bought.
The controls pin that, so a later change that narrows matching cannot pass by
fixing the over-cap case alone.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"

for _script in ("bootstrap_names.py", "occ_index.py", "occurrence_targets.py",
                "evidence_verify.py", "canon_senses.py"):
    assert (SCRIPTS_DIR / _script).is_file(), f"{_script} not found at {SCRIPTS_DIR}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/occ_index.test.py's own loader: SCRIPTS_DIR must be on
    sys.path around the in-process load so a standalone script's own top-level
    ``from bootstrap_names import ...`` resolves exactly as it would under a
    real ``python3 <script>.py`` invocation.
    """
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


bn = _load_module("bootstrap_names_for_capped_lookup_test",
                  SCRIPTS_DIR / "bootstrap_names.py", SCRIPTS_DIR)
occ = _load_module("occ_index_for_capped_lookup_test",
                   SCRIPTS_DIR / "occ_index.py", SCRIPTS_DIR)
targets = _load_module("occurrence_targets_for_capped_lookup_test",
                       SCRIPTS_DIR / "occurrence_targets.py", SCRIPTS_DIR)
ev = _load_module("evidence_verify_for_capped_lookup_test",
                  SCRIPTS_DIR / "evidence_verify.py", SCRIPTS_DIR)
senses = _load_module("canon_senses_for_capped_lookup_test",
                      SCRIPTS_DIR / "canon_senses.py", SCRIPTS_DIR)


def make_lang(particles=(), stopwords=(), elision_pattern=None, has_elision=None,
              name_inventory=()):
    """Same shape as tests/bootstrap_names.test.py's and
    tests/occ_index.test.py's helpers, so fixtures stay comparable.
    """
    elision_re = re.compile(elision_pattern) if elision_pattern else None
    if has_elision is None:
        has_elision = elision_re is not None
    return bn.LanguageConfig(
        path=Path("<test-fixture>"),
        particles=frozenset(p.lower() for p in particles),
        stopwords=frozenset(stopwords),
        elision_re=elision_re,
        has_elision=has_elision,
        raw_bytes=b"{}",
        name_inventory=frozenset(name_inventory),
    )


# The run itself is one token long so that the ONLY reason `name` and the raw
# slice can differ is the cap -- no space-joining is involved.
OVER_CAP_FORM = "A" * (bn._MAX_CANDIDATE_NAME_CHARS + 50)
PREFIX = "Il vit "
BLOCK = PREFIX + OVER_CAP_FORM + " parla."
SPAN = (len(PREFIX), len(PREFIX) + len(OVER_CAP_FORM))


def _sanity_the_fixture_is_actually_over_cap():
    """The whole file is vacuous if the fixture stops tripping the cap (say the
    constant is raised). Assert the premise rather than trusting it.
    """
    assert len(OVER_CAP_FORM) > bn._MAX_CANDIDATE_NAME_CHARS
    lang = make_lang()
    emitted = [n for n, _mid, _s, _e in bn.extract_candidate_spans(BLOCK, lang)]
    capped = [n for n in emitted if bn._CAPPED_NAME_MARKER_RE.search(n)]
    assert len(capped) == 1, f"fixture no longer produces exactly one capped name: {emitted}"
    return lang


# ---------------------------------------------------------------------------
# The span itself was never the problem -- pin that, so a regression here is
# not misread as a regression in the lookup.
# ---------------------------------------------------------------------------

def test_the_span_covers_the_full_original_run():
    lang = _sanity_the_fixture_is_actually_over_cap()
    spans = [(s, e) for _n, _mid, s, e in bn.extract_candidate_spans(BLOCK, lang)
             if e - s == len(OVER_CAP_FORM)]
    assert spans == [SPAN], spans
    assert BLOCK[SPAN[0]:SPAN[1]] == OVER_CAP_FORM


# ---------------------------------------------------------------------------
# Consumer 1 -- occ_index.production_occurrences()
# ---------------------------------------------------------------------------

def test_occ_index_finds_an_over_cap_form_by_its_own_source_form():
    lang = _sanity_the_fixture_is_actually_over_cap()
    assert occ.production_occurrences(OVER_CAP_FORM, BLOCK, lang) == [SPAN]


def test_index_manifest_emits_records_for_an_over_cap_form(tmp_path):
    """The BATCH path, not just the single-form one. `index_manifest()` keys the
    block's spans by fold key and then INTERSECTS those keys with a map built
    from the caller's `source_forms`. Keyed by the capped name, an over-cap form
    never intersects and this function emits zero records for it in silence --
    a fourth site of the same class, in the same file as the first, and named by
    neither the reviewer finding nor the issue that tracked it.
    """
    lang = _sanity_the_fixture_is_actually_over_cap()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"blocks": {
        "PARA:seg01:0001": {
            "id": "PARA:seg01:0001", "type": "PARA", "seg": "seg01",
            "order_index": 0, "source_file": "x.txt",
            "plain_text": BLOCK, "sha1": "deadbeef",
        },
    }}), encoding="utf-8")
    records = occ.index_manifest(manifest_path, [OVER_CAP_FORM], lang)
    assert len(records) == 1, records
    assert (records[0]["char_start"], records[0]["char_end"]) == SPAN
    assert records[0]["source_form"] == OVER_CAP_FORM


def test_occ_index_does_not_require_the_synthetic_capped_key():
    """The capped key is an artifact of bounding the RETURNED string. A canon
    entry never stores it, so matching on it is matching on nothing a caller
    can produce -- assert the real form works, and record what the synthetic
    one does rather than depending on it either way.
    """
    lang = _sanity_the_fixture_is_actually_over_cap()
    capped = bn._capped_candidate_name(OVER_CAP_FORM)
    assert capped != OVER_CAP_FORM
    assert occ.production_occurrences(OVER_CAP_FORM, BLOCK, lang), (
        "the ORIGINAL source form must resolve; that is the only spelling canon.json holds"
    )


# ---------------------------------------------------------------------------
# Consumer 2 -- occurrence_targets._spans_by_name()
# ---------------------------------------------------------------------------

def test_occurrence_targets_groups_an_over_cap_form_under_its_own_fold_key():
    lang = _sanity_the_fixture_is_actually_over_cap()
    grouped = targets._spans_by_name(BLOCK, lang)
    assert grouped.get(bn.fold_match_key(OVER_CAP_FORM)) == [SPAN], sorted(grouped)


# ---------------------------------------------------------------------------
# Consumer 3 -- evidence_verify._group_production_spans_by_name()
# ---------------------------------------------------------------------------

def test_evidence_verify_groups_an_over_cap_form_under_its_own_source_form():
    lang = _sanity_the_fixture_is_actually_over_cap()
    competitors = senses.fold_collision_map([OVER_CAP_FORM])
    grouped = ev._group_production_spans_by_name(BLOCK, lang, competitors)
    assert grouped.get(OVER_CAP_FORM) == [SPAN], sorted(grouped)


# ---------------------------------------------------------------------------
# Controls -- the fix must not narrow matching for the shapes where `name` and
# the raw slice differ for reasons OTHER than the cap. Measured: both fold to
# the same key today, and the over-cap shape is the only one that does not.
# ---------------------------------------------------------------------------

def test_a_double_space_between_tokens_still_matches_the_single_spaced_form():
    lang = make_lang()
    text = "Il vit Marie  Claire parla."
    found = occ.production_occurrences("Marie Claire", text, lang)
    assert found, "a space-normalized source_form must still find its double-spaced run"
    start, end = found[0]
    assert text[start:end] == "Marie  Claire"


def test_a_newline_between_tokens_still_matches_the_single_spaced_form():
    lang = make_lang()
    text = "Il vit Marie\nClaire parla."
    found = occ.production_occurrences("Marie Claire", text, lang)
    assert found, "a space-normalized source_form must still find its newline-split run"
    start, end = found[0]
    assert text[start:end] == "Marie\nClaire"


def test_an_ordinary_under_cap_name_is_unaffected():
    lang = make_lang()
    text = "Il vit Marie Claire parla."
    assert occ.production_occurrences("Marie Claire", text, lang) == [(7, 19)]
    assert text[7:19] == "Marie Claire"


# ---------------------------------------------------------------------------
# The OTHER direction of the same identity question -- and the one the first
# version of this fix got wrong.
#
# Runs are built over `mask_sentinels(text)` while their offsets stay in the
# RAW `text`, so a run may legitimately SPAN an inline `⟦...⟧` sentinel: the
# extractor emits `Marie Claire` over a span whose raw slice reads
# `Marie ⟦FNREF_5⟧ Claire`. Keying on that raw slice folds the sentinel's own
# letters into the key (`Marie FNREF Claire`) and the occurrence stops being
# reachable from its canon form -- the same unreachability the cap fix above
# exists to remove, reintroduced by the fix itself.
#
# So the key is folded from the MASKED slice: uncapped (an over-cap run keeps
# its identity) and sentinel-free (an interrupted run keeps its identity).
# Both halves are pinned here, including together in one fixture.
# ---------------------------------------------------------------------------

SENTINEL_BLOCK = "Il vit Marie ⟦FNREF_5⟧ Claire parla."
SENTINEL_SPAN = (7, 29)


def _sanity_the_run_really_spans_the_sentinel():
    """Vacuous unless the extractor actually emits ONE run across the
    sentinel. If tokenization ever stops joining across a masked sentinel
    the premise is gone, and every assertion below would pass for the wrong
    reason.
    """
    lang = make_lang()
    emitted = [(n, s, e) for n, _mid, s, e in bn.extract_candidate_spans(SENTINEL_BLOCK, lang)]
    spanning = [(n, s, e) for n, s, e in emitted if (s, e) == SENTINEL_SPAN]
    assert len(spanning) == 1, f"fixture no longer yields one sentinel-spanning run: {emitted}"
    name, start, end = spanning[0]
    assert name == "Marie Claire", name
    assert SENTINEL_BLOCK[start:end] == "Marie ⟦FNREF_5⟧ Claire"
    assert "⟦" not in name, "the emitted name is sentinel-free; only the raw slice is not"
    return lang


def test_occ_index_finds_a_sentinel_interrupted_run_by_its_canon_form():
    lang = _sanity_the_run_really_spans_the_sentinel()
    assert occ.production_occurrences("Marie Claire", SENTINEL_BLOCK, lang) == [SENTINEL_SPAN]


def test_index_manifest_emits_records_for_a_sentinel_interrupted_run(tmp_path):
    lang = _sanity_the_run_really_spans_the_sentinel()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"blocks": {
        "PARA:seg01:0001": {
            "id": "PARA:seg01:0001", "type": "PARA", "seg": "seg01",
            "order_index": 0, "source_file": "x.txt",
            "plain_text": SENTINEL_BLOCK, "sha1": "deadbeef",
        },
    }}), encoding="utf-8")
    records = occ.index_manifest(manifest_path, ["Marie Claire"], lang)
    assert len(records) == 1, records
    assert (records[0]["char_start"], records[0]["char_end"]) == SENTINEL_SPAN
    assert records[0]["source_form"] == "Marie Claire"


def test_occurrence_targets_groups_a_sentinel_interrupted_run_by_its_canon_form():
    lang = _sanity_the_run_really_spans_the_sentinel()
    grouped = targets._spans_by_name(SENTINEL_BLOCK, lang)
    assert grouped.get(bn.fold_match_key("Marie Claire")) == [SENTINEL_SPAN], sorted(grouped)


def test_evidence_verify_groups_a_sentinel_interrupted_run_by_its_canon_form():
    lang = _sanity_the_run_really_spans_the_sentinel()
    competitors = senses.fold_collision_map(["Marie Claire"])
    grouped = ev._group_production_spans_by_name(SENTINEL_BLOCK, lang, competitors)
    assert grouped.get("Marie Claire") == [SENTINEL_SPAN], sorted(grouped)


def test_a_run_that_is_BOTH_over_cap_and_sentinel_interrupted_stays_reachable():
    """The two defects compose: the run is longer than the cap AND crosses a
    sentinel, so the emitted `name` is neither the raw slice nor a masked one.
    Only a key that is both uncapped and sentinel-free finds it.
    """
    lang = make_lang()
    prefix = "Il vit "
    surface = OVER_CAP_FORM + " ⟦FNREF_5⟧ Claire"
    block = prefix + surface + " parla."
    span = (len(prefix), len(prefix) + len(surface))
    assert block[span[0]:span[1]] == surface
    canon_form = OVER_CAP_FORM + " Claire"
    assert occ.production_occurrences(canon_form, block, lang) == [span]


# The sentinel bug was NOT specific to the upper-initial route: it reproduced
# on every route the extractor has, because all four build runs over the same
# masked token stream. One case each, so a future route-specific regression
# cannot hide behind the plain-French fixture above.

def test_sentinel_interrupted_runs_stay_reachable_on_the_elision_route():
    lang = make_lang(elision_pattern=r"^([dl])['’]([A-ZÀÂÄÆÇÉÈÊËÎÏÔŒÖÙÛÜŸ].*)$")
    text = "Il vit d'Artagnan ⟦FNREF_5⟧ Dupont parla."
    found = occ.production_occurrences("Artagnan Dupont", text, lang)
    assert found, "elision route: a sentinel-interrupted run must stay reachable"
    start, end = found[0]
    assert text[start:end] == "Artagnan ⟦FNREF_5⟧ Dupont"


def test_sentinel_interrupted_runs_stay_reachable_on_the_caseless_inventory_route():
    lang = make_lang(name_inventory=("Marie Claire",))
    text = "Il vit MARIE ⟦FNREF_5⟧ CLAIRE parla."
    found = occ.production_occurrences("MARIE CLAIRE", text, lang)
    assert found, "inventory route: a sentinel-interrupted run must stay reachable"
    start, end = found[0]
    assert text[start:end] == "MARIE ⟦FNREF_5⟧ CLAIRE"


def test_sentinel_interrupted_runs_stay_reachable_on_the_hebrew_maqaf_route():
    """Hebrew has no uppercase, so it reaches the extractor only via the
    inventory route -- and `fold_match_key` folds maqaf and space together
    (#238/#241), which is the fold this key construction has to preserve.
    """
    lang = make_lang(name_inventory=("משה־לייב",))
    text = "והוא ראה משה־⟦FNREF_5⟧לייב אמר."
    found = occ.production_occurrences("משה לייב", text, lang)
    assert found, "hebrew maqaf route: a sentinel-interrupted run must stay reachable"
    start, end = found[0]
    assert text[start:end] == "משה־⟦FNREF_5⟧לייב"
