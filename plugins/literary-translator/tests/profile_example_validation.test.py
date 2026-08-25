"""tests/profile_example_validation.test.py

Targets ``assets/profile.example.yml`` + ``profile_validate.py``, split into
exactly THREE cases (per the plugin's own test enumeration):

  1. A missing ``profile.yml`` triggers Step 0's auto-copy path -- the
     shipped example is copied to the target path verbatim, and that SAME
     run goes on to print the whole intake questionnaire before halting
     non-zero (#751). It used to halt on the spot and tell the reader to
     fill in every placeholder and re-run, which left the questions
     unrelayed while a sentinel-laden profile sat on disk.
  2. The shipped example loaded VERBATIM (placeholders intact) is FATALLY
     rejected. See the NOTE below on why this is checked at *two* levels
     (the real CLI entry point, and ``scan_placeholders()`` directly).
  3. A fixture with every placeholder replaced by a real value, otherwise
     structurally IDENTICAL to the shipped example -- including the
     currently-INACTIVE ``adapter_config.plain_text`` sub-block's
     ``blank_line_threshold: 2`` -- passes Step 0 cleanly. This
     regression-locks the per-field-typed "inactive format sub-block" schema
     loosening: ``adapter_config.plain_text`` keeps its own fields' base
     types (``blank_line_threshold`` stays ``integer|null``, non-null is
     fine) even while ``source.format`` is NOT ``plain_text`` -- only the
     format-specific if/then rules (e.g. "if method==blank_line_run then
     blank_line_threshold is REQUIRED to be a non-null integer") are gated
     off, never the field's own type.

NOTE on case 2's architecture (#727 -- superseding the pre-#727 note this
replaces, see git history for the earlier "only glossary.research_mode is
unconditionally schema-enforced" reasoning, which #727 made moot by
reordering Step 0 itself): Step 0's order is now existence -> dependency
preflight -> parse/profile_version -> unknown-top-level-keys -> **the
placeholder scan (step 5), which now runs BEFORE whole-file jsonschema
validation (step 6)** and exits 1 on its own the instant any placeholder or
``CHOOSE_``-sentinel survives -- schema validation and the procedural checks
never run at all on that exit path. This is precisely #727's fix: before it,
a fresh copy of the shipped example only ever reported the ONE
placeholder-bearing field profile.schema.json happens to restrict
unconditionally (``glossary.research_mode``), because the OLD numbering's
jsonschema validation (then step 5) halted the whole run before the OLD
numbering's placeholder scan (then step 7) ever got a turn -- every other
shipped placeholder (the two ``/ABS/PATH/TO/...`` paths, the book-title
placeholder, and the remaining ``CHOOSE_``-sentinels, several of which sit
behind a format-gated conditional or were previously not sentinels at all)
went unmentioned until the operator fixed that one field and reran, only to
be told about the next single field, and so on one at a time. Moving the
placeholder scan ahead of schema validation is what makes a single CLI
invocation against the verbatim shipped example enumerate EVERY surviving
sentinel and placeholder in one run, which
``test_verbatim_shipped_example_is_fatally_rejected_by_cli`` now asserts
directly against the real CLI entry point (not just against
``scan_placeholders()`` in isolation, which
``test_verbatim_shipped_example_scan_placeholders_names_every_placeholder``
below still separately exercises as the actual mechanism responsible for the
guarantee).
"""
import importlib.util
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPT_PATH = ASSETS_DIR / "scripts" / "profile_validate.py"
EXAMPLE_PATH = ASSETS_DIR / "profile.example.yml"

# Every CHOOSE_-prefixed sentinel assets/profile.example.yml actually ships,
# named once here rather than transcribed separately into each of the two
# case-2 tests below (the CLI-level one and the scan_placeholders()-level
# one) -- two hand-maintained copies of the same list drift apart silently,
# since both tests only assert PRESENCE and so stay green when the example
# grows a further sentinel neither list names. The authoritative, both-ways
# drift guard against profile_validate.py's own KNOB_QUESTIONS lives in
# tests/profile_validate.test.py
# (test_knob_questions_matches_shipped_sentinels_set_equality).
SHIPPED_CHOOSE_SENTINELS = (
    "CHOOSE_none_confirmed_or_regex",
    "CHOOSE_none_confirmed_or_markdown_ref_or_custom_regex",
    "CHOOSE_true_or_false",
    "CHOOSE_live_or_offline",
    "CHOOSE_translate_all_or_preserve_source_or_omit_apparatus_or_body_refs_only",
    "CHOOSE_segment_drafts_and_audit_or_assembled_book",
    "CHOOSE_obsidian_or_epub_or_custom",
    # #730. verse_policy.mode shipped as an already-decided value until this
    # release, so the six-value enum never reached the user as a question.
    "CHOOSE_full_rhymed_plus_literal_or_full_rhymed_only_or_rhythmic_approximation"
    "_or_mixed_by_length_or_literal_only_or_skip",
)


def _load_module(name, path):
    """One importlib shim for every shipped script this file drives --
    ``assets/scripts/`` is not a package on sys.path, so a plain ``import``
    won't reach it."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_profile_validate():
    """Imports profile_validate.py fresh from its real, shipped install
    path -- ``assets/scripts/`` is not a package on sys.path, so a plain
    ``import`` won't reach it. A fresh module per call (see the ``pv``
    fixture below) avoids any cross-test state leakage through the
    module-level ``yaml``/``jsonschema`` handles ``dependency_preflight()``
    populates."""
    return _load_module("profile_validate_under_test", SCRIPT_PATH)


@pytest.fixture()
def pv():
    assert SCRIPT_PATH.is_file(), f"expected profile_validate.py at {SCRIPT_PATH}"
    assert EXAMPLE_PATH.is_file(), f"expected profile.example.yml at {EXAMPLE_PATH}"
    return _load_profile_validate()


def _run_main(pv_module, profile_path: Path, capsys):
    """Invokes the real CLI entry point exactly as the plugin does
    (``profile_validate.py --profile <path>``) and returns
    ``(exit_code, stdout, stderr)``. ``main()`` always ends in
    ``sys.exit()``, never a bare return, so every call site must catch
    ``SystemExit``."""
    with pytest.raises(SystemExit) as exc_info:
        pv_module.main(["--profile", str(profile_path)])
    captured = capsys.readouterr()
    return exc_info.value.code, captured.out, captured.err


def _build_filled_profile(durable_root: Path, source_path: Path) -> dict:
    """Builds a profile dict that is structurally IDENTICAL to
    ``assets/profile.example.yml`` (same keys, same nesting, same shape),
    with every shipped placeholder replaced by a real, valid value --
    EXCEPT ``adapter_config.plain_text.segmentation.blank_line_threshold``,
    which was already a real value (``2``) in the shipped example, not a
    placeholder, and is kept exactly as-is per the spec's explicit call-out
    (this is the field that regression-locks the inactive-format schema
    loosening: ``plain_text`` sits inert because ``source.format`` is
    ``gutenberg_epub`` here, yet its own ``blank_line_threshold`` field still
    validates as a plain, correctly-typed integer)."""
    return {
        "profile_version": 1,
        "project": {
            "title": "Les Historiettes de Tallemant des Reaux, tome 3",
            "durable_root": str(durable_root),
            "durable_root_adopt_existing": False,
            "pipeline_version": "v1",
            "max_segment_words": 15000,
        },
        "source": {
            "format": "gutenberg_epub",
            "path": str(source_path),
            "gutenberg_id": None,
            "language": {
                "code": "fr",
                "particle_config": "fr.json",
                "smoke_test": {"report_path": None},
            },
            "adapter_config": {
                "gutenberg_epub": {
                    "spine_overrides": {},
                    "frontback_overrides": {},
                },
                "plain_text": {
                    "segmentation": {
                        "method": "blank_line_run",
                        "blank_line_threshold": 2,
                        "heading_regex": None,
                    },
                    "verse_detection": "none_confirmed",
                    "verse_regex": None,
                    "footnotes": "none_confirmed",
                    "footnote_anchor_regex": None,
                    "footnote_def_regex": None,
                },
                "custom": {"extractor_path": None},
            },
        },
        "target": {
            "language": {
                "code": "ru",
                "register_notes": "ty/vy politeness distinction -- see style_bible.md section B",
            },
        },
        "verse_policy": {
            "mode": "full_rhymed_plus_literal",
            "threshold_lines": None,
        },
        "engine": {
            "effort": "high",
            "max_fix_rounds": 4,
            "batch_agent_cap": 3500,
        },
        "footnotes": {"apparatus_policy": "translate_all"},
        # #727: glossary.enabled and the three fields below were previously
        # shipped as real, already-decided values in profile.example.yml and
        # needed no filling-in here at all -- they are transcribed here now
        # only because #727 turned them into CHOOSE_-sentinels, so this
        # fixture's claim of "structurally identical to the shipped example,
        # every placeholder replaced" would otherwise silently go false the
        # moment the example shipped one more sentinel. (verse_policy.mode
        # below became one in #730 and was already spelled out here, so that
        # release cost this builder no edit -- which is the point.)
        "glossary": {"enabled": True, "research_mode": "offline"},
        "validation": {
            "untranslated_sentinel": "нет перевода",
            # #533. Carried here because this helper's own docstring claims
            # structural identity with the shipped example, and the example
            # now ships this key explicitly at its default. Leaving it out
            # would keep the SUITE green -- the property is optional -- while
            # making that claim quietly false, which is the failure this
            # helper exists to make impossible.
            "admit_contract_only_stale": False,
            # #520 and #199, for the same reason the key above carries its own
            # note: this helper's docstring claims structural identity with the
            # shipped example, and the example ships both of these explicitly at
            # their empty default. Omitting either keeps the SUITE green -- both
            # properties are optional -- while making that claim quietly false,
            # which is the failure this helper exists to make impossible.
            # `forbidden_patterns` was missing here from 1.67.0 until #199
            # added its neighbour and the gap became visible.
            "forbidden_patterns": [],
            "terms": [],
        },
        "output": {
            "v1_scope": "segment_drafts_and_audit",
            "target": "obsidian",
            "destination": str(durable_root / "out"),
        },
    }


# ---------------------------------------------------------------------------
# Case 1: missing profile.yml -> Step 0 auto-copies the shipped example
# verbatim to the target path and then CONTINUES into the same run's
# placeholder scan, so the one invocation that creates the starter profile is
# also the one that prints the intake questionnaire (#751).
# ---------------------------------------------------------------------------

def test_missing_profile_triggers_autocopy_and_halt(pv, tmp_path, capsys):
    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    assert not profile_path.exists()

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "a freshly auto-copied profile must halt, not proceed"
    assert profile_path.exists(), "Step 0 must auto-copy the shipped example on absence"
    assert profile_path.read_bytes() == EXAMPLE_PATH.read_bytes(), (
        "the auto-copied profile must be byte-identical to assets/profile.example.yml"
    )
    assert str(profile_path) in err, err
    assert "placeholder" in err.lower(), err


def test_missing_profile_prints_the_whole_questionnaire_in_that_same_run(pv, tmp_path, capsys):
    """#751. The defect this closes is a SEQUENCE, not a missing check.

    Before this, the absence branch copied the example and exited straight
    away, telling the reader to "fill in every placeholder ... then re-run
    Step 0" -- so the questionnaire only ever existed on a LATER invocation.
    An orchestrator obeying that message opens the fresh copy, whose own
    inline comments say "consciously replace", fills the sentinels from
    them, and re-runs against a file that no longer has a single question
    left in it. Validation prints OK and the user was asked nothing.

    Closing that means there must be NO window between the file acquiring
    sentinels and the questions being on screen: the one run that creates
    the profile must also print them. Asserted here at the real CLI entry
    point, against a genuinely ABSENT profile -- which is what makes this
    different from ``test_verbatim_shipped_example_is_fatally_rejected_by_cli``
    above, which hands the validator a profile that already exists and so
    exercises the ``exists() -> True`` branch instead.

    Every sentinel is asserted, not merely one: "prints the questionnaire"
    is a claim about the WHOLE set of open decisions, and a run that named
    only the first would satisfy a weaker check while leaving the operator
    to discover the rest one re-run at a time -- the very thing #727 fixed
    for the already-exists branch and this extends to the creating one."""
    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    assert not profile_path.exists()

    exit_code, out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "the run that creates the starter profile must still halt"
    assert profile_path.exists(), "Step 0 must have created the starter profile"

    # The relay instruction: the questions are useless if they stop at the
    # orchestrator, so the header naming its own audience is pinned too.
    assert "Step 0 needs these intake decisions answered" in err, (
        "the FIRST run against an absent profile must print the questionnaire "
        f"header, not defer it to a re-run:\n{err}"
    )
    assert "fill in their answers" in err, (
        f"the questionnaire header must instruct relaying it to the user:\n{err}"
    )

    # ...and every open decision, in that one run -- same expectation the
    # already-exists branch is held to at case 2 below.
    for sentinel in SHIPPED_CHOOSE_SENTINELS:
        assert sentinel in err, (
            f"expected sentinel {sentinel!r} named in the creating run; got:\n{err}"
        )
    # The dotted paths come from KNOB_QUESTIONS rather than a hand-typed list:
    # this module's own comment says a restated name list here "has now gone
    # stale twice, once per release that added one", and a stale list stays
    # GREEN while quietly asserting less. Not circular -- the stderr lines are
    # produced by _scan_choose_sentinels() walking the parsed document, and
    # KNOB_QUESTIONS only supplies the appended question text. Its key set is
    # held equal to the shipped example's sentinel-bearing paths, both ways, by
    # profile_validate.test.py's own two-way drift guard.
    for field in sorted(pv.KNOB_QUESTIONS):
        assert field in err, (
            f"expected field {field!r} named in the creating run; got:\n{err}"
        )

    # The creation notice itself must not send the reader to the file's own
    # comments for the answers -- that route reaches a valid profile with
    # every decision made by the wrong party.
    assert "never answer them from this file's own inline comments" in err, (
        f"the creation notice must forbid answering from the example's comments:\n{err}"
    )

    assert out == "", f"Step 0 reports on stderr only; got stdout:\n{out}"


def test_missing_profile_autocopy_does_not_touch_an_existing_profile(pv, tmp_path, capsys):
    """Companion sanity check for case 1's own guard: an EXISTING profile.yml
    (however malformed its content) must never be silently overwritten by the
    auto-copy branch -- existence is checked fresh, not "does it look
    filled-in". This is what makes the halt on a genuinely-absent file safe
    to rely on elsewhere (e.g. scaffold_idempotency.test.py)."""
    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    profile_path.parent.mkdir(parents=True)
    sentinel_content = b"not a real profile, just a sentinel the auto-copy must not clobber\n"
    profile_path.write_bytes(sentinel_content)

    # Whatever happens next (this content will fail YAML/schema validation
    # further down the pipeline), the auto-copy branch itself must not fire.
    with pytest.raises(SystemExit):
        pv.main(["--profile", str(profile_path)])

    assert profile_path.read_bytes() == sentinel_content


# ---------------------------------------------------------------------------
# Case 2: the shipped example loaded VERBATIM (placeholders intact) is
# fatally rejected. See the module docstring's NOTE for why this is split
# into a CLI-level assertion and a scan_placeholders()-level assertion.
# ---------------------------------------------------------------------------

def test_verbatim_shipped_example_is_fatally_rejected_by_cli(pv, tmp_path, capsys):
    """#727's actual fix, asserted at the real CLI entry point: a single
    invocation against the verbatim shipped example -- whose surviving
    CHOOSE_-sentinels span source.adapter_config.plain_text (verse_detection,
    footnotes), glossary (enabled, research_mode), footnotes
    (apparatus_policy), output (v1_scope, target) and verse_policy (mode,
    #730) -- must name EVERY one of them in that ONE run, not just the single
    field schema.py happens to restrict unconditionally. Before #727, Step 5
    (whole-file jsonschema validation) ran before Step 7 (scan_placeholders)
    and halted the whole run on the first schema-level violation it hit
    (glossary.research_mode's unconditional enum), so an operator only ever
    learned about one missing decision per re-run. #727 moves the placeholder
    scan ahead of schema validation specifically so this no longer happens."""
    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_bytes(EXAMPLE_PATH.read_bytes())

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "the verbatim shipped example must never pass Step 0"
    assert "Step 0 needs these intake decisions answered" in err, (
        "the questionnaire header must be printed once the placeholder scan "
        f"finds any surviving sentinel:\n{err}"
    )
    for sentinel in SHIPPED_CHOOSE_SENTINELS:
        assert sentinel in err, f"expected sentinel {sentinel!r} named in ONE run; got:\n{err}"
    for field in (
        "source.adapter_config.plain_text.verse_detection",
        "source.adapter_config.plain_text.footnotes",
        "glossary.enabled",
        "glossary.research_mode",
        "footnotes.apparatus_policy",
        "output.v1_scope",
        "output.target",
        "verse_policy.mode",
    ):
        assert field in err, f"expected field {field!r} named in ONE run; got:\n{err}"


def test_verbatim_shipped_example_scan_placeholders_names_every_placeholder(pv):
    """Exercises scan_placeholders() -- the actual Step 5 mechanism behind
    the "names every placeholder" guarantee -- directly against the
    verbatim, unmodified shipped example. This is what fires in full once a
    user has fixed the one schema-blocking field (glossary.research_mode)
    and re-runs Step 0; regression-locks that the mechanism itself still
    correctly names EVERY remaining placeholder in one pass, not just the
    first one it encounters."""
    profile = yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
    errors = pv.scan_placeholders(profile)
    joined = "\n".join(errors)

    # Every literal placeholder substring the module itself declares...
    for placeholder in pv.PLACEHOLDER_SUBSTRINGS:
        assert placeholder in joined, (
            f"expected placeholder {placeholder!r} to be named; got:\n{joined}"
        )
    # ...plus every CHOOSE_-prefixed sentinel actually shipped in the example.
    for sentinel in SHIPPED_CHOOSE_SENTINELS:
        assert sentinel in joined, f"expected sentinel {sentinel!r} to be named; got:\n{joined}"

    # ...and each violation is attributed to its own field, by dotted path.
    for field in (
        "project.title",
        "project.durable_root",
        "source.path",
        "source.adapter_config.plain_text.verse_detection",
        "source.adapter_config.plain_text.footnotes",
        "glossary.enabled",
        "glossary.research_mode",
        "footnotes.apparatus_policy",
        "output.v1_scope",
        "output.target",
        "verse_policy.mode",
        "output.destination",
    ):
        assert any(err.startswith(f"{field}:") for err in errors), (
            f"expected an error attributed to {field!r}; got:\n{joined}"
        )


# ---------------------------------------------------------------------------
# Case 3: every placeholder replaced with a real value, otherwise
# structurally identical (including the inactive plain_text sub-block's
# blank_line_threshold: 2) -> clean pass.
# ---------------------------------------------------------------------------

def test_fully_filled_fixture_structurally_identical_passes_cleanly(pv, tmp_path, monkeypatch, capsys):
    # pytest's own tmp_path resolves under a literal `tmp` path component
    # on Linux (e.g. CI runners honoring `TMPDIR=/tmp`), which
    # check_durable_root would otherwise reject -- this fixture's
    # durable_root is genuinely under tmp_path, so accept it explicitly
    # (see profile_validate.py's `check_durable_root`).
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    durable_root = tmp_path / "book-project"
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"fake epub bytes for the fixture")

    profile_data = _build_filled_profile(durable_root, source_path)
    # Sanity: the field this case exists to regression-lock is genuinely
    # present and unchanged from the shipped example's own value.
    assert (
        profile_data["source"]["adapter_config"]["plain_text"]["segmentation"]["blank_line_threshold"] == 2
    )
    assert profile_data["source"]["format"] == "gutenberg_epub", (
        "plain_text must stay the INACTIVE sub-block for this regression lock to mean anything"
    )

    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(yaml.safe_dump(profile_data, sort_keys=False), encoding="utf-8")

    exit_code, out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, f"expected a clean Step 0 pass; stdout:\n{out}\nstderr:\n{err}"
    assert "OK -- Step 0 validation passed" in out, out


def test_shipped_example_batch_agent_cap_is_the_409_step2_default(pv):
    """Shipped-value lock, most recently moved by #409 step 2:
    profile.example.yml's engine.batch_agent_cap must be 10000, never a
    stale prior default -- 1000 (pre-1.3.5, refused any glossary/mass batch
    over ~26 segments at `1+N*38`) or 3500 (1.3.5-through-1.16.2, which the
    post-#348/#352 `1+N*86` mass-translate formula reduced to admitting only
    40 segments: `1 + 40*86 = 3441`).

    10000 is a policy CHOICE (an operator-sized cap), not a value derivable
    from the formula alone -- but its CONSEQUENCE for the binding consumer
    (mass-translate, the highest per-unit-cost gate this cap protects) is:
    `1 + 106*94 = 9965 <= 10000` admits a 106-segment book batch, while
    `1 + 107*94 = 10059 > 10000` refuses a 107-segment one -- the same
    boundary profile.example.yml's own engine.batch_agent_cap comment
    states.

    #732 correction: this test used to compute the pre-#607 per-segment 86
    (`max_fix_rounds * (6 + WAIT_CALLS)`) and assert 116 -- both sides
    hard-coded, so it agreed with ITSELF while the shipped comment it claims
    to pin had moved to 94/106. Correcting those two constants would have
    fixed the value and left the class, so the boundary no longer lives here
    at all: it is driven from the real, executed estimator in
    batch_size_estimator.test.py (which already instantiates the template),
    and this test keeps only what it owns -- the shipped cap constant itself.

    This test does not re-derive 10000 itself (there is no formula
    that produces a cap from nothing); it pins the shipped constant and
    documents, via that boundary arithmetic, what choosing it actually
    means -- so a future bump has the same obligation to update this
    comment's own arithmetic, not just the literal.

    Reads the REAL shipped example directly -- this is red against a tree
    that still ships an older default and green once the example is bumped
    to match. Only the shipped DEFAULT moves; already-seeded projects keep
    whatever value they filled in, so this touches fresh Step-0a copies
    only."""
    example = yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
    shipped_cap = example["engine"]["batch_agent_cap"]
    assert shipped_cap == 10000, (
        "profile.example.yml's engine.batch_agent_cap must be 10000 (the "
        f"#409 step 2 default); found {shipped_cap!r}"
    )
    # The 94-calls/segment and 106-segment figures the comment states are NOT
    # re-derived here. A second copy of the estimator's formula is exactly what
    # let this test agree with itself across #607 while the template moved; the
    # boundary is driven from the real, executed estimator in
    # batch_size_estimator.test.py::test_shipped_cap_boundary_comes_from_the_real_estimator,
    # which reads the per-segment cost back out of the gate's own reported
    # estimatedCalls and names this comment as the thing to update.
    assert example["engine"]["max_fix_rounds"] == 4, (
        "the shipped max_fix_rounds moved; profile.example.yml's batch_agent_cap "
        "comment states its boundary AT max_fix_rounds:4, so both it and the "
        "estimator-driven boundary test must be revisited together"
    )


def test_fully_filled_fixture_no_placeholders_survive(pv, tmp_path):
    """Companion unit-level check: the case-3 fixture builder must not
    accidentally leave a placeholder substring or CHOOSE_ sentinel behind --
    if it did, the clean-pass assertion above would be vacuous (it could
    pass for the wrong reason, e.g. a schema bug that stopped enforcing the
    scan_placeholders step)."""
    durable_root = tmp_path / "book-project"
    source_path = tmp_path / "source.epub"
    profile_data = _build_filled_profile(durable_root, source_path)

    errors = pv.scan_placeholders(profile_data)

    assert errors == [], f"the case-3 fixture must be placeholder-free; got:\n{errors}"


# ---------------------------------------------------------------------------
# #732 -- every DIGEST_SUBST_FIELDS engine knob discloses its change cost
# ---------------------------------------------------------------------------
#
# The class this closes: an operator deciding to change engine.effort or
# engine.max_fix_rounds was told nothing about what the change costs, while
# engine.batch_agent_cap -- three lines below, in the same block -- spelled its
# own cost out in detail. #732 measured the population of the event it was
# originally filed about (a LOWERED engine.effort discarding drafts) at zero
# across all five version-controlled durable roots, so the asymmetry is
# DISCLOSED rather than removed; this guard is what keeps the disclosure honest.
#
# Both sides of the assertion are derived live -- the example's own engine keys
# and resume_setup.py's own DIGEST_SUBST_FIELDS -- so a future knob added to the
# hashed projection turns this red until it discloses, and a heading on a knob
# outside that projection turns it red too. A hand-typed membership list here
# would freeze exactly what the guard exists to detect.
#
# WHY THE AXIS IS DIGEST_SUBST_FIELDS AND NOT "is hashed at all". There are TWO
# cost mechanisms and they do not select the same fields: compute_agent_config_hash
# (cache_key.py) folds effort/max_fix_rounds/MODEL, while DIGEST_SUBST_FIELDS
# folds effort/max_fix_rounds/batch_agent_cap/... and explicitly NOT model. So
# `engine.model` costs a converged-segment invalidation without minting a fresh
# RUN_ID of its own, and this guard does not reach it -- deliberately, because it
# is not a live YAML key: it ships COMMENTED OUT at `# model: gpt-5.3-codex`, and
# a commented-out key cannot carry an annotation block. Its cost is disclosed in
# that line's own comment instead. Naming the axis DIGEST_SUBST_FIELDS rather
# than "hashed" also keeps the assertion honest if the shipped example ever
# uncomments `model`: it would then be a live engine key that legitimately DOES
# carry a cost, and a guard calling itself "hashed" would demand the opposite.

CHANGE_COST_HEADING = "Change cost:"

RESUME_SETUP_PATH = ASSETS_DIR / "scripts" / "resume_setup.py"


def _collapse_comment_block(lines):
    """The repository's own comment-normalization shape (see
    canon_category_disclosure.test.py): strip indentation, drop ONE leading
    '#', join, then collapse whitespace. Normalizing raw comment lines
    without dropping the '#' is NOT sufficient -- a re-wrap inserts another
    '#' into the joined string and would break an assertion that is supposed
    to be wrap-insensitive. removeprefix, NOT lstrip('#'): lstrip strips a
    RUN, so it would eat the '#' of an issue reference like '#732' the moment
    one opened a line, silently changing the text being asserted against."""
    return " ".join(
        " ".join(line.strip().removeprefix("#") for line in lines).split()
    )


def _engine_comment_blocks():
    """{engine key -> collapsed text of its own annotation block}.

    A key's block is the run of comment lines at indent >= 4 following its
    `  key:` line -- the file's own convention for a field's annotation. A
    blank line does not close the run (the engine block currently contains
    none); a non-comment line at indent 2 does. Indent 4 is load-bearing, not incidental: `# model:
    gpt-5.3-codex` is a commented-out KEY at indent 2 sitting between
    `effort` and `max_fix_rounds`, and an indent-agnostic reader would
    swallow it into effort's block."""
    lines = EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    engine_idx = [i for i, line in enumerate(lines) if line.rstrip() == "engine:"]
    assert len(engine_idx) == 1, (
        f"expected exactly one top-level 'engine:' line in {EXAMPLE_PATH.name}, "
        f"found {len(engine_idx)} -- this test's anchor is stale"
    )

    blocks = {}
    current = None
    for line in lines[engine_idx[0] + 1 :]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break  # the next top-level key ends the engine block
        stripped = line.strip()
        if indent >= 4 and stripped.startswith("#"):
            if current is not None:
                blocks[current].append(stripped)
            continue
        if indent == 2 and not stripped.startswith("#") and ":" in stripped:
            current = stripped.split(":", 1)[0].strip()
            blocks.setdefault(current, [])
            continue
        # anything else (an indent-2 comment, a nested sub-block) closes the
        # current key's own annotation run without opening a new one
        current = None
    return {key: _collapse_comment_block(body) for key, body in blocks.items()}


def _digest_subst_engine_keys():
    """Live engine keys that are ALSO members of resume_setup.py's
    DIGEST_SUBST_FIELDS -- read from that module itself, never
    transcribed. See this section's header for why this is the axis."""
    module = _load_module("resume_setup_for_change_cost", RESUME_SETUP_PATH)
    example = yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
    return set(example["engine"]) & set(module.DIGEST_SUBST_FIELDS)


def test_every_digest_subst_engine_knob_discloses_its_change_cost():
    """Set equality, both directions. A knob in DIGEST_SUBST_FIELDS costs a
    fresh RUN_ID to change and must say so at the knob; a knob outside it
    (engine.max_codex_jobs_per_batch, which #735 removed from the hashed
    projection and which costs nothing to change) must NOT claim a cost it
    does not have."""
    blocks = _engine_comment_blocks()
    disclosed = {key for key, text in blocks.items() if CHANGE_COST_HEADING in text}
    expected = _digest_subst_engine_keys()

    assert expected, (
        "no engine key intersects resume_setup.py's DIGEST_SUBST_FIELDS -- either "
        "the example's engine block or that frozenset has moved, and this guard is "
        "now vacuous rather than passing"
    )
    assert disclosed == expected, (
        f"engine keys carrying a '# {CHANGE_COST_HEADING}' paragraph: {sorted(disclosed)}; "
        f"engine keys in resume_setup.py's DIGEST_SUBST_FIELDS: {sorted(expected)}. "
        f"Missing a disclosure: {sorted(expected - disclosed)}. Claiming a cost it does "
        f"not have: {sorted(disclosed - expected)}."
    )


def test_change_cost_paragraphs_still_carry_their_load_bearing_claims():
    """The heading alone is not the contract -- it can survive while the
    paragraph beneath it is emptied or reversed, which is the precise
    regression the heading-only check cannot catch. Asserted against the
    block's NORMALIZED text, so a re-wrap does not break it."""
    blocks = _engine_comment_blocks()

    for key in ("effort", "max_fix_rounds"):
        text = blocks.get(key, "")
        assert CHANGE_COST_HEADING in text, (
            f"engine.{key} lost its change-cost paragraph entirely -- a PRECONDITION "
            f"for the substance checks below; the set-equality test above reports the "
            f"same event as the real finding"
        )
        for mechanism in ("agent_config_hash", "DIGEST_SUBST_FIELDS"):
            assert mechanism in text, (
                f"engine.{key}'s change-cost paragraph no longer names {mechanism} -- "
                f"changing this knob costs BOTH a converged-segment invalidation and a "
                f"fresh RUN_ID, and an operator told only one of the two is mispriced"
            )

    # #732's own finding, and the one an operator is most likely to guess
    # wrong: the two directions are NOT priced differently.
    assert "LOWERING costs exactly the same as raising" in blocks.get("effort", ""), (
        "engine.effort's change-cost paragraph lost the #732 lowering clause -- that a "
        "LOWERED tier costs exactly what raising costs is the whole reason this "
        "disclosure exists, and without it the paragraph reads as if only raising is "
        "expensive"
    )
