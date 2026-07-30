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

The fix keys the match on the span's own slice of the source text rather than
on the reconstructed-and-possibly-capped `name`. That is why the whitespace
controls below matter: `name` is a space-JOINED reconstruction of the run's
tokens, not a literal slice, so the two genuinely differ for a run containing a
double space or a newline. Measured across both: `fold_match_key` folds those
apart-at-the-byte-level forms to the SAME key, and the ONLY shape where the two
keys disagree is the over-cap one. The controls pin that, so a future change
that narrows matching cannot pass by fixing the over-cap case alone.
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
