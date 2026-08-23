"""tests/assemble_link_groups_wiring.test.py -- #588's assemble.py leg: the
`canon_link_groups.json` sidecar reaching `render_obsidian.render()` through
`nodestream["link_groups"]`, and the `delink_cost` block coming back out of
the adapter on the path that matters.

Runs the REAL `assemble.py` as a subprocess against a real durable_root
(same fixture discipline as `tests/assemble.test.py`, whose helpers are
deliberately re-derived here rather than imported -- this suite's
each-file-self-contained convention).

The wiring facts pinned here, none of which the renderer's own unit tests
can see:

  - The sidecar is loaded, validated, projected to `{member: primary}`, and
    PERSISTED into `out/.assembled/nodestream.json` -- the same artifact
    `validate_backlinks.py` later reads, so gate and renderer consume one
    authority rather than each re-loading the file.
  - It is gated on `output.target == "obsidian"` ONLY. #588's own delivered
    vault had the `## Mentions` appendix OFF, and every default fixture here
    writes `enabled: false`: collision de-linking runs regardless of that
    flag (#206/#207), so its cost must be reported regardless too. This is
    exactly the path the W9 gate short-circuits out of.
  - No sidecar means ZERO new dependency surface: `canon_link_groups` (and
    with it `jsonschema`) is never imported, proven by DELETING the loader
    from the fixture's scripts/ dir and still assembling successfully.
  - A present-but-broken sidecar is FAIL-CLOSED (one JSON line, named
    reason), never silently dropped -- shipping a vault whose links
    contradict the operator's own recorded identity call is the failure
    this closes.
  - `adapter_result.delink_cost` rides out on assemble.py's stdout line, and
    the WARN naming the number lands on stderr.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"

ASSEMBLE_SRC = SCRIPTS_SRC_DIR / "assemble.py"
OUTPUT_RESOLVE_SRC = SCRIPTS_SRC_DIR / "output_resolve.py"
RENDER_OBSIDIAN_SRC = SCRIPTS_SRC_DIR / "render_obsidian.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
CANON_LINK_GROUPS_SRC = SCRIPTS_SRC_DIR / "canon_link_groups.py"
# #497: only needed when the Mentions appendix is ON -- assemble.py imports
# occurrence_targets.py lazily, and it in turn imports these two.
MENTIONS_SRCS = tuple(
    SCRIPTS_SRC_DIR / name
    for name in ("occurrence_targets.py", "bootstrap_names.py", "canon_senses.py")
)
# #492: assemble.py imports cache_key.py as a sibling, at module import time.
CACHE_KEY_SRC = SCRIPTS_SRC_DIR / "cache_key.py"
# Must match this fixture's manifest.source_inputs and
# profile source.language.particle_config respectively.
SOURCE_INPUT_NAME = "source.txt"
PARTICLE_CONFIG_NAME = "he_test.json"
LINK_GROUPS_SCHEMA_SRC = SCHEMAS_SRC_DIR / "canon-link-groups.schema.json"

for _src in (ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC,
             CANON_LINK_GROUPS_SRC, LINK_GROUPS_SCHEMA_SRC, CACHE_KEY_SRC, *MENTIONS_SRCS):
    assert _src.is_file(), f"fixture source not found: {_src}"

# #492 retired the hand-written DUMMY_CACHE_KEY that used to sit here:
# assembly now recomputes every content-affecting cache-key field from the
# live durable_root, so a fabricated stored key is a guaranteed refusal rather
# than an inert schema-shaped placeholder. real_cache_key() below produces the
# genuine one by running the shipped cache_key.py.

# Two spellings of one man -- the maqaf/no-maqaf pair #588 is actually about.
SPACED = "משה לייב"
MAQAF = "משה־לייב"
SHARED_TARGET = "Moyshe-Leyb"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _profile(root: Path, output_target="obsidian", mentions=False):
    return {
        "profile_version": 1,
        "project": {"title": "Test Book", "durable_root": str(root),
                    "pipeline_version": "v1", "max_segment_words": 15000},
        "source": {
            "format": "plain_text", "path": "/logical/source.txt", "gutenberg_id": None,
            "language": {"code": "he", "particle_config": "he_test.json",
                          "smoke_test": {"report_path": None}},
            "adapter_config": {
                "gutenberg_epub": None,
                "plain_text": {
                    "segmentation": {"method": "blank_line_run", "blank_line_threshold": 2,
                                      "heading_regex": None},
                    "verse_detection": "none_confirmed", "verse_regex": None,
                    "footnotes": "none_confirmed", "footnote_anchor_regex": None,
                    "footnote_def_regex": None,
                },
                "custom": {"extractor_path": None},
            },
        },
        "target": {"language": {"code": "en", "register_notes": "informal"}},
        "verse_policy": {"mode": "literal_only", "threshold_lines": None},
        "engine": {"effort": "high", "max_fix_rounds": 4, "batch_agent_cap": 1000},
        "footnotes": {"apparatus_policy": "translate_all"},
        "glossary": {"research_mode": "live"},
        "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
        "output": {
            "v1_scope": "assembled_book", "destination": str(root / "out"),
            "target": output_target,
            "name_display": {"parenthetical_originals": "never"},
            "adapter_config": {
                # Explicitly OFF -- #588's own delivered vault had it off, and
                # collision de-linking is decoupled from this flag (#206/#207).
                "obsidian": {"folders": {}, "mentions_section": {"enabled": mentions}},
                "epub": None, "custom": None,
            },
        },
    }


def _canon_entry(source_form, target):
    return {
        "source_form": source_form, "is_proper_name": True,
        "canonical_target_form": target, "basis": "transliterated",
        "confidence": "high", "category": "person",
    }


def _draft_content_sha1(doc: dict) -> str:
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(projected, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def _write_cache_key_inputs(root: Path, scripts_dir: Path) -> None:
    """#492: the durable-root files cache_key.py's own field computers read.
    assemble.py now recomputes every content-affecting cache-key field from
    the live root and refuses on a mismatch, so this fixture must carry real
    inputs and a real stored key. Restated from tests/final_audit.test.py's
    make_durable_root() rather than imported -- house convention is one
    self-contained file per test module. Only style_bible.md's two
    STYLE_CONTRACT markers are load-bearing; `runs/.plugin_bundle_hash` is the
    marker Step 0a writes and cache_key.py reads back rather than re-hashing
    the bundle.

    Every write here FILLS A GAP and never clobbers: `mentions=True` stages the
    REAL `bootstrap_names.py` (#497 needs its `extract_candidate_spans`) and a
    `languages/he_test.json` carrying `name_inventory`, and both are also names
    this helper would otherwise supply as placeholders. cache_key.py only needs
    the paths to EXIST and to hash stably, so deferring to whatever is already
    there is correct for both purposes."""
    def _fill(path: Path, data: bytes) -> None:
        if not path.exists():
            path.write_bytes(data)

    _fill(scripts_dir / "bootstrap_names.py", b"# bootstrap_names.py fixture\n")
    _fill(scripts_dir / "segpack.py", b"# segpack.py fixture\n")
    _fill(
        root / "style_bible.md",
        b"# Style Bible\n\n<!-- STYLE_CONTRACT_BEGIN -->\n"
        b"Formal register, Oxford comma.\n<!-- STYLE_CONTRACT_END -->\n",
    )
    _fill(root / "translate_TASK.md", b"TRANSLATE TASK PROMPT v1\n")
    _fill(root / "review_TASK.md", b"REVIEW TASK PROMPT v1\n")
    _fill(root / "extract.py", b"# extract.py fixture v1\n")
    _fill(root / SOURCE_INPUT_NAME, b"Ceci est un texte source de test.\n")
    languages_dir = root / "languages"
    languages_dir.mkdir(exist_ok=True)
    _fill(
        languages_dir / PARTICLE_CONFIG_NAME,
        json.dumps({"PARTICLES": ["de"], "STOPWORDS": ["le"], "has_elision": False,
                    "ELISION_RE": None}).encode("utf-8"),
    )
    (root / "schemas").mkdir(exist_ok=True)
    for _name in ("draft.schema.json", "review.schema.json", "segpack.schema.json"):
        _fill(root / "schemas" / _name, b"{}\n")
    runs_dir = root / "runs"
    runs_dir.mkdir(exist_ok=True)
    _fill(runs_dir / ".plugin_bundle_hash", b"test-plugin-bundle-marker-v1\n")


def real_cache_key(root: Path, seg: str) -> dict:
    """The segment's REAL 15-field cache key, from the SHIPPED cache_key.py run
    against this fixture root -- never hand-typed, so it cannot drift from what
    assemble.py recomputes at run time."""
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "cache_key.py"), "--seg", seg],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"fixture setup: cache_key.py --seg {seg} failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def make_root(tmp_path, output_target="obsidian", entries=None, block_text=None,
              with_loader=True, mentions=False, source_text=None) -> Path:
    """A minimal one-segment, one-block converged book, with the two colliding
    canon entries in place. `with_loader=False` omits canon_link_groups.py
    from scripts/ entirely, which is how the no-new-dependency claim is
    proven rather than asserted.

    `mentions=True` (#497) turns the `## Mentions` appendix on, which is what
    makes `assemble.py` call `occurrence_targets.build()` at all -- it also
    stages that module's own imports and a `languages/` config, since the
    occurrence engine's spans are configuration-dependent. `source_text`
    overrides the manifest block's SOURCE-side `plain_text`, which is the text
    the engine scans (the draft's `block_text` is the TARGET side)."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    # CACHE_KEY_SRC is unconditional (#492): assemble.py imports it as a
    # sibling, so leaving it out would fail every case on a dependency
    # precondition instead of on what the case is about.
    sources = [ASSEMBLE_SRC, OUTPUT_RESOLVE_SRC, RENDER_OBSIDIAN_SRC, VALIDATE_DRAFT_SRC,
               CACHE_KEY_SRC]
    if with_loader:
        sources.append(CANON_LINK_GROUPS_SRC)
    if mentions:
        sources.extend(MENTIONS_SRCS)
        languages_dir = root / "languages"
        languages_dir.mkdir()
        (languages_dir / "he_test.json").write_text(
            # `name_inventory` is what makes an uncased script's names
            # findable at all -- Hebrew has no capitalization for the matcher
            # to key on, so without it the engine finds nothing and BOTH
            # assertions below would pass for the wrong reason.
            json.dumps({"PARTICLES": [], "STOPWORDS": [], "has_elision": False,
                        "ELISION_RE": None, "name_inventory": [SPACED]}),
            encoding="utf-8",
        )
    for src in sources:
        shutil.copy2(src, scripts_dir / src.name)
    schemas_dir = root / "schemas"
    schemas_dir.mkdir()
    shutil.copy2(LINK_GROUPS_SCHEMA_SRC, schemas_dir / LINK_GROUPS_SCHEMA_SRC.name)

    (root / "profile.yml").write_text(
        yaml.safe_dump(_profile(root, output_target, mentions), sort_keys=False),
        encoding="utf-8",
    )
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(root / "profile.yml")}), encoding="utf-8"
    )
    if entries is None:
        entries = {SPACED: _canon_entry(SPACED, SHARED_TARGET),
                   MAQAF: _canon_entry(MAQAF, SHARED_TARGET)}
    (root / "canon.json").write_text(
        json.dumps({"entries": entries, "review_queue": [],
                    "generation_hashes": {"particle_config_hash": "x",
                                           "derivation_bundle_hash": "y"}},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    (root / "segments").mkdir()
    (root / "runs").mkdir()
    _write_cache_key_inputs(root, scripts_dir)

    if block_text is None:
        block_text = f"{SHARED_TARGET} spoke. Later {SHARED_TARGET} left."
    manifest = {
        "blocks": {"p1": {"id": "p1", "type": "PARA", "seg": "seg01", "order_index": 0,
                          "plain_text": source_text or "source text",
                          "sha1": hashlib.sha1(b"p1").hexdigest(),
                          "source_file": "source.txt"}},
        "spine": [{"pos": 0, "file": "source.txt", "klass": "body"}],
        "segments": [{"seg": "seg01", "kind": "body", "title_text": "Chapter One",
                      "block_ids": ["p1"], "word_count": 10}],
        "footnotes": [], "frontback": [], "verse": {"store": []},
        "source_inputs": ["source.txt"],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y"},
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    segpack = {
        "seg": "seg01", "title": "seg01", "kind": "body", "word_count": 10,
        "blocks": [{"id": "p1", "order_index": 0,
                     "plain_text": source_text or "source text"}],
        "footnotes": [], "verses": [], "names": [], "canon_names": [], "new_names": [],
        "generation_hashes": {"source_extraction_hash": "x", "source_input_hash": "y",
                               "particle_config_hash": "x", "derivation_bundle_hash": "y"},
    }
    (root / "segments" / "segpack_seg01.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8")

    draft = {"seg": "seg01", "blocks": {"p1": block_text},
             "footnotes": {}, "verses": {}, "names": [], "notes": []}
    (root / "segments" / "seg01.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8")

    (root / "runs" / "ledger.json").write_text(
        json.dumps({"segments": {"seg01": {
            "timestamp": "2026-01-01T00:00:00+00:00", "status": "converged", "rounds": 1,
            "cache_key": real_cache_key(root, "seg01"), "n_blocks": 1, "n_footnotes": 0, "n_verses": 0,
            "reviewed_draft_sha1": _draft_content_sha1(draft),
        }}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def write_sidecar(root: Path, doc) -> Path:
    path = root / "canon_link_groups.json"
    if isinstance(doc, (dict, list)):
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(doc, encoding="utf-8")
    return path


ONE_GROUP_DOC = {
    "schema_version": 1,
    "groups": [{"primary": SPACED, "members": [SPACED, MAQAF],
                "note": "two pointings of the same man"}],
}


def run_assemble(root: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(root / "scripts" / "assemble.py")],
                          capture_output=True, text=True, timeout=timeout)


def one_json_line(proc) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def read_nodestream(root: Path) -> dict:
    path = root / "out" / ".assembled" / "nodestream.json"
    assert path.is_file(), f"expected nodestream.json at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The sidecar reaches the adapter
# ---------------------------------------------------------------------------

def test_valid_sidecar_is_projected_into_the_persisted_nodestream(tmp_path):
    root = make_root(tmp_path)
    write_sidecar(root, ONE_GROUP_DOC)
    proc = run_assemble(root)
    assert proc.returncode == 0, proc.stderr
    assert read_nodestream(root)["link_groups"] == {SPACED: SPACED, MAQAF: SPACED}


def test_the_group_actually_relinks_the_rendered_vault(tmp_path):
    """End to end, with the Mentions appendix OFF: the shared target goes
    from unlinked to linked, and the reported cost from 2 to 0."""
    root = make_root(tmp_path)
    before = one_json_line(run_assemble(root))
    assert before["adapter_result"]["delink_cost"]["unlinked_occurrences_total"] == 2

    write_sidecar(root, ONE_GROUP_DOC)
    proc = run_assemble(root)
    assert proc.returncode == 0, proc.stderr
    after = one_json_line(proc)
    assert after["adapter_result"]["delink_cost"]["unlinked_occurrences_total"] == 0
    assert after["adapter_result"]["delink_cost"]["inline_links_emitted"] == 1

    vault = "\n".join(p.read_text(encoding="utf-8")
                      for p in (root / "out").glob("*.md"))
    assert f"|{SHARED_TARGET}]]" in vault, vault


def test_delink_cost_rides_out_on_stdout_with_the_mentions_appendix_off(tmp_path):
    """The whole point of reporting from the RENDERER rather than the W9
    gate: `validate_backlinks.py` short-circuits when the appendix is
    disabled, which is this fixture's own configuration."""
    root = make_root(tmp_path)
    proc = run_assemble(root)
    assert proc.returncode == 0, proc.stderr
    cost = one_json_line(proc)["adapter_result"]["delink_cost"]
    assert cost["delinked_targets"] == [{
        "canonical_target_form": SHARED_TARGET,
        "owners": sorted([SPACED, MAQAF]),
        "unlinked_occurrences": 2,
    }]
    assert cost["unlinked_occurrences_total"] == 2
    assert cost["inline_links_emitted"] == 0
    assert "WARN: collision de-linking left 2 occurrence(s)" in proc.stderr


def test_empty_groups_sidecar_attaches_nothing(tmp_path):
    """A schema-valid `groups: []` is a distinguished empty state, not an
    error -- and it must not put an empty key into the persisted artifact
    that a reader could mistake for "a group was applied"."""
    root = make_root(tmp_path)
    write_sidecar(root, {"schema_version": 1, "groups": []})
    proc = run_assemble(root)
    assert proc.returncode == 0, proc.stderr
    assert "link_groups" not in read_nodestream(root)


# ---------------------------------------------------------------------------
# Zero new dependency surface without a sidecar
# ---------------------------------------------------------------------------

def test_no_sidecar_never_imports_the_loader(tmp_path):
    """Proven by DELETION, not by inspection: with canon_link_groups.py
    absent from scripts/ entirely, assembly still succeeds. An eager import
    would fail the dependency preflight here."""
    root = make_root(tmp_path, with_loader=False)
    assert not (root / "scripts" / "canon_link_groups.py").exists()
    proc = run_assemble(root)
    assert proc.returncode == 0, proc.stderr
    assert "link_groups" not in read_nodestream(root)


def test_a_present_sidecar_without_the_loader_is_a_named_precondition(tmp_path):
    """...and the moment a sidecar IS present, the missing loader stops
    being invisible: fail-closed with a named reason, never a silent skip
    of the operator's recorded identity call."""
    root = make_root(tmp_path, with_loader=False)
    write_sidecar(root, ONE_GROUP_DOC)
    proc = run_assemble(root)
    assert proc.returncode != 0
    report = one_json_line(proc)
    assert report["reason"] == "dependency_precondition"
    assert "canon_link_groups.py" in report["error"]


# ---------------------------------------------------------------------------
# Fail-closed on a broken sidecar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc", [
    "{not json",
    {"schema_version": 1, "groups": [{"primary": SPACED, "members": [SPACED],
                                       "note": "one member asserts nothing"}]},
    {"schema_version": 1, "groups": [{"primary": "Ghost", "members": ["Ghost", MAQAF],
                                       "note": "primary is not a canon key"}]},
    {"schema_version": 1, "groups": [{"primary": SPACED, "members": [SPACED, MAQAF]}]},
], ids=["malformed-json", "one-member-group", "member-not-in-canon", "missing-note"])
def test_broken_sidecar_is_fail_closed_with_a_named_reason(tmp_path, doc):
    root = make_root(tmp_path)
    write_sidecar(root, doc)
    proc = run_assemble(root)
    assert proc.returncode != 0, proc.stdout
    report = one_json_line(proc)
    assert report["reason"] == "canon_link_groups_invalid"
    assert "canon_link_groups.json" in report["error"]


def test_a_dangling_symlink_sidecar_blocks_rather_than_reading_as_absent(tmp_path):
    """A broken sidecar is one the operator MEANT to have. Treating it as
    absent would silently skip an identity pass they believe is applied."""
    root = make_root(tmp_path)
    (root / "canon_link_groups.json").symlink_to(root / "gone.json")
    proc = run_assemble(root)
    assert proc.returncode != 0
    assert one_json_line(proc)["reason"] == "canon_link_groups_invalid"


# ---------------------------------------------------------------------------
# Target gating
# ---------------------------------------------------------------------------

def test_a_non_obsidian_target_never_reads_the_sidecar(tmp_path):
    """Gated on `output.target == "obsidian"` alone -- and on a non-obsidian
    target collision de-linking itself is inert, so there is nothing for a
    group to modify. Even a MALFORMED sidecar must be untouched there."""
    root = make_root(tmp_path, output_target="custom")
    write_sidecar(root, "{not json")
    proc = run_assemble(root)
    # `custom` with no renderer_path fails at adapter dispatch, well after
    # nodestream assembly -- what matters is that the failure is NOT the
    # sidecar's, and that no link_groups key was ever attached.
    assert "canon_link_groups" not in proc.stdout
    assert "link_groups" not in read_nodestream(root)


# ---------------------------------------------------------------------------
# #497 -- the ORDERING leg. occurrence_targets.build() reads the link-group map
# off nodestream["link_groups"], so _attach_link_groups has to run BEFORE
# _attach_mentions. Nothing in either function says so; only this end-to-end
# assertion does, and it is the regression a future reorder would trip.
# ---------------------------------------------------------------------------

SOURCE_WITH_OCCURRENCE = f"ראה {MAQAF} אתמול."


def test_lt497_a_ruled_fold_group_is_credited_to_its_primary_end_to_end(tmp_path):
    """With the appendix ON and a valid one-referent ruling, the PERSISTED
    mentions map carries the primary alone -- not both members, and not
    neither. Both halves matter: `SPACED in mentions` is what fails if the
    attach order is wrong (the map would not be there yet), and `MAQAF not in
    mentions` is what fails if the group were credited to every member."""
    root = make_root(tmp_path, mentions=True, source_text=SOURCE_WITH_OCCURRENCE)
    write_sidecar(root, ONE_GROUP_DOC)

    proc = run_assemble(root)
    assert proc.returncode == 0, proc.stderr

    nodestream = read_nodestream(root)
    assert nodestream["link_groups"] == {SPACED: SPACED, MAQAF: SPACED}
    mentions = nodestream["mentions"]
    assert SPACED in mentions and mentions[SPACED], mentions
    assert MAQAF not in mentions, mentions
    assert [rec["seg"] for rec in mentions[SPACED]] == ["seg01"]


def test_lt497_without_the_sidecar_the_same_book_credits_neither_member(tmp_path):
    """The control that keeps the test above from passing for the wrong
    reason: identical book, no ruling -- the fold collision withholds both, so
    the persisted mentions map is empty."""
    root = make_root(tmp_path, mentions=True, source_text=SOURCE_WITH_OCCURRENCE)

    proc = run_assemble(root)
    assert proc.returncode == 0, proc.stderr

    nodestream = read_nodestream(root)
    assert "link_groups" not in nodestream
    assert nodestream["mentions"] == {}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
