"""tests/validate_backlinks.test.py -- scripts/validate_backlinks.py, the
ADVISORY Mentions-appendix coverage gate (1.8.0, plan's Contract + D4
sections). See that script's own module docstring for the full spec this
file was written against.

## Fixture strategy

Every test builds a REAL, self-contained ``durable_root`` on disk (real
COPIES of ``validate_backlinks.py`` and its sibling imports --
``validate_draft.py``, ``output_resolve.py``, ``bootstrap_names.py``,
``canon_senses.py`` + its schema, ``render_obsidian.py`` -- plus
``profile.yml``/the ownership marker/``manifest.json``/a ``languages/*.json``
particle config) and invokes the ACTUAL ``validate_backlinks.py`` as a
subprocess (``python3 {durable_root}/scripts/validate_backlinks.py``) --
mirroring ``tests/validate_draft.test.py``'s and
``tests/validate_assembled.test.py``'s own established fixture pattern, so
the script's ``Path(__file__)``-based self-anchoring resolves against the
isolated fixture root exactly as it would in production.

``occurrence_targets.py`` -- A1's own disjoint module, built in parallel --
is DELIBERATELY never copied here. Instead a small TEST DOUBLE is written
into the fixture's ``scripts/`` directory: a ``build()`` that reads its
return value from a colocated ``_test_aggregate.json`` file (set per test
via ``set_aggregate()``) and records the args it was called with to
``_test_build_call.json`` (read via ``read_build_call()``). This is a
deliberate design choice, not a stand-in for missing code: it decouples
THIS file's job -- validate_backlinks.py's OWN gate logic (the
effective-enabled short-circuit, marker-region parsing, exit codes,
collision grouping, inline-advisory tallying) -- from
``occurrence_targets.py``'s numeric matcher correctness, which is
``occurrence_targets.test.py``'s job. It also means this suite runs
identically regardless of A1's landing status.

Entity/segment note files are hand-crafted directly (``raw_entity_note``/
``raw_segment_note`` below) rather than produced by calling
``render_obsidian.render()`` -- this decouples the marker-parser tests from
D1's own landing status in ``render_obsidian.py`` (built in parallel, A4)
while still exercising the REAL, exact reserved-marker text
(``<!-- lt:mentions:begin/end -->``) the contract pins. The one exception
is ``test_spoof_b_render_rejects_newline_injected_fields``, which
necessarily drives the real ``render_obsidian.render()`` to prove the
precondition this gate's own parser relies on (a canon field can never
carry a forged marker line into a rendered vault) -- see that test's own
docstring for its A4/D1 dependency.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"

VALIDATE_BACKLINKS_SRC = SCRIPTS_SRC_DIR / "validate_backlinks.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
OUTPUT_RESOLVE_SRC = SCRIPTS_SRC_DIR / "output_resolve.py"
BOOTSTRAP_NAMES_SRC = SCRIPTS_SRC_DIR / "bootstrap_names.py"
CANON_SENSES_SRC = SCRIPTS_SRC_DIR / "canon_senses.py"
RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"
CANON_SENSES_SCHEMA_SRC = SCHEMAS_SRC_DIR / "canon-senses.schema.json"

for _p in (
    VALIDATE_BACKLINKS_SRC, VALIDATE_DRAFT_SRC, OUTPUT_RESOLVE_SRC,
    BOOTSTRAP_NAMES_SRC, CANON_SENSES_SRC, RENDER_OBSIDIAN_SRC, CANON_SENSES_SCHEMA_SRC,
):
    assert _p.is_file(), f"required fixture source not found at {_p}"


# ---------------------------------------------------------------------------
# occurrence_targets.py test double -- see module docstring.
# ---------------------------------------------------------------------------
_STUB_OCCURRENCE_TARGETS_SRC = '''\
"""Test double for occurrence_targets.py -- installed only inside an
isolated tests/validate_backlinks.test.py fixture root, never touching the
real assets/scripts/occurrence_targets.py (A1's own file). Reads its return
value from a colocated _test_aggregate.json (see set_aggregate() in the
test file) and records the (manifest, canon, nodestream) it was called
with to _test_build_call.json, so the test process can assert
validate_backlinks.py re-derives fresh from the CURRENT on-disk inputs
every run -- never trusting a cached/persisted value.
"""
import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def build(manifest, canon, senses_result, language_config, nodestream):
    call_record = {"manifest": manifest, "canon": canon, "nodestream": nodestream}
    (_HERE / "_test_build_call.json").write_text(
        json.dumps(call_record, ensure_ascii=False), encoding="utf-8"
    )
    return json.loads((_HERE / "_test_aggregate.json").read_text(encoding="utf-8"))
'''


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _copy(src: Path, dst: Path) -> None:
    dst.write_bytes(src.read_bytes())


def make_root(
    tmp_path,
    *,
    target="obsidian",
    mentions_enabled=True,
    folders=None,
    destination="out",
    particle_config_name="test.json",
):
    """A bare durable_root: real copies of validate_backlinks.py + every
    sibling it imports, the occurrence_targets.py TEST DOUBLE, a minimal
    languages/*.json particle config, profile.yml + ownership marker, and
    an empty manifest.json. canon.json / canon_senses.json /
    out/.assembled/nodestream.json / the vault's own note files are each
    written by the test itself (or deliberately left absent), mirroring
    validate_assembled.test.py's own bare-root + separate-writer style."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (
        VALIDATE_BACKLINKS_SRC, VALIDATE_DRAFT_SRC, OUTPUT_RESOLVE_SRC,
        BOOTSTRAP_NAMES_SRC, CANON_SENSES_SRC, RENDER_OBSIDIAN_SRC,
    ):
        _copy(src, scripts_dir / src.name)
    (scripts_dir / "occurrence_targets.py").write_text(
        _STUB_OCCURRENCE_TARGETS_SRC, encoding="utf-8"
    )

    (root / "schemas").mkdir()
    _copy(CANON_SENSES_SCHEMA_SRC, root / "schemas" / "canon-senses.schema.json")

    (root / "languages").mkdir()
    (root / "languages" / particle_config_name).write_text(
        json.dumps({"PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None}),
        encoding="utf-8",
    )

    profile = {
        "output": {
            "v1_scope": "assembled_book",
            "destination": destination,
            "target": target,
            "adapter_config": {
                "obsidian": {
                    "folders": folders or {},
                    "mentions_section": {"enabled": mentions_enabled},
                }
            },
        },
        "source": {
            "language": {
                "code": "en",
                "particle_config": particle_config_name,
                "smoke_test": {"report_path": None},
            }
        },
    }
    profile_path = root / "profile.yml"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )

    write_manifest(root, {})
    set_aggregate(root, {"eligible_by_source_form": {}, "unresolved_homonyms": {}})
    return root


def write_manifest(root: Path, blocks: dict) -> None:
    (root / "manifest.json").write_text(
        json.dumps({"blocks": blocks, "segments": []}, ensure_ascii=False), encoding="utf-8"
    )


def canon_entry(source_form, canonical_target_form, *, category=None,
                is_proper_name=True, basis="transliterated", confidence="high", note=None):
    entry = {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "canonical_target_form": canonical_target_form,
        "basis": basis,
        "confidence": confidence,
    }
    if category is not None:
        entry["category"] = category
    if note is not None:
        entry["note"] = note
    return entry


def write_canon(root: Path, entries: dict) -> None:
    (root / "canon.json").write_text(
        json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8"
    )


def write_canon_senses(root: Path, entries_by_source_form: dict) -> None:
    doc = {"schema_version": 1, "entries_by_source_form": entries_by_source_form}
    (root / "canon_senses.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def write_nodestream(root: Path, seg_order: list, nodes=None) -> dict:
    assembled_dir = root / "out" / ".assembled"
    assembled_dir.mkdir(parents=True, exist_ok=True)
    nodestream = {"book": {"seg_order": seg_order}, "nodes": nodes or []}
    (assembled_dir / "nodestream.json").write_text(
        json.dumps(nodestream, ensure_ascii=False), encoding="utf-8"
    )
    return nodestream


def set_aggregate(root: Path, aggregate: dict) -> None:
    (root / "scripts" / "_test_aggregate.json").write_text(
        json.dumps(aggregate, ensure_ascii=False), encoding="utf-8"
    )


def read_build_call(root: Path):
    p = root / "scripts" / "_test_build_call.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def write_note(root: Path, relpath: str, text: str, *, vault_dir="out") -> Path:
    path = root / vault_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def raw_entity_note(source_form="Ivan", canonical_target_form="Ivan",
                     extra_frontmatter_lines=None, body_lines=None):
    """Hand-crafted entity note text -- see module docstring for why this
    bypasses render_obsidian.render() itself. `extra_frontmatter_lines`,
    when given, are inserted INSIDE the "---"-delimited frontmatter block
    (used by the frontmatter-scalar-injection spoof test). `body_lines`
    replaces the default (heading-only) body when given."""
    fm_body = [
        f"source_form: {source_form}",
        f"canonical_target_form: {canonical_target_form}",
        "category: other",
        "is_proper_name: true",
        "basis: transliterated",
        "confidence: high",
        "note: ''",
        "direction: ltr",
    ]
    if extra_frontmatter_lines:
        fm_body.extend(extra_frontmatter_lines)
    lines = ["---"] + fm_body + ["---", "", f"# {canonical_target_form}"]
    if body_lines is not None:
        lines += [""] + list(body_lines)
    return "\n".join(lines) + "\n"


def mentions_block(link_targets):
    return [
        "<!-- lt:mentions:begin -->",
        "## Mentions",
        "",
        *[f"- [[{t}]]" for t in link_targets],
        "<!-- lt:mentions:end -->",
    ]


def raw_segment_note(seg="seg01", body_lines=None):
    lines = ["---", f"seg: {seg}", "title: Test", "direction: ltr", "---", ""]
    if body_lines:
        lines += list(body_lines)
    return "\n".join(lines) + "\n"


def run_gate(root: Path, extra_args=None):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "validate_backlinks.py"), *(extra_args or [])],
        capture_output=True, text=True, timeout=30,
    )


def report_of(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line on stdout, got: {proc.stdout!r} "
        f"(stderr={proc.stderr!r})"
    )
    return json.loads(lines[0])


# ===========================================================================
# Effective-enabled short-circuit
# ===========================================================================

def test_disabled_flag_off_short_circuits_and_never_loads_manifest(tmp_path):
    """Flag off (target=obsidian). Deleting manifest.json PROVES the
    short-circuit fires BEFORE any metric input is loaded -- if the gate
    tried to load manifest.json anyway it would exit 2, not 0 (mutation:
    'run metric-1 when disabled -> every project warns')."""
    root = make_root(tmp_path, target="obsidian", mentions_enabled=False)
    (root / "manifest.json").unlink()

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report == {
        "mentions_coverage": {"status": "disabled", "checked_entities": 0, "missing": []},
        "unresolved_homonyms": [],
        "collisions": [],
        "inline_advisory": {"thin_coverage": []},
        "warnings": 0,
    }
    assert read_build_call(root) is None, "occurrence_targets.build() must never run when disabled"


def test_disabled_wrong_target_dormant_flag_short_circuits(tmp_path):
    """target=custom with the obsidian mentions_section flag still TRUE
    (dormant) -- the gate must ignore the flag under a non-obsidian target
    (mutation: 'gate ignores target -> parses a non-Obsidian vault')."""
    root = make_root(tmp_path, target="custom", mentions_enabled=True)
    (root / "manifest.json").unlink()

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["status"] == "disabled"
    assert report["warnings"] == 0
    assert read_build_call(root) is None


# ===========================================================================
# Metric 1 -- Mentions coverage (happy path + missing)
# ===========================================================================

def test_enabled_full_coverage_no_warnings(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {"Ivan": canon_entry("Ivan", "Ivan")})
    write_nodestream(root, ["seg01"])
    set_aggregate(root, {
        "eligible_by_source_form": {"Ivan": [{"source_form": "Ivan", "seg": "seg01", "origin": "block"}]},
        "unresolved_homonyms": {},
    })
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=mentions_block(["001 seg01"])))
    write_note(root, "001 seg01.md", raw_segment_note())

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"] == {"status": "enabled", "checked_entities": 1, "missing": []}
    assert report["warnings"] == 0


def test_missing_link_warns_and_exits_1(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {"Ivan": canon_entry("Ivan", "Ivan")})
    write_nodestream(root, ["seg01"])
    set_aggregate(root, {
        "eligible_by_source_form": {"Ivan": [{"source_form": "Ivan", "seg": "seg01", "origin": "block"}]},
        "unresolved_homonyms": {},
    })
    # Mentions region present but EMPTY -- the expected link never landed.
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=mentions_block([])))
    write_note(root, "001 seg01.md", raw_segment_note())

    proc = run_gate(root)
    assert proc.returncode == 1, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}]
    assert report["warnings"] == 1


def test_missing_entity_note_file_warns(tmp_path):
    """The entity note simply doesn't exist on disk (render never wrote
    it, or it was deleted) -- treated as coverage MISS, never a hard
    error: exactly the false-green defect this gate exists to catch."""
    root = make_root(tmp_path)
    write_canon(root, {"Ivan": canon_entry("Ivan", "Ivan")})
    write_nodestream(root, ["seg01"])
    set_aggregate(root, {
        "eligible_by_source_form": {"Ivan": [{"source_form": "Ivan", "seg": "seg01", "origin": "block"}]},
        "unresolved_homonyms": {},
    })

    proc = run_gate(root)
    assert proc.returncode == 1, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}]


def test_zero_occurrence_entity_not_checked_not_warned(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {"Petro": canon_entry("Petro", "Petro")})
    write_nodestream(root, [])
    set_aggregate(root, {"eligible_by_source_form": {"Petro": []}, "unresolved_homonyms": {}})

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"] == {"status": "enabled", "checked_entities": 0, "missing": []}
    assert report["warnings"] == 0


# ===========================================================================
# Spoof tests (D4 / codex R5 b2, R6 b1, R7 b1)
# ===========================================================================

def _one_expected_setup(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {"Ivan": canon_entry("Ivan", "Ivan")})
    write_nodestream(root, ["seg01"])
    set_aggregate(root, {
        "eligible_by_source_form": {"Ivan": [{"source_form": "Ivan", "seg": "seg01", "origin": "block"}]},
        "unresolved_homonyms": {},
    })
    write_note(root, "001 seg01.md", raw_segment_note())
    return root


def test_spoof_a_fake_markdown_heading_without_markers_does_not_satisfy(tmp_path):
    """(a): a fake '## Mentions' heading + the correct link, but with NO
    reserved <!-- lt:mentions:begin/end --> markers -- the gate recognizes
    ONLY the exact marker lines, never a heading, so this still WARNs."""
    root = _one_expected_setup(tmp_path)
    write_note(root, "other/Ivan.md", raw_entity_note(
        body_lines=["## Mentions", "", "- [[001 seg01]]"]
    ))

    proc = run_gate(root)
    assert proc.returncode == 1, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}]


def test_spoof_c_multiple_marker_pairs_rejected_never_trusts_first(tmp_path):
    """(c1): TWO complete, well-formed marker pairs -- the FIRST one even
    carries the correct link -- must still be rejected outright (never
    'trust the first match')."""
    root = _one_expected_setup(tmp_path)
    body = mentions_block(["001 seg01"]) + [""] + mentions_block([])
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=body))

    proc = run_gate(root)
    assert proc.returncode == 1, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}]


def test_spoof_c_dangling_begin_marker_rejected(tmp_path):
    """(c2): a begin marker with no matching end -- malformed, rejected."""
    root = _one_expected_setup(tmp_path)
    write_note(root, "other/Ivan.md", raw_entity_note(
        body_lines=["<!-- lt:mentions:begin -->", "- [[001 seg01]]"]
    ))

    proc = run_gate(root)
    assert proc.returncode == 1, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}]


def test_spoof_d_frontmatter_scalar_injection_does_not_satisfy(tmp_path):
    """(d): a complete, well-formed marker pair + the correct link is
    smuggled into the frontmatter block itself (between the two "---"
    delimiters, exactly where an unrestricted scalar like `category`/
    `source` could carry attacker-authored text) -- the REAL body has no
    Mentions region at all. The gate strips the WHOLE frontmatter block
    wholesale before ever searching for markers, so this still WARNs."""
    root = _one_expected_setup(tmp_path)
    write_note(root, "other/Ivan.md", raw_entity_note(
        extra_frontmatter_lines=mentions_block(["001 seg01"]),
        body_lines=None,
    ))

    proc = run_gate(root)
    assert proc.returncode == 1, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}]


def test_spoof_b_render_rejects_newline_injected_fields(tmp_path):
    """(b): a forged marker region injected via a newline-bearing
    canonical_target_form is impossible because render_obsidian.render()
    itself refuses to render such a canon entry when the mentions_section
    feature is effective-enabled (D1) -- proving the precondition this
    gate's own parser relies on (no rendered vault can EVER carry a
    canon-field-forged marker line).

    NOTE: this test drives the REAL render_obsidian.py directly (loaded
    from its actual repo source, not this file's isolated fixture root) --
    it depends on D1 having landed in render_obsidian.py (built in
    parallel, A4/#28). Until then it is expected to FAIL (RenderError not
    yet raised); it is not gated on validate_backlinks.py's own logic."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "render_obsidian_for_vb_spoof_b", RENDER_OBSIDIAN_SRC
    )
    render_obsidian = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render_obsidian)

    nodestream = {
        "book": {"seg_order": ["seg01"], "title": "T"},
        "nodes": [],
        "footnotes": [],
        "meta": {"target": "en"},
    }
    canon = {"entries": {
        "Ivan": canon_entry("Ivan", "Ivan\ninjected"),
    }}
    profile = {
        "output": {
            "target": "obsidian",
            "adapter_config": {"obsidian": {"mentions_section": {"enabled": True}}},
        },
    }
    out_dir = tmp_path / "spoof_b_out"
    out_dir.mkdir()

    with pytest.raises(render_obsidian.RenderError):
        render_obsidian.render(nodestream, canon, profile, out_dir)


# ===========================================================================
# Exit-neutral diagnostics: collisions, unresolved_homonyms, inline advisory
# ===========================================================================

def test_collision_is_exit_neutral(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {
        "Ivan": canon_entry("Ivan", "Ivan"),
        "IVANOV": canon_entry("IVANOV", "IVAN "),  # collides with "Ivan" under normalize_form
    })
    write_nodestream(root, [])
    set_aggregate(root, {"eligible_by_source_form": {}, "unresolved_homonyms": {}})

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report["warnings"] == 0
    assert len(report["collisions"]) == 1
    assert sorted(report["collisions"][0]["owners"]) == ["IVANOV", "Ivan"]
    # B5 (#240 gate half): this gate's own grouping key folds case + collapses
    # whitespace ("Ivan" == "IVAN " under normalize_form), but
    # render_obsidian.build_entity_index groups NFC-only, case-SENSITIVELY --
    # "Ivan" and "IVAN " are two entirely distinct target strings there, each
    # with exactly one owner, so the renderer NEVER de-links this pair. The
    # gate reports a collision the renderer will never act on -- RED today:
    # the field does not exist at all (KeyError).
    assert report["collisions"][0]["renderer_delinked"] is False


def test_collision_renderer_delinked_true_for_same_case_pair(tmp_path):
    """B4: two entries sharing an IDENTICAL (same-case) canonical_target_form
    -- render_obsidian.build_entity_index groups by NFC-exact, so this pair
    collides there too and IS de-linked. RED today: the field does not exist
    (KeyError); probe-confirmed the renderer removes this target from its
    link map under collision_delink=True."""
    root = make_root(tmp_path)
    write_canon(root, {
        "Petro": canon_entry("Petro", "Peter"),
        "Pavlo": canon_entry("Pavlo", "Peter"),
    })
    write_nodestream(root, [])
    set_aggregate(root, {"eligible_by_source_form": {}, "unresolved_homonyms": {}})

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert len(report["collisions"]) == 1
    assert report["collisions"][0]["canonical_target_form"] == "Peter"
    assert report["collisions"][0]["renderer_delinked"] is True


def test_collision_sense_translated_pair_is_delinked_after_240(tmp_path):
    """B6: a sense_translated entry sharing a target with an ordinary
    transliterated entry -- POST-#240 semantics: the renderer's collision
    tally now counts the sense_translated owner too, so this IS de-linked.
    RED today twice over (pre-#240 render_obsidian.py): the field does not
    exist at all, AND -- even once added naively -- the target would survive
    both the collision_delink=False and collision_delink=True maps (the old
    `:428` skip erased the sense_translated owner from the tally entirely,
    so the narrative entry looked like the sole, uncontested owner)."""
    root = make_root(tmp_path)
    write_canon(root, {
        "Nadezhda": canon_entry("Nadezhda", "Hope", basis="sense_translated"),
        "Hope_src": canon_entry("Hope_src", "Hope", basis="transliterated"),
    })
    write_nodestream(root, [])
    set_aggregate(root, {"eligible_by_source_form": {}, "unresolved_homonyms": {}})

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert len(report["collisions"]) == 1
    assert report["collisions"][0]["canonical_target_form"] == "Hope"
    assert report["collisions"][0]["renderer_delinked"] is True


def test_unresolved_homonym_is_not_a_coverage_warning(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {})
    write_nodestream(root, [])
    set_aggregate(root, {
        "eligible_by_source_form": {},
        "unresolved_homonyms": {"Split": {"count": 2, "segs": ["seg02", "seg01"]}},
    })

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"] == {"status": "enabled", "checked_entities": 0, "missing": []}
    assert report["warnings"] == 0
    assert report["unresolved_homonyms"] == [{"source_form": "Split", "count": 2, "segs": ["seg01", "seg02"]}]


def test_inline_advisory_reports_thin_coverage_but_stays_exit_neutral(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {"Ivan": canon_entry("Ivan", "Ivan")})
    write_nodestream(root, ["seg01"])
    set_aggregate(root, {
        "eligible_by_source_form": {"Ivan": [{"source_form": "Ivan", "seg": "seg01", "origin": "block"}]},
        "unresolved_homonyms": {},
    })
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=mentions_block(["001 seg01"])))
    # Segment note carries NO inline entity link at all.
    write_note(root, "001 seg01.md", raw_segment_note(body_lines=["Plain narrative text."]))

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr  # Mentions coverage itself is satisfied
    report = report_of(proc)
    assert report["warnings"] == 0
    assert report["inline_advisory"]["thin_coverage"] == [
        {"source_form": "Ivan", "inline_links": 0, "source_occurrences": 1}
    ]


def test_inline_advisory_counts_a_real_inline_link(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {"Ivan": canon_entry("Ivan", "Ivan")})
    write_nodestream(root, ["seg01"])
    set_aggregate(root, {
        "eligible_by_source_form": {"Ivan": [{"source_form": "Ivan", "seg": "seg01", "origin": "block"}]},
        "unresolved_homonyms": {},
    })
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=mentions_block(["001 seg01"])))
    write_note(root, "001 seg01.md", raw_segment_note(
        body_lines=["Ivan walked in: [[other/Ivan|Ivan]]."]
    ))

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report["inline_advisory"]["thin_coverage"] == []


def test_inline_advisory_excludes_links_inside_a_mentions_region(tmp_path):
    """(mutation: 'count Mentions links as inline coverage -> advisory
    falsely full'): a segment note carrying a SMUGGLED
    <!-- lt:mentions:begin/end --> region with an entity link inside it
    must NOT count toward inline coverage -- real segment notes never
    legitimately carry this region (D1: only entity notes do); this
    exercises the defensive exclusion directly."""
    root = make_root(tmp_path)
    write_canon(root, {"Ivan": canon_entry("Ivan", "Ivan")})
    write_nodestream(root, ["seg01"])
    set_aggregate(root, {
        "eligible_by_source_form": {"Ivan": [{"source_form": "Ivan", "seg": "seg01", "origin": "block"}]},
        "unresolved_homonyms": {},
    })
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=mentions_block(["001 seg01"])))
    # A segment note smuggling a marker-wrapped ENTITY link (not a real
    # Mentions region -- those only ever live in entity notes) -- proves
    # the defensive exclusion, not just its absence in practice.
    smuggled_body = [
        "Narrative.",
        "",
        "<!-- lt:mentions:begin -->",
        "## Mentions",
        "",
        "- [[other/Ivan|Ivan]]",
        "<!-- lt:mentions:end -->",
    ]
    write_note(root, "001 seg01.md", raw_segment_note(body_lines=smuggled_body))

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report["inline_advisory"]["thin_coverage"] == [
        {"source_form": "Ivan", "inline_links": 0, "source_occurrences": 1}
    ]


# ===========================================================================
# Fresh re-derivation (never trust a persisted/cached value)
# ===========================================================================

def test_expected_set_is_fresh_rederived_each_run(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {})
    write_nodestream(root, ["seg01"])

    proc1 = run_gate(root)
    assert proc1.returncode == 0, proc1.stderr
    call1 = read_build_call(root)
    assert call1 is not None
    assert call1["nodestream"]["book"]["seg_order"] == ["seg01"]

    write_nodestream(root, ["seg01", "seg02"])
    proc2 = run_gate(root)
    assert proc2.returncode == 0, proc2.stderr
    call2 = read_build_call(root)
    assert call2["nodestream"]["book"]["seg_order"] == ["seg01", "seg02"], (
        "validate_backlinks.py must re-read nodestream.json fresh every run, "
        "never cache/trust a stale copy"
    )


# ===========================================================================
# Tolerances + hard errors
# ===========================================================================

def test_canon_absent_is_tolerated_as_zero_entities(tmp_path):
    root = make_root(tmp_path)
    # No write_canon() call at all -- canon.json genuinely absent.
    write_nodestream(root, [])

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["checked_entities"] == 0
    assert report["collisions"] == []


def test_missing_nodestream_is_a_hard_error_exit_2(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {})
    # out/.assembled/nodestream.json deliberately never written.

    proc = run_gate(root)
    assert proc.returncode == 2, proc.stdout
    assert "nodestream" in proc.stderr.lower()
    assert proc.stdout.strip() == ""


def test_malformed_nodestream_is_a_hard_error_exit_2(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {})
    assembled_dir = root / "out" / ".assembled"
    assembled_dir.mkdir(parents=True)
    (assembled_dir / "nodestream.json").write_text("{not valid json", encoding="utf-8")

    proc = run_gate(root)
    assert proc.returncode == 2, proc.stdout
    assert proc.stdout.strip() == ""


def test_missing_manifest_is_a_hard_error_exit_2(tmp_path):
    root = make_root(tmp_path)
    write_canon(root, {})
    write_nodestream(root, [])
    (root / "manifest.json").unlink()

    proc = run_gate(root)
    assert proc.returncode == 2, proc.stdout
    assert "manifest.json" in proc.stderr


def test_malformed_nodestream_node_is_a_clean_hard_error_not_a_traceback(tmp_path):
    """A node missing "seg" must not escape as an uncaught KeyError (exit 1
    with a raw traceback) -- it's a malformed input, a clean exit 2."""
    root = make_root(tmp_path)
    write_canon(root, {})
    write_nodestream(root, [], nodes=[{"order_index": 0}])

    proc = run_gate(root)
    assert proc.returncode == 2, proc.stdout
    assert "Traceback" not in proc.stderr
    assert "nodestream" in proc.stderr


def _write_raw_nodestream(root: Path, doc: dict) -> None:
    assembled_dir = root / "out" / ".assembled"
    assembled_dir.mkdir(parents=True, exist_ok=True)
    (assembled_dir / "nodestream.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_seg_order_non_list_is_clean_exit_2(tmp_path):
    """B7 (#236): `book.seg_order` a bare string ("abc") is not a list --
    RED today: no type check at all, so the gate iterates it as CHARACTERS
    and returns a bogus seg map ({'a', 'b', 'c'}) -- a silently WRONG
    answer, not a crash. Must be a clean, reason-carrying exit 2."""
    root = make_root(tmp_path)
    write_canon(root, {})
    _write_raw_nodestream(root, {"book": {"seg_order": "abc"}, "nodes": []})

    proc = run_gate(root)
    assert proc.returncode == 2, proc.stdout
    assert "Traceback" not in proc.stderr
    assert "seg_order" in proc.stderr


def test_seg_order_non_string_element_is_clean_exit_2(tmp_path):
    """B8 (#236): `seg_order = [1, 2]` -- RED today: uncaught AttributeError
    ('int' object has no attribute 'encode') escapes as exit 1 plus a raw
    traceback, and exit 1 is ADVISORY -- W9 would silently continue past a
    gate that crashed."""
    root = make_root(tmp_path)
    write_canon(root, {})
    _write_raw_nodestream(root, {"book": {"seg_order": [1, 2]}, "nodes": []})

    proc = run_gate(root)
    assert proc.returncode == 2, proc.stdout
    assert "Traceback" not in proc.stderr
    assert "seg_order" in proc.stderr


def test_book_not_an_object_is_clean_exit_2(tmp_path):
    """B9 (#236): `book = ["x"]` (non-empty, so it survives the `or {}`
    fallback that only catches falsy values). RED today: uncaught
    AttributeError ('list' object has no attribute 'get') when
    `_seg_filename_map` calls `.get("seg_order")` on a list."""
    root = make_root(tmp_path)
    write_canon(root, {})
    _write_raw_nodestream(root, {"book": ["x"], "nodes": []})

    proc = run_gate(root)
    assert proc.returncode == 2, proc.stdout
    assert "Traceback" not in proc.stderr
    assert "book" in proc.stderr


def test_canon_entries_present_but_not_object_is_clean_exit_2(tmp_path):
    """B10 (#236): canon.json `{"entries": []}` -- RED today:
    render_obsidian._canon_entries tolerantly returns {} for this shape
    (correct FOR THE RENDERER, which also accepts a bare entries{} mapping
    as its whole `canon` argument), so the gate silently checked ZERO
    entities and exited 0 -- green but vacuous. The gate is stricter than
    the renderer on purpose here."""
    root = make_root(tmp_path)
    (root / "canon.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    write_nodestream(root, [])

    proc = run_gate(root)
    assert proc.returncode == 2, proc.stdout
    assert "Traceback" not in proc.stderr
    assert "entries" in proc.stderr


# ===========================================================================
# #236 fence-awareness + inline-code stripping
# ===========================================================================


def test_fenced_mentions_region_is_not_a_region(tmp_path):
    """B11: an entity note whose ONLY <!-- lt:mentions:begin/end --> marker
    pair sits inside a ``` fence enclosing an EXAMPLE that happens to name
    the real expected link -- the dangerous direction, a false GREEN. RED
    today: `parse_mentions_region` treats the fenced pair as the real
    region regardless of the fence (probe-confirmed), so the example's
    "001 seg01" link would satisfy real coverage even though there is no
    genuine Mentions region in the note at all."""
    root = _one_expected_setup(tmp_path)
    body = [
        "Some prose before the example.",
        "",
        "Here is what a real Mentions section would look like:",
        "",
        "```",
        *mentions_block(["001 seg01"]),
        "```",
        "",
        "More prose after -- no REAL marker pair anywhere in this note.",
    ]
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=body))

    proc = run_gate(root)
    assert proc.returncode == 1, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}], (
        "a marker pair living inside a fenced code block must never count "
        "as a real Mentions region"
    )


def test_fenced_marker_does_not_break_a_real_region(tmp_path):
    """B12: the INVERSE-direction guard, so the fence fix cannot become a
    one-way ratchet -- a REAL Mentions region plus a SEPARATE fenced
    example block (also containing marker-shaped lines) must still parse
    the real region normally. RED under a naive "reject the whole note on
    ANY marker duplicate" fix: two begin/end pairs total (one real, one
    fenced) would make `_single_marker_pair` see len(begins) != 1 and
    return None, false-RED-ing every expected seg as missing."""
    root = _one_expected_setup(tmp_path)
    body = [
        *mentions_block(["001 seg01"]),
        "",
        "Here is an example of the marker syntax:",
        "",
        "```",
        *mentions_block(["999 FORGED SEGMENT"]),
        "```",
    ]
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=body))

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report["warnings"] == 0
    assert report["mentions_coverage"]["missing"] == []


def test_info_bearing_fence_line_never_closes_the_outer_fence(tmp_path):
    """Bot review P1 finding 2: a line shaped like an OPENING fence with an
    info string (e.g. "```python") must NEVER be mistaken for a CLOSING
    fence while an outer fence is still open -- CommonMark reserves an
    info string for opening fences only; a real closer carries nothing but
    optional trailing whitespace after the delimiter run. RED today: the
    prior `_FENCE_DELIM_RE` was prefix-only (captured just the backtick
    run, ignored "python"), so `char == open_char and length >= open_len`
    fired on "```python" exactly as it would on a bare "```", wrongly
    closing the outer fence and un-masking everything after it -- bot
    repro: `_single_marker_pair(['```', '```python', begin, link, end,
    '```']) == (2, 4)` (the inner marker pair wrongly counted as real)."""
    root = _one_expected_setup(tmp_path)
    body = [
        "An outer fence containing an inner, illustrative fenced example:",
        "",
        "```",
        "```python",
        *mentions_block(["001 seg01"]),
        "```",
    ]
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=body))

    proc = run_gate(root)
    assert proc.returncode == 1, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}], (
        "a marker pair living inside a fence that an info-bearing "
        "delimiter line wrongly appeared to close must still be treated "
        "as entirely inside that fence, never a real Mentions region"
    )


def test_four_space_indented_fence_does_not_open_a_fence(tmp_path):
    """Bot review P2 finding (round 2): a fence delimiter indented 4+
    COLUMNS is CommonMark indented code, not a fence -- whether or not a
    fence happens to be open at that point. RED today: `_fenced_line_mask`
    matched `_FENCE_DELIM_RE` against `ln.strip()`, which erases ANY amount
    of leading whitespace, so a 4-space-indented "```" wrongly opened a
    spurious fence and masked the real marker pair right after it -- bot
    repro: `_single_marker_pair(['    ```', begin, '[[001 seg01]]', end])
    is None` (wrongly). Real marker pair right after the indented "```"
    (never closed -- it must never have opened) must still be found."""
    root = _one_expected_setup(tmp_path)
    body = [
        "    ```",  # 4-space indent -- indented CODE, not a fence opener
        *mentions_block(["001 seg01"]),
    ]
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=body))

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [], (
        "a 4-space-indented ``` line must never open a spurious fence and "
        "mask a real Mentions marker pair right after it"
    )


@pytest.mark.parametrize("spaces", [0, 1, 2, 3])
def test_zero_to_three_space_indented_fence_still_opens_a_fence(tmp_path, spaces):
    """The boundary companion to the test above: at 0-3 columns of
    indentation a ``` line IS still a genuine fence opener (CommonMark's
    own <=3-column tolerance). Left deliberately unclosed here, so the
    real marker pair right after it stays masked as fence content and is
    reported MISSING -- pins the boundary at exactly 3 vs 4, not just
    proving one side of it."""
    root = _one_expected_setup(tmp_path)
    body = [
        " " * spaces + "```",
        *mentions_block(["001 seg01"]),
    ]
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=body))

    proc = run_gate(root)
    assert proc.returncode == 1, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}], (
        f"a {spaces}-space-indented ``` line must still open a real fence "
        f"(<=3 columns of indentation is within CommonMark's tolerance)"
    )


def test_tab_indented_fence_does_not_open_a_fence(tmp_path):
    """A single leading tab expands to column 4 (CommonMark's tab-stop
    rule, never a flat width) -- same "indented code, not a fence"
    treatment as 4 literal spaces above, not 1 character's worth of
    indent."""
    root = _one_expected_setup(tmp_path)
    body = [
        "\t```",
        *mentions_block(["001 seg01"]),
    ]
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=body))

    proc = run_gate(root)
    assert proc.returncode == 0, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [], (
        "a tab-indented (column 4) ``` line must never open a spurious "
        "fence either"
    )


def test_inline_code_wikilink_not_counted_as_coverage(tmp_path):
    """B13: a Mentions region whose only wikilink is a BACKTICK-QUOTED
    `` `[[001 real]]` `` -- an author showing the link syntax as literal
    text, not emitting a real link. RED today: counted as real coverage
    (probe-confirmed: `frozenset({'001 real'})`)."""
    root = _one_expected_setup(tmp_path)
    body = [
        "<!-- lt:mentions:begin -->",
        "## Mentions",
        "",
        "See the syntax: `[[001 seg01]]` (not a real link, just an example).",
        "<!-- lt:mentions:end -->",
    ]
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=body))

    proc = run_gate(root)
    assert proc.returncode == 1, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}], (
        "a backtick-quoted wikilink must never count as real coverage"
    )


def test_double_backtick_quoted_wikilink_not_counted_as_coverage(tmp_path):
    """Bot review P1 finding 3: a Mentions region whose only wikilink is
    wrapped in a DOUBLE-backtick span (`` ``[[001 real]]`` ``, the
    CommonMark idiom for quoting text that itself contains a single
    backtick, or just an author's stylistic choice) -- must be stripped
    exactly like the single-backtick case above. RED today: the prior
    `_INLINE_CODE_RE = r"`[^`\\n]*`"` only ever matched a SINGLE-backtick
    span -- against "``[[001 seg01]]``" it "closed" on the very next
    (adjacent) backtick, consuming an EMPTY single-backtick pair at each
    end and leaving the wikilink in the middle fully exposed to
    `_WIKILINK_RE` (probe-confirmed: `parse_mentions_region` returned
    `frozenset({'001 seg01'})`) -- the exact false-GREEN #236 exists to
    prevent."""
    root = _one_expected_setup(tmp_path)
    body = [
        "<!-- lt:mentions:begin -->",
        "## Mentions",
        "",
        "See the syntax: ``[[001 seg01]]`` (not a real link, just an example).",
        "<!-- lt:mentions:end -->",
    ]
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=body))

    proc = run_gate(root)
    assert proc.returncode == 1, proc.stderr
    report = report_of(proc)
    assert report["mentions_coverage"]["missing"] == [{"source_form": "Ivan", "seg": "seg01"}], (
        "a double-backtick-quoted wikilink must never count as real coverage"
    )


# ===========================================================================
# --vault override
# ===========================================================================

def test_vault_override_is_honored(tmp_path):
    root = make_root(tmp_path, destination="out")
    write_canon(root, {"Ivan": canon_entry("Ivan", "Ivan")})
    write_nodestream(root, ["seg01"])
    set_aggregate(root, {
        "eligible_by_source_form": {"Ivan": [{"source_form": "Ivan", "seg": "seg01", "origin": "block"}]},
        "unresolved_homonyms": {},
    })
    # Note files live under a DIFFERENT directory than the resolved default
    # (root/"out") -- the default out_dir has nothing in it.
    write_note(root, "other/Ivan.md", raw_entity_note(body_lines=mentions_block(["001 seg01"])), vault_dir="alt_vault")
    write_note(root, "001 seg01.md", raw_segment_note(), vault_dir="alt_vault")

    default_proc = run_gate(root)
    assert default_proc.returncode == 1, "default out_dir has no notes -- should WARN"

    override_proc = run_gate(root, extra_args=["--vault", str(root / "alt_vault")])
    assert override_proc.returncode == 0, override_proc.stderr
    report = report_of(override_proc)
    assert report["warnings"] == 0


# ===========================================================================
# Exit-code contract (consolidated)
# ===========================================================================

def test_exit_code_contract_across_the_three_states(tmp_path):
    # 0: disabled.
    disabled_root = make_root(tmp_path / "disabled", target="obsidian", mentions_enabled=False)
    assert run_gate(disabled_root).returncode == 0

    # 0: enabled, warnings == 0.
    clean_root = make_root(tmp_path / "clean")
    write_canon(clean_root, {})
    write_nodestream(clean_root, [])
    assert run_gate(clean_root).returncode == 0

    # 1: enabled, warnings > 0 (advisory).
    warn_root = make_root(tmp_path / "warn")
    write_canon(warn_root, {"Ivan": canon_entry("Ivan", "Ivan")})
    write_nodestream(warn_root, ["seg01"])
    set_aggregate(warn_root, {
        "eligible_by_source_form": {"Ivan": [{"source_form": "Ivan", "seg": "seg01", "origin": "block"}]},
        "unresolved_homonyms": {},
    })
    assert run_gate(warn_root).returncode == 1

    # 2: hard error (malformed nodestream).
    hard_error_root = make_root(tmp_path / "hard_error")
    write_canon(hard_error_root, {})
    assembled_dir = hard_error_root / "out" / ".assembled"
    assembled_dir.mkdir(parents=True)
    (assembled_dir / "nodestream.json").write_text("not json", encoding="utf-8")
    assert run_gate(hard_error_root).returncode == 2
