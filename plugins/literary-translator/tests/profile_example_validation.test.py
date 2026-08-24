"""tests/profile_example_validation.test.py

Targets ``assets/profile.example.yml`` + ``profile_validate.py``, split into
exactly THREE cases (per the plugin's own test enumeration):

  1. A missing ``profile.yml`` triggers Step 0's auto-copy-then-halt path --
     the shipped example is copied to the target path verbatim, and the run
     halts (non-zero exit) naming the path and instructing the user to fill
     in every placeholder.
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


def _load_profile_validate():
    """Imports profile_validate.py fresh from its real, shipped install
    path -- ``assets/scripts/`` is not a package on sys.path, so a plain
    ``import`` won't reach it. A fresh module per call (see the ``pv``
    fixture below) avoids any cross-test state leakage through the
    module-level ``yaml``/``jsonschema`` handles ``dependency_preflight()``
    populates."""
    spec = importlib.util.spec_from_file_location(
        "profile_validate_under_test", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None, f"could not load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
# verbatim to the target path, then halts (never runs dependency preflight
# or schema validation in this branch at all).
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
    `1 + 116*86 = 9977 <= 10000` admits a 116-segment book batch, while
    `1 + 117*86 = 10063 > 10000` refuses a 117-segment one -- the same
    boundary profile.example.yml's own engine.batch_agent_cap comment
    states. This test does not re-derive 10000 itself (there is no formula
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
    # The boundary this cap draws for mass-translate (86 calls/segment at the
    # shipped max_fix_rounds:4 -- see profile.example.yml's own derivation).
    max_fix_rounds = example["engine"]["max_fix_rounds"]
    wait_calls = 9  # 1.16.1/#348's shipped WAIT_CHUNKS(8) + 1 authoritative re-check
    per_segment = 8 + 2 * wait_calls + max_fix_rounds * (6 + wait_calls)
    admitted = (shipped_cap - 1) // per_segment
    assert 1 + admitted * per_segment <= shipped_cap
    assert 1 + (admitted + 1) * per_segment > shipped_cap
    assert admitted == 116, (
        f"at the shipped max_fix_rounds:{max_fix_rounds} (per-segment cost "
        f"{per_segment}), batch_agent_cap:{shipped_cap} admits {admitted} "
        f"mass-translate segments, not the 116 profile.example.yml's own "
        f"comment documents -- either the cap, max_fix_rounds, or the "
        f"documented figure has drifted from the other two"
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
